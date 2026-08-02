"""Ground-truth check of the HTTP API column's Account, independent of the
DOM reader that the rollout trusted.

Opens the column config on one People table, screenshots the panel, and dumps
EVERY leaf near an 'Account' label with no x-window filtering, so a
mis-scoped selector cannot manufacture a reassuring answer. Read-only.

  python verify_account_visual.py "Material Sciences"
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
SHOTS = os.path.join(SCRIPT_DIR, "people_shots")
os.makedirs(SHOTS, exist_ok=True)

# Every leaf containing "Account" or any known account name, anywhere on screen.
PROBE = """()=>{
  const norm=s=>(s||'').replace(/\\s+/g,' ').trim();
  const want=/Account|Qasim|Data Cloud|Labs/i;
  const out=[];
  for(const el of document.querySelectorAll('*')){
    if(el.children.length) continue;
    const t=norm(el.textContent);
    if(!t||t.length>70||!want.test(t)) continue;
    const r=el.getBoundingClientRect();
    if(r.width===0||r.height===0) continue;
    out.push({t, x:Math.round(r.x), y:Math.round(r.y), w:Math.round(r.width)});
  }
  out.sort((a,b)=>a.y-b.y||a.x-b.x);
  return out;
}"""


def main():
    name = sys.argv[1]
    rec = json.load(open(AUDIT, encoding="utf-8"))[name]
    with browser_session.clay_page(headless=False) as page:
        def say(m):
            print(m, flush=True)

        clay_ui.open_workbook_by_id(page, rec["workbook_id"])
        colcfg.focus_table_maybe_empty(page, "People")
        page.wait_for_timeout(1500)

        fixapi._open_cfg(page, say)
        page.wait_for_timeout(2500)

        print(f"\n_ACCOUNT reader says: {page.evaluate(fixapi._ACCOUNT)!r}")
        print("\nEvery Account/name-ish leaf on screen (no x filter):")
        for p in page.evaluate(PROBE):
            print(f"  x={p['x']:<5} y={p['y']:<5} w={p['w']:<5} {p['t']!r}")

        slug = "".join(c if c.isalnum() else "_" for c in name)[:40]
        shot = os.path.join(SHOTS, f"account_{slug}.png")
        page.screenshot(path=shot)
        print(f"\nscreenshot -> {shot}")

        page.keyboard.press("Escape")
        page.wait_for_timeout(400)


if __name__ == "__main__":
    main()
