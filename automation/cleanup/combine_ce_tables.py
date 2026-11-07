"""Combine every Competitive Events table into ONE CSV, with provenance.

Per the user (2026-09-12), two columns are added:

    Exhibitor/Sponsor    "Exhibitor" or "Sponsor" - which table the row came from
    Company Event Name   the event, e.g. "AfPA Conference"

TWO THINGS WORTH KNOWING BEFORE YOU READ THE OUTPUT.

1. **`Company Event Name` is NOT a copy of the existing `Event` column.** It is
   taken from the manifest key — i.e. the Clay workbook name — because that is
   the authority for which event a table belongs to and is populated for every
   row by construction. The scraped `Event` cell can be blank or spelled
   differently (the Competitor sheet says "Transport Research Arena conference"
   where the workbook is "Transport Research Arena"). `Event` is preserved
   untouched alongside it, and any row where the two disagree is counted and
   reported at the end so the drift is visible rather than silently papered over.

2. **The two table types do not share a schema.** `exhibitors_normalized` has
   `Year` and `Country`; `sponsors_normalized` has neither. The output is the
   UNION of both, in exhibitors order, so a sponsor row simply has those two
   cells empty. No column is dropped to make the shapes match.

Classification results are joined in where they exist (`Side`, `Classification`,
`Outcome`), so this doubles as the fleet deliverable once the rollout has run.
Rows with no result yet are marked `not processed` rather than left ambiguous,
and the gate is re-applied here exactly as in export_ce_classification.py: a row
with no `Domain`, or one the ledger has already claimed, never shows a
classification even if an older pre-gate result file contains one.

Reading is credit-free (`clay tables rows list` ignores the query quota) but
slow — 67 tables, several minutes. Fetched tables are cached to
`ce_combine_cache.json` so a re-run after a kill costs nothing; `--refresh`
forces a re-read.

    python combine_ce_tables.py
    python combine_ce_tables.py --refresh
    python combine_ce_tables.py --out "Highways CE - combined.csv"
"""

import argparse
import csv
import glob
import json
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

import state_io                      # noqa: E402
import audit_ce_classification as A  # noqa: E402

OUT_DIR = os.path.join(SCRIPT_DIR, "ce_classification_exports")
CACHE = os.path.join(SCRIPT_DIR, "ce_combine_cache.json")
RESULT_DIR = os.path.join(SCRIPT_DIR, "ce_classification_results")

ADDED_FRONT = ("Company Event Name", "Exhibitor/Sponsor")
ADDED_BACK = ("Side", "Classification", "Outcome")
KIND = {"exhibitors_normalized": "Exhibitor", "sponsors_normalized": "Sponsor"}

# Column list + order for the deliverable, captured from the user's own edit of
# the combined CSV (2026-09-12). Absent or --all, every column is emitted.
COLUMNS_SPEC = os.path.join(SCRIPT_DIR, "ce_export_columns.json")

# THE NAME COLLISION, and why the suffix exists.
#
# Two CE tables already carry classification columns from earlier work done
# inside Clay: `Big 5 Construct Saudi|exhibitors_normalized` has Classifier /
# Side / Classification, and `Construction Summit|exhibitors_normalized` has
# a `Side<newline>Classification` column, plus Side / Classification. Unioning
# and THEN appending our own Side/Classification produced a CSV with two columns
# called `Side` and two called `Classification` — same header, different data.
#
# That is not cosmetic. On the 489 Big 5 Construct Saudi rows the two disagree
# on 106 of them, and anything reading by header name silently takes whichever
# it meets first. The user's hand-edit deleted one of each pair by position and
# kept the IN-TABLE values, so 546 rows carried the old classification rather
# than the Registrar output we had just produced.
#
# Source columns whose names collide with ours are therefore renamed on the way
# out. `Side`/`Classification` now unambiguously mean the Registrar output, and
# the pre-existing values survive under a distinct name rather than being
# dropped or silently shadowed.
COLLISION_SUFFIX = " (in table)"


def scope():
    out = []
    for kind in ("exhibitors", "sponsors"):
        mf = os.path.join(SCRIPT_DIR, f"highways_ce_{kind}_manifest.json")
        if not os.path.exists(mf):
            continue
        for event, rec in state_io.load_json(mf).items():
            if event in A.SKIP_EVENTS:
                continue
            out.append(dict(event=event, table=rec["table_name"],
                            table_id=rec["table_id"]))
    # Exhibitors first, then sponsors, each alphabetical — a stable, readable order.
    out.sort(key=lambda r: (r["table"] != "exhibitors_normalized", r["event"]))
    return out


def fetch(tid):
    """(ordered column names, [ {col: value} ]) for every row. Fails loud."""
    cols = A.clay(f"tables columns list {tid}")["data"]
    order = [c["name"] for c in cols]
    cmap = {c["id"]: c["name"] for c in cols}
    rows, cursor = [], None
    while True:
        cmd = f"tables rows list {tid} --limit 100" + (f" --cursor {cursor}" if cursor else "")
        d = A.clay(cmd)
        for r in d.get("data", []):
            rec = {"__rowid": r["id"]}
            for cid, cell in (r.get("cells") or {}).items():
                nm = cmap.get(cid)
                if nm is None:
                    continue
                if cell.get("status") != "success":
                    rec[nm] = ""
                    continue
                v = cell.get("value")
                rec[nm] = ("" if v is None else v
                           if isinstance(v, (str, int, float, bool))
                           else json.dumps(v, ensure_ascii=False))
            rows.append(rec)
        cursor = d.get("cursor")
        if not cursor:
            break
    # Same self-check as the audit: a partial read must not look like a short table.
    g = A.clay(f"tables get {tid}")
    declared = (g.get("data", g) or {}).get("rowCount")
    if declared is not None and declared != len(rows):
        raise A.ClayReadError(
            f"table {tid}: paged {len(rows)} rows but rowCount is {declared}")
    return order, rows


def load_results():
    merged = {}
    for fp in sorted(glob.glob(os.path.join(RESULT_DIR, "*.jsonl"))) + \
            sorted(glob.glob(os.path.join(SCRIPT_DIR, "*_results.jsonl"))):
        for line in open(fp, encoding="utf-8"):
            if line.strip():
                r = json.loads(line)
                merged[r["id"].split(":", 1)[-1]] = r
    return merged


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="Highways CE - all tables combined.csv")
    ap.add_argument("--refresh", action="store_true", help="ignore the cache")
    ap.add_argument("--all-columns", action="store_true",
                    help="emit every column, ignoring ce_export_columns.json")
    args = ap.parse_args()

    cache = {} if args.refresh else state_io.load_json(CACHE)
    results = load_results()
    print(f"classification results available: {len(results)} rows")

    tables = scope()
    print(f"scope: {len(tables)} tables\n")

    col_order, all_rows = [], []
    event_mismatch, missing_event = 0, 0
    for i, rec in enumerate(tables, 1):
        ck = f"{rec['event']}|{rec['table']}"
        if ck in cache:
            order, rows = cache[ck]["order"], cache[ck]["rows"]
            src = "cache"
        else:
            order, rows = fetch(rec["table_id"])
            cache[ck] = {"order": order, "rows": rows}
            state_io.save_json(CACHE, cache)
            src = "clay"
        for c in order:
            out_name = c + COLLISION_SUFFIX if c in ADDED_BACK + ADDED_FRONT else c
            if out_name not in col_order:
                col_order.append(out_name)
        for r in rows:
            for c in list(r):
                if c in ADDED_BACK + ADDED_FRONT:
                    r[c + COLLISION_SUFFIX] = r.pop(c)
            r["Company Event Name"] = rec["event"]
            r["Exhibitor/Sponsor"] = KIND[rec["table"]]
            ev = str(r.get("Event", "")).strip()
            if not ev:
                missing_event += 1
            elif ev != rec["event"]:
                event_mismatch += 1
        all_rows.extend(rows)
        print(f"  [{i:2}/{len(tables)}] {rec['event'][:38]:40} "
              f"{KIND[rec['table']]:9} {len(rows):5} rows ({src})", flush=True)

    full = list(ADDED_FRONT) + col_order + list(ADDED_BACK)

    # Restrict and order to the captured spec, if there is one.
    spec = None
    if not args.all_columns and os.path.exists(COLUMNS_SPEC):
        spec = state_io.load_json(COLUMNS_SPEC)
    if spec:
        missing = [c for c in spec if c not in full]
        dropped = [c for c in full if c not in spec]
        header = [c for c in spec if c in full]
        print(f"column spec: {len(header)} of {len(full)} columns kept")
        if dropped:
            print(f"  dropped ({len(dropped)}): {', '.join(dropped)}")
        if missing:
            # Loud, not silent: a name in the spec that no table provides means
            # the spec and the tables have drifted apart.
            print(f"  !! IN SPEC BUT NOT AVAILABLE ({len(missing)}): {', '.join(missing)}")
    else:
        header = full
        print(f"no column spec — emitting all {len(header)} columns")
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, args.out)
    counts = {"classified": 0, "not new": 0, "no domain": 0, "not processed": 0,
              "failed": 0}

    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        for r in all_rows:
            side = cls = ""
            res = results.get(r["__rowid"])
            # The gate, re-applied. Domain first: a row with no Domain was never
            # ledger-checked, so its Is New is a default, not a verdict.
            if not str(r.get("Domain", "")).strip():
                outcome = "skipped: no domain"
                counts["no domain"] += 1
            elif not A.is_new(r.get("Is New")):
                outcome = "skipped: not new"
                counts["not new"] += 1
            elif res is None:
                outcome = "not processed"
                counts["not processed"] += 1
            elif res.get("status") == "failed":
                outcome = "FAILED: " + str((res.get("error") or {}).get("message", ""))[:150]
                counts["failed"] += 1
            else:
                payload = res.get("result") or {}
                so = payload.get("structuredOutputs") or payload
                side = so.get("Side") or ""
                cls = so.get("Classification") or ""
                outcome = "classified" if side else "no output"
                counts["classified" if side else "not processed"] += 1
            r["Side"], r["Classification"], r["Outcome"] = side, cls, outcome
            w.writerow([r.get(c, "") for c in header])

    print(f"\n{'=' * 66}")
    print(f"rows written : {len(all_rows)}")
    print(f"columns      : {len(header)}")
    for k, v in counts.items():
        if v:
            print(f"  {k:14} {v}")
    print(f"\n'Event' cell blank            : {missing_event}")
    print(f"'Event' != workbook name      : {event_mismatch}")
    print(f"\n-> {path}")


if __name__ == "__main__":
    main()
