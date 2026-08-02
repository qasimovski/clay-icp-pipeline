"""Fleet driver for the people-tables "Waterfall and Validate Email" pass.

Loops over events whose Sellers/Buyers - People tables still lack WORK EMAIL and
runs the guarded per-event flow from people_email.py:
  apply (no run) -> fix Validate Email (input + !!{{WORK EMAIL}} + Auto-run OFF)
  -> run only if WORK EMAIL is still empty.

Every guard asks Clay directly (CLI), so re-entry after an interrupted run never
re-applies a template or re-charges a run — an event is never done twice.

  python people_email_rollout.py --limit 5              # 5 events, smallest first
  python people_email_rollout.py --limit 5 --order alpha
  python people_email_rollout.py --list                 # show what is pending
"""

import argparse
import datetime
import json
import os
import subprocess
import sys
import traceback

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
sys.path.insert(0, os.path.join(os.path.dirname(SCRIPT_DIR), "build_automation"))

import browser_session                          # noqa: E402
import people_email         # noqa: E402

AUDIT = os.path.join(SCRIPT_DIR, "people_email_audit.json")
STATE = os.path.join(SCRIPT_DIR, "people_email_state.json")
LOG_DIR = os.path.join(SCRIPT_DIR, "people_email_logs")
os.makedirs(LOG_DIR, exist_ok=True)
ROWS_SCRIPT = os.path.join(SCRIPT_DIR, "check_table_rows.py")
# Events the user has told us not to touch. Honoured on every batch.
SKIP_PATH = os.path.join(SCRIPT_DIR, "people_email_skip.json")


def load(p, d):
    if os.path.exists(p):
        try:
            return json.load(open(p, encoding="utf-8"))
        except Exception:
            pass
    return d


def save(p, s):
    json.dump(s, open(p, "w", encoding="utf-8"), indent=1, ensure_ascii=False)


def pending_events(audit):
    """Events with at least one people table missing WORK EMAIL."""
    out = []
    for wb, tables in audit.items():
        todo = [t for t, i in tables.items()
                if "WORK EMAIL" not in (i.get("columns") or [])]
        if todo:
            out.append({"workbook_name": wb,
                        "workbook_id": next(iter(tables.values()))["workbook_id"],
                        "tables": tables, "todo": todo})
    return out


def row_total(ev):
    """Total rows across the event's pending tables (for smallest-first order)."""
    tot = 0
    for t in ev["todo"]:
        tid = ev["tables"][t]["table_id"]
        try:
            path = ROWS_SCRIPT.replace("C:\\", "/mnt/c/").replace("\\", "/")
            out = subprocess.run(
                ["wsl", "-d", "Ubuntu", "--", "python3", path, tid],
                capture_output=True, text=True, timeout=120,
                env={**os.environ, "MSYS_NO_PATHCONV": "1"})
            tot += int((out.stdout or "0").strip().split()[0])
        except Exception:
            tot += 9999
    return tot


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=5)
    ap.add_argument("--order", choices=("small", "alpha"), default="small")
    ap.add_argument("--only")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--skip-run", action="store_true")
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--shards", type=int, default=1,
                    help="partition events across N workers (disjoint)")
    ap.add_argument("--shard", type=int, default=0)
    ap.add_argument("--audit", help="audit file (default people_email_audit.json)")
    ap.add_argument("--tables", nargs="+",
                    help="table names to process, in order")
    args = ap.parse_args()

    if args.tables:
        bad = [t for t in args.tables if t not in people_email.ALLOWED_PREFIXES]
        if bad:
            raise SystemExit(f"refusing to touch tables outside the allowed "
                             f"list: {bad}")
        people_email.TABLES = tuple(args.tables)
    audit_path = args.audit or AUDIT
    if not os.path.isabs(audit_path):
        audit_path = os.path.join(SCRIPT_DIR, audit_path)
    print(f"audit={os.path.basename(audit_path)} tables={people_email.TABLES}", flush=True)
    audit = load(audit_path, None)
    if audit is None:
        raise SystemExit("run audit_people_email.py (in WSL) first")
    events = pending_events(audit)

    skip = set((load(SKIP_PATH, {}) or {}).get("skip") or [])
    if skip:
        held = [e["workbook_name"] for e in events if e["workbook_name"] in skip]
        if held:
            print(f"skipping (user instruction): {held}", flush=True)
        events = [e for e in events if e["workbook_name"] not in skip]

    if args.only:
        events = [e for e in events if e["workbook_name"] == args.only]

    if args.order == "alpha":
        events.sort(key=lambda e: e["workbook_name"])
    else:
        print("measuring row counts to take the smallest events first...",
              flush=True)
        for e in events:
            e["rows"] = row_total(e)
        # An empty table has nothing to enrich, and "Run 0 rows" is not a thing.
        skipped_empty = [e["workbook_name"] for e in events if e["rows"] == 0]
        if skipped_empty:
            print(f"skipping {len(skipped_empty)} empty table(s): "
                  f"{skipped_empty}", flush=True)
        events = [e for e in events if e["rows"] > 0]
        events.sort(key=lambda e: (e["rows"], e["workbook_name"]))

    if args.list:
        for e in events:
            print(f"  {e['workbook_name']:48} tables={e['todo']} "
                  f"rows={e.get('rows', '?')}")
        print(f"{len(events)} events pending")
        return

    # Disjoint partition by index: two workers never touch the same event.
    if args.shards > 1:
        events = [e for i, e in enumerate(events) if i % args.shards == args.shard]
        print(f"shard {args.shard}/{args.shards}: {len(events)} events in scope",
              flush=True)

    batch = events[: args.limit]
    state_path = STATE
    if args.shards > 1:
        state_path = STATE.replace(".json", f"_w{args.shard}.json")
    state = load(state_path, {})
    log_path = os.path.join(LOG_DIR, "run.log")
    print(f"batch of {len(batch)}: "
          f"{[e['workbook_name'] for e in batch]}", flush=True)

    with browser_session.clay_page(headless=not args.headed) as page, \
            open(log_path, "a", encoding="utf-8") as logf:
        def say(m):
            print(m, flush=True); logf.write(m + "\n"); logf.flush()
        say(f"\n===== rollout {datetime.datetime.now().isoformat(timespec='seconds')} "
            f"=====")
        for i, ev in enumerate(batch):
            name = ev["workbook_name"]
            entry = {"workbook_id": ev["workbook_id"], "workbook_name": name}
            say(f"\n--- [{i+1}/{len(batch)}] {name} (rows={ev.get('rows','?')}) ---")
            rec = state.setdefault(name, {})
            for table in people_email.TABLES:
                if table not in ev["tables"]:
                    continue
                try:
                    r = people_email.do_table(page, entry, table,
                                   ev["tables"][table]["table_id"], say,
                                   skip_run=args.skip_run)
                except Exception as exc:
                    say(f"!! EXCEPTION {name}/{table}: {str(exc)[:160]}")
                    logf.write(traceback.format_exc()); logf.flush()
                    r = {"table": table, "status": "error",
                         "error": str(exc)[:300]}
                    try:
                        page.keyboard.press("Escape")
                    except Exception:
                        pass
                rec[table] = r
                save(state_path, state)

        say("\n===== SUMMARY =====")
        for ev in batch:
            for table, r in (state.get(ev["workbook_name"]) or {}).items():
                say(f"  {ev['workbook_name'][:38]:38} {table:16} "
                    f"{r.get('status')} ran={r.get('ran')}")


if __name__ == "__main__":
    main()
