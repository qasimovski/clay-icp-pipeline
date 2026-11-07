"""Build one EVCharge Products & Services companies workbook.

Per Top level category in config/icps/<icp>/products_services.yaml:

  1. create a workbook in `10. EVCharge [2026 - Qasim] -> Products & Services`
     and rename it to the category name,
  2. Find companies -> "Build with filters instead" -> the `</>` Query mode
     toggle,
  3. paste that category's rendered Clay search query verbatim,
  4. read back the preview count and the Result limits inputs,
  5. only with --run: Continue -> save the results into a table, renamed to
     `Companies`.

The workbook carries the category name; the table inside it is always
`Companies` — the same shape as the Labs Product & Services folder, so the
downstream people pass can address one table name across every workbook.

HARD SCOPE: every navigation is by the Products & Services folder id from
config/local.yaml (`evcharge_ps_folder_id`). Nothing outside the EVCharge folder
is reachable from here, and there is no delete operation.

RUN-NOW vs CONFIGURE-ONLY. Steps 1-4 create and configure but spend nothing;
materialising the search results into a table is what costs credits, so it is
behind an explicit `--run`. The default is configure-only — the opposite of the
usual argparse default, on purpose (CLAUDE.md: paid work is never the default).

RE-ENTRY. The guard asks Clay, not a state file: the folder listing gives the
workbook (create it only if the category has none) and `list_table_tabs` gives
its tables (refuse to re-run the search if one already exists). Both fail closed
— an unreadable listing is treated as "already there", never as "safe to spend".

    python build_ps_companies.py --list
    python build_ps_companies.py "Charging Software, Platforms & Data"
    python build_ps_companies.py "Charging Software, Platforms & Data" --run
    python build_ps_companies.py "..." --headed --shots-dir <dir>
"""

import argparse
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
AUTO_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, SCRIPT_DIR)
sys.path.insert(0, os.path.join(AUTO_DIR, "clay_sync"))
sys.path.insert(0, os.path.join(AUTO_DIR, "build_automation"))

import clay_ui                     # noqa: E402
import browser_session             # noqa: E402
import evcharge_ps_scope as scope  # noqa: E402
import ps_query_config as psq      # noqa: E402

VerificationError = browser_session.VerificationError

SHOTS_DIR = os.path.join(SCRIPT_DIR, "ps_companies_shots")

# --------------------------------------------------------------------------
# DOM helpers. Coordinates are last-resort anchors; everything that can be
# addressed by role or by text is. Verified against the live panel 2026-08-07.
# --------------------------------------------------------------------------

# Smallest visible element containing `txt` — Clay splits most labels across
# child nodes, so an exact-leaf match misses them.
FIND = """(txt)=>{
  const norm=s=>(s||'').replace(/\\s+/g,' ').trim();
  let best=null, bestArea=1e12;
  for(const el of document.querySelectorAll('*')){
    const t=norm(el.textContent);
    if(!t||t.length>70||!t.includes(txt)) continue;
    const r=el.getBoundingClientRect();
    if(r.width===0||r.height===0) continue;
    const a=r.width*r.height;
    if(a<bestArea){bestArea=a; best={t, x:Math.round(r.x+r.width/2),
                                     y:Math.round(r.y+r.height/2)};}
  }
  return best;
}"""

# Clay's query editor is Ace. Read and write through Ace's own API — the DOM
# only holds the rendered, virtualized slice of the document.
ACE_GET = """()=>{
  const el=document.querySelector('.ace_editor');
  if(!el) return null;
  try { return el.env.editor.getValue(); } catch(e) { return 'ERR:'+e.message; }
}"""

# The match-count line the panel prints once a query parses, e.g.
# "50 of 5,922 (~5,922 found)". Bounded to short leaves on purpose: the result
# grid renders full company descriptions, and several of those contain the word
# "found" ("Founded in 1997, ...") — an unbounded match returns a screenful of
# prose instead of the count.
PREVIEW = """()=>{
  const norm=s=>(s||'').replace(/\\s+/g,' ').trim();
  const out=[];
  for(const el of document.querySelectorAll('*')){
    if(el.children.length) continue;
    const t=norm(el.textContent);
    if(t.length>60) continue;
    if(/^[\\d,.]+ of [\\d,.]+/.test(t)||/\\([~\\d,.]+ found\\)/.test(t)
       ||/^[\\d,.KM]+ (results|companies|matches)/i.test(t)) out.push(t);
  }
  return out;
}"""

# Result limits. Reported, never set. Per the user (2026-08-07) the Total box is
# left EMPTY on purpose: an empty box means Clay's real default of 50K rows per
# table, and the greyed "100" is an illustrative placeholder, not a cap that
# gets applied. Do not "helpfully" type 100 in — it would truncate every
# category to 100 companies.
LIMITS = """()=>{
  const out=[];
  for(const i of document.querySelectorAll('input')){
    const r=i.getBoundingClientRect();
    if(r.width===0||r.x>520) continue;
    out.push({y:Math.round(r.y), value:i.value,
              placeholder:i.getAttribute('placeholder')||''});
  }
  return out;
}"""

# The rightmost breadcrumb leaf is the open table's name; its chevron sits just
# to the right of the text.
CRUMB = """()=>{
  const norm=s=>(s||'').replace(/\\s+/g,' ').trim();
  let best=null;
  for(const el of document.querySelectorAll('*')){
    if(el.children.length) continue;
    const r=el.getBoundingClientRect();
    // Band spans both layouts: the breadcrumb sits at y~51 with the orange
    // credit banner shown and y~23 once it is dismissed. A 38-66 band silently
    // found nothing in the second case and the rename failed after the table
    // had already been created.
    if(r.width===0||r.y<10||r.y>70) continue;
    const t=norm(el.textContent);
    if(!t||t==='/') continue;
    // The widened band also catches the toolbar's "N/N rows" chip, which sits
    // further right than the breadcrumb and would win the rightmost test —
    // renaming would then target the chip and find no menu.
    if(/^[\\d.,KM]+\\/[\\d.,KM]+( rows)?$/.test(t)) continue;
    if(!best||r.x>best.x) best={t, x:Math.round(r.x),
                                right:Math.round(r.right),
                                y:Math.round(r.y+r.height/2)};
  }
  return best;
}"""

# The bottom-bar "+ Add" that opens the source picker in a workbook that already
# has tables. Bounded to the tab strip: several other controls are named "Add"
# (the Excluded-companies "+ Add" in the filters panel, grid row adders), and a
# role lookup picks one of those instead — it matched a hidden one and the click
# silently did nothing. Its x shifts with the tab widths, so match on the band.
BOTTOM_ADD = """()=>{
  const norm=s=>(s||'').replace(/\\s+/g,' ').trim();
  for(const b of document.querySelectorAll('button,[role="button"]')){
    const r=b.getBoundingClientRect();
    if(r.width===0||r.y<925||r.y>985) continue;
    if(norm(b.textContent)!=='Add') continue;
    return {x:Math.round(r.x+r.width/2), y:Math.round(r.y+r.height/2)};
  }
  return null;
}"""

# Every workbook's results table gets this name, matching the Labs Product &
# Services folder — the workbook carries the category, the table does not.
TABLE_NAME = "Companies"

# The Filters panel's Query mode toggle — the `</>` button beside the funnel.
QUERY_MODE_TOGGLE = (122, 232)

# Where to click to focus the Ace editor. Derived from the editor's own rect
# rather than hardcoded: the panel's vertical layout differs between Find
# companies and Find people (whose Filters section sits below a Summary block),
# and a fixed point that misses the editor makes Ctrl+A select nothing — the
# paste then silently goes nowhere and leaves Clay's prefill in place.
ACE_BOX = """()=>{
  const el=document.querySelector('.ace_editor');
  if(!el) return null;
  const r=el.getBoundingClientRect();
  if(r.width===0||r.height===0) return null;
  return {x:Math.round(r.x+r.width/2), y:Math.round(r.y+Math.min(30, r.height/2))};
}"""

# Fallback only, for a panel whose editor rect cannot be read.
EDITOR_BODY = (277, 300)


def say(msg):
    print(msg, flush=True)


def shot(page, shots_dir, name):
    os.makedirs(shots_dir, exist_ok=True)
    path = os.path.join(shots_dir, f"{name}.png")
    page.screenshot(path=path)
    say(f"  shot: {path}")
    return path


# --------------------------------------------------------------------------
# workbook creation / lookup
# --------------------------------------------------------------------------

def ps_folder_id(icp=None):
    """The Products & Services folder id for an ICP.

    Keyed on the ICP so `--icp highways` cannot land in the EVCharge folder:
    `highways` reads `highways_ps_folder_id` / $CLAY_HIGHWAYS_PS_FOLDER_ID.
    `evcharge` keeps its original key names byte-for-byte — they are live
    config, and renaming persisted settings is exactly what CLAUDE.md forbids.
    """
    icp = icp or psq.DEFAULT_ICP
    return browser_session.local_setting(f"{icp}_ps_folder_id",
                                         f"CLAY_{icp.upper()}_PS_FOLDER_ID")


def find_or_create_workbook(page, name, shots_dir, icp=None):
    """(workbook_id, "existing"|"created") for the category's workbook.

    Fails closed: if the folder listing will not hydrate, scope.scan raises
    rather than reporting an empty folder — reporting absence here would create
    a duplicate workbook.
    """
    scope.open_folder(page, ps_folder_id(icp))
    wb, _ = scope.scan(page)
    for wid, wname in wb.items():
        if wname == name:
            say(f"  workbook already exists: {wid}")
            return wid, "existing"

    say(f"  creating workbook for {name!r}")
    page.get_by_test_id("create-new").click()
    page.wait_for_timeout(2500)
    page.get_by_role("menuitem", name="Workbook").click()
    page.wait_for_timeout(12000)
    if "/workbooks/" not in page.url:
        shot(page, shots_dir, "create_failed")
        raise VerificationError(f"no workbook opened after + New > Workbook: {page.url}")
    wid = page.url.split("/workbooks/")[1].split("/")[0].split("?")[0]
    say(f"  created {wid}")
    rename_workbook(page, name)
    return wid, "created"


def rename_workbook(page, name):
    """Rename the open workbook via the breadcrumb chevron menu.

    The breadcrumb name and the right-panel name render as two identically
    named buttons, so a role-based click is ambiguous and hangs; the chevron is
    addressed positionally and the menu item by text.
    """
    crumb = page.evaluate(FIND, "Untitled workbook")
    if not crumb:
        cur = page.evaluate(FIND, name)
        if cur:
            say("  already named correctly")
            return "already"
        raise VerificationError("workbook breadcrumb not found")
    page.mouse.click(crumb["x"] + 70, crumb["y"])
    page.wait_for_timeout(2500)
    item = page.evaluate(FIND, "Rename workbook")
    if not item:
        page.keyboard.press("Escape")
        raise VerificationError("'Rename workbook' not in the breadcrumb menu")
    page.mouse.click(item["x"], item["y"])
    page.wait_for_timeout(2000)
    page.keyboard.press("Control+A")
    page.wait_for_timeout(300)
    page.keyboard.type(name, delay=25)
    page.wait_for_timeout(600)
    page.keyboard.press("Enter")
    page.wait_for_timeout(5000)
    got = page.evaluate(FIND, name)
    say(f"  renamed workbook -> {got['t'] if got else None!r}")
    return "ok" if got else "failed"


def commit_table(page, shots_dir, slug):
    """Click through the "Enrich companies" modal with nothing selected.

    The modal is the last step of a saved search. Its button is "Create table"
    (older variants say "Save"); every enrichment checkbox above it is left
    untouched, so the committed table holds the search results and no paid
    columns.
    """
    for label in ("Create table", "Save"):
        btn = page.evaluate(FIND, label)
        if not btn or btn["y"] < 850:
            continue
        say(f"  clicking {label!r} with no enrichments selected: {btn}")
        page.mouse.click(btn["x"], btn["y"])
        page.wait_for_timeout(30000)
        return label
    shot(page, shots_dir, f"{slug}_no_commit_button")
    raise VerificationError(
        "no 'Create table'/'Save' button on the enrichment step — the table may "
        "be uncommitted; check Clay before re-running")


def rename_table(page, new_name=TABLE_NAME, current=None):
    """Rename a table via its TAB menu in the bottom bar.

    Uses clay_ui.open_tab_menu, not the breadcrumb. The breadcrumb version of
    this caused real damage: its "rightmost leaf in a y-band" heuristic picked
    up whatever else happened to render up there — the toolbar's "N/N rows"
    chip, an Auto-run "0", the "Tools" button — and on one workbook it selected
    the WORKBOOK breadcrumb and renamed the workbook to "People" instead of the
    table. The tab menu is unambiguous: clay_ui locates the tab by exact text in
    the bottom band with aria-haspopup=menu, and verifies the menu really opened.

    `current` names the tab to rename; without it, the sole tab that is neither
    `Companies` nor already `new_name` is used.
    """
    if current is None:
        tabs = [t for t in existing_tables(page)
                if t not in ("Companies", new_name, "Stop", "History")
                and t.strip() not in ("", "…")]
        if not tabs:
            say(f"  no tab to rename (already {new_name!r}?)")
            return "already"
        current = tabs[0]
    say(f"  renaming tab {current!r} -> {new_name!r}")

    clay_ui.open_tab_menu(page, current)
    page.wait_for_timeout(1200)
    item = page.evaluate(FIND, "Rename")
    if not item:
        page.keyboard.press("Escape")
        raise VerificationError(
            f"'Rename' not in the tab menu for {current!r} "
            f"(menu items: {clay_ui.visible_menuitems(page)[:8]})")
    page.mouse.click(item["x"], item["y"])
    page.wait_for_timeout(1800)
    page.keyboard.press("Control+A")
    page.wait_for_timeout(300)
    page.keyboard.type(new_name, delay=45)
    page.wait_for_timeout(600)
    page.keyboard.press("Enter")
    page.wait_for_timeout(5000)

    tabs = existing_tables(page)
    ok = new_name in tabs
    say(f"  tabs now: {tabs}")
    return "ok" if ok else "failed"


def real_tables(workbook_id):
    """The workbook's ACTUAL tables, from the Clay CLI.

    `existing_tables` reads the bottom tab strip, which also contains UI chrome
    — "Stop", "History" and a non-ASCII overflow button. Filtering that by name
    kept failing (the overflow glyph is not the character it renders as), and a
    chrome entry mistaken for a table made the people pass try to RENAME a
    button instead of building the table. The CLI returns tables and nothing
    else, so use it for any decision about what exists.
    """
    import apply_ps_supabase as _aps        # local: avoids a circular import
    rows = [r for r in _aps._wsl("list_workbook_tables.py", workbook_id).splitlines()
            if "|" in r]
    return [r.split("|", 1)[1] for r in rows]


def existing_tables(page):
    """Table tabs in the open workbook. Fail closed on an unreadable bottom bar:
    a table that exists but reads as absent would let the search run twice."""
    try:
        tabs = clay_ui.list_table_tabs(page)
    except Exception as e:
        say(f"  !! table pre-check failed ({str(e)[:60]}) — treating as present")
        return ["<unknown>"]
    return tabs


# --------------------------------------------------------------------------
# the Find companies panel
# --------------------------------------------------------------------------

def open_find_companies(page, shots_dir):
    """Source picker -> Find companies -> "Build with filters instead".

    An EMPTY workbook shows the source picker on open, so `Find companies` is
    already on screen. A workbook that already has a table opens into the grid
    instead and has no such button — there the picker is reached through the
    bottom-bar `Add`. That second path is what `--measure` uses to re-price a
    query against an already-built workbook.
    """
    src = page.get_by_role("button", name="Find companies", exact=True)
    if src.count() == 0:
        say("  no source picker (workbook has tables) — opening it via Add")
        add = page.evaluate(BOTTOM_ADD)
        if not add:
            shot(page, shots_dir, "no_add_button")
            raise VerificationError(
                "neither a 'Find companies' source button nor a bottom-bar "
                "'Add' — cannot reach the source picker")
        page.mouse.click(add["x"], add["y"])
        page.wait_for_timeout(5000)
        src = page.get_by_role("button", name="Find companies", exact=True)
        if src.count() == 0:
            shot(page, shots_dir, "no_find_companies_after_add")
            raise VerificationError("'Find companies' not in the Add panel")
    src.first.click()
    page.wait_for_timeout(9000)
    btn = page.evaluate(FIND, "Build with filters instead")
    if not btn:
        # Clay sometimes opens straight into the filters panel.
        if page.evaluate(FIND, "Add filter"):
            say("  filters panel already open")
            return
        shot(page, shots_dir, "no_build_with_filters")
        raise VerificationError("'Build with filters instead' not offered")
    page.mouse.click(btn["x"], btn["y"])
    page.wait_for_timeout(9000)
    say("  Build with filters open")


def enter_query_mode(page):
    """Filters panel -> the `</>` toggle. Verified by the Ace editor appearing.

    The toggle sits on the same row as "Add filter", so it is located from that
    anchor rather than a fixed y. QUERY_MODE_TOGGLE's hardcoded (122, 232) broke
    the moment the orange credit banner was dismissed and shifted the whole page
    up ~28px — the same failure that hit every other panel coordinate in this
    repo. Fall back to the constant only if the anchor cannot be found.
    """
    for attempt in range(4):
        anchor = page.evaluate(FIND, "Add filter")
        y = anchor["y"] if anchor else QUERY_MODE_TOGGLE[1]
        page.mouse.click(QUERY_MODE_TOGGLE[0], y)
        page.wait_for_timeout(3000)
        if page.evaluate(ACE_GET) is not None:
            say(f"  query mode on (toggle y={y})")
            return True
        say(f"  query mode did not open (attempt {attempt + 1}, y={y})")
        page.wait_for_timeout(1500)
    raise VerificationError("could not switch the Filters panel to query mode")


def set_query(page, query):
    """Replace the editor's contents with the category's query, verbatim."""
    pre = page.evaluate(ACE_GET)
    say(f"  editor prefilled with {len(pre or '')} chars")
    spot = page.evaluate(ACE_BOX)
    say(f"  editor at {spot or EDITOR_BODY}")
    page.mouse.click(*(( spot["x"], spot["y"]) if spot else EDITOR_BODY))
    page.wait_for_timeout(800)
    page.keyboard.press("Control+A")
    page.wait_for_timeout(400)
    page.keyboard.press("Delete")
    page.wait_for_timeout(600)
    page.keyboard.insert_text(query)
    page.wait_for_timeout(6000)
    got = page.evaluate(ACE_GET) or ""
    same = " ".join(got.split()) == " ".join(query.split())
    say(f"  editor now {len(got)} chars; exact match: {same}")
    if not same:
        say(f"  !! editor content differs; starts {got[:100]!r}")
    return same


def read_preview(page, waits=6):
    """Poll for the panel's match-count line — it lands well after the paste."""
    seen = []
    for _ in range(waits):
        seen = page.evaluate(PREVIEW)
        if seen:
            break
        page.wait_for_timeout(4000)
    return seen


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def build(page, name, query, args):
    shots_dir = args.shots_dir
    wid, how = find_or_create_workbook(page, name, shots_dir,
                                       getattr(args, 'icp', None))
    if how == "existing":
        clay_ui.open_workbook_by_id(page, wid)
        page.wait_for_timeout(8000)
        tables = existing_tables(page)
        # --measure re-prices an edited query against a workbook that is already
        # built. It configures and reads the preview count, then stops; main()
        # refuses to combine it with --run, so it can never reach the code that
        # spends. The re-entry guard is skipped BECAUSE the table exists —
        # that is the case being measured.
        if tables and args.measure:
            say(f"  measuring against existing table(s) {tables}")
        elif tables and args.replace and TABLE_NAME in tables:
            # Rebuild after a query edit. Delete BEFORE building, not after:
            # two tables named `Companies` in one workbook are indistinguishable
            # to the tab menu, so a delete afterwards could remove the new one.
            # The rows are recoverable from Clay's Trash if this proves wrong.
            say(f"  --replace: deleting the existing {TABLE_NAME!r} table")
            if not clay_ui.delete_table(page, TABLE_NAME):
                raise VerificationError(
                    f"{TABLE_NAME!r} reported present but delete_table found no "
                    f"tab — refusing to build a second one")
            page.wait_for_timeout(5000)
            left = existing_tables(page)
            if TABLE_NAME in left:
                raise VerificationError(
                    f"{TABLE_NAME!r} still present after delete (tabs: {left})")
            say(f"  deleted; tabs now {left}")
        elif tables and not args.force:
            # Clay names a saved search's table after the query ("EV Payments,
            # Fintech, Global"), so an interrupted run leaves a real, populated
            # table under the wrong name. Finish that rather than skipping it —
            # the rows are already paid for.
            if TABLE_NAME not in tables:
                say(f"  table present but not {TABLE_NAME!r}: {tables}")
                renamed = rename_table(page)
                tables = existing_tables(page)
                return {"workbook_id": wid, "status": "built",
                        "renamed": renamed, "tables": tables}
            say(f"SKIP {name}: workbook already has {TABLE_NAME!r}")
            return {"workbook_id": wid, "status": "built", "tables": tables}

    open_find_companies(page, shots_dir)
    enter_query_mode(page)
    exact = set_query(page, query)
    preview = read_preview(page)
    limits = page.evaluate(LIMITS)
    say(f"  preview: {preview}")
    say(f"  result limits: {limits}")
    shot(page, shots_dir, f"{args.slug}_query_pasted")

    if not exact:
        raise VerificationError("query did not paste verbatim — refusing to continue")

    if args.measure:
        say(f"MEASURED {name}: {preview}")
        # Keep the recorded status — measuring must not demote a built workbook
        # to `configured`, which would make a later --run try to build it again.
        return {"workbook_id": wid, "preview": preview}

    if not args.run:
        say(f"CONFIGURED (not run) {name}: workbook {wid}")
        return {"workbook_id": wid, "status": "configured",
                "preview": preview, "limits": limits}

    cont = page.evaluate(FIND, "Continue")
    if not cont:
        shot(page, shots_dir, f"{args.slug}_no_continue")
        raise VerificationError("'Continue' not found")
    say(f"  clicking Continue {cont}")
    page.mouse.click(cont["x"], cont["y"])
    page.wait_for_timeout(9000)
    shot(page, shots_dir, f"{args.slug}_after_continue")

    newt = page.evaluate(FIND, "Save to new table")
    if newt:
        say(f"  clicking Save to new table {newt}")
        page.mouse.click(newt["x"], newt["y"])
        page.wait_for_timeout(25000)
        shot(page, shots_dir, f"{args.slug}_saved")

    # Clay's last step is an "Enrich companies" modal: a checklist of paid
    # per-row enrichments (Enrich Company, Research ICP with AI, News, Latest
    # Funding, Job Openings, Competitors) over a "Create table" button. The ask
    # is the search only, so EVERY box stays unchecked and we click straight
    # through — ticking one here would silently add a paid column to the whole
    # table. Nothing in this branch may click a checkbox.
    create = commit_table(page, shots_dir, args.slug)
    say(f"  commit: {create}")
    shot(page, shots_dir, f"{args.slug}_table")
    say(f"  url: {page.url}")

    # Reload the workbook before renaming. Straight after "Create table" the
    # page is still the save flow's own view: the breadcrumb is present but its
    # chevron does not open the TABLE menu, so the rename reliably fails with
    # "'Rename' not in the table menu" — on a fresh load of the same workbook it
    # works every time. Costs one navigation and turns a guaranteed failure into
    # a guaranteed success.
    clay_ui.open_workbook_by_id(page, wid)
    page.wait_for_timeout(9000)
    renamed = rename_table(page)
    tables = existing_tables(page)
    say(f"  tables now: {tables}")
    if TABLE_NAME not in tables:
        raise VerificationError(
            f"no {TABLE_NAME!r} table after the run (tabs: {tables}) — the "
            f"search may not have saved; do NOT re-run without checking Clay")
    return {"workbook_id": wid, "status": "built", "preview": preview,
            "renamed": renamed, "tables": tables}


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    psq.add_cli_args(ap)
    ap.add_argument("category", nargs="?", help="Top level name from the config")
    ap.add_argument("--list", action="store_true",
                    help="category names only, no browser")
    ap.add_argument("--print-query", action="store_true",
                    help="print the rendered query and exit, no browser")
    ap.add_argument("--run", action="store_true",
                    help="materialise the results into a table (SPENDS CREDITS). "
                         "Without it the search is configured and left untriggered.")
    ap.add_argument("--replace", action="store_true",
                    help="rebuild after a query edit: DELETE the workbook's "
                         "existing Companies table, then build a new one. "
                         "Requires --run. Destructive; rows go to Clay's Trash.")
    ap.add_argument("--measure", action="store_true",
                    help="re-price an edited query against an already-built "
                         "workbook: configure, read the preview count, stop. "
                         "Cannot be combined with --run.")
    ap.add_argument("--force", action="store_true",
                    help="proceed even when the workbook already has a table")
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--shots-dir", default=SHOTS_DIR)
    args = ap.parse_args()

    if args.replace and not args.run:
        ap.error("--replace requires --run: deleting the old table without "
                 "building the new one would leave the workbook empty")
    if args.measure and args.run:
        ap.error("--measure and --run are mutually exclusive: --measure exists "
                 "to re-price a query against an already-built workbook without "
                 "spending, and it skips the guard that stops a second build")

    cats = dict(psq.categories(args.icp))
    if args.list:
        for i, name in enumerate(cats, 1):
            print(f"  {i:2d}. {name}")
        return 0
    if not args.category:
        ap.error("a category name is required (see --list)")
    if args.category not in cats:
        print(f"unknown category {args.category!r}; see --list")
        return 1

    query = cats[args.category]
    if args.print_query:
        print(query)
        return 0

    args.slug = "".join(c if c.isalnum() else "_" for c in args.category)[:60]
    say(f"{args.category}: query is {len(query)} chars, "
        f"{'RUN' if args.run else 'configure-only'}")

    with browser_session.clay_page(headless=not args.headed) as page:
        result = build(page, args.category, query, args)
    say(f"result: {result}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
