"""Delivery: Clay workbook CSVs, the Salesforce CSV, and the Clay Tables sheet rows.

    split       classified + people -> <out>/<event>/<Table>.csv + plan.json
                (the plan is what clay-icp-pipeline's import_ce_delivery_tables.py reads)
    salesforce  people (+ Supabase emails) -> Salesforce-shaped CSV, columns from a schema file
    rows        importer plan + state -> paste-ready HTML/TSV for the ICP sheet's Clay Tables tab

    python deliver.py split --classified classified.csv --people people.csv --out delivery/ --plan plan.json
    python deliver.py salesforce --people "people - supabase.csv" --schema "Competitive Events.csv" \
        --out salesforce.csv [--event-ids event_ids.csv] [--map sf_map.yaml]
    python deliver.py rows --plan plan.json --state state.json --prefix Seamless --out "Clay Tables rows"

THE CSV FILENAME IS THE TABLE NAME: Clay names an imported table after the file
stem, and renaming afterwards is manual UI work.
"""

import argparse
import collections
import csv
import html
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def read_csv(path):
    with open(path, encoding="utf-8-sig", newline="") as fh:
        r = csv.DictReader(fh)
        return list(r.fieldnames or []), list(r)


def write_csv(path, header, rows):
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=header, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def safe_dir(name):
    return re.sub(r'[<>:"/\\|?*]', "_", name).strip().rstrip(".")


def plural(entity):
    """Table-name form of an entity label: Exhibitor -> Exhibitors, Directory -> Directories."""
    if entity.endswith("s"):
        return entity
    if entity.endswith("y") and entity[-2:-1] not in "aeiou":
        return entity[:-1] + "ies"
    return entity + "s"


# ------------------------------------------------------------------ split
def cmd_split(a):
    ch, companies = read_csv(a.classified)
    ph, people = read_csv(a.people)
    for need in ("Event", "Entity"):
        if need not in ch or need not in ph:
            raise SystemExit(f"both CSVs need an {need!r} column (build_feed.py writes it)")
    groups = collections.OrderedDict()
    for r in companies:
        ev = a.single_workbook or r["Event"]
        groups.setdefault((ev, plural(r["Entity"])), (ch, []))[1].append(r)
    side_word = {"Buy Side": "Buyside", "Sell Side": "Sellside"}
    for r in people:
        if r.get("Side") not in side_word:
            raise SystemExit(f"person with Side {r.get('Side')!r} — refusing to split and lose them")
        ev = a.single_workbook or r["Event"]
        t = f"{plural(r['Entity'])} {side_word[r['Side']]} People"
        groups.setdefault((ev, t), (ph, []))[1].append(r)
    plan, wrote = [], 0
    for ev in sorted({e for e, _ in groups}):
        d = os.path.join(a.out, safe_dir(ev))
        os.makedirs(d, exist_ok=True)
        tables = sorted((t for e, t in groups if e == ev),
                        key=lambda t: (t.split(" ")[0], "People" in t, t))
        for t in tables:
            header, rows = groups[(ev, t)]
            if not rows:
                continue
            p = os.path.join(d, t + ".csv")
            write_csv(p, header, rows)
            kind = "people" if t.endswith("People") else "companies"
            plan.append({"event": ev, "table": t, "kind": kind,
                         "csv": os.path.abspath(p), "rows": len(rows)})
            wrote += len(rows)
    if wrote != len(companies) + len(people):
        raise SystemExit("lossy split — row counts do not add up")
    json.dump(plan, open(a.plan, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    print(f"{len({p['event'] for p in plan})} workbooks, {len(plan)} tables, "
          f"{len(companies):,} company + {len(people):,} people rows -> {a.out}\nplan -> {a.plan}")


# ------------------------------------------------------------------ salesforce
# Salesforce column -> people-CSV column, or a callable. Defaults follow the
# Competitive Events upload (2026-09): Buy-side/Sell-side, SPEX = seller,
# Attendee = buyer, Account Name = Company Name, status+sub_status.
DEFAULT_MAP = {
    "Competitive Event Name": "Event",
    "Data Source": lambda r: "Clay",
    "Classification": lambda r: {"Buy Side": "Buy-side", "Sell Side": "Sell-side"}.get(r.get("Side"), ""),
    "Is CA SPEX": lambda r: str(r.get("Side") == "Sell Side"),
    "Is CA Attendee": lambda r: str(r.get("Side") == "Buy Side"),
    "Is CA Speaker": lambda r: "False",
    "First Name": "First Name", "Last Name": "Last Name", "Job Title": "Job Title",
    "Account Name": "Company Name", "Company Name": "Company Name",
    "Website URL": "Website", "Mailing City": "Mailing City",
    "Mailing State Province": "Mailing State Province", "Mailing Country": "Mailing Country",
    "Person Linkedin Url": "LinkedIn URL", "Contact Email": "Contact Email",
    "Email Validation Status": lambda r: "+".join(x for x in (r.get("Email Validation Status"),
                                                               r.get("Email Sub Status")) if x),
    "MX Provider": "MX Provider", "Validated by Clay Date Stamp": "Validated At",
}


def cmd_salesforce(a):
    # Header only: a real Salesforce export can be 100 MB.
    with open(a.schema, encoding="utf-8-sig", newline="") as fh:
        schema = next(csv.reader(fh))
    ph, people = read_csv(a.people)
    mapping = dict(DEFAULT_MAP)
    if a.map:
        import yaml
        for k, v in (yaml.safe_load(open(a.map, encoding="utf-8")) or {}).items():
            mapping[k] = v                     # a people-CSV column name, or "" to leave blank
    # A schema column with the same name in the people CSV maps to itself —
    # compared loosely, because Salesforce headers carry strays like "Founded ".
    loose = {c.strip().lower(): c for c in ph}
    for col in schema:
        if col not in mapping and col.strip().lower() in loose:
            mapping[col] = loose[col.strip().lower()]
    ids = {}
    if a.event_ids:
        _, er = read_csv(a.event_ids)
        ids = {r["Event"]: r["Id"] for r in er}
        mapping["Competitive Event Id"] = lambda r: ids.get(r.get("Event"), "")
    out, filled = [], collections.Counter()
    for r in people:
        row = {}
        for col in schema:
            src = mapping.get(col)
            v = src(r) if callable(src) else (r.get(src, "") if src else "")
            row[col] = v
            if v:
                filled[col] += 1
        out.append(row)
    write_csv(a.out, schema, out)
    print(f"{len(out):,} rows, {len(schema)} Salesforce columns -> {a.out}")
    empty = [c for c in schema if not filled[c]]
    unmapped = [c for c in schema if c not in mapping]
    print("filled per column:")
    for c in schema:
        print(f"  {c:34} {filled[c]:7,}")
    if unmapped or empty:
        print("\nASK THE USER about these columns before uploading:")
        print(f"  no source mapped : {unmapped}")
        print(f"  empty in every row: {[c for c in empty if c not in unmapped]}")
    if not ids:
        print("  Competitive Event Id: pass --event-ids (CSV Event,Id) — the Salesforce record per event")


# ------------------------------------------------------------------ rows
def cmd_rows(a):
    from _clay import clay
    plan = json.load(open(a.plan, encoding="utf-8"))
    state = json.load(open(a.state, encoding="utf-8"))
    ws = a.workspace
    wb_of = {k.split("||")[0]: v.get("workbook_id") for k, v in state.items() if v.get("workbook_id")}
    wanted = set(wb_of.values())
    tables, cursor = {}, None
    for _ in range(400):
        d = clay(["tables", "list", "--limit", "100"] + (["--cursor", cursor] if cursor else []))
        for t in d.get("data") or []:
            w = (t.get("workbook") or {}).get("id")
            if w in wanted:
                tables[(w, t["name"])] = t["id"]
        cursor = d.get("cursor")
        if not cursor:
            break
    rows, missing = [], []
    for e in sorted(plan, key=lambda e: (e["event"].lower(), e["table"])):
        if e["kind"] != "people":
            continue
        wid = wb_of.get(e["event"])
        tid = tables.get((wid, e["table"]))
        if not tid:
            missing.append((e["event"], e["table"]))
            continue
        buy = "Buyside" in e["table"]
        suffix = "" if e["table"].startswith("Exhibitors") or a.no_entity_suffix else \
            " - " + e["table"].split(" ")[0]
        name = f"{'Buyers' if buy else 'Sellers'} from {e['event']}{suffix}"
        rows.append({"Name": name, "Promo": "CONF" if buy else "SPEX",
                     "Link": f"https://app.clay.com/workspaces/{ws}/workbooks/{wid}/tables/{tid}",
                     "Qty": e["rows"], "GTM": a.gtm, "List description": f"{a.prefix}_{name}"})
    cols = ["Name", "Promo", "Link to Clay Table", "Qty in Table", "# Emails or LinkedIn",
            "Status", "GTM", "List description", "List Number"]
    h = ["<!doctype html><meta charset='utf-8'><style>td,th{border:1px solid #ccc;padding:3px 7px;"
         "white-space:nowrap}table{border-collapse:collapse}</style>"
         "<p>Select the table (Ctrl+A), copy, paste into the sheet — links land on the text.</p><table><tr>"
         + "".join(f"<th>{c}</th>" for c in cols) + "</tr>"]
    t = ["\t".join(cols)]
    for r in rows:
        u, n = html.escape(r["Link"], quote=True), html.escape(r["Name"])
        h.append(f"<tr><td><a href='{u}'>{n}</a></td><td>{r['Promo']}</td><td><a href='{u}'>{u}</a></td>"
                 f"<td>{r['Qty']}</td><td></td><td></td><td>{html.escape(r['GTM'])}</td>"
                 f"<td>{html.escape(r['List description'])}</td><td></td></tr>")
        q = r["Name"].replace('"', '""')
        t.append("\t".join([f'=HYPERLINK("{r["Link"]}","{q}")', r["Promo"],
                            f'=HYPERLINK("{r["Link"]}","{r["Link"]}")', str(r["Qty"]),
                            "", "", r["GTM"], r["List description"], ""]))
    h.append(f"<tr><td><b>{len(rows)} tables</b></td><td></td><td></td>"
             f"<td><b>{sum(r['Qty'] for r in rows)}</b></td></tr></table>")
    open(a.out + ".html", "w", encoding="utf-8").write("\n".join(h))
    open(a.out + ".tsv", "w", encoding="utf-8", newline="\n").write("\n".join(t) + "\n")
    print(f"{len(rows)} people tables, {sum(r['Qty'] for r in rows):,} rows -> {a.out}.html / .tsv")
    if missing:
        print(f"!! {len(missing)} planned tables not found in Clay (import incomplete?): {missing[:5]}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("split")
    s.add_argument("--classified", required=True)
    s.add_argument("--people", required=True)
    s.add_argument("--out", required=True)
    s.add_argument("--plan", required=True)
    s.add_argument("--single-workbook", help="one workbook for everything (directory-style sources)")
    f = sub.add_parser("salesforce")
    f.add_argument("--people", required=True)
    f.add_argument("--schema", required=True, help="a Salesforce export whose header is the target")
    f.add_argument("--out", required=True)
    f.add_argument("--event-ids")
    f.add_argument("--map", help="YAML {Salesforce column: people column or ''} overrides")
    r = sub.add_parser("rows")
    r.add_argument("--plan", required=True)
    r.add_argument("--state", required=True)
    r.add_argument("--prefix", required=True, help="List description prefix, e.g. Seamless")
    r.add_argument("--workspace", required=True, help="Clay workspace id")
    r.add_argument("--gtm", default="")
    r.add_argument("--no-entity-suffix", action="store_true")
    r.add_argument("--out", required=True, help="output path without extension")
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    {"split": cmd_split, "salesforce": cmd_salesforce, "rows": cmd_rows}[a.cmd](a)


if __name__ == "__main__":
    main()
