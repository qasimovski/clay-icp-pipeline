"""Fleet driver — run the people search over the classified CE companies.

    feed CSV -> chunk JSONL -> bulk routine run -> poll -> download results
             -> export_ce_people.py flattens them to the deliverable CSV

Scope is `Highways CE - people feed.csv` (4,547 companies), which is already
filtered to rows that HAVE a Domain, WERE `Is New` at classification time, and
RESOLVE to an ICP persona and country list. Skip-by-absence: a company that is
not in that file cannot be searched by any mistake made here.

WHY CHUNKS, and why they are written to disk once. This environment kills long
background tasks at a variable cadence, so a single 4,547-row run would lose
everything on a kill. Chunks of 250 cap the loss at one chunk. They are built
ONCE into ce_people_feeds/ and reused verbatim afterwards — if the chunking were
recomputed each run, finishing a chunk would shrink the pending list and shift
every later boundary, so a chunk id would silently come to mean a different set
of companies and the state file would be lying about what had been done.

ALREADY-COLLECTED DOMAINS ARE NEVER RE-SENT. The pilot runs are on disk in
ce_people_results/ in the same shape as a bulk result, so the 65 companies they
cover are skipped when the chunks are first built. That is also the resume story
for a chunk that was collected but whose state write was lost.

MONEY. One `cpj-find-lists-of-people` call per row — 4,547 in total, up to 10
people each. Measured on a 60-row random sample (seed 20260913): 60% of
companies return people, 5.8 people per hit, the other 40% being "No Profile
Found" — the domain does not resolve to a LinkedIn company, which no filter
change fixes. So expect roughly 2,700 companies and 15,700 people.

The default is NOT dry: a bare invocation spends. `--dry-run` shows the plan.

    python ce_people_rollout.py --plan          # chunk sizes, no browser, no spend
    python ce_people_rollout.py --dry-run       # build chunks, start nothing
    python ce_people_rollout.py --limit 1       # one chunk, for real
    python ce_people_rollout.py --start-only    # fire every chunk, do not wait
    python ce_people_rollout.py --collect       # download results already started
    python ce_people_rollout.py                 # start + wait + collect, chunk by chunk
"""

import argparse
import csv
import json
import os
import subprocess
import sys
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

import state_io  # noqa: E402

ENV = {**os.environ, "MSYS_NO_PATHCONV": "1"}

IDS = os.path.join(SCRIPT_DIR, "ce_nodes", "people_workflow.json")

# Both scopes run through the SAME published workflow — only the feed differs.
# Other Sources supplies a `Countries Override` column and CE does not; the
# mapping node falls back to the ICP country list when it arrives blank, so one
# driver serves both. Filenames are per scope so the two can never share state.
SCOPES = {
    "ce": ("Highways CE - people feed.csv", "ce_people"),
    "other_sources": ("Highways Other Sources - people feed.csv",
                      "other_sources_people"),
    # The two Buyside P&S categories that never got a ClayQL People table.
    "bs_gap": ("Highways BuySide P&S gap - people feed.csv", "bs_gap_people"),
    # CE re-run after the scraper-side ICP fit filter: the CE feed stamped with
    # `Fit` by build_ce_fit_people_feed.py. Own state/results, so the completed
    # `ce` run is never touched and its collected-domain skip cannot hide these
    # rows. The pilot is a separate scope because chunks are built once and
    # reused: a pilot chunk dir under `ce_fit` would freeze the full run to the
    # pilot's rows.
    "ce_fit_pilot": ("Highways CE - people feed - fit - pilot.csv",
                     "ce_fit_pilot_people"),
    "ce_fit": ("Highways CE - people feed - fit.csv", "ce_fit_people"),
    # The `ce_fit` companies that hit the 10-person cap, searched once per
    # seniority half (build_ce_fit_split_feed.py). One scope per half because
    # results are keyed by Domain and the two halves share every Domain.
    "ce_fit_sen_a": ("Highways CE - people feed - fit - sen A.csv",
                     "ce_fit_sen_a_people"),
    "ce_fit_sen_b": ("Highways CE - people feed - fit - sen B.csv",
                     "ce_fit_sen_b_people"),
    # Other Sources re-run on workflow v7 (2026-10-19): the September run never
    # applied `Countries Override` (v3's mapping node was not wired to it) and
    # returned identifiers only. Same feed, own results, so the September run
    # stays as it was.
    "other_sources_v7": ("Highways Other Sources - people feed.csv",
                         "other_sources_v7_people"),
    # The rows of `other_sources_v7` that came back "failed" (Clay's "Unknown
    # error"); collected results are copied into that scope's results dir as
    # retry_*.jsonl, which sort after chunk_* and so supersede the failures.
    "other_sources_v7_retry": ("Highways Other Sources - people feed - v7 - retry.csv",
                               "other_sources_v7_retry_people"),
    "other_sources_v7_sen_a":("Highways Other Sources - people feed - v7 - sen A.csv",
                               "other_sources_v7_sen_a_people"),
    "other_sources_v7_sen_b": ("Highways Other Sources - people feed - v7 - sen B.csv",
                               "other_sources_v7_sen_b_people"),
}
def use_scope(name):
    """Point the module's paths at one scope. Called at import for `ce`, so the
    filenames are byte-identical to what the completed CE run already wrote —
    CLAUDE.md: never rename anything persisted."""
    global FEED, FEED_DIR, RESULT_DIR, STATE, LOCK
    feed, slug = SCOPES[name]
    FEED = os.path.join(SCRIPT_DIR, "ce_classification_exports", feed)
    FEED_DIR = os.path.join(SCRIPT_DIR, f"{slug}_feeds")
    RESULT_DIR = os.path.join(SCRIPT_DIR, f"{slug}_results")
    STATE = os.path.join(SCRIPT_DIR, f"{slug}_rollout_state.json")
    LOCK = os.path.join(SCRIPT_DIR, f"{slug}_rollout.lock")


use_scope("ce")


CHUNK = 250
LOCK_STALE_SECONDS = 300
# `Countries Override` is listed for every scope. A feed without that column
# yields "" for it, which is precisely the "use the ICP list" case — so adding
# it here cannot change CE behaviour. `Fit` likewise: blank means "no fit
# decision", which the workflow's `Fit Keep?` gate treats as KEEP. `Seniority
# Group` too: blank means the full ICP seniority list.
INPUTS = ("Countries Override", "Company Name", "Domain", "Side", "Classification",
          "Company Event Name", "Exhibitor/Sponsor", "Fit", "Seniority Group")


def wslpath(p):
    """RAW WSL path. Never quoted here — see shq."""
    return p.replace("C:\\", "/mnt/c/").replace("\\", "/")


def shq(p):
    """Shell-quoted WSL path, for a command line. Kept separate from wslpath
    because curl's config file does its own quoting: passing an already-quoted
    path into a curlrc produces a filename with literal quotes in it, which
    fails as curl error 23."""
    return "'" + wslpath(p) + "'"


# ---------------------------------------------------------------- lock

def take_lock(force=False):
    """One worker at a time. Overlapping workers double-charged 114 rows across
    6 tables on the classification pass — the relaunch check (`Get-Process
    python`) saw nothing because the killed shell's child had survived it."""
    if os.path.exists(LOCK) and not force:
        age = time.time() - os.path.getmtime(LOCK)
        if age < LOCK_STALE_SECONDS:
            raise SystemExit(
                f"another worker held the lock {age:.0f}s ago ({LOCK}).\n"
                f"Wait, or pass --force-unlock if you are certain it is dead.")
        print(f"! stale lock ({age:.0f}s old) — taking it")
    open(LOCK, "w").write(str(os.getpid()))


def heartbeat():
    try:
        os.utime(LOCK, None)
    except OSError:
        pass


def release():
    try:
        os.remove(LOCK)
    except OSError:
        pass


# ---------------------------------------------------------------- planning

def collected_domains():
    """Every id already carrying a result on disk, whatever produced it."""
    done = set()
    if not os.path.isdir(RESULT_DIR):
        return done
    for name in os.listdir(RESULT_DIR):
        if not name.endswith(".jsonl"):
            continue
        for line in open(os.path.join(RESULT_DIR, name), encoding="utf-8"):
            line = line.strip()
            if line:
                try:
                    done.add(json.loads(line)["id"])
                except (ValueError, KeyError):
                    raise SystemExit(f"unreadable result line in {name} — refusing "
                                     f"to guess what has been paid for")
    return done


def chunk_files():
    if not os.path.isdir(FEED_DIR):
        return []
    return sorted(os.path.join(FEED_DIR, n) for n in os.listdir(FEED_DIR)
                  if n.startswith("chunk_") and n.endswith(".jsonl"))


def build_chunks():
    """Write the chunk JSONLs once. Reuse them for ever after."""
    existing = chunk_files()
    if existing:
        return existing, 0

    if not os.path.exists(FEED):
        raise SystemExit(f"no feed at {FEED} — run build_ce_people_feed.py first")
    rows = list(csv.DictReader(open(FEED, encoding="utf-8-sig")))
    done = collected_domains()
    pending = [r for r in rows if r["Domain"] not in done]

    os.makedirs(FEED_DIR, exist_ok=True)
    paths = []
    for i in range(0, len(pending), CHUNK):
        part = pending[i:i + CHUNK]
        p = os.path.join(FEED_DIR, f"chunk_{i // CHUNK:03d}.jsonl")
        with open(p, "w", encoding="utf-8", newline="\n") as fh:
            for r in part:
                fh.write(json.dumps(
                    {"id": r["Domain"],
                     "inputs": {c: r.get(c, "") for c in INPUTS}},
                    ensure_ascii=False) + "\n")
        paths.append(p)
    return paths, len(rows) - len(pending)


# ---------------------------------------------------------------- clay

def routine():
    if not os.path.exists(IDS):
        raise SystemExit(f"no {IDS} — run build_ce_people_workflow.py first")
    return "workflow:" + json.load(open(IDS, encoding="utf-8"))["workflow"]


def start_bulk(path):
    """Fire a bulk run and return its id WITHOUT waiting.

    The caller MUST persist the id before doing anything else: a run that was
    started but whose id was never recorded is invisible to --collect and would
    be re-run, and re-charged, on the next pass.
    """
    r = subprocess.run(
        ["wsl", "bash", "-lc",
         f"clay routines runs start {routine()} --bulk {shq(path)}"],
        capture_output=True, env=ENV)
    if r.returncode != 0:
        raise RuntimeError("bulk start failed: "
                           + r.stderr.decode("utf-8", "replace")[:300])
    return json.loads(r.stdout.decode("utf-8", "replace"))["routineRunId"]


def fetch(rid):
    g = subprocess.run(["wsl", "bash", "-lc", f"clay routines runs get {rid} --bulk"],
                       capture_output=True, env=ENV)
    if g.returncode != 0:
        return None
    return json.loads(g.stdout.decode("utf-8", "replace"))


def wait(rid, seconds=2400):
    deadline = time.time() + seconds
    last = None
    while time.time() < deadline:
        d = fetch(rid)
        if d and d.get("status") in ("complete", "validation_failed",
                                     "processing_failed"):
            return d
        if d:
            now = f"{d.get('finished', 0)}/{d.get('total', '?')}"
            if now != last:
                print(f"      {now}", flush=True)
                last = now
        heartbeat()
        time.sleep(20)
    raise RuntimeError(f"run {rid} did not finish within {seconds}s")


def download(payload, dest):
    """Fetch the signed result file NOW — the URL expires in ~15 minutes."""
    url = payload.get("resultUrl")
    if not url:
        raise RuntimeError("completed run carried no resultUrl")
    os.makedirs(RESULT_DIR, exist_ok=True)
    cfg = dest + ".curlrc"
    with open(cfg, "w", encoding="utf-8") as fh:
        fh.write(f'url = "{url}"\noutput = "{wslpath(dest)}"\n')
    r = subprocess.run(["wsl", "bash", "-lc", f"curl -sS -K {shq(cfg)}"],
                       capture_output=True, env=ENV)
    os.remove(cfg)
    if r.returncode != 0 or not os.path.exists(dest) or os.path.getsize(dest) == 0:
        raise RuntimeError("download failed: "
                           + r.stderr.decode("utf-8", "replace")[:200])
    return sum(1 for line in open(dest, encoding="utf-8") if line.strip())


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--plan", action="store_true", help="report scope, no spend")
    ap.add_argument("--dry-run", action="store_true",
                    help="build the chunks and report, start nothing")
    ap.add_argument("--only", action="append", default=[],
                    help="chunk name, e.g. chunk_003")
    ap.add_argument("--limit", type=int, help="at most N chunks this invocation")
    ap.add_argument("--start-only", action="store_true")
    ap.add_argument("--collect", action="store_true")
    ap.add_argument("--force-unlock", action="store_true")
    ap.add_argument("--scope", default="ce", choices=sorted(SCOPES),
                    help="which feed to run; state and results are per scope")
    args = ap.parse_args()
    use_scope(args.scope)

    paths, skipped = build_chunks()
    total = sum(sum(1 for _ in open(p, encoding="utf-8")) for p in paths)
    print(f"chunks: {len(paths)}  rows to search: {total:,}"
          + (f"  (already collected, skipped: {skipped})" if skipped else ""))
    if args.plan:
        for p in paths:
            n = sum(1 for _ in open(p, encoding="utf-8"))
            print(f"   {os.path.basename(p):18} {n:4} rows")
        return

    state = state_io.load_json(STATE)

    todo = [p for p in paths
            if not args.only or os.path.basename(p)[:-6] in args.only]
    pending = []
    for p in todo:
        key = os.path.basename(p)[:-6]
        st = state.get(key) or {}
        res = os.path.join(RESULT_DIR, key + ".jsonl")
        # Done means BOTH: state says so AND the results are on disk. A state
        # file that outlived its results would otherwise mark work complete
        # that cannot be exported.
        if st.get("status") == "done" and os.path.exists(res):
            continue
        pending.append((key, p, st))
    if args.limit:
        pending = pending[:args.limit]

    print(f"pending this run: {len(pending)}")
    if args.dry_run or not pending:
        for key, p, st in pending:
            print(f"   {key:14} {st.get('status', 'new')}")
        return

    take_lock(force=args.force_unlock)
    try:
        for key, path, st in pending:
            n = sum(1 for _ in open(path, encoding="utf-8"))
            dest = os.path.join(RESULT_DIR, key + ".jsonl")
            rid = st.get("run_id")

            if not rid and not args.collect:
                # Intent-first: record that we are about to spend BEFORE the
                # call, so a kill between start and the id write is visible as
                # `starting` rather than looking like untouched work.
                state[key] = {"status": "starting", "rows": n}
                state_io.save_json(STATE, state)
                print(f"\n{key}  {n} rows — starting", flush=True)
                rid = start_bulk(path)
                state[key] = {"status": "running", "rows": n, "run_id": rid}
                state_io.save_json(STATE, state)
                print(f"   run {rid}", flush=True)

            if not rid:
                print(f"\n{key}: nothing started yet — skipped by --collect")
                continue
            if args.start_only:
                continue

            if args.collect:
                # DRAIN WHAT IS READY; never block the queue. `--start-only`
                # fires every chunk at once and a bulk run routinely reaches
                # 248/250 in minutes and then spends an hour retrying its last
                # two companies. Polling in order meant 40 minutes burned on
                # each straggler while nine ALREADY-COMPLETE chunks sat
                # uncollected behind them. One status check, download if
                # terminal, move on — run `--collect` again for the rest.
                payload = fetch(rid)
                if not payload or payload.get("status") not in (
                        "complete", "validation_failed", "processing_failed"):
                    done_n = (payload or {}).get("finished", "?")
                    tot_n = (payload or {}).get("total", "?")
                    print(f"{key}: still running ({done_n}/{tot_n}) — skipped")
                    continue
            else:
                print(f"\n{key}  waiting on {rid}", flush=True)
                try:
                    payload = wait(rid)
                except RuntimeError as e:
                    # A slow run is NOT a failure and must not abandon the rest.
                    print(f"   .. still running ({e}); will collect next pass")
                    continue
            if payload.get("status") != "complete":
                state[key] = {"status": "failed", "rows": n, "run_id": rid,
                              "why": payload.get("status")}
                state_io.save_json(STATE, state)
                print(f"   !! {payload.get('status')}")
                continue
            got = download(payload, dest)
            state[key] = {"status": "done", "rows": n, "run_id": rid,
                          "results": got}
            state_io.save_json(STATE, state)
            print(f"   -> {got} results   {dest}")
            heartbeat()
    finally:
        release()

    done = sum(1 for v in state.values() if v.get("status") == "done")
    print(f"\nchunks done: {done}/{len(paths)}")


if __name__ == "__main__":
    main()
