"""Render the Highways `Clay Tables` rows for PASTING INTO GOOGLE SHEETS.

Two outputs, because the two paste routes behave differently:

  1. `... (paste into Sheets).html`  — open it in a browser, select the table,
     copy, paste into Sheets. Google Sheets honours `<a href>` from the HTML
     clipboard, so the link lands ON THE TEXT ("Buyers from FloodEx"), not as a
     visible URL. This is what the Labs tab looks like, and the result contains
     no formulas.

  2. `... (paste into Sheets).tsv` — the same grid with `=HYPERLINK(url, text)`
     in the linked cells. Paste it straight from a text editor when opening a
     browser is inconvenient; Sheets evaluates the formulas on paste.

TAB-SEPARATED, NEVER COMMA-SEPARATED. Category names carry commas of their own
("Buyers from Finance, Investment, Legal"), and a CSV paste would split that
into three cells and shift every column after it.

BOTH THE NAME AND THE LINK COLUMN ARE LINKED. The Highways tab keeps a separate
`Link to Clay Table` column (Labs folds the link into the name), so the name is
hyperlinked like Labs *and* column C is kept and hyperlinked — whichever
convention the sheet settles on, neither has to be rebuilt.

    python write_icp_clay_tables_paste.py
"""

import argparse
import html
import json
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(SCRIPT_DIR, "icp_clay_tables_highways.json")
BASE = os.path.join(SCRIPT_DIR, "ICP - Highways - Clay Tables (paste into Sheets)")

COLS = ["Name", "Promo", "Link to Clay Table", "Qty in Table",
        "# Emails or LinkedIn", "Status", "GTM", "List description",
        "List Number"]
ORDER = ["Competitive Events",
         "Other Sources / Associations / Directories",
         "Industries / Products & Services",
         "Buyside P&S"]


def tsv_formula(url, text):
    """=HYPERLINK for Sheets. Quotes inside the label are doubled, and a label
    is never allowed to start with a character Sheets would read as an operator.
    """
    return f'=HYPERLINK("{url}","{text.replace(chr(34), chr(34) * 2)}")'


def main():
    # `--src`/`--out` render another build (e.g. the post-fit-filter CE rows:
    # build_icp_clay_tables.py --delivery ce_fit --out icp_clay_tables_highways_ce_fit).
    global BASE
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--src", default=SRC, help="rows JSON from build_icp_clay_tables.py")
    ap.add_argument("--out", default=BASE, help="output path without extension")
    args = ap.parse_args()
    BASE = os.path.join(SCRIPT_DIR, args.out)
    rows = json.load(open(os.path.join(SCRIPT_DIR, args.src), encoding="utf-8"))
    for r in rows:
        for c in COLS:
            if isinstance(r.get(c), str) and ("\t" in r[c] or "\n" in r[c]):
                raise SystemExit(f"!! a value contains a tab/newline and would "
                                 f"break the paste: {r[c]!r}")

    # ---------------- HTML ----------------
    out = ["<!doctype html><meta charset='utf-8'>",
           "<style>",
           "body{font:13px -apple-system,Segoe UI,Roboto,sans-serif;padding:18px}",
           "table{border-collapse:collapse}",
           "td,th{border:1px solid #c6c6c6;padding:3px 7px;white-space:nowrap}",
           "th{background:#ddd;font-weight:700;text-align:left}",
           "tr.section td{background:#bdd7ee;font-weight:700;font-size:14px}",
           "tr.total td{font-weight:700;background:#f2f2f2}",
           "td.num{text-align:right}",
           "p.hint{font:13px sans-serif;background:#fff6d8;border:1px solid #e2c96b;"
           "padding:9px 12px;border-radius:6px;max-width:940px}",
           "</style>",
           "<p class='hint'><b>To paste into Google Sheets:</b> click just above "
           "the first row and drag to the end of the table (or press "
           "<b>Ctrl&nbsp;+&nbsp;A</b>), copy with <b>Ctrl&nbsp;+&nbsp;C</b>, then "
           "paste into the sheet with <b>Ctrl&nbsp;+&nbsp;V</b>. The hyperlinks "
           "come across on the text.</p>",
           "<table>"]
    grand = 0
    for section in ORDER:
        items = [x for x in rows if x["section"] == section]
        if not items:
            continue
        out.append(f"<tr class='section'><td colspan='{len(COLS)}'>"
                   f"{html.escape(section)}</td></tr>")
        out.append("<tr>" + "".join(f"<th>{html.escape(c)}</th>"
                                    for c in COLS) + "</tr>")
        for it in items:
            url = html.escape(it["Link to Clay Table"], quote=True)
            name = html.escape(it["Name"])
            cells = [f"<td><a href='{url}'>{name}</a></td>",
                     f"<td>{html.escape(it['Promo'])}</td>",
                     f"<td><a href='{url}'>{url}</a></td>",
                     f"<td class='num'>{it['Qty in Table']}</td>",
                     "<td></td>", "<td></td>",
                     f"<td>{html.escape(it['GTM'])}</td>",
                     f"<td>{html.escape(it['List description'])}</td>",
                     "<td></td>"]
            out.append("<tr>" + "".join(cells) + "</tr>")
        sub = sum(x["Qty in Table"] for x in items)
        grand += sub
        out.append(f"<tr class='total'><td>{len(items)} tables</td><td></td>"
                   f"<td></td><td class='num'>{sub}</td>"
                   + "<td></td>" * 5 + "</tr>")
        out.append(f"<tr><td colspan='{len(COLS)}'></td></tr>")
    out.append(f"<tr class='total'><td>TOTAL (people rows, all scopes)</td>"
               f"<td></td><td></td><td class='num'>{grand}</td>"
               + "<td></td>" * 5 + "</tr>")
    out.append("</table>")
    with open(BASE + ".html", "w", encoding="utf-8") as fh:
        fh.write("\n".join(out))

    # ---------------- TSV ----------------
    lines = []
    for section in ORDER:
        items = [x for x in rows if x["section"] == section]
        if not items:
            continue
        lines.append(section)
        lines.append("\t".join(COLS))
        for it in items:
            url = it["Link to Clay Table"]
            lines.append("\t".join([
                tsv_formula(url, it["Name"]),
                it["Promo"],
                tsv_formula(url, url),
                str(it["Qty in Table"]),
                "", "",
                it["GTM"],
                it["List description"],
                "",
            ]))
        sub = sum(x["Qty in Table"] for x in items)
        lines.append("\t".join([f"{len(items)} tables", "", "", str(sub)]))
        lines.append("")
    lines.append("\t".join(["TOTAL (people rows, all scopes)", "", "", str(grand)]))
    with open(BASE + ".tsv", "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")

    print(f"{len(rows)} people tables, {grand:,} rows")
    print(f"-> {BASE}.html   (open, select, copy, paste)")
    print(f"-> {BASE}.tsv    (paste directly; uses =HYPERLINK)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
