# Event ICP skills — quick start

Two Claude Code skills that run the Highways process on any event:

- **event-icp-clay-workflow**: scraped CSVs → classify buy/sell in Clay → find people → **marketing manager approves** → emails (Supabase first, then Blitz → LeadMagic → Prospeo + ZeroBounce) → Salesforce CSV → Clay tables.
- **supabase-known-lookup**: checks the team's Supabase for companies and emails we already have, so you don't pay twice.

## What you need

**Software**
- **Claude Code**, opened inside this repo. The skills load from `.claude/skills/`.
- **Clay CLI**, signed in (`clay login`, then `clay whoami`).
  - On Windows it only runs inside **WSL**, so install WSL with Python 3 in it.
  - The Clay plugin for Claude Code is recommended: the skill uses its `workflows-cli` guide.
- **Python 3** with `pip install pyyaml openpyxl "psycopg[binary]" playwright`.
- **Chrome**, with a profile that is signed in to Clay. It's only used for the Clay table import.

**Access**
- A **`.env` file** with `SUPABASE_URL` and `SUPABASE_PUBLISHABLE_KEY`, and the **team key file `clay-team.key`**. Ask Qasim, and share them privately: never paste them into chat or commit them. (The database password, `SUPABASE_DB_URL`, is not needed.)
- **In Clay, four accounts** for the email workflow: *Supabase GTM API* and *Blitz* (HTTP API headers accounts), plus your connected *LeadMagic* and *Prospeo* accounts. `references/email_workflow.md` says what goes in each.
- In Clay: an **empty folder you create by hand** for the results. You'll be asked for its URL.

**Your inputs**
- **Cleaned, fit-filtered CSVs**, one per event and type (e.g. `exhibitors_normalized.csv`, `sponsors_normalized.csv`).
- **The event's ICP workbook**: Products_Services, Seller JTs, BuySide ICP, Competitor.
- **The classification prompt**, if you have one. Otherwise Claude drafts it and you approve it.
- **A previous Salesforce upload CSV.** Its header is the target format.
- **Optional:** the Salesforce Competitive Event Id for each event.

## How to use it

1. **Open Claude Code in the repo** and say, for example:
   *"Run the event ICP Clay workflow for Aviation Week. The CSVs are in C:\…\scrapers_aviation, and the ICP is C:\…\ICP - Aviation.xlsx."*
2. **Answer its first questions** in one go: prompt, entities, Salesforce file, `.env` location, credit limit.
3. **Check what it proposes.** It shows which column holds the company, website, job titles, countries and industries, with evidence. Correct anything that's wrong; it asks whenever it's unsure.
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
- **You'll get up to 20 people per company.** Clay returns at most 10 per search, so busy companies are searched twice, once per seniority band.
- **On a low-memory PC, imports pause and resume themselves.** Re-running a step is safe: finished work is skipped.
- **No email is looked up before the marketing manager approves the people list.** Their removals are applied, recorded with their name, and the email workflow refuses to run on anything else.
- **Emails Supabase already has are never paid for again** — not by the waterfall, not by ZeroBounce. Every new email (and every "nothing found") is written back, so nobody on the team pays for that person twice.
