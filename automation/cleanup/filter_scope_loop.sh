#!/bin/sh
# Resumable Is-New filter sweep over a Highways scope. Sequential; a table that
# already carries a filter is skipped by the pass, so no stacked filters.
#   filter_scope_loop.sh <scope> <target> <logfile>
SCOPE="$1"; TARGET="$2"; LOG="$3"
case "$SCOPE" in
  ps)      STATE=highways_ps_isnew_filter_state.json ;;
  buyside) STATE=highways_bs_isnew_filter_state.json ;;
esac
for attempt in $(seq 1 25); do
  n=$(python -c "import json,os;p='$STATE';d=json.load(open(p)) if os.path.exists(p) else {};print(sum(1 for v in d.values() if v.get('status') in ('filtered','already_filtered')))" 2>/dev/null || echo 0)
  if [ "$n" -ge "$TARGET" ]; then echo "FILTER SWEEP COMPLETE: $n/$TARGET"; exit 0; fi
  echo "--- attempt $attempt, at $n/$TARGET" >> "$LOG"
  python -u apply_isnew_filter.py --scope "$SCOPE" >> "$LOG" 2>&1
done
echo "GAVE UP after 25 attempts"; exit 1
