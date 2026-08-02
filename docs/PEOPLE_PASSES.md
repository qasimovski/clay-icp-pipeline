# People passes — from a filtered company table to a filtered contacts table

The stage between a scored company table and an enrichable contacts table:
filter to companies you have not worked before, expand them into people, then
narrow that people set to the target personas.

Deliberately contains **no table IDs and no live queries** — those are per-event
variables, and per `docs/SENSITIVE_DATA.md` the targeted title/country lists are
the same proprietary targeting logic that `*_contact_titles.csv` is excluded for.
Keep your real queries locally, gitignored. This documents the shape they take
and the correctness rules they must satisfy.

For the query language itself, see `docs/CLAY_QUERY_SKILL.md` — Clay's official
search-query reference (grammar, operators, the full field list and enums).

---

## 1. Filter the company table to new records

Gate on the blocklist result column so only companies never seen before flow
onward. See `blocklist_ledger/` for the dedupe registry that produces it.

Click the `Is New` column header — it sets to *is checked*.

**Verify before continuing.** The filtered row count must look like your new
companies, not the whole table. This is the highest-leverage check in the whole
step: if the filter is not actually applied, everything downstream runs against
the full company set and burns Actions at the full-table rate.

## 2. Find people at these companies

Run the action against the **filtered** view. It creates the People table.

Confirm the new table inherited the filtered company set rather than all rows —
same check as step 1, one level down.

## 3. Apply the persona query

Switch the People table to query mode and paste that workbook's query.

Each workbook has its own query because the persona and the per-company cap
differ by segment. What they share is the shape:

```
select from people
where experiences.count(
    is_current = true
    and seniority in (<seniority enum values>)
    and job_title contains (<title keywords>)
    and company.locations.count(country_name in (<target countries>)) >= 1
  ) >= 1
  and clay.filter_to_companies(@table("<table>:<view>:<filter>"))
limit <N> by clay_company_id
```

## 4. Gate downstream enrichment

Every paid column runs only when the blocklist said the company was new. Filters
and conditional runs cost 0 Actions, so gating is free and is where the saving
actually lands. An ungated pipeline pays for the ledger without benefiting.

---

## Query correctness rules

Four mistakes found in a live nine-workbook set. All four were silent — the
queries ran and returned plausible results.

### Keep the whole persona in ONE experience predicate

Splitting the title filter and the company-country filter across two separate
`experiences.count(...)` clauses lets **two different jobs** satisfy them
between them. Someone who is a CEO at company A and holds any role at company B
with an in-region office passes, though no single role meets both conditions.

```
WRONG:  experiences.count(is_current = true and job_title ...) >= 1
        and experiences.count(is_current = true and company.locations ...) >= 1

RIGHT:  experiences.count(
            is_current = true
            and job_title ...
            and company.locations ...
        ) >= 1
```

### Use `contains` for job titles, not `in`

`in` is exact whole-value matching. `job_title in ("CEO")` matches only the
literal string `CEO` — it misses `CEO & Founder`, `Group CEO`, `CEO, EMEA`.
Compound titles are the norm at senior level, so this quietly removes a large
share of the intended audience.

`contains` is token-based and catches all of them. Clay's reference also offers
`is_similar_to`, which additionally expands synonyms and abbreviations — better
recall again, at the cost of some precision.

### Always set a per-company cap

Without `limit N by clay_company_id`, one large employer can return an unbounded
number of people, and the cost lands on the enrichment columns downstream.
Choose N by segment: narrower segments justify a higher cap.

### Decide person-location vs company-location once, then be consistent

Two different filters, easily confused:

| Filter | Means |
|---|---|
| `location_country in (...)` at top level | where the **person** lives |
| `company.locations.count(country_name in (...)) >= 1` inside the predicate | where the **employer** has an office |

A UK-based executive at a company with no UK office passes the first and fails
the second. Mixing them across workbooks makes segments non-comparable.

Default to **company office location**: it is the better market-relevance signal
for event targeting, and a person's profile location is often stale or a
different city. Use person location when physical attendance is the constraint.

---

## Verification, in order

1. Filtered company count matches expected new companies — **stop if not**.
2. People table inherited the filtered set, not all companies.
3. Query returns a plausible count for the segment.
4. Spot-check ~10 people: genuinely senior title, employer really in a target
   country.
5. Only then enable paid enrichment columns.

Steps 1 and 2 are where a mistake costs the most, because everything after them
is metered per row.

## Cost note

An HTTP API call consumes **1 Action per row**; formulas and conditional runs
consume none. A 20,000-row table is ~20,000 Actions for a single per-row column,
so the Actions budget — not storage or row limits — is the constraint that binds
first. Confirm per-row metering against your own plan before a large run.
