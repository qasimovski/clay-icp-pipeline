"""Fleet driver for the 3 "Find people at these companies" builds -> per-event
"Sellers - People" table. Scope = people_targets.json (68 in-scope; ACHEMA /
ELRIG / Lab Indonesia / Asia Labex excluded). Resumable via people_state_all.json.

  python people_rollout.py --only "Middle East Coatings Show"
  python people_rollout.py --limit 5
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

MANIFEST = os.path.join(SCRIPT_DIR, "cols_manifest.json")
TARGETS = os.path.join(SCRIPT_DIR, "people_targets.json")
STATE = os.path.join(SCRIPT_DIR, "people_state_all.json")
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
    ap.add_argument("--only")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--headed", action="store_true")
    args = ap.parse_args()

    wbs = {e["workbook_id"]: e["workbook_name"]
           for e in json.load(open(MANIFEST, encoding="utf-8"))["workbooks"]}
    target_ids = load(TARGETS, [])
    targets = [(wid, wbs.get(wid, wid)) for wid in target_ids]
    if args.only:
        targets = [(w, n) for (w, n) in targets if args.only in (w, n)]
        if not targets:
            raise SystemExit(f"--only {args.only!r} matched nothing")

    state = load(STATE, {})
    pending = [(w, n) for (w, n) in targets if state.get(w, {}).get("status") != "ok"]
    if args.limit:
        pending = pending[: args.limit]

    done = sum(1 for v in state.values() if v.get("status") == "ok")
    print(f"processing {len(pending)} of {len(targets)} (state has {done} done)", flush=True)
    log_path = os.path.join(LOG_DIR, "run.log")

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
