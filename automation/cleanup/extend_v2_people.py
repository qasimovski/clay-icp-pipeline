"""Extend the v2 classification workflow with the people search.

Adds four nodes AFTER the existing `Highways Registrar`, leaving everything
upstream untouched:

    Highways Registrar → ICP Mapping → Searchable? ─┬─ no  → Skipped - Unmapped
                                                     └─ yes → Find People → People Found

WHY EXTEND RATHER THAN BUILD A SECOND WORKFLOW. The gates that make this
affordable — `Has Domain?` and the `Is New?` ledger check — already sit upstream
of the Registrar. A separate people workflow would have to re-implement both or
run ungated, and the second mistake is the expensive one.

WHAT THE ICP MAPPING NODE DOES. `Side` chooses the persona source, exactly as
the ICP workbook is organised:

    Sell Side → persona from `Seller JTs` (one shared list for every category)
                countries from `Products_Services`, per Top level
    Buy Side  → persona AND countries from `BuySide ICP`, per Industry

`Classification` is the Top level / Industry in both, which is what the
Registrar emits, so it keys straight in. The ICP tables are generated INTO the
node from the YAML configs by this script, so the configs stay the source of
truth and a drift between them and the live node is a re-run away from being
fixed.

ACCURACY, and what changed from v1's Find People node. v1 passed four
parameters; the action accepts 46. The ones that matter here:

  job_title_seniority_levels_v2   the ICP's seniority list, mapped to Clay's
                                  slugs. v1 had NO seniority filter at all, so
                                  it returned interns and assistants that the
                                  sheet explicitly excludes.
  job_title_seniority_match_mode  "exact" — our list is explicit, not a floor.
  job_title_mode                  "contain" — docs/PEOPLE_PASSES.md rule 2:
                                  `in` is whole-value and misses "Group CEO",
                                  "CEO, EMEA"; compound titles concentrate at
                                  senior level, which is exactly who we want.
  location_countries_include      the category's countries. v1 used the generic
                                  `locations`, which accepts cities or countries
                                  and is therefore ambiguous.
  include_past_experiences        false — the ClayQL equivalent of
                                  `is_current = true`. Without it a person who
                                  LEFT the company still matches.
  limit                           10, per the user. Also the action's documented
                                  ceiling ("Defaults to 10. Maximum 10") — v1
                                  set 15, silently over it.
  identifiers_only                true — name, title, profile URL. That is all
                                  the work-email waterfall needs downstream, and
                                  it is what v1 did.
  job_title_exclude_keywords      the user's exclusion list (2026-09-12), the
                                  same 54 terms on both sides. Eight of the 57
                                  terms they gave were dropped because they
                                  collide with titles the ICP sheets search for;
                                  five seniority terms Clay's enum cannot express
                                  (Apprentice, Assistant, Graduate, Student,
                                  Volunteer) were carried over from the buy-side
                                  list. The YAML comments carry the full ledger.
                                  v1 sent no exclusions at all.

TWO DELIBERATE FIDELITY GAPS, both recorded rather than hidden:

  1. `exact_relevance` is NOT applied. In ClayQL it ANDs sector language onto
     the bare-title branch only, so a plain "Managing Director" has to prove it
     works in roads. The action has no branches — `job_description_keywords`
     would apply to every title at once and would drop a "Highways Director"
     whose experience description happens not to repeat the word. It is also
     largely redundant here: the COMPANY has already been classified into a
     Highways ICP category by the Registrar, which is the check that branch was
     standing in for.

  2. `Operations` is dropped from the seller list. The sheet restricts it to
     senior level only, and a flat call cannot carry a per-title seniority rule.
     Including it would admit every Operations Coordinator — and with only 10
     slots per company, a weak title does not just add noise, it displaces a
     better person. If you want it back, the clean way is a second call with
     `job_title_seniority_match_mode: "floor"` at director level.

    python extend_v2_people.py --dry-run    # print the generated node specs
    python extend_v2_people.py              # create and wire the nodes
    python extend_v2_people.py --update     # push a YAML change to the live node
"""

import argparse
import json
import os
import subprocess
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
sys.path.insert(0, os.path.join(os.path.dirname(SCRIPT_DIR),
                                 "build_automation"))

import ps_people_config as sellercfg          # noqa: E402
import ps_buyside_people_config as buyercfg   # noqa: E402
import ps_query_config as psq                 # noqa: E402

ENV = {**os.environ, "MSYS_NO_PATHCONV": "1"}
# Live Clay ids come from gitignored config (docs/SENSITIVE_DATA.md); a literal
# here would be committed. Fails loud rather than resolving to None and building
# a URL like ".../workflows/None".
def _id(key):
    import browser_session
    v = browser_session.local_setting(key, "CLAY_" + key.upper())
    if not v:
        raise SystemExit(f"missing `{key}` in config/local.yaml "
                         f"(or $CLAY_{key.upper()}) — see config/local.yaml.example")
    return v


WF = _id("highways_ce_workflow_id")
REGISTRAR = _id("highways_ce_registrar_node_id")
BUILD = _id("highways_ce_build_node_id")
# The live ICP Mapping node, so an ICP edit can be pushed with --update
# instead of creating a second one beside it.
MAPPING = _id("highways_ce_mapping_node_id")
FIND_PEOPLE_PKG = "e251a70e-46d7-4f3a-b3ef-a211ad3d8bd2"
FIND_PEOPLE_KEY = "cpj-find-lists-of-people"

SELL_ICP, BUY_ICP = "highways", "highways_buyside"

# Our YAML seniority names -> the action's enum slugs, read off the live schema.
# A value the action does not recognise is dropped SILENTLY, taking the whole
# filter's meaning with it, so this map is asserted complete before use.
SENIORITY_SLUG = {
    "Founder": "founder", "Owner": "owner", "Board Member": "board-member",
    "Partner": "partner", "C-suite": "c-suite", "VP": "vp",
    "Director": "director", "Head": "head", "Manager": "manager",
    "Senior": "senior", "Mid-level": "mid-level",
}

# See gap 2 in the module docstring.
SELLER_DROP = {"Operations"}


def build_icp():
    """The ICP tables to embed in the mapping node, generated from the configs."""
    sell = sellercfg.load(SELL_ICP)
    buy = buyercfg.load(BUY_ICP)

    unknown = [s for s in sell["seniority"] + buy["seniority"]
               if s not in SENIORITY_SLUG]
    if unknown:
        raise SystemExit(f"seniority values with no Clay slug: {sorted(set(unknown))}")

    def dedupe(titles):
        # Case-insensitively, preserving order.
        seen, out = set(), []
        for t in titles:
            if t.lower() not in seen:
                seen.add(t.lower())
                out.append(t)
        return out

    ordered = dedupe(t for t in (list(sell["titles_exact"]) + list(sell["titles_contains"]))
                     if t not in SELLER_DROP)

    # The sheet's "Job Title Must Match Exactly" rows (COO, CTO, CEO on the
    # contractor tiers and Parking Operations). `titles_for` returns only the
    # contains list + senior_titles, so before 2026-10-19 a bare "COO" or "CTO"
    # was never searched — senior_titles carries only the spelled-out forms.
    # Added to the one contains list like the sell side's exact titles; the
    # company is already classified into the category, which is the check
    # `exact_relevance` stood in for (gap 1 above).
    def buy_titles(name):
        cat = next(c for c in buy["categories"] if c["name"] == name)
        return dedupe(list(buyercfg.titles_for(buy, name))
                      + list(cat.get("titles_exact") or []))

    sell_countries = {c["name"]: c.get("countries") or []
                      for c in psq.load(SELL_ICP)["categories"]}
    buy_countries = psq.load(BUY_ICP).get("countries") or []

    return {
        "sell_titles": ordered,
        "sell_seniority": [SENIORITY_SLUG[s] for s in sell["seniority"]],
        # Same 54 terms on both sides: the user gave ONE list "for all queries".
        "sell_exclude": list(sell.get("exclude_titles") or []),
        "sell_countries": {k: v for k, v in sell_countries.items() if v},
        "buy_seniority": [SENIORITY_SLUG[s] for s in buy["seniority"]],
        "buy_exclude": list(buy["exclude_titles"]),
        "buy_countries": buy_countries,
        "buy": {c["name"]: buy_titles(c["name"]) for c in buy["categories"]},
    }


MAPPING_CODE = '''import json

# Generated from config/icps/highways/seller_people.yaml (Seller JTs),
# config/icps/highways_buyside/people.yaml (BuySide ICP) and the matching
# products_services.yaml country lists, by extend_v2_people.py. Regenerate
# rather than hand-editing: the YAML is the source of truth.
ICP = %s

# Optional per-row split of the ICP seniority list into two searches. The action
# caps a search at 10 people; at a company that hit that cap, searching each
# half separately returns up to 20 with the same titles, exclusions and
# countries. The halves partition Clay's levels, so A + B covers exactly the
# ICP list — a group only ever narrows it, never adds a level.
SENIORITY_GROUPS = {
    "A": ["founder", "owner", "board-member", "partner", "c-suite", "vp", "director"],
    "B": ["head", "manager", "senior", "mid-level"],
}


def handler(context):
    def s(key):
        v = context.get_input(key)
        return "" if v is None else str(v).strip()

    side = s("side")
    cat = s("classification")
    group = s("seniority_group").upper()

    # Optional per-row geography. Other Sources takes its countries from the
    # SOURCE directory (a CECA member is a UK contractor), not from the ICP
    # category, so the caller passes them in. Absent — which is every CE and
    # P&S row — this is "" and the ICP list applies exactly as before.
    override = [c.strip() for c in s("countries_override").split(",") if c.strip()]

    titles, seniority, exclude, countries = [], [], [], []
    if side == "Sell Side":
        # One shared persona; only the geography varies by Top level.
        # Membership is tested explicitly rather than inferred from a non-empty
        # country list, because an override would otherwise let an off-list
        # Classification through with a persona it never earned.
        if cat in ICP["sell_countries"]:
            titles = ICP["sell_titles"]
            seniority = ICP["sell_seniority"]
            exclude = ICP["sell_exclude"]
            countries = override or ICP["sell_countries"][cat]
    elif side == "Buy Side":
        titles = ICP["buy"].get(cat) or []
        if titles:
            seniority = ICP["buy_seniority"]
            exclude = ICP["buy_exclude"]
            countries = override or ICP["buy_countries"]

    # An unknown group stops the row rather than falling back to the full list:
    # a typo must not quietly turn a half-search into a whole one.
    bad_group = bool(group) and group not in SENIORITY_GROUPS
    if group and not bad_group:
        seniority = [x for x in seniority if x in SENIORITY_GROUPS[group]]

    # Searchable only when the persona AND the geography both resolved. An
    # unresolved row would otherwise search every title in every country — the
    # `All other industries` catch-all lands here by design, and inspection
    # showed 89%% of those companies carry no roads language at all.
    searchable = bool(side and cat and titles and countries and seniority
                      and not bad_group)
    reason = ""
    if not searchable:
        if bad_group:
            reason = "unknown seniority group '" + group + "'"
        elif not side or not cat:
            reason = "not classified"
        elif side not in ("Sell Side", "Buy Side"):
            reason = "unrecognised side"
        else:
            reason = "no ICP mapping for '" + cat + "'"

    return {
        "searchable": searchable,
        "unmapped_reason": reason,
        "job_title_keywords": ", ".join(titles) if searchable else "",
        "job_title_exclude_keywords": ", ".join(exclude) if searchable else "",
        "seniority_levels": ", ".join(seniority) if searchable else "",
        "countries": ", ".join(countries) if searchable else "",
        "title_count": len(titles),
        "country_count": len(countries),
        "persona": ("Seller JTs" if side == "Sell Side" else
                    ("BuySide ICP / " + cat if side == "Buy Side" else "")),
        "seniority_group": group,
    }
'''


def clay(args, infile=None):
    cmd = "clay " + args
    if infile:
        cmd += " --input '" + infile.replace("C:\\", "/mnt/c/").replace("\\", "/") + "'"
    r = subprocess.run(["wsl", "bash", "-lc", cmd], capture_output=True, env=ENV)
    if r.returncode != 0:
        raise SystemExit(f"FAILED: {cmd}\n  {r.stderr.decode('utf-8','replace')[:400]}")
    return json.loads(r.stdout.decode("utf-8", "replace"))


def write(name, obj):
    p = os.path.join(SCRIPT_DIR, "ce_nodes")
    os.makedirs(p, exist_ok=True)
    fp = os.path.join(p, name)
    json.dump(obj, open(fp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return fp


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--update", action="store_true",
                    help="regenerate the ICP table and update the LIVE mapping "
                         "node in place, rather than creating a new one")
    args = ap.parse_args()

    icp = build_icp()
    print(f"sell persona      : {len(icp['sell_titles'])} titles "
          f"({len(SELLER_DROP)} dropped: {sorted(SELLER_DROP)})")
    print(f"sell seniority    : {icp['sell_seniority']}")
    print(f"sell categories   : {len(icp['sell_countries'])} with countries")
    print(f"buy categories    : {len(icp['buy'])}")
    print(f"buy countries     : {icp['buy_countries']}")
    print(f"buy exclusions    : {len(icp['buy_exclude'])}")

    mapping_code = MAPPING_CODE % json.dumps(icp, ensure_ascii=False)
    print(f"\nmapping node code : {len(mapping_code):,} chars")

    specs = {}
    specs["mapping"] = {
        "nodeType": "code",
        "name": "ICP Mapping",
        "description": ("Side + Classification -> persona, seniority, countries. "
                        "Generated from the ICP YAML by extend_v2_people.py."),
        "code": mapping_code,
        "inputSchema": {"type": "object", "properties": {
            "side": {"type": "string", "sourceNodeId": REGISTRAR,
                     "sourcePath": "$.Side"},
            "classification": {"type": "string", "sourceNodeId": REGISTRAR,
                               "sourcePath": "$.Classification"}}},
        "outputSchema": {
            "searchable": {"type": "boolean", "description": "persona AND geography resolved"},
            "unmapped_reason": {"type": "string", "description": "why not searchable"},
            "job_title_keywords": {"type": "string", "description": "CSV of titles"},
            "job_title_exclude_keywords": {"type": "string", "description": "CSV of exclusions"},
            "seniority_levels": {"type": "string", "description": "CSV of Clay seniority slugs"},
            "countries": {"type": "string", "description": "CSV of countries"},
            "title_count": {"type": "number", "description": "titles applied"},
            "country_count": {"type": "number", "description": "countries applied"},
            "persona": {"type": "string", "description": "which ICP list was used"},
        },
    }

    if args.dry_run:
        for k, v in specs.items():
            print(f"\n--- {k} ---")
            print(json.dumps({kk: (vv if kk != 'code' else f'<{len(vv)} chars>')
                              for kk, vv in v.items()}, indent=1)[:900])
        print("\n--- generated ICP (head) ---")
        print(json.dumps(icp, ensure_ascii=False, indent=1)[:900])
        return

    if args.update:
        # Only `code` changes when the YAML changes — the wiring and the schemas
        # are unaffected, so re-sending them would be noise in appliedUpdates.
        MAP = clay(f"workflows nodes update {WF} {MAPPING}",
                   write("n_mapping_update.json", {"code": mapping_code}))
        MAP["nodeId"] = MAPPING
        print(f"\n~ ICP Mapping  {MAPPING}  {MAP.get('operation')}")
    else:
        MAP = clay(f"workflows nodes create {WF}",
                   write("n_mapping.json", specs["mapping"]))
        print(f"\n+ ICP Mapping  {MAP['nodeId']}")
    for u in MAP.get("appliedUpdates", []):
        if not u.get("success"):
            print(f"   !! {u}")
    print(json.dumps({"mapping": MAP["nodeId"]}))


if __name__ == "__main__":
    main()
