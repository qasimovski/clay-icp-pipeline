"""Print `<name>|<rowCount>` for every table in a workbook (run in WSL).

`list_workbook_tables.py` gives ids and names; getting counts then meant a
second process per table via `check_table_rows.py`. Verifying a six-table
workbook after every import therefore cost ~40 WSL process spawns, which on a
memory-constrained machine contributed to the driver connection drops that kept
interrupting the delivery import — not merely to its slowness.

This does both in ONE process: page the table list once, and fetch the row count
for the matching tables only.

A count that cannot be read prints as empty rather than 0. A zero-row table and
an unreadable one must never look alike — the caller uses this to decide whether
an import landed, and "0" would read as a successful empty import.

    python3 list_workbook_table_rows.py <workbookId>
"""

import sys
import time
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import clay_bin  # noqa: E402


def rows_for(binary, tid):
    for _ in range(3):
        ok, d, _ = clay_bin.run(binary, ("tables", "get", tid))
        if ok and isinstance(d, dict):
            n = d.get("rowCount")
            if n is None and isinstance(d.get("data"), dict):
                n = d["data"].get("rowCount")
            return n
        time.sleep(2)
    return None


def main():
    if len(sys.argv) < 2:
        sys.stderr.write("usage: list_workbook_table_rows.py <workbookId>\n")
        return 2
    wb = sys.argv[1]
    binary = clay_bin.pick()
    cursor, found = None, []
    for _ in range(60):
        args = ["tables", "list", "--limit", "100"]
        if cursor:
            args += ["--cursor", cursor]
        ok, d, err = clay_bin.run(binary, args)
        if not ok:
            if d is not None:
                sys.stderr.write(f"{err}\n")
                return 1
            time.sleep(2)
            continue
        rows = d.get("data") if isinstance(d, dict) else d
        for t in rows or []:
            if ((t.get("workbook") or {}).get("id")) == wb:
                found.append((t.get("id"), t.get("name")))
        cursor = d.get("cursor") if isinstance(d, dict) else None
        if not cursor or not rows:
            break
    for tid, name in found:
        n = rows_for(binary, tid)
        print(f"{name}|{'' if n is None else n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
