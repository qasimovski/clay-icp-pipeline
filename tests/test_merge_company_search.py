"""merge_company_search.py files each company under exactly ONE workbook and drops
companies already worked elsewhere.

Both failures are paid for later, at the people search: a company in two
workbooks is searched for people twice, and a company already worked in
Competitive Events or Other Sources is searched again (user, 2026-10-29 and
2026-11-05: never pay twice; dedupe the buy side against CE and Other Sources).
"""

import csv
import json
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(REPO, "automation", "cleanup", "merge_company_search.py")


def _setup(tmp_path, results, done=True):
    work = tmp_path / "search"
    (work / "results").mkdir(parents=True)
    plan = [{"name": n, "group": g, "priority": "High", "query": "q"} for n, g, _ in results]
    (work / "plan.json").write_text(json.dumps({"searches": plan}), encoding="utf-8")
    state = {n: {"run": {"status": "done" if done else "running"}} for n, _, _ in results}
    (work / "state.json").write_text(json.dumps(state), encoding="utf-8")
    for n, _, rows in results:
        slug = "".join(c if c.isalnum() else "_" for c in n.lower()).strip("_")
        with open(work / "results" / f"{slug}.jsonl", "w", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r) + "\n")
    return work


def _run(work, out, *extra):
    return subprocess.run([sys.executable, SCRIPT, "--work", str(work), "--out", str(out), *extra],
                          capture_output=True, text=True)


def _read(path):
    with open(path, encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def test_company_in_two_searches_lands_in_the_first_group_only(tmp_path):
    engie = {"clay_company_id": 1, "name": "ENGIE", "domain": "https://www.engie.com/"}
    work = _setup(tmp_path, [
        ("IPPs", "2. Developers & IPPs", [engie]),
        ("Utilities", "3. Utilities & energy retailers", [dict(engie, domain="engie.com")]),
    ])
    out = tmp_path / "out.csv"
    assert _run(work, out).returncode == 0
    rows = _read(out)
    assert len(rows) == 1
    assert rows[0]["Workbook"] == "2. Developers & IPPs"
    assert rows[0]["Also Matched"] == "Utilities"
    assert rows[0]["Tier"] == "A"


def test_excluded_domain_and_nameonly_rows_are_dropped_with_a_reason(tmp_path):
    work = _setup(tmp_path, [("IPPs", "2. Developers & IPPs", [
        {"clay_company_id": 1, "name": "Known Co", "domain": "known.com"},
        {"clay_company_id": 2, "name": "No Domain Ltd", "domain": None},
        {"clay_company_id": 3, "name": "New Co", "domain": "new.com"},
    ])])
    ce = tmp_path / "ce.csv"
    ce.write_text("Company Name,Domain\nKnown Co,www.KNOWN.com\nNo Domain Ltd,\n", encoding="utf-8")
    out = tmp_path / "out.csv"
    r = _run(work, out, "--exclude", f"{ce}|Domain|Company Name|In Solar CE")
    assert r.returncode == 0, r.stderr
    assert [x["Domain"] for x in _read(out)] == ["new.com"]
    dropped = _read(tmp_path / "out - dropped.csv")
    assert {d["Company Name"] for d in dropped} == {"Known Co", "No Domain Ltd"}
    assert {d["Reason"] for d in dropped} == {"In Solar CE"}


def test_refuses_to_merge_an_unfinished_run(tmp_path):
    work = _setup(tmp_path, [("IPPs", "2. Developers & IPPs", [])], done=False)
    r = _run(work, tmp_path / "out.csv")
    assert r.returncode != 0 and "not done" in (r.stderr + r.stdout)


def test_tier_from_the_plan_wins_over_the_solar_fallback(tmp_path):
    # EVCharge buy side V2 has 10 groups and no tier column: the plan carries tier "",
    # which must not become the Solar map's "A" for groups 1-4 (2026-11-06).
    work = _setup(tmp_path, [("Fleets", "4. Fleets", [{"clay_company_id": 1, "name": "DHL", "domain": "dhl.com"}])])
    plan_path = work / "plan.json"
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    plan["searches"][0]["tier"] = ""
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    out = tmp_path / "out.csv"
    assert _run(work, out).returncode == 0
    assert _read(out)[0]["Tier"] == ""
