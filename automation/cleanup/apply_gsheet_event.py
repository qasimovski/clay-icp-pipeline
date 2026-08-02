"""Apply the "Google Sheet - Lookup & Send Data" template to a single named
table (Sellers - People / Buyers - People) in one workbook.

This is the first pass that targets the People tables directly rather than the
entity's main normalized table — TABLE is passed in per-call (not config-
derived) because both "Sellers - People" and "Buyers - People" need the
template applied within the same workbook, and Sponsors-entity events use
differently-named people tables ("Sponsors - Sellers - People" / "Sponsors -
Buyers - People") that the caller resolves via pipeline_config per workbook.

Idempotent: skips if the template's signature column is already present.
Skips (does not error) if the named table doesn't exist in the workbook at all
(some events produced zero Sellers/Buyers rows and have no such table).

Supports --recon (see apply_gsheet_recon.py) via `recon=True`: opens the
template's Configure panel and reports its field labels/fill-state without
touching Save, so the fill logic can be written from real observations.
"""

import os
import re
import sys
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
AUTO_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, SCRIPT_DIR)
sys.path.insert(0, os.path.join(AUTO_DIR, "clay_sync"))
sys.path.insert(0, os.path.join(AUTO_DIR, "build_automation"))

import clay_ui        # noqa: E402
import common         # noqa: E402
import build_lib as B  # noqa: E402

TEMPLATE = "Google Sheet - Lookup & Send Data"
# Signature column that indicates the template is already applied to a table.
# Verified empirically (Analytica China / Sellers - People, 2026-07-21): this
# template does NOT add a "Send table data" column like the other Lookup&Send
# templates — it adds three action columns ("Lookup in Audiences", "Lookup
# row", "Add row"). "Lookup in Audiences" is the first of the three and a
# name unlikely to collide with anything else on a People table.
SIG = "Lookup in Audiences"

# Config-panel field-label column: same DOM region used by the other
# template-apply passes (apply_lookup_event.py, apply_v1_event.py) — Clay
# renders every template's Configure panel in this fixed x/y band regardless
# of which template is open.
_LABEL_SCAN_JS = """()=>{
  const out=[];
  const norm=s=>(s||'').replace(/\\s+/g,' ').trim();
  for(const el of document.querySelectorAll('*')){
    if(el.children.length) continue;
    const t=norm(el.textContent);
    if(!t) continue;
    const r=el.getBoundingClientRect();
    if(r.x>1240&&r.x<1600&&r.y>120&&r.y<900&&r.width>0){
      out.push({text:t, x:Math.round(r.x), y:Math.round(r.y), w:Math.round(r.width)});
    }
  }
  return out;
}"""


def _open_template(page):
    va = page.get_by_text("View all enrichments", exact=False)
    try:
        va.first.click(timeout=8000)
    except Exception:
        page.keyboard.press("Control+e"); page.wait_for_timeout(2000)
        va.first.click(timeout=15000)
    page.wait_for_timeout(2500)
    page.get_by_role("tab", name="Templates").first.click(timeout=25000)
    page.wait_for_timeout(2000)
    you = page.get_by_role("radio", name="You")
    if not you.count():
        you = page.get_by_text("You", exact=True)
    try:
        you.first.click(timeout=8000); page.wait_for_timeout(1500)
    except Exception:
        pass
    tpl = page.get_by_text(TEMPLATE, exact=True)
    for _ in range(6):
        if tpl.count() and tpl.first.is_visible():
            break
        sb = page.get_by_placeholder("Search for a data point or provider")
        try:
            if sb.count():
                sb.first.fill(TEMPLATE)
        except Exception:
            pass
        page.wait_for_timeout(1500); tpl = page.get_by_text(TEMPLATE, exact=True)
    if not (tpl.count() and tpl.first.is_visible()):
        raise clay_ui.ClayUIError(f"template {TEMPLATE!r} not found")
    tpl.first.click(timeout=20000)
    page.wait_for_timeout(3500)
    if not page.get_by_text("Configure", exact=True).count():
        raise clay_ui.ClayUIError("config panel did not open")


def _open_template_retry(page, tries=3):
    last = None
    for _ in range(tries):
        try:
            _open_template(page); return
        except Exception as e:
            last = e
            try:
                page.keyboard.press("Escape"); page.wait_for_timeout(2500)
            except Exception:
                pass
    raise clay_ui.ClayUIError(f"could not open template after {tries}: {last}")


def recon(page, entry, table_name, say, screenshot=None):
    """Open the template on `table_name` and report the Configure panel's
    contents (labels + surrounding text) WITHOUT filling or saving. Read-only
    apart from opening the panel; always Escapes at the end."""
    wid = entry["workbook_id"]; name = entry["workbook_name"]
    clay_ui.open_workbook_by_id(page, wid)
    if not B.table_exists(page, table_name):
        say(f"SKIP {name}/{table_name}: table does not exist")
        return {"workbook_id": wid, "workbook_name": name, "table": table_name,
                "status": "no_table"}
    B.focus_table_maybe_empty(page, table_name)
    page.wait_for_timeout(800)
    already = clay_ui._find_header_rect(page, SIG)
    _open_template_retry(page)
    rows = page.evaluate(_LABEL_SCAN_JS)
    rows.sort(key=lambda r: (r["y"], r["x"]))
    say(f"RECON {name}/{table_name}: already_has_{SIG!r}={bool(already)}")
    for r in rows:
        say(f"  y={r['y']:<4} x={r['x']:<4} w={r['w']:<4} {r['text']!r}".encode(
            "ascii", "backslashreplace").decode("ascii"))
    if screenshot:
        page.screenshot(path=screenshot)
        say(f"screenshot saved: {screenshot}")
    page.keyboard.press("Escape")
    return {"workbook_id": wid, "workbook_name": name, "table": table_name,
            "status": "recon", "already_applied": bool(already), "panel": rows}


def _save_disabled(page):
    s = page.get_by_role("button", name="Save", exact=True).last
    return s.evaluate("el=>el.disabled||el.getAttribute('data-disabled')!==null")


def _pct(page):
    """Current 'N% of table completed' reading, or None if not present."""
    t = page.evaluate("()=>document.body.innerText")
    m = re.search(r"(\d+)% of table completed", t)
    return int(m.group(1)) if m else None


def _wait_for_full_completion(page, say, table_name, max_wait_s=1800, poll_s=12):
    """Poll the table's run-progress percentage (accurate per user 2026-07-22)
    until it reaches 100%, so every row's action columns finish - not just
    the first batch. Returns (reached_100: bool, last_pct: int|None)."""
    start = time.time()
    last = None
    while time.time() - start < max_wait_s:
        pct = _pct(page)
        if pct == 100:
            return True, 100
        if pct != last:
            say(f"   ...{table_name} progress {pct}%")
            last = pct
        page.wait_for_timeout(poll_s * 1000)
    return False, last


def apply_gsheet(page, entry, table_name, dry_run, say):
    wid = entry["workbook_id"]; name = entry["workbook_name"]
    clay_ui.open_workbook_by_id(page, wid)

    if not B.table_exists(page, table_name):
        say(f"SKIP {name}/{table_name}: table does not exist")
        return {"workbook_id": wid, "workbook_name": name, "table": table_name,
                "status": "no_table"}

    B.focus_table_maybe_empty(page, table_name)
    page.wait_for_timeout(800)

    if clay_ui._find_header_rect(page, SIG):
        pct = _pct(page)
        if pct == 100:
            say(f"SKIP {name}/{table_name}: template already applied and 100% complete")
            return {"workbook_id": wid, "workbook_name": name, "table": table_name,
                    "status": "ok", "note": "already_applied"}
        say(f"RESUME {name}/{table_name}: template already applied but at {pct}% "
            f"- waiting for the run to finish all rows")
        done, last_pct = _wait_for_full_completion(page, say, table_name)
        if not done:
            say(f"INCOMPLETE {name}/{table_name}: still at {last_pct}% after max wait "
                f"- will resume on next run")
            return {"workbook_id": wid, "workbook_name": name, "table": table_name,
                    "status": "incomplete", "note": "already_applied", "state": last_pct}
        say(f"DONE {name}/{table_name}: already-applied run finished all rows (100%)")
        return {"workbook_id": wid, "workbook_name": name, "table": table_name,
                "status": "ok", "note": "already_applied_resumed"}

    _open_template_retry(page)

    if dry_run:
        say(f"DRYRUN {name}/{table_name}: template opened; would Save and run")
        page.keyboard.press("Escape")
        return {"workbook_id": wid, "workbook_name": name, "table": table_name,
                "status": "dryrun"}

    for _ in range(10):
        if not _save_disabled(page):
            break
        page.wait_for_timeout(1000)

    save = page.get_by_role("button", name="Save", exact=True).last
    r = save.bounding_box()
    page.mouse.click(r["x"] + r["width"] - 5, r["y"] + r["height"] / 2)
    page.wait_for_timeout(1200)
    ti = None
    for mi in page.get_by_role("menuitem").all():
        try:
            t = mi.inner_text().strip()
            if mi.is_visible() and re.search(r"Save and run.*in this view", t, re.I):
                ti = mi; break
        except Exception:
            pass
    if ti is None:
        items = [m.inner_text().strip().replace("\n", " ") for m in
                 page.get_by_role("menuitem").all() if m.is_visible()]
        say(f"ABORT {name}/{table_name}: no 'Save and run ... in this view': {items[-4:]}")
        page.keyboard.press("Escape")
        return {"workbook_id": wid, "workbook_name": name, "table": table_name,
                "status": "aborted", "reason": "no_run_option"}
    lbl = ti.inner_text().strip().replace("\n", " ")
    ti.click(timeout=8000)
    page.wait_for_timeout(16000)
    st = page.evaluate("()=>{const t=document.body.innerText;"
                        "return (t.match(/\\d+% of table completed/)||['?'])[0];}")
    # The "N% of table completed" text can be a stale leftover from an
    # unrelated column's auto-run and isn't proof OUR save landed (seen once:
    # Analytica India / Sellers - People reported "applied" + 100% but the
    # column was never actually added) - verify the signature column is
    # really there before calling this "ok".
    confirmed = False
    for _ in range(8):
        if clay_ui._find_header_rect(page, SIG):
            confirmed = True
            break
        page.wait_for_timeout(2500)
    if not confirmed:
        say(f"UNCONFIRMED {name}/{table_name}: clicked {lbl!r} but {SIG!r} column "
            f"never appeared | {st}")
        return {"workbook_id": wid, "workbook_name": name, "table": table_name,
                "status": "unconfirmed", "ran": lbl, "state": st}

    # Column exists now, but a big table can still be mid-run (seen tables
    # sitting at 20-90% right after the initial 16s wait) - keep polling the
    # (accurate, per user 2026-07-22) completion percentage until every row
    # is actually done, not just the first batch.
    done, last_pct = _wait_for_full_completion(page, say, table_name)
    if not done:
        say(f"INCOMPLETE {name}/{table_name}: applied + {lbl!r} but still at "
            f"{last_pct}% after max wait - will resume on next run")
        return {"workbook_id": wid, "workbook_name": name, "table": table_name,
                "status": "incomplete", "ran": lbl, "state": last_pct}
    say(f"DONE {name}/{table_name}: applied + {lbl!r} | 100% of table completed")
    return {"workbook_id": wid, "workbook_name": name, "table": table_name,
            "status": "ok", "ran": lbl, "state": "100% of table completed"}


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Ad-hoc single workbook/table run")
    ap.add_argument("workbook_id")
    ap.add_argument("workbook_name")
    ap.add_argument("table_name")
    ap.add_argument("--recon", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--screenshot")
    a = ap.parse_args()
    entry = {"workbook_id": a.workbook_id, "workbook_name": a.workbook_name}
    with common.clay_page(headless=not a.headed) as page:
        def say(m):
            print(m, flush=True)
        if a.recon:
            out = recon(page, entry, a.table_name, say, screenshot=a.screenshot)
        else:
            out = apply_gsheet(page, entry, a.table_name, a.dry_run, say)
        print("\nRESULT:", out)
