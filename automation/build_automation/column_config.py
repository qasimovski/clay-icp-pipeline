"""Primitives for the per-event Labs pipeline build, distilled from the
Interphex pilot (see queue/cmd_*.py + shots/ for provenance).

Pilot lessons encoded here:
- Never wait on networkidle inside the Clay app; wait on concrete elements.
- Formula NL descriptions must avoid "/" and newlines (token picker).
- keyboard.insert_text for prompt bodies (no key events -> no "/" picker);
  keyboard.type("/") only for deliberate token insertion.
- Claygent/Enrich saves with auto-run OFF get a plain Save (saves dormant);
  sends with auto-run ON get a split menu -> match "Save and don't run" or
  "Save and run N rows in this view" by regex.
- Claygent multi-output fields do NOT create child columns until run; use
  extractor formulas {{Col}}?.Field instead.
- Send-mapping checkboxes for columns already present in the destination are
  locked; tolerate extras, never loop-retry a row more than twice.
- The 'Create a column with AI' card and paid provider cards (Owler, Apollo,
  Pubrio...) must never be clicked; match the native cards precisely.
- New plain-type columns open an inline 'New Column' name editor: type the
  name immediately, don't blind-rename afterwards.
"""
import os
import re
import time

import browser_session
import formula_columns
import clay_ui


class VerificationError(Exception):
    """A verification gate failed — stop this event, never click past it."""


# ---------------------------------------------------------------- navigation

def open_workbook(page, folder, table=None, retries=4):
    last = None
    for _ in range(retries):
        try:
            clay_ui.open_target_location(page)
            clay_ui.open_workbook(page, folder)
            if table:
                focus_table(page, table)
            return
        except Exception as e:
            last = e
            time.sleep(4)
    raise VerificationError(f"cannot open workbook {folder!r}: {last}")


def focus_table(page, table, retries=3):
    last = None
    for attempt in range(retries):
        try:
            tab = page.get_by_role("button", name=table, exact=True).first
            tab.wait_for(state="visible", timeout=30000)
            tab.click(timeout=15000)
            page.wait_for_timeout(600)
            # wait for ANY data cell — c0 may be virtualized out when scrolled right
            page.get_by_test_id(re.compile(r"^cell-r\d+-c\d+$")).first.wait_for(
                state="visible", timeout=120000)
            return
        except Exception as e:
            last = e
            page.keyboard.press("Escape")
            page.wait_for_timeout(5000)
    raise VerificationError(f"focus_table {table!r} failed: {last}")


def focus_table_maybe_empty(page, table, retries=3):
    """Focus a table tab that may hold zero rows (no data-cell wait)."""
    last = None
    for attempt in range(retries):
        try:
            tab = page.get_by_role("button", name=table, exact=True).first
            tab.wait_for(state="visible", timeout=30000)
            tab.click(timeout=15000)
            page.wait_for_timeout(2500)
            return
        except Exception as e:
            last = e
            page.keyboard.press("Escape")
            page.wait_for_timeout(5000)
    raise VerificationError(f"focus_table_maybe_empty {table!r} failed: {last}")


def table_exists(page, name):
    return bool(clay_ui.existing_tables(page, [name]))


def close_tools_panel(page):
    """Collapse the Tools sidebar if it is open (it narrows the grid and
    shifts far-right header geometry). Acts ONLY when the sidebar's 'Tools'
    heading is present — never touches Claygent/send config panels."""
    js = """() => {
      for (const el of document.querySelectorAll('*')) {
        if (el.children.length === 0 && el.textContent.trim() === 'Tools') {
          const r = el.getBoundingClientRect();
          if (r.x > 1240 && r.y > 70 && r.y < 130) return {y: r.y + r.height / 2};
        }
      }
      return null;
    }"""
    pos = page.evaluate(js)
    if not pos:
        return False
    # collapse icon sits at the far right of the Tools header row
    js2 = """(y) => {
      let best = null;
      for (const el of document.querySelectorAll('button')) {
        const r = el.getBoundingClientRect();
        if (r.width > 0 && r.x > 1600 && Math.abs(r.y + r.height/2 - y) < 18)
          if (!best || r.x > best.x) best = {x: r.x + r.width/2, y: r.y + r.height/2};
      }
      return best;
    }"""
    btn = page.evaluate(js2, pos["y"])
    if not btn:
        return False
    page.mouse.click(btn["x"], btn["y"])
    page.wait_for_timeout(1200)
    return page.evaluate(js) is None


def header_exists(page, name):
    close_tools_panel(page)
    if formula_columns._header_pos(page, name):
        return True
    formula_columns.scroll_grid_right(page)
    if formula_columns._header_pos(page, name):
        return True
    # scroll fully left and check start of grid too (hover the header band —
    # the grid body ignores wheel events on empty tables)
    page.mouse.move(600, 171)
    for _ in range(8):
        page.mouse.wheel(-2500, 0)
        page.wait_for_timeout(250)
    return bool(formula_columns._header_pos(page, name))


# ------------------------------------------------------------- panel helpers

def open_enrichment_search(page, term):
    page.keyboard.press("Escape")
    page.wait_for_timeout(600)
    page.get_by_role("button", name="Add column").first.click(timeout=40000)
    page.wait_for_timeout(1200)
    page.get_by_role("menuitem", name="Add enrichment", exact=True).first.click(timeout=10000)
    page.wait_for_timeout(2500)
    boxes = page.get_by_placeholder("Search")
    filled = False
    for i in range(boxes.count()):
        b = boxes.nth(i)
        try:
            if b.is_visible():
                b.fill(term)
                filled = True
        except Exception:
            pass
    if not filled:
        raise VerificationError("no search box in enrichment panel")
    page.wait_for_timeout(2200)


def click_native_card(page, must_contain, must_not=("Create a column", "Owler",
                                                    "Apollo", "Harmonic",
                                                    "Prospeo", "Pubrio",
                                                    "Icypeas", "Surfe",
                                                    "Artificial Intelligence"),
                      search_term=None):
    """Click a card in the enrichment picker. Cards can render late or the
    search filter can silently miss — rescan (and optionally re-fill the
    search) a few times before giving up."""
    for attempt in range(4):
        target = None
        for el in page.get_by_role("button").all():
            try:
                if not el.is_visible():
                    continue
                t = el.inner_text().strip().replace("\n", " ")
                if all(m in t for m in must_contain) and not any(m in t for m in must_not):
                    target = el
                    break
            except Exception:
                pass
        if target:
            target.click(timeout=20000)
            page.wait_for_timeout(4000)
            return
        page.wait_for_timeout(3000)
        if search_term:
            boxes = page.get_by_placeholder("Search")
            for i in range(boxes.count()):
                b = boxes.nth(i)
                try:
                    if b.is_visible():
                        b.fill(search_term)
                except Exception:
                    pass
            page.wait_for_timeout(2200)
    raise VerificationError(f"native card {must_contain} not found")


def open_card(page, search_term, must_contain, must_not=None, retries=3):
    """Open an enrichment/source card, retrying the WHOLE sequence (panel
    open + search + card click) — the card list itself sometimes fails to
    populate, in which case re-clicking inside a dead panel can't help."""
    last = None
    for attempt in range(retries):
        try:
            page.keyboard.press("Escape")
            page.wait_for_timeout(800)
            open_enrichment_search(page, search_term)
            kwargs = {"search_term": search_term}
            if must_not is not None:
                kwargs["must_not"] = must_not
            click_native_card(page, must_contain, **kwargs)
            return
        except Exception as e:
            last = e
            page.keyboard.press("Escape")
            page.wait_for_timeout(5000)
    raise VerificationError(f"could not open card {must_contain}: {last}")


def set_model_gpt41mini(page):
    page.get_by_text("Configure", exact=True).first.click(timeout=10000)
    page.wait_for_timeout(2000)
    page.get_by_role("combobox").filter(has_text="Argon").first.click(timeout=10000)
    page.wait_for_timeout(1500)
    page.get_by_role("option", name="GPT 4.1 Mini").first.click(timeout=10000)
    page.wait_for_timeout(1200)


def rename_panel_title(page, name, current="Use AI"):
    js = """(cur) => {
      for (const el of document.querySelectorAll('*')) {
        if (el.children.length === 0 && el.textContent.trim() === cur) {
          const r = el.getBoundingClientRect();
          if (r.x > 1240 && r.y < 130) return {x: r.x + r.width + 18, y: r.y + r.height/2};
        }
      }
      return null;
    }"""
    pos = page.evaluate(js, current)
    if not pos:
        return False
    page.mouse.click(pos["x"], pos["y"])
    page.wait_for_timeout(1000)
    page.keyboard.press("Control+a")
    page.keyboard.type(name, delay=12)
    page.keyboard.press("Enter")
    page.wait_for_timeout(800)
    return True


def prompt_editor(page):
    js = """() => {
      const out = [];
      document.querySelectorAll('[contenteditable="true"]').forEach((el, i) => {
        const r = el.getBoundingClientRect();
        if (r.x > 1240 && r.width > 200) out.push({i, y: Math.round(r.y)});
      });
      return out;
    }"""
    eds = [e for e in page.evaluate(js) if e["y"] > 330]
    if not eds:
        raise VerificationError("prompt editor not found")
    return page.locator('[contenteditable="true"]').nth(eds[0]["i"])


def fill_prompt(page, body, inputs):
    """body: multiline str (inserted line-wise). inputs: [(label, token_or_None)]."""
    box = prompt_editor(page)
    box.click(timeout=8000)
    page.wait_for_timeout(400)
    for line in body.split("\n"):
        if line:
            page.keyboard.insert_text(line)
        page.keyboard.press("Enter")
    for label, token in inputs:
        page.keyboard.insert_text(label)
        if token:
            page.keyboard.type("/", delay=60)
            page.wait_for_timeout(1300)
            page.keyboard.type(token, delay=25)
            page.wait_for_timeout(1300)
            page.keyboard.press("Enter")
            page.wait_for_timeout(600)
        page.keyboard.press("Enter")
    page.wait_for_timeout(600)
    return box.inner_text()


def rename_response_output(page, name):
    resp = page.locator('input[value="response"]')
    if not resp.count():
        return False
    resp.first.click(timeout=8000)
    page.keyboard.press("Control+a")
    page.keyboard.type(name, delay=15)
    page.keyboard.press("Tab")
    page.wait_for_timeout(500)
    return True


def add_output(page, name):
    page.get_by_role("button", name="Add output").click(timeout=8000)
    page.wait_for_timeout(1000)
    f1 = page.locator('input[value="field1"]')
    if not f1.count():
        raise VerificationError("new output input (field1) not found")
    f1.first.click(timeout=8000)
    page.keyboard.press("Control+a")
    page.keyboard.type(name, delay=15)
    page.keyboard.press("Tab")
    page.wait_for_timeout(500)


def collapse_configuration(page):
    """Collapse the Configuration accordion so Run settings come into view."""
    try:
        btn = page.get_by_role("button", name="Configuration").first
        if btn.count():
            btn.click(timeout=5000)
            page.wait_for_timeout(1000)
            return True
    except Exception:
        pass
    return False


def auto_update_off(page):
    """Turn OFF every ON auto-run switch in the right panel. The switch can sit
    below the fold behind a long prompt — collapse Configuration first and use
    locator clicks (they auto-scroll). Raises if no switch is found at all."""
    collapse_configuration(page)
    found = 0
    flipped = 0
    for el in page.locator('[role="switch"]').all():
        try:
            bb = el.bounding_box()
        except Exception:
            continue
        if bb and bb["x"] > 1500 and bb["y"] > 140:
            found += 1
            st = el.get_attribute("aria-checked") or el.get_attribute("data-state")
            if st in ("true", "checked"):
                el.click(timeout=8000)   # locator click scrolls into view
                page.wait_for_timeout(700)
                now = el.get_attribute("aria-checked") or el.get_attribute("data-state")
                if now in ("true", "checked"):
                    raise VerificationError("auto-run switch would not turn off")
                flipped += 1
    if found == 0:
        raise VerificationError("no auto-run switch found in panel")
    return flipped


def add_run_condition(page, pre_text, token, post_text=""):
    """Check 'Add run condition' and type pre_text + {{token}} + post_text."""
    lab = page.get_by_text("Add run condition", exact=True)
    if not lab.count():
        raise VerificationError("run condition label not visible")
    lb = lab.first.bounding_box()
    cb = None
    for el in page.locator('[role="checkbox"]').all():
        try:
            bb = el.bounding_box()
        except Exception:
            continue
        if bb and bb["x"] > 1240 and abs((bb["y"] + bb["height"] / 2) -
                                         (lb["y"] + lb["height"] / 2)) < 12:
            cb = el
            break
    if cb is None:
        raise VerificationError("run condition checkbox not found")
    st = cb.get_attribute("aria-checked") or cb.get_attribute("data-state")
    if st not in ("true", "checked"):
        cb.click(timeout=8000)
        page.wait_for_timeout(1400)
    js = """() => {
      const out = [];
      document.querySelectorAll('[contenteditable="true"]').forEach((el, i) => {
        const r = el.getBoundingClientRect();
        if (r.x > 1240 && r.width > 150 && r.y > 150 && r.y < 935)
          out.push({i, y: Math.round(r.y)});
      });
      return out;
    }"""
    eds = page.evaluate(js)
    if not eds:
        raise VerificationError("condition editor not found")
    ed = page.locator('[contenteditable="true"]').nth(eds[-1]["i"])
    ed.click(timeout=8000)
    if ed.inner_text().strip().replace("﻿", ""):
        return  # already set (resume)
    if pre_text:
        page.keyboard.type(pre_text, delay=25)
    page.keyboard.type("/", delay=60)
    page.wait_for_timeout(1300)
    page.keyboard.type(token, delay=25)
    page.wait_for_timeout(1300)
    page.keyboard.press("Enter")
    page.wait_for_timeout(600)
    if post_text:
        page.keyboard.type(post_text, delay=25)
    page.wait_for_timeout(400)
    cond = ed.inner_text()
    if token not in cond:
        raise VerificationError(f"condition text missing token: {cond[:80]!r}")


def save_plain(page):
    """Save the open column config. With auto-run OFF this commits directly;
    if a save menu appears anyway (auto-run was somehow ON), choose the
    explicit no-run option rather than leaving the panel hanging."""
    save = page.get_by_role("button", name="Save", exact=True).last
    bb = save.bounding_box()
    if not bb or bb["x"] < 1400:
        raise VerificationError("save button not in panel position")
    save.click(timeout=10000)
    page.wait_for_timeout(2500)
    opt = page.get_by_text("Save and don't run", exact=True)
    try:
        if opt.count() and opt.first.is_visible():
            opt.first.click(timeout=8000)
    except Exception:
        pass
    page.wait_for_timeout(4000)


def save_via_menu(page, option_regex):
    """Open the Save split menu and click the option matching option_regex."""
    save = page.get_by_role("button", name="Save", exact=True).last
    bb = save.bounding_box()
    if not bb or bb["x"] < 1400:
        raise VerificationError("save button not in panel position")
    page.mouse.click(bb["x"] + bb["width"] - 12, bb["y"] + bb["height"] / 2)
    page.wait_for_timeout(1500)
    rx = re.compile(option_regex, re.I)
    target = None
    for role in ("menuitem", "button"):
        for el in page.get_by_role(role).all():
            try:
                if el.is_visible() and rx.search(el.inner_text().strip()):
                    target = el
                    break
            except Exception:
                pass
        if target:
            break
    if not target:
        raise VerificationError(f"save option {option_regex!r} not found")
    target.click(timeout=8000)
    page.wait_for_timeout(5000)


# Names that must never be blind-renamed (imported + pipeline columns).
KNOWN_HEADERS = {
    "%", "Event", "Company Name", "Profile URL", "Booth", "Year",
    "Description", "Address Line 1", "City", "Postal Code", "Country",
    "Phone", "Email", "Website", "Normalize a Domain",
    "Normalize Company Name", "Normalized Country", "Official Domain",
    "Company Domain", "Enrich Company", "Name", "Website (2)",
    "Employee Count", "Size", "Industry", "Url", "Type", "Domain", "Founded",
    "Annual Revenue", "Description (2)", "Resolved Description",
    "Labs Series Registrar", "Side", "Classification", "Fit", "Country Fit",
    "Composite Tier", "Company Composite Tier", "Send to Blocklist",
    "Send to Sellers", "Send to Buyers", "Send to Contacts", "Sub Level",
    "Sector Keyword Match", "JT Fit", "Preview", "Add column",
}


def rename_last_column(page, name, timeout_s=60):
    """Rename the newest (rightmost) column to `name`. Polls until the new
    column actually renders (never blind-renames a known pipeline column),
    retries the header-menu flow through toasts/panels."""
    deadline = time.time() + timeout_s
    last_err = None
    while time.time() < deadline:
        try:
            page.keyboard.press("Escape")
            page.wait_for_timeout(500)
            close_tools_panel(page)
            formula_columns.scroll_grid_right(page)
            if formula_columns._header_pos(page, name):
                return  # already named (or renamed on a prior attempt)
            auto = formula_columns.last_header(page)
            if not auto or auto in KNOWN_HEADERS or auto.startswith("Rows from"):
                page.wait_for_timeout(3000)   # new column not rendered yet
                continue
            formula_columns.rename_column(page, auto, name)
            return
        except Exception as e:
            last_err = e
            page.wait_for_timeout(2000)
    raise VerificationError(f"column {name!r} not present after rename: {last_err}")


def column_status(page, name):
    """Text of the status cell under a column header: '' for the healthy
    checkmark icon, '0%' for a dormant claygent, '100%' for an errored
    formula (red) or completed send (green)."""
    pos = formula_columns._header_pos(page, name)
    if not pos:
        return None
    js = """(x) => {
      for (const el of document.querySelectorAll('*')) {
        if (el.children.length) continue;
        const r = el.getBoundingClientRect();
        if (r.y > 190 && r.y < 218 && Math.abs(r.x - x) < 160 && r.x >= x - 40)
          { const t = el.textContent.trim(); if (t) return t; }
      }
      return '';
    }"""
    return page.evaluate(js, pos["x"])


def delete_column(page, name):
    """Delete a column WE created (broken formula recovery only)."""
    pos = formula_columns.header_click_pos(page, name)
    if not pos:
        raise VerificationError(f"cannot find column {name!r} to delete")
    page.mouse.click(pos["x"] + 10, pos["y"])
    page.wait_for_timeout(1200)
    page.get_by_role("menuitem", name="Delete", exact=True).click(timeout=10000)
    page.wait_for_timeout(1500)
    # confirm dialog if one appears
    for label in ("Delete", "Confirm", "Delete column"):
        btn = page.get_by_role("button", name=label, exact=True)
        try:
            if btn.count() and btn.last.is_visible():
                btn.last.click(timeout=5000)
                break
        except Exception:
            pass
    page.wait_for_timeout(2000)


def unknown_headers(page):
    """Headers not in KNOWN_HEADERS (candidates for leftover recovery)."""
    close_tools_panel(page)
    formula_columns.scroll_grid_right(page)
    js = """() => {
      const out = [];
      for (const el of document.querySelectorAll('*')) {
        if (el.children.length) continue;
        const t = el.textContent.trim();
        if (!t || t.length > 60) continue;
        const r = el.getBoundingClientRect();
        if (r.y > 150 && r.y < 195 && r.x > 250 && r.width > 5) out.push(t);
      }
      return out;
    }"""
    return [h for h in page.evaluate(js)
            if h not in KNOWN_HEADERS and not h.startswith("Rows from")
            and not re.match(r"^\d+%$", h) and h not in ("✓", "—")]


def recover_leftover(page, prefixes, name, attempts=4, allow=()):
    """Resume helper: if an earlier run saved a column but died before the
    rename, find it by auto-name prefix and rename it to `name`. Retries with
    settle time — the grid needs a beat after tab switches before header
    clicks reliably open the menu."""
    js = """() => {
      const out = [];
      for (const el of document.querySelectorAll('*')) {
        if (el.children.length) continue;
        const t = el.textContent.trim();
        if (!t || t.length > 60) continue;
        const r = el.getBoundingClientRect();
        if (r.y > 150 && r.y < 195 && r.x > 250 && r.width > 5) out.push(t);
      }
      return out;
    }"""
    last = None
    for attempt in range(attempts):
        close_tools_panel(page)
        page.wait_for_timeout(2500)
        formula_columns.scroll_grid_right(page)
        target = None
        for h in page.evaluate(js):
            if any(h.startswith(p) for p in prefixes) and                     (h not in KNOWN_HEADERS or h in allow):
                target = h
                break
        if target is None:
            return False
        try:
            formula_columns.rename_column(page, target, name)
            return True
        except Exception as e:
            last = e
            page.keyboard.press("Escape")
            page.wait_for_timeout(3000)
    raise VerificationError(f"leftover {prefixes} rename to {name!r} failed: {last}")


def add_csv_table_robust(page, path):
    """Import a CSV as a new table in the open workbook. Unlike
    clay_ui.add_csv_table this tolerates multiple visible 'Search' boxes
    (Tools panel + create-table modal) and retries the whole flow."""
    table = os.path.splitext(os.path.basename(path))[0]
    last = None
    for attempt in range(3):
        try:
            page.keyboard.press("Escape")
            page.wait_for_timeout(800)
            add = clay_ui._add_table_button(page)
            add.wait_for(state="visible", timeout=20000)
            add.click(timeout=15000)
            page.wait_for_timeout(1500)
            filled = False
            boxes = page.get_by_placeholder("Search")
            for i in range(boxes.count()):
                b = boxes.nth(i)
                try:
                    if b.is_visible():
                        b.fill("Import from CSV")
                        filled = True
                except Exception:
                    pass
            if not filled:
                raise VerificationError("no visible search box in create-table modal")
            page.wait_for_timeout(1500)
            page.get_by_role("button", name="Import from CSV",
                             exact=True).first.click(timeout=10000)
            clay_ui._import_csv(page, path, has_continue=True)
            tab = page.get_by_role("button", name=table, exact=True).first
            tab.wait_for(state="visible", timeout=60000)
            tab.click(timeout=10000)
            page.wait_for_timeout(1000)
            page.get_by_test_id(re.compile(r"^cell-r\d+-c0$")).first.wait_for(
                state="visible", timeout=180000)
            return
        except Exception as e:
            last = e
            page.wait_for_timeout(3000)
    raise VerificationError(f"csv import failed for {table}: {last}")


# -------------------------------------------------------------- send actions

MAP_SCAN = """() => {
  const out = [];
  const btns = [...document.querySelectorAll('button')].map(b => {
    const r = b.getBoundingClientRect();
    return {t: b.textContent.trim(), y: r.y + r.height / 2, x: r.x};
  }).filter(b => b.x > 1240 && b.t && b.t.length < 60);
  for (const el of document.querySelectorAll('[role="checkbox"]')) {
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.x < 1200 || r.y < 140 || r.y > 935) continue;
    const cy = r.y + r.height / 2;
    let best = null, bestd = 1e9;
    for (const b of btns) {
      const d = Math.abs(b.y - cy);
      if (d < bestd && d < 10) { bestd = d; best = b.t; }
    }
    out.push({x: Math.round(r.x + r.width/2), y: Math.round(cy), label: best,
              st: el.getAttribute('aria-checked') || el.getAttribute('data-state') || ''});
  }
  return out;
}"""


def _map_scroll_top(page):
    page.mouse.move(1470, 500)
    for _ in range(20):
        page.mouse.wheel(0, -700)
        page.wait_for_timeout(140)


def set_mapping(page, keep, required=None, budget=4):
    """Bounded mapping setter: aim for exactly `keep` ON; tolerate locked
    extras; raise if any of `required` (default: keep) ends up OFF."""
    attempts = {}
    for _pass in range(4):
        changed = 0
        _map_scroll_top(page)
        for _ in range(16):
            for r in page.evaluate(MAP_SCAN):
                lbl = r["label"]
                if not lbl or lbl == "Add run condition":
                    continue
                on = r["st"] in ("true", "checked")
                want = lbl in keep
                if on != want and attempts.get(lbl, 0) < budget:
                    attempts[lbl] = attempts.get(lbl, 0) + 1
                    page.mouse.click(r["x"], r["y"])
                    page.wait_for_timeout(380)
                    changed += 1
            page.mouse.wheel(0, 520)
            page.wait_for_timeout(450)
        if changed == 0:
            break
    _map_scroll_top(page)
    on, seen = [], set()
    for _ in range(16):
        for r in page.evaluate(MAP_SCAN):
            if r["label"] and r["label"] not in seen:
                seen.add(r["label"])
                if r["st"] in ("true", "checked"):
                    on.append(r["label"])
        page.mouse.wheel(0, 520)
        page.wait_for_timeout(380)
    missing = set(required if required is not None else keep) - set(on)
    # columns not offered at all (e.g. dormant claygent outputs) are reported,
    # not fatal, IF they were merely in keep; required must be present.
    if missing:
        raise VerificationError(f"mapping missing required columns: {missing}")
    return sorted(set(on) - set(keep))  # tolerated extras


def open_send_panel(page, retries=3):
    last = None
    for attempt in range(retries):
        try:
            _open_send_panel_once(page)
            return
        except Exception as e:
            last = e
            page.keyboard.press("Escape")
            page.wait_for_timeout(5000)
    raise VerificationError(f"send panel would not open: {last}")


def _open_send_panel_once(page):
    open_enrichment_search(page, "Send table")
    js = """() => {
      for (const el of document.querySelectorAll('*')) {
        if (el.children.length === 0 && el.textContent.trim() === 'Send table data') {
          const r = el.getBoundingClientRect();
          if (r.x > 1240 && r.y > 150) return {x: r.x + 20, y: r.y + r.height/2};
        }
      }
      return null;
    }"""
    pos = page.evaluate(js)
    if not pos:
        raise VerificationError("Send table data item not found")
    page.mouse.click(pos["x"], pos["y"])
    page.wait_for_timeout(4000)
    # confirm config panel (Select table + Create present)
    if not page.get_by_role("button", name="Select table", exact=True).count():
        raise VerificationError("send config panel did not open")


def dest_existing(page, path_buttons):
    """Select an existing destination via the picker modal. path_buttons is
    the click sequence, e.g. ['Home', 'Labs [2026 - Qasim]',
    'Labs - Block List - Companies', 'Table 1']."""
    page.get_by_role("button", name="Select table", exact=True).first.click(timeout=10000)
    page.wait_for_timeout(2500)
    for name in path_buttons:
        page.get_by_role("button", name=name, exact=True).first.click(timeout=15000)
        page.wait_for_timeout(1800)
    page.get_by_role("button", name="Select table", exact=True).last.click(timeout=10000)
    page.wait_for_timeout(3000)


def dest_create_table(page, name, retries=3):
    def committed():
        if page.locator(f'input[value="{name}"]').count():
            return True
        js = """(nm) => {
          for (const el of document.querySelectorAll('*')) {
            if (el.children.length === 0 && el.textContent.trim() === nm) {
              const r = el.getBoundingClientRect();
              if (r.x > 1240 && r.y > 200 && r.y < 500 && r.width > 0) return true;
            }
          }
          return false;
        }"""
        return page.evaluate(js, name)

    last = None
    for attempt in range(retries):
        try:
            if committed():
                return
            # a prior attempt may have left a pending destination — adopt it
            inp = page.locator('input[value^="New table"]')
            if not inp.count():
                page.get_by_role("button", name="Create", exact=True).first.click(timeout=10000)
                page.wait_for_timeout(1500)
                page.get_by_role("menuitem", name="Table", exact=True).click(timeout=10000)
                page.wait_for_timeout(3000)
                inp = page.locator('input[value^="New table"]')
                for _ in range(5):
                    if inp.count():
                        break
                    page.wait_for_timeout(2000)
            if not inp.count():
                raise VerificationError("new-table name input not found")
            inp.first.click(timeout=8000)
            page.keyboard.press("Control+a")
            page.keyboard.type(name, delay=25)
            page.keyboard.press("Tab")
            page.wait_for_timeout(1200)
            if not committed():
                raise VerificationError("destination name did not commit")
            return
        except Exception as e:
            last = e
            page.wait_for_timeout(4000)
    raise VerificationError(f"dest_create_table {name!r} failed: {last}")
