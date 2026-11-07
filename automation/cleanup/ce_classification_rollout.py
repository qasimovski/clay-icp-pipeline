"""Fleet driver — run the Highways Registrar classification over every
Competitive Events table, one bulk run per table.

Pipeline per table:

    eligible rows -> bulk JSONL -> routine run -> poll -> download results
                  -> export_ce_classification.py joins them onto the full table

Scope is both CE manifests (67 tables / 39 events). `Highways UK 2025` is
excluded by name: it is the hand-imported sample, it has no `Is New` column, and
the workflow extractor excludes it for the same reason.

WHY ONE BULK RUN PER TABLE rather than one run for the fleet: resume
granularity. This environment kills long background tasks at a variable cadence
— it did so repeatedly while this pass was being built — and a per-table run
means a kill costs at most one table's progress, never the fleet's. It also
keeps the row-id join trivial and lets `--only` / `--after` / `--shards` work.

WHY THE FEED IS PRE-FILTERED to `Is New = true AND Domain non-empty`: the
workflow gates on exactly these two conditions, so sending the rest would spend
workflow run slots to reach a terminal "skipped" node. The gate stays in the
workflow as defence-in-depth — this is an optimisation, not the guard. Measured
on the fleet: it cuts what we send by roughly a quarter.

THE EMPTY-DOMAIN ROWS ARE OUT OF SCOPE BY DECISION, not by oversight. Per the
user (2026-09-12): **do not backfill `Domain`, and do not run the Claygent where
`Domain` is empty.** That is 1,134 of 7,418 rows (15%), concentrated in the
Middle East and Asia shows where the scrapers got thin website coverage and
Enrich Company resolved nothing — Intertraffic China 97/145 (67%), Big 5 UAE
140/428, Big 5 Construct Saudi 127/489, Big 5 Egypt 74/208, SaMoTer 79/400, and
all 17 of ITS Australia Summit's exhibitors.

`Official Domain` is populated on ~100% of rows and would recover most of them,
so the option was live and was declined. Do not "fix" this by adding a fallback.
Note also that a fallback on its own would recover nothing anyway: those rows
were never ledger-checked (the ledger column carries "Run only if Domain is not
empty"), so their `Is New` sits at the unchecked default of false and the
`Is New?` gate would reject them regardless. Recovering them would mean
re-running the ledger too — a separate, paid decision nobody has taken.

MONEY. Each eligible row costs 1 action + 1 data credit. Nothing else in this
pass is paid: reading rows ignores the query quota, and the export is local. The
default is therefore NOT dry — `--dry-run` shows the plan, and a bare invocation
spends. That is the opposite of the Playwright passes' configure-only default,
and deliberately so: a bulk run is atomic and resumable, there is no
half-configured state to leave behind.

RE-ENTRY. State lives in ce_classification_rollout_state.json (state_io: atomic,
fails loud). A table is skipped when state says `done` AND its results file is
on disk — both, because a state file that outlived its results would otherwise
mark work complete that cannot be exported. A table whose run failed is left
un-recorded so a relaunch retries it.

    python ce_classification_rollout.py --list        # scope + eligible, no spend
    python ce_classification_rollout.py --dry-run
    python ce_classification_rollout.py --only "Geotechnica"
    python ce_classification_rollout.py --limit 3
    python ce_classification_rollout.py --after "Geotechnica"
    python ce_classification_rollout.py --shards 4 --shard 0
"""

import argparse
import json
import os
import subprocess
import sys
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
sys.path.insert(0, os.path.join(os.path.dirname(SCRIPT_DIR),
                                 "build_automation"))

import state_io                      # noqa: E402
import audit_ce_classification as A  # noqa: E402

ENV = {**os.environ, "MSYS_NO_PATHCONV": "1"}
# Live Clay ids come from gitignored config (docs/SENSITIVE_DATA.md); a literal
# here would be committed. Fails loud rather than resolving to None and building
# a URL like ".../workflows/None".
def _id(key):
    import browser_session
    v = browser_session.local_setting(key, "CLAY_" + key.upper())
    if not v:
        raise SystemExit(f"missing `{key}` in config/local.yaml "
                         f"(or $CLAY_{key.upper()}) — see config/local.yaml.example")
    return v


ROUTINE = "workflow:" + _id("highways_ce_workflow_id")
FEED_DIR = os.path.join(SCRIPT_DIR, "ce_classification_feeds")
RESULT_DIR = os.path.join(SCRIPT_DIR, "ce_classification_results")
STATE = os.path.join(SCRIPT_DIR, "ce_classification_rollout_state.json")
LOCK = os.path.join(SCRIPT_DIR, "ce_classification_rollout.lock")

# A lock is abandoned after this long without a heartbeat. Kept SHORT because
# this environment kills background tasks every few minutes under memory
# pressure, so an orphaned lock is routine; a long threshold would block the
# relaunch that is the normal recovery. The heartbeat is touched inside the poll
# loop, not just per table, so a live worker refreshes it every few seconds.
LOCK_STALE_SECONDS = 300

# Must match the v2 trigger's inputSchema exactly.
FEED_KEYS = ("Company Name", "Domain", "Description", "Description (2)",
             "Primary Offerings", "Event", "Year", "Is New")


def take_lock(force=False):
    """Refuse to start while another worker holds the lock.

    CLAUDE.md warns to check `Get-Process python` before relaunching "to avoid
    overlapping workers"; this makes the driver enforce it instead of trusting
    the operator. It is not theoretical — two workers overlapped on 2026-09-12
    and National Civils Show was bulk-run twice (two routineRunIds, 3 rows
    charged twice), because each worker saw the table as pending before the
    other had written its state.

    Staleness is judged by HEARTBEAT AGE, not by whether the pid is alive: this
    environment kills background tasks abruptly, so an abandoned lock is the
    normal case, and pid liveness checks are unreliable across the win32/WSL
    split anyway.
    """
    if os.path.exists(LOCK) and not force:
        age = time.time() - os.path.getmtime(LOCK)
        if age < LOCK_STALE_SECONDS:
            try:
                held = open(LOCK, encoding="utf-8").read().strip()
            except OSError:
                held = "?"
            raise SystemExit(
                f"another worker holds the lock ({held}, heartbeat {int(age)}s "
                f"ago).\nIf you are certain it is dead: --force-unlock, or "
                f"delete\n  {LOCK}")
        print(f"stale lock ({int(age)}s old) — taking over", flush=True)
    with open(LOCK, "w", encoding="utf-8") as fh:
        fh.write(f"pid={os.getpid()} started={time.strftime('%Y-%m-%d %H:%M:%S')}")


def heartbeat():
    try:
        os.utime(LOCK, None)
    except OSError:
        pass


def release_lock():
    try:
        os.remove(LOCK)
    except OSError:
        pass


def wslpath(p):
    """Windows path -> WSL path. RAW, no quoting — see shq()."""
    return p.replace("C:\\", "/mnt/c/").replace("\\", "/")


def shq(p):
    """WSL path, shell-quoted for interpolation into a `bash -lc` string.

    Event names contain spaces ("The Big 5 UAE"), so an unquoted path splits
    into several argv entries and the CLI rejects the call with "too many
    arguments". Kept SEPARATE from wslpath because curl's config file does its
    own quoting (`output = "..."`): feeding it a pre-quoted path writes to a
    filename containing literal quote characters, which fails as curl error 23.
    Both bugs happened, in that order.
    """
    return "'" + wslpath(p) + "'"


def slug(s):
    return "".join(c if c.isalnum() or c in " -_" else "_" for c in s).strip()


def scope(args):
    out = []
    for kind in ("exhibitors", "sponsors"):
        mf = os.path.join(SCRIPT_DIR, f"highways_ce_{kind}_manifest.json")
        if not os.path.exists(mf):
            continue
        for event, rec in state_io.load_json(mf).items():
            if event in A.SKIP_EVENTS:
                continue
            if args.table and rec["table_name"] != args.table:
                continue
            out.append(dict(event=event, table=rec["table_name"],
                            table_id=rec["table_id"], rows=rec.get("rows") or 0))
    # Smallest first — a regression surfaces on a 13-row table, not a 637-row one.
    out.sort(key=lambda r: (r["rows"], r["event"]))
    if args.only:
        want = {o.lower() for o in args.only}
        out = [r for r in out if r["event"].lower() in want]
    if args.after:
        names = [r["event"] for r in out]
        if args.after not in names:
            raise SystemExit(f"--after: {args.after!r} not in scope")
        cut = max(i for i, n in enumerate(names) if n == args.after)
        out = out[cut + 1:]
    if args.shards:
        out = [r for i, r in enumerate(out) if i % args.shards == args.shard]
    return out


def key(rec):
    return f"{rec['event']}|{rec['table']}"


def results_path(rec):
    return os.path.join(RESULT_DIR, f"{slug(rec['event'])}__{rec['table']}.jsonl")


def build_feed(rec):
    """Write the eligible rows for one table as bulk JSONL. Returns the count."""
    cols = A.clay(f"tables columns list {rec['table_id']}")["data"]
    cmap = {c["id"]: c["name"] for c in cols}
    rows, cursor = [], None
    while True:
        cmd = f"tables rows list {rec['table_id']} --limit 100" + (
            f" --cursor {cursor}" if cursor else "")
        d = A.clay(cmd)
        for r in d.get("data", []):
            vals = {}
            for cid, cell in (r.get("cells") or {}).items():
                nm = cmap.get(cid)
                if nm in FEED_KEYS:
                    v = cell.get("value") if cell.get("status") == "success" else None
                    vals[nm] = "" if v is None else str(v)
            inputs = {k: vals.get(k, "") for k in FEED_KEYS}
            # The gate, applied before we spend anything.
            if inputs["Domain"].strip() and A.is_new(inputs["Is New"]):
                rows.append({"id": f"{rec['table']}:{r['id']}", "inputs": inputs})
        cursor = d.get("cursor")
        if not cursor:
            break
    os.makedirs(FEED_DIR, exist_ok=True)
    path = os.path.join(FEED_DIR, f"{slug(rec['event'])}__{rec['table']}.jsonl")
    with open(path, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    return path, len(rows)


def start_bulk(feed_path):
    """Fire a bulk run and return its id WITHOUT waiting.

    Bulk runs execute server-side; holding a local process open just to poll
    them is what makes this pass fragile here — the environment kills long
    tasks under memory pressure every few minutes, and each kill loses whatever
    the poll loop was waiting on. Starting is one call, so `--start-only` can
    launch the whole fleet in a couple of minutes and `--collect` can gather the
    results afterwards, in any number of short sessions.

    The caller MUST persist the returned id before doing anything else: a run
    that was started but whose id was never recorded is invisible to `--collect`
    and would be re-run — and re-charged — on the next pass.
    """
    r = subprocess.run(
        ["wsl", "bash", "-lc",
         f"clay routines runs start {ROUTINE} --bulk {shq(feed_path)}"],
        capture_output=True, env=ENV)
    if r.returncode != 0:
        raise RuntimeError(f"bulk start failed: {r.stderr.decode('utf-8','replace')[:300]}")
    return json.loads(r.stdout.decode("utf-8", "replace"))["routineRunId"]


def fetch_run(rid):
    g = subprocess.run(["wsl", "bash", "-lc", f"clay routines runs get {rid} --bulk"],
                       capture_output=True, env=ENV)
    if g.returncode != 0:
        return None
    return json.loads(g.stdout.decode("utf-8", "replace"))


def run_bulk(feed_path, poll_seconds=900):
    """Start the bulk run and wait. Returns the completed run payload."""
    r = subprocess.run(
        ["wsl", "bash", "-lc",
         f"clay routines runs start {ROUTINE} --bulk {shq(feed_path)}"],
        capture_output=True, env=ENV)
    if r.returncode != 0:
        raise RuntimeError(f"bulk start failed: {r.stderr.decode('utf-8','replace')[:300]}")
    started = json.loads(r.stdout.decode("utf-8", "replace"))
    rid = started["routineRunId"]
    print(f"    run {rid}", flush=True)

    deadline = time.time() + poll_seconds
    while time.time() < deadline:
        g = subprocess.run(
            ["wsl", "bash", "-lc", f"clay routines runs get {rid} --bulk"],
            capture_output=True, env=ENV)
        if g.returncode == 0:
            d = json.loads(g.stdout.decode("utf-8", "replace"))
            if d.get("status") in ("complete", "validation_failed", "processing_failed"):
                return rid, d
            print(f"    {d.get('finished', 0)}/{d.get('total', '?')}", flush=True)
        heartbeat()
        time.sleep(15)
    raise RuntimeError(f"run {rid} did not finish within {poll_seconds}s")


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
        raise RuntimeError(f"download failed: {r.stderr.decode('utf-8','replace')[:200]}")
    return sum(1 for line in open(dest, encoding="utf-8") if line.strip())


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--table", choices=("exhibitors_normalized", "sponsors_normalized"))
    ap.add_argument("--only", action="append", default=[])
    ap.add_argument("--after")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--shards", type=int)
    ap.add_argument("--shard", type=int, default=0)
    ap.add_argument("--list", action="store_true", help="scope only, no spend")
    ap.add_argument("--dry-run", action="store_true",
                    help="build feeds and report eligible counts, start nothing")
    ap.add_argument("--force-unlock", action="store_true",
                    help="take the lock even if another worker appears live")
    ap.add_argument("--start-only", action="store_true",
                    help="fire every pending bulk run and record its id; do not wait")
    ap.add_argument("--collect", action="store_true",
                    help="download results for runs already started")
    args = ap.parse_args()
    if args.shards and not (0 <= args.shard < args.shards):
        raise SystemExit("--shard must be in [0, --shards)")

    if not (args.list or args.dry_run):
        take_lock(force=args.force_unlock)

    state = state_io.load_json(STATE)
    todo = scope(args)

    def done(rec):
        """Skip only what is provably finished.

        Normally that means state says done AND the results file is on disk —
        both, because a state file that outlived its results would mark work
        complete that cannot be exported. The exception is a table with nothing
        eligible: it produces no results file by definition, so requiring one
        would make it re-read on every relaunch and never settle.
        """
        st = state.get(key(rec), {})
        if st.get("status") in ("started", "starting"):
            # "starting" means a run may be live but unrecorded — never re-fire
            # it automatically; reconcile by hand.
            return True
        if st.get("status") != "done":
            return False
        return st.get("eligible") == 0 or os.path.exists(results_path(rec))

    pending = [r for r in todo if not done(r)]
    if args.limit:
        pending = pending[:args.limit]

    if args.list:
        print(f"{len(todo)} tables in scope, {len(pending)} pending\n")
        print(f"{'event':40} {'table':10} {'rows':>6}  state")
        for r in todo:
            st = state.get(key(r), {})
            mark = ">" if r in pending else " "
            print(f"{mark}{r['event'][:38]:39} {r['table'][:9]:10} {r['rows']:6}  "
                  f"{st.get('status', '-')}"
                  + (f" ({st.get('classified')} classified)" if st.get("classified") else ""))
        return

    # ---- collect mode: gather results for runs already started --------------
    if args.collect:
        started = {k: v for k, v in state.items() if v.get("status") == "started"}
        print(f"{len(started)} run(s) awaiting collection\n")
        for k, st in sorted(started.items()):
            ev, tname = k.split("|", 1)
            rec = dict(event=ev, table=tname)
            d = fetch_run(st["routineRunId"])
            if d is None:
                print(f"  {k[:50]:52} read failed, left as started")
                continue
            if d.get("status") != "complete":
                print(f"  {k[:50]:52} {d.get('status')} "
                      f"{d.get('finished', '?')}/{d.get('total', '?')}")
                continue
            try:
                got = download(d, results_path(rec))
            except Exception as e:
                print(f"  {k[:50]:52} download failed: {str(e)[:70]}")
                continue
            state[k] = {**st, "status": "done", "classified": got}
            state_io.save_json(STATE, state)
            heartbeat()
            print(f"  {k[:50]:52} downloaded {got}")
        release_lock()
        return

    print(f"{len(pending)} table(s) to process\n")
    for rec in pending:
        print(f"=== {rec['event']} / {rec['table']} ({rec['rows']} rows) ===", flush=True)
        try:
            feed, n = build_feed(rec)
            print(f"    eligible: {n}", flush=True)
            if n == 0:
                state[key(rec)] = {"status": "done", "eligible": 0, "classified": 0,
                                   "note": "nothing eligible"}
                state_io.save_json(STATE, state)
                heartbeat()
                print("    nothing to do", flush=True)
                continue
            if args.dry_run:
                print(f"    --dry-run: would spend ~{n} action + ~{n} data credits",
                      flush=True)
                continue
            if args.start_only:
                # WRITE INTENT FIRST. Recording the id straight after the start
                # call still leaves a window: if the process is killed between
                # the call returning and the save, the run is live and charged
                # but invisible, and the next pass starts it again. That window
                # cost 114 double-charged rows across 6 tables on 2026-09-12.
                # A "starting" row with no id is the signal to reconcile by hand
                # (find the recent run in Clay) rather than blindly re-run.
                state[key(rec)] = {"status": "starting", "eligible": n}
                state_io.save_json(STATE, state)
                rid = start_bulk(feed)
                state[key(rec)] = {"status": "started", "eligible": n,
                                   "routineRunId": rid}
                state_io.save_json(STATE, state)
                heartbeat()
                print(f"    started {rid}", flush=True)
                continue
            rid, payload = run_bulk(feed)
            if payload.get("status") != "complete":
                raise RuntimeError(f"run ended {payload.get('status')}")
            got = download(payload, results_path(rec))
            state[key(rec)] = {"status": "done", "eligible": n, "classified": got,
                               "routineRunId": rid}
            state_io.save_json(STATE, state)
            heartbeat()
            print(f"    downloaded {got} results", flush=True)
        except Exception as e:
            # NOT recorded as done — a relaunch retries this table. The bulk run
            # may still have completed and spent; the run id is in the log above.
            print(f"    FAILED: {str(e)[:250]}", flush=True)
            break

    print("\nnext: python export_ce_classification.py --event <name> "
          "--results ce_classification_results/*.jsonl")


if __name__ == "__main__":
    main()
