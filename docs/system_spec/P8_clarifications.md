# P8 — Verification & Clarifications Pack

> **Дата**: 2026-02-21  
> **Mode**: DISCOVERY (read-only)  
> **Preflight**: `docs/system_current_overview.md` та `research/ПОВНИЙ АУДИТ AS-IS + TO-BE ADR + ПЛАН.md` звірені  
> **Exit criteria**: Всі Q1–Q10 мають file:line evidence + класифікація (A)/(B)/(C)

---

## Зміст

1. [Q1: Preview TTL — curr vs tail](#q1-preview-ttl--curr-vs-tail)
2. [Q2: Silent tick drops + watchdog](#q2-silent-tick-drops--watchdog)
3. [Q3: I4 — один update-потік vs dual streams](#q3-i4--один-update-потік-vs-dual-streams)
4. [Q4: tick_promoted vs FINAL_SOURCES](#q4-tick_promoted-vs-final_sources)
5. [Q5: Dedup semantics — DiskLayer vs UDS](#q5-dedup-semantics--disklayer-vs-uds)
6. [Q6: WindowSpec.cold_load vs ReadPolicy.disk_policy truth table](#q6-windowspeccold_load-vs-readpolicydisk_policy-truth-table)
7. [Q7: Stitching на preview overlay](#q7-stitching-на-preview-overlay)
8. [Q8: _normalize_bar default complete=True](#q8-_normalize_bar-default-completetrue)
9. [Q9: Live process watchdog](#q9-live-process-watchdog)
10. [Q10: Redis key normalization — raw vs symbol_key()](#q10-redis-key-normalization--raw-vs-symbol_key)
11. [GO/NO-GO verdict](#gono-go-verdict)
12. [Зведена таблиця класифікації](#зведена-таблиця-класифікації)

---

## Q1: Preview TTL — curr vs tail

### Q1.1: Де встановлюється TTL на preview:curr?

**Факт**: TTL проходить через 3 точки; **жодна не є SSOT**:

| Точка | Значення | file:line | Роль |
|-------|----------|-----------|------|
| config.json | **1800** | `config.json:71` | SSOT config intent |
| UDS hardcode | **120** | `runtime/store/uds.py:44` (`PREVIEW_CURR_TTL_S = 120`) | Default у UDS constructor |
| tick_preview_worker fallback | **60** | `runtime/ingest/tick_preview_worker.py:114` (`cfg.get("preview_curr_ttl_s", 60)`) | Worker fallback |

**Call chain (runtime)**:

1. `tick_preview_worker.py:114` — worker парсить `cfg.get("preview_curr_ttl_s", 60)` → **при наявності config.json отримує 1800**.
2. Worker зберігає `self._curr_ttl_s = 1800` (`tick_preview_worker.py:173`).
3. `_publish_bar()` (`tick_preview_worker.py:403`) → `self._uds.publish_preview_bar(bar, ttl_s=self._curr_ttl_s)` → **передає ttl_s=1800**.
4. `uds.py:781` — `ttl_curr = ttl_s if ttl_s is not None else self._preview_curr_ttl_s` → **отримує 1800** від worker, hardcode 120 ігнорується.
5. `redis_layer.py:43` — `self._client.set(key, raw, ex=int(ttl_s))` → **Redis SET ... EX 1800**.

**Висновок Q1.1**: У production runtime TTL preview:curr = **1800s** (від config через worker). Hardcode 120 в uds.py:44 **реально не використовується** для tick_preview_worker (бо worker передає ttl_s явно). Hardcode використовується лише якщо хтось викликає `publish_preview_bar()` **без** ttl_s (напр. engine_b.py:458 — `self._uds.publish_preview_bar(bar)` без ttl_s → fallback до 120s).

**Додаткова знахідка**: `engine_b.py:458` — `self._uds.publish_preview_bar(bar)` **без ttl_s** → потрапляє в uds.py:781 → `ttl_curr = None → self._preview_curr_ttl_s = 120`. Це 2-й шлях з іншим TTL.

### Q1.2: Чи має TTL preview:tail?

**Факт**: **Ні**. `redis_layer.py:84` — `write_preview_tail()` викликає `self._write_json(key, payload, None)` → `self._client.set(key, raw)` без `ex=`. Також в `uds.py:812–821` — tail payload пишеться через `self._redis.write_preview_tail()` без TTL.

**Документація підтверджує**: `P3_uds_store.md:685` — "no TTL, max 2000". `P1_process_inventory.md:728` — "—" у колонці TTL.

**Висновок Q1.2**: Preview tail **не має TTL** — тільки trim до 2000 при кожному write. Це коректно за дизайном — tail є append-only ring buffer.

### Q1.3: Хто SSOT для TTL?

**Факт**: `build_uds_from_config()` (`uds.py:2008–2094`) **не передає** `preview_curr_ttl_s` в UDS constructor. Конструктор UDS приймає `preview_curr_ttl_s: int = PREVIEW_CURR_TTL_S` (L299), але build_uds не використовує цей параметр → завжди default **120**.

Worker (`tick_preview_worker.py:114`) читає config і передає TTL як call-time parameter (`ttl_s=self._curr_ttl_s`). Тому:

- **UDS internal**: self._preview_curr_ttl_s = 120 (hardcode)
- **Worker override at publish-time**: ttl_s = 1800 (config)
- **engine_b (без ttl_s)**: fallback → 120

### Класифікація Q1: **(C) — Реалізація суперечить документам і між собою**

**SSOT зламаний**: 3 місця з 3 різними значеннями (1800/120/60). Runtime результат залежить від caller (1800 для tick_preview, 120 для engine_b). `build_uds_from_config()` не прокидує config → UDS.

**Потрібен PATCH (S1)**: Wire `preview_curr_ttl_s` від config через `build_uds_from_config()` до UDS constructor. Видалити fallback 60 у worker.

---

## Q2: Silent tick drops + watchdog

### Q2.1: Periodic emission стану tick_agg

**Факт**: `TickAggregator` (tick_agg.py) має internal counters:

- `ticks_total`, `ticks_rejected_tf`, `ticks_dropped_late_bucket`, `ticks_dropped_before_open`, `ticks_dropped_out_of_order`, `promoted_total` — `tick_agg.py:66–72`
- `.stats()` method повертає dict — `tick_agg.py:74`

**Хто логує**: `TickPreviewWorker._maybe_emit_stats()` (`tick_preview_worker.py:204–226`) — кожні 60 секунд (**тільки** worker stats, **не** tick_agg.stats()):

```python
logging.info("TICK_PREVIEW_STATS %s", json.dumps(payload, ensure_ascii=False))
```

Worker має свої лічильники (`_stats dict`): `ticks_in_total`, `ticks_dropped_schema`, `ticks_dropped_price`, `ticks_dropped_calendar_closed`, `preview_publish_total`, `preview_gap_total`, `promoted_publish_total` тощо.

**Критичний gap**: Worker stats **не включають** tick_agg drop counters (`_dropped_late_bucket`, `_dropped_before_open`, `_dropped_out_of_order`). Tick_agg counters **залишаються тільки в пам'яті об'єкта** і ніколи не логуються/публікуються.

**Чи є в /api/status**: Ні. `/api/status` endpoint (`server.py:~1040`) повертає Redis status, але **не** tick_agg метрики.

### Q2.2: Watchdog "0 тіків N секунд"

**Факт**: **Є часткова реалізація** в `TickPreviewWorker`:

```python
# tick_preview_worker.py:155
_ZERO_TICKS_WARN_INTERVAL_S = 120

# tick_preview_worker.py:206-217
silence_s = now - self._last_tick_rx_ts
if silence_s > self._ZERO_TICKS_WARN_INTERVAL_S:
    if not self._zero_ticks_warned:
        logging.warning(
            "TickPreview: 0 тиків вже %.0f с (канал=%s) — можливо ринок закритий або channel невірний",
            silence_s, self._channel,
        )
        self._zero_ticks_warned = True
```

- Тригер: 120s без тіків → **один** warning (one-shot, `_zero_ticks_warned` flag).
- Не враховує market calendar (тригер і в неділю).
- Не публікується в `degraded[]` або `/api/status`.
- Watchdog перевіряється тільки в `_maybe_emit_stats()` (кожні 60s), тобто перша перевірка через 60s після тиші.

### Q2.3: Хто формує degraded[] при ingest зависанні?

**Факт**: **Ніхто**. `degraded[]` формується тільки на рівні UDS `read_updates`/`read_window` для Redis-down / geom_fix / prime-not-ready сценаріїв:

- `uds.py:530` — `["preview_requires_redis"]`
- `uds.py:543/582` — `["redis_down"]`
- `uds.py:903` — `["geom_non_monotonic"]`

**Але**: якщо tick_preview_worker або m1_poller **зависли** (процес живий, але не продукує дані) — **немає механізму** який би поставив `degraded[]` в API responses. UI показуватиме stale дані без loud сигналу.

### Класифікація Q2: **(C) — Реалізація суперечить інваріантам I5/I6**

**Проблеми**:

1. tick_agg drop counters — **SILENT** (не логуються, не в status). Порушує I5.
2. "0 тіків" watchdog — **частковий** (one-shot warning, не calendar-aware, не в degraded[]).
3. Ingest зависання → **немає degraded-but-loud** в UI.

**Потрібен PATCH (S2)**: емітувати tick_agg.stats() разом з worker stats; при drop_rate > threshold → WARNING (rate-limited); при silence > N → degraded[].

---

## Q3: I4 — один update-потік vs dual streams

### Q3.1: Один endpoint чи два?

**Факт**: `/api/updates` — **один** endpoint, але з **двома** backend шляхами в UDS:

```
# uds.py:515
preview_mode = tf_s in self._preview_tf_allowlist

if preview_mode:
    # → redis.read_preview_updates()     [redis_layer.py:97-157]
    #   Redis list: {ns}:preview:updates:{symbol_key}:{tf_s}:list
else:
    # → self._updates_bus.read_updates() [uds.py:1917-1974]
    #   Redis list: {ns}:updates:list:{symbol}:{tf_s}
```

UI вирішує по TF:

- **M1, M3** (preview TFs) → Preview Ring path
- **M5-D1** (final TFs) → UpdatesBus path

Обидва повертають однакову структуру `UpdatesResult` з `events[]`, `cursor_seq`, `meta.extensions.plane`.

### Q3.2: Bridge `_publish_final_to_preview_ring` — best-effort чи гарантія?

**Факт**: Best-effort з LOUD warning при fail.

```python
# uds.py:934-967
def _publish_final_to_preview_ring(self, bar: CandleBar) -> bool:
    if self._redis is None:
        return False
    try:
        ...
        self._redis.publish_preview_event(bar.symbol, ...)
        return True
    except Exception as exc:
        Logging.warning(
            "UDS: final→preview ring publish failed symbol=%s tf_s=%s err=%s", ...)
        return False
```

**При fail/lag**: Final бар **записаний на диск і Updates Bus**, але **не потрапив** у Preview Ring. M1/M3 UI буде бачити preview (tick) дані замість final. Final>preview promotion відбудеться при наступному successful bridge.

**Немає retry/queue**: publish — one-shot. Якщо Redis тимчасово недоступний, бар пропущений у preview ring (але є на диску).

### Q3.3: Чи є тест для розсинхрону?

**Факт**: Так, **є** exit gate `gate_ui_live_candle_plane.py` (EG15):

- `tools/exit_gates/gates/gate_ui_live_candle_plane.py:54` — перевіряє що `publish_preview_bar` є у UDS source.
- `tools/exit_gates/gates/gate_preview_plane.py:299` — перевіряє що `read_updates` для preview TF ходить ТІЛЬКИ в preview-bus (не в UpdatesBus).
- `tools/exit_gates/gates/gate_api_updates_contract.py:87` — перевіряє final_sources consistency.

**Але**: ці gates — **статичний** аналіз коду (AST). Немає **runtime** тесту який би виявив "UpdatesBus ok, Preview Ring stale" при Live операції.

### Семантика контракту для M1/M3 в live

| Фаза | Authoritative source для UI |
|-------|---------------------------|
| Тіки → tick preview | Preview Ring (`preview_tick`, complete=false) |
| Rollover → tick promoted | Preview Ring (`tick_promoted`, complete=true) |
| History → final | Preview Ring (через bridge) + UpdatesBus (через _publish_update) |

**Факт**: Final M1/M3 бар публікується в **обидва** канали: UpdatesBus (через `_publish_update`, uds.py:1276) **і** Preview Ring (через `_publish_final_to_preview_ring`, uds.py:670). Але UI для M1/M3 читає **тільки** Preview Ring через `/api/updates`. UpdatesBus events для M1/M3 **ніколи не віддаються UI** (preview_mode=True перемикає на preview path).

### Класифікація Q3: **(B) — Документи суперечать одне одному**

**Суть**: I4 декларує "один update-потік", але фактична архітектура — **два backend канали з routing по TF**. Це **конструкція by design** (Preview Ring + UpdatesBus), а не баг. Але інваріант I4 не описує routing mechanism.

**Рекомендація для документів**: Уточнити I4:
> "Один endpoint `/api/updates`, два backend planes: Preview Ring (M1/M3) та UpdatesBus (M5-D1). Routing по `tf_s ∈ preview_tf_allowlist`. Bridge забезпечує final>preview для preview TFs."

**GO (документ update, не PATCH).**

---

## Q4: tick_promoted vs FINAL_SOURCES

### Q4.1: publish_promoted_bar() → commit_final_bar()?

**Факт**: **Ні**. `publish_promoted_bar()` **не** викликає `commit_final_bar()`.

Call graph:

```
tick_preview_worker.py:350  → self._uds.publish_promoted_bar(promoted)
    uds.py:675              → publish_promoted_bar(bar)
        uds.py:686          → bar.src != "tick_promoted" → return False (guard)
        uds.py:695          → bar_payload = bar.to_dict()
        uds.py:704          → event = {key, bar, complete:True, source:"tick_promoted"}
        uds.py:710          → self._redis.publish_preview_event() → Redis preview list
        uds.py:717          → return True
```

**Promoted бар пише ТІЛЬКИ в Preview Ring** — Redis list `{ns}:preview:updates:{sk}:{tf_s}:list`. Він **не** йде на диск, **не** в UpdatesBus, **не** в Redis tail/snap.

### Q4.2: Чи зберігається promoted бар на диск?

**Факт**: **Ні**. Promoted бар (`src="tick_promoted"`) **не** проходить через `commit_final_bar()`. `FINAL_SOURCES = {"history", "derived", "history_agg"}` — `tick_promoted` **не** в цьому наборі.

Навіть якщо хтось спробує `commit_final_bar(promoted_bar)`:

```python
# uds.py:641
if bar.src not in FINAL_SOURCES:
    return CommitResult(False, "non_final_source", ...)
```

→ буде reject.

### Q4.3: Де визначений FINAL_SOURCES?

**Факт**: В **трьох** місцях (відомий P5-F1 finding):

| # | file:line | Значення |
|---|-----------|----------|
| 1 | `runtime/store/uds.py:42` | `{"history", "derived", "history_agg"}` |
| 2 | `runtime/store/layers/disk_layer.py:~10` | `{"history", "derived", "history_agg"}` |
| 3 | `runtime/store/ssot_jsonl.py:~12` | `{"history", "derived", "history_agg"}` |

**Всі три ідентичні**. `tick_promoted` не входить ні в один.

### Класифікація Q4: **(A) — Документи праві, реалізація відповідає**

**Архітектурне рішення коректне**:

- `tick_promoted` = "mock final" для UI (швидкий візуальний complete до приходу History final).
- Живе тільки в Preview Ring (Redis, ephemeral).
- Справжній History final приходить через M1 poller → `commit_final_bar()` → disk + UpdatesBus + Bridge → Preview Ring.
- UI `applyUpdates` реалізує `_isAllowedSourceUpgrade()` (`app.js:1665–1670`): `tick_promoted→history` = дозволений upgrade.

**Ніякого ADR не потрібно.** tick_promoted правильно не входить в FINAL_SOURCES.

---

## Q5: Dedup semantics — DiskLayer vs UDS

### Q5.1: Чи однакова семантика `_choose_better_bar`?

**Факт**: **Майже однакова, але не ідентична**.

**UDS версія** (`uds.py:1741–1757`):

```python
def _choose_better_bar(existing, incoming):
    # 1. incoming complete > existing incomplete → incoming
    # 2. existing complete > incoming incomplete → existing
    # 3. incoming final_source > existing non-final → incoming
    # 4. existing final_source > incoming non-final → existing
    # 5. tie → return existing  ← !!!
```

**DiskLayer версія** (`disk_layer.py:174–199`):

```python
def _choose_better_bar(existing, incoming):
    # 1. incoming complete > existing incomplete → incoming
    # 2. existing complete > incoming incomplete → existing
    # 3. incoming final_source > existing non-final → incoming
    # 4. existing final_source > incoming non-final → existing
    # 5. Compare event_ts/ssot_write_ts_ms (newer wins) ← !!!
    # 6. tie → return incoming  ← !!!
```

**Дві ключові різниці**:

| Критерій | UDS (uds.py) | DiskLayer (disk_layer.py) |
|----------|-------------|--------------------------|
| Timestamp priority | **Відсутня** | event_ts → ssot_write_ts_ms (newer wins) |
| Tie-break | `return existing` (first wins) | `return incoming` (last wins) |

### Q5.2: Чи є unit test що порівнює?

**Факт**: **Ні**. Немає тесту який передає однакові кейси обом функціям і порівнює результат.

### Вплив

**Сценарій розбіжності**: два final бари з однаковим open_ms, обидва complete=True, обидва `src ∈ FINAL_SOURCES`, але різний event_ts:

- DiskLayer: обере бар з **newer** event_ts.
- UDS: обере **existing** (перший зустрічений).

На практиці це мало ймовірно (watermark drop запобігає duplicate commit), але теоретично можливо при recovery/rebuild.

### Класифікація Q5: **(C) — Реалізація суперечить (між компонентами)**

**Два dedup-шляхи мають різну семантику** при equal-priority bars. Для production це LOW risk (watermark захищає), але для correctness і детермінізму це gap.

**Рекомендація**: Об'єднати в одну canonical `_choose_better_bar()` (можливо в `core/`) і використовувати її з обох місць.

---

## Q6: WindowSpec.cold_load vs ReadPolicy.disk_policy truth table

### Q6.1: Де і як map-иться?

**Факт**: `read_window()` (`uds.py:364–490`) має каскадну логіку:

```
1. force_disk=True → DISK (bypass все)
2. cold_load=True + prefer_redis=True → REDIS (якщо хоча б min bars)
3. RAM hit → RAM
4. RAM miss → check _disk_allowed(policy, "ram_miss")
5. Range query → check _disk_allowed(policy, "range_query")
```

`_disk_allowed()` (`uds.py:1304–1337`):

```python
if dp == "explicit" → True
if dp == "bootstrap" → True якщо elapsed <= 60s
else → False (+ DISK_HOTPATH_BLOCKED log)
```

### Q6.2: Truth table

UI Server встановлює policy так (`server.py:~1285-1345`):

- `/api/bars` cold_load: `cold_load=True`, `prefer_redis=True`, `disk_policy="bootstrap"`
- `/api/bars` range (scrollback): `cold_load=False`, `disk_policy="explicit"`

| cold_load | force_disk | prefer_redis | disk_policy | Bootstrap <60s | Результат |
|-----------|-----------|-------------|-------------|---------------|----------|
| **True** | False | True | bootstrap | Yes | Redis → fallback до disk |
| **True** | False | True | bootstrap | No | Redis → RAM → **BLOCKED** (degraded) |
| **True** | False | True | never | — | Redis → RAM → **BLOCKED** |
| **True** | False | True | explicit | — | Redis → fallback до disk |
| False | **True** | — | — | — | **DISK** (forced) |
| False | False | — | bootstrap | Yes | RAM → fallback disk |
| False | False | — | bootstrap | No | RAM → **BLOCKED** |
| False | False | — | explicit | — | RAM → fallback disk |
| False | False | — | never | — | RAM → **BLOCKED** |

### Конфлікти/невідповідності

**Дозволених комбінацій**: 6 зі стандартних 9 дають корисний результат. **Заборонених**: немає formal reject. 3 комбінації ведуть до degraded (порожні бари + warnings), що коректно.

**Спірний сценарій**: `cold_load=True, disk_policy="bootstrap"`, але bootstrap вікно (60s) минуло. UI буде показувати часткові дані з RAM. Це by design — после bootstrap disk hot-path заблокований (I5).

### Класифікація Q6: **(A) — Документи праві, реалізація відповідає**

Truth table формалізована. Немає логічних конфліктів. Єдина зауваження: `BOOTSTRAP_WINDOW_S = 60` — hardcode в `uds.py:238` (не в config). Це LOW finding (P3-F4 вже зафіксовано).

---

## Q7: Stitching на preview overlay

### Q7.1: Де застосовується stitching?

**Факт**: Stitching застосовується в `server.py` для `/api/bars` та `/api/latest`:

```python
# server.py:1343-1346
_stitching_on = bool(cfg.get("ui_stitching_enabled", False))
out_bars = _stitch_bars_previous_close(hist_bars) if _stitching_on else hist_bars
if _stitching_on:
    meta.setdefault("extensions", {})["stitching"] = True
```

Також в `/api/latest` (`server.py:1428-1432`) — аналогічна логіка.

**Stitching НЕ застосовується для** `/api/updates` та `/api/overlay`.

### Q7.2: Чи є guard для market boundaries?

**Факт**: **Ні**. `_stitch_bars_previous_close()` (`server.py:1229–1253`) — простий цикл:

```python
for i in range(1, len(bars)):
    prev_close = bars[i-1].get("close") or bars[i-1].get("c")
    curr_open = curr.get(open_key)
    if abs(curr_open - prev_close) > 0.0001:
        bars[i][open_key] = prev_close
        # Adjust high/low if needed
```

**Немає**: перевірки weekend gap, session boundary, market close/open часу. Stitching працює **безумовно** на всіх послідовних барах.

### Q7.3: Чи stitching увімкнений в production?

**Факт**: **Так**. `config.json:73` — `"ui_stitching_enabled": true`.

### Вплив

Stitching **приховує** реальні market gaps (weekend, daily break, news gap) — `bars[i].open = bars[i-1].close` для будь-якого розриву > 0.0001. Це TV-like поведінка (TradingView робить так само), але:

- Gap через weekend: close п'ятниці 2850.00, open понеділка 2855.00 → UI покаже open=2850.00 замість реального 2855.00.
- Це **тільки display** (SSOT на диску не модифікується).
- `meta.extensions.stitching = true` — **loud** (документовано).

### Класифікація Q7: **(B) — Документи недостатньо описують обмеження**

**Реалізація коректна** за задокументованим дизайном (TV-like PREVIOUS_CLOSE). Але документи **не описують** обмеження: stitching без calendar-awareness може дезорієнтувати при аналізі gap-ів.

**Рекомендація**: Додати в `P4_api_surface.md` або `guide_candles.md`:
> "Stitching замикає ВСІ gaps, включаючи weekend/session breaks. Для gap-аналізу (SMC FVG) потрібно використовувати raw SSOT (ui_stitching_enabled=false або direct disk read)."

**GO (документ update, не PATCH).**

---

## Q8: _normalize_bar default complete=True

### Q8.1: Які шляхи повертають бар без поля complete?

**Факт**: `complete` default=True зустрічається в **5 місцях**:

| # | file:line | Код | Контекст |
|---|-----------|-----|---------|
| 1 | `uds.py:88` | `complete=bool(raw.get("complete", True))` | `_disk_bar_to_candle()` — конвертація disk raw → CandleBar |
| 2 | `uds.py:1587` | `complete = bool(b.get("complete", True))` | `_bars_to_lwc()` — Redis/RAM → LWC format |
| 3 | `uds.py:1622` | `complete = bool(payload.get("complete", True))` | `_redis_curr_to_bar_item()` — preview curr → bar |
| 4 | `uds.py:1669` | `complete = bool(bar.get("complete", True))` | `_normalize_bar_window_v1()` — final bar normalization |
| 5 | `ssot_jsonl.py:326` | `complete=bool(obj.get("complete", True))` | Disk JSONL read |

**Шляхи де complete може бути відсутнє**:

- **Legacy JSONL бари** — ранні записи до введення complete field. На диску `data_v3/` є файли з ~2025 року де complete може бути відсутнє.
- **Raw Redis payload** — якщо Redis snapshot записаний без complete field (малоймовірно з поточним кодом, але теоретично).

### Q8.2: Чи є downstream залежність від default True?

**Факт**: Так — `_bars_to_lwc()` (uds.py:1587) використовується для **всіх** API responses. Якщо bar не має `complete` → `complete=True`. Далі:

- UI `applyUpdates` (`app.js:1726`) перевіряє `complete` для final>preview logic.
- Бар маркований `complete=True` в UI = "final" → не може бути overwritten preview.

**Вплив**: Якщо preview bar загубить `complete` field → стане `True` → UI трактуватиме як final → **помилковий final>preview lock**.

### Класифікація Q8: **(C) — Реалізація суперечить інваріантам**

**Проблема**: Default `complete=True` порушує I3 (Final > Preview) — preview бар без explicit `complete=false` буде промоутований до final.

**Фактичний ризик**: LOW-MEDIUM. В поточному коді всі bar-creating функції явно встановлюють `complete`. Ризик тільки при:

1. Legacy disk data (старі JSONL без complete)
2. Manual data injection
3. Corrupt Redis entry

**Рекомендація**: Змінити default на `False` (conservative/safe) для конвертації функцій. Явний `complete=True` тільки для final paths.

---

## Q9: Live process watchdog

### Q9.1: Heartbeat per process

| Процес | Heartbeat/stale detection | file:line | Loud? |
|--------|--------------------------|-----------|-------|
| **M1 Poller** | ✅ `_stale_check()` — M1_STALE warning якщо market open + silence > 720s | `m1_poller.py:609-628` | ✅ Throttled warning (перший + кожні 60) |
| **Tick Publisher** | ❌ Немає stale detection | — | — |
| **Tick Preview Worker** | ⚠ Часткова: "0 тіків > 120s" one-shot warning | `tick_preview_worker.py:206-217` | ⚠ One-shot, не calendar-aware |
| **Connector (D1)** | ❌ Немає explicit stale check (але D1 fetch рідкісний by design) | — | — |
| **UI Server** | ❌ Немає process health reporting | — | — |

### Q9.2: Хто ставить degraded[] при зависанні?

**Факт**: **Ніхто централізовано**. Degraded pipeline:

1. UDS internal → `degraded[]` в WindowResult/UpdatesResult meta → API response → UI.
2. **Але**: UDS не знає чи ingest процес завис. UDS бачить тільки чи Redis/disk доступні.

**Supervisor** (`app/main.py:391-475`) — **restart** crashed процесів з exponential backoff (`_BACKOFF_CFG`), але:

- Не має health check механізму (лише exit code detection).
- Якщо процес **не крашиться** але зависає внутрішньо (deadlock, infinite wait) — supervisor **не помітить**.
- Немає "process last heartbeat" → degraded pipeline.

### Класифікація Q9: **(C) — Реалізація суперечить інваріантам I5/I6**

**Проблеми**:

1. Tick Publisher — **нуль** stale detection.
2. Tick Preview Worker — **часткова**, one-shot, не calendar-aware.
3. Connector — **нуль** (D1 low-freq by design, але формально gap).
4. **Немає** centralized health aggregation → degraded[] у API responses.
5. Supervisor — restart only, **не** health monitoring.

**Потрібен дизайн** (ADR-рівень для повного вирішення): per-process heartbeat → aggregator → degraded[]. Але **S2 PATCH** може закрити найкритичніший gap (tick_agg drops loud).

---

## Q10: Redis key normalization — raw vs symbol_key()

### Q10.1: Чому UpdatesBus використовує raw symbol?

**Факт**: Це **свідомий контракт**, не випадковість.

**UpdatesBus** (`uds.py:1907-1908`):

```python
seq_key = self._key("updates", "seq", str(event["key"]["symbol"]), str(event["key"]["tf_s"]))
list_key = self._key("updates", "list", str(event["key"]["symbol"]), str(event["key"]["tf_s"]))
```

→ `event["key"]["symbol"]` = raw symbol (XAU/USD) → Redis key = `v3_local:updates:list:XAU/USD:3600`.

**Preview Ring** (`redis_keys.py:17-21`):

```python
def preview_updates_seq_key(ns, symbol, tf_s):
    return f"{ns}:preview:updates:{symbol_key(symbol)}:{int(tf_s)}:seq"
```

→ `symbol_key("XAU/USD")` = `"XAU_USD"` → Redis key = `v3_local:preview:updates:XAU_USD:60:seq`.

**Redis Snapshot** (`redis_snapshot.py:279`):

```python
key_symbol = symbol_key(bar.symbol)
```

→ Tail/snap keys: `v3_local:ohlcv:tail:XAU_USD:3600`.

### Q10.2: Єдине місце нормалізації?

**Факт**: `redis_keys.py:4` — `symbol_key()` — єдина нормалізація:

```python
def symbol_key(symbol: str) -> str:
    return str(symbol).strip().replace("/", "_")
```

Але **не всі callers використовують** `symbol_key()`:

- Preview Ring: **так** (через redis_keys.py functions)
- Redis Snapshot: **так** (redis_snapshot.py:279)
- Redis Tail/Snap read (redis_layer.py:50): **так**
- UpdatesBus: **ні** (raw symbol пряму)
- `/api/updates` reader (`uds.py:551`): передає raw symbol в UpdatesBus.read_updates()

### Зведення ключів

| Redis key pattern | Формат symbol | Приклад |
|-------------------|---------------|---------|
| `{ns}:ohlcv:tail:{sk}:{tf}` | `symbol_key()` | `v3_local:ohlcv:tail:XAU_USD:3600` |
| `{ns}:ohlcv:snap:{sk}:{tf}` | `symbol_key()` | `v3_local:ohlcv:snap:XAU_USD:3600` |
| `{ns}:preview:curr:{sk}:{tf}` | `symbol_key()` | `v3_local:preview:curr:XAU_USD:60` |
| `{ns}:preview:tail:{sk}:{tf}` | `symbol_key()` | `v3_local:preview:tail:XAU_USD:60` |
| `{ns}:preview:updates:{sk}:{tf}:list` | `symbol_key()` | `v3_local:preview:updates:XAU_USD:60:list` |
| `{ns}:updates:list:{symbol}:{tf}` | **raw** | `v3_local:updates:list:XAU/USD:3600` |
| `{ns}:updates:seq:{symbol}:{tf}` | **raw** | `v3_local:updates:seq:XAU/USD:3600` |

### Класифікація Q10: **(C) — Реалізація inconsistent**

**Dual convention**: Preview Ring keys = `symbol_key()` (normalized), UpdatesBus keys = raw symbol (з `/`). Це працює коректно (reader і writer обидва використовують raw), але:

1. **Ops/debug ризик**: `redis-cli KEYS *XAU*` не покаже ВСІ ключі для XAU/USD одним патерном.
2. **Migration ризик**: при зміні формату symbol потрібно знати обидва варіанти.
3. **Не документовано** як formal contract.

**Рекомендація**: LOW priority PATCH — уніфікувати на `symbol_key()` всюди, або формально задокументувати dual convention.

---

## GO/NO-GO verdict

### GO/NO-GO по кожному critical gate

| Gate | Питання | Статус | Verdict |
|------|---------|--------|---------|
| **Q1** | TTL істина | **(C)** — 3-way mismatch, build_uds не wired | **GO PATCH** (S1: wire TTL) |
| **Q2+Q9** | Loud деградація + watchdog | **(C)** — tick_agg SILENT, часткові watchdogs | **GO PATCH** (S2: tick_agg loud + stats emit) |
| **Q4** | tick_promoted finality | **(A)** — коректно by design | **GO** (no action) |
| **Q3** | I4 dual stream | **(B)** — documents need update, not code | **GO** (doc update) |

### Мінімальний GO/NO-GO

**GO для переходу до TO-BE implementation** з CONDITIONS:

1. **S1 PATCH**: Wire `preview_curr_ttl_s` (блокер: SSOT split-brain).
2. **S2 PATCH**: Tick_agg stats → worker emit + periodic warning при high drop rate.
3. **Doc update**: I4 clarification (dual plane routing).
4. **Q8 STRETCH**: `complete` default → False (conservative, але не blocking).

**NO-GO не потрібен**: жоден інваріант не вимагає зміни. Q3 (I4) = уточнення формулювання, не зміна семантики.

---

## Зведена таблиця класифікації

| Q# | Тема | Класифікація | Severity | Потрібно |
|----|------|-------------|----------|----------|
| Q1 | Preview TTL 3-way mismatch | **(C)** Реалізація ≠ документи | **HIGH** | PATCH S1 |
| Q2 | Silent tick drops | **(C)** I5 порушено | **HIGH** | PATCH S2 |
| Q3 | I4 dual streams | **(B)** Документи суперечать | **MED** | Doc update |
| Q4 | tick_promoted finality | **(A)** Все коректно | — | — |
| Q5 | Dedup semantics різні | **(C)** Між компонентами | **LOW** | Unify (S3+) |
| Q6 | cold_load/disk_policy | **(A)** Все коректно | — | — |
| Q7 | Stitching без calendar | **(B)** Документи неповні | **MED** | Doc update |
| Q8 | complete=True default | **(C)** I3 потенційно порушено | **MED** | PATCH (stretch) |
| Q9 | Process watchdog | **(C)** I5 порушено | **HIGH** | Design (ADR) |
| Q10 | Redis key dual convention | **(C)** Inconsistent | **LOW** | Doc/PATCH |

### ADR теми (якщо потрібні)

1. **Q9 full solution**: Centralized process health → degraded[] pipeline (ADR-level, бо нова підсистема). Але S2 покриває найкритичніший gap (tick drops loud) без ADR.

### Порядок ремедіації

```
S1 (TTL wire) → S2 (tick drops loud) → Doc updates (Q3, Q7) → Q8 (complete default) → Q5 (dedup unify) → Q10 (key normalize) → Q9 full ADR
```
