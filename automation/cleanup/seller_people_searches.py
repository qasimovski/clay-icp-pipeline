"""Automate the 3 "Find people at these companies" builds per event, saving
into a per-event "Sellers - People" table.

Reuses the proven filter-fill logic from the Chrome extension (vendored here as
people_search_fill.js) by injecting its functions into the page, and adds the
pieces the extension never covered: the Experience filter,
Limit-per-company = 50, opening the Find People source view, and the
Continue -> Save (new / existing) table flow.

Per event: build1 -> Save to NEW table (rename "Sellers - People");
builds 2 & 3 -> Save to EXISTING "Sellers - People" (Import and run).
"""

import os
import re
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
AUTO_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, SCRIPT_DIR)
sys.path.insert(0, os.path.join(AUTO_DIR, "clay_sync"))
sys.path.insert(0, os.path.join(AUTO_DIR, "build_automation"))

import clay_ui        # noqa: E402
import browser_session         # noqa: E402
import column_config as colcfg  # noqa: E402
import pipeline_config as pcfg  # noqa: E402

# Entity/ICP-driven; set by configure() (defaults applied on import). TABLE is the
# source table the Find-People search runs over; PEOPLE_TABLE is the per-event
# output; SELLER_COUNTRIES is the Location list; BUILDS are the sequential searches.
TABLE = None
PEOPLE_TABLE = None
SELLER_COUNTRIES = None
# Vendored copy of the Chrome extension's filter-fill logic (location_filler/
# extension/content.js). Kept in-repo so this pipeline is self-contained;
# override with CLAY_PEOPLE_FILL_JS to point at a different source.
CONTENT_JS = os.environ.get(
    "CLAY_PEOPLE_FILL_JS", os.path.join(SCRIPT_DIR, "people_search_fill.js"))

# ---- build specs — loaded from config/icps/<icp>/people_search.yaml ---------
# Each build: {name, jobTitleMode ('exact'|'contains'), jobTitles[], experience?}.
BUILDS = []


def configure(entity=None, icp=None):
    """Point the seller build at a given entity type + ICP (config-driven)."""
    global TABLE, PEOPLE_TABLE, SELLER_COUNTRIES, BUILDS
    cfg = pcfg.load(entity, icp)
    TABLE = cfg.main_table
    PEOPLE_TABLE = cfg.seller_people_table
    SELLER_COUNTRIES = cfg.seller_countries
    BUILDS = [{"name": b["name"], "jobTitleMode": b["job_title_mode"],
               "jobTitles": b["job_titles"],
               **({"experience": b["experience"]} if b.get("experience") else {})}
              for b in cfg.seller_searches]
    return cfg


configure()


def build_bundle():
    """Turn content.js into an injectable bundle: strip the double-injection
    guard and the chrome.runtime listener, expose the fill functions on
    window.__ext, and append fillExperience."""
    txt = open(CONTENT_JS, encoding="utf-8").read()
    start = txt.index("const FIELD_LABEL")
    end = txt.index("chrome.runtime.onMessage.addListener")
    body = txt[start:end]
    extra = r"""
async function fillExperience(csv){
  await openSectionOnce("Experience");
  await sleep(400);
  let input=null;
  const labelEl = findLeafByText(t=>/experience description keywords/i.test(t));
  if(labelEl){ input = inputNear(labelEl,5).input; }
  if(!input){
    input = Array.from(document.querySelectorAll('input,[role="combobox"],[contenteditable="true"]'))
      .filter(isVisible).find(e=>/product roadmap|growth team|manager/i.test(e.getAttribute('placeholder')||''));
  }
  if(!input) throw new Error("Experience keywords input not found");
  input.focus(); await sleep(150);
  setNativeValue(input, csv);
  input.dispatchEvent(new InputEvent('input',{bubbles:true,data:csv,inputType:'insertFromPaste'}));
  await sleep(400);
  dispatchKey(input,'Enter',13);
  await sleep(500);
  return { cleared: getFieldValue(input).trim()==='' };
}
// the "Limit per company" number input (the input just below that label)
function _limitPerCompanyInput(){
  const lab = findLeafByText(t=>/^limit per company$/i.test(t));
  if(!lab) return null;
  const ly=lab.getBoundingClientRect().bottom;
  let best=null,btop=1e9;
  for(const el of document.querySelectorAll('input')){
    const r=el.getBoundingClientRect(); if(r.width===0)continue;
    if(r.top>=ly-4 && r.top<ly+90 && r.top<btop){ best=el; btop=r.top; }
  }
  return best;
}
async function setLimitPerCompany(n){
  const input=_limitPerCompanyInput();
  if(!input) throw new Error("Limit per company input not found");
  input.scrollIntoView({block:'center'}); await sleep(300);
  input.focus();
  setNativeValue(input,''); input.dispatchEvent(new InputEvent('input',{bubbles:true}));
  await sleep(80);
  setNativeValue(input, String(n));
  input.dispatchEvent(new InputEvent('input',{bubbles:true,data:String(n),inputType:'insertText'}));
  input.dispatchEvent(new Event('change',{bubbles:true}));
  await sleep(200); dispatchKey(input,'Enter',13); await sleep(150); input.blur();
  await sleep(200);
  return getFieldValue(input);
}
window.__ext = { fillSeniority, fillJobTitles, fillCountries, fillExperience,
                 LOCATION_VALUES, setLimitPerCompany, findSectionHeader,
                 openSectionOnce };
"""
    return body + extra


_BUNDLE = None


def inject(page):
    global _BUNDLE
    if _BUNDLE is None:
        _BUNDLE = build_bundle()
    page.evaluate(_BUNDLE)


# ---- open the Find People source view -------------------------------------

def open_find_people(page, wid, say):
    clay_ui.open_workbook_by_id(page, wid)
    colcfg.focus_table_maybe_empty(page, TABLE)   # people search uses this table's view
    page.wait_for_timeout(1200)
    # Ctrl+E toggles the Tools panel; it may already be open, so press until the
    # panel's search box appears.
    box = None
    for _ in range(4):
        page.keyboard.press("Control+e"); page.wait_for_timeout(2200)
        boxes = [t for t in page.get_by_placeholder("Search").all()
                 if t.is_visible() and t.bounding_box() and t.bounding_box()["x"] > 1240]
        if boxes:
            box = boxes[0]; break
    if not box:
        raise clay_ui.ClayUIError("Tools search box not found after Ctrl+E")
    box.fill("Find people at these companies"); page.wait_for_timeout(3000)
    find = """()=>{for(const el of document.querySelectorAll('button,[role="option"],[role="menuitem"],[role="button"],div')){
        const r=el.getBoundingClientRect(); if(r.width===0||r.x<1240)continue;const t=(el.textContent||'').trim();
        if(t.startsWith('Find people at these companies')&&t.includes('Source')&&t.length<50)
          return {x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2)};}return null;}"""
    pt = None
    for _ in range(10):
        pt = page.evaluate(find)
        if pt:
            break
        page.wait_for_timeout(800)
    if not pt:
        raise clay_ui.ClayUIError("'Find people at these companies' (Source) not found")
    page.mouse.click(pt["x"], pt["y"])
    # wait for the Find-with-filters view
    for _ in range(20):
        if page.get_by_text("Find with filters", exact=False).count():
            break
        page.wait_for_timeout(700)
    page.wait_for_timeout(2500)
    if not page.get_by_text("Find with filters", exact=False).count():
        raise clay_ui.ClayUIError("Find with filters view did not open")
    say("  Find People view opened")


# ---- fill one build's filters ---------------------------------------------

def fill_filters(page, spec, say):
    inject(page)
    sen = page.evaluate("() => window.__ext.fillSeniority()")
    say(f"  seniority: +{len(sen.get('added',[]))} notFound={sen.get('notFound')}")
    jt = page.evaluate("(a) => window.__ext.fillJobTitles(a.t, a.m)",
                       {"t": spec["jobTitles"], "m": spec["jobTitleMode"]})
    say(f"  jobtitle({spec['jobTitleMode']}): +{len(jt.get('added',[]))} "
        f"notFound={jt.get('notFound')} mode={jt.get('modeNote')}")
    if spec.get("experience"):
        ex = page.evaluate("(k) => window.__ext.fillExperience(k)", spec["experience"])
        say(f"  experience: cleared={ex.get('cleared')}")
    loc = page.evaluate("(c) => window.__ext.fillCountries(c, false)", SELLER_COUNTRIES)
    say(f"  location: +{len(loc.get('added',[]))} notFound={loc.get('notFound')}")
    # limit per company = 50 (set + verify in JS, scrolls into view first).
    # Retry a few times — the Limit section occasionally isn't ready on first try.
    val = ""
    for _ in range(4):
        val = page.evaluate("() => window.__ext.setLimitPerCompany(50)")
        if str(val).strip() == "50":
            break
        page.wait_for_timeout(1000)
    if str(val).strip() != "50":
        raise clay_ui.ClayUIError(f"Limit per company not set (got {val!r})")
    say(f"  limit per company = {val}")
    return {"seniority": sen, "jobtitle": jt, "location": loc, "limit": val}


# ---- results + Continue menu ----------------------------------------------

def wait_results(page, timeout_s=70):
    """Return '0' (no results), a count string, or None (timed out)."""
    for _ in range(timeout_s):
        n = page.evaluate(
            "()=>{const t=document.body.innerText;"
            "const m=t.match(/Showing\\s+([\\d,]+)\\s+result/);"
            "if(m)return m[1]; if(t.includes('No results found'))return '0'; return null;}")
        if n is not None:
            return n
        page.wait_for_timeout(1000)
    return None


def _menu_open(page):
    return bool(page.get_by_text("Save to new table", exact=True).count())


def _open_continue_menu(page):
    cont = page.get_by_role("button", name="Continue").first
    r = cont.bounding_box()
    for _ in range(6):
        if _menu_open(page):
            return True
        page.mouse.click(r["x"] + r["width"] - 12, r["y"] + r["height"] / 2)
        page.wait_for_timeout(1500)
    return _menu_open(page)


def _click_exact(page, txt, timeout_s=8):
    """Click a visible element whose trimmed text == txt. Prefer real controls
    (button / menuitem / option) over plain text nodes so we hit e.g. the
    'Select table' confirm button, not the dialog's 'Select table' title."""
    for _ in range(timeout_s):
        pt = page.evaluate("""(txt)=>{
            const prefer='button,[role="menuitem"],[role="option"],[role="button"]';
            const pick=(sel)=>{for(const el of document.querySelectorAll(sel)){
              const r=el.getBoundingClientRect(); if(r.width===0||r.height===0)continue;
              if((el.textContent||'').trim()===txt) return {x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2)};}
              return null;};
            return pick(prefer) || pick('div,span,td,li,p');}""", txt)
        if pt:
            page.mouse.click(pt["x"], pt["y"])
            return True
        page.wait_for_timeout(700)
    return False


def save_new_and_rename(page, wid, say, screenshot=None):
    """build1: Continue -> Save to new table (auto-creates + runs), then rename
    the newly-created people table to PEOPLE_TABLE. Returns False if the Continue
    menu never enabled (i.e. the build produced no results)."""
    if not _open_continue_menu(page):
        say("  (no results — Continue disabled; skipping save-new)")
        return False
    if not _click_exact(page, "Save to new table"):
        raise clay_ui.ClayUIError("'Save to new table' not clickable")
    page.wait_for_timeout(6000)   # table is created + run kicked off server-side
    if screenshot:
        screenshot("save_new_done")
    return _rename_new_people_table(page, wid, say)


def _rename_new_people_table(page, wid, say):
    clay_ui.open_workbook_by_id(page, wid)
    page.wait_for_timeout(3000)
    page.keyboard.press("Escape"); page.wait_for_timeout(500)
    # the new table is the bottom tab that is not a known table. Protect BOTH
    # normalized sources (in Sponsors mode TABLE is Sponsors_normalized, so
    # Exhibitors_normalized must be listed explicitly or it could be grabbed).
    known = {TABLE, "Sponsors_normalized", "Exhibitors_normalized", PEOPLE_TABLE}
    info = page.evaluate("""(known)=>{for(const el of document.querySelectorAll('button,[role="tab"]')){
        const r=el.getBoundingClientRect(); if(r.width===0||r.y<914||r.y>944||r.x<135||r.x>1150)continue;
        const t=(el.textContent||'').trim();
        if(t && t!=='Overview' && t!=='Add' && !known.includes(t) && !t.endsWith(' - People') && t.length<60)
          return {x:Math.round(r.x+18),y:Math.round(r.y+r.height/2),t:t};}return null;}""", list(known))
    if not info:
        raise clay_ui.ClayUIError("new people table tab not found for rename")
    say(f"  new table tab: {info['t']!r}")
    page.mouse.click(info["x"], info["y"]); page.wait_for_timeout(2200)
    # open its top-breadcrumb menu -> Rename. Pick the RIGHTMOST breadcrumb
    # segment (the active table name) by position, not text — auto-names can be
    # long/truncated and won't match the tab text exactly. Retry: the menu
    # occasionally doesn't render on the first chevron click.
    _bc = """()=>{let best=null;
        for(const el of document.querySelectorAll('button,[role="button"],[aria-haspopup]')){
          const r=el.getBoundingClientRect(); if(r.width===0||r.y<30||r.y>72||r.x>1000)continue;
          const t=(el.textContent||'').trim();
          if(!t||t==='Competitive Events'||t.length<3)continue;
          if(!best||r.x>best.rx) best={x:Math.round(r.x+r.width-10),y:Math.round(r.y+r.height/2),rx:r.x};}
        return best;}"""
    renamed = False
    for _ in range(4):
        bc = page.evaluate(_bc)
        if not bc:
            page.wait_for_timeout(1000); continue
        page.mouse.click(bc["x"], bc["y"]); page.wait_for_timeout(1500)
        if _click_exact(page, "Rename", timeout_s=4):
            page.wait_for_timeout(900)
            page.keyboard.press("Control+a"); page.keyboard.type(PEOPLE_TABLE, delay=40)
            page.keyboard.press("Enter"); page.wait_for_timeout(1500)
            renamed = True; break
        page.keyboard.press("Escape"); page.wait_for_timeout(800)
    if not renamed:
        raise clay_ui.ClayUIError("Rename option not found")
    say(f"  renamed -> {PEOPLE_TABLE!r}")
    return True


def save_existing(page, say, screenshot=None):
    """builds 2 & 3: Continue -> Save to existing table -> select PEOPLE_TABLE
    -> Select table -> Import and run. Returns False if no results."""
    if not _open_continue_menu(page):
        say("  (no results — Continue disabled; skipping save-existing)")
        return False
    if not _click_exact(page, "Save to existing table"):
        raise clay_ui.ClayUIError("'Save to existing table' not clickable")
    page.wait_for_timeout(2500)
    if screenshot:
        screenshot("existing_select")
    if not _click_exact(page, PEOPLE_TABLE, timeout_s=10):
        raise clay_ui.ClayUIError(f"could not select {PEOPLE_TABLE!r} in table picker")
    page.wait_for_timeout(1200)
    # confirm the "Select table" dialog
    if not _click_exact(page, "Select table", timeout_s=8):
        raise clay_ui.ClayUIError("'Select table' confirm button not found")
    page.wait_for_timeout(2500)
    if screenshot:
        screenshot("existing_importrun")
    if not _click_exact(page, "Import and run", timeout_s=12):
        raise clay_ui.ClayUIError("'Import and run' not found")
    page.wait_for_timeout(4000)
    say("  saved to existing + import and run")
    return True


# ---- per-event orchestrator -----------------------------------------------

# Real table tabs sit in the bottom bar at x>135 (left of that is nav chrome
# like Overview/Resources; "Add" is the new-table button).
def _bottom_tabs(page):
    return page.evaluate("""()=>{const s=new Set();
        for(const el of document.querySelectorAll('button,[role="tab"]')){
          const r=el.getBoundingClientRect(); if(r.width===0||r.y<914||r.y>944||r.x<135||r.x>1150)continue;
          const t=(el.textContent||'').trim(); if(t && t!=='Add' && t!=='Overview')s.add(t);}return [...s];}""")


def _prepare_people_table(page, wid, say):
    """Return True if a 'Sellers - People' table is ready to append to. Handles
    resume: if it already exists -> True; if a prior run left an unrenamed
    people table (auto-named artifact) -> rename it and return True; otherwise
    (clean workbook) -> False so build 1 creates it."""
    clay_ui.open_workbook_by_id(page, wid)
    page.wait_for_timeout(2500)
    tabs = _bottom_tabs(page)
    if PEOPLE_TABLE in tabs:
        return True
    known = {TABLE, "Sponsors_normalized", "Exhibitors_normalized", "Overview", "Add", ""}
    # never salvage a finished people table (e.g. 'Sellers - People' /
    # 'Buyers - People') — only auto-named interrupted artifacts.
    stray = [t for t in tabs if t not in known and not t.endswith(" - People")]
    if stray:
        say(f"  salvaging unrenamed people table {stray[0]!r} -> {PEOPLE_TABLE!r}")
        _rename_new_people_table(page, wid, say)
        return True
    return False


def run_searches(page, wid, name, say):
    """Run all 3 builds for one event into a single 'Sellers - People' table.
    The first build that yields results creates the table (Save to new +
    rename); later builds append (Save to existing). Builds with no results
    are skipped. Each build re-opens the Find People view fresh (which re-focuses
    Exhibitors_normalized), matching the manual per-build navigation."""
    created = _prepare_people_table(page, wid, say)
    if created:
        say(f"  ({PEOPLE_TABLE!r} ready — builds will append via save-existing)")
    summary = []
    for i, spec in enumerate(BUILDS):
        say(f"-- {name}: build {i+1} ({spec['name']}) --")
        open_find_people(page, wid, say)
        fill_filters(page, spec, say)
        page.keyboard.press("Escape"); page.wait_for_timeout(700)
        if not created:
            ok = save_new_and_rename(page, wid, say)
            if ok:
                created = True
        else:
            ok = save_existing(page, say)
        summary.append({"build": spec["name"], "saved": bool(ok)})
    return {"workbook_id": wid, "workbook_name": name,
            "status": "ok" if created else "no_results",
            "created_table": created, "builds": summary}
