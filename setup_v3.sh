#!/usr/bin/env bash
# =============================================================================
# setup_v3.sh — SMC Trader Bot v3 Deploy Script
# ADR-0045
#
# Запускай на VPS: bash setup_v3.sh
# Prerequisites: Ubuntu 22.04+, Python 3.11+, supervisor installed
#
# Що робить:
#   1. Створює /opt/smc-trader-v3/ зі структурою
#   2. Копіює smc_trader_v3.py
#   3. Встановлює Python venv + deps
#   4. Створює .env.example (заповни TELEGRAM_BOT_TOKEN, CHAT_ID, API_KEY)
#   5. Встановлює supervisor config
#   6. Запускає бота
#
# НЕ ЧІПАЄ існуючий /opt/smc-v3/ — платформа та бот незалежні процеси.
# =============================================================================

set -euo pipefail

# ─── config ───────────────────────────────────────────────────────────────────

BOT_DIR="/opt/smc-trader-v3"
VENV_DIR="$BOT_DIR/.venv"
BOT_SCRIPT="$BOT_DIR/smc_trader_v3.py"
LOG_DIR="/var/log/smc-trader-v3"
SUPERVISOR_CONF="/etc/supervisor/conf.d/smc-trader-v3.conf"
RUN_USER="${SUDO_USER:-ubuntu}"
PYTHON_MIN="3.11"

# Source file location (run from project root)
SOURCE_SCRIPT="$(dirname "$(realpath "$0")")/smc_trader_v3.py"

# ─── helpers ──────────────────────────────────────────────────────────────────

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'

info()    { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

check_root() {
    if [[ $EUID -ne 0 ]]; then
        error "Run as root: sudo bash setup_v3.sh"
    fi
}

check_python() {
    local py
    py="$(command -v python3 || true)"
    if [[ -z "$py" ]]; then
        error "python3 not found. Install: sudo apt install python3.11"
    fi
    local ver
    ver="$($py -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
    info "Python $ver found at $py"
    python3 -c "
import sys
major, minor = sys.version_info[:2]
req_major, req_minor = map(int, '$PYTHON_MIN'.split('.'))
if (major, minor) < (req_major, req_minor):
    print(f'ERROR: Python {major}.{minor} < required $PYTHON_MIN')
    sys.exit(1)
"
}

check_supervisor() {
    if ! command -v supervisorctl &>/dev/null; then
        warn "supervisor not found. Installing..."
        apt-get install -y supervisor
    fi
    info "supervisor: $(supervisorctl --version 2>&1 | head -1)"
}

check_source() {
    if [[ ! -f "$SOURCE_SCRIPT" ]]; then
        error "smc_trader_v3.py not found at $SOURCE_SCRIPT. Run from project root."
    fi
}

# ─── step 1: directories ──────────────────────────────────────────────────────

setup_dirs() {
    info "Creating directories..."
    mkdir -p "$BOT_DIR/data"
    mkdir -p "$BOT_DIR/logs"
    mkdir -p "$LOG_DIR"

    chown -R "$RUN_USER:$RUN_USER" "$BOT_DIR"
    chown -R "$RUN_USER:$RUN_USER" "$LOG_DIR"
    info "Directories: $BOT_DIR, $LOG_DIR"
}

# ─── step 2: copy bot script ──────────────────────────────────────────────────

copy_script() {
    info "Copying smc_trader_v3.py..."
    cp "$SOURCE_SCRIPT" "$BOT_SCRIPT"
    chown "$RUN_USER:$RUN_USER" "$BOT_SCRIPT"
    chmod 644 "$BOT_SCRIPT"
    info "Copied to $BOT_SCRIPT"
}

# ─── step 3: python venv ──────────────────────────────────────────────────────

setup_venv() {
    info "Creating Python venv at $VENV_DIR..."
    if [[ -d "$VENV_DIR" ]]; then
        warn "venv already exists, upgrading deps"
    else
        python3 -m venv "$VENV_DIR"
        chown -R "$RUN_USER:$RUN_USER" "$VENV_DIR"
    fi

    # Install/upgrade deps
    info "Installing dependencies..."
    su - "$RUN_USER" -c "
        $VENV_DIR/bin/pip install --quiet --upgrade pip
        $VENV_DIR/bin/pip install --quiet \
            'aiogram>=3.7,<4.0' \
            'anthropic>=0.40.0' \
            'aiohttp>=3.9,<4.0' \
            'python-dotenv>=1.0'
    "

    # Print installed versions
    info "Installed packages:"
    su - "$RUN_USER" -c "$VENV_DIR/bin/pip show aiogram anthropic aiohttp | grep -E '^(Name|Version)'" || true
}

# ─── step 4: .env ─────────────────────────────────────────────────────────────

setup_env() {
    local env_file="$BOT_DIR/.env"
    local env_example="$BOT_DIR/.env.example"

    cat > "$env_example" <<'ENV'
# SMC Trader Bot v3 — Environment Variables
# Copy to .env and fill in your values:
#   cp .env.example .env && nano .env

# === REQUIRED ===

# Telegram Bot Token (from @BotFather)
TELEGRAM_BOT_TOKEN=

# Your personal Telegram user ID (numeric, from @userinfobot)
TELEGRAM_CHAT_ID=

# Anthropic API Key (https://console.anthropic.com)
ANTHROPIC_API_KEY=

# === OPTIONAL ===

# Claude model (default: claude-opus-4-6, most capable)
# For faster/cheaper proactive checks: claude-haiku-4-5-20251001
CLAUDE_MODEL=claude-opus-4-6

# Primary symbol to monitor
PRIMARY_SYMBOL=XAU/USD

# Platform WebSocket URL (default: local VPS)
PLATFORM_WS_URL=ws://localhost:8000/ws
PLATFORM_HTTP_URL=http://localhost:8000

# Data and log directories (relative to bot dir or absolute)
DATA_DIR=/opt/smc-trader-v3/data
LOG_DIR=/var/log/smc-trader-v3
ENV

    chown "$RUN_USER:$RUN_USER" "$env_example"
    chmod 600 "$env_example"

    if [[ -f "$env_file" ]]; then
        warn ".env already exists — not overwriting. Check .env.example for new vars."
    else
        cp "$env_example" "$env_file"
        chown "$RUN_USER:$RUN_USER" "$env_file"
        chmod 600 "$env_file"
        warn ".env created from template. FILL IN your tokens before starting:"
        warn "  nano $env_file"
    fi
}

# ─── step 5: supervisor config ────────────────────────────────────────────────

setup_supervisor() {
    info "Creating supervisor config at $SUPERVISOR_CONF..."

    cat > "$SUPERVISOR_CONF" <<CONF
; SMC Trader Bot v3 (ADR-0045)
; Independent from /opt/smc-v3 platform — separate process, separate venv
;
; Control:
;   sudo supervisorctl start smc-trader-v3
;   sudo supervisorctl stop smc-trader-v3
;   sudo supervisorctl restart smc-trader-v3
;   sudo supervisorctl status smc-trader-v3
;   sudo supervisorctl tail smc-trader-v3 stdout

[program:smc-trader-v3]
command=$VENV_DIR/bin/python -u $BOT_SCRIPT
directory=$BOT_DIR
user=$RUN_USER
autostart=true
autorestart=true
startretries=5
startsecs=10
stopasgroup=true
killasgroup=true
stopsignal=TERM
stopwaitsecs=15
stdout_logfile=$LOG_DIR/bot.stdout.log
stderr_logfile=$LOG_DIR/bot.stderr.log
stdout_logfile_maxbytes=20MB
stdout_logfile_backups=3
stderr_logfile_maxbytes=10MB
stderr_logfile_backups=2
environment=HOME="/home/$RUN_USER",PYTHONUNBUFFERED="1",PATH="$VENV_DIR/bin:/usr/local/bin:/usr/bin"
CONF

    chmod 644 "$SUPERVISOR_CONF"
    info "Supervisor config written"
}

# ─── step 6: reload supervisor ────────────────────────────────────────────────

reload_supervisor() {
    info "Reloading supervisor..."

    # Check if .env is filled
    local env_file="$BOT_DIR/.env"
    if grep -q "^TELEGRAM_BOT_TOKEN=$" "$env_file" 2>/dev/null || \
       grep -q "^ANTHROPIC_API_KEY=$" "$env_file" 2>/dev/null; then
        warn "---"
        warn ".env NOT configured (tokens empty)."
        warn "Fill in $env_file then run:"
        warn "  sudo supervisorctl reread && sudo supervisorctl update"
        warn "  sudo supervisorctl start smc-trader-v3"
        warn "---"
        supervisorctl reread
    else
        supervisorctl reread
        supervisorctl update
        supervisorctl start smc-trader-v3 || warn "Start failed — check: supervisorctl status"
        sleep 3
        supervisorctl status smc-trader-v3
    fi
}

# ─── step 7: verify ───────────────────────────────────────────────────────────

verify() {
    info "=== Verify ==="
    echo "Bot dir:      $BOT_DIR"
    echo "Script:       $BOT_SCRIPT ($(wc -l < "$BOT_SCRIPT") lines)"
    echo "Venv:         $VENV_DIR"
    echo "Data:         $BOT_DIR/data"
    echo "Logs:         $LOG_DIR"
    echo "Supervisor:   $SUPERVISOR_CONF"
    echo ""
    info "Next steps if not started:"
    info "  1. nano $BOT_DIR/.env"
    info "  2. sudo supervisorctl reread && sudo supervisorctl update"
    info "  3. sudo supervisorctl start smc-trader-v3"
    info "  4. sudo supervisorctl tail -f smc-trader-v3 stdout"
    echo ""
    info "Update bot script (after git pull on dev machine):"
    info "  sudo cp /path/to/smc_trader_v3.py $BOT_SCRIPT"
    info "  sudo supervisorctl restart smc-trader-v3"
}

# ─── main ─────────────────────────────────────────────────────────────────────

main() {
    echo ""
    echo "╔══════════════════════════════════════════╗"
    echo "║   SMC Trader Bot v3 — Deploy Script      ║"
    echo "║   ADR-0045                               ║"
    echo "╚══════════════════════════════════════════╝"
    echo ""

    check_root
    check_python
    check_supervisor
    check_source

    setup_dirs
    copy_script
    setup_venv
    setup_env
    setup_supervisor
    reload_supervisor
    verify

    echo ""
    info "Setup complete."
}

main "$@"
