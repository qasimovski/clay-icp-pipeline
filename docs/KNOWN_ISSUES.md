# Known issues

These are carried forward from the live Labs build (Exhibitors + Sponsors,
81 tables across ~60 event workbooks, built 2026-07-07 to 2026-07-09). The
template in this repo is authored to the **corrected/mature** behavior
described below, not to whichever live table happened to be built last —
see each item for what that means for tables already live in Clay.

## 1. Unresolved — needs a Head-of-GTM decision: the "CAB" composite-tier discrepancy

The Tiering Model sheet states Industry=C, JT=A, Country=B (code `CAB`) is
Tier 3. The tier values actually applied in the BuySide ICP — tiered sheet
show all 23 CAB rows as **Tier 2**. These two disagree.

The template (and the live Interphex/mature build) follows the **applied**
Tier 2 value for the 8 combinations currently present in real data, and
falls back to the Tiering Model sheet's stated value for the other 19
combinations not yet present. **This must not be silently resolved** — it
needs a decision from whoever owns the ICP, then a one-line fix to
`config/icps/<icp>/icp.yaml: known_issues.cab_discrepancy` once decided.

## 2. Drift — `Official Domain` conditional gating

The mature (Interphex/Exhibitors) build only runs `Official Domain` when
the domain is still unknown after normalization (`only run if /Website is
empty`). Some live Sponsors-replica tables (built 2026-07-07–09) appear to
be missing this gate, meaning `Official Domain` may be configured to run
unconditionally on rows that already have a domain — wasted config, not
wasted credits, since it's still configure-only either way (nothing was
actually run), but worth tightening before the next rollout.

**Template behavior:** gated, for both entity types
(`config/entity-types/*.yaml: gating.official_domain_skip_if_domain_known: true`).
**Action for existing live tables:** audit and add the gate where missing —
separate from this repo's scope.

## 3. Drift — `Enrich Company` passthrough field set

The mature Exhibitors build passes through a fuller field set (Size, Type,
Industry, LinkedIn URL, Founded, Annual Revenue, Primary Offerings, Company
Specialties, Sub-Industry, Business Stage, Total Funding Amount, Name,
Website, Employee Count). Some live Sponsors tables only pass through a
narrower set (Name, Website, Employee Count) — this looks like unintentional
drift during replication rather than an intentional per-entity-type choice,
since nothing about Sponsors data makes Industry/LinkedIn/Founded/Revenue
irrelevant.

**Template behavior:** the fuller field set for both entity types.
**Action for existing live tables:** audit Sponsors-replica tables and add
the missing passthrough fields if the enrichment data is needed there.

## 4. Drift — fan-out gating on `Side`

The mature Exhibitors build gates the "Send to Sellers"/"Send to Buyers"
actions on `{{Side}} == "Seller"` / `"Buyer"` respectively. Some live
Sponsors-replica tables have these actions ungated (`run_as_button` with no
conditional formula) — meaning the send may fire for the wrong side's rows
if triggered before classification has actually run and set `Side`.

**Template behavior:** gated, for both entity types
(`config/entity-types/*.yaml: gating.fanout_gated_on_side: true`).
**Action for existing live tables:** audit and add the gate where missing.

## 5. As-built deltas from the original Interphex plan (informational, not a defect)

Documented in `docs/examples/interphex-build-spec.md`'s "AS-BUILT" section —
kept here as a pointer since these affect anyone reading the original spec
literally:

- Claygents were built **column-level** per event (GPT-4.1 Mini, 1/row)
  rather than as saved workspace-level Claygents reused across events — the
  automation scripts are the actual reuse mechanism, not Clay's saved-agent
  feature.
- The resolved-domain column was renamed to `Company Domain` to avoid a
  name clash with the imported raw `Website` column.
- `Website` resolution turned out to compile to a free deterministic
  formula, not a paid AI Formula, in this workspace — worth re-verifying if
  Clay changes this column type's behavior.
- Two parallel Clay folder trees existed briefly during the initial build
  (`Labs [2026 - Qasim]` vs `Labs [2026 - Qasim - Fable]`) — a one-time
  consolidation issue from that specific rollout, not a pipeline design
  issue.

## 6. Automation is reference/manual-run, not config-driven (by design, this round)

`automation/build_automation/` and `automation/clay_sync/` are copied over
largely as-is (only path constants adjusted for the new repo location — see
`automation/README.md`). They are **not** parameterized to take an
entity-type or ICP flag and run end-to-end; building for a new entity type
or ICP still means a human following `docs/PIPELINE_ARCHITECTURE.md` and
the rendered build prompt inside the Clay UI (with the Playwright scripts
available as a reference for how the original build was automated). A
future pass could make this fully config-driven — explicitly out of scope
for this iteration.
