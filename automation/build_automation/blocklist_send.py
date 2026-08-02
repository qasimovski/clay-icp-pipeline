"""SUPERSEDED — the blocklist is the Supabase ledger now, not a Clay table.

Dedupe moved to `blocklist_ledger/`: one HTTP API column per workbook does
lookup-and-insert against Supabase and returns `Is New`, and the view is
filtered on it so paid columns never run on already-worked companies. That
gates spend inline; this module's Clay table only ever recorded it, so every
event still paid to enrich repeats.

Kept because `build_workbook.py` (the Interphex-era reference build) still
calls it. New builds should follow step 5 of `template/BUILD_PROMPT.template.md`.
See `docs/PIPELINE_ARCHITECTURE.md` for the current design.

Dynamic destination routing for the shared Labs - Block List - Companies
workbook.

Clay caps a merged/union table at 20 incoming "Send table data" sources AND
at roughly 50,000 rows, whichever is hit first: once either limit is reached,
new sources silently stop delivering rows (the source-side send still shows
as "created and run", but nothing lands). `ensure_destination` live-checks the
current table's row count and source count before every send and rolls over
to the next table (Table 2, Table 3, ...), creating it if needed, so callers
always get back a destination path with room for one more source.
"""
import os
import re

import browser_session
import clay_ui
import column_config as colcfg

# Live workspace/workbook/table/view ids identify a real Clay account, so per
# docs/SENSITIVE_DATA.md they belong in gitignored config/local.yaml or the
# environment — never hard-coded here. (They were hard-coded here, which is
# exactly what that policy forbids; see docs/AUDIT.md.)
_BLOCKLIST_KEYS = ("blocklist_workbook_id", "blocklist_table_id",
                   "blocklist_view_id")


def _blocklist_url():
    wid = browser_session.local_setting(
        "blocklist_workbook_id", "CLAY_BLOCKLIST_WORKBOOK_ID")
    tid = browser_session.local_setting(
        "blocklist_table_id", "CLAY_BLOCKLIST_TABLE_ID")
    vid = browser_session.local_setting(
        "blocklist_view_id", "CLAY_BLOCKLIST_VIEW_ID")
    return (f"{clay_ui.CLAY_URL}/workspaces/{clay_ui.WORKSPACE_ID}"
            f"/workbooks/{wid}/tables/{tid}/views/{vid}")


BLOCKLIST_WORKBOOK_PATH = [
    "Home",
    os.environ.get("CLAY_TARGET_FOLDER", clay_ui.TARGET_FOLDER),
    os.environ.get("CLAY_BLOCKLIST_WORKBOOK", "Labs - Block List - Companies"),
]

MAX_SOURCES = 20
MAX_ROWS = 50000
ROW_SAFETY_MARGIN = 2000  # stop routing new sends within this many rows of the cap


def _rows_and_sources(page):
    """Read the currently-focused table's row count and merge-source count
    from the toolbar. A fresh table with no incoming sends has no "Rows
    from:" badge at all, which reads as 0 sources."""
    badge = page.get_by_text(re.compile(r"^[\d,]+/[\d,]+ rows$")).first
    badge.wait_for(state="visible", timeout=30000)
    rows_text = badge.inner_text()
    # This count decides whether a table has room for another send, so a
    # surprise in the badge format must fail loudly rather than via a bare
    # ValueError from int().
    m = re.search(r"/\s*([\d,]+)\s*rows", rows_text)
    if not m:
        raise colcfg.VerificationError(
            f"could not parse the row badge {rows_text!r}")
    rows = int(m.group(1).replace(",", ""))

    sources = 0
    try:
        btn = page.locator("button", has_text="Rows from").first
        if btn.count():
            btn.click(timeout=5000)
            page.wait_for_timeout(600)
            edit_source = page.get_by_text("Edit source", exact=True).first
            if edit_source.count():
                edit_source.click(timeout=5000)
                page.wait_for_timeout(1000)
                sources = page.get_by_text(re.compile(r"^Rows from: .+_normalized$")).count()
            page.keyboard.press("Escape")
            page.wait_for_timeout(300)
    except Exception:
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass
    return rows, sources


MAX_TABLE_INDEX = 10  # Table 1..10 already exist (created manually); if all
                       # fill up, stop and ask a human to add Table 11+ rather
                       # than risk more fragile create/rename automation.


# Per-process memo of the table the last scan settled on, plus how many sends
# we have routed to it since. The scan is expensive (a nav plus, per table, a
# popover open/close) and re-ran for EVERY event in a 77-workbook fleet, even
# though its answer only changes once a table gains MAX_SOURCES sources. We
# re-scan when the memoized table could plausibly be full, so the cap is still
# enforced by a live reading and never by arithmetic alone.
_destination_memo = None       # (path_list, sources_at_scan)
_sends_since_scan = 0


def note_send_routed():
    """Record that one send was configured to the memoized destination.

    build_workbook calls this after a successful send so the memo knows when
    the cached table may have filled up."""
    global _sends_since_scan
    _sends_since_scan += 1


def reset_destination_memo():
    """Forget the cached destination (new process/fleet run, or after a
    manual change in Clay)."""
    global _destination_memo, _sends_since_scan
    _destination_memo = None
    _sends_since_scan = 0


def ensure_destination(page, log=None):
    """Return the destination path list (e.g. [..., "Table 2"]) of the first
    blocklist table (Table 1..MAX_TABLE_INDEX) with room for one more source
    and under the row cap. Raises RuntimeError if all existing tables are
    full — that means a human needs to add the next one."""
    global _destination_memo, _sends_since_scan

    def say(msg):
        if log:
            log.write(msg + "\n")

    if _destination_memo is not None:
        path, sources_at_scan = _destination_memo
        # Only trust the memo while the table demonstrably still has room even
        # if every send since the scan added a source.
        if sources_at_scan + _sends_since_scan < MAX_SOURCES:
            say(f"[blocklist_send] reusing {path[-1]} "
                f"(scanned at {sources_at_scan} sources, "
                f"+{_sends_since_scan} since)")
            return path
        say(f"[blocklist_send] re-scanning: {path[-1]} may be full "
            f"({sources_at_scan}+{_sends_since_scan} >= {MAX_SOURCES})")
        reset_destination_memo()

    # Table 1's direct URL is known-good and reliably hydrates; land there
    # first, then use ordinary tab clicks for any further tables — the same
    # pattern already proven inside every event build.
    # Retried like every other navigation in the codebase (clay_ui retries 6x):
    # a single flake here used to fail the whole blocklist step for the event.
    table1_url = _blocklist_url()
    last = None
    for attempt in range(4):
        try:
            page.goto(table1_url, wait_until="domcontentloaded", timeout=45000)
            page.get_by_text(re.compile(r"^[\d,]+/[\d,]+ rows$")).first.wait_for(
                state="visible", timeout=30000)
            break
        except Exception as e:
            last = e
            say(f"[blocklist_send] nav retry {attempt}: {str(e)[:100]}")
            page.wait_for_timeout(2000)
    else:
        raise colcfg.VerificationError(
            f"could not open the blocklist table after 4 attempts: {last}")
    page.wait_for_timeout(1000)

    for idx in range(1, MAX_TABLE_INDEX + 1):
        name = f"Table {idx}"
        if idx > 1:
            tab = page.get_by_role("button", name=name, exact=True)
            if tab.count() == 0:
                raise RuntimeError(
                    f"{name} does not exist yet — create it manually in Clay "
                    f"(all tables up to Table {idx - 1} are full).")
            colcfg.focus_table_maybe_empty(page, name)
            page.wait_for_timeout(1500)
        rows, sources = _rows_and_sources(page)
        say(f"[blocklist_send] {name}: rows={rows} sources={sources}")
        if sources < MAX_SOURCES and rows < (MAX_ROWS - ROW_SAFETY_MARGIN):
            path = BLOCKLIST_WORKBOOK_PATH + [name]
            _destination_memo = (path, sources)
            _sends_since_scan = 0
            return path

    raise RuntimeError(
        f"All blocklist tables (Table 1..{MAX_TABLE_INDEX}) are full — "
        f"create Table {MAX_TABLE_INDEX + 1} manually in Clay.")
