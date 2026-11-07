"""Which Competitive Events tables still have a `Country` input on their
`Verified Domain` Claygent column? (read-only scan, resumable)

Scope test for the Verified-Domain fix. Fill percentage ALONE is the wrong test
and would cause real damage:

  * Country input PRESENT  -> not yet fixed; the column is blocked and needs the
    input turned off, then a run.
  * Country input ABSENT, 0% filled -> already fixed. 0% is CORRECT here, not a
    fault: the column carries a separate `Only run if Website is empty` run
    condition, and on a table where every row has a Website no row qualifies.
    "Fixing" it again would be a no-op at best.
  * Country input ABSENT, partially filled -> already fixed and run.

So this asks the column's own config, and pairs with ce_verified_domain_fill.json
(the cheap CLI fill pass) to classify every table.

Writes ce_verified_domain_scan.json after EVERY table, so the environment's
background-task kills cost one table, not the sweep.

    python scan_verified_domain.py            # scan all not-yet-scanned
    python scan_verified_domain.py --only "Battery Tech Expo"
    python scan_verified_domain.py --report   # classify, no browser
"""

import argparse
import json
import os
import sys
import traceback

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
sys.path.insert(0, os.path.join(os.path.dirname(SCRIPT_DIR), "clay_sync"))
sys.path.insert(0, os.path.join(os.path.dirname(SCRIPT_DIR), "build_automation"))

import clay_ui                      # noqa: E402
import browser_session              # noqa: E402
import column_config as colcfg      # noqa: E402
import apply_ps_supabase as aps     # noqa: E402
import build_ps_companies as bps    # noqa: E402
import state_io                     # noqa: E402

MANIFEST = os.path.join(SCRIPT_DIR, "evcharge_ce_manifest.json")
FILL = os.path.join(SCRIPT_DIR, "ce_verified_domain_fill.json")
OUT = os.path.join(SCRIPT_DIR, "ce_verified_domain_scan.json")
COLUMN = "Verified Domain"
COUNTRY = "Country"


# A toggle on a given row of the panel. Each #INPUTS# entry carries one; its
# presence is what proves a matched label is a real input rather than a grid
# cell that happens to read "Country".
SWITCH_ON_ROW = """([ymin,ymax])=>{
  const out=[];
  for(const e of document.querySelectorAll('[role="switch"],input[type=checkbox]')){
    const r=e.getBoundingClientRect();
    if(r.width===0||r.x<1250||r.y<ymin||r.y>ymax) continue;
    out.push({x:Math.round(r.x+r.width/2), y:Math.round(r.y+r.height/2),
              checked:String(e.getAttribute('aria-checked')||e.checked)});
  }
  return out;}"""


def country_input(page, shots_dir, slug):
    """(has_country, switch) for the open Verified Domain column.

    Looks for the SPECIFIC `Country` input rather than enumerating everything in
    a coordinate band. The band approach failed badly: when the grid is scrolled
    horizontally its cells render in the same x-range as the panel, so the scan
    returned table values ('Privately Held', 'Public Company') as if they were
    input labels — and any table whose Country label it never actually examined
    came back a confident, wrong `country=False`.

    Requiring a toggle on the matched row is the disambiguator: grid cells have
    none, every real #INPUTS# entry does.
    """
    clay_ui.open_column_menu(page, COLUMN)
    page.wait_for_timeout(2000)
    edit = page.evaluate(bps.FIND, "Edit column")
    if not edit:
        raise aps.VerificationError(f"'Edit column' not in the {COLUMN!r} menu")
    page.mouse.click(edit["x"], edit["y"])
    page.wait_for_timeout(7000)

    anchor = None
    for _ in range(24):
        anchor = page.evaluate(aps.PANEL_LEAF, ["#INPUTS#", 150, 520])
        if anchor:
            break
        page.mouse.move(1450, 600)
        page.mouse.wheel(0, 300)
        page.wait_for_timeout(450)
    if not anchor:
        bps.shot(page, shots_dir, f"{slug}_no_inputs")
        raise aps.VerificationError("#INPUTS# never came into view")

    lab = (page.evaluate(aps.PANEL_LEAF, [COUNTRY + ":", anchor["y"], anchor["y"] + 200])
           or page.evaluate(aps.PANEL_LEAF, [COUNTRY, anchor["y"], anchor["y"] + 200]))
    if not lab:
        page.keyboard.press("Escape")
        return False, None
    sw = page.evaluate(SWITCH_ON_ROW, [lab["y"] - 14, lab["y"] + 14])
    page.keyboard.press("Escape")
    page.wait_for_timeout(600)
    return bool(sw), (sw[0] if sw else None)


def classify(name, scan, fill):
    """The FIX test is the Country input being present AND its toggle ON.

    Presence alone is not enough: turning the input off leaves the row in place
    with `checked: false` (that is exactly how APTA's looks after the fix), so a
    presence-only test would re-report every table already fixed this way as
    still needing work.
    """
    rec = scan.get(name, {})
    sw = rec.get("switch") or {}
    on = str(sw.get("checked", "")).lower() == "true"
    if fill.get(name, {}).get("filled"):
        return "skip: already run"
    if rec.get("has_country") and on:
        return "FIX: Country input ON, 0% filled"
    if name in scan:
        return "skip: already fixed (Country off/absent; 0% is the Website rule)"
    return "unscanned"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", action="append", default=[])
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--shots-dir", default=os.path.join(SCRIPT_DIR, "ce_vd_shots"))
    args = ap.parse_args()

    manifest = state_io.load_json(MANIFEST)
    fill = state_io.load_json(FILL) if os.path.exists(FILL) else {}
    scan = state_io.load_json(OUT)

    if args.report:
        buckets = {}
        for n in sorted(manifest):
            buckets.setdefault(classify(n, scan, fill), []).append(n)
        for k in sorted(buckets):
            print(f"\n{k}  ({len(buckets[k])})")
            for n in buckets[k]:
                f = fill.get(n, {})
                print(f"   {f.get('filled','?'):>5}/{f.get('rows','?'):<5} {n}")
        return 0

    # Only tables that could still need the fix: 0% filled (or fill unknown).
    names = [n for n in sorted(manifest, key=lambda x: manifest[x].get("rows") or 0)
             if not fill.get(n, {}).get("filled")]
    if args.only:
        names = [n for n in names if n in args.only]
    names = [n for n in names if n not in scan]
    if args.limit:
        names = names[:args.limit]

    print(f"{len(names)} to scan")
    for i, name in enumerate(names, 1):
        rec = manifest[name]
        slug = "".join(c if c.isalnum() else "_" for c in name)[:60]
        print(f"\n=== [{i}/{len(names)}] {name}", flush=True)
        try:
            with browser_session.clay_page(headless=not args.headed) as page:
                clay_ui.open_workbook_by_id(page, rec["workbook_id"])
                page.wait_for_timeout(8000)
                colcfg.focus_table_maybe_empty(page, rec["table_name"])
                page.wait_for_timeout(2500)
                has, sw = country_input(page, args.shots_dir, slug)
            scan[name] = {"has_country": has, "switch": sw}
            print(f"  country_input={has}  switch={sw}")
        except Exception as e:
            traceback.print_exc()
            print(f"  FAILED: {str(e)[:120]}")
        state_io.save_json(OUT, scan)
    print(f"\nscanned {len(scan)} of {len(manifest)}; wrote {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
