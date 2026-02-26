# ADR-0013: Семантика `complete=true` для partial derived bars

- Статус: Accepted
- Дата: 2026-02-25

## Контекст

Для derived TF (зокрема M5) можливі календарні паузи/гепи в межах bucket, коли bucket вже elapsed, але агреговано менше source-барів, ніж nominal `N`.

Історично `complete=true` інколи інтерпретували як "100% N з N source bars", що некоректно для partial calendar pause / boundary-tolerant сценаріїв.

## Рішення

1. Зафіксувати контракт: `complete=true` означає **bucket elapsed** (нових source-барів для цього bucket більше не буде), а не гарантію "N з N".
2. Для derived partial барів у `extensions` передавати явні маркери:
   - `partial=true`
   - `partial_calendar_pause=true` (коли фільтровані `calendar_pause_flat`)
   - `boundary_partial=true` (boundary/mid-session tolerant fallback)
   - `source_count`, `expected_count`
   - `partial_reasons[]` для явного переліку причин (`calendar_pause`, `boundary_gap`)
3. Downstream (UI/screening/strategy engines) має застосовувати soft-penalty, а не hard reject; рекомендована формула: `1 - source_count/expected_count`.
4. Per-symbol calendar вже підтримується в derive pipeline через `calendars: {symbol -> MarketCalendar}` і має лишатися джерелом правди для індивідуальної поведінки символів.

## Наслідки

- Зворотна сумісність: старі поля (`complete`, `src`, OHLCV) не змінені.
- Strict-клієнти можуть явно відфільтрувати partial-bar через `extensions.partial*`.
- Менше хибних відмов у сигнальних пайплайнах: часткові бари не відкидаються повністю, але мають нижчу вагу.
