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
import browser_session         # noqa: E402
import column_config as colcfg  # noqa: E402
import pipeline_config as pcfg  # noqa: E402

# Entity/ICP-driven (defaults exhibitors/labs; override via CLAY_PIPELINE_ENTITY).
# The lookup template's field-mapping is entity-specific (raw columns differ) —
# verify the Sponsors template before running it for Sponsors.
_CFG = pcfg.load()
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

# A config-field label lives in the panel's left label column (x~1270). Its y
# varies between workbooks, so match by normalized text within that column. Some
# labels ("Website (2)") render as a parent with child spans, so we match on the
# element's whole textContent (not leaf-only) and pick the tightest such element.
_LABEL_Y = """(label)=>{const norm=s=>(s||'').replace(/\\s+/g,' ').trim();
  let best=null;
  for(const el of document.querySelectorAll('*')){
    if(norm(el.textContent)!==label) continue;
    const r=el.getBoundingClientRect();
    if(r.x>1255&&r.x<1400&&r.y>140&&r.y<560&&r.width>0&&r.width<240){
      if(best===null||r.width<best.w) best={y:Math.round(r.y),w:r.width};
    }
  }
  return best===null?null:best.y;}"""

_NAME_EMPTY = """(ly)=>{const ls=[...document.querySelectorAll('*')].filter(e=>!e.children.length);
  for(const el of ls){const r=el.getBoundingClientRect();
  if(r.x>1255&&r.y>ly+5&&r.y<ly+60&&(el.textContent||'').includes('Start typing'))return true;}return false;}"""


def _open_template(page):
    # Tools panel is open by default now; click 'View all enrichments' directly,
    # falling back to Ctrl+E only if the button isn't present.
    va = page.get_by_text("View all enrichments", exact=False)
    try:
        va.first.click(timeout=8000)
    except Exception:
        page.keyboard.press("Control+e"); page.wait_for_timeout(2000)
        va.first.click(timeout=15000)
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


def _select_enrich(page, field_label, sub_option, say):
    """Map the config field `field_label` to the Enrich Company sub-output
    `sub_option` (e.g. field 'Name' -> 'Name', field 'Website (2)' -> 'Website
    (2)'). Opens the field's column dropdown, expands the Enrich Company group,
    and clicks the sub-output. Idempotent: skips an already-filled field."""
    # Poll for the label: the config panel's field rows can render a beat after
    # the "Configure" header appears (more so on a slow link).
    ly = None
    for _ in range(12):
        ly = page.evaluate(_LABEL_Y, field_label)
        if ly is not None:
            break
        page.wait_for_timeout(1000)
    if ly is None:
        raise clay_ui.ClayUIError(f"{field_label!r} field label not found")
    if not page.evaluate(_NAME_EMPTY, ly):
        say(f"  {field_label} field already filled — leaving as-is")
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
        raise clay_ui.ClayUIError(f"{EC!r} not found in {field_label} dropdown")
    page.mouse.click(pt["x"], pt["y"]); page.wait_for_timeout(1200)
    npt = None
    page.mouse.move(1400, 500)
    for _ in range(8):
        npt = page.evaluate(_FINDDD, sub_option)
        if npt:
            break
        page.mouse.wheel(0, -200); page.wait_for_timeout(300)
    if not npt:
        raise clay_ui.ClayUIError(
            f"{sub_option!r} sub-option not found under Enrich Company for {field_label!r}")
    page.mouse.click(npt["x"], npt["y"]); page.wait_for_timeout(1200)
    if page.evaluate(_NAME_EMPTY, ly):
        raise clay_ui.ClayUIError(f"{field_label} field still empty after selection")
    say(f"  {field_label} = Enrich Company -> {sub_option}")


# Config fields the lookup template exposes, each mapped to its Enrich Company
# sub-output. The user's edited "Sponsors - Lookup & Send Data" leaves both empty
# (the older Exhibitors template pre-filled Website (2)); _select_enrich skips any
# field that is already filled, so this list is safe for either template.
LOOKUP_FILL = [("Website (2)", "Website (2)"), ("Name", "Name")]


def _save_disabled(page):
    s = page.get_by_role("button", name="Save", exact=True).last
    return s.evaluate("el=>el.disabled||el.getAttribute('data-disabled')!==null")


def apply_lookup(page, entry, dry_run, say):
    wid = entry["workbook_id"]; name = entry["workbook_name"]
    clay_ui.open_workbook_by_id(page, wid)
    colcfg.focus_table(page, TABLE)
    page.wait_for_timeout(800)

    if clay_ui._find_header_rect(page, SIG):
        say(f"SKIP {name}: template already applied ({SIG!r} present)")
        return {"workbook_id": wid, "workbook_name": name, "status": "ok",
                "note": "already_applied"}

    _open_template_retry(page)
    # Map fields in panel order (Website (2) above Name): filling an earlier field
    # can auto-open the next empty field's dropdown, so doing them top-down leaves
    # no dropdown open after the last one — nothing to swallow the Save click.
    for field_label, sub in LOOKUP_FILL:
        _select_enrich(page, field_label, sub, say)

    if dry_run:
        say(f"DRYRUN {name}: fields set {[f for f, _ in LOOKUP_FILL]}; would Save and run in this view")
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
