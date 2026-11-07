"""Ledger completion across every EVCharge Products & Services Companies table
(run in WSL).

For each workbook: find the `Companies` table, confirm the template's columns
exist, and walk EVERY page of rows to report how many resolved `Is New` and how
they split. Partial fill means the run is unfinished, not that those rows are
"not new" — the distinction matters because an unfinished ledger silently lets
already-worked companies through as new.

Deliberately not the 20-page sample `check_column_fill.py` takes: on an
8,631-row table that covers under a quarter, and reading it as a percentage
reports a half-finished run as nearly done.

    wsl -d Ubuntu -- python3 verify_ps_ledger.py
"""

import argparse
import json
import os
import subprocess
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_TABLE = "Companies"
COLUMN = "Is New"


def sh(script, *args):
    r = subprocess.run([sys.executable, os.path.join(SCRIPT_DIR, script), *args],
                       capture_output=True, text=True, timeout=1800)
    return r.stdout.strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--icp", default="evcharge")
    ap.add_argument("--table", default=DEFAULT_TABLE,
                    help="Companies | People")
    args = ap.parse_args()
    table = args.table

    state = json.load(open(os.path.join(
        SCRIPT_DIR, f"ps_companies_state_{args.icp}.json"), encoding="utf-8"))

    print(f"{table}:")
    print(f"{'rows':>7} {'filled':>7} {'new':>7} {'seen':>6}  category")
    tot = {"rows": 0, "filled": 0, "new": 0, "seen": 0}
    incomplete = []
    for name, rec in sorted(state.items()):
        wid = rec.get("workbook_id")
        if not wid:
            continue
        listing = [r for r in sh("list_workbook_tables.py", wid).splitlines()
                   if "|" in r]
        tid = next((r.split("|", 1)[0] for r in listing
                    if r.split("|", 1)[1] == table), None)
        if not tid:
            print(f"{'-':>7} {'-':>7} {'-':>7} {'-':>6}  {name}  (no {table})")
            incomplete.append(f"{name}: no {table} table")
            continue

        out = sh("check_ledger_column.py", tid, COLUMN)
        if "not on this table" in out:
            print(f"{'-':>7} {'-':>7} {'-':>7} {'-':>6}  {name}  "
                  f"(no {COLUMN!r} column - template not applied)")
            incomplete.append(f"{name}: no {COLUMN} column")
            continue

        vals = {}
        for line in out.splitlines():
            line = line.strip()
            if line.startswith("rows"):
                vals["rows"] = int(line.split(":")[1].replace(",", "").split()[0])
            elif line.startswith("filled"):
                vals["filled"] = int(line.split(":")[1].split("(")[0]
                                     .replace(",", "").strip())
            elif line.startswith("'True'"):
                vals["new"] = int(line.split(":")[1].replace(",", "").strip())
            elif line.startswith("'False'"):
                vals["seen"] = int(line.split(":")[1].replace(",", "").strip())
        rows = vals.get("rows", 0)
        filled = vals.get("filled", 0)
        new = vals.get("new", 0)
        seen = vals.get("seen", 0)
        flag = "" if rows and filled == rows else "  <-- INCOMPLETE"
        print(f"{rows:>7,} {filled:>7,} {new:>7,} {seen:>6,}  {name}{flag}")
        if flag:
            incomplete.append(f"{name}: {filled:,}/{rows:,} resolved")
        for k, v in (("rows", rows), ("filled", filled), ("new", new),
                     ("seen", seen)):
            tot[k] += v

    print(f"\n{tot['rows']:>7,} {tot['filled']:>7,} {tot['new']:>7,} "
          f"{tot['seen']:>6,}  TOTAL")
    if incomplete:
        print(f"\n{len(incomplete)} table(s) not finished:")
        for i in incomplete:
            print(f"  - {i}")
        return 1
    print("\nevery Companies table has a fully resolved ledger check")
    return 0


if __name__ == "__main__":
    sys.exit(main())
