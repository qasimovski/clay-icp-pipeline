# Interphex — Pilot Clay Build (paste-ready)

Concrete, per-column build steps for the **Interphex** pilot workbook, derived from
`../terapinn_sellside_clay_build_prompt.md` and the finalized `Labs_Playbook_ICP_-_Tiered.xlsx`
tabs. Formula logic is given as JS-style expressions — Clay's formula editor references
columns via the `/` picker, so adapt the `input["…"]` tokens to Clay's syntax as you paste.

Companion importable reference tables (in this folder):
`fit_lookup.csv` (28) · `seller_sublevels.csv` (253) · `seller_contact_titles.csv` (46) ·
`buyer_contact_titles.csv` (434).

---

## Status before you start

- The **Interphex** workbook already exists in `Labs [2026 - Qasim] → Competitive Events`.
- It now contains three tables: the old-schema **`Exhibitors`** (from `Exhibitors.csv`), **`Speakers`**,
  and the freshly imported **`Exhibitors_normalized`** (602 rows, the new schema with `Description`).
- **⚠️ Credit warning:** Clay shows *"You're approaching your credit limit for this year."* This is
  another reason the paid steps below stay configure-only.

### Two things flagged, not silently resolved

1. **Table name.** The build spec's pipeline lives in a table called `Exhibitors`. To avoid a
   destructive delete (the `_clay_sync` module has a hard no-delete invariant), the normalized data
   was imported as a **separate `Exhibitors_normalized` table** alongside the untouched old one.
   **Build the pipeline below on `Exhibitors_normalized`.** Decide at rollout whether to delete the
   old `Exhibitors` table (needs a new delete capability in the tool) or keep both.
2. **CAB composite-tier discrepancy** (Buyer contacts) — see the flag at the bottom. Do not resolve
   it silently.

---

## The run-now vs configure-only split (the one hard rule)

| Configure only — **do NOT run** (paid per row) | Create **and run** (free) |
|---|---|
| `Official Domain` (Claygent) | `Normalize a Domain`, `Normalize Company Name` (reuse existing) |
| `Labs Series Registrar` — Side/Classification (Claygent) | `Normalized Country`, `Resolved Description` (formulas) |
| `Sub Level` (Claygent, Sellers) | `Fit`, `Country Fit`, `Composite Tier` (formulas) |
| `Enrich Company` action | Send Table Data → Blocklist |
| Find People (Contacts – Sellers / Buyers) | Split actions → Sellers / Buyers; route → Contacts |
| `Website` **only if** it proves to be a paid AI column (verify — see step 4) | `Sector Keyword Match` (formula) |

> Interphex is an exhibitor list of pharma/lab suppliers, so the **Seller** path (and its Seller
> Composite Tier, which resolves fully in this table) is the dominant one here.

---

## Table 1 — `Exhibitors_normalized`

Base columns from import: `Event, Company Name, Profile URL, Booth, Year, Description,
Address Line 1, City, Postal Code, Country, Phone, Email, Website`.

Build in this order:

**1. `Normalize a Domain`** (formula) — reuse the existing workspace formula; do not recreate under
a new name. Source: the imported `Website`. Output used as `normalizedURL`.

**2. `Normalize Company Name`** (formula) — reuse the existing workspace formula.

**3. `Official Domain`** (Claygent — **configure only, do not run**). Runs only when `/Website` is empty.
Prompt:
```
Research and return the verified primary corporate domain for the company below. Use web search to
find candidates, then verify by checking the candidate site's logo, About page, and footer that it
is that company's official site. Exclude social media and directory domains (linkedin.com,
facebook.com, crunchbase.com, etc.). Prefer the global root domain over regional/country subsites.
Return ONLY the bare registrable domain (e.g. "thermofisher.com") — no protocol, no path, no www.
Company Name: {{Company Name}}
Country: {{Country}}
```
Output field: `domain`.

**4. `Website`** (AI Formula) — resolves from `/normalizedURL` if present, else `/domain`.
```js
return (input["normalizedURL"] || "").trim() || (input["domain"] || "").trim();
```
> **Verify in the builder:** does "AI Formula" call a paid model per row here, or compile to a plain
> deterministic formula? If **paid**, move it to the configure-only list. Report which it is.
> **Also flag:** this output shares the name `Website` with the imported raw column — rename the
> resolved one (e.g. `Company Domain`) to avoid a clash, and feed that downstream.

**5. `Normalized Country`** (formula — **create and run**):
```js
const m = {"Great Britain":"United Kingdom","China PR":"China","Czech Rep.":"Czech Republic",
           "Türkiye":"Turkey","USA":"United States","United Arab Emirates":"UAE",
           "Hongkong, PR China":"China"};
const c = (input["Country"] || "").trim();
return m[c] || c;
```

**6. `Enrich Company`** (Clay action — Companies/People/Jobs; identifier = company domain).
**Configure only — do not run.** Columns: size, type, domain, LinkedIn, founded, industry,
description, company specialties, sub-industry, primary offerings; plus derived: business stage,
annual revenue, total funding (range USD), name, website, employee count.

**7. `Resolved Description`** (formula — **create and run**; confirm precedence):
```js
return (input["description"] || "").trim() || (input["Description"] || "").trim();
```
> Precedence = enriched `description` → raw CSV `Description` fallback. Until `Enrich Company` runs
> (paid, deferred), this correctly falls back to the raw `Description` — which is 98% populated for
> Interphex, so the classifier is well fed even without enrichment. **Confirm this precedence.**

**8. Send Table Data → `Labs - Blocklist - Companies`** (**create and run now**, unconditional).
Columns sent: `Company Name`, `Company Domain` (from the resolved `Website`/`Official Domain`).
**Target the Blocklist table one level UP from `Competitive Events`** — not a table inside it.

**9. `Side` / `Classification`** — Claygent **"Labs Series Registrar"** (**configure only, do not run**).
Use the full prompt verbatim from `../terapinn_sellside_clay_build_prompt.md` (lines 170–260).
Input mapping:
- `Company Domain` ← resolved `Website` / `Official Domain`
- `Description` ← `Resolved Description`
- `Primary Offerings` ← `Enrich Company` → *Primary Offerings*
- `Company Name` ← `Company Name`

Outputs two fields: `Side` (Buyer/Seller) and `Classification` (one of the 28 short labels).

**10. `Fit`** (lookup — **create and run**). Import `fit_lookup.csv` as a lookup table and key on
`Classification`. All 28 labels are distinct, so a single table is unambiguous. Inline alternative:
```js
const fit = {"Medical, Diagnostics & Healthcare":"A","Laboratories & Research Organisations":"A",
"Pharma & Biotech":"A","Chemicals, Petrochemicals & Materials":"B","Food & Agriculture":"B","FMCG":"B",
"Environment, Water & Energy":"B","Forensics":"B","Academic":"B","Non-industry Specific JT Searches":"B",
"Government & Public Sector":"C","Investors & Venture Capital":"C",
"Lab Equipment & Instrumentation Suppliers":"A","Laboratory Data, Integration & Connectivity Software":"A",
"Laboratory Automation & Robotics":"A","Testing & Diagnostics":"A","Chemicals & Reagents":"A",
"Digital & AI Services":"B","Material Sciences":"B","Food and Agri tech & monitoring":"B",
"FMCG testing solutions":"B","Environment and energy tech & monitoring":"B","Cleanroom Technology":"B",
"Sustainability":"B","Distributors of lab equipment":"B","Real Estate, Facilities, Architecture":"C",
"Strategic Management Consultants":"C","Forensics & Security":"C"};
return fit[input["Classification"]] || "";
```

**11. `Country Fit`** (formula — **create and run**), branch on `Side`:
```js
const c = input["Normalized Country"], side = input["Side"];
const A = ["United Kingdom","UAE","Saudi Arabia","United States","Netherlands"];
if (side === "Seller") {
  if (A.includes(c)) return "A";
  if (["United States","Germany","Japan","China"].includes(c)) return "B";
  return "C";
}
if (side === "Buyer") {
  if (A.includes(c)) return "A";
  const B = ["Ireland","Belgium","France","Luxembourg","Switzerland","Qatar","Kuwait","Bahrain",
             "Oman","Jordan","Lebanon","Canada","Mexico","Austria","Czech Republic","Denmark",
             "Finland","Greece","Hungary","Italy","Norway","Poland","Portugal","Spain","Sweden"];
  if (B.includes(c)) return "B";
  return "C";
}
return "";
```
> This is the general-purpose (cross-edition) tiering, not a single Lab Live edition — so the Buyer
> T2 set is the combined union, per the spec.

**12. `Composite Tier`** (formula — **create and run**), branch on `Side`:
```js
const side = input["Side"];
if (side !== "Seller") return "";           // Buyer composite deferred — needs contact JT Fit
const k = (input["Fit"] || "") + (input["Country Fit"] || "");
if (["AA","AB"].includes(k)) return 1;
if (["AC","BA","BB"].includes(k)) return 2;
return 3;                                    // BC, CA, CB, CC
```

**13. Split** — two Send Table Data actions (**create and activate now**):
`Side = Seller` → `Sellers`; `Side = Buyer` → `Buyers`. They move no rows until the classifier is
run by a human — expected.

---

## Table 2 — `Sellers`

Receives seller rows with `Classification`, `Fit`, `Country Fit`, `Composite Tier` already attached.
`Classification` already holds the one-of-16 seller category — no "Top Level Category" step needed.

- **`Sub Level`** (Claygent — **configure only, do not run**; only when `Classification` is filled).
  Restrict the option list to the sub-levels under the category `Classification` holds. Full
  253-row taxonomy in `seller_sublevels.csv` (16 categories). Prompt skeleton:
  ```
  Given the company's Classification (a Seller category) and its Description/Primary Offerings,
  choose the single best-fitting Sub Level from the list of sub-levels under that category ONLY.
  Output only the exact Sub Level label. Category: {{Classification}}  Description: {{Resolved Description}}
  Sub-levels for this category: <inject the rows from seller_sublevels.csv where category = Classification>
  ```

- **Route to contacts** (**create and run**): filter `Composite Tier ∈ {1, 2}` → Send Table Data →
  `Contacts – Sellers`. Tier 3 is inbound-only — do not send.

---

## Table 3 — `Buyers`

Receives buyer rows with `Classification`, `Fit`, `Country Fit` attached. `Composite Tier` is null
here (pending contact-level JT Fit). `Classification` already holds the one-of-12 segment.

- **Route to contacts** (**create and run**): Send Table Data → `Contacts – Buyers` (all rows —
  volume is naturally low).

---

## Table 4 — `Contacts – Sellers`

One row per person. **Configure (don't run)** a Find People search per company using
`seller_contact_titles.csv`:
- **24 senior titles**, JT Fit B, Tier 1, no sector filter (16 exact-match + 8 contains-match).
- **22 commercial keywords**, JT Fit C, Tier 2, **must co-occur with a sector keyword**.

Add **`Sector Keyword Match`** (formula — **create and run**): true if the contact's title/company
context contains one of: `lab, laboratory, life science, pharma, scientific, analytical, biotech,
diagnostics, R&D, research`. A Tier-2 (commercial) contact is valid **only if** this is true.
```js
const t = ((input["Job Title"]||"") + " " + (input["Company Name"]||"")).toLowerCase();
const kw = ["lab","laboratory","life science","pharma","scientific","analytical","biotech",
            "diagnostics","r&d","research"];
return kw.some(k => t.includes(k));
```

---

## Table 5 — `Contacts – Buyers`

One row per person. **Configure (don't run)** a Find People search per buyer company, filtered to the
title list for that company's `Classification` segment — `buyer_contact_titles.csv` (434 titles / 12
segments). Add:
- **`JT Fit`**: A = sector-specific keyword · B = senior title only · C = generic commercial
  (Dimension 2 of the Tiering Model tab).
- **`Composite Tier`** (formula — **create and run**) = company `Fit` × contact `JT Fit` × company
  `Country Fit`, per the 27-combination Buyer matrix — **applying the CAB correction below.**

### Buyer composite-tier lookup (with the CAB flag)
The `Tiering Model` tab says **Industry=C, JT=A, Country=B (CAB) → Tier 3**. The applied
`BuySide ICP – TIERED` data shows all 23 CAB rows as **Tier 2**. **These disagree — flagged, not
resolved.** Build to match the applied data for the 8 combinations present in that data:

| Score | Tier |
|---|---|
| AAA, ABA, BAA | 1 |
| BAB, BBA, **CAB** | 2 |
| CAA, CBB | 3 |

For the 19 combinations not present in any row, use the `Tiering Model` tab's stated values.
**Get a human decision on CAB (Tier 2 vs Tier 3) before finalizing this column.**

---

## AS-BUILT (2026-07-07, automated session) — see automation/ for the scripts

The pipeline was built by Playwright automation (persistent worker in `automation/`;
screenshots of every step in `automation/shots/`). Summary of deltas vs. the plan above:

- Built on the `Exhibitors_normalized` table in the **`Labs [2026 - Qasim]`** tree
  (where `_clay_sync` put all event workbooks). **The `Labs [2026 - Qasim - Fable]`
  folder named in CLAUDE.md exists with an EMPTY Competitive Events and its own
  `Labs - Blocklist - Companies`** — two parallel trees; needs a consolidation decision.
- Blocklist send targets `Labs [2026 - Qasim]/Labs - Block List - Companies/Table 1`
  (same tree as the workbook) — ran for all 602 rows.
- The resolved-domain column is named **`Company Domain`** (avoids the `Website` clash).
- `Website` resolution built as a plain deterministic formula (free) — not an AI Formula.
- Claygents are **column-level** (GPT 4.1 Mini, 1/row), not saved workspace Claygents;
  the automation scripts are the reuse mechanism. `Side`/`Classification` are extractor
  formulas reading the Registrar's output fields.
- Enrich Company (native, no cost badge): 11 fields ON. **Picker lacks**: Specialties,
  Sub-Industry, Primary Offerings, Business Stage, Total Funding. `Primary Offerings`
  is therefore blank in the Registrar inputs (its rule 7 covers this).
- Find People = two **saved searches** ("Interphex - Contacts - Sellers titles" 53
  filters; "Interphex - Contacts - Buyers titles" 190 filters, distinct union of the
  434-row segment list — per-segment refinement must be applied at run time).
- Send mappings: `Size`/`Industry` (and on routes `Phone`/`Country`) could not be
  unchecked (destination-locked) — harmless extras. Sellers route also lacks `Sub Level`
  (dormant Claygent outputs aren't offered; Auto-extract new columns is ON).
- `Sector Keyword Match` is company-context (Company Name + Resolved Description) until
  Find People supplies a Job Title column. `Contacts – Buyers` has a manual `JT Fit`
  Text column + contact-level `Composite Tier` formula (CAB→2 per applied data,
  **CAB discrepancy still needs a human decision**; combos absent from both tabs
  default to Tier 3).

## What was actually done vs. what remains

**Done in this session (free ops only):**
- Extended `_clay_sync` (`--normalized` flag) and imported `Exhibitors_normalized` (602 rows) into
  the Interphex workbook — non-destructively, alongside the old `Exhibitors` table.
- Generated the 4 reference CSVs in this folder.

**Remains for you (Clay builder — no paid step gets triggered):**
- Build columns 1–13 on `Exhibitors_normalized`, the Sellers/Buyers/Contacts tables, the reference
  lookups, the Blocklist send, and the split/route actions. Run only the free ones.
- Configure (don't run) the three Claygents, `Enrich Company`, and both Find People searches.

**Open questions to confirm:** (1) `Website` AI Formula paid or free? (2) `Resolved Description`
precedence OK? (3) resolved-`Website` name clash — rename? (4) CAB → Tier 2 or 3? (5) at rollout,
delete the old `Exhibitors` table or keep both?
