"""Collect the Other Sources Domain Finder results -> results.csv (Row ID, Company Name, Group, Domain, Status, Reasoning).

    python collect.py

Same rules as the CE collector (../../domain_finder/collect.py: registrable domain, social / directory / marketplace
answers refused). Reads runs_*/results/*.jsonl (pilot, rest, retries; a complete answer wins). Works on partial runs.
build_os_feed.py --finder applies results.csv.
"""
import collections
import csv
import glob
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "domain_finder"))
sys.path.insert(0, os.path.join(HERE, "..", "..", "..", "..", "work"))
from collect import BLOCK  # noqa: E402  (CE collector)
from build_comparative import reg_domain  # noqa: E402


def main():
    feed = {r["Row ID"]: r for r in csv.DictReader(open(os.path.join(HERE, "feed.csv"), encoding="utf-8-sig"))}
    res = {}
    for f in sorted(glob.glob(os.path.join(HERE, "runs_*", "results", "*.jsonl"))):
        for line in open(f, encoding="utf-8"):
            d = json.loads(line)
            if d["id"] in feed and (d.get("status") == "complete" or d["id"] not in res):
                res[d["id"]] = d
    rows, counts = [], collections.Counter()
    for rid, fr in feed.items():
        d = res.get(rid)
        dom, reason, status = "", "", "pending"
        if d:
            out = d.get("result") or {}
            reason = " ".join(str(out.get("reasoning") or "").split())[:400]
            raw = str((out.get("structuredOutputs") or {}).get("Domain") or "").strip()
            if d.get("status") != "complete":
                status = "failed"
            elif not raw or "empty" in raw.lower():
                status = "empty"
            else:
                dom = reg_domain(raw)
                status = "found" if dom and not BLOCK.search(dom) else "refused"
                if status == "refused":
                    reason, dom = f"returned {raw}; " + reason, ""
        counts[status] += 1
        rows.append({"Row ID": rid, "Company Name": fr["Company Name"], "Source": fr["Event"].replace(
            "Other Sources: ", ""), "Country": fr["Country"], "Domain": dom, "Status": status, "Reasoning": reason})
    with open(os.path.join(HERE, "results.csv"), "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print(dict(counts))


if __name__ == "__main__":
    main()
