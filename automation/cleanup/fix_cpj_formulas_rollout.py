"""Fleet driver: switch the custom CPJ columns to formulas that index the
"Company Table Data" lookup, on every table the template was applied to.

Which columns: fix_cpj_formulas.ACTIVE_COLUMNS -- seven, because the user excluded
Country for the remaining events (its source column is empty on many of them).
Size the completeness check off ACTIVE_COLUMNS, never PROPERTY_FOR_COLUMN.

Scope is the tables cpj_people_state.json records as `ok` -- i.e. the ones that
actually carry the 16 columns. Deliberately NOT the raw targets file: a table
without the template has nothing to convert, and the three v1 tables are excluded
by name (the user: do not rerun those).

Proven on Pittcon / Sellers - People (2026-08-04): the eight columns went from 0%
to 100% while the eight canonical ones were left untouched.

  python fix_cpj_formulas_rollout.py --list
  python fix_cpj_formulas_rollout.py --limit 2 --dry-run
  python fix_cpj_formulas_rollout.py --shards 2 --shard 0 --state fx_state_s0.json
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

import clay_ui                       # noqa: E402
import browser_session               # noqa: E402
import column_config as colcfg       # noqa: E402
import fix_cpj_formulas as fx        # noqa: E402
import apply_cpj_people_rollout as base  # noqa: E402  (V1_SKIP, scope helpers)
import state_io                      # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# Which apply-pass state and targets to read. Overridable so the same driver
# serves the Sponsors entity, whose people tables and state live in their own
# files (cpj_sponsors_state.json / cpj_sponsors_targets.json).
APPLIED_STATE = os.path.join(SCRIPT_DIR, os.environ.get(
    "CLAY_CPJ_APPLIED_STATE", "cpj_people_state.json"))
TARGETS_FILE = os.path.join(SCRIPT_DIR, os.environ.get(
    "CLAY_CPJ_TARGETS", "cpj_people_targets.json"))
LOG_DIR = os.path.join(SCRIPT_DIR, "cpj_formula_logs")
os.makedirs(LOG_DIR, exist_ok=True)

# A table is finished when every ACTIVE column is set (or was already set).
GOOD = ("ok", "already_set")
DONE = ("all_set",)


def load_scope():
    applied = state_io.load_json(APPLIED_STATE, {})
    targets = json.load(open(TARGETS_FILE, encoding="utf-8"))
    targets = targets.get("targets", targets)
    tid_of = {f"{ev}|{tb}": tid
              for ev, rec in targets.items()
              for tb, tid in rec["tables"].items()}
    wid_of = {f"{ev}|{tb}": rec["workbook_id"]
              for ev, rec in targets.items() for tb in rec["tables"]}
    scope = []
    for key in sorted(applied):
        if key in base.V1_SKIP:
            continue
        if applied[key].get("status") != "ok":
            continue          # no template on it -> nothing to convert
        event, _, table = key.partition("|")
        scope.append({"key": key, "event": event, "table": table,
                      "workbook_id": wid_of.get(key),
                      "table_id": tid_of.get(key)})
    return [s for s in scope if s["workbook_id"]]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", default=os.path.join(SCRIPT_DIR,
                                                    "cpj_formula_state.json"))
    ap.add_argument("--only", action="append", default=[])
    ap.add_argument("--limit", type=int)
    ap.add_argument("--shards", type=int, default=1)
    ap.add_argument("--shard", type=int, default=0)
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--headed", action="store_true")
    a = ap.parse_args()

    scope = load_scope()
    if a.only:
        scope = [s for s in scope
                 if any(o.lower() in s["event"].lower() for o in a.only)]
    if a.shards > 1:
        scope = [s for i, s in enumerate(scope) if i % a.shards == a.shard]

    state = state_io.load_json(a.state, {})
    pending = [s for s in scope
               if state.get(s["key"], {}).get("status") not in DONE]

    if a.list:
        print(f"scope {len(scope)} tables, {len(pending)} pending")
        for s in scope:
            st = state.get(s["key"], {}).get("status", "-")
            print(f"  {st:<10}{s['event']:<44}{s['table']}")
        return 0

    if a.limit:
        pending = pending[:a.limit]
    if not pending:
        print("nothing pending")
        return 0

    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    suffix = f"_w{a.shard}" if a.shards > 1 else ""
    log = open(os.path.join(LOG_DIR, f"run_{stamp}{suffix}.log"), "a",
               encoding="utf-8")

    def say(m):
        print(m, flush=True)
        log.write(m + "\n")
        log.flush()

    say(f"[{stamp}] {len(pending)} table(s) pending")
    ok = failed = 0
    with browser_session.clay_page(headless=not a.headed) as page:
        page.set_viewport_size({"width": 1720, "height": 1900})
        open_wid = None
        for i, item in enumerate(pending, 1):
            say(f"\n=== [{i}/{len(pending)}] {item['event']} / {item['table']}")
            results = {}
            try:
                if open_wid != item["workbook_id"]:
                    clay_ui.open_workbook_by_id(page, item["workbook_id"])
                    open_wid = item["workbook_id"]
                if not colcfg.table_exists(page, item["table"]):
                    say("  SKIP: table missing")
                    results = {"__table__": "no_table"}
                else:
                    colcfg.focus_table_maybe_empty(page, item["table"])
                    page.wait_for_timeout(1200)
                    for column in fx.ACTIVE_COLUMNS:
                        # Per-column isolation: one column throwing (a Playwright
                        # timeout clicking a missing editor) used to abort the
                        # whole table and lose the remaining seven, which is how
                        # 32 tables came out partial_2of8.
                        try:
                            r = fx.set_formula(page, column, say,
                                               dry_run=a.dry_run)
                            results[column] = r["status"]
                        except Exception as e:
                            say(f"  {column}: EXCEPTION {str(e)[:90]}")
                            results[column] = "exception"
                            # leave the panel in a known state for the next column
                            try:
                                page.keyboard.press("Escape")
                                page.wait_for_timeout(800)
                            except Exception:
                                pass
            except Exception:
                say(traceback.format_exc()[-900:])
                results["__error__"] = "exception"
                open_wid = None

            good = sum(1 for c in fx.ACTIVE_COLUMNS if results.get(c) in GOOD)
            if a.dry_run:
                status = "dryrun"
            elif results.get("__table__") == "no_table":
                status = "all_set"          # nothing to convert, never revisit
            elif good == len(fx.ACTIVE_COLUMNS):
                status = "all_set"
                ok += 1
            else:
                status = f"partial_{good}of{len(fx.ACTIVE_COLUMNS)}"
                failed += 1
            say(f"  -> {status}")
            if not a.dry_run:
                state[item["key"]] = {
                    "status": status, "columns": results,
                    "at": datetime.datetime.now().isoformat(timespec="seconds")}
                state_io.save_json(a.state, state)

    say(f"\ndone: all_set={ok} partial={failed}")
    log.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
