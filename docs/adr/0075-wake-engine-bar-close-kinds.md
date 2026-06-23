# ADR-0075: WakeEngine Bar-Close Kinds (candle_close, +session_close/gap_open)

- **Status**: Accepted — 2026-06-14 (candle_close implemented; session_close/gap_open = next phases)
- **Deciders**: Стас (owner — «finale від брокера це і є закритий бар»), Архі (requested kinds, priority), Claude (R_ARCHITECT)
- **Related**: ADR-0049 (WakeEngine types/check IPC), ADR-040 errata A2 (structure_break event buffer — the pattern this mirrors), ADR-0013b (complete/preview semantics), trader-v3 ADR-078 (Canonical Wake-Params Contract — the consuming contract).

## Quality Axes
- **Ambition**: R3 — закриває реальний семантичний gap (close ≠ touch), розширює WakeEngine акуратним повторним використанням наявного патерну.
- **Maturity**: M3 hold — дзеркало перевіреного structure_events buffer; pure-check лишається pure (S0), contract-test стереже drift.

## 1. Контекст

Архі (consulted) попросив 3 нові wake-kinds, з пріоритетом:
- **candle_close (HIGH)** — «H4 ЗАКРИЛАСЬ вище 4268 = thesis мертвий» — це інше ніж `price_cross` (торкання + відскок). Для invalidation потрібне саме **закриття бару**, а не торкання рівня. Сьогодні ці два стани **неможливо розрізнити** одним kind.
- **session_close (MEDIUM)** — закриття сесії як природна точка переоцінки (зараз — хак через `scheduled` з hardcoded `hour_utc`, ламається на DST/holidays).
- **gap_open (MEDIUM)** — геп при відкритті (неділя 22:00 UTC / news) більший за `min_points` — «розбуди тільки якщо геп > 20п», економить виклик на тихому відкритті.

**Ключовий інсайт (Стас):** «finale від брокера це і є закритий бар». Платформа вже розрізняє final (`complete=true`) і preview бари (I3 Final>Preview, ADR-0013b). Тобто **подія закриття бару вже є first-class сигналом** — не треба інферити закриття зі змін `open_ms`. `cb.complete` у `SmcRunner.on_bar_dict` = момент закриття.

## 2. Альтернативи

- **Alt A — poll last bar в WakeEngine, інферити закриття зі зміни open_ms.** ❌ Перевигадує детекцію закриття; ігнорує наявний `complete`-сигнал; stateful у движку без потреби.
- **Alt B — обробляти candle_close прямо в `on_bar_dict` (event-driven, точно на закритті).** ✓ семантично точно, але `on_bar_dict` не знає про wake-умови (вони в WakeEngine, з Redis) → довелося б тягнути wake-стан у SmcRunner (порушує розділення).
- **Alt C (ОБРАНО) — буфер закритих барів у SmcRunner (як structure_events), WakeEngine drainить + pure `check_condition`.** Дзеркалить перевірений патерн ADR-040 A2 (`_recent_structure_events` + `get_recent_structure_events` + `structure_events` param). Розділення чисте: SmcRunner буферить факти, WakeEngine вирішує, `wake_check` pure. ≤2s лаг (tick) — прийнятно для торгівлі.

## 3. Рішення

**candle_close (implemented):**
1. **`SmcRunner._recent_bar_closes`** — per-symbol буфер; у `on_bar_dict` коли `cb.complete` → append `{tf_s, close, open_time_ms, ts_ms=close_time_ms}`, FIFO cap 100. Дзеркало structure_events buffer.
2. **`SmcRunner.get_recent_bar_closes(symbol, since_ts_ms)`** — drain (thread-safe), фільтр `ts_ms >= since`.
3. **`WakeEngine._tick_symbol`** — drainить bar_closes `since_ts_ms=last_wake` (як structure_events) → передає `bar_close_events` у `check_condition`.
4. **`wake_check.check_condition`** — новий `bar_close_events` param + гілка `CANDLE_CLOSE`: для кожної close-події з matching `tf_s`, якщо `direction=above` і `close >= level` (або `below` і `close <= level`) → fire. **Pure** (S0): дані-всередину → bool-назовні.
5. **`wake_types.WakeConditionKind.CANDLE_CLOSE`** = `"candle_close"`.
6. **Контракт (trader-v3 `wake_params`)** — canonical `{level, tf_s, direction}`; `candle_close` у `PLATFORM_SUPPORTED`; contract-test (drift-gate) звіряє canonical ↔ `wake_check` p.get-ключі.

**Дедуп / fire-once:** drain `since last_wake` + наявний event_dedup cooldown (ADR-037) → candle_close fire один раз на кваліфіковане закриття, без спаму щотіку.

**session_close / gap_open (next phases, цей же ADR):**
- **session_close** — WakeEngine трекає попередню сесію per symbol (`_get_session_info` вже дає `current_session`); fire при переході target→інша. Stateful у движку, без нового SmcRunner-буфера.
- **gap_open** — WakeEngine має `_prev_prices` + `is_open` (calendar); при переході `is_open` false→true виміряти `|price - last_pre_close|` ≥ `min_points` → fire.
Доки не імплементовано — контракт тримає їх `PLATFORM_PENDING` (ack каже «pending», не fake-armed; ADR-078 §4).

## 4. Consequences
- **+** Архі розрізняє close vs touch — invalidation нарешті виразна (його HIGH gap закрито).
- **+** Нуль нового I/O-шляху — bars вже течуть через `on_bar_dict`; буфер $0, drain $0, check $0.
- **+** Pure `check_condition` + contract-test → drift схеми↔платформи неможливий.
- **−** ≤2s лаг (WakeEngine tick) між закриттям бару і fire — прийнятно (vs sleep-cycle сьогодні).
- **−** Ще один per-symbol буфер у SmcRunner (cap 100) — мітигація: FIFO, малий, thread-safe.

## 5. Rollback
- Видалити гілку `CANDLE_CLOSE` з `wake_check` + drain з `wake_engine` + буфер з `smc_runner` → поведінка як до 0075 (kind просто не fired). Contract-test поверне candle_close у PENDING (move з SUPPORTED).
- Жодних persisted-даних / schema-міграцій — буфер in-memory ephemeral (S1).

## 6. Cross-repo
Платформна частина — у v3 (`core/smc`, `runtime/smc`). Контракт-дзеркало — trader-v3 `wake_params` (canonical keys) + emit enum. X31 збережено: жодного runtime-import між репо; синхронність гарантує trader-v3 contract-test (читає v3 `wake_check` як текст). Коли v3 додав kind — trader-v3 move PENDING→SUPPORTED + `_ENUM_TO_KIND` мапа тесту.
