"""Apply the ICP sheet's exact title and country rules to the people Find People returned.

Clay's search (icp.yaml) is deliberately the wider net: word-level contains, soft exclusions, one
search per side. The sheet's real rules are stricter and Clay can't express them, so they live in
`post_filter.yaml` beside icp.yaml (shape: assets/post_filter.example.yaml) and are applied here:

  * country   the person's Mailing Country must be in the side's list in icp.yaml
              (sell: the person's category list; buy: the category's list, else default_countries).
              Find People's country filter is not re-checked anywhere else.
  * sell      the title must pass one of `sell.personas` (exact titles, or contains keywords with
              their own exclusions). Persona = the first persona listed that passes ("Booker wins").
              No personas in the file -> the sell title is not judged here.
  * buy       the title must pass the category's contains keywords (icp.yaml titles) or the
              cross-cutting `buy.exact` titles, with none of `buy.exclude`
              (`exclude_allowed` lifts a word for some categories: Partner for Investors).
  * flags     kept, never dropped, for the marketing manager to judge (column Title Check):
              near-exact (a senior exact title plus a qualifier, "Group CEO") and non-English
              titles the rules can't read.
  * Geo Tier  stamped from `<side>.geo_tier`; Team Evidence flagged for `team_evidence` categories.
  * LinkedIn-only countries (Germany on Mining) go to their own file and never to the email file.

    python post_filter.py --people people.csv --icp icp.yaml --rules post_filter.yaml \
        --out-prefix "<Series>" [--results runs/*/results]
      -> <Series> - email list.csv, - LinkedIn only.csv, - dropped by post filter.csv

`--results` (the Find People result dirs) only feeds the LinkedIn headline into the team-evidence
check. The three outputs always add up to the input.
"""

import argparse
import collections
import csv
import glob
import json
import os
import re
import sys

import yaml

csv.field_size_limit(10**9)
# Parts of a compound title, each judged as an exact title: "CEO & Founder", "GM - Egypt".
# Not on "and": "Logistics and Sales" must not pass the bare exact title "Sales".
SPLIT = re.compile(r"\s*(?:&|/|\||,|;|\s[-–—]\s|@)\s*")
# Exact titles too generic to count as "near-exact" ("Sales Support Manager" contains Manager).
BARE = {"manager", "director", "sales", "president", "vice president", "owner"}
NEAR = "near-exact title, check: {} + qualifier"
NON_LATIN = "non-English title, check (Clay matched it)"
# A Latin-script title in another language ("Projektleiter", "Gerente general"): accented letters,
# or one of these title words. Judged only for titles that failed the English rules, so a false hit
# just adds a flagged person for the approver. No re.I on the accent class: case-folding makes
# "ſ" match "s" and "K" match "k".
ACCENTED = re.compile(r"[À-ÖØ-öø-ÿĀ-ž]")
FOREIGN = re.compile(
    r"\b(projekt\w*|proje|gerente|gerencia|director[ae]? de|directeur|direttore|"
    r"diretor\w*|ingenier[oa]|ing[eé]nieur|ingegnere|engenheiro|vertrieb\w*|\w*leiter|"
    r"gesch[aä]ftsf[uü]hrer|ge[oó]log[oa]|yönetici\w*|müdür\w*|responsable|responsabile|jefe|"
    r"chef|gestor|coordinador[a]?|comercial|ventas|verkauf\w*|verkoop\w*|försäljning\w*|"
    r"säljare|produktmanager|marketingmanager|satış|pazarlama|mühendis\w*|uzman\w*|"
    r"superintendente|presidente|amministratore|commerciale|einkauf\w*|compras|achats|acheteur|"
    r"minero|miner[ií]a|minera[cç][aã]o|mantenimiento|manuten[cç][aã]o|exploraci[oó]n|"
    r"jef[ea]|encargad[oa]|supervisor[a]? de|analista|especialista|t[eé]cnico|toimitusjohtaja|"
    r"dyrektor|jednatel|vendedor|manajer|direktur|kierownik|ortak|kurucu|algemeen)\b", re.I)
SELL, BUY = "Sell Side", "Buy Side"


def norm(s):
    return " ".join(re.sub(r"[^\w\s-]", " ", (s or "").lower()).replace("-", " ").split())


def has(term, title):
    """Whole-word, case-insensitive; hyphens and spaces are the same ("Pre-Sales" = "Pre Sales")."""
    t = re.escape(norm(term)).replace(r"\ ", r"\s+")
    return re.search(r"(?<!\w)" + t + r"(?!\w)", norm(title)) is not None


def exact(titles, title):
    parts = {norm(p) for p in SPLIT.split(title or "") if p.strip()} | {norm(title)}
    return next((t for t in titles if norm(t) in parts), None)


def passes(persona, title):
    """-> the keyword that lets `title` through for this persona, or None."""
    hit = exact(persona.get("exact") or [], title)
    if hit:
        return hit
    for kw, rule in (persona.get("contains") or {}).items():
        if has(kw, title) and not any(has(x, title) for x in (rule or {}).get("exclude", [])):
            return kw
    return None


def near_exact(exacts, title, bare=BARE):
    """A senior exact title plus a qualifier ("Group CEO", "Managing Director: Africa", but also
    "GM HR"): kept with a flag. Bare titles never count."""
    return next((t for t in exacts if norm(t) not in bare and has(t, title)), None)


def is_non_english(title):
    return bool(title.strip()) and (not re.search(r"[A-Za-z]", title)
                                    or bool(ACCENTED.search(title) or FOREIGN.search(title)))


def headlines(dirs):
    out = {}
    for pattern in dirs or []:
        for d in glob.glob(pattern):
            for fp in glob.glob(os.path.join(d, "*.jsonl")):
                for line in open(fp, encoding="utf-8"):
                    stack = [json.loads(line)]
                    while stack:
                        o = stack.pop()
                        if isinstance(o, dict):
                            if o.get("url") and "headline" in o:
                                out[o["url"]] = o.get("headline") or ""
                            stack.extend(o.values())
                        elif isinstance(o, list):
                            stack.extend(o)
    return out


class Rules:
    """icp.yaml + post_filter.yaml, resolved once. Also used by speakers.py for the country rule."""

    def __init__(self, icp, rules):
        self.icp, self.rules = icp, rules or {}
        sell, buy = icp.get("sell") or {}, icp.get("buy") or {}
        self.sell_countries = {k: set(v or []) for k, v in (sell.get("categories") or {}).items()}
        default = set(buy.get("default_countries") or [])
        self.buy_cats = {k: (v or {}) for k, v in (buy.get("categories") or {}).items()}
        self.buy_countries = {k: set(v.get("countries") or []) or default for k, v in self.buy_cats.items()}
        self.sell_x = list(icp.get("exclude_titles") or []) + list(sell.get("exclude_titles") or [])
        r_sell, r_buy = self.rules.get("sell") or {}, self.rules.get("buy") or {}
        self.personas = r_sell.get("personas") or {}
        self.buy_exact = r_buy.get("exact") or []
        self.buy_x = r_buy.get("exclude") or []
        allowed = dict(r_buy.get("exclude_allowed") or {})
        if r_buy.get("partner_allowed"):                 # the Mining file's older spelling
            allowed.setdefault("Partner", r_buy["partner_allowed"])
        self.allowed = allowed
        self.contains_too = r_buy.get("contains_too") or {}
        te = r_buy.get("team_evidence") or r_buy.get("mining_team_evidence") or {}
        self.te_cats = set(te.get("categories") or [])
        self.te_words = te.get("words") or []
        self.te_col = te.get("column") or ("Mining Team Evidence" if "mining_team_evidence" in r_buy
                                            else "Team Evidence")
        self.tiers = {SELL: r_sell.get("geo_tier") or {}, BUY: r_buy.get("geo_tier") or {}}
        self.tier_default = self.rules.get("geo_tier_default", "Secondary")
        self.linkedin_only = set(self.rules.get("linkedin_only_countries") or [])
        flags = self.rules.get("flags") or {}
        self.flag_near = flags.get("near_exact", True)
        self.flag_foreign = flags.get("non_english", True)
        self.bare = {norm(x) for x in flags.get("bare_titles", BARE)}

    def country_ok(self, side, cat, country):
        if side == SELL:
            if cat in self.sell_countries:
                return country in self.sell_countries[cat]
            return any(country in v for v in self.sell_countries.values())
        if cat in self.buy_countries:
            return country in self.buy_countries[cat]
        return any(country in v for v in self.buy_countries.values())

    def geo_tier(self, side, country):
        return next((t for t, cs in self.tiers.get(side, {}).items() if country in (cs or [])),
                    self.tier_default)

    def buy_contains(self, cat):
        also = self.contains_too.get(cat) or []
        titles = (self.buy_cats.get(cat) or {}).get("titles") or []
        return [t for t in titles if t not in self.buy_exact or t in also]

    def buy_excludes(self, cat):
        # icp.yaml's buy.categories.<cat>.exclude_titles too: the Clay search (ICP Mapping) and Blitz
        # already exclude them for that category, so a row from anywhere else must be judged the same
        # way (EVCharge buy side, 2026-11-06: "MD" is a physician in Hospitals & health, not an MD).
        cat_x = list((self.buy_cats.get(cat) or {}).get("exclude_titles") or [])
        return [x for x in self.buy_x if cat not in (self.allowed.get(x) or [])] + cat_x

    def judge(self, side, cat, title, country, headline=""):
        """-> (reason or "", fields to set). An empty reason means keep."""
        f = {"Persona": "", "Matched Title Keyword": "", "Title Check": "", self.te_col: ""}
        foreign = self.flag_foreign and is_non_english(title)
        if side not in (SELL, BUY):
            return f"unrecognised side: {side or 'blank'}", f
        if not self.country_ok(side, cat, country):
            return f"country not on {'sell' if side == SELL else 'buy'} geo: {country or 'blank'}", f
        if side == SELL:
            hit_x = next((x for x in self.sell_x if has(x, title)), None)
            if hit_x:
                return "excluded word: " + hit_x, f
            if not self.personas:
                return "", f
            names = list(self.personas)
            hits = {p: passes(self.personas[p], title) for p in names}
            p = next((p for p in names if hits[p]), None)
            near = {q: near_exact(self.personas[q].get("exact") or [], title, self.bare) for q in names}
            q = next((q for q in names if near[q]), None) if self.flag_near else None
            if p:
                f.update({"Persona": p, "Matched Title Keyword": hits[p]})
            elif q:
                f.update({"Persona": q, "Matched Title Keyword": near[q], "Title Check": NEAR.format(near[q])})
            elif foreign:
                f["Title Check"] = NON_LATIN
            else:
                return "title not on the sell-side title list", f
            return "", f
        xs = self.buy_excludes(cat)
        hit_x = next((x for x in xs if has(x, title)), None)
        if hit_x:
            return "excluded word: " + hit_x, f
        hit = exact(self.buy_exact, title) or next((t for t in self.buy_contains(cat) if has(t, title)), None)
        if not hit and self.flag_near and near_exact(self.buy_exact, title, self.bare):
            hit = near_exact(self.buy_exact, title, self.bare)
            f["Title Check"] = NEAR.format(hit)
        elif not hit and foreign:
            f["Title Check"] = NON_LATIN
        elif not hit:
            return "title not on the buy-side title list", f
        f["Matched Title Keyword"] = hit or ""
        if cat in self.te_cats:
            text = title + " " + (headline or "")
            f[self.te_col] = "yes" if any(has(w, text) for w in self.te_words) else "NO - check"
        return "", f


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--people", required=True, help="collect.py people output")
    ap.add_argument("--icp", required=True)
    ap.add_argument("--rules", required=True, help="post_filter.yaml")
    ap.add_argument("--results", nargs="*", default=[], help="Find People result dirs (globs ok), for headlines")
    ap.add_argument("--out-prefix", required=True)
    ap.add_argument("--linkedin-only-name", default="LinkedIn only",
                    help='file label for the LinkedIn-only countries (Mining: "Germany LinkedIn only")')
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    R = Rules(yaml.safe_load(open(a.icp, encoding="utf-8")) or {},
              yaml.safe_load(open(a.rules, encoding="utf-8")) or {})
    heads = headlines(a.results)
    rows = list(csv.DictReader(open(a.people, encoding="utf-8-sig")))
    kept, li_only, dropped = [], [], []
    why = collections.Counter()
    for r in rows:
        reason, fields = R.judge(r.get("Side", ""), r.get("Classification", ""), r.get("Job Title", ""),
                                 r.get("Mailing Country", ""), heads.get(r.get("LinkedIn URL", ""), ""))
        r.update(fields)
        r["Geo Tier"] = ""
        if reason:
            why[reason.split(":")[0]] += 1
            dropped.append({**r, "Dropped Because": reason})
            continue
        r["Geo Tier"] = R.geo_tier(r["Side"], r["Mailing Country"])
        (li_only if r["Mailing Country"] in R.linkedin_only else kept).append(r)

    cols = list(rows[0].keys()) if rows else []
    for name, data, extra in (("email list", kept, []), (a.linkedin_only_name, li_only, []),
                              ("dropped by post filter", dropped, ["Dropped Because"])):
        with open(f"{a.out_prefix} - {name}.csv", "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, cols + extra, extrasaction="ignore")
            w.writeheader()
            w.writerows(data)
    assert len(kept) + len(li_only) + len(dropped) == len(rows)
    print(f"people in {len(rows):,} = email list {len(kept):,} + {a.linkedin_only_name} {len(li_only):,} "
          f"+ dropped {len(dropped):,}")
    flagged = collections.Counter(r["Title Check"].split(",")[0] for r in kept + li_only if r["Title Check"])
    for k, n in flagged.most_common():
        print(f"  kept, flagged for the approver: {k[:40]:40} {n:,}")
    for k, n in why.most_common():
        print(f"  dropped: {k:40} {n:,}")


if __name__ == "__main__":
    main()
