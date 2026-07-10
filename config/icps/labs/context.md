# Labs Tiered ICP — vertical-specific context

> This is the Labs-specific residue split out of `docs/GTM_METHODOLOGY.md`.
> Read that file first for the generic shape; this file has the actual
> editions, country lists, and knowledge-base pointers for this vertical.

## The project

Terrapinn runs the **Lab Live event series** — conference + exhibition
events covering the whole life cycle of a laboratory. Editions:

| Edition | Location | Home country |
|---|---|---|
| London Lab Live | London, UK | United Kingdom |
| ARABLAB Live | Dubai, UAE | UAE |
| SaudiLab Live | Saudi Arabia | Saudi Arabia |
| Future Labs USA | United States | United States |
| Future Labs Europe | Amsterdam, NL | Netherlands |

## Per-edition country tiers (critical — changes per edition)

The "A / home" and "B / travel-range" country lists change with each
edition, and are applied *at build time* in Clay when you need a
single-edition list rather than the general-purpose cross-edition union in
`icp.yaml: country_fit`:

- **London Lab Live** → home UK; T2: Ireland, Belgium, France, Netherlands, Luxembourg, Switzerland
- **ARABLAB Live** → home UAE; T2: Saudi Arabia, Qatar, Kuwait, Bahrain, Oman, Jordan, Lebanon
- **SaudiLab Live** → home Saudi Arabia; T2: UAE, Qatar, Kuwait, Bahrain, Oman, Jordan
- **Future Labs USA** → home USA; T2: Canada, Mexico
- **Future Labs Europe** → home Netherlands; T2: most of Europe **except Germany** — never build German buyer data for this edition

## The knowledge base: the ICP workbook

Source of truth: `Labs_Playbook_ICP_-_Tiered.xlsx` (not committed — see
`docs/SENSITIVE_DATA.md`). Sheets, and what each is for:

| Sheet | Role | Act on it? |
|---|---|---|
| Change Log for GTM | Read first. Explains the whole tiering system, editions, country framework, composite-tier rules. | Reference |
| Tiering Model | Full scoring logic for BuySide (3 dimensions) and SellSide (2 dimensions) + the composite-tier lookup tables. | Reference |
| BuySide ICP – TIERED | Every buyer job-title row, pre-scored, with a final Tier (1/2/3) and a GTM instruction. | Build from this |
| SellSide P&S – TIERED | Every product/service sub-category, pre-scored, with a final Tier + instruction. | Build from this |
| Seller JTs – TIERED | Job-title keywords to target inside exhibitor companies, tiered by seniority. | Build from this |
| Derived Function from ICP | Lookup mapping job title → functional area / sub-area. For enrichment/categorisation later. | Reference |
| Clay Tables | The tracker: every Clay table built, its link, quantity, promo type, and status. | Log here |
| Competitor | Directory of competitor events (name, focus, location, URL, what data exists). Source of the CSVs. | Reference |

**Golden rule:** the tiers are already decided by the Head of GTM. The
engineer does not re-score anything. The Tier column *is* the instruction —
follow it exactly.

## Seller job titles (company-level context, not classifier taxonomy)

Senior decision-maker titles (CEO, MD, VP, Founder, Country Manager, BDM…)
= Tier 1. Commercial titles (Sales, Marketing, BD, Events, EMEA/APAC…) =
Tier 2 **only if a sector keyword is also present** (`lab / laboratory /
life science / pharma / scientific / analytical / biotech / diagnostics /
R&D / research`).

## Workspace

Clay workspace: see `config/local.yaml` (gitignored) for the live URL/name —
this is account-specific, not part of the reusable ICP config.
