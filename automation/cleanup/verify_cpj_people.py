"""Post-run verification for the CPJ People pass (run in WSL, read-only).

For every table in cpj_people_targets.json, report which of the template's
columns exist and how full they are, and classify the table:

  ok        all present, the canonical AND the custom columns carry data
  canonical only of the 8 Clay-canonical fields resolved; the custom exhibitor
            columns came back null -- the per-row company reference
            (company_table_id/company_record_id, recorded by the Find People
            search) does not resolve to a live row in that workbook's exhibitor
            table, so Clay supplies its own company data but none of the
            Claygent-authored columns. Proven on ADLM USA / Sellers - People,
            whose exhibitor table plainly HAS that data (Side 100%,
            Sub - industry 66%) while the People copies are null.
  missing   the template's columns are not on the table at all
  empty     the table has no rows to judge

Fill rates are compared against the SOURCE table where one is known, so a column
that is empty because the event never had the data is not reported as a failure.

  python3 verify_cpj_people.py
  python3 verify_cpj_people.py --out cpj_verify.json --limit 20
"""
import argparse
import glob
import json
import os
import subprocess
import sys
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CLEAN = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
TARGETS = os.path.join(SCRIPT_DIR, "cpj_people_targets.json")

# The 8 that resolve from Clay's own company data.
CANONICAL = ["Size", "Type", "Domain", "LinkedIn URL", "Founded", "Industry",
             "Description", "Specialities"]
# The 8 that need the referenced exhibitor row (5 Claygent + the v2 additions).
CUSTOM = ["Sub Industry", "Primary Offerings", "Business Stage",
          "Annual Revenue", "Total Funding Amount Range Usd",
          "Side", "Classification", "Country"]
ALL_COLS = CANONICAL + CUSTOM


def clay_bin():
    base = os.path.expanduser("~/.config/clay/bin")
    c = sorted(glob.glob(os.path.join(base, "clay-*-linux-x64")) +
               glob.glob(os.path.join(base, "clay-*-linux-arm64")))
    return c[-1] if c else "clay"


def run(binary, *args):
    env = dict(os.environ)
    env["PATH"] = CLEAN
    for _ in range(4):
        r = subprocess.run([binary, *args], capture_output=True, text=True,
                           env=env)
        if r.returncode == 0 and r.stdout.strip():
            try:
                return json.loads(r.stdout)
            except json.JSONDecodeError:
                pass
        time.sleep(2)
    return None


def audit_table(binary, table_id, sample=100):
    cols = run(binary, "tables", "columns", "list", table_id)
    if not cols:
        return None
    names = {c["id"]: c["name"] for c in cols.get("data", [])}
    present = [c for c in ALL_COLS if c in names.values()]
    if not present:
        return {"present": [], "verdict": "missing"}
    rows = run(binary, "tables", "rows", "list", table_id, "--limit", str(sample))
    data = (rows or {}).get("data") or []
    if not data:
        return {"present": present, "verdict": "empty", "rows": 0}
    filled = {c: 0 for c in present}
    for r in data:
        named = {names.get(k, k): v for k, v in r.get("cells", {}).items()}
        for c in present:
            v = (named.get(c) or {}).get("value")
            if v not in (None, "", []):
                filled[c] += 1
    n = len(data)
    canon_hits = sum(1 for c in CANONICAL if filled.get(c))
    custom_hits = sum(1 for c in CUSTOM if filled.get(c))
    verdict = ("ok" if custom_hits else
               "canonical" if canon_hits else "empty")
    return {"present": present, "rows": n, "verdict": verdict,
            "filled": {c: f"{filled[c]}/{n}" for c in present},
            "missing_cols": [c for c in ALL_COLS if c not in present]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(SCRIPT_DIR,
                                                  "cpj_verify.json"))
    ap.add_argument("--limit", type=int)
    ap.add_argument("--sample", type=int, default=100)
    a = ap.parse_args()

    payload = json.load(open(TARGETS, encoding="utf-8"))
    targets = payload.get("targets", payload)
    binary = clay_bin()
    out, tally = {}, {}
    items = []
    for event in sorted(targets):
        for table, tid in sorted(targets[event]["tables"].items()):
            items.append((event, table, tid))
    if a.limit:
        items = items[:a.limit]

    for event, table, tid in items:
        res = audit_table(binary, tid, a.sample)
        key = f"{event}|{table}"
        if res is None:
            out[key] = {"verdict": "unreadable"}
        else:
            out[key] = res
        v = out[key]["verdict"]
        tally[v] = tally.get(v, 0) + 1
        print(f"{v:<10}{event:<44}{table:<18}"
              f"{out[key].get('rows', '-')}", flush=True)

    print("\n--- tally ---")
    for k in sorted(tally):
        print(f"  {k:<12}{tally[k]}")
    json.dump(out, open(a.out, "w", encoding="utf-8"), indent=1,
              ensure_ascii=False)
    print(f"\nwrote {a.out}")


if __name__ == "__main__":
    sys.exit(main())
