"""Combine the 43 exported Speakers_normalized CSVs into one file with a fixed
13-column schema, dropping rows with no Speaker Name.

Per the user (2026-08-07), after the Speakers_normalized export pass. Pure local
file work: reads `<out-root>/<Event>/Speakers_normalized.csv`, writes one CSV.
No browser, no Clay, no credits.

=== Why this needs an alias table rather than a straight column subset ===

The 43 tables carry FOUR different header shapes, because the email waterfall was
built in two generations and two events were built by hand. The four differ only
in the last four columns:

  35 files  WORK EMAIL | Validate Email           | Smtp Provider   | Email Validation Result
   6 files  WORK EMAIL | Validate email           | Mx Provider     | Status          (+ Mx Record)
   1 file   WORK EMAIL | Validate Email           | Smtp Provider   | Email Status Combined  (BioTrinity)
   1 file   WORK EMAIL | LeadMagic Validate Email | Mx Provider (2) | Status (2)      (Bioprocessing Summit)

so a naive `row["Status"]` would be empty for 36 of 43 events and a naive
`row["Smtp Provider"]` empty for 7. ALIASES maps each output column to its
candidates in priority order; the first one PRESENT IN THE HEADER wins (presence,
not truthiness — a column that exists but is empty for a row is a real empty
value, not a reason to fall through to a different column's data).

Two vocabulary differences survive the merge on purpose, because normalizing them
would destroy information the source actually carries:

  Status        old-gen files say bare `valid` / `invalid`; new-gen files say a
                compound `<status>+<sub_status>` (`invalid+mailbox_not_found`,
                `do_not_mail+role_based`, `unknown+greylisted`). The token before
                the '+' is the same vocabulary, so `startswith('valid')` works
                across both, but the strings are not equal.
  Validate Email  reads `Valid email` / `Invalid email` everywhere EXCEPT
                Bioprocessing Summit, whose older LeadMagic column reports run
                success (`Success`) rather than a validity verdict. That event's
                verdict lives in Status, like the other old-gen files.

Smtp Provider also differs in case only (`g-suite` vs `Google Workspace`); left
as-is for the same reason.

    python combine_speakers_csvs.py --dry-run
    python combine_speakers_csvs.py
    python combine_speakers_csvs.py --out "D:\\somewhere\\Speakers_combined.csv"
"""
import argparse
import csv
import os
import sys
from collections import Counter

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.normpath(os.path.join(SCRIPT_DIR, "..", ".."))
sys.path.insert(0, os.path.join(REPO_ROOT, "automation", "build_automation"))
sys.path.insert(0, os.path.join(REPO_ROOT, "automation", "clay_sync"))

import browser_session  # noqa: E402

csv.field_size_limit(10 ** 9)

DEFAULT_ROOT = browser_session.local_setting(
    "export_root", "CLAY_EXPORT_ROOT",
    default=r"C:\Users\qasim\terrapinn\Labs - Tables")

SOURCE_NAME = "Speakers_normalized.csv"
DEFAULT_OUT = "Speakers_combined.csv"

# Output column -> source candidates, first PRESENT in the header wins.
# The output names are the user's, not Clay's ("LinkedIn Profile", not
# "LinkedIn URL"); the order here is the order in the written file.
ALIASES = [
    ("Created at",       ["Created At"]),
    ("Updated at",       ["Updated At"]),
    ("Event",            ["Event"]),
    ("Speaker Name",     ["Speaker Name"]),
    ("Bio",              ["Bio"]),
    # NOT "LinkedIn Profile URL", which also exists in every file and is filled
    # 95.1% against this column's 98.8% — the user named "LinkedIn URL".
    ("LinkedIn Profile", ["LinkedIn URL"]),
    ("Title",            ["Title"]),
    ("Company",          ["Company"]),
    ("Country",          ["Country"]),
    ("Work Email",       ["WORK EMAIL"]),
    ("Validate Email",   ["Validate Email", "Validate email",
                          "LeadMagic Validate Email"]),
    ("Smtp Provider",    ["Smtp Provider", "Mx Provider", "Mx Provider (2)"]),
    ("Status",           ["Status", "Status (2)", "Email Validation Result",
                          "Email Status Combined"]),
]

# The row filter. Kept as a named constant because it is the one destructive
# decision in this pass: 1,256 of 6,765 rows have no Speaker Name (the column is
# a downstream enrichment, so an unenriched row is blank here while its raw
# "Name" is populated).
KEY_COLUMN = "Speaker Name"


def resolve(header, candidates):
    """First candidate present in this file's header, else None."""
    for c in candidates:
        if c in header:
            return c
    return None


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=DEFAULT_ROOT,
                    help="directory of per-event folders")
    ap.add_argument("--out", help=f"output CSV (default <root>/{DEFAULT_OUT})")
    ap.add_argument("--dry-run", action="store_true",
                    help="report the mapping and row counts, write nothing")
    args = ap.parse_args()

    out_path = args.out or os.path.join(args.root, DEFAULT_OUT)
    out_cols = [name for name, _ in ALIASES]

    sources = []
    for d in sorted(os.listdir(args.root)):
        p = os.path.join(args.root, d, SOURCE_NAME)
        if os.path.isfile(p):
            sources.append((d, p))
    if not sources:
        raise SystemExit(f"no {SOURCE_NAME} under {args.root}")

    rows_out, per_event, dropped = [], [], 0
    used = {name: Counter() for name, _ in ALIASES}
    unresolved = []

    for event, path in sources:
        with open(path, encoding="utf-8", newline="") as fh:
            rd = csv.DictReader(fh)
            header = rd.fieldnames or []
            mapping = {}
            for name, cands in ALIASES:
                src = resolve(header, cands)
                if src is None:
                    unresolved.append((event, name))
                else:
                    used[name][src] += 1
                mapping[name] = src
            kept = 0
            for r in rd:
                if not (r.get(KEY_COLUMN) or "").strip():
                    dropped += 1
                    continue
                rows_out.append({
                    name: (r.get(src) or "") if src else ""
                    for name, src in mapping.items()})
                kept += 1
        per_event.append((event, kept))

    print(f"[sources] {len(sources)} files under {args.root}")
    print(f"[schema]  {len(out_cols)} columns\n")
    print("column -> source column(s) actually used (file count):")
    for name, _ in ALIASES:
        got = ", ".join(f"{s} x{n}" for s, n in used[name].most_common())
        print(f"  {name:17} <- {got or '(NONE RESOLVED)'}")
    if unresolved:
        print(f"\n[warn] {len(unresolved)} column(s) unresolved, written empty:")
        for e, c in unresolved[:20]:
            print(f"   {e}: {c}")

    print(f"\n[rows] kept {len(rows_out):,}, dropped {dropped:,} "
          f"with empty {KEY_COLUMN} "
          f"(of {len(rows_out) + dropped:,})")

    if args.dry_run:
        print("\n[dry-run] nothing written")
        return 0

    # newline="" + \r\n is what Excel expects; utf-8-sig so the emoji and
    # accented names in these tables open correctly by double-click on Windows.
    with open(out_path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=out_cols)
        w.writeheader()
        w.writerows(rows_out)
    print(f"\n[out] {out_path}  ({os.path.getsize(out_path):,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
