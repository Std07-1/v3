# ADR-0091: Tenant isolation — публічна платформа vs приватні сервіси на одному VPS

## Метадані

| Поле | Значення |
| ---- | -------- |
| ID | ADR-0091 |
| Статус | **Accepted** (owner-рішення 2026-09-06; P1 виконано того ж дня, P2–P7 слайсами — статус у §8) |
| Дата | 2026-09-06 |
| Автори | Станіслав (owner: «V3 публічний, відкритий. Кожен може моніторити графіки, але ніяк не зловживати й проникнути в сам v3, і особливо в приватний дитячий застосунок») + Fable 5.1 (аудит VPS + workflow «public-surface») |
| Будується на | ADR-0060 (deploy discipline), ADR-0076 (unified auth), ADR-0058 (read-only API), `docs/runbooks/origin_hardening.md` (Cloudflare-only ingress, SSH key-only), ADR-0090 (винесення agent bridge — закриває доступ платформи до файлів бота) |
| Поважає | I5 (відмова = 503/401 + WARN, не тихий drop), I1 (UDS єдиний writer — не змінюється), X31 (нуль змін у trader-v3; лише права/користувачі на хості), «Deploy = git» (усі живі конфіги мають reviewable копію в `tools/`) |
| Зачіпає шари | VPS: unix-users, sudoers, права `/opt/*`, supervisor conf, Redis ACL, nginx vhosts, cloudflared; репо: `tools/smc-v3.supervisor.conf`, `tools/nginx/`, `runtime/api_v3/endpoints.py` (`_client_ip`), `runtime/ws/ws_server.py` (`_archi_auth` query-token), `SECURITY.md`, `docs/runbooks/origin_hardening.md` |
| Initiative | `tenant_isolation_v1` |

---

## Quality Axes

- **Ambition target**: **R3** — модель загроз і межа орендарів на спільному хості: публічний сайт з
  нульовою довірою до відвідувача не має шляху до приватних сімейних сервісів навіть через RCE.
- **Maturity impact**: **M3 → M4** на рівні ops (least privilege per program, секрети per program, Redis
  ACL, reviewable конфіги). M5 — після виносу приватних сервісів на окремий хост (§7 п.1).

---

## 1. Контекст: що живе на VPS (інвентар 06.09, секрети масковані)

| Орендар | Процес(и) | Користувач до 06.09 | Дані | Назовні |
|---|---|---|---|---|
| **v3 платформа (публічна)** | `smc-ws`, `smc-fxcm` (+sidecar), `smc-ticks`, `smc-preview`, (`smc-binance*` off) | `ubuntu` | `/opt/smc-v3`, `data_v3`, `/var/log/smc-v3` | nginx 80/443 лише з Cloudflare-діапазонів (ufw default deny), `ws_server` на 127.0.0.1:8000 |
| Арчі (trader-v3) | `smc_trader_v3` (OFF, маркер) | `ubuntu` | `/opt/smc-trader-v3/data` (пам'ять, директиви), `.env` | Telegram |
| family-agent | `family_agent` | `ubuntu` | `/opt/family-agent/data/**/family_userbot.session` (TG-сесія особистих чатів), `.env` (ANTHROPIC, TG_API_ID/HASH) | Telegram |
| tg-guard | `tg_guard` | `ubuntu` | `/opt/tg-guard` | Telegram |
| Traccar (GPS дитячого телефона) | java | **root** | `/opt/traccar/{conf,data}` (H2 DB, user `sa`) | `*:8082`, `*:5001-5267` на всіх інтерфейсах, зовні закриті ufw; назовні лише cloudflared tunnel `app.m7x2kids.com`→8082, `gps.m7x2kids.com`→5055 |
| kidlock-cp | Cloudflare Worker (не на VPS) | — | — | `cp.m7x2kids.com` |

Спільне: один Redis 127.0.0.1:6379 **без пароля** (db1 = платформа `v3_local:*`, db0 = legacy `v3`/`v3_prod`/`ai_one`);
`ubuntu` = єдиний адмін (`/etc/sudoers.d/90-cloud-init-users`: `ubuntu ALL=(ALL) NOPASSWD:ALL`), root-login off,
sshd key-only, fail2ban (sshd, nginx-botsearch, nginx-req-limit), nginx `limit_req` 20r/s + `limit_conn` 20 + real IP з
`CF-Connecting-IP`, `aione-watchdog` (health/attack alerts → Telegram).

### 1.1 Модель загроз (до 06.09)

Відвідувач публічного графіка → RCE або SSRF у `ws_server` (процес `ubuntu`) →
**`sudo -n` без пароля = root** → GPS-база дитини (`traccar.xml` 644, H2 під root), TG-сесія сімейного бота
(`/opt/family-agent/data` 775), пам'ять Арчі, усі `.env`, `sudo` = будь-що. Додатково у env `smc-ws` лежав
`ANTHROPIC_API_KEY` (для мертвого cowork) і `ARCHI_AUTH_TOKEN`; Redis без auth дозволяє минтити api_v3-токени
(`v3_local:tokens:*`, `token_store.py:39-42`) і крутити inbox бота.

### 1.2 Публічна поверхня (workflow «public-surface», file:line)

| Знахідка | Sev | Стан |
|---|---|---|
| WS без max_clients / per-IP / rate-limit дій / cooldown `switch`; `heartbeat=None`; `max_msg_size` 4 MiB (`ws_server.py:1691-1900`) — один таб `setInterval(switch,5ms)` кладе UDS executor для всіх | S1 | **ЗАКРИТО** `1ace5f9` (SEC-06, changelog 20260906-001) |
| nginx-зони `smc_limits.conf`/`realip_cloudflare.conf` лише на VPS (drift) | S2 | **ЗАКРИТО** `ee408b7` (`tools/nginx/conf.d/`) |
| `_archi_auth` приймає `?token=` на всіх роутах (`:2350-2354`), archi vhost без `limit_req`, `listen 80` без редиректу, self-signed | S2 | P5 |
| `/api/public/snapshot` без auth, sync Redis+file I/O на event loop, без negative-cache (`public_snapshot.py:200-223`) | S2 | ADR-0090 S1 (переїзд у bridge) + negative-cache там |
| `api_v3 _client_ip` бере ПЕРШИЙ hop XFF (`endpoints.py:1731`) — spoofable audit/rate keys | S2 | P5 |
| Платформа = writer у стан бота (`owner-note`, `proposals/review`, `chat`) | S2 | ADR-0090 S1/S6 |
| raw WS payload у логах без sanitize (`:1806, :1815`) | S3 | **ЗАКРИТО** `1ace5f9` |
| `str(e)`/шляхи у JSON-помилках (`:2906, :2923, :2955`, `/api/context :3252, :3258`) | S3 | P5 |
| CSP `unsafe-inline`; `ACAO:*` на автентифікованому SSE (`:2448, :2666`) | S3 | P5 |
| мертві helper-модулі `runtime/api/{rate_limit,csrf,audit,sanitizer}.py` (лише в тестах) | S3 | ADR-0090 S1 (wire у bridge) або видалити |

---

## 2. Альтернативи

### A. Status quo (один користувач, спільний Redis, NOPASSWD)

- **REJECT** — публічний сайт = root над дитячими даними.

### B. Приватні сервіси на окремий VPS (family-agent, tg-guard, Traccar)

- Pro: фізична межа; публічний хост не містить приватних даних взагалі.
- Con: другий хост і його обслуговування; переїзд Traccar (пристрої дитини вказують на `gps.m7x2kids.com`
  через тунель — DNS не змінюється, але даних/бекапів треба перенести).
- **TARGET (owner-рішення про хостинг)** — не блокує C, C робить переїзд безпечним у будь-який момент.

### C. Least privilege на тому самому хості (CHOSEN, негайно)

- Per-program unix-users без sudo; секрети per program; Redis auth + ACL; приватні каталоги 700/750;
  публічна поверхня лише nginx+Cloudflare; приватні hostnames лише через тунель (+ Cloudflare Access).
- Pro: закриває RCE→root і cross-tenant read сьогодні; усе reviewable у репо.
- Con: спільне ядро/мережа; не захищає від kernel-exploit.
- **ACCEPT** як P1–P7.

### D. Контейнери (docker/podman per tenant)

- Pro: файлова і мережева ізоляція з коробки.
- Con: FXCM SDK на Python 3.7 у `.venv37`, supervisor-контракти, watchdog, тунель — переписування ops без
  функціонального виграшу над C; для приватного застосунку B дає більше.
- **DEFER.**

---

## 3. Рішення (принципи)

1. **Один program = один користувач без sudo.** `ubuntu` лишається адмін-акаунтом людини (cloud-init), але
   жоден мережевий сервіс під ним не працює.
2. **Секрети per program.** Кожен процес бачить лише свій `.env` (640, група процесу); жодних секретів у
   `environment=` supervisor для публічних процесів; жодних чужих ключів (Anthropic у платформі — ні).
3. **Redis з паролем і ACL** (Redis 6.0.16 підтримує ACL): `smc` — `~v3_local:*` read/write без `FLUSH*`/`KEYS`;
   `archi` — свої ключі; `bridge` — read на `agent:*|thesis:*|wake:*`, write лише `wake:events`,
   `archi:web_inbox`; `default` вимкнено. Пароль — з `.env` кожного процесу.
4. **Приватні каталоги 700/750**, група лише для процесу-власника; `/opt/backups` 750 root.
5. **Публічна поверхня = nginx з Cloudflare-діапазонів**; усе інше на loopback або в тунелі; приватні
   hostnames (`archi|gorn|ochi`, `app|gps.m7x2kids`) — за Cloudflare Access (owner) на додачу до bearer.
6. **Reviewable ops**: supervisor/nginx/sudoers/ACL — копії в `tools/` (без секретів), drift-детектор
   (ADR-0060) порівнює.
7. **Жодних крос-орендарських шляхів у конфігах платформи** (`agent_console.data_dir` зникає з ADR-0090 S6).

---

## 4. Слайси

| Слайс | Що | Статус |
|---|---|---|
| **P1** платформа під `smc` | user `smc` (nologin, без sudo; legacy `/home/smc/smc_v1` → root-only бекап), HOME `/var/lib/smc`, `ubuntu` ∈ `smc`; `/opt/smc-v3` = ubuntu:smc g+rX + setgid; `data_v3`, `History`, `logs`, `/var/log/smc-v3` = smc:smc g+rwX; `.env` 640 ubuntu:smc, `.env.bak.*` 600; supervisor `user=smc`, `umask=002`, `HOME`, без `ANTHROPIC_API_KEY`/`COWORK_TRIGGERS_DIR`; logrotate `create smc smc`; `/opt/family-agent/data` 750; `/opt/traccar/{conf,data}` 750, `traccar.xml` 640 | **DONE 06.09 05:12 UTC** (bekап `/root/smc-v3.conf.bak.*`, rollback `/root/smc-user-rollback-*.sh`); шаблон `ee408b7` |
| **P1b** SEC-06 WS rails + nginx-зони в репо | див. §1.2 | **DONE** `1ace5f9`, `ee408b7` |
| **P2** Redis auth + ACL | `requirepass` + `aclfile /etc/redis/users.acl` (користувачі §3 п.3); `config.json` `redis.password_env`; клієнти платформи читають пароль з `.env` (`REDIS_PASSWORD`); бот — зі свого `.env` (companion trader-v3 нотатка, X31: лише env); db0 legacy-ключі → аудит і видалення | ⏳ (weekend; rollback = `requirepass ""`) |
| **P3** bridge/archi користувачі | з ADR-0090 S1: `smcbridge` user; `/opt/smc-trader-v3/data` → `archi:archi` 750 + ACL read для `smcbridge` до S6; `smc` втрачає read на дані бота (`sudo -u smc test -r … = fail`) | ⏳ (після ADR-0090 S1) |
| **P4** приватні сервіси | `family` і `tgguard` users (nologin), homes 700, `.env` 600; Traccar лишається root, але `web.address=127.0.0.1` (тунель і так локальний) + порти 5xxx лише на loopback, якщо пристрої йдуть через `gps.m7x2kids.com`; Cloudflare Access на `app.m7x2kids.com` | ⏳ (Traccar-зміни — з owner-go, це дитячий застосунок) |
| **P5** hardening публічного коду | `api_v3 _client_ip`: `CF-Connecting-IP` → `X-Real-IP` → останній hop XFF; archi vhost: `listen 80 → 301`, `limit_req` на `/api/archi/`, `?token=` лише на SSE-роутах; фіксовані коди помилок замість `str(e)`; `/api/context`: символ ∉ allowlist → 400; SSE без `ACAO:*`; CSP без `unsafe-inline` (hash/nonce) | ⏳ (2 патчі ≤150 LOC) |
| **P6** sudoers/deploy-акаунт | `ubuntu` лишається людським адміном; для агентських/CI деплоїв — окремий `deploy` user з sudoers-whitelist (`supervisorctl`, `systemctl reload nginx`, `nginx -t`) — **не** різати `ubuntu` (root-login off, пароля нема → лок-аут) | ⏳ (owner-рішення) |
| **P7** нагляд | `aione-watchdog`: алерт на новий listening-порт поза списком, на процес мережевого сервісу під `ubuntu`, на `sudo` з процесу-сервісу; `SECURITY.md` = реальна топологія | ⏳ |

---

## 5. Наслідки

- ✅ RCE у публічному процесі більше не дає root і не читає TG-сесію, GPS-базу, `.env` інших сервісів (доведено P1 §6).
- ✅ Усі ops-конфіги мають копію в репо; `local ≡ git ≡ deploy` поширюється на supervisor/nginx.
- ⚠️ Ops-інструменти (backfill, rebuild) під `ubuntu` пишуть у `data_v3` через групу `smc` + setgid + `umask 002`;
  файли, створені `smc`, group-writable (`umask=002` у supervisor) — інакше `--force` rebuild під ubuntu впаде на EACCES.
- ⚠️ До ADR-0090 S6 `smc`/`smcbridge` читає дані бота — це свідомий залишок, закривається виносом.
- ⚠️ Redis ACL (P2) = зміна для всіх клієнтів одночасно (платформа + бот); робити у вихідні з observation.

---

## 6. Verification (доказ, не «процес RUNNING»)

```bash
# P1 (виконано 06.09 — усі рядки «ok»)
ps -eo user,cmd | grep -E "[r]untime.ws.ws_server|[a]pp\.main|[b]roker_sidecar|[t]ick_" | awk '{print $1}' | sort -u   # → smc
sudo -u smc sudo -n true; echo $?                                    # → 1 (нема sudo)
for p in /opt/family-agent/.env /opt/family-agent/data /opt/smc-trader-v3/.env /opt/backups /opt/traccar/conf/traccar.xml; do sudo -u smc test -r "$p" && echo "LEAK $p"; done   # → нічого
sudo cat /proc/$(pgrep -f runtime.ws.ws_server | head -1)/environ | tr '\0' '\n' | grep -c ANTHROPIC   # → 0
# P2
redis-cli -n 1 PING                                                   # → NOAUTH
redis-cli --user smc --pass "$REDIS_PASSWORD" -n 1 FLUSHDB            # → NOPERM
# публічна поверхня (SEC-06, доведено 06.09 05:24 UTC)
# 10 сокетів з одного IP → 8 open + 2×503; switch flood → switch_throttled; 12 дій → 4×action_rate_limited
```

---

## 7. Open Questions (owner)

1. **Окремий VPS для сімейного/дитячого** (альтернатива B) — коли? До того P4 тримає межу на тому ж хості.
2. Cloudflare Access для `app.m7x2kids.com`, `archi|gorn|ochi.aione-smc.com` — увімкнути (безкоштовно до 50 користувачів)?
3. Redis: ACL на спільному інстансі (P2) чи другий інстанс для бота/bridge на іншому порту?
4. `ubuntu` sudo NOPASSWD:ALL лишається як людський адмін; чи потрібен окремий `deploy` акаунт для агентських SSH-сесій (P6)?

---

## 8. Статус слайсів

| Слайс | Статус | Дата/коміт |
|---|---|---|
| P1 платформа під smc + права приватних каталогів | ✅ DONE | 2026-09-06 05:12 UTC; `ee408b7` |
| P1b SEC-06 rails + nginx-зони | ✅ DONE | `1ace5f9`, `ee408b7` |
| P2 Redis auth + ACL | ⏳ | — |
| P3 bridge/archi users | ⏳ (після ADR-0090 S1) | — |
| P4 приватні сервіси | ⏳ (owner-go) | — |
| P5 hardening коду | ⏳ | — |
| P6 deploy-акаунт | ⏳ (owner) | — |
| P7 нагляд + SECURITY.md | ⏳ | — |

---

## Rollback

- P1: `/root/smc-user-rollback-20260906-051235.sh` (conf з бекапу, `chown -R ubuntu:ubuntu`, reread/update).
- P2: `requirepass ""` + `aclfile` off + restart redis; клієнти працюють без пароля (env порожній = без auth).
- P4/P5: `git revert` відповідних патчів; права каталогів — `chmod` назад за таблицею §1.

---

## Changelog

- 2026-09-06: Created (Accepted). P1/P1b виконано того ж дня і доведено живими перевірками (§6).
  RECON: VPS-інвентар (масковані секрети) + workflow «public-surface» проти коду.
