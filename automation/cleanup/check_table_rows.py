"""Print a table's row count (run in WSL). Used to order the rollout smallest-first."""
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
    for _ in range(4):
        r = subprocess.run([b, "tables", "get", sys.argv[1]],
                           capture_output=True, text=True, env=env)
        if r.returncode == 0 and r.stdout.strip():
            try:
                d = json.loads(r.stdout)
            except json.JSONDecodeError:
                time.sleep(2)
                continue
            n = d.get("rowCount")
            if n is None and isinstance(d.get("data"), dict):
                n = d["data"].get("rowCount")
            if n is None:
                # A response without a row count is not a zero-row table.
                print("ERROR", file=sys.stderr)
                return 1
            print(n)
            return 0
        time.sleep(2)
    # Never print a number here: a CLI failure that looks like "0 rows" makes
    # the caller file the table as empty and drop it from every future batch.
    print("ERROR", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
