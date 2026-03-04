# ADR-0027: Client-Side Replay (TradingView-style)

- **Статус**: Accepted
- **Дата**: 2026-02-28
- **Автор**: code-review-audit
- **Initiative**: `replay_v2`

## Контекст і проблема

ADR-0017 реалізував backend-driven replay: окремий Python-процес читає M1 JSONL з диска, фідить UDS+DeriveEngine, Redis → UI. Це працює, але:

1. **Важкий для користувача** — CLI-запуск, окремий процес, Redis round-trips
2. **Не інтуїтивний** — немає UI-кнопок, scrubbing, play/pause в інтерфейсі
3. **Ресурсозатратний** — Redis write/read на кожен крок, derive engine працює вхолосту

Користувач очікує replay як у TradingView: кнопка в UI → scrub → play → candle-by-candle.

## Розглянуті варіанти

### A. Backend-driven replay (ADR-0017, поточний)

- **Плюси**: replay проходить через весь pipeline (UDS/derive), перевіряє correctness
- **Мінуси**: важкий, CLI-only, не підходить для щоденного трейдерського workflow
- **Рішення**: залишити для CI/audit, але не для UI

### B. Client-side replay (ОБРАНО)

- **Плюси**: zero backend overhead, дані вже в пам'яті, миттєва швидкість, простий UI
- **Мінуси**: replay не проходить derive pipeline (бачить тільки вже обчислені бари)
- **Архітектура**: UI завантажує бари нормально → replay = visibility filter → chart.setData(slice)

### C. Hybrid: backend generates, UI controls

- **Плюси**: replay проходить derive
- **Мінуси**: складність, latency, over-engineering

## Рішення

Client-side replay (B). Дані вже завантажені через WS (full frame). Replay = **контроль видимості** барів:

1. Усі candles зберігаються в `replayStore.allCandles[]`
2. `cursorIndex` визначає скільки барів показувати: `candles.slice(0, cursorIndex)`
3. Play = setInterval що інкрементує cursorIndex (1–50 bars/sec)
4. SMC overlay фільтрується по `cursorMs` (zones/swings/levels з якорем ≤ cursor)
5. TF switching: cursor = timestamp (`posMs`), при switch binary search в новому масиві  
6. Delta frames під час replay — drop (при exit replay → request fresh full frame)

### Ключові файли

| Файл | Роль |
|------|------|
| `stores/replayStore.svelte.ts` | SSOT replay state (Svelte 5 runes) |
| `layout/ReplayBar.svelte` | UI controls (play/pause, speed, scrubber, step) |
| `layout/ChartPane.svelte` | Intercept frame processing in replay mode |
| `App.svelte` | Wire ReplayBar, enter/exit button |

### UI Layout

```
┌─────────────────────────────────────────────┐
│  [chart area — candles up to cursor]        │
│  [SMC overlay — filtered by cursor]         │
├─────────────────────────────────────────────┤
│ ◀ |◀ ▶/⏸ ▶| ▶  ═══════●═══════  3x  ✕    │
│  step  play  step    scrubber   speed exit  │
└─────────────────────────────────────────────┘
```

### Invariants

- **R0**: Replay не пише в UDS/Redis — client-only state
- **R1**: Replay не блокує WS — heartbeats/config frames проходять
- **R2**: Exit replay → server отримує switch action → fresh full frame → нормальний render
- **R3**: cursor = timestamp (posMs) = SSOT при TF switch
- **R4**: Speed options: 1, 2, 5, 10, 25, 50 bars/sec

### Keyboard Shortcuts

| Key | Action |
|-----|--------|
| Space | Play / Pause |
| ← | Step back 1 bar |
| → | Step forward 1 bar |
| Shift+← | Step back 10 bars |
| Shift+→ | Step forward 10 bars |
| Escape | Exit replay |

## Наслідки

- ADR-0017 backend replay залишається для CI/audit use case
- WS actions (replay_seek/play/pause) в types.ts — deprecated для цього flow
- Потрібен "Enter Replay" кнопка у UI (ChartHud або toolbar)
- `replayStore` = Svelte 5 `.svelte.ts` file з `$state` runes

## Rollback

1. Видалити `stores/replayStore.svelte.ts`
2. Видалити `layout/ReplayBar.svelte`
3. Revert зміни в ChartPane.svelte та App.svelte
4. Видалити цей ADR (або Deprecated)
