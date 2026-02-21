# P7 — Gap Analysis: Зведений аналіз дефектів і ризиків

> **Документ**: синтез усіх знахідок з P1–P6 + cross-cutting codebase audit.
> **Джерело**: code-first reverse-engineering, кожен факт з `file:line` evidence.
> **Дата**: 2025-02-21

---

## Зміст

1. [Зведена статистика](#1-зведена-статистика)
2. [HIGH risk — детально](#2-high-risk--детально)
3. [MEDIUM risk — детально](#3-medium-risk--детально)
4. [LOW risk — стислий реєстр](#4-low-risk--стислий-реєстр)
5. [INFORMATIONAL — реєстр](#5-informational--реєстр)
6. [Cross-cutting patterns](#6-cross-cutting-patterns)
7. [Invariant coverage matrix](#7-invariant-coverage-matrix)
8. [Rule compliance matrix](#8-rule-compliance-matrix)
9. [except Exception audit](#9-except-exception-audit)
10. [Remediation roadmap](#10-remediation-roadmap)
11. [Series statistics](#11-series-statistics)

---

## 1. Зведена статистика

| Рівень | Кількість |
|--------|-----------|
| **HIGH** | 2 |
| **MEDIUM** | 10 |
| **LOW** | 24 |
| **INFORMATIONAL** | 11 |
| **Разом** | **47** |

### Розподіл по документах

| Документ | Знахідок | HIGH | MED | LOW | INFO |
|----------|----------|------|-----|-----|------|
| P1 Process Inventory | 2 | 0 | 0 | 0 | 2 |
| P2 Data Flow | 12 | 0 | 1 | 5 | 6 |
| P3 UDS / Store | 10 | 0 | 3 | 5 | 2 |
| P4 API Surface | 8 | 0 | 1 | 6 | 1 |
| P5 Contracts & Guards | 7 | 1 | 2 | 4 | 0 |
| P6 Config & Policy | 8 | 1 | 3 | 4 | 0 |
| **Разом** | **47** | **2** | **10** | **24** | **11** |

---

## 2. HIGH risk — детально

### H-1. P5-F2 — Tick Aggregator drops SILENT

| Поле | Значення |
|------|----------|
| **Джерело** | P5 §14 |
| **Суть** | Guards G42–G45 у `tick_agg.py` при відкиданні тіків (out-of-order, stale, duplicate) **лише інкрементують counter**. Немає `logging.warning`, немає `degraded[]` |
| **Інваріанти** | **I5** (degraded-but-loud), **I6** (watermark drop-stale видимий) |
| **Наслідок** | Масове відкидання тіків (clock drift, мережева затримка) — **невидиме**. Графік показує «нормальний» preview, хоча 50%+ тіків могли бути відкинуті |
| **Evidence** | `runtime/ingest/tick_agg.py` — counters `_dropped_stale`, `_dropped_dup`, `_dropped_ooo`. Ніде не логуються, не публікуються в status |
| **Ремедіація** | Додати periodic log (раз на 60с) якщо drop_rate > threshold + включити counters у `/api/status` → `degraded[]` |

### H-2. P6-F4 — PREVIEW_CURR_TTL_S 3-way mismatch

| Поле | Значення |
|------|----------|
| **Джерело** | P6 §19 |
| **Суть** | Три різні значення TTL для preview барів у трьох місцях: config.json **1800s**, `uds.py:44` hardcode **120s**, `tick_preview_worker.py:114` default **60s**. `build_uds_from_config()` у `composition.py` **не передає** config value до UDS — завжди hardcode 120s |
| **Інваріанти** | **Rule №4** (SSOT config), split-brain конфігурації |
| **Наслідок** | Preview бари зникають через 120с (hardcode UDS) замість 1800с (config intent). При зупинці tick stream — preview пропадає за 2 хв, хоча config передбачає 30 хв |
| **Evidence** | `runtime/store/uds.py:44` (`PREVIEW_CURR_TTL_S = 120`), `config.json:71` (`"preview_curr_ttl_s": 1800`), `runtime/ingest/tick_preview_worker.py:114` (`cfg.get("preview_curr_ttl_s", 60)`) |
| **Ремедіація** | 1) Визначити одне SSOT значення в config.json. 2) `build_uds_from_config()` має передавати його при створенні UDS. 3) `tick_preview_worker` має读 його з config, а не fallback 60 |

---

## 3. MEDIUM risk — детально

### M-1. P5-F1 — FINAL_SOURCES triple duplication

| Поле | Значення |
|------|----------|
| **Джерело** | P5 §14 |
| **Суть** | `FINAL_SOURCES` визначено у 3 місцях: `uds.py:42`, `disk_layer.py:9`, `ssot_jsonl.py:12`. Додавання нового final source вимагає зміни 3 файлів |
| **Інваріанти** | **I3** (Final > Preview) |
| **Evidence** | `runtime/store/uds.py:42`, `runtime/store/layers/disk_layer.py:9`, `runtime/store/ssot_jsonl.py:12` |
| **Ремедіація** | Визначити FINAL_SOURCES в одному місці (напр. `core/contracts/`) і імпортувати |

### M-2. P5-F7 — Default complete=True у нормалізації

| Поле | Значення |
|------|----------|
| **Джерело** | P5 §14, дублюється P4-F3 |
| **Суть** | `_normalize_bar_window_v1` у `server.py:744` — якщо raw bar без поля `complete` → default `True`. Preview бар без explicit `complete=False` автоматично стає «final» |
| **Інваріанти** | **I3** (Final > Preview) |
| **Evidence** | `ui_chart_v3/server.py:744` |
| **Ремедіація** | Змінити default на `False` або fail-loud якщо поле відсутнє |

### M-3. P6-F3 — TF_ALLOWLIST hardcoded duplicate

| Поле | Значення |
|------|----------|
| **Джерело** | P6 §19 |
| **Суть** | `core/buckets.py:10` має hardcoded `TF_ALLOWLIST = {60,180,300,...}` — дублікат `config.json:tf_allowlist_s`. Додати/видалити TF → правити два місця |
| **Інваріанти** | **Rule №4** (SSOT config) |
| **Evidence** | `core/buckets.py:10`, `config.json:55-63` |
| **Ремедіація** | `core/buckets.py` має бути SSOT; config.json має посилатись або config_loader має валідувати проти buckets.py |

### M-4. P6-F5 — Validation covers only connector

| Поле | Значення |
|------|----------|
| **Джерело** | P6 §19 |
| **Суть** | `_validate_config()` виконується лише в `composition.py:219` (Connector). M1 Poller, Tick Publisher, Tick Preview Worker, UI Server — **не валідують** config на старті |
| **Інваріанти** | **Rule №5.1** (fail-fast guard) |
| **Evidence** | `app/composition.py:219` |
| **Ремедіація** | Виконувати `_validate_config()` у кожному entrypoint при старті |

### M-5. P3-F3 — RAM LRU max_keys=8 для writer

| Поле | Значення |
|------|----------|
| **Джерело** | P3 §23 |
| **Суть** | Writer RAM cache = 8 keys, при 13 символів × 8 TF = 104 комбінацій. Часте eviction. Writer не покладається на RAM для швидкості, але cache стає марним |
| **Інваріанти** | — |
| **Evidence** | `runtime/store/uds.py:2060` |
| **Ремедіація** | Збільшити `max_keys` для writer або видалити writer RAM cache (якщо не потрібна) |

### M-6. P3-F4 — Disk bootstrap window = 60s

| Поле | Значення |
|------|----------|
| **Джерело** | P3 §23 |
| **Суть** | `BOOTSTRAP_WINDOW_S = 60` (`uds.py:237`). Після 60с Reader не може fallback на disk у `"bootstrap"` policy. Повільний Redis prime → UI бачить порожні дані |
| **Інваріанти** | **I1** (UDS SSOT) |
| **Evidence** | `runtime/store/uds.py:237` |
| **Ремедіація** | Збільшити window або зробити його configurable через config.json |

### M-7. P3-F9 — No fsync in JsonlAppender

| Поле | Значення |
|------|----------|
| **Джерело** | P3 §23 |
| **Суть** | `flush()` без `os.fsync()`. При OS crash — втрата останніх барів з OS buffer |
| **Інваріанти** | — |
| **Evidence** | `runtime/store/ssot_jsonl.py:140` |
| **Ремедіація** | Додати `os.fsync(fd)` після flush (trade-off: performance vs durability). Або зробити configurable |

### M-8. P4-F3 — Fallback complete=True (дублікат M-2)

Дублює M-2 (P5-F7). Зафіксовано в P4 §23 як окремий finding з identical evidence `server.py:741`.

### M-9. P2-F12 — H4/D1 tail < coldload

| Поле | Значення |
|------|----------|
| **Джерело** | P2 §16 |
| **Суть** | H4 tail=256, coldload=1080; D1 tail=128, coldload=365. Redis tail не покриває cold load → disk fallback завжди для великих `limit`. Порушує "disk не hot-path після bootstrap" |
| **Інваріанти** | **I1** (UDS, disk=recovery) |
| **Evidence** | `config.json:186-208` (tf_profiles) |
| **Ремедіація** | Збільшити tail_n ≥ coldload для H4/D1, або зменшити coldload, або прийняти disk fallback як degraded-but-loud |

### M-10. P6-H10 — PREVIEW_CURR_TTL_S hardcode

Дублює H-2 (P6-F4) як одиниця у hardcode audit. `uds.py:44` hardcode `120s` не передається з config.

---

## 4. LOW risk — стислий реєстр

| # | ID | Документ | Суть | Evidence |
|---|-----|----------|------|---------|
| 1 | P3-F1 | P3 | UpdatesBus raw symbol (`XAU/USD`) vs усі інші Redis ключі (`XAU_USD`). Працює, неконсистентно | `uds.py:1905` |
| 2 | P3-F2 | P3 | SSOT `logging.error` на кожний non-final drop — log spam | `ssot_jsonl.py:113-123` |
| 3 | P3-F5 | P3 | Preview tail без TTL → stale preview при зупинці tick stream | `redis_layer.py` |
| 4 | P3-F6 | P3 | Два шляхи dedup: DiskLayer vs UDS `_ensure_sorted_dedup` — різна реалізація | `disk_layer.py:202`, `uds.py:1769` |
| 5 | P3-F7 | P3 | Legacy `"l"` fallback для `"low"` у `_disk_bar_to_candle` | `uds.py:48` |
| 6 | P2-F1 | P2 | Два паралельні event streams: UpdatesBus + Preview Ring. Bridge може fail → stale preview | `uds.py:670` |
| 7 | P2-F2 | P2 | Config `"price_tik"` замість `"price_tick"` — typo, функціонально OK | `config.json:210` |
| 8 | P2-F3 | P2 | Preview bars `v=0` (FXCM тіки без volume) | `tick_agg.py`, `tick_preview_worker.py` |
| 9 | P2-F5 | P2 | Redis snap OK + updates fail → UI бачить стару history без live events | `uds.py:661-663` |
| 10 | P2-F6 | P2 | Два M3 derive paths: DeriveEngine + inline `_derive_m3()` fallback у m1_poller | `m1_poller.py:384-391` |
| 11 | P4-F1 | P4 | Overlay volume=0 завжди — `_aggregate_bucket()` hardcode | `server.py:1645` |
| 12 | P4-F2 | P4 | Extra fields у `/api/updates` поза updates_v1 контрактом | `server.py:1115-1118` |
| 13 | P4-F4 | P4 | `align` param parsed but ignored (ADR-0002 Phase 3) | `server.py:1060, 1261` |
| 14 | P4-F5 | P4 | Overlay errors silently ignored on client — `pollOverlay()` catch без UI signal | `app.js:2032-2034` |
| 15 | P4-F6 | P4 | Config cache mtime check per request thread — `os.path.getmtime()` | `server.py:469-499` |
| 16 | P4-F8 | P4 | Lazy globals для `_updates_log_state` — GIL masks race | `server.py:1134-1139` |
| 17 | P5-F3 | P5 | MAX_EVENTS trimming — LOUD (correctly warns). OK | `uds.py:579-581` |
| 18 | P5-F4 | P5 | `_ensure_sorted_dedup` auto-fix SILENT — sort+dedup без degraded flag | `uds.py:1769` |
| 19 | P5-F5 | P5 | Disk filter non-final drops — SILENT | `disk_layer.py:148` |
| 20 | P5-F6 | P5 | Selftests only at boot — не re-run periodic | `uds.py:2097` |
| 21 | P6-F1 | P6 | Legacy config loaders: `composition.py:21`, `collectors.py:136` — не через `core.config_loader` | `composition.py:21`, `collectors.py:136` |
| 22 | P6-F2 | P6 | UDS config read-once — зміни після bootstrap невидимі | `uds.py:2036` |
| 23 | P6-F6 | P6 | 7 client policy constants hardcoded в `app.js` — не server-driven | `app.js:86-145` |
| 24 | P6-F7 | P6 | `broker_base_tfs_s` fallback mismatch: config=`[86400]`, fallback=`[14400,86400]` (includes H4) | `composition.py:287` |

---

## 5. INFORMATIONAL — реєстр

| # | ID | Документ | Суть |
|---|-----|----------|------|
| 1 | P3-F8 | P3 | Preview bridge тільки M1/M3 — M5+ лише UpdatesBus. **Правильна поведінка** |
| 2 | P3-F10 | P3 | ~200 LOC selftests в production файлах (uds.py, redis_layer.py) |
| 3 | P2-F4 | P2 | DeriveEngine shared UDS — race неможливий через синхронний виклик |
| 4 | P2-F7 | P2 | `/api/updates` не читає disk — відповідає I1 "disk=recovery" |
| 5 | P2-F8 | P2 | RAM cache populated from updates read — оптимізація |
| 6 | P2-F9 | P2 | `boot_id` mismatch → full UI reload. **Правильна поведінка** |
| 7 | P2-F10 | P2 | Stitching applied once per path — no double-apply |
| 8 | P2-F11 | P2 | Three independent Redis clients per process (max 3 у M1 Poller) |
| 9 | P1-note1 | P1 | Connector 13 UDS vs M1 Poller 1 shared — різний паттерн, no conflict |
| 10 | P1-note2 | P1 | pidfile guard тільки у M1 Poller; Connector без pidfile |
| 11 | P4-F7 | P4 | Public API guard lock — thread-safe, OK для 10-50 трейдерів |

---

## 6. Cross-cutting patterns

Аналіз 47 знахідок виявляє 6 повторюваних системних патернів:

### Pattern A — SSOT Duplication (8 знахідок)

**Суть**: константа/политика визначена у кількох місцях, зміна потребує синхронної правки 2-3 файлів.

| ID | Що дублюється | Місця | Ризик |
|----|---------------|-------|-------|
| P5-F1 | `FINAL_SOURCES` | `uds.py:42`, `disk_layer.py:9`, `ssot_jsonl.py:12` | MED |
| P6-F3 | `TF_ALLOWLIST` | `buckets.py:10`, `config.json:55-63` | MED |
| P6-F4 | `PREVIEW_CURR_TTL_S` | `config.json:71`, `uds.py:44`, `tick_preview_worker.py:114` | **HIGH** |
| P6-F7 | `broker_base_tfs_s` fallback | `composition.py:287` vs `config.json` | LOW |
| P6-F1 | config loader | `composition.py:21`, `collectors.py:136` — bypass `core.config_loader` | LOW |
| P3-F6 | dedup logic | `disk_layer.py:202` vs `uds.py:1769` | LOW |
| P2-F6 | M3 derive path | DeriveEngine vs `_derive_m3()` inline | LOW |
| P6-F6 | client policy | 7 hardcoded JS constants vs config.json | LOW |

**Кореневий дефект**: відсутній enforcement mechanism для SSOT — кожен файл/процес може визначати свою копію без CI guard.

### Pattern B — Silent Drops / Silent Fallbacks (8 знахідок)

**Суть**: дані відкидаються, фолбечаться або модифікуються без видимого сигналу.

| ID | Що тиха | Evidence | Ризик |
|----|---------|----------|-------|
| P5-F2 | Tick drops (stale/dup/ooo) | `tick_agg.py` counters-only | **HIGH** |
| P5-F4 | `_ensure_sorted_dedup` auto-fix | `uds.py:1769` | LOW |
| P5-F5 | Disk filter non-final drops | `disk_layer.py:148` | LOW |
| P3-F2 | SSOT error log spam (inverse) | `ssot_jsonl.py:113-123` → spam | LOW |
| P4-F5 | Overlay errors on client | `app.js:2032-2034` | LOW |
| P2-F5 | Snap OK + updates fail | `uds.py:661-663` | LOW |
| Cross | `except Exception: pass` (40 місць) | See §9 | MED |
| P4-F8 | Lazy globals race masked by GIL | `server.py:1134-1139` | LOW |

**Кореневий дефект**: Rule №9 (degraded-but-loud) не enforce-ується — немає lint/gate що примушує кожен drop/catch мати logging.

### Pattern C — Final vs Preview Integrity Gaps (4 знахідки)

**Суть**: межа між final і preview розмита через defaults/missing checks.

| ID | Суть | Evidence | Ризик |
|----|------|----------|-------|
| P5-F7 / P4-F3 | Default `complete=True` | `server.py:744` | MED |
| P5-F1 | Triple FINAL_SOURCES | 3 файли | MED |
| P3-F5 | Preview tail без TTL | `redis_layer.py` | LOW |
| P2-F1 | Dual event stream (UpdatesBus + Preview Ring) | `uds.py:670` | LOW |

**Кореневий дефект**: invariant I3 (Final > Preview) залежить від runtime поведінки, але не має compile-time/boot-time gate.

### Pattern D — Config Distribution Gap (6 знахідок)

**Суть**: config.json значення не доходять до всіх споживачів.

| ID | Суть | Evidence | Ризик |
|----|------|----------|-------|
| P6-F4 | PREVIEW_CURR_TTL_S не wired | `composition.py` → UDS | **HIGH** |
| P6-F5 | Validation only in connector | `composition.py:219` | MED |
| P6-F2 | UDS read-once | `uds.py:2036` | LOW |
| P6-F6 | Client not server-driven | `app.js:86-145` | LOW |
| P6-F1 | Legacy loaders bypass | 2 файли | LOW |
| P6-F7 | Fallback mismatch | `composition.py:287` | LOW |

**Кореневий дефект**: `build_uds_from_config()` і процес-entrypoints не систематично маплять config fields → runtime params.

### Pattern E — Disk/Bootstrap Boundary (3 знахідки)

**Суть**: граница disk=recovery може бути порушена при slow prime або великих coldload.

| ID | Суть | Evidence | Ризик |
|----|------|----------|-------|
| P3-F4 | BOOTSTRAP_WINDOW = 60s tight | `uds.py:237` | MED |
| P2-F12 | H4/D1 tail < coldload | `config.json` tf_profiles | MED |
| P3-F9 | No fsync | `ssot_jsonl.py:140` | MED |

**Кореневий дефект**: disk boundary не configurable, hardcoded assumptions.

### Pattern F — Code Hygiene / Anti-patterns (3 знахідки)

| ID | Суть | Evidence | Ризик |
|----|------|----------|-------|
| P3-F10 | ~200 LOC selftests в production | `uds.py`, `redis_layer.py` | INFO |
| P4-F8 | Lazy globals | `server.py:1134-1139` | LOW |
| Cross | 0 TODO/FIXME у production | (позитив) | — |

---

## 7. Invariant coverage matrix

Як кожен інваріант покривається, де є gaps.

| Інваріант | Опис | Знахідки що порушують | Gap |
|-----------|------|----------------------|-----|
| **I0** | Dependency Rule (core/ ¬→ runtime) | — | **Немає enforcement gate** у CI/tools. Тільки конвенція |
| **I1** | UDS як SSOT / disk=recovery | P3-F4, P2-F12 | Disk bootstrap hardcoded; H4/D1 tail < coldload |
| **I2** | Єдина геометрія часу | P3-F6 | Два шляхи dedup з різною "better bar" логікою |
| **I3** | Final > Preview (NoMix) | P5-F1, P5-F7, P4-F3, P3-F5 | Default complete=True; FINAL_SOURCES×3; preview no TTL |
| **I4** | Один update-потік для UI | P2-F1 | Dual stream (UpdatesBus + Preview Ring) з bridge |
| **I5** | Degraded-but-loud | **P5-F2**, P3-F2, P4-F2, P4-F5, P5-F4, P5-F5 + 40 `except: pass` | **Найбільш порушений** — 6+ знахідок + 40 bare except |
| **I6** | Watermark / drop-stale | P5-F2 | Tick drops invisible |

### Найбільш порушений інваріант: **I5 (Degraded-but-loud)**

- 6 явних знахідок (P5-F2, P3-F2, P4-F2, P4-F5, P5-F4, P5-F5)
- 40 `except Exception: pass` у production коді (§9)
- Жодного lint/gate який примушує logging у catch blocks

---

## 8. Rule compliance matrix

| Rule | Стан | Основні порушення |
|------|------|-------------------|
| **Rule №4** (SSOT config) | **6 порушень** | P6-F3, P6-F4, P6-F6, P6-F7, P6-F1, P6-F5 |
| **Rule №5** (Contract-first) | Partial | P4-F2 (extra fields поза контрактом) |
| **Rule №6** (Інваріанти) | **I5 weak** | Див. матрицю вище |
| **Rule №9** (No silent fallback) | **Weak** | P5-F2 (HIGH), P3-F2, P5-F4, P5-F5 + 40 bare except |
| **Rule №9.1** (Rate-limit logs) | P3-F2 | SSOT error spam без throttle |
| **Rule №10** (Exit gates) | Present | 24 gates exist, але P5-F6: selftests boot-only |
| **Rule №11** (UI thin) | OK | 7 client hardcodes (P6-F6) — порушення SSOT, не тонкості |
| **Rule №17** (Code hygiene) | OK | 0 TODO/FIXME; P3-F10 selftests у prod файлах |
| **Rule §3.5** (Dependency rails) | **No gate** | I0 not enforced (P5 finding) |

---

## 9. except Exception audit

### Статистика по файлах

| Файл | Bare `except Exception:` (pass/continue) | З `as exc` (logged) | Разом |
|------|------------------------------------------|---------------------|-------|
| `runtime/store/uds.py` | 10 (L36, L91, L1191, L1196, L1412, L1414, L1441, L1443, L1608, L1613) | 10 | 20 |
| `app/main.py` | 6 (L98, L111, L116, L289, L292, L300) | 4 | 10 |
| `runtime/store/ssot_jsonl.py` | 1 (L146) | 0 | 1 |
| `runtime/store/redis_snapshot.py` | 1 (L54) | 0 | 1 |
| `runtime/ingest/polling/m1_poller.py` | 4 (L823, L852, L1311, L1352) | 0 | 4 |
| `runtime/ingest/tick_publisher_fxcm.py` | 2 (L84, L313) | 0 | 2 |
| `runtime/ingest/broker/fxcm/provider.py` | 1 (L308) | 0 | 1 |
| `app/composition.py` | 1 (L545) | 0 | 1 |
| **Production total** | **26** | **14** | **40** |

### Аналіз

- **Більшість** bare except — **cleanup/shutdown** paths (close file handles, FXCM logout, pidfile removal). Це типовий "best-effort cleanup" паттерн.
- **Проблемні**: `uds.py` L1191/L1196, L1608/L1613 — conversion helpers що swallow exceptions під час нормальної роботи (не тільки shutdown).
- **main.py** L98/L111/L116 — supervisor process cleanup — допустимий паттерн, але має бути logged.
- **0 bare `except:`** (без Exception) — позитив.
- **0 TODO/FIXME/HACK** у production — позитив.

### Рекомендація

Критичність LOW (individual), але **Pattern B accumulation** робить це MEDIUM системно. Рекомендується:

1. Lint rule: `except Exception: pass` → обов'язковий `logging.debug()` мінімум
2. Окремий PATCH для uds.py conversion helpers (L1608/L1613)

---

## 10. Remediation roadmap

### Priority 1 — HIGH (виправити зараз)

| Slice | Дія | Files | LOC | Deps |
|-------|-----|-------|-----|------|
| **S1** | P6-F4: Wire `preview_curr_ttl_s` від config.json через `build_uds_from_config()` до UDS. Видалити hardcode 120 з `uds.py:44` | `composition.py`, `uds.py`, `tick_preview_worker.py` | ~30 | — |
| **S2** | P5-F2: Tick aggregator — додати periodic log + status counters для drops | `tick_agg.py`, `/api/status` handler | ~40 | — |

### Priority 2 — MEDIUM structural (тиждень)

| Slice | Дія | Files | LOC | Deps |
|-------|-----|-------|-----|------|
| **S3** | P5-F1: FINAL_SOURCES → один файл у `core/contracts/` | `core/contracts/`, `uds.py`, `disk_layer.py`, `ssot_jsonl.py` | ~20 | — |
| **S4** | P5-F7: complete default → `False` або fail-loud | `server.py` | ~5 | Tests |
| **S5** | P6-F5: Валідація config у кожному entrypoint | `m1_poller.py`, `tick_publisher_fxcm.py`, `tick_preview_worker.py`, `server.py` | ~40 | S1 |
| **S6** | P3-F4: BOOTSTRAP_WINDOW_S → configurable | `uds.py`, `config.json` | ~15 | — |
| **S6.5** | P3-F3: RAM LRU max_keys=8 для writer → збільшити або видалити | `uds.py` | ~10 | — |
| **S7** | P2-F12: H4/D1 tail_n ≥ coldload | `config.json` | ~5 | — |
| **S8** | P6-F3: TF_ALLOWLIST → один SSOT | `buckets.py`, `config_loader.py` | ~15 | — |
| **S9** | P3-F9: fsync option | `ssot_jsonl.py` | ~10 | — |

### Priority 3 — LOW (backlog)

| Slice | Дія |
|-------|-----|
| S10 | P3-F1: Normalize UpdatesBus symbol to canonical `_` format |
| S11 | P3-F2: Rate-limit SSOT error logging |
| S12 | P3-F5: Preview tail TTL |
| S13 | P4-F2: Strip extra fields outside updates_v1 |
| S14 | P6-F6: Server-driven client policy via `/api/config` |
| S15 | I0 enforcement: deps_guard.py |
| S16 | Bare except audit: add logging to 26 silent catch blocks |
| S17 | P2-F6: Remove inline `_derive_m3()` fallback |
| S18 | P4-F5: Overlay error surfacing in UI |

### Залежності між slices

```
S1 (wire preview TTL) → S5 (validate all entrypoints)
S3 (FINAL_SOURCES SSOT) — standalone
S4 (complete default) → needs test update
S6, S7, S8, S9 — all standalone
```

---

## 11. Series statistics

### Загальна статистика 7 документів

| Метрика | Значення |
|---------|----------|
| Документів | 7 (P1–P7) |
| Загальний обсяг (рядків) | ~6500+ |
| Знахідок | 47 (2 HIGH, 10 MED, 24 LOW, 11 INFO) |
| Cross-cutting patterns | 6 (A–F) |
| Інваріантів перевірено | 7 (I0–I6) |
| Rules перевірено | 9 |
| Exit gates documented | 24 (EG1–EG24) |
| Guards documented | 109 (G1–G109) |
| Contracts documented | 4 JSON Schema + 16 dataclass |
| Config entries documented | 278 рядків, 17 секцій |
| Hardcodes знайдено | 14 server + 7 client |
| `except Exception` у production | 40 (26 bare/silent + 14 logged) |
| TODO/FIXME/HACK | 0 (позитив) |
| Changelogs written | 7 (010–016) |

### Покриття по шарах

| Шар | Документи | Головні gaps |
|-----|-----------|-------------|
| **core/** | P5 (contracts), P6 (config_loader) | I0 no enforcement gate; TF_ALLOWLIST dupe |
| **runtime/store/** | P3, P5 | FINAL_SOURCES×3; PREVIEW_TTL mismatch; bare excepts |
| **runtime/ingest/** | P1, P2, P5 | Tick drops silent; M3 dual derive path |
| **app/** | P1, P6 | Validation only in connector; fallback mismatch |
| **ui_chart_v3/** | P4, P6 | complete default=True; extra fields; client hardcodes |

### Топ-5 файлів за кількістю знахідок

| Файл | Знахідок | IDs |
|------|----------|-----|
| `runtime/store/uds.py` | 10 | P3-F1..F7, P6-F2, P6-F4(H10), P5-F4 |
| `ui_chart_v3/server.py` | 6 | P4-F1..F8, P5-F7 |
| `app/composition.py` | 4 | P6-F1, P6-F5, P6-F4, P6-F7 |
| `config.json` | 3 | P2-F2, P2-F12, P6-F3 |
| `runtime/store/ssot_jsonl.py` | 2 | P3-F2, P3-F9 |

---

*Кінець P7. Наступний крок: remediation slices S1–S2 (HIGH) за пріоритетом.*
