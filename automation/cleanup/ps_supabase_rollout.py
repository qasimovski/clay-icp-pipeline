"""Apply "Companies - Supabase" to every remaining Companies table in the
Product & Services folder, one workbook at a time.

Per the user: the API account must be set to "Qasim - Labs" on each table (it
defaults to another account), and Name must point at the table's own Name column.

Skipped by instruction (already done by the user):
  Cleanroom Technology, Chemicals & Reagents, Digital & AI Services

Guard: a table is skipped if Clay already reports the template's columns
("HTTP API" / "Is New"), checked via the CLI and failing closed — so a rerun
after an interrupted batch never applies twice.

  python ps_supabase_rollout.py --limit 3
  python ps_supabase_rollout.py --list
"""

import argparse
import datetime
import json
import os
import sys
import traceback

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
sys.path.insert(0, os.path.join(os.path.dirname(SCRIPT_DIR), "build_automation"))

import browser_session                              # noqa: E402
import apply_companies_supabase  # noqa: E402

AUDIT = os.path.join(SCRIPT_DIR, "product_services_companies.json")
STATE = os.path.join(SCRIPT_DIR, "ps_supabase_state.json")
LOG_DIR = os.path.join(SCRIPT_DIR, "ps_supabase_logs")
os.makedirs(LOG_DIR, exist_ok=True)

SKIP = {"Cleanroom Technology", "Chemicals & Reagents", "Digital & AI Services"}
MARKER = ("HTTP API", "Is New")
DONE = ("ok", "already_applied", "dryrun")


def load(p, d):
    if os.path.exists(p):
        try:
            return json.load(open(p, encoding="utf-8"))
        except Exception:
            pass
    return d


def save(p, s):
    json.dump(s, open(p, "w", encoding="utf-8"), indent=1, ensure_ascii=False)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=8)
    ap.add_argument("--only")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--skip-run", action="store_true")
    ap.add_argument("--headed", action="store_true")
    args = ap.parse_args()

    audit = json.load(open(AUDIT, encoding="utf-8"))
    state = load(STATE, {})

    events = [(wb, rec) for wb, rec in audit.items() if wb not in SKIP]
    events.sort(key=lambda kv: kv[1].get("rows") or 0)
    pending = [(wb, rec) for wb, rec in events
               if state.get(wb, {}).get("status") not in DONE]

    if args.only:
        pending = [(wb, rec) for wb, rec in pending if wb == args.only]
    if args.list:
        for wb, rec in pending:
            print(f"  {wb:50} rows={rec.get('rows')}")
        print(f"{len(pending)} pending (skipping {sorted(SKIP)})")
        return

    batch = pending[: args.limit]
    print(f"batch: {[wb for wb, _ in batch]}", flush=True)
    log_path = os.path.join(LOG_DIR, "run.log")

    with browser_session.clay_page(headless=not args.headed) as page, \
            open(log_path, "a", encoding="utf-8") as logf:
        def say(m):
            print(m, flush=True); logf.write(m + "\n"); logf.flush()
        say(f"\n===== supabase rollout "
            f"{datetime.datetime.now().isoformat(timespec='seconds')} =====")
        for i, (wb, rec) in enumerate(batch):
            say(f"\n--- [{i+1}/{len(batch)}] {wb} (rows={rec.get('rows')}) ---")
            entry = {"workbook_id": rec["workbook_id"], "workbook_name": wb,
                     "table_id": rec.get("table_id")}
            try:
                r = apply_companies_supabase.apply_supabase(page, entry, False, say,
                                     run_after=not args.skip_run, marker=MARKER)
            except Exception as e:
                say(f"!! EXCEPTION {wb}: {str(e)[:160]}")
                logf.write(traceback.format_exc()); logf.flush()
                r = {"status": "error", "error": str(e)[:300]}
                try:
                    page.keyboard.press("Escape")
                except Exception:
                    pass
            state[wb] = r
            save(STATE, state)

        say("\n===== SUMMARY =====")
        for wb, _ in batch:
            r = state.get(wb, {})
            say(f"  {wb:50} {r.get('status')} api={r.get('api')} "
                f"name={r.get('name')}")


if __name__ == "__main__":
    main()
