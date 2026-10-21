"""Bulk-run a PUBLISHED Clay workflow over a feed CSV, chunk by chunk, resumably.

    feed CSV -> chunk JSONL (built once) -> routines runs start --bulk -> poll
             -> download results -> retry rows that came back "failed"

    python bulk_run.py --workflow wf_… --feed feed.csv --work runs/classify \
        --inputs "Company Name,Domain,Description,Event" --id-cols "Event,Entity,Domain" --plan
    ... --limit 1          # ONE chunk first, always (a pilot)
    ...                    # then the rest
    ... --retry-failed     # re-send only rows whose result was "failed"

THE DEFAULT SPENDS. A bare run starts every pending chunk; --plan shows the plan
and spends nothing. Say the count and the per-row cost to the user first.

Chunks are written ONCE into <work>/feeds and reused; recomputing them after a
partial run would shift every boundary and make the state file lie. A chunk is
done only when state says so AND its result file exists. The run id is saved
the moment a run starts, so a kill can never re-charge a chunk on resume.

The workflow must be published and registered as a routine
(`clay routines create workflow <wf>`), or `runs start` answers not_found.
"""

import argparse
import csv
import json
import os
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
    seen, recs = set(), []
    for r in rows:
        rid = "|".join((r.get(c.strip()) or "").strip() for c in args.id_cols.split(","))
        if not rid.strip("|") or rid in seen:
            continue                      # one search per id
        seen.add(rid)
        recs.append({"id": rid, "inputs": {c: r.get(c, "") for c in cols}})
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


def run_chunk(args, path, state, state_path, results):
    name = os.path.basename(path)
    entry = state.get(name) or {}
    dest = os.path.join(results, name)
    if entry.get("status") == "done" and os.path.exists(dest):
        return
    rid = entry.get("run_id")
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
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    feeds = os.path.join(args.work, "feeds")
    results = os.path.join(args.work, "results")
    os.makedirs(feeds, exist_ok=True)
    os.makedirs(results, exist_ok=True)
    state_path = os.path.join(args.work, "state.json")
    state = load_state(state_path)
    chunks = build_chunks(args, feeds, state)
    pending = [c for c in chunks if (state.get(os.path.basename(c)) or {}).get("status") != "done"]
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
