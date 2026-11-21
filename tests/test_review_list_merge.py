"""review_list.merge_route: attendee / named-contact lists that take the speaker route must never become speakers.

Failure it prevents (Submarine, 19 Nov 2026): every speaker-route row was tagged Source "Speaker", so ~435 people from
attendee lists and named-contact lists would have gone to Salesforce as "Is CA Speaker = True" with no SPEX /
Attendee flag. deliver.py reads `Source` and the company-events column, so the merge must keep both right.
"""
import importlib.util
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, ".claude", "skills", "event-icp-clay-workflow", "scripts")
sys.path.insert(0, SCRIPTS)
spec = importlib.util.spec_from_file_location("review_list", os.path.join(SCRIPTS, "review_list.py"))
review_list = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(review_list)
merge = review_list.merge_route


def test_speaker_into_find_people_keeps_old_tag():
    p = {"Source": "Find People", "Event": "A; B", "Speaker Event(s)": ""}
    merge(p, {"Source": "Speaker", "Speaker Event(s)": "C", "Speaker Event Title": "CTO", "Events": ""}, "Event")
    assert p["Source"] == "Find People + Speaker"
    assert p["Speaker Event(s)"] == "C" and p["Event"] == "A; B" and p["Speaker Event Title"] == "CTO"


def test_attendee_goes_to_company_events_not_speaker_events():
    p = {"Source": "Find People", "Event": "A", "Speaker Event(s)": ""}
    merge(p, {"Source": "Attendee", "Speaker Event(s)": "", "Events": "Capacity Europe"}, "Event")
    assert p["Source"] == "Find People + Attendee"
    assert p["Event"] == "A; Capacity Europe" and p["Speaker Event(s)"] == ""
    assert "Speaker" not in p["Source"]


def test_speaker_and_attendee_files_merge_into_one_row():
    s = {"Source": "Speaker", "Speaker Event(s)": "X", "Events": ""}
    merge(s, {"Source": "Attendee", "Speaker Event(s)": "", "Events": "Capacity Europe"})
    assert s["Source"] == "Speaker + Attendee"
    assert s["Speaker Event(s)"] == "X" and s["Events"] == "Capacity Europe"


def test_no_duplicate_tags_or_events():
    p = {"Source": "Find People + Attendee", "Event": "Capacity Europe", "Speaker Event(s)": ""}
    merge(p, {"Source": "Attendee", "Speaker Event(s)": "", "Events": "Capacity Europe"}, "Event")
    assert p["Source"] == "Find People + Attendee" and p["Event"] == "Capacity Europe"


def _write(path, rows):
    import csv
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def test_tag_only_rows_tag_but_never_add(tmp_path):
    """Stage-0 people ('already found' in the people search) come through as tag-only rows: they must tag the
    person's existing row (Submarine, 20 Nov 2026: 455 already-found speakers went untagged) and must never be
    added as a new, empty row when the person is no longer in the lists."""
    import argparse
    import csv
    people = tmp_path / "people.csv"
    _write(people, [{"Company Name": "Acme", "Domain": "acme.com", "Side": "Buy Side", "Event": "A",
                     "Person Name": "Jo Bloggs", "LinkedIn URL": "https://www.linkedin.com/in/jo-bloggs",
                     "Mailing Country": "United Kingdom"}])
    att = tmp_path / "att.csv"
    _write(att, [{"LinkedIn URL": "https://www.linkedin.com/in/jo-bloggs", "Source": "Attendee",
                  "Events": "Capacity Europe", "Speaker Event(s)": "", "Speaker Event Title": "",
                  "Person Name": "Jo Bloggs", "Domain": "acme.com", "Tag Only": "yes"},
                 {"LinkedIn URL": "https://www.linkedin.com/in/gone-person", "Source": "Attendee",
                  "Events": "Capacity Europe", "Speaker Event(s)": "", "Speaker Event Title": "",
                  "Person Name": "Gone Person", "Domain": "x.com", "Tag Only": "yes"}])
    rules = tmp_path / "rules.yaml"
    rules.write_text("linkedin_only_countries: [Germany]\n", encoding="utf-8")
    out = tmp_path / "rv"
    review_list.cmd_build(argparse.Namespace(company_csv=[], company_results=[], people=str(people),
                                             linkedin_only=None, speakers=[str(att)], rules=str(rules),
                                             drop_cols="", out_prefix=str(out)))
    rows = list(csv.DictReader(open(str(out) + ".csv", encoding="utf-8-sig")))
    assert len(rows) == 1
    assert rows[0]["Source"] == "Find People + Attendee" and rows[0]["Event"] == "A; Capacity Europe"
    assert "Tag Only" not in rows[0]
