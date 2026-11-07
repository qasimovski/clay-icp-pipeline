"""Scope manifest for the EVCharge Competitive Events ledger pass (run in WSL).

Reads evcharge_competitive_workbooks.json (the folder listing) and asks Clay,
per workbook, whether it holds an `Exhibitors_normalized` table and whether that
table already carries the ledger columns.

Written as a manifest rather than resolved live per run because the pass shards
across parallel workers: every worker must agree on the scope and its ordering,
or two of them pick up the same workbook. Row counts are recorded for ordering
and blast-radius reporting only — never as a correctness input.

    wsl -d Ubuntu -- python3 build_evcharge_ce_manifest.py
"""

import json
import os
import subprocess
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(SCRIPT_DIR, "evcharge_competitive_workbooks.json")
OUT = os.path.join(SCRIPT_DIR, "evcharge_ce_manifest.json")
# The EVCharge Competitive Events tables are LOWERCASE `exhibitors_normalized`:
# import_evcharge.py created them from `exhibitors_normalized.csv` and Clay names
# a table after the CSV stem. The Labs fleet's `Exhibitors_normalized` is
# capitalised. Match case-insensitively and carry the real name forward — these
# are live Clay object names and must be used byte-exact when focusing a tab.
TABLE = "exhibitors_normalized"
MARKER = "HTTP API"


def sh(script, *args):
    r = subprocess.run([sys.executable, os.path.join(SCRIPT_DIR, script), *args],
                       capture_output=True, text=True, timeout=600)
    return r.stdout.strip()


def main():
    wbs = json.load(open(SRC, encoding="utf-8"))
    out, missing = {}, []
    for wid, name in sorted(wbs.items(), key=lambda kv: kv[1]):
        listing = [r for r in sh("list_workbook_tables.py", wid).splitlines()
                   if "|" in r]
        match = next((r for r in listing
                      if r.split("|", 1)[1].lower() == TABLE.lower()), None)
        tid = match.split("|", 1)[0] if match else None
        tname = match.split("|", 1)[1] if match else None
        if not tid:
            missing.append(name)
            print(f"  --      no {TABLE:24s} {name}")
            continue
        cols = sh("check_table_columns.py", tid).splitlines()
        rows = sh("check_table_rows.py", tid)
        dups = [c for c in cols if c.startswith("HTTP API (")]
        out[name] = {
            "workbook_id": wid, "table_id": tid, "table_name": tname,
            "rows": int(rows) if rows.isdigit() else None,
            "applied": MARKER in cols,
            "duplicates": dups,
        }
        flag = ""
        if dups:
            flag = f"  <-- DUPLICATE {dups}"
        elif MARKER in cols:
            flag = "  (already applied)"
        print(f"  {str(out[name]['rows']):>7} rows  {name}{flag}")

    json.dump(out, open(OUT, "w", encoding="utf-8"), indent=1,
              ensure_ascii=False, sort_keys=True)
    todo = [n for n, r in out.items() if not r["applied"]]
    print(f"\n{len(out)} workbooks with {TABLE}; {len(missing)} without")
    print(f"{len(todo)} still need the template")
    total = sum(r["rows"] or 0 for r in out.values() if not r["applied"])
    print(f"~{total:,} rows to check")
    dupes = [n for n, r in out.items() if r["duplicates"]]
    if dupes:
        print(f"\n!! {len(dupes)} with duplicate ledger columns: {dupes}")
    if missing:
        print(f"\nno {TABLE} table: {missing}")
    print(f"\nwrote {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
