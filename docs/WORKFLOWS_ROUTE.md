# The Workflows route — classify → ICP map → find people → email

How the Highways **Competitive Events** scope was taken from 7,418 scraped
exhibitor/sponsor rows to 16,391 deduped, ICP-matched people ready for the work
email waterfall, using Clay **Workflows** driven from the CLI rather than
Playwright against the column UI.

Written to be re-run. Products & Services, Buyside P&S and Other Sources are the
next scopes; §8 is the only part that changes between them.

> Read `PIPELINE_ARCHITECTURE.md` for the 14-step column pipeline this route
> sits beside, and `PEOPLE_PASSES.md` for the query-correctness rules. This
> document does not replace either — it records a second way through, and when
> to prefer it.

---

## 1. When to use this route at all

**Use the Workflows route when the company's ICP category is not already a
property of the table it lives in.**

That was the whole problem in Competitive Events. The 67 CE tables hold scraped
exhibitors and sponsors from 39 events, mixed together — a paving contractor
next to a recruitment agency next to a national roads authority. Nothing on the
table says which is which, or which side of the market it sits on. Somebody has
to decide, per row, before any people search can be aimed.

The alternative — adding `Side`/`Classification` columns to 67 tables through
the column UI and segmenting by view filters, the way Labs did — needs
per-table column creation on a UI that was rebuilt mid-2026, and drives
segmentation through view filters that must be set and unset around every
search.

**Do not use it when the category is known by construction.** A Products &
Services workbook *is* a category; every company in its `Companies` table is
that category, on the sell side, by the way the table was built. Classifying
them again would be paying a model to tell you what the folder name already
says. See §8.

### What Workflows cannot do

Load-bearing, and it shapes everything below:

- **A workflow cannot create a Clay table, or write rows into one.** `clay
  tables` is read-only; there is no CSV-import or column-create API. Output
  comes back as a result file, and the deliverable is a CSV.
- **A bulk run always enters at the trigger.** There is no "start at node N in
  bulk" — `clay workflows nodes test` runs a single node for a single row. This
  is why the people search needed its own workflow (§3).
- **A workflow is not runnable from the CLI until it is registered as a
  routine.** `clay routines create workflow <wfId>`. Before that, `runs start`
  answers `not_found`, which reads like a typo and is not.

---

## 2. The shape

```
CLASSIFY (workflow 1)                          PEOPLE (workflow 2)

Manual trigger                                 Manual trigger
  ↓                                              ↓
Build Company Record                           Build Company Record
  ↓                                              ↓
Has Domain? ──no──→ Skipped - No Domain        ICP Mapping
  ↓ yes                                          ↓
Read Is New                                    Searchable? ──no──→ Skipped - Unmapped
  ↓                                              ↓ yes
Is New? ──no──→ Skipped - Not New              Find People
  ↓ yes                                          (cpj-find-lists-of-people)
Highways Registrar  (Claygent, GPT 4.1 mini)
  → Side + Classification
```

Node types: `trigger`, `code`, `conditional`, `agent`, `tool`. The people nodes
exist in **both** workflows — see §3.

**Two gates, both upstream of anything paid.** `Has Domain?` and `Is New?`
between them removed 1,929 of 7,418 CE rows before a single model call.

`Read Is New` is a **pure code node**. It reads the value the Supabase ledger
column already wrote onto the table; it never calls the ledger itself. That
matters: re-running the workflow does not re-check or re-claim anything.

---

## 3. Why the people search is a second workflow

The people nodes were added to the classification workflow first, deliberately —
that is where they can be tested against a freshly classified row, and it avoids
duplicating the gates. But the **bulk run** could not use them, because a bulk
run enters at the trigger and that trigger leads into the Registrar. Replaying
it over 4,547 already-classified companies would have:

- re-paid for classifications already held in the shipped CSV, and
- risked disagreeing with that CSV, because the model is not deterministic.

So a second workflow takes `Side` and `Classification` as **trigger inputs** and
starts at `ICP Mapping`. It duplicates no logic: both `ICP Mapping` nodes are
generated from the same YAML by the same `extend_v2_people.build_icp()`, and
both builders carry `--update` to regenerate in place. They cannot drift.

---

## 4. Configuration — what is the source of truth

| What | Where | Notes |
|---|---|---|
| Classifier prompt + closed label list | `automation/cleanup/ce_classification_prompt.py` | The prompt verbatim, plus `WORKFLOW_PROMPT` with its `{{...}}` interpolation |
| Sell-side persona, seniority, exclusions | `config/icps/<icp>/seller_people.yaml` | One shared persona across every category |
| Buy-side persona per industry | `config/icps/<icp>_buyside/people.yaml` | Industry, titles and countries in one place |
| Countries, per category | `config/icps/<icp>/products_services.yaml` | Same source the company search used, so people and companies cannot drift on geography |
| Job-title exclusions | both YAMLs, mirrored; `ce_nodes/exclude_titles.json` is the machine copy | 54 terms |
| Live workflow/node ids | `automation/cleanup/ce_nodes/people_workflow.json` (gitignored) | Written at build time so the runner never hard-codes an id |

**The YAML is the source of truth and the node is generated.** Never hand-edit
the `ICP Mapping` node in Clay — edit the YAML and re-run the builder with
`--update`.

### The exclusion list, and the two rules on top of it

54 terms, applied to every query on both sides. Two refinements, both of which
came from real damage:

1. **A term that collides with an ICP title is removed, not shipped.** Asking
   for a title and rejecting it in the same predicate returns nobody for that
   term. `ps_buyside_people_config.validate()` fails loud on this. Eight of the
   57 terms originally supplied were dropped for collisions — including three
   (`Legal`, `Counsel`, `Consultant`) whose only clash was with a category the
   CE classifier can never emit but which is live on the **P&S** route. One
   config, two routes: check both.
2. **On a tie, an EXACT senior title wins.** Clay's `job_title_exclude_keywords` is a
   *soft* filter — 113 of 16,586 returned people still carried an excluded term.
   They are removed locally at export, except where the title also matches an
   **exact** title: "Chief Executive Officer - Software" is still a CEO.

The rescue list is `titles_exact` only — never the `contains` list. Measured
2026-09-13 and the reason the narrowing exists: rescuing on everything the
search asked for saved **528 of 528** buy-side P&S rows carrying an excluded
term, because those lists are function words (`Operations`, `Environment`,
`Procurement`, `Content`) that appear in most corporate titles — "People
Operations Manager" was surviving on `Operations`. Restricted to exact titles it
rescues 20 there and 10 on CE, keeping the senior compound titles and correctly
dropping Transport Coordinator, Marketing Assistant and Commercial Finance
Manager. `people_export_rules.rescue_titles()` is the single source.

---

## 5. Money, and where the gates are

Per row of scope:

| Step | Cost | Gated by |
|---|---|---|
| Ledger check (Supabase HTTP column, on the table) | 1 action | `Domain` non-empty |
| Registrar classification | 1 action + 1 data | `Has Domain?` **and** `Is New?` |
| Find People (up to 10 people) | 1 action | `Searchable?` — persona **and** countries resolved |
| Work email waterfall | ~1.2 credits/person | applied per person, §7 |
| Validate Email | ~1/person | `!!{{WORK EMAIL}}` |

**The default is not dry.** Unlike the Playwright passes, whose rule is
configure-but-never-trigger, a bulk run is atomic and resumable, so a bare
invocation of the rollouts spends. `--dry-run` / `--plan` show the plan.

**Skip-by-absence beats skip-by-rule.** The feed builder writes only searchable
rows, so a company that should not be searched is not in the file at all and no
later mistake can reach it.

---

## 6. Running it

```bash
# ---- classify -------------------------------------------------------------
python build_highways_ce_manifest.py              # scope: tables to cover
python ce_classification_rollout.py --list        # scope + eligible, no spend
python ce_classification_rollout.py --dry-run
python ce_classification_rollout.py --start-only  # fire every table's bulk run
python ce_classification_rollout.py --collect     # download results
python audit_ce_classification.py                 # fail-loud read-back

# ---- deliverable ----------------------------------------------------------
python export_ce_classification.py                # per-event CSV, gates re-applied
python combine_ce_tables.py                       # one combined CSV

# ---- people ---------------------------------------------------------------
python ce_people_mapping.py --report              # coverage: what maps, what doesn't
python build_ce_people_feed.py                    # searchable companies only
python extend_v2_people.py                        # people nodes on workflow 1
python build_ce_people_workflow.py                # workflow 2 (bulk entry point)
#   then: clay workflows publish <wf>; clay routines create workflow <wf>
python ce_people_rollout.py --plan                # chunk sizes, no spend
python ce_people_rollout.py --limit 1             # ONE chunk first, always
python ce_people_rollout.py --start-only
python ce_people_rollout.py --collect
python export_ce_people.py                        # one row per person

# ---- email handoff --------------------------------------------------------
python build_clay_email_import.py                 # full + 50-row pilot, Clay-ready
```

**Pilot before the fleet, every time.** The 60-row sample before the CE people
run cost 60 credits and established a 60% hit rate and 5.8 people per hit, which
is what made a 4,547-credit run a decision rather than a hope. `--limit 1` on
the first chunk then proved the bulk path end to end before the other 17 fired.

### Re-entry

State is `state_io` (atomic, fails loud). A unit is done only when **state says
so AND its result file is on disk** — a state file that outlived its results
would otherwise mark work complete that cannot be exported. A heartbeat lock
(`LOCK_STALE_SECONDS = 300`) keeps two workers apart; intent-first writes
(`{"status": "starting"}` *before* the call) mean a kill between start and the
id write shows as `starting`, not as untouched work. Both exist because
overlapping workers double-charged 114 rows on the classification pass.

---

## 7. Output contract

Three files, and they reconcile exactly:

```
Highways CE - people.csv              16,365   the deliverable, one row per person
Highways CE - people - excluded.csv      103   removed by the title rule, term named
Highways CE - people - duplicates.csv    118   removed as duplicate profiles
                                      ------
                        people found  16,586
```

The same three-file contract holds for every scope. Final, 2026-09-14:

| Deliverable | delivered | excluded | duplicates | = found |
|---|---|---|---|---|
| `Highways CE - people.csv` | 16,365 | 103 | 118 | 16,586 |
| `Highways P&S - people.csv` | 62,739 | 3,695 | 517 | 66,951 |
| `Highways BuySide P&S - people.csv` | 7,840 | 509 | 64 | 8,413 |

`people_export_rules.reconcile()` asserts each line and refuses to write a file
that loses people silently.

**THE THREE SHEETS STAY SEPARATE** (user, 2026-09-13). Dedupe therefore stops at
each file's edge. Measured across all three: CE∩P&S 49, CE∩BuySide 5,
P&S∩BuySide 7, none in all three — 86,944 rows for 86,883 distinct people, an
overlap of 0.07%. Small enough that merging would buy nothing; report it, do not
act on it.

Nothing is dropped silently — every removed row is written out with the reason.
Companies that returned nobody are reported by outcome (`No Profile Found`, `all
people excluded`, `all people already counted elsewhere`), not folded together.

**Dedupe keys on the LinkedIn `/in/<slug>`**, the same normalisation the
Supabase people ledger's `normalize_linkedin` uses, so this file and the ledger
agree. First occurrence in feed order wins.

### The email handoff

`build_clay_email_import.py` renames three columns so the waterfall card
prefills instead of being mapped by hand — the step (h) failure mode is a silent
mis-map, not an error:

    Person Name  → Full Name       (required)
    Domain       → Company Domain  (required)
    Company Name → Name            (optional; Enrow and Wiza use it)

`LinkedIn URL` is left alone on purpose: the function declares that input as
`Linked In Profile` while the card was observed showing `Social Profile URL`.
No column name matches both. Map it by hand; only Wiza consumes it.

**The waterfall** — `TERRAPINN find Work EMAIL`, a custom Clay *function*, 1.2
credits/run, 9 steps merging into one `WORK EMAIL` column. Order as read from
the live function on 2026-09-13:

| 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 |
|---|---|---|---|---|---|---|---|---|
| LeadMagic\* | Icypeas | Kitt | Enrow | Findymail | Wiza † | Prospeo\* | Hunter | Datagma |

\* catch-all results ON  † Clay marks this step deprecated in its own description

Read it yourself rather than trusting this table — it is workspace config and
can change under you:

```bash
clay functions list --limit 100                  # find the function's table id
# then the MCP surfaces_read (function) returns each step with its
# waterfall_position="n (out of 9)" and package name
```

**Running it from the CLI needs write access to the function's table**, which a
user who did not create it does not have (`routines create` → `auth_forbidden`,
then `runs start` → `not_found`). The fallback, and the route every other
vertical used, is to import the CSV into a Clay table and add the waterfall
column through the UI — `RUNBOOK.md` step (h).

Beware the near-namesakes: `Work Email - Waterfall - Qasim` and `Work Email
Waterfall` are **merge-only** functions with zero provider steps — they pick a
winner from nine results computed elsewhere and cannot find anything. `Work
Email` is Clay's stock routine, not this waterfall. Do not substitute either.

---

## 8. What changes for the next three scopes

Everything in §§2–7 holds. What differs is only **whether classification is
needed**, and where the company feed comes from.

### Products & Services (sell side)

> **Already built, 2026-08-14/15** — 23 sell-side and 8 buy-side `Companies`
> tables (66,110 + 11,028 companies), ledger run, `Is New` filtered, and
> `People` tables on 22 of 23 / 6 of 8. Verified against live Clay 2026-09-13.
> The two missing buy-side `People` searches (Major Engineering Contractors,
> Sub-engineering contractors) are the only company-side work outstanding.
> `export_ps_people.py` exports the lot from the CLI, no browser.

**Skip the classifier entirely.** One workbook per category, and every company
in its `Companies` table is that category, on the sell side, by construction.
`Side` is the constant `"Sell Side"` and `Classification` is the workbook's
category name. The workflow reduces to feed → `ICP Mapping` → `Find People`,
which is exactly workflow 2 with no changes at all.

Two routes are then available, and **the existing one is not obviously worse**:

| | ClayQL / UI (`build_ps_people.py`) | Workflows (workflow 2) |
|---|---|---|
| Whole persona in one `experiences.count()` | ✓ | ✗ flat keyword list |
| Company office location, not person location | ✓ | ✗ `location_countries_include` is the **person** |
| `exact_relevance` on bare titles | ✓ | ✗ no branches |
| Per-title seniority (`Operations`, senior only) | ✓ | ✗ dropped |
| Output | a Clay `People` table **and** a CSV | a CSV |
| Driving | DOM automation | CLI, resumable |

The ClayQL route is already built and proven for Highways P&S, honours all four
`PEOPLE_PASSES.md` rules, and yields a Clay table that the email waterfall runs
on natively. **Prefer it here.** The Workflows route earned its place in CE by
solving a problem P&S does not have.

### Buyside P&S

Same as above with `Side = "Buy Side"` and the category being a `BuySide ICP`
industry, so the persona comes from `config/icps/<icp>_buyside/people.yaml` and
the countries from that side's `products_services.yaml`. `build_ps_people.py
--icp <icp>_buyside` already does this.

One thing to fix first: that config still carries categories the rewritten sheet
dropped. `Finance, Investment, Legal` is not in the classifier's closed list but
**is** live in `products_services.yaml`, which is exactly the collision that
forced three terms out of the exclusion list (§4). Reconcile the config to the
sheet before running, or the mismatch keeps costing.

### Other Sources

**This one does need the classifier.** Its workbooks are hand-curated and of
mixed provenance — no folder name says what a company is — so it is the CE
situation again. Run the full two-workflow route.

Its scope is a hand-curated JSON of live table ids, not an entity/ICP concept —
regenerate it with `list_other_sources_workbooks.py` rather than trying to
retrofit `--entity` onto it (CLAUDE.md, "Three ways in, not one").

### Reusable regardless of route

`ce_people_mapping.resolve()`, `extend_v2_people.build_icp()`, the 54-term
exclusion list and its two rules, `export_ce_people.py`'s exclusion/dedupe/
reconciliation logic, `build_clay_email_import.py`, and every trap in §9.

---

## 9. Traps — the ones that fail silently

Ordered by how much they cost when missed.

| Trap | Symptom | Guard |
|---|---|---|
| **Bare `#INPUTS#` in the prompt** | Model classifies with no data and still answers confidently — Stucchi came back "Road Construction & Civil" | Interpolate `{{name}}` etc.; `WORKFLOW_PROMPT` does |
| **Missing `Is New?` gate** | Already-worked companies re-enter the funnel and re-spend it | Gate before the agent node; string-coerce the value |
| **`outputSchema` write shape** | `nodes get` returns it expanded as JSON Schema; writing that back is rejected | Write FLAT `{name: {type, description}}` |
| **Edges are not implied by `inputSchema`** | Graph validates as "trigger not connected", or nodes silently orphaned | Wire every edge with `nodes update {"incomingEdges": [...]}`; branch edges carry `ruleId` |
| **`workflows create` makes an EMPTY workflow** | No trigger, no nodes; builder fails looking for a trigger node | Create the trigger first — that is what produces the trigger *node* |
| **Workflow not registered as a routine** | `runs start` → `not_found` | `clay routines create workflow <id>` |
| **`identifiers_only: true`** | Payload truncated to name/title/URL at source; the other 33 fields are unrecoverable without a re-run | Decide before the fleet run, not after |
| **Exclusion keywords are a soft filter** | Excluded titles come back anyway | Re-apply locally at export |
| **Clay CLI binary picked lexicographically** | `clay-0.6.0` beats `clay-0.16.0` → `upgrade_required` → empty read → every table "already done" | `clay_bin.py` parses the version |
| **Audit paging `if not d: break`** | Undercounts and reports success — 3,552 instead of 5,489 | Retry, raise, and self-check `paged == rowCount` |
| **Feed is domain-unique, not company-unique** | Same firm under two domains returns the same people twice | Dedupe on the profile slug at export |
| **Overlapping workers** | Double charge; `Get-Process python` shows nothing because the killed shell's child survived | Heartbeat lock + intent-first state |
| **Stale run condition from a template** | Carried over as a raw `!!{{f_…}}` that resolves to nothing → gate dead, column runs on every row | Always retype it |
| **CSV import "Continue" button** | Table created with its header and **0 rows**; importer reports success; each retry adds `Sponsors (2)`, `(3)`, `(4)` beside the broken original | Commit via `stage-display-footer`, never "Continue"; `_wait_for_import_ready` waits for the parse |
| **Delivery plan written in WSL** | `/mnt/c/...` paths; Chrome's file chooser cannot open them, so the workbook and table are created and only the rows are missing | `import_ce_delivery_tables.native_path` + an up-front existence check |

### The three delivery folders

The review copies go to their own folders, one workbook per event/category, and
the **CSV filename is the table name** — Clay names an imported table after the
file, and renaming afterwards is manual UI work.

| Scope | Split | Plan | Importer | Shape |
|---|---|---|---|---|
| Competitive Events | `split_ce_event_tables.py` | `ce_event_tables_plan.json` | `--delivery ce` | 39 workbooks, 144 tables |
| Products & Services | `split_ps_delivery_tables.py --scope ps` | `ps_delivery_tables_plan.json` | `--delivery ps` | 23 workbooks, 45 tables |
| Buyside P&S | `split_ps_delivery_tables.py --scope bs` | `bs_delivery_tables_plan.json` | `--delivery bs` | 8 workbooks, 16 tables |
| Other Sources | `split_os_delivery_tables.py` | `os_delivery_tables_plan.json` | `--delivery os` | 1 workbook, 3 tables |

Other Sources is ONE workbook because it has no natural unit: it is a single
deduped table whose `Source Name` is a semicolon-joined list of every directory
a company appeared in (77 distinct values over 3,047 companies), so splitting on
it yields neither clean names nor disjoint groups. Its companies are gated on
`Is New` — verified, not assumed: all 697 company domains behind the people fall
inside that set and none outside it.

Folder ids live in gitignored `config/local.yaml` as
`highways_{,ps_,bs_,os_}delivery_folder_id`. Repair empties and duplicates
with `repair_delivery_empties.py --delivery <x>` (plan-driven; it refuses to
delete a table that holds rows unless it is a numbered duplicate), then
re-run the importer — it is re-entrant and confirms by row count. Verify with `verify_ce_delivery.py`,
which asks Clay rather than the state file — the import's own state records what
it *believed* it did, and the two empty-table traps above are exactly the cases
where that belief is wrong.

**This machine has ~7.7 GB of RAM and Chrome settles around 1.6 GB.** Long
import runs die with `Connection closed while reading from the driver` or a node
`EPIPE` once free memory drops under ~1.5 GB, and a wedged Chrome serves the
signed-out page — which reads as "not logged in to Clay" even though the session
is fine. Recycle Chrome (`restart_clay_chrome.ps1`) and resume; the importer is
re-entrant and skips tables whose row counts already match.

---

## 10. Baseline numbers

Sanity checks for the next scope — if a new run is wildly off these, stop and
look rather than pressing on.

| | |
|---|---|
| CE rows in scope | 7,418 across 67 tables / 39 events |
| Gated out (no `Domain`, or not `Is New`) | 1,929 (26%) |
| Classified | 5,489, zero blank outputs, 67/67 tables |
| Unmapped after classification | 942 (17%) — overwhelmingly `All other industries`, inspected and 89% genuinely outside the ICP |
| Searchable companies | 4,547 |
| Returned people | 2,895 (64%) — the rest almost all "No Profile Found", i.e. the domain does not resolve to a LinkedIn company |
| People per company that returned any | 5.7 |
| People delivered | 16,391 |
| Duplicate profiles | 118 (0.71%) |
| Titles caught by the local exclusion pass | 77 (0.46%) |

---

## 11. Known gaps, recorded rather than hidden

- **`location_countries_include` filters the person, not the company office.**
  The action has no company-country parameter. Verified this is not what
  suppresses results (two zero-yield companies returned zero with the filter
  removed), but it makes segments less comparable than the ClayQL route's
  `company.locations.count(...)`. `PEOPLE_PASSES.md` calls that out as a
  silent-failure class.
- **`exact_relevance` and per-title seniority are not applied** on this route —
  the action has no branches. Partly mitigated because the company has already
  been classified into an ICP category before any person is searched.
- **909 `All other industries` + 33 off-list rows are unsearchable.** The label
  is valid but is not a key in either ICP map. Not a taxonomy gap: 89% carry no
  roads language at all.
- **1,134 CE rows have no `Domain` and were never ledger-checked.** Per the user
  (2026-09-12): do not backfill. Note a fallback alone would recover nothing —
  their `Is New` sits at the unchecked default of false, so the gate would
  reject them anyway. Recovering them means re-running the ledger too.
- **90 people share a name and company but have two different profile URLs** —
  a mix of namesakes and duplicate LinkedIn profiles. A URL dedupe cannot catch
  them; they will surface as two rows with the same verified email.
