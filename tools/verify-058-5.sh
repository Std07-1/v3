#!/bin/bash
set -u
TOKEN="tk_84a1dc64aebf59654d070ce7f896b3fcf549d450eee7bccf35bda6e63dd867b6"
URL="https://aione-smc.com/api/v3/macro/context"

sudo systemctl reload nginx
sleep 2

echo "=== GET valid (expect 200) ==="
curl -sI -H "X-API-Key: ${TOKEN}" "${URL}" | head -8

echo
echo "=== POST denied (expect 405) ==="
curl -sI -X POST -H "X-API-Key: ${TOKEN}" "${URL}" | head -3

echo
echo "=== DELETE denied (expect 405) ==="
curl -sI -X DELETE -H "X-API-Key: ${TOKEN}" "${URL}" | head -3

echo
echo "=== Body disclaimer + envelope ==="
curl -s -H "X-API-Key: ${TOKEN}" "${URL}" | python3 -c '
import sys, json
d = json.load(sys.stdin)
print("schema_version:", d.get("schema_version"))
print("kind:", d.get("kind"))
print("disclaimer:", (d.get("disclaimer") or "MISSING")[:100])
'

echo
echo "=== nginx access log tail (last 3 lines) ==="
sudo tail -n 3 /var/log/nginx/access.log
