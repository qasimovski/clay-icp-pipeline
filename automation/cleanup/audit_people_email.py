"""Audit the Sellers/Buyers - People tables for the "Waterfall and Validate
Email" pass (run in WSL).

Writes people_email_audit.json: for every Competitive Events workbook, each
people table's id and columns of interest. ACHEMA is already done by the user and
is used as the reference for what a finished table looks like.

    MSYS_NO_PATHCONV=1 wsl -d Ubuntu -- python3 .../audit_people_email.py
"""
import glob
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
FOLDER = os.path.join(HERE, "competitive_events_workbooks.json")
OUT = os.path.join(HERE, "people_email_audit.json")
CLEAN = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

# Default family; override with --tables (e.g. the Sponsors - * tables).
PEOPLE_TABLES = ("Sellers - People", "Buyers - People")
# Inputs the template's Configure panel needs.
INPUT_COLS = ("Full Name", "Company Domain", "LinkedIn Profile", "Add row",
              "Company Table Data")


def clay():
    base = os.path.expanduser("~/.config/clay/bin")
    c = sorted(glob.glob(os.path.join(base, "clay-*-linux-x64")) +
               glob.glob(os.path.join(base, "clay-*-linux-arm64")))
    return c[-1] if c else "clay"


def run(b, args):
    env = dict(os.environ)
    env["PATH"] = CLEAN
    for _ in range(5):
        r = subprocess.run([b] + args, capture_output=True, text=True, env=env)
        if r.returncode == 0 and r.stdout.strip():
            try:
                return json.loads(r.stdout)
            except json.JSONDecodeError:
                pass
        time.sleep(2)
    return None


def main():
    global PEOPLE_TABLES, OUT
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--tables", nargs="+",
                    help="table names to audit (default Sellers/Buyers - People)")
    ap.add_argument("--out", help="output json filename")
    ap.add_argument("--folder", help="workbook-list json (default Competitive "
                                     "Events); e.g. other_sources_workbooks.json")
    a = ap.parse_args()
    if a.tables:
        PEOPLE_TABLES = tuple(a.tables)
    if a.out:
        OUT = a.out if os.path.isabs(a.out) else os.path.join(HERE, a.out)
    if a.folder:
        global FOLDER
        FOLDER = a.folder if os.path.isabs(a.folder) else os.path.join(HERE, a.folder)
    print(f"auditing {PEOPLE_TABLES} -> {os.path.basename(OUT)}", flush=True)

    b = clay()
    wanted = None
    if os.path.exists(FOLDER):
        wanted = set(json.load(open(FOLDER, encoding="utf-8")).values())
        print(f"scoping to {len(wanted)} Competitive Events workbooks")

    found, cur = {}, None
    for _ in range(300):
        d = run(b, ["tables", "list", "--limit", "100"] +
                (["--cursor", cur] if cur else []))
        if d is None:
            break
        for t in d.get("data", []):
            wb = (t.get("workbook") or {}).get("name")
            if t.get("name") in PEOPLE_TABLES and (wanted is None or wb in wanted):
                found.setdefault(wb, {})[t["name"]] = {
                    "table_id": t["id"],
                    "workbook_id": (t.get("workbook") or {}).get("id")}
        cur = d.get("cursor")
        if not cur:
            break

    print(f"workbooks with people tables: {len(found)}")
    audit = {}
    for wb in sorted(found):
        audit[wb] = {}
        for tname, info in sorted(found[wb].items()):
            cols = run(b, ["tables", "columns", "list", info["table_id"]])
            names = []
            if cols is not None:
                data = cols.get("data") if isinstance(cols, dict) else cols
                names = [c.get("name") for c in data]
            info["n_columns"] = len(names)
            info["has_inputs"] = [c for c in INPUT_COLS if c in names]
            info["missing_inputs"] = [c for c in INPUT_COLS if c not in names]
            info["columns"] = names
            audit[wb][tname] = info
            print(f"  {wb[:34]:34} {tname:16} cols={len(names):3} "
                  f"missing={info['missing_inputs']}", flush=True)

    json.dump(audit, open(OUT, "w", encoding="utf-8"), indent=1,
              ensure_ascii=False)
    n_tables = sum(len(v) for v in audit.values())
    print(f"\n{n_tables} people tables across {len(audit)} workbooks")
    print("written:", OUT)


if __name__ == "__main__":
    main()
