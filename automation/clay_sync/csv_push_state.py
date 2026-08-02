"""
Sync state for clay_sync.py.

Tracks which scraper CSVs have already been pushed to Clay, keyed by the CSV's
path relative to the scrapers root (e.g. "ACHEMA/Exhibitors.csv"). Lets the
sync decide, per file, whether it's new, changed since last sync, or unchanged.

State file: .clay_sync_state.json at the scrapers root. Each entry:
  {
    "sha256": "<hex>", "mtime": <float>, "workbook": "ACHEMA",
    "table": "Exhibitors", "last_synced": "2026-07-04T12:34:56"
  }
"""

import hashlib
import json
import os
import tempfile

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
# CSV keys stay relative to the scrapers root (the parent of this scripts
# folder) so they read "ACHEMA/Exhibitors.csv", not "_clay_sync/...".
SCRAPERS_ROOT = os.path.dirname(SCRIPT_DIR)
STATE_PATH = os.path.join(SCRIPT_DIR, ".clay_sync_state.json")


def load_state():
    if not os.path.exists(STATE_PATH):
        return {}
    with open(STATE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_state(state):
    # Temp file + os.replace: a kill mid-write can never truncate the state
    # file (which would make every CSV look never-pushed on the next run).
    fd, tmp = tempfile.mkstemp(prefix=".clay_sync_state.", suffix=".tmp",
                               dir=SCRIPT_DIR)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
        os.replace(tmp, STATE_PATH)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def rel_key(csv_path):
    """Relative-path key used to index a CSV in the state file."""
    return os.path.relpath(csv_path, SCRAPERS_ROOT).replace(os.sep, "/")


def file_signature(csv_path):
    """(sha256, mtime) for a CSV. mtime is the cheap first-pass check;
    sha256 is authoritative for whether contents actually changed."""
    mtime = os.path.getmtime(csv_path)
    h = hashlib.sha256()
    with open(csv_path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest(), mtime


def needs_sync(csv_path, state):
    """Return (needs_sync, sha256, mtime).
    New file -> True. Same mtime as recorded -> False without hashing.
    mtime differs -> hash and compare to catch real content changes."""
    key = rel_key(csv_path)
    entry = state.get(key)
    mtime = os.path.getmtime(csv_path)
    if entry is None:
        sha, _ = file_signature(csv_path)
        return True, sha, mtime
    if entry.get("mtime") == mtime:
        return False, entry.get("sha256"), mtime
    sha, _ = file_signature(csv_path)
    return sha != entry.get("sha256"), sha, mtime
