#!/bin/bash
# Watchtower login banner -- displays active findings on SSH login
FINDINGS_FILE="/var/run/watchtower/banner.txt"
if [ -f "$FINDINGS_FILE" ] && [ -s "$FINDINGS_FILE" ]; then
    cat "$FINDINGS_FILE"
fi
