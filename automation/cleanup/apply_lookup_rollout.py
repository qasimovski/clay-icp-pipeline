"""Fleet driver to apply + RUN "Exhibitors - Lookup & Send Table Data - v1"
across in-scope workbooks (reuses cols_manifest.json — 71 in-scope, 8 protected
already excluded). Idempotent (event skips workbooks that already have the
'Send table data' column).

  python apply_lookup_rollout.py --only "ASMS Annual Conference"
  python apply_lookup_rollout.py --limit 10        # next 10 not-yet-applied
  python apply_lookup_rollout.py --dry-run --limit 10
"""

import argparse
import datetime
import json
import os
import sys
import traceback

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(SCRIPT_DIR), "build_automation"))

import common               # noqa: E402
import apply_lookup_event as A  # noqa: E402
import pipeline_config as _PC  # noqa: E402

_SLUG = _PC.load().slug()  # entity slug namespaces manifest + state (shared workbooks)
MANIFEST_PATH = os.path.join(SCRIPT_DIR, f"cols_manifest_{_SLUG}.json")
LOG_DIR = os.path.join(SCRIPT_DIR, "lookup_logs")
os.makedirs(LOG_DIR, exist_ok=True)


def state_path(tag):
    return os.path.join(SCRIPT_DIR, f"lookup_state_{_SLUG}_{tag}.json")


def load_state(p):
    if os.path.exists(p):
        try:
            return json.load(open(p, encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_state(p, s):
    json.dump(s, open(p, "w", encoding="utf-8"), indent=1, ensure_ascii=False)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--shard", type=int, default=None)
    ap.add_argument("--shards", type=int, default=1)
    args = ap.parse_args()

    manifest = json.load(open(MANIFEST_PATH, encoding="utf-8"))
    wbs = manifest["workbooks"]
    if args.only:
        wbs = [e for e in wbs if args.only in (e["workbook_id"], e["workbook_name"])]
        if not wbs:
            raise SystemExit(f"--only {args.only!r} matched nothing")
    if args.shard is not None:
        wbs = [e for i, e in enumerate(wbs) if i % args.shards == args.shard]
    # Per-shard state files avoid a read-modify-write race between concurrent
    # shards; a non-sharded run uses the shared 'all' state. Either way the real
    # idempotency guard is the event-level 'Send table data' column check, so a
    # missed state entry only causes a harmless re-skip.
    sp = state_path("all" if args.shard is None else f"w{args.shard}")
    state = load_state(sp)
    pending = [e for e in wbs if state.get(e["workbook_id"], {}).get("status") != "ok"]
    if args.limit:
        pending = pending[: args.limit]

    tag = "dry" if args.dry_run else ("only" if args.only else "all")
    log_path = os.path.join(LOG_DIR, tag + ".log")
    print(f"[{tag}] processing: {len(pending)} of {len(wbs)} "
          f"(state has {sum(1 for v in state.values() if v.get('status')=='ok')} done)",
          flush=True)

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
                r = A.apply_lookup(page, entry, args.dry_run, say)
                results.append(r)
                if r["status"] in ("ok", "dryrun"):
                    cf = 0
                    if not args.dry_run:
                        state[entry["workbook_id"]] = {"status": "ok", "ts": stamp,
                                                       "detail": r}
                        save_state(sp, state)
                else:
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
                say("!! 3 consecutive failures — aborting"); sys.exit(2)
        ok = sum(1 for r in results if r["status"] in ("ok", "dryrun"))
        bad = [r for r in results if r["status"] not in ("ok", "dryrun")]
        say(f"\nSUMMARY: {ok} ok, {len(bad)} not-ok")
        for r in bad:
            say(f"  ! {r['workbook_name']}: {r['status']}")


if __name__ == "__main__":
    main()
