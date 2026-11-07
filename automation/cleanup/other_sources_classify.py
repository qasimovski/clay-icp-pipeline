"""Classify the Other Sources companies with the Highways Registrar (WSL).

    ALL_SOURCES_DEDUPED -> eligible rows -> bulk JSONL -> the CE classification
    workflow -> Side + Classification

WHY THE CE WORKFLOW, UNCHANGED. Other Sources is the Competitive Events problem
again: 3,047 companies scraped from 16 directories with nothing on the table
saying what any of them IS. `docs/WORKFLOWS_ROUTE.md` §8 calls this out as the
one remaining scope that genuinely needs the classifier — unlike Products &
Services, where the workbook name is the category. So this reuses
`workflow:wf_...` as-is rather than building anything new; only the feed differs.

WHAT IS NOT AN INPUT. The table's `Category` column holds the DIRECTORY's own
membership label ("Architects", "Tier 3", "HERS Registered Organisations",
"Corporate Members"), 487 distinct values, many of them membership tiers with no
ICP meaning. Per the user (2026-09-14) it is deliberately NOT fed to the
classifier and NOT used for targeting — it travels through to the export as
provenance only. The Registrar classifies from Description, Primary Offerings
and the domain, exactly as it does for CE.

SCOPE IS `Is New` AND `Domain`, and both gates are already satisfied on this
table: the user ran the Supabase ledger column and filtered the view, and every
one of the 1,204 `Is New` rows carries a Domain. The filter is re-applied here
anyway — skip-by-absence beats skip-by-rule, and a feed built from a stale cache
must not quietly widen.

MONEY. 1 action + 1 data credit per row. A bare invocation SPENDS; `--plan` and
`--dry-run` do not, and `--pilot N` runs N rows inline first.

    python3 other_sources_classify.py --plan
    python3 other_sources_classify.py --pilot 25
    python3 other_sources_classify.py --start-only
    python3 other_sources_classify.py --collect
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

import clay_bin      # noqa: E402
import state_io      # noqa: E402

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


TABLE = _id("highways_other_sources_table_id")   # ALL_SOURCES_DEDUPED
ROUTINE = "workflow:" + _id("highways_ce_workflow_id")
CACHE = os.path.join(SCRIPT_DIR, "other_sources_rows.json")
FEED_DIR = os.path.join(SCRIPT_DIR, "other_sources_feeds")
RESULT_DIR = os.path.join(SCRIPT_DIR, "other_sources_results")
STATE = os.path.join(SCRIPT_DIR, "other_sources_classify_state.json")
CHUNK = 250

# Table column -> the workflow trigger input it feeds. The trigger's names are
# the CE table's, and they are NOT renamed: the workflow is live and shared,
# and CLAUDE.md forbids renaming anything persisted or external.
#
# `Description (2)` is empty here. Build Company Record does `enriched or
# scraped`, so this table's single `Description` lands in the `Description`
# slot and is picked up by the fallback. `Event` carries Source Name, which is
# the nearest thing Other Sources has to provenance.
FIELDS = {"Company Name": "Company Name", "Domain": "Domain",
          "Description": "Description", "Primary Offerings": "Primary Offerings"}


# This pass runs INSIDE WSL (it reads the table through clay_bin), so it calls
# the clay binary directly rather than shelling out to `wsl` the way the
# Windows-side Playwright rollouts do. Calling `wsl` from inside wsl is a
# FileNotFoundError, not a no-op.


def rows():
    """The table, from cache; read once and reused."""
    if os.path.exists(CACHE):
        return json.load(open(CACHE, encoding="utf-8"))
    import export_ps_people as E
    data = E.read_table(clay_bin.pick(), TABLE)
    json.dump(data, open(CACHE, "w", encoding="utf-8"), ensure_ascii=False)
    return data


def eligible(data):
    """Is New AND Domain. Both already true on this table; re-checked anyway."""
    out = []
    for r in data:
        if str(r.get("Is New", "")).strip().lower() != "true":
            continue
        if not str(r.get("Domain", "")).strip():
            continue
        out.append(r)
    return out


def item(r):
    inputs = {dst: str(r.get(src, "") or "").strip()
              for src, dst in FIELDS.items()}
    inputs["Description (2)"] = ""
    inputs["Is New"] = "True"
    inputs["Event"] = str(r.get("Source Name", "") or "").strip()
    inputs["Year"] = ""
    return {"id": str(r.get("Domain", "")).strip(), "inputs": inputs}


def clay_cli(args, tries=6):
    """One clay call with backoff. `args` is a tuple, never a shell string."""
    delay = 2
    for attempt in range(tries):
        ok, d, err = clay_bin.run(clay_bin.pick(), tuple(args))
        if ok:
            return d
        if d is not None:
            raise RuntimeError(f"clay {' '.join(args)}: {err}")
        if attempt < tries - 1:
            time.sleep(delay)
            delay = min(delay * 2, 64)
    raise RuntimeError(f"clay {' '.join(args)} failed after {tries} tries")


def download(payload, dest):
    """Fetch the signed result file NOW — the URL expires in ~15 minutes."""
    url = payload.get("resultUrl")
    if not url:
        raise RuntimeError("completed run carried no resultUrl")
    os.makedirs(RESULT_DIR, exist_ok=True)
    cfg = dest + ".curlrc"
    with open(cfg, "w", encoding="utf-8") as fh:
        fh.write(f'url = "{url}"\noutput = "{dest}"\n')
    r = subprocess.run(["curl", "-sS", "-K", cfg], capture_output=True)
    os.remove(cfg)
    if r.returncode != 0 or not os.path.exists(dest) or not os.path.getsize(dest):
        raise RuntimeError("download failed: "
                           + r.stderr.decode("utf-8", "replace")[:200])
    return sum(1 for line in open(dest, encoding="utf-8") if line.strip())


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--plan", action="store_true", help="scope only, no spend")
    ap.add_argument("--dry-run", action="store_true", help="build feeds, start nothing")
    ap.add_argument("--pilot", type=int, help="run N rows inline first (SPENDS)")
    ap.add_argument("--start-only", action="store_true")
    ap.add_argument("--collect", action="store_true")
    ap.add_argument("--limit", type=int)
    args = ap.parse_args()

    data = rows()
    eli = eligible(data)
    print(f"table rows {len(data):,}   eligible (Is New AND Domain) {len(eli):,}")
    with_text = sum(1 for r in eli if r.get("Description", "").strip()
                    or r.get("Primary Offerings", "").strip())
    print(f"  with Description or Primary Offerings: {with_text:,}"
          f"   (the rest classify from the domain alone)")
    if args.plan:
        return 0

    os.makedirs(FEED_DIR, exist_ok=True)
    chunks = []
    for i in range(0, len(eli), CHUNK):
        p = os.path.join(FEED_DIR, f"chunk_{i // CHUNK:03d}.jsonl")
        with open(p, "w", encoding="utf-8", newline="\n") as fh:
            for r in eli[i:i + CHUNK]:
                fh.write(json.dumps(item(r), ensure_ascii=False) + "\n")
        chunks.append(p)
    print(f"{len(chunks)} chunk(s) of up to {CHUNK}")

    if args.pilot:
        sample = [item(r) for r in eli[:args.pilot]]
        fp = os.path.join(FEED_DIR, "pilot.json")
        json.dump({"items": sample}, open(fp, "w", encoding="utf-8"),
                  ensure_ascii=False)
        rid = clay_cli(("routines", "runs", "start", ROUTINE,
                        "--input", fp))["routineRunId"]
        print(f"pilot {len(sample)} rows -> {rid}")
        return 0

    if args.dry_run:
        return 0

    state = state_io.load_json(STATE)
    todo = chunks[:args.limit] if args.limit else chunks
    for path in todo:
        key = os.path.basename(path)[:-6]
        st = state.get(key) or {}
        dest = os.path.join(RESULT_DIR, key + ".jsonl")
        if st.get("status") == "done" and os.path.exists(dest):
            continue
        rid = st.get("run_id")
        n = sum(1 for _ in open(path, encoding="utf-8"))
        if not rid and not args.collect:
            # Intent-first: record the spend BEFORE making it, so a kill
            # between start and the id write shows as `starting` rather than
            # looking like untouched work.
            state[key] = {"status": "starting", "rows": n}
            state_io.save_json(STATE, state)
            rid = clay_cli(("routines", "runs", "start", ROUTINE,
                            "--bulk", path))["routineRunId"]
            state[key] = {"status": "running", "rows": n, "run_id": rid}
            state_io.save_json(STATE, state)
            print(f"{key}  {n} rows -> {rid}")
        if not rid or args.start_only:
            continue
        d = {}
        for _ in range(180):
            d = clay_cli(("routines", "runs", "get", rid, "--bulk"))
            if d.get("status") in ("complete", "validation_failed",
                                   "processing_failed"):
                break
            time.sleep(20)
        if d.get("status") != "complete":
            state[key] = {"status": "failed", "run_id": rid, "why": d.get("status")}
            state_io.save_json(STATE, state)
            print(f"   !! {key}: {d.get('status')}")
            continue
        got = download(d, dest)
        state[key] = {"status": "done", "rows": n, "run_id": rid, "results": got}
        state_io.save_json(STATE, state)
        print(f"   {key}: {got} results -> {dest}")

    done = sum(1 for v in state.values() if v.get("status") == "done")
    print(f"\nchunks done: {done}/{len(chunks)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
