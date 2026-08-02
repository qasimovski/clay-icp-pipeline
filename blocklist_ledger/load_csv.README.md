# load_csv.js — bulk CSV loader for the ledger

Loads a CSV straight into `company_ledger` / `people_ledger` using Postgres
`COPY`, so Postgres parses the CSV itself — quoted commas, embedded newlines and
escaped quotes are all handled correctly without a JS CSV parser.

Use this instead of the Supabase dashboard importer for anything large, and
instead of `psql \copy` if psql isn't installed.

## Setup (once)

    npm install pg pg-copy-streams

## Usage

    # always dry-run first: does everything, then rolls back
    PGHOST=db.<ref>.supabase.co PGPASSWORD='<db-password>' \
      node load_csv.js --file companies.csv --target company --dry-run

    # then for real
    PGHOST=db.<ref>.supabase.co PGPASSWORD='<db-password>' \
      node load_csv.js --file companies.csv --target company

`--target company` expects headers `Name` and `Company Domain`.
`--target people`  expects headers `Full Name` and `Linkedin Profile`.

Header names must match EXACTLY, including spaces and capitals. Extra columns are
fine and are ignored (it reports which ones).

## Guard rails (all tested)

| Condition | Behaviour |
|---|---|
| A required header is missing | **Aborts** before loading, lists what it found |
| Extra columns present | Loads, prints which were ignored |
| Values starting with `/` (unresolved Clay refs) | **Aborts** — this is the failure that produced junk rows during setup |
| Both key fields blank on a row | Skipped, counted in the report |
| Duplicate pairs within the CSV | Collapsed via `SELECT DISTINCT` |
| Pair already in the ledger | Left alone; original `first_seen` preserved |

Everything runs in one transaction, so a mid-load failure leaves the ledger
untouched. `--dry-run` rolls back deliberately.

## Output

    staged     : 400000 rows in 12.4s
    distinct   : 398211 pairs
    unusable   : 3 rows (both fields blank -> skipped)
    bad refs   : 0 rows starting with '/'
    ledger     : 0 -> 398208  (+398208 new, 1792 already present or duplicate)
    size       : company_ledger 96 MB,  database total 106 MB of 500 MB
