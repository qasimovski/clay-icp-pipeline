"""List the workbooks in Labs [2026 - Qasim] -> Other Sources (a sibling
subfolder to Competitive Events) and write other_sources_workbooks.json
({workbook_id: name}), same shape as competitive_events_workbooks.json.

Read-only discovery step for applying the "Google Sheet - Lookup & Send
Data" template to this folder's Sellers - People / Buyers - People tables
(see apply_gsheet_event.py) — run once, then re-run only if the folder's
contents change.
"""

import json
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(SCRIPT_DIR), "clay_sync"))
sys.path.insert(0, os.path.join(os.path.dirname(SCRIPT_DIR), "build_automation"))

import clay_ui        # noqa: E402
import common         # noqa: E402

SUBFOLDER = "Other Sources"
OUT = os.path.join(SCRIPT_DIR, "other_sources_workbooks.json")


def main():
    with common.clay_page(headless=True) as page:
        clay_ui.open_target_location(page, subfolder=SUBFOLDER)
        id_to_name = clay_ui.list_workbooks(page)
    json.dump(id_to_name, open(OUT, "w", encoding="utf-8"),
              indent=2, ensure_ascii=False)
    print(f"{SUBFOLDER} folder: {len(id_to_name)} workbooks listed")
    for wid, name in id_to_name.items():
        print(f"  {wid}  {name}")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
