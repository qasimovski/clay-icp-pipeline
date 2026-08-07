"""Fleet driver for export_event_tables.py: download Exhibitors_normalized,
"Sellers - People" and "Buyers - People" from every Competitive Events workbook
into `<out-root>/<Event>/<Table name>.csv`.

Per the user (2026-08-04): ACHEMA was exported and reviewed first, then "continue
with the others". Scope is every workbook in competitive_events_workbooks.json
(97), ordered alphabetically.

=== Why alphabetical, not smallest-table-first ===

The fleet convention is ascending by row count so a selector regression surfaces
on a small table. That buys nothing here: this pass spends no credits and writes
nothing to Clay, so a regression costs a re-run, not money — and the ordering
input (live row counts) is exactly the stale data the convention warns about.
Alphabetical is stable across shards and reads straight off the log.

=== Re-entry is guarded by the ARTIFACT, not the state file ===

A workbook is skipped when its three CSVs already exist on disk non-empty, so a
lost/deleted state file cannot cause a silent re-download, and a partial event
(one table failed) resumes at exactly the missing table. The state file records
outcomes for reporting and for which tables were legitimately ABSENT from a
workbook — the people-build rollout left some events without people tables, and
those must not be retried forever as failures.

Everything is read-only against Clay: a table tab, the Tools panel, the Export
tab and Download CSV are the only things clicked.

    python export_event_tables_rollout.py --list          # scope, no browser
    python export_event_tables_rollout.py --dry-run       # what it would fetch
    python export_event_tables_rollout.py --only "ACHEMA" --headed
    python export_event_tables_rollout.py --shards 3 --shard 0   # one per terminal
    python export_event_tables_rollout.py --after "Interphex"    # resume past one
    python export_event_tables_rollout.py --only "ACHEMA" --force  # re-download

Long runs get killed at a variable cadence (see CLAUDE.md); just relaunch the
same command — the artifact guard makes it resume.
"""
import argparse
import os
import sys
import traceback

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.normpath(os.path.join(SCRIPT_DIR, "..", ".."))
sys.path.insert(0, os.path.join(REPO_ROOT, "automation", "build_automation"))
sys.path.insert(0, os.path.join(REPO_ROOT, "automation", "clay_sync"))

import browser_session  # noqa: E402
import clay_ui  # noqa: E402
import state_io  # noqa: E402
import export_event_tables as ex  # noqa: E402

STATE_PATH = os.path.join(SCRIPT_DIR, "export_tables_state.json")


def state_path_for(shard, shards):
    """Shards keep separate state files so two workers never clobber one
    another's write; merge_shards.py is the existing tool for combining them."""
    if shards <= 1:
        return STATE_PATH
    base, ext = os.path.splitext(STATE_PATH)
    return f"{base}_w{shard}{ext}"


def event_done(out_dir, tables, state_entry):
    """True when every requested table is either already downloaded non-empty
    or recorded ABSENT from this workbook."""
    absent = set((state_entry or {}).get("absent", []))
    for t in tables:
        if t in absent:
            continue
        p = os.path.join(out_dir, f"{t}.csv")
        if not (os.path.exists(p) and os.path.getsize(p) > 0):
            return False
    return True


def pending_tables(out_dir, tables, force):
    if force:
        return list(tables)
    out = []
    for t in tables:
        p = os.path.join(out_dir, f"{t}.csv")
        if os.path.exists(p) and os.path.getsize(p) > 0:
            continue
        out.append(t)
    return out


def build_scope(args):
    wbs = ex.load_workbooks()
    scope = sorted(wbs.items(), key=lambda kv: kv[1].lower())
    if args.only:
        wanted = [o.lower() for o in args.only]
        scope = [(i, n) for i, n in scope
                 if any(w == n.lower() or w in n.lower() for w in wanted)]
    if args.after:
        names = [n.lower() for _, n in scope]
        key = args.after.lower()
        idx = next((k for k, n in enumerate(names) if n == key or key in n), None)
        if idx is None:
            raise SystemExit(f"--after {args.after!r} matched no workbook in scope")
        scope = scope[idx + 1:]
    if args.shards > 1:
        scope = [x for k, x in enumerate(scope) if k % args.shards == args.shard]
    if args.limit:
        scope = scope[:args.limit]
    return scope


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out-root", default=ex.DEFAULT_OUT_ROOT)
    ap.add_argument("--tables", nargs="*", default=ex.TABLES)
    ex.add_scope_args(ap)
    ap.add_argument("--only", nargs="*", help="workbook name(s) or substrings")
    ap.add_argument("--after", help="resume just past this workbook")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--shards", type=int, default=1)
    ap.add_argument("--shard", type=int, default=0)
    ap.add_argument("--list", action="store_true", help="print scope, no browser")
    ap.add_argument("--dry-run", action="store_true",
                    help="print what each workbook still needs, no browser")
    ap.add_argument("--force", action="store_true",
                    help="re-download tables that already exist on disk")
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--download-timeout", type=int, default=300)
    args = ap.parse_args()

    ex.set_workbooks_json(args.workbooks)
    scope = build_scope(args)
    sp = state_path_for(args.shard, args.shards)
    state = state_io.load_json(sp)

    if args.list:
        print(f"[scope] {len(scope)} workbook(s)  (state: {sp})")
        for wb_id, name in scope:
            print(f"  {name}   {wb_id}")
        return 0

    if args.dry_run:
        todo = 0
        for wb_id, name in scope:
            out_dir = os.path.join(args.out_root, name)
            need = pending_tables(out_dir, ex.expand_tables(args.tables, name), args.force)
            absent = set(state.get(name, {}).get("absent", []))
            need = [t for t in need if t not in absent] if not args.force else need
            if need:
                todo += 1
                print(f"  [todo] {name}: {need}")
            else:
                print(f"  [skip] {name}: complete")
        print(f"\n[dry-run] {todo}/{len(scope)} workbook(s) need work")
        return 0

    print(f"[rollout] {len(scope)} workbook(s), shard {args.shard}/{args.shards}")
    print(f"[state]   {sp}")
    print(f"[out]     {args.out_root}\n")

    ok = skipped = failed = 0
    with browser_session.clay_page(headless=not args.headed) as page:
        for n, (wb_id, name) in enumerate(scope, 1):
            out_dir = os.path.join(args.out_root, name)
            entry = state.get(name, {})
            if not args.force and event_done(out_dir, ex.expand_tables(args.tables, name), entry):
                print(f"[{n}/{len(scope)}] {name}: already complete, skipping")
                skipped += 1
                continue

            need = pending_tables(out_dir, ex.expand_tables(args.tables, name), args.force)
            print(f"[{n}/{len(scope)}] {name}  ({wb_id})  needs {need}")
            try:
                page.goto(
                    f"https://app.clay.com/workspaces/{clay_ui.WORKSPACE_ID}"
                    f"/workbooks/{wb_id}",
                    wait_until="domcontentloaded", timeout=90000)
                tabs = ex.wait_for_tabs(page)
            except Exception as e:
                print(f"    !! could not open workbook: {type(e).__name__}: {e}")
                entry["error"] = f"open: {e}"
                state[name] = entry
                state_io.save_json(sp, state)
                failed += 1
                continue

            # Create the event folder only once we know this workbook actually
            # holds one of the requested tables. Creating it up front littered
            # the output directory with empty folders for every event that has
            # no table of this entity type (most events have no Sponsors_*).
            if any(t in tabs for t in need):
                os.makedirs(out_dir, exist_ok=True)
            done, absent, errs = list(entry.get("done", [])), [], []
            for table in need:
                if table not in tabs:
                    print(f"    [absent] {table}")
                    absent.append(table)
                    continue
                if not ex.focus_table(page, table):
                    print(f"    !! could not focus tab {table!r}")
                    errs.append(table)
                    continue
                try:
                    ex.export_table(page, table, out_dir,
                                    args.download_timeout * 1000)
                    if table not in done:
                        done.append(table)
                except Exception as e:
                    print(f"    !! {table}: {type(e).__name__}: {e}")
                    traceback.print_exc(limit=1)
                    errs.append(table)

            entry = {"workbook_id": wb_id, "done": done,
                     "absent": sorted(set(entry.get("absent", [])) | set(absent))}
            if errs:
                entry["failed"] = errs
            state[name] = entry
            state_io.save_json(sp, state)

            if errs:
                failed += 1
            else:
                ok += 1
            print(f"    [{name}] {len(done)} on disk, "
                  f"{len(entry['absent'])} absent, {len(errs)} failed")

    print(f"\n[rollout done] ok={ok} skipped={skipped} failed={failed}")
    if failed:
        print("[hint] re-run the same command; complete workbooks are skipped "
              "and only the missing tables are retried.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
