# Cowork — Mentor Channel Subsystem

> **Призначення**: persistent memory + state SSOT для cowork-каналу
> (`@aione_smc` SMC mentor digest). Execution лишається у Claude Desktop scheduled
> task; v3 monorepo володіє state, schema, retention.
>
> **Cross-repo boundary** (X31-style): `cowork/` НЕ імпортує з `trader-v3/`.
> `cowork/memory/` — pure (JSONL helpers, schema). `runtime/api_v3/cowork.py`
> — adapter, що монтує endpoints. Жодних shared file paths між cowork та trader-v3.
>
> **Foundational ADR**: [docs/adr/ADR-001-cowork-memory-architecture.md](docs/adr/ADR-001-cowork-memory-architecture.md)
>
> **SSOT prompt** (НЕ копіювати сюди): [`docs/runbooks/cowork_prompt_template_v3.md`](../docs/runbooks/cowork_prompt_template_v3.md)

---

## Що це і чого тут немає

| Є тут | Немає тут (живе деінде) |
|---|---|
| `memory/schema.py` — `PublishedThesis` dataclass (dual-purpose: cowork T1 + system narrative) | Сам Claude Desktop task / orchestrator (не Python код) |
| `memory/store.py` — append/read JSONL + retention | System prompt (живе у `docs/runbooks/cowork_prompt_template_v3.md`) |
| `data/published_thesis.jsonl` (gitignored) | Anthropic API ключ (у Claude Desktop env) |
| `docs/adr/ADR-NNN-*.md` — окремий ADR-каталог | Telegram bot token (у Claude Desktop env) |
| `tests/` — pytest для memory модулів | Mounting endpoints (`runtime/api_v3/cowork.py`, окремий slice) |

---

## Архітектура (1-line)

```
Claude Desktop task ─GET─► /api/v3/cowork/recent_thesis ─┐
        │                                                  │
        ├─► fetch v3 platform endpoints (bars, zones, ...) │
        ├─► load prompt SSOT (docs/runbooks/...)           │
        ├─► call Anthropic (Opus 4.7 / Sonnet 4.6)         │
        ├─► publish Telegram (msg_id capture)              │
        └─POST► /api/v3/cowork/published ─► JSONL append ──┘
                                                  │
                                          cowork/data/published_thesis.jsonl
                                                  │
                                          ┌───────┴───────┐
                                          ▼               ▼
                            cowork PRIOR CONTEXT     System narrative
                            (next scan, T1)          (Архі / UI / dashboards)
```

---

## Memory Tier Roadmap

| Tier | Що | Slice | Status |
|---|---|---|---|
| **T1** | Channel context window — останні N тез як PRIOR CONTEXT для наступного scan | cowork.001 | ✅ Implemented (2026-05-06) |
| **T2** | Self-eval cycle — оцінка попередніх тез через post-hoc price action | TBD | Future |
| **T3** | Lesson journal — зведені error patterns + успішні setups | TBD | Future |
| **T4** | Cross-agent share — Архі читає cowork тези через `/api/v3/cowork/recent_thesis` (НЕ shared files) | TBD | Future |

### Operational layer (ADR-002)

| Slice | Component | Status |
|---|---|---|
| **cowork.001** | `register_cowork_routes()` + `GET /recent_thesis` + `POST /published` | ✅ Deployed |
| **cowork.002** | Prompt rewrite + `cowork_operational_frame_v3.md` runbook | ✅ Done (docs) |
| **cowork.003** | Pure cadence runner (`cowork/runner.py`) + 33 unit tests | ✅ Deployed |
| **cowork.004** | Event watcher daemon (`tools/cowork/event_watcher.py`) + supervisor conf + 24 tests | ✅ Deployed |
| **cowork.005** | `evaluate_event_flag_payload` extract + `GET /api/v3/cowork/event_flag` + 7 endpoint tests | ✅ Deployed (2026-05-07) |

---

## Quick Reference

```bash
# Run tests
python -m pytest cowork/tests/ -v

# Read recent theses (manual inspection)
python -c "from cowork.memory.store import read_recent; print(read_recent('XAU/USD', limit=5))"

# Tail live storage
Get-Content cowork/data/published_thesis.jsonl -Tail 10
```

---

## Інваріанти cowork

| ID | Назва | Опис |
|---|---|---|
| **CW1** | Single SSOT prompt | Prompt живе у `docs/runbooks/cowork_prompt_template_v3.md`. Не дублювати у `cowork/` |
| **CW2** | Pure memory layer | `cowork/memory/` — без HTTP, без Anthropic SDK, без Telegram. Тільки JSONL + schema |
| **CW3** | Dual-purpose schema | `PublishedThesis` обслуговує і cowork T1, і system narrative. Один запис, два споживача |
| **CW4** | Append-only | `published_thesis.jsonl` ніколи не редагується in-place. Виправлення — новий запис з `corrects=<scan_id>` |
| **CW5** | Cross-repo isolation | НЕ імпортувати з `trader-v3/`. НЕ читати/писати у `trader-v3/data/` |
| **CW6** | Idempotent publish | `POST /cowork/published` дедуплікує по `scan_id` (повтор = 200 + `duplicate=true`) |
