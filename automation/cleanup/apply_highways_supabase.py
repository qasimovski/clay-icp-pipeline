"""Apply the "Companies - Supabase" blocklist-ledger template to the Highways /
Competitive Events tables (`exhibitors_normalized`, then `sponsors_normalized`).

A Highways-scoped sibling of apply_ps_supabase.py, reusing that module's panel
library wholesale — the UI sequence is identical, only the scope differs. The
sequence, per the user (2026-08-13, matching the 2026-08-07 EVCharge one):

  1. open the template's Configure panel,
  2. map **Name** from the **Terrapinn - Company Enrichments (CPJ)** group
     (Domain auto-maps already),
  3. **Save without running**,
  4. **Edit column** -> switch the API account to "Qasim - Labs"; the template
     defaults to an account this workspace cannot use, and the wrong key is not
     an error the run surfaces — it just returns nothing useful for every row,
  5. only then run the **empty or out-of-date** rows.

Steps 1-4 spend nothing. Step 5 is the one paid action this repo runs on
purpose: 1 Action/row on the ledger check (CLAUDE.md).

WHY --configure-only AND --run-only ARE SEPARATE, AND WHY THAT MATTERS HERE
  The Highways ledger lives at a DIFFERENT endpoint from the Labs one
  (`ledger_check_highways_company`, see blocklist_ledger/highways/README.md).
  The template is shared, so if it is still pointing at the Labs RPC, running it
  writes Highways companies into the LABS ledger — silently, with a 200 and a
  perfectly plausible `is_new`. That contaminates a live 82k-row ledger and
  there is no undo.

  So the endpoint gets verified BETWEEN configure and run, out of band (read the
  column config via the Clay MCP/CLI and confirm the URL contains
  `ledger_check_highways_company`). Configure first, check, then run. Do not
  collapse these into one step just because it is fewer commands.

    python apply_highways_supabase.py --list
    python apply_highways_supabase.py --only "National Diesel Dirt & Turf Expo" --configure-only
    python apply_highways_supabase.py --only "National Diesel Dirt & Turf Expo" --run-only
    python apply_highways_supabase.py --table sponsors_normalized --configure-only
"""

import argparse
import os
import sys
import traceback

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
sys.path.insert(0, os.path.join(os.path.dirname(SCRIPT_DIR), "clay_sync"))
sys.path.insert(0, os.path.join(os.path.dirname(SCRIPT_DIR), "build_automation"))

import clay_ui                      # noqa: E402
import browser_session              # noqa: E402
import column_config as colcfg      # noqa: E402
import apply_ps_supabase as aps     # noqa: E402
import state_io                     # noqa: E402

SHOTS_DIR = os.path.join(SCRIPT_DIR, "highways_supabase_shots")
TEMPLATE = os.environ.get("CLAY_SUPABASE_TEMPLATE", "Companies - Supabase")

# Columns the template creates. `HTTP API` is the marker that it is applied;
# `Is New` having values is the marker that it has RUN. Same pair as the P&S
# pass, so aps.MARKER_COLUMNS needs no override here — but be explicit, because
# a sibling pass in this directory does override it and the global is shared.
MARKERS = ("HTTP API", "Is New")


# Three scopes, and they differ in ONE step: where the template's `Name` input
# is mapped from.
#
#   ce       Competitive Events exhibitors_normalized / sponsors_normalized.
#            The company name the ledger needs is NOT the table's own column —
#            it lives under the "Terrapinn - Company Enrichments (CPJ)" lookup
#            group, because those tables were built by the CPJ template.
#   ps       Products & Services `Companies` tables.
#   buyside  BuySide P&S `Companies` tables.
#            Both of these were built by a Clay company SEARCH, so they carry
#            Clay's native `Name` and `Domain` columns and the mapping is the
#            table's own `Name`. Using the CPJ picker here would fail outright —
#            there is no CPJ group on a search-derived table.
SCOPES = {
    "ce":      {"manifest": "highways_ce_{key}_manifest.json",
                "state":    "highways_supabase_state_{key}.json",
                "name_source": "cpj"},
    "ps":      {"manifest": "highways_ps_companies_manifest.json",
                "state":    "highways_ps_supabase_state.json",
                "name_source": "own"},
    "buyside": {"manifest": "highways_bs_companies_manifest.json",
                "state":    "highways_bs_supabase_state.json",
                "name_source": "own"},
}


def manifest_path(scope, table):
    return os.path.join(SCRIPT_DIR, SCOPES[scope]["manifest"].format(
        key=table.split("_")[0]))


def state_path(scope, table):
    return os.path.join(SCRIPT_DIR, SCOPES[scope]["state"].format(
        key=table.split("_")[0]))


# The Claygent / "Use AI" editor, which is the sidebar these search-built
# workbooks open into (it is left over from the Enrich-companies step that
# created the table). Its own prompt box is a contenteditable in the same
# position as the Templates search box, so typing a template name into it turns
# the name into a per-row AI research brief — aps.TEMPLATES_PANEL refuses on
# exactly these signatures.
#
# aps.open_template's retry cannot clear it: that retry only presses Ctrl+E when
# aps.TOOLS_OPEN sees Import/Enrich/Export/Templates, and this panel shows none
# of them, so the reset was a no-op and all three attempts failed identically.
AI_PANEL = """()=>{
  const norm=s=>(s||'').replace(/\\s+/g,' ').trim();
  const near=[];
  for(const el of document.querySelectorAll('*')){
    if(el.children.length) continue;
    const r=el.getBoundingClientRect();
    if(r.width===0||r.x<1240||r.y>430) continue;
    near.push(norm(el.textContent));
  }
  return ['Use case','Prompt','Generate','Compare models'].some(t=>near.includes(t));
}"""

# The back chevron at the sidebar's top-left, which returns the panel to the
# Tools root. Located by its rect, never a fixed coordinate.
AI_PANEL_BACK = """()=>{
  for(const b of document.querySelectorAll('button,[role="button"]')){
    const r=b.getBoundingClientRect();
    if(r.width===0||r.width>44) continue;
    if(r.x<1245||r.x>1300||r.y<55||r.y>95) continue;
    return {x:Math.round(r.x+r.width/2), y:Math.round(r.y+r.height/2)};
  }
  return null;
}"""


def reset_sidebar(page, tries=5):
    """Close the sidebar completely, so open_template starts from scratch.

    ESCAPE, not the panel's back chevron. Measured on a BuySide Companies table
    (2026-08-14): one Escape takes the sidebar from the Use-AI editor to fully
    closed (ai_panel=False, tools_open=False), and Ctrl+E then reopens Tools on
    "For you" — precisely the state the Competitive Events tables were already
    in, and the one aps.open_template is written against.

    The back chevron does NOT do this: it navigates within the Use-AI editor, so
    a sidebar stayed open, aps.open_template saw no need to press Ctrl+E, and
    typed the template name into the Claygent prompt box instead of the
    Templates search. Never types anything here.
    """
    for _ in range(tries):
        if not page.evaluate(AI_PANEL) and not page.evaluate(aps.TOOLS_OPEN):
            return True
        page.keyboard.press("Escape")
        page.wait_for_timeout(1500)
    return not page.evaluate(AI_PANEL)


def configure(page, name, rec, args):
    """Steps 1-4. Spends nothing."""
    slug = "".join(c if c.isalnum() else "_" for c in name)[:60]
    clay_ui.open_workbook_by_id(page, rec["workbook_id"])
    page.wait_for_timeout(8000)
    colcfg.focus_table_maybe_empty(page, rec.get("table_name"))
    page.wait_for_timeout(2500)

    applied, cols = aps.marker_present(rec["workbook_id"], rec.get("table_id"))
    if applied:
        aps.say(f"  {MARKERS[0]!r} already present — not re-applying")
    else:
        if not reset_sidebar(page):
            raise browser_session.VerificationError(
                "the Use-AI sidebar would not close; refusing to continue, "
                "because open_template would type the template name into its "
                "prompt box")
        aps.open_template(page, args.shots_dir, slug)
        if SCOPES[args.scope]["name_source"] == "cpj":
            aps.set_name_from_cpj(page, args.shots_dir, slug)
        else:
            aps.set_name_field(page, args.shots_dir, slug)
        saved = aps.save_without_running(page, args.shots_dir, slug,
                                         rec["workbook_id"], rec.get("table_id"))
        aps.say(f"  saved: {saved}")
        # Post-save proof. save_via_menu has reported success while the panel
        # sat there un-committed (London Build, the CPJ pass, 2026-08-13), so
        # the page's own status text is not evidence — the marker column is.
        ok = False
        for _ in range(6):
            page.wait_for_timeout(2500)
            ok, cols = aps.marker_present(rec["workbook_id"], rec.get("table_id"))
            if ok:
                break
        if not ok:
            raise browser_session.VerificationError(
                f"save reported {saved!r} but no {MARKERS[0]!r} column appeared")

    acct = aps.fix_api_account(page, args.shots_dir, slug)
    verified = aps.verify_api_account(page, args.shots_dir, slug)
    if not verified:
        raise browser_session.VerificationError(
            f"API account is not {aps.API_ACCOUNT!r} after saving — refusing to "
            f"leave it pointing at an account this workspace cannot use")
    shot = aps.bps.shot(page, args.shots_dir, f"{slug}_configured")
    return {"status": "configured", "account": acct, "verified": verified,
            "workbook_id": rec["workbook_id"], "shot": shot}


def run_one(page, name, rec, args):
    """Step 5. SPENDS 1 Action per row. Only reached via --run-only, after the
    endpoint has been confirmed to be the Highways RPC."""
    slug = "".join(c if c.isalnum() else "_" for c in name)[:60]
    clay_ui.open_workbook_by_id(page, rec["workbook_id"])
    page.wait_for_timeout(8000)
    colcfg.focus_table_maybe_empty(page, rec.get("table_name"))
    page.wait_for_timeout(2500)
    ran = aps.run_empty_or_stale(page, args.shots_dir, slug, column=MARKERS[0])
    aps.say(f"  {MARKERS[0]}: {ran}")
    return {"ran": ran}


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scope", default="ce", choices=sorted(SCOPES),
                    help="which folder's tables to walk; also selects where "
                         "the Name input is mapped from (see SCOPES)")
    ap.add_argument("--table", default="exhibitors_normalized",
                    choices=["exhibitors_normalized", "sponsors_normalized"],
                    help="ce scope only: which normalized table")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--only", action="append", default=[])
    ap.add_argument("--limit", type=int)
    ap.add_argument("--configure-only", action="store_true",
                    help="steps 1-4; spends nothing (the default)")
    ap.add_argument("--run-only", action="store_true",
                    help="step 5 only. SPENDS 1 Action/row. Verify the endpoint "
                         "is the Highways RPC first — see the module docstring.")
    ap.add_argument("--redo", action="store_true")
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--shots-dir", default=SHOTS_DIR)
    args = ap.parse_args()

    MANIFEST = manifest_path(args.scope, args.table)
    STATE = state_path(args.scope, args.table)
    manifest = state_io.load_json(MANIFEST)
    state = state_io.load_json(STATE)
    aps.TABLE = args.table if args.scope == "ce" else "Companies"
    aps.TEMPLATE = TEMPLATE
    aps.MARKER_COLUMNS = MARKERS

    names = sorted(manifest, key=lambda n: (manifest[n].get("rows") or 0, n))
    if args.only:
        names = [n for n in names if n in args.only]

    if args.run_only:
        todo = [n for n in names
                if args.redo
                or (state.get(n, {}).get("status") == "configured"
                    and not state.get(n, {}).get("ran"))]
    else:
        todo = [n for n in names
                if args.redo or state.get(n, {}).get("status") != "configured"]
    if args.limit:
        todo = todo[:args.limit]

    if args.list:
        print(f"table    : {args.table}")
        print(f"template : {TEMPLATE!r}   account: {aps.API_ACCOUNT!r}")
        print(f"\n{len(todo)} pending of {len(names)} in scope:")
        for n in names:
            s = state.get(n, {})
            mark = s.get("status", "pending")
            if s.get("ran"):
                mark = "ran"
            print(f"  {mark:11s} {str(manifest[n].get('rows')):>6} rows  {n}")
        return 0

    failures = []
    for i, name in enumerate(todo, 1):
        rec = manifest[name]
        print(f"\n=== [{i}/{len(todo)}] {name}  ({rec.get('rows')} rows)",
              flush=True)
        try:
            with browser_session.clay_page(headless=not args.headed) as page:
                if args.run_only:
                    state[name] = {**state.get(name, {}),
                                   **run_one(page, name, rec, args)}
                else:
                    state[name] = {**state.get(name, {}),
                                   **configure(page, name, rec, args)}
        except Exception as e:
            traceback.print_exc()
            failures.append(name)
            state[name] = {**state.get(name, {}),
                           "status": "failed", "error": str(e)[:300]}
        state_io.save_json(STATE, state)

    print(f"\ndone: {len(todo) - len(failures)}/{len(todo)}")
    for n in failures:
        print(f"  failed: {n}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
