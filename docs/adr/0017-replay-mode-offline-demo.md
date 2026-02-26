# ADR-0017: Replay-Mode з data_v3/ для Offline Demo

- **Статус**: Proposed
- **Дата**: 2026-02-26
- **Автор**: code-review-audit
- **Initiative**: `offline_demo`

## Контекст і проблема

Система не запускається без FXCM credentials + live Redis (`app/main.py` не має `sim_mode`, `mock`, `offline` прапорців). Це унеможливлює:

- Демонстрацію на чистій машині
- CI integration tests без broker
- Зовнішній рев'ю / оцінку продукту
- Розробку UI без підключення до брокера

При цьому `data_v3/` містить SSOT JSONL файли з реальними даними для 13 символів (M1→D1), що є достатньою базою для повного replay.

## Розглянуті варіанти

### A. Генератор синтетичних тиків (sim-mode)

- **Плюси**: не залежить від історичних даних, тестує весь pipeline
- **Мінуси**: потрібно реалізувати реалістичний tick generator, не доводить correctness на реальних даних
- **Estimate**: 3-5 днів

### B. Replay-mode з data_v3/ (рекомендовано)

- **Плюси**: реальні дані, доводить correctness, мінімальні зміни — потрібен лише "reader → UDS bootstrapper" без broker. UI бачить реальні свічки.
- **Мінуси**: немає live preview/tick stream (тільки final бари); потрібен Redis (але без FXCM)
- **Estimate**: 1-2 дні

### C. Docker-compose з mock-broker

- **Плюси**: повний pipeline
- **Мінуси**: складність, потрібен Docker, mock-broker — окремий модуль
- **Estimate**: 5+ днів

## Рішення

**Варіант B** — replay-mode з диску:

### Архітектура

```
python -m app.main --mode replay [--symbols XAU_USD,NAS100] [--speed 1x]

                data_v3/
                   │
        ┌──────────┴──────────┐
        │   ReplayProvider    │  ← новий модуль runtime/ingest/replay.py
        │  (читає JSONL,      │
        │   емітить bars)     │
        └──────────┬──────────┘
                   │ commit_final_bar()
                   ▼
                  UDS ──► Redis ──► /api/updates ──► UI
```

### Реалізація

1. **`runtime/ingest/replay.py`** (~100 LOC): читає `data_v3/{sym}/tf_60/part-*.jsonl`, парсить бари, відправляє в UDS через `commit_final_bar()` з контрольованою швидкістю.
2. **`app/main.py --mode replay`**: стартує ReplayProvider + UI processes, без connector/m1_poller/tick_publisher.
3. **DeriveEngine**: працює як звичайно — отримує M1 → будує M3→H4.
4. **UI**: працює без змін — бачить бари через `/api/updates` і `/api/bars`.

### Обмеження

- Тільки final бари (без preview/tick stream) — достатньо для demo/CI
- Потрібен Redis (localhost:6379) — це OK для demo
- `--speed 1x` = реальний час, `--speed 0` = dump all instantly (для CI)

## Наслідки

- Новий файл: `runtime/ingest/replay.py` (~100 LOC)
- Зміна: `app/main.py` — додати `--mode replay` (≤30 LOC)
- Інваріанти I0–I6 не порушуються (ReplayProvider = ще один writer через UDS)
- README: додати quickstart без FXCM
- Exit-gates: додати gate "replay-mode smoke" (start → bars visible → exit 0)

## Rollback

1. Видалити `runtime/ingest/replay.py`
2. Прибрати `--mode replay` з `app/main.py`
3. Прибрати replay quickstart з README
