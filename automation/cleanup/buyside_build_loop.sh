#!/bin/sh
# Build Highways BUY-SIDE category workbooks one at a time, resuming across
# background kills. Sequential by construction. See ps_build_loop.sh.
#   buyside_build_loop.sh "<extra flags>" <logfile>
FLAG="$1"; LOG="$2"
python - <<'PY' | tr -d '\r' > /tmp/bs_cats.txt
import sys; sys.path.insert(0,'.')
import ps_query_config as psq
for n,_ in psq.categories('highways_buyside'): print(n)
PY
while IFS= read -r cat; do
  [ -z "$cat" ] && continue
  if grep -qF "BUILT_OK::$cat" "$LOG" 2>/dev/null; then continue; fi
  echo "=== $cat" >> "$LOG"
  if python -u build_ps_companies.py --icp highways_buyside "$cat" $FLAG >> "$LOG" 2>&1; then
    echo "BUILT_OK::$cat" >> "$LOG"
  fi
done < /tmp/bs_cats.txt
echo "LOOP DONE"
