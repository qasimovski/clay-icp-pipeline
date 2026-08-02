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
| `test_local_settings.py` | live Clay ids reappearing in tracked source |

What is **not** covered: the Playwright UI interactions themselves (selector
drift against the live Clay DOM). Those are only verifiable against a real
workspace — use each pass's `--recon` / `--dry-run` for that.
