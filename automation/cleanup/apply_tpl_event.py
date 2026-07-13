"""Apply the "Exhibitors - All Columns" template to one workbook's
Exhibitors_normalized table, mapping only the EMPTY fields per the spec, and
Save WITHOUT running.

Flow (validated via recon against the live UI):
  Ctrl+E -> View all enrichments -> Templates tab -> select template ->
  fill empty fields (click box, type exact column, Enter) -> Save caret ->
  "Save and don't run".

Conditional mapping (only applied to fields that are EMPTY; pre-filled fields
are left untouched, per the instructions):
  Company Name -> Company Name        (normally pre-filled -> skipped)
  Country -> Normalized Country
  Normalize a Domain -> Company Domain (normally pre-filled -> skipped)
  Normalized Country -> Normalized Country
  Description -> Description
"""

import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
AUTO_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, os.path.join(AUTO_DIR, "clay_sync"))
sys.path.insert(0, os.path.join(AUTO_DIR, "build_automation"))

import clay_ui        # noqa: E402
import common         # noqa: E402
import build_lib as B  # noqa: E402

TABLE = "Exhibitors_normalized"
TEMPLATE = "Exhibitors - All Columns"
ORDER = ["Company Name", "Country", "Normalize a Domain", "Normalized Country", "Description"]
MAPPING = {"Company Name": "Company Name", "Country": "Normalized Country",
           "Normalize a Domain": "Company Domain",
           "Normalized Country": "Normalized Country", "Description": "Description"}

# Capture each template field's label position + whether it's empty (shows the
# "Start typing" placeholder). Run BEFORE any fill so same-named value pills
# can't be mistaken for a field label.
_CAPTURE = """(labels) => {
  const norm = s => (s||'').replace(/\\s+/g,' ').trim();  // \\s matches nbsp
  const leaves=[...document.querySelectorAll('*')].filter(e=>!e.children.length);
  const res=[];
  for (const lab of labels){
    let ly=null;
    for (const el of leaves){ const r=el.getBoundingClientRect();
      if (r.x>1250 && r.y>220 && r.y<640 && norm(el.textContent)===lab){ ly=r.y; break; } }
    if (ly===null){ res.push({label:lab, found:false, empty:false, pt:null}); continue; }
    let empty=false, pt=null;
    for (const el of leaves){ const r=el.getBoundingClientRect();
      if (r.x>1250 && r.y>ly+5 && r.y<ly+48 && (el.textContent||'').includes('Start typing')){
        empty=true; pt={x:Math.round(r.x+30), y:Math.round(r.y+r.height/2)}; break; } }
    res.push({label:lab, found:true, empty, pt});
  }
  return res;
}"""


def _open_template(page):
    page.keyboard.press("Control+e")
    page.wait_for_timeout(2000)
    page.get_by_text("View all enrichments", exact=False).first.click(timeout=25000)
    page.wait_for_timeout(2500)
    page.get_by_role("tab", name="Templates").first.click(timeout=25000)
    page.wait_for_timeout(2000)

    # Created by -> You (shortens the huge, virtualized template list so the
    # target reliably renders; the full list often virtualizes it out).
    you = page.get_by_role("radio", name="You")
    if not you.count():
        you = page.get_by_text("You", exact=True)
    try:
        you.first.click(timeout=8000)
        page.wait_for_timeout(2000)
    except Exception:
        pass

    # locate the template; fall back to the modal search box, then retry
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
        page.wait_for_timeout(1500)
        tpl = page.get_by_text(TEMPLATE, exact=True)
    if not (tpl.count() and tpl.first.is_visible()):
        raise clay_ui.ClayUIError(f"template {TEMPLATE!r} not found")
    tpl.first.click(timeout=25000)
    page.wait_for_timeout(3500)
    if not page.get_by_text("Configure", exact=True).count():
        raise clay_ui.ClayUIError("template config panel did not open")


def _open_template_retry(page, tries=3):
    """Open the template config, retrying the whole flow — the enrichment modal
    navigation intermittently times out under load; a fresh attempt clears it."""
    last = None
    for _ in range(tries):
        try:
            _open_template(page)
            return
        except Exception as e:
            last = e
            try:
                page.keyboard.press("Escape")
                page.wait_for_timeout(2500)
            except Exception:
                pass
    raise clay_ui.ClayUIError(f"could not open template after {tries} tries: {last}")


def _save_disabled(page):
    s = page.get_by_role("button", name="Save", exact=True).last
    return s.evaluate("el => el.disabled || el.getAttribute('data-disabled')!==null")


def apply_template(page, entry, dry_run, say):
    wid = entry["workbook_id"]
    name = entry["workbook_name"]

    clay_ui.open_workbook_by_id(page, wid)
    B.focus_table(page, TABLE)
    _open_template_retry(page)

    fields = page.evaluate(_CAPTURE, ORDER)
    missing = [f["label"] for f in fields if not f["found"]]
    # Only the fields we actually fill are critical; Company Name / Normalize a
    # Domain are skip-only (pre-filled) and their pre-filled value varies per
    # workbook, so a miss there must not abort.
    FILLABLE = {"Country", "Normalized Country", "Description"}
    critical = [x for x in missing if x in FILLABLE]
    if critical:
        say(f"ABORT {name}: required fields not found: {critical}")
        page.keyboard.press("Escape")
        return {"workbook_id": wid, "workbook_name": name, "status": "aborted",
                "reason": "fields_missing", "missing": critical}
    if missing:
        say(f"  note: skip-only fields not located (ignored): {missing}")

    to_fill = [f for f in fields if f["empty"]]
    plan = [(f["label"], MAPPING[f["label"]]) for f in to_fill]
    prefilled = [f["label"] for f in fields if not f["empty"]]

    if dry_run:
        say(f"DRYRUN {name}: pre-filled(skip)={prefilled} | would set {plan}")
        page.keyboard.press("Escape")
        return {"workbook_id": wid, "workbook_name": name, "status": "dryrun",
                "to_fill": plan, "prefilled": prefilled}

    say(f"APPLY {name}: pre-filled(skip)={prefilled} | filling {plan}")
    for f in to_fill:
        target = MAPPING[f["label"]]
        page.mouse.click(f["pt"]["x"], f["pt"]["y"])
        page.wait_for_timeout(900)
        page.keyboard.type(target, delay=25)
        page.wait_for_timeout(1300)
        page.keyboard.press("Enter")
        page.wait_for_timeout(1000)
        say(f"  - set {f['label']!r} = {target!r}")

    # all required placeholders should be gone now
    left = page.get_by_text("Start typing or select a column").count()
    # wait for Save to enable
    enabled = False
    for _ in range(10):
        if not _save_disabled(page):
            enabled = True
            break
        page.wait_for_timeout(1000)
    if not enabled:
        say(f"ABORT {name}: Save stayed disabled (placeholders left={left})")
        page.keyboard.press("Escape")
        return {"workbook_id": wid, "workbook_name": name, "status": "aborted",
                "reason": "save_disabled", "placeholders_left": left}

    # open the Save split menu and click "Save and don't run"
    save = page.get_by_role("button", name="Save", exact=True).last
    r = save.bounding_box()
    page.mouse.click(r["x"] + r["width"] - 5, r["y"] + r["height"] / 2)
    page.wait_for_timeout(1200)
    opt = page.get_by_role("menuitem", name="Save and don't run", exact=True)
    if not (opt.count() and opt.first.is_visible()):
        # some builds render it as a button
        opt = page.get_by_role("button", name="Save and don't run", exact=True)
    if not opt.count():
        items = [m.inner_text().strip() for m in page.get_by_role("menuitem").all() if m.is_visible()]
        say(f"ABORT {name}: 'Save and don't run' not in menu: {items}")
        page.keyboard.press("Escape")
        return {"workbook_id": wid, "workbook_name": name, "status": "aborted",
                "reason": "no_dont_run_option"}
    opt.first.click(timeout=8000)
    page.wait_for_timeout(4000)
    say(f"DONE {name}: template applied, Save and don't run clicked")
    return {"workbook_id": wid, "workbook_name": name, "status": "ok",
            "filled": [p[0] for p in plan]}
