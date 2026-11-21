"""Collect the Other Sources classifier results -> classified_os.csv (one row per domain = Key).

    python collect_os.py

Same checks as the CE collector (../../classify/collect.py): every answer must be one of the 28 labels in the CE
classifier_prompt.txt on the right side, else 'off-list'. Reads runs_*/results/*.jsonl (pilot, rest, retries; a
complete answer wins) and applies overrides_os.csv (Key, Side, Classification, Note) if present. Also prints, per
source directory, the side split and the share of "All other industries", so the pilot can be read against the
Other_Sources v2 tab's directory Side (a hint only, user answer 4, 21 Nov 2026).
"""
import csv
import glob
import json
import os
import re
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
PROMPT = os.path.join(HERE, "..", "..", "classify", "classifier_prompt.txt")
TAB_SIDE = {"#101": "Sell", "#102": "Sell", "#103": "Sell", "#104": "Both", "#105": "Sell", "#106": "Sell",
            "#107": "Sell", "#108": "Both", "#109": "Both", "#110": "Both", "#111": "Both", "#112": "Buy",
            "#113": "Buy", "#114": "Buy"}


def labels():
    p = open(PROMPT, encoding="utf-8").read()
    return set(re.findall(r"^B\d+\) (.+)$", p, re.M)), set(re.findall(r"^S\d+\) (.+)$", p, re.M))


def main():
    buy, sell = labels()
    feed = {r["Key"]: r for r in csv.DictReader(open(os.path.join(HERE, "feed_classify_os.csv"), encoding="utf-8-sig"))}
    res = {}
    for f in sorted(glob.glob(os.path.join(HERE, "runs_*", "results", "*.jsonl"))):
        for line in open(f, encoding="utf-8"):
            d = json.loads(line)
            if d.get("status") == "complete" or d["id"] not in res:
                res[d["id"]] = d
    overrides = {}
    op = os.path.join(HERE, "overrides_os.csv")
    if os.path.exists(op):
        overrides = {r["Key"]: r for r in csv.DictReader(open(op, encoding="utf-8-sig"))}
    out, c = [], Counter()
    for key, fr in feed.items():
        d = res.get(key)
        side = cl = reason = ""
        if key in overrides:
            o = overrides[key]
            side, cl, st, srcl = o["Side"], o["Classification"], "ok", "override: " + o.get("Note", "")
        elif not d:
            st, srcl = "pending", ""
        else:
            so = (d.get("result") or {}).get("structuredOutputs") or {}
            side, cl = str(so.get("Side") or "").strip(), str(so.get("Classification") or "").strip()
            reason = " ".join(str((d.get("result") or {}).get("reasoning") or so.get("Reasoning") or "").split())[:300]
            if d.get("status") != "complete":
                st = "failed"
            elif (side == "Buy Side" and cl in buy) or (side == "Sell Side" and cl in sell):
                st = "ok"
            else:
                st = "off-list"
            srcl = "clay classifier"
        c[st] += 1
        out.append({"Key": key, "Company Name": fr["Company Name"], "Side": side if st == "ok" else "",
                    "Classification": cl if st == "ok" else "", "Status": st, "Source": srcl,
                    "Sources": fr["Sources"], "Compliance": fr["Compliance"], "Raw Side": side, "Raw Classification": cl,
                    "Reasoning": reason})
    with open(os.path.join(HERE, "classified_os.csv"), "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, list(out[0]))
        w.writeheader()
        w.writerows(out)
    print("domains by status:", dict(c))
    ok = [x for x in out if x["Status"] == "ok"]
    print("sides:", dict(Counter(x["Side"] for x in ok)))
    for (s, cl), n in Counter((x["Side"], x["Classification"]) for x in ok).most_common():
        print(f"  {n:6}  {s:9}  {cl}")
    per = defaultdict(Counter)
    for x in ok:
        for s in x["Sources"].split("; "):
            per[s][x["Side"][:4]] += 1
            per[s]["AOI"] += x["Classification"] == "All other industries"
    print("per source (tab side | Buy / Sell / All other industries):")
    for s, v in sorted(per.items()):
        print(f"  {s[:50]:50s} tab {TAB_SIDE.get(s[:4], '?'):4s} | buy {v['Buy ']:4d} sell {v['Sell']:4d} aoi {v['AOI']:4d}")


if __name__ == "__main__":
    main()
