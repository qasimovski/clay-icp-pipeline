"""Fleet driver: apply the "Qasim - CPJ - Labs - People" template to the
"Sellers - People" and "Buyers - People" tables of every Competitive Events
workbook.

Scope is cpj_people_targets.json (build it with build_cpj_targets.py) — 143
tables across 76 events as of 2026-08-03, of which ACHEMA / Sellers - People is
the user's hand-built reference and is skipped by the marker guard. Folder-scoped
rather than --entity/--icp: the user named these two table names, and the
Competitive Events folder is not an entity/ICP concept the resolver knows.

Two steps per table, in this order:

  1. re-run "Company Table Data" ("Run column", scoped to that one column). A Clay
     Company Lookup stores a SNAPSHOT, and these lookups last ran before the
     exhibitor tables' Claygent columns were filled — so without this the five
     Claygent-derived columns (Sub Industry, Primary Offerings, Business Stage,
     Annual Revenue, Total Funding Amount Range Usd) come back empty with status
     "success". Proven on ACHEMA / Buyers - People (2026-08-03): 0% -> 50/50/50/
     100/100%, matching the user's reference table. The user authorised the re-run.
  2. apply the template and map its thirteen inputs.

Never the table-wide "Run N rows": that would re-fire the email waterfall and
Validate Email on these tables and cost real credits. See run_lookup_column.py.

Both tables of a workbook are done on ONE workbook load (already_open, audit E8).

Costs nothing per row: the template's twelve columns are `type: "basic"`
references onto the "Company Table Data" lookup, which has already run. That is
why the default is apply-AND-run and there is no per-batch spend cap — the thing
being conserved here is wall-clock and UI risk, not credits.

Resumable per (event, table): the state file records every outcome and a rerun
skips anything already recorded done, while the pre-apply marker check asks Clay
itself (fail-closed) so an interrupted run can never apply twice.

  python apply_cpj_people_rollout.py --list                 # scope only, no browser
  python apply_cpj_people_rollout.py --dry-run --limit 2     # map, never save
  python apply_cpj_people_rollout.py --only "ACHEMA Middle East"
  python apply_cpj_people_rollout.py --limit 5
  python apply_cpj_people_rollout.py --shards 4 --shard 0    # one worker per terminal
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

import browser_session          # noqa: E402
import apply_cpj_people as app  # noqa: E402
import run_lookup_column as rlc  # noqa: E402  (refresh the lookup first)
import state_io                 # noqa: E402  (atomic, fail-loud state files)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

TARGETS = os.path.join(SCRIPT_DIR, os.environ.get(
    "CLAY_CPJ_TARGETS", "cpj_people_targets.json"))
LOG_DIR = os.path.join(SCRIPT_DIR, "cpj_people_logs")
os.makedirs(LOG_DIR, exist_ok=True)

# Outcomes that mean "do not come back to this table". "healed" is an
# already-applied table whose lookup was refreshed so its five Claygent-derived
# columns finally resolve.
DONE = ("ok", "healed", "already_applied", "no_table", "empty_table")

# Tables the user has told us not to touch (2026-08-04: "Do not rerun the 3 v1
# tables and skip those"). These three carry the FIRST version of the template, so
# applying v2 would not add Side / Classification / Country to them -- it would
# duplicate the twelve columns they already have, because Clay names a colliding
# column "X (2)" rather than updating X.
#
# Honoured on every batch and independently of the state file, so regenerating or
# losing state cannot expose them. That is deliberately stronger than recording a
# done status: it makes them unreachable rather than merely uninteresting.
V1_SKIP = {
    "ACHEMA|Sellers - People",         # the user's hand-built reference build
    "ACHEMA|Buyers - People",          # v1 + lookup refreshed; 12 columns populated
    "ACHEMA Middle East|Sellers - People",   # v1; 5 columns still unpopulated
}


def load_scope():
    payload = json.load(open(TARGETS, encoding="utf-8"))
    targets = payload["targets"] if "targets" in payload else payload
    # Sorted before sharding: an unsorted scope repartitions when the manifest is
    # regenerated and shard 2 silently changes meaning (audit C15,
    # tests/test_scope_ordering.py).
    scope = []
    for event in sorted(targets):
        rec = targets[event]
        for table in app.ALL_PEOPLE_TABLES:      # Sellers first, then Buyers
            tid = rec["tables"].get(table)
            if tid:
                scope.append({"event": event, "workbook_id": rec["workbook_id"],
                              "table": table, "table_id": tid})
    return scope


def key_of(item):
    return f"{item['event']}|{item['table']}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", default=os.path.join(SCRIPT_DIR,
                                                    "cpj_people_state.json"))
    ap.add_argument("--only", action="append", default=[],
                    help="event name substring (repeatable)")
    ap.add_argument("--table", choices=list(app.ALL_PEOPLE_TABLES),
                    help="restrict to one of the two People tables")
    ap.add_argument("--limit", type=int, help="stop after N tables")
    ap.add_argument("--shards", type=int, default=1)
    ap.add_argument("--shard", type=int, default=0)
    ap.add_argument("--list", action="store_true", help="print scope and exit")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--recon", action="store_true")
    ap.add_argument("--no-run", action="store_true",
                    help="save without running (the columns stay empty)")
    ap.add_argument("--no-refresh", action="store_true",
                    help="skip the 'Company Table Data' re-run; the five "
                         "Claygent-derived columns will then be empty")
    ap.add_argument("--heal", action="store_true",
                    help="for tables that already carry the template, refresh "
                         "their lookup instead of skipping them")
    ap.add_argument("--headed", action="store_true")
    a = ap.parse_args()

    scope = load_scope()
    if a.table:
        scope = [s for s in scope if s["table"] == a.table]
    if a.only:
        scope = [s for s in scope
                 if any(o.lower() in s["event"].lower() for o in a.only)]
    if a.shards > 1:
        scope = [s for i, s in enumerate(scope) if i % a.shards == a.shard]

    state = state_io.load_json(a.state, {})
    pending = [s for s in scope
               if key_of(s) not in V1_SKIP
               and state.get(key_of(s), {}).get("status") not in DONE]

    if a.list:
        print(f"scope {len(scope)} tables, {len(pending)} pending "
              f"(state {os.path.basename(a.state)})")
        for s in scope:
            k = key_of(s)
            st = ("v1-skip" if k in V1_SKIP
                  else state.get(k, {}).get("status", "-"))
            print(f"  {st:<16}{s['event']:<45}{s['table']:<18}{s['table_id']}")
        return 0

    if a.limit:
        pending = pending[:a.limit]
    if not pending:
        print("nothing pending")
        return 0

    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    suffix = f"_w{a.shard}" if a.shards > 1 else ""
    log_path = os.path.join(LOG_DIR, f"run_{stamp}{suffix}.log")
    log = open(log_path, "a", encoding="utf-8")

    def say(m):
        print(m, flush=True)
        log.write(m + "\n")
        log.flush()

    say(f"[{stamp}] {len(pending)} table(s) pending; log {log_path}")
    ok = failed = skipped = 0
    with browser_session.clay_page(headless=not a.headed) as page:
        page.set_viewport_size(app.PANEL_VIEWPORT)
        open_wid = None
        for i, item in enumerate(pending, 1):
            key = key_of(item)
            say(f"\n=== [{i}/{len(pending)}] {item['event']} / {item['table']}")
            try:
                if not (a.recon or a.dry_run):
                    applied = app.already_applied(item["table_id"],
                                                  item["table"], say)
                    if applied is None:
                        say("  SKIP: unreadable — left pending")
                        skipped += 1
                        continue
                    if applied:
                        if a.heal:
                            # Already carries the template; refreshing its lookup
                            # is what makes the five Claygent-derived columns
                            # resolve. Proven on ACHEMA / Buyers - People.
                            say("  already applied — refreshing the lookup only")
                            r = rlc.run_column(page, item["workbook_id"],
                                               item["table"], app.LOOKUP, say)
                            open_wid = None   # run_column navigates by id itself
                            res = {"status": ("healed" if r.get("status") == "ok"
                                              else "heal_failed"),
                                   "table": item["table"], "lookup_run": r,
                                   "at": datetime.datetime.now().isoformat(
                                       timespec="seconds")}
                        else:
                            res = {"status": "already_applied",
                                   "table": item["table"],
                                   "at": datetime.datetime.now().isoformat(
                                       timespec="seconds")}
                            say("  SKIP: already applied (--heal to refresh its "
                                "lookup)")
                            skipped += 1
                        state[key] = res
                        state_io.save_json(a.state, state)
                        continue

                    # A table with no rows cannot be mapped at all: with no rows
                    # the "Company Table Data" lookup has no schema, so the picker
                    # offers no properties and clicking the lookup selects the
                    # lookup ITSELF as the value (observed on ResearchPharm /
                    # Sellers - People, 0 rows -- "Size" came back holding
                    # 'Company Table Data'). Recorded as done so it is not retried
                    # forever; it becomes mappable once the event has people.
                    rows = app.table_rows(item["table_id"], say)
                    if rows == 0:
                        res = {"status": "empty_table", "table": item["table"],
                               "rows": 0,
                               "at": datetime.datetime.now().isoformat(
                                   timespec="seconds")}
                        state[key] = res
                        state_io.save_json(a.state, state)
                        say("  SKIP: table has 0 rows — nothing to map")
                        skipped += 1
                        continue

                    if not a.no_refresh:
                        # Step 1: refresh the lookup BEFORE mapping, so the
                        # template's reference columns compute against fresh data
                        # on their first run instead of resolving to empty.
                        r = rlc.run_column(page, item["workbook_id"],
                                           item["table"], app.LOOKUP, say)
                        if r.get("status") != "ok":
                            say(f"  lookup refresh returned {r.get('status')!r} — "
                                f"continuing to the template anyway")
                        open_wid = None       # run_column did its own navigation

                res = app.apply_to_table(
                    page, item["workbook_id"], item["event"], item["table"], say,
                    dry_run=a.dry_run, recon=a.recon, run_after=not a.no_run,
                    already_open=(open_wid == item["workbook_id"]))
                open_wid = item["workbook_id"]
            except Exception:
                say(traceback.format_exc()[-1200:])
                res = {"status": "error", "table": item["table"],
                       "at": datetime.datetime.now().isoformat(timespec="seconds")}
                # The page may be anywhere after a failure; force a fresh load.
                open_wid = None
            if res.get("status") == "ok":
                ok += 1
            elif res.get("status") in ("recon", "dryrun"):
                pass
            else:
                failed += 1
            if not (a.recon or a.dry_run):
                state[key] = res
                state_io.save_json(a.state, state)

    say(f"\ndone: ok={ok} failed={failed} skipped={skipped}")
    log.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
