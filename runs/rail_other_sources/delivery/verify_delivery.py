"""(Copied 21 Nov 2026 from the Submarine OS delivery for the Rail Other Sources review copies.)
Final check of the Rail OS Clay review copies (free, read-only). For every workbook in import_state.json: the
live tables and row counts over the CLI (list_workbook_table_rows.py, WSL) against plan.json.
Reports: tables short / missing / unreadable, extra tables (e.g. 'Name (2)', leftovers), and workbooks whose state
points at more than one id. Writes verify_delivery.txt.

    python verify_delivery.py
"""
import collections, json, os, subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
plan = json.load(open(os.path.join(HERE, "plan.json"), encoding="utf-8"))
state = json.load(open(os.path.join(HERE, "import_state.json"), encoding="utf-8"))
want = collections.defaultdict(dict)
for e in plan:
    want[e["event"]][e["table"]] = e["rows"]
wids = collections.defaultdict(set)
for k, v in state.items():
    if v.get("workbook_id"):
        wids[k.split("||")[0]].add(v["workbook_id"])
lines, bad = [], 0
for ev in sorted(want):
    ids = wids.get(ev, set())
    if len(ids) != 1:
        lines.append(f"!! {ev}: workbook ids in state {sorted(ids)}"); bad += 1
        if not ids:
            continue
    wid = sorted(ids)[0]
    r = subprocess.run(["wsl", "bash", "-lc", "cd /mnt/c/Users/qasim/terrapinn/clay-icp-pipeline/automation/cleanup && "
                        f"python3 list_workbook_table_rows.py {wid}"], capture_output=True)
    live = {}
    for ln in r.stdout.decode("utf-8", "replace").splitlines():
        if "|" in ln:
            n, c = ln.rsplit("|", 1)
            live[n.strip()] = int(c) if c.strip().isdigit() else None
    for t, n in want[ev].items():
        if live.get(t) != n:
            lines.append(f"!! {ev} | {t}: Clay {live.get(t, 'MISSING')} / CSV {n}"); bad += 1
    for t in set(live) - set(want[ev]):
        lines.append(f"!! {ev} | extra table {t!r} ({live[t]} rows)"); bad += 1
summary = (f"{len(want)} workbooks, {len(plan)} tables, {sum(e['rows'] for e in plan):,} rows checked; "
           f"{bad} problem(s)")
open(os.path.join(HERE, "verify_delivery.txt"), "w", encoding="utf-8").write("\n".join(lines + [summary]) + "\n")
print("\n".join(lines + [summary]))
