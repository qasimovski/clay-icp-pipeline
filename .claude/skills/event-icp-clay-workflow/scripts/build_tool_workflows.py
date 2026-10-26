"""Build (or repair) the small one-action Clay workflows the people / speaker steps use.

    enrich-person    Manual (LinkedIn URL) -> Enrich Person (cpj-enrich-person)
                     name, title, current company (+ its domain), location. Measured 1 action, 0 data credits.
    enrich-company   Manual (Company Domain) -> Enrich Company (cpj-enrich-company-v2, by DOMAIN)
                     size, type, industry, founded, revenue, LinkedIn, website + derived_datapoints
                     (Sub - Industry, Primary Offerings, Business Stage), which ONLY a domain lookup
                     returns. Measured 1 action, 0 data credits; ~1 company in 4 has no derived data.
    linkedin-finder  Manual (speaker fields) -> Build Speaker Record -> LinkedIn Finder (agent, web search)
                     for speakers the strict TinyFish match could not resolve. PAID: Mining measured
                     1 action + 0.1 data credit per speaker with gpt-6-luna.

    python build_tool_workflows.py enrich-person   --name "<Series> - Enrich Person"            --ids wf_person.json
    python build_tool_workflows.py enrich-company  --name "<Series> - Enrich Company by Domain"  --ids wf_company.json
    python build_tool_workflows.py linkedin-finder --name "<Series> - Speakers - LinkedIn Finder" --ids wf_finder.json \
        --prompt finder_prompt.md --model <agentModel id>
    python build_tool_workflows.py linkedin-finder --ids wf_finder.json --prompt finder_prompt.md --update

Re-running repairs: nodes that exist are updated, not duplicated. The ids file (workflow, trigger and
node ids) is the state. Then: draft-test one row and READ `dataCreditsUsed` before quoting a price
(the catalog says "1 credit" for actions that measured 0), publish, `clay routines create workflow
<wf> --name "<name>"`, and bulk-run with bulk_run.py:
    enrich-person   --inputs "LinkedIn URL"   --id-cols "Row ID"
    enrich-company  --inputs "Company Domain" --id-cols "Company Domain"
    linkedin-finder --inputs "Speaker ID,Speaker Name,Job Title,Company,Company Website,Country,Events,Side,Classification" --id-cols "Row ID"
The finder prompt template is assets/speaker_linkedin_finder_prompt.md: fill its <ANGLE BRACKETS>
for the event and get the user's yes on the prompt before building.
"""

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _clay import clay, to_cli_path  # noqa: E402

PACKAGE = "e251a70e-46d7-4f3a-b3ef-a211ad3d8bd2"
TOOLS = {
    "enrich-person": dict(trigger_field="LinkedIn URL", snake="linkedin_url", node="Enrich Person",
                          trigger_doc="The person's LinkedIn profile URL", action="cpj-enrich-person",
                          mapping={"person_identifier": {"type": "reference", "expression": "{{linkedin_url}}"}}),
    "enrich-company": dict(trigger_field="Company Domain", snake="company_domain", node="Enrich Company",
                           trigger_doc="The company's domain (example.com)", action="cpj-enrich-company-v2",
                           mapping={"company_identifier_type": {"type": "static", "value": "company_domain"},
                                    "company_identifier": {"type": "reference", "expression": "{{company_domain}}"}}),
}
FINDER_FIELDS = (("Speaker ID", "speaker_id", "Our id for the speaker (name + company key)"),
                 ("Speaker Name", "speaker_name", "Full name as listed by the event"),
                 ("Job Title", "job_title", "Title as listed by the event"),
                 ("Company", "company", "Company as listed by the event"),
                 ("Company Website", "company_website", "Company domain, if we have one"),
                 ("Country", "country", "Country as listed by the event"),
                 ("Events", "events", "Events the speaker spoke at"),
                 ("Side", "side", "Buy Side / Sell Side of their company"),
                 ("Classification", "classification", "ICP category of their company"))
FINDER_OUTPUT = {
    "status": {"type": "string", "description": "found / ambiguous / not_found"},
    "linkedin_url": {"type": "string", "description": "https://www.linkedin.com/in/<slug>, or empty unless found"},
    "profile_name": {"type": "string", "description": "Name as shown on the profile"},
    "current_title": {"type": "string", "description": "Current title on the profile"},
    "current_company": {"type": "string", "description": "Current employer on the profile"},
    "location": {"type": "string", "description": "Profile location (city, country) or unknown"},
    "match_basis": {"type": "string", "description": "Evidence for the name and person match"},
    "confidence": {"type": "string", "description": "High / Medium / none"},
    "reasoning": {"type": "string", "description": "1-3 sentences"},
}


class Builder:
    def __init__(self, ids_path):
        self.ids_path = ids_path
        self.nodes_dir = os.path.join(os.path.dirname(os.path.abspath(ids_path)), "nodes")
        self.ids = json.load(open(ids_path, encoding="utf-8")) if os.path.exists(ids_path) else {}

    def run(self, args, payload=None, name="payload"):
        if payload is not None:
            os.makedirs(self.nodes_dir, exist_ok=True)
            fp = os.path.join(self.nodes_dir, f"{name}.json")
            json.dump(payload, open(fp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            args = args + ["--input", to_cli_path(fp)]
        return clay(args)

    def save(self):
        json.dump(self.ids, open(self.ids_path, "w", encoding="utf-8"), indent=1)

    def workflow(self, name, fields):
        """Create the workflow + manual trigger once; collect existing node ids by name."""
        if not self.ids.get("workflow"):
            if not name:
                raise SystemExit("--name is required to create the workflow")
            self.ids["workflow"] = self.run(["workflows", "create", "--name", name])["id"]
            self.save()
        wf = self.ids["workflow"]
        g = self.run(["workflows", "graph", "get", wf])
        if not (g.get("summary") or {}).get("triggers"):
            self.run(["workflows", "triggers", "create", wf], {
                "triggerType": "manual", "createTriggerNode": True,
                "inputSchema": {"type": "object", "required": [], "properties": {
                    label: {"type": "string", "description": doc} for label, doc in fields}}}, "trigger")
            g = self.run(["workflows", "graph", "get", wf])
        existing = {}
        for n in (g.get("summary") or {}).get("nodes") or g.get("nodes") or []:
            if n.get("nodeType") == "trigger":
                self.ids["trigger"] = n["id"]
            elif n.get("name"):
                existing[n["name"]] = n["id"]
        return wf, existing

    def upsert(self, wf, existing, key, spec):
        if spec["name"] in existing:
            nid = existing[spec["name"]]
            r = self.run(["workflows", "nodes", "update", wf, nid],
                         {k: v for k, v in spec.items() if k != "nodeType"}, f"u_{key}")
        else:
            r = self.run(["workflows", "nodes", "create", wf], spec, f"c_{key}")
            nid = r["nodeId"]
        bad = [(u.get("field"), u.get("message")) for u in r.get("appliedUpdates", []) if not u.get("success")]
        print(f"  {spec['name']:24} {nid}  {'applied: all ok' if not bad else 'NOT APPLIED: ' + str(bad)}")
        self.ids[key] = nid
        return nid

    def edge(self, wf, nid, source, key):
        self.run(["workflows", "nodes", "update", wf, nid], {"incomingEdges": [{"sourceNode": source}]}, f"e_{key}")

    def finish(self, wf):
        self.save()
        v = self.run(["workflows", "graph", "validate", wf])
        print(f"workflow {wf} valid: {v.get('valid')} errors: {v.get('errors')}  (ids -> {self.ids_path})")


def build_tool(a):
    c = TOOLS[a.kind]
    B = Builder(a.ids)
    wf, existing = B.workflow(a.name, [(c["trigger_field"], c["trigger_doc"])])
    nid = B.upsert(wf, existing, "tool", {
        "nodeType": "tool", "name": c["node"], "description": f"{c['action']} from {c['trigger_field']}",
        "tools": [{"toolType": "clay_action", "actionKey": c["action"], "actionPackageId": PACKAGE,
                   "description": c["node"], "inputMappingConfig": c["mapping"]}],
        "inputSchema": {"type": "object", "properties": {
            c["snake"]: {"type": "string", "sourceNodeId": B.ids["trigger"],
                         "sourcePath": f"$['{c['trigger_field']}']"}}}})
    B.edge(wf, nid, B.ids["trigger"], "tool")
    B.finish(wf)


def finder_prompt(path):
    text = open(path, encoding="utf-8").read()
    left = re.findall(r"<[A-Z][A-Z ,/&-]{3,}>", text)
    if left:
        raise SystemExit(f"the prompt still has template placeholders {sorted(set(left))[:5]} - fill them first")
    known = {snake for _, snake, _ in FINDER_FIELDS}
    unknown = set(re.findall(r"\{\{(\w+)\}\}", text)) - known
    if unknown:
        raise SystemExit(f"the prompt uses inputs that don't exist: {sorted(unknown)} (known: {sorted(known)})")
    if not re.search(r"\{\{speaker_name\}\}", text):
        raise SystemExit("the prompt never interpolates {{speaker_name}}: the agent would search blind")
    return text


def build_finder(a):
    prompt = finder_prompt(a.prompt)
    B = Builder(a.ids)
    if a.update:
        r = B.run(["workflows", "nodes", "update", B.ids["workflow"], B.ids["agent"]], {"agentPrompt": prompt}, "u_prompt")
        print("prompt updated:", [u.get("field") for u in r.get("appliedUpdates", []) if u.get("success")])
        return
    if not a.model:
        raise SystemExit("--model is required (the agentModel id the user picked; Mining used gpt-6-luna)")
    wf, existing = B.workflow(a.name, [(label, doc) for label, _, doc in FINDER_FIELDS])
    code = ("def handler(context):\n"
            "    # Blank inputs become an explicit 'not provided', so the prompt never shows an empty field.\n"
            "    def s(key):\n"
            "        v = context.get_input(key)\n"
            "        v = '' if v is None else str(v).strip()\n"
            "        return v or 'not provided'\n\n"
            "    return {" + ", ".join(f'"{snake}": s("{label}")' for label, snake, _ in FINDER_FIELDS) + "}\n")
    build = B.upsert(wf, existing, "build", {
        "nodeType": "code", "name": "Build Speaker Record", "description": "Shape the speaker row.", "code": code,
        "inputSchema": {"type": "object", "properties": {
            label: {"type": "string", "sourceNodeId": B.ids["trigger"], "sourcePath": f"$['{label}']"}
            for label, _, _ in FINDER_FIELDS}},
        "outputSchema": {snake: {"type": "string", "description": d} for _, snake, d in FINDER_FIELDS}})
    agent = B.upsert(wf, existing, "agent", {
        "nodeType": "agent", "name": "LinkedIn Finder",
        "description": "Finds the speaker's personal LinkedIn profile; verifies name + employer before answering.",
        "agentName": a.agent_name, "agentModel": a.model, "agentInternetSearchEnabled": True,
        "agentPrompt": prompt,
        "inputSchema": {"type": "object", "properties": {
            snake: {"type": "string", "sourceNodeId": build, "sourcePath": f"$.{snake}"}
            for _, snake, _ in FINDER_FIELDS}},
        "outputSchema": FINDER_OUTPUT})
    B.edge(wf, build, B.ids["trigger"], "build")
    B.edge(wf, agent, build, "agent")
    node = B.run(["workflows", "nodes", "get", wf, agent])
    node = node.get("node") or node
    print("agentModel:", node.get("agentModel"), "| web search:", node.get("agentInternetSearchEnabled"))
    B.finish(wf)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("kind", choices=["enrich-person", "enrich-company", "linkedin-finder"])
    ap.add_argument("--ids", required=True, help="JSON state file for this workflow's ids")
    ap.add_argument("--name", help="workflow name (needed the first time)")
    ap.add_argument("--prompt", help="linkedin-finder: the filled prompt file")
    ap.add_argument("--model", help="linkedin-finder: agentModel id")
    ap.add_argument("--agent-name", default="Speaker LinkedIn Finder")
    ap.add_argument("--update", action="store_true", help="linkedin-finder: push a prompt change only")
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if a.kind == "linkedin-finder":
        if not a.prompt:
            raise SystemExit("--prompt is required")
        build_finder(a)
    else:
        build_tool(a)


if __name__ == "__main__":
    main()
