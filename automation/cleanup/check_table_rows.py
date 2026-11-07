"""Print a table's row count (run in WSL). Used to order the rollout smallest-first.

Binary selection goes through `clay_bin`, which sorts by PARSED version. The
`sorted(glob(...))[-1]` this used to do is lexicographic, so it picked
clay-0.6.0 over clay-0.16.0; the server answered `upgrade_required`, every call
failed, and `verify_ps_companies.py` reported all 8 buy-side categories as
"row count unreadable" — a fleet-wide false negative from a string sort.
`check_table_columns.py` and `list_workbook_tables.py` were migrated when that
was first found; this one was missed.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import clay_bin  # noqa: E402


def main():
    for b in clay_bin.candidates():
        for _ in range(4):
            ok, d, _ = clay_bin.run(b, ("tables", "get", sys.argv[1]))
            if not ok:
                if d is not None:
                    break        # real JSON error (upgrade_required): next binary
                time.sleep(2)    # transient: retry the same binary
                continue
            n = d.get("rowCount") if isinstance(d, dict) else None
            if n is None and isinstance(d, dict) and isinstance(d.get("data"), dict):
                n = d["data"].get("rowCount")
            if n is None:
                # A response without a row count is not a zero-row table.
                print("ERROR", file=sys.stderr)
                return 1
            print(n)
            return 0
    # Never print a number here: a CLI failure that looks like "0 rows" makes
    # the caller file the table as empty and drop it from every future batch.
    print("ERROR", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
