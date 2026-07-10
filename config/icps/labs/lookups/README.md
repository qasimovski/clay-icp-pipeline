# Labs lookup CSVs — populate locally

The `.example.csv` files here show the schema only (header + a couple of
illustrative rows). The real lookup files — `fit_lookup.csv`,
`seller_sublevels.csv`, `seller_contact_titles.csv`, `buyer_contact_titles.csv`
— are gitignored (see `docs/SENSITIVE_DATA.md` for why: they're a row-level
export of the same proprietary ICP targeting logic as the xlsx playbook).

To populate them:

1. Export the corresponding sheet from `Labs_Playbook_ICP_-_Tiered.xlsx`:
   - `SellSide P&S – TIERED` → `seller_sublevels.csv`
   - `Seller JTs – TIERED` → `seller_contact_titles.csv`
   - `BuySide ICP – TIERED` → `buyer_contact_titles.csv`
   - The Fit column derived from the taxonomy in `config/icps/labs/icp.yaml` → `fit_lookup.csv`
2. Save each as CSV in this folder with the exact filename above (no `.example`).
3. `automation/build_automation/build_event.py` reads these from
   `CLAY_PIPELINE_ICP_LOOKUPS_DIR` (defaults to this folder) — see
   `automation/README.md`.
