"""Read-only recon: open the HTTP API column config on one People table and
dump the geometry of the panel and every Save-ish control, so the rollout's
save step can target real coordinates instead of the Companies pass's
hardcoded x-window. Saves nothing; closes with Escape.

  python recon_save_button.py "Material Sciences"
"""
import json
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
AUTO_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, SCRIPT_DIR)
sys.path.insert(0, os.path.join(AUTO_DIR, "clay_sync"))
sys.path.insert(0, os.path.join(AUTO_DIR, "build_automation"))

os.environ.setdefault("CLAY_SUPABASE_TEMPLATE", "People - Supabase")

import clay_ui                     # noqa: E402
import browser_session             # noqa: E402
import column_config as colcfg     # noqa: E402
import fix_supabase_api as fixapi  # noqa: E402

AUDIT = os.path.join(SCRIPT_DIR, "product_services_people.json")

VIEWPORT = """()=>({w:window.innerWidth, h:window.innerHeight,
                    dw:document.documentElement.scrollWidth})"""

# Every button/role=button on screen, with text and geometry. No x filter at
# all -- the point is to discover where things actually are.
BUTTONS = """()=>{
  const norm=s=>(s||'').replace(/\\s+/g,' ').trim();
  const out=[];
  for(const b of document.querySelectorAll('button,[role="button"],[role="menuitem"]')){
    const r=b.getBoundingClientRect();
    if(r.width===0||r.height===0) continue;
    const t=norm(b.textContent);
    if(!t||t.length>60) continue;
    out.push({t, x:Math.round(r.x), y:Math.round(r.y),
              w:Math.round(r.width), h:Math.round(r.height),
              right:Math.round(r.right)});
  }
  out.sort((a,b)=>a.y-b.y||a.x-b.x);
  return out;
}"""

# Anything whose text starts with "Save", wherever it is.
SAVEISH = """()=>{
  const norm=s=>(s||'').replace(/\\s+/g,' ').trim();
  const out=[];
  for(const el of document.querySelectorAll('*')){
    const t=norm(el.textContent);
    if(!t||t.length>60||!/^Save/.test(t)) continue;
    const r=el.getBoundingClientRect();
    if(r.width===0||r.height===0) continue;
    out.push({t, tag:el.tagName, leaf:!el.children.length,
              x:Math.round(r.x), y:Math.round(r.y),
              w:Math.round(r.width), right:Math.round(r.right)});
  }
  return out;
}"""


def main():
    name = sys.argv[1]
    audit = json.load(open(AUDIT, encoding="utf-8"))
    rec = audit[name]
    with browser_session.clay_page(headless=False) as page:
        def say(m):
            print(m, flush=True)

        clay_ui.open_workbook_by_id(page, rec["workbook_id"])
        colcfg.focus_table_maybe_empty(page, "People")
        page.wait_for_timeout(1500)

        print("VIEWPORT:", page.evaluate(VIEWPORT))

        fixapi._open_cfg(page, say)
        page.wait_for_timeout(2000)

        print("\nACCOUNT reads as:", repr(page.evaluate(fixapi._ACCOUNT)))

        print("\n--- Save-ish elements (before opening any menu) ---")
        for s in page.evaluate(SAVEISH):
            print(f"  {s['tag']:<8} leaf={str(s['leaf']):<5} x={s['x']:<5} "
                  f"y={s['y']:<5} w={s['w']:<5} right={s['right']:<5} {s['t']!r}")

        print("\n--- all buttons, bottom half of screen ---")
        for b in page.evaluate(BUTTONS):
            if b["y"] > 600:
                print(f"  x={b['x']:<5} y={b['y']:<5} w={b['w']:<4} "
                      f"right={b['right']:<5} {b['t']!r}")

        page.keyboard.press("Escape")
        page.wait_for_timeout(500)


if __name__ == "__main__":
    main()
