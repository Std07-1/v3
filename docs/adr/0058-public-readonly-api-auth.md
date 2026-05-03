# ADR-0058: Public Read-Only API + Token Auth для External Consumers

- **Status**: Proposed
- **Date**: 2026-05-03
- **Author**: Стас
- **Initiative**: `public_api_v1`
- **Related ADRs**: ADR-0039 (Signal Engine), ADR-0040 (TDA Cascade), ADR-0049 (Wake Engine IPC), ADR-0011 (SSOT Broadcast)
- **Cross-ref**: trader-v3/ADR-052 (Chat Modularization + Security Layer) — same threat-model vocabulary

---

## Quality Axes

- **Ambition target**: R2 — нова зовнішня поверхня з security review, доку-only proposal стадія; реалізація в наступному slice після Accepted
- **Maturity impact**: M3 → M4 — вводить перший формальний auth-layer на платформі (раніше всі API були same-origin тільки)

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

| Endpoint | Що повертає | Джерело |
|---|---|---|
| `GET /api/v3/signals/latest?limit=N` | Останні N TDA signals (всі символи) | `data_v3/_signals/journal-*.jsonl` tail |
| `GET /api/v3/signals/journal?date=YYYY-MM-DD&symbol=...` | Повний journal за дату | Файлова система |
| `GET /api/v3/bias/latest` | Поточний `bias_map` (всі TF) | SmcRunner snapshot |
| `GET /api/v3/narrative/latest?symbol=XAUUSD` | Поточний narrative блок | SmcRunner snapshot |
| `GET /api/v3/macro/context` | TDA `tda_state.json` snapshot | Файлова система |

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

### 3.4 Token lifecycle

- Створення: manual `redis-cli SETEX {ns}:tokens:tk_NEW {ttl_s} '{...}'`
- Rotation: видати новий токен, дати споживачу час перейти, видалити старий
- Revocation: `DEL {ns}:tokens:tk_REVOKED`
- TTL: default 90 днів, renewable
- Token format: `tk_` + 32 random bytes hex

### 3.5 Rate limiting

```nginx
limit_req_zone $http_x_api_key zone=api_v3:10m rate=60r/m;

location /api/v3/ {
    limit_req zone=api_v3 burst=10 nodelay;
    auth_request /_auth;
    ...
}
```

- 60 req/min per token, burst 10
- Hit → 429 + `Retry-After`

---

## 4. Інваріанти і Rails

| Rail | Як забезпечується |
|---|---|
| **I1 read-only** | FastAPI sidecar та `/api/v3/*` endpoints **не** мають POST/PUT/DELETE; UDS sovereignty preserved; nginx config забороняє все крім GET (`limit_except GET { deny all; }`) |
| **I5 degraded-but-loud** | Rate-limit hit → nginx access log + Prometheus counter `api_v3_rate_limit_hits_total`; auth fail → FastAPI structured log + counter; жодного silent drop |
| **F4 no silent fallback** | FastAPI Redis-down → 503 + log, **не** fallback на "allow all"; nginx `auth_request` fail-closed |
| **X1 Compliance — secrets** | Tokens живуть тільки в Redis (TTL); не логуються; access log редактує `X-API-Key` через `map` directive (`'tk_***'`) |
| **OWASP A01 (Broken Access)** | auth_request обов'язковий на всіх `/api/v3/*` locations |
| **OWASP A07 (ID/Auth Failures)** | Redis token store з TTL, без in-memory cache в nginx (avoid stale-after-revoke) |
| **OWASP A09 (Logging)** | Structured logs у FastAPI (consumer, endpoint, status); nginx redact для `X-API-Key`; Prometheus counters |

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
- `GET /api/v3/signals/latest`, `/journal`, `/bias/latest`, `/narrative/latest`, `/macro/context`
- Реалізація як **новий aiohttp router у ws_server** (same-origin first), потім nginx експортує `/api/v3/*` зовні
- Тести: payload schema parity з internal data

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

1. **Чи нам треба per-IP rate limit окрім per-token?** (defense-in-depth проти token leak) → recommend yes
2. **Чи писати access events в `data_v3/_audit/api_v3_access.jsonl` для self-monitoring?** → recommend yes, append-only JSONL
3. **CF page rule для cache control** — деякі endpoints (`/macro/context`) можуть кешуватись 60s, інші (`/signals/latest`) — no-cache. Уточнити при 058.3
4. **TLS mutual fingerprint** як додатковий рівень? → defer, тільки якщо OWASP review вимагатиме

---

## 9. Decision Log

- **2026-05-03**: Стас затвердив path B (HTTP proxy + auth) як відповідь на питання про канал доставки даних старому новинному боту під час hibernation Архі. Quote: «так, якщо потрібен шлях B (HTTP proxy), то так, - ADR, Auth та інше, все що потрібно».
- **Next**: Compliance/OWASP review → Accepted → Slice 058.1
