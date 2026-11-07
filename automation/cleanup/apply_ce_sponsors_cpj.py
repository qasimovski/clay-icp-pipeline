"""Apply the "Domain & Company CPJ" template to every Competitive Events
`sponsors_normalized` table.

Simplest of the EVCharge template passes: the template ships with BOTH inputs
already mapped (Company Name -> `Company Name`, Website -> `Domain`), so there
is no mapping step — open the Configure panel and commit.

APPLY ONLY, NOT RUN. The template's provider is OpenAI at ~0.1 credits/row and
is marked "Conditionally run"; per CLAUDE.md a paid provider is configured and
left untriggered unless explicitly asked for. `--run` exists but is off by
default and the user has not asked for it.

Skipped by name (user, 2026-08-08): ACT Expo and APAC EV Charging
Infrastructure already have it. Skipped by Clay: any table already carrying the
template's output columns — the marker check is what actually prevents a
duplicate application, since a name list cannot know about work done later.

    python apply_ce_sponsors_cpj.py --list
    python apply_ce_sponsors_cpj.py --only "AMTS"
    python apply_ce_sponsors_cpj.py                # apply to all in scope
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
STATE = os.path.join(SCRIPT_DIR, "ce_sponsors_cpj_state.json")
SHOTS_DIR = os.path.join(SCRIPT_DIR, "ce_sponsors_shots")
TABLE = "sponsors_normalized"
TEMPLATE = "Domain & Company CPJ"

# Output columns this template creates. Presence of ANY is proof it is already
# applied — checked against Clay rather than trusted from the name list below,
# because a duplicate application would mean a second paid column on the table.
MARKERS = ("Verified Domain", "Primary Offerings", "Subindustry",
           "Business Stage", "Company Specialties")

DONE_BY_HAND = ("ACT Expo", "APAC EV Charging Infrastructure")

# The template is a CHAIN, not one column: Verified Domain (Claygent) feeds
# Official Domain (formula, free) which feeds Enrich Company, whose outputs are
# the 14 company fields. Running only the entry point is not enough — Verified
# Domain carries an `Only run if Website is empty` condition and sponsors rows
# DO have Websites, so it reports "Run condition not met" on most of them and
# cascades nothing. Enrich Company is what actually populates the table.
# Order matters: Verified Domain first so any row it does resolve is available
# to Enrich Company. Per the user (2026-08-09).
RUN_COLUMNS = ("Verified Domain", "Enrich Company")


def already_applied(rec):
    """True when the manifest's recorded columns include a template output.

    The manifest snapshot is authoritative enough here because it was built from
    Clay minutes ago AND every apply re-checks live before saving (see
    aps.save_without_running's pre-save guard), so a stale 'not applied' cannot
    produce a duplicate.
    """
    cols = set(rec.get("columns") or [])
    return bool(cols & set(MARKERS))


def one(page, name, rec, args):
    slug = "".join(c if c.isalnum() else "_" for c in name)[:60]
    clay_ui.open_workbook_by_id(page, rec["workbook_id"])
    page.wait_for_timeout(8000)
    colcfg.focus_table_maybe_empty(page, rec.get("table_name") or TABLE)
    page.wait_for_timeout(2500)

    # LIVE re-check, not the manifest snapshot. This pass DID create duplicate
    # columns on many tables (user, 2026-08-09) for exactly two reasons, both
    # fixed here: `already_applied()` read a static manifest taken before any
    # work, so after each background-task kill the tables applied in the
    # previous window still looked unapplied; and the save call below omitted
    # workbook_id/table_id, which silently disabled the pre-save duplicate
    # guard that the sibling passes rely on. Never call save_without_running
    # without those ids.
    applied, cols = aps.marker_present(rec["workbook_id"], rec.get("table_id"))
    if applied:
        aps.say(f"  template columns already present {cols[-2:]} — skipping")
        return {"status": "applied", "saved": "already_applied",
                "workbook_id": rec["workbook_id"]}

    aps.open_template(page, args.shots_dir, slug)
    # No mapping: both inputs ship bound. Touching them would risk unbinding a
    # correct mapping for no gain.
    aps.say("  fields ship pre-mapped (Company Name -> Company Name, "
            "Website -> Domain) — not touching them")
    saved = aps.save_without_running(page, args.shots_dir, slug,
                                     rec["workbook_id"], rec.get("table_id"))
    aps.say(f"  saved: {saved}")
    bps_shot = aps.bps.shot(page, args.shots_dir, f"{slug}_applied")
    return {"status": "applied", "saved": saved,
            "workbook_id": rec["workbook_id"], "shot": bps_shot}


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--only", action="append", default=[])
    ap.add_argument("--skip", action="append", default=list(DONE_BY_HAND))
    ap.add_argument("--limit", type=int)
    ap.add_argument("--column", action="append", default=[],
                    help="override which column(s) --run-columns triggers. The "
                         "chain needs TWO passes: Enrich Company depends on "
                         "Verified Domain -> Official Domain, so running both "
                         "back-to-back finds Enrich Company's inputs not yet "
                         "ready and reports nothing_to_run.")
    ap.add_argument("--run-columns", action="store_true",
                    help="run empty-or-out-of-date rows on the template's chain "
                         "(Verified Domain then Enrich Company). SPENDS CREDITS.")
    ap.add_argument("--redo", action="store_true")
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--shots-dir", default=SHOTS_DIR)
    args = ap.parse_args()

    if not os.path.exists(MANIFEST):
        raise SystemExit(f"no manifest at {MANIFEST} — build it first")
    manifest = state_io.load_json(MANIFEST)
    state = state_io.load_json(STATE)
    aps.TABLE = TABLE
    aps.TEMPLATE = TEMPLATE

    names = sorted(manifest, key=lambda n: (manifest[n].get("rows") or 0, n))
    names = [n for n in names if n not in args.skip]
    pre = [n for n in names if already_applied(manifest[n])]
    names = [n for n in names if n not in pre]
    if args.only:
        names = [n for n in names if n in args.only]
    pending = [n for n in names
               if args.redo or state.get(n, {}).get("status") != "applied"]
    if args.limit:
        pending = pending[:args.limit]

    if args.list:
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
        # Scope: every table the template has been applied to, plus the two the
        # user applied by hand — they need running too.
        done = [n for n in manifest
                if state.get(n, {}).get("status") == "applied"
                or n in DONE_BY_HAND]
        if args.only:
            done = [n for n in done if n in args.only]
        done = sorted(done, key=lambda n: (manifest[n].get("rows") or 0, n))
        todo = [n for n in done
                if args.redo
                or not set(args.column or RUN_COLUMNS).issubset(
                    (state.get(n, {}).get("ran_columns") or {}).keys())]
        if args.limit:
            todo = todo[:args.limit]
        print(f"{len(todo)} to run of {len(done)} applied")
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
                    colcfg.focus_table_maybe_empty(page,
                                                   rec.get("table_name") or TABLE)
                    page.wait_for_timeout(2500)
                    ran = {}
                    for col in (args.column or RUN_COLUMNS):
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
                state[name] = {**state.get(name, {}),
                               "run_error": str(e)[:300]}
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
