# AGENTS.md — Trading Platform v3 (FXCM Connector)

> **Purpose**: This document provides essential context for AI coding agents working on this project.  
> **Language**: Ukrainian (primary), English for technical terms.  
> **Last Updated**: 2026-02-28

---

## 1. Project Overview

**Trading Platform v3** — це торгова платформа "дані → аналітика/SMC → UI → торгова взаємодія" з жорсткими інваріантами та **UnifiedDataStore (UDS)** як єдиним write-center.

### 1.1 Архітектура A → C → B

| Шар | Що | Де |
|---|---|---|
| **A** Broker + ingest | FXCM History + tick stream → 5 writer-процесів | `runtime/ingest/`, `app/` |
| **C** UDS | SSOT disk + Redis cache + updates bus | `runtime/store/uds.py` |
| **B** UI (http) | read-only HTTP polling renderer, same-origin, порт 8089 | `ui_chart_v3/` *(private module)* |
| **B** UI (ws) | read-only WS real-time renderer, same-origin, порт 8000 | `ui_v4/` + `runtime/ws/ws_server.py` |
| **TUI** | aione-top: інтерактивний TUI-монітор процесів/pipeline | `aione_top/` |

---

## 1.1 Актуальний статус (2026-02-28)

- Потік B (мульти-символьна активація) — **відкладено** через integrity derived TF (див. ADR-0025)
- Всі результати audit/rebuild зафіксовані в [docs/adr/0025-potik-b-data-quality-summary.md](docs/adr/0025-potik-b-data-quality-summary.md)
- Потік B закритий, фокус на XAU/USD та SMC engine (Потік C)
- Для майбутньої активації інших символів потрібен окремий audit/fix integrity derived TF

### 1.2 Технологічний стек

**Backend (Python):**

- Python 3.7 (strict requirement для forexconnect SDK)
- Redis 5.0.1 (pub/sub, snapshots, updates bus)
- numpy 1.21.6, pandas 1.1.5
- forexconnect 1.6.43 (FXCM SDK)
- aiohttp ≥3.8 (для UI HTTP API)

**Frontend UI v4:**

- Svelte 5 (runes mode) + Vite 6 + TypeScript 5.7
- lightweight-charts 5.0.0 (LWC)
- WebSocket transport

**Інфраструктура:**

- Redis (db=1, namespace `v3_local`)
- JSONL файли як SSOT (`data_v3/{symbol}/tf_{tf_s}/`)

---

## 2. Project Structure

```
v3/
├── app/                    # Supervisor та lifecycle
│   ├── main.py            # Головний supervisor (S2 restart backoff)
│   ├── main_connector.py  # D1 fetcher (disabled, ADR-0023: broker_base_tfs_s=[])
│   ├── composition.py     # DI composition
│   └── lifecycle.py       # Process lifecycle
│
├── core/                   # Pure logic (NO I/O)
│   ├── model/bars.py      # CandleBar dataclass, FINAL_SOURCES
│   ├── derive.py          # DERIVE_CHAIN, GenericBuffer, aggregate_bars
│   ├── buckets.py         # Time bucket math
│   └── config_loader.py   # Config parsing helpers
│
├── runtime/               # I/O та процеси
│   ├── ingest/            # Data ingestion
│   │   ├── broker/fxcm/   # FXCM provider
│   │   ├── polling/       # m1_poller, dedup, fetch_policy
│   │   ├── tick_*.py      # Tick stream + preview worker
│   │   ├── derive_engine.py  # Derive cascade (M1→H4+D1, ADR-0023)
│   │   └── market_calendar.py
│   ├── store/             # UDS та storage layers
│   │   ├── uds.py         # UnifiedDataStore (SSOT)
│   │   ├── layers/        # disk_layer, ram_layer, redis_layer
│   │   ├── redis_snapshot.py, redis_keys.py, redis_spec.py
│   │   └── ssot_jsonl.py  # JSONL append-only writer
│   ├── ws/                # WebSocket server (ui_v4)
│   │   └── ws_server.py   # Port 8000
│   └── obs_60s.py         # 60s observability
│
├── ui_chart_v3/           # HTTP UI (port 8089, polling)
│   └── server.py          # Flask-like HTTP API
│
├── ui_v4/                 # WebSocket UI (port 8000, Svelte 5)
│   ├── src/               # TypeScript + Svelte
│   ├── package.json       # npm deps
│   └── README_DEV.md      # UI v4 dev guide
│
├── aione_top/             # TUI монітор процесів
│   └── app.py             # `python -m aione_top`
│
├── tools/                 # Утиліти та діагностика (isolated)
│   ├── run_exit_gates.py  # Quality gates runner
│   ├── exit_gates/        # AST gates (dependency check)
│   ├── rebuild_*.py       # Data rebuild tools
│   └── audit/             # Audit scripts
│
├── tests/                 # 29+ тестів
│   ├── test_derive_*.py   # Derivation tests
│   ├── test_uds_*.py      # UDS tests
│   ├── test_s*_*.py       # SSOT compliance tests
│   └── test_ws_server.py  # WS tests
│
├── docs/                  # Повна документація
│   ├── index.md           # Точка входу
│   ├── system_current_overview.md  # Архітектура
│   ├── adr/               # 22 ADR (architecture decisions)
│   ├── contracts.md       # JSON Schema contracts
│   ├── ui_api.md          # HTTP API reference
│   └── runbooks/          # Production runbooks
│
├── config.json            # SSOT конфігурація
├── pyproject.toml         # Python package metadata
├── requirements.txt       # Runtime deps
└── data_v3/               # SSOT JSONL data storage
```

---

## 3. Build and Run Commands

### 3.1 Initial Setup

```bash
# 1. Python 3.7 venv + dependencies
pip install -r requirements.txt

# 2. Secrets (.env — лише FXCM credentials)
cp .env.example .env  # відредагуй FXCM_USER / FXCM_PASS / FXCM_URL

# 3. UI v4 build (опціонально, для WebSocket UI)
cd ui_v4
npm install
npm run build
cd ..
```

### 3.2 Run Commands

```bash
# Запуск усіх процесів (supervisor mode)
python -m app.main --mode all --stdio pipe

# Окремі режими
python -m app.main --mode connector      # D1 fetcher (disabled, ADR-0023)
python -m app.main --mode m1_poller      # M1 poller + derive engine
python -m app.main --mode tick_publisher # Tick stream
python -m app.main --mode tick_preview   # Preview worker
python -m app.main --mode ui             # HTTP UI (port 8089)
python -m app.main --mode ws_server      # WS UI (port 8000)

# TUI монітор (окремо)
python -m aione_top
```

### 3.3 Health Checks

```bash
# HTTP API status
curl http://127.0.0.1:8089/api/status

# UI v3 (HTTP polling)
open http://127.0.0.1:8089/

# UI v4 (WebSocket real-time) — requires ws_server.enabled=true
open http://127.0.0.1:8000/
```

---

## 4. Testing Strategy

### 4.1 Quality Gates (MUST PASS)

```bash
# Головні ворота якості — FAIL = NO-GO
python -m tools.run_exit_gates --manifest tools/exit_gates/manifest.json
```

### 4.2 Test Suite

```bash
# Run all tests
python -m pytest tests/ -v

# Специфічні категорії
python -m pytest tests/test_derive_*.py -v    # Derivation logic
python -m pytest tests/test_uds_*.py -v       # UDS compliance
python -m pytest tests/test_s*_*.py -v        # SSOT invariants
```

### 4.3 Key Test Files

| Тест | Що перевіряє |
|---|---|
| `test_derive_calendar_pause_partial.py` | Каскадна деривація з calendar pause |
| `test_uds_commit_split_brain.py` | Split-brain resilience |
| `test_uds_partial_penalty.py` | Partial bar penalty logic |
| `test_s1_preview_ttl_ssot.py` | Preview TTL compliance (ADR-0001) |
| `test_s3_final_sources_ssot.py` | FINAL_SOURCES integrity |
| `test_ws_server.py` | WebSocket server functionality |

---

## 5. Code Style Guidelines

### 5.1 Dependency Rule (I0) — CRITICAL

```text
┌─────────────────────────────────────────────────────────────┐
│  core/        pure-логіка (час, контракти, моделі)          │
│               НЕ імпортує: runtime/, ui/, tools/            │
│               НЕ має I/O: файли, мережа, Redis, FXCM        │
├─────────────────────────────────────────────────────────────┤
│  runtime/     I/O та процеси (ingest, store, pub/sub)       │
│               Імпортує: core/                               │
│               НЕ імпортує: tools/, ui/                      │
├─────────────────────────────────────────────────────────────┤
│  ui_chart_v3/ презентація + HTTP API                        │
│               Імпортує: core/, runtime/ (ReadPolicy, UDS)   │
│               НЕ містить доменної логіки                    │
├─────────────────────────────────────────────────────────────┤
│  app/         запуск, supervisor, lifecycle                 │
│               Імпортує: core/, runtime/                     │
├─────────────────────────────────────────────────────────────┤
│  tools/       одноразові утиліти/діагностика/міграції       │
│               Імпортує: core/ (дозволено)                   │
│               НЕ імпортується з runtime/ui/app              │
└─────────────────────────────────────────────────────────────┘
```

**Enforcement**: `tools/exit_gates/gates/` містить AST gate для перевірки.

### 5.2 Naming Conventions

- **Python**: `snake_case` для функцій/змінних, `PascalCase` для класів
- **Time fields**: `open_time_ms`, `close_time_ms` (epoch milliseconds)
- **Redis keys**: `{namespace}:ohlcv:snap:{sym}:{tf_s}`, `{NS}:preview:curr:{sym}:{tf_s}`
- **Config keys**: `snake_case` в JSON

### 5.3 Time Geometry (Dual Convention) — CRITICAL

| Шар | Поле | Семантика | Формула |
|---|---|---|---|
| CandleBar / SSOT JSONL / HTTP API | `close_time_ms` | **end-excl** | `open_time_ms + tf_s * 1000` |
| Redis (ohlcv / preview:curr / preview:tail) | `close_ms` | **end-incl** | `open_ms + tf_s * 1000 - 1` |

- Конвертація end-excl → end-incl відбувається **тільки** на межі Redis write
- При читанні з Redis, UDS перераховує `close_ms = open_ms + tf_s*1000`

### 5.4 Logging Format

```python
# Structured logs з префіксами
logging.info("EVENT_NAME key1=%s key2=%d", value1, value2)
logging.warning("DEGRADED_REASON symbol=%s tf=%s", sym, tf)
```

Префікси:

- `SUPERVISOR_*` — supervisor events
- `PRIME_READY_*` — cold start readiness
- `UDS_*` — UDS operations
- `DERIVE_*` — derive engine

---

## 6. Key Invariants (I0–I6)

| ID | Інваріант | Enforcement |
|---|---|---|
| **I0** | **Dependency Rule**: core/ ← runtime/ ← ui/ | Exit-gate AST check |
| **I1** | **UDS як вузька талія**: всі writes через UDS; UI = read-only | Runtime guard `_ensure_writer_role()` |
| **I2** | **Єдина геометрія часу**: end-excl (SSOT/API), end-incl (Redis), конвертація на межі Redis | `assert_invariants()` |
| **I3** | **Final > Preview (NoMix)**: `complete=true` завжди перемагає `complete=false` | Watermark + NoMix tracking |
| **I4** | **Один update-потік для UI**: `/api/updates` (upsert events) | Contract-first API |
| **I5** | **Degraded-but-loud**: fallback → `warnings[]`/`meta.extensions`, не silent | `_contract_guard_warn_*` |
| **I6** | **Disk hot-path ban**: disk не для polling; лише bootstrap/warmup/scrollback | `_disk_allowed()` guard |

---

## 7. Configuration

### 7.1 Key Config Files

| Файл | Призначення |
|---|---|
| `config.json` | SSOT policy (symbols, TFs, calendar, Redis) |
| `.env` | Secrets only (FXCM credentials) |
| `env_profile.py` | Env loading logic |

### 7.2 Critical Config Sections

```json
{
  "symbols": ["XAU/USD", "EUR/USD", ...],  // 13 символів
  "tf_allowlist_s": [60, 180, 300, 900, 1800, 3600, 14400, 86400],
  "redis": { "enabled": true, "host": "127.0.0.1", "port": 6379, "db": 1 },
  "ws_server": { "enabled": true, "host": "127.0.0.1", "port": 8000 },
  "bootstrap": { "prime_ready_timeout_s": 120, ... }
}
```

### 7.3 Environment Variables

```bash
# FXCM credentials (required)
FXCM_USER=your_user
FXCM_PASS=your_pass
FXCM_URL=https://www.fxcorporate.com/Hosts.jsp

# Redis override (optional)
REDIS_HOST=127.0.0.1
REDIS_PORT=6379
```

---

## 8. Development Workflow

### 8.1 Three Modes of Operation

| Режим | Коли | Що дозволено |
|-------|------|-------|
| `MODE=DISCOVERY` | Будь-який аналіз. За замовчуванням якщо не впевнений. | FACTS з `path:line` + FAILURE MODEL + GAP ANALYSIS |
| `MODE=PATCH` | Після DISCOVERY, якщо інваріанти не порушено. | Мінімальний диф ≤150 LOC, 1 файл, verify, ADR-reference |
| `MODE=ADR` | Якщо зміна торкається інваріантів, формату, протоколу. | Документ `docs/adr/NNNN-<назва>.md` з повним обґрунтуванням |
| `MODE=BUILD` | Нова підсистема з approved ADR. | Types+contracts FIRST → Pure logic з tests → Integration glue → UI wiring. Files >1 дозволено, LOC >150 дозволено якщо новий модуль з тестами. Послідовність P-slices з ADR. |

### 8.2 Stop-Rules

Зупинитись і **не додавати нові фічі**, якщо:

- Порушені інваріанти I0–I6
- З'явився split-brain (два паралельні джерела істини)
- З'явився silent fallback
- Зміна торкається контрактів/даних без плану міграції

### 8.3 ADR Process

Всі архітектурні рішення документуються в `docs/adr/`:

- ADR-0001: UDS як єдина талія
- ADR-0002: DeriveChain M1→H4+D1
- ADR-0023: D1 Live Derive from M1 (D1 = 1440×M1, anchor 79200)
- ADR-0003: Cold start hardening
- ... (22 ADR total)

---

## 9. Security Considerations

### 9.1 Secrets Management

- **Ніколи** не коміть `.env` — він у `.gitignore`
- Використовуй `.env.example` як шаблон
- FXCM credentials завантажуються через `env_profile.load_env_secrets()`

### 9.2 Data Protection

- `data_v3/` — локальне сховище JSONL (чутливі дані)
- `logs/` — логи процесів (можуть містити sensitive data)
- Redis db=1 — не production shared instance

### 9.3 Network Security

- UI HTTP: `127.0.0.1:8089` (localhost only)
- UI WS: `127.0.0.1:8000` (localhost only)
- FXCM: HTTPS з автентифікацією
- Redis: localhost only (за замовчуванням)

---

## 10. Troubleshooting

### 10.1 Common Issues

| Проблема | Діагностика | Рішення |
|---|---|---|
| `prime_pending` | `curl /api/status` | Чекати cold start завершення |
| `prime_broken` | Логи `connector`/`m1_poller` | Перевірити FXCM credentials |
| `redis_spec_mismatch` | UDS warnings | Перевірити Redis TTL конфігурацію |
| `insufficient_warmup` | UI показує порожній графік | Зачекати bootstrap або перезапустити |

### 10.2 Recovery Commands

```bash
# Перезбірка derived барів з M1
python -m tools.rebuild_from_m1 --symbol XAU/USD --tf 300

# Deduplication M1/M3
python -m tools.dedup_rebuild_m1m3 --symbol XAU/USD

# Purge broken bars
python -m tools.purge_broken_bars --symbol XAU/USD --tf 14400
```

### 10.3 Log Locations

```bash
# Supervisor mode (stdio=pipe) — логи в консолі
# Supervisor mode (stdio=files) — логи в:
ls logs/*.log

# Окремі процеси (якщо запущені вручну):
tail -f logs/connector.out.log
tail -f logs/m1_poller.out.log
```

---

## 11. Quick Reference

| Команда | Призначення |
|---|---|
| `python -m app.main --mode all --stdio pipe` | Запуск всіх процесів |
| `python -m tools.run_exit_gates` | Quality gates |
| `python -m pytest tests/ -v` | Run tests |
| `python -m aione_top` | TUI монітор |
| `curl http://127.0.0.1:8089/api/status` | Health check |

### Key Files to Know

| Файл | Чому важливий |
|---|---|
| `runtime/store/uds.py` | UDS — єдине місце запису |
| `core/derive.py` | DERIVE_CHAIN — логіка агрегації TF |
| `core/model/bars.py` | CandleBar, FINAL_SOURCES — модель даних |
| `config.json` | SSOT конфігурація |
| `docs/index.md` | Навігація по документації |

---

## 12. Contact & Resources

- **Документація**: `docs/index.md`
- **ADR Index**: `docs/adr/index.md`
- **Config Reference**: `docs/config_reference.md`
- **Production Runbook**: `docs/runbooks/production.md`
- **Changelog**: `CHANGELOG.md` / `changelog.jsonl`

## 13. Жорсткі заборони

**Z1 — Заборона на "загальну пораду".** ~~"Рекомендую додати тести"~~ → конкретно: який тест, що перевіряє, який assertion, де живе.

**Z2 — Заборона на "тимчасово так".** Тимчасове без тікета/дедлайну/гейта = навічно. Кожне `TODO` без expiry date = технічний борг, який ніколи не буде сплачений.

**Z3 — Заборона на "працює на моїй машині".** Відтворюваність = мінімум: команда + вхідні дані + очікуваний/фактичний результат.

**Z4 — Заборона на вигадані номери рядків.** Якщо ти не бачив код — пиши `[path:?]` а не вигадуй line number. Брехливий доказ гірше за відсутній.

**Z5 — Заборона на комплімент-обгортку.** ~~"Загалом непогано, але..."~~ Дефект не потребує вступного реверансу. Час обмежений — витрачай його на суть.

**Z6 — Заборона на рефакторинг як фікс.** Мінімальний фікс = мінімальний diff. "Давайте перепишемо модуль" — це не фікс, це initiative.

## 14. Поведінка в edge cases (що робити коли...)

**Коли автор каже "це by design":**
→ Покажи конкретний сценарій, де цей design ламається. Якщо не можеш — визнай.

**Коли не вистачає коду:**
→ Маркуй `[ASSUMED]`. Давай worst-case оцінку. Запропонуй конкретну команду/файл для перевірки.

**Коли все виглядає добре:**
→ Шукай глибше. Подумай: "якщо б я хотів зламати цю систему зсередини — що б я зробив?" Якщо після 30 хвилин не знайшов S0/S1 — ок, але знайди хоча б 5 S2/S3.

**Коли дефектів >30:**
→ Пріоритезуй безжалісно. Top-10 з доказами краще за 30 без.

---

## 15. Антипаттерни, які ти ніколи не робиш

- ❌ "Код чистий і добре структурований" — це не рев'ю, це ввічливість.
- ❌ "Рекомендую розглянути можливість..." — або конкретний баг, або нема.
- ❌ "В цілому добре, але є нюанси" — кожен "нюанс" має severity і ID.
- ❌ Перелік зауважень без severity/priority — це шум, не сигнал.
- ❌ Рефакторинг як фікс S0 — S0 фікситься мінімальним патчем СЬОГОДНІ.
- ❌ Хвалити за "правильні рішення" — тебе покликали не за цим.

---

## 16. Контракт з замовником

Ти гарантуєш:

1. Кожен дефект має evidence (код, лог, або `[ASSUMED]` з обґрунтуванням)
2. Severity не завищена (S0 = production data loss / corruption / crash, не "некрасиво")
3. Фікс-мінімум реально мінімальний (не "заодно перепишемо")
4. Якщо щось не перевірив — чесно скажеш що не перевірив
5. Відповідь можна використати як технічний тікет без переписування

Ти **не** гарантуєш:

- Що знайшов усе (100% coverage неможливий)
- Що автор буде задоволений (це не ціль)
- Що всі рекомендації варто робити зараз (є priority для цього)
