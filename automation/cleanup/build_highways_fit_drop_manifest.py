"""Build the row-deletion manifest that brings the live Highways source tables in
Clay in line with the ICP fit filter.

The fit filter (`scrapers_highways/apply_fit_filter.py`, rules in each event's
`FIT_FILTER.md`) trimmed the local `exhibitors_normalized.csv` /
`sponsors_normalized.csv`; the untouched originals sit in
`<event>/_pre_fit_filter/`. The Clay tables were imported from those originals
on 2026-08-13 and have since been enriched and classified, so they are trimmed
by deleting the dropped ROWS — never by re-importing — which keeps every
enrichment on the rows that survive.

READ-ONLY: this script only reads Clay. It writes
    highways_fit_drop_manifest.json   {event: {table: {...drop row ids...}}}
    highways_fit_drop_review.csv      one line per Clay row that would go

Matching, per table, by company name (casefold, whitespace collapsed):
    KEEP       name is still in the live filtered CSV (multiset: one Clay row
               per live row)
    DROP       name was in the pre-filter original and is not in the live CSV
    EXTRA      another copy of a kept name beyond the live count — left alone
    UNMATCHED  name in neither file — left alone, reported
A whole data type the filter removed (no live CSV) drops every original row.
Events without `_pre_fit_filter/` were never filtered and are skipped.

Scope is `automation/clay_sync/highways_logs/table_ids.json`, as for
build_highways_ce_manifest.py.

Run in WSL (the `clay` CLI is Linux-only here):
    python3 build_highways_fit_drop_manifest.py
    python3 build_highways_fit_drop_manifest.py --only "FloodEx"
"""
import argparse
import csv
import json
import os
import sys
import time
from collections import Counter

import clay_bin

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
IDS = os.path.join(os.path.dirname(SCRIPT_DIR), "clay_sync", "highways_logs",
                   "table_ids.json")
SCRAPERS = os.environ.get("CLAY_HIGHWAYS_SCRAPERS_ROOT",
                          "/mnt/c/Users/qasim/terrapinn/scrapers_highways")
OUT_JSON = os.path.join(SCRIPT_DIR, "highways_fit_drop_manifest.json")
OUT_CSV = os.path.join(SCRIPT_DIR, "highways_fit_drop_review.csv")
BACKUP = "_pre_fit_filter"

BIN = None


def clay_json(args, tries=4):
    err = ""
    for _ in range(tries):
        ok, d, err = clay_bin.run(BIN, args, timeout=180)
        if ok:
            return d
        time.sleep(2)
    raise SystemExit(f"clay {' '.join(args)} failed: {err}")


def norm(s):
    return " ".join(str(s or "").split()).casefold()


def read_csv(path):
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def column_ids(table_id):
    d = clay_json(["tables", "columns", "list", table_id])
    by_name = {c.get("name"): c.get("id") for c in d.get("data") or []}
    return by_name.get("Company Name"), by_name.get("Website")


def clay_rows(table_id):
    """[(row_id, name, website)] for every row. Paged to exhaustion and
    self-checked against the table's rowCount — a short read raises rather than
    under-reporting (the `if not d: break` audit trap)."""
    name_col, web_col = column_ids(table_id)
    if not name_col:
        raise SystemExit(f"{table_id}: no 'Company Name' column")
    expected = clay_json(["tables", "get", table_id]).get("rowCount")
    out, cursor = [], None
    while True:
        args = ["tables", "rows", "list", table_id, "--limit", "100"]
        if cursor:
            args += ["--cursor", cursor]
        d = clay_json(args)
        for r in d.get("data") or []:
            cells = r.get("cells") or {}
            val = lambda c: (cells.get(c) or {}).get("value") if c else None
            out.append((r["id"], val(name_col) or "", val(web_col) or ""))
        cursor = d.get("cursor")
        if not cursor:
            break
    if expected is not None and len(out) != expected:
        raise SystemExit(f"{table_id}: paged {len(out)} rows but rowCount={expected}")
    return out


def plan_table(event, table, table_id):
    original = read_csv(os.path.join(SCRAPERS, event, BACKUP, table + ".csv"))
    if original is None:
        return None  # this data type wasn't part of the filter for this event
    live = read_csv(os.path.join(SCRAPERS, event, table + ".csv")) or []
    live_left = Counter(norm(r["Company Name"]) for r in live)
    original_names = {norm(r["Company Name"]) for r in original}

    rows = clay_rows(table_id)
    keep, drop, extra, unmatched = [], [], [], []
    for rid, name, web in rows:
        k = norm(name)
        rec = {"row_id": rid, "name": name, "website": web}
        if live_left[k] > 0:
            live_left[k] -= 1
            keep.append(rec)
        elif k in live_left:
            extra.append(rec)
        elif k in original_names:
            drop.append(rec)
        else:
            unmatched.append(rec)
    missing = sorted(n for n, c in live_left.items() for _ in range(c))
    return {
        "table_id": table_id,
        "clay_rows": len(rows),
        "original_rows": len(original),
        "live_rows": len(live),
        "keep": len(keep),
        "drop": drop,
        "extra": extra,
        "unmatched": unmatched,
        "live_missing_in_clay": missing,
    }


def main():
    global BIN
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", action="append", metavar="EVENT")
    args = ap.parse_args()

    BIN = clay_bin.pick()
    with open(IDS, encoding="utf-8") as fh:
        ids = json.load(fh)
    events = sorted(ids)
    if args.only:
        unknown = set(args.only) - set(ids)
        if unknown:
            raise SystemExit(f"not in table_ids.json: {sorted(unknown)}")
        events = [e for e in events if e in args.only]

    manifest, review = {}, []
    for event in events:
        if not os.path.isdir(os.path.join(SCRAPERS, event, BACKUP)):
            continue
        for table, table_id in sorted(ids[event].items()):
            p = plan_table(event, table, table_id)
            if p is None:
                continue
            manifest.setdefault(event, {})[table] = p
            flags = []
            if p["clay_rows"] != p["original_rows"]:
                flags.append(f"clay {p['clay_rows']} != original {p['original_rows']}")
            for key in ("extra", "unmatched", "live_missing_in_clay"):
                if p[key]:
                    flags.append(f"{key}={len(p[key])}")
            print(f"{event:45} {table:22} clay={p['clay_rows']:4} "
                  f"keep={p['keep']:4} drop={len(p['drop']):4}"
                  + (f"   !! {'; '.join(flags)}" if flags else ""), flush=True)
            for r in p["drop"]:
                review.append([event, table, r["row_id"], r["name"], r["website"]])

    with open(OUT_JSON, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=1, ensure_ascii=False)
    with open(OUT_CSV, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["Event", "Table", "Row ID", "Company Name", "Website"])
        w.writerows(review)

    total = sum(len(t["drop"]) for e in manifest.values() for t in e.values())
    print(f"\n{total} rows to delete across "
          f"{sum(len(e) for e in manifest.values())} tables -> {OUT_JSON}")


if __name__ == "__main__":
    sys.exit(main())
