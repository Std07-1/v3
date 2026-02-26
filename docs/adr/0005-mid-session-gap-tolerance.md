# ADR-0004: Mid-session gap tolerance in Derive Chain

## Статус: Accepted

## Контекст

Неліквідні інструменти (NGAS, потенційно HKG33 та інші) отримують від FXCM M1 бари
тільки для хвилин з реальними угодами. Хвилини без угод (V=0) — FXCM не повертає бар.

Поточна каскадна деривація (`M1→M5→M15→M30→H1→H4`) вимагає **всі** source-бари у bucket.
Якщо відсутній 1 M1 mid-session → M5 не деривується → M15 → M30 → H1 → H4 не створюється.

### Приклад: NGAS H4 stuck at 2026-02-20

- NGAS має 35 відсутніх M5 слотів через M1 пропуски (порівняно: XAU=7, GBP/CAD=3)
- Один пропущений M1 mid-session → каскадний збій аж до H4
- `_collect_boundary_tolerant()` толерує лише boundary gaps (session open/close), не mid-session

## Рішення

Розширити `_collect_boundary_tolerant()` у `core/derive.py`:

1. **Новий параметр**: `max_mid_session_gaps: int = 0` (default=0 зберігає поведінку)
2. **Mid-session gap**: замість `return None`, інкрементуємо лічильник
3. **Бюджет**: якщо `mid_session_skips > max_mid_session_gaps` → `return None` (fail)
4. **Константа**: `MAX_MID_SESSION_GAPS = 3` (максимум 3 source-слоти пропущені на bucket)
5. **Degraded-but-loud (I5)**: extension `mid_session_gaps=N` на результуючому барі

### Чому 3?

- Для M1→M5: 3 з 5 M1 пропущені → мінімум 2 M1 присутні (40% coverage)
- Для M5→M15: 3 з 3 M5... ні, M15=3×M5, якщо 3 M5 пропущені — бар не створюється
- На практиці NGAS має 1-2 M1 пропуски на M5-bucket, не 3+
- Каскад не компаундується: якщо M5 створений (degraded), M15 бачить його як наявний

### Що НЕ змінюється

- Strict derive (has_range=True) пріоритет як і раніше — толерантність тільки fallback
- Boundary tolerance працює як раніше (окремий лічильник)
- Контракт бару: поля, часова геометрія, complete/source — без змін
- SSOT/JSONL формат — без змін
- Dependency Rule — без змін (pure logic в core/)

## Інваріанти

- I3 (Final > Preview): зберігається — derived бар все ще complete=True, src="derived"
- I5 (Degraded-but-loud): extension `mid_session_gaps > 0` explicit маркер
- I6 (Stop-rule): не потрібно — інваріанти не змінюються, лише розширення толерантності

## Exit Criteria

1. NGAS H4 деривується для Feb 22-23 (раніше stuck)
2. Extension `mid_session_gaps` присутній на деградованих барах
3. Бари з 0 mid-session gaps — ідентичні результату без PATCH
4. Existing tests проходять

## Rollback

1. Встановити `MAX_MID_SESSION_GAPS = 0` → повна regression до попередньої поведінки
2. Видалити бари з `mid_session_gaps > 0` з SSOT файлів (purge)
3. Файли: тільки `core/derive.py`
