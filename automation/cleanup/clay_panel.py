"""Open the Claygent / "Use AI" column panel in the CURRENT Clay UI.

WHY THIS EXISTS. `column_config.open_card()` drives the OLD path:

    Add column -> "Add enrichment" menuitem -> a Search box -> click a card

None of that exists any more (observed 2026-09-12). What replaced it:

    Ctrl+E (Tools) -> a contenteditable [role=combobox] search
                   -> [role=option] rows
                   -> "Claygent (AI web researcher)"   <- 1 credit/row
                   -> panel titled "Use AI", tabs: Generate | Configure

The GOOD news, and the reason this module is small: everything BELOW that point
is unchanged. Once the Configure tab is showing, the panel still has Use case /
Model ("Clay > Argon") / Configuration>Prompt / Define outputs>`response`, so
column_config's set_model_gpt41mini, fill_prompt, auto_update_off,
rename_response_output, add_output, add_run_condition and save_plain all still
apply. Do not rewrite those; only the opener moved.

THREE TRAPS, each of which cost a debugging cycle:

 1. **The first search result is "Create a column with AI for <query>"**, the AI
    prompt-writer. column_config.py has warned since the Interphex pilot that it
    must never be clicked. The Claygent card is the SECOND row. Selecting by
    ordinal is still wrong — "Favorite" and "Add filter" chips sit in the same
    geometry band and one of them got clicked during recon — so match on the
    card's TEXT and require role=option.

 2. **The search box keeps its text between openings.** It is a contenteditable,
    not an input; typing straight into it appends, giving "ClaygentClaygent" and
    zero results. Always Ctrl+A / Delete first.

 3. **The panel is sticky across navigation.** Landing on a workbook does not
    reset it: if a previous run left the "Use AI" card open, Ctrl+E reports the
    panel open and there is no search box to type into. reset_to_tools_list()
    backs out via the panel's own back arrow before searching.

Geometry note: the panel lives at x 1240..1720 in a 1720x980 viewport. On a
smaller physical display Playwright EMULATES that viewport, so these
coordinates hold and screenshots show the whole panel even when the real Chrome
window is too narrow to display it. Do not "fix" the viewport to match the
window — every x>1240 test in column_config.py depends on it.
"""

import os

import browser_session
import column_config as colcfg

VerificationError = browser_session.VerificationError

# The card we want, and the one we must never click.
CARD_PREFIX = "claygent (ai web researcher)"
BANNED_PREFIX = "create a column with ai"

# Is the Tools list (not a card) showing? Its tab strip is the tell.
TOOLS_LIST = """() => {
  const norm = s => (s||'').replace(/\\s+/g,' ').trim();
  for (const el of document.querySelectorAll('*')) {
    if (el.children.length) continue;
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.x < 1240 || r.y > 260) continue;
    const t = norm(el.textContent);
    if (t === 'Import' || t === 'Enrich' || t === 'Export' || t === 'Functions') return true;
  }
  return false;
}"""

# Is a column card ("Use AI") open?
CARD_OPEN = """() => {
  const norm = s => (s||'').replace(/\\s+/g,' ').trim();
  for (const el of document.querySelectorAll('*')) {
    if (el.children.length) continue;
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.x < 1240 || r.y > 120) continue;
    if (norm(el.textContent) === 'Use AI') return true;
  }
  return false;
}"""

# The panel's back arrow, left of the title in the card header.
BACK_ARROW = """() => {
  let best = null;
  for (const el of document.querySelectorAll('button')) {
    const r = el.getBoundingClientRect();
    if (r.width < 10 || r.width > 40 || r.x < 1240 || r.x > 1300) continue;
    if (r.y < 55 || r.y > 95) continue;
    if (!best || r.x < best.x) best = {x: r.x + r.width/2, y: r.y + r.height/2};
  }
  return best;
}"""

OPTION_ROWS = """() => {
  const out = [];
  for (const el of document.querySelectorAll('[role="option"]')) {
    const r = el.getBoundingClientRect();
    if (r.x < 1240 || r.x > 1760 || r.width < 60 || r.height < 20) continue;
    out.push({t: (el.innerText||'').trim().replace(/\\s+/g,' ').slice(0,90),
              x: Math.round(r.x), y: Math.round(r.y), h: Math.round(r.height)});
  }
  out.sort((a,b) => a.y - b.y);
  return out;
}"""


def _say(m):
    # Clay's card labels carry U+2219 ("Enrichment ∙ By Clay") and the Windows
    # console is cp1252, so a bare print() raises UnicodeEncodeError *after* the
    # card has already been matched — a crash that looks like a selector failure
    # but is only a logging one. Never let logging fail the pass.
    try:
        print(m, flush=True)
    except UnicodeEncodeError:
        print(m.encode("ascii", "replace").decode("ascii"), flush=True)


def reset_to_tools_list(page):
    """Leave whatever card is open and get back to the searchable Tools list."""
    page.keyboard.press("Escape")
    page.wait_for_timeout(700)
    for _ in range(3):
        if page.evaluate(TOOLS_LIST):
            return True
        if page.evaluate(CARD_OPEN):
            back = page.evaluate(BACK_ARROW)
            if back:
                page.mouse.click(back["x"], back["y"])
                page.wait_for_timeout(2000)
                continue
        # Panel closed, or in an unknown state: Ctrl+E TOGGLES, so only press it
        # when nothing of ours is showing.
        page.keyboard.press("Control+E")
        page.wait_for_timeout(3000)
    return page.evaluate(TOOLS_LIST)


def search_box(page):
    for el in page.locator('[role="combobox"]').all():
        try:
            bb = el.bounding_box()
        except Exception:
            continue
        if bb and 1240 <= bb["x"] <= 1760 and bb["y"] < 200:
            return el
    return None


def open_claygent(page, shots_dir=None, slug="claygent"):
    """Tools -> search Claygent -> open the card -> show the Configure tab.

    Leaves the panel on Configure, which is where column_config's primitives
    expect to act. Raises rather than guessing if the card cannot be matched.
    """
    if not reset_to_tools_list(page):
        raise VerificationError("could not get back to the Tools list")

    box = search_box(page)
    if box is None:
        raise VerificationError("Tools search box (role=combobox) not found")
    box.click(timeout=10000)
    page.wait_for_timeout(1000)
    page.keyboard.press("Control+a")     # trap 2: it keeps its previous text
    page.keyboard.press("Delete")
    page.wait_for_timeout(400)
    page.keyboard.type("Claygent", delay=40)
    page.wait_for_timeout(4000)

    rows = page.evaluate(OPTION_ROWS)
    if not rows:
        raise VerificationError("no [role=option] results for 'Claygent'")
    target = None
    for r in rows:
        t = r["t"].lower()
        if t.startswith(BANNED_PREFIX):
            continue                     # trap 1: the AI prompt-writer
        if t.startswith(CARD_PREFIX):
            target = r
            break
    if target is None:
        raise VerificationError(
            f"'Claygent (AI web researcher)' not among results: "
            f"{[r['t'][:40] for r in rows[:6]]}")

    _say(f"    opening card: {target['t'][:50]!r}")
    page.mouse.click(target["x"] + 60, target["y"] + target["h"] // 2)
    page.wait_for_timeout(7000)
    if not page.evaluate(CARD_OPEN):
        raise VerificationError("'Use AI' card did not open")

    # The card lands on Generate (the prompt-writer). Configure is the tab we want.
    conf = page.get_by_text("Configure", exact=True)
    if not conf.count():
        raise VerificationError("'Configure' tab not found on the Use AI card")
    conf.first.click(timeout=10000)
    page.wait_for_timeout(3000)
    if shots_dir:
        browser_session.screenshot(page, os.path.join(shots_dir, f"{slug}_configure"))
    _say("    Configure tab open")
    return True


def header(page, name):
    """Locator for a column header in the CURRENT Clay UI.

    Headers are `button[role=menuitem]` now. formula_columns._header_pos scans a
    y-band of 150..195 and the header row sits at y~124, which is why
    `grid_headers()` returns ['Status Code: 200', '100%'] and every rename/delete
    built on it fails. A role+name locator also AUTO-SCROLLS: the header row is
    far wider than the viewport (columns render at negative x once scrolled), so
    geometry-based clicking misses off-screen headers entirely.
    """
    return page.get_by_role("menuitem", name=name, exact=True)


def delete_column(page, name, expect_one=True):
    """Delete a column by header name. Destructive — callers must be sure.

    Menu items confirmed present 2026-09-12: Rename column / Edit column /
    Insert 1 column left|right / Edit description / Change color / Text /
    Used in... / Duplicate / Save as function / Text to columns / Sort / Dedupe /
    Filter on this column / Pin / Hide / Delete.
    """
    h = header(page, name)
    n = h.count()
    if n == 0:
        raise VerificationError(f"no column header named {name!r}")
    if expect_one and n != 1:
        raise VerificationError(
            f"{n} headers named {name!r} — refusing to guess which to delete")
    h.first.click(timeout=20000)
    page.wait_for_timeout(2000)

    item = page.get_by_role("menuitem", name="Delete", exact=True)
    if not item.count():
        raise VerificationError(
            f"'Delete' not in the header menu for {name!r} (menu may not have opened)")
    item.last.click(timeout=10000)
    page.wait_for_timeout(1800)

    # Clay may confirm. Click only an affirmative button, never a bare "Cancel".
    for label in ("Delete", "Delete column", "Confirm", "Yes"):
        btn = page.get_by_role("button", name=label, exact=True)
        try:
            if btn.count() and btn.last.is_visible():
                btn.last.click(timeout=5000)
                break
        except Exception:
            pass
    page.wait_for_timeout(2500)
    return True


def panel_has(page, text, ymin=100, ymax=980):
    """Is `text` present as a leaf in the panel band? Used for assertions."""
    js = """([txt, ymin, ymax]) => {
      const norm = s => (s||'').replace(/\\s+/g,' ').trim();
      for (const el of document.querySelectorAll('*')) {
        if (el.children.length) continue;
        const r = el.getBoundingClientRect();
        if (r.width === 0 || r.x < 1240 || r.y < ymin || r.y > ymax) continue;
        if (norm(el.textContent) === txt) return true;
      }
      return false;
    }"""
    return page.evaluate(js, [text, ymin, ymax])
