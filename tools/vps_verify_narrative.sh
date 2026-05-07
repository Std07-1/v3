#!/bin/bash
TOKEN="tk_0a673a22d89c5b0d47b14c49da9bbcef45c7582d4c2cf2e51a2b10d8f2d50f68"
echo "=== /narrative/snapshot ==="
curl -s -o /tmp/narr.json -w "HTTP %{http_code}\n" \
  -H "X-API-Key: $TOKEN" \
  "http://127.0.0.1:8000/api/v3/narrative/snapshot?symbol=XAU%2FUSD&tf=900"
echo "--- body (first 800B) ---"
head -c 800 /tmp/narr.json
echo
echo
echo "=== /smc/levels ==="
curl -s -o /tmp/lvl.json -w "HTTP %{http_code}\n" \
  -H "X-API-Key: $TOKEN" \
  "http://127.0.0.1:8000/api/v3/smc/levels?symbol=XAU%2FUSD&tf=900"
head -c 400 /tmp/lvl.json
echo
echo
echo "=== /smc/zones ==="
curl -s -o /tmp/zns.json -w "HTTP %{http_code}\n" \
  -H "X-API-Key: $TOKEN" \
  "http://127.0.0.1:8000/api/v3/smc/zones?symbol=XAU%2FUSD&tf=900"
head -c 400 /tmp/zns.json
echo
