"""Render the Highways `Clay Tables` rows as a paste-ready .xlsx.

Reads `icp_clay_tables_highways.json` (from build_icp_clay_tables.py) and writes
a single-sheet workbook laid out EXACTLY like the Highways playbook's
`Clay Tables` tab, with live hyperlinks:

    A Name | B Promo | C Link to Clay Table | D Qty in Table |
    E # Emails or LinkedIn | F Status | G GTM | H List description | I List Number

A SEPARATE FILE, NOT AN EDIT OF THE PLAYBOOK. `ICP - Highways new.xlsx` is a
561 KB, 8-sheet workbook; round-tripping it through openpyxl silently drops
charts, images and conditional formatting it does not model. Copy-pasting these
rows into the real tab keeps the hyperlinks and risks nothing.

Section order follows the Highways tab, with `Buyside P&S` appended — that
section exists in the Labs playbook but is MISSING from the Highways one, so it
has to be added there by hand.

    python write_icp_clay_tables_xlsx.py
"""

import json
import os
import sys

import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(SCRIPT_DIR, "icp_clay_tables_highways.json")
OUT = os.path.join(SCRIPT_DIR, "ICP - Highways - Clay Tables (people).xlsx")

COLS = ["Name", "Promo", "Link to Clay Table", "Qty in Table",
        "# Emails or LinkedIn", "Status", "GTM", "List description",
        "List Number"]
WIDTHS = [52, 8, 74, 12, 20, 14, 10, 54, 12]
ORDER = ["Competitive Events",
         "Other Sources / Associations / Directories",
         "Industries / Products & Services",
         "Buyside P&S"]


def main():
    rows = json.load(open(SRC, encoding="utf-8"))
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Clay Tables"

    bold = Font(bold=True)
    section_font = Font(bold=True, size=12)
    head_fill = PatternFill("solid", fgColor="DDDDDD")
    sect_fill = PatternFill("solid", fgColor="BDD7EE")
    link_font = Font(color="0563C1", underline="single")

    for i, w in enumerate(WIDTHS, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w

    r = 1
    grand = 0
    for section in ORDER:
        items = [x for x in rows if x["section"] == section]
        if not items:
            continue
        ws.cell(r, 1, section).font = section_font
        ws.cell(r, 1).fill = sect_fill
        r += 1
        for c, name in enumerate(COLS, start=1):
            cell = ws.cell(r, c, name)
            cell.font = bold
            cell.fill = head_fill
        r += 1
        for it in items:
            ws.cell(r, 1, it["Name"])
            ws.cell(r, 2, it["Promo"])
            link = ws.cell(r, 3, it["Link to Clay Table"])
            link.hyperlink = it["Link to Clay Table"]
            link.font = link_font
            ws.cell(r, 4, it["Qty in Table"]).alignment = Alignment(horizontal="right")
            ws.cell(r, 5, it["# Emails or LinkedIn"])
            ws.cell(r, 6, it["Status"])
            ws.cell(r, 7, it["GTM"])
            ws.cell(r, 8, it["List description"])
            ws.cell(r, 9, it["List Number"])
            r += 1
        subtotal = sum(x["Qty in Table"] for x in items)
        grand += subtotal
        ws.cell(r, 1, f"{len(items)} tables").font = bold
        ws.cell(r, 4, subtotal).font = bold
        r += 2

    ws.cell(r, 1, "TOTAL (people rows, all scopes)").font = bold
    ws.cell(r, 4, grand).font = bold
    ws.freeze_panes = "A2"
    wb.save(OUT)
    print(f"{len(rows)} people tables, {grand:,} rows -> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
