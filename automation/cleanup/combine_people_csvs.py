"""Combine the 242 exported People CSVs under "Labs - Tables - Cleaned" into one
file with a fixed 26-column schema, reconciling the columns that carry the same
data under different names, and deriving Country from Location where the table
has no Country column.

Per the user (2026-08-07). Pure local file work: no browser, no Clay, no credits.

Requires `pycountry` (pip install pycountry) for country-name recognition.

=== Reconciliation ===

The 242 files disagree on names for identical data, because they were built by
different generations of the pipeline. ALIASES maps each output column to its
candidates in priority order; the first PRESENT IN THE HEADER wins (presence, not
truthiness — an existing-but-empty cell is a real empty value, not a reason to
fall through to a different column's data). The non-obvious ones, each confirmed
against sample values rather than guessed:

  Company Name    `Company` (22 files) preferred over `Company Table Data` (242),
                  because the latter is the lookup's DISPLAY value and is
                  sometimes an abbreviation: Material Sciences shows
                  Company Table Data="IRLabs" vs Company="Infrared Laboratories, Inc."
  Linkedin URL    `Company LinkedIn` in the 12 Products & Services files — verified
                  to be EXACTLY the set of 12 files missing `LinkedIn URL`, holding
                  the same https://www.linkedin.com/company/... values.
  Domain          falls back to `Company Domain` for the 92 files with no `Domain`.
                  NOTE these two differ in FORMAT: `Domain` is sometimes a full URL
                  ("https://cobetter.com/") where `Company Domain` is bare
                  ("cobetter.com"). Values are passed through unchanged; see the
                  format report printed at the end.
  Subindustry     `Sub Industry` (230) / `Sub-Industry` (12) — disjoint, together 242.

`Company Enrichments (CPJ)` from the user's list is NOT an output column: no
column in any of the 397 exported CSVs mentions CPJ or "enrichment". It is a
section label introducing the 13 company-enrichment columns that follow it.

=== Country ===

Only 22 of 242 files have a `Country` column; 224 carry `Location` instead, which
is a composite whose shape varies by locale. Resolution is tiered, and every row
records which tier produced it so the report can show how much is derived.

`Location` is preferred over `Country` where both exist — see country_of() for
why; the short version is that in those 4 files `Country` is the COMPANY's
country from the CPJ lookup, not the person's, and the two agree only 42.6% of
the time. Tier 1 therefore fires only for the 19 files that have `Country` +
`City` + `State or Province` and no Location, where it IS the person's country.

  1 the file's own `Country` column, when there is no Location
  2 last comma-segment of Location, if a known country ("Orange, California,
    United States" -> United States; "Tianjin, China" -> China)   ~81%
  3 whole Location, if it is itself a known country     ("Spain")
    (NATIVE_NAMES / METRO_AREAS / US states also feed tiers 2-3: "Nederland"
     -> Netherlands, "Greater Boston" -> United States)
  4 NATIVE_SCRIPT_COUNTRY substring match, for locations written in a non-Latin
    script ("中国 广东省 深圳" -> China, "دبي الإمارات العربية المتحدة" -> United
    Arab Emirates)
  5 non-Latin but no country recognised -> the WHOLE Location value
  6 unresolved -> the WHOLE Location value, so nothing is silently dropped

Every value, including tier 1, goes through canonicalize_country(), so the output
is one English name per country: acronyms and long forms ("UAE", "United States
of America", "Republic of Korea") all collapse onto pycountry's common_name
("United Arab Emirates", "United States", "South Korea").

Tiers 5 and 6 mean the column is not 100% country names; the printed report gives
the exact split. METRO_AREAS and NATIVE_SCRIPT_COUNTRY are deliberately partial —
they cover the observed volume, and anything else falls through to the raw value
rather than being guessed at.

`Domain` is normalised to a bare host (bare_domain): scheme, www., path and query
stripped, lowercased — the two source columns otherwise mix "https://x.com/" with
"x.com".

    python combine_people_csvs.py --dry-run
    python combine_people_csvs.py
    python combine_people_csvs.py --country-report   # show unresolved Locations
"""
import argparse
import csv
import os
import re
import sys
import unicodedata
from collections import Counter

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.normpath(os.path.join(SCRIPT_DIR, "..", ".."))
sys.path.insert(0, os.path.join(REPO_ROOT, "automation", "build_automation"))
sys.path.insert(0, os.path.join(REPO_ROOT, "automation", "clay_sync"))

import browser_session  # noqa: E402

csv.field_size_limit(10 ** 9)

try:
    import pycountry
except ImportError:
    raise SystemExit("this pass needs pycountry:  pip install pycountry")

# A local filesystem path, so it is account-specific: read it from
# $CLAY_CLEANED_EXPORT_ROOT or `cleaned_export_root:` in config/local.yaml
# rather than fixing one machine's path in tracked source (same convention as
# export_event_tables.py's export_root). The default keeps this machine working.
DEFAULT_ROOT = browser_session.local_setting(
    "cleaned_export_root", "CLAY_CLEANED_EXPORT_ROOT",
    default=r"C:\Users\qasim\terrapinn\Labs - Tables - Cleaned")
DEFAULT_OUT = "People_combined.csv"

PEOPLE_FILES = {"Sellers - People.csv", "Buyers - People.csv",
                "Sponsors - Sellers - People.csv",
                "Sponsors - Buyers - People.csv", "People.csv"}

# output column -> source candidates, first present in the header wins
ALIASES = [
    ("Full Name",       ["Full Name"]),
    ("Job Title",       ["Job Title", "Title"]),
    ("Company Name",    ["Company", "Company Name", "Company Table Data"]),
    # Country is resolved separately (see country_of); listed here for ordering.
    ("Country",         []),
    ("Side",            ["Side"]),
    ("Classification",  ["Classification"]),
    ("Linkedin Profile", ["LinkedIn Profile", "Linkedin Profile"]),
    ("Work Email",      ["WORK EMAIL", "Work Email"]),
    ("Email Validation Result",
                        ["Email Validation Result", "Email Validation Status",
                         "Email Status Combined", "Status", "Status (2)"]),
    ("Smtp Provider",   ["Smtp Provider", "Mx Provider", "Mx Provider (2)"]),
    ("Created at",      ["Created At", "Created at"]),
    ("Updated at",      ["Updated At", "Updated at"]),
    ("Size",            ["Size"]),
    ("Type",            ["Type"]),
    ("Domain",          ["Domain", "Company Domain"]),
    ("Linkedin URL",    ["LinkedIn URL", "Linkedin URL", "Company LinkedIn"]),
    ("Founded",         ["Founded"]),
    ("Industry",        ["Industry"]),
    ("Description",     ["Description"]),
    ("Specialities",    ["Specialities", "Specialties"]),
    ("Subindustry",     ["Sub Industry", "Sub-Industry", "Subindustry"]),
    ("Primary Offerings", ["Primary Offerings"]),
    ("Business Stage",  ["Business Stage"]),
    ("Annual Revenue",  ["Annual Revenue"]),
    ("Total Funding Amount Range USD",
                        ["Total Funding Amount Range Usd",
                         "Total Funding Amount Range USD"]),
]
# Output order, per the user (2026-08-07): timestamps, then where the row came
# from, then everything about the COMPANY, then Side/Classification, then the
# PERSON, then email + its validation. Kept as an explicit list rather than
# ALIASES order, because ALIASES is a mapping table and reordering it to please
# the spreadsheet would obscure which source column feeds which output.
#
# The company/person split is the point of the layout, so: Company Name, Domain
# and `Linkedin URL` are the COMPANY's (Linkedin URL resolves from
# `Company LinkedIn` in 12 files), while `Linkedin Profile` and Country are the
# PERSON's — Country is derived from the person's Location, not the company's
# country. See country_of().
OUT_COLS = [
    # when
    "Created at", "Updated at",
    # where it came from
    "Event", "Source", "Category Type",
    # the company
    "Company Name", "Domain", "Linkedin URL",
    "Size", "Type", "Founded", "Industry", "Description", "Specialities",
    "Subindustry", "Primary Offerings", "Business Stage", "Annual Revenue",
    "Total Funding Amount Range USD",
    # how it is segmented
    "Side", "Classification",
    # the person
    "Full Name", "Job Title", "Country", "Linkedin Profile",
    # email + validation
    "Work Email", "Email Validation Result", "Smtp Provider",
]

# Fail loudly if the layout and the column set ever drift apart: a typo here
# would otherwise silently drop a column from the export or write an empty one.
_expected = {n for n, _ in ALIASES} | {"Event", "Source", "Category Type"}
assert set(OUT_COLS) == _expected, (
    f"OUT_COLS out of sync: missing {_expected - set(OUT_COLS)}, "
    f"unexpected {set(OUT_COLS) - _expected}")
assert len(OUT_COLS) == len(set(OUT_COLS)), "OUT_COLS has a duplicate"

# --- pipeline grouping -----------------------------------------------------
# The export tree is flat for Competitive Events (<Event>/<table>.csv) and two
# levels deep for the other three pipelines (<Group>/<Segment>/<table>.csv), so
# the group is the first path component when it is one of these, else the row
# came from a Competitive Events workbook.
KNOWN_GROUPS = {"Buyside P&S", "Products & Services", "Other Sources"}
COMPETITIVE = "Competitive Events"

# Category Type, per the user (2026-08-07). For the three named groups the
# group decides it; inside Competitive Events the FILENAME decides, because a
# "Sponsors - Sellers - People" table hangs off Sponsors_normalized while a
# plain "Sellers - People" hangs off Exhibitors_normalized.
GROUP_CATEGORY = {
    "Products & Services": "SellSide P&S",
    "Buyside P&S": "BuySide P&S",
    "Other Sources": "Other Source",
}


# SUPPLIED, NOT MEASURED. Buyside P&S / FMCG is the one People table of 242
# that was exported without unhiding the `Created At` / `Updated At` system
# columns, so its 2,548 rows carry no timestamps at all (33 columns where its
# siblings have 35). The user supplied this value on 2026-08-07 to stand in for
# them: "7th August 2026, 01:19 PM", written in the format every real value in
# these columns uses (YYYY-MM-DDTHH:MM:SS.mmmZ).
#
# These are therefore a CONSTANT placeholder, not per-row Clay timestamps: real
# ones vary row to row (an ACHEMA table spans 17 distinct Created At values in
# 1.9s). Do not read them as an import time, and re-export the table with the
# columns unhidden if the true values are ever needed. Applied only where the
# cell is blank, so it can never overwrite a genuine timestamp.
TIMESTAMP_BACKFILL = {
    ("Buyside P&S", "FMCG"): "2026-08-07T13:19:00.000Z",
}


class LayoutError(Exception):
    """The export tree does not match what this pass knows how to classify."""


def group_of(rel_path):
    """Pipeline group for a People CSV, from its path depth.

    Fails loudly on an unrecognised layout instead of defaulting. The default
    was the dangerous behaviour: a new ICP whose group folders are named
    something other than KNOWN_GROUPS would have every row silently labelled
    `Competitive Events` / `Exhibitor` — a wrong answer with no error, which is
    far worse than a crash. Depth 2 (<Event>/<table>.csv) is a genuine
    Competitive Events workbook; depth 3 must name a known group."""
    parts = rel_path.split(os.sep)
    if len(parts) == 2:
        return COMPETITIVE
    if len(parts) == 3:
        if parts[0] in KNOWN_GROUPS:
            return parts[0]
        raise LayoutError(
            f"{rel_path}: unknown pipeline group {parts[0]!r}. Add it to "
            f"KNOWN_GROUPS and GROUP_CATEGORY (and the Side rule if the group "
            f"is single-sided), or re-run with --allow-unknown to treat it as "
            f"{COMPETITIVE}.")
    raise LayoutError(
        f"{rel_path}: unexpected nesting depth {len(parts)}; this pass "
        f"understands <Event>/<table>.csv and <Group>/<Segment>/<table>.csv.")


def looks_like_people_file(filename):
    """A CSV that is a People table by name but not one this pass knows.

    PEOPLE_FILES is an exact-match allowlist, so an unrecognised People table
    is skipped in silence and simply missing from the output — a smaller sheet
    with no complaint. Anything with 'People' in the name that is not in the
    allowlist is therefore an error, not a skip."""
    return (filename.lower().endswith(".csv")
            and "people" in filename.lower()
            and filename not in PEOPLE_FILES)


def category_of(group, filename):
    if group in GROUP_CATEGORY:
        return GROUP_CATEGORY[group]
    return "Sponsor" if filename.startswith("Sponsors - ") else "Exhibitor"


# Company tables a People table can inherit Classification from, most specific
# first. Only consulted for rows whose own Classification is blank.
def company_tables(directory, people_filename):
    names = sorted(f for f in os.listdir(directory)
                   if f.endswith("_normalized.csv") or f == "Companies.csv")
    want = "Sponsors_normalized.csv" if people_filename.startswith("Sponsors - ") \
        else "Exhibitors_normalized.csv"
    names.sort(key=lambda f: (f != want,))
    return [os.path.join(directory, f) for f in names]


def classification_index(directory, people_filename):
    """bare-domain -> Classification, from this workbook's company table(s).

    Fills the 4,059 rows (3 files: MEDICA, ChemSpec Europe, The Advanced
    Materials Show USA) whose People table carries no Classification while the
    sibling Exhibitors_normalized does. Keyed on the domain because that is the
    only identifier both tables share; matched 100% of those rows."""
    idx = {}
    for path in company_tables(directory, people_filename):
        try:
            with open(path, encoding="utf-8-sig", newline="") as fh:
                for r in csv.DictReader(fh):
                    cls = (r.get("Classification") or "").strip()
                    if not cls:
                        continue
                    for kc in ("Company Domain", "Normalize a Domain",
                               "Official Domain", "Domain"):
                        k = bare_domain(r.get(kc))
                        if k:
                            idx.setdefault(k, cls)
        except (OSError, csv.Error):
            continue
    return idx

# --- country recognition ---------------------------------------------------
_COUNTRY = {}
for _c in pycountry.countries:
    _canon = getattr(_c, "common_name", _c.name)
    for _a in filter(None, {getattr(_c, "name", None),
                            getattr(_c, "common_name", None),
                            getattr(_c, "official_name", None),
                            _c.alpha_2, _c.alpha_3}):
        _COUNTRY[_a.lower()] = _canon

# Colloquial and constituent-country names pycountry does not carry.
ALIAS_NAMES = {
    "usa": "United States", "u.s.a.": "United States", "us": "United States",
    "u.s.": "United States", "united states of america": "United States",
    "uk": "United Kingdom", "u.k.": "United Kingdom",
    "great britain": "United Kingdom", "england": "United Kingdom",
    "scotland": "United Kingdom", "wales": "United Kingdom",
    "northern ireland": "United Kingdom",
    "turkey": "Türkiye", "south korea": "South Korea", "north korea": "North Korea",
    "russia": "Russia", "vietnam": "Vietnam", "czech republic": "Czechia",
    "ivory coast": "Côte d'Ivoire", "uae": "United Arab Emirates",
    "holland": "Netherlands", "macau": "Macao", "laos": "Laos",
    "syria": "Syria", "iran": "Iran", "tanzania": "Tanzania",
    "bolivia": "Bolivia", "venezuela": "Venezuela", "moldova": "Moldova",
    "brunei": "Brunei", "cape verde": "Cabo Verde", "swaziland": "Eswatini",
    "burma": "Myanmar", "palestine": "Palestine",
    "democratic republic of the congo": "Congo, The Democratic Republic of the",
    "hong kong sar": "Hong Kong",
}

# Country names written in their own language, seen in the Location field.
NATIVE_NAMES = {
    "nederland": "Netherlands", "italia": "Italy", "schweiz": "Switzerland",
    "suisse": "Switzerland", "svizzera": "Switzerland", "españa": "Spain",
    "espana": "Spain", "méxico": "Mexico", "danmark": "Denmark",
    "österreich": "Austria", "osterreich": "Austria", "sverige": "Sweden",
    "polska": "Poland", "deutschland": "Germany", "belgië": "Belgium",
    "belgique": "Belgium", "norge": "Norway", "suomi": "Finland",
    "brasil": "Brazil", "türkiye": "Türkiye", "hrvatska": "Croatia",
    "magyarország": "Hungary", "česko": "Czechia", "ελλάδα": "Greece",
}

# LinkedIn metro-area labels -> country. Deliberately partial: it covers the
# high-volume entries; anything absent falls through to the raw Location rather
# than being guessed at.
METRO_AREAS = {c.lower(): v for c, v in {
    # United States
    "New York City Metropolitan Area": "United States",
    "New York Metropolitan Area": "United States",
    "Greater Boston": "United States", "San Francisco Bay Area": "United States",
    "Greater Chicago Area": "United States", "Greater Philadelphia": "United States",
    "Greater Houston": "United States", "Dallas-Fort Worth Metroplex": "United States",
    "Los Angeles Metropolitan Area": "United States",
    "Washington DC-Baltimore Area": "United States",
    "Atlanta Metropolitan Area": "United States",
    "Raleigh-Durham-Chapel Hill Area": "United States",
    "Greater Minneapolis-St. Paul Area": "United States",
    "Detroit Metropolitan Area": "United States", "Greater Cleveland": "United States",
    "Texas Metropolitan Area": "United States", "Ohio Metropolitan Area": "United States",
    "Greater Phoenix Area": "United States", "Denver Metropolitan Area": "United States",
    "Greater Seattle Area": "United States", "Greater St. Louis": "United States",
    "Miami-Fort Lauderdale Area": "United States",
    "Greater Tampa Bay Area": "United States", "Charlotte Metro": "United States",
    "Cincinnati Metropolitan Area": "United States",
    "Greater Pittsburgh Region": "United States",
    "Salt Lake City Metropolitan Area": "United States",
    "Greater Indianapolis": "United States", "Greater Milwaukee": "United States",
    "Nashville Metropolitan Area": "United States",
    "San Diego Metropolitan Area": "United States",
    "Kansas City Metropolitan Area": "United States",
    "South Carolina Area": "United States", "Greater Orlando": "United States",
    "Greater Hartford": "United States", "Knoxville Metropolitan Area": "United States",
    "Oregon Metropolitan Area": "United States", "Greater Sacramento": "United States",
    "Greater Madison Area": "United States", "Alabama Area": "United States",
    "Memphis Metropolitan Area": "United States",
    "Greater Richmond Region": "United States",
    "Grand Rapids Metropolitan Area": "United States",
    "Louisville Metropolitan Area": "United States",
    # United Kingdom
    "Greater Aberdeen Area": "United Kingdom", "Greater Oxford Area": "United Kingdom",
    "Greater Glasgow Area": "United Kingdom", "Greater Cardiff Area": "United Kingdom",
    "Belfast Metropolitan Area": "United Kingdom",
    "Greater Sheffield Area": "United Kingdom",
    "Greater Edinburgh Area": "United Kingdom", "Greater Reading Area": "United Kingdom",
    "Greater Leeds Area": "United Kingdom", "Greater Leicester Area": "United Kingdom",
    "Greater Swansea Area": "United Kingdom", "Greater Liverpool Area": "United Kingdom",
    "Greater Exeter Area": "United Kingdom", "Greater Cambridge Area": "United Kingdom",
    "Greater Cheshire West and Chester Area": "United Kingdom",
    # elsewhere
    "Greater Barcelona Metropolitan Area": "Spain",
    "Greater Madrid Metropolitan Area": "Spain",
    "Greater Bilbao Metropolitan Area": "Spain",
    "Barcelona y alrededores": "Spain", "Madrid y alrededores": "Spain",
    "Greater Paris Metropolitan Region": "France", "Greater Lyon Area": "France",
    "Greater Lille Metropolitan Area": "France", "Paris et périphérie": "France",
    "Mumbai Metropolitan Region": "India", "Greater Hyderabad Area": "India",
    "Greater Delhi Area": "India", "Greater Ahmedabad Area": "India",
    "Pune/Pimpri-Chinchwad Area": "India", "Greater Bengaluru Area": "India",
    "Antwerp Metropolitan Area": "Belgium", "Ghent Metropolitan Area": "Belgium",
    "Brussels Metropolitan Area": "Belgium",
    "Randstad": "Netherlands", "Arnhem-Nijmegen Region": "Netherlands",
    "Greater Milan Metropolitan Area": "Italy",
    "Riyadh Region": "Saudi Arabia", "Jakarta Metropolitan Area": "Indonesia",
    "Johannesburg Metropolitan Area": "South Africa", "Greater Istanbul": "Türkiye",
    "Ho Chi Minh City Metropolitan Area": "Vietnam", "Hanoi Capital Region": "Vietnam",
    "Greater Stockholm Metropolitan Area": "Sweden",
    "Greater Gothenburg Metropolitan Area": "Sweden",
    "Greater Zurich Area": "Switzerland",
    "Greater Montreal Metropolitan Area": "Canada", "Greater Tokyo Area": "Japan",
    # second pass, added after measuring the tail
    "Greater Brighton and Hove Area": "United Kingdom",
    "Greater Southampton Area": "United Kingdom",
    "Greater Preston Area": "United Kingdom",
    "Greater Nottingham": "United Kingdom", "Greater Coventry Area": "United Kingdom",
    "Athens Metropolitan Area": "Greece", "Helsinki Metropolitan Area": "Finland",
    "Greater Malmö Metropolitan Area": "Sweden",
    "Seoul Incheon Metropolitan Area": "South Korea",
    "Greater Groningen Area": "Netherlands", "Greater Enschede Area": "Netherlands",
    "Amsterdam Area": "Netherlands",
    "Greater Metz Area": "France",
    "Greater Bergamo Metropolitan Area": "Italy", "Milano": "Italy",
    "Greater Vancouver Metropolitan Area": "Canada",
    "Greater Pamplona Area": "Spain", "Krakow Metropolitan Area": "Poland",
    "Metro Jacksonville": "United States", "Greater Tucson Area": "United States",
    "Buffalo-Niagara Falls Area": "United States",
    "Oklahoma City Metropolitan Area": "United States",
}.items()}

# US states/territories, so bare "California" and "<State> Metropolitan Area"
# resolve without enumerating every metro label.
US_STATES = {s.lower() for s in [
    "Alabama", "Alaska", "Arizona", "Arkansas", "California", "Colorado",
    "Connecticut", "Delaware", "Florida", "Georgia", "Hawaii", "Idaho",
    "Illinois", "Indiana", "Iowa", "Kansas", "Kentucky", "Louisiana", "Maine",
    "Maryland", "Massachusetts", "Michigan", "Minnesota", "Mississippi",
    "Missouri", "Montana", "Nebraska", "Nevada", "New Hampshire", "New Jersey",
    "New Mexico", "New York", "North Carolina", "North Dakota", "Ohio",
    "Oklahoma", "Oregon", "Pennsylvania", "Rhode Island", "South Carolina",
    "South Dakota", "Tennessee", "Texas", "Utah", "Vermont", "Virginia",
    "Washington", "West Virginia", "Wisconsin", "Wyoming",
    "District of Columbia", "Washington DC", "Puerto Rico",
]}
# NB "Georgia" is both a US state and a country. pycountry is consulted BEFORE
# US_STATES in lookup_country, so a bare "Georgia" resolves to the country —
# the safer default here, since a Location whose last segment is a bare US state
# name is far rarer than one naming the country.

_METRO_AFFIX = re.compile(
    r"^(greater|metro|metropolitan|metropolregion|grand)\s+|"
    r"\s+(metropolitan\s+)?(area|region|metroplex|metro)$", re.I)

_NON_LATIN = ("CJK", "HIRAGANA", "KATAKANA", "HANGUL", "ARABIC", "CYRILLIC",
              "HEBREW", "THAI", "DEVANAGARI", "GREEK")

# Country names written in a non-Latin script, matched as a SUBSTRING with
# longest-match-wins. Substring rather than prefix/suffix because the scripts
# disagree on where the country goes: Arabic puts it last
# ("دبي الإمارات العربية المتحدة" = Dubai, UAE) and Chinese puts it first
# ("中国 广东省 深圳" = China, Guangdong, Shenzhen).
#
# Longest-match is what disambiguates عمان, which is both Oman and Amman:
# "مسقط مسقط عمان" (Muscat) contains only عمان -> Oman, while "عمان الأردن"
# (Amman) also contains the longer الأردن -> Jordan. Ordering by length makes
# that fall out automatically instead of needing a special case.
NATIVE_SCRIPT_COUNTRY = {
    # Arabic
    "الإمارات العربية المتحدة": "United Arab Emirates",
    "المملكة العربية السعودية": "Saudi Arabia",
    "السعودية": "Saudi Arabia",
    "الأردن": "Jordan", "عمان": "Oman", "مصر": "Egypt", "العراق": "Iraq",
    "البحرين": "Bahrain", "الكويت": "Kuwait", "قطر": "Qatar",
    "لبنان": "Lebanon", "المغرب": "Morocco", "الجزائر": "Algeria",
    "تونس": "Tunisia", "ليبيا": "Libya", "السودان": "Sudan",
    "سوريا": "Syria", "سورية": "Syria", "اليمن": "Yemen",
    "فلسطين": "Palestine", "تركيا": "Türkiye", "باكستان": "Pakistan",
    "الهند": "India", "إيران": "Iran", "موريتانيا": "Mauritania",
    "الصومال": "Somalia", "جيبوتي": "Djibouti",
    # Chinese / Japanese (Han)
    "中华人民共和国": "China", "中華人民共和國": "China", "中国": "China",
    "中國": "China", "台灣": "Taiwan", "台湾": "Taiwan",
    "香港": "Hong Kong", "澳門": "Macao", "澳门": "Macao",
    "日本": "Japan", "新加坡": "Singapore", "韓國": "South Korea",
    "韩国": "South Korea", "大韓民國": "South Korea",
    # Korean
    "대한민국": "South Korea", "한국": "South Korea",
    # Thai / Cyrillic / Greek / Hebrew
    "ประเทศไทย": "Thailand", "ไทย": "Thailand",
    "Россия": "Russia", "Российская Федерация": "Russia",
    "Украина": "Ukraine", "Казахстан": "Kazakhstan", "Беларусь": "Belarus",
    "Ελλάδα": "Greece", "ישראל": "Israel",
    # City/region tokens for the native-script METRO labels, which name no
    # country at all ("서울 인천 지역" = Seoul-Incheon area, "常州-无锡-苏州地区"
    # = Changzhou-Wuxi-Suzhou area, "広域東京エリア" = Greater Tokyo). Each token
    # below belongs to exactly one country, so substring matching is safe.
    "서울": "South Korea", "인천": "South Korea", "대전": "South Korea",
    "울산": "South Korea", "부산": "South Korea",
    "常州": "China", "无锡": "China", "苏州": "China", "西安": "China",
    "广州": "China", "佛山": "China", "太原": "China", "北京": "China",
    "上海": "China", "深圳": "China",
    "東京": "Japan", "大阪": "Japan",
    "高雄": "Taiwan", "臺北": "Taiwan", "台北": "Taiwan",
    "كازابلانكا": "Morocco",
}
# longest first, so a shorter name nested inside a longer one never wins
_NATIVE_ORDERED = sorted(NATIVE_SCRIPT_COUNTRY.items(),
                         key=lambda kv: -len(kv[0]))


def native_script_country(text):
    for native, english in _NATIVE_ORDERED:
        if native in text:
            return english
    return None


# --- city gazetteer (last resort) -----------------------------------------
# The unresolved tail is not misspelled countries, it is LinkedIn metro labels
# naming a city: "Greater Strasbourg Metropolitan Area", "Basel Metropolitan
# Area", "Greensboro--Winston-Salem--High Point Area". geonamescache maps city
# -> country; where a name exists in several countries ("Rome" is in IT, US and
# TG; "Edmonton" in CA and GB) the MOST POPULOUS wins, which picks Rome, Italy
# over Rome, Georgia. Cities below MIN_CITY_POP are ignored so an obscure
# hamlet sharing a common English word cannot hijack a label.
MIN_CITY_POP = 50_000
_CITY = {}
try:
    import geonamescache as _gnc
    _gc = _gnc.GeonamesCache()
    _iso2 = {c.alpha_2: getattr(c, "common_name", c.name)
             for c in pycountry.countries}
    for _city in _gc.get_cities().values():
        _pop = _city.get("population") or 0
        if _pop < MIN_CITY_POP:
            continue
        _cn = _iso2.get(_city.get("countrycode"))
        if not _cn:
            continue
        for _nm in {_city["name"], *(_city.get("alternatenames") or [])}:
            _k = _nm.strip().lower()
            if not _k:
                continue
            prev = _CITY.get(_k)
            if prev is None or _pop > prev[1]:
                _CITY[_k] = (_cn, _pop)
except ImportError:            # optional: without it the tail stays raw
    _gc = None

_SPLIT_TOKENS = re.compile(r"\s+et\s+|\s+and\s+|--|[-–—/,]")


def city_country(text):
    """Country for a metro label naming a city, or None.

    Runs only after every country lookup has failed, so it can never override a
    real country name — "Georgia" resolves as the country long before it could
    resolve as a US city."""
    if not _CITY:
        return None
    t = (text or "").strip().lower()
    core = _METRO_AFFIX.sub("", t).strip()
    for cand in (core, t):
        if cand in _CITY:
            return _CITY[cand][0]
    # "Greensboro--Winston-Salem--High Point Area", "Lyon et périphérie":
    # take the FIRST component that resolves, not the best-populated one.
    # Best-populated looked reasonable and was wrong: it resolved that
    # Greensboro label to ISRAEL, because a later fragment ("Salem") matched a
    # much larger city elsewhere. LinkedIn always puts the anchor city first,
    # so leftmost-wins is both safer and semantically right.
    for piece in _SPLIT_TOKENS.split(core):
        piece = _METRO_AFFIX.sub("", piece.strip()).strip()
        hit = _CITY.get(piece)
        if hit:
            return hit[0]
    return None


def canonicalize_country(value):
    """Best English country name for a value, or the value unchanged.

    Applied to EVERY tier including the file's own Country column, so acronyms
    and long-form variants ("UAE", "United States of America", "Republic of
    Korea") land on the same string as everything else. pycountry's
    common_name is the canonical form, which is why this returns "South Korea"
    rather than "Korea, Republic of"."""
    v = (value or "").strip()
    if not v:
        return ""
    hit = lookup_country(v)
    if hit:
        return hit
    hit = native_script_country(v)
    if hit:
        return hit
    return v


def bare_domain(value):
    """Strip scheme, www., path, query and trailing dots/slashes.

    The two source columns disagree on format — `Domain` is often a full URL
    ("https://cobetter.com/") where `Company Domain` is bare ("cobetter.com") —
    so without this the merged column mixes both. Anything that does not look
    like a host is returned unchanged rather than mangled."""
    v = (value or "").strip()
    if not v:
        return ""
    v = re.sub(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", "", v)
    v = v.split("/")[0].split("?")[0].split("#")[0]
    v = v.split("@")[-1]                      # in case a URL carried userinfo
    v = v.rstrip(".").strip().lower()
    if v.startswith("www."):
        v = v[4:]
    return v


def is_non_latin(s):
    """True if the string's letters are in a non-Latin script. Checked by
    Unicode character NAME, not by a codepoint range, so accented Latin
    ("Türkiye", "México") is never misread as a foreign script."""
    for ch in s:
        if ch.isalpha():
            n = unicodedata.name(ch, "")
            if any(n.startswith(k) for k in _NON_LATIN):
                return True
    return False


def lookup_country(text):
    t = (text or "").strip().lower().rstrip(".")
    if not t:
        return None
    hit = (_COUNTRY.get(t) or ALIAS_NAMES.get(t) or NATIVE_NAMES.get(t)
           or METRO_AREAS.get(t))
    if hit:
        return hit
    if t in US_STATES:
        return "United States"
    # Strip a "Greater ..." / "... Metropolitan Area" wrapper and retry once.
    # This is what turns "Italy Metropolitan Area" -> Italy and
    # "South Carolina Metropolitan Area" -> United States without needing an
    # entry per metro label. Applied only after the exact lookups above, so it
    # can never override a real name.
    core = _METRO_AFFIX.sub("", t).strip()
    if core and core != t:
        return (_COUNTRY.get(core) or ALIAS_NAMES.get(core)
                or NATIVE_NAMES.get(core)
                or ("United States" if core in US_STATES else None))
    return None


def country_of(row, country_col, location_col):
    """(value, tier) — see the module docstring for the tier meanings.

    Location BEATS the Country column when both exist. That looks backwards, and
    it is the single most important decision in this pass, so: the two are not
    the same field. In the 19 files with `Country` + `City` + `State or Province`
    and no Location, Country is the PERSON's country. But in the 4 files with
    `Country` + `Location` (ACHEMA Buyers, BIO Korea x2, Pittcon Sellers) the
    Country column arrived from the CPJ *company* lookup and holds the COMPANY's
    country, while Location holds the person's — measured on those 376 rows, the
    two agree only 42.6% of the time (Location "Ankara, Ankara, Türkiye" against
    Country "Germany", and so on for an ACHEMA-sized German exhibitor list).
    Preferring Location wherever it exists makes this column mean "the person's
    country" in all 242 files instead of silently mixing the two."""
    loc = (row.get(location_col) or "").strip() if location_col else ""
    if not loc:
        if country_col:
            v = (row.get(country_col) or "").strip()
            if v:
                return canonicalize_country(v), "1 Country column"
        return "", "0 no source"
    seg = loc.split(",")[-1].strip()
    hit = lookup_country(seg)
    if hit:
        return hit, "2 last segment"
    hit = lookup_country(loc)
    if hit:
        return hit, "3 whole Location"
    hit = native_script_country(loc)
    if hit:
        return hit, "4 native script"
    hit = city_country(seg) or city_country(loc)
    if hit:
        return hit, "5 city gazetteer"
    if is_non_latin(loc):
        return loc, "6 non-Latin, UNMAPPED"
    return loc, "6 unresolved, whole value"


def resolve(header, candidates):
    for c in candidates:
        if c in header:
            return c
    return None


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=DEFAULT_ROOT)
    ap.add_argument("--out", help=f"output CSV (default <root>/{DEFAULT_OUT})")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--country-report", action="store_true",
                    help="list the most common unresolved Location values")
    ap.add_argument("--allow-unknown", action="store_true",
                    help="downgrade layout errors (unknown pipeline group, "
                         "unrecognised People filename) to warnings; unknown "
                         f"groups are then treated as {COMPETITIVE!r}")
    args = ap.parse_args()

    out_path = args.out or os.path.join(args.root, DEFAULT_OUT)

    # Scan and VALIDATE the whole tree before processing anything, so an
    # unfamiliar layout is reported in full rather than one error at a time.
    files, problems = [], []
    for dp, _dns, fns in os.walk(args.root):
        for f in sorted(fns):
            full = os.path.join(dp, f)
            rel = os.path.relpath(full, args.root)
            # A People table always lives inside an event/segment folder, so
            # anything sitting at the root is not one — including this pass's
            # OWN output, People_combined.csv, which otherwise trips the
            # "looks like a People table" guard on every re-run.
            if os.sep not in rel or os.path.abspath(full) == os.path.abspath(out_path):
                continue
            if looks_like_people_file(f):
                problems.append(
                    f"{rel}: looks like a People table but is not in "
                    f"PEOPLE_FILES, so it would be skipped silently.")
                continue
            if f not in PEOPLE_FILES:
                continue
            try:
                group = group_of(rel)
            except LayoutError as e:
                # Record it and fall back, so --allow-unknown behaves exactly
                # as its message promises instead of quietly dropping the file.
                problems.append(str(e))
                group = COMPETITIVE
            # The Category Type of a Competitive Events table is inferred from
            # its filename prefix; check the inference still holds rather than
            # trusting it (it held 41/41 and 143/143 on the Labs tree).
            if group == COMPETITIVE:
                want = ("Sponsors_normalized.csv" if f.startswith("Sponsors - ")
                        else "Exhibitors_normalized.csv")
                if want not in os.listdir(dp):
                    problems.append(
                        f"{rel}: would be typed "
                        f"{category_of(group, f)!r} from its filename, but the "
                        f"folder has no {want} to back that up.")
            files.append((os.path.basename(dp), f, os.path.join(dp, f),
                          group, dp))
    files.sort()
    if problems:
        head = (f"{len(problems)} layout problem(s) under {args.root}:\n  "
                + "\n  ".join(problems[:25]))
        if len(problems) > 25:
            head += f"\n  ... +{len(problems) - 25} more"
        if not args.allow_unknown:
            raise SystemExit(head + "\n\nRe-run with --allow-unknown to "
                             "proceed anyway (unknown groups become "
                             f"{COMPETITIVE!r}).")
        print(f"[warn] {head}\n")
    if not files:
        raise SystemExit(f"no People CSVs under {args.root}")

    rows_out = []
    used = {n: Counter() for n, _ in ALIASES}
    filled = Counter()
    tiers = Counter()
    unresolved = Counter()
    per_event = Counter()
    missing_cols = Counter()

    for event, fname, path, group, directory in files:
        category = category_of(group, fname)
        cls_idx = None                       # built lazily, only if a row needs it
        with open(path, encoding="utf-8-sig", newline="") as fh:
            rd = csv.DictReader(fh)
            header = rd.fieldnames or []
            mapping = {}
            for name, cands in ALIASES:
                if name == "Country":
                    continue
                src = resolve(header, cands)
                mapping[name] = src
                if src:
                    used[name][src] += 1
                else:
                    missing_cols[name] += 1
            ccol = resolve(header, ["Country"])
            lcol = resolve(header, ["Location"])
            used["Country"][ccol or (lcol and "Location") or "-"] += 1
            for r in rd:
                out = {"Event": event, "Source": group,
                       "Category Type": category}
                for name, src in mapping.items():
                    out[name] = (r.get(src) or "") if src else ""
                out["Domain"] = bare_domain(out["Domain"])

                # Classification: for the two P&S pipelines it is the segment
                # folder (already true of every row, so this only backfills);
                # elsewhere inherit from the workbook's company table.
                if not out["Classification"].strip():
                    if group in GROUP_CATEGORY and group != "Other Sources":
                        out["Classification"] = event
                        filled["Classification/folder"] += 1
                    else:
                        if cls_idx is None:
                            cls_idx = classification_index(directory, fname)
                        key = out["Domain"] or bare_domain(r.get("Company Domain"))
                        hit = cls_idx.get(key) if key else None
                        if hit:
                            out["Classification"] = hit
                            filled["Classification/company table"] += 1
                        else:
                            filled["Classification STILL BLANK"] += 1

                # Timestamps: supplied placeholder for the one table exported
                # without the hidden system columns (see TIMESTAMP_BACKFILL).
                ts = TIMESTAMP_BACKFILL.get((group, event))
                if ts:
                    for tcol in ("Created at", "Updated at"):
                        if not out[tcol].strip():
                            out[tcol] = ts
                            filled[f"{tcol}/supplied placeholder"] += 1

                # Side: constant per pipeline for the two P&S groups.
                if not out["Side"].strip() and group in ("Products & Services",
                                                         "Buyside P&S"):
                    out["Side"] = ("Seller" if group == "Products & Services"
                                   else "Buyer")
                    filled["Side/pipeline rule"] += 1
                val, tier = country_of(r, ccol, lcol)
                out["Country"] = val
                tiers[tier] += 1
                if tier.startswith("6"):
                    unresolved[val.split(",")[-1].strip()] += 1
                rows_out.append(out)
                per_event[event] += 1

    print(f"[sources] {len(files)} People CSVs under {args.root}")
    print(f"[events]  {len(per_event)} distinct Event values")
    print(f"[rows]    {len(rows_out):,}")
    print(f"[schema]  {len(OUT_COLS)} columns\n")
    print("column -> source column(s) used (file count):")
    for name, _ in ALIASES:
        got = ", ".join(f"{s} x{n}" for s, n in used[name].most_common())
        miss = f"   [{missing_cols[name]} file(s) had none]" if missing_cols[name] else ""
        print(f"  {name:30} <- {got}{miss}")

    print("\nSource / Category Type:")
    for (s, c), n in sorted(Counter((r["Source"], r["Category Type"])
                                    for r in rows_out).items()):
        print(f"  {s:22} {c:16} {n:9,}")

    print("\nbackfilled:")
    if filled:
        for k, n in filled.most_common():
            print(f"  {k:34} {n:8,}")
    else:
        print("  (nothing was blank)")
    for c in ("Event", "Source", "Category Type", "Side", "Classification"):
        blank = sum(1 for r in rows_out if not (r[c] or "").strip())
        print(f"  {c:34} {blank:8,} still blank")

    print(f"\nCountry resolution ({len(rows_out):,} rows):")
    for t, n in sorted(tiers.items()):
        print(f"  {t:28} {n:8,}  {100 * n / len(rows_out):5.1f}%")

    if args.country_report and unresolved:
        print(f"\nunresolved Location tails ({len(unresolved)} distinct), top 25:")
        for v, n in unresolved.most_common(25):
            print(f"  {n:6,}  {v}")

    # Domain is normalised to a bare host; assert the normalisation actually
    # took, rather than trusting it.
    dfill = sum(1 for r in rows_out if r["Domain"].strip())
    bad = [r["Domain"] for r in rows_out
           if r["Domain"] and ("/" in r["Domain"] or "://" in r["Domain"]
                               or r["Domain"].startswith("www."))]
    print(f"\nDomain: {dfill:,} filled, normalised to bare hosts; "
          f"{len(bad)} still carry a scheme/path/www ({bad[:3] if bad else '-'})")

    raw_left = sum(n for t, n in tiers.items() if t.startswith("6"))
    print(f"Country: {raw_left:,} row(s) still hold a raw Location value "
          f"({100 * raw_left / len(rows_out):.2f}%)")

    if args.dry_run:
        print("\n[dry-run] nothing written")
        return 0

    with open(out_path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=OUT_COLS)
        w.writeheader()
        w.writerows(rows_out)
    print(f"\n[out] {out_path}  ({os.path.getsize(out_path):,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
