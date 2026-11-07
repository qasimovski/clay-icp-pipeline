"""The EVCharge Products & Services query config renders queries that Clay will
run without complaint.

Every failure guarded here is SILENT in Clay: an unknown `industry` value or a
4th `products_and_services` value does not raise in the search panel — Clay
drops it and returns a narrower result set, so the workbook looks built and is
quietly wrong. The pass materialises those results into tables, so "quietly
narrower" means paying to import the wrong companies.
"""

import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "automation", "cleanup"))

import ps_query_config as psq  # noqa: E402


@pytest.fixture
def cfg():
    """Fresh per test — the negative cases mutate it, and a shared copy would
    let one test's injected error satisfy the next test's `match=`."""
    return psq.load("evcharge")


def test_shipped_config_is_valid(cfg):
    assert psq.validate(cfg) is True


def test_every_category_renders_a_select_from_companies(cfg):
    cats = psq.categories("evcharge")
    assert len(cats) == 15, "the sheet's 16th Top level is 'GH remove' — 15 build"
    for name, query in cats:
        assert query.startswith("select from companies\n"), name
        # Never a limit clause: the row cap belongs in the panel, and a stray
        # `limit` in the query silently truncates every category.
        assert "\nlimit " not in query and not query.endswith("limit"), name


def test_countries_are_the_sheets_22(cfg):
    assert len(cfg["countries"]) == 22
    assert len(set(cfg["countries"])) == 22
    # The sheet's typos are normalised to Clay's enum, not carried through.
    for typo in ("Portugla", "Saudi", "UK", "UAE"):
        assert typo not in cfg["countries"]
    for fixed in ("Portugal", "Saudi Arabia", "United Kingdom",
                  "United Arab Emirates"):
        assert fixed in cfg["countries"]


def test_every_query_carries_all_22_countries(cfg):
    for name, query in psq.categories("evcharge"):
        for country in cfg["countries"]:
            assert f'"{country}"' in query, f"{name} lost {country}"


def test_unknown_industry_value_is_rejected(cfg):
    cfg["categories"][0]["industry"].append("Electric Vehicle Charging")
    with pytest.raises(psq.ConfigError, match="not a Clay enum value"):
        psq.validate(cfg)


def test_unknown_country_is_rejected(cfg):
    cfg["countries"].append("Britain")
    with pytest.raises(psq.ConfigError, match="not a Clay country_name value"):
        psq.validate(cfg)


def test_a_fourth_products_value_is_rejected(cfg):
    cat = cfg["categories"][0]
    cat["products_and_services"] = ["a", "b", "c", "d"]
    with pytest.raises(psq.ConfigError, match="caps it at 3"):
        psq.validate(cfg)


def test_case_only_duplicate_keywords_are_rejected(cfg):
    cfg["categories"][0]["description"].append(
        cfg["categories"][0]["description"][0].upper())
    with pytest.raises(psq.ConfigError, match="repeats"):
        psq.validate(cfg)


def test_each_horizontal_category_gets_exactly_one_anchor(cfg):
    """Every horizontal category is anchored, by one list or the other — an
    unanchored one readmits every VC / contractor / consultancy on earth."""
    by_setting = {}
    for c in cfg["categories"]:
        by_setting.setdefault(c.get("ev_anchor", False), set()).add(c["name"])
    assert by_setting[True] == {
        "Installation, Construction & Maintenance Service",
        "Payments, Access & Commercial Services",
        "Professional & Advisory Services",
        "Finance, Investment & Funding",
    }
    assert by_setting["adjacent"] == {
        "Traffic, Road Safety & Enforcement Systems",
        "Civil Engineering & Construction Technology",
        "Infrastructure Asset Management & Operations",
    }
    # Match the anchor CLAUSE, not the line shape: a category with no products
    # clause renders its own keywords as `  and description contains (...)` too,
    # so counting those lines cannot tell an anchor from a sub-level list.
    queries = dict(psq.categories("evcharge"))
    for cat in cfg["categories"]:
        expected = psq.anchor_list(cfg, cat)
        query = queries[cat["name"]]
        clause = ('  and description contains ('
                  + ", ".join(f'"{v}"' for v in expected) + ")")
        if expected:
            assert query.endswith(clause), cat["name"]
        else:
            assert not query.endswith(
                '"electrification")'), f"{cat['name']} is unexpectedly anchored"


def test_the_adjacent_anchor_is_a_superset_of_the_strict_one(cfg):
    """It exists to widen, never to swap: a category moved onto it must not
    lose any EV term it already matched."""
    assert set(cfg["ev_anchor"]) < set(cfg["adjacent_anchor"])


def test_an_unknown_anchor_setting_is_rejected(cfg):
    cfg["categories"][0]["ev_anchor"] = "wide"
    with pytest.raises(psq.ConfigError, match="must be true, false or"):
        psq.validate(cfg)


def test_a_category_may_carry_no_products_clause(cfg):
    """Charging Software drops it deliberately — the renderer must emit a plain
    `and description contains (...)`, not a dangling `or` or empty list."""
    q = psq.query_for("Charging Software, Platforms & Data")
    assert "products_and_services" not in q
    assert "  and description contains (" in q
    assert "()" not in q


def test_description_and_products_are_ored_not_anded(cfg):
    """ANDing them would require a company to match BOTH a literal keyword and
    the semantic field — a near-empty intersection, and the opposite of the
    recall the OR is there for (docs/CLAY_QUERY_SKILL.md)."""
    for name, query in psq.categories("evcharge"):
        if "products_and_services" not in query:
            continue
        assert "or products_and_services is_similar_to" in query, name
        assert "and products_and_services is_similar_to" not in query, name


# --------------------------------------------------------------------------
# Optional clauses (Solar buy side, 2026-11-05): naics, company_size,
# people_titles + person_countries. Each is silent in Clay when wrong.
# --------------------------------------------------------------------------

@pytest.fixture
def solar():
    return psq.load("solar_buyside")


def test_solar_buyside_config_is_valid(solar):
    assert psq.validate(solar) is True
    plan = psq.search_plan("solar_buyside")
    # 26 sheet sub-segments; 4 of them also run a 1,000+ staff companion search (user, 2026-11-06)
    assert len({p["subsegment"] for p in plan}) == 26
    assert len(plan) == 30
    # 8 buyer groups = 8 workbooks (user, 2026-11-05)
    assert len({p["group"] for p in plan}) == 8


def test_naics_is_ored_with_industry_not_anded(solar):
    cat = next(c for c in solar["categories"] if c.get("naics"))
    q = psq.render(solar, cat)
    assert "and (industry in (" in q
    assert 'or naics_codes_2022 in ("31-33"))' in q


def test_unquoted_or_malformed_naics_is_rejected(solar):
    cat = next(c for c in solar["categories"] if c.get("naics"))
    cat["naics"] = ["31-34"]
    with pytest.raises(psq.ConfigError, match="naics"):
        psq.validate(solar)


def test_people_exists_carries_whole_persona_and_person_countries(solar):
    cat = next(c for c in solar["categories"] if c.get("people_titles"))
    q = psq.render(solar, cat)
    line = [l for l in q.splitlines() if "people.exists(" in l]
    assert len(line) == 1, "one people.exists with the whole title list, not one per title"
    assert "is_current = true" in line[0]
    for t in cat["people_titles"]:
        assert f'"{t}"' in line[0]
    assert "person.location_country in (" in line[0]


def test_person_countries_use_the_people_enum_not_the_company_enum(solar):
    # Companies spell it "Ivory Coast"; people spell it "Côte d'Ivoire".
    solar["person_countries"] = ["Ivory Coast"]
    with pytest.raises(psq.ConfigError, match="location_country"):
        psq.validate(solar)


def test_categories_without_optional_clauses_render_unchanged(cfg):
    for name, q in psq.categories("evcharge"):
        assert "people.exists" not in q and "naics_codes_2022" not in q, name


def test_description_not_renders_as_one_negated_clause(solar):
    cat = next(c for c in solar["categories"] if c.get("description_not"))
    q = psq.render(solar, cat)
    assert q.count("and not description contains (") == 1


def test_a_keyword_both_required_and_excluded_is_rejected(solar):
    cat = next(c for c in solar["categories"] if c.get("description_not"))
    cat["description_not"] = list(cat["description_not"]) + [cat["description"][0].upper()]
    with pytest.raises(psq.ConfigError, match="both required and excluded"):
        psq.validate(solar)


# --------------------------------------------------------------------------
# EVCharge buy side V2 (2026-11-06): persona companions + tier passthrough.
# --------------------------------------------------------------------------

def test_evcharge_buyside_v2_config_is_valid():
    cfg = psq.load("evcharge_buyside_v2")
    assert psq.validate(cfg) is True
    plan = psq.search_plan("evcharge_buyside_v2")
    # 10 buyer groups = 10 workbooks; 35 sheet sub-segments (BuySide ICP v2 tab)
    assert len({p["group"] for p in plan}) == 10
    assert len({p["subsegment"] for p in plan}) == 35
    # The sheet has no tier column: Tier blank, not the Solar fallback map.
    assert all(p["tier"] == "" for p in plan)


def test_every_persona_search_gets_a_big_company_companion_without_the_persona():
    cfg = psq.load("evcharge_buyside_v2")
    by_name = {c["name"]: c for c in cfg["categories"]}
    names = [c["name"] for c in cfg["categories"]]
    personas = [c for c in cfg["categories"] if c.get("people_titles")]
    opted_out = [c for c in personas if c.get("persona_companion") is False]
    assert personas and opted_out
    for base in opted_out:
        # e.g. government: a persona-free 1,000+ search would add every big ministry back
        assert f"{base['name']} (1,000+ staff)" not in by_name
    for base in [c for c in personas if c not in opted_out]:
        twin_name = f"{base['name']} (1,000+ staff)"
        assert twin_name in by_name, twin_name
        # straight after its base, so the base keeps first claim in the merge
        assert names.index(twin_name) == names.index(base["name"]) + 1
        twin = by_name[twin_name]
        q = psq.render(cfg, twin)
        assert "people.exists" not in q
        assert 'company_size in ("1,001-5,000", "5,001-10,000", "10,001+")' in q
        assert twin["subsegment"] == base["name"]
        # same industry/description as the base
        assert twin["description"] == base["description"]


def test_no_companions_without_the_setting(solar):
    # Solar's companions are written by hand; the expansion must not add more.
    assert len(psq.search_plan("solar_buyside")) == 30
    assert all("tier" not in p for p in psq.search_plan("solar_buyside"))
