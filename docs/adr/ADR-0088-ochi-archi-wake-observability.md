# ADR-0088: «Очі Арчі» — серверний джойн пробуджень і стану у готові картки

## Метадані

| Поле | Значення |
| ---- | -------- |
| ID | ADR-0088 |
| Статус | **Accepted** |
| Дата | 2026-07-16 |
| Автори | Станіслав (owner, vision «вікно в життя Арчі») + Fable 5 (архітектор) |
| Будується на | ADR-025 (Archi Console, auth-gate + читання data_dir), ADR-018 (thinking-архів, rotation-aware reader), ADR-0086 (патерн серверного санітизованого джойну), ADR-0049/0085 (WakeEngine, `frame.archi_chart`) |
| Companion | **trader-v3/ADR-097** — новий writer `data/v3_wake_trace.jsonl` (durable дзеркало пробудження). Платформа = read-only споживач; нуль змін бота у цьому ADR (X31) |
| Поважає | I5 (degraded-but-loud: недоступне джерело → null + `degraded[]`, HTTP 200; малформні рядки — скіп із лічильником), I7/S1 (платформа читає, НІКОЛИ не пише за Арчі), X28 (класифікація/дельти рахуються на бекенді, UI = dumb renderer), X31 (нуль імпортів trader-коду — контракт класифікації дзеркалиться) |
| Зачіпає шари | `runtime/ws/wake_cards.py` (NEW, pure), `runtime/ws/ws_server.py` (2 тонкі handlers + routes), `tests/test_ochi_wake_cards.py` (NEW), `docs/ui_api.md`. Споживач: SPA «Очі Арчі» |
| Initiative | `ochi_archi_v1` |

---

## Quality Axes

- **Ambition target**: **R3** — нова поверхня спостереження за автономним агентом: «кіноплівка
  пробуджень» (як/чому Арчі прокидався, що вирішив, скільки коштувало) + «стан зараз» (фокус,
  теза, армовані рівні з відстанню до ціни). Не приватний кокпіт — дружній наратив для трейдера.
- **Maturity impact**: **M4 → M4.5** — закривається розрив «фронт джойнив би 3 endpoint-и у
  браузері»: сервер зшиває `wake_log` + `wake_trace` + `thinking` (кіноплівка) і
  `agent:state` + директиви + теза + ціна (стан) у ГОТОВІ картки. Класифікація тригерів —
  read-side дзеркало контракту trader-v3 з consistency-тестом (контракт-дрейф стає видимим).

---

## 1. Контекст

Owner-vision: SPA «Очі Арчі» показує (1) «стан зараз» — живий фокус агента, і (2) «кіноплівку
пробуджень» — стрічку карток, кожна = одне пробудження Арчі з повним контекстом (тригер →
рішення → армовані рівні → доставлено → вартість). Мета — зрозумілий наратив, а не сирий дамп.

Джерела вже існують, але **розкидані** по трьох файлах/ключах трейдера, які тільки збіг у часі
пов'язує: `v3_wake_log.jsonl` (SSOT списку пробуджень), `v3_wake_trace.jsonl` (durable
дзеркало, trader-v3/ADR-097), thinking-архів (`v3_thinking_archive*.jsonl`), плюс `agent:state`
(Redis) і `v3_agent_directives.json` для «стану зараз». `agent_console.data_dir` у `config.json`
вже вказує на `data/` трейдера — платформа їх бачить read-only.

## 2. RECON (verified 2026-07-16)

| Джерело | Ключ / файл | Що дає |
|---|---|---|
| Список пробуджень (SSOT) | `{data_dir}/v3_wake_log.jsonl` | по рядку на пробудження: ts, wake_id, reason, call_type, model, токени, cost, ack, watch/wake_at/wake_conditions counts, scenario, vp, delivered, price ([VERIFIED trader-v3 agent_call.py:1623-1647]) |
| Дзеркало пробудження | `{data_dir}/v3_wake_trace.jsonl` (ADR-097) | по wake_id: mirror (укр текст ⏰📍📋🔔🌀🔎), mirror_light, ack, emit_warning, message. Старі пробудження trace НЕ мають → `null` |
| Thinking-архів | `{data_dir}/v3_thinking_archive*.jsonl` | extended thinking: ts, call_type, thinking ([VERIFIED trader-v3 thinking_archive.py:64-72]) |
| Presence | Redis HASH `{ns}:agent:state` | ts_ms, mood, health, inner_thought, next_wake_ms/reason, market_session, budget, calls ([VERIFIED trader-v3 events.py:84-104]) |
| Директиви | `{data_dir}/v3_agent_directives.json` | mood, inner_thought(+ts), thought_history, active_scenario, watch_levels, wake_at, wake_conditions, virtual_position, kill/consecutive/budget, last_emit_warning |
| Теза | Redis HASH `{ns}:thesis:{sym}` | thesis, conviction, key_level(_price), invalidation(_price), updated_at_ms ([VERIFIED narrative_enricher.py:118-168]) |
| Ціна | `SmcRunner.get_last_price(symbol)` | current price — той самий reader, що `/api/context` і api_v3 (I1) ([VERIFIED ws_server.py:3087]) |

Класифікація тригерів (alert/тихо + heartbeat/wake_at/watch/ritual/vp/other) —
[VERIFIED trader-v3 wake_log.py:80-141]. Платформа не може імпортувати trader-код (X31), тому
pure-логіка дзеркалиться на платформі з коментарем-референсом + consistency-тестом.

## 3. Альтернативи

1. **Фронт джойнить сам** через існуючі `/api/archi/thinking` + `/api/archi/directives` +
   `/api/archi/feed` — **ВІДХИЛЕНО**: X28-порушення (браузер класифікує тригери, рахує дельти
   до ціни, зшиває три джерела за timestamp); 3× трафік (три запити на кадр); join-логіка у
   кожному клієнті = дублювання і дрейф.
2. **Окремий процес/сервіс** що агрегує і віддає картки — **ВІДХИЛЕНО**: зайвий демон,
   окремий деплой/нагляд, дублювання доступу до `data_dir` і Redis, які ws_server вже має.
3. **Розширення існуючого console-gate у ws_server** (два read-only endpoint-и за тим самим
   `_archi_auth`, pure-джойн у новому модулі) — **ОБРАНО**: реюз auth/redis/data_dir/price-reader
   що вже змонтовані; санітизація і класифікація в одній точці; UI лишається dumb renderer.

## 4. Рішення

Два GET-endpoint-и під існуючим `if _console_enabled:` (auth = `_archi_auth`, як усі
`/api/archi/*`). Pure-ядро — `runtime/ws/wake_cards.py`; ws_server тримає тонкі handlers (I/O).

### D1. `GET /api/archi/wakes?limit=30&before_ts=<ts>` — кіноплівка

Keyset newest-first: ≤`limit` (clamp 1..100) карток з `ts < before_ts` (`None` = найновіша
сторінка). Картка = **всі поля wake_log-запису verbatim** + `{category, alert, trace|null,
thinking|null, thinking_ts|null}`. Джойн trace по `wake_id` (fallback по точному `ts`, якщо
`wake_id` порожній); thinking — найближчий запис по ts з `|Δ| ≤ 600s` ТА збігом `call_type`.

```jsonc
{
  "wakes": [
    {                                     // картка З trace + thinking
      "ts": 1783019000, "wake_id": "wake_1783019000_ab12",
      "reason": "Watch level fired @4700", "call_type": "platform_wake",
      "model": "claude-sonnet-4-6", "in": 12000, "out": 800, "cache_read": 9000,
      "truncated": false, "cost": 0.021, "ack": "бачу пробій, апдейчу тезу",
      "emit_warning": "", "watch": 2, "wake_conditions": 3, "wake_at": 1,
      "scenario": "sc-london-short", "vp": null, "delivered": true,
      "msg_len": 480, "price": 4700.5,
      "category": "watch", "alert": true,           // класифіковано сервером (X28)
      "trace": {
        "mirror": "⏰ Розбудило: platform watch 4700 …",
        "mirror_light": true, "ack": "бачу пробій, апдейчу тезу",
        "emit_warning": "", "message": "XAU підійшов до 4700 зверху …"
      },
      "thinking": "Ціна торкнулась 4700 — рівень інвалідації …",
      "thinking_ts": 1783018995
    },
    {                                     // стара картка БЕЗ trace (mirror gracefully null)
      "ts": 1782900000, "wake_id": "wake_1782900000_cd34",
      "reason": "timer:next_check_heartbeat +30m", "call_type": "proactive",
      "model": "claude-haiku-4-5", "in": 5000, "out": 200, "cache_read": 4800,
      "truncated": false, "cost": 0.003, "ack": "", "emit_warning": "",
      "watch": 1, "wake_conditions": 2, "wake_at": 1, "scenario": null, "vp": null,
      "delivered": false, "msg_len": 0, "price": 4712.0,
      "category": "heartbeat", "alert": false,
      "trace": null, "thinking": null, "thinking_ts": null
    }
  ],
  "total": 187,          // повна кількість пробуджень у логу
  "oldest_ts": 1782900000 // курсор наступної сторінки (before_ts)
}
```

### D2. `GET /api/archi/now?symbol=<sym>` — стан зараз

Серверний джойн presence + директиви + теза + ціна + армовані рівні з **серверними**
`delta`/`delta_pct` (X28). Символ фокусу з `?symbol=` (UI знає) з fallback на
`wake_conditions.params.symbol`; без нього ціна/теза = `null` (degraded), решта віддається.

```jsonc
{
  "symbol": "XAU/USD", "generated_ms": 1783019100000,
  "price": 4700.5, "stale": false,
  "state": {                              // agent:state HASH (str→str), presence
    "ts_ms": "1783019090000", "health": "ok", "model_current": "claude-sonnet-4-6",
    "next_wake_ms": "1783020900000", "next_wake_reason": "London open",
    "budget_today_usd": "0.42", "budget_pct": "8.4", "calls_today": "14",
    "has_virtual_position": "0", "circuit_breaker": "0",
    "inner_thought": "тримаю short-тезу, чекаю ретест 4700",
    "mood": "focused", "market_session": "london", "last_error": ""
  },
  "directives": {                         // whitelist «стану зараз» з файла директив
    "mood": "focused", "inner_thought": "тримаю short-тезу, чекаю ретест 4700",
    "active_scenario": {
      "id": "sc-london-short", "direction": "short", "thesis": "…",
      "entry_zone_low": 4695.0, "entry_zone_high": 4705.0, "invalidation": 4720.0,
      "targets": [4670.0, 4650.0], "confidence": 0.6, "status": "waiting"
    },
    "virtual_position": null, "kill_switch_active": false, "consecutive_errors": 0,
    "budget_strategy": "normal", "next_check_minutes": 30,
    "next_check_reason": "спокійний ринок",
    "thought_history": [ {"ts": 1783018800, "text": "…", "mood": "calm"} ],
    "watch_levels": [ {"id": "wl1", "price": 4720.0, "direction": "above"} ],
    "wake_at": [ {"id": "deep_brief", "time_epoch": 1783020900.0, "reason": "…"} ],
    "wake_conditions": [
      {"id": "wc1", "kind": "price_cross", "params": {"price": 4700.0, "direction": "below"}}
    ],
    "last_emit_warning": "",
    "token_usage_today": {"input": 120000, "output": 8000}
  },
  "thesis": {
    "thesis": "D1 bearish, чекаю ретест OB 4700-4705 для short",
    "conviction": "high", "key_level": "4700", "invalidation": "4720",
    "key_level_price": 4700.0, "invalidation_price": 4720.0,
    "updated_at_ms": 1783018000000, "age_ms": 1100000  // вік рахує бекенд (X28)
  },
  "armed": [                              // найближчі до ціни першими; дельти = сервер
    {"level": 4700.0, "direction": "below", "source": "wake_condition",
     "kind": "price_cross", "id": "wc1", "delta": -0.5, "delta_pct": -0.0106},
    {"level": 4720.0, "direction": "above", "source": "watch_level",
     "kind": "watch_level", "id": "wl1", "delta": 19.5, "delta_pct": 0.4148}
  ],
  "degraded": []                          // недоступні джерела: напр. ["thesis_unavailable"]
}
```

Правила:
- **Whitelist-only** для директив і тези: приватна історія/пам'ять/reasoning НЕ проходять.
- **Дельти рахує сервер** (X28): фронт малює `delta`/`delta_pct` як дані.
- Схема `wake_conditions.params` історично використовує ключ `price` (реальні дані директив);
  Redis-canonical (ADR-078) — `level`. Обидва підтримуються при видобутку рівня.
- Деградація (I5): недоступний Redis/файл → відповідне поле `null` + запис у `degraded[]`,
  HTTP **200** (не 500). `stale=true` якщо `agent:state` старіший за 15 хв.

### D3. Реалізація і Slices

- `runtime/ws/wake_cards.py` (NEW): pure `read_wake_cards(...)`, `build_now_view(...)`,
  `classify_alert`/`categorize_wake` (дзеркало trader-v3), `clamp_wake_limit`. Нуль I/O у ядрі.
- `ws_server.py`: два тонкі handlers (`_api_archi_wakes`, `_api_archi_now`) + routes у
  `if _console_enabled:`. Реюз `_archi_auth`, `_agent_redis_client`, `_console_data_dir`,
  `_read_thinking_records`, `SmcRunner.get_last_price` (I1 — жодного нового price-reader).
- `tests/test_ochi_wake_cards.py` (NEW): 18 специфікацій (join, mirror-null, thinking-вікно
  600s + негатив 700s, keyset-пагінація, clamp, малформні рядки, класифікація, armed-дельти,
  stale, thesis-whitelist).

| # | Що | LOC | Verify |
|---|----|-----|--------|
| P1 | `wake_cards.py` + тести | ≤200 (новий tested модуль) | pytest: усі специфікації зелені |
| P2 | ws_server handlers + routes | ≤150 | py_compile + AST-guard (X33) + WS-група зелена |
| P3 | docs (`ui_api.md`, index) | — | drift-check |

## 5. Consequences

- SPA «Очі Арчі» стає dumb renderer: один запит на кіноплівку, один на стан; нуль домену у фронті.
- Класифікація тригерів має єдине джерело контракту (trader-v3 wake_log.py); дрейф ловиться
  consistency-тестом на платформі (X31 залишається чистим — імпорту немає).
- Мільйон переглядів = ≤читання файлів/Redis на запит (endpoint дешевий; кеш можна додати
  пізніше однією точкою, як TTL-кеш ADR-0086).

## 6. Ризики

- **R1 контракт-дрейф класифікації** (trader-v3 змінює `categorize_wake` → платформа відстає):
  mitigation — коментар-референс + consistency-тест; майбутнє — спільний контракт-фікстур.
- **R2 thinking-джойн для глибокої пагінації**: скан архіву обмежений (500 записів) → дуже
  старі картки деградують до `thinking:null`. Прийнятно (I5 graceful); wake_log cap 200 (~10h)
  покривається скановим вікном.

## 7. Rollback

Прибрати `add_get("/api/archi/wakes", …)` та `add_get("/api/archi/now", …)` з
`if _console_enabled:` (два рядки) → endpoint-и зникають, SPA чесно показує «немає даних».
Модуль `wake_cards.py` лишається orphan (безпечно) або видаляється. Дані ніде не персистяться.
