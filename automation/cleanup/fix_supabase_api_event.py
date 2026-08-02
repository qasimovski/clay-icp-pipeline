"""Repair the "Companies - Supabase" HTTP API column on Product & Services
Companies tables.

Per the user (2026-08-01): the API account was not actually changed when the
template was applied. For each of the 9 Companies tables already processed:
open the "HTTP API" column -> Edit column, pick "Qasim - Labs" in the
"Select HTTP API (Headers) account" dropdown, confirm the Body JSON maps
"Name" to the table's own Name column, save, and run.

The account lives at the top of the column config; the Name mapping is a column
chip inside the Body JSON ("Name": {{Name}}), a few scrolls down.

  python fix_supabase_api_event.py --audit                 # read-only, all 9
  python fix_supabase_api_event.py --only "Material Sciences"
  python fix_supabase_api_event.py --limit 9               # fix + run
"""

import argparse
import datetime
import json
import os
import sys
import traceback

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
AUTO_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, SCRIPT_DIR)
sys.path.insert(0, os.path.join(AUTO_DIR, "clay_sync"))
sys.path.insert(0, os.path.join(AUTO_DIR, "build_automation"))

import clay_ui        # noqa: E402
import common         # noqa: E402
import build_lib as B  # noqa: E402
import add_workemail_waterfall_event as W  # noqa: E402

AUDIT = os.path.join(SCRIPT_DIR, "product_services_companies.json")
STATE_IN = os.path.join(SCRIPT_DIR, "ps_supabase_state.json")
STATE = os.path.join(SCRIPT_DIR, "ps_supabase_api_fix.json")
COLUMN = "HTTP API"
TABLE = "Companies"
WANT_ACCOUNT = "Qasim - Labs"

# the value shown to the right of the "Account" label in the column config
_ACCOUNT = """()=>{
  const norm=s=>(s||'').replace(/\\s+/g,' ').trim();
  const leaves=[...document.querySelectorAll('*')].filter(e=>!e.children.length);
  let ly=null;
  for(const el of leaves){
    const r=el.getBoundingClientRect();
    if(r.width===0||r.x<1250||r.x>1300) continue;
    if(norm(el.textContent)==='Account'){ly=r.y+r.height/2; break;}
  }
  if(ly===null) return null;
  for(const el of leaves){
    const r=el.getBoundingClientRect();
    const cy=r.y+r.height/2;
    if(r.width===0||r.x<1450||Math.abs(cy-ly)>12) continue;
    const t=norm(el.textContent);
    if(t) return t;
  }
  return null;
}"""

# the collapsed select box under "Select HTTP API (Headers) account"
_SELECT_BOX = """()=>{
  const norm=s=>(s||'').replace(/\\s+/g,' ').trim();
  const leaves=[...document.querySelectorAll('*')].filter(e=>!e.children.length);
  let ly=null;
  for(const el of leaves){
    const r=el.getBoundingClientRect();
    if(r.x<1240) continue;
    if(norm(el.textContent)==='Select HTTP API (Headers) account'){ly=r.y;break;}
  }
  if(ly===null) return null;
  for(const el of leaves){
    const r=el.getBoundingClientRect();
    if(r.width===0||r.x<1265||r.x>1600||r.y<ly+8||r.y>ly+60) continue;
    if(norm(el.textContent))
      return {x:Math.round(r.x+120), y:Math.round(r.y+r.height/2)};
  }
  return null;
}"""

# an open dropdown option with exact text, strictly below the select box
_OPTION = """([txt, below])=>{
  const norm=s=>(s||'').replace(/\\s+/g,' ').trim();
  for(const el of document.querySelectorAll('*')){
    if(el.children.length) continue;
    const r=el.getBoundingClientRect();
    if(r.width===0||r.x<1265||r.x>1450||r.y<below+40||r.y>1000) continue;
    if(norm(el.textContent)===txt)
      return {x:Math.round(r.x+20), y:Math.round(r.y+r.height/2)};
  }
  return null;
}"""

# the column chip that follows the '"Name":' key inside the Body JSON
_BODY_CHIP = """(key)=>{
  const norm=s=>(s||'').replace(/\\s+/g,' ').trim();
  const leaves=[...document.querySelectorAll('*')].filter(e=>!e.children.length);
  let ly=null, lx=null;
  for(const el of leaves){
    const r=el.getBoundingClientRect();
    if(r.width===0||r.x<1250) continue;
    if(norm(el.textContent)===key){ly=r.y+r.height/2; lx=r.x; break;}
  }
  if(ly===null) return null;
  const chips=[];
  for(const el of leaves){
    const r=el.getBoundingClientRect();
    const cy=r.y+r.height/2;
    if(r.width===0||r.x<=lx||Math.abs(cy-ly)>12) continue;
    const t=norm(el.textContent);
    if(t && t!=='"' && t!==',' && t!==':') chips.push(t);
  }
  return {found:true, chips};
}"""


def _open_cfg(page, say, tries=4):
    """Opening the column menu is flaky (the 'Edit column' item sometimes isn't
    measurable on the first try) — retry rather than lose the table."""
    last = None
    for i in range(tries):
        try:
            W.open_column_config(page, COLUMN)
            page.wait_for_timeout(2500)
            return
        except Exception as e:
            last = e
            say(f"  open_column_config retry {i+1}: {str(e)[:80]}")
            try:
                page.keyboard.press("Escape")
            except Exception:
                pass
            page.wait_for_timeout(2000)
    raise last


def _scroll_to_body(page, tries=6):
    """Scroll the config panel until the Body JSON's '"Name":' key is visible."""
    for _ in range(tries):
        hit = page.evaluate(_BODY_CHIP, '"Name":')
        if hit:
            return hit
        page.mouse.move(1400, 700)
        page.mouse.wheel(0, 260)
        page.wait_for_timeout(700)
    return None


def read_config(page):
    """Read the account and the Name chip out of an open column config."""
    acct = None
    for _ in range(8):
        acct = page.evaluate(_ACCOUNT)
        if acct:
            break
        page.wait_for_timeout(700)
    body = _scroll_to_body(page)
    chips = (body or {}).get("chips") or []
    return {"account": acct, "name_chips": chips}


def set_account(page, say):
    box = page.evaluate(_SELECT_BOX)
    if not box:
        say("  !! account select box not found")
        return "no_box"
    page.mouse.click(box["x"], box["y"])
    page.wait_for_timeout(1800)
    opt = None
    for _ in range(6):
        opt = page.evaluate(_OPTION, [WANT_ACCOUNT, box["y"]])
        if opt:
            break
        page.wait_for_timeout(800)
    if not opt:
        say(f"  !! {WANT_ACCOUNT!r} not in the dropdown")
        page.keyboard.press("Escape")
        return "no_option"
    page.mouse.click(opt["x"], opt["y"])
    page.wait_for_timeout(2200)
    got = page.evaluate(_ACCOUNT)
    say(f"  account now {got!r}")
    return "ok" if got == WANT_ACCOUNT else "failed"


_SAVE_BTN = """()=>{
  const norm=s=>(s||'').replace(/\\s+/g,' ').trim();
  for(const b of document.querySelectorAll('button,[role="button"]')){
    const r=b.getBoundingClientRect();
    if(r.width===0||r.x<1600||r.x>1730) continue;
    if(norm(b.textContent)==='Save')
      return {x:Math.round(r.x+r.width/2), y:Math.round(r.y+r.height/2),
              right:Math.round(r.right-10)};
  }
  return null;
}"""

# the caret menu's items: "Save and run 10 rows" / "Save and run N rows in this
# view" / "Save and don't run"
_SAVE_MENU = """()=>{
  const norm=s=>(s||'').replace(/\\s+/g,' ').trim();
  const out=[];
  for(const el of document.querySelectorAll('*')){
    if(el.children.length) continue;
    const t=norm(el.textContent);
    if(!t.startsWith('Save and')) continue;
    const r=el.getBoundingClientRect();
    if(r.width===0) continue;
    out.push({t, x:Math.round(r.x+r.width/2), y:Math.round(r.y+r.height/2)});
  }
  return out;
}"""


def save_and_run(page, say):
    """Commit via the Save split button's "Save and run ... in this view" item.

    Plain Save does NOT persist an account change — verified by reload, it
    reverts. Only the save-and-run path sticks.
    """
    btn = page.evaluate(_SAVE_BTN)
    if not btn:
        say("  !! Save button not found")
        return "no_save"
    page.mouse.click(btn["right"], btn["y"])   # the caret half
    page.wait_for_timeout(1800)
    items = page.evaluate(_SAVE_MENU)
    say(f"  save menu: {[i['t'] for i in items]}")
    pick = [i for i in items if "in this view" in i["t"]] \
        or [i for i in items if i["t"].startswith("Save and run")]
    if not pick:
        page.keyboard.press("Escape")
        say("  !! no 'Save and run' item")
        return "no_item"
    say(f"  clicking {pick[0]['t']!r}")
    page.mouse.click(pick[0]["x"], pick[0]["y"])
    page.wait_for_timeout(5000)
    return "ok"


def fix_event(page, entry, say, do_run=True, audit_only=False):
    wid, name = entry["workbook_id"], entry["workbook_name"]
    res = {"workbook_name": name,
           "at": datetime.datetime.now().isoformat(timespec="seconds")}
    clay_ui.open_workbook_by_id(page, wid)
    if not B.table_exists(page, TABLE):
        say(f"SKIP {name}: no {TABLE!r} table")
        return {**res, "status": "no_table"}
    B.focus_table_maybe_empty(page, TABLE)
    page.wait_for_timeout(1200)

    _open_cfg(page, say)
    before = read_config(page)
    say(f"  before: account={before['account']!r} "
        f"Name chips={before['name_chips']}")
    res.update(account_before=before["account"],
               name_before=before["name_chips"])

    if audit_only:
        page.keyboard.press("Escape")
        page.wait_for_timeout(600)
        return {**res, "status": "audit"}

    needs_account = before["account"] != WANT_ACCOUNT
    if needs_account:
        # the account control is at the top; scroll back up before clicking
        page.mouse.move(1400, 500)
        for _ in range(8):
            page.mouse.wheel(0, -300)
            page.wait_for_timeout(200)
        res["set_account"] = set_account(page, say)
    else:
        say(f"  account already {WANT_ACCOUNT!r}")
        res["set_account"] = "prefilled"

    if "Name" not in (before["name_chips"] or []):
        say(f"  !! Name mapping looks wrong: {before['name_chips']} "
            f"— leaving it for review")
        res["name_status"] = "needs_review"
    else:
        res["name_status"] = "ok"

    if needs_account and res["set_account"] != "ok":
        page.keyboard.press("Escape")
        return {**res, "status": "account_not_set"}

    if not do_run:
        page.keyboard.press("Escape")
        return {**res, "status": "dry", "account_after": before["account"]}

    # scroll back to the bottom bar and commit through "Save and run ... view"
    res["saved"] = save_and_run(page, say)
    if res["saved"] != "ok":
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass
        return {**res, "status": "save_failed"}

    # verify against a fresh load — the reason plain Save was caught out
    page.reload()
    page.wait_for_timeout(6000)
    B.focus_table_maybe_empty(page, TABLE)
    page.wait_for_timeout(1500)
    _open_cfg(page, say)
    after = read_config(page)
    say(f"  after reload: account={after['account']!r} "
        f"Name chips={after['name_chips']}")
    res.update(account_after=after["account"], name_after=after["name_chips"])
    page.keyboard.press("Escape")
    page.wait_for_timeout(800)
    if after["account"] != WANT_ACCOUNT:
        return {**res, "status": "account_not_persisted"}
    return {**res, "status": "ok", "ran": True}


def load(p, d):
    if os.path.exists(p):
        try:
            return json.load(open(p, encoding="utf-8"))
        except Exception:
            pass
    return d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--audit", action="store_true")
    ap.add_argument("--only")
    ap.add_argument("--limit", type=int, default=9)
    ap.add_argument("--no-run", action="store_true")
    ap.add_argument("--headed", action="store_true")
    args = ap.parse_args()

    audit = json.load(open(AUDIT, encoding="utf-8"))
    done_before = load(STATE_IN, {})
    # only the tables this repo's rollout actually applied the template to
    targets = [wb for wb, r in done_before.items()
               if r.get("status") in ("ok", "already_applied")]
    state = load(STATE, {})

    if args.only:
        targets = [wb for wb in targets if wb == args.only]
    else:
        targets = [wb for wb in targets
                   if args.audit or state.get(wb, {}).get("status") != "ok"]
    targets = targets[: args.limit]
    print(f"targets ({len(targets)}): {targets}", flush=True)

    with common.clay_page(headless=not args.headed) as page:
        for i, wb in enumerate(targets):
            print(f"\n--- [{i+1}/{len(targets)}] {wb} ---", flush=True)
            entry = {"workbook_id": audit[wb]["workbook_id"],
                     "workbook_name": wb}

            def say(m):
                print(m, flush=True)
            try:
                r = fix_event(page, entry, say, do_run=not args.no_run,
                              audit_only=args.audit)
            except Exception as e:
                print(f"!! EXCEPTION {wb}: {str(e)[:200]}", flush=True)
                traceback.print_exc()
                r = {"workbook_name": wb, "status": "error",
                     "error": str(e)[:300]}
                try:
                    page.keyboard.press("Escape")
                except Exception:
                    pass
            if not args.audit:
                state[wb] = r
                json.dump(state, open(STATE, "w", encoding="utf-8"), indent=1,
                          ensure_ascii=False)
            else:
                print(f"  AUDIT {wb}: {r}", flush=True)

    print("\n===== SUMMARY =====")
    for wb in targets:
        r = state.get(wb, {})
        print(f"  {wb:52} {r.get('status')} acct={r.get('account_after')} "
              f"name={r.get('name_status')} ran={r.get('ran')}")


if __name__ == "__main__":
    main()
