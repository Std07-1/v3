#!/usr/bin/env bash
# =============================================================================
# origin_firewall.sh — origin hardening для VPS за Cloudflare
# -----------------------------------------------------------------------------
# Мета (контекст: "відкриваюся публіці"):
#   1. 80/443 на origin приймаються ТІЛЬКИ з Cloudflare IP-ренджів
#      (закриває direct-to-origin bypass WAF/DDoS Cloudflare).
#   2. SSH key-only: PasswordAuthentication no (ефективно, не лише у файлі).
#
# Підкоманди:
#   audit          (default) read-only звіт поточного стану. БЕЗПЕЧНО.
#   harden         застосувати UFW CF-only + SSH key-only (з backup + guard).
#   update-cf      перебудувати лише CF-allowlist під свіжі ренджі (для timer).
#   install-timer  systemd timer: щоденний refresh CF-ренджів.
#   rollback       відкотити SSH+UFW з останніх backup у BACKUP_DIR.
#
# SSOT Cloudflare-ренджів = LIVE fetch на VPS (cloudflare.com/ips-v4|v6).
# Вбудований список = ДАТОВАНИЙ snapshot-fallback (лише якщо fetch недоступний).
#
# КРИТИЧНО про SSH (Ubuntu 22.04 trap):
#   /etc/ssh/sshd_config.d/50-cloud-init.conf часто містить
#   "PasswordAuthentication yes" і ПЕРЕКРИВАЄ головний sshd_config
#   (sshd: "the first obtained value for each parameter is used").
#   Тому drop-in тут називається 00-* (читається ПЕРШИМ -> виграє),
#   плюс конфліктні "yes" нейтралізуються. Істина = `sshd -T`, не grep файлу.
#
# Snapshot embedded ренджів: 2026-06 (v6 без 2405:b500::/32 — його прибрали).
# =============================================================================
set -uo pipefail

# ── Константи (без magic values) ────────────────────────────────────────────
readonly CF_V4_URL="https://www.cloudflare.com/ips-v4"
readonly CF_V6_URL="https://www.cloudflare.com/ips-v6"
readonly CF_MIN_V4=12          # floor: live-список < цього => truncation => fallback
readonly CF_MIN_V6=4
readonly HTTP_PORTS="80,443"
readonly UFW_CF_COMMENT="cf-origin"
readonly SSH_RULE_COMMENT="ssh-admin"
readonly HARDENING_DROPIN="/etc/ssh/sshd_config.d/00-aione-hardening.conf"
readonly BACKUP_DIR="/var/backups/origin-firewall"
readonly FETCH_TIMEOUT=10

# Snapshot-fallback (2026-06). LIVE fetch має пріоритет; це лише якщо CDN недоступний.
readonly CF_V4_FALLBACK=(
  173.245.48.0/20 103.21.244.0/22 103.22.200.0/22 103.31.4.0/22
  141.101.64.0/18 108.162.192.0/18 190.93.240.0/20 188.114.96.0/20
  197.234.240.0/22 198.41.128.0/17 162.158.0.0/15 104.16.0.0/13
  104.24.0.0/14 172.64.0.0/13 131.0.72.0/22
)
readonly CF_V6_FALLBACK=(
  2400:cb00::/32 2606:4700::/32 2803:f800::/32
  2405:8100::/32 2a06:98c0::/29 2c0f:f248::/32
)

# ── Кольори / логи (degraded-but-loud) ──────────────────────────────────────
if [[ -t 1 ]]; then
  C_RED=$'\e[31m'; C_GRN=$'\e[32m'; C_YLW=$'\e[33m'; C_BLU=$'\e[34m'; C_DIM=$'\e[2m'; C_RST=$'\e[0m'
else
  C_RED=''; C_GRN=''; C_YLW=''; C_BLU=''; C_DIM=''; C_RST=''
fi
GAPS=()  # збираємо знахідки аудиту

log()     { printf '%s[*]%s %s\n' "$C_BLU" "$C_RST" "$*"; }
ok()      { printf '%s[OK]%s %s\n' "$C_GRN" "$C_RST" "$*"; }
warn()    { printf '%s[!]%s %s\n' "$C_YLW" "$C_RST" "$*" >&2; }
gap()     { printf '%s[GAP]%s %s\n' "$C_RED" "$C_RST" "$*"; GAPS+=("$*"); }
die()     { printf '%s[FATAL]%s %s\n' "$C_RED" "$C_RST" "$*" >&2; exit 1; }
section() { printf '\n%s== %s ==%s\n' "$C_BLU" "$*" "$C_RST"; }

require_root() { [[ "$(id -u)" -eq 0 ]] || die "Потрібен root: sudo $0 ${CMD:-}"; }

# ── Cloudflare ренджі: live-or-fallback (stdout=ренджі, лог=stderr) ──────────
resolve_cf_list() {
  local url="$1" floor="$2" label="$3"; shift 3
  local fallback=("$@") fetched n
  fetched="$(curl -fsS --max-time "$FETCH_TIMEOUT" "$url" 2>/dev/null \
              | tr -d '\r' | grep -E '^[0-9A-Fa-f:.]+/[0-9]+$' || true)"
  n="$(printf '%s' "$fetched" | grep -c . || true)"
  if [[ "${n:-0}" -ge "$floor" ]]; then
    printf '%s ' "live:$n $label" >&2; echo >&2
    printf '%s\n' "$fetched"
  else
    warn "CF $label: live fetch дав ${n:-0} (< floor $floor) — використовую snapshot (${#fallback[@]})"
    printf '%s\n' "${fallback[@]}"
  fi
}

cf_all_ranges() {
  { resolve_cf_list "$CF_V4_URL" "$CF_MIN_V4" "IPv4" "${CF_V4_FALLBACK[@]}"
    resolve_cf_list "$CF_V6_URL" "$CF_MIN_V6" "IPv6" "${CF_V6_FALLBACK[@]}"; } | sort -u
}

# ── SSH helpers ─────────────────────────────────────────────────────────────
detect_ssh_port() {
  local p
  p="$(sshd -T 2>/dev/null | awk '$1=="port"{print $2; exit}')"
  [[ -z "$p" ]] && p="$(ss -tlnp 2>/dev/null | awk '/sshd/{n=split($4,a,":"); print a[n]; exit}')"
  [[ -z "$p" ]] && p=22
  echo "$p"
}

count_authorized_keys() {
  local total=0 u home akf cnt
  local -a users=()
  [[ -n "${SUDO_USER:-}" ]] && users+=("$SUDO_USER")
  users+=("root")
  for u in "${users[@]}"; do
    home="$(getent passwd "$u" | cut -d: -f6)"
    [[ -z "$home" ]] && continue
    akf="$home/.ssh/authorized_keys"
    if [[ -f "$akf" ]]; then
      cnt="$(grep -cE '^(ssh-|ecdsa-|sk-)' "$akf" 2>/dev/null || echo 0)"
      total=$(( total + cnt ))
    fi
  done
  echo "$total"
}

# ── UFW helpers ─────────────────────────────────────────────────────────────
# Джерела (From) для всіх ALLOW-правил, що стосуються 80/443. Обробляє "(v6)" зсув.
http_allowed_sources() {
  ufw status 2>/dev/null | sed 's/#.*//' | awk '
    $1 ~ /(^|,)(80|443)(\/|$)/ {
      for (i=1;i<=NF;i++) if ($i=="ALLOW") { print $(i+1); break }
    }'
}
# Джерела наших CF-правил (за коментарем-тегом).
applied_cf_sources() {
  ufw status 2>/dev/null | grep -F "# $UFW_CF_COMMENT" | awk '
    { for (i=1;i<=NF;i++) if ($i=="ALLOW") { print $(i+1); break } }' | sort -u
}

ensure_ufw_ipv6() {
  local f=/etc/default/ufw
  [[ -f "$f" ]] || return 0
  if ! grep -qiE '^IPV6=yes' "$f"; then
    backup_file "$f"
    sed -ri 's/^IPV6=.*/IPV6=yes/I' "$f" || echo 'IPV6=yes' >> "$f"
    warn "Увімкнено IPV6=yes у $f (CF має IPv6-ренджі)."
  fi
}

remove_world_open_http() {
  local spec
  for spec in "80/tcp" "443/tcp" "80,443/tcp" "80" "443" "Nginx Full" "Nginx HTTP" "Nginx HTTPS" "WWW Full" "WWW"; do
    if ufw delete allow "$spec" >/dev/null 2>&1; then
      warn "  - прибрано широке правило: allow $spec (було world-open)"
    fi
  done
}

# Add missing CF-ренджі, видалити застарілі (ті, що вже не в CF). Не чіпає SSH/інше.
reconcile_cf_rules() {
  local -a desired=("$@")
  local -a applied=()
  mapfile -t applied < <(applied_cf_sources)
  local c added=0 removed=0
  for c in "${desired[@]}"; do
    [[ -z "$c" ]] && continue
    if ! printf '%s\n' "${applied[@]:-}" | grep -qxF "$c"; then
      if ufw allow from "$c" to any port "$HTTP_PORTS" proto tcp comment "$UFW_CF_COMMENT" >/dev/null 2>&1; then
        log "  + allow $c -> $HTTP_PORTS"; (( added++ )) || true
      fi
    fi
  done
  for c in "${applied[@]:-}"; do
    [[ -z "$c" ]] && continue
    if ! printf '%s\n' "${desired[@]}" | grep -qxF "$c"; then
      ufw delete allow from "$c" to any port "$HTTP_PORTS" proto tcp >/dev/null 2>&1 || true
      warn "  - застарілий CF-рендж прибрано: $c"; (( removed++ )) || true
    fi
  done
  log "CF-allowlist: +$added / -$removed (всього бажаних: ${#desired[@]})"
}

# ── Backup / restore ────────────────────────────────────────────────────────
backup_file() {
  local f="$1" dest
  [[ -f "$f" ]] || return 0
  mkdir -p "$BACKUP_DIR"
  dest="$BACKUP_DIR/$(echo "$f" | tr '/' '_').$(date +%Y%m%d-%H%M%S).bak"
  cp -a "$f" "$dest" && log "  backup: $f -> $dest"
}
restore_latest() {
  local f="$1" key latest
  key="$(echo "$f" | tr '/' '_')"
  latest="$(ls -1t "$BACKUP_DIR/${key}."*.bak 2>/dev/null | head -1 || true)"
  [[ -n "$latest" ]] && { cp -a "$latest" "$f" && log "  restored $f <- $latest"; }
}

# ── Нейтралізація конфліктних PasswordAuthentication yes ─────────────────────
neutralize_password_auth_conflicts() {
  local f
  for f in /etc/ssh/sshd_config /etc/ssh/sshd_config.d/*.conf; do
    [[ -f "$f" ]] || continue
    [[ "$f" == "$HARDENING_DROPIN" ]] && continue
    if grep -qiE '^[[:space:]]*PasswordAuthentication[[:space:]]+yes' "$f"; then
      backup_file "$f"
      sed -ri 's/^([[:space:]]*)(PasswordAuthentication[[:space:]]+yes)/\1# \2  # neutralized by origin_firewall.sh/I' "$f"
      warn "  нейтралізовано 'PasswordAuthentication yes' у $f"
    fi
  done
}

# =============================================================================
# AUDIT (read-only)
# =============================================================================
audit_ssh() {
  section "SSH — ефективна конфігурація (sshd -T = істина)"
  if ! command -v sshd >/dev/null 2>&1; then gap "sshd не знайдено"; return; fi
  local eff_pw eff_pk eff_root eff_port
  eff_pw="$(sshd -T 2>/dev/null   | awk '$1=="passwordauthentication"{print $2}')"
  eff_pk="$(sshd -T 2>/dev/null   | awk '$1=="pubkeyauthentication"{print $2}')"
  eff_root="$(sshd -T 2>/dev/null | awk '$1=="permitrootlogin"{print $2}')"
  eff_port="$(detect_ssh_port)"
  printf '  port=%s  passwordauthentication=%s  pubkeyauthentication=%s  permitrootlogin=%s\n' \
         "$eff_port" "${eff_pw:-?}" "${eff_pk:-?}" "${eff_root:-?}"
  if [[ "${eff_pw:-}" == "no" ]]; then ok "PasswordAuthentication=no (key-only діє)"
  else gap "PasswordAuthentication=${eff_pw:-невідомо} (НЕ key-only). Ймовірно 50-cloud-init.conf перекриває файл."; fi
  [[ "${eff_pk:-}" == "yes" ]] || gap "PubkeyAuthentication=${eff_pk:-?} — має бути yes"

  echo "  ${C_DIM}--- хто задає PasswordAuthentication (порядок = пріоритет) ---${C_RST}"
  grep -rniE '^[[:space:]]*PasswordAuthentication' /etc/ssh/sshd_config /etc/ssh/sshd_config.d/ 2>/dev/null \
    | sed 's/^/    /' || echo "    (нічого явного — діє дефолт sshd)"

  local keys; keys="$(count_authorized_keys)"
  if [[ "$keys" -gt 0 ]]; then ok "authorized_keys знайдено: $keys (admin-користувачі)"
  else gap "0 authorized_keys для admin-користувачів — вимикати password-auth НЕБЕЗПЕЧНО без ключа"; fi
}

audit_ufw_cf() {
  section "FIREWALL — UFW + Cloudflare allowlist (80/443)"
  if ! command -v ufw >/dev/null 2>&1; then gap "ufw не встановлено"; return; fi
  local active; active="$(ufw status 2>/dev/null | awk 'NR==1{print $2}')"
  ufw status verbose 2>/dev/null | sed 's/^/  /'
  [[ "$active" == "active" ]] || gap "UFW неактивний — host firewall не діє"

  local -a applied desired missing
  mapfile -t applied < <(http_allowed_sources)
  mapfile -t desired < <(cf_all_ranges)

  if printf '%s\n' "${applied[@]:-}" | grep -qiE '^Anywhere'; then
    gap "80/443 відкриті для Anywhere — origin доступний В ОБХІД Cloudflare (DDoS/WAF bypass, leak origin IP)."
  fi
  if [[ "${#applied[@]}" -eq 0 ]]; then
    gap "Немає жодного ALLOW на 80/443 — або CF-allowlist відсутній, або сайт недоступний."
  else
    # Чого з CF бракує в allowlist (це і давало selective 522).
    mapfile -t missing < <(comm -23 <(printf '%s\n' "${desired[@]}" | sort -u) \
                                     <(printf '%s\n' "${applied[@]}"  | sort -u))
    if [[ "${#missing[@]}" -gt 0 ]]; then
      gap "Відсутні CF-ренджі в allowlist (${#missing[@]}): ${missing[*]}"
      echo "      ${C_DIM}^ саме неповний список давав selective 522 раніше${C_RST}"
    else
      ok "CF-allowlist повний відносно поточних CF-ренджів (${#desired[@]})"
    fi
  fi
}

audit_listening() {
  section "LISTENING — що слухає назовні (0.0.0.0 / ::)"
  if ! command -v ss >/dev/null 2>&1; then warn "ss недоступний"; return; fi
  ss -tlnH 2>/dev/null | awk '{print $4}' | while read -r a; do
    case "$a" in
      127.0.0.1:*|\[::1\]:*) : ;;  # loopback — ок
      0.0.0.0:*|\[::\]:*|\*:*) echo "  ${C_YLW}назовні:${C_RST} $a" ;;
      *) echo "  $a" ;;
    esac
  done
  echo "  ${C_DIM}^ 8000 (ws_server) має бути 127.0.0.1, не 0.0.0.0 — інакше йде в обхід nginx/CF${C_RST}"
}

audit_fail2ban() {
  section "FAIL2BAN"
  if ! command -v fail2ban-client >/dev/null 2>&1; then warn "fail2ban не встановлено (опціонально, але бажано для SSH)"; return; fi
  fail2ban-client status sshd 2>/dev/null | sed 's/^/  /' || warn "немає sshd jail"
}

audit_verdict() {
  section "ПІДСУМОК"
  if [[ "${#GAPS[@]}" -eq 0 ]]; then
    ok "GAP не знайдено — origin виглядає замкненим на CF + SSH key-only."
  else
    printf '%s%d GAP до закриття:%s\n' "$C_RED" "${#GAPS[@]}" "$C_RST"
    local g; for g in "${GAPS[@]}"; do echo "  - $g"; done
    echo
    echo "Закрити: sudo $0 harden   (спершу прочитай runbook; тримай 2-гу SSH-сесію відкритою)"
  fi
}

cmd_audit() {
  section "ORIGIN AUDIT — $(hostname 2>/dev/null) — $(date -u +%FT%TZ) UTC"
  [[ "$(id -u)" -ne 0 ]] && warn "Без root: sshd -T / ufw обмежені. Краще: sudo $0 audit"
  audit_ssh
  audit_ufw_cf
  audit_listening
  audit_fail2ban
  audit_verdict
}

# =============================================================================
# HARDEN
# =============================================================================
phase_ufw_harden() {
  local ssh_port; ssh_port="$(detect_ssh_port)"
  section "UFW: CF-only на 80/443, SSH збережено ($ssh_port)"
  command -v ufw >/dev/null 2>&1 || die "ufw не встановлено: apt-get install -y ufw"

  # 1) SSH дозволити ПЕРШИМ — анти-lockout перед будь-яким deny.
  ufw allow "${ssh_port}/tcp" comment "$SSH_RULE_COMMENT" >/dev/null 2>&1 || true
  ok "SSH ${ssh_port}/tcp дозволено (анти-lockout)."

  # 2) Резолвимо CF-список + floor-guard ЩЕ ДО deny — щоб збійний fetch не поклав сайт.
  local -a cf; mapfile -t cf < <(cf_all_ranges)
  [[ "${#cf[@]}" -ge "$CF_MIN_V4" ]] || die "CF-список підозріло малий (${#cf[@]}) — НЕ застосовую (захист від обнулення allowlist)."

  # 3) IPv6 у UFW (CF має IPv6) + дефолтні політики.
  ensure_ufw_ipv6
  ufw default deny incoming  >/dev/null 2>&1 || true
  ufw default allow outgoing >/dev/null 2>&1 || true

  # 4) прибрати world-open 80/443 (працює і до enable).
  remove_world_open_http

  # 5) enable ПЕРЕД додаванням CF — щоб IPV6=yes став активним до v6-правил.
  ufw --force enable >/dev/null 2>&1 || true

  # 6) синхронізувати CF-ренджі (тег # cf-origin, idempotent).
  reconcile_cf_rules "${cf[@]}"
  ufw reload >/dev/null 2>&1 || true
  ok "UFW активний. 80/443 = тільки Cloudflare."
}

phase_ssh_harden() {
  local force="$1"
  section "SSH: key-only (PasswordAuthentication no)"
  command -v sshd >/dev/null 2>&1 || die "sshd не знайдено"

  local keys; keys="$(count_authorized_keys)"
  if [[ "$keys" -eq 0 ]]; then
    [[ "$force" == "yes" ]] || die "0 authorized_keys для admin — вимкнення password-auth ЗАБЛОКУЄ доступ. Додай SSH-ключ, або --force якщо впевнений."
    warn "0 authorized_keys, але --force: продовжую (РИЗИК LOCKOUT на твою відповідальність)."
  else
    ok "authorized_keys: $keys — key-login можливий."
  fi

  backup_file /etc/ssh/sshd_config
  local f; for f in /etc/ssh/sshd_config.d/*.conf; do backup_file "$f"; done

  neutralize_password_auth_conflicts

  # Drop-in 00-* читається ПЕРШИМ => "first value wins" => перекриває 50-cloud-init.conf.
  cat > "$HARDENING_DROPIN" <<'EOF'
# AIONE origin hardening — SSH key-only.
# 00- => читається першим серед drop-in; sshd бере перше значення кожного ключа,
# тому це перекриває 50-cloud-init.conf (sshd_config(5)).
PasswordAuthentication no
KbdInteractiveAuthentication no
ChallengeResponseAuthentication no
PubkeyAuthentication yes
EOF
  chmod 644 "$HARDENING_DROPIN"
  log "Записано $HARDENING_DROPIN"

  sshd -t 2>/dev/null || die "sshd -t FAILED — конфіг невалідний. НЕ перезавантажую. Відкат: sudo $0 rollback"

  local eff; eff="$(sshd -T 2>/dev/null | awk '$1=="passwordauthentication"{print $2}')"
  [[ "$eff" == "no" ]] || die "Ефективно passwordauthentication=$eff (очікувалось no). Конфлікт у drop-in. Перевір: sshd -T | grep -i passwordauth"

  systemctl reload ssh 2>/dev/null || systemctl reload sshd 2>/dev/null || die "reload ssh не вдався — НЕ закривай поточну сесію, перевір вручну."
  ok "SSH key-only діє ефективно. Reload (не restart) — поточні сесії живі."
}

cmd_harden() {
  require_root
  local force="no" skip_ssh="no" skip_ufw="no" a
  for a in "$@"; do
    case "$a" in
      --force) force="yes" ;;
      --skip-ssh) skip_ssh="yes" ;;
      --skip-ufw) skip_ufw="yes" ;;
      *) die "Невідомий аргумент: $a" ;;
    esac
  done

  warn "HARDEN — це host-level зміни з ризиком lockout."
  warn "ПЕРЕД продовженням ВІДКРИЙ ДРУГУ SSH-сесію і тримай її до кінця перевірки."
  log  "Backup-каталог: $BACKUP_DIR | Відкат: sudo $0 rollback"

  [[ "$skip_ufw" == "yes" ]] || phase_ufw_harden
  [[ "$skip_ssh" == "yes" ]] || phase_ssh_harden "$force"

  section "ПЕРЕВІРКА (зроби ОБОВ'ЯЗКОВО, не закриваючи поточну сесію)"
  cat <<EOF
  1) НОВА сесія ключем:    ssh -o PasswordAuthentication=no <user>@<host>
  2) Ефективний SSH:       sudo sshd -T | grep -iE 'passwordauthentication|pubkeyauthentication'
  3) UFW:                  sudo ufw status verbose
  4) Origin лише з CF:     curl -I https://<домен>/   (через CF -> 200/301)
  5) Повторний аудит:      sudo $0 audit
EOF
  ok "HARDEN завершено. Якщо щось не так — sudo $0 rollback"
}

# =============================================================================
# UPDATE-CF (для systemd timer) — чіпає ЛИШЕ CF-allowlist
# =============================================================================
cmd_update_cf() {
  require_root
  command -v ufw >/dev/null 2>&1 || die "ufw не встановлено"
  local -a cf; mapfile -t cf < <(cf_all_ranges)
  [[ "${#cf[@]}" -ge "$CF_MIN_V4" ]] || die "CF-список малий (${#cf[@]}) — пропускаю refresh (захист від обнулення)."
  reconcile_cf_rules "${cf[@]}"
  ufw reload >/dev/null 2>&1 || true
  ok "update-cf: allowlist синхронізовано ($(date -u +%FT%TZ)Z)."
}

# =============================================================================
# INSTALL-TIMER
# =============================================================================
cmd_install_timer() {
  require_root
  local self; self="$(readlink -f "$0")"
  cat > /etc/systemd/system/cf-ufw-update.service <<EOF
[Unit]
Description=Refresh Cloudflare IP allowlist in UFW (origin_firewall.sh update-cf)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=$self update-cf
EOF
  cat > /etc/systemd/system/cf-ufw-update.timer <<'EOF'
[Unit]
Description=Daily Cloudflare IP allowlist refresh

[Timer]
OnCalendar=daily
RandomizedDelaySec=3600
Persistent=true

[Install]
WantedBy=timers.target
EOF
  systemctl daemon-reload
  systemctl enable --now cf-ufw-update.timer >/dev/null 2>&1 || true
  ok "Timer встановлено: cf-ufw-update.timer (daily)."
  log "Скрипт-шлях у unit: $self (не переміщуй файл, інакше онови unit)."
  log "Лог: journalctl -u cf-ufw-update.service --no-pager | Стан: systemctl list-timers cf-ufw-update.timer"
}

# =============================================================================
# ROLLBACK
# =============================================================================
cmd_rollback() {
  require_root
  section "ROLLBACK — відновлення SSH + UFW з $BACKUP_DIR"
  [[ -f "$HARDENING_DROPIN" ]] && { rm -f "$HARDENING_DROPIN"; warn "видалено $HARDENING_DROPIN"; }
  restore_latest /etc/ssh/sshd_config
  local f; for f in /etc/ssh/sshd_config.d/*.conf; do restore_latest "$f"; done
  if sshd -t 2>/dev/null; then
    systemctl reload ssh 2>/dev/null || systemctl reload sshd 2>/dev/null || true
    ok "sshd відновлено й reload."
  else
    warn "sshd -t FAILED після rollback — перевір вручну, НЕ закривай сесію."
  fi
  restore_latest /etc/ufw/user.rules
  restore_latest /etc/ufw/user6.rules
  ufw reload >/dev/null 2>&1 || true
  ok "UFW-правила відновлено. Перевір: sudo sshd -T | grep -i passwordauth ; sudo ufw status verbose"
}

# ── Dispatch ────────────────────────────────────────────────────────────────
usage() {
  cat <<EOF
origin_firewall.sh — origin hardening для VPS за Cloudflare

  sudo $0 audit            read-only звіт (default, безпечно)
  sudo $0 harden [опції]   UFW CF-only(80/443) + SSH key-only
       --force             вимкнути password-auth навіть без authorized_keys (РИЗИК)
       --skip-ssh          лише firewall, SSH не чіпати
       --skip-ufw          лише SSH, firewall не чіпати
  sudo $0 update-cf        пересинхронізувати лише CF-allowlist
  sudo $0 install-timer    systemd timer: щоденний refresh CF-ренджів
  sudo $0 rollback         відкотити SSH+UFW з останніх backup

SSOT CF-ренджів = live fetch на VPS; вбудований список = dated fallback.
EOF
}

CMD="${1:-audit}"
[[ $# -gt 0 ]] && shift || true
case "$CMD" in
  audit)         cmd_audit "$@" ;;
  harden)        cmd_harden "$@" ;;
  update-cf)     cmd_update_cf "$@" ;;
  install-timer) cmd_install_timer "$@" ;;
  rollback)      cmd_rollback "$@" ;;
  -h|--help|help) usage ;;
  *) usage; die "Невідома підкоманда: $CMD" ;;
esac
# end origin_firewall.sh
