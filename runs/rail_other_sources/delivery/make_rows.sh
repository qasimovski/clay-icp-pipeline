#!/bin/bash
# Clay Tables rows for the Rail Other Sources review copies (paste into the ICP's Clay Tables tab). Run in WSL.
export CLAY_BIN=$HOME/.config/clay/bin/clay-0.1.17-linux-x64
D="/mnt/c/Users/qasim/terrapinn/scrapers_rail/Comparative/clay/other_sources/delivery"
cd /mnt/c/Users/qasim/terrapinn/clay-icp-pipeline/.claude/skills/event-icp-clay-workflow/scripts
python3 deliver.py rows --plan "$D/plan.json" --state "$D/import_state.json" --prefix Rail --workspace "$CLAY_WORKSPACE_ID" --gtm "$REVIEWER" --no-entity-suffix --out "$D/Rail OS - Clay Tables rows (paste into Sheets)"
