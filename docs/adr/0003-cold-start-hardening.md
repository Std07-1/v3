# ADR-0003: Cold Start Hardening

> **Статус**: IMPLEMENTED (всі 4 slices S1–S4 завершені)  
> **Дата**: 2026-02-19  
> **Initiative**: `cold_start_hardening`  
> **Попередник**: Аналітичний меморандум (docs/Аналітичний меморандум.md)  
> **Навігація**: [docs/index.md](index.md)

---

## Зміст

1. [Контекст та ціль](#1-контекст-та-ціль)
2. [AS-IS: Поточна послідовність старту](#2-as-is-поточна-послідовність-старту)
3. [Що вже виправлено](#3-що-вже-виправлено-before-adr-0003)
4. [Failure Model](#4-failure-model-актуальний)
5. [Gap Analysis — залишкові проблеми](#5-gap-analysis--залишкові-проблеми)
6. [Рішення та P-slices](#6-рішення-та-p-slices)
7. [Інваріанти (не змінюються)](#7-інваріанти-не-змінюються)
8. [Exit Gates](#8-exit-gates)
9. [Таблиці лімітів (window / cold-start / retention)](#9-таблиці-лімітів)
10. [Rollback](#10-rollback)

---

## 1. Контекст та ціль

**Ціль**: Ідеальний cold start для 13 символів × 8 TF з TradingView-клас UX.

**SLO**:
- p95 < 200мс time-to-first-candle (після bootstrap)
- 0 split-brain випадків
- 0 silent fallback
- Bootstrap duration < 60с (при стабільному FXCM)
- Broker down → degraded-but-loud, NOT crash

**Scope** (5 процесів):
- connector (D1-only fetcher)
- tick_publisher (FXCM WS tick stream)
- tick_preview (tick → preview M1/M3)
- m1_poller (M1 History → final M1 + DeriveEngine cascade M3→M5→…→H4)
- ui (HTTP server, port 8089)

**Non-goals**:
- Не міняти derive chain
- Не додавати нові TF/процеси
- Не міняти геометрію часу
- Не міняти контракти

---

## 2. AS-IS: Поточна послідовність старту

```
Supervisor (app.main --mode all)
 │
 ├─[1] connector          → FXCM login (2-30s) → bootstrap_and_warmup()
 │      ├─ _bootstrap_from_disk()           # disk tail → watermark D1
 │      ├─ _prime_redis_from_disk()         # disk→Redis (budget=5s, 8 TFs)
 │      ├─ set_prime_ready()                # Redis prime:ready (TTL=6h)
 │      └─ _cold_start_base_from_broker()   # FXCM fetch D1 (180 bars)
 │
 ├─[2] tick_preview       → Redis sub (no FXCM needed)
 ├─[3] tick_publisher     → FXCM WS connect
 │
 ├─[4] m1_poller          → _bootstrap_warmup()
 │      ├─ Redis priming M1→H4 (7 TFs, no budget)         ← FIX 20260219-027
 │      ├─ M1Buffer warmup (10 bars per sym, hardcoded)
 │      ├─ DeriveEngine warmup (M1+M5+M15+M30+H1 з диску) ← FIX 20260219-021
 │      └─ tail_catchup (FXCM fetch gap M1)
 │
 ├─ _wait_for_prime_ready(timeout=20s) → return IGNORED!   ← ПРОБЛЕМА P3
 │
 └─[5] ui                 → _bootstrap_warmup()
        └─ disk→RAM (13 sym × 8 TF, disk_policy="bootstrap")
```

### Ключові факти (file:line evidence)

| Факт | Файл | Рядок | Деталі |
|------|------|-------|--------|
| connector bootstrap без try/except | engine_b.py | L302-320 | Crash однієї фази → crash процесу |
| m1_poller warmup фази 1,2 без try/except | m1_poller.py | L854-874 | Redis priming + M1Buffer — unprotected |
| m1_poller фаза 2b — per-TF try/except | m1_poller.py | L897-903 | Тільки `read_tail_candles()` обгорнуто |
| m1_poller `warmup_bars()` поза try/except | m1_poller.py | L907 | Якщо raises → crash |
| Supervisor: non-zero exit → RuntimeError | main.py | L348 | Kills ALL processes |
| prime_ready timeout hardcoded 20s | main.py | L175 | `def _wait_for_prime_ready(…, timeout_s=20)` |
| prime_ready result ignored | main.py | L321 | `_wait_for_prime_ready(config_path)` — return discarded |
| redis_priming_budget_s = 5 (config.json) | config.json | L157 | 104 op × ~100мс = ~10с > 5с → partial |
| UI warmup bars per TF hardcoded | server.py | L160-174 | `_WARMUP_BARS_BY_TF`, `_COLD_START_BARS_BY_TF` |
| DeriveEngine warmup_bars() source TF filter | derive_engine.py | L174 | `if bar.tf_s not in DERIVE_CHAIN: continue` |
| DeriveEngine DERIVE_CHAIN keys | core/derive.py | L30-38 | `{60, 300, 900, 1800, 3600}` — source TFs |

---

## 3. Що вже виправлено (before ADR-0003)

| ID | Що | Файл | Статус |
|----|----|------|--------|
| 20260219-020 | Kill stale m1_poller + dedup 2560 JSONL рядків | m1_poller.py, JSONL | ✅ DONE |
| 20260219-021 | DeriveEngine warmup fix: _WARMUP_TFS з M1/M5/M15/M30/H1 | m1_poller.py L884-893 | ✅ DONE |
| 20260219-022 | m1_poller pidfile guard (dup prevention) | m1_poller.py L1145-1192 | ✅ DONE |
| 20260219-027 | Redis priming розширено M1/M3 → M1-H4 (7 TFs) | m1_poller.py L1128-1138 | ✅ DONE |

**Наслідки**: DeriveEngine cascade працює з першої хвилини після рестарту. Redis priming наповнює дані для всіх TF. Ці слайси **виключені** з плану нижче.

---

## 4. Failure Model (актуальний)

| # | Сценарій | Де | Поточна поведінка | Severity |
|---|----------|----|--------------------|----------|
| F1 | FXCM login fail | connector + m1_poller | connector: retry loop (10s..3600s); m1_poller: skip tail_catchup → reconnect loop 120s | HIGH |
| F2 | Redis down при старті | всі writer-и | `has_redis_writer()=False` → skip priming; UI: порожні масиви | HIGH |
| F3 | ~~DeriveEngine warmup порожній~~ | ~~m1_poller~~ | **FIXED (20260219-021)** | ~~FIXED~~ |
| F4 | prime_ready gate: timeout 20s → result IGNORED | supervisor→UI | UI стартує з degraded; RAM warmup з disk покриває | MEDIUM |
| F5 | m1_poller priming invisible для gate | m1_poller | prime:ready сетить connector; m1_poller priming не впливає на gate | MEDIUM |
| F6 | Partial Redis priming (budget 5s < потрібно ~10s) | connector | 104 операцій, budget occupied → partial=True → degraded | MEDIUM |
| F7 | Corrupt JSONL на диску | engine_b + m1_poller | bootstrap_from_disk() / prime_from_disk() — **без try/except** → **CRASH** | **HIGH** |
| F8 | Broker cold_start fetch fail | connector | `_cold_start_base_from_broker()` — **без try/except** → **CRASH** | **HIGH** |
| F9 | Child process crash → supervisor kills ALL | main.py | `RuntimeError("process_exited:…")` → finally terminates all | **HIGH** |
| F10 | Rapid restart boot_id collision | engine_b | `time.strftime(…)` → same second = same id | LOW |

---

## 5. Gap Analysis — залишкові проблеми

### P0 — Bootstrap error isolation (F7, F8): **HIGH**

**Проблема**: `engine_b.bootstrap_and_warmup()` (L302-320) і `m1_poller._bootstrap_warmup()` (L848+) — жоден крок не обгорнутий у try/except. Один corrupt JSONL або network glitch → crash процесу → supervisor crashes ALL 5 процесів.

**Evidence**:
- `engine_b.py:L302`: `bootstrap_and_warmup()` викликає 3 методи послідовно без protection
- `engine_b.py:L453`: `_cold_start_base_from_broker()` — broker fetch без try/except
- `m1_poller.py:L854-874`: Фази 1 (Redis priming) і 2 (M1Buffer warmup) — unprotected
- `m1_poller.py:L907`: `warmup_bars()` виклик поза try/except

**Наслідок**: Один corrupt файл або мережевий збій = вся платформа down.

### P1 — Supervisor fail-all strategy (F9): **HIGH**

**Проблема**: `main.py:L348` — будь-який non-zero exit code → `RuntimeError` → finally block terminates ALL children.

**Evidence**:
- `main.py:L348`: `raise RuntimeError(f"process_exited:{item.label}")`
- `main.py:L362` (finally): calls `_terminate(item)` for all remaining processes

**Наслідок**: tick_publisher WS disconnect (найслабша ланка) → **все down**. Найслабший процес визначає uptime всієї системи.

### P2 — prime_ready gate cosmetic (F4, F5): **MEDIUM**

**Проблема**: 
1. `main.py:L321`: `_wait_for_prime_ready(config_path)` — **return value discarded**
2. Timeout = 20s (hardcoded, `main.py:L175`)
3. m1_poller НЕ публікує свій prime_ready → gate бачить тільки connector

**Наслідок**: UI може стартувати з порожнім Redis для M1/M3 (хоча disk warmup покриває).

### P3 — Redis priming budget неадекватний (F6): **MEDIUM**

**Проблема**: `config.json:L157`: `redis_priming_budget_s=5`. Для 13 sym × 8 TF = 104 операцій. Кожна ~50-200мс → ~10.4с > 5с budget → partial priming гарантовано.

**Примітка**: Оскільки m1_poller тепер теж прайміть M1-H4 (без budget), connector partial priming є менш критичним. Але D1 може не прайминутися вчасно.

### P4 — Hardcoded bootstrap params (config SSOT): **LOW**

**Проблема**: Багато параметрів hardcoded замість config.json.

| Параметр | Де | Значення |
|----------|-----|----------|
| prime_ready_timeout | main.py:L175 | 20s |
| DeriveEngine warmup bars | m1_poller.py:L886-893 | M1:300, M5:20, M15:10, M30:10, H1:10 |
| M1Buffer warmup tail_n | m1_poller.py:L869 | 10 |
| _PRIME_TFS | m1_poller.py:L1133 | (60,180,300,900,1800,3600,14400) |
| UI warmup bars | server.py:L160-174 | 300-500 per TF |
| UI cold_start bars | server.py:L170-177 | 168-10080 per TF |

---

## 6. Рішення та P-slices

### Phase A (P0 — безпека): S1

**S1 — Error isolation в bootstrap**

| | |
|---|---|
| **Ціль** | Bootstrap crash → процес стартує в degraded mode (polling loop працює), not crash |
| **Файли** | `engine_b.py`, `m1_poller.py` |
| **LOC est** | ~80 |
| **Priority** | P0 — критичний |
| **Status** | ✅ DONE (20260219-029) |

**Зміни**:

1. **engine_b.py `bootstrap_and_warmup()`** (L302-320):
   - Обгорнути кожен крок окремим try/except
   - `_bootstrap_from_disk()` fail → WARNING + continue (polling loop without watermark)
   - `_prime_redis_from_disk()` fail → WARNING + continue (no redis, but disk works)
   - `_cold_start_base_from_broker()` fail → WARNING + continue (no D1 initial bars, fetched on next poll tick)
   - Після bootstrap: log summary з переліком degraded кроків

2. **m1_poller.py `_bootstrap_warmup()`** (L848+):
   - Фаза 1 (Redis priming): обгорнути весь блок у try/except → WARNING + continue
   - Фаза 2 (M1Buffer warmup): обгорнути у try/except → WARNING + continue
   - Фаза 2b (DeriveEngine warmup): `warmup_bars()` (L907) теж обгорнути → WARNING
   - Фаза 3 (tail_catchup): вже частково protected, додати top-level try/except

**Rail**: bootstrap crash → процес все одно переходить до main loop у degraded. `status` endpoint показує `bootstrap_degraded=true` + `bootstrap_errors=["phase_name: error_msg"]`.

**Exit gate**: Запуск з corrupt JSONL → процес НЕ crash, logs WARNING, main loop працює.

---

### Phase B (P1 — операційна стабільність): S2 + S3

**S2 — Supervisor process isolation**

| | |
|---|---|
| **Ціль** | Non-critical child crash → restart з backoff, NOT kill-all |
| **Файли** | `app/main.py` |
| **LOC est** | ~100 |
| **Priority** | P1 |
| **Status** | ✅ DONE (20260219-033) |

**Зміни**:

1. Класифікація процесів:
   - **Critical**: connector, m1_poller — без них нема свіжих даних
   - **Non-critical**: tick_publisher, tick_preview — live preview, не SSOT (restart-tolerant)
   - **Essential**: ui — HTTP API (restart-tolerant)

2. Логіка:
   - Non-critical crash → restart з exponential backoff (base=5s, max=120s, max_attempts=10)
   - Critical crash → WARNING, restart з backoff (base=10s, max=300s, max_attempts=5)
   - Critical exhausted attempts → THEN supervisor fails (loud)
   - Restart counter reset після 10 хвилин стабільної роботи

3. Supervisor loop:
   ```
   while processes:
       for item in processes:
           code = poll()
           if code == 0:       → remove (clean exit)
           elif code != None:  → restart_or_fail(item)
       sleep 1
   ```

**Rail**: tick_publisher crash → restart через 5s, log WARNING. connector crash 5 разів за 10 хв → supervisor fails з loud error.

**Exit gate**: Kill tick_publisher → supervisor restarts it. Kill connector 6 times → supervisor exits gracefully.

---

**S3 — Unified prime_ready + budget fix**

| | |
|---|---|
| **Ціль** | UI чекає реальну готовність М1→H4+D1, priming не обрізається по budget |
| **Файли** | `m1_poller.py`, `app/main.py`, `config.json`, `redis_snapshot.py`, `uds.py` |
| **LOC est** | ~80 |
| **Priority** | P1 |
| **Status** | ✅ DONE (20260219-034) |

**Рішення — спрощений варіант** (без нового Redis key):

1. **m1_poller.py**: Після завершення _bootstrap_warmup() → публікувати `prime:ready:m1` Redis key (аналогічно connector's `prime:ready`)
2. **main.py `_wait_for_prime_ready()`**: 
   - Чекати AND(connector `prime:ready`, m1_poller `prime:ready:m1`) або timeout
   - **Використовувати return value**: якщо False → UI стартує з `warnings=["prime_timeout"]`
   - Timeout: з `config.json` (нове поле `prime_ready_timeout_s`, default=30)
3. **config.json**: 
   - `redis_priming_budget_s: 5 → 15` (або зовсім прибрати budget для m1_poller)
   - Додати `prime_ready_timeout_s: 30`

**Rail**: prime_ready gate → реальний gate (return value controls UI degraded flag).

**Exit gate**: Зупинити m1_poller → UI стартує після timeout з warning. Запустити обидва → UI стартує коли ready.

---

### Phase C (P2 — config SSOT): S4

**S4 — Bootstrap params → config.json**

| | |
|---|---|
| **Ціль** | Hardcoded bootstrap params → SSOT config.json |
| **Файли** | `config.json`, `m1_poller.py`, `server.py`, `main.py` |
| **LOC est** | ~50 |
| **Priority** | P2 — низький, policy compliance |
| **Status** | ✅ DONE (20260219-035) |

**Зміни**: Перенести в config.json:
- `prime_ready_timeout_s` (main.py) — done in S3
- `derive_warmup_bars_by_tf` (m1_poller.py)
- UI warmup params: `ui_warmup_bars_by_tf`, `ui_cold_start_bars_by_tf`

**Рішення**: Додати секцію `bootstrap` в config.json, але зберегти hardcoded defaults як fallback:
```json
"bootstrap": {
    "prime_ready_timeout_s": 30,
    "derive_warmup_bars_by_tf": {"60": 300, "300": 20, "900": 10, "1800": 10, "3600": 10},
    "ui_warmup_bars_by_tf": {"60": 500, "180": 500, "300": 500, "900": 500, "1800": 500, "3600": 500, "14400": 300, "86400": 200}
}
```

---

## 7. Інваріанти (не змінюються)

Жоден slice НЕ змінює інваріанти I0–I6:

- I0 (Dependency Rule): всі зміни в runtime/ та app/ — відповідає
- I1 (UDS вузька талія): bootstrap пише через UDS — відповідає
- I2 (геометрія часу): не змінюється
- I3 (Final > Preview): не змінюється
- I4 (один update-потік): не змінюється
- I5 (degraded-but-loud): **підсилюється** (bootstrap errors → явний degraded flag)
- I6 (stop-rule): N/A

---

## 8. Exit Gates

Мінімальний набір верифікацій після кожного slice:

| Gate | Slice | Опис |
|------|-------|------|
| G1 | S1 | Corrupt JSONL → процес НЕ crash, WARNING в logs, main loop працює |
| G2 | S1 | FXCM down → connector bootstrap degraded, main loop retry |
| G3 | S2 | Kill tick_publisher → supervisor restarts (≤10s), інші живі |
| G4 | S2 | Kill connector 6× за 10хв → supervisor exits gracefully |
| G5 | S3 | Обидва prime:ready → UI стартує без warnings |
| G6 | S3 | m1_poller down → UI стартує після timeout з warning |
| G7 | General | API: кожен TF повертає ≥ min_coldload_bars |
| G8 | General | Zero split-brain: монотонність open_ms, без дублів |

---

## 9. Таблиці лімітів

### 9.1 Cold-start bootstrap ліміти (що потрібно для стабільного старту)

| TF | Cold-start window | Cold-start bars | Джерело |
|----|-------------------:|----------------:|---------|
| M1 | 3 дні | 4,320 | m1_poller tail_catchup (FXCM) |
| M3 | 3 дні | 1,440 | derived від M1 |
| M5 | 7 днів | 2,016 | derived від M1 |
| M15 | 7 днів | 672 | derived від M5 |
| M30 | 7 днів | 336 | derived від M15 |
| H1 | 30 днів | 720 | derived від M30 |
| H4 | 180 днів | 1,080 | derived від H1 |
| D1 | 365 днів | 365 | connector (FXCM fetch) |

### 9.2 Redis tail_n (config.json SSOT — вже задано)

| TF | tail_n | Покриття |
|----|-------:|----------|
| M1 | 2,880 | ~2 дні |
| M3 | 1,440 | ~3 дні |
| M5 | 8,000 | ~28 днів |
| M15 | 4,000 | ~42 дні |
| M30 | 2,500 | ~52 дні |
| H1 | 2,000 | ~83 дні |
| H4 | 256 | ~43 дні |
| D1 | 128 | ~128 днів |

### 9.3 UI window ліміти (retention / max window)

| TF | bars/day | default_window_days | default bars | max_window_days | max bars |
|----|--------:|--------------------:|-------------:|----------------:|---------:|
| M1 | 1,440 | 7 | 10,080 | 21 | 30,240 |
| M5 | 288 | 60 | 17,280 | 180 | 51,840 |
| M15 | 96 | 180 | 17,280 | 365 | 35,040 |
| M30 | 48 | 365 | 17,520 | 730 | 35,040 |
| H1 | 24 | 730 | 17,520 | 1,460 | 35,040 |
| H4 | 6 | 1,460 | 8,760 | 3,650 | 21,900 |
| D1 | 1 | 365 | 365 | 1,095 | 1,095 |

> **Ключова різниця**: Cold-start ≠ retention. Cold-start — мінімум для стабільного старту. Retention — максимальна глибина для scrollback. config.json `min_coldload_bars_by_tf` має відповідати таблиці 9.1.

### 9.4 min_coldload_bars_by_tf (config.json — потребує звірки)

**Поточні значення** (`config.json`):
```json
"min_coldload_bars_by_tf_s": {
    "60": 1440, "180": 480, "300": 2016, "900": 672,
    "1800": 150, "3600": 720, "14400": 1080, "86400": 365
}
```

**Оцінка**: M1 (1440) та M3 (480) надто малі для 3-денного cold-start window. M30 (150) теж мало. Це використовується як "мінімум для UI cold-load" — якщо Redis має менше, то warning. Значення адекватні як мінімум, але потребують перегляду при зміні вимог.

---

## 10. Rollback

Кожен slice містить свій rollback:

| Slice | Rollback |
|-------|----------|
| S1 | Видалити try/except блоки → повернути до прямих викликів |
| S2 | Повернути `raise RuntimeError(…)` при non-zero exit |
| S3 | Видалити `prime:ready:m1`, повернути `_wait_for_prime_ready` без AND |
| S4 | Видалити `bootstrap` секцію з config.json, повернути hardcoded |

---

## Рекомендований порядок виконання

```
Phase A (P0 — безпека):           S1 (error isolation)
  → Bootstrap не crashує систему; degraded-but-loud

Phase B (P1 — стабільність):      S2 (process isolation) → S3 (unified gate)
  → Окремий restart; адекватний gate; prime_ready реальний

Phase C (P2 — config SSOT):       S4 (params → config.json)
  → Policy SSOT compliance
```

**Початок**: S1 (error isolation) — найкритичніший. Зараз один corrupt файл = вся платформа down.

---

*Документ є ADR-0003 проєкту v3. Будь-які зміни інваріантів або контрактів потребують окремого ADR.*
