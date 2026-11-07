"""Print how far a delivery import has got. Kept as a file rather than a
shell one-liner: the state keys contain `||`, which does not survive being
quoted through bash into python -c.

    python delivery_progress.py [ce|ps|bs]

The plan is the denominator, so this never reports a fraction of a number it
guessed: a delivery with no plan file is an error worth seeing, not a 0/144.
"""
import json
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# Same keys as import_ce_delivery_tables.DELIVERIES — (state, plan).
DELIVERIES = {
    "ce": ("ce_delivery_tables_state.json", "ce_event_tables_plan.json"),
    "ps": ("ps_delivery_tables_state.json", "ps_delivery_tables_plan.json"),
    "bs": ("bs_delivery_tables_state.json", "bs_delivery_tables_plan.json"),
    "os": ("os_delivery_tables_state.json", "os_delivery_tables_plan.json"),
}

name = sys.argv[1] if len(sys.argv) > 1 else "ce"
if name not in DELIVERIES:
    print(f"   -> unknown delivery {name!r} (use: {', '.join(sorted(DELIVERIES))})")
    sys.exit(2)
state_file, plan_file = (os.path.join(SCRIPT_DIR, f) for f in DELIVERIES[name])

if not os.path.exists(plan_file):
    print(f"   -> no plan yet ({os.path.basename(plan_file)}) — run the splitter")
    sys.exit(0)
plan = json.load(open(plan_file, encoding="utf-8"))
total = len(plan)
books = len({e["event"] for e in plan})

if not os.path.exists(state_file):
    print(f"   -> 0/{total} tables, 0/{books} workbooks")
    sys.exit(0)
state = json.load(open(state_file, encoding="utf-8"))
done = sum(1 for v in state.values() if v.get("status") == "done")
seen = {k.split("||")[0] for k in state}
print(f"   -> {done}/{total} tables, {len(seen)}/{books} workbooks")
