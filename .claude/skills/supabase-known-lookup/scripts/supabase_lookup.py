"""Read-only lookups against the team's shared Supabase (schema `gtm`).

    companies  Which domains the team already knows (gtm.companies). Used as the
               "is this company new?" gate BEFORE paying for enrichment or
               classification.
    contacts   Which people already have an email (gtm.emails, with the
               ZeroBounce answer) or were already searched with no email found
               (gtm.paid_ledger misses), keyed by LinkedIn slug. Used BEFORE the
               paid email waterfall so nobody pays twice for the same person.

Nothing here writes. The connection is opened read-only, and the script prints
counts only — never rows — so no contact data lands in a chat transcript.

Two ways in (counts are identical, the matching is the same SQL-side identity logic):
  * the team API (no database password needed):  --api, with SUPABASE_URL, SUPABASE_PUBLISHABLE_KEY and the team
    key (GTM_API_KEY, or --api-key-file pointing at clay-team.key) in the environment or --env-file;
  * the direct database connection: $SUPABASE_DB_URL, or --env-file pointing at a .env that sets it.

    python supabase_lookup.py ping
    python supabase_lookup.py companies --in feed.csv --domain-col Domain --out feed_checked.csv
    python supabase_lookup.py contacts  --in people.csv --linkedin-col "LinkedIn URL" --out people_known.csv
"""

import argparse
import collections
import csv
import hashlib
import os
import re
import sys
import unicodedata
from urllib.parse import unquote

# Everything below mirrors clay_to_claude's gtm.normalize / gtm.identity, so the
# keys looked up here are byte-for-byte the keys the gtm pipeline wrote. If those
# drift, matches silently drop — keep them in step.
def clean_text(value):
    if value is None:
        return ""
    s = str(value)
    if s.strip().lower() in {"nan", "none", "null"}:
        return ""
    s = unicodedata.normalize("NFKC", s).replace("​", "").replace("\xa0", " ")
    s = re.sub(r"[•·]", " ", s)
    return re.sub(r"\s+", " ", s).strip(" ,;-")


def linkedin_slug(value):
    s = str(value or "").strip().lower()
    m = re.search(r"linkedin\.com/in/([^/?#\s]+)", s)
    return (m.group(1).strip() or None) if m else None


def norm_domain(value):
    s = re.sub(r"\s+", "", str(value or "").lower())
    s = re.sub(r"^(https?:/*)?(www\.)*", "", s)
    s = s.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0].split("@")[-1]
    s = re.sub(r":\d+$", "", s).rstrip(".").strip()
    return s if s and "." in s else None


_HONORIFICS = {"dr", "mr", "mrs", "ms", "miss", "prof", "sir", "dame", "eng", "ir",
               "jr", "sr", "ii", "iii", "phd", "mba"}


def _sha16(*parts):
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:16]


def fold_name(value):
    s = unicodedata.normalize("NFKD", clean_text(value).lower())
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return " ".join(t for t in s.split() if len(t) > 1 and t not in _HONORIFICS)


def person_keys(linkedin_url, first, last, full, domain):
    """Every identity form gtm can know a person by (gtm.identity.all_person_keys)."""
    keys = []
    s = linkedin_slug(linkedin_url)
    if s:
        keys.append("li:" + s)
        if unquote(s) != s:
            keys.append("li:" + unquote(s))
    f, l = clean_text(first).lower(), clean_text(last).lower()
    if not (f or l) and full:
        parts = clean_text(full).lower().split()
        f, l = (parts[0], " ".join(parts[1:])) if parts else ("", "")
    ck = norm_domain(domain)
    if (f or l) and ck:
        keys.append("nd:" + _sha16(f, l, ck))
        ff, fl = fold_name(f), fold_name(l)
        if ff or fl:
            keys.append("nf:" + _sha16(ff.split()[0] if ff else "", fl, ck))
    return list(dict.fromkeys(keys))


# ZeroBounce answers that are safe to reuse vs. ones the user must decide on.
VALID = {"valid"}
RISKY = {"catch-all", "unknown", "pending_verification", None, ""}
BAD = {"invalid", "spamtrap", "abuse", "do_not_mail"}


# Columns added to the CSV. Contact columns are named like the Salesforce
# Competitive Events file so the final export maps without renaming.
CONTACT_COLS = ["Contact Email", "Email Validation Status", "Email Sub Status",
                "MX Provider", "Validated At", "Supabase Status"]
COMPANY_COLS = ["Known Company", "Known Since", "Known By"]
PERSON_FIELDS = ["first_name", "last_name", "job_title", "city", "state", "country"]


ENV_KEYS = ("SUPABASE_DB_URL", "SUPABASE_URL", "SUPABASE_PUBLISHABLE_KEY", "GTM_API_KEY")


def load_env_file(path):
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        for k in ENV_KEYS:
            if line.startswith(k + "=") and not os.environ.get(k):
                os.environ[k] = line.split("=", 1)[1].strip().strip('"\'')


# ---------------------------------------------------------------- team API (api.lookup_contacts / api.lookup_companies)
def api_call(fn, payload):
    import json
    import urllib.error
    import urllib.request

    need = [k for k in ("SUPABASE_URL", "SUPABASE_PUBLISHABLE_KEY", "GTM_API_KEY") if not os.environ.get(k)]
    if need:
        raise SystemExit(f"--api needs {need} (env or --env-file; the team key via --api-key-file). Never paste keys into chat.")
    req = urllib.request.Request(
        os.environ["SUPABASE_URL"].rstrip("/") + "/rest/v1/rpc/" + fn, data=json.dumps(payload).encode("utf-8"), method="POST",
        headers={"apikey": os.environ["SUPABASE_PUBLISHABLE_KEY"], "x-gtm-key": os.environ["GTM_API_KEY"],
                 "Content-Profile": "api", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise SystemExit(f"team API {fn}: HTTP {e.code} {e.read().decode('utf-8', 'replace')[:200]} — stop the paid step that needed it")


def connect():
    url = os.environ.get("SUPABASE_DB_URL")
    if not url:
        raise SystemExit("SUPABASE_DB_URL is not set — pass --env-file or export it "
                         "(never paste the connection string into chat)")
    try:
        import psycopg
        conn = psycopg.connect(url, connect_timeout=15)
        conn.read_only = True
        return conn
    except ImportError:
        import psycopg2
        conn = psycopg2.connect(url, connect_timeout=15)
        conn.set_session(readonly=True)
        return conn


def query(conn, sql, params):
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def read_csv(path):
    with open(path, encoding="utf-8-sig", newline="") as fh:
        r = csv.DictReader(fh)
        return list(r.fieldnames or []), list(r)


def write_csv(path, header, rows):
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=header, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def need_col(header, col, flag):
    if col not in header:
        raise SystemExit(f"column {col!r} not in the CSV — pass {flag}. Columns: {header}")


def cmd_ping(args):
    if args.api:
        api_call("lookup_companies", {"domains": []})
        print("team API reachable (key accepted)")
        return
    conn = connect()
    for t in ("companies", "people", "emails", "paid_ledger"):
        n = query(conn, f"select count(*) from gtm.{t}", ())[0][0]
        print(f"gtm.{t:12} {n:>9,} rows")


def cmd_companies(args):
    header, rows = read_csv(args.inp)
    need_col(header, args.domain_col, "--domain-col")
    doms = sorted({d for d in (norm_domain(r.get(args.domain_col)) for r in rows) if d})
    known = {}
    targets = collections.defaultdict(set)
    if args.api:
        for i in range(0, len(doms), 5000):
            for o in api_call("lookup_companies", {"domains": doms[i:i + 5000]}):
                if o.get("known"):
                    known[o["domain"]] = (o.get("known_since"), "")
                for t in o.get("targeted_events") or []:
                    targets[o["domain"]].add(t)
    conn = None if args.api else connect()
    for i in range(0, len(doms), 1000) if conn else ():
        for dom, seen, by in query(conn, "select domain, first_seen_at, first_seen_by "
                                         "from gtm.companies where domain = any(%s)",
                                   (doms[i:i + 1000],)):
            known[dom] = (seen, by)
    # "Known" only means the team has seen the domain somewhere (107k rows, most
    # of them sourced, never worked). company_targets says which events it was
    # actually targeted for — the user decides which of the two is the gate.
    for i in range(0, len(doms), 1000) if conn else ():
        for dom, ev, side in query(conn, "select domain, event_slug, side from "
                                         "gtm.company_targets where domain = any(%s)",
                                   (doms[i:i + 1000],)):
            targets[dom].add(f"{ev}/{side}")
    out_rows = []
    tally = collections.Counter()
    for r in rows:
        d = norm_domain(r.get(args.domain_col))
        if not d:
            r.update({"Known Company": "no domain", "Known Since": "", "Known By": ""})
            tally["no domain"] += 1
        elif d in known:
            seen, by = known[d]
            r.update({"Known Company": "yes", "Known Since": str(seen or "")[:10],
                      "Known By": by or ""})
            tally["known"] += 1
        else:
            r.update({"Known Company": "no", "Known Since": "", "Known By": ""})
            tally["new"] += 1
        r["Targeted Events"] = "; ".join(sorted(targets.get(d, ()))) if d else ""
        if d and targets.get(d):
            tally["targeted"] += 1
        out_rows.append(r)
    write_csv(args.out, header + [c for c in COMPANY_COLS + ["Targeted Events"]
                                  if c not in header], out_rows)
    print(f"rows {len(rows):,}  distinct domains {len(doms):,}")
    print(f"  new        {tally['new']:,}")
    print(f"  known      {tally['known']:,}   (already in gtm.companies)")
    print(f"    of which targeted for an event before: {tally['targeted']:,}")
    print(f"  no domain  {tally['no domain']:,}")
    print(f"-> {args.out}")
    if args.skip:
        # The user's chosen gate, written as the feed that may be paid for.
        def gated(r):
            if args.skip == "known":
                return r["Known Company"] == "yes"
            ev = r.get("Targeted Events") or ""
            if args.skip == "targeted":
                return bool(ev)
            return any(t.split("/")[0] == args.event_slug for t in ev.split("; ") if t)
        keep = [r for r in out_rows if not gated(r)]
        path = args.out[:-4] + " - gated.csv"
        write_csv(path, header + [c for c in COMPANY_COLS + ["Targeted Events"] if c not in header], keep)
        print(f"gate --skip {args.skip}: {len(out_rows) - len(keep):,} removed, {len(keep):,} kept -> {path}")


def cmd_contacts(args):
    if args.api:
        return cmd_contacts_api(args)
    header, rows = read_csv(args.inp)
    need_col(header, args.linkedin_col, "--linkedin-col")
    col = lambda name: name if name and name in header else None
    first_c, last_c = col(args.first_col), col(args.last_col)
    full_c, dom_c = col(args.name_col), col(args.domain_col)
    if not dom_c:
        print(f"note: no {args.domain_col!r} column — matching on LinkedIn only "
              f"(pass --domain-col to also match name+domain)")

    def keys_of(r):
        return person_keys(r.get(args.linkedin_col),
                           r.get(first_c) if first_c else "", r.get(last_c) if last_c else "",
                           r.get(full_c) if full_c else "", r.get(dom_c) if dom_c else "")
    row_keys = [keys_of(r) for r in rows]
    keys = sorted({k for ks in row_keys for k in ks})
    conn = connect()

    def chunks(seq):
        for i in range(0, len(seq), 1000):
            yield seq[i:i + 1000]

    # Alias -> canonical person_key (people the gtm pipeline keyed another way).
    canon = {k: k for k in keys}
    for part in chunks(keys):
        for ident, pk in query(conn, "select identity_key, person_key from gtm.person_identities "
                                     "where identity_key = any(%s)", (part,)):
            canon[ident] = pk
    pks = sorted(set(canon.values()))
    known_people = set()
    for part in chunks(pks):
        known_people.update(pk for (pk,) in query(conn, "select person_key from gtm.people "
                                                        "where person_key = any(%s)", (part,)))

    # Best email per person: sendable first, then a real ZeroBounce answer, then newest.
    best = {}
    for part in chunks(pks):
        for pk, email, st, sub, mx, vat in query(conn, """
                select distinct on (person_key) person_key, email, verify_status,
                       verify_sub_status, mx_provider, verified_at
                from gtm.emails where person_key = any(%s)
                order by person_key, sendable desc, gtm.verify_rank(verify_status) desc,
                         verified_at desc nulls last, found_at desc nulls last""", (part,)):
            best[pk] = (email, st, sub, mx, vat)

    # Paid-and-missed: the team ledger says the waterfall already ran and found nothing.
    missed = set()
    ledger_rows = query(conn, "select count(*) from gtm.paid_ledger where action = 'email_waterfall'", ())[0][0]
    for part in chunks(sorted(set(keys) | set(pks))):
        missed.update(ik for (ik,) in query(conn, "select identity_key from gtm.paid_ledger where "
                                                  "action = 'email_waterfall' and result = 'miss' and "
                                                  "identity_key = any(%s)", (part,)))

    person = {}
    if args.with_person_fields:
        for part in chunks(pks):
            for row in query(conn, "select person_key, " + ", ".join(PERSON_FIELDS) +
                                   " from gtm.people where person_key = any(%s)", (part,)):
                person[row[0]] = row[1:]

    extra = CONTACT_COLS + (["Supabase " + f for f in PERSON_FIELDS] if args.with_person_fields else [])
    tally = collections.Counter()
    todo, decide = [], []
    for r, ks in zip(rows, row_keys):
        for c in CONTACT_COLS:
            r.setdefault(c, "")
        if r.get("Contact Email"):
            r["Supabase Status"] = "email already in input"
            tally["email already in input"] += 1
            continue
        pk = next((canon[k] for k in ks if canon.get(k) in best), None) \
            or next((canon[k] for k in ks if canon.get(k) in known_people), None)
        hit = best.get(pk) if pk else None
        if not ks:
            status = "no identity (no LinkedIn or name+domain)"
            todo.append(r)
        elif hit:
            email, st, sub, mx, vat = hit
            r.update({"Contact Email": email, "Email Validation Status": st or "",
                      "Email Sub Status": sub or "", "MX Provider": mx or "",
                      "Validated At": str(vat or "")[:19]})
            if st in VALID:
                status = "email reused - valid"
            elif st in BAD:
                status = "known, bad email"          # do not send; user decides on a re-find
                decide.append(r)
            else:
                status = "email reused - risky"      # catch-all / unknown / pending
        elif any(k in missed for k in ks) or (pk and pk in missed):
            status = "searched before, no email"
        elif pk in known_people:
            status = "known, no email"               # in gtm.people, never found an email
            decide.append(r)
        else:
            status = "not in supabase"
            todo.append(r)
        r["Supabase Status"] = status
        tally[status] += 1
        if args.with_person_fields and pk in person:
            for f, v in zip(PERSON_FIELDS, person[pk]):
                r["Supabase " + f] = v or ""
    out_header = header + [c for c in extra if c not in header]
    write_csv(args.out, out_header, rows)
    todo_path = args.out[:-4] + " - still to enrich.csv"
    decide_path = args.out[:-4] + " - user to decide.csv"
    write_csv(todo_path, out_header, todo)
    write_csv(decide_path, out_header, decide)
    print(f"people {len(rows):,}  identity keys tried {len(keys):,}  "
          f"(LinkedIn{' + name/domain' if dom_c else ''})")
    for k_, n in tally.most_common():
        print(f"  {k_:42} {n:>7,}")
    print(f"-> {args.out}")
    print(f"-> {todo_path}   ({len(todo):,} for the paid waterfall)")
    print(f"-> {decide_path}   ({len(decide):,} known with a bad or no email — ASK: re-find or skip?)")
    if ledger_rows < 1000:
        print(f"note: the team ledger holds only {ledger_rows:,} waterfall records, so "
              f"'searched before, no email' catches little; earlier misses outside it are invisible")


API_STATUS = {"no identity": "no identity (no LinkedIn or name+domain)"}


def cmd_contacts_api(args):
    """Same statuses and output files as the database path, answered by api.lookup_contacts (1,000 people per call)."""
    header, rows = read_csv(args.inp)
    need_col(header, args.linkedin_col, "--linkedin-col")
    col = lambda name: name if name and name in header else None   # noqa: E731
    first_c, last_c, full_c, dom_c = col(args.first_col), col(args.last_col), col(args.name_col), col(args.domain_col)
    ask = [i for i, r in enumerate(rows) if not r.get("Contact Email")]
    answers = {}
    for i in range(0, len(ask), 1000):
        batch = ask[i:i + 1000]
        people = [{"linkedin_url": rows[j].get(args.linkedin_col) or "", "first_name": rows[j].get(first_c) if first_c else "",
                   "last_name": rows[j].get(last_c) if last_c else "", "full_name": rows[j].get(full_c) if full_c else "",
                   "domain": rows[j].get(dom_c) if dom_c else ""} for j in batch]
        for j, o in zip(batch, api_call("lookup_contacts", {"people": people})):
            answers[j] = o
    extra = CONTACT_COLS + (["Supabase " + f for f in PERSON_FIELDS] if args.with_person_fields else [])
    tally, todo, decide = collections.Counter(), [], []
    for j, r in enumerate(rows):
        for c in CONTACT_COLS:
            r.setdefault(c, "")
        if j not in answers:
            r["Supabase Status"] = "email already in input"
        else:
            o = answers[j]
            st = API_STATUS.get(o["status"], o["status"])
            if o.get("email"):
                r.update({"Contact Email": o["email"], "Email Validation Status": o.get("email_validation_status") or "",
                          "Email Sub Status": o.get("email_sub_status") or "", "MX Provider": o.get("mx_provider") or "",
                          "Validated At": str(o.get("validated_at") or "")[:19]})
            r["Supabase Status"] = st
            if st in ("known, bad email", "known, no email"):
                decide.append(r)
            elif st in ("not in supabase", API_STATUS["no identity"]):
                todo.append(r)
            if args.with_person_fields:
                for f in PERSON_FIELDS:
                    r["Supabase " + f] = o.get(f) or ""
        tally[r["Supabase Status"]] += 1
    out_header = header + [c for c in extra if c not in header]
    write_csv(args.out, out_header, rows)
    todo_path, decide_path = args.out[:-4] + " - still to enrich.csv", args.out[:-4] + " - user to decide.csv"
    write_csv(todo_path, out_header, todo)
    write_csv(decide_path, out_header, decide)
    print(f"people {len(rows):,}  asked the team API about {len(ask):,}")
    for k_, n in tally.most_common():
        print(f"  {k_:42} {n:>7,}")
    print(f"-> {args.out}\n-> {todo_path}   ({len(todo):,} for the paid waterfall)")
    print(f"-> {decide_path}   ({len(decide):,} known with a bad or no email — ASK: re-find or skip?)")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--env-file", help=".env file that sets SUPABASE_DB_URL (or the --api variables)")
    ap.add_argument("--api", action="store_true", help="use the team API instead of the database connection")
    ap.add_argument("--api-key-file", help="file holding the team key (clay-team.key); sets GTM_API_KEY")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("ping", help="row counts only — proves the connection")
    c = sub.add_parser("companies")
    c.add_argument("--in", dest="inp", required=True)
    c.add_argument("--domain-col", default="Domain")
    c.add_argument("--out", required=True)
    c.add_argument("--skip", choices=["known", "targeted", "targeted-for-event"],
                   help="also write '<out> - gated.csv' without these rows — the USER picks the rule")
    c.add_argument("--event-slug", help="with --skip targeted-for-event: the gtm event_slug")
    p = sub.add_parser("contacts")
    p.add_argument("--in", dest="inp", required=True)
    p.add_argument("--linkedin-col", default="LinkedIn URL")
    p.add_argument("--out", required=True)
    p.add_argument("--first-col", default="First Name")
    p.add_argument("--last-col", default="Last Name")
    p.add_argument("--name-col", default="Person Name", help="full name, when first/last are absent")
    p.add_argument("--domain-col", default="Domain", help="company domain, for the name+domain keys")
    p.add_argument("--with-person-fields", action="store_true",
                   help="also copy first/last name, title, city/state/country from gtm.people")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if args.env_file:
        load_env_file(args.env_file)
    if args.api_key_file and not os.environ.get("GTM_API_KEY"):
        os.environ["GTM_API_KEY"] = open(args.api_key_file, encoding="utf-8").read().strip()
    if getattr(args, "skip", None) == "targeted-for-event" and not args.event_slug:
        raise SystemExit("--skip targeted-for-event needs --event-slug")
    {"ping": cmd_ping, "companies": cmd_companies, "contacts": cmd_contacts}[args.cmd](args)


if __name__ == "__main__":
    main()
