# Sensitive data — what's not in this repo, and why

This repo holds the reusable **process**: docs, a placeholder-driven build
template, config schema, and reference automation. It deliberately excludes
real business/competitive data. If you're setting this up fresh, this is
what you need to supply locally (all gitignored — see `.gitignore`).

## Excluded entirely — stays on your machine, never committed

| What | Why | Where it would normally live |
|---|---|---|
| Real scraped event CSVs (`*_normalized.csv` per event, master rollups) | Actual scraped competitor data — not this repo's concern; lives in your scraper output tree. | Set `CLAY_PIPELINE_SCRAPERS_ROOT` (see `automation/README.md`) to point at it. |
| The real ICP playbook workbook (`<Vertical>_Playbook_ICP_-_Tiered.xlsx`) | The proprietary tiering/targeting logic for a given vertical — business-sensitive. | Keep locally; `config/icps/<icp>/icp.yaml` is the *extracted* structured form of what you need from it. |
| Real lookup CSVs (`fit_lookup.csv`, `seller_sublevels.csv`, `seller_contact_titles.csv`, `buyer_contact_titles.csv`) | These are a row-level export of the same proprietary ICP targeting logic in the xlsx (which job titles/segments are targeted, tiered) — excluding the xlsx but including its full CSV export would be inconsistent. | `config/icps/<icp>/lookups/*.csv`, gitignored. Only `.example.csv` versions (header + a few illustrative rows) are committed, to document the schema. |
| Clay workspace identifiers (workspace URL/ID, folder IDs) | Identifies your specific live Clay account, not the reusable process. | `config/local.yaml`, gitignored — copy `config/local.yaml.example` and fill in your own. |
| Automation run artifacts (screenshots, logs, state files, the command-queue directory) | Runtime output of a specific automation run, not source. | `automation/**/shots/`, `rollout_logs/`, `rollout_state*.json`, `queue/cmd_*` — all gitignored. |

## What IS committed, and why it's safe

- The **classifier taxonomy** (Buyer/Seller category labels and tie-break
  rules) inside `config/icps/<icp>/icp.yaml` — these are the same labels
  already fully reproduced in the Claygent prompt text, which is explicitly
  part of the "process" the user wants documented. No separate exposure is
  created by also having them in the config file.
- The `.example.csv` lookup files — header row plus 2–3 illustrative rows,
  enough to show the schema a real lookup file needs, without exposing the
  full real targeting list.

## Before you commit anything

Run `git status` and eyeball it — confirm no `*.xlsx`, no real (non-`.example`)
lookup CSVs, and no `shots/`/`rollout_logs/`/`rollout_state*.json`/`queue/cmd_*`
files are staged, every time, not just on the first commit.
