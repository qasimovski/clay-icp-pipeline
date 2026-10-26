"""Delivery: Clay workbook CSVs, the Salesforce CSV, and the Clay Tables sheet rows.

    split       classified + people -> <out>/<event>/<Table>.csv + plan.json
                (the plan is what clay-icp-pipeline's import_ce_delivery_tables.py reads)
                --decisions: the review handoff - company tables hold every scraped row with its
                Decision/Reason, people are placed in every event their company is kept at, and
                every person carries an Outreach Channel
    salesforce  people (+ Supabase emails) -> Salesforce-shaped CSV, columns from a schema file
    rows        importer plan + state -> paste-ready HTML/TSV for the ICP sheet's Clay Tables tab

    python deliver.py split --classified classified.csv --people people.csv --out delivery/ --plan plan.json
    python deliver.py split --decisions remap/decisions.csv --people "<S> - review.csv" \
        --linkedin-only "<S> - review - LinkedIn only.csv" [--exclude-source Speaker] \
        --out event_tables/ --plan plan.json
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
COMPANY_COLS = ["Company Name", "Website", "Domain", "Country", "Side", "Classification", "Decision",
                "Reason", "People Found"]
SIDE_WORD = {"Buy Side": "Buyside", "Sell Side": "Sellside"}
TABLE_ORDER = lambda t: (t.split(" ")[0], "People" in t, t)  # noqa: E731  Exhibitors, its People, Sponsors...


def norm_dom(d):
    d = re.sub(r"^https?://", "", (d or "").strip().lower()).split("/")[0]
    return d[4:] if d.startswith("www.") else d


def write_plan(a, groups):
    """groups: {(event, table): (header, rows)} -> one CSV per table + the importer's plan."""
    plan = []
    for ev in sorted({e for e, _ in groups}):
        d = os.path.join(a.out, safe_dir(ev))
        os.makedirs(d, exist_ok=True)
        for t in sorted((t for e, t in groups if e == ev), key=TABLE_ORDER):
            header, rows = groups[(ev, t)]
            if not rows:
                continue                   # no empty tables
            p = os.path.join(d, t + ".csv")
            write_csv(p, header, rows)
            plan.append({"event": ev, "table": t, "kind": "people" if t.endswith("People") else "companies",
                         "csv": os.path.abspath(p), "rows": len(rows)})
    json.dump(plan, open(a.plan, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    return plan


def split_by_decisions(a):
    """The review handoff (Highways convention, Mining Oct 2026): each event's company tables carry
    EVERY scraped row with its Decision and Reason, so the manager sees what was left out and why;
    a person is placed in every event table where their company (by domain) is a KEEP row, so the
    same person can sit in several events' tables. Rows from --linkedin-only files are tagged
    Outreach Channel = --linkedin-only-label and must never reach the email step."""
    _, decisions = read_csv(a.decisions)
    people = []
    for path, channel in [(p, a.channel) for p in a.people] + [(p, a.linkedin_only_label) for p in a.linkedin_only]:
        ph, rows = read_csv(path)
        for r in rows:
            if a.exclude_source and r.get("Source") in a.exclude_source:
                continue
            people.append(dict(r, **{"Outreach Channel": channel}))
    if not people:
        raise SystemExit("no people to place")
    p_cols = ["Event", a.entity_col] + [c for c in people[0] if c not in
                                        ("Event", "Entity", a.entity_col, "Outreach Channel")] + ["Outreach Channel"]
    by_dom = collections.defaultdict(list)
    for p in people:
        if p.get("Side") not in SIDE_WORD:
            raise SystemExit(f"person with Side {p.get('Side')!r} — refusing to split and lose them")
        by_dom[norm_dom(p.get("Domain"))].append(p)
    groups, placed = collections.OrderedDict(), set()
    for d in decisions:
        ev, ent = a.single_workbook or d["Event"], plural(d.get("Entity") or "Companies")
        dom, dec = norm_dom(d.get("Domain")), d.get("Decision", "")
        if dec == "CLASSIFY":
            dec, d = "UNCLASSIFIED", dict(d, Reason="type unknown; not classified, not searched")
        found = by_dom.get(dom, []) if dec == "KEEP" and dom else []
        groups.setdefault((ev, ent), (COMPANY_COLS, []))[1].append(
            {**{c: d.get(c, "") for c in COMPANY_COLS}, "Decision": dec, "People Found": len(found)})
        for p in found:
            t = f"{ent} {SIDE_WORD[p['Side']]} People"
            rows = groups.setdefault((ev, t), (p_cols, []))[1]
            if all(x.get("LinkedIn URL") != p.get("LinkedIn URL") for x in rows):
                rows.append(dict(p, **{"Event": ev, a.entity_col: d.get("Entity") or ent}))
                placed.add(p.get("LinkedIn URL"))
    plan = write_plan(a, groups)
    unplaced = {p.get("LinkedIn URL") for p in people} - placed
    print(f"{len({p['event'] for p in plan})} workbooks, {len(plan)} tables -> {a.out}\nplan -> {a.plan}")
    print(f"company rows {sum(e['rows'] for e in plan if e['kind'] == 'companies'):,} | people rows "
          f"{sum(e['rows'] for e in plan if e['kind'] == 'people'):,} ({len(placed):,} distinct people; one "
          f"at several events appears in each)")
    if unplaced:
        print(f"!! {len(unplaced):,} people have no KEEP company row to sit under (domain mismatch?) — "
              f"look before importing")


def cmd_split(a):
    if a.decisions:
        return split_by_decisions(a)
    if not a.classified or len(a.people) != 1:
        raise SystemExit("split needs --classified and one --people (or --decisions)")
    ch, companies = read_csv(a.classified)
    ph, people = read_csv(a.people[0])
    for need in ("Event", "Entity"):
        if need not in ch or need not in ph:
            raise SystemExit(f"both CSVs need an {need!r} column (build_feed.py writes it)")
    groups = collections.OrderedDict()
    for r in companies:
        ev = a.single_workbook or r["Event"]
        groups.setdefault((ev, plural(r["Entity"])), (ch, []))[1].append(r)
    for r in people:
        if r.get("Side") not in SIDE_WORD:
            raise SystemExit(f"person with Side {r.get('Side')!r} — refusing to split and lose them")
        ev = a.single_workbook or r["Event"]
        t = f"{plural(r['Entity'])} {SIDE_WORD[r['Side']]} People"
        groups.setdefault((ev, t), (ph, []))[1].append(r)
    plan = write_plan(a, groups)
    if sum(e["rows"] for e in plan) != len(companies) + len(people):
        raise SystemExit("lossy split — row counts do not add up")
    print(f"{len({p['event'] for p in plan})} workbooks, {len(plan)} tables, "
          f"{len(companies):,} company + {len(people):,} people rows -> {a.out}\nplan -> {a.plan}")


# ------------------------------------------------------------------ salesforce
# Salesforce column -> people-CSV column, or a callable. Defaults follow the
# Competitive Events upload (2026-09): Buy-side/Sell-side, SPEX = seller,
# Attendee = buyer, Account Name = Company Name, status+sub_status.
# Speaker rows follow the earlier uploads (Mining, Oct 2026): Is CA Speaker True; a speaker-only
# row is not SPEX / Attendee and has no Classification. `_company` / `_speaker` say, per output row,
# whether the person is there for their company or as a speaker at THAT event (see --explode-events).
def _is_company(r):
    return r.get("_company", r.get("Source", "Find People") != "Speaker")


def _is_speaker(r):
    return r.get("_speaker", "Speaker" in (r.get("Source") or ""))


DEFAULT_MAP = {
    "Competitive Event Name": "Event",
    "Data Source": lambda r: "Clay",
    "Classification": lambda r: {"Buy Side": "Buy-side", "Sell Side": "Sell-side"}.get(r.get("Side"), "")
    if _is_company(r) else "",
    "Is CA SPEX": lambda r: str(_is_company(r) and r.get("Side") == "Sell Side"),
    "Is CA Attendee": lambda r: str(_is_company(r) and r.get("Side") == "Buy Side"),
    "Is CA Speaker": lambda r: str(bool(_is_speaker(r))),
    "Size": "Company Size", "Type": "Company Type", "Industry": "Company Industry",
    "Description": "Company Description",
    "First Name": "First Name", "Last Name": "Last Name", "Job Title": "Job Title",
    "Account Name": "Company Name", "Company Name": "Company Name",
    "Website URL": lambda r: r.get("Website") or r.get("Company Website") or "", "Mailing City": "Mailing City",
    "Mailing State Province": "Mailing State Province", "Mailing Country": "Mailing Country",
    "Person Linkedin Url": "LinkedIn URL", "Contact Email": "Contact Email",
    "Email Validation Status": lambda r: "+".join(x for x in (r.get("Email Validation Status"),
                                                               r.get("Email Sub Status")) if x),
    "MX Provider": "MX Provider", "Validated by Clay Date Stamp": "Validated At",
}


def explode(people):
    """One Salesforce row per person per event (the Competitive Events upload is per event): the
    company's events (`Events`, "; "-joined) plus the person's speaker events (`Speaker Event(s)`).
    Rows that already carry an `Event` are left as they are."""
    out = []
    for r in people:
        if r.get("Event"):
            out.append(r)
            continue
        split = lambda v: [e.strip() for e in (v or "").split(";") if e.strip()]  # noqa: E731
        company = split(r.get("Events")) if r.get("Source", "Find People") != "Speaker" else []
        speaker = split(r.get("Speaker Event(s)"))
        for ev in dict.fromkeys(company + speaker):
            out.append(dict(r, Event=ev, _company=ev in company, _speaker=ev in speaker))
    return out


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
    if a.explode_events:
        people = explode(people)
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
    s.add_argument("--classified", help="classified.csv (the plain split: one row per company row)")
    s.add_argument("--decisions", help="remap_prior.py decisions.csv: every scraped row with its "
                                       "Decision + Reason; people are placed by domain")
    s.add_argument("--people", action="append", default=[], required=True,
                   help="people CSV(s); with --decisions, the review list(s)")
    s.add_argument("--linkedin-only", action="append", default=[],
                   help="people in LinkedIn-only countries (post_filter.py); tagged, never emailed")
    s.add_argument("--channel", default="Email + LinkedIn", help="Outreach Channel for --people rows")
    s.add_argument("--linkedin-only-label", default="LinkedIn + phone only",
                   help='Outreach Channel for --linkedin-only rows (Mining: "LinkedIn + phone only (Germany)")')
    s.add_argument("--exclude-source", action="append", default=[],
                   help="leave out people whose Source is this (e.g. Speaker, when speakers go apart)")
    s.add_argument("--entity-col", default="Exhibitor/Sponsor", help="people tables' entity column name")
    s.add_argument("--out", required=True)
    s.add_argument("--plan", required=True)
    s.add_argument("--single-workbook", help="one workbook for everything (directory-style sources)")
    f = sub.add_parser("salesforce")
    f.add_argument("--people", required=True)
    f.add_argument("--schema", required=True, help="a Salesforce export whose header is the target")
    f.add_argument("--out", required=True)
    f.add_argument("--event-ids")
    f.add_argument("--map", help="YAML {Salesforce column: people column or ''} overrides")
    f.add_argument("--explode-events", action="store_true",
                   help="one row per person per event, from Events + Speaker Event(s) (the review list's shape)")
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
