"""Is a just-applied column actually finished?

The table-wide "N% of table completed" banner is NOT a given column's
progress. add_workemail_waterfall.py records what that cost: a stale
table-wide 100% "previously made a 4%-run column look finished", and
apply_gsheet_lookup.py records the same false positive on Analytica India.
Wherever that reading alone decided "already applied and complete", a
workbook could be marked done forever with an unfinished column.

The fix keeps the table-wide reading as a cheap first signal but requires
the column's OWN status cell to agree before declaring completion. That can
only convert a wrong "skip as done" into "resume and wait" — the resume path
just polls for completion, it never re-applies or re-charges — so the change
is safe in the direction that matters.
"""

import re

# Column status-cell text that means the column has NOT finished running.
# column_status returns '' for the healthy checkmark, '0%' for a dormant
# (never-run) column, and a percentage while a run is in flight.
DORMANT = "0%"


def table_pct(page):
    """The table-wide 'N% of table completed' reading, or None if absent.

    Table-scoped, not column-scoped: never use this alone to decide a column
    is done (that is the bug this module exists for)."""
    text = page.evaluate("()=>document.body.innerText")
    m = re.search(r"(\d+)% of table completed", text)
    return int(m.group(1)) if m else None


def column_finished(page, column, colcfg):
    """True/False/None for 'has `column` finished running?', from the
    column's own status cell. None means the status could not be read."""
    try:
        status = colcfg.column_status(page, column)
    except Exception:
        return None
    if status is None:
        return None
    status = status.strip()
    if status == DORMANT:
        return False
    m = re.match(r"^(\d+)%$", status)
    if m:
        return int(m.group(1)) == 100
    # Empty string = the healthy checkmark icon (finished, no error badge).
    return status == ""


def already_complete(page, column, colcfg):
    """Whether `column` may be treated as already applied AND finished.

    Requires the table-wide banner at 100% *and* the column's own status to
    agree. Returns (complete: bool, detail: str) — detail is for the log so
    an operator can see which signal withheld agreement."""
    pct = table_pct(page)
    if pct != 100:
        return False, f"table {pct}%"
    finished = column_finished(page, column, colcfg)
    if finished is True:
        return True, "table 100% + column finished"
    if finished is None:
        return False, "table 100% but column status unreadable"
    return False, "table 100% but column still running"
