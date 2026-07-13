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
sys.path.insert(0, os.path.join(AUTO_DIR, "clay_sync"))
sys.path.insert(0, os.path.join(AUTO_DIR, "build_automation"))

import clay_ui        # noqa: E402
import common         # noqa: E402
import build_lib as B  # noqa: E402

TABLE = "Exhibitors_normalized"
TEMPLATE = "Exhibitors - All Columns - v1"
FILL = [("Country", "Country"), ("Description", "Description")]
# a column v1 adds that the reverted base lacks — its presence => already applied
V1_SIGNATURE = "Official Domain"

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


def _open_v1(page):
    page.keyboard.press("Control+e")
    page.wait_for_timeout(2000)
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
    B.focus_table(page, TABLE)

    # idempotency: already has v1?
    if clay_ui._find_header_rect(page, V1_SIGNATURE):
        say(f"SKIP {name}: v1 already applied ({V1_SIGNATURE!r} present)")
        return {"workbook_id": wid, "workbook_name": name, "status": "ok",
                "note": "already_applied"}

    _open_v1_retry(page)

    plan = []
    for label, target in FILL:
        info = page.evaluate(_FIELD_BOX, label)
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
