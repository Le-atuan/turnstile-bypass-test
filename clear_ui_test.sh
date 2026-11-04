#!/usr/bin/env bash
# Delete all run_ui_test.py / run_ui_test_uc.py logs in logs/
cd "$(dirname "$0")" || exit 1
count=$(find logs -maxdepth 1 -name 'ui_test_*.log' 2>/dev/null | wc -l | tr -d ' ')
rm -f logs/ui_test_*.log
echo "Removed $count ui_test log file(s) from logs/"
