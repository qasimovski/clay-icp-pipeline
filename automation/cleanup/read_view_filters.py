"""Read-only: record WHAT each filtered view filters on — column, operator and
value — plus its rows and columns chips.

Companion to audit_view_filters.py, which records only how many filters a view
has. This one opens the filter popover and transcribes each row, because two
things depend on knowing the actual definitions:

  * a filter can only be RESTORED if it was recorded first, and Clay has no API
    to read or write view filters (UI automation only, see CLAUDE.md), so this
    file is the sole restore record before anything is cleared;
  * `apply_view_filters.py` only knows how to rebuild ONE pair (Side equal to
    Seller / Send table data has results) on Exhibitors_normalized — it cannot
    restore a People table's filters, so those must be transcribed here or they
    are gone for good.

Also captures the COLUMNS chip ("41/43 columns"). Hidden columns are excluded
from Clay's CSV export just as filtered rows are, so a view can be short in two
independent ways and clearing filters only fixes one of them.

Scope defaults to the tables that audit_view_filters.py found with filters > 0.

    python read_view_filters.py --audit filters_audit.json --out filters_detail.json
    python read_view_filters.py --audit ... --out ... --shards 3 --shard 0
"""
import argparse
import glob
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.normpath(os.path.join(SCRIPT_DIR, "..", ".."))
sys.path.insert(0, os.path.join(REPO_ROOT, "automation", "build_automation"))
sys.path.insert(0, os.path.join(REPO_ROOT, "automation", "clay_sync"))
sys.path.insert(0, SCRIPT_DIR)

import browser_session  # noqa: E402
import clay_ui  # noqa: E402
import state_io  # noqa: E402
import export_event_tables as ex  # noqa: E402
import people_search as ps  # noqa: E402

# Every leaf in the popover's filter-row band. Read the SMALLEST element per
# x-slot: the chip is a container (icon + label) and the grid header sits ~18px
# below, so a loose scan transcribes the header instead of the filter.
_ROWS_JS = """(band)=>{
  const [ymin, ymax] = band;
  const norm = s => (s||'').replace(/\\s+/g,' ').trim();
  const best = new Map();
  for (const el of document.querySelectorAll('*')) {
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0 || r.width > 420) continue;
    const cy = r.y + r.height/2;
    if (cy < ymin || cy > ymax || r.x < 160 || r.x > 1120) continue;
    const t = norm(el.textContent);
    if (!t || t.length > 60) continue;
    const k = Math.round(r.x/18) + ':' + Math.round(cy/12);
    const a = r.width * r.height;
    if (!best.has(k) || a < best.get(k).a)
      best.set(k, {t, a, x: Math.round(r.x), y: Math.round(cy)});
  }
  return [...best.values()].sort((p,q) => p.y - q.y || p.x - q.x)
                           .map(v => ({t: v.t, x: v.x, y: v.y}));
}"""

_COLS_CHIP = """()=>{
  const norm = s => (s||'').replace(/\\s+/g,' ').trim();
  for (const el of document.querySelectorAll('*')) {
    if (el.children.length) continue;
    const t = norm(el.textContent);
    if (!/^\\d+\\/\\d+ columns$/.test(t)) continue;
    return t;
  }
  return null;
}"""


def group_rows(items):
    """Cluster the popover leaves into one list per filter row (by y)."""
    rows, cur, last_y = [], [], None
    for it in items:
        if last_y is None or abs(it["y"] - last_y) <= 14:
            cur.append(it)
        else:
            rows.append(cur)
            cur = [it]
        last_y = it["y"]
    if cur:
        rows.append(cur)
    out = []
    for r in rows:
        texts = [x["t"] for x in r]
        if any(t in ("Where", "And", "Or") for t in texts):
            out.append(texts)
    return out


def read_detail(page, table):
    if not ex.focus_table(page, table):
        return {"error": "could not focus tab"}
    page.wait_for_timeout(1200)
    info = {"columns_chip": page.evaluate(_COLS_CHIP)}
    anchor = page.evaluate(ps.ROWS_ANCHOR)
    info["rows_chip"] = anchor["t"] if anchor else None
    try:
        info["filters"] = ps.filter_count(page)
    except Exception:
        info["filters"] = None
    if not info["filters"]:
        return info
    try:
        ps.open_filter_popover(page, lambda _m: None)
        page.wait_for_timeout(1200)
        info["filter_rows"] = group_rows(page.evaluate(_ROWS_JS, [130, 300]))
        page.keyboard.press("Escape")
        page.wait_for_timeout(500)
    except Exception as e:
        info["filter_rows_error"] = f"{type(e).__name__}: {e}"
    return info


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--audit", nargs="*", default=[],
                    help="audit_view_filters output(s); scope = filters > 0")
    ap.add_argument("--out", required=True)
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--shards", type=int, default=1)
    ap.add_argument("--shard", type=int, default=0)
    ap.add_argument("--headed", action="store_true")
    args = ap.parse_args()

    audit = {}
    for pat in args.audit:
        for p in glob.glob(pat):
            audit.update(state_io.load_json(p))

    wbs = ex.load_workbooks()
    by_name = {n: i for i, n in wbs.items()}
    targets = []
    for name, tables in sorted(audit.items()):
        want = [t for t, r in tables.items()
                if isinstance(r, dict) and (r.get("filters") or 0) > 0]
        if want:
            targets.append((by_name[name], name, want))
    if args.only:
        low = [o.lower() for o in args.only]
        targets = [t for t in targets
                   if any(w == t[1].lower() or w in t[1].lower() for w in low)]
    if args.shards > 1:
        targets = [x for k, x in enumerate(targets) if k % args.shards == args.shard]

    out = state_io.load_json(args.out)
    print(f"[detail] {len(targets)} workbook(s) with filtered views", flush=True)
    with browser_session.clay_page(headless=not args.headed) as page:
        for n, (wb_id, name, tables) in enumerate(targets, 1):
            if name in out:
                print(f"[{n}/{len(targets)}] {name}: cached", flush=True)
                continue
            try:
                page.goto(f"https://app.clay.com/workspaces/{clay_ui.WORKSPACE_ID}"
                          f"/workbooks/{wb_id}",
                          wait_until="domcontentloaded", timeout=90000)
                ex.wait_for_tabs(page)
            except Exception as e:
                print(f"[{n}/{len(targets)}] {name}: OPEN FAILED {e}", flush=True)
                continue
            entry = {}
            for t in tables:
                entry[t] = read_detail(page, t)
                print(f"[{n}/{len(targets)}] {name} | {t}: "
                      f"{entry[t].get('rows_chip')} | {entry[t].get('columns_chip')} "
                      f"| {entry[t].get('filter_rows')}", flush=True)
            out[name] = entry
            state_io.save_json(args.out, out)
    print("[detail done]", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
