# ADR-002: Cowork Cadence Runner + Event Watcher + Event-Flag Endpoint

- **Status**: Accepted (2026-05-07)
- **Date**: 2026-05-07
- **Author**: Стас + GitHub Copilot (R_PATCH_MASTER)
- **Initiative**: `cowork_subsystem_v1`
- **Related ADRs**: [ADR-001](ADR-001-cowork-memory-architecture.md) (foundational), [ADR-0058](../../../docs/adr/0058-public-readonly-api-auth.md) (token auth)
- **Supersedes**: nothing (extends ADR-001 §6 slice plan with the actual shape that emerged)

---

## Quality Axes

- **Ambition target**: R3 — codifies a hybrid cadence model (scheduled slots + event-flag fallback) with three coupled production components (pure runner, daemon, HTTP read-back) and explicit cross-host dataflow
- **Maturity impact**: M3 → M4 — adds a typed cadence SSOT (`SCHEDULED_SLOTS_UTC`), an idempotent watcher with cold-start safety, and a degraded-but-loud `event_flag` read endpoint with `triggers_configured` honesty bit

---

## 1. Контекст

ADR-001 §6 описав заплановану розбивку на slices `cowork.000…003+`, де `.001` =
endpoints, `.002` = prompt patches, `.003` = Claude Desktop config update,
`.004+` = T2 self-eval. Під час реалізації виявились два операційних gap'и
([cowork_prompt_template_v3.md §"Known operational gaps"](../../../docs/runbooks/cowork_prompt_template_v3.md)):

| Gap | Симптом | Причина |
|---|---|---|
| **G2** | bot прокидається тільки за scheduled cadence; пропускає TDA сигнали або bias-flip між слотами | scheduled task без секундарного тригера |
| **G3** | визначення слотів живе тільки у prompt template — LLM сам вирішує бігти чи ні; нема runtime SSOT, тестів, observability | "політика у промпті" не є code-side контрактом |

Закрити їх правкою prompt'а **неможливо** — потрібен runtime код:

1. **Pure cadence guard** який повертає рішення "run | off_slot_skip | event_wake" для будь-якого `(now_utc, event_flag_state)`
2. **Daemon** який моніторить TDA signal journal + bias snapshot і пише `event_flag.json` коли щось змінюється
3. **HTTP endpoint** щоб scheduled task **на десктопі Стаса** міг прочитати `event_flag.json` що живе **на VPS** (cross-host: VPS = source of truth, desktop bot = consumer)

Ці три частини утворюють єдиний контракт. Один окремий ADR краще ніж три розрізнені — це slice-family `cowork.003 + cowork.004 + cowork.005` з спільною інваріантною поверхнею.

---

## 2. Розглянуті альтернативи

### A. "Політика тільки в промпті" (status-quo з ADR-001)

LLM сам вирішує бігти чи ні на базі STEP 0a секції prompt'а.

**Проти**:

- Жодних тестів — кожна зміна слотів вимагає manual A/B
- Нема structured `off_slot_skip` log → observability blind spot
- LLM палить тривіальні tokens на "сьогодні не мій слот"
- Event triggers (G2) **взагалі неможливі** без runtime daemon

→ **Відхилено**: prompt не може бути SSOT для runtime cadence.

### B. Cron на VPS який запускає scheduled task

Перенести scheduling на VPS cron + Python wrapper що викликає Anthropic.

**За**: канонічна архітектура.
**Проти**: ADR-001 alt A вже відхилено (Claude Desktop env, ключі, cutover ризик). Не повторюємо.

→ **Відхилено** як re-litigation.

### C. Hybrid: pure cadence library + watcher daemon + HTTP read-back (вибрано)

Три компоненти зі строгими межами:

| Компонент | Локація | Виконання | I/O |
|---|---|---|---|
| `cowork.runner` | `cowork/runner.py` | імпортується bot'ом локально + endpoint'ом | файл `event_flag.json` (опціонально) |
| `cowork.event_watcher` | `tools/cowork/event_watcher.py` | supervisor daemon на VPS | TDA signal JSONL read + `/api/v3/bias/latest` GET + `event_flag.json` write |
| `GET /api/v3/cowork/event_flag` | `runtime/api_v3/cowork.py` | aiohttp handler у ws_server | читає `event_flag.json` з VPS-шляху |

**Дані течуть**:

```
TDA journal (data_v3/_signals/*.jsonl)  ─┐
                                          ├─► event_watcher (poll 30s)
GET /api/v3/bias/latest ──────────────────┘        │
                                                   ▼
                                  /opt/smc-cowork/triggers/event_flag.json
                                                   │
                                                   ▼
                                  GET /api/v3/cowork/event_flag (aiohttp)
                                                   │
                                                   ▼
                                  Claude Desktop bot ─► cowork.runner.should_run_now()
                                                   │
                                                   ▼
                                  decision: run | event_wake | off_slot_skip | event_stale_skip
```

**За**:

- Pure runner тестується без HTTP/Redis (29+4 unit tests)
- Watcher має cold-start guard (перший poll seed'ить state без firing) — нема spurious wake-ups при рестарті
- HTTP endpoint повертає `triggers_configured` bit → degraded-but-loud коли env var не виставлено
- Same evaluator (`evaluate_event_flag_payload`) використовують і file-side runner, і HTTP-side endpoint → один SSOT, нема semantic drift між "що бачить бот локально" і "що повертає API"

**Проти**:

- Cross-host залежність (desktop ← VPS API) — bot повинен мати network access
- Watcher token має `read` scope (для bias read) — ще один секрет в supervisor env

→ **Mitigation**: degraded fallback у bot — якщо HTTP 5xx, працює з `event_flag = "absent"` (так само як без watcher).

→ **Вибрано**.

---

## 3. Рішення

### 3.1 Component contracts

#### 3.1.1 `cowork.runner` (slice cowork.003)

Pure-ish module (єдиний дозволений I/O — read `event_flag.json` для legacy file path API).

**Public API**:

```python
SCHEDULED_SLOTS_UTC: dict[str, list[tuple[int, int]]]  # SSOT (CR1)
TOLERANCE_MIN: int = 15
EVENT_FLAG_TTL_MIN: int = 30

def should_run_now(
    now_utc: datetime | None = None,
    event_flag_path: Path | None = None,
    *,
    event_flag_payload: dict | None = None,  # cowork.005 addition
) -> CadenceDecision: ...

def evaluate_event_flag_payload(
    payload: dict | None,
    now_utc: datetime,
) -> tuple[str, str | None, int | None]:
    """Pure evaluator: returns (state, trigger, age_min).
    state ∈ {"absent","invalid","stale","present"}.
    Used by both file-side and HTTP-side consumers (single SSOT).
    """
```

**Інваріанти (CR1–CR4 з docstring)**:

- **CR1**: slot table = single runtime SSOT; mirrored only у prompt template STEP 0a (doc layer)
- **CR2**: `should_run_now()` ніколи не raise для malformed flag → `event_flag = "invalid"` ≡ absent
- **CR3**: обидва DST варіанти приймаються; closer-by-minute виграє на DST transition days
- **CR4**: return = JSON-serializable dict без `datetime` об'єктів

**Decision matrix** (visible у `CadenceDecision.action`):

| Slot match? | Event flag | Action | Note |
|---|---|---|---|
| ✅ within ±15min | irrelevant | `run` | scheduled scan |
| ❌ off-slot | `present` (≤30 min, valid trigger) | `event_wake` | secondary trigger fired |
| ❌ off-slot | `stale` / `invalid` / `absent` | `off_slot_skip` | LLM tokens not consumed |

#### 3.1.2 `tools.cowork.event_watcher` (slice cowork.004)

Long-running daemon (supervisor program `cowork_event_watcher`).

**Triggers**:

| Trigger | Detection | Reset |
|---|---|---|
| `tda_signal` | new `wall_ms` for watched symbol in TDA signal journal events (allowlist: `signal_emitted`, `trade_entered`, `trade_exited`, `scenario_invalidated`) | seen_set per `(symbol, wall_ms)` |
| `bias_flip` | sha1 of multi-TF bias map changes between polls | last_hash per symbol |

**State persistence**: `<triggers_dir>/.watcher_state.json` (atomic `.tmp` + `os.replace`).

**Cold-start policy**: first poll seeds state без firing. Тільки subsequent changes
дають event flag write. Це інваріант **EW1** — restart не повинен генерувати spurious wake-up.

**Atomic write** (інваріант **EW2**): `event_flag.json` пишеться як `event_flag.json.tmp` + `os.replace()`. Reader (runner.py / endpoint) ніколи не читає half-written файл (CR2).

**Environment** (всі optional крім token):

| Var | Default | Призначення |
|---|---|---|
| `COWORK_TRIGGERS_DIR` | `/opt/smc-cowork/triggers` | де писати `event_flag.json` |
| `COWORK_SIGNALS_DIR` | `data_v3/_signals` | TDA signal journal root |
| `COWORK_EVENT_WATCHER_API_BASE` | `http://127.0.0.1:8000` | base для bias polling |
| `COWORK_EVENT_WATCHER_TOKEN` | required | `read`-scope token |
| `COWORK_EVENT_WATCHER_INTERVAL_S` | `30` | poll period |
| `COWORK_EVENT_WATCHER_SYMBOL` | `XAU/USD` | watched symbol |

#### 3.1.3 `GET /api/v3/cowork/event_flag` (slice cowork.005)

Token-gated HTTP read-back so the desktop bot reads VPS state без shared filesystem.

**Auth**: `X-API-Key` (ADR-0058), scope = `read`.

**Response envelope** (kind = `cowork_event_flag`):

```json
{
  "schema_version": "v3.0",
  "kind": "cowork_event_flag",
  "server_ts": "2026-05-07T22:36:59Z",
  "data": {
    "state": "absent | present | stale | invalid",
    "trigger": "tda_signal | bias_flip | null",
    "age_min": 0..30 | null,
    "ts": "<original event ts>" | null,
    "now_utc": "2026-05-07T22:36:59Z",
    "triggers_configured": true | false
  }
}
```

**Інваріант EF1 (degraded-but-loud)**: коли `APP_COWORK_TRIGGERS_DIR` не виставлено
у app, endpoint повертає `state="absent"`, `triggers_configured=false`. Споживач
бачить що watcher НЕ розгорнутий, не плутає це зі станом "був, але видохся".

**Інваріант EF2 (single evaluator)**: handler НЕ містить копії `evaluate_event_flag_payload` — імпортує з `cowork.runner`. Гарантує що file-side і HTTP-side decision паритетні.

### 3.2 Cross-repo + cross-host контракт

| Хост | Що живе | Доступ |
|---|---|---|
| Desktop (Стас) | Claude Desktop scheduled task + `cowork.runner` (через `pip install -e` або інлайн копія) | local Python + HTTPS до VPS |
| VPS `/opt/smc-v3` | `runtime/api_v3/cowork.py` (handler), `cowork/runner.py` (shared evaluator), env `COWORK_TRIGGERS_DIR=/opt/smc-cowork/triggers` | supervisor `smc:smc-ws` |
| VPS `/opt/smc-cowork/triggers` | `event_flag.json`, `.watcher_state.json` | owner `ubuntu`, watcher write + endpoint read |
| VPS supervisor | `cowork_event_watcher` daemon | env `COWORK_EVENT_WATCHER_*` |

**X31-style ізоляція**: `cowork/` НЕ імпортує з `trader-v3/`. `tools/cowork/event_watcher.py`
НЕ імпортує з `trader-v3/`. Watcher читає TDA signal journal через filesystem path,
не через trader-v3 модулі.

### 3.3 Token store extension

`runtime/api_v3/token_store.py` додає scope `cowork_write` до `VALID_SCOPES`
(окрім існуючого `read`). Призначення: дозволити `POST /api/v3/cowork/published`
тільки токенам зі spec scope, щоб leaked read-only token не міг полютити cowork journal.

`GET /api/v3/cowork/event_flag` потребує тільки `read`.

---

## 4. Наслідки

### Позитивні

- ✅ Закриває G2: bot прокидається на ad-hoc TDA сигнали + bias flips між слотами
- ✅ Закриває G3: cadence політика тестована, observable, structured-logged
- ✅ Single evaluator SSOT — runner.py і API endpoint неможливо рознести семантично
- ✅ Cold-start guard у watcher — рестарти не генерують спам wake-ups
- ✅ Degraded-but-loud `triggers_configured` bit — споживач бачить мисконфіг явно
- ✅ Reuse ADR-0058 token auth + audit middleware — zero new attack surface

### Негативні

- ⚠️ Bot тепер критично залежить від HTTPS до VPS API для отримання event_flag → mitigation: на 5xx бот працює з `state="absent"` (так само як без watcher)
- ⚠️ Watcher token (`read` scope) ще один секрет у supervisor env → стандартний secrets rotation
- ⚠️ Cross-host синхронізація state files — ризик clock drift між VPS і desktop → `now_utc` у response envelope дозволяє bot калібруватись

### Ризики

| Ризик | Severity | Mitigation |
|---|---|---|
| Watcher падає → нові події не пишуться | M | supervisor autorestart=true + heartbeat alerting (ADR-0060 Phase 1) |
| Slot table drift між prompt і runner | L | CR1 invariant + smoke test у `cowork/tests/` periodically reads prompt template і compare'ить |
| `event_flag.json` corruption (disk full mid-write) | L | atomic `.tmp` + `os.replace` (EW2); reader degrade до `state="invalid"` (CR2) |
| Cross-host clock skew | L | `EVENT_FLAG_TTL_MIN=30` буфер + envelope `now_utc` для калібрування |

### Rollback

1. `sudo supervisorctl stop cowork_event_watcher` + видалити з conf.d
2. У supervisor env `smc:smc-ws` зняти `COWORK_TRIGGERS_DIR` → endpoint повертатиме `triggers_configured=false`, `state="absent"`
3. Bot прочитає absent → degraded до scheduled-only cadence
4. Rollback runner.py до версії без `event_flag_payload` параметра — необов'язково (backward compatible)

Бюджет rollback: **<5 хв** (один supervisor stop + один env edit + reload).

---

## 5. Implementation slices (actual)

| Slice | Scope | Files | LOC |
|---|---|---|---|
| **cowork.001** | `register_cowork_routes()` + `recent_thesis` GET + `published` POST | `runtime/api_v3/cowork.py`, `cowork/memory/store.py`, `runtime/api_v3/token_store.py` (+`cowork_write`) | ~370 |
| **cowork.002** | Prompt rewrite + operational frame runbook | `docs/runbooks/cowork_prompt_template_v3.md`, `docs/runbooks/cowork_operational_frame_v3.md` | ~1500 (docs) |
| **cowork.003** | Pure cadence runner + 29 unit tests | `cowork/runner.py`, `tests/test_cowork_runner.py` | ~310 + ~310 |
| **cowork.004** | Event watcher daemon + supervisor conf + 24 unit tests | `tools/cowork/event_watcher.py`, `tools/supervisor/cowork_event_watcher.conf`, `tests/test_cowork_event_watcher.py` | ~430 + ~370 |
| **cowork.005** | `evaluate_event_flag_payload` extracted + `GET /event_flag` + 7 endpoint tests + VPS deploy | `cowork/runner.py` (+ pure evaluator), `runtime/api_v3/cowork.py` (+handler), `runtime/api_v3/endpoints.py` (+env wiring), `tests/test_cowork_endpoints.py` | ~150 changed + ~140 tests |

Тестове покриття на момент CUT: **80 passing** (33 runner / 19 endpoints / 24 watcher / 4 store smoke).

VPS deploy: cowork.001-005 live на supervisor `smc:smc-ws` (pid 703616 на момент 2026-05-07T22:35Z), `cowork_event_watcher` daemon RUNNING (pid 702847).

---

## 6. Open questions (track у follow-up)

1. **Heartbeat alerting** для `cowork_event_watcher` — наразі покладаємось на `supervisor autorestart`. Phase 1 ADR-0060 покриє це
2. **Cross-host metrics** — desktop bot не репортить decision distribution (`run` / `event_wake` / `off_slot_skip`) в Prometheus → defer до окремого slice
3. **Multi-symbol watcher** — поточно `COWORK_EVENT_WATCHER_SYMBOL=XAU/USD` (single). Multi-symbol → defer до ADR-0054 multi-symbol re-activation
