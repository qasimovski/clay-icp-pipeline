"""Fleet driver to apply the two view filters (Side=Seller AND Send table data
has results) across in-scope workbooks (reuses cols_manifest.json). Resumable
via filters_state_all.json; the event clears+reapplies so re-runs are safe.

  python apply_view_filters_rollout.py --only "ASMS Annual Conference"
  python apply_view_filters_rollout.py --limit 20        # next 20 not-yet-done
  python apply_view_filters_rollout.py --dry-run --limit 20
"""

import argparse
import datetime
import json
import os
import sys
import traceback

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(SCRIPT_DIR), "build_automation"))

import browser_session                # noqa: E402
import apply_view_filters  # noqa: E402
import pipeline_config as pcfg  # noqa: E402

_SLUG = pcfg.load().slug()  # entity slug (e.g. sponsors_labs) namespaces the manifest
MANIFEST_PATH = os.path.join(SCRIPT_DIR, f"cols_manifest_{_SLUG}.json")
LOG_DIR = os.path.join(SCRIPT_DIR, "filters_logs")
os.makedirs(LOG_DIR, exist_ok=True)


def load_state(path):
    if os.path.exists(path):
        try:
            return json.load(open(path, encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_state(path, s):
    json.dump(s, open(path, "w", encoding="utf-8"), indent=1, ensure_ascii=False)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--ids-file", help="JSON list of workbook_ids to restrict to (a shard)")
    ap.add_argument("--state-suffix", default="all",
                    help="state file suffix -> filters_state_<suffix>.json")
    ap.add_argument("--shard", type=int, default=None)
    ap.add_argument("--shards", type=int, default=1)
    args = ap.parse_args()

    manifest = json.load(open(MANIFEST_PATH, encoding="utf-8"))
    wbs = manifest["workbooks"]
    if args.only:
        wbs = [e for e in wbs if args.only in (e["workbook_id"], e["workbook_name"])]
        if not wbs:
            raise SystemExit(f"--only {args.only!r} matched nothing")
    if args.ids_file:
        ids = set(json.load(open(args.ids_file, encoding="utf-8")))
        wbs = [e for e in wbs if e["workbook_id"] in ids]
    if args.shard is not None:
        wbs = [e for i, e in enumerate(wbs) if i % args.shards == args.shard]

    # a shard writes its own state file so concurrent workers don't race
    suffix = args.state_suffix if args.state_suffix != "all" else (
        f"w{args.shard}" if args.shard is not None else "all")
    state_path = os.path.join(SCRIPT_DIR, f"filters_state_{_SLUG}_{suffix}.json")
    state = load_state(state_path)
    pending = [e for e in wbs if state.get(e["workbook_id"], {}).get("status") != "ok"]
    if args.limit:
        pending = pending[: args.limit]

    tag = "dry" if args.dry_run else ("only" if args.only else suffix)
    log_path = os.path.join(LOG_DIR, tag + ".log")
    done = sum(1 for v in state.values() if v.get("status") == "ok")
    print(f"[{tag}] processing: {len(pending)} of {len(wbs)} (state has {done} done)",
          flush=True)

    cf = 0
    results = []
    with browser_session.clay_page(headless=not args.headed) as page, \
            open(log_path, "a", encoding="utf-8") as logf:
        def say(m):
            print(m, flush=True); logf.write(m + "\n"); logf.flush()
        stamp = datetime.datetime.now().isoformat(timespec="seconds")
        say(f"\n===== {tag} run {stamp} =====")
        for i, entry in enumerate(pending):
            say(f"\n--- [{i+1}/{len(pending)}] {entry['workbook_name']} ---")
            try:
                r = apply_view_filters.apply_filters(page, entry, args.dry_run, say)
                results.append(r)
                if r["status"] in ("ok", "dryrun"):
                    cf = 0
                    if not args.dry_run:
                        state[entry["workbook_id"]] = {"status": "ok", "ts": stamp,
                                                       "detail": r}
                        save_state(state_path, state)
                else:
                    cf += 1
                    if not args.dry_run:
                        state[entry["workbook_id"]] = {"status": r["status"], "ts": stamp,
                                                       "detail": r}
                        save_state(state_path, state)
            except Exception as e:
                cf += 1
                say(f"!! EXCEPTION on {entry['workbook_name']}: {str(e)[:200]}")
                logf.write(traceback.format_exc()); logf.flush()
                if not args.dry_run:
                    state[entry["workbook_id"]] = {"status": "error", "ts": stamp,
                                                   "error": str(e)[:300]}
                    save_state(state_path, state)
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
