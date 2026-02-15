# Trading Platform v3 (FXCM Connector + UDS + UI)

Торгова платформа “дані → аналітика/SMC → UI → торгова взаємодія” з жорсткими інваріантами даних та **єдиним write-center**: UnifiedDataStore (UDS).

## Документи-опори (SSOT для опису системи)

- [docs/system_current_overview.md](docs/system_current_overview.md) — поточна схема, процеси, плейни, UI pipeline.
- [research/ПОВНИЙ АУДИТ AS-IS + TO-BE ADR + ПЛАН.md](research/%D0%9F%D0%9E%D0%92%D0%9D%D0%98%D0%99%20%D0%90%D0%A3%D0%94%D0%98%D0%A2%20AS-IS%20%2B%20TO-BE%20ADR%20%2B%20%D0%9F%D0%9B%D0%90%D0%9D.md) — історія/ризики/GO-NO-GO та execution plan.
- [docs/config_reference.md](docs/config_reference.md) — довідник полів config.json.
- [docs/ADR-0001 UnifiedDataStore.md](docs/ADR-0001%20UnifiedDataStore.md) — ADR по UDS.
- [docs/redis_snapshot_design.md](docs/redis_snapshot_design.md) — Redis snapshot/tail дизайн і failure-modes.

## Канон A→C→B (не розмивається)

- **A (Broker + ingest/writers)**: FXCM History + tick stream → writer процеси.
- **C (UDS)**: єдина точка запису/читання marketdata (SSOT disk + Redis snapshots + updates bus) з rails/guards.
- **B (UI)**: read-only renderer; читає лише через HTTP API, без прямого доступу до диску/Redis.

## Data planes / SSOT-площини

Система має три ізольовані SSOT-площини (див. [docs/system_current_overview.md](docs/system_current_overview.md)):

- **SSOT-1 (M1/M3)**: M1 final з FXCM History (m1_poller), M3 derived = 3×M1. Preview-plane: tick stream → TickPreviewWorker → Redis preview keyspace.
- **SSOT-2 (M5+)**: engine_b polling (60s). Derived 15m/30m/H1 з M5.
- **SSOT-3 (H4/D1)**: прямий fetch з FXCM на close бакета.

UDS — центр: writers пишуть через UDS; UI читає через UDS.

## Вимоги

- Python 3.7 у .venv
- Runtime залежності: див. requirements.txt
- Локальна розробка: pyproject.toml (deps + metadata)
- ForexConnect SDK встановлюється окремо (vendor SDK / wheel), додав 1.6.43

- Для env-профілів потрібен python-dotenv (є в requirements.txt), змінено на 0.21,<1.0

## Структура даних

- data_v3/SYMBOL/tf_TF/part-YYYYMMDD.jsonl — основні дані
- History/ — довільна історія/експерименти (не для репо)

## Геометрія часу

- Canon: `close_time_ms = open_time_ms + tf_s*1000` (end-excl) для SSOT і API.
- UI, якщо потрібно, рахує end-incl локально: `close_incl_ms = close_time_ms - 1`.
- `event_ts` присутній лише для `complete=true` у вихідних payload-ах.

## Запуск системи

- Активуйте .venv.
- Запуск усіх 5 процесів (одна команда):
  - python -m app.main --mode all
- Лише конектор (M5+):
  - python -m app.main --mode connector
- Лише M1 poller (M1/M3):
  - python -m app.main --mode m1_poller
- Лише UI:
  - python -m app.main --mode ui
- Лише tick publisher:
  - python -m app.main --mode tick_publisher
- Лише tick preview worker:
  - python -m app.main --mode tick_preview

### Quickstart (dev)

1) Підготуйте Python 3.7 venv і залежності.

2) Налаштуйте секрети (FXCM) через `.env` або системні ENV змінні (див. нижче).

3) Запустіть все:

- `python -m app.main --mode all --stdio pipe`

1) Перевірте, що UI відповідає:

- `http://127.0.0.1:8089/api/status`
- `http://127.0.0.1:8089/`

## Процеси (as-is)

Supervisor (`python -m app.main --mode all`) керує процесами:

- `connector` — M5+ polling (engine_b) + derived + HTF fetch на close.
- `tick_publisher` — тик-стрім → Redis Pub/Sub.
- `tick_preview` — Pub/Sub → агрегація → UDS preview-plane (M1/M3).
- `m1_poller` — FXCM M1 History → UDS final (M1) + derive (M3).
- `ui` — HTTP server (8089): `/api/bars`, `/api/updates`, `/api/status`, `/api/config`.

## Інваріанти (операційні rails)

- **Часова геометрія (канон)**: `close_time_ms = open_time_ms + tf_s*1000` (end-excl) у SSOT і HTTP API.
- **Final > Preview**: `complete=true` завжди перемагає `complete=false` для ключа `(symbol, tf_s, open_time_ms)`.
- **Один update-потік для UI**: UI змінює свій стан лише з `/api/updates` (upsert events).
- **Disk hot-path ban**: disk не використовується в interactive read-path; дозволений лише bootstrap warmup window (UI прогріває RAM на старті, далі `disk_policy=never`).
- **Degraded-but-loud**: будь-яка деградація/фолбек → `warnings[]`/`meta.extensions` (без silent fallback).

## HTTP API (UI server, same-origin)

Базовий URL за замовчуванням: `http://127.0.0.1:8089`.

Endpoints:

- `GET /api/status` — статус UDS/UI, prime_ready, RAM/Redis метрики.
- `GET /api/config` — policy SSOT для UI (window_policy, allowlists, caps), `config_invalid` + warnings.
- `GET /api/bars?symbol=...&tf_s=...&limit=...` — вікно барів (window_v1).
- `GET /api/updates?symbol=...&tf_s=...&since_seq=...` — upsert events (updates_v1).

Публічні контракти (SSOT):

- `core/contracts/public/marketdata_v1/window_v1.json`
- `core/contracts/public/marketdata_v1/updates_v1.json`
- `core/contracts/public/marketdata_v1/bar_v1.json`

## Policy SSOT (UI)

- Сервер віддає policy через `/api/config`: `policy_version`, `build_id`, `window_policy` та попередження.
- UI читає policy один раз на старті; при відмові `/api/config` переходить у fallback-константи з loud-логом `UI_POLICY_FALLBACK_ACTIVE`.
- На старті сервер має sanity-check rail і логує `POLICY_CONFIG_INVALID`, якщо політика суперечлива (наприклад `warmup < cold_start` у show-immediately TF).

## Профілі середовища (local/prod)

Секрети завантажуються з одного файлу `.env` (без dispatcher/profile).

### Як працює

- `.env` містить секрети (FXCM креденшіали, канали, Redis override).
- `env_profile.py` читає `.env` напряму через python-dotenv.
- Обидва процеси (connector і UI) логують `ENV: secrets_loaded path=... keys=N` при старті.

### Мінімальні ключі (.env — тільки секрети)

- FXCM:
  - `FXCM_USERNAME`
  - `FXCM_PASSWORD`
  - `FXCM_CONNECTION`
  - `FXCM_HOST_URL`
- Redis/канали — у `config.json` секціях `"redis"` та `"channels"`

### Приклад .env

```dotenv
# .env — тільки секрети (канали/Redis тепер у config.json)
FXCM_USERNAME=demo_user
FXCM_PASSWORD=demo_pass
FXCM_CONNECTION=Demo
FXCM_HOST_URL=http://www.fxcorporate.com/Hosts.jsp
```

### Канали (config.json → "channels")

```json
"channels": {
    "prefix": "fxcm_local",
    "ohlcv": "fxcm_local:ohlcv",
    "price_tick": "fxcm_local:price_tik",
    "status": "fxcm_local:status",
    "commands": "fxcm_local:commands",
    "heartbeat": "fxcm_local:heartbeat"
}
```

### Перевірка, що секрети завантажено

У логах має бути:

- Конектор: `ENV: secrets_loaded path=... keys=N`
- UI: `ENV: secrets_loaded path=... keys=N`
- Supervisor (python -m app.main): той самий лог перед стартом процесів

Якщо бачите `ENV: .env не завантажено`, перевірте наявність python-dotenv
та файлу `.env` у кореневій директорії.

### Режими stdio supervisor

- Дефолт: один термінал з префіксами (`[ui]`, `[connector]`):
  - python -m app.main --mode all --stdio pipe
- Логи у файли:
  - python -m app.main --mode all --stdio files --log-dir logs
- Успадкований stdio без префіксів:
  - python -m app.main --mode all --stdio inherit
- Окреме вікно (Windows-only, лише з inherit):
  - python -m app.main --mode ui --stdio inherit --new-console

### Приклади запусків

- Стандартний dev (префікси в одному терміналі):
  - python -m app.main --mode all --stdio pipe
- Лише UI з префіксами:
  - python -m app.main --mode ui --stdio pipe
- Тихий режим у файли:
  - python -m app.main --mode all --stdio files --log-dir logs
- Мінімальний шум (повністю null):
  - python -m app.main --mode all --stdio null
- Окреме вікно UI (Windows):
  - python -m app.main --mode ui --stdio inherit --new-console

## Налаштування

Основні параметри знаходяться у config.json:

- broker_base_tfs_s — базові TF з брокера (4h/1d)
- derived_tfs_s — похідні TF (15m–1h)
- day_anchor_offset_s та *alt* — якорі сесії
- redis.* — snapshots для cold-load UI (опційно)
- min_coldload_bars_by_tf_s — мінімум барів для cold-load з Redis tail
- ui_debug — показ діагностики у UI
- tf_preview_allowlist_s — allowlist TF для preview-plane (за замовчуванням 60/180)

Календарі задаються групами:

- market_calendar_by_group — параметри календаря для групи
- market_calendar_symbol_groups — мапінг символ → група

Модель підтримує лише одну daily break пару на групу (UTC).

Runtime/connector потребує перезапуску після змін config.json.
UI API читає config.json з кешем mtime (перевірка ~0.5s) для ui_debug/tf_allowlist/min_coldload_bars.

## Policy SSOT (UI) — деталі

- Сервер віддає policy через `/api/config`: `policy_version`, `build_id`, `window_policy`, `tf_allowlist`, `preview_tf_allowlist`.
- UI читає policy один раз на старті; при відмові `/api/config` переходить у fallback-константи з loud-логом `UI_POLICY_FALLBACK_ACTIVE`.
- Серверний rail валідує `warmup < cold_start` для show-immediately TF і логить `POLICY_CONFIG_INVALID`.

## UI: cold-load та snapshots

- UDS використовується як read-only у UI (role=reader).
- Для final cold-start `/api/bars` використовує внутрішній `prefer_redis=true` і `disk_policy=never`.
- При малому Redis-tail повертається degraded-but-loud (`redis_small_tail`, `history_short`) замість silent empty.
- no_data rail: `bars=[]` завжди супроводжується `warnings[]` (мінімум `no_data_unexplained`).
- /api/updates читає Redis updates bus (disk лише recovery-сценарій поза hot-path).
- Клієнт UI абортує попередні load-запити і ігнорує застарілі відповіді.

## Preview TF (1m/3m)

- Preview-plane живе у Redis preview keyspace (curr/tail/updates), не пишеться у SSOT.
- /api/bars для tf_s=60/180 читає preview-plane; для non-preview TF `include_preview=1` ігнорується з warning.
- Tick pipeline: tick_publisher_fxcm → Redis PubSub → tick_preview_worker → TickAggregator → UDS preview.
- M1 Poller finals bridge: commit_final M1/M3 → publish до preview ring (final>preview).

## Статус: UDS як write-center

- Writer/connector пише тільки через UDS (без прямого JsonlAppender/RedisSnapshotWriter).
- /api/updates працює через Redis updates bus; disk лишається recovery.
- Tick-stream wiring done: TickAggregator → preview-plane, schema guard tick_v1.
- M1 poller: final M1/M3 з History API, finals bridge, warmup, calendar gate.
- PREVIOUS_CLOSE stitching: open[i]=close[i-1] у /api/bars для TV-like smooth candles.

## UI: scrollback, кеш, favorites

- Scrollback тригериться при дефіциті лівого буфера (~1000 барів).
- Пачки scrollback: базово до 1000 (з clamp у діапазоні 500..2000), для favorites застосовується x2.
- Active cap: до 20000 барів (server-side clamp + client-side policy).
- Warm кеш: LRU до 6 ключів, по 20000 барів на ключ.
- Лог UI_CONTINUITY (rate-limit) підказує, чи є реальні дірки у даних.

## Git та дані

- Дані в data_v3 і History не зберігаються у репо.
- Для синхронізації даних використовуйте окреме сховище (наприклад, архів або зовнішній диск).

## Стиснення (план)

Планується опційне стиснення JSONL (gzip/zstd) із прозорим читанням.

## Exit-gates (GO/NO-GO)

Exit-gates — це мінімальні автоматизовані перевірки контрактів/інваріантів. Runner:

- `python -m tools.run_exit_gates --manifest tools/exit_gates/manifest.json`

Артефакти пишуться в `reports/exit_gates/<run_id>/report.json`.

Якщо gates FAIL — це формальний **NO-GO** до наступних PATCH, доки не буде triage (evidence → класифікація → next action).

## Діагностика (коротко)

- Redis readiness (`{namespace}:prime:ready`) може існувати довше за OHLCV ключі (TTL mismatch). Тому для triage завжди перевіряйте readiness **і** `ohlcv:snap|tail` ключі.
- Основний інструмент “що реально відбувається” — `GET /api/status` (prime_ready, redis_spec_mismatch, disk_bootstrap_reads_total, warnings).
