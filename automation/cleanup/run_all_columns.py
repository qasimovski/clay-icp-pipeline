"""Trigger a full run of an already-v1-applied Exhibitors_normalized: select all
rows -> Actions -> "Run N rows". This is a server-side batch run that persists
after the browser closes (unlike the apply-time 'Save and run', which didn't
stick when the worker navigated away). Skips workbooks that don't have v1."""

import os
import re
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
AUTO_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, os.path.join(AUTO_DIR, "clay_sync"))
sys.path.insert(0, os.path.join(AUTO_DIR, "build_automation"))

import clay_ui        # noqa: E402
import browser_session         # noqa: E402
import column_config as colcfg  # noqa: E402
import pipeline_config as pcfg  # noqa: E402

# Entity-driven (default exhibitors; override via CLAY_PIPELINE_ENTITY).
TABLE = pcfg.load().main_table
V1_SIGNATURE = "Official Domain"   # present only if v1 applied


def run_v1(page, entry, dry_run, say):
    wid = entry["workbook_id"]; name = entry["workbook_name"]
    clay_ui.open_workbook_by_id(page, wid)
    colcfg.focus_table(page, TABLE)
    page.wait_for_timeout(1000)

    if not clay_ui._find_header_rect(page, V1_SIGNATURE):
        say(f"SKIP {name}: v1 not applied (no {V1_SIGNATURE!r}) — nothing to run")
        return {"workbook_id": wid, "workbook_name": name, "status": "skip"}

    if dry_run:
        say(f"DRYRUN {name}: would select all rows -> Actions -> Run N rows")
        return {"workbook_id": wid, "workbook_name": name, "status": "dryrun"}

    page.keyboard.press("Escape"); page.wait_for_timeout(500)
    # select all rows
    page.get_by_role("checkbox").first.click(timeout=8000)
    page.wait_for_timeout(1200)
    page.get_by_role("button", name="Actions", exact=True).first.click(timeout=8000)
    page.wait_for_timeout(1200)
    ran = None
    for mi in page.get_by_role("menuitem").all():
        try:
            t = mi.inner_text().strip()
            if mi.is_visible() and re.match(r"Run [\d.,Kk]+ rows?", t):
                ran = t; mi.click(timeout=8000); break
        except Exception:
            pass
    if not ran:
        items = [m.inner_text().strip() for m in page.get_by_role("menuitem").all() if m.is_visible()]
        say(f"ABORT {name}: no 'Run N rows' action: {items[-6:]}")
        page.keyboard.press("Escape")
        return {"workbook_id": wid, "workbook_name": name, "status": "aborted",
                "reason": "no_run_action"}
    page.wait_for_timeout(1500)
    # confirm dialog if one appears
    for drole in ("alertdialog", "dialog"):
        d = page.get_by_role(drole)
        try:
            if d.count() and d.first.is_visible():
                for lbl in ("Run", "Confirm", "Yes", "Continue"):
                    b = d.first.get_by_role("button", name=lbl, exact=True)
                    if b.count() and b.last.is_visible():
                        b.last.click(timeout=6000); break
                break
        except Exception:
            pass
    # let the server-side run firmly initiate before we move on
    page.wait_for_timeout(9000)
    st = page.evaluate("()=>{const t=document.body.innerText;return {running:/Cells running/i.test(t),completed:(t.match(/\\d+% of table completed/)||[''])[0]};}")
    say(f"DONE {name}: triggered {ran!r} | {st}")
    return {"workbook_id": wid, "workbook_name": name, "status": "ok",
            "ran": ran, "state": st}
