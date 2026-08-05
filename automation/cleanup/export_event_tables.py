"""Download the three per-event Clay tables of one Competitive Events workbook
to CSV, into `<out-root>/<Event>/<Table name>.csv`.

Per the user (2026-08-04): for each event in the Competitive Events folder,
download "Exhibitors_normalized", "Sellers - People" and "Buyers - People" to
`C:\\Users\\qasim\\terrapinn\\Labs - Tables\\<Event>\\`, each file named after its
Clay table. Nothing else in the workbook is touched.

=== The UI path (recon'd headless against ACHEMA, 2026-08-04) ===

    Ctrl+E                -> right-hand "Tools" panel
    click the "Export" tab -> role="tab", NOT role="button" (get_by_role
                              ("button", name="Export") finds nothing)
    click "Download CSV"   -> a plain <div>, no role; fires the download
                              IMMEDIATELY, with no confirm dialog or column
                              picker in between

Clay names the file `<Table>-Default-view-export-<epoch_ms>.csv`; we rename to
the bare table name on save. "Default view" is what Clay exports here, i.e. the
same rows/columns the table tab opens on — this pass never changes the view.

=== Read-only by construction ===

The only clicks are: a table tab, the Export tab, and Download CSV. No control
whose accessible name matches /run/i is ever clicked, no column or view is
edited, and nothing costs credits — so unlike the build passes there is no
configure-only/run-now split to worry about.

Invocation:

    python export_event_tables.py --list                     # tabs only, no download
    python export_event_tables.py --event "ACHEMA"           # the real thing
    python export_event_tables.py --event "ACHEMA" --headed  # watch it
    python export_event_tables.py --event "ACHEMA" --out-root "D:\\somewhere"

`--event` takes the workbook name as it appears in competitive_events_workbooks.json
(the generated id->name map); `--workbook-id` takes the id directly.
"""
import argparse
import json
import os
import re
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.normpath(os.path.join(SCRIPT_DIR, "..", ".."))
sys.path.insert(0, os.path.join(REPO_ROOT, "automation", "build_automation"))
sys.path.insert(0, os.path.join(REPO_ROOT, "automation", "clay_sync"))

import browser_session  # noqa: E402
import clay_ui  # noqa: E402

WORKBOOKS_JSON = os.path.join(SCRIPT_DIR, "competitive_events_workbooks.json")

# Where the CSVs land: <out-root>/<Workbook>/<Table name>.csv. A local
# filesystem path, so it is account-specific: read it from $CLAY_EXPORT_ROOT or
# `export_root:` in config/local.yaml rather than fixing one machine's path in
# tracked source. The default keeps the original destination working unchanged.
DEFAULT_OUT_ROOT = browser_session.local_setting(
    "export_root", "CLAY_EXPORT_ROOT",
    default=r"C:\Users\qasim\terrapinn\Labs - Tables")

# The three tables to export, in tab order. A workbook missing one (the empty
# people-build events) is reported and skipped, not treated as a failure.
TABLES = ["Exhibitors_normalized", "Sellers - People", "Buyers - People"]

# Locates a leaf element by exact text. The Export tab and the Download CSV row
# are neither buttons nor menuitems in Clay's Tools panel, so role-based
# locators miss them; match on text and click the measured rect instead.
_FIND_JS = """(txt) => {
    const out = [];
    for (const el of document.querySelectorAll('*')) {
        if ((el.textContent || '').trim() !== txt) continue;
        if (el.children.length > 2) continue;          // leaf-ish only
        const r = el.getBoundingClientRect();
        if (r.width === 0 || r.height === 0) continue;
        out.push({tag: el.tagName, role: el.getAttribute('role'),
                  x: r.x, y: r.y, w: r.width, h: r.height});
    }
    return out;
}"""


class ExportError(Exception):
    pass


# Which id->name manifest the passes resolve against. Defaults to Competitive
# Events; the "Other Sources" folder is a different manifest whose workbooks
# name their company table after the workbook itself, so both the scope file and
# the table names have to be swappable.
_workbooks_json = WORKBOOKS_JSON


def set_workbooks_json(path):
    global _workbooks_json
    if path:
        _workbooks_json = path


def add_scope_args(ap):
    """The two flags every pass needs to point at a different Clay folder."""
    ap.add_argument("--workbooks", help="id->name manifest JSON "
                    "(default: competitive_events_workbooks.json)")
    return ap


def expand_tables(tables, event):
    """Substitute {event} in table names.

    In Other Sources the company table is called "<Workbook>_normalized" rather
    than a fixed "Exhibitors_normalized", so a pass is told
    `--tables "{event}_normalized" "Sellers - People" "Buyers - People"` and
    resolves the first per workbook."""
    return [t.replace("{event}", event) for t in tables]


def load_workbooks():
    with open(_workbooks_json, encoding="utf-8") as fh:
        return json.load(fh)


def resolve_workbook(args):
    if args.workbook_id:
        wbs = load_workbooks()
        return args.workbook_id, wbs.get(args.workbook_id, args.workbook_id)
    wbs = load_workbooks()
    matches = [(i, n) for i, n in wbs.items() if n == args.event]
    if not matches:
        matches = [(i, n) for i, n in wbs.items()
                   if args.event.lower() in n.lower()]
    if not matches:
        raise ExportError(f"no workbook matching {args.event!r} in {_workbooks_json}")
    if len(matches) > 1:
        raise ExportError(
            f"{args.event!r} matches {len(matches)}: {[n for _, n in matches]}")
    return matches[0]


def wait_for_tabs(page, timeout_s=240, stable_polls=3):
    """The workbook shell renders long before the bottom tab bar does; poll for
    the tabs rather than trusting the add-table button, which also exists on
    the workspace home page and made an unrendered page look 'open'.

    Wait for the list to STOP CHANGING, not merely to be non-empty. The tab bar
    fills in progressively, so returning on the first non-empty read can report
    a real table as missing — and this pass turns "missing" into a permanent
    `absent` record, which would silently drop an event's tables for good."""
    last, same = None, 0
    for _ in range(timeout_s // 2):
        tabs = clay_ui.list_table_tabs(page)
        if tabs and tabs == last:
            same += 1
            if same >= stable_polls:
                return tabs
        else:
            same = 0
        last = tabs
        page.wait_for_timeout(2000)
    if last:
        return last
    raise ExportError("workbook tabs never rendered")


def find_one(page, text, what):
    hits = page.evaluate(_FIND_JS, text)
    if not hits:
        raise ExportError(f"could not find {what} ({text!r})")
    return hits[0]


def click_rect(page, rect):
    page.mouse.click(rect["x"] + rect["w"] / 2, rect["y"] + rect["h"] / 2)


# The header breadcrumb ("Competitive Events / <Event> / <Table>") is the one
# place the FOCUSED table is named. Anchored to the top 90px so it can't match a
# bottom-bar tab or a right-panel entry carrying the same string.
_BREADCRUMB_JS = """(name) => {
    for (const el of document.querySelectorAll('button, a, span, p, div')) {
        const r = el.getBoundingClientRect();
        if (r.height === 0 || r.y > 90) continue;
        if ((el.textContent || '').trim() === name) return true;
    }
    return false;
}"""


# A completed download raises a modal — "Your export was downloaded! ..." —
# behind a FULL-VIEWPORT (1720x980) role="presentation" backdrop. The backdrop
# swallows every click, including the next table tab, so table 2 of each
# workbook failed to focus while table 3 succeeded (its 45s focus wait happened
# to outlast the modal). Dismiss it explicitly rather than waiting it out.
_MODAL_JS = """() => {
    for (const el of document.querySelectorAll('[role="dialog"]')) {
        const r = el.getBoundingClientRect();
        if (r.width > 50 && r.height > 20) return true;
    }
    return false;
}"""


def dismiss_export_modal(page, tries=12):
    """Close the post-download modal and wait for its backdrop to go away."""
    for i in range(tries):
        if not page.evaluate(_MODAL_JS):
            return True
        try:
            ok = page.get_by_role("button", name="OK", exact=True)
            if ok.count() and ok.first.is_visible():
                ok.first.click(timeout=3000)
            else:
                page.keyboard.press("Escape")
        except Exception:
            page.keyboard.press("Escape")
        page.wait_for_timeout(1000)
    return not page.evaluate(_MODAL_JS)


def focus_table(page, name, timeout_s=45):
    """Click a bottom-bar table tab and WAIT until the header breadcrumb names
    it. The fixed sleep this replaces was the bug that exported the wrong table:
    on ACHEMA Middle East the click on 'Sellers - People' had not taken effect
    within 4s, so the export ran against the still-focused Exhibitors_normalized
    and wrote its rows into 'Sellers - People.csv' — same byte size, no error.
    Never assume a tab click landed; confirm it."""
    dismiss_export_modal(page)               # else the backdrop eats this click
    rect = clay_ui._find_tab_rect(page, name)
    if rect is None:
        return False
    page.mouse.click(rect["x"] + rect["w"] / 2, rect["y"] + rect["h"] / 2)
    for _ in range(timeout_s * 2):
        page.wait_for_timeout(500)
        if page.evaluate(_BREADCRUMB_JS, name):
            page.wait_for_timeout(1500)      # let the grid settle
            return True
    return False


def clay_slug(name):
    """Clay's own slug for a table in the exported filename: non-alphanumerics
    collapse to '-', but '_' survives ("Sellers - People" -> "Sellers-People",
    "Exhibitors_normalized" -> "Exhibitors_normalized").

    '&' is SPELLED OUT, not dropped: "MATDAT Equipment & Materials_normalized"
    exports as "MATDAT-Equipment-and-Materials_normalized-...". Collapsing it to
    a dash made the identity guard reject two correct exports as wrong-table —
    the guard has to model Clay's slug exactly or it fails closed on good data.
    """
    return re.sub(r"[^A-Za-z0-9_]+", "-", name.replace("&", "and")).strip("-")


# The Tools panel's Export tab, matched by ROLE not by text alone. A plain
# text search for "Export" also matches ordinary grid content — LABMAS's
# "Sellers - People" has a cell reading "Export" at x=694,y=599 — which made the
# panel-open check report "already open" and then click a data cell instead of
# the tab. role="tab" is unique to the real control.
_EXPORT_TAB_JS = """() => {
    for (const el of document.querySelectorAll('[role="tab"]')) {
        if ((el.textContent || '').trim() !== 'Export') continue;
        const r = el.getBoundingClientRect();
        if (r.width === 0 || r.height === 0) continue;
        return {x: r.x, y: r.y, w: r.width, h: r.height};
    }
    return null;
}"""


def find_export_tab(page):
    return page.evaluate(_EXPORT_TAB_JS)


def open_tools_panel(page, tries=4):
    """Ctrl+E TOGGLES the Tools panel, it does not just open it. Switching table
    tabs leaves the panel as it was, so a blind Ctrl+E on the second table
    CLOSES it — that is what made 'Sellers - People' fail with "could not find
    the Export tab" while tables 1 and 3 succeeded (open/close/open).

    So: press, then check for the Export tab, and press again if the panel went
    the wrong way. The presence of the tab is the state check; the panel's own
    'Tools' heading is not, because the word appears elsewhere in the shell."""
    for _ in range(tries):
        if find_export_tab(page):
            return
        page.keyboard.press("Control+e")
        page.wait_for_timeout(2500)
    if not find_export_tab(page):
        raise ExportError("Tools panel would not open (no Export tab after "
                          f"{tries} Ctrl+E presses)")


def export_table(page, table, out_dir, dl_timeout_ms):
    """Ctrl+E -> Export tab -> Download CSV, saved as <out_dir>/<table>.csv."""
    page.keyboard.press("Escape")            # clear any lingering popover
    page.wait_for_timeout(400)
    open_tools_panel(page)

    tab = find_export_tab(page)
    if tab is None:
        raise ExportError("the Export tab vanished after opening the panel")
    click_rect(page, tab)
    page.wait_for_timeout(2500)

    dl_rect = find_one(page, "Download CSV", "the Download CSV row")
    with page.expect_download(timeout=dl_timeout_ms) as di:
        click_rect(page, dl_rect)
    download = di.value

    # Clay names the file after the table it actually exported, so the filename
    # is an independent witness that we exported the table we meant to. Check it
    # BEFORE saving: a wrong-table CSV written under the right name is silent
    # data corruption, and byte size alone will not reveal it.
    expected = clay_slug(table)
    if not download.suggested_filename.startswith(expected + "-"):
        download.cancel()
        raise ExportError(
            f"{table}: Clay exported {download.suggested_filename!r}, which is "
            f"not this table (expected prefix {expected + '-'!r}) — the tab "
            f"focus did not take. Nothing written.")

    dest = os.path.join(out_dir, f"{table}.csv")
    download.save_as(dest)
    dismiss_export_modal(page)               # clear the "downloaded!" modal now
    size = os.path.getsize(dest)
    print(f"      -> {dest}  ({size:,} bytes, clay name "
          f"{download.suggested_filename!r})")
    if size == 0:
        raise ExportError(f"{table}: downloaded file is empty")
    return dest


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--event", help="workbook name (exact, or unique substring)")
    g.add_argument("--workbook-id", help="workbook id, e.g. wb_0th...")
    ap.add_argument("--out-root", default=DEFAULT_OUT_ROOT)
    ap.add_argument("--tables", nargs="*", default=TABLES,
                    help="override which table tabs to export")
    add_scope_args(ap)
    ap.add_argument("--list", action="store_true",
                    help="open the workbook, print its tabs, download nothing")
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--download-timeout", type=int, default=180,
                    help="seconds to wait for each CSV (default 180)")
    args = ap.parse_args()

    set_workbooks_json(args.workbooks)
    wb_id, wb_name = resolve_workbook(args)
    tables = expand_tables(args.tables, wb_name)
    out_dir = os.path.join(args.out_root, wb_name)
    print(f"[event] {wb_name}  ({wb_id})")
    print(f"[out]   {out_dir}")

    with browser_session.clay_page(headless=not args.headed) as page:
        page.goto(f"https://app.clay.com/workspaces/{clay_ui.WORKSPACE_ID}"
                  f"/workbooks/{wb_id}",
                  wait_until="domcontentloaded", timeout=90000)
        tabs = wait_for_tabs(page)
        print(f"[tabs]  {tabs}")
        if args.list:
            return 0

        os.makedirs(out_dir, exist_ok=True)
        done, missing, failed = [], [], []
        for table in tables:
            if table not in tabs:
                print(f"  [skip] {table}: not in this workbook")
                missing.append(table)
                continue
            print(f"  [export] {table}")
            if not focus_table(page, table):
                print(f"      !! could not focus tab {table!r}")
                failed.append(table)
                continue
            try:
                export_table(page, table, out_dir, args.download_timeout * 1000)
                done.append(table)
            except Exception as e:
                print(f"      !! {type(e).__name__}: {e}")
                failed.append(table)

        print(f"\n[done] {wb_name}: {len(done)} exported"
              f"{', ' + str(len(missing)) + ' absent' if missing else ''}"
              f"{', ' + str(len(failed)) + ' FAILED' if failed else ''}")
        if failed:
            print(f"[failed] {failed}")
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
