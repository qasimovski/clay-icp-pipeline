# Clay ICP Pipeline — Reusable Template

Turns scraped trade-show CSVs into tiered, scored Clay contact lists via a
config-driven ICP scoring pipeline. Extracted from the **Labs Tiered ICP**
build — two entity-type pipelines (Exhibitors, Sponsors) built out across
~60 event workbooks (81 live Clay tables) — and generalized so a new entity
type (e.g. Speakers) or a whole new vertical (e.g. a Solar Tiered ICP) means
filling in config, not re-deriving the pipeline.

## How the pieces fit together

```
raw CSV (per event, per entity type)
   │
   ▼
normalize domain / company name / country  ──┐
   ▼                                          │  identical mechanics
Enrich Company (Clay native action)           │  across entity types
   ▼                                          │  and ICPs
ICP classifier (Claygent: Side + Classification) ◄── ICP-specific taxonomy
   ▼                                          │
Fit / Country Fit / Composite Tier  ──────────┘  ICP-specific tier lists
   ▼
split → Sellers / Buyers tables → Contacts tables (Find People)
   ▼
Blocklist send (cross-event dedupe registry)
```

- `docs/GTM_METHODOLOGY.md` — the engine: why this exists, the
  BuySide/SellSide scoring shape, the credit-minimization funnel. Read
  this first.
- `docs/PIPELINE_ARCHITECTURE.md` — the concrete 15-step column pipeline
  built inside every event workbook, with pointers to which config file
  governs each ICP- or entity-specific step.
- `template/BUILD_PROMPT.template.md` + `template/render_build_prompt.py`
  — the per-event build spec, generated from config rather than
  hand-copied per entity type/ICP.
- `config/entity-types/*.yaml` — what changes per **data source**
  (Exhibitors vs Sponsors vs a future Speakers): raw columns, field names,
  destination table names.
- `config/icps/*/icp.yaml` — what changes per **vertical** (Labs vs a
  future Solar): the classification taxonomy, fit lists, country tiers.
- `automation/` — the Playwright scripts that actually built the live Labs
  tables, kept as reference/manual-run code (not config-driven this round
  — see `automation/README.md`).

## Quickstart: render a build spec for an existing entity type + ICP

```bash
pip install -r requirements.txt
cp config/local.yaml.example config/local.yaml   # fill in your Clay workspace details
python template/render_build_prompt.py --entity exhibitors --icp labs --out /tmp/spec.md
```

Paste the rendered spec into a Clay-aware coding agent (or follow it by
hand) against the target event's workbook. Follow the run-now-vs-
configure-only rule embedded in the spec: formulas and Send Table Data
actions get created *and run*; anything that calls a paid provider
(Claygent columns, Enrich Company, Find People) gets configured *but not
run* until a human explicitly triggers it.

## Adding a new entity type (e.g. Speakers)

1. Get one real normalized CSV for the new entity type and read its actual
   header row — don't assume it matches Exhibitors or Sponsors.
2. Copy `config/entity-types/_template.yaml` → `config/entity-types/speakers.yaml`
   and fill in: `source_csv`, table names, `raw_columns`, the country/
   description/website source field names, `enrich_company_fields`,
   `gating`, and `composite_tier.seller_mode`.
3. Render: `python template/render_build_prompt.py --entity speakers --icp labs`.
4. Build per `docs/PIPELINE_ARCHITECTURE.md` — the pipeline mechanics don't
   change; only the config does.

## Adding a new ICP (e.g. Solar Tiered ICP)

1. Get the vertical's ICP playbook (or equivalent source-of-truth
   spreadsheet) and pull the taxonomy/tiers from it — don't invent
   categories from scratch.
2. Copy `config/icps/_template/icp.yaml` → `config/icps/solar/icp.yaml` and
   fill in: `classifier.taxonomy` (Buyer/Seller categories + Fit letters),
   `classifier.tie_break_rules`, `country_normalization`, `country_fit`
   tier lists, and the composite-tier matrices.
3. Write `config/icps/solar/context.md` (editions, per-edition country
   lists, and any vertical-specific knowledge-base notes — see
   `config/icps/labs/context.md` for the shape).
4. Populate `config/icps/solar/lookups/` locally from the real ICP
   workbook (gitignored — see `config/icps/labs/lookups/README.md` for the
   pattern, and `docs/SENSITIVE_DATA.md` for why these stay local).
5. Render and build the same as an existing ICP.

## Known limitations

- **CAB tiering discrepancy** — an unresolved conflict between the Labs ICP
  workbook's stated tiering model and its actually-applied tier values.
  Flagged, not silently resolved. See `docs/KNOWN_ISSUES.md` #1.
- **Sponsors-replica drift** — some live Sponsors tables built during the
  original rollout are missing conditional gating (`Official Domain`,
  fan-out actions) and pass through a narrower `Enrich Company` field set
  than the mature Exhibitors build. This template is authored to the
  corrected/mature behavior; the already-live tables need a separate audit
  pass. See `docs/KNOWN_ISSUES.md` #2–4.
- **Automation is reference-only** — `automation/` scripts are not yet
  parameterized by entity-type/ICP config; building a new pipeline still
  means a human following the rendered build spec inside the Clay UI. See
  `automation/README.md`.

## Sensitive data / what's not in this repo

This repo holds the process, not the data. See `docs/SENSITIVE_DATA.md`
for exactly what's excluded (real scraped CSVs, the real ICP playbook
workbook, real lookup CSVs, your Clay workspace identifiers) and how to
supply it locally.
