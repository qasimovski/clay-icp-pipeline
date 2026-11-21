"""(Copied 21 Nov 2026 from scrapers_submarine/_run_v2/other_sources/delivery for Rail Other Sources.)
After deliver.py split: "Companies Buyside People" -> "Buyside People" (file + plan entry), so each
source workbook holds Companies / Buyside People / Sellside People (user, 2026-11-05).
THE CSV FILENAME IS THE TABLE NAME - run this BEFORE the import, never after.
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    p = os.path.join(HERE, "plan.json")
    plan = json.load(open(p, encoding="utf-8"))
    n = 0
    for e in plan:
        if e["table"].startswith("Companies ") and e["table"].endswith("People"):
            new = e["table"][len("Companies "):]
            dst = os.path.join(os.path.dirname(e["csv"]), new + ".csv")
            os.replace(e["csv"], dst)
            e["table"], e["csv"] = new, dst
            n += 1
    json.dump(plan, open(p, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    print(f"renamed {n} people tables; plan has {len(plan)} tables in {len({e['event'] for e in plan})} workbooks")


if __name__ == "__main__":
    main()
