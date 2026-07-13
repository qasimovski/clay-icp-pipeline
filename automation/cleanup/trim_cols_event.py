"""Trim one workbook's Exhibitors_normalized table: delete every column to the
right of 'Normalized Country' (per the manifest delete-list), keeping Normalized
Country and everything left of it.

Manifest-driven and fail-closed: the keep range is guarded by
clay_ui.KEEP_NEVER_DELETE_COLS, and the workbook is only trimmed when Normalized
Country is actually present. Columns are deleted RIGHT-TO-LEFT so a still-present
column never loses a dependency mid-run (avoids cascade dialogs).
"""

import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
AUTO_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, os.path.join(AUTO_DIR, "clay_sync"))
sys.path.insert(0, os.path.join(AUTO_DIR, "build_automation"))

import clay_ui        # noqa: E402
import common         # noqa: E402
import build_lib as B  # noqa: E402

TABLE = "Exhibitors_normalized"
CUT = "Website"


def clean_workbook_cols(page, entry, dry_run, say):
    wid = entry["workbook_id"]
    name = entry["workbook_name"]
    delete = list(entry["delete"])            # display order (left->right)

    clay_ui.open_workbook_by_id(page, wid)
    B.focus_table(page, TABLE)

    # Safety gate: the cut column must be present (else wrong/edited table).
    present_cut = False
    for _ in range(6):
        if clay_ui._find_header_rect(page, CUT):
            present_cut = True
            break
        page.wait_for_timeout(1000)
    if not present_cut:
        say(f"ABORT {name} [{wid}]: {CUT!r} not found in {TABLE}")
        return {"workbook_id": wid, "workbook_name": name, "status": "aborted",
                "reason": "no_cut_column"}

    if dry_run:
        present = [c for c in delete if clay_ui._find_header_rect(page, c)]
        absent = [c for c in delete if c not in present]
        say(f"DRYRUN {name} [{wid}]: keep through {CUT!r} | would delete "
            f"({len(present)}) {present}"
            + (f" | already absent {absent}" if absent else ""))
        return {"workbook_id": wid, "workbook_name": name, "status": "dryrun",
                "to_delete": present, "already_gone": absent}

    say(f"TRIM {name} [{wid}]: deleting {len(delete)} cols right of {CUT!r}")
    deleted, already_gone, failed = [], [], []
    for col in reversed(delete):              # right-to-left
        try:
            if clay_ui.delete_column(page, col):
                deleted.append(col); say(f"  - deleted {col!r}")
            else:
                already_gone.append(col); say(f"  - {col!r} already absent")
        except Exception as e:
            failed.append({"column": col, "error": str(e)[:200]})
            say(f"  ! FAILED to delete {col!r}: {str(e)[:160]}")
            try:
                page.keyboard.press("Escape")
            except Exception:
                pass

    remaining = [c for c in delete if clay_ui._find_header_rect(page, c)]
    cut_ok = bool(clay_ui._find_header_rect(page, CUT))
    status = "ok" if (not failed and not remaining and cut_ok) else "partial"
    if not cut_ok:
        say(f"  !! WARNING {CUT!r} no longer present after trim")
    say(f"DONE {name}: status={status} deleted={len(deleted)} "
        f"remaining={remaining} failed={len(failed)}")
    return {"workbook_id": wid, "workbook_name": name, "status": status,
            "deleted": deleted, "failed": failed, "remaining": remaining,
            "cut_ok": cut_ok}
