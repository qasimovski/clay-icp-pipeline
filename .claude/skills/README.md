# Event ICP skills — quick start

Two Claude Code skills that run the Highways process on any event:

- **event-icp-clay-workflow**: scraped CSVs → reuse any earlier run's typing under the newest ICP version → classify or re-check buy/sell → find people → apply the sheet's exact title and country rules → speakers → per-event review tables in Clay → **marketing manager approves** → emails (Supabase first, then Blitz → LeadMagic → Prospeo + ZeroBounce) → Salesforce CSV.
- **supabase-known-lookup**: checks the team's Supabase for companies and emails we already have, so you don't pay twice.

## What you need

**Software**
- **Claude Code**, opened inside this repo. The skills load from `.claude/skills/`.
- **Clay CLI**, signed in (`clay login`, then `clay whoami`).
  - On Windows it only runs inside **WSL**, so install WSL with Python 3 in it.
  - The Clay plugin for Claude Code is recommended: the skill uses its `workflows-cli` guide.
- **Python 3** with `pip install pyyaml openpyxl requests "psycopg[binary]" playwright`.
- **Chrome**, with a profile that is signed in to Clay. It's only used for the Clay table import.

**Access**
- A **`.env` file** with `SUPABASE_URL` and `SUPABASE_PUBLISHABLE_KEY`, and the **team key file `clay-team.key`**. Ask Qasim, and share them privately: never paste them into chat or commit them. (The database password, `SUPABASE_DB_URL`, is not needed.)
- **For speakers only:** a `TINYFISH_API_KEY` (environment or `.env`), for the free LinkedIn search.
- **In Clay, four accounts** for the email workflow: *Supabase GTM API* and *Blitz* (HTTP API headers accounts), plus your connected *LeadMagic* and *Prospeo* accounts. `references/email_workflow.md` says what goes in each.
- In Clay: an **empty folder you create by hand** for the results. You'll be asked for its URL.

**Your inputs**
- **Cleaned, fit-filtered CSVs**, one per event and type (e.g. `exhibitors_normalized.csv`, `sponsors_normalized.csv`).
- **The event's ICP workbook**: Products_Services, Seller JTs, BuySide ICP, Geo_Scope, Competitor. If it has versions (V2 tabs), say which is current: **the newest version always wins**.
- **Any earlier run on these events** (another ICP version, or another ICP): where its types and decisions are. They are reused, not paid for again.
- **The classification prompt**, if you have one. Otherwise Claude drafts it and you approve it.
- **A previous Salesforce upload CSV.** Its header is the target format.
- **Optional:** the Salesforce Competitive Event Id for each event.

## The ICP lives in config, not in code

Each ICP has a folder `config/icps/<icp>/` with three files. Everything that changes between events is in them:

| File | What's in it |
|---|---|
| `icp.yaml` | categories (products & services, industries), job titles, exclusions, seniority, countries |
| `post_filter.yaml` | the sheet's exact rules: personas, exact titles, per-keyword exclusions, country tiers, LinkedIn-only countries |
| `scope.yaml` | the competitor events (in or cut, tier, per-event rules), and how an older version's types map onto the new labels |

`config/icps/mining/` is the full worked example. Claude builds these files from your workbook, asks about anything unclear, and reads them back to you before anything runs.

## How to use it

1. **Open Claude Code in the repo** and say, for example:
   *"Run the event ICP Clay workflow for Aviation Week. The CSVs are in C:\…\scrapers_aviation, and the ICP is C:\…\ICP - Aviation.xlsx."*
2. **Answer its first questions** in one go: workbook version, earlier runs, which events are in scope, geography, speakers, prompt, Salesforce file, `.env` location, credit limit.
3. **Check what it proposes.** It shows which column holds the company, website, job titles, countries and industries, with evidence. Correct anything that's wrong.
   - **It asks whenever it's unsure.** That's a hard rule. Answering takes seconds; a wrong guess costs a paid re-run.
   - **It writes your answers into the config** with the date, so the next run knows they were decided.
4. **Approve each paid step.** It tells you the count and the cost first, and runs one pilot chunk before the rest.
5. **Get your outputs:**
   - `<Series> - Salesforce.csv`
   - `people.csv`, plus the excluded and duplicates files
   - `people - approved.csv` after the marketing manager's review, then `people - emails.csv`
   - per-event tables in your Clay folder
   - paste-ready rows for the ICP sheet's Clay Tables tab

**Supabase check on its own:** *"Check which people in <file>.csv we already have emails for in Supabase."*

## Good to know

- **Nothing is spent without your yes**, and nothing in Clay is ever deleted. New results go into a new folder.
- **Busy companies are searched more than once.** Clay returns at most 10 people per search, so a company that fills it is searched again per seniority band: 2 bands, or 3 if you want more depth.
- **Some people are kept with a flag** in `Title Check`: a near-exact title like "Group CEO", or a non-English title. The marketing manager decides on those.
- **On a low-memory PC, imports pause and resume themselves.** Re-running a step is safe: finished work is skipped.
- **No email is looked up before the marketing manager approves the people list.** Their removals are applied, recorded with their name, and the email workflow refuses to run on anything else.
- **Emails Supabase already has are never paid for again** — not by the waterfall, not by ZeroBounce. Every new email (and every "nothing found") is written back, so nobody on the team pays for that person twice.
