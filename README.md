# FXCM Connector v3

## Призначення

Торгова платформа (дані → аналітика/SMC → UI → торгова взаємодія).
Дві ізольовані data planes з єдиним write-center (UDS):

**SSOT-1 (M1/M3)** — M1 poller з FXCM History API (8s cycle, calendar gate, watermark, adaptive fetch). M3 derived з 3×M1. Preview-plane: tick stream → preview bars. Final bridge → preview ring.

**SSOT-2 (M5+)** — engine_b polling (60s). Derived 15m/30m/H1 з M5.

**SSOT-3 (H4/D1)** — broker fetch на закритті бакета.

UDS є центром читання/запису: writer пише через UDS (SSOT disk + Redis snap + updates bus), UI читає через UDS.
M1/M3 preview живе в ізольованому Redis keyspace (curr/tail/updates), PREVIOUS_CLOSE stitching у /api/bars.

## Вимоги

- Python 3.7 у .venv
- Runtime залежності: див. requirements.txt
- Локальна розробка: pyproject.toml (deps + metadata)
- ForexConnect SDK встановлюється окремо (vendor SDK / wheel), додав 1.6.43

- Для env-профілів потрібен python-dotenv (є в requirements.txt), змінено на 0.21,<1.0

## Структура даних

- data_v3/SYMBOL/tf_TF/part-YYYYMMDD.jsonl — основні дані
- History/ — довільна історія/експерименти (не для репо)

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

## UI: cold-load та snapshots

- UDS використовується як read-only у UI (role=reader).
- /api/bars у режимі prefer_redis читає Redis tail/snap, але при малому tail переходить на диск.
- /api/updates читає Redis updates bus (disk лише для recovery при redis_down).
- Клієнт UI абортує попередні load-запити і ігнорує застарілі відповіді.

## Preview TF (1m/3m)

- Preview-plane живе у Redis preview keyspace (curr/tail/updates), не пишеться у SSOT.
- /api/bars для tf_s=60/180 читає preview-plane; для non-preview TF `include_preview=1` ігнорується з warning.
- Tick pipeline: tick_publisher_fxcm → Redis PubSub → tick_preview_worker → TickAggregator → UDS preview.
- M1 Poller finals bridge: commit_final M1/M3 → publish до preview ring (final>preview).

## Статус: UDS як write-center (P2X done)

- Writer/connector пише тільки через UDS (без прямого JsonlAppender/RedisSnapshotWriter).
- /api/updates працює через Redis updates bus; disk лишається recovery.
- Tick-stream wiring done: TickAggregator → preview-plane, schema guard tick_v1.
- M1 poller: final M1/M3 з History API, finals bridge, warmup, calendar gate.
- PREVIOUS_CLOSE stitching: open[i]=close[i-1] у /api/bars для TV-like smooth candles.

## UI: scrollback, кеш, favorites

- Scrollback тригериться при дефіциті лівого буфера (~2000 барів), підкидає пачки по 5000.
- Для фаворитів ліміти подвоєні (trigger/chunk/maxBars).
- Active кеш: 60000 барів (або 120000 для фаворитів).
- Warm кеш: LRU до 6 ключів, по 20000 барів на ключ.
- Лог UI_CONTINUITY (rate-limit) підказує, чи є реальні дірки у даних.

## Git та дані

- Дані в data_v3 і History не зберігаються у репо.
- Для синхронізації даних використовуйте окреме сховище (наприклад, архів або зовнішній диск).

## Стиснення (план)

Планується опційне стиснення JSONL (gzip/zstd) із прозорим читанням.
