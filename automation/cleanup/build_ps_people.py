"""Run "Find people at these companies" on one Products & Services workbook.

Per category workbook built by build_ps_companies.py:

  1. open the workbook's `Companies` table,
  2. Tools -> Import -> "Find people at these companies",
  3. expand Filters, switch to the `</>` Query mode,
  4. read Clay's prefilled `clay.filter_to_companies(...)` clause and compose
     the seller persona query around it (config/icps/<icp>/seller_people.yaml),
  5. only with --run: Continue -> save -> rename the new table to `People`.

The company scope is Clay's, not ours. Its prefill names live table/view/filter
ids that differ per workbook, so the query is composed at run time rather than
stored. If the clause cannot be read the pass STOPS: replacing the editor
without it turns a search over ~4,000 companies into a search over all of Clay.

RUN-NOW vs CONFIGURE-ONLY. Steps 1-4 spend nothing; saving the results into a
table is what costs credits, so it sits behind an explicit `--run` (CLAUDE.md:
paid work is never the default).

RE-ENTRY. The guard asks Clay: a workbook that already has a `People` table is
skipped, and `list_table_tabs` failing is treated as "already there".

    python build_ps_people.py --list
    python build_ps_people.py "Finance, Investment & Funding"
    python build_ps_people.py "Finance, Investment & Funding" --run
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
import build_ps_companies as bps    # noqa: E402
import ps_people_config as ppc      # noqa: E402
import ps_buyside_people_config as bpc  # noqa: E402
import ps_query_config as psq       # noqa: E402
import state_io                     # noqa: E402

VerificationError = browser_session.VerificationError

SHOTS_DIR = os.path.join(SCRIPT_DIR, "ps_people_shots")
COMPANIES_TABLE = "Companies"
PEOPLE_TABLE = "People"

# Tools panel: Ctrl+E toggles it (see the clay-csv-export-ui note). Its tab strip
# sits at y~194 — people_search.py's older 200-235 band no longer matches, which
# is why that module's open_tools_import fails against today's UI.
TOOLS_TAB_Y = 194
IMPORT_TAB_X = 1360

# The Filters section header in the Find-people panel is a plain <div> with no
# role and no cursor affordance, so neither get_by_role nor a coordinate click on
# the chevron registers. Click the text node's PARENT element directly.
EXPAND_FILTERS = """()=>{
  const norm=s=>(s||'').replace(/\\s+/g,' ').trim();
  for(const el of document.querySelectorAll('*')){
    if(el.children.length) continue;
    if(norm(el.textContent)!=='Filters') continue;
    const r=el.getBoundingClientRect();
    if(r.width===0||r.x>500) continue;
    el.parentElement.click();
    return true;
  }
  return false;
}"""


def say(msg):
    print(msg, flush=True)


def open_find_people(page, shots_dir, slug):
    """Tools -> Import -> Find people at these companies."""
    page.keyboard.press("Control+E")
    page.wait_for_timeout(4000)
    # Locate the Import tab rather than clicking a fixed y. TOOLS_TAB_Y=194 was
    # measured with the orange credit banner on screen; once it was dismissed
    # the whole page shifted up ~28px and the click landed on nothing. Same
    # failure as every other hardcoded panel coordinate in this repo.
    tab = page.evaluate(bps.FIND, "Import")
    if tab and tab["x"] > 1240:
        page.mouse.click(tab["x"], tab["y"])
    else:
        say(f"  'Import' tab not located ({tab}); falling back to the constant")
        page.mouse.click(IMPORT_TAB_X, TOOLS_TAB_Y)
    page.wait_for_timeout(3500)
    item = page.evaluate(bps.FIND, "Find people at these companies")
    if not item:
        bps.shot(page, shots_dir, f"{slug}_no_find_people")
        raise VerificationError(
            "'Find people at these companies' not in Tools > Import")
    say(f"  clicking Find people at these companies {item}")
    page.mouse.click(item["x"], item["y"])
    page.wait_for_timeout(12000)


def enter_query_mode(page, shots_dir, slug):
    """Expand Filters, then click the `</>` toggle beside the funnel."""
    if not page.evaluate(EXPAND_FILTERS):
        bps.shot(page, shots_dir, f"{slug}_no_filters_header")
        raise VerificationError("Filters section header not found")
    page.wait_for_timeout(3500)
    anchor = page.evaluate(bps.FIND, "Add filter")
    if not anchor:
        bps.shot(page, shots_dir, f"{slug}_filters_not_expanded")
        raise VerificationError("Filters did not expand ('Add filter' absent)")
    for attempt in range(4):
        page.mouse.click(122, anchor["y"])
        page.wait_for_timeout(3500)
        if page.evaluate(bps.ACE_GET) is not None:
            say("  query mode on")
            return True
        say(f"  query mode did not open (attempt {attempt + 1})")
    raise VerificationError("could not switch the Filters panel to query mode")


def build(page, name, workbook_id, args):
    shots_dir = args.shots_dir
    clay_ui.open_workbook_by_id(page, workbook_id)
    page.wait_for_timeout(9000)

    # Ask the CLI, not the tab strip: the strip carries UI chrome that has been
    # mistaken for a table (see bps.real_tables).
    tabs = bps.real_tables(workbook_id)
    say(f"  tables: {tabs}")
    if COMPANIES_TABLE not in tabs:
        raise VerificationError(
            f"no {COMPANIES_TABLE!r} table in {name!r} (tabs: {tabs}) — build "
            f"the companies table first")
    if PEOPLE_TABLE in tabs and not args.force:
        say(f"SKIP {name}: workbook already has {PEOPLE_TABLE!r}")
        return {"workbook_id": workbook_id, "status": "built", "tables": tabs}
    # A table beyond Companies that is NOT named People means a previous run
    # created it and died before the rename (Clay names it after the query).
    # Rename it instead of building another: without this the next run makes a
    # SECOND people table and pays for the rows twice.
    extra = [t for t in tabs if t not in (COMPANIES_TABLE, PEOPLE_TABLE)]
    if extra and not args.force:
        say(f"  table present but not {PEOPLE_TABLE!r}: {extra} — renaming")
        renamed = bps.rename_table(page, PEOPLE_TABLE, current=extra[0])
        tabs = bps.real_tables(workbook_id)
        return {"workbook_id": workbook_id, "status": "built",
                "renamed": renamed, "tables": tabs}

    open_find_people(page, shots_dir, args.slug)
    enter_query_mode(page, shots_dir, args.slug)

    prefill = page.evaluate(bps.ACE_GET)
    say(f"  prefill: {prefill!r}")
    scope = ppc.company_scope_from_prefill(prefill)
    if not scope:
        bps.shot(page, shots_dir, f"{args.slug}_no_company_scope")
        raise VerificationError(
            "Clay's prefill carries no clay.filter_to_companies(...) clause. "
            "Refusing to continue: without it this searches all of Clay, not "
            "this workbook's companies.")
    say(f"  company scope: {scope}")

    # The buy side has a DIFFERENT job-title list per top level, so it uses its
    # own config module; the seller side applies one persona to every workbook.
    if args.buyside:
        query = bpc.render(args.people_cfg, name, scope)
    else:
        # Countries may be per CATEGORY (Highways) rather than per ICP
        # (EVCharge). Pass this category's list so the people search matches
        # the geography of the company search that built the table; None falls
        # back to the ICP-level list inside render().
        cat = {c["name"]: c for c in psq.load(args.icp)["categories"]}.get(name)
        query = ppc.render(args.people_cfg, scope,
                           non_sector=args.non_sector,
                           countries=(cat or {}).get("countries"))
    say(f"  query is {len(query)} chars, non_sector={args.non_sector}")
    if not bps.set_query(page, query):
        raise VerificationError("query did not paste verbatim — refusing to continue")

    preview = bps.read_preview(page)
    limits = page.evaluate(bps.LIMITS)
    say(f"  preview: {preview}")
    say(f"  result limits (left untouched; the cap is in the query): {limits}")
    bps.shot(page, shots_dir, f"{args.slug}_people_query")

    if not args.run:
        say(f"CONFIGURED (not run) {name}")
        return {"workbook_id": workbook_id, "status": "configured",
                "preview": preview}

    cont = page.evaluate(bps.FIND, "Continue")
    if not cont:
        bps.shot(page, shots_dir, f"{args.slug}_no_continue")
        raise VerificationError("'Continue' not found")
    say(f"  clicking Continue {cont}")
    page.mouse.click(cont["x"], cont["y"])
    page.wait_for_timeout(9000)
    bps.shot(page, shots_dir, f"{args.slug}_after_continue")

    newt = page.evaluate(bps.FIND, "Save to new table")
    if newt:
        say(f"  clicking Save to new table {newt}")
        page.mouse.click(newt["x"], newt["y"])
        page.wait_for_timeout(25000)

    # The enrichment checklist again — every box stays unchecked.
    say(f"  commit: {bps.commit_table(page, shots_dir, args.slug)}")
    bps.shot(page, shots_dir, f"{args.slug}_people_table")
    say(f"  url: {page.url}")

    # Reload before renaming: straight after the save flow the breadcrumb's
    # chevron does not open the TABLE menu, so the rename reliably fails. Same
    # fix as build_ps_companies.
    clay_ui.open_workbook_by_id(page, workbook_id)
    page.wait_for_timeout(9000)
    extra = [t for t in bps.real_tables(workbook_id)
             if t not in (COMPANIES_TABLE, PEOPLE_TABLE)]
    renamed = bps.rename_table(page, PEOPLE_TABLE,
                               current=extra[0] if extra else None)
    tabs = bps.real_tables(workbook_id)
    say(f"  tables now: {tabs}")
    if PEOPLE_TABLE not in tabs:
        raise VerificationError(
            f"no {PEOPLE_TABLE!r} table after the run (tabs: {tabs})")
    return {"workbook_id": workbook_id, "status": "built", "preview": preview,
            "renamed": renamed, "tables": tabs}


def company_workbooks(icp):
    """{category: workbook_id} for the categories whose Companies table exists,
    read from the companies pass's state file."""
    path = os.path.join(SCRIPT_DIR, f"ps_companies_state_{icp}.json")
    state = state_io.load_json(path)
    if not state:
        raise SystemExit(
            f"no companies state at {path} — run build_ps_companies_rollout.py "
            f"first; this pass needs the workbook ids it recorded")
    return {name: rec["workbook_id"] for name, rec in state.items()
            if rec.get("status") == "built" and rec.get("workbook_id")}


def anchored_categories(icp):
    """Categories that took an EV anchor in products_services.yaml.

    Those are exactly the horizontal, "non sector specific" ones, so they get
    the sheet's column-D sector keywords added to the title list.
    """
    cfg = psq.load(icp)
    return {c["name"] for c in cfg["categories"] if c.get("ev_anchor")}


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    psq.add_cli_args(ap)
    ap.add_argument("category", nargs="?")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--print-query", action="store_true",
                    help="render with a placeholder company scope, no browser")
    ap.add_argument("--run", action="store_true",
                    help="save the results into a People table (SPENDS CREDITS)")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--shots-dir", default=SHOTS_DIR)
    args = ap.parse_args()

    args.buyside = os.path.exists(bpc.config_path(args.icp))
    if args.buyside:
        cfg = bpc.load(args.icp)
        bpc.validate(cfg)
    else:
        cfg = ppc.load(args.icp)
        ppc.validate(cfg)
    args.people_cfg = cfg
    wbs = company_workbooks(args.icp)
    anchored = anchored_categories(args.icp)

    if args.list:
        print(f"{len(wbs)} workbooks with a {COMPANIES_TABLE} table:")
        for name in sorted(wbs):
            tag = "non-sector" if name in anchored else "sector"
            print(f"  {tag:11s}  {name}")
        return 0
    if not args.category:
        ap.error("a category name is required (see --list)")
    if args.category not in wbs:
        print(f"unknown category {args.category!r}; see --list")
        return 1

    args.non_sector = args.category in anchored
    args.slug = "".join(c if c.isalnum() else "_" for c in args.category)[:60]

    if args.print_query:
        placeholder = 'clay.filter_to_companies(@table("t_X:gv_X:f_X"))'
        print(bpc.render(cfg, args.category, placeholder) if args.buyside
              else ppc.render(cfg, placeholder, non_sector=args.non_sector))
        return 0

    say(f"{args.category}: {'RUN' if args.run else 'configure-only'}, "
        f"non_sector={args.non_sector}")
    with browser_session.clay_page(headless=not args.headed) as page:
        result = build(page, args.category, wbs[args.category], args)
    say(f"result: {result}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
