# Workflow 3 — emails (runs only on the marketing-approved list)

```
Trigger (one approved person: LinkedIn URL, First/Last/Person Name, Company Name, Domain, Job Title, Mailing Country)
  → Build Person (code)                     JSON bodies for the HTTP calls
  → Supabase Lookup (HTTP API → api.lookup_contacts, team key)
  → Read Supabase (code) → Route?
       stored email (valid, risky OR bad) → Out - From Supabase      0 credits: email + validation columns copied
       searched before, no email          → Out - Searched Before    a paid search already found nothing
       lookup failed                      → Out - Lookup Failed      nothing spent; re-run these rows later
       no LinkedIn URL                    → Out - No LinkedIn        not searched (Find People always returns one)
       not in Supabase                    → Blitz → Found? ─yes→ ZeroBounce → Record Body → Record (api.record_contacts) → Out
                                                       └no→ LeadMagic → Found? ─yes→ (same tail)
                                                                      └no→ Prospeo → Found? ─yes→ (same tail)
                                                                                    └no→ Record (miss) → Out - No Email Found
```

`scripts/email_workflow.py` generates all 32 nodes from one definition (no branch merges back, so each
provider gets its own tail; a small Read node after each provider pulls the email out of Clay's
`{result, success, textPreview}` wrapper), checks the graph offline, and builds it through the CLI.

**Proven live on 2026-10-23** (`Emails - Supabase first (v1)`): stored email → reused, 0 credits; searched before →
stopped; three genuine misses → all three providers tried, miss recorded; a Blitz hit → ZeroBounce `valid` → email
recorded (provider, status, MX/SMTP provider, date, ledger hit under every identity key) → the same person run again
was reused from Supabase in 0 s with no provider or ZeroBounce call.

## Why it is shaped like this

- **Supabase is asked first, for every person, inside the workflow** — at run time, so an email a colleague
  found an hour ago is reused too. Anything Supabase holds is reused as stored, including risky and bad
  emails: their ZeroBounce columns came with them, so neither the waterfall nor ZeroBounce runs again.
- **A failed lookup never means "new".** If the reply is not the expected list (Supabase down, wrong key,
  schema not exposed), the person goes to `Out - Lookup Failed` and nothing is paid.
- **ZeroBounce only sees waterfall emails** (Blitz, LeadMagic or Prospeo output) — a test proves no path
  feeds it a Supabase email.
- **Every paid answer is written back**, a hit with its ZeroBounce answer and a miss as "searched, nothing
  found" — under every identity form of the person, so the next workflow anywhere in the team skips them.
  `Out - …` reports `recorded`; `collect.py emails` counts `found but NOT recorded` — re-run those rows.

## Accounts (once per workspace, in the Clay UI — keys never go through chat or files in git)

| Clay account | Type | Holds |
|---|---|---|
| **Supabase GTM API** | HTTP API (Headers) | `apikey`: the Supabase publishable key · `x-gtm-key`: the team key (`clay-team.key`) · `Content-Profile`: `api` |
| **Blitz** | HTTP API (Headers) | `x-api-key`: the Blitz key |
| **LeadMagic** | connected account | the team's LeadMagic key (billed to LeadMagic, not Clay credits) |
| **Prospeo** | connected account | the team's Prospeo key |
| ZeroBounce | connected account | the team's own ZeroBounce key |

LeadMagic, Prospeo and ZeroBounce are Clay's **native** actions on your connected accounts — never wrap them in HTTP.
Put the Supabase project URL and each account's **name** in `email_workflow.yaml` next to the run (shape:
`assets/email_workflow.example.yaml`; keep it out of git). The CLI never shows account ids, only names, so accounts
are picked in the Clay UI and verified by name.

## Build (once per series)

1. `clay workflows create --name "<Series> - Emails"` → an empty workflow id.
2. `python scripts/email_workflow.py check --config email_workflow.yaml` → `graph ok`.
3. `python scripts/email_workflow.py build --config email_workflow.yaml --workflow <wf> --state email_build_state.json`
   — creates the manual trigger, the 32 nodes (resumable: ids are saved as they are made), wires every edge,
   validates the graph. After a skill update, `--sync` pushes code/input/rule changes into the existing nodes
   without touching their tools or accounts.
4. **Accounts, in the Clay UI.** Clay pre-selects them: the native LeadMagic / Prospeo / ZeroBounce nodes get your
   connected account (check it is the team's), but **every HTTP node gets the most recently created HTTP account**.
   Open the workflow and set **Supabase GTM API** on *Supabase Lookup* and the four *Record* nodes, **Blitz** on
   *Blitz - Find Work Email*. Then `python scripts/email_workflow.py check-accounts --config email_workflow.yaml
   --state email_build_state.json` must print `all accounts as expected`.
5. **Draft test on 2–3 approved rows**, chosen so the paths differ: one person Supabase knows, one it doesn't.
   People imported without an email have usually failed an earlier waterfall already — for the hit path, use
   someone you know is findable.
   `clay workflows runs test <wf> --inputs row.json`, then `runs get <wf> <run> --verbose`, and read:
   - **Supabase Lookup** returned a JSON list (not an auth error) — this proves the account headers are
     sent. If it shows `PGRST106` the `api` schema is not exposed; `401` means a wrong or missing team key.
   - **Read Supabase** picked the route you expect.
   - **Blitz** answered (a `found` field), **LeadMagic/Prospeo** used the connected accounts, **ZeroBounce**
     returned `status` / `sub_status` / `smtp_provider`.
   - **Record** returned `{"received": 1, …}` and the `Out` node says `recorded: true`.
   Show the user the outputs. A draft test spends like a real run on the paths it takes.
6. `clay workflows publish <wf> --name "v1 - emails"`, then `clay routines create workflow <wf> --name "<Series> - Emails"`
   (`--name` is required).

## Run (per approved list)

Only through `bulk_run.py --approval approval.json` (it refuses any feed that isn't byte-identical to the approved
list) — commands in SKILL.md Phase 8. Plan → one pilot chunk → the rest, each after the user's yes.

**Rate limits:** Blitz allows 10 requests/second per endpoint on Start (25 on Scale). Bulk runs that exceed it get
`429` and those rows come back failed — `bulk_run.py --retry-failed` re-sends them (a Blitz miss or a 429 uses no
record). If many rows fail with 429, lower `--chunk`.

**Cost to quote before the pilot:** only people the lookup cannot answer cost anything — Blitz (no charge on a
miss; unlimited on paid plans within fair use), LeadMagic and Prospeo on the team's own plans for whoever reaches
them, plus ZeroBounce once per waterfall hit (1 Clay credit unless an own key is connected). The Supabase calls are
free.

## Traps

| Trap | Guard |
|---|---|
| Clay puts the newest HTTP account (e.g. Blitz) on the Supabase nodes → Supabase gets the Blitz key | set accounts in the UI, then `check-accounts` |
| A static `headers` value on an HTTP node is rejected (`Invalid input`) | none is set: Clay sends `Content-Type: application/json` itself, auth headers come from the account |
| An input path from a tool node not starting at `$.result` | the validator refuses it; every tool-output reference starts at `$.result` (tested) |
| The HTTP account's headers are not sent | the draft test's Supabase Lookup output — a JSON list, not `401` |
| Someone runs the workflow on `people.csv` before the manager approved | `bulk_run.py --approval` refuses; never omit it |
| The manager's change edits the approved file by hand | the hash no longer matches → re-run `approval.py apply` |
| Supabase unreachable mid-run | those rows end `lookup failed`, nothing paid; re-run them later |
| A waterfall hit that did not record (`recorded: false`) | `collect.py emails` counts it; re-run those rows — the lookup then finds the email and costs nothing |
| Clay's code node returns a different shape for the HTTP output | the Read nodes accept a raw list, `{body: …}`, a JSON string, nested wrappers (tested) |
