"""Repair the delivery tables the verification sweep found wrong.

Three tables landed EMPTY and one duplicate was created beside two of them:

    The Big 5 Egypt        Exhibitors   0 rows, expected 208
    The Big 5 UAE          Sponsors     0 rows, expected 3    (+ `Sponsors (2)`)
    Traffic InfraTech Expo Sponsors     0 rows, expected 4    (+ `Sponsors (2)`)

HOW THEY HAPPENED, both worth knowing because the fix is in the importer too:

  * The empties came from an import that created the table and then failed
    before its rows landed. The importer's "already present" branch then
    rubber-stamped them as done WITHOUT comparing row counts, so every later
    run skipped them. That branch now compares counts (see import_ce_delivery).
  * The `Sponsors (2)` duplicates came from retrying on top of a table that
    already existed: Clay appends "(2)" rather than replacing, so the retry made
    a second table and left the broken original in place.

THIS SCRIPT DELETES TABLES, which nothing else in the delivery path does. It is
therefore explicit rather than clever: the exact (event, table) pairs are listed
in TARGETS below, it refuses to delete anything holding rows unless that table
is a known `(2)` duplicate, and it never touches a workbook not named here.
`clay_ui.delete_table` drives the UI because Clay has no delete API.

After deleting it clears those entries from the import state so the normal
importer rebuilds them on its next pass.

    python3 repair_ce_delivery.py            # report what it would do
    python3 repair_ce_delivery.py --apply
"""

import argparse
import io
import json
import os
import subprocess
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
AUTO_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, SCRIPT_DIR)
sys.path.insert(0, os.path.join(AUTO_DIR, "clay_sync"))
sys.path.insert(0, os.path.join(AUTO_DIR, "build_automation"))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import clay_ui           # noqa: E402
import browser_session   # noqa: E402
import state_io          # noqa: E402

ENV = {**os.environ, "MSYS_NO_PATHCONV": "1"}
STATE = os.path.join(SCRIPT_DIR, "ce_delivery_tables_state.json")

# (event, table to delete, why). Deliberately hard-coded: this is a one-off
# repair of named damage, not a rule. Re-run the sweep rather than generalising.
TARGETS = [
    ("The Big 5 Egypt", "Exhibitors", "empty, expected 208"),
    ("The Big 5 UAE", "Sponsors", "empty, expected 3"),
    ("The Big 5 UAE", "Sponsors (2)", "duplicate left by a retry"),
    ("The Big 5 UAE", "Sponsors (3)", "duplicate left by a retry"),
    ("The Big 5 UAE", "Sponsors (4)", "duplicate left by a retry"),
    ("Traffic InfraTech Expo", "Sponsors (2)", "duplicate left by a retry"),
]
# ROUND 2 (2026-09-15): the first repair deleted the damage, but re-importing
# re-created it, because the CAUSE was still live — see clay_ui._import_csv.
# The Add-table flow was clicking 'Continue', which advances past the upload and
# yields a 0-row table; every retry then added another "(n)". `Sponsors (3)` and
# `(4)` above are from diagnosing that, and they are why the entries here are
# cumulative: a target that is already gone prints "already gone" and costs
# nothing, so leaving old ones in place is safer than editing them out and
# losing track of what was damaged.


def wsl(args):
    r = subprocess.run(["wsl", "bash", "-lc",
                        f"cd /mnt/c/Users/qasim/terrapinn/clay-icp-pipeline/"
                        f"automation/cleanup && {args}"],
                       capture_output=True, env=ENV)
    return r.returncode, r.stdout.decode("utf-8", "replace").strip()


def live_tables(wid):
    code, out = wsl(f"python3 list_workbook_table_rows.py {wid}")
    if code != 0:
        return None
    out_map = {}
    for line in out.splitlines():
        if "|" in line:
            name, rows = line.rsplit("|", 1)
            out_map[name.strip()] = int(rows) if rows.strip().isdigit() else None
    return out_map


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    state = state_io.load_json(STATE)
    wb = {}
    for k, v in state.items():
        if v.get("workbook_id"):
            wb.setdefault(k.split("||")[0], v["workbook_id"])

    plan = []
    for event, table, why in TARGETS:
        wid = wb.get(event)
        if not wid:
            print(f"!! no workbook id for {event!r} — skipping")
            continue
        live = live_tables(wid)
        if live is None:
            print(f"!! could not read {event!r} — skipping rather than "
                  f"deleting blind")
            continue
        if table not in live:
            print(f"   {event[:28]:30} {table:18} already gone")
            continue
        rows = live[table]
        # The one safety rule: never delete a table with rows unless it is one
        # of the known "(2)" duplicates, whose good copy is being rebuilt.
        if rows and "(2)" not in table:
            print(f"!! {event[:28]:30} {table:18} holds {rows} rows — "
                  f"REFUSING to delete (expected it to be empty)")
            continue
        plan.append((event, wid, table, rows, why))
        print(f"   {event[:28]:30} {table:18} {str(rows):>5} rows   {why}")

    if not plan:
        print("\nnothing to repair")
        return 0
    if not args.apply:
        print(f"\n{len(plan)} table(s) would be deleted. Add --apply.")
        return 0

    with browser_session.clay_page(headless=True) as page:
        if not clay_ui.is_logged_in(page):
            raise SystemExit("not logged in to Clay")
        for event, wid, table, rows, why in plan:
            print(f"\n{event} — deleting {table!r}", flush=True)
            clay_ui.open_workbook_by_id(page, wid)
            clay_ui.delete_table(page, table)
            after = live_tables(wid) or {}
            if table in after:
                print(f"   !! {table} still present — leaving state alone")
                continue
            print(f"   deleted")
            # Clear state so the importer rebuilds the real table next pass.
            base = table.replace(" (2)", "")
            state.pop(f"{event}||{base}", None)
            state_io.save_json(STATE, state)

    print("\nrepair done — re-run the importer to rebuild, then the sweep")
    return 0


if __name__ == "__main__":
    sys.exit(main())
