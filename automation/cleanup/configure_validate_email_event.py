"""Finish the "Find Email and Validate Email" template on one workbook:

  1. map Validate Email's required email input to the WORK EMAIL column
  2. re-type its run condition as !!{{WORK EMAIL}}
  3. (optionally) trigger the WORK EMAIL waterfall run

Why step 2 is mandatory per workbook: the template carries the condition over as
a raw field id from the table it was built on — on one event it arrived as
`!!{{f_<id-from-the-source-table>}}` while this table's WORK EMAIL is a
different `f_<id>`, so the baked gate points at a column that does not exist
here. Typing the name fresh binds it to the local column. (Auto-run does come
through correctly as OFF.)

  python configure_validate_email_event.py <wid> <name>            # configure only
  python configure_validate_email_event.py <wid> <name> --run      # + run WORK EMAIL
"""

import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
AUTO_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, SCRIPT_DIR)
sys.path.insert(0, os.path.join(AUTO_DIR, "clay_sync"))
sys.path.insert(0, os.path.join(AUTO_DIR, "build_automation"))

import clay_ui        # noqa: E402
import common         # noqa: E402
import build_lib as B  # noqa: E402
import add_workemail_waterfall_event as W  # noqa: E402
import apply_email_template_event as T  # noqa: E402  (_click_opt)

# Default is the speakers table; the people tables ("Sellers - People" /
# "Buyers - People") pass --table. The Validate Email fix is identical for both:
# the template inherits a gate pointing at a column that does not exist here
# (Clay renders it as "(Deleted column)"), and it arrives with Auto-run ON.
TABLE = "Speakers_normalized"
VALIDATE_COL = "Validate Email"
SOURCE_COL = "WORK EMAIL"
GATE_COL = "Speaker Name"          # the waterfall's own gate

# The email input row sits between "Setup Inputs" and "Run settings"; when
# unmapped its box reads "Please select a valid value" (or the usual
# placeholder). Returns the box's click point.
_INPUT_BOX = """()=>{
  const norm=s=>(s||'').replace(/\\s+/g,' ').trim();
  const leaves=[...document.querySelectorAll('*')].filter(e=>!e.children.length);
  let top=null, bottom=null;
  for(const el of leaves){
    const r=el.getBoundingClientRect();
    if(r.width===0||r.x<1240) continue;
    const t=norm(el.textContent);
    if(t==='Setup Inputs') top=r.y;
    if(t==='Run settings' && top!==null && bottom===null) bottom=r.y;
  }
  if(top===null) return null;
  if(bottom===null) bottom=top+400;
  for(const el of leaves){
    const r=el.getBoundingClientRect();
    if(r.width===0||r.x<1265||r.x>1700||r.y<top||r.y>bottom) continue;
    const t=norm(el.textContent);
    if(t==='Please select a valid value'||t.includes('Start typing'))
      return {x:Math.round(r.x+40), y:Math.round(r.y+r.height/2), text:t};
  }
  return null;
}"""

_MAPPED = """(want)=>{
  const norm=s=>(s||'').replace(/\\s+/g,' ').trim();
  const leaves=[...document.querySelectorAll('*')].filter(e=>!e.children.length);
  let top=null, bottom=null;
  for(const el of leaves){
    const r=el.getBoundingClientRect();
    if(r.width===0||r.x<1240) continue;
    const t=norm(el.textContent);
    if(t==='Setup Inputs') top=r.y;
    if(t==='Run settings' && top!==null && bottom===null) bottom=r.y;
  }
  if(top===null) return null;
  if(bottom===null) bottom=top+400;
  for(const el of leaves){
    const r=el.getBoundingClientRect();
    if(r.width===0||r.x<1265||r.x>1700||r.y<top||r.y>bottom) continue;
    if(norm(el.textContent)===want) return true;
  }
  return false;
}"""


def map_email_input(page, say):
    """Point the required email input at the WORK EMAIL column."""
    if page.evaluate(_MAPPED, SOURCE_COL):
        say(f"  email input already mapped to {SOURCE_COL!r}")
        return "prefilled"
    box = page.evaluate(_INPUT_BOX)
    if not box:
        say("  !! could not find the email input box")
        return "missing"
    say(f"  email input box reads {box['text']!r}")
    page.mouse.click(box["x"], box["y"])
    page.wait_for_timeout(1400)
    page.keyboard.type(SOURCE_COL, delay=40)
    page.wait_for_timeout(1600)
    if not T._click_opt_scroll(page, SOURCE_COL, xmin=1290, xmax=1720):
        say(f"  !! {SOURCE_COL!r} not offered in the picker")
        return "failed"
    page.wait_for_timeout(1500)
    ok = page.evaluate(_MAPPED, SOURCE_COL)
    say(f"  email input = {SOURCE_COL!r}" if ok
        else f"  !! mapping did not stick")
    return "ok" if ok else "failed"


def configure(page, entry, say, run_after=False, table=None):
    global TABLE
    if table:
        TABLE = table
    wid, name = entry["workbook_id"], entry["workbook_name"]
    clay_ui.open_workbook_by_id(page, wid)
    if not B.table_exists(page, TABLE):
        say(f"SKIP {name}: no {TABLE}")
        return {"workbook_id": wid, "workbook_name": name, "status": "no_table"}
    B.focus_table_maybe_empty(page, TABLE)
    page.wait_for_timeout(1000)

    if not W.find_header_scrolling(page, VALIDATE_COL):
        try:
            hdrs = page.evaluate("""()=>{const o=[];
              for(const el of document.querySelectorAll('*')){
                if(el.children.length) continue;
                const r=el.getBoundingClientRect();
                if(r.width===0||r.y<95||r.y>210) continue;
                const t=(el.textContent||'').trim();
                if(t) o.push(t.slice(0,28));}
              return o;}""")
            say(f"  visible headers: {hdrs}")
        except Exception:
            pass
        say(f"SKIP {name}: no {VALIDATE_COL!r} column — apply the template first")
        return {"workbook_id": wid, "workbook_name": name,
                "status": "no_validate_column"}

    W.open_column_config(page, VALIDATE_COL)
    page.wait_for_timeout(2000)
    mapped = map_email_input(page, say)

    W._open_run_settings(page)
    W.auto_run_off(page, say)
    # Always retype: the inherited condition references the template's original
    # table by field id, which resolves to nothing here.
    cond = W._set_gate_condition(page, SOURCE_COL, say)
    say(f"  condition now: {cond!r}")

    saved = W.save_column(page, say)
    say(f"  saved via {saved}")
    page.wait_for_timeout(3000)

    v = W.verify_persisted(page, VALIDATE_COL, SOURCE_COL, say)
    ok = v.get("ok") and mapped in ("ok", "prefilled")
    say(f"{'DONE' if ok else 'REVIEW'} {name}: validate email input={mapped} "
        f"gate_ok={v.get('ok')}")

    result = {"workbook_id": wid, "workbook_name": name,
              "status": "ok" if ok else "check_failed",
              "email_input": mapped, "verify": v, "saved": saved}

    if run_after:
        # Only run the waterfall once its own gate is confirmed present.
        W.open_column_config(page, SOURCE_COL)
        W._open_run_settings(page)
        st = W.read_state(page)
        auto_off = all(sw.get("state") not in ("true", "checked")
                       for sw in st.get("switches", [])
                       if "Auto-run" in (sw.get("label") or ""))
        say(f"  {SOURCE_COL} gate: condition={st.get('condition')!r} "
            f"auto_run_off={auto_off}")
        # Exact match required: the template can carry a gate over as a raw
        # field id from its source table, which resolves to nothing here.
        if W._norm_cond(st.get("condition")) != W._norm_cond(
                W.expected_condition(GATE_COL)):
            say(f"  {SOURCE_COL} gate is {st.get('condition')!r}, not "
                f"{W.expected_condition(GATE_COL)!r} — retyping")
            W.auto_run_off(page, say)
            W._set_gate_condition(page, GATE_COL, say)
            W.save_column(page, say)
            page.wait_for_timeout(2500)
            st = W.verify_persisted(page, SOURCE_COL, GATE_COL, say).get("state", {})
        else:
            page.keyboard.press("Escape")
            page.wait_for_timeout(1200)
        trig = W.trigger_run(page, say)
        result["ran"] = trig
        say(f"  triggered: {trig!r}")

    return result


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("workbook_id")
    ap.add_argument("workbook_name")
    ap.add_argument("--table", help="table name (default Speakers_normalized)")
    ap.add_argument("--run", action="store_true",
                    help="also trigger the WORK EMAIL waterfall run")
    ap.add_argument("--headed", action="store_true")
    a = ap.parse_args()
    entry = {"workbook_id": a.workbook_id, "workbook_name": a.workbook_name}
    with common.clay_page(headless=not a.headed) as page:
        def say(m):
            print(m, flush=True)
        print("\nRESULT:", configure(page, entry, say, run_after=a.run,
                                     table=a.table))
