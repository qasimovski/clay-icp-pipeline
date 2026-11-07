"""Filter each EVCharge Products & Services `Companies` view to `Is New` checked.

The gate that makes every downstream paid column cheaper: run it BEFORE the
People - Supabase column and before any enrichment, so only first-sighting
companies (and the people under them) flow onward. Filters cost 0 Actions, so
this step is free and is where the whole saving comes from.

Mechanism is the column header menu -> **"Filter on this column"**, which
pre-fills the operator for a boolean column as *is checked* — the same route
`apply_view_filters.py` uses. That is simpler and far more reliable than
building the filter by hand through the funnel popover, which needs the column
dropdown scrolled to find `Is New`.

Idempotent and read-mostly: a view that already carries a filter is left alone
rather than having a second one stacked on it. The check is the funnel's own
badge count, because the filter chip's column label is NOT in the DOM (see the
clay-filter-ui-detection note) — the badge plus the row count are the only
readable signals.

    python filter_ps_is_new.py --list
    python filter_ps_is_new.py "Traffic, Road Safety & Enforcement Systems"
    python filter_ps_is_new.py --all
"""

import argparse
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
AUTO_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, SCRIPT_DIR)
sys.path.insert(0, os.path.join(AUTO_DIR, "clay_sync"))
sys.path.insert(0, os.path.join(AUTO_DIR, "build_automation"))

import clay_ui                      # noqa: E402
import browser_session              # noqa: E402
import column_config as colcfg      # noqa: E402
import build_ps_companies as bps    # noqa: E402
import build_ps_people as bpp       # noqa: E402
import state_io                     # noqa: E402

VerificationError = browser_session.VerificationError

SHOTS_DIR = os.path.join(SCRIPT_DIR, "ps_filter_shots")
TABLE = "Companies"
COLUMN = "Is New"

# The toolbar's "N/N rows" chip. The funnel sits immediately to its right and
# its badge carries the filter count. On narrow layouts Clay drops the word
# "rows", leaving a bare "1,140/1,140" next to the columns chip "14/16" — so
# take the RIGHTMOST such chip.
ROWS_CHIP = """()=>{
  const norm=s=>(s||'').replace(/\\s+/g,' ').trim();
  let best=null;
  for(const el of document.querySelectorAll('*')){
    if(el.children.length) continue;
    const t=norm(el.textContent);
    if(!/^[\\d.,KM]+\\/[\\d.,KM]+( rows)?$/.test(t)) continue;
    const r=el.getBoundingClientRect();
    if(r.y<60||r.y>135) continue;
    if(!best||r.right>best.right)
      best={t, right:Math.round(r.right), y:Math.round(r.y+r.height/2)};
  }
  return best;
}"""

# The number rendered in the funnel's badge, just right of the rows chip.
FILTER_BADGE = """(x0)=>{
  const norm=s=>(s||'').replace(/\\s+/g,' ').trim();
  for(const el of document.querySelectorAll('*')){
    if(el.children.length) continue;
    const r=el.getBoundingClientRect();
    if(r.width===0||r.y<60||r.y>135) continue;
    if(r.x<x0+40||r.x>x0+95) continue;
    const t=norm(el.textContent);
    if(/^\\d+$/.test(t)) return parseInt(t,10);
  }
  return 0;
}"""


def say(m):
    print(m, flush=True)


def rows_and_filters(page):
    chip = page.evaluate(ROWS_CHIP)
    if not chip:
        return None, 0
    return chip["t"], page.evaluate(FILTER_BADGE, chip["right"])


def filter_is_new(page, name, args):
    slug = "".join(c if c.isalnum() else "_" for c in name)[:60]
    before, nfilters = rows_and_filters(page)
    say(f"  before: rows={before} filters={nfilters}")
    if nfilters:
        say(f"  view already has {nfilters} filter(s) — leaving it alone")
        return {"status": "already_filtered", "rows_before": before,
                "filters": nfilters}

    clay_ui.open_column_menu(page, COLUMN)
    page.wait_for_timeout(1200)
    hit = None
    for mi in page.get_by_role("menuitem").all():
        try:
            if mi.is_visible() and "Filter on this column" in mi.inner_text():
                hit = mi
                break
        except Exception:
            pass
    if not hit:
        bps.shot(page, args.shots_dir, f"{slug}_no_filter_item")
        raise VerificationError(
            f"'Filter on this column' not in the {COLUMN!r} column menu")
    hit.click(timeout=8000)
    page.wait_for_timeout(4000)
    page.keyboard.press("Escape")
    page.wait_for_timeout(3000)

    after, nfilters = rows_and_filters(page)
    say(f"  after : rows={after} filters={nfilters}")
    bps.shot(page, args.shots_dir, f"{slug}_filtered")
    if nfilters != 1:
        raise VerificationError(
            f"expected exactly 1 filter after 'Filter on this column', "
            f"got {nfilters} (rows {before} -> {after})")
    # Clay defaults a boolean column's filter to "is checked", which is what we
    # want — but the chip's label is not in the DOM, so the row count is the
    # only confirmation available. Equal counts are legitimate (every row new),
    # so this is reported, never treated as failure.
    if after == before:
        say("  note: row count unchanged — every row has Is New checked")
    return {"status": "filtered", "rows_before": before, "rows_after": after,
            "filters": nfilters}


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("category", nargs="?")
    ap.add_argument("--icp", default="evcharge")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--all", action="store_true", help="every workbook in scope")
    ap.add_argument("--only", action="append", default=[])
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--shots-dir", default=SHOTS_DIR)
    args = ap.parse_args()

    wbs = bpp.company_workbooks(args.icp)
    path = os.path.join(SCRIPT_DIR, f"ps_filter_state_{args.icp}.json")
    state = state_io.load_json(path)

    if args.list:
        for n in sorted(wbs):
            print(f"  {state.get(n, {}).get('status', 'pending'):17s} {n}")
        return 0

    if args.all or args.only:
        names = sorted(args.only or wbs)
    elif args.category:
        names = [args.category]
    else:
        ap.error("give a category, --only, or --all (see --list)")
    unknown = [n for n in names if n not in wbs]
    if unknown:
        print(f"unknown: {unknown}")
        return 1

    failures = []
    for i, name in enumerate(names, 1):
        print(f"\n=== [{i}/{len(names)}] {name}", flush=True)
        try:
            with browser_session.clay_page(headless=not args.headed) as page:
                clay_ui.open_workbook_by_id(page, wbs[name])
                page.wait_for_timeout(8000)
                colcfg.focus_table_maybe_empty(page, TABLE)
                page.wait_for_timeout(2500)
                state[name] = filter_is_new(page, name, args)
        except Exception as e:
            print(f"  FAILED: {e}")
            failures.append(name)
            state[name] = {"status": "failed", "error": str(e)[:300]}
        state_io.save_json(path, state)

    print(f"\ndone: {len(names) - len(failures)}/{len(names)}")
    if failures:
        for n in failures:
            print(f"  failed: {n}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
