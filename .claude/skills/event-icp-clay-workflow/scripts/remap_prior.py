"""Re-decide an already-scraped (and already-typed) event list against a NEW ICP version, without
paying to classify it again.

When an ICP gets a new version (V1 tabs -> V2 tabs) or a new ICP arrives for events that were
scraped and typed before, the earlier work is reused, never trusted blindly:

    THE NEWER VERSION ALWAYS WINS. Anything the old version decided that the new one doesn't say is
    ignored; anything the old version cut that the new one wants is put back. The old KEEP/DROP is
    never an input - only the old per-row TYPE is, mapped onto the new labels by scope.yaml.

Everything ICP-specific is a variable in `scope.yaml` (shape and comments:
assets/scope.example.yaml, the real Mining V2 file):
    type_map      old type -> [Side, Classification]   (labels = icp.yaml category keys exactly)
    drop_types    old types with no home in the new version, each with its reason
    unknown_types old types that mean "not typed"      -> Clay classifier, or dropped, per tier
    drop_if       row rules, e.g. sanctioned HQ        -> dropped everywhere
                  ({column, equals | not_equals | in | not_in, reason}; the same shape per event)
    events        the Competitor sheet: in-scope events, their tier, and per-event keep rules:
                  keep (labels / label sets), region, entities ([Sponsor] = "sponsors only"), and
                  drop_if (row rules for that event only, e.g. a co-located show's rows)
    tiers         per tier: keep every label, or only what the event names; what to do with unknowns
    label_sets / regions   named lists the event rules refer to (SELL_ALL, MEA ...)
An event that is not in `events` is dropped whole ("Cut").

Input `--prior` is ONE CSV of company rows (one per scraped exhibitor/sponsor row), made with a small
adapter from whatever the earlier run left behind - ask the user where the old types are:
    Event, Entity, Company Name, Website, Country, Prior Type   (+ any column a rule names)
Optional:
    --retypes  CSV(s): new types for companies the old run left unknown (Company Key or Domain, Prior Type,
               + any column to override, e.g. Sanctioned HQ). Only replaces an unknown type.
    --verdicts CSV(s) by Domain (Verdict, Side, Classification, Reason, Confidence, Note; any case),
               from recheck.py or the Clay classifier's classified.csv:
               * a company with a new label: Verdict KEEP = no change, OUT leaves the feed, RELABEL
                 takes the verdict's label, anything else (RENTAL) stays in and is listed for the user;
               * a company the old run left unknown: the verdict's label is judged by each event's
                 rules like any other label; OUT or no label drops it.
    --speakers CSV: Event, Name, Job Title, Company, Country -> kept speakers with their company's label.
               Their company's type comes from the prior rows, else from --company-types (Company Key
               or Company Name, Prior Type): pass the old run's full type list, which also covers
               companies seen only at Cut events (Mining: 1,552 speakers kept with it, 1,117 without).

Outputs in --out (utf-8-sig):
    decisions.csv       every company row: event, tier, old type, new label, KEEP / CLASSIFY / DROP, reason
                        (final: re-check verdicts and the company's majority label applied; it is what
                        `deliver.py split --decisions` puts in the per-event company tables)
    feed_companies.csv  one row per company (by domain): the people workflow's feed
    classify_queue.csv  unknowns at tiers that send them to the Clay classifier
    no_domain.csv       kept or queued companies with no usable domain (cannot be searched)
    recheck_changes.csv every non-KEEP verdict that was applied
    feed_speakers.csv   kept speakers (with --speakers)
    REVIEW.md           per-event before -> after, drop reasons, old-vs-new differences: SHOW THE USER

    python remap_prior.py --scope scope.yaml --prior prior_rows.csv --out remap/ \
        [--prior-decision-col "V1 Decision"] [--retypes r.csv] [--verdicts v.csv] [--speakers s.csv]
"""

import argparse
import collections
import csv
import glob
import os
import re
import sys

import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_feed import flag, norm_domain  # noqa: E402

csv.field_size_limit(10**9)
LEGAL = {"inc", "incorporated", "ltd", "limited", "llc", "gmbh", "ag", "sa", "sas", "sarl", "srl", "spa",
         "bv", "nv", "plc", "corp", "corporation", "co", "kg", "kk", "ab", "as", "oy", "pty", "pvt",
         "private", "se", "the", "company", "lp", "llp", "sl", "aps", "a/s", "s.a", "co.,ltd", "coltd",
         "ltda", "sac", "s a c", "tbk", "pt", "jsc", "ooo", "too", "cv", "sdn", "bhd", "asa",
         "oyj", "s p a", "sa de cv", "de", "group"}
FEED_HDR = ["Company Name", "Domain", "Side", "Classification", "Countries Override", "Seniority Group",
            "Fit", "Events", "Best Tier", "Website", "Country"]


def company_key(name):
    """Name key for companies with no domain: case, accents-in-place, punctuation and legal suffixes
    ignored ("The Acme Mining Co. (Pty) Ltd" = "acme mining")."""
    s = " ".join((name or "").split()).casefold().replace("̇", "").replace("&", " and ")
    s = re.sub(r"\(.*?\)", " ", s)
    s = re.sub(r"[^\w\s]", " ", s)
    toks = s.split()
    while len(toks) > 1 and toks[-1] in LEGAL:
        toks.pop()
    while len(toks) > 1 and toks[0] in ("the", "pt"):
        toks.pop(0)
    return " ".join(toks)


def read(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write(path, header, rows):
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        for r in rows:
            w.writerow([r.get(h, "") for h in header])


def paths(patterns):
    return [p for pat in patterns or [] for p in sorted(glob.glob(pat))]


RULE_OPS = ("equals", "not_equals", "in", "not_in")


def rule_hits(rule, row):
    """A drop_if rule: {column, equals | not_equals | in | not_in, reason}. Values compare stripped."""
    v = (row.get(rule["column"]) or "").strip()
    if "equals" in rule:
        return v == str(rule["equals"])
    if "not_equals" in rule:
        return v != str(rule["not_equals"])
    if "in" in rule:
        return v in {str(x) for x in rule["in"]}
    return v not in {str(x) for x in rule["not_in"]}


class Scope:
    def __init__(self, cfg):
        self.cfg = cfg
        self.type_map = {k: tuple(v) for k, v in (cfg.get("type_map") or {}).items()}
        self.drop_types = cfg.get("drop_types") or {}
        self.unknown = set(cfg.get("unknown_types") or ["UNKNOWN", ""])
        self.drop_if = cfg.get("drop_if") or []
        self.sets = {k: set(v) for k, v in (cfg.get("label_sets") or {}).items()}
        self.events = cfg.get("events") or {}
        self.tiers = {str(k): v or {} for k, v in (cfg.get("tiers") or {}).items()}
        self.regions = {}
        for name, r in (cfg.get("regions") or {}).items():
            yes = re.compile(r"\b(" + "|".join(re.escape(x) for x in r.get("tokens") or []) + r")", re.I)
            no = re.compile("|".join(re.escape(x) for x in r.get("not") or []) or r"(?!x)x", re.I)
            self.regions[name] = (yes, no)
        labels = {v[1] for v in self.type_map.values()} | set(cfg.get("extra_labels") or [])
        for name, ev in self.events.items():
            for lab in self.labels(ev.get("keep")) - labels:
                raise SystemExit(f"scope.yaml: event {name!r} keeps {lab!r}, which no type_map entry produces")
            if str(ev.get("tier")) not in self.tiers:
                raise SystemExit(f"scope.yaml: event {name!r} has tier {ev.get('tier')!r}, not in `tiers`")
            for rule in list(self.drop_if) + list(ev.get("drop_if") or []):
                ops = [k for k in RULE_OPS if k in rule]
                if not rule.get("column") or len(ops) != 1 or not rule.get("reason"):
                    raise SystemExit(f"scope.yaml: event {name!r} drop_if {rule!r} needs column, reason and "
                                     f"exactly one of {', '.join(RULE_OPS)}")

    def labels(self, spec):
        """A keep list: label names and/or label_set names."""
        out = set()
        for x in spec or []:
            out |= self.sets.get(x, {x})
        return out

    def in_region(self, region, values):
        yes, no = self.regions[region]
        for v in values:
            for part in re.split(r"[;,|/]", v or ""):
                p = part.strip()
                if p and not no.search(p) and yes.search(p):
                    return True
        return False

    def decide(self, event, row, ptype):
        """-> (decision, side, label, reason). decision is KEEP / CLASSIFY / DROP."""
        ev = self.events.get(event)
        if ev is None:
            return "DROP", "", "", "event not in scope (Cut)"
        tier = str(ev.get("tier"))
        t = self.tiers[tier]
        for rule in list(self.drop_if) + list(ev.get("drop_if") or []):
            if rule_hits(rule, row):
                return "DROP", "", "", rule["reason"]
        ents = ev.get("entities")
        if ents and (row.get("Entity") or "") not in ents:
            return "DROP", "", "", f"Tier {tier} note: {'/'.join(ents)} only ({row.get('Entity') or 'no entity'})"
        if ptype in self.unknown:
            if (ev.get("unknown") or t.get("unknown", "drop")) == "classify":
                return "CLASSIFY", "", "", f"unknown type at a Tier {tier} event -> Clay classifier"
            return "DROP", "", "", f"unknown type at a Tier {tier} event (unsure -> drop)"
        if ptype in self.drop_types:
            return "DROP", "", "", f"{ptype}: {self.drop_types[ptype]}"
        if ptype not in self.type_map:
            return "DROP", "", "", f"{ptype}: out of ICP in the new version"
        side, label = self.type_map[ptype]
        return self.judge_label(event, row, side, label)

    def judge_label(self, event, row, side, label):
        """The event's rules for a company that already has a new-version label."""
        ev = self.events.get(event)
        if ev is None:
            return "DROP", "", "", "event not in scope (Cut)"
        tier = str(ev.get("tier"))
        t = self.tiers[tier]
        keep = ev.get("keep")
        if keep is None and t.get("keep", "all") == "all":
            return "KEEP", side, label, f"Tier {tier}: every label kept"
        if label not in self.labels(keep):
            return "DROP", side, label, f"Tier {tier} note: {ev.get('note') or 'label not kept at this event'}"
        reg = ev.get("region")
        if reg and label in self.labels(reg.get("labels")) and not self.in_region(
                reg["region"], [row.get(c, "") for c in reg.get("fields") or ["Country"]]):
            return "DROP", side, label, f"Tier {tier} note: not {reg['region']}"
        return "KEEP", side, label, f"Tier {tier} note: {ev.get('note') or 'kept'}"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scope", required=True)
    ap.add_argument("--prior", required=True, help="prior company rows CSV (see the docstring)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--type-col", default="Prior Type")
    ap.add_argument("--prior-decision-col", default="", help="the old KEEP/DROP, for REVIEW.md comparisons only")
    ap.add_argument("--retypes", nargs="*", default=[])
    ap.add_argument("--verdicts", nargs="*", default=[])
    ap.add_argument("--speakers")
    ap.add_argument("--company-types", nargs="*", default=[], help="extra company-key -> type lookups for speakers")
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    S = Scope(yaml.safe_load(open(a.scope, encoding="utf-8")) or {})
    os.makedirs(a.out, exist_ok=True)

    retype = {}
    for r in (r for p in paths(a.retypes) for r in read(p)):
        k = norm_domain(r.get("Domain")) or "key:" + (r.get("Company Key") or company_key(r.get("Company Name")))
        retype[k] = r
    verdicts = {}
    for r in (r for p in paths(a.verdicts) for r in read(p)):
        r = {k.strip().title(): (v or "").strip() for k, v in r.items() if k}   # domain -> Domain ...
        verdicts[norm_domain(r.get("Domain"))] = r

    decisions, retyped = [], 0
    # company key -> Counter of (type, the drop_if columns' values), for the speakers: a speaker's
    # company is judged with the same row rules (a sanctioned HQ drops its speakers too).
    rule_cols = [x["column"] for x in S.drop_if]
    type_by_key = collections.defaultdict(collections.Counter)
    for r in read(a.prior):
        ev, name = r["Event"], r["Company Name"]
        dom = norm_domain(r.get("Website") or r.get("Domain"))
        dflag = flag(dom)
        if dflag.startswith(("shared", "hosted")):
            dom = ""                                   # never search a shared host as if it were the company
        ptype = (r.get(a.type_col) or "").strip()
        if ptype in S.unknown:
            n = retype.get(dom) or retype.get("key:" + company_key(name))
            if n:
                r = {**r, **{k: v for k, v in n.items() if k in r and k != a.type_col and v != ""}}
                ptype, retyped = (n.get("Prior Type") or n.get(a.type_col) or "").strip(), retyped + 1
        d, side, label, why = S.decide(ev, r, ptype)
        if ptype not in S.unknown:
            type_by_key[company_key(name)][(ptype, tuple(r.get(c, "") for c in rule_cols))] += 1
        tier = (S.events.get(ev) or {}).get("tier", "")
        decisions.append({"Event": ev, "Tier": tier, "Entity": r.get("Entity", ""), "Company Name": name,
                          "Website": r.get("Website", ""), "Domain": dom, "Domain Flag": dflag,
                          "Country": r.get("Country", ""), "Prior Type": ptype,
                          "Prior Decision": r.get(a.prior_decision_col, "") if a.prior_decision_col else "",
                          "Side": side, "Classification": label, "Decision": d, "Reason": why,
                          "_row": r})
    groups = collections.defaultdict(list)
    for x in decisions:
        if x["Decision"] in ("KEEP", "CLASSIFY"):
            groups[x["Domain"] or "name:" + company_key(x["Company Name"])].append(x)
    feed, queue, nodom, conflicts, changes = [], [], [], [], []
    for key, xs in sorted(groups.items()):
        kept = [x for x in xs if x["Decision"] == "KEEP"]
        tiers = [x["Tier"] for x in xs if x["Tier"] != ""]
        base = {"Company Name": collections.Counter(x["Company Name"] for x in xs).most_common(1)[0][0],
                "Domain": xs[0]["Domain"], "Fit": "KEEP", "Events": "; ".join(sorted({x["Event"] for x in xs})),
                "Best Tier": min(tiers) if tiers else "", "Website": xs[0]["Website"],
                "Country": next((x["Country"] for x in xs if x["Country"]), "")}
        v = verdicts.get(base["Domain"]) if base["Domain"] else None
        if not kept and v:
            # A company the old run left unknown, labelled since (the Clay classifier's classified.csv
            # or a local re-check): the label still has to pass each event's rules.
            if v.get("Verdict") == "OUT" or not (v.get("Side") and v.get("Classification")):
                for x in xs:
                    x.update(Decision="DROP", Reason=f"classified: {v.get('Verdict') or 'no label'} "
                                                     f"{v.get('Reason', '')} ({v.get('Note', '')})".strip())
                changes.append(dict(base, Side="", Classification="", **{
                    "Verdict": v.get("Verdict") or "OUT", "New Side": "", "New Classification": "",
                    "Reason": v.get("Reason", ""), "Confidence": v.get("Confidence", ""), "Note": v.get("Note", "")}))
                continue
            for x in xs:
                d, side, label, why = S.judge_label(x["Event"], x["_row"], v["Side"], v["Classification"])
                x.update(Decision=d, Side=side, Classification=label, Reason=why + " (classified since)")
            kept = [x for x in xs if x["Decision"] == "KEEP"]
            if not kept:
                continue
            v = None                       # the label IS the verdict; nothing more to apply
        if not kept:
            (queue if base["Domain"] else nodom).append(dict(base, Side="", Classification=""))
            continue
        labels = collections.Counter((x["Side"], x["Classification"]) for x in kept)
        (side, label), _ = labels.most_common(1)[0]
        if len(labels) > 1:
            conflicts.append((key, dict(labels)))
        row = dict(base, Side=side, Classification=label)
        if v and (v.get("Verdict") or "KEEP") != "KEEP":
            changes.append(dict(row, **{"Verdict": v["Verdict"], "New Side": v.get("Side", ""),
                                        "New Classification": v.get("Classification", ""),
                                        "Reason": v.get("Reason", ""), "Confidence": v.get("Confidence", ""),
                                        "Note": v.get("Note", "")}))
            if v["Verdict"] == "OUT":
                for x in xs:
                    x.update(Decision="DROP", Side="", Classification="",
                             Reason=f"re-check: {v.get('Reason', '')} ({v.get('Note', '')})")
                continue
            if v.get("Side") and v.get("Classification"):
                row.update(Side=v["Side"], Classification=v["Classification"])
        # Every row of the company carries its final label. A row the old run left unknown is the
        # same company (same domain or name key) as a typed row, so it is kept with it.
        for x in xs:
            if x["Decision"] == "CLASSIFY":
                x.update(Decision="KEEP", Reason="unknown on this row; same company is typed on another row")
            x.update(Side=row["Side"], Classification=row["Classification"])
        (feed if base["Domain"] else nodom).append(row)
    # The FINAL state per scraped row (after re-check verdicts and the majority label): what the
    # per-event Clay tables show (deliver.py split --decisions).
    write(os.path.join(a.out, "decisions.csv"),
          ["Event", "Tier", "Entity", "Company Name", "Website", "Domain", "Domain Flag", "Country",
           "Prior Type", "Prior Decision", "Side", "Classification", "Decision", "Reason"], decisions)
    write(os.path.join(a.out, "feed_companies.csv"), FEED_HDR, feed)
    write(os.path.join(a.out, "classify_queue.csv"), FEED_HDR, queue)
    write(os.path.join(a.out, "no_domain.csv"), FEED_HDR, nodom)
    write(os.path.join(a.out, "recheck_changes.csv"), FEED_HDR + ["Verdict", "New Side", "New Classification",
                                                                 "Reason", "Confidence", "Note"], changes)

    for r in (r for p in paths(a.company_types) for r in read(p)):
        k = r.get("Company Key") or company_key(r.get("Company Name"))
        n = retype.get("key:" + k)
        t = ((n.get("Prior Type") if n and (r.get(a.type_col) or "").strip() in S.unknown else None)
             or r.get(a.type_col) or "").strip()
        if k not in type_by_key and t not in S.unknown:
            vals = {**r, **(n or {})} if n and (r.get(a.type_col) or "").strip() in S.unknown else r
            type_by_key[k][(t, tuple(vals.get(c, "") for c in rule_cols))] += 1
    speakers = []
    if a.speakers:
        for s in read(a.speakers):
            co = (s.get("Company") or "").strip()
            types = type_by_key.get(company_key(co)) if co else None
            if not types:
                d, side, label, why = "DROP", "", "", "speaker: company not typed"
            else:
                (ptype, vals), _ = types.most_common(1)[0]
                d, side, label, why = S.decide(s["Event"], {**dict(zip(rule_cols, vals)),
                                                            "Country": s.get("Country", "")}, ptype)
                if d == "CLASSIFY":
                    d, why = "DROP", "speaker: company type unknown"
            speakers.append({**s, "Tier": (S.events.get(s["Event"]) or {}).get("tier", ""), "Side": side,
                             "Classification": label, "Decision": d, "Reason": why})
        write(os.path.join(a.out, "feed_speakers.csv"),
              ["Event", "Tier", "Name", "Job Title", "Company", "Country", "Side", "Classification",
               "Decision", "Reason"], [s for s in speakers if s["Decision"] == "KEEP"])

    review(a, S, decisions, speakers, feed, queue, nodom, conflicts, changes, retyped)


def review(a, S, decisions, speakers, feed, queue, nodom, conflicts, changes, retyped):
    by = collections.Counter((r["Side"], r["Classification"]) for r in feed)
    L = [f"# Re-decided against {S.cfg.get('icp_version', 'the new ICP version')} - review", "",
         "The new version wins: the old KEEP/DROP is ignored, only the old type is reused. Company counts are "
         "scraped rows (exhibitors + sponsors) per event; the feed is deduplicated by domain.", "",
         "## Totals", "",
         f"- **Feed (searchable, has a domain): {len(feed):,} companies** - "
         + ", ".join(f"{sum(v for (s, _), v in by.items() if s == side):,} {side}"
                     for side in sorted({s for s, _ in by})),
         f"- Classifier queue (unknown type, has a domain): {len(queue):,}",
         f"- Kept or queued but **no usable domain** (cannot be searched): {len(nodom):,}",
         f"- Unknown types given a type by --retypes: {retyped:,}",
         f"- Re-check verdicts applied (not KEEP): {len(changes):,} "
         f"({dict(collections.Counter(c['Verdict'] for c in changes))})",
         f"- Companies whose rows disagree on label (majority used): {len(conflicts):,}"]
    if speakers:
        L.append(f"- Speakers kept: {sum(s['Decision'] == 'KEEP' for s in speakers):,} of {len(speakers):,}")
    L += ["", "| Side | Classification | Companies |", "|---|---|---|"]
    L += [f"| {s} | {c} | {v:,} |" for (s, c), v in sorted(by.items())]
    L += ["", "## Per event (company rows)", "",
          "| Event | Tier | Rows | KEEP | CLASSIFY | DROP | old DROP -> KEEP | old KEEP -> DROP |",
          "|---|---|---|---|---|---|---|---|"]
    ev = collections.defaultdict(list)
    for x in decisions:
        ev[x["Event"]].append(x)
    for e in sorted(ev, key=lambda k: (str((S.events.get(k) or {}).get("tier", "z")), k)):
        xs = ev[e]
        c = collections.Counter(x["Decision"] for x in xs)
        back = sum(x["Prior Decision"] == "DROP" and x["Decision"] == "KEEP" for x in xs)
        lost = sum(x["Prior Decision"] == "KEEP" and x["Decision"] == "DROP" for x in xs)
        old = (f"{back:,} | {lost:,}" if a.prior_decision_col else "- | -")
        L.append(f"| {e} | {xs[0]['Tier']} | {len(xs):,} | {c['KEEP']:,} | {c['CLASSIFY']:,} | {c['DROP']:,} | {old} |")
    L += ["", "## Drop reasons (company rows)", "", "| Reason | Rows |", "|---|---|"]
    L += [f"| {k} | {v:,} |" for k, v in
          collections.Counter(x["Reason"] for x in decisions if x["Decision"] == "DROP").most_common()]
    if a.prior_decision_col:
        L += ["", "## Old-version drops that the new version puts back", "", "| Event | Label | Rows |", "|---|---|---|"]
        back = collections.Counter((x["Event"], x["Classification"]) for x in decisions
                                   if x["Prior Decision"] == "DROP" and x["Decision"] == "KEEP")
        L += [f"| {e} | {c} | {v:,} |" for (e, c), v in sorted(back.items())]
    for e, evc in S.events.items():
        if evc.get("keep") is not None or evc.get("region"):
            ks = [x for x in decisions if x["Event"] == e and x["Decision"] == "KEEP"]
            L += ["", f"## {e}: kept sample ({len(ks):,} kept rows; rule: {evc.get('note', '')})", ""]
            L += [f"- {x['Company Name']} ({x['Country'] or 'no country'}) - {x['Classification']}" for x in ks[:10]]
    if conflicts:
        L += ["", "## Label conflicts under one domain (first 25)", ""]
        L += [f"- `{k}`: " + ", ".join(f"{c} x{n}" for (_, c), n in v.items()) for k, v in conflicts[:25]]
    open(os.path.join(a.out, "REVIEW.md"), "w", encoding="utf-8").write("\n".join(L) + "\n")
    print("\n".join(L[:14]))
    print(f"-> {a.out}  (decisions, feed_companies, classify_queue, no_domain, recheck_changes, REVIEW.md)")


if __name__ == "__main__":
    main()
