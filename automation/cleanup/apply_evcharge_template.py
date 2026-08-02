"""Apply + run the consolidated EVCharge template on one event's
`exhibitors_normalized`.

The template ("Domain, Enrich Company, Lookup, Add rows" — see
config/entity-types/exhibitors_evcharge.yaml) replaces the two Labs templates,
so this is a single pass: apply the columns dormant, then trigger the
server-side run (select all rows -> Actions -> Run N rows), which is the only
run trigger that survives the browser closing.

Unlike apply_all_columns.py, the config-field mapping is DISCOVERED rather than
declared: it reads every field row in the template's Configure panel and fills
only the empty ones whose label matches a column of the source table. Clay
auto-maps by exact name, and the EVCharge columns (Company Name / Country /
Website / Description) are named exactly as the template expects, so normally
there is nothing left to fill — but a label that doesn't auto-map is filled
instead of aborting the workbook.

Scope: only workbooks in "10. EVCharge [2026 - Qasim]" / Competitive Events,
addressed by the ids captured at import time
(automation/clay_sync/evcharge_logs/wb_ids.json).

Usage:
  python apply_evcharge_template.py --event "EV Auto Show" --inspect  # dump panel fields
  python apply_evcharge_template.py --event "EV Auto Show" --dry-run
  python apply_evcharge_template.py --event "EV Auto Show" --apply    # apply, then run
  python apply_evcharge_template.py --event "EV Auto Show" --run-only # already applied
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

os.environ.setdefault("CLAY_PIPELINE_ENTITY", "exhibitors_evcharge")

import clay_ui        # noqa: E402
import browser_session         # noqa: E402
import column_config as colcfg  # noqa: E402
import pipeline_config as pcfg  # noqa: E402
import apply_all_columns as allcols  # noqa: E402
import run_all_columns as runcols   # noqa: E402

_CFG = pcfg.load()
TABLE = _CFG.main_table
TEMPLATE = _CFG.templates["all_columns"]
MARKER = "Official Domain"          # present only once the template is applied
COLUMNS = _CFG.entity_cfg["raw_columns"]

WB_IDS = os.path.join(AUTO_DIR, "clay_sync", "evcharge_logs", "wb_ids.json")

# Every field row in the Configure panel: its label and whether its input still
# shows the "Start typing" placeholder (i.e. nothing auto-mapped into it).
_PANEL_FIELDS = """() => {
  const norm = s => (s || '').replace(/\\s+/g, ' ').trim();
  const leaves = [...document.querySelectorAll('*')].filter(e => !e.children.length);
  const inPanel = r => r.x > 1250 && r.y > 180 && r.y < 900 && r.width > 0;
  const rows = [];
  for (const el of leaves) {
    const r = el.getBoundingClientRect();
    if (!inPanel(r)) continue;
    const t = norm(el.textContent);
    if (!t || t.includes('Start typing')) continue;
    rows.push({label: t, y: Math.round(r.y)});
  }
  const placeholders = [];
  for (const el of leaves) {
    const r = el.getBoundingClientRect();
    if (inPanel(r) && (el.textContent || '').includes('Start typing'))
      placeholders.push({y: Math.round(r.y), x: Math.round(r.x),
                         h: Math.round(r.height)});
  }
  // a label owns the placeholder that sits just below it
  for (const row of rows) {
    const p = placeholders.find(p => p.y > row.y + 5 && p.y < row.y + 48);
    row.empty = !!p;
    if (p) { row.click = {x: p.x + 30, y: p.y + Math.round(p.h / 2)}; }
  }
  return rows;
}"""


def workbook_id(event):
    with open(WB_IDS, encoding="utf-8") as fh:
        ids = json.load(fh)
    for wid, name in ids.items():
        if name == event:
            return wid
    raise SystemExit(f"No EVCharge workbook named {event!r} in {WB_IDS}")


def panel_fields(page):
    """Field rows of the open Configure panel, retried while it renders."""
    for _ in range(15):
        rows = page.evaluate(_PANEL_FIELDS)
        if any(r.get("empty") is not None for r in rows):
            return rows
        page.wait_for_timeout(1000)
    return page.evaluate(_PANEL_FIELDS)


def apply_template(page, wid, event, inspect, dry_run, say):
    clay_ui.open_workbook_by_id(page, wid)
    colcfg.focus_table(page, TABLE)

    if clay_ui._find_header_rect(page, MARKER):
        say(f"SKIP {event}: template already applied ({MARKER!r} present)")
        return "already_applied"

    allcols.TEMPLATE = TEMPLATE          # reuse the template-picker flow, our name
    allcols._open_v1_retry(page)
    rows = panel_fields(page)

    if inspect:
        say(f"Configure panel fields for {TEMPLATE!r}:")
        for r in rows:
            state = "EMPTY" if r.get("empty") else "filled/label"
            say(f"    y={r['y']:>4}  [{state:>12}]  {r['label'][:70]!r}")
        page.keyboard.press("Escape")
        return "inspected"

    todo = [r for r in rows if r.get("empty") and r["label"] in COLUMNS]
    unmapped = [r["label"] for r in rows if r.get("empty") and r["label"] not in COLUMNS]
    if unmapped:
        say(f"NOTE {event}: empty field(s) with no matching column, left alone: {unmapped}")

    if dry_run:
        say(f"DRYRUN {event}: would fill {[r['label'] for r in todo]} then "
            f"Save-and-don't-run, then trigger the run")
        page.keyboard.press("Escape")
        return "dryrun"

    for r in todo:
        page.mouse.click(r["click"]["x"], r["click"]["y"]); page.wait_for_timeout(900)
        page.keyboard.type(r["label"], delay=25); page.wait_for_timeout(1300)
        page.keyboard.press("Enter"); page.wait_for_timeout(1000)
        say(f"  set {r['label']!r}")

    for _ in range(10):
        if not allcols._save_disabled(page):
            break
        page.wait_for_timeout(1000)
    else:
        page.keyboard.press("Escape")
        raise SystemExit(f"ABORT {event}: Save stayed disabled")

    save = page.get_by_role("button", name="Save", exact=True).last
    box = save.bounding_box()
    page.mouse.click(box["x"] + box["width"] - 5, box["y"] + box["height"] / 2)
    page.wait_for_timeout(1200)
    dont = page.get_by_role("menuitem", name="Save and don't run", exact=True)
    if not (dont.count() and dont.first.is_visible()):
        items = [m.inner_text().strip().replace("\n", " ")
                 for m in page.get_by_role("menuitem").all() if m.is_visible()]
        page.keyboard.press("Escape")
        raise SystemExit(f"ABORT {event}: no 'Save and don't run' option: {items}")
    dont.first.click(timeout=8000)
    page.wait_for_timeout(5000)
    say(f"APPLIED {event}: columns added (dormant)")
    return "applied"


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--event", required=True, help="workbook / event folder name")
    ap.add_argument("--inspect", action="store_true",
                    help="open the template config panel, dump its fields, exit")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--apply", action="store_true", help="apply, then trigger the run")
    ap.add_argument("--run-only", action="store_true",
                    help="skip the apply; just trigger the run on an applied table")
    ap.add_argument("--headed", action="store_true")
    args = ap.parse_args()
    if not (args.inspect or args.dry_run or args.apply or args.run_only):
        ap.error("pick one of --inspect / --dry-run / --apply / --run-only")

    wid = workbook_id(args.event)
    say = lambda m: print(m, flush=True)
    say(f"{args.event}  [{wid}]  table={TABLE!r}  template={TEMPLATE!r}")

    # run_all_columns resolved TABLE at import time from the default entity config;
    # point it at this entity's table.
    runcols.TABLE = TABLE
    runcols.ALL_COLUMNS_MARKER = MARKER

    with browser_session.clay_page(headless=not args.headed) as page:
        if not args.run_only:
            state = apply_template(page, wid, args.event, args.inspect,
                                   args.dry_run, say)
            if state in ("inspected", "dryrun"):
                return
        if args.apply or args.run_only:
            res = runcols.run_v1(page, {"workbook_id": wid, "workbook_name": args.event},
                             False, say)
            say(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
