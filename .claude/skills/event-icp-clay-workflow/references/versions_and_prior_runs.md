# ICP versions and earlier runs (V2 vs V1)

An ICP changes over time (the Mining workbook gained V2 tabs beside the V1 ones), and the same
events are often scraped, typed or searched before, under an older version or for another ICP.
Check for both at the start of every run. Reusing the earlier work is what keeps the build cheap,
and deciding it again under the new rules is what keeps it right.

## The rule: the newer version always wins

User, 25 Oct 2026, for every ICP from now on:

> "V2 always takes precedence over V1, so always prefer V2 over V1. If anything was done in V1 but
> it's not in V2, then ignore it. If there is something that was cut in V1 but is right now in V2,
> include that."

What that means in practice:

| Earlier run did | New version says | Result |
|---|---|---|
| typed a company `HEAVY_EQUIP` | Heavy Equipment is a Sell category | **reuse the type** (no paid classify) |
| KEPT a company at a Cut event | the event is Cut | dropped |
| DROPPED a company (e.g. a borderline-event buyer) | the event note now wants it | **put back** |
| a category with no new home (`INDUSTRIAL`) | nothing | dropped (ask the user; Mining: drop) |
| left a company `UNKNOWN` | — | classify it (Clay classifier, or `recheck.py` locally) |
| applied a rule the new version doesn't have | — | ignored |
| a geography decision | person location in V2 | the new geography rules only |

**The old KEEP/DROP is never an input.** Only the old per-row TYPE is. It is mapped onto the
new labels by `scope.yaml` `type_map`, and every decision is made again from the new tabs.

**Use the newest version's tabs only.** When a workbook has V1 and V2 tabs, read only V2. When a
V2 tab is missing for something, ask. Never fall back to V1 silently. A downloaded copy of the
workbook may be newer than the one sitting in the scrape folder (Mining's was), so ask which file
is current.

## Questions to ask before reusing anything

1. Which workbook and which tabs are current? Is there an older version, and where did its run
   leave its output (types, decisions, websites, people)?
2. Where are the old per-row types, and what do the old type codes mean? Read the old brief or
   rules file; don't guess from the code names.
3. For every old type with no obvious new home: drop it, or map it? (Mining: INDUSTRIAL,
   MINING_SUPPLIES and SITE_SERVICES were dropped.)
4. The Competitor sheet: which events are in, which are Cut, each event's tier, and each event's
   note. Read every note to the user as a rule ("PDAC: MEA miners and governments only") and
   confirm it.
5. Unknowns: classify them at all? With the paid Clay classifier, or locally with `recheck.py`
   against the same brief the old run used ("the same classification", Mining)?
6. Did the old run fill in websites or fix domains? Reuse those fills, but only on rows with no
   site of their own. A name-keyed fill applied to every row overwrote real sites on Mining
   (mining.komatsu became komatsu.com) and 60 companies vanished.
7. Did the old run already search people for some of these companies? The Supabase gate
   (`supabase-known-lookup companies`) answers this; ask which rule removes them.

## Doing it

1. **Adapter, then `prior_rows.csv`.** Every earlier run leaves its own file shapes, so write a
   small adapter (in the run folder, not the skill) that emits one row per scraped company row:
   `Event, Entity, Company Name, Website, Country, Prior Type`. Add any column a rule names, such
   as `Sanctioned HQ` or `Countries Of Exploration`, and the old decision for comparison
   (`--prior-decision-col`). Use the FINAL website the old run settled on.
2. **`scope.yaml`** in `config/icps/<icp>/` (shape: `assets/scope.example.yaml`, the real Mining
   file). Then run
   `icp_mapping.py check icp.yaml --rules post_filter.yaml --scope scope.yaml`.
3. **Run it:**
   ```bash
   python scripts/remap_prior.py --scope scope.yaml --prior prior_rows.csv --out remap/ \
       --prior-decision-col "V1 Decision" [--speakers speakers_in.csv --company-types old_types.csv]
   ```
4. **Show `remap/REVIEW.md`:** per-event before→after, why old KEEPs are now dropped, what comes
   back, and samples for every event with a special rule. **Stop for the user's review.** On
   Mining, PDAC "as written" lost ~180 exploration suppliers; the user had to see that and confirm.
5. **Tighten before paying** (`recheck.py`). The labels whose old typing was wider than the new
   rules are re-checked against the new tabs, word for word, with fresh homepage evidence. Agents
   follow a brief (`assets/recheck_brief.example.md`). Then apply the verdicts:
   `remap_prior.py … --verdicts recheck/out/*.csv`.
   - The same tool types the unknowns locally, if the user wants that instead of the Clay
     classifier.
   - Verdicts the brief can't settle (Mining's rental-only firms) are listed for the user, never
     guessed.
6. **Feed the people workflow:** `remap/feed_companies.csv` already has the people workflow's
   columns.
   - `remap/no_domain.csv` lists companies that can't be searched; ask whether to find their
     websites.
   - `remap/decisions.csv` is what the per-event Clay tables show (`deliver.py split --decisions`).

## Mining V2, for scale (Oct 2026)

- **Remap:** 23,193 scraped company rows. 3,459 companies were searchable straight from V1 types,
  with no paid classification; re-classifying them would have cost about 10k credits.
- **Unknowns:** 219 V1 UNKNOWNs were typed locally, and 94 of them joined the feed.
- **Re-check:** 1,339 companies were queued. 201 were OUT, 42 RELABEL and 17 RENTAL (decided by
  the user's rule).
- **Feed:** 3,538 companies. The remap is reproducible with
  `remap_prior.py --scope config/icps/mining/scope.yaml`; its row decisions match the hand-built
  `scrapers_mining/v2_remap.py` exactly.
