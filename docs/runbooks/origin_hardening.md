# Runbook — Origin Hardening (Cloudflare-only ingress + SSH key-only)

> **Мета**: перед відкриттям публіці закрити origin так, щоб `80/443` приймались
> **тільки** з Cloudflare-ренджів (немає direct-to-origin в обхід WAF/DDoS), і щоб
> SSH був **key-only** (`PasswordAuthentication no` — ефективно, не лише у файлі).
>
> **Інструмент**: `tools/origin_firewall.sh` (UFW — стандарт runbook; той самий шар,
> де минулий 522 стався через неповний CF-allowlist).
>
> **Ризик**: host-level зміни → можливий **lockout**. Цей runbook = послідовність,
> що його унеможливлює.

---

## 0. Передумови (1 хв) — не пропускай

1. **Відкрий ДРУГУ SSH-сесію** до VPS і **тримай її відкритою** до кінця кроку 5.
   Якщо щось піде не так — у тебе є жива сесія для `rollback`.
2. Переконайся, що твій публічний ключ на сервері:

   ```bash
   ssh aione-vps 'grep -cE "^(ssh-|ecdsa-|sk-)" ~/.ssh/authorized_keys; echo root:; sudo grep -cE "^(ssh-|ecdsa-|sk-)" /root/.ssh/authorized_keys 2>/dev/null'
   ```

   Має бути `>= 1`. Якщо `0` — спершу `ssh-copy-id`, інакше harden заблокує доступ
   (скрипт це й сам не дасть зробити без `--force`).

> ⚠️ **Чому "password-auth мав би бути off, але може й не бути" (Ubuntu 22.04 trap)**
> `/etc/ssh/sshd_config.d/50-cloud-init.conf` часто містить `PasswordAuthentication yes`
> і **перекриває** головний `sshd_config` (sshd бере **перше** значення кожного ключа).
> Тому правда — **тільки** `sudo sshd -T | grep -i passwordauth`, а не `grep` у файлі.
> Скрипт ставить drop-in `00-aione-hardening.conf` (читається першим → виграє) **і**
> нейтралізує конфліктні `yes`.

---

## 1. Деплой скрипта на VPS

```bash
# з локального репо
scp tools/origin_firewall.sh aione-vps:/opt/smc-v3/tools/origin_firewall.sh

ssh aione-vps '
  sed -i "s/\r$//" /opt/smc-v3/tools/origin_firewall.sh   # CRLF guard (редаговано на Windows)
  chmod 755 /opt/smc-v3/tools/origin_firewall.sh
'
```

> Стабільний шлях важливий: `install-timer` зашиває абсолютний шлях у systemd unit.

---

## 2. AUDIT спочатку (read-only — нічого не змінює)

Це і є "дослідження що і як зараз". Безпечно запускати будь-коли.

```bash
ssh aione-vps 'sudo /opt/smc-v3/tools/origin_firewall.sh audit'
```

Звіт покаже по кожному контролю `OK` / `GAP`:

- **SSH** — ефективні `passwordauthentication` / `pubkeyauthentication` / `permitrootlogin`
  (через `sshd -T`), хто саме задає `PasswordAuthentication` і в якому порядку, к-сть `authorized_keys`.
- **UFW + CF** — чи активний; чи `80/443` відкриті `Anywhere` (= bypass CF); **які CF-ренджі
  відсутні** в allowlist (саме це давало selective 522).
- **Listening** — що слухає `0.0.0.0`/`::` (ws_server `:8000` має бути `127.0.0.1`).
- **fail2ban** — стан `sshd` jail.

**Розв'язка**: якщо `GAP не знайдено` — origin уже замкнений, далі можна не йти.
Інакше — крок 3.

---

## 3. HARDEN (застосувати)

```bash
ssh aione-vps 'sudo /opt/smc-v3/tools/origin_firewall.sh harden'
```

Що робить (саме в цьому порядку — анти-lockout):

1. **UFW**: дозволяє SSH-порт **ПЕРШИМ** → `default deny incoming` → прибирає world-open
   `80/443` → додає CF v4+v6 на `80,443` (тег `# cf-origin`) → `enable`.
   Захист: якщо CF-список з fetch підозріло малий — **не застосовує** (не обнуляє allowlist).
2. **SSH**: backup → нейтралізує конфліктні `yes` → пише `00-aione-hardening.conf`
   (`PasswordAuthentication no`, `Pubkey yes`) → `sshd -t` валідація → перевіряє, що
   `sshd -T` дає `passwordauthentication=no` → **`reload` (не restart)** → поточні сесії живі.

Опції: `--skip-ssh` (лише firewall), `--skip-ufw` (лише SSH),
`--force` (вимкнути password-auth навіть без ключа — **РИЗИК**, не треба).

---

## 4. ПЕРЕВІРКА (обов'язково, не закриваючи поточну сесію)

```bash
# 4.1 НОВА сесія, лише ключем (НЕ має просити пароль):
ssh -o PasswordAuthentication=no <user>@<host>

# 4.2 Ефективний SSH (істина):
ssh aione-vps 'sudo sshd -T | grep -iE "passwordauthentication|pubkeyauthentication"'
#   очікувано: passwordauthentication no / pubkeyauthentication yes

# 4.3 Firewall:
ssh aione-vps 'sudo ufw status verbose'

# 4.4 Сайт живий через Cloudflare:
curl -I https://aione-smc.com/        # 200/301
curl -I https://archi.aione-smc.com/  # той самий VPS — теж за CF

# 4.5 Повторний аудит — має бути 0 GAP:
ssh aione-vps 'sudo /opt/smc-v3/tools/origin_firewall.sh audit'
```

> Тільки коли **4.1 пройшов** (новий ключовий вхід працює) — можна закривати стару сесію.

---

## 5. Observation window (D9.1) — 60s після зміни

Зміна зачіпає **обидва** сервіси на VPS (platform `aione-smc.com` + Archi `archi.aione-smc.com`).

```bash
ssh aione-vps 'for i in 1 2 3 4 5 6; do echo "=== T+$((i*10))s ==="; \
  sudo ufw status | head -1; \
  curl -s -o /dev/null -w "platform=%{http_code} " https://aione-smc.com/; \
  curl -s -o /dev/null -w "archi=%{http_code}\n" https://archi.aione-smc.com/; \
  sleep 10; done'
```

**STOP-сигнали** → одразу rollback: будь-який `522` у браузері, `000`/`522` у curl через CF,
SSH-сесія відвалюється, новий ключовий вхід не працює.

---

## 6. Auto-update CF-ренджів (опційно — "можна, якщо треба")

Ренджі Cloudflare змінюються (нещодавно прибрали `2405:b500::/32` з v6, розбили
`104.16.0.0/12` на `/13`+`/14`). Щоб allowlist не застарів і знову не дав 522:

```bash
ssh aione-vps 'sudo /opt/smc-v3/tools/origin_firewall.sh install-timer'

# перевірка:
ssh aione-vps 'systemctl list-timers cf-ufw-update.timer; \
  journalctl -u cf-ufw-update.service --no-pager -n 20'
```

Щоденно (з рандомним зсувом) fetch свіжих ренджів → atomic re-sync лише CF-правил
(SSH/інше не чіпає) → лог у `journalctl`. Ручний прогін: `... origin_firewall.sh update-cf`.

---

## 7. ROLLBACK (якщо щось не так)

```bash
ssh aione-vps 'sudo /opt/smc-v3/tools/origin_firewall.sh rollback'
```

Відновлює `sshd_config` + drop-in'и та UFW-правила з останніх backup у
`/var/backups/origin-firewall/`, валідує `sshd -t`, робить `reload`.
Timer (крок 6) знімається окремо: `sudo systemctl disable --now cf-ufw-update.timer`.

---

## Опційно — додаткове підсилення (не входить у базовий запит)

- **Root login**: `PermitRootLogin prohibit-password` (ключ-only для root, або `no` якщо
  логінишся під non-root sudo-юзером). Додай у `00-aione-hardening.conf` за бажанням.
- **fail2ban**: якщо `audit` показав відсутність — `apt-get install -y fail2ban`
  закриває SSH brute-force (доповнює key-only).
- **Cloudflare real-IP у nginx**: щоб логи/rate-limit бачили реальний IP клієнта, а не CF —
  перевір `/etc/nginx/conf.d/realip_cloudflare.conf` (теж потребує свіжих CF-ренджів).

---

## Інваріанти / контекст

- **UFW** — стандарт цього runbook (не nftables), мінімальний drift. `# cf-origin` — тег
  наших правил для idempotent re-sync.
- **SSOT CF-ренджів** = live fetch на VPS; вбудований список у скрипті = **dated fallback**
  (2026-06), вживається лише коли CDN недосяжний.
- **Degraded-but-loud**: підозріло малий fetch → skip (не обнуляє allowlist), гучний лог.
- **`reload`, не `restart`** sshd → активні сесії не рвуться під час зміни.
