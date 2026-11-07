"""Build the workflow that runs the people search over the CLASSIFIED companies.

    Manual -> Build Company Record -> ICP Mapping -> Searchable? -+- no  -> Skipped - Unmapped
                                                                  +- yes -> Find People

WHY A SECOND WORKFLOW, having deliberately EXTENDED the first one. The user's
instruction was to add nodes to the existing workflow rather than duplicate it,
and that is what `extend_v2_people.py` did — the people nodes live in v2 and are
the ones proven on Keller, Colas and Tarmac. The problem is only the ENTRY
POINT for the bulk run:

  * v2's graph starts at the Registrar, an AGENT node. Replaying it over the
    4,547 already-classified companies would re-pay for classifications we
    already hold in `Highways CE - all tables combined.csv` — and a re-run is
    not even guaranteed to agree with the CSV we shipped, because the model is
    not deterministic.
  * A workflow cannot be entered part-way in bulk. `clay workflows nodes test`
    runs a single node for a single row; `clay routines runs start --bulk`
    always enters at the trigger.

So this workflow duplicates no logic — it CONSUMES v2's output. `Side` and
`Classification` arrive as trigger inputs from the feed CSV instead of from the
Registrar, and every node after that is byte-identical to v2's, generated from
the same YAML by the same `extend_v2_people.build_icp()`. Regenerate both with
`--update` and they cannot drift.

WHAT IS **NOT** RE-CHECKED HERE, and why that is safe. v2's `Has Domain?` and
`Is New?` gates are absent. They are not needed and could not work anyway:

  * `build_ce_people_feed.py` already applied both — a row reaches the feed only
    if it has a Domain AND was `Is New` at classification time. Skip-by-absence:
    a company that is not in the feed cannot be searched by any later mistake.
  * v2's `Read Is New` is a pure CODE node reading the value the Supabase column
    already wrote into the table. It never calls the ledger, so re-running would
    not re-check anything regardless.

MONEY. One `cpj-find-lists-of-people` call per row, up to 10 people each. The
`Searchable?` gate is defence-in-depth only: the feed is already filtered to
rows that resolve, so it should route ~0 rows to `Skipped - Unmapped`. If it
routes many, something upstream changed — stop and look.

    python build_ce_people_workflow.py --dry-run   # print the specs, create nothing
    python build_ce_people_workflow.py             # create the workflow + nodes
    python build_ce_people_workflow.py --update    # push a YAML change to its mapping node
"""

import argparse
import json
import os
import subprocess
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

import extend_v2_people as V2  # noqa: E402

ENV = {**os.environ, "MSYS_NO_PATHCONV": "1"}
NODE_DIR = os.path.join(SCRIPT_DIR, "ce_nodes")
# Written on creation and read by ce_people_rollout.py, so the runner never has
# an id hard-coded that could drift from the workflow that actually exists.
IDS = os.path.join(NODE_DIR, "people_workflow.json")

WF_NAME = "Highways - CE - People"

# Feed columns that become trigger inputs. Must match build_ce_people_feed.py's
# COLUMNS exactly — a typo here produces empty strings, not an error.
TRIGGER_FIELDS = ("Company Name", "Domain", "Side", "Classification",
                  "Company Event Name", "Exhibitor/Sponsor",
                  "Countries Override")

BUILD_CODE = '''def handler(context):
    # Shaping only. Unlike v2's node of the same name there is no description to
    # resolve and no ledger verdict to read: this workflow's input is already
    # classified, already domain-gated and already deduped by the feed builder.
    def s(key):
        v = context.get_input(key)
        return "" if v is None else str(v).strip()

    return {
        "company_name": s("Company Name"),
        "domain": s("Domain"),
        "side": s("Side"),
        "classification": s("Classification"),
        "event": s("Company Event Name"),
        "source": s("Exhibitor/Sponsor"),
        "countries_override": s("Countries Override"),
    }
'''


def clay(args, infile=None):
    cmd = "clay " + args
    if infile:
        cmd += " --input '" + infile.replace("C:\\", "/mnt/c/").replace("\\", "/") + "'"
    r = subprocess.run(["wsl", "bash", "-lc", cmd], capture_output=True, env=ENV)
    if r.returncode != 0:
        raise SystemExit(f"FAILED: {cmd}\n  {r.stderr.decode('utf-8','replace')[:500]}")
    return json.loads(r.stdout.decode("utf-8", "replace"))


def write(name, obj):
    os.makedirs(NODE_DIR, exist_ok=True)
    fp = os.path.join(NODE_DIR, name)
    json.dump(obj, open(fp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return fp


def create(wf, name, spec, existing):
    """Create the node, or update the one already carrying that name.

    Idempotent because a build that dies part-way (a rejected field, a killed
    process) otherwise leaves orphan nodes that a re-run would DUPLICATE rather
    than repair — and a duplicated node in a paid graph is not a cosmetic
    problem. Matching on the node's name is safe here: this workflow is built
    only by this script and every name in it is unique.
    """
    fp = write(f"p_{name}.json", spec)
    if spec["name"] in existing:
        nid = existing[spec["name"]]
        body = {k: v for k, v in spec.items() if k != "nodeType"}
        node = clay(f"workflows nodes update {wf} {nid}", write(f"p_{name}.json", body))
        node["nodeId"] = nid
        verb = "~"
    else:
        node = clay(f"workflows nodes create {wf}", fp)
        verb = "+"
    for u in node.get("appliedUpdates", []):
        if not u.get("success"):
            raise SystemExit(f"node {name}: field not applied: {u}")
    print(f"  {verb} {spec['name']:24} {node['nodeId']}")
    return node["nodeId"]


def wire(wf, target, edges, label):
    """Attach an incoming edge. Conditional branches carry their rule id."""
    clay(f"workflows nodes update {wf} {target}",
         write(f"pe_{label}.json", {"incomingEdges": edges}))


def specs(icp, ids):
    """Node specs. `ids` is filled in as nodes are created, so order matters."""
    mapping_code = V2.MAPPING_CODE % json.dumps(icp, ensure_ascii=False)
    out = {}

    out["build"] = {
        "nodeType": "code",
        "name": "Build Company Record",
        "description": "Shape the feed row. No enrichment, no gates — both ran upstream.",
        "code": BUILD_CODE,
        "inputSchema": {"type": "object", "properties": {
            f: {"type": "string", "sourceNodeId": ids.get("trigger"),
                "sourcePath": f"$['{f}']"} for f in TRIGGER_FIELDS}},
        "outputSchema": {
            "company_name": {"type": "string", "description": "Company name"},
            "domain": {"type": "string", "description": "Domain, the search key"},
            "side": {"type": "string", "description": "Buy Side / Sell Side"},
            "classification": {"type": "string", "description": "ICP category"},
            "event": {"type": "string", "description": "Company Event Name"},
            "source": {"type": "string", "description": "Exhibitor or Sponsor"},
            "countries_override": {"type": "string",
                                   "description": "CSV of countries, or blank"},
        },
    }

    # Identical to v2's ICP Mapping except that Side and Classification come
    # from the feed rather than from the Registrar.
    out["mapping"] = {
        "nodeType": "code",
        "name": "ICP Mapping",
        "description": ("Side + Classification -> persona, seniority, countries. "
                        "Generated from the ICP YAML by extend_v2_people.py."),
        "code": mapping_code,
        "inputSchema": {"type": "object", "properties": {
            "side": {"type": "string", "sourceNodeId": ids.get("build"),
                     "sourcePath": "$.side"},
            "classification": {"type": "string", "sourceNodeId": ids.get("build"),
                               "sourcePath": "$.classification"},
            "countries_override": {"type": "string",
                                   "sourceNodeId": ids.get("build"),
                                   "sourcePath": "$.countries_override"}}},
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

    out["gate"] = {
        "nodeType": "conditional",
        "name": "Searchable?",
        "description": "Persona and geography both resolved? Unmapped rows never search.",
        "conditionalMode": "rules",
        "inputSchema": {"type": "object", "properties": {
            "searchable": {"type": "boolean", "sourceNodeId": ids.get("mapping"),
                           "sourcePath": "$.searchable"}}},
        "rulesConditionalConfig": {"rules": [
            {"id": "not_searchable", "name": "No ICP mapping - stop",
             "condition": {"type": "GroupOp", "combinationMode": "And", "items": [
                 {"type": "BinOp", "dataPath": ["searchable"], "operator": "False"}]}},
            {"id": "searchable", "name": "Mapped - find people",
             "condition": {"type": "GroupOp", "combinationMode": "And", "items": [
                 {"type": "BinOp", "dataPath": ["searchable"], "operator": "True"}]}},
        ]},
    }

    out["skip"] = {
        "nodeType": "code",
        "name": "Skipped - Unmapped",
        "description": "Terminal for rows with no ICP persona. No people call, no spend.",
        "code": ('def handler(context):\n'
                 '    # Emitted rather than dropped so every company is accounted for.\n'
                 '    def s(key):\n'
                 '        v = context.get_input(key)\n'
                 '        return "" if v is None else str(v).strip()\n'
                 '\n'
                 '    return {"company_name": s("company_name"), "domain": s("domain"),\n'
                 '            "people_found": 0, "skipped_reason": s("reason")}\n'),
        "inputSchema": {"type": "object", "properties": {
            "company_name": {"type": "string", "sourceNodeId": ids.get("build"),
                             "sourcePath": "$.company_name"},
            "domain": {"type": "string", "sourceNodeId": ids.get("build"),
                       "sourcePath": "$.domain"},
            "reason": {"type": "string", "sourceNodeId": ids.get("mapping"),
                       "sourcePath": "$.unmapped_reason"}}},
        # FLAT {name: {type, description}}, not a JSON Schema object. `nodes get`
        # reads it back expanded into full JSON Schema, which makes the wrong
        # shape look right — writing that back is rejected. Same trap as v2.
        "outputSchema": {
            "company_name": {"type": "string", "description": "Company name"},
            "domain": {"type": "string", "description": "Domain"},
            "people_found": {"type": "number", "description": "Always 0"},
            "skipped_reason": {"type": "string", "description": "Why no search ran"},
        },
    }

    # Byte-identical to v2's Find People, including the static parameters whose
    # reasons are recorded in extend_v2_people's docstring.
    out["find"] = {
        "nodeType": "tool",
        "name": "Find People",
        "description": "cpj-find-lists-of-people, driven by the ICP Mapping node.",
        "tools": [{
            "toolType": "clay_action",
            "actionKey": V2.FIND_PEOPLE_KEY,
            "actionPackageId": V2.FIND_PEOPLE_PKG,
            "description": "Find contacts at company",
            "inputMappingConfig": {
                "company_identifier": {"type": "reference", "expression": "{{domain}}"},
                "job_title_keywords": {"type": "reference",
                                       "expression": "{{job_title_keywords}}"},
                "job_title_exclude_keywords": {"type": "reference",
                                               "expression": "{{job_title_exclude_keywords}}"},
                "job_title_seniority_levels_v2": {"type": "reference",
                                                  "expression": "{{seniority_levels}}"},
                "location_countries_include": {"type": "reference",
                                               "expression": "{{countries}}"},
                "job_title_mode": {"type": "static", "value": "contain"},
                "job_title_seniority_match_mode": {"type": "static", "value": "exact"},
                "include_past_experiences": {"type": "static", "value": False},
                # FALSE, changed 2026-09-14. `identifiers_only` only raises
                # the result cap from 10 to 500 and our limit is 10, so it
                # costs nothing — and switching it off returns first/last name
                # and structured_location, without which a returned person has
                # no country and the ICP country filter cannot be applied to
                # them at all. CE and Other Sources ran BEFORE this flip, so
                # their results carry only name/title/url.
                "identifiers_only": {"type": "static", "value": False},
                "limit": {"type": "static", "value": 10},
            },
        }],
        "inputSchema": {"type": "object", "properties": {
            "domain": {"type": "string", "sourceNodeId": ids.get("build"),
                       "sourcePath": "$.domain"},
            "job_title_keywords": {"type": "string", "sourceNodeId": ids.get("mapping"),
                                   "sourcePath": "$.job_title_keywords"},
            "job_title_exclude_keywords": {"type": "string",
                                           "sourceNodeId": ids.get("mapping"),
                                           "sourcePath": "$.job_title_exclude_keywords"},
            "seniority_levels": {"type": "string", "sourceNodeId": ids.get("mapping"),
                                 "sourcePath": "$.seniority_levels"},
            "countries": {"type": "string", "sourceNodeId": ids.get("mapping"),
                          "sourcePath": "$.countries"}}},
    }
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--workflow",
                    help="adopt an existing EMPTY workflow instead of creating "
                         "one (recovery path: a create that failed part-way "
                         "leaves a workflow behind)")
    ap.add_argument("--update", action="store_true",
                    help="regenerate the ICP table and push it to the existing "
                         "workflow's mapping node")
    args = ap.parse_args()

    icp = V2.build_icp()
    print(f"sell persona    : {len(icp['sell_titles'])} titles")
    print(f"sell categories : {len(icp['sell_countries'])} with countries")
    print(f"buy categories  : {len(icp['buy'])}")
    print(f"exclusions      : {len(icp['buy_exclude'])} (both sides)")

    if args.update:
        saved = json.load(open(IDS, encoding="utf-8"))
        code = V2.MAPPING_CODE % json.dumps(icp, ensure_ascii=False)
        r = clay(f"workflows nodes update {saved['workflow']} {saved['mapping']}",
                 write("p_mapping_update.json", {"code": code}))
        print(f"\n~ ICP Mapping {saved['mapping']} {r.get('operation')}")
        return

    if args.dry_run:
        for k, v in specs(icp, {}).items():
            print(f"\n--- {k} ---")
            print(json.dumps({kk: (f"<{len(vv)} chars>" if kk == "code" else vv)
                              for kk, vv in v.items()}, indent=1)[:700])
        return

    # Guard against building a SECOND workflow by accident. `--workflow` is the
    # deliberate recovery path, so it is allowed to re-enter an existing build.
    if os.path.exists(IDS) and not args.workflow:
        raise SystemExit(f"{IDS} already exists — this workflow was built. Use "
                         f"--update to push a config change, --workflow <id> to "
                         f"repair it, or delete the file to build a second one.")

    wf = args.workflow or clay(f'workflows create --name "{WF_NAME}"')["id"]
    print(f"\nworkflow {wf}")

    # `workflows create` makes a genuinely EMPTY workflow — no trigger, no
    # nodes. The trigger has to be created before anything can reference it,
    # and creating it is what produces the trigger NODE that inputSchema binds
    # to; the trigger's own id is a different thing and is not usable there.
    # `workflows create` makes a genuinely EMPTY workflow — no trigger, no
    # nodes. The trigger has to exist before anything can reference it, and
    # creating it is what produces the trigger NODE that inputSchema binds to;
    # the trigger's own id is a different thing and is not usable there.
    # Guarded because a second create would add a SECOND trigger, not replace.
    TRIGGER_DOC = (
        ("Company Name", "Company name"),
        ("Domain", "Domain — the people-search key"),
        ("Side", "Buy Side / Sell Side, from the Registrar"),
        ("Classification", "ICP category, from the Registrar"),
        ("Company Event Name", "Provenance: which event"),
        ("Exhibitor/Sponsor", "Provenance: which table"),
        ("Countries Override", "Optional CSV of countries. Other Sources takes "
                               "its geography from the source directory, not "
                               "the ICP category; blank = use the ICP list"),
    )
    if clay(f"workflows graph get {wf}").get("summary", {}).get("triggers"):
        print("  trigger already present — not creating a second one")
    else:
        clay(f"workflows triggers create {wf}", write("p_trigger.json", {
            "triggerType": "manual",
            "inputSchema": {"type": "object", "required": [], "properties": {
                f: {"type": "string", "description": d} for f, d in TRIGGER_DOC}}}))

    g = clay(f"workflows graph get {wf}")
    nodes = g.get("summary", {}).get("nodes") or g.get("nodes") or []
    ids = {"workflow": wf}
    existing = {}
    for n in nodes:
        if n.get("nodeType") == "trigger":
            ids["trigger"] = n.get("id")
        elif n.get("name"):
            existing[n["name"]] = n.get("id")
    if not ids.get("trigger"):
        raise SystemExit("workflow still has no trigger node — inspect it by hand")
    print(f"  trigger {ids['trigger']}")
    if existing:
        print(f"  adopting {len(existing)} existing node(s): {sorted(existing)}")

    # Created in dependency order: every inputSchema names a node that exists.
    for key in ("build", "mapping", "gate", "skip", "find"):
        ids[key] = create(wf, key, specs(icp, ids)[key], existing)

    # An inputSchema reference usually wires the data edge, but two cases need
    # the edge attached by hand: the trigger (which is not a data source in the
    # same sense) and a conditional's BRANCH edges, which carry a rule id.
    # A plain edge for each link in the chain...
    wire(wf, ids["build"], [{"sourceNode": ids["trigger"]}], "build")
    wire(wf, ids["mapping"], [{"sourceNode": ids["build"]}], "mapping")
    wire(wf, ids["gate"], [{"sourceNode": ids["mapping"]}], "gate")
    # ...and the two branch edges, which carry the rule they fire on.
    wire(wf, ids["skip"], [{"sourceNode": ids["gate"], "ruleId": "not_searchable",
                            "ruleName": "No ICP mapping - stop"}], "skip")
    wire(wf, ids["find"], [{"sourceNode": ids["gate"], "ruleId": "searchable",
                            "ruleName": "Mapped - find people"}], "find")

    json.dump(ids, open(IDS, "w", encoding="utf-8"), indent=1)
    v = clay(f"workflows graph validate {wf}")
    print(f"\ngraph valid: {v.get('valid')}  errors: {v.get('errors')}")
    print(f"ids -> {IDS}")
    print(json.dumps(ids, indent=1))


if __name__ == "__main__":
    main()
