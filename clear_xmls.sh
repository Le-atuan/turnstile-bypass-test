#!/usr/bin/env bash
# Delete all downloaded XML files in logs/xmls/
cd "$(dirname "$0")" || exit 1
count=$(find logs/xmls -maxdepth 1 -name '*.xml' 2>/dev/null | wc -l | tr -d ' ')
rm -f logs/xmls/*.xml
echo "Removed $count XML file(s) from logs/xmls/"
