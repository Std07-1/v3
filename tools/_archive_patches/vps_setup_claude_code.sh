#!/bin/bash
# Setup script for Claude Code on VPS
# Run: bash /tmp/vps_setup_claude_code.sh

set -e

echo "=== 1. Fix .bashrc ==="
# Remove garbled API key line (if still present)
sed -i '/^export ANTHROPIC_API_KEY=tuzVCY/d' /home/ubuntu/.bashrc
# Remove broken alias line (if present)
sed -i '/^alias archi=cd/d' /home/ubuntu/.bashrc
# Remove old comment (if present)
sed -i '/^# Claude Code: launch from/d' /home/ubuntu/.bashrc

# Add proper alias
cat >> /home/ubuntu/.bashrc << 'EOF'

# Claude Code: launch from trader-v3 project dir
alias archi='cd /opt/smc-trader-v3 && claude'
EOF

echo "bashrc alias added"

echo "=== 2. Create settings.json ==="
mkdir -p /home/ubuntu/.claude
cat > /home/ubuntu/.claude/settings.json << 'EOF'
{
  "permissions": {
    "allow": [
      "Bash(cat:*)",
      "Bash(ls:*)",
      "Bash(head:*)",
      "Bash(tail:*)",
      "Bash(grep:*)",
      "Bash(find:*)",
      "Bash(wc:*)",
      "Bash(stat:*)",
      "Bash(du:*)",
      "Bash(diff:*)",
      "Bash(sort:*)",
      "Bash(uniq:*)",
      "Bash(python -m pytest:*)",
      "Bash(supervisorctl status:*)",
      "Bash(curl http://127.0.0.1:*)",
      "Bash(redis-cli:*)",
      "Bash(ps:*)",
      "Bash(date:*)",
      "Bash(pwd:*)",
      "Bash(echo:*)",
      "Bash(tree:*)"
    ],
    "deny": [
      "Bash(rm -rf:*)",
      "Bash(dd:*)",
      "Bash(mkfs:*)",
      "Bash(shutdown:*)",
      "Bash(reboot:*)"
    ]
  }
}
EOF
echo "settings.json created"

echo "=== 3. Verify ==="
echo "Alias:"
grep "^alias archi" /home/ubuntu/.bashrc
echo "Settings:"
cat /home/ubuntu/.claude/settings.json | python3 -c "import sys,json; json.load(sys.stdin); print('Valid JSON')"
echo "Stale files:"
ls /home/ubuntu/*.py 2>/dev/null || echo "None (good)"

echo "=== DONE ==="
