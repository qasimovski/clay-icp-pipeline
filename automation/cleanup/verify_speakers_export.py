"""Verify the exported Speakers_normalized CSVs against the live Clay tables (run in WSL).

Per the user (2026-08-07): Speakers_normalized was exported for the 43 Competitive
Events workbooks that have one, with the hidden Created At / Updated At columns
unhidden first.

Two things can silently truncate a Clay CSV export, and neither raises an error
(see docs and the clay-csv-export-ui notes): the export is of the DEFAULT VIEW,
so a view filter drops rows and a hidden column drops a column. The exporter
already asserts the timestamp columns reached the header; this checks the other
half — that the CSV's row count matches the live table's.

Row counts come from the read-only CLI, so this makes no UI assumptions and
cannot be fooled by the grid's own "X/Y rows" chip racing the render.

    MSYS_NO_PATHCONV=1 wsl -d Ubuntu -- python3 .../verify_speakers_export.py
"""
import csv
import glob
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SCOPE = os.path.join(HERE, "speakers_normalized_workbooks.json")
OUT = os.path.join(HERE, "speakers_export_verify.json")
CLEAN_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
TS_COLS = ("Created At", "Updated At")

# The Windows export root as WSL sees it. Kept out of tracked source everywhere
# else (browser_session.local_setting), but this script only ever runs under WSL
# where the drive mount prefix is what varies, so allow an env override.
EXPORT_ROOT = os.environ.get(
    "CLAY_EXPORT_ROOT_WSL", "/mnt/c/Users/qasim/terrapinn/Labs - Tables")

csv.field_size_limit(10 ** 9)


def clay_bin():
    env = os.environ.get("CLAY_BIN")
    if env:
        return env
    base = os.path.expanduser("~/.config/clay/bin")
    c = sorted(glob.glob(os.path.join(base, "clay-*-linux-x64")) +
               glob.glob(os.path.join(base, "clay-*-linux-arm64")))
    return c[-1] if c else "clay"


def run(bin_, args):
    env = dict(os.environ)
    env["PATH"] = CLEAN_PATH
    for _ in range(6):
        r = subprocess.run([bin_] + args, capture_output=True, text=True, env=env)
        if r.returncode == 0 and r.stdout.strip():
            try:
                return json.loads(r.stdout)
            except json.JSONDecodeError:
                pass
        time.sleep(2)
    raise SystemExit(f"clay {' '.join(args)} failed: {r.stderr[:300]}")


def live_row_count(bin_, tid):
    """Page the rows endpoint and count. `tables get` carries no row count, so
    counting is the only CLI-side truth available. 100 is the CLI's hard cap on
    --limit (it rejects anything larger), so the biggest speakers tables cost a
    few dozen requests."""
    total, cur = 0, None
    for _ in range(2000):
        d = run(bin_, ["tables", "rows", "list", tid, "--limit", "100"] +
                (["--cursor", cur] if cur else []))
        rows = d.get("data") if isinstance(d, dict) else d
        total += len(rows or [])
        cur = d.get("cursor") if isinstance(d, dict) else None
        if not cur:
            break
    return total


def csv_stats(path):
    if not (os.path.exists(path) and os.path.getsize(path) > 0):
        return None
    with open(path, encoding="utf-8", newline="") as fh:
        rd = csv.reader(fh)
        header = next(rd)
        n = sum(1 for _ in rd)
    return {"rows": n, "cols": len(header),
            "missing_ts": [c for c in TS_COLS if c not in header]}


def main():
    bin_ = clay_bin()
    scope = json.load(open(SCOPE, encoding="utf-8"))
    want_names = set(scope.values())

    # workbook name -> live Speakers_normalized table id
    tables, cur = {}, None
    for _ in range(200):
        d = run(bin_, ["tables", "list", "--limit", "100"] +
                (["--cursor", cur] if cur else []))
        for t in d.get("data", []):
            wb = (t.get("workbook") or {}).get("name")
            if wb in want_names and t.get("name") == "Speakers_normalized":
                tables[wb] = t["id"]
        cur = d.get("cursor")
        if not cur:
            break

    report, bad = {}, []
    for name in sorted(want_names, key=str.lower):
        path = os.path.join(EXPORT_ROOT, name, "Speakers_normalized.csv")
        st = csv_stats(path)
        tid = tables.get(name)
        rec = {"table_id": tid, "csv": st}
        if st is None:
            rec["status"] = "NO CSV"
        elif tid is None:
            rec["status"] = "NO LIVE TABLE"
        else:
            live = live_row_count(bin_, tid)
            rec["live_rows"] = live
            if st["missing_ts"]:
                rec["status"] = "MISSING TIMESTAMPS"
            elif st["rows"] != live:
                rec["status"] = "ROW MISMATCH"
            else:
                rec["status"] = "ok"
        if rec["status"] != "ok":
            bad.append(name)
        report[name] = rec
        print(f"  {name:52} {rec['status']:20} "
              f"csv={st['rows'] if st else '-'} live={rec.get('live_rows', '-')} "
              f"cols={st['cols'] if st else '-'}", flush=True)

    json.dump(report, open(OUT, "w", encoding="utf-8"), indent=2)
    print(f"\n[verify] {len(want_names) - len(bad)}/{len(want_names)} ok -> {OUT}")
    if bad:
        print(f"[bad] {bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
