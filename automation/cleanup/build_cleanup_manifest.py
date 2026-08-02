"""Build the Competitive Events cleanup manifest from the live Clay inventory.

Clay has no delete API, so deletion is done by driving the web UI (see
delete_byproduct_tables.py). This script does NOT delete anything — it only reads the
workspace table inventory via the `clay` CLI and writes an explicit, auditable
allowlist of exactly which tables to delete in which workbooks.

Outputs (next to this script):
  cleanup_manifest.json   the plan — one entry per in-scope workbook:
                          {workbook_id, workbook_name, keep:[...], delete:[...]}
  inventory_snapshot.json full pre-run inventory keyed by workbook_id, so a
                          post-run pass can prove the protected / look-alike
                          workbooks were left untouched.

Scope (see the approved plan):
  KEEP      = {Exhibitors_normalized, Sponsors_normalized}  (never deleted)
  in-scope  = workbooks that contain >=1 KEEP table AND are not PROTECTED
  PROTECTED = the 7 workbooks the user named — never opened, never in the manifest

Run it where the `clay` CLI works (WSL/Linux). The CLI binary is resolved from
$CLAY_BIN, else the cached plugin binary, else `clay` on PATH. Subprocess calls
use a clean POSIX PATH so the CLI doesn't trip over Windows-interop PATH entries.
"""

import glob
import json
import os
import subprocess
import sys
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
MANIFEST_PATH = os.path.join(SCRIPT_DIR, "cleanup_manifest.json")
SNAPSHOT_PATH = os.path.join(SCRIPT_DIR, "inventory_snapshot.json")

KEEP = {"Exhibitors_normalized", "Sponsors_normalized"}
PROTECTED = {
    "Labs - Block List - Companies",
    "Labs - Block List - People",
    "ACHEMA",
    "ACHEMA Middle East",
    "ADLM USA",
    "American Chemical Society Fall (ACS)",
    "American Chemical Society Spring (ACS)",
}

# A clean POSIX PATH — the CLI shells out and chokes on WSL's Windows-interop
# PATH entries ("C:/Program Files/..."), so we never inherit those.
CLEAN_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"


def clay_bin():
    """Resolve a runnable clay CLI binary. Prefer $CLAY_BIN, then the cached
    plugin linux binary (the wrapper script has CRLF shebang issues under WSL),
    then a bare `clay` on PATH."""
    env = os.environ.get("CLAY_BIN")
    if env:
        return env
    for base in (os.path.expanduser("~/.config/clay/bin"),
                 os.path.expanduser("~/.config/clay")):
        cands = sorted(glob.glob(os.path.join(base, "clay-*-linux-x64")) +
                       glob.glob(os.path.join(base, "clay-*-linux-arm64")))
        if cands:
            return cands[-1]
    return "clay"


def _run(bin_, args):
    env = dict(os.environ)
    env["PATH"] = CLEAN_PATH
    for _ in range(5):
        r = subprocess.run([bin_] + args, capture_output=True, text=True, env=env)
        if r.returncode == 0 and r.stdout.strip():
            try:
                return json.loads(r.stdout)
            except json.JSONDecodeError:
                pass
        time.sleep(2)
    raise SystemExit(f"clay {' '.join(args)} failed: {r.returncode} {r.stderr[:300]}")


def list_all_tables(bin_):
    """Every table in the workspace: {id, name, workbook:{id,name}, owner...}."""
    tables, cursor = [], None
    for _ in range(200):
        args = ["tables", "list", "--limit", "100"]
        if cursor:
            args += ["--cursor", cursor]
        d = _run(bin_, args)
        tables += d.get("data", [])
        cursor = d.get("cursor")
        if not cursor:
            break
    return tables


def main():
    bin_ = clay_bin()
    print(f"using clay binary: {bin_}", flush=True)
    tables = list_all_tables(bin_)
    print(f"fetched {len(tables)} tables", flush=True)

    # index by workbook id -> {name, tables:set}
    books = {}
    for t in tables:
        wo = t.get("workbook") or {}
        wid = wo.get("id")
        if not wid:
            continue
        b = books.setdefault(wid, {"workbook_id": wid,
                                   "workbook_name": wo.get("name") or "",
                                   "tables": []})
        b["tables"].append(t.get("name"))

    # full snapshot (everything) for the untouched-check later
    snapshot = {wid: {"workbook_name": b["workbook_name"],
                      "tables": sorted(b["tables"])}
                for wid, b in books.items()}
    with open(SNAPSHOT_PATH, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, indent=2, ensure_ascii=False)

    manifest = []
    skipped_protected, skipped_no_keep = [], []
    for wid, b in books.items():
        name = b["workbook_name"]
        names = b["tables"]
        keep = sorted({n for n in names if n in KEEP})
        if name in PROTECTED:
            skipped_protected.append(name)
            continue
        if not keep:
            # no normalized table -> nothing to keep -> would empty it -> skip
            skipped_no_keep.append(name)
            continue
        delete = sorted([n for n in names if n not in KEEP])
        if not delete:
            continue  # already clean
        manifest.append({"workbook_id": wid, "workbook_name": name,
                         "keep": keep, "delete": delete})

    # hard invariant: never emit a workbook that would be left empty
    for e in manifest:
        assert e["keep"], f"refusing empty-keep workbook {e['workbook_name']!r}"
        assert not (set(e["delete"]) & KEEP), \
            f"delete list overlaps KEEP for {e['workbook_name']!r}"

    manifest.sort(key=lambda e: (e["workbook_name"], e["workbook_id"]))
    total_del = sum(len(e["delete"]) for e in manifest)
    payload = {"keep": sorted(KEEP), "protected": sorted(PROTECTED),
               "workbook_count": len(manifest), "delete_total": total_del,
               "workbooks": manifest}
    with open(MANIFEST_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)

    print(f"\nmanifest: {len(manifest)} workbooks, {total_del} tables to delete")
    print(f"skipped (protected): {len(set(skipped_protected))} "
          f"-> {sorted(set(skipped_protected))}")
    print(f"skipped (no normalized table, left untouched): "
          f"{len(skipped_no_keep)}")
    dup = {}
    for e in manifest:
        dup.setdefault(e["workbook_name"], []).append(e["workbook_id"])
    dups = {n: ids for n, ids in dup.items() if len(ids) > 1}
    if dups:
        print(f"duplicate-named in-scope workbooks (by id): {dups}")
    print(f"\nwrote {MANIFEST_PATH}")
    print(f"wrote {SNAPSHOT_PATH}")


if __name__ == "__main__":
    sys.exit(main())
