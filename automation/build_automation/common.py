"""Shared helpers for the Interphex pilot Clay build automation.

Reuses the proven navigation primitives from scrapers/_clay_sync (session,
folder navigation, workbook opening, virtualized-listing scrolling). Every
step script imports from here so a Clay UI change only touches one place.

HARD SAFETY RULE encoded here: nothing in this module ever clicks a control
whose accessible name matches /run/i unless it is an explicit "don't run" /
"save without running" option. Paid columns are configured with auto-run OFF.
"""
import os
import sys
import contextlib

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CLAY_SYNC_DIR = os.path.normpath(os.path.join(SCRIPT_DIR, "..", "clay_sync"))
sys.path.insert(0, CLAY_SYNC_DIR)

import clay_ui          # noqa: E402
import clay_sync        # noqa: E402  (SESSION_PATH, UA)
import humanize         # noqa: E402

from playwright.sync_api import sync_playwright  # noqa: E402

SHOTS_DIR = os.path.join(SCRIPT_DIR, "shots")
os.makedirs(SHOTS_DIR, exist_ok=True)

WORKBOOK = "Interphex"
MAIN_TABLE = "Exhibitors_normalized"


def shot(page, name):
    path = os.path.join(SHOTS_DIR, f"{name}.png")
    page.screenshot(path=path)
    print(f"[shot] {path}")
    return path


# Clay bot-blocks Playwright-launched browsers (serves the logged-out marketing
# page even with valid cookies). Driving a REAL user-launched Chrome over CDP is
# treated as a normal user and works. Launch that Chrome with:
#   chrome.exe --remote-debugging-port=9222 --user-data-dir=C:\clay-debug
#             --window-size=1720,1000
# and log into Clay in it. Set CLAY_CDP to override the endpoint.
CDP_ENDPOINT = os.environ.get("CLAY_CDP", "http://127.0.0.1:9222")


@contextlib.contextmanager
def clay_page(headless=True):
    """Yield a logged-in Clay page. Prefer driving a real Chrome over CDP (not
    bot-blocked); fall back to launching a bundled browser with the saved
    session if no CDP browser is available."""
    with sync_playwright() as p:
        browser = None
        if os.environ.get("CLAY_USE_CDP"):
            try:
                browser = p.chromium.connect_over_cdp(CDP_ENDPOINT, timeout=5000)
            except Exception:
                browser = None
        if browser is not None:
            ctx = browser.contexts[0] if browser.contexts else browser.new_context()
            page = ctx.new_page()
            try:
                page.set_viewport_size({"width": 1720, "height": 980})
            except Exception:
                pass
            try:
                yield page
            finally:
                try:
                    page.close()          # close only our tab; leave Chrome open
                except Exception:
                    pass
                # NOTE: do NOT call browser.close() here — on a CDP connection it
                # can terminate the user's real Chrome. Just let the driver detach.
            return
        # fallback: bundled browser + saved session
        if not os.path.exists(clay_sync.SESSION_PATH):
            raise SystemExit(f"No Clay session at {clay_sync.SESSION_PATH}; run clay_login.py")
        browser = p.chromium.launch(
            headless=headless,
            args=["--disable-blink-features=AutomationControlled",
                  # memory-savers so the worker survives on a low-RAM machine
                  "--disable-dev-shm-usage", "--disable-gpu",
                  "--disable-extensions", "--disable-background-networking",
                  "--disable-features=site-per-process,TranslateUI",
                  "--renderer-process-limit=2"])
        ctx = browser.new_context(storage_state=clay_sync.SESSION_PATH,
                                  user_agent=clay_sync.UA,
                                  viewport={"width": 1720, "height": 980})
        ctx.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
        page = ctx.new_page()
        try:
            yield page
        finally:
            browser.close()


def open_interphex(page, table=None, retries=3):
    """Navigate to the Interphex workbook (retrying the flaky hydration) and
    optionally focus one of its table tabs."""
    last = None
    for _ in range(retries):
        try:
            clay_ui.open_target_location(page)
            clay_ui.open_workbook(page, WORKBOOK)
            break
        except Exception as e:  # flaky hydration — retry from the top
            last = e
    else:
        raise clay_ui.ClayUIError(f"Could not open {WORKBOOK}: {last}")
    if table:
        focus_table(page, table)


def focus_table(page, table):
    """Click a table tab in the open workbook's bottom bar and wait for data."""
    tab = page.get_by_role("button", name=table, exact=True).first
    tab.wait_for(state="visible", timeout=30000)
    tab.click(timeout=10000)
    humanize.dwell(0.6, 1.2)
    clay_ui._wait_for_table_data(page)


def dump_headers(page):
    """Best-effort dump of the focused table's column headers."""
    js = """() => {
        const out = [];
        for (const sel of ['[role="columnheader"]',
                           '[data-testid^="header-"]',
                           '[data-testid^="column-header"]']) {
            for (const el of document.querySelectorAll(sel)) {
                const t = el.textContent.trim();
                if (t && !out.includes(t)) out.push(t);
            }
            if (out.length) return {sel, out};
        }
        return {sel: null, out};
    }"""
    res = page.evaluate(js)
    print(f"[headers via {res['sel']}] ({len(res['out'])}):")
    for h in res["out"]:
        print("   ", repr(h))
    return res["out"]


def visible_texts(page, role, limit=80):
    """Print visible elements of a role (menu recon)."""
    items = page.get_by_role(role).all()
    texts = []
    for it in items:
        try:
            if it.is_visible():
                t = it.inner_text().strip()
                if t:
                    texts.append(t)
        except Exception:
            pass
    print(f"--- visible {role} ({len(texts)}) ---")
    for t in texts[:limit]:
        print(repr(t))
    return texts
