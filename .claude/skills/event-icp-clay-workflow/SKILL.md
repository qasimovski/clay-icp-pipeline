---
name: event-icp-clay-workflow
description: Build and run the Terrapinn Clay Workflows pipeline for an event's ICP — take scraped exhibitor/sponsor/speaker CSVs, reuse any earlier run's typing under the newest ICP version (V2 always beats V1), check companies against the team's Supabase, classify or re-check them into the ICP's Buy Side / Sell Side categories, find the right people per category (job titles, personas, seniority, countries from the ICP workbook) and apply the sheet's exact title and country rules, match speakers to LinkedIn, hand the marketing manager a per-event review in Clay and stop for their approval, then get emails in a Clay workflow that reuses everything Supabase already holds (Blitz → LeadMagic → Prospeo + ZeroBounce only for the rest), and deliver a Salesforce-shaped CSV plus Clay Tables sheet rows. Every ICP-specific value (ICP, job titles, countries, products/services and industries, competitor events) is a variable in config/icps/<icp>/. Use this whenever someone wants to "do the Highways / Mining process" for another event or vertical (aviation, fintech, mining, EV…), push an ICP through Clay, turn an ICP workbook into a people search, re-run an event under a new ICP version, or asks how to get from scraped event CSVs to people lists for Salesforce — even if they don't say "workflow". Asks whenever unsure and before every paid step.
---

# Event ICP → Clay Workflows → people → Salesforce

The pipeline is the same for every event; what changes is the ICP and the input files. It was
built on Highways (Oct 2026: 3,540 companies → 10,951 people) and hardened on Mining (Oct 2026:
23,193 scraped rows → 3,538 companies → 19,082 people + speakers, 31 event workbooks). Every rule
below comes from something that went wrong on one of them.

## Rule 1: when you are not sure, ask

This is a hard rule, not a style preference. The human at the other end can answer in ten
seconds what a wrong guess costs in a paid re-run, or in a list that silently misses (or includes)
the wrong people.

**Ask whenever:**
- a column, tab, version or file could mean two things (which column is the website? which
  workbook is current? V1 or V2?);
- the sheet is ambiguous, contradictory or silent (a blank buy-side country, a note like "tight
  filters", a title in two lists);
- a rule can be read two ways ("exporters only": the company or the person?);
- a choice changes who is searched or what is spent (seniority depth, a second exact-title
  search, re-checking or classifying unknowns, the Claygent, speakers, a re-scrape);
- a number looks wrong (a 90% drop, an event with zero rows, two lists that are near-identical);
- you are about to delete, overwrite or publish anything;
- the earlier run left something you can't explain.

**How:** in small batches, each with your recommended answer first and the evidence (counts,
samples). Never spend a credit on an assumption. Record every answer where the next run will find
it: as a comment beside the value in the ICP's YAML (`# user, 25 Oct 2026: Booker wins`) or in a
script's docstring.

When the user says "go with your judgment", decide, write the rule down the same way, apply it
consistently, and report what it did. Mining's rental-only firms were handled like this.

## Rule 2: everything ICP-specific is a variable

| Varies per ICP / event | Lives in |
|---|---|
| the ICP itself, its categories (products & services, industries), side labels | `config/icps/<icp>/icp.yaml` |
| job titles, exclusions, seniority and its groups, countries per side and category | `icp.yaml` |
| exact-title rules, personas, per-keyword exclusions, geo tiers, LinkedIn-only countries, team evidence, speaker settings | `config/icps/<icp>/post_filter.yaml` |
| competitor events (in / Cut, tier, per-event rules), old-type → new-label map, company drop rules, re-check labels | `config/icps/<icp>/scope.yaml` |
| input CSVs, run folder, Salesforce schema, Clay folder, event ids, credit ceiling | asked at intake; command-line arguments |

Shapes and real examples are in `assets/` (`icp.example.yaml` Highways, `icp.mining.example.yaml`,
`post_filter.example.yaml`, `scope.example.yaml`). **No script hard-codes an ICP.** If something
seems to need a script change for one ICP, make it a config value with a check instead. Validate
all three files together:

```bash
python scripts/icp_mapping.py check config/icps/<icp>/icp.yaml --rules config/icps/<icp>/post_filter.yaml --scope config/icps/<icp>/scope.yaml
```

## Rule 3: the newest ICP version wins, and earlier runs are reused, not trusted

Before building anything, ask: has any of these events been scraped, typed, classified or
searched before, under an older version of this ICP or under another ICP?

If so, follow `references/versions_and_prior_runs.md`:
- reuse the old per-row TYPE, mapped onto the new labels;
- never reuse the old KEEP/DROP;
- whatever the old version cut but the new one wants comes back;
- whatever the old version did that the new one doesn't say is ignored.

This is how Mining kept 3,459 companies out of a ~10k-credit re-classification.

## Before you start: what must exist

- The **Clay CLI**, signed in (`clay whoami`). On Windows it only runs inside WSL; `_clay.py`
  handles that. Load the Clay plugin's `clay:workflows-cli` skill for command syntax.
- **Python 3** with `pyyaml`, `openpyxl`, `requests` and `psycopg` (for Supabase).
- The **GitHub repo** `qasimovski/clay-icp-pipeline`: the Clay table importer, and `config/icps/`.
- The companion skill **`supabase-known-lookup`**, and a `.env` with the Supabase variables. Ask
  where it is; never ask for a key or connection string in chat, and never print one.
- For speakers: `TINYFISH_API_KEY` (in the environment or a `.env`; ask where).

Work in a run folder of the user's choosing and keep every file there. Scripts are in this skill's
`scripts/`; run each with `--help` when unsure. Each script's docstring is its reference.

## Phase 0: intake (ask, then summarise back)

Ask in one message, recommended answers first:

**The event and the ICP**
1. Event / series name (used in file names and the `<ICP>_` list prefix), and where the scraped
   CSVs are: exhibitors, sponsors, speakers?
2. The ICP workbook path, and **which version** (tabs) is current. Is there an older version?
3. **Earlier runs:** have any of these events been scraped, typed, classified or searched before?
   Where is that output (Rule 3)?
4. The **Competitor sheet:** which events are in scope now, which are Cut, and are the tiers and
   notes current? Is anything else (Other Sources, net-new P&S / BuySide searches) for later?
   (Mining: "only the competitive ones for now".)
5. Have the CSVs been cleaned and fit-filtered? Filter BEFORE Clay: Highways enriched and
   classified 3,878 companies that a filter then dropped.

**Who and where**
6. Geography: the PERSON's location or the company's? Countries searched but not emailed
   (LinkedIn-only)? Parked or sanctioned countries?
7. The classification prompt (theirs, or a draft from the workbook for their review), or local
   re-classification with the earlier run's brief?
8. Speakers: in scope? With what title rule?
9. The Salesforce target (a previous upload CSV whose header is the schema) and the
   `Competitive Event Id` per event, if they have them.

**Money and approval**
10. Where the Supabase `.env` and the team key file are.
11. A credit ceiling, and actions vs data credits.
12. Who the marketing manager is, the Clay folder for the review copies (they create it by hand),
    and whether the email-workflow accounts exist.

## Phase 1: inspect the inputs, then reuse or build the company feed

```bash
python scripts/inspect_inputs.py csv "<…/*/exhibitors_normalized.csv>" "<…/*/sponsors_normalized.csv>"
python scripts/inspect_inputs.py xlsx "<ICP workbook>.xlsx"
```

Propose the CSV map with evidence (fill rates, samples), point out anything odd, and ask. Then:

- **An earlier run exists:** follow Rule 3. Write the adapter to `prior_rows.csv`, then `scope.yaml`,
  then `remap_prior.py`. **Show `REVIEW.md` and stop for the user's review.**
- **A fresh start:**
  ```bash
  python scripts/build_feed.py --out feed.csv --input "<glob>=Exhibitor" --input "<glob>=Sponsor" \
      --name-col "Company Name" --website-col Website --description-col Description --event-col Event
  ```
  Show the flagged domains (shared host, hosted site or marketplace storefront, no domain) and any
  `overlap:` lines, and ask for the right domains. A search keyed on `www.gov.uk` returns the whole
  UK government.

Companies with no usable domain can't be searched. List them and ask whether to find their sites,
and how. Mining filled 399 of 433 from reviewed web evidence.

## Phase 2: the ICP config (three files)

Follow `references/icp_from_workbook.md`: propose which tab and column feeds each field, ask about
every ambiguity, write the three files, and run the check (Rule 2). Read the summary back in plain
words: titles per side, categories, countries and tiers, seniority groups, personas, LinkedIn-only
countries, the warnings. **Get an explicit yes.** The classifier prompt (if any) uses the same
labels, character for character, and needs its own yes.

## Phase 3: the company gate (free)

```bash
python <supabase-known-lookup>/scripts/supabase_lookup.py --env-file <.env> companies \
    --in feed.csv --domain-col Domain --out "feed - supabase.csv"
```

Show: new / known / known-and-targeted-before. Ask which rule removes companies from the paid feed
(skip all known, only those already targeted for this event, or none). Then re-run with
`--skip … --event-slug <slug>`. Gated rows are not sent at all.

## Phase 4: tighten before paying (free: agent time, homepage fetches)

Before searching people for a company, make sure it really is in the ICP under the NEW rules.

- **Re-check the wide labels:** the labels whose old typing was looser than the new rules
  (`scope.yaml recheck.labels`).
- **Type the unknowns:** locally, if the user chose that over the paid classifier.

Use `recheck.py` (queue, then fetch, then batches) with a brief built from
`assets/recheck_brief.example.md`; get the user's yes on the filled brief. Agents write verdicts,
`recheck.py check` validates them, and `remap_prior.py --verdicts` applies them. Verdicts the
brief can't settle are listed for the user, never guessed. Mining: 201 companies OUT before a
single search.

## Phase 5: build the workflows

Follow `references/workflow_build.md`:
- **classify**, only if companies need the paid classifier;
- **people:** Build → ICP Mapping → Searchable? → Fit Keep? → Find People;
- **one-action tools** (`build_tool_workflows.py`): Enrich Person, Enrich Company by domain, and
  the speaker LinkedIn Finder.

```bash
python scripts/icp_mapping.py payload icp.yaml --build-node-id <people Build node id> --out mapping_update.json
```

Draft-test each workflow on 2–3 real rows, read the node outputs **and the measured cost**, show
the user, then publish and `clay routines create workflow <wf> --name "<name>"`.

## Phase 6: classify (PAID, confirm first)

Only for companies with no type. State the count and the cost from the draft test, and wait for a
yes.

```bash
python scripts/bulk_run.py --workflow <classify wf> --feed "feed - gated.csv" --work runs/classify \
    --inputs "<trigger input columns>" --id-cols "Event,Entity,Domain" --plan
python scripts/bulk_run.py … --limit 1          # pilot: one chunk; show the user the labels
python scripts/bulk_run.py …                    # the rest, after their OK
python scripts/bulk_run.py … --retry-failed     # "Unknown error" rows pass on retry
python scripts/collect.py classified --feed "feed - gated.csv" --results runs/classify/results --out classified.csv
```

After a remap, `classified.csv` can go back into `remap_prior.py --verdicts`, so each label still
passes each event's rules.

## Phase 7: find people, then apply the sheet's rules

**Search.** Price it from the draft test (Find People v2 measured 1 action, 0 data credits per
search).

```bash
python scripts/collect.py people-feed --classified classified.csv --out people_feed.csv   # or remap/feed_companies.csv
python scripts/bulk_run.py --workflow <people wf> --feed people_feed.csv --work runs/people \
    --inputs "Company Name,Domain,Side,Classification,Countries Override,Seniority Group,Fit" --id-cols Domain --plan
```

**Go deeper at full companies.** A company that returns exactly 10 is searched again per seniority
group. Ask before going deeper than the groups in `icp.yaml`.

```bash
python scripts/collect.py capped --feed people_feed.csv --results runs/people/results --out-prefix people_feed --icp icp.yaml
python scripts/collect.py merge-splits --out runs/split_merged runs/sen_*/results
```

**Export and filter.**

```bash
python scripts/collect.py people --classified classified.csv --results runs/people/results --split runs/split_merged --icp icp.yaml --out people.csv
python scripts/post_filter.py --people people.csv --icp icp.yaml --rules post_filter.yaml --results runs/people/results runs/split_merged --out-prefix "<S>"
```

`references/people_rules.md` covers:
- what is searched wide and what is judged exactly;
- which people are kept with a flag for the approver (near-exact and non-English titles);
- how the LinkedIn-only countries split off.

Show the dropped reasons and flag counts.

## Phase 8: speakers (if in scope)

Follow `references/speakers.md`:
- **Stage 0, already found:** the speaker is in the people results.
- **Stage 1, strict TinyFish match:** free.
- **Stage 2, the LinkedIn Finder Claygent:** paid; ask first, then pilot.
- **Enrich:** Enrich Person and Enrich Company by domain (both 1 action, 0 credits).
- **Output:** `speakers.py people`.

A wrong profile is worse than a miss. Profiles that two methods disagree on are excluded.

## Phase 9: the review list, the Clay review copies, and the marketing manager (HARD STOP)

```bash
python scripts/review_list.py domains --in "<S> - email list.csv" "<S> - LinkedIn only.csv" --out company_feed.csv
#   Enrich Company by domain on company_feed.csv (1 action, 0 credits)
python scripts/review_list.py build --people "<S> - email list.csv" --linkedin-only "<S> - LinkedIn only.csv" \
    [--speakers "spk/<S> - speakers.csv"] --company-results runs/enrich_company/results --rules post_filter.yaml \
    --out-prefix "<S> - review" [--drop-cols "…"]
python scripts/approval.py review --people "<S> - review.csv" --out "<S> - people for review.csv"
```

**The per-event Clay review copies:** `deliver.py split --decisions` → import into the user's
folder → the Clay Tables rows. Follow `references/delivery.md`: the convention, the import, the
low-memory rules, and confirming what is and isn't in the folder (speakers? LinkedIn-only?).

People are found, but **no email is looked up, found or validated yet.** The manager often
removes titles, countries or companies, and enriching first pays for people they then delete.
Give the user the files, the Clay folder and the summary. **Then stop and wait.** Don't build or
run the email workflow, and don't suggest "starting the pilot meanwhile".

When the manager answers, turn it into `changes.yaml` (shape in `approval.py`'s docstring), read
it back in plain words, and apply it:

```bash
python scripts/approval.py apply --people "<S> - review.csv" --changes changes.yaml \
    --approved-by "<manager's name>" --out "people - approved.csv" [--notes "…"]
```

**Additions** (a new title or country) need a new Find People run for the affected companies,
priced first, then a fresh review.

## Phase 10: emails (workflow 3, approved file only, never the LinkedIn-only file)

Read `references/email_workflow.md`.
- **Supabase first, for everyone:** any email it already holds is reused with its validation.
- **The waterfall only for the rest:** Blitz → LeadMagic → Prospeo, then ZeroBounce on the
  waterfall's email.
- **Every paid answer is written back.**
- **A failed Supabase lookup:** that person is not searched.

```bash
python scripts/email_workflow.py check --config email_workflow.yaml
python scripts/email_workflow.py build --config email_workflow.yaml --workflow <wf> --state email_build_state.json
python scripts/email_workflow.py check-accounts --config email_workflow.yaml --state email_build_state.json
python scripts/bulk_run.py --workflow <email wf> --feed "people - approved.csv" --approval approval.json \
    --work runs/emails --inputs "LinkedIn URL,First Name,Last Name,Person Name,Company Name,Domain,Job Title,Mailing Country" \
    --id-cols "LinkedIn URL" --plan
python scripts/collect.py emails --people "people - approved.csv" --results runs/emails/results --out "people - emails.csv"
```

`--approval` refuses any feed that isn't byte-identical to the approved list. Price it before the
pilot. Report sources, per-provider counts, "no email", and any found-but-not-recorded rows.

## Phase 11: deliver to Salesforce

```bash
python scripts/deliver.py salesforce --people "people - emails.csv" --schema "<Salesforce CSV>" \
    --out "<S> - Salesforce.csv" --explode-events [--event-ids event_ids.csv] [--map sf_map.yaml]
```

- **`--explode-events`** writes one row per person per event (company events + speaker events).
- **Speaker rows** follow the earlier uploads: Is CA Speaker True; a speaker-only row is not SPEX
  or Attendee and has no Classification.
- **LinkedIn-only people** get their own file, never an email column.
- Ask about every unmapped or empty column before calling it done.

## Long jobs on a small machine

Read `references/delivery.md` before any long run:
- check processes before resuming anything "killed";
- never run two importers;
- re-read live counts before deleting;
- Clay finishes submitted work after the local driver dies;
- pause and resume on low memory.

The user's standing OK covers repairing and resuming FREE imports. It never covers restarting a
paid run.

## How to report

At each checkpoint, say:
- what was done, with the counts;
- what it cost: actions and data credits, measured, never from the catalog;
- what is next, and what you need from the user.

Plain language: the user is a GTM operator, not a developer. Never claim a step worked without the
count that proves it (Clay row counts over the CLI, result files on disk). Never claim a run
stopped without checking Clay. If you got something wrong, say so plainly with the correction.
