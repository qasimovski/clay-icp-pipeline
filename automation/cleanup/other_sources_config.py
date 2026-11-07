"""Read `config/icps/<icp>/other_sources.yaml` and resolve a row's countries.

The Other Sources people search takes its geography from the SOURCE, not from
the ICP category (user, 2026-09-14) — a CECA member is a UK contractor, so its
people should be UK. This module is the only place that mapping lives.

THE ONE REAL SUBTLETY: `Source Name` in the Clay table is semicolon-joined,
because the table is deduped across directories — "ADEPT Member Directory;
LCRIG Member Directory" is one company found in two. Countries are therefore the
UNION over the tokens: a company in both a UK and an Australian directory is
legitimately searchable in both.

An unrecognised token is an ERROR, never a silent empty list. A row that
resolved to no countries would search every country on earth, which is the
failure `ps_people_config.render()` refuses for the same reason.

    python other_sources_config.py                 # print the map
    python other_sources_config.py --check rows.json   # validate a table dump
"""

import argparse
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(SCRIPT_DIR))
sys.path.insert(0, SCRIPT_DIR)

import ps_query_config as psq  # noqa: E402

DEFAULT_ICP = "highways"


class UnknownSource(Exception):
    """A Source Name token with no entry in the config."""


def config_path(icp=None):
    return os.path.join(REPO, "config", "icps", icp or DEFAULT_ICP,
                        "other_sources.yaml")


def load(icp=None):
    import yaml
    with open(config_path(icp), encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    if not cfg or not cfg.get("sources"):
        raise psq.ConfigError(f"{config_path(icp)} has no `sources`")
    return cfg


def index(cfg):
    """{token seen in the table -> entry}. Both spellings resolve."""
    out = {}
    for s in cfg["sources"]:
        for key in (s.get("table_name"), s["name"]):
            if key:
                out[key.strip().lower()] = s
    return out


def countries_for(source_name, idx):
    """Union of the countries of every directory this row came from.

    Order is preserved and stable so two rows from the same pair of sources
    render an identical query — a set would reorder between runs and make the
    queries look different for no reason.
    """
    out, seen = [], set()
    tokens = [t.strip() for t in str(source_name or "").split(";") if t.strip()]
    if not tokens:
        raise UnknownSource("row has no Source Name")
    for t in tokens:
        entry = idx.get(t.lower())
        if entry is None:
            raise UnknownSource(t)
        for c in entry.get("countries") or []:
            if c not in seen:
                seen.add(c)
                out.append(c)
    if not out:
        raise UnknownSource(f"{source_name!r} resolved to no countries")
    return out


def validate(cfg):
    """Every country must be a Clay `country_name` enum value.

    Same check ps_query_config applies to the company searches: Clay drops an
    unknown value silently and just returns fewer people, with no error.
    """
    valid = psq._enum_values("country_name")
    problems = []
    for s in cfg["sources"]:
        if not s.get("name"):
            problems.append("a source has no name")
        for c in s.get("countries") or []:
            if c not in valid:
                problems.append(f"{s.get('name')}: {c!r} is not a Clay country_name")
        if not (s.get("countries") or []):
            problems.append(f"{s.get('name')}: no countries")
    if problems:
        raise psq.ConfigError("other_sources config is invalid:\n  - "
                              + "\n  - ".join(problems))
    return True


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--icp", default=DEFAULT_ICP)
    ap.add_argument("--check", help="a JSON dump of table rows to validate")
    args = ap.parse_args()

    cfg = load(args.icp)
    validate(cfg)
    idx = index(cfg)
    print(f"{len(cfg['sources'])} sources, config valid\n")
    for s in cfg["sources"]:
        print(f"   {s['name'][:50]:52} {', '.join(s['countries'])}")

    if args.check:
        import collections
        import json
        rows = json.load(open(args.check, encoding="utf-8"))
        ok, bad = collections.Counter(), collections.Counter()
        for r in rows:
            try:
                ok[tuple(countries_for(r.get("Source Name", ""), idx))] += 1
            except UnknownSource as e:
                bad[str(e)] += 1
        print(f"\n{sum(ok.values())} rows resolved, {sum(bad.values())} failed")
        for k, n in ok.most_common():
            print(f"   {n:5}  {', '.join(k)}")
        for k, n in bad.most_common():
            print(f"   !! {n:5}  {k}")
        return 1 if bad else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
