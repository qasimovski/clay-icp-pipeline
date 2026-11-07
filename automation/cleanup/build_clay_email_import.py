"""Turn the people deliverable into a CSV that imports cleanly into Clay for the
work-email waterfall.

WHY A SEPARATE FILE rather than importing the deliverable as-is. Clay prefills a
card's inputs ONLY where an input's name exactly matches a column name on the
table; everything else has to be mapped by hand through the picker tree, and
docs/RUNBOOK.md step (h) records that hand-mapping silently mis-mapped a field
on an earlier pass. Renaming three columns to the names the waterfall asks for
turns four manual bindings into (at most) one.

WHAT IS RENAMED, and to what the card actually calls it:

    Person Name   -> Full Name        (card input "Full Name",     required)
    Domain        -> Company Domain   (card input "Company Domain", required)
    Company Name  -> Name             (function input "Name",       optional)

`LinkedIn URL` is deliberately NOT renamed. The function declares that input as
`Linked In Profile`, but the card in the column UI was observed presenting it as
`Social Profile URL` (recon 2026-07-27). The two disagree, no column name can
match both, and it is one field — map it by hand and check the label you are
actually shown. Only Wiza (step 6 of 9) consumes it, so a miss is survivable;
a wrong binding on Full Name or Company Domain is not.

`Outcome` is dropped: after the exclusion and duplicate passes every surviving
row carries the single value "found", so the column is a constant.

THE WATERFALL, as read from the live function on 2026-09-13
(`function:t_<the function's table id>`, "TERRAPINN find Work EMAIL", 1.2 credits/run):

    1 LeadMagic (catch-all ON)   4 Enrow       7 Prospeo (catch-all ON)
    2 Icypeas                    5 Findymail   8 Hunter
    3 Kitt                       6 Wiza *      9 Datagma

    * Clay marks the Wiza step deprecated in its own description.

All nine merge into one `WORK EMAIL` column, which is what the Validate Email
step then gates on.

    python build_clay_email_import.py            # full file + the 50-row pilot
    python build_clay_email_import.py --pilot 0  # full file only
"""

import argparse
import csv
import io
import os
import random
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

OUT_DIR = os.path.join(SCRIPT_DIR, "ce_classification_exports")
SOURCE = os.path.join(OUT_DIR, "Highways CE - people.csv")

# (source column, name it carries in the import). Order is the import order.
COLUMNS = (
    ("Person Name", "Full Name"),
    ("Job Title", "Job Title"),
    ("LinkedIn URL", "LinkedIn URL"),
    ("Company Name", "Name"),
    ("Domain", "Company Domain"),
    ("Side", "Side"),
    ("Classification", "Classification"),
    ("Company Event Name", "Company Event Name"),
    ("Exhibitor/Sponsor", "Exhibitor/Sponsor"),
    ("Country", "Country"),
    ("Website", "Website"),
)
# Same seed as the 50 people already prepared for the CLI pilot, so whichever
# route runs first, both are measuring the same sample.
PILOT_SEED = 20260913


def write(path, rows):
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=[dst for _, dst in COLUMNS])
        w.writeheader()
        for r in rows:
            w.writerow({dst: r.get(src, "") for src, dst in COLUMNS})
    return path


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", default=SOURCE)
    ap.add_argument("--pilot", type=int, default=50,
                    help="rows in the pilot file; 0 to skip it")
    args = ap.parse_args()

    if not os.path.exists(args.source):
        raise SystemExit(f"no people CSV at {args.source}")
    rows = list(csv.DictReader(open(args.source, encoding="utf-8-sig")))

    missing = [src for src, _ in COLUMNS if src not in (rows[0] if rows else {})]
    if missing:
        raise SystemExit(f"source is missing {missing} — refusing to write an "
                         f"import file with silently empty columns")

    full = write(os.path.join(OUT_DIR, "Highways CE - people - CLAY IMPORT.csv"), rows)
    print(f"{len(rows):,} rows -> {full}")

    if args.pilot:
        random.seed(PILOT_SEED)
        sample = random.sample(rows, min(args.pilot, len(rows)))
        pilot = write(os.path.join(
            OUT_DIR, f"Highways CE - people - CLAY IMPORT ({args.pilot} pilot).csv"),
            sample)
        print(f"{len(sample):,} rows -> {pilot}")
        print(f"   distinct companies: {len({r['Domain'] for r in sample})}"
              f"   events: {len({r['Company Event Name'] for r in sample})}")

    print("\ncolumn mapping applied:")
    for src, dst in COLUMNS:
        print(f"   {src:22} -> {dst}" + ("   (renamed)" if src != dst else ""))

    # The two required inputs must be present on every row, or the waterfall
    # burns a run to return nothing. Assert rather than assume.
    for _, dst in (("Person Name", "Full Name"), ("Domain", "Company Domain")):
        src = next(s for s, d in COLUMNS if d == dst)
        blank = sum(1 for r in rows if not str(r.get(src, "")).strip())
        print(f"\n{dst:16} blank on {blank} of {len(rows):,} rows"
              + ("  <-- FIX BEFORE RUNNING" if blank else "  (required input, all present)"))


if __name__ == "__main__":
    main()
