"""Dynamic destination routing for the shared Labs - Block List - Companies
workbook.

Clay caps a merged/union table at 20 incoming "Send table data" sources AND
at roughly 50,000 rows, whichever is hit first: once either limit is reached,
new sources silently stop delivering rows (the source-side send still shows
as "created and run", but nothing lands). `ensure_destination` live-checks the
current table's row count and source count before every send and rolls over
to the next table (Table 2, Table 3, ...), creating it if needed, so callers
always get back a destination path with room for one more source.
"""
import re

import column_config as colcfg

TABLE1_URL = ("https://app.clay.com/workspaces/448891/workbooks/"
              "wb_0thngorwWnTjpruUykJ/tables/t_0thngozNZTVZ4cYPz3i/"
              "views/gv_0thngozjQPZ8XmH6KuZ")
BLOCKLIST_WORKBOOK_PATH = ["Home", "Labs [2026 - Qasim]", "Labs - Block List - Companies"]

MAX_SOURCES = 20
MAX_ROWS = 50000
ROW_SAFETY_MARGIN = 2000  # stop routing new sends within this many rows of the cap


def _rows_and_sources(page):
    """Read the currently-focused table's row count and merge-source count
    from the toolbar. A fresh table with no incoming sends has no "Rows
    from:" badge at all, which reads as 0 sources."""
    rows_text = page.get_by_text(re.compile(r"^[\d,]+/[\d,]+ rows$")).first.inner_text()
    rows = int(rows_text.split("/")[1].split(" ")[0].replace(",", ""))

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


def ensure_destination(page, log=None):
    """Return the destination path list (e.g. [..., "Table 2"]) of the first
    blocklist table (Table 1..MAX_TABLE_INDEX) with room for one more source
    and under the row cap. Raises RuntimeError if all existing tables are
    full — that means a human needs to add the next one."""
    def say(msg):
        if log:
            log.write(msg + "\n")

    # Table 1's direct URL is known-good and reliably hydrates; land there
    # first, then use ordinary tab clicks for any further tables — the same
    # pattern already proven inside every event build.
    page.goto(TABLE1_URL, wait_until="domcontentloaded")
    page.get_by_text(re.compile(r"^[\d,]+/[\d,]+ rows$")).first.wait_for(
        state="visible", timeout=30000)
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
            return BLOCKLIST_WORKBOOK_PATH + [name]

    raise RuntimeError(
        f"All blocklist tables (Table 1..{MAX_TABLE_INDEX}) are full — "
        f"create Table {MAX_TABLE_INDEX + 1} manually in Clay.")
