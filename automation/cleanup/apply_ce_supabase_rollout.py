"""Fleet driver: the "Companies - Supabase" ledger template across every
EVCharge **Competitive Events** `exhibitors_normalized` table.

Differs from the Products & Services rollout in three ways, all of them real:

  * the table is `exhibitors_normalized` — LOWERCASE, because
    import_evcharge.py created it from `exhibitors_normalized.csv` and Clay
    names a table after the CSV stem. The Labs fleet's is capitalised;
  * the template's **Name** input maps from the `Terrapinn - Company
    Enrichments (CPJ)` group, not the table's own column (user, 2026-08-08);
  * scope is `evcharge_ce_manifest.json` (build it with
    build_evcharge_ce_manifest.py, in WSL), so every parallel worker agrees on
    the same scope and ordering.

SHARDING is how this goes faster without skipping anything: `--shards N
--shard i` partitions the manifest by index, one worker per terminal, each with
its own state file. The partition is over the manifest's sorted key order, so
it is stable as long as the manifest is — regenerate it and the shards shift,
which is why scope lives in a file rather than being resolved live.

    wsl -d Ubuntu -- python3 build_evcharge_ce_manifest.py    # once, first
    python apply_ce_supabase_rollout.py --list
    python apply_ce_supabase_rollout.py --run --shards 4 --shard 0   # terminal 1
    python apply_ce_supabase_rollout.py --run --shards 4 --shard 1   # terminal 2
    ...
    python merge_ce_state.py            # fold shard state back (optional)
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
import state_io                         # noqa: E402

MANIFEST = os.path.join(SCRIPT_DIR, "evcharge_ce_manifest.json")
LOG_DIR = os.path.join(SCRIPT_DIR, "ce_supabase_logs")
SHOTS_DIR = os.path.join(SCRIPT_DIR, "ce_supabase_shots")
TABLE = "exhibitors_normalized"
TEMPLATE = "Companies - Supabase"


def one(page, name, rec, args):
    slug = "".join(c if c.isalnum() else "_" for c in name)[:60]
    wid = rec["workbook_id"]
    clay_ui.open_workbook_by_id(page, wid)
    page.wait_for_timeout(8000)
    # Focus by the manifest's recorded table name, not a constant: these are
    # live Clay object names and the case differs from the Labs fleet's.
    colcfg.focus_table_maybe_empty(page, rec.get("table_name") or TABLE)
    page.wait_for_timeout(2500)

    applied, cols = aps.marker_present(wid, rec.get('table_id'))
    aps.say(f"  columns: {cols[-3:]}")
    if applied:
        aps.say(f"  {aps.MARKER_COLUMNS[0]!r} present — skipping the template")
        saved = "already_applied"
    else:
        aps.open_template(page, args.shots_dir, slug)
        aps.set_name_from_cpj(page, args.shots_dir, slug)
        saved = aps.save_without_running(page, args.shots_dir, slug, wid,
                                         rec.get('table_id'))
        page.wait_for_timeout(6000)

    acct = aps.fix_api_account(page, args.shots_dir, slug)
    if not aps.verify_api_account(page, args.shots_dir, slug):
        raise aps.VerificationError(
            f"account is not {aps.API_ACCOUNT!r} on reopen — refusing to run")

    if not args.run:
        return {"workbook_id": wid, "status": "configured", "saved": saved,
                "account": acct}
    ran = aps.run_empty_or_stale(page, args.shots_dir, slug)
    return {"workbook_id": wid, "status": "ran", "saved": saved,
            "account": acct, "ran": ran}


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only", action="append", default=[])
    ap.add_argument("--skip", action="append", default=[])
    ap.add_argument("--limit", type=int)
    ap.add_argument("--shards", type=int, default=1)
    ap.add_argument("--shard", type=int, default=0)
    ap.add_argument("--run", action="store_true",
                    help="run the empty/out-of-date rows (1 Action per row)")
    ap.add_argument("--redo", action="store_true")
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--shots-dir", default=SHOTS_DIR)
    args = ap.parse_args()

    if not os.path.exists(MANIFEST):
        raise SystemExit(
            f"no manifest at {MANIFEST} — run build_evcharge_ce_manifest.py in "
            f"WSL first. Resolving scope live would let two shards disagree.")
    manifest = state_io.load_json(MANIFEST)
    aps.TABLE = TABLE
    aps.TEMPLATE = TEMPLATE

    tag = f"_w{args.shard}" if args.shards > 1 else ""
    path = os.path.join(SCRIPT_DIR, f"ce_supabase_state{tag}.json")
    state = state_io.load_json(path)

    # Ascending by row count so a regression surfaces on a small table. Ties and
    # unknown counts fall back to name order, which keeps the partition stable.
    names = sorted(manifest, key=lambda n: (manifest[n].get("rows") or 0, n))
    if args.only:
        names = [n for n in names if n in args.only]
    names = [n for n in names if n not in args.skip]
    if args.shards > 1:
        names = [n for i, n in enumerate(names) if i % args.shards == args.shard]

    terminal = {"ran"} if args.run else {"ran", "configured"}
    pending = [n for n in names
               if args.redo or state.get(n, {}).get("status") not in terminal]
    if args.limit:
        pending = pending[:args.limit]

    if args.list or args.dry_run:
        mode = "RUN (1 Action/row)" if args.run else "configure-only"
        print(f"mode={mode}  shard={args.shard}/{args.shards}  "
              f"state={os.path.basename(path)}")
        print(f"{len(pending)} pending of {len(names)} in this shard "
              f"({len(manifest)} total):")
        for n in names:
            st = state.get(n, {}).get("status")
            mark = "pending" if n in pending else (st or "?")
            pre = "applied" if manifest[n].get("applied") else "-"
            print(f"  {mark:11s} {str(manifest[n].get('rows')):>7} rows  "
                  f"{pre:8s} {n}")
        print(f"\n~{sum(manifest[n].get('rows') or 0 for n in pending):,} rows "
              f"in this shard's pending set")
        return 0

    os.makedirs(LOG_DIR, exist_ok=True)
    failures = []
    for i, name in enumerate(pending, 1):
        print(f"\n=== [{i}/{len(pending)}] {name}  "
              f"({manifest[name].get('rows')} rows)", flush=True)
        try:
            with browser_session.clay_page(headless=not args.headed) as page:
                result = one(page, name, manifest[name], args)
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
