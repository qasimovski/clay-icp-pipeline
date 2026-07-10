"""Persistent Clay automation worker.

Opens one logged-in browser session, navigates to the Interphex workbook, then
executes numbered command files from ./queue:

    queue/cmd_000.py, cmd_001.py, ...   (executed in order, once each)

Each command runs with `page`, `common`, `formula_lib` in scope. Stdout and
tracebacks are written to queue/cmd_NNN.ok or .err. Drop a file named STOP in
the queue to shut the worker down cleanly.
"""
import contextlib
import io
import os
import time
import traceback

import common
import formula_lib  # noqa: F401  (exposed to commands)

QUEUE = os.path.join(common.SCRIPT_DIR, "queue")
os.makedirs(QUEUE, exist_ok=True)


def main():
    with common.clay_page() as page:
        for attempt in range(10):
            try:
                common.open_interphex(page, table=common.MAIN_TABLE)
                break
            except Exception as e:
                print(f"nav retry {attempt}: {e}", flush=True)
                time.sleep(8)
        else:
            raise SystemExit("navigation failed after 10 attempts")
        open(os.path.join(QUEUE, "READY"), "w").close()
        print("READY", flush=True)

        n = 0
        while True:
            if os.path.exists(os.path.join(QUEUE, "STOP")):
                print("STOP seen — exiting", flush=True)
                break
            cmd = os.path.join(QUEUE, f"cmd_{n:03}.py")
            done_ok = os.path.join(QUEUE, f"cmd_{n:03}.ok")
            done_err = os.path.join(QUEUE, f"cmd_{n:03}.err")
            if os.path.exists(done_ok) or os.path.exists(done_err):
                n += 1
                continue
            if not os.path.exists(cmd):
                time.sleep(1)
                continue
            time.sleep(0.3)  # let the write finish
            buf = io.StringIO()
            try:
                with contextlib.redirect_stdout(buf):
                    code = open(cmd, encoding="utf-8").read()
                    exec(compile(code, cmd, "exec"),
                         {"page": page, "common": common,
                          "formula_lib": formula_lib})
                dest = done_ok
            except Exception:
                buf.write("\n" + traceback.format_exc())
                dest = done_err
            with open(dest, "w", encoding="utf-8") as f:
                f.write(buf.getvalue())
            print(f"cmd_{n:03} -> {os.path.basename(dest)}", flush=True)
            n += 1


if __name__ == "__main__":
    main()
