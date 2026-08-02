"""List the workbooks inside a named subfolder of Labs [2026 - Qasim].

Read-only scoping preflight, same idea as folder_scope.py but for any named
subfolder — so a pass can be restricted to exactly that folder's workbooks and
nothing else can be reached.

    python folder_scope_named.py "Product & Services" product_services_workbooks.json
"""

import json
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(SCRIPT_DIR), "clay_sync"))
sys.path.insert(0, os.path.join(os.path.dirname(SCRIPT_DIR), "build_automation"))

import clay_ui        # noqa: E402
import browser_session         # noqa: E402


def main():
    folder = sys.argv[1]
    out_name = sys.argv[2] if len(sys.argv) > 2 else None
    if not out_name:
        out_name = folder.lower().replace(" & ", "_").replace(" ", "_") \
                   + "_workbooks.json"
    out = os.path.join(SCRIPT_DIR, out_name)

    with browser_session.clay_page(headless=True) as page:
        clay_ui.open_target_location(page, subfolder=folder)
        id_to_name = clay_ui.list_workbooks(page)

    json.dump(id_to_name, open(out, "w", encoding="utf-8"), indent=2,
              ensure_ascii=False)
    print(f"{folder}: {len(id_to_name)} workbooks")
    for wid, name in sorted(id_to_name.items(), key=lambda kv: kv[1]):
        print(f"  {wid} | {name}")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
