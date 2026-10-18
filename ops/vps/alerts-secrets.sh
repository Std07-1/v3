#!/usr/bin/env bash
# ADR-0114: секрети бота сигналів (процес smc-alerts) у /etc/smc-v3/alerts.env, root:smc 640.
# Запуск (одна команда): sudo bash /opt/smc-v3/ops/vps/alerts-secrets.sh
#
# Rev 2: основний канал — пуш у браузер (ключ VAPID: sudo .venv/bin/python -m runtime.alerts.vapid_keys). Цей скрипт
# потрібен лише для Telegram (alerts.telegram_enabled=true) і змінює ЛИШЕ свої два рядки — решта файлу (ключ VAPID)
# лишається: стерти ключ = вимкнути пуш на всіх пристроях.
#
# Токен вводиться наосліп (stdin, read -s): не потрапляє ні в argv (sudo пише argv в auth.log), ні на екран, ні в
# історію shell. printf — вбудована команда bash, окремого процесу з токеном в аргументах нема.
# Chat id власника за замовчуванням — той самий, що в системного бота «Monitor» (/etc/aione-alerts/env, ADR-0106):
# у приватному чаті chat id = id користувача Telegram, однаковий для будь-якого бота.
# Попередній файл лишається поруч як alerts.env.bak.<epoch>. Перед першим повідомленням натисніть Start у новому боті.
set -euo pipefail
umask 077

ENV_FILE="${ALERTS_ENV_FILE:-/etc/smc-v3/alerts.env}"   # перевизначається для пісочниці
MONITOR_ENV="${MONITOR_ENV_FILE:-/etc/aione-alerts/env}"
OWNER_GROUP="${ALERTS_ENV_GROUP:-smc}"

if [ "$(id -u)" -ne 0 ] && [ -z "${ALERTS_ENV_FILE:-}" ]; then
  echo "потрібен root: sudo bash $0" >&2
  exit 2
fi

default_chat=""
if [ -r "$MONITOR_ENV" ]; then
  default_chat="$(sed -n 's/^ALERT_CHAT_ID=//p' "$MONITOR_ENV" | head -n 1 | tr -d '"'"'"' \r')"
fi

read -rsp "Токен бота сигналів від BotFather (не відображається): " token
echo
read -rp "Chat id власника [${default_chat:-нема, введіть число}]: " chat
chat="${chat:-$default_chat}"

if ! [[ "$token" =~ ^[0-9]+:[A-Za-z0-9_-]{30,}$ ]]; then
  echo "це не схоже на токен бота (очікується 123456789:AA…)" >&2
  exit 3
fi
if ! [[ "$chat" =~ ^-?[0-9]+$ ]]; then
  echo "chat id має бути числом" >&2
  exit 3
fi

dir="$(dirname "$ENV_FILE")"
install -d -m 750 "$dir"
if [ "$(id -u)" -eq 0 ]; then
  chgrp "$OWNER_GROUP" "$dir"
fi
if [ -f "$ENV_FILE" ]; then
  cp -p "$ENV_FILE" "$ENV_FILE.bak.$(date +%s)"
fi

tmp="$(mktemp "$dir/.alerts.env.XXXXXX")"
trap 'rm -f "$tmp"' EXIT
if [ -f "$ENV_FILE" ]; then
  # Інші рядки (ALERTS_VAPID_PRIVATE_KEY) переносимо як є; grep без збігів дає код 1 — це не помилка
  grep -v -E '^(ALERTS_TELEGRAM_TOKEN|ALERTS_OWNER_CHAT_ID)=' "$ENV_FILE" > "$tmp" || true
fi
printf 'ALERTS_TELEGRAM_TOKEN=%s\nALERTS_OWNER_CHAT_ID=%s\n' "$token" "$chat" >> "$tmp"
chmod 640 "$tmp"
if [ "$(id -u)" -eq 0 ]; then
  chown "root:$OWNER_GROUP" "$tmp"
fi
mv -f "$tmp" "$ENV_FILE"
trap - EXIT
unset token

echo "записано $ENV_FILE (640, група $OWNER_GROUP); застосувати: sudo supervisorctl restart smc-alerts"
