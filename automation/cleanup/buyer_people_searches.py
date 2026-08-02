"""Buyer-side people builds.

Per event Exhibitors_normalized:
  1. change the Side view filter from Seller -> Buyer (keep the
     'Send table data has results' filter intact);
  2. for each ICP segment (buyers_config.json): set the Classification filter to
     'contains any of' <segment name>; if the filtered view has 0 rows, skip the
     segment; otherwise run Find-people searches into ONE 'Buyers - People' table
     (append). Each segment yields up to two searches — one per job-title match
     mode ('is exactly' for the exact list, 'contains' for the contains list) —
     because Clay's Job Title filter is single-mode. Seniority (all 11) and the
     50-country list are the same for every search.

Reuses seller_people_searches (open Find people, fill seniority/job-titles/countries/limit,
save-new/save-existing) with PEOPLE_TABLE swapped to 'Buyers - People', and
apply_view_filters for the filter-panel primitives.
"""

import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
AUTO_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, os.path.join(AUTO_DIR, "clay_sync"))
sys.path.insert(0, os.path.join(AUTO_DIR, "build_automation"))

import clay_ui               # noqa: E402
import column_config as colcfg        # noqa: E402
import seller_people_searches   # noqa: E402
import apply_view_filters  # noqa: E402
import pipeline_config as pcfg  # noqa: E402

LIMIT_PER_COMPANY = 50

# Entity/ICP-driven config, set by configure(); defaults applied on import so
# direct use (e.g. buyer_people_rollout --only) works without an explicit call.
TABLE = None          # source table, e.g. Exhibitors_normalized / Sponsors_normalized
COUNTRIES = None      # buyer Find-People Location list (50, no Germany)
SEGMENTS = None       # [{name, exact, contains}] per ICP Classification segment


def configure(entity=None, icp=None):
    """Point the buyer build at a given entity type + ICP (config-driven)."""
    global TABLE, COUNTRIES, SEGMENTS
    cfg = pcfg.load(entity, icp)
    TABLE = cfg.main_table
    seller_people_searches.TABLE = cfg.main_table   # shared open_find_people focuses this source table
    COUNTRIES = cfg.buyer_countries
    # config uses key 'segment'; internals below expect 'name'.
    SEGMENTS = [{"name": s["segment"], "exact": s.get("exact", []),
                 "contains": s.get("contains", [])} for s in cfg.buyer_segments]
    seller_people_searches.PEOPLE_TABLE = cfg.buyer_people_table   # save into the buyer table
    return cfg


configure()


# --------------------------------------------------------------- filter panel

_FUNNEL_JS = """()=>{
    const btns=[...document.querySelectorAll('button,[role="button"]')]
      .map(e=>{const r=e.getBoundingClientRect();return {r,cy:r.y+r.height/2,t:(e.textContent||'').trim()};})
      .filter(o=>o.r.width>0&&o.cy>92&&o.cy<112&&o.r.x<620);
    btns.sort((a,b)=>a.r.x-b.r.x);
    let idx=-1; for(let i=0;i<btns.length;i++){ if(/^\\d[\\d,]*\\s*\\/\\s*\\d/.test(btns[i].t)) idx=i; }  // LAST 'N/M' = rows count
    let f=(idx>=0&&btns[idx+1])?btns[idx+1]:btns.find(o=>/^\\d+$/.test(o.t)&&o.r.x>360);
    if(!f)return null; const r=f.r;
    return {x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2)};}"""


def _open_filter_panel(page):
    """Open the view's filter panel via the toolbar funnel (the button just to
    the right of the 'N/M rows' count). Idempotent: returns once a 'Where' row
    is visible."""
    for _ in range(6):
        if apply_view_filters._row_y(page, "Where", tries=1):
            return True
        pt = page.evaluate(_FUNNEL_JS)
        if pt:
            page.mouse.click(pt["x"], pt["y"]); page.wait_for_timeout(1500)
        else:
            page.wait_for_timeout(1000)
    return bool(apply_view_filters._row_y(page, "Where", tries=2))


def _panel_rows(page):
    """[{y, col}] for each filter row (col text may lag / be empty)."""
    return page.evaluate("""()=>[...document.querySelectorAll('[role="combobox"]')]
        .map(e=>{const r=e.getBoundingClientRect();return {y:Math.round(r.y+r.height/2),col:(e.textContent||'').trim()};})
        .filter(o=>o.y>130&&o.y<420);""")


def set_side_buyer(page, say):
    """Change the Side filter's value from Seller to Buyer (row 1)."""
    y1 = apply_view_filters._row_y(page, "Where")
    if y1 is None:
        raise clay_ui.ClayUIError("Side (Where) row not found")
    val = page.evaluate(apply_view_filters._VAL_AT, y1)
    if not val:
        # Fallback (some workbooks' Side row isn't matched by the 'equal to'
        # anchor): click the rightmost combobox on the Where/Side row = value box.
        val = page.evaluate(
            """(y)=>{let best=null;for(const e of document.querySelectorAll('[role="combobox"]')){
                const r=e.getBoundingClientRect(); if(r.width===0)continue;
                const cy=r.y+r.height/2;
                if(Math.abs(cy-y)<22 && r.x>400 && (!best||r.x>best.x))
                    best={x:Math.round(r.x+r.width/2), y:Math.round(cy)};}
                return best;}""", y1)
    if not val:
        # Last resort (e.g. an extra empty filter row shifts Side down): click the
        # current Side value chip anywhere in the panel. Match 'Seller' (first run)
        # or 'Buyer' (resume, already set) — re-typing 'Buyer' is idempotent.
        val = page.evaluate(
            """()=>{for(const e of document.querySelectorAll('button,[role="button"],span,[role="combobox"]')){
                const r=e.getBoundingClientRect(); if(r.width===0)continue;
                const cy=r.y+r.height/2; if(cy<130||cy>420)continue;
                const t=(e.textContent||'').trim();
                if(t==='Seller'||t==='Buyer')
                    return {x:Math.round(r.x+r.width/2), y:Math.round(cy)};}
                return null;}""")
    if not val:
        raise clay_ui.ClayUIError("Side value box not found")
    page.mouse.click(val["x"], val["y"]); page.wait_for_timeout(500)
    page.keyboard.press("Control+a"); page.keyboard.press("Delete"); page.wait_for_timeout(300)
    page.keyboard.type("Buyer", delay=45); page.wait_for_timeout(700)
    page.keyboard.press("Enter"); page.wait_for_timeout(1500)
    ok = page.evaluate("""()=>[...document.querySelectorAll('button,[role="button"]')].some(e=>{
        const r=e.getBoundingClientRect(); return r.width>0&&r.y>125&&r.y<190&&(e.textContent||'').trim()==='Buyer';})""")
    say(f"  Side -> Buyer ({'ok' if ok else 'UNVERIFIED'})")


def _classification_row_y(page):
    rows = _panel_rows(page)
    for r in rows:
        if "Classification" in r["col"]:
            return r["y"]
    return None


def _col_rows(page):
    """Filter-row column-selector combobox Ys (x<360), sorted top->bottom.
    Row 0 = Side (Where), row 1 = Send table data, row 2 = Classification."""
    return page.evaluate("""()=>[...document.querySelectorAll('[role="combobox"]')]
        .map(e=>{const r=e.getBoundingClientRect();return {y:Math.round(r.y+r.height/2),x:r.x};})
        .filter(o=>o.x<360&&o.y>130&&o.y<440).sort((a,b)=>a.y-b.y).map(o=>o.y);""")


def ensure_classification_filter(page, say):
    """Ensure a Classification row exists (as the 3rd filter row) with operator
    'contains any of'. Detected by POSITION, not text, so it never adds a
    duplicate row. Returns its row-Y."""
    rows = _col_rows(page)
    if len(rows) < 3:
        apply_view_filters._filter_on_column(page, "Classification"); page.wait_for_timeout(1200)
        rows = _col_rows(page)
    if len(rows) < 3:
        raise clay_ui.ClayUIError("Classification row not created")
    cy = rows[2]
    # set operator to 'contains any of' if not already
    op = page.evaluate(apply_view_filters._OP_AT, cy)
    if op:
        cur = page.evaluate("""(y)=>{for(const el of document.querySelectorAll('button,[role="button"]')){
            const r=el.getBoundingClientRect(); const cx=r.x+r.width/2, cy=r.y+r.height/2;
            if(r.width===0||cx<360||cx>720||Math.abs(cy-y)>18)continue;
            return (el.textContent||'').trim();}return '';}""", cy)
        if "contains any of" not in cur.lower():
            page.mouse.click(op["x"], op["y"]); page.wait_for_timeout(1000)
            if not seller_people_searches._click_exact(page, "contains any of", timeout_s=6):
                raise clay_ui.ClayUIError("'contains any of' operator not selectable")
            page.wait_for_timeout(1000)
    say("  Classification filter ready (contains any of)")
    return cy


# the Classification value combobox ('Enter values') is the row's combobox to
# the right of the operator (x>560); the leftmost combobox is the column picker.
# Wide y tolerance because a committed chip can grow the row height.
_CLASS_VAL_JS = """(y)=>{for(const el of document.querySelectorAll('[role="combobox"],input,[role="textbox"]')){
    const r=el.getBoundingClientRect(); const cy=r.y+r.height/2;
    if(r.width===0||Math.abs(cy-y)>32||r.x<560||r.x>1000)continue;
    return {x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2)};} return null;}"""

_CLASS_CHIPS_JS = """(y)=>{const o=[];for(const el of document.querySelectorAll('span,div')){
    const r=el.getBoundingClientRect(); const cy=r.y+r.height/2;
    if(r.width===0||Math.abs(cy-y)>16||r.x<560||r.x>840||el.children.length)continue;
    const t=(el.textContent||'').trim();
    if(t&&t!=='Enter values'&&t.length<50)o.push(t);} return [...new Set(o)];}"""


def _set_class_value_once(page, cy, value):
    vb = page.evaluate(_CLASS_VAL_JS, cy)
    click = vb or {"x": 650, "y": cy}        # fallback: click in the value area
    page.mouse.click(click["x"], click["y"]); page.wait_for_timeout(500)
    page.keyboard.press("Control+a"); page.keyboard.press("Delete"); page.wait_for_timeout(150)
    for _ in range(10):                      # remove any existing chip(s)
        page.keyboard.press("Backspace")
    page.wait_for_timeout(300)
    page.keyboard.type(value, delay=30); page.wait_for_timeout(900)
    page.keyboard.press("Enter"); page.wait_for_timeout(2500)   # let the grid re-filter


def set_classification_value(page, cy, value, say):
    """Set the Classification 'contains any of' value to exactly [value]."""
    _set_class_value_once(page, cy, value)
    say(f"  Classification = {value!r}")


_ROWCOUNT_JS = """()=>{
    const btns=[...document.querySelectorAll('button,[role="button"]')]
      .map(e=>{const r=e.getBoundingClientRect();return {cy:r.y+r.height/2,x:r.x,t:(e.textContent||'').trim()};})
      .filter(o=>o.cy>92&&o.cy<112&&o.x<620);
    btns.sort((a,b)=>a.x-b.x);
    let m=null; for(const b of btns){const mm=b.t.match(/^(\\d[\\d,]*)\\s*\\/\\s*(\\d[\\d,]*)$/); if(mm)m=mm;}
    return m?parseInt(m[1].replace(/,/g,'')):null;}"""


def _table_row_count(page, timeout_s=45):
    """Read the toolbar rows-count button ('N/M') and return N once it is STABLE
    (2 consecutive equal reads ~0.8s apart) — avoids reading a stale count while
    the grid is still re-filtering after a filter change."""
    last = None; stable = 0
    for _ in range(timeout_s):
        n = page.evaluate(_ROWCOUNT_JS)
        if n is not None:
            if n == last:
                stable += 1
                if stable >= 2:
                    return n
            else:
                stable = 0; last = n
        page.wait_for_timeout(800)
    return last


# ---------------------------------------------------------------- find-people

def _buyer_fill(page, titles, mode, say):
    seller_people_searches.inject(page)
    sen = page.evaluate("() => window.__ext.fillSeniority()")
    if sen.get("notFound"):
        sen2 = page.evaluate("() => window.__ext.fillSeniority()")  # retry missing
        say(f"  seniority retry, notFound now={sen2.get('notFound')}")
    else:
        say(f"  seniority +{len(sen.get('added',[]))}")
    jt = page.evaluate("(a) => window.__ext.fillJobTitles(a.t, a.m)", {"t": titles, "m": mode})
    say(f"  jobtitle({mode}) +{len(jt.get('added',[]))} notFound={len(jt.get('notFound',[]))} {jt.get('modeNote')}")
    loc = page.evaluate("(c) => window.__ext.fillCountries(c, false)", COUNTRIES)
    say(f"  countries +{len(loc.get('added',[]))} notFound={loc.get('notFound')}")
    val = "?"
    for _ in range(4):
        val = page.evaluate("() => window.__ext.setLimitPerCompany(%d)" % LIMIT_PER_COMPANY)
        if str(val).strip() == str(LIMIT_PER_COMPANY):
            break
        page.wait_for_timeout(1000)
    say(f"  limit per company = {val}")


def _run_search(page, wid, titles, mode, created, say):
    """One Find-people search -> save. Returns updated `created` flag."""
    seller_people_searches.open_find_people(page, wid, say)
    _buyer_fill(page, titles, mode, say)
    page.keyboard.press("Escape"); page.wait_for_timeout(700)
    if not created:
        if seller_people_searches.save_new_and_rename(page, wid, say):
            return True
        return False
    else:
        seller_people_searches.save_existing(page, say)
        return True


# ------------------------------------------------------------------- per event

def _reopen_view(page, wid):
    """Return to the Exhibitors_normalized table with the filter panel open."""
    clay_ui.open_workbook_by_id(page, wid)
    colcfg.focus_table_maybe_empty(page, TABLE); page.wait_for_timeout(900)
    apply_view_filters._close_tools_panel(page)
    if not _open_filter_panel(page):
        raise clay_ui.ClayUIError("filter panel did not open")


def run_buyer_searches(page, wid, name, say, done_segments=None, on_segment=None):
    """Run all segments for one event. `done_segments` (set of segment names)
    are skipped (segment-level resume for events that exceed one process
    lifetime). `on_segment(seg_name, created)` is called after each segment
    completes so the caller can persist progress."""
    done = set(done_segments or [])
    # 0. resolve any existing / interrupted 'Buyers - People' first (navigates).
    #    (Never touches 'Sellers - People' — salvage skips '* - People' tables.)
    created = seller_people_searches._prepare_people_table(page, wid, say)
    # 1. set the view filters once: Side -> Buyer (keep Send), Classification ready
    _reopen_view(page, wid)
    set_side_buyer(page, say)
    cy = ensure_classification_filter(page, say)

    summary = []
    for seg in SEGMENTS:
        if seg["name"] in done:
            continue
        set_classification_value(page, cy, seg["name"], say)
        n = _table_row_count(page)
        say(f"-- segment {seg['name']!r}: {n} rows --")
        saved = []
        if n:
            for mode, titles in (("exact", seg["exact"]), ("contains", seg["contains"])):
                if not titles:
                    continue
                try:
                    created = _run_search(page, wid, titles, mode, created, say)
                    saved.append(mode)
                except Exception as e:
                    say(f"  !! {seg['name']}/{mode} failed: {str(e)[:150]}")
                # a search navigates away — restore the filtered table view
                _reopen_view(page, wid)
                cy = ensure_classification_filter(page, say)
                set_classification_value(page, cy, seg["name"], say)
        summary.append({"segment": seg["name"], "rows": n, "saved": saved})
        if on_segment:
            on_segment(seg["name"], created)   # persist progress after each segment
    return {"workbook_id": wid, "workbook_name": name,
            "status": "ok" if created else "no_results",
            "created_table": created, "segments": summary}
