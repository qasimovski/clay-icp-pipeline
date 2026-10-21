# From the ICP workbook to icp.yaml

Every event's workbook is laid out a little differently, so the mapping is proposed, shown and
confirmed — never assumed. Run `scripts/inspect_inputs.py xlsx <workbook>` and read each tab's
header row and first rows before proposing anything.

## What usually lives where (Terrapinn ICP workbooks, 2026)

| icp.yaml field | Usual tab / column | Check with the user |
|---|---|---|
| `sell.titles` | `Seller JTs` → JOB TITLES | Is there a second title column (e.g. Seamless: "JOB TITLES - to find at non sector specific companies")? Does it apply to every seller, or only some companies? |
| `exclude_titles` | `Seller JTs` → an EXCLUDE column, or a separate list from the user | Same list for buy side? (Highways: one list for all queries) |
| `seniority` | `Seller JTs` / `BuySide ICP` → SENIORITY (free text, e.g. "Any seniority. Exclude Intern/Trainee, Assistant") | Translate to Clay slugs and read the translation back |
| `sell.categories` → countries | `Products_Services` → Top level + COUNTRIES | Top level only, or Sub level too? Countries per Top level, or one list for all? |
| `buy.categories` → titles | `BuySide ICP` → INDUSTRY + JOB TITLES (rows grouped under an industry) | Rows with no industry in col A belong to the industry above them; "MORE INFO" rows are notes, not titles |
| `buy.categories` → countries | `BuySide ICP` → Countries | Per industry, or one default |
| match type | MATCH TYPE column ("Contains" / "Job Title Must Match Exactly") | The workflow search is word-level contains for every title; exact titles also go into `rescue_titles` |
| per-event overrides | `Competitor` → JOB TITLES / COUNTRIES per event (e.g. "TOP 10 PER BANK - exact match, UAE only") | Does the user want per-event personas? That needs a per-row `Countries Override` and possibly a separate search — say so and ask, don't silently flatten |

Category keys in `icp.yaml` must be **exactly** the labels the classifier prompt emits. Build the
prompt's closed lists from the same tabs, in the same words.

## Clay seniority slugs

`founder, owner, board-member, partner, c-suite, vp, director, head, manager, senior, mid-level,
entry, intern`. "Any seniority, exclude Intern/Trainee/Assistant" became, on Highways, every slug
from `founder` to `mid-level` plus `Assistant`, `Trainee`, `Intern` in `exclude_titles`. An
unknown slug is dropped silently by Clay, so `icp_mapping.py check` refuses it.

`seniority_groups` A/B must partition `seniority` (Highways: A = founder…director,
B = head…mid-level). They exist because Find People returns at most 10 people per search: a company
that returns exactly 10 is searched once per group, for up to 20.

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

## Read back before building

Print `icp_mapping.py check icp.yaml` and summarise it for the user in plain words: how many
titles per side, which categories, which countries, which seniority, the warnings. Get an explicit
yes. A wrong title list costs nothing to fix before the run and a full re-run after it.
