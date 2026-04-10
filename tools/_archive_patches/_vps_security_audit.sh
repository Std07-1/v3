#!/bin/bash
echo "=== FAIL2BAN JAILS ==="
sudo fail2ban-client status
echo "=== SSHD JAIL ==="
sudo fail2ban-client status sshd 2>/dev/null
echo "=== NGINX ACCESS TOP IPs ==="
sudo tail -2000 /var/log/nginx/access.log 2>/dev/null | awk '{print $1}' | sort | uniq -c | sort -rn | head -15
echo "=== NGINX 4xx/5xx ==="
sudo tail -5000 /var/log/nginx/access.log 2>/dev/null | awk '{print $9}' | sort | uniq -c | sort -rn | head -10
echo "=== BOT/SCANNER REQUESTS ==="
sudo grep -ciE 'wp-login|xmlrpc|phpmyadmin|\.env|\.git/|/config|/backup|shell\.php|passwd' /var/log/nginx/access.log 2>/dev/null
echo "=== SAMPLE BOT REQUESTS ==="
sudo grep -iE 'wp-login|xmlrpc|phpmyadmin|\.env|\.git/|shell\.php' /var/log/nginx/access.log 2>/dev/null | tail -10
echo "=== SSH CONFIG SECURITY ==="
sudo grep -vE '^\s*#|^\s*$' /etc/ssh/sshd_config | grep -iE 'PermitRoot|PasswordAuth|PubkeyAuth|MaxAuth|UsePAM'
echo "=== SSH CONFIG.D ==="
ls /etc/ssh/sshd_config.d/ 2>/dev/null
for f in /etc/ssh/sshd_config.d/*.conf; do
    echo "--- $f ---"
    cat "$f" 2>/dev/null
done
echo "=== NGINX SECURITY HEADERS ==="
curl -sI http://127.0.0.1:80/ | grep -iE 'x-frame|x-content|strict-transport|content-security|x-xss|referrer-policy|permissions-policy'
echo "=== CLOUDFLARE REAL IP ==="
cat /etc/nginx/conf.d/realip_cloudflare.conf 2>/dev/null
echo "=== FULL NGINX SITE CONFIG ==="
cat /etc/nginx/sites-enabled/smc
echo "=== LOG ROTATION ==="
cat /etc/logrotate.d/smc-v3 2>/dev/null || echo "NO smc-v3 logrotate config"
echo "=== DONE ==="
