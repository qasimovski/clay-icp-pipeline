"""Add LeadMagic > "Validate email" to Speakers_normalized in one workbook,
mapped to the WORK EMAIL column, gated on !!{{WORK EMAIL}}, keeping only the
Status / Mx Record / Mx Provider output fields.

Step 2 of the user's 2026-07-27 instruction; step 1 is
add_workemail_waterfall_event.py (Waterfall > WORK EMAIL). Run this only after
the waterfall has finished, since the gate and the Person's Email mapping both
reference the column it produces.

  python add_validate_email_event.py <wid> <name> --recon
  python add_validate_email_event.py <wid> <name> --dry-run
  python add_validate_email_event.py <wid> <name>
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
import common         # noqa: E402
import build_lib as B  # noqa: E402
import add_workemail_waterfall_event as W  # noqa: E402  (shared helpers)

TABLE = "Speakers_normalized"
CARD_MUST_CONTAIN = ("Validate email", "LeadMagic")
SOURCE_COLUMN = "WORK EMAIL"        # what Person's Email maps to, and the gate
EMAIL_FIELD = "Person's Email"
KEEP_FIELDS = ("Status", "Mx Record", "Mx Provider")
# Columns that prove this action is already on the table.
SIG_CANDIDATES = ("Mx Record", "Mx Provider", "Validate email")


def _sig_present(page):
    for n in SIG_CANDIDATES:
        if clay_ui._find_header_rect(page, n):
            return n
    return None


def _dump(page, say, note):
    say(f"  --- panel ({note}) ---")
    for t in W._panel_text(page):
        say(f"    {t!r}")


def _buttons(page):
    out = []
    for el in page.get_by_role("button").all():
        try:
            if not el.is_visible():
                continue
            bb = el.bounding_box()
            if not bb or bb["x"] < 1240:
                continue
            out.append((el.inner_text().strip().replace("\n", " ")[:70],
                        round(bb["x"]), round(bb["y"])))
        except Exception:
            pass
    return out


_FIELD_ROWS = """()=>{
  const norm=s=>(s||'').replace(/\s+/g,' ').trim();
  const out=[];
  for(const el of document.querySelectorAll('[role="checkbox"],input[type="checkbox"],[role="switch"]')){
    const r=el.getBoundingClientRect();
    if(r.width===0||r.x<1240) continue;
    // nearest text to the right of the control is its field name
    let label='';
    for(const p of document.querySelectorAll('*')){
      if(p.children.length) continue;
      const q=p.getBoundingClientRect();
      if(q.width===0||q.x<r.x||q.x>r.x+320) continue;
      if(Math.abs((q.y+q.height/2)-(r.y+r.height/2))>10) continue;
      const t=norm(p.textContent);
      if(t){label=t.slice(0,40);break;}
    }
    out.push({label, state:el.getAttribute('aria-checked')||el.getAttribute('data-state')||el.checked,
              x:Math.round(r.x), y:Math.round(r.y)});
  }
  out.sort((a,b)=>a.y-b.y);
  return out;
}"""


def _field_checkboxes(page):
    try:
        return page.evaluate(_FIELD_ROWS)
    except Exception as e:
        return [{"error": str(e)[:80]}]


def _continue_button(page):
    for el in page.get_by_role("button").all():
        try:
            if not el.is_visible():
                continue
            bb = el.bounding_box()
            if not bb or bb["x"] < 1240:
                continue
            if "Continue to add fields" in el.inner_text():
                return el
        except Exception:
            pass
    return None


# Output fields this action can emit, in panel order. Toggling is driven from
# these NAMES (rendered at x~1285) and each row's switch is found on the same
# row - never the reverse. Position alone cannot separate the field toggles
# (x~1672) from the Run-settings switches (Auto-run, x~1659).
ALL_OUTPUT_FIELDS = ("Status", "Sub Status", "Email", "Mx Record", "Mx Provider")

_FIELD_TOGGLES = """(names)=>{
  const norm=s=>(s||'').replace(/\\s+/g,' ').trim();
  const leaves=[...document.querySelectorAll('*')].filter(e=>!e.children.length);
  const switches=[];
  for(const sw of document.querySelectorAll('[role="switch"]')){
    const r=sw.getBoundingClientRect();
    if(r.width===0||r.x<1600) continue;
    switches.push({el:sw, cy:r.y+r.height/2,
                   x:Math.round(r.x+r.width/2),
                   state:sw.getAttribute('aria-checked')||sw.getAttribute('data-state')});
  }
  const out=[];
  for(const p of leaves){
    const q=p.getBoundingClientRect();
    if(q.width===0||q.x<1250||q.x>1340) continue;
    const t=norm(p.textContent);
    if(!names.includes(t)) continue;
    const cy=q.y+q.height/2;
    const sw=switches.find(s=>Math.abs(s.cy-cy)<=12);
    if(sw) out.push({label:t, state:sw.state, x:sw.x, y:Math.round(sw.cy)});
  }
  out.sort((a,b)=>a.y-b.y);
  return out;
}"""


def read_output_fields(page):
    return page.evaluate(_FIELD_TOGGLES, list(ALL_OUTPUT_FIELDS))


def set_output_fields(page, keep, say, needed=None):
    """Drive the picker toward `keep`, tolerating rows that aren't listed.

    A field already added as a column no longer appears as a toggle, so a
    missing row is normal, not an error. Returns True if a toggle was changed
    (i.e. the selection needs committing).
    """
    changed = False
    for attempt in range(3):
        rows = page.evaluate(_FIELD_TOGGLES, list(ALL_OUTPUT_FIELDS))
        if not rows:
            say("  no toggle rows offered (all wanted fields already columns?)")
            return changed
        wrong = [r for r in rows
                 if (r["label"] in keep) != (r["state"] in ("true", "checked"))]
        if not wrong:
            on = [r["label"] for r in rows if r["state"] in ("true", "checked")]
            say(f"  toggles now: ON={on} "
                f"(rows offered: {[r['label'] for r in rows]})")
            return changed
        for r in wrong:
            say(f"  toggling {r['label']!r} (was {r['state']})")
            page.mouse.click(r["x"], r["y"])
            page.wait_for_timeout(900)
            changed = True
    rows = page.evaluate(_FIELD_TOGGLES, list(ALL_OUTPUT_FIELDS))
    on = [r["label"] for r in rows if r["state"] in ("true", "checked")]
    say(f"  !! toggles did not settle; ON={on}")
    return changed


_COMMIT_BTN = """(labels)=>{
  const norm=s=>(s||'').replace(/\\s+/g,' ').trim();
  for(const want of labels){
    for(const b of document.querySelectorAll('button')){
      const r=b.getBoundingClientRect();
      if(r.width===0||r.x<1240) continue;
      const t=norm(b.textContent);
      if(t===want||t.startsWith(want))
        return {label:want, x:Math.round(r.x+r.width/2),
                y:Math.round(r.y+r.height/2), disabled:!!b.disabled};
    }
  }
  return null;
}"""

# 'Extract additional data' is what the edit flow shows; 'Save' is the create
# flow's control. Order matters: in edit mode both can be present.
_COMMIT_LABELS = ("Extract additional data", "Save")


def commit_fields(page, say):
    """Commit the output-field selection, whichever control this flow shows."""
    for _ in range(4):
        pt = page.evaluate(_COMMIT_BTN, list(_COMMIT_LABELS))
        if pt and not pt["disabled"]:
            page.mouse.click(pt["x"], pt["y"])
            page.wait_for_timeout(5000)
            say(f"  committed via {pt['label']!r}")
            return pt["label"]
        page.wait_for_timeout(1200)
    say("  !! no commit control found for the field picker")
    return None


def add_validate(page, entry, dry_run, say, recon=False, shot_dir=None):
    wid = entry["workbook_id"]
    name = entry["workbook_name"]
    clay_ui.open_workbook_by_id(page, wid)

    if not B.table_exists(page, TABLE):
        say(f"SKIP {name}/{TABLE}: table does not exist")
        return {"workbook_id": wid, "workbook_name": name, "table": TABLE,
                "status": "no_table"}

    B.focus_table_maybe_empty(page, TABLE)
    page.wait_for_timeout(800)

    if not clay_ui._find_header_rect(page, SOURCE_COLUMN):
        say(f"SKIP {name}/{TABLE}: no {SOURCE_COLUMN!r} column — run the "
            f"waterfall step first")
        return {"workbook_id": wid, "workbook_name": name, "table": TABLE,
                "status": "no_source_column"}

    already = _sig_present(page)
    if already and not recon:
        # Already built (possibly by the user by hand): leave it alone.
        got = [f for f in KEEP_FIELDS if clay_ui._find_header_rect(page, f)]
        say(f"SKIP {name}/{TABLE}: already applied ({already!r}), fields={got}")
        return {"workbook_id": wid, "workbook_name": name, "table": TABLE,
                "status": "exists", "note": "already_present", "fields": got}

    B.open_card(page, "Validate email", must_contain=CARD_MUST_CONTAIN,
                must_not=("Create a column",))
    page.wait_for_timeout(3000)

    if recon:
        _dump(page, say, "card opened")
        say("  --- buttons ---")
        for t, x, y in _buttons(page):
            say(f"    x={x:<5} y={y:<5} {t!r}")
        if shot_dir:
            page.screenshot(path=os.path.join(shot_dir, "lm_card.png"))
        # advance to the field picker — this only changes the panel step, it
        # does not save anything, so it is safe to inspect
        cont = _continue_button(page)
        if cont:
            cont.click(timeout=10000)
            page.wait_for_timeout(3500)
            _dump(page, say, "after Continue to add fields")
            say("  --- buttons (field step) ---")
            for t, x, y in _buttons(page):
                say(f"    x={x:<5} y={y:<5} {t!r}")
            say("  --- checkboxes (field step) ---")
            for c in _field_checkboxes(page):
                say(f"    {c}")
            if shot_dir:
                page.screenshot(path=os.path.join(shot_dir, "lm_fields.png"))
        else:
            say("  !! no 'Continue to add fields' button found")
        page.keyboard.press("Escape")
        return {"workbook_id": wid, "workbook_name": name, "table": TABLE,
                "status": "recon"}

    # The card auto-maps Person's Email to WORK EMAIL; confirm rather than assume.
    panel = W._panel_text(page)
    if SOURCE_COLUMN not in panel:
        say(f"ABORT {name}/{TABLE}: {SOURCE_COLUMN!r} not shown in the card's "
            f"column mapping: {panel[:14]}")
        page.keyboard.press("Escape")
        return {"workbook_id": wid, "workbook_name": name, "table": TABLE,
                "status": "aborted", "reason": "email_not_mapped"}
    say(f"  Person's Email mapped to {SOURCE_COLUMN!r} (auto)")

    # Auto-run OFF first (user rule), then the gate.
    W._open_run_settings(page)
    W.auto_run_off(page, say)
    W._set_gate_condition(page, SOURCE_COLUMN, say)

    if dry_run:
        st = W.read_state(page)
        say(f"DRYRUN {name}/{TABLE}: state {st}; not saving")
        page.keyboard.press("Escape")
        return {"workbook_id": wid, "workbook_name": name, "table": TABLE,
                "status": "dryrun", "state": st}

    cont = _continue_button(page)
    if not cont:
        say(f"ABORT {name}/{TABLE}: no 'Continue to add fields' button")
        page.keyboard.press("Escape")
        return {"workbook_id": wid, "workbook_name": name, "table": TABLE,
                "status": "aborted", "reason": "no_continue"}
    cont.click(timeout=10000)
    page.wait_for_timeout(3500)

    set_output_fields(page, KEEP_FIELDS, say)

    saved = commit_fields(page, say) or W.save_column(page, say)
    page.wait_for_timeout(3000)

    # Confirm the chosen output columns actually exist.
    got = [f for f in KEEP_FIELDS if clay_ui._find_header_rect(page, f)]
    say(f"  columns present: {got}")

    # Verify the gate + auto-run persisted, reopening whichever column exists.
    verify = None
    for col in list(KEEP_FIELDS) + ["Validate email"]:
        if clay_ui._find_header_rect(page, col):
            try:
                verify = W.verify_persisted(page, col, SOURCE_COLUMN, say)
                break
            except Exception as e:
                say(f"  verify via {col!r} failed: {str(e)[:100]}")
    status = "ok" if (verify and verify.get("ok")) else "check_failed"
    say(f"{'DONE' if status == 'ok' else 'REVIEW'} {name}/{TABLE}: validate email "
        f"added (NOT run), fields={got}")
    return {"workbook_id": wid, "workbook_name": name, "table": TABLE,
            "status": status, "saved": saved, "fields": got, "verify": verify}


def _named_button(page, text):
    for el in page.get_by_role("button").all():
        try:
            if not el.is_visible():
                continue
            bb = el.bounding_box()
            if not bb or bb["x"] < 1240:
                continue
            if el.inner_text().strip() == text:
                return el
        except Exception:
            pass
    return None


def _open_field_picker(page, say):
    """Open Validate email's config and step into the 'Edit columns' picker."""
    W.open_column_config(page, "Validate email")
    page.wait_for_timeout(2000)
    btn = _named_button(page, "Edit columns")
    if not btn:
        return False
    btn.click(timeout=10000)
    page.wait_for_timeout(3500)
    return True


def offered_fields(page):
    """Field names the picker still offers = the ones NOT yet added as columns."""
    return [r["label"] for r in page.evaluate(_FIELD_TOGGLES,
                                              list(ALL_OUTPUT_FIELDS))]


def fix_fields(page, entry, say):
    """Ensure Status / Mx Record / Mx Provider are added as columns.

    Truth comes from the picker: anything it still offers has not been added.
    The commit control here is 'Extract additional data' — the create flow's
    'Save' drops both the field selection and the run condition.
    """
    wid, name = entry["workbook_id"], entry["workbook_name"]
    clay_ui.open_workbook_by_id(page, wid)
    B.focus_table_maybe_empty(page, TABLE)
    page.wait_for_timeout(1200)

    if not _open_field_picker(page, say):
        say(f"ABORT {name}: no 'Edit columns' button in the column config")
        page.keyboard.press("Escape")
        return {"workbook_id": wid, "workbook_name": name,
                "status": "aborted", "reason": "no_edit_columns"}

    offered = offered_fields(page)
    missing = [f for f in KEEP_FIELDS if f in offered]
    say(f"  picker offers {offered} -> already added: "
        f"{[f for f in KEEP_FIELDS if f not in offered]}, to add: {missing}")

    # Only touch toggles when something actually needs adding. Otherwise the
    # picker offers just the unwanted fields (already off as columns) and
    # flipping them achieves nothing while there is no commit control to press.
    changed = False
    if missing:
        changed = set_output_fields(page, KEEP_FIELDS, say)
    else:
        say("  all wanted fields already added")
    saved = "no_change"
    if changed:
        saved = commit_fields(page, say)
        page.wait_for_timeout(3000)
    else:
        say("  nothing to toggle")
        page.keyboard.press("Escape")
        page.wait_for_timeout(1500)

    # Verify by reopening the picker: none of the wanted fields should remain on
    # offer once they are real columns.
    still = []
    try:
        if _open_field_picker(page, say):
            still = [f for f in KEEP_FIELDS if f in offered_fields(page)]
            page.keyboard.press("Escape")
            page.wait_for_timeout(1200)
    except Exception as e:
        say(f"  re-check failed: {str(e)[:100]}")
        still = ["<unverified>"]
    added = [f for f in KEEP_FIELDS if f not in still]
    say(f"  output columns added: {added}"
        + (f" | still missing: {still}" if still else ""))

    verify = None
    try:
        verify = W.verify_persisted(page, "Validate email", SOURCE_COLUMN, say)
    except Exception as e:
        say(f"  gate verify failed: {str(e)[:120]}")
    return {"workbook_id": wid, "workbook_name": name,
            "status": "ok" if not still else "check_failed",
            "fields": added, "missing": still, "saved": saved, "verify": verify}


def ensure_validate(page, entry, say):
    """Get one workbook to the finished state, in the order that actually works.

    create (gate) -> reopen -> set output fields via 'Extract additional data'
    -> re-check the gate and repair it if the field commit dropped it.

    Doing the field selection inside the create flow loses fields AND the gate
    (observed on ChemE Show and ChinaBio), which is why this is two passes.
    """
    wid, name = entry["workbook_id"], entry["workbook_name"]

    created = None
    if not clay_ui._find_header_rect(page, "Validate email"):
        created = add_validate(page, entry, False, say)
        if created.get("status") not in ("ok", "check_failed", "exists"):
            return created            # no_table / no_source_column / aborted
    else:
        say(f"  'Validate email' column already exists on {name}")

    # Pass 2: output fields, via the edit flow (this is the reliable commit).
    fields = fix_fields(page, entry, say)
    got = fields.get("fields") or []
    verify = fields.get("verify") or {}
    if fields.get("status") == "aborted":
        return fields

    # The field commit can drop the run condition — put it back and prove it.
    if not verify.get("ok"):
        say(f"  gate missing after field commit on {name} — repairing")
        try:
            verify = W.repair_gate(page, "Validate email", SOURCE_COLUMN, say)
        except Exception as e:
            say(f"  !! gate repair failed: {str(e)[:140]}")
            verify = {"ok": False, "error": str(e)[:200]}

    ok = len(got) == len(KEEP_FIELDS) and verify.get("ok")
    say(f"{'DONE' if ok else 'REVIEW'} {name}/{TABLE}: fields={got} "
        f"gate_ok={verify.get('ok')}")
    return {"workbook_id": wid, "workbook_name": name, "table": TABLE,
            "status": "ok" if ok else "check_failed",
            "fields": got, "verify": verify, "created": created}


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("workbook_id")
    ap.add_argument("workbook_name")
    ap.add_argument("--recon", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--shot-dir", dest="shot_dir")
    ap.add_argument("--fix-fields", dest="fix_fields", action="store_true",
                    help="repair output-field selection on an existing column")
    ap.add_argument("--ensure", action="store_true",
                    help="full two-pass flow: create, set fields, verify gate")
    a = ap.parse_args()
    entry = {"workbook_id": a.workbook_id, "workbook_name": a.workbook_name}
    with common.clay_page(headless=not a.headed) as page:
        def say(m):
            print(m, flush=True)
        if a.ensure:
            print("\nRESULT:", ensure_validate(page, entry, say))
        elif a.fix_fields:
            print("\nRESULT:", fix_fields(page, entry, say))
        else:
            print("\nRESULT:", add_validate(page, entry, a.dry_run, say,
                                            recon=a.recon, shot_dir=a.shot_dir))
