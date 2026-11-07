#!/bin/sh
# Build the People table for each Highways P&S category, resuming across the
# harness's background kills. Sequential by construction.
#   people_build_loop.sh "<extra flags>" <logfile>
#
# SKIPS "Tolling & Road User Charging": its Companies view is 0/258 after the
# Is New filter — every company was already recorded under ITS, Parking
# Technology or Transport Operations — so Clay rejects the query outright
# ("The selected table field is empty"). Retrying it just burns the loop.
FLAG="$1"; LOG="$2"
python - <<'PY' | tr -d '\r' > /tmp/hw_people_cats.txt
import sys; sys.path.insert(0,'.')
import ps_query_config as psq
for n,_ in psq.categories('highways'):
    if n != "Tolling & Road User Charging":
        print(n)
PY
while IFS= read -r cat; do
  [ -z "$cat" ] && continue
  if grep -qF "PEOPLE_OK::$cat" "$LOG" 2>/dev/null; then continue; fi
  echo "=== $cat" >> "$LOG"
  if python -u build_ps_people.py --icp highways "$cat" $FLAG >> "$LOG" 2>&1; then
    echo "PEOPLE_OK::$cat" >> "$LOG"
  fi
done < /tmp/hw_people_cats.txt
echo "LOOP DONE"
