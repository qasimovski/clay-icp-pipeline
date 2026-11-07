"""Bulk-run a PUBLISHED Clay workflow over a feed CSV, chunk by chunk, resumably.

    feed CSV -> chunk JSONL (built once) -> routines runs start --bulk -> poll
             -> download results -> retry rows that came back "failed"

    python bulk_run.py --workflow wf_… --feed feed.csv --work runs/classify \
        --inputs "Company Name,Domain,Description,Event" --id-cols "Event,Entity,Domain" --plan
    ... --limit 1          # ONE chunk first, always (a pilot)
    ...                    # then the rest
    ... --retry-failed     # re-send only rows whose result was "failed"
    ... --reuse-from runs/enrich_company ../other_icp/runs/enrich_company   # earlier answers: copied, not bought

NEVER PAY TWICE (user, 29 Oct 2026). One row per id within a run; across runs, --reuse-from copies every id
an earlier run of the SAME workflow answered completely, with the SAME inputs, within --max-age-days (90).

THE DEFAULT SPENDS. A bare run starts every pending chunk; --plan shows the plan
and spends nothing. The email workflow always runs with --approval approval.json:
it refuses any feed that is not the exact list the marketing manager approved. Say the count and the per-row cost to the user first.

Chunks are written ONCE into <work>/feeds and reused; recomputing them after a
partial run would shift every boundary and make the state file lie. A chunk is
done only when state says so AND its result file exists. The run id is saved
the moment a run starts, so a kill can never re-charge a chunk on resume.

The workflow must be published and registered as a routine
(`clay routines create workflow <wf>`), or `runs start` answers not_found.
"""

import argparse
import collections
import csv
import json
import os
import re
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _clay import clay, to_cli_path  # noqa: E402


def load_state(path):
    return json.load(open(path, encoding="utf-8")) if os.path.exists(path) else {}


def save_state(path, state):
    tmp = path + ".tmp"
    json.dump(state, open(tmp, "w", encoding="utf-8"), indent=1)
    os.replace(tmp, path)


def check_linkedin_spellings(rows, id_cols):
    """Refuse a feed keyed on LinkedIn URL that spells one profile two ways (".../in/x" and ".../in/x/").
    One search per id dedupes on the TEXT, so both spellings would be enriched and paid for (Solar, Oct 2026:
    Clay writes the trailing slash, Blitz doesn't; 285 profiles had both). review_list.py writes one
    canonical form; this catches any other feed before a credit is spent."""
    if "LinkedIn URL" not in [c.strip() for c in id_cols.split(",")]:
        return
    spell = collections.defaultdict(set)
    for r in rows:
        u = (r.get("LinkedIn URL") or "").strip()
        m = re.search(r"linkedin\.com/in/([^/?#\s]+)", u.lower())
        if m:
            spell[m.group(1)].add(u)
    clash = {s: v for s, v in spell.items() if len(v) > 1}
    if clash:
        v = next(iter(clash.values()))
        raise SystemExit(f"{len(clash)} LinkedIn profile(s) appear under 2+ spellings (e.g. {sorted(v)}): they "
                         f"would be searched and paid for twice. Normalise the feed to "
                         f"https://www.linkedin.com/in/<slug> first (review_list.canonical_linkedin).")


def build_chunks(args, feeds, state):
    existing = sorted(n for n in os.listdir(feeds) if n.endswith(".jsonl"))
    if existing and not args.retry_failed:
        return [os.path.join(feeds, n) for n in existing]
    if args.retry_failed:
        unrun = [n for n in existing if n.startswith("retry_")
                 and (state.get(n) or {}).get("status") != "done"]
        if unrun:                         # never queue the same failed rows twice
            return [os.path.join(feeds, n) for n in existing]
    rows = list(csv.DictReader(open(args.feed, encoding="utf-8-sig")))
    if not rows:
        raise SystemExit(f"{args.feed} has no rows")
    cols = [c.strip() for c in args.inputs.split(",") if c.strip()]
    missing = [c for c in cols + args.id_cols.split(",") if c.strip() and c.strip() not in rows[0]]
    if missing:
        raise SystemExit(f"feed has no column(s) {missing} — check --inputs / --id-cols")
    check_linkedin_spellings(rows, args.id_cols)
    seen, recs = set(), []
    for r in rows:
        rid = "|".join((r.get(c.strip()) or "").strip() for c in args.id_cols.split(","))
        if not rid.strip("|") or rid in seen:
            continue                      # one search per id
        seen.add(rid)
        recs.append({"id": rid, "inputs": {c: r.get(c, "") for c in cols}})
    if getattr(args, "reuse_from", None) and not args.retry_failed:
        hit = reusable(args.reuse_from, recs, args.workflow, args.max_age_days)
        if hit:
            os.makedirs(os.path.join(args.work, "results"), exist_ok=True)
            with open(os.path.join(args.work, "results", "reused.jsonl"), "w", encoding="utf-8", newline="\n") as fh:
                for r in hit.values():
                    fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        print(f"reuse: {len(hit):,} of {len(recs):,} already answered by an earlier run (0 actions); "
              f"{len(recs) - len(hit):,} to send")
        recs = [x for x in recs if x["id"] not in hit]
        if not recs:
            return []
    long_ids = [x["id"] for x in recs if len(x["id"]) > 64]
    if long_ids:
        # Clay rejects a bulk row id over 64 characters (Mining speakers, Oct 2026). Give the feed a
        # short stable id column (speakers.py writes `Row ID` = sha1[:16]) and pass --id-cols "Row ID".
        raise SystemExit(f"{len(long_ids)} row ids are over 64 characters (e.g. {long_ids[0][:80]!r}) — "
                         f"use a short stable id column")
    if args.retry_failed:
        failed = failed_ids(os.path.join(args.work, "results"))
        recs = [x for x in recs if x["id"] in failed]
        if args.plan or not recs:
            print(f"retry: {len(recs)} failed rows" + (" (--plan: not written)" if recs else ""))
            return [os.path.join(feeds, n) for n in existing]
        n = len([f for f in existing if f.startswith("retry_")])
        path = os.path.join(feeds, f"retry_{n:03d}.jsonl")
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            for x in recs:
                fh.write(json.dumps(x, ensure_ascii=False) + "\n")
        print(f"retry chunk: {len(recs)} failed rows -> {path}")
        return [os.path.join(feeds, n) for n in existing] + [path]
    paths = []
    for i in range(0, len(recs), args.chunk):
        p = os.path.join(feeds, f"chunk_{i // args.chunk:03d}.jsonl")
        with open(p, "w", encoding="utf-8", newline="\n") as fh:
            for x in recs[i:i + args.chunk]:
                fh.write(json.dumps(x, ensure_ascii=False) + "\n")
        paths.append(p)
    return paths


def reusable(reuse_dirs, recs, workflow, max_age_days, now=None):
    """{id: result record} that an earlier run already paid for and that this run may copy instead of
    sending (user, 29 Oct 2026: "when you enrich a company once, you should not do it twice for the same
    competitive event or for the same ICP"; reuse window 90 days; across ICPs only company data is reused).

    A record is reused only when ALL hold:
      - the earlier run used the same workflow (its state.json `_workflow`; folders from before this rule
        have none, and are trusted because the operator named them);
      - it sent EXACTLY the same inputs for that id (so a Find People search, whose inputs carry the ICP's
        side / category / countries / seniority, is never reused for another ICP or title set, while Enrich
        Company, keyed by domain alone, is reused across events and ICPs);
      - its latest result is "complete" and the result file is at most max_age_days old.
    """
    now = now or time.time()
    want = {x["id"]: x["inputs"] for x in recs}
    out = {}
    for d in reuse_dirs:
        st = load_state(os.path.join(d, "state.json")) if os.path.exists(os.path.join(d, "state.json")) else {}
        if st.get("_workflow") and st["_workflow"] != workflow:
            print(f"reuse: skipping {d} (workflow {st['_workflow']}, not {workflow})")
            continue
        sent = {}
        fdir = os.path.join(d, "feeds")
        for n in sorted(os.listdir(fdir)) if os.path.isdir(fdir) else []:
            for line in open(os.path.join(fdir, n), encoding="utf-8"):
                if line.strip():
                    x = json.loads(line)
                    sent[x["id"]] = x["inputs"]
        rdir = os.path.join(d, "results")
        for n in sorted(os.listdir(rdir)) if os.path.isdir(rdir) else []:
            p = os.path.join(rdir, n)
            if (now - os.path.getmtime(p)) / 86400 > max_age_days:
                continue
            for line in open(p, encoding="utf-8"):
                if not line.strip():
                    continue
                r = json.loads(line)
                i = r.get("id")
                if i in want and sent.get(i) == want[i]:
                    if r.get("status") == "complete":
                        out[i] = r
                    else:
                        out.pop(i, None)          # a later failed record beats an earlier success
    return out


def failed_ids(results):
    """ids whose LATEST record is not complete (retry files sort after chunks)."""
    last = {}
    if os.path.isdir(results):
        for n in sorted(os.listdir(results)):
            for line in open(os.path.join(results, n), encoding="utf-8"):
                if line.strip():
                    r = json.loads(line)
                    last[r["id"]] = r.get("status")
    return {k for k, v in last.items() if v != "complete"}


def adopt_orphan(path, state):
    """-> the run id of a run Clay started for this chunk whose start call we never heard back from,
    or None when there is none. Stops (SystemExit) when it can't tell which run is ours.

    Candidates: the 20 most recent routine runs that no state entry knows, with `total` equal to the
    chunk's row count. One -> adopt. None -> the start never reached Clay, so starting is safe.
    Several -> ambiguous: stop rather than guess (a wrong guess re-pays or loses a chunk)."""
    rows = sum(1 for line in open(path, encoding="utf-8") if line.strip())
    known = {v.get("run_id") for v in state.values() if isinstance(v, dict)}
    recent = clay(["routines", "runs", "list", "--limit", "20"]).get("data") or []
    # Only runs of OUR routine: other workflows (or colleagues) may have 250-row runs too.
    ours = {(r.get("routine") or {}).get("id") for r in recent if r.get("routineRunId") in known}
    cands = [r["routineRunId"] for r in recent
             if r.get("routineRunId") not in known and r.get("total") == rows
             and (not ours or (r.get("routine") or {}).get("id") in ours)]
    if len(cands) > 1:
        raise SystemExit(f"{os.path.basename(path)}: its start call timed out and {len(cands)} unknown "
                         f"runs of {rows} rows exist ({cands}); set its run_id in state.json by hand")
    return cands[0] if cands else None


def run_chunk(args, path, state, state_path, results):
    name = os.path.basename(path)
    entry = state.get(name) or {}
    dest = os.path.join(results, name)
    if entry.get("status") == "done" and os.path.exists(dest):
        return
    rid = entry.get("run_id")
    if not rid and entry.get("status") == "starting":
        # The last `runs start` never returned (seen 2026-11-06: network_timeout). The run may exist
        # at Clay without our id, and starting again would pay for the chunk twice. Adopt it when
        # exactly one recent run is unknown to the state file and has this chunk's row count.
        rid = adopt_orphan(path, state)
        if rid:
            state[name] = {"status": "running", "run_id": rid}
            save_state(state_path, state)
            print(f"{name}: adopted {rid} (its start call had timed out)", flush=True)
    if not rid:
        state[name] = {"status": "starting"}
        save_state(state_path, state)
        rid = clay(["routines", "runs", "start", f"workflow:{args.workflow}",
                    "--bulk", to_cli_path(path)])["routineRunId"]
        state[name] = {"status": "running", "run_id": rid}
        save_state(state_path, state)
        print(f"{name}: started {rid}", flush=True)
    deadline = time.time() + args.timeout
    while True:
        d = clay(["routines", "runs", "get", rid, "--bulk"], check=False)
        st = d.get("status") if isinstance(d, dict) else None
        if st in ("complete", "validation_failed", "processing_failed"):
            break
        if time.time() > deadline:
            print(f"{name}: still running after {args.timeout}s — re-run to keep waiting")
            return
        time.sleep(20)
    if st != "complete":
        state[name] = {"status": st, "run_id": rid}
        save_state(state_path, state)
        raise SystemExit(f"{name}: run {rid} ended {st} — stop and look")
    # The signed result URL expires in ~15 minutes: download now.
    urllib.request.urlretrieve(d["resultUrl"], dest)
    n = sum(1 for line in open(dest, encoding="utf-8") if line.strip())
    state[name] = {"status": "done", "run_id": rid, "rows": n}
    save_state(state_path, state)
    print(f"{name}: {n} results -> {dest}", flush=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--workflow", required=True)
    ap.add_argument("--feed", required=True)
    ap.add_argument("--work", required=True, help="folder for feeds/, results/, state.json")
    ap.add_argument("--inputs", required=True, help="feed columns sent as trigger inputs")
    ap.add_argument("--id-cols", default="Domain", help="columns joined with | as the row id")
    ap.add_argument("--chunk", type=int, default=250)
    ap.add_argument("--limit", type=int, help="at most N chunks this invocation")
    ap.add_argument("--timeout", type=int, default=2400, help="seconds to wait per chunk")
    ap.add_argument("--plan", action="store_true", help="show the plan, spend nothing")
    ap.add_argument("--retry-failed", action="store_true")
    ap.add_argument("--reuse-from", nargs="*", default=[],
                    help="earlier --work folders of the SAME workflow: rows they answered with the same inputs "
                         "within --max-age-days are copied, not sent (0 actions)")
    ap.add_argument("--max-age-days", type=int, default=90, help="reuse window (user, 29 Oct 2026: 90 days)")
    ap.add_argument("--approval", help="approval.json from approval.py apply: REQUIRED for the email workflow; the feed must be "
                                       "byte-identical to the list the marketing manager approved")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    feeds = os.path.join(args.work, "feeds")
    results = os.path.join(args.work, "results")
    os.makedirs(feeds, exist_ok=True)
    os.makedirs(results, exist_ok=True)
    state_path = os.path.join(args.work, "state.json")
    state = load_state(state_path)
    if state.setdefault("_workflow", args.workflow) != args.workflow:
        raise SystemExit(f"{args.work} belongs to workflow {state['_workflow']} - use a new --work folder")
    save_state(state_path, state)
    if args.approval:                    # the marketing-manager gate: no approved list, no paid email search
        from approval import verify
        rec = verify(args.approval, args.feed)
        if state.setdefault("_approved_sha256", rec["approved_sha256"]) != rec["approved_sha256"]:
            raise SystemExit(f"{args.work} was started from a different approved list - use a new --work folder")
        save_state(state_path, state)
        print(f"approved by {rec['approved_by']} at {rec['approved_at']}: {rec['rows_approved']} people")
    chunks = build_chunks(args, feeds, state)
    pending = [c for c in chunks if (state.get(os.path.basename(c)) or {}).get("status") != "done"]
    if args.retry_failed:
        # Solar, 28 Oct 2026: "--retry-failed --limit 1" ran the next REGULAR chunk (250 unapproved rows)
        # because retry chunks sort after the pending regular ones. A retry runs retry rows only.
        pending = [c for c in pending if os.path.basename(c).startswith("retry_")]
    rows = sum(sum(1 for _ in open(c, encoding="utf-8")) for c in pending)
    print(f"chunks {len(chunks)}  pending {len(pending)}  rows to run {rows:,}")
    if args.plan:
        print("--plan: nothing started")
        return
    for c in pending[:args.limit] if args.limit else pending:
        run_chunk(args, c, state, state_path, results)
    left = failed_ids(results)
    print(f"failed rows on disk: {len(left)}" + ("  -> re-run with --retry-failed" if left else ""))


if __name__ == "__main__":
    main()
