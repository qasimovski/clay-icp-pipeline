# GTM Methodology — the engine behind this pipeline

> This is the ICP-agnostic "why" behind the pipeline. It applies whether the
> vertical is Labs, Solar, Aviation, or anything else. Vertical-specific
> content (editions, country lists, the actual taxonomy) lives in
> `config/icps/<icp>/context.md` and `config/icps/<icp>/icp.yaml` — read
> this file first, then the ICP-specific one.

## 1. What this process is for

Terrapinn runs event series (conferences + exhibitions) and wants two
audiences out of every competitor event's public data:

- **BuySide (Buyers)** — people who attend events like this to *buy*:
  practitioners, procurement, technical/operations roles at companies that
  consume the vertical's products or services.
- **SellSide (Sellers)** — companies that exhibit/sponsor to *sell*:
  vendors, suppliers, service providers in the vertical.

The output is Clay tables of these two audiences, scored and tiered so
outbound effort concentrates on the best-fit, most reachable rows first.

## 2. The knowledge base pattern

Each ICP has a single spreadsheet as its source of truth (e.g. a
`<Vertical>_Playbook_ICP_-_Tiered.xlsx`), with sheets that generally follow
this shape:

| Sheet role | Purpose |
|---|---|
| Change log / scoring explainer | Explains the tiering system, editions, country framework, composite-tier rules. Read first. |
| Tiering model | Full scoring logic for BuySide (3 dimensions) and SellSide (2 dimensions), plus composite-tier lookup tables. |
| BuySide ICP — tiered | Every buyer job-title row, pre-scored, with a final Tier and GTM instruction. |
| SellSide categories — tiered | Every product/service sub-category, pre-scored, with a final Tier + instruction. |
| Seller job titles — tiered | Job-title keywords to target inside exhibitor/sponsor companies, tiered by seniority. |
| Tracker | Every Clay table built: link, quantity, promo type, status. |
| Competitor directory | Competitor events: name, focus, location, URL, what data exists — source of the CSVs. |

**Golden rule:** the tiers are already decided by whoever owns the ICP. The
engineer building the pipeline does not re-score anything — the Tier column
*is* the instruction, and it's followed exactly.

## 3. How tiering works (so the rules aren't a black box)

Each row carries a composite score that resolves to a Tier.

**BuySide — 3 dimensions, combined into a 3-letter code (e.g. `AAA`, `BBA`):**
1. **Industry/Category Fit** — Core (A) / Adjacent (B) / Peripheral (C)
2. **Job-Title Fit** — vertical-specific keyword (A) / senior-only (B) / generic commercial (C)
3. **Country Fit** — event home country (A) / travel-range (B) / elsewhere (C)

**SellSide — 2 dimensions (e.g. `AA`, `AB`):**
1. **Category Fit** — Core (A) / Adjacent (B) / Peripheral (C)
2. **Country Fit** — home country (A) / global hub countries (B) / elsewhere (C)

The exact category/keyword lists and country lists are ICP-specific — see
`config/icps/<icp>/icp.yaml`.

**The Tier → action mapping (the traffic light) — same across ICPs:**

| Tier | Action |
|---|---|
| **Tier 1** | Mass build. No volume cap. Home (T1) + travel-range (T2) countries. |
| **Tier 2** | Build, narrower. Target the T2 travel-range countries for that edition. |
| **Tier 3** | Inbound only. Do not proactively build. |

## 4. Country lists are per-edition

The "A / home" and "B / travel-range" country lists typically change per
event edition and are applied at build time in Clay. See
`config/icps/<icp>/context.md` for the concrete lists for a given ICP.

## 5. The input data: scraped competitor CSVs

Competitor event URLs are scraped into per-event CSVs. Each event yields
some subset of these CSV types:

| CSV type | Feeds | Typical promo tag |
|---|---|---|
| Sponsors | Seller list | SPEX |
| Exhibitors | Seller list (primary) + a secondary Buyer list from buy-side titles inside exhibitor companies | SPEX (+ CONF) |
| Attendees | Buyer list | CONF |
| Speakers | Buyer list | CONF |

**Policy caveat carried over from the Labs build:** BuySide data is not
meant to be sourced from competitor shows' *attendee* lists (assumed
non-public); if scraped attendee data exists, flag Buyer builds sourced
from it for human sign-off before spending credits. Seller lists (from
exhibitor/sponsor data) are the safe, unambiguous work.

## 6. The core objective: minimize Clay credits

**Clay charges credits for enrichment and AI classification — not for
import, text filters, or formulas.** Remove every irrelevant row using free
operations first; only spend credits on the survivors deleted before
enrichment is a credit never spent.

### The funnel (cheapest, highest-kill gates first)

**Stage 1 — Clean the CSV before import (free, biggest lever)**
- Deduplicate across events (same company/person recurs across shows).
  Dedupe on email / LinkedIn URL / company+name.
- Drop rows missing must-have fields (no name / company / usable job title).
- Normalize country + job-title text so later filters match ("UK" = "United Kingdom").

**Stage 2 — Split Seller vs Buyer, then free filters (zero credits), in this order:**
1. **Country gate** — apply the edition's T1/T2 list; drop Tier-3 countries.
   (Highest-volume kill, free text match → do first.)
2. **Seniority exclusion** — drop Intern / Trainee / Assistant / Entry / Freelance.
3. **Job-title keyword match** — text `contains` against the ICP keyword
   lists. No keyword hit → drop.

**Stage 3 — Classify & tier (keep paid AI to a minimum)**
- Compute the Tier with free conditional formulas wherever the inputs are
  already known (job-title keyword + country + known category).
- Use Claygent / AI classification only for genuinely ambiguous rows (e.g.
  is this company A-Core or B-Adjacent from its description). AI = credits
  → last resort.
- Apply the sector-keyword filter to Seller commercial titles here (still a
  free text filter).
- **Classify before enriching**, so enrichment only ever touches Tier 1 /
  Tier 2 survivors.

**Stage 4 — Enrich last, and conditionally (this is where credits go)**
- Set every enrichment column to "only run if" Tier ∈ {1,2} and the field is
  missing. Clay skips credits on rows that fail the condition.
- Use waterfall, cheapest-provider-first for contact/email enrichment; the
  next provider only fires if the previous returns nothing.

**Stage 5 — Log** the finished table in the tracker sheet (link, quantity,
promo, status) so no source is rebuilt twice.

**Shorthand:** `dedupe → country → seniority → keyword → tier → then enrich.`

## 7. What "a job well done" looks like

- Irrelevant rows are removed with free operations before any credit is spent.
- Enrichment runs only on Tier 1 / Tier 2, in-country, senior, keyword-matched rows.
- Seller commercial-title rows carry the sector-keyword filter (no stray salespeople).
- Every build is logged in the tracker; no duplicated spend across events.
- Attendee-derived Buyer builds are confirmed with a human before credits are spent.

## 8. Quick glossary

- **ICP** — Ideal Customer Profile.
- **SPEX** — sponsorship/exhibition (a Seller list).
- **CONF** — conference/delegate (a Buyer list).
- **T1 / T2 / T3** — country tiers (home / travel-range / elsewhere), per edition.
- **Tier 1/2/3** — the composite action tier (mass build / narrow build / inbound only).
- **Claygent** — Clay's AI research/classification agent (costs credits).
- **Waterfall** — chained enrichment providers, cheapest first, stop on first hit.
- **Event** — a competitor trade show. One Clay **workbook** per event; in code,
  "workbook" is always used for the Clay object, "event" for the show itself.
- **Pass** — one fleet-wide automation run over the event workbooks
  (`automation/cleanup/`: `<verb>.py` = one workbook, `<verb>_rollout.py` = fleet).
- **Step** — one column/action inside a table (the 14 steps in
  `docs/PIPELINE_ARCHITECTURE.md`). A pass builds one or more steps across the fleet.
- **Rollout** — the fleet driver for a pass (resumable, sharded, `--only`/`--limit`).
- **Marker column** — a column whose presence proves a Clay template was already
  applied; the idempotency check every pass runs before touching a workbook.
- **Run condition** — Clay's "Only run if" setting on an enrichment column
  (Run settings); gates paid columns so they only fire on qualifying rows.
- **Blocklist table** vs **blocklist ledger** — two different systems: the shared
  Clay *Blocklist table* (a Send Table Data destination used for downstream dedupe)
  and the Supabase *blocklist ledger* (`blocklist_ledger/`, an HTTP API enrichment
  that gates paid columns inline via `Is New`).

See also: `docs/examples/aviation-reference-notes.md` — a second, independently
built vertical (Aviation) that follows this exact same shape (normalize →
find domain → classify Buyer/Seller → enrich → split → find people →
blocklist-gated send), which is the proof this methodology generalizes
rather than being Labs-specific.
