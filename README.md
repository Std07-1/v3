# FXCM Connector v3

## Призначення

Конектор отримує історію 5m з брокера, будує похідні TF локально та зберігає все в JSONL.
База TF:

- 5m — з брокера.
- 4h / 1d — з брокера як «джерело істини».
- 15m–1h — похідні з 5m.

## Вимоги

- Python 3.7 у .venv
- Runtime залежності: див. requirements.txt
- Локальна розробка: pyproject.toml (deps + metadata)
- ForexConnect SDK встановлюється окремо (vendor SDK / wheel)
- Для env-профілів потрібен python-dotenv (є в requirements.txt)

## Структура даних

- data_v3/SYMBOL/tf_TF/part-YYYYMMDD.jsonl — основні дані
- History/ — довільна історія/експерименти (не для репо)

## Запуск системи

- Активуйте .venv.
- Запуск UI+конектор (одна команда):
  - python -m app.main
- Лише конектор:
  - python -m app.main --mode connector
- Лише UI:
  - python -m app.main --mode ui

## Профілі середовища (local/prod)

Профілі потрібні для безпечної ізоляції local/prod через env. У v3 використовується
один SSOT конфіг (config.json), а профіль визначає креденшіали FXCM і Redis.

### Як працює перемикання

- `.env` містить тільки `AI_ONE_ENV_FILE=.env.local` або `.env.prod`.
- `env_profile.py` спочатку читає `.env`, потім завантажує профільний файл.
- Обидва процеси (connector і UI) логують активний профіль при старті.

### Мінімальні ключі профілю

- FXCM:
  - `FXCM_USERNAME`
  - `FXCM_PASSWORD`
  - `FXCM_CONNECTION`
  - `FXCM_HOST_URL`
- Redis (ізоляція local/prod):
  - `FXCM_REDIS_HOST`
  - `FXCM_REDIS_PORT`
  - `FXCM_REDIS_DB`
  - `FXCM_REDIS_NS`

### Приклад dispatcher

```dotenv
# .env
AI_ONE_ENV_FILE=.env.local
```

### Приклад локального профілю

```dotenv
# .env.local
FXCM_USERNAME=demo_user
FXCM_PASSWORD=demo_pass
FXCM_CONNECTION=Demo
FXCM_HOST_URL=http://www.fxcorporate.com/Hosts.jsp

FXCM_REDIS_HOST=127.0.0.1
FXCM_REDIS_PORT=6379
FXCM_REDIS_DB=1
FXCM_REDIS_NS=v3_local
```

### Приклад прод профілю

```dotenv
# .env.prod
FXCM_USERNAME=prod_user
FXCM_PASSWORD=prod_pass
FXCM_CONNECTION=Real
FXCM_HOST_URL=http://www.fxcorporate.com/Hosts.jsp

FXCM_REDIS_HOST=redis.prod.local
FXCM_REDIS_PORT=6380
FXCM_REDIS_DB=0
FXCM_REDIS_NS=v3_prod
```

### Перевірка, що профіль підхоплено

У логах має бути:

- Конектор: `ENV: dispatcher=... profile=...`
- UI: `ENV: dispatcher=... profile=...`
- Supervisor (python -m app.main): той самий лог перед стартом процесів

Якщо бачите `ENV: профіль не завантажено`, перевірте наявність python-dotenv
та правильний `AI_ONE_ENV_FILE` у `.env`.

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

Календарі задаються групами:

- market_calendar_by_group — параметри календаря для групи
- market_calendar_symbol_groups — мапінг символ → група

Модель підтримує лише одну daily break пару на групу (UTC).

Runtime/connector потребує перезапуску після змін config.json.
UI API читає config.json з кешем mtime (перевірка ~0.5s) для ui_debug/tf_allowlist/min_coldload_bars.

## UI: cold-load та snapshots

- /api/bars у режимі prefer_redis читає Redis tail/snap, але при малому tail переходить на диск.
- /api/updates завжди читає диск і використовує tail-only скан.
- Клієнт UI абортує попередні load-запити і ігнорує застарілі відповіді.

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
