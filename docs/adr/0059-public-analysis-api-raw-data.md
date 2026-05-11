# ADR-0059: Public Analysis API — Raw Data Access for External Consumers

- **Status**: **Withdrawn — Failed Experiment** (2026-05-11). Cowork ідея визнана провальною: ~1500 преміум-запитів витрачено на промпт-tuning, 5-scenario gate так і не пройдено, прийнятої цінності бот не дав. Endpoints, runner, event_watcher, supervisor program, runtime data, токен `cowork_event_watcher` — повністю видалено (локально + VPS). Зберігається лише як запис прийнятого і відхиленого рішення. Початковий статус: Deferred (2026-05-04).
- **Date**: 2026-05-04
- **Author**: Стас
- **Initiative**: `public_api_v1` (continuation)
- **Related ADRs**: ADR-0058 (Public Read-Only API + Token Auth), ADR-0024 (SMC Engine), ADR-0029 (OB Confluence Scoring), ADR-0035 (Sessions & Killzones), ADR-0024b (Key Levels), ADR-0001 (UDS)
- **Supersedes**: nothing — extends ADR-0058 with new endpoints under same auth/envelope umbrella

---

## Quality Axes

- **Ambition target**: R2.5 — нова поверхня з self-contained kill switch + version-bump до v3.1; ключове досягнення — переведення cowork bot з режиму "transcriber коду" в режим "аналітик з доступом до сирих даних"
- **Maturity impact**: M3 → M4 — додає runtime-controlled feature flag (Redis kill switch без supervisor restart) + versioned schema migration pattern + feature-level granularity для public API

---

## 1. Контекст і проблема

### 1.1 Поточний стан (post ADR-0058)

ADR-0058 встановив auth-protected public API з 5 endpoints:

| Endpoint | Тип даних | Призначення |
|---|---|---|
| `GET /api/v3/signals/latest` | TDA signal | Останній A+ сигнал |
| `GET /api/v3/signals/journal?date=...` | TDA signals history | Денний журнал |
| `GET /api/v3/bias/latest?symbol=...` | Multi-TF bias map | Висновок системи про напрямок |
| `GET /api/v3/narrative/snapshot?symbol=...` | TDA narrative блок | Текстовий висновок системи |
| `GET /api/v3/macro/context?symbol=...` | Sessions/killzones state | Контекст |

Усі endpoints — **висновки системи**, не сирі дані.

### 1.2 Проблема "Cowork як transcriber коду"

Cowork bot (старий новинний бот, розгорнутий у Claude Desktop scheduled task, працює поки Архі в hibernation) був задизайнений навколо ADR-0058 endpoints. На практиці виявилось:

| Симптом | Причина | Наслідок |
|---|---|---|
| Cowork часто перевіряє API (раз на 5-15 хвилин), але сигнали генеруються рідко (1-3 на день) | TDA cascade консервативний за дизайном | Cowork постить ОДНЕ І ТЕ Ж — повторні нотатки про той самий signal/bias |
| Cowork "трактує висновки коду" замість "формує власну думку про ринок" | API дає тільки post-hoc system output, не raw market context | Контент = техно-репорт, не ринковий аналіз |
| Власник просить "аналіз ETHUSDT зараз" — cowork не може відповісти, бо немає свіжого signal | API не дає bars / zones / levels — лише останній згенерований TDA вердикт | Cowork безсилий для on-demand asks |

**Корінь проблеми**: ADR-0058 експонує **"що сказала система"**, але не **"з чого система виводить"**. Для аналітика потрібен **свіжий ринковий context** (свічки, зони, рівні) — те що щохвилини отримує Архі через WS frame.

### 1.3 Що отримує Архі (для еталону)

Архі (через WS delta stream від `ws_server`):

- **Bars**: rolling window 200+ свічок per TF (M15/H1/H4/D1)
- **SMC zones**: всі OB/FVG/Liquidity з grade + status + confluence factors
- **Swings**: BOS/CHoCH events
- **Key levels**: PDH/PDL/PWH/PWL/Asia/London/NY session H-L
- **PD state**: premium/discount + EQ
- **Bias map**: per-TF
- **Sessions / killzones**: live стан

Це і є SSOT для будь-якого торгового аналітика на платформі.

### 1.4 Чому не дати cowork той самий WS

Розглядалось і відкинуто (Option B з discussion 2026-05-04):

| Критерій | WS streaming | REST polling |
|---|---|---|
| Складність cowork-side | Висока (WS client, reconnect, frame parser) | Низька (HTTP fetch) |
| Bandwidth | ~5-15 KB/s постійно | ~30-60 KB per request, кожні 5-15 хв |
| Rate-limit модель | Per-connection | Per-token (вже існує) |
| Auth | WS handshake + token | Існуючий header pattern |
| Failure mode | Stuck connection, silent stale | HTTP timeout — explicit |
| Auditability | Складно (frame-level audit) | Легко (request-level audit JSONL) |

REST polling простіший і symmetric з ADR-0058. WS залишаємо як майбутній ADR якщо знадобиться.

### 1.5 Чому не filesystem read

Cowork живе на **окремому хості** (Claude Desktop sandbox), не має SSH/SFTP/SMB доступу до VPS. Дозволити такий доступ = security S0 (filesystem leak surface). Закрито.

---

## 2. Альтернативи

### 2.1 Hybrid REST polling (recommended)

Додати **3 нові endpoints** під тим же auth/envelope umbrella:

```
GET /api/v3/bars/window?symbol=...&tfs=M15,H1,H4
GET /api/v3/smc/zones?symbol=...&tf=M15&limit=50&offset=0
GET /api/v3/smc/levels?symbol=...
```

**+** Symmetric з ADR-0058 (token auth, envelope, audit JSONL — все existing infra)
**+** Cowork отримує те ж що Архі — equal capabilities
**+** Контракт versioned (X28 тримається — UI dumb renderer principle)
**+** Existing audit + rate-limit працюють без змін
**−** Більший payload (30-60 KB) → треба новий rate-limit zone або reuse
**−** Polling latency (1-15 хв) — для аналітики ок, для real-time трейдингу — ні

### 2.2 WebSocket streaming endpoint

```
WS wss://aione-smc.com/api/v3/stream?token=...
```

**+** Real-time, eliminates polling
**+** Full symmetry з Архі
**−** Складніше для cowork (WS client lifecycle, reconnect)
**−** Bandwidth concerns (постійний потік)
**−** Auth модель інша (handshake-time validation)
**−** Audit складніший (per-frame instead of per-request)

**Rejected for now** — WS буде окремим ADR якщо/коли cowork доросте до real-time use case.

### 2.3 Filesystem mount / SSH read of `data_v3/_signals/*.jsonl`

**Rejected** — security S0.

### 2.4 Не робити нічого, дочекатись Архі

**Rejected** — Архі повертається після Anthropic monthly reset (2026-06-01); до того часу cowork залишається в transcriber-mode, що псує канал.

---

## 3. Рішення

**Обрано Альтернатива 2.1.** 3 нові endpoints + kill switch + version bump до **v3.1**.

### 3.1 Cross-endpoint invariants (apply to усіх 3 analysis endpoints)

**Single source of `current_price`** (F-S3-001 — Opus audit):

За один HTTP-цикл cowork може викликати `bars/window` + `smc/zones` + `smc/levels`. Якщо tick prevailed між запитами — три різні значення `current_price` у відповідях створюють inconsistency.

**Контракт**: усі endpoints, які повертають `current_price`, обчислюють його як **`close` останнього complete M15 bar з UDS** (а не live tick). Це гарантує:

- Консистентність крос-endpoint у межах 15-хвилинного вікна
- Жодних race conditions з tick stream
- Cowork знає точно: "current_price = M15.close, age ≤ 15 хв"

Якщо знадобиться live tick price — окремий ADR (out of scope для 0059).

### 3.1.1 `GET /api/v3/bars/window` (multi-TF, incremental support)

**Query params**:

| Param | Required | Default | Validation |
|---|---|---|---|
| `symbol` | yes | — | `XAU/USD`, `XAG/USD`, `BTCUSDT`, `ETHUSDT` |
| `tfs` | no | `M15,H1,H4` | Comma-separated, allowed: `M15,H1,H4` (M15,H1,H4 only — D1 не потрібен для intraday analysis) |
| `count` | no | `200` | min 50, max 200 (per TF) |
| `since_ms` | no | — | Epoch ms; повертає тільки бари з `open_ms > since_ms` per TF (incremental fetch — F-S1-001 Opus audit) |

**Incremental fetch semantics** (`?since_ms`):

Cowork polls раз на 5-15 хв. Без `since_ms` кожен запит повертає 200 свічок × 3 TF ≈ 80 KB, з яких ~95% — ті ж самі immutable complete bars з попереднього запиту. Це wasted bandwidth + UDS read pressure.

- Перший fetch: cowork викликає **без** `since_ms` → отримує full 200 bars per TF + останній `open_ms` запам'ятовує
- Наступні fetch: викликає з `?since_ms=<last_open_ms>` → отримує тільки нові complete bars (зазвичай 0-5 per TF)
- Якщо `since_ms > останній bar open_ms` — повертається пустий масив `[]` per TF (NOT 204 — бо інші TF можуть мати дані)
- Якщо `since_ms` старший за рік — server повертає full 200 (як без параметра) + warning у `meta.warnings: ["since_ms_too_old_full_window_returned"]`
- Idempotent: запит з тим самим `since_ms` завжди повертає той самий результат (бо complete bars immutable)

**Response shape**:

```json
{
  "schema_version": "v3.1",
  "kind": "bars_window",
  "server_ts": "2026-05-04T18:30:00Z",
  "disclaimer": "Educational/research data only. Not financial advice.",
  "data": {
    "symbol": "ETHUSDT",
    "current_price": 2325.59,
    "incremental": false,
    "since_ms": null,
    "bars": {
      "M15": [
        {"open_ms": 1714780800000, "o": 2310.5, "h": 2315.0, "l": 2308.2, "c": 2314.1, "v": 1234.5},
        ... (up to 200 bars)
      ],
      "H1": [ ... ],
      "H4": [ ... ]
    },
    "meta": {
      "latest_open_ms": {"M15": 1714794300000, "H1": 1714791600000, "H4": 1714780800000},
      "warnings": []
    }
  }
}
```

При incremental fetch: `incremental: true`, `since_ms: <value>`, `bars.<TF>` містить тільки нові, `meta.latest_open_ms` — найновіший `open_ms` у вікні (не у delta). Cowork використовує `latest_open_ms` для наступного `since_ms`.

**Інваріанти**:

- Тільки `complete=true` бари (preview виключено)
- Monotonic ASC по `open_ms`
- Dedup по `(tf, open_ms)`
- Source: UDS `read_window()` (I1 — UDS = вузька талія)
- Атомарний snapshot — всі 3 TF з одного moment in time (один `read_window()` call sequence без yield)
- `current_price` per §3.1 cross-endpoint rule (M15.close)

**Payload бюджет**:

- Full snapshot: ≤ 80 KB (3 TF × 200 bars × ~120 bytes per bar = ~72 KB). Hard cap: max_bytes 102400 (100 KB) → 503 + degraded log якщо exceeded.
- Incremental (typical): ≤ 5 KB (sub-15-min polling pattern)

#### 3.1.2 `GET /api/v3/smc/zones` (paginated)

**Query params**:

| Param | Required | Default | Validation |
|---|---|---|---|
| `symbol` | yes | — | XAU/USD, XAG/USD, BTCUSDT, ETHUSDT |
| `tf` | yes | — | M15, H1, H4 |
| `limit` | no | 50 | min 1, max 200 |
| `offset` | no | 0 | min 0 |
| `kind` | no | `all` | `all`, `ob`, `fvg`, `liquidity` |
| `status` | no | `active` | `active`, `mitigated`, `all` |
| `include_internal` | no | `false` | `true` повертає `grade_score` (числовий). За замовчуванням приховано (F-S1-003 Opus audit) |

**Response shape (default — без `include_internal`)**:

```json
{
  "schema_version": "v3.1",
  "kind": "smc_zones",
  "server_ts": "...",
  "disclaimer": "...",
  "data": {
    "symbol": "ETHUSDT",
    "tf": "M15",
    "current_price": 2325.59,
    "total": 47,
    "limit": 50,
    "offset": 0,
    "zones": [
      {
        "id": "ob_eth_900_1714780800000",
        "kind": "order_block",
        "direction": "bearish",
        "tf": "M15",
        "top": 2330.50,
        "bottom": 2325.99,
        "anchor_ms": 1714780800000,
        "last_touch_ms": 1714782300000,
        "status": "active",
        "grade": "A",
        "confluence_factors": ["displacement", "session_sweep", "htf_alignment"],
        "distance_pts": 4.41,
        "proximity_atr": 1.2
      },
      ...
    ]
  }
}
```

**Why `confluence_factors` exposed but `grade_score` hidden** (Opus F-S1-003 compromise):

- `grade` (`A`/`B`/`C`/`D`) — фінальний висновок системи. Cowork використовує як є
- `confluence_factors` — **factual list** які умови виконались. Cowork ПОТРЕБУЄ цього щоб писати свій наратив ("displacement + sweep Asia low — institutional trap"). Без factors — знову transcriber
- `grade_score` (числовий 0-10) — **provoke score(factors) reverse-engineering**. LLM-cowork отримавши `score: 7` + factors може почати "уточнювати, у мене виходить 8" → X28 violation. Тому ховаємо за `?include_internal=true` (для debug / R_BUG_HUNTER access, не для cowork prompt template)

Cowork prompt template (slice 059.5) **MUST NOT** використовувати `?include_internal=true` (validate в manual review gate).

**Сортування**: `proximity_atr ASC` (найближчі до поточної ціни — перші).

**Виключені поля** (internal/debug, X28-захищено): `swing_id`, `internal_seq`, `_rebuild_id`, всі `_*` поля. `grade_score` — приховано в default response (див. вище).

**Pagination caveat**: offset/limit без cursor → можливий race якщо SmcRunner mutate snapshot між page 1 і page 2. Для cowork polling pattern (`limit=50` зазвичай покриває весь active set) це безпечно. Cursor-based pagination — slice 059.7 (F-S2-001).

**`current_price`** per §3.1 cross-endpoint rule (M15.close).

**Payload бюджет**: ≤ 50 KB (50 zones × ~700 bytes = ~35 KB; redact `grade_score` економить ~10 bytes/zone).

#### 3.1.3 `GET /api/v3/smc/levels`

**Query params**:

| Param | Required | Default |
|---|---|---|
| `symbol` | yes | — |

**Response shape**:

```json
{
  "schema_version": "v3.1",
  "kind": "smc_levels",
  "server_ts": "...",
  "disclaimer": "...",
  "data": {
    "symbol": "ETHUSDT",
    "current_price": 2325.59,
    "previous_day": {
      "high": 2345.06,
      "low": 2297.40,
      "close": 2310.20,
      "ts_ms": 1714694400000
    },
    "previous_week": {
      "high": 2389.50,
      "low": 2280.10
    },
    "sessions": {
      "asia":   { "high": 2325.99, "low": 2296.01, "complete": true,  "swept_high": false, "swept_low": false },
      "london": { "high": 2343.20, "low": 2310.00, "complete": false, "swept_high": false, "swept_low": false },
      "ny":     { "high": null,    "low": null,    "complete": false, "swept_high": false, "swept_low": false }
    }
  }
}
```

**Без pagination** — фіксований невеликий dataset.

**Payload бюджет**: ≤ 5 KB.

### 3.2 Kill Switch (Redis-controlled, no restart)

**Окремо від `signals/*` endpoints** — `bars/`, `smc/zones`, `smc/levels` мають власний killswitch.

**Config (`config.json`)**:

```json
{
  "api_v3": {
    "enabled": true,
    "audit_dir": "data_v3/_audit",
    "signals_enabled": true,    // controls signals/* + bias/* + narrative/* + macro/* (existing)
    "analysis_enabled": true    // controls bars/window + smc/zones + smc/levels (NEW)
  }
}
```

**Runtime kill switch (Redis)**:

- Flag: `v3_local:api_v3:analysis_kill`
- Якщо exists і `!= "0"` → middleware returns **503** з envelope:
  ```json
  {
    "schema_version": "v3.1",
    "kind": "error",
    "data": { "code": "analysis_disabled", "message": "Analysis endpoints temporarily disabled by operator" }
  }
  ```
- Перевірка лише на `bars/`, `smc/*` paths — `signals/*` працює незалежно
- TTL: optional (admin може `EXPIRE 86400` для auto-undo)

**CLI tool**: `tools/api_v3/toggle_analysis.py`

```bash
python -m tools.api_v3.toggle_analysis --off    # set flag, log warning
python -m tools.api_v3.toggle_analysis --on     # delete flag
python -m tools.api_v3.toggle_analysis --status # query current state
python -m tools.api_v3.toggle_analysis --off --ttl 3600  # auto-undo in 1h
```

**TG command** (slice 059.6, optional, після Архі recovery):

- `/cowork_kill` — owner-only, викликає той самий CLI internally
- `/cowork_resume` — owner-only, відключає flag

### 3.3 Schema version bump

| Версія | Endpoints | Live since |
|---|---|---|
| `v3.0` | signals/*, bias/*, narrative/*, macro/* | 2026-05-03 (ADR-0058) |
| `v3.1` | + bars/window, smc/zones, smc/levels | 2026-05-04 (ADR-0059) |

Старі endpoints (`v3.0`) **продовжують повертати `v3.0`** в `schema_version` — backward compat. Нові endpoints повертають `v3.1`. Cowork перевіряє: якщо бачить `v3.1` — нові endpoints доступні.

### 3.4 Auth + audit

- Same token (`tk_84a*` — `old_news_bot`) дає доступ і до signals/*, і до analysis/* (немає per-endpoint scope в цій версії)
- Audit JSONL логує **всі** запити однаково (existing F-S3-002)
- Per-token rate-limit reuses existing nginx zone (2 r/s burst 30) — для analysis polling це достатньо (cowork запитує 1-3 рази на 15 хв)

### 3.5 Symbol scope

| Symbol | Status | Підтримка |
|---|---|---|
| `XAU/USD` | **Primary** | Full support, акцент уваги |
| `XAG/USD` | Secondary (correlation) | Read access — для cowork як "що поряд робить срібло" |
| `BTCUSDT` | Available | Full support (24/7) |
| `ETHUSDT` | Available | Full support (24/7) |

Cowork може запитувати XAG для correlation context, але primary analysis = XAU.

### 3.6 X28 (Consumer Invariants) — extended

Cowork тепер отримує сирі дані. **MUST NOT** перераховувати:

- ❌ `grade` — це SSOT висновок системи; cowork використовує як є
- ❌ Власна функція `score(confluence_factors)` → не може давати "свою думку про A vs B+"
- ❌ Класифікація бару як "bullish OB" / "bearish FVG" — це SmcEngine output
- ❌ Session boundaries (ADR-0035) — використовувати як є
- ❌ `swept_high/swept_low` — це SmcEngine fact
- ❌ Викликати endpoints з `?include_internal=true` (це debug-only flag, не для cowork)

Cowork **MAY**:

- ✅ Цитувати `confluence_factors` у наративі ("zone has displacement + session_sweep + htf_alignment")
- ✅ Обчислювати власні метрики поверх bars (ATR, RSI, Bollinger — будь-які індикатори що НЕ є SMC reasoning)
- ✅ Композувати наратив з даних (DXY + bars + zones + factors → trader thesis)
- ✅ Фільтрувати zones за proximity / grade для свого UI
- ✅ Combine M15+H1+H4 для own narrative weights (поверх SMC grades — НЕ замість)

**Enforcement**: cowork prompt template (slice 059.5) має explicit "DO NOT recompute grade" + 5-scenario manual review gate перевіряє цю поведінку (див. §5.5).

---

## 4. Наслідки

### 4.1 Позитивні

- **Cowork стає аналітиком, не transcriber** — отримує raw context для own thinking
- **On-demand answers** — "що по ETHUSDT зараз?" → cowork fetches latest bars+zones+levels
- **Архі-symmetric capabilities** — cowork бачить те ж що Архі (в міру polling latency)
- **Maturity bump M3 → M4** — runtime feature flag pattern встановлено для майбутніх public API extensions

### 4.2 Негативні

- **Більший payload** → треба моніторити cowork polling pattern перші 7 днів через audit JSONL
- **Cowork тепер може робити "wrong" висновки** з сирих даних — більше відповідальності на cowork prompt template
- **Версіонування ускладнюється** — два рівні `v3.0` / `v3.1` в одній API surface (mitigation: чітка документація в quickstart v2)
- **Rate-limit pressure** — якщо cowork polling agresivly, може hit existing 2 r/s limit; mitigation: prompt template caps polling до раз на 5 хв

### 4.3 Ризики

| ID | Risk | Severity | Mitigation |
|---|---|---|---|
| R1 | Cowork polls aggressively, eats rate-limit | M | Quickstart v2 prescribes 5-min cache; alert on `429_total` metric |
| R2 | Cowork interprets raw zones contrary to SMC reasoning | M | X28 extended; reference prompt template based on R_TRADER + R_MENTOR |
| R3 | Payload growth блокує WS server (event loop) | L | Bars query uses UDS `read_window()` (already optimized); 100 KB hard cap |
| R4 | Kill switch not honored due to Redis outage | L | **Fail-open by design** — якщо Redis недоступний, analysis endpoints продовжують відповідати 200 (не блокуємо legitimate traffic). Rationale: kill switch — це operator policy flag, НЕ security gate (auth path вже залежить від Redis на 058 і дасть 503 якщо Redis впав). Краще зберегти availability де можливо ніж DoS на analysis surface. **Alerting**: Prometheus counter `api_v3_kill_switch_check_failed_total` + log warning кожні 60s; PagerDuty якщо >10/min |
| R5 | Schema v3.0 vs v3.1 confusion | L | Quickstart v2 explicit table; tests assert correct version per endpoint; integration test slice 059.7 включає schema-migration test |
| R6 | Cowork hallucinates поверх raw data → low-quality posts | M | Prompt template = first-class deliverable (slice 059.5) з 5-scenario manual review gate; analysis endpoints не йдуть live доки prompt не пройшов gate (F-S1-005). Періодичний re-review (раз на місяць або при market regime change) |
| R7 | Bars/window без `since_ms` параметра → bandwidth waste | L | `?since_ms` semantics задокументовано в §3.1.1; quickstart v2 prescribes incremental fetch як default pattern |

### 4.4 Rollback plan

- **Feature-level rollback**: `analysis_enabled: false` у config.json → 503 на нові endpoints, signals/* далі працюють. Restart `smc:smc-ws`.
- **Runtime rollback**: `python -m tools.api_v3.toggle_analysis --off` → миттєвий 503 без рестарту
- **Code rollback**: `git revert` slices 059.1-059.4. signals/* (058.x) недоторкнуто.
- **Schema rollback**: новий cowork код тримати compat з обома v3.0 та v3.1 envelope shapes — старі endpoints не змінилися.

### 4.5 Cowork lifecycle post-Архі recovery (F-S3-002 — Opus audit)

Архі повертається після Anthropic monthly reset (~2026-06-01) і відновлює власні TG публікації. Raw data endpoints (`bars/window`, `smc/zones`, `smc/levels`) **не залежать** від Архі — продовжують працювати незалежно. Питання: що робити з cowork bot?

**Опції**:

| Опція | Що робить cowork | Pros | Cons |
|---|---|---|---|
| **A — Continue parallel** (default) | Cowork постить далі, в свій канал, паралельно з Архі | Different channels OK; cowork = market context, Архі = personal trading; redundancy на випадок Архі hibernation повторно | Можливі дублі думок; +cost cowork (Claude Desktop subscription) |
| **B — Pause via kill switch** | Owner викликає `--off` 2026-06-01, cowork tихий | Zero overlap, zero conflict | Втрата continuity якщо Архі знову hibernation |
| **C — Role pivot** | Cowork переходить на weekly recap mode (раз на тиждень) | Differentiated — cowork = strategic, Архі = tactical | Потребує окремий prompt template, додатковий слайс |

**Default**: **Опція A** (continue parallel). Owner-decision point на 2026-06-05 (через 5 днів спостереження) — якщо overlap токсичний, перейти на B або C. Не блокує 0059 implementation.

---

## 5. Імплементація

### 5.1 Slices

| Slice | Що | Ціль LOC | Tests |
|---|---|---|---|
| **059.4** | Kill switch (Redis flag + middleware + CLI tool) + tests. **First — safety gate** | ~100 | 4 |
| **059.1** | `GET /api/v3/bars/window` (multi-TF + `?since_ms` incremental) + tests | ~180 | 7 |
| **059.2** | `GET /api/v3/smc/zones` (paginated, filtered, `?include_internal` gating) + tests | ~140 | 6 |
| **059.3** | `GET /api/v3/smc/levels` + tests | ~80 | 4 |
| **059.7** | Cursor-based pagination для smc/zones + integration/load/schema-migration test slice (F-S2-001+002) | ~150 | 7 |
| **059.5** | **First-class deliverable**: `cowork_consumer_quickstart.md` v2 + `cowork_prompt_template.md` (R_TRADER + R_MENTOR base) + 5-scenario manual review gate (§5.5). **Endpoints не йдуть live (kill switch OFF) доки prompt template не пройшов 5/5 scenarios** | docs + gate | manual |
| **059.6** | (Optional, post Архі recovery 2026-06-01+) TG `/cowork_kill` + `/cowork_resume` commands in trader-v3 | ~40 | 2 |

**Order of execution** (revised post Opus audit):

```
059.4 (kill switch, ON by default for analysis_*)
  ↓
059.1 (bars + since_ms)
  ↓
059.2 (zones, grade_score gated)
  ↓
059.3 (levels)
  ↓
059.7 (cursor + integration/load/schema-migration tests)
  ↓
059.5 (docs + prompt template + 5-scenario review)
  ↓
GATE: prompt template passes 5/5 scenarios?
  ├─ YES → kill switch OFF → analysis live → cowork integration
  └─ NO  → iterate prompt → re-run scenarios
  ↓
059.6 (optional, after Архі повертається)
```

Обґрунтування "kill switch first": хочемо мати safety гачок до того як endpoints піднімуться. Endpoints імплементовані з kill switch ON by default — стають доступні тільки після successful prompt review gate.

### 5.2 Файли (estimated)

| Файл | Дія | Slice |
|---|---|---|
| `runtime/api_v3/endpoints.py` | Додати 3 handlers + kill switch middleware + cursor pagination | 059.1-059.4, 059.7 |
| `runtime/api_v3/__init__.py` | Експорт нових handlers | 059.1 |
| `runtime/ws/ws_server.py` | Wire `analysis_enabled` config + redis flag | 059.4 |
| `tools/api_v3/toggle_analysis.py` | NEW — CLI kill switch tool | 059.4 |
| `tests/test_api_v3_bars_window.py` | NEW (включає `?since_ms` semantics tests) | 059.1 |
| `tests/test_api_v3_smc_zones.py` | NEW (включає `?include_internal` gating tests) | 059.2 |
| `tests/test_api_v3_smc_levels.py` | NEW | 059.3 |
| `tests/test_api_v3_killswitch.py` | NEW (включає fail-open behavior test) | 059.4 |
| `tests/test_api_v3_integration.py` | NEW — end-to-end (token → endpoint → audit JSONL → envelope) + load test (24h cowork polling pattern simulation) + schema-migration test (v3.0 reader tolerates v3.1 extra fields) | 059.7 |
| `config.json` | + `api_v3.analysis_enabled: true` (default), + `analysis_kill_switch_default_state: "on"` для slice 059.5 gate | 059.4 |
| `docs/runbooks/cowork_consumer_quickstart.md` | Update до v2 (включає `?since_ms` pattern, current_price rule, schema v3.0/v3.1 differentiation) | 059.5 |
| `docs/runbooks/cowork_prompt_template.md` | NEW (R_TRADER + R_MENTOR base, explicit X28 boundaries, anti-hallucination guardrails) | 059.5 |
| `docs/runbooks/cowork_prompt_validation.md` | NEW — 5-scenario manual review checklist + acceptance criteria | 059.5 |
| `SECURITY.md` | A07 row update (kill switch + fail-open semantics) | 059.4 |

### 5.3 Інваріанти що PATCH повинен зберегти

- **I0**: `core/` не імпортує `runtime/api_v3/`
- **I1**: bars читаються тільки через UDS `read_window()`; zones — через `SmcRunner` snapshot
- **I3**: тільки `complete=true` бари в API response
- **I5**: kill switch активний → 503 + structured log; cowork бачить explicit reason у envelope (не silent 502)
- **X28**: cowork prompt template explicitly forbids re-derive grades / structure classifications

### 5.4 Verification matrix per slice

Кожен slice:

1. AST OK на touched files
2. New tests pass (mark count in PR description)
3. Existing 29 (post-058) + new tests = no regression
4. `tools.run_exit_gates` clean
5. Live deploy verify через audit JSONL (заявка з токеном → запис у `data_v3/_audit/api_v3_access-YYYY-MM-DD.jsonl`)
6. D9.1 60-120s observation window після supervisorctl restart

### 5.5 Slice 059.5 acceptance — 5-scenario manual review gate (F-S1-005 Opus audit)

Prompt template — це **core deliverable**, не side-doc. Без надійного prompt 059 ризикує дати cowork більше ammunition для тих самих проблем (просто з кращим vocabulary).

**Manual review protocol** (Стас + agent, периодичний):

Agent проганяє prompt template `cowork_prompt_template.md` через 5 reference scenarios. Для кожного — fixed input dataset (заздалегідь зафіксований API response від bars/window + smc/zones + smc/levels) → cowork-style LLM call → output validated проти acceptance checklist.

**5 scenarios**:

| # | Market state | Що перевіряємо |
|---|---|---|
| 1 | **No setup** (bias=neutral, no A/A+ zones, current_price у midrange) | Output **не вигадує** setup; чесно каже "чекаємо/немає edge" |
| 2 | **Strong A+ setup** (D1+H4 aligned bearish, A+ zone з displacement+sweep, OTE entry виразний) | Output цитує factors, дає чіткий plan, **не перераховує** grade |
| 3 | **Mixed signals** (H4 bearish, M15 bullish counter-trend) | Output explicit про conflict, не вигадує "всі TF aligned" |
| 4 | **News pending** (high-impact event у наступну годину) | Output згадує event-risk, рекомендує stand-aside |
| 5 | **Range exhausted** (ATR daily travel >150%) | Output попереджає про continuation risk |

**Acceptance checklist per scenario**:

- ✅ Усі numeric values (price, levels, R:R) **співпадають** з input API data (no hallucinated numbers)
- ✅ X28 не порушено: жодних reverse-engineered grade scores; output не каже "я б дав цій зоні B+ замість A"
- ✅ На "no setup" state cowork **не вигадує** setup
- ✅ `confluence_factors` цитуються як є (не перейменовуються, не комбінуються)
- ✅ Sessions / killzones використовуються як factual input, не перераховуються
- ✅ Output language: ukrainian, brief (≤500 words), trader-grade (NOT system log style)

**Gate semantics**:

- **5/5 pass** → kill switch OFF → analysis endpoints live → cowork активується
- **<5/5 pass** → prompt iterates → re-run failed scenarios; kill switch залишається ON
- **Periodic re-review**: раз на місяць або при market regime change (новий high-impact event class, structural market shift) — owner може запустити re-validation. Не automated CI gate (ми не контролюємо cowork sandbox).

**Output**: коротка нотатка в `docs/runbooks/cowork_prompt_validation.md` з датою last review + verdict + rationale якщо fail.

#### 5.5.1 Slice 059.5c amendment (2026-05-04) — prompt template v2 supersession

Prompt template v2 (slice 059.5c) **supersedes** v1 (slice 059.5). Re-validation gate runs fresh — v1 review row у `cowork_prompt_validation.md` §5 marked **superseded, never gated**; v2 review row створюється pending.

**Що зміст v2 додає** (vs v1):

- **DT-канон як constitutional fence**: 5 ключових законів (DT-2 HTF=закон, DT-4 sweep=передумова, DT-5 бездіяльність=позиція, DT-9 сесії, DT-10 зона=intent) у system prompt. Решта 5 — у methodology reference.
- **IOFED Drill awareness**: cowork розпізнає stages 1-3 у живому ринку, описує IOFED stage у тезі. Stage 4-5 — live trader concern, cowork не "входить" сам.
- **THIN-ICE WATCHDOG**: 6 auto-warning patterns (P1, P2, P4, P6, P10, P11 з R_MENTOR §1.2) — спрацьовує до thesis-секції, замінює thesis на ⚠️ THIN ICE block.
- **NEWS GATE (м'який)**: high-impact event ±30min → ⚠️ NEWS GATE block, не auto-stand-aside (cowork voice залишається активним з event-risk warning).
- **ATR/displacement local compute clarification**: явний дозвіл — це математика, не grade re-derive (X28 OK).
- **6-section post structure** (вместо 4): додає HTF READING + ⚠️ THIN ICE optional.
- **Scope XAU explicit**: інші символи endpoints приймають, але v2 calibrated для золота.

**Methodology reference**: новий файл `cowork_methodology.md` (≤8 KB target — conservative budget for unknown cowork delivery mechanism: Claude Desktop scheduled task can be Projects-RAG OR static system prompt). Містить full DT-канон, hierarchy of TFs (cowork-adapted: D1 proxied via levels), IOFED 5-stage chart, Session Canon + sweep semantics, displacement/momentum rules, Wyckoff phases vocabulary, A+ Setup Anatomy, full P1-P12 pitfalls, Multi-TF Nuance Decision Tree (R1 розширення з discussion), 15-term SMC glossary, mentor voice patterns, authority hierarchy.

**5/5 review gate execution required перед kill switch OFF — без exception.** Якщо при першому 5/5 виявимо що методологія потребує stable input fixtures для repeatability — додаткова slice 059.5d (post-flip).

**Decision log update**: 2026-05-04 — v2 template + methodology.md прийняті; v1 supersedes; conservative 8 KB methodology budget chosen в умовах невідомого cowork delivery mechanism (опція A з discussion).

---

## 6. Success criteria & metrics (F-S1-004 Opus audit)

ADR має вимірюваний exit. Без metrics 059 — open-ended commitment.

**Baseline**: збираємо manually з вже існуючих cowork публікацій у TG (no formal blocking observation period — owner оцінює retrospectively з історії каналу).

**Target window**: 7 днів після успішного 059.5 review gate (kill switch OFF, cowork активований з новим prompt).

| # | Metric | Baseline (припущення з історії) | Target (7d post-deploy) | Rollback trigger |
|---|---|---|---|---|
| M1 | % posts які транскрибують той самий signal/bias | ~70-80% | ≤30% | ≥60% за 7 днів |
| M2 | Avg unique narrative angles per day (різні "кути зору" на ринок) | ~1 | ≥3 | ≤1.5 за 7 днів |
| M3 | Hallucination rate (numeric values у text не співпадають з API data) | ~5% (припущення) | ≤2% | ≥10% за 3 дні |
| M4 | Cowork polling frequency (per endpoint, через audit JSONL) | TBD | ≤12 req/h per endpoint | Hit 2 r/s rate-limit ≥10 разів за 24h |

**Measurement source**:

- M1, M2 — manual review TG channel posts owner-ом (раз на тиждень, ~30 хв)
- M3 — agent perevірка: random sample 10 posts проти audit JSONL data; %hallucinated
- M4 — `data_v3/_audit/api_v3_access-*.jsonl` aggregation (ad-hoc PowerShell script)

**Rollback action** при triggered threshold: `python -m tools.api_v3.toggle_analysis --off` → cowork повертається на 058 endpoints (transcriber mode) → root-cause investigation → iterate prompt template → re-run 5-scenario gate → re-enable.

---

## 7. Open questions (НЕ блокують Proposed → Accepted)

1. **Caching headers** — чи додавати `Cache-Control: max-age=N` на response? Recommendation: `max-age=30` для bars/zones/levels (cowork може кешувати 30s локально). Decide in slice 059.1.
2. **CORS** — analysis endpoints inherit existing ADR-0058 CORS lock (server-side fetchers only, no browser direct). OK.
3. **Compression** — gzip support через nginx (вже є для існуючих endpoints). OK.
4. **Per-endpoint metrics** — додати `api_v3_analysis_requests_total{endpoint=...}` Prometheus counter? Defer to slice 059.4 if simple.
5. **Token scope split** — ADR-0058 §3.3.1 заклав scope vocabulary (`read`, `read:no-narrative`, `read:XAU/USD`). 059 reuses single token для signals + analysis. Коли з'явиться second consumer (research bot, Discord publisher), чи split scope на `read:signals` vs `read:analysis`? **Recommendation**: yes, але не блокуючи 059. Окремий micro-ADR коли second consumer з'явиться.

---

## 8. Decision log

- **2026-05-04**: ADR-0059 drafted post discussion (Q1-Q6 resolved):
  - Q1: multi-TF в одному запиті (atomic snapshot, ~30-60 KB ok)
  - Q2: object format `{open_ms, o, h, l, c, v}` (no internal `complete/source` exposure)
  - Q3: zones — variant C (full SMC fields minus internal IDs/debug)
  - Q4: levels — flat structure with `current_price` bonus, no pagination
  - Q5: separate kill switch (`analysis_enabled` config + Redis runtime flag + CLI tool)
  - Q6: schema bump до v3.1 (unified, backward compat for v3.0)
- **2026-05-04**: Symbol scope — XAU primary, XAG read-only correlation, BTC/ETH full support
- **2026-05-04**: Initial order — 059.4 (kill switch) first для safety, потім 059.1-059.3, потім docs
- **2026-05-04 (Opus 1M-context audit — revision A)**: Independent audit by Opus identified 9 amendments. Owner (Стас) accepted 7 fully + 2 with practical modifications (grade_score gated under `?include_internal` not full redact; manual 5-scenario review not automated CI test set). Applied amendments:
  - **F-S1-001**: `?since_ms` incremental fetch parameter for bars/window (~95% bandwidth reduction for cowork polling pattern). §3.1.1 updated.
  - **F-S1-002**: Renamed R4 from misleading "fail-closed" to **fail-open by design** + rationale (kill switch is policy flag, not security gate; auth path on 058 already fails-closed via Redis dependency) + Prometheus counter `api_v3_kill_switch_check_failed_total`. §4.3 updated.
  - **F-S1-003 (compromise)**: `confluence_factors` залишено в default response (cowork потрібен для narrative — інакше знову transcriber); `grade_score` (числовий) переміщено під `?include_internal=true` (debug-only, prompt template MUST NOT використовувати). §3.1.2 + §3.6 updated.
  - **F-S1-004**: Added §6 Success Criteria з 4 metrics (M1-M4) + targets + rollback triggers. NO blocking 7-day formal baseline observation period (per owner — оцінка manually з історії TG channel + ad-hoc post-deploy review).
  - **F-S1-005**: Slice 059.5 elevated до first-class deliverable з 5-scenario manual review gate (§5.5). Endpoints не йдуть live доки prompt template не пройшов 5/5. Periodic re-review (monthly або при market regime change) — manual, NOT automated CI (ми не контролюємо cowork Claude Desktop sandbox).
  - **F-S2-001**: Added slice 059.7 — cursor-based pagination для smc/zones (current offset/limit має race risk).
  - **F-S2-002**: Slice 059.7 також включає integration + load + schema-migration test slice (~150 LOC).
  - **F-S3-001**: Cross-endpoint `current_price` consistency rule — usе analysis endpoints використовують `M15.close` (не live tick). §3.1 (новий abzac) added.
  - **F-S3-002**: Cowork lifecycle post-Архі-recovery (default Опція A — continue parallel; owner-decision на 2026-06-05). §4.5 added.
  - **F-S3-003**: Token scope split (read:signals vs read:analysis) added to Open Questions §7 Q5.
- **Revised order** (post Opus audit): `059.4 → 059.1 → 059.2 → 059.3 → 059.7 → 059.5 (gate) → endpoints live → 059.6 (optional)`
- **TBD (Architect review)**: ADR move from Proposed → Accepted post Стас sign-off on revision A

---

## 9. References

- ADR-0058 §1, §3.1 — auth/envelope/audit baseline
- ADR-0058 §3.3.1 — token scope vocabulary (deferred until second consumer per Open Q5)
- ADR-0024 §3 — SmcZone shape (filter for X28 exposure)
- ADR-0029 — confluence factors taxonomy + 8-factor grade scoring
- ADR-0035 — sessions / killzones definitions
- ADR-0024b — key levels semantics (PDH/PDL/PWH/PWL)
- ADR-0001 — UDS `read_window()` API
- `docs/runbooks/cowork_consumer_quickstart.md` — current v1, бути updated до v2 in slice 059.5
- `docs/runbooks/cowork_prompt_template.md` — NEW в slice 059.5 (first-class deliverable)
- `docs/runbooks/cowork_prompt_validation.md` — NEW в slice 059.5 (5-scenario manual review log)
- `runtime/api_v3/endpoints.py` — current home for handlers
- `runtime/api_v3/auth.py` — TokenStore (reused)
