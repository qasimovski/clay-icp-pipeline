"""Step (h) "Email Pass" report: how many rows actually got enriched (run in WSL).

Per Speakers_normalized table: rows, rows with the gate column filled, and rows
with WORK EMAIL filled — plus the validation outputs where they exist. Read-only.

    MSYS_NO_PATHCONV=1 wsl -d Ubuntu -- python3 .../report_email_pass.py
"""
import glob
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
AUDIT = os.path.join(HERE, "speakers_email_audit.json")
CLEAN = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

GATE = "Speaker Name"
OUT = "WORK EMAIL"
VALID = ("Email Validation Result", "Status")   # template vs old build


def clay():
    base = os.path.expanduser("~/.config/clay/bin")
    c = sorted(glob.glob(os.path.join(base, "clay-*-linux-x64")) +
               glob.glob(os.path.join(base, "clay-*-linux-arm64")))
    return c[-1] if c else "clay"


def run(b, args):
    env = dict(os.environ)
    env["PATH"] = CLEAN
    for _ in range(5):
        r = subprocess.run([b] + args, capture_output=True, text=True, env=env)
        if r.returncode == 0 and r.stdout.strip():
            try:
                return json.loads(r.stdout)
            except json.JSONDecodeError:
                pass
        time.sleep(2)
    return None


def counts(b, tid):
    cols = run(b, ["tables", "columns", "list", tid])
    if cols is None:
        return None
    cols = cols.get("data") if isinstance(cols, dict) else cols
    fid = {c.get("name"): c.get("id") for c in cols}
    rows, cur, pages = [], None, 0
    while pages < 30:
        args = ["tables", "rows", "list", tid, "--limit", "100"]
        if cur:
            args += ["--cursor", cur]
        d = run(b, args)
        if d is None:
            break
        rows += d.get("data", [])
        cur = d.get("cursor")
        pages += 1
        if not cur:
            break

    def filled(col):
        f = fid.get(col)
        if not f:
            return None
        n = 0
        for r in rows:
            cell = (r.get("cells") or {}).get(f)
            if isinstance(cell, dict) and cell.get("value"):
                n += 1
        return n

    val = None
    val_col = None
    for c in VALID:
        if c in fid:
            val, val_col = filled(c), c
            break
    return {"rows": len(rows), "gate": filled(GATE), "out": filled(OUT),
            "validated": val, "validated_col": val_col}


def main():
    b = clay()
    audit = json.load(open(AUDIT, encoding="utf-8"))
    groups = {"template_built": [], "built_old": []}
    tot = {k: {"rows": 0, "gate": 0, "out": 0, "validated": 0}
           for k in groups}
    print(f"{'event':46} {'rows':>6} {'gated':>6} {'emails':>7} {'hit%':>6} "
          f"{'valid':>6}")
    for name in sorted(audit):
        rec = audit[name]
        if rec.get("state") not in groups or not rec.get("table_id"):
            continue
        c = counts(b, rec["table_id"])
        if not c:
            print(f"{name:46}  (query failed)")
            continue
        groups[rec["state"]].append((name, c))
        g = tot[rec["state"]]
        g["rows"] += c["rows"]
        g["gate"] += c["gate"] or 0
        g["out"] += c["out"] or 0
        g["validated"] += c["validated"] or 0
        hit = f"{100*(c['out'] or 0)/c['gate']:.0f}%" if c.get("gate") else "-"
        print(f"{name:46} {c['rows']:6} {c['gate'] or 0:6} {c['out'] or 0:7} "
              f"{hit:>6} {c['validated'] or 0:6}", flush=True)

    print()
    for k, g in tot.items():
        if not groups[k]:
            continue
        hit = f"{100*g['out']/g['gate']:.1f}%" if g["gate"] else "-"
        print(f"{k}: {len(groups[k])} tables | rows {g['rows']} | "
              f"gated {g['gate']} | WORK EMAIL {g['out']} ({hit} of gated) | "
              f"validated {g['validated']}")
    gt = {k: sum(t[k] for t in tot.values()) for k in ("rows", "gate", "out",
                                                       "validated")}
    print(f"TOTAL: rows {gt['rows']} | gated {gt['gate']} | "
          f"WORK EMAIL {gt['out']} | validated {gt['validated']}")


if __name__ == "__main__":
    main()
