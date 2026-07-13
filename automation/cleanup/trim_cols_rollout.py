"""Fleet driver for the Exhibitors_normalized column trim (delete everything
right of 'Normalized Country'). Mirrors cleanup_rollout.py: resumable per-shard
state, --dry-run, --only, --limit, --shard/--shards.

  python trim_cols_rollout.py --dry-run
  python trim_cols_rollout.py --only "Interphex"
  python trim_cols_rollout.py                 # single-threaded, all pending
  python trim_cols_rollout.py --shards 2 --shard 0   # (single-threaded proved
                                                     #  faster/reliable here)

State in cols_state_<tag>.json; logs in cols_logs/. Partial (transient flake)
does not abort; only exceptions/aborted count toward the 3-consecutive stop.
"""

import argparse
import datetime
import json
import os
import sys
import traceback

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(SCRIPT_DIR), "build_automation"))

import common          # noqa: E402
import trim_cols_event as T  # noqa: E402

MANIFEST_PATH = os.path.join(SCRIPT_DIR, "cols_manifest.json")
LOG_DIR = os.path.join(SCRIPT_DIR, "cols_logs")
os.makedirs(LOG_DIR, exist_ok=True)


def load_manifest():
    if not os.path.exists(MANIFEST_PATH):
        raise SystemExit(f"no manifest at {MANIFEST_PATH}; run build_cols_manifest.py")
    return json.load(open(MANIFEST_PATH, encoding="utf-8"))


def state_path(tag):
    return os.path.join(SCRIPT_DIR, f"cols_state_{tag}.json")


def state_tag(args):
    if args.dry_run:
        return "dry"
    if args.only:
        return "only"
    if args.shard is not None:
        return f"w{args.shard}"
    return "all"


def load_state(p):
    if os.path.exists(p):
        try:
            return json.load(open(p, encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_state(p, s):
    json.dump(s, open(p, "w", encoding="utf-8"), indent=1, ensure_ascii=False)


def select(wbs, args):
    if args.only:
        sel = [e for e in wbs if args.only in (e["workbook_id"], e["workbook_name"])]
        if not sel:
            raise SystemExit(f"--only {args.only!r} matched nothing")
        return sel
    if args.shard is not None:
        return [e for i, e in enumerate(wbs) if i % args.shards == args.shard]
    return wbs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--shard", type=int, default=None)
    ap.add_argument("--shards", type=int, default=1)
    ap.add_argument("--only")
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--limit", type=int)
    args = ap.parse_args()

    manifest = load_manifest()
    sel = select(manifest["workbooks"], args)
    sp = state_path(state_tag(args))
    state = {} if args.dry_run else load_state(sp)
    pending = [e for e in sel if state.get(e["workbook_id"], {}).get("status") != "ok"]
    if args.limit:
        pending = pending[: args.limit]

    tag = ("DRY" if args.dry_run else
           (f"shard {args.shard}/{args.shards}" if args.shard is not None else "all"))
    log_path = os.path.join(LOG_DIR, ("dryrun" if args.dry_run else
                                      (f"shard{args.shard}" if args.shard is not None
                                       else "all")) + ".log")
    print(f"[{tag}] pending: {len(pending)} of {len(sel)} selected", flush=True)

    cf = 0
    results = []
    with common.clay_page(headless=not args.headed) as page, \
            open(log_path, "a", encoding="utf-8") as logf:
        def say(m):
            print(m, flush=True); logf.write(m + "\n"); logf.flush()

        stamp = datetime.datetime.now().isoformat(timespec="seconds")
        say(f"\n===== {tag} run {stamp} =====")
        for i, entry in enumerate(pending):
            say(f"\n--- [{i+1}/{len(pending)}] {entry['workbook_name']} ---")
            try:
                r = T.clean_workbook_cols(page, entry, args.dry_run, say)
                results.append(r)
                if r["status"] in ("ok", "dryrun"):
                    cf = 0
                    if not args.dry_run:
                        state[entry["workbook_id"]] = {"status": "ok", "ts": stamp,
                                                       "deleted": len(r.get("deleted", []))}
                        save_state(sp, state)
                elif r["status"] == "partial":
                    cf = 0
                    if not args.dry_run:
                        state[entry["workbook_id"]] = {"status": "partial", "ts": stamp,
                                                       "detail": r}
                        save_state(sp, state)
                else:  # aborted
                    cf += 1
                    if not args.dry_run:
                        state[entry["workbook_id"]] = {"status": r["status"], "ts": stamp,
                                                       "detail": r}
                        save_state(sp, state)
            except Exception as e:
                cf += 1
                say(f"!! EXCEPTION on {entry['workbook_name']}: {str(e)[:200]}")
                logf.write(traceback.format_exc()); logf.flush()
                if not args.dry_run:
                    state[entry["workbook_id"]] = {"status": "error", "ts": stamp,
                                                   "error": str(e)[:300]}
                    save_state(sp, state)
                try:
                    page.keyboard.press("Escape")
                except Exception:
                    pass
            if cf >= 3:
                say("!! 3 consecutive failures — aborting (systemic problem)")
                sys.exit(2)

        if args.dry_run:
            tot = sum(len(r.get("to_delete", [])) for r in results)
            say(f"\nDRY-RUN SUMMARY: {len(results)} workbooks, {tot} columns would be deleted")
        else:
            ok = sum(1 for r in results if r["status"] == "ok")
            bad = [r for r in results if r["status"] != "ok"]
            say(f"\nSUMMARY: {ok} ok, {len(bad)} not-ok")
            for r in bad:
                say(f"  ! {r['workbook_name']}: {r['status']} remaining={r.get('remaining')}")


if __name__ == "__main__":
    main()
