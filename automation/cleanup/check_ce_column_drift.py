"""Compare the live Competitive Events tables against the cached column sets.

Run after someone edits the tables by hand. Answers three things:

  removed    columns that were there when we cached and are gone now
  added      columns that appeared since
  impact     whether anything the pipeline DEPENDS ON was removed

The last one is the point. `ce_combine_cache.json` is only a snapshot for
rebuilding the CSV quickly, so drift there is harmless — `--refresh` fixes it.
What is not harmless is losing a column the pass reads: the feed builder needs
`Company Name`, `Domain`, `Description`, `Description (2)`, `Primary Offerings`,
`Event`, `Year` and `Is New`, and the gate logic needs `Domain` and `Is New`
specifically. Deleting one of those changes what a future run would classify,
silently.

Read-only and credit-free.

    python check_ce_column_drift.py
"""

import io
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import state_io                      # noqa: E402
import audit_ce_classification as A  # noqa: E402
import ce_classification_rollout as R  # noqa: E402

CACHE = os.path.join(SCRIPT_DIR, "ce_combine_cache.json")

# What the pass actually reads. Losing any of these changes behaviour.
CRITICAL = set(R.FEED_KEYS) | {"Domain", "Is New"}


def main():
    cache = state_io.load_json(CACHE)
    if not cache:
        raise SystemExit(f"no cache at {CACHE}; nothing to compare against")

    scope = []
    for kind in ("exhibitors", "sponsors"):
        mf = os.path.join(SCRIPT_DIR, f"highways_ce_{kind}_manifest.json")
        if not os.path.exists(mf):
            continue
        for event, rec in state_io.load_json(mf).items():
            if event not in A.SKIP_EVENTS:
                scope.append((f"{event}|{rec['table_name']}", rec["table_id"]))
    scope.sort()

    removed_all, added_all = {}, {}
    critical_hits, unreadable = [], []
    for key, tid in scope:
        cached = cache.get(key, {}).get("order")
        if not cached:
            continue
        try:
            live = [c["name"] for c in A.clay(f"tables columns list {tid}")["data"]]
        except A.ClayReadError as e:
            unreadable.append((key, str(e)[:60]))
            continue
        removed = [c for c in cached if c not in live]
        added = [c for c in live if c not in cached]
        if removed:
            removed_all[key] = removed
        if added:
            added_all[key] = added
        hit = [c for c in removed if c in CRITICAL]
        if hit:
            critical_hits.append((key, hit))

    print(f"compared {len(scope)} tables against the cache\n")

    def summarise(title, d):
        if not d:
            print(f"{title}: none")
            return
        counts = {}
        for cols in d.values():
            for c in cols:
                counts[c] = counts.get(c, 0) + 1
        print(f"{title} ({len(d)} tables affected):")
        for c, n in sorted(counts.items(), key=lambda x: -x[1]):
            print(f"   {n:3} tables   {c}")

    summarise("REMOVED", removed_all)
    print()
    summarise("ADDED", added_all)

    if unreadable:
        print(f"\nUNREADABLE ({len(unreadable)}):")
        for k, e in unreadable:
            print(f"   {k}: {e}")

    print()
    if critical_hits:
        print(f"!! PIPELINE-CRITICAL COLUMNS REMOVED on {len(critical_hits)} table(s):")
        for k, cols in critical_hits:
            print(f"   {k}: {cols}")
        print("\n   The feed builder and the Is New / Domain gates read these.")
        print("   A future run against these tables would behave differently.")
    else:
        print("No pipeline-critical column was removed "
              "(feed keys and both gate columns intact).")

    if removed_all or added_all:
        print("\nThe combine cache is now stale — rebuild with:")
        print("   python combine_ce_tables.py --refresh")


if __name__ == "__main__":
    main()
