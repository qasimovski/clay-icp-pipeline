"""Full-table fill and value distribution for one column (run in WSL).

`check_column_fill.py` is a run-GUARD: it only has to answer "has this column
any values at all", so it stops at a 20-page (2,000-row) sample and says
`truncated`. Reading those numbers as a completion percentage is wrong — on an
8,631-row table the sample covers under a quarter of it.

This walks every page and reports the real totals plus how the values split, so
a blocklist run can be confirmed as FINISHED (nothing empty) and quantified
(how many companies came back `Is New`) rather than merely started.

    python3 check_ledger_column.py <tableId> "Is New"
    python3 check_ledger_column.py <tableId> "Is New" --max-pages 500
"""
import argparse
import collections
import glob
import json
import os
import subprocess
import sys
import time

CLEAN = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"


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
    # Never return an empty page on failure: a caller would read it as "no more
    # rows" and report a partial table as complete.
    raise SystemExit(f"clay call failed: {' '.join(args)}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("table_id")
    ap.add_argument("column")
    ap.add_argument("--max-pages", type=int, default=500)
    args = ap.parse_args()

    b = clay()
    cols = run(b, ["tables", "columns", "list", args.table_id])
    cols = cols.get("data") if isinstance(cols, dict) else cols
    fid = next((c.get("id") for c in cols if c.get("name") == args.column), None)
    if not fid:
        print(f"column {args.column!r} not on this table")
        return 1

    rows, cur, pages = [], None, 0
    while pages < args.max_pages:
        a = ["tables", "rows", "list", args.table_id, "--limit", "100"]
        if cur:
            a += ["--cursor", cur]
        d = run(b, a)
        rows += d.get("data", [])
        cur = d.get("cursor")
        pages += 1
        if not cur:
            break

    dist = collections.Counter()
    empty = 0
    for r in rows:
        cell = (r.get("cells") or {}).get(fid)
        val = cell.get("value") if isinstance(cell, dict) else cell
        if val in (None, "", []):
            empty += 1
        else:
            dist[str(val)] += 1

    total = len(rows)
    filled = total - empty
    print(f"table  : {args.table_id}")
    print(f"column : {args.column}")
    print(f"rows   : {total:,}{'  (MORE REMAIN - raise --max-pages)' if cur else ''}")
    print(f"filled : {filled:,} ({filled / total:.1%})" if total else "filled : 0")
    print(f"empty  : {empty:,}")
    for v, n in dist.most_common(10):
        print(f"   {v!r:>12}: {n:,}")
    if cur:
        return 1
    return 0 if empty == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
