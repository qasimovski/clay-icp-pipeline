# Tests

```bash
pip install -r ../requirements.txt
python -m pytest tests/ -q          # from the repo root
```

These cover the **decision logic** that guards credit spend and fleet state —
the parts where a wrong answer costs money or strands a workbook. They are
browser-free: Playwright calls are monkeypatched, so the suite runs in under
a second and touches no Clay account.

| File | Guards against |
|---|---|
| `test_state_io.py` | a truncated/corrupt state file silently reading as empty and re-running paid work |
| `test_run_all_columns_guard.py` | the batch `Run N rows` trigger firing on a table that already ran |
| `test_merge_shards.py` | shard state (incl. the double-charge run log) being lost or stranded on merge |
| `test_row_count_failure.py` | a Clay CLI failure reading as "0 rows" and retiring a workbook |
| `test_column_completion.py` | the table-wide "% completed" banner marking an unfinished column done |
| `test_speakers_precheck_tristate.py` | a failed pre-check latching "already built"/"already run" |
| `test_scope_ordering.py` | shard partitions shifting when a scope file is regenerated |
| `test_worker_queue.py` | torn command reads, two workers on one queue, path escape |
| `test_render_build_prompt.py` | build specs rendering wrong instead of failing |
| `test_blocklist_destination_memo.py` | the destination memo carrying past Clay's source cap |
| `test_workbook_navigation_reuse.py` | redundant workbook loads in two-table passes |
| `test_delivery_plan_paths.py` | a WSL-written delivery plan importing 0-row tables on win32; a speakers table reaching the Clay review copies (speakers go to the Salesforce CSV only) |
| `test_local_settings.py` | live Clay ids reappearing in tracked source |
| `test_people_email_run_guard.py` | the email waterfall re-firing on a table that already ran (the write-ahead log's retry semantics) |
| `test_cpj_leaf_selection.py` | the CPJ People pass binding the People table's own same-named column instead of the company lookup's property — a wrong value, silently, in every row of 143 tables |
| `test_ps_query_config.py` | an EVCharge Products & Services query that Clay narrows *silently* — an `industry`/`country_name` value outside Clay's enum, a 4th `products_and_services` value past Clay's cap of 3, a dropped country, or description/products ANDed instead of ORed. Each one builds a workbook that looks right and imports the wrong companies |
| `test_icp_rerun_rules.py` | an event re-run under a new ICP version reading the OLD keep/drop (V2 must win), a Cut event or a Tier 3 note slipping through, a per-keyword exclusion applied globally, a LinkedIn-only (Germany) person reaching the email file, a flagged title dropped instead of kept for the approver, a person missing from an event their company exhibited at, a speaker-only row landing in a Clay people table, an unquoted `Yes:` or comma label silently changing the config, a Salesforce CSV written under a name that breaks the `Competitive_/Other_sources_/Buy_side_/Sell_side_<Brand>` rule — each one pays for the wrong people, loses the right ones, or hands IT a file it can't place. Also the audiences step: a person already in the ICP's Clay audience going on to paid emails and Salesforce, a longer profile slug (`john-smith-123`) removing a different person (`john-smith`), and a Clay CLI failure reading as "not in the audience" |

What is **not** covered: the Playwright UI interactions themselves (selector
drift against the live Clay DOM). Those are only verifiable against a real
workspace — use each pass's `--recon` / `--dry-run` for that.
