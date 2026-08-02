"""Fleet driver: apply the "Google Sheet - Lookup & Send Data" template to an
event workbook's seller/buyer people tables (`config/entity-types/<entity>.
yaml: tables.seller_people/buyer_people` — "Sellers - People"/"Buyers - People"
for Exhibitors, "Sponsors - Sellers - People"/"Sponsors - Buyers - People" for
Sponsors). Whichever of the two tables exist gets it; a missing table is
skipped, not errored (some events have only one side, or neither).

Entity/ICP-agnostic (--entity/--icp), same pattern as seller_people_rollout.py /
buyer_people_rollout.py — this is step (g) in the per-event pipeline, run after
seller_people_rollout.py (e) and buyer_people_rollout.py (f) since it targets the tables
those passes create. Scope is the union of their target files (a workbook
only needs this step if it has a seller and/or buyer people table already):

  people_targets_<slug>.json + buyer_targets_<slug>.json   scope (union)
  gsheet_state_<slug>.json                                 resumable per-(workbook,table) progress
  gsheet_logs/run_<slug>*.log                               progress log

Only ever touches the two named people tables — never opens/edits the main
normalized table or any other table in a workbook.

Idempotent per (workbook, table): skips a table that already has the
"Lookup in Audiences" signature column (see apply_gsheet_lookup.py).

  python apply_gsheet_lookup_rollout.py --only "Analytica India"      # default exhibitors/labs
  python apply_gsheet_lookup_rollout.py --limit 10
  python apply_gsheet_lookup_rollout.py --entity sponsors --limit 10
  python apply_gsheet_lookup_rollout.py --dry-run --limit 5
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
import pipeline_config as pcfg    # noqa: E402

LOG_DIR = os.path.join(SCRIPT_DIR, "gsheet_logs")
os.makedirs(LOG_DIR, exist_ok=True)

# id -> name lookup: the folder-wide workbook list, not the per-slug
# cols_manifest (which only exists for entities that have re-run
# build_workbook_manifest.py since the slug-namespacing convention landed — the
# original Exhibitors manifest is still the un-suffixed legacy cols_manifest.json).
FOLDER_PATH = os.path.join(SCRIPT_DIR, "competitive_events_workbooks.json")

_FINAL_STATUSES = ("ok", "dryrun", "no_table", "skip_requested")


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


def workbook_done(state, wid, tables):
    """A workbook is fully processed once every table in `tables` has reached
    a final status (ok/dryrun/no_table) — an error/unconfirmed entry must be
    retried, not treated as done."""
    rec = state.get(wid, {})
    return all(table_done(rec, t) for t in tables)


def main():
    ap = argparse.ArgumentParser()
    pcfg.add_cli_args(ap)   # --entity / --icp
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only", help="workbook id or exact name")
    ap.add_argument("--limit", type=int, help="max workbooks to process this run")
    ap.add_argument("--headed", action="store_true")
    args = ap.parse_args()

    cfg = pcfg.load(args.entity, args.icp)
    slug = cfg.slug()
    tables = [cfg.seller_people_table, cfg.buyer_people_table]
    STATE_PATH = os.path.join(SCRIPT_DIR, f"gsheet_state_{slug}.json")
    print(f"entity={cfg.entity} icp={cfg.icp} | tables={tables}", flush=True)

    wbs = json.load(open(FOLDER_PATH, encoding="utf-8"))
    # Scope = union of the seller/buyer people-build targets (this pass only
    # matters for a workbook once it has one of those two tables).
    seller_ids = set(load(os.path.join(SCRIPT_DIR, f"people_targets_{slug}.json"), []))
    buyer_ids = set(load(os.path.join(SCRIPT_DIR, f"buyer_targets_{slug}.json"), []))
    target_ids = sorted(seller_ids | buyer_ids)
    if not target_ids:
        raise SystemExit(
            f"no targets for entity={cfg.entity} icp={cfg.icp} — run "
            f"seller_people_rollout.py/buyer_people_rollout.py's targets-building step first.")
    scope = [{"workbook_id": wid, "workbook_name": wbs.get(wid, wid)} for wid in target_ids]
    if args.only:
        scope = [e for e in scope if args.only in (e["workbook_id"], e["workbook_name"])]
        if not scope:
            raise SystemExit(f"--only {args.only!r} matched nothing in scope")

    state = load(STATE_PATH, {})
    pending = [e for e in scope if not workbook_done(state, e["workbook_id"], tables)]
    if args.limit:
        pending = pending[: args.limit]

    tag = "dry" if args.dry_run else ("only" if args.only else "all")
    log_path = os.path.join(LOG_DIR, f"run_{slug}_{tag}.log")
    done_ct = sum(1 for e in scope if workbook_done(state, e["workbook_id"], tables))
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
            for table in tables:
                if table_done(rec, table):
                    continue  # already reached a final status for this table
                # Transient DNS blips (ERR_NAME_NOT_RESOLVED) happen on this
                # network independent of Clay itself (nslookup flaps even when
                # curl just succeeded) - ride out up to 2 retries with a 30s
                # pause before recording a real failure.
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
        say(f"\nSUMMARY: see gsheet_state_{slug}.json for per-table results")


if __name__ == "__main__":
    main()
