#!/usr/bin/env bash
# /usr/local/sbin/aione-commands.sh — команди власника боту системних сповіщень (ADR-0106 §3.7).
#   aione-commands.sh <offset|seed>
# Читає getUpdates від offset і друкує:
#   NEXT <offset>                 — наступний offset (watchdog зберігає його лише після відповідей на всі команди)
#   CMD <команда> [аргумент]      — лише з чату ALERT_CHAT_ID; інші чати ігноруються (кількість — у stderr)
# seed (перший запуск): лише проходить повз наявні повідомлення — старі команди не виконуються — і реєструє меню
# команд бота. Секрети — /etc/aione-alerts/env, як у aione-alert.sh; токен ніде не друкується.
set -u

ENV_FILE="${ENV_FILE:-/etc/aione-alerts/env}"      # overridable for tests
LOG="${LOG:-/var/log/aione-alerts.log}"           # overridable for tests
TELEGRAM_API="${TELEGRAM_API:-https://api.telegram.org}"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S%z') [commands] $*" >> "$LOG" 2>/dev/null; }

[ -r "$ENV_FILE" ] || { log "FATAL: cannot read $ENV_FILE"; exit 1; }
# shellcheck disable=SC1090
. "$ENV_FILE"
: "${TELEGRAM_BOT_TOKEN:?missing in env}" "${ALERT_CHAT_ID:?missing in env}"

OFFSET="${1:-seed}"
case "$OFFSET" in
  seed)        QUERY="offset=-1" ;;
  ''|*[!0-9]*) log "bad offset: $OFFSET"; exit 2 ;;
  *)           QUERY="offset=$OFFSET" ;;
esac

RESP="$(mktemp /tmp/aione-commands.XXXXXX)"
trap 'rm -f "$RESP"' EXIT
HTTP="$(curl -sS -m 10 -o "$RESP" -w '%{http_code}' \
  "$TELEGRAM_API/bot${TELEGRAM_BOT_TOKEN}/getUpdates?${QUERY}&timeout=0&allowed_updates=%5B%22message%22%5D" 2>>"$LOG")"
if [ "$HTTP" != "200" ]; then
  log "getUpdates FAIL http=$HTTP body=$(head -c 200 "$RESP" 2>/dev/null)"
  exit 3
fi

if [ "$OFFSET" = "seed" ]; then
  curl -sS -m 10 -o /dev/null "$TELEGRAM_API/bot${TELEGRAM_BOT_TOKEN}/setMyCommands" \
    --data-urlencode 'commands=[{"command":"visitors","description":"Відвідувачі за 7 днів (/visitors 30 — за 30)"},{"command":"status","description":"Стан сервера зараз"}]' \
    2>>"$LOG" || log "setMyCommands FAIL — меню команд не зареєстровано, самі команди працюють"
fi

python3 - "$RESP" "$ALERT_CHAT_ID" "$OFFSET" <<'PY'
import json
import sys

payload = json.load(open(sys.argv[1], encoding="utf-8"))
chat_id, offset = sys.argv[2], sys.argv[3]
updates = payload.get("result") or []
ids = [update.get("update_id", 0) for update in updates]
if ids:
    print("NEXT %d" % (max(ids) + 1))
else:
    print("NEXT %s" % ("0" if offset == "seed" else offset))
if offset == "seed":
    sys.exit(0)
foreign = 0
for update in updates:
    message = update.get("message") or {}
    if str((message.get("chat") or {}).get("id")) != chat_id:
        foreign += 1
        continue
    words = (message.get("text") or "").split()
    if words and words[0].startswith("/"):
        print("CMD " + " ".join([words[0].split("@")[0].lower()] + words[1:2]))
if foreign:
    sys.stderr.write("aione-commands: ignored %d update(s) from other chats\n" % foreign)
PY
