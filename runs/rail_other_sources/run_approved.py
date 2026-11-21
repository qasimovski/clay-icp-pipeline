"""Drive one APPROVED Other Sources bulk run through Clay's per-chunk approval gate.

    python run_approved.py finder_rest
    python run_approved.py classify_pilot
    python run_approved.py <job> --retry-failed

Each job below records the user's OK (date + words). The script approves ONLY requests that are (a) awaiting approval
in this job's state.json, (b) for this job's workflow, (c) action routines.run-batch, and (d) for exactly as many
items as that chunk file holds. No standing rule is ever saved (no --always-allow). Then bulk_run.py is re-run until
every chunk has its result file. A job not listed here cannot be approved by this script.
"""
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
S = r"C:\Users\qasim\terrapinn\clay-icp-pipeline\.claude\skills\event-icp-clay-workflow\scripts"
FINDER = os.environ.get("RAIL_FINDER_WF", "")
CLASSIFIER = os.environ.get("RAIL_CLASSIFIER_WF", "")
F_IN, C_IN = "Company Name,Event,Country,Description", "Company Name,Domain,Description,Primary Offerings"
CE = os.path.join(HERE, "..")
PEOPLE = os.environ.get("RAIL_PEOPLE_WF", "")
P_IN = "Company Name,Domain,Side,Classification,Countries Override,Seniority Group,Fit,Events"
def _reuse_dirs():
    p = os.path.join(HERE, "people", "runs", "enrich_company_reuse_dirs.txt")
    return open(p).read().split() if os.path.exists(p) else []


JOBS = {
    # user, 21 Nov 2026: "Yes, approve each chunk of this job" (finder on the remaining 1,818 names, 8 chunks)
    "finder_rest": dict(wf=FINDER, dir="domain_finder", feed="feed_rest.csv", work="runs_rest", inputs=F_IN,
                        ids="Row ID", reuse=[os.path.join(CE, "domain_finder", "runs"), "runs_pilot"], parallel=4),
    # user, 21 Nov 2026: "Yes, approve this chunk" (classifier pilot, 250 domains)
    "classify_pilot": dict(wf=CLASSIFIER, dir="classify", feed="feed_pilot.csv", work="runs_pilot", inputs=C_IN,
                           ids="Key", reuse=[os.path.join(CE, "classify", "runs"),
                                             os.path.join(CE, "classify", "runs_wave2")], parallel=1),
    # user, 21 Nov 2026: "Yes, approve each chunk" (classifier on the other 1,976 wave-1 domains, 8 chunks)
    "classify_rest": dict(wf=CLASSIFIER, dir="classify", feed="feed_rest.csv", work="runs_rest", inputs=C_IN,
                          ids="Key", reuse=[os.path.join(CE, "classify", "runs"), os.path.join(CE, "classify", "runs_wave2"),
                                            "runs_pilot"], parallel=4),
    # user, 21 Nov 2026: "Yes, approve each chunk" (wave 2 = 925 finder-found domains, 4 chunks)
    "classify_wave2": dict(wf=CLASSIFIER, dir="classify", feed="feed_wave2.csv", work="runs_wave2", inputs=C_IN,
                           ids="Key", reuse=[os.path.join(CE, "classify", "runs"), os.path.join(CE, "classify", "runs_wave2"),
                                             "runs_pilot", "runs_rest"], parallel=4),
    # user, 21 Nov 2026: "Yes, approve these runs" - people pilot (first 250 of 3,150, CE workflow v2), classify the
    # 9 portal bodies (9 rows), and a <=4-row test of whether Find People resolves their subdomains
    "people_pilot": dict(wf=PEOPLE, dir="people", feed="people_feed_pilot.csv", work="runs/people", inputs=P_IN,
                         ids="Row ID", reuse=[os.path.join(CE, "people", "runs", "people")], parallel=1),
    "classify_portal": dict(wf=CLASSIFIER, dir="classify", feed="feed_portal.csv", work="runs_portal", inputs=C_IN,
                            ids="Key", reuse=[], parallel=1),
    "people_portal_test": dict(wf=PEOPLE, dir="people", feed="people_feed_portal_test.csv", work="runs/portal_test",
                               inputs=P_IN, ids="Row ID", reuse=[], parallel=1),
    # user, 21 Nov 2026: "Yes to both" - B1 (heads + managers) re-search on the 231 capped searches, fresh work folder
    "people_b1": dict(wf=PEOPLE, dir="people", feed="full/people_feed - sen B1.csv", work="runs/sen_b1", inputs=P_IN,
                      ids="Row ID", reuse=[os.path.join(CE, "people", "runs", "sen_b1")], parallel=1),
    # user, 21 Nov 2026: "Yes, approve each chunk. Also use blitz for companies that come out with no data"
    # (Enrich Company, 1,009 domains, 72 reused from 22 earlier enrich runs, 937 sent, 4 chunks)
    "enrich_company": dict(wf=os.environ.get("ENRICH_COMPANY_WF", ""), dir="people", feed="final/company_feed.csv",
                           work="runs/enrich_company", inputs="Company Domain", ids="Company Domain",
                           reuse=_reuse_dirs(),
                           parallel=2),
}


def clay(args):
    out = subprocess.run(["wsl", "bash", "-lc", "clay " + " ".join(args)], capture_output=True, text=True,
                         encoding="utf-8", errors="replace").stdout
    try:
        return json.loads(out)
    except ValueError:
        return {"raw": out[:300]}


def chunk_ids(work):
    ids = set()
    for f in os.listdir(os.path.join(work, "feeds")):
        for line in open(os.path.join(work, "feeds", f), encoding="utf-8"):
            ids.add(json.loads(line)["id"])
    return ids


def feed_ids(job, base):
    import csv
    cols = job["ids"].split(",")
    with open(os.path.join(base, job["feed"]), encoding="utf-8-sig", newline="") as fh:
        return {"|".join(r[c] for c in cols) for r in csv.DictReader(fh)}


def check_scope(job, base):
    """21 Nov 2026 incident: bulk_run reuses chunk files already in <work>/feeds (written by an earlier --plan on a
    BIGGER feed) and ignores --feed, so a 250-row pilot ran all 3,150 rows. Refuse unless the chunk files hold
    exactly the approved feed's ids."""
    work = os.path.join(base, job["work"])
    if not os.path.isdir(os.path.join(work, "feeds")):
        return
    have, want = chunk_ids(work), feed_ids(job, base)
    if not have <= want:      # a subset is fine (rows --reuse-from copied are never chunked); any extra id is not
        raise SystemExit(f"STOP: {work}/feeds holds {len(have)} ids, the approved feed {job['feed']} has {len(want)} "
                         f"({len(have - want)} not approved). Nothing approved.")


def approve_pending(job, base):
    check_scope(job, base)
    work = os.path.join(base, job["work"])
    state = json.load(open(os.path.join(work, "state.json"), encoding="utf-8"))
    n = 0
    for name, e in state.items():
        if not isinstance(e, dict) or e.get("status") != "awaiting_approval":
            continue
        rows = sum(1 for _ in open(os.path.join(work, "feeds", name), encoding="utf-8"))
        ap = clay(["approvals", "get", e["approval_id"]])
        disp = ap.get("display") or {}
        m = re.search(r"on (\d[\d,]*) items", disp.get("description") or "")
        items = int(m.group(1).replace(",", "")) if m else -1
        ok = (not ap.get("decision") and ap.get("action") == "routines.run-batch"
              and (disp.get("resource") or {}).get("id") == job["wf"] and items == rows)
        if not ok:
            print(f"{name}: NOT approving {e['approval_id']} (decision={ap.get('decision')} action={ap.get('action')} "
                  f"wf={(disp.get('resource') or {}).get('id')} items={items} chunk rows={rows})", flush=True)
            continue
        r = clay(["approvals", "approve", e["approval_id"]])
        print(f"{name}: approved {e['approval_id']} ({items} items) -> {r.get('decision')}", flush=True)
        n += 1
    return n


def main():
    job = JOBS[sys.argv[1]]
    extra = sys.argv[2:]
    base = os.path.join(HERE, job["dir"])
    cmd = [sys.executable, os.path.join(S, "bulk_run.py"), "--workflow", job["wf"], "--feed", job["feed"],
           "--work", job["work"], "--inputs", job["inputs"], "--id-cols", job["ids"], "--parallel",
           str(job["parallel"]), "--timeout", "1800", "--reuse-from", *job["reuse"]] + extra
    check_scope(job, base)      # before the first bulk_run call too (a fresh work folder is written from --feed)
    # 21 Nov 2026 incident: with --retry-failed every bulk_run call writes a NEW retry chunk for rows that keep failing
    # (government portals Clay rejects), and this loop approved 53 of them (318 rows). Now: in retry mode at most ONE
    # retry chunk is approved per invocation, then the flag is dropped so no further retry chunk is ever written.
    retry_mode = "--retry-failed" in cmd
    approved_retries = 0
    for rnd in range(60):
        if retry_mode and approved_retries >= 1 and "--retry-failed" in cmd:
            cmd.remove("--retry-failed")
        p = subprocess.run(cmd, cwd=base, capture_output=True, text=True, encoding="utf-8", errors="replace")
        print(p.stdout[-1500:], p.stderr[-800:], flush=True)
        st = json.load(open(os.path.join(base, job["work"], "state.json"), encoding="utf-8"))
        chunks = {k: v for k, v in st.items() if isinstance(v, dict)}
        waiting = [k for k, v in chunks.items() if v.get("status") == "awaiting_approval"]
        if not waiting and p.returncode == 0 and "rows to run 0" in p.stdout:
            print("DONE", flush=True)
            return
        if waiting:
            if retry_mode and approved_retries >= 1:
                raise SystemExit(f"STOP: retry mode already approved one retry chunk; not approving {waiting}")
            approved_retries += approve_pending(job, base)
        elif p.returncode != 0:
            raise SystemExit("bulk_run failed and nothing to approve - stop")
        time.sleep(5)


if __name__ == "__main__":
    main()
