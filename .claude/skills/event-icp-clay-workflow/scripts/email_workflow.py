"""The email workflow (workflow 3): Supabase first, then the Blitz -> LeadMagic -> Prospeo waterfall, ZeroBounce on
waterfall hits only, and every paid answer written back to Supabase. Runs ONLY on the marketing-approved people list.

    Trigger (one approved person)
      -> Build Person (code)                     JSON bodies for the HTTP calls
      -> Supabase Lookup (HTTP, team API)        api.lookup_contacts
      -> Read Supabase (code) -> Route?
           stored email (valid, risky OR bad) -> Out - From Supabase        0 credits, no waterfall, no ZeroBounce
           searched before, no email          -> Out - Searched Before      never paid twice
           lookup failed                      -> Out - Lookup Failed        nothing spent, re-run later
           no LinkedIn URL                    -> Out - No LinkedIn          not searched
           not in Supabase                    -> Blitz (HTTP) -> Read Blitz -> Found?
               found -> ZeroBounce -> Record Body -> Record (HTTP api.record_contacts) -> Out
               none  -> LeadMagic -> Found? -> (same tail) | none -> Prospeo -> Found? -> (same tail)
                                                                   | none -> Record miss -> Out - No Email Found

No branch ever merges back, so each tail is generated once per provider from one definition.

    python email_workflow.py spec   --config email_workflow.yaml --out email_spec.json     # offline: nodes + edges
    python email_workflow.py check  --config email_workflow.yaml                           # offline: graph integrity
    python email_workflow.py accounts --workflow wf_...       # account ids already set on that workflow's tool nodes
    python email_workflow.py build  --config email_workflow.yaml --workflow wf_... --state email_build_state.json

`build` needs an EMPTY workflow you created (`clay workflows create --name "<Series> - Emails"`). It is resumable:
node ids are saved as they are created, so a re-run never creates a node twice. After it: graph validate, ONE draft
test on 2-3 approved rows (`runs test`, read every node's output), publish, `routines create workflow`.

The config (gitignored, next to the run - see assets/email_workflow.example.yaml) holds the Supabase project URL and
the Clay account ids. Account ids are not secrets; the keys themselves live only in the Clay accounts.
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

HTTP = ("4299091f-3cd3-4d68-b198-0143575f471d", "http-api-v2")
LEADMAGIC = ("edb58209-a62d-42be-992a-e41b87eeacc2", "leadmagic-find-work-email")
PROSPEO = ("48a31bbb-63e6-4461-8a62-d88bb2cd6b0f", "prospeo-find-work-email-v2")
ZEROBOUNCE = ("8f0d2dc0-a6b4-4b84-9aad-a330b4a4586a", "validate-email")
BLITZ_EMAIL_URL = "https://api.blitz-api.ai/v2/enrichment/email"

# people.csv columns sent as trigger inputs (collect.py PERSON_COLS + the company columns)
TRIGGER_INPUTS = ["LinkedIn URL", "First Name", "Last Name", "Person Name", "Company Name", "Domain", "Job Title", "Mailing Country"]
# every terminal node returns exactly these, so collect.py reads one shape whatever path a person took
UNIFIED = {
    "linkedin_url": "LinkedIn URL as sent", "contact_email": "Email (from Supabase or the waterfall)",
    "email_validation_status": "ZeroBounce / stored status", "email_sub_status": "ZeroBounce / stored sub-status",
    "mx_provider": "MX / SMTP provider", "validated_at": "When the email was checked",
    "sendable": "true only for valid", "email_source": "supabase | blitz | leadmagic | prospeo | none",
    "supabase_status": "lookup_contacts status", "recorded": "written back to Supabase", "note": "why this path",
}

# ---------------------------------------------------------------- code node bodies (Clay runs Python handlers)
_HELPERS = '''
import json

def s(context, key):
    v = context.get_input(key)
    return "" if v is None else str(v).strip()

def first_list(obj, depth=0):
    """The JSON array inside an HTTP API node output, whatever wrapper Clay puts around the body."""
    if depth > 4 or obj is None:
        return None
    if isinstance(obj, str):
        try:
            return first_list(json.loads(obj), depth + 1)
        except ValueError:
            return None
    if isinstance(obj, list):
        return obj
    if isinstance(obj, dict):
        for k in ("body", "response", "data", "result", "results", "value"):
            if k in obj:
                got = first_list(obj[k], depth + 1)
                if got is not None:
                    return got
    return None

def first_dict(obj, depth=0):
    if depth > 4 or obj is None:
        return {}
    if isinstance(obj, str):
        try:
            return first_dict(json.loads(obj), depth + 1)
        except ValueError:
            return {}
    if isinstance(obj, dict):
        if "found" in obj or "email" in obj or "received" in obj:
            return obj
        for k in ("body", "response", "data", "result", "value"):
            if k in obj:
                got = first_dict(obj[k], depth + 1)
                if got:
                    return got
        return obj
    return {}
'''

BUILD_PERSON = _HELPERS + '''
def handler(context):
    li = s(context, "LinkedIn URL")
    first, last = s(context, "First Name"), s(context, "Last Name")
    full = s(context, "Person Name") or (first + " " + last).strip()
    person = {"linkedin_url": li, "first_name": first, "last_name": last, "full_name": full,
              "domain": s(context, "Domain"), "company_name": s(context, "Company Name"),
              "job_title": s(context, "Job Title"), "country": s(context, "Mailing Country")}
    return {**person,
            "lookup_body": json.dumps({"people": [{k: person[k] for k in ("linkedin_url", "first_name", "last_name", "full_name", "domain")}]}),
            "blitz_body": json.dumps({"person_linkedin_url": li})}
'''

READ_SUPABASE = _HELPERS + '''
def handler(context):
    rows = first_list(context.get_input("response"))
    li = s(context, "linkedin_url")
    base = {"route_reuse": False, "route_skip": False, "route_failed": False, "route_no_linkedin": False, "route_find": False,
            "supabase_status": "", "contact_email": "", "email_validation_status": "", "email_sub_status": "",
            "mx_provider": "", "validated_at": "", "sendable": False, "note": ""}
    if not rows or not isinstance(rows[0], dict) or "status" not in rows[0]:
        # Unreachable / unexpected reply: spend nothing. The person is re-run later, never "assumed new".
        return {**base, "route_failed": True, "note": "supabase lookup failed: " + json.dumps(context.get_input("response"))[:300]}
    r = rows[0]
    st = r.get("status") or ""
    out = {**base, "supabase_status": st, "contact_email": r.get("email") or "",
           "email_validation_status": r.get("email_validation_status") or "", "email_sub_status": r.get("email_sub_status") or "",
           "mx_provider": r.get("mx_provider") or "", "validated_at": r.get("validated_at") or "", "sendable": bool(r.get("sendable"))}
    if r.get("email"):                                   # valid, risky or bad: reuse as stored, no waterfall, no ZeroBounce
        return {**out, "route_reuse": True, "note": "email from supabase"}
    if st == "searched before, no email":
        return {**out, "route_skip": True, "note": "a paid search already found nothing"}
    if not li:
        return {**out, "route_no_linkedin": True, "note": "no LinkedIn URL - not searched"}
    return {**out, "route_find": True, "note": "not in supabase - waterfall"}
'''

READ_BLITZ = _HELPERS + '''
def handler(context):
    d = first_dict(context.get_input("response"))
    email = (d.get("email") or "").strip() if d.get("found", bool(d.get("email"))) else ""
    return {"email": email, "found": bool(email)}
'''


def record_body_code(provider):
    """Builds the record_contacts body for one waterfall hit (provider) or a miss (provider None)."""
    hit = provider is not None
    return _HELPERS + f'''
def handler(context):
    import datetime
    email = s(context, "email") if {hit!r} else ""
    status, sub, mx = (s(context, "zb_status"), s(context, "zb_sub_status"), s(context, "zb_smtp_provider")) if {hit!r} else ("", "", "")
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    person = {{k: s(context, k) for k in ("linkedin_url", "first_name", "last_name", "full_name", "domain", "company_name", "job_title", "country")}}
    rec = {{**person, "provider": {provider or "waterfall"!r}}}
    if email:
        rec.update({{"email": email, "email_validation_status": status, "email_sub_status": sub, "mx_provider": mx, "validated_at": now}})
    return {{"record_body": json.dumps({{"people": [rec]}}), "linkedin_url": person["linkedin_url"], "contact_email": email,
             "email_validation_status": status, "email_sub_status": sub, "mx_provider": mx,
             "validated_at": now if email else "", "sendable": status == "valid",
             "email_source": {provider or "none"!r}, "supabase_status": "not in supabase"}}
'''


def terminal_code(kind):
    """Final node of a path: returns the UNIFIED shape (the bulk result is the last node's output only)."""
    if kind == "recorded":
        return _HELPERS + '''
def handler(context):
    d = first_dict(context.get_input("record_response"))
    ok = isinstance(d, dict) and "received" in d
    fields = ("linkedin_url", "contact_email", "email_validation_status", "email_sub_status", "mx_provider", "validated_at", "email_source", "supabase_status")
    out = {k: s(context, k) for k in fields}
    out["sendable"] = s(context, "sendable").lower() == "true"
    out["recorded"] = ok
    out["note"] = "written back to supabase" if ok else "NOT written back - re-record: " + json.dumps(context.get_input("record_response"))[:200]
    return out
'''
    return _HELPERS + f'''
def handler(context):
    fields = ("linkedin_url", "contact_email", "email_validation_status", "email_sub_status", "mx_provider", "validated_at", "supabase_status", "note")
    out = {{k: s(context, k) for k in fields}}
    out["sendable"] = s(context, "sendable").lower() == "true"
    out["email_source"] = {"supabase" if kind == "supabase" else "none"!r}
    out["recorded"] = False
    return out
'''


# ---------------------------------------------------------------- the graph
def _ref(node, path):
    return {"type": "string", "sourceNodeId": f"<{node}>", "sourcePath": path}


def _schema(**props):
    return {"type": "object", "properties": props}


def _out(fields):
    return {k: {"type": "boolean" if k in ("sendable", "recorded") or k.startswith("route_") or k == "found" else "string", "description": v}
            for k, v in fields.items()}


def _http(name, desc, url, body_var, body_node, account):
    tool = {"toolType": "clay_action", "actionPackageId": HTTP[0], "actionKey": HTTP[1], "description": "HTTP API",
            "inputMappingConfig": {"url": {"type": "static", "value": url}, "method": {"type": "static", "value": "POST"},
                                   "body": {"type": "reference", "expression": "{{" + body_var + "}}"},
                                   "headers": {"type": "static", "value": {"Content-Type": "application/json"}},
                                   "shouldRetry": {"type": "static", "value": True},
                                   "responseTimeout": {"type": "static", "value": 20000}}}
    if account:
        tool["appAccountId"] = account
    return {"nodeType": "tool", "name": name, "description": desc, "tools": [tool],
            "inputSchema": _schema(**{body_var: _ref(body_node, f"$.{body_var}")})}


def _cond(name, desc, var, src, path, yes, no):
    """Two-rule conditional on a boolean (True/False operators: the only ones the existing workflows prove)."""
    return {"nodeType": "conditional", "name": name, "description": desc, "conditionalMode": "rules",
            "inputSchema": _schema(**{var: {"type": "boolean", "sourceNodeId": f"<{src}>", "sourcePath": path}}),
            "rulesConditionalConfig": {"rules": [
                {"id": yes[0], "name": yes[1], "condition": {"type": "GroupOp", "combinationMode": "And",
                                                             "items": [{"type": "BinOp", "dataPath": [var], "operator": "True"}]}},
                {"id": no[0], "name": no[1], "condition": {"type": "GroupOp", "combinationMode": "And",
                                                           "items": [{"type": "BinOp", "dataPath": [var], "operator": "False"}]}}]}}


def _not_empty_cond(name, desc, src, path, yes, no):
    node = _cond(name, desc, "email", src, path, yes, no)
    node["inputSchema"]["properties"]["email"]["type"] = "string"
    rules = node["rulesConditionalConfig"]["rules"]
    rules[0]["condition"]["items"][0]["operator"] = "NotEmpty"
    rules[1]["condition"]["items"][0]["operator"] = "Empty"
    return node


PERSON_FIELDS = ("linkedin_url", "first_name", "last_name", "full_name", "domain", "company_name", "job_title", "country")


def graph(cfg):
    """[(key, spec, [(parent_key, rule_id|None, rule_name|None)])] in creation order."""
    acc = cfg.get("accounts") or {}
    base = cfg["supabase_url"].rstrip("/") + "/rest/v1/rpc/"
    nodes = []

    def add(key, spec, *parents):
        nodes.append((key, spec, list(parents)))

    add("build", {"nodeType": "code", "name": "Build Person", "description": "Shapes one approved person; JSON bodies for the HTTP calls.",
                  "code": BUILD_PERSON,
                  "inputSchema": _schema(**{c: _ref("trigger", f"$['{c}']") for c in TRIGGER_INPUTS}),
                  "outputSchema": _out({**{f: f for f in PERSON_FIELDS}, "lookup_body": "lookup_contacts body", "blitz_body": "Blitz body"})},
        ("trigger", None, None))
    add("sb_lookup", _http("Supabase Lookup", "api.lookup_contacts with the team key (Supabase GTM API account): reuse anything stored.",
                           base + "lookup_contacts", "lookup_body", "build", acc.get("supabase")), ("build", None, None))
    add("read_sb", {"nodeType": "code", "name": "Read Supabase", "description": "Status + stored email fields; picks the route.",
                    "code": READ_SUPABASE,
                    "inputSchema": _schema(response={"type": "object", "sourceNodeId": "<sb_lookup>", "sourcePath": "$"},
                                           linkedin_url=_ref("build", "$.linkedin_url")),
                    "outputSchema": _out({"route_reuse": "", "route_skip": "", "route_failed": "", "route_no_linkedin": "", "route_find": "",
                                          **{k: v for k, v in UNIFIED.items() if k not in ("linkedin_url", "email_source", "recorded")}})},
        ("sb_lookup", None, None))
    routes = [("reuse", "Stored email - reuse, 0 credits"), ("skip", "Searched before - never pay twice"),
              ("failed", "Lookup failed - spend nothing"), ("no_linkedin", "No LinkedIn - not searched"), ("find", "Not in Supabase - waterfall")]
    add("route", {"nodeType": "conditional", "name": "Route?", "description": "Supabase decides; only 'find' can spend.", "conditionalMode": "rules",
                  "inputSchema": _schema(**{f"route_{r}": {"type": "boolean", "sourceNodeId": "<read_sb>", "sourcePath": f"$.route_{r}"} for r, _ in routes}),
                  "rulesConditionalConfig": {"rules": [
                      {"id": r, "name": label, "condition": {"type": "GroupOp", "combinationMode": "And",
                                                             "items": [{"type": "BinOp", "dataPath": [f"route_{r}"], "operator": "True"}]}}
                      for r, label in routes]}},
        ("read_sb", None, None))
    stored = {k: _ref("read_sb", f"$.{k}") for k in ("contact_email", "email_validation_status", "email_sub_status", "mx_provider",
                                                    "validated_at", "sendable", "supabase_status", "note")}
    for r, label, kind, name in (("reuse", routes[0][1], "supabase", "Out - From Supabase"), ("skip", routes[1][1], "none", "Out - Searched Before"),
                                 ("failed", routes[2][1], "none", "Out - Lookup Failed"), ("no_linkedin", routes[3][1], "none", "Out - No LinkedIn")):
        add(f"out_{r}", {"nodeType": "code", "name": name, "description": "Terminal - nothing spent.", "code": terminal_code(kind),
                         "inputSchema": _schema(linkedin_url=_ref("build", "$.linkedin_url"), **stored), "outputSchema": _out(UNIFIED)},
            ("route", r, label))

    # waterfall: Blitz (needs the LinkedIn URL) -> LeadMagic -> Prospeo
    blitz = _http("Blitz - Find Work Email", "BlitzAPI /v2/enrichment/email (Blitz account holds x-api-key). A miss uses no record.",
                  BLITZ_EMAIL_URL, "blitz_body", "build", acc.get("blitz"))
    add("blitz", blitz, ("route", "find", routes[4][1]))
    add("read_blitz", {"nodeType": "code", "name": "Read Blitz", "description": "found + email from the Blitz reply.", "code": READ_BLITZ,
                       "inputSchema": _schema(response={"type": "object", "sourceNodeId": "<blitz>", "sourcePath": "$"}),
                       "outputSchema": _out({"email": "work email", "found": "Blitz found a verified email"})},
        ("blitz", None, None))
    add("blitz_found", _cond("Blitz Found?", "", "found", "read_blitz", "$.found", ("found", "Blitz hit - validate"), ("none", "No Blitz email - LeadMagic")),
        ("read_blitz", None, None))
    lm_tool = {"toolType": "clay_action", "actionPackageId": LEADMAGIC[0], "actionKey": LEADMAGIC[1], "description": "LeadMagic find work email",
               "inputMappingConfig": {"name": {"type": "reference", "expression": "{{full_name}}"},
                                      "domain": {"type": "reference", "expression": "{{domain}}"}}}
    if acc.get("leadmagic"):
        lm_tool["appAccountId"] = acc["leadmagic"]
    add("leadmagic", {"nodeType": "tool", "name": "LeadMagic - Find Work Email", "description": "Own LeadMagic key (connected account).",
                      "tools": [lm_tool], "inputSchema": _schema(full_name=_ref("build", "$.full_name"), domain=_ref("build", "$.domain"))},
        ("blitz_found", "none", "No Blitz email - LeadMagic"))
    add("lm_found", _not_empty_cond("LeadMagic Found?", "", "leadmagic", "$.email", ("found", "LeadMagic hit - validate"),
                                    ("none", "No LeadMagic email - Prospeo")), ("leadmagic", None, None))
    pr_tool = {"toolType": "clay_action", "actionPackageId": PROSPEO[0], "actionKey": PROSPEO[1], "description": "Prospeo find work email",
               "inputMappingConfig": {"full_name": {"type": "reference", "expression": "{{full_name}}"},
                                      "company_domain": {"type": "reference", "expression": "{{domain}}"}}}
    if acc.get("prospeo"):
        pr_tool["appAccountId"] = acc["prospeo"]
    add("prospeo", {"nodeType": "tool", "name": "Prospeo - Find Work Email", "description": "Own Prospeo key (connected account).",
                    "tools": [pr_tool], "inputSchema": _schema(full_name=_ref("build", "$.full_name"), domain=_ref("build", "$.domain"))},
        ("lm_found", "none", "No LeadMagic email - Prospeo"))
    add("pr_found", _not_empty_cond("Prospeo Found?", "", "prospeo", "$.email", ("found", "Prospeo hit - validate"),
                                    ("none", "No email found - record the miss")), ("prospeo", None, None))

    person_refs = {f: _ref("build", f"$.{f}") for f in PERSON_FIELDS}
    for prov, src, src_path, cond, rule in (("blitz", "read_blitz", "$.email", "blitz_found", "Blitz hit - validate"),
                                            ("leadmagic", "leadmagic", "$.email", "lm_found", "LeadMagic hit - validate"),
                                            ("prospeo", "prospeo", "$.email", "pr_found", "Prospeo hit - validate")):
        zb_tool = {"toolType": "clay_action", "actionPackageId": ZEROBOUNCE[0], "actionKey": ZEROBOUNCE[1], "description": "ZeroBounce verify email",
                   "inputMappingConfig": {"email": {"type": "reference", "expression": "{{email}}"}}}
        if acc.get("zerobounce"):
            zb_tool["appAccountId"] = acc["zerobounce"]
        add(f"zb_{prov}", {"nodeType": "tool", "name": f"ZeroBounce ({prov})", "description": "Validates the waterfall email only.",
                           "tools": [zb_tool], "inputSchema": _schema(email=_ref(src, src_path))}, (cond, "found", rule))
        add(f"body_{prov}", {"nodeType": "code", "name": f"Record Body ({prov})", "description": "record_contacts body: email + ZeroBounce answer.",
                             "code": record_body_code(prov),
                             "inputSchema": _schema(**person_refs, email=_ref(src, src_path), zb_status=_ref(f"zb_{prov}", "$.status"),
                                                    zb_sub_status=_ref(f"zb_{prov}", "$.sub_status"),
                                                    zb_smtp_provider=_ref(f"zb_{prov}", "$.smtp_provider")),
                             "outputSchema": _out({"record_body": "", **{k: v for k, v in UNIFIED.items() if k not in ("recorded", "note")}})},
            (f"zb_{prov}", None, None))
        add(f"rec_{prov}", _http(f"Record ({prov})", "api.record_contacts: the team never pays for this person again.",
                                 base + "record_contacts", "record_body", f"body_{prov}", acc.get("supabase")), (f"body_{prov}", None, None))
        add(f"out_{prov}", _terminal_recorded(prov, f"body_{prov}", f"rec_{prov}"), (f"rec_{prov}", None, None))
    add("body_miss", {"nodeType": "code", "name": "Record Body (miss)", "description": "record_contacts body without an email = searched, none found.",
                      "code": record_body_code(None), "inputSchema": _schema(**person_refs),
                      "outputSchema": _out({"record_body": "", **{k: v for k, v in UNIFIED.items() if k not in ("recorded", "note")}})},
        ("pr_found", "none", "No email found - record the miss"))
    add("rec_miss", _http("Record (miss)", "api.record_contacts: searched with no result, so nobody pays for it again.",
                          base + "record_contacts", "record_body", "body_miss", acc.get("supabase")), ("body_miss", None, None))
    add("out_miss", _terminal_recorded("miss", "body_miss", "rec_miss"), ("rec_miss", None, None))
    return nodes


def _terminal_recorded(prov, body, rec):
    fields = ("linkedin_url", "contact_email", "email_validation_status", "email_sub_status", "mx_provider", "validated_at",
              "sendable", "email_source", "supabase_status")
    return {"nodeType": "code", "name": f"Out - {'No Email Found' if prov == 'miss' else 'Found by ' + prov}",
            "description": "Terminal: the unified email record.", "code": terminal_code("recorded"),
            "inputSchema": _schema(**{f: _ref(body, f"$.{f}") for f in fields},
                                   record_response={"type": "object", "sourceNodeId": f"<{rec}>", "sourcePath": "$"}),
            "outputSchema": _out(UNIFIED)}


def check(nodes):
    """Offline integrity: unique keys, parents exist and come first, every input reads an ancestor, every rule wired once,
    every leaf returns the unified shape. Returns a list of problems (empty = ok)."""
    problems, keys, ancestors = [], {"trigger"}, {"trigger": set()}
    for key, spec, parents in nodes:
        if key in keys:
            problems.append(f"{key}: duplicate key")
        for p, rule, _ in parents:
            if p not in keys:
                problems.append(f"{key}: parent {p} not created before it")
        anc = set()
        for p, _, _ in parents:
            anc |= {p} | ancestors.get(p, set())
        for name, prop in (spec.get("inputSchema") or {}).get("properties", {}).items():
            src = (prop.get("sourceNodeId") or "")[1:-1]
            if src and src not in anc:
                problems.append(f"{key}.{name}: reads {src}, which is not upstream")
        keys.add(key)
        ancestors[key] = anc
    by_key = {k: s for k, s, _ in nodes}
    children = {}
    for key, _, parents in nodes:
        for p, rule, _ in parents:
            children.setdefault(p, []).append((key, rule))
    for key, spec in by_key.items():
        if spec["nodeType"] == "conditional":
            rule_ids = {r["id"] for r in spec["rulesConditionalConfig"]["rules"]}
            wired = [r for _, r in children.get(key, [])]
            if sorted(wired) != sorted(rule_ids):
                problems.append(f"{key}: rules {sorted(rule_ids)} wired as {sorted(wired)}")
        elif key not in children and set(spec.get("outputSchema") or {}) != set(UNIFIED):
            problems.append(f"{key}: leaf does not return the unified shape")
    return problems


# ---------------------------------------------------------------- Clay
def load_cfg(path):
    import yaml

    cfg = yaml.safe_load(open(path, encoding="utf-8")) or {}
    if not str(cfg.get("supabase_url", "")).startswith("https://"):
        raise SystemExit(f"{path}: supabase_url must be https://<project-ref>.supabase.co")
    return cfg


def _subst(obj, ids):
    if isinstance(obj, dict):
        return {k: _subst(v, ids) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_subst(v, ids) for v in obj]
    if isinstance(obj, str) and obj.startswith("<") and obj.endswith(">") and obj[1:-1] in ids:
        return ids[obj[1:-1]]
    return obj


def cmd_build(a):
    from _clay import clay

    cfg = load_cfg(a.config)
    nodes = graph(cfg)
    problems = check(nodes)
    if problems:
        raise SystemExit("graph problems:\n  " + "\n  ".join(problems))
    missing = [k for k in ("supabase", "blitz", "leadmagic", "prospeo") if not (cfg.get("accounts") or {}).get(k)]
    if missing:
        raise SystemExit(f"accounts missing in {a.config}: {missing} (see `email_workflow.py accounts`)")
    state = json.load(open(a.state, encoding="utf-8")) if os.path.exists(a.state) else {}
    if state.get("workflow") not in (None, a.workflow):
        raise SystemExit(f"{a.state} belongs to {state['workflow']}, not {a.workflow}")
    state["workflow"] = a.workflow
    ids = state.setdefault("ids", {})
    if "trigger" not in ids:
        g = clay(["workflows", "graph", "get", a.workflow])["summary"]
        trig = [n for n in g["nodes"] if n["nodeType"] == "trigger"]
        if not trig:
            clay(["workflows", "triggers", "create", a.workflow, "--input", json.dumps({
                "triggerType": "manual", "inputSchema": {"type": "object", "required": [],
                                                         "properties": {c: {"type": "string", "description": c} for c in TRIGGER_INPUTS}}})])
            g = clay(["workflows", "graph", "get", a.workflow])["summary"]
            trig = [n for n in g["nodes"] if n["nodeType"] == "trigger"]
        if len(g["nodes"]) > len(trig):
            raise SystemExit(f"{a.workflow} already has nodes besides the trigger - build into an EMPTY workflow")
        ids["trigger"] = trig[0]["id"]
        json.dump(state, open(a.state, "w", encoding="utf-8"), indent=1)
    for key, spec, _ in nodes:                                       # 1. create (no edges yet)
        if key in ids:
            continue
        out = clay(["workflows", "nodes", "create", a.workflow, "--input", json.dumps(_subst(spec, ids))])
        ids[key] = out["nodeId"]
        json.dump(state, open(a.state, "w", encoding="utf-8"), indent=1)
        print(f"created {spec['name']}: {ids[key]}", flush=True)
    for key, _, parents in nodes:                                    # 2. edges (full replacement, idempotent)
        edges = []
        for p, rule, rname in parents:
            e = {"sourceNode": ids[p]}
            if rule:
                e.update({"ruleId": rule, "ruleName": rname})
            edges.append(e)
        clay(["workflows", "nodes", "update", a.workflow, ids[key], "--input", json.dumps({"incomingEdges": edges})])
    print(json.dumps(clay(["workflows", "graph", "validate", a.workflow], check=False), indent=1)[:2000])
    print(f"{len(nodes)} nodes. Next: draft test on 2-3 approved rows, read every node's output, then publish.")


def cmd_accounts(a):
    from _clay import clay

    g = clay(["workflows", "graph", "get", a.workflow, "--mode", "full"])
    for n in g.get("nodes") or []:
        for t in n.get("tools") or []:
            if t.get("appAccountId") or t.get("appAccountName"):
                print(f"{n.get('name')!r:40} {t.get('actionKey'):32} account={t.get('appAccountName')!r} id={t.get('appAccountId')}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("spec")
    s.add_argument("--config", required=True)
    s.add_argument("--out", required=True)
    c = sub.add_parser("check")
    c.add_argument("--config", required=True)
    b = sub.add_parser("build")
    b.add_argument("--config", required=True)
    b.add_argument("--workflow", required=True)
    b.add_argument("--state", default="email_build_state.json")
    n = sub.add_parser("accounts")
    n.add_argument("--workflow", required=True)
    a = ap.parse_args()
    if a.cmd in ("spec", "check"):
        nodes = graph(load_cfg(a.config))
        problems = check(nodes)
        if a.cmd == "spec":
            json.dump([{"key": k, "parents": p, "spec": s_} for k, s_, p in nodes], open(a.out, "w", encoding="utf-8"), indent=1)
            print(f"{len(nodes)} nodes -> {a.out}")
        print("graph ok" if not problems else "PROBLEMS:\n  " + "\n  ".join(problems))
        sys.exit(1 if problems else 0)
    {"build": cmd_build, "accounts": cmd_accounts}[a.cmd](a)


if __name__ == "__main__":
    main()
