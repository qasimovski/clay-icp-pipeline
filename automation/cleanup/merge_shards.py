"""Fold per-shard buyer_state_<slug>_w{i}.json files into the master
buyer_state_<slug>.json (slug = "<entity>_<icp>", e.g. exhibitors_labs).

Shards partition events disjointly (by target index), so each shard file is the
sole authority for its own events — a plain overlay is correct and cannot lose a
master entry (shard records are seeded from master before being updated). Run
this after workers are killed, before relaunching, and for final reporting.

  python merge_shards.py                       # entity=exhibitors icp=labs
  python merge_shards.py --entity sponsors     # different entity
"""
import argparse
import glob
import json
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
import pipeline_config as pcfg  # noqa: E402
import state_io           # noqa: E402  (atomic, fail-loud state files)


def main():
    ap = argparse.ArgumentParser()
    pcfg.add_cli_args(ap)
    args = ap.parse_args()
    slug = pcfg.load(args.entity, args.icp).slug()
    state_path = os.path.join(SCRIPT_DIR, f"buyer_state_{slug}.json")

    master = json.load(open(state_path, encoding="utf-8")) if os.path.exists(state_path) else {}
    merged = 0
    for f in sorted(glob.glob(os.path.join(SCRIPT_DIR, f"buyer_state_{slug}_w*.json"))):
        shard = json.load(open(f, encoding="utf-8"))
        for wid, rec in shard.items():
            master[wid] = rec
            merged += 1
        print(f"  merged {len(shard)} entries from {os.path.basename(f)}")
    state_io.save_json(state_path, master)
    from collections import Counter
    c = Counter(v.get("status") for v in master.values())
    print(f"merged {merged} shard entries -> {os.path.basename(state_path)} now: {dict(c)}")


if __name__ == "__main__":
    main()
