"""Verify the EVCharge Products & Services build against Clay itself (run in WSL).

Read-only. For every category marked `built` in the rollout state, ask the Clay
CLI — not the browser, and not the rollout's own log — three things:

  * does the workbook hold a table named `Companies`,
  * is it the ONLY table there (a second table means an interrupted run saved
    twice, and the duplicate is paid-for rows),
  * how many rows did it actually import.

`check_table_rows.py` prints ERROR rather than 0 when the CLI call fails, so a
transport failure can never be mistaken for an empty table and quietly retire a
workbook from future passes.

    wsl -d Ubuntu -- python3 .../verify_ps_companies.py
    wsl -d Ubuntu -- python3 .../verify_ps_companies.py --all
"""

import argparse
import json
import os
import subprocess
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
TABLE = "Companies"
PEOPLE = "People"


def sh(script, arg):
    r = subprocess.run([sys.executable, os.path.join(SCRIPT_DIR, script), arg],
                       capture_output=True, text=True, timeout=300)
    return r.stdout.strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--icp", default="evcharge")
    ap.add_argument("--all", action="store_true",
                    help="include categories not yet marked built")
    args = ap.parse_args()

    state_path = os.path.join(SCRIPT_DIR, f"ps_companies_state_{args.icp}.json")
    state = json.load(open(state_path, encoding="utf-8"))

    totals, problems = {TABLE: 0, PEOPLE: 0}, []
    print(f"{'companies':>10}  {'people':>7}  {'tabs':>4}  category")
    for name, rec in sorted(state.items(),
                            key=lambda kv: kv[1].get("workbook_id", "")):
        if rec.get("status") != "built" and not args.all:
            continue
        wid = rec.get("workbook_id")
        listing = [r for r in sh("list_workbook_tables.py", wid).splitlines()
                   if "|" in r]
        names = [r.split("|", 1)[1] for r in listing]
        ids = {r.split("|", 1)[1]: r.split("|", 1)[0] for r in listing}
        counts = {}
        for want in (TABLE, PEOPLE):
            tid = ids.get(want)
            counts[want] = sh("check_table_rows.py", tid) if tid else "-"
        print(f"{counts[TABLE]:>10}  {counts[PEOPLE]:>7}  {len(names):>4}  {name}")

        if TABLE not in ids:
            problems.append(f"{name}: no {TABLE!r} table (has {names})")
        # `People` is optional until the people pass has run, but a DUPLICATE of
        # either name is never right — an interrupted run saved twice and those
        # rows are already paid for.
        for want in (TABLE, PEOPLE):
            if names.count(want) > 1:
                problems.append(f"{name}: {names.count(want)} {want!r} tables")
            if counts[want] != "-" and not counts[want].isdigit():
                problems.append(f"{name}: {want} row count unreadable "
                                f"({counts[want]!r})")
            elif counts[want].isdigit():
                totals[want] += int(counts[want])
        extra = [n for n in names if n not in (TABLE, PEOPLE)]
        if extra:
            problems.append(f"{name}: unexpected table(s) {extra}")

    print(f"\ntotal companies: {totals[TABLE]:,}   total people: "
          f"{totals[PEOPLE]:,}")
    if problems:
        print(f"\n{len(problems)} problem(s):")
        for p in problems:
            print(f"  - {p}")
        return 1
    print(f"all verified: one {TABLE!r} (+ optional {PEOPLE!r}) table per workbook")
    return 0


if __name__ == "__main__":
    sys.exit(main())
