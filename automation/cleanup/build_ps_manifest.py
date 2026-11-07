"""Regenerate the Products & Services companies manifest from live Clay (WSL).

Writes `highways_ps_companies_manifest.json` / `highways_bs_companies_manifest.json`
— the `{category: {workbook_id, table_id, table_name, rows, columns[]}}` scope
files that `apply_highways_supabase.py` and `apply_isnew_filter.py` read.

WHY THIS EXISTS. Those two manifests were assembled by hand during the
2026-08-14/15 build and had no writer, so the repo could read them but not
rebuild them. CLAUDE.md's instruction for a hand-curated scope is "regenerate
the scope file instead" — which was not actually possible for P&S until now.
A manifest nobody can regenerate is a manifest that silently goes stale.

WHY IT DOES NOT REUSE `list_workbook_tables.py` PER CATEGORY. That script pages
the WHOLE workspace table list and filters client-side (the API has no workbook
filter), so calling it once per category re-pages the workspace 23 times — which
is why `verify_ps_companies.py` takes many minutes. Here the listing is paged
ONCE into `{workbook_id: [(table_id, name)]}` and reused. Same data, same
`clay_bin` binary selection, ~23x fewer calls.

FAIL LOUD, NEVER GUESS. A category whose row count or column list cannot be read
is written with `"rows": null` and reported, never as `0` / `[]` — an unreadable
table that lands in the manifest as empty would be dropped from every later pass
(the failure `check_table_rows.py` prints ERROR for). If any category fails, the
file is still written but the exit code is non-zero.

SCOPE COMES FROM THE CONFIG, not from the folder. The categories are
`config/icps/<icp>/products_services.yaml`, so a workbook sitting in the folder
that is not a category (a duplicate, a stray "Untitled workbook") cannot enter
the manifest. Their workbook ids come from the `*_workbooks.json` folder listing
written by `evcharge_ps_scope.py --out`.

    python3 build_ps_manifest.py --icp highways            # -> highways_ps_...
    python3 build_ps_manifest.py --icp highways_buyside    # -> highways_bs_...
    python3 build_ps_manifest.py --icp highways --dry-run  # report, write nothing
"""

import argparse
import json
import os
import sys
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

import clay_bin              # noqa: E402
import ps_query_config as psq  # noqa: E402

TABLE = "Companies"

# Per ICP: the folder listing to read workbook ids from, and the manifest to
# write. Filenames are byte-identical to what the consumers already open —
# CLAUDE.md: never rename anything persisted.
SCOPES = {
    "highways": ("highways_ps_workbooks.json",
                 "highways_ps_companies_manifest.json"),
    "highways_buyside": ("highways_bs_workbooks.json",
                         "highways_bs_companies_manifest.json"),
    "evcharge": ("evcharge_ps_workbooks.json",
                 "evcharge_ps_companies_manifest.json"),
    "evcharge_buyside": ("evcharge_buyside_workbooks.json",
                         "evcharge_bs_companies_manifest.json"),
}


def all_tables(binary):
    """{workbook_id: [(table_id, name), ...]} for the whole workspace, paged once."""
    out, cursor, last = {}, None, ""
    for _ in range(60):
        args = ["tables", "list", "--limit", "100"]
        if cursor:
            args += ["--cursor", cursor]
        ok, d, err = clay_bin.run(binary, args)
        if not ok:
            last = err
            if d is not None:
                raise SystemExit(f"table listing failed: {err}")
            time.sleep(2)
            continue
        rows = d.get("data") if isinstance(d, dict) else d
        for t in rows or []:
            wb = (t.get("workbook") or {}).get("id")
            if wb:
                out.setdefault(wb, []).append((t.get("id"), t.get("name")))
        cursor = d.get("cursor") if isinstance(d, dict) else None
        if not cursor or not rows:
            return out
    raise SystemExit(f"table listing did not terminate: {last}")


def table_rows(binary, tid):
    """Row count, or None when Clay could not tell us. Never 0 on failure."""
    for _ in range(4):
        ok, d, _ = clay_bin.run(binary, ("tables", "get", tid))
        if ok and isinstance(d, dict):
            n = d.get("rowCount")
            if n is None and isinstance(d.get("data"), dict):
                n = d["data"].get("rowCount")
            if n is not None:
                return n
            return None
        time.sleep(2)
    return None


def table_columns(binary, tid):
    """Column names, or None when unreadable. Never [] on failure."""
    for _ in range(3):
        ok, d, _ = clay_bin.run(binary, ("tables", "columns", "list", tid))
        if ok:
            data = d.get("data") if isinstance(d, dict) else d
            return [c.get("name") for c in data or []]
        if d is not None:
            return None
        time.sleep(2)
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--icp", default="highways", choices=sorted(SCOPES))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    listing_name, manifest_name = SCOPES[args.icp]
    listing_path = os.path.join(SCRIPT_DIR, listing_name)
    if not os.path.exists(listing_path):
        raise SystemExit(f"no folder listing at {listing_path} — regenerate it "
                         f"with `evcharge_ps_scope.py --folder <id> --out ...`")
    listing = json.load(open(listing_path, encoding="utf-8"))

    categories = [c["name"] for c in psq.load(args.icp)["categories"]]
    # name -> [workbook_id, ...]. A category with more than one workbook is the
    # duplicate case; it is reported rather than silently resolved.
    by_name = {}
    for wid, name in listing.items():
        by_name.setdefault(name, []).append(wid)

    binary = clay_bin.pick()
    tables = all_tables(binary)
    print(f"{args.icp}: {len(categories)} categories, "
          f"{len(listing)} workbooks in the folder listing, "
          f"{sum(len(v) for v in tables.values())} tables in the workspace\n")

    manifest, problems, dupes = {}, [], []
    for name in categories:
        wids = by_name.get(name) or []
        if not wids:
            problems.append(f"{name}: no workbook in {listing_name}")
            continue
        if len(wids) > 1:
            dupes.append((name, wids))
        # Pick the workbook that actually HOLDS a Companies table, not simply
        # the first one listed. The Highways folder carries an empty duplicate
        # "Asset Management & Maintenance" workbook, and taking wids[0] filed
        # the real 1,284-row table as "no Companies table" — a manifest that
        # would have dropped the category from every later pass.
        withtab = [w for w in wids
                   if any(n == TABLE for _, n in (tables.get(w) or []))]
        if len(withtab) > 1:
            problems.append(f"{name}: {len(withtab)} workbooks each hold a "
                            f"'{TABLE}' table — resolve by hand, refusing to "
                            f"guess which one is current")
        wid = withtab[0] if withtab else wids[0]

        tabs = tables.get(wid) or []
        want = [(tid, tname) for tid, tname in tabs if tname == TABLE]
        if not want:
            problems.append(f"{name}: workbook has no '{TABLE}' table "
                            f"(has {[t[1] for t in tabs]})")
            continue
        if len(want) > 1:
            problems.append(f"{name}: {len(want)} tables named '{TABLE}' — "
                            f"an interrupted run saved twice, and the duplicate "
                            f"is paid-for rows")
        tid, tname = want[0]

        rows = table_rows(binary, tid)
        cols = table_columns(binary, tid)
        if rows is None:
            problems.append(f"{name}: row count unreadable (written as null)")
        if cols is None:
            problems.append(f"{name}: columns unreadable (written as null)")

        manifest[name] = {"columns": cols, "rows": rows, "table_id": tid,
                          "table_name": tname, "workbook_id": wid}
        others = sorted({t[1] for t in tabs} - {TABLE})
        print(f"  {str(rows if rows is not None else 'ERROR'):>7}  {name[:46]:48}"
              + (f"  + {others}" if others else ""))

    total = sum(r["rows"] for r in manifest.values() if r["rows"] is not None)
    print(f"\n{len(manifest)} categories, {total:,} companies")

    if dupes:
        print(f"\nDUPLICATE WORKBOOKS ({len(dupes)}) — the manifest points at the "
              f"first; confirm which one holds the paid rows before deleting:")
        for name, wids in dupes:
            for wid in wids:
                tabs = tables.get(wid) or []
                n = next((table_rows(binary, t) for t, nm in tabs if nm == TABLE), None)
                print(f"   {name[:40]:42} {wid}  {TABLE}="
                      f"{n if n is not None else 'absent/unreadable'}")

    if problems:
        print(f"\n{len(problems)} problem(s):")
        for p in problems:
            print(f"   {p}")

    if args.dry_run:
        print("\n--dry-run: nothing written")
        return 1 if problems else 0

    out = os.path.join(SCRIPT_DIR, manifest_name)
    json.dump(manifest, open(out, "w", encoding="utf-8"),
              indent=1, sort_keys=True, ensure_ascii=False)
    print(f"\n-> {out}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
