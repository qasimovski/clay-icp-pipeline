"""Build the full Labs pipeline in one event's workbook (Interphex-approved
recipe). Usage:  python build_workbook.py --folder "Forum Labo"

Every step is guarded by an existence check, so rerunning resumes an
interrupted build. Verification gates (VerificationError) stop the event rather than
save anything wrong. Paid columns are configured with auto-run OFF and never
run; the only executed actions are formulas, CSV imports, and Send Table Data.
"""
import argparse
import csv
import os
import re
import sys
import time

import browser_session
import formula_columns
import column_config as colcfg
import clay_ui
import blocklist_send

# This automation was copied here from a per-pilot folder that sat directly
# inside the scraper tree (three levels down from SCRAPERS_ROOT) with its
# reference CSVs one level up (REF_DIR). Now that it lives in its own repo,
# both locations are external to the repo and configurable via env vars —
# see automation/README.md. Defaults assume the repo sits next to `scrapers/`
# and lookup CSVs live in config/icps/<icp>/lookups/ (see config/local.yaml.example).
REPO_ROOT = os.path.normpath(os.path.join(browser_session.SCRIPT_DIR, "..", ".."))
SCRAPERS_ROOT = os.environ.get(
    "CLAY_PIPELINE_SCRAPERS_ROOT",
    os.path.normpath(os.path.join(REPO_ROOT, "..", "scrapers")))
REF_DIR = os.environ.get(
    "CLAY_PIPELINE_ICP_LOOKUPS_DIR",
    os.path.normpath(os.path.join(REPO_ROOT, "config", "icps", "labs", "lookups")))
ROLLOUT_SHOTS = os.path.join(browser_session.SCRIPT_DIR, "rollout_shots")

SEND_KEEP = {"Event", "Company Name", "Profile URL", "Booth", "Year",
             "Address Line 1", "City", "Postal Code", "Country", "Phone",
             "Email", "Website", "Normalized Country", "Company Domain",
             "Resolved Description", "Side", "Classification", "Fit",
             "Country Fit", "Composite Tier"}
ROUTE_KEEP = {"Event", "Company Name", "Company Domain", "Website",
              "Normalized Country", "Resolved Description", "Classification",
              "Fit", "Country Fit", "Composite Tier"}

OFFICIAL_DOMAIN_PROMPT = (
    "Research and return the verified primary corporate domain for the "
    "company below. Use web search to find candidate domains, then verify by "
    "checking the candidate site's logo, about page, and footer that it is "
    "that company's official website. Exclude social media and directory "
    "domains such as linkedin.com, facebook.com and crunchbase.com. Prefer "
    "the global root domain over regional or country subsites. Return ONLY "
    "the bare registrable domain, for example thermofisher.com, with no "
    "protocol, no path and no www prefix. If no official domain can be "
    "verified, return UNKNOWN.")

REGISTRAR_PROMPT = """#CONTEXT#
You are the "Labs Series Registrar" — an AI classification agent for Terrapinn's Lab Live event series. Your sole job is to read incoming company data and classify each company into exactly one Buyer OR Seller category from a closed list. Use the provided Description and Primary Offerings to determine the company's core commercial activity. If the Description is blank or insufficient, visit the Company Domain to understand what the company does before classifying. Do not include any names, companies, confidence scores, or reasoning in the output.

#OBJECTIVE#
Classify each company into exactly one category from either Buyer or Seller using the closed lists below, based on the company's core commercial activity inferred from Description, Primary Offerings, and, if needed, information found by visiting the Company Domain.

Buyer (company sources/uses lab products & services):
1) Medical, Diagnostics & Healthcare (Medical and Diagnostic Labs, Hospitals, Medical Practices, Veterinary, Healthcare)
2) Chemicals, Petrochemicals & Materials (Chemical Manufacturing, Oil/Gas/Mining, Plastics/Rubber, Metal, Glass/Ceramics)
3) Food & Agriculture (Food/Beverage Manufacturing, Dairy, Meat, Seafood, Farming, Horticulture)
4) FMCG (Cosmetics, Consumer Goods, Personal Care, Soap/Cleaning Manufacturing)
5) Environment, Water & Energy (Environmental Services, Water Supply/Irrigation, Waste Treatment, Utilities, Renewables)
6) Laboratories & Research Organisations (Research Services, Research, Testing, Inspection & Certification)
7) Government & Public Sector (Government Administration, Public Policy, Public Safety, Public Health)
8) Forensics (Law Enforcement, Forensic Science Labs)
9) Pharma & Biotech (Biotechnology Research, Pharmaceutical Manufacturing, Biotechnology)
10) Academic (Higher Education, Education Management)
11) Investors & Venture Capital (VC/PE Principals, Investment Management, Funds & Trusts)
12) Non-industry Specific JT Searches (Catchall lab-keyword searches across all industries)

Seller (company supplies/markets lab products & services):
1) Lab Equipment & Instrumentation Suppliers
2) Laboratory Data, Integration & Connectivity Software
3) Laboratory Automation & Robotics
4) Testing & Diagnostics
5) Chemicals & Reagents
6) Digital & AI Services
7) Material Sciences
8) Food and Agri tech & monitoring
9) FMCG testing solutions
10) Environment and energy tech & monitoring
11) Cleanroom Technology
12) Sustainability
13) Distributors of lab equipment
14) Real Estate, Facilities, Architecture
15) Strategic Management Consultants
16) Forensics & Security

#INSTRUCTIONS#
1. Primary determination (Step A):
   - Read Description first to identify core activity. If insufficient or blank, visit the Company Domain to determine what the company does.
   - Decide: does the company manufacture, distribute, or provide services related to lab equipment/software/testing/facilities (Seller), or does it operate labs, conduct R&D, or deliver healthcare/testing/regulation primarily for its own purposes (Buyer)?
2. Tie-breakers (Step B):
   - If the company type seems ambiguous/diversified, prefer signals from Company Domain and Description. If titles are present in Description, use these hints: procurement/lab/QA-QC/R&D/facility roles imply Buyer; sales/business development/channel/account roles imply Seller.
3. Special-case rules (Step C):
   - Distributors: classify as Seller -> "Distributors of lab equipment".
   - Strategic Management Consultants: classify as Seller -> "Strategic Management Consultants" unless clearly attending purely as a buy-side researcher for a client.
   - Investors & VC: classify as Buyer -> "Investors & Venture Capital" for funds/PE/VC when titles like Partner/Principal/Analyst/Associate are indicated; this overrides Step colcfg.
   - CROs/Contract Testing Organisations: if sourcing for own lab -> Buyer ("Pharma & Biotech" or "Laboratories & Research Organisations"). If exhibiting to sell testing services -> Seller -> "Testing & Diagnostics".
   - Academic institutions: default Buyer -> "Academic" even when a department maps to a vertical.
   - Real estate/architecture firms: always Seller -> "Real Estate, Facilities, Architecture".
4. Catchall rule (Step D):
   - Only use Buyer -> "Non-industry Specific JT Searches" when, after Steps A-C, the company and any title clues still do not resolve to a clear vertical (e.g., true cross-vertical conglomerates, students, media/trade associations, generic distributors without vertical focus).
5. Output format:
   - Output exactly two fields:
     - Side: Buyer or Seller
     - Classification: exact SHORT label from the closed list above (do not include the parenthetical description — e.g. output "Medical, Diagnostics & Healthcare", not the full line with examples in parentheses)
   - Do not include any additional commentary or fields.
6. Self-verification before output:
   - Confirm the classification is one of the exact 28 labels (matched by its short form, ignoring the parenthetical).
   - Confirm only one side (Buyer or Seller) is selected.
   - Confirm Step A was checked before inferring from titles.
   - If using Catchall, confirm Steps B-D genuinely failed to resolve a vertical.
7. If {{Description}} and {{Primary Offerings}} are empty but {{Company Domain}} has a value then use {{Company Domain}} for classification tasks.

#EXAMPLES#
Example 1:
Input summary: A company that manufactures chromatography instruments for labs.
Output:
Side: Seller
Classification: Lab Equipment & Instrumentation Suppliers

Example 2:
Input summary: A hospital network operating clinical labs providing patient diagnostics.
Output:
Side: Buyer
Classification: Medical, Diagnostics & Healthcare

Example 3:
Input summary: A venture capital fund investing in biotech and lab automation; title indicates Analyst.
Output:
Side: Buyer
Classification: Investors & Venture Capital

#INPUTS#"""


# Known-good formulas (pilot-approved shapes, null-safe). Written directly in
# the manual editor — the AI generator is only a fallback.
_FIT_A = '","'.join(["Medical, Diagnostics & Healthcare", "Laboratories & Research Organisations",
    "Pharma & Biotech", "Lab Equipment & Instrumentation Suppliers",
    "Laboratory Data, Integration & Connectivity Software", "Laboratory Automation & Robotics",
    "Testing & Diagnostics", "Chemicals & Reagents"])
_FIT_B = '","'.join(["Chemicals, Petrochemicals & Materials", "Food & Agriculture", "FMCG",
    "Environment, Water & Energy", "Forensics", "Academic", "Non-industry Specific JT Searches",
    "Digital & AI Services", "Material Sciences", "Food and Agri tech & monitoring",
    "FMCG testing solutions", "Environment and energy tech & monitoring", "Cleanroom Technology",
    "Sustainability", "Distributors of lab equipment"])
_FIT_C = '","'.join(["Government & Public Sector", "Investors & Venture Capital",
    "Real Estate, Facilities, Architecture", "Strategic Management Consultants",
    "Forensics & Security"])

HANDWRITTEN_FORMULAS = {
    "Normalize a Domain":
        '({{Website}}||"").toLowerCase().replace(/^https?:\/\//,"").replace(/^www\./,"").split("/")[0].replace(/\/$/,"")',
    "Normalize Company Name":
        '({{Company Name}}||"").toLowerCase().replace(/[^\w\s]/g," ").replace(/(inc|llc|ltd|limited|gmbh|ag|sa|bv|co|corp|corporation|kg|srl|spa|pty|plc|lp|llp)\s*$/,"").replace(/\s+/g," ").trim()',
    "Normalized Country":
        '((c)=>({"Great Britain":"United Kingdom","China PR":"China","Czech Rep.":"Czech Republic","Türkiye":"Turkey","USA":"United States","United Arab Emirates":"UAE","Hongkong, PR China":"China"})[c]||c||"")(({{Country}}||"").trim())',
    "Company Domain":
        '({{Normalize a Domain}}||"") ? ({{Normalize a Domain}}||"") : ((({{Official Domain}}||"").toUpperCase()=="UNKNOWN") ? "" : ({{Official Domain}}||""))',
    "Resolved Description":
        '({{Description (2)}}||"") ? ({{Description (2)}}||"") : ({{Description}}||"")',
    "Side": '{{Labs Series Registrar}}?.Side || ""',
    "Classification": '{{Labs Series Registrar}}?.Classification || ""',
    "Fit":
        '(["' + _FIT_A + '"].includes({{Classification}}) ? "A" : ["' + _FIT_B +
        '"].includes({{Classification}}) ? "B" : ["' + _FIT_C + '"].includes({{Classification}}) ? "C" : "")',
    "Country Fit":
        '((s,c)=>!s?"":s=="Seller"?(["United Kingdom","UAE","Saudi Arabia","United States","Netherlands"].includes(c)?"A":["United States","Germany","Japan","China"].includes(c)?"B":"C"):s=="Buyer"?(["United Kingdom","UAE","Saudi Arabia","United States","Netherlands"].includes(c)?"A":["Ireland","Belgium","France","Luxembourg","Switzerland","Qatar","Kuwait","Bahrain","Oman","Jordan","Lebanon","Canada","Mexico","Austria","Czech Republic","Denmark","Finland","Greece","Hungary","Italy","Norway","Poland","Portugal","Spain","Sweden"].includes(c)?"B":"C"):"")(({{Side}}||""),({{Normalized Country}}||""))',
    "Composite Tier":
        '((s,f,c)=>s!="Seller"||!f||!c?"":["AA","AB"].includes(f+c)?1:["AC","BA","BB"].includes(f+c)?2:3)(({{Side}}||""),({{Fit}}||""),({{Country Fit}}||""))',
    "Sector Keyword Match":
        '((t)=>["lab","laboratory","life science","pharma","scientific","analytical","biotech","diagnostics","r&d","research"].some(k=>t.includes(k)))((({{Company Name}}||"")+" "+({{Resolved Description}}||"")).toLowerCase())',
    "Contacts Composite Tier":
        '((f,j,c)=>!f||!j||!c?"":["AAA","ABA","BAA","AAB"].includes(f+j+c)?1:["BAB","BBA","CAB","AAC","ABB","BBB","ACA","ACB","BAC"].includes(f+j+c)?2:3)(({{Fit}}||""),({{JT Fit}}||""),({{Country Fit}}||""))',
}


FIT_LABELS_A = ["Medical, Diagnostics & Healthcare", "Laboratories & Research Organisations",
                "Pharma & Biotech", "Lab Equipment & Instrumentation Suppliers",
                "Laboratory Data, Integration & Connectivity Software",
                "Laboratory Automation & Robotics", "Testing & Diagnostics",
                "Chemicals & Reagents"]
FIT_LABELS_B = ["Chemicals, Petrochemicals & Materials", "Food & Agriculture", "FMCG",
                "Environment, Water & Energy", "Forensics", "Academic",
                "Non-industry Specific JT Searches", "Digital & AI Services",
                "Material Sciences", "Food and Agri tech & monitoring",
                "FMCG testing solutions", "Environment and energy tech & monitoring",
                "Cleanroom Technology", "Sustainability", "Distributors of lab equipment"]
FIT_LABELS_C = ["Government & Public Sector", "Investors & Venture Capital",
                "Real Estate, Facilities, Architecture", "Strategic Management Consultants",
                "Forensics & Security"]


def slug(folder):
    return re.sub(r"[^A-Za-z0-9]+", "_", folder).strip("_")


class WorkbookBuilder:
    def __init__(self, page, folder, log):
        self.page = page
        self.folder = folder
        self.log = log
        self.shots = os.path.join(ROLLOUT_SHOTS, slug(folder))
        os.makedirs(self.shots, exist_ok=True)

    def screenshot(self, name):
        p = os.path.join(self.shots, f"{name}.png")
        try:
            self.page.screenshot(path=p)
        except Exception:
            pass

    def say(self, msg):
        line = f"[{self.folder}] {msg}"
        print(line, flush=True)
        self.log.write(line + "\n")
        self.log.flush()


    def rename_new(self, name, table, prefixes, allow=()):
        """Rename the just-saved column; on failure re-navigate (resets all UI
        state — the reliable path) and recover the leftover by prefix."""
        try:
            colcfg.rename_last_column(self.page, name, timeout_s=45)
            return
        except Exception as e:
            self.say(f"WARN rename {name} flaked ({str(e)[:80]}); re-navigating")
        colcfg.open_workbook(self.page, self.folder)
        colcfg.focus_table_maybe_empty(self.page, table)
        if colcfg.recover_leftover(self.page, prefixes, name, allow=allow):
            self.say(f"recovered {name} after re-navigation")
            return
        # claygent auto-names are unpredictable — fall back to the newest
        # unknown non-Send column (rightmost). Warn if several candidates.
        unknown = [h for h in colcfg.unknown_headers(self.page)
                   if not h.startswith("Send")]
        if not unknown:
            raise colcfg.VerificationError(f"{name}: not found after re-navigation")
        if len(unknown) > 1:
            self.say(f"WARN multiple unknown columns {unknown}; renaming"
                     f" newest -> {name}; strays need manual cleanup")
        import formula_columns as _fc
        _fc.rename_column(self.page, unknown[-1], name)
        self.say(f"recovered {name} (was {unknown[-1]!r})")

    # ---------------------------------------------------------------- step 0
    def step_import(self):
        page = self.page
        csv_path = os.path.join(SCRAPERS_ROOT, self.folder, "Exhibitors_normalized.csv")
        if not os.path.exists(csv_path):
            raise colcfg.VerificationError("no Exhibitors_normalized.csv")
        last = None
        for attempt in range(4):
            try:
                clay_ui.open_target_location(page)
                exists = clay_ui.workbook_exists(page, self.folder)
                clay_ui.open_target_location(page)
                if not exists:
                    self.say("workbook missing -> creating with normalized CSV")
                    clay_ui.create_workbook_with_csvs(page, self.folder, [csv_path])
                    self.screenshot("00_workbook_created")
                    return
                clay_ui.open_workbook(page, self.folder)
                if colcfg.table_exists(page, "Exhibitors_normalized"):
                    self.say("normalized table already present")
                    return
                colcfg.add_csv_table_robust(page, csv_path)
                self.screenshot("00_imported")
                self.say("normalized table imported")
                return
            except Exception as e:
                last = e
                self.say(f"import attempt {attempt} failed: {str(e)[:120]}")
                page.wait_for_timeout(6000)
        raise colcfg.VerificationError(f"import failed after retries: {last}")

    # ------------------------------------------------------------- formulas
    NULL_SAFE = (' Very important: treat any input value that is null or '
                 'undefined as an empty string, never call any method directly '
                 'on a possibly null value, and use only null safe operations '
                 'so the formula never throws an error on empty rows.')

    def formula(self, name, desc, gate, allow_empty_preview=True):
        page = self.page
        page.keyboard.press("Escape")   # clear any lingering popover/editor
        page.wait_for_timeout(600)
        if colcfg.header_exists(page, name):
            self.say(f"column exists: {name}")
            return
        # recovery: delete broken formula leftovers from a prior interrupted
        # run (never touch send columns — those also read 100%)
        for h in colcfg.unknown_headers(page):
            if h.startswith("Send"):
                continue
            if colcfg.column_status(page, h) == "100%":
                cells = formula_columns.preview_cells(page, n=5, header=h) or []
                located = [c for c in cells if c is not None]
                if located and not any(c for c in located if c and c != "None"):
                    colcfg.delete_column(page, h)
                    self.say(f"deleted broken leftover column: {h}")
                else:
                    self.say(f"NOTE leftover {h!r} kept (status 100% but not "
                             f"confirmably broken)")
        f, preview = "", []
        template = HANDWRITTEN_FORMULAS.get(
            "Contacts Composite Tier" if (name == "Composite Tier" and
                                          getattr(self, "_contacts_ct", False))
            else name)
        if template:
            try:
                f, preview = formula_columns.build_formula_handwritten(
                    page, template, f"f_{slug(name)}")
                ne = [p for p in (preview or []) if p and p != "None"]
                ok, _why = gate(f, ne)
                if not ok and ne:
                    raise colcfg.VerificationError("manual result failed gate")
            except Exception as e:
                self.say(f"WARN {name}: manual editor failed ({str(e)[:80]}); "
                         f"falling back to AI generation")
                formula_columns.cancel_panel(page)
                page.wait_for_timeout(3000)
                f = ""
        for attempt in range(3):
            if f.strip():
                break
            try:
                f, preview = formula_columns.build_formula_column(
                    page, desc + self.NULL_SAFE, f"f_{slug(name)}")
            except Exception as e:
                self.say(f"WARN {name}: formula panel flake (attempt {attempt}): "
                         f"{str(e)[:80]}")
                page.keyboard.press("Escape")
                page.wait_for_timeout(3000)
                if "editor" in str(e).lower():
                    # Clay-side dead panel — a reload clears it (CMEF case)
                    try:
                        page.reload(wait_until="domcontentloaded")
                        page.wait_for_timeout(8000)
                    except Exception:
                        pass
                continue
            if f.strip():
                break
            # generation returned nothing — discard the draft and retry
            formula_columns.cancel_panel(page)
            self.say(f"WARN {name}: empty generation (attempt {attempt}), retrying")
            page.wait_for_timeout(4000)
        if not f.strip():
            raise colcfg.VerificationError(f"{name}: formula generation returned empty 3x")
        nonempty = [p for p in (preview or []) if p and p != "None"]
        ok, why = gate(f, nonempty)
        if not ok and not (allow_empty_preview and not nonempty and why == "preview"):
            formula_columns.cancel_panel(page)
            raise colcfg.VerificationError(f"{name}: gate failed ({why}); formula={f[:200]!r}")
        formula_columns.save_column(page, f"f_{slug(name)}_save")
        try:
            colcfg.rename_last_column(page, name)
        except Exception as e:
            # clean up the auto-named column so a retry can't stack duplicates
            self.say(f"WARN {name}: rename failed ({str(e)[:80]}); deleting "
                     f"saved column for clean retry")
            try:
                formula_columns.scroll_grid_right(page)
                auto = formula_columns.last_header(page)
                if auto and auto not in colcfg.KNOWN_HEADERS                         and not auto.startswith("Rows from"):
                    colcfg.delete_column(page, auto)
            except Exception:
                pass
            raise colcfg.VerificationError(f"{name}: rename failed; column removed for retry")
        # post-save health: '' (checkmark icon) = healthy; a persistent '100%'
        # WITHOUT any cell values = per-row errors. Large tables keep '100%'
        # visible for a while after computing, so poll long and, if it stays,
        # sample actual cell texts before declaring the column broken.
        status = None
        for _ in range(36):                    # up to 3 min for slow computes
            status = colcfg.column_status(page, name)
            if status == "":
                break
            page.wait_for_timeout(5000)
        if status == "100%":
            verdict = "inconclusive"
            cells = []
            for _ in range(3):                 # re-sample; computing cells fill in
                cells = formula_columns.preview_cells(page, n=6, header=name) or []
                if any(c for c in cells if c and c != "None"):
                    verdict = "healthy"
                    break
                page.wait_for_timeout(20000)
            if verdict == "healthy":
                self.say(f"NOTE {name}: status 100% but cells hold values — healthy")
            elif cells and any(c is not None for c in cells):
                # cells located and all empty with a persistent red 100%
                colcfg.delete_column(page, name)
                raise colcfg.VerificationError(f"{name}: formula errored on rows "
                                  f"(deleted for clean retry); formula={f[:200]!r}")
            else:
                self.say(f"WARN {name}: health check inconclusive "
                         f"(status 100%, cells unreadable) — keeping column")
        self.say(f"column built: {name} (status {status!r})")

    def step_formula_columns_1(self):
        self.formula(
            "Normalize a Domain",
            'Using the column "Website", extract just the bare domain name in '
            'lowercase, like a hostname. Remove the http or https protocol '
            'prefix, remove a leading www dot, and remove the URL path meaning '
            'everything from the first slash character after the hostname '
            'onward, and remove any trailing slash. If "Website" is empty '
            'return an empty string.',
            lambda f, pv: ((sum(bool(re.match(r"^\S+\.[a-z]{2,}$", p)) for p in pv)
                            >= 0.7 * len(pv), "preview")
                           if pv else (("www" in f), "formula")))
        self.formula(
            "Normalize Company Name",
            'Using the column "Company Name", return a normalized version: '
            'convert it to lowercase, remove all punctuation characters, '
            'remove common legal suffixes such as inc, llc, ltd, limited, '
            'gmbh, ag, sa, bv, co, corp, corporation, kg, srl, spa, pty, plc, '
            'lp and llp when they appear as the last word, and collapse '
            'repeated spaces into a single space with no leading or trailing '
            'spaces. If "Company Name" is empty return an empty string.',
            lambda f, pv: ((all(p == p.lower() for p in pv), "preview")
                           if pv else (("gmbh" in f.lower()), "formula")))
        self.formula(
            "Normalized Country",
            'Using the column "Country", map these specific values to '
            'replacements: "Great Britain" becomes "United Kingdom", '
            '"China PR" becomes "China", "Czech Rep." becomes "Czech '
            'Republic", "Türkiye" becomes "Turkey", "USA" becomes "United '
            'States", "United Arab Emirates" becomes "UAE", and "Hongkong, '
            'PR China" becomes "China". Any other value must be returned '
            'unchanged exactly as it is. If "Country" is empty return an '
            'empty string.',
            lambda f, pv: (all(k in f.lower() for k in
                               ("great britain", "china pr", "czech", "türkiye",
                                "usa", "united arab emirates", "hongkong")), "formula"))

    # ----------------------------------------------------- claygents / enrich
    def step_official_domain(self):
        page = self.page
        if colcfg.header_exists(page, "Official Domain"):
            self.say("column exists: Official Domain")
            return
        if colcfg.recover_leftover(page, ("Primary Corporate", "Corporate Domain",
                                     "Verified Corporate"), "Official Domain"):
            self.say("recovered leftover claygent -> Official Domain")
            return
        colcfg.open_card(page, "Claygent", ("Claygent (AI Web Researcher)",),
                    must_not=("Create a column",))
        colcfg.set_model_gpt41mini(page)
        txt = colcfg.fill_prompt(page, OFFICIAL_DOMAIN_PROMPT,
                            [("Company Name: ", "Company Name"),
                             ("Country: ", "Country")])
        if "UNKNOWN" not in txt or "Company Name" not in txt:
            raise colcfg.VerificationError("official domain prompt incomplete")
        colcfg.auto_update_off(page)
        colcfg.add_run_condition(page, "!", "Website")
        colcfg.save_plain(page)
        self.rename_new("Official Domain", "Exhibitors_normalized",
                        ("Primary Corporate", "Corporate Domain", "Verified Corporate"))
        self.screenshot("04_official_domain")
        self.say("Official Domain configured (dormant)")

    def step_company_domain(self):
        self.formula(
            "Company Domain",
            'Using the columns "Normalize a Domain" and "Official Domain", '
            'return the value of "Normalize a Domain" if it is not empty. '
            'Otherwise return the value of "Official Domain" unless it equals '
            '"UNKNOWN", in which case return an empty string. If both are '
            'empty return an empty string.',
            lambda f, pv: (("unknown" in f.lower()), "formula"))

    def step_enrich(self):
        page = self.page
        if colcfg.header_exists(page, "Enrich Company"):
            self.say("column exists: Enrich Company")
            return
        colcfg.open_card(page, "Enrich Company", ("Enrich Company", "Companies, People"))
        colcfg.auto_update_off(page)
        page.get_by_role("button", name="Continue to add fields").click(timeout=15000)
        page.wait_for_timeout(3000)
        search = page.get_by_placeholder("Search data columns")
        if not search.count():
            raise colcfg.VerificationError("enrich field search not found")
        for term in ("Size", "Type", "Domain", "Url", "Founded", "Industry",
                     "Description", "Annual revenue"):
            search.fill(term)
            page.wait_for_timeout(1100)
            for r in page.evaluate(colcfg.MAP_SCAN.replace("[role=\"checkbox\"]",
                                                      "[role=\"switch\"]")):
                lbl = r["label"] or ""
                if lbl.startswith(term) and not lbl.startswith("Logo") \
                        and r["st"] not in ("true", "checked"):
                    page.mouse.click(r["x"], r["y"])
                    page.wait_for_timeout(450)
        search.fill("")
        page.wait_for_timeout(900)
        colcfg.save_plain(page)
        if not colcfg.header_exists(page, "Enrich Company"):
            raise colcfg.VerificationError("Enrich Company column missing after save")
        self.screenshot("06_enrich")
        self.say("Enrich Company configured (dormant)")

    def step_resolved_description(self):
        self.formula(
            "Resolved Description",
            'Using the column "Description (2)" and the column "Description", '
            'return the value of the column "Description (2)" if it is not '
            'empty. Otherwise return the value of the column "Description". '
            'If both are empty return an empty string.',
            lambda f, pv: ((f.count("{{") >= 2), "formula"))

    def step_registrar(self):
        page = self.page
        if colcfg.header_exists(page, "Labs Series Registrar"):
            self.say("column exists: Labs Series Registrar")
            return
        if colcfg.recover_leftover(page, ("Side:", "Classification", "Side"),
                              "Labs Series Registrar",
                              allow=("Classification", "Side")):
            self.say("recovered leftover claygent -> Labs Series Registrar")
            return
        colcfg.open_card(page, "Claygent", ("Claygent (AI Web Researcher)",),
                    must_not=("Create a column",))
        colcfg.set_model_gpt41mini(page)
        txt = colcfg.fill_prompt(page, REGISTRAR_PROMPT,
                            [("Company Name: ", "Company Name"),
                             ("Company Domain: ", "Company Domain"),
                             ("Description: ", "Resolved Description"),
                             ("Primary Offerings: ", None)])
        for marker in ("#CONTEXT#", "#EXAMPLES#", "Forensics & Security",
                       "Non-industry Specific JT Searches"):
            if marker not in txt:
                raise colcfg.VerificationError(f"registrar prompt missing {marker!r}")
        colcfg.auto_update_off(page)   # collapses Configuration, revealing outputs
        if not colcfg.rename_response_output(page, "Side"):
            raise colcfg.VerificationError("response output not found")
        colcfg.add_output(page, "Classification")
        colcfg.save_plain(page)
        self.rename_new("Labs Series Registrar", "Exhibitors_normalized",
                        ("Side:", "Classification", "Side"),
                        allow=("Classification", "Side"))
        self.screenshot("08_registrar")
        self.say("Labs Series Registrar configured (dormant)")

    def step_extractors_and_tiering(self):
        self.formula("Side",
                     'Return the value of the "Side" output field of the "Labs '
                     'Series Registrar" column. If it is empty return an empty string.',
                     lambda f, pv: (("Side" in f), "formula"))
        self.formula("Classification",
                     'Return the value of the "Classification" output field of the '
                     '"Labs Series Registrar" column. If it is empty return an '
                     'empty string.',
                     lambda f, pv: (("Classification" in f), "formula"))
        fit_desc = ('Using the column "Classification", return a single letter '
                    'from this exact mapping. Return "A" when the value is one '
                    'of: ' + ", ".join(f'"{x}"' for x in FIT_LABELS_A) +
                    '. Return "B" when the value is one of: ' +
                    ", ".join(f'"{x}"' for x in FIT_LABELS_B) +
                    '. Return "C" when the value is one of: ' +
                    ", ".join(f'"{x}"' for x in FIT_LABELS_C) +
                    '. The match must be exact including punctuation. If the '
                    'value is empty or not in any list return an empty string.')
        all_labels = FIT_LABELS_A + FIT_LABELS_B + FIT_LABELS_C
        self.formula("Fit", fit_desc,
                     lambda f, pv: ((all(x.lower() in f.lower() for x in all_labels)),
                                    "formula"))
        self.formula(
            "Country Fit",
            'Using the columns "Side" and "Normalized Country", return a '
            'single letter. When "Side" equals "Seller": return "A" if '
            '"Normalized Country" is one of "United Kingdom", "UAE", "Saudi '
            'Arabia", "United States", "Netherlands"; otherwise return "B" if '
            'it is one of "United States", "Germany", "Japan", "China"; '
            'otherwise return "C". When "Side" equals "Buyer": return "A" if '
            '"Normalized Country" is one of "United Kingdom", "UAE", "Saudi '
            'Arabia", "United States", "Netherlands"; otherwise return "B" if '
            'it is one of "Ireland", "Belgium", "France", "Luxembourg", '
            '"Switzerland", "Qatar", "Kuwait", "Bahrain", "Oman", "Jordan", '
            '"Lebanon", "Canada", "Mexico", "Austria", "Czech Republic", '
            '"Denmark", "Finland", "Greece", "Hungary", "Italy", "Norway", '
            '"Poland", "Portugal", "Spain", "Sweden"; otherwise return "C". '
            'If "Side" is empty return an empty string.',
            lambda f, pv: (all(x in f.lower() for x in ("saudi arabia", "netherlands",
                                                        "luxembourg", "czech republic",
                                                        "seller")), "formula"))
        self.formula(
            "Composite Tier",
            'Using the columns "Side", "Fit" and "Country Fit", do the '
            'following. If "Side" does not equal "Seller", return an empty '
            'string. Otherwise concatenate "Fit" and "Country Fit" into a two '
            'letter code and return a number: return 1 if the code is "AA" or '
            '"AB"; return 2 if the code is "AC" or "BA" or "BB"; return 3 if '
            'the code is "BC" or "CA" or "CB" or "CC". If "Fit" or "Country '
            'Fit" is empty return an empty string.',
            lambda f, pv: (all(x in f.upper() for x in ("AA", "AB", "AC", "BA", "BB")) and
                           "seller" in f.lower(), "formula"))

    # --------------------------------------------------------------- tables
    def step_ref_tables(self):
        page = self.page
        for name in ("fit_lookup", "seller_sublevels", "seller_contact_titles",
                     "buyer_contact_titles"):
            if colcfg.table_exists(page, name):
                continue
            path = os.path.join(REF_DIR, name + ".csv")
            colcfg.add_csv_table_robust(page, path)
            self.say(f"ref table imported: {name}")
        colcfg.focus_table(page, "Exhibitors_normalized")

    # ---------------------------------------------------------------- sends
    def step_send_blocklist(self):
        page = self.page
        if colcfg.header_exists(page, "Send to Blocklist"):
            self.say("send exists: Blocklist")
            return
        if colcfg.recover_leftover(page, ("Send table data",), "Send to Blocklist"):
            self.say("recovered unrenamed send -> Send to Blocklist")
            return
        target = blocklist_send.ensure_destination(page, log=self.log)
        self.say(f"blocklist target: {target[-1]}")
        colcfg.open_workbook(page, self.folder)
        colcfg.focus_table(page, "Exhibitors_normalized")
        colcfg.open_send_panel(page)
        colcfg.dest_existing(page, target)
        extras = colcfg.set_mapping(page, {"Company Name", "Company Domain"})
        if extras:
            self.say(f"WARN blocklist mapping kept locked extras: {extras}")
        colcfg.save_via_menu(page, r"Save and run .* rows in this view")
        self.rename_new("Send to Blocklist", "Exhibitors_normalized", ("Send table data",))
        self.screenshot("14_blocklist")
        self.say("Blocklist send created AND run")

    def _send_split(self, side, table):
        page = self.page
        col = f"Send to {table}"
        if colcfg.header_exists(page, col):
            self.say(f"send exists: {col}")
            return
        if colcfg.recover_leftover(page, ("Send table data",), col):
            self.say(f"recovered unrenamed send -> {col}")
            return
        for attempt in range(2):
            colcfg.open_send_panel(page)
            if colcfg.table_exists(page, table):
                colcfg.dest_existing(page, [table])
            else:
                colcfg.dest_create_table(page, table)
            colcfg.set_mapping(page, SEND_KEEP, required=SEND_KEEP - {"Composite Tier"})
            colcfg.add_run_condition(page, "", "Side", f' == "{side}"')
            colcfg.save_via_menu(page, r"Save and don't run")
            try:
                self.rename_new(col, "Exhibitors_normalized", ("Send table data",))
                break
            except colcfg.VerificationError as e:
                if attempt == 0 and "not found after re-navigation" in str(e):
                    self.say(f"WARN {col}: send did not commit — rebuilding")
                    colcfg.open_workbook(page, self.folder)
                    colcfg.focus_table(page, "Exhibitors_normalized")
                    continue
                raise
        self.say(f"{col} created (active, dormant)")

    def step_splits(self):
        colcfg.focus_table(self.page, "Exhibitors_normalized")
        self._send_split("Seller", "Sellers")
        self._send_split("Buyer", "Buyers")

    # -------------------------------------------------- sellers/buyers layer
    def step_sublevel(self):
        page = self.page
        colcfg.focus_table_maybe_empty(page, "Sellers")
        if colcfg.header_exists(page, "Sub Level"):
            self.say("column exists: Sub Level")
            return
        groups = {}
        with open(os.path.join(REF_DIR, "seller_sublevels.csv"),
                  encoding="utf-8-sig") as fh:
            for row in csv.DictReader(fh):
                groups.setdefault(row["Seller Category (Classification)"],
                                  []).append(row["Sub Level"])
        lines = []
        for cat, subs in groups.items():
            lines.append(f"{cat}:")
            lines.extend(f"- {s}" for s in subs)
        head = ("You are the Sub Level classifier for Terrapinn's Lab Live "
                "event series. The company below has already been classified "
                "into the Seller category given in the Classification input. "
                "Choose the single best-fitting Sub Level for this company, "
                "chosen ONLY from the sub-levels listed under that exact "
                "category in the taxonomy below. Use the Description to "
                "decide; if it is insufficient, visit the Company Domain "
                "website. Output ONLY the exact Sub Level label, nothing "
                "else. If none fits well, output the closest one from the "
                "category. Never output a sub-level from a different "
                "category.")
        colcfg.open_card(page, "Claygent", ("Claygent (AI Web Researcher)",),
                    must_not=("Create a column",))
        colcfg.set_model_gpt41mini(page)
        txt = colcfg.fill_prompt(page, head + "\n\nTAXONOMY\n" + "\n".join(lines) +
                            "\n\nINPUTS",
                            [("Company Name: ", "Company Name"),
                             ("Classification: ", "Classification"),
                             ("Company Domain: ", "Company Domain"),
                             ("Description: ", "Resolved Description")])
        if "Forensics & Security" not in txt or "TAXONOMY" not in txt:
            raise colcfg.VerificationError("sub level prompt incomplete")
        colcfg.rename_panel_title(page, "Sub Level")
        colcfg.auto_update_off(page)   # collapses Configuration, revealing outputs
        colcfg.rename_response_output(page, "Sub Level")
        colcfg.add_run_condition(page, "!!", "Classification")
        colcfg.save_plain(page)
        self.rename_new("Sub Level", "Sellers", ("Sub Level", "Sub-Level", "Sublevel"))
        self.screenshot("17_sublevel")
        self.say("Sub Level configured (dormant)")

    def step_sends(self):
        page = self.page
        # Sellers -> Contacts – Sellers (Tier 1-2)
        colcfg.focus_table_maybe_empty(page, "Sellers")
        if not colcfg.header_exists(page, "Send to Contacts") and \
                colcfg.recover_leftover(page, ("Send table data",), "Send to Contacts"):
            self.say("recovered unrenamed send -> Send to Contacts (Sellers)")
        if not colcfg.header_exists(page, "Send to Contacts"):
            colcfg.open_send_panel(page)
            if colcfg.table_exists(page, "Contacts – Sellers"):
                colcfg.dest_existing(page, ["Contacts – Sellers"])
            else:
                colcfg.dest_create_table(page, "Contacts – Sellers")
            colcfg.set_mapping(page, ROUTE_KEEP | {"Sub Level"}, required=ROUTE_KEEP)
            colcfg.add_run_condition(page, "[1,2].includes(", "Composite Tier", ")")
            colcfg.save_via_menu(page, r"Save and don't run")
            self.rename_new("Send to Contacts", "Sellers", ("Send table data",))
            self.say("Sellers route created")
        # Buyers -> Contacts – Buyers (all rows)
        colcfg.focus_table_maybe_empty(page, "Buyers")
        if not colcfg.header_exists(page, "Send to Contacts") and \
                colcfg.recover_leftover(page, ("Send table data",), "Send to Contacts"):
            self.say("recovered unrenamed send -> Send to Contacts (Buyers)")
        if not colcfg.header_exists(page, "Send to Contacts"):
            colcfg.open_send_panel(page)
            if colcfg.table_exists(page, "Contacts – Buyers"):
                colcfg.dest_existing(page, ["Contacts – Buyers"])
            else:
                colcfg.dest_create_table(page, "Contacts – Buyers")
            colcfg.set_mapping(page, ROUTE_KEEP - {"Composite Tier"},
                          required=ROUTE_KEEP - {"Composite Tier"})
            colcfg.save_via_menu(page, r"Save and don't run")
            self.rename_new("Send to Contacts", "Buyers", ("Send table data",))
            self.say("Buyers route created")

    # ------------------------------------------------------- contacts layer
    def step_contacts(self):
        page = self.page
        colcfg.focus_table_maybe_empty(page, "Contacts – Sellers")
        self.formula(
            "Sector Keyword Match",
            'Using the columns "Company Name" and "Resolved Description", '
            'return true if either of them contains any of these keywords '
            'ignoring case: lab, laboratory, life science, pharma, '
            'scientific, analytical, biotech, diagnostics, r&d, research. '
            'Otherwise return false. If both columns are empty return false.',
            lambda f, pv: (all(k in f.lower() for k in
                               ("laboratory", "pharma", "biotech", "research")),
                           "formula"))
        colcfg.focus_table_maybe_empty(page, "Contacts – Buyers")
        for attempt in range(3):
            if colcfg.header_exists(page, "JT Fit"):
                break
            # recover an unnamed leftover from a prior attempt first
            recovered = False
            for cand in ("New Column", "Text", "Untitled"):
                if formula_columns._header_pos(page, cand):
                    try:
                        formula_columns.rename_column(page, cand, "JT Fit")
                        recovered = True
                    except Exception as e:
                        self.say(f"WARN JT Fit recovery rename flaked: {str(e)[:80]}")
                        page.keyboard.press("Escape")
                        page.wait_for_timeout(3000)
                    break
            if recovered or formula_columns._header_pos(page, "JT Fit"):
                continue
            page.keyboard.press("Escape")
            page.wait_for_timeout(800)
            page.get_by_role("button", name="Add column").first.click(timeout=20000)
            page.wait_for_timeout(1500)
            page.get_by_role("menuitem", name="Text", exact=True).first.click(timeout=10000)
            page.wait_for_timeout(2500)
            page.keyboard.type("JT Fit", delay=25)
            page.keyboard.press("Enter")
            page.wait_for_timeout(2500)
            page.keyboard.press("Escape")   # close the inline naming popover
            page.wait_for_timeout(800)
        if not colcfg.header_exists(page, "JT Fit"):
            raise colcfg.VerificationError("JT Fit column not created")
        self.say("JT Fit text column ready")
        # try/finally: this flag selects a different formula template at
        # :315, so leaving it set after a raise silently mis-builds every
        # later formula column on this builder.
        self._contacts_ct = True
        try:
            self.formula(
                "Composite Tier",
                'Using the columns "Fit", "JT Fit" and "Country Fit", concatenate '
                'their values in that exact order into a three letter code. '
                'Return the number 1 if the code is one of: AAA, ABA, BAA, AAB. '
                'Return the number 2 if the code is one of: BAB, BBA, CAB, AAC, '
                'ABB, BBB, ACA, ACB, BAC. Return the number 3 for any other '
                'complete three letter code. If any of the three column values '
                'is empty return an empty string.',
                lambda f, pv: (all(x in f.upper() for x in ("AAA", "ABA", "BAA", "AAB",
                                                            "CAB", "BAC")), "formula"))
        finally:
            self._contacts_ct = False

    # ---------------------------------------------------------- find people
    def _fp_search(self, table, name, desc, min_filters):
        page = self.page
        colcfg.focus_table_maybe_empty(page, table)
        colcfg.open_card(page, "Find people at these companies",
                    ("Find people at these companies", "Source"))
        ta = None
        for i in range(page.locator("textarea").count()):
            el = page.locator("textarea").nth(i)
            try:
                bb = el.bounding_box()
            except Exception:
                continue
            if bb and bb["x"] > 900:
                ta = el
                break
        if ta is None:
            raise colcfg.VerificationError("FP chat textarea not found")
        ta.click(timeout=5000)
        page.keyboard.insert_text(desc)
        page.wait_for_timeout(600)
        ta.press("Enter")
        # wait for sculptor completion
        done = False
        for _ in range(60):                 # sculptor can take minutes under load
            page.wait_for_timeout(3000)
            js = """() => {
              for (const el of document.querySelectorAll('*')) {
                if (el.children.length === 0 &&
                    /Completed search configuration/.test(el.textContent)) return true;
              }
              return false;
            }"""
            if page.evaluate(js):
                done = True
                break
        if not done:
            raise colcfg.VerificationError("FP sculptor never completed")
        js2 = """() => {
          for (const el of document.querySelectorAll('*')) {
            if (el.children.length === 0) {
              const m = el.textContent.trim().match(/^(\\d+) filters$/);
              if (m) return parseInt(m[1], 10);
            }
          }
          return 0;
        }"""
        nf = page.evaluate(js2)
        if nf < min_filters:
            raise colcfg.VerificationError(f"FP filters too few: {nf} < {min_filters}")
        page.get_by_role("button", name="Save search", exact=True).first.click(timeout=10000)
        page.wait_for_timeout(2200)
        inp = None
        for i in range(page.locator("input").count()):
            el = page.locator("input").nth(i)
            try:
                bb = el.bounding_box()
            except Exception:
                continue
            if bb and 600 < bb["x"] < 1100 and 280 < bb["y"] < 330:
                inp = el
                break
        if inp is None:
            raise colcfg.VerificationError("FP save-search name input not found")
        inp.click(timeout=5000)
        page.keyboard.type(name, delay=12)
        page.get_by_role("button", name="Save search", exact=True).last.click(timeout=10000)
        page.wait_for_timeout(3000)
        self.say(f"FP search saved: {name} ({nf} filters)")
        # navigate back into the workbook for the next step
        crumb = page.get_by_role("button", name=self.folder, exact=True)
        if crumb.count():
            crumb.first.click(timeout=10000)
            page.wait_for_timeout(3500)
        else:
            colcfg.open_workbook(page, self.folder)

    def step_find_people(self):
        sellers_desc = (
            "Find people who work at the companies in this table, using each "
            "row's Company Domain. Include people whose job title exactly "
            "matches any of: CEO, Chief Executive Officer, MD, Managing "
            "Director, Managing Partner, President, Vice President, Regional "
            "Vice President, Founder, GM, CCO, CMO, Chief Strategy Officer, "
            "Regional Director, Country Manager, BDM, Executive Director, "
            "General Manager, COO, Chief Operating Officer, Chief Business "
            "Officer, CTO, Chief Technology Officer, Owner. Also include "
            "people whose title contains any of: Business Development, "
            "Channel, Commercial, Distribution, Events, Exhibitions, "
            "Go-to-market, Growth, Healthcare Solutions, Marketing, Partner, "
            "Partnerships, Product, Sales, Strategy, Trade Show, Middle East, "
            "EMEA, MENA, MEA, APAC, Americas. Exclude interns, trainees, "
            "assistants, entry level and freelance roles. Up to 5 people per "
            "company.")
        with open(os.path.join(REF_DIR, "buyer_contact_titles.csv"),
                  encoding="utf-8-sig") as fh:
            distinct = sorted(set(r["Job Title"] for r in csv.DictReader(fh)))
        buyers_desc = (
            "Find people who work at the companies in this table, using each "
            "row's Company Domain. Include people whose job title matches or "
            "contains any of the following: " + ", ".join(distinct) +
            ". Exclude interns, trainees, assistants, entry level and "
            "freelance roles. Up to 5 people per company.")
        try:
            self._fp_search("Contacts – Sellers",
                            f"{self.folder} - Contacts - Sellers titles",
                            sellers_desc, 40)
        except Exception as e:
            self.say(f"WARN Find People (sellers) failed: {str(e)[:160]}")
        try:
            self._fp_search("Contacts – Buyers",
                            f"{self.folder} - Contacts - Buyers titles",
                            buyers_desc, 150)
        except Exception as e:
            self.say(f"WARN Find People (buyers) failed: {str(e)[:160]}")

    # ------------------------------------------------------------------ run
    STEPS = ["step_import", "step_formula_columns_1", "step_official_domain",
             "step_company_domain", "step_enrich", "step_resolved_description",
             "step_registrar", "step_extractors_and_tiering", "step_ref_tables",
             "step_send_blocklist", "step_splits", "step_sublevel",
             "step_sends", "step_contacts", "step_find_people"]

    def run(self):
        t0 = time.time()
        self.step_import()
        colcfg.open_workbook(self.page, self.folder, table=None)
        colcfg.focus_table(self.page, "Exhibitors_normalized")
        for name in self.STEPS[1:]:
            t = time.time()
            getattr(self, name)()
            self.say(f"{name} done in {int(time.time()-t)}s")
        self.say(f"EVENT COMPLETE in {int((time.time()-t0)/60)} min")


def build_workbook(folder, log):
    with browser_session.clay_page() as page:
        b = WorkbookBuilder(page, folder, log)
        try:
            b.run()
        except Exception:
            b.screenshot("FAIL")
            raise


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--folder", required=True)
    args = ap.parse_args()
    build_workbook(args.folder, sys.stdout)
