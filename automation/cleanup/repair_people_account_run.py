"""Finish the People - Supabase columns: confirm the account, Save, and RUN.

Why this exists: the rollout applied the template and ran it while the account
was still "Data Cloud and Clay", so every row came back
`Error: Clay received a 401`. Switching the account afterwards persists, but on
its own it re-runs nothing -- the cells stay errored. This does the step the
rollout missed, per the user (2026-08-02): HTTP API column -> Edit column ->
Qasim - Labs -> **Save and run**.

Two earlier readings were wrong and are corrected here:
  * `check_column_fill.py` reports errored cells as UNFILLED, so "0/225" meant
    "225 errors", not "never ran".
  * a `RESULTS({{Lookup in Audiences}})` query counts rows whose lookup
    RETURNED, not rows that FOUND records. Every row actually reads
    "No records found", so the run condition passes and all rows do run.

Verification is the cell text itself: `Status Code: 200` vs `401`.

  python repair_people_account_run.py "Material Sciences" --check
  python repair_people_account_run.py "Material Sciences"
"""

import argparse
import json
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
AUTO_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, SCRIPT_DIR)
sys.path.insert(0, os.path.join(AUTO_DIR, "clay_sync"))
sys.path.insert(0, os.path.join(AUTO_DIR, "build_automation"))

os.environ.setdefault("CLAY_SUPABASE_TEMPLATE", "People - Supabase")

import clay_ui                          # noqa: E402
import browser_session                  # noqa: E402
import column_config as colcfg          # noqa: E402
import fix_supabase_api as fixapi       # noqa: E402
import add_workemail_waterfall as panel  # noqa: E402
import state_io                         # noqa: E402

AUDIT = os.path.join(SCRIPT_DIR, os.environ.get(
    "CLAY_PEOPLE_MANIFEST", "product_services_people.json"))
STATE = os.path.join(SCRIPT_DIR, os.environ.get(
    "CLAY_PEOPLE_REPAIR_STATE", "ps_people_repair_state.json"))
SHOTS = os.path.join(SCRIPT_DIR, "people_shots")
os.makedirs(SHOTS, exist_ok=True)

TABLE = "People"
API_ACCOUNT = "Qasim - Labs"

# Tally the HTTP API column's visible cells by outcome. Sampling the rendered
# grid is enough to tell 401-everywhere from 200-everywhere, which is the only
# distinction that matters here.
CELLS = """()=>{
  const norm=s=>(s||'').replace(/\\s+/g,' ').trim();
  let ok=0, e401=0, other=0, blank=0;
  const seen=[];
  for(const el of document.querySelectorAll('*')){
    if(el.children.length) continue;
    const t=norm(el.textContent);
    if(!t) continue;
    if(/^Status Code:\\s*200/.test(t)) {ok++; continue;}
    if(/received a 401/.test(t)) {e401++; continue;}
    if(/^Error:/.test(t)) {other++; if(seen.length<3) seen.push(t.slice(0,70));}
  }
  return {ok, e401, other, sample:seen};
}"""


def read_account(page):
    for _ in range(8):
        a = page.evaluate(fixapi._ACCOUNT)
        if a:
            return a
        page.wait_for_timeout(700)
    return None


def commit_and_run(page, say):
    """Click the plain Save (centre, NOT the caret) and then trigger the run.

    The caret click is what produced the empty "Save and ..." menu: the
    Edit-column panel has no split button. The previous fallback then pressed
    Escape, which closed the whole panel and made the Save button unfindable --
    hence "no Save control at all".
    """
    btn = page.evaluate(fixapi._SAVE_BTN)
    if not btn:
        say("  !! Save button not found")
        return "no_save"
    page.mouse.click(btn["x"], btn["y"])
    page.wait_for_timeout(5000)
    say("  clicked Save")

    ran = panel.trigger_run(page, say)
    if not ran:
        say("  !! could not trigger a run")
        return "saved_not_run"
    return "ok"


def repair(page, name, entry, say, check_only=False):
    res = {"workbook_name": name}
    clay_ui.open_workbook_by_id(page, entry["workbook_id"])
    if not colcfg.table_exists(page, TABLE):
        return {**res, "status": "no_table"}
    colcfg.focus_table_maybe_empty(page, TABLE)
    page.wait_for_timeout(1500)

    before = page.evaluate(CELLS)
    say(f"  cells before: 200={before['ok']} 401={before['e401']} "
        f"other={before['other']} {before['sample']}")
    res["cells_before"] = before

    fixapi._open_cfg(page, say)
    page.wait_for_timeout(2000)
    acct = read_account(page)
    say(f"  account: {acct!r}")
    res["account"] = acct

    if check_only:
        page.keyboard.press("Escape")
        return {**res, "status": "check"}

    if acct != API_ACCOUNT:
        page.mouse.move(1400, 500)
        for _ in range(8):
            page.mouse.wheel(0, -300)
            page.wait_for_timeout(200)
        res["set_account"] = fixapi.set_account(page, say)
        if res["set_account"] != "ok":
            page.keyboard.press("Escape")
            return {**res, "status": "account_not_set"}

    res["commit"] = commit_and_run(page, say)
    if res["commit"] not in ("ok",):
        return {**res, "status": res["commit"]}

    # let the run land, then read the cells again
    page.wait_for_timeout(20000)
    after = page.evaluate(CELLS)
    say(f"  cells after : 200={after['ok']} 401={after['e401']} "
        f"other={after['other']} {after['sample']}")
    res["cells_after"] = after

    slug = "".join(c if c.isalnum() else "_" for c in name)[:40]
    shot = os.path.join(SHOTS, f"repaired_{slug}.png")
    page.screenshot(path=shot)
    res["shot"] = shot

    status = "ok" if after["ok"] > 0 and after["e401"] == 0 else "needs_review"
    return {**res, "status": status}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("names", nargs="+")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--headed", action="store_true")
    args = ap.parse_args()

    audit = json.load(open(AUDIT, encoding="utf-8"))
    state = state_io.load_json(STATE, {})

    with browser_session.clay_page(headless=not args.headed) as page:
        for i, name in enumerate(args.names, 1):
            print(f"\n=== [{i}/{len(args.names)}] {name} ===", flush=True)
            if name not in audit:
                print(f"  !! not in manifest"); continue

            def say(m):
                print(m, flush=True)
            try:
                r = repair(page, name, audit[name], say,
                           check_only=args.check)
            except Exception as e:
                import traceback
                traceback.print_exc()
                r = {"workbook_name": name, "status": "error",
                     "error": str(e)[:300]}
            print(f"  -> {r.get('status')}", flush=True)
            if not args.check:
                state[name] = r
                state_io.save_json(STATE, state)


if __name__ == "__main__":
    main()
