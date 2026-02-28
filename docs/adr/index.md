# ADR Index — Trading Platform v3

> **SSOT**: Цей файл — єдиний каталог усіх Architecture Decision Records.
> Оновлюється при кожному новому/зміненому ADR.

---

## Реєстр ADR

| # | Назва | Статус | Дата | Ключові слова | Initiative |
|---|-------|--------|------|---------------|------------|
| [0001](0001-unified-data-store.md) | UnifiedDataStore | **Active** | 2026-02-09 | UDS, RAM/Redis/Disk, window, updates, watermark, I1, I3, I4 | `uds_v1` |
| [0002](0002-derive-chain-from-m1.md) | DeriveChain M1→H4 | **Completed** | 2026-02-18 | cascade, M1→M3→…→H4, derive.py, I0, I1 | `derive_chain_m1` |
| [0003](0003-cold-start-hardening.md) | Cold Start Hardening | **Implemented** | 2026-02-19 | bootstrap, supervisor, prime_ready, I5 | `cold_start_hardening` |
| [0004](0004-log-format-and-throttles.md) | Log Format & Throttles | **Implemented** | 2026-02-22 | aione_top, log parse, throttle, I5 | — |
| [0005](0005-mid-session-gap-tolerance.md) | Mid-session Gap Tolerance | **Accepted** | 2026-02-22 | illiquid, NGAS, HKG33, gap tolerance, I3, I5 | — |
| [0006](0006-aione-top-data-sources.md) | aione_top Data Sources | **Implemented** | 2026-02-22 | monitoring, TUI, data sources, read-only | — |
| [0007](0007-drawing-tools-unblock.md) | Drawing Tools Unblock | **Implemented** | 2026-02-23 | DrawingsRenderer, toolbar, click model, client-only | `drawing_tools_v1` |
| [0008](0008-glass-toolbar-light-theme.md) | Glass Toolbar + Light Theme | **Done** | 2026-02-24 | WCAG, CSS vars, glassmorphism, I5 | `drawing_tools_v1` |
| [0009](0009-drawing-sync-render-fix.md) | Drawing Sync Render Fix | **Done** | 2026-02-24 | Y-lag, draft freeze, rAF, priceToCoordinate | `drawing_tools_v1` |
| [0010](0010-thread-safe-ram-layer.md) | Thread-safe RAM Layer | **Done** | 2026-02-24 | threading.Lock, RamLayer, data race, I5 | `concurrency_hardening` |
| [0011](0011-ssot-broadcast-serialization.md) | SSOT Broadcast Serialization | **Implemented** | 2026-02-24 | ws_server, aiohttp, broadcast, wait_for, latency | `ws_performance_v1` |
| [0012](../system_spec/MODE%3DDISCOVERY%20D1.md) | D1 TradingView Parity | **Implemented** | 2026-02-24 | D1, flat filter, tick relay, forming candle, HUD, I7, I8, I9 | `d1_tv_parity` |
| [0013](0013-d1-chart-rendering-fix.md) | D1 Chart Rendering Fix | **Implemented** | 2026-02-25 | D1, LWC, time mapping, YYYY-MM-DD, epoch seconds | `d1_tv_parity` |
| [0014](0014-uds-split-brain-resilience-draft.md) | UDS Split-Brain Resilience | **Proposed** | 2026-02-26 | split-brain, UDS, Redis, watermark | `concurrency_hardening` |
| [0015](0015-calendar-pause-flat-bars-draft.md) | Calendar Pause/Flat Bar Interpretation | **Proposed** | 2026-02-26 | calendar_pause, complete=True, derive, M5 | `derive_chain_m1` |
| [0016](0016-python-version-broker-isolation.md) | Python Version Upgrade + Broker Subprocess Isolation | **Proposed** | 2026-02-26 | Python 3.7, 3.11, forexconnect, subprocess, venv | `platform_modernization` |
| [0017](0017-replay-mode-offline-demo.md) | Replay-Mode з data_v3/ для Offline Demo | **Proposed** | 2026-02-26 | replay, offline, demo, data_v3, CI | `offline_demo` |
| [0018](0018-slo-observability-prometheus.md) | SLO Observability + Prometheus Integration | **Proposed** | 2026-02-26 | SLO, latency, prometheus, metrics, p95 | `observability_v1` |
| [0019](0019-code-review-quick-fixes.md) | Code Review Quick-Fixes Batch (#1,#2,#4,#5,#8,#9) | **Implemented** | 2026-02-26 | bisect, warmup lock, LRU FD, stop_event, max_workers | `code_review_hardening` |
| [0020](0020-candlebar-extensions-immutability.md) | CandleBar Extensions Immutability | **Proposed** | 2026-02-26 | CandleBar, frozen, Dict, MappingProxyType | `code_review_hardening` |
| [0021](0021-jsonl-appender-thread-safety.md) | JsonlAppender Thread-Safety | **Proposed** | 2026-02-26 | JsonlAppender, threading.Lock, defense-in-depth | `code_review_hardening` |
| [0022](0022-ws-audit-operational-docs.md) | WS Audit: Operational Docs (#10–#14) | **Proposed** | 2026-02-26 | has_range, TF mapping, prometheus, rate-limit, shutdown | `code_review_hardening` |
| [0023](0023-d1-live-derive-from-m1.md) | D1 Live Derive from M1 | **Implemented** | 2026-02-27 | D1, 86400, derive chain, M1→D1, anchor 79200, ADR-0002 extension | `d1_derive_from_m1` |
| [0024](0024-smc-engine.md) | SMC Engine — Smart Money Concepts | **Proposed** | 2026-02-28 | SMC, OB, FVG, BOS, CHoCH, liquidity, core/smc, replay, mentor | `smc_engine_v1` |

---

## Міграція нумерації

Попередня нумерація мала колізію: два різні ADR використовували номер `0004`.

| Старий файл | Новий номер | Причина |
|-------------|-------------|---------|
| `ADR-0004-log-format-and-throttles.md` | **0004** | Залишено (перший за датою) |
| `ADR-0004-mid-session-gap-tolerance.md` | **0005** | Перенумеровано (другий за датою) |
| `ADR-0005-aione-top-monitoring.md` | **0006** | Зсув +1 |
| `ADR-0006-Unblocking DrawingsRenderer.md` | **0007** | Зсув +1 |
| `ADR-0007-Glass-Toolbar-Light-Theme.md` | **0008** | Зсув +1 |
| `ADR-0008-Drawing-Sync-Render-Fix.md` | **0009** | Зсув +1 |
| `ADR-0009-Thread-Safe-RAM-Layer.md` | **0010** | Зсув +1 |

Оригінальні файли у `docs/` збережені як архів. Канонічна копія — у `docs/adr/`.

---

## Статуси ADR

| Статус | Значення |
|--------|----------|
| **Proposed** | Запропоновано, ще не прийнято |
| **Accepted** | Прийнято, очікує реалізації |
| **Implemented** | Реалізовано та верифіковано |
| **Done** | Завершено (= Implemented для малих ADR) |
| **Active** | Фундаментальний, постійно еволюціонує |
| **Completed** | Усі фази завершено |
| **Deprecated** | Замінено іншим ADR |

---

## Шаблон нового ADR

Див. формат у [copilot-instructions.md](../../.github/copilot-instructions.md) → Тема B, Правило B2.

Наступний номер: **0025**
