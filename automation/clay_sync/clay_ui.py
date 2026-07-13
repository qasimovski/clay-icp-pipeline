"""
Clay web-UI actions (Playwright), each isolated so a Clay UI change only
touches one function.

Selectors were captured with `playwright codegen` against a live account and
are the real ones Clay uses (stable test-ids / roles where possible). If Clay's
UI shifts, re-record and update only the affected function.

HARD SCOPE: everything happens inside
    Labs [2026 - Qasim]  ->  Competitive Events
and nothing outside it is ever touched.

A `delete_table` operation was added for the Competitive Events cleanup pass
(see automation/cleanup/). It is manifest-driven and refuses to delete any name
in KEEP_NEVER_DELETE, so it can only remove pipeline-byproduct tables, never a
normalized source table. Replacing an existing table's contents on a re-scrape
is still not implemented (see clay_sync.py).
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


# --------------------------------------------------------------------------
# cleanup: navigate a workbook by id, enumerate its tables, delete a table
# --------------------------------------------------------------------------

# Names the cleanup must never delete under any circumstance. delete_table
# hard-refuses these regardless of what a caller passes, so a bug in the
# manifest or worker can never remove a normalized source table.
KEEP_NEVER_DELETE = {"Exhibitors_normalized", "Sponsors_normalized"}

# Workspace id used to build canonical workbook URLs. Override via env if the
# session belongs to a different workspace.
WORKSPACE_ID = os.environ.get("CLAY_WORKSPACE_ID", "448891")


def open_workbook_by_id(page, workbook_id: str) -> None:
    """Navigate straight to a workbook by its id and wait until its bottom bar
    (add-table button) has rendered. Keyed by id, so it is immune to the
    virtualized-listing scroll problems and to duplicate workbook names.

    Tries the workspace-scoped URL first, then the bare form, retrying the
    flaky hydration a few times before giving up."""
    urls = [
        f"{CLAY_URL}/workspaces/{WORKSPACE_ID}/workbooks/{workbook_id}",
        f"{CLAY_URL}/workbooks/{workbook_id}",
    ]
    last_err = None
    for attempt in range(4):
        url = urls[attempt % len(urls)]
        try:
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
            except PWTimeout:
                pass
            # The add-table bottom-bar button only exists inside an open
            # workbook, so its presence confirms we landed on the workbook view.
            _add_table_button(page).wait_for(state="visible", timeout=25000)
            humanize.dwell(0.6, 1.2)  # let the table tabs render
            return
        except Exception as e:
            last_err = e
            humanize.dwell(1.5, 3.0)
    raise ClayUIError(
        f"Could not open workbook id {workbook_id!r} after 4 attempts: {last_err}")


# The table tabs live in the workbook's bottom bar; each is a button whose
# accessible name is the table's exact name. Collect the bottom-band buttons
# and drop the known chrome ('Add', 'Add row', pagination, etc.).
_TABS_JS = """() => {
    const h = window.innerHeight;
    const chrome = new Set(['Add', 'Add row', '']);
    const out = [];
    for (const b of document.querySelectorAll('button')) {
        const r = b.getBoundingClientRect();
        if (r.width === 0 || r.height === 0) continue;
        // bottom bar only — tabs sit in the lowest ~140px of the viewport
        if (r.y < h - 140) continue;
        const t = (b.textContent || '').trim();
        if (!t || t.length > 80 || chrome.has(t)) continue;
        if (/^\\d+$/.test(t)) continue;           // page numbers
        if (!out.includes(t)) out.push(t);
    }
    return out;
}"""


def list_table_tabs(page) -> list:
    """Best-effort list of the table-tab names in the open workbook's bottom
    bar. Used for logging/verification; deletion itself is driven off the
    manifest name list, not this scan."""
    try:
        return page.evaluate(_TABS_JS)
    except Exception:
        return []


def _tab(page, name):
    return page.get_by_role("button", name=name, exact=True).first


# The bottom-bar table tabs are the ONLY buttons that carry aria-haspopup="menu"
# and sit in the lowest band of the viewport. Match on that (plus exact text) so
# we never hit a same-named element in the right-sidebar Tables list or the
# Overview canvas — that ambiguity deleted 7/8 fine but missed 'Buyers'.
_FIND_TAB_JS = """(name) => {
    const H = window.innerHeight;
    for (const b of document.querySelectorAll('button[aria-haspopup="menu"]')) {
        const r0 = b.getBoundingClientRect();
        if (r0.width === 0 || r0.y < H - 70) continue;   // bottom bar only
        if ((b.textContent || '').trim() !== name) continue;
        // The tab bar overflows horizontally on many-table workbooks, pushing a
        // tab's chevron off-screen so the click misses. Scroll it into view
        // (centered) and return the post-scroll rect so the click lands right.
        b.scrollIntoView({inline: 'center', block: 'nearest'});
        const r = b.getBoundingClientRect();
        return {x: r.x, y: r.y, w: r.width, h: r.height};
    }
    return null;
}"""


def _find_tab_rect(page, name):
    """Rect of the bottom-bar tab button for `name`, or None."""
    return page.evaluate(_FIND_TAB_JS, name)


def tab_present_stable(page, name: str, tries: int = 6) -> bool:
    """Whether the bottom-bar tab `name` is present, tolerant of the transient
    re-render that follows a sibling deletion. Clears any lingering toast/overlay
    with Escape and retries before concluding the tab is gone — the eager check
    caused false 'already absent' skips that left tables undeleted."""
    for _ in range(tries):
        page.keyboard.press("Escape")
        page.wait_for_timeout(600)
        if _find_tab_rect(page, name):
            return True
        page.wait_for_timeout(900)
    return False


# The table-tab menu always contains these; the grid cell/column menu never
# does. Presence of any confirms the chevron click opened the TAB menu rather
# than merely activating the table (which happens if the click misses the
# chevron) — critical under load, where the click often lands a few px off.
_TAB_MENU_SIGNATURE = ("Rename", "Duplicate table", "Move table",
                       "Edit table settings")


def _tab_menu_open(page) -> bool:
    items = visible_menuitems(page)
    return any(any(sig in it for sig in _TAB_MENU_SIGNATURE) for it in items)


def activate_overview(page) -> bool:
    """Click the workbook's 'Overview' tab so NO table tab is active. A tab that
    just became active (e.g. the neighbour promoted after a deletion) does not
    open its menu on a chevron click — an inactive tab does. Anchoring on
    Overview before each menu-open keeps the target tab inactive."""
    try:
        btn = page.get_by_role("button", name="Overview", exact=True).first
        if btn.count():
            btn.click(timeout=5000)
            page.wait_for_timeout(600)
            return True
    except Exception:
        pass
    return False


def open_tab_menu(page, name: str, tries: int = 6):
    """Open a table tab's dropdown menu by clicking the chevron at the right edge
    of the bottom-bar tab button, then VERIFY the menu actually opened. The tab
    is located via _FIND_TAB_JS (bottom band + aria-haspopup=menu + exact text)
    so same-named sidebar/canvas elements can't be mistaken for it. Under load
    the chevron click sometimes just activates the table (no menu) — retry with a
    freshly-measured rect until the tab menu signature appears."""
    for _ in range(tries):
        # Ensure the target tab is inactive (active tabs don't open on chevron).
        activate_overview(page)
        rect = _find_tab_rect(page, name)
        if not rect:
            page.wait_for_timeout(1000)
            continue
        # Chevron is the rightmost glyph of the tab; nudge in from the edge.
        page.mouse.click(rect["x"] + rect["w"] - 7, rect["y"] + rect["h"] / 2)
        page.wait_for_timeout(900)
        if _tab_menu_open(page):
            return
        page.keyboard.press("Escape")   # likely activated the table — retry
        page.wait_for_timeout(700)
    raise ClayUIError(f"could not open tab menu for {name!r} after {tries} tries")


def visible_menuitems(page) -> list:
    out = []
    for mi in page.get_by_role("menuitem").all():
        try:
            if mi.is_visible():
                out.append(mi.inner_text().strip())
        except Exception:
            pass
    return out


# --------------------------------------------------------------------------
# column deletion (Exhibitors_normalized trim: drop everything right of a marker)
# --------------------------------------------------------------------------

# Columns at/left of the cut in Exhibitors_normalized — delete_column hard-refuses
# these so a manifest/worker bug can never trim into the kept range. Cut is now
# 'Website' (the raw-import tail): the Normalize a Domain / Normalize Company Name
# / Normalized Country helper columns are INTENTIONALLY deletable in this pass.
KEEP_NEVER_DELETE_COLS = {
    "Created At", "Updated At", "Event", "Company Name", "Profile URL", "Booth",
    "Year", "Description", "Address Line 1", "City", "Postal Code", "Country",
    "Phone", "Email", "Website",
}

# Signature items unique to a column HEADER menu (not the grid cell menu): if any
# is visible the header menu is open. 'Rename column' is the stable anchor.
_COL_MENU_SIGNATURE = ("Rename column", "Edit column", "Insert 1 column left")

# Find the leaf header node with exact text `name` in the header band, scroll it
# horizontally into view (the grid's wheel-scroll is unreliable headless), and
# return its rect. None if not present.
_FIND_HEADER_JS = """(name) => {
    for (const el of document.querySelectorAll('*')) {
        if (el.children.length) continue;
        if ((el.textContent || '').trim() !== name) continue;
        const r0 = el.getBoundingClientRect();
        // Header row sits near the top; its exact y varies with banners and the
        // browser used (headless ~171, real Chrome ~133), so use a wide band.
        // Column-name text only appears in the header, so this stays unambiguous.
        if (r0.y > 95 && r0.y < 210 && r0.width > 5) {
            el.scrollIntoView({inline: 'center', block: 'nearest'});
            const r = el.getBoundingClientRect();
            return {x: Math.round(r.x), y: Math.round(r.y + r.height / 2),
                    w: Math.round(r.width)};
        }
    }
    return null;
}"""


def _find_header_rect(page, name):
    return page.evaluate(_FIND_HEADER_JS, name)


def _col_menu_open(page) -> bool:
    for mi in page.get_by_role("menuitem").all():
        try:
            if mi.is_visible():
                t = mi.inner_text().strip()
                if any(sig in t for sig in _COL_MENU_SIGNATURE):
                    return True
        except Exception:
            pass
    return False


def open_column_menu(page, name: str, tries: int = 6):
    """Scroll the column header into view and click it to open the header menu,
    verifying the menu actually opened (retry with re-measured rect)."""
    for _ in range(tries):
        rect = _find_header_rect(page, name)
        if not rect:
            page.wait_for_timeout(600)
            continue
        page.mouse.click(rect["x"] + rect["w"] - 8, rect["y"])
        page.wait_for_timeout(450)
        if _col_menu_open(page):
            return
        page.keyboard.press("Escape")
        page.wait_for_timeout(350)
    raise ClayUIError(f"could not open header menu for column {name!r}")


def delete_column(page, name: str, verify: bool = True) -> bool:
    """Delete column `name` from the focused table. Refuses KEEP_NEVER_DELETE_COLS.
    Returns True if deleted, False if the column was already absent. The header
    menu's 'Delete' item is unique (no column is named 'Delete')."""
    if name in KEEP_NEVER_DELETE_COLS:
        raise ClayUIError(f"refusing to delete protected column {name!r}")
    if not _find_header_rect(page, name):
        return False  # already gone (idempotent re-run)

    clicked = False
    for _ in range(3):
        try:
            open_column_menu(page, name)
        except ClayUIError:
            page.keyboard.press("Escape"); page.wait_for_timeout(1000)
            continue
        mi = page.get_by_role("menuitem", name="Delete", exact=True)
        try:
            if mi.count() and mi.first.is_visible():
                mi.first.click(timeout=8000)
                clicked = True
                break
        except Exception:
            pass
        page.keyboard.press("Escape"); page.wait_for_timeout(700)
    if not clicked:
        page.keyboard.press("Escape")
        raise ClayUIError(f"'Delete' menu item not found for column {name!r}")
    page.wait_for_timeout(250)

    # confirm dialog if one appears (scoped to the dialog)
    for drole in ("alertdialog", "dialog"):
        d = page.get_by_role(drole)
        try:
            if d.count() and d.first.is_visible():
                for label in ("Delete column", "Delete", "Confirm", "Remove",
                              "Yes, delete"):
                    b = d.first.get_by_role("button", name=label, exact=True)
                    if b.count() and b.last.is_visible():
                        b.last.click(timeout=8000)
                        break
                break
        except Exception:
            pass
    page.wait_for_timeout(250)

    if verify:
        for _ in range(20):
            if not _find_header_rect(page, name):
                return True
            page.wait_for_timeout(500)
        raise ClayUIError(f"clicked delete for column {name!r} but it is still present")
    return True


def delete_table(page, name: str, verify: bool = True) -> bool:
    """Delete the table tab `name` from the currently open workbook.

    Hard safety: refuses any name in KEEP_NEVER_DELETE. Returns True if a
    deletion was performed, False if the tab was already absent. Raises
    ClayUIError on any unexpected UI state (fail closed — never guess).

    Interaction (confirmed via recon against the live UI): click the tab's
    chevron -> menu ['Enable auto-delete...', 'Rename', 'Edit table settings',
    'Move table', 'Duplicate table', 'Delete'] -> click the EXACT 'Delete'
    item (never a substring match — 'Enable auto-delete...' also contains
    'delete') -> confirm dialog if one appears.
    """
    if name in KEEP_NEVER_DELETE:
        raise ClayUIError(f"refusing to delete protected table {name!r}")

    # Settle any post-delete re-render, then confirm the tab is really present.
    # 3 tries suffices now that activate_overview stabilises state before each
    # menu-open; keeps the already-absent path (idempotent re-runs) cheap.
    if not tab_present_stable(page, name, tries=3):
        return False  # already gone (idempotent re-run)

    # Open the tab menu and click the exact 'Delete' item. The chevron click
    # occasionally lands mid-re-render and no menu opens (empty menuitems), so
    # retry the whole open-and-click a few times before failing.
    clicked = False
    for attempt in range(3):
        try:
            open_tab_menu(page, name)
        except ClayUIError:
            page.keyboard.press("Escape")
            page.wait_for_timeout(1000)
            continue
        for label in ("Delete", "Delete table"):
            mi = page.get_by_role("menuitem", name=label, exact=True)
            try:
                if mi.count() and mi.first.is_visible():
                    mi.first.click(timeout=10000)
                    clicked = True
                    break
            except Exception:
                pass
        if clicked:
            break
        page.keyboard.press("Escape")   # menu didn't open / no Delete item — retry
        page.wait_for_timeout(1200)
    if not clicked:
        items = visible_menuitems(page)
        page.keyboard.press("Escape")
        raise ClayUIError(
            f"'Delete' menu item not found for table {name!r}; "
            f"visible menu items = {items!r}")
    humanize.dwell(0.6, 1.1)

    # Confirm dialog if one appears. Scope the button lookup to the dialog so we
    # never click an unrelated page 'Delete'. If no dialog, the delete was
    # immediate — verification below is the source of truth either way.
    for drole in ("alertdialog", "dialog"):
        d = page.get_by_role(drole)
        try:
            if d.count() and d.first.is_visible():
                for label in ("Delete table", "Delete", "Confirm",
                              "Yes, delete", "Remove"):
                    b = d.first.get_by_role("button", name=label, exact=True)
                    if b.count() and b.last.is_visible():
                        b.last.click(timeout=8000)
                        break
                break
        except Exception:
            pass
    humanize.dwell(0.8, 1.5)

    if verify:
        # Poll until the bottom-bar tab is gone (data-heavy tables take a beat).
        for _ in range(20):
            if not _find_tab_rect(page, name):
                return True
            page.wait_for_timeout(1000)
        raise ClayUIError(
            f"clicked delete for {name!r} but the tab is still present")
    return True
