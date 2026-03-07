# ADR-0031: Bias Banner — Multi-TF Trend Bias Display

- **Статус**: Implemented
- **Дата**: 2026-03-08
- **Автор**: User + Agent
- **Initiative**: `smc_vis_phi2`

---

## 1. Контекст і проблема

SMC Engine обчислює `trend_bias` (`"bullish" | "bearish" | None`) для кожного compute TF (M15, H1, H4, D1). Значення `trend_bias` визначається структурою ринку — BOS/CHoCH events у `detect_structure_events()` [VERIFIED core/smc/structure.py:80]. `None` = ще не було жодного BOS (cold start / недостатньо барів).

**Поточний стан:**

- Backend обчислює trend_bias для ВСІХ compute TFs (900, 3600, 14400, 86400) — [VERIFIED core/smc/config.py:239]
- `SmcEngine.get_htf_bias(symbol, tf_s)` вже існує — [VERIFIED core/smc/engine.py:319]
- Wire передає `trend_bias` лише для поточного viewer TF — [VERIFIED runtime/ws/ws_server.py:307]
- UI зберігає `trend_bias: string | null` (single value) — [VERIFIED ui_v4/src/types.ts:58]
- **UI НЕ рендерить trend_bias ніде** — ні в ChartHud, ні в OverlayRenderer, ні в StatusBar

**Проблема:**
Трейдер не бачить контексту напрямку. Зони, свінги, рівні — це елементи, але без bias banner вони не складаються в історію. Трейдер не знає: "D1 бичачий, H4 бичачий, а H1 ведмежій — це корекція чи розворот?" Bias banner — перший крок до narrative (контекст → сценарій → дія).

---

## 2. Розглянуті варіанти

### Варіант A: Розширити поточний frame — додати `bias_map`

Одне нове поле у WS frame: `bias_map: { "900": "bullish", "3600": "bearish", ... }`.

- **Плюси**: мінімальна зміна wire, UI одразу має всі TF
- **Мінуси**: дані з різних TF snapshots — може потребувати lock; frame стає більшим

### Варіант B: Окремий WS action `get_bias_map`

Клієнт запитує — сервер відповідає.

- **Плюси**: ізольований від основного frame потоку
- **Мінуси**: додатковий round-trip; складніше синхронізувати з TF switch; **X1 заборона** — не створювати альтернативний endpoint для тих самих даних

### Варіант C: UI збирає bias при кожному TF switch (вже є)

UI вже отримує `trend_bias` для поточного TF. Можна кешувати попередні значення при TF switch.

- **Плюси**: zero backend change
- **Мінуси**: стартово порожній (потрібно перемкнути всі TF щоб заповнити); stale при оновленні іншого TF

---

## 3. Рішення: Варіант A — `bias_map` у full frame

**Обґрунтування**: мінімальна складність, data вже є в backend, один wire field, sub-μs cost.

### 3.1 Wire format (розширення)

```json
{
  "action": "full",
  "symbol": "XAU/USD",
  "tf": "H1",
  "trend_bias": "bearish",
  "bias_map": {
    "900": "bullish",
    "3600": "bearish",
    "14400": "bullish",
    "86400": "bullish"
  },
  "zones": [...],
  "swings": [...],
  "levels": [...]
}
```

- `trend_bias` — залишається як є (backward compatible), bias для **поточного viewer TF**
- `bias_map` — **нове поле**, bias для **всіх compute TFs**
- `bias_map` включається лише в full frame (switch response), НЕ в delta frame (delta = інкрементальний update)

### 3.2 Backend зміни

**Файл**: `runtime/ws/ws_server.py` → `_build_full_frame()`

```python
# In _handle_switch(), after smc_wire is built:
bias_map = _smc_runner.get_bias_map(session.symbol) if _smc_runner else None
```

**Оцінка**: ~5 LOC у `_handle_switch()` [VERIFIED ws_server.py:896], ~1 LOC у `_build_full_frame()` signature/dict. Використовує **існуючий** `get_htf_bias()` [VERIFIED engine.py:319].

**SmcRunner API**: Додати `get_bias_map(symbol)` метод — ітерує `self._compute_tfs` [VERIFIED smc_runner.py:108], повертає `Dict[str, str]`. Thread-safe: single-threaded asyncio event loop + warmup completes before delta_loop. Snapshots = frozen dataclasses, dict reads = atomic under GIL.

### 3.3 TypeScript типи

**Файл**: `ui_v4/src/types.ts`

```typescript
export interface SmcData {
  zones: SmcZone[];
  swings: SmcSwing[];
  levels: SmcLevel[];
  trend_bias?: string | null;
  bias_map?: Record<string, string>;  // NEW: {"900":"bullish","3600":"bearish",...}
  zone_grades?: Record<string, ZoneGradeInfo>;
}
```

### 3.4 UI Store

**Файл**: `ui_v4/src/stores/smcStore.ts`

`applySmcFull()` — додати `bias_map` parameter, зберегти в SmcData.
`applySmcDelta()` — НЕ оновлювати bias_map (delta = одиночний TF bar update; bias_map = snapshot ALL TFs)

### 3.5 UI Component: BiasBanner

**Файл**: `ui_v4/src/layout/BiasBanner.svelte` (новий)

**Розташування**: під ChartHud, фіксований зліва. Горизонтальний рядок:

```
D1↑  H4↑  H1↓  M15↑
```

**Дизайн:**

- Кожен TF = pill/badge
- Колір: зелений (bullish ↑), червоний (bearish ↓), dim (null / no data)
- Порядок: від старшого до молодшого (D1 → H4 → H1 → M15)
- **Alignment indicator**: unanimous = всі non-null TFs мають однаковий bias → підсвічений border. Якщо частина null (cold start) — порівнюються тільки non-null. Якщо всі null — no indicator.
- Compact: не займає вертикального простору. Одна стрічка ~30px висотою

**Reactive data**: `$derived` від `smcStore.smcData.bias_map`

### 3.6 Delta Update для bias_map

Bias зміна = нечаста подія (BOS/CHoCH на будь-якому TF). Два варіанти:

- **Варіант A (простий)**: bias_map оновлюється лише при full frame (TF switch). Stale при зміні bias на іншому TF = прийнятний tradeoff. Трейдер бачить актуальний bias при кожному switch.
- **Варіант B (реактивний)**: delta frame включає `bias_changed: {"3600": "bearish"}` якщо trend_changed. Потребує cross-TF event routing.

**Рішення**: Варіант A (простий). Bias banner = **контекстний індикатор, не торговий сигнал**. bias_map оновлюється лише при full frame (connect + TF switch). Між switch-ами bias може бути stale (потенційно години, якщо трейдер залишається на одному TF). Це прийнятно: bias = загальний контекст для narrative interpretation, не entry trigger. Якщо потрібна реактивність (bias_changed в delta) — ADR-0032.

---

## 4. P-Slices (план реалізації)

### P1: Backend `get_bias_map()` + wire (≤30 LOC)

1. `SmcRunner.get_bias_map(symbol)` → `Dict[str, str]`
2. `_handle_switch()` → збирає bias_map + передає у `_build_full_frame()`
3. `_build_full_frame()` → додає `"bias_map": bias_map` у frame dict
4. **Verify**: WS client script → frame.get("bias_map") not None

### P2: TypeScript types + store (≤15 LOC)

1. `SmcData.bias_map` type у `types.ts`
2. `applySmcFull()` передає bias_map
3. `normalizeSmcData()` default = `{}`
4. **Verify**: console.log smcData.bias_map

### P3: BiasBanner.svelte (≤60 LOC)

1. New component з pills D1/H4/H1/M15
2. Mount у `App.svelte` після ChartHud
3. Reactive від smcData
4. **Verify**: візуальна перевірка на XAU/USD

---

## 5. Наслідки

- **Wire**: +1 поле `bias_map` у full frame (~80 bytes, negligible)
- **Backend**: +1 метод SmcRunner, +5 LOC у ws_server. Uses existing `get_htf_bias()`
- **UI**: +1 компонент BiasBanner.svelte, ~1 LOC зміна в types/store/App.svelte кожен
- **Test**: test_ws_server.py — перевірити bias_map у full frame
- **Інваріанти**: S0 (pure read), S1 (no write), I4 (same update потік), I1 (read-only UI)

---

## 6. Інваріанти check

| ID | Статус | Обґрунтування |
|----|--------|---------------|
| I0 | ✅ | core/ не змінюється (get_htf_bias вже є). runtime/ зміна. ui/ зміна |
| I1 | ✅ | UI = read-only renderer, отримує дані через WS |
| I4 | ✅ | Один update потік — bias_map вбудований у existing full frame |
| I5 | ⚠ | Null/missing = visible dim pill ✅. Stale bias = не позначений явно (bias_map = context-only, не trading signal — прийнятний tradeoff, see §3.6) |
| S0 | ✅ | get_htf_bias = pure read |
| S1 | ✅ | No SSOT writes |
| S5 | ✅ | compute_tfs з config.json |

---

## 7. Rollback

1. Видалити `bias_map` з `_build_full_frame()` frame dict
2. Видалити `get_bias_map()` з SmcRunner
3. Видалити `BiasBanner.svelte` + mount з App.svelte
4. Видалити `bias_map` з SmcData type
5. Wire: backward compatible — UI handles missing field via `?? {}`
