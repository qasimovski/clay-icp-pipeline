"""Fleet rollout: run build_workbook over every scrapers folder that has an
Exhibitors_normalized.csv (excluding Interphex, already built).

  python rollout.py                 # run everything not yet done
  python rollout.py --only NAME     # single event
  python rollout.py --limit N       # first N pending events

State in rollout_state.json; per-event logs in rollout_logs/. Healthy events
(existing workbook) run first; the 7 known-broken NEEDS_HUMAN folders are attempted
once at the end. Aborts after 3 consecutive failures (systemic problem).
"""
import argparse
import datetime
import json
import os
import socket
import sys
import tempfile
import time
import traceback

import browser_session
import build_workbook

SCRAPERS_ROOT = build_workbook.SCRAPERS_ROOT
STATE_PATH = os.path.join(browser_session.SCRIPT_DIR, "rollout_state.json")
LOG_DIR = os.path.join(browser_session.SCRIPT_DIR, "rollout_logs")
os.makedirs(LOG_DIR, exist_ok=True)

NEEDS_HUMAN = {"MEDICA", "Medtech Japan", "Pittcon", "SLAS Europe", "SLAS2026",
          "WHX Dubai", "World Health Expo Lagos"}
SKIP = {"Interphex"}


def _read_json(path):
    """Corrupt state must abort, not read as empty — an empty merge would mark
    every already-built event pending and rebuild the fleet (duplicate columns,
    re-run credits; see the CMEF collision in CLEANUP_NOTES.md)."""
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as e:
        raise SystemExit(
            f"state file {path!r} unreadable ({e}); restore or delete it "
            f"deliberately before re-running — continuing would rebuild "
            f"already-done events.")


def load_state(own_path=STATE_PATH):
    """Merge the legacy state file and every worker shard state file, so any
    worker (and the summary) sees all completed events."""
    merged = {}
    import glob as _glob
    paths = [STATE_PATH] + sorted(_glob.glob(
        os.path.join(browser_session.SCRIPT_DIR, "rollout_state_w*.json")))
    for p in paths:
        if os.path.exists(p):
            merged.update(_read_json(p))
    return merged


def save_state_entry(own_path, folder, entry):
    own = _read_json(own_path) if os.path.exists(own_path) else {}
    own[folder] = entry
    # Temp file + os.replace so a kill mid-write can't truncate the state.
    fd, tmp = tempfile.mkstemp(prefix=os.path.basename(own_path) + ".",
                               suffix=".tmp", dir=os.path.dirname(own_path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(own, fh, indent=1)
        os.replace(tmp, own_path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def discover_workbook_folders():
    events = []
    for d in sorted(os.listdir(SCRAPERS_ROOT)):
        if d in SKIP:
            continue
        if os.path.isfile(os.path.join(SCRAPERS_ROOT, d, "Exhibitors_normalized.csv")):
            events.append(d)
    healthy = [e for e in events if e not in NEEDS_HUMAN]
    tricky = [e for e in events if e in NEEDS_HUMAN]
    return healthy + tricky




def wait_online(max_wait=3600):
    """Block until app.clay.com is reachable (or max_wait expires)."""
    t0 = time.time()
    warned = False
    while time.time() - t0 < max_wait:
        try:
            socket.create_connection(("app.clay.com", 443), timeout=5).close()
            return True
        except OSError:
            if not warned:
                print("network down — waiting for connectivity...", flush=True)
                warned = True
            time.sleep(20)
    return False

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--shard", type=int, default=None)
    ap.add_argument("--shards", type=int, default=1)
    args = ap.parse_args()

    own_path = STATE_PATH if args.shard is None else os.path.join(
        browser_session.SCRIPT_DIR, f"rollout_state_w{args.shard}.json")
    state = load_state()
    events = [args.only] if args.only else discover_workbook_folders()
    if args.shard is not None:
        # shard over the FULL stable event list, not the pending snapshot —
        # otherwise two workers with slightly different snapshots overlap
        events = [e for i, e in enumerate(events) if i % args.shards == args.shard]
    pending = [e for e in events if state.get(e, {}).get("status") != "done"]
    if args.limit:
        pending = pending[: args.limit]
    print(f"pending events: {len(pending)}", flush=True)

    consecutive_failures = 0
    for i, folder in enumerate(pending):
        stamp = datetime.datetime.now().isoformat(timespec="seconds")
        print(f"\n=== [{i+1}/{len(pending)}] {folder}  ({stamp}) ===", flush=True)
        log_path = os.path.join(LOG_DIR, build_workbook.slug(folder) + ".log")
        if not wait_online():
            print("network down for 1h — aborting", flush=True)
            sys.exit(3)
        try:
            with open(log_path, "a", encoding="utf-8") as log:
                log.write(f"\n===== run {stamp} =====\n")
                build_workbook.build_workbook(folder, log)
            state[folder] = {"status": "done", "ts": stamp}
            save_state_entry(own_path, folder, state[folder])
            consecutive_failures = 0
            print(f"=== {folder}: DONE ===", flush=True)
        except Exception as e:
            err = f"{type(e).__name__}: {str(e)[:300]}"
            state[folder] = {"status": "failed", "error": err, "ts": stamp}
            save_state_entry(own_path, folder, state[folder])
            with open(log_path, "a", encoding="utf-8") as log:
                log.write(traceback.format_exc())
            if "INTERNET_DISCONNECTED" in err or "ERR_NAME_NOT_RESOLVED" in err:
                print(f"=== {folder}: NETWORK OUTAGE — will retry after "
                      f"connectivity returns ===", flush=True)
                state[folder] = {"status": "pending_network", "error": err,
                                 "ts": stamp}
                save_state_entry(own_path, folder, state[folder])
            else:
                consecutive_failures += 1
            print(f"=== {folder}: FAILED — {err} ===", flush=True)
        if consecutive_failures >= 3:
            print("3 consecutive failures — aborting (systemic problem)", flush=True)
            sys.exit(2)

    done = sum(1 for v in state.values() if v.get("status") == "done")
    failed = {k: v["error"] for k, v in state.items() if v.get("status") == "failed"}
    print(f"\nSUMMARY: {done} done, {len(failed)} failed")
    for k, v in failed.items():
        print(f"  ! {k}: {v}")


if __name__ == "__main__":
    main()
