"""Persistent Clay automation worker.

Opens one logged-in browser session, navigates to the Interphex workbook, then
executes numbered command files from ./queue:

    queue/cmd_000.py, cmd_001.py, ...   (executed in order, once each)

Each command runs with `page`, `browser_session`, `formula_columns` in scope. Stdout and
tracebacks are written to queue/cmd_NNN.ok or .err. Drop a file named STOP in
the queue to shut the worker down cleanly.

TRUST BOUNDARY: this executes whatever Python lands in ./queue, against a
browser holding a live Clay session with delete rights. Anything that can
write to that directory therefore controls the workspace. Keep the queue
directory local and operator-owned; never point a worker at a shared,
synced, or world-writable path.

Writers must publish a command ATOMICALLY — write cmd_NNN.py.tmp, then
rename it onto cmd_NNN.py. The worker used to sleep 0.3s and hope the write
had finished, which would compile and run a half-written file; it now
ignores .tmp files and additionally waits for the size to settle.
"""
import contextlib
import io
import os
import time
import traceback

import browser_session
import formula_columns  # noqa: F401  (exposed to commands)

QUEUE = os.path.join(browser_session.SCRIPT_DIR, "queue")
os.makedirs(QUEUE, exist_ok=True)
LOCK = os.path.join(QUEUE, "worker.lock")


def _claim_queue():
    """Take the single-worker lock, so two workers can't run the same command.

    O_EXCL create is atomic on Windows and POSIX. A stale lock from a killed
    worker must be removed by hand — that is deliberate: silently stealing it
    is how two workers end up driving one queue (the collision recorded in
    CLEANUP_NOTES.md)."""
    try:
        fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise SystemExit(
            f"another worker holds {LOCK}. If no worker is running, delete "
            f"that file and retry.")
    with os.fdopen(fd, "w") as fh:
        fh.write(str(os.getpid()))


def _read_when_settled(path, tries=20, pause=0.1):
    """Read `path` once its size has stopped changing, so a command that is
    still being written is never compiled half-complete."""
    last = -1
    for _ in range(tries):
        try:
            size = os.path.getsize(path)
        except OSError:
            return None
        if size == last:
            break
        last = size
        time.sleep(pause)
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def main():
    _claim_queue()
    try:
        _serve()
    finally:
        try:
            os.remove(LOCK)
        except OSError:
            pass


def _serve():
    with browser_session.clay_page() as page:
        for attempt in range(10):
            try:
                browser_session.open_interphex(page, table=browser_session.MAIN_TABLE)
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
            code = _read_when_settled(cmd)
            if code is None:
                time.sleep(1)
                continue
            buf = io.StringIO()
            try:
                with contextlib.redirect_stdout(buf):
                    exec(compile(code, cmd, "exec"),
                         {"page": page, "browser_session": browser_session,
                          "formula_columns": formula_columns})
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
