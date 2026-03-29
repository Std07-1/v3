#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════
# VPS Cold Start Setup — Trading Platform v3
# Run ON the VPS after: scp data_v3_migration.tar.gz root@<vps>:/root/
# ═══════════════════════════════════════════════════════
set -euo pipefail

APP_DIR="/opt/aione-v3"
REPO_URL="https://github.com/Std07-1/v3.git"
PYTHON_MIN="3.12"

echo "═══ Step 1: System packages ═══"
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip redis-server git curl

echo "═══ Step 2: Redis check ═══"
systemctl enable redis-server
systemctl start redis-server
redis-cli -n 1 PING || { echo "FATAL: Redis not responding"; exit 1; }
echo "Redis OK"

echo "═══ Step 3: Clone repo ═══"
mkdir -p "$APP_DIR"
if [ -d "$APP_DIR/.git" ]; then
    echo "Repo exists, pulling latest..."
    cd "$APP_DIR"
    git pull --ff-only
else
    echo "Cloning $REPO_URL ..."
    echo "If private repo — use: git clone https://<TOKEN>@github.com/Std07-1/v3.git $APP_DIR"
    git clone "$REPO_URL" "$APP_DIR" || {
        echo "FATAL: git clone failed. For private repos, generate a GitHub PAT:"
        echo "  https://github.com/settings/tokens → Generate (repo scope)"
        echo "  Then run: git clone https://<PAT>@github.com/Std07-1/v3.git $APP_DIR"
        exit 1
    }
    cd "$APP_DIR"
fi

echo "═══ Step 4: Python venv ═══"
PYTHON_BIN=$(command -v python3.14 || command -v python3.13 || command -v python3.12 || command -v python3)
PY_VER=$($PYTHON_BIN --version 2>&1 | grep -oP '\d+\.\d+')
echo "Using Python: $PYTHON_BIN ($PY_VER)"

if [ ! -d ".venv" ]; then
    $PYTHON_BIN -m venv .venv
fi
source .venv/bin/activate
pip install --upgrade pip -q
pip install -r requirements.txt -q
echo "Deps installed OK"

echo "═══ Step 5: Extract data ═══"
DATA_ARCHIVE="/root/data_v3_migration.tar.gz"
if [ -f "$DATA_ARCHIVE" ]; then
    tar -xzf "$DATA_ARCHIVE" -C "$APP_DIR/"
    echo "Data extracted:"
    for sym in XAU_USD XAG_USD BTCUSDT ETHUSDT; do
        COUNT=$(find "$APP_DIR/data_v3/$sym" -name "*.jsonl" | wc -l)
        echo "  $sym: $COUNT files"
    done
else
    echo "WARNING: $DATA_ARCHIVE not found — upload it first!"
fi

echo "═══ Step 6: .env setup ═══"
if [ ! -f "$APP_DIR/.env" ]; then
    cp "$APP_DIR/.env.example" "$APP_DIR/.env"
    echo "CREATED .env from example — EDIT secrets before starting!"
    echo "  nano $APP_DIR/.env"
else
    echo ".env already exists"
fi

echo "═══ Step 7: Verify ═══"
cd "$APP_DIR"
source .venv/bin/activate
python -c "
import json
from pathlib import Path
syms = ['XAU_USD','XAG_USD','BTCUSDT','ETHUSDT']
tfs = [60,180,300,900,1800,3600,14400,86400]
ok = 0
for s in syms:
    for t in tfs:
        d = Path(f'data_v3/{s}/tf_{t}')
        if d.exists() and list(d.glob('*.jsonl')):
            ok += 1
print(f'Data check: {ok}/32 TF dirs present')
assert ok == 32, f'FAIL: only {ok}/32'
print('PASS')
"
redis-cli -n 1 PING > /dev/null
echo "Redis: OK"

echo ""
echo "═══════════════════════════════════════════"
echo "  SETUP COMPLETE"
echo "═══════════════════════════════════════════"
echo ""
echo "Next steps:"
echo "  1. Edit secrets:  nano $APP_DIR/.env"
echo "  2. Test cold start:  cd $APP_DIR && source .venv/bin/activate"
echo "     python -m app.main --mode all --stdio pipe"
echo "  3. Health check:  curl http://127.0.0.1:8000/api/status"
echo ""
