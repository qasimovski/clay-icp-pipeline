"""Interactive Clay login that watches EVERY tab, not just the one it opened.

Why this exists: clay_login_auto.py polls only the single page it created, so a
sign-in that lands in another tab (Google OAuth opening a new tab, or you
navigating in a second tab) is never detected and the session is never saved.
This version scans ctx.pages every poll, prints what it sees so a failure is
diagnosable, and saves as soon as ANY tab looks signed in.

Run it yourself (a headed window the agent launches won't show on your desktop):

    python C:/Users/qasim/terrapinn/clay-icp-pipeline/automation/clay_sync/clay_login_watch.py

Log into Clay in the window it opens, then leave it alone until it prints
"SESSION SAVED". Optional: --minutes N (default 20).
"""
import argparse
import os
import sys
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
import clay_sync  # noqa: E402  (SESSION_PATH)
from playwright.sync_api import sync_playwright  # noqa: E402

APP_MARKERS = ("Find leads", "Signals", "Campaigns", "Claygents", "Workbooks")


def page_state(page):
    """(signed_in, url, title) for one tab; never raises."""
    try:
        url = (page.url or "").lower()
    except Exception:
        return False, "<gone>", ""
    try:
        title = page.title() or ""
    except Exception:
        title = ""
    if "app.clay.com" not in url:
        return False, url, title
    if any(x in url for x in ("/login", "sign-in", "signin", "/auth", "expired=true")):
        return False, url, title
    if "/workspaces/" in url or "/workbooks" in url:
        return True, url, title
    if "go to market" in title.lower():
        return False, url, title
    for t in APP_MARKERS:
        try:
            if page.get_by_text(t, exact=True).count():
                return True, url, title
        except Exception:
            pass
    return False, url, title


def attempt(p, launch_kwargs, label, deadline_s):
    browser = p.chromium.launch(headless=False, **launch_kwargs)
    try:
        return _watch_for_login(browser, label, deadline_s)
    finally:
        # finally, not a trailing call: an exception in the poll loop used to
        # leave the Chrome window running.
        try:
            browser.close()
        except Exception:
            pass


def _watch_for_login(browser, label, deadline_s):
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
    print(f"\n>>> Chrome window open ({label}). Log into Clay in THAT window.")
    print(f">>> It watches all tabs for {deadline_s // 60} min; leave it open "
          f"until you see 'SESSION SAVED'.\n", flush=True)

    saved, stable, last_report = False, 0, ""
    deadline = time.time() + deadline_s
    while time.time() < deadline:
        pages = list(ctx.pages)
        states = [page_state(pg) for pg in pages]
        hit = any(s[0] for s in states)
        # Print tab state whenever it changes, so a stuck login is visible.
        report = " | ".join(f"{'IN ' if s[0] else 'out'} {s[1][:70]}" for s in states)
        if report != last_report:
            print(f"  [{time.strftime('%H:%M:%S')}] {report}", flush=True)
            last_report = report
        if hit:
            stable += 1
            if stable >= 2:
                time.sleep(3)
                ctx.storage_state(path=clay_sync.SESSION_PATH)
                print("\n=== SESSION SAVED to", clay_sync.SESSION_PATH, "===\n",
                      flush=True)
                saved = True
                break
        else:
            stable = 0
        time.sleep(3)
    if not saved:
        print("TIMEOUT - no signed-in tab seen. If you logged in inside a "
              "DIFFERENT Chrome window (not the one this opened), that session "
              "cannot be captured - redo it in this script's window.", flush=True)
    return saved


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=int, default=20)
    a = ap.parse_args()
    with sync_playwright() as p:
        try:
            if attempt(p, {"channel": "chrome",
                           "args": ["--disable-blink-features=AutomationControlled"]},
                       "chrome", a.minutes * 60):
                return 0
        except Exception as e:
            print("chrome channel unavailable:", str(e)[:120], flush=True)
        return 0 if attempt(
            p, {"args": ["--disable-blink-features=AutomationControlled"]},
            "chromium", a.minutes * 60) else 1


if __name__ == "__main__":
    sys.exit(main())
