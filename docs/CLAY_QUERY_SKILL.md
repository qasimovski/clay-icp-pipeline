---
name: clay-search-query
description: Convert natural-language audience descriptions — including job postings, briefs, and ICP notes — into Clay search queries over people, companies, and jobs. Use when the user wants to write or refine a Clay search query, or describes leads, companies, candidates, or job postings they want to find in Clay.
---

<!-- Refreshed 2026-11-05 from `clay searches query-mode reference` (CLI 1.12.0). Re-run that command and replace the body below the frontmatter to update. -->
---
name: clay-search-query
description: Convert natural-language audience descriptions — including job postings, briefs, and ICP notes — into Clay search queries over people and companies. Use when the user wants to write or refine a Clay search query, or describes leads, companies, or candidates they want to find in Clay.
---

# Clay search query reference

Use this reference to author a Clay advanced search query.

External usage:
- The final Clay search query can be pasted into Clay Search and then used with tables, audiences, and other Clay products. If you mention where to paste it, include the direct URL: https://clay.com.
- Generate a Clay advanced search query that captures the user's filters.
- Ask a brief follow-up question before generating the query unless the user explicitly asks you not to ask follow-up questions.
- If the user explicitly asks you not to ask follow-up questions, generate the best query immediately using the available details.
- If no meaningful query can be produced, explain the issue instead of generating a query.
- If some requested criteria cannot be represented, still generate the best query and tell the user what was not captured.

## Context

You generate Clay search queries for Clay Search — a unified store of people, companies, and work experiences, with job postings reachable as filters on either. Clay is a data platform for people and company intelligence.

## Dataset Capabilities

The Clay Search dataset covers:
- People: profile data, work experience history, and education history
- Companies: company profile data, derived classifications, and technographics
- Job postings (as filters on people and companies): open postings, plus historical postings that closed within the last 6 months

See the field lists below for the queryable fields.

The Clay Search dataset CANNOT filter by (would require adding extra enrichments once the search is run in Clay):
- Email addresses or phone numbers
- Fortune 500 / Inc 5000 / unicorn classifications
- Detailed skills beyond job descriptions
- Prior founder exit history; do not approximate it by excluding companies with current founders
- Competitor relationships or market share

Preview/output boundary:
- This skill is only for converting natural-language input (including job postings, briefs, and ICP notes) into Clay search query filters.
- If the user asks for any additional output data (emails, phone numbers, revenue details, tech stack columns, custom research, etc.), do NOT encode that as a Clay search query filter unless it is also a genuine filter criterion. Tell the user the output or enrichment request was not captured by the query and would require adding extra enrichments once the search is run in Clay. If needed, use multiple boolean clauses or nested query blocks to structure complex searches and represent the user's intent more clearly.

If some requested criteria are not available but other meaningful criteria are available, still generate a valid query for the available criteria and tell the user what was not captured. Do not skip query generation unless no meaningful query can be produced.

## Semantic policies

### Entity selection

Vocabulary:
- Result entity: what the query returns after `select from ...`.
- Filter entity: the related entity whose attributes constrain the result set.
- Active entity: the already-known search entity, if one exists; use it as the default only when the user request is ambiguous.

| User intent | Query entity |
| --- | --- |
| Find people, leads, candidates, employees, alumni | `people` |
| Find accounts, companies, organizations, vendors | `companies` |
| Companies with people/jobs matching criteria | `companies` with `people.exists/count` or `jobs.exists/count` |
| People at companies matching criteria | `people` with `experiences.any(... company.* ...)` |

Rules:
- Choose the result entity from explicit result nouns in the user request.
- If the user is refining an existing query and does not ask to switch result type, preserve the existing query's result entity.
- If the result entity is ambiguous and a query entity is already known, default to that entity.
- Company attributes do NOT imply a companies result. For example, "people at fintech companies" returns people and filters via `experiences.any(... company.* ...)`.
- People or role criteria do NOT imply a people result. For example, "companies with VP sales leaders" returns companies and filters via `people.exists(...)`.
- Job postings are never a result entity; hiring/job criteria filter people or companies. For example, "companies hiring engineers" returns companies and filters via `jobs.exists(...)`, and "people at companies hiring engineers" returns people and filters via `experiences.any(... company.jobs.any(...) ...)`.

### Partial and unsupported criteria

| Situation | Output |
| --- | --- |
| No Clay search intent | Do not generate a query; explain that no Clay search intent was found. |
| All substantive criteria are outside the dataset | Do not generate a query; explain that the criteria are not available. |
| Some criteria are expressible and some are not | Generate a query for expressible criteria; tell the user what was not captured. |
| Informal / approximate mapping (industry label → closest enum values, headcount → nearest bucket, keyword → related field) | Apply the closest filter in the query; this is captured — do NOT tell the user it was uncaptured. |
| User asks to add columns, enrich, pull in data, or configure outputs | Generate a query for filters; tell the user the output ask was not captured by the query. |

### Tiering, scoring, and ranking language

- Treat labels like "Tier 1/2/3", "must-have", "nice-to-have", "high priority", and "low priority" as prioritization metadata, not Clay search query operators.
- Ignore scoring/ranking directives ("score", "weight", "rank", "boost", "prioritize") when building filters. The Clay search query encodes filtering, not weighted ranking.
- Qualitative candidate-quality preferences without a measurable threshold, such as "top performers" or "proven track record of attainment", are soft ranking metadata. Omit them from the query and do not mention them as unsupported; do not invent quota/award keyword proxies.
- If the user provides multiple tiers, include all concrete, expressible criteria across all tiers in ONE `query` (do not drop lower-tier criteria).
- If the user appears to be looking for multiple personas or company groups that can be combined, use one query with `or` blocks. Avoid generating multiple separate queries unless the user explicitly asks for multiple queries or the criteria cannot be combined.
- Flatten same-field values across tiers into combined predicates (`in (...)` / `contains (...)`) when possible, while preserving explicit hard constraints and exclusions.
- Do not call out tier/scoring wording as missing unless the user asks for non-filter outputs derived from scoring (for example, "add a score column").

### Low-coverage fields policy

Low-coverage fields in Clay Search that should be treated as optional by default are: `ai_business_types`.

When you filter on a low-coverage field, ALWAYS include an `is_null` fallback in the same logical block:
- Text match: `(ai_business_types contains "B2B" or ai_business_types is_null)`

This "optional by default" behavior improves recall on sparse columns. Users can later remove the `is_null` clause for a stricter search.

### Value language

Query values must be in English because the dataset is English-backed, regardless of the language the request is written in. Map enum values to their canonical English forms, and render free-text values (keywords, industries, locations, job titles, product/service concepts) in English — e.g. the French request "entreprises de logiciels en Allemagne" produces `industry in ("Software Development") and locations.any(is_headquarters = true and country_name = "Germany")`, never `"logiciels"` or `"Allemagne"`. Keep user-supplied proper nouns exactly as given (company names, domains, LinkedIn URLs, and resource references like `@table(...)`). This governs only the wording of the values — it never removes a filter the user asked for. Capture every attribute the user specifies as its own filter, including attributes whose value happens to be the name of a language — e.g. the companies query "companies that employ Japanese speakers" still produces `people.exists(is_current = true and person.languages = "Japanese")` (current-employee scope per the tenure defaults below).

### Role and tenure defaults

| User wording | Experience scope |
| --- | --- |
| "currently", "now", "current role", "works at", "at [company]", generic role searches like "software engineers" | `is_current = true` |
| "used to", "former", "past", "alumni" | `is_current = false` |
| "ever worked", "experience at", "has worked at" | omit `is_current` |
| Well-known employer groups like Big 4, MBB, FAANG with "experience" or no current wording | omit `is_current` |

This mapping applies wherever an experience is scoped: `experiences.any(...)` in people queries and the bare predicate inside `people.exists(...)` / `people.count(...)` in companies queries. In a companies query, "people at this company" with no past/current wording still defaults to `is_current = true` (treat as current employees) unless the user asks for alumni / ever-worked / any tenure.

### Founder and stealth searches (people queries)

For asks about **new founders** or people **building something new**, require BOTH signals and join them with `and` (not `or`) so each block narrows the result (people queries only):
- Founder identity — a current experience whose title or seniority marks a founder: `(job_title contains ("founder", "co-founder") or seniority = "Founder")`.
- Building signal — building wording on the profile: `(headline contains ("building", "building something new") or about contains ("building", "building something new"))`.

Default query — a current founder AND a profile that signals building something new: `select from people where experiences.any(is_current = true and (job_title contains ("founder", "co-founder") or seniority = "Founder")) and (headline contains ("building", "building something new") or about contains ("building", "building something new"))`

Only when the user **explicitly mentions stealth** add the stealth block, also joined with `and`: require the current founder's employer to be a stealth or unannounced company (`company_name contains "stealth" or company_name is_null` — an unannounced/unnamed company counts as stealth), and add `"stealth"` as a profile keyword. Do NOT add the stealth block for a plain new-founder / building ask that never says stealth.

Stealth variant: `select from people where experiences.any(is_current = true and (job_title contains ("founder", "co-founder") or seniority = "Founder") and (company_name contains "stealth" or company_name is_null)) and (headline contains ("building", "building something new", "stealth") or about contains ("building", "building something new", "stealth"))`

### Job posting defaults

| User wording | Job posting scope (inside `jobs.exists` / `company.jobs.any`) |
| --- | --- |
| Companies hiring for roles, people at companies hiring | include `job_still_open = true` |
| Past, closed, historical postings | include `job_still_open = false` when closed-only; otherwise use the requested date/history filters |
| Hiring activity over a time range | do not force `job_still_open = true`; use the date/history fields requested |

### Query mode policy

- Always use `select from ...` queries.
- Never use count-mode clauses.
- Never include `limit` clauses.

## Grammar

query         = mode "from" entity where? limit_by? limit?
mode          = "select" | "count"
entity        = "people" | "companies"
where         = "where" predicate
limit_by      = "limit" POSITIVE_INTEGER "by" "clay_company_id"
limit         = "limit" INTEGER

predicate     = or_expr
or_expr       = and_expr ("or" and_expr)*
and_expr      = unary_expr ("and" unary_expr)*
unary_expr    = "not" unary_expr | primary_expr
primary_expr  = comparison | aggregate_expr | experience_expr | "(" predicate ")"

comparison    = field_ref comp_op value
            | field_ref "is_null"
            | field_ref "is_not_null"

comp_op       = "=" | "!=" | "<" | "<=" | ">" | ">="
            | "contains" | "starts_with" | "ends_with"
            | "in" | "not_in"
            | "is_similar_to"

value         = scalar_value | date_expr | value_list
scalar_value  = STRING | NUMBER | BOOLEAN
value_list    = "(" scalar_value ("," scalar_value)* ")"
date_expr     = "today" "(" ")" (("+" | "-") "interval" POSITIVE_INTEGER interval_unit)?
interval_unit = "day" | "days" | "week" | "weeks" | "month" | "months" | "year" | "years"
STRING        = '"' [^"]* '"'
NUMBER        = [0-9]+ ("." [0-9]+)?
POSITIVE_INTEGER = [1-9][0-9]*
BOOLEAN       = "true" | "false"

## Operators

- "contains" is token-based (whole word match), NOT substring. e.g. description contains "engineer" matches "Software Engineer" but not "engineering".
- "contains" also accepts a parenthesized list to match ANY of several phrases on the **same field**: description contains ("saas", "fintech") means description contains "saas" OR description contains "fintech". Applies to text fields like description, headline, about, and free-text job location. **Always** prefer the list form over repeating the field with "or": use field contains ("a", "b"), NOT (field contains "a" or field contains "b"). Prefer `is_similar_to` over `contains` for job_title (or `=`/`in` for explicit exact-match asks). Structured city fields (`location_city`, `person.location_city`, and `locations.city`) use exact `=`/`in` matching; follow the Location filtering section.
- "starts_with" and "ends_with" are substring-based.
- "is_null" and "is_not_null" take no value.
- "in" and "not_in" take a parenthesized list: field in ("a", "b", "c")
- "is_similar_to" is fuzzy matching whose mechanism depends on the field. On job title fields (job_title) it expands the title into related synonyms, abbreviations, and variants (takes a parenthesized list). On semantic fields (products_and_services) it semantically matches what the company does/makes/sells; takes one or more non-empty string values in a parenthesized list, e.g. products_and_services is_similar_to ("b2b saas", "crm"). Multiple values within one comparison match companies similar to ANY value. For any other text field (description, headline, about, company_name, location, etc.) use `contains`. **Default to `is_similar_to` for job_title** (best recall). Switch off it only when the user specifies, or clearly seems to specify, literal matching: exact-title asks (e.g. "exact title", "exactly", "strict match") → `=` or `in (...)` ("exact title CTO" → `job_title = "CTO"`; "exactly a VP of Sales or CTO" → `job_title in ("VP of Sales", "CTO")`); a request phrased as literal keyword include/exclude lists (e.g. "title contains ...") → mirror it verbatim with `contains`, keeping every exclusion ("...contains software engineer or developer, not mechanical or civil" → `job_title contains ("software engineer", "developer") and not job_title contains ("mechanical", "civil")`); a role qualified by required words that must appear in the title (e.g. "engineers with automation in the title", "titles that contain X and Y") is also a literal-title signal → use `contains` and AND-join each required word as its own predicate so all must co-occur ("engineers with automation in the title" → `job_title contains "engineer" and job_title contains "automation"`).
- Boolean precedence: "and" binds tighter than "or". Use parentheses to override.
- Preserve comparison boundaries exactly: "under", "below", "fewer than", "over", "above", and "more than" are strict (`<` or `>`); "at most" and "no more than" use `<=`; "at least" uses `>=`.
- Enum fields only support "=", "!=", "in", and "not_in". Do NOT use numeric operators (<, <=, >, >=) or text-search operators (contains, starts_with, ends_with) on them — match against the listed values exactly.
- Enum value sets are field-specific — always match against the values listed for that exact field. Both experience and job-posting entities have a `seniority` field with DIFFERENT value sets: experience seniority (e.g. "Entry", "Senior", "Intern / In Training") vs job-posting seniority (e.g. "Entry level", "Mid-Senior level", "Executive", "Intern"). Always use the values listed for the specific field you are filtering on.
- Display labels: Some enum values are shown with a display label in parentheses, e.g. `"1" (1 employee)`. The quoted string is the Clay search query value — always use it in `query`. The parenthesized label is the human-readable form — use it when explaining uncaptured criteria to the user.

## Where Semantics

- Multiple conditions joined by "and" require ALL to be true (intersection).
- Multiple conditions joined by "or" require ANY to be true (union).
- "and" binds tighter than "or": A or B and C means A or (B and C). Use parentheses to override: (A or B) and C.
- For "in" lists, values are OR'd: field in ("a", "b") means field = "a" OR field = "b". To exclude a list, wrap with "not": not field in ("a", "b") means field != "a" AND field != "b".
- For "contains" lists, phrases are OR'd: description contains ("a", "b") means description contains "a" OR description contains "b".
- For multi-word terms, always quote the full phrase: description contains "Vice President", NOT description contains "Vice" and description contains "President". A quoted phrase requires all its words together; a list like ("Vice", "President") instead matches EITHER word.
- For "is_similar_to" lists on job_title, phrases are OR'd like `contains` lists: job_title is_similar_to ("CTO", "VP Engineering") matches either. Use `is_similar_to` for job_title unless the user asks for (or seems to ask for) literal matching — an explicit exact-title ask (use `=`/`in`) or a request phrased with literal keyword include/exclude lists such as "title contains ..." (use `contains`, preserving all exclusions).

## Common query guardrails

- Collapse same-field alternatives into one list instead of an OR chain: `in (...)` for normalized/enum fields and structured city/state fields. Use `location_city in ("New York", "New York City", "Brooklyn")`, not `location_city = "New York" or location_city = "Brooklyn"`.
- Do not include case-only duplicates in a value list; `contains`, `is_similar_to`, `=`, and `in` are case-insensitive on text fields. Use `job_title is_similar_to ("Sales")`, not `job_title is_similar_to ("sales", "Sales")`.
- Never silently trim explicit user lists. If the user provided 12 domains, 9 countries, or 15 job-title phrases, keep all 12/9/15 values in Clay search query when those fields are expressible.
- Never drop negation/exclusion clauses. If the user specifies `not (title contains "X")` or excludes certain titles, always preserve that exclusion in Clay search query — e.g. `not job_title is_similar_to ("X")`. The `is_similar_to` operator does NOT implicitly exclude unrelated titles; explicit exclusions must be kept.
- Never invent fields that are not listed in the field list below. If no listed field captures a criterion, keep the expressible filters and briefly tell the user that criterion was not represented.

## People query guardrails

- For role keywords, emit one case-insensitive title predicate using `is_similar_to`. Use `job_title is_similar_to ("Sales")`, not `job_title is_similar_to ("sales", "Sales")`. Prefer `is_similar_to` over `contains` for job_title by default. Switch off `is_similar_to` only when the user specifies (or seems to specify) literal matching: an explicit exact-title ask (use `=`/`in`, e.g. "exact title CTO" → `job_title = "CTO"`), or a request phrased as literal keyword include/exclude lists like "title contains ..." (use `contains`, keeping all exclusions).
- Current-employer asks with explicit domains/LinkedIn URLs or resolvable household names use `clay.filter_to_companies((...))` at the top level — NOT `company.domain` inside `experiences.any(...)`.
- Use `company.domain` inside `experiences.any(...)` only for former employers, "used to work at" asks, or any-tenure employer history.
- For explicit city alternatives, use `location_city in ("New York", "New York City", "Brooklyn")`, not repeated OR conditions.
- There are no `first_name` or `last_name` fields. Use `full_name contains "Name"` when a name filter is expressible, or briefly tell the user exact first/last-name requirements were not represented.

Experience `seniority` mapping:
- Only use leadership values (`C-suite`, `VP`, `Director`, `Head`, `Founder`, `Owner`, `Partner`, `Board Member`) when the query is clearly about executives or high-level org leaders/managers (e.g. "execs", "leadership", "decision makers", "VP of Sales", "founders").
- Individual-contributor searches (engineers, designers, analysts, sales reps, recruiters, etc.) must NOT include leadership seniority values. Scope them to `Senior`, `Mid-level`, `Entry`, or `Intern / In Training` based on what the query asks (e.g. "senior engineers" → `seniority = "Senior"`; "junior analysts" → `seniority in ("Entry", "Intern / In Training")`).
- If the query doesn't mention a level, omit the `seniority` filter entirely — the `job_title` keyword already captures the role; do not guess a level.

Company similarity / vertical context inside people searches:
- For first-pass asks about people at companies matching a broad company category, market, or similarity pattern, map the company context to `company.industry` values inside `experiences.any(...)`.
- For asks like "people at companies similar to <company/domain>" or "like <company>", treat the named company/domain as a vertical anchor, NOT an exact-company identifier: infer relevant `company.industry` values and filter on those. Do NOT use `clay.filter_to_companies`, `company.domain`, or `company_name` for that similarity anchor unless the user explicitly asks to include/exclude that exact company.
- When you apply that similarity proxy, briefly tell the user an approximation was applied: direct similarity-by-domain/name is unavailable, so relevant industries were applied.
- Do NOT add `company.ai_subindustries`, `company.ai_industries`, or `company.ai_revenue_streams` for broad similarity or vertical context unless the user explicitly asks for those AI-derived fields.

## Companies query guardrails

- Default to `industry` for company vertical filtering. NEVER emit `ai_subindustries`, `ai_industries`, or `ai_revenue_streams` unless the user explicitly asks for that field or its values.
- Exception: if the user explicitly asks in a follow-up refinement to narrow or exclude by specific subindustries, industries, or revenue streams, predicates on the matching AI-derived field are allowed.
- If an initial (non-refinement) request names specific subindustries, map them to the closest `industry` values in first pass. That mapping is captured — do NOT tell the user the subindustry labels were uncaptured, and do NOT hedge that they were "broadly mapped", "can't be distinguished further", "partially included", or have "no dedicated label". To help the user narrow, offer subindustries as a follow-up question instead of a not-captured caveat.
- When a companies query asks to return one or more specific companies by name, domain, LinkedIn URL, or pasted identifier list, treat it as exact-company inclusion and use exactly one inline `clay.include_company_identifiers((...))` call. This applies even when the user does not say "exact", "only", or "include" (for example, "find me Clay, Instantly, and HubSpot"). Do NOT use top-level `domain =` or `domain in (...)` for these asks, except to preserve distinct conditions paired with explicit domains: use the single inclusion call together with parenthesized `domain = ... and ...` branches, such as `clay.include_company_identifiers(("acme.com", "beta.com")) and ((domain = "acme.com" and industry = "Health Care") or (domain = "beta.com" and industry = "Financial Services"))`. Never flatten those branch-local conditions into shared OR lists.
- `clay.include_company_identifiers` is companies-only and inline-list-only. It does not accept resource references. Never emit more than one call in a query; combine every exact identifier into one list of at most 1000 entries. For a larger list, do not generate a query. Tell the user: "Company identifier lists are limited to 1000 entries. Reduce the list and try again."
- For explicit company exclusion lists, use exactly one inline `clay.exclude_company_identifiers((...))` call. Never emit `not clay.include_company_identifiers((...))`; use the canonical exclusion function directly.
- For generic industry or vertical labels such as "SaaS", "tech", "fintech", "banking", or "healthcare", use `industry` first. For plain "fintech" with no narrower product words, use exactly `industry in ("Financial Services", "Banking")`; do not add `Capital Markets`, `Insurance`, or description keywords unless the user names those narrower categories.
- Company location filtering uses the `locations` tuple array. For generic location asks, use `locations.any(is_headquarters = true and country_name in ("China", "Brazil", "Nigeria"))` for country alternatives. Do not emit `locations.country_iso`; company `locations` has `country_name`, `city`, `state_or_province`, `postal_code`, `region`, and `is_headquarters` subfields.
- Generic company location language such as "companies in", "based in", or "located in" defaults to headquarters: include `is_headquarters = true` inside the same `locations.any(...)` predicate. Omit the headquarters condition only when the user explicitly asks for an office, branch, physical presence, operating market, destination coverage, or served country.
- Do not invent `name`, `slug`, `derived_description`, or `pattern_tags`. Use listed fields such as `domain`, `description`, `industry`, and `ai_business_types`.

## Field docs

### People fields

- full_name (string): Full name of the person (first and last).
- location_city (string): City name, e.g. 'San Francisco', 'Berlin'. Common: "Aberdeen", "Abilene", "Akron", "Albany", "Albuquerque", "Alexandria", "Allentown", "Amarillo", "Anaheim", "Anchorage", "Ann Arbor", "Antioch", "Apple Valley", "Appleton", "Arlington", "Arvada", "Asheville", "Atlanta", "Atlantic City", "Augusta", …
- location_state (string): State, province, or municipality, e.g. 'California', 'Ontario'.
- location_country (string, enum): Full country name, e.g. 'United States', 'Germany'. Values: "Afghanistan", "Åland Islands", "Albania", "Algeria", "American Samoa", "Andorra", "Angola", "Anguilla", "Antarctica", "Antigua and Barbuda", "Argentina", "Armenia", "Aruba", "Australia", "Austria", "Azerbaijan", "Bahamas", "Bahrain", "Bangladesh", "Barbados", "Belarus", "Belgium", "Belize", "Benin", "Bermuda", "Bhutan", "Bolivia", "Bosnia and Herzegovina", "Botswana", "Bouvet Island", "Brazil", "British Indian Ocean Territory", "Brunei Darussalam", "Bulgaria", "Burkina Faso", "Burundi", "Cambodia", "Cameroon", "Canada", "Cape Verde", "Cayman Islands", "Central African Republic", "Chad", "Chile", "China", "Christmas Island", "Cocos (Keeling) Islands", "Collectivity of Saint Martin", "Colombia", "Comoros", "Cook Islands", "Costa Rica", "Côte d'Ivoire", "Croatia", "Cuba", "Curaçao", "Cyprus", "Czech Republic", "Democratic Republic of the Congo", "Denmark", "Djibouti", "Dominica", "Dominican Republic", "Ecuador", "Egypt", "El Salvador", "Equatorial Guinea", "Eritrea", "Estonia", "Ethiopia", "Falkland Islands (Malvinas)", "Faroe Islands", "Fiji", "Finland", "France", "French Guiana", "French Polynesia", "French Southern Territories", "Gabon", "Gambia", "Georgia", "Germany", "Ghana", "Gibralta", "Greece", "Greenland", "Grenada", "Guadeloupe", "Guam", "Guatemala", "Guernsey", "Guinea-Bissau", "Guinea", "Guyana", "Haiti", "Heard Island and McDonald Islands", "Holy See", "Honduras", "Hong Kong", "Hungary", "Iceland", "India", "Indonesia", "Iran", "Iraq", "Ireland", "Isle of Man", "Israel", "Italy", "Jamaica", "Japan", "Jersey", "Jordan", "Kazakhstan", "Kenya", "Kiribati", "Kuwait", "Kyrgyzstan", "Laos", "Latvia", "Lebanon", "Lesotho", "Liberia", "Libya", "Liechtenstein", "Lithuania", "Luxembourg", "Macao", "Madagascar", "Malawi", "Malaysia", "Maldives", "Mali", "Malta", "Marshall Islands", "Martinique", "Mauritania", "Mauritius", "Mayotte", "Mexico", "Micronesia", "Moldova", "Monaco", "Mongolia", "Montenegro", "Montserrat", "Morocco", "Mozambique", "Myanmar", "Namibia", "Nauru", "Nepal", "Netherlands", "New Caledonia", "New Zealand", "Nicaragua", "Niger", "Nigeria", "Niue", "Norfolk Island", "North Korea", "North Macedonia", "Northern Mariana Islands", "Norway", "Oman", "Pakistan", "Palau", "Palestine", "Panama", "Papua New Guinea", "Paraguay", "Peru", "Philippines", "Pitcairn", "Poland", "Portugal", "Puerto Rico", "Qatar", "Republic of the Congo", "Réunion", "Romania", "Russia", "Rwanda", "Saint Barthélemy", "Saint Helena, Ascension and Tristan da Cunha", "Saint Kitts and Nevis", "Saint Lucia", "Saint Pierre and Miquelon", "Saint Vincent and the Grenadines", "Samoa", "San Marino", "Sao Tome and Principe", "Saudi Arabia", "Senegal", "Serbia", "Seychelles", "Sierra Leone", "Singapore", "Sint Maarten", "Slovakia", "Slovenia", "Solomon Islands", "Somalia", "South Africa", "South Georgia and the South Sandwich Islands", "South Korea", "South Sudan", "Spain", "Sri Lanka", "Sudan", "Suriname", "Swaziland", "Sweden", "Switzerland", "Syria", "Taiwan", "Tajikistan", "Tanzania", "Thailand", "Timor-Leste", "Togo", "Tokelau", "Tonga", "Trinidad and Tobago", "Tunisia", "Turkey", "Turkmenistan", "Turks and Caicos Islands", "Tuvalu", "Uganda", "Ukraine", "United Arab Emirates", "United Kingdom", "United States Minor Outlying Islands", "United States", "Uruguay", "Uzbekistan", "Vanuatu", "Venezuela", "Vietnam", "Virgin Islands, British", "Virgin Islands, U.S.", "Wallis and Futuna", "Western Sahara", "Yemen", "Zambia", "Zimbabwe"
- location_region (string, enum): Geographic region. Values: "APAC", "EMEA", "LATAM", "NAM"
- estimated_follower_count (number): Estimated social media audience/follower count.
- network_size (number): Estimated professional network size (connection count).
- years_of_experience (number): Estimated full-time years of experience based on full-time roles on the profile.
- headline (string): Profile headline (the tagline below their name). Use contains for keyword matching.
- about (string): About/summary section of the profile. Use contains for keyword matching.
- languages (string, array): Languages listed on the profile. Use = for exact match, in for multiple. e.g. languages = "Spanish" or languages in ("Spanish", "French"). Common: "English", "Spanish", "French", "German", "Portuguese", "Chinese", "Japanese", "Hindi", "Arabic", "Korean", "Italian", "Dutch", "Russian"
- education (tuple array): Education history. Use .any() or .count() with inner predicates on subfields.
Subfields:
- school_name (string): Name of the school or university.
- degree (string): Degree obtained, e.g. 'Bachelor of Science', 'Master of Business Administration', 'PhD'.
- field_of_study (string): Academic major or field, e.g. 'Computer Science', 'Economics', 'Mechanical Engineering'.
- start_date (month): Date education started (YYYY-MM).
- end_date (month): Date education ended (YYYY-MM).
- description (string): Education description text. Use contains for keyword matching.
- activities_and_societies (string): Activities and societies during education. Use contains for keyword matching.

### Companies fields

- domain (string): Company website domain, e.g. 'google.com', 'stripe.com'.
- description (string): Company description. Use contains for keyword matching.
- products_and_services (string): What the company does, makes, and sells, matched by meaning rather than exact keywords. Only supports is_similar_to with one or more non-empty string values; multiple values match companies similar to ANY value (e.g. products_and_services is_similar_to ("b2b saas", "crm")). Distinct from description keyword matching.
- company_type (string, enum): Company type classification. Values: "Privately Held" (Privately held), "Public Company" (Public company), "Partnership", "Self Employed" (Self-employed), "Non Profit" (Nonprofit), "Educational", "Self Owned" (Self-owned), "Government Agency" (Government agency)
- company_size (string, enum): Company size range bucket. Prefer this for first-pass company-size filtering; use estimated_employee_count only when the user explicitly asks for exact employee count/headcount. Values: "1" (1 employee), "2-10" (2–10 employees), "11-50" (11–50 employees), "51-200" (51–200 employees), "201-500" (201–500 employees), "501-1,000" (501–1,000 employees), "1,001-5,000" (1,001–5,000 employees), "5,001-10,000" (5,001–10,000 employees), "10,001+" (10,001+ employees)
- estimated_employee_count (number): Estimated total employee count. Use only when the user explicitly asks for exact employee count/headcount or asks to switch from company-size buckets to exact counts.
- estimated_follower_count (number): Estimated social media audience/follower count.
- year_founded (year): Year the company was founded.
- employee_growth_3mo (number): Employee growth ratio (1.1 = +10% growth, 0.9 = −10% decline).
- employee_growth_6mo (number): Employee growth ratio (1.1 = +10% growth, 0.9 = −10% decline).
- employee_growth_12mo (number): Employee growth ratio (1.1 = +10% growth, 0.9 = −10% decline).
- employee_growth_24mo (number): Employee growth ratio (1.1 = +10% growth, 0.9 = −10% decline).
- annual_revenue (string, enum): Annual revenue bracket. Use = or in with exact bucket values, not numeric operators. Values: "0-500K" ($0–$500K), "500K-1M" ($500K–$1M), "1M-5M" ($1M–$5M), "5M-10M" ($5M–$10M), "10M-25M" ($10M–$25M), "25M-75M" ($25M–$75M), "75M-200M" ($75M–$200M), "200M-500M" ($200M–$500M), "500M-1B" ($500M–$1B), "1B-10B" ($1B–$10B), "10B-100B" ($10B–$100B), "100B-1T" ($100B+)
- ai_business_types (string): Derived business model type. LOW COVERAGE — treat as optional: always pair with an is_null fallback, e.g. (ai_business_types contains "B2B" or ai_business_types is_null). Use contains for matching. Common: "B2B", "B2C", "Nonprofit"
- industry (string, enum): The company's main industry. This is the primary, structured way to filter companies by industry. Values: "Abrasives and Nonmetallic Minerals Manufacturing", "Accessible Architecture and Design", "Accommodation Services", "Accounting", "Administration of Justice", "Administrative and Support Services", "Advertising Services", "Agricultural Chemical Manufacturing", "Agriculture, Construction, Mining Machinery Manufacturing", "Air, Water, and Waste Program Management", "Airlines and Aviation", "Alternative Dispute Resolution", "Alternative Medicine", "Ambulance Services", "Amusement Parks and Arcades", "Animal Feed Manufacturing", "Animation", "Animation and Post-production", "Apparel Manufacturing", "Apparel and Fashion", "Appliances, Electrical, and Electronics Manufacturing", "Architectural and Structural Metal Manufacturing", "Architecture and Planning", "Armed Forces", "Artists and Writers", "Arts and Crafts", "Audio and Video Equipment Manufacturing", "Automation Machinery Manufacturing", "Automotive", "Aviation & Aerospace", "Aviation and Aerospace Component Manufacturing", "Baked Goods Manufacturing", "Banking", "Bars, Taverns, and Nightclubs", "Bed-and-Breakfasts, Hostels, Homestays", "Beverage Manufacturing", "Biomass Electric Power Generation", "Biotechnology", "Biotechnology Research", "Blockchain Services", "Blogs", "Boilers, Tanks, and Shipping Container Manufacturing", "Book Publishing", "Book and Periodical Publishing", "Breweries", "Broadcast Media Production and Distribution", "Building Construction", "Building Equipment Contractors", "Building Finishing Contractors", "Building Materials", "Building Structure and Exterior Contractors", "Business Consulting and Services", "Business Content", "Business Intelligence Platforms", "Business Supplies and Equipment", "Capital Markets", "Caterers", "Chemical Manufacturing", "Chemical Raw Materials Manufacturing", "Child Day Care Services", "Chiropractors", "Civic and Social Organizations", "Civil Engineering", "Claims Adjusting, Actuarial Services", "Clay and Refractory Products Manufacturing", "Climate Data and Analytics", "Climate Technology Product Manufacturing", "Coal Mining", "Collection Agencies", "Commercial Real Estate", "Commercial and Industrial Equipment Rental", "Commercial and Industrial Machinery Maintenance", "Commercial and Service Industry Machinery Manufacturing", "Communications Equipment Manufacturing", "Community Development and Urban Planning", "Community Services", "Computer Games", "Computer Hardware", "Computer Hardware Manufacturing", "Computer Networking", "Computer Networking Products", "Computer and Network Security", "Computers and Electronics Manufacturing", "Conservation Programs", "Construction", "Construction Hardware Manufacturing", "Consumer Electronics", "Consumer Goods", "Consumer Goods Rental", "Consumer Services", "Cosmetics", "Cosmetology and Barber Schools", "Courts of Law", "Credit Intermediation", "Dairy", "Dairy Product Manufacturing", "Dance Companies", "Data Infrastructure and Analytics", "Data Security Software Products", "Defense & Space", "Defense and Space Manufacturing", "Dentists", "Design", "Design Services", "Desktop Computing Software Products", "Digital Accessibility Services", "Distilleries", "E-Learning", "E-Learning Providers", "Economic Programs", "Education", "Education Administration Programs", "Education Management", "Electric Lighting Equipment Manufacturing", "Electric Power Generation", "Electric Power Transmission, Control, and Distribution", "Electrical Equipment Manufacturing", "Electronic and Precision Equipment Maintenance", "Embedded Software Products", "Emergency and Relief Services", "Engineering Services", "Engines and Power Transmission Equipment Manufacturing", "Entertainment", "Entertainment Providers", "Environmental Quality Programs", "Environmental Services", "Equipment Rental Services", "Events Services", "Executive Offices", "Executive Search Services", "Fabricated Metal Products", "Facilities Services", "Farming, Ranching, Forestry", "Farming", "Fashion Accessories Manufacturing", "Financial Services", "Fine Art", "Fine Arts Schools", "Fire Protection", "Fisheries", "Flight Training", "Food & Beverages", "Food and Beverage Manufacturing", "Food and Beverage Retail", "Food and Beverage Services", "Food Production", "Footwear Manufacturing", "Forestry and Logging", "Freight and Package Transportation", "Fruit and Vegetable Preserves Manufacturing", "Fundraising", "Funds and Trusts", "Furniture", "Furniture and Home Furnishings Manufacturing", "Gambling Facilities and Casinos", "Geothermal Electric Power Generation", "Glass Product Manufacturing", "Glass, Ceramics and Concrete Manufacturing", "Golf Courses and Country Clubs", "Government Administration", "Government Relations", "Government Relations Services", "Graphic Design", "Ground Passenger Transportation", "HVAC and Refrigeration Equipment Manufacturing", "Health and Human Services", "Health, Wellness and Fitness", "Higher Education", "Highway, Street, and Bridge Construction", "Historical Sites", "Holding Companies", "Home Health Care Services", "Horticulture", "Hospitality", "Hospitals", "Hospitals and Health Care", "Hotels and Motels", "Household Appliance Manufacturing", "Household Services", "Household and Institutional Furniture Manufacturing", "Housing Programs", "Housing and Community Development", "Human Resources", "Human Resources Services", "Hydroelectric Power Generation", "IT Services and IT Consulting", "IT System Custom Software Development", "IT System Data Services", "IT System Design Services", "IT System Installation and Disposal", "IT System Operations and Maintenance", "IT System Testing and Evaluation", "IT System Training and Support", "Import and Export", "Individual and Family Services", "Industrial Automation", "Industrial Machinery Manufacturing", "Industry Associations", "Information Services", "Information Technology and Services", "Insurance", "Insurance Agencies and Brokerages", "Insurance Carriers", "Insurance and Employee Benefit Funds", "Interior Design", "International Affairs", "International Trade and Development", "Internet Marketplace Platforms", "Internet News", "Internet Publishing", "Investment Advice", "Investment Banking", "Investment Management", "Janitorial Services", "Landscaping Services", "Language Schools", "Laundry and Drycleaning Services", "Law Enforcement", "Law Practice", "Leasing Non-residential Real Estate", "Leasing Residential Real Estate", "Leather Product Manufacturing", "Legal Services", "Legislative Offices", "Leisure, Travel & Tourism", "Libraries", "Loan Brokers", "Luxury Goods and Jewelry", "Machinery Manufacturing", "Manufacturing", "Maritime", "Maritime Transportation", "Market Research", "Marketing Services", "Mattress and Blinds Manufacturing", "Measuring and Control Instrument Manufacturing", "Meat Products Manufacturing", "Mechanical or Industrial Engineering", "Media & Telecommunications", "Media Production", "Medical Devices", "Medical Equipment Manufacturing", "Medical Practices", "Medical and Diagnostic Laboratories", "Mental Health Care", "Metal Ore Mining", "Metal Treatments", "Metal Valve, Ball, and Roller Manufacturing", "Metalworking Machinery Manufacturing", "Military and International Affairs", "Mining", "Mobile Computing Software Products", "Mobile Food Services", "Mobile Gaming Apps", "Motor Vehicle Manufacturing", "Motor Vehicle Parts Manufacturing", "Movies and Sound Recording", "Movies, Videos and Sound", "Museums", "Museums, Historical Sites, and Zoos", "Music", "Musicians", "Nanotechnology Research", "Natural Gas Distribution", "Newspaper Publishing", "Non-profit Organization Management", "Non-profit Organizations", "Nonmetallic Mineral Mining", "Nonresidential Building Construction", "Nuclear Electric Power Generation", "Nursing Homes and Residential Care Facilities", "Office Administration", "Office Furniture and Fixtures Manufacturing", "Oil and Gas", "Oil, Gas, and Mining", "Online Audio and Video Media", "Online Media", "Online and Mail Order Retail", "Operations Consulting", "Optometrists", "Outpatient Care Centers", "Outsourcing and Offshoring Consulting", "Outsourcing/Offshoring", "Packaging and Containers", "Packaging and Containers Manufacturing", "Paint, Coating, and Adhesive Manufacturing", "Paper and Forest Product Manufacturing", "Paper and Forest Products", "Performing Arts", "Performing Arts and Spectator Sports", "Periodical Publishing", "Personal Care Product Manufacturing", "Personal Care Services", "Personal and Laundry Services", "Pet Services", "Pharmaceutical Manufacturing", "Philanthropic Fundraising Services", "Philanthropy", "Photography", "Physical, Occupational and Speech Therapists", "Physicians", "Plastics Manufacturing", "Plastics and Rubber Product Manufacturing", "Political Organizations", "Primary Metal Manufacturing", "Primary and Secondary Education", "Printing Services", "Professional Organizations", "Professional Services", "Professional Training and Coaching", "Program Development", "Public Assistance Programs", "Public Health", "Public Policy", "Public Policy Offices", "Public Relations and Communications Services", "Public Safety", "Radio and Television Broadcasting", "Rail Transportation", "Railroad Equipment Manufacturing", "Ranching", "Real Estate", "Real Estate Agents and Brokers", "Real Estate and Equipment Rental Services", "Recreational Facilities", "Religious Institutions", "Renewable Energy Equipment Manufacturing", "Renewable Energy Power Generation", "Renewable Energy Semiconductor Manufacturing", "Renewables & Environment", "Repair and Maintenance", "Research", "Research Services", "Residential Building Construction", "Restaurants", "Retail", "Retail Apparel and Fashion", "Retail Appliances, Electrical, and Electronic Equipment", "Retail Art Dealers", "Retail Art Supplies", "Retail Books and Printed News", "Retail Building Materials and Garden Equipment", "Retail Florists", "Retail Furniture and Home Furnishings", "Retail Gasoline", "Retail Groceries", "Retail Health and Personal Care Products", "Retail Luxury Goods and Jewelry", "Retail Motor Vehicles", "Retail Musical Instruments", "Retail Office Equipment", "Retail Office Supplies and Gifts", "Retail Pharmacies", "Retail Recyclable Materials & Used Merchandise", "Reupholstery and Furniture Repair", "Robotics Engineering", "Rubber Products Manufacturing", "Satellite Telecommunications", "School and Employee Bus Services", "Seafood Product Manufacturing", "Securities and Commodity Exchanges", "Security Guards and Patrol Services", "Security Systems Services", "Security and Investigations", "Semiconductor Manufacturing", "Semiconductors", "Services for Renewable Energy", "Services for the Elderly and Disabled", "Sheet Music Publishing", "Shipbuilding", "Shuttles and Special Needs Transportation Services", "Sightseeing Transportation", "Soap and Cleaning Product Manufacturing", "Social Networking Platforms", "Software Development", "Solar Electric Power Generation", "Sound Recording", "Space Research and Technology", "Specialty Trade Contractors", "Spectator Sports", "Sporting Goods", "Sporting Goods Manufacturing", "Sports Teams and Clubs", "Sports and Recreation Instruction", "Spring and Wire Product Manufacturing", "Staffing and Recruiting", "Steam and Air-Conditioning Supply", "Strategic Management Services", "Subdivision of Land", "Sugar and Confectionery Product Manufacturing", "Surveying and Mapping Services", "Taxi and Limousine Services", "Technical and Vocational Training", "Technology, Information and Internet", "Technology, Information and Media", "Telecommunications", "Telecommunications Carriers", "Telephone Call Centers", "Temporary Help Services", "Textile Manufacturing", "Theater Companies", "Think Tanks", "Tobacco", "Tobacco Manufacturing", "Translation and Localization", "Transportation Equipment Manufacturing", "Transportation Programs", "Transportation, Logistics, Supply Chain and Storage", "Transportation/Trucking/Railroad", "Travel Arrangements", "Truck Transportation", "Trusts and Estates", "Turned Products and Fastener Manufacturing", "Urban Transit Services", "Utilities", "Utilities Administration", "Utility System Construction", "Vehicle Repair and Maintenance", "Venture Capital and Private Equity Principals", "Veterinary", "Veterinary Services", "Vocational Rehabilitation Services", "Warehousing", "Warehousing and Storage", "Waste Collection", "Waste Treatment and Disposal", "Water Supply and Irrigation Systems", "Water, Waste, Steam, and Air Conditioning Services", "Wellness and Fitness Services", "Wholesale", "Wholesale Alcoholic Beverages", "Wholesale Apparel and Sewing Supplies", "Wholesale Appliances, Electrical, and Electronics", "Wholesale Building Materials", "Wholesale Chemical and Allied Products", "Wholesale Computer Equipment", "Wholesale Drugs and Sundries", "Wholesale Food and Beverage", "Wholesale Footwear", "Wholesale Furniture and Home Furnishings", "Wholesale Hardware, Plumbing, Heating Equipment", "Wholesale Import and Export", "Wholesale Luxury Goods and Jewelry", "Wholesale Machinery", "Wholesale Metals and Minerals", "Wholesale Motor Vehicles and Parts", "Wholesale Paper Products", "Wholesale Petroleum and Petroleum Products", "Wholesale Raw Farm Products", "Wholesale Recyclable Materials", "Wind Electric Power Generation", "Wine and Spirits", "Wineries", "Wireless Services", "Wood Product Manufacturing", "Writing and Editing", "Zoos and Botanical Gardens"
- naics_codes_2022 (string, enum, array): NAICS 2022 industry classification codes as numeric strings (2-6 digits). Matching is hierarchical: a code matches companies whose more specific codes fall under it (e.g. "5132" matches a company classified "513210"). Census publishes three 2-digit sectors as ranges: "31-33" (Manufacturing), "44-45" (Retail Trade), "48-49" (Transportation and Warehousing). Use = for one code, in for multiple, e.g. naics_codes_2022 in ("31-33", "541511"). Values: "11" (11: Agriculture, Forestry, Fishing and Hunting), "111" (111: Crop Production), "1111" (1111: Oilseed and Grain Farming), "11111" (11111: Soybean Farming), "111110" (111110: Soybean Farming), "11112" (11112: Oilseed (except Soybean) Farming), "111120" (111120: Oilseed (except Soybean) Farming), "11113" (11113: Dry Pea and Bean Farming), "111130" (111130: Dry Pea and Bean Farming), "11114" (11114: Wheat Farming), "111140" (111140: Wheat Farming), "11115" (11115: Corn Farming), "111150" (111150: Corn Farming), "11116" (11116: Rice Farming), "111160" (111160: Rice Farming), "11119" (11119: Other Grain Farming), "111191" (111191: Oilseed and Grain Combination Farming), "111199" (111199: All Other Grain Farming), "1112" (1112: Vegetable and Melon Farming), "11121" (11121: Vegetable and Melon Farming), …
- hierarchy_status (string, enum): The company's position in a known corporate hierarchy. Use "Ultimate parent" for the top-level parent or "Subsidiary" for a company beneath an ultimate parent. Use = for one status or in for multiple, e.g. hierarchy_status in ("Subsidiary", "Ultimate parent"). Values: "Ultimate parent", "Subsidiary"
- ai_industries (string, enum, array): AI-derived industry classification. Do NOT filter on this field unless the user explicitly asks for ai_industries — default vertical filtering uses industry. Use = for exact match, in for multiple. e.g. ai_industries = "Professional, Business and Legal Services". Values: "Agriculture, Forestry and Fisheries", "Automotive, Aerospace and Defense Manufacturing", "Education and Training", "Energy, Utilities and Environmental Services", "Finance and Insurance", "Healthcare and Life Sciences", "Hospitality, Food and Travel Services", "Industrial Manufacturing and Materials", "Media, Entertainment and Culture", "Non-Profit, Public Sector and Education (Non-Commercial)", "Personal and Home Services", "Professional, Business and Legal Services", "Real Estate and Construction", "Retail and Consumer Channels", "Software and IT", "Transportation and Logistics"
- ai_subindustries (string, enum, array): AI-derived subindustry classification. Use = for exact match, in for multiple. e.g. ai_subindustries = "AI and ML Platforms". Values: "AI and ML Platforms", "Agriculture and Forestry Software", "Blockchain and Web3", "Carriers and ISPs", "Cloud and Infrastructure Software", "Consumer Software", "Data and Analytics Software", "Developer Tools and Platforms", "Enterprise Software Solutions", "Financial Services Software", "Government and Public Sector Software", "Hardware and Networking", "Healthcare Software", "IoT and Embedded Systems Software", "IT Services and Cybersecurity", "Manufacturing Software", "Metaverse, AR/VR and Other Emerging Platforms", "Quantum Computing Software", "Real Estate and PropTech Software", "Retail and Ecommerce Software", "Security and Identity Software", "Biotechnology and Pharmaceuticals", "Digital Health and Telemedicine", "Hospitals, Clinics and Outpatient Care", "Medical Devices and Diagnostic Equipment", "Medical Testing and Clinical Laboratories", "Mental Health and Rehabilitation Services", "Pharma Distribution and CRO Services", "Banking and Lending", "Capital Markets and Cryptocurrency", "Cryptocurrency and Blockchain Services", "Financial Services Platforms", "Insurance and InsurTech", "Investment Management and WealthTech", "Venture Capital and Private Equity", "Electric Power and Grid Management", "Nuclear and Advanced Generation", "Oil and Gas Exploration, Production and Services", "Renewable Energy and Clean Tech", "Sustainability Tech and Environmental Consulting", "Water, Waste and Environmental Management", "3D Printing and Advanced Manufacturing", "Building Materials and Chemicals", "Consumer Goods and Appliances", "Electronics and Computer Equipment", "Food, Beverage and Tobacco Production", "Industrial Machinery and Equipment", "Mining, Metals and Natural Resources", "Architecture, Urban Planning and Green Building", "Commercial Real Estate Development and Leasing", "Construction and Civil Engineering Services", "Property and Facility Management", "Residential Real Estate Development and Brokerage", "Specialty Construction Products", "Automotive Service and Collision Repair", "Brick-and-Mortar Retail", "Media and Entertainment Retail", "Online Commerce and Marketplaces", "Retail Technology", "Specialty Auctions and Collectibles", "Wholesale and Distribution", "Autonomous Vehicles and Drone Delivery", "Car and Truck Rental", "Freight and Cargo", "Logistics Technology", "Passenger Transit and Mobility", "Warehousing, Fulfillment and 3PL Services", "Accounting, Audit and Financial Advisory", "Advertising, Marketing and Multimedia Design", "Defense and Government Services", "Facilities Management and Commercial Cleaning", "Human Resources, Staffing and Recruitment", "Legal Services and Regulatory Compliance", "Management Consulting and Strategy Consulting", "Translation, Document and Information Management", "Corporate Training and Learning and Development", "E-Learning Platforms and EdTech", "K-12 and Higher Education Institutions", "Test Prep, Tutoring and After-School Services", "Vocational Training and Certification Programs", "Digital Publishing and Streaming Platforms", "Film, Television and Broadcasting", "Gaming, Esports and Interactive Entertainment", "Live Events, Experiences and Ticketed Attractions", "Museums, Art Galleries and Cultural Preservation", "Music, Audio and Podcast Services", "Sports and Recreation", "Food and Beverage Services", "Hospitality and Lodging", "Travel Agencies and Leisure Services", "Funeral Homes and Related Services", "Home Services", "Personal Care and Wellness", "Veterinary Care and Pet Services", "AgriTech and Precision Farming", "Aquaculture and Fisheries", "Crop Farming and Livestock Production", "Farming Equipment and Supplies", "Forestry, Logging and Wood Products", "Mining and Extraction", "Aviation and Aerospace Component Manufacturing", "Automotive and Rental Retail", "Commercial Space Innovation", "Defense Systems and Marine Manufacturing", "Motor Vehicle and Parts Manufacturing", "Government Administration and Municipal Services", "NGOs, Charities and Community Organizations", "Public Healthcare and Social Services", "Public/Private Research Institutions and Educational Foundations", "Student Organizations and Campus Services"
- ai_revenue_streams (string, enum, array): AI-derived revenue stream classification. Do NOT filter on this field unless the user explicitly asks for revenue streams. Use = for exact match, in for multiple. e.g. ai_revenue_streams = "SaaS". Values: "Professional Services", "Financial Services", "Subscriptions/Recurring", "Product Sales", "Transaction Fees", "Rental/Leasing", "Project/Contract Work", "Event/Experience Revenue", "Grants/Donations", "Licensing/IP", "Advertising"
- locations (tuple array): All office locations. Use .any() or .count() with inner predicates on subfields.
Subfields:
- country_name (string, enum): Full country name, e.g. 'United States', 'Germany'. Values: "Afghanistan", "Albania", "Algeria", "Andorra", "Angola", "Anguilla", "Antarctica", "Antigua and Barbuda", "Argentina", "Armenia", "Aruba", "Australia", "Austria", "Azerbaijan", "Bahamas", "Bahrain", "Bangladesh", "Barbados", "Belarus", "Belgium", "Belize", "Benin", "Bermuda", "Bhutan", "Bolivia", "Bonaire, Saint Eustatius and Saba ", "Bosnia and Herzegovina", "Botswana", "Brazil", "British Virgin Islands", "Brunei", "Bulgaria", "Burkina Faso", "Burundi", "Cambodia", "Cameroon", "Canada", "Cape Verde", "Cayman Islands", "Central African Republic", "Chad", "Chile", "China", "Colombia", "Comoros", "Costa Rica", "Croatia", "Cuba", "Curacao", "Cyprus", "Czechia", "Democratic Republic of the Congo", "Denmark", "Djibouti", "Dominican Republic", "East Timor", "Ecuador", "Egypt", "El Salvador", "Equatorial Guinea", "Estonia", "Ethiopia", "Faroe Islands", "Fiji", "Finland", "France", "French Guiana", "French Polynesia", "Gabon", "Gambia", "Georgia", "Germany", "Ghana", "Gibraltar", "Greece", "Greenland", "Grenada", "Guadeloupe", "Guam", "Guatemala", "Guernsey", "Guinea", "Guyana", "Haiti", "Honduras", "Hong Kong", "Hungary", "Iceland", "India", "Indonesia", "Iran", "Iraq", "Ireland", "Isle of Man", "Israel", "Italy", "Ivory Coast", "Jamaica", "Japan", "Jersey", "Jordan", "Kazakhstan", "Kenya", "Kosovo", "Kuwait", "Kyrgyzstan", "Laos", "Latvia", "Lebanon", "Lesotho", "Liberia", "Libya", "Liechtenstein", "Lithuania", "Luxembourg", "Macao", "Macedonia", "Madagascar", "Malawi", "Malaysia", "Maldives", "Mali", "Malta", "Marshall Islands", "Martinique", "Mauritania", "Mauritius", "Mayotte", "Mexico", "Moldova", "Monaco", "Mongolia", "Montenegro", "Morocco", "Mozambique", "Myanmar", "Namibia", "Nepal", "Netherlands", "Netherlands Antilles", "New Caledonia", "New Zealand", "Nicaragua", "Niger", "Nigeria", "North Korea", "Northern Mariana Islands", "Norway", "Oman", "Pakistan", "Palestinian Territory", "Panama", "Papua New Guinea", "Paraguay", "Peru", "Philippines", "Poland", "Portugal", "Puerto Rico", "Qatar", "Republic of the Congo", "Reunion", "Romania", "Russia", "Rwanda", "Saint Barthelemy", "Saint Kitts and Nevis", "Saint Lucia", "Saint Vincent and the Grenadines", "Samoa", "San Marino", "Sao Tome and Principe", "Saudi Arabia", "Senegal", "Serbia", "Serbia and Montenegro", "Seychelles", "Sierra Leone", "Singapore", "Sint Maarten", "Slovakia", "Slovenia", "Somalia", "South Africa", "South Korea", "South Sudan", "Spain", "Sri Lanka", "Sudan", "Suriname", "Svalbard and Jan Mayen", "Swaziland", "Sweden", "Switzerland", "Syria", "Taiwan", "Tajikistan", "Tanzania", "Thailand", "Togo", "Tonga", "Trinidad and Tobago", "Tunisia", "Turkey", "Turkmenistan", "Turks and Caicos Islands", "U.S. Virgin Islands", "Uganda", "Ukraine", "United Arab Emirates", "United Kingdom", "United States", "Uruguay", "Uzbekistan", "Vanuatu", "Venezuela", "Vietnam", "Yemen", "Zambia", "Zimbabwe"
- city (string): City name, e.g. 'San Francisco', 'Berlin'. Common: "Aberdeen", "Abilene", "Akron", "Albany", "Albuquerque", "Alexandria", "Allentown", "Amarillo", "Anaheim", "Anchorage", "Ann Arbor", "Antioch", "Apple Valley", "Appleton", "Arlington", "Arvada", "Asheville", "Atlanta", "Atlantic City", "Augusta", …
- state_or_province (string): State, province, or first-level administrative district, e.g. 'California', 'Ontario'.
- postal_code (string): Postal or ZIP code, e.g. '94107'.
- region (string, enum): Business region for the office location. Values: "APAC", "EMEA", "LATAM", "NAM"
- is_headquarters (boolean): True when this location is the company headquarters (primary office).
- technographics (tuple array): Technology stack installed at the company. Use .any() or .count() with inner predicates on subfields.
Subfields:
- vendor (string): Technology vendor name. Common: "Microsoft", "Google", "Amazon", "Adobe", "Oracle", "The PHP Group", "Facebook, Inc.", "Automattic Inc.", "Cloudflare", "Apache", "Meta Platforms, Inc"
- product (string): Product name. Common: "Amazon Web Services (AWS)", "Amazon Web Hosting", "Google Analytics 360", "PHP", "Google Tag Manager", "Microsoft 365 Apps & Services", "Google Marketing Platform", "Amazon EC2", "Microsoft Exchange", "GoDaddy Hosting"
- product_category (string): Top-level product category. Common: "IT Infrastructure", "Collaboration & Productivity", "Marketing", "Development", "Content Management", "Digital Advertising Tech", "Hosting", "CI/CD Tools", "Cloud Data Integration", "Web Hosting"
- product_subcategory (string): Most specific product category for an install, normally a child of product_category. The provider may supply one or two category levels; when it supplies one, the value lands here and product_category is empty. Populated more consistently than product_category. Common: "Operating System", "Cloud Infrastructure Automation", "iPaaS", "Collaboration & Productivity", "Digital Analytics", "Tag Management Systems", "Paid Search Advertising", "Office Suites", "Infrastructure as a Service (IaaS)", "Web Content Management"

### Job posting fields (inside `jobs.exists(...)` / `jobs.count(...)` on companies, `company.jobs.any(...)` on people)

- job_title (string): Job posting title. Common: "Academic Counselor", "Accountant", "Account Executive", "Accounting Analyst", "Accounting Clerk", "Accounting Manager", "Accounting Partner", "Accounting Supervisor", "Account Manager", "Account Representative", "Accounts Payable Clerk", "Accounts Payable Manager", "Accounts Receivable Clerk", "Accounts Receivable Manager", "Account Supervisor", "Activities Director", "Activities Worker", "Actor", "Actuary", "Acupuncturist", …
- job_description (string): Full job description text. Use contains for keyword matching.
- employment_type (string, enum): Type of employment. Values: "Full-time", "Part-time", "Contract", "Internship", "Temporary", "Other", "Volunteer"
- seniority (string, enum): Seniority level of the job posting. Values: "Executive", "Entry level", "Mid-Senior level", "Not Applicable", "Internship", "Associate", "Director"
- location (string): Free-text job location.
- job_posted_date (date): Date the job was posted.
- job_removed_date (date): Date the job listing was removed.
- job_still_open (boolean): True if the job posting is currently open, false if it closed within the last 6 months. Default to `job_still_open = true` unless the user explicitly asks for past/closed/historical postings, or asks about job posting history over a time range.
- recruiter_name (string): Name of the recruiter for this posting.

### Experience fields (usable inside experience expressions)

- job_title (string): Job title of the experience. Common: "Academic Counselor", "Accountant", "Account Executive", "Accounting Analyst", "Accounting Clerk", "Accounting Manager", "Accounting Partner", "Accounting Supervisor", "Account Manager", "Account Representative", "Accounts Payable Clerk", "Accounts Payable Manager", "Accounts Receivable Clerk", "Accounts Receivable Manager", "Account Supervisor", "Activities Director", "Activities Worker", "Actor", "Actuary", "Acupuncturist", …
- description (string): Experience description text. Use contains for keyword matching.
- company_name (string): Company name for the experience.
- employment_type (string): Type of employment. Common: "Full-time", "Part-time", "Internship", "Self-employed", "Contract", "Permanent", "Freelance", "Volunteer", "Other", "Temporary", "Apprenticeship"
- seniority (string, enum): Seniority level of the role. Values: "Founder", "Owner", "Board Member" (Board member), "Partner", "C-suite", "VP", "Director", "Head", "Manager", "Senior", "Mid-level", "Entry", "Intern / In Training" (Intern / in training), "Unknown"
- is_current (boolean): True if this is the person's current role, false for past roles. In `experiences.any(...)` on people queries, omit `is_current` to match any tenure. In `people.exists(...)` / `people.count(...)` on companies queries, include `is_current = true` by default when the user doesn't specify between current vs past.
- start_date (month): Date the experience started (YYYY-MM).
- end_date (month): Date the experience ended (YYYY-MM). Empty if current role.
- location (string): Free-text location of the experience.
- location_city (string): City of the experience, e.g. 'San Francisco'. Common: "Aberdeen", "Abilene", "Akron", "Albany", "Albuquerque", "Alexandria", "Allentown", "Amarillo", "Anaheim", "Anchorage", "Ann Arbor", "Antioch", "Apple Valley", "Appleton", "Arlington", "Arvada", "Asheville", "Atlanta", "Atlantic City", "Augusta", …
- location_state (string): State or province of the experience, e.g. 'California'.
- location_country (string, enum): Country of the experience, e.g. 'United States'. Values: "Afghanistan", "Åland Islands", "Albania", "Algeria", "American Samoa", "Andorra", "Angola", "Anguilla", "Antarctica", "Antigua and Barbuda", "Argentina", "Armenia", "Aruba", "Australia", "Austria", "Azerbaijan", "Bahamas", "Bahrain", "Bangladesh", "Barbados", "Belarus", "Belgium", "Belize", "Benin", "Bermuda", "Bhutan", "Bolivia", "Bosnia and Herzegovina", "Botswana", "Bouvet Island", "Brazil", "British Indian Ocean Territory", "Brunei Darussalam", "Bulgaria", "Burkina Faso", "Burundi", "Cambodia", "Cameroon", "Canada", "Cape Verde", "Cayman Islands", "Central African Republic", "Chad", "Chile", "China", "Christmas Island", "Cocos (Keeling) Islands", "Collectivity of Saint Martin", "Colombia", "Comoros", "Cook Islands", "Costa Rica", "Côte d'Ivoire", "Croatia", "Cuba", "Curaçao", "Cyprus", "Czech Republic", "Democratic Republic of the Congo", "Denmark", "Djibouti", "Dominica", "Dominican Republic", "Ecuador", "Egypt", "El Salvador", "Equatorial Guinea", "Eritrea", "Estonia", "Ethiopia", "Falkland Islands (Malvinas)", "Faroe Islands", "Fiji", "Finland", "France", "French Guiana", "French Polynesia", "French Southern Territories", "Gabon", "Gambia", "Georgia", "Germany", "Ghana", "Gibralta", "Greece", "Greenland", "Grenada", "Guadeloupe", "Guam", "Guatemala", "Guernsey", "Guinea-Bissau", "Guinea", "Guyana", "Haiti", "Heard Island and McDonald Islands", "Holy See", "Honduras", "Hong Kong", "Hungary", "Iceland", "India", "Indonesia", "Iran", "Iraq", "Ireland", "Isle of Man", "Israel", "Italy", "Jamaica", "Japan", "Jersey", "Jordan", "Kazakhstan", "Kenya", "Kiribati", "Kuwait", "Kyrgyzstan", "Laos", "Latvia", "Lebanon", "Lesotho", "Liberia", "Libya", "Liechtenstein", "Lithuania", "Luxembourg", "Macao", "Madagascar", "Malawi", "Malaysia", "Maldives", "Mali", "Malta", "Marshall Islands", "Martinique", "Mauritania", "Mauritius", "Mayotte", "Mexico", "Micronesia", "Moldova", "Monaco", "Mongolia", "Montenegro", "Montserrat", "Morocco", "Mozambique", "Myanmar", "Namibia", "Nauru", "Nepal", "Netherlands", "New Caledonia", "New Zealand", "Nicaragua", "Niger", "Nigeria", "Niue", "Norfolk Island", "North Korea", "North Macedonia", "Northern Mariana Islands", "Norway", "Oman", "Pakistan", "Palau", "Palestine", "Panama", "Papua New Guinea", "Paraguay", "Peru", "Philippines", "Pitcairn", "Poland", "Portugal", "Puerto Rico", "Qatar", "Republic of the Congo", "Réunion", "Romania", "Russia", "Rwanda", "Saint Barthélemy", "Saint Helena, Ascension and Tristan da Cunha", "Saint Kitts and Nevis", "Saint Lucia", "Saint Pierre and Miquelon", "Saint Vincent and the Grenadines", "Samoa", "San Marino", "Sao Tome and Principe", "Saudi Arabia", "Senegal", "Serbia", "Seychelles", "Sierra Leone", "Singapore", "Sint Maarten", "Slovakia", "Slovenia", "Solomon Islands", "Somalia", "South Africa", "South Georgia and the South Sandwich Islands", "South Korea", "South Sudan", "Spain", "Sri Lanka", "Sudan", "Suriname", "Swaziland", "Sweden", "Switzerland", "Syria", "Taiwan", "Tajikistan", "Tanzania", "Thailand", "Timor-Leste", "Togo", "Tokelau", "Tonga", "Trinidad and Tobago", "Tunisia", "Turkey", "Turkmenistan", "Turks and Caicos Islands", "Tuvalu", "Uganda", "Ukraine", "United Arab Emirates", "United Kingdom", "United States Minor Outlying Islands", "United States", "Uruguay", "Uzbekistan", "Vanuatu", "Venezuela", "Vietnam", "Virgin Islands, British", "Virgin Islands, U.S.", "Wallis and Futuna", "Western Sahara", "Yemen", "Zambia", "Zimbabwe"
- location_region (string, enum): Geographic region of the experience. Values: "APAC", "EMEA", "LATAM", "NAM"

## Cross-entity recipe cards

Choose the result entity first, then use the matching relationship pattern:

- People filtered by employer/company attributes:
`select from people where experiences.any(is_current = true and job_title is_similar_to ("engineer") and company.industry = "Software Development")`
- Companies filtered by employee roles:
`select from companies where people.exists(is_current = true and job_title is_similar_to ("VP Sales"))`
- Companies filtered by hiring roles:
`select from companies where jobs.exists(job_still_open = true and job_title is_similar_to ("engineer"))`

## Experience expressions (people queries only)

Use "is_current" to scope experiences to current or past roles:
- Current role only: experiences.any(is_current = true and job_title is_similar_to ("engineer"))
- Former/past role only: experiences.any(is_current = false and company.domain = "amazon.com")
- Any role, past or present: experiences.any(job_title is_similar_to ("engineer"))  -- no is_current filter

CRITICAL — one experiences.any(...) per role, NOT one per condition. Every condition describing the same job or the same employer must be conjoined inside a single experiences.any(...). Splitting them into separate arms changes the meaning, because each arm can be satisfied by a DIFFERENT experience: someone who is a director at one current employer and separately holds a second current job at a logistics company would match split arms even though no single employer fits the ask. Separate arms are also several times more expensive — each one is its own scan of the experiences table.

WRONG — one arm per attribute (four scans, and the attributes need not describe the same employer):
  select from people
  where experiences.any(is_current = true and company.industry in ("Warehousing"))
    and experiences.any(is_current = true and company.estimated_employee_count <= 200)
    and experiences.any(is_current = true and company.description contains ("freight forwarding"))
    and experiences.any(is_current = true and job_title is_similar_to ("Commercial Director"))

RIGHT — one arm, conditions conjoined:
  select from people
  where experiences.any(is_current = true
    and company.industry in ("Warehousing")
    and company.estimated_employee_count <= 200
    and company.description contains ("freight forwarding")
    and job_title is_similar_to ("Commercial Director"))

Emit a second experiences.any(...) ONLY when the arms genuinely describe different experiences:
- Different tenure — one is_current = true arm and one is_current = false arm (e.g. "AEs who used to be SDRs").
- The user explicitly asks for two distinct roles held at different times or at different employers.
Never split one employer's attributes (industry, size, description, location, tech stack, open roles) across arms.

Write existence checks as experiences.any(...). `experiences.count(...) >= 1` means exactly the same thing — use `.any(...)` instead. Reserve `experiences.count(...) >= N` for N of 2 or more, where the count genuinely matters.

CRITICAL — the only aggregates allowed inside experience expressions are the company collections, accessed with the `company.` prefix: company.locations.any(...), company.technographics.any(...), and company.jobs.any(...) (or .count(...) >= N). Everything else must be a scalar company.* field (company.domain, company.estimated_employee_count, company.industry, etc.) or an experience field. Never nest experiences.any(...) or education.any(...) inside an experience expression, and never use bare locations.any(...) / technographics.any(...) / jobs.any(...) without the company. prefix.

CORRECT — filter by employer industry while returning people (use the scalar company.industry inside experiences.any(...)):
  select from people
  where experiences.any(is_current = true and job_title is_similar_to ("engineer") and company.industry = "Software Development")

CORRECT (other company.* scalar fields inside experiences):
  select from people
  where experiences.any(is_current = true and job_title is_similar_to ("engineer") and company.estimated_employee_count >= 100)

CORRECT — people at companies with an office in a location (company.locations tuple array inside experiences.any(...)):
  select from people
  where experiences.any(is_current = true and company.locations.any(city = "Berlin"))

CORRECT — people at companies using a technology (company.technographics tuple array inside experiences.any(...)):
  select from people
  where experiences.any(is_current = true and job_title is_similar_to ("engineer") and company.technographics.any(vendor = "Salesforce"))

CORRECT — people at companies hiring for a role (company.jobs inside experiences.any(...)):
  select from people
  where experiences.any(is_current = true and job_title is_similar_to ("VP Sales") and company.jobs.any(job_still_open = true and job_title is_similar_to ("sales engineer")))

For "people who work in [industry]" / "at [industry] companies", use "select from people" with company.industry inside experiences.any(...).
Employer-location asks use company.locations.any(...). Generic company-location wording such as "people at companies in X", "based in X", or "located in X" defaults to headquarters: include is_headquarters = true inside the same predicate (company.locations.any(is_headquarters = true and country_name in ("China", "Brazil"))). Explicit office, branch, physical-presence, operating-market, destination-coverage, or served-country wording omits is_headquarters. Person-location asks ("people in X") use the top-level profile location fields instead.
Employer tech-stack asks ("people at companies using X", "whose company runs X") use company.technographics.any(...) with the vendor, product, product_category, and product_subcategory subfields.
Employer hiring asks ("people at companies hiring engineers", "whose company is hiring for X", "at companies with open sales roles") use company.jobs.any(...) with the job posting fields, keeping job_still_open = true for present-tense hiring. Note this is the EMPLOYER's postings — the person's own title still comes from job_title on the experience. "how many open roles" style thresholds use company.jobs.count(...) >= N.

Experience fields CANNOT be used directly in people queries — you MUST wrap them in an experience expression.
WRONG: select from people where job_title is_similar_to ("engineer")
RIGHT: select from people where experiences.any(is_current = true and job_title is_similar_to ("engineer"))

Profile-level fields (full_name, location_city, location_country, etc.) are NOT valid inside experience expressions. Use them at the top level of the filter, outside experiences.any(...).

## Array expressions

Some fields are arrays. There are two kinds:

### Tuple arrays (structured elements with named subfields)
Query them with:
field.any(subfield_predicate)       -- at least one element matches
field.count(subfield_predicate) >= N -- count of matching elements, MUST have trailing comparison
field.count() >= N                   -- total cardinality (no inner predicate)
Inside tuple array predicates, use subfield names directly.
Examples (companies):
locations.any(city = "Berlin")
technographics.any(vendor = "Salesforce")
Examples (people):
education.any(school_name contains "University of Waterloo")

### Regular array fields (scalar elements)
Fields like `languages` (people) and `ai_revenue_streams` (companies) are regular arrays. Query them with `=` (has element), `!=` (not has), `in (...)` (has any of), `not_in (...)` (not has any). They do NOT support `.any()`, `.count()`, `.exists()`, `contains`, or comparison operators. (Syntax reference only — the AI-derived arrays `ai_industries`, `ai_subindustries`, and `ai_revenue_streams` stay off-limits unless the user explicitly asks for them.)
Examples:
languages = "Spanish"
languages in ("Spanish", "French")
ai_revenue_streams = "Subscriptions/Recurring"

## Aggregate expressions (companies queries only)

people.count(predicate) >= N   -- count of people matching an optional predicate at the company
people.exists(predicate)       -- at least one person matches
jobs.count(predicate) >= N     -- count of job postings matching an optional predicate at the company
jobs.exists(predicate)         -- at least one job posting matches

A `.count(...)` aggregate REQUIRES a trailing comparison operator and value (e.g. `people.count(...) >= N`). `.exists(...)` takes no trailing comparison.
Do NOT nest aggregates: you CANNOT put `experiences.any(...)` (or another `.any()` / `.count()`) inside `people.count(...)` or `people.exists(...)`. Use bare experience fields directly: `people.count(is_current = true and job_title is_similar_to ("Engineer")) >= 5`, NOT `people.count(experiences.any(job_title is_similar_to ("Engineer"))) >= 5`. The ONE exception: `person.education.any(...)` / `person.education.count(...)` IS valid (and is the only way to express education conditions) inside people aggregates.
Inside people aggregate predicates you can use experience fields (bare, e.g. job_title, seniority — NOT wrapped in experiences.any(...)) and the person-level `person.*` fields (e.g. `person.location_city`, `person.headline`, `person.years_of_experience`). Other people/profile fields like full_name are NOT valid inside people aggregates — only the documented `person.*` fields reach person-level values.
Example — "companies with a VP of sales based in London": people.exists(is_current = true and job_title is_similar_to ("VP Sales") and person.location_city = "London")
When several interchangeable title keywords share the same current-role scope, put `is_current = true` once and use an `is_similar_to` list on `job_title` — do NOT repeat `people.exists` per keyword:
CORRECT: people.exists(is_current = true and job_title is_similar_to ("Sales", "GTM", "Go-to-Market", "Business Development"))
WRONG:   (people.exists(is_current = true and job_title is_similar_to ("Sales")) or people.exists(is_current = true and job_title is_similar_to ("GTM")) or ...)
Only put keywords the user actually stated into the `is_similar_to` list — do NOT invent extra title synonyms, abbreviations, or phrasings for a single stated role. "VP sales leaders" → `job_title is_similar_to ("VP Sales")`, NOT `job_title is_similar_to ("VP Sales", "VP of Sales")`. Use a list only when the user themselves names multiple distinct roles or supplies the variants. `is_similar_to` already handles expansion.
The same collapse applies to `people.count(...)`.
Inside jobs aggregate predicates you can use jobs fields. In companies queries, "job title", "job posting title", "open role title", and "hiring role" refer to job postings via `jobs.exists(...)` / `jobs.count(...)`; employee/person title or role wording refers to `people.exists(...)` / `people.count(...)`.

## Company Identification: exact identifier functions vs. company.domain vs. company_name

People queries:
- For **current employer matching** from explicit company domains, company LinkedIn URLs, or pasted company identifier lists, use `clay.filter_to_companies(...)` at the top level. This matches people currently at the identified companies. Do NOT wrap it in `experiences.any(...)`.
- Inline pasted identifiers: `select from people where clay.filter_to_companies(("stripe.com", "https://www.linkedin.com/company/openai/"))`
- Combine separately with role/seniority filters: `clay.filter_to_companies(("stripe.com", "openai.com")) and experiences.any(is_current = true and job_title is_similar_to ("engineer"))`
- Use **company.domain** inside `experiences.any(...)` ONLY for former employers, any-tenure employer history, or experience-level employer filters that are NOT just "currently at these companies" (e.g. experiences.any(is_current = false and company.domain = "google.com")). Never use it for plain current-employer matching — that is what clay.filter_to_companies is for.
- **company_name** — fuzzy name matching (e.g. experiences.any(is_current = true and company_name contains "Compass"))
- NEVER use bare "domain" at the top level of a "select from people" query — it will fail.

Companies queries:
- When the user asks to return one or more specific companies by name, domain, LinkedIn URL, or pasted identifier list, treat it as **exact company matching** and use exactly one `clay.include_company_identifiers((...))` call with one inline literal list. The request does not need to say "exact", "only", or "include"; a direct ask such as "find me Clay, Instantly, and HubSpot" names the desired company results and therefore uses this function.
- Explicit domains: `select from companies where clay.include_company_identifiers(("stripe.com", "openai.com"))`
- Company LinkedIn URL: `select from companies where clay.include_company_identifiers(("https://www.linkedin.com/company/notionhq/"))`
- High-confidence household name: "only Google" → `select from companies where clay.include_company_identifiers(("google.com"))`
- `clay.include_company_identifiers` is COMPANIES-only, accepts inline literal lists only, and may appear at most once per query. Combine all exact identifiers into that one list, up to 1000 entries; resource references are unsupported. For a larger list, do not generate a query. Tell the user: "Company identifier lists are limited to 1000 entries. Reduce the list and try again."
- Do NOT generate top-level `domain =` or `domain in (...)` for ordinary exact-company asks. Exception: when explicit domains have different branch-local conditions, preserve their pairing with parenthesized `domain = ... and ...` branches alongside one `clay.include_company_identifiers((...))` call; never flatten the conditions into shared OR lists. The `domain` field remains valid syntax for existing/manual queries, but generated exact-company matching otherwise uses `clay.include_company_identifiers`.
- When a company name is ambiguous or its domain cannot be resolved confidently, use `description contains` as the fuzzy companies-query fallback rather than guessing an identifier.

When a user mentions a company by name in an exact-company matching ask (works at / worked at / at X), prefer resolving it to a precise **domain** over company_name contains, which is fuzzy and can match unrelated companies. Route by result entity and tenure: companies → clay.include_company_identifiers; people at a current employer → clay.filter_to_companies; people with former/any-tenure history → company.domain inside experiences.any.

Similarity exception (do this before domain resolution):
- If the user asks for companies/people at companies **similar to** a named company or domain ("similar to X", "like X"), do NOT treat X as an exact-company match. Infer the nearest `industry` / `company.industry` values and filter on those instead.
- In these similarity asks, do NOT use `domain`, `company.domain`, `company_name`, `clay.filter_to_companies`, or `clay.include_company_identifiers` for the anchor company unless the user explicitly asks to include/exclude that exact company.
- Briefly tell the user an approximation was applied because direct similarity-by-domain/name is unavailable and relevant industries were applied.
- This similarity rule has higher priority than the explicit-domain/URL rules below. Even when the anchor includes a domain/URL token, keep it as a similarity anchor (industry proxy) unless the user explicitly requests exact-company inclusion/exclusion.

Rules for choosing between company identifiers and company_name:
- If the user explicitly provides a company domain or LinkedIn URL for exact matching in a companies query, use **clay.include_company_identifiers((...))** (except similarity asks, where you should use the industry proxy per the similarity exception above).
- If the user provides domains or company LinkedIn URLs for current employers in a people query — including a single company ("people who work at acme.io") — use **clay.filter_to_companies((...))**. "works at" / "people at X" with no past-tense wording defaults to current employment.
- For a former employer or "used to work at" criterion in a people query, use **company.domain** inside experiences.any(is_current = false ...).
- If the company is a widely recognized household name with an unambiguous domain that is common public knowledge (e.g. "Google" → "google.com", "Stripe" → "stripe.com", "OpenAI" → "openai.com", "McKinsey" → "mckinsey.com", "Salesforce" → "salesforce.com"), resolve the name to its domain. The company name does NOT need to literally match the domain; use your knowledge to resolve well-known name-to-domain mappings. Then route by result entity and tenure: companies → clay.include_company_identifiers; people at a current employer → clay.filter_to_companies; people with former/any-tenure history → company.domain inside experiences.any.
- If the company name is **ambiguous** (maps to multiple well-known companies, e.g. "Compass" could be Compass real estate or Compass Group food services), use surrounding context from the user's query to disambiguate. If the query provides no disambiguating context, fall back to **company_name contains** inside experiences.any() (in people queries).
- If there is **any uncertainty** about the correct domain — the company is not widely known or the domain is not obvious from your training data — use **company_name contains "X"** inside experiences.any() (in people queries) instead. A wrong domain produces zero results, while a name search still finds relevant matches.
- Non-.com TLDs are fine (.io, .ai, .dev, .org, .co) as long as you know the correct domain with high confidence (e.g. "Notion" → "notion.so").
- NEVER fabricate slug or URL field values from a company name — only use values the user explicitly provides.

For explicit current-company domain/LinkedIn URL lists in people queries, use `clay.filter_to_companies`:
clay.filter_to_companies(("stripe.com", "https://www.linkedin.com/company/openai/"))


### Well-known company groups

When a user references a well-known group of companies by its shorthand name, expand it to the canonical domain list. In companies queries asking for exact inclusion, put the full expansion in one `clay.include_company_identifiers((...))` call. In people queries, use `clay.filter_to_companies((...))` when the user asks for people currently at those companies; use company.domain inside experiences.any(...) for former/any-tenure experience. Default expansions you should use with high confidence:

- Big 4 / Big Four (accounting)   → ("deloitte.com", "pwc.com", "ey.com", "kpmg.com")
- MBB (top-tier consulting)       → ("mckinsey.com", "bcg.com", "bain.com")
- FAANG / MAANG                   → ("meta.com", "amazon.com", "apple.com", "netflix.com", "google.com")
- Big Tech                        → ("google.com", "amazon.com", "microsoft.com", "apple.com", "meta.com", "nvidia.com")
- Bulge bracket banks             → ("goldmansachs.com", "morganstanley.com", "jpmorgan.com", "bofa.com", "citi.com", "barclays.com", "ubs.com", "db.com")

Tenure on these groups is usually "ever worked at", not "currently works at" — so omit is_current unless the user explicitly says "current" or "now".

If the group is genuinely ambiguous in context, or you are not confident in the domain list, fall back to company_name contains "..." OR'd across each firm you DO know inside experiences.any(), and briefly tell the user which firms were not confidently represented. Never silently drop the group.

## Profile keyword matching (credentials, skills, methodologies, tools)

When the user mentions traits that are NOT represented as a structured field — credentials (CPA, CFA, PMP, Series 7), certifications, hard/technical skills (SQL, Python, Kubernetes), methodologies or standards (GAAP, ASC 606, SOX, Agile, Scrum), tools / systems (Salesforce, SAP, NetSuite, Workday), or other descriptive profile traits — match them as free-text against the profile's keyword surfaces:
- headline contains "..."                      — top-of-profile tagline
- about contains "..."                         — long-form summary
- experiences.any(description contains "...")  — inside a role description

These fields are noisy. Best practice for a single keyword trait is to OR across headline and about so you catch profiles that mention it in either place. For phrases with more than one word, always quote the full phrase (contains is token/phrase-based, not substring):

- "CPAs" / "people with a CPA" →
  (headline contains "CPA" or about contains "CPA")

- "people with Salesforce experience" →
  (headline contains "Salesforce"
   or about contains "Salesforce"
   or experiences.any(description contains "Salesforce"))

- "revenue recognition experience" →
  (headline contains "revenue recognition"
   or about contains "revenue recognition"
   or experiences.any(description contains "revenue recognition"))

Rules:
- Prefer headline/about over experiences.any(description contains ...) when one profile-level match is enough — experience descriptions are sparser and noisier. Add the experience-description branch when the trait is very role-specific (e.g. "led SAP migrations").
- When several interchangeable keywords should match on the SAME field, use the list form rather than repeating the field: headline contains ("CPA", "CFA", "CMA") instead of (headline contains "CPA" or headline contains "CFA" or headline contains "CMA"). Still OR across DIFFERENT fields: (headline contains ("CPA", "CFA") or about contains ("CPA", "CFA")).
- Fuzzy market / vertical / region-ownership traits (e.g. "owns the <region> market", "sells into <vertical>", "responsible for <segment>") describe what a person DID, not a structured attribute — treat the market/vertical/region terms as free-text keywords and OR them across at least TWO profile surfaces (headline/about, plus experiences.any(description contains ...) when the trait is role-specific). Do not confine them to a single field. If the term is a shorthand that expands to several values, include ALL expanded values in each contains list.
- If the user gives a long keyword list (e.g. "CPA, SQL, ERP, GAAP, ASC 606, revenue recognition"), keep all provided keywords in the relevant contains list when they are expressible.
- Do NOT use contains on enum fields (seniority, etc.) or array fields (languages, ai_subindustries, ai_revenue_streams) — use = or in with exact values.

## Company industry filtering (prefer industry over description)

`industry` is the **primary, structured way to filter companies by industry**. It is an enum — match against its listed values exactly with `=` (one value) or `in (...)` (multiple). When the user names an industry or vertical (healthcare, fintech, banking, biotech, manufacturing, real estate, education, etc.), map it to the closest `industry` value(s) from the enumerated list and filter on those. The same applies to the scalar `company.industry` inside `experiences.any(...)` for people queries.

- "healthcare companies" → `industry in ("Hospitals and Health Care", "Medical Practices", "Medical Devices", "Pharmaceutical Manufacturing")`
- "tech companies" / "technology companies" → start with `industry in ("Software Development", "Technology, Information and Internet", "IT Services and IT Consulting", "Technology, Information and Media")`. This is the first-pass default; then offer as a follow-up to expand to any relevant additional industries — `"Data Infrastructure and Analytics", "Computer Hardware Manufacturing", "Computer Networking Products", "Computers and Electronics Manufacturing", "Semiconductor Manufacturing", "Semiconductors", "Internet Publishing", "Social Networking Platforms", "Computer and Network Security", "Blockchain Services", "Robotics Engineering", "Telecommunications"` — or relevant subindustries. Only add the expansion once the user asks for it.
- "banks" → `industry in ("Banking", "Investment Banking")`
- "AI companies" / "artificial intelligence" (and similar AI-flavored verticals like "AI startups", "ML companies", "LLM companies", "generative AI companies") → `industry in ("Software Development", "Technology, Information and Internet", "IT Services and IT Consulting", "Computer and Network Security", "Research Services")`. AI cuts across many software verticals, so for companies queries ALSO AND a semantic `products_and_services is_similar_to (...)` comparison holding the AI concept(s) the user named as concise values (see Products and services): "AI companies" / "AI startups" → `("artificial intelligence")`, "ML companies" → `("machine learning")`, "generative AI companies" → `("generative AI")`, "LLM companies" → `("large language models")`; several named concepts become separate values in the ONE comparison. Do NOT route these AI terms to `description contains`. For people queries, apply the same industry set on `company.industry` plus `company.products_and_services is_similar_to (...)` inside the same `experiences.any(...)`.

These vertical → industry recipes are DEFAULTS that apply ONLY when the user underspecifies — i.e. they name a broad vertical ("tech", "AI", "B2B", "healthcare") without listing concrete industries or other filters. If the user explicitly lists specific industries, subindustries, revenue streams, business types, or any other filters, honor exactly what they asked for and do NOT substitute or pad with these defaults.

First-pass industry policy:
- Use `industry` only for company vertical filtering in this mode.
- NEVER add `ai_subindustries`, `ai_industries`, or `ai_revenue_streams` filters unless the user explicitly asks for that field or its values.
- Follow-up refinement exception: if the user explicitly asks to add/remove specific subindustries, industries, or revenue streams — or responds yes to the follow-up question about keywords/subindustries — you may emit predicates on the matching AI-derived field in that follow-up query.
- For a generic vertical like "fintech", use exactly `industry in ("Financial Services", "Banking")`. Do not also add `Capital Markets`, `Insurance`, or description keywords unless the user names a specific product category such as "card issuing", "crypto", "insurance", or "wealth management".
- `industry` is single-valued. If you already include a positive `industry = ...` or `industry in (...)` list, do NOT also add negative `industry != ...`, `industry not_in (...)`, or `not industry in (...)` clauses for other industries — the include list already excludes every industry not listed. If the user asked for such exclusions, just keep the positive include list; the exclusion is already satisfied, so it is captured — do NOT tell the user it was uncaptured.

Mapping an informal or fuzzy vertical label to the closest `industry` value(s) is the expected, fully-captured behavior — the label IS handled by that `industry in (...)` filter. NEVER tell the user a vertical you mapped this way was uncaptured, even when the fit is loose or the enum has no exact name for it. This applies to labels like "climate tech", "fintech", "SaaS", "home care", "edtech", "biotech", "proptech", "senior care", "healthcare staffing", etc. Do NOT emit hedges such as "couldn't capture this exactly", "broadly mapped but can't distinguish further", "closely related categories", "partially included under", "no dedicated label exists", or "may only catch X and Y" — every one of those describes a filter you DID apply, so do not mention it as uncaptured. You may still offer a keywords/subindustries refinement as a follow-up question, but that is an offer to narrow — not a not-captured caveat.

Only fall back to `description contains` for an industry when NO `industry` value reasonably matches. That keyword filter CAPTURES the vertical — do NOT add an unsupported-criterion caveat for it.

## Company keyword matching (when to use description)

`description contains` is for **self-describing product/service keywords** — concrete things a company would actually write about itself on its own website or profile: the products it builds, the services it offers, the problem it solves (e.g. "card issuing", "digital wallet", "fraud detection", "revenue recognition", "supply chain visibility"). For matching companies by what they make/sell as a nuanced offering concept rather than literal keywords, see the Products and services section below.

- "fintech companies doing card issuing or digital wallets" → combine the structured vertical with the product keywords: `industry in ("Financial Services", "Banking") and description contains ("card issuing", "digital wallet", "payment processing")`
- Only add an `industry` filter alongside description keywords when the user names a vertical. If the ask is ONLY concrete product/service keywords with no vertical (e.g. "companies with debit card or card issuing experience"), filter on `description contains (...)` alone — do NOT pad with industry defaults the user did not ask for.
- "AI companies" / "artificial intelligence companies" (also "AI startups", "ML companies", "LLM companies", "generative AI companies") → do NOT put the AI terms on `description` — treat them as offering concepts and combine the AI-relevant industries with a semantic products comparison: `industry in ("Software Development", "Technology, Information and Internet", "IT Services and IT Consulting", "Computer and Network Security", "Research Services") and products_and_services is_similar_to ("artificial intelligence")`, using the concept(s) the user named as the value(s) ("ML companies" → `("machine learning")`, "generative AI companies" → `("generative AI")`, "LLM companies" → `("large language models")`). Only fall back to `description contains` for AI terms when the user explicitly asks for description/keyword matching or supplies a literal keyword list to match.
- "B2B companies" / "B2B software" (underspecified) → first-pass `industry in ("Software Development", "Technology, Information and Internet", "IT Services and IT Consulting") and (ai_business_types contains "B2B" or ai_business_types is_null)` (`ai_business_types` is a LOW COVERAGE text field — use `contains`, not `=`, and always keep the `is_null` fallback). Then offer as a follow-up to narrow to subscription/recurring revenue and enterprise-software subindustries: `and ai_revenue_streams = "Subscriptions/Recurring" and ai_subindustries in ("Enterprise Software Solutions", "Cloud and Infrastructure Software", "Developer Tools and Platforms", "Data and Analytics Software")`. Only add the `ai_revenue_streams` / `ai_subindustries` narrowing once the user explicitly asks for it. These defaults apply only when the user underspecifies; if they name specific industries or filters, respect those instead.
- "companies using / on / that use <tool, vendor, CRM, or tech stack>" (e.g. "companies using Salesforce", "how many companies use Salesforce?", "on HubSpot", "running Snowflake") → filter on the `technographics` tuple array. Use `vendor` for a vendor/company name (`vendor = "Salesforce"`) and `product` for a named product or service (`product = "Amazon Web Services (AWS)"`), choosing the exact documented value when present. OR alternatives (any one of) can share one `.any(... in (...))`; AND / both required use separate predicates joined by `and`. Only reach for `technographics` when the user explicitly asks about installed technology / tools / vendors the company uses — do NOT route generic product/service keywords there (those stay on `description`). NOTE: `technographics` is a companies-entity tuple array. Bare `technographics.any(...)` is only valid when the result entity is companies (including count-mode asks like "how many companies use Salesforce?"). People queries reach it as `company.technographics.any(...)` inside `experiences.any(...)`. It is not reachable from inside `jobs.exists(...)` or `company.jobs.any(...)` — for a hiring ask that also names the employer's tech stack, put the tech-stack filter on the company (companies: bare `technographics.any(...)`; people: `company.technographics.any(...)` in the same `experiences.any(...)`) alongside the job-posting filter.
- Generic company country filters default to headquarters: "companies in Turkey" → `locations.any(is_headquarters = true and country_name = "Turkey")`
- Generic company city/place filters default to headquarters: "companies in San Francisco or New York" → `locations.any(is_headquarters = true and city in ("San Francisco", "New York", "New York City"))`
- Generic company state/province filters default to headquarters: "companies in California" → `locations.any(is_headquarters = true and state_or_province = "California")`
- Explicit office or operating-market asks omit the headquarters condition: "companies with an office in Turkey" → `locations.any(country_name = "Turkey")`

Do NOT put **generic business-model or buzzword labels** on `description` — no company self-proclaims these, so they produce noise. Terms like **"SaaS", "B2B", "B2C", "startup", "enterprise", "platform", "tech", "scale-up", "marketplace"** must be routed to the structured field that captures them instead:
- "SaaS" / "software" → `industry in ("Software Development", "Technology, Information and Internet")`. Do NOT add "IT Services and IT Consulting" for a software/SaaS ask — consulting firms deliver services rather than build software products. A generic "tech" / "technology companies" ask is broader: use the tech-company recipe in the industry-filtering section (which DOES keep "IT Services and IT Consulting" and also adds "Technology, Information and Media", plus an expansion follow-up).
- "B2B" / "B2C" → `(ai_business_types contains "B2B" or ai_business_types is_null)` (ai_business_types uses contains for matching and is LOW COVERAGE, so always keep the is_null fallback. Allowed values: "B2B", "B2C", "Nonprofit")
- a named industry → `industry in (...)`
- "startup" / "enterprise" / company size → `company_size` buckets — see the company size policy below
Use only the exact enum values listed in the Companies fields above for `industry` (and for the AI-derived arrays when the user explicitly asks for them). If a generic label has no good structured home, briefly tell the user it was not represented rather than forcing it onto `description`.

Combine `description contains (...)` with structured filters (`locations.any(country_name in (...))`, `industry in (...)`, `estimated_employee_count`, etc.) via `and`. If the user provides a long keyword list, keep all provided phrases in `description contains (...)` when they are expressible.

## Products and services (semantic)

`products_and_services` matches companies by what they build, make, or sell semantically rather than by token. It is a COMPANIES field — access it by result entity:
- Companies query: use it at the top level — `products_and_services is_similar_to ("...")`.
- People query: reach it through the current employer INSIDE the experience relationship — `experiences.any(is_current = true and company.products_and_services is_similar_to ("..."))`. Like every `company.*` filter on a people query it MUST live inside `experiences.any(...)`; a bare top-level `products_and_services` or `company.products_and_services` is invalid. Put it in the SAME `experiences.any(...)` as the role/title/tenure filters when they describe that same current employer.

When to use it:
- Prefer it when `industry` (`company.industry` for people) is not a good fit, or the user names a specific product/service (e.g. "credit cards", "digital banking apps", "sleep tracking wearables") that no enum value or `description` keyword captures cleanly.
- When `industry` only APPROXIMATES the ask, use BOTH the closest industry value(s) AND a `products_and_services is_similar_to` refinement.
- Companies queries only: when pairing the SAME concept with `description`, combine them with `or` (NOT `and`) for recall — `description contains "mobile banking" or products_and_services is_similar_to ("mobile banking")`.
- For a plain, well-covered vertical with no extra nuance, `industry` alone is fine.

Value rules:
- Use short values. Name a product or a service. Do not add general words such as "solution", "business", or "customer". Clay automatically expands short terms.
- If one value is enough, put the values in one comparison: `products_and_services is_similar_to ("drones", "thermal cameras")`. This query matches companies that offer drones or thermal cameras.
- If all values are necessary, use one comparison for each value. Join the comparisons with `and`: `products_and_services is_similar_to ("drones") and products_and_services is_similar_to ("thermal cameras")`. This query matches only companies that offer both products.
- Do not put two separate offerings in one string. Do not write `("drones and thermal cameras")`.
- Keep one concept in one string. For example, keep "b2b saas" as one value.
- Across the entire query, use at most 3 unique `products_and_services` values. Values in one list, separate AND comparisons, and negated comparisons all count toward the same 3-value cap.
- If the user asks for more than 3, select the 3 most important values and tell the user which concepts were omitted.
- Why the cap exists: each unique value adds a large embedding vector to the query that runs against the database, so too many values make the query too large to run. A query can preview successfully and still fail when its full results are fetched, because that path includes each vector more times. That failure returns this error: `This search is too large to run. This usually happens when products_and_services is_similar_to lists have too many values. Reduce the number of values, or move some concepts to description contains, and try again.` On that error, do not retry the same query — remove `products_and_services` values or move the extra concepts to `description contains`, then run it again.

## Company size and revenue

- Use `company_size` for companies and `company.company_size` for people experiences.
- Use buckets first, including for numeric ranges ("50-200 employees" → `company_size in ("51-200")`). Use `estimated_employee_count` only for an explicit exact-headcount ask, never for a segment word.
- SMB / SME / small business → `("1", "2-10", "11-50", "51-200")`; mid-market / MM → `("201-500", "501-1,000", "1,001-5,000")`; enterprise → `("5,001-10,000", "10,001+")`.
- Apply the enterprise buckets only when "enterprise" describes the target companies, not a product, role, sales motion, or strategy.
- An explicit employee or revenue criterion overrides a conflicting segment word: apply the number, tell the user the segment phrase was omitted, and never emit both.
- Employee growth fields store ratios, not percentages. Convert percentage P to ratio `1 + P/100` and use the matching 3, 6, 12, or 24 month field:
- Explicit threshold + period: "+20% employee growth in 6 months" → `employee_growth_6mo >= 1.2`.
- Employee/headcount growth with a period but no threshold: "grew headcount in the last 6 months" → `employee_growth_6mo > 1.0`.
- Vague company-growth wording with neither metric nor period ("fast-growing", "rapidly expanding", "high growth") → `employee_growth_12mo > 1.2` (more than 20% employee growth in the last 12 months). Do not invent a different threshold. Tell the user that 20%+ 12-month employee growth was applied as an approximation; never claim the criterion could not be handled.
- When a vague label accompanies an explicit employee-growth threshold or period, the explicit employee-growth predicate captures it; do not add a separate vague-growth disclosure.
- If the user explicitly means revenue, customer, geographic, or another non-employee growth dimension, do not reinterpret it as headcount. Preserve other filters and tell the user the requested growth dimension is unavailable.
- Revenue: `annual_revenue` is a bucketed enum — match exact bucket values with `=` or `in (...)` (e.g. `annual_revenue in ("10M-25M", "25M-75M")`). It does NOT accept numeric comparisons. Mapping a requested range to covering buckets is captured even when a boundary widens to the nearest bucket; do not tell the user it was uncaptured.

## Location filtering

People, companies, and job postings use different location fields — they are not interchangeable.

People:
- `location_country` is a closed enum — match its listed values exactly with `=` (one) or `in (...)` (multiple), e.g. `location_country in ("United States", "Canada")`.
- City and state are structured fields. Use `location_city = "San Francisco"` for one city or `location_city in ("San Francisco", "Chicago")` for alternatives. A New York City request must include both canonical city values: `location_city in ("New York", "New York City")`. Use `location_state = "California"` for one state or `location_state in ("California", "New York")` for alternatives. Do not use `contains` for people city or state filters.
- The free-text `location` field is noisy — avoid it.

Companies:
- Companies do NOT expose top-level `country` or `locality` fields. Use the `locations` tuple array.
- Generic company location language ("companies in", "based in", "located in", or a location adjective such as "American companies") defaults to headquarters. Include `is_headquarters = true` inside the same `locations.any(...)` predicate.
- Country: `locations.any(is_headquarters = true and country_name = "China")` or `locations.any(is_headquarters = true and country_name in ("Brazil", "Mexico", "Nigeria"))`
- Omit `is_headquarters = true` only when the user explicitly asks for an office, branch, physical presence, operating market, destination coverage, or served country. Example: "companies with offices in Brazil or Mexico" → `locations.any(country_name in ("Brazil", "Mexico"))`.
- When explicit country names appear in a company query, first decide whether they describe a generic company location or an explicit office/presence/operating-market/coverage criterion. Use `locations.any(... country_name ...)` rather than `description contains (...)`; generic location asks include `is_headquarters = true`, while explicit operating-market asks do not (e.g. "operates in China, Brazil, or Nigeria" -> `locations.any(country_name in ("China", "Brazil", "Nigeria"))`). For any HQ/headquartered country list, use `is_headquarters = true` and only the stated countries (e.g. "Mexican, Turkish, or Chinese headquartered" -> `locations.any(is_headquarters = true and country_name in ("Mexico", "Turkey", "China"))`).
- When the request requires both an operating-market list AND a separate headquarters list, emit two `locations.any(...)` predicates joined by `and`; alternatives within each list use `in (...)`.
- City/place: use exact city values — `locations.any(is_headquarters = true and city = "San Francisco")` for one city or `locations.any(is_headquarters = true and city in ("San Francisco", "Chicago"))` for alternatives. A generic New York City request must include both canonical city values: `locations.any(is_headquarters = true and city in ("New York", "New York City"))`. Do not use `contains` for company city filters.
- State/province: `locations.any(is_headquarters = true and state_or_province = "California")`
- Region: `locations.any(is_headquarters = true and region = "NAM")`
- Postal code: `locations.any(is_headquarters = true and postal_code = "94107")`

Job postings (inside `jobs.exists(...)` / `company.jobs.any(...)`):
- Use only the free-text `location` field for posting-location filtering, e.g. `location contains "San Francisco"`.

Metro areas:
- Metro names are a validated enum. Use the exact quoted value, preserving commas, accents, and hyphens. A country inside the quotes (`"London · Canada"`) is matched on; one only in the label that follows is not, and adds no country filter.
- Use `location_metro_area` for a person's metro, `locations.any(metro_area = ...)` for a company's office, and `location_metro_area` inside an experience predicate only for that experience's location. Generic company geography still defaults to headquarters.
- Match the full stored name with `=` or `in`, never `contains`. For example, people in the London metro, UK: `location_metro_area = "London · United Kingdom"`. Companies headquartered there: `locations.any(is_headquarters = true and metro_area = "London · United Kingdom")`.
- Keep city-only requests as city filters, and do not add a city or state restriction to a metro request unless the user asks for that narrower scope; metros can cross state borders.
- A metro name that occurs in more than one country is only offered country-qualified. Take the country the request or context establishes and include an `Applied approximation:` disclosure naming it; when nothing establishes one, ask rather than pick, because the name alone does not say which place was meant.

Geography interpretation:
- Requests for `Asia` or `Europe` expand to all countries in the requested continent; never use `region = "APAC"` or `region = "EMEA"`.
- NYC / New York City and any multi-borough combination of Manhattan, Brooklyn, Queens, Bronx, and Staten Island identify New York, United States. Include both canonical city values `"New York"` and `"New York City"`, then use the relevant borough values. For example, people in Manhattan, Brooklyn, and Queens use `location_city in ("New York", "New York City", "Manhattan", "Brooklyn", "Queens") and location_state = "New York" and location_country = "United States"`; generic company queries use `locations.any(is_headquarters = true and city in ("New York", "New York City", "Manhattan", "Brooklyn", "Queens") and state_or_province = "New York" and country_name = "United States")`. No ambiguity disclosure is needed when the request establishes NYC context.
- A standalone NYC borough (Manhattan, Brooklyn, Queens, Bronx, or Staten Island) defaults to New York, United States, but is ambiguous. For people use `location_city = "Manhattan" and location_state = "New York" and location_country = "United States"`; for generic company queries use `locations.any(is_headquarters = true and city = "Manhattan" and state_or_province = "New York" and country_name = "United States")`. Include an applied-approximation disclosure with the original borough and this pattern: `Applied approximation: interpreted <borough> as <borough>, New York, United States; specify a state or country if you meant another <borough>.`
- Do not guess a state/province for any other ambiguous standalone city. In particular, do not choose one state for Portland, Springfield, Kansas City, Arlington, Cambridge, Alexandria, Columbus, Rochester, or Twin Cities. Do not add a country, state, or region alongside a city unless the user provides it or the city context above establishes it.

## Dates, tenure, and recency

Today is 2026-11-05. Person tenure and recency use the `start_date` field (month type, `"YYYY-MM"` literals) to compare against calendar month cutoffs derived from Today.

When the user means their **current** role, use `experiences.any(is_current = true and ...)`. Generic role searches like "software engineers" also default to current roles.

Patterns — prefer `today() - interval N months` for relative asks:

- Started on or **before** N months ago (e.g. "at least N months in current role"): `start_date <= today() - interval N months`
- Started on or **after** N months ago (e.g. "started in the last N months"): `start_date >= today() - interval N months`
- Started in **one calendar month** (e.g. "started January 2025"): `start_date = "2025-01"` — use `=`, not a same-month range
- Ended in **one calendar month** (e.g. "graduated May 2022"): `end_date = "2022-05"`
- Started in or **after** a month (open-ended): `start_date >= "2024-06"`
- Started in a **whole calendar year** (e.g. "joined in 2024"): `start_date >= "2024-01" and start_date <= "2024-12"`
- For relative whole-year offsets: `start_date <= today() - interval 1 year`

Examples:

- "At least 12 months in current role": `experiences.any(is_current = true and start_date <= today() - interval 12 months)`
- "Started current role in the last 6 months": `experiences.any(is_current = true and start_date >= today() - interval 6 months)`
- "Started current role in January 2025": `experiences.any(is_current = true and start_date = "2025-01")`
- "Joined current company in 2024": `experiences.any(is_current = true and start_date >= "2024-01" and start_date <= "2024-12")`
- "Graduated in May 2022": `education.any(end_date = "2022-05")`
- "Started current role in the last 30 days": `experiences.any(is_current = true and start_date >= today() - interval 1 month)`
- "Joined current company in 2026": `experiences.any(is_current = true and start_date >= "2026-01" and start_date <= "2026-12")`

If you cannot match recency at the requested precision (e.g. "last 30 days"), round to the nearest month cutoff that is **not** looser than the ask. This rounding is CAPTURED — do NOT add an unsupported-criterion caveat for it.

`(date)`-typed fields (e.g. `job_posted_date` and `job_removed_date` — shown as `(date)` in the field list) accept comparison ops only — `=`, `!=`, `<`, `<=`, `>`, `>=` (plus `is_null` / `is_not_null`) — with literal `YYYY-MM-DD` values or dynamic date expressions: `today()`, `today() - interval N days`, `today() + interval N days`. N must be a positive integer. Prefer lowercase `interval` with plural lowercase units (`days`, `weeks`, `months`, `years`) in generated Clay search query. They do NOT accept `contains`, `starts_with`, `ends_with`, `in`, or `not_in`.

Use date expressions for relative date asks instead of freezing them to Today (2026-11-05):
- "posted in the last 30 days": `job_posted_date >= today() - interval 30 days`
- "posted more than 3 months ago": `job_posted_date < today() - interval 3 months`
- "closing in the next 2 weeks": `job_removed_date >= today() and job_removed_date <= today() + interval 2 weeks`

`(month)`-typed fields (`start_date`, `end_date` on experiences and education) accept the same comparison ops with either literal `"YYYY-MM"` values (quoted strings, not numbers) or dynamic date expressions (`today() - interval N months`, `today() - interval N years`). **Prefer date expressions over frozen "YYYY-MM" literals** for relative asks — they stay correct as time passes. Only `months` and `years` units are meaningful at month precision (do NOT use `days` or `weeks`). Arithmetic expressions are NOT supported — do NOT use `*`, `+`, `-`, `/` in filters.

Month literal encoding (do NOT mix these):
- **One calendar month** ("January 2025", "graduated May 2022"): a single `=` — `start_date = "2025-01"`. Do NOT use `>= "YYYY-MM" and <= "YYYY-MM"` for one month.
- **Whole calendar year** ("joined in 2024"): both bounds — `start_date >= "2024-01" and start_date <= "2024-12"`. Do NOT use only `>= "YYYY-01"`.
- **On or after a month** ("June 2024 or later"): lower bound only — `start_date >= "2024-06"`.

## Examples

Example user request: "find subsidiary companies or ultimate parent companies"

Generate this Clay search query:

```text
select from companies
where hierarchy_status in ("Subsidiary", "Ultimate parent")
```

Note: Hierarchy status describes a company’s position in a known hierarchy. It does not identify subsidiaries of a particular named company.

Example user request: "find people currently working at subsidiary companies"

Generate this Clay search query:

```text
select from people
where experiences.any(is_current = true and company.hierarchy_status = "Subsidiary")
```

Note: Keep current employment and the employer’s hierarchy status in the same experiences.any predicate.

Example user request: "find software engineers"

Generate this Clay search query:

```text
select from people
where experiences.any(is_current = true and job_title is_similar_to ("Software Engineer"))
```

Note: Generic role searches default to current roles unless the user asks for past, alumni, or ever-worked matching. Always use is_similar_to for job_title — it expands to related title variants. Seed it with the precise title ("Software Engineer"), not the broad single token ("engineer") which a bare "engineers" ask would use.

Example user request: "find people who have the exact title CTO"

Generate this Clay search query:

```text
select from people
where experiences.any(is_current = true and job_title = "CTO")
```

Note: The user explicitly asks for an exact title match, so use = instead of is_similar_to. is_similar_to would expand to related variants (Chief Technology Officer, VP of Engineering, etc.) which the user does not want here.

Example user request: "people who started their current role in January 2025"

Generate this Clay search query:

```text
select from people
where experiences.any(is_current = true and start_date = "2025-01")
```

Note: One calendar month on a month field uses a single = literal — not >= and <= with the same month.

Example user request: "people who joined their current company in 2024"

Generate this Clay search query:

```text
select from people
where experiences.any(is_current = true and start_date >= "2024-01" and start_date <= "2024-12")
```

Note: Whole calendar year on a month field needs both bounds — >= YYYY-01 and <= YYYY-12.

Example user request: "people who started their current role in the last 30 days"

Generate this Clay search query:

```text
select from people
where experiences.any(is_current = true and start_date >= today() - interval 1 month)
```

Note: Month fields cannot use day/week intervals — round sub-month recency to a month interval.

Example user request: "how many people are in new york"

Generate this Clay search query:

```text
select from people
where location_city in ("New York", "New York City")
```

Note: Use location_city for city-level filtering, include both canonical New York City values, and avoid the free-text location field.

Example user request: "people in manhattan"

Generate this Clay search query:

```text
select from people
where
  location_city = "Manhattan"
  and location_state = "New York"
  and location_country = "United States"
```

Then tell the user these approximations were applied:

```json
[
  {
    "phrase": "manhattan",
    "reason": "Applied approximation: interpreted Manhattan as Manhattan, New York, United States; specify a state or country if you meant another Manhattan."
  }
]
```

Note: A standalone Manhattan defaults to New York but must disclose the geographic ambiguity.

Example user request: "people in the new york metro area"

Generate this Clay search query:

```text
select from people
where
  location_metro_area = "New York-Newark-Jersey City, NY-NJ"
  and location_country = "United States"
```

Note: Metro names are a validated enum — match the full stored value with `=` or `in`, never `contains`. New York metro spans multiple states, so constrain it to the United States without guessing one state.

Example user request: "companies in the US with more than 500 employees"

Generate this Clay search query:

```text
select from companies
where
  locations.any(is_headquarters = true and country_name = "United States")
  and company_size in ("501-1,000", "1,001-5,000", "5,001-10,000", "10,001+")
```

Note: First-pass company-size asks map to company_size buckets covering the requested range. Use numeric estimated_employee_count only when the user explicitly asks for exact employee counts/headcount.

Example user request: "software companies with employee headcount growth over 20% in the last year"

Generate this Clay search query:

```text
select from companies
where
  industry in ("Software Development", "Technology, Information and Internet")
  and employee_growth_12mo >= 1.2
```

Note: An explicit numeric employee-growth cutoff is expressible. employee_growth fields store ratios, so +20% becomes 1.2; choose the field matching the requested 3, 6, 12, or 24 month window. Do not add an unsupported-criterion caveat.

Example user request: "fast-growing fintech companies that grew their headcount in the last 6 months"

Generate this Clay search query:

```text
select from companies
where
  industry in ("Financial Services", "Banking")
  and employee_growth_6mo > 1.0
```

Note: A stated employee-growth period without a numeric cutoff means positive growth in that period. The nearby vague label is captured by that same predicate, so do not add a separate proxy disclosure or invent a stronger threshold.

Example user request: "software companies with over 20% revenue growth in the last year"

Generate this Clay search query:

```text
select from companies
where industry in ("Software Development", "Technology, Information and Internet")
```

Then tell the user these criteria were not captured:

```json
[
  {
    "phrase": "over 20% revenue growth in the last year",
    "reason": "Revenue growth rate is unavailable; use an enrichment to add revenue-growth data rather than substituting employee growth."
  }
]
```

Note: An explicit non-employee growth dimension must not be silently reinterpreted as headcount growth.

Example user request: "people who currently work at Google as engineers"

Generate this Clay search query:

```text
select from people
where
  clay.filter_to_companies(("google.com"))
  and experiences.any(is_current = true and job_title is_similar_to ("engineer"))
```

Note: Google is a household-name company with an unambiguous domain. Current-employer matching uses clay.filter_to_companies at the top level (NOT company.domain inside experiences.any). Role/title filters stay inside experiences.any with is_current = true.

Example user request: "up to 5 people per company that have the title accountant and work at the Big 4"

Generate this Clay search query:

```text
select from people
where
  clay.filter_to_companies(("deloitte.com", "pwc.com", "ey.com", "kpmg.com"))
  and experiences.any(is_current = true and job_title is_similar_to ("accountant")) by clay_company_id
```

Note: Per-company caps use `limit N by clay_company_id` (people only). Combine with clay.filter_to_companies for current employers and experiences.any + is_current for role. Expand Big 4 to the canonical domain list. Put `limit by` before any overall `limit`.

Example user request: "count of saas companies"

Generate this Clay search query:

```text
select from companies
where industry in ("Software Development", "Technology, Information and Internet")
```

Note: A company never self-describes as "SaaS" in its website copy — it is a generic business-model label, so do NOT use description contains "saas". Map it to the structured industry enum (software/tech) instead. Reserve description contains for self-describing product/service keywords (e.g. "card issuing", "fraud detection").

Example user request: "only include stripe.com, openai.com, and notion.so in this company search"

Generate this Clay search query:

```text
select from companies
where clay.include_company_identifiers(("stripe.com", "openai.com", "notion.so"))
```

Note: Exact company matching from an explicit domain list uses one companies-only clay.include_company_identifiers call with one inline list. Never use top-level domain = / domain in for this generated exact-company filter, and never split identifiers across multiple calls.

Example user request: "find the company at https://www.linkedin.com/company/grow-with-clay/"

Generate this Clay search query:

```text
select from companies
where clay.include_company_identifiers(("https://www.linkedin.com/company/grow-with-clay/"))
```

Note: An explicitly provided company LinkedIn URL is an exact identifier, so use the companies-only clay.include_company_identifiers inline-list function. Do not rewrite the URL or match it with the domain field.

Example user request: "find the exact company Google"

Generate this Clay search query:

```text
select from companies
where clay.include_company_identifiers(("google.com"))
```

Note: Google is a household company whose domain is established public knowledge. For a companies-result exact match, resolve it to google.com inside clay.include_company_identifiers; do not emit top-level domain = "google.com".

Example user request: "find me clay, instantly and hubspot"

Generate this Clay search query:

```text
select from companies
where clay.include_company_identifiers(("clay.com", "instantly.ai", "hubspot.com"))
```

Note: A direct request for a list of specifically named companies is exact company inclusion even without words such as "exact", "only", or "include". Resolve high-confidence names to domains inside one clay.include_company_identifiers call; do not use top-level domain matching.

Example user request: "software companies founded in the last 10 years"

Generate this Clay search query:

```text
select from companies
where
  year_founded > 2016
  and industry in ("Software Development", "Technology, Information and Internet")
```

Note: Use the prompt-build year for relative founding-year requests, and map software companies to the structured industry field.

Example user request: "people who have worked at more than 3 companies"

Generate this Clay search query:

```text
select from people
where experiences.count(company_name is_not_null) > 3
```

Example user request: "companies with at least one VP"

Generate this Clay search query:

```text
select from companies
where people.exists(is_current = true and seniority = "VP")
```

Example user request: "Tier 1: Financial Services or Banking companies in the United States with 201-500 employees. Tier 2: same profile in Canada with 51-200 employees. Prioritize tier 1 higher, but include both tiers."

Generate this Clay search query:

```text
select from companies
where
  industry in ("Financial Services", "Banking")
  and (
    (locations.any(is_headquarters = true and country_name = "United States") and company_size = "201-500")
    or (locations.any(is_headquarters = true and country_name = "Canada") and company_size = "51-200")
  )
```

Note: Tier/priority language is ranking metadata, not filter syntax. Keep concrete criteria from all tiers, and preserve tier-paired constraints by OR-ing per-tier clauses instead of flattening paired fields into independent global lists.

Example user request: "people at fintech companies"

Generate this Clay search query:

```text
select from people
where experiences.any(
  is_current = true
  and company.industry in ("Financial Services", "Banking")
)
```

Note: Company attributes can filter people results through scalar company.* fields inside experiences.any(...). Do not switch to companies unless the user asks for companies/accounts. Plain "fintech" maps to exactly industry in ("Financial Services", "Banking").

Example user request: "find product managers at companies in my Target Accounts audience"

Generate this Clay search query:

```text
select from people
where experiences.any(is_current = true and job_title is_similar_to ("Product Manager"))
```

Then tell the user these resource selections are required:

```json
[
  {
    "phrase": "my Target Accounts audience",
    "reason": "Resource selection required: select this audience under Target companies → Select audience in the Clay Search panel to include its companies."
  }
]
```

Note: A name alone cannot supply an audience segment ID. Positive current-company inclusion belongs in Target companies, which serializes clay.filter_to_companies(...); never invert it into the exclusions picker or invent a reference.

Example user request: "find product managers but exclude anyone in my Former Customers audience"

Generate this Clay search query:

```text
select from people
where experiences.any(is_current = true and job_title is_similar_to ("Product Manager"))
```

Then tell the user these resource selections are required:

```json
[
  {
    "phrase": "my Former Customers audience",
    "reason": "Resource selection required: select this audience in the exclusions picker in the Clay Search panel."
  }
]
```

Note: Explicit exclusion intent belongs in the exclusions picker. Preserve the role filter while handing off the unresolved audience name, and never invent a segment ID.

Example user request: "find people who work at companies similar to Goldman Sachs in New York"

Generate this Clay search query:

```text
select from people
where
  location_city in ("New York", "New York City")
  and experiences.any(
    is_current = true
    and company.industry in ("Financial Services", "Banking", "Investment Banking")
  )
```

Then tell the user these approximations were applied:

```json
[
  {
    "phrase": "companies similar to Goldman Sachs",
    "reason": "Applied approximation: direct similarity-by-company/domain is unavailable, so this query uses related company industries instead."
  }
]
```

Note: Similarity-to-company asks use industry proxy filters, not exact-company domain matching. Briefly tell the user that a proxy was applied.

Example user request: "find companies similar to github.com in Germany that make over 1M in revenue"

Generate this Clay search query:

```text
select from companies
where
  locations.any(is_headquarters = true and country_name = "Germany")
  and industry in ("Software Development", "Technology, Information and Internet", "IT Services and IT Consulting")
  and annual_revenue in (
    "1M-5M",
    "5M-10M",
    "10M-25M",
    "25M-75M",
    "75M-200M",
    "200M-500M",
    "500M-1B",
    "1B-10B",
    "10B-100B",
    "100B-1T"
  )
```

Then tell the user these approximations were applied:

```json
[
  {
    "phrase": "companies similar to github.com",
    "reason": "Applied approximation: direct similarity-by-company/domain is unavailable, so this query uses related industries instead."
  }
]
```

Note: Similarity phrasing with an explicit domain token still uses industry proxy (not exact domain matching); numeric revenue wording is expressed via annual_revenue buckets.

Example user request: "companies with VP sales leaders"

Generate this Clay search query:

```text
select from companies
where people.exists(is_current = true and job_title is_similar_to ("VP Sales"))
```

Note: People/role criteria can filter company results through people.exists(...). Do not switch to people unless the user asks for people/leads/candidates.

Example user request: "companies with a VP of sales based in London"

Generate this Clay search query:

```text
select from companies
where people.exists(is_current = true and job_title is_similar_to ("VP Sales") and person.location_city = "London")
```

Note: Person-level conditions inside people.exists use person.* fields: person.location_city is where the person is located, while bare location_city is where the role was located.

Example user request: "companies that employ someone with an MBA from Harvard"

Generate this Clay search query:

```text
select from companies
where people.exists(is_current = true and person.education.any(school_name contains "Harvard" and degree contains "MBA"))
```

Note: Education conditions inside people.exists use the person.education tuple array — the only aggregate allowed inside a people aggregate. Conditions inside one person.education.any(...) match within a single education entry.

Example user request: "companies with at least 20 people in engineering"

Generate this Clay search query:

```text
select from companies
where people.count(is_current = true and job_title is_similar_to ("Engineer")) >= 20
```

Example user request: "fintech and payment companies in Mexico, Colombia, or Brazil with 50-5000 employees, matching website keywords like card issuing and digital wallet"

Generate this Clay search query:

```text
select from companies
where
  locations.any(is_headquarters = true and country_name in ("Mexico", "Colombia", "Brazil"))
  and company_size in ("51-200", "201-500", "501-1,000", "1,001-5,000")
  and industry in ("Financial Services", "Banking")
  and description contains (
    "card issuing",
    "debit card",
    "payment processing",
    "digital wallet",
    "embedded finance"
  )
```

Note: Multiple website/keyword phrases on the same company field → description contains ("a", "b", ...). Never (description contains "a" or description contains "b" or ...). Combine them with structured geography, size, and industry filters via and.

Example user request: "consulting, legal, and accounting firms that have an in-house sales or go-to-market team"

Generate this Clay search query:

```text
select from companies
where
  industry in ("Accounting", "Business Consulting and Services", "Legal Services", "Human Resources Services", "Advertising Services")
  and people.exists(
    is_current = true
    and job_title is_similar_to ("Sales", "GTM", "Go-to-Market", "Business Development", "Account Executive")
  )
```

Note: Inside people.exists / people.count, use bare experience fields (not experiences.any). Put is_current once and collapse interchangeable title keywords with job_title is_similar_to ("a", "b", ...), not repeated people.exists OR chains.

Example user request: "people in california who are not engineers"

Generate this Clay search query:

```text
select from people
where
  location_state = "California"
  and not experiences.any(is_current = true and job_title is_similar_to ("engineer"))
```

Example user request: "large tech companies in san francisco or new york"

Generate this Clay search query:

```text
select from companies
where
  locations.any(is_headquarters = true and city in ("San Francisco", "New York", "New York City"))
  and industry in ("Software Development", "Technology, Information and Internet", "IT Services and IT Consulting", "Technology, Information and Media")
  and company_size in ("1,001-5,000", "5,001-10,000", "10,001+")
```

Note: Generic company city/place filtering defaults to headquarters and uses exact city values in the locations tuple. Multiple place names on the same field stay in one locations.any(...) predicate, not repeated OR chains. "large" maps to the upper company_size buckets.

Example user request: "companies with revenue between 10M and 50M"

Generate this Clay search query:

```text
select from companies
where annual_revenue in ("10M-25M", "25M-75M")
```

Note: annual_revenue uses predefined buckets. The range 10M-50M spans two buckets. Do NOT use numeric operators on annual_revenue.

Example user request: "companies with roles closing in the next 2 weeks"

Generate this Clay search query:

```text
select from companies
where jobs.exists(
  job_still_open = true
  and job_removed_date >= today()
  and job_removed_date <= today() + interval 2 weeks
)
```

Note: Job postings are filters on companies (jobs.exists) or people (company.jobs.any), never a result entity. Use today() plus an interval for future date windows; the interval amount must be a positive integer, and prefer lowercase plural units.

Example user request: "companies hiring for VP roles"

Generate this Clay search query:

```text
select from companies
where jobs.exists(job_still_open = true and job_title is_similar_to ("VP"))
```

Note: Companies "hiring for" a role means currently-open job postings, so include job_still_open = true inside jobs.exists(...).

Example user request: "people who used to work at Amazon"

Generate this Clay search query:

```text
select from people
where experiences.any(is_current = false and company.domain = "amazon.com")
```

Note: Use is_current = false for former employees. Amazon is a household name — use company.domain for precise matching.

Example user request: "people who work at Compass"

Generate this Clay search query:

```text
select from people
where experiences.any(is_current = true and company_name contains "Compass")
```

Note: "Compass" is ambiguous (Compass real estate, Compass Group food services, Compass Minerals, etc.). Without disambiguating context, fall back to company_name contains inside experiences.any(...).

Example user request: "engineers at software companies with under 50 employees in the US"

Generate this Clay search query:

```text
select from people
where
  location_country = "United States"
  and experiences.any(
    is_current = true
    and job_title is_similar_to ("engineer")
    and company.estimated_employee_count <= 50
  )
```

Note: company.estimated_employee_count is a scalar field and CAN be used inside experiences.any(). Company fields appear inside experiences.any() as scalar company.* fields or the company.locations/company.technographics tuple arrays.

Example user request: "commercial and operations directors in Brazil at small freight forwarding companies — logistics, import/export or warehousing, under 200 employees"

Generate this Clay search query:

```text
select from people
where
  location_country = "Brazil"
  and experiences.any(
    is_current = true
    and job_title is_similar_to ("Commercial Director", "Operations Director", "Commercial Manager", "Operations Manager")
    and company.industry in ("Transportation, Logistics, Supply Chain and Storage", "Freight and Package Transportation", "Import and Export", "Warehousing")
    and company.estimated_employee_count <= 200
    and company.description contains ("freight forwarder", "customs broker")
  )
```

Note: Several company attributes plus a title all describe ONE current employer, so they are conjoined inside a single experiences.any(...). Do NOT emit one experiences.any(...) per attribute — separate arms can each match a different job, and each arm is its own scan of the experiences table.

Example user request: "engineers in the US but not in California"

Generate this Clay search query:

```text
select from people
where
  location_country = "United States"
  and location_state != "California"
  and experiences.any(is_current = true and job_title is_similar_to ("engineer"))
```

Example user request: "B2B companies in healthcare"

Generate this Clay search query:

```text
select from companies
where
  (ai_business_types contains "B2B" or ai_business_types is_null)
  and industry in ("Hospitals and Health Care", "Medical Practices", "Medical Devices", "Pharmaceutical Manufacturing")
```

Note: ai_business_types uses contains for matching and is a LOW COVERAGE field — always keep the is_null fallback so sparse data does not silently drop matches. Allowed values: "B2B", "B2C", "Nonprofit". Combine with industry for precise industry matching; a broad "healthcare" vertical maps to the standard healthcare industry set.

Example user request: "engineers at healthcare companies in the US"

Generate this Clay search query:

```text
select from people
where
  location_country = "United States"
  and experiences.any(
    is_current = true
    and job_title is_similar_to ("engineer")
    and company.industry in ("Hospitals and Health Care", "Medical Practices", "Medical Devices", "Pharmaceutical Manufacturing")
  )
```

Note: Use company.industry inside experiences.any() for employer industry. A broad "healthcare" vertical maps to the standard healthcare industry set.

Example user request: "sales reps at companies that use Salesforce"

Generate this Clay search query:

```text
select from people
where experiences.any(
  is_current = true
  and job_title is_similar_to ("sales representative")
  and company.technographics.any(vendor = "Salesforce")
)
```

Note: Employer tech-stack asks use the company.technographics tuple array inside experiences.any(), with vendor, product, and product_category subfields. Bare technographics.any(...) is invalid in people queries.

Example user request: "engineering managers at companies hiring backend engineers"

Generate this Clay search query:

```text
select from people
where experiences.any(
  is_current = true
  and job_title is_similar_to ("engineering manager")
  and company.jobs.any(job_still_open = true and job_title is_similar_to ("backend engineer"))
)
```

Note: Employer hiring asks use company.jobs.any(...) inside experiences.any() — the person's own title stays on job_title of the experience, while the employer's postings are filtered inside company.jobs.any(...). Present-tense hiring keeps job_still_open = true. Bare jobs.any(...) is invalid in people queries.

Example user request: "engineers at companies based in Germany"

Generate this Clay search query:

```text
select from people
where experiences.any(
  is_current = true
  and job_title is_similar_to ("engineer")
  and company.locations.any(is_headquarters = true and country_name = "Germany")
)
```

Note: Employer-location asks use the company.locations tuple array inside experiences.any(). Generic "companies based in" wording defaults to is_headquarters = true inside the same company.locations.any(...); explicit office or physical-presence wording omits it. Person-location asks use top-level location fields instead.

Example user request: "HR professionals at salary.com, jfrog.com, and benchling.com"

Generate this Clay search query:

```text
select from people
where
  clay.filter_to_companies(("salary.com", "jfrog.com", "benchling.com"))
  and experiences.any(is_current = true and job_title is_similar_to ("Human Resources"))
```

Note: An explicit list of current-employer domains routes through clay.filter_to_companies at the top level; the role filter stays inside experiences.any. NEVER use bare "domain in (...)" at the top level of a people query — "domain" does not exist on the people entity. (Contrast with "similar to"/"like <company>" asks, which are industry-proxy matches, not exact-domain lists.)

Example user request: "find people in Mexico, Colombia, or Brazil who currently work at stripe.com, openai.com, notion.so, or ramp.com and are software engineers, data engineers, machine learning engineers, or product managers"

Generate this Clay search query:

```text
select from people
where
  location_country in ("Mexico", "Colombia", "Brazil")
  and clay.filter_to_companies(("stripe.com", "openai.com", "notion.so", "ramp.com"))
  and experiences.any(
    is_current = true
    and job_title is_similar_to ("Software Engineer", "Data Engineer", "Machine Learning Engineer", "Product Manager")
  )
```

Note: When users provide explicit country/domain/job-title lists, preserve all listed values in the Clay search query (no list trimming).

Example user request: "people who went to University of Waterloo"

Generate this Clay search query:

```text
select from people
where education.any(school_name contains "University of Waterloo")
```

Note: Use education.any() with school_name for university filtering. Education has no is_current concept.

Example user request: "people with an MBA who are currently engineers"

Generate this Clay search query:

```text
select from people
where
  education.any(degree contains "MBA")
  and experiences.any(is_current = true and job_title is_similar_to ("engineer"))
```

Note: Education and experience expressions can be combined in the same query.

Example user request: "people who have ever worked as a product manager"

Generate this Clay search query:

```text
select from people
where experiences.any(job_title is_similar_to ("Product Manager"))
```

Note: No recency filter — omit is_current to match any role, current or past.

Example user request: "people who speak Spanish or French"

Generate this Clay search query:

```text
select from people
where languages in ("Spanish", "French")
```

Note: languages is an array field — use = for a single value and in (...) to match any of multiple values.

Example user request: "AI companies"

Generate this Clay search query:

```text
select from companies
where
  industry in ("Software Development", "Technology, Information and Internet", "IT Services and IT Consulting", "Computer and Network Security", "Research Services")
  and products_and_services is_similar_to ("artificial intelligence")
```

Note: Underspecified "AI companies": combine the AI-relevant industries with a semantic products_and_services is_similar_to comparison holding the AI concept the user named (automatically expanded). Do not route AI/ML/LLM terms to description contains unless the user explicitly asks for keyword matching.

Example user request: "im looking for b2b saas companies based in SF"

Generate this Clay search query:

```text
select from companies
where
  products_and_services is_similar_to ("b2b saas")
  and locations.any(is_headquarters = true and city = "San Francisco")
```

Note: The "b2b saas" offering concept maps to the semantic products_and_services field (one concise value, automatically expanded). Generic "based in SF" defaults to the company headquarters location.

Example user request: "sheet metal companies across manhattan, brooklyn, and queens"

Generate this Clay search query:

```text
select from companies
where
  industry in ("Architectural and Structural Metal Manufacturing", "Fabricated Metal Products")
  and description contains "sheet metal"
  and locations.any(is_headquarters = true and city in ("Manhattan", "Brooklyn", "Queens") and state_or_province = "New York" and country_name = "United States")
```

Note: The three boroughs establish New York, United States, so keep the headquarters, exact city, state, and country constraints together inside one locations.any() predicate.

Example user request: "companies in manhattan"

Generate this Clay search query:

```text
select from companies
where locations.any(is_headquarters = true and city = "Manhattan" and state_or_province = "New York" and country_name = "United States")
```

Then tell the user these approximations were applied:

```json
[
  {
    "phrase": "manhattan",
    "reason": "Applied approximation: interpreted Manhattan as Manhattan, New York, United States; specify a state or country if you meant another Manhattan."
  }
]
```

Note: A standalone Manhattan defaults to New York but must disclose the geographic ambiguity.

Example user request: "companies in DFW"

Generate this Clay search query:

```text
select from companies
where locations.any(is_headquarters = true and metro_area = "Dallas-Fort Worth-Arlington, TX" and country_name = "United States")
```

Note: DFW uses the stored metro name and country; generic company location filtering defaults to headquarters.

Example user request: "companies based in New York or nearby states (New York, Pennsylvania, Connecticut, New Jersey) that have at least 10 open job postings including at least one open Chief of Staff role, and work in solar panels or clean batteries — climate tech and similar industries, plus relevant sub-industries"

Generate this Clay search query:

```text
select from companies
where
  locations.any(is_headquarters = true and state_or_province in ("New York", "Pennsylvania", "Connecticut", "New Jersey"))
  and jobs.count(job_still_open = true) >= 10
  and jobs.exists(job_still_open = true and job_title is_similar_to ("Chief of Staff"))
  and industry in ("Renewables & Environment", "Renewable Energy Equipment Manufacturing", "Renewable Energy Power Generation", "Solar Electric Power Generation", "Services for Renewable Energy")
  and ai_subindustries in ("Renewable Energy and Clean Tech", "Sustainability Tech and Environmental Consulting")
  and products_and_services is_similar_to ("solar panels", "clean batteries")
```

Note: A dense multi-criteria company search. "New York or nearby states" → the company headquarters locations tuple by state with the named neighbors. Hiring criteria filter companies via the jobs relationship: "at least 10 open postings" → jobs.count(job_still_open = true) >= 10, and "at least one open Chief of Staff posting" → jobs.exists(job_still_open = true and job_title is_similar_to ("Chief of Staff")) (is_similar_to works inside jobs.exists on a companies query). "climate tech and similar industries" only approximates the ask, so pair the closest industry enum values and relevant ai_subindustries with a products_and_services refinement for the concrete offerings. The two alternative offerings are separate values in one semantic comparison, so either may match. Keep each value short and dense; no generic filler.

Example user request: "companies that build digital banking apps"

Generate this Clay search query:

```text
select from companies
where
  industry in ("Financial Services", "Banking")
  and products_and_services is_similar_to ("digital banking apps")
```

Note: industry only APPROXIMATES "digital banking apps" (no clean enum), so pair the closest industry values with a products_and_services is_similar_to refinement — structured + semantic in one query. Keep the semantic value a dense, concrete offering ("digital banking apps"); no generic filler words.

Example user request: "companies with mobile banking"

Generate this Clay search query:

```text
select from companies
where
  description contains "mobile banking"
  or products_and_services is_similar_to ("mobile banking")
```

Note: When a product/service concept is paired with the description field, OR them (never AND): the concept may appear in the company description text OR be caught by the semantic products_and_services match, so OR maximizes recall.

Example user request: "find companies that make drones or thermal cameras"

Generate this Clay search query:

```text
select from companies
where
  products_and_services is_similar_to ("drones", "thermal cameras")
```

Note: This is OR semantics, so bunch "drones" and "thermal cameras" in the same is_similar_to comparison. Do not fold them into one string like "drones and thermal cameras".

Example user request: "looking for people at companies that produce consumer goods like sleep tracking wearables or fitness equipment"

Generate this Clay search query:

```text
select from people
where
  experiences.any(is_current = true and company.products_and_services is_similar_to ("sleep tracking wearables", "fitness equipment"))
```

Note: This is OR semantics, so bunch both offerings in the same company.products_and_services is_similar_to comparison. On a people query that comparison must live inside experiences.any(is_current = true and ...).

Example user request: "sales reps at companies that sell cybersecurity software"

Generate this Clay search query:

```text
select from people
where experiences.any(is_current = true and job_title is_similar_to ("sales representative") and company.products_and_services is_similar_to ("cybersecurity software"))
```

Note: People-result query with a SINGLE offering concept: the role (sales rep) and the employer offering describe the same current employer, so both go inside one experiences.any(...). company.products_and_services must be inside experiences.any on a people query. One concept → one filter (no split); no generic "software"/"solution" filler beyond the concrete concept.

Example user request: "product managers at large companies that build electric vehicles"

Generate this Clay search query:

```text
select from people
where experiences.any(is_current = true and job_title is_similar_to ("Product Manager") and company.estimated_employee_count >= 1000 and company.products_and_services is_similar_to ("electric vehicles"))
```

Note: People-result query combining a role with structured and semantic company filters, all describing the same current employer, so all sit inside one experiences.any(...). "large companies" → company.estimated_employee_count >= 1000; the employer offering → company.products_and_services is_similar_to ("electric vehicles"). On a people query every company.* filter (scalar or semantic) MUST live inside experiences.any — never at the top level.

Example user request: "senior VPs at companies that do go-to-market for email sequencing and campaigns, with more than 10 years of experience but who joined their current company in the last 6 months, ideally from an Ivy League school — or CEOs at those same companies who went to Harvard"

Generate this Clay search query:

```text
select from people
where
  experiences.any(
    is_current = true
    and company.products_and_services is_similar_to ("email marketing", "sales sequencing", "email campaigns")
  )
  and (
    (
      experiences.any(is_current = true and seniority = "VP" and start_date >= today() - interval 6 months)
      and years_of_experience > 10
      and education.any(school_name in ("Harvard University", "Yale University", "Princeton University", "Columbia University", "University of Pennsylvania", "Brown University", "Dartmouth College", "Cornell University"))
    )
    or (
      experiences.any(is_current = true and job_title is_similar_to ("CEO"))
      and education.any(school_name contains "Harvard")
    )
  )
```

Note: Email marketing, sales sequencing, and email campaigns use OR semantics, so bunch them in the same company.products_and_services is_similar_to comparison. The shared employer filter applies to both person-profile branches. On a people query, company.* filters must live inside experiences.any(...).

Example user request: "Fortune 500 companies in tech"

Generate this Clay search query:

```text
select from companies
where industry in ("Software Development", "Technology, Information and Internet", "IT Services and IT Consulting", "Technology, Information and Media")
```

Then tell the user these criteria were not captured:

```json
[
  {
    "phrase": "Fortune 500",
    "reason": "Fortune 500 classification is not available in this dataset; consider filtering by estimated_employee_count, annual_revenue, or industry instead."
  }
]
```

Note: For partially expressible requests, include what can be represented in the query and briefly tell the user what could not be represented.

Example user request: "companies using Salesforce"

Generate this Clay search query:

```text
select from companies
where technographics.any(vendor = "Salesforce")
```

Example user request: "companies using Amazon Web Services"

Generate this Clay search query:

```text
select from companies
where technographics.any(product = "Amazon Web Services (AWS)")
```

Note: Amazon is the vendor, but Amazon Web Services is a documented product value. Preserve the named technology at product precision instead of broadening it to every Amazon technology.

Example user request: "software engineers not based in the US or Canada"

Generate this Clay search query:

```text
select from people
where
  experiences.any(is_current = true and job_title is_similar_to ("Software Engineer"))
  and location_country not_in ("United States", "Canada")
```

Note: Exclude a set of enum values with not_in (...). location_country is a closed enum, so not_in negates the whole list. Profile-level location stays at the top level, outside experiences.any().

Example user request: "fintech companies that are not currently hiring engineers"

Generate this Clay search query:

```text
select from companies
where
  industry in ("Financial Services", "Banking")
  and not jobs.exists(job_still_open = true and job_title is_similar_to ("engineer"))
```

Note: To exclude companies that match an aggregate, wrap the whole aggregate in not. "not currently hiring" keeps job_still_open = true inside the negated jobs.exists(...). Job-posting titles inside jobs.exists still default to is_similar_to (companies-result queries can resolve it).

Example user request: "senior engineers at startups or product managers at large companies"

Generate this Clay search query:

```text
select from people
where experiences.any(
  is_current = true
  and (
    (job_title is_similar_to ("engineer") and seniority = "Senior" and company.estimated_employee_count <= 50)
    or (job_title is_similar_to ("Product Manager") and company.estimated_employee_count >= 1000)
  )
)
```

Note: Two distinct personas become an OR of two AND groups inside a single experiences.any(). Parenthesize each group because "and" binds tighter than "or". is_current = true is shared across both branches. Titles default to is_similar_to.

Example user request: "companies that are Canada-based in software or fintech, or Australia-based in healthcare or medical devices, all with 51-200 employees"

Generate this Clay search query:

```text
select from companies
where
  (
    (locations.any(is_headquarters = true and country_name = "Canada") and industry in ("Software Development", "Financial Services"))
    or (locations.any(is_headquarters = true and country_name = "Australia") and industry in ("Hospitals and Health Care", "Medical Devices"))
  )
  and company_size = "51-200"
```

Note: Nested boolean: two location+industry branches OR'd together, with the shared size constraint kept OUTSIDE the OR (joined by and) so neither branch can bypass it. Keep the OR group parenthesized.

Example user request: "product managers in the Bay Area with no more than 8 years of experience"

Generate this Clay search query:

```text
select from people
where
  location_metro_area = "San Francisco Bay Area"
  and location_country = "United States"
  and years_of_experience <= 8
  and experiences.any(is_current = true and job_title is_similar_to ("Product Manager"))
```

Note: Bay Area uses the stored metro name with United States, without adding a city or state restriction. years_of_experience is a top-level people field for TOTAL career experience — filter it at the top level with numeric comparisons, never inside experiences.any(). Per-role tenure uses start_date; overall experience uses years_of_experience.

Example user request: "account executives in Austin who were previously sales development representatives"

Generate this Clay search query:

```text
select from people
where
  location_city = "Austin"
  and experiences.any(is_current = true and job_title is_similar_to ("Account Executive"))
  and experiences.any(is_current = false and job_title is_similar_to ("SDR", "Sales Development Representative"))
```

Note: Current-role and former-role criteria go in SEPARATE experiences.any() blocks — one with is_current = true for the present role, one with is_current = false for the past role. Do not fold both tenures into a single experiences.any(). Titles default to is_similar_to (it takes a parenthesized list).

Existing query being refined:

```text
select from people
where
  location_city = "San Francisco"
  and experiences.any(is_current = true and job_title contains "engineer")
```

Example follow-up request: "narrow to employers with at least 1,000 employees"

Generate this Clay search query:

```text
select from people
where
  location_city = "San Francisco"
  and experiences.any(is_current = true and job_title contains "engineer" and company.estimated_employee_count >= 1000)
```

Note: Additive refinement. Company size is a scalar company.* attribute of the employer, so it is appended INSIDE the existing experiences.any() rather than as a new top-level predicate. Preserve the existing predicates and their order.

Existing query being refined:

```text
select from people
where
  location_city = "Denver"
  and experiences.any(is_current = true and job_title contains "Revenue Operations Manager")
```

Example follow-up request: "also match similar and alternative titles for this role"

Generate this Clay search query:

```text
select from people
where
  location_city = "Denver"
  and experiences.any(is_current = true and job_title is_similar_to ("Revenue Operations Manager"))
```

Note: The refinement explicitly asks for similar/alternative titles, so the existing job_title contains predicate is upgraded to job_title is_similar_to (...). is_similar_to always takes a parenthesized list. (is_similar_to is already the default for job_title in fresh queries; this refinement matters when the current query used contains.)

Existing query being refined:

```text
select from people
where
  location_city in ("New York", "New York City", "San Francisco")
  and experiences.any(is_current = true and job_title contains "Sales Manager")
```

Example follow-up request: "they also need at least 3 years in their current role"

Generate this Clay search query:

```text
select from people
where
  location_city in ("New York", "New York City", "San Francisco")
  and experiences.any(is_current = true and job_title contains "Sales Manager" and start_date <= today() - interval 3 years)
```

Note: Tenure-in-current-role refinement appends start_date <= today() - interval 3 years INSIDE the existing current-role experiences.any(). Tenure is a property of that role, so it belongs in the same block, not at the top level.

Existing query being refined:

```text
select from companies
where
  locations.any(is_headquarters = true and country_name = "France")
  and industry in ("Software Development", "Technology, Information and Internet", "IT Services and IT Consulting")
```

Example follow-up request: "add Spain and Italy too"

Generate this Clay search query:

```text
select from companies
where
  locations.any(is_headquarters = true and country_name in ("France", "Spain", "Italy"))
  and industry in ("Software Development", "Technology, Information and Internet", "IT Services and IT Consulting")
```

Note: Same-field broadening MERGES into the existing predicate (country_name = "France" becomes country_name in ("France", "Spain", "Italy")) — NOT a new top-level or. The shared industry filter is left untouched.

Existing query being refined:

```text
select from companies
where locations.any(is_headquarters = true and country_name = "United States") and industry in ("Software Development", "Technology, Information and Internet", "IT Services and IT Consulting")
```

Example follow-up request: "or any company hiring a VP of Sales right now"

Generate this Clay search query:

```text
select from companies
where
  (locations.any(is_headquarters = true and country_name = "United States") and industry in ("Software Development", "Technology, Information and Internet", "IT Services and IT Consulting"))
  or jobs.exists(job_still_open = true and job_title is_similar_to ("VP Sales"))
```

Note: A genuinely alternative branch uses a top-level or. Wrap the pre-existing branch in parentheses so its AND constraints stay grouped and the new jobs.exists(...) branch is a true alternative. The newly added job-posting title uses is_similar_to (the default for job_title in companies-result queries).

Existing query being refined:

```text
select from people
where
  location_city = "San Francisco"
  and experiences.any(is_current = true and job_title contains "Account Executive")
```

Example follow-up request: "exclude any enterprise or major account executives"

Generate this Clay search query:

```text
select from people
where
  location_city = "San Francisco"
  and experiences.any(is_current = true and job_title contains "Account Executive" and not job_title contains ("Enterprise Account Executive", "Major Account Executive"))
```

Note: Exclusion refinement on a role narrows the SAME current-role experiences.any() with "and not job_title contains (...)". Negating a contains-list excludes every listed title variant while keeping the base "Account Executive" match.

Existing query being refined:

```text
select from people
where
  location_city = "Boston"
  and experiences.any(is_current = true and job_title contains "Product Manager" and seniority = "Senior")
```

Example follow-up request: "actually switch the location to Denver"

Generate this Clay search query:

```text
select from people
where
  location_city = "Denver"
  and experiences.any(is_current = true and job_title contains "Product Manager" and seniority = "Senior")
```

Note: Replacement intent edits the existing location predicate in place (Boston → Denver) instead of appending a new one. Every other predicate is preserved exactly.

Existing query being refined:

```text
select from people
where experiences.any(is_current = false and company.domain = "stripe.com") and location_country = "United States"
```

Example follow-up request: "actually I care about people still there today, not ex-employees"

Generate this Clay search query:

```text
select from people
where clay.filter_to_companies(("stripe.com")) and location_country = "United States"
```

Note: Switching from a former-employer query to a current-employer query REWRITES the company-matching approach: the experiences.any(is_current = false and company.domain = ...) block is replaced by clay.filter_to_companies((...)) at the top level. Never simply flip is_current to true inside company.domain — current-employer matching always uses clay.filter_to_companies, even when rewriting a prior former-employer query. Unrelated predicates (here the location filter) are preserved.

Example user request: "asdfghjkl"

Do not generate a Clay search query. Explain to the user:

```text
Input doesn't appear to be a meaningful query.
```

Example user request: "what's the weather today"

Do not generate a Clay search query. Explain to the user:

```text
This doesn't relate to searching for people or companies.
```

Example user request: "translate this to french"

Do not generate a Clay search query. Explain to the user:

```text
This doesn't relate to searching for people or companies.
```

Example user request: "people with Salesforce admin experience who have a CPA"

Generate this Clay search query:

```text
select from people
where
  (headline contains "CPA" or about contains "CPA")
  and (
    headline contains "Salesforce"
    or about contains "Salesforce"
    or experiences.any(description contains "Salesforce")
  )
```

Note: Credentials (CPA) and tool experience (Salesforce) are keyword traits — match via headline/about, with experience description as a fallback for tool usage.

Example user request: "5+ years of SME experience, including 3+ years of full-cycle sales experience; payments, B2B, or fintech experience; experience selling technical products and multiple products; top performers with a proven track record of attainment"

Generate this Clay search query:

```text
select from people
where
  years_of_experience >= 5
  and experiences.any(job_title is_similar_to ("Account Executive", "Sales Representative", "Business Development"))
  and (
    headline contains ("full-cycle sales", "payments", "B2B", "fintech", "technical products", "multi-product sales")
    or about contains ("full-cycle sales", "payments", "B2B", "fintech", "technical products", "multi-product sales")
    or experiences.any(description contains ("full-cycle sales", "payments", "B2B", "fintech", "technical products", "multi-product sales"))
  )
```

Note: Full-cycle sales implies sales-role experience, so retain that intent with a job-title predicate. Preserve the named payments/B2B/fintech and technical-sales context as profile and experience keywords. "Top performers with a proven track record of attainment" is qualitative ranking metadata without a measurable threshold, so omit it from the query without an unsupported-criterion caveat.

Example user request: "NYC-based accountants who have been in their current role for more than 12 months at tech companies with 200–800 employees, with CPA credentials and Big 4 experience. Add columns for years in current role, current company, and work email."

Generate this Clay search query:

```text
select from people
where
  location_city in ("New York", "New York City", "Manhattan", "Brooklyn", "Queens", "Bronx", "Staten Island")
  and location_state = "New York"
  and location_country = "United States"
  and (headline contains "CPA" or about contains "CPA")
  and experiences.any(
    is_current = true
    and job_title is_similar_to ("accountant")
    and company.estimated_employee_count >= 200
    and company.estimated_employee_count <= 800
    and company.industry in ("Software Development", "Technology, Information and Internet", "IT Services and IT Consulting", "Technology, Information and Media")
    and start_date <= today() - interval 12 months
  )
  and experiences.any(company.domain in ("deloitte.com", "pwc.com", "ey.com", "kpmg.com"))
```

Then tell the user these criteria were not captured:

```json
[
  {
    "phrase": "Add columns for years in current role, current company, and work email",
    "reason": "The Clay search query is a filter language and does not add enrichment columns — column configuration is handled outside the Clay search query."
  }
]
```

Note: "NYC-based" → location_city in ("New York", "New York City", "Manhattan", "Brooklyn", "Queens", "Bronx", "Staten Island"), location_state = "New York", and location_country = "United States". "CPA" still ORs headline and about (different fields). "Tech companies" is expressed via the scalar `company.industry` inside experiences.any(). Big 4 uses a domain list. The same `experiences.any` for the current accountant role includes "more than 12 months in current role" via `start_date <= today() - interval 12 months`.


## Workspace resource references

Queries can reference workspace audience segments; each reference resolves when the search previews or runs. If earlier examples say to route an audience name to a picker ("Resource selection required"), that applies only when pasting into Clay Search. On this surface, resolve the id with the CLI and author the reference directly.

- `@audience_segment("SEGMENT_ID")` — an Audiences segment. Get segment ids with `clay audiences list`. Contact segments match people queries; Account segments match companies queries. The list is paginated: pass each response's `cursor` back with `--cursor` until it is absent before matching by name or checking for duplicates. Never invent a segment id.
- `@audience_segment("ALL")` — the whole Audiences dataset for the query's result entity (every Contact for people queries, every Account for companies queries). Use it whenever the user wants to exclude what the workspace already has — "exclude my existing contacts/accounts", "net-new only", "not already in Clay", "exclude all my audiences" — without listing or creating a segment: `clay.exclude_people_identifiers(@audience_segment("ALL"))` / `clay.exclude_company_identifiers(@audience_segment("ALL"))`. Resolve a specific segment id only when the user names a particular audience or segment. `ALL` is a sentinel, not a name match: a segment whose name contains "ALL" still needs its real id.
- `@table("tableId:viewId:fieldId")` — a Clay table column of identifiers. Preserve existing references exactly; do not construct new ones (the ids come from the in-app picker).
- `@bitmap("BITMAP_ID")` — preserve if present; never construct.


Where references are accepted (one source per call):

- Exclusions — up to 3 calls of the matching function per query, top-level and combined with `and`:
  - People queries: `clay.exclude_people_identifiers(@audience_segment("SEGMENT_ID"))`
  - Companies queries: `clay.exclude_company_identifiers(@audience_segment("SEGMENT_ID"))`
- Current-employer inclusion in people queries — `clay.filter_to_companies(...)` (1 call per query) also accepts an Account segment: `clay.filter_to_companies(@audience_segment("SEGMENT_ID"))`.

Example — product managers who are not already in the workspace's Audiences:

```
select from people
where experiences.any(is_current = true and job_title is_similar_to ("Product Manager"))
  and clay.exclude_people_identifiers(@audience_segment("ALL"))
```

Example — product managers, excluding everyone already in one named audience:

```
select from people
where experiences.any(is_current = true and job_title is_similar_to ("Product Manager"))
  and clay.exclude_people_identifiers(@audience_segment("SEGMENT_ID"))
```

The entity kind of the segment must match the function: a Contact segment cannot be used in `clay.exclude_company_identifiers` or `clay.filter_to_companies`, and an Account segment cannot be used in `clay.exclude_people_identifiers`. When the user names an audience, resolve it to a segment id via `clay audiences list` and confirm with the user if multiple segments match the name.
