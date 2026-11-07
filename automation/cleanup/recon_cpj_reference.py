"""Read-only recon: on the user's finished reference build (ACHEMA / Sellers -
People, 2026-08-03), open each column the "Qasim - CPJ - Labs - People" template
created and dump what it is bound to.

Why this exists: the template's Configure panel exposes THIRTEEN inputs whose
labels are the source table's own column names (Size, Type, domain, LinkedIn,
Founded, Industry, Resolved Description, Specialities, Company Table Data,
Primary Offerings, Business Stage, Annual Revenue, Total Funding Amount Range
Usd) while it creates TWELVE columns (…, Sub Industry, …). Twelve of the
thirteen bind name-to-name onto a property under this table's "Company Table
Data" lookup, but the input labelled "Company Table Data" has no same-named
property, and the created "Sub Industry" column has no same-named input. The two
readings — bind it to the whole lookup column, or to the lookup's
"Sub - industry" property — put different data in 142 tables, so this reads the
answer off the build the user already did by hand instead of guessing.

Reads the column config panel via add_workemail_waterfall.open_column_config
(header menu -> Edit column) and dumps the panel's leaf text. Saves nothing;
closes each panel with Escape.

  python recon_cpj_reference.py                       # all 12 created columns
  python recon_cpj_reference.py --column "Sub Industry"
"""
import argparse
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
AUTO_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, SCRIPT_DIR)
sys.path.insert(0, os.path.join(AUTO_DIR, "clay_sync"))
sys.path.insert(0, os.path.join(AUTO_DIR, "build_automation"))

import clay_ui                            # noqa: E402
import browser_session                    # noqa: E402
import column_config as colcfg            # noqa: E402
import formula_columns as fcol            # noqa: E402
import add_workemail_waterfall as panel   # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# The user's reference build. Its workbook id is live, so it comes from the environment only
# (docs/SENSITIVE_DATA.md: no live Clay ids in tracked source); main() refuses to run without it.
REF_WORKBOOK_ID = os.environ.get("CLAY_CPJ_REF_WB", "")
REF_WORKBOOK = os.environ.get("CLAY_CPJ_REF_NAME", "ACHEMA")
REF_TABLE = os.environ.get("CLAY_CPJ_REF_TABLE", "Sellers - People")

# The twelve columns the template created, in creation order (from
# `clay tables columns list`, updatedAt 14:41-14:44 on 2026-08-03).
CREATED = ["Size", "Type", "LinkedIn URL", "Founded", "Industry", "Description",
           "Specialities", "Sub Industry", "Primary Offerings",
           "Business Stage", "Annual Revenue", "Total Funding Amount Range Usd"]

# Every leaf in the right-hand config panel, top-to-bottom. Deliberately no
# upper x bound: the formula/reference chip can extend well right of the label
# column, and clipping it is how a binding gets misread.
PANEL = """()=>{
  const norm=s=>(s||'').replace(/\\s+/g,' ').trim();
  const out=[];
  for(const el of document.querySelectorAll('*')){
    if(el.children.length) continue;
    const r=el.getBoundingClientRect();
    if(r.width===0||r.height===0||r.x<1240) continue;
    const t=norm(el.textContent);
    if(!t||t.length>90) continue;
    out.push({t, x:Math.round(r.x), y:Math.round(r.y)});
  }
  out.sort((a,b)=>a.y-b.y||a.x-b.x);
  return out;
}"""


# The reference chip renders only the LEAF name ("Sub - industry"), which is not
# enough to tell WHICH parent it came from -- and that is exactly the difference
# between a working binding and one that resolves empty (Clay's Company Lookup
# returns canonical company fields only; custom columns are not in its payload).
# So dump the chip element's own markup, where the resolved path/ids live.
CHIP_HTML = """(leaf)=>{
  const norm=s=>(s||'').replace(/\\s+/g,' ').trim();
  const out=[];
  for(const el of document.querySelectorAll('*')){
    if(el.children.length) continue;
    const r=el.getBoundingClientRect();
    if(r.width===0||r.x<1290||r.x>1400||r.y<240||r.y>330) continue;
    if(norm(el.textContent)!==leaf) continue;
    let node=el, chain=[];
    for(let i=0;i<5 && node;i++){
      const attrs={};
      for(const a of node.attributes||[]) attrs[a.name]=String(a.value).slice(0,300);
      chain.push({tag:node.tagName, attrs});
      node=node.parentElement;
    }
    out.push({chain, html:(el.parentElement?el.parentElement.outerHTML:el.outerHTML).slice(0,1200)});
  }
  return out;
}"""


def read_chip_html(page, column, leaf):
    print(f"\n{'='*66}\n=== chip markup for {column!r} (leaf {leaf!r})\n{'='*66}",
          flush=True)
    try:
        panel.open_column_config(page, column)
    except Exception as e:
        print(f"  !! could not open config: {str(e)[:100]}", flush=True)
        return
    page.wait_for_timeout(1500)
    hits = page.evaluate(CHIP_HTML, leaf)
    if not hits:
        print(f"  !! no chip element reading {leaf!r} in the band", flush=True)
    for h in hits:
        for i, node in enumerate(h["chain"]):
            print(f"  [{i}] <{node['tag']}> {node['attrs']}", flush=True)
        print(f"  html: {h['html']}", flush=True)
    page.keyboard.press("Escape")
    page.wait_for_timeout(900)


def read_column(page, column):
    print(f"\n{'='*66}\n=== {column!r}\n{'='*66}", flush=True)
    try:
        panel.open_column_config(page, column)
    except Exception as e:
        print(f"  !! could not open config: {str(e)[:100]}", flush=True)
        return
    page.wait_for_timeout(1200)
    for row in page.evaluate(PANEL):
        print(f"  x={row['x']:<5} y={row['y']:<5} {row['t']!r}", flush=True)
    try:
        txt = fcol.read_formula_text(page)
        if txt:
            print(f"  --- formula editor text ---\n  {txt[:400]!r}", flush=True)
    except Exception as e:
        print(f"  (formula read failed: {str(e)[:70]})", flush=True)
    page.keyboard.press("Escape")
    page.wait_for_timeout(900)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--column", action="append", default=[],
                    help="only this column (repeatable); default is all 12")
    ap.add_argument("--chip-html", metavar="LEAF",
                    help="dump the reference chip's markup for --column, where "
                         "the resolved path lives (the visible chip shows only "
                         "the leaf name)")
    ap.add_argument("--headed", action="store_true")
    a = ap.parse_args()
    wanted = a.column or CREATED
    if not REF_WORKBOOK_ID:
        raise SystemExit("set CLAY_CPJ_REF_WB to the reference workbook id (wb_...)")

    with browser_session.clay_page(headless=not a.headed) as page:
        clay_ui.open_workbook_by_id(page, REF_WORKBOOK_ID)
        if not colcfg.table_exists(page, REF_TABLE):
            raise SystemExit(f"{REF_WORKBOOK}: no table {REF_TABLE!r}")
        colcfg.focus_table_maybe_empty(page, REF_TABLE)
        page.wait_for_timeout(1500)
        print(f"reference: {REF_WORKBOOK} / {REF_TABLE}", flush=True)
        for column in wanted:
            if a.chip_html:
                read_chip_html(page, column, a.chip_html)
            else:
                read_column(page, column)


if __name__ == "__main__":
    main()
