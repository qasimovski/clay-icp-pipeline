#!/bin/sh
# Resumable Supabase pass over a Highways P&S / BuySide scope.
#   supabase_scope_loop.sh <scope> <configure|run> <target> <logfile>
SCOPE="$1"; MODE="$2"; TARGET="$3"; LOG="$4"
if [ "$MODE" = "run" ]; then FLAG="--run-only"; KEY="v.get('ran')";
else FLAG="--configure-only"; KEY="v.get('status')=='configured'"; fi
case "$SCOPE" in
  ps)      STATE=highways_ps_supabase_state.json ;;
  buyside) STATE=highways_bs_supabase_state.json ;;
esac
for attempt in $(seq 1 25); do
  n=$(python -c "import json,os;p='$STATE';d=json.load(open(p)) if os.path.exists(p) else {};print(sum(1 for v in d.values() if $KEY))" 2>/dev/null || echo 0)
  if [ "$n" -ge "$TARGET" ]; then echo "SCOPE $SCOPE $MODE COMPLETE: $n/$TARGET"; exit 0; fi
  echo "--- attempt $attempt, at $n/$TARGET" >> "$LOG"
  python -u apply_highways_supabase.py --scope "$SCOPE" $FLAG >> "$LOG" 2>&1
done
echo "GAVE UP after 25 attempts"; exit 1
