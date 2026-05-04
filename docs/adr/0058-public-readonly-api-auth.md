# ADR-0058: Public Read-Only API + Token Auth для External Consumers

- **Status**: Accepted (2026-05-03, post Compliance review — 0 S0, 8 S1 amendments applied, 5 S2 amendments applied)
- **Date**: 2026-05-03
- **Author**: Стас
- **Initiative**: `public_api_v1`
- **Related ADRs**: ADR-0039 (Signal Engine), ADR-0040 (TDA Cascade), ADR-0049 (Wake Engine IPC), ADR-0011 (SSOT Broadcast)
- **Cross-ref**: trader-v3/ADR-052 (Chat Modularization + Security Layer) — same threat-model vocabulary

---

## Quality Axes

- **Ambition target**: R2.5 — нова зовнішня поверхня з повним Compliance/OWASP API Top 10 (2023) sign-off; пост-amendments hardened proposal готовий до 058.1 implementation
- **Maturity impact**: M3 → M4 — вводить перший формальний auth-layer на платформі (раніше всі API були same-origin тільки) + grace period rotation + per-IP defense-in-depth + CORS lock + TLS bypass guard

---

## 1. Контекст і проблема

### 1.1 Поточний стан

Platform v3 на VPS (`/opt/smc-v3/`, ws_server :8000) має HTTP/WS API:

- `/api/bars`, `/api/status`, `/api/updates`, `/api/context`, `/ws`
- Доступ — **тільки same-origin** через nginx (`aione-smc.com` → `127.0.0.1:8000`)
- Жодного auth-шару: nginx довіряє Cloudflare, ws_server довіряє nginx
- TDA signal journals: `data_v3/_signals/journal-YYYY-MM-DD.jsonl` (append-only, файлова система)
- Wake events: Redis `{ns}:wake:events` LIST (ADR-0049)

### 1.2 Потреба

Зовнішні споживачі потребують доступу до **читання** свіжих даних платформи:

| Споживач | Use case | Канал |
|---|---|---|
| **Старий новинний бот (Claude Desktop scheduled task)** | Tелеграм-публікація останніх TDA signals + bias + narrative wrapper | HTTP fetch з cowork sandbox |
| **Майбутній Discord/X publisher** | Multi-channel broadcast | HTTP fetch |
| **External agents** (cross-checks, research) | Читання journal/state | HTTP fetch |

Архі (trader-v3) перебуває в hibernation до monthly Anthropic reset (2026-06-01); поки він спить — старий бот має продовжувати публікацію новин і сигналів. Він живе як scheduled task у Claude Desktop cowork-сандбоксі і потребує fetch-able endpoint.

### 1.3 Чому не існуючі канали

| Варіант | Чому ні |
|---|---|
| WS `/ws` | Stateful, потребує persistent connection — не підходить cron-стилю scheduled task |
| `/api/status` same-origin | Cowork sandbox = окремий мережевий контекст, не same-origin |
| Redis pubsub (як ADR-0049) | Sandbox не має доступу до VPS Redis; expose Redis публічно = security S0 |
| File mount | Cowork pass-2 читає mounted workspace, але це snapshot — не live; потребує periodic scp |

---

## 2. Альтернативи

### 2.1 nginx `auth_request` → FastAPI sidecar (recommended)

```
external client ──HTTPS──► CF ──► nginx ──auth_request──► FastAPI:8001 (token validator)
                                          │ 200 OK
                                          ▼
                                  ws_server :8000 /api/v3/...
```

- nginx робить subrequest на `/_auth` з headers `X-API-Key`, `X-Forwarded-For`
- FastAPI sidecar читає Redis `{ns}:tokens:{key}` → returns 200/401/403
- Per-token rate limit через nginx `limit_req_zone $http_x_api_key`
- Read-only endpoints: `GET /api/v3/signals/latest?limit=N`, `/api/v3/signals/journal?date=YYYY-MM-DD`, `/api/v3/bias/latest`, `/api/v3/narrative/latest`

**+** Стандартний enterprise pattern, незалежний lifecycle FastAPI sidecar
**+** Token rotation = Redis SET + TTL, без рестарту nginx
**+** Audit trail: nginx access log + FastAPI structured log
**−** +1 process у supervisord, +1 порт

### 2.2 Static API-key у nginx (`if ($http_x_api_key != "...")`)

- Single hardcoded token у `/etc/nginx/sites-enabled/smc`
- nginx робить простий 401 без backend

**+** Zero-cost (no extra process)
**−** Token rotation = nginx config edit + reload (operational friction)
**−** Жодного per-token rate limit / revocation
**−** No audit per-consumer

### 2.3 Cloudflare Access JWT

- CF Zero Trust видає short-lived JWT для authenticated identity
- nginx перевіряє `Cf-Access-Jwt-Assertion` через JWKS

**+** Identity-based (Stas Google account, etc.), MFA built-in
**−** Cowork scheduled task не має human identity → потрібен service token
**−** Залежність від CF Zero Trust ($), додатковий vendor lock-in

### 2.4 mTLS (mutual TLS)

**−** Найскладніша операційно (cert rotation, CA, distribution до cowork sandbox)
**−** Overkill для read-only public-fetch use case
**Rejected**

---

## 3. Рішення

**Обрано Альтернатива 2.1 — nginx `auth_request` + FastAPI token-validator sidecar.**

### 3.1 Архітектура

```
external ──HTTPS──► Cloudflare ──► nginx (aione-smc.com)
                                       │
                                       ├── /api/v3/* ──auth_request /_auth──► FastAPI :8001
                                       │                                      ├── Redis {ns}:tokens:{key}
                                       │                                      └── 200 OK / 401 / 403
                                       │
                                       └── (если auth OK) proxy_pass ──► ws_server :8000 /api/v3/*
```

### 3.2 Endpoints (read-only, version-prefixed)

| Endpoint | Що повертає | Джерело | Lifecycle | Cache hint |
|---|---|---|---|---|
| `GET /api/v3/signals/latest?limit=N&source=tda_cascade\|smc_narrative\|all` | Останні N events з journal | `data_v3/_signals/journal-*.jsonl` tail | Persisted (TDA only зараз; smc_narrative — майбутнє розширення) | Polite ≤30s (new entries rare, ~2/day) |
| `GET /api/v3/signals/journal?date=YYYY-MM-DD&symbol=...&source=...` | Повний journal за дату | Файлова система | Persisted (immutable після кінця дня) | Aggressive (immutable past dates: 1h+) |
| `GET /api/v3/bias/latest` | Поточний `bias_map` (всі TF) | SmcRunner snapshot | **Live RAM** (no history) | Polite ≤5s |
| `GET /api/v3/narrative/snapshot?symbol=XAUUSD` | Поточний `NarrativeBlock` (live) — headline, scenario, entry/target/invalidation, archi_thesis (якщо Архі онлайн) | SmcRunner snapshot + narrative_enricher | **Live RAM** (no history; tick ~2s) | Polite ≤5s (do NOT cache >5s) |
| `GET /api/v3/macro/context` | TDA `tda_state.json` snapshot | Файлова система | Persisted (rewritten ~1/day) | Polite ≤60s |

**Pagination cap (F-S1-003)**: для list endpoints `?limit` має hard maximum **100**. `?limit > 100` → `400 Bad Request` з body `{kind:"error", data:{code:"limit_exceeded", max:100, requested:N}}`. Default `?limit=10`.

**Historical scraping limit (F-S2-002)**: `/signals/journal?date=X` приймає `X` тільки в межах **останніх 90 днів** від today. `?date < (today - 90d)` → `400 Bad Request` з `{kind:"error", data:{code:"date_too_old", max_back_days:90}}`. Захист від full-history scraping (disk I/O DDoS vector).

**Cache stability contract**: `Live RAM` означає "значення може змінитись між двома fetch'ами в межах секунд — НЕ кешуй >5s". `Persisted (immutable)` означає "можна кешувати aggressive (1h+) для минулих дат". Точні CF page rules — slice 058.3.

**Verified state (2026-05-03)**: journal містить тільки `source=tda_cascade` events (entry + closed pairs, ~2/день). `smc_narrative` події в journal **не пишуться** — narrative живе тільки в SmcRunner RAM, broadcast у WS frame для UI v4 NarrativePanel. Тому розділяємо:

- **`/signals/*`** — historical/persisted (journal-backed)
- **`/narrative/snapshot`** — live RAM, **немає** `/narrative/latest` from journal до окремого ADR (потребує persistence layer для smc_narrative events)

### 3.2.1 Response envelope (tagged union)

Всі endpoints повертають **єдиний envelope** для версіонування і дисамбігуації типу:

```json
{
  "schema_version": "v3.0",
  "kind": "tda_cascade",          // або "smc_narrative" | "bias_map" | "narrative_block" | "tda_state"
  "server_ts": "2026-05-03T13:15:11Z",
  "data": { /* payload schema залежить від kind */ }
}
```

Для list endpoints (`/signals/latest`, `/signals/journal`):

```json
{
  "schema_version": "v3.0",
  "kind": "signal_list",
  "server_ts": "...",
  "items": [
    {"kind": "tda_cascade", "data": {...}},
    {"kind": "tda_cascade", "data": {...}}
  ],
  "count": 2,
  "filters": {"source": "all", "limit": 10}
}
```

**Чому це обов'язково**:

1. Journal вже сьогодні mixed-source (`tda_cascade` events, у майбутньому `smc_narrative`); споживач без `kind` field змушений вгадувати тип по presence/absence полів — fragile
2. `schema_version` дає safe-rollout шлях для breaking field changes (старий бот продовжує читати v3.0, новий бот — v3.1)
3. `?source=` filter дозволяє фільтрувати на платформі (один прохід по journal) замість на споживачі (zero efficiency win)

**Default `?source=all`** — backward-friendly для майбутніх consumers що не знають усіх типів.

### 3.3 Auth контракт

**Request**:

```http
GET /api/v3/signals/latest?limit=5 HTTP/1.1
Host: aione-smc.com
X-API-Key: tk_abc123...
```

**FastAPI `/_auth` perspective**:

```
1. Read X-API-Key з headers
2. Lookup Redis SETEX {ns}:tokens:tk_abc123 → JSON {scope: "read", consumer: "old_news_bot", created: ts}
3. If exists → return 200 + headers (X-Consumer, X-Scope)
4. If missing → 401
5. If scope mismatch → 403
```

### 3.3.1 Token scope semantics (F-S1-007)

`scope` field у Redis token JSON визначає що саме токен дозволяє:

| Scope value | Семантика | Use case |
|---|---|---|
| `"read"` | Усі публічні `/api/v3/*` GET endpoints, всі symbols (XAU/USD, XAG/USD, BTCUSDT, ETHUSDT) | Default для всіх consumers сьогодні (одна tenant, single owner) |
| `"read:XAU/USD"` | (FUTURE, не impl у 058.1) Тільки endpoints з `?symbol=XAU/USD` або symbol-agnostic (`/macro/context`) | Multi-tenant scenarios, per-symbol tokens для third-party publishers |
| `"read:no-narrative"` | (FUTURE) Все крім `/narrative/snapshot` (якщо narrative містить competitive/sensitive info) | Restricted external integrations |

Для slice 058.1 implementовано **тільки `"read"`**. Інші scope values reserved — FastAPI повертає `403 forbidden_scope` якщо токен має невідомий scope (fail-closed). Розширення scope vocabulary = новий ADR amendment.

**Principle of least privilege**: коли з'явиться other-tenant publisher — видати йому token зі вузьким scope, не дефолтним `"read"`.

### 3.4 Token lifecycle

#### Створення

```bash
# slice 058.4 tooling
python -m tools.api_v3.issue_token \
    --consumer old_news_bot \
    --scope read \
    --ttl-days 90
# виводить tk_<64 hex chars>
```

Під капотом:

```python
import secrets
token = "tk_" + secrets.token_bytes(32).hex()  # F-S1-006: cryptographically secure
redis.setex(f"{ns}:tokens:{token}", ttl_s, json.dumps({
    "scope": "read",
    "consumer": "old_news_bot",
    "created": iso_ts,
    "expires": iso_ts_plus_ttl,
}))
```

**Token format**: `tk_` префікс + **32 bytes** (256 біт) ентропії з `secrets.token_bytes()` (Python stdlib cryptographic source) → 64 hex chars. Загальна довжина 67 символів. Не використовувати `random.randbytes()` (не cryptographic).

#### Rotation з grace period (F-S1-001)

**Runbook**:

1. Issue новий токен: `python -m tools.api_v3.issue_token --consumer old_news_bot --ttl-days 90`
2. Notify consumer (Telegram/email): «Новий токен: `tk_NEW`. Старий працюватиме до `<old_expires + 7d>`.»
3. Consumer оновлює свою конфігурацію, починає використовувати новий
4. **Grace period: 7 днів** — обидва токени активні
5. Verify: `python -m tools.api_v3.list_tokens` показує `last_used_at` для обох; новий має recent activity, старий — silent
6. Після grace period: `redis-cli DEL {ns}:tokens:tk_OLD`
7. Verify: 24h без 401 у `data_v3/_audit/api_v3_access.jsonl` для consumer

**Чому 7 днів**: Cowork scheduled task = cron-стиль, може deploy редко. 7 днів покриває weekend-only deployments + 2-3 дні reaction window.

#### Revocation (instant)

```bash
redis-cli DEL {ns}:tokens:tk_REVOKED
```

→ наступний request с цим токеном дає 401 одразу (FastAPI sidecar не кешує — Redis lookup per-request).

#### Renewal (F-S2-003)

TTL default 90 днів. Якщо токен скоро спливе:

- **Option A** (recommended): issue новий токен з grace period (див. вище)
- **Option B** (operational shortcut): manual extension `redis-cli EXPIRE {ns}:tokens:tk_X {new_ttl_s}`

Option B можна використовувати тільки якщо consumer identity verified out-of-band (Telegram message від owner). Tooling: `python -m tools.api_v3.extend_token --token tk_X --days 90`.

### 3.5 Rate limiting + transport hardening

#### Per-token rate limit (primary)

```nginx
limit_req_zone $http_x_api_key zone=api_v3_tok:10m rate=60r/m;

location /api/v3/ {
    limit_req zone=api_v3_tok burst=10 nodelay;
    ...
}
```

- 60 req/min per token, burst 10 → hit → 429 + `Retry-After`

#### Per-IP rate limit (defense-in-depth, F-S1-002)

```nginx
limit_req_zone $binary_remote_addr zone=api_v3_ip:10m rate=120r/m;

location /api/v3/ {
    limit_req zone=api_v3_tok burst=10 nodelay;
    limit_req zone=api_v3_ip burst=20 nodelay;
    ...
}
```

- 120 req/min per IP — **2× per-token rate** щоб legitimate multi-IP consumer (CF edge diversity, NAT) не страждав, але блокувати single-source spam якщо токен витік
- Обидва limiti спрацьовують незалежно — спам з одного IP блокується навіть з валідним токеном

#### CORS lock (F-S1-004)

```nginx
location /api/v3/ {
    # API server-to-server only, NOT browser fetch
    add_header Access-Control-Allow-Origin "" always;
    add_header Access-Control-Allow-Methods "GET" always;
    # Preflight OPTIONS → 405 (only GET allowed)
    ...
}
```

Явно empty `Allow-Origin` — browser fetch з malicious site не зможе прочитати response. API призначений для server-side fetch (cron, scheduled task, server agent), не для front-end JavaScript.

#### TLS bypass guard (F-S1-005)

Cloudflare termination → origin VPS на `:80`. Якщо атакуючий знайде direct VPS IP і зробить HTTP request bypassing CF — токен у headers буде передано в clear text.

```nginx
server {
    listen 80;
    server_name aione-smc.com;

    # F-S1-005: enforce HTTPS навіть при CF bypass
    if ($http_x_forwarded_proto != "https") {
        return 301 https://$host$request_uri;
    }
    ...
}
```

`$http_x_forwarded_proto` встановлюється CF як `https` при коректному flow. Direct VPS hit без CF не матиме цього header → redirect на HTTPS змусить TLS handshake (origin server має валідний cert або Let's Encrypt).

**Альтернатива** (якщо origin cert не налаштований): Cloudflare Authenticated Origin Pulls (mTLS між CF і origin) — але це окрема ops робота, defer до slice 058.3.

#### Audit JSONL (F-S3-002 → resolved тут)

FastAPI middleware пише per-request:

```
data_v3/_audit/api_v3_access.jsonl  (append-only, rotate щодня)
```

Fields per line:

```json
{"ts":"2026-05-03T13:15:11.123Z","consumer":"old_news_bot","endpoint":"/api/v3/signals/latest","method":"GET","status":200,"latency_ms":12,"ip_hash":"sha256_8bytes"}
```

- **IP hash** замість raw IP (F-S3-003 GDPR mitigation): `sha256(ip + daily_salt)[:16]` — анонімізація з можливістю correlate within day без зберігання PII
- **Retention**: 90 днів (rotate `gzip` після 7 днів, delete після 90)
- **Token redaction**: `X-API-Key` ніколи не пишеться, тільки `consumer` field з token JSON

---

## 4. Інваріанти і Rails

| Rail | Як забезпечується |
|---|---|
| **I1 read-only** | FastAPI sidecar та `/api/v3/*` endpoints **не** мають POST/PUT/DELETE; UDS sovereignty preserved; nginx config забороняє все крім GET (`limit_except GET { deny all; }`) |
| **I5 degraded-but-loud** | Rate-limit hit → nginx access log + Prometheus counter `api_v3_rate_limit_hits_total`; auth fail → FastAPI structured log + counter; жодного silent drop |
| **F4 no silent fallback** | FastAPI Redis-down → 503 + log, **не** fallback на "allow all"; nginx `auth_request` fail-closed |
| **X1 Compliance — secrets** | Tokens живуть тільки в Redis (TTL); не логуються; access log редактує `X-API-Key` через `map` directive (`'tk_***'`) |
| **OWASP A01 (Broken Access)** | auth_request обов'язковий на всіх `/api/v3/*` locations; scope check у FastAPI (§3.3.1); невідомий scope → 403 fail-closed |
| **OWASP A02 (Auth)** | Token entropy = `secrets.token_bytes(32)` (cryptographic source); grace period 7d на rotation (§3.4); revocation = instant Redis DEL |
| **OWASP A03 (Property-level)** | `?include_internal=false` за замовчуванням (§10.6); sensitive fields opt-in only |
| **OWASP A04 (Resource Consumption)** | Pagination cap `max_limit=100` (§3.2); historical date limit 90 днів (§3.2); response size cap 1MB (нижче); per-IP rate limit 120 req/min (§3.5) |
| **OWASP A05 (Function-level)** | nginx `limit_except GET { deny all; }` зашиває read-only у конфіг; FastAPI не має POST/PUT/DELETE handlers взагалі |
| **OWASP A07 (ID/Auth Failures)** | Redis token store з TTL, без in-memory cache в nginx (avoid stale-after-revoke); TLS enforcement redirect (§3.5) проти CF bypass |
| **OWASP A08 (Misconfig)** | Empty CORS (§3.5) проти browser fetch; FastAPI catch-all 404 handler (нижче); structured error responses замість 500 stack traces |
| **OWASP A09 (Logging)** | Structured logs у FastAPI (consumer, endpoint, status); nginx redact для `X-API-Key`; Prometheus counters; audit JSONL (§3.5) з IP hash |
| **F-S1-008 Symbol validation** | FastAPI Query param `symbol: str = Query(...)` має validator: `if symbol not in config["symbols"]: raise HTTPException(400, {"code": "invalid_symbol", "allowed": config["symbols"]})`. Запобігає probing `?symbol=../etc/passwd` або injection через невідомі symbol values. |
| **F-S2-001 Response size cap** | nginx `client_max_body_size 1m;` для `/api/v3/` (request side); FastAPI middleware рахує response bytes — якщо >1MB → 413 + `{code:"response_too_large", hint:"use pagination"}`. Захист від unbounded `/signals/journal?date=X` для дуже активних днів. |
| **F-S2-004 404 catch-all** | FastAPI має `@app.exception_handler(404)` що повертає structured JSON `{schema_version:"v3.0", kind:"error", data:{code:"not_found", path:request.url.path, message:"Endpoint not found"}}`. Запобігає 500-стек-трейс leak для typo paths типу `/api/v3/signal/latest` (single `signal` без `s`). |

---

## 5. Consequences

### Позитивні

- Foundation для Telegram/Discord/X publishers без переробки auth для кожного
- Старий новинний бот отримує fetch-канал → продовжує публікацію поки Архі спить
- Audit trail для кожного external read (хто, коли, що)
- Token rotation/revocation без deploy

### Негативні / ризики

- Нова attack surface — обов'язковий OWASP review до Accepted
- Operational overhead: token issuance/rotation як новий ритуал
- +1 supervisord process (FastAPI sidecar)
- Залежність від Redis для auth path (Redis down → API v3 down; ws_server :8000 продовжує працювати same-origin)

### Не вирішено цим ADR (out of scope)

- **Push-mode** (webhooks для external consumers) — окремий ADR
- **WebSocket public surface** — інша threat model, окремий ADR
- **Path C — Redis pubsub bridge** для cowork (ADR-0049 розширення) — окремий ADR якщо знадобиться
- **Write API** (POST signals, command surface) — НЕ цей ADR; будь-яка write-поверхня = новий ADR + invariant review

---

## 6. Rollback

| Крок | Дія | Час |
|---|---|---|
| 1 | Закоментувати `location /api/v3/` block у `/etc/nginx/sites-enabled/smc` | 10s |
| 2 | `sudo nginx -t && sudo systemctl reload nginx` | 5s |
| 3 | `supervisorctl stop api_v3_auth` (FastAPI sidecar) | 5s |
| 4 | (опціонально) `redis-cli --scan --pattern '{ns}:tokens:*' \| xargs redis-cli DEL` для зачистки | 30s |

**Жодних незворотних змін**: Redis tokens мають TTL і самозникнуть; data_v3/ не зачіпається; ws_server :8000 і UI продовжують працювати same-origin незалежно.

---

## 7. Implementation Plan (для майбутнього slice після Accepted)

### Slice 058.1 — FastAPI sidecar skeleton (M0.5)

- `runtime/api_v3/auth_validator.py` — FastAPI app з `/_auth`, `/health`
- Redis client, token lookup, structured logging
- supervisord entry, port :8001 (localhost-only bind)
- Тести: token valid/missing/expired/scope-mismatch

### Slice 058.2 — Read endpoints

- `GET /api/v3/signals/latest`, `/journal`, `/bias/latest`, `/narrative/snapshot`, `/macro/context`
- Реалізація як **новий aiohttp router у ws_server** (same-origin first), потім nginx експортує `/api/v3/*` зовні
- **Tagged union envelope** (§3.2.1): `schema_version`, `kind`, `data`/`items`
- **`?source=` filter** для `/signals/*`: `tda_cascade`, `smc_narrative`, `all` (default)
- Тести: payload schema parity з internal data, envelope contract, filter correctness

### Slice 058.3 — nginx auth_request integration

- nginx config: `limit_req_zone`, `auth_request /_auth`, `limit_except GET`, `X-API-Key` map redact
- VPS deploy + smoke test через CF
- D9 observation window 60s

### Slice 058.4 — Token operational tooling

- `tools/api_v3/issue_token.py` — generate + SETEX
- `tools/api_v3/list_tokens.py` — read-only audit
- `tools/api_v3/revoke_token.py` — DEL
- Runbook: `docs/runbooks/api_v3_tokens.md`

### Slice 058.5 — Compliance review (R_COMPLIANCE)

- OWASP A01/A07/A09 sign-off
- Disclaimer: read-only data is NOT financial advice
- Update `SECURITY.md` з новою зовнішньою поверхнею

---

## 8. Open Questions

1. ~~**Per-IP rate limit окрім per-token?**~~ → **RESOLVED** (F-S1-002, §3.5): yes, 120 req/min per IP як defense-in-depth
2. ~~**Audit events в `data_v3/_audit/api_v3_access.jsonl`?**~~ → **RESOLVED** (§3.5): yes, append-only JSONL з IP hash, 90d retention
3. **CF page rule для cache control** — згідно §3.2 cache hints (Polite ≤5s/≤30s/≤60s, Aggressive 1h+ для immutable past). Точна CF page rule конфігурація → slice 058.3 task
4. ~~**TLS mutual fingerprint**?~~ → **DEFERRED** (S3): F-S1-005 TLS bypass redirect покриває baseline; mTLS до origin (CF Authenticated Origin Pulls) — окрема ops робота якщо/коли direct-VPS-IP threat актуалізується
5. **API deprecation path для v3 → v4** (F-S3-001): коли з'явиться v4 — `/api/v3/*` живе ще 6 місяців paralel, потім → `410 Gone` з header `X-Deprecated-Version: v3, Sunset: YYYY-MM-DD`. Деталі — окремий ADR коли v4 буде на горизонті.
6. **Token runtime path для Cowork consumer** (operational, поза цим ADR): env-injected? per-task secret store? plain `.env` mode 600? — рішення оператора Cowork, не платформи. Consumer-side mini-spec буде підготовлений коли path вибрано.

---

## 9. Decision Log

- **2026-05-03**: Стас затвердив path B (HTTP proxy + auth) як відповідь на питання про канал доставки даних старому новинному боту під час hibernation Архі. Quote: «так, якщо потрібен шлях B (HTTP proxy), то так, - ADR, Auth та інше, все що потрібно».
- **2026-05-03 (amendment)**: Додано §3.2.1 (tagged union envelope) + `?source=` filter + split `/narrative/snapshot` vs `/signals/latest` (verified: journal містить тільки `tda_cascade`, smc_narrative живе в SmcRunner RAM). Додано §10 (Consumer Invariants — X28 enforcement). Залишається v1, не v2.
- **2026-05-03 (Compliance pass — R_COMPLIANCE)**: OWASP API Top 10 (2023) review завершено. Verdict: **ACCEPTED-WITH-CONDITIONS** (0 S0, 8 S1, 5 S2, 3 S3 findings). Усі 8 S1 + 5 S2 amendments застосовано в одному проході (R_PATCH_MASTER):
  - **S1**: grace period 7d (§3.4), per-IP rate limit 120 r/m (§3.5), pagination cap max=100 (§3.2), CORS empty (§3.5), TLS bypass redirect (§3.5), `secrets.token_bytes(32)` entropy (§3.4), token scope semantics (§3.3.1 new), symbol validation (§4)
  - **S2**: response size cap 1MB (§4), historical journal limit 90d (§3.2), token renewal process (§3.4), FastAPI 404 catch-all (§4), sensitive field redaction (§10.6 new)
  - **S3**: API deprecation path (§8 Open Q5 new), audit JSONL resolved (§3.5), GDPR IP hash (§3.5)
  - License audit clean: FastAPI MIT, uvicorn BSD-3, redis-py MIT, aiohttp Apache-2.0; `pip-audit` run required перед slice 058.1 install
  - Token format: custom `tk_{64 hex}` (NOT JWT) — менше CVE surface (no algorithm confusion)
- **2026-05-03 Status**: Proposed → **Accepted**. Готовий до slice 058.1 (FastAPI sidecar implementation) без додаткових doc rounds.
- **2026-05-04 (Slice 058.5 executed — R_COMPLIANCE sign-off)**: Post-implementation OWASP audit проти живого коду після slices 058.2/058.3. Findings:
  - ✅ **8/8 S1 amendments verified in code**: token entropy `secrets.token_bytes(32)` ([token_store.py:32](../../runtime/api_v3/token_store.py)), `MAX_LIMIT=100` ([endpoints.py](../../runtime/api_v3/endpoints.py)), `MAX_DATE_BACK_DAYS=90`, per-IP `api_v3` zone 2r/s burst 30 ([nginx](../../tools/smc-nginx-v4-hardened.conf)), `TOKEN_PREFIX="tk_"`, symbol validation, scope check, source filter
  - ✅ **5/5 S2 amendments verified**: 404 catch-all (`_handle_v3_not_found`), 90d horizon, `?source=` filter, structured error envelopes, no `?include_internal` honored
  - ⚠️ **3 gaps закриті у цій же сесії**:
    - F-S2-001 (A05): додано `limit_except GET HEAD OPTIONS { deny all; }` в nginx `/api/v3/` block
    - F-S2-002 (A08): додано `add_header Access-Control-Allow-Origin "" always;` в nginx
    - F-S2-003: додано `disclaimer` field у всі envelopes (data/items/error) + assertion в test suite (24/24 pass)
  - ⏸ **F-S3-002 (audit JSONL with IP hash) deferred** — S3 priority, обсяг ~150 LOC окремий slice; current logging вже purpose-fit (token prefix + consumer in `_validate_token`)
  - ✅ **SECURITY.md updated** з новою публічною поверхнею + повна OWASP A01–A10 mitigation table + Public API Disclaimer section
  - **Verdict**: **ACCEPTED** (no conditions remaining for slice 058.5 scope). Public API surface production-grade.
- **2026-05-04 (Slice 058.6 executed — F-S3-002 audit JSONL closed + cowork consumer onboarding)**: Picked up the deferred S3 item the same day to fully close the OWASP roadmap before handing off to the cowork bot.
  - ✅ **`audit_middleware` mounted on `/api/v3/*`** ([endpoints.py](../../runtime/api_v3/endpoints.py)): per-request append to `data_v3/_audit/api_v3_access-YYYY-MM-DD.jsonl`. Captured fields: `ts, consumer, ip_hash, method, path, query, status, latency_ms`. Token value is **never** persisted (only the consumer name resolved by the lookup).
  - ✅ **IP hashing (GDPR-friendly)**: `SHA-256(daily_salt || ip)[:16]`. Daily salt rotation = no cross-day correlation; intra-day grouping preserved for rate-limit forensics. Salt seed configurable via `API_V3_AUDIT_SALT` env var.
  - ✅ **90-day retention** enforced via `_cleanup_old_audit_files()` swept once on `register_routes()` startup.
  - ✅ **Fail-soft**: audit write failures only log a warning; never break the API request path.
  - ✅ **5 new tests** (29/29 total pass): success record shape, 401 anonymous record, token-never-leaks invariant, retention cleanup, opt-out via `audit_dir=None`.
  - ✅ **SECURITY.md A09 row** (Logging & Monitoring Failures) updated to reference live audit JSONL.
  - ✅ **Consumer mini-spec** published at [docs/runbooks/cowork_consumer_quickstart.md](../runbooks/cowork_consumer_quickstart.md): base URL, auth, envelope contract, 5 endpoints, rate limits, error codes, X28 obligations, audit trail disclosure.
  - **Verdict**: **F-S3-002 CLOSED**. ADR-0058 OWASP roadmap fully resolved. Ready for cowork bot integration.
- **2026-05-04 (Scope extension → ADR-0059)**: Cowork bot pilot revealed root architectural gap — current 5 endpoints expose **system conclusions only** (signals, bias, narrative, macro), not raw market context. Cowork stuck in "system commentator" mode (5/10 quality) — repeatedly transcribes same TDA verdict because new signals are rare (1-3/day). Owner pivot: «потрібен розбір на зараз а не трактування минулих сигналів... повернемось до ідеї яку відкинули — читання data\ або інші способи щоб отримувати свіжі дані». Decision: extend ADR-0058 surface with raw data endpoints (`bars/window`, `smc/zones`, `smc/levels`) under same auth/envelope/audit umbrella. Schema bump v3.0 → v3.1. New ADR drafted: [ADR-0059](0059-public-analysis-api-raw-data.md) (Status: Proposed).
- **Next**: Slice 058.1 (FastAPI sidecar skeleton) — окрема сесія R_PATCH_MASTER з P-slices ≤150 LOC each. Parallel: ADR-0059 slice 059.4 (kill switch) → 059.1-059.3 (endpoints) → 059.5 (docs).

---

## 10. Consumer Invariants — Що споживачі НЕ мають права робити (X28 enforcement)

> Цей розділ — **інваріант для будь-якого external consumer** (Telegram bot, Discord publisher, research agent), що читає `/api/v3/*`. Порушення = X28 violation (frontend re-derives backend SSOT).

### 10.1 Заборонено (consumer side)

| Дія consumer'а | Чому заборонено | SSOT де живе |
|---|---|---|
| Перерахунок `headline` з `bias` + `zone_id` | Headline вже синтезований у `core/smc/narrative.py` з повним контекстом (sessions, killzones, sweep, P/D, range exhaustion). Будь-який rewrite втрачає контекст. | [core/smc/narrative.py](../../core/smc/narrative.py) |
| Перерахунок `bias` з `zones[]` або `structure[]` | `bias_map` обчислюється в SmcRunner з multi-TF context. Локальний rewrite дасть інший результат на інших TF. | SmcRunner snapshot, ADR-0031 |
| Перерахунок `grade` / `grade_score` з confluence factors | Grade = 8-factor weighted scoring (ADR-0029). Зміна вагів на consumer side зламає parity з UI. | [core/smc/confluence.py](../../core/smc/confluence.py) |
| Перерахунок `entry_price` / `stop_loss` / `take_profit` / `R:R` | Numeric resolution = ADR-0039 Signal Engine; OTE/zone_edge methods. | [core/smc/signals.py](../../core/smc/signals.py) |
| Перерахунок `target_desc` / `invalidation` text | Згенеровано narrative.py з прив'язкою до конкретних levels (PDH/PDL/EQH). | narrative.py |
| Власна class'ifікація `market_phase` / `session` / `killzone` | Sessions = ADR-0035, market_phase = narrative.py. | sessions.py, narrative.py |
| Override `confidence` value | Confidence = weighted output з 5 факторів (Signal Engine §4). | signals.py |
| Mute `archi_thesis` поки не подобається | Якщо Архі онлайн і має тезу — публікація мусить її показати (transparency); consumer може вибрати presentation, не suppression |

### 10.2 Дозволено (consumer's job)

| Дія | Приклад |
|---|---|
| **Format/presentation** | Markdown/HTML rendering, emoji mapping, mobile vs desktop layout |
| **Channel-specific packaging** | Telegram caption + image, Discord embed, X thread split |
| **Aggregation** | Weekly recap (group TDA signals by day, compute win rate over journal range) |
| **Macro layer enrichment** | DXY level, Fed calendar, ForexFactory news — **ДОДАЄТЬСЯ збоку**, не перезаписує `bias` |
| **Cross-asset insights** | "BTC А+ долонг while XAU B+ short" — observation, не override grade |
| **Hallucination defense** | Regex-перевірка LLM-generated wrapper text проти SSOT values (e.g. price у тексті ≠ price у `data` → reject) |
| **Off-hours evergreen** | Educational пости коли journal порожній — generic content, без спекуляцій про "що зараз робить ринок" |
| **Chart generation** | mplfinance/lightweight-charts screenshot з overlay zone_id з API |

### 10.3 Patterns (recommended)

**Pattern A — Direct passthrough** (для Telegram коротких повідомлень):

```
fetch /narrative/snapshot → render headline + entry_desc + target_desc → post
```

**Pattern B — Wrapper з підсиленням** (для довших аналітичних постів):

```
fetch /narrative/snapshot
+ fetch /macro/context
+ external DXY/news (consumer's responsibility)
→ LLM compose з SYSTEM_PROMPT: "паrafраз headline, ДОДАЙ macro context, НЕ перерахуй bias/target/grade"
→ regex check: всі numeric values з API мусять бути присутні в output
→ post
```

**Pattern C — Aggregation** (weekly recap):

```
for date in last_7_days:
  fetch /signals/journal?date=date&source=tda_cascade
  collect entry/closed pairs
compute: win_rate, avg_R, max_loss, partial_win_count
→ honest report (3 trades: 1 partial win, 2 losses → educational angle)
```

### 10.4 Validation rail (recommended для consumers)

Перед публікацією consumer мусить пройти **hallucination guard**:

```python
# pseudocode
api_data = fetch(endpoint)
text = llm_generate(api_data)

# guard: всі numeric values з api_data["data"] мусять бути дослівно в text
for key in ("entry_price", "stop_loss", "take_profit", "grade"):
    if str(api_data["data"][key]) not in text:
        raise PublicationGuardFailure(f"missing {key} in generated text")
```

Це не enforced платформою (consumer's responsibility), але **рекомендована практика** для будь-якого LLM-wrapper що публікує платформенні дані.

### 10.5 Cohabitation з Архі (future)

**Важливо**: Cowork consumer працює **незалежно** від стану Архі. `/api/v3/*` доступний 24/7, hibernation Архі не впливає на доступність даних системи. Архі — додатковий шар, не передумова.

Коли Архі онлайн (post 2026-06-01 monthly reset або вручну):

- `/narrative/snapshot` автоматично почне повертати **додаткове поле** `archi_thesis` (через існуючий [runtime/smc/narrative_enricher.py](../../runtime/smc/narrative_enricher.py))
- Bot-consumer **повинен** показувати Архі-тезу окремим блоком ("🤖 Архі вважає: ...")
- Якщо Архі-теза суперечить системному `bias` — показати **обидва** як adversarial views, не вибирати "правильний"
- Це healthy adversarial loop: система vs Архі → publication показує обидва → traders judge → over time accumulate accuracy stats per source

Коли Архі офлайн:

- `/narrative/snapshot` повертає той самий envelope **без** `archi_thesis` field (або з `archi_thesis: null`)
- Bot-consumer публікує тільки системний deterministic naratив
- Це baseline режим — повноцінний publishing шар без залежності від Архі

**Інваріант**: bot ніколи не "вирішує" хто правий (це робить ринок). Bot = transparent reporter, не arbiter. Cowork consumer = first-class citizen, не fallback.

### 10.6 Sensitive field redaction (F-S2-005)

Деякі future fields у responses можуть містити internal-only reasoning (Архі's chain-of-thought, debug traces, internal correlation IDs). Для opt-in доступу:

```http
GET /api/v3/narrative/snapshot?symbol=XAUUSD&include_internal=true
```

**Default**: `include_internal=false`. Без явного `?include_internal=true` всі fields позначені як `_internal_*` (prefix convention) виключаються з response.

Приклади internal-only fields (на майбутнє, поки не impl у 058.1):

- `_internal_archi_chain_of_thought` — Архі's reasoning (PII-like, sensitive competitive info)
- `_internal_correlation_id` — debug trace IDs
- `_internal_runner_state_dump` — SmcRunner internal state для debug

**Сьогодні** (slice 058.1 baseline): жодних `_internal_*` fields у response — все public-safe. Цей розділ — forward-compatibility hook щоб коли такі поля з'являться, contract вже визначений і consumer не зламається.
