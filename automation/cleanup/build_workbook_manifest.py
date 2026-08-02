"""Build the column-trim manifest: for each in-scope Competitive Events
<entity> main table, list the columns to the RIGHT of the entity's cut column
(these get deleted; everything up to and including the cut column is kept).

Entity-aware via CLAY_PIPELINE_ENTITY (default exhibitors): Exhibitors cuts at
'Website'; Sponsors cuts at 'Year' (see config/entity-types/<entity>.yaml).

Inventory-driven and read-only. Column order comes from `clay tables columns
list` which matches the UI's left-to-right display order (verified via recon).

Outputs (next to this script):
  cols_manifest.json          per-workbook {workbook_id, name, table_id,
                              keep:[...], delete:[...]} (delete in display order)
  cols_snapshot.json          pre-run full column list per in-scope table

Scope: workbooks inside Competitive Events (competitive_events_workbooks.json)
that have the entity's main table, excluding PROTECTED. Run in WSL/Linux.
"""

import glob
import json
import os
import subprocess
import sys
import time

import pipeline_config as pcfg

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
FOLDER_PATH = os.path.join(SCRIPT_DIR, "competitive_events_workbooks.json")

# Entity-driven (config/entity-types/<entity>.yaml, via CLAY_PIPELINE_ENTITY).
_CFG = pcfg.load()
_SLUG = _CFG.slug()
TABLE = _CFG.main_table
CUT_COLUMN = _CFG.entity_cfg.get("trim_cut_column", "Website")  # keep this + left; delete right
# Per-entity manifest/snapshot: Sponsors & Exhibitors live in the SAME workbooks,
# so one fixed cols_manifest.json would collide (and a Sponsors build would clobber
# the Exhibitors one). The slug (e.g. sponsors_labs) keeps them separate.
MANIFEST_PATH = os.path.join(SCRIPT_DIR, f"cols_manifest_{_SLUG}.json")
SNAPSHOT_PATH = os.path.join(SCRIPT_DIR, f"cols_snapshot_{_SLUG}.json")
CLEAN_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

# Workbooks excluded from the trim. Falls back to the Exhibitors historical set
# (table-cleanup 7 + Analytica) when the entity config doesn't set
# trim_protected_workbooks; Sponsors sets its own (block-list tables only).
_DEFAULT_PROTECTED = {
    "Labs - Block List - Companies", "Labs - Block List - People",
    "ACHEMA", "ACHEMA Middle East", "ADLM USA",
    "American Chemical Society Fall (ACS)",
    "American Chemical Society Spring (ACS)", "Analytica",
}
PROTECTED = set(_CFG.entity_cfg.get("trim_protected_workbooks") or _DEFAULT_PROTECTED)


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
