# Rollout cleanup notes (check at fleet completion)
- ACS Fall: possible stray unnamed Registrar claygent copies (dormant, 0 credits) — earlier duplicate builds.
- Bioprocessing Summit Europe: possible stray "New table"/"New table (2)" tables in workbook from destination-create retries; Buyers split adopted one — verify and delete leftovers.
- All events: blocklist mapping carries locked extras (Country, Postal Code, Resolved Description, Side) — Table 1 registry has extra columns (cosmetic).
- Postal Code imports as numeric on many events -> red failed-cells badge (cosmetic, non-numeric postcodes preserved as failures).
- CMEF: worker collision built columns concurrently — check for duplicate 'Sector Keyword Match'/'Composite Tier' columns on Contacts tables; delete strays.
- FP catch-up pass needed: ~15 events with <2 saved Find People searches (see rollout_logs grep 'FP search saved'); rerun step_find_people for those; watch for duplicate saved-search names.
