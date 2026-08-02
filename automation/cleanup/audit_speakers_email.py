"""Audit every Speakers_normalized table for the email build state (run in WSL).

Writes speakers_email_audit.json: per workbook, which of the email columns exist.
The rollout uses this to decide what to skip — a browser header scan cannot be
trusted for this, because newly added columns sit off-screen to the right and
_find_header_rect only sees the viewport (that under-report is what almost made
the rollout add a SECOND set of email columns to Digi-tech Pharma & AI, which
was already built the old way).

    MSYS_NO_PATHCONV=1 wsl -d Ubuntu -- python3 .../audit_speakers_email.py
"""
import glob
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SCOPE = os.path.join(HERE, "speakers_normalized_workbooks.json")
OUT = os.path.join(HERE, "speakers_email_audit.json")
CLEAN_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

# New-template outputs vs the earlier LeadMagic build.
TEMPLATE_COLS = ("WORK EMAIL", "Validate Email", "Smtp Provider",
                 "Email Validation Result")
OLD_COLS = ("Validate email", "Status", "Mx Record", "Mx Provider")


def clay_bin():
    env = os.environ.get("CLAY_BIN")
    if env:
        return env
    base = os.path.expanduser("~/.config/clay/bin")
    c = sorted(glob.glob(os.path.join(base, "clay-*-linux-x64")) +
               glob.glob(os.path.join(base, "clay-*-linux-arm64")))
    return c[-1] if c else "clay"


def run(bin_, args):
    env = dict(os.environ)
    env["PATH"] = CLEAN_PATH
    for _ in range(6):
        r = subprocess.run([bin_] + args, capture_output=True, text=True, env=env)
        if r.returncode == 0 and r.stdout.strip():
            try:
                return json.loads(r.stdout)
            except json.JSONDecodeError:
                pass
        time.sleep(2)
    raise SystemExit(f"clay {' '.join(args)} failed: {r.stderr[:200]}")


def main():
    bin_ = clay_bin()
    scope = json.load(open(SCOPE, encoding="utf-8"))
    want_names = set(scope.values())

    tables, cur = {}, None
    for _ in range(200):
        d = run(bin_, ["tables", "list", "--limit", "100"] +
                (["--cursor", cur] if cur else []))
        for t in d.get("data", []):
            wb = (t.get("workbook") or {}).get("name")
            if wb in want_names and t.get("name") == "Speakers_normalized":
                tables[wb] = t["id"]
        cur = d.get("cursor")
        if not cur:
            break

    audit = {}
    for wid, name in scope.items():
        tid = tables.get(name)
        if not tid:
            audit[name] = {"workbook_id": wid, "table_id": None,
                           "state": "no_table"}
            print(f"  {name:50} NO TABLE", flush=True)
            continue
        cols = run(bin_, ["tables", "columns", "list", tid])
        cols = cols.get("data") if isinstance(cols, dict) else cols
        names = {c.get("name") for c in cols}
        has_tpl = [c for c in TEMPLATE_COLS if c in names]
        has_old = [c for c in OLD_COLS if c in names]
        has_gate = "Speaker Name" in names
        if "WORK EMAIL" in names:
            state = "template_built" if "Validate Email" in names else "built_old"
        else:
            state = "pending" if has_gate else "no_gate_column"
        audit[name] = {"workbook_id": wid, "table_id": tid, "state": state,
                       "template_cols": has_tpl, "old_cols": has_old,
                       "has_speaker_name": has_gate, "n_columns": len(names)}
        print(f"  {name:50} {state:15} tpl={has_tpl} old={has_old}", flush=True)

    json.dump(audit, open(OUT, "w", encoding="utf-8"), indent=1,
              ensure_ascii=False)
    counts = {}
    for v in audit.values():
        counts[v["state"]] = counts.get(v["state"], 0) + 1
    print("\nsummary:", counts)
    print("written:", OUT)


if __name__ == "__main__":
    main()
