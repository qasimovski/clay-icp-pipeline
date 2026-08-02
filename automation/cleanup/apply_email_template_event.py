"""Apply the user's "Find Email and Validate Email" template to one workbook's
Speakers_normalized.

This is the user's rebuilt template (2026-07-27) and it is much simpler than the
one this repo fought with earlier: its Configure panel has four fields, already
expanded (no CollapsibleSection rows), and no Run settings — the run conditions
and auto-run state live on the columns the template creates, baked in by the
template itself.

  Speaker Name   -> the table's own "Speaker Name" column
  company_domain -> Find LinkedIn and Enrich Person > Enrich person >
                    current_experience > [0] > company_domain
  org            -> Find LinkedIn and Enrich Person > Enrich person > org
  LinkedIn URL   -> auto-mapped

Saves WITHOUT running by default so the created columns can be inspected first;
pass --run to use the "Save and run ... in this view" option instead.

  python apply_email_template_event.py <wid> <name> --recon
  python apply_email_template_event.py <wid> <name> --dry-run
  python apply_email_template_event.py <wid> <name>
"""

import os
import re
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
AUTO_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, SCRIPT_DIR)
sys.path.insert(0, os.path.join(AUTO_DIR, "clay_sync"))
sys.path.insert(0, os.path.join(AUTO_DIR, "build_automation"))

import clay_ui        # noqa: E402
import common         # noqa: E402
import build_lib as B  # noqa: E402
import add_workemail_waterfall_event as W  # noqa: E402  (shared helpers)

TABLE = "Speakers_normalized"
TEMPLATE_CANDIDATES = [n for n in (
    os.environ.get("CLAY_EMAIL_TEMPLATE"),
    "Find Email and Validate Email",
) if n]
TEMPLATE_USED = None

# field label -> {"filter": text typed to narrow the tree (optional),
#                  "steps": [(row text, xmin, xmax), ...]}
# Verified on CPHI Americas 2026-07-27. The table's own columns are nested under
# the action that produced them, so every path starts at the 'Find LinkedIn and
# Enrich Person' group.
MAPPING = {
    "Speaker Name": {
        "steps": [("Find LinkedIn and Enrich Person", 1290, 1720),
                  ("Speaker Name", 1290, 1720)],
    },
    "company_domain": {
        # plain clicking stops at current_experience (clicking it selects the
        # array); typing 'domain' first expands the tree to the leaf
        "filter": "domain",
        "steps": [("Find LinkedIn and Enrich Person", 1290, 1720),
                  ("Enrich person", 1320, 1720),
                  ("current_experience", 1330, 1720),
                  ("0", 1355, 1420),
                  ("company_domain", 1340, 1720)],
    },
    "org": {
        "steps": [("Find LinkedIn and Enrich Person", 1290, 1720),
                  ("Enrich person", 1320, 1720),
                  ("org", 1320, 1720)],
    },
}
# Substring each field's chip must contain once mapped. LinkedIn URL is
# auto-mapped by the template; verified, never set.
EXPECTED = {
    "Speaker Name": "Speaker Name",
    "company_domain": "company_domain",
    "org": "org",
    "LinkedIn URL": "LinkedIn URL",
}

# Value box / chip for a field label in this (non-collapsible) panel: the label
# sits at x~1270 and its box directly below at x~1279.
# Every legitimate chip segment is one of these; anything else in the band is
# grid content showing through beside the panel.
CHIP_VOCAB = ["Speaker Name", "company_domain", "org", "Enrich person",
              "current_experience", "0", "LinkedIn URL", "Title", "Company",
              "Country", "Find LinkedIn and Enrich Person", "Name", "Email"]

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
  const seen=[]; for(const p of parts) if(!seen.length||seen[seen.length-1]!==p.t) seen.push(p.t);
  return {empty:false, chip:seen.join(' > ') || null};
}"""


def chips(page):
    out = {}
    for label in list(MAPPING) + ["LinkedIn URL"]:
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
        for _ in range(4):
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
            page.wait_for_timeout(3500)
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


def _fill(page, label, say):
    """Map one field by walking the picker tree. 'ok' | 'prefilled' | 'missing'
    | 'failed'."""
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
    page.wait_for_timeout(1400)
    spec = MAPPING[label]
    if spec.get("filter"):
        page.keyboard.type(spec["filter"], delay=35)
        page.wait_for_timeout(1800)
    for step, xmin, xmax in spec["steps"]:
        if not _click_opt_scroll(page, step, xmin=xmin, xmax=xmax):
            # No Escape here: it closes the whole Configure panel, so every
            # later field then reports "field not in panel" and one bad step
            # cascades into a total failure.
            say(f"  {label}: step {step!r} not found")
            return "failed"
    page.wait_for_timeout(1200)
    got = (page.evaluate(_BOX, [label, CHIP_VOCAB]) or {}).get("chip")
    if got and EXPECTED[label] in got:
        say(f"  {label} = {got!r}")
        return "ok"
    say(f"  {label}: ended as {got!r} (wanted {EXPECTED[label]!r})")
    return "failed"


_FIND_OPT = """([txt, xmin, xmax, ymin, ymax])=>{
  const norm=s=>(s||'').replace(/\\s+/g,' ').trim();
  const leaves=[...document.querySelectorAll('*')].filter(e=>!e.children.length);
  for(const el of leaves){
    const r=el.getBoundingClientRect();
    if(r.width===0) continue;
    if(r.x<xmin||r.x>xmax||r.y<ymin||r.y>ymax) continue;
    if(norm(el.textContent)===txt)
      return {x:Math.round(r.x+r.width/2), y:Math.round(r.y+r.height/2)};
  }
  return null;
}"""


def _click_opt(page, txt, xmin=1290, xmax=1720, ymin=200, ymax=950, tries=10):
    for _ in range(tries):
        pt = page.evaluate(_FIND_OPT, [txt, xmin, xmax, ymin, ymax])
        if pt:
            page.mouse.click(pt["x"], pt["y"])
            page.wait_for_timeout(1100)
            return True
        page.wait_for_timeout(400)
    return False


def _click_opt_scroll(page, txt, xmin=1290, xmax=1720, tries=3):
    """_click_opt, but scroll the option list if the row is below the fold.
    'Enrich person' has 30 children, so leaves like 'org' start off-screen."""
    for attempt in range(tries):
        if _click_opt(page, txt, xmin=xmin, xmax=xmax, tries=4):
            return True
        page.mouse.move(1420, 600)
        page.mouse.wheel(0, 260 * (attempt + 1))
        page.wait_for_timeout(900)
    return _click_opt(page, txt, xmin=xmin, xmax=xmax, tries=4)


def apply_template(page, entry, dry_run, say, recon=False, run_after=False):
    wid, name = entry["workbook_id"], entry["workbook_name"]
    clay_ui.open_workbook_by_id(page, wid)
    if not B.table_exists(page, TABLE):
        say(f"SKIP {name}/{TABLE}: table does not exist")
        return {"workbook_id": wid, "workbook_name": name, "status": "no_table"}
    B.focus_table_maybe_empty(page, TABLE)
    page.wait_for_timeout(900)

    _open_template_retry(page)
    say(f"  template: {TEMPLATE_USED!r}")

    if recon:
        say(f"  fields: {chips(page)}")
        page.keyboard.press("Escape")
        return {"workbook_id": wid, "workbook_name": name, "status": "recon",
                "fields": chips(page)}

    statuses = {label: _fill(page, label, say) for label in MAPPING}
    final = chips(page)
    say(f"  mapped: {final}")

    bad = {k: v for k, v in final.items()
           if k in EXPECTED and not (v and EXPECTED[k] in v)}
    if bad:
        say(f"ABORT {name}/{TABLE}: mapping incomplete {bad}")
        page.keyboard.press("Escape")
        return {"workbook_id": wid, "workbook_name": name, "status": "aborted",
                "reason": "mapping_incomplete", "fields": final,
                "statuses": statuses}

    if dry_run:
        say(f"DRYRUN {name}/{TABLE}: mapping complete, not saving")
        page.keyboard.press("Escape")
        return {"workbook_id": wid, "workbook_name": name, "status": "dryrun",
                "fields": final, "statuses": statuses}

    opt = r"Save and run.*in this view" if run_after else r"Save and don'?t run"
    try:
        B.save_via_menu(page, opt)
        saved = opt
    except Exception:
        saved = W.save_column(page, say)
    say(f"  saved via {saved}")
    page.wait_for_timeout(6000)
    return {"workbook_id": wid, "workbook_name": name, "status": "ok",
            "saved": saved, "fields": final, "statuses": statuses,
            "template": TEMPLATE_USED}


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("workbook_id")
    ap.add_argument("workbook_name")
    ap.add_argument("--recon", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--run", action="store_true",
                    help="use 'Save and run in this view' instead of not running")
    ap.add_argument("--headed", action="store_true")
    a = ap.parse_args()
    entry = {"workbook_id": a.workbook_id, "workbook_name": a.workbook_name}
    with common.clay_page(headless=not a.headed) as page:
        def say(m):
            print(m, flush=True)
        print("\nRESULT:", apply_template(page, entry, a.dry_run, say,
                                          recon=a.recon, run_after=a.run))
