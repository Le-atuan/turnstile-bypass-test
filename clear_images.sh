#!/usr/bin/env bash
# Delete all failure screenshots/HTML (.png, .html) in logs/
cd "$(dirname "$0")" || exit 1
count=$(find logs -maxdepth 1 \( -name '*.png' -o -name '*.html' \) 2>/dev/null | wc -l | tr -d ' ')
rm -f logs/*.png logs/*.html
echo "Removed $count image/html file(s) from logs/"
