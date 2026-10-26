# Speakers

Speakers come from event sites as a name, a title and a company: no LinkedIn, no domain. They are
people already, so they skip Find People. But each one has to be matched to the right LinkedIn
profile, and **a wrong person is far worse than no answer** because the profile gets contacted.

Ask first:
- Are speakers in scope for this run, and are they delivered with the review tables or separately?
  Mining kept them out of the per-event tables ("leave the speaker's data") and gave them their
  own Salesforce file.
- Title rule: keep every speaker in an in-scope country and flag a failing title
  (`speakers.titles: flag`, Mining: "keep all speakers, filter on country only"), or apply the
  sheet's title rules (`filter`).
- Is the Claygent finder approved (paid), and which model? Mining used `gpt-6-luna`, at 1 action
  + 0.1 data credit per speaker.

## The steps

```bash
S=<skill>/scripts
python $S/speakers.py prep    --speakers remap/feed_speakers.csv --feed remap/feed_companies.csv --decisions remap/decisions.csv --out spk/speakers.csv
python $S/speakers.py search  --speakers spk/speakers.csv --cache spk/lookups.jsonl --limit 20     # pilot, then all
python $S/speakers.py match   --speakers spk/speakers.csv --cache spk/lookups.jsonl --people people.csv --rules post_filter.yaml --out spk/matched.csv
python $S/speakers.py finder-feed --matched spk/matched.csv --out spk/finder_feed.csv           # the unresolved
#   build + pilot the LinkedIn Finder (PAID, asks first):
python $S/build_tool_workflows.py linkedin-finder --name "<S> - Speakers - LinkedIn Finder" --ids spk/wf_finder.json --prompt spk/finder_prompt.md --model <id>
python $S/bulk_run.py --workflow <wf> --feed spk/finder_feed.csv --work runs/finder --id-cols "Row ID" \
    --inputs "Speaker ID,Speaker Name,Job Title,Company,Company Website,Country,Events,Side,Classification" --plan
python $S/speakers.py enrich-feed --matched spk/matched.csv --finder-feed spk/finder_feed.csv --finder-results runs/finder/results --out spk/enrich_feed.csv
#   Enrich Person (1 action, 0 credits) on enrich_feed, then Enrich Company by domain on the employers:
python $S/build_tool_workflows.py enrich-person  --name "<S> - Enrich Person" --ids spk/wf_person.json
python $S/build_tool_workflows.py enrich-company --name "<S> - Enrich Company by Domain" --ids wf_company.json
python $S/speakers.py people --enrich-feed spk/enrich_feed.csv --person-results runs/enrich_person/results \
    --company-results runs/enrich_company/results --icp icp.yaml --rules post_filter.yaml --out "spk/<S> - speakers.csv"
```

`spk/<S> - speakers.csv` holds people rows (`Source = Speaker`) that `review_list.py build
--speakers` merges in. `- checks.csv` beside it holds every profile with its checks: country in
scope, title passes, moved since the event, and the profile source.

## How a profile is accepted

1. **Stage 0 (free):** the same name tokens at the same company domain are already in the people
   search results, so we have the person already. Mining: 96.
2. **Stage 1, strict TinyFish (free):** search `"<name>" "<company>" linkedin`. A result passes
   only when ALL of these hold:
   - it is a personal `/in/` URL;
   - the name part of the result title has the first AND last name;
   - a distinctive company word (or the domain stem) is in the title or snippet. Industry words
     like "mining" or "gold" don't count (`speakers.company_stop_words`).
   Exactly one distinct profile may pass; two passing is ambiguous and rejected. Mining: 580 of
   1,168.
3. **Stage 2, the LinkedIn Finder Claygent (paid):** for the rest, from
   `assets/speaker_linkedin_finder_prompt.md`. Fill its event context and get the user's yes
   first. Only `found` with `High` confidence is used. Its nickname step (William → Bill) is what
   found people the strict search missed. Mining pilot: 21 of 25, all correct.
4. **Two methods disagree → excluded.** Mining ran the Claygent over the strict matches as well,
   as a second opinion; that was paid. 20 of 564 pointed at a DIFFERENT profile, and those were
   left out entirely.

## Traps

- **Speaker ids must be stable:** name tokens + company key, never row positions. A positional id
  misaligned the search cache once, when the kept speakers changed. `cached()` re-keys old
  records from their query.
- **Clay bulk row ids max out at 64 characters.** Use `Row ID` = sha1[:16]; `bulk_run.py` refuses
  longer ids.
- **A dead `bulk_run.py` does not stop Clay.** Chunks already submitted still run and are still
  billed: Mining's "stopped" confirmation run finished all 23 chunks, about 630 credits. Read
  `runs/<x>/state.json` and the Clay run before saying a run stopped.
- **A speaker who is already in the people list** is one row, `Source = Find People + Speaker`.
  `review_list.py` does this by LinkedIn slug; never deliver the person twice.
