#!/usr/bin/env bash
# Quick VPS health audit — read-only, no changes
set +e

echo "=== UPTIME / LOAD ==="
uptime

echo
echo "=== MEMORY ==="
free -h

echo
echo "=== DISK (root + /opt + /var/log) ==="
df -h / /opt /var/log 2>/dev/null

echo
echo "=== TOP RAM CONSUMERS (15) ==="
ps aux --sort=-%mem | head -15

echo
echo "=== NGINX STATUS ==="
sudo systemctl is-active nginx
sudo systemctl status nginx --no-pager | head -15

echo
echo "=== NGINX RESTARTS LAST 7 DAYS ==="
journalctl -u nginx --since '7 days ago' --no-pager 2>/dev/null | grep -iE 'started|stopped|failed|reload' | tail -30

echo
echo "=== NGINX ERROR LOG (last 50, errors only) ==="
sudo tail -200 /var/log/nginx/error.log | grep -iE 'error|crit|emerg|alert' | tail -50

echo
echo "=== UFW STATUS ==="
sudo ufw status | head -25

echo
echo "=== SUPERVISOR PROGRAMS ==="
sudo supervisorctl status 2>&1

echo
echo "=== RECENT OOM KILLS ==="
sudo dmesg -T 2>/dev/null | grep -iE 'killed process|out of memory' | tail -10
[ -z "$(sudo dmesg -T 2>/dev/null | grep -iE 'killed process|out of memory' | tail -1)" ] && echo "(no OOM events found)"

echo
echo "=== SYSTEMD FAILED UNITS ==="
systemctl --failed --no-pager

echo
echo "=== CRON LAST RUNS (root + ubuntu) ==="
sudo grep CRON /var/log/syslog 2>/dev/null | tail -10

echo
echo "=== AUTH LOG: failed/unusual logins ==="
sudo tail -200 /var/log/auth.log | grep -iE 'failed|invalid|break-in|refused' | tail -10

echo
echo "=== DONE ==="
