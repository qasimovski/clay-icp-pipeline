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

`scripts/email_workflow.py` generates all 30 nodes from one definition (no branch merges back, so each
provider gets its own tail), checks the graph offline, and builds it through the CLI.

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
| ZeroBounce (optional) | connected account | own key; leave blank to pay 1 Clay credit per check |

Then find each account's id: select it once on any tool node in any workflow and run
`python scripts/email_workflow.py accounts --workflow <that wf>`. Put the ids and the Supabase project URL in
`email_workflow.yaml` next to the run (shape: `assets/email_workflow.example.yaml`; keep it out of git).

## Build (once per series)

1. `clay workflows create --name "<Series> - Emails"` → an empty workflow id.
2. `python scripts/email_workflow.py check --config email_workflow.yaml` → `graph ok`.
3. `python scripts/email_workflow.py build --config email_workflow.yaml --workflow <wf> --state email_build_state.json`
   — creates the manual trigger, the 30 nodes (resumable: ids are saved as they are made), wires every edge,
   validates the graph.
4. **Draft test on 2–3 approved rows**, chosen so the paths differ: one person Supabase knows, one it doesn't.
   `clay workflows runs test <wf> --inputs row.json`, then `runs get <wf> <run> --verbose`, and read:
   - **Supabase Lookup** returned a JSON list (not an auth error) — this proves the account headers are
     sent. If it shows `PGRST106` the `api` schema is not exposed; `401` means a wrong or missing team key.
   - **Read Supabase** picked the route you expect.
   - **Blitz** answered (a `found` field), **LeadMagic/Prospeo** used the connected accounts, **ZeroBounce**
     returned `status` / `sub_status` / `smtp_provider`.
   - **Record** returned `{"received": 1, …}` and the `Out` node says `recorded: true`.
   Show the user the outputs. A draft test spends like a real run on the paths it takes.
5. `clay workflows publish <wf> --name "v1 - emails"`, then `clay routines create workflow <wf>`.

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
| The HTTP account's headers are not sent (Clay merges account + node headers) | the draft test's Supabase Lookup output — a JSON list, not `401` |
| Someone runs the workflow on `people.csv` before the manager approved | `bulk_run.py --approval` refuses; never omit it |
| The manager's change edits the approved file by hand | the hash no longer matches → re-run `approval.py apply` |
| Supabase unreachable mid-run | those rows end `lookup failed`, nothing paid; re-run them later |
| A waterfall hit that did not record (`recorded: false`) | `collect.py emails` counts it; re-run those rows — the lookup then finds the email and costs nothing |
| Clay's code node returns a different shape for the HTTP output | the Read nodes accept a raw list, `{body: …}`, a JSON string, nested wrappers (tested) |
