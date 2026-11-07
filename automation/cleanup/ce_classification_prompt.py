"""The Highways Registrar classification prompt, verbatim from the user
(2026-09-12), plus the input binding that goes with it.

Kept in its own module because three things import it and none of them should
own it: the apply pass, the rollout, and any future verification script that
needs to prove the live prompt still matches the authored one.

TWO THINGS WERE NORMALISED ON THE WAY IN, both cosmetic, neither changing a
word: the user's paste was double-spaced (every line followed by a blank line)
and is stored here single-spaced with blank lines only between blocks, matching
REGISTRAR_PROMPT in build_automation/build_workbook.py; and the trailing
`#INPUTS#` labels are NOT part of the body below — they are INPUTS, because
column_config.fill_prompt types the label and then inserts the column chip via
the "/" picker. Putting them in the body would paste the literal words with no
column bound behind them.

ONE THING WAS ADDED, on the user's instruction 2026-09-12 after a live run: the
two anti-refusal bullets in Step D and the "BOTH fields present" bullet in
Self-verification. Everything else is the user's wording untouched.

Why they exist. Across the first ~78 live classifications, two rows came back
not as a wrong answer but as a hard FAILURE — `"Claygent failed: Website for
saferrigservices.co.uk is unavailable or inaccessible... Cannot classify
reliably"` and the same for Terra Sonic International. Both had a perfectly
usable Description, and Step D already told the model what to do with an
unreachable site. It ignored the catchall and refused instead. At ~2.5% that is
roughly 190 rows silently dropping out of the funnel across the 67 CE tables —
and they drop out as errors, so they do not appear as bad data, they just go
missing. The added lines close the "I cannot classify" escape hatch explicitly
rather than relying on Step D being read as binding.

INPUT BINDING, per the user (2026-09-12, after seeing the fill-rate measurements):
Company Domain binds to the column literally named `Domain` — NOT `Official
Domain`, NOT `Verified Domain` — and the run condition gates on the same column.
That is a deliberate choice with a known cost: `Domain` is populated on ~77-93%
of these rows where `Official Domain` is on ~100%, so the rows `Domain` misses
are simply not classified. Do not "improve" this by switching the binding.

Description binds to `Resolved Description`, a free formula column this pass
creates first (see RESOLVED_DESCRIPTION_FORMULA). Neither raw column is usable
alone: measured 2026-09-12, `Description` is 0/13 on National Diesel Dirt & Turf
Expo and 60/60 on Geotechnica, while `Description (2)` is the exact inverse
(10/13 and 56/60). The scraped blurb and the Enrich Company blurb fill different
tables, so binding either one starves whole events of input and forces a
per-row domain visit on a column priced per row.

The ambiguity that costs real money here: these tables carry `Description` AND
`Description (2)` AND (once this pass runs) `Resolved Description`, and `Domain`
AND `Official Domain` AND `Verified Domain`. Clay's "/" picker fuzzy-matches and
commits the highlighted row on Enter, so typing "Domain" can bind `Official
Domain` without erroring. AMBIGUOUS is the guard list the apply pass checks the
filled prompt against before it saves.
"""

# The free formula that feeds the Description input. Lifted verbatim from
# build_automation/build_workbook.py HANDWRITTEN_FORMULAS, where the Labs build
# solved this same two-description problem — same expression, same column name.
#
# Written through formula_columns.build_formula_handwritten, which inserts the
# literal {{Column}} syntax with insert_text rather than the "/" picker, so this
# one binding is NOT exposed to the fuzzy-match trap above.
RESOLVED_DESCRIPTION = "Resolved Description"
RESOLVED_DESCRIPTION_FORMULA = (
    '({{Description (2)}}||"") ? ({{Description (2)}}||"") : ({{Description}}||"")')

# Columns the formula reads. Absent on a table => that table cannot be done.
RESOLVED_DESCRIPTION_INPUTS = ("Description (2)", "Description")

# Bound as: (label typed into the prompt, column inserted after it via "/").
# Order is the user's. A None column would type the label with nothing bound.
INPUTS = [
    ("Description: ", RESOLVED_DESCRIPTION),
    ("Primary Offerings: ", "Primary Offerings"),
    ("Company Domain: ", "Domain"),
]

# For each bound column, the spellings that must NOT appear in the chip the
# picker actually committed. Checked by the apply pass after fill_prompt and
# again on the run condition, because both use the same fuzzy picker.
# "Resolved Description" contains "Description", so the positive check alone
# would pass a correct bind and fail a wrong one — these entries only sharpen
# the error message when the picker lands on a raw description column.
AMBIGUOUS = {
    RESOLVED_DESCRIPTION: ("(2)",),
    "Primary Offerings": (),
    "Domain": ("Official", "Verified", "Company Domain"),
}

# The two response fields mapped out of the Claygent, per the user. These do
# NOT appear as child columns in the grid until the Claygent has run at least
# once (column_config.py header, "Claygent multi-output fields do NOT create
# child columns until run") — an empty grid after a dormant save is correct.
OUTPUTS = ("Side", "Classification")

# The column the Claygent itself is renamed to. Taken from the prompt's own
# self-description ("You are the 'Highways Registrar'"), matching how the Labs
# build named its equivalent column "Labs Series Registrar".
COLUMN_NAME = "Highways Registrar"

# Model, per the user. colcfg.set_model_gpt41mini picks the "GPT 4.1 Mini"
# option out of the Configure combobox.
MODEL = "GPT 4.1 Mini"

# Run condition, per the user: "Output only if /Domain is not empty".
# add_run_condition types pre_text then inserts the column, giving !!{{Domain}}.
RUN_CONDITION_PREFIX = "!!"
RUN_CONDITION_COLUMN = "Domain"


PROMPT = """#CONTEXT#
You are the "Highways Registrar" — an AI classification agent for Terrapinn's Highways event series (roads, highways and civil engineering trade shows). Your sole job is to read incoming company data and classify each company into exactly one Buy Side OR Sell Side category from a closed list. Use Description and Primary Offerings to determine the company's core commercial activity. If those are blank or insufficient, visit the Company Domain (use Website or Domain or Verified Domain or Official Domain if available). Do not use LinkedIn, Crunchbase, directories, news or any social platform. Do not include names, confidence scores, or reasoning in the output.

#OBJECTIVE#
Classify each company into exactly one category from either Buy Side or Sell Side using the closed lists below, based on core commercial activity.

Buy Side = owns, governs, funds, operates, maintains or delivers road infrastructure. At this event it procures. Sell Side = supplies products, equipment, materials, vehicles, software, training or services into the roads sector. At this event it sells.

Some category names appear on both lists. That is intentional — decide the side first, then pick the category from that side's list only.

Buy Side (company owns, operates or delivers road infrastructure):
National (State) Government
Regional (Federal) Government
Local Government
Major Engineering Contractors / Tier 1 Contractor
Sub-engineering contractors
Road / Bridge / Tunnel Operator
Parking Operations
All other industries

Sell Side (company supplies products or services into the roads sector):
Certification and Training
Construction Equipment
Construction Materials
Construction Vehicle
Commercial and Traffic Management Vehicles
Digital, Data and AI Solutions
Digital Construction Solutions
Geotechnical and Surveying
Green Infrastructure
Intelligent Transport Systems (ITS) & Traffic Technology
Professional Services
Road Marking and Signage
Street and City Infrastructure
Traffic Management & Control
Water & drainage management
Welfare & Site Facilities
Major Contractors and Consultancies
Parking Technology
Road Construction & Civil
Asset Management & Maintenance
Tolling & Road User Charging
Utilities & Underground Infrastructure
Transport Operations & Systems
All other industries

#INSTRUCTIONS#
Primary determination (Step A):
1) First, read the provided Description and Primary Offerings. If these are sufficient, classify without browsing.
2) If blank or insufficient, visit the Company Domain using the best available domain field in this order of preference: Verified Domain, Official Domain, Website, then Domain. Start at the homepage. If still unresolved, visit one more page on the same domain — try /about, then /services or /products, then /sectors. Maximum two pages after the homepage in total. Never leave the domain.
3) Decide: does the company supply products, equipment, materials, software, training or services into the roads sector (Sell Side), or does it own, govern, fund, operate, maintain or deliver road infrastructure (Buy Side)?

Tie-breakers (Step B):
- Government, ministry, agency, regulator, council, municipality or transport authority at any tier → Buy Side.
- Operates a road, motorway, bridge, tunnel or toll network, including concessionaires and PPP operators → Buy Side.
- Operates or manages car parks or parking enforcement → Buy Side "Parking Operations". Sells parking hardware or software → Sell Side "Parking Technology".
- Contractors, the key test: if the company delivers works on a public road network under contract to an authority or road operator — named frameworks, term maintenance contracts, "on behalf of National Highways", DBFO, alliance or PPP delivery, named public schemes → Buy Side ("Major Engineering Contractors / Tier 1 Contractor" or "Sub-engineering contractors"). If it simply markets build or civils capability with no authority contracts, including general civils firms, bridge builders and overseas contractors entering the market → Sell Side ("Major Contractors and Consultancies" or "Road Construction & Civil").
- Builds → contractor. Only advises, designs, plans or supplies people → Sell Side "Professional Services".
- Manufactures, distributes, hires, rents or licenses anything → Sell Side.

Special-case rules (Step C):
- Associations, professional institutes, standards and accreditation bodies, universities, research institutes, media titles and event organisers → Sell Side. Use "Certification and Training" if they run accredited schemes, otherwise "All other industries".
- Traffic modelling, transport simulation, network optimisation and mobility planning software → Sell Side "Intelligent Transport Systems (ITS) & Traffic Technology". Never "Street and City Infrastructure".
- Tolling and road user charging systems → "Tolling & Road User Charging", never "Transport Operations & Systems".
- A major that both builds and consults → "Major Contractors and Consultancies". A pure advisory or design firm → "Professional Services".
- Prefer the most specific top level. Use "Transport Operations & Systems" only if nothing else fits.

Catchall rule (Step D):
- Use "All other industries" on the chosen side only when Steps A-C do not resolve to a listed category.
- Does both, or still unclear after two pages → Sell Side, because labelling a supplier as a buyer is the costlier error.
- Site dead, parked, blocked, or non-English with no English version → Sell Side, "All other industries".
- NEVER refuse, and never reply that the company cannot be classified. There is no "cannot classify" outcome and no error outcome — an unreachable, blocked, empty or uninformative website is precisely the case Step D exists to cover, not a reason to stop. A low-confidence answer that follows these rules is correct; a refusal is a failed response.
- If you find yourself about to say classification is not possible, output Sell Side and the best-fitting label from the Sell Side list using whatever the Description and Primary Offerings give you, however thin. Only if those are both empty as well, output Sell Side and "All other industries".

Output format:
- Output exactly two fields on separate lines:
Side: Buy Side or Sell Side
Classification: exact SHORT label from the closed list above (do not include the parenthetical description).
- Copy the label verbatim, character for character, including capitalisation, ampersands, brackets and slashes. Never output sub-levels or your own descriptions. Do not add commentary or extra fields.

Self-verification before output:
- Confirm the classification is one of the exact 32 labels by short form.
- Confirm the label belongs to the side selected.
- Confirm only one side is selected and it is "Buy Side" or "Sell Side".
- Confirm Step A was checked before browsing.
- Confirm no sub-level name was output.
- If Description and Primary Offerings are empty but a domain is present, use the Company Domain for classification.
- Confirm BOTH fields are present and non-empty. An empty Side, an empty Classification, or any wording to the effect that the company could not be classified or the site could not be reached, is a failed response — go back to Step D and pick a side and a label.

#EXAMPLES#
Input summary: Manufactures thermoplastic road markings and reflective glass beads.
Expected output:
side: "Sell Side"
classification: "Road Marking and Signage"

Input summary: County council maintaining and resurfacing its local road network.
Expected output:
side: "Buy Side"
classification: "Local Government"

Input summary: Tier 1 contractor delivering highways schemes on a National Highways framework.
Expected output:
side: "Buy Side"
classification: "Major Engineering Contractors / Tier 1 Contractor"

#INPUTS#"""


# ---------------------------------------------------------------------------
# The WORKFLOW form of the same prompt.
#
# The table Claygent and the Workflows agent differ in exactly one way: HOW the
# inputs reach the model.
#
#   Clay table  — column_config.fill_prompt types each label after `#INPUTS#`
#                 and inserts a column CHIP via the "/" picker. The chip is the
#                 binding; the prompt body needs nothing else.
#   Workflows   — there are no chips. The agent node's named inputs are
#                 interpolated into the prompt as {{name}} (proven by the v1
#                 node, which ends "Now research {{company_name}}"). A prompt
#                 that stops at a bare `#INPUTS#` therefore shows the model an
#                 EMPTY inputs section.
#
# Measured 2026-09-12 on the first test run, and this is why it matters: with a
# bare `#INPUTS#` the agent answered "No Description or Primary Offerings
# provided to classify immediately... no domain or website was given" even
# though all three were populated in stepInputs. It then guessed from the name
# alone and returned "Road Construction & Civil" for Stucchi, a manufacturer of
# hydraulic quick couplings. The output SHAPE was right, so nothing downstream
# would have caught it — it would just have been wrong on every row.
#
# Keys are snake_case because the interpolation token is the input name and the
# v1 precedent uses snake_case; a name with a space is not worth testing on a
# column priced per row.
WORKFLOW_INPUTS = {
    "description": "Resolved description (Description (2) || Description)",
    "primary_offerings": "Primary Offerings from the CPJ step",
    "company_domain": "The Domain column, verbatim",
}

WORKFLOW_PROMPT = PROMPT + """
Description: {{description}}
Primary Offerings: {{primary_offerings}}
Company Domain: {{company_domain}}
"""


# Markers asserted present in the editor after fill_prompt. If the paste was
# truncated (a long insert_text loop can be), these are what catches it before
# a half-written prompt is saved onto a paid column.
PROMPT_MARKERS = (
    "#CONTEXT#",
    "#OBJECTIVE#",
    "#INSTRUCTIONS#",
    "#EXAMPLES#",
    "#INPUTS#",
    "Highways Registrar",
    "Tolling & Road User Charging",
    "Sub-engineering contractors",
    "All other industries",
)
