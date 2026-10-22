"""The email workflow + marketing-manager gate (event-icp-clay-workflow skill).

Why these are tests and not conventions: every failure here costs money silently.
- A stored email that is routed to the waterfall is paid for twice (the reason the workflow exists).
- A Supabase outage read as "not in Supabase" pays for everyone ("assume new").
- A feed edited after the manager's approval enriches people they removed.
- A waterfall hit that is not written back is paid for again by the next colleague.
The code-node bodies are executed here exactly as Clay runs them: a `handler(context)` with `get_input`.
"""

import csv
import importlib.util
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, ".claude", "skills", "event-icp-clay-workflow", "scripts")
sys.path.insert(0, SCRIPTS)


def load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(SCRIPTS, f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ew = load("email_workflow")
approval = load("approval")
collect = load("collect")
CFG = {"supabase_url": "https://example.supabase.co", "accounts": {"supabase": "a1", "blitz": "a2", "leadmagic": "a3", "prospeo": "a4"}}


class Ctx:
    def __init__(self, **inputs):
        self.inputs = inputs

    def get_input(self, key):
        return self.inputs.get(key)


def run(code, **inputs):
    ns = {}
    exec(code, ns)                                   # noqa: S102 - the node body, as Clay executes it
    return ns["handler"](Ctx(**inputs))


# ---------------------------------------------------------------- graph
def test_graph_is_consistent_and_every_leaf_returns_the_unified_record():
    assert ew.check(ew.graph(CFG)) == []


def test_check_catches_a_node_reading_something_not_upstream():
    nodes = ew.graph(CFG)
    key, spec, parents = nodes[1]
    spec = json.loads(json.dumps(spec))
    spec["inputSchema"]["properties"]["lookup_body"]["sourceNodeId"] = "<prospeo>"
    nodes[1] = (key, spec, parents)
    assert any("not upstream" in p for p in ew.check(nodes))


def test_only_the_find_route_reaches_a_paid_node():
    nodes = ew.graph(CFG)
    parents = {k: p for k, _, p in nodes}
    paid = {k for k, s, _ in nodes if s["nodeType"] == "tool" and k not in ("sb_lookup",) and not k.startswith("rec_")}

    def reaches_via_find(k):
        for p, rule, _ in parents.get(k, []):
            if p == "route":
                return rule == "find"
            if reaches_via_find(p):
                return True
        return False
    assert paid and all(reaches_via_find(k) for k in paid)            # Blitz, LeadMagic, Prospeo, every ZeroBounce


def test_zerobounce_never_sees_a_supabase_email():
    nodes = {k: (s, p) for k, s, p in ew.graph(CFG)}
    for k, (s, _) in nodes.items():
        if k.startswith("zb_"):
            src = s["inputSchema"]["properties"]["email"]["sourceNodeId"][1:-1]
            assert src in ("read_blitz", "leadmagic", "prospeo")


def test_accounts_land_on_tools_zero_bounce_optional():
    by = {k: s for k, s, _ in ew.graph(CFG)}
    assert by["sb_lookup"]["tools"][0]["appAccountId"] == "a1" and by["rec_miss"]["tools"][0]["appAccountId"] == "a1"
    assert by["blitz"]["tools"][0]["appAccountId"] == "a2"
    assert "appAccountId" not in by["zb_blitz"]["tools"][0]
    assert by["sb_lookup"]["tools"][0]["inputMappingConfig"]["url"]["value"] == "https://example.supabase.co/rest/v1/rpc/lookup_contacts"


# ---------------------------------------------------------------- code nodes
def test_build_person_bodies():
    out = run(ew.BUILD_PERSON, **{"LinkedIn URL": "https://www.linkedin.com/in/jane", "First Name": "Jane", "Last Name": "Doe",
                                  "Domain": "acme.com", "Company Name": "Acme"})
    assert out["full_name"] == "Jane Doe"
    assert json.loads(out["lookup_body"]) == {"people": [{"linkedin_url": "https://www.linkedin.com/in/jane", "first_name": "Jane",
                                                          "last_name": "Doe", "full_name": "Jane Doe", "domain": "acme.com"}]}
    assert json.loads(out["blitz_body"]) == {"person_linkedin_url": "https://www.linkedin.com/in/jane"}


STORED = [{"status": "known, bad email", "email": "x@acme.com", "email_validation_status": "invalid", "mx_provider": "google"}]


@pytest.mark.parametrize("response", [STORED, {"body": STORED}, json.dumps(STORED), {"response": {"body": json.dumps(STORED)}}])
def test_any_stored_email_is_reused_whatever_wrapper_clay_uses(response):
    out = run(ew.READ_SUPABASE, response=response, linkedin_url="https://www.linkedin.com/in/x")
    assert out["route_reuse"] and not out["route_find"]
    assert out["contact_email"] == "x@acme.com" and out["email_validation_status"] == "invalid" and out["mx_provider"] == "google"


@pytest.mark.parametrize("status,email,li,route", [
    ("email reused - risky", "r@a.com", "li", "route_reuse"),
    ("searched before, no email", None, "li", "route_skip"),
    ("known, no email", None, "li", "route_find"),
    ("not in supabase", None, "li", "route_find"),
    ("not in supabase", None, "", "route_no_linkedin"),
])
def test_routes(status, email, li, route):
    out = run(ew.READ_SUPABASE, response=[{"status": status, "email": email}], linkedin_url=li)
    assert [k for k in out if k.startswith("route_") and out[k]] == [route]


@pytest.mark.parametrize("response", [None, {"code": "PGRST106", "message": "Invalid schema"}, {"body": "<html>502</html>"}, []])
def test_a_failed_lookup_never_spends(response):
    out = run(ew.READ_SUPABASE, response=response, linkedin_url="https://www.linkedin.com/in/x")
    assert out["route_failed"] and not out["route_find"]


@pytest.mark.parametrize("response,email", [({"found": True, "email": "a@b.com"}, "a@b.com"), ({"body": {"found": False, "email": None}}, ""),
                                            (json.dumps({"found": True, "email": "c@d.com"}), "c@d.com"), (None, "")])
def test_read_blitz(response, email):
    out = run(ew.READ_BLITZ, response=response)
    assert out["email"] == email and out["found"] is bool(email)


PERSON = {"linkedin_url": "https://www.linkedin.com/in/jane", "first_name": "Jane", "last_name": "Doe", "full_name": "Jane Doe",
          "domain": "acme.com", "company_name": "Acme", "job_title": "CTO", "country": "UK"}


def test_record_body_for_a_hit_carries_the_zerobounce_answer():
    out = run(ew.record_body_code("leadmagic"), **PERSON, email="jane@acme.com", zb_status="valid", zb_sub_status="", zb_smtp_provider="google")
    body = json.loads(out["record_body"])["people"][0]
    assert body["email"] == "jane@acme.com" and body["email_validation_status"] == "valid" and body["mx_provider"] == "google"
    assert body["provider"] == "leadmagic" and body["linkedin_url"] == PERSON["linkedin_url"]
    assert out["email_source"] == "leadmagic" and out["sendable"] is True


def test_record_body_for_a_miss_has_no_email_so_the_api_logs_a_miss():
    out = run(ew.record_body_code(None), **PERSON)
    body = json.loads(out["record_body"])["people"][0]
    assert "email" not in body and out["email_source"] == "none"


def test_terminal_flags_an_unrecorded_hit():
    good = run(ew.terminal_code("recorded"), linkedin_url="li", contact_email="a@b.com", sendable="True", email_source="blitz",
               record_response={"body": {"received": 1}})
    bad = run(ew.terminal_code("recorded"), linkedin_url="li", contact_email="a@b.com", record_response={"message": "timeout"})
    assert set(good) == set(ew.UNIFIED) and good["recorded"] is True and good["sendable"] is True
    assert bad["recorded"] is False and "NOT written back" in bad["note"]


def test_supabase_terminal():
    out = run(ew.terminal_code("supabase"), linkedin_url="li", contact_email="a@b.com", sendable="False", supabase_status="known, bad email")
    assert set(out) == set(ew.UNIFIED) and out["email_source"] == "supabase" and out["recorded"] is False


# ---------------------------------------------------------------- approval gate
def write(path, rows):
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


ROWS = [{"LinkedIn URL": "https://www.linkedin.com/in/a", "Job Title": "Project Manager", "Mailing Country": "India", "Domain": "a.com", "Classification": "X"},
        {"LinkedIn URL": "https://www.linkedin.com/in/b", "Job Title": "PR Intern", "Mailing Country": "United Kingdom", "Domain": "b.com", "Classification": "X"},
        {"LinkedIn URL": "https://uk.linkedin.com/in/C", "Job Title": "CTO", "Mailing Country": "United Kingdom", "Domain": "www.c.com", "Classification": "Y"},
        {"LinkedIn URL": "https://www.linkedin.com/in/d", "Job Title": "Head of Labs", "Mailing Country": "United Kingdom", "Domain": "d.com", "Classification": "X"}]


def test_apply_changes_removals_and_reasons():
    kept, removed = approval.apply_changes(ROWS, {"remove_title_keywords": ["intern", "PR"], "remove_countries": ["india"],
                                                  "remove_linkedin": ["https://www.linkedin.com/in/c"]})
    assert [r["LinkedIn URL"][-1] for r in kept] == ["d"]
    reasons = {r["LinkedIn URL"][-1]: r["Removed Because"] for r in removed}
    assert reasons["a"].startswith("country") and reasons["b"].startswith("title") and reasons["C"] == "person removed by name"
    kept, _ = approval.apply_changes(ROWS, {"remove_title_keywords": ["PR"]})
    assert "a" in [r["LinkedIn URL"][-1] for r in kept]                  # "PR" is a word: it does not match "Project"


def test_a_feed_edited_after_approval_is_refused(tmp_path, monkeypatch):
    people = tmp_path / "people.csv"
    write(people, ROWS)
    out = tmp_path / "people - approved.csv"
    monkeypatch.setattr(sys, "argv", ["approval.py", "apply", "--people", str(people), "--approved-by", "MM", "--out", str(out)])
    approval.main()
    rec = approval.verify(tmp_path / "approval.json", out)
    assert rec["rows_approved"] == 4 and rec["approved_by"] == "MM"
    with open(out, "a", encoding="utf-8") as fh:
        fh.write("https://www.linkedin.com/in/sneaked,x,y,z,w\n")
    with pytest.raises(SystemExit, match="not the approved list"):
        approval.verify(tmp_path / "approval.json", out)


# ---------------------------------------------------------------- collect emails
def test_collect_emails_maps_the_unified_record(tmp_path, monkeypatch):
    people = tmp_path / "approved.csv"
    write(people, ROWS[:3])
    res = tmp_path / "results"
    res.mkdir()
    recs = [{"id": ROWS[0]["LinkedIn URL"], "status": "complete", "result": {"contact_email": "a@a.com", "email_validation_status": "valid",
                                                                            "email_source": "supabase", "recorded": False, "sendable": True}},
            {"id": ROWS[1]["LinkedIn URL"], "status": "complete", "result": {"result": {"contact_email": "", "email_source": "none", "recorded": True}}}]
    (res / "chunk_0000.jsonl").write_text("\n".join(json.dumps(r) for r in recs), encoding="utf-8")
    out = tmp_path / "emails.csv"
    monkeypatch.setattr(sys, "argv", ["collect.py", "emails", "--people", str(people), "--results", str(res), "--out", str(out)])
    collect.main()
    rows = list(csv.DictReader(open(out, encoding="utf-8-sig")))
    assert [r["Email Source"] for r in rows] == ["supabase", "none", "not run"]
    assert rows[0]["Contact Email"] == "a@a.com" and rows[0]["Email Validation Status"] == "valid" and rows[0]["Email Recorded"] == "false"
    assert len(list(csv.DictReader(open(str(out)[:-4] + " - not run.csv", encoding="utf-8-sig")))) == 1
