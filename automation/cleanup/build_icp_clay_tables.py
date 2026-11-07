"""Build the `Clay Tables` rows for the Highways ICP playbook (run in WSL).

PEOPLE TABLES ONLY, one row each, in the shape the Labs playbook's `Clay Tables`
tab uses. Emits a CSV plus an .xlsx whose column layout matches the HIGHWAYS tab
(which differs from Labs: Highways puts the link in its own column C).

    Name | Promo | Link to Clay Table | Qty in Table | # Emails or LinkedIn |
    Status | GTM | List description | List Number

WHICH TABLES THESE POINT AT — the delivery copies, deliberately:

  * The Highways CE people never existed as live per-event Clay tables at all.
    They came back from the Workflows route as CSVs, so the delivery workbooks
    are the ONLY Clay tables that hold them.
  * The live P&S `People` tables predate the 54-term exclusion list and the
    country filter. They still contain the titles and out-of-country people
    that were removed at export, so linking them would hand the GTM a superset
    of what was actually approved.

The delivery tables are the filtered, deduped, verified set — the same rows the
marketing manager reviews — so they are the honest target for a list link.

ROW COUNTS COME FROM THE PLAN, not from a fresh read: the plan's count is the
CSV's count, and `verify_ce_delivery.py` has already confirmed every one of them
against Clay. Re-reading here would only add a way to disagree.

    python3 build_icp_clay_tables.py            # all four scopes
    python3 build_icp_clay_tables.py --delivery ps
"""

import argparse
import collections
import csv
import io
import json
import os
import sys
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import clay_bin  # noqa: E402

DELIVERIES = {
    "ce": ("ce_event_tables_plan.json", "ce_delivery_tables_state.json"),
    "ps": ("ps_delivery_tables_plan.json", "ps_delivery_tables_state.json"),
    "bs": ("bs_delivery_tables_plan.json", "bs_delivery_tables_state.json"),
    "os": ("os_delivery_tables_plan.json", "os_delivery_tables_state.json"),
    # CE after the scraper fit filter, in its own delivery folder (2026-10-19).
    # Replaces `ce` in the playbook; never emitted alongside it by default.
    "ce_fit": ("ce_event_tables_fit_plan.json", "ce_fit_delivery_tables_state.json"),
    "os_v7": ("os_v7_delivery_tables_plan.json", "os_v7_delivery_tables_state.json"),
    # Mining ICP v2 CE review copies (2026-10-26); the plan/state live with the mining scrape.
    # Run with --prefix Mining.
    "mining_ce": ("../../../scrapers_mining/_v2/clay_run/event_tables_plan.json",
                  "../../../scrapers_mining/_v2/clay_run/event_tables_state.json"),
    # Solar & Storage Live ICP v2 CE review copies (2026-10-29) (folder id in the plan/state files).
    # Run with --prefix Solar. Speakers are never in it (they go to the Salesforce CSV only).
    "solar_ce": ("../../../scrapers_solar/_run_v2/delivery/plan.json",
                 "../../../scrapers_solar/_run_v2/delivery/state.json"),
    # Solar Other Sources v2 review copies (2026-11-05) (folder id in the plan/state files): ONE WORKBOOK PER
    # SOURCE LIST (user's choice, unlike Highways' single "Other Sources" workbook), so rows are named per
    # list like CE ("Buyers from MCS"). state_merged.json = state.json + the import_parallel worker states.
    # Run with --prefix Solar.
    "solar_os": ("../../../scrapers_solar/_other_sources/delivery/plan.json",
                 "../../../scrapers_solar/_other_sources/delivery/state_merged.json"),
    # Solar buy side (Buyside P&S, 2026-11-06), folder "BuySide P&S": one workbook per
    # buyer group ("1. Installers & EPCs" ...); workbook 4's people are two tables (split by sub-segment).
    # Run with --prefix Solar.
    "solar_bs": ("../../../scrapers_solar/_buyside/delivery/plan.json",
                 "../../../scrapers_solar/_buyside/delivery/state_merged.json"),
}

# Playbook section headings, in the order the Highways tab lists them. Buyside
# P&S is absent from that tab today — Labs has it — so it is emitted last for
# the section that has to be added.
SECTION = {
    "ce": "Competitive Events",
    "os": "Other Sources / Associations / Directories",
    "ps": "Industries / Products & Services",
    "bs": "Buyside P&S",
    "ce_fit": "Competitive Events",
    "os_v7": "Other Sources / Associations / Directories",
    "mining_ce": "Competitive Events",
    "solar_ce": "Competitive Events",
    "solar_os": "Other Sources / Associations / Directories",
    "solar_bs": "Buyside P&S",
}
ORDER = ("ce", "ce_fit", "mining_ce", "solar_ce", "os", "os_v7", "solar_os", "ps", "bs", "solar_bs")
# What a bare run emits: `ce`/`ce_fit` and `os`/`os_v7` share a section, so one of each.
DEFAULT = ("ce", "os", "ps", "bs")

GTM = "Qasim"
PREFIX = "Highways"          # Labs rows read `Labs_<name>`; --prefix for another ICP
SELL, BUY = "SPEX", "CONF"   # promo codes, per the Labs tab


def people_rows(delivery, plan):
    """[(sort key, display name, promo, event, table)] for the PEOPLE tables.

    Naming follows the Labs tab exactly, because the playbook is read across
    ICPs and an inconsistent row is worse than a missing one:
        Sellers from <event>              Sponsors get a " - Sponsors" suffix
        Buyers  from <event>
        <category>                        sell-side P&S is the bare category
        Buyers from <category>            buy-side P&S
    """
    out = []
    for e in plan:
        ev, table = e["event"], e["table"]
        if "People" not in table:
            continue                     # Companies tables are not wanted
        if delivery in ("ce", "ce_fit", "mining_ce", "solar_ce", "solar_os"):
            side = BUY if "Buyside" in table else SELL
            who = "Buyers" if side == BUY else "Sellers"
            suffix = " - Sponsors" if table.startswith("Sponsors") else ""
            name = f"{who} from {ev}{suffix}"
        elif delivery == "ps":
            side, name = SELL, ev
        elif delivery == "bs":
            side, name = BUY, f"Buyers from {ev}"
        elif delivery == "solar_bs":
            # "1. Installers & EPCs" -> "Buyers from Installers & EPCs"; a split people table keeps its part
            # ("Buyside People - Other C&I" -> "... - Other C&I"), so two rows never share a name.
            group = ev.split(". ", 1)[-1]
            part = table.split(" - ", 1)[1] if " - " in table else ""
            side, name = BUY, f"Buyers from {group}" + (f" - {part}" if part else "")
        else:                            # os: one workbook, two people tables
            side = BUY if "Buyside" in table else SELL
            who = "Buyers" if side == BUY else "Sellers"
            name = f"{who} from Other Sources"
        out.append((name.lower(), name, side, ev, table, e["rows"]))
    out.sort()
    return out


def workbook_tables(binary, wids):
    """{workbook id: {table name: table id}} for just these workbooks, one filtered call each
    (`clay tables list --filter workbook.id=`, CLI 1.12). Paging the whole workspace is 60+ calls
    now that it holds more than 6,000 tables."""
    by_wb = collections.defaultdict(dict)
    for wid in sorted(wids):
        for _ in range(3):
            ok, d, err = clay_bin.run(binary, ["tables", "list", "--limit", "100", "--filter", f"workbook.id={wid}"])
            if ok:
                for t in (d.get("data") if isinstance(d, dict) else d) or []:
                    if (t.get("workbook") or {}).get("id") == wid:
                        by_wb[wid][t.get("name")] = t.get("id")
                break
            time.sleep(2)
    return by_wb


def workspace_tables(binary):
    """{workbook id: {table name: table id}} for the whole workspace.

    Paged once and reused for every delivery. `tables list` has no
    workbook filter that this CLI version accepts, so the filtering is
    client-side — the same thing list_workbook_table_rows.py does, but paid for
    once instead of once per workbook.
    """
    by_wb, cursor = collections.defaultdict(dict), None
    for _ in range(400):
        args = ["tables", "list", "--limit", "100"]
        if cursor:
            args += ["--cursor", cursor]
        ok, d, err = clay_bin.run(binary, args)
        if not ok:
            if d is not None:
                raise SystemExit(f"clay tables list failed: {err}")
            time.sleep(2)
            continue
        rows = d.get("data") if isinstance(d, dict) else d
        for t in rows or []:
            wid = (t.get("workbook") or {}).get("id")
            if wid:
                by_wb[wid][t.get("name")] = t.get("id")
        cursor = d.get("cursor") if isinstance(d, dict) else None
        if not cursor or not rows:
            break
    return by_wb


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--delivery", action="append", default=[],
                    choices=sorted(DELIVERIES))
    ap.add_argument("--out", default="icp_clay_tables_highways")
    ap.add_argument("--prefix", default=PREFIX, help="List description prefix (Mining, ...)")
    args = ap.parse_args()
    wanted = args.delivery or list(DEFAULT)

    ws_id = None
    for line in open(os.path.join(os.path.dirname(os.path.dirname(SCRIPT_DIR)),
                                  "config", "local.yaml"), encoding="utf-8"):
        if line.strip().startswith("workspace_id:"):
            ws_id = line.split(":", 1)[1].strip().strip('"\'')
            break
    if not ws_id:
        raise SystemExit("no workspace_id in config/local.yaml")

    binary = clay_bin.pick()
    wids = set()
    for d in wanted:
        st = json.load(open(os.path.join(SCRIPT_DIR, DELIVERIES[d][1]), encoding="utf-8"))
        wids |= {v["workbook_id"] for v in st.values() if v.get("workbook_id")}
    print(f"reading the tables of {len(wids)} workbooks ...", flush=True)
    by_wb = workbook_tables(binary, wids)
    print(f"   {sum(len(v) for v in by_wb.values()):,} tables in "
          f"{len(by_wb):,} workbooks\n")

    emitted, missing = [], []
    for delivery in ORDER:
        if delivery not in wanted:
            continue
        plan_f, state_f = DELIVERIES[delivery]
        plan = json.load(open(os.path.join(SCRIPT_DIR, plan_f), encoding="utf-8"))
        state = json.load(open(os.path.join(SCRIPT_DIR, state_f), encoding="utf-8"))
        wb_of = {}
        for k, v in state.items():
            if v.get("workbook_id"):
                wb_of.setdefault(k.split("||")[0], v["workbook_id"])

        rows = people_rows(delivery, plan)
        n_before = len(emitted)
        for _k, name, promo, ev, table, qty in rows:
            wid = wb_of.get(ev)
            tid = (by_wb.get(wid) or {}).get(table) if wid else None
            if not tid:
                missing.append((delivery, ev, table))
                continue
            link = (f"https://app.clay.com/workspaces/{ws_id}"
                    f"/workbooks/{wid}/tables/{tid}")
            emitted.append({
                "section": SECTION[delivery],
                "Name": name,
                "Promo": promo,
                "Link to Clay Table": link,
                "Qty in Table": qty,
                "# Emails or LinkedIn": "",
                "Status": "",
                "GTM": GTM,
                "List description": f"{args.prefix}_{name}",
                "List Number": "",
            })
        print(f"{SECTION[delivery]:46} {len(emitted) - n_before:>4} people "
              f"tables   {sum(r['Qty in Table'] for r in emitted[n_before:]):>8,} rows")

    if missing:
        print(f"\n!! {len(missing)} people table(s) had no id — NOT emitted:")
        for m in missing[:10]:
            print(f"   {m}")

    cols = ["Name", "Promo", "Link to Clay Table", "Qty in Table",
            "# Emails or LinkedIn", "Status", "GTM", "List description",
            "List Number"]
    csv_path = os.path.join(SCRIPT_DIR, args.out + ".csv")
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        for section in [SECTION[d] for d in ORDER if d in wanted]:
            rows = [r for r in emitted if r["section"] == section]
            if not rows:
                continue
            w.writerow([section])
            w.writerow(cols)
            for r in rows:
                w.writerow([r[c] for c in cols])
            w.writerow(["", "", "", sum(r["Qty in Table"] for r in rows)])
            w.writerow([])

    total = sum(r["Qty in Table"] for r in emitted)
    print(f"\n{len(emitted)} people tables   {total:,} rows total")
    print(f"-> {csv_path}")
    json.dump(emitted, open(os.path.join(SCRIPT_DIR, args.out + ".json"), "w",
                            encoding="utf-8"), indent=1, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
