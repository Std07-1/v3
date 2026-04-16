# ADR-0049: Wake Engine — External Consumer IPC via Redis

- **Status**: Accepted
- **Date**: 2026-04-16
- **Author**: Стас
- **Initiative**: `wake_engine_v1`
- **Related ADRs**: ADR-0033 (Context Flow Narrative), ADR-0024 (SMC Engine)
- **Cross-ref**: trader-v3/ADR-034 (Wake Conditions — consumer side), trader-v3/ADR-039 (Integration detail)

---

## 1. Контекст і проблема

### 1.1 Поточний стан

Platform v3 ws_server вже обчислює в delta_loop (кожні 2s):

- Ціни, ATR, сесійний контекст, SMC zones, structure, bias_map, narrative, signals
- Все це in-memory, $0 обчислення, доступне через SmcRunner

Зовнішні споживачі (Telegram bot, інші агенти) отримують ці дані через:

- WS API (`ws://localhost:8000/ws`) — full + delta frames
- HTTP API (`/api/status`, `/api/context`)
- Проблема: **polling з затримкою до 30s**, немає push-механізму для подій

### 1.2 Потреба

Зовнішні споживачі потребують **event-driven** повідомлення коли:

- Ціна перетинає рівень
- Відкривається торгова сесія
- Стрибок волатильності (ATR spike)
- Структурний break (BOS/CHoCH)
- Ціна торкається зони

Platform вже має всі дані для цих перевірок. Потрібен лише механізм реєстрації умов та push-delivery через Redis IPC.

---

## 2. Рішення

### 2.1 Архітектура

Новий модуль `WakeEngine` живе у процесі ws_server поруч зі SmcRunner.
Підключається до існуючого delta_loop — **не окремий процес/thread**.

```
broker → ingest → UDS ──► SmcRunner ──► zones, bias, narrative ──► WS/UI
                              │
                              ▼
                         WakeEngine
                              ├── Читає conditions з Redis
                              ├── Перевіряє кожні 2s (delta_loop tick)
                              └── При match → Redis LPUSH event + PubSub notify
```

**Чому in-process:**

- SmcRunner вже має price/ATR/session/zones in-memory — 0 serialization
- delta_loop 2s = достатня гранулярність
- WakeEngine = **read-only**, не пише в UDS (I1 дотримано)

### 2.2 Модулі

| Модуль | Шар | Призначення |
|--------|-----|-------------|
| `core/smc/wake_types.py` | core | Типи: WakeCondition, WakeEvent, AwarenessState |
| `core/smc/wake_check.py` | core | Pure checker: conditions × state → events ($0, no I/O) |
| `runtime/smc/wake_engine.py` | runtime | I/O wrapper: tick(), Redis read/publish |

### 2.3 Redis IPC контракт

**Вхід** (споживач записує):

```
{ns}:wake:conditions:{symbol}  →  JSON array of WakeCondition
```

**Вихід** (платформа записує):

```
{ns}:wake:events               →  List (LPUSH, max 100, TTL 24h)
{ns}:wake:notify               →  PubSub channel (instant delivery)
```

### 2.4 Типи wake conditions

| Kind | Перевірка | Складність |
|------|-----------|------------|
| `price_cross` | price ≥/≤ level | O(1) |
| `session_open` | calendar session start | O(1) |
| `volatility_spike` | price delta / ATR > threshold | O(1) |
| `zone_touch` | price near zone boundary | O(N zones) |
| `structure_break` | new BOS/CHoCH | O(1) |
| `max_silence` | time since last wake | O(1) |

Всі перевірки — pure math з in-memory даних. $0 cost.

### 2.5 AwarenessState — digest між wake events

Між wake events WakeEngine акумулює зміни:

- price_delta_pct, new zones/structure breaks, session transitions, narrative diff
- При wake event — digest відправляється разом з event

---

## 3. Інваріанти

| ID | Правило | Як дотримано |
|----|---------|-------------|
| I0 | core/ no I/O | wake_types.py, wake_check.py — pure, no imports з runtime |
| I1 | UDS = вузька талія | WakeEngine read-only, не пише в UDS |
| I4 | Один update-потік | Wake events = окремий Redis channel, не змішується з bar updates |
| I5 | Degraded-but-loud | Якщо Redis недоступний — лог WARNING, wake engine disabled, ws_server працює далі |
| S0 | core/smc = pure | wake_check.py = same rules |

---

## 4. Config (config.json SSOT)

```json
{
  "wake_engine": {
    "enabled": true,
    "tick_interval_s": 2.0,
    "max_conditions_per_symbol": 20,
    "event_ttl_s": 86400,
    "max_events": 100
  }
}
```

---

## 5. Boundary Rule

> **WakeEngine — generic IPC mechanism.** Платформа не знає хто споживач (Telegram bot, інший агент, зовнішній сервіс). Вона лише перевіряє conditions та публікує events.
>
> Споживач-specific логіка (як реагувати на wake event, personality, conversation) живе ТІЛЬКИ у споживача (trader-v3/).
>
> Документація споживача (ADR, architecture, prompts) живе ТІЛЬКИ в репозиторії споживача.

---

## 6. Alternatives Considered

| Варіант | Чому ні |
|---------|---------|
| Окремий процес WakeEngine | Дублювання SmcRunner state, IPC overhead, складність |
| Polling з боку споживача | 30s затримка, CPU waste на повторні HTTP calls |
| WS subscription з фільтрами | Складніше ніж Redis PubSub для server-to-server IPC |

---

## 7. Rollback

1. `config.json: wake_engine.enabled = false`
2. Видалити інтеграцію з delta_loop (1 рядок)
3. Модулі wake_types/wake_check/wake_engine можуть залишитись — вони ізольовані
