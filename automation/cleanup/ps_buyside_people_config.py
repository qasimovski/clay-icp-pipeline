"""Render the BUY-SIDE people query for one Buyside P&S workbook.

Reads config/icps/<icp>/people.yaml. Separate from ps_people_config because the
shape genuinely differs: the seller side has ONE persona applied to every
workbook, while the buy side has a DIFFERENT job-title list per top level (the
sheet's Job Titles column varies by row block).

Countries come from the same ICP's products_services.yaml, so the People and
Companies searches can never drift apart on geography.

The company scope is Clay's own `clay.filter_to_companies(@table(...))`, read off
its prefill at run time — it names live table/view/filter ids that differ per
workbook and never belong in tracked config (docs/SENSITIVE_DATA.md).

    python ps_buyside_people_config.py --list
    python ps_buyside_people_config.py --print "Transport, Mobility & Logistics"
"""

import argparse
import os
import sys

import yaml

import ps_people_config as ppc     # company_scope_from_prefill, SENIORITY_VALUES
import ps_query_config as psq

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(SCRIPT_DIR))
DEFAULT_ICP = "evcharge_buyside"

ConfigError = psq.ConfigError


def config_path(icp=DEFAULT_ICP):
    return os.path.join(REPO, "config", "icps", icp, "people.yaml")


def load(icp=DEFAULT_ICP):
    path = config_path(icp)
    if not os.path.exists(path):
        raise ConfigError(f"no people.yaml for ICP {icp!r} ({path})")
    with open(path, encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    for key in ("per_company_limit", "seniority", "senior_titles",
                "exclude_titles", "categories"):
        if not cfg.get(key):
            raise ConfigError(f"{path}: missing or empty {key!r}")
    cfg["countries"] = psq.load(icp)["countries"]
    return cfg


def validate(cfg):
    problems = []
    bad = [s for s in cfg["seniority"] if s not in ppc.SENIORITY_VALUES]
    if bad:
        problems.append(f"seniority values not in Clay's enum: {bad}")
    for junior in ("Entry", "Intern / In Training"):
        if junior in cfg["seniority"]:
            problems.append(f"{junior!r} is in seniority but the sheet excludes it")
    if not isinstance(cfg["per_company_limit"], int) or cfg["per_company_limit"] < 1:
        problems.append("per_company_limit must be a positive integer")

    excl = {t.lower() for t in cfg["exclude_titles"]}
    seen = set()
    for cat in cfg["categories"]:
        name = cat.get("name")
        if not name:
            problems.append("a category has no name")
            continue
        if name in seen:
            problems.append(f"duplicate category {name!r}")
        seen.add(name)
        if not cat.get("titles"):
            problems.append(f"{name}: no titles")
        # A title that is ALSO excluded cancels itself out: the query would ask
        # for it and reject it in the same predicate, silently returning nobody
        # for that term. This is exactly the `Facilities` collision the user
        # resolved by hand — fail loud rather than ship a self-defeating filter.
        clash = sorted({t for t in (cat.get("titles") or []) if t.lower() in excl})
        if clash:
            problems.append(
                f"{name}: title(s) {clash} are also in exclude_titles — the "
                f"query would request and reject them in one predicate")
    senior_clash = sorted({t for t in cfg["senior_titles"] if t.lower() in excl})
    if senior_clash:
        problems.append(f"senior_titles {senior_clash} are also excluded")

    if problems:
        raise ConfigError("buyside people config is invalid:\n  - "
                          + "\n  - ".join(problems))
    return True


def _quoted(values):
    return ", ".join(f'"{v}"' for v in values)


def titles_for(cfg, name):
    for cat in cfg["categories"]:
        if cat["name"] == name:
            # De-duplicate case-insensitively while preserving order: a repeat
            # is harmless to Clay but makes the query hard to read back.
            out, seen = [], set()
            for t in list(cat["titles"]) + list(cfg["senior_titles"]):
                if t.lower() not in seen:
                    seen.add(t.lower())
                    out.append(t)
            return out
    raise ConfigError(f"no category named {name!r} in {config_path()}")


def render(cfg, name, company_scope):
    if not company_scope:
        raise ConfigError(
            "refusing to render a people query with no company scope: without "
            "clay.filter_to_companies(...) the search covers all of Clay")
    # A category may add an EXACT-title branch — the sheet's "Job Title Must
    # Match Exactly" rows (Parking Operations, and the two contractor tiers).
    # When the config also defines `exact_relevance`, that branch is ANDed with
    # a sector condition on the ROLE's own experience description: a bare
    # "COO"/"CTO"/"Managing Director" says nothing about highways by itself.
    #
    # The contains branch is deliberately left alone — those titles (Highways,
    # Roads, Asset Management, Network Operations …) already name the domain,
    # and the user asked to skip the extra condition there (2026-08-15).
    #
    # Absent both keys the shape is byte-identical to before, so the EVCharge
    # buy-side config renders exactly as it did.
    exact = next((c.get("titles_exact") for c in cfg["categories"]
                  if c["name"] == name), None)
    if exact:
        branch = f'job_title in ({_quoted(exact)})'
        if cfg.get("exact_relevance"):
            branch = (f'(job_title in ({_quoted(exact)})\n'
                      f'          and description contains '
                      f'({_quoted(cfg["exact_relevance"])}))')
        title_clause = [
            f'    and ({branch}',
            f'         or job_title contains ({_quoted(titles_for(cfg, name))}))',
        ]
    else:
        title_clause = [
            f'    and job_title contains ({_quoted(titles_for(cfg, name))})']

    return "\n".join([
        "select from people",
        "where experiences.count(",
        "    is_current = true",
        f'    and seniority in ({_quoted(cfg["seniority"])})',
        *title_clause,
        f'    and not job_title contains ({_quoted(cfg["exclude_titles"])})',
        f'    and company.locations.count(country_name in '
        f'({_quoted(cfg["countries"])})) >= 1',
        "  ) >= 1",
        f"  and {company_scope}",
        f'limit {cfg["per_company_limit"]} by clay_company_id',
    ])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--icp", default=DEFAULT_ICP)
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--print", metavar="CATEGORY")
    args = ap.parse_args()
    cfg = load(args.icp)
    validate(cfg)
    if args.print:
        print(render(cfg, args.print,
                     'clay.filter_to_companies(@table("t_X:gv_X:f_X"))'))
        return 0
    print(f"{args.icp}: {len(cfg['categories'])} categories, "
          f"{len(cfg['senior_titles'])} senior titles, "
          f"{len(cfg['exclude_titles'])} exclusions, "
          f"cap {cfg['per_company_limit']}/company")
    for cat in cfg["categories"]:
        print(f"  {len(titles_for(cfg, cat['name'])):>3} titles  {cat['name']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
