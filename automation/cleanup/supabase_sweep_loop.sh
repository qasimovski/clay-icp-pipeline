#!/bin/sh
# Relaunch the resumable Supabase pass until the state file says the scope is
# done. Sequential by construction: the next attempt only starts after the
# previous python exits, so two workers can never race the state file.
#   supabase_sweep_loop.sh <table> <configure|run> <target_count> <logfile>
TABLE="$1"; MODE="$2"; TARGET="$3"; LOG="$4"
KEY=$(echo "$TABLE" | cut -d_ -f1)
STATE="highways_supabase_state_${KEY}.json"
if [ "$MODE" = "run" ]; then FLAG="--run-only"; COUNT="v.get('ran')";
else FLAG="--configure-only"; COUNT="v.get('status')=='configured'"; fi
for attempt in $(seq 1 25); do
  n=$(python -c "import json,os;p='$STATE';d=json.load(open(p)) if os.path.exists(p) else {};print(sum(1 for v in d.values() if $COUNT))" 2>/dev/null || echo 0)
  if [ "$n" -ge "$TARGET" ]; then echo "SWEEP COMPLETE ($MODE): $n/$TARGET"; exit 0; fi
  echo "--- attempt $attempt, at $n/$TARGET" >> "$LOG"
  python -u apply_highways_supabase.py --table "$TABLE" $FLAG >> "$LOG" 2>&1
done
echo "GAVE UP after 25 attempts"; exit 1
