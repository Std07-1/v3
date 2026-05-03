#!/usr/bin/env bash
# One-shot: capture POST / body to identify mystery sender
set -euo pipefail

# 1. Add log_format
sudo tee /etc/nginx/conf.d/post_debug.conf > /dev/null <<'CFG'
log_format post_debug '$time_iso8601 ip=$remote_addr method=$request_method '
                     'uri=$request_uri ua="$http_user_agent" ref="$http_referer" '
                     'ct="$content_type" cl=$content_length body="$request_body"';
CFG

# 2. Patch active site config: insert capture block above current `location / {`
SITE=/etc/nginx/sites-enabled/smc

if grep -q '# POST_DEBUG_BLOCK' "$SITE"; then
  echo "already patched, skipping"
else
  sudo cp "$SITE" "$SITE.bak.$(date +%s)"
  # Insert NEW exact-match for POST / before the existing `location / {`
  sudo python3 - <<'PY'
import re, pathlib
p = pathlib.Path("/etc/nginx/sites-enabled/smc")
src = p.read_text()
patch = '''
    # POST_DEBUG_BLOCK — temporary, capture mystery POST to /
    location = / {
        if ($request_method = POST) {
            access_log /var/log/nginx/post_debug.log post_debug;
            client_body_buffer_size 16k;
            return 204;
        }
        try_files $uri /index.html;
    }

'''
new = src.replace("    location / {", patch + "    location / {", 1)
p.write_text(new)
print("patched")
PY
fi

# 3. Test + reload
sudo nginx -t
sudo systemctl reload nginx
sudo touch /var/log/nginx/post_debug.log
sudo chmod 644 /var/log/nginx/post_debug.log
echo "=== READY ==="
