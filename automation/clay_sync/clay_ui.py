"""
Clay web-UI actions (Playwright), each isolated so a Clay UI change only
touches one function.

Selectors were captured with `playwright codegen` against a live account and
are the real ones Clay uses (stable test-ids / roles where possible). If Clay's
UI shifts, re-record and update only the affected function.

HARD SCOPE: everything happens inside
    Labs [2026 - Qasim]  ->  Competitive Events
and nothing outside it is ever touched. There is NO delete operation anywhere
in this module. Replacing an existing table's contents on a re-scrape is not
implemented yet (see clay_sync.py) — for now the tool only creates new
workbooks that don't already exist.
"""

import os
import re

from playwright.sync_api import Page, TimeoutError as PWTimeout

import humanize

TARGET_FOLDER = "Labs [2026 - Qasim]"
TARGET_SUBFOLDER = "Competitive Events"
CLAY_URL = "https://app.clay.com"


class ClayUIError(Exception):
    """Any failure interacting with the Clay UI."""


# --------------------------------------------------------------------------
# session / scope
# --------------------------------------------------------------------------

def is_logged_in(page: Page) -> bool:
    """Navigate to Clay and report whether we have an authenticated session."""
    try:
        page.goto(CLAY_URL, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_load_state("networkidle", timeout=15000)
    except PWTimeout:
        pass
    url = page.url.lower()
    if any(x in url for x in ("login", "signin", "sign-in")):
        return False
    return True


def _open_cell(page: Page, name: str) -> None:
    """Click into a folder/subfolder that shows as a table cell with a link.
    Raises on failure so the caller can retry."""
    cell = page.get_by_role("cell", name=name).get_by_role("link").first
    cell.wait_for(state="visible", timeout=30000)
    cell.scroll_into_view_if_needed(timeout=10000)
    cell.click(timeout=20000)
    page.wait_for_load_state("networkidle", timeout=20000)


def open_target_location(page: Page) -> None:
    """Navigate Home -> Labs [2026 - Qasim] -> Competitive Events and assert
    we're in a folder view. Raise (never fall back elsewhere) if it can't be
    reached. Idempotent — call before working on each event.

    Clay's home/folder views load unevenly under load, so retry the whole
    hop a few times (reloading each attempt) before giving up."""
    last_err = None
    for attempt in range(3):
        try:
            # The root URL renders the full app (All Files, with folder cells);
            # the deep /workspaces/<id>/home URL returns a blank shell. Use the
            # root and let _open_cell wait for each folder cell to hydrate.
            try:
                page.goto(CLAY_URL, wait_until="domcontentloaded", timeout=30000)
            except PWTimeout:
                pass
            _open_cell(page, TARGET_FOLDER)
            _open_cell(page, TARGET_SUBFOLDER)
            # Confirm we landed somewhere we can create workbooks.
            page.get_by_test_id("create-new").wait_for(timeout=15000)
            return
        except Exception as e:
            last_err = e
            humanize.dwell(1.5, 3.0)  # let Clay settle, then retry from the top
    raise ClayUIError(
        f"Could not navigate to {TARGET_FOLDER!r} / {TARGET_SUBFOLDER!r} after "
        f"3 attempts: {last_err}")


# --------------------------------------------------------------------------
# workbooks — scoped to the open Competitive Events subfolder
# --------------------------------------------------------------------------

# Every cell in a workbook row is wrapped in a link to that workbook's URL,
# so the workbook id in the href keys the row; the Name column is the first
# cell, so the first non-empty cell text per id is the workbook name.
_COLLECT_ROWS_JS = """() => {
    const out = {};
    for (const a of document.querySelectorAll('a[href*="/workbooks/"]')) {
        const m = a.href.match(/\\/workbooks\\/([^/?]+)/);
        if (!m) continue;
        const cell = a.closest('td, [role="cell"], [role="gridcell"]');
        const text = (cell ? cell.textContent : a.textContent).trim();
        if (!(m[1] in out) && text) out[m[1]] = text;
    }
    return out;
}"""


def list_workbooks(page: Page) -> dict:
    """{workbook_id: name} for every workbook row in the open subfolder.

    The listing VIRTUALIZES beyond ~60 rows (only the visible slice is in the
    DOM — this is what caused duplicate workbooks: names below the fold read
    as absent). Wheel-scroll over the listing and accumulate rows until no
    new ones appear for a few consecutive attempts.

    The subfolder holds ~90 workbooks, so an empty listing is never real: if
    no row appears the view didn't hydrate — raise (fail the folder) rather
    than report absence and let the caller create a duplicate."""
    try:
        page.get_by_role("cell").first.wait_for(state="visible", timeout=45000)
    except Exception as e:
        raise ClayUIError(
            f"Workbook listing never hydrated — cannot tell what exists, "
            f"refusing to risk creating a duplicate: {e}")
    humanize.dwell(1.0, 1.5)
    page.wait_for_timeout(3000)  # let the first screen of rows hydrate

    first_cell = page.get_by_role("cell").first
    box = first_cell.bounding_box()
    if box:
        page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)

    id_to_name = {}
    stable = 0
    while stable < 3:
        before = len(id_to_name)
        id_to_name.update(page.evaluate(_COLLECT_ROWS_JS))
        page.mouse.wheel(0, 3000)
        page.wait_for_timeout(1200)
        id_to_name.update(page.evaluate(_COLLECT_ROWS_JS))
        stable = stable + 1 if len(id_to_name) == before else 0
    return id_to_name


def workbook_exists(page: Page, name: str) -> bool:
    """Whether a workbook `name` already exists in the open subfolder.
    Exact-name match distinguishes e.g. "ACHEMA" from "ACHEMA Middle East".
    Scrolls the whole (virtualized) listing via list_workbooks — a plain DOM
    count only sees the visible slice and reports false absences.

    NOTE: scrolls the listing, so after calling this re-run
    open_target_location before clicking cells by position."""
    return name in set(list_workbooks(page).values())


def _scroll_to_cell(page: Page, name: str) -> None:
    """Wheel-scroll the (virtualized) listing until a cell named `name` is in
    the DOM. No-op if it's already there. Gives up after the listing stops
    yielding new rows."""
    page.get_by_role("cell").first.wait_for(state="visible", timeout=45000)
    box = page.get_by_role("cell").first.bounding_box()
    if box:
        page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    stable = 0
    while stable < 3:
        if page.get_by_role("cell", name=name, exact=True).count() > 0:
            return
        before = len(page.evaluate(_COLLECT_ROWS_JS))
        page.mouse.wheel(0, 3000)
        page.wait_for_timeout(1200)
        stable = stable + 1 if len(page.evaluate(_COLLECT_ROWS_JS)) == before else 0
    raise ClayUIError(f"Cell {name!r} not found after scrolling the listing")


def open_workbook(page: Page, name: str) -> None:
    """From the open subfolder listing, open workbook `name` and wait until
    its bottom bar (with the add-table button) has rendered. Scrolls the
    virtualized listing first — rows below the fold aren't in the DOM."""
    try:
        _scroll_to_cell(page, name)
        _open_cell(page, name)
        _add_table_button(page).wait_for(state="visible", timeout=30000)
        humanize.dwell(0.5, 1.0)  # let the remaining table tabs render
    except Exception as e:
        raise ClayUIError(f"Could not open workbook {name!r}: {e}")


def existing_tables(page: Page, candidates: list) -> set:
    """Which of `candidates` (table names) already exist in the open workbook.
    Each table tab renders as a button with the table's exact name; with only
    the four standard names in play, presence-checking each is unambiguous."""
    found = set()
    for name in candidates:
        try:
            if page.get_by_role("button", name=name, exact=True).count() > 0:
                found.add(name)
        except Exception:
            pass
    return found


def create_workbook_with_csvs(page: Page, name: str, csv_paths: list) -> None:
    """Create workbook `name` in the open subfolder and import each CSV in
    `csv_paths` as its own table. Clay names each table after its CSV filename
    (Exhibitors.csv -> "Exhibitors"), so no rename is needed.

    The first CSV goes through the empty-workbook "Import from CSV" entry point;
    every additional CSV is added via the bottom-bar "Add" button (Create new
    table -> Import from CSV), since the initial import only makes one table.

    Assumes open_target_location(page) has already put us in the subfolder.
    """
    try:
        page.get_by_test_id("create-new").click(timeout=15000)
        humanize.dwell()
        page.get_by_test_id("new-workbook").click(timeout=15000)
        humanize.dwell(0.5, 1.0)

        # Name the workbook (label is "title"), commit with Enter.
        humanize.type_into(page, page.get_by_label("title"), name)
        page.keyboard.press("Enter")
        humanize.dwell()

        # First table: the empty workbook's "Import from CSV" entry point.
        # No "Continue" step in this flow — straight to the commit footer.
        page.get_by_role("button", name="Import from CSV").click(timeout=20000)
        _import_csv(page, csv_paths[0], has_continue=False)
        _wait_for_table_data(page)

        # Remaining tables: bottom-bar "Add" -> Create new table -> Import CSV.
        for path in csv_paths[1:]:
            add_csv_table(page, path)
    except Exception as e:
        raise ClayUIError(f"Failed to create workbook {name!r} with CSVs: {e}")


def _import_csv(page: Page, csv_path: str, has_continue: bool) -> None:
    """From an open CSV import dialog: browse to the file, set it, and commit.
    The Add-table modal has an extra 'Continue' step before the commit footer;
    the first-import flow goes straight to the footer. Readiness is confirmed
    by the caller via _wait_for_table_data — we never wait on networkidle,
    because Clay keeps connections open and it never fires."""
    humanize.dwell()
    with page.expect_file_chooser(timeout=15000) as fc:
        page.get_by_text("Browse files").click()
    fc.value.set_files(os.path.abspath(csv_path))
    humanize.dwell(0.5, 1.0)

    if has_continue:
        page.get_by_role("button", name="Continue").click(timeout=15000)
        humanize.dwell()
        # A commit footer may or may not follow Continue — best-effort.
        try:
            _click_footer_commit(page, timeout=10000)
        except Exception:
            pass
    else:
        _click_footer_commit(page, timeout=30000)


def _click_footer_commit(page: Page, timeout: int) -> None:
    """Click the commit control in the import dialog's footer. The
    stage-display-footer test-id IS the actionable commit target — clicking it
    is the approach that created 60+ workbooks successfully, so keep it as-is."""
    page.get_by_test_id("stage-display-footer").click(timeout=timeout)


def _add_table_button(page: Page):
    """The bottom-bar add-table button. Its accessible name is "Add" and it is
    type="button"; the grid's separate "Add row" button (also named "Add") is
    type="submit", so filtering on type uniquely targets the add-table one."""
    return page.get_by_role("button", name="Add", exact=True).and_(
        page.locator('button[type="button"]'))


def add_csv_table(page: Page, csv_path: str) -> None:
    """Add another table to the already-open workbook by importing a CSV via
    the bottom-bar 'Add' button (Create new table -> Import from CSV)."""
    add_table = _add_table_button(page)
    add_table.wait_for(state="visible", timeout=20000)
    add_table.click(timeout=15000)
    humanize.dwell()
    # The "Create new table" modal is a large, partly virtualized source picker;
    # clicking the bare "Import from CSV" text is unreliable (off-screen / animated
    # duplicates). Filter via the modal search box to leave a single match, then click.
    search = page.get_by_placeholder("Search")
    search.wait_for(state="visible", timeout=10000)
    search.fill("Import from CSV")
    humanize.dwell(0.5, 1.0)
    page.get_by_role("button", name="Import from CSV", exact=True).first.click(timeout=10000)
    _import_csv(page, csv_path, has_continue=True)

    # The previous table's data cells are still on screen, so a bare data-cell
    # wait would pass instantly. Instead: wait for the new table's tab (named
    # after the CSV stem) to appear, focus it, then wait for its rows.
    table_name = os.path.splitext(os.path.basename(csv_path))[0]
    tab = page.get_by_role("button", name=table_name, exact=True).first
    try:
        tab.wait_for(state="visible", timeout=60000)
    except Exception as e:
        raise ClayUIError(
            f"Added-table import committed but no {table_name!r} tab appeared: {e}")
    tab.click(timeout=10000)
    humanize.dwell(0.6, 1.2)  # let the grid re-render for the new table
    _wait_for_table_data(page)


def _wait_for_table_data(page: Page, timeout: int = 300000) -> None:
    """Block until the imported table shows at least one data cell. Clay's grid
    cells carry a data-testid like 'cell-r0-c0'; the first one appearing means
    ingestion has produced rows. Raise if none appear within `timeout`."""
    try:
        page.get_by_test_id(re.compile(r"^cell-r\d+-c0$")).first.wait_for(
            state="visible", timeout=timeout)
    except Exception as e:
        raise ClayUIError(
            f"Import committed but no table data appeared within "
            f"{timeout // 1000}s: {e}")
    humanize.dwell(0.3, 0.7)  # let the rest of the rows settle
