#!/usr/bin/env bash
# Delete all run_test.py logs in logs/
cd "$(dirname "$0")" || exit 1
count=$(find logs -maxdepth 1 -name 'run_test_*.log' 2>/dev/null | wc -l | tr -d ' ')
rm -f logs/run_test_*.log
echo "Removed $count run_test log file(s) from logs/"
