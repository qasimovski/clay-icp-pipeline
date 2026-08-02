"""Fold per-shard state files (<base>_w{i}.json) into their master
<base>.json. Default base is buyer_state_<slug> (slug = "<entity>_<icp>",
e.g. exhibitors_labs); --base merges any other sharded family, including the
people_email run logs that guard against double-charging.

Shards partition events disjointly (by target index), so each shard file is the
sole authority for its own events — a plain overlay is correct and cannot lose a
master entry (shard records are seeded from master before being updated). Run
this after workers are killed, before relaunching, and for final reporting.

  python merge_shards.py                             # buyer_state, exhibitors/labs
  python merge_shards.py --entity sponsors           # different entity
  python merge_shards.py --base people_email_runs    # merge shard run logs
  python merge_shards.py --base people_email_state
"""
import argparse
import glob
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
import pipeline_config as pcfg  # noqa: E402
import state_io           # noqa: E402  (atomic, fail-loud state files)


def merge_family(base):
    """Overlay every <base>_w*.json onto <base>.json. Returns (path, count)."""
    state_path = os.path.join(SCRIPT_DIR, f"{base}.json")
    master = state_io.load_json(state_path)
    merged = 0
    for f in sorted(glob.glob(os.path.join(SCRIPT_DIR, f"{base}_w*.json"))):
        shard = state_io.load_json(f)
        for key, rec in shard.items():
            master[key] = rec
            merged += 1
        print(f"  merged {len(shard)} entries from {os.path.basename(f)}")
    state_io.save_json(state_path, master)
    return state_path, master, merged


def main():
    ap = argparse.ArgumentParser()
    pcfg.add_cli_args(ap)
    ap.add_argument("--base", help="state-file family to merge (filename stem "
                    "without .json); default buyer_state_<slug>")
    args = ap.parse_args()
    base = args.base or f"buyer_state_{pcfg.load(args.entity, args.icp).slug()}"

    state_path, master, merged = merge_family(base)
    from collections import Counter
    c = Counter((v.get("status") if isinstance(v, dict) else None)
                for v in master.values())
    print(f"merged {merged} shard entries -> {os.path.basename(state_path)} "
          f"now: {dict(c)}")


if __name__ == "__main__":
    main()
