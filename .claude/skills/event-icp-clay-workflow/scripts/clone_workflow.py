"""Copy an existing Clay workflow's graph into a NEW workflow (draft), node for node.

Solar (Oct 2026): the people workflow is the same graph for every ICP. Only the generated
`ICP Mapping` node differs, so a new ICP starts from the last good one (Mining's) instead of a
hand rebuild. After cloning, regenerate that node from the new icp.yaml:

    python clone_workflow.py --source wf_<mining people> --name "Solar - Competitors v2 - People" \
        --state runs/clone_state.json
    python icp_mapping.py payload config/icps/solar/icp.yaml --build-node-id <new Build node id> \
        --out mapping_update.json
    clay workflows nodes update <new wf> <new ICP Mapping node id> --input mapping_update.json

What it does:
  * creates the workflow, then the manual trigger with the source trigger's inputSchema (optionally
    replaced with --trigger-schema file.json);
  * creates every other node in edge order, with every sourceNodeId (inputSchema, tool inputSchema)
    and incoming edge (with its ruleId) remapped to the new node ids;
  * writes outputSchema FLAT ({name: {type, description}}): the expanded JSON-Schema shape that
    `graph get` returns is the documented write trap;
  * drops read-only fields (id, toolId, outgoingEdges, recentOutputPaths, inputParameters).
Creating a draft costs nothing. Nothing is published or run. Resumable: the old -> new id map is
saved to --state after every node, so a rerun skips what exists.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _clay import clay, to_cli_path  # noqa: E402

COPY = ["name", "description", "nodeType", "inputSchema", "outputSchema", "code", "tools", "retryConfig",
        "conditionalMode", "rulesConditionalConfig", "agentConditionalConfig", "delaySeconds", "listMode",
        "listFailureMode", "listEntriesRef", "batchRunSettings", "agentPrompt", "agentModel",
        "agentInternetSearchEnabled", "agentType", "agentName", "inputMappingConfig", "subworkflowId",
        "mergeInputMappingConfig"]
TOOL_DROP = {"toolId", "inputParameters"}


def remap(obj, ids):
    """Every 'sourceNodeId' / 'sourceNode' value -> the new node id (recursively)."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k in ("sourceNodeId", "sourceNode") and isinstance(v, str):
                if v not in ids:
                    raise SystemExit(f"node references {v}, which has not been created yet")
                out[k] = ids[v]
            else:
                out[k] = remap(v, ids)
        return out
    if isinstance(obj, list):
        return [remap(x, ids) for x in obj]
    return obj


def node_refs(obj):
    """Every node id a node reads from (sourceNodeId anywhere in its config)."""
    if isinstance(obj, dict):
        out = {obj["sourceNodeId"]} if isinstance(obj.get("sourceNodeId"), str) else set()
        for k, v in obj.items():
            if k not in ("incomingEdges", "outgoingEdges"):
                out |= node_refs(v)
        return out
    if isinstance(obj, list):
        return set().union(*(node_refs(x) for x in obj)) if obj else set()
    return set()


def flat_output(schema):
    if not schema or "properties" not in schema:
        return schema
    return {k: {kk: vv for kk, vv in v.items() if kk in ("type", "description")}
            for k, v in schema["properties"].items()}


def spec_file(state_path, name, spec):
    """Specs go to the CLI as a FILE: inline JSON with code in it is mangled by the WSL shell quoting."""
    d = os.path.join(os.path.dirname(os.path.abspath(state_path)), "clone_specs")
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, "".join(c if c.isalnum() else "_" for c in name) + ".json")
    json.dump(spec, open(p, "w", encoding="utf-8"), indent=1)
    return to_cli_path(p)


def save(path, st):
    tmp = path + ".tmp"
    json.dump(st, open(tmp, "w", encoding="utf-8"), indent=1)
    os.replace(tmp, path)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", required=True, help="source workflow id")
    ap.add_argument("--name", required=True, help="new workflow name")
    ap.add_argument("--state", required=True, help="resume file (old -> new ids)")
    ap.add_argument("--trigger-schema", help="JSON file: inputSchema for the new manual trigger")
    ap.add_argument("--describe", action="append", default=[],
                    help='"<node name>=<new description>" (repeatable)')
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    st = json.load(open(a.state, encoding="utf-8")) if os.path.exists(a.state) else {"ids": {}}
    g = clay(["workflows", "graph", "get", a.source, "--mode", "full"])
    nodes = {n["id"]: n for n in g["nodes"]}
    trig_src = g["summary"]["triggers"][0]
    desc = dict(x.split("=", 1) for x in a.describe)

    if not st.get("workflow"):
        st["workflow"] = clay(["workflows", "create", "--name", a.name])["id"]
        save(a.state, st)
    wf = st["workflow"]
    print("workflow", wf, flush=True)

    trig_node = next(n for n in g["nodes"] if n["nodeType"] == "trigger")
    if trig_node["id"] not in st["ids"]:
        src = clay(["workflows", "triggers", "get", trig_src["id"]])
        schema = json.load(open(a.trigger_schema, encoding="utf-8")) if a.trigger_schema else src["inputSchema"]
        res = clay(["workflows", "triggers", "create", wf, "--input",
                    spec_file(a.state, "trigger", {"triggerType": src["triggerType"], "inputSchema": schema})])
        new = clay(["workflows", "triggers", "get", res["resourceId"]])
        st["trigger"], st["ids"][trig_node["id"]] = res["resourceId"], new["workflowNodeId"]
        save(a.state, st)
    print("trigger node", st["ids"][trig_node["id"]], flush=True)

    # Edge order: a node is created once every node it reads from exists.
    todo = [n for n in g["nodes"] if n["nodeType"] != "trigger"]
    while todo:
        progressed = False
        for n in list(todo):
            if n["id"] in st["ids"]:
                todo.remove(n)
                progressed = True
                continue
            refs = {e["sourceNode"] for e in n.get("incomingEdges") or []} | node_refs(n)
            if not refs <= set(st["ids"]):
                continue
            spec = {k: n[k] for k in COPY if n.get(k) not in (None, [], {}) and not (k == "code" and n[k] == "None")}
            if "outputSchema" in spec:
                spec["outputSchema"] = flat_output(spec["outputSchema"])
            if "tools" in spec:
                spec["tools"] = [{k: v for k, v in t.items() if k not in TOOL_DROP} for t in spec["tools"]]
            if n["name"] in desc:
                spec["description"] = desc[n["name"]]
            spec = remap(spec, st["ids"])
            spec["incomingEdges"] = remap([{k: v for k, v in e.items() if k in ("sourceNode", "ruleId")}
                                           for e in n.get("incomingEdges") or []], st["ids"])
            res = clay(["workflows", "nodes", "create", wf, "--input", spec_file(a.state, n["name"], spec)])
            bad = [u for u in res.get("appliedUpdates") or [] if not u.get("success")]
            if bad:
                raise SystemExit(f"{n['name']}: some fields were not applied: {bad}")
            st["ids"][n["id"]] = res["nodeId"]
            st.setdefault("names", {})[n["name"]] = res["nodeId"]
            save(a.state, st)
            print(f"  {n['name']}: {res['nodeId']}", flush=True)
            todo.remove(n)
            progressed = True
        if not progressed:
            raise SystemExit("could not order the remaining nodes: " + ", ".join(n["name"] for n in todo))
    v = clay(["workflows", "graph", "validate", wf], check=False)
    print("validate:", json.dumps(v)[:600])


if __name__ == "__main__":
    main()
