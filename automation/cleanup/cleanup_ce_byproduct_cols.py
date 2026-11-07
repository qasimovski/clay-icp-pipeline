"""Competitive Events tidy-up on the zero-fill `Verified Domain` tables:
delete the `Lookup row` / `Add row` byproduct columns, then run the ledger
check's empty-or-out-of-date rows.

Scope (user, 2026-08-08): the tables whose Verified Domain is 0% filled AND
whose Country input is already off — i.e. the ones the Country fix does NOT
apply to. Verified Domain is deliberately NOT triggered here: those tables carry
an `Only run if Website is empty` condition and their rows all have Websites, so
a run would return "Run condition not met" for every row. Tables whose Verified
Domain already has data are skipped entirely.

Scope is computed from the two read-only scans rather than hardcoded:
  ce_verified_domain_fill.json  (filled == 0)
  ce_verified_domain_scan.json  (Country input absent / toggled off)

Deleting a column is irreversible through the API, so both names are matched
EXACTLY and `clay_ui.delete_column` refuses anything in its KEEP_NEVER_DELETE_COLS
allowlist. A column already gone is reported, never treated as an error — this
pass has to be safe to relaunch after a kill.

    python cleanup_ce_byproduct_cols.py --list
    python cleanup_ce_byproduct_cols.py --only "Nordic EV Summit"
    python cleanup_ce_byproduct_cols.py            # all in scope
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

MANIFEST = os.path.join(SCRIPT_DIR, "evcharge_ce_manifest.json")
FILL = os.path.join(SCRIPT_DIR, "ce_verified_domain_fill.json")
SCAN = os.path.join(SCRIPT_DIR, "ce_verified_domain_scan.json")
STATE = os.path.join(SCRIPT_DIR, "ce_byproduct_state.json")
SHOTS_DIR = os.path.join(SCRIPT_DIR, "ce_vd_shots")

DROP_COLUMNS = ("Lookup row", "Add row")
LEDGER_COLUMN = "HTTP API"
# Already handled end-to-end today, including its Verified Domain run.
DONE_BY_HAND = ("APTA's TRANSform & EXPO",)


def in_scope(manifest, fill, scan):
    """Zero-fill tables whose Country input is off/absent — smallest first."""
    out = []
    for name in manifest:
        if name in DONE_BY_HAND:
            continue
        f = fill.get(name, {})
        if f.get("filled"):                    # Verified Domain has data -> skip
            continue
        rec = scan.get(name)
        if rec is None:                        # never scanned -> do not guess
            continue
        sw = rec.get("switch") or {}
        if rec.get("has_country") and str(sw.get("checked", "")).lower() == "true":
            continue                           # still needs the Country fix
        out.append(name)
    return sorted(out, key=lambda n: (manifest[n].get("rows") or 0, n))


def one(page, name, rec, args):
    slug = "".join(c if c.isalnum() else "_" for c in name)[:60]
    clay_ui.open_workbook_by_id(page, rec["workbook_id"])
    page.wait_for_timeout(8000)
    colcfg.focus_table_maybe_empty(page, rec.get("table_name"))
    page.wait_for_timeout(2500)

    deleted = {}
    for col in DROP_COLUMNS:
        gone = clay_ui.delete_column(page, col)
        deleted[col] = "deleted" if gone else "already absent"
        aps.say(f"  {col}: {deleted[col]}")
        page.wait_for_timeout(2500)

    ran = aps.run_empty_or_stale(page, args.shots_dir, slug, column=LEDGER_COLUMN)
    aps.say(f"  {LEDGER_COLUMN}: {ran}")
    return {"status": "done", "deleted": deleted, "ran": ran,
            "workbook_id": rec["workbook_id"]}


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--only", action="append", default=[])
    ap.add_argument("--limit", type=int)
    ap.add_argument("--redo", action="store_true")
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--shots-dir", default=SHOTS_DIR)
    args = ap.parse_args()

    manifest = state_io.load_json(MANIFEST)
    fill = state_io.load_json(FILL)
    scan = state_io.load_json(SCAN)
    state = state_io.load_json(STATE)

    names = in_scope(manifest, fill, scan)
    if args.only:
        names = [n for n in names if n in args.only]
    pending = [n for n in names
               if args.redo or state.get(n, {}).get("status") != "done"]
    if args.limit:
        pending = pending[:args.limit]

    if args.list:
        print(f"{len(pending)} pending of {len(names)} in scope:")
        for n in names:
            mark = "pending" if n in pending else state.get(n, {}).get("status", "?")
            print(f"  {mark:8s} {str(manifest[n].get('rows')):>6} rows  {n}")
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
