"""Re-run ONE named column via its header menu -> "Run column".

Why this exists rather than reusing run_all_columns.py: that one selects all rows
and fires the table-wide "Run N rows", which on a People table re-fires the email
waterfall and Validate Email — real credits. "Run column" is scoped to a single
column, so refreshing a lookup costs only that lookup.

=== Why a /run/i click is allowed here ===

browser_session's standing rule is never to click a control matching /run/i.
This script is the deliberate exception, instructed by the user (2026-08-03,
"re run") for the diagnosis below, and it is narrow by construction: it runs the
ONE column named on the command line and nothing else.

=== What it is for ===

The "Qasim - CPJ - Labs - People" template's twelve reference columns read
properties off each People table's "Company Table Data" lookup. Eight populate;
the five Claygent-authored ones (Sub Industry, Primary Offerings, Business Stage,
Annual Revenue, Total Funding Amount Range Usd) come back empty with status
"success" — while the user's reference table (ACHEMA / Sellers - People) has them
at 43-100%. The reference paths are identical in shape on both
('f_<lookup>.<property>', verified via each column's data-id), so the difference
is in what the lookup itself returns: a Company Lookup stores a snapshot, and
these tables' lookups last ran before the exhibitor tables' Claygent columns were
filled. Re-running the lookup is the test.

  python run_lookup_column.py <wb_id> "<table>" "Company Table Data" --dry-run
  python run_lookup_column.py <wb_id> "<table>" "Company Table Data"
"""

import argparse
import datetime
import json
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
AUTO_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, SCRIPT_DIR)
sys.path.insert(0, os.path.join(AUTO_DIR, "clay_sync"))
sys.path.insert(0, os.path.join(AUTO_DIR, "build_automation"))

import clay_ui                            # noqa: E402
import browser_session                    # noqa: E402
import column_config as colcfg            # noqa: E402
import add_workemail_waterfall as panel   # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

RUN_ITEM = "Run column"


def run_column(page, wid, table, column, say, dry_run=False):
    res = {"workbook_id": wid, "table": table, "column": column,
           "at": datetime.datetime.now().isoformat(timespec="seconds")}
    clay_ui.open_workbook_by_id(page, wid)
    if not colcfg.table_exists(page, table):
        say(f"SKIP: no table {table!r}")
        return {**res, "status": "no_table"}
    colcfg.focus_table_maybe_empty(page, table)
    page.wait_for_timeout(1200)

    if not panel.find_header_scrolling(page, column):
        say(f"ABORT: column {column!r} not found")
        return {**res, "status": "no_column"}

    before = None
    try:
        before = colcfg.column_status(page, column)
    except Exception:
        pass
    say(f"  {column!r} status before: {before!r}")

    clay_ui.open_column_menu(page, column)
    page.wait_for_timeout(1000)

    target = None
    for mi in page.get_by_role("menuitem").all():
        try:
            if mi.is_visible() and mi.inner_text().strip() == RUN_ITEM:
                target = mi
                break
        except Exception:
            pass
    if target is None:
        say(f"ABORT: no {RUN_ITEM!r} item in the {column!r} menu")
        page.keyboard.press("Escape")
        return {**res, "status": "no_run_item", "status_before": before}

    if dry_run:
        say(f"DRYRUN: would click {RUN_ITEM!r} on {column!r}")
        page.keyboard.press("Escape")
        return {**res, "status": "dryrun", "status_before": before}

    # The one deliberate /run/i click — scoped to this column only.
    target.click(timeout=10000)
    say(f"  clicked {RUN_ITEM!r}")
    page.wait_for_timeout(4000)

    # Some builds ask to confirm.
    for label in ("Run", "Confirm", "Yes", "Continue"):
        try:
            btn = page.get_by_role("button", name=label, exact=True)
            if btn.count() and btn.first.is_visible():
                btn.first.click(timeout=5000)
                say(f"  confirmed via {label!r}")
                page.wait_for_timeout(2500)
                break
        except Exception:
            pass

    after = None
    try:
        after = colcfg.column_status(page, column)
    except Exception:
        pass
    say(f"  {column!r} status after: {after!r}")
    return {**res, "status": "ok", "status_before": before,
            "status_after": after}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("workbook_id")
    ap.add_argument("table")
    ap.add_argument("column")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--headed", action="store_true")
    a = ap.parse_args()

    def say(m):
        print(m, flush=True)

    with browser_session.clay_page(headless=not a.headed) as page:
        page.set_viewport_size({"width": 1720, "height": 1500})
        out = run_column(page, a.workbook_id, a.table, a.column, say,
                         dry_run=a.dry_run)
    print("\nRESULT:", json.dumps(out, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
