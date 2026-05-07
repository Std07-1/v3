# ADR-001: Cowork Memory Architecture — Hybrid Execution + V3 State SSOT

- **Status**: Accepted (2026-05-06)
- **Date**: 2026-05-06
- **Author**: Стас + GitHub Copilot (R_ARCHITECT)
- **Initiative**: `cowork_subsystem_v1`
- **Related ADRs**: [ADR-0058](../../../docs/adr/0058-public-readonly-api-auth.md) (token auth), [ADR-0059](../../../docs/adr/0059-public-analysis-api-raw-data.md) (raw data endpoints), trader-v3 [ADR-024](../../../trader-v3/docs/adr/ADR-024-autonomy-charter.md) (autonomy pattern reference)
- **Supersedes**: nothing (foundational ADR for cowork subsystem)

---

## Quality Axes

- **Ambition target**: R3 — нова підсистема з власним ADR-каталогом, dual-purpose schema, чіткими крос-репо межами; вводить hybrid execution pattern (external task + v3-owned state)
- **Maturity impact**: M3 → M4 — додає formal subsystem boundaries, append-only event store з retention policy, dual-consumer schema design discipline

---

## 1. Контекст

### 1.1 Що таке cowork сьогодні

Cowork — Claude Desktop scheduled task що публікує SMC mentor digest у Telegram-канал
`@aione_smc` поки Архі (trader-v3) у hibernation. Він fetch-ить v3 platform endpoints
(ADR-0058 + ADR-0059), завантажує SSOT prompt
([`docs/runbooks/cowork_prompt_template_v3.md`](../../../docs/runbooks/cowork_prompt_template_v3.md)),
викликає Anthropic API (Opus 4.7 / Sonnet 4.6) і публікує наратив у канал.

**Не Python код**. Це Claude Desktop task — стандартний tool у Claude Desktop UI.
Жодного orchestrator-файлу у репо не існує і не повинно існувати.

### 1.2 Проблема (виявлена при external review v3 prompt)

Зовнішній review поточного prompt v3 (8/10) виявив 4 systematic дефекти:

| # | Дефект | Симптом | Корінь |
|---|---|---|---|
| 1 | Cross-call inconsistency | Сусідні пости показують різний grade тієї ж зони | Stateless task → нема пам'яті між викликами |
| 2 | Silent direction flips | Через 3-6 годин cowork "перевзувається" без пояснення | Нема prior context |
| 3 | Macro feed ignorance | Коли feed недоступний — мовчить про event risk | Прийнято патч (R8.1 calendar fallback) |
| 4 | Bot-leak phrases | "cited as-is", "API state показує" — system prompt просочується | Прийнято патч (AP7 expansion) |

Дефекти #1 і #2 **не вирішуються правкою prompt** — потребують **persistent memory**
між запусками. Stateless Claude Desktop task не може знати що публікував 6 годин тому.

### 1.3 Що ще "висить у повітрі"

Платформа окремо потребує **компактний наративний layer** — короткий, структурований
"що зараз думає система про XAU/USD" — для:

- Архі (T4: cross-agent context при поверненні з hibernation)
- UI dashboards (HUD-overlay з останньою тезою)
- Future analytics (timeline review of what was said)

Поточний `/api/v3/narrative/snapshot` — це SMC Engine narrative (специфічно про
TDA cascade verdict), не "людська теза мовою трейдера". Потрібен другий шар —
**published mentor thesis** як окрема стрічка.

---

## 2. Розглянуті альтернативи

### A. Move execution into v3 (cowork as supervisor process)

Перенести Claude Desktop логіку у Python orchestrator (`cowork/orchestrator.py`),
запустити як supervisor task на VPS.

**За**: повний контроль, можна reuse trader-v3 patterns (Anthropic SDK, debounce, ...).

**Проти**:

- Власник явно сказав: «це й не код, це Claude Desktop task». Існуюча інстанція працює, її переписувати — incremental work з нульовим business value
- Подвійна логіка (Claude Desktop + Python) під час cutover = ризик дублів у каналі
- Anthropic API key + Telegram bot token треба тоді тримати на VPS (наразі в Claude Desktop env, ізольовані від платформи)
- Збільшує attack surface VPS

**Відхилено** як over-engineering.

### B. Memory всередині Claude Desktop (file system tool)

Дозволити Claude Desktop писати у локальний файл через filesystem tool, читати при наступному запуску.

**За**: zero v3 changes, pure Claude Desktop scope.

**Проти**:

- Claude Desktop file system tool працює тільки на хост-машині (десктоп Стаса), не на VPS — нема high availability
- Файл невидимий для платформи → немає shared narrative для Архі / UI
- Нема retention / schema validation / тестування
- Ламається при ребуті десктопа

**Відхилено**: не покриває dual-purpose потребу (system narrative).

### C. Hybrid — execution в Claude Desktop, state у v3 (вибрано)

Claude Desktop task залишається orchestrator-ом, але:

- Перед scan: `GET /api/v3/cowork/recent_thesis?symbol=XAU/USD&limit=3` → отримує prior theses як PRIOR CONTEXT
- Після publish: `POST /api/v3/cowork/published` з payload → v3 append-ить у JSONL
- v3 володіє schema, retention, контрактом, тестами
- Той самий JSONL обслуговує і cowork T1 (PRIOR CONTEXT), і system narrative (через окремий read endpoint для Архі/UI)

**За**: мінімальна зміна Claude Desktop (2 нових HTTP виклики), весь state під дисципліною v3, dual-purpose schema (один write — два consumers), reuse ADR-0058 token auth.

**Проти**: cowork тепер критично залежить від v3 endpoint доступності; нема T1 якщо API down.

→ Mitigation: graceful degrade у Claude Desktop task — якщо `recent_thesis` 5xx,
працює без PRIOR CONTEXT (коментує це у пості). Це degraded-but-loud (I5).

**Вибрано** як best fit per власник constraint + дисципліна v3.

---

## 3. Рішення

### 3.1 Subsystem boundary

Створити `cowork/` як top-level subsystem у v3 monorepo:

- НЕ `.gitignore`-d (на відміну від `trader-v3/` — cowork-схема є частиною platform contract, бо `published_thesis.jsonl` стає system narrative)
- Окремий ADR-каталог `cowork/docs/adr/` (ця ADR = 001)
- Власні tests `cowork/tests/`
- Власна `cowork/data/` (gitignored)

### 3.2 Module layout

```
cowork/
├── README.md                      # entry point + tier roadmap
├── memory/
│   ├── __init__.py
│   ├── schema.py                  # PublishedThesis dataclass (dual-purpose)
│   └── store.py                   # append/read/retention helpers (pure JSONL)
├── data/
│   ├── .gitkeep
│   └── published_thesis.jsonl     # gitignored
├── tests/
│   ├── __init__.py
│   └── test_store_smoke.py
└── docs/
    └── adr/
        ├── index.md
        └── ADR-001-cowork-memory-architecture.md  # цей файл
```

**Endpoints** монтуються у `runtime/api_v3/cowork.py` (окремий slice cowork.001),
що імпортує `cowork.memory.store`. Dependency direction:
`runtime/api_v3 → cowork/memory` (allowed, ідентично `runtime/smc → core/smc`).

### 3.3 Cross-repo invariants (CW1–CW6)

| ID | Назва | Опис |
|---|---|---|
| **CW1** | Single SSOT prompt | Prompt живе у `docs/runbooks/cowork_prompt_template_v3.md`. cowork/ читає, не дублює |
| **CW2** | Pure memory layer | `cowork/memory/` без HTTP, без Anthropic SDK, без Telegram. Тільки JSONL + dataclass |
| **CW3** | Dual-purpose schema | `PublishedThesis` обслуговує і cowork T1, і system narrative. Один запис, два consumers |
| **CW4** | Append-only | `published_thesis.jsonl` ніколи не редагується in-place. Виправлення = новий запис з `corrects=<scan_id>` |
| **CW5** | Cross-repo isolation | НЕ імпортувати з `trader-v3/`. НЕ читати/писати у `trader-v3/data/` (X31-style) |
| **CW6** | Idempotent publish | `POST /cowork/published` дедуплікує по `scan_id` (повтор = 200 + `duplicate=true` у envelope) |

Інваріанти I0–I7 платформи + S0–S6 SMC + ADR-0058 auth diciplines продовжують діяти.

### 3.4 Schema — `PublishedThesis` (dual-purpose)

Один запис обслуговує два consumers:

- **Cowork T1 (PRIOR CONTEXT)**: щоб наступний scan знав що публікував 1-6 годин тому
- **System narrative**: щоб Архі / UI / dashboards мали короткий human-readable дайджест

Поля поділяються на 3 групи:

#### Group A — Identity & provenance (обов'язкові)

| Поле | Тип | Призначення |
|---|---|---|
| `ts` | ISO8601 string (UTC) | Час публікації |
| `scan_id` | string | Унікальний id (cowork генерує: `scan-YYYYMMDD-HHMMSS-<symbol>`) |
| `symbol` | string | "XAU/USD" |
| `model` | string | "claude-opus-4-7" / "claude-sonnet-4-6" |
| `prompt_version` | string | "v3.0" (з cowork_prompt_template_v3.md header) |

#### Group B — System narrative core (для всіх consumers)

| Поле | Тип | Призначення |
|---|---|---|
| `current_price` | float | Ціна на момент scan |
| `tldr` | string (≤200 chars) | 1-речення компактна теза |
| `preferred_scenario_id` | "A" \| "B" \| "C" | Який сценарій основний |
| `preferred_direction` | "bullish" \| "bearish" \| "range" | Напрямок основного сценарію |
| `preferred_probability` | int (0-100) | Ймовірність основного |
| `thesis_grade` | "A+" \| "A" \| "B" \| "C" | Загальна якість edge |
| `market_phase` | string | "ranging" \| "trending_up" \| "trending_down" \| "transition" |
| `session` | string | "asia" \| "london" \| "newyork" \| "off-session" |
| `in_killzone` | bool | Чи зараз killzone |
| `watch_levels` | list[float] | 2-5 ключових рівнів моніторингу |
| `scenarios_summary` | list[{id, label, probability}] | Усі сценарії компактно |

#### Group C — Cowork-specific (для T1+ tier work)

| Поле | Тип | Призначення |
|---|---|---|
| `telegram_msg_id` | int \| null | ID опублікованого посту (для self-eval T2) |
| `prompt_hash` | string | SHA256[:8] від rendered prompt (для prompt drift detection) |
| `prior_context_used` | bool | Чи використовував T1 PRIOR CONTEXT |
| `corrects` | string \| null | Якщо це correction — `scan_id` що його перекриває (CW4) |

#### Optional Group D — Self-eval (T2 future)

Зарезервовано: `eval_at_h1`, `eval_at_h4`, `outcome`, `error_pattern`. Не пишеться в T1.

### 3.5 Endpoints (slice cowork.001, окремий PATCH)

Монтуються у `runtime/api_v3/endpoints.py:register_routes()`:

```
GET  /api/v3/cowork/recent_thesis?symbol=XAU/USD&limit=3&max_age_h=12
POST /api/v3/cowork/published                  # body = PublishedThesis JSON
```

Auth: token via `X-API-Key` (ADR-0058 token gate, той самий audit middleware).
Rate limit: GET — 60/min, POST — 6/min.
Envelope: ADR-0058 v3.x з `data.theses[]` (GET) або `data.scan_id, duplicate` (POST).

### 3.6 Retention

- `published_thesis.jsonl` ротується щомісячно: `published_thesis-YYYYMM.jsonl`
- Стара ротація — 12 місяців, потім архів у `data/_archive/` (manual grooming)
- Read endpoint фільтрує по `max_age_h` (default 12) щоб не повертати застаріле

### 3.7 Migration cutover

Phase 0 (this PATCH): scaffold + ADR + tests — НЕ змінює production.

Phase 1 (slice cowork.001): endpoints + storage live, але cowork prompt ще не питає
PRIOR CONTEXT. Існуюча Claude Desktop task НЕ змінюється.

Phase 2 (slice cowork.002): prompt patches (R9 prior context awareness, AP10 silent
contradiction, checklist #13). Claude Desktop task оновлюється: 2 нових HTTP виклики.

Phase 3+: T2 self-eval, T3 lessons, T4 cross-agent share.

Існуючий cowork bot **продовжує працювати без перебоїв** на всіх фазах. Cutover —
тільки конфіг-зміна у Claude Desktop task (не код).

---

## 4. Наслідки

### Позитивні

- ✅ Cross-call consistency (вирішує дефект #1 ext review)
- ✅ Silent flip detection (вирішує дефект #2)
- ✅ System narrative SSOT для Архі / UI / dashboards (закриває окрему потребу)
- ✅ Reuse ADR-0058 auth + audit infrastructure (zero new attack surface)
- ✅ Schema дисципліна (dataclass + JSONL + tests)
- ✅ Окремий ADR-каталог не засмічує platform каталог

### Негативні

- ⚠️ Cowork тепер залежить від v3 API uptime для T1 функціональності (mitigation: graceful degrade у Claude Desktop task — якщо 5xx, scan продовжується без PRIOR CONTEXT)
- ⚠️ Schema evolution потребуватиме version bump у `published_thesis.jsonl` (planned: `_schema_version` field уже є у Group A через `prompt_version`)
- ⚠️ JSONL append → single-file growth; ротація щомісячно мітигує

### Ризики

| Ризик | Severity | Mitigation |
|---|---|---|
| Schema breaking change ламає Архі при cross-agent share (T4) | M | `prompt_version` як discriminator + старі версії читаються через convertor у `cowork/memory/schema.py` |
| Claude Desktop task пропускає POST → втрата T1 для наступного scan | L | Idempotent retry; ext review додає AP10 для silent flip detection незалежно від storage |
| `published_thesis.jsonl` стає write hotspot | L | JSONL append-only, ~10-20 records/day per symbol — далеко від I/O bound |

### Rollback

1. Зняти `register_cowork_routes()` виклик з `runtime/api_v3/endpoints.py`
2. Claude Desktop task повертає старий prompt без R9/AP10 (з git history)
3. `cowork/data/published_thesis.jsonl` залишається як архів — не видаляється
4. ADR помічається `Status: Rolled back (partial)`

Часовий бюджет rollback: **<5 хв**.

---

## 5. Open questions (track у follow-up ADRs)

1. **Cowork orchestration на VPS** — якщо у майбутньому Claude Desktop виявиться ненадійним, чи переносити execution? → defer до окремого ADR з real-world reliability data
2. **T4 cross-agent — push vs pull** — Архі читає через `/cowork/recent_thesis` (pull) чи cowork pushes у Архі workspace? → defer до ADR-002 (T4 design)
3. **Prompt versioning** — чи треба formal A/B між prompt versions через scan ID prefix? → defer

---

## 6. Implementation slices

| Slice | Зміст | Files | LOC budget |
|---|---|---|---|
| **cowork.000** (this) | ADR + scaffold + schema + store + tests | 8 | ~250 |
| **cowork.001** | Endpoints in `runtime/api_v3/cowork.py` + register hook | 2-3 | ~150 |
| **cowork.002** | Prompt patches (R9, AP10, checklist #13) у `docs/runbooks/cowork_prompt_template_v3.md` | 1 | ~80 |
| **cowork.003** | Claude Desktop task config update (external, not in repo) | 0 | n/a |
| **cowork.004** | T2 self-eval design + ADR-002 | TBD | TBD |

---

## 7. Acceptance criteria (cowork.000 — this slice)

- [x] `cowork/` directory створено
- [x] `cowork/README.md` пояснює boundaries + tier roadmap
- [x] `cowork/docs/adr/index.md` + ADR-001 (Accepted)
- [x] `cowork/memory/schema.py` — `PublishedThesis` dataclass з валідацією
- [x] `cowork/memory/store.py` — `append_thesis()` + `read_recent()` + retention helper
- [x] `cowork/tests/test_store_smoke.py` — мінімум 3 теста (append, read, dedup)
- [x] `cowork/data/.gitkeep` + root `.gitignore` оновлено
- [x] CW1-CW6 інваріанти задокументовані

Verify: `python -m pytest cowork/tests/ -v` → all pass.
