# People: the search, the sheet's exact rules, and the review list

Clay's Find People is the **wide net**: word-level contains, soft exclusions, up to 10 people per
search. The ICP sheet's rules are stricter, and Clay can't express them all. So the search
(`icp.yaml`) is deliberately a little wider than the sheet, and `post_filter.py` then applies the
sheet word for word (`post_filter.yaml`). Both files are variables per ICP in `config/icps/<icp>/`.

## What goes where

| Rule from the sheet | Where it lives | Why there |
|---|---|---|
| Contains titles per side / category | `icp.yaml` titles | Clay can search them |
| Exact titles safe in contains mode (CEO, Managing Director) | `icp.yaml` titles **and** `post_filter.yaml` exact | search wide, judge exactly |
| Exact titles that would flood contains mode (Vice President, bare Manager / Director / Sales) | `icp.yaml` `<side>.exact_titles` (a separate exact-mode search, if approved) | "Manager" contains-matches everyone |
| An exclusion that no keyword on that side ever wants | `icp.yaml` `exclude_titles` / `<side>.exclude_titles` | Clay drops them before they cost a slot |
| An exclusion tied to ONE keyword ("Sales Manager, but not Aftersales"; "Marketing, but not SEO") | `post_filter.yaml` `contains: {kw: {exclude: [...]}}` | a global exclusion would hurt another keyword (Stand staff WANTS Aftersales) |
| A buy-side exclusion lifted for one group ("Partner" allowed for Investors) | `post_filter.yaml` `buy.exclude_allowed` | per category |
| Two seller personas (Bookers / Stand staff) | ONE search in `icp.yaml`; `post_filter.yaml` `sell.personas` tags them; the first persona listed wins | two searches pay twice for the shared keywords (Mining: 25 shared) |
| Countries per side / category | `icp.yaml` | `location_countries_include` filters the PERSON's location |
| Country tiers (Core / Yes / Secondary) | `post_filter.yaml` `<side>.geo_tier` | a tag for the manager, not a filter |
| Countries searched but never emailed (Germany on Mining) | `post_filter.yaml` `linkedin_only_countries` | their own file; never reaches the email step |
| "Contacts must work in the mining team" | `post_filter.yaml` `buy.team_evidence` | a flag from title + headline; the manager decides |

Each decision behind a value goes in a YAML comment with the date and who made it, e.g.
`# user, 25 Oct 2026: Booker wins on overlap`. The next run then knows it was decided, and by whom.

## Kept but flagged, never silently dropped

`post_filter.py` drops only what the sheet clearly excludes. Two kinds are **kept with a flag**
in `Title Check` for the marketing manager (user, 25 Oct 2026):

- **near-exact**: a senior exact title plus a qualifier, e.g. "Group CEO", "Managing Director:
  Africa", but also "GM HR". Bare Manager / Director / Sales / President / Vice President /
  Owner never count (`flags.bare_titles`).
- **non-English**: a title the English rules can't read, e.g. 副总经理, مدير عام, "Projektleiter",
  "Gerente general". Clay matched it, so it may be right.

The approver decides on these. Whether to keep or drop a flag class is the user's call: ask.

## Seniority depth: more than two groups

Find People returns at most 10 people per search (with full profiles). A company that returns
exactly 10 is searched again once per seniority group in `icp.yaml` `seniority_groups`. When a
group comes back full again, split that group too. On Mining, B (head…mid-level) was full at 724
of 804 companies. The user chose to split it, so B became B1 (head, manager) and B2 (senior,
mid-level), which ran on those 724 only and added 4,470 people for 1,448 actions.

```bash
python scripts/collect.py capped --feed people_feed.csv --results runs/people/results --out-prefix people_feed --icp icp.yaml
# … run each group's feed; then, for a group that was full again (after icp.yaml lists B1, B2 and the
#   mapping node is regenerated and published):
python scripts/collect.py capped --feed "people_feed - sen B.csv" --results runs/sen_B/results --out-prefix people_feed --groups B1,B2
python scripts/collect.py merge-splits --out runs/split_merged runs/sen_A/results runs/sen_B/results runs/sen_B1/results runs/sen_B2/results
python scripts/collect.py people --classified classified.csv --results runs/people/results --split runs/split_merged --icp icp.yaml --out people.csv
```

Each extra split costs one search per capped company per group. Say the count and ask before
running it ("instead of dividing seniority into 2, do 3 so we get more leads": user's call).

## The post-filter

```bash
python scripts/post_filter.py --people people.csv --icp icp.yaml --rules post_filter.yaml \
    --results runs/people/results runs/split_merged --out-prefix "<Series>" \
    [--linkedin-only-name "Germany LinkedIn only"]
```

It writes `<Series> - email list.csv`, `- LinkedIn only.csv` and `- dropped by post filter.csv`,
which always add up to the input. Show the user the dropped reasons and the flag counts.

**Countries are checked here, not before.** `collect.py` doesn't re-check the person's country;
this is the only place. A person whose location is written in Arabic or Chinese gets their
country from the ISO2 fallback in `collect.py` (Mining: 60 of the first ~560 people would
otherwise have had none).

## The review list (one canonical file)

```bash
python scripts/review_list.py domains --in "<S> - email list.csv" "<S> - LinkedIn only.csv" --out company_feed.csv
#   -> Enrich Company by domain (build_tool_workflows.py enrich-company; 1 action, 0 data credits)
python scripts/review_list.py build --people "<S> - email list.csv" --linkedin-only "<S> - LinkedIn only.csv" \
    [--speakers "spk/<S> - speakers.csv"] --company-results runs/enrich_company/results \
    --rules post_filter.yaml --out-prefix "<S> - review" [--drop-cols "..."]
```

`<S> - review.csv` plus `<S> - review - LinkedIn only.csv` are THE list. The marketing manager
reviews it, `approval.py apply` runs on it, and the email step runs on the approved file.
- **Firmographics:** Sub-Industry, Primary Offerings and Business Stage come from Enrich Company
  **by domain** only.
- **Which columns the manager sees** is the user's call (`--drop-cols`). Mining dropped
  Countries Override and the company Country (the person's Mailing Country is the one used), and
  the speaker event columns.
