"""Apply + RUN the "Exhibitors - All Columns - v1" template on one workbook's
Exhibitors_normalized (assumes it's been reverted to the through-Website base).

Maps the two empty config fields to the raw columns (Country->Country,
Description->Description; Company Name / Website are pre-filled by auto-map, so
skipped), then Save AND RUN all rows in this view (executes, consumes credits).
Idempotent: skips a workbook that already has v1 applied.
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

# Entity/ICP-driven (defaults exhibitors/labs; override via CLAY_PIPELINE_ENTITY
# env or the rollout's --entity). Template content is entity-specific — see the
# note in config/entity-types/<entity>.yaml: templates.
_CFG = pcfg.load()
TABLE = _CFG.main_table
TEMPLATE = _CFG.templates.get("all_columns", "Exhibitors - All Columns - v1")
# Template config-field label -> raw column to map it to. Each entity's saved
# template labels its two fillable fields with that entity's own source-column
# names, so label == target here: Exhibitors' template asks "Country"/"Description"
# (its columns), Sponsors' asks "Location"/"Company Description" (its columns) —
# both taken from config/entity-types/<entity>.yaml: field_sources. Company Name /
# Website are pre-filled by auto-map and skipped.
_FS = _CFG.entity_cfg.get("field_sources", {})
_COUNTRY_FIELD = _FS.get("country_source_field", "Country")
_DESC_FIELD = _FS.get("description_source_field", "Description")
# Prefer an explicit template_v1_fill from the entity config (list of
# [field_label, column]); else fall back to the field_sources-derived pair.
_EXPLICIT_FILL = _CFG.entity_cfg.get("template_v1_fill")
if _EXPLICIT_FILL:
    FILL = [(f[0], f[1]) for f in _EXPLICIT_FILL]
else:
    FILL = [(_COUNTRY_FIELD, _COUNTRY_FIELD), (_DESC_FIELD, _DESC_FIELD)]
# a column v1 adds that the reverted base lacks — its presence => already applied
ALL_COLUMNS_MARKER = "Official Domain"

_FIELD_BOX = """(label)=>{
  const norm=s=>(s||'').replace(/\\s+/g,' ').trim();
  const leaves=[...document.querySelectorAll('*')].filter(e=>!e.children.length);
  let ly=null;
  for(const el of leaves){const r=el.getBoundingClientRect();
    if(r.x>1250&&r.y>220&&r.y<640&&norm(el.textContent)===label){ly=r.y;break;}}
  if(ly===null)return null;
  for(const el of leaves){const r=el.getBoundingClientRect();
    if(r.x>1250&&r.y>ly+5&&r.y<ly+48&&(el.textContent||'').includes('Start typing'))
      return {x:Math.round(r.x+30),y:Math.round(r.y+r.height/2),empty:true};}
  return {empty:false};
}"""


def _open_enrichment_dialog(page):
    """Open the 'Add enrichment' dialog. The Tools panel is open by default now,
    so click its 'View all enrichments' button directly; only fall back to
    Ctrl+E (which used to be required) if the button isn't immediately present."""
    va = page.get_by_text("View all enrichments", exact=False)
    try:
        va.first.click(timeout=8000)
        return
    except Exception:
        pass
    page.keyboard.press("Control+e")
    page.wait_for_timeout(2000)
    va.first.click(timeout=15000)


def _open_v1(page):
    _open_enrichment_dialog(page)
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
        raise clay_ui.ClayUIError("v1 config panel did not open")


def _open_v1_retry(page, tries=3):
    last = None
    for _ in range(tries):
        try:
            _open_v1(page); return
        except Exception as e:
            last = e
            try:
                page.keyboard.press("Escape"); page.wait_for_timeout(2500)
            except Exception:
                pass
    raise clay_ui.ClayUIError(f"could not open v1 after {tries}: {last}")


def _save_disabled(page):
    s = page.get_by_role("button", name="Save", exact=True).last
    return s.evaluate("el=>el.disabled||el.getAttribute('data-disabled')!==null")


def apply_v1(page, entry, dry_run, say):
    wid = entry["workbook_id"]; name = entry["workbook_name"]
    clay_ui.open_workbook_by_id(page, wid)
    colcfg.focus_table(page, TABLE)

    # idempotency: already has v1?
    if clay_ui._find_header_rect(page, ALL_COLUMNS_MARKER):
        say(f"SKIP {name}: v1 already applied ({ALL_COLUMNS_MARKER!r} present)")
        return {"workbook_id": wid, "workbook_name": name, "status": "ok",
                "note": "already_applied"}

    _open_v1_retry(page)

    plan = []
    for label, target in FILL:
        # Poll for the field: on a slow link the config panel can take several
        # seconds to render its rows, so retry before declaring it missing.
        info = None
        for _ in range(12):
            info = page.evaluate(_FIELD_BOX, label)
            if info:
                break
            page.wait_for_timeout(1000)
        if not info:
            say(f"ABORT {name}: field {label!r} not found")
            page.keyboard.press("Escape")
            return {"workbook_id": wid, "workbook_name": name, "status": "aborted",
                    "reason": f"field_missing:{label}"}
        if not info.get("empty"):
            continue  # pre-filled -> leave
        plan.append((label, target, info))

    if dry_run:
        say(f"DRYRUN {name}: would map {[(l,t) for l,t,_ in plan]} then Save-and-run")
        page.keyboard.press("Escape")
        return {"workbook_id": wid, "workbook_name": name, "status": "dryrun",
                "to_fill": [(l, t) for l, t, _ in plan]}

    for label, target, info in plan:
        page.mouse.click(info["x"], info["y"]); page.wait_for_timeout(900)
        page.keyboard.type(target, delay=25); page.wait_for_timeout(1300)
        page.keyboard.press("Enter"); page.wait_for_timeout(1000)
        say(f"  set {label!r} = {target!r}")

    enabled = False
    for _ in range(10):
        if not _save_disabled(page):
            enabled = True; break
        page.wait_for_timeout(1000)
    if not enabled:
        say(f"ABORT {name}: Save stayed disabled")
        page.keyboard.press("Escape")
        return {"workbook_id": wid, "workbook_name": name, "status": "aborted",
                "reason": "save_disabled"}

    # Save split menu -> "Save and don't run" (just add the columns; the run is
    # triggered separately below, because the apply-time run doesn't persist).
    save = page.get_by_role("button", name="Save", exact=True).last
    r = save.bounding_box()
    page.mouse.click(r["x"] + r["width"] - 5, r["y"] + r["height"] / 2)
    page.wait_for_timeout(1200)
    dr = page.get_by_role("menuitem", name="Save and don't run", exact=True)
    if not (dr.count() and dr.first.is_visible()):
        items = [m.inner_text().strip().replace("\n", " ") for m in
                 page.get_by_role("menuitem").all() if m.is_visible()]
        say(f"ABORT {name}: no 'Save and don't run' option: {items}")
        page.keyboard.press("Escape")
        return {"workbook_id": wid, "workbook_name": name, "status": "aborted",
                "reason": "no_save_option"}
    dr.first.click(timeout=8000)
    page.wait_for_timeout(4000)
    say(f"DONE {name}: applied v1 (dormant); run triggered in the run pass")
    return {"workbook_id": wid, "workbook_name": name, "status": "ok",
            "note": "applied_dormant"}
