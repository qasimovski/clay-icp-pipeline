"""The Mining-hardened steps of the event-icp-clay-workflow skill (Oct 2026): version precedence,
the sheet's exact people rules, the per-event review split, seniority depth and the config checks.

Why these are tests and not conventions: each failure either pays for people the ICP excludes, or
silently loses people it wants.
- remap_prior: the OLD version's KEEP/DROP leaking into the new run (V2 must always win), a Cut
  event's companies being searched, a Tier 3 note ignored, an unknown searched without a label.
- post_filter: a per-keyword exclusion applied globally (Stand staff WANT Aftersales), a person in
  a LinkedIn-only country reaching the email file, a flagged title dropped instead of kept.
- deliver split --decisions: a person missing from an event their company exhibited at, or a
  LinkedIn-only person losing the tag that keeps them out of the email step.
- icp_mapping cross-check: an unquoted `Yes:` (a boolean) or a comma label splitting in two.
"""

import csv
import importlib.util
import json
import os
import sys
import types

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, ".claude", "skills", "event-icp-clay-workflow", "scripts")
sys.path.insert(0, SCRIPTS)


def load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(SCRIPTS, f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


pf = load("post_filter")
rp = load("remap_prior")
deliver = load("deliver")
collect = load("collect")
icpm = load("icp_mapping")
build_feed = load("build_feed")


def write_csv(path, rows):
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


def read_csv(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


# ---------------------------------------------------------------- post_filter
ICP = {
    "exclude_titles": ["Intern"],
    "sell": {"exclude_titles": ["Presales"], "titles": ["Sales Manager"],
             "categories": {"Heavy Equipment": ["United Arab Emirates", "Germany"]}},
    "buy": {"default_countries": ["Egypt"],
            "categories": {"Mining Operators": {"titles": ["Mine Manager", "Geologist"]},
                           "Investors": {"titles": ["Mining Finance"]}}},
}
RULES = {
    "sell": {"personas": {
        "Booker": {"exact": ["CEO"], "contains": {"Sales Manager": {"exclude": ["Aftersales"]}}},
        "Stand staff": {"exact": ["CEO"], "contains": {"Aftersales": {"exclude": ["Aftersales Assistant"]},
                                                       "Sales Manager": {}}}},
             "geo_tier": {"Core": ["United Arab Emirates"]}},
    "buy": {"exact": ["CEO", "Mine Manager"], "contains_too": {"Mining Operators": ["Mine Manager"]},
            "exclude": ["Partner", "Sales Manager"], "exclude_allowed": {"Partner": ["Investors"]},
            "team_evidence": {"categories": ["Investors"], "words": ["Mining"]}},
    "linkedin_only_countries": ["Germany"],
}
R = pf.Rules(ICP, RULES)


@pytest.mark.parametrize("side,cat,title,country,want_reason,persona", [
    ("Sell Side", "Heavy Equipment", "CEO & Founder", "United Arab Emirates", "", "Booker"),
    ("Sell Side", "Heavy Equipment", "Aftersales Sales Manager", "United Arab Emirates", "", "Stand staff"),
    ("Sell Side", "Heavy Equipment", "Presales Engineer", "United Arab Emirates", "excluded word", ""),
    ("Sell Side", "Heavy Equipment", "Sales Manager", "France", "country not on sell geo", ""),
    ("Sell Side", "Heavy Equipment", "Logistics Coordinator", "United Arab Emirates", "title not on", ""),
    ("Buy Side", "Mining Operators", "Underground Mine Manager", "Egypt", "", ""),
    ("Buy Side", "Mining Operators", "Partner", "Egypt", "excluded word", ""),
    ("Buy Side", "Investors", "Partner, Mining Finance", "Egypt", "", ""),
])
def test_sheet_rules(side, cat, title, country, want_reason, persona):
    reason, f = R.judge(side, cat, title, country)
    assert reason.startswith(want_reason) if want_reason else reason == ""
    assert f["Persona"] == persona


def test_booker_wins_and_flags_are_kept():
    # On both lists -> the first persona listed (Booker).
    assert R.judge("Sell Side", "Heavy Equipment", "Sales Manager", "Germany")[1]["Persona"] == "Booker"
    # A senior exact title plus a qualifier is kept and flagged, not dropped.
    reason, f = R.judge("Sell Side", "Heavy Equipment", "Group CEO Africa", "United Arab Emirates")
    assert reason == "" and f["Title Check"].startswith("near-exact")
    reason, f = R.judge("Buy Side", "Mining Operators", "مدير عام", "Egypt")
    assert reason == "" and f["Title Check"].startswith("non-English")
    # Team evidence is a flag for the approver.
    assert R.judge("Buy Side", "Investors", "Head of Mining Finance", "Egypt")[1]["Team Evidence"] == "yes"


def test_linkedin_only_country_never_reaches_the_email_file(tmp_path):
    people = [{"Side": "Sell Side", "Classification": "Heavy Equipment", "Job Title": "CEO",
               "Mailing Country": c, "LinkedIn URL": f"https://www.linkedin.com/in/{c}"}
              for c in ("Germany", "United Arab Emirates", "France")]
    write_csv(tmp_path / "p.csv", people)
    (tmp_path / "icp.yaml").write_text(json.dumps(ICP), encoding="utf-8")
    (tmp_path / "rules.yaml").write_text(json.dumps(RULES), encoding="utf-8")
    sys.argv = ["post_filter.py", "--people", str(tmp_path / "p.csv"), "--icp", str(tmp_path / "icp.yaml"),
                "--rules", str(tmp_path / "rules.yaml"), "--out-prefix", str(tmp_path / "S")]
    pf.main()
    email = read_csv(tmp_path / "S - email list.csv")
    li = read_csv(tmp_path / "S - LinkedIn only.csv")
    dropped = read_csv(tmp_path / "S - dropped by post filter.csv")
    assert [r["Mailing Country"] for r in email] == ["United Arab Emirates"]
    assert [r["Mailing Country"] for r in li] == ["Germany"]
    assert [r["Mailing Country"] for r in dropped] == ["France"]
    assert email[0]["Geo Tier"] == "Core" and li[0]["Geo Tier"] == "Secondary"


# ---------------------------------------------------------------- remap_prior
SCOPE = {
    "type_map": {"HEAVY": ["Sell Side", "Heavy Equipment"], "MINER": ["Buy Side", "Mining Operators"],
                 "BANK": ["Buy Side", "Investors"]},
    "drop_types": {"INDUSTRIAL": "no new category"},
    "drop_if": [{"column": "Sanctioned HQ", "equals": "Y", "reason": "sanctioned"}],
    "regions": {"MEA": {"tokens": ["egypt", "niger"], "not": ["papua new guinea"]}},
    "tiers": {1: {"keep": "all", "unknown": "classify"}, 3: {"keep": "listed", "unknown": "drop"}},
    "events": {"Tier1 Expo": {"tier": 1},
               "Tight Expo": {"tier": 3, "keep": ["Mining Operators"],
                              "region": {"region": "MEA", "labels": ["Mining Operators"], "fields": ["Country"]}}},
}
S = rp.Scope(SCOPE)


@pytest.mark.parametrize("event,row,ptype,want", [
    ("Tier1 Expo", {}, "HEAVY", ("KEEP", "Heavy Equipment")),
    ("Cut Expo", {}, "HEAVY", ("DROP", "")),                              # not in scope = Cut
    ("Tier1 Expo", {"Sanctioned HQ": "Y"}, "HEAVY", ("DROP", "")),
    ("Tier1 Expo", {}, "INDUSTRIAL", ("DROP", "")),
    ("Tier1 Expo", {}, "SOMETHING_ELSE", ("DROP", "")),                   # out of ICP in the new version
    ("Tier1 Expo", {}, "UNKNOWN", ("CLASSIFY", "")),
    ("Tight Expo", {}, "UNKNOWN", ("DROP", "")),                          # unsure at tier 3 -> drop
    ("Tight Expo", {}, "HEAVY", ("DROP", "Heavy Equipment")),             # the note keeps miners only
    ("Tight Expo", {"Country": "Egypt"}, "MINER", ("KEEP", "Mining Operators")),
    ("Tight Expo", {"Country": "Papua New Guinea"}, "MINER", ("DROP", "Mining Operators")),
])
def test_scope_decisions(event, row, ptype, want):
    d, _, label, _ = S.decide(event, row, ptype)
    assert (d, label) == want


def test_new_version_wins_over_the_old_decision(tmp_path):
    """The old KEEP/DROP is never read; verdicts re-check labelled rows and label unknown ones."""
    prior = [
        {"Event": "Tier1 Expo", "Entity": "Exhibitor", "Company Name": "Old Keep Corp", "Website": "oldkeep.com",
         "Country": "Egypt", "Prior Type": "INDUSTRIAL", "Sanctioned HQ": "", "V1": "KEEP"},
        {"Event": "Tier1 Expo", "Entity": "Exhibitor", "Company Name": "Old Drop Corp", "Website": "olddrop.com",
         "Country": "Egypt", "Prior Type": "HEAVY", "Sanctioned HQ": "", "V1": "DROP"},
        {"Event": "Tier1 Expo", "Entity": "Sponsor", "Company Name": "Bank", "Website": "bank.com",
         "Country": "Egypt", "Prior Type": "BANK", "Sanctioned HQ": "", "V1": "KEEP"},
        {"Event": "Tier1 Expo", "Entity": "Exhibitor", "Company Name": "Mystery", "Website": "mystery.com",
         "Country": "Egypt", "Prior Type": "UNKNOWN", "Sanctioned HQ": "", "V1": "DROP"},
        {"Event": "Tier1 Expo", "Entity": "Exhibitor", "Company Name": "Hosted", "Website": "x.wixsite.com",
         "Country": "Egypt", "Prior Type": "HEAVY", "Sanctioned HQ": "", "V1": "KEEP"},
    ]
    write_csv(tmp_path / "prior.csv", prior)
    write_csv(tmp_path / "v.csv", [
        {"domain": "bank.com", "verdict": "OUT", "side": "", "classification": "", "reason": "NO_MINING_LINK",
         "confidence": "H", "note": "retail bank"},
        {"domain": "mystery.com", "verdict": "CLASSIFIED", "side": "Sell Side", "classification": "Heavy Equipment",
         "reason": "", "confidence": "M", "note": "crushers"}])
    (tmp_path / "scope.yaml").write_text(json.dumps(SCOPE), encoding="utf-8")
    out = tmp_path / "out"
    sys.argv = ["remap_prior.py", "--scope", str(tmp_path / "scope.yaml"), "--prior", str(tmp_path / "prior.csv"),
                "--out", str(out), "--prior-decision-col", "V1", "--verdicts", str(tmp_path / "v.csv")]
    rp.main()
    dec = {r["Company Name"]: r for r in read_csv(out / "decisions.csv")}
    feed = {r["Domain"]: r for r in read_csv(out / "feed_companies.csv")}
    assert dec["Old Keep Corp"]["Decision"] == "DROP"            # old KEEP ignored
    assert dec["Old Drop Corp"]["Decision"] == "KEEP"            # old DROP put back
    assert dec["Bank"]["Decision"] == "DROP" and "bank.com" not in feed
    assert dec["Mystery"]["Decision"] == "KEEP" and feed["mystery.com"]["Classification"] == "Heavy Equipment"
    assert dec["Hosted"]["Domain"] == "" and "x.wixsite.com" not in feed   # never search a hosting platform
    assert set(feed) == {"olddrop.com", "mystery.com"}
    assert read_csv(out / "classify_queue.csv") == []                # the unknown was labelled, not queued


def test_scope_refuses_a_keep_label_no_type_produces():
    bad = json.loads(json.dumps(SCOPE))
    bad["events"]["Tight Expo"]["keep"] = ["Mining Operator"]          # typo
    with pytest.raises(SystemExit):
        rp.Scope(bad)


def test_per_event_entities_and_row_rules():
    """Solar, 26 Oct 2026: "sponsors only" notes and co-located floors (London Build rows) are config."""
    cfg = json.loads(json.dumps(SCOPE))
    cfg["extra_labels"] = ["Fire Safety"]                              # a label only the classifier gives
    cfg["events"]["Sponsor Summit"] = {"tier": 1, "entities": ["Sponsor"], "keep": ["Fire Safety"]}
    cfg["events"]["Shared Floor"] = {"tier": 1, "drop_if": [
        {"column": "Show", "equals": "London Build", "reason": "co-located show"},
        {"column": "PV tag", "not_in": ["yes", "partly"], "reason": "not PV"}]}
    s = rp.Scope(cfg)
    assert s.decide("Sponsor Summit", {"Entity": "Exhibitor"}, "HEAVY")[0] == "DROP"
    assert s.decide("Sponsor Summit", {"Entity": "Sponsor"}, "HEAVY")[0] == "DROP"   # label not kept
    assert s.judge_label("Sponsor Summit", {"Entity": "Sponsor"}, "Sell Side", "Fire Safety")[0] == "KEEP"
    assert s.decide("Shared Floor", {"Show": "London Build", "PV tag": "yes"}, "HEAVY")[0] == "DROP"
    assert s.decide("Shared Floor", {"Show": "elemental", "PV tag": ""}, "HEAVY")[0] == "DROP"
    assert s.decide("Shared Floor", {"Show": "elemental", "PV tag": "partly"}, "HEAVY")[0] == "KEEP"
    cfg["events"]["Shared Floor"]["drop_if"][0].pop("equals")          # a rule with no operator
    with pytest.raises(SystemExit):
        rp.Scope(cfg)


# ---------------------------------------------------------------- deliver split --decisions
def test_people_are_placed_in_every_event_and_keep_their_channel(tmp_path):
    write_csv(tmp_path / "dec.csv", [
        {"Event": "A Expo", "Entity": "Exhibitor", "Company Name": "Acme", "Website": "", "Domain": "acme.com",
         "Country": "", "Side": "Sell Side", "Classification": "Heavy Equipment", "Decision": "KEEP", "Reason": "r"},
        {"Event": "B Expo", "Entity": "Sponsor", "Company Name": "Acme", "Website": "", "Domain": "acme.com",
         "Country": "", "Side": "Sell Side", "Classification": "Heavy Equipment", "Decision": "KEEP", "Reason": "r"},
        {"Event": "B Expo", "Entity": "Exhibitor", "Company Name": "Gone", "Website": "", "Domain": "gone.com",
         "Country": "", "Side": "", "Classification": "", "Decision": "DROP", "Reason": "out"}])
    person = {"Domain": "acme.com", "Side": "Sell Side", "Source": "Find People", "Job Title": "CEO"}
    write_csv(tmp_path / "p.csv", [dict(person, **{"LinkedIn URL": "u1"}), dict(person, Source="Speaker",
                                                                              **{"LinkedIn URL": "u3"})])
    write_csv(tmp_path / "li.csv", [dict(person, **{"LinkedIn URL": "u2"})])
    a = types.SimpleNamespace(decisions=str(tmp_path / "dec.csv"), people=[str(tmp_path / "p.csv")],
                              linkedin_only=[str(tmp_path / "li.csv")], channel="Email + LinkedIn",
                              linkedin_only_label="LinkedIn + phone only (Germany)", exclude_source=["Speaker"],
                              entity_col="Exhibitor/Sponsor", single_workbook=None, out=str(tmp_path / "t"),
                              plan=str(tmp_path / "plan.json"))
    deliver.cmd_split(a)
    plan = {(e["event"], e["table"]): e for e in json.load(open(tmp_path / "plan.json", encoding="utf-8"))}
    assert set(plan) == {("A Expo", "Exhibitors"), ("A Expo", "Exhibitors Sellside People"),
                         ("B Expo", "Exhibitors"), ("B Expo", "Sponsors"), ("B Expo", "Sponsors Sellside People")}
    for t in (("A Expo", "Exhibitors Sellside People"), ("B Expo", "Sponsors Sellside People")):
        rows = read_csv(plan[t]["csv"])
        assert {r["LinkedIn URL"]: r["Outreach Channel"] for r in rows} == {
            "u1": "Email + LinkedIn", "u2": "LinkedIn + phone only (Germany)"}     # speaker u3 left out
    companies = read_csv(plan[("B Expo", "Exhibitors")]["csv"])
    assert companies[0]["Decision"] == "DROP" and companies[0]["People Found"] == "0"


def test_speakers_never_reach_the_clay_tables_without_any_flag(tmp_path):
    """Speakers go to the Salesforce CSV only (user, 29 Oct 2026): split drops a speaker-only row even when
    --exclude-source is not given; a person the search found who also spoke stays in their people table."""
    write_csv(tmp_path / "dec.csv", [
        {"Event": "A Expo", "Entity": "Exhibitor", "Company Name": "Acme", "Website": "", "Domain": "acme.com",
         "Country": "", "Side": "Sell Side", "Classification": "Heavy Equipment", "Decision": "KEEP", "Reason": "r"}])
    person = {"Domain": "acme.com", "Side": "Sell Side", "Job Title": "CEO"}
    write_csv(tmp_path / "p.csv", [dict(person, Source="Find People", **{"LinkedIn URL": "u1"}),
                                   dict(person, Source="Speaker", **{"LinkedIn URL": "u2"}),
                                   dict(person, Source="Find People + Speaker", **{"LinkedIn URL": "u3"})])
    a = types.SimpleNamespace(decisions=str(tmp_path / "dec.csv"), people=[str(tmp_path / "p.csv")],
                              linkedin_only=[], channel="Email + LinkedIn", linkedin_only_label="LinkedIn only",
                              exclude_source=[], entity_col="Exhibitor/Sponsor", single_workbook=None,
                              out=str(tmp_path / "t"), plan=str(tmp_path / "plan.json"))
    deliver.cmd_split(a)
    plan = json.load(open(tmp_path / "plan.json", encoding="utf-8"))
    assert not any("speaker" in e["table"].lower() for e in plan)
    people = next(e for e in plan if e["kind"] == "people")
    assert {r["LinkedIn URL"] for r in read_csv(people["csv"])} == {"u1", "u3"}


# ---------------------------------------------------------------- seniority depth
def test_capped_groups_and_merge(tmp_path):
    feed = tmp_path / "feed.csv"
    write_csv(feed, [{"Company Name": "A", "Domain": "a.com", "Seniority Group": ""},
                     {"Company Name": "B", "Domain": "b.com", "Seniority Group": ""}])
    res = tmp_path / "res"
    res.mkdir()
    full = [{"url": f"u{i}"} for i in range(10)]
    (res / "chunk_000.jsonl").write_text(
        json.dumps({"id": "a.com", "status": "complete", "result": {"people": full}}) + "\n"
        + json.dumps({"id": "b.com", "status": "complete", "result": {"people": full[:3]}}) + "\n", encoding="utf-8")
    collect.cmd_capped(types.SimpleNamespace(feed=str(feed), results=str(res), out_prefix=str(tmp_path / "pf"),
                                             groups="B1,B2", icp=None))
    assert [r["Domain"] for r in read_csv(tmp_path / "pf - sen B2.csv")] == ["a.com"]
    d1, d2 = tmp_path / "s1", tmp_path / "s2"
    d1.mkdir()
    d2.mkdir()
    (d1 / "c.jsonl").write_text(json.dumps({"id": "a.com", "status": "complete", "result": {"people": full[:5]}}) + "\n",
                                encoding="utf-8")
    (d2 / "c.jsonl").write_text(json.dumps({"id": "a.com", "status": "complete", "result": {"people": full[3:]}}) + "\n",
                                encoding="utf-8")
    collect.cmd_merge_splits(types.SimpleNamespace(dirs=[str(d1), str(d2)], out=str(tmp_path / "m")))
    merged = collect.load_results(str(tmp_path / "m"))
    assert len(collect.people_of(merged["a.com"])[0]) == 10          # union, deduped on the profile URL


# ---------------------------------------------------------------- config checks and domains
def test_cross_check_catches_yaml_traps():
    import yaml
    icp, problems = icpm.build(json.loads(json.dumps(ICP)))
    assert not problems
    rules = yaml.safe_load("sell:\n  geo_tier:\n    Yes: [Germany]\nbuy:\n  contains_too:\n"
                           "    Processing, Refining & Smelting: []\n  team_evidence:\n"
                           "    categories: [Processing, Refining & Smelting]\n")
    found = icpm.cross_check(icp, rules, {"type_map": {"X": ["Buy Side", "Nope"]}})
    assert any("boolean" in p for p in found)
    assert any("'Refining & Smelting'" in p for p in found)
    assert any("type_map X" in p for p in found)


@pytest.mark.parametrize("raw,want", [
    ("https:// westermo.com/", "westermo.com"),
    ("http:en.jinyuankj.com.cn", "en.jinyuankj.com.cn"),
    ("https://.carletonengineering.co.za/", "carletonengineering.co.za"),
    ("tapflo.kz  | www.tapflo.com", ""),
])
def test_scraped_website_quirks(raw, want):
    assert build_feed.norm_domain(raw) == want


def test_marketplace_storefront_is_flagged():
    assert build_feed.flag("hdsieve.en.alibaba.com").startswith("hosted")


# ---------------------------------------------------------------- one profile, one paid lookup
def test_one_linkedin_profile_is_never_enriched_twice(tmp_path):
    """Clay writes ".../in/x/" and Blitz ".../in/x" (Solar, Oct 2026: 285 profiles had both). The email run
    dedupes on the URL text, so two spellings meant two paid lookups. review_list writes one canonical
    form, and bulk_run refuses a LinkedIn-keyed feed that still spells one profile two ways."""
    rl, br = load("review_list"), load("bulk_run")
    for u in ("https://www.linkedin.com/in/jane-doe/", "http://uk.linkedin.com/in/jane-doe?trk=x",
              "https://www.linkedin.com/in/jane-doe#experience/", "https://www.linkedin.com/in/jane-doe"):
        assert rl.canonical_linkedin(u) == "https://www.linkedin.com/in/jane-doe"
    rows = [{"LinkedIn URL": "https://www.linkedin.com/in/jane-doe/"},
            {"LinkedIn URL": "https://www.linkedin.com/in/jane-doe"}]
    with pytest.raises(SystemExit, match="paid for twice"):
        br.check_linkedin_spellings(rows, "LinkedIn URL")
    br.check_linkedin_spellings(rows[:1] * 2, "LinkedIn URL")      # same text twice: bulk_run sends it once
    br.check_linkedin_spellings(rows, "Row ID")                      # not keyed on the URL: not this check's job


# ---------------------------------------------------------------- reuse earlier runs' answers
def _run_dir(root, name, workflow, sent, results, age_days=0):
    d = root / name
    (d / "feeds").mkdir(parents=True)
    (d / "results").mkdir()
    (d / "state.json").write_text(json.dumps({"_workflow": workflow} if workflow else {}), encoding="utf-8")
    (d / "feeds" / "chunk_000.jsonl").write_text(
        "".join(json.dumps({"id": i, "inputs": x}) + "\n" for i, x in sent.items()), encoding="utf-8")
    p = d / "results" / "chunk_000.jsonl"
    p.write_text("".join(json.dumps(r) + "\n" for r in results), encoding="utf-8")
    t = os.path.getmtime(p) - age_days * 86400
    os.utime(p, (t, t))
    return str(d)


def test_an_answer_already_paid_for_is_reused_not_bought_again(tmp_path):
    """User, 29 Oct 2026: never enrich a company twice for the same event or ICP; reuse window 90 days; across
    ICPs only company data. Same workflow + same inputs + complete + fresh -> reused; anything else is sent."""
    br = load("bulk_run")
    co = {"Company Domain": "acme.com"}
    ppl_solar = {"Domain": "acme.com", "Side": "Sell Side", "Classification": "Inverters"}
    ppl_mining = {"Domain": "acme.com", "Side": "Sell Side", "Classification": "Heavy Equipment"}
    ok = {"id": "acme.com", "status": "complete", "result": {"name": "Acme"}}
    fresh = _run_dir(tmp_path, "company_mining", "wf_co", {"acme.com": co}, [ok])
    old = _run_dir(tmp_path, "company_2025", "wf_co", {"acme.com": co}, [ok], age_days=120)
    failed = _run_dir(tmp_path, "company_failed", "wf_co", {"acme.com": co},
                      [ok, {"id": "acme.com", "status": "failed"}])
    people_mining = _run_dir(tmp_path, "people_mining", "wf_ppl", {"acme.com": ppl_mining}, [ok])
    recs_co = [{"id": "acme.com", "inputs": co}]
    assert set(br.reusable([fresh], recs_co, "wf_co", 90)) == {"acme.com"}       # company data: reused
    assert br.reusable([old], recs_co, "wf_co", 90) == {}                          # older than 90 days
    assert br.reusable([failed], recs_co, "wf_co", 90) == {}                       # last answer failed
    assert br.reusable([fresh], recs_co, "wf_other", 90) == {}                     # another workflow
    # people: a Mining search has other inputs (category), so Solar searches again
    assert br.reusable([people_mining], [{"id": "acme.com", "inputs": ppl_solar}], "wf_ppl", 90) == {}
    assert set(br.reusable([people_mining], [{"id": "acme.com", "inputs": ppl_mining}], "wf_ppl", 90)) == {"acme.com"}
