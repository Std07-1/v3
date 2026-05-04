# Cowork Consumer Quickstart — Public `/api/v3/*` Read-Only API

> **Status**: Active v2 (post slice 059.5, 2026-05-04)
> **Audience**: cowork bot / external publication agents that need SMC platform output
> **Companion ADRs**: [ADR-0058](../adr/0058-public-readonly-api-auth.md) · [ADR-0059](../adr/0059-public-analysis-api-raw-data.md) · [SECURITY.md](../../SECURITY.md)

**v2 changelog (2026-05-04)**: adds 3 raw-data analysis endpoints (`/bars/window`, `/smc/zones`, `/smc/levels`) under schema `v3.1`. Legacy `signals/*`, `bias/*`, `narrative/*`, `macro/*` залишаються `v3.0` без змін. Cowork повинен трактувати `schema_version` як **per-endpoint** значення, не глобальне.

---

## 1. Base URL & auth

```
Base:   https://aione-smc.com/api/v3/
Header: X-API-Key: tk_<64 hex>            # required on every request
```

Token is provisioned by ops via `tools/api_v3/issue_token.py` and delivered out-of-band
(NEVER commit it). Default TTL = 90 days; ops will rotate before expiry with a 7-day
overlap window where both old and new tokens accept traffic.

Missing or wrong key → `401` envelope:

```json
{ "kind": "error", "schema_version": "v3.0", "server_ts": "...",
  "disclaimer": "...",
  "data": { "code": "missing_api_key" | "invalid_api_key", "message": "..." } }
```

---

## 2. Envelope contract (all endpoints)

```jsonc
{
  "schema_version": "v3.0" | "v3.1",  // per-endpoint; v3.1 = analysis endpoints (bars/smc)
  "kind": "<endpoint name>",          // e.g. "signals_latest", "smc_zones"
  "server_ts": "2026-05-04T18:42:11Z",
  "disclaimer": "Educational/research data only. Not financial advice. ...",
  "data":  { ... }                    // OR
  "items": [ ... ],                   // (list endpoints only — v3.0 only)
  "total": 42                         // (list endpoints only)
}
```

**Schema mapping** (per-endpoint, not global):

| Endpoint family | `schema_version` | Notes |
|---|---|---|
| `signals/*`, `bias/*`, `narrative/*`, `macro/*` | `v3.0` | ADR-0058 contracts; unchanged |
| `bars/window`, `smc/zones`, `smc/levels` | `v3.1` | ADR-0059 analysis endpoints |

Forward-compat rule: a v3.0-pinned reader **MUST** parse v3.1 envelopes by ignoring unknown `data.*` fields (`snapshot_id`, `next_cursor`, `warnings`, `incremental`, `since_ms`, `meta.*`). It **MUST NOT** crash on their presence.

**Hard rule**: consumer **MUST surface the `disclaimer` text** in every published output
(channel post, digest, alert, etc.). See ADR-0058 §10 / X28 invariant.

---

## 3. The five endpoints

| Endpoint | Method | Purpose | Key params |
|---|---|---|---|
| `/api/v3/signals/latest` | GET | Most recent active signals | `limit≤100` (def 10), `source=tda_cascade\|smc_narrative\|all` |
| `/api/v3/signals/journal` | GET | Per-day signal journal | `date=YYYY-MM-DD` (≤90d back), `symbol=`, `source=` |
| `/api/v3/bias/latest` | GET | Multi-TF directional bias | `symbol=XAU/USD` (req) |
| `/api/v3/narrative/snapshot` | GET | Active scenario / context | `symbol=` (req), `tf=900` (def, M15 = 900) |
| `/api/v3/macro/context` | GET | Session, killzone, market phase | (none) |

Full param/response shapes: see [docs/contracts.md](../contracts.md) and the
schemas embedded in [`runtime/api_v3/endpoints.py`](../../runtime/api_v3/endpoints.py).

### 3.1 Quick `curl` smoke test

```bash
TOKEN="tk_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
curl -s -H "X-API-Key: $TOKEN" \
  "https://aione-smc.com/api/v3/signals/latest?limit=5&source=tda_cascade" \
  | jq '.kind, .total, .items[0]'
```

---

## 4. Rate limits & quotas

| Limit | Value | Action when exceeded |
|---|---|---|
| Requests / IP / sec | **2 r/s** (burst 30) | nginx returns `503` immediately |
| Concurrent connections / IP | **20** | nginx `503` |
| `?limit` hard cap | **100** | API returns `400 limit_too_large` envelope |
| `?date` lookback | **90 days** | API returns `400 date_too_old` envelope |

**Recommended client behaviour**:

- Cache idempotent GETs for 30 s (`Cache-Control: no-store` on responses is set for
  privacy, not freshness — server-side data is updated on the order of seconds anyway).
- Retry on `429`/`503` with exponential backoff (start 2 s, cap 30 s, jitter ±20%).
- Fail-loud (alert ops) on `schema_version` mismatch. Do NOT silently coerce.
- Alert ops on **any** `5xx` other than `503 limit_exceeded`.

---

## 5. Error codes (envelope `data.code`)

| Code | HTTP | Meaning |
|---|---|---|
| `missing_api_key` | 401 | No `X-API-Key` header |
| `invalid_api_key` | 401 | Token unknown or expired |
| `auth_backend_unavailable` | 503 | Token store (Redis) down — retry |
| `auth_unconfigured` | 503 | Server misconfigured — alert ops |
| `bad_param` | 400 | Param shape wrong (e.g. malformed date) |
| `limit_too_large` | 400 | `?limit > 100` |
| `date_too_old` | 400 | `?date` further back than 90 days |
| `source_invalid` | 400 | `?source` not in `{tda_cascade, smc_narrative, all}` |
| `not_found` | 404 | Unknown `/api/v3/*` path |
| `data_unavailable` | 503 | Upstream SMC runner not warmed up — retry |

---

## 6. Consumer obligations (X28 — anti-redrive)

The platform is **the SSOT** for all SMC-derived values. Consumer **MUST NOT** re-derive
any of:

- `headline`, `bias`, `grade`, `confidence`
- `entry`, `sl`, `tp`, `target_desc`, `r_r`
- `market_phase`, `session`, `killzone`
- Zone classifications (OB / FVG / liquidity / IFVG / Breaker)

Consumer **MAY**:

- Format / translate / shorten for human readability
- Aggregate multiple signals into a single post (preserving each signal's grade & disclaimer)
- Add its own metadata (publishing channel, post ID, etc.)
- Filter by grade / source / symbol

Violation = X28 in the platform constitution. Spotted = consumer access revoked.

---

## 7. Operational contacts & changes

- **Token rotation**: ops emails consumer 7 days before expiry with new token + cutover date
- **Schema bump** (v3.0 → v4.0): minimum 30-day notice, parallel-serve both versions for 14 days
- **Endpoint deprecation**: `Sunset` HTTP header set 60 days in advance; consumer alerts on header presence
- **Outage / degraded data**: `degraded[]` field appears in response; consumer should pause publication when present

For incidents or token issues: ping ops in the dedicated ops channel.

---

## 8. Audit trail

Every request is logged to `data_v3/_audit/api_v3_access-YYYY-MM-DD.jsonl` on the
server with: timestamp, consumer name (resolved from token), hashed IP (per-day salt,
GDPR-friendly), method, path, query, status, latency. Retention: 90 days. Token
values are NEVER persisted. See ADR-0058 §3.5 / F-S3-002.

---

# Part B — Analysis endpoints (schema v3.1, ADR-0059)

## 9. Three new analysis endpoints

| Endpoint | Method | Purpose | Key params |
|---|---|---|---|
| `/api/v3/bars/window` | GET | OHLCV window per TF (multi-TF, incremental) | `symbol` (req), `tfs=M15,H1,H4` (def), `count≤200`, `since_ms` |
| `/api/v3/smc/zones` | GET | Active SMC zones (OB/FVG/Liquidity) per TF | `symbol`, `tf` (req), `kind`, `status`, `limit≤200`, `cursor` \| `offset` |
| `/api/v3/smc/levels` | GET | Compact key levels: prev day/week + sessions | `symbol` (req) |

All three require the same `X-API-Key` header as v3.0 endpoints. All three are gated behind the analysis kill switch (§13).

### 9.1 `current_price` cross-endpoint contract (ADR-0059 §3.1)

Усі три endpoints повертають поле `data.current_price`. Воно **завжди** = `close` останнього complete M15 bar з UDS — НЕ live tick. Гарантія:

- За один cowork-цикл (виклик 3 endpoints поспіль за <1s) усі три відповіді повернуть **те саме** `current_price`
- Вік даних ≤ 15 хв (M15 bar period)
- Race conditions з tick stream не можливі

Це означає: cowork **не повинен** використовувати `current_price` як live quote. Це reference price для контексту аналізу.

---

## 10. `/bars/window` — incremental fetch pattern

**Перший виклик** (без `?since_ms`):

```bash
curl -s -H "X-API-Key: $TOKEN" \
  "https://aione-smc.com/api/v3/bars/window?symbol=XAU/USD&tfs=M15,H1,H4&count=200" \
  | jq '.data.meta.latest_open_ms'
```

Відповідь містить `data.meta.latest_open_ms = {M15: ..., H1: ..., H4: ...}`. Cowork запам'ятовує найбільший `open_ms` per TF.

**Наступні виклики** (incremental):

```bash
LAST_M15=1714794300000
curl -s -H "X-API-Key: $TOKEN" \
  "https://aione-smc.com/api/v3/bars/window?symbol=XAU/USD&tfs=M15&since_ms=$LAST_M15"
```

Повертає тільки бари з `open_ms > since_ms`. Якщо нових немає — `data.bars.M15 = []` (HTTP 200, не 204).

**Edge cases**:

- `since_ms` старший за рік → server повертає full window + `data.meta.warnings: ["since_ms_too_old_full_window_returned"]`
- `since_ms > latest_open_ms` → пустий масив per TF (не помилка)
- Запит з тим самим `since_ms` ідемпотентний (complete bars immutable)

**Bandwidth економія**: full window ≈ 80 KB; типовий incremental ≈ 5 KB.

---

## 11. `/smc/zones` — pagination (cursor-based, ADR-0059 §5.5)

Зони рідко стабільні: `proximity_atr` пересортовує snapshot щоразу як ціна рухається. Тому використовуй **cursor-based pagination**, не `?offset`.

**Перша сторінка** (без `?cursor`):

```bash
curl -s -H "X-API-Key: $TOKEN" \
  "https://aione-smc.com/api/v3/smc/zones?symbol=XAU/USD&tf=M15&limit=50" \
  | jq '{snapshot_id: .data.snapshot_id, next_cursor: .data.next_cursor, count: (.data.zones|length)}'
```

Відповідь:

- `data.snapshot_id` — SHA1 prefix зон у поточному snapshot (детермінований)
- `data.next_cursor` — opaque base64url token; `null` якщо це остання сторінка
- `data.zones` — до 50 зон, відсортовані по `proximity_atr ASC` (найближчі — перші)

**Наступні сторінки**:

```bash
CURSOR="eyJzbmFwc2hvdF9pZCI6...."
curl -s -H "X-API-Key: $TOKEN" \
  "https://aione-smc.com/api/v3/smc/zones?symbol=XAU/USD&tf=M15&limit=50&cursor=$CURSOR"
```

**Stale cursor handling**: якщо snapshot змінився між сторінками (нова зона з'явилась або grade rerated), відповідь міститиме `data.warnings: ["cursor_stale"]` + best-effort serve решти зон. Cowork **не повинен** ретраїти — просто продовжувати з новим `next_cursor`.

**Cursor rules**:

- `?cursor` має пріоритет над `?offset` коли обидва задані (offset тихо ігнорується)
- Cursor — opaque token; cowork **не парсить** його вміст
- Якщо `?cursor` malformed → `400 cursor_invalid`
- Якщо JSON всередині cursor зіпсований → `400 cursor_corrupt`

**`grade_score` приховано за замовчуванням** (ADR-0059 §3.1.2 / X28 enforcement):

- `grade` (`A+`/`A`/`B`/`C`) повертається завжди
- `confluence_factors` (list[str]) повертається завжди
- Числовий `grade_score` доступний лише з `?include_internal=true` — **cowork prompt template НЕ ВИКОРИСТОВУЄ цей параметр**

---

## 12. `/smc/levels` — compact response

Без pagination, фіксована структура:

```json
{
  "schema_version": "v3.1",
  "kind": "smc_levels",
  "data": {
    "symbol": "XAU/USD",
    "current_price": 2325.59,
    "previous_day": { "high": ..., "low": ..., "close": ..., "ts_ms": ... },
    "previous_week": { "high": ..., "low": ... },
    "sessions": {
      "asia":   { "high": ..., "low": ..., "complete": true,  "swept_high": false, "swept_low": false },
      "london": { "high": ..., "low": ..., "complete": false, "swept_high": false, "swept_low": false },
      "ny":     { "high": null, "low": null, "complete": false, "swept_high": false, "swept_low": false }
    }
  }
}
```

**Notes**:

- `previous_week` — лише `high`/`low` (за дизайном; ADR-0059 §3.1.3)
- Session name mapping: backend internal `newyork` → wire `ny`
- `swept_*` обчислені як `current.high > previous.high` (boolean derive, не domain re-classification — X28 OK)
- `complete: true` ⇔ session має дані AND неактивна

Payload ≤ 5 KB.

---

## 13. Analysis kill switch (ADR-0059 §3.2)

`bars/window` + `smc/zones` + `smc/levels` мають **окремий** kill switch від `signals/*`:

- Redis flag: `v3_local:api_v3:analysis_kill`
- Якщо встановлено → endpoints повертають **503** з envelope:
  ```json
  { "kind": "error", "schema_version": "v3.1",
    "data": { "code": "analysis_disabled_runtime", "message": "..." } }
  ```
- `signals/*`, `bias/*`, `narrative/*`, `macro/*` продовжують працювати

**Cowork behavior on `503 analysis_disabled_runtime`**:

1. Зупинити аналітичний цикл (не публікувати нічого з `bars/smc`)
2. Повернутись до v3.0-only mode (signals transcriber)
3. Періодично (раз на 5 хв) пробувати відновити; alert ops якщо kill triggered >24h

Ops toggle: `python -m tools.api_v3.toggle_analysis --on|--off|--status`.

---

## 14. New error codes (v3.1 endpoints)

| Code | HTTP | Endpoint(s) | Meaning |
|---|---|---|---|
| `analysis_disabled_runtime` | 503 | bars/*, smc/* | Kill switch ON |
| `analysis_disabled_config` | 503 | bars/*, smc/* | `config.json:api_v3.analysis_enabled=false` |
| `cursor_invalid` | 400 | smc/zones | `?cursor` not valid base64url |
| `cursor_corrupt` | 400 | smc/zones | Cursor decodes but JSON shape wrong |
| `payload_too_large` | 503 | bars/*, smc/* | Internal cap exceeded (request smaller window/limit) |
| `since_ms_too_old_full_window_returned` | 200 (warning) | bars/window | `since_ms` >1y old; got full window instead |
| `cursor_stale` | 200 (warning) | smc/zones | Snapshot mutated mid-pagination; best-effort served |

---

## 15. Cowork obligations for v3.1 (X28 expanded)

В додаток до §6, для аналітичних endpoints:

- **MUST NOT** перерахувати `grade` / `grade_score` / `confluence_factors` зон
- **MUST NOT** використовувати `?include_internal=true` в production prompt
- **MUST NOT** обчислювати власні OB/FVG/liquidity зони з bars (use `/smc/zones`)
- **MUST NOT** обчислювати власну session H/L (use `/smc/levels`)
- **MUST NOT** трактувати `current_price` як live tick
- **MAY** обчислювати: distance до зони у власних одиницях, своє форматування часу/цін, narrative around factors
- **MAY** комбінувати дані з 3 endpoints в один пост (зберігаючи disclaimer)

Перевіряється в 5-scenario gate (`docs/runbooks/cowork_prompt_validation.md`).

