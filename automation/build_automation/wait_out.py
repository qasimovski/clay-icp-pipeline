"""Poll for a worker result file and print it. Usage: python wait_out.py cmd_000 [timeout_s]"""
import os
import sys
import time

name = sys.argv[1]
timeout = int(sys.argv[2]) if len(sys.argv) > 2 else 180
qdir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "queue")
deadline = time.time() + timeout
while time.time() < deadline:
    for ext in ("ok", "err"):
        p = os.path.join(qdir, f"{name}.{ext}")
        if os.path.exists(p):
            print(f"=== {name}.{ext} ===")
            print(open(p, encoding="utf-8").read())
            sys.exit(0 if ext == "ok" else 1)
    time.sleep(2)
print(f"TIMEOUT waiting for {name}")
sys.exit(2)
