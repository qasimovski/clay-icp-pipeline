"""Fleet driver: apply the "Find LinkedIn and Enrich Person" template to
every Competitive Events workbook's "Speakers_normalized" table.

Scope = speakers_normalized_workbooks.json (the 43 workbooks that have a
Speakers_normalized table, built via list_other_sources_workbooks.py-style
discovery — see automation/cleanup/save_speaker_wbs equivalent). ChinaBio and
Digi-tech Pharma & AI are pre-seeded as done in the state file (user applied
this template to them manually before this rollout existed).

Waits for each table's run to reach 100% completion (see
apply_findlinkedin.py's _wait_for_full_completion) before considering
it done. Idempotent per workbook via the "Enrich person" signature column.

  python apply_findlinkedin_rollout.py --only "HIMSS"
  python apply_findlinkedin_rollout.py --limit 10
  python apply_findlinkedin_rollout.py --dry-run --limit 5
"""

import argparse
import datetime
import json
import os
import sys
import traceback

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(SCRIPT_DIR), "build_automation"))

import browser_session                        # noqa: E402
import apply_findlinkedin  # noqa: E402
import state_io           # noqa: E402  (atomic, fail-loud state files)

SCOPE_PATH = os.path.join(SCRIPT_DIR, "speakers_normalized_workbooks.json")
STATE_PATH = os.path.join(SCRIPT_DIR, "findlinkedin_state.json")
LOG_DIR = os.path.join(SCRIPT_DIR, "findlinkedin_logs")
os.makedirs(LOG_DIR, exist_ok=True)

TABLE = "Speakers_normalized"
_FINAL_STATUSES = ("ok", "dryrun", "no_table")


def load(p, d):
    return state_io.load_json(p, d)


def save(p, s):
    state_io.save_json(p, s)


def workbook_done(state, wid):
    return state.get(wid, {}).get("status") in _FINAL_STATUSES


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only", help="workbook id or exact name")
    ap.add_argument("--limit", type=int, help="max workbooks to process this run")
    ap.add_argument("--headed", action="store_true")
    args = ap.parse_args()

    wbs = json.load(open(SCOPE_PATH, encoding="utf-8"))
    # Sorted by name, not dict insertion order: --shard partitions and
    # --after cursors are computed from this list, so regenerating the
    # scope JSON must not re-partition the fleet under running workers.
    scope = [{"workbook_id": wid, "workbook_name": name} for wid, name in wbs.items()]
    scope.sort(key=lambda e: e["workbook_name"])
    if args.only:
        scope = [e for e in scope if args.only in (e["workbook_id"], e["workbook_name"])]
        if not scope:
            raise SystemExit(f"--only {args.only!r} matched nothing in scope")

    state = load(STATE_PATH, {})
    pending = [e for e in scope if not workbook_done(state, e["workbook_id"])]
    if args.limit:
        pending = pending[: args.limit]

    tag = "dry" if args.dry_run else ("only" if args.only else "all")
    log_path = os.path.join(LOG_DIR, f"run_{tag}.log")
    done_ct = sum(1 for e in scope if workbook_done(state, e["workbook_id"]))
    print(f"[{tag}] scope={len(scope)} pending={len(pending)} already_done={done_ct}",
          flush=True)

    cf = 0
    with browser_session.clay_page(headless=not args.headed) as page, \
            open(log_path, "a", encoding="utf-8") as logf:
        def say(m):
            print(m, flush=True); logf.write(m + "\n"); logf.flush()
        stamp = datetime.datetime.now().isoformat(timespec="seconds")
        say(f"\n===== {tag} run {stamp} =====")
        for i, entry in enumerate(pending):
            wid = entry["workbook_id"]
            say(f"\n--- [{i+1}/{len(pending)}] {entry['workbook_name']} ---")
            r = None
            last_exc = None
            for dns_try in range(3):
                try:
                    r = apply_findlinkedin.apply_findlinkedin(page, entry, TABLE, args.dry_run, say)
                    last_exc = None
                    break
                except Exception as e:
                    last_exc = e
                    if "ERR_NAME_NOT_RESOLVED" in str(e) and dns_try < 2:
                        say(f"   DNS blip, pausing 30s (try {dns_try+1}/3)")
                        try:
                            page.keyboard.press("Escape")
                        except Exception:
                            pass
                        page.wait_for_timeout(30000)
                        continue
                    break
            if last_exc is not None:
                say(f"!! EXCEPTION on {entry['workbook_name']}: {str(last_exc)[:200]}")
                logf.write(traceback.format_exc()); logf.flush()
                cf += 1
                if not args.dry_run:
                    state[wid] = {"status": "error", "error": str(last_exc)[:300]}
                    save(STATE_PATH, state)
                try:
                    page.keyboard.press("Escape")
                except Exception:
                    pass
            else:
                if not args.dry_run:
                    state[wid] = r
                    save(STATE_PATH, state)
                cf = cf + 1 if r["status"] not in ("ok", "dryrun", "no_table") else 0
            if cf >= 3:
                say("!! 3 consecutive failures — aborting"); sys.exit(2)
        say("\nSUMMARY: see findlinkedin_state.json for per-workbook results")


if __name__ == "__main__":
    main()
