"""Fleet driver: delete the CPJ template's 16 columns (Size .. Country) from the
Sponsors people tables that carry them, and nothing else.

Scope comes from the targets file; a table is only touched if Clay currently shows
template columns on it, so this cannot "clean" a table that never had them.

Stops the ENTIRE run on the first sign of trouble -- collateral loss, an unexpected
column shape, or an unreadable table. With a destructive pass, continuing after a
surprise is how one mistake becomes forty.

  python delete_cpj_columns_rollout.py --list
  python delete_cpj_columns_rollout.py --dry-run
  python delete_cpj_columns_rollout.py --limit 5
  python delete_cpj_columns_rollout.py
"""

import argparse
import datetime
import json
import os
import sys
import traceback

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
AUTO_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, SCRIPT_DIR)
sys.path.insert(0, os.path.join(AUTO_DIR, "clay_sync"))
sys.path.insert(0, os.path.join(AUTO_DIR, "build_automation"))

import browser_session               # noqa: E402
import delete_cpj_columns as dc      # noqa: E402
import state_io                      # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

TARGETS = os.path.join(SCRIPT_DIR, os.environ.get(
    "CLAY_CPJ_TARGETS", "cpj_sponsors_targets.json"))
LOG_DIR = os.path.join(SCRIPT_DIR, "cpj_delete_logs")
os.makedirs(LOG_DIR, exist_ok=True)

DONE = ("clean", "nothing_to_delete", "no_table")
# Any of these stops the whole run rather than moving on.
FATAL = ("collateral_loss", "unexpected_shape", "unreadable",
         "post_check_unreadable")


def load_scope():
    payload = json.load(open(TARGETS, encoding="utf-8"))
    targets = payload.get("targets", payload)
    scope = []
    for event in sorted(targets):
        rec = targets[event]
        for table, tid in sorted(rec["tables"].items()):
            scope.append({"key": f"{event}|{table}", "event": event,
                          "table": table, "table_id": tid,
                          "workbook_id": rec["workbook_id"]})
    return scope


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", default=os.path.join(SCRIPT_DIR,
                                                    "cpj_delete_state.json"))
    ap.add_argument("--only", action="append", default=[])
    ap.add_argument("--limit", type=int)
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--headed", action="store_true")
    a = ap.parse_args()

    scope = load_scope()
    if a.only:
        scope = [s for s in scope
                 if any(o.lower() in s["event"].lower() for o in a.only)]
    state = state_io.load_json(a.state, {})
    pending = [s for s in scope
               if state.get(s["key"], {}).get("status") not in DONE]

    if a.list:
        print(f"scope {len(scope)} tables, {len(pending)} pending")
        for s in scope:
            print(f"  {state.get(s['key'], {}).get('status', '-'):<20}"
                  f"{s['event']:<40}{s['table']}")
        return 0

    if a.limit:
        pending = pending[:a.limit]
    if not pending:
        print("nothing pending")
        return 0

    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    log = open(os.path.join(LOG_DIR, f"run_{stamp}.log"), "a", encoding="utf-8")

    def say(m):
        print(m, flush=True)
        log.write(m + "\n")
        log.flush()

    say(f"[{stamp}] {len(pending)} table(s) pending "
        f"({'DRY RUN' if a.dry_run else 'DELETING'})")
    tally = {}
    if a.dry_run:
        for item in pending:
            say(f"\n=== {item['event']} / {item['table']}")
            res = dc.clean_table(None, item["workbook_id"], item["table"],
                                 item["table_id"], say, dry_run=True)
            tally[res["status"]] = tally.get(res["status"], 0) + 1
        say(f"\ndry-run tally: {tally}")
        return 0

    stopped = False
    with browser_session.clay_page(headless=not a.headed) as page:
        page.set_viewport_size({"width": 1720, "height": 1200})
        open_wid = None
        for i, item in enumerate(pending, 1):
            say(f"\n=== [{i}/{len(pending)}] {item['event']} / {item['table']}")
            try:
                res = dc.clean_table(
                    page, item["workbook_id"], item["table"], item["table_id"],
                    say, already_open=(open_wid == item["workbook_id"]))
                open_wid = item["workbook_id"]
            except Exception:
                say(traceback.format_exc()[-900:])
                res = {"status": "exception", "table": item["table"],
                       "at": datetime.datetime.now().isoformat(
                           timespec="seconds")}
                open_wid = None
            state[item["key"]] = res
            state_io.save_json(a.state, state)
            tally[res["status"]] = tally.get(res["status"], 0) + 1
            say(f"  -> {res['status']}")
            if res["status"] in FATAL:
                say(f"\nSTOPPING the whole run: {item['key']} returned "
                    f"{res['status']!r}. Nothing further will be touched.")
                stopped = True
                break

    say(f"\ntally: {tally}")
    if stopped:
        say("run halted early — review before continuing")
    log.close()
    return 1 if stopped else 0


if __name__ == "__main__":
    sys.exit(main())
