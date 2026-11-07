"""Delete the 16 columns the "Qasim - Labs - CPJ - People - v2" template created on
one people table -- Size through Country -- and nothing else.

Per the user (2026-08-04): "Delete the columns applied via this template for these
tables i.e from Size till Country. Do not delete anything else."

Clay has no delete API, so this drives the column header menu -> Delete. That makes
it the most dangerous script in this folder, so it is deliberately fail-closed:

  * ALLOWLIST is the only thing that can ever be deleted. A column not on it is
    never touched, whatever the panel offers.
  * The table's OTHER columns are counted before and after. Every Sponsors people
    table has exactly 27 non-template columns (audited across all 41), so if that
    count changes the pass stops immediately rather than continuing to the next
    table -- one wrong deletion must not become forty.
  * Nothing is deleted unless the column list could be read first; an unreadable
    table is skipped, not guessed at.
  * --dry-run reports exactly what it would remove and touches nothing.

  python delete_cpj_columns.py <wid> "<table>" --table-id t_... --dry-run
  python delete_cpj_columns.py <wid> "<table>" --table-id t_...
"""

import argparse
import datetime
import json
import os
import subprocess
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
import state_io                           # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# The template's 16 created columns, in creation order (Size .. Country). This is
# the ONLY set this script may delete.
ALLOWLIST = ["Size", "Type", "Domain", "LinkedIn URL", "Founded", "Industry",
             "Description", "Specialities", "Sub Industry", "Primary Offerings",
             "Business Stage", "Annual Revenue", "Total Funding Amount Range Usd",
             "Side", "Classification", "Country"]

# Every Sponsors people table has exactly this many non-template columns; a change
# means something outside the allowlist went missing.
EXPECTED_OTHERS = 27

COLS_SCRIPT = os.path.join(SCRIPT_DIR, "check_table_columns.py")
STATE = os.path.join(SCRIPT_DIR, os.environ.get(
    "CLAY_CPJ_DELETE_STATE", "cpj_delete_state.json"))


def _wsl(script, *args, timeout=180):
    path = script.replace("C:\\", "/mnt/c/").replace("\\", "/")
    out = subprocess.run(["wsl", "-d", "Ubuntu", "--", "python3", path, *args],
                         capture_output=True, text=True, timeout=timeout,
                         env={**os.environ, "MSYS_NO_PATHCONV": "1"})
    return (out.stdout or "").strip()


def live_columns(table_id, say):
    """Column names straight from Clay, or None if unreadable."""
    try:
        cols = [c for c in _wsl(COLS_SCRIPT, table_id).splitlines() if c.strip()]
    except Exception as e:
        say(f"  !! column read failed: {str(e)[:70]}")
        return None
    return cols or None


def delete_column(page, column, say):
    """Header menu -> Delete, then confirm if Clay asks."""
    if not panel.find_header_scrolling(page, column):
        say(f"  {column}: header not found")
        return False
    clay_ui.open_column_menu(page, column)
    page.wait_for_timeout(900)
    target = None
    for mi in page.get_by_role("menuitem").all():
        try:
            if mi.is_visible() and mi.inner_text().strip() == "Delete":
                target = mi
                break
        except Exception:
            pass
    if target is None:
        say(f"  {column}: no 'Delete' menu item")
        page.keyboard.press("Escape")
        return False
    target.click(timeout=8000)
    page.wait_for_timeout(1500)
    for label in ("Delete", "Delete column", "Confirm", "Yes"):
        try:
            btn = page.get_by_role("button", name=label, exact=True)
            if btn.count() and btn.first.is_visible():
                btn.first.click(timeout=5000)
                page.wait_for_timeout(1200)
                break
        except Exception:
            pass
    page.wait_for_timeout(1200)
    return True


def clean_table(page, wid, table, table_id, say, dry_run=False,
                already_open=False):
    res = {"table": table, "table_id": table_id,
           "at": datetime.datetime.now().isoformat(timespec="seconds")}

    before = live_columns(table_id, say)
    if before is None:
        say(f"  SKIP {table}: columns unreadable — refusing to delete blind")
        return {**res, "status": "unreadable"}
    present = [c for c in ALLOWLIST if c in before]
    others = [c for c in before if c not in ALLOWLIST]
    say(f"  {table}: {len(before)} columns — {len(present)} template, "
        f"{len(others)} other")
    if len(others) != EXPECTED_OTHERS:
        say(f"  ABORT {table}: expected {EXPECTED_OTHERS} non-template columns, "
            f"found {len(others)} — not touching this table")
        return {**res, "status": "unexpected_shape", "others": len(others)}
    if not present:
        say(f"  {table}: nothing from the template present")
        return {**res, "status": "nothing_to_delete"}
    if dry_run:
        say(f"  DRYRUN would delete: {present}")
        return {**res, "status": "dryrun", "would_delete": present}

    if not already_open:
        clay_ui.open_workbook_by_id(page, wid)
    if not colcfg.table_exists(page, table):
        say(f"  SKIP {table}: table not in workbook")
        return {**res, "status": "no_table"}
    colcfg.focus_table_maybe_empty(page, table)
    page.wait_for_timeout(1200)

    deleted, failed = [], []
    # Delete right-to-left: removing a column shifts everything after it, and the
    # rightmost is the least disruptive to the header scroll position.
    for column in reversed(present):
        ok = delete_column(page, column, say)
        (deleted if ok else failed).append(column)
        say(f"    {'deleted' if ok else 'FAILED'} {column!r}")

    after = live_columns(table_id, say)
    if after is None:
        return {**res, "status": "post_check_unreadable",
                "deleted": deleted, "failed": failed}
    left_template = [c for c in ALLOWLIST if c in after]
    left_others = [c for c in after if c not in ALLOWLIST]
    say(f"  after: {len(after)} columns — {len(left_template)} template, "
        f"{len(left_others)} other")
    lost = [c for c in others if c not in after]
    if lost:
        say(f"  !! COLLATERAL LOSS on {table}: {lost} — STOPPING")
        return {**res, "status": "collateral_loss", "lost": lost,
                "deleted": deleted, "failed": failed}
    return {**res,
            "status": "clean" if not left_template else "partial",
            "deleted": deleted, "failed": failed,
            "left_template": left_template, "others_after": len(left_others)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("workbook_id")
    ap.add_argument("table")
    ap.add_argument("--table-id", required=True)
    ap.add_argument("--event")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--headed", action="store_true")
    a = ap.parse_args()

    def say(m):
        print(m, flush=True)

    if a.dry_run:
        out = clean_table(None, a.workbook_id, a.table, a.table_id, say,
                          dry_run=True)
    else:
        with browser_session.clay_page(headless=not a.headed) as page:
            page.set_viewport_size({"width": 1720, "height": 1200})
            out = clean_table(page, a.workbook_id, a.table, a.table_id, say)
        state = state_io.load_json(STATE, {})
        state[f"{a.event or a.workbook_id}|{a.table}"] = out
        state_io.save_json(STATE, state)
    print("\nRESULT:", json.dumps(out, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
