#!/bin/bash
# Started by launchd at 9:10 IST on weekdays. Paper trades until 15:20, then notifies.
cd "$(dirname "$0")" || exit 1
LOG="logs/paper-$(date +%F).log"
/usr/bin/caffeinate -i /usr/bin/python3 -u trade.py paper >> "$LOG" 2>&1
SUMMARY="$(tail -1 "$LOG")"
/usr/bin/osascript -e "display notification \"${SUMMARY//\"/}\" with title \"NSE paper trading\"" 2>/dev/null
