# config.json — довідник полів

> **Навігація**: [docs/index.md](index.md)  
> **SSOT**: `config.json` (Правило №4).
> Секрети — лише у `.env.prod`/`.env.local`.
> Цей файл — пояснення кожного ключа.

---

## Загальні

| Ключ | Тип | Приклад | Опис |
| --- | --- | --- | --- |
| `symbol` | str | `"XAU/USD"` | Символ за замовчуванням (legacy, не використ. в multi-sym mode) |
| `symbols` | str[] | `["XAU/USD", ...]` | **Список всіх активних символів** (4 шт: XAU/USD, XAG/USD, BTCUSDT, ETHUSDT). Визначає що збирається/показується |
| `data_root` | str | `"./data_v3"` | Кореневий каталог SSOT JSONL на диску |

---

## M5 Pipeline (polling connector) — ⚠️ DEPRECATED

> **Декомісіоновано**: M5 polling вимкнено (ADR-0002 Phase 5, `m5_polling_enabled=false`). Всі TF derived від M1 через DeriveEngine. Ключі залишаються в config.json для зворотної сумісності.

| Ключ | Тип | За замовч. | Опис |
| --- | --- | --- | --- |
| `warmup_bars` | int | 1000 | Скільки M5 барів завантажити з FXCM History на coldstart |
| `safety_delay_s` | int | 2 | Затримка після закриття M5 бару перед запитом до FXCM (секунди) |
| `m5_tail_fetch_n` | int | 12 | Скільки останніх M5 барів запитувати у кожному poll-циклі |
| `m5_tail_stale_s` | int | 720 | Якщо останній M5 бар старший за N секунд — стейл |
| `m5_tail_catchup_max_missing_bars` | int | 5000 | Макс кількість M5 барів для catchup при великому gap |
| `m5_tail_catchup_max_lookback_bars` | int | 5000 | Макс lookback для catchup |

---

## M5 Derived TF rebuild (14400s, 86400s)

| Ключ | Тип | За замовч. | Опис |
| --- | --- | --- | --- |
| `derived_tail_rebuild_enabled` | bool | true | Чи перебудовувати derived TF при полінгу |
| `derived_tail_rebuild_m5_bars` | int | 5000 | Lookback M5 барів для derived rebuild |
| `derived_tail_rebuild_budget_s` | int | 5 | Таймаут на rebuild (секунди) |
| `derived_tfs_s` | int[] | [14400, 86400] | TF для derived (H4=14400, D1=86400). ADR-0023 |

---

## M5 Backfill

| Ключ | Тип | За замовч. | Опис |
| --- | --- | --- | --- |
| `m5_backfill_step_bars` | int | 300 | Розмір чанку M5 backfill |
| `m5_backfill_every_min` | int | 5 | Як часто запускати backfill (хвилини) |
| `m5_backfill_max_bars` | int | 30000 | Макс глибина M5 backfill |

---

## M1 Poller (FXCM History API → M1 final + M3 derived)

| Ключ | Тип | За замовч. | Опис |
| --- | --- | --- | --- |
| `m1_poller.enabled` | bool | true | Увімкнути M1 poller |
| `m1_poller.tail_fetch_n` | int | 5 | Скільки M1 барів запитувати з FXCM History кожну хвилину |
| `m1_poller.safety_delay_s` | int | 8 | Затримка після закриття M1 перед запитом (FXCM потрібно ~5-8с) |
| `m1_poller.m3_derive_enabled` | bool | true | Будувати M3 з 3×M1 при кожному коміті |
| `m1_poller.backfill_enabled` | bool | true | ⚠️ **DEAD CONFIG** — код не читає. Зарезервовано для майбутнього |
| `m1_poller.backfill_max_bars` | int | 1440 | ⚠️ **DEAD CONFIG** — не реалізовано в m1_poller.py |

> **Warmup M1**: На cold start M1 poller завантажує `redis.tail_n_by_tf_s["60"]` = 2880 барів з диску в Redis.
> M1Buffer (для M3 derive) ініціалізується всього 10 барами (хардкод у коді).

---

## Live Recovery (M1) — ⚠️ DEPRECATED

> **Декомісіоновано**: Live Recovery був частиною M5 connector pipeline. Після переходу на M1-based pipeline ці ключі не використовуються активно. Залишаються в config.json для зворотної сумісності.

| Ключ | Тип | За замовч. | Опис |
| --- | --- | --- | --- |
| `live_recover_threshold_bars` | int | 3 | Мін кількість пропущених барів для активації recovery |
| `live_recover_max_bars_per_cycle` | int | 120 | Макс барів за один цикл recovery |
| `live_recover_cooldown_s` | int | 5 | Cooldown між recovery циклами |
| `live_recover_max_total_bars` | int | 5000 | Загальний бюджет recovery барів |
| `live_recover_log_interval_s` | int | 60 | Інтервал логування recovery |

---

## Connector (FXCM ForexConnect) — ⚠️ DEPRECATED

> **Декомісіоновано**: M5/D1 connector (engine_b) вимкнено — `broker_base_tfs_s=[]`, `m5_polling_enabled=false` (ADR-0002/0023). Ключі залишаються в config.json. `flat_bar_max_volume` ще використовується в M1 ingestion pipeline.

| Ключ | Тип | За замовч. | Опис |
| --- | --- | --- | --- |
| `connector_retry_base_s` | int | 10 | Початковий інтервал ретрі при disconnect |
| `connector_retry_max_s` | int | 3600 | Максимальний інтервал ретрі (exp backoff) |
| `connector_wake_ahead_s` | int | 900 | За скільки секунд до відкриття ринку будити конектор |
| `flat_bar_max_volume` | int | 4 | Макс volume для позначки бару як "flat" (calendar pause) |

---

## History API Circuit Breaker

| Ключ | Тип | За замовч. | Опис |
| --- | --- | --- | --- |
| `history_summary_interval_s` | int | 600 | Інтервал логування summary History API |
| `history_still_failing_interval_s` | int | 600 | Інтервал логування при постійних помилках |
| `history_circuit_fail_streak` | int | 3 | Кількість послідовних помилок для "розмикання" circuit |
| `history_circuit_base_s` | int | 300 | Базова пауза при circuit open |
| `history_circuit_max_s` | int | 900 | Макс пауза circuit |
| `history_circuit_log_interval_s` | int | 300 | Логування стану circuit |
| `history_symbols_sample_n` | int | 3 | Скільки символів показувати у summary |
| `history_network_error_escalate_s` | int | 600 | Ескалація при мережевій помилці |

---

## Broker Base TFs (H4, D1 — прямий fetch з FXCM)

| Ключ | Тип | За замовч. | Опис |
| --- | --- | --- | --- |
| `broker_base_tfs_s` | int[] | [] | TF для прямого fetch. **Порожній** — всі TF derived (ADR-0023) |
| `broker_base_fetch_on_close` | bool | true | Забирати бар одразу після close |
| `broker_base_max_tf_per_poll` | int | 0 | 0 = без ліміту |
| `broker_base_cold_start_enabled` | bool | true | Завантажити H4/D1 історію на coldstart |
| `broker_base_cold_start_counts` | dict | {"14400":1080,"86400":180} | Скільки барів на coldstart для кожного TF |

---

## TF Allowlist

| Ключ | Тип | Опис |
| --- | --- | --- |
| `tf_allowlist_s` | int[] | **Повний список дозволених TF** (від M1 до D1). Визначає що зберігається в Redis/UDS |

---

## Preview (Tick → M1/M3 live preview)

| Ключ | Тип | За замовч. | Опис |
| --- | --- | --- | --- |
| `preview_tick_enabled` | bool | true | Увімкнути tick → preview побудову свічок |
| `preview_tick_tfs_s` | int[] | [60, 180, 300, 900, 1800, 3600, 14400, 86400] | TF для tick preview (M1→D1, включно з HTF running accumulator) |
| `preview_tick_publish_min_interval_ms` | int | 250 | Мін інтервал між публікаціями preview в Redis |
| `preview_curr_ttl_s` | int | 1800 | TTL preview_curr ключа в Redis |
| `tick_auto_promote_m1` | bool | true | Auto-promote: на переході M1 бакету → публікувати як complete (до приходу History final) |
| `ui_stitching_enabled` | bool | false | Stitching open[i]=close[i-1] для UI. **false** = показувати реальні гепи (як TV) |

---

## Tick Stream (raw ticks → Redis pub/sub)

| Ключ | Тип | За замовч. | Опис |
| --- | --- | --- | --- |
| `tick_stream_enabled` | bool | true | Увімкнути публікацію сирих тіків |
| `tick_stream_symbols` | str[] | [] | Порожній = всі символи |
| `tick_stream_min_interval_ms` | int | 200 | Мін інтервал між тіками одного символу |
| `tick_stream_last_tick_ttl_s` | int | 30 | TTL останнього тіка в Redis |
| `tick_stream_price_mode` | str | `"bid"` | Яку ціну публікувати: `bid`/`ask`/`mid` |

---

## Market Calendar

| Ключ | Тип | Опис |
| --- | --- | --- |
| `calendar_gate_enabled` | bool | Вхідний gate: відкидати дані поза торговими годинами |
| `market_calendar_by_group` | dict | Розклади торгових сесій по групах (fx, cfd_us, cfd_eu, cfd_hk) |
| `market_calendar_symbol_groups` | dict | Маппінг символу → група розкладу |
| `market_ignore_minutes_utc` | str[] | Конкретні хвилини для ігнорування (outage workaround) |
| `market_boundary_slip_minutes_per_day` | int | Допуск зсуву кордону сесії (хвилини) |

---

## Day Anchors (для HTF bucket alignment)

> **DST-залежні значення!** Ці якорі змінюються при переході літній/зимовий час.
> Повна процедура переходу: [`docs/runbooks/dst_transition.md`](../runbooks/dst_transition.md)

| Ключ | Тип | Літо (EDT) | Зима (EST) | Опис |
| --- | --- | --- | --- | --- |
| `day_anchor_offset_s` | int | **79200** (22:00 UTC) | **82800** (23:00 UTC) | H4 bucket alignment — поточний активний якір |
| `day_anchor_offset_s_alt` | int | 82800 | 0 | Альт H4 якір (для backward compat при DST) |
| `day_anchor_offset_s_alt2` | int | 0 | 79200 | Альт H4 якір 2 |
| `day_anchor_offset_s_d1` | int | **75600** (21:00 UTC) | **79200** (22:00 UTC) | D1 bucket alignment — ADR-0023 |
| `day_anchor_offset_s_d1_alt` | int | 79200 | 0 | Альт D1 якір (для backward compat) |

**Пояснення**: Primary (`day_anchor_offset_s`, `_d1`) = поточний сезон. Alt/Alt2 = попередній сезон для `select_anchor_offset_for_open_ms()` — дозволяє читати старі бари без rebuild всієї історії.

---

## WS Server rails (SEC-06, `ws_server.*`)

| Ключ | Дефолт | Значення |
|---|---|---|
| `max_clients` | 200 | одночасних WS-сесій на процес; перевищення → HTTP 503 до upgrade (0 = без ліміту) |
| `max_clients_per_ip` | 8 | сесій з однієї адреси (`X-Real-IP` від nginx після `realip_cloudflare.conf`); 0 = без ліміту |
| `actions_burst` | 10 | token bucket на сесію: скільки дій підряд без паузи; 0 = без ліміту |
| `actions_per_s` | 2.0 | refill token bucket (дій/с) |
| `switch_cooldown_s` | 1.0 | мінімальний інтервал між `switch` (кожен = cold-start read + SMC snapshot); 0 = вимкнено |

Відмови = degraded-but-loud: `WS_REJECT`/`WS_ACTION_RATE_LIMITED`/`WS_SWITCH_REJECT` у логах + error frame
`switch_throttled`/`action_rate_limited` клієнту. Heartbeat aiohttp (ping/pong) = `heartbeat_interval_s`.

---

## Глибина M1-історії FXCM (виміряна властивість брокера, ADR-0054 P0.6)

Не конфіг-ключ, а межа, яку треба знати при засіві нового символу.

| Дата виміру | Символ | Найстаріший отриманий M1 | Глибина | Як міряли |
|---|---|---|---|---|
| 2026-09-06 | NAS100 | `2026-04-17 20:39 UTC` | **≥142 дні** (≈4.7 міс) | 9 ланцюжкових викликів `fetch_tf_backfill --tf 60 --n 8640` з `--date-to` = найстаріший бар попередньої партії |

Це **нижня** межа: цикл зупинено за кількістю кроків, а не через відмову брокера —
останній крок ще повертав повні 8639 барів. Практичний висновок: 30 торгових днів
(вимога ADR-0054 Фази 0) FXCM віддає із запасом.

Обмеження процедури засіву:

- `fetch_tf_backfill` відкриває **другу** ForexConnect-сесію тим самим логіном, тому
  запускати лише при зупинених `smc-fxcm`/`smc-ticks` (або у вікні закритого ринку).
- Запускати **від користувача `smc`** (`sudo -u smc env HOME=/var/lib/smc …`): каталог
  `History/` належить йому після ADR-0091 P1, інакше ForexConnect падає з
  `QuotesManager error: Global lock initialization failed`.
- Ланцюжок вести від **сьогодні назад** по `first=` з виводу самого fetch. Курсор «по
  найстарішому бару на диску» ламається, якщо перед тим був разовий глибокий виклик:
  06.09 це лишило дірку 08.07→04.09, і D1 обірвався саме там (M1/H4 виглядали цілими).

---

## Agent bridge (ADR-0090, `agent_bridge.*`)

Єдина секція для всього, що існує заради зовнішнього AI-клієнта. Top-level ключів `wake_engine`,
`agent_console`, `public_snapshot` більше немає — стереже CI-гейт `platform_config_no_agent_keys`.
Резолвер: `runtime/agent_bridge/config.py` (`resolve_agent_bridge_config`).

| Ключ | Дефолт | Значення |
|---|---|---|
| `enabled` | false | головний перемикач; репо-дефолт = вимкнено. `wake_engine.enabled` і `console.enabled` діють лише при `enabled=true`. Процес bridge при `false` лишається живим і віддає лише `GET /api/bridge/health` (`enabled:false`), приватні маршрути не монтуються |
| env `AI_ONE_AGENT_BRIDGE_ENABLED` | — | перекриває `enabled` на конкретному хості (`1/0/true/false/yes/no/on/off`), бо config.json = git-singleton без overlay (`gate_config_singleton`). Інше значення = ValueError на старті; override логується `AGENT_BRIDGE_ENABLED_BY_ENV` |
| `host` / `port` | 127.0.0.1 / 8010 | адреса окремого процесу bridge (S1); до S1 не використовується |
| `data_dir` | /opt/smc-trader-v3/data | каталог даних клієнта, який читає консоль (до S6; після S6 знімається) |
| `smc_snapshot_url` | http://127.0.0.1:8000/api/internal/smc_snapshot | SMC-вхід bridge (S2) |
| `wake_engine.*` | як у ADR-0049/0087 | параметри WakeEngine; секція передається в `WakeEngine(config=…)` напряму |
| `console.enabled` | true | у процесі bridge: маунт `/api/archi/*` (лише при `enabled=true`); `/api/agent/state\|feed` змонтовані завжди, при вимкненій консолі auth fail-closed → 401 |
| `console.auth_token_env` | ARCHI_AUTH_TOKEN | ім'я env-змінної з Bearer-токеном; токен у config.json не зберігається |
| `console.allow_no_token_dev_mode` | false | ADR-0076 F1: enabled без токена = fail-closed |
| `console.thinking_max_items` / `feed_max_items` | 100 / 200 | ліміти пагінації консолі |
| `public_snapshot.enabled` / `ttl_s` | false / 5 | `/api/public/snapshot` (ADR-0086); маунт переїжджає в bridge на S1 |

## Redis

| Ключ | Тип | Опис |
| --- | --- | --- |
| `redis.enabled` | bool | Redis підключення |
| `redis.host` / `port` / `db` | str/int | Адреса Redis |
| `redis.namespace` | str | Префікс ключів (`v3_local`) |
| `redis.allow_env_override` | bool | Дозволити ENV перевизначити Redis параметри |
| `redis.ttl_by_tf_s` | dict | TTL ключів по TF (секунди). M1=86400, M3=86400, M5=259200, D1=604800 |
| `redis.tail_n_by_tf_s` | dict | Скільки барів тримати в Redis tail по TF. M1=10080, M3=3360, M5=10000, D1=2000 |

---

## Redis Priming (coldstart)

| Ключ | Тип | Опис |
| --- | --- | --- |
| `redis_priming_enabled` | bool | Прайм Redis з диску при старті |
| `redis_priming_budget_s` | int | Таймаут на прайм одного символу (default=15) |
| `redis_priming_tfs_s` | int[] | TF для priming (усі від M1 до D1) |
| `redis_priming_symbols` | str[] | Порожній = всі символи |
| `min_coldload_bars_by_tf_s` | dict | Мін кількість барів для UI coldload fallback: M1=1440, M5=2016, D1=365 |

---

## Bootstrap (S4, ADR-0003)

| Ключ | Тип | Default | Опис |
| --- | --- | --- | --- |
| `bootstrap.prime_ready_timeout_s` | int | 120 | Таймаут AND-gate (m1_poller + binance_ingest) перед стартом WS Server |
| `bootstrap.derive_warmup_bars_by_tf` | dict | `{60:10080, 300:20, 900:10, 1800:10, 3600:10}` | Кількість барів з диску для warmup DeriveEngine каскаду |
| `bootstrap.ui_warmup_bars_by_tf` | dict | `{60:500, ..., 86400:200}` | RAM-кеш прогрів UI (symbol × TF) |
| `bootstrap.ui_cold_start_bars_by_tf` | dict | `{60:10080, ..., 86400:365}` | Policy для cold-start UI вікна (передається клієнту) |

---

## Channels (Redis Pub/Sub)

| Ключ | Тип | Опис |
| --- | --- | --- |
| `channels.prefix` | str | Загальний префікс (`fxcm_local`) |
| `channels.ohlcv` | str | Канал OHLCV подій |
| `channels.price_tick` | str | Канал сирих тіків (**увага: `price_tik`** — typo, зберігаємо для сумісності) |
| `channels.status` | str | Канал статусу системи |
| `channels.commands` | str | Канал команд |
| `channels.heartbeat` | str | Канал heartbeat |

---

## Debug / UI

| Ключ | Тип | Опис |
| --- | --- | --- |
| `ui_debug` | bool | Розширене логування UI |
| `group_logs_enabled` | bool | Групувати логи по символу |

---

## Known Broker Outages

| Ключ | Тип | Опис |
| --- | --- | --- |
| `known_broker_outages` | array | Відомі перерви брокера. Дозволяє calendar gate ігнорувати ці gap-и |

Кожен запис: `{ symbol, from_utc, to_utc, reason }`.
