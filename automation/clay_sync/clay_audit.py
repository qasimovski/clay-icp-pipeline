"""
Read-only audit of the Clay target folder.

Opens Labs [2026 - Qasim] -> Competitive Events, lists every workbook name,
and flags names that appear more than once (duplicates left behind by failed
import retries). Changes nothing in Clay.

Usage:
  python clay_audit.py          # headless
  python clay_audit.py --show   # visible browser
"""

import argparse
import os
import sys
from collections import Counter

from playwright.sync_api import sync_playwright

import clay_ui

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SESSION_PATH = os.path.join(SCRIPT_DIR, ".clay_session.json")

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--show", action="store_true",
                        help="run with a visible browser")
    args = parser.parse_args()

    if not os.path.exists(SESSION_PATH):
        print(f"No Clay session found at {SESSION_PATH}. Run: python clay_login.py")
        sys.exit(1)

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=not args.show,
            args=["--disable-blink-features=AutomationControlled"],
        )
        ctx = browser.new_context(storage_state=SESSION_PATH, user_agent=UA,
                                  viewport={"width": 1600, "height": 900})
        ctx.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"
        )
        page = ctx.new_page()
        if not clay_ui.is_logged_in(page):
            browser.close()
            print("Clay session expired or invalid — run: python clay_login.py")
            sys.exit(1)

        clay_ui.open_target_location(page)
        names = list(clay_ui.list_workbooks(page).values())
        browser.close()

    counts = Counter(names)
    dupes = {n: c for n, c in counts.items() if c > 1}

    print(f"\n{len(names)} workbook rows, {len(counts)} distinct names in "
          f"{clay_ui.TARGET_FOLDER} / {clay_ui.TARGET_SUBFOLDER}")
    if dupes:
        print(f"\nDUPLICATES ({len(dupes)} names) — delete the extras by hand:")
        for n in sorted(dupes):
            print(f"  {counts[n]}x  {n}")
    else:
        print("\nNo duplicate workbook names found.")
    print("\nAll workbooks:")
    for n in sorted(counts):
        suffix = f"  (x{counts[n]})" if counts[n] > 1 else ""
        print(f"  {n}{suffix}")


if __name__ == "__main__":
    main()
