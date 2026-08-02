"""Add the "Waterfall > WORK EMAIL" action to Speakers_normalized in one
workbook, gated on Speaker Name, and run it.

Replaces the template-based approach (apply_findworkemail_event.py). The saved
template "Email Waterfall and Validate Email" proved unreliable to apply — its
Configure panel needed four fields re-mapped on every application and silently
mis-mapped one of them — so the user's instruction (2026-07-27) is to add the
underlying actions directly instead:

  1. this script: Waterfall > WORK EMAIL  (card "TERRAPINN Find Work Email
     Waterfall", ~1.4 credits/row, by Jaimie Featherstone) with run condition
     !!{{Speaker Name}}, saved and run.
  2. then LeadMagic > Validate email, gated on !!{{WORK EMAIL}} — separate step.

Why this is simpler than the template: the card's inputs are ALREADY mapped
(recon on BioTrinity 2026-07-27 showed "Required inputs mapped for 9/9 data
providers": Full Name <- Enrich person > name, Company Domain <- Enrich person >
experience > [0] > company_domain, Social Profile URL <- LinkedIn URL, Company
Name <- Enrich person > org). The only thing to configure is the run condition,
which lives in a collapsed "Run settings" section.

The output column is named WORK EMAIL by the card itself (its panel title), so
{{WORK EMAIL}} in the next step's condition resolves without a rename.

  python add_workemail_waterfall.py <wid> <name> --recon
  python add_workemail_waterfall.py <wid> <name> --dry-run
  python add_workemail_waterfall.py <wid> <name>
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

TABLE = "Speakers_normalized"
# Card to open, and the column it creates (the card's own panel title).
CARD_MUST_CONTAIN = ("TERRAPINN", "Find Work Email Waterfall")
MARKER = "WORK EMAIL"
# Gate: only spend credits on rows that actually have a speaker name.
RUN_IF_COLUMN = "Speaker Name"

_PANEL = """()=>{
  const norm=s=>(s||'').replace(/\\s+/g,' ').trim();
  const out=[];
  for(const el of document.querySelectorAll('*')){
    if(el.children.length) continue;
    const t=norm(el.textContent);
    if(!t) continue;
    const r=el.getBoundingClientRect();
    if(r.width===0||r.x<1240||r.y<80||r.y>950) continue;
    out.push({t:t.slice(0,70), x:Math.round(r.x), y:Math.round(r.y)});
  }
  out.sort((a,b)=>a.y-b.y||a.x-b.x);
  return out;
}"""


def _panel_text(page):
    try:
        return [i["t"] for i in page.evaluate(_PANEL)]
    except Exception:
        return []


def _pct(page):
    t = page.evaluate("()=>document.body.innerText")
    m = re.search(r"(\d+)% of table completed", t)
    return int(m.group(1)) if m else None


def _wait_for_full_completion(page, say, max_wait_s=3600, poll_s=15):
    """Poll to 100%. Longer ceiling than the template pass: a waterfall walks up
    to 9 providers per row, so a 254-row table can outlast 30 minutes."""
    start = time.time()
    last = None
    while time.time() - start < max_wait_s:
        pct = _pct(page)
        if pct == 100:
            return True, 100
        if pct != last:
            say(f"   ...{TABLE} progress {pct}%")
            last = pct
        page.wait_for_timeout(poll_s * 1000)
    return False, last


# --------------------------------------------------------------------------
# canonical column-config helpers (fix_workemail_run_condition.py imports these)
# --------------------------------------------------------------------------

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
  for(const sw of document.querySelectorAll('[role="switch"]')){
    const r=sw.getBoundingClientRect();
    if(r.width===0||r.x<1240) continue;
    let label='';
    for(const p of document.querySelectorAll('*')){
      if(p.children.length) continue;
      const q=p.getBoundingClientRect();
      if(q.width===0||q.x>r.x||q.x<1240) continue;
      if(Math.abs((q.y+q.height/2)-(r.y+r.height/2))>12) continue;
      const t=norm(p.textContent);
      if(t) label=t.slice(0,50);
    }
    out.switches.push({label, state:sw.getAttribute('aria-checked')||
                       sw.getAttribute('data-state'), y:Math.round(r.y)});
  }
  for(const c of document.querySelectorAll('[role="checkbox"]')){
    const r=c.getBoundingClientRect();
    if(r.width===0||r.x<1240) continue;
    out.checkbox={state:c.getAttribute('aria-checked')||c.getAttribute('data-state'),
                  y:Math.round(r.y)};
  }
  // No y ceiling - the condition box sits below the fold until the panel
  // scrolls, and a ceiling made a gated column read as ungated.
  const eds=[...document.querySelectorAll('[contenteditable="true"]')]
    .filter(e=>{const r=e.getBoundingClientRect(); return r.x>1240&&r.width>100;})
    .map(e=>({y:e.getBoundingClientRect().y, t:norm(e.innerText)}))
    .sort((a,b)=>a.y-b.y);
  out.editables=eds.map(e=>e.t.slice(0,40));
  const filled=eds.find(e=>e.t.startsWith('!!'));
  out.condition = filled ? filled.t : null;
  return out;
}"""


def read_state(page):
    try:
        return page.evaluate(_STATE)
    except Exception as e:
        return {"error": str(e)[:120]}


def find_header_scrolling(page, column, tries=16):
    """Locate a column header, scrolling the grid right if needed.

    clay_ui._find_header_rect only sees the viewport, and columns added by a
    template land off-screen to the right — so a freshly created "Validate
    Email" reads as missing until the grid is scrolled to it.
    """
    # Poll first: a freshly created column can take several seconds to render,
    # and the grid draws its far-right columns lazily — checking once right
    # after focusing the table reports "column not found" for a column that is
    # plainly there a moment later.
    for _ in range(10):
        rect = clay_ui._find_header_rect(page, column)
        if rect:
            return rect
        page.wait_for_timeout(1200)
    for _ in range(tries):
        page.mouse.move(700, 400)
        page.mouse.wheel(900, 0)
        page.wait_for_timeout(500)
        rect = clay_ui._find_header_rect(page, column)
        if rect:
            return rect
    return None


def open_column_config(page, column):
    """Open a column's config panel via its header menu -> Edit column.

    Uses clay_ui.open_column_menu, which re-measures the header rect and
    verifies the menu actually opened — a single blind click on the chevron
    misses often enough to fail a batch ("'Edit column' menu item not found").
    """
    if not find_header_scrolling(page, column):
        raise colcfg.VerificationError(f"column {column!r} not found")
    clay_ui.open_column_menu(page, column)
    page.wait_for_timeout(800)
    for el in page.get_by_role("menuitem").all():
        try:
            if el.is_visible() and el.inner_text().strip() == "Edit column":
                el.click(timeout=8000)
                page.wait_for_timeout(3000)
                return True
        except Exception:
            pass
    raise colcfg.VerificationError("'Edit column' menu item not found")


def _condition_y(page):
    lab = page.get_by_text("Add run condition", exact=True)
    if lab.count():
        bb = lab.first.bounding_box()
        if bb:
            return bb["y"]
    return 10 ** 6


_SWITCH_ROWS = """()=>{
  const norm=s=>(s||'').replace(/\\s+/g,' ').trim();
  const leaves=[...document.querySelectorAll('*')].filter(e=>!e.children.length);
  const out=[];
  for(const sw of document.querySelectorAll('[role="switch"]')){
    const r=sw.getBoundingClientRect();
    if(r.width===0||r.x<1240) continue;
    const cy=r.y+r.height/2;
    let label='', bx=1e9;
    for(const p of leaves){
      const q=p.getBoundingClientRect();
      if(q.width===0||q.x<1250||q.x>1500) continue;
      if(Math.abs((q.y+q.height/2)-cy)>10) continue;
      if(q.x<bx){bx=q.x; label=norm(p.textContent).slice(0,50);}
    }
    out.push({label, state:sw.getAttribute('aria-checked')||
              sw.getAttribute('data-state'),
              x:Math.round(r.x+r.width/2), y:Math.round(cy)});
  }
  out.sort((a,b)=>a.y-b.y);
  return out;
}"""


def auto_update_off(page, say):
    """Turn the Auto-run switch OFF. User rule (2026-07-27): auto-run must
    ALWAYS be off - an action saved with it on starts running every row
    immediately, before any gate is in place.

    Targets the switch on the row LABELLED "Auto-run" only. An earlier version
    flipped every switch above the run-condition row, which on the LeadMagic
    card also turned off 'Only "Safe To Send" Emails?' - a setting the user
    never asked to change.
    """
    flipped = 0
    for row in page.evaluate(_SWITCH_ROWS):
        if not row["label"].startswith("Auto-run"):
            continue
        if row["state"] not in ("true", "checked"):
            say("  auto-run already off")
            return 0
        page.mouse.click(row["x"], row["y"])
        page.wait_for_timeout(1000)
        now = next((r["state"] for r in page.evaluate(_SWITCH_ROWS)
                    if r["label"].startswith("Auto-run")), None)
        if now in ("true", "checked"):
            raise colcfg.VerificationError("auto-run switch would not turn off")
        say("  auto-run turned OFF")
        flipped += 1
    if flipped == 0:
        say("  !! no 'Auto-run' switch row found")
    return flipped


_SAVE_BTN = """()=>{
  const norm=s=>(s||'').replace(/\\s+/g,' ').trim();
  for(const b of document.querySelectorAll('button')){
    const r=b.getBoundingClientRect();
    if(r.width===0||r.x<1500||r.y<800) continue;
    if(norm(b.textContent)==='Save')
      return {x:Math.round(r.x+r.width/2), y:Math.round(r.y+r.height/2),
              disabled:!!b.disabled};
  }
  return null;
}"""


def save_column(page, say):
    """Commit the open column config. With Auto-run OFF there is no split menu.

    Locates Save in JS and clicks by coordinate: enumerating Playwright button
    locators raced the panel re-render after toggling output fields, found no
    Save at all, and silently discarded a correct field selection.
    """
    for _ in range(4):
        pt = page.evaluate(_SAVE_BTN)
        if pt and not pt["disabled"]:
            page.mouse.click(pt["x"], pt["y"])
            page.wait_for_timeout(4000)
            return "save_click"
        page.wait_for_timeout(1200)
    try:
        colcfg.save_via_menu(page, r"Save and don'?t run")
        return "save_and_dont_run"
    except Exception as e:
        say(f"  !! could not save: {str(e)[:120]}")
        return "save_failed"


def verify_persisted(page, column, gate_column, say):
    """Reopen the column and report what ACTUALLY persisted. The pre-save panel
    lies: the first BioTrinity attempt showed a set condition, then saved with
    the checkbox unticked and no formula at all."""
    page.keyboard.press("Escape")
    page.wait_for_timeout(1500)
    # Reopening right after a save is flaky (the header menu can refuse to open
    # while the grid re-renders), so retry rather than lose the verification.
    last = None
    for attempt in range(4):
        try:
            open_column_config(page, column)
            break
        except Exception as e:
            last = e
            say(f"  reopen attempt {attempt+1} failed: {str(e)[:80]}")
            try:
                page.keyboard.press("Escape")
            except Exception:
                pass
            page.wait_for_timeout(2500)
    else:
        raise colcfg.VerificationError(f"could not reopen {column!r} to verify: {last}")
    _open_run_settings(page)
    st = read_state(page)
    # Exact match: a mangled formula like "!!{{!!{{WORK EMAIL}}_0tit6v...}}"
    # contains the column name but is not a working gate.
    cond_ok = _norm_cond(st.get("condition")) == _norm_cond(
        expected_condition(gate_column))
    cb_ok = (st.get("checkbox") or {}).get("state") in ("true", "checked")
    auto_off = all(sw.get("state") not in ("true", "checked")
                   for sw in st.get("switches", [])
                   if "Auto-run" in (sw.get("label") or ""))
    say(f"  PERSISTED: condition={st.get('condition')!r} checkbox={cb_ok} "
        f"auto_run_off={auto_off}")
    page.keyboard.press("Escape")
    return {"state": st, "condition_ok": cond_ok, "checkbox_ok": cb_ok,
            "auto_run_off": auto_off,
            "ok": cond_ok and cb_ok and auto_off}


def trigger_run(page, say):
    """Select all rows -> Actions -> 'Run N rows'. The reliable server-side
    trigger (see run_all_columns.py); rows failing the run condition are skipped
    by Clay, so this does not spend credits on ungated rows."""
    # Select all rows. The grid's header checkbox is the first role=checkbox, but
    # a plain locator click times out often enough to abort a batch (seen on Lab
    # of the Future Europe and Lab & Clearnroom Expo), so retry and fall back to
    # clicking its coordinates.
    selected = False
    for attempt in range(3):
        try:
            page.get_by_role("checkbox").first.click(timeout=8000)
            selected = True
            break
        except Exception:
            try:
                bb = page.get_by_role("checkbox").first.bounding_box()
                if bb:
                    page.mouse.click(bb["x"] + bb["width"] / 2,
                                     bb["y"] + bb["height"] / 2)
                    selected = True
                    break
            except Exception:
                pass
            page.wait_for_timeout(1500)
    if not selected:
        say("  !! could not select all rows")
        return None
    page.wait_for_timeout(1200)
    for attempt in range(3):
        try:
            page.get_by_role("button", name="Actions", exact=True).first.click(
                timeout=8000)
            break
        except Exception:
            page.wait_for_timeout(1500)
    page.wait_for_timeout(1500)
    for mi in page.get_by_role("menuitem").all():
        try:
            t = mi.inner_text().strip()
            if mi.is_visible() and re.match(r"Run [\d.,Kk]+ rows?", t):
                mi.click(timeout=8000)
                page.wait_for_timeout(4000)
                say(f"  triggered: {t!r}")
                return t
        except Exception:
            pass
    return None


def column_progress(page, column):
    """The COLUMN's own status cell (not the table-wide '% of table completed'
    text, which is what made an unfinished run look complete)."""
    try:
        return colcfg.column_status(page, column)
    except Exception:
        return None


def _open_run_settings(page):
    """Expand the collapsed 'Run settings' section so the run-condition
    checkbox is in the DOM (it isn't rendered while collapsed)."""
    for el in page.get_by_role("button").all():
        try:
            if not el.is_visible():
                continue
            bb = el.bounding_box()
            if not bb or bb["x"] < 1240:
                continue
            if el.inner_text().strip() == "Run settings":
                exp = el.get_attribute("aria-expanded")
                if exp == "false" or exp is None:
                    el.click(timeout=8000)
                    page.wait_for_timeout(1500)
                return True
        except Exception:
            pass
    return False


_FIND_TOKEN_OPT = """(txt)=>{
  const norm=s=>(s||'').replace(/\\s+/g,' ').trim();
  const leaves=[...document.querySelectorAll('*')].filter(e=>!e.children.length);
  for(const el of leaves){
    const r=el.getBoundingClientRect();
    if(r.width===0||r.x<1240||r.y<120||r.y>950) continue;
    if(norm(el.textContent)===txt)
      return {x:Math.round(r.x+r.width/2), y:Math.round(r.y+r.height/2)};
  }
  return null;
}"""

# Find the run-condition editor by anchoring to the "Add run condition" label:
# the condition box is the first contenteditable BELOW it. Picking "the last
# editable in the panel" with a y<935 ceiling silently selected a different box
# (the real one sits at y~973 until the panel scrolls), so the formula was typed
# into the wrong field and the column saved ungated.
_COND_EDITORS = """(labelY)=>{
  const norm=s=>(s||'').replace(/\\s+/g,' ').trim();
  const out=[];
  document.querySelectorAll('[contenteditable="true"]').forEach((el)=>{
    const r=el.getBoundingClientRect();
    if(r.x>1240&&r.width>100)
      out.push({y:Math.round(r.y), cy:Math.round(r.y+r.height/2),
                x:Math.round(r.x), text:norm(el.innerText)});
  });
  out.sort((a,b)=>a.y-b.y);
  // Priority: the empty formula box (its placeholder), then a box already
  // holding a formula, then position. A Run-settings section can also contain a
  // DESCRIPTION editable ("Only run if output from WORK EMAIL column is not
  // empty") that sits above the formula and would otherwise win on position —
  // typing the gate into it leaves the real formula untouched.
  const byPlaceholder=out.find(e=>e.text.startsWith('E.g., !!'));
  const byFormula=out.find(e=>e.text.startsWith('!!'));
  const below=out.filter(e=>e.y>labelY-5);
  return {all:out, pick:(byPlaceholder||byFormula||below[0]||out[out.length-1]||null)};
}"""


def _cond_pick(page, label_y):
    try:
        return (page.evaluate(_COND_EDITORS, label_y) or {}).get("pick")
    except Exception:
        return None


def expected_condition(column):
    return "!!{{" + column + "}}"


def _norm_cond(t):
    return (t or "").replace(" ", "").strip()


_CLEAR_COND = """(labelY)=>{
  const norm=s=>(s||'').replace(/\s+/g,' ').trim();
  const eds=[...document.querySelectorAll('[contenteditable="true"]')]
    .filter(e=>{const r=e.getBoundingClientRect(); return r.x>1240&&r.width>100;})
    .map(e=>({el:e, y:e.getBoundingClientRect().y, text:norm(e.innerText)}))
    .sort((a,b)=>a.y-b.y);
  const pick = eds.find(e=>e.text.startsWith('E.g., !!'))
            || eds.find(e=>e.text.startsWith('!!'))
            || eds.filter(e=>e.y>labelY-5)[0]
            || eds[eds.length-1];
  if(!pick) return 'no-editor';
  pick.el.focus();
  const sel=window.getSelection();
  const range=document.createRange();
  range.selectNodeContents(pick.el);
  sel.removeAllRanges(); sel.addRange(range);
  document.execCommand('delete');
  pick.el.dispatchEvent(new InputEvent('input',{bubbles:true}));
  return norm(pick.el.innerText);
}"""


def _condition_checkbox(page):
    """The checkbox on the 'Add run condition' row."""
    lab = page.get_by_text("Add run condition", exact=True)
    if not lab.count():
        return None, None
    lb = lab.first.bounding_box()
    for el in page.locator('[role="checkbox"]').all():
        try:
            bb = el.bounding_box()
        except Exception:
            continue
        if bb and bb["x"] > 1240 and abs((bb["y"] + bb["height"] / 2) -
                                        (lb["y"] + lb["height"] / 2)) < 12:
            return el, lb
    return None, lb


def _reset_condition(page, say):
    """Untick + retick 'Add run condition' so the box comes back empty."""
    cb, _ = _condition_checkbox(page)
    if cb is None:
        return False
    try:
        cb.click(timeout=8000)          # off — discards the formula
        page.wait_for_timeout(1200)
        cb2, _ = _condition_checkbox(page)
        (cb2 or cb).click(timeout=8000)  # back on — empty box
        page.wait_for_timeout(1400)
        say("  reset the run-condition box")
        return True
    except Exception as e:
        say(f"  reset failed: {str(e)[:100]}")
        return False


def _clear_editor(page, label_y, say):
    """Empty the formula box via the DOM.

    Keyboard clearing was unreliable: Ctrl+A/Delete left token content behind,
    and hammering Backspace past the start of the box destroyed the editor
    entirely ("lost the editor after clearing"). Selecting the node contents and
    deleting is deterministic.
    """
    for attempt in range(3):
        try:
            left = page.evaluate(_CLEAR_COND, label_y)
        except Exception as e:
            say(f"  clear failed: {str(e)[:80]}")
            return False
        page.wait_for_timeout(600)
        if left in ("", "no-editor") or str(left).startswith("E.g.,"):
            return left != "no-editor"
        say(f"  clear attempt {attempt+1}: box still reads {str(left)[:50]!r}")
    return False


_SCROLL_COND = """(labelY)=>{
  const norm=s=>(s||'').replace(/\\s+/g,' ').trim();
  const eds=[...document.querySelectorAll('[contenteditable="true"]')]
    .filter(e=>{const r=e.getBoundingClientRect(); return r.x>1240&&r.width>100;})
    .map(e=>({el:e, y:e.getBoundingClientRect().y, text:norm(e.innerText)}))
    .sort((a,b)=>a.y-b.y);
  if(!eds.length) return false;
  const pick = eds.find(e=>e.text.startsWith('E.g., !!'))
            || eds.find(e=>e.text.startsWith('!!'))
            || eds.filter(e=>e.y>labelY-5)[0]
            || eds[eds.length-1];
  if(!pick) return false;
  pick.el.scrollIntoView({block:'center'});
  return true;
}"""


def _focus_condition_editor(page, label_y, say):
    """Click the run-condition formula box, scrolling the panel if it is below
    the fold. Returns the pick dict, or None."""
    for attempt in range(5):
        pick = _cond_pick(page, label_y)
        if not pick:
            return None
        if pick["cy"] > 900:
            # JS scroll is reliable here; the mouse wheel often scrolled the
            # grid behind the panel instead of the panel itself.
            try:
                page.evaluate(_SCROLL_COND, label_y)
            except Exception:
                pass
            page.wait_for_timeout(900)
            continue
        page.mouse.click(pick["x"] + 40, pick["cy"])
        page.wait_for_timeout(700)
        return pick
    return None


def set_run_condition(page, column, say):
    """Tick 'Add run condition' and put !!{{column}} in the formula box.

    Types the formula literally (the "/" menu picks the wrong row) and verifies
    by reading the box back. Everything is coordinate-driven: mixing a filtered
    JS list with locator .nth() indices addressed the wrong element entirely.
    """
    lab = page.get_by_text("Add run condition", exact=True)
    if not lab.count():
        raise colcfg.VerificationError("run condition label not visible")
    lb = lab.first.bounding_box()
    cb = None
    for el in page.locator('[role="checkbox"]').all():
        try:
            bb = el.bounding_box()
        except Exception:
            continue
        if bb and bb["x"] > 1240 and abs((bb["y"] + bb["height"] / 2) -
                                        (lb["y"] + lb["height"] / 2)) < 12:
            cb = el
            break
    if cb is None:
        raise colcfg.VerificationError("run condition checkbox not found")
    st = cb.get_attribute("aria-checked") or cb.get_attribute("data-state")
    if st not in ("true", "checked"):
        cb.click(timeout=8000)
        page.wait_for_timeout(1400)

    label_y = (lb or {}).get("y", 0)
    pick = _cond_pick(page, label_y)
    if not pick:
        raise colcfg.VerificationError("condition editor not found")
    say(f"  condition editor: {{'y': {pick['y']}, 'text': {pick['text'][:40]!r}}}")
    existing = pick["text"]
    # Only a real formula counts as already set; descriptive text that mentions
    # the column is not a gate.
    # Exact match only — a mangled formula that merely contains the column name
    # must be cleared and retyped, not accepted.
    if _norm_cond(existing) == _norm_cond(expected_condition(column)):
        say(f"  run condition already set: {existing[:60]!r}")
        return existing

    want = expected_condition(column)
    for attempt in range(3):
        if not _focus_condition_editor(page, label_y, say):
            raise colcfg.VerificationError("could not focus the condition editor")
        cur = (_cond_pick(page, label_y) or {}).get("text", "")
        if cur and not cur.startswith("E.g.,"):
            # Untick/retick rather than editing the DOM: only the checkbox
            # actually clears the app's stored formula.
            if not _reset_condition(page, say):
                say("  !! could not reset the existing formula")
                continue
            if not _focus_condition_editor(page, label_y, say):
                say("  !! lost the editor after reset")
                continue
        page.keyboard.type(want, delay=45)
        page.wait_for_timeout(1600)
        page.keyboard.press("Tab")        # commit: Save from a focused editor drops it
        page.wait_for_timeout(1200)
        got = (_cond_pick(page, label_y) or {}).get("text", "")
        if _norm_cond(got) == _norm_cond(want):
            return got
        say(f"  condition attempt {attempt+1}: got {got[:60]!r}, want {want!r}")
    raise colcfg.VerificationError(f"condition never became {want!r}")


def _verify_card(page):
    """Confirm the open panel is the WORK EMAIL waterfall with inputs mapped."""
    txt = _panel_text(page)
    joined = " | ".join(txt)
    return {
        "is_work_email": MARKER in txt,
        "inputs_mapped": bool(re.search(r"Required inputs mapped for \d+/\d+",
                                        joined)),
        "providers": next((t for t in txt if "providers configured" in t), None),
    }


def repair_run_condition(page, column, gate_column, say):
    """Set Auto-run OFF + !!{{gate_column}} on an EXISTING column, then verify.

    Generalised from fix_workemail_run_condition.py: any save path can drop a run
    condition (the field-picker's 'Save' does), so every step needs a way to put
    the gate back and prove it stuck.
    """
    open_column_config(page, column)
    _open_run_settings(page)
    auto_update_off(page, say)
    set_run_condition(page, gate_column, say)
    saved = save_column(page, say)
    say(f"  gate repair saved via {saved}")
    page.wait_for_timeout(2500)
    return verify_persisted(page, column, gate_column, say)


def add_waterfall(page, entry, dry_run, say, recon=False, run_after=False):
    wid = entry["workbook_id"]
    name = entry["workbook_name"]
    clay_ui.open_workbook_by_id(page, wid)

    if not colcfg.table_exists(page, TABLE):
        say(f"SKIP {name}/{TABLE}: table does not exist")
        return {"workbook_id": wid, "workbook_name": name, "table": TABLE,
                "status": "no_table"}

    colcfg.focus_table_maybe_empty(page, TABLE)
    page.wait_for_timeout(800)

    if not clay_ui._find_header_rect(page, RUN_IF_COLUMN):
        say(f"SKIP {name}/{TABLE}: no {RUN_IF_COLUMN!r} column to gate on")
        return {"workbook_id": wid, "workbook_name": name, "table": TABLE,
                "status": "no_gate_column"}

    if clay_ui._find_header_rect(page, MARKER) and not recon:
        # Already built (possibly by the user by hand). Don't rebuild and don't
        # wait on the table-wide "% of table completed" text — it is not this
        # column's progress.
        say(f"SKIP {name}/{TABLE}: {MARKER!r} column already present "
            f"(column status {column_progress(page, MARKER)!r})")
        return {"workbook_id": wid, "workbook_name": name, "table": TABLE,
                "status": "exists", "note": "already_present",
                "column_status": column_progress(page, MARKER)}

    colcfg.open_card(page, "Waterfall", must_contain=CARD_MUST_CONTAIN,
                must_not=("Create a column",))
    page.wait_for_timeout(2500)

    card = _verify_card(page)
    say(f"  card: {card}")
    if not card["is_work_email"]:
        say(f"ABORT {name}/{TABLE}: opened panel is not the {MARKER!r} waterfall")
        page.keyboard.press("Escape")
        return {"workbook_id": wid, "workbook_name": name, "table": TABLE,
                "status": "aborted", "reason": "wrong_card", "card": card}

    if recon:
        say(f"RECON {name}/{TABLE}: panel contents")
        for t in _panel_text(page):
            say(f"    {t!r}")
        _open_run_settings(page)
        say("  --- after expanding Run settings ---")
        for t in _panel_text(page):
            say(f"    {t!r}")
        page.keyboard.press("Escape")
        return {"workbook_id": wid, "workbook_name": name, "table": TABLE,
                "status": "recon", "card": card}

    if not _open_run_settings(page):
        say(f"ABORT {name}/{TABLE}: 'Run settings' section not found")
        page.keyboard.press("Escape")
        return {"workbook_id": wid, "workbook_name": name, "table": TABLE,
                "status": "aborted", "reason": "no_run_settings"}

    # !!{{Speaker Name}} — column_config types the "!!" then inserts /<column>.
    try:
        cond = set_run_condition(page, RUN_IF_COLUMN, say)
    except Exception as e:
        say(f"ABORT {name}/{TABLE}: run condition failed: {str(e)[:160]}")
        page.keyboard.press("Escape")
        return {"workbook_id": wid, "workbook_name": name, "table": TABLE,
                "status": "aborted", "reason": "run_condition",
                "error": str(e)[:200]}
    say(f"  run condition set: !!{{{{{RUN_IF_COLUMN}}}}}")

    if dry_run:
        say(f"DRYRUN {name}/{TABLE}: card ok, condition {cond}; not saving")
        page.keyboard.press("Escape")
        return {"workbook_id": wid, "workbook_name": name, "table": TABLE,
                "status": "dryrun", "card": card}

    # Auto-run OFF before saving. Saving with it on made the very first attempt
    # start running every row immediately — ungated, because the condition was
    # silently dropped by the same save.
    auto_update_off(page, say)

    saved = save_column(page, say)
    say(f"  saved via {saved}")

    # Confirm the real column appeared — never trust the page's status text.
    confirmed = None
    for _ in range(10):
        if clay_ui._find_header_rect(page, MARKER):
            confirmed = MARKER
            break
        page.wait_for_timeout(2500)
    if not confirmed:
        say(f"UNCONFIRMED {name}/{TABLE}: saved but no {MARKER!r} column appeared")
        return {"workbook_id": wid, "workbook_name": name, "table": TABLE,
                "status": "unconfirmed", "saved": saved}

    # The gate is only real if reopening the column shows it.
    v = verify_persisted(page, MARKER, RUN_IF_COLUMN, say)
    if not v["ok"]:
        say(f"ABORT {name}/{TABLE}: gate did not persist — NOT running. {v}")
        return {"workbook_id": wid, "workbook_name": name, "table": TABLE,
                "status": "gate_not_persisted", "saved": saved, "verify": v}

    if not run_after:
        say(f"READY {name}/{TABLE}: {MARKER} added, gated on !!{{{{{RUN_IF_COLUMN}}}}}, "
            f"auto-run off, NOT run (pass --run to run it)")
        return {"workbook_id": wid, "workbook_name": name, "table": TABLE,
                "status": "ready", "saved": saved, "verify": v}

    trig = trigger_run(page, say)
    if not trig:
        say(f"ABORT {name}/{TABLE}: could not trigger 'Run N rows'")
        return {"workbook_id": wid, "workbook_name": name, "table": TABLE,
                "status": "run_not_triggered", "saved": saved, "verify": v}
    # Progress is reported per column; the table-wide "% of table completed"
    # string is NOT this column's progress and previously made a 4%-run column
    # look finished.
    say(f"  column status now: {column_progress(page, MARKER)!r}")
    return {"workbook_id": wid, "workbook_name": name, "table": TABLE,
            "status": "running", "saved": saved, "verify": v, "ran": trig,
            "column_status": column_progress(page, MARKER)}


def run_only(page, entry, say):
    """Trigger the run for an already-configured, already-gated column.

    Re-verifies the gate first: triggering a run on an ungated column is exactly
    the mistake that spent credits on rows without a speaker name.
    """
    wid = entry["workbook_id"]
    name = entry["workbook_name"]
    clay_ui.open_workbook_by_id(page, wid)
    colcfg.focus_table_maybe_empty(page, TABLE)
    page.wait_for_timeout(1000)

    if not clay_ui._find_header_rect(page, MARKER):
        say(f"SKIP {name}/{TABLE}: no {MARKER!r} column")
        return {"workbook_id": wid, "workbook_name": name, "status": "no_column"}

    open_column_config(page, MARKER)
    _open_run_settings(page)
    st = read_state(page)
    cond_ok = RUN_IF_COLUMN in (st.get("condition") or "")
    auto_off = all(sw.get("state") not in ("true", "checked")
                   for sw in st.get("switches", [])
                   if "Auto-run" in (sw.get("label") or ""))
    say(f"  gate check: condition={st.get('condition')!r} auto_run_off={auto_off}")
    page.keyboard.press("Escape")
    page.wait_for_timeout(1200)
    if not (cond_ok and auto_off):
        say(f"ABORT {name}/{TABLE}: gate not in place — refusing to run")
        return {"workbook_id": wid, "workbook_name": name,
                "status": "gate_missing", "state": st}

    trig = trigger_run(page, say)
    if not trig:
        say(f"ABORT {name}/{TABLE}: could not trigger 'Run N rows'")
        return {"workbook_id": wid, "workbook_name": name,
                "status": "run_not_triggered"}
    return {"workbook_id": wid, "workbook_name": name, "status": "running",
            "ran": trig, "column_status": column_progress(page, MARKER)}


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Add Waterfall > WORK EMAIL to one workbook")
    ap.add_argument("workbook_id")
    ap.add_argument("workbook_name")
    ap.add_argument("--recon", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--run", action="store_true",
                    help="trigger the run after the gate is verified")
    ap.add_argument("--run-only", dest="run_only", action="store_true",
                    help="only trigger the run on an already-gated column")
    a = ap.parse_args()
    entry = {"workbook_id": a.workbook_id, "workbook_name": a.workbook_name}
    with browser_session.clay_page(headless=not a.headed) as page:
        def say(m):
            print(m, flush=True)
        if a.run_only:
            out = run_only(page, entry, say)
        else:
            out = add_waterfall(page, entry, a.dry_run, say, recon=a.recon,
                                run_after=a.run)
        print("\nRESULT:", out)
