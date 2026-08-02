"""Print `<table_id>|<name>` for every table in a workbook (run in WSL).

Used as the duplicate guard for the people-search pass: the bottom tab bar
overlaps the last grid row in the UI, so Clay's own table list is the reliable
source of truth.
"""
import glob
import json
import os
import subprocess
import sys
import time

CLEAN = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"


def main():
    base = os.path.expanduser("~/.config/clay/bin")
    c = sorted(glob.glob(os.path.join(base, "clay-*-linux-x64")) +
               glob.glob(os.path.join(base, "clay-*-linux-arm64")))
    b = c[-1] if c else "clay"
    env = dict(os.environ)
    env["PATH"] = CLEAN
    wb = sys.argv[1]
    # the API has no workbook filter, so page through and match client-side
    cursor, seen = None, 0
    for _ in range(60):
        cmd = [b, "tables", "list", "--limit", "100"]
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
        for t in rows:
            seen += 1
            if ((t.get("workbook") or {}).get("id")) == wb:
                print(f"{t.get('id')}|{t.get('name')}")
        cursor = d.get("cursor") if isinstance(d, dict) else None
        if not cursor or not rows:
            return 0
    sys.stderr.write((r.stderr or "")[:300])
    return 1


if __name__ == "__main__":
    sys.exit(main())
