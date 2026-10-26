<!--
TEMPLATE for recheck.py agents. This copy is the real Mining V2 brief (Oct 2026). For a new ICP:
  * replace everything between "## The rules" and "## Input / output" with THAT workbook's tabs,
    word for word (sub-levels, buyer groups and their own conditions, the OUT-OF-ICP list);
  * keep the reason codes in step with scope.yaml `recheck.out_codes`, and the extra verdicts
    with `recheck.extra_verdicts`;
  * show the user the filled brief and get a yes before any agent runs.
Everything in <ANGLE BRACKETS> is filled per run.
-->

# Re-check: does each company meet the <ICP NAME> **<VERSION>** rules?

Working directory: `<RUN FOLDER>`.

Each row of your batch is one company, with the evidence we hold (`web_evidence`: today's homepage).

- **A row with a `current_label`** was typed by an earlier run under older, wider rules and mapped
  to a new label. The `why_queued` column says why it is re-checked. Judge it against the rules below,
  word for word, and give one verdict: `KEEP`, `RELABEL`, `OUT` (or an extra verdict this brief
  allows, e.g. `RENTAL`). The old type is one piece of evidence, not the answer.
- **A row with no `current_label`** was never typed. Give it a label from the rules below
  (`CLASSIFIED`), or `OUT` with a reason code, or `UNKNOWN` when the evidence really doesn't say
  what the company does. An UNKNOWN is not searched for people, so it costs nothing; don't turn a
  guess into a label to avoid it.

## The rules (Mining example: from "Mining Playbook ICP.xlsx", V2 tabs)

### Sell side (Products_Services v2)
**Rule:** in the ICP only if the company **SELLS equipment, technology or services to mines and
quarries**.

| Classification | Its V2 sub-levels |
|---|---|
| Heavy Equipment | Excavators; Dump trucks; Wheel loaders; Dozers; Graders; Underground haul trucks; Drilling rigs; Roof bolters; Mobile crushers; Mobile screens; Fixed crushing plants; Screening systems; Conveyors |
| Processing Technology | Comminution; Crushing; Grinding Mills; Screening; Classification Systems; Flotation; Separation; Gravity Separation; Magnetic Separation; Filtration; Dewatering Equipment; Material Handling & Conveying; Feeders & Hoppers; Process Control & Automation; Plant Optimisation Software |
| Production & Material Handling Solutions | Conveying; Transfer; Feeding; Bulk Material Handling; Stockpiling; Storage; Loading; Unloading; Integrated Material Handling; Dry Bulk Handling |
| Mining & Technical Consulting | Geology & exploration services; Mine engineering & design; Environmental & social impact assessments |
| Automation & Digital Technologies | Fleet Management; Dispatch; Equipment Tracking; Mine Planning Software; Geological Modelling Software; Scheduling & Optimisation Software; IoT Sensors; Condition Monitoring; Remote Monitoring; Autonomous Haulage; Remote Operations Centres; Data Analytics Platforms; AI Optimisation; Predictive Maintenance; Drones; Aerial Vehicle technology; Aerial Mapping; Surveying Technology |
| Water & Waste Management | Tailings Management; Dewatering Systems; Thickening & Filtration; Slurry Handling; Water Treatment; Water Recycling; Effluent Treatment; Dust Suppression; Dust Control; Mine Water Management; Water Infrastructure; Pumping Systems; Environmental Management Systems; Waste Management Solutions |
| Power Solutions | Diesel Power Generators; Gas Power Generators; Temporary & Rental Power; Hybrid Power; Solar Hybrid; Renewable Energy Systems; Microgrids; Battery Energy Storage; Energy Storage; Off-Grid Power; Remote Site Power; Energy Management; Power Optimisation |
| Distributors & Dealers | Regional distributors and authorised dealers; Spare parts and aftermarket service providers; Importers and exporters of mining equipment; Value-added resellers, agents and channel partners. *"They sell to mines. Contacts must work in the mining team."* Keywords: authorised dealer; distributor of mining equipment; spare parts; aftermarket; mining consumables; importer/exporter; mining equipment trading; reseller; sales agent; channel partner. |

### Buy side (BuySide ICP v2)
**Buyers** are the organisations that **operate, regulate, finance or engineer mines and quarries**.

| Classification | Who | V2's own condition |
|---|---|---|
| Mining & Minerals Operators | Metals and minerals producers; coal producers; exploration and development companies (gold, copper, iron ore, bauxite, phosphate, potash, zinc, lithium, silica, gypsum…) | none |
| Government & Regulators | (a) Mining and natural-resources ministries and regulators: ministry of mines, mineral resources, geological survey, mining authority, licensing. (b) Investment promotion and trade bodies: investment promotion, export promotion, chamber of commerce, mining association. | none |
| Quarrying, Aggregates & Cement | Quarry operators and aggregates producers; cement and ready-mix producers (quarry, aggregates, limestone, sand and gravel, crushed stone, marble, cement, ready-mix, asphalt, clinker) | "Contacts must work in the quarrying or raw-materials team, not construction" |
| EPC Contractors & Engineering Services | Mining EPC and engineering firms (Worley, Fluor, Bechtel, AtkinsRéalis, Hatch) | "**Company must have mining projects in its description**" |
| Investors & Financial Institutions | Mining investors, funds and banks: investment banking, PE/VC, capital markets, banking, investment management | company keywords "**mining; metals; natural resources; commodities**"; "contacts MUST work in the mining team" |
| Processing, Refining & Smelting | Smelter, refinery, aluminium, beneficiation, foundry, metallurgical operations | none |

### OUT OF ICP: always exclude
- `OIL_GAS`: oil and gas operators and service companies.
- `CONSTRUCTION`: general construction contractors and building fit-out suppliers with no mining or quarry
  product.
- `WATER_UTILITY`: general water utilities with no mining product.
- `GENERIC_IT`: generic IT firms with no mining reference.
- `GENERIC_CONSULTING`: generic consulting firms with no mining reference.

## Verdicts (Mining)
| verdict | When |
|---|---|
| `KEEP` | The current Side and Classification are right under V2. |
| `RELABEL` | It is in V2's ICP, but under a **different** Side or Classification. Give the right one. |
| `OUT` | It is not in V2's ICP. Give the `reason` code. |
| `RENTAL` | Its business is equipment **rental/hire only**, with no dealing, distribution or aftermarket. V2's distributor sub-levels don't name rental. The user decides these, so don't call them OUT. |

`reason` codes for **OUT**:
- the five OUT-OF-ICP codes above;
- `NOT_V2_SUBLEVEL`: a real business, but none of the sub-levels or groups above. Examples: generic
  industrial components such as bearings, motors, valves, PPE, cables and fasteners; general
  industrial distribution; site services.
- `NO_MINING_LINK`: V2 requires a mining link for this group and there is none. This applies to a
  bank or fund with no mining/metals/resources/commodities focus, an EPC with no mining projects,
  and an association or agency with no mining/minerals/trade-promotion role.
- `NOT_A_COMPANY`: not a real company record, e.g. a pavilion, an individual, or an event.

For KEEP, RELABEL and RENTAL, `reason` = the V2 sub-level or group it matches, e.g. `Mine Planning
Software`, `Spare parts and aftermarket service providers`, `Mining investors, funds and banks`.

## How to judge (apply consistently)
- **The mining link is the core test.** A company that *exhibited at a mining show* and sells a V2
  sub-level product is in, even if its homepage is general.
  - For example, ABB or Advantech selling automation to mines is in; Aggreko rental power is in.
  - A company whose product isn't on any V2 list is `OUT NOT_V2_SUBLEVEL`, even at a mining show.
  - Examples: hydraulic hoses, bearings, safety boots, office IT, logistics, catering.
- **Automation:** industrial IT or software that names mining, quarrying, heavy industry or field
  equipment as a market is in. It also counts if the product is clearly a V2 sub-level, e.g. drones,
  survey instruments, condition monitoring or fleet tracking. A generic IT reseller, web agency or
  ERP shop is `OUT GENERIC_IT`.
- **Water:** a supplier of pumps, treatment, dewatering, dust suppression or waste equipment is in,
  because these are V2 sub-levels. A municipal water utility or water-only public body is
  `OUT WATER_UTILITY`.
- **Distributors:** in if they distribute or deal in equipment, parts, consumables or services used
  by mines or quarries. A distributor of generic industrial items only (bearings, electrical
  wholesale, valves, fasteners) is `OUT NOT_V2_SUBLEVEL`.
- **Investors:** a bank or fund with a mining, metals, natural-resources or commodities practice is
  in. Pan-African or development banks financing mining projects count. A retail bank, an asset
  finance house or a leasing firm with no mining focus is `OUT NO_MINING_LINK`.
- **Government:** mining or natural-resources ministries, regulators, geological surveys, mining
  authorities, investment and export promotion agencies, chambers of commerce and mining
  associations are in. Ports, rail, customs, EU institutions, nuclear agencies, general
  municipalities and economic zones with no mining role are `OUT NO_MINING_LINK`.
- **Wrong label but in ICP:** use `RELABEL`, e.g. a "distributor" that actually manufactures crushers
  becomes `Sell Side / Processing Technology`, or a contract miner becomes `Buy Side / Mining &
  Minerals Operators`.
- **Unsure:** if the evidence doesn't settle it, keep the current label. Use `KEEP` with confidence
  `L` and say why in the note. Don't drop on a guess.
- **Ignore geography** (the people search handles it). Ignore sanctions too, since sanctioned HQs
  were already removed.

## Verdict columns
- `KEEP`, `RENTAL`: `side` and `classification` repeat the current ones.
- `RELABEL`: in the ICP under a **different** label; give the right `side` + `classification`.
- `CLASSIFIED` (unknown rows only): the `side` + `classification` it belongs to.
- `OUT`: blank `side`/`classification`, `reason` = one of the codes above.
- `UNKNOWN` (unknown rows only): blank label; say in the note what is missing.
- For a label, `reason` = the sub-level or group it matches (e.g. `Mine Planning Software`).

## Input / output
- **Input:** `<RUN FOLDER>/recheck/in/<batch>.csv`. Read it in chunks of about 40 rows.
- **Output:** `<RUN FOLDER>/recheck/out/<batch>.csv`, utf-8-sig, with exactly this header:
  `domain,verdict,side,classification,reason,confidence,note`
  - One row per input row, in input order. Copy `domain` from the input with a Python csv script.
  - `confidence`: H, M or L. `note`: 3-15 words, no commas and no double quotes.
- **Validate:** `python <skill>/scripts/recheck.py check --dir <RUN FOLDER>/recheck --icp <icp.yaml>
  --scope <scope.yaml> <batch>` until it prints OK.
- **Searching:** web search (TinyFish `mcp__tinyfish__search`) for at most <N> companies per batch,
  no more than 3 per minute. A search result can be about a different company with the same name:
  trust it only when its URL or text matches the company's domain, country or business.
- Keep scratch files in your own folder under `<SCRATCHPAD>`. Modify nothing else.

## Reply with exactly
```
<batch>: <check line>     (one line per batch)
Searches run: n
Notable calls (up to 10): name | verdict | reason | why
```
