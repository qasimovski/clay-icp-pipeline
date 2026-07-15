"""Apply + run "Exhibitors - Lookup & Send Table Data - v1" on one workbook's
Exhibitors_normalized. Config: Website (2) is pre-filled (skip); the Name field
is empty and must be set to the "Name" sub-output of the "Enrich Company -
Terrapinn - Competitors" column via the dropdown arrow -> scroll to that column
-> pick "Name". Then Save and run in this view (fast: lookup + send, persists).
Idempotent: skips if 'Send table data' column already present."""

import os
import re
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
AUTO_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, SCRIPT_DIR)
sys.path.insert(0, os.path.join(AUTO_DIR, "clay_sync"))
sys.path.insert(0, os.path.join(AUTO_DIR, "build_automation"))

import clay_ui        # noqa: E402
import common         # noqa: E402
import build_lib as B  # noqa: E402
import pipeline_config as PC  # noqa: E402

# Entity/ICP-driven (defaults exhibitors/labs; override via CLAY_PIPELINE_ENTITY).
# The lookup template's field-mapping is entity-specific (raw columns differ) —
# verify the Sponsors template before running it for Sponsors.
_CFG = PC.load()
TABLE = _CFG.main_table
TEMPLATE = _CFG.templates.get("lookup", "Exhibitors - Lookup & Send Table Data - v1")
EC = "Enrich Company - Terrapinn - Competitors"
SIG = "Send table data"   # present => this template already applied

# find a dropdown entry by exact text, strictly within the field popover column.
# Wide y-band because the config panel's vertical position varies per workbook;
# x-scope (1275-1550) + exact-text keeps this off the grid headers / other fields.
_FINDDD = """(txt)=>{
  for(const b of document.querySelectorAll('button,[role="option"],[role="menuitem"],div[role="button"]')){
    const r=b.getBoundingClientRect();
    if(r.width===0||r.x<1275||r.x>1550||r.y<150||r.y>900) continue;
    if((b.textContent||'').trim()===txt) return {x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2)};
  } return null; }"""

# The 'Name' field label lives in the config panel's left label column (x~1270).
# Its y varies between workbooks, so match by exact text + narrow x, wide y.
_NAME_LABEL_Y = """()=>{const ls=[...document.querySelectorAll('*')].filter(e=>!e.children.length);
  for(const el of ls){const r=el.getBoundingClientRect();
  if(r.x>1255&&r.x<1360&&r.y>140&&r.y<540&&(el.textContent||'').trim()==='Name')return Math.round(r.y);}return null;}"""

_NAME_EMPTY = """(ly)=>{const ls=[...document.querySelectorAll('*')].filter(e=>!e.children.length);
  for(const el of ls){const r=el.getBoundingClientRect();
  if(r.x>1255&&r.y>ly+5&&r.y<ly+60&&(el.textContent||'').includes('Start typing'))return true;}return false;}"""


def _open_template(page):
    page.keyboard.press("Control+e"); page.wait_for_timeout(2000)
    page.get_by_text("View all enrichments", exact=False).first.click(timeout=25000)
    page.wait_for_timeout(2500)
    page.get_by_role("tab", name="Templates").first.click(timeout=25000)
    page.wait_for_timeout(2000)
    you = page.get_by_role("radio", name="You")
    if not you.count():
        you = page.get_by_text("You", exact=True)
    try:
        you.first.click(timeout=8000); page.wait_for_timeout(1500)
    except Exception:
        pass
    tpl = page.get_by_text(TEMPLATE, exact=True)
    for _ in range(6):
        if tpl.count() and tpl.first.is_visible():
            break
        sb = page.get_by_placeholder("Search for a data point or provider")
        try:
            if sb.count():
                sb.first.fill(TEMPLATE)
        except Exception:
            pass
        page.wait_for_timeout(1500); tpl = page.get_by_text(TEMPLATE, exact=True)
    if not (tpl.count() and tpl.first.is_visible()):
        raise clay_ui.ClayUIError(f"template {TEMPLATE!r} not found")
    tpl.first.click(timeout=20000)
    page.wait_for_timeout(3500)
    if not page.get_by_text("Configure", exact=True).count():
        raise clay_ui.ClayUIError("config panel did not open")


def _open_template_retry(page, tries=3):
    last = None
    for _ in range(tries):
        try:
            _open_template(page); return
        except Exception as e:
            last = e
            try:
                page.keyboard.press("Escape"); page.wait_for_timeout(2500)
            except Exception:
                pass
    raise clay_ui.ClayUIError(f"could not open template after {tries}: {last}")


def _select_name(page, say):
    ly = page.evaluate(_NAME_LABEL_Y)
    if ly is None:
        raise clay_ui.ClayUIError("Name field label not found")
    if not page.evaluate(_NAME_EMPTY, ly):
        say("  Name field already filled — leaving as-is")
        return
    page.mouse.click(1685, ly + 35)   # arrow -> open column dropdown
    page.wait_for_timeout(1200)
    page.mouse.move(1400, 560)
    pt = None
    for _ in range(15):
        pt = page.evaluate(_FINDDD, EC)
        if pt:
            break
        page.mouse.wheel(0, 240); page.wait_for_timeout(350)
    if not pt:
        raise clay_ui.ClayUIError(f"{EC!r} not found in Name dropdown")
    page.mouse.click(pt["x"], pt["y"]); page.wait_for_timeout(1200)
    npt = None
    page.mouse.move(1400, 500)
    for _ in range(8):
        npt = page.evaluate(_FINDDD, "Name")
        if npt:
            break
        page.mouse.wheel(0, -200); page.wait_for_timeout(300)
    if not npt:
        raise clay_ui.ClayUIError("'Name' sub-option not found under Enrich Company")
    page.mouse.click(npt["x"], npt["y"]); page.wait_for_timeout(1200)
    if page.evaluate(_NAME_EMPTY, ly):
        raise clay_ui.ClayUIError("Name field still empty after selection")
    say("  Name = Enrich Company -> Name")


def _save_disabled(page):
    s = page.get_by_role("button", name="Save", exact=True).last
    return s.evaluate("el=>el.disabled||el.getAttribute('data-disabled')!==null")


def apply_lookup(page, entry, dry_run, say):
    wid = entry["workbook_id"]; name = entry["workbook_name"]
    clay_ui.open_workbook_by_id(page, wid)
    B.focus_table(page, TABLE)
    page.wait_for_timeout(800)

    if clay_ui._find_header_rect(page, SIG):
        say(f"SKIP {name}: template already applied ({SIG!r} present)")
        return {"workbook_id": wid, "workbook_name": name, "status": "ok",
                "note": "already_applied"}

    _open_template_retry(page)
    _select_name(page, say)

    if dry_run:
        say(f"DRYRUN {name}: Name set; would Save and run in this view")
        page.keyboard.press("Escape")
        return {"workbook_id": wid, "workbook_name": name, "status": "dryrun"}

    for _ in range(10):
        if not _save_disabled(page):
            break
        page.wait_for_timeout(1000)

    save = page.get_by_role("button", name="Save", exact=True).last
    r = save.bounding_box()
    page.mouse.click(r["x"] + r["width"] - 5, r["y"] + r["height"] / 2)
    page.wait_for_timeout(1200)
    ti = None
    for mi in page.get_by_role("menuitem").all():
        try:
            t = mi.inner_text().strip()
            if mi.is_visible() and re.search(r"Save and run.*in this view", t, re.I):
                ti = mi; break
        except Exception:
            pass
    if ti is None:
        items = [m.inner_text().strip().replace("\n", " ") for m in
                 page.get_by_role("menuitem").all() if m.is_visible()]
        say(f"ABORT {name}: no 'Save and run ... in this view': {items[-4:]}")
        page.keyboard.press("Escape")
        return {"workbook_id": wid, "workbook_name": name, "status": "aborted",
                "reason": "no_run_option"}
    lbl = ti.inner_text().strip().replace("\n", " ")
    ti.click(timeout=8000)
    # lookup+send is fast; wait for it to complete so the run persists
    page.wait_for_timeout(16000)
    st = page.evaluate("()=>{const t=document.body.innerText;return (t.match(/\\d+% of table completed/)||['?'])[0];}")
    say(f"DONE {name}: applied + {lbl!r} | {st}")
    return {"workbook_id": wid, "workbook_name": name, "status": "ok",
            "ran": lbl, "state": st}
