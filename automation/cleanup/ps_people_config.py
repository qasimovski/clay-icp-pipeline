"""Render the seller-side people query for one Products & Services workbook.

Reads config/icps/<icp>/seller_people.yaml (the `Seller JTs` sheet) and reuses
ps_query_config for the country list, so the people and company searches can
never drift apart on geography.

The company scope is NOT stored in config. Clay prefills the Find-people editor
with its own

    select from people
    where clay.filter_to_companies(@table("t_...:gv_...:f_..."))

naming live table/view/filter ids that differ per workbook, and per
docs/SENSITIVE_DATA.md never belong in tracked source. `render()` takes that
clause as an argument; `company_scope_from_prefill()` extracts it. If it cannot
be found the caller must stop — a people query without it searches ALL of Clay,
not the workbook's companies.

    python ps_people_config.py --print
    python ps_people_config.py --print --non-sector
"""

import argparse
import os
import re
import sys

import yaml

import ps_query_config as psq

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(SCRIPT_DIR))

DEFAULT_ICP = psq.DEFAULT_ICP

# Clay's experience seniority enum. A value outside it is dropped silently by
# the search rather than erroring, which would quietly widen the persona.
SENIORITY_VALUES = {
    "Founder", "Owner", "Board Member", "Partner", "C-suite", "VP", "Director",
    "Head", "Manager", "Senior", "Mid-level", "Entry", "Intern / In Training",
    "Unknown",
}

# The whole prefilled clause, kept verbatim including the @table(...) reference.
_SCOPE_RE = re.compile(r"clay\.filter_to_companies\(@table\(\"[^\"]+\"\)\)")


class ConfigError(psq.ConfigError):
    pass


def config_path(icp=DEFAULT_ICP):
    return os.path.join(REPO, "config", "icps", icp, "seller_people.yaml")


def load(icp=DEFAULT_ICP):
    path = config_path(icp)
    if not os.path.exists(path):
        raise ConfigError(f"no seller_people.yaml for ICP {icp!r} ({path})")
    with open(path, encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    for key in ("per_company_limit", "seniority", "titles_exact",
                "titles_contains", "titles_contains_non_sector"):
        if not cfg.get(key):
            raise ConfigError(f"{path}: missing or empty {key!r}")
    # EVCharge sets one country list for the whole ICP; Highways sets them per
    # Top level (the sheet varies COUNTRIES per category), so this may be None
    # and render() then requires an explicit `countries` argument.
    cfg["countries"] = psq.load(icp).get("countries")
    return cfg


def validate(cfg):
    problems = []
    bad = [s for s in cfg["seniority"] if s not in SENIORITY_VALUES]
    if bad:
        problems.append(f"seniority values not in Clay's enum: {bad}")
    for junior in ("Entry", "Intern / In Training"):
        if junior in cfg["seniority"]:
            problems.append(
                f"{junior!r} is in seniority, but the sheet excludes it "
                f"(Seniority = All except Intern/Trainee, Assistant, Entry, "
                f"Freelance)")
    if not isinstance(cfg["per_company_limit"], int) or \
            cfg["per_company_limit"] < 1:
        problems.append("per_company_limit must be a positive integer")
    for s_only in cfg.get("senior_only_seniority") or []:
        if s_only not in SENIORITY_VALUES:
            problems.append(f"senior_only_seniority {s_only!r} is not in "
                            f"Clay's seniority enum")
    if cfg.get("titles_contains_senior_only") and             not cfg.get("senior_only_seniority"):
        problems.append("titles_contains_senior_only needs senior_only_seniority")
    for field in ("titles_exact", "titles_contains",
                  "titles_contains_non_sector"):
        lowered = [t.lower() for t in cfg[field]]
        dupes = {t for t in lowered if lowered.count(t) > 1}
        if dupes:
            problems.append(f"{field} repeats {sorted(dupes)} "
                            f"(matching is case-insensitive)")
    overlap = ({t.lower() for t in cfg["titles_contains"]} &
               {t.lower() for t in cfg["titles_contains_non_sector"]})
    if overlap:
        problems.append(
            f"titles_contains_non_sector repeats {sorted(overlap)} already in "
            f"titles_contains — the merged list would carry a duplicate")
    if problems:
        raise ConfigError("seller_people config is invalid:\n  - "
                          + "\n  - ".join(problems))
    return True


def company_scope_from_prefill(prefill):
    """The clay.filter_to_companies(...) clause out of Clay's prefilled query.

    Returns None when absent — the caller MUST treat that as fatal. Replacing
    the editor without this clause turns a search over one workbook's companies
    into a search over the whole Clay database.
    """
    if not prefill:
        return None
    m = _SCOPE_RE.search(prefill)
    return m.group(0) if m else None


def _quoted(values):
    return ", ".join(f'"{v}"' for v in values)


def render(cfg, company_scope, non_sector=False, countries=None):
    """The people query for one workbook, with Clay's own company scope kept.

    `countries` overrides the ICP-level list. Required for ICPs that set
    countries PER CATEGORY (Highways), so a category's people search matches the
    geography of the company search that built its table.

    Two optional branches, both driven by config and both absent from the
    EVCharge seller_people.yaml, which defines neither key:

      exact_relevance             ANDed onto the `job_title in (...)` branch, so
                                  a bare "MD"/"COO" only counts when the ROLE's
                                  own experience description carries sector
                                  language. Deliberately NOT applied to the
                                  contains branch — those titles (Highways,
                                  Infrastructure, Transport ...) already name
                                  the sector, and the user asked to skip it
                                  there (2026-08-14).
      titles_contains_senior_only ORed as its own branch WITH ITS OWN seniority
                                  list, for titles the sheet restricts further
                                  ("Operations = Senior level only"). Kept
                                  separate rather than flattened into the
                                  permissive list, which would have admitted
                                  every Operations Coordinator.
    """
    if not company_scope:
        raise ConfigError(
            "refusing to render a people query with no company scope: without "
            "clay.filter_to_companies(...) the search covers all of Clay")
    ctys = countries or cfg.get("countries")
    if not ctys:
        raise ConfigError(
            "no countries: this ICP sets them per category, so render() needs "
            "an explicit countries= list")
    contains = list(cfg["titles_contains"])
    if non_sector:
        contains += cfg["titles_contains_non_sector"]

    exact = f'job_title in ({_quoted(cfg["titles_exact"])})'
    if cfg.get("exact_relevance"):
        exact = (f'(job_title in ({_quoted(cfg["titles_exact"])})\n'
                 f'          and description contains '
                 f'({_quoted(cfg["exact_relevance"])}))')
    branches = [exact, f'job_title contains ({_quoted(contains)})']
    if cfg.get("titles_contains_senior_only"):
        branches.append(
            f'(job_title contains '
            f'({_quoted(cfg["titles_contains_senior_only"])})\n'
            f'          and seniority in '
            f'({_quoted(cfg["senior_only_seniority"])}))')

    lines = ["select from people", "where experiences.count(",
             "    is_current = true",
             f'    and seniority in ({_quoted(cfg["seniority"])})',
             f'    and ({branches[0]}']
    for b in branches[1:]:
        lines.append(f'         or {b}')
    lines[-1] += ")"
    lines += [
        f'    and company.locations.count(country_name in '
        f'({_quoted(ctys)})) >= 1',
        "  ) >= 1",
        f"  and {company_scope}",
        f'limit {cfg["per_company_limit"]} by clay_company_id',
    ]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    psq.add_cli_args(ap)
    ap.add_argument("--print", action="store_true")
    ap.add_argument("--non-sector", action="store_true",
                    help="include the column-D sector keywords")
    args = ap.parse_args()
    cfg = load(args.icp)
    validate(cfg)
    if args.print:
        print(render(cfg, 'clay.filter_to_companies(@table("t_X:gv_X:f_X"))',
                     non_sector=args.non_sector))
        return 0
    contains = len(cfg["titles_contains"])
    print(f"{args.icp}: {len(cfg['titles_exact'])} exact titles, "
          f"{contains} contains keywords "
          f"(+{len(cfg['titles_contains_non_sector'])} non-sector), "
          f"{len(cfg['seniority'])} seniority levels, "
          f"{len(cfg['countries'])} countries, "
          f"cap {cfg['per_company_limit']}/company")
    return 0


if __name__ == "__main__":
    sys.exit(main())
