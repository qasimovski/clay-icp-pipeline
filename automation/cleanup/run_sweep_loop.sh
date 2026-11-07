#!/bin/sh
# Relaunch a resumable apply_highways_cpj sweep until the state file says the
# whole scope is done. Sequential by construction: the next attempt only starts
# after the previous python exits, so two workers can never race the state file
# (that race dropped a state entry on 2026-08-13).
#   run_sweep_loop.sh <table> <column> <target_count> <logfile>
TABLE="$1"; COL="$2"; TARGET="$3"; LOG="$4"
KEY=$(echo "$TABLE" | cut -d_ -f1)
STATE="highways_cpj_state_${KEY}.json"
for attempt in $(seq 1 25); do
  n=$(python -c "import json;d=json.load(open('$STATE'));print(sum(1 for v in d.values() if '$COL' in (v.get('ran_columns') or {})))" 2>/dev/null || echo 0)
  if [ "$n" -ge "$TARGET" ]; then echo "SWEEP COMPLETE: $n/$TARGET ($COL)"; exit 0; fi
  echo "--- attempt $attempt, at $n/$TARGET" >> "$LOG"
  python -u apply_highways_cpj.py --table "$TABLE" --run-columns --column "$COL" >> "$LOG" 2>&1
done
echo "GAVE UP after 25 attempts"; exit 1
