#!/bin/bash
# Save paper-trading results back to the repo (used by the GitHub workflows).
# Usage: ./publish.sh "message" [--running]
cd "$(dirname "$0")" || exit 1
python3 trade.py status ${2:-} >/dev/null
for p in paper logs docs data/5m data/nifty50.txt; do [ -e "$p" ] && git add -A "$p"; done
git diff --cached --quiet && exit 0
git commit -q -m "$1"
for i in 1 2 3; do
  git pull -q --rebase -X theirs && git push -q && exit 0
  git rebase --abort 2>/dev/null
  sleep 5
done
echo "push failed" >&2; exit 1
