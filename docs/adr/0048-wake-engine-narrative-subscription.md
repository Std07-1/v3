# ADR-0048: Platform Wake Engine + Narrative Enrichment + Subscription Prep

**Status**: Proposed  
**Date**: 2026-04-14  
**Author**: Стас + Copilot  
**Depends on**: ADR-0033 (Context Flow Narrative), ADR-0036 (Premium Shell), ADR-0039 (Signal Engine), ADR-0045 (VPS Trader Bot)  
**Cross-ref**: trader-v3/ADR-034 (Wake Conditions Architecture — Арчі визначає свою свідомість)  
**Initiative**: `wake_engine_v1`

---

## Ключова думка

> Platform (v3) = тіло. Бачить ціну, відчуває ATR, знає сесію — кожні 2 секунди.  
> Trader bot (trader-v3) = мозок. Думає, аналізує, приймає рішення — тільки коли потрібно.  
> Між ними — **Wake Engine**: тіло каже мозку "прокинься, щось сталось".  
>  
> Побічний ефект: те саме "бачення тіла" можна збагатити та продати як premium наратив —  
> і ніхто не знатиме що за ним стоїть Арчі.

---

## 1. Контекст і Проблема

### 1.1 Поточний стан

**Platform (v3)** має SmcRunner у процесі ws_server, який:
- Живе in-process з delta_loop (кожні 2s, `DEFAULT_DELTA_POLL_S = 2.0`)
- Має доступ до: `_last_prices`, `_calendars` (MarketCalendar), `_compute_tfs`, `_tda_runner`
- Обчислює: zones, structure, bias_map, narrative, signals, shell — **$0 обчислення**
- Публікує через render_frame → WS → UI

**Trader bot (trader-v3)** — окремий процес на VPS:
- Підключається до `GET /api/context` кожні 30 секунд
- Читає через Redis IPC: `{ns}:agent:state`, `{ns}:agent:feed`, `{ns}:archi:chat`
- Коли потрібно думати — викликає Claude Sonnet → ~$0.03 за виклик
- Проблема: **30s polling = максимальна затримка пробудження 30 секунд**

**Narrative (NarrativeBlock, ADR-0033):**
- Є mode (trade/wait), headline, scenarios, market_phase, session context
- Генерується synthesize_narrative() — pure function, $0
- Відображається в NarrativePanel.svelte на chart HUD
- **Проблема**: наратив = технічний SMC контекст, але не "жива аналітика трейдера"

### 1.2 Три проблеми

| # | Проблема | Вплив |
|---|----------|-------|
| P1 | Wake conditions (trader-v3 ADR-034) потребують real-time перевірки, а бот поллить кожні 30s | Арчі може пропустити момент (price_cross, volatility_spike) і не прокинутись |
| P2 | Наратив — сухий технічний контекст. Не має "думки трейдера", thesis, conviction | Виглядає як генерований — не можна продавати як premium продукт |
| P3 | Немає інфраструктури для subscription/feature gating | Коли захочемо перейти з free → paid, доведеться переробляти wire format |

### 1.3 Рішення

Один модуль `runtime/smc/wake_engine.py` — живе у ws_server поруч зі SmcRunner:
1. **WakeConditionChecker**: перевіряє wake conditions Арчі кожні 2s (delta_loop tick) — $0
2. **NarrativeEnricher**: додає "thesis layer" до NarrativeBlock — Арчі-збагачений контент
3. **Feature tier marker**: кожне поле у wire frame має `tier: "free" | "premium"` для gating

---

## 2. Архітектура

### 2.1 Потік даних

```
                          ┌──────────────────────────────────────────┐
                          │           ws_server (один процес)        │
                          │                                          │
  broker → ingest → UDS ──┤ SmcRunner ──► zones, bias, narrative     │
                          │     │                                    │
                          │     ▼                                    │
                          │ WakeEngine                               │
                          │     ├── WakeChecker ─[match]──► Redis    │──► trader-v3 bot
                          │     │   (перевірка кожні 2s)    IPC      │    (прокидається)
                          │     │                                    │
                          │     ├── NarrativeEnricher                │
                          │     │   (thesis layer з Redis cache)     │──► render_frame
                          │     │                                    │    → WS → UI
                          │     └── TierMarker                       │
                          │         (free/premium field tags)        │──► subscription gate
                          │                                          │
                          └──────────────────────────────────────────┘
```

### 2.2 Integration point

WakeEngine підключається до **існуючого** delta_loop у ws_server:

```python
# ws_server.py _global_delta_loop(), після SMC обчислень:
if _wake_engine is not None:
    _wake_engine.tick(
        prices=_smc_runner._last_prices,      # {symbol: float}
        snapshots=_smc_runner,                 # SmcRunner ref
        calendars=_smc_runner._calendars,      # MarketCalendar[]
        ts_ms=int(time.time() * 1000),
    )
```

**Чому тут, а не окремий процес/thread:**
- SmcRunner вже має price/ATR/session/zones in-memory — копіювати нема сенсу
- delta_loop тикає кожні 2s — ідеальна гранулярність для wake conditions
- Один процес = 0 IPC overhead, 0 serialization cost
- S0/S1: WakeEngine = read-only, не пише в UDS

### 2.3 Модулі

| Модуль | Шар | Призначення |
|--------|-----|-------------|
| `core/smc/wake_types.py` | core | Типи: WakeCondition, WakeEvent, AwarenessState |
| `core/smc/wake_check.py` | core | Pure $0 checker: conditions × state → events |
| `runtime/smc/wake_engine.py` | runtime | I/O: tick(), Redis publish, thesis cache, tier marking |
| `runtime/smc/narrative_enricher.py` | runtime | Thesis injection: Redis read → NarrativeBlock enrichment |

---

## 3. Wake Condition Checker (P1)

### 3.1 Як це працює

Після кожного Sonnet-виклику, trader-v3 бот записує wake conditions у Redis:

```python
# Бот записує (trader-v3, після аналізу):
redis.set(f"{ns}:wake:conditions:{symbol}", json.dumps([
    {"kind": "price_cross", "params": {"level": 4680, "direction": "below"},
     "reason": "Liquidity sweep test — below PDL"},
    {"kind": "session_open", "params": {"session": "london"},
     "reason": "London open — potential sweep of Asia low"},
    {"kind": "volatility_spike", "params": {"atr_mult": 2.0},
     "reason": "Sharp move may invalidate current thesis"},
    {"kind": "max_silence", "params": {"hours": 4},
     "reason": "Safety net — maintain market awareness"}
]))
```

Platform WakeEngine перевіряє кожні 2s (tick у delta_loop):

```python
# runtime/smc/wake_engine.py:
class WakeEngine:
    def tick(self, prices, snapshots, calendars, ts_ms):
        for symbol, conditions in self._active_conditions.items():
            price = prices.get(symbol)
            if price is None:
                continue
            for cond in conditions:
                if self._check(cond, price, snapshots, calendars, ts_ms):
                    self._fire_wake_event(symbol, cond, price, ts_ms)
```

### 3.2 Wake condition types

| Kind | Перевірка | Складність | $0? |
|------|-----------|------------|-----|
| `price_cross` | `price >= level` або `price <= level` | O(1) | ✅ |
| `session_open` | `calendar.is_session_start(session, ts_ms)` | O(1) | ✅ |
| `volatility_spike` | `abs(price - prev_price) / atr > mult` | O(1) | ✅ |
| `zone_touch` | `abs(price - zone.high) < atr * tolerance` | O(N zones) | ✅ |
| `structure_break` | `new BOS/CHoCH in SmcSnapshot` | O(1) read | ✅ |
| `max_silence` | `ts_ms - last_wake_ts > hours * 3600000` | O(1) | ✅ |

Всі перевірки — pure math з наявних in-memory даних. **$0 cost**.

### 3.3 Wake event delivery

При match — WakeEngine записує event у Redis:

```python
# Redis key: {ns}:wake:events (list, LPUSH, TTL = 24h)
event = {
    "ts_ms": ts_ms,
    "symbol": symbol,
    "kind": cond["kind"],
    "reason": cond["reason"],
    "price": price,
    "meta": {"atr": atr_est, "session": current_session}
}
redis.lpush(f"{ns}:wake:events", json.dumps(event))
redis.ltrim(f"{ns}:wake:events", 0, 99)  # max 100 events
redis.publish(f"{ns}:wake:notify", json.dumps({"symbol": symbol, "kind": cond["kind"]}))
```

Trader-v3 бот підписаний на Redis PubSub `{ns}:wake:notify` — реагує миттєво (< 100ms).

### 3.4 Awareness Accumulator

Між wake events WakeEngine акумулює "awareness" — зміни від попереднього wake:

```python
# core/smc/wake_types.py:
@dataclasses.dataclass(frozen=True)
class AwarenessState:
    """Що змінилось від останнього wake — digest для бота."""
    price_delta_pct: float       # % зміна ціни
    atr_delta_pct: float         # % зміна ATR
    new_zones: int               # кількість нових зон
    mitigated_zones: int         # зони що були mitigation-tested
    structure_breaks: int        # нові BOS/CHoCH
    session_changes: List[str]   # ["asia_close", "london_open"]
    max_zone_proximity_pct: float  # наскільки близько до найближчої зони (% ATR)
```

Бот отримує awareness state разом з wake event — знає ВСЕ що пропустив.

---

## 4. Narrative Enrichment (P2)

### 4.1 Проблема

Поточний NarrativeBlock (ADR-0033):
```
headline: "🔴 SELL setup ready — M15 aligned with D1 bearish bias"
scenarios: [{trigger: "ready", tf_label: "M15", …}]
```

Це **технічний контекст** — корисний для трейдера, але:
- Не має "думки" — чому саме sell, яка conviction
- Не має thesis — що ми чекаємо, яка картина
- Виглядає "сгенерованим" — не можна продавати як premium

### 4.2 Thesis Layer

NarrativeEnricher додає "thesis" до NarrativeBlock — enriched_narrative:

```python
# core/smc/wake_types.py:
@dataclasses.dataclass(frozen=True)
class ThesisLayer:
    """Арчі-сгенерований thesis overlay для NarrativeBlock.
    
    Записується ботом у Redis після Sonnet-аналізу.
    Platform лише читає і додає до narrative wire frame.
    """
    thesis: str           # "Жду sweep PDL 4650 → reaction із London killzone"
    conviction: str       # "high" | "medium" | "low"
    key_level: str        # "PDL 4650 — main target for liquidity sweep"
    invalidation: str     # "Break above 4730 invalidates sell thesis"
    updated_at_ms: int    # Коли Арчі останній раз оновив thesis
    freshness: str        # "fresh" (<1h) | "aging" (1-4h) | "stale" (>4h) — computed
```

### 4.3 Як бот записує thesis

Після кожного Sonnet-аналізу, trader-v3 бот записує thesis у Redis:

```python
# trader-v3 бот (після analysis):
redis.hset(f"{ns}:thesis:{symbol}", mapping={
    "thesis": "Жду sweep PDL 4650 → reaction із London killzone",
    "conviction": "high",
    "key_level": "PDL 4650",
    "invalidation": "Break above 4730",
    "updated_at_ms": str(int(time.time() * 1000)),
})
redis.expire(f"{ns}:thesis:{symbol}", 86400)  # TTL 24h
```

### 4.4 Platform enrichment

NarrativeEnricher в ws_server читає thesis з Redis (cached, refresh кожні 10s):

```python
# runtime/smc/narrative_enricher.py:
class NarrativeEnricher:
    _cache: Dict[str, ThesisLayer] = {}
    _cache_ts: float = 0
    CACHE_TTL = 10.0  # refresh кожні 10 секунд
    
    def enrich(self, symbol: str, narrative: NarrativeBlock) -> Dict[str, Any]:
        """Додає thesis layer до narrative wire dict."""
        wire = narrative_to_wire(narrative)
        thesis = self._get_cached_thesis(symbol)
        if thesis is not None:
            wire["thesis"] = {
                "text": thesis.thesis,
                "conviction": thesis.conviction,
                "key_level": thesis.key_level,
                "invalidation": thesis.invalidation,
                "freshness": thesis.freshness,
                "tier": "premium",  # P3: feature gating
            }
        return wire
```

### 4.5 Що бачить UI

**Free tier** (NarrativePanel, як зараз):
```
🔴 SELL setup ready — M15 aligned with D1 bearish bias
▸ Scenarios: Target 4650 (M15)
```

**Premium tier** (enriched narrative):
```
🔴 SELL setup ready — M15 aligned with D1 bearish bias
💡 Thesis: Жду sweep PDL 4650 → reaction із London killzone
   Conviction: HIGH | Key level: PDL 4650
   Invalidation: Break above 4730
▸ Scenarios: ...
```

**Ззовні виглядає як**: "SMC-система з вбудованою аналітикою" — ніхто не знатиме що за thesis стоїть AI-агент Арчі.

---

## 5. Subscription Prep (P3)

### 5.1 Feature tier architecture

Кожне поле в wire frame отримує implicit tier assignment:

```python
# core/smc/wake_types.py:
class FeatureTier:
    FREE = "free"
    PREMIUM = "premium"

# Tier mapping (config.json SSOT):
TIER_MAP = {
    "narrative.headline":      "free",
    "narrative.mode":          "free",
    "narrative.scenarios":     "free",      # basic scenarios
    "narrative.thesis":        "premium",   # Арчі thesis
    "narrative.conviction":    "premium",
    "narrative.invalidation":  "premium",
    "signals":                 "free",      # basic signals
    "signals.entry_price":     "premium",   # exact numbers
    "signals.stop_loss":       "premium",
    "signals.take_profit":     "premium",
    "shell.thesis_bar":        "premium",
    "wake_status":             "premium",   # Арчі presence
}
```

### 5.2 Config.json SSOT

```json
{
    "subscription": {
        "enabled": false,
        "default_tier": "free",
        "tier_map": {
            "narrative_thesis": "premium",
            "signal_exact_prices": "premium",
            "shell_thesis_bar": "premium",
            "wake_status": "premium"
        },
        "enforcement": "server_strip"
    }
}
```

- `enabled: false` — поки що all features = free (ніхто нічого не помічає)
- Коли `enabled: true` — ws_server strip-іть premium поля з wire frame для free users
- `enforcement: "server_strip"` — strip на сервері, UI не довіряємо (security)

### 5.3 Implementation approach

Subscription gate = **thin middleware** у ws_server перед send_json():

```python
# runtime/ws/tier_gate.py (thin, ~30 LOC):
def strip_for_tier(frame: dict, user_tier: str, tier_map: dict) -> dict:
    """Strip premium fields from frame for free users."""
    if user_tier == "premium":
        return frame  # no-op
    out = dict(frame)
    if "narrative" in out and isinstance(out["narrative"], dict):
        for key in ("thesis", "conviction", "invalidation"):
            out["narrative"].pop(key, None)
    if "signals" in out and isinstance(out["signals"], list):
        for sig in out["signals"]:
            for key in ("entry_price", "stop_loss", "take_profit"):
                sig.pop(key, None)
    if "shell" in out and isinstance(out["shell"], dict):
        out["shell"].pop("thesis_bar", None)
    out.pop("wake_status", None)
    return out
```

### 5.4 User identification (мінімальна prep)

Поки що — auth не потрібен (все free). Prep для subscription:
- WS connect може передати `?token=xxx` або Bearer header
- ws_server зберігає `WsSession.tier = "free" | "premium"`
- По замовчуванню `tier = config.subscription.default_tier`
- Перемикання: змінити config.json `subscription.enabled = true` → рестарт ws_server

---

## 6. Presence Layer (від ADR-034)

### 6.1 Wake status у render_frame

WakeEngine публікує presence status у render_frame:

```python
# В render_frame будуванні (ws_server.py):
if _wake_engine is not None:
    frame["wake_status"] = _wake_engine.get_presence(symbol)
    # → {"state": "watching", "last_wake_reason": "session_open",
    #    "last_wake_ts_ms": 1713099600000, "active_conditions": 3,
    #    "next_expected": "volatility_spike or max_silence (4h)"}
```

### 6.2 Що бачить UI

NarrativePanel (або ChartHud badge):
```
🟢 Watching · 3 wake conditions active · Last wake: London open (2h ago)
```

Або:
```
🔵 Thinking... · Triggered: price_cross PDL 4680
```

Або:
```
⚫ Sleeping · Next check: volatility_spike or max_silence (2h left)
```

---

## 7. Інваріанти

| ID | Правило | Enforcement |
|----|---------|-------------|
| **W0** | WakeEngine = read-only, не пише в UDS (S1 compliance) | Code review + test |
| **W1** | Wake check = $0 (pure math, in-memory data) | No API calls, no Redis writes in check loop |
| **W2** | Thesis layer = Redis read only (бот пише, platform читає) | NarrativeEnricher does not SET/HSET |
| **W3** | Tier gate = server-side, UI не довіряємо | strip_for_tier у ws_server, не в frontend JS |
| **W4** | Config SSOT: всі tier mappings у config.json | Заборонено hardcoded tier checks у коді |
| **W5** | subscription.enabled=false = all features available | Default = free = повний доступ до всього |
| **W6** | Wake event delivery < 1s від match до Redis publish | Check in delta_loop tick = 2s resolution, publish = sync Redis LPUSH |

---

## 8. Wire Format Changes

### 8.1 Narrative extension

```typescript
// ui_v4/src/lib/types.ts — NarrativeBlock extension:
interface NarrativeBlock {
    // ... existing fields (mode, headline, scenarios, etc.)
    
    // ADR-0048: thesis layer (premium, nullable)
    thesis?: {
        text: string;           // Арчі thesis
        conviction: "high" | "medium" | "low";
        key_level: string;
        invalidation: string;
        freshness: "fresh" | "aging" | "stale";
        tier: "premium";
    };
}
```

### 8.2 Wake status (new top-level field)

```typescript
// ui_v4/src/lib/types.ts:
interface WakeStatus {
    state: "watching" | "thinking" | "sleeping";
    last_wake_reason: string;
    last_wake_ts_ms: number;
    active_conditions: number;
    next_expected: string;
}

// In render frame:
interface RenderFrame {
    // ... existing fields
    wake_status?: WakeStatus;   // ADR-0048
}
```

### 8.3 Backward compatibility

- Нові поля = **optional** (nullable)
- Старий UI без ADR-0048 support — просто ігнорує нові поля
- `subscription.enabled = false` → всі поля присутні (no strip)

---

## 9. Redis Keys (IPC протокол)

| Key | Writer | Reader | TTL | Формат |
|-----|--------|--------|-----|--------|
| `{ns}:wake:conditions:{symbol}` | trader-v3 bot | WakeEngine | 24h | JSON list of WakeCondition |
| `{ns}:wake:events` | WakeEngine | trader-v3 bot | 24h | JSON list (LPUSH, max 100) |
| `{ns}:wake:notify` | WakeEngine (PubSub) | trader-v3 bot (Subscribe) | — | JSON `{symbol, kind}` |
| `{ns}:wake:awareness:{symbol}` | WakeEngine | trader-v3 bot | 24h | JSON AwarenessState |
| `{ns}:thesis:{symbol}` | trader-v3 bot | NarrativeEnricher | 24h | HASH: thesis, conviction, key_level, etc. |
| `{ns}:wake:presence:{symbol}` | WakeEngine | ws_server render_frame | 60s | JSON WakePresence |

**Namespace**: `{ns}` = `config.redis.namespace` (default: `v3_local`).

---

## 10. Альтернативи

### 10.1 Окремий процес для WakeEngine

❌ **Відхилено**: SmcRunner вже має price/zones/sessions in-memory в ws_server. Окремий процес = IPC overhead + дублювання стану + serialization cost. Немає причин.

### 10.2 Wake conditions через HTTP polling (як зараз)

❌ **Відхилено**: 30s polling = максимум 30s latency. Redis PubSub = <100ms. Різниця критична для price_cross events.

### 10.3 Narrative enrichment у frontend (client-side)

❌ **Відхилено**: Порушує W3 (security). Якщо thesis у wire frame — будь-хто з DevTools бачить premium контент. Server-side strip = єдиний варіант.

### 10.4 Subscription через окремий мікросервіс

❌ **Відхилено**: Over-engineering для поточного масштабу (1-10 users). Thin gate в ws_server = достатньо. Коли >100 users — можна виділити.

---

## 11. Залежності

### 11.1 Від v3 platform

- `runtime/ws/ws_server.py` — delta_loop hook
- `runtime/smc/smc_runner.py` — SmcRunner як data source
- `core/smc/types.py` — NarrativeBlock, SmcSnapshot
- `config.json` — нова секція `subscription` + `wake_engine`
- `ui_v4/src/lib/types.ts` — NarrativeBlock thesis extension

### 11.2 Від trader-v3

- trader-v3/ADR-034 — Wake Conditions Protocol (бот пише conditions → platform перевіряє)
- Бот записує thesis у Redis після Sonnet-аналізу
- Бот підписується на `{ns}:wake:notify` PubSub

### 11.3 Не залежить від

- Конкретної моделі AI (Sonnet/Opus/Haiku) — platform лише передає
- Frontend framework — wire format = JSON, UI може бути будь-який
- Auth provider — WsSession.tier = мінімальна абстракція

---

## 12. Config.json Changes

```json
{
    "wake_engine": {
        "enabled": true,
        "check_interval_ticks": 1,
        "max_conditions_per_symbol": 10,
        "awareness_accumulator_enabled": true,
        "presence_broadcast_enabled": true,
        "thesis_cache_ttl_s": 10,
        "redis_keys": {
            "conditions": "wake:conditions",
            "events": "wake:events",
            "notify_channel": "wake:notify",
            "awareness": "wake:awareness",
            "thesis": "thesis",
            "presence": "wake:presence"
        }
    },
    "subscription": {
        "enabled": false,
        "default_tier": "free",
        "tier_map": {
            "narrative_thesis": "premium",
            "signal_exact_prices": "premium",
            "shell_thesis_bar": "premium",
            "wake_status": "premium"
        },
        "enforcement": "server_strip"
    }
}
```

---

## 13. P-Slices (Implementation Plan)

### P1: Types + Wake Checker (core) — ≤80 LOC

**Files**: `core/smc/wake_types.py` (NEW)

- `WakeCondition` (frozen dataclass): kind, params, reason
- `WakeEvent` (frozen dataclass): ts_ms, symbol, kind, reason, price, meta
- `AwarenessState` (frozen dataclass): price_delta_pct, atr_delta_pct, structure_breaks, etc.
- `ThesisLayer` (frozen dataclass): thesis, conviction, key_level, invalidation, freshness
- `FeatureTier` constants

**Verify**: `python -c "from core.smc.wake_types import *"`

### P2: Pure Wake Check Logic (core) — ≤100 LOC

**Files**: `core/smc/wake_check.py` (NEW)

- `check_condition(cond: WakeCondition, price: float, snapshot, calendar, ts_ms) -> bool`
- Pure function, S0-compliant, no I/O
- Unit tests: price_cross, session_open, volatility_spike, max_silence

**Verify**: `python -m pytest tests/test_wake_check.py -v`

### P3: WakeEngine runtime wrapper — ≤120 LOC

**Files**: `runtime/smc/wake_engine.py` (NEW)

- `WakeEngine.__init__(config, redis_client, namespace)`
- `WakeEngine.tick(prices, snapshots, calendars, ts_ms)` — call from delta_loop
- `WakeEngine._load_conditions()` — Redis read, cached
- `WakeEngine._fire_wake_event()` — Redis LPUSH + PUBLISH
- `WakeEngine.get_presence(symbol)` — for render_frame

**Verify**: Integration test з мокованим Redis

### P4: ws_server integration — ≤30 LOC diff

**Files**: `runtime/ws/ws_server.py` (MODIFY)

- Import WakeEngine
- Init at startup (якщо `config.wake_engine.enabled`)
- Call `_wake_engine.tick()` у delta_loop
- Add `frame["wake_status"]` у render_frame

**Verify**: `python -m runtime.ws.ws_server` starts without errors + WS frame має wake_status

### P5: NarrativeEnricher — ≤80 LOC

**Files**: `runtime/smc/narrative_enricher.py` (NEW)

- `NarrativeEnricher.__init__(redis_client, namespace, cache_ttl)`
- `NarrativeEnricher.enrich(symbol, narrative) -> dict` — thesis injection
- Redis HGETALL cached read

**Verify**: Unit test: enrich() з мокованим Redis та NarrativeBlock

### P6: Subscription tier gate — ≤40 LOC

**Files**: `runtime/ws/tier_gate.py` (NEW)

- `strip_for_tier(frame, user_tier, tier_map) -> dict`
- Pure function + config-driven

**Verify**: Unit test: strip removes premium fields for free tier, passes all for premium

### P7: Config.json + types.ts — ≤40 LOC diff

**Files**: `config.json` (MODIFY), `ui_v4/src/lib/types.ts` (MODIFY)

- Add `wake_engine` + `subscription` sections to config
- Add `ThesisLayer` + `WakeStatus` TypeScript types
- NarrativeBlock.thesis optional field

**Verify**: `npx vite build` passes, TypeScript types compile

### P8: UI NarrativePanel thesis display — ≤60 LOC diff

**Files**: `ui_v4/src/layout/NarrativePanel.svelte` (MODIFY)

- Render thesis block if narrative.thesis exists
- Freshness indicator (green/yellow/red dot)
- Conditional "premium" badge if subscription.enabled

**Verify**: Visual — thesis shows in NarrativePanel when data present

---

## 14. Бюджет (вартість)

| Компонент | Вартість | Частота |
|-----------|----------|---------|
| WakeEngine tick (pure math) | $0 | кожні 2s |
| Redis read conditions (cached) | $0 | кожні 30s refresh |
| Redis read thesis (cached) | $0 | кожні 10s refresh |
| Redis LPUSH + PUBLISH (wake event) | $0 | на match (~2-5/day) |
| Sonnet виклик (бот, не platform!) | ~$0.03 | на wake event |
| **Загальне навантаження platform** | **$0 / день** | — |

Platform cost = $0. Все AI-обчислення — в trader-v3 боті (окремий процес, окремий бюджет).

---

## 15. Risks

| # | Ризик | Mitigation |
|---|-------|------------|
| R1 | Redis down → wake conditions не перевіряються | Degraded-but-loud: WakeEngine.tick() catches exceptions, logs warning, sets degraded flag |
| R2 | Stale thesis у cache → UI показує застарілу "думку" | `freshness` field computed from `updated_at_ms`: fresh <1h, aging 1-4h, stale >4h. UI показує індикатор |
| R3 | Subscription strip bypass (DevTools) | W3: strip server-side. Навіть з DevTools — premium payload не приходить у WS frame для free users |
| R4 | WakeEngine tick > 50ms → delta_loop jitter | W6 rail: log_warning якщо tick > 10ms. Max conditions per symbol = 10. Performance budget respected |

---

## 16. Rollback Plan

- `wake_engine.enabled = false` у config.json → WakeEngine не ініціалізується
- `subscription.enabled = false` (default) → всі поля присутні, no strip
- Нові Redis keys з TTL = самоочищаються через 24h
- NarrativeBlock thesis = optional → старий UI/code просто ігнорує
- wake_status = optional → відсутність = як раніше

**Повний rollback = одна зміна config.json + рестарт ws_server.**

---

## 17. Success Criteria

| # | Критерій | Як перевірити |
|---|----------|---------------|
| SC-1 | Wake event delivery < 2s від price_cross | Лог: wake event timestamp vs delta_loop tick |
| SC-2 | Thesis відображається у NarrativePanel | Visual: thesis text + conviction badge видно |
| SC-3 | Free tier не бачить premium fields | DevTools → WS frame inspection: no thesis, no exact prices |
| SC-4 | platform CPU overhead < 1% | VPS htop: ws_server CPU before/after WakeEngine |
| SC-5 | Backward compatible: UI без ADR-0048 працює | Deploy platform, keep old UI → no errors |

---

## 18. Зв'язок з іншими ADR

| ADR | Зв'язок |
|-----|---------|
| trader-v3/ADR-034 | Визначає Wake Conditions Protocol (бот-сторона). ADR-0048 = platform-сторона |
| ADR-0033 | NarrativeBlock — enrichment target. Thesis layer розширює, не замінює |
| ADR-0036 | Shell payload — thesis_bar premium tier |
| ADR-0039 | Signal Engine — signal exact prices = premium tier candidate |
| ADR-0045 | VPS Trader Bot — бот як writer thesis/conditions, platform як reader/checker |
| ADR-0042 | Delta Frame Sync — wake_status додається до thick delta frame |
