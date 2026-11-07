"""Build the scope manifests for the Highways / Competitive Events passes.

Writes one manifest per table type:
    highways_ce_exhibitors_manifest.json   (37 events)
    highways_ce_sponsors_manifest.json     (30 events)

keyed by workbook (event) name, each record carrying workbook_id / table_id /
table_name / rows / columns — the shape the apply passes expect.

Scope comes from `automation/clay_sync/highways_logs/table_ids.json`, harvested
and verified when the CSVs were imported. That file, not a folder listing, is
the authority: the `clay` CLI has no folder concept, so there is no read path
that can say "these tables are in Competitive Events" — but every table id in
that file was put there by the Highways import and verified row-for-row against
its source CSV.

`rows` is read from the source CSV (ordering + blast-radius reporting only,
never a correctness input). `columns` is read live from Clay, so a table already
carrying a template's outputs is visible in `--list` without opening a browser.

Run in WSL (the `clay` CLI is Linux-only here):
    python3 build_highways_ce_manifest.py            # both manifests
    python3 build_highways_ce_manifest.py --no-columns   # skip the live read
"""
import argparse
import csv
import glob
import json
import os
import subprocess
import sys
import time

CLEAN = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
IDS = os.path.join(os.path.dirname(SCRIPT_DIR), "clay_sync", "highways_logs",
                   "table_ids.json")
SCRAPERS = os.environ.get("CLAY_HIGHWAYS_SCRAPERS_ROOT",
                          "/mnt/c/Users/qasim/terrapinn/scrapers_highways")
TABLES = ("exhibitors_normalized", "sponsors_normalized")


def clay_bin():
    base = os.path.expanduser("~/.config/clay/bin")
    c = sorted(glob.glob(os.path.join(base, "clay-*-linux-x64")) +
               glob.glob(os.path.join(base, "clay-*-linux-arm64")))
    return c[-1] if c else "clay"


def clay_json(args, tries=4):
    env = dict(os.environ)
    env["PATH"] = CLEAN
    for _ in range(tries):
        r = subprocess.run([clay_bin(), *args], capture_output=True, text=True,
                           env=env)
        if r.returncode == 0 and r.stdout.strip():
            try:
                return json.loads(r.stdout)
            except json.JSONDecodeError:
                pass
        time.sleep(2)
    raise SystemExit(f"clay {' '.join(args)} failed: {(r.stderr or '')[:200]}")


def all_tables():
    """{table_id: {"workbook_id":..., "workbook_name":..., "name":...}} — the
    whole workspace, paged. Cheaper than one call per table id."""
    out, cursor = {}, None
    while True:
        args = ["tables", "list", "--limit", "100"]   # CLI caps --limit at 100
        if cursor:
            args += ["--cursor", cursor]
        d = clay_json(args)
        for t in d.get("data") or []:
            out[t["id"]] = {"workbook_id": (t.get("workbook") or {}).get("id"),
                            "workbook_name": (t.get("workbook") or {}).get("name"),
                            "name": t.get("name")}
        cursor = d.get("cursor")
        if not cursor:
            return out


def csv_rows(event, table):
    p = os.path.join(SCRAPERS, event, table + ".csv")
    if not os.path.isfile(p):
        return None
    with open(p, encoding="utf-8-sig", newline="") as fh:
        return sum(1 for _ in csv.DictReader(fh))


def columns_of(table_id):
    d = clay_json(["tables", "columns", "list", table_id])
    data = d.get("data") if isinstance(d, dict) else d
    return [c.get("name") for c in (data or [])]


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-columns", action="store_true",
                    help="skip the live column read (one CLI call per table)")
    args = ap.parse_args()

    with open(IDS, encoding="utf-8") as fh:
        ids = json.load(fh)
    live = all_tables()

    for table in TABLES:
        manifest, missing = {}, []
        for event in sorted(ids):
            tid = (ids[event] or {}).get(table)
            if not tid:
                continue
            meta = live.get(tid)
            if not meta:
                missing.append(f"{event}/{table} ({tid})")
                continue
            # The harvested id must still name the table we think it does.
            # A mismatch means table_ids.json is stale; refuse rather than
            # point a template pass at the wrong table.
            if meta["name"] != table:
                raise SystemExit(
                    f"{event}: id {tid} is named {meta['name']!r}, expected "
                    f"{table!r} — re-harvest table_ids.json")
            rec = {"workbook_id": meta["workbook_id"],
                   "table_id": tid,
                   "table_name": table,
                   "rows": csv_rows(event, table)}
            if not args.no_columns:
                rec["columns"] = columns_of(tid)
            manifest[event] = rec
        out = os.path.join(SCRIPT_DIR,
                           f"highways_ce_{table.split('_')[0]}_manifest.json")
        tmp = out + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, indent=1, sort_keys=True)
        os.replace(tmp, out)
        print(f"{out}: {len(manifest)} events"
              + (f" | MISSING {missing}" if missing else ""))


if __name__ == "__main__":
    sys.exit(main())
