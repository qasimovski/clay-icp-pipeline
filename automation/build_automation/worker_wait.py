"""Poll for a worker result file and print it.

Usage: python worker_wait.py cmd_000 [timeout_s]
"""
import os
import re
import sys
import time

QUEUE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "queue")
# Command names are worker.py's own cmd_NNN convention. Validating against it
# keeps the name a name: it is joined into a path, so "../../etc/passwd" used
# to read straight out of the queue directory.
NAME_RE = re.compile(r"^cmd_\d{3,}$")


def main(argv):
    if not argv:
        raise SystemExit(__doc__.strip())
    name = argv[0]
    if not NAME_RE.match(name):
        raise SystemExit(f"bad command name {name!r}: expected cmd_NNN")
    if len(argv) > 1:
        try:
            timeout = int(argv[1])
        except ValueError:
            raise SystemExit(f"bad timeout {argv[1]!r}: expected seconds")
    else:
        timeout = 180

    deadline = time.time() + timeout
    while time.time() < deadline:
        for ext in ("ok", "err"):
            path = os.path.join(QUEUE, f"{name}.{ext}")
            if os.path.exists(path):
                print(f"=== {name}.{ext} ===")
                with open(path, encoding="utf-8") as fh:
                    print(fh.read())
                return 0 if ext == "ok" else 1
        time.sleep(2)
    print(f"TIMEOUT waiting for {name}")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
