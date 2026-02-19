# Документація Trading Platform v3 — Індекс (SSOT)

> **Остання перевірка**: 2026-02-18  
> **Мова**: українська (англійська лише для загальноприйнятих термінів)

Цей файл — **точка входу** в усю документацію проєкту. Будь-яке знання про систему має бути знайдене через цей індекс.

---

## Навігація

### 1. Архітектура (high-level)

| Документ | Зміст |
|---|---|
| [system_current_overview.md](system_current_overview.md) | Поточна архітектура, процеси, SSOT-площини, Mermaid-схеми, annotated tree |
| [ADR-0001 UnifiedDataStore.md](ADR-0001%20UnifiedDataStore.md) | Архітектурне рішення: UDS (RAM↔Redis↔Disk) + Contract-first API |
| [ADR-0002-derive-chain-from-m1.md](ADR-0002-derive-chain-from-m1.md) | DeriveChain: M1→M3→M5→M15→M30→H1→H4 (4 phases) |

### 2. Потоки даних

| Документ | Зміст |
|---|---|
| [system_current_overview.md § Схема потоку даних](system_current_overview.md#схема-потік-даних) | A→C→B: Broker → UDS → UI (Final + Preview + Overlay) |
| [guide_candles.md](guide_candles.md) | Інструкція: «гарні» M1/M3 свічки від брокера до UI |
| [guide_candle_acquisition.md](guide_candle_acquisition.md) | Повний посібник: отримання свічок (FXCM API, календар, watermark, recover) |
| [redis_snapshot_design.md](redis_snapshot_design.md) | Redis snapshot keyspace, TTL, failure modes, stale readiness |

### 3. Контракти

| Документ | Зміст |
|---|---|
| [contracts.md](contracts.md) | Реєстр JSON Schema контрактів, правила еволюції, приклади payload |
| Схеми (SSOT): | |
| — `core/contracts/public/marketdata_v1/bar_v1.json` | Один OHLCV бар |
| — `core/contracts/public/marketdata_v1/window_v1.json` | Відповідь `/api/bars` |
| — `core/contracts/public/marketdata_v1/updates_v1.json` | Відповідь `/api/updates` |
| — `core/contracts/public/marketdata_v1/tick_v1.json` | Тік (raw tick) |

### 4. Runtime інваріанти / Rails

| Документ | Зміст |
|---|---|
| [system_current_overview.md § Інваріанти](system_current_overview.md#інваріанти) | I0–I6 канон, dependency rule, watermark, final>preview, NoMix |
| [config_reference.md](config_reference.md) | Довідник полів `config.json` (SSOT конфігу) |

### 5. UI API (HTTP)

| Документ | Зміст |
|---|---|
| [ui_api.md](ui_api.md) | Таблиця endpoint-ів, формати, джерела даних, кеш/TTL |

### 6. Runbooks (експлуатація)

| Документ | Зміст |
|---|---|
| [runbooks/coldstart.md](runbooks/coldstart.md) | Cold start: rebuild derived, перевірка після рестарту |
| [runbooks/live_recover.md](runbooks/live_recover.md) | Live Recover: автоматичне наздоганяння M5 після паузи |
| [runbooks/production.md](runbooks/production.md) | Prod: systemd units, порядок старту, health-check, типові інциденти |

### 7. Аудит / Прогрес (P0–P6)

| Документ | Зміст |
|---|---|
| [audit/progress.md](audit/progress.md) | Чекліст P0–P6 з доказами та статусами (DONE/PARTIAL/TODO/UNKNOWN) |
| [audit/coldstart_regressions.md](audit/coldstart_regressions.md) | Матриця регресій: cold start 13 символів |
| [audit/htf_availability_regressions.md](audit/htf_availability_regressions.md) | Матриця регресій: H4/D1 availability |
| [audit/ui_live_candle_regressions.md](audit/ui_live_candle_regressions.md) | Матриця регресій: UI live candle |
| [audit/p0_freeze_snapshot/](audit/p0_freeze_snapshot/) | P0: freeze snapshot (as-is flow, policy table, smoke results) |
| [audit/p1_policy_diff/](audit/p1_policy_diff/) | P1: policy diff table |
| [audit/p2_empty_chart_rootcause/](audit/p2_empty_chart_rootcause/) | P2: порожній графік — root cause analysis |

### 8. Дослідження (research/)

| Документ | Зміст |
|---|---|
| `research/ПОВНИЙ АУДИТ AS-IS + TO-BE ADR + ПЛАН.md` | Історичний аудит + TO-BE ADR + execution plan (P0–P12) |
| `research/author_profile.md` | Профіль автора |
| `research/ui_live_candle_guide.md` | Гайд по live candle UI |

---

## Канон A→C→B

```
A (Broker/Ingest) → C (UDS — єдина талія) → B (UI — read-only renderer)
```

- **A**: FXCM History + Tick Stream → writer-процеси (connector, m1_poller, tick_preview_worker, tick_publisher)
- **C**: UnifiedDataStore — єдина точка запису/читання marketdata (SSOT disk + Redis snapshots + updates bus)
- **B**: UI HTTP API — read-only, без доменної логіки, без прямого доступу до диску/Redis

## Ключові інваріанти (коротко)

| ID | Інваріант |
|---|---|
| I0 | **Dependency Rule**: `core/` не імпортує `runtime/ui/tools`; `runtime/` не імпортує `tools/`; `ui/` не імпортує домен напряму |
| I1 | **UDS як вузька талія**: всі writes через UDS; UI read-only |
| I2 | **Геометрія часу**: canonical = epoch_ms int, end-excl (`close_time_ms = open_time_ms + tf_s*1000`) |
| I3 | **Final > Preview (NoMix)**: complete=true завжди перемагає complete=false |
| I4 | **Один update-потік для UI**: /api/updates (upsert events) |
| I5 | **Degraded-but-loud**: будь-який fallback → warnings[]/degraded[], silent fallback заборонено |
| I6 | **Disk hot-path ban**: disk не hot-path; лише bootstrap/warmup/scrollback/recovery |

---

## Швидкі посилання

- **Конфіг SSOT**: `config.json` (довідник: [config_reference.md](config_reference.md))
- **Запуск**: `python -m app.main --mode all --stdio pipe`
- **UI**: `http://127.0.0.1:8089/`
- **Статус**: `http://127.0.0.1:8089/api/status`
- **Exit Gates**: `python -m tools.run_exit_gates --manifest tools/exit_gates/manifest.json`
- **Журнал змін**: `changelog.jsonl` (індекс: `CHANGELOG.md`)
