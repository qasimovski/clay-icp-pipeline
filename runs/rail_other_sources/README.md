# Rail Other Sources run (21 Nov 2026)

The scripts that took the Rail ICP's 14 Other Sources directories (supplier directories, UNIFE / UITP / UIC / Alamys
member lists, Railway Gazette's Knowledge Hub) through the same Clay pipeline as the Rail Competitive Events, up to the
marketing manager's review. Copied from the run folder (`scrapers_rail/Comparative/clay/other_sources/`, not in this
repo) with every live Clay id moved to an environment variable and the reviewer's name removed (this repo is public).
The run's decision log (`DECISIONS.md`, "Rail OTHER SOURCES") stays in the run folder.

Several scripts are adapted copies of the Rail CE run scripts (`collect_people.py`, `dl_search.py`, `dl_build.py`) and
of the Submarine OS delivery scripts (`rename_tables.py`, `import_loop.py`, `verify_delivery.py`, `make_rows.sh`);
their docstrings say what changed. Paths point at the run folder layout.

## Order (paid steps marked)

| # | Script | What | Cost |
|---|---|---|---|
| 1 | `build_os_feed.py` | route every OS company: left out when already in the CE run, sanctioned / HOLD dropped, held hosts, no-website names -> finder feed, domains -> classifier feed | free |
| 2 | `run_approved.py finder_*` + `domain_finder/collect.py` | Domain Finder on no-website names; `build_os_feed.py --finder` applies it | 1 action / name |
| 3 | `run_approved.py classify_*` + `classify/collect_os.py` | CE classifier per domain; label check vs the 28 labels; `overrides_os.csv` | 1 action / domain |
| 4 | `people/build_os_people_feed.py` | one Find People row per (domain, side, label); buyers in all five buy columns | free |
| 5 | `run_approved.py people_*`, `people/collect_people.py` | Find People, B1 re-search on capped searches, export | Clay actions |
| 6 | `people/deepline/dl_search.py`, `dl_build.py` | Deepline people on companies with 0-2 people, cap 20, post_filter | free |
| 7 | `people/deepline/dl_portal*.py`, `people/blitz/find_bodies.py` | bodies listed on a whole-government portal: own site, else department-keyword people search; employer filter | free / Blitz records |
| 8 | `run_approved.py enrich_company`, `people/blitz/blitz_company.py` | Enrich Company once per domain; Blitz for companies with no data | 1 action / domain |
| 9 | `delivery/build_decisions.py` -> skill `deliver.py split` -> `delivery/rename_tables.py` -> `delivery/import_loop.py` -> `delivery/verify_delivery.py` -> `delivery/make_rows.sh` | per-directory Clay review workbooks (free CSV import), then the Clay Tables rows | free |

## `run_approved.py`: the approval driver

Clay asks a human to approve every paid CLI run. The driver approves only requests for one job the user OK'd (job list
in the file, each with the user's words and date), for that job's workflow, action `routines.run-batch`, with the
chunk's exact item count. Two guards were added after real overspends in this run
(`tests/test_rail_os_run_approved.py`):

- **scope check**: `bulk_run.py --plan` writes chunk files into `--work`, and a later call with the same `--work`
  reuses them and ignores `--feed`. A 250-row pilot planned into the full feed's folder ran all 3,150 rows. The driver
  now refuses unless every id in `<work>/feeds` is in the approved feed.
- **one retry chunk**: `--retry-failed` writes a new retry chunk on every call, so rows that always fail (government
  portals Clay rejects) were re-sent 53 times. In retry mode the driver approves one retry chunk, then stops.

Environment: `RAIL_FINDER_WF`, `RAIL_CLASSIFIER_WF`, `RAIL_PEOPLE_WF`, `ENRICH_COMPANY_WF` (unset = no request ever
matches, so nothing is approved), `RAIL_OS_FOLDER_ID` (import), `CLAY_WORKSPACE_ID` and `REVIEWER` (`make_rows.sh`).
