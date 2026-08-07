"""Apply the "People - Supabase" template to the People table of one
Product & Services workbook.

Per the user (2026-08-02), the order matters and is NOT the same as the
Companies pass:

  1. apply the template and "Save and run" over ALL rows in the view,
  2. THEN open the HTTP API column -> Edit column and switch the account to
     "Qasim - Labs", saving through the split button again.

Applying the template always lands the WRONG HTTP API (Headers) account, so
step 2 is mandatory every time. Plain Save does not persist an account change
(verified during the Companies pass -- it reverts on reload), which is why both
saves go through "Save and run ... in this view".

Scope: tables named exactly "People" in the Product & Services folder
(product_services_people.json). Nothing else is reachable. Chemicals &
Reagents is deliberately absent from that manifest -- it was done by hand.

The fragile UI primitives are imported from the Companies scripts rather than
copied, so a Clay UI change still only has to be fixed in one place.

  python apply_people_supabase.py <workbook_name> --recon
  python apply_people_supabase.py <workbook_name> --dry-run
  python apply_people_supabase.py <workbook_name>
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

# apply_companies_supabase reads its template name from this at import time.
os.environ.setdefault("CLAY_SUPABASE_TEMPLATE", "People - Supabase")

import clay_ui                       # noqa: E402
import browser_session               # noqa: E402
import column_config as colcfg       # noqa: E402
import apply_companies_supabase as acs   # noqa: E402  (open_template, _BOX, ...)
import fix_supabase_api as fixapi    # noqa: E402  (account switch)
import repair_people_account_run as repair  # noqa: E402  (commit_and_run, CELLS)
import state_io                      # noqa: E402

# Which folder's People tables are in scope. Overridable so the same flow can
# run against another folder (e.g. Buyside P&S) without editing code -- the
# manifest is the ONLY thing that decides what is reachable, so pointing it
# elsewhere is the whole scoping mechanism.
AUDIT = os.path.join(SCRIPT_DIR, os.environ.get(
    "CLAY_PEOPLE_MANIFEST", "product_services_people.json"))
STATE = os.path.join(SCRIPT_DIR, os.environ.get(
    "CLAY_PEOPLE_STATE", "ps_people_supabase_state.json"))

TABLE = "People"
COLUMN = "HTTP API"
API_ACCOUNT = "Qasim - Labs"

# The template's two body fields. The JSON keys are the RPC's parameter names
# (ledger_check_person), which are fixed; the values bind to THIS table's
# columns -- note "LinkedIn Profile" carries a capital I in Clay while the RPC
# parameter is spelled "Linkedin Profile". Confirmed against the already-working
# Chemicals & Reagents column.
FIELDS = ("Full Name", "LinkedIn Profile")

# Proof the template is already applied, so a re-run never double-applies.
MARKER = ("HTTP API", "Is New")

# Save without running in phase 1, so the only run happens after the account is
# corrected. Halves Actions; see apply_people() for why it matters here.
FIX_FIRST = os.environ.get("CLAY_PEOPLE_FIX_FIRST", "") not in ("", "0")

acs.TABLE = TABLE
fixapi.TABLE = TABLE


def set_field(page, label, say):
    """Point one template field at the table column of the same name.

    Returns "prefilled" when the template already bound it, which is the
    common case for fields whose name matches exactly.
    """
    info = None
    for _ in range(10):
        info = page.evaluate(acs._BOX, [label, [label]])
        if info:
            break
        page.wait_for_timeout(800)
    if not info:
        say(f"  {label}: field not in panel")
        return "missing"
    if not info.get("empty"):
        say(f"  {label}: already set to {info.get('chip')!r}")
        return "prefilled" if info.get("chip") == label else "wrong"
    page.mouse.click(info["x"], info["y"])
    page.wait_for_timeout(1800)
    page.keyboard.type(label, delay=40)
    page.wait_for_timeout(1800)
    for _ in range(6):
        pt = page.evaluate(acs._FIND_ROW, [label, 1290, 1400])
        if pt:
            page.mouse.click(pt["x"], pt["y"])
            page.wait_for_timeout(2200)
            break
        page.wait_for_timeout(1200)
    got = (page.evaluate(acs._BOX, [label, [label]]) or {}).get("chip")
    say(f"  {label} = {got!r}")
    return "ok" if got == label else "failed"


def fix_account(page, say):
    """Phase 2: open the HTTP API column and switch the account.

    Separate from the template apply because the template ALWAYS lands the
    wrong account and the only control that sticks is the one in the column
    config, committed through "Save and run".
    """
    fixapi._open_cfg(page, say)
    before = None
    for _ in range(8):
        before = page.evaluate(fixapi._ACCOUNT)
        if before:
            break
        page.wait_for_timeout(700)
    say(f"  account before fix: {before!r}")
    if before == API_ACCOUNT:
        say("  already correct — nothing to change")
        page.keyboard.press("Escape")
        return {"account_before": before, "set_account": "prefilled",
                "account_after": before, "status": "ok"}

    # the account control is at the top of the panel
    page.mouse.move(1400, 500)
    for _ in range(8):
        page.mouse.wheel(0, -300)
        page.wait_for_timeout(200)
    res = fixapi.set_account(page, say)
    if res != "ok":
        page.keyboard.press("Escape")
        return {"account_before": before, "set_account": res,
                "status": "account_not_set"}

    # The Edit-column panel has a PLAIN Save (next to "Try on 5 rows"), not the
    # template panel's split button, so a "Save and ..." menu lookup returns []
    # and the caret click does nothing. Click Save's CENTRE and then trigger the
    # run explicitly.
    #
    # Switching the account persists on its own, but it re-runs NOTHING: the
    # cells left over from phase 1 stay `Error: Clay received a 401` forever.
    # The run below is what actually makes the column work, and is the step the
    # first version of this script missed.
    saved = repair.commit_and_run(page, say)
    if saved != "ok":
        return {"account_before": before, "set_account": res, "saved": saved,
                "status": saved}

    # Verify against a FRESH load. Plain Save was caught reverting this exact
    # setting during the Companies pass, so the reload is the real check.
    page.reload()
    page.wait_for_timeout(6000)
    colcfg.focus_table_maybe_empty(page, TABLE)
    page.wait_for_timeout(1500)
    fixapi._open_cfg(page, say)
    after = None
    for _ in range(8):
        after = page.evaluate(fixapi._ACCOUNT)
        if after:
            break
        page.wait_for_timeout(700)
    say(f"  account after reload: {after!r}")
    page.keyboard.press("Escape")
    page.wait_for_timeout(800)
    return {"account_before": before, "set_account": res, "saved": saved,
            "account_after": after,
            "status": "ok" if after == API_ACCOUNT else "account_not_persisted"}


def apply_people(page, entry, say, recon=False, dry_run=False):
    wid, name = entry["workbook_id"], entry["workbook_name"]
    res = {"workbook_name": name,
           "at": datetime.datetime.now().isoformat(timespec="seconds")}

    clay_ui.open_workbook_by_id(page, wid)
    if not colcfg.table_exists(page, TABLE):
        say(f"SKIP {name}: no {TABLE!r} table")
        return {**res, "status": "no_table"}
    colcfg.focus_table_maybe_empty(page, TABLE)
    page.wait_for_timeout(1200)

    if not (recon or dry_run) and acs.already_applied(entry.get("table_id"),
                                                      MARKER, say):
        return {**res, "status": "already_applied"}

    acs.open_template(page, say)

    if recon:
        rows = page.evaluate(acs.PANEL)
        say(f"RECON {name}: template panel contents")
        for r in rows:
            say(f"   x={r['x']:<5} y={r['y']:<5} {r['t']!r}")
        page.keyboard.press("Escape")
        return {**res, "status": "recon", "panel": [r["t"] for r in rows]}

    field_res = {f: set_field(page, f, say) for f in FIELDS}
    res["fields"] = field_res
    bad = [f for f, v in field_res.items() if v not in ("ok", "prefilled")]
    if bad:
        say(f"ABORT {name}: fields not bound: {bad}")
        page.keyboard.press("Escape")
        return {**res, "status": "aborted"}

    if dry_run:
        say(f"DRYRUN {name}: fields={field_res}; not saving")
        page.keyboard.press("Escape")
        return {**res, "status": "dryrun"}

    # Phase 1 — commit the template.
    #
    # FIX_FIRST (Buyside P&S onward): save WITHOUT running. The template always
    # lands the wrong account, so running here would 401 every ungated row and
    # then need a second full run after the account is corrected — double the
    # Actions for an identical end state. With the workspace near its annual
    # credit limit that is the difference between finishing and stalling.
    #
    # Otherwise (the Product & Services pass): save AND run first, the order the
    # user originally specified.
    if FIX_FIRST:
        colcfg.save_via_menu(page, r"Save and don'?t run")
        say("  phase 1: saved WITHOUT running (fix-first mode)")
        res["phase1_saved"] = "no_run"
    else:
        colcfg.save_via_menu(page, r"Save and run.*in this view")
        say("  phase 1: saved and running over all rows in view")
        res["phase1_saved"] = "run_all_in_view"
    page.wait_for_timeout(8000)

    # Phase 2 — correct the account, then Save and trigger the run. This is the
    # only run in fix-first mode.
    res.update(fix_account(page, say))
    return res


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("workbook_name")
    ap.add_argument("--recon", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--headed", action="store_true")
    a = ap.parse_args()

    audit = json.load(open(AUDIT, encoding="utf-8"))
    if a.workbook_name not in audit:
        raise SystemExit(
            f"{a.workbook_name!r} is not in {os.path.basename(AUDIT)} "
            f"(in scope: {', '.join(sorted(audit))})")
    rec = audit[a.workbook_name]
    entry = {"workbook_id": rec["workbook_id"],
             "workbook_name": a.workbook_name,
             "table_id": rec.get("table_id")}

    with browser_session.clay_page(headless=not a.headed) as page:
        def say(m):
            print(m, flush=True)
        out = apply_people(page, entry, say, recon=a.recon, dry_run=a.dry_run)

    print("\nRESULT:", json.dumps(out, indent=1))
    if not (a.recon or a.dry_run):
        state = state_io.load_json(STATE, {})
        state[a.workbook_name] = out
        state_io.save_json(STATE, state)
        print(f"state -> {STATE}")
