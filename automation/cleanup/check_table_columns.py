"""Print one table's column names, one per line (run in WSL). Used by the
rollout's pre-apply guard to ask Clay whether a table is already built."""
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
    for _ in range(5):
        r = subprocess.run([b, "tables", "columns", "list", sys.argv[1]],
                           capture_output=True, text=True, env=env)
        if r.returncode == 0 and r.stdout.strip():
            try:
                d = json.loads(r.stdout)
            except json.JSONDecodeError:
                time.sleep(2)
                continue
            data = d.get("data") if isinstance(d, dict) else d
            for col in data:
                print(col.get("name"))
            return 0
        time.sleep(2)
    return 1


if __name__ == "__main__":
    sys.exit(main())
