"""Read-only audit: for every Competitive Events table this repo exports, record
the view's filter count and its rows chip (visible/total), so we know which CSV
exports are short because the Default view filters rows out.

Motivated by the 2026-08-04 export: Clay's Ctrl+E -> Export -> Download CSV
exports the DEFAULT VIEW, not the table. A filtered default view therefore
yields a CSV that is short by exactly that filter, with nothing in the file to
say so.

Reading a filter is not as simple as reading the page: Clay's filter chip does
not put the column name in the DOM at all (no text, aria-label, title or input
value), and searching the page for the column name matches the GRID HEADER ~18px
below instead — which once reported a filter as applied when it was not, and
built a People table from 1,140 companies instead of 538. So this audit reads
the two signals that ARE trustworthy: the funnel BADGE COUNT and the rows chip.
Both helpers are reused from people_search.py rather than re-derived.

`visible < total` is the fact that matters — it is the row count the export will
contain. It is reported per table alongside the filter count.

    python audit_view_filters.py --out filters_audit.json
    python audit_view_filters.py --only "ACHEMA" --out /tmp/one.json
    python audit_view_filters.py --shards 3 --shard 0 --out filters_audit_w0.json

Read-only: it opens tables and reads two numbers. It clicks nothing.
"""
import argparse
import json
import os
import re
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


def parse_chip(text):
    """'1,601/1,601 rows' -> (1601, 1601). Returns (None, None) if unparsable.
    Clay abbreviates large counts ('1.2K/3K'), which must NOT be silently read
    as 1.2 — those are returned as None so they show up as unknown, not wrong."""
    if not text:
        return None, None
    m = re.match(r"^([\d.,KM]+)/([\d.,KM]+)( rows)?$", text.strip())
    if not m:
        return None, None
    def num(s):
        if re.search(r"[KM]", s):
            return None
        try:
            return int(s.replace(",", "").replace(".", ""))
        except ValueError:
            return None
    return num(m.group(1)), num(m.group(2))


def audit_table(page, table):
    if not ex.focus_table(page, table):
        return {"error": "could not focus tab"}
    page.wait_for_timeout(1500)
    anchor = page.evaluate(ps.ROWS_ANCHOR)
    chip = anchor["t"] if anchor else None
    visible, total = parse_chip(chip)
    try:
        nfilters = ps.filter_count(page)
    except Exception as e:
        nfilters = None
        chip = f"{chip} (filter_count error: {e})"
    return {"rows_chip": chip, "visible": visible, "total": total,
            "filters": nfilters,
            "short_by": (total - visible)
            if (visible is not None and total is not None) else None}


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True)
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--tables", nargs="*", default=ex.TABLES)
    ex.add_scope_args(ap)
    ap.add_argument("--shards", type=int, default=1)
    ap.add_argument("--shard", type=int, default=0)
    ap.add_argument("--headed", action="store_true")
    args = ap.parse_args()

    ex.set_workbooks_json(args.workbooks)
    wbs = ex.load_workbooks()
    scope = sorted(wbs.items(), key=lambda kv: kv[1].lower())
    if args.only:
        want = [o.lower() for o in args.only]
        scope = [(i, n) for i, n in scope
                 if any(w == n.lower() or w in n.lower() for w in want)]
    if args.shards > 1:
        scope = [x for k, x in enumerate(scope) if k % args.shards == args.shard]

    out = state_io.load_json(args.out)
    print(f"[audit] {len(scope)} workbook(s) -> {args.out}", flush=True)

    with browser_session.clay_page(headless=not args.headed) as page:
        for n, (wb_id, name) in enumerate(scope, 1):
            if name in out:
                print(f"[{n}/{len(scope)}] {name}: cached, skipping", flush=True)
                continue
            try:
                page.goto(f"https://app.clay.com/workspaces/{clay_ui.WORKSPACE_ID}"
                          f"/workbooks/{wb_id}",
                          wait_until="domcontentloaded", timeout=90000)
                tabs = ex.wait_for_tabs(page)
            except Exception as e:
                print(f"[{n}/{len(scope)}] {name}: OPEN FAILED {e}", flush=True)
                continue
            entry = {}
            for t in ex.expand_tables(args.tables, name):
                if t not in tabs:
                    continue
                entry[t] = audit_table(page, t)
                r = entry[t]
                print(f"[{n}/{len(scope)}] {name} | {t}: chip={r.get('rows_chip')} "
                      f"filters={r.get('filters')} short_by={r.get('short_by')}",
                      flush=True)
            out[name] = entry
            state_io.save_json(args.out, out)
    print("[audit done]", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
