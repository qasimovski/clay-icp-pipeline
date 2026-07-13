"""Trigger runs on v1-applied workbooks (Actions -> Run N rows). Targets only
the workbooks marked applied in v1_state_all.json; resumable via v1run_state.

  python run_v1_rollout.py --dry-run
  python run_v1_rollout.py
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
import run_v1_event as R  # noqa: E402

MANIFEST_PATH = os.path.join(SCRIPT_DIR, "cols_manifest.json")
APPLIED_STATE = os.path.join(SCRIPT_DIR, "v1_state_all.json")   # applied set
LOG_DIR = os.path.join(SCRIPT_DIR, "v1run_logs")
os.makedirs(LOG_DIR, exist_ok=True)


def state_path(tag):
    return os.path.join(SCRIPT_DIR, f"v1run_state_{tag}.json")


def load(p):
    if os.path.exists(p):
        try:
            return json.load(open(p, encoding="utf-8"))
        except Exception:
            pass
    return {}


def save(p, s):
    json.dump(s, open(p, "w", encoding="utf-8"), indent=1, ensure_ascii=False)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--headed", action="store_true")
    args = ap.parse_args()

    manifest = json.load(open(MANIFEST_PATH, encoding="utf-8"))
    applied = load(APPLIED_STATE)
    applied_ids = {wid for wid, st in applied.items() if st.get("status") == "ok"}
    wbs = [e for e in manifest["workbooks"] if e["workbook_id"] in applied_ids]
    if args.only:
        wbs = [e for e in wbs if args.only in (e["workbook_id"], e["workbook_name"])]

    tag = "dry" if args.dry_run else "all"
    sp = state_path(tag)
    st = {} if args.dry_run else load(sp)
    pending = [e for e in wbs if st.get(e["workbook_id"], {}).get("status") not in ("ok", "skip")]
    if args.limit:
        pending = pending[: args.limit]

    log_path = os.path.join(LOG_DIR, tag + ".log")
    print(f"[{tag}] applied={len(wbs)} to-run={len(pending)}", flush=True)

    cf = 0
    with common.clay_page(headless=not args.headed) as page, \
            open(log_path, "a", encoding="utf-8") as logf:
        def say(m):
            print(m, flush=True); logf.write(m + "\n"); logf.flush()
        stamp = datetime.datetime.now().isoformat(timespec="seconds")
        say(f"\n===== {tag} run {stamp} =====")
        for i, e in enumerate(pending):
            say(f"\n--- [{i+1}/{len(pending)}] {e['workbook_name']} ---")
            try:
                r = R.run_v1(page, e, args.dry_run, say)
                if r["status"] in ("ok", "skip", "dryrun"):
                    cf = 0
                    if not args.dry_run:
                        st[e["workbook_id"]] = {"status": r["status"], "ts": stamp}
                        save(sp, st)
                else:
                    cf += 1
                    if not args.dry_run:
                        st[e["workbook_id"]] = {"status": r["status"], "ts": stamp, "detail": r}
                        save(sp, st)
            except Exception as ex:
                cf += 1
                say(f"!! EXCEPTION on {e['workbook_name']}: {str(ex)[:200]}")
                logf.write(traceback.format_exc()); logf.flush()
                if not args.dry_run:
                    st[e["workbook_id"]] = {"status": "error", "ts": stamp, "error": str(ex)[:300]}
                    save(sp, st)
                try:
                    page.keyboard.press("Escape")
                except Exception:
                    pass
            if cf >= 3:
                say("!! 3 consecutive failures — aborting"); sys.exit(2)
    print("run pass complete", flush=True)


if __name__ == "__main__":
    main()
