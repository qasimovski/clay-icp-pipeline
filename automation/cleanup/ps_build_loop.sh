#!/bin/sh
# Build Highways P&S category workbooks one at a time, resuming across the
# harness's background kills. Sequential by construction.
#   ps_build_loop.sh "<extra flags>" <logfile>
# "" = configure-only (spends nothing); "--run" materialises the table.
#
# The category list is piped through `tr -d '\r'`: python on Windows writes
# CRLF, and the stray \r made every name a miss. build_ps_companies refused
# each one rather than guessing, so nothing was created — but the sweep did
# nothing for a full chunk.
FLAG="$1"; LOG="$2"
python - <<'PY' | tr -d '\r' > /tmp/ps_cats.txt
import sys; sys.path.insert(0,'.')
import ps_query_config as psq
for n,_ in psq.categories('highways'): print(n)
PY
while IFS= read -r cat; do
  [ -z "$cat" ] && continue
  if grep -qF "BUILT_OK::$cat" "$LOG" 2>/dev/null; then continue; fi
  echo "=== $cat" >> "$LOG"
  if python -u build_ps_companies.py --icp highways "$cat" $FLAG >> "$LOG" 2>&1; then
    echo "BUILT_OK::$cat" >> "$LOG"
  fi
done < /tmp/ps_cats.txt
echo "LOOP DONE"
