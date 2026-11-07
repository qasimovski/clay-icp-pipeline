#!/bin/sh
# Relaunch the resumable APPLY sweep until every table in scope is applied.
# Sequential: never two workers on one state file. See run_sweep_loop.sh.
#   apply_sweep_loop.sh <table> <target_count> <logfile>
TABLE="$1"; TARGET="$2"; LOG="$3"
KEY=$(echo "$TABLE" | cut -d_ -f1)
STATE="highways_cpj_state_${KEY}.json"
for attempt in $(seq 1 25); do
  n=$(python -c "import json,os;p='$STATE';d=json.load(open(p)) if os.path.exists(p) else {};print(sum(1 for v in d.values() if v.get('status')=='applied'))" 2>/dev/null || echo 0)
  if [ "$n" -ge "$TARGET" ]; then echo "APPLY COMPLETE: $n/$TARGET"; exit 0; fi
  echo "--- attempt $attempt, at $n/$TARGET" >> "$LOG"
  python -u apply_highways_cpj.py --table "$TABLE" >> "$LOG" 2>&1
done
echo "GAVE UP after 25 attempts"; exit 1
