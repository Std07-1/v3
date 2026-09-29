#!/usr/bin/env bash
# /usr/local/sbin/aione-alert.sh
# aione-vps -> Telegram alert sender. INDEPENDENT of the Archi bot process:
# it is a plain curl to the Telegram Bot API, so it still fires when the whole
# supervisor stack (bot + platform) is down — "bot down" is itself an alert.
#
# Secrets come from /etc/aione-alerts/env (root:root 600). The token is NEVER
# echoed or logged. Degraded-but-loud: every failure is logged, hard timeouts
# guarantee a hung network call can never wedge the caller (the 60s watchdog).
set -u

ENV_FILE=/etc/aione-alerts/env
LOG=/var/log/aione-alerts.log

ts()  { date '+%Y-%m-%d %H:%M:%S%z'; }
log() { echo "$(ts) [alert] $*" >> "$LOG" 2>/dev/null; }

if [ ! -r "$ENV_FILE" ]; then
  log "FATAL: cannot read $ENV_FILE"
  exit 1
fi
# shellcheck disable=SC1090
. "$ENV_FILE"
: "${TELEGRAM_BOT_TOKEN:?missing in env}" "${ALERT_CHAT_ID:?missing in env}"

MSG="${1:-}"
if [ -z "$MSG" ]; then
  log "WARN: empty message, nothing sent"
  exit 2
fi

LABEL="${HOSTNAME_LABEL:-$(hostname)}"
TEXT="[$LABEL] $MSG"
RESP="$(mktemp /tmp/aione-alert-resp.XXXXXX)"

HTTP="$(curl -sS -m 10 --retry 1 --retry-delay 2 \
  -o "$RESP" -w '%{http_code}' \
  "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
  --data-urlencode "chat_id=${ALERT_CHAT_ID}" \
  --data-urlencode "text=${TEXT}" \
  --data-urlencode "disable_web_page_preview=true" 2>>"$LOG")"
RC=$?
BODY="$(cat "$RESP" 2>/dev/null)"
rm -f "$RESP"

if [ "$RC" -ne 0 ] || [ "$HTTP" != "200" ]; then
  # Log the API error body (contains no secret) so silent-drop never happens.
  log "SEND FAIL rc=$RC http=$HTTP body=${BODY:0:200} msg=${MSG:0:120}"
  exit 3
fi
log "SENT ok: ${MSG:0:120}"
exit 0
