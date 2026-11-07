"""Read-only recon: open the "Qasim - CPJ - Labs - People" template's Configure
panel on one event workbook's Sellers/Buyers - People table and dump every leaf
text in the panel band with its geometry.

Written before apply_cpj_people.py exists, because this template's panel is
bigger than anything the earlier passes mapped: the user's finished reference
(ACHEMA / Sellers - People) gained TWELVE columns — Size, Type, LinkedIn URL,
Founded, Industry, Description, Specialities, Sub Industry, Primary Offerings,
Business Stage, Annual Revenue, Total Funding Amount Range Usd — so the panel
almost certainly scrolls, and a mapping written from guessed labels would save
half-configured. This prints the ground truth first, at three scroll positions.

Per the user (2026-08-03): every input is bound from under the "Company Table
Data" lookup column, and nothing here costs credits — the fields are read off a
lookup that has already run, not fetched from a provider.

Scope: takes a workbook id + table name directly (event workbooks live in
Competitive Events, which has no per-entity People manifest of its own — the
ids come from people_email_audit.json). Saves nothing; closes with Escape.

  python recon_cpj_people_panel.py wb_<workbook id> "ACHEMA Middle East" "Sellers - People"
  python recon_cpj_people_panel.py <wid> <name> "Sellers - People" --probe Size
"""
import argparse
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
AUTO_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, SCRIPT_DIR)
sys.path.insert(0, os.path.join(AUTO_DIR, "clay_sync"))
sys.path.insert(0, os.path.join(AUTO_DIR, "build_automation"))

os.environ.setdefault("CLAY_PEOPLE_TEMPLATE", "Qasim - CPJ - Labs - People")

import clay_ui                        # noqa: E402
import browser_session                # noqa: E402
import column_config as colcfg        # noqa: E402
import apply_people_waterfall as apw  # noqa: E402

# The panel band includes grid content showing through beside it — real company
# names, addresses, en dashes — which cp1252 cannot encode, so a plain print()
# raises UnicodeEncodeError partway through and loses the whole recon. Same
# failure render_build_prompt.py:256 documents for its no---out form.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

VIEWPORT = """()=>({w:window.innerWidth, h:window.innerHeight})"""

# Every leaf text from the panel's left edge rightward, with geometry. No y
# filter and a generous x floor -- the point is to discover where things are.
LEAVES = """(xmin)=>{
  const norm=s=>(s||'').replace(/\\s+/g,' ').trim();
  const out=[];
  for(const el of document.querySelectorAll('*')){
    if(el.children.length) continue;
    const r=el.getBoundingClientRect();
    if(r.width===0||r.height===0||r.x<xmin) continue;
    const t=norm(el.textContent);
    if(!t||t.length>70) continue;
    out.push({t, x:Math.round(r.x), y:Math.round(r.y),
              right:Math.round(r.right)});
  }
  out.sort((a,b)=>a.y-b.y||a.x-b.x);
  return out;
}"""


def dump(page, label, xmin=1200):
    print(f"\n--- {label} (leaves, x>={xmin}) ---", flush=True)
    for l in page.evaluate(LEAVES, xmin):
        print(f"  x={l['x']:<5} y={l['y']:<5} right={l['right']:<5} {l['t']!r}",
              flush=True)


def probe(page, label, typed=None):
    """Click one field's value box and dump what the picker offers under
    "Company Table Data". Read-only: nothing is selected, nothing is saved.

    `typed` is typed into the picker search AFTER the group is expanded. The
    repo's earlier note ("filtering reports No properties") was recorded for
    filtering BEFORE expanding; whether it descends once the schema is open is
    the decisive question for the 25 of 35 properties that virtualization never
    renders.
    """
    info = page.evaluate(apw._BOX, [label, apw.CHIP_VOCAB])
    if not info:
        print(f"\n!! {label!r}: label not found in the panel band", flush=True)
        return
    if not info.get("empty"):
        print(f"\n{label!r} is already set to {info.get('chip')!r}", flush=True)
        return
    print(f"\n=== picker for {label!r} (box x={info['x']} y={info['y']}) ===",
          flush=True)
    page.mouse.click(info["x"], info["y"])
    page.wait_for_timeout(2500)
    dump(page, f"{label} picker, top level", xmin=1260)
    pt = page.evaluate(apw._FIND_ROW, ["Company Table Data", 1290, 1400])
    if pt:
        print("\n  -- expanding 'Company Table Data' --", flush=True)
        page.mouse.click(pt["x"], pt["y"])
        page.wait_for_timeout(3000)
        dump(page, f"{label} picker, Company Table Data expanded", xmin=1260)
    if typed:
        print(f"\n  -- typing {typed!r} with the group expanded --", flush=True)
        page.keyboard.type(typed, delay=40)
        page.wait_for_timeout(2500)
        dump(page, f"{label} picker, filtered by {typed!r}", xmin=1260)
    page.keyboard.press("Escape")
    page.wait_for_timeout(800)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("workbook_id")
    ap.add_argument("workbook_name")
    ap.add_argument("table", nargs="?", default="Sellers - People")
    ap.add_argument("--probe", action="append", default=[],
                    help="field label whose picker should be opened and dumped")
    ap.add_argument("--headed", action="store_true")
    a = ap.parse_args()

    with browser_session.clay_page(headless=not a.headed) as page:
        # Match apply_cpj_people.PANEL_VIEWPORT: at clay_page's 980px the last
        # three of the thirteen inputs are below the fold, so a recon run at the
        # default height cannot see the panel the pass actually works against.
        page.set_viewport_size(
            {"width": 1720,
             "height": int(os.environ.get("CLAY_CPJ_VIEWPORT_H", "1500"))})
        clay_ui.open_workbook_by_id(page, a.workbook_id)
        if not colcfg.table_exists(page, a.table):
            raise SystemExit(f"{a.workbook_name}: no table {a.table!r}")
        colcfg.focus_table_maybe_empty(page, a.table)
        page.wait_for_timeout(1500)
        print("VIEWPORT:", page.evaluate(VIEWPORT), flush=True)

        apw._open_template_retry(page)
        print(f"template opened: {apw.TEMPLATE_USED!r}", flush=True)
        page.wait_for_timeout(2000)

        dump(page, "panel as opened")

        # Probing and scroll-dumping are separate modes: the wheel dumps push
        # the top fields to negative y, where apw._BOX (x1255-1400/y200-900)
        # can no longer see them, so a probe after scrolling reports the label
        # as missing when it is merely off-band.
        if a.probe:
            for spec in a.probe:
                # "domain:Company Domain" = probe 'domain', typing 'Company
                # Domain' into the picker once the group is expanded.
                lbl, _, typed = spec.partition(":")
                probe(page, lbl, typed=typed or None)
        else:
            for delta in (400, 400, 400):
                page.mouse.move(1500, 700)
                page.mouse.wheel(0, delta)
                page.wait_for_timeout(1500)
                dump(page, f"after wheel +{delta}")

        page.keyboard.press("Escape")
        page.wait_for_timeout(500)


if __name__ == "__main__":
    main()
