#!/bin/sh
# Build the People table for each Highways BUY-SIDE category, resuming across
# background kills. Sequential by construction.
#   buyside_people_loop.sh "<extra flags>" <logfile> [exclude regex]
FLAG="$1"; LOG="$2"; EXCL="$3"
python - <<'PY' | tr -d '\r' > /tmp/hw_bs_cats.txt
import sys; sys.path.insert(0,'.')
import ps_buyside_people_config as bpc
for c in bpc.load('highways_buyside')['categories']: print(c['name'])
PY
while IFS= read -r cat; do
  [ -z "$cat" ] && continue
  if [ -n "$EXCL" ] && echo "$cat" | grep -qE "$EXCL"; then continue; fi
  if grep -qF "PEOPLE_OK::$cat" "$LOG" 2>/dev/null; then continue; fi
  echo "=== $cat" >> "$LOG"
  if python -u build_ps_people.py --icp highways_buyside "$cat" $FLAG >> "$LOG" 2>&1; then
    echo "PEOPLE_OK::$cat" >> "$LOG"
  fi
done < /tmp/hw_bs_cats.txt
echo "LOOP DONE"
