"""One-command Clay login for the automation session.

Opens your real Chrome at the Clay login page, waits while you log in, then
AUTO-DETECTS the logged-in workspace and saves the session (no Enter needed).
Run it yourself in a terminal:

    python automation/clay_sync/clay_login_auto.py

Leave the window open until it prints "SESSION SAVED", then it closes itself.
The saved session is what the automation reuses (.clay_session.json).
"""
import os
import sys
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
import clay_sync  # noqa: E402  (SESSION_PATH)
from playwright.sync_api import sync_playwright  # noqa: E402


def signed_in(page):
    try:
        url = (page.url or "").lower()
    except Exception:
        return False
    if "app.clay.com" not in url:
        return False
    if any(x in url for x in ("/login", "sign-in", "signin", "/auth")):
        return False
    if "/workspaces/" in url:
        return True
    try:
        title = (page.title() or "").lower()
    except Exception:
        title = ""
    if "go to market" in title:
        return False
    for t in ("Find leads", "Signals", "Campaigns", "Claygents"):
        try:
            if page.get_by_text(t, exact=True).count():
                return True
        except Exception:
            pass
    return False


def attempt(p, launch_kwargs, label):
    browser = p.chromium.launch(headless=False, **launch_kwargs)
    ctx = browser.new_context()
    page = ctx.new_page()
    for i in range(3):
        try:
            page.goto("https://app.clay.com/login",
                      wait_until="domcontentloaded", timeout=45000)
            break
        except Exception as e:
            print(f"[{label}] load retry {i}: {str(e)[:80]}", flush=True)
            time.sleep(3)
    print(f"\n>>> A Chrome window is open ({label}). Log into Clay now.")
    print(">>> Leave it open until you see 'SESSION SAVED' below.\n", flush=True)
    saved, stable = False, 0
    deadline = time.time() + 600          # 10 minutes; you control this process
    while time.time() < deadline:
        if signed_in(page):
            stable += 1
            if stable >= 2:
                time.sleep(3)
                ctx.storage_state(path=clay_sync.SESSION_PATH)
                print("\n=== SESSION SAVED to", clay_sync.SESSION_PATH, "===\n", flush=True)
                saved = True
                break
        else:
            stable = 0
        time.sleep(3)
    if not saved:
        print("TIMEOUT — login not detected.", flush=True)
    browser.close()
    return saved


def main():
    with sync_playwright() as p:
        try:
            if attempt(p, {"channel": "chrome",
                           "args": ["--disable-blink-features=AutomationControlled"]},
                       "chrome"):
                return 0
        except Exception as e:
            print("chrome channel unavailable:", str(e)[:120], flush=True)
        # fallback to bundled chromium
        return 0 if attempt(
            p, {"args": ["--disable-blink-features=AutomationControlled"]},
            "chromium") else 1


if __name__ == "__main__":
    sys.exit(main())
