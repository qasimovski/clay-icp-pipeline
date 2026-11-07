"""Read-only scoping preflight for the EVCharge Products & Services build.

Opens `10. EVCharge [2026 - Qasim]` by id (never by name — duplicate names and
listing virtualization break name-based navigation) and reports what is inside:
subfolders and, when `--folder <id>` is given, the workbooks in that subfolder.

Nothing here creates, edits or deletes anything. Its output is the fail-closed
re-entry guard for build_ps_companies.py: a category whose workbook already
exists is skipped, so a relaunch can never create a duplicate or re-run a
(credit-spending) search.

    python evcharge_ps_scope.py                       # list the EVCharge folder
    python evcharge_ps_scope.py --folder <folder_id>  # list one subfolder
    python evcharge_ps_scope.py --folder <id> --out evcharge_ps_workbooks.json
"""

import argparse
import json
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
AUTO_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, SCRIPT_DIR)
sys.path.insert(0, os.path.join(AUTO_DIR, "clay_sync"))
sys.path.insert(0, os.path.join(AUTO_DIR, "build_automation"))

import clay_ui            # noqa: E402
import browser_session    # noqa: E402

# Rows in a folder view. Workbooks link to /workbooks/<id>; subfolders link to
# /home/<id>. Collect both so one pass answers "does Products & Services exist?"
# and "what is already in it?".
_ROWS_JS = """() => {
    const wb = {}, folders = {};
    for (const a of document.querySelectorAll('a[href]')) {
        const cell = a.closest('td, [role="cell"], [role="gridcell"]');
        const text = ((cell ? cell.textContent : a.textContent) || '').trim();
        if (!text) continue;
        let m = a.href.match(/\\/workbooks\\/([^/?]+)/);
        if (m) { if (!(m[1] in wb)) wb[m[1]] = text; continue; }
        m = a.href.match(/\\/home\\/([^/?]+)/);
        if (m) { if (!(m[1] in folders)) folders[m[1]] = text; }
    }
    return {wb, folders};
}"""


def folder_url(folder_id):
    workspace = browser_session.local_setting("workspace_id", "CLAY_WORKSPACE_ID")
    return f"{clay_ui.CLAY_URL}/workspaces/{workspace}/home/{folder_id}"


def open_folder(page, folder_id):
    """Navigate straight to `folder_id` and confirm we landed in it."""
    last = None
    for _ in range(4):
        try:
            try:
                page.goto(folder_url(folder_id), wait_until="domcontentloaded",
                          timeout=45000)
            except Exception:
                pass
            page.get_by_test_id("create-new").wait_for(timeout=20000)
            if folder_id not in page.url:
                raise clay_ui.ClayUIError(f"landed outside {folder_id}: {page.url}")
            page.wait_for_timeout(1500)
            return
        except Exception as e:
            last = e
            page.wait_for_timeout(1500)
    raise clay_ui.ClayUIError(f"could not open folder {folder_id}: {last}")


def scan(page):
    """{workbook_id: name}, {folder_id: name} for the open folder view.

    The listing virtualizes, so scroll until no new row appears three times
    running — the same accumulate-and-scroll contract as clay_ui.list_workbooks.
    """
    try:
        page.get_by_role("cell").first.wait_for(state="visible", timeout=45000)
        page.wait_for_timeout(2500)
        cell = page.get_by_role("cell").first.bounding_box()
        if cell:
            page.mouse.move(cell["x"] + cell["width"] / 2,
                            cell["y"] + cell["height"] / 2)
    except Exception:
        pass          # an empty folder has no cells; that is a real answer here

    wb, folders, stable = {}, {}, 0
    while stable < 3:
        before = len(wb) + len(folders)
        got = page.evaluate(_ROWS_JS)
        wb.update(got["wb"])
        folders.update(got["folders"])
        page.mouse.wheel(0, 3000)
        page.wait_for_timeout(1200)
        got = page.evaluate(_ROWS_JS)
        wb.update(got["wb"])
        folders.update(got["folders"])
        stable = stable + 1 if len(wb) + len(folders) == before else 0
    return wb, folders


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--folder", help="folder id to list (default: the EVCharge "
                                     "folder from config/local.yaml)")
    ap.add_argument("--out", help="write {workbook_id: name} to this file "
                                  "(under automation/cleanup/)")
    ap.add_argument("--headed", action="store_true")
    args = ap.parse_args()

    folder_id = args.folder or browser_session.local_setting(
        "evcharge_folder_id", "CLAY_EVCHARGE_FOLDER_ID")

    with browser_session.clay_page(headless=not args.headed) as page:
        open_folder(page, folder_id)
        wb, folders = scan(page)
        print(f"url: {page.url}")

    print(f"\nsubfolders ({len(folders)}):")
    for fid, name in sorted(folders.items(), key=lambda kv: kv[1]):
        print(f"  {fid}  {name}")
    print(f"\nworkbooks ({len(wb)}):")
    for wid, name in sorted(wb.items(), key=lambda kv: kv[1]):
        print(f"  {wid}  {name}")

    if args.out:
        path = os.path.join(SCRIPT_DIR, os.path.basename(args.out))
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(wb, fh, indent=2, ensure_ascii=False, sort_keys=True)
        print(f"\nwrote {path}")


if __name__ == "__main__":
    main()
