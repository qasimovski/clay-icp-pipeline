"""Apply the "Companies - Supabase" template to the Companies table of one
Product & Services workbook.

Per the user (2026-08-01):
  * the API account must be set to "Qasim - Labs" (a dropdown),
  * the Name field must be set to the table's own "Name" column,
  * other fields may already be filled — leave those alone.

Scope: tables named exactly "Companies" in the Product & Services folder
(product_services_companies.json). Nothing else is reachable.

  python apply_companies_supabase.py <workbook_name> --recon
  python apply_companies_supabase.py <workbook_name> --dry-run
  python apply_companies_supabase.py <workbook_name> [--run]
"""

import argparse
import json
import os
import subprocess
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
AUTO_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, SCRIPT_DIR)
sys.path.insert(0, os.path.join(AUTO_DIR, "clay_sync"))
sys.path.insert(0, os.path.join(AUTO_DIR, "build_automation"))

import clay_ui        # noqa: E402
import browser_session         # noqa: E402
import column_config as colcfg  # noqa: E402
import add_workemail_waterfall as panel  # noqa: E402

AUDIT = os.path.join(SCRIPT_DIR, "product_services_companies.json")
COLS_SCRIPT = os.path.join(SCRIPT_DIR, "check_table_columns.py")
TABLE = "Companies"
TEMPLATE = os.environ.get("CLAY_SUPABASE_TEMPLATE", "Companies - Supabase")
API_ACCOUNT = "Qasim - Labs"

PANEL = """()=>{
  const norm=s=>(s||'').replace(/\\s+/g,' ').trim();
  const out=[];
  for(const el of document.querySelectorAll('*')){
    if(el.children.length) continue;
    const t=norm(el.textContent);
    if(!t) continue;
    const r=el.getBoundingClientRect();
    if(r.width===0||r.x<1240||r.y<80||r.y>960) continue;
    out.push({t:t.slice(0,60), x:Math.round(r.x), y:Math.round(r.y)});
  }
  out.sort((a,b)=>a.y-b.y||a.x-b.x);
  return out;
}"""

_FIND_ROW = """([txt, xmin, xmax])=>{
  const norm=s=>(s||'').replace(/\\s+/g,' ').trim();
  for(const el of document.querySelectorAll('*')){
    if(el.children.length) continue;
    const r=el.getBoundingClientRect();
    if(r.width===0||r.x<xmin||r.x>xmax||r.y<150||r.y>1000) continue;
    if(norm(el.textContent)===txt)
      return {x:Math.round(r.x+15), y:Math.round(r.y+r.height/2)};
  }
  return null;
}"""

_BOX = """([label, vocab])=>{
  const norm=s=>(s||'').replace(/\\s+/g,' ').trim();
  const leaves=[...document.querySelectorAll('*')].filter(e=>!e.children.length);
  let ly=null;
  for(const el of leaves){
    const r=el.getBoundingClientRect();
    if(r.width===0||r.x<1255||r.x>1400||r.y<180||r.y>920) continue;
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
    if(!vocab.length||vocab.includes(t)) parts.push(t);
  }
  if(box) return box;
  return {empty:false, chip:(parts[0] || None_)};
}""".replace("None_", "null")


_FIND_TEXT = """(txt)=>{
  // Do NOT require a leaf: this label is split across child nodes, so a
  // leaf-only scan finds nothing even though the text is plainly on screen.
  // Take the SMALLEST element whose text contains it.
  const norm=s=>(s||'').replace(/\s+/g,' ').trim();
  let best=null, bestArea=1e12;
  for(const el of document.querySelectorAll('*')){
    const t=norm(el.textContent);
    if(!t||t.length>80||!t.includes(txt)) continue;
    const r=el.getBoundingClientRect();
    if(r.width===0||r.height===0) continue;
    const area=r.width*r.height;
    if(area<bestArea){bestArea=area; best={x:Math.round(r.x+r.width/2),
                                          y:Math.round(r.y+r.height/2)};}
  }
  return best;
}"""


def _click_text(page, txt):
    """Click by coordinates. The element can be present but fail Playwright's
    actionability checks (it sits in a panel that never reports stable), which
    made every locator click on "View all enrichments" time out."""
    pt = page.evaluate(_FIND_TEXT, txt)
    if not pt:
        return False
    page.mouse.click(pt["x"], pt["y"])
    page.wait_for_timeout(2000)
    return True


def _templates_tab(page):
    try:
        page.get_by_role("tab", name="Templates").first.click(timeout=8000)
    except Exception:
        # same actionability problem as the entry point — click by coordinates
        if not _click_text(page, "Templates"):
            raise clay_ui.ClayUIError("no Templates tab")
    page.wait_for_timeout(1800)
    you = page.get_by_role("radio", name="You")
    if not you.count():
        you = page.get_by_text("You", exact=True)
    try:
        you.first.click(timeout=8000)
        page.wait_for_timeout(1200)
    except Exception:
        pass


def open_template(page, say):
    """Open the template's Configure panel.

    Two entry points, because "View all enrichments" is not always present:
    fall back to Add column -> Add enrichment, which is the same picker.
    """
    last = None
    for attempt in range(4):
        try:
            # "View all enrichments" is the entry point that reaches the
            # Templates tab. Ctrl+E opens a DIFFERENT panel (the column editor)
            # which has no Templates tab, so do not use it here; alternate with
            # Add column -> Add enrichment instead.
            if attempt % 2 == 0:
                if not _click_text(page, "View all enrichments"):
                    raise clay_ui.ClayUIError("no 'View all enrichments' text")
            else:
                colcfg.open_enrichment_search(page, TEMPLATE)
            page.wait_for_timeout(2200)
            _templates_tab(page)
            cand = page.get_by_text(TEMPLATE, exact=True)
            for _ in range(5):
                if cand.count() and cand.first.is_visible():
                    break
                sb = page.get_by_placeholder("Search for a data point or provider")
                try:
                    if sb.count():
                        sb.first.fill(TEMPLATE)
                except Exception:
                    pass
                page.wait_for_timeout(1500)
                cand = page.get_by_text(TEMPLATE, exact=True)
            if not (cand.count() and cand.first.is_visible()):
                raise clay_ui.ClayUIError(f"template {TEMPLATE!r} not in picker")
            cand.first.click(timeout=20000)
            page.wait_for_timeout(4500)
            if not page.get_by_text("Configure", exact=True).count():
                raise clay_ui.ClayUIError("config panel did not open")
            return
        except Exception as e:
            last = e
            say(f"  open attempt {attempt+1} failed: {str(e)[:80]}")
            try:
                page.keyboard.press("Escape")
            except Exception:
                pass
            page.wait_for_timeout(2500)
    raise clay_ui.ClayUIError(f"could not open {TEMPLATE!r}: {last}")


_ROW_ICONS = """()=>{
  const out=[];
  for(const b of document.querySelectorAll('button,[role="button"]')){
    const r=b.getBoundingClientRect();
    if(r.width===0||r.x<1600||r.x>1700||r.y<430||r.y>570) continue;
    out.push({x:Math.round(r.x+r.width/2), y:Math.round(r.y+r.height/2)});
  }
  out.sort((a,b)=>a.x-b.x);
  return out;
}"""

# The account modal is centred (x ~600-1150). Its select box shows the current
# account; the options list appears just below it.
# Scope strictly to the modal's DOM subtree: the dialog is centred over the
# grid, so an x/y window also captures Industry/Location cells behind it (that
# is how the "current account" read as "Research").
_MODAL = """()=>{
  const norm=s=>(s||'').replace(/\s+/g,' ').trim();
  let head=null;
  for(const el of document.querySelectorAll('*')){
    if(el.children.length) continue;
    if(norm(el.textContent)==='Select HTTP API (Headers) account'){head=el;break;}
  }
  if(!head) return {open:false, current:null, rows:[]};
  let root=head;
  for(let i=0;i<8&&root.parentElement;i++){
    root=root.parentElement;
    const r=root.getBoundingClientRect();
    if(r.width>380&&r.height>120) break;
  }
  const rows=[];
  for(const el of root.querySelectorAll('*')){
    if(el.children.length) continue;
    const r=el.getBoundingClientRect();
    if(r.width===0) continue;
    const t=norm(el.textContent);
    if(!t||t.length>60) continue;
    if(t==='Select HTTP API (Headers) account'||t==='Manage accounts') continue;
    if(t==='Cancel'||t==='Continue') continue;
    rows.push({t, x:Math.round(r.x), y:Math.round(r.y+r.height/2),
               w:Math.round(r.width)});
  }
  rows.sort((a,b)=>a.y-b.y);
  return {open:true, current:(rows.length?rows[0].t:null), rows};
}"""


def set_api_account(page, say):
    """Set the provider's API account to Qasim - Labs.

    The account lives behind the KEY icon on the "HTTP API (Headers)" provider
    row, which opens a "Select HTTP API (Headers) account" modal. On a fresh
    apply it defaults to another account ("Data Cloud and Clay" on Material
    Sciences), so this must be set explicitly every time.
    """
    icons = page.evaluate(_ROW_ICONS)
    if not icons:
        say("  !! no provider-row icons found")
        return "missing"
    page.mouse.click(icons[0]["x"], icons[0]["y"])   # key icon
    page.wait_for_timeout(2500)
    m = page.evaluate(_MODAL)
    if not m.get("open"):
        say("  !! account modal did not open")
        return "missing"
    say(f"  account modal open, current={m.get('current')!r}")

    if m.get("current") != API_ACCOUNT:
        # open the dropdown and pick the right account
        box = m["rows"][0]
        page.mouse.click(box["x"] + 120, box["y"])
        page.wait_for_timeout(1800)
        pt = page.evaluate(_FIND_ROW, [API_ACCOUNT, 600, 1200])
        if not pt:
            say(f"  !! {API_ACCOUNT!r} not in the account list")
            return "failed"
        page.mouse.click(pt["x"], pt["y"])
        page.wait_for_timeout(1800)
        m = page.evaluate(_MODAL)
        if m.get("current") != API_ACCOUNT:
            say(f"  !! account still reads {m.get('current')!r}")
            return "failed"
    say(f"  API account = {API_ACCOUNT!r}")

    for label in ("Continue", "Save", "Done"):
        try:
            btn = page.get_by_role("button", name=label, exact=True)
            if btn.count():
                btn.first.click(timeout=8000)
                page.wait_for_timeout(2500)
                say(f"  confirmed account modal via {label!r}")
                return "ok"
        except Exception:
            pass
    if _click_text(page, "Continue"):
        say("  confirmed account modal via Continue (coords)")
        return "ok"
    say("  !! could not confirm the account modal")
    return "failed"


def set_name_field(page, say):
    info = None
    for _ in range(10):
        info = page.evaluate(_BOX, ["Name", ["Name"]])
        if info:
            break
        page.wait_for_timeout(800)
    if not info:
        say("  Name: field not in panel")
        return "missing"
    if not info.get("empty"):
        say(f"  Name: already set to {info.get('chip')!r}")
        return "prefilled"
    page.mouse.click(info["x"], info["y"])
    page.wait_for_timeout(1800)
    page.keyboard.type("Name", delay=40)
    page.wait_for_timeout(1800)
    for _ in range(6):
        pt = page.evaluate(_FIND_ROW, ["Name", 1290, 1400])
        if pt:
            page.mouse.click(pt["x"], pt["y"])
            page.wait_for_timeout(2200)
            break
        page.wait_for_timeout(1200)
    got = (page.evaluate(_BOX, ["Name", ["Name"]]) or {}).get("chip")
    say(f"  Name = {got!r}")
    return "ok" if got == "Name" else "failed"


def already_applied(table_id, marker, say):
    """Fail-closed check against Clay's real column list."""
    if not table_id or not marker:
        return False
    path = COLS_SCRIPT.replace("C:\\", "/mnt/c/").replace("\\", "/")
    try:
        out = subprocess.run(["wsl", "-d", "Ubuntu", "--", "python3", path,
                              table_id], capture_output=True, text=True,
                             timeout=180,
                             env={**os.environ, "MSYS_NO_PATHCONV": "1"})
        cols = (out.stdout or "").strip().splitlines()
    except Exception as e:
        say(f"  !! column pre-check failed ({str(e)[:60]}) — skipping for safety")
        return True
    if not cols:
        say("  !! column pre-check empty — skipping for safety")
        return True
    hit = [c for c in marker if c in cols]
    if hit:
        say(f"  already applied (has {hit})")
        return True
    return False


def apply_supabase(page, entry, dry_run, say, recon=False, run_after=False,
                   marker=()):
    wid, name = entry["workbook_id"], entry["workbook_name"]
    clay_ui.open_workbook_by_id(page, wid)
    if not colcfg.table_exists(page, TABLE):
        say(f"SKIP {name}: no {TABLE!r} table")
        return {"workbook_name": name, "status": "no_table"}
    colcfg.focus_table_maybe_empty(page, TABLE)
    page.wait_for_timeout(1200)

    if not (recon or dry_run) and already_applied(entry.get("table_id"), marker, say):
        return {"workbook_name": name, "status": "already_applied"}

    open_template(page, say)

    if recon:
        rows = page.evaluate(PANEL)
        say(f"RECON {name}: panel contents")
        for r in rows:
            say(f"   x={r['x']:<5} y={r['y']:<5} {r['t']!r}")
        page.keyboard.press("Escape")
        return {"workbook_name": name, "status": "recon",
                "panel": [r["t"] for r in rows]}

    api = set_api_account(page, say)
    nm = set_name_field(page, say)
    if api not in ("ok", "prefilled") or nm not in ("ok", "prefilled"):
        say(f"ABORT {name}: api={api} name={nm}")
        page.keyboard.press("Escape")
        return {"workbook_name": name, "status": "aborted", "api": api,
                "name": nm}

    if dry_run:
        say(f"DRYRUN {name}: api={api} name={nm}; not saving")
        page.keyboard.press("Escape")
        return {"workbook_name": name, "status": "dryrun", "api": api,
                "name": nm}

    opt = r"Save and run.*in this view" if run_after else r"Save and don'?t run"
    try:
        colcfg.save_via_menu(page, opt)
        saved = "run" if run_after else "no_run"
    except Exception:
        saved = panel.save_column(page, say)
    say(f"  saved via {saved}")
    page.wait_for_timeout(6000)
    return {"workbook_name": name, "status": "ok", "saved": saved, "api": api,
            "name": nm}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("workbook_name")
    ap.add_argument("--recon", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--marker", nargs="*", default=[],
                    help="column names proving the template is already applied")
    ap.add_argument("--headed", action="store_true")
    a = ap.parse_args()

    audit = json.load(open(AUDIT, encoding="utf-8"))
    if a.workbook_name not in audit:
        raise SystemExit(f"{a.workbook_name!r} has no Companies table in scope")
    rec = audit[a.workbook_name]
    entry = {"workbook_id": rec["workbook_id"], "workbook_name": a.workbook_name,
             "table_id": rec.get("table_id")}
    with browser_session.clay_page(headless=not a.headed) as page:
        def say(m):
            print(m, flush=True)
        print("\nRESULT:", apply_supabase(page, entry, a.dry_run, say,
                                          recon=a.recon, run_after=a.run,
                                          marker=tuple(a.marker)))
