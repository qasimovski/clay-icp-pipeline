"""Formula-column builder for the Clay grid.

Uses the Formula generator panel's natural-language box + Generate, then
verifies the generated formula text and the live preview cells BEFORE saving.
Descriptions must not contain "/" (it opens the column-token picker in the
TipTap editor) or newlines.
"""
import browser_session


def _panel_editors(page):
    """Visible contenteditables inside the right-hand panel, top-to-bottom."""
    js = """() => {
      const out = [];
      document.querySelectorAll('[contenteditable="true"]').forEach((el, i) => {
        const r = el.getBoundingClientRect();
        if (r.width > 50 && r.height > 20 && r.x > 1200)
          out.push({i, x: r.x, y: r.y});
      });
      return out;
    }"""
    return page.evaluate(js)


def read_formula_text(page):
    """Text of the generated formula (second panel editor), '' if none."""
    eds = _panel_editors(page)
    if len(eds) < 2:
        return ""
    idx = eds[-1]["i"]
    return page.locator('[contenteditable="true"]').nth(idx).inner_text().strip()


def preview_cells(page, n=8, header="Preview"):
    """Texts under the draft Preview column header for the first n rows.
    Draft-column cells carry no grid testids, so sample by screen position:
    x from the Preview header, y from the testid'd cells' row bands."""
    js = """([n, header]) => {
      let hdr = null;
      for (const el of document.querySelectorAll('*')) {
        if (el.children.length === 0 && el.textContent.trim() === header) {
          const r = el.getBoundingClientRect();
          if (r.y < 220 && r.width > 0) { hdr = r; break; }
        }
      }
      if (!hdr) return null;
      const cx = hdr.x + 40;
      const rowY = {};
      for (const el of document.querySelectorAll('[data-testid^="cell-r"]')) {
        const m = el.dataset.testid.match(/^cell-r(\\d+)-c\\d+$/);
        if (!m) continue;
        const r = el.getBoundingClientRect();
        if (r.height > 5) rowY[m[1]] = r.y + r.height / 2;
      }
      const out = [];
      for (let i = 0; i < n; i++) {
        const y = rowY[String(i)];
        if (y === undefined) { out.push(null); continue; }
        const el = document.elementFromPoint(cx, y);
        out.push(el ? el.textContent.trim() : null);
      }
      return out;
    }"""
    return page.evaluate(js, [n, header])


def build_formula_column(page, description, screenshot_prefix, gen_timeout=150000):
    """Open add-column -> Formula, describe, Generate, and return
    (formula_text, preview) WITHOUT saving. Caller verifies then saves."""
    assert "/" not in description and "\n" not in description, \
        "description must avoid '/' and newlines (token picker / paragraph breaks)"
    page.get_by_role("button", name="Add column").first.click(timeout=40000)
    page.wait_for_timeout(1200)
    page.get_by_role("menuitem", name="Formula", exact=True).first.click(timeout=25000)
    page.wait_for_timeout(2000)

    eds = _panel_editors(page)
    assert eds, "no description editor found in formula panel"
    desc = page.locator('[contenteditable="true"]').nth(eds[0]["i"])
    desc.click(timeout=5000)
    page.keyboard.type(description, delay=4)
    page.wait_for_timeout(500)
    page.get_by_role("button", name="Generate", exact=True).click(timeout=10000)

    # Wait until the formula editor holds content and preview cells appear.
    page.wait_for_timeout(3000)
    deadline = gen_timeout
    waited = 0
    formula = ""
    while waited < deadline:
        formula = read_formula_text(page)
        if formula and "Hello" not in formula[:40]:
            break
        page.wait_for_timeout(2000)
        waited += 2000
    page.wait_for_timeout(2500)  # let preview column materialize
    browser_session.screenshot(page, f"{screenshot_prefix}_generated")
    return formula, preview_cells(page)


def save_column(page, screenshot_prefix):
    page.get_by_role("button", name="Save column", exact=True).click(timeout=15000)
    page.wait_for_timeout(4000)
    browser_session.screenshot(page, f"{screenshot_prefix}_saved")


def cancel_panel(page):
    page.keyboard.press("Escape")
    page.wait_for_timeout(800)


def scroll_grid_right(page):
    """Scroll the grid fully right (new columns append at the right end).
    On EMPTY tables the grid body ignores wheel events — hover the header
    band instead, which always scrolls with the grid."""
    cell = page.get_by_test_id("cell-r0-c0")
    try:
        box = cell.bounding_box()
    except Exception:
        box = None
    if box:
        page.mouse.move(box["x"] + 50, box["y"] + 5)
    else:
        page.mouse.move(600, 171)   # header band
    for _ in range(6):
        page.mouse.wheel(2500, 0)
        page.wait_for_timeout(500)


def _header_pos(page, name):
    """(x, y) of the leaf header node with exact text `name`, or None."""
    js = """(name) => {
      for (const el of document.querySelectorAll('*')) {
        if (el.children.length) continue;
        if (el.textContent.trim() !== name) continue;
        const r = el.getBoundingClientRect();
        if (r.y > 150 && r.y < 195 && r.width > 5)
          return {x: r.x, y: r.y + r.height / 2};
      }
      return null;
    }"""
    return page.evaluate(js, name)




def header_click_pos(page, name, max_rounds=10):
    """_header_pos that guarantees the header is INSIDE the viewport before
    returning — clicks at off-viewport coordinates silently miss (the cause
    of most rename/delete flakes). Scrolls horizontally to bring it in."""
    for _ in range(max_rounds):
        pos = _header_pos(page, name)
        if pos and 250 < pos["x"] < 1500:
            return pos
        cell = page.get_by_test_id("cell-r0-c0")
        try:
            box = cell.bounding_box()
            if box:
                page.mouse.move(box["x"] + 300, box["y"] + 5)
            else:
                page.mouse.move(600, 171)   # header band scrolls on empty tables
        except Exception:
            page.mouse.move(600, 171)
        if pos and pos["x"] >= 1500:
            page.mouse.wheel(800, 0)      # header is to the right — scroll right
        elif pos:
            page.mouse.wheel(-800, 0)     # header is to the left edge
        else:
            page.mouse.wheel(2000, 0)     # not in DOM yet — keep scrolling right
        page.wait_for_timeout(600)
    return None

def rename_column(page, current, new, screenshot_prefix=None):
    """Rename a column via its header menu. Grid must show the header
    (call scroll_grid_right first for right-end columns)."""
    pos = header_click_pos(page, current)
    assert pos, f"header {current!r} not clickable in viewport"
    page.mouse.click(pos["x"] + 10, pos["y"])
    page.wait_for_timeout(1200)
    # With many columns the header menu grows a long column-list section and
    # virtualizes — scroll inside the menu until "Rename column" exists.
    item = page.get_by_role("menuitem", name="Rename column", exact=True)
    for _ in range(12):
        if item.count():
            break
        menus = page.get_by_role("menu")
        bb = None
        try:
            if menus.count():
                bb = menus.last.bounding_box()
        except Exception:
            pass
        if bb:
            page.mouse.move(bb["x"] + bb["width"] / 2, bb["y"] + bb["height"] / 2)
        page.mouse.wheel(0, 400)
        page.wait_for_timeout(450)
    item.click(timeout=10000)
    page.wait_for_timeout(1000)
    page.keyboard.press("Control+a")
    page.keyboard.type(new, delay=15)
    page.keyboard.press("Enter")
    page.wait_for_timeout(1500)
    if screenshot_prefix:
        browser_session.screenshot(page, f"{screenshot_prefix}_renamed")
    # commit can lag under load — poll before declaring failure
    for _ in range(8):
        if _header_pos(page, new):
            return
        page.wait_for_timeout(2000)
    assert _header_pos(page, new), f"rename to {new!r} did not take"


def last_header(page, exclude=("Add column", "+ Add column")):
    """Rightmost real column header currently visible."""
    js = """() => {
      const out = [];
      for (const el of document.querySelectorAll('*')) {
        if (el.children.length) continue;
        const t = el.textContent.trim();
        if (!t || t.length > 60) continue;
        const r = el.getBoundingClientRect();
        if (r.y > 150 && r.y < 195 && r.x > 250 && r.width > 5)
          out.push({t, x: Math.round(r.x)});
      }
      out.sort((a, b) => a.x - b.x);
      return out.map(o => o.t);
    }"""
    import re as _re
    headers = [h for h in page.evaluate(js) if h not in exclude
               and not _re.match(r"^\d+%$", h) and h not in ("✓", "—")]
    return headers[-1] if headers else None


def grid_headers(page):
    """Visible grid column header texts (leaf nodes in the header band)."""
    js = """() => {
      const out = [];
      for (const el of document.querySelectorAll('*')) {
        if (el.children.length) continue;
        const t = el.textContent.trim();
        if (!t || t.length > 60) continue;
        const r = el.getBoundingClientRect();
        if (r.y > 150 && r.y < 195 && r.x > 250 && r.x < 1250 && r.width > 5)
          out.push({t, x: Math.round(r.x)});
      }
      out.sort((a, b) => a.x - b.x);
      return out.map(o => o.t);
    }"""
    return page.evaluate(js)


def build_formula_handwritten(page, template, screenshot_prefix):
    """Write a formula directly in the manual Formula editor — no AI
    generation. `template` uses {{Column Name}} placeholders; literal text is
    inserted via insert_text (no key events, so "/" in regexes is safe) and
    tokens via the "/" picker. Returns (formula_text, preview) unsaved."""
    import re as _re
    page.get_by_role("button", name="Add column").first.click(timeout=40000)
    page.wait_for_timeout(1200)
    page.get_by_role("menuitem", name="Formula", exact=True).first.click(timeout=25000)
    page.wait_for_timeout(2000)
    # the manual Formula section may already be expanded (empty-table panels
    # pre-expand it) — only click the accordion header if a second editor is
    # missing, and locate that header precisely inside the panel region.
    eds = _panel_editors(page)
    for _ in range(3):
        if len(eds) >= 2:
            break
        js = """() => {
          for (const el of document.querySelectorAll('*')) {
            if (el.children.length === 0 && el.textContent.trim() === 'Formula') {
              const r = el.getBoundingClientRect();
              if (r.x > 1240 && r.y > 300 && r.y < 800 && r.width > 0)
                return {x: r.x + 10, y: r.y + r.height / 2};
            }
          }
          return null;
        }"""
        pos = page.evaluate(js)
        if pos:
            page.mouse.click(pos["x"], pos["y"])
        page.wait_for_timeout(1500)
        eds = _panel_editors(page)
    assert len(eds) >= 2, "manual formula editor not found"
    ed = page.locator('[contenteditable="true"]').nth(eds[-1]["i"])
    ed.click(timeout=8000)
    page.keyboard.press("Control+a")
    page.keyboard.press("Delete")
    page.wait_for_timeout(400)
    # the manual editor parses literal {{Column}} syntax directly (probed)
    page.keyboard.insert_text(template)
    page.wait_for_timeout(4000)   # let tokens bind and the preview compute
    browser_session.screenshot(page, f"{screenshot_prefix}_manual")
    return read_formula_text(page), preview_cells(page)
