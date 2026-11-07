"""Print `<table_id>|<name>` for every table in a workbook (run in WSL).

Used as the duplicate guard for the people-search pass: the bottom tab bar
overlaps the last grid row in the UI, so Clay's own table list is the reliable
source of truth.
"""
import sys
import time

import clay_bin


def main():
    # Binary chosen by PARSED version, not lexicographic sort — see clay_bin.
    # Probed once here rather than per page, so a rejected binary costs one
    # call instead of one per cursor.
    b = clay_bin.pick()
    wb = sys.argv[1]
    # the API has no workbook filter, so page through and match client-side
    cursor, seen, last = None, 0, ""
    for _ in range(60):
        args = ["tables", "list", "--limit", "100"]
        if cursor:
            args += ["--cursor", cursor]
        ok, d, err = clay_bin.run(b, args)
        if not ok:
            last = err
            if d is not None:
                break        # real JSON error, retrying will not help
            time.sleep(2)
            continue
        rows = d.get("data") if isinstance(d, dict) else d
        for t in rows:
            seen += 1
            if ((t.get("workbook") or {}).get("id")) == wb:
                print(f"{t.get('id')}|{t.get('name')}")
        cursor = d.get("cursor") if isinstance(d, dict) else None
        if not cursor or not rows:
            return 0
    sys.stderr.write(f"{last}\n")
    return 1


if __name__ == "__main__":
    sys.exit(main())
