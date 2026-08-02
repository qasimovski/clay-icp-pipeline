#!/usr/bin/env python3
"""Render BUILD_PROMPT.template.md for a given entity type + ICP.

Usage:
    python render_build_prompt.py --entity exhibitors --icp labs
    python render_build_prompt.py --entity sponsors --icp labs --out /tmp/spec.md

Reads config/entity-types/<entity>.yaml, config/icps/<icp>/icp.yaml, and
config/local.yaml (falls back to config/local.yaml.example with a warning
if you haven't created your own local.yaml yet), fills in every {{PLACEHOLDER}}
in BUILD_PROMPT.template.md, and writes a paste-ready build spec — the same
shape as docs/examples/interphex-build-spec.md, but generated instead of
hand-written.

Requires PyYAML (`pip install pyyaml`) — the one dependency this repo has,
because the config files are hand-edited YAML, not JSON.
"""
import argparse
import os
import sys

try:
    import yaml
except ImportError:
    sys.exit("This script needs PyYAML: pip install pyyaml")

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_yaml(path):
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_local_config():
    real = os.path.join(REPO_ROOT, "config", "local.yaml")
    example = os.path.join(REPO_ROOT, "config", "local.yaml.example")
    if os.path.exists(real):
        return load_yaml(real)
    print(f"warning: {real} not found, using {example} placeholders instead. "
          f"Copy local.yaml.example to local.yaml and fill in your workspace details.",
          file=sys.stderr)
    return load_yaml(example)


def format_taxonomy_block(taxonomy):
    lines = ["Buyer (company sources/uses this vertical's products & services):"]
    for i, item in enumerate(taxonomy["buyer"], 1):
        suffix = f" ({item['examples']})" if item.get("examples") else ""
        lines.append(f"{i}) {item['label']}{suffix}")
    lines.append("")
    lines.append("Seller (company supplies/markets this vertical's products & services):")
    for i, item in enumerate(taxonomy["seller"], 1):
        suffix = f" ({item['examples']})" if item.get("examples") else ""
        lines.append(f"{i}) {item['label']}{suffix}")
    return "\n".join(lines)


def format_tie_break_rules(rules):
    return "\n".join(f"   - {r}" for r in rules)


def format_country_normalization(mapping):
    return ", ".join(f"`{k}`→{v}" for k, v in mapping.items())


def format_fit_lookup_table(taxonomy):
    lines = ["    | Segment/Category | Fit |", "    |---|---|"]
    for item in taxonomy["buyer"] + taxonomy["seller"]:
        lines.append(f"    | {item['label']} | {item['fit']} |")
    return "\n".join(lines)


def format_country_fit_block(country_fit):
    def side_block(side_name, side):
        a = ", ".join(side.get("a", []))
        b = ", ".join(side.get("b", []))
        return (f"    - {side_name}: `Normalized Country` ∈ {{{a}}} → A. "
                f"∈ {{{b}}} → B. Else → C.")
    return "\n".join([
        side_block("Seller", country_fit["seller"]),
        side_block("Buyer", country_fit["buyer"]),
    ])


def format_composite_tier_seller(entity_cfg, icp_cfg):
    mode = entity_cfg["composite_tier"]["seller_mode"]
    if mode == "fit_and_country_fit":
        matrix = icp_cfg["composite_tier_seller_matrix"]
        by_tier = {}
        for k, v in matrix.items():
            by_tier.setdefault(v, []).append(k)
        parts = [f"{'/'.join(sorted(ks))}→{t}" for t, ks in sorted(by_tier.items())]
        return ("2D matrix on (`Fit`, `Country Fit`): " + ", ".join(parts) +
                ". Resolves fully here.")
    if mode == "country_fit_only":
        return ("`Country Fit` alone drives the tier — A→1, B→2, C→3. Category/Fit "
                "is not combined into a matrix for this entity type's seller tiering; "
                "JT Fit is applied separately at the contact level.")
    return f"(unrecognized composite_tier.seller_mode: {mode!r} — check entity-type config)"


def format_known_issues(icp_cfg):
    issues = icp_cfg.get("known_issues") or {}
    if not issues:
        return "None recorded for this ICP."
    lines = []
    for key, text in issues.items():
        lines.append(f"**{key}**: {text.strip()}")
    return "\n\n".join(lines)


def format_seller_contact_titles_block():
    return ("See `config/icps/<icp>/lookups/seller_contact_titles.csv` "
            "(or its `.example.csv` schema) for the full title list, "
            "split into senior titles (JT Fit B, Tier 1, no sector filter) "
            "and commercial keywords (JT Fit C, Tier 2, sector filter required).")


def render(entity_key, icp_key):
    entity_cfg = load_yaml(os.path.join(REPO_ROOT, "config", "entity-types", f"{entity_key}.yaml"))
    icp_cfg = load_yaml(os.path.join(REPO_ROOT, "config", "icps", icp_key, "icp.yaml"))
    local_cfg = load_local_config()

    with open(os.path.join(REPO_ROOT, "template", "BUILD_PROMPT.template.md"),
              "r", encoding="utf-8") as f:
        text = f.read()

    tables = entity_cfg["tables"]
    field_sources = entity_cfg["field_sources"]
    run_conditions = entity_cfg["run_conditions"]

    replacements = {
        "{{ICP_NAME}}": icp_cfg["icp_name"],
        "{{ENTITY_TYPE}}": entity_cfg["entity_type"],
        "{{SOURCE_CSV}}": entity_cfg["source_csv"],
        "{{MAIN_TABLE}}": tables["main"],
        "{{SELLER_TABLE}}": tables["seller"],
        "{{BUYER_TABLE}}": tables["buyer"],
        "{{SELLER_CONTACTS_TABLE}}": tables["seller_contacts"],
        "{{BUYER_CONTACTS_TABLE}}": tables["buyer_contacts"],
        "{{WORKSPACE_URL}}": local_cfg["workspace_url"],
        "{{WORKSPACE_NAME}}": local_cfg["workspace_name"],
        "{{EVENTS_FOLDER}}": local_cfg["events_folder"],
        "{{BLOCKLIST_TABLE}}": local_cfg["blocklist_table"],
        "{{RAW_COLUMNS}}": ", ".join(entity_cfg["raw_columns"]),
        "{{WEBSITE_SOURCE_FIELD}}": field_sources["website"],
        "{{COUNTRY_SOURCE_FIELD}}": field_sources["country"],
        "{{DESCRIPTION_SOURCE_FIELD}}": field_sources["description"],
        "{{ENRICH_COMPANY_FIELDS}}": ", ".join(entity_cfg["enrich_company_fields"]),
        "{{COUNTRY_NORMALIZATION_BLOCK}}": format_country_normalization(icp_cfg["country_normalization"]),
        "{{CLASSIFIER_NAME}}": icp_cfg["classifier"]["name"],
        "{{CLASSIFIER_TAXONOMY_BLOCK}}": format_taxonomy_block(icp_cfg["classifier"]["taxonomy"]),
        "{{CLASSIFIER_TIE_BREAK_RULES_BLOCK}}": format_tie_break_rules(icp_cfg["classifier"]["tie_break_rules"]),
        "{{FIT_LOOKUP_TABLE}}": format_fit_lookup_table(icp_cfg["classifier"]["taxonomy"]),
        "{{COUNTRY_FIT_BLOCK}}": format_country_fit_block(icp_cfg["country_fit"]),
        "{{COMPOSITE_TIER_SELLER_MODE}}": entity_cfg["composite_tier"]["seller_mode"],
        "{{COMPOSITE_TIER_SELLER_BLOCK}}": format_composite_tier_seller(entity_cfg, icp_cfg),
        "{{SELLER_CONTACT_TITLES_BLOCK}}": format_seller_contact_titles_block(),
        "{{KNOWN_ISSUES_BLOCK}}": format_known_issues(icp_cfg),
        "{{OFFICIAL_DOMAIN_GATING_TEXT}}": (
            "only runs when the domain is still unknown after normalization —"
            if run_conditions.get("official_domain_skip_if_domain_known") else
            "runs unconditionally —"
        ),
        "{{FANOUT_GATING_TEXT}}": (
            "Gate each action on `{{Side}}` matching its destination (do not send Buyer rows to the Seller table or vice versa)."
            if run_conditions.get("people_fanout_only_if_side") else
            "(no run conditions configured for this entity type — see docs/KNOWN_ISSUES.md)"
        ),
    }

    for placeholder, value in replacements.items():
        text = text.replace(placeholder, str(value))

    # Note: literal {{ColumnName}} references (e.g. {{Description}}, {{Side}})
    # are intentional Clay field-interpolation syntax meant to stay verbatim
    # in the rendered prompt/formula text for Clay to interpret — only our
    # own ALL_CAPS placeholder tokens indicate a template bug if left unfilled.
    unresolved = [p for p in replacements if p in text]
    if unresolved:
        print("warning: these template placeholders were not substituted:", file=sys.stderr)
        for p in unresolved:
            print(f"  {p}", file=sys.stderr)

    return text


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--entity", required=True, help="e.g. exhibitors, sponsors")
    parser.add_argument("--icp", required=True, help="e.g. labs")
    parser.add_argument("--out", help="output path (default: stdout)")
    args = parser.parse_args()

    rendered = render(args.entity, args.icp)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(rendered)
        print(f"wrote {args.out}", file=sys.stderr)
    else:
        print(rendered)


if __name__ == "__main__":
    main()
