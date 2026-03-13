# ADR-0016: Python Version Upgrade + Broker Subprocess Isolation

- **Статус**: **Implemented** (2026-03-08)
- **Дата**: 2026-02-26 (initial), 2026-03-08 (rev 2 + accepted + implemented)
- **Автор**: R_ARCHITECT
- **Initiative**: `platform_modernization`
- **Пов'язані ADR**: ADR-0001 (UDS), ADR-0002 (DeriveChain), ADR-0023 (D1 from M1), ADR-0024 (SMC Engine)
- **LIC-07**: `docs/compliance/fxcm-sdk-license-review.md`

---

## 1. Контекст і проблема

Платформа жорстко пінена на Python 3.7 (`pyproject.toml:9`: `requires-python = ">=3.7,<3.8"`).
Python 3.7 досяг EOL у червні 2023 — це **2.5+ роки без security-патчів**.

Причина піну — FXCM SDK (`forexconnect==1.6.43`), який **не має wheel для Python 3.8+** і навряд чи отримає (FXCM не публікує оновлення SDK). LIC-07 підтверджує: SDK proprietary / EULA-governed, derivative works заборонені → самостійний rebuild wheel неможливий.

### 1.1 Що блокує Python 3.7

| Залежність | Обмеження |
|------------|-----------|
| `forexconnect==1.6.43` | Бінарний wheel тільки для Python 3.7 (Windows), proprietary |
| `numpy==1.21.6` | Остання версія з Python 3.7 support (EOL) |
| `pandas==1.1.5` | Остання версія з Python 3.7 support (EOL) |
| `setuptools==39.0.1` | Критично застарілий (CVE exposure) |

### 1.2 Що втрачаємо на Python 3.7

- **Security**: 0 CVE patches для stdlib з червня 2023
- **Performance**: CPython 3.11 = ~25% faster; 3.12+ = ще швидше (JIT у 3.13)
- **Features**: `match/case`, `asyncio.TaskGroup`, `Self`, `TypeVarTuple`, `ExceptionGroup`
- **Ecosystem**: Нові бібліотеки drop 3.7 support; aiohttp, redis-py будуть dropping
- **Contributors**: Python 3.7 = бар'єр для нових розробників

### 1.3 Поточна межа SDK (audit, 2026-03-08)

`forexconnect` імпортується **рівно у 2 файлах**:

| Файл | Що робить | Вхід | Вихід |
|------|-----------|------|-------|
| `runtime/ingest/broker/fxcm/provider.py:13` | `FxcmHistoryProvider` — history fetch | FXCM credentials + symbol + TF | `List[CandleBar]` (pure Python dataclass) |
| `runtime/ingest/tick_publisher_fxcm.py:21` | `FxcmTickPublisher` — live tick stream | FXCM credentials + symbols | JSON payload → Redis PubSub `v3_local:price_tick` |

**Ключова проблема `m1_poller`**:
`runtime/ingest/polling/m1_poller.py` імпортує `FxcmHistoryProvider` (line 1074)
**і** `UDS` (line 32) **в одному процесі**. Provider повертає
`List[CandleBar]`, poller одразу робить `uds.commit_final_bar(bar)` + каскадну
деривацію через `DeriveEngine.on_bar(bar)`. Це **tight coupling** в рамках
одного Python-процесу, яке потрібно розв'язати.

---

## 2. Обмеження (Constraints)

| # | Обмеження | Джерело |
|---|-----------|---------|
| C1 | FXCM SDK = Python 3.7 only, rebuild заборонений (LIC-07 EULA) | `docs/compliance/fxcm-sdk-license-review.md` |
| C2 | Інваріанти I0–I6 не порушуються | `.github/copilot-instructions.md` |
| C3 | Redis = єдиний IPC між процесами (вже зараз) | `app/main.py:147`, `tick_publisher → Redis PubSub` |
| C4 | UDS = вузька талія, всі writes тільки через UDS (I1) | `runtime/store/uds.py` |
| C5 | Latency budget: tick → UI ≤ existing p95; bar commit → derive cascade ≤ existing | SLO |
| C6 | `CandleBar` dataclass = wire format між broker і platform | `core/model/bars.py` |
| C7 | Platform target: Python 3.12+ (≥3.11, рекомендовано 3.12 для стабільності) | — |

---

## 3. Розглянуті альтернативи

### Альтернатива A: Повна міграція на Python 3.11+ (відхилена)

- **Суть**: Замінити `forexconnect` на REST API (FXCM пропонує REST/WS альтернативу) або обгорнути через ctypes/CFFI
- **Pros**: Один venv, чистий stack
- **Cons**: FXCM REST API повільніший для tick stream; ctypes wrapper = derivative work SDK (заборонено LIC-07 EULA); міграція може зайняти тижні + новий QA цикл для broker integration
- **Blast radius**: `provider.py`, `tick_publisher_fxcm.py`, можливо `m1_poller.py` (новий API contract)
- **Ризик**: **Високий**. Broker integration = критичний production path. Зміна broker API = ризик data loss
- **Висновок**: Відхилена. EULA блокує ctypes/CFFI. REST API потребує окремого ADR + тестування

### Альтернатива B: Dual-venv з broker subprocess ізоляцією (рекомендована)

- **Суть**: Broker-процеси (`tick_publisher`, `m1_poller`) працюють у `.venv37/` (Python 3.7). Все інше — у `.venv/` (Python 3.12+). IPC через Redis (вже є). `m1_poller` розділяється на broker-sidecar (fetch, 3.7) + platform-side ingestion (UDS+derive, 3.12+)
- **Pros**: Мінімальна зміна архітектури; Redis IPC вже production-proven; кожна сторона оновлюється незалежно; rollback = повернути єдиний .venv37
- **Cons**: Два venv = CI ускладнення; додатковий Redis hop для m1_poller (раніше in-process); невеликий overhead на серіалізацію
- **Blast radius**: `app/main.py` (supervisor spawn), `m1_poller.py` (split), нові файли: `requirements-broker.txt`, broker-sidecar entrypoint
- **LOC estimate**: ~200 LOC нового коду (sidecar + supervisor patching)
- **Ризик**: **Низький–Середній**

### Альтернатива C: Статус-кво (відхилена)

- **Суть**: Залишити все на Python 3.7
- **Pros**: Нуль зусиль
- **Cons**: Наростаючий security debt, ecosystem lock-in, contributor friction
- **Висновок**: Відхилена. Security exposure занадто високий для production trading platform

---

## 4. Рішення: Альтернатива B — Dual-Venv Broker Isolation

### 4.1 Архітектурна схема (TO-BE)

```
┌──────────────────────────────────────────────────────────────────────┐
│  .venv/ (Python 3.12+)                                               │
│                                                                      │
│  app/main.py (supervisor)                                            │
│    ├── spawn .venv37/python -m runtime.ingest.broker_sidecar         │
│    ├── spawn .venv37/python -m runtime.ingest.tick_publisher_fxcm    │
│    ├── spawn .venv/python  -m runtime.ingest.tick_preview_worker     │
│    ├── spawn .venv/python  -m runtime.ingest.m1_ingestion_worker     │
│    ├── spawn .venv/python  -m runtime.ws.ws_server                   │
│    └── spawn .venv/python  -m ui_chart_v3                            │
│                                                                      │
│  core/*, runtime/store/*, runtime/ws/*, runtime/smc/*,               │
│  runtime/ingest/derive_engine.py, runtime/ingest/tick_preview_worker │
│  ui_chart_v3/, ui_v4/, tools/, aione_top/, tests/                    │
└──────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────────┐
│  .venv37/ (Python 3.7 — LEGACY, broker тільки)                       │
│                                                                      │
│  runtime/ingest/tick_publisher_fxcm.py  (tick stream → Redis PubSub) │
│  runtime/ingest/broker_sidecar.py       (M1 fetch → Redis queue)     │
│  runtime/ingest/broker/fxcm/provider.py (FxcmHistoryProvider)        │
│                                                                      │
│  Залежності: forexconnect==1.6.43, redis, core.model.bars,           │
│              core.config_loader, env_profile                         │
└──────────────────────────────────────────────────────────────────────┘

                    ┌────────────────────────────┐
                    │  Redis db=1 (v3_local)     │
                    │                            │
                    │  price_tick (PubSub) ──────┼──→ tick_preview_worker
                    │  broker:m1:bars (List) ────┼──→ m1_ingestion_worker
                    │  updates (PubSub) ─────────┼──→ ws_server, ui
                    └────────────────────────────┘
```

### 4.2 Ключове рішення: розділення m1_poller

**AS-IS**: `m1_poller.py` = один процес (Py 3.7), який:

1. Логіниться в FXCM через `FxcmHistoryProvider`
2. Полить M1 бари (history fetch)
3. Коммітить у UDS (`uds.commit_final_bar()`)
4. Запускає каскадну деривацію (`derive_engine.on_bar()`)

**TO-BE**: два процеси, з'єднані через Redis:

| Процес | Python | Відповідальність | Межа |
|--------|--------|------------------|------|
| **broker_sidecar** (новий) | 3.7 | **Stateless fetcher**: отримує команду `{symbol, from_ms, n_bars}` з Redis → FXCM login + fetch → серіалізація `CandleBar` → `RPUSH v3_local:broker:m1:bars` | Пише JSON-серіалізовані бари у Redis list. **Не має polling state** — ні watermark, ні gap detection, ні scheduling |
| **m1_ingestion_worker** (новий, з існуючого m1_poller) | 3.12+ | **Scheduling + ingestion**: watermark tracking, gap detection, backfill logic, calendar awareness, flat bar policy. Надсилає fetch-команди sidecar через `RPUSH v3_local:broker:m1:cmd`. Читає бари з `BLPOP v3_local:broker:m1:bars` → `uds.commit_final_bar()` → `derive_engine.on_bar()` | Вся бізнес-логіка polling живе тут. Пише в UDS |

**Контракт на межі** (Redis):

Command queue (ingestion_worker → broker_sidecar): `v3_local:broker:m1:cmd`

```json
{"v": 1, "cmd": "fetch", "symbol": "XAU/USD", "from_ms": 1741392000000, "n_bars": 5}
```

Response queue (broker_sidecar → ingestion_worker): `v3_local:broker:m1:bars`

```json
{
  "v": 1,
  "symbol": "XAU/USD",
  "tf_s": 60,
  "open_time_ms": 1741392000000,
  "close_time_ms": 1741392060000,
  "o": 2915.43,
  "h": 2916.01,
  "low": 2915.12,
  "c": 2915.78,
  "v": 142,
  "complete": true,
  "src": "history"
}
```

### 4.3 tick_publisher — без змін архітектури

`tick_publisher_fxcm.py` вже повністю ізольований:

- Працює як окремий процес
- Пише лише в Redis PubSub `v3_local:price_tick`
- Читає лише `tick_preview_worker` (який вже не залежить від forexconnect)

**Зміна**: тільки шлях Python executable у supervisor: `.venv37/python` замість `sys.executable`.

### 4.4 Latency analysis

| Шлях | AS-IS | TO-BE | Delta |
|------|-------|-------|-------|
| `tick → Redis PubSub → preview_worker` | ~1ms (Redis PubSub) | ~1ms (без змін) | **0** |
| `FXCM fetch → UDS commit` (m1_poller) | ~0ms (in-process call) | ~1–3ms (RPUSH + BLPOP + JSON serde) | **+1–3ms** |
| `UDS commit → derive cascade` | ~0ms (in-process) | ~0ms (in-process, m1_ingestion_worker) | **0** |
| `derive → WS delta → UI` | ~5–15ms (Redis PubSub + WS) | ~5–15ms (без змін) | **0** |

**Висновок**: додатковий latency = **~1–3ms** тільки на шляху `FXCM history → UDS commit`. Це:

- Торкається лише M1 polling (інтервал ~8–10s між фетчами), не tick stream
- Не впливає на preview latency (tick path окремий)
- Не впливає на derive cascade latency (cascade залишається in-process у m1_ingestion_worker)
- **Неістотно** для trading platform з polling інтервалом 8s

### 4.5 Що залежить від core/ у broker venv

`broker_sidecar` і `tick_publisher_fxcm` імпортують з `core/`:

- `core.model.bars.CandleBar` — dataclass (pure Python, 0 залежностей від numpy/pandas)
- `core.config_loader` — config parsing (pure Python, залежить від `json`, `pathlib`)

**Рішення**: Наступні файли мають залишатися **Python 3.7-сумісними** (pure Python, без walrus `:=`, без `match/case`, без `type` statement):

- `core/model/bars.py` — CandleBar dataclass
- `core/config_loader.py` — config parsing
- `env_profile.py` — secrets loading

У `.venv37/` інсталювати проєкт або додати `PYTHONPATH` до workspace root.

**Enforcement**: Exit-gate P6 включає syntax check: `.venv37/python -c "import core.model.bars; import core.config_loader; import env_profile"` для гарантії 3.7-сумісності.

Альтернатива: дублювати `CandleBar` JSON schema в broker-sidecar (але це порушує SSOT → відхилено).

### 4.6 Supervisor зміни в app/main.py

```python
# AS-IS (app/main.py:147):
cmd = [sys.executable, "-u", "-m", module]

# TO-BE:
BROKER_MODULES = {
    "runtime.ingest.tick_publisher_fxcm",
    "runtime.ingest.broker_sidecar",
}

def _python_for(module: str) -> str:
    if module in BROKER_MODULES:
        broker_py = cfg.get("broker_python", ".venv37/Scripts/python.exe")
        if os.path.isfile(broker_py):
            return broker_py
        logging.warning("BROKER_PYTHON_NOT_FOUND path=%s fallback=sys.executable", broker_py)
    return sys.executable

cmd = [_python_for(module), "-u", "-m", module]
```

**Config SSOT**: `config.json` отримує нове поле:

```json
{
  "broker_python": ".venv37/Scripts/python.exe"
}
```

---

## 5. P-Slices (план реалізації)

Кожен slice ≤150 LOC, окремий verify, окремий rollback.

| Slice | Що | LOC | Verify | Rollback |
|-------|----|-----|--------|----------|
| **P1** | `requirements-broker.txt`: forexconnect + redis + мін. залежності. Створити `.venv37/`. | ~10 | `.venv37/python -c "import forexconnect"` | `rm -r .venv37/` |
| **P2** | `runtime/ingest/broker_sidecar.py`: FXCM fetch → RPUSH bars у Redis list | ~100 | Запуск sidecar, перевірка `LLEN v3_local:broker:m1:bars` | Видалити файл |
| **P3** | `runtime/ingest/m1_ingestion_worker.py`: BLPOP → UDS commit → derive | ~120 | Запуск worker, verify bars у UDS, derive cascade працює | Повернути m1_poller.py |
| **P4** | `app/main.py`: supervisor spawn broker modules з `.venv37/python`, `config.json: broker_python` | ~30 | `--mode all` з dual venv, verify all processes alive | Прибрати `BROKER_MODULES` dict |
| **P5** | `pyproject.toml` + `requirements.txt`: `requires-python >= "3.11"`, оновити numpy/pandas/aiohttp | ~15 | `pip install -r requirements.txt` у 3.12 venv, `pytest tests/` | Повернути old requirements |
| **P6** | Exit-gate: `tools/exit_gates/gates/gate_dual_python.py` — verify broker=3.7, main=3.12+ | ~50 | `python -m tools.run_exit_gates` | Видалити gate |
| **P7** | Docs update: AGENTS.md, README.md, system_current_overview.md | ~30 | Manual review | git revert |

**Загальний бюджет**: ~355 LOC нового коду + ~15 LOC config/requirements.

### 5.1 Порядок залежностей

```
P1 (.venv37/) → P2 (broker_sidecar) → P3 (m1_ingestion_worker) → P4 (supervisor)
                                                                      ↓
                                                                   P5 (requirements upgrade)
                                                                      ↓
                                                                   P6 (exit-gate)
                                                                      ↓
                                                                   P7 (docs)
```

P2 і P3 можна розробляти паралельно (mock Redis для тестів).
P5 тільки після P4 verify (все працює з dual venv).

---

## 6. Failure Model

| # | Сценарій | Що станеться | Mitigation |
|---|----------|-------------|------------|
| F1 | `.venv37/` не знайдено | Supervisor WARNING + fallback на `sys.executable` | Config SSOT `broker_python` + degraded-but-loud (I5) |
| F2 | broker_sidecar впав (FXCM disconnect) | Redis list порожній → m1_ingestion_worker чекає (BLPOP) | Existing reconnect logic в FxcmHistoryProvider + supervisor restart |
| F3 | m1_ingestion_worker впав | Бари накопичуються у Redis list (bounded TTL) | Supervisor restart; list TTL guard |
| F4 | Redis list overflow (broker пише, worker не читає) | Memory pressure | `LTRIM` після кожного RPUSH з max_length = 10000 бар (safety rail). Якщо bars dropped → `degraded-but-loud` log + metric `broker_bars_dropped_total{reason=overflow}` (I5) |
| F5 | JSON serde incompatibility (3.7 writes, 3.12 reads) | Десеріалізація fail | Контракт v1 = стандартний JSON (float/int/str). Тест P2+P3 verify |
| F6 | `core/model/bars.py` ламається на 3.7 після оновлення | broker_sidecar crash | Lint gate: `core/model/bars.py` + `core/config_loader.py` = 3.7-compatible |
| F7 | numpy/pandas upgrade ламає derive logic | Числові відмінності | Existing test suite (440+ tests) запускається після P5 |

---

## 7. Інваріанти: перевірка

| Інваріант | Порушується? | Обґрунтування |
|-----------|-------------|---------------|
| **I0** Dependency Rule | ❌ Ні | core/ залишається pure; broker_sidecar живе в runtime/ingest/ |
| **I1** UDS = вузька талія | ❌ Ні | broker_sidecar НЕ пише в UDS. Тільки m1_ingestion_worker (через UDS API). Те саме що зараз |
| **I2** Геометрія часу | ❌ Ні | CandleBar формат не змінюється. JSON serde зберігає точність epoch ms (int) |
| **I3** Final > Preview | ❌ Ні | Контракт не змінюється. broker_sidecar передає complete=true бари |
| **I4** Один update-потік | ❌ Ні | `/api/updates` залишається єдиним каналом для UI |
| **I5** Degraded-but-loud | ✅ Підсилюється | Новий fallback path (F1) = explicit WARNING + degraded[] |
| **I6** Stop-rule | ❌ Не тригериться | Жоден інваріант I0–I5 не порушений |
| **S0–S6** SMC | ❌ Ні | SMC Engine залишається в main venv (3.12+), zero contact з broker |

---

## 8. Наслідки

### 8.1 Файлова структура (зміни)

| Файл | Зміна |
|------|-------|
| `requirements.txt` | `requires numpy>=1.26, pandas>=2.1, aiohttp>=3.9`, без forexconnect |
| `requirements-broker.txt` | **Новий**: forexconnect==1.6.43, redis, python-dotenv |
| `pyproject.toml` | `requires-python = ">=3.11"` |
| `config.json` | `+ "broker_python": ".venv37/Scripts/python.exe"` |
| `app/main.py` | `_python_for(module)` routing |
| `runtime/ingest/broker_sidecar.py` | **Новий**: FXCM fetch → RPUSH |
| `runtime/ingest/m1_ingestion_worker.py` | **Новий**: BLPOP → UDS + derive (refactor з m1_poller) |
| `runtime/ingest/polling/m1_poller.py` | Залишається як legacy (backward compat), deprecated |
| `tools/exit_gates/gates/gate_dual_python.py` | **Новий**: verify dual venv |

### 8.2 Що НЕ змінюється

- `core/` — весь шар (pure logic, залишається 3.7-compatible для broker venv)
- `runtime/store/uds.py` — API не змінюється
- `runtime/ws/ws_server.py` — без змін
- `ui_chart_v3/`, `ui_v4/` — без змін
- Redis key structure — без змін
- Wire format (CandleBar JSON, tick payload) — без змін
- SMC Engine — без змін, живе в main venv

---

## 9. Майбутні можливості (не в scope цього ADR)

- **Python 3.14+**: Після стабілізації (жовтень 2025 release) → міграція main venv 3.12→3.14
- **FXCM REST API**: Окремий ADR якщо FXCM SDK стане повністю непрацездатним
- **Broker abstraction**: `runtime/ingest/broker/` → interface для підключення інших брокерів (Binance, IBKR)
- **Sub-interpreter (PEP 734)**: Python 3.13+ дозволяє sub-interpreters без GIL. Теоретично можна замінити subprocess на sub-interpreter з Py3.7 wheel — але на практиці це експериментально

---

## 10. Rollback

| Крок | Дія |
|------|-----|
| 1 | Повернути `pyproject.toml`: `requires-python = ">=3.7,<3.8"` |
| 2 | Повернути `requirements.txt` зі старими версіями numpy/pandas + forexconnect |
| 3 | Видалити `requirements-broker.txt` |
| 4 | Відновити `app/main.py` spawn без `_python_for()` routing |
| 5 | Видалити `broker_sidecar.py`, `m1_ingestion_worker.py` |
| 6 | Повернути `.venv/` на Python 3.7; видалити `.venv37/` |
| 7 | `git revert` всіх commits з initiative `platform_modernization` |

---

## 11. Open Questions

| # | Питання | Хто відповідає |
|---|---------|----------------|
| Q1 | Чи потрібен `PYTHONPATH` для core/ у `.venv37/`, чи `pip install -e .` з мінім. pyproject? | R_PATCH_MASTER (Phase P1) |
| Q2 | Windows-specific шляхи `.venv37/Scripts/python.exe` vs Linux `.venv37/bin/python` | R_CHART_UX (CI/DevOps) |
| Q3 | Чи варто тримати `m1_poller.py` як fallback (single-venv mode) чи deprecate повністю? | R_ARCHITECT (після P4 verify) |
| Q4 | Redis list TTL/max-length для `broker:m1:bars` — конкретні числа | R_PATCH_MASTER (Phase P3) |

---

## Appendix A: Поточна карта імпортів (forexconnect boundary)

```
forexconnect (PyPI wheel, Py3.7 only)
  └── runtime/ingest/broker/fxcm/provider.py:13  → FxcmHistoryProvider
  └── runtime/ingest/tick_publisher_fxcm.py:21   → FxcmTickPublisher

FxcmHistoryProvider
  └── runtime/ingest/polling/m1_poller.py:1074   → M1SymbolPoller._fetch()
        └── uds.commit_final_bar(bar)            → UDS (I1)
        └── derive_engine.on_bar(bar)            → DeriveEngine cascade

FxcmTickPublisher
  └── Redis PubSub "v3_local:price_tick"         → tick_preview_worker (окремий процес)
```

## Appendix B: Dual-venv dependency split

```
.venv37/ (Python 3.7)              .venv/ (Python 3.12+)
─────────────────────              ──────────────────────
forexconnect==1.6.43               numpy>=1.26
redis>=4.0                         pandas>=2.1
python-dotenv>=0.21                aiohttp>=3.9
                                   redis>=5.0
core/model/bars.py (PYTHONPATH)    python-dotenv>=0.21
core/config_loader.py              svelte (ui_v4 build)
env_profile.py                     lightweight-charts
                                   + all core/, runtime/, tools/
```

---

## Appendix C: Python 3.14 Migration Incident (2026-03-12)

> **Мета цього appendix**: зафіксувати два інциденти, спричинені стрибком main venv на Python 3.14.2,
> щоб при будь-якій наступній міграції Python-версії (або зміні venv) ця секція
> слугувала обов'язковим чеклістом. Scope = виключно dual-venv і наслідки зміни
> Python-версії.

---

### C.1 Контекст

ADR-0016 §4 рекомендував `Python ≥3.11` для main venv, з позначкою "рекомендовано 3.12 для стабільності". Фактична міграція (2026-03-12) була виконана на **Python 3.14.2** (free-threaded build, Windows x64) — це перевищило рекомендацію на дві мажорних версії без окремого аудиту.

Broker venv `.venv37/` (Python 3.7) залишився без змін.

### C.2 Інцидент 1: Venv Trampoline → Orphan Workers → Data Corruption

#### Що сталось

Python 3.14 на Windows використовує **trampoline launcher**: `.venv/Scripts/python.exe` — не реальний інтерпретатор, а stub, який запускає `C:\Python314\python.exe` як дочірній процес. Кожен `subprocess.Popen()` у supervisor створює два процеси:

```
supervisor (PID 1000)
  └── trampoline (PID 2000)  ← Popen.pid повертає цей PID
        └── real worker (PID 3000)  ← реальний m1_ingestion_worker
```

`proc.terminate()` вбивала **тільки трамплін** (PID 2000). Реальний worker (PID 3000) ставав orphan і продовжував працювати.

#### Каскад наслідків

1. Supervisor бачив що процес "мертвий" (трамплін завершився) → S2 restart → новий worker
2. Два `m1_ingestion_worker` конкурували за `BLPOP v3_local:broker:m1:bars`
3. Кожен отримував лише частину барів → corrupt M1 (зламаний stitching, низький volume)
4. `BROKER_PROXY_TIMEOUT` через конкуренцію → stuck `M1_LIVE_RECOVER` loop (20000+ секунд)
5. 749 corrupt M1 барів → derived TFs (M3–H4, D1) успадкували корупцію

#### Як фіксили

| Фікс | Файл | Суть |
|------|------|------|
| `_kill_tree(pid)` | `app/main.py:412` | Замість `proc.terminate()` — `taskkill /F /T /PID` на Windows (вбиває entire tree, включно з children трампліну). На Linux — `os.killpg()`. |
| `_terminate()` | `app/main.py:445` | Всі виклики supervisor'а тепер використовують `_kill_tree()` замість `proc.terminate()` |
| PID-file lock | `app/main.py:540` | `logs/supervisor.pid` з перевіркою liveness через `tasklist` — запобігає подвійному supervisor |
| Data repair | `tools/rebuild_from_m1.py` | Purge 749 corrupt M1, re-fetch з FXCM, rebuild derived TFs. Також виправлено баг Stage 1: derive per upsert → derive after full buffer load |

#### Чому саме так, а не інакше

- **`taskkill /T`** (tree kill) — єдиний спосіб на Windows вбити і трамплін, і реального worker. `proc.terminate()` / `TerminateProcess` вбиває лише вказаний PID. Альтернатива — трекати child PID через `psutil` або WMI — надлишкова залежність для одного edge case.
- **PID-file lock** — не блокує concurrency повністю (race window мілісекунди), але достатній для захисту від `python -m app.main` запущеного двічі руками.
- **`proc.poll()` продовжує працювати коректно**: трамплін чекає real worker, тому `poll()` повертає `None` поки обидва живі. Supervisor правильно бачить "running" стан.

### C.3 Інцидент 2: Type Checker Drift (Pyright + Python 3.14 stubs)

#### Що сталось

Pyright (VS Code) з Python 3.14 target використовує **оновлені stdlib type stubs** з більш точним inference. Код, який проходив type check на 3.7/3.11, почав видавати 7 помилок:

| # | Файл | Суть помилки |
|---|------|-------------|
| 1 | `tools/rebuild_from_m1.py` | `Optional[int]` присвоєно змінній з type `int` без guard |
| 2 | `core/smc/engine.py` | `Dict[str, list]` оголошено, але фактично записується `deque` |
| 3 | `core/smc/engine.py` | Type comment посилається на `SmcDisplayConfig`, але import відсутній |
| 4 | `core/smc/config.py` | `_SUB: dict[str, type]` — `type` не має `.from_dict()`, потрібен explicit callable |
| 5–6 | `core/smc/narrative.py` | `direction: str \| None` використовується як ключ `tuple[str,str,str]` dict — потрібен guard |
| 7 | `core/smc/narrative.py` | `Optional` в list comprehension — Pyright 3.14 не звужує тип через `[a, b]` ітерацію |

#### Чому це сталось саме на 3.14

- Python 3.14 type stubs (`typeshed`) ширше анотують `collections`, `builtins`, generics. Pyright inference engine стає суворішим при виведенні типів через assignment patterns.
- На 3.11/3.12 ці ж stubs давали `Any` або ширший union — помилки мовчали.
- Жоден з 7 дефектів **не був runtime failure**. Всі — type-level drift (код працював коректно, але типові анотації не відповідали).

#### Як фіксили

Мін-diff патчі в кожному файлі:

- `Dict[str, list]` → `Dict[str, deque]` (відповідає фактичному використанню)
- Додано missing import `SmcDisplayConfig`
- `_SUB` отримав explicit callable typing замість bare `type`
- `direction` guard: `if direction is None: continue` перед використанням як dict key
- `Optional` narrowing: explicit `if z is not None` замість ітерації через `[a, b]`

Усі 136 SMC-тестів пройшли після фіксу. Zero runtime behavior change.

### C.4 Обов'язковий чеклист при міграції Python-версії

> **Цей чеклист — SSOT для всіх майбутніх міграцій main venv.**
> Пропуск будь-якого пункту = ризик повторення інциденту.

| # | Крок | Чому |
|---|------|------|
| 1 | **Не перескакувати**: якщо ADR рекомендує 3.12, мігрувати на 3.12, не на 3.14. Стрибок >1 мажорна → окремий ADR | Кожна мажорна версія змінює stdlib stubs, venv механіку, GIL семантику |
| 2 | **Перевірити venv launcher**: на Windows запустити `.venv/Scripts/python.exe`, порівняти PID з `os.getpid()` всередині. Якщо відрізняються — trampoline активний | Trampoline ламає `proc.terminate()`, `proc.pid`, `os.kill()` |
| 3 | **Перевірити `_kill_tree()`**: supervisor має вбивати tree, не одиничний PID | Без tree kill — orphan workers при restart |
| 4 | **Запустити type checker на весь `core/` + `runtime/`**: `get_errors()` або `pyright --project .` | Нові stubs можуть зламати inference навіть без зміни коду |
| 5 | **Запустити повний test suite**: `pytest tests/ -v` | Зміни в stdlib (sort stability, dict ordering, float repr) можуть зламати edge cases |
| 6 | **Перевірити broker venv сумісність**: `.venv37/python -c "import core.model.bars"` | `core/model/bars.py` має залишитися 3.7-сумісним (§4.5) |
| 7 | **Smoke test supervisor**: `--mode all`, перевірити що кожен worker має 1 процес (не trampoline pair) | Рання детекція duplicate-worker проблеми |
| 8 | **Data integrity spot-check**: порівняти M1 stitching (`open[i+1] == close[i]`) для останніх 100 барів | Якщо duplicate workers вже стартували — дані можуть бути corrupt |

### C.5 Поточний стан (після фіксів)

- Main venv: **Python 3.14.2** (працює з `_kill_tree` rail)
- Broker venv: **Python 3.7** (без змін)
- `_kill_tree()` + PID-file lock = захист від trampoline-інциденту
- 7 type errors виправлені, 0 diagnostics errors у `core/` + `runtime/`
- Data integrity відновлена: 749 corrupt M1 re-fetched, derived TFs rebuilt
- Рекомендація ADR-0016 "3.12" → де-факто 3.14 (працює, але стрибок задокументований як ризик)
