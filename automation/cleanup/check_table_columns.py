"""Print one table's column names, one per line (run in WSL). Used by the
rollout's pre-apply guard to ask Clay whether a table is already built."""
import sys
import time

import clay_bin


def main():
    # clay_bin.candidates() is newest-by-PARSED-version, not lexicographic.
    # The old sorted()[-1] here picked clay-0.6.0 over clay-0.16.0 and the
    # server answered upgrade_required, which this script reported as "no
    # columns" — and the caller's guard reads that as "already applied".
    args = ("tables", "columns", "list", sys.argv[1])
    last = ""
    for b in clay_bin.candidates():
        for _ in range(3):
            ok, d, err = clay_bin.run(b, args)
            if ok:
                data = d.get("data") if isinstance(d, dict) else d
                for col in data:
                    print(col.get("name"))
                return 0
            last = err
            if d is not None:
                break        # a real JSON error (e.g. upgrade_required): next binary
            time.sleep(2)    # transient: retry the same binary
    sys.stderr.write(f"no usable clay binary: {last}\n")
    return 1


if __name__ == "__main__":
    sys.exit(main())
