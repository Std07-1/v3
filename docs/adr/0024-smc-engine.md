# ADR-0024: SMC Engine — Smart Money Concepts Computation Layer

- **Статус**: Proposed
- **Дата**: 2026-02-28
- **Автор**: Claude Opus 4.6 (Patch Master)
- **Initiative**: `smc_engine_v1`
- **Залежності**: ADR-0001 (UDS), ADR-0002 (DeriveChain), ADR-0017 (Replay), ADR-0023 (D1)

---

## 1. Контекст і проблема

### 1.1 Бізнес-контекст

Платформа має **технічно відмінну** інфраструктуру (Svelte 5 + WebSocket + FXCM live data + DeriveEngine M1→D1), але **нульову SMC-специфічність** (оцінка 3/10 — `research/leader/Untitled-4.md`). Трейдер бачить "ще один швидкий графік, на якому треба все малювати руками".

**Без SMC Engine**: 18–23 хвилини ручного аналізу на кожен setup (OB + FVG + Structure + Liquidity).  
**З SMC Engine**: 30–60 секунд — система вже знає, де зони.

**Конкурентна позиція**:

| Функція | TradingView | Paid SMC tools ($100-300/мо) | Aione v3 (поточний) | Aione v3 + SMC Engine |
|---------|-------------|------------------------------|---------------------|-----------------------|
| Auto Order Blocks | ❌ | ✅ | ❌ | ✅ |
| Auto FVG | ❌ | ✅ | ❌ | ✅ |
| Market Structure | ❌ (індикатори) | ✅ | ❌ | ✅ |
| Liquidity Levels | ❌ (вручну) | ✅ | ❌ | ✅ |
| Real-time speed | ⚠️ | ⚠️ | ✅ | ✅ |
| Replay + SMC навчання | ❌ | ❌ | ❌ | ✅ ← **унікальне** |
| Ціна | Free/Paid | $100-300/мо | 0 | 0 |

**Ключовий диференціатор**: Replay + SMC = тренажер для менторів, якого **ніхто не має**.

### 1.2 Технічний контекст

- 13 символів × 8 TFs = 104 пари (symbol, tf), кожна потребує SMC-обчислень
- DeriveEngine вже виробляє cascade M1→D1 з commit + events bus
- WS frames **вже мають** порожні масиви `zones: [], swings: [], levels: []` (ws_server.py:279-281)
- UI types.ts **вже визначає** SmcZone, SmcSwing, SmcLevel, SmcData
- Відсутній: `core/smc/` (pure logic), `runtime/smc/` (I/O обгортка), алгоритми, контракти
- Replay architecture (ADR-0017) проектується з SMC-хуком: `ReplayEngine.step()` → `SmcEngine.update()`

### 1.3 Вимоги

1. **Постійне обчислення** — при кожному commit bar (M1 close → cascade → SMC update)
2. **13 символів × 8 TFs** — масштабується горизонально, per-(symbol, tf) ізоляція
3. **Real-time WS** — зони з'являються/зникають у дельта-фреймах
4. **Replay-сумісний** — той самий SmcEngine працює в Replay Mode (ADR-0017)
5. **Mentor-friendly** — чітке відображення для навчальних сесій
6. **User-configurable** — параметри через config.json (sensitivity, layers on/off)
7. **I0 compliant** — чиста логіка у core/smc/, I/O у runtime/smc/

---

## 2. Розглянуті варіанти

### Варіант A: Frontend-only (SmcEngine у ui_v4/src/)

**Плюси**: Простіше, менше backend змін.  
**Мінуси**: Порушує I0. Дублює логіку для HTTP API та Replay. Неможливо серверні alertи. 13 символів × 8 TFs обчислювать у браузері нереалістично.  
**Вердикт**: ❌ Відхилено.

### Варіант B: Backend з UDS persistence (зберігати SMC зони в JSONL)

**Плюси**: Моментальний cold-start SMC даних.  
**Мінуси**: Порушує I1 (UDS = OHLCV тільки). Складна міграція. SMC зони — derived read-only дані, не SSOT.  
**Вердикт**: ❌ Відхилено — порушує інваріант.

### Варіант C: Backend pure + ephemeral state + Redis pub/sub ← ОБРАНО

**Плюси**: I0/I1 compliant. SMC = read-only overlay, обчислений з CandleBar. Ephemeral — відновлюється з warm bars при restart. Redis bus для events. Природна інтеграція з DeriveEngine та Replay.  
**Мінуси**: Cold-start потребує warmup (500 барів на TF = ~3s).  
**Вердикт**: ✅ Обрано.

---

## 3. Рішення: Архітектура SMC Engine

### 3.1 Шари (відповідно до I0)

```
core/smc/                          ← PURE LOGIC (NO I/O)
  ├─ __init__.py
  ├─ types.py                      ← SmcZone, SmcSwing, SmcLevel, SmcSnapshot, SmcDelta
  ├─ config.py                     ← SmcConfig dataclass (parsed from config.json)
  ├─ swings.py                     ← Fractal swing detection (foundation)
  ├─ structure.py                  ← BOS, CHoCH, trend bias
  ├─ order_blocks.py               ← OB detection (bullish/bearish/breaker)
  ├─ fvg.py                        ← Fair Value Gap detection
  ├─ liquidity.py                  ← Equal Highs/Lows, PDH/PDL, PWH/PWL
  └─ engine.py                     ← SmcEngine orchestrator (per symbol+tf state)

runtime/smc/                       ← I/O WRAPPER
  ├─ __init__.py
  └─ smc_runner.py                 ← SmcRunner: subscribes bar events → SmcEngine → publish
```

### 3.2 Потік даних

```
                Live Pipeline                              Replay Pipeline
                ─────────────                              ───────────────
FXCM tick → M1 bar                                  JSONL → DiskReader → M1 bar
         │                                                            │
         ▼                                                            ▼
   DeriveEngine.on_bar(M1)                            ReplayEngine.step()
         │ cascade: M3→M5→M15→M30→H1→H4→D1                   │ same cascade
         │                                                     │
         ▼                                                     ▼
   UDS.commit_final_bar(bar)                          (no UDS commit)
         │                                                     │
         ├─── Redis pub/sub ──────────┐                        │
         │                            ▼                        │
         │                    SmcRunner.on_bar_event()         │
         │                            │                        │
         │                            ▼                        ▼
         │                    SmcEngine.on_bar(bar)    SmcEngine.on_bar(bar)
         │                            │                        │
         │                            ▼                        ▼
         │                    SmcDelta {new/mitigated/updated} SmcDelta
         │                            │                        │
         │                            ▼                        ▼
         │                    Redis pub: smc:delta:{sym}:{tf}  WS frame
         │                            │
         │                            ▼
         └─────────────────► WS _build_delta_frame()
                              zones, swings, levels populated
                                      │
                                      ▼
                                    UI render
```

### 3.3 SmcEngine — чиста логіка (core/smc/engine.py)

```python
class SmcEngine:
    """Orchestrator: тримає per-(symbol, tf) стан і координує детекцію.
    
    Pure logic — без I/O. Приймає бари, повертає SMC дані.
    Може використовуватись у Live, Replay, тестах, CLI діагностиці.
    """
    
    def __init__(self, config: SmcConfig) -> None:
        """config — параметри з config.json:smc (sensitivity, lookback, layers)."""
        self._config = config
        self._states: Dict[Tuple[str, int], _TfState] = {}  # (symbol, tf_s) → state
    
    def update(self, symbol: str, tf_s: int, bars: List[CandleBar]) -> SmcSnapshot:
        """Full recompute з N барів. Для cold-start/warmup."""
        
    def on_bar(self, bar: CandleBar) -> SmcDelta:
        """Incremental update при новому закритому барі.
        
        Повертає SmcDelta: які зони з'явились, зникли, оновились.
        Це O(1) відносно lookback — не перераховує все.
        """
    
    def get_snapshot(self, symbol: str, tf_s: int) -> SmcSnapshot:
        """Поточний повний стан для (symbol, tf). Для full WS frame."""
    
    def get_htf_bias(self, symbol: str, tf_s: int) -> Optional[TrendBias]:
        """HTF trend bias для cross-TF alignment.
        
        Використання: M15 entry alignment → запитує H4 bias.
        """
    
    def reset(self, symbol: str, tf_s: int) -> None:
        """Скидає стан для (symbol, tf). Для symbol switch / config change."""
```

### 3.4 SmcRunner — I/O обгортка (runtime/smc/smc_runner.py)

```python
class SmcRunner:
    """Підписується на bar events (Redis bus), запускає SmcEngine, публікує дельти.
    
    Один SmcRunner на процес. SmcEngine всередині.
    Lifecycle: start() → on_bar_event() loop → stop()
    """
    
    def __init__(self, config: Dict, smc_engine: SmcEngine) -> None:
        """config — повний config.json, smc_engine — core/smc інстанс."""
    
    def start(self, uds_reader) -> None:
        """Warmup: зчитує warm bars з UDS → SmcEngine.update() для всіх (symbol, tf).
        Підписується на Redis канал v3_local:updates:* для on_bar_event().
        """
    
    def on_bar_event(self, bar: CandleBar) -> None:
        """Callback від Redis pub/sub. Визначає (symbol, tf), делегує SmcEngine.on_bar().
        Публікує SmcDelta в Redis канал v3_local:smc:delta:{symbol}:{tf_s}.
        """
    
    def get_snapshot(self, symbol: str, tf_s: int) -> SmcSnapshot:
        """Для HTTP API та WS full frame."""
```

---

## 4. SMC Concepts та алгоритми

### 4.1 Swing Detection (фундамент усіх обчислень)

**Алгоритм**: Fractal pivot з configurable period (за замовчуванням 5).

```python
def detect_swings(bars: List[CandleBar], period: int = 5) -> List[SmcSwing]:
    """
    Swing High: bar[i].h > max(bar[i-period:i].h) AND bar[i].h > max(bar[i+1:i+period+1].h)
    Swing Low:  bar[i].low < min(bar[i-period:i].low) AND bar[i].low < min(bar[i+1:i+period+1].low)
    
    Потребує мінімум 2*period+1 барів.
    Останні `period` барів — unconfirmed (майбутнє ще не відоме).
    
    Incremental: новий бар може:
    1. Підтвердити pending swing (period барів пройшло)
    2. Інвалідувати pending swing (новий extremum)
    3. Не змінити нічого
    """
```

**Складність**: O(n) при full compute, O(period) при incremental.

### 4.2 Market Structure — BOS / CHoCH

```
Визначення тренду:
  Bullish: послідовність HH + HL (Higher High, Higher Low)
  Bearish: послідовність LL + LH (Lower Low, Lower High)

BOS (Break of Structure) — продовження тренду:
  Bullish BOS: ціна закривається вище попереднього Swing High, тренд вже bullish
  Bearish BOS: ціна закривається нижче попереднього Swing Low, тренд вже bearish

CHoCH (Change of Character) — розворот:
  Bullish CHoCH: ціна закривається вище попереднього Swing High, тренд був bearish → reversal
  Bearish CHoCH: ціна закривається нижче попереднього Swing Low, тренд був bullish → reversal

Incremental:
  На кожному новому Swing → перевірити BOS/CHoCH умову.
  На кожному новому барі → перевірити чи поточний бар ламає structure.
```

**Output**: `SmcSwing(kind="bos_bull"|"bos_bear"|"choch_bull"|"choch_bear", price, time_ms, confirmed)`

### 4.3 Order Blocks (OB)

```
Bullish OB:
  1. Знайти BOS/CHoCH подію (bullish)
  2. Відслідкувати імпульс (рух, що створив BOS)
  3. Знайти останню bearish свічку (close < open) ДО імпульсу
  4. Зона = [candle.low, candle.high]
  5. Valідність: імпульс > min_impulse_atr_mult × ATR(period)

Bearish OB:
  Дзеркально: остання bullish свічка перед bearish BOS/CHoCH.

Lifecycle зони:
  active  → ціна ще не торкалась зони
  tested  → ціна торкнулась зони, але не пробила (bounce)
  mitigated → ціна закрилася за межами зони (протилежний бік)
  breaker → після mitigation зона стає breaker block (протилежний bias)

Incremental:
  Новий бар → Перевірити всі active/tested OB → чи mitigated?
  Новий BOS/CHoCH → Знайти нову OB.
```

**Output**: `SmcZone(kind="ob_bull"|"ob_bear", status="active"|"tested"|"mitigated"|"breaker")`

### 4.4 Fair Value Gaps (FVG)

```
Bullish FVG (3-candle pattern):
  bar[0].high < bar[2].low → gap between candle 0 top and candle 2 bottom
  Zone: [bar[0].high, bar[2].low]
  Умова: middle candle (bar[1]) = impulse candle (strong body)

Bearish FVG:
  bar[0].low > bar[2].high → gap
  Zone: [bar[2].high, bar[0].low]

Lifecycle:
  active → gap не заповнений
  partially_filled → ціна увійшла в зону але не заповнила повністю
  filled → ціна повністю закрила gap (close за протилежний бік)

Incremental:
  Новий бар → Перевірити останні 3 бари → нова FVG?
  Новий бар → Перевірити all active FVGs → filled?
```

**Output**: `SmcZone(kind="fvg_bull"|"fvg_bear", status="active"|"partially_filled"|"filled")`

### 4.5 Liquidity Levels

```
Equal Highs (sell-side liquidity):
  2+ Swing Highs з ціною в межах tolerance → рівень ліквідності
  Трейдери з SL над цими рівнями = sell-side liquidity
  
Equal Lows (buy-side liquidity):
  2+ Swing Lows з ціною в межах tolerance

PDH/PDL (Previous Day High/Low):
  З D1 барів: yesterday.h / yesterday.low
  (потребує cross-TF access — SmcEngine зчитує D1 snapshot)

PWH/PWL (Previous Week High/Low):
  З D1 барів: max(last 5 D1).h / min(last 5 D1).low

Incremental:
  Новий Swing → перевірити equal highs/lows.
  Нова D1 bar → оновити PDH/PDL/PWH/PWL.
```

**Output**: `SmcLevel(kind="eq_highs"|"eq_lows"|"pdh"|"pdl"|"pwh"|"pwl", price, touches)`

---

## 5. Контракт smc_v1

### 5.1 Core Types (core/smc/types.py)

```python
from __future__ import annotations
import dataclasses
from typing import Any, Dict, List, Optional, Tuple

# ── Zone Kinds ──
ZONE_KINDS = frozenset({"ob_bull", "ob_bear", "fvg_bull", "fvg_bear"})
ZONE_STATUSES = frozenset({"active", "tested", "mitigated", "breaker", 
                            "partially_filled", "filled"})

# ── Swing Kinds ──
SWING_KINDS = frozenset({"hh", "hl", "lh", "ll",
                         "bos_bull", "bos_bear", "choch_bull", "choch_bear"})

# ── Level Kinds ──
LEVEL_KINDS = frozenset({"eq_highs", "eq_lows", "pdh", "pdl", "pwh", "pwl"})


@dataclasses.dataclass(frozen=True)
class SmcZone:
    """Order Block або Fair Value Gap."""
    id: str              # Deterministic: "{kind}_{symbol}_{tf_s}_{anchor_ms}"
    symbol: str
    tf_s: int
    kind: str            # ZONE_KINDS
    start_ms: int        # Left edge — open_time_ms of anchor candle
    end_ms: Optional[int]  # Right edge (extends until mitigated); None = still active
    high: float          # Zone top
    low: float           # Zone bottom
    status: str          # ZONE_STATUSES
    strength: float      # 0.0–1.0, impulse magnitude / ATR
    anchor_bar_ms: int   # The candle that created this zone
    
    def to_wire(self) -> Dict[str, Any]:
        """Серіалізація для WS/HTTP (wire format = ui_v4 SmcZone type)."""
        return {
            "id": self.id,
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "high": self.high,
            "low": self.low,
            "kind": self.kind,       # UI map: ob → "ob", fvg → "fvg" (prefix before _)
            "status": self.status,
            "strength": self.strength,
        }


@dataclasses.dataclass(frozen=True)
class SmcSwing:
    """Swing point або Structure event (BOS/CHoCH)."""
    id: str              # "{kind}_{symbol}_{tf_s}_{time_ms}"
    symbol: str
    tf_s: int
    kind: str            # SWING_KINDS
    price: float
    time_ms: int
    confirmed: bool      # True after `period` bars passed
    
    def to_wire(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "a": {"t": self.time_ms, "p": self.price},
            "b": {"t": self.time_ms, "p": self.price},  # For BOS/CHoCH: b = break point
            "label": self.kind.upper().replace("_", " "),
        }


@dataclasses.dataclass(frozen=True)
class SmcLevel:
    """Liquidity level (Equal Highs/Lows, PDH/PDL, PWH/PWL)."""
    id: str              # "{kind}_{symbol}_{tf_s}_{price_rounded}"
    symbol: str
    tf_s: int
    kind: str            # LEVEL_KINDS
    price: float
    time_ms: Optional[int]  # Коли утворився
    touches: int         # Кількість дотиків до рівня
    
    def to_wire(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "price": self.price,
            "t_ms": self.time_ms,
            "color": None,  # UI вирішує по kind
        }


@dataclasses.dataclass(frozen=True)
class SmcSnapshot:
    """Повний стан SMC для (symbol, tf) — для full frame / HTTP."""
    symbol: str
    tf_s: int
    zones: List[SmcZone]
    swings: List[SmcSwing]
    levels: List[SmcLevel]
    trend_bias: Optional[str]  # "bullish" | "bearish" | "neutral"
    last_bos_ms: Optional[int]
    last_choch_ms: Optional[int]
    computed_at_ms: int
    bar_count: int       # Кількість вхідних барів
    
    def to_wire(self) -> Dict[str, Any]:
        return {
            "zones": [z.to_wire() for z in self.zones],
            "swings": [s.to_wire() for s in self.swings],
            "levels": [l.to_wire() for l in self.levels],
        }


@dataclasses.dataclass(frozen=True)
class SmcDelta:
    """Інкрементальна зміна після on_bar() — для delta frame."""
    symbol: str
    tf_s: int
    bar_open_ms: int     # Який бар спричинив зміну
    new_zones: List[SmcZone]
    mitigated_zones: List[str]   # IDs зон що змінили status
    updated_zones: List[SmcZone] # Зони з оновленим status
    new_swings: List[SmcSwing]
    new_levels: List[SmcLevel]
    removed_levels: List[str]    # IDs видалених рівнів
    trend_bias: Optional[str]
    
    @property
    def has_changes(self) -> bool:
        return bool(self.new_zones or self.mitigated_zones or self.updated_zones
                     or self.new_swings or self.new_levels or self.removed_levels)
```

### 5.2 Wire Format (WS / HTTP)

**WS full frame** (доповнення існуючого формату):

```json
{
  "type": "full",
  "schema_v": "ui_v4_v2",
  "data": {
    "candles": [...],
    "zones": [
      {
        "id": "ob_bull_XAU/USD_3600_1770302400000",
        "start_ms": 1770302400000,
        "end_ms": null,
        "high": 2850.50,
        "low": 2848.20,
        "kind": "ob_bull",
        "status": "active",
        "strength": 0.82
      }
    ],
    "swings": [
      {
        "id": "bos_bull_XAU/USD_3600_1770309600000",
        "a": {"t": 1770309600000, "p": 2852.30},
        "b": {"t": 1770309600000, "p": 2852.30},
        "label": "BOS BULL"
      }
    ],
    "levels": [
      {
        "id": "eq_highs_XAU/USD_3600_285050",
        "price": 2850.50,
        "t_ms": 1770302400000,
        "color": null
      }
    ]
  }
}
```

**WS delta frame** (доповнення):

```json
{
  "type": "delta",
  "data": {
    "upsert": [...],
    "smc_delta": {
      "new_zones": [...],
      "mitigated_zone_ids": ["ob_bear_XAU/USD_900_1770288000000"],
      "updated_zones": [...],
      "new_swings": [...],
      "new_levels": [...],
      "removed_level_ids": [],
      "trend_bias": "bullish"
    }
  }
}
```

**HTTP endpoint** (новий):

```
GET /api/smc?symbol=XAU/USD&tf_s=3600
→ SmcSnapshot.to_wire() + meta
```

### 5.3 Config SSOT (config.json)

```json
{
  "smc": {
    "enabled": true,
    "lookback_bars": 500,
    "swing_period": 5,
    "ob": {
      "enabled": true,
      "min_impulse_atr_mult": 1.5,
      "atr_period": 14,
      "max_active_per_side": 5,
      "track_breakers": true
    },
    "fvg": {
      "enabled": true,
      "min_gap_atr_mult": 0.1,
      "max_active": 10
    },
    "structure": {
      "enabled": true,
      "confirmation_bars": 1
    },
    "liquidity": {
      "enabled": true,
      "eq_tolerance_pips": {
        "XAU/USD": 0.50,
        "XAG/USD": 0.05,
        "NAS100": 5.0,
        "SPX500": 2.0,
        "default": 0.0003
      },
      "min_touches": 2,
      "pdh_pdl_enabled": true,
      "pwh_pwl_enabled": true
    },
    "max_zones_per_tf": 30,
    "performance": {
      "max_compute_ms": 10,
      "log_slow_threshold_ms": 5
    }
  }
}
```

Кожен `enabled` прапорець — per-layer toggle. UI надсилає `overlay_toggle` action для відображення.

---

## 6. Інтеграція

### 6.1 Live Pipeline — SmcRunner у supervisor

**Розміщення**: SmcRunner запускається як 6-й процес у supervisor (після ws_server):

```python
# app/main.py — додати mode="smc_runner"
# SmcRunner живе в тому ж процесі що й ws_server (shared memory для snapshots)
# АБО як окремий процес з Redis pub/sub bridge
```

**Рекомендація**: SmcRunner у **тому ж процесі** що й ws_server — уникає серіалізації SmcSnapshot при кожному frame.

```
ws_server process:
  ├─ aiohttp WS сервер
  ├─ SmcRunner (thread або asyncio task)
  │   ├─ Redis subscriber: v3_local:updates:*
  │   ├─ SmcEngine (core, pure)
  │   └─ In-memory SmcSnapshot per (symbol, tf)
  └─ _build_full_frame() / _build_delta_frame()
       └─ reads SmcRunner.get_snapshot(symbol, tf_s)
```

### 6.2 WS Server Integration

**Зміни у ws_server.py** (~30 LOC):

```python
# В _build_full_frame():
smc = self._smc_runner.get_snapshot(session.symbol, session.tf_s)
frame["data"]["zones"] = [z.to_wire() for z in smc.zones]    # замість []
frame["data"]["swings"] = [s.to_wire() for s in smc.swings]  # замість []
frame["data"]["levels"] = [l.to_wire() for l in smc.levels]  # замість []

# В _build_delta_frame():
delta = self._smc_runner.last_delta(session.symbol, session.tf_s)
if delta and delta.has_changes:
    frame["data"]["smc_delta"] = {
        "new_zones": [z.to_wire() for z in delta.new_zones],
        "mitigated_zone_ids": delta.mitigated_zones,
        "updated_zones": [z.to_wire() for z in delta.updated_zones],
        "new_swings": [s.to_wire() for s in delta.new_swings],
        "new_levels": [l.to_wire() for l in delta.new_levels],
        "removed_level_ids": delta.removed_levels,
        "trend_bias": delta.trend_bias,
    }
```

### 6.3 HTTP API (ui_chart_v3/server.py)

Новий endpoint `/api/smc`:

```python
@app.route("/api/smc")
def api_smc():
    symbol = request.args.get("symbol", "XAU/USD")
    tf_s = int(request.args.get("tf_s", 3600))
    snap = smc_runner.get_snapshot(symbol, tf_s)
    return jsonify({"ok": True, **snap.to_wire(), "trend_bias": snap.trend_bias})
```

### 6.4 Replay Integration (ADR-0017)

```python
# runtime/replay/replay_engine.py (Phase 3 of ADR-0017)
class ReplayEngine:
    def __init__(self, ...):
        self._smc = SmcEngine(smc_config)  # standalone, не SmcRunner
    
    def step(self) -> ReplayFrame:
        bar = self._next_bar()
        derived = self._derive_engine.on_bar(bar)
        
        # SMC update для кожного derived бару
        for b in [bar] + derived:
            self._smc.on_bar(b)
        
        snapshot = self._smc.get_snapshot(self._symbol, self._tf_s)
        return ReplayFrame(candles=..., smc=snapshot.to_wire())
```

### 6.5 Mentor Use Case — Multi-TF Analysis

```
Ментор показує учню XAU/USD:

1. D1 chart: SmcEngine.get_htf_bias("XAU/USD", 86400) → "bearish"
   └─ D1 BOS bearish at 2880, OB bearish at [2890, 2895]
   
2. H4 chart: SmcEngine.get_snapshot("XAU/USD", 14400)
   └─ H4 OB bearish at [2865, 2870], FVG bearish at [2862, 2866]
   └─ "Бачиш? HTF bias bearish, H4 підтверджує зону пропозиції"

3. M15 chart: SmcEngine.get_snapshot("XAU/USD", 900)
   └─ M15 CHoCH bearish at 2863 (confirms H4 OB reaction)
   └─ SmcEngine.get_htf_bias("XAU/USD", 14400) → "bearish" (alignment ✓)
   └─ "Ось entry — M15 CHoCH підтверджує H4 bearish OB"

4. Risk Calculator (UI):
   └─ Entry: 2863.00 (M15 CHoCH level)
   └─ SL: 2870.50 (above H4 OB high + buffer)
   └─ TP1: 2850.00 (next liquidity: equal lows)
   └─ R:R = 1:1.7
```

**Cross-TF bias query** (`get_htf_bias`) дозволяє LTF entry шукати alignment з HTF structure.

---

## 7. Performance Budget

### 7.1 Обчислювальні обмеження

| Операція | Бюджет | Обґрунтування |
|----------|--------|---------------|
| `SmcEngine.on_bar()` per (symbol, tf) | < 10ms | WS delta poll = 1.0s, 104 пар |
| `SmcEngine.update()` full (500 bars) | < 100ms | Warm-up при cold-start |
| Всі (symbol, tf) один цикл M1 close | < 500ms | 13 × 8 = 104, але cascade впорядкований |
| `SmcSnapshot.to_wire()` | < 1ms | JSON серіалізація overlay |
| Total latency (bar close → UI zone appear) | < 2s | Включає derive cascade + SMC + WS |

### 7.2 Memory Budget

| Компонент | Розмір | Обґрунтування |
|-----------|--------|---------------|
| Per-(symbol, tf) state | ~50 KB | 500 bars cache + active zones + swings |
| 104 пар (13 sym × 8 tf) | ~5 MB | 104 × 50 KB |
| Wire payload (full frame) | < 10 KB | 30 zones + 50 swings + 20 levels |
| Wire payload (delta) | < 1 KB | 1-3 changes per bar |

### 7.3 Rail: Slow Computation Guard

```python
# core/smc/engine.py — вбудований rail
def on_bar(self, bar: CandleBar) -> SmcDelta:
    t0 = _perf_counter_ms()
    delta = self._compute(bar)
    elapsed = _perf_counter_ms() - t0
    if elapsed > self._config.max_compute_ms:
        _log.warning("SMC_SLOW sym=%s tf=%d elapsed_ms=%.1f budget=%d",
                     bar.symbol, bar.tf_s, elapsed, self._config.max_compute_ms)
    return delta
```

---

## 8. Multi-TF Alignment Strategy

### 8.1 Проблема

SMC-трейдер потребує **HTF bias для LTF entry**:

- D1/H4 визначають напрямок (supply/demand зони)
- M15/M5 визначають точку входу (CHoCH/BOS підтвердження)
- Без alignment: LTF signal може бути проти HTF тренду = low-probability trade

### 8.2 Рішення: Shared SmcEngine

SmcEngine тримає стан **всіх TFs** для одного символу в пам'яті. Cross-TF query — це просто зчитування з іншого `_TfState`:

```python
def get_htf_bias(self, symbol: str, tf_s: int) -> Optional[TrendBias]:
    """Повертає trend bias з найближчого HTF.
    
    Ієрархія: D1 → H4 → H1 → M30 → M15 → M5 → M3
    Для tf_s=900 (M15) → шукає H4 (14400) або H1 (3600).
    """
    htf_order = [86400, 14400, 3600, 1800, 900, 300, 180]
    for htf in htf_order:
        if htf > tf_s:
            state = self._states.get((symbol, htf))
            if state and state.trend_bias:
                return state.trend_bias
    return None
```

### 8.3 Alignment Score (Phase 2+)

```python
@dataclasses.dataclass(frozen=True)
class AlignmentScore:
    """Multi-TF alignment quality для entry filtering."""
    htf_bias: str        # bullish/bearish/neutral
    ltf_signal: str      # bos/choch/ob_test
    aligned: bool        # HTF bias == LTF signal direction
    score: float         # 0-1, confluence count
    htf_tf_s: int        # reference HTF used
```

Це дозволяє UI показувати: `"H4 ↘ + M15 CHoCH Bear → ALIGNED ✓ (0.85)"`.

---

## 9. Mentor/Replay: Training Simulator

### 9.1 Value Proposition

| Для кого | Що отримує | Як |
|----------|------------|-----|
| **Ментор** | Показує учням як зони формуються в реальному часі | Replay + пауза на ключових моментах |
| **Учень** | Бачить SMC аналіз розгортатися крок за кроком | Replay на historicals з різною швидкістю |
| **Трейдер** | Тренує pattern recognition на минулих даних | Solo replay з SMC overlay |
| **Спільнота** | Безкоштовний SMC аналіз 13 символів 24/7 | Live dashboard з auto-zones |

### 9.2 Replay + SMC Flow

```
Lesson: "XAU/USD crash 2026-01-15"

Mentor:
  1. Start replay: symbol=XAU/USD, date=2026-01-15, speed=10x
  2. Bars play: M1 → cascade → SMC zones appear on chart
  3. Pause at 09:15 UTC:
     → "See? D1 OB bearish formed here" (zone highlighted)
  4. Resume play:
     → H4 FVG appears at 09:45 (zone appears in real-time)
  5. Pause at 10:30:
     → "M15 CHoCH confirms the bearish thesis"
     → Click on OB → Risk Calculator shows Entry/SL/TP
  6. Resume: price drops to TP
     → "3.8 R:R trade, aligned with HTF bias"

Student sees:
  - Zones appearing/disappearing as bars form
  - Structure labels (BOS/CHoCH) on chart
  - Alignment indicator (HTF bias + LTF signal)
  - Risk Calculator (what the entry would have been)
```

### 9.3 Replay SMC API

```typescript
// WS replay frame extension
interface ReplayFrame {
  type: "replay";
  data: {
    candles: Candle[];
    zones: SmcZone[];      // current SMC state at this replay moment
    swings: SmcSwing[];
    levels: SmcLevel[];
    smc_delta?: SmcDelta;  // what changed since last frame
    replay_meta: {
      bar_index: number;
      total_bars: number;
      replay_time_ms: number;
      speed: number;
    };
  };
}
```

---

## 10. Implementation Plan (P-slices)

Кожен slice ≤150 LOC, 1 інваріант, verify + rollback.

### P1: Foundation Types + Swing Detection

```
Files: core/smc/__init__.py, core/smc/types.py, core/smc/config.py, core/smc/swings.py
Tests: tests/test_smc_swings.py
LOC: ~150
Verify: pytest, detect swings on 500 M1 XAU/USD bars
Dependencies: None (pure, no integration)
```

### P2: Market Structure (BOS/CHoCH)

```
Files: core/smc/structure.py
Tests: tests/test_smc_structure.py
LOC: ~120
Verify: detect BOS/CHoCH on known XAU/USD H1 data
Dependencies: P1 (swings)
```

### P3: Order Blocks

```
Files: core/smc/order_blocks.py
Tests: tests/test_smc_order_blocks.py
LOC: ~130
Verify: detect OBs, verify lifecycle (active → mitigated)
Dependencies: P2 (structure)
```

### P4: Fair Value Gaps

```
Files: core/smc/fvg.py
Tests: tests/test_smc_fvg.py
LOC: ~80
Verify: detect FVGs, verify fill lifecycle
Dependencies: P1 (types)
```

### P5: Liquidity Levels

```
Files: core/smc/liquidity.py
Tests: tests/test_smc_liquidity.py
LOC: ~100
Verify: detect equal highs/lows, PDH/PDL from D1
Dependencies: P1 (swings)
```

### P6: SmcEngine Orchestrator

```
Files: core/smc/engine.py
Tests: tests/test_smc_engine.py, tests/test_smc_incremental.py
LOC: ~150
Verify: full compute + incremental consistency, performance <10ms/bar
Dependencies: P1–P5
```

### P7: SmcRunner + WS Integration

```
Files: runtime/smc/__init__.py, runtime/smc/smc_runner.py
Changes: runtime/ws/ws_server.py (~30 LOC)
Tests: tests/test_smc_runner.py
LOC: ~120
Verify: live WS frame з зонами, delta frame з змінами
Dependencies: P6 + existing ws_server
```

### P8: HTTP API + Config SSOT

```
Changes: ui_chart_v3/server.py (~20 LOC), config.json (smc section)
Files: core/contracts/public/marketdata_v1/smc_v1.json
Tests: tests/test_api_smc.py
LOC: ~60
Verify: /api/smc endpoint, config reload
Dependencies: P7
```

### P9: UI Overlay Rendering

```
Files: ui_v4/src/smc/SmcOverlayRenderer.ts, ui_v4/src/smc/SmcPanel.svelte
Changes: ui_v4/src/chart/engine.ts (hook overlays)
LOC: ~200 (frontend)
Verify: zones visible on chart, toggle layers, colors per smcThemes
Dependencies: P8
```

### P10: Replay Integration

```
Changes: runtime/replay/ (when ADR-0017 is implemented)
LOC: ~20
Verify: replay frame with SMC zones, step-by-step zone appearance
Dependencies: P6 + ADR-0017
```

### P11: Exit Gates + Docs

```
Files: tools/exit_gates/gates/gate_smc_contract.py, docs/contracts.md update
Changes: tools/exit_gates/manifest.json
LOC: ~80
Verify: exit-gates pass, docs current
Dependencies: P8
```

**Орієнтовний timeline**:

```
P1–P5: Week 1  (core/smc/ — pure logic, parallel development possible)
P6:    Week 2  (integration, performance tuning)
P7–P8: Week 2  (runtime integration, API)
P9:    Week 3  (UI rendering)
P10:   After ADR-0017 (replay)
P11:   Week 3  (quality gates)
```

---

## 11. Exit Gates

### Gate: SMC Contract Compliance

```python
# tools/exit_gates/gates/gate_smc_contract.py
"""
Перевіряє:
1. SmcZone.id format: "{kind}_{symbol}_{tf_s}_{anchor_ms}"
2. SmcZone.kind ∈ ZONE_KINDS
3. SmcZone.status ∈ ZONE_STATUSES
4. SmcSwing.kind ∈ SWING_KINDS
5. SmcLevel.kind ∈ LEVEL_KINDS
6. to_wire() output matches ui_v4 TypeScript types
7. I0: core/smc/ has no I/O imports
"""
```

### Gate: SMC Performance Rail

```python
"""
Перевіряє:
1. SmcEngine.on_bar() < max_compute_ms (з config.json)
2. SmcEngine.update(500 bars) < 100ms
3. Wire payload < 10 KB per snapshot
"""
```

### Gate: SMC Determinism

```python
"""
Перевіряє:
1. update(bars) == sequential on_bar(bar) for each bar (determinism)
2. Same bars → same zones/swings/levels (reproducibility)
3. Replay produces identical SMC state as live would have
"""
```

---

## 12. Інваріанти (SMC-specific)

| ID | Інваріант | Enforcement |
|----|-----------|-------------|
| **S0** | core/smc/ = pure logic, NO I/O | I0 dependency rule gate |
| **S1** | SMC не пише в UDS/SSOT JSONL | SmcRunner is read-only, no commit calls |
| **S2** | SMC deterministic: same bars → same zones | Determinism gate + tests |
| **S3** | Zone IDs deterministic: same input → same ID | ID format = `{kind}_{symbol}_{tf_s}_{anchor_ms}` |
| **S4** | Performance: on_bar() < max_compute_ms | Runtime rail + gate |
| **S5** | Config SSOT: all params from config.json:smc | No hardcoded thresholds |
| **S6** | Wire format matches ui_v4 TypeScript types | Contract gate |

---

## 13. Rollback

### Per-slice rollback

Кожен P-slice — незалежний rollback:

- P1–P6: `rm -rf core/smc/` — повертає до стану "без SMC"
- P7: revert ws_server.py changes — frames повертаються до `zones: []`
- P8: revert server.py + config.json — endpoint зникає
- P9: revert ui_v4/ smc components — UI без overlay
- P10: revert replay integration — replay без SMC

### Full rollback

```bash
git checkout -- core/smc/ runtime/smc/ runtime/ws/ws_server.py ui_chart_v3/server.py config.json ui_v4/src/smc/ tests/test_smc_*
rm -rf core/smc/ runtime/smc/ ui_v4/src/smc/
```

Система повертається до стану "графік без SMC overlay" — жодних data corruption чи breaking changes, бо SMC = read-only ephemeral overlay.

---

## 14. Ризики та міtigації

| Ризик | Ймовірність | Вплив | Мітигація |
|-------|-------------|-------|-----------|
| SMC compute > 10ms per bar на Python 3.7 | Medium | Повільні delta frames | numpy vectorization; per-layer disable; async compute |
| Thread safety (SmcRunner in ws_server process) | Medium | Data race | threading.Lock per (symbol, tf); або asyncio-only |
| 13 symbols × 8 TFs = 104 warmups at cold start | Low | Slow cold-start (+10s) | Parallel warmup; priority symbols first |
| config.json bloat (SMC params) | Low | Config drift | Nested smc section, SmcConfig dataclass guard |
| UI rendering perf (30+ zones on chart) | Low | Canvas lag | max_zones_per_tf cap; LOD by zoom |
| Cross-TF bias stale data | Medium | Wrong HTF bias | Timestamp on bias; stale → "neutral" fallback |

---

## 15. Зв'язок з іншими ADR

| ADR | Зв'язок |
|-----|---------|
| 0001 (UDS) | SmcRunner читає bars через UDS.read_window() для warmup |
| 0002 (DeriveChain) | DeriveEngine cascade → trigger SmcEngine.on_bar() |
| 0003 (Cold Start) | SmcRunner warmup після prime_ready |
| 0017 (Replay) | SmcEngine інтегрується в ReplayEngine.step() |
| 0023 (D1 Derive) | D1 бари = input для PDH/PDL/PWH/PWL liquidity levels |

---

## 16. Відкриті питання (для Phase 2+)

1. **Alert system**: Chи SmcRunner публікує alerts ("New OB formed on XAU/USD H4")? Окремий ADR.
2. **Risk Calculator**: Server-side чи UI-only? Якщо server — окремий endpoint.
3. **Backtesting**: SmcEngine + historical bars → win rate by zone type. Потребує окремий initiative.
4. **Custom zones**: User-drawn зони що co-exist з auto-detected. Drawing storage ADR.
5. **Weekly TF (W1)**: Додавання tf_s=604800. Потребує зміну в TF allowlist + derive chain.
