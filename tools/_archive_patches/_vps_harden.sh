#!/bin/bash
# VPS Security Hardening Script
# Run on aione-vps as ubuntu user
set -e

echo "=== 1. NGINX HARDENED CONFIG ==="
sudo cp /tmp/smc-nginx-v4-hardened.conf /etc/nginx/sites-enabled/smc
sudo nginx -t && sudo systemctl reload nginx
echo "NGINX: OK"

echo "=== 2. FAIL2BAN NGINX JAILS ==="
# Create nginx-botsearch filter (blocks bot/scanner access)
sudo tee /etc/fail2ban/filter.d/nginx-botsearch.conf > /dev/null << 'EOF'
[Definition]
failregex = ^<HOST> .* "(GET|POST|HEAD) /(wp-login|xmlrpc|phpmyadmin|\.env|\.git|admin|cgi-bin).*" (4\d\d|5\d\d)
ignoreregex =
EOF

# Create nginx-req-limit filter (rate limit abuse)
sudo tee /etc/fail2ban/filter.d/nginx-req-limit.conf > /dev/null << 'EOF'
[Definition]
failregex = limiting requests, excess:.* by zone.*client: <HOST>
ignoreregex =
EOF

# Add nginx jails to fail2ban
sudo tee /etc/fail2ban/jail.d/nginx.conf > /dev/null << 'EOF'
[nginx-botsearch]
enabled  = true
port     = http,https
filter   = nginx-botsearch
logpath  = /var/log/nginx/access.log
maxretry = 3
findtime = 600
bantime  = 86400
action   = %(action_)s

[nginx-req-limit]
enabled  = true
port     = http,https
filter   = nginx-req-limit
logpath  = /var/log/nginx/error.log
maxretry = 5
findtime = 60
bantime  = 3600
action   = %(action_)s
EOF

sudo fail2ban-client reload
echo "FAIL2BAN: OK"

echo "=== 3. LOGROTATE FOR SMC-V3 ==="
sudo tee /etc/logrotate.d/smc-v3 > /dev/null << 'EOF'
/var/log/smc-v3/*.log {
    daily
    rotate 7
    compress
    delaycompress
    missingok
    notifempty
    create 0644 root root
    postrotate
        supervisorctl signal HUP smc-v3 > /dev/null 2>&1 || true
    endscript
}
EOF
echo "LOGROTATE: OK"

echo "=== 4. HARDEN SSH ==="
# Ensure MaxAuthTries is set (reduce brute-force window)
if ! grep -q "MaxAuthTries" /etc/ssh/sshd_config.d/99-hardening.conf; then
    echo "MaxAuthTries 3" | sudo tee -a /etc/ssh/sshd_config.d/99-hardening.conf
    echo "LoginGraceTime 20" | sudo tee -a /etc/ssh/sshd_config.d/99-hardening.conf
    sudo systemctl reload sshd
    echo "SSH hardened: MaxAuthTries=3, LoginGraceTime=20s"
else
    echo "SSH: already hardened"
fi

echo "=== 5. JOURNAL SIZE LIMIT ==="
# Prevent journal from growing to 2+GB again
sudo mkdir -p /etc/systemd/journald.conf.d/
sudo tee /etc/systemd/journald.conf.d/size-limit.conf > /dev/null << 'EOF'
[Journal]
SystemMaxUse=100M
SystemKeepFree=1G
MaxRetentionSec=7day
EOF
sudo systemctl restart systemd-journald
echo "JOURNAL: limited to 100M / 7 days"

echo "=== 6. VERIFY ==="
sudo nginx -t
sudo fail2ban-client status
sudo fail2ban-client status sshd
echo "=== ALL DONE ==="
