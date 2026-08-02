"""Fleet driver for the Competitive Events cleanup.

Reads cleanup_manifest.json and cleans each in-scope workbook (delete pipeline
byproducts, keep only the normalized source table[s]). Shard across N parallel
processes, each its own headless browser reusing the saved Clay session.

  # 1) inspect what WOULD be deleted (no deletions), whole fleet:
  python delete_byproduct_tables_rollout.py --dry-run

  # 2) real run, 4 workers (run each in its own terminal):
  python delete_byproduct_tables_rollout.py --shards 4 --shard 0
  python delete_byproduct_tables_rollout.py --shards 4 --shard 1
  python delete_byproduct_tables_rollout.py --shards 4 --shard 2
  python delete_byproduct_tables_rollout.py --shards 4 --shard 3

  # target one workbook (validation):
  python delete_byproduct_tables_rollout.py --only "Interphex" --dry-run

State per shard in cleanup_state_w{shard}.json (resumable, like build rollout).
Per-run logs in cleanup_logs/. Aborts a shard after 3 consecutive workbook
failures (systemic problem — bad session, Clay down, selector drift).
"""

import argparse
import datetime
import json
import os
import sys
import traceback

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(SCRIPT_DIR), "build_automation"))

import browser_session          # noqa: E402  (clay_page)
import delete_byproduct_tables   # noqa: E402
import state_io           # noqa: E402  (atomic, fail-loud state files)

MANIFEST_PATH = os.path.join(SCRIPT_DIR, "cleanup_manifest.json")
FOLDER_PATH = os.path.join(SCRIPT_DIR, "competitive_events_workbooks.json")
LOG_DIR = os.path.join(SCRIPT_DIR, "cleanup_logs")
os.makedirs(LOG_DIR, exist_ok=True)


def load_manifest():
    if not os.path.exists(MANIFEST_PATH):
        raise SystemExit(f"no manifest at {MANIFEST_PATH}; run build_cleanup_manifest.py")
    return json.load(open(MANIFEST_PATH, encoding="utf-8"))


def state_path(tag):
    return os.path.join(SCRIPT_DIR, f"cleanup_state_{tag}.json")


def state_tag(args):
    if args.dry_run:
        return "dry"
    if args.only:
        return "only"
    if args.shard is not None:
        return f"w{args.shard}"
    return "all"


def load_state(path):
    return state_io.load_json(path)


def save_state(path, state):
    state_io.save_json(path, state)


def folder_scope(workbooks, dry_run, no_folder_scope):
    """Restrict to workbooks whose id is in the Competitive Events folder set
    (from folder_scope.py). Hard guarantee that nothing outside the folder is
    ever cleaned. Required for real runs unless explicitly disabled."""
    if no_folder_scope:
        return workbooks
    if not os.path.exists(FOLDER_PATH):
        if dry_run:
            print("WARN: no competitive_events_workbooks.json — folder scope not "
                  "enforced for this dry run (run folder_scope.py before a real run)")
            return workbooks
        raise SystemExit("refusing real run without folder scope: run "
                         "folder_scope.py first (or pass --no-folder-scope)")
    folder_ids = set(json.load(open(FOLDER_PATH, encoding="utf-8")))
    keep = [e for e in workbooks if e["workbook_id"] in folder_ids]
    dropped = [e["workbook_name"] for e in workbooks
               if e["workbook_id"] not in folder_ids]
    if dropped:
        print(f"folder-scope excluded {len(dropped)} manifest workbook(s) not in "
              f"Competitive Events: {dropped}")
    return keep


def select(workbooks, args):
    if args.only:
        sel = [e for e in workbooks
               if args.only in (e["workbook_id"], e["workbook_name"])]
        if not sel:
            raise SystemExit(f"--only {args.only!r} matched no manifest workbook")
        return sel
    if args.shard is not None:
        return [e for i, e in enumerate(workbooks) if i % args.shards == args.shard]
    return workbooks


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="log what would be deleted; delete nothing")
    ap.add_argument("--shard", type=int, default=None)
    ap.add_argument("--shards", type=int, default=1)
    ap.add_argument("--only", help="single workbook by id or exact name")
    ap.add_argument("--headed", action="store_true", help="show the browser")
    ap.add_argument("--limit", type=int, help="first N pending workbooks")
    ap.add_argument("--no-folder-scope", action="store_true",
                    help="skip the Competitive Events folder intersection (unsafe)")
    args = ap.parse_args()

    manifest = load_manifest()
    workbooks = folder_scope(manifest["workbooks"], args.dry_run, args.no_folder_scope)
    sel = select(workbooks, args)

    sp = state_path(state_tag(args))
    state = {} if args.dry_run else load_state(sp)
    pending = [e for e in sel if state.get(e["workbook_id"], {}).get("status") != "ok"]
    if args.limit:
        pending = pending[: args.limit]

    tag = ("DRY" if args.dry_run else
           (f"shard {args.shard}/{args.shards}" if args.shard is not None else "all"))
    log_path = os.path.join(
        LOG_DIR, ("dryrun" if args.dry_run else f"shard{args.shard}") + ".log")
    print(f"[{tag}] pending workbooks: {len(pending)} "
          f"(of {len(sel)} selected, {len(workbooks)} total)", flush=True)

    consecutive_failures = 0
    results = []
    with browser_session.clay_page(headless=not args.headed) as page, \
            open(log_path, "a", encoding="utf-8") as logf:
        def say(msg):
            print(msg, flush=True)
            logf.write(msg + "\n")
            logf.flush()

        stamp = datetime.datetime.now().isoformat(timespec="seconds")
        say(f"\n===== {tag} run {stamp} =====")
        for i, entry in enumerate(pending):
            say(f"\n--- [{i+1}/{len(pending)}] {entry['workbook_name']} ---")
            try:
                r = delete_byproduct_tables.clean_workbook(page, entry, args.dry_run, say)
                results.append(r)
                if r["status"] in ("ok", "dryrun"):
                    consecutive_failures = 0
                    if not args.dry_run:
                        state[entry["workbook_id"]] = {
                            "status": "ok", "ts": stamp,
                            "deleted": len(r.get("deleted", []))}
                        save_state(sp, state)
                elif r["status"] == "partial":
                    # transient table-menu flakes leave a few stragglers; the
                    # workbook stays pending and a resume pass mops them up. Not
                    # a systemic failure — don't count it toward the abort.
                    consecutive_failures = 0
                    if not args.dry_run:
                        state[entry["workbook_id"]] = {"status": "partial",
                                                       "ts": stamp, "detail": r}
                        save_state(sp, state)
                else:  # aborted (missing keep) — this SHOULD NOT happen; escalate
                    consecutive_failures += 1
                    if not args.dry_run:
                        state[entry["workbook_id"]] = {"status": r["status"],
                                                       "ts": stamp, "detail": r}
                        save_state(sp, state)
            except Exception as e:
                consecutive_failures += 1
                say(f"!! EXCEPTION on {entry['workbook_name']}: {str(e)[:200]}")
                logf.write(traceback.format_exc())
                logf.flush()
                if not args.dry_run:
                    state[entry["workbook_id"]] = {"status": "error",
                                                   "ts": stamp, "error": str(e)[:300]}
                    save_state(sp, state)
                try:
                    page.keyboard.press("Escape")
                except Exception:
                    pass
            if consecutive_failures >= 3:
                say("!! 3 consecutive failures — aborting (systemic problem)")
                sys.exit(2)

        # summary
        if args.dry_run:
            tot = sum(len(r.get("to_delete", [])) for r in results)
            say(f"\nDRY-RUN SUMMARY: {len(results)} workbooks, "
                f"{tot} tables would be deleted")
        else:
            ok = sum(1 for r in results if r["status"] == "ok")
            bad = [r for r in results if r["status"] != "ok"]
            say(f"\nSUMMARY: {ok} ok, {len(bad)} not-ok")
            for r in bad:
                say(f"  ! {r['workbook_name']}: {r['status']} "
                    f"remaining={r.get('remaining')} failed={r.get('failed')}")


if __name__ == "__main__":
    main()
