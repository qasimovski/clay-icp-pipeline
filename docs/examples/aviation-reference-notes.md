# Aviation Clay Project — Pipeline Findings (read-only inspection, 2026-07-04)

> Kept here as evidence this pipeline shape generalizes across verticals,
> not just Labs. Original notes are from a read-only inspection of a
> colleague's aviation-vertical Clay project, done to learn the post-import
> pattern before replicating it for Labs. **Zero edits/runs/deletes were
> made** — navigation, column-config viewing, and screenshots only.

## Structure

```
Aviation [...] project/
├── Competitive Events/            ← 32 workbooks (one per event) + 7 event subfolders
│   └── <Event> workbook, tables:
│       ├── Speakers 2026          (imported CSV, person-level)
│       ├── Exhibitors 2026        (imported CSV, company-level)
│       ├── Find People - Sell Side  (generated from Find People searches)
│       └── Find People - Buy Side   (generated from Find People searches)
├── Industries / Products & Services/
├── Other Sources/
├── Aviation Block-List (Temp)     ← workbook: Managing Directors, Table - 1,
│                                     Table 2, Table 3, Table 4 - New, Table 4 - New (2)
└── Large Companies - Exclusion    ← workbook: "Companies" table, 457 rows
                                      (Company Name, Domain)
```

Representative workbook inspected: **Aircraft Interiors Expo (AIX)**.

## Exhibitors table — the columns added after import (36 total, 13 hidden)

Imported data: Details Link, Company Name, Website, Description, Exhibitor
Name & Website variants. Added processing columns, in pipeline order:

1. **Normalize Company Name** — Clay Formatters → "Normalize Company Name"
   (free; strips Inc/LLC/GmbH). Auto-run, no condition.
2. **Normalized Name** — plain Text column referencing `normalized_name`.
3. *(hidden)* **Find company domain** — Clay's domain-finder action;
   intermediate hidden columns: Domain, Root Domain, Response, Company Domain,
   **Normalize a Domain** (formatter).
4. **Final Company Domain** — URL column referencing `normalizedUrl`.
5. **Buyer/Seller Classification** — **Claygent (Web research), OpenAI GPT-4.1
   Mini on the shared "Clay OpenAI" account**. Full prompt (aviation version):
   - Visit {{Final Company Domain}}, determine what {{Company Name}} does.
   - Test: flies aircraft → Buyer; owns/runs airport → Buyer; government
     body/regulator → Buyer; airline/airport trade association → Buyer;
     otherwise → Seller. "No exceptions" — vendors selling ONLY to airlines
     are still Sellers.
   - Rules: 3-letter names are acronyms not IATA codes; website says
     "solutions/platform/services for airlines" → Seller; **if uncertain →
     Seller**; return ONLY "Buyer" or "Seller".
   - Few-shot examples (Emirates→Buyer, Amadeus→Seller, SITA→Seller...).
6. **Classification** — plain Text column materializing the Claygent `response`.
7. **Enrich Company** — Clay "Enrich Company" action, input = Final Company
   Domain. Outputs feed the read-only firmographic columns: Size, Type,
   Company LinkedIn, Founded, Industry, Description (2), Annual Revenue,
   Total Funding, Business Stage, Specialities, Sub-Industry, Primary Offering.
8. **Seller JT - Exact / Seller JT - Contains / Buyer JT - Contains** — three
   "Update People Table" link columns. Each ties this company table to a saved
   **Find People search** keyed on Final Company Domain. These populate the
   Find People - Sell/Buy Side tables. ⚠️ Running one triggers a FULL
   people-search refresh across all companies.

## Speakers table (person-level import), 17 columns

Full/First/Last Name, Job Role, LinkedIn Profile URL → **Enrich person**
(run condition: `!!{{LinkedIn URL}}` — only if LinkedIn URL not empty) →
Company Name → Normalize Company Name → Normalized Name → Final Domain →
**Buyer/Seller Classification** (same Claygent) → Classification →
**Enrich Company** → **Lookup in Audiences** → Total Records →
**Send table data** (destination "Table 2", 1 of 15 columns, run condition
LinkedIn URL not empty).

## Find People - Sell Side (5,814 rows; 4,835 pass filters)

Sources (2 Find People searches): "Executive Leadership, Global" and
"Aviation Business Development Leads". Columns:

- First/Last/Full Name, Job Title, Location, Company Domain, LinkedIn Profile
- **Managing Director Checkbox** — free JS formula:
  `{{Job Title}}?.toLowerCase()?.includes("managing director")`
- **Lookup in Audiences** — Clay Labs action; checks Clay Audiences by
  LinkedIn URL; **Total Records** = match count
- **Lookup Single Row in Other Table** — against table "Aviation - new BLOCK
  list", match column = LinkedIn Profile → No Record Found = not blocked
- **Lookup Single Row in Other Table (2)** — against master "Table 2",
  match = LinkedIn Profile → not already sent from another event
- **Send table data** — destination "Table 2", sends ONLY LinkedIn Profile

**The gate is the VIEW FILTER (4 conditions), not run conditions:**
1. Total Records equal to 0 (not already in Audiences)
2. Block-list lookup has no results
3. Master-table lookup has no results (cross-event dedupe)
4. Managing Director Checkbox is not checked

## Find People - Buy Side (leaner)

First/Last/Full Name, Job Title, Location, Company Domain, LinkedIn Profile,
Lookup in Audiences, Total Records, Send table data (→ "Table 2").
**No blocklist lookup, no MD checkbox** — blocklist only gates Sell Side.

## Blocklist mechanism (replicated for Labs — see `docs/PIPELINE_ARCHITECTURE.md`)

Three separate exclusion layers, all free (no credits):
1. **A blocklist table** — a plain Clay table of LinkedIn profile URLs;
   every Sell-Side table has a "Lookup Single Row in Other Table" column
   against it + a view filter "has no results".
2. **A large-companies exclusion table** — for excluding giant companies
   (Company Name, Domain).
3. **A master registry table** — receives every sent row's LinkedIn Profile
   via "Send table data"; every event table looks it up first, so a person
   is only ever exported once across all events.

Additionally "Lookup in Audiences" dedupes against contacts already in Clay
Audiences (i.e., already in campaigns).

## Credit-consciousness patterns observed

- Formatter/formula columns (normalize, checkbox) are free; used liberally.
- Claygent classification uses the cheapest model (GPT-4.1 Mini) with a
  tightly-scoped prompt, single-word output, and a "Max Cost" setting.
- Enrichments carry run conditions (`only run if LinkedIn URL is not empty`).
- Dedupe/blocklist lookups are free table lookups; they run BEFORE the send,
  and the send exports only the minimal key (LinkedIn Profile).
- Person/company enrichment happens once per table; the Find People searches
  do the heavy lifting of surfacing contacts (Find People rows are cheaper
  than per-row enrichment waterfalls).

## Why this matters for extending this repo to a new vertical

This is a second, independently built vertical using the same shape as the
Labs pipeline in this repo: normalize → find domain → classify Buyer/Seller
→ enrich → split → find people → blocklist-gated send. It confirms the
pipeline in `docs/PIPELINE_ARCHITECTURE.md` is a genuine cross-vertical
pattern, not something specific to Labs — the main things that changed
between Aviation and Labs were the classifier prompt/taxonomy and the
job-title lists, exactly the pieces `config/icps/<icp>/icp.yaml` is meant
to hold for a new ICP.
