"""Apply + run the "Companies - Supabase" ledger template on every Competitive
Events `sponsors_normalized` table.

Per the user (2026-08-09), the same five steps as the exhibitors pass, with one
difference in step 2:

  1. open the template's Configure panel,
  2. map **Name** -> the table's own `Name` column (Domain auto-maps). NOT the
     `Terrapinn - Company Enrichments (CPJ)` group used on exhibitors — the
     sponsors tables carry a plain `Name` column of their own, produced by the
     Domain & Company CPJ template's Enrich Company outputs,
  3. **Save and don't run**,
  4. **Edit column** -> API account -> `Qasim - Labs`, verified by reopening,
  5. run the **empty or out-of-date** rows on `HTTP API`.

ACT Expo is excluded by name (user). Every other table is guarded by Clay
itself: an `HTTP API` column already present means the template is applied and
the template step is skipped, so a relaunch cannot create a duplicate paid
column.

    python apply_ce_sponsors_supabase.py --list
    python apply_ce_sponsors_supabase.py --only "AMTS"
    python apply_ce_sponsors_supabase.py            # all in scope
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

MANIFEST = os.path.join(SCRIPT_DIR, "evcharge_ce_sponsors_manifest.json")
STATE = os.path.join(SCRIPT_DIR, "ce_sponsors_supabase_state.json")
SHOTS_DIR = os.path.join(SCRIPT_DIR, "ce_sponsors_shots")
TABLE = "sponsors_normalized"
TEMPLATE = "Companies - Supabase"
EXCLUDE = ("ACT Expo",)


def one(page, name, rec, args):
    slug = "".join(c if c.isalnum() else "_" for c in name)[:60]
    wid = rec["workbook_id"]
    clay_ui.open_workbook_by_id(page, wid)
    page.wait_for_timeout(8000)
    colcfg.focus_table_maybe_empty(page, rec.get("table_name") or TABLE)
    page.wait_for_timeout(2500)

    applied, cols = aps.marker_present(wid, rec.get("table_id"))
    aps.say(f"  columns: {cols[-3:]}")
    if applied:
        aps.say(f"  {aps.MARKER_COLUMNS[0]!r} present — skipping the template")
        saved = "already_applied"
    else:
        aps.open_template(page, args.shots_dir, slug)
        # Same nested mapping as exhibitors, despite first appearances: the
        # sponsors table has no plain top-level `Name` column. Typing "Name"
        # into the picker offers only `Company Name` plus the
        # `Terrapinn - Company Enrichments (CPJ)` group — the `Name` wanted here
        # is inside that group (it is an Enrich Company output). The plain
        # column mapper fails with "'Name' column not offered".
        aps.set_name_from_cpj(page, args.shots_dir, slug)
        saved = aps.save_without_running(page, args.shots_dir, slug, wid,
                                         rec.get("table_id"))
        page.wait_for_timeout(6000)

    acct = aps.fix_api_account(page, args.shots_dir, slug)
    if not aps.verify_api_account(page, args.shots_dir, slug):
        raise aps.VerificationError(
            f"account is not {aps.API_ACCOUNT!r} on reopen — refusing to run")

    if not args.run:
        return {"status": "configured", "saved": saved, "account": acct,
                "workbook_id": wid}
    ran = aps.run_empty_or_stale(page, args.shots_dir, slug,
                                 column=aps.MARKER_COLUMNS[0])
    aps.say(f"  {aps.MARKER_COLUMNS[0]}: {ran}")
    return {"status": "ran", "saved": saved, "account": acct, "ran": ran,
            "workbook_id": wid}


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--only", action="append", default=[])
    ap.add_argument("--skip", action="append", default=list(EXCLUDE))
    ap.add_argument("--limit", type=int)
    ap.add_argument("--no-run", action="store_true",
                    help="stop after the API account fix, do not trigger")
    ap.add_argument("--redo", action="store_true")
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--shots-dir", default=SHOTS_DIR)
    args = ap.parse_args()
    args.run = not args.no_run          # this pass runs by default, per the user

    manifest = state_io.load_json(MANIFEST)
    state = state_io.load_json(STATE)
    aps.TABLE = TABLE
    aps.TEMPLATE = TEMPLATE

    names = sorted(manifest, key=lambda n: (manifest[n].get("rows") or 0, n))
    names = [n for n in names if n not in args.skip]
    if args.only:
        names = [n for n in names if n in args.only]
    terminal = {"ran"} if args.run else {"ran", "configured"}
    pending = [n for n in names
               if args.redo or state.get(n, {}).get("status") not in terminal]
    if args.limit:
        pending = pending[:args.limit]

    if args.list:
        print(f"excluded: {list(args.skip)}   mode="
              f"{'RUN' if args.run else 'configure-only'}")
        print(f"{len(pending)} pending of {len(names)} in scope:")
        for n in names:
            mark = "pending" if n in pending else state.get(n, {}).get("status", "?")
            print(f"  {mark:11s} {str(manifest[n].get('rows')):>6} rows  {n}")
        print(f"\n~{sum(manifest[n].get('rows') or 0 for n in pending):,} rows pending")
        return 0

    failures = []
    for i, name in enumerate(pending, 1):
        print(f"\n=== [{i}/{len(pending)}] {name}  "
              f"({manifest[name].get('rows')} rows)", flush=True)
        try:
            with browser_session.clay_page(headless=not args.headed) as page:
                state[name] = one(page, name, manifest[name], args)
        except Exception as e:
            traceback.print_exc()
            failures.append(name)
            state[name] = {**state.get(name, {}), "status": "failed",
                           "error": str(e)[:300]}
        state_io.save_json(STATE, state)

    print(f"\ndone: {len(pending) - len(failures)}/{len(pending)}")
    for n in failures:
        print(f"  failed: {n}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
