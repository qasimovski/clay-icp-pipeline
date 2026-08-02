"""Per-workbook pass over Product & Services Companies tables:

  1. filter the Companies view to `Is New` is checked,
  2. run "Find people at these companies" -> creates a People table,
  3. paste that workbook's Clay search query (query mode) into the People table.

Queries come from a --queries file, passed in and never stored in this repo: they
are written per group of workbooks (not part of the uniform process) and each one
carries its own `clay.filter_to_companies(@table("t_...:gv_...:f_..."))` reference
to a live table/view/filter. The file is read verbatim — queries are never
rewritten here. Format is repeated blocks of:

    <Workbook name>:

    select from people
    where ...

Excluded by instruction: Lab Equipment & Instrumentation Suppliers,
Testing & Diagnostics (and anything not present in the filters file).

  python people_search_event.py "Material Sciences" --filter-only
  python people_search_event.py "Material Sciences" --queries <file>
"""

import argparse
import json
import os
import re
import subprocess
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
AUTO_DIR = os.path.dirname(SCRIPT_DIR)
REPO = os.path.dirname(AUTO_DIR)
sys.path.insert(0, SCRIPT_DIR)
sys.path.insert(0, os.path.join(AUTO_DIR, "clay_sync"))
sys.path.insert(0, os.path.join(AUTO_DIR, "build_automation"))

import clay_ui        # noqa: E402
import common         # noqa: E402
import build_lib as B  # noqa: E402

AUDIT = os.path.join(SCRIPT_DIR, "product_services_companies.json")
TABLE = "Companies"
LEAVE_ALONE = {"Lab Equipment & Instrumentation Suppliers", "Testing & Diagnostics"}

# smallest element containing the text (labels are split across child nodes)
FIND = """(txt)=>{
  const norm=s=>(s||'').replace(/\\s+/g,' ').trim();
  let best=null, bestArea=1e12;
  for(const el of document.querySelectorAll('*')){
    const t=norm(el.textContent);
    if(!t||t.length>60||!t.includes(txt)) continue;
    const r=el.getBoundingClientRect();
    if(r.width===0||r.height===0) continue;
    const a=r.width*r.height;
    if(a<bestArea){bestArea=a; best={t, x:Math.round(r.x+r.width/2),
                                     y:Math.round(r.y+r.height/2)};}
  }
  return best;
}"""

# the toolbar rows chip — the filter funnel sits just to its right. On narrow
# layouts Clay drops the words, leaving bare "1,140/1,140" next to the columns
# chip "14/16", so take the RIGHTMOST such chip.
ROWS_ANCHOR = """()=>{
  const norm=s=>(s||'').replace(/\\s+/g,' ').trim();
  let best=null;
  for(const el of document.querySelectorAll('*')){
    if(el.children.length) continue;
    const t=norm(el.textContent);
    if(!/^[\\d.,KM]+\\/[\\d.,KM]+( rows)?$/.test(t)) continue;
    const r=el.getBoundingClientRect();
    if(r.y<85||r.y>120) continue;
    if(!best||r.right>best.right)
      best={t, right:Math.round(r.right), y:Math.round(r.y+r.height/2)};
  }
  return best;
}"""

# An exact-text option inside the open column dropdown. The popover is anchored
# to the funnel, so its x varies per table (it can sit at the far left) — bound
# the search to the popover's own position, never to fixed screen coordinates.
OPTION = """([txt, ymin, xmin, xmax])=>{
  const norm=s=>(s||'').replace(/\\s+/g,' ').trim();
  for(const el of document.querySelectorAll('*')){
    if(el.children.length) continue;
    const r=el.getBoundingClientRect();
    if(r.width===0||r.x<xmin||r.x>xmax||r.y<ymin) continue;
    if(norm(el.textContent)===txt)
      return {x:Math.round(r.x+30), y:Math.round(r.y+r.height/2)};
  }
  return null;
}"""

# The filter row: column chip + operator. The column chip is NOT a leaf (icon +
# label), and the grid header sits only ~18px below it — so scan by the row's own
# band and take the smallest element per x position.
FILTER_ROW = """()=>{
  const norm=s=>(s||'').replace(/\\s+/g,' ').trim();
  const best=new Map();
  for(const el of document.querySelectorAll('*')){
    const t=norm(el.textContent);
    if(!t||t.length>45) continue;
    const r=el.getBoundingClientRect();
    if(r.width===0||r.height===0) continue;
    const cy=r.y+r.height/2;
    if(cy<138||cy>168||r.x<180||r.x>1100) continue;
    const k=Math.round(r.x/20);
    const a=r.width*r.height;
    if(!best.has(k)||a<best.get(k).a)
      best.set(k, {t, a, x:Math.round(r.x+r.width/2)});
  }
  return [...best.values()].sort((p,q)=>p.x-q.x).map(v=>({t:v.t, x:v.x}));
}"""


def load_queries(path):
    """Parse "<Workbook>:\n\n<query>" blocks out of the filters file."""
    text = open(path, encoding="utf-8").read()
    blocks, name, buf = {}, None, []
    for line in text.splitlines():
        m = re.match(r"^([A-Za-z][^:]{2,70}):\s*$", line)
        if m and not line.startswith(" "):
            if name and "".join(buf).strip():
                blocks[name] = "\n".join(buf).strip()
            name, buf = m.group(1).strip(), []
        elif name is not None:
            buf.append(line)
    if name and "".join(buf).strip():
        blocks[name] = "\n".join(buf).strip()
    return blocks


def open_filter_popover(page, say):
    a = None
    for _ in range(10):          # the toolbar chip renders late on a cold load
        a = page.evaluate(ROWS_ANCHOR)
        if a:
            break
        page.wait_for_timeout(1500)
    if not a:
        raise B.GateError("toolbar 'N/N rows' anchor not found")
    say(f"  rows chip: {a['t']}")
    for attempt in range(4):
        page.mouse.click(a["right"] + 32, a["y"])
        page.wait_for_timeout(2200)
        # the popover is open only once its controls are on screen
        if page.evaluate(FIND, "Add filter"):
            return a
        say(f"  filter popover did not open (attempt {attempt + 1})")
        page.keyboard.press("Escape")
        page.wait_for_timeout(1200)
    raise B.GateError("filter popover would not open")


def read_filter_row(page):
    return [r["t"] for r in page.evaluate(FILTER_ROW)]


# The chip inside the OPEN filter popover. Bounded tightly on purpose: the grid
# header row sits at y~171 and carries a column literally named "Is New", so a
# loose band matches the header and falsely reports the filter as applied.
CHIP = """(name)=>{
  const norm=s=>(s||'').replace(/\\s+/g,' ').trim();
  for(const el of document.querySelectorAll('*')){
    const t=norm(el.textContent);
    if(!t||t.length>45||!t.includes(name)) continue;
    const r=el.getBoundingClientRect();
    if(r.width===0||r.width>400) continue;
    const cy=r.y+r.height/2;
    if(cy<140||cy>166||r.x<180||r.x>1100) continue;
    return {t, x:Math.round(r.x), y:Math.round(cy), w:Math.round(r.width)};
  }
  return null;
}"""


def filter_count(page):
    """How many filters the view has, per the badge printed next to the funnel.

    The filter chip's column label is not present in the DOM (no text, aria,
    title or input value), so the badge — plus the row count — is the only
    readable signal that a filter is applied.
    """
    a = page.evaluate(ROWS_ANCHOR)
    if not a:
        return 0
    return page.evaluate("""(x0)=>{
      const norm=s=>(s||'').replace(/\\s+/g,' ').trim();
      for(const el of document.querySelectorAll('*')){
        if(el.children.length) continue;
        const r=el.getBoundingClientRect();
        if(r.width===0||r.y<86||r.y>120) continue;
        if(r.x<x0+40||r.x>x0+90) continue;
        const t=norm(el.textContent);
        if(/^\\d+$/.test(t)) return parseInt(t,10);
      }
      return 0;
    }""", a["right"])


def chip_is(page, name):
    """True when the open popover's filter row names this column."""
    for _ in range(5):
        hit = page.evaluate(CHIP, name)
        if hit:
            return True
        page.wait_for_timeout(700)
    return False


def apply_is_new_filter(page, say):
    """Filter the view to `Is New` is checked. Idempotent: if a filter on Is New
    already exists, leave it alone."""
    before = open_filter_popover(page, say)
    page.wait_for_timeout(1200)
    n = filter_count(page)
    if n:
        say(f"  view already has {n} filter(s) — leaving it alone")
        page.keyboard.press("Escape")
        return "already"
    say(f"  existing filter row: {read_filter_row(page)}")

    add = page.evaluate(FIND, "Add filter")
    if not add:
        raise B.GateError("'Add filter' not found")
    page.mouse.click(add["x"], add["y"])
    page.wait_for_timeout(2500)

    where = page.evaluate(FIND, "Where")
    page.mouse.click(where["x"] + 180, where["y"])   # the column dropdown
    page.wait_for_timeout(2000)
    # tables with many columns push "Is New" below the dropdown's fold
    opt = None
    for i in range(10):
        opt = page.evaluate(OPTION, ["Is New", where["y"] + 20,
                                     where["x"] - 220, where["x"] + 800])
        if opt:
            break
        if i:
            page.mouse.move(500, where["y"] + 220)
            page.mouse.wheel(0, 240)
        page.wait_for_timeout(900)
    if not opt:
        page.keyboard.press("Escape")
        raise B.GateError("'Is New' not in the column dropdown")
    page.mouse.click(opt["x"], opt["y"])
    page.wait_for_timeout(3000)

    row = read_filter_row(page)
    say(f"  filter row now: {row}")
    page.keyboard.press("Escape")
    page.wait_for_timeout(3000)
    after = page.evaluate(ROWS_ANCHOR)
    n = filter_count(page)
    say(f"  rows {before['t']} -> {after['t'] if after else '?'}, filters={n}")
    ok = n == 1 and any("checked" in t for t in row)
    if ok and after and after["t"] == before["t"]:
        say("  note: row count unchanged — every row has Is New checked")
    return "ok" if ok else "unverified"


TOOLS_BTN = (1672, 102)

# The workbook's table tabs along the bottom bar. The last grid row can sit in
# the same band, so require a real tab/button element — plain leaf text there is
# grid content, not a tab.
TABS = """()=>{
  const norm=s=>(s||'').replace(/\\s+/g,' ').trim();
  const out=[], seen=new Set();
  for(const b of document.querySelectorAll('button,[role="tab"],a')){
    const r=b.getBoundingClientRect();
    if(r.width===0||r.y<935||r.y>980||r.x<50||r.x>900) continue;
    const t=norm(b.textContent);
    if(!t||t.length>60||seen.has(t)) continue;
    seen.add(t);
    out.push({t, x:Math.round(r.x)});
  }
  out.sort((a,b)=>a.x-b.x);
  return out;
}"""

# tabs that exist before this pass runs; anything else means a people table is
# already there and the search must NOT be run again (it would duplicate)
BASE_TABS = {"Overview", "Companies", "Add", "Settings"}


TABLES_SCRIPT = os.path.join(SCRIPT_DIR, "list_workbook_tables.py")


def existing_people_tables(workbook_id, say):
    """Tables in the workbook other than Companies, per Clay's own list.

    Fail-closed: if the listing can't be read, report a table as present so a
    second "Find people" run can never duplicate one.
    """
    path = TABLES_SCRIPT.replace("C:\\", "/mnt/c/").replace("\\", "/")
    try:
        out = subprocess.run(["wsl", "-d", "Ubuntu", "--", "python3", path,
                              workbook_id], capture_output=True, text=True,
                             timeout=300,
                             env={**os.environ, "MSYS_NO_PATHCONV": "1"})
        rows = [r for r in (out.stdout or "").strip().splitlines() if "|" in r]
    except Exception as e:
        say(f"  !! table pre-check failed ({str(e)[:60]}) — treating as present")
        return ["<unknown>"]
    if not rows:
        say("  !! table pre-check empty — treating as present")
        return ["<unknown>"]
    return [r.split("|", 1)[1] for r in rows
            if r.split("|", 1)[1] != TABLE]

# a tab in the Tools sidebar header (Import / Signals / Enrich / ...)
TAB = """(txt)=>{
  const norm=s=>(s||'').replace(/\\s+/g,' ').trim();
  for(const el of document.querySelectorAll('*')){
    if(el.children.length) continue;
    const r=el.getBoundingClientRect();
    if(r.width===0||r.x<1150||r.y<200||r.y>235) continue;
    if(norm(el.textContent)===txt)
      return {x:Math.round(r.x+r.width/2), y:Math.round(r.y+r.height/2)};
  }
  return null;
}"""

# an entry in the Tools sidebar body, matched exactly (x>1250 keeps grid text out)
SIDEBAR_ITEM = """(txt)=>{
  const norm=s=>(s||'').replace(/\\s+/g,' ').trim();
  for(const el of document.querySelectorAll('*')){
    if(el.children.length) continue;
    const r=el.getBoundingClientRect();
    if(r.width===0||r.x<1250||r.x>1720||r.y<230||r.y>950) continue;
    if(norm(el.textContent)===txt)
      return {x:Math.round(r.x+r.width/2), y:Math.round(r.y+r.height/2)};
  }
  return null;
}"""

PANEL = """()=>{
  const norm=s=>(s||'').replace(/\\s+/g,' ').trim();
  const out=[], seen=new Set();
  for(const el of document.querySelectorAll('*')){
    if(el.children.length) continue;
    const t=norm(el.textContent);
    if(!t||t.length>80) continue;
    const r=el.getBoundingClientRect();
    if(r.width===0) continue;
    const k=t+'@'+Math.round(r.y)+','+Math.round(r.x);
    if(seen.has(k)) continue; seen.add(k);
    out.push({t, x:Math.round(r.x), y:Math.round(r.y)});
  }
  out.sort((a,b)=>a.y-b.y||a.x-b.x);
  return out;
}"""


def open_tools_import(page, say):
    """Tools sidebar -> Import tab."""
    tab = page.evaluate(TAB, "Import")
    if not tab:
        page.mouse.click(*TOOLS_BTN)
        page.wait_for_timeout(2500)
        tab = page.evaluate(TAB, "Import")
    if not tab:
        raise B.GateError("Tools sidebar 'Import' tab not found")
    page.mouse.click(tab["x"], tab["y"])
    page.wait_for_timeout(2500)
    say("  Tools > Import open")


def start_find_people(page, say):
    """Click "Find people at these companies" — creates a new People table."""
    open_tools_import(page, say)
    item = None
    for _ in range(6):
        item = page.evaluate(SIDEBAR_ITEM, "Find people at these companies")
        if item:
            break
        page.wait_for_timeout(900)
    if not item:
        raise B.GateError("'Find people at these companies' not in Import tab")
    say(f"  clicking Find people at these companies {item}")
    page.mouse.click(item["x"], item["y"])
    page.wait_for_timeout(6000)
    return True


# Clay's query editor is Ace; read/write through its own API rather than the DOM
ACE_GET = """()=>{
  const el=document.querySelector('.ace_editor');
  if(!el) return null;
  try { return el.env.editor.getValue(); } catch(e) { return 'ERR:'+e.message; }
}"""

PREVIEW = """()=>{
  const norm=s=>(s||'').replace(/\\s+/g,' ').trim();
  for(const el of document.querySelectorAll('*')){
    if(el.children.length) continue;
    const t=norm(el.textContent);
    if(/found\\)/.test(t)||/^\\d[\\d,.]* of /.test(t)) return t;
  }
  return null;
}"""


PEOPLE_NAME = "People"

# the rightmost breadcrumb leaf = the current table's name; its chevron sits just
# to the right of the text
CRUMB = """()=>{
  const norm=s=>(s||'').replace(/\\s+/g,' ').trim();
  let best=null;
  for(const el of document.querySelectorAll('*')){
    if(el.children.length) continue;
    const r=el.getBoundingClientRect();
    if(r.width===0||r.y<38||r.y>66) continue;
    const t=norm(el.textContent);
    if(!t||t==='/') continue;
    if(!best||r.x>best.x) best={t, x:Math.round(r.x),
                                right:Math.round(r.right),
                                y:Math.round(r.y+r.height/2)};
  }
  return best;
}"""


def rename_table(page, new_name, say):
    """Rename the currently open table via the breadcrumb menu."""
    c = page.evaluate(CRUMB)
    if not c:
        raise B.GateError("breadcrumb table name not found")
    say(f"  current table name: {c['t']!r}")
    if c["t"] == new_name:
        say("  already named correctly")
        return "already"
    page.mouse.click(c["right"] + 12, c["y"])
    page.wait_for_timeout(2000)
    item = page.evaluate(FIND, "Rename")
    if not item:
        page.keyboard.press("Escape")
        raise B.GateError("'Rename' not in the table menu")
    page.mouse.click(item["x"], item["y"])
    page.wait_for_timeout(1800)
    page.keyboard.press("Control+A")
    page.wait_for_timeout(300)
    page.keyboard.type(new_name, delay=45)
    page.wait_for_timeout(600)
    page.keyboard.press("Enter")
    page.wait_for_timeout(4000)
    got = (page.evaluate(CRUMB) or {}).get("t")
    say(f"  renamed to {got!r}")
    return "ok" if got == new_name else "failed"


def read_limits(page):
    """Result limits inputs (value vs. greyed placeholder) at the panel bottom."""
    return page.evaluate("""()=>{
      const out=[];
      for(const i of document.querySelectorAll('input')){
        const r=i.getBoundingClientRect();
        if(r.width===0||r.x>520||r.y<700) continue;
        out.push({y:Math.round(r.y), value:i.value,
                  placeholder:i.getAttribute('placeholder')||''});
      }
      return out;
    }""")


def enter_query_mode(page, say):
    """Filters panel -> the </> (Query mode) toggle."""
    for _ in range(4):
        page.mouse.click(122, 292)
        page.wait_for_timeout(2500)
        if page.evaluate(ACE_GET) is not None:
            say("  query mode on")
            return True
        page.wait_for_timeout(1200)
    raise B.GateError("could not switch the Filters panel to query mode")


def set_query(page, query, say):
    """Replace the editor's contents with the workbook's query, verbatim."""
    pre = page.evaluate(ACE_GET)
    say(f"  editor prefilled with {len(pre or '')} chars")
    page.mouse.click(277, 380)
    page.wait_for_timeout(800)
    page.keyboard.press("Control+A")
    page.wait_for_timeout(400)
    page.keyboard.press("Delete")
    page.wait_for_timeout(600)
    page.keyboard.insert_text(query)
    page.wait_for_timeout(4000)
    got = page.evaluate(ACE_GET) or ""
    same = " ".join(got.split()) == " ".join(query.split())
    say(f"  editor now {len(got)} chars; exact match: {same}")
    if not same:
        say(f"  !! editor content differs; starts {got[:80]!r}")
    return same


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("workbook")
    ap.add_argument("--queries",
                    help="file of per-workbook Clay search queries "
                         "(required unless --filter-only)")
    ap.add_argument("--filter-only", action="store_true")
    ap.add_argument("--recon-people", action="store_true")
    ap.add_argument("--force", action="store_true",
                    help="run even if a people table already exists")
    args = ap.parse_args()

    audit = json.load(open(AUDIT, encoding="utf-8"))
    if args.filter_only:
        queries = {}
    elif not args.queries:
        print("--queries <file> is required (see the module docstring for the "
              "expected format)")
        return
    else:
        queries = load_queries(args.queries)
    wb = args.workbook
    if wb in LEAVE_ALONE:
        print(f"REFUSING: {wb} is on the leave-alone list")
        return
    if wb not in audit:
        print(f"unknown workbook {wb!r}")
        return
    if not args.filter_only:
        if wb not in queries:
            print(f"no query for {wb!r} in {args.queries}")
            return
        print(f"{wb}: query is {len(queries[wb])} chars", flush=True)

    def say(m):
        print(m, flush=True)

    already = existing_people_tables(audit[wb]["workbook_id"], say)
    if already and not args.force:
        print(f"SKIP {wb}: people table already present {already} "
              f"(pass --force to override)")
        return

    with common.clay_page(headless=True) as page:
        clay_ui.open_workbook_by_id(page, audit[wb]["workbook_id"])
        B.focus_table_maybe_empty(page, TABLE)
        page.wait_for_timeout(3000)
        already = []
        if already and not args.force:
            say(f"SKIP {wb}: people table already present {already} "
                f"(pass --force to override)")
            return
        r = apply_is_new_filter(page, say)
        say(f"filter: {r}")
        if args.filter_only:
            return
        start_find_people(page, say)
        enter_query_mode(page, say)
        say(f"  preview before: {page.evaluate(PREVIEW)}")
        ok = set_query(page, queries[wb], say)
        say(f"query set: {ok}")
        page.wait_for_timeout(6000)
        say(f"  preview after: {page.evaluate(PREVIEW)}")
        shot = os.path.join(os.environ.get("CLAUDE_JOB_DIR", "."), "tmp",
                            "query_pasted.png")
        page.screenshot(path=shot)
        say(f"shot: {shot}")
        say(f"  result limits: {read_limits(page)}")
        if args.recon_people:
            return
        cont = page.evaluate(FIND, "Continue")
        say(f"  Continue: {cont}")
        page.mouse.click(cont["x"], cont["y"])
        page.wait_for_timeout(6000)
        shot2 = os.path.join(os.environ.get("CLAUDE_JOB_DIR", "."), "tmp",
                             "after_continue.png")
        page.screenshot(path=shot2)
        say(f"shot: {shot2}")
        newt = page.evaluate(FIND, "Save to new table")
        if not newt:
            raise B.GateError("'Save to new table' not offered after Continue")
        say(f"  clicking Save to new table {newt}")
        page.mouse.click(newt["x"], newt["y"])
        page.wait_for_timeout(20000)
        shot3 = os.path.join(os.environ.get("CLAUDE_JOB_DIR", "."), "tmp",
                             "new_people_table.png")
        page.screenshot(path=shot3)
        say(f"shot: {shot3}")
        say(f"  url: {page.url}")

        # Clay then offers optional enrichments ("Enrich people"). The user asked
        # only for the search — leave every box unchecked and just Save.
        save = page.evaluate(FIND, "Save")
        if save and save["y"] > 900:
            say(f"  Save (no enrichments selected): {save}")
            page.mouse.click(save["x"], save["y"])
            page.wait_for_timeout(20000)
        shot4 = os.path.join(os.environ.get("CLAUDE_JOB_DIR", "."), "tmp",
                             "people_table_saved.png")
        page.screenshot(path=shot4)
        say(f"shot: {shot4}")
        say(f"  url: {page.url}")
        a = page.evaluate(ROWS_ANCHOR)
        say(f"  new table rows: {a['t'] if a else '?'}")
        say(f"rename: {rename_table(page, PEOPLE_NAME, say)}")


if __name__ == "__main__":
    main()
