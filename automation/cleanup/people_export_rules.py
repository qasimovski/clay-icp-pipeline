"""The three rules every people export applies, in one place.

Extracted from `export_ce_people.py` when the Products & Services export needed
the same behaviour. Both exporters import from here so the two deliverables
cannot drift — a person excluded from one file must be excluded from the other,
or a downstream dedupe across scopes compares unlike things.

THE RULES

1. **Exclusion.** The user's 54-term list, whole-word and case-insensitive
   against the job title. Applied LOCALLY because Clay's own
   `job_title_exclude_keywords` is a SOFT filter — measured on the CE fleet, 113
   of 16,586 returned people still carried an excluded term. On the ClayQL route
   the `not job_title contains (...)` clause is harder, but it is only as good as
   the config that was live when the table was BUILT: the 28 Highways P&S People
   tables were built 2026-08-14/15, before the exclusion list existed at all.

2. **An EXACT senior title wins a tie** (user, 2026-09-13). "Chief Executive
   Officer - Software" is still a CEO; the word after the dash does not stop it.
   A person is dropped only when an excluded term matches AND no *exact* title
   does.

   THE RESCUE LIST IS THE EXACT TITLES ONLY — `titles_exact` on the sell side,
   per-category `titles_exact` plus the ICP `senior_titles` on the buy side —
   never the `contains` list. Measured 2026-09-13, and this is the whole reason
   the narrowing exists: rescuing on the full asked list saved **528 of 528**
   buy-side rows carrying an excluded term, because that list is function words
   (`Operations`, `Environment`, `Procurement`, `Data`, `Planning`) that appear
   in half of all corporate titles. "People Operations Manager" was surviving on
   `Operations`, "Digital Content Officer" on `Content`, "Risk and Compliance
   Lead" on `Compliance`. Restricted to exact titles it rescues 20 instead —
   and on CE it rescues 10 rather than 36, keeping precisely the senior compound
   titles the user approved (CEO - Software, VP Corporate Finance & Strategy,
   Managing Director UK & Sales Platform Manager) while correctly dropping
   Transport Coordinator, Marketing Assistant and Commercial Finance Manager.

   Known residue, accepted: "Assistant To Chief Executive Officer" is rescued by
   `Chief Executive Officer`. Detecting "assistant TO an exec" needs word-order
   parsing that is not worth the false positives it would bring.

3. **The person must be in one of that category's ICP countries** (user,
   2026-09-14), where the category supplies the list. This is NOT redundant with
   the search: the company search filtered on the COMPANY's office country and
   the people query on `company.locations.count(country_name in ...)`, so a
   multinational with a London office legitimately returned staff sitting in
   Bangalore or Chicago. Measured on the sell-side P&S file, 10,807 of 62,739
   people (17.2%) are outside their own category's list.

   A BLANK country is not "outside" and is kept by default. Their employer is
   in-country by construction, so dropping them would lose 2,750 real people on
   an absence of evidence; `drop_unknown` makes that the caller's explicit
   choice instead.

4. **Dedupe on the LinkedIn `/in/<slug>`**, first occurrence wins. Same
   normalisation as the Supabase people ledger's `normalize_linkedin`, so a row
   deduped here and a row deduped there agree.

ORDER MATTERS: exclusion → country → dedupe. If dedupe ran first, a person who
appears out-of-country in category A and in-country in category B would claim
the slot with the A row, get dropped for country, and the B row would already be
marked a duplicate — losing them from the deliverable entirely.

Nothing is dropped silently: `classify()` returns WHY, and the callers write
every removed row to a sidecar CSV carrying that reason.
"""

import json
import os
import re

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
EXCLUDE_TITLES = os.path.join(SCRIPT_DIR, "ce_nodes", "exclude_titles.json")

KEEP = "keep"
EXCLUDED = "excluded"
DUPLICATE = "duplicate"
OUT_OF_COUNTRY = "out_of_country"


def word(term):
    """Whole-word, case-insensitive pattern for one term.

    Whole-word and not substring: `HR` must not strike "Chairman", `UI` must not
    strike "Building", `QA` must not strike "Quantity". Multi-word terms
    ("Office Manager", "Data Analyst") work unchanged — the boundary guards sit
    outside the whole phrase.
    """
    return re.compile(r"(?<![A-Za-z])" + re.escape(term) + r"(?![A-Za-z])",
                      re.IGNORECASE)


def profile_key(url):
    """The LinkedIn `/in/<slug>` — the identity a person is deduped on.

    Measured on the CE file the raw URLs and the normalised slugs gave the SAME
    distinct count, so nothing was hiding behind a trailing slash or a capital.
    Normalise anyway: that was luck, not a guarantee.
    """
    u = (url or "").strip().lower().split("?")[0].split("#")[0].rstrip("/")
    m = re.search(r"/in/([^/]+)", u)
    return m.group(1) if m else u


def exclusion_patterns(path=None):
    """The exclusion list as [(term, pattern)], from the one tracked copy."""
    terms = json.load(open(path or EXCLUDE_TITLES, encoding="utf-8"))
    return [(t, word(t)) for t in terms]


def asked_patterns(titles):
    """[(title, pattern)] for a list of titles."""
    return [(t, word(t)) for t in titles]


def rescue_titles(icp, category=None):
    """The EXACT titles that may overturn an exclusion, for one ICP/category.

    Exact titles only — see rule 2 in the module docstring. Both exporters call
    this rather than building their own list, because a rescue list that differs
    between the two deliverables makes them impossible to compare or merge.
    """
    if icp.endswith("_buyside"):
        import ps_buyside_people_config as buyercfg
        cfg = buyercfg.load(icp)
        exact = list(cfg.get("senior_titles") or [])
        for cat in cfg["categories"]:
            if category is None or cat["name"] == category:
                exact += list(cat.get("titles_exact") or [])
        return exact

    import ps_people_config as sellercfg  # local: avoids a cycle at import time
    return list(sellercfg.load(icp)["titles_exact"])


def classify(title, url, seen, patterns, asked=(), strict=False,
             dedupe=True, country=None, allowed=None, drop_unknown=False):
    """Decide one person. Returns (verdict, detail).

    `seen` is a dict of profile_key -> whatever the caller wants to remember
    about the first sighting (a domain, a category name); the CALLER updates it
    on a keep, using the returned key.

    verdict is KEEP / EXCLUDED / OUT_OF_COUNTRY / DUPLICATE. detail is the
    excluded term, the offending country, the value `seen` held for the first
    sighting, or the profile key on a keep.

    `allowed` is that category's country set; pass None to skip the test
    entirely (the CE exporter does — its geography was settled upstream).
    """
    title = title or ""
    hit = next((t for t, rx in patterns if rx.search(title)), None)
    if hit:
        rescue = (None if strict else
                  next((t for t, rx in asked if rx.search(title)), None))
        if not rescue:
            return EXCLUDED, hit

    if allowed:
        c = (country or "").strip()
        if not c:
            if drop_unknown:
                return OUT_OF_COUNTRY, "(blank)"
        elif c not in allowed:
            return OUT_OF_COUNTRY, c

    key = profile_key(url)
    if dedupe and key and key in seen:
        return DUPLICATE, seen[key]
    return KEEP, key


def reconcile(kept, excluded, duplicates, found, out_of_country=0):
    """Assert the three output files account for every person found.

    The CE export reconciled 16,391 + 77 + 118 == 16,586. A silent shortfall
    here means rows were dropped by a code path nobody wrote down, which is the
    failure this whole module exists to prevent.
    """
    total = kept + excluded + duplicates + out_of_country
    if total != found:
        raise SystemExit(
            f"!! export does not reconcile: kept {kept:,} + excluded "
            f"{excluded:,} + out-of-country {out_of_country:,} + duplicates "
            f"{duplicates:,} = {total:,}, but {found:,} people were found. "
            f"Refusing to ship a file that loses people without saying so.")
    return True
