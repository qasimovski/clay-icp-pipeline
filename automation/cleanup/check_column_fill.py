"""Print "<filled> <total>" for one column of a table (run in WSL).

Used as a run-guard: if WORK EMAIL already has values, the waterfall has already
run and must not be triggered again. State files cannot be trusted for this —
a late-finishing worker overwrote one with a stale snapshot and caused SLAS
Europe's run to be triggered (and re-charged) twice.

    python3 check_column_fill.py <tableId> "WORK EMAIL"
"""
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
    raise SystemExit(1)


def main():
    tid, col = sys.argv[1], sys.argv[2]
    b = clay()
    cols = run(b, ["tables", "columns", "list", tid])
    cols = cols.get("data") if isinstance(cols, dict) else cols
    fid = next((c.get("id") for c in cols if c.get("name") == col), None)
    if not fid:
        print("0 0")
        return 0
    rows, cur, pages = [], None, 0
    while pages < 20:
        args = ["tables", "rows", "list", tid, "--limit", "100"]
        if cur:
            args += ["--cursor", cur]
        d = run(b, args)
        rows += d.get("data", [])
        cur = d.get("cursor")
        pages += 1
        if not cur:
            break
    filled = 0
    for r in rows:
        cell = (r.get("cells") or {}).get(fid)
        if isinstance(cell, dict) and cell.get("value"):
            filled += 1
    print(f"{filled} {len(rows)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
