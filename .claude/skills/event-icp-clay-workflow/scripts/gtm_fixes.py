"""Apply the marketing manager's per-table review to the LOCAL delivery files. Clay is never touched.

The manager's sheet comes back with one row per Clay people table: the Clay Tables rows from `deliver.py rows`,
plus their own columns (Verdict, What GTM needs to do, Contacts now, Removed - companies / titles / countries /
cap, Contacts after, LinkedIn-only contacts, ...).

User rule (ClimaTech, 10 Nov 2026):
  - a Verdict of ACCEPT needs nothing;
  - every other verdict ("ACCEPT AFTER CLEAN-UP", "NEEDS FIXING", ...) is fixed in the local CSVs only,
    following the "What GTM needs to do" column;
  - the Clay tables are NOT re-imported or edited.

What changes, per flagged table. A removal covers only THAT event + entity, the table the manager looked at. The
same company in another event's table, which the manager accepted, stays.
  1. event_tables/<Event>/<Entity> <Side>side People.csv: the company's rows are removed.
  2. decisions_delivery.csv and event_tables/<Event>/<Entity>.csv: the company's KEEP row at that event + entity
     becomes DROP "removed in GTM review (<by>, <date>): <reason>".
  3. The review list(s) (--review, repeatable), which approval.py / the email workflow / deliver.py salesforce
     read next:
       - the event leaves a person's Event column once the company has no KEEP row left at that event (a company
         that is also a sponsor there keeps the event);
       - a person left with no Event and no Speaker Event(s) is removed.
Every file touched is backed up first (backup_pre_gtm_<stamp>/). Removed rows go to "gtm fixes - removed.csv".
Re-running is safe: a company already removed is reported as "already applied".

What it understands in "What GTM needs to do". Anything else stops the run, so nothing is guessed: ask the user,
then extend this parser.
  - "Delete these companies (all contacts): A (17); B (3)."
      -> removes those companies. Each (n) must equal that company's rows in the table.
  - "Enrich the N contacts in Germany with LinkedIn URL only - no email, no phone."
      -> checks that N LinkedIn-only rows (Outreach Channel "LinkedIn only ...") remain. Nothing to change: they
         are already split off.
  - "Accept after clean-up." / "Remove X of Y contacts (p%) before enriching." / "Reason: ..."
      -> context. X is checked against what was removed and Y against the table (a warning, not a stop:
         ClimaTech's EnerGaia row said 101 where its company counts sum to 100).
A non-zero "Removed - titles", "Removed - countries" or "Removed - cap" with no instruction it can parse also stops.

    python gtm_fixes.py --sheet "<manager review>.tsv|.csv" --final-dir <people/final> \
        --review "<S> - review.csv" --review "<S> - review - LinkedIn only.csv" --by "<manager>"   # dry run
    python gtm_fixes.py ... --apply
"""

import argparse
import collections
import csv
import datetime
import json
import os
import re
import shutil
import sys

csv.field_size_limit(10**9)

DELETE = re.compile(r"Delete these companies \(all contacts\):\s*((?:[^;]+?\(\d+\)(?:;\s*)?)+)\.")
REASON = re.compile(r"Reason:\s*(.+?\))\.|Reason:\s*([^.]+)\.")
GEO = re.compile(r"Enrich the (\d+) contacts in ([A-Za-z .'-]+?) with LinkedIn URL only[^.]*\.")
REMOVE = re.compile(r"Remove (\d+) of (\d+) contacts \([\d.]+%\) before enriching\.")
CONTEXT = re.compile(r"Accept after clean-up\.|Accept\.", re.I)
ITEM = re.compile(r"^(.*?)\s*\((\d+)\)$")


def read(p):
    with open(p, encoding="utf-8-sig", newline="") as fh:
        r = csv.DictReader(fh, delimiter="\t" if p.lower().endswith(".tsv") else ",")
        return list(r.fieldnames or []), list(r)


def write(p, header, rows):
    with open(p, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=header, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def norm(s):
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def events(v):
    return [e.strip() for e in (v or "").split(";") if e.strip()]


def parse(text):
    """-> dict(deletes=[(name, n)], reason, geo=[(n, country)], remove=(x, y) or None); SystemExit on leftovers."""
    left, out = text or "", {"deletes": [], "reason": "", "geo": [], "remove": None}
    m = DELETE.search(left)
    if m:
        for part in m.group(1).split(";"):
            im = ITEM.match(part.strip())
            if not im:
                raise SystemExit(f"cannot read company item {part!r} in: {text}")
            out["deletes"].append((im.group(1).strip(), int(im.group(2))))
        left = left.replace(m.group(0), " ")
    m = REASON.search(left)
    if m:
        out["reason"] = (m.group(1) or m.group(2)).strip()
        left = left.replace(m.group(0), " ")
    for m in list(GEO.finditer(left)):
        out["geo"].append((int(m.group(1)), m.group(2).strip()))
        left = left.replace(m.group(0), " ")
    m = REMOVE.search(left)
    if m:
        out["remove"] = (int(m.group(1)), int(m.group(2)))
        left = left.replace(m.group(0), " ")
    left = CONTEXT.sub(" ", left).strip()
    if left:
        raise SystemExit(f"instruction not understood (ask the user, then extend gtm_fixes.parse): {left!r}\n  in: {text}")
    return out


def table_for(row, state):
    """Sheet row -> (event, entity plural, people table name) via the workbook id in its Clay link + its Name."""
    link, name = row.get("Link to Clay Table") or "", (row.get("Name") or "").strip()
    wb = re.search(r"workbooks/(wb_[A-Za-z0-9]+)", link)
    evs = {k.split("||")[0] for k, v in state.items() if wb and v.get("workbook_id") == wb.group(1)}
    if len(evs) != 1:
        raise SystemExit(f"{name}: workbook in {link!r} matches {sorted(evs) or 'no event'} in the import state")
    side = "Sellside" if name.startswith("Sellers") else "Buyside" if name.startswith("Buyers") else None
    if not side:
        raise SystemExit(f"{name}: Name does not start with Sellers / Buyers")
    m = re.search(r" - ([A-Za-z]+)$", name)
    entity = m.group(1) if m else "Exhibitors"
    return evs.pop(), entity, f"{entity} {side} People"


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="Apply the manager's per-table review to the local CSVs (not Clay)")
    ap.add_argument("--sheet", required=True, help="the manager's review sheet (.tsv or .csv)")
    ap.add_argument("--final-dir", required=True, help="folder with event_tables/, import_state.json, decisions")
    ap.add_argument("--state", default="import_state.json")
    ap.add_argument("--decisions", default="decisions_delivery.csv")
    ap.add_argument("--review", action="append", default=[], help="review list file(s) to keep in step")
    ap.add_argument("--by", required=True, help="the manager's name (goes into the Reason)")
    ap.add_argument("--apply", action="store_true", help="write the changes (default: dry run)")
    a = ap.parse_args()
    D = a.final_dir
    J = lambda *p: os.path.join(D, *p)  # noqa: E731
    state = json.load(open(J(a.state), encoding="utf-8"))
    _, sheet = read(a.sheet)
    today = datetime.date.today().isoformat()
    dh, dec = read(J(a.decisions))

    log = J("gtm fixes - removed.csv")
    done_before = {(r.get("Removed From"), norm(r["Company Name"])) for r in (read(log)[1] if os.path.exists(log) else [])}
    work, removed, warns, verdicts = [], [], [], collections.Counter()
    for s in sheet:
        v = (s.get("Verdict") or "").strip().upper()
        verdicts[v or "(blank)"] += 1
        if v in ("ACCEPT", ""):
            continue
        ins = parse(s.get("What GTM needs to do"))
        for col in ("Removed - titles", "Removed - countries (incl. geo red flags)", "Removed - cap"):
            if (s.get(col) or "0").strip() not in ("", "0"):
                raise SystemExit(f"{s['Name']}: {col} = {s[col]} but no instruction for it is understood; ask the user")
        ev, ent, tb = table_for(s, state)
        path = J("event_tables", ev, tb + ".csv")
        ph, prow = read(path)
        if s.get("Contacts now") and int(s["Contacts now"]) != len(prow) and not any(
                f == ev + " / " + tb for f, _ in done_before):
            warns.append(f"{s['Name']}: sheet says {s['Contacts now']} contacts, local table has {len(prow)}")
        names = {norm(n): (n, k) for n, k in ins["deletes"]}
        hit = collections.Counter(norm(r["Company Name"]) for r in prow if norm(r["Company Name"]) in names)
        doms = {norm(r["Company Name"]): r["Domain"] for r in prow if norm(r["Company Name"]) in names}
        for key, (n, k) in names.items():
            if not hit[key]:
                done = (ev + " / " + tb, key) in done_before
                if not done:
                    raise SystemExit(f"{s['Name']}: company {n!r} is not in {path}")
                print(f"  {s['Name']}: {n} already applied")
            elif hit[key] != k:
                raise SystemExit(f"{s['Name']}: {n} has {hit[key]} rows locally, the sheet says {k}; ask the user")
        keep = [r for r in prow if norm(r["Company Name"]) not in names]
        gone = [dict(r, **{"Removed From": f"{ev} / {tb}", "Removed Because": ins["reason"]}) for r in prow
                if norm(r["Company Name"]) in names]
        removed += gone
        if ins["remove"] and gone and ins["remove"][0] != len(gone):
            warns.append(f"{s['Name']}: sheet says remove {ins['remove'][0]}, its company counts give {len(gone)}")
        for n_geo, country in ins["geo"]:
            got = sum(1 for r in keep if (r.get("Outreach Channel") or "").startswith("LinkedIn only")
                      and norm(r.get("Mailing Country")) == norm(country))
            (warns.append if got != n_geo else lambda _: None)(
                f"{s['Name']}: sheet says {n_geo} LinkedIn-only contacts in {country}, {got} remain")
        work.append((s, ev, ent, tb, path, ph, keep, gone, set(doms.values()), ins))
        print(f"{s['Name']}  [{v}]  {ev} / {tb}: {len(prow)} -> {len(keep)} (remove {len(gone)} at "
              f"{len(doms)} companies; LinkedIn-only left {sum(1 for r in keep if (r.get('Outreach Channel') or '').startswith('LinkedIn only'))})")

    # decisions: the KEEP row of each removed company at that event + entity -> DROP
    flips = 0
    for s, ev, ent, tb, path, ph, keep, gone, doms, ins in work:
        why = f"removed in GTM review ({a.by}, {today}): {ins['reason'] or 'per the manager'}"
        for r in dec:
            if r["Event"] == ev and r["Entity"] + "s" == ent and r["Domain"] in doms and r["Decision"] == "KEEP":
                r.update(Decision="DROP", Reason=why)
                flips += 1
    keep_at = {(r["Event"], r["Domain"]) for r in dec if r["Decision"] == "KEEP"}
    lost = {(ev, d) for _, ev, _, _, _, _, _, _, doms, _ in work for d in doms if (ev, d) not in keep_at}

    rev_out = []
    for p in a.review:
        rh, rr = read(p)
        kept, n_ev, n_rm = [], 0, 0
        for r in rr:
            evs = events(r.get("Event"))
            new = [e for e in evs if (e, r["Domain"]) not in lost]
            if new != evs:
                n_ev += 1
                r["Event"] = "; ".join(new)
                if not new and not events(r.get("Speaker Event(s)")):
                    n_rm += 1
                    removed.append(dict(r, **{"Removed From": os.path.basename(p),
                                              "Removed Because": "GTM review removed their only event"}))
                    continue
            kept.append(r)
        rev_out.append((p, rh, kept))
        print(f"{os.path.basename(p)}: {len(rr)} -> {len(kept)} ({n_ev} people lose an event, {n_rm} removed)")
    print(f"decisions: {flips} KEEP rows -> DROP;  sheet verdicts: {dict(verdicts)}")
    for w in warns:
        print("  WARN", w)
    if not a.apply:
        print("dry run: nothing written (add --apply)")
        return

    bk = J("backup_pre_gtm_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S"))
    touched = [J(a.decisions)] + [w[4] for w in work] + [J("event_tables", w[1], w[2] + ".csv") for w in work] + a.review
    for f in dict.fromkeys(touched):
        dst = os.path.join(bk, os.path.relpath(os.path.abspath(f), os.path.abspath(D)).replace("..", "_up"))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(f, dst)
    for s, ev, ent, tb, path, ph, keep, gone, doms, ins in work:
        write(path, ph, keep)
        cp = J("event_tables", ev, ent + ".csv")
        ch, crow = read(cp)
        why = f"removed in GTM review ({a.by}, {today}): {ins['reason'] or 'per the manager'}"
        for r in crow:
            if r["Domain"] in doms and r["Decision"] == "KEEP":
                r.update(Decision="DROP", Reason=why)
        write(cp, ch, crow)
    write(J(a.decisions), dh, dec)
    for p, rh, kept in rev_out:
        write(p, rh, kept)
    rp = log
    hdr = list(dict.fromkeys(k for r in removed for k in r))
    old = read(rp)[1] if os.path.exists(rp) else []
    write(rp, list(dict.fromkeys(list(old[0]) + hdr)) if old else hdr, old + removed)
    print(f"written; backup -> {bk}; {len(removed)} removed rows logged -> {rp}")


if __name__ == "__main__":
    main()
