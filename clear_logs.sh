#!/usr/bin/env bash
# Delete all .log files in logs/
cd "$(dirname "$0")" || exit 1
count=$(find logs -maxdepth 1 -name '*.log' 2>/dev/null | wc -l | tr -d ' ')
rm -f logs/*.log
echo "Removed $count log file(s) from logs/"
