"""Scroll a People table and tally what the HTTP API column's cells actually
say, so a residual "Is New" shortfall can be diagnosed instead of guessed at.

check_column_fill counts an errored cell as unfilled, so a 350/362 reading could
be 12 errors, 12 rows still running, or 12 rows Clay skipped. Only the cell text
distinguishes them.

  python scan_http_errors.py "Cleanroom Technology"
"""
import json
import os
import sys
import collections

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
AUTO_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, SCRIPT_DIR)
sys.path.insert(0, os.path.join(AUTO_DIR, "clay_sync"))
sys.path.insert(0, os.path.join(AUTO_DIR, "build_automation"))

os.environ.setdefault("CLAY_SUPABASE_TEMPLATE", "People - Supabase")

import clay_ui                  # noqa: E402
import browser_session          # noqa: E402
import column_config as colcfg  # noqa: E402

AUDIT = os.path.join(SCRIPT_DIR, "product_services_people.json")

# Any cell text that looks like an HTTP API result or an error, anywhere.
PROBE = """()=>{
  const norm=s=>(s||'').replace(/\\s+/g,' ').trim();
  const out=[];
  for(const el of document.querySelectorAll('*')){
    if(el.children.length) continue;
    const t=norm(el.textContent);
    if(!t||t.length>90) continue;
    if(/^Status Code:/.test(t)||/^Error:/.test(t)||/Queued|Running|Pending/i.test(t))
      out.push(t.slice(0,80));
  }
  return out;
}"""


def main():
    name = sys.argv[1]
    rec = json.load(open(AUDIT, encoding="utf-8"))[name]
    tally = collections.Counter()
    with browser_session.clay_page(headless=False) as page:
        clay_ui.open_workbook_by_id(page, rec["workbook_id"])
        colcfg.focus_table_maybe_empty(page, "People")
        page.wait_for_timeout(2500)

        page.mouse.move(700, 500)
        for step in range(60):
            for t in page.evaluate(PROBE):
                tally[t] += 1
            page.mouse.wheel(0, 900)
            page.wait_for_timeout(450)

        print(f"\n=== {name}: distinct HTTP API cell texts seen ===")
        for t, n in tally.most_common(20):
            print(f"  {n:>5}  {t!r}")


if __name__ == "__main__":
    main()
