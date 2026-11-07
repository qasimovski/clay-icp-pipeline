"""Join Clay Workflows classification results back onto the FULL Clay table and
write the deliverable CSV — one file per Competitive Events table.

WHY THIS EXISTS. Clay Workflows cannot write columns back into a Clay table (no
action writes rows into one; `clay tables` is read-only). So `Side` and
`Classification` come back in a bulk result file keyed by the `id` we sent, and
the deliverable has to be assembled here. Per the user: the CSV keeps **every
column of the source table**, with Side and Classification appended.

THE GATE IS ENFORCED HERE TOO, and that is deliberate. Per the user
(2026-09-12): **the Claygent may only run where `Is New` is true.** The workflow
enforces it going forward (the `Is New?` node, published v3), but the first
Geotechnica run predates that node, so its result file contains classifications
for already-worked companies. Those are suppressed here rather than shipped:
the rows were paid for, but shipping them would re-add records the blocklist
ledger deliberately deduped out, which is the whole point of the ledger. A row
that fails the gate is emitted with empty Side/Classification and an Outcome
saying why, so every source row is still accounted for.

Row ids are the join key. The bulk feed sends `id = "<table>:<rowId>"`, so a
result maps back to exactly one row without matching on company name — which
would be wrong anyway, since the same company appears in both the exhibitors and
sponsors tables of one event.

    python export_ce_classification.py --event Geotechnica
    python export_ce_classification.py --event Geotechnica --table exhibitors_normalized
    python export_ce_classification.py --event Geotechnica --include-reasoning

Read-only against Clay and credit-free: `clay tables rows list` ignores the
query quota. Runs in either shell — the clay CLI calls are routed through WSL.
"""

import argparse
import csv
import glob
import json
import os
import subprocess
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

import state_io  # noqa: E402

OUT_DIR = os.path.join(SCRIPT_DIR, "ce_classification_exports")
ENV = {**os.environ, "MSYS_NO_PATHCONV": "1"}

# Appended after the source table's own columns, in this order.
ADDED = ("Side", "Classification", "Outcome")


def clay(args):
    r = subprocess.run(["wsl", "bash", "-lc", "clay " + args],
                       capture_output=True, env=ENV)
    if r.returncode != 0:
        raise SystemExit(f"clay {args}\n  {r.stderr.decode('utf-8','replace')[:300]}")
    return json.loads(r.stdout.decode("utf-8", "replace"))


def table_ids(event, which):
    """(table_name -> table_id) for one event, from the CE manifests."""
    found = {}
    for kind in ("exhibitors", "sponsors"):
        mf = os.path.join(SCRIPT_DIR, f"highways_ce_{kind}_manifest.json")
        if not os.path.exists(mf):
            continue
        rec = state_io.load_json(mf).get(event)
        if rec and (which is None or rec["table_name"] == which):
            found[rec["table_name"]] = rec["table_id"]
    return found


def fetch_table(tid):
    """(ordered column names, {rowId: {col: value}}) — every column, every row."""
    cols = clay(f"tables columns list {tid}")["data"]
    order = [c["name"] for c in cols]
    cmap = {c["id"]: c["name"] for c in cols}
    out, cursor = {}, None
    while True:
        cmd = f"tables rows list {tid} --limit 100" + (f" --cursor {cursor}" if cursor else "")
        d = clay(cmd)
        for r in d.get("data", []):
            rec = {}
            for cid, cell in (r.get("cells") or {}).items():
                nm = cmap.get(cid)
                if nm is None:
                    continue
                if cell.get("status") != "success":
                    rec[nm] = ""
                    continue
                v = cell.get("value")
                # Action columns (Enrich Company, HTTP API) hold objects. Keep
                # them as compact JSON rather than dropping real data.
                rec[nm] = ("" if v is None else
                           v if isinstance(v, (str, int, float, bool))
                           else json.dumps(v, ensure_ascii=False))
            out[r["id"]] = rec
        cursor = d.get("cursor")
        if not cursor:
            break
    return order, out


def load_results(result_files):
    """{rowId: result}. Later files win, so a re-run supersedes its failure."""
    merged = {}
    for fp in result_files:
        for line in open(fp, encoding="utf-8"):
            if not line.strip():
                continue
            r = json.loads(line)
            merged[r["id"].split(":", 1)[-1]] = r
    return merged


def is_new(value):
    """Same fail-closed coercion as the workflow's `Read Is New` node."""
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() == "true"


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--event", required=True, help="event/workbook name")
    ap.add_argument("--table", choices=("exhibitors_normalized", "sponsors_normalized"),
                    help="just one table type (default: both)")
    ap.add_argument("--results", action="append", default=[],
                    help="bulk result .jsonl (repeatable; later wins). "
                         "Defaults to every *_results.jsonl beside this script.")
    ap.add_argument("--include-reasoning", action="store_true",
                    help="append the model's reasoning — useful for review, "
                         "noise for the IT handoff")
    ap.add_argument("--out-dir", default=OUT_DIR)
    args = ap.parse_args()

    tables = table_ids(args.event, args.table)
    if not tables:
        raise SystemExit(f"no manifest entry for event {args.event!r}"
                         + (f" table {args.table!r}" if args.table else ""))

    files = args.results or sorted(glob.glob(os.path.join(SCRIPT_DIR, "*_results.jsonl")))
    if not files:
        raise SystemExit("no result files given and none found; pass --results")
    results = load_results(files)
    print(f"results loaded: {len(results)} rows from {len(files)} file(s)")

    os.makedirs(args.out_dir, exist_ok=True)
    for tname, tid in sorted(tables.items()):
        order, rows = fetch_table(tid)
        header = list(order) + list(ADDED) + (["Reasoning"] if args.include_reasoning else [])
        path = os.path.join(args.out_dir, f"{args.event} - {tname}.csv".replace("/", "-"))
        counts = {"classified": 0, "not new": 0, "no domain": 0, "no result": 0, "failed": 0}

        with open(path, "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.writer(fh)
            w.writerow(header)
            for rid, rec in rows.items():
                res = results.get(rid)
                side = cls = ""
                reasoning = ""
                # DOMAIN IS TESTED FIRST, and the order is not cosmetic. The
                # ledger column carries "Run only if Domain is not empty", so on
                # a row with no Domain it never fired and `Is New` is sitting at
                # its unchecked default — false. Testing Is New first would
                # label those rows "not new" (i.e. already worked) when the truth
                # is they were never checked at all. Same defaulting that made
                # the Tolling & Road User Charging table look fully deduped when
                # its ledger column had simply never run.
                if not str(rec.get("Domain", "")).strip():
                    outcome = "skipped: no domain (never ledger-checked)"
                    counts["no domain"] += 1
                elif not is_new(rec.get("Is New")):
                    # THE GATE. Suppress regardless of what the result file says:
                    # pre-gate runs classified these, and shipping them would
                    # undo the ledger's dedupe.
                    outcome = "skipped: not new"
                    counts["not new"] += 1
                elif res is None:
                    outcome = "not processed"
                    counts["no result"] += 1
                elif res.get("status") == "failed":
                    outcome = "FAILED: " + str((res.get("error") or {}).get("message", ""))[:150]
                    counts["failed"] += 1
                else:
                    payload = res.get("result") or {}
                    so = payload.get("structuredOutputs") or payload
                    side = so.get("Side") or ""
                    cls = so.get("Classification") or ""
                    reasoning = (payload.get("reasoning") or "").replace("\n", " ")
                    outcome = "classified" if side else "no output"
                    counts["classified" if side else "no result"] += 1
                row = [rec.get(c, "") for c in order] + [side, cls, outcome]
                if args.include_reasoning:
                    row.append(reasoning)
                w.writerow(row)

        print(f"\n{tname}: {len(rows)} rows, {len(order)} source columns "
              f"+ {len(header) - len(order)} added")
        for k, v in counts.items():
            if v:
                print(f"    {k:12} {v}")
        print(f"    -> {path}")


if __name__ == "__main__":
    main()
