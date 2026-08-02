"""Shared state-file I/O for the per-event passes.

Every rollout keeps a resumable JSON state file recording which workbooks are
already done. Two failure modes used to lose that record silently:

- json.dump() straight onto the live file: a kill mid-write truncates it.
- a corrupt/truncated file swallowed by `except Exception: return {}`: every
  workbook reverts to "pending" and paid enrichments re-run across the fleet.

State files are the guard on credit spend, so failing LOUD is the safe
direction: load_json refuses to continue on a corrupt file instead of
pretending it was empty. save_json writes a temp file in the same directory
and os.replace()s it over the target (atomic on both Windows and POSIX), so
the state file on disk is always a complete JSON document.
"""

import json
import os
import tempfile


def load_json(path, default=None):
    """Read a JSON state/scope file. Missing file -> `default` ({} if None).
    Corrupt/unreadable file -> SystemExit with instructions, never {}."""
    if not os.path.exists(path):
        return {} if default is None else default
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as e:
        raise SystemExit(
            f"state file {path!r} is unreadable ({e}).\n"
            f"Refusing to treat it as empty - that would re-run already-done, "
            f"credit-spending work. Restore the file (a .tmp sibling may hold "
            f"the interrupted write), or delete it deliberately to start over.")


def save_json(path, data, **dump_kwargs):
    """Atomically write `data` as JSON to `path` (temp file + os.replace)."""
    dump_kwargs.setdefault("indent", 1)
    dump_kwargs.setdefault("ensure_ascii", False)
    directory = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(
        prefix=os.path.basename(path) + ".", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, **dump_kwargs)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
