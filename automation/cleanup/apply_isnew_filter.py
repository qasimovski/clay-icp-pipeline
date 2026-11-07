"""Filter each Highways / Competitive Events view on `Is New`.

Per the user (2026-08-13): opening the `Is New` column's header menu and
choosing "Filter on this column" applies `Is New is Checked` on its own — there
is no value to set afterwards. This pass does exactly that and then proves it.

WHY THIS MATTERS: the ledger only saves money if the view is filtered on
`Is New` BEFORE any paid column runs (CLAUDE.md, blocklist_ledger/README.md).
For Highways the company enrichment has already run, so this filter is what
protects the NEXT passes — People, and any future Highways event.

IDEMPOTENCY IS THE WHOLE RISK HERE. "Filter on this column" ADDS a row; running
this twice yields two identical `Is New is Checked` filters. So a table that
already has any filter is skipped rather than re-filtered, and the count is read
from the funnel badge, not from the chip.

TWO DETECTION TRAPS, both already paid for elsewhere in this repo:
  * The filter chip's column label is absent from the DOM entirely — no text,
    aria, title or input value. The funnel badge next to the rows chip is the
    only readable "how many filters" signal (people_search.filter_count).
  * The grid header row carries a column literally named "Is New", so a loose
    text match reports the filter as applied when it is not. Any chip lookup
    must stay inside the open popover's tight y-band (people_search.CHIP).

    python apply_isnew_filter.py --list
    python apply_isnew_filter.py --only "National Diesel Dirt & Turf Expo"
    python apply_isnew_filter.py --table sponsors_normalized
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
import people_search as ps          # noqa: E402
import read_view_filters as rvf     # noqa: E402
import state_io                     # noqa: E402
# The column-menu -> "Filter on this column" click, already hardened against the
# menu rendering late. Private by name only; this is its second caller.
from apply_view_filters import _filter_on_column   # noqa: E402

COLUMN = "Is New"


# Scope selects which folder's tables to filter. `ce` walks the Competitive
# Events normalized tables; `ps` / `buyside` walk the search-built `Companies`
# tables in Products & Services / BuySide P&S.
SCOPES = {
    "ce":      ("highways_ce_{key}_manifest.json",
                "highways_isnew_filter_state_{key}.json"),
    "ps":      ("highways_ps_companies_manifest.json",
                "highways_ps_isnew_filter_state.json"),
    "buyside": ("highways_bs_companies_manifest.json",
                "highways_bs_isnew_filter_state.json"),
}


def manifest_path(scope, table):
    return os.path.join(SCRIPT_DIR,
                        SCOPES[scope][0].format(key=table.split("_")[0]))


def state_path(scope, table):
    return os.path.join(SCRIPT_DIR,
                        SCOPES[scope][1].format(key=table.split("_")[0]))


# LOCAL COPIES OF THE TOOLBAR ANCHORS, deliberately not people_search's.
#
# ps.ROWS_ANCHOR and ps.filter_count scan y 85..120. These Highways workbooks
# render the toolbar HIGHER — the rows chip sits at y≈74 (measured 2026-08-13) —
# so both returned nothing and every check silently read "0 filters", which made
# a correctly-filtered table look unfiltered. Re-basing here rather than
# widening the shared band keeps the P&S/Labs passes on the geometry they were
# hardened against.
#
# The band below spans both layouts (60..125) and still excludes the grid header
# at y≈143, which carries a column literally named "Is New".
_ANCHOR = """()=>{
  const norm=s=>(s||'').replace(/\\s+/g,' ').trim();
  let best=null;
  for(const el of document.querySelectorAll('*')){
    if(el.children.length) continue;
    const t=norm(el.textContent);
    if(!/^[\\d.,KM]+\\/[\\d.,KM]+( rows)?$/.test(t)) continue;
    const r=el.getBoundingClientRect();
    if(r.y<60||r.y>125) continue;
    if(!best||r.right>best.right)
      best={t, right:Math.round(r.right), y:Math.round(r.y+r.height/2)};
  }
  return best;
}"""

# The funnel badge: a bare integer just right of the rows chip, same band.
_BADGE = """(x0)=>{
  const norm=s=>(s||'').replace(/\\s+/g,' ').trim();
  for(const el of document.querySelectorAll('*')){
    if(el.children.length) continue;
    const r=el.getBoundingClientRect();
    if(r.width===0||r.y<60||r.y>125) continue;
    if(r.x<x0+10||r.x>x0+100) continue;
    const t=norm(el.textContent);
    if(/^\\d+$/.test(t)) return parseInt(t,10);
  }
  return 0;
}"""


def rows_chip(page):
    a = page.evaluate(_ANCHOR)
    return a["t"] if a else None


def filter_count(page):
    """How many filters the view has, per the badge beside the funnel.

    The chip's column label is absent from the DOM (no text/aria/title/value),
    so the badge plus the rows chip is the only readable signal — see
    people_search.filter_count for the original write-up.
    """
    a = page.evaluate(_ANCHOR)
    return page.evaluate(_BADGE, a["right"]) if a else 0


def read_filters(page, say):
    """[[row texts], ...] for the view's current filters, or [] if none."""
    if not filter_count(page):
        return []
    a = page.evaluate(_ANCHOR)
    # Best-effort, 2 attempts. The popover's row text is a nice-to-have: the
    # funnel badge plus the rows chip is what actually proves the filter, and on
    # this layout the popover often will not open for the read (it returns []).
    # Four attempts cost ~13s per table across 67 tables for no added certainty.
    for _ in range(2):
        page.mouse.click(a["right"] + 32, a["y"])
        page.wait_for_timeout(2000)
        if page.evaluate(ps.FIND, "Add filter"):
            break
        page.keyboard.press("Escape")
        page.wait_for_timeout(800)
    rows = rvf.group_rows(page.evaluate(rvf._ROWS_JS, [130, 300]))
    page.keyboard.press("Escape")
    page.wait_for_timeout(600)
    return rows


def one(page, name, rec, args):
    slug = "".join(c if c.isalnum() else "_" for c in name)[:60]
    clay_ui.open_workbook_by_id(page, rec["workbook_id"])
    page.wait_for_timeout(8000)
    colcfg.focus_table_maybe_empty(page, rec.get("table_name"))
    page.wait_for_timeout(2500)

    before_rows = rows_chip(page)
    n_before = filter_count(page)
    print(f"  before: {n_before} filter(s), rows chip {before_rows!r}", flush=True)
    if n_before:
        # Never stack a second identical filter. Report what is there so a
        # wrong pre-existing filter is visible rather than silently kept.
        existing = read_filters(page, lambda m: None)
        print(f"  already filtered: {existing} — skipping", flush=True)
        return {"status": "already_filtered", "filters": existing,
                "rows_chip": before_rows}

    _filter_on_column(page, COLUMN)
    page.wait_for_timeout(2500)

    n_after = filter_count(page)
    after_rows = rows_chip(page)
    rows = read_filters(page, lambda m: None)
    print(f"  after: {n_after} filter(s), rows chip {after_rows!r}, rows {rows}",
          flush=True)
    if n_after != 1:
        raise browser_session.VerificationError(
            f"expected exactly 1 filter after filtering on {COLUMN!r}, "
            f"funnel badge says {n_after}")
    shot = page.screenshot(path=os.path.join(args.shots_dir, f"{slug}_filtered.png"))
    return {"status": "filtered", "filters": rows, "n_filters": n_after,
            "rows_chip_before": before_rows, "rows_chip_after": after_rows}


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scope", default="ce", choices=sorted(SCOPES))
    ap.add_argument("--table", default="exhibitors_normalized",
                    choices=["exhibitors_normalized", "sponsors_normalized"],
                    help="ce scope only")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--only", action="append", default=[])
    ap.add_argument("--limit", type=int)
    ap.add_argument("--redo", action="store_true")
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--shots-dir",
                    default=os.path.join(SCRIPT_DIR, "highways_filter_shots"))
    args = ap.parse_args()
    os.makedirs(args.shots_dir, exist_ok=True)

    MANIFEST = manifest_path(args.scope, args.table)
    STATE = state_path(args.scope, args.table)
    manifest = state_io.load_json(MANIFEST)
    state = state_io.load_json(STATE)

    names = sorted(manifest, key=lambda n: (manifest[n].get("rows") or 0, n))
    if args.only:
        names = [n for n in names if n in args.only]
    todo = [n for n in names
            if args.redo
            or state.get(n, {}).get("status") not in ("filtered", "already_filtered")]
    if args.limit:
        todo = todo[:args.limit]

    if args.list:
        print(f"table: {args.table}   column: {COLUMN!r}")
        print(f"\n{len(todo)} pending of {len(names)} in scope:")
        for n in names:
            mark = state.get(n, {}).get("status", "pending")
            print(f"  {mark:17s} {str(manifest[n].get('rows')):>6} rows  {n}")
        return 0

    failures = []
    for i, name in enumerate(todo, 1):
        rec = manifest[name]
        print(f"\n=== [{i}/{len(todo)}] {name}  ({rec.get('rows')} rows)",
              flush=True)
        try:
            with browser_session.clay_page(headless=not args.headed) as page:
                state[name] = one(page, name, rec, args)
        except Exception as e:
            traceback.print_exc()
            failures.append(name)
            state[name] = {**state.get(name, {}), "status": "failed",
                           "error": str(e)[:300]}
        state_io.save_json(STATE, state)

    print(f"\ndone: {len(todo) - len(failures)}/{len(todo)}")
    for n in failures:
        print(f"  failed: {n}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
