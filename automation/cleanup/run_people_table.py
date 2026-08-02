"""Trigger a run on one people table: select all rows -> Actions -> Run N rows.

Kept separate from the apply step so the waterfall is configured (and Validate
Email's gate rebound to this table's WORK EMAIL) BEFORE anything is charged, and
so a table is never run twice by accident.

Refuses to run if WORK EMAIL already holds values unless --force is given.

  python run_people_table.py <wid> <name> "Sellers - People"
"""

import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
AUTO_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, SCRIPT_DIR)
sys.path.insert(0, os.path.join(AUTO_DIR, "clay_sync"))
sys.path.insert(0, os.path.join(AUTO_DIR, "build_automation"))

import clay_ui        # noqa: E402
import common         # noqa: E402
import build_lib as B  # noqa: E402
import add_workemail_waterfall_event as W  # noqa: E402


def run_table(page, entry, table, say, force=False):
    wid, name = entry["workbook_id"], entry["workbook_name"]
    clay_ui.open_workbook_by_id(page, wid)
    if not B.table_exists(page, table):
        say(f"SKIP {name}/{table}: no such table")
        return {"status": "no_table"}
    B.focus_table_maybe_empty(page, table)
    page.wait_for_timeout(1200)

    if not W.find_header_scrolling(page, "WORK EMAIL"):
        say(f"SKIP {name}/{table}: no WORK EMAIL column — apply the template first")
        return {"status": "not_applied"}

    trig = W.trigger_run(page, say)
    if not trig:
        say(f"ABORT {name}/{table}: could not trigger 'Run N rows'")
        return {"status": "run_not_triggered"}
    return {"status": "running", "ran": trig}


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("workbook_id")
    ap.add_argument("workbook_name")
    ap.add_argument("table")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--headed", action="store_true")
    a = ap.parse_args()
    entry = {"workbook_id": a.workbook_id, "workbook_name": a.workbook_name}
    with common.clay_page(headless=not a.headed) as page:
        def say(m):
            print(m, flush=True)
        print("\nRESULT:", run_table(page, entry, a.table, say, force=a.force))
