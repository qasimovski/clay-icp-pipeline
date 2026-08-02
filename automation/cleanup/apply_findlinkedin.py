"""Apply the "Find LinkedIn and Enrich Person" template to a single named
table (Speakers_normalized) in one workbook.

Mirrors apply_gsheet_lookup.py's structure: --recon to inspect the template's
Configure panel/field-mapping without saving, --dry-run to open the template
without saving, and a real apply path that waits for the run to reach 100%
completion (not just column presence) before returning "ok" - a table
sitting at 100% right after Save can still be mid-run for large tables.

Idempotent: skips (or resumes waiting, if not yet 100%) if the template's
signature column is already present. Skips (does not error) if the named
table doesn't exist in the workbook at all.
"""

import os
import re
import sys
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
AUTO_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, SCRIPT_DIR)
sys.path.insert(0, os.path.join(AUTO_DIR, "clay_sync"))
sys.path.insert(0, os.path.join(AUTO_DIR, "build_automation"))

import clay_ui        # noqa: E402
import browser_session         # noqa: E402
import column_config as colcfg  # noqa: E402
import column_completion  # noqa: E402  (column-scoped done check)

TEMPLATE = "Find LinkedIn and Enrich Person"
# Signature column indicating the template is already applied - confirmed via
# --recon before real use (see apply_gsheet_lookup.py's MARKER discovery process;
# update this once we've seen the real Configure panel / resulting columns).
MARKER = "Enrich person"

# Unlike the Google Sheet template, this one's 4 Configure fields (Name, Bio,
# LinkedIn, Email) are NOT auto-mapped (confirmed via --recon on Automation UK,
# 2026-07-25: all four show "Start typing or select a column"). Map each field
# to Speakers_normalized's own column of the same name.
FILL = [("Name", "Name"), ("Bio", "Bio"), ("LinkedIn", "LinkedIn"), ("Email", "Email")]

# Same config-panel field-box locator as apply_all_columns.py: find the label's
# y-position, then the "Start typing" placeholder box just below it.
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

_LABEL_SCAN_JS = """()=>{
  const out=[];
  const norm=s=>(s||'').replace(/\\s+/g,' ').trim();
  for(const el of document.querySelectorAll('*')){
    if(el.children.length) continue;
    const t=norm(el.textContent);
    if(!t) continue;
    const r=el.getBoundingClientRect();
    if(r.x>1240&&r.x<1600&&r.y>120&&r.y<900&&r.width>0){
      out.push({text:t, x:Math.round(r.x), y:Math.round(r.y), w:Math.round(r.width)});
    }
  }
  return out;
}"""


def _open_template(page):
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


def recon(page, entry, table_name, say, screenshot=None):
    """Open the template on `table_name` and report the Configure panel's
    contents WITHOUT filling or saving. Always Escapes at the end."""
    wid = entry["workbook_id"]; name = entry["workbook_name"]
    clay_ui.open_workbook_by_id(page, wid)
    if not colcfg.table_exists(page, table_name):
        say(f"SKIP {name}/{table_name}: table does not exist")
        return {"workbook_id": wid, "workbook_name": name, "table": table_name,
                "status": "no_table"}
    colcfg.focus_table_maybe_empty(page, table_name)
    page.wait_for_timeout(800)
    already = clay_ui._find_header_rect(page, MARKER)
    _open_template_retry(page)
    rows = page.evaluate(_LABEL_SCAN_JS)
    rows.sort(key=lambda r: (r["y"], r["x"]))
    say(f"RECON {name}/{table_name}: already_has_{MARKER!r}={bool(already)}")
    for r in rows:
        say(f"  y={r['y']:<4} x={r['x']:<4} w={r['w']:<4} {r['text']!r}".encode(
            "ascii", "backslashreplace").decode("ascii"))
    if screenshot:
        page.screenshot(path=screenshot)
        say(f"screenshot saved: {screenshot}")
    page.keyboard.press("Escape")
    return {"workbook_id": wid, "workbook_name": name, "table": table_name,
            "status": "recon", "already_applied": bool(already), "panel": rows}


def _save_disabled(page):
    s = page.get_by_role("button", name="Save", exact=True).last
    return s.evaluate("el=>el.disabled||el.getAttribute('data-disabled')!==null")



def _wait_for_full_completion(page, say, table_name, max_wait_s=1800, poll_s=12):
    """Completion needs the table-wide banner AND the marker column's own
    status to agree, so a stale table-wide 100% can't end the wait early."""
    start = time.time()
    last = None
    while time.time() - start < max_wait_s:
        complete, why = column_completion.already_complete(page, MARKER, colcfg)
        if complete:
            return True, 100
        pct = column_completion.table_pct(page)
        if pct != last:
            say(f"   ...{table_name} progress {pct}% ({why})")
            last = pct
        page.wait_for_timeout(poll_s * 1000)
    return False, last


def apply_findlinkedin(page, entry, table_name, dry_run, say):
    wid = entry["workbook_id"]; name = entry["workbook_name"]
    clay_ui.open_workbook_by_id(page, wid)

    if not colcfg.table_exists(page, table_name):
        say(f"SKIP {name}/{table_name}: table does not exist")
        return {"workbook_id": wid, "workbook_name": name, "table": table_name,
                "status": "no_table"}

    colcfg.focus_table_maybe_empty(page, table_name)
    page.wait_for_timeout(800)

    if clay_ui._find_header_rect(page, MARKER):
        complete, why = column_completion.already_complete(page, MARKER, colcfg)
        if complete:
            say(f"SKIP {name}/{table_name}: template already applied and complete ({why})")
            return {"workbook_id": wid, "workbook_name": name, "table": table_name,
                    "status": "ok", "note": "already_applied"}
        say(f"RESUME {name}/{table_name}: template already applied but not complete "
            f"({why}) - waiting for the run to finish all rows")
        done, last_pct = _wait_for_full_completion(page, say, table_name)
        if not done:
            say(f"INCOMPLETE {name}/{table_name}: still at {last_pct}% after max wait "
                f"- will resume on next run")
            return {"workbook_id": wid, "workbook_name": name, "table": table_name,
                    "status": "incomplete", "note": "already_applied", "state": last_pct}
        say(f"DONE {name}/{table_name}: already-applied run finished all rows (100%)")
        return {"workbook_id": wid, "workbook_name": name, "table": table_name,
                "status": "ok", "note": "already_applied_resumed"}

    _open_template_retry(page)

    plan = []
    for label, target in FILL:
        info = None
        for _ in range(12):
            info = page.evaluate(_FIELD_BOX, label)
            if info:
                break
            page.wait_for_timeout(1000)
        if not info:
            # Not every event's Configure panel shows all 4 fields - confirmed
            # via screenshot (Lab Facilities Summit Europe only showed Name/
            # LinkedIn/Email, no Bio row at all, despite the Bio column
            # genuinely existing in the table). Treat a missing field as
            # nothing-to-map rather than aborting the whole apply.
            say(f"  {label!r} field not present in this Configure panel - skipping")
            continue
        if not info.get("empty"):
            continue  # pre-filled -> leave
        plan.append((label, target, info))

    if dry_run:
        say(f"DRYRUN {name}/{table_name}: would map {[(l, t) for l, t, _ in plan]} "
            f"then Save and run")
        page.keyboard.press("Escape")
        return {"workbook_id": wid, "workbook_name": name, "table": table_name,
                "status": "dryrun", "to_fill": [(l, t) for l, t, _ in plan]}

    for label, target, info in plan:
        page.mouse.click(info["x"], info["y"]); page.wait_for_timeout(900)
        page.keyboard.type(target, delay=25); page.wait_for_timeout(1300)
        page.keyboard.press("Enter"); page.wait_for_timeout(1000)
        say(f"  set {label!r} = {target!r}")

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
        say(f"ABORT {name}/{table_name}: no 'Save and run ... in this view': {items[-4:]}")
        page.keyboard.press("Escape")
        return {"workbook_id": wid, "workbook_name": name, "table": table_name,
                "status": "aborted", "reason": "no_run_option"}
    lbl = ti.inner_text().strip().replace("\n", " ")
    ti.click(timeout=8000)
    page.wait_for_timeout(16000)
    st = page.evaluate("()=>{const t=document.body.innerText;"
                        "return (t.match(/\\d+% of table completed/)||['?'])[0];}")
    confirmed = False
    for _ in range(8):
        if clay_ui._find_header_rect(page, MARKER):
            confirmed = True
            break
        page.wait_for_timeout(2500)
    if not confirmed:
        say(f"UNCONFIRMED {name}/{table_name}: clicked {lbl!r} but {MARKER!r} column "
            f"never appeared | {st}")
        return {"workbook_id": wid, "workbook_name": name, "table": table_name,
                "status": "unconfirmed", "ran": lbl, "state": st}

    done, last_pct = _wait_for_full_completion(page, say, table_name)
    if not done:
        say(f"INCOMPLETE {name}/{table_name}: applied + {lbl!r} but still at "
            f"{last_pct}% after max wait - will resume on next run")
        return {"workbook_id": wid, "workbook_name": name, "table": table_name,
                "status": "incomplete", "ran": lbl, "state": last_pct}
    say(f"DONE {name}/{table_name}: applied + {lbl!r} | 100% of table completed")
    return {"workbook_id": wid, "workbook_name": name, "table": table_name,
            "status": "ok", "ran": lbl, "state": "100% of table completed"}


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Ad-hoc single workbook/table run")
    ap.add_argument("workbook_id")
    ap.add_argument("workbook_name")
    ap.add_argument("table_name")
    ap.add_argument("--recon", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--screenshot")
    a = ap.parse_args()
    entry = {"workbook_id": a.workbook_id, "workbook_name": a.workbook_name}
    with browser_session.clay_page(headless=not a.headed) as page:
        def say(m):
            print(m, flush=True)
        if a.recon:
            out = recon(page, entry, a.table_name, say, screenshot=a.screenshot)
        else:
            out = apply_findlinkedin(page, entry, a.table_name, a.dry_run, say)
        print("\nRESULT:", out)
