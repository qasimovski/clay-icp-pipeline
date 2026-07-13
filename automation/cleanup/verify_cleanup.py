"""Verify the Competitive Events cleanup, read-only, via the `clay` CLI.

Re-reads the live table inventory and checks:
  1. every in-scope workbook (by id, from cleanup_manifest.json) now has a table
     set that is a SUBSET of KEEP — i.e. all byproduct tables are gone;
  2. the KEEP table(s) each in-scope workbook was supposed to keep are still
     present;
  3. the untouched set (protected workbooks + everything not in the manifest)
     is unchanged vs inventory_snapshot.json captured before the run.

Deletes nothing. Run where the `clay` CLI works (WSL/Linux). Because the CLI can
lag behind UI deletions, --wait retries until the inventory settles.

  python verify_cleanup.py
  python verify_cleanup.py --wait 120
"""

import argparse
import json
import os
import sys
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
import build_cleanup_manifest as bm  # reuse clay_bin + list_all_tables

MANIFEST_PATH = os.path.join(SCRIPT_DIR, "cleanup_manifest.json")
SNAPSHOT_PATH = os.path.join(SCRIPT_DIR, "inventory_snapshot.json")
KEEP = bm.KEEP


def current_by_id(bin_):
    tables = bm.list_all_tables(bin_)
    books = {}
    for t in tables:
        wo = t.get("workbook") or {}
        wid = wo.get("id")
        if not wid:
            continue
        b = books.setdefault(wid, {"name": wo.get("name") or "", "tables": []})
        b["tables"].append(t.get("name"))
    return books


def check(books, manifest, snapshot):
    problems = []
    in_scope_ids = {e["workbook_id"] for e in manifest["workbooks"]}

    # 1 + 2: in-scope workbooks reduced to KEEP subset, keep tables intact
    for e in manifest["workbooks"]:
        wid = e["workbook_id"]
        cur = set(books.get(wid, {}).get("tables", []))
        leftover = cur - KEEP
        if leftover:
            problems.append(f"[not-clean] {e['workbook_name']} [{wid}] still has "
                            f"{sorted(leftover)}")
        keep_missing = set(e["keep"]) - cur
        if keep_missing:
            problems.append(f"[keep-lost] {e['workbook_name']} [{wid}] lost "
                            f"{sorted(keep_missing)}")

    # 3: untouched set unchanged vs snapshot
    for wid, snap in snapshot.items():
        if wid in in_scope_ids:
            continue
        before = set(snap["tables"])
        after = set(books.get(wid, {}).get("tables", []))
        if before != after:
            problems.append(f"[untouched-changed] {snap['workbook_name']} [{wid}] "
                            f"before={sorted(before)} after={sorted(after)}")
    return problems


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wait", type=int, default=0,
                    help="seconds to keep retrying while the CLI catches up")
    args = ap.parse_args()

    manifest = json.load(open(MANIFEST_PATH, encoding="utf-8"))
    snapshot = json.load(open(SNAPSHOT_PATH, encoding="utf-8"))
    bin_ = bm.clay_bin()

    deadline = time.time() + args.wait
    while True:
        books = current_by_id(bin_)
        problems = check(books, manifest, snapshot)
        if not problems or time.time() >= deadline:
            break
        print(f"{len(problems)} problems; retrying in 15s "
              f"({int(deadline - time.time())}s left)...", flush=True)
        time.sleep(15)

    if not problems:
        print(f"OK: all {manifest['workbook_count']} in-scope workbooks reduced "
              f"to {sorted(KEEP)} only; untouched set unchanged.")
        return 0
    print(f"FAILED: {len(problems)} problem(s):")
    for p in problems:
        print("  " + p)
    return 1


if __name__ == "__main__":
    sys.exit(main())
