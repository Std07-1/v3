# Сесійні нотатки: ADR-0035 Hotfix — Narrative Delta + Session Levels + KZ Badge

- **ID**: 20260310-001
- **Дата**: 2026-03-10
- **ADR**: 0035 (Sessions & Killzones)
- **Initiative**: smc_sessions_v1
- **Статус**: Implemented + Verified (491 tests pass, 0 TS errors)

---

## 1. Контекст: що було зламано

Після повного BUILD ADR-0035 (P0–P7, 491 test, 40 нових тестів) користувач протестував live систему і надіслав скріншоти H1 та M15.

### Баг-репорти

1. **Наратив не оновлюється** — після завантаження сторінки наратив (SELL/WAIT/mode) заморожується і не змінюється при нових барах. Потрібно вручну перезавантажувати сторінку.
2. **Рівні сесій не видно** — на чарті немає ліній Asia Hi/Lo, London Hi/Lo, NY Hi/Lo. У наративі `outside_killzone`, але рівнів сесій немає взагалі.
3. **Мітка "LVL"** замість назв сесій — всі рівні показуються як "LVL" замість "Asia Hi", "London Lo" тощо.
4. **Немає індикатора кілзони** — HUD не показує, чи зараз кілзона.

---

## 2. Діагноз: root causes (3 кореневі причини)

### Root Cause 1: Наратив тільки у full frame

- **ДО**: `ws_server.py` лінія 631: коментар `"narrative only in full frame (not delta)"`.
- Наратив рахувався лише при повному фреймі (connect / TF switch / reload).
- Delta loop (кожну секунду) НЕ перераховував наратив → заморожений стан.

### Root Cause 2: M1 warmup = 500 барів (~8.3 години)

- **ДО**: `smc_runner.py` використовував `self._lookback` (500 барів) для ВСІХ TF, включаючи M1.
- Для сесій потрібно мінімум 48 годин M1 (2880 барів) щоб покрити попередню сесію (PDH/PDL тощо).
- 500 барів = ~8.3 годин → не вистачало для prev London/NY/Asia H/L.

### Root Cause 3: Session levels + M1 тільки у full frame path

- **ДО**: Session levels ін'єктувались лише через `get_display_snapshot()` (full frame path).
- Delta loop не мав ні session levels injection, ні M1 feed для оновлення сесій.
- M1 часто не є subscribed TF → engine не отримував M1 бари для відстеження session H/L.

### Root Cause 4 (Бонус): Pre-existing TS помилки

- `FrameType` не включав `'config'` і `'error'` → 2 помилки `svelte-check`.
- `UiWarningCode` не включав `'server_error'` → 1 помилка.
- `OverlayRenderer` default `frame` не мав `bias_map`, `momentum_map` → 1 помилка.

---

## 3. Рішення: 5 патчів + 1 бонус

---

### P1: M1 Warmup Lookback 500 → 2880 барів

**Чому саме так**: 2880 = 48 годин × 60 хв. Достатньо для покриття prev + current сесії (найдовша: NY = 9 годин + prev NY потребує ~33 год назад від поточного моменту).

**Файл**: `runtime/smc/smc_runner.py`

**ДО** (warmup):

```python
bars = self._read_bars_for_warmup(uds_reader, symbol, tf_s)
snap = self._engine.update(symbol, tf_s, bars)
```

Один виклик з `self._lookback` = 500. Session engine отримує лише 500 M1 барів.

**ПІСЛЯ** (warmup, лінії 155–170):

```python
bars = self._read_bars_for_warmup(uds_reader, symbol, tf_s)
snap = self._engine.update(symbol, tf_s, bars)
# ADR-0035: reuse M1 bars for session H/L (avoid duplicate read)
if tf_s == 60:
    if bars:
        self._engine.feed_m1_bars_bulk(symbol, bars)
    if len(bars) < 2880:
        try:
            extra = self._read_m1_for_sessions(uds_reader, symbol, 2880)
            if len(extra) > len(bars):
                self._engine.feed_m1_bars_bulk(symbol, extra)
        except Exception:
            pass  # warmup bars already fed, extra is best-effort
    m1_warmed.add(symbol)
```

**Новий метод** `_read_m1_for_sessions()` (лінії 244–272):

```python
def _read_m1_for_sessions(self, uds_reader, symbol, limit):
    """Read M1 bars with custom lookback for session warmup. S1: read-only."""
    spec = WindowSpec(symbol=symbol, tf_s=60, limit=limit, cold_load=True)
    policy = ReadPolicy(disk_policy="explicit", prefer_redis=True)
    result = uds_reader.read_window(spec, policy)
    # ... конвертація в CandleBar list
```

**Чому окремий метод**: `_read_bars_for_warmup()` використовує `self._lookback` (загальний для всіх TF). Для сесій потрібен більший lookback лише для M1, без зміни загальної логіки warmup.

---

### P2: Наратів у Delta Frame

**Чому саме так**: Наратив — це синтез SMC стану. Він має оновлюватись при кожному complete барі, щоб трейдер бачив актуальний mode (SELL/BUY/WAIT) без перезавантаження.

**Файл**: `runtime/ws/ws_server.py` (delta loop)

**ДО**:

```python
# ADR-0033 N4: narrative only in full frame (not delta)
```

Delta loop взагалі не рахував наратив.

**ПІСЛЯ** (лінії 1030–1060):

```python
# --- ADR-0035: narrative recompute on complete bars ---
if _smc_runner is not None and any(
    ev.get("complete") for ev in events if isinstance(ev, dict)
):
    try:
        _last_c = ...  # ціна з останньої свічки
        _atr_est = ...  # ATR estimation
        _narr = _smc_runner.get_narrative(symbol, tf_s, float(_last_c), float(_atr_est))
        if _narr is not None:
            from core.smc.narrative import narrative_to_wire
            frame["narrative"] = narrative_to_wire(_narr)
    except Exception as _narr_exc:
        _log.debug("WS_DELTA_NARRATIVE_ERR sym=%s err=%s", symbol, _narr_exc)
```

**Файл**: `ui_v4/src/App.svelte`

**ДО**:

```svelte
if (f?.frame_type === "full")
    cachedNarrative = (f as any).narrative ?? null;
```

Наратив приймався ТІЛЬКИ з full frame.

**ПІСЛЯ** (лінія 282):

```svelte
if ((f as any)?.narrative != null)
    cachedNarrative = (f as any).narrative ?? null;
```

Наратив приймається з БУДЬ-ЯКОГО фрейму, де є поле `narrative`.

**Чому `!= null` замість `frame_type check`**: Наратив може бути і у full, і у delta. Перевірка наявності поля — найпростіший і найнадійніший спосіб.

---

### P3: M1 Live Feed у Delta Loop

**Чому саме так**: Session engine потребує M1 бари для відстеження session H/L в реальному часі. M1 зазвичай НЕ є підписаним TF на дисплеї (користувач може дивитись на H1 або M15), тому без окремого polling M1 engine не отримує нових барів.

**Файл**: `runtime/ws/ws_server.py`

**ДО**: Delta loop ітерував тільки `subs_by_target[symbol]` — підписані TF (наприклад, H1, M15). M1 не в списку → session engine не оновлювався.

**ПІСЛЯ** (лінії 1118–1135):

```python
# ADR-0035: M1 cursor per symbol for session H/L live feed
_m1_cursor_by_sym: Dict[str, Optional[int]] = {}

# ... в кінці delta loop iteration:
for sym in active_symbols:
    try:
        m1_seq = _m1_cursor_by_sym.get(sym)
        m1_result = await _uds_read_updates(app, sym, 60, m1_seq, False)
        if m1_result is None:
            continue
        m1_events = getattr(m1_result, "events", [])
        m1_cursor = getattr(m1_result, "cursor_seq", 0)
        _m1_cursor_by_sym[sym] = m1_cursor
        for ev in m1_events:
            if isinstance(ev, dict) and ev.get("complete"):
                bar = ev.get("bar")
                if isinstance(bar, dict):
                    _smc_runner.feed_m1_bar_dict(sym, bar)
    except Exception as m1_exc:
        _log.debug("WS_M1_SESSION_FEED_ERR sym=%s err=%s", sym, m1_exc)
```

**Новий метод** `feed_m1_bar_dict()` у SmcRunner (лінії 274–283):

```python
def feed_m1_bar_dict(self, symbol, bar_dict):
    """Feed M1 bar from delta loop for session H/L computation (ADR-0035)."""
    cb = _bar_dict_to_candle_bar(bar_dict, symbol, 60)
    if cb is not None:
        self._engine.feed_m1_bar(cb)
```

**Чому cursor-based incremental**: UDS повертає лише нові бари після `cursor_seq`. Це ~0 барів у більшості ітерацій (M1 = 1 бар на хвилину, delta loop = 1 сек). Мінімальне навантаження.

---

### P4: Session Levels у Delta Frame

**Чому саме так**: Session levels (Asia Hi/Lo, London Hi/Lo тощо) — це динамічні рівні, які змінюються протягом сесії. Без ін'єкції в delta frame клієнт бачить їх ТІЛЬКИ при connect/TF switch.

**Файл**: `runtime/ws/ws_server.py` (delta loop, лінії 1054–1059)

**ДО**: Session levels тільки в `get_display_snapshot()` (full frame).

**ПІСЛЯ**:

```python
# ADR-0035: inject fresh session levels in delta
try:
    _sess_lvls = _smc_runner.get_session_levels_wire(symbol)
    if _sess_lvls:
        frame["session_levels"] = _sess_lvls
except Exception:
    pass  # session levels are best-effort in delta
```

**Новий метод** `get_session_levels_wire()` у SmcRunner (лінії 358–368):

```python
def get_session_levels_wire(self, symbol):
    """ADR-0035: session levels as wire dicts for delta frame injection."""
    levels = self._engine.get_session_levels(symbol, int(time.time() * 1000))
    return [lv.to_wire() for lv in levels]
```

**Файл**: `ui_v4/src/types.ts` (лінія 188)

**ДО**: `RenderFrame` не мав `session_levels`.

**ПІСЛЯ**:

```typescript
/** ADR-0035: refreshed session levels in delta (full-replace session kinds) */
session_levels?: SmcLevel[];
```

**Файл**: `ui_v4/src/stores/smcStore.ts` (лінії 102–118)

**ДО**: Не існувало.

**ПІСЛЯ**:

```typescript
const SESSION_KINDS = new Set([
    'as_h', 'as_l', 'p_as_h', 'p_as_l',
    'lon_h', 'lon_l', 'p_lon_h', 'p_lon_l',
    'ny_h', 'ny_l', 'p_ny_h', 'p_ny_l',
]);

export function applySessionLevels(current: SmcData, sessionLevels: SmcLevel[]): SmcData {
    const nonSession = current.levels.filter(l => !SESSION_KINDS.has(l.kind ?? ''));
    return { ...current, levels: [...nonSession, ...sessionLevels] };
}
```

**Файл**: `ui_v4/src/layout/ChartPane.svelte` (лінії 390–400)

**ДО**: Тільки `applySmcDelta`.

**ПІСЛЯ**:

```svelte
// ADR-0035: session levels refresh from delta
if (currentFrame.session_levels && currentFrame.session_levels.length > 0) {
    smcData = applySessionLevels(
        untrack(() => smcData),
        currentFrame.session_levels,
    );
}
```

**Чому full-replace, а не delta**: Session levels — це повний знімок поточних рівнів (12 можливих kinds). При зміні сесії старі рівні зникають, нові з'являються. Простіше і коректніше замінити всі session-kind рівні за один раз, ніж merge/delete окремо.

---

### P5: KZ Badge + Session Context у HUD

**Чому саме так**: `NarrativeBlock` вже мав поля `in_killzone`, `current_session`, `session_context` (додані в ADR-0035 BUILD), але ChartHud їх не відображав.

**Файл**: `ui_v4/src/layout/ChartHud.svelte`

**ДО**: Наратив показував mode (SELL/WAIT), phase, bias — без жодної інформації про сесію чи кілзону.

**ПІСЛЯ**:

KZ badge (лінія 354):

```html
{#if narrative.in_killzone}
    <span class="narr-kz">KZ</span>
{/if}
```

Session context у tooltip (лінії 421–430):

```html
{#if narrative.session_context}
    <div class="ntt-session">🕐 {narrative.session_context}</div>
{:else if narrative.current_session}
    <div class="ntt-session">
        🕐 {narrative.current_session}{narrative.in_killzone ? ' (KZ)' : ''}
    </div>
{/if}
```

CSS:

```css
.narr-kz {
    font-size: 7px; font-weight: 600;
    color: #ff9800; background: rgba(255,152,0,0.15);
    padding: 0 3px; border-radius: 2px;
}
.ntt-session {
    color: #42a5f5; font-size: 9px; opacity: 0.8; margin-top: 2px;
}
```

**Чому помаранчевий**: `#ff9800` = London session color (з OverlayRenderer LEVEL_STYLES). Кілзона — це "hot zone" → помаранчевий (warm) колір привертає увагу.

**Чому блакитний для session context**: `#42a5f5` = NY session color. Нейтральний інформаційний колір, не конфліктує з mode (зелений/червоний/жовтий).

---

### Бонус: 4 Pre-existing TS помилки

**Файл**: `ui_v4/src/types.ts`

**ДО**:

```typescript
export type FrameType = 'full' | 'delta' | 'scrollback' | 'drawing_ack' | 'replay' | 'heartbeat' | 'warming';
// frameRouter.ts порівнює з "config" і "error" → TS error
```

**ПІСЛЯ**: Додано `| 'config' | 'error'` до union.

**ДО**:

```typescript
export type UiWarningCode = '...' | 'schema_mismatch';
// frameRouter.ts викликає addUiWarning('server_error', 'ws', ...) → TS error
```

**ПІСЛЯ**: Додано `| 'server_error'`, `UiWarning.kind` розширено на `| 'ws'`.

**Файл**: `ui_v4/src/chart/overlay/OverlayRenderer.ts`

**ДО**:

```typescript
private frame: Required<SmcData> = { zones: [], swings: [], levels: [], trend_bias: null, zone_grades: {} };
// SmcData тепер включає bias_map, momentum_map (з ADR-0031/0033) → TS error
```

**ПІСЛЯ**: Додано `bias_map: {}, momentum_map: {}`.

---

## 4. Тестовий вплив

| Тест | Зміна |
|------|-------|
| `test_warmup_calls_read_window_per_symbol_tf` | Assertion 4→6 calls (+ 2 M1 session reads) |
| Решта 490 тестів | Без змін |
| `svelte-check` | 4 errors → 0 errors, 4 warnings (всі a11y/CSS pre-existing) |

## 5. Змінені файли (9 файлів)

| Файл | LOC змін | Що |
|------|----------|----|
| `runtime/smc/smc_runner.py` | +60 | M1 warmup 2880, `_read_m1_for_sessions`, `feed_m1_bar_dict`, `get_session_levels_wire` |
| `runtime/ws/ws_server.py` | +45 | Narrative in delta, session_levels in delta, M1 polling loop |
| `ui_v4/src/App.svelte` | ~1 | Narrative acceptance з будь-якого frame |
| `ui_v4/src/types.ts` | +4 | `session_levels`, FrameType +config/error, UiWarningCode +server_error |
| `ui_v4/src/stores/smcStore.ts` | +15 | `SESSION_KINDS`, `applySessionLevels()` |
| `ui_v4/src/layout/ChartPane.svelte` | +8 | `applySessionLevels` import + call |
| `ui_v4/src/layout/ChartHud.svelte` | +25 | KZ badge, session tooltip, CSS |
| `ui_v4/src/chart/overlay/OverlayRenderer.ts` | ~1 | SmcData default +bias_map/momentum_map |
| `tests/test_smc_runner.py` | ~1 | Warmup calls assertion 4→6 |

## 6. Архітектурна діаграма: до і після

### ДО (ADR-0035 BUILD, тільки full frame path)

```
[Connect/TF switch] → full frame
    → get_display_snapshot() → session_levels ✅
    → get_narrative() → narrative ✅
    → M1 warmup 500 bars → session H/L неповні ⚠️

[Delta loop, кожну секунду]
    → smc_delta (zones/OB/FVG) ✅
    → narrative ❌ (не рахується)
    → session_levels ❌ (не ін'єктуються)
    → M1 feed ❌ (не полається)
```

### ПІСЛЯ (Hotfix)

```
[Connect/TF switch] → full frame
    → get_display_snapshot() → session_levels ✅
    → get_narrative() → narrative ✅
    → M1 warmup 2880 bars → session H/L повні ✅

[Delta loop, кожну секунду]
    → smc_delta (zones/OB/FVG) ✅
    → narrative ✅ (перераховується при complete bars)
    → session_levels ✅ (ін'єктуються щоразу)
    → M1 feed ✅ (cursor-based polling)
```

## 7. Чому саме так (design decisions)

### Чому M1 polling окремо від основного delta loop

M1 не є "subscribed TF" коли користувач дивиться на H1/M15. Основний цикл ітерує `subs_by_target` (підписані TF). M1 потрібен лише для session engine, тому він полається **після** основного циклу, окремим блоком.

### Чому `applySessionLevels` full-replace, а не incremental

Session levels — це повний snapshot (до 12 рівнів). Вони не мають delta семантики (як зони з merge/evict). Full-replace простіший, коректніший, і не залишає "привидів" при зміні сесії.

### Чому `get_session_levels_wire()` best-effort (pass on exception)

Session levels — це UX enhancement, не критичний data path. Якщо engine ще не warmed up або помилка — краще показати чарт без сесій, ніж зламати весь delta frame.

### Чому 2880, а не 1440 або 4320

2880 M1 барів = 48 годин. Покриває:

- Поточну сесію (макс 9 год = 540 M1)
- Попередню сесію того ж типу (~24 год назад)
- Запас на вихідні/gap (ще ~15 год)

1440 (24h) не вистачає для prev session через вихідні. 4320 (72h) — зайвий overhead.

### Чому narrative рахується тільки при complete bars

Перерахунок narrative на кожному тіку (preview bar) — це overhead без цінності. Трейдеру потрібен оновлений narrative при закритті бару (коли щось фундаментально змінилось), а не при кожному тіку.
