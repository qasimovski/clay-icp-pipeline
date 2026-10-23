"""Profile the inputs before anything is configured: scraped CSVs and the ICP workbook.

Prints headers, fill rates and a few sample values so Claude can PROPOSE which
column is the company name / website / description / country, and which ICP
sheet column holds job titles / countries / industries / seniority — and then
ASK the user to confirm. It decides nothing itself.

    python inspect_inputs.py csv "C:/…/scrapers_x/*/exhibitors_normalized.csv" [more globs]
    python inspect_inputs.py xlsx "C:/…/ICP - X new.xlsx" [--rows 12]
"""

import argparse
import collections
import csv
import glob
import sys

# Header words that usually mean a field. A hint only — always confirm with the user.
HINTS = {
    "company name": ("company", "name", "exhibitor", "sponsor", "organisation", "organization"),
    "website / domain": ("website", "domain", "url", "web"),
    "description": ("description", "about", "profile", "summary", "offering"),
    "country": ("country", "location", "hq"),
    "job titles": ("job title", "titles", "jt"),
    "industry / category": ("industry", "category", "classification", "top level", "sector"),
    "seniority": ("seniority", "level"),
    "match type": ("match",),
}


def hint(col):
    c = col.lower()
    return [k for k, words in HINTS.items() if any(w in c for w in words)]


def cmd_csv(patterns):
    files = sorted({f for p in patterns for f in glob.glob(p)})
    if not files:
        raise SystemExit("no files match")
    headers = collections.Counter()
    fill = collections.Counter()
    total = 0
    samples = collections.defaultdict(list)
    per_file = []
    for f in files:
        with open(f, encoding="utf-8-sig", newline="") as fh:
            r = csv.DictReader(fh)
            cols = r.fieldnames or []
            n = 0
            for row in r:
                n += 1
                for c in cols:
                    v = (row.get(c) or "").strip()
                    if v:
                        fill[c] += 1
                        if len(samples[c]) < 3 and v not in samples[c]:
                            samples[c].append(v[:60])
            total += n
            headers.update(cols)
            per_file.append((f, n, len(cols)))
    print(f"{len(files)} files, {total:,} rows\n")
    for f, n, k in per_file[:60]:
        print(f"  {n:6,} rows  {k:2} cols  {f}")
    if len(per_file) > 60:
        print(f"  … {len(per_file) - 60} more")
    print(f"\n{'column':34} {'in files':>8} {'filled':>7}  hint / samples")
    for c, nfiles in headers.most_common():
        pct = f"{fill[c] / total:.0%}" if total else "-"
        h = ",".join(hint(c))
        print(f"{c[:34]:34} {nfiles:>8} {pct:>7}  {('[' + h + '] ') if h else ''}{samples[c]}")
    odd = [c for c, n in headers.items() if n != len(files)]
    if odd:
        print(f"\ncolumns NOT in every file (ask how to treat them): {odd}")


def cmd_xlsx(path, rows):
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    for ws in wb.worksheets:
        print(f"\n=== tab: {ws.title}")
        shown = 0
        for i, row in enumerate(ws.iter_rows(values_only=True), 1):
            vals = [(j, str(v).replace("\n", " ")[:45]) for j, v in enumerate(row) if v is not None]
            if not vals:
                continue
            letters = " | ".join(f"{chr(65 + j) if j < 26 else j}:{v}" for j, v in vals)
            print(f"  r{i:<4} {letters}")
            shown += 1
            if shown >= rows:
                print("  …")
                break


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("kind", choices=["csv", "xlsx"])
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--rows", type=int, default=12, help="xlsx: rows shown per tab")
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if a.kind == "csv":
        cmd_csv(a.paths)
    else:
        cmd_xlsx(a.paths[0], a.rows)


if __name__ == "__main__":
    main()
