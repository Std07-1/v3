#!/bin/bash
set -e
WSPID=$(sudo supervisorctl status smc:smc-ws | awk '{print $4}' | tr -d ',')
echo "PID=$WSPID"
echo "=== CWD ==="
sudo readlink /proc/$WSPID/cwd
echo "=== CMDLINE ==="
sudo cat /proc/$WSPID/cmdline | tr '\0' ' '; echo
echo "=== ENV (filtered) ==="
sudo cat /proc/$WSPID/environ | tr '\0' '\n' | grep -iE 'config|smc|api|pythonpath|pwd|home' | head -20
echo "=== CONFIG SYMBOLS LIVE ==="
sudo cat /opt/smc-v3/config.json | python3 -c 'import json,sys; c=json.load(sys.stdin); print("symbols:", c.get("symbols")); print("api_v3:", c.get("api_v3"))'
echo "=== ws_server stderr tail 50 ==="
sudo tail -n 50 /var/log/smc-v3/ws_server.stderr.log 2>/dev/null || echo "no log"
echo "=== logs dir ==="
sudo ls /var/log/smc-v3/ 2>/dev/null
