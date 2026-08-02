"""Apply "Google Sheets - Companies Lookup and Add Row" to the Companies table
of one Product & Services workbook.

Recon (Material Sciences, 2026-07-31) — the Configure panel has just two fields:
  Domain   pre-filled with the table's own "Domain" column   -> leave
  Name     EMPTY -> the table's own "Name" column            -> set

Creates "Lookup row" + "Add row" (matching the user's finished Chemicals &
Reagents reference), which are therefore the "already applied" signature.

Scope is restricted to tables named exactly "Companies" inside the Product &
Services folder (see product_services_companies.json, built from the folder
listing) — nothing else can be reached.

  python apply_companies_lookup_event.py <workbook_name> --recon
  python apply_companies_lookup_event.py <workbook_name> --dry-run
  python apply_companies_lookup_event.py <workbook_name> [--run]
"""

import argparse
import json
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
AUTO_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, SCRIPT_DIR)
sys.path.insert(0, os.path.join(AUTO_DIR, "clay_sync"))
sys.path.insert(0, os.path.join(AUTO_DIR, "build_automation"))

import clay_ui        # noqa: E402
import common         # noqa: E402
import build_lib as B  # noqa: E402
import add_workemail_waterfall_event as W  # noqa: E402  (save/menu helpers)
import subprocess  # noqa: E402

AUDIT = os.path.join(SCRIPT_DIR, "product_services_companies.json")
TABLE = "Companies"          # the ONLY table name this pass may touch
TEMPLATE_CANDIDATES = [n for n in (
    os.environ.get("CLAY_COMPANIES_TEMPLATE"),
    "Google Sheets - Companies Lookup and Add Row",
) if n]
TEMPLATE_USED = None
SIG = ("Lookup row", "Add row")     # columns the template adds

# Only Name needs setting; Domain arrives mapped.
FIELDS = {"Name": "Name"}
EXPECTED = {"Domain": "Domain", "Name": "Name"}
CHIP_VOCAB = ["Name", "Domain"]

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
    if(r.width===0||r.x<1265||r.x>1345||r.y<ly+8||r.y>ly+60) continue;
    const t=norm(el.textContent);
    if(!t) continue;
    if(t.includes('Start typing')){
      box={x:Math.round(r.x+30), y:Math.round(r.y+r.height/2), empty:true};
      continue;
    }
    if(vocab.includes(t)) parts.push(t);
  }
  if(box) return box;
  return {empty:false, chip:(parts[0] || null)};
}"""

_FIND_ROW = """([txt, xmin, xmax])=>{
  const norm=s=>(s||'').replace(/\\s+/g,' ').trim();
  for(const el of document.querySelectorAll('*')){
    if(el.children.length) continue;
    const r=el.getBoundingClientRect();
    if(r.width===0||r.x<xmin||r.x>xmax||r.y<180||r.y>1000) continue;
    if(norm(el.textContent)===txt)
      return {x:Math.round(r.x+15), y:Math.round(r.y+r.height/2)};
  }
  return null;
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
            _open_template(page)
            return
        except Exception as e:
            last = e
            try:
                page.keyboard.press("Escape"); page.wait_for_timeout(2500)
            except Exception:
                pass
    raise clay_ui.ClayUIError(f"could not open template after {tries}: {last}")


def _fill(page, label, column, say):
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
    page.wait_for_timeout(1800)
    page.keyboard.type(column, delay=40)
    page.wait_for_timeout(2000)
    for attempt in range(6):
        pt = page.evaluate(_FIND_ROW, [column, 1290, 1400])
        if pt:
            page.mouse.click(pt["x"], pt["y"])
            page.wait_for_timeout(2500)
            break
        page.wait_for_timeout(1200)
    else:
        say(f"  {label}: option {column!r} not found")
        return "failed"
    got = (page.evaluate(_BOX, [label, CHIP_VOCAB]) or {}).get("chip")
    if got == column:
        say(f"  {label} = {got!r}")
        return "ok"
    say(f"  {label}: ended as {got!r} (wanted {column!r})")
    return "failed"


COLS_SCRIPT = os.path.join(SCRIPT_DIR, "check_table_columns.py")


def already_applied(table_id, say):
    """Ask Clay whether this Companies table already has the template's columns.

    Fail-closed: if the check cannot answer, treat it as applied rather than risk
    a duplicate set of Lookup row / Add row columns.
    """
    if not table_id:
        return True
    path = COLS_SCRIPT.replace("C:\\", "/mnt/c/").replace("\\", "/")
    try:
        out = subprocess.run(["wsl", "-d", "Ubuntu", "--", "python3", path,
                              table_id], capture_output=True, text=True,
                             timeout=180,
                             env={**os.environ, "MSYS_NO_PATHCONV": "1"})
        cols = (out.stdout or "").strip().splitlines()
    except Exception as e:
        say(f"  !! column pre-check failed ({str(e)[:70]}) — skipping for safety")
        return True
    if not cols:
        say("  !! column pre-check returned nothing — skipping for safety")
        return True
    hit = [c for c in SIG if c in cols]
    if hit:
        say(f"  already applied (has {hit}) — not applying again")
        return True
    say(f"  {len(cols)} columns, none of {list(SIG)} present")
    return False


def apply_lookup(page, entry, dry_run, say, recon=False, run_after=False):
    wid, name = entry["workbook_id"], entry["workbook_name"]
    clay_ui.open_workbook_by_id(page, wid)
    if not B.table_exists(page, TABLE):
        say(f"SKIP {name}: no {TABLE!r} table")
        return {"workbook_name": name, "status": "no_table"}
    B.focus_table_maybe_empty(page, TABLE)
    page.wait_for_timeout(1200)

    if not (recon or dry_run) and already_applied(entry.get("table_id"), say):
        return {"workbook_name": name, "status": "already_applied"}

    _open_template_retry(page)
    say(f"  template: {TEMPLATE_USED!r}")

    if recon:
        c = chips(page)
        say(f"  fields: {c}")
        page.keyboard.press("Escape")
        return {"workbook_name": name, "status": "recon", "fields": c}

    statuses = {lab: _fill(page, lab, col, say) for lab, col in FIELDS.items()}
    final = chips(page)
    say(f"  mapped: {final}")
    bad = {k: v for k, v in final.items() if v != EXPECTED[k]}
    if bad:
        say(f"ABORT {name}: mapping incomplete {bad}")
        page.keyboard.press("Escape")
        return {"workbook_name": name, "status": "aborted",
                "reason": "mapping_incomplete", "fields": final}

    if dry_run:
        say(f"DRYRUN {name}: mapping complete, not saving")
        page.keyboard.press("Escape")
        return {"workbook_name": name, "status": "dryrun", "fields": final,
                "statuses": statuses}

    opt = r"Save and run.*in this view" if run_after else r"Save and don'?t run"
    try:
        B.save_via_menu(page, opt)
        saved = "run" if run_after else "no_run"
    except Exception:
        saved = W.save_column(page, say)
    say(f"  saved via {saved}")
    page.wait_for_timeout(6000)
    return {"workbook_name": name, "status": "ok", "saved": saved,
            "fields": final, "statuses": statuses, "template": TEMPLATE_USED}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("workbook_name")
    ap.add_argument("--recon", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--run", action="store_true",
                    help="Save and run in this view")
    ap.add_argument("--headed", action="store_true")
    a = ap.parse_args()

    audit = json.load(open(AUDIT, encoding="utf-8"))
    if a.workbook_name not in audit:
        raise SystemExit(f"{a.workbook_name!r} has no Companies table in the "
                         f"Product & Services audit")
    rec = audit[a.workbook_name]
    entry = {"workbook_id": rec["workbook_id"], "workbook_name": a.workbook_name,
             "table_id": rec.get("table_id")}
    with common.clay_page(headless=not a.headed) as page:
        def say(m):
            print(m, flush=True)
        print("\nRESULT:", apply_lookup(page, entry, a.dry_run, say,
                                        recon=a.recon, run_after=a.run))
