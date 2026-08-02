"""Fleet driver for the "Find people at these companies" seller builds -> per-event
seller people table.

Entity/ICP-agnostic (--entity/--icp): the source table + output people-table name
come from config/entity-types/<entity>.yaml, the build specs (job titles, seller
Location list) from config/icps/<icp>/people_search.yaml. Run files are namespaced
per "<entity>_<icp>" slug so entities never share state/targets/logs:

  people_targets_<slug>.json   scope: workbook ids that have this entity's source table
  people_state_<slug>.json     resumable per-event progress
  people_logs/run_<slug>.log   progress log

  python people_rollout.py --only "Middle East Coatings Show"   # default exhibitors/labs
  python people_rollout.py --limit 5
  python people_rollout.py --entity sponsors --limit 5          # same flow for Sponsors
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
import people_builds as P   # noqa: E402
import pipeline_config as PC  # noqa: E402

LOG_DIR = os.path.join(SCRIPT_DIR, "people_logs")
os.makedirs(LOG_DIR, exist_ok=True)


def load(p, d):
    if os.path.exists(p):
        try:
            return json.load(open(p, encoding="utf-8"))
        except Exception:
            pass
    return d


def main():
    ap = argparse.ArgumentParser()
    PC.add_cli_args(ap)   # --entity / --icp
    ap.add_argument("--only")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--shard", type=int, default=None)
    ap.add_argument("--shards", type=int, default=1)
    args = ap.parse_args()

    cfg = P.configure(args.entity, args.icp)   # point the build at this entity/ICP
    slug = cfg.slug()
    manifest_path = os.path.join(SCRIPT_DIR, f"cols_manifest_{slug}.json")
    targets_path = os.path.join(SCRIPT_DIR, f"people_targets_{slug}.json")
    # Per-shard state so concurrent workers (disjoint events) never race on one file.
    _sfx = f"_w{args.shard}" if args.shard is not None else ""
    STATE = os.path.join(SCRIPT_DIR, f"people_state_{slug}{_sfx}.json")
    log_path = os.path.join(LOG_DIR, f"run_{slug}{_sfx}.log")
    print(f"entity={cfg.entity} icp={cfg.icp} | source={cfg.main_table} "
          f"-> {cfg.seller_people_table}", flush=True)

    wbs = {e["workbook_id"]: e["workbook_name"]
           for e in json.load(open(manifest_path, encoding="utf-8"))["workbooks"]}
    target_ids = load(targets_path, [])
    if not target_ids:
        raise SystemExit(
            f"no targets at {targets_path} — create it: a JSON list of workbook ids "
            f"that have a {cfg.main_table!r} table (see build_cols_manifest.py).")
    targets = [(wid, wbs.get(wid, wid)) for wid in target_ids]
    if args.only:
        targets = [(w, n) for (w, n) in targets if args.only in (w, n)]
        if not targets:
            raise SystemExit(f"--only {args.only!r} matched nothing")
    if args.shard is not None:
        targets = [t for i, t in enumerate(targets) if i % args.shards == args.shard]

    state = load(STATE, {})
    # An event is "done" if it is ok in this shard's state OR in the base
    # (non-sharded) state — so events completed via a plain/--only run (e.g. the
    # WCF test) are never re-processed by a shard and appended twice.
    base_state = load(os.path.join(SCRIPT_DIR, f"people_state_{slug}.json"), {}) \
        if args.shard is not None else {}
    def _done(w):
        return state.get(w, {}).get("status") == "ok" or base_state.get(w, {}).get("status") == "ok"
    pending = [(w, n) for (w, n) in targets if not _done(w)]
    if args.limit:
        pending = pending[: args.limit]

    done = len(targets) - len(pending)
    print(f"processing {len(pending)} of {len(targets)} (state has {done} done)", flush=True)

    cf = 0
    with common.clay_page(headless=not args.headed) as page, \
            open(log_path, "a", encoding="utf-8") as logf:
        def say(m):
            print(m, flush=True); logf.write(m + "\n"); logf.flush()
        stamp = datetime.datetime.now().isoformat(timespec="seconds")
        say(f"\n===== people run {stamp} =====")
        for i, (wid, name) in enumerate(pending):
            say(f"\n=== [{i+1}/{len(pending)}] {name} ===")
            try:
                r = P.run_event(page, wid, name, say)
                state[wid] = {"status": "ok", "ts": stamp, "detail": r}
                json.dump(state, open(STATE, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
                cf = 0
                saved = [b["build"] for b in r["builds"] if b["saved"]]
                say(f"EVENT_DONE {name} | table_created={r['created_table']} | "
                    f"builds_saved={saved}")
            except Exception as e:
                cf += 1
                say(f"!! EXCEPTION on {name}: {str(e)[:200]}")
                logf.write(traceback.format_exc()); logf.flush()
                state[wid] = {"status": "error", "ts": stamp, "error": str(e)[:300]}
                json.dump(state, open(STATE, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
                try:
                    page.keyboard.press("Escape")
                except Exception:
                    pass
            if cf >= 3:
                say("!! 3 consecutive failures — aborting"); sys.exit(2)


if __name__ == "__main__":
    main()
