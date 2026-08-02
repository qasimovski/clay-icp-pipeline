"""Do the whole "Waterfall and Validate Email" pass for ONE event's people
tables, guarded so no table is ever processed twice.

Per table (Sellers - People, Buyers - People):
  1. ask Clay (CLI) whether WORK EMAIL already exists -> if so, never re-apply
     the template (that is how duplicate column sets get created);
  2. apply the template WITHOUT running (Full Name / Company Domain / LinkedIn
     Profile arrive pre-filled; success <- Add row > success; Company Name <-
     Company Table Data > Name);
  3. fix Validate Email: Email Address -> WORK EMAIL, run condition
     !!{{WORK EMAIL}}, Auto-run OFF — the template inherits a gate pointing at a
     column that does not exist here, and arrives with Auto-run ON;
  4. ask Clay whether WORK EMAIL is still empty -> only then trigger the run.

Steps 3 and 4 are deliberately after the apply so nothing is charged before the
gate is right, and so a run can never fire twice.

  python people_email.py <workbook_name>            # both tables
  python people_email.py <workbook_name> --skip-run # configure only
"""

import argparse
import datetime
import json
import os
import subprocess
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
AUTO_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, SCRIPT_DIR)
sys.path.insert(0, os.path.join(AUTO_DIR, "clay_sync"))
sys.path.insert(0, os.path.join(AUTO_DIR, "build_automation"))

import browser_session                               # noqa: E402
import apply_people_waterfall    # noqa: E402
import configure_validate_email  # noqa: E402
import run_people_table                # noqa: E402
import clay_ui                              # noqa: E402
import column_config as colcfg                       # noqa: E402
import add_workemail_waterfall as panel   # noqa: E402

AUDIT = os.path.join(SCRIPT_DIR, "people_email_audit.json")
# Write-ahead log of run triggers, keyed by table id. Written BEFORE the click,
# so a worker killed mid-trigger still leaves proof the run happened. Empty
# WORK EMAIL cells are NOT proof a table has not run: results land after the
# trigger, and trusting the fill count re-ran Analytica China's waterfall.
RUNS = os.path.join(SCRIPT_DIR, "people_email_runs.json")
# Each parallel worker needs its OWN run log: two processes doing
# read-modify-write on one file can lose a trigger record, which would let a
# waterfall run twice later. Shards are disjoint, so the files merge cleanly.
if os.environ.get("CLAY_RUNS_FILE"):
    RUNS = os.environ["CLAY_RUNS_FILE"]
    if not os.path.isabs(RUNS):
        RUNS = os.path.join(SCRIPT_DIR, RUNS)
COLS_SCRIPT = os.path.join(SCRIPT_DIR, "check_table_columns.py")
FILL_SCRIPT = os.path.join(SCRIPT_DIR, "check_column_fill.py")
TABLES = ("Sellers - People", "Buyers - People")
# The user restricted this pass to specific table names; a run must never touch
# anything outside the family it was pointed at.
ALLOWED_PREFIXES = ("Sellers - People", "Buyers - People",
                    "Sponsors - Sellers - People", "Sponsors - Buyers - People")


def _wsl(script, *args, timeout=180):
    path = script.replace("C:\\", "/mnt/c/").replace("\\", "/")
    out = subprocess.run(["wsl", "-d", "Ubuntu", "--", "python3", path, *args],
                         capture_output=True, text=True, timeout=timeout,
                         env={**os.environ, "MSYS_NO_PATHCONV": "1"})
    return (out.stdout or "").strip()


def _load_runs():
    if os.path.exists(RUNS):
        try:
            return json.load(open(RUNS, encoding="utf-8"))
        except Exception:
            pass
    return {}


# Labels that mean the run definitely did NOT happen — safe to retry. Anything
# else (a "Run N rows" label, or the ambiguous "triggering" written just before
# the click) counts as run and must never be repeated.
RETRYABLE = ("run_not_triggered", "no_table", "not_applied", "None")


def already_triggered(table_id):
    rec = _load_runs().get(table_id)
    if not rec:
        return False
    label = str(rec.get("label") or "")
    if label in RETRYABLE:
        return False
    return True


def record_trigger(table_id, event, table, label=None):
    runs = _load_runs()
    rec = runs.get(table_id, {})
    rec.update({"event": event, "table": table,
                "triggered_at": datetime.datetime.now().isoformat(
                    timespec="seconds")})
    if label:
        rec["label"] = label
    runs[table_id] = rec
    json.dump(runs, open(RUNS, "w", encoding="utf-8"), indent=1,
              ensure_ascii=False)


def table_columns(table_id, say):
    """Column names straight from Clay. Empty list means 'could not tell'."""
    try:
        return _wsl(COLS_SCRIPT, table_id).splitlines()
    except Exception as e:
        say(f"    !! column check failed: {str(e)[:70]}")
        return []


def work_email_fill(table_id, say):
    """(filled, total) for WORK EMAIL, or (None, None) if unknown."""
    try:
        parts = _wsl(FILL_SCRIPT, table_id, "WORK EMAIL").split()
        return int(parts[0]), int(parts[1])
    except Exception as e:
        say(f"    !! fill check failed: {str(e)[:70]}")
        return None, None


def do_table(page, entry, table, table_id, say, skip_run=False):
    result = {"table": table}

    cols = table_columns(table_id, say)
    if not cols:
        say(f"  SKIP {table}: cannot read columns — refusing to touch it")
        result["status"] = "unknown_columns"
        return result
    applied = "WORK EMAIL" in cols
    say(f"  {table}: {len(cols)} columns, WORK EMAIL present={applied} (CLI)")

    # The CLI lags for columns created moments ago: a worker killed right after
    # applying the template leaves Clay's API still reporting the old column
    # list, and trusting it alone re-applied the template to Analytica China and
    # produced a duplicate set. Confirm in the UI too and treat presence in
    # EITHER source as "already applied".
    if not applied:
        try:
            clay_ui.open_workbook_by_id(page, entry["workbook_id"])
            if colcfg.table_exists(page, table):
                colcfg.focus_table_maybe_empty(page, table)
                page.wait_for_timeout(1200)
                if panel.find_header_scrolling(page, "WORK EMAIL"):
                    applied = True
                    say(f"  {table}: WORK EMAIL visible in the UI — "
                        f"treating as already applied (CLI was stale)")
        except Exception as e:
            say(f"  !! UI presence check failed: {str(e)[:80]} — "
                f"refusing to apply")
            result["status"] = "presence_check_failed"
            return result

    if not applied:
        r = apply_people_waterfall.apply_people_template(page, entry, table, False, say,
                                    run_after=False)
        result["apply"] = r.get("status")
        if r.get("status") != "ok":
            say(f"  SKIP {table}: apply returned {r.get('status')}")
            result["status"] = "apply_failed"
            return result
    else:
        say(f"  {table}: template already applied — not re-applying")
        result["apply"] = "already"

    try:
        r = configure_validate_email.configure(page, entry, say, run_after=False, table=table)
        result["validate"] = r.get("status")
        result["gate_ok"] = (r.get("verify") or {}).get("ok")
    except Exception as e:
        say(f"  !! validate config failed on {table}: {str(e)[:140]}")
        result["validate"] = "error"
        result["status"] = "validate_failed"
        return result

    if skip_run:
        result["status"] = "configured"
        return result

    # Never run a waterfall twice. The write-ahead record is checked first
    # because it survives a killed worker; fill counts do not (results arrive
    # after the trigger).
    if already_triggered(table_id):
        prev = _load_runs()[table_id]
        say(f"  {table}: run already triggered {prev.get('triggered_at')} "
            f"({prev.get('label')}) — NOT running again")
        result["status"] = "ok"
        result["ran"] = "skipped_already_triggered"
        return result

    filled, total = work_email_fill(table_id, say)
    if filled is None:
        say(f"  {table}: fill unknown — not running")
        result["status"] = "configured"
        return result
    say(f"  {table}: WORK EMAIL {filled}/{total}")
    if filled:
        say(f"  {table}: already has emails — not running again")
        record_trigger(table_id, entry["workbook_name"], table,
                       label="pre-existing emails")
        result["status"] = "ok"
        result["ran"] = "skipped_already_filled"
        return result

    record_trigger(table_id, entry["workbook_name"], table, label="triggering")
    rr = run_people_table.run_table(page, entry, table, say)
    record_trigger(table_id, entry["workbook_name"], table,
                   label=(rr.get("ran") or rr.get("status")))
    result["ran"] = rr.get("ran")
    result["status"] = "ok" if rr.get("status") == "running" else rr.get("status")
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("workbook_name")
    ap.add_argument("--skip-run", action="store_true")
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--audit", help="audit file (default people_email_audit.json)")
    ap.add_argument("--tables", nargs="+",
                    help="table names to process, in order")
    a = ap.parse_args()

    global TABLES
    if a.tables:
        bad = [t for t in a.tables if t not in ALLOWED_PREFIXES]
        if bad:
            raise SystemExit(f"refusing to touch tables outside the allowed "
                             f"list: {bad}")
        TABLES = tuple(a.tables)
    audit_path = a.audit or AUDIT
    if not os.path.isabs(audit_path):
        audit_path = os.path.join(SCRIPT_DIR, audit_path)
    audit = json.load(open(audit_path, encoding="utf-8"))
    if a.workbook_name not in audit:
        raise SystemExit(f"{a.workbook_name!r} has no people tables in the audit")
    tables = audit[a.workbook_name]
    wid = next(iter(tables.values()))["workbook_id"]
    entry = {"workbook_id": wid, "workbook_name": a.workbook_name}

    out = []
    with browser_session.clay_page(headless=not a.headed) as page:
        def say(m):
            print(m, flush=True)
        say(f"=== {a.workbook_name} ({len(tables)} people tables) ===")
        for table in TABLES:
            if table not in tables:
                continue
            out.append(do_table(page, entry, table, tables[table]["table_id"],
                                say, skip_run=a.skip_run))
    print("\nRESULT:", json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
