"""Switch the CPJ People columns that resolve to nothing over to a FORMULA that
indexes the lookup object directly.

Why: the eight columns bound to CUSTOM exhibitor properties came back null on 140
of 143 tables even though the reference paths were right and the source data was
there -- Clay's Company Lookup does not resolve those properties through a plain
column reference. Indexing the lookup value itself does. Per the user (2026-08-04):

    Edit column -> the value row's settings icon -> "Formula" ->
    {{Company Table Data}}?.["Sub - industry"]||""

The eight canonical properties (Size, Type, Domain, LinkedIn URL, Founded,
Industry, Description, Specialities) already populate from their references and are
deliberately NOT touched.

Verifies the formula that actually persisted by reading the editor back, and never
saves a column whose formula did not take. Idempotent in the practical sense: a
column already carrying the right formula is detected and skipped.

  python fix_cpj_formulas.py <wb_id> "<table>" --dry-run          # all 8, no save
  python fix_cpj_formulas.py <wb_id> "<table>" --column "Side"
  python fix_cpj_formulas.py <wb_id> "<table>"                    # all 8, saves
"""

import argparse
import datetime
import json
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
AUTO_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, SCRIPT_DIR)
sys.path.insert(0, os.path.join(AUTO_DIR, "clay_sync"))
sys.path.insert(0, os.path.join(AUTO_DIR, "build_automation"))

import clay_ui                            # noqa: E402
import browser_session                    # noqa: E402
import column_config as colcfg            # noqa: E402
import formula_columns as fcol            # noqa: E402
import add_workemail_waterfall as panel   # noqa: E402
import state_io                           # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

LOOKUP = "Company Table Data"

# created column -> the lookup property it must read. The property name is the
# EXHIBITOR table's column name, which is why several differ from the column they
# feed ("Sub - industry" -> "Sub Industry").
PROPERTY_FOR_COLUMN = {
    "Sub Industry": "Sub - industry",
    "Primary Offerings": "Primary Offerings",
    "Business Stage": "Business Stage",
    "Annual Revenue": "Annual Revenue",
    "Total Funding Amount Range Usd": "Total Funding Amount Range Usd",
    "Side": "Side",
    "Classification": "Classification",
    "Country": "Country",
}

# Per the user (2026-08-04): "For the other events please do not do Country
# column." Country is excluded from the default set -- and it would mostly be
# churn anyway, because the exhibitor table's Country is empty on many events
# (ASMS 0%, ADLM USA 0%, while Pittcon is 100%). PROPERTY_FOR_COLUMN keeps the
# mapping so `--column Country` still works for a table where it IS wanted;
# Pittcon and ASMS Annual Conference already had it set before this instruction
# and are deliberately left alone.
SKIP_COLUMNS = {"Country"}

# What the fleet actually sets. Callers must size their "complete" check off THIS,
# not PROPERTY_FOR_COLUMN, or no table can ever reach all_set again.
ACTIVE_COLUMNS = [c for c in PROPERTY_FOR_COLUMN if c not in SKIP_COLUMNS]

STATE = os.path.join(SCRIPT_DIR, os.environ.get(
    "CLAY_CPJ_FORMULA_STATE", "cpj_formula_state.json"))


def formula_for(column):
    """`||""` keeps an unmatched row empty rather than surfacing undefined."""
    return f'{{{{{LOOKUP}}}}}?.["{PROPERTY_FOR_COLUMN[column]}"]||""'


# The settings button at the right end of the value row opens "Select an input
# type". It carries no label, title or test id, so it has to be found by position
# -- but ANCHORED to the value row's own hint text rather than a fixed y band: the
# row sits at a different height on some columns, which is why a fixed window found
# nothing for Classification and Country.
_MODE_BUTTON = """()=>{
  const norm=s=>(s||'').replace(/\\s+/g,' ').trim();
  let hintY=null;
  for(const el of document.querySelectorAll('*')){
    if(el.children.length) continue;
    const r=el.getBoundingClientRect();
    if(r.width===0||r.x<1240||r.x>1720) continue;
    if(norm(el.textContent).startsWith('Reference data from a column')){
      hintY=r.y; break;
    }
  }
  for(const el of document.querySelectorAll('button')){
    const r=el.getBoundingClientRect();
    if(r.width<20||r.width>44||r.height<24||r.height>40) continue;
    if(r.x<1645||r.x>1705) continue;
    if(hintY!==null){ if(r.y<hintY+10||r.y>hintY+110) continue; }
    // No hint text means a formula-mode panel, whose value row sits ~180px lower.
    // "Regenerate" (x=1601) and Close (y=88) fall outside this window anyway.
    else if(r.y<255||r.y>520) continue;
    return {x:Math.round(r.x+r.width/2), y:Math.round(r.y+r.height/2),
            hintY:hintY===null?null:Math.round(hintY)};
  }
  return null;
}"""

# Some of these columns arrive ALREADY in formula mode -- Classification and
# Country come from the template as AI/formula columns (their panel carries a
# "Formula" component icon and a "Regenerate" button, and their value row sits
# ~180px lower). For those the mode switch is unnecessary and its button is not
# where the reference-mode panel puts it, so detect the mode first and only switch
# when the column is still a plain column reference.
_IS_FORMULA_MODE = """()=>{
  for(const el of document.querySelectorAll('[data-sentry-component=Formula]')){
    const r=el.getBoundingClientRect();
    if(r.width>0&&r.x>=1240&&r.x<=1720&&r.y<220) return true;
  }
  return false;
}"""

# The "Formula" choice inside that popover ("Write a complex formula value").
_FORMULA_OPTION = """()=>{
  const norm=s=>(s||'').replace(/\\s+/g,' ').trim();
  for(const el of document.querySelectorAll('*')){
    if(el.children.length) continue;
    const r=el.getBoundingClientRect();
    if(r.width===0||r.x<1400||r.x>1700||r.y<300||r.y>600) continue;
    if(norm(el.textContent)==='Formula')
      return {x:Math.round(r.x+20), y:Math.round(r.y+r.height/2)};
  }
  return null;
}"""


def set_formula(page, column, say, dry_run=False):
    res = {"column": column, "formula": formula_for(column)}
    try:
        panel.open_column_config(page, column)
    except Exception as e:
        say(f"  {column}: cannot open config: {str(e)[:90]}")
        return {**res, "status": "no_column"}
    page.wait_for_timeout(1500)

    existing = ""
    try:
        existing = fcol.read_formula_text(page) or ""
    except Exception:
        pass
    if formula_for(column).replace(" ", "") in existing.replace(" ", ""):
        say(f"  {column}: formula already set")
        page.keyboard.press("Escape")
        return {**res, "status": "already_set"}

    # Decide by what the panel ACTUALLY offers, not by a mode icon: a formula
    # column exposes a second panel editor. The icon-based check gave false
    # positives, after which the formula was typed into the wrong field and read
    # back empty -- and the next column then timed out clicking a missing editor.
    if len(fcol._panel_editors(page)) < 2:
        btn = page.evaluate(_MODE_BUTTON)
        if not btn:
            say(f"  {column}: input-type button not found")
            page.keyboard.press("Escape")
            return {**res, "status": "no_mode_button"}
        page.mouse.click(btn["x"], btn["y"])
        page.wait_for_timeout(1800)

        opt = page.evaluate(_FORMULA_OPTION)
        if not opt:
            say(f"  {column}: 'Formula' option not offered")
            page.keyboard.press("Escape")
            return {**res, "status": "no_formula_option"}
        page.mouse.click(opt["x"], opt["y"])
        page.wait_for_timeout(2200)
    else:
        say(f"  {column}: already a formula column — writing straight into it")

    eds = fcol._panel_editors(page)
    if len(eds) < 2:
        say(f"  {column}: no formula editor (only {len(eds)} panel editor(s))")
        page.keyboard.press("Escape")
        return {**res, "status": "no_editor"}
    ed = page.locator('[contenteditable="true"]').nth(eds[-1]["i"])
    ed.click(timeout=8000)
    page.keyboard.press("Control+a")
    page.keyboard.press("Delete")
    page.wait_for_timeout(500)
    # insert_text, not type: no key events, so "/" and quotes cannot trigger the
    # token picker or shortcuts (the reason build_formula_handwritten uses it).
    page.keyboard.insert_text(formula_for(column))
    page.wait_for_timeout(3000)

    got = ""
    try:
        got = fcol.read_formula_text(page) or ""
    except Exception:
        pass
    res["editor"] = got[:200]
    key = PROPERTY_FOR_COLUMN[column]
    if key not in got:
        say(f"  {column}: formula did not take — editor reads {got[:80]!r}")
        page.keyboard.press("Escape")
        return {**res, "status": "formula_not_set"}

    if dry_run:
        say(f"  {column}: DRYRUN formula ok -> {got[:70]!r}")
        page.keyboard.press("Escape")
        page.wait_for_timeout(600)
        return {**res, "status": "dryrun"}

    try:
        saved = panel.save_column(page, say)
    except Exception as e:
        say(f"  {column}: save failed: {str(e)[:90]}")
        return {**res, "status": "save_failed"}
    say(f"  {column} = {got[:60]!r} (saved via {saved})")
    page.wait_for_timeout(1500)
    return {**res, "status": "ok", "saved": saved}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("workbook_id")
    ap.add_argument("table")
    ap.add_argument("--column", action="append", default=[],
                    help="only this column (repeatable); default is all 8")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--event", help="name for the state file key")
    a = ap.parse_args()
    columns = a.column or list(ACTIVE_COLUMNS)

    def say(m):
        print(m, flush=True)

    out = {}
    with browser_session.clay_page(headless=not a.headed) as page:
        page.set_viewport_size({"width": 1720, "height": 1900})
        clay_ui.open_workbook_by_id(page, a.workbook_id)
        if not colcfg.table_exists(page, a.table):
            raise SystemExit(f"no table {a.table!r}")
        colcfg.focus_table_maybe_empty(page, a.table)
        page.wait_for_timeout(1200)
        for column in columns:
            out[column] = set_formula(page, column, say, dry_run=a.dry_run)

    if not a.dry_run:
        key = f"{a.event or a.workbook_id}|{a.table}"
        state = state_io.load_json(STATE, {})
        state[key] = {"at": datetime.datetime.now().isoformat(timespec="seconds"),
                      "columns": {c: r["status"] for c, r in out.items()}}
        state_io.save_json(STATE, state)
    print("\nRESULT:", json.dumps({c: r["status"] for c, r in out.items()},
                                  indent=1))


if __name__ == "__main__":
    main()
