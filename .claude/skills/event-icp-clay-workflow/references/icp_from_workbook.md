# From the ICP workbook to the ICP's config files

Every event's workbook is laid out a little differently, so the mapping is proposed, shown and
confirmed — never assumed. Run `scripts/inspect_inputs.py xlsx <workbook>` and read each tab's
header row and first rows before proposing anything.

## Three files per ICP: everything that varies is a variable

They live in `config/icps/<icp>/` in the repo. No script hard-codes an ICP: if you catch yourself
editing a script for one ICP, the value belongs in one of these files instead, and in the
`icp_mapping.py check` cross-check.

| File | Holds | Example |
|---|---|---|
| `icp.yaml` | the Clay search: categories, titles, exclusions, seniority (+ groups), countries per side and category | `assets/icp.example.yaml` (Highways), `assets/icp.mining.example.yaml` |
| `post_filter.yaml` | the sheet's exact rules: personas, exact vs contains, per-keyword exclusions, geo tiers, LinkedIn-only countries, team evidence, speaker settings | `assets/post_filter.example.yaml` (Mining) |
| `scope.yaml` | the Competitor sheet: events in scope, tiers, per-event keep rules; the old-type → new-label map; re-check labels | `assets/scope.example.yaml` (Mining) |

```bash
python scripts/icp_mapping.py check config/icps/<icp>/icp.yaml \
    --rules config/icps/<icp>/post_filter.yaml --scope config/icps/<icp>/scope.yaml
```

The check must be clean before anything runs. Put each decision in a comment next to its value,
with who made it and when.

## Versions: read only the current tabs

When the workbook has versioned tabs (`Seller JTs` and `Seller JTs v2`), use the newest only;
**V2 always beats V1** (see `references/versions_and_prior_runs.md`). Ask which file is current: a
fresh download may be newer than the copy beside the scrape. When the newest version lacks a
tab, ask. Don't fall back to the old one silently.

## What usually lives where (Terrapinn ICP workbooks, 2026)

| icp.yaml field | Usual tab / column | Check with the user |
|---|---|---|
| `sell.titles` | `Seller JTs` → JOB TITLES | Is there a second title column (e.g. Seamless: "JOB TITLES - to find at non sector specific companies")? Does it apply to every seller, or only some companies? |
| `exclude_titles` | `Seller JTs` → an EXCLUDE column, or a separate list from the user | Same list for buy side? (Highways: one list for all queries) |
| `seniority` | `Seller JTs` / `BuySide ICP` → SENIORITY (free text, e.g. "Any seniority. Exclude Intern/Trainee, Assistant") | Translate to Clay slugs and read the translation back |
| `sell.categories` → countries | `Products_Services` → Top level + COUNTRIES | Top level only, or Sub level too? Countries per Top level, or one list for all? |
| `buy.categories` → titles | `BuySide ICP` → INDUSTRY + JOB TITLES (rows grouped under an industry) | Rows with no industry in col A belong to the industry above them; "MORE INFO" rows are notes, not titles |
| `buy.categories` → countries | `BuySide ICP` → Countries | Per industry, or one default |
| match type | MATCH TYPE column ("Contains" / "Job Title Must Match Exactly") | The default search is word-level contains. Exact titles that are safe in contains mode (CEO, Managing Director) go in `titles`; ones it would flood ("Vice President", bare "Manager") go in `<side>.exact_titles` for a separate exact-mode search (Find People v2 has `job_title_mode` exact, Oct 2026). Per-side / per-category exclusions: `<side>.exclude_titles`, `buy.categories.<label>.exclude_titles` (Mining: buy excludes "Sales Manager", sell searches it) |
| per-event overrides | `Competitor` → JOB TITLES / COUNTRIES per event (e.g. "TOP 10 PER BANK - exact match, UAE only") | Does the user want per-event personas? That needs a per-row `Countries Override` and possibly a separate search — say so and ask, don't silently flatten |
| countries per side | `Geo_Scope` → a SELL and a BUY column per country (Core / Yes / Secondary / Parked / blank) | Geography by the PERSON's location or the company's? (Mining: person only.) Search every non-blank, non-Parked country and tag the tier (`post_filter.yaml` geo_tier), or only some tiers? Is a compliance row (sanctioned, Parked, "LinkedIn only") a search filter, a company drop (`scope.yaml drop_if`) or an outreach rule (`linkedin_only_countries`)? Blank buy cells for big markets: ask, don't fill |
| seller personas | `Seller JTs` → two lists (Mining: Bookers / Stand staff) | Both? Tagged, or two searches? Which wins on overlap (Mining: Booker)? One search, tagged after, pays once for the shared keywords |
| events in scope | `Competitor` → Tier, status (in / Cut), notes | every Cut event is left out of `scope.yaml events`; every note is a rule, so read each one back ("PDAC: keep MEA miners and governments only") and confirm the literal reading |
| company drops | `Products_Services` notes ("no data build for sanctioned HQs"), the OUT-OF-ICP list | `scope.yaml drop_if` / `drop_types`, and the re-check brief's OUT codes |

Category keys in `icp.yaml` must be **exactly** the labels the classifier prompt emits. Build the
prompt's closed lists from the same tabs, in the same words.

## Clay seniority slugs

`founder, owner, board-member, partner, c-suite, vp, director, head, manager, senior, mid-level,
entry, intern`. "Any seniority, exclude Intern/Trainee/Assistant" became, on Highways, every slug
from `founder` to `mid-level` plus `Assistant`, `Trainee`, `Intern` in `exclude_titles`. An
unknown slug is dropped silently by Clay, so `icp_mapping.py check` refuses it.

`seniority_groups` must partition `seniority` (Highways: A = founder…director, B = head…mid-level;
Mining: A, B1 = head + manager, B2 = senior + mid-level). They exist because Find People returns at
most 10 people per search: a company that returns exactly 10 is searched once per group, for up to
10 per group. How deep to go is the user's call (`references/people_rules.md`).

## Things that went wrong on Highways — check each one

- **Exact-match titles that never reached the search.** The sheet listed COO / CTO / CEO as
  "must match exactly"; only the spelled-out forms were searched, so a person titled "COO" was
  missed. Put abbreviations AND spelled-out forms in the title list.
- **Exclusion vs title collisions.** Asking for "Data" while excluding "Data" returns nobody for
  that term. `icp_mapping.py check` blocks exact collisions and warns on partial ones ("Finance"
  inside "Project Finance") — show the warnings and let the user decide.
- **Word-level contains.** "PR" does not match "Project"; "Program Management" does not match
  "Programme Management" — add both spellings.
- **A title row the sheet added later.** Re-diff the workbook against icp.yaml whenever the user
  says the ICP changed (`inspect_inputs.py xlsx` again, compare per industry, report gaps).

## And on Mining

- **YAML booleans.** An unquoted `Yes:` key (a geo tier) parses as `True`. Quote it: `"Yes":`.
- **Commas in labels.** "Processing, Refining & Smelting" in an unquoted flow list
  (`[a, b]`) becomes two labels. Quote it. The check catches both traps.
- **Clay's country spellings.** The sheet's "Côte d’Ivoire" (curly apostrophe) matches nobody;
  Clay's is "Côte d'Ivoire". `assets/clay_countries.json` is Clay's list, and the check refuses
  anything else.
- **One exclusion list doesn't fit both sides.** The buy side excludes "Sales Manager" while the
  sell side searches for it. Use per-side `exclude_titles`, and per-keyword exclusions in
  `post_filter.yaml`.
- **A cross-cutting exact title can be a group's own contains keyword** ("Mine Manager" for
  miners). Put it in `post_filter.yaml buy.contains_too`, so "Underground Mine Manager" passes for
  that group.
- **Stray rows.** A row pasted from another vertical (Mining's P&S tab had a rail line in it):
  point it out and ask; never search it.

## Read back before building

Print `icp_mapping.py check icp.yaml` and summarise it for the user in plain words: how many
titles per side, which categories, which countries, which seniority, the warnings. Get an explicit
yes. A wrong title list costs nothing to fix before the run and a full re-run after it.
