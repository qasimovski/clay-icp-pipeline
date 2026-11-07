"""Apply the "Domain & Company CPJ" template to the Highways / Competitive
Events tables — `exhibitors_normalized` first, then `sponsors_normalized`.

A sibling of apply_ce_sponsors_cpj.py (the EVCharge run) pointed at the Highways
manifests, with two differences that matter:

 1. `--table` selects which table type to walk, so the same pass does the
    exhibitors sweep and then the sponsors sweep, against separate manifests and
    separate state files. Exhibitors first, per the user (2026-08-13).
 2. It sets `aps.MARKER_COLUMNS` before calling into apply_ps_supabase. The
    EVCharge pass did NOT, so the live duplicate guard there was checking for
    `HTTP API` — a column this template never creates — and could never report
    "already applied". Do not remove that assignment.

APPLY ONLY, NOT RUN. The chain's two action columns are paid providers
(`Verified Domain` is Claygent, `Enrich Company` is an Enrich Company action),
so per CLAUDE.md they are created and fully configured but never triggered.
There is deliberately no --run-columns flag here: the user asked for the
template to be applied, not run.

The template ships with both inputs bound (Company Name -> `Company Name`,
Website -> `Domain`), and `exhibitors_normalized` / `sponsors_normalized` both
carry those exact column names, so there is no mapping step — open Configure and
commit.

Skipped by name: Big 5 Construct Saudi, applied by the user by hand
(2026-08-13). Its table is also the reference for what a correct application
looks like — 17 new columns, `Verified Domain` .. `Primary Offerings`.

    python apply_highways_cpj.py --list
    python apply_highways_cpj.py --recon --limit 1     # probe the panel only
    python apply_highways_cpj.py --limit 1             # smallest table, for review
    python apply_highways_cpj.py                       # the rest of exhibitors
    python apply_highways_cpj.py --table sponsors_normalized
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

SHOTS_DIR = os.path.join(SCRIPT_DIR, "highways_cpj_shots")
# NOT the EVCharge pass's "Domain & Company CPJ" — that name does not exist in
# this workspace. Confirmed by --recon against the live Templates panel
# (2026-08-13): the saved template is spelled "Domain and Company (CPJ)", and
# the panel's exact-name match is what the search depends on, so the ampersand
# form finds the card visually but never selects it.
TEMPLATE = os.environ.get("CLAY_HIGHWAYS_CPJ_TEMPLATE",
                          "Domain and Company (CPJ)")

# Output columns this template creates, in the order Clay adds them. Presence of
# the first is proof it is already applied — checked against Clay (not the
# manifest snapshot) before every save, because a duplicate application means a
# second PAID column on the table.
MARKERS = ("Verified Domain", "Official Domain", "Enrich Company",
           "Primary Offerings", "Subindustry", "Business Stage",
           "Company Specialties")

DONE_BY_HAND = ("Big 5 Construct Saudi",)

# The template is a CHAIN, not one column: Verified Domain (Claygent) feeds
# Official Domain (a free formula) which feeds Enrich Company, whose outputs are
# the 14 company fields. Order matters, and so does running them in SEPARATE
# invocations: Enrich Company reads Official Domain, so triggering both
# back-to-back finds its inputs not yet resolved and reports nothing_to_run.
# Run `--column "Verified Domain"`, let it finish, then `--column "Enrich
# Company"`. Learned on the EVCharge run (see apply_ce_sponsors_cpj.py).
#
# Note Verified Domain carries `Only run if Website is empty`, so on tables
# where most rows already have a Website it legitimately runs on very few rows —
# that is the gate working, not a failure. Enrich Company is what populates the
# table.
RUN_COLUMNS = ("Verified Domain", "Enrich Company")


def manifest_path(table):
    return os.path.join(SCRIPT_DIR,
                        f"highways_ce_{table.split('_')[0]}_manifest.json")


def state_path(table):
    return os.path.join(SCRIPT_DIR,
                        f"highways_cpj_state_{table.split('_')[0]}.json")


def already_applied(rec):
    """True when the manifest's recorded columns include a template output.

    A pre-filter for --list only. The authoritative check is the live one in
    `one()` / `aps.save_without_running`, so a stale 'not applied' here can
    never produce a duplicate.
    """
    return bool(set(rec.get("columns") or []) & set(MARKERS))


def one(page, name, rec, args):
    slug = "".join(c if c.isalnum() else "_" for c in name)[:60]
    clay_ui.open_workbook_by_id(page, rec["workbook_id"])
    page.wait_for_timeout(8000)
    colcfg.focus_table_maybe_empty(page, rec.get("table_name"))
    page.wait_for_timeout(2500)

    # LIVE re-check, never the manifest snapshot: a previous window may have
    # applied this table after the manifest was built.
    applied, cols = aps.marker_present(rec["workbook_id"], rec.get("table_id"))
    if applied:
        aps.say(f"  template columns already present {cols[-2:]} — skipping")
        return {"status": "applied", "saved": "already_applied",
                "workbook_id": rec["workbook_id"]}

    aps.open_template(page, args.shots_dir, slug)
    if args.recon:
        # Read-only probe: the Configure panel is open, so screenshot what the
        # template search actually matched and back out without saving.
        aps.dump_panel(page, f"{TEMPLATE!r} configure panel")
        shot = aps.bps.shot(page, args.shots_dir, f"{slug}_recon")
        page.keyboard.press("Escape")
        page.wait_for_timeout(1200)
        return {"status": "recon", "shot": shot}

    # No mapping: both inputs ship bound (Company Name -> Company Name,
    # Website -> Domain). Touching them risks unbinding a correct mapping.
    aps.say("  fields ship pre-mapped — not touching them")
    saved = aps.save_without_running(page, args.shots_dir, slug,
                                     rec["workbook_id"], rec.get("table_id"))
    aps.say(f"  saved: {saved}")
    shot = aps.bps.shot(page, args.shots_dir, f"{slug}_applied")

    # POST-SAVE PROOF. `save_via_menu` reported success on London Build while
    # the Configure panel sat there with Save un-clicked, so the table kept its
    # 8 original columns and state recorded it applied — a silent miss that only
    # a live read caught (2026-08-13). Per CLAUDE.md the page's own status text
    # is not evidence; the marker column is. Poll, because Clay takes a few
    # seconds to materialise 17 columns.
    #
    # Note aps.marker_present fails CLOSED (reports "applied") when the CLI read
    # itself fails. That is the right bias for the pre-save guard and the wrong
    # one here, so a CLI outage can still let a miss through — it cannot cause a
    # duplicate, only another retry next run.
    for _ in range(6):
        page.wait_for_timeout(2500)
        applied, cols = aps.marker_present(rec["workbook_id"], rec.get("table_id"))
        if applied:
            return {"status": "applied", "saved": saved,
                    "workbook_id": rec["workbook_id"], "shot": shot,
                    "verified_columns": len(cols)}
    raise browser_session.VerificationError(
        f"save reported {saved!r} but no template column appeared on "
        f"{rec.get('table_name')} — treating as NOT applied so it is retried")


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--table", default="exhibitors_normalized",
                    choices=["exhibitors_normalized", "sponsors_normalized"])
    ap.add_argument("--list", action="store_true",
                    help="print scope and exit — no browser")
    ap.add_argument("--recon", action="store_true",
                    help="open the template's Configure panel and dump it, "
                         "then back out without saving. Changes nothing.")
    ap.add_argument("--only", action="append", default=[])
    ap.add_argument("--skip", action="append", default=list(DONE_BY_HAND))
    ap.add_argument("--limit", type=int)
    ap.add_argument("--run-columns", action="store_true",
                    help="SPENDS CREDITS. Trigger 'empty or out-of-date rows' "
                         "on the template's chain for tables it is applied to. "
                         "Off by default; only use it when asked to run.")
    ap.add_argument("--column", action="append", default=[],
                    help="which column(s) --run-columns triggers. Defaults to "
                         f"{list(RUN_COLUMNS)}, but the chain wants TWO "
                         "invocations — see RUN_COLUMNS.")
    ap.add_argument("--redo", action="store_true")
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--template", default=TEMPLATE,
                    help=f"saved template name to search for (default {TEMPLATE!r})")
    ap.add_argument("--shots-dir", default=SHOTS_DIR)
    args = ap.parse_args()

    MANIFEST, STATE = manifest_path(args.table), state_path(args.table)
    if not os.path.exists(MANIFEST):
        raise SystemExit(f"no manifest at {MANIFEST} — run "
                         f"build_highways_ce_manifest.py (in WSL) first")
    manifest = state_io.load_json(MANIFEST)
    state = state_io.load_json(STATE)
    aps.TABLE = args.table
    aps.TEMPLATE = args.template
    # See the module docstring: without this the live duplicate guard checks the
    # Supabase pass's marker and never fires.
    aps.MARKER_COLUMNS = MARKERS

    # Smallest table first, so a selector regression surfaces on a 13-row table
    # rather than on Environmental Services and Solutions Expo (637).
    names = sorted(manifest, key=lambda n: (manifest[n].get("rows") or 0, n))
    names = [n for n in names if n not in args.skip]
    pre = [n for n in names if already_applied(manifest[n])]
    names = [n for n in names if n not in pre]
    if args.only:
        names = [n for n in names if n in args.only]
    pending = [n for n in names
               if args.redo or args.recon
               or state.get(n, {}).get("status") != "applied"]
    if args.limit:
        pending = pending[:args.limit]

    if args.list:
        print(f"table    : {args.table}")
        print(f"template : {args.template!r}")
        print(f"skipped by name: {list(args.skip)}")
        print(f"skipped (template columns already present): {len(pre)}")
        for n in pre:
            print(f"    {n}")
        print(f"\n{len(pending)} pending of {len(names)} in scope:")
        for n in names:
            mark = "pending" if n in pending else state.get(n, {}).get("status", "?")
            print(f"  {mark:8s} {str(manifest[n].get('rows')):>6} rows  {n}")
        return 0

    if args.run_columns:
        # Scope: tables this pass applied, plus any applied by hand — those need
        # running too. Smallest first, same as the apply sweep.
        done = [n for n in manifest
                if state.get(n, {}).get("status") == "applied"
                or n in DONE_BY_HAND]
        if args.only:
            done = [n for n in done if n in args.only]
        done = sorted(done, key=lambda n: (manifest[n].get("rows") or 0, n))
        cols = args.column or list(RUN_COLUMNS)
        todo = [n for n in done
                if args.redo
                or not set(cols).issubset(
                    (state.get(n, {}).get("ran_columns") or {}).keys())]
        if args.limit:
            todo = todo[:args.limit]
        print(f"{len(todo)} to run of {len(done)} applied | columns: {cols}")
        failed = []
        for i, name in enumerate(todo, 1):
            rec = manifest[name]
            slug = "".join(c if c.isalnum() else "_" for c in name)[:60]
            print(f"\n=== [{i}/{len(todo)}] {name}  "
                  f"({rec.get('rows')} rows)", flush=True)
            try:
                with browser_session.clay_page(headless=not args.headed) as page:
                    clay_ui.open_workbook_by_id(page, rec["workbook_id"])
                    page.wait_for_timeout(8000)
                    colcfg.focus_table_maybe_empty(page, rec.get("table_name"))
                    page.wait_for_timeout(2500)
                    ran = {}
                    for col in cols:
                        ran[col] = aps.run_empty_or_stale(page, args.shots_dir,
                                                          slug, column=col)
                        aps.say(f"  {col}: {ran[col]}")
                        page.wait_for_timeout(3000)
                prev = state.get(name, {}).get("ran_columns") or {}
                state[name] = {**state.get(name, {}),
                               "ran_columns": {**prev, **ran}}
            except Exception as e:
                traceback.print_exc()
                failed.append(name)
                state[name] = {**state.get(name, {}), "run_error": str(e)[:300]}
            state_io.save_json(STATE, state)
        print(f"\ndone: {len(todo) - len(failed)}/{len(todo)}")
        for n in failed:
            print(f"  failed: {n}")
        return 1 if failed else 0

    failures = []
    for i, name in enumerate(pending, 1):
        print(f"\n=== [{i}/{len(pending)}] {name}  "
              f"({manifest[name].get('rows')} rows)", flush=True)
        try:
            with browser_session.clay_page(headless=not args.headed) as page:
                res = one(page, name, manifest[name], args)
            if not args.recon:
                state[name] = res
        except Exception as e:
            traceback.print_exc()
            failures.append(name)
            if not args.recon:
                state[name] = {**state.get(name, {}), "status": "failed",
                               "error": str(e)[:300]}
        if not args.recon:
            state_io.save_json(STATE, state)

    print(f"\ndone: {len(pending) - len(failures)}/{len(pending)}")
    for n in failures:
        print(f"  failed: {n}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
