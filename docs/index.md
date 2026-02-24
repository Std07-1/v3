# Документація Trading Platform v3 — Індекс (SSOT)

> **Остання перевірка**: 2026-02-24  
> **Мова**: українська (англійська лише для загальноприйнятих термінів)

Цей файл — **точка входу** в усю документацію проєкту. Будь-яке знання про систему має бути знайдене через цей індекс.

---

## Навігація

### 1. Архітектура (high-level)

| Документ | Зміст |
|---|---|
| [system_current_overview.md](system_current_overview.md) | Поточна архітектура, процеси, SSOT-площини, Mermaid-схеми, annotated tree |
| **[docs/adr/index.md](adr/index.md)** | **Індекс усіх ADR (SSOT)** — 10 ADR з обґрунтуваннями архітектурних рішень |
| [ADR-0001](adr/0001-unified-data-store.md) | UDS (RAM↔Redis↔Disk) + Contract-first API |
| [ADR-0002](adr/0002-derive-chain-from-m1.md) | DeriveChain: M1→M3→M5→M15→M30→H1→H4 |
| [ADR-0003](adr/0003-cold-start-hardening.md) | Cold start: error isolation, process restart, unified gate |
| [ADR-0004](adr/0004-log-format-and-throttles.md) | Формат лог-рядків, throttle UDS |
| [ADR-0005](adr/0005-mid-session-gap-tolerance.md) | Mid-session Gap Tolerance (illiquid instruments) |
| [ADR-0006](adr/0006-aione-top-data-sources.md) | aione_top: контракт джерел даних |
| [ADR-0007](adr/0007-drawing-tools-unblock.md) | DrawingsRenderer: 4 інструменти, click-click UX |
| [ADR-0008](adr/0008-glass-toolbar-light-theme.md) | Glass toolbar, WCAG AA, CSS custom properties |
| [ADR-0009](adr/0009-drawing-sync-render-fix.md) | Y-axis sync render fix + draft freeze clamp |
| [ADR-0010](adr/0010-thread-safe-ram-layer.md) | Thread-safety in ram_layer via threading.Lock |

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

### 5.1. UI v4 (Svelte 5 — next-gen frontend, chart parity DONE)

| Документ | Зміст |
|---|---|
| [ui_v4_integration.md](ui_v4_integration.md) | Інтеграційний гайд: стан реалізації, WS-протокол, GAP аналіз, план підключення |
| [ui_v4/README_DEV.md](../ui_v4/README_DEV.md) | Developer quick start, стек, env-змінні |
| [ui_v4/UI_v4_COPILOT_README.md](../ui_v4/UI_v4_COPILOT_README.md) | SSOT інструкція для побудови (slices 0–5) |
| [ui_v4/src/types.ts](../ui_v4/src/types.ts) | SSOT типи: RenderFrame, WsAction, Candle, SmcData, Drawing |
| [system_spec/UI_v4_DISCOVERY_AUDIT_rev2.md](system_spec/UI_v4_DISCOVERY_AUDIT_rev2.md) | UI v4 аудит: T1-T10 ALL COMPLETE, chart parity DONE |

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
- **B**: UI — read-only renderer:
  - **ui_chart_v3**: HTTP polling API (порт 8089, vanilla JS, поточний production)
  - **ui_v4**: WebSocket real-time (порт 8000, Svelte 5 + LWC 5, chart parity DONE, audit T1-T10 COMPLETE) → [ui_v4_integration.md](ui_v4_integration.md)
- **TUI**: aione_top — standalone TUI-монітор процесів/pipeline (`python -m aione_top`)

## Ключові інваріанти (коротко)

| ID | Інваріант |
|---|---|
| I0 | **Dependency Rule**: `core/` не імпортує `runtime/ui/tools`; `runtime/` не імпортує `tools/`; `ui/` не імпортує домен напряму |
| I1 | **UDS як вузька талія**: всі writes через UDS; UI read-only |
| I2 | **Геометрія часу (dual convention)**: canonical = epoch_ms int; CandleBar/SSOT/API = end-excl (`close_time_ms = open + tf_s*1000`); Redis ALL = end-incl (`close_ms = open + tf_s*1000 - 1`). Конвертація на межі Redis write |
| I3 | **Final > Preview (NoMix)**: complete=true завжди перемагає complete=false |
| I4 | **Один update-потік для UI**: /api/updates (upsert events) |
| I5 | **Degraded-but-loud**: будь-який fallback → warnings[]/degraded[], silent fallback заборонено |
| I6 | **Disk hot-path ban**: disk не hot-path; лише bootstrap/warmup/scrollback/recovery. Scrollback: disk_policy=explicit, max_steps=6, cooldown 0.5s |

---

## Швидкі посилання

- **Конфіг SSOT**: `config.json` (довідник: [config_reference.md](config_reference.md))
- **Запуск**: `python -m app.main --mode all --stdio pipe`
- **UI v3**: `http://127.0.0.1:8089/`
- **UI v4**: `http://127.0.0.1:8000/` (WS real-time, config-gated)
- **Статус**: `http://127.0.0.1:8089/api/status`
- **Exit Gates**: `python -m tools.run_exit_gates --manifest tools/exit_gates/manifest.json`
- **Журнал змін**: `changelog.jsonl` (індекс: `CHANGELOG.md`)
