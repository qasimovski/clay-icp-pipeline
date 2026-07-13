"""Build the column-trim manifest: for each in-scope Competitive Events
Exhibitors_normalized table, list the columns to the RIGHT of 'Normalized
Country' (these get deleted; everything up to and including Normalized Country
is kept).

Inventory-driven and read-only. Column order comes from `clay tables columns
list` which matches the UI's left-to-right display order (verified via recon).

Outputs (next to this script):
  cols_manifest.json          per-workbook {workbook_id, name, table_id,
                              keep:[...], delete:[...]} (delete in display order)
  cols_snapshot.json          pre-run full column list per in-scope table

Scope: workbooks inside Competitive Events (competitive_events_workbooks.json)
that have an Exhibitors_normalized table, excluding PROTECTED. Run in WSL/Linux.
"""

import glob
import json
import os
import subprocess
import sys
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
FOLDER_PATH = os.path.join(SCRIPT_DIR, "competitive_events_workbooks.json")
MANIFEST_PATH = os.path.join(SCRIPT_DIR, "cols_manifest.json")
SNAPSHOT_PATH = os.path.join(SCRIPT_DIR, "cols_snapshot.json")

TABLE = "Exhibitors_normalized"
CUT_COLUMN = "Website"   # keep this and everything left; delete right (revert pass)
CLEAN_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

# This task's protected set = the 7 from the table cleanup + Analytica.
PROTECTED = {
    "Labs - Block List - Companies", "Labs - Block List - People",
    "ACHEMA", "ACHEMA Middle East", "ADLM USA",
    "American Chemical Society Fall (ACS)",
    "American Chemical Society Spring (ACS)", "Analytica",
}


def clay_bin():
    env = os.environ.get("CLAY_BIN")
    if env:
        return env
    for base in (os.path.expanduser("~/.config/clay/bin"),):
        c = sorted(glob.glob(os.path.join(base, "clay-*-linux-x64")) +
                   glob.glob(os.path.join(base, "clay-*-linux-arm64")))
        if c:
            return c[-1]
    return "clay"


def run(bin_, args):
    env = dict(os.environ); env["PATH"] = CLEAN_PATH
    for _ in range(6):
        r = subprocess.run([bin_] + args, capture_output=True, text=True, env=env)
        if r.returncode == 0 and r.stdout.strip():
            try:
                return json.loads(r.stdout)
            except json.JSONDecodeError:
                pass
        time.sleep(2)
    raise SystemExit(f"clay {' '.join(args)} failed: {r.stderr[:200]}")


def list_all_tables(bin_):
    out, cur = [], None
    for _ in range(200):
        d = run(bin_, ["tables", "list", "--limit", "100"] +
                (["--cursor", cur] if cur else []))
        out += d.get("data", []); cur = d.get("cursor")
        if not cur:
            break
    return out


def columns(bin_, tid):
    d = run(bin_, ["tables", "columns", "list", tid])
    data = d.get("data") if isinstance(d, dict) else d
    return [c.get("name") for c in data]


def main():
    if not os.path.exists(FOLDER_PATH):
        raise SystemExit("run folder_scope.py first (need competitive_events_workbooks.json)")
    folder_ids = set(json.load(open(FOLDER_PATH, encoding="utf-8")))
    bin_ = clay_bin()
    tables = list_all_tables(bin_)

    # in-scope Exhibitors_normalized tables: in folder, not protected
    targets = []
    for t in tables:
        wo = t.get("workbook") or {}
        if t.get("name") != TABLE:
            continue
        if wo.get("id") not in folder_ids:
            continue
        if (wo.get("name") or "") in PROTECTED:
            continue
        targets.append((wo["id"], wo.get("name") or "", t["id"]))

    manifest, snapshot, problems = [], {}, []
    for wid, name, tid in sorted(targets, key=lambda x: x[1]):
        cols = columns(bin_, tid)
        snapshot[tid] = {"workbook_name": name, "columns": cols}
        if CUT_COLUMN not in cols:
            problems.append(f"{name}: no {CUT_COLUMN!r} column — SKIPPED")
            continue
        i = cols.index(CUT_COLUMN)
        keep = cols[: i + 1]
        delete = cols[i + 1:]
        if not delete:
            continue  # already trimmed
        manifest.append({"workbook_id": wid, "workbook_name": name,
                         "table_id": tid, "keep": keep, "delete": delete})

    manifest.sort(key=lambda e: e["workbook_name"])
    json.dump(snapshot, open(SNAPSHOT_PATH, "w", encoding="utf-8"),
              indent=2, ensure_ascii=False)
    payload = {"table": TABLE, "cut_column": CUT_COLUMN,
               "protected": sorted(PROTECTED), "workbook_count": len(manifest),
               "delete_total": sum(len(e["delete"]) for e in manifest),
               "workbooks": manifest}
    json.dump(payload, open(MANIFEST_PATH, "w", encoding="utf-8"),
              indent=2, ensure_ascii=False)

    print(f"in-scope workbooks to trim: {len(manifest)}")
    print(f"total columns to delete: {payload['delete_total']}")
    if problems:
        print("PROBLEMS:")
        for p in problems:
            print("  " + p)
    # show the distinct delete-list shapes
    from collections import Counter
    shapes = Counter(tuple(e["delete"]) for e in manifest)
    print(f"\ndistinct delete-list shapes: {len(shapes)}")
    for shape, n in shapes.most_common():
        print(f"\n[{n} workbooks] delete {len(shape)} cols:")
        print("  " + ", ".join(shape))
    print(f"\nwrote {MANIFEST_PATH}\nwrote {SNAPSHOT_PATH}")


if __name__ == "__main__":
    main()
