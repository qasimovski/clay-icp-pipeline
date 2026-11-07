"""Build the scope for the "Qasim - CPJ - Labs - People" pass (run in WSL).

Writes cpj_people_targets.json: for every Competitive Events workbook, the live
table id of its "Sellers - People" and/or "Buyers - People" table.

  { "<event>": {"workbook_id": "wb_...",
                "tables": {"Sellers - People": "t_...",
                           "Buyers - People":  "t_..."}} }

Why a fresh manifest rather than reusing people_email_audit.json: that file is
the email pass's July snapshot. Its table ids all still resolve, but it covers 76
workbooks while Competitive Events holds 97, so trusting it would silently skip
any People table built since. This pages Clay's own table list once (each row
carries workbook.id) and matches client-side -- the API has no workbook filter.

The workbook -> folder mapping is the one thing the CLI cannot answer (it exposes
no folder info), so folder membership comes from competitive_events_workbooks.json.
Any Sellers/Buyers - People table found in a workbook OUTSIDE that file is
reported under "unscoped" instead of being dropped, so a stale folder snapshot
shows up as a number to look at rather than as silently missing work.

  python3 build_cpj_targets.py
  python3 build_cpj_targets.py --out /tmp/targets.json
"""
import argparse
import glob
import json
import os
import subprocess
import sys
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CLEAN = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
FOLDER_FILE = os.path.join(SCRIPT_DIR, "competitive_events_workbooks.json")
# Exhibitors-entity people tables by default; the Sponsors entity uses its own
# table names ("Sponsors - Sellers - People" / "Sponsors - Buyers - People"), so
# the names are selectable rather than hard-coded.
WANTED = ("Sellers - People", "Buyers - People")
SPONSORS = ("Sponsors - Sellers - People", "Sponsors - Buyers - People")


def clay_bin():
    base = os.path.expanduser("~/.config/clay/bin")
    c = sorted(glob.glob(os.path.join(base, "clay-*-linux-x64")) +
               glob.glob(os.path.join(base, "clay-*-linux-arm64")))
    return c[-1] if c else "clay"


def all_tables(binary):
    """Every table in the workspace, paged. Raises rather than returning a short
    list: a truncated inventory would quietly narrow the rollout's scope."""
    env = dict(os.environ)
    env["PATH"] = CLEAN
    out, cursor = [], None
    for _ in range(200):
        cmd = [binary, "tables", "list", "--limit", "100"]
        if cursor:
            cmd += ["--cursor", cursor]
        r = subprocess.run(cmd, capture_output=True, text=True, env=env)
        if r.returncode != 0 or not r.stdout.strip():
            time.sleep(2)
            continue
        try:
            d = json.loads(r.stdout)
        except json.JSONDecodeError:
            time.sleep(2)
            continue
        rows = d.get("data") if isinstance(d, dict) else d
        out.extend(rows or [])
        cursor = d.get("cursor") if isinstance(d, dict) else None
        if not cursor or not rows:
            return out
    raise SystemExit("table listing did not terminate — refusing to write a "
                     "possibly truncated scope")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(SCRIPT_DIR,
                                                  "cpj_people_targets.json"))
    ap.add_argument("--sponsors", action="store_true",
                    help="scope the Sponsors-entity people tables instead")
    ap.add_argument("--table", action="append", default=[],
                    help="explicit table name(s); overrides --sponsors")
    a = ap.parse_args()
    wanted = tuple(a.table) if a.table else (SPONSORS if a.sponsors else WANTED)

    folder = json.load(open(FOLDER_FILE, encoding="utf-8"))   # wb_id -> name
    tables = all_tables(clay_bin())
    print(f"workspace tables listed: {len(tables)}")

    targets, unscoped = {}, []
    for t in tables:
        if t.get("name") not in wanted:
            continue
        wid = (t.get("workbook") or {}).get("id")
        if wid not in folder:
            unscoped.append({"table_id": t.get("id"), "name": t.get("name"),
                             "workbook_id": wid,
                             "workbook_name": (t.get("workbook") or {}).get("name")})
            continue
        ev = folder[wid]
        targets.setdefault(ev, {"workbook_id": wid, "tables": {}})
        targets[ev]["tables"][t["name"]] = t["id"]

    n_tables = sum(len(v["tables"]) for v in targets.values())
    sellers = sum(1 for v in targets.values() if wanted[0] in v["tables"])
    buyers = sum(1 for v in targets.values() if wanted[1] in v["tables"])
    print(f"Competitive Events workbooks in folder file: {len(folder)}")
    print(f"  with a People table: {len(targets)}")
    print(f"  tables in scope: {n_tables}  (sellers {sellers}, buyers {buyers})")
    print(f"  table names: {wanted}")
    both = [e for e, v in targets.items() if len(v["tables"]) == 2]
    print(f"  events with both tables: {len(both)}")
    if unscoped:
        print(f"\n!! {len(unscoped)} People table(s) in workbooks NOT in "
              f"{os.path.basename(FOLDER_FILE)} — review before rolling out:")
        for u in unscoped[:40]:
            print(f"     {u['workbook_name']!r} ({u['workbook_id']}) "
                  f"{u['name']} {u['table_id']}")

    payload = {"targets": targets, "unscoped": unscoped}
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=1, ensure_ascii=False)
    print(f"\nwrote {a.out}")


if __name__ == "__main__":
    sys.exit(main())
