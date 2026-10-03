#!/usr/bin/env bash
# /usr/local/sbin/aione-watchdog.sh
# aione-vps watchdog — periodic health + attack visibility + bounded self-heal -> Telegram.
# Runs as root every 60s via aione-watchdog.timer. INDEPENDENT of the Archi bot:
# if the whole supervisor stack dies, this still runs and reports it.
#
# ─────────────────────────────────────────────────────────────────────────────
# DATA SAFETY (Archi's accumulated knowledge is irreplaceable — top invariant):
#   * NO remediation path in this script EVER writes to, deletes from, or touches
#     /opt/smc-trader-v3/data/ (conversation, knowledge, learning journal,
#     directives, consciousness). Restarts move *processes*, never files.
#   * The ONLY disk-reclaim action is `journalctl --vacuum-*`, which by construction
#     operates solely on the systemd journal under /var/log/journal — it cannot and
#     does not reach /opt. If disk is full and the journal isn't the hog, we ESCALATE
#     to the human, we never delete anything ourselves.
#   * check_backup actively guards the knowledge: it alarms if the daily
#     /opt/backups/trader-v3-*.tar.gz stops refreshing, so a silently-broken backup
#     cron surfaces before it can cost data.
# ─────────────────────────────────────────────────────────────────────────────
#
# DELIVERY GUARANTEE (v1's hard-won property, preserved): every check commits its
# consumed position (journal cursor / log offset / per-IP seen / service state /
# throttle / tally) ONLY after notify() confirms a successful Telegram send. A failed
# send is retried next tick, never silently dropped.
#
# ALERT SHAPE (plain-language): every message is 2-3 phone-readable lines:
#   <emoji> <ЗАГОЛОВОК>
#   Що: <що сталося, людською + роль компонента>
#   Дія: <що робити зараз — або "нічого, сам полагодив">
#
# SELF-HEAL (kubernetes-style: reconcile + report, no human tap):
#   * systemd service 'failed' -> auto `systemctl restart`, PER-SERVICE (never a
#     blanket "restart everything"), rate-limited to RESTART_MAX per RESTART_WINDOW_S
#     with min RESTART_GAP_S between tries. Result reported next tick when the outcome
#     is KNOWN (no in-tick sleep). Budget exhausted -> stop, escalate (anti-crash-loop).
#   * disk / >= DISK_PCT_MAX -> journal vacuum only (see DATA SAFETY), report freed %,
#     escalate if still full.
#   * Ambiguous/dangerous (OOM, leak, load, swap, ban-spike, ssh, program crash,
#     stale backup) -> ALERT ONLY, human-in-loop. No autonomous action.
#
# CRASH MONITOR (check_supervisor): every supervisor program is watched independently.
# A program that goes FATAL (supervisord gave up) is reported per-program with its role.
# NOTHING here is auto-restarted: supervisord's own autorestart handles the platform
# programs, and the bot (smc_trader_v3, autostart=false) is deliberately owner-managed
# — it must NEVER be auto-started by us. Silent on STOPPED/BACKOFF/STARTING (transient
# or intentional); only a terminal FATAL wakes the phone.
#
# Degraded-but-loud: each check is isolated; a failing check logs, never aborts the
# rest. All state under $STATE_DIR (root 700). No secrets here.
#
# ADR-0106 (git: ops/vps/ платформи; на сервер — install з checkout-а, див. ранбук):
#   * свій SSH-вхід (ключ з known_keys І мережа з known_ips) — без повідомлення, лише лічильник для звіту;
#   * ресурси (load/пам'ять/swap/процеси) — повідомлення на ПОЧАТОК проблеми (RES_ENTER_TICKS тіків поспіль)
#     і на КІНЕЦЬ (RES_CLEAR_TICKS поспіль) з винуватцями й тривалістю; поки триває — нагадування раз на RES_REMIND_S;
#   * падіння — з хвостом stderr (схоже на секрет — масковано), відновлення — з тривалістю простою;
#   * ранковий звіт о REPORT_HOUR за REPORT_TZ замість heartbeat-а: сервер, платформа, нічний settle, Арчі,
#     SSH, бани, події доби, відвідувачі (ADR-0105). Доставлений звіт обнуляє денні лічильники;
#   * команди власника боту (§3.7): /visitors [днів], /status — відповідь за тік; лише з чату ALERT_CHAT_ID.
set -u

STATE_DIR="${STATE_DIR:-/var/lib/aione-alerts}"   # overridable so tests isolate state
LOG="${LOG:-/var/log/aione-alerts.log}"           # overridable so tests don't pollute the prod log
ALERT="${ALERT:-/usr/local/sbin/aione-alert.sh}"  # overridable: пісочниця/тести ловлять текст замість відправки
FAIL2BAN_LOG="${FAIL2BAN_LOG:-/var/log/fail2ban.log}"
PROC_LOADAVG="${PROC_LOADAVG:-/proc/loadavg}"      # overridable for tests
PROC_MEMINFO="${PROC_MEMINFO:-/proc/meminfo}"      # overridable for tests
PLATFORM_DIR="${PLATFORM_DIR:-/opt/smc-v3}"        # checkout платформи: venv для зведення відвідувачів
VISITORS_USER="${VISITORS_USER:-smc}"              # власник журналу відвідувачів (ADR-0105)
SETTLE_STATUS="${SETTLE_STATUS:-/var/lib/smc-v3/m1_settle/last_status.json}"  # стан нічного settle (ADR-0103)
COMMANDS="${COMMANDS:-/usr/local/sbin/aione-commands.sh}"  # команди власника боту (ADR-0106 §3.7)
BACKUP_DIR="${BACKUP_DIR:-/opt/backups}"           # overridable for tests
KNOWN_IPS_FILE="${KNOWN_IPS_FILE:-/etc/aione-alerts/known_ips}"  # IP/prefix -> human label for SSH alerts
KNOWN_KEYS_FILE="${KNOWN_KEYS_FILE:-/etc/aione-alerts/known_keys}"  # SSH key fingerprint -> human label

# --- tunables (env-overridable, used by the E2E test harness) ---
WATCH_SERVICES="${WATCH_SERVICES:-supervisor traccar nginx cloudflared redis-server fail2ban}"
LOAD1_MAX="${LOAD1_MAX:-12}"
MEMAVAIL_MIN_KB="${MEMAVAIL_MIN_KB:-358400}"      # 350 MB
SWAP_USED_MAX_KB="${SWAP_USED_MAX_KB:-1048576}"   # 1 GB
PROC_MAX="${PROC_MAX:-400}"                       # ps -e process count (fork-bomb heuristic)
DISK_PCT_MAX="${DISK_PCT_MAX:-90}"                # / usage %
BAN_SPIKE="${BAN_SPIKE:-5}"                       # >=N new bans in one tick -> flood alert
RES_THROTTLE_S="${RES_THROTTLE_S:-600}"           # 10 min between same resource alert
SSH_DEDUP_S="${SSH_DEDUP_S:-1800}"                # 30 min per source IP for login alert
REPORT_TZ="${REPORT_TZ:-Europe/Prague}"            # ранковий звіт: часова зона власника (ADR-0106 §3.5)
REPORT_HOUR="${REPORT_HOUR:-9}"                    # локальна година ранкового звіту
VISITORS_REPORT_ROWS="${VISITORS_REPORT_ROWS:-12}" # рядків відвідувачів у звіті (ліміт повідомлення Telegram)
VISITORS_TIMEOUT_S="${VISITORS_TIMEOUT_S:-60}"     # зведення відвідувачів не може підвісити тік
RES_ENTER_TICKS="${RES_ENTER_TICKS:-2}"            # ресурс: стільки поганих тіків поспіль = проблема почалась
RES_CLEAR_TICKS="${RES_CLEAR_TICKS:-5}"            # ... стільки нормальних поспіль = минула
RES_REMIND_S="${RES_REMIND_S:-21600}"              # поки триває — нагадування не частіше 6 год
# self-heal tunables
RESTART_MAX="${RESTART_MAX:-3}"                   # max auto-restart attempts per service per window
RESTART_WINDOW_S="${RESTART_WINDOW_S:-3600}"      # 1 h budget window
RESTART_GAP_S="${RESTART_GAP_S:-120}"             # min seconds between two auto-restart tries (anti-flap)
VACUUM_SIZE="${VACUUM_SIZE:-50M}"                 # journalctl --vacuum-size target on disk-full
AUTOHEAL="${AUTOHEAL:-1}"                         # 0 disables all remediation (alert-only mode) — for tests
# backup freshness
BACKUP_MAX_AGE_S="${BACKUP_MAX_AGE_S:-108000}"    # 30 h — daily cron @03:00 must have run
BACKUP_THROTTLE_S="${BACKUP_THROTTLE_S:-21600}"   # re-alert stale backup at most every 6 h

mkdir -p "$STATE_DIR" 2>/dev/null
chmod 700 "$STATE_DIR" 2>/dev/null
NCPU="$(nproc 2>/dev/null || echo 4)"

ts()   { date '+%Y-%m-%d %H:%M:%S%z'; }
log()  { echo "$(ts) [watchdog] $*" >> "$LOG" 2>/dev/null; }
now()  { date +%s; }

# notify: send and RETURN the real status (0 ok / non-zero fail) so callers can
# commit consumed-state only after a confirmed delivery. A delivered event is also
# listed in the morning report ("події за добу", ADR-0106 §3.5).
notify() {
  if "$ALERT" "$1"; then
    printf '%s\t%s\n' "$(now)" "$(printf '%s\n' "$1" | head -n1)" >> "$STATE_DIR/events.day" 2>/dev/null
    return 0
  fi
  log "notify FAILED (retry next tick): ${1:0:80}"
  return 1
}

# throttle_open KEY WINDOW -> 0 if window elapsed (READ-ONLY; caller stamps on success)
throttle_open() {
  local key="$STATE_DIR/throttle.$1" win="$2" last
  last="$(cat "$key" 2>/dev/null || echo 0)"
  [ "$(( $(now) - last ))" -ge "$win" ]
}
throttle_stamp() { now > "$STATE_DIR/throttle.$1"; }

add_tally() {                              # $1=tally name  $2=increment
  local f="$STATE_DIR/$1" t
  t="$(cat "$f" 2>/dev/null || echo 0)"
  echo "$(( ${t:-0} + ${2:-0} ))" > "$f"
}
add_ban_tally() { add_tally ban.tally "${1:-0}"; }

# людська тривалість: 47 хв / 2 год 5 хв / 3 д 4 год
human_dur() {                              # $1=seconds
  local s="${1:-0}"
  if [ "$s" -lt 60 ]; then echo "<1 хв"
  elif [ "$s" -lt 3600 ]; then echo "$(( s / 60 )) хв"
  elif [ "$s" -lt 86400 ]; then echo "$(( s / 3600 )) год $(( s % 3600 / 60 )) хв"
  else echo "$(( s / 86400 )) д $(( s % 86400 / 3600 )) год"; fi
}

# простій служби/програми: момент падіння живе поруч зі state-файлом ($pf.since) до
# ПІДТВЕРДЖЕНОЇ відправки повідомлення про відновлення (гарантія доставки).
mark_down()   { [ -f "$1.since" ] || now > "$1.since"; }
since_dur()   { local s; s="$(cat "$1.since" 2>/dev/null || echo 0)"; [ "${s:-0}" -gt 0 ] && human_dur "$(( $(now) - s ))" || echo "невідомо скільки"; }
clear_since() { rm -f "$1.since"; }

# хвіст помилки у повідомленні: схоже на секрет (password=/token:/Bot-токен) — масковано
mask_secrets() {
  sed -E 's/((pass(word)?|passwd|token|secret|api[_-]?key|authorization)[^=:]*[=:][[:space:]]*)[^[:space:],;]+/\1***/Ig; s/[0-9]{8,11}:[A-Za-z0-9_-]{30,}/***/g'
}

# винуватці ресурсних проблем — одним рядком, щоб не треба було заходити на сервер
top_cpu()   { ps -eo pcpu=,comm= --sort=-pcpu 2>/dev/null | head -5 | awk '{printf "%s%s %s%%", (NR>1?"; ":""), $2, $1}'; }
top_rss()   { ps -eo rss=,comm= --sort=-rss 2>/dev/null | head -5 | awk '{printf "%s%s %dMB", (NR>1?"; ":""), $2, $1/1024}'; }
top_count() { ps -e -o comm= 2>/dev/null | sort | uniq -c | sort -rn | head -5 | awk '{printf "%s%s×%s", (NR>1?"; ":""), $2, $1}'; }

# res_track KEY BAD TITLE CULPRITS_FN HINT CLEAR_TEXT — ресурсна проблема як стан (ADR-0106 §3.3):
# повідомлення на початок (RES_ENTER_TICKS поганих тіків поспіль), нагадування раз на RES_REMIND_S,
# повідомлення на кінець (RES_CLEAR_TICKS нормальних поспіль) з тривалістю. Стан — "$STATE_DIR/res.KEY":
# bad_streak ok_streak start alerted last_notice clear_from. Недоставлене — стан не рухається, повтор наступного тіку.
res_track() {
  local f="$STATE_DIR/res.$1" bad ok start alerted last clear_from t
  read -r bad ok start alerted last clear_from < "$f" 2>/dev/null
  bad="${bad:-0}"; ok="${ok:-0}"; start="${start:-0}"; alerted="${alerted:-0}"; last="${last:-0}"; clear_from="${clear_from:-0}"
  t="$(now)"
  if [ "$2" = 1 ]; then
    [ "$bad" -eq 0 ] && [ "$alerted" -eq 0 ] && start="$t"
    bad=$(( bad + 1 )); ok=0; clear_from=0
    if [ "$alerted" -eq 0 ] && [ "$bad" -ge "$RES_ENTER_TICKS" ]; then
      notify "$(printf '⚠️ %s\nХто: %s\nДія: %s; напишу, коли мине' "$3" "$($4)" "$5")" && { alerted=1; last="$t"; }
    elif [ "$alerted" -eq 1 ] && [ "$(( t - last ))" -ge "$RES_REMIND_S" ]; then
      notify "$(printf '⏳ досі триває (%s): %s\nХто: %s' "$(human_dur "$(( t - start ))")" "$3" "$($4)")" && last="$t"
    fi
  else
    [ "$ok" -eq 0 ] && clear_from="$t"
    ok=$(( ok + 1 )); bad=0
    if [ "$alerted" -eq 1 ] && [ "$ok" -ge "$RES_CLEAR_TICKS" ]; then
      notify "$(printf '✅ минуло: %s — тривало %s\nДія: нічого' "$6" "$(human_dur "$(( clear_from - start ))")")" \
        && { start=0; alerted=0; last=0; }
    elif [ "$alerted" -eq 0 ]; then
      start=0                              # короткий сплеск без повідомлення — забуваємо
    fi
  fi
  echo "$bad $ok $start $alerted $last $clear_from" > "$f"
}

# plain-language role of each watched systemd service (drives the "Що:" line)
svc_role() {
  case "$1" in
    supervisor)   echo "супервізор програм — платформа, міст Арчі, family, guard (Арчі autostart=false — його це НЕ підніме)";;
    traccar)      echo "GPS-трекер дитини";;
    nginx)        echo "веб/проксі — сайт aione-smc.com і console";;
    cloudflared)  echo "Cloudflare-тунель — доступ ззовні до сайту";;
    redis-server) echo "Redis — IPC-шина між ботом і платформою";;
    fail2ban)     echo "бан брутфорсу SSH/веб";;
    *)            echo "$1";;
  esac
}

# human label for an SSH source IP. Reads $KNOWN_IPS_FILE lines "<ip-or-prefix> <label>".
# A key ending in '.' is a prefix match (e.g. "203.0.113." matches 203.0.113.56 but NOT
# 203.0.1130.x — the trailing dot pins the octet boundary). Exact IPs also supported.
# Empty output = unknown IP (the alert then flags it loudly).
ip_label() {                               # $1=ip -> echo label or nothing
  [ -r "$KNOWN_IPS_FILE" ] || return 0
  awk -v ip="$1" '
    /^[[:space:]]*#/ || /^[[:space:]]*$/ { next }
    { key=$1; $1=""; sub(/^[[:space:]]+/,""); label=$0
      if (key ~ /\.$/) { if (index(ip, key) == 1) { print label; exit } }
      else if (key == ip) { print label; exit } }' "$KNOWN_IPS_FILE"
}

# human label for an SSH key fingerprint (SHA256:...). Reads $KNOWN_KEYS_FILE lines
# "<SHA256:fingerprint> <label>". Lets a future dedicated Claude/agent key be labelled
# so the alert says WHICH key was used (e.g. "агент" vs "ручний ключ"). Empty = unlabelled.
key_label() {                              # $1=fingerprint -> echo label or nothing
  [ -n "$1" ] || return 0
  [ -r "$KNOWN_KEYS_FILE" ] || return 0
  awk -v fp="$1" '
    /^[[:space:]]*#/ || /^[[:space:]]*$/ { next }
    { key=$1; $1=""; sub(/^[[:space:]]+/,""); label=$0
      if (key == fp) { print label; exit } }' "$KNOWN_KEYS_FILE"
}

# plain-language role of each supervisor *program* (crash monitor)
prog_role() {
  case "$1" in
    smc_trader_v3)     echo "Арчі — торговий агент (мозок)";;
    smc-ws)            echo "WS-сервер платформи — графік і API сайту";;
    smc-fxcm)          echo "FXCM-фід — ціни всіх символів графіка";;
    smc-ticks)         echo "тік-агрегатор платформи";;
    smc-preview)       echo "живі бари графіка (preview)";;
    smc-binance)       echo "Binance-фід — крипта";;
    smc-binance-ticks) echo "Binance-тіки";;
    smc-agent-bridge)  echo "міст Арчі ↔ платформа";;
    family_agent)      echo "сімейний агент";;
    tg_guard)          echo "Telegram-guard";;
    *)                 echo "$1";;
  esac
}

# ---------- restart budget (rate-limit + anti-flap) ----------
# per-service append-only log of attempt epochs, pruned to the window on read.
restart_attempts() {                       # $1=svc -> echoes count-in-window (single clean int), prunes file
  local f="$STATE_DIR/restart.$1" cutoff n
  cutoff="$(( $(now) - RESTART_WINDOW_S ))"
  [ -f "$f" ] || { echo 0; return; }
  awk -v c="$cutoff" '$1+0>=c' "$f" > "$f.tmp" 2>/dev/null && mv -f "$f.tmp" "$f" || rm -f "$f.tmp"
  n="$(grep -c . "$f" 2>/dev/null)"        # grep prints exactly "0" on empty; capture avoids the
  echo "${n:-0}"                           # "0\n0" that `... || echo 0` produced (S1 fix)
}
restart_last_ts() { tail -n1 "$STATE_DIR/restart.$1" 2>/dev/null || echo 0; }
record_restart()  { echo "$(now)" >> "$STATE_DIR/restart.$1"; }

# INITIATE a bounded auto-restart of a failed systemd service. Fire-and-mark: we do
# NOT sleep/poll here (that misreads a slow JVM cold-start like traccar as failure and
# holds the flock). We record the attempt, kick the restart, mark $pf=healing, and the
# NEXT tick's healing-resolution reports the real outcome. Budget exhausted -> escalate.
initiate_autoheal() {                      # $1=svc  $2=pf
  local svc="$1" pf="$2" n last gap role
  role="$(svc_role "$svc")"
  n="$(restart_attempts "$svc")"
  if [ "$n" -ge "$RESTART_MAX" ]; then
    if throttle_open "esc.$svc" "$RES_THROTTLE_S"; then
      notify "$(printf '🛑 auto-fix ВИЧЕРПАНО: %s\nЩо: %s — падав %d× за годину, рестарт не тримається (crash-loop)\nДія: потрібен ти — ssh aione-vps '\''journalctl -u %s -n80 --no-pager'\''' "$svc" "$role" "$RESTART_MAX" "$svc")" \
        && throttle_stamp "esc.$svc"
    fi
    echo failed > "$pf"                    # stay failed; window roll-over re-earns budget
    return 0
  fi
  last="$(restart_last_ts "$svc")"
  gap="$(( $(now) - last ))"
  [ "$gap" -lt "$RESTART_GAP_S" ] && return 0   # too soon since last try; leave $pf, retry next tick
  record_restart "$svc"
  log "auto-restart $svc (attempt $((n+1))/$RESTART_MAX)"
  systemctl restart "$svc" >/dev/null 2>&1 &   # fire; do not block the tick on a slow start
  echo healing > "$pf"                          # resolved next tick
  return 0
}

# ---------- checks ----------

check_services() {
  local svc state prev pf role n
  for svc in $WATCH_SERVICES; do
    state="$(systemctl is-active "$svc" 2>/dev/null || true)"
    [ -z "$state" ] && state="unknown"
    pf="$STATE_DIR/svc.$svc"
    prev="$(cat "$pf" 2>/dev/null || echo firstrun)"
    role="$(svc_role "$svc")"

    # 1. firstrun grace — seed only, never act/alert on the state we booted into.
    if [ "$prev" = "firstrun" ]; then echo "$state" > "$pf"; continue; fi

    # 2. healing resolution — we kicked a restart last tick; report the KNOWN outcome.
    if [ "$prev" = "healing" ]; then
      case "$state" in
        active)
          n="$(restart_attempts "$svc")"
          notify "$(printf '🔧 auto-fix: %s\nЩо: %s був failed → рестартнув, тепер active (спроба %d/%d за год, простій %s)\nДія: нічого, сам полагодив — стежу далі' "$svc" "$role" "$n" "$RESTART_MAX" "$(since_dur "$pf")")" \
            && { echo active > "$pf"; clear_since "$pf"; } ;;
        activating|reloading)
          log "$svc still $state after restart; confirm next tick" ;;   # in-progress: no alert, keep healing
        failed)
          [ "$AUTOHEAL" = "1" ] && initiate_autoheal "$svc" "$pf" ;;      # retry or escalate
        *)
          notify "$(printf '⚠️ auto-fix: %s\nЩо: рестартнув %s, але стан=%s (не піднявся)\nДія: глянь — ssh aione-vps '\''systemctl status %s -n30 --no-pager'\''' "$svc" "$role" "$state" "$svc")" \
            && echo "$state" > "$pf" ;;
      esac
      continue
    fi

    # 3. failed -> initiate PER-SERVICE self-heal (rate-limited). Only this service.
    if [ "$state" = "failed" ] && [ "$AUTOHEAL" = "1" ]; then
      mark_down "$pf"
      initiate_autoheal "$svc" "$pf"
      continue
    fi

    # 4. normal transitions. Transient states (systemd mid-restart) stay SILENT — a
    #    down-alert only fires for a stable 'inactive' (a deliberate stop), and 'failed'
    #    is already handled in step 3. This stops a slow JVM (traccar) restart from
    #    reading as "впав (activating)" while systemd is healing it itself.
    [ "$state" = "$prev" ] && continue
    case "$state" in
      active)   # recovered from a non-active state (prev != active guaranteed above)
        notify "$(printf '🟢 %s знову active\nЩо: %s — відновився, простій %s\nДія: нічого' "$svc" "$role" "$(since_dur "$pf")")" \
          && { echo active > "$pf"; clear_since "$pf"; } ;;
      inactive) # stable down, likely a deliberate stop -> alert, never auto-restart
        mark_down "$pf"
        notify "$(printf '🔴 %s впав (inactive)\nЩо: %s\nДія: якщо це не ти зупинив — ssh aione-vps '\''sudo systemctl start %s'\''' "$svc" "$role" "$svc")" \
          && echo inactive > "$pf" ;;
      activating|deactivating|reloading|unknown)
        : ;;                                                                 # transient — wait for a stable state, no alert, no $pf write
      *)
        echo "$state" > "$pf" ;;                                             # any other drift, no alert
    esac
  done
  return 0
}

# supervisor program crash monitor — per-program, alert-only, NEVER restarts anything.
check_supervisor() {
  command -v supervisorctl >/dev/null 2>&1 || { log "supervisorctl absent; skip check_supervisor"; return 0; }
  local out
  out="$(supervisorctl status 2>/dev/null)"
  [ -z "$out" ] && { log "check_supervisor: empty status (supervisord down? check_services covers it)"; return 0; }
  # each line: "<name|group:name>  <STATE>  pid .., uptime .."  -> write per-program state files.
  # (piped while runs in a subshell; that is fine — all state lives in files, not vars.)
  printf '%s\n' "$out" | while read -r name state _rest; do
    [ -z "$name" ] && continue
    # NB: no `local` here — this while runs in a pipe subshell; vars stay scoped to it
    # and all durable state lives in files, so plain assignments are correct and safe.
    short="${name#*:}"                                   # strip group prefix (e.g. smc:smc-ws -> smc-ws)
    pf="$STATE_DIR/prog.$(printf '%s' "$name" | tr ':/.' '___')"
    prev="$(cat "$pf" 2>/dev/null || echo firstrun)"
    role="$(prog_role "$short")"
    if [ "$prev" = "firstrun" ]; then echo "$state" > "$pf"; continue; fi
    [ "$state" = "$prev" ] && continue
    case "$state" in
      FATAL)
        mark_down "$pf"
        errtail="$(timeout 5 supervisorctl tail "$name" stderr 2>/dev/null | grep -v '^[[:space:]]*$' | tail -n 3 | cut -c1-200 | mask_secrets | paste -sd'|')"
        notify "$(printf '🔴 %s впав: FATAL\nЩо: %s — supervisor здався (не піднявся після retries), сам НЕ встане\nПомилка: %s\nДія: підняти — ssh aione-vps '\''sudo supervisorctl start %s'\''' "$short" "$role" "${errtail:-stderr порожній}" "$name")" \
          && echo "$state" > "$pf" ;;
      RUNNING)
        case "$prev" in
          FATAL) notify "$(printf '🟢 %s знову RUNNING\nЩо: %s — піднявся, простій %s\nДія: нічого' "$short" "$role" "$(since_dur "$pf")")" \
                   && { echo "$state" > "$pf"; clear_since "$pf"; } ;;
          *)     echo "$state" > "$pf" ;;                # e.g. STOPPED->RUNNING (owner started it) -> silent
        esac ;;
      *)
        echo "$state" > "$pf" ;;                         # STOPPED/BACKOFF/STARTING/EXITED/STOPPING -> silent (transient/intentional)
    esac
  done
  return 0
}

# knowledge guard: alarm if Archi's daily backup stopped refreshing (silent-cron guard).
check_backup() {
  local latest mt age
  latest="$(ls -t "$BACKUP_DIR"/trader-v3-*.tar.gz 2>/dev/null | head -1)"
  if [ -z "$latest" ]; then
    throttle_open backup "$BACKUP_THROTTLE_S" \
      && notify "$(printf '⚠️ бекап Арчі: жодного trader-v3-*.tar.gz у %s\nЩо: накопичені знання Арчі, схоже, НЕ бекапляться\nДія: ssh aione-vps '\''tail -30 /var/log/trader-v3-backup.log'\''; прогнати /opt/scripts/vps_backup_trader_v3.sh' "$BACKUP_DIR")" \
      && throttle_stamp backup
    return 0
  fi
  mt="$(stat -c %Y "$latest" 2>/dev/null || echo 0)"
  age="$(( $(now) - mt ))"
  if [ "$age" -gt "$BACKUP_MAX_AGE_S" ]; then
    throttle_open backup "$BACKUP_THROTTLE_S" \
      && notify "$(printf '⚠️ бекап Арчі застряг: останній %sг тому (норма <%sг)\nЩо: %s не оновлюється → conversation/knowledge/learning/directives під ризиком\nДія: ssh aione-vps '\''tail -30 /var/log/trader-v3-backup.log'\''; прогнати /opt/scripts/vps_backup_trader_v3.sh' "$((age/3600))" "$((BACKUP_MAX_AGE_S/3600))" "$(basename "$latest")")" \
      && throttle_stamp backup
  fi
  return 0
}

check_resources() {
  local load1 memavail swap_total swap_free swap_used procs used
  load1="$(awk '{print $1}' "$PROC_LOADAVG" 2>/dev/null)"
  memavail="$(awk '/^MemAvailable:/{print $2}' "$PROC_MEMINFO" 2>/dev/null)"
  swap_total="$(awk '/^SwapTotal:/{print $2}' "$PROC_MEMINFO" 2>/dev/null)"
  swap_free="$(awk '/^SwapFree:/{print $2}' "$PROC_MEMINFO" 2>/dev/null)"
  procs="$(ps -e --no-headers 2>/dev/null | wc -l)"
  used="$(df -P / 2>/dev/null | awk 'NR==2{print $5+0}')"
  swap_used="$(( ${swap_total:-0} - ${swap_free:-0} ))"

  local bad
  bad=0; [ -n "$load1" ] && awk -v l="$load1" -v m="$LOAD1_MAX" 'BEGIN{exit !(l+0>m+0)}' && bad=1
  res_track load "$bad" "$(printf 'висока load: %s (поріг %s, ядер %s)' "$load1" "$LOAD1_MAX" "$NCPU")" top_cpu \
    "якщо не спаде — рестарт винуватця" "load у нормі ($load1)"
  bad=0; [ -n "$memavail" ] && [ "$memavail" -lt "$MEMAVAIL_MIN_KB" ] && bad=1
  res_track mem "$bad" "$(printf 'мало памʼяті: вільно %sMB (поріг %sMB)' "$(( ${memavail:-0} / 1024 ))" "$(( MEMAVAIL_MIN_KB / 1024 ))")" top_rss \
    "можливий витік — далі swap і OOM" "памʼяті знову досить ($(( ${memavail:-0} / 1024 ))MB вільно)"
  bad=0; [ "$swap_used" -gt "$SWAP_USED_MAX_KB" ] && bad=1
  res_track swap "$bad" "$(printf 'тиск на swap: використано %sMB' "$(( swap_used / 1024 ))")" top_rss \
    "RAM закінчується, система гальмуватиме" "swap відпустило ($(( swap_used / 1024 ))MB)"
  bad=0; [ -n "$procs" ] && [ "$procs" -gt "$PROC_MAX" ] && bad=1
  res_track procs "$bad" "$(printf 'сплеск процесів: %s (поріг %s)' "$procs" "$PROC_MAX")" top_count \
    "якщо росте — застряглий цикл спавну" "процесів знову норма ($procs)"
  if [ -n "$used" ] && [ "$used" -ge "$DISK_PCT_MAX" ]; then
    if [ "$AUTOHEAL" = "1" ]; then
      throttle_open disk "$RES_THROTTLE_S" && { try_autoheal_disk "$used"; throttle_stamp disk; }
    else
      throttle_open disk "$RES_THROTTLE_S" \
        && notify "$(printf '⚠️ диск / на %s%% (max %s%%)\nЩо: логи/SQLite/JSONL-записи скоро почнуть падати\nДія: ssh aione-vps '\''du -sh /var/log/* | sort -rh | head'\''' "$used" "$DISK_PCT_MAX")" \
        && throttle_stamp disk
    fi
  fi
  return 0
}

# disk-full self-heal: vacuum the systemd journal ONLY (see DATA SAFETY — never touches
# /opt/.../data). Reports freed %. If still full afterwards the hog isn't the journal
# -> escalate; we never auto-delete anything outside the journal.
try_autoheal_disk() {                      # $1=used%
  local before after freed
  before="$1"
  log "auto-vacuum journal (disk ${before}%)"
  journalctl --vacuum-size="$VACUUM_SIZE" >/dev/null 2>&1
  after="$(df -P / 2>/dev/null | awk 'NR==2{print $5+0}')"
  freed="$(( before - after ))"
  if [ "$after" -lt "$DISK_PCT_MAX" ]; then
    notify "$(printf '🔧 auto-fix: диск був %s%%\nЩо: вакуумнув systemd-журнал (→%s), тепер %s%%, звільнив ~%s%% (data/ Арчі не чіпав)\nДія: нічого, сам полагодив' "$before" "$VACUUM_SIZE" "$after" "$freed")"
  else
    notify "$(printf '🛑 auto-fix не вистачило: диск досі %s%% (був %s%%)\nЩо: журнал вакуумнув, але хог не в ньому — сам більше нічого не видаляю\nДія: потрібен ти — ssh aione-vps '\''du -sh /var/log/* /opt/* 2>/dev/null | sort -rh | head'\''' "$after" "$before")"
  fi
  return 0
}

check_oom() {
  local cf="$STATE_DIR/oom.cursor" tmp out
  if [ ! -f "$cf" ]; then
    journalctl -k -n1 --cursor-file="$cf" -o cat -q >/dev/null 2>&1   # seed only, no alert
    return 0
  fi
  tmp="$cf.next"; cp -f "$cf" "$tmp" 2>/dev/null
  out="$(journalctl -k --cursor-file="$tmp" -o cat -q 2>/dev/null \
        | grep -iE 'out of memory|oom-kill|killed process' | head -5)"
  if [ -z "$out" ]; then mv -f "$tmp" "$cf"; return 0; fi              # nothing to send: advance
  if notify "$(printf '💥 OOM: ядро вбило процес за браком памʼяті\nЩо: %s\nДія: НЕ авто-фікшу — глянь хто зжер RAM: ssh aione-vps '\''ps aux --sort=-rss | head; dmesg | grep -i oom | tail'\''' "$(echo "$out" | tr '\n' '|' | cut -c1-240)")"; then
    mv -f "$tmp" "$cf"                                                 # delivered: promote cursor
  else
    rm -f "$tmp"                                                       # failed: re-read next tick
  fi
  return 0
}

check_bans() {
  [ -r "$FAIL2BAN_LOG" ] || { log "cannot read $FAIL2BAN_LOG"; return; }
  local cf="$STATE_DIR/fail2ban.cursor" cur_inode cur_size s_inode s_off new count detail
  cur_inode="$(stat -c %i "$FAIL2BAN_LOG" 2>/dev/null)"
  cur_size="$(stat -c %s "$FAIL2BAN_LOG" 2>/dev/null)"
  s_inode=""; s_off=0
  if [ -f "$cf" ]; then read -r s_inode s_off < "$cf" 2>/dev/null; fi
  [ -z "${s_off:-}" ] && s_off=0
  if [ "$cur_inode" != "$s_inode" ] || [ "$cur_size" -lt "$s_off" ]; then s_off=0; fi   # rotation/truncation
  if [ -z "$s_inode" ]; then echo "$cur_inode $cur_size" > "$cf"; return 0; fi          # first observation: seed
  new="$(tail -c "+$((s_off + 1))" "$FAIL2BAN_LOG" 2>/dev/null | grep -E '\] Ban ')"
  count="$(printf '%s' "$new" | grep -c 'Ban ')"
  if [ "$count" -lt "$BAN_SPIKE" ]; then
    echo "$cur_inode $cur_size" > "$cf"                                # nothing to instant-send: advance
    [ "$count" -gt 0 ] && add_ban_tally "$count"
    return 0
  fi
  detail="$(printf '%s\n' "$new" | sed -nE 's/.*\[([^]]+)\] Ban ([0-9a-fA-F.:]+).*/\1/p' | sort | uniq -c | tr '\n' ' ')"
  if notify "$(printf '🚫 сплеск банів: %s нових за ~1 хв [%s]\nЩо: активний брутфорс/флуд — fail2ban уже банить IP\nДія: нічого термінового; периметр тримає. Глянь якщо цікаво: ssh aione-vps '\''sudo fail2ban-client status'\''' "$count" "$detail")"; then
    echo "$cur_inode $cur_size" > "$cf"                                # delivered: advance + tally
    add_ban_tally "$count"
  fi                                                                    # failed: keep old offset -> re-detect
  return 0
}

check_ssh_logins() {
  local cf="$STATE_DIR/ssh.cursor" tmp lines line user ip seenf last allok lbl msg fp keylbl fpline own_n=0 other_n=0
  if [ ! -f "$cf" ]; then
    journalctl -u ssh.service -n1 --cursor-file="$cf" -o cat -q >/dev/null 2>&1   # seed only
    return 0
  fi
  tmp="$cf.next"; cp -f "$cf" "$tmp" 2>/dev/null
  lines="$(journalctl -u ssh.service --cursor-file="$tmp" -o cat -q 2>/dev/null \
          | grep -E 'Accepted (publickey|password|keyboard-interactive)')"
  if [ -z "$lines" ]; then mv -f "$tmp" "$cf"; return 0; fi
  allok=1
  while IFS= read -r line; do
    [ -z "$line" ] && continue
    user="$(echo "$line" | sed -nE 's/.*Accepted [a-z-]+ for ([^ ]+) from .*/\1/p')"
    ip="$(echo "$line"   | sed -nE 's/.*from ([0-9a-fA-F.:]+) port .*/\1/p')"
    [ -z "$ip" ] && continue
    # key fingerprint (publickey logins only; password/kbd-int leave it empty)
    fp="$(echo "$line" | sed -nE 's/.*ssh2: [A-Z0-9]+ (SHA256:[A-Za-z0-9+/=]+).*/\1/p')"
    keylbl="$(key_label "$fp")"
    fpline=""; [ -n "$fp" ] && fpline="$(printf '\nКлюч: %s%s' "$fp" "${keylbl:+ — $keylbl}")"
    lbl="$(ip_label "$ip")"
    # ADR-0106 §3.2: свій вхід (відомий ключ І відома мережа) — без повідомлення, лише в ранковий звіт
    if [ -n "$keylbl" ] && [ -n "$lbl" ]; then
      own_n=$(( own_n + 1 ))
      continue
    fi
    other_n=$(( other_n + 1 ))
    seenf="$STATE_DIR/sshseen.$(echo "$ip" | tr './:' '___')"
    last="$(cat "$seenf" 2>/dev/null || echo 0)"
    if [ "$(( $(now) - last ))" -ge "$SSH_DEDUP_S" ]; then
      if [ -n "$keylbl" ]; then
        msg="$(printf '🔑 SSH: твій ключ, але нова мережа — %s з %s%s\nЩо: якщо ти в дорозі чи з іншого інтернету — все гаразд\nДія: якщо це не ти — зміни ключі; свою мережу додай у %s' "${user:-?}" "$ip" "$fpline" "$KNOWN_IPS_FILE")"
      else
        msg="$(printf '🚨 SSH-вхід НЕВІДОМИМ ключем: %s з %s%s%s\nЩо: ключа немає серед відомих — доступ має хтось інший\nДія: якщо це не ти — ТЕРМІНОВО зміни ключі: ssh aione-vps '\''last -20'\''' "${user:-?}" "$ip" "${lbl:+ ($lbl)}" "$fpline")"
      fi
      if notify "$msg"; then
        now > "$seenf"
      else
        allok=0
      fi
    fi
  done <<< "$lines"
  if [ "$allok" -eq 1 ]; then                                              # promote cursor only if all sent;
    mv -f "$tmp" "$cf"                                                     # лічильники — лише разом з курсором,
    add_tally ssh_own.tally "$own_n"; add_tally ssh_other.tally "$other_n"  # інакше повтор тіку порахує двічі
  else
    rm -f "$tmp"
  fi
  return 0
}

# ---------- ранковий звіт (ADR-0106 §3.5) ----------
report_server_line() {
  printf 'Сервер: %s; load %s; памʼять вільно %s; диск %s' \
    "$(uptime -p 2>/dev/null | sed 's/^up //')" \
    "$(awk '{print $1", "$2", "$3}' "$PROC_LOADAVG" 2>/dev/null)" \
    "$(awk '/^MemAvailable:/{a=$2} /^MemTotal:/{t=$2} END{printf "%.1f з %.1f ГБ", a/1048576, t/1048576}' "$PROC_MEMINFO" 2>/dev/null)" \
    "$(df -P / 2>/dev/null | awk 'NR==2{print $5}')"
}

report_programs_lines() {
  local out
  out="$(timeout 10 supervisorctl status 2>/dev/null)"
  [ -z "$out" ] && { printf '❗ Програми: supervisor не відповідає'; return 0; }
  printf '%s\n' "$out" | awk '
    { name=$1; sub(/.*:/, "", name) }
    $2=="RUNNING"                { run = run (run ? ", " : "") name; next }
    $2=="STOPPED" || $2=="EXITED" { off = off (off ? ", " : "") name; next }
                                  { bad = bad (bad ? ", " : "") name " " $2 }
    END {
      printf "Працюють: %s", (run ? run : "жодна")
      if (off) printf "\nВимкнені: %s", off
      if (bad) printf "\n❗ Не в нормі: %s", bad
    }'
}

report_settle_line() {
  [ -r "$SETTLE_STATUS" ] || { printf 'Нічний settle: стану немає'; return 0; }
  python3 - "$SETTLE_STATUS" "$REPORT_TZ" 2>/dev/null <<'PY' || printf 'Нічний settle: стан не прочитано'
import datetime as dt, json, sys
from zoneinfo import ZoneInfo
status = json.load(open(sys.argv[1], encoding="utf-8"))
finished = status.get("finished_at") or ""
try:  # оркестратор пише UTC без позначки зони (ADR-0103)
    when = dt.datetime.fromisoformat(finished).replace(tzinfo=dt.timezone.utc).astimezone(ZoneInfo(sys.argv[2])).strftime("%d.%m %H:%M")
except ValueError:
    when = finished or "?"
problems = len(status.get("problems") or [])
code = status.get("exit_code")
verdict = "ок" if code == 0 and not problems else "❗ код %s, проблем %d" % (code, problems)
sys.stdout.write("Нічний settle: %s — %s" % (when, verdict))
PY
}

report_archi_line() {
  local bkp
  bkp="$(ls -t "$BACKUP_DIR"/trader-v3-*.tar.gz 2>/dev/null | head -1)"
  if [ -n "$bkp" ]; then
    printf 'Арчі: бекап %s год тому' "$(( ( $(now) - $(stat -c %Y "$bkp" 2>/dev/null || echo 0) ) / 3600 ))"
  else
    printf '❗ Арчі: бекапу немає'
  fi
}

report_ssh_line() {
  local own other
  own="$(cat "$STATE_DIR/ssh_own.tally" 2>/dev/null || echo 0)"; own="${own:-0}"
  other="$(cat "$STATE_DIR/ssh_other.tally" 2>/dev/null || echo 0)"; other="${other:-0}"
  if [ "$own" -eq 0 ] && [ "$other" -eq 0 ]; then printf 'SSH за добу: входів не було'
  elif [ "$other" -eq 0 ]; then printf 'SSH за добу: %s — усі твої' "$own"
  else printf '❗ SSH за добу: твоїх %s, інших %s (про кожен повідомляв)' "$own" "$other"; fi
}

report_events_lines() {
  local f="$STATE_DIR/events.day" n ts title
  n="$(grep -c . "$f" 2>/dev/null)"; n="${n:-0}"
  if [ "$n" -eq 0 ]; then printf 'Події за добу: не було'; return 0; fi
  printf 'Події за добу: %s' "$n"
  [ "$n" -gt 6 ] && printf '\n  …%s раніших пропущено' "$(( n - 6 ))"
  tail -n 6 "$f" | while IFS=$'\t' read -r ts title; do
    printf '\n  %s %s' "$(TZ="$REPORT_TZ" date -d "@$ts" +%H:%M 2>/dev/null)" "$title"
  done
}

report_visitors() {                        # $1=годин (24); зведення ADR-0105 від власника журналу; збій — рядком
  local hours="${1:-24}" text rc
  text="$(cd "$PLATFORM_DIR" 2>/dev/null && timeout "$VISITORS_TIMEOUT_S" runuser -u "$VISITORS_USER" -- \
          env PYTHONIOENCODING=utf-8 ./.venv/bin/python -m tools.visitors.report --apply-retention \
          --hours "$hours" --max-rows "$VISITORS_REPORT_ROWS" 2>>"$LOG")"
  rc=$?
  if [ "$rc" -eq 0 ] && [ -n "$text" ]; then printf '%s' "$text"
  else printf '❗ Відвідувачі: зведення не пораховано (код %s)' "$rc"; fi
}

check_morning_report() {
  local hf="$STATE_DIR/report.date" today text
  today="$(TZ="$REPORT_TZ" date +%F)"
  if [ ! -f "$hf" ]; then echo "$today" > "$hf"; return 0; fi   # перший запуск: перший звіт — наступного ранку
  [ "$(cat "$hf" 2>/dev/null)" = "$today" ] && return 0
  [ "$(TZ="$REPORT_TZ" date +%-H)" -lt "$REPORT_HOUR" ] && return 0
  text="$(printf '☀️ Ранковий звіт — %s\n%s\n%s\n%s\n%s\n%s\nБанів fail2ban: %s\n%s\n—\n%s' \
    "$(TZ="$REPORT_TZ" date +%d.%m)" "$(report_server_line)" "$(report_programs_lines)" "$(report_settle_line)" \
    "$(report_archi_line)" "$(report_ssh_line)" "$(cat "$STATE_DIR/ban.tally" 2>/dev/null || echo 0)" \
    "$(report_events_lines)" "$(report_visitors)")"
  if "$ALERT" "$text"; then                # не notify: сам звіт — не подія доби
    echo "$today" > "$hf"
    : > "$STATE_DIR/events.day"
    echo 0 > "$STATE_DIR/ssh_own.tally"; echo 0 > "$STATE_DIR/ssh_other.tally"; echo 0 > "$STATE_DIR/ban.tally"
    log "morning report sent"
  else
    log "morning report FAILED (retry next tick)"
  fi
  return 0
}

# ---------- команди власника (ADR-0106 §3.7) ----------
command_reply() {                          # $1=команда $2=аргумент -> текст відповіді
  local days
  case "$1" in
    /visitors)
      days="${2:-7}"
      case "$days" in ''|*[!0-9]*) days=7 ;; esac
      [ "$days" -lt 1 ] && days=1
      [ "$days" -gt 365 ] && days=365
      report_visitors "$(( days * 24 ))" ;;
    /status)
      printf '📟 Стан зараз — %s\n%s\n%s\n%s\n%s\n%s\nБанів fail2ban з ранку: %s\n%s' \
        "$(TZ="$REPORT_TZ" date '+%d.%m %H:%M')" "$(report_server_line)" "$(report_programs_lines)" \
        "$(report_settle_line)" "$(report_archi_line)" "$(report_ssh_line | sed 's/за добу/з ранку/')" \
        "$(cat "$STATE_DIR/ban.tally" 2>/dev/null || echo 0)" "$(report_events_lines | sed 's/за добу/з ранку/')" ;;
    *)
      printf 'Команди:\n/visitors — відвідувачі за 7 днів (/visitors 30 — за 30)\n/status — стан сервера зараз' ;;
  esac
}

# Нові повідомлення боту -> відповіді. offset рухається лише після відповідей на ВСІ команди пачки (повтор тіку
# може продублювати відповідь, але не загубити команду); перший запуск лише засіває offset — старі команди не виконуються.
check_commands() {
  [ -x "$COMMANDS" ] || return 0
  local of="$STATE_DIR/tg.offset" out next line cmd arg rest
  out="$("$COMMANDS" "$(cat "$of" 2>/dev/null || echo seed)" 2>>"$LOG")" || { log "commands: poll failed (retry next tick)"; return 0; }
  next="$(printf '%s\n' "$out" | sed -n 's/^NEXT \([0-9][0-9]*\)$/\1/p')"
  [ -n "$next" ] || { log "commands: no NEXT in poll output"; return 0; }
  while IFS= read -r line; do
    case "$line" in "CMD "*) ;; *) continue ;; esac
    read -r cmd arg rest <<< "${line#CMD }"
    log "command $cmd ${arg:-}"
    "$ALERT" "$(command_reply "$cmd" "${arg:-}")" || { log "commands: reply FAILED (retry next tick)"; return 0; }
  done <<< "$out"
  echo "$next" > "$of"
  return 0
}

# ---------- main ----------
# Single-run lock: a slow tick + the next timer fire (or a manual test run racing
# the timer) must not interleave state writes. Best-effort (proceed if flock absent).
if command -v flock >/dev/null 2>&1 && exec 9>"$STATE_DIR/.watchdog.lock" 2>/dev/null; then
  flock -n 9 || { log "overlapping run holds the lock; skipping tick"; exit 0; }
fi

log "tick start"
check_services   || log "check_services errored"
check_supervisor || log "check_supervisor errored"
check_backup     || log "check_backup errored"
check_resources  || log "check_resources errored"
check_oom        || log "check_oom errored"
check_bans       || log "check_bans errored"
check_ssh_logins || log "check_ssh_logins errored"
check_morning_report || log "check_morning_report errored"
check_commands   || log "check_commands errored"
log "tick done"
exit 0
