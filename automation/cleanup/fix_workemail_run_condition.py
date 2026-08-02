"""Repair an existing WORK EMAIL waterfall column: Auto-run OFF + run condition
!!{{Speaker Name}}, then VERIFY by reopening the column config.

Why this exists: the first BioTrinity run (2026-07-27) saved the column with
Auto-run still ON and no run condition, despite the pre-save panel reporting the
condition was set — and the pass reported success because it trusted the page's
"% of table completed" text, which is a TABLE-wide metric, not the column's own
progress. Two rules came out of that:

  * Auto-run must always be turned OFF before saving an action column.
  * A saved setting is only real if reopening the column shows it. Never trust
    the pre-save panel or any page status string.

  python fix_workemail_run_condition.py <wid> <name> --inspect     # read-only report
  python fix_workemail_run_condition.py <wid> <name>               # repair + verify
"""

import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
AUTO_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, SCRIPT_DIR)
sys.path.insert(0, os.path.join(AUTO_DIR, "clay_sync"))
sys.path.insert(0, os.path.join(AUTO_DIR, "build_automation"))

import clay_ui        # noqa: E402
import browser_session         # noqa: E402
import column_config as colcfg  # noqa: E402
import add_workemail_waterfall as panel  # noqa: E402

TABLE = "Speakers_normalized"
COL = "WORK EMAIL"
GATE_COLUMN = "Speaker Name"

_STATE = """()=>{
  const norm=s=>(s||'').replace(/\\s+/g,' ').trim();
  const out={switches:[], condition:null, checkbox:null, panel:false};
  for(const el of document.querySelectorAll('*')){
    if(el.children.length) continue;
    if(norm(el.textContent)==='Run settings'){
      const r=el.getBoundingClientRect();
      if(r.x>1240) out.panel=true;
    }
  }
  for(const s of document.querySelectorAll('[role="switch"]')){
    const r=s.getBoundingClientRect();
    if(r.width===0||r.x<1240) continue;
    // label = nearest text to the LEFT on the same row
    let label='';
    for(const p of document.querySelectorAll('*')){
      if(p.children.length) continue;
      const q=p.getBoundingClientRect();
      if(q.width===0||q.x>r.x||q.x<1240) continue;
      if(Math.abs((q.y+q.height/2)-(r.y+r.height/2))>12) continue;
      const t=norm(p.textContent);
      if(t) label=t.slice(0,50);
    }
    out.switches.push({label, state:s.getAttribute('aria-checked')||
                       s.getAttribute('data-state'), y:Math.round(r.y)});
  }
  for(const c of document.querySelectorAll('[role="checkbox"]')){
    const r=c.getBoundingClientRect();
    if(r.width===0||r.x<1240) continue;
    out.checkbox={state:c.getAttribute('aria-checked')||c.getAttribute('data-state'),
                  y:Math.round(r.y)};
  }
  // No y ceiling: the condition box sits below the fold (y~970+) until the
  // panel scrolls, and an earlier version of this reader missed it and reported
  // condition=None for a column that actually had one.
  const eds=[...document.querySelectorAll('[contenteditable="true"]')]
    .filter(e=>{const r=e.getBoundingClientRect(); return r.x>1240&&r.width>100;})
    .map(e=>({y:e.getBoundingClientRect().y, t:norm(e.innerText)}))
    .sort((a,b)=>a.y-b.y);
  out.editables=eds.map(e=>e.t.slice(0,40));
  const ph=eds.find(e=>e.t.startsWith('E.g., !!'));
  const filled=eds.find(e=>e.t.startsWith('!!'));
  out.condition = filled ? filled.t : (ph ? null : (eds.length ? eds[eds.length-1].t : null));
  return out;
}"""


def open_column_config(page, column):
    """Open a column's config via its header menu -> Edit column."""
    rect = clay_ui._find_header_rect(page, column)
    if not rect:
        raise colcfg.GateError(f"column {column!r} not found")
    w = rect.get("w") or rect.get("width") or 80
    page.mouse.click(rect["x"] + w - 12, rect["y"] + 12)
    page.wait_for_timeout(1500)
    for el in page.get_by_role("menuitem").all():
        try:
            if el.is_visible() and el.inner_text().strip() == "Edit column":
                el.click(timeout=8000)
                page.wait_for_timeout(3000)
                return True
        except Exception:
            pass
    raise colcfg.GateError("'Edit column' menu item not found")


def read_state(page):
    try:
        return page.evaluate(_STATE)
    except Exception as e:
        return {"error": str(e)[:120]}


def _auto_run_off(page, say):
    """Turn the Auto-run switch OFF (user rule: always off)."""
    flipped = 0
    for s in page.locator('[role="switch"]').all():
        try:
            bb = s.bounding_box()
        except Exception:
            continue
        if not bb or bb["x"] < 1240:
            continue
        st = s.get_attribute("aria-checked") or s.get_attribute("data-state")
        # Only the Auto-run switch sits above the run-condition checkbox; the
        # "shorten outputs" switch below it must be left alone.
        label_ok = bb["y"] < _condition_y(page)
        if label_ok and st in ("true", "checked"):
            s.click(timeout=8000)
            page.wait_for_timeout(900)
            now = s.get_attribute("aria-checked") or s.get_attribute("data-state")
            if now in ("true", "checked"):
                raise colcfg.GateError("auto-run switch would not turn off")
            flipped += 1
            say("  auto-run turned OFF")
    return flipped


def _condition_y(page):
    lab = page.get_by_text("Add run condition", exact=True)
    if lab.count():
        bb = lab.first.bounding_box()
        if bb:
            return bb["y"]
    return 10 ** 6


def repair(page, entry, say, inspect=False):
    wid = entry["workbook_id"]
    name = entry["workbook_name"]
    clay_ui.open_workbook_by_id(page, wid)
    colcfg.focus_table_maybe_empty(page, TABLE)
    page.wait_for_timeout(1000)

    open_column_config(page, COL)
    panel._open_run_settings(page)
    before = read_state(page)
    say(f"  BEFORE: {before}")

    if inspect:
        page.keyboard.press("Escape")
        return {"workbook_id": wid, "workbook_name": name, "status": "inspect",
                "before": before}

    _auto_run_off(page, say)
    panel._set_gate_condition(page, GATE_COLUMN, say)
    after_edit = read_state(page)
    say(f"  AFTER EDIT: {after_edit}")

    # Save WITHOUT running — the user re-runs deliberately once the gate is
    # proven. With Auto-run OFF the Save button commits directly (there is no
    # "Save and don't run" split menu in that state).
    saved = None
    for el in page.get_by_role("button").all():
        try:
            if not el.is_visible():
                continue
            bb = el.bounding_box()
            if not bb or bb["x"] < 1500 or bb["y"] < 820:
                continue
            if el.inner_text().strip() == "Save":
                page.mouse.click(bb["x"] + bb["width"] / 2,
                                 bb["y"] + bb["height"] / 2)
                page.wait_for_timeout(4000)
                saved = "save_click"
                break
        except Exception:
            pass
    if saved is None:
        try:
            colcfg.save_via_menu(page, r"Save and don'?t run")
            saved = "save_and_dont_run"
        except Exception as e:
            say(f"  !! could not save: {str(e)[:120]}")
            saved = "save_failed"
    say(f"  saved via {saved}")
    page.wait_for_timeout(3000)

    # The whole point: verify what PERSISTED, by reopening the column.
    page.keyboard.press("Escape")
    page.wait_for_timeout(1500)
    open_column_config(page, COL)
    panel._open_run_settings(page)
    persisted = read_state(page)
    say(f"  PERSISTED: {persisted}")
    page.keyboard.press("Escape")

    cond_ok = GATE_COLUMN in (persisted.get("condition") or "")
    cb_ok = (persisted.get("checkbox") or {}).get("state") in ("true", "checked")
    auto_off = all(s.get("state") not in ("true", "checked")
                   for s in persisted.get("switches", [])
                   if "Auto-run" in (s.get("label") or ""))
    ok = cond_ok and cb_ok and auto_off
    say(f"  verdict: condition={cond_ok} checkbox={cb_ok} auto_run_off={auto_off}")
    return {"workbook_id": wid, "workbook_name": name,
            "status": "ok" if ok else "failed", "saved": saved,
            "before": before, "persisted": persisted,
            "condition_ok": cond_ok, "auto_run_off": auto_off}


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("workbook_id")
    ap.add_argument("workbook_name")
    ap.add_argument("--inspect", action="store_true")
    ap.add_argument("--headed", action="store_true")
    a = ap.parse_args()
    entry = {"workbook_id": a.workbook_id, "workbook_name": a.workbook_name}
    with browser_session.clay_page(headless=not a.headed) as page:
        def say(m):
            print(m, flush=True)
        print("\nRESULT:", repair(page, entry, say, inspect=a.inspect))
