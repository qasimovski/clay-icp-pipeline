"""Apply the "Waterfall and Validate Email" template to a Sellers/Buyers -
People table in one workbook.

Step (h) "Email Pass" for the people tables, following the method proven on
Speakers_normalized: apply -> rebind -> verify -> gated run.

Configure panel (recon TechBio UK / Sellers - People, 2026-07-28 — matches the
user's ACHEMA screenshot):
  Full Name        pre-filled with the table's own "Full Name"          -> leave
  Company Domain   pre-filled with "Company Domain"                     -> leave
  success          EMPTY -> "Add row" > "success"
  Company Name     EMPTY -> "Company Table Data" > "Name"
  LinkedIn Profile pre-filled with "LinkedIn Profile"                   -> leave

Creates (per ACHEMA, the user's finished reference): the waterfall provider
columns + WORK EMAIL + Validate Email + Smtp Provider + Email Validation Result.
WORK EMAIL is the "already applied" signature.

  python apply_people_waterfall.py <wid> <name> "Sellers - People" --recon
  python apply_people_waterfall.py <wid> <name> "Sellers - People" --dry-run
  python apply_people_waterfall.py <wid> <name> "Sellers - People" [--run]
"""

import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
AUTO_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, SCRIPT_DIR)
sys.path.insert(0, os.path.join(AUTO_DIR, "clay_sync"))
sys.path.insert(0, os.path.join(AUTO_DIR, "build_automation"))

import clay_ui        # noqa: E402
import browser_session         # noqa: E402
import column_config as colcfg  # noqa: E402
import add_workemail_waterfall as panel   # noqa: E402  (shared helpers)
import apply_email_template      # noqa: E402  (_click_opt_scroll)

TEMPLATE_CANDIDATES = [n for n in (
    os.environ.get("CLAY_PEOPLE_TEMPLATE"),
    "Waterfall and Validate Email",
) if n]
TEMPLATE_USED = None

MARKER = "WORK EMAIL"
PEOPLE_TABLES = ("Sellers - People", "Buyers - People")

# Only these two need filling; the rest arrive pre-filled.
#
# Picker geometry (recon TechBio UK, 2026-07-28): group rows sit at x~1309,
# their children at x~1323, and nested PROPERTIES at x~1359 — the last of which
# overlaps the grid showing through beside the panel, so leaf steps use a wider
# window and rely on exact text. "Add row" is not top-level: it lives under the
# group "Google Sheet - Lookup & Send Data". Properties only render a beat after
# the parent is clicked, hence the generous waits.
GRP_X = (1290, 1345)      # groups and their immediate children
LEAF_X = (1290, 1400)     # nested properties, indented further right
MAPPING = {
    "success": {
        "filter": "success",
        "steps": [("Google Sheet - Lookup & Send Data",) + GRP_X,
                  ("Add row",) + GRP_X,
                  ("success",) + LEAF_X],
    },
    "Company Name": {
        # no filter: filtering by "Name" reports "No properties" because the
        # search does not descend into an unexpanded column's schema
        "steps": [("Company Table Data",) + GRP_X,
                  ("Name",) + LEAF_X],
    },
}
# What every field should read once configured (substring match).
EXPECTED = {
    "Full Name": "Full Name",
    "Company Domain": "Company Domain",
    "success": "success",
    "Company Name": "Name",
    "LinkedIn Profile": "LinkedIn Profile",
}
# Legitimate chip segments; anything else in the band is grid content showing
# through beside the panel (see apply_email_template for why this matters).
CHIP_VOCAB = ["Full Name", "Company Domain", "LinkedIn Profile", "success",
              "Company Name", "Name", "Add row", "Company Table Data",
              "Lookup row", "Lookup in Audiences",
              "Google Sheet - Lookup & Send Data"]

_BOX = """([label, vocab])=>{
  const norm=s=>(s||'').replace(/\\s+/g,' ').trim();
  const leaves=[...document.querySelectorAll('*')].filter(e=>!e.children.length);
  let ly=null;
  for(const el of leaves){
    const r=el.getBoundingClientRect();
    if(r.width===0||r.x<1255||r.x>1400||r.y<200||r.y>900) continue;
    if(norm(el.textContent)===label){ly=r.y; break;}
  }
  if(ly===null) return null;
  let box=null; const parts=[];
  for(const el of leaves){
    const r=el.getBoundingClientRect();
    if(r.width===0||r.x<1265||r.x>1700||r.y<ly+8||r.y>ly+60) continue;
    const t=norm(el.textContent);
    if(!t) continue;
    if(t.includes('Start typing')){
      box={x:Math.round(r.x+30), y:Math.round(r.y+r.height/2), empty:true};
      continue;
    }
    if(vocab.includes(t)) parts.push({t, x:r.x});
  }
  if(box) return box;
  parts.sort((a,b)=>a.x-b.x);
  const seen=[];
  for(const p of parts) if(!seen.length||seen[seen.length-1]!==p.t) seen.push(p.t);
  return {empty:false, chip:seen.join(' > ') || null};
}"""


def chips(page):
    out = {}
    for label in EXPECTED:
        try:
            info = page.evaluate(_BOX, [label, CHIP_VOCAB])
        except Exception:
            info = None
        out[label] = None if not info else (
            "<empty>" if info.get("empty") else info.get("chip"))
    return out


def _open_template(page):
    global TEMPLATE_USED
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
    for name in TEMPLATE_CANDIDATES:
        cand = page.get_by_text(name, exact=True)
        for _ in range(5):
            if cand.count() and cand.first.is_visible():
                break
            sb = page.get_by_placeholder("Search for a data point or provider")
            try:
                if sb.count():
                    sb.first.fill(name)
            except Exception:
                pass
            page.wait_for_timeout(1500)
            cand = page.get_by_text(name, exact=True)
        if cand.count() and cand.first.is_visible():
            cand.first.click(timeout=20000)
            TEMPLATE_USED = name
            page.wait_for_timeout(4000)
            if not page.get_by_text("Configure", exact=True).count():
                raise clay_ui.ClayUIError("config panel did not open")
            return
    raise clay_ui.ClayUIError(f"no template found; tried {TEMPLATE_CANDIDATES}")


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


def _click_step(page, text, xmin, xmax, tries=6):
    """Click one picker row, scrolling and waiting for lazily-rendered
    properties (they can take a couple of seconds to appear after the parent
    is expanded)."""
    for attempt in range(tries):
        pt = page.evaluate(_FIND_ROW, [text, xmin, xmax])
        if pt:
            page.mouse.click(pt["x"], pt["y"])
            page.wait_for_timeout(3000)
            return True
        if attempt % 2 == 1:
            page.mouse.move(1420, 700)
            page.mouse.wheel(0, 280)
        page.wait_for_timeout(1200)
    return False


_FIND_ROW = """([txt, xmin, xmax])=>{
  const norm=s=>(s||'').replace(/\s+/g,' ').trim();
  for(const el of document.querySelectorAll('*')){
    if(el.children.length) continue;
    const r=el.getBoundingClientRect();
    if(r.width===0||r.x<xmin||r.x>xmax||r.y<180||r.y>1000) continue;
    if(norm(el.textContent)===txt)
      return {x:Math.round(r.x+15), y:Math.round(r.y+r.height/2)};
  }
  return null;
}"""


def _fill(page, label, say):
    """Map one field by walking the picker tree."""
    info = None
    for _ in range(10):
        info = page.evaluate(_BOX, [label, CHIP_VOCAB])
        if info:
            break
        page.wait_for_timeout(800)
    if not info:
        say(f"  {label}: field not in panel")
        return "missing"
    if not info.get("empty"):
        say(f"  {label}: already set to {info.get('chip')!r}")
        return "prefilled"

    page.mouse.click(info["x"], info["y"])
    page.wait_for_timeout(2000)
    spec = MAPPING[label]
    if spec.get("filter"):
        page.keyboard.type(spec["filter"], delay=40)
        page.wait_for_timeout(2500)
    for step, xmin, xmax in spec["steps"]:
        if not _click_step(page, step, xmin, xmax):
            say(f"  {label}: step {step!r} not found")
            return "failed"
    page.wait_for_timeout(1500)
    got = (page.evaluate(_BOX, [label, CHIP_VOCAB]) or {}).get("chip")
    if got and EXPECTED[label] in got:
        say(f"  {label} = {got!r}")
        return "ok"
    say(f"  {label}: ended as {got!r} (wanted {EXPECTED[label]!r})")
    return "failed"


def apply_people_template(page, entry, table, dry_run, say, recon=False,
                          run_after=False):
    wid, name = entry["workbook_id"], entry["workbook_name"]
    clay_ui.open_workbook_by_id(page, wid)
    if not colcfg.table_exists(page, table):
        say(f"SKIP {name}/{table}: table does not exist")
        return {"workbook_id": wid, "workbook_name": name, "table": table,
                "status": "no_table"}
    colcfg.focus_table_maybe_empty(page, table)
    page.wait_for_timeout(1000)

    _open_template_retry(page)
    say(f"  template: {TEMPLATE_USED!r}")

    if recon:
        c = chips(page)
        say(f"  fields: {c}")
        page.keyboard.press("Escape")
        return {"workbook_id": wid, "workbook_name": name, "table": table,
                "status": "recon", "fields": c}

    statuses = {label: _fill(page, label, say) for label in MAPPING}
    final = chips(page)
    say(f"  mapped: {final}")

    bad = {k: v for k, v in final.items()
           if not (v and EXPECTED[k] in v)}
    if bad:
        say(f"ABORT {name}/{table}: mapping incomplete {bad}")
        page.keyboard.press("Escape")
        return {"workbook_id": wid, "workbook_name": name, "table": table,
                "status": "aborted", "reason": "mapping_incomplete",
                "fields": final, "statuses": statuses}

    if dry_run:
        say(f"DRYRUN {name}/{table}: mapping complete, not saving")
        page.keyboard.press("Escape")
        return {"workbook_id": wid, "workbook_name": name, "table": table,
                "status": "dryrun", "fields": final, "statuses": statuses}

    opt = r"Save and run.*in this view" if run_after else r"Save and don'?t run"
    try:
        colcfg.save_via_menu(page, opt)
        saved = "run" if run_after else "no_run"
    except Exception:
        saved = panel.save_column(page, say)
    say(f"  saved via {saved}")
    page.wait_for_timeout(6000)

    confirmed = clay_ui._find_header_rect(page, MARKER) is not None
    return {"workbook_id": wid, "workbook_name": name, "table": table,
            "status": "ok", "saved": saved, "fields": final,
            "statuses": statuses, "sig_visible": confirmed,
            "template": TEMPLATE_USED}


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("workbook_id")
    ap.add_argument("workbook_name")
    ap.add_argument("table", nargs="?", default="Sellers - People")
    ap.add_argument("--recon", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--run", action="store_true",
                    help="Save and run in this view (spends credits)")
    ap.add_argument("--headed", action="store_true")
    a = ap.parse_args()
    entry = {"workbook_id": a.workbook_id, "workbook_name": a.workbook_name}
    with browser_session.clay_page(headless=not a.headed) as page:
        def say(m):
            print(m, flush=True)
        print("\nRESULT:", apply_people_template(page, entry, a.table,
                                                 a.dry_run, say,
                                                 recon=a.recon,
                                                 run_after=a.run))
