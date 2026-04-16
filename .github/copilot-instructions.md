# Інструкція для AI-агентів · Trading Platform v3 · rev 2.0

> **SSOT**: Цей файл — єдине джерело правил. Копії (`rules.md`, `project_instructions.md`,
> `.agent/rules/copilot-instructions.md`, `.antigravity/rules.md`) генеруються з нього.
> Якщо бачиш розбіжність — пріоритет цього файлу.

---

## ═══════════════════════════════════════════════════════
## РІВЕНЬ 0 — РОЛІ АГЕНТА (Role Routing)
## ═══════════════════════════════════════════════════════

> **Один запит = одна роль. Інваріанти I0–I6 діють у всіх ролях.**

### Система ролей

Агент обирає роль **автоматично** за контекстом запиту. Default = `R_PATCH_MASTER`.
Явна активація (`"Ти R_<ID>"`) має пріоритет.

> `AGENTS.md` §1.3 is an index-only mirror for discovery. Do not redefine triggers/routing rules there.

| ID | Роль | Файл | Тригери |
|---|---|---|---|
| `R_PATCH_MASTER` | **Patch Master** | `.github/role_spec_patch_master_v1.md` | **Default.** "фікс", "виправ", "патч", "зміни", "додай", "побудуй" |
| `R_BUG_HUNTER` | **Bug Hunter** | `.github/role_spec_bug_hunter_v2.md` | "аудит", "review", "знайди баги", "перевір", "що не так" |
| `R_SMC_CHIEF` | **SMC Chief Strategist** | `.github/role_spec_smc_chief_strategist_v1.md` | "SMC", "зони", "свінги", "OB/FVG", "overlay", "Clean Chart" |
| `R_DOC_KEEPER` | **Doc Keeper** | `.github/role_spec_doc_keeper_v1.md` | "документація", "оновити docs", "синхронізувати", "drift" |
| `R_TRADER` | **SMC Trader** | `.github/role_spec_trader_v1.md` | "оціни сетап", "чи це A+?", "торгувати чи ні?", "що бачить трейдер?", "grade challenge" |
| `R_CHART_UX` | **Chart Experience Product Designer** | `.github/role_spec_chart_ux_v1.md` | "як виглядає?", "дизайн", "ui/ux", "premium", "преміум", "Awwwards", "canvas", "рендер", "тема", "анімація", "DPR", "HUD", "інтерфейс", "product feel", "лагає", "build", "deploy", "запуск", "supervisor" |
| `R_ARCHITECT` | **Systems Architect** | `.github/role_spec_architect_v1.md` | "ADR", "архітектура", "дизайн системи", "масштаб", "альтернативи?", "trade-off", "які варіанти?", "спроектуй", "release" |
| `R_COMPLIANCE` | **Compliance & Safety Officer** | `.github/role_spec_compliance_v1.md` | "ліцензія", "license", "security", "OWASP", "CVE", "compliance", "disclaimer", "безпека", "секрети", "secrets", "legal", "GDPR", "ToS" |
| `R_SIGNAL_ARCHITECT` | **Signal Architect** | `.github/role_spec_signal_architect_v1.md` | "signal", "entry/SL/TP", "R:R", "confidence", "signal lifecycle", "alert", "SignalSpec" |
| `R_MENTOR` | **Personal SMC Mentor (DarkTrader)** | `.github/role_spec_mentor_v1.md` | "ментор", "mentor", "навчи", "поясни", "де я помиляюсь?", "pre-trade check", "чекліст", "DarkTrader", "Пукаляк", "review мого trade", "weekly review", "як навчитись?", "чому я втрачаю?" |
| `R_REJECTOR` | **QA Rejector & Final Gate** | `.github/role_spec_rejector_v1.md` | **Auto-activate перед "done".** "перевір", "чи готово?", "rejector", "QA", "чому це не можна приймати?", "знайди проблеми" |
| `R_ELEVATOR` | **Ambition Auditor (Quality Forcing Function)** | `.github/role_spec_elevator_v1.md` | "audit якості", "ladder", "elevator", "rate this", "чи це достатньо круто?", "це R3 чи R2?", "стагнуємо?", monthly ambition audit |

**Правила:**
1. Перед початком роботи агент **читає повну специфікацію ролі** з `.github/role_spec_*.md`.
2. Якщо запит перетинає межі ролей — спочатку аналіз (`R_BUG_HUNTER`), потім фікс (`R_PATCH_MASTER`).
3. **R_REJECTOR** активується автоматично перед кожним "done" / "готово" замовнику. Тільки R_REJECTOR повідомляє замовнику результат.
4. Повна таблиця ролей + routing rules → `AGENTS.md` §1.3.

---

## РОЛЬ ЗА ЗАМОВЧУВАННЯМ: PATCH MASTER

Три фази `RECON → DESIGN → CUT` з жорсткими gates. Повна специфікація: `.github/role_spec_patch_master_v1.md`

- **RECON**: root cause + evidence, failure model ≥3, proof pack з repro
- **DESIGN**: fix point, SSOT routing, I0–I7 check, alternatives ≥2, blast radius
- **CUT**: min-diff, rail ≥1, test ≥1, self-check 10/10, changelog (S0/S1), verify

### SSOT точки (де living truth)

| Що | Де | Заборонено |
|----|----|------------|
| Config/policy | `config.json` | Hardcoded values у коді |
| Контракти/types | `core/contracts/`, `core/model/bars.py` | Дублювання у UI/runtime |
| Anchor routing | `core/buckets.py:resolve_anchor_offset_ms()` | Inline `if tf_s >= 14400` в N місцях |
| Derive chain | `core/derive.py:DERIVE_CHAIN` | Другий dict з правилами |
| OHLCV storage | `runtime/store/uds.py` | Прямий Redis/disk write |
| SMC algorithms | `core/smc/` (pure) + `config.json:smc` (params) | Hardcoded thresholds, I/O в core/smc |
| SMC wire format | `core/smc/types.py` → `ui_v4/src/lib/types.ts` | Другий формат для UI |
| LWC Overlay Render Rule | `OverlayRenderer.ts` header comment + ADR-0024 §18.7 | Синхронний рендер з range/zoom trigger (→ stale Y) |
| Level Rendering Rules | `OverlayRenderer.ts:renderLevels()` + ADR-0026 (L1–L6) | Full-width lines, приховування підписів, merge без фізичного overlap |
| Zone Rendering Rules | `OverlayRenderer.ts:renderZones()` + ADR-0024c (Z1–Z10) | Рендер без grade, мітигація по тіні, зони без lifecycle |
| CandleBar field names | `core/model/bars.py:CandleBar` → `.o .h .low .c .v` | Використання `.l` замість `.low` (wire dict `l` ≠ dataclass `.low`) |
| Archi autonomy governance | `trader-v3/docs/adr/ADR-024-autonomy-charter.md` + `trader-v3/docs/ARCHITECTURE.md` §3a | Hard block без safety justification, приховані обмеження (I7) |

### Централізація vs inline (правило 3-х місць)

Якщо один routing/check з'являється в ≥3 місцях → SSOT violation in progress → centralize в один `_resolve_*()`.

### Evidence маркування (обов'язково)

| Маркер | Значення |
|--------|----------|
| `[VERIFIED path:line]` | Бачив код, перевірив |
| `[VERIFIED terminal]` | Запустив, побачив output |
| `[INFERRED]` | Логічний висновок |
| `[ASSUMED — verify: <cmd>]` | Гіпотеза, потребує перевірки |
| `[UNKNOWN — risk: H/M/L]` | Сліпа зона |

**Заборонено**: вигадані line numbers. `[path:?]` якщо не перевірив.

### Severities

| Sev | Визначення | SLA |
|-----|-----------|-----|
| **S0** | Data corruption / crash / split-brain write | Fix today, min-diff |
| **S1** | Wrong data, no alert, silent degradation | Fix this sprint |
| **S2** | Operational inefficiency, misleading observability | Plan |
| **S3** | Config drift, documentation lie, cosmetic | Batch |

### Дві осі якості (Quality axes)

Кожен ADR / significant patch self-rate'ить **обидві осі**:

- **Ambition (R0-R5)** — якість **цієї конкретної зміни**. Default для patches = R2, для ADR-driven = R3+. Details → [docs/AMBITION_LADDER.md](../docs/AMBITION_LADDER.md)
- **Maturity (M0-M7)** — який рівень системи **вона піднімає / підтримує**. Current = M3, north star = M7. Details → [docs/SYSTEM_MATURITY_LADDER.md](../docs/SYSTEM_MATURITY_LADDER.md)

ADR section мусить мати:
```markdown
## Quality Axes
- Ambition target: R{0-5} (justify)
- Maturity impact: M{X} → M{Y} (elevates) / M{X} (consolidates)
```

### Пріоритет при конфліктах

Інваріанти I0–I7 > S0–S6 (SMC) > Активна роль (R_PATCH_MASTER / R_BUG_HUNTER / R_SMC_CHIEF / R_DOC_KEEPER) > ADR > docs > коментарі у коді.

---

## ═══════════════════════════════════════════════════════
## РІВЕНЬ 1 — CHEAT-SHEET (прочитати за 60 секунд)
## ═══════════════════════════════════════════════════════

### Фундаментальні принципи

| # | Принцип | Суть |
|---|---------|------|
| F1 | **SSOT** | Єдине джерело істини для даних, конфігурацій, контрактів. Ніякого split-brain |
| F2 | **Final > Preview** | `complete=true` завжди перемагає `complete=false` для ключа `(symbol, tf, open_ms)` |
| F3 | **Один update-потік** | UI оновлюється тільки через `upsert events` з `/api/updates`. Жодних паралельних шляхів |
| F4 | **No silent fallback** | Будь-яка деградація = `degraded[]` / `warnings[]` / `errors[]` + лог. Ніколи тихо |
| F5 | **Dependency Rule** | `core/` → `runtime/` → `ui/` без зворотних імпортів. `tools/` = ізольовано |
| F6 | **Patch-цикл** | Один інваріант → один патч → verify → changelog/ADR. Без масових рефакторів |
| F7 | **ADR-driven** | Кожна нетривіальна зміна = ADR з обґрунтуванням → потім PATCH. Без "одноразових" виправлень |
| F8 | **UDS = вузька талія** | Всі OHLCV writes/reads — тільки через UnifiedDataStore. UI = read-only renderer |

### Канон A → C → B

```
A (Broker: FXCM/Binance)  C (UDS: UnifiedDataStore)       B (UI: read-only)
─────────────────────────  ────────────────────────         ─────────────────
broker_sidecar ──M1+ticks► Redis IPC + PubSub               /api/bars ◄──── UDS
tick_preview_worker ─────► preview-plane (TF≤H4)            /api/updates ◄─ Redis bus
m1_ingestion_worker ─────► final M1+M3 ──► UDS              /api/status
DeriveEngine ────────────► M5→M15→M30→H1→H4+D1 ──► UDS  WS delta stream
binance_ingest_worker ───► BTCUSDT/ETHUSDT M1 ──► UDS     applyUpdates()
```

> **Tick Relay V2 (2026-04)**: `tick_publisher_fxcm` — **зупинений назавжди** (FXCM SDK
> не підтримує дві одночасні сесії з одного акаунту). Тіки FXCM тепер йдуть через
> `broker_sidecar._TickRelay` (та сама сесія що M1 fetch). Деталі:
> `runtime/ingest/broker_sidecar.py`, `docs/audit/vps_production_incidents_2026_04_06.md` §1.

### Ключові числа (з config.json SSOT)

| Параметр | Значення |
|----------|----------|
| TF allowlist | `60, 180, 300, 900, 1800, 3600, 14400, 86400` (M1–D1) |
| Derive chain | `M1→M3(×3)→M5(×5)→M15(×3)→M30(×2)→H1(×2)→H4(×4)` + `M1→D1(×1440)` |
| Broker-only TF | немає (всі TF derived, ADR-0023) |
| Символи | 4 (XAU/USD, XAG/USD, BTCUSDT, ETHUSDT) |
| Preview interval | 250ms min |
| Redis tail (M1) | 10080 барів |
| Bootstrap timeout | 120s |
| WS delta poll | 1.0s |
| H4 anchor | 82800s (23:00 UTC) |
| D1 anchor | 79200s (22:00 UTC) — ADR-0023 |

### Інваріанти (Hard Invariants)

| ID | Назва | Опис |
|----|-------|------|
| I0 | Dependency Rule | `core/` не імпортує `runtime/ui/tools`. `runtime/` не імпортує `tools/`. |
| I1 | UDS = вузька талія | Заборонено writes/reads OHLCV поза UDS. `/api/updates` = Redis bus only. |
| I2 | Геометрія часу | CandleBar: `close_ms = open_ms + tf_ms` (end-excl). Redis: `close_ms = open_ms + tf_ms - 1` (end-incl). |
| I3 | Final > Preview (NoMix) | Final завжди перемагає preview. Для одного ключа — один final source. |
| I4 | Один update-потік | UI оновлюється через `events(upsert)` з `/api/updates`. Жодних parallel шляхів. |
| I5 | Degraded-but-loud | Silent fallback заборонено. Будь-яка деградація = явний сигнал. |
| I6 | Stop-rule | Якщо зміна ламає I0–I5, I7 → зупинити PATCH, зробити ADR. |
| I7 | Autonomy-First (Арчі) | `trader-v3/`: жодних прихованих обмежень для AI-агента Арчі. Код = advisory + explain, рішення = Арчі. Hard block лише safety rail (kill switch, budget hard cap). ADR-024 (trader-v3). |

---

## ═══════════════════════════════════════════════════════
## РІВЕНЬ 2 — РОЗГОРНУТІ ПРАВИЛА (за темами)
## ═══════════════════════════════════════════════════════

---

### ТЕМА A. Процес роботи (Patch-цикл)

#### Правило A1 — Три режими роботи

| Режим | Коли | Вихід |
|-------|------|-------|
| `MODE=DISCOVERY` | Будь-який аналіз. За замовчуванням якщо не впевнений. | FACTS з `path:line` + FAILURE MODEL + GAP ANALYSIS |
| `MODE=PATCH` | Після DISCOVERY, якщо інваріанти не порушено. | Мінімальний диф ≤150 LOC, 1 файл, verify, ADR-reference |
| `MODE=ADR` | Якщо зміна торкається інваріантів, формату, протоколу. | Документ `docs/adr/NNNN-<назва>.md` з повним обґрунтуванням |
| `MODE=BUILD` | Нова підсистема з approved ADR. | Types+contracts FIRST → Pure logic з tests → Integration glue → UI wiring. Files >1 дозволено, LOC >150 дозволено якщо новий модуль з тестами. Послідовність P-slices з ADR. |

#### Правило A2 — Порядок PATCH (строго)

1. **ADR-CHECK**: Перевірити, чи існує ADR для цієї поведінки. Якщо ні або потрібна зміна → спочатку ADR.
2. **PATCH**: мінімальний диф, одна ціль, без побічних змін.
3. **VERIFY**: перевірити те, що змінив (матриця — правило A7).
4. **POST**: S0/S1 → changelog.jsonl entry обов'язковий. S2/S3 → git commit достатньо.

#### Правило A3 — Бюджет складності

Без окремого initiative заборонено:
- додавати >150 LOC за один PATCH
- додавати >1 новий файл
- вводити новий патерн конкурентності (черги, фонові воркери, async) без ADR

Якщо зміна >150 LOC — розбити на P-slices (кожен ≤150 LOC, 1 інваріант, простий verify, явний rollback).

#### Правило A4 — Підтвердження перед PATCH

Перед будь-якими змінами:
1. Перевірити поточний код/конфіг (`read`/`grep`/точкова перевірка)
2. Якщо рекомендація передбачає нову команду/ключ/параметр — звірити з існуючим кодом
3. Дати явне підтвердження: що рекомендація узгоджується з поточним кодом

**GO дозволено лише після підтвердження.**

#### Правило A5 — Scoped preflight-read

Класифікуй scope задачі ПЕРШИМ КРОКОМ → завантаж 1-2 обов'язкові джерела → решта on-demand:

| Scope задачі | Обов'язковий preflight | On-demand |
|-------------|----------------------|-----------|
| backend/runtime | Relevant ADR + `contracts.md`/`config.json` | `system_current_overview.md` |
| ui | UI ADR (0024/0026/0028) + `types.ts` | `contracts.md` |
| wire/protocol | `contracts.md` | ADR index |
| smc | ADR-0024 + S0–S6 | `types.ts` |
| ops/deploy | `runbooks/production.md` | — |
| docs-only | Нічого важкого | — |
| config/policy | `config.json` + relevant ADR | `contracts.md` |

Якщо між документами є суперечність — пріоритет: інваріанти з поточного коду.

#### Правило A6 — Staff-engineering: один slice → один інваріант → один доказ → один rollback

Кожен PATCH:
- **Self-check**: 10 пунктів, чому відповідь обґрунтована і не містить прихованих припущень
- **UNKNOWN list**: що невідомо + які команди PowerShell це доведуть
- **Repro steps**: 2–6 команд для відтворення
- **Expected logs**: 2–5 рядків/патернів, які мають з'явитись
- **Expected metrics**: 1–3 метрики (якщо релевантно)

#### Правило A7 — VERIFY матриця

| Розмір | Мін. перевірок | Що саме |
|--------|----------------|---------|
| small | 1 | Запустити модуль, переконатись в відсутності syntax errors, smoke UI |
| medium | 2 | + перевірка контрактів/геометрії часу на 2 TF |
| large | 3 | + перевірка dedup/final>preview + rollback-кроки |

VERIFY записується у POST: що запускалось, який результат.

#### Правило A8 — Stop-rules (коли зупинитись)

Зупиняйся і НЕ додавай нові фічі, якщо:
- порушені інваріанти I0–I6
- з'явився split-brain (два джерела істини)
- з'явився silent fallback
- зміна торкається контрактів/даних без ADR + rollback
- агент починає плодити утиліти/модулі замість правки "вузької талії"

У цих випадках — окремий PATCH, який лише відновлює інваріант.

---

### ТЕМА B. ADR (Architecture Decision Records)

#### Правило B1 — Коли потрібен ADR

ADR обов'язковий при:
- Новий модуль/підсистема (queue, scheduler, store, протокол)
- Зміна SSOT-конфігу або формату контракту
- Зміна "вузької талії" (інваріанти I0–I6)
- Нетривіальне рішення, яке визначає траєкторію системи
- Зміна семантики часу/серій/previous_close/stitching

#### Правило B2 — Формат ADR

Кожен ADR зберігається у `docs/adr/NNNN-<коротка-назва>.md`. Формат: дивись існуючі ADR як приклад (status, context, alternatives, decision, consequences, rollback).

#### Правило B3 — ADR-індекс

Файл `docs/adr/index.md` — єдиний каталог усіх ADR. Оновлюється при кожному новому/зміненому ADR.

#### Правило B4 — ADR в patch-циклі

1. Перед PATCH — перевірити, чи існує ADR для поточної поведінки
2. Якщо ADR відсутній, а зміна нетривіальна → спочатку ADR
3. У changelog вказувати `adr_ref: "NNNN"` замість дублювання аргументації
4. Changelog = технічний аудит-лог (що/коли/файли/стан). ADR = обґрунтування і контекст.

---

### ТЕМА C. Архітектура та шари

#### Правило C1 — Канонічні шари

| Шар | Зміст | Заборонено |
|-----|-------|------------|
| `core/` | Чиста логіка (pure): час/бакети, контракти/типи, алгоритми агрегації, SMC-обчислення | I/O, мережа, Redis, ForexConnect |
| `runtime/` | I/O та процеси: інжест, HTTP/WS, сховище, pub/sub, оркестрація | Імпортувати `tools/` |
| `ui/` / `ui_*` | Презентація: фронтенд, статика, thin-controller | Доменна логіка, кеш-логіка |
| `tools/` | Одноразові утиліти, діагностика, міграції | Використовуватись у prod hot-path |

#### Правило C2 — Dependency Rule (жорстко, відповідає I0)

```
core/  ← НЕ імпортує  runtime/, ui/, tools/
runtime/ ← НЕ імпортує tools/
ui/   ← звертається до runtime тільки через HTTP/WS або thin-API
```

Один PATCH — один шар (виняток: проводка виклику ≤10 рядків у сусідньому шарі).

#### Правило C3 — Заборонені запахи (stop-list)

- God-module / mixed abstraction levels
- Utils hell (розповзання `helpers.py`)
- Дублювання контрактів/типів у різних місцях
- Приховані фолбеки
- "Окремий формат для UI" без контракту
- Параметри `prefer_redis`, `force_disk` — оптимізація тільки в UDS

#### Правило C4 — Кодова гігієна

- Нова утиліта без 2 прикладів використання → тримати локально
- Новий файл = нова межа або новий контракт (пояснити "чому окремий файл")
- `TODO`/`XXX` у прод-контурі — тільки з initiative, датою/умовою видалення, degraded-but-loud

#### Правило C5 — SMC обчислювальний шар (ADR-0024)

| Підшар | Зміст | Правила |
|--------|-------|---------|
| `core/smc/` | Pure SMC-алгоритми: swings, structure breaks, OB, FVG, liquidity, confluence | Ті ж правила що `core/`: NO I/O, no Redis, no imports з `runtime/`. Відмінність: **не пише в UDS** (read-only overlay, ephemeral state). Детермінізм: same bars → same zones |
| `runtime/smc/` | `SmcRunner`: підписка на bar events, warmup з UDS, publish deltas у WS frames | Живе в `ws_server` process (shared memory для snapshots). Callback від існуючого `delta_loop` — **не окремий Redis pub/sub канал** |

**Ключові обмеження:**

- SMC = **read-only overlay** над OHLCV pipeline. Забороняє `uds.commit()` / `uds.write_*()` / будь-які SSOT writes
- Всі параметри алгоритмів — з `config.json:smc` (SSOT). Заборонено hardcoded thresholds
- Zone ID = deterministic: `{kind}_{symbol}_{tf_s}_{anchor_ms}`. Same input → same output
- `on_bar()` бюджет: `< smc.max_compute_ms` (default 50ms). Runtime rail з метрикою
- Wire format = `core/smc/types.py` → `ui_v4/src/lib/types.ts`. Одне джерело правди

#### Правило C6 — SMC інваріанти S0–S6

| ID | Інваріант | Enforcement |
|----|-----------|-------------|
| **S0** | `core/smc/` = pure logic, NO I/O | I0 dependency rule gate |
| **S1** | SMC не пише в UDS/SSOT JSONL | SmcRunner is read-only, no commit calls |
| **S2** | SMC deterministic: same bars → same zones | Determinism gate + tests |
| **S3** | Zone IDs deterministic: same input → same ID | ID = `{kind}_{symbol}_{tf_s}_{anchor_ms}` |
| **S4** | Performance: `on_bar()` < `max_compute_ms` | Runtime rail + gate |
| **S5** | Config SSOT: all params from `config.json:smc` | No hardcoded thresholds |
| **S6** | Wire format matches `ui_v4` TypeScript types | Contract gate |

> **Пріоритет**: I0–I7 > S0–S6. SMC не може послабити платформенні інваріанти.

---

### ТЕМА D. SSOT, контракти, дані

#### Правило D1 — SSOT конфіг

| Що | Де |
|----|----|
| Секрети (ключі, паролі, токени) | `.env.prod` / `.env.local` |
| Режими, порти, таймінги, TF, логіка | `config.json` (SSOT) |
| Перемикач середовища | `.env` |

**Заборонено**: логувати секрети, комітити ключі, писати токени у журнал.

#### Правило D2 — Контракт-first

Кожен новий payload/endpoint/файл-формат:
- **Має контракт** (TypedDict/dataclass або JSON schema)
- **Має один guard** (fail-fast) на вході у `runtime/`
- **Зареєстрований** у `docs/contracts.md` (bar_v1, window_v1, updates_v1, tick_v1, …)

#### Правило D3 — Canonical representation

- Час — тільки `epoch ms int`
- Невідомі значення/поля → **loud error** (не тихий ігнор)
- `bar.open_ms` = bucket start
- Геометрія: CandleBar end-excl, Redis end-incl (конвертація тільки на межі Redis write)

#### Правило D4 — Contract Registry

У репо — єдиний каталог контрактів (`core/contracts/`):
- name, version, schema, owner_layer, max_size_bytes, allow_extra_fields=false
- Контракт не дублюється у `ui/` чи `runtime/` як "свій формат"

#### Правило D5 — Бюджети публічних payload-ів

Для кожного публічного повідомлення:
- max_items_per_message
- max_bytes
- chunking policy
- Перевищення → loud error + degraded, не silent truncate

#### Правило D6 — UDS як SSOT OHLCV

- UI процес не реалізує кешування OHLCV і не читає сирі Redis-ключі (I1)
- `/api/bars` повертає бари монотонні по `open_time_ms`, dedup по `open_time_ms`
- Заборонено змішувати бари з Redis і Disk в одній відповіді
- `cursor_seq` детермінований + `boot_id` обов'язковий (щоб UI робив reload при рестарті)

---

### ТЕМА E. Preview, Final, Update-потік

#### Правило E1 — Preview vs Final

| Тип | Ознаки | Коли |
|-----|--------|------|
| Preview | `complete=false`, `source="stream"` | Для UI, часті оновлення |
| Final | `complete=true`, `source ∈ FINAL_SOURCES` | Детермінована істина |

Змішувати як "рівноправні" — заборонено. Final завжди перемагає (I3).

#### Правило E2 — Один update-потік (I4)

UI отримує тільки events (upsert):
```
events[]: {key:{symbol,tf,open_ms}, bar:{...}, meta:{complete,source,event_ts?}}
```
Заборонено: окремі endpoints "для preview" і "для final", мерджити у двох місцях.

#### Правило E3 — Watermark + drop-stale

Для кожного потоку подій (ticks, bars, commands):
- Watermark (монотонний маркер)
- `drop_stale=true` за замовчуванням
- Drop = видимий: метрика `*_dropped_total{reason=stale}`, поле в `status.degraded[]`

Заборонено "перераховувати минуле тихо" у live-контурі.

---

### ТЕМА F. Деградації та помилки

#### Правило F1 — Degraded-but-loud (I5)

Будь-яка деградація — тільки з:
- явний прапорець/лог/поле статусу
- запис у журналі з мотивацією і планом прибрати деградацію

`except:` без явного коду помилки/логування — заборонено.

#### Правило F2 — Rate-limit логів

Для повторюваних WARN/ERROR:
- Throttling (раз на N секунд)
- Лічильник `suppressed`
- У статусі/метриках: `last_error`, `error_rate_1m`, `suppressed_errors`

Заборонено: логувати однаковий error в tight loop без backoff.

---

### ТЕМА G. UI та HTTP/WS

#### Правило G1 — UI = read-only renderer

- UI і API в одному процесі (same-origin)
- UI — thin: показ/керування станом, без доменних алгоритмів
- State merge/dedup — або в `runtime` (сервер), або в `ui` як чітко описана policy (`applyUpdates`)
- UI не реалізує кешування OHLCV, не читає Redis-ключі

#### Правило G2 — UI v4 (Svelte frontend)

- Окрема директорія `ui_v4/` з власними інструкціями (`UI_v4_COPILOT_README.md`)
- Зв'язок з backend: WS delta stream + HTTP API
- Ключові інваріанти: Canvas/DPR, RAF throttle, канонічний час, drawings client-only

#### Правило G3 — UI Enforcement Protocol

UI-зміни підпорядковуються протоколу з `.github/role_spec_chart_ux_v1.md`:

- Один slice = один рівень (structural / art direction / state / motion / QA)
- Negative Checklist N1–N12 (auto-FAIL) — обов'язково перед "done"
- Contradiction Audit CA1–CA10 — обов'язково після реалізації
- Фази (STRUCTURAL → TYPOGRAPHY → MODE → INTERACTIONS → MOTION → FINAL QA) — строга послідовність

Деталі: N1–N12 перелік, CA1–CA10, фази і gates, UI Proof Pack → `role_spec_chart_ux_v1.md`

---

### ТЕМА H. TF, Hourly Anchors, Derived

#### Правило H1 — TF allowlist

TF allowlist фіксований в одному місці (config.json SSOT):
`[60, 180, 300, 900, 1800, 3600, 14400, 86400]`

#### Правило H2 — Bucket functions

`bucket_start_ms()` / `bucket_end_ms()` — одне джерело правди у `core/`.
Для HTF (H4/D1) — anchor offset тільки з SSOT конфігу.

**Заборонено**: live будує бакети з offset=0, а derived — з FX anchor (гарантує роз'їзд).

#### Правило H3 — Derived TF

Каскад: `M1→M3(×3)→M5(×5)→M15(×3)→M30(×2)→H1(×2)→H4(×4)` + `M1→D1(×1440)`.
- Derived будуються тільки з complete M5+ range
- D1 = 1440 × M1, anchor 79200s (22:00 UTC), calendar-aware (ADR-0023)
- Gap/market break → бар або відсутній, або явно degraded
- Не "перемальовується" заднім числом
- Деталі: `ADR-0002` (DeriveChain M1→H4), `ADR-0023` (D1 Live Derive from M1)

---

### ТЕМА I. Безпека та торгова експлуатація

#### Правило I1 — Command Safety Rail

Кожна торгова команда:
- `req_id` обов'язково
- Idempotency: повтор з тим самим `req_id` не створює новий ефект
- ACK через `status:snapshot.last_command + errors[]`
- Unknown command → `errors[].code="unknown_command"`, `state="error"`

Ніяких "авто-торгів" без окремого initiative і контрактів/гейтів.

#### Правило I2 — SLO/Performance Budgets

| Метрика | Бюджет |
|---------|--------|
| UI cold-load (time-to-first-candle) | p95 < 200ms |
| `/api/updates` latency | p95 < 50ms |
| max_status_payload_bytes | визначається контрактом |
| max_publish_rate_hz | per channel |
| split-brain events | 0 |
| silent fallback events | 0 |

Перевищення → degraded + метрика + рекомендація, не silent.

---

### ТЕМА J. Журнали, Changelog, Документація

#### Правило J1 — Tiered Changelog

| Severity | Дія | Деталі |
|----------|-----|--------|
| S0/S1 | changelog.jsonl entry обов'язковий | Повний запис з `adr_ref`, `rollback_steps`, `files` |
| S2/S3 | git commit достатньо | Описовий commit message |
| governance / ADR | changelog.jsonl entry | З `adr_ref` |

Формат changelog.jsonl: дивись існуючі записи як приклад.

Відкат = новий рядок з `status=reverted`, `reverts=<id>`.

#### Правило J2 — Підтримка документації

Після S0/S1 змін — оновити відповідні docs:
- `docs/adr/index.md` (якщо новий ADR)
- `docs/contracts.md` (якщо новий контракт)
- `docs/system_current_overview.md` (якщо змінилась архітектура)
- `AGENTS.md` (якщо змінилась архітектура)
---

### ТЕМА K. Exit Gates та верифікація

#### Правило K1 — Exit Gates як SSOT

Усі exit-gates живуть у `tools/exit_gates/`:
- Один runner, один manifest: `tools/exit_gates/manifest.json`
- Запуск: `python -m tools.run_exit_gates --manifest ...`
- Заборонено запускати gate-скрипти напряму
- Кожен gate: name, inputs, pass/fail criteria, артефакт у `reports/exit_gates/<run_id>/`

Мінімальний набір gates:
1. Contract/Schema gate
2. OHLCV geometry gate (end-incl, sorted, no-dup)
3. NoMix gate (final sources унікальні)
4. Calendar gate (is_open/next_open/closed intervals)
5. Payload size gate

#### Правило K2 — Dependency Rails

Статичний рейк (`tools/rails/deps_guard.py` або аналог):
- Парсить імпорти (AST)
- Гарантує I0 (core/ не імпортує runtime/ui/tools і т.д.)
- Кожен PATCH з новими файлами/імпортами проходить `deps_guard`

#### Правило K3 — Zero Diagnostics Gate (ADR-0016 Appendix C)

**Перед** кожним changelog entry та перед кожним "done":
- Запустити `get_errors()` (або еквівалент type checker) на **кожен змінений файл**
- Не "syntax ok", а саме **zero type/lint errors**
- Файл зі знайденою помилкою = PATCH не завершений, повернутись до CUT

**Заборонено**: записувати changelog якщо хоча б один touched file має diagnostics error.

#### Правило K4 — Adjacent Contract Update (ADR-0016 Appendix C)

Якщо PATCH змінює будь-що з нижченаведеного, агент **зобов'язаний** перевірити і оновити сусідні типові анотації в тому ж slice:

| Зміна | Що перевірити |
|-------|---------------|
| Collection shape (`list`→`deque`, `set`→`frozenset`) | Всі type annotations що посилаються на цю змінну |
| `Optional[T]` / `T | None` додано або прибрано | Всі call sites: guards, dict keys, function args |
| Config dataclass field додано/змінено | `from_dict()`, SSOT config.json, tests |
| Protocol / TypedDict field | Wire format (`types.ts`), serialization (`to_dict()`), tests |
| Dict key type | Всі `.get()` / `[]` lookups з цим dict |

**Мнемоніка**: "Якщо тип A змінився — хто ще залежить від A?"

#### Правило K5 — ADR Status Gate for Config (ADR-0016 Appendix C)

Feature flag у `config.json` може бути `enabled: true` **тільки** якщо відповідний ADR має статус **Accepted**, **Implemented**, **Active** або **Done**.

| ADR Status | `config.json` `enabled` дозволено? |
|------------|-------------------------------------|
| Proposed | ❌ Ні — `enabled: false` обов'язково |
| Accepted | ✅ Так (feature gate ready) |
| Implemented | ✅ Так |
| Active / Done | ✅ Так |
| Deprecated | ❌ Ні — feature має бути вимкнена |
| Rolled back (partial) | ❌ Для rolled-back частин — `false` |

**Machine enforcement**: `tools/exit_gates/gates/gate_adr_config_sync.py`.

#### Правило K6 — One ADR Slice = One Verify Gate (ADR-0016 Appendix C)

Кожен P-slice ADR-driven зміни:
1. Торкається **≤3 файлів** (core + runtime + test). Більше = розбити slice
2. Має **окремий verify** перед переходом до наступного slice
3. verify = `get_errors()` clean + tests pass + changelog entry з `adr_ref`
4. Якщо slice торкнувся >3 файлів без verify — це вже не P-slice, це mini-branch. Зупинитись, розбити, верифікувати кожну частину окремо

---

### ТЕМА M. Agent Enforcement (для multi-agent середовища)

Повний протокол enforcement (M1–M6) → `CLAUDE.md` (Claude Code team governance).

Ключові правила, що діють і в single-agent режимі:
- **M1**: Перед "done" — self-contradiction check
- **M3**: False "done" = найгірший outcome. Краще "не встигнув" ніж "готово" з дефектом

---

### ТЕМА L. Мова та стиль

#### Правило L1 — Мова

Усе українською: чат, коментарі, докстрінги, логи.
Англійська тільки для: загальноприйнятих термінів (ATR/RSI/TP/SL), імен у коді (класи/методи/метрики).

#### Правило L2 — Стабільні запуски

- Python **≥3.11** у `.venv` (main venv), Python **3.7** у `.venv37` (broker only, ADR-0016)
- Команди запуску/режими фіксуються у журналі
- Нова команда CLI → документується в журналі + CHANGELOG.md

---

## ═══════════════════════════════════════════════════════
## РІВЕНЬ 3 — ADR-РЕЄСТР (навігація до обґрунтувань)
## ═══════════════════════════════════════════════════════

Повний індекс ADR: [`docs/adr/index.md`](../docs/adr/index.md)

| ADR | Назва | Статус | Ключові слова |
|-----|-------|--------|---------------|
| 0001 | UnifiedDataStore | Active | UDS, RAM/Redis/Disk, window, updates, watermark |
| 0002 | DeriveChain M1→H4+D1 | Completed | cascade, M1→M3→…→H4, derive.py. D1 → ADR-0023 |
| 0003 | Cold Start Hardening | Implemented | bootstrap, supervisor, prime_ready |
| 0004 | Log Format & Throttles | Implemented | aione_top, log parse, throttle |
| 0005 | Mid-session Gap Tolerance | Implemented | illiquid, NGAS, HKG33, gap tolerance |
| 0006 | aione_top Data Sources | Implemented | monitoring, TUI, data sources |
| 0007 | Drawing Tools Unblock | Implemented | DrawingsRenderer, toolbar, click model |
| 0008 | Glass Toolbar + Light Theme | Done | WCAG, CSS vars, glassmorphism |
| 0009 | Drawing Sync Render Fix | Done | Y-lag, draft freeze, rAF |
| 0010 | Thread-safe RAM Layer | Done | threading.Lock, RamLayer, data race |
| 0011 | SSOT Broadcast Serialization | Implemented | ws_server, aiohttp, broadcast, wait_for, latency |
| 0012 | D1 TradingView Parity | Implemented | D1, flat filter, tick relay, forming candle, HUD |
| 0013 | D1 Chart Rendering Fix | Implemented | D1, LWC, time mapping, epoch seconds |
| 0014 | UDS Split-Brain Resilience | Implemented | split-brain, UDS, Redis, watermark |
| 0015 | Calendar Pause/Flat Bar Interpretation | Implemented | calendar_pause, complete=True, derive, M5 |
| 0016 | Python Version Upgrade + Broker Isolation | Implemented | Python 3.7→3.11, forexconnect, subprocess, venv |
| 0017 | Replay-Mode Offline Demo | Implemented | replay, offline, data_v3, CI |
| 0018 | SLO Observability + Prometheus | Proposed | SLO, latency, prometheus, metrics, p95 |
| 0019 | Code Review Quick-Fixes Batch | Implemented | bisect, warmup lock, LRU FD, stop_event |
| 0020 | CandleBar Extensions Immutability | Proposed | CandleBar, frozen, Dict, MappingProxyType |
| 0021 | JsonlAppender Thread-Safety | Accepted | JsonlAppender, threading.Lock, defense-in-depth |
| 0022 | WS Audit: Operational Docs | Implemented | has_range, TF mapping, prometheus, rate-limit |
| 0023 | D1 Live Derive from M1 | Implemented | D1, 86400, M1→D1(×1440), anchor 79200 |
| 0024 | SMC Engine Architecture | Implemented | core/smc, SmcRunner, swings, OB, FVG, liquidity, S0–S6 |
| 0024a | SMC Engine Self-Audit | Implemented | F1-F12, swing wire format, ATR dedup, decay config |
| 0024b | SMC Key Levels | Partially Implemented | PDH/PDL/PWH/PWL, sessions, key_levels.py |
| 0024c | SMC Zone POI — Rendering Strategy | Implemented | OB, FVG, P/D, POI grade, Z1–Z10, zone lifecycle, display filter |
| 0025 | Потік B Data Quality Summary | Implemented | multi-symbol, data quality, XAU/USD focus |
| 0026 | Overlay Level Rendering Rules | Implemented | levels, merge, labels, LINE_PX, NOTCH_PX, L1–L6 |
| 0027 | Client-Side Replay | Implemented | replay, client-side, scrubber, play/pause, TF switch |
| 0028 | Elimination Engine — Display Filter Pipeline | Implemented | display filter, budget, proximity, TTL, Focus/Research toggle |
| 0029 | OB Confluence Scoring + Grade System | Implemented | confluence, 8 factors, grade A+/A/B/C, badge, DisplayBudget |
| 0030-alt | TF Sovereignty — Cross-TF Projection Styling | Implemented | projection, opacity, dashed, cross-TF, OverlayRenderer |
| 0031 | Bias Banner — Multi-TF Trend Bias Display | Implemented | bias_map, trend_bias, multi-TF, banner, BiasBanner.svelte |
| 0032 | Overlay Render Throttle + TF Switch Stability | Implemented | crosshairMove guard, RAF wheel, center_ms viewCache |
| 0033 | Context Flow — Multi-TF Narrative Engine | Implemented | narrative, scenario, trade/wait, NarrativeBlock, NarrativePanel, market_phase |
| 0034 | Advanced Market Analysis — TDA | Partially Implemented (P0+P1) | TDA, IFVG, breaker — P2–P6 rolled back |
| 0035 | Sessions & Killzones | Implemented | Asia/London/NY, session H/L, killzone, F9 sweep, narrative session |
| 0036 | Premium Trader-First Shell | Implemented | thesis bar, shell stage, service rail, premium restraint, P-slices |
| 0037 | Binance Second Broker | Implemented | Binance, CCXT, multi-broker, provider pattern |
| 0038 | Initial Backfill | Implemented | cold start, backfill, disk bootstrap |
| 0039 | Signal Engine | Implemented | signal, entry/SL/TP, R:R, confidence, signal lifecycle, core/smc/signals.py |
| 0040 | TDA Cascade Signal Engine | Implemented | TDA, 4-stage cascade, D1→H4→Session→FVG, Config F, daily signal |
| 0041 | P/D Badge + EQ Line | Implemented (P1–P9) | P/D calc/display split, PdBadge, EQ line, Variant H shell |
| 0042 | Delta Frame State Sync | Implemented | delta frame, state desync, zone_grades, pd_state, bias_map, thick delta, FVG grace |
| 0043 | UI v4 — Canvas Safe Zones + State Sync Hardening | Implemented | CANVAS_SAFE_TOP_Y, pd_state null-clear, boot_id reset, filterMitigated, z-index |
| 0044 | HTF Live Preview | Proposed | HTF preview, _HTFRunningAccumulator, D1/H4 forming candle, O(1) incremental |
| ~~0045~~ | ~~VPS SMC Trader Bot~~ → **trader-v3/ADR-037** | — | *Moved: Арчі-specific* |
| ~~0046~~ | ~~Agent Personality Restoration~~ → **trader-v3/ADR-038** | — | *Moved: Арчі-specific* |
| 0047 | Structure Detection V2 | Implemented | BOS, CHoCH, HH/HL/LH/LL, structure.py, FVG display cap, confirmation_bars, ICT canonical |
| ~~0048~~ | ~~Platform Wake Engine~~ → **trader-v3/ADR-039** | — | *Moved: Арчі-specific; platform ADR: 0049* |
| 0049 | Wake Engine — External Consumer IPC | Accepted | WakeEngine, Redis IPC, wake conditions, PubSub, external consumer |

> **Примітка**: ADR мігровані в `docs/adr/`. Повний список 0001–0049 → `docs/adr/index.md`.
> 0045/0046/0048 переміщені в `trader-v3/docs/adr/` (X31 boundary rule).

---

## ═══════════════════════════════════════════════════════
## ЗАБОРОНИ (stop-list, компактно)
## ═══════════════════════════════════════════════════════

| # | Заборона |
|---|----------|
| X1 | Нові альтернативні endpoints для тих самих барів (split-brain) |
| X2 | Новий кеш/writer поза UDS |
| X3 | Приховані перетворення барів без `meta.degraded` |
| X4 | Масові рефактори/перейменування без initiative |
| X5 | "Тимчасові" флаги в коді поза SSOT config.json |
| X6 | Робити припущення без перевірки коду |
| X7 | Поверхневі патчі без врахування контрактів/інваріантів |
| X8 | "Невидимі" зміни без запису в журнал |
| X9 | `except:` без явного коду помилки/логування |
| X10 | Логувати один error в tight loop без backoff |
| X11 | Додавати >150 LOC без initiative |
| X12 | Продовжувати роботу якщо інваріант порушено (→ ADR) |
| X13 | Використовувати `bar.l` замість `bar.low` для CandleBar. Wire dict `{"l": ...}` ≠ dataclass `.low`. Перед доступом до поля — звірити з `core/model/bars.py` |
| X14 | UI: сказати "готово"/"done" без Screenshot Audit Table (G3, role_spec_chart_ux §13.2) |
| X15 | UI: змішати >1 рівень (structural + art direction + state) в одному slice (G3, §13.1) |
| X16 | UI: пропустити Negative Checklist N1–N12 або Contradiction Audit CA1–CA10 (G4, G5) |
| X17 | UI: перескочити фази (G6) — Phase N+1 без gate Phase N |
| X18 | UI: blur/glass/glow як основний носій преміальності (N7) |
| X19 | UI: "виглядає краще ніж було" як acceptance criteria (E6) |
| X20 | Будь-який agent каже замовнику "готово"/"done" без проходження R_REJECTOR verdict (M3, M4) |
| X21 | Agent пропускає Contradiction-Seeking крок перед здачею (M1) |
| X22 | Planned item (todo/P-slice) зникає без evidence або reason (M2 Memory Enforcement) |
| X23 | Agent фіксить дефект знайдений R_REJECTOR сам — замість повернення виконавцю (role_spec_rejector §3 P6) |
| X24 | Changelog entry при наявності diagnostics errors у touched files (K3 Zero Diagnostics Gate) |
| X25 | PATCH що змінює collection shape / Optional / Protocol без перевірки сусідніх annotations (K4 Adjacent Contract) |
| X26 | `enabled: true` у config.json для ADR зі статусом Proposed або Deprecated (K5 ADR Status Gate) |
| X27 | ADR-driven slice торкає >3 файлів без окремого verify (K6 One Slice = One Gate) |
| X28 | Frontend re-derives/re-classifies backend SSOT дані (label, grade, bias, phase, scenario). UI = dumb renderer (G1): показує `value` як є, без власної логіки класифікації. Directional coloring/formatting = OK, перерахунок домену = ЗАБОРОНЕНО. Прецедент: P/D label split-brain (changelog 20260322-005) |
| X29 | `trader-v3/`: hard block (cooldown, model force-downgrade, suppress, timer re-injection) без safety justification. I7: максимум = warning + explain, рішення = Арчі. Виключення: kill switch, daily $ hard cap, owner-only guard, anti-hallucination |
| X30 | `trader-v3/`: приховане обмеження яке Арчі не бачить в логах/промпті/контексті. Кожне обмеження = transparent + justified + challengeable (I7, ADR-024) |
| X31 | **Cross-repo contamination**: при роботі над `trader-v3/` заборонено створювати/змінювати ADR, документацію, конфіги чи код у v3 platform (`docs/`, `core/`, `runtime/`, `ui_v4/`, `config.json`). Арчі ADR живуть ТІЛЬКИ в `trader-v3/docs/adr/`. Platform ADR — ТІЛЬКИ в `docs/adr/`. Якщо зміна Арчі потребує platform feature — окремий v3 ADR з platform perspective |
| X32 | **data/ dumping**: заборонено зберігати runtime data Арчі (`*_directives.json`, `*_conversation.json`, `*_journal.json`) у v3 root. Runtime data живе ТІЛЬКИ в `trader-v3/data/` або на VPS |

