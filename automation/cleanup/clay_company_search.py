"""Run Clay company searches from the CLI (`clay searches query-mode`) and keep every
page on disk. The no-UI replacement for Find companies -> Query mode when the
deliverable is a CSV, not a Clay table (first used for the Solar buy side,
2026-11-05).

Runs in WSL (the `clay` CLI is Linux-only on this machine) with the stdlib only.
The plan comes from ps_query_config.py, which runs on Windows:

    python automation/cleanup/ps_query_config.py --icp solar_buyside --plan-json <work>/plan.json
    wsl -d Ubuntu -- python3 /mnt/c/.../clay_company_search.py --work <work> --validate
    wsl ... --work <work> --sample 25            # first 25 of each search, for a precision check
    wsl ... --work <work> --run [--only NAME]    # every result, paged 500 at a time

Modes, from free to spending:
  --list       plan only, nothing sent to Clay.
  --validate   `create` each query: Clay parses it and returns a search id. No
               results are fetched, so nothing counts against the result quota.
  --sample N   create + ONE page of N results per search -> samples/<slug>.jsonl.
  --run        create + page until Clay says no more -> results/<slug>.jsonl.

Money and quota rules (from `clay searches query-mode run --help`):
  * Every page fetched counts against the workspace's period result quota, and
    may be charged. A retry after a server_error can re-serve, re-charge and
    re-count the same page, so a page is retried once only, and only when the
    failed call returned no data.
  * A search is a forward-only, server-side iterator with no cursor. So each page
    is appended to disk and the state saved BEFORE the next call; a resumed run
    continues the SAME search id. If that id has expired (not_found), the run
    stops: re-creating it would re-pay every page already on disk. Pass
    --restart NAME to accept that knowingly.
  * quota_exceeded stops the whole run; it is never retried.
  * The credit balance is read before and after each search, so the cost of a
    search is measured, not assumed.

State: <work>/state.json, via state_io (atomic, and fails loud if corrupt).
"""

import argparse
import datetime as dt
import glob
import json
import os
import re
import subprocess
import sys
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
import state_io  # noqa: E402

PAGE = 500  # the CLI's maximum per call


class ClayError(Exception):
    def __init__(self, code, message, data_returned=False):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.data_returned = data_returned


def clay_bin():
    if os.environ.get("CLAY_BIN"):
        return os.environ["CLAY_BIN"]
    found = sorted(glob.glob(os.path.expanduser("~/.config/clay/bin/clay-*-linux-x64")),
                   key=lambda p: [int(x) for x in re.findall(r"\d+", os.path.basename(p))])
    if not found:
        raise SystemExit("no clay binary under ~/.config/clay/bin (set CLAY_BIN)")
    return found[-1]


def clay(*args):
    """Run the CLI with a clean PATH (WSL's Windows-interop PATH breaks it)."""
    p = subprocess.run([clay_bin(), *args], capture_output=True, text=True,
                       env={"PATH": "/usr/bin:/bin", "HOME": os.path.expanduser("~")})
    if p.returncode != 0:
        try:
            err = json.loads(p.stderr)["error"]
            raise ClayError(err.get("code", "unknown"), err.get("message", ""))
        except (ValueError, KeyError, TypeError):
            raise ClayError("unknown", (p.stderr or p.stdout).strip()[:500])
    return json.loads(p.stdout)


def balance():
    try:
        return clay("credits", "balance").get("balance")
    except ClayError:
        return None


def slug(name):
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def append_jsonl(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


# Clay's search backend rejects heavy queries INTERMITTENTLY: the same query
# fails with one of these and succeeds a minute later (seen 2026-11-05 on 5 of
# 26 Solar buy-side searches; each passed on a retry, none was ever charged).
# They come back as validation_error, so they are matched on the message.
TRANSIENT_MESSAGES = ("too large to run", "exceeded the maximum memory limit")  # Bitmap create/append
BACKOFF = (15, 45, 90, 180)


def is_transient(e):
    return e.code == "server_error" or (
        e.code == "validation_error" and any(m in str(e) for m in TRANSIENT_MESSAGES))


def fetch_page(search_id, limit):
    """One page. A failed call returned no data, so retrying it fetches the
    same page rather than skipping one; Clay notes a retry can re-count it
    against the quota (searches have cost 0 credits so far)."""
    for wait in (*BACKOFF, None):
        try:
            return clay("searches", "query-mode", "run", search_id, "--limit", str(limit))
        except ClayError as e:
            if wait is None or not is_transient(e):
                raise
            print(f"    transient ({str(e)[:60]}), retrying in {wait}s", flush=True)
            time.sleep(wait)
    raise AssertionError("unreachable")


def create(query):
    out = clay("searches", "query-mode", "create", "--query", query)
    return out["searchId"]


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--work", required=True, help="run folder holding plan.json")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--list", action="store_true")
    mode.add_argument("--validate", action="store_true")
    mode.add_argument("--sample", type=int, metavar="N")
    mode.add_argument("--run", action="store_true")
    ap.add_argument("--only", action="append", default=[], help="search name (repeatable)")
    ap.add_argument("--restart", action="append", default=[],
                    help="search name whose expired search may be re-created (re-pays its pages)")
    args = ap.parse_args()

    plan = state_io.load_json(os.path.join(args.work, "plan.json"))
    if not plan:
        raise SystemExit(f"no plan.json in {args.work} (ps_query_config.py --plan-json)")
    searches = plan["searches"]
    if args.only:
        unknown = set(args.only) - {s["name"] for s in searches}
        if unknown:
            raise SystemExit(f"--only names not in the plan: {sorted(unknown)}")
        searches = [s for s in searches if s["name"] in args.only]

    state_path = os.path.join(args.work, "state.json")
    state = state_io.load_json(state_path, default={})

    if args.list:
        for s in searches:
            st = state.get(s["name"], {})
            print(f"{s['group']:<34} {s['name']:<52} "
                  f"{st.get('run', {}).get('status', '-'):<9} {st.get('run', {}).get('rows', '')}")
        return 0

    if args.validate:
        bad = 0
        for s in searches:
            try:
                sid = create(s["query"])
                state.setdefault(s["name"], {})["validated"] = {"at": now(), "searchId": sid}
                print(f"OK    {s['name']}")
            except ClayError as e:
                bad += 1
                state.setdefault(s["name"], {})["validated"] = {"at": now(), "error": str(e)}
                print(f"FAIL  {s['name']}: {e}")
            state_io.save_json(state_path, state)
        return 1 if bad else 0

    if args.sample:
        for s in searches:
            st = state.setdefault(s["name"], {})
            prev = st.get("sample", {})
            # Re-sample when the query changed since the last sample (a config edit),
            # never otherwise: a sample costs quota.
            if (prev.get("status") == "done" and prev.get("n") == args.sample
                    and prev.get("query", s["query"]) == s["query"]):
                print(f"skip  {s['name']} (sampled)")
                continue
            before = balance()
            sid = create(s["query"])
            try:
                page = fetch_page(sid, args.sample)
            except ClayError as e:
                st["sample"] = {"status": "error", "error": str(e), "at": now()}
                state_io.save_json(state_path, state)
                if e.code == "quota_exceeded":
                    raise SystemExit(f"quota_exceeded on {s['name']}; stopping")
                print(f"FAIL  {s['name']}: {e}")
                continue
            path = os.path.join(args.work, "samples", slug(s["name"]) + ".jsonl")
            if os.path.exists(path):
                os.remove(path)
            append_jsonl(path, page.get("data", []))
            after = balance()
            st["sample"] = {"status": "done", "n": args.sample, "query": s["query"],
                            "rows": len(page.get("data", [])),
                            "hasMore": page.get("hasMore"), "searchId": sid, "at": now(),
                            "credits_before": before, "credits_after": after,
                            "periodQuota": page.get("periodQuota")}
            state_io.save_json(state_path, state)
            print(f"done  {s['name']}: {len(page.get('data', []))} rows, more={page.get('hasMore')}, "
                  f"credits {before} -> {after}, quota {page.get('periodQuota')}")
        return 0

    # --run
    for s in searches:
        st = state.setdefault(s["name"], {})
        run = st.get("run") or {}
        if run.get("status") == "done" and s["name"] not in args.restart:
            if run.get("query", s["query"]) != s["query"]:
                print(f"WARN  {s['name']}: done with an OLDER query; --restart it to re-run")
            print(f"skip  {s['name']} (done, {run.get('rows')} rows)")
            continue
        path = os.path.join(args.work, "results", slug(s["name"]) + ".jsonl")
        if run.get("searchId") and s["name"] not in args.restart:
            sid = run["searchId"]
            print(f"resume {s['name']} at page {run.get('pages', 0)} ({run.get('rows', 0)} rows)")
        else:
            if run.get("rows") and s["name"] in args.restart and os.path.exists(path):
                os.replace(path, path + f".restarted-{dt.datetime.now():%Y%m%d%H%M%S}")
            sid = create(s["query"])
            run = {"status": "running", "searchId": sid, "pages": 0, "rows": 0, "query": s["query"],
                   "started": now(), "credits_before": balance()}
            st["run"] = run
            state_io.save_json(state_path, state)
        while True:
            try:
                page = fetch_page(sid, PAGE)
            except ClayError as e:
                run["status"] = "error"
                run["error"] = str(e)
                state_io.save_json(state_path, state)
                if e.code == "not_found":
                    raise SystemExit(f"{s['name']}: search {sid} expired with {run['rows']} rows "
                                     f"on disk. Re-creating re-pays them: --restart \"{s['name']}\"")
                print(f"STOP  {s['name']}: {e}; state saved, re-run --run to resume", flush=True)
                raise SystemExit(1)
            data = page.get("data", [])
            append_jsonl(path, data)
            run["pages"] += 1
            run["rows"] += len(data)
            run["periodQuota"] = page.get("periodQuota")
            run["status"] = "running"
            if not page.get("hasMore"):
                run["status"] = "done"
                run["exhaustionReason"] = page.get("exhaustionReason")
                run["finished"] = now()
                run["credits_after"] = balance()
            state_io.save_json(state_path, state)
            print(f"  {s['name']}: page {run['pages']} +{len(data)} = {run['rows']}", flush=True)
            if run["status"] == "done":
                break
        print(f"done  {s['name']}: {run['rows']} rows ({run.get('exhaustionReason')}), "
              f"credits {run.get('credits_before')} -> {run.get('credits_after')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
