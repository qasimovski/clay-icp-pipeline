"""Audit every Competitive Events table for classification eligibility.

Read-only and credit-free (`clay tables rows list` ignores the query quota).
Answers, per table, the only question that matters before a fleet run: how many
rows would actually reach the Claygent, and is a zero real or a symptom?

    eligible   Is New true AND Domain non-empty  -> these cost 1 action + 1 data
    not_new    ledger already claimed them       -> free, correctly skipped
    no_domain  ledger column never fired on them -> free, but see below

WHY THE SUSPECT FLAG EXISTS. The ledger column carries "Run only if Domain is
not empty", so a row with no Domain never got checked and `Is New` sits at its
unchecked default of FALSE. A table whose ledger column never ran at all is
therefore indistinguishable, by `Is New` alone, from a table that is genuinely
fully deduped: both read all-false. That is exactly how `Tolling & Road User
Charging` came to look finished while its HTTP API column had never fired, and
how it then produced a 0-row view and got silently skipped by the People pass.

So this flags any table with rows but zero eligible, and separates the two
causes by looking at whether the `HTTP API` cells are populated:

    LEDGER-NEVER-RAN   HTTP API empty on every row -> NOT deduped, needs the
                       ledger pass before classification means anything
    genuinely worked   HTTP API populated          -> fine, nothing to do

    python audit_ce_classification.py            # both table types
    python audit_ce_classification.py --table sponsors_normalized
    python audit_ce_classification.py --json out.json
"""

import argparse
import json
import os
import subprocess
import sys
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

import state_io  # noqa: E402

ENV = {**os.environ, "MSYS_NO_PATHCONV": "1"}
# Hand-imported sample with no Is New column; out of scope by name, the same way
# the extractor's DEFAULT_SKIP_WORKBOOKS excludes it.
SKIP_EVENTS = {"Highways UK 2025"}


class ClayReadError(RuntimeError):
    pass


def clay(args, tries=3):
    """Read from Clay, retrying transient failures, RAISING on persistent ones.

    The first version of this returned None on failure and every caller did
    `if not d: break`. A single flaky page then ended the paging loop and the
    table silently reported fewer rows than it has — Melbourne Build came back
    as 100 rows against a live rowCount of 208, and the fleet total was ~2,600
    rows short. An undercount here understates the cost of the whole run, so
    this fails loud instead. Same rule as state_io: never let "could not read"
    masquerade as "there was nothing there".
    """
    last = ""
    for _ in range(tries):
        r = subprocess.run(["wsl", "bash", "-lc", "clay " + args],
                           capture_output=True, env=ENV)
        if r.returncode == 0:
            try:
                return json.loads(r.stdout.decode("utf-8", "replace"))
            except json.JSONDecodeError as e:
                last = f"bad JSON: {e}"
        else:
            last = r.stderr.decode("utf-8", "replace")[:200]
        time.sleep(2)
    raise ClayReadError(f"clay {args}\n  {last}")


def is_new(v):
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() == "true"


def audit(tid):
    cd = clay(f"tables columns list {tid}")
    cmap = {c["id"]: c["name"] for c in cd["data"]}
    names = set(cmap.values())
    stats = dict(rows=0, eligible=0, not_new=0, no_domain=0,
                 http_filled=0, has_is_new="Is New" in names,
                 has_domain="Domain" in names)
    cursor = None
    while True:
        cmd = f"tables rows list {tid} --limit 100" + (f" --cursor {cursor}" if cursor else "")
        d = clay(cmd)
        for r in d.get("data", []):
            stats["rows"] += 1
            rec = {}
            for cid, cell in (r.get("cells") or {}).items():
                nm = cmap.get(cid)
                if nm in ("Is New", "Domain", "HTTP API"):
                    rec[nm] = cell.get("value") if cell.get("status") == "success" else None
            if rec.get("HTTP API") not in (None, "", [], {}):
                stats["http_filled"] += 1
            if not str(rec.get("Domain") or "").strip():
                stats["no_domain"] += 1
            elif is_new(rec.get("Is New")):
                stats["eligible"] += 1
            else:
                stats["not_new"] += 1
        cursor = d.get("cursor")
        if not cursor:
            break

    # SELF-CHECK. Clay's own rowCount is the authority; if paging disagrees we
    # read a partial table and every number below it is wrong.
    g = clay(f"tables get {tid}")
    declared = (g.get("data", g) or {}).get("rowCount")
    if declared is not None and declared != stats["rows"]:
        raise ClayReadError(
            f"table {tid}: paged {stats['rows']} rows but rowCount is "
            f"{declared} - partial read, refusing to report it")
    return stats


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--table", choices=("exhibitors_normalized", "sponsors_normalized"))
    ap.add_argument("--json", help="also write the raw audit here")
    args = ap.parse_args()

    scope = []
    for kind in ("exhibitors", "sponsors"):
        mf = os.path.join(SCRIPT_DIR, f"highways_ce_{kind}_manifest.json")
        if not os.path.exists(mf):
            continue
        for event, rec in state_io.load_json(mf).items():
            if event in SKIP_EVENTS:
                continue
            if args.table and rec["table_name"] != args.table:
                continue
            scope.append((event, rec["table_name"], rec["table_id"], rec.get("rows") or 0))
    scope.sort(key=lambda t: (t[3], t[0]))
    print(f"scope: {len(scope)} tables\n")

    hdr = f"{'event':40} {'table':10} {'rows':>5} {'elig':>5} {'notnew':>6} {'nodom':>5}  flag"
    print(hdr); print("-" * len(hdr))
    out, tot = {}, dict(rows=0, eligible=0, not_new=0, no_domain=0)
    suspects, failures = [], []
    for event, tname, tid, _ in scope:
        try:
            s = audit(tid)
        except ClayReadError as e:
            print(f"{event[:39]:40} {tname[:9]:10} {'':>5} {'':>5} {'':>6} {'':>5}  "
                  f"READ FAILED - {str(e)[:70]}")
            failures.append((event, tname, str(e)[:160]))
            continue
        # A table is only SUSPECT when it had keyable rows and still checked
        # none of them. If every row lacks a Domain the ledger was right not to
        # fire, and an empty HTTP API column means nothing.
        keyable = s["rows"] - s["no_domain"]
        flag = ""
        if not s["has_is_new"]:
            flag = "NO 'Is New' COLUMN"
        elif keyable > 0 and s["http_filled"] == 0:
            flag = f"LEDGER-NEVER-RAN ({keyable} keyable rows, 0 checked)"
        elif s["rows"] and s["eligible"] == 0:
            flag = ("no keyable rows" if keyable == 0
                    else "zero eligible (genuinely worked)")
        if flag.startswith(("LEDGER", "NO ")):
            suspects.append((event, tname, flag, s))
        for k in tot:
            tot[k] += s[k]
        out[f"{event}|{tname}"] = s
        print(f"{event[:39]:40} {tname[:9]:10} {s['rows']:5} {s['eligible']:5} "
              f"{s['not_new']:6} {s['no_domain']:5}  {flag}")

    print("\n" + "=" * 70)
    print(f"TOTAL rows      : {tot['rows']}")
    print(f"  eligible      : {tot['eligible']}   <- the only rows that cost anything")
    print(f"  already worked: {tot['not_new']}")
    print(f"  no domain     : {tot['no_domain']}")
    print(f"\nest. cost: ~{tot['eligible']} action + ~{tot['eligible']} data credits")
    if suspects:
        print(f"\nNEEDS ATTENTION ({len(suspects)}):")
        for e, t, f, s in suspects:
            print(f"   {e} / {t}: {f}  (rows={s['rows']}, HTTP API filled={s['http_filled']})")
    if failures:
        print(f"\nREAD FAILURES ({len(failures)}) - totals above are INCOMPLETE:")
        for e, t, msg in failures:
            print(f"   {e} / {t}: {msg}")
    if args.json:
        state_io.save_json(args.json, out)
        print(f"\nwrote {args.json}")


if __name__ == "__main__":
    main()
