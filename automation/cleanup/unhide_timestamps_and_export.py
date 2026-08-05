"""Unhide the "Created At" / "Updated At" system columns on a table's view, then
re-export the CSV so it carries them.

Per the user (2026-08-05): both columns are needed on every exported table. They
exist in every table with fixed system ids (`f_created_at`, `f_updated_at`) but
are hidden by default, and Clay's CSV export drops hidden columns exactly as it
drops filtered rows — ACHEMA showed "48/50 columns" and exported 48.

Why re-export rather than patch the CSVs: the export contains no row id, so
there is no key to join API-fetched timestamps onto. The rows API documents only
"approximate creation order", and these tables have duplicate company names, so
an order-based join could attach the wrong timestamp to a real record with
nothing in the file to reveal it.

The two columns are NOT constant per table, so a single value cannot be filled
down without inventing per-row data:
    ACHEMA / Exhibitors_normalized (1,601 rows)
      Created At  17 distinct values, spanning 1.9s  (1,501 rows differ from row 1)
      Updated At  67 distinct values, spanning 31s   (1,597 rows differ from row 1)
The spread is small there because the rows were bulk-imported, but it is a
property of how a table was built, not a guarantee — check before assuming it.

Idempotent and resumable: a table whose CSV header already contains "Created At"
is skipped, so an interrupted run costs nothing to restart.

    python unhide_timestamps_and_export.py --workbooks other_sources_workbooks.json \\
        --out-root "...\\Other Sources" --tables "{event}_normalized" --only "LABMAS"
    python unhide_timestamps_and_export.py --shards 3 --shard 0        # fleet
    python unhide_timestamps_and_export.py --report-spread             # measure, export nothing
"""
import argparse
import csv
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

csv.field_size_limit(10 ** 9)

STATE_PATH = os.path.join(SCRIPT_DIR, "unhide_timestamps_state.json")
TS_COLS = ["Created At", "Updated At"]

# The toolbar columns chip, e.g. "48/50 columns" — the visible/total counter that
# proves an unhide actually took effect.
_CHIP_JS = """()=>{
  const norm=s=>(s||'').replace(/\\s+/g,' ').trim();
  for(const el of document.querySelectorAll('*')){
    if(el.children.length) continue;
    const t=norm(el.textContent);
    if(!/^\\d+\\/\\d+ columns$/.test(t)) continue;
    const r=el.getBoundingClientRect();
    return {t, x:Math.round(r.x+r.width/2), y:Math.round(r.y+r.height/2)};
  }
  return null;
}"""

# A column's row inside the open columns panel: role="button", exact name, and
# the panel's own width (~384px) so a same-named grid cell cannot match.
_ROW_JS = """(name)=>{
  const norm=s=>(s||'').replace(/\\s+/g,' ').trim();
  for(const el of document.querySelectorAll('[role="button"]')){
    if(norm(el.textContent)!==name) continue;
    const r=el.getBoundingClientRect();
    if(r.width<200||r.width>460) continue;
    return {x:Math.round(r.x), y:Math.round(r.y+r.height/2), w:Math.round(r.width)};
  }
  return null;
}"""


def chip(page):
    return page.evaluate(_CHIP_JS)


def parse_chip(text):
    try:
        vis, tot = text.split(" ")[0].split("/")
        return int(vis.replace(",", "")), int(tot.replace(",", ""))
    except Exception:
        return None, None


def missing_ts(path):
    """Which of the two timestamp columns an already-exported CSV lacks.

    This drives WHICH eye toggles to click, and it matters: the toggle is a
    toggle, so clicking a column that is already visible HIDES it. Only ever
    click the ones the CSV proves are absent."""
    if not (os.path.exists(path) and os.path.getsize(path) > 0):
        return list(TS_COLS)
    try:
        with open(path, encoding="utf-8", newline="") as fh:
            header = next(csv.reader(fh))
    except Exception:
        return list(TS_COLS)
    return [c for c in TS_COLS if c not in header]


def unhide_timestamps(page, names=TS_COLS):
    """Make each named column visible, ONE AT A TIME, reopening the panel between
    clicks. Returns (before, after) chips.

    One at a time because the panel re-sorts when a column becomes visible: the
    hidden columns sit in a group at the top, so unhiding "Created At" moves it
    out and shifts "Updated At" up into the row that was just measured. Clicking
    both from one panel session landed the second click on the wrong row and
    produced CSVs with exactly one of the two columns (4 tables on 2026-08-05).
    The chip count is not a sufficient check either — Material Sciences read
    15/17 -> 17/17 while only "Created At" reached the CSV, so the caller
    verifies against the exported header instead."""
    before = chip(page)
    if before is None:
        raise ex.ExportError("columns chip not found")
    clicked = []
    for name in names:
        c = chip(page)
        if c is None:
            raise ex.ExportError("columns chip vanished mid-unhide")
        page.mouse.click(c["x"], c["y"])
        page.wait_for_timeout(2000)
        row = page.evaluate(_ROW_JS, name)
        if row is None:
            page.keyboard.press("Escape")
            page.wait_for_timeout(800)
            continue
        page.mouse.click(row["x"] + row["w"] - 24, row["y"])   # the eye toggle
        page.wait_for_timeout(1200)
        page.keyboard.press("Escape")
        page.wait_for_timeout(1200)
        clicked.append(name)
    after = chip(page)
    if not clicked:
        raise ex.ExportError(f"no timestamp rows found in the columns panel "
                             f"(chip {before['t']})")
    return before, after


def csv_has_timestamps(path):
    if not (os.path.exists(path) and os.path.getsize(path) > 0):
        return False
    try:
        with open(path, encoding="utf-8", newline="") as fh:
            header = next(csv.reader(fh))
    except Exception:
        return False
    return all(c in header for c in TS_COLS)


def spread(path):
    """Distinct-value counts and time span for both columns, for reporting."""
    import datetime as dt
    with open(path, encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    out = {"rows": len(rows)}
    for col in TS_COLS:
        vals = sorted(v for v in (r.get(col) or "" for r in rows) if v)
        if not vals:
            out[col] = None
            continue
        try:
            f = lambda s: dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
            span = str(f(vals[-1]) - f(vals[0]))
        except Exception:
            span = "?"
        first = rows[0].get(col)
        out[col] = {"distinct": len(set(vals)), "min": vals[0], "max": vals[-1],
                    "span": span,
                    "differ_from_row1": sum(1 for r in rows if r.get(col) != first)}
    return out


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out-root", default=ex.DEFAULT_OUT_ROOT)
    ap.add_argument("--tables", nargs="*", default=ex.TABLES)
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--shards", type=int, default=1)
    ap.add_argument("--shard", type=int, default=0)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--state")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--include-missing", action="store_true",
                    help="also export tables that have no CSV yet (a fresh "
                         "folder); default only re-exports existing CSVs")
    ap.add_argument("--report-spread", action="store_true",
                    help="after exporting, print each CSV's timestamp spread")
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--download-timeout", type=int, default=900)
    ex.add_scope_args(ap)
    args = ap.parse_args()

    ex.set_workbooks_json(args.workbooks)
    wbs = ex.load_workbooks()
    scope = sorted(wbs.items(), key=lambda kv: kv[1].lower())
    if args.only:
        low = [o.lower() for o in args.only]
        scope = [(i, n) for i, n in scope
                 if any(w == n.lower() or w in n.lower() for w in low)]
    if args.shards > 1:
        scope = [x for k, x in enumerate(scope) if k % args.shards == args.shard]
    if args.limit:
        scope = scope[:args.limit]

    sp = args.state or STATE_PATH
    state = state_io.load_json(sp)

    if args.dry_run:
        todo = 0
        for _, name in scope:
            out_dir = os.path.join(args.out_root, name)
            need = [t for t in ex.expand_tables(args.tables, name)
                    if (args.include_missing
                        or os.path.exists(os.path.join(out_dir, t + ".csv")))
                    and not csv_has_timestamps(os.path.join(out_dir, t + ".csv"))]
            if need:
                todo += len(need)
                print(f"  [todo] {name}: {need}")
        print(f"\n[dry-run] {todo} table(s) need the timestamp columns")
        return 0

    print(f"[unhide+export] {len(scope)} workbook(s) | state {sp}", flush=True)
    ok = skipped = failed = 0
    with browser_session.clay_page(headless=not args.headed) as page:
        for n, (wb_id, name) in enumerate(scope, 1):
            out_dir = os.path.join(args.out_root, name)
            want = ex.expand_tables(args.tables, name)
            need = [t for t in want
                    if (args.include_missing
                        or os.path.exists(os.path.join(out_dir, t + ".csv")))
                    and not csv_has_timestamps(os.path.join(out_dir, t + ".csv"))]
            if not need:
                print(f"[{n}/{len(scope)}] {name}: nothing to do", flush=True)
                skipped += 1
                continue
            try:
                page.goto(f"https://app.clay.com/workspaces/{clay_ui.WORKSPACE_ID}"
                          f"/workbooks/{wb_id}",
                          wait_until="domcontentloaded", timeout=90000)
                tabs = ex.wait_for_tabs(page)
            except Exception as e:
                print(f"[{n}/{len(scope)}] {name}: OPEN FAILED {e}", flush=True)
                failed += 1
                continue
            entry = state.get(name, {})
            if any(t in tabs for t in need):
                os.makedirs(out_dir, exist_ok=True)
            for table in need:
                if table not in tabs:
                    print(f"    [absent] {table}", flush=True)
                    continue
                rec = {}
                try:
                    if not ex.focus_table(page, table):
                        raise ex.ExportError("could not focus tab")
                    dest_path = os.path.join(out_dir, table + ".csv")
                    b, a = unhide_timestamps(page, missing_ts(dest_path))
                    rec["chip"] = f"{b['t']} -> {a['t']}"
                    print(f"[{n}/{len(scope)}] {name} | {table}: {rec['chip']}",
                          flush=True)
                    dest = ex.export_table(page, table, out_dir,
                                           args.download_timeout * 1000)
                    if not csv_has_timestamps(dest):
                        raise ex.ExportError(
                            "exported CSV still lacks the timestamp columns")
                    rec["status"] = "done"
                    ok += 1
                    if args.report_spread:
                        print(f"      spread: {spread(dest)}", flush=True)
                except Exception as e:
                    rec["status"] = "failed"
                    rec["error"] = f"{type(e).__name__}: {e}"
                    print(f"      !! {rec['error']}", flush=True)
                    failed += 1
                entry[table] = rec
                state[name] = entry
                state_io.save_json(sp, state)

    print(f"\n[unhide+export done] ok={ok} skipped={skipped} failed={failed}",
          flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
