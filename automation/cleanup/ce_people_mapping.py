"""Resolve a classified Competitive Events row to its people-search inputs.

Given the Registrar's `Side` + `Classification`, answer the only question the
people pass needs: which persona, and which countries?

    Sell Side  persona  <- config/icps/highways/seller_people.yaml   (Seller JTs)
               countries<- config/icps/highways/products_services.yaml, by Top level
    Buy Side   persona  <- config/icps/highways_buyside/people.yaml  (BuySide ICP)
               countries<- config/icps/highways_buyside/products_services.yaml

That split mirrors the ICP workbook itself: the sell side needs two tabs
(`Products_Services` for industry + countries, `Seller JTs` for the persona,
which is shared across every category), while the buy side has industry,
persona and countries together in one tab. `Classification` is the Top level /
Industry in both, which is exactly what the Registrar emits, so it keys straight
in with no translation.

Sub level plays no part. It varied the COMPANY search — it supplied the
description / products_and_services terms that built the P&S company tables —
but the persona is shared across a Top level and countries are set per Top
level, so nothing after classification needs it.

WHAT THIS MODULE DELIBERATELY DOES NOT DO. It does not render ClayQL. The
renderers already exist and are proven (`ps_people_config.render`,
`ps_buyside_people_config.render`); duplicating them here would be a second
place for the query shape to drift. This only answers "which category, which
countries, and is this row searchable at all".

UNMAPPED IS A FIRST-CLASS ANSWER, not an error. 942 of the 5,489 classified rows
resolve to no persona or no countries — overwhelmingly `All other industries`,
which is a valid label in the classifier's closed list but is not a key in
either ICP map. Those rows must be reported, never silently searched: a row with
no country list would otherwise query every title everywhere.

    python ce_people_mapping.py --report          # coverage over the deliverable
    python ce_people_mapping.py --report --event "Big 5 Construct Saudi"
    python ce_people_mapping.py --show "Sell Side" "Construction Materials"
"""

import argparse
import csv
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

import ps_people_config as sellercfg          # noqa: E402
import ps_buyside_people_config as buyercfg   # noqa: E402
import ps_query_config as psq                 # noqa: E402

SELL_ICP = "highways"
BUY_ICP = "highways_buyside"

COMBINED = os.path.join(SCRIPT_DIR, "ce_classification_exports",
                        "Highways CE - all tables combined.csv")


class Unmapped(Exception):
    """The row cannot be searched. Carries why, for reporting."""


def _load():
    """Everything the resolver needs, read once."""
    sell = sellercfg.load(SELL_ICP)
    buy = buyercfg.load(BUY_ICP)
    # Countries live with the COMPANY config, per Top level on the sell side and
    # at ICP level on the buy side — the same source the company searches used,
    # so people and companies can never drift apart on geography.
    sell_countries = {c["name"]: c.get("countries")
                      for c in psq.load(SELL_ICP)["categories"]}
    buy_countries = psq.load(BUY_ICP).get("countries")
    buy_categories = {c["name"] for c in buy["categories"]}
    return sell, buy, sell_countries, buy_countries, buy_categories


def resolve(side, classification, loaded=None):
    """(kind, category, countries) for a classified row, or raise Unmapped.

    `kind` is "sell" or "buy" and says which renderer to call; `category` is the
    name to pass to the buy-side renderer (the sell side needs no category, only
    its countries).
    """
    sell, buy, sell_countries, buy_countries, buy_categories = loaded or _load()
    side = (side or "").strip()
    cat = (classification or "").strip()
    if not side or not cat:
        raise Unmapped("no side or no classification")

    if side == "Sell Side":
        if cat not in sell_countries:
            raise Unmapped(f"'{cat}' is not a Products_Services Top level")
        ctys = sell_countries.get(cat)
        if not ctys:
            raise Unmapped(f"'{cat}' has no countries in Products_Services")
        return "sell", cat, ctys

    if side == "Buy Side":
        if cat not in buy_categories:
            raise Unmapped(f"'{cat}' is not a BuySide ICP industry")
        if not buy_countries:
            raise Unmapped("buy-side ICP has no countries")
        return "buy", cat, buy_countries

    raise Unmapped(f"unrecognised side {side!r}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--report", action="store_true",
                    help="coverage over the combined deliverable")
    ap.add_argument("--event", help="limit the report to one event")
    ap.add_argument("--csv", default=COMBINED)
    ap.add_argument("--show", nargs=2, metavar=("SIDE", "CLASSIFICATION"),
                    help="resolve one pair and print what it maps to")
    args = ap.parse_args()

    loaded = _load()
    sell, buy, sell_countries, buy_countries, buy_categories = loaded
    print(f"sell-side Top levels with countries : "
          f"{sum(1 for v in sell_countries.values() if v)}")
    print(f"buy-side industries                 : {len(buy_categories)}")
    print(f"buy-side countries                  : {len(buy_countries or [])}")

    if args.show:
        side, cat = args.show
        try:
            kind, name, ctys = resolve(side, cat, loaded)
        except Unmapped as e:
            print(f"\n{side} / {cat}  ->  UNMAPPED: {e}")
            return
        if kind == "sell":
            titles = (len(sell["titles_exact"]) + len(sell["titles_contains"])
                      + len(sell.get("titles_contains_senior_only") or []))
            src = "Seller JTs (shared persona)"
        else:
            titles = len(buyercfg.titles_for(buy, name))
            src = f"BuySide ICP / {name}"
        print(f"\n{side} / {cat}")
        print(f"   persona   : {src} — {titles} titles")
        print(f"   countries : {len(ctys)} — {', '.join(ctys)}")
        print(f"   renderer  : {'ps_people_config' if kind == 'sell' else 'ps_buyside_people_config'}")
        return

    if not args.report:
        return
    if not os.path.exists(args.csv):
        raise SystemExit(f"no combined CSV at {args.csv}")

    import collections
    rows = [r for r in csv.DictReader(open(args.csv, encoding="utf-8-sig"))
            if r.get("Side", "").strip()]
    if args.event:
        rows = [r for r in rows if r["Company Event Name"] == args.event]
    ok = collections.Counter()
    bad = collections.Counter()
    per_event = collections.defaultdict(lambda: [0, 0])
    for r in rows:
        try:
            kind, name, ctys = resolve(r["Side"], r["Classification"], loaded)
        except Unmapped as e:
            bad[(r["Side"], r["Classification"], str(e))] += 1
            per_event[r["Company Event Name"]][1] += 1
        else:
            ok[(r["Side"], name, len(ctys))] += 1
            per_event[r["Company Event Name"]][0] += 1

    tot_ok, tot_bad = sum(ok.values()), sum(bad.values())
    scope = f" — {args.event}" if args.event else ""
    print(f"\nclassified rows{scope}: {tot_ok + tot_bad}")
    print(f"   searchable : {tot_ok}")
    print(f"   UNMAPPED   : {tot_bad}")

    print(f"\nsearchable, by (side, category) — the search configurations needed:")
    for (side, name, nc), n in ok.most_common(20):
        print(f"   {n:5}  {side:10} {name[:44]:46} {nc} countries")
    if len(ok) > 20:
        print(f"   … {len(ok) - 20} more")

    if bad:
        print(f"\nUNMAPPED, by reason:")
        for (side, cat, why), n in bad.most_common(12):
            print(f"   {n:5}  {side:10} {cat[:38]:40} {why}")

    if not args.event:
        worst = sorted(per_event.items(), key=lambda x: -x[1][1])[:8]
        print(f"\nevents with the most unmapped rows:")
        for ev, (g, b) in worst:
            if b:
                print(f"   {ev[:44]:46} {b:4} unmapped of {g + b}")


if __name__ == "__main__":
    main()
