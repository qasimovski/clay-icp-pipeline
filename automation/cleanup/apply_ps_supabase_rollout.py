"""Fleet driver for apply_ps_supabase.py — the blocklist-ledger template across
the EVCharge Products & Services `Companies` tables.

Scope is the companies pass's own state file, so only workbooks with a built
`Companies` table are reachable.

Default is CONFIGURE-ONLY (template + Name mapping + the API-account fix, all
free). `--run` adds the one deliberate paid step: "Run N empty or out-of-date
rows" on the ledger check, 1 Action/row.

`--skip` takes categories already done by hand, so a manual run is never
repeated. Re-entry also asks Clay directly: the template step is skipped when
the `HTTP API` column already exists, and the account step is a no-op when it
already reads "Qasim - Labs".

Ordering is ascending by company count — a selector regression surfaces on the
104-row table, not the 19,943-row one.

    python apply_ps_supabase_rollout.py --list
    python apply_ps_supabase_rollout.py                    # configure, spend nothing
    python apply_ps_supabase_rollout.py --run              # SPENDS 1 Action/row
    python apply_ps_supabase_rollout.py --run --only "Finance, Investment & Funding"
"""

import argparse
import os
import sys
import traceback

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
sys.path.insert(0, os.path.join(os.path.dirname(SCRIPT_DIR), "clay_sync"))
sys.path.insert(0, os.path.join(os.path.dirname(SCRIPT_DIR), "build_automation"))

import clay_ui                          # noqa: E402
import browser_session                  # noqa: E402
import column_config as colcfg          # noqa: E402
import apply_ps_supabase as aps         # noqa: E402
import build_ps_companies as bps        # noqa: E402
import build_ps_people as bpp           # noqa: E402
import state_io                         # noqa: E402

from build_ps_companies_rollout import preview_count  # noqa: E402

LOG_DIR = os.path.join(SCRIPT_DIR, "ps_supabase_logs")

# Done by hand before this pass existed (user, 2026-08-07). Skipping by NAME is
# weaker than skipping by absence, but these workbooks are legitimately in scope
# for a re-run later, so they stay in the manifest — the Clay-side guards
# (column present, account already correct) are what actually stop rework.
ALREADY_DONE = (
    "Alternative Fuels & Future Energy Infrastructure",
    "Automotive & Mobility Providers",
)


def one(page, name, workbook_id, args):
    slug = "".join(c if c.isalnum() else "_" for c in name)[:60]
    clay_ui.open_workbook_by_id(page, workbook_id)
    page.wait_for_timeout(8000)
    colcfg.focus_table_maybe_empty(page, args.table)
    page.wait_for_timeout(2500)

    applied, cols = aps.marker_present(workbook_id)
    aps.say(f"  columns: {cols[-3:]}")
    if applied:
        aps.say(f"  {aps.MARKER_COLUMNS[0]!r} present — skipping the template")
        saved = "already_applied"
    else:
        aps.open_template(page, args.shots_dir, slug)
        if args.no_map:
            aps.say("  --no-map: template fields already mapped")
        elif args.name_source == "cpj":
            aps.set_name_from_cpj(page, args.shots_dir, slug)
        else:
            aps.set_name_field(page, args.shots_dir, slug)
        saved = aps.save_without_running(page, args.shots_dir, slug, workbook_id)
        page.wait_for_timeout(6000)

    acct = aps.fix_api_account(page, args.shots_dir, slug)
    if not aps.verify_api_account(page, args.shots_dir, slug):
        raise aps.VerificationError(
            f"account is not {aps.API_ACCOUNT!r} on reopen — refusing to run")

    if not args.run:
        return {"workbook_id": workbook_id, "status": "configured",
                "saved": saved, "account": acct}
    ran = aps.run_empty_or_stale(page, args.shots_dir, slug)
    return {"workbook_id": workbook_id, "status": "ran", "saved": saved,
            "account": acct, "ran": ran}


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--icp", default="evcharge")
    aps.add_target_args(ap)
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only", action="append", default=[])
    # NOTE: ALREADY_DONE is Companies-only history. It is stripped for any other
    # target below — those workbooks' People tables have had nothing done to them.
    ap.add_argument("--skip", action="append", default=list(ALREADY_DONE))
    ap.add_argument("--after")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--run", action="store_true",
                    help="run the empty/out-of-date rows (1 Action per row)")
    ap.add_argument("--redo", action="store_true")
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--shots-dir", default=aps.SHOTS_DIR)
    args = ap.parse_args()

    aps.configure_target(args)
    if args.table != "Companies":
        args.skip = [s for s in args.skip if s not in ALREADY_DONE]
    wbs = bpp.company_workbooks(args.icp)
    sizes = {n: preview_count(r.get("preview")) for n, r in
             state_io.load_json(os.path.join(
                 SCRIPT_DIR, f"ps_companies_state_{args.icp}.json")).items()}
    # Separate state per target table: the Companies run must not mark the
    # People run done, or vice versa.
    suffix = "" if args.table == "Companies" else f"_{args.table.lower()}"
    path = os.path.join(SCRIPT_DIR,
                        f"ps_supabase_state_{args.icp}{suffix}.json")
    state = state_io.load_json(path)

    names = sorted(wbs, key=lambda n: (sizes.get(n) is None, sizes.get(n) or 0, n))
    names = [n for n in names if n not in args.skip]
    if args.only:
        names = [n for n in names if n in args.only]
    if args.after:
        names = names[names.index(args.after) + 1:]

    terminal = {"ran"} if args.run else {"ran", "configured"}
    pending = [n for n in names
               if args.redo or state.get(n, {}).get("status") not in terminal]
    if args.limit:
        pending = pending[:args.limit]

    if args.list or args.dry_run:
        mode = "RUN (1 Action/row)" if args.run else "configure-only"
        print(f"icp={args.icp}  mode={mode}")
        print(f"skipping (done by hand): {list(args.skip)}")
        print(f"{len(pending)} pending of {len(names)} in scope:")
        for n in names:
            mark = "pending" if n in pending else state.get(n, {}).get("status", "?")
            print(f"  {mark:11s} {str(sizes.get(n)):>6s} rows  {n}")
        return 0

    os.makedirs(LOG_DIR, exist_ok=True)
    failures = []
    for i, name in enumerate(pending, 1):
        print(f"\n=== [{i}/{len(pending)}] {name}  ({sizes.get(name)} rows)",
              flush=True)
        try:
            with browser_session.clay_page(headless=not args.headed) as page:
                result = one(page, name, wbs[name], args)
        except Exception as e:
            traceback.print_exc()
            failures.append(name)
            state[name] = {**state.get(name, {}), "status": "failed",
                           "error": str(e)[:300]}
        else:
            state[name] = {**state.get(name, {}), **result}
        state_io.save_json(path, state)

    print(f"\ndone: {len(pending) - len(failures)}/{len(pending)}")
    if failures:
        print("failed:")
        for n in failures:
            print(f"  {n}")
    print(f"state: {path}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
