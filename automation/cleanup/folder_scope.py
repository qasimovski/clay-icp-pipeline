"""Preflight folder-scoping for the cleanup.

Navigates Home -> Labs [2026 - Qasim] -> Competitive Events, lists the workbook
ids actually in that subfolder, and writes competitive_events_workbooks.json.
delete_byproduct_tables_rollout.py intersects the manifest with this set so a workbook is only
ever cleaned if it is genuinely inside Competitive Events — nothing outside that
folder is touched, even if it happens to contain a normalized table.

Also cross-checks against the manifest and reports:
  - manifest workbooks IN the folder  (will be cleaned)
  - manifest workbooks NOT in the folder (excluded — reported, never touched)

Run on Windows (Playwright), after clay_login.py.
"""

import json
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(SCRIPT_DIR), "clay_sync"))
sys.path.insert(0, os.path.join(os.path.dirname(SCRIPT_DIR), "build_automation"))

import clay_ui        # noqa: E402
import browser_session         # noqa: E402

OUT = os.path.join(SCRIPT_DIR, "competitive_events_workbooks.json")
MANIFEST = os.path.join(SCRIPT_DIR, "cleanup_manifest.json")


def main():
    with browser_session.clay_page(headless=True) as page:
        clay_ui.open_target_location(page)
        id_to_name = clay_ui.list_workbooks(page)
    json.dump(id_to_name, open(OUT, "w", encoding="utf-8"),
              indent=2, ensure_ascii=False)
    print(f"Competitive Events folder: {len(id_to_name)} workbooks listed")
    print(f"wrote {OUT}")

    manifest = json.load(open(MANIFEST, encoding="utf-8"))
    folder_ids = set(id_to_name)
    in_folder, not_in_folder = [], []
    for e in manifest["workbooks"]:
        (in_folder if e["workbook_id"] in folder_ids else not_in_folder).append(e)
    print(f"\nmanifest workbooks IN Competitive Events (will clean): {len(in_folder)}")
    print(f"manifest workbooks NOT in the folder (EXCLUDED): {len(not_in_folder)}")
    for e in not_in_folder:
        print(f"  - EXCLUDED {e['workbook_name']} [{e['workbook_id']}]")
    # sanity: how many folder workbooks are not in the manifest (protected /
    # look-alikes / already clean) — informational only
    manifest_ids = {e["workbook_id"] for e in manifest["workbooks"]}
    other = [f"{n} [{i}]" for i, n in id_to_name.items() if i not in manifest_ids]
    print(f"\nfolder workbooks not in manifest (protected/clean/look-alike): {len(other)}")


if __name__ == "__main__":
    main()
