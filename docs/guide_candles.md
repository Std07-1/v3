# Посібник: «гарні» 1m/3m свічки від брокера до UI

Ціль: швидко отримати TV‑like свічки для M1/M3 без «вічної боротьби».
Цей гайд описує мінімальний робочий контур і типові пастки.

## 1) Канонічні інваріанти (мають триматись завжди)

- Єдиний формат барів: CandleBar з open_time_ms, close_time_ms, o/h/low/c, v.
- M1/M3 у preview‑площині: preview (complete=false) і final (complete=true) живуть у одному preview‑ring.
- Final завжди перемагає preview для одного (symbol, tf, open_time_ms).
- Бари ingest‑яться тільки у зростаючому порядку open_time_ms.

## 2) Мінімальний шлях даних (M1/M3)

### 2.1 Preview (tick stream)

1. TickPublisher читає тики брокера і публікує у Redis channel.
2. TickPreviewWorker агрегує тики → M1 preview.
3. TickPreviewWorker публікує:
   - preview бари (complete=false) у preview ring,
   - promoted бари (complete=true, src=tick_promoted) у preview ring.
4. UI читає preview ring через /api/updates.

### 2.2 Final (polling)

1. M1 Poller раз на хвилину робить History API fetch (M1).
2. Фільтрує лише закриті бари (open_time_ms <= expected_closed).
3. Сортує за open_time_ms (asc) → commit_final_bar.
4. UDS:
   - пише SSOT (disk),
   - публікує final у preview ring (final>preview).
5. UI отримує final через /api/updates і перемальовує попередній preview.

## 3) Мінімальні налаштування (config.json)

- tick_stream_enabled=true
- preview_tick_enabled=true
- tick_stream_price_mode="bid" (щоб preview не стрибав при заміні на history)
- tick_auto_promote_m1=true (mock‑final з тиків)
- m1_poller.enabled=true
- ui_stitching_enabled=true (лише display‑stitching у /api/bars)

## 4) Типові пастки та як їх уникати

1) **PREVIOUS_CLOSE у FXCM History**
   - Дає артефакт: чергування обсягів (high/low) + рвані переходи.
   - Рішення: використовувати FIRST_TICK (default) + UI stitching.

2) **Зворотний порядок барів від History**
   - Якщо ingest не сортований, watermark викидає старі бари як stale.
   - Рішення: фільтр open_time_ms <= expected_closed + sort asc.

3) **MID vs BID**
   - Preview на MID, history на BID → видимі стрибки.
   - Рішення: tick_stream_price_mode="bid".

4) **Split‑brain preview/final**
   - Final іде в інший канал → UI не бачить фінали.
   - Рішення: final→preview ring bridge у UDS.

## 5) Швидка перевірка (3 хвилини)

1. /api/updates (M1)
   - Має бути source=history для final і preview_tick для preview.
2. /api/bars (M1)
   - open == prev close (з UI stitching) та немає gap.
3. Логи:
   - TickPreview: TICK_PREVIEW_STATS
   - M1 Poller: M1_POLLER_STATS

## 6) Чіткі правила для розробників

- Немає альтернативних шляхів для M1/M3: тільки preview‑ring.
- Не змішувати preview/final у різних каналах.
- Усі бари history ingest‑яться у зростаючому порядку.
- Якщо щось «підправляє» candle‑формування — це має бути окремий PATCH + gate.

## 7) Контрольні команди

- Перевірка апдейтів:
  - curl <http://localhost:8089/api/updates?symbol=XAU/USD&tf_s=60&limit=200>
- Перевірка барів:
  - curl <http://localhost:8089/api/bars?symbol=XAU/USD&tf_s=60&limit=200>

## 8) Коли не чіпати

- M5+ (5m/15m/30m/1h/4h/1d) — окремий pipeline, не змішується з M1/M3.
