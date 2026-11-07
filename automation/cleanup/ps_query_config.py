"""Load config/icps/<icp>/products_services.yaml and render one Clay search
query per Top level category.

Single resolver for both the single-workbook pass (build_ps_companies.py) and
the fleet driver (build_ps_companies_rollout.py), the same way pipeline_config.py
is the single resolver for the event passes.

The rendered shape (see docs/CLAY_QUERY_SKILL.md):

    select from companies
    where locations.any(country_name in (<countries>))
      and industry in (<industry>)
      and (description contains (<description>)
           or products_and_services is_similar_to (<products_and_services>))
      and description contains (<ev_anchor>)      -- only when ev_anchor: true

Optional per-category clauses (added 2026-11-05 for the Solar buy side, whose
sheet maps whole sectors such as "Manufacturing (all)" and needs more than an
industry list to stay precise). Each is absent from the query unless set:

    naics:          OR'd with `industry` -> (industry in (...) or naics_codes_2022 in (...))
    description_not: and not description contains (...)   -- drops e.g. equipment makers
                    from a buy-side installer search
    company_size:   and company_size in (...)
    people_titles:  and people.exists(is_current = true and job_title contains (...)
                                      [and person.location_country in (<person_countries>)])
                    -- the company must employ the buyer persona today, in a
                       target country when `person_countries` is set (top level
                       or per category).

`group`, `subsegment` (when two searches feed one sub-segment), `priority` and `notes` are metadata for the runner and the review;
the renderer ignores them.

`validate()` fails loud on the two mistakes that a live Clay search silently
swallows rather than erroring on:

  * an `industry` or `country_name` value that is not in Clay's enum — Clay drops
    the unknown value and quietly returns a narrower result set;
  * more than 3 unique `products_and_services` values, which Clay caps.

Both enums are read out of docs/CLAY_QUERY_SKILL.md rather than copied here, so
there is exactly one tracked list of legal values.

    python ps_query_config.py --list
    python ps_query_config.py --print "EV Charging Hardware & Physical Infrastructure"
"""

import argparse
import os
import re
import sys

import yaml

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(SCRIPT_DIR))
SKILL_DOC = os.path.join(REPO, "docs", "CLAY_QUERY_SKILL.md")

DEFAULT_ICP = os.environ.get("CLAY_PIPELINE_ICP", "evcharge")

# Clay caps this per query; values in one list, separate AND comparisons and
# negated comparisons all count toward the same budget.
MAX_PRODUCTS_VALUES = 3

# NAICS sectors Census publishes as ranges; the only non-numeric codes Clay takes.
NAICS_RANGES = {"31-33", "44-45", "48-49"}


class ConfigError(Exception):
    """The query config is wrong — stop, never render a half-valid query."""


def config_path(icp=DEFAULT_ICP):
    return os.path.join(REPO, "config", "icps", icp, "products_services.yaml")


def load(icp=DEFAULT_ICP):
    path = config_path(icp)
    if not os.path.exists(path):
        raise ConfigError(f"no products_services.yaml for ICP {icp!r} ({path})")
    with open(path, encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    # Only `categories` is universal. `countries` may be given per category
    # instead of ICP-wide (the Highways sheet does), and the two anchor lists
    # are an EVCharge device — no other ICP sets `ev_anchor` on a category.
    # Nothing is lost by relaxing this: validate() still fails when a category
    # has no countries from either source, and anchor_list() still raises when a
    # category asks for an anchor list the config does not define.
    if not cfg.get("categories"):
        raise ConfigError(f"{path}: missing or empty 'categories'")
    _expand_persona_companions(cfg)
    return cfg


def _expand_persona_companions(cfg):
    """`persona_companion: {suffix, company_size}` (EVCharge buy side V2, 2026-11-06).

    The persona filter (people_titles) drops big real targets whose staff Clay has not
    typed; on the Solar buy side the user fixed that by hand with "<name> (1,000+ staff)"
    companions (2026-11-06). This writes the same companion for every category that has
    people_titles: the same query without the persona, limited to the given sizes, filed
    under the base sub-segment. It goes straight after its base category, so the base
    search keeps first claim on a company found by both (merge order = plan order).
    A category that sets its own company_size, or `persona_companion: false`, gets none.
    """
    spec = cfg.get("persona_companion")
    if not spec:
        return
    suffix, sizes = spec.get("suffix"), spec.get("company_size")
    if not suffix or not sizes:
        raise ConfigError("persona_companion needs both 'suffix' and 'company_size'")
    out = []
    for cat in cfg["categories"]:
        out.append(cat)
        if (cat.get("people_titles") and not cat.get("company_size")
                and cat.get("persona_companion", True) is not False):
            twin = {k: v for k, v in cat.items() if k not in ("people_titles", "person_countries")}
            twin["name"] = f"{cat['name']} {suffix}"
            twin["subsegment"] = cat.get("subsegment") or cat["name"]
            twin["company_size"] = list(sizes)
            out.append(twin)
    cfg["categories"] = out


# --------------------------------------------------------------------------
# enum values, read from the query-language reference
# --------------------------------------------------------------------------

def _enum_values(field):
    """The quoted values listed for `field` in docs/CLAY_QUERY_SKILL.md.

    The field-doc lines look like:
        - industry (string, enum): ... Values: "Accounting", "Advertising ...
    """
    with open(SKILL_DOC, encoding="utf-8") as fh:
        for line in fh:
            if not line.startswith(f"- {field} (string, enum"):
                continue
            _, _, tail = line.partition(" Values: ")
            if not tail:
                break
            return set(re.findall(r'"([^"]+)"', tail))
    raise ConfigError(f"{SKILL_DOC}: no enum values found for {field!r}")


def validate(cfg):
    """Raise ConfigError listing every problem, rather than the first one."""
    industries = _enum_values("industry")
    countries = _enum_values("country_name")
    problems = []

    for c in cfg.get("countries") or []:
        if c not in countries:
            problems.append(f"country {c!r} is not a Clay country_name value")

    # Per-category country lists. EVCharge gives one list for the whole ICP;
    # the Highways sheet varies COUNTRIES per Top level (6 for Certification and
    # Training, 13 for Construction Equipment), so a category may carry its own
    # and it wins over the ICP-level list. Validated the same way, because an
    # off-enum country is dropped silently by Clay and just narrows the results.
    for cat in cfg["categories"]:
        for c in cat.get("countries") or []:
            if c not in countries:
                problems.append(
                    f"{cat.get('name')}: country {c!r} is not a Clay "
                    f"country_name value")
        if not (cat.get("countries") or cfg.get("countries")):
            problems.append(
                f"{cat.get('name')}: no countries, and the ICP has no default list")

    for cat in cfg["categories"]:
        try:
            anchor_list(cfg, cat)
        except ConfigError as e:
            problems.append(str(e))

    seen = set()
    for cat in cfg["categories"]:
        name = cat.get("name")
        if not name:
            problems.append("a category has no name")
            continue
        if name in seen:
            problems.append(f"duplicate category {name!r}")
        seen.add(name)
        # A category needs EITHER a positive industry list OR an exclusion.
        # "All Industries (exclude Aviation/Aerospace)" is the second shape: no
        # include list at all, so the country + description clauses carry the
        # whole filter and the exclusion trims the sheet's carve-out.
        if not cat.get("industry") and not cat.get("industry_not"):
            problems.append(f"{name}: no industry or industry_not values")
        if cat.get("industry") and cat.get("industry_not"):
            problems.append(
                f"{name}: has BOTH industry and industry_not — a positive "
                f"include list already excludes everything not listed, so the "
                f"exclusion is dead weight (docs/CLAY_QUERY_SKILL.md)")
        for ind in cat.get("industry_not") or []:
            if ind not in industries:
                problems.append(f"{name}: industry_not {ind!r} is not a Clay enum value")
        if not cat.get("description"):
            problems.append(f"{name}: no description keywords")
        for ind in cat.get("industry") or []:
            if ind not in industries:
                problems.append(f"{name}: industry {ind!r} is not a Clay enum value")
        prods = cat.get("products_and_services") or []
        if len(set(prods)) > MAX_PRODUCTS_VALUES:
            problems.append(
                f"{name}: {len(set(prods))} products_and_services values, "
                f"Clay caps it at {MAX_PRODUCTS_VALUES}")
        for field in ("description", "products_and_services", "description_not"):
            vals = cat.get(field) or []
            lowered = [v.lower() for v in vals]
            dupes = {v for v in lowered if lowered.count(v) > 1}
            if dupes:
                problems.append(f"{name}: {field} repeats {sorted(dupes)} "
                                f"(matching is case-insensitive)")
            for v in vals:
                if '"' in v:
                    problems.append(f"{name}: {field} value {v!r} contains a quote")

    _validate_optional_clauses(cfg, problems)

    if problems:
        raise ConfigError("query config is invalid:\n  - " + "\n  - ".join(problems))
    return True


def _validate_optional_clauses(cfg, problems):
    """naics / company_size / people_titles / person_countries — the clauses
    only some ICPs use. Read lazily so an ICP that sets none of them never
    depends on those enum lists being present in the reference."""
    cats = cfg["categories"]
    if any(c.get("naics") for c in cats):
        # The reference truncates the NAICS value list ("..."), so it can't be
        # read as an enum. Check the documented SHAPE instead: a 2-6 digit
        # code, or one of the three sector ranges Census publishes.
        for cat in cats:
            for code in cat.get("naics") or []:
                if not (isinstance(code, str) and
                        (re.fullmatch(r"\d{2,6}", code) or code in NAICS_RANGES)):
                    problems.append(f"{cat.get('name')}: naics {code!r} is not a 2-6 digit "
                                    f"code or one of {sorted(NAICS_RANGES)} (quote it in YAML)")
            if cat.get("naics") and not cat.get("industry"):
                problems.append(f"{cat.get('name')}: naics is OR'd with industry, "
                                f"so it needs a positive industry list")
    if any(c.get("company_size") for c in cats):
        sizes = _enum_values("company_size")
        for cat in cats:
            for v in cat.get("company_size") or []:
                if v not in sizes:
                    problems.append(f"{cat.get('name')}: company_size {v!r} is not a Clay value")
    person_lists = [cfg.get("person_countries") or []] + [c.get("person_countries") or [] for c in cats]
    if any(person_lists):
        # Companies and people use DIFFERENT country enums ("Brunei" vs
        # "Brunei Darussalam", "Ivory Coast" vs "Côte d'Ivoire"), so the person
        # list is checked against the people enum, never the companies one.
        people_ctys = _enum_values("location_country")
        for lst in person_lists:
            for c in lst:
                if c not in people_ctys:
                    problems.append(f"person country {c!r} is not a Clay location_country value")
    for cat in cats:
        overlap = ({v.lower() for v in cat.get("description") or []} &
                   {v.lower() for v in cat.get("description_not") or []})
        if overlap:
            problems.append(f"{cat.get('name')}: {sorted(overlap)} is both required and "
                            f"excluded, so the search can never match it")
        titles = cat.get("people_titles") or []
        lowered = [t.lower() for t in titles]
        if len(set(lowered)) != len(lowered):
            problems.append(f"{cat.get('name')}: people_titles repeats a value")
        for t in titles:
            if '"' in t:
                problems.append(f"{cat.get('name')}: people_titles value {t!r} contains a quote")


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------

def _quoted(values):
    return ", ".join(f'"{v}"' for v in values)


# A category's `ev_anchor` selects WHICH anchor list is ANDed on, not just
# whether one is. `true` -> the strict EV list; `adjacent` -> the wider
# kerbside/decarbonisation list, for sellers who are real EVCharge exhibitors
# but never write "EV" on their profile; `false`/absent -> none.
ANCHOR_SETS = {True: "ev_anchor", "adjacent": "adjacent_anchor"}


def anchor_list(cfg, cat):
    """The anchor keywords for a category, or [] when it takes none.

    Two spellings. The general one — `anchor: <name>` on the category, resolved
    against a top-level `anchors:` mapping — is what non-EVCharge ICPs use; the
    Highways config anchors its horizontal categories on road/highway language
    the same way EVCharge anchors on EV language. The EVCharge `ev_anchor:
    true|adjacent` spelling is kept working verbatim: it is live config.
    """
    named = cat.get("anchor")
    if named:
        anchors = cfg.get("anchors") or {}
        if named not in anchors:
            raise ConfigError(
                f"{cat.get('name')!r}: anchor {named!r} is not defined in the "
                f"config's top-level 'anchors' mapping")
        return anchors[named]

    setting = cat.get("ev_anchor", False)
    if setting is False or setting is None:
        return []
    if setting not in ANCHOR_SETS:
        raise ConfigError(
            f"{cat.get('name')!r}: ev_anchor must be true, false or "
            f"'adjacent', got {setting!r}")
    key = ANCHOR_SETS[setting]
    if not cfg.get(key):
        raise ConfigError(f"ev_anchor {setting!r} needs a top-level {key!r} list")
    return cfg[key]


def render(cfg, cat):
    """The Clay search query for one category, as pasted into Query mode."""
    # A category's own `countries` wins over the ICP-level list (the Highways
    # sheet sets COUNTRIES per Top level); absent, the ICP list applies.
    ctys = cat.get("countries") or cfg.get("countries")
    lines = [
        "select from companies",
        f'where locations.any(country_name in ({_quoted(ctys)}))',
    ]
    if cat.get("industry") and cat.get("naics"):
        lines.append(f'  and (industry in ({_quoted(cat["industry"])})')
        lines.append(f'       or naics_codes_2022 in ({_quoted(cat["naics"])}))')
    elif cat.get("industry"):
        lines.append(f'  and industry in ({_quoted(cat["industry"])})')
    elif cat.get("industry_not"):
        lines.append(f'  and not industry in ({_quoted(cat["industry_not"])})')
    desc = f'description contains ({_quoted(cat["description"])})'
    prods = cat.get("products_and_services") or []
    if prods:
        lines.append(f"  and ({desc}")
        lines.append(f'       or products_and_services is_similar_to ({_quoted(prods)}))')
    else:
        lines.append(f"  and {desc}")
    anchor = anchor_list(cfg, cat)
    if anchor:
        lines.append(f'  and description contains ({_quoted(anchor)})')
    if cat.get("description_not"):
        lines.append(f'  and not description contains ({_quoted(cat["description_not"])})')
    if cat.get("company_size"):
        lines.append(f'  and company_size in ({_quoted(cat["company_size"])})')
    titles = cat.get("people_titles") or []
    if titles:
        # One people.exists with the whole title list (the reference's
        # "collapse" rule), current role only, and the person in a target
        # country when a person country list is set.
        inner = f'is_current = true and job_title contains ({_quoted(titles)})'
        pctys = cat.get("person_countries") or cfg.get("person_countries")
        if pctys:
            inner += f' and person.location_country in ({_quoted(pctys)})'
        lines.append(f'  and people.exists({inner})')
    return "\n".join(lines)


def categories(icp=DEFAULT_ICP):
    """[(name, query)] in config order, after validation."""
    cfg = load(icp)
    validate(cfg)
    return [(c["name"], render(cfg, c)) for c in cfg["categories"]]


def search_plan(icp=DEFAULT_ICP):
    """[{name, group, priority, query}] in config order, after validation —
    what a CLI runner needs to file each search's results under its workbook."""
    cfg = load(icp)
    validate(cfg)
    plan = [{"name": c["name"], "group": c.get("group") or c["name"],
             "subsegment": c.get("subsegment") or c["name"],
             "priority": c.get("priority", ""), "query": render(cfg, c)}
            for c in cfg["categories"]]
    # `tiers: {<group number>: <tier>}` puts the tier in the plan, so the merge stops
    # falling back to its built-in Solar map (groups 1-4 A, 5-7 B, 8 C). EVCharge V2 has
    # no tier column and sets `tiers: {}` -> Tier blank.
    if "tiers" in cfg:
        tiers = {str(k): v for k, v in (cfg["tiers"] or {}).items()}
        for p in plan:
            p["tier"] = tiers.get(p["group"].split(".")[0].strip(), "")
    return plan


def query_for(name, icp=DEFAULT_ICP):
    for cat_name, q in categories(icp):
        if cat_name == name:
            return q
    raise ConfigError(f"no category named {name!r} in {config_path(icp)}")


def add_cli_args(ap):
    ap.add_argument("--icp", default=DEFAULT_ICP,
                    help=f"ICP whose products_services.yaml to read "
                         f"(default {DEFAULT_ICP}, or $CLAY_PIPELINE_ICP)")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    add_cli_args(ap)
    ap.add_argument("--list", action="store_true",
                    help="category names and query sizes")
    ap.add_argument("--print", metavar="CATEGORY",
                    help="the rendered query for one category")
    ap.add_argument("--print-all", action="store_true")
    ap.add_argument("--plan-json", metavar="PATH",
                    help="write search_plan() as JSON, for clay_company_search.py "
                         "(which runs in WSL, where PyYAML may be absent)")
    args = ap.parse_args()

    if args.plan_json:
        import json
        try:
            plan = search_plan(args.icp)
        except ConfigError as e:
            print(e)
            return 1
        with open(args.plan_json, "w", encoding="utf-8") as fh:
            json.dump({"icp": args.icp, "searches": plan}, fh, ensure_ascii=False, indent=1)
        print(f"{args.icp}: {len(plan)} searches -> {args.plan_json}")
        return 0

    try:
        cats = categories(args.icp)
    except ConfigError as e:
        print(e)
        return 1

    if args.print:
        print(query_for(args.print, args.icp))
        return 0
    if args.print_all:
        for name, q in cats:
            print(f"### {name}\n\n{q}\n")
        return 0

    print(f"{args.icp}: {len(cats)} categories")
    # Ask the config whether a category is anchored, not the rendered text. The
    # old test looked for "and description contains" on the LAST line, which is
    # equally true of any category that has no products_and_services clause — it
    # reported three unanchored contractor categories as anchored, in the very
    # output used to judge whether a category is scoped correctly.
    cfg = load(args.icp)
    by_name = {c["name"]: c for c in cfg["categories"]}
    for name, q in cats:
        anchored = "anchored" if anchor_list(cfg, by_name[name]) else "-"
        print(f"  {len(q):5d} chars  {anchored:8s}  {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
