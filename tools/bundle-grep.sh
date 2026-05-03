#!/usr/bin/env bash
BUNDLE=/opt/smc-v3/ui_v4/dist/assets/index-DDg_5hnv.js

echo "=== Bundle size + sha256 ==="
ls -la "$BUNDLE"
sha256sum "$BUNDLE"

echo
echo "=== Word search counts ==="
for w in sendBeacon Beacon "POST" XMLHttpRequest navigator.send "method:" "method :"; do
  c=$(grep -ocF "$w" "$BUNDLE" || echo 0)
  echo "  $w  ->  $c"
done

echo
echo "=== Context around 'POST' (any) ==="
grep -oE '.{40}POST.{60}' "$BUNDLE" | head -20

echo
echo "=== Context around 'method' (any) ==="
grep -oE '.{20}method[^a-zA-Z][^,}]{0,50}' "$BUNDLE" | head -20

echo
echo "=== ALL POST in last 5 min, any IP ==="
sudo grep "POST " /var/log/nginx/access.log | tail -30

echo
echo "=== ARCHI subdomain logs (separate vhost?) ==="
sudo ls -la /var/log/nginx/ | grep -iE "archi|api"

echo
echo "=== nginx vhosts that handle POST anywhere ==="
sudo grep -l "POST\|api" /etc/nginx/sites-enabled/* /etc/nginx/conf.d/*.conf 2>/dev/null
