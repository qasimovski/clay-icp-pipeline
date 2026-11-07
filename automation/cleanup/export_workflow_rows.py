"""
Read company rows out of a Clay folder into a bulk JSONL for a Clay Workflow run.

Read-only and credit-free: every call is `clay folders/tables ...`, which are
read paths. Nothing here creates, edits or triggers anything in Clay.

Why this exists: Clay Workflows has no table-as-source trigger, and the CLI
cannot build or populate a table (`clay tables` is list/get/columns/rows/query;
its `update` only toggles query sync). The supported way to put existing table
rows through a workflow is a bulk routine run:

    export_workflow_rows.py --apply                     # this script -> rows.jsonl
    clay routines runs start workflow:<id> --bulk rows.jsonl
    clay routines runs get <runId> --bulk --wait        # -> resultUrl -> CSV

Two live-data traps this handles, both of which silently corrupt the input if
ignored:

1. **Column ids are opaque and per-table.** Cells come back keyed by `f_...`,
   and the ids differ between two tables with identical column names. Every
   table therefore gets its own `columns list` call and its own id->name map.
   The shapes differ too: exhibitors_normalized carries Event/Year/Country,
   sponsors_normalized does not - so FIELDS below maps both onto one flat key
   set, missing columns emitting "". One workflow input schema then serves both.
2. **Cells are status-tagged, not raw values.** A cell is
   `{"status": "success", "value": ...}` OR error/running/queued/retry/
   rate_limited/awaiting_callback/empty. Reading `.value` blindly turns an
   unfinished enrichment into a blank that a paid downstream step then acts on.
   Only `success` cells contribute a value; everything else is counted per
   column into the summary so an incomplete table is visible before it is fed
   to a workflow, never after.

HARD SCOPE: one folder, addressed by its own id read from config/local.yaml
(never hard-coded here - docs/SENSITIVE_DATA.md), and only tables whose name is
in --table. Workbooks in --skip-workbook are dropped by name.

WSL-only, like the other clay-CLI readers in this directory: the CLI binary is
Linux and the Windows wrapper rejects MINGW64. $CLAY_BIN overrides resolution.

Usage:
  python3 export_workflow_rows.py                 # dry run: scope + row counts
  python3 export_workflow_rows.py --apply         # write scope + jsonl
  python3 export_workflow_rows.py --apply --only "Scotplant"
  python3 export_workflow_rows.py --apply --limit 3
"""

import argparse
import glob
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
AUTO_DIR = os.path.dirname(HERE)
REPO = os.path.dirname(AUTO_DIR)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(AUTO_DIR, "clay_sync"))
sys.path.insert(0, os.path.join(AUTO_DIR, "build_automation"))

import state_io  # noqa: E402

LOCAL_CONFIG = os.path.join(REPO, "config", "local.yaml")
CLEAN_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"


def local_setting(key, env_var, default=None):
    """Read an id from the environment or gitignored config/local.yaml.

    Prefers browser_session.local_setting so there is one reader in the repo,
    but that module imports clay_ui -> playwright, which is deliberately not
    installed under WSL (Playwright passes run on Windows; the clay-CLI readers
    run here). So fall back to an inline parser with the same semantics: an
    unquoted '#' starts a comment, a '#' inside a quoted value does not.
    """
    val = os.environ.get(env_var)
    if val:
        return val.strip()
    try:
        import browser_session
        return browser_session.local_setting(key, env_var, default)
    except Exception:
        pass
    try:
        with open(LOCAL_CONFIG, encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if not line.startswith(key + ":"):
                    continue
                value = line.split(":", 1)[1].strip()
                if value[:1] in ("'", '"'):
                    quote = value[0]
                    end = value.find(quote, 1)
                    if end != -1:
                        return value[1:end]
                return value.split("#", 1)[0].strip()
    except OSError:
        pass
    return default

# Sample table, imported by hand and never part of the per-event build.
# Per the user (2026-08-26): excluded from the Competitive Events workflow scope.
DEFAULT_SKIP_WORKBOOKS = ("Highways UK 2025",)
DEFAULT_TABLES = ("exhibitors_normalized", "sponsors_normalized")

# Clay column name -> output key. First present column wins, so the enriched
# value is preferred where the scrape and the enrichment both wrote one. Keys
# absent from a given table emit "" rather than being omitted, so both table
# shapes produce the same flat record and one workflow input schema covers both.
FIELDS = [
    ("company_name",         ["Company Name"]),
    ("enriched_name",        ["Name"]),
    ("domain",               ["Domain", "Official Domain", "Verified Domain"]),
    ("website",              ["Website"]),
    ("linkedin",             ["LinkedIn"]),
    ("description_source",   ["Description"]),
    ("description_enriched", ["Description (2)"]),
    ("industry",             ["Industry"]),
    ("subindustry",          ["Subindustry"]),
    ("primary_offerings",    ["Primary Offerings"]),
    ("company_specialties",  ["Company Specialties"]),
    ("size",                 ["Size"]),
    ("type",                 ["Type"]),
    ("founded",              ["Founded"]),
    ("business_stage",       ["Business Stage"]),
    ("annual_revenue",       ["Annual Revenue"]),
    ("country",              ["Country"]),
    ("event",                ["Event"]),
    ("year",                 ["Year"]),
    ("is_new",               ["Is New"]),
]
TIMESTAMP_COLS = ("Created At", "Updated At")


def clay_bin():
    """Prefer $CLAY_BIN, else the newest cached linux binary, else `clay`."""
    env = os.environ.get("CLAY_BIN")
    if env:
        return env
    base = os.path.expanduser("~/.config/clay/bin")
    cands = sorted(glob.glob(os.path.join(base, "clay-*-linux-x64")) +
                   glob.glob(os.path.join(base, "clay-*-linux-arm64")))
    return cands[-1] if cands else "clay"


def run(bin_, args):
    """One clay CLI call returning parsed JSON. Retries transient failures,
    then fails loud - a half-read table must never look like a short table."""
    env = dict(os.environ)
    env["PATH"] = CLEAN_PATH
    last = None
    for attempt in range(6):
        r = subprocess.run([bin_] + args, capture_output=True, text=True, env=env)
        if r.returncode == 0 and r.stdout.strip():
            try:
                return json.loads(r.stdout)
            except json.JSONDecodeError:
                pass
        last = r
        time.sleep(1.5 * (attempt + 1))
    raise SystemExit("clay {} failed: {} {}".format(
        " ".join(args), last.returncode, (last.stderr or "")[:300]))


def paginate(bin_, args, limit=100):
    """Yield every page of a cursor-paginated list command."""
    cursor = None
    for _ in range(2000):
        page = args + ["--limit", str(limit)]
        if cursor:
            page += ["--cursor", cursor]
        d = run(bin_, page)
        for item in d.get("data", []):
            yield item
        cursor = d.get("cursor")
        if not cursor:
            return
    raise SystemExit("clay {}: cursor did not terminate".format(" ".join(args)))


def cell_value(cell):
    """(value, status) for one status-tagged cell. Only `success` yields a
    value; anything still computing or errored yields "" plus its status, so
    the caller can count it instead of mistaking it for an empty field."""
    if not isinstance(cell, dict):
        return "", "malformed"
    status = cell.get("status")
    if status != "success":
        return "", status or "unknown"
    val = cell.get("value")
    if val is None:
        return "", "success"
    if isinstance(val, bool):
        return val, "success"
    if isinstance(val, (dict, list)):
        return json.dumps(val, ensure_ascii=False), "success"
    return str(val), "success"


def extract_table(bin_, table, workbook, entity, incomplete):
    """All rows of one table as flat dicts, keyed by FIELDS."""
    cols = run(bin_, ["tables", "columns", "list", table["id"]]).get("data", [])
    by_name = {c["name"]: c["id"] for c in cols if c.get("name")}
    present = {}
    for key, candidates in FIELDS:
        for name in candidates:
            if name in by_name:
                present[key] = (by_name[name], name)
                break

    rows = []
    for row in paginate(bin_, ["tables", "rows", "list", table["id"]]):
        cells = row.get("cells") or {}
        rec = {
            "source_table_id": table["id"],
            "source_table": table["name"],
            "source_row_id": row["id"],
            "workbook": workbook["name"],
            "workbook_id": workbook["id"],
            "entity": entity,
        }
        for key, _ in FIELDS:
            if key not in present:
                rec[key] = ""
                continue
            col_id, col_name = present[key]
            val, status = cell_value(cells.get(col_id, {}))
            rec[key] = val
            if status != "success":
                tally = incomplete.setdefault(
                    "{}::{}".format(table["name"], col_name), {})
                tally[status] = tally.get(status, 0) + 1
        rows.append(rec)
    return rows, sorted(set(by_name) - set(TIMESTAMP_COLS))


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--folder", help="Clay folder id (default: local.yaml "
                                     "highways_subfolder_id)")
    ap.add_argument("--folder-key", default="highways_subfolder_id",
                    help="local.yaml key holding the folder id")
    ap.add_argument("--table", action="append", default=None,
                    help="table name to include (repeatable)")
    ap.add_argument("--skip-workbook", action="append", default=None,
                    help="workbook name to exclude (repeatable)")
    ap.add_argument("--only", action="append", default=None,
                    help="only this workbook name (repeatable)")
    ap.add_argument("--limit", type=int, help="stop after N workbooks")
    ap.add_argument("--out", default=os.path.join(HERE, "ce_workflow_rows.jsonl"))
    ap.add_argument("--scope-out",
                    default=os.path.join(HERE, "ce_workflow_scope.json"))
    ap.add_argument("--apply", action="store_true",
                    help="write the jsonl + scope file (default: report only)")
    args = ap.parse_args()

    tables_wanted = set(args.table or DEFAULT_TABLES)
    skip = set(args.skip_workbook if args.skip_workbook is not None
               else DEFAULT_SKIP_WORKBOOKS)
    only = set(args.only or [])

    folder = args.folder or local_setting(
        args.folder_key, "CLAY_" + args.folder_key.upper())
    if not folder:
        raise SystemExit("no folder id: set {} in config/local.yaml or pass "
                         "--folder".format(args.folder_key))

    bin_ = clay_bin()
    print("clay binary : {}".format(bin_), flush=True)
    print("folder      : {}".format(folder), flush=True)
    print("tables      : {}".format(", ".join(sorted(tables_wanted))), flush=True)
    print("skipping    : {}".format(", ".join(sorted(skip)) or "(none)"), flush=True)

    children = run(bin_, ["folders", "list", folder]).get("data", [])
    workbooks = [c for c in children if c.get("resourceType") == "WORKBOOK"]
    workbooks.sort(key=lambda w: w["name"].lower())

    selected, skipped = [], []
    for wb in workbooks:
        if wb["name"] in skip:
            skipped.append(wb["name"])
            continue
        if only and wb["name"] not in only:
            continue
        selected.append(wb)
    if args.limit:
        selected = selected[:args.limit]

    print("workbooks   : {} selected, {} skipped by name {}".format(
        len(selected), len(skipped), skipped if skipped else ""), flush=True)

    incomplete, all_rows, scope = {}, [], []
    for i, wb in enumerate(selected, 1):
        tabs = run(bin_, ["tables", "list", "--limit", "50",
                          "--filter", "workbook.id=" + wb["id"]]).get("data", [])
        for t in tabs:
            if t["name"] not in tables_wanted:
                continue
            entity = t["name"].replace("_normalized", "")
            rows, colnames = extract_table(bin_, t, wb, entity, incomplete)
            all_rows.extend(rows)
            scope.append({"workbook": wb["name"], "workbook_id": wb["id"],
                          "table": t["name"], "table_id": t["id"],
                          "entity": entity, "rows": len(rows),
                          "columns": colnames})
            print("  [{}/{}] {:34} {:22} {:5d} rows".format(
                i, len(selected), wb["name"][:34], t["name"][:22], len(rows)),
                flush=True)

    by_entity = {}
    for r in all_rows:
        by_entity[r["entity"]] = by_entity.get(r["entity"], 0) + 1
    print("\ntotal: {} rows across {} tables ({})".format(
        len(all_rows), len(scope),
        ", ".join("{}={}".format(k, v) for k, v in sorted(by_entity.items()))))

    no_domain = sum(1 for r in all_rows if not r["domain"])
    not_new = sum(1 for r in all_rows if r["is_new"] is False)
    blank_new = sum(1 for r in all_rows if r["is_new"] == "")
    print("rows with no domain              : {}".format(no_domain))
    print("rows with Is New == false        : {}".format(not_new))
    print("rows with Is New unset/incomplete: {}".format(blank_new))
    if incomplete:
        print("\nnon-success cells (column -> status -> count):")
        for k in sorted(incomplete):
            print("  {}: {}".format(k, incomplete[k]))

    if not args.apply:
        print("\ndry run - nothing written. Re-run with --apply.")
        return

    with open(args.out, "w", encoding="utf-8") as fh:
        for r in all_rows:
            fh.write(json.dumps(
                {"id": "{}:{}".format(r["source_table_id"], r["source_row_id"]),
                 "inputs": r}, ensure_ascii=False) + "\n")
    state_io.save_json(args.scope_out, {
        "folder": folder,
        "tables": sorted(tables_wanted),
        "skipped_workbooks": sorted(skipped),
        "rows": len(all_rows),
        "by_entity": by_entity,
        "incomplete_cells": incomplete,
        "scope": scope})
    print("\nwrote {} ({} lines)".format(args.out, len(all_rows)))
    print("wrote {}".format(args.scope_out))


if __name__ == "__main__":
    main()
