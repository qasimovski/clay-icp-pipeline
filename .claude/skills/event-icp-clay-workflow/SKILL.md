---
name: event-icp-clay-workflow
description: Build and run the Terrapinn Clay Workflows pipeline for an event's ICP — take scraped exhibitor/sponsor CSVs, check them against the team's Supabase, enrich and classify each company into the ICP's Buy Side / Sell Side categories with a Clay agent, find the right people per category (job titles, seniority, countries from the ICP workbook), reuse known emails, and deliver a Salesforce-shaped CSV plus per-event Clay tables and Clay Tables sheet rows. Use this whenever someone wants to "do the Highways process" for another event or vertical (aviation, fintech, mining, EV…), set up classification and Find People in Clay workflows, turn an ICP workbook into a people search, or asks how to get from scraped event CSVs to people lists for Salesforce — even if they don't say "workflow". Asks before every paid step.
---

# Event ICP → Clay Workflows → people → Salesforce

The pipeline is the same for every event; what changes is the ICP (classification labels, job
titles, seniority, countries, buy-side industries) and the input files. It was built and run on
Highways (Oct 2026: 3,540 companies → 10,951 people, plus Other Sources → 5,582) and every rule
below comes from something that went wrong there.

**The one rule that matters most: when you are not sure, ask.** Which column is the company
website? Which tab holds the buy-side titles? Does "Any seniority" include Entry? Is this domain
right? A question costs the user ten seconds; a wrong guess costs a re-run of paid enrichment or a
list that silently misses people. Ask in small batches with your recommended answer first, and
never spend credits on an assumption.

## Before you start — what must exist

- The **Clay CLI**, signed in (`clay whoami`). On Windows it only runs inside WSL; the scripts'
  `_clay.py` handles that. Load the Clay plugin's `clay:workflows-cli` skill for command syntax.
- **Python 3** with `pyyaml`, `openpyxl`, and `psycopg` (for Supabase).
- The **GitHub repo** `qasimovski/clay-icp-pipeline` (for the Clay table importer only) — clone it
  if it isn't on the machine.
- The companion skill **`supabase-known-lookup`** and a `.env` with `SUPABASE_DB_URL` (ask where;
  never ask for the connection string in chat).

Work in a run folder of the user's choosing (e.g. `<event>_clay_run/`) and keep every file there.
Scripts are in this skill's `scripts/`; run each with `--help` when unsure.

## Phase 0 — intake (ask, then summarise back)

Ask the user, in one message, recommended answers first:
1. Event / series name (used in file names, e.g. "Seamless"), and where the scraped CSVs are.
2. Have the CSVs been cleaned and fit-filtered already? (Filter BEFORE Clay — on Highways 3,878
   companies were enriched and classified, then dropped by a filter that could have run first.)
3. The ICP workbook path.
4. The classification prompt — do they have one (a Clay prompt), or should you draft one from
   the workbook for their review? See `references/classifier_prompt_example.md` for the shape.
5. Which entities: exhibitors, sponsors, speakers?
6. The Salesforce target: a previous Salesforce upload CSV whose header is the schema (Qasim's:
   `Labs - Tables - Mapped/Combined/Competitive Events/Competitive Events.csv`), and the
   Salesforce `Competitive Event Id` per event if they have them.
7. Where the Supabase `.env` is.
8. A credit ceiling for this run.

## Phase 1 — inspect the inputs and agree the column map

```bash
python scripts/inspect_inputs.py csv "<…/*/exhibitors_normalized.csv>" "<…/*/sponsors_normalized.csv>"
python scripts/inspect_inputs.py xlsx "<ICP workbook>.xlsx"
```

Propose the CSV map (company name, website, description, country, event — a column or the
folder name) and show the evidence: fill rates and samples. Point out anything odd (a country
column at 17% fill with values like "P1"; columns missing from some files) and ask how to treat
it. Then build the feed:

```bash
python scripts/build_feed.py --out feed.csv --input "<glob>=Exhibitor" --input "<glob>=Sponsor" \
    --name-col "Company Name" --website-col Website --description-col Description --event-col Event
```

Show the flagged domains (`shared host`, `hosted site`, `no domain`) and any `overlap:` lines
(an event whose exhibitor and sponsor lists are near-identical — usually one list scraped twice;
ask which is real), and ask for the correct
domains of any shared host — a search keyed on `www.gov.uk` returns the whole UK government.

## Phase 2 — the ICP config

Follow `references/icp_from_workbook.md`: propose which tab/column feeds each field, ask about
every ambiguity, write `icp.yaml` (shape: `assets/icp.example.yaml`, the real Highways ICP), then

```bash
python scripts/icp_mapping.py check icp.yaml
```

Read the summary back in plain words and get an explicit yes. Write the classifier prompt with the
same category labels, character for character, and get the user's approval of the prompt too.

## Phase 3 — the company gate (free)

```bash
python <supabase-known-lookup>/scripts/supabase_lookup.py --env-file <.env> companies \
    --in feed.csv --domain-col Domain --out "feed - supabase.csv"
```

Show: new / known / known-and-targeted-before. Ask which rule removes companies from the paid
feed — skip all known, only those already targeted for this event, or none — then re-run with
`--skip known|targeted|targeted-for-event --event-slug <slug>` to write `feed - supabase - gated.csv`.
Rows that are gated out are not sent at all.

## Phase 4 — build the workflows

Follow `references/workflow_build.md`: workflow 1 (classify: Build → Has Domain? → Enrich company
→ Classifier → Company Output) and workflow 2 (people: Build → ICP Mapping → Searchable? → Fit
Keep? → Find People), from the templates in `assets/nodes/`. Generate the mapping node:

```bash
python scripts/icp_mapping.py payload icp.yaml --build-node-id <people Build node id> --out mapping_update.json
```

Draft-test each workflow on 2–3 real rows, read the node outputs, show the user, then publish
and `clay routines create workflow <wf>`.

## Phase 5 — classify (PAID — confirm first)

State the count and the cost (rows × (Enrich company 1 credit + the agent's credits; read the
agent cost from the draft test) and wait for yes. Then:

```bash
python scripts/bulk_run.py --workflow <classify wf> --feed "feed - gated.csv" --work runs/classify \
    --inputs "<trigger input columns>" --id-cols "Event,Entity,Domain" --plan
python scripts/bulk_run.py … --limit 1          # pilot: one chunk; show the user the labels
python scripts/bulk_run.py …                    # the rest, after their OK
python scripts/bulk_run.py … --retry-failed     # "Unknown error" rows pass on retry
python scripts/collect.py classified --feed "feed - gated.csv" --results runs/classify/results --out classified.csv
```

Show the label distribution and a sample per label; if a large share lands in an "other"
bucket, look at why before going on.

## Phase 6 — find people (price it from the draft test — see the action-key note in workflow_build.md)

```bash
python scripts/collect.py people-feed --classified classified.csv --out people_feed.csv
python scripts/bulk_run.py --workflow <people wf> --feed people_feed.csv --work runs/people \
    --inputs "Company Name,Domain,Side,Classification,Countries Override,Seniority Group,Fit" --id-cols Domain
python scripts/collect.py capped --feed people_feed.csv --results runs/people/results --out-prefix people_feed
python scripts/bulk_run.py --workflow <people wf> --feed "people_feed - sen A.csv" --work runs/sen_a --inputs "…" --id-cols Domain
python scripts/bulk_run.py --workflow <people wf> --feed "people_feed - sen B.csv" --work runs/sen_b --inputs "…" --id-cols Domain
python scripts/collect.py people --classified classified.csv --results runs/people/results \
    --split runs/sen_a/results --split runs/sen_b/results --icp icp.yaml --out people.csv
```

Companies that returned exactly 10 are searched again per seniority half (A, B) for up to 20 —
the action's hard maximum is 10 per search with full profiles; `identifiers_only` would allow
more but drops the name and location fields Salesforce needs. The export re-applies the
exclusion list locally (Clay's is a soft filter), dedupes on the LinkedIn slug, and writes
`people.csv` + `- excluded.csv` + `- duplicates.csv` that add up exactly.

## Phase 7 — reuse known emails (free)

Run the `supabase-known-lookup` skill's `contacts` mode on `people.csv` (pass `--domain-col
Domain` so name+domain matches count too). Report reused emails (valid vs risky), the size of
`… - still to enrich.csv` — that file, not the whole list, is what the paid email waterfall
should get later — and ask what to do with `… - user to decide.csv` (known people with a bad or
no email: re-find or skip).

## Phase 8 — deliver

```bash
python scripts/deliver.py salesforce --people "people - supabase.csv" --schema "<Salesforce CSV>" \
    --out "<Series> - Salesforce.csv" [--event-ids event_ids.csv] [--map sf_map.yaml]
python scripts/deliver.py split --classified classified.csv --people "people - supabase.csv" \
    --out delivery --plan delivery_plan.json
```

The Salesforce step prints which columns are filled and which have no source — ask about every
unmapped or empty column before calling it done. Then the Clay import into a folder the user
creates by hand (`references/delivery.md` — headless Chrome, pilot one event, memory pauses), and

```bash
python scripts/deliver.py rows --plan delivery_plan.json --state delivery_state.json \
    --prefix <Series> --workspace <workspace id> --gtm <name> --out "<Series> - Clay Tables rows"
```

## How to report

At each checkpoint: what was done, the counts, what it cost, what is next and what you need from
the user. Plain language — the user is a GTM operator, not a developer. Never claim a step worked
without the count that proves it (Clay row counts over the CLI, result files on disk).
