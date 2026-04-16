---
applyTo: "core/**"
---

# core/ — Pure Logic Layer

**SSOT root**: `.github/copilot-instructions.md` → `I0 Dependency Rule`.

## Жорсткі правила для `core/`

### I0 — Dependency Rule (hard)
- `core/` **НЕ імпортує** `runtime/`, `ui*/`, `tools/`
- `core/` не робить I/O: no Redis, no HTTP, no file read/write, no subprocess
- Якщо функції потрібні дані — приймай через параметр, не йди шукати

### I2 — Time geometry (CandleBar — end-excl)
- `CandleBar.close_time_ms = open_time_ms + tf_s * 1000`
- Redis шар конвертує в end-incl (`close_ms - 1`). `core/` ПРО ЦЕ НЕ ДУМАЄ.

### X13 — CandleBar `.low` НЕ `.l`
- Dataclass поля: `.o .h .low .c .v`
- Wire dict: `{"l": ...}` — тільки при (де)серіалізації на межі
- `core/` використовує dataclass → завжди `.low`

### Contract-first
- Новий payload → TypedDict або dataclass у `core/contracts/` або `core/model/`
- Без контракту у `runtime/` ніхто не приймає

### Детермінізм
- Same input → same output (немає random без seed, немає `datetime.now()` усередині logic — передавай `now_ms: int` параметром)
- Без глобального state у pure модулях

## Pattern reminders

- Bucket math — єдиний `core/buckets.py:resolve_anchor_offset_ms()`. Inline if-ladder у ≥3 місцях = SSOT violation.
- Derive chain — єдиний `core/derive.py:DERIVE_CHAIN`. Другий dict з правилами заборонено.
- FINAL_SOURCES — єдиний перелік у `core/model/bars.py`. Не хардкодь строки "stream" / "polled" / "derived" у if-else.
