# Документація Trading Platform v3 — Індекс (SSOT)

> **Остання перевірка**: 2026-03-24
> **Мова**: українська (англійська лише для загальноприйнятих термінів)

Цей файл — **точка входу** в усю документацію проєкту. Будь-яке знання про систему має бути знайдене через цей індекс.

---

## Навігація

### 1. Архітектура (high-level)

| Документ | Зміст |
|---|---|
| [system_current_overview.md](system_current_overview.md) | Поточна архітектура, процеси, SSOT-площини, Mermaid-схеми, annotated tree |
| **[docs/adr/index.md](adr/index.md)** | **Індекс усіх ADR (SSOT)** — 44+ ADR з обґрунтуваннями архітектурних рішень |
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
| [ADR-0023](adr/0023-d1-live-derive-from-m1.md) | D1 Live Derive from M1 (1440×M1, anchor 79200s) |
| [ADR-0024](adr/0024-smc-engine.md) | **SMC Engine Architecture** — swings, OB, FVG, liquidity, P/D, inducement, lifecycle (Implemented E1+S4+E2+N1/N2/N3) |
| [ADR-0025](adr/0025-potik-b-data-quality-summary.md) | Потік B data quality summary |
| [ADR-0026](adr/0026-overlay-level-rendering-rules.md) | Overlay Level Rendering Rules (L1–L6) |
| [ADR-0027](adr/0027-client-side-replay.md) | Client-Side Replay (TradingView-style) |
| [ADR-0028](adr/0028-v2-elimination-engine.md) | Elimination Engine — Display Filter Pipeline |
| [ADR-0029](adr/0029-confluence-scoring.md) | OB Confluence Scoring + Grade System (8-factor, A+/A/B/C) |
| [ADR-0030-alt](adr/0030-alt-tf-sovereignty.md) | TF Sovereignty — Cross-TF Projection Styling |
| [ADR-0031](adr/0031-bias-banner.md) | Bias Banner — Multi-TF Trend Bias Display |
| [ADR-0032](adr/0032-overlay-render-throttle-tf-switch.md) | Overlay Render Throttle + TF Switch Stability |
| [ADR-0033](adr/0033-context-flow-narrative.md) | Context Flow — Multi-TF Narrative Engine (trade/wait/prepare scenario) |
| [ADR-0034](adr/0034-advanced-market-analysis-tda.md) | Advanced Market Analysis — TDA (P0 IFVG + P1 Breaker Implemented; P2–P6 rolled back) |
| [ADR-0035](adr/0035-sessions-killzones.md) | Sessions & Killzones — Asia/London/NY H/L, killzone context, F9 sweep |
| [ADR-0036](adr/0036-premium-trader-first-shell.md) | Premium Trader-First Shell for UI v4 |
| [ADR-0037](adr/0037-binance-second-broker.md) | Binance Futures — Second Broker (BTC/ETH Live Ingest, ADR-0037) |
| [ADR-0038](adr/0038-initial-backfill-virgin-symbols.md) | Initial Backfill for Virgin Symbols (Bootstrap Phase 2.5, ADR-0038) |
| [ADR-0039](adr/0039-signal-engine.md) | Signal Engine — Numeric Entry/SL/TP + R:R + Alerts (ADR-0039) |
| [ADR-0040](adr/0040-tda-cascade-signal-engine.md) | TDA Cascade — Daily Signal Engine Rebuild (D1→H4→Session→FVG, ADR-0040) |
| [ADR-0041](adr/0041-pd-badge-eq-line.md) | Premium/Discount Badge + EQ Line — Decoupled Calc/Display + Variant H Shell (ADR-0041) |
| [ADR-0042](adr/0042-delta-frame-state-sync.md) | Delta Frame State Synchronization — Full/Delta Parity (DF-1/DF-2/DF-3, ADR-0042) |
| [ADR-0043](adr/0043-ui-v4-canvas-safe-zones-state-sync.md) | UI v4 — Canvas Safe Zones + State Sync Hardening (ADR-0043) |
| [ADR-0044](adr/0044-htf-live-preview.md) | HTF Live Preview — Incremental HTF accumulator for D1/H4 forming candle (ADR-0044) |

### 2. Потоки даних

| Документ | Зміст |
|---|---|
| [system_current_overview.md § Схема потоку даних](system_current_overview.md#схема-потік-даних) | A→C→B: Broker → UDS → UI (Final + Preview + Overlay) |

| [redis_snapshot_design.md](redis_snapshot_design.md) | Redis snapshot keyspace, TTL, failure modes, stale readiness |

### 3. Контракти

| Документ | Зміст |
|---|---|
| [contracts.md](contracts.md) | Реєстр JSON Schema контрактів, правила еволюції, приклади payload, **SMC wire format** |
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

### 5. UI API (WebSocket + HTTP)

| Документ | Зміст |
|---|---|
| [ui_api.md](ui_api.md) | WS протокол `ui_v4_v2`, frames (full/delta/scrollback/config/heartbeat/error), actions, guards, SMC інтеграція |

### 5.1. UI v4 (Svelte 5 — next-gen frontend, chart parity DONE)

| Документ | Зміст |
|---|---|

| [ui_v4/README_DEV.md](../ui_v4/README_DEV.md) | Developer quick start, стек, env-змінні |
| [ui_v4/UI_v4_COPILOT_README.md](../ui_v4/UI_v4_COPILOT_README.md) | SSOT інструкція для побудови (slices 0–5) |
| [ui spec.md](ui%20spec.md) | Premium trader-first UI spec: thesis bar, art direction, signature interactions, before/after |
| [ui_v4/src/types.ts](../ui_v4/src/types.ts) | SSOT типи: RenderFrame, WsAction, Candle, SmcData, Drawing |
| [system_spec/UI_v4_DISCOVERY_AUDIT_rev2.md](system_spec/UI_v4_DISCOVERY_AUDIT_rev2.md) | UI v4 аудит: T1-T10 ALL COMPLETE, chart parity DONE |

### 5.2. SMC Engine (Smart Money Concepts — ADR-0024)

| Документ | Зміст |
|---|---|
| [ADR-0024](adr/0024-smc-engine.md) | Архітектура, P-slices, інваріанти S0–S6, §18 Implementation Progress |
| [ADR-0028](adr/0028-v2-elimination-engine.md) | Display Filter Pipeline (budget, proximity, TTL) |
| [ADR-0029](adr/0029-confluence-scoring.md) | Confluence Scoring: 8-factor grade A+/A/B/C |
| `core/smc/types.py` | SSOT типи: SmcZone, SmcSwing, SmcLevel, SmcSnapshot, SmcDelta |
| `core/smc/engine.py` | SmcEngine — pure orchestrator (zone lifecycle, ranking, caps) |
| `core/smc/confluence.py` | confluence_score() — 8-factor grade (ADR-0029) |
| `core/smc/config.py` | SmcConfig dataclass (params з config.json:smc) |
| `runtime/smc/smc_runner.py` | SmcRunner — in-process під ws_server, warmup + on_bar |
| `ui_v4/src/stores/smcStore.ts` | applySmcFull / applySmcDelta — incremental SMC state |
| `ui_v4/src/chart/overlay/OverlayRenderer.ts` | Canvas rendering: OB/FVG/swings/levels з opacity за strength |
| `ui_v4/src/chart/overlay/DisplayBudget.ts` | Display budget filter (ADR-0028) |
| **[trader_coverage.md](trader_coverage.md)** | **Трейдерські концепти → ADR → Модуль** — карта захисту (що читати перед зміною будь-якого SMC-компонента) |

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
| [audit/coldstart_regressions.md](audit/coldstart_regressions.md) | Матриця регресій: cold start 4 символи |
| [audit/htf_availability_regressions.md](audit/htf_availability_regressions.md) | Матриця регресій: H4/D1 availability |
| [audit/ui_live_candle_regressions.md](audit/ui_live_candle_regressions.md) | Матриця регресій: UI live candle |

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

- **A**: FXCM/Binance History + Tick Stream → writer-процеси (m1_poller, tick_publisher, tick_preview, binance_ingest_worker, binance_tick_publisher)
- **C**: UnifiedDataStore — єдина точка запису/читання marketdata (SSOT disk + Redis snapshots + updates bus)
- **B**: UI — read-only renderer:
  - **ui_v4**: WebSocket + HTTP API (port 8000, Svelte 5 + LWC 5, same-origin) → [README_DEV.md](../ui_v4/README_DEV.md)
  - **SMC Overlay** (ADR-0024): SmcRunner → SmcEngine → WS `smc_snapshot`/`smc_delta` → smcStore → OverlayRenderer (OB/FVG/swings/levels)
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

### SMC інваріанти (S0–S6, ADR-0024)

| ID | Інваріант |
|---|---|
| S0 | `core/smc/` = pure logic, NO I/O |
| S1 | SMC не пише в UDS/SSOT JSONL (read-only overlay) |
| S2 | Deterministic: same bars → same zones |
| S3 | Zone ID deterministic: `{kind}_{symbol}_{tf_s}_{anchor_ms}` |
| S4 | Performance: `on_bar()` < `max_compute_ms` |
| S5 | Config SSOT: all params from `config.json:smc` |
| S6 | Wire format = `core/smc/types.py` → `ui_v4/src/types.ts` |

---

## Швидкі посилання

- **Конфіг SSOT**: `config.json` (довідник: [config_reference.md](config_reference.md))
- **Запуск**: `python -m app.main --mode all --stdio pipe`
- **UI**: `http://127.0.0.1:8000/` (WS real-time)
- **Статус**: `http://127.0.0.1:8000/api/status`
- **Exit Gates**: `python -m tools.run_exit_gates --manifest tools/exit_gates/manifest.json`
