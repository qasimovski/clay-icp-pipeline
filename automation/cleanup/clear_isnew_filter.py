"""Remove the `Is New` view filter from named BuySide P&S Companies tables.

WHY THIS EXISTS, and why it is deliberately NOT a general "unfilter everything"
pass: the Highways ledger dedupes COMPANIES across the whole ICP, sell side and
buy side together. Two buy-side categories — Major Engineering Contractors /
Tier 1 Contractor and Sub-engineering contractors — came back 0/3,863 and
0/3,707 after the Is New filter, because every one of those firms had already
been recorded by the sell-side sweeps.

That is the wrong outcome for those two. The sell-side tables hold the SALES and
BD people at Balfour Beatty; the buy-side rows exist to reach the Head of
Highways and the Bid/Procurement people at the same firm. Different personas,
same companies — so the company-level dedupe should not gate them. Per the user
(2026-08-15), the filter comes off these views and the People search runs over
the full company set.

Everything else keeps its filter. Pass the tables explicitly; there is no
"all" mode, so this cannot quietly unfilter a view that should stay gated.

    python clear_isnew_filter.py --only "Sub-engineering contractors"
    python clear_isnew_filter.py --only "A" --only "B"
"""

import argparse
import os
import sys
import traceback

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
sys.path.insert(0, os.path.join(os.path.dirname(SCRIPT_DIR), "clay_sync"))
sys.path.insert(0, os.path.join(os.path.dirname(SCRIPT_DIR), "build_automation"))

import clay_ui                      # noqa: E402
import browser_session              # noqa: E402
import column_config as colcfg      # noqa: E402
import state_io                     # noqa: E402
import apply_isnew_filter as aif    # noqa: E402

MANIFEST = os.path.join(SCRIPT_DIR, "highways_bs_companies_manifest.json")
STATE = os.path.join(SCRIPT_DIR, "highways_bs_isnew_filter_state.json")


def clear(page, name, rec):
    clay_ui.open_workbook_by_id(page, rec["workbook_id"])
    page.wait_for_timeout(8000)
    colcfg.focus_table_maybe_empty(page, rec.get("table_name"))
    page.wait_for_timeout(2500)

    before_n = aif.filter_count(page)
    before_rows = aif.rows_chip(page)
    print(f"  before: {before_n} filter(s), rows {before_rows!r}", flush=True)
    if not before_n:
        return {"status": "already_clear", "rows_chip": before_rows}

    # Open the filter popover, then use its own "Clear filters" control rather
    # than deleting rows one at a time.
    a = page.evaluate(aif._ANCHOR)
    opened = False
    for _ in range(4):
        page.mouse.click(a["right"] + 32, a["y"])
        page.wait_for_timeout(2200)
        if page.evaluate(aif.ps.FIND, "Add filter"):
            opened = True
            break
        page.keyboard.press("Escape")
        page.wait_for_timeout(1000)
    if not opened:
        raise browser_session.VerificationError("filter popover would not open")

    btn = page.get_by_role("button", name="Clear filters", exact=True)
    if not btn.count():
        raise browser_session.VerificationError(
            "no 'Clear filters' button in the open filter popover")
    btn.first.click(timeout=8000)
    page.wait_for_timeout(2500)
    page.keyboard.press("Escape")
    page.wait_for_timeout(1500)

    after_n = aif.filter_count(page)
    after_rows = aif.rows_chip(page)
    print(f"  after: {after_n} filter(s), rows {after_rows!r}", flush=True)
    if after_n:
        raise browser_session.VerificationError(
            f"expected 0 filters after clearing, badge says {after_n}")
    return {"status": "filter_cleared", "rows_chip_before": before_rows,
            "rows_chip_after": after_rows,
            "note": "Is New filter deliberately removed — the buy-side personas "
                    "at these firms are distinct from the sell-side ones "
                    "(user, 2026-08-15)"}


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", action="append", required=True,
                    help="workbook name to unfilter (repeatable). Required — "
                         "there is no 'all' mode on purpose.")
    ap.add_argument("--headed", action="store_true")
    args = ap.parse_args()

    manifest = state_io.load_json(MANIFEST)
    state = state_io.load_json(STATE)
    unknown = [n for n in args.only if n not in manifest]
    if unknown:
        raise SystemExit(f"not in the BuySide manifest: {unknown}")

    failures = []
    for i, name in enumerate(args.only, 1):
        print(f"\n=== [{i}/{len(args.only)}] {name}", flush=True)
        try:
            with browser_session.clay_page(headless=not args.headed) as page:
                state[name] = {**state.get(name, {}),
                               **clear(page, name, manifest[name])}
        except Exception as e:
            traceback.print_exc()
            failures.append(name)
            state[name] = {**state.get(name, {}), "status": "clear_failed",
                           "error": str(e)[:300]}
        state_io.save_json(STATE, state)
    print(f"\ndone: {len(args.only) - len(failures)}/{len(args.only)}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
