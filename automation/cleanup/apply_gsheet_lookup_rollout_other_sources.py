"""Fleet driver: apply the "Google Sheet - Lookup & Send Data" template to
every workbook in Labs [2026 - Qasim] -> Other Sources' "Sellers - People" AND
"Buyers - People" tables (whichever exist; a missing table is skipped, not
errored). Unlike apply_gsheet_lookup_rollout.py, this folder isn't an entity/ICP
concept (no per-event build pipeline put these tables there), so scope comes
directly from other_sources_workbooks.json (see list_other_sources_workbooks.py)
rather than a targets file.

Waits for each table's run to reach 100% completion (see
apply_gsheet_lookup.py's _wait_for_full_completion) before considering it
done - a table sitting at 100% right after Save is just a snapshot; large
tables can still be processing rows well after that first read.

  python apply_gsheet_lookup_rollout_other_sources.py --only "EIAC Directory"
  python apply_gsheet_lookup_rollout_other_sources.py --limit 5
  python apply_gsheet_lookup_rollout_other_sources.py --dry-run --limit 5
"""

import argparse
import datetime
import json
import os
import sys
import traceback

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(SCRIPT_DIR), "build_automation"))

import browser_session                  # noqa: E402
import apply_gsheet_lookup  # noqa: E402

FOLDER_PATH = os.path.join(SCRIPT_DIR, "other_sources_workbooks.json")
STATE_PATH = os.path.join(SCRIPT_DIR, "gsheet_state_other_sources.json")
LOG_DIR = os.path.join(SCRIPT_DIR, "gsheet_logs")
os.makedirs(LOG_DIR, exist_ok=True)

TABLES = ["Sellers - People", "Buyers - People"]
_FINAL_STATUSES = ("ok", "dryrun", "no_table")


def load(p, d):
    if os.path.exists(p):
        try:
            return json.load(open(p, encoding="utf-8"))
        except Exception:
            pass
    return d


def save(p, s):
    json.dump(s, open(p, "w", encoding="utf-8"), indent=1, ensure_ascii=False)


def table_done(rec, table):
    return rec.get(table, {}).get("status") in _FINAL_STATUSES


def workbook_done(state, wid):
    rec = state.get(wid, {})
    return all(table_done(rec, t) for t in TABLES)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only", help="workbook id or exact name")
    ap.add_argument("--limit", type=int, help="max workbooks to process this run")
    ap.add_argument("--headed", action="store_true")
    args = ap.parse_args()

    wbs = json.load(open(FOLDER_PATH, encoding="utf-8"))
    scope = [{"workbook_id": wid, "workbook_name": name} for wid, name in wbs.items()]
    if args.only:
        scope = [e for e in scope if args.only in (e["workbook_id"], e["workbook_name"])]
        if not scope:
            raise SystemExit(f"--only {args.only!r} matched nothing in scope")

    state = load(STATE_PATH, {})
    pending = [e for e in scope if not workbook_done(state, e["workbook_id"])]
    if args.limit:
        pending = pending[: args.limit]

    tag = "dry" if args.dry_run else ("only" if args.only else "all")
    log_path = os.path.join(LOG_DIR, f"run_othersources_{tag}.log")
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
            rec = state.setdefault(wid, {}) if not args.dry_run else {}
            wb_failed = False
            for table in TABLES:
                if table_done(rec, table):
                    continue
                r = None
                last_exc = None
                for dns_try in range(3):
                    try:
                        r = apply_gsheet_lookup.apply_gsheet(page, entry, table, args.dry_run, say)
                        last_exc = None
                        break
                    except Exception as e:
                        last_exc = e
                        if "ERR_NAME_NOT_RESOLVED" in str(e) and dns_try < 2:
                            say(f"   DNS blip on {table}, pausing 30s (try {dns_try+1}/3)")
                            try:
                                page.keyboard.press("Escape")
                            except Exception:
                                pass
                            page.wait_for_timeout(30000)
                            continue
                        break
                if last_exc is not None:
                    say(f"!! EXCEPTION on {entry['workbook_name']}/{table}: {str(last_exc)[:200]}")
                    logf.write(traceback.format_exc()); logf.flush()
                    wb_failed = True
                    if not args.dry_run:
                        rec[table] = {"status": "error", "error": str(last_exc)[:300]}
                        state[wid] = rec
                        save(STATE_PATH, state)
                    try:
                        page.keyboard.press("Escape")
                    except Exception:
                        pass
                    continue
                if not args.dry_run:
                    rec[table] = r
                    state[wid] = rec
                    save(STATE_PATH, state)
                if r["status"] not in ("ok", "dryrun", "no_table"):
                    wb_failed = True
            cf = cf + 1 if wb_failed else 0
            if cf >= 3:
                say("!! 3 consecutive failed workbooks — aborting"); sys.exit(2)
        say("\nSUMMARY: see gsheet_state_other_sources.json for per-table results")


if __name__ == "__main__":
    main()
