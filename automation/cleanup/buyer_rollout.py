"""Fleet driver for the buyer-side builds -> per-event buyer people table.

Entity/ICP-agnostic (--entity/--icp): the source table and output people-table
name come from config/entity-types/<entity>.yaml, the segment/job-title config
from config/icps/<icp>/people_search.yaml. All run files are namespaced per
"<entity>_<icp>" slug so different entities never share state/targets/logs:

  buyer_targets_<slug>.json   scope: workbook ids that have this entity's source table
  buyer_state_<slug>.json     resumable per-event/-segment progress
  buyer_logs/run_<slug>.log   progress log

  python buyer_rollout.py --only "Pittcon"                 # default entity=exhibitors icp=labs
  python buyer_rollout.py --limit 5
  python buyer_rollout.py --entity sponsors --limit 5      # run the same flow for Sponsors
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
import buyer_builds as BU   # noqa: E402
import pipeline_config as PC  # noqa: E402

LOG_DIR = os.path.join(SCRIPT_DIR, "buyer_logs")
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
    # Parallel workers: --shards N launches N processes, each --shard i in 0..N-1.
    # Events are partitioned by index so no two workers touch the same event, and
    # each worker writes only its own buyer_state_<slug>_w{i}.json (never the
    # master), so there is no concurrent-writer clobbering. Merge with merge_shards.py.
    ap.add_argument("--shard", type=int, default=0)
    ap.add_argument("--shards", type=int, default=1)
    args = ap.parse_args()

    cfg = BU.configure(args.entity, args.icp)   # point the build at this entity/ICP
    slug = cfg.slug()
    manifest_path = os.path.join(SCRIPT_DIR, f"cols_manifest_{slug}.json")
    targets_path = os.path.join(SCRIPT_DIR, f"buyer_targets_{slug}.json")
    STATE = os.path.join(SCRIPT_DIR, f"buyer_state_{slug}.json")
    print(f"entity={cfg.entity} icp={cfg.icp} | source={cfg.main_table} "
          f"-> {cfg.buyer_people_table}", flush=True)

    names = {}
    for e in json.load(open(manifest_path, encoding="utf-8"))["workbooks"]:
        names[e["workbook_id"]] = e["workbook_name"]
    target_ids = load(targets_path, [])
    if not target_ids:
        raise SystemExit(
            f"no targets at {targets_path} — create it: a JSON list of workbook ids "
            f"that have a {cfg.main_table!r} table (see build_cols_manifest.py).")
    targets = [(w, names.get(w, w)) for w in target_ids]
    if args.only:
        targets = [(w, n) for (w, n) in targets if args.only in (w, n)]
        if not targets:
            raise SystemExit(f"--only {args.only!r} matched nothing")

    sharded = args.shards > 1
    if sharded:
        state_write = os.path.join(SCRIPT_DIR, f"buyer_state_{slug}_w{args.shard}.json")
        log_path = os.path.join(LOG_DIR, f"run_{slug}_w{args.shard}.log")
    else:
        state_write = STATE
        log_path = os.path.join(LOG_DIR, f"run_{slug}.log")

    # Read-view for skip/resume decisions = master overlaid by this shard's own
    # file (shard wins). Writes go only to `out` -> state_write.
    master = load(STATE, {})
    state = dict(master)
    out = load(state_write, {}) if sharded else state
    if sharded:
        state.update(out)

    # Disjoint partition by target index so no two shards ever share an event.
    part = [(w, n) for i, (w, n) in enumerate(targets) if i % args.shards == args.shard]
    # 'deferred' = big events that can't finish within one process lifetime;
    # skipped by normal batches, handled separately.
    pending = [(w, n) for (w, n) in part
               if state.get(w, {}).get("status") not in ("ok", "deferred")]
    if args.limit:
        pending = pending[: args.limit]

    done = sum(1 for v in state.values() if v.get("status") == "ok")
    print(f"[shard {args.shard}/{args.shards}] processing {len(pending)} of {len(part)} "
          f"in partition ({done} done overall)", flush=True)

    cf = 0   # consecutive non-network failures
    nf = 0   # network failures (transient DNS/connectivity blips)
    with common.clay_page(headless=not args.headed) as page, \
            open(log_path, "a", encoding="utf-8") as logf:
        def say(m):
            print(m, flush=True); logf.write(m + "\n"); logf.flush()
        stamp = datetime.datetime.now().isoformat(timespec="seconds")
        say(f"\n===== buyer run {stamp} =====")
        for i, (wid, name) in enumerate(pending):
            say(f"\n=== [{i+1}/{len(pending)}] {name} ===")
            prev = state.get(wid, {})
            done_segs = list(prev.get("segments_done", []))
            # Seed this shard's write-record with prior progress so a mid-run
            # persist never overlays an incomplete segments_done onto the master.
            out.setdefault(wid, dict(prev))
            if done_segs:
                say(f"  (resuming — {len(done_segs)} segments already done)")

            def on_segment(seg_name, created, _wid=wid):
                st = out.setdefault(_wid, {})
                st["status"] = "in_progress"; st["ts"] = stamp
                st["created_table"] = created
                sd = st.setdefault("segments_done", [])
                if seg_name not in sd:
                    sd.append(seg_name)
                json.dump(out, open(state_write, "w", encoding="utf-8"), indent=1, ensure_ascii=False)

            try:
                r = BU.run_buyer_event(page, wid, name, say,
                                       done_segments=done_segs, on_segment=on_segment)
                out[wid] = {"status": "ok", "ts": stamp, "detail": r,
                            "created_table": r["created_table"],
                            "segments_done": [s["segment"] for s in r["segments"]] + done_segs}
                json.dump(out, open(state_write, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
                cf = 0; nf = 0
                built = [s["segment"] for s in r["segments"] if s.get("saved")]
                say(f"EVENT_DONE {name} | table_created={r['created_table']} | "
                    f"segments_built={built}")
            except Exception as e:
                msg = str(e)
                net = any(s in msg for s in (
                    "ERR_NAME_NOT_RESOLVED", "ERR_INTERNET_DISCONNECTED",
                    "ERR_CONNECTION", "ERR_NETWORK", "ERR_TIMED_OUT",
                    "ERR_PROXY_CONNECTION_FAILED"))
                say(f"!! EXCEPTION on {name}: {msg[:200]}")
                logf.write(traceback.format_exc()); logf.flush()
                # Preserve any per-segment progress (segments_done / created_table)
                # so a transient error doesn't force a full restart + duplicate
                # people on the next resume.
                errst = out.get(wid, {})
                errst["status"] = "error"; errst["ts"] = stamp
                errst["error"] = msg[:300]
                out[wid] = errst
                json.dump(out, open(state_write, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
                try:
                    page.keyboard.press("Escape")
                except Exception:
                    pass
                # A transient DNS/connectivity blip shouldn't abort the whole
                # worker: pause and ride it out (errored events retry on relaunch).
                # Only abort on a sustained outage.
                if net:
                    nf += 1
                    say(f"  (network error — pausing 30s; net-fail {nf}/10)")
                    try:
                        page.wait_for_timeout(30000)
                    except Exception:
                        pass
                    if nf >= 10:
                        say("!! 10 network failures — aborting (outage)"); sys.exit(3)
                else:
                    cf += 1
                    if cf >= 3:
                        say("!! 3 consecutive failures — aborting"); sys.exit(2)


if __name__ == "__main__":
    main()
