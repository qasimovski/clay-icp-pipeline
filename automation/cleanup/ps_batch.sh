#!/usr/bin/env bash
# One delivery batch, run in the FOREGROUND so the harness does not reap it.
#
# Background runs of delivery_loop.sh kept losing their python child at this
# environment's kill cadence, and two drivers at once pull Chrome out from
# under each other. So: one batch, one driver, observable.
#
#   bash ps_batch.sh <ce|ps|bs> [workbooks]
set -u
D="${1:-ps}"
N="${2:-3}"
CD="/c/Users/qasim/terrapinn/clay-icp-pipeline/automation/cleanup"
SP="C:/Users/qasim/AppData/Local/Temp/claude/C--Users-qasim-terrapinn-clay-icp-pipeline/d258de6f-cd14-4f74-842f-c69d19e8c871/scratchpad"
cd "$CD" || exit 1

# HEADLESS=1 recycles Chrome with --headless=new on the SAME clay-debug profile:
# same logged-in session, no window, ~0.3 GB less resident. Verified against
# Clay — it does not trip the bot-block, because we reuse an existing session
# rather than logging in (a Playwright-LAUNCHED browser is what Clay blocks).
# Unset it to go back to a visible browser if Clay ever starts objecting.
RESTART=restart_clay_chrome.ps1
[ "${HEADLESS:-0}" = "1" ] && RESTART=restart_clay_chrome_headless.ps1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$RESTART" | tail -1
# Warm the fresh browser: the first CDP connect after a restart reliably fails
# with "Target page ... has been closed", and this absorbs that instead of
# spending the batch on it.
CLAY_USE_CDP=1 timeout 120 python -u "$SP/whoami.py" 2>/dev/null | tail -1

CLAY_USE_CDP=1 PYTHONUNBUFFERED=1 timeout 460 python -u import_ce_delivery_tables.py \
  --delivery "$D" --apply --limit "$N" 2>&1 \
  | grep -viE "^\s+at |coreBundle|node:internal|site-packages|\^\^\^|^  File " \
  | grep -E "confirmed|tables confirmed|left pending|!!|not logged|^[A-Za-z].*\((new|existing)" | tail -12
python delivery_progress.py "$D"
