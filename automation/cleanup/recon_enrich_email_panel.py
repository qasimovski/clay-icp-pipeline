"""Read-only recon: open the "Enrich and Validate Email" template's Configure
panel on one Product & Services People table and dump every leaf text in the
panel band with its geometry.

Written because apply_people_enrich_email.py --recon reported three of the four
fields as `null` (label not found in the x1255-1400 / y200-900 band) while
"Full Name" resolved fine — so the labels are either below the fold or spelled
differently than the user's mapping keys. This prints the ground truth instead
of guessing, then scrolls the panel and prints it again.

Saves nothing; closes with Escape.

  python recon_enrich_email_panel.py "Cleanroom Technology"
"""
import json
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
AUTO_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, SCRIPT_DIR)
sys.path.insert(0, os.path.join(AUTO_DIR, "clay_sync"))
sys.path.insert(0, os.path.join(AUTO_DIR, "build_automation"))

os.environ.setdefault("CLAY_PEOPLE_TEMPLATE", "Enrich and Validate Email")

import clay_ui                        # noqa: E402
import browser_session                # noqa: E402
import column_config as colcfg        # noqa: E402
import apply_people_waterfall as apw  # noqa: E402

AUDIT = os.path.join(SCRIPT_DIR, "product_services_people.json")
TABLE = "People"

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


def probe(page, label, say, typed=None):
    """Click one field's value box and dump what the picker offers, then leave
    the box as it was. Read-only: nothing is selected, nothing is saved.

    `typed` narrows the picker the same way apply_people_waterfall._fill does,
    which is the decisive test for whether a column is offered at all (the
    visible list is short and the overlay clips it).
    """
    info = page.evaluate(apw._BOX, [label, apw.CHIP_VOCAB])
    if not info:
        print(f"\n!! {label!r}: label not found in the panel band", flush=True)
        return
    if not info.get("empty"):
        print(f"\n{label!r} is already set to {info.get('chip')!r}", flush=True)
        return
    print(f"\n=== picker options for {label!r} (box at "
          f"x={info['x']} y={info['y']}) ===", flush=True)
    page.mouse.click(info["x"], info["y"])
    page.wait_for_timeout(2500)
    dump(page, f"{label} picker, top level", xmin=1260)

    if typed:
        page.keyboard.type(typed, delay=40)
        page.wait_for_timeout(2500)
        dump(page, f"{label} picker, filtered by {typed!r}", xmin=1260)

    # Walk DOWN the tree, expanding each level in turn. Clay's search shows a
    # group when a DESCENDANT matches, so leaves are only visible once every
    # ancestor is expanded — no break, this is a chain not a choice.
    for group in ("People - Supabase", "Lookup in Audiences", "Add row",
                  "Company Table Data"):
        pt = page.evaluate(apw._FIND_ROW, [group, 1290, 1400])
        if not pt:
            continue
        print(f"\n  -- expanding {group!r} --", flush=True)
        page.mouse.click(pt["x"], pt["y"])
        page.wait_for_timeout(3000)
        dump(page, f"{label} picker, {group} expanded", xmin=1260)


def main():
    name = sys.argv[1] if len(sys.argv) > 1 else "Cleanroom Technology"
    probes = [a for a in sys.argv[2:] if not a.startswith("-")]
    audit = json.load(open(AUDIT, encoding="utf-8"))
    rec = audit[name]
    if probes:
        with browser_session.clay_page(headless=True) as page:
            def say(m):
                print(m, flush=True)
            clay_ui.open_workbook_by_id(page, rec["workbook_id"])
            colcfg.focus_table_maybe_empty(page, TABLE)
            page.wait_for_timeout(1500)
            apw._open_template_retry(page)
            print(f"template opened: {apw.TEMPLATE_USED!r}", flush=True)
            page.wait_for_timeout(1800)
            for spec in probes:
                # "records:Lookup" = probe 'records', typing 'Lookup' to filter.
                label, _, typed = spec.partition(":")
                probe(page, label, say, typed=typed or None)
            page.keyboard.press("Escape")
            page.wait_for_timeout(500)
        return

    with browser_session.clay_page(headless=True) as page:
        def say(m):
            print(m, flush=True)

        clay_ui.open_workbook_by_id(page, rec["workbook_id"])
        colcfg.focus_table_maybe_empty(page, TABLE)
        page.wait_for_timeout(1500)
        print("VIEWPORT:", page.evaluate(VIEWPORT), flush=True)

        apw._open_template_retry(page)
        print(f"template opened: {apw.TEMPLATE_USED!r}", flush=True)
        page.wait_for_timeout(2000)

        dump(page, "panel as opened")

        # Scroll inside the panel and re-dump: fields below the fold are the
        # leading theory for the three nulls.
        page.mouse.move(1500, 700)
        page.mouse.wheel(0, 400)
        page.wait_for_timeout(1500)
        dump(page, "after wheel +400")

        page.mouse.wheel(0, 400)
        page.wait_for_timeout(1500)
        dump(page, "after wheel +800")

        page.keyboard.press("Escape")
        page.wait_for_timeout(500)


if __name__ == "__main__":
    main()
