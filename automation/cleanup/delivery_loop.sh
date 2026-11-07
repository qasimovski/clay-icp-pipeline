#!/usr/bin/env bash
# Build the Katie delivery folder in small batches, recycling Chrome between them.
#
# WHY. This machine has ~1.9 GB free of 7.7 GB and Chrome grows with every CSV
# import — measured at 1.25 GB across 9 processes after a dozen tables. Left
# running it wedges or dies, which showed up as "Connection closed while reading
# from the driver", as a browser serving the signed-out page while the session
# was perfectly valid, and as the environment killing the whole pass for memory.
# None of those were Clay problems and none of them needed a code fix; they
# needed a smaller working set.
#
# So: a couple of workbooks per browser, then kill that profile's Chrome and
# start a fresh one. import_ce_delivery_tables.py is resumable per TABLE and
# re-reads what exists from Clay, so a recycle mid-build costs nothing.
#
# Only processes whose command line carries the clay-debug profile are killed —
# never the user's own Chrome windows.
#
#   bash delivery_loop.sh [batches] [workbooks-per-batch] [ce|ps|bs]
set -u
BATCHES="${1:-4}"
LIMIT="${2:-2}"
DELIVERY="${3:-ce}"
CD="/c/Users/qasim/terrapinn/clay-icp-pipeline/automation/cleanup"
CHROME="/c/Users/qasim/AppData/Local/Google/Chrome/Application/chrome.exe"

progress() { python "$CD/delivery_progress.py" "$DELIVERY"; }

# Separate script files on purpose: the inline versions died to quoting. The
# state keys contain `||`, which does not survive bash -> python -c, and the
# PowerShell restart block did not survive bash -> powershell either (it killed
# Chrome and then failed to start it again, leaving the loop with no browser).
restart_chrome() {
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$CD/restart_clay_chrome.ps1"     | tail -1
  curl -s -m 5 http://127.0.0.1:9222/json/version >/dev/null 2>&1
}

cd "$CD" || exit 1
for i in $(seq 1 "$BATCHES"); do
  echo "=== $DELIVERY batch $i/$BATCHES (limit $LIMIT) ==="
  CLAY_USE_CDP=1 timeout 520 python import_ce_delivery_tables.py --delivery "$DELIVERY" --apply --limit "$LIMIT" 2>&1 \
    | grep -E "tables confirmed|left pending|!!|login probe" | tail -4
  progress
  if [ "$i" -lt "$BATCHES" ]; then
    echo "   recycling Chrome"
    restart_chrome || break
  fi
done
echo "=== loop finished ==="
progress
