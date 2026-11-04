#!/bin/bash
# Turn the daily schedule on or off (macOS launchd, your user account only).
#   ./schedule.sh on      paper trading 9:10 IST + data collection 15:45 IST, Mon-Fri
#   ./schedule.sh off     remove both jobs
#   ./schedule.sh status
set -e
D="$(cd "$(dirname "$0")" && pwd)"
LA="$HOME/Library/LaunchAgents"
JOBS="com.nse-day-trader.paper com.nse-day-trader.collect"

write_plist() { # label script hour minute
  local days=""
  for w in 1 2 3 4 5; do
    days="$days<dict><key>Weekday</key><integer>$w</integer><key>Hour</key><integer>$3</integer><key>Minute</key><integer>$4</integer></dict>"
  done
  cat > "$LA/$1.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>$1</string>
<key>ProgramArguments</key><array><string>/bin/bash</string><string>$D/$2</string></array>
<key>StartCalendarInterval</key><array>$days</array>
<key>StandardErrorPath</key><string>$D/logs/launchd-$1.err</string>
</dict></plist>
EOF
  plutil -lint -s "$LA/$1.plist"
}

case "$1" in
  on)
    mkdir -p "$LA" "$D/logs"
    write_plist com.nse-day-trader.paper run_paper.sh 9 10
    write_plist com.nse-day-trader.collect run_collect.sh 15 45
    for j in $JOBS; do
      launchctl bootout "gui/$(id -u)/$j" 2>/dev/null || true
      launchctl bootstrap "gui/$(id -u)" "$LA/$j.plist"
    done
    echo "On. Paper trading starts 9:10 IST and data is saved at 15:45 IST, Mon-Fri."
    echo "The Mac must be awake with the lid open at 9:10. Logs: $D/logs/" ;;
  off)
    for j in $JOBS; do
      launchctl bootout "gui/$(id -u)/$j" 2>/dev/null || true
      rm -f "$LA/$j.plist"
    done
    echo "Off. Both jobs removed." ;;
  status)
    launchctl list | grep nse-day-trader || echo "Not scheduled." ;;
  *) echo "usage: $0 on|off|status"; exit 2 ;;
esac
