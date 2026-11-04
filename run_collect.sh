#!/bin/bash
# Started by launchd at 15:45 IST on weekdays. Saves today's bars so the history keeps growing.
cd "$(dirname "$0")" || exit 1
LOG="logs/collect-$(date +%F).log"
{ /usr/bin/python3 trade.py collect --interval 5m; /usr/bin/python3 trade.py collect --interval 1d; } >> "$LOG" 2>&1
