# ADR-0011: SSOT Broadcast Serialization

- **Статус**: Implemented
- **Дата**: 2026-02-24
- **Автор**: AI-assisted discovery + expert review
- **Initiative**: `ws_performance_v1`

## Контекст і проблема

Попередня реалізація `ws_server.py` (aiohttp) створювала окрему фонову задачу
`_delta_loop` для **кожного підключеного клієнта**. O(N) зростання по всіх вісях:

**Симптоми при масштабуванні (50+ трейдерів):**

1. **CPU/IO Overhead**: N клієнтів = N паралельних `_uds_read_updates` через thread pool,
   N× побудова frame через `_build_delta_frame`, N× неявний `json.dumps()` через `send_json`.
2. **Мережева затримка**: Event Loop перевантажувався дубльованою роботою,
   клієнти отримували дані розсинхронізовано.
3. **Dictionary allocation**: Інтенсивна I/O та серіалізація уповільнювала Event Loop.

### Discovery (факти з коду, BEFORE)

| # | Факт | Файл:рядок (до рефактору) |
|---|------|---------------------------|
| F1 | Per-session delta task при з'єднанні | `ws_server.py:568` — `ensure_future(_delta_loop(session, app))` |
| F2 | Per-session delta task при switch | `ws_server.py:668` — аналогічний `ensure_future` |
| F3 | Per-session UDS read | `ws_server.py:445` — `_uds_read_updates(...)` через `run_in_executor` |
| F4 | Per-session frame build | `ws_server.py:491` — `_build_delta_frame(...)` з нуля кожен poll |
| F5 | Per-session serialization | `ws_server.py:495` — `send_json(frame)` → неявний `json.dumps()` |

## Розглянуті варіанти

1. **Глобальний поллер + SSOT Pre-serialization** — один UDS read per key,
   одна серіалізація, broadcast по `send_str`. **✅ Обрано.**
2. **`websockets.broadcast`** — не підходить (проєкт на `aiohttp`, не на `websockets`).
3. **Delta compression (тільки змінені поля)** — YAGNI при 2s інтервалі.
4. **Глобальні курсори `app["_ws_cursors"]`** — розглядалось, але відхилено
   на користь per-session `last_update_seq` + `min_seq` агрегації (зберігає
   adopt-tail семантику для нових/switched клієнтів).

## Рішення (реалізовано)

### Архітектура (BEFORE → AFTER)

```
BEFORE (per-session, O(N)):            AFTER (global fanout):
┌────────────────────────────┐         ┌─────────────────────────────────────┐
│ client_1 → _delta_loop_1   │         │ _global_delta_loop  (один таск)    │
│   └→ _uds_read_updates     │         │   ├→ group by (sym, tf)            │
│   └→ _build_delta_frame    │         │   ├→ min_seq → _uds_read ×1/key   │
│   └→ json.dumps + send     │         │   ├→ build ×1/key + per-client seq │
│ client_2 → _delta_loop_2   │         │   └→ _safe_broadcast (send_str)   │
│   └→ ...  (×N)             │         └─────────────────────────────────────┘
│ client_N → _delta_loop_N   │         UDS read + build + dumps: O(unique_keys)
│   └→ ...  (×N)             │         send_str:   O(clients)  ← фізична межа
└────────────────────────────┘
```

### Ключові елементи реалізації

1. **`_global_delta_loop(app)`**: Один asyncio таск, poll кожні `DEFAULT_DELTA_POLL_S`
   (2.0s). Групує активні сесії по `(symbol, tf)`.

2. **min_seq cursor**: Per key `(symbol, tf)` береться `min(session.last_update_seq)`
   серед підписників. UDS опитується один раз з цим курсором. Після poll
   **всі active recipients** отримують однаковий `cursor` — курсори ре-синхронізуються.

3. **Adopt-tail**: Клієнт з `last_update_seq=None` (новий або після switch)
   отримує `cursor` без включення в `active_recipients`. Повний фрейм він уже
   отримав через `_send_full_frame()`. На наступному poll він стає active.

4. **`_safe_broadcast()`**: Кожен `send_str` обгорнутий у `asyncio.wait_for(timeout=1.0s)`.
   Timeout або Exception → `WS_BROADCAST_ERR` warning + cleanup + close.

5. **`forming_by_target` purge**: Після кожного циклу ключі без підписників
   видаляються з `forming_by_target` (запобігання memory leak).

### Cursor ідемпотентність

**Контракт**: UI фронтенд (`engine.ts:406`) де-факто ідемпотентний по `t_ms`:
`deduped.set(c.t_ms, c)` — повторні candles з тим самим `t_ms` перезаписуються.

**Чому min_seq безпечний**: Курсори ре-синхронізуються після кожного poll
(всі active → `cursor`). Розходження можливе лише для щойно підключених клієнтів,
які проходять adopt-tail і не отримують delta до ре-синхронізації.

### `meta.seq` у broadcast frames

**Всі** frames (delta, heartbeat, full, scrollback, config) використовують
per-connection `session.next_seq()`. `_safe_broadcast()` інжектить seq для
кожного клієнта перед `json.dumps()` — це забезпечує W2 (строга монотонність
per-connection) та сумісність з `frameRouter.ts` stale-frame guard.

> **Історична примітка**: Перша реалізація ADR-0011 використовувала `time_ms`
> як seq для broadcast delta (єдина серіалізація). Це зламало UI stale-frame
> detection: після delta з seq≈1.77T всі per-session frames (seq=35..N)
> відкидались як stale. Виправлено на per-client seq + per-client serialization.

### Slow-client policy (виправлено)

**`_safe_broadcast()`** (`BROADCAST_SEND_TIMEOUT_S = 1.0`):

```python
async def _guarded_send(s):
    frame["meta"]["seq"] = s.next_seq()
    payload = json.dumps(frame)
    await asyncio.wait_for(s.ws.send_str(payload), timeout=timeout_s)
```

- Timeout per client → **не блокує** інших subscribers
- Кожен Exception → `WS_BROADCAST_ERR client_id=... reason=timeout|ConnectionReset`
- Cleanup: `sessions.pop()` + `ws.close()`
- Відповідає I5 (degraded-but-loud): жодних silent failures

## Інваріанти (контракти)

| # | Інваріант | Реалізація |
|---|-----------|------------|
| BC1 | Payload-per-client | Frame dict build O(1), `json.dumps()` per client з per-client seq |
| BC2 | json.dumps kwargs | Стандартні aiohttp defaults (без пробілів, ascii-safe) |
| BC3 | Snapshot-before-send | `subscribers = tuple(group_sessions)` до `await` |
| BC4 | Ordering | UDS cursor → строга монотонність per `(symbol, tf)` |
| BC5 | Slow-client timeout | `wait_for(send_str, 1.0s)` per recipient; timeout→close |
| BC6 | Degraded-but-loud (I5) | `WS_BROADCAST_ERR` warning + cleanup. Без silent close |
| BC7 | UI ідемпотентність | `engine.ts` dedup по `t_ms`; повторні candles безпечні |
| BC8 | Memory purge | `forming_by_target` purge для ключів без підписників |

## Наслідки

### Що змінилось

| Компонент | Зміна |
|-----------|-------|
| `_global_delta_loop()` | Новий; замінює per-session `_delta_loop` |
| `_safe_broadcast()` | Новий; per-client `wait_for` + degraded-but-loud |
| `WsSession` | Видалено `delta_task`; `last_update_seq` залишається per-session |
| `ws_handler` | Видалено `ensure_future(_delta_loop)` |
| `_handle_switch` | Видалено delta_task management (тільки reset cursor) |
| `build_app()` | `on_startup`/`on_cleanup` для global delta task |

### Що НЕ змінилось

- Full frame, scrollback, heartbeat, config — per-session як раніше
- UDS інтерфейс (`read_updates`, `read_window`) — без змін
- Candle mapping (`map_bars_to_candles_v4`) — без змін
- Dedup логіка (`seen_events`, final>preview) — перенесена as-is в global loop

### Складність (complexity budget)

| Метрика | Значення |
|---------|----------|
| UDS read + build | O(unique_keys) — **не залежить від N клієнтів** |
| send_str broadcast | O(clients) — **фізична межа, не оптимізується** |
| Memory per key | 1 frame dict (serialization per-client за рахунок W2 seq) |

## План верифікації (VERIFY)

1. **`t_ser_ms`**: Замір від UDS read до frame dict build per key. O(1) відносно N.
2. **`t_send_ms`**: Замір `asyncio.gather`. Стабільний при зростанні N.
3. **Slow-client test**: Підключити клієнт з штучною затримкою >1s →
   має отримати `WS_BROADCAST_ERR reason=timeout` + disconnect.
4. **Adopt-tail test**: Client A підключений → Client B підключається →
   B отримує full frame, потім бере participate у delta без дублів.
5. **W2 invariant**: ВСІ frames (включно з broadcast delta) мають per-connection `session.seq`.
6. **Лог**: `WS_BROADCAST_METRICS sym=... tf=... subs=N t_ser_ms=... t_send_ms=...`

## Rollback

1. `git revert` комміту (повернення до per-session `_delta_loop`).
2. Рестарт процесу: `supervisorctl restart ws_server` або manual restart.
3. Перевірити delta frames per-client (лог `WS_DELTA_PUSH`).

Складність rollback: **низька** — один файл (`ws_server.py`), чисте revert.

---

## Доповнення 1: Split-brain TF Bug (2026-02-24)

Під час впровадження глобального fanout було виявлено баг: при перемиканні таймфрейму (TF) HUD починав показувати, наприклад, `1D`, тоді як сам графік залишався на `1M` або `5M`. Відбувалася розсинхронізація (split-brain) між візуальними компонентами UI.

### Причини (Failure Model)

1. **Backend Leak (`ws_server.py`)**: Всередині ітерації `_global_delta_loop` по унікальним цілям `(symbol, tf)`, змінна `frame` **не обнулялася** на початку циклу. Якщо для якоїсь пари (наприклад, 1D з tick-relay) генерувався новий `frame`, а наступна пара (наприклад, 1M) не мала нових подій з UDS, старий `frame` від 1D помилково розсилався підписникам 1M (Cross-pollution).
2. **UI Split-brain (`ui_v4`)**: Компонент `ChartHud` відстежував TF пасивно — просто читаючи `symbol` та `tf` з будь-якого останнього отриманого фрейму, без наявності Єдиного Джерела Істини (SSOT). Коли приходив "течучий" бекенд-фрейм з TF `1D`, HUD оновлювався, а графік (який має свою логіку відмальовування) ігнорував його або відмальовував неправильно.

### Впроваджене рішення та обґрунтування

1. **Backend Isolation (`ws_server.py`)**: 
   - **Дія**: Додано явне `frame = None` на рівні кожної ітерації `(symbol, tf)`.
   - **Обґрунтування**: Змінні не мають витікати між різними підписниками. Кожен розрахунок `payload` має бути на 100% герметичним.
2. **UI SSOT (`frameRouter.ts` + `App.svelte`)**:
   - **Дія**: Додано новий стор `export const currentPair = writable<{ symbol, tf } | null>(null)` у роутер. Він заповнюється **виключно** при отриманні валідного `full` фрейму. HUD тепер підписаний тільки на цей SSOT.
   - **Обґрунтування**: Iнваріант F1 (Single Source of Truth) вимагає, щоб UI мав лише одне джерело стану поточного активу. Пасивне читання з потоку фреймів порушує цей принцип.
3. **Split-brain Guard (`frameRouter.ts`)**:
   - **Дія**: Додано перевірку: якщо прилітає `delta` або `scrollback` фрейм, чиї `symbol` або `tf` не співпадають з `currentPair`, фрейм негайно **відкидається** з логуванням попередження (`schema_mismatch`).
   - **Обґрунтування**: Інваріант I5 (Degraded-but-Loud). Замість мовчазного показу неправильного стану (split-brain), краще явно дропнути невалідний фрейм і залогувати помилку. Сервер ніколи не повинен надсилати дельти з чужого TF підписникам.
