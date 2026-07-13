"""Apply two view filters to one workbook's Exhibitors_normalized:
   1) WHERE  Side           equal to     Seller
   2) AND    Send table data has results

Uses each column's header menu -> "Filter on this column" (which pre-fills the
column), then sets the value / operator. Idempotent: clears existing filters
first, then adds the two, so re-running yields the same two filters.
"""

import os
import re
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
AUTO_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, os.path.join(AUTO_DIR, "clay_sync"))
sys.path.insert(0, os.path.join(AUTO_DIR, "build_automation"))

import clay_ui        # noqa: E402
import common         # noqa: E402
import build_lib as B  # noqa: E402

TABLE = "Exhibitors_normalized"

# ---- geometry helpers (filter panel rows) ---------------------------------
# The panel sits at the top of the grid. Rows are ~48px apart. A credit-limit
# banner may shift everything, so anchor row-Y off the "Where"/"And" labels.

_LABEL_Y = """(txt)=>{for(const el of document.querySelectorAll('div,span,label,p,button')){
  const r=el.getBoundingClientRect();
  if(r.width===0||r.x>320||r.y<115||r.y>390)continue;
  if((el.textContent||'').trim()===txt) return Math.round(r.y+r.height/2);}
  return null;}"""

# click an exact-text entry inside an open dropdown (scoped away from the grid)
_CLICK_OPT = """(a)=>{const [txt,ymin,ymax]=a;
  for(const el of document.querySelectorAll('[role="option"],[role="menuitem"],li,button')){
    const r=el.getBoundingClientRect(); if(r.width===0)continue;
    if(r.x<380||r.x>820||r.y<ymin||r.y>ymax)continue;
    if((el.textContent||'').trim().toLowerCase()===txt.toLowerCase())
      return {x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2)};}
  return null;}"""

# find an exact-text option in a bounded x/y box (for the column list)
_FIND_OPT = """(a)=>{const[txt,xmin,xmax,ymin,ymax]=a;
  for(const el of document.querySelectorAll('[role="option"],[role="menuitem"],li,button')){
    const r=el.getBoundingClientRect(); if(r.width===0||r.height===0)continue;
    const cx=r.x+r.width/2, cy=r.y+r.height/2;
    if(cx<xmin||cx>xmax||cy<ymin||cy>ymax)continue;
    if((el.textContent||'').trim()===txt) return {x:Math.round(cx),y:Math.round(cy)};}
  return null;}"""

# the column-selector combobox on the row centered at Y
_COMBO_AT = """(y)=>{for(const el of document.querySelectorAll('[role="combobox"]')){
  const r=el.getBoundingClientRect(); if(r.width===0)continue;
  if(Math.abs(r.y+r.height/2 - y)<18)
    return {x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2)};} return null;}"""

# the operator button on the row centered at Y (immediately right of the column)
_OP_AT = """(y)=>{for(const el of document.querySelectorAll('button,[role="button"]')){
  const r=el.getBoundingClientRect(); const cx=r.x+r.width/2, cy=r.y+r.height/2;
  if(r.width===0||cx<360||cx>720||Math.abs(cy-y)>18)continue;
  const t=(el.textContent||'').trim();
  if(t==='equal to'||t==='has an error'||t==='has results'||t==='is empty'||t==='is not empty')
    return {x:Math.round(cx),y:Math.round(cy)};}
  return null;}"""

# the VALUE box on row Y: the first wide (non-icon) control right of the
# operator. Its absolute x shifts when the Enrich sidebar narrows the layout,
# so anchor it off the operator's right edge instead of hardcoding x.
_VAL_AT = """(y)=>{
  let opR=0;
  for(const el of document.querySelectorAll('button,[role="button"]')){
    const r=el.getBoundingClientRect(); if(Math.abs(r.y+r.height/2-y)>16)continue;
    if((el.textContent||'').trim()==='equal to') opR=Math.max(opR,r.right);}
  if(!opR)return null;
  let best=null;
  for(const el of document.querySelectorAll('button,[role="button"],[role="textbox"],input,div')){
    const r=el.getBoundingClientRect(); if(Math.abs(r.y+r.height/2-y)>16||r.width<120)continue;
    if(r.x<opR+2||r.x>opR+120)continue;
    if(!best||r.x<best.lx) best={x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2),lx:r.x};}
  return best;}"""

# verify the two filters are present. Only the column selectors are comboboxes
# (grid headers are plain buttons), so read those, plus exact-text checks for
# the value ('Seller') and operator ('has results') scoped by x-band.
_VERIFY = """()=>{
  const inpanel=r=>r.width>0&&r.y>125&&r.y<340;
  const combos=[...document.querySelectorAll('[role="combobox"]')]
    .filter(e=>inpanel(e.getBoundingClientRect()))
    .map(e=>(e.textContent||'').trim());
  const hasBtn=(txt,xmin,xmax)=>[...document.querySelectorAll('button,[role="button"]')]
    .some(e=>{const r=e.getBoundingClientRect(); const cx=r.x+r.width/2;
      return inpanel(r)&&cx>xmin&&cx<xmax&&(e.textContent||'').trim()===txt;});
  return {combos:combos, seller:hasBtn('Seller',540,1010),
          hasResults:hasBtn('has results',360,760)};}"""


def _filter_on_column(page, col):
    clay_ui.open_column_menu(page, col)
    page.wait_for_timeout(900)
    hit = False
    for mi in page.get_by_role("menuitem").all():
        try:
            if mi.is_visible() and "Filter on this column" in mi.inner_text():
                mi.click(timeout=6000); hit = True; break
        except Exception:
            pass
    if not hit:
        raise clay_ui.ClayUIError(f"'Filter on this column' not found for {col!r}")
    page.wait_for_timeout(1800)


def _clear_filters(page):
    cf = page.get_by_role("button", name="Clear filters", exact=True)
    if cf.count():
        cf.first.click(timeout=6000); page.wait_for_timeout(1000)


def _close_tools_panel(page):
    """The right-hand Enrich/Tools sidebar, if open, narrows the layout and
    shifts the filter panel's value box left of its usual x. Close it so the
    hardcoded control positions are consistent across workbooks."""
    is_open = page.evaluate("""()=>{for(const e of document.querySelectorAll('*')){
        const r=e.getBoundingClientRect();
        if(r.width>0&&r.x>1240&&r.y<260&&(e.textContent||'').trim()==='Enrich')return true;}
      return false;}""")
    if is_open:
        try:
            page.get_by_role("button", name="Tools").first.click(timeout=4000)
            page.wait_for_timeout(900)
        except Exception:
            pass


def _row_y(page, label, tries=12):
    """Poll for a filter row's connector label ('Where'/'And') Y — the panel
    can take a moment to render after 'Filter on this column'."""
    for _ in range(tries):
        y = page.evaluate(_LABEL_Y, label)
        if y is not None:
            return y
        page.wait_for_timeout(500)
    return None


def _select_column(page, y, target):
    """On the filter row centered at Y, open its column combobox and pick
    `target` by scrolling the dropdown (no grid-header interaction)."""
    cb = page.evaluate(_COMBO_AT, y)
    if not cb:
        raise clay_ui.ClayUIError(f"column combobox not found on row y={y}")
    page.mouse.click(cb["x"], cb["y"]); page.wait_for_timeout(900)
    box = [target, 240, 620, y + 15, 960]
    opt = page.evaluate(_FIND_OPT, box)
    if not opt:
        page.mouse.move(cb["x"], y + 130)
        for _ in range(20):
            page.mouse.wheel(0, 260); page.wait_for_timeout(280)
            opt = page.evaluate(_FIND_OPT, box)
            if opt:
                break
    if not opt:
        raise clay_ui.ClayUIError(f"column {target!r} not found in dropdown")
    page.mouse.click(opt["x"], opt["y"]); page.wait_for_timeout(1000)


def apply_filters(page, entry, dry_run, say):
    wid = entry["workbook_id"]; name = entry["workbook_name"]
    clay_ui.open_workbook_by_id(page, wid)
    # a pre-existing filter may currently show 0 rows -> don't wait on a data cell
    B.focus_table_maybe_empty(page, TABLE)
    page.wait_for_timeout(1200)
    _close_tools_panel(page)

    # open the panel (adds a Side row) then clear everything -> clean slate
    _filter_on_column(page, "Side")
    _clear_filters(page)

    if dry_run:
        say(f"DRYRUN {name}: would add Side=Seller AND Send table data has results")
        page.keyboard.press("Escape")
        return {"workbook_id": wid, "workbook_name": name, "status": "dryrun"}

    # --- Row 1: Side equal to Seller ---
    _filter_on_column(page, "Side")
    y1 = _row_y(page, "Where")
    if y1 is None:
        # one more attempt: re-open the filter on Side
        _filter_on_column(page, "Side")
        y1 = _row_y(page, "Where")
    if y1 is None:
        raise clay_ui.ClayUIError("filter row 'Where' not found")
    val = page.evaluate(_VAL_AT, y1)
    if not val:
        raise clay_ui.ClayUIError("Side value box not found")
    page.mouse.click(val["x"], val["y"]); page.wait_for_timeout(500)
    page.keyboard.type("Seller", delay=40)
    page.keyboard.press("Enter"); page.wait_for_timeout(1200)

    # --- Row 2: Send table data has results ---
    # Build it inside the open panel: 'Add filter' -> pick column from its own
    # dropdown -> set operator. (Never touch the Send-table-data grid header —
    # it may be off-screen, which opens the enrich panel and drops row 1.)
    page.get_by_role("button", name="Add filter", exact=True).first.click(timeout=6000)
    page.wait_for_timeout(1200)
    y2 = _row_y(page, "And")
    if y2 is None:
        raise clay_ui.ClayUIError("filter row 'And' not found")
    _select_column(page, y2, "Send table data")
    # open row2 operator dropdown (default 'has an error') and pick 'has results'
    op = page.evaluate(_OP_AT, y2)
    if not op:
        raise clay_ui.ClayUIError("row2 operator button not found")
    page.mouse.click(op["x"], op["y"]); page.wait_for_timeout(1000)
    pt = page.evaluate(_CLICK_OPT, ["has results", y2, y2 + 620])
    if not pt:
        # dump what's available for diagnosis
        opts = page.evaluate("""(a)=>{const [ymin,ymax]=a;const o=[];
          for(const el of document.querySelectorAll('[role="option"],[role="menuitem"],li,button')){
            const r=el.getBoundingClientRect(); if(r.width===0)continue;
            if(r.x<380||r.x>820||r.y<ymin||r.y>ymax)continue;
            const t=(el.textContent||'').trim(); if(t&&t.length<28)o.push(t);}
          return [...new Set(o)];}""", [y2, y2 + 620])
        raise clay_ui.ClayUIError(f"'has results' operator not found; options={opts}")
    page.mouse.click(pt["x"], pt["y"]); page.wait_for_timeout(1200)

    # --- verify --- (let the grid finish re-filtering first)
    rc = "?"
    for _ in range(12):
        rc = page.evaluate("()=>{const m=document.body.innerText.match(/\\d+\\/\\d+ rows/);return m?m[0]:'?';}")
        if rc != "?" and not rc.startswith("0/"):
            break
        page.wait_for_timeout(1000)
    v = page.evaluate(_VERIFY)
    # combobox labels can lag; the presence of 2 filter rows + the exact Seller
    # value + the has-results operator (unique to the Send-table-data row) is
    # sufficient given we build the rows deterministically per column.
    cols_ok = ("Side" in v["combos"] and "Send table data" in v["combos"]) \
        or len(v["combos"]) >= 2
    ok = cols_ok and v["seller"] and v["hasResults"]
    page.keyboard.press("Escape")
    if not ok:
        say(f"  VERIFY FAILED: {v} | {rc}")
        return {"workbook_id": wid, "workbook_name": name, "status": "verify_failed",
                "verify": v, "rows": rc}
    say(f"DONE {name}: Side=Seller AND Send table data has results | {rc}")
    return {"workbook_id": wid, "workbook_name": name, "status": "ok", "rows": rc}
