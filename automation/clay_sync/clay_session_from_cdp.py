"""Copy the live Clay login out of YOUR real Chrome into .clay_session.json.

Clay bot-blocks Playwright-launched browsers on the login page (see
automation/build_automation/browser_session.py:36-45), so an interactive login inside a
launched window often can't be completed or captured. Driving a real,
user-launched Chrome over CDP is treated as a normal user and works.

You launch Chrome (once), logged into Clay:

    chrome.exe --remote-debugging-port=9222 --user-data-dir=C:/clay-debug \
               --window-size=1720,1000 https://app.clay.com

...then this script connects to it, verifies a signed-in Clay tab, and writes
the storage state to .clay_session.json so the headless passes can reuse it.

    python clay_session_from_cdp.py            # save (opens its own tab)
    python clay_session_from_cdp.py --check    # report tab state only, no write

Never closes the browser: browser.close() on a CDP connection can kill the
user's real Chrome.
"""
import argparse
import os
import sys
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
import clay_sync  # noqa: E402  (SESSION_PATH)
from playwright.sync_api import sync_playwright  # noqa: E402

CDP = os.environ.get("CLAY_CDP", "http://127.0.0.1:9222")


def _signed_in(page):
    """True if this tab is inside the Clay app (not a login/expired page)."""
    try:
        url = (page.url or "").lower()
    except Exception:
        return False
    if "app.clay.com" not in url:
        return False
    if any(x in url for x in ("/login", "sign-in", "signin", "/auth", "expired=true")):
        return False
    return "/workspaces/" in url or "/workbooks" in url


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="report what's open, don't write the session file")
    ap.add_argument("--wait", type=int, default=0,
                    help="seconds to keep polling for a signed-in tab")
    a = ap.parse_args()

    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp(CDP, timeout=8000)
        except Exception as e:
            raise SystemExit(
                f"No Chrome on {CDP} ({str(e)[:80]}).\nLaunch it with:\n"
                '  chrome.exe --remote-debugging-port=9222 '
                "--user-data-dir=C:/clay-debug https://app.clay.com")
        ctx = browser.contexts[0] if browser.contexts else browser.new_context()

        deadline = time.time() + max(a.wait, 0)
        probe = None
        while True:
            tabs = [(pg, _signed_in(pg)) for pg in ctx.pages]
            for pg, ok in tabs:
                try:
                    print(f"  {'IN ' if ok else 'out'} {pg.url[:100]}", flush=True)
                except Exception:
                    pass
            if any(ok for _, ok in tabs):
                break
            # No signed-in tab yet: open one and see where Clay redirects us.
            if probe is None:
                probe = ctx.new_page()
                try:
                    probe.goto("https://app.clay.com/workbooks",
                               wait_until="domcontentloaded", timeout=45000)
                except Exception as e:
                    print("  probe load:", str(e)[:80], flush=True)
                time.sleep(3)
                if _signed_in(probe):
                    print(f"  IN  {probe.url[:100]}", flush=True)
                    break
            if time.time() >= deadline:
                print("\nNOT SIGNED IN — log into Clay in that Chrome window "
                      "first, then re-run.", flush=True)
                return 1
            time.sleep(3)

        if a.check:
            print("\nsigned in — session NOT written (--check)", flush=True)
            return 0
        ctx.storage_state(path=clay_sync.SESSION_PATH)
        print(f"\n=== SESSION SAVED to {clay_sync.SESSION_PATH} ===", flush=True)
        print("mtime:", time.ctime(os.path.getmtime(clay_sync.SESSION_PATH)),
              flush=True)
        return 0


if __name__ == "__main__":
    sys.exit(main())
