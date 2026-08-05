"""Clear a filtered view's filters, then re-export that table's CSV over the
short one.

Per the user (2026-08-04): Clay's Ctrl+E export exports the DEFAULT VIEW, so a
filtered view produced a CSV short by exactly that filter — Analytica USA's
Exhibitors_normalized exported 69 of 258 rows. The instruction is to clear the
filters, re-download, and replace the file; the filters were temporary and are
NOT to be re-added afterwards. Hidden columns are deliberately left alone (the
user chose to keep the current visible column set), so a view can still be short
on columns by design — only rows are being fixed here.

=== This pass MUTATES live Clay views. Two properties keep it honest ===

1. It only ever touches a table the audit recorded with filters > 0, and the
   only control it clicks in the popover is "Clear filters". It never edits a
   column, a run condition, or anything else in the workbook.
2. It screenshots the open filter popover BEFORE clearing. Clay has no API for
   view filters and the filter chip's column name is not in the DOM at all —
   the popover row's entire textContent is "Andhas results", with the column
   contributing nothing — so a screenshot is the ONLY record of what a view
   filtered on. Cleared filters cannot otherwise be reconstructed.

Clearing is verified two ways before the re-export: the funnel badge must read 0
AND the rows chip must reach visible == total. Exporting against a view that did
not actually clear would silently rewrite the same short CSV, which is the
failure this pass exists to fix.

    python clear_filters_and_export.py --audit "faudit_w*.json" --dry-run
    python clear_filters_and_export.py --audit "faudit_w*.json" --only "ACHEMA Middle East"
    python clear_filters_and_export.py --audit "faudit_w*.json" --shards 3 --shard 0
"""
import argparse
import glob
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
import audit_view_filters as av  # noqa: E402
import people_search as ps  # noqa: E402

STATE_PATH = os.path.join(SCRIPT_DIR, "clear_filters_state.json")
SHOTS_DIR = os.path.join(SCRIPT_DIR, "clear_filters_shots")


def state_path_for(shard, shards):
    if shards <= 1:
        return STATE_PATH
    base, ext = os.path.splitext(STATE_PATH)
    return f"{base}_w{shard}{ext}"


def safe(name):
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", name).strip("_")


# The funnel button sits immediately right of the toolbar rows chip and carries
# the filter count as its label. people_search.open_filter_popover clicks a fixed
# +32px past the chip's RIGHT EDGE, which assumes a narrow chip: on a table whose
# chip reads "35,311/50,000 rows" the chip is 197px wide and +32 lands on the
# funnel's left border, so the popover never opened (Non-industry Specific JT
# Searches, 2026-08-05). Find the button itself instead of guessing an offset.
_FUNNEL_JS = """(x0)=>{
    let best = null;
    for (const b of document.querySelectorAll('button')) {
        const r = b.getBoundingClientRect();
        if (r.width === 0 || r.height === 0) continue;
        const cy = r.y + r.height/2;
        if (cy < 85 || cy > 125) continue;          // toolbar band only
        if (r.x < x0) continue;                     // right of the rows chip
        const t = (b.textContent || '').trim();
        if (!/^\\d*$/.test(t)) continue;             // badge count, or bare icon
        if (!best || r.x < best.x) best = {x: r.x, y: cy, w: r.width, t};
    }
    return best;
}"""


def open_filter_popover(page, tries=4):
    """Open the view's filter popover, clicking the funnel button by position."""
    anchor = None
    for _ in range(10):
        anchor = page.evaluate(ps.ROWS_ANCHOR)
        if anchor:
            break
        page.wait_for_timeout(1500)
    if not anchor:
        raise ex.ExportError("toolbar rows chip not found")
    for attempt in range(tries):
        f = page.evaluate(_FUNNEL_JS, anchor["right"])
        if f:
            page.mouse.click(f["x"] + f["w"] / 2, f["y"])
        else:                                  # fall back to the old offset
            page.mouse.click(anchor["right"] + 32, anchor["y"])
        page.wait_for_timeout(2200)
        if page.evaluate(ex._FIND_JS, "Add filter"):
            return anchor
        page.keyboard.press("Escape")
        page.wait_for_timeout(1000)
    raise ex.ExportError("filter popover would not open")


def clear_filters(page, event, table):
    """Open the filter popover, record it, clear it, and verify. Returns a dict
    describing what happened; raises nothing the caller cannot log."""
    info = {}
    open_filter_popover(page)
    page.wait_for_timeout(1000)
    os.makedirs(SHOTS_DIR, exist_ok=True)
    shot = os.path.join(SHOTS_DIR, f"{safe(event)}__{safe(table)}__before.png")
    page.screenshot(path=shot)
    info["screenshot"] = shot

    target = page.evaluate(ex._FIND_JS, "Clear filters")
    if not target:
        page.keyboard.press("Escape")
        raise ex.ExportError("'Clear filters' control not found in the popover")
    ex.click_rect(page, target[0])
    page.wait_for_timeout(2000)
    page.keyboard.press("Escape")
    page.wait_for_timeout(1500)

    info["filters_after"] = ps.filter_count(page)

    # The grid repopulates asynchronously after a clear, and the rows chip
    # reports 0 visible until it does — ILMAC read "0/178" a second after its
    # badge went to 0. Reading once turns that transient into a false "still
    # short" and skips a table that actually cleared fine, so poll until
    # visible == total (or the chip simply stops changing).
    vis = tot = None
    last, stable = None, 0
    for _ in range(30):
        anchor = page.evaluate(ps.ROWS_ANCHOR)
        chip = anchor["t"] if anchor else None
        vis, tot = av.parse_chip(chip)
        info["rows_chip_after"] = chip
        if vis is not None and tot is not None and vis == tot:
            break
        stable = stable + 1 if chip == last else 0
        if stable >= 4:                    # settled but genuinely still short
            break
        last = chip
        page.wait_for_timeout(1000)
    info["visible"], info["total"] = vis, tot
    if info["filters_after"]:
        raise ex.ExportError(
            f"filters still present after clearing (badge="
            f"{info['filters_after']}, chip={info['rows_chip_after']})")
    if vis is not None and tot is not None and vis != tot:
        raise ex.ExportError(
            f"badge cleared but view still short ({vis}/{tot}) — not exporting")
    return info


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--audit", nargs="*", required=True,
                    help="audit_view_filters output(s); scope = filters > 0")
    ap.add_argument("--out-root", default=ex.DEFAULT_OUT_ROOT)
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--shards", type=int, default=1)
    ap.add_argument("--shard", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-export", action="store_true",
                    help="clear filters only; skip the CSV download (use when a "
                         "later pass will export anyway)")
    ex.add_scope_args(ap)
    ap.add_argument("--state", help="override the state file path")
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--download-timeout", type=int, default=300)
    args = ap.parse_args()

    ex.set_workbooks_json(args.workbooks)
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
            targets.append((by_name[name], name, want, tables))
    if args.only:
        low = [o.lower() for o in args.only]
        targets = [t for t in targets
                   if any(w == t[1].lower() or w in t[1].lower() for w in low)]
    if args.shards > 1:
        targets = [x for k, x in enumerate(targets) if k % args.shards == args.shard]

    if args.dry_run:
        print(f"[dry-run] {len(targets)} workbook(s) with filtered views")
        for _, name, want, tables in targets:
            for t in want:
                r = tables[t]
                print(f"  {name} | {t}: {r.get('rows_chip')} "
                      f"(filters={r.get('filters')}, would recover "
                      f"{r.get('short_by')} rows)")
        return 0

    sp = args.state or state_path_for(args.shard, args.shards)
    state = state_io.load_json(sp)
    print(f"[clear+export] {len(targets)} workbook(s) | state {sp}", flush=True)

    ok = failed = 0
    with browser_session.clay_page(headless=not args.headed) as page:
        for n, (wb_id, name, want, _t) in enumerate(targets, 1):
            todo = [t for t in want
                    if state.get(name, {}).get(t, {}).get("status") != "done"]
            if not todo:
                print(f"[{n}/{len(targets)}] {name}: done already", flush=True)
                continue
            try:
                page.goto(f"https://app.clay.com/workspaces/{clay_ui.WORKSPACE_ID}"
                          f"/workbooks/{wb_id}",
                          wait_until="domcontentloaded", timeout=90000)
                ex.wait_for_tabs(page)
            except Exception as e:
                print(f"[{n}/{len(targets)}] {name}: OPEN FAILED {e}", flush=True)
                failed += 1
                continue

            entry = state.get(name, {})
            out_dir = os.path.join(args.out_root, name)
            os.makedirs(out_dir, exist_ok=True)
            for table in todo:
                print(f"[{n}/{len(targets)}] {name} | {table}", flush=True)
                rec = {}
                try:
                    if not ex.focus_table(page, table):
                        raise ex.ExportError("could not focus tab")
                    before = page.evaluate(ps.ROWS_ANCHOR)
                    rec["rows_chip_before"] = before["t"] if before else None
                    if not ps.filter_count(page):
                        rec["status"] = "no-filters"
                        print("      (no filters now — skipping)", flush=True)
                        entry[table] = rec
                        continue
                    rec.update(clear_filters(page, name, table))
                    print(f"      cleared: {rec['rows_chip_before']} -> "
                          f"{rec['rows_chip_after']}", flush=True)
                    if not args.no_export:
                        ex.export_table(page, table, out_dir,
                                        args.download_timeout * 1000)
                    rec["status"] = "done"
                    ok += 1
                except Exception as e:
                    rec["status"] = "failed"
                    rec["error"] = f"{type(e).__name__}: {e}"
                    print(f"      !! {rec['error']}", flush=True)
                    failed += 1
                entry[table] = rec
                state[name] = entry
                state_io.save_json(sp, state)

    print(f"\n[clear+export done] ok={ok} failed={failed}", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
