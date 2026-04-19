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
| [0005](0005-mid-session-gap-tolerance.md) | Mid-session Gap Tolerance | **Implemented** | 2026-02-22 | illiquid, NGAS, HKG33, gap tolerance, I3, I5 | — |
| [0006](0006-aione-top-data-sources.md) | aione_top Data Sources | **Implemented** | 2026-02-22 | monitoring, TUI, data sources, read-only | — |
| [0007](0007-drawing-tools-unblock.md) | Drawing Tools Unblock | **Implemented** | 2026-02-23 | DrawingsRenderer, toolbar, click model, client-only | `drawing_tools_v1` |
| [0008](0008-glass-toolbar-light-theme.md) | Glass Toolbar + Light Theme | **Done** | 2026-02-24 | WCAG, CSS vars, glassmorphism, I5 | `drawing_tools_v1` |
| [0009](0009-drawing-sync-render-fix.md) | Drawing Sync Render Fix | **Done** | 2026-02-24 | Y-lag, draft freeze, rAF, priceToCoordinate | `drawing_tools_v1` |
| [0010](0010-thread-safe-ram-layer.md) | Thread-safe RAM Layer | **Done** | 2026-02-24 | threading.Lock, RamLayer, data race, I5 | `concurrency_hardening` |
| [0011](0011-ssot-broadcast-serialization.md) | SSOT Broadcast Serialization | **Implemented** | 2026-02-24 | ws_server, aiohttp, broadcast, wait_for, latency | `ws_performance_v1` |
| [0012](../system_spec/MODE%3DDISCOVERY%20D1.md) | D1 TradingView Parity | **Implemented** | 2026-02-24 | D1, flat filter, tick relay, forming candle, HUD, I7, I8, I9 | `d1_tv_parity` |
| [0013](0013-d1-chart-rendering-fix.md) | D1 Chart Rendering Fix | **Implemented** | 2026-02-25 | D1, LWC, time mapping, YYYY-MM-DD, epoch seconds | `d1_tv_parity` |
| [0013b](0013b-complete-semantics-partial-derived.md) | complete=true Semantics for Partial Derived Bars | **Accepted** | 2026-02-25 | partial derived, calendar pause, complete=true, boundary_partial, extensions | `derive_chain_m1` |
| [0014](0014-uds-split-brain-resilience.md) | UDS Split-Brain Resilience | **Implemented** | 2026-02-26 | split-brain, UDS, Redis, watermark | `concurrency_hardening` |
| [0015](0015-calendar-pause-flat-bars.md) | Calendar Pause/Flat Bar Interpretation | **Implemented** | 2026-02-26 | calendar_pause, complete=True, derive, M5 | `derive_chain_m1` |
| [0016](0016-python-version-broker-isolation.md) | Python Version Upgrade + Broker Subprocess Isolation | **Implemented** | 2026-03-08 | dual-venv, broker_sidecar, broker_python, Python 3.14 trampoline, supervisor PID lock | `platform_modernization` |
| [0017](0017-replay-mode-offline-demo.md) | Replay-Mode з data_v3/ для Offline Demo | **Implemented** | 2026-02-28 | replay, offline, demo, data_v3, CI | `offline_demo` |
| [0018](0018-slo-observability-prometheus.md) | SLO Observability + Prometheus Integration | **Proposed** | 2026-02-26 | SLO, latency, prometheus, metrics, p95 | `observability_v1` |
| [0019](0019-code-review-quick-fixes.md) | Code Review Quick-Fixes Batch (#1,#2,#4,#5,#8,#9) | **Implemented** | 2026-02-26 | bisect, warmup lock, LRU FD, stop_event, max_workers | `code_review_hardening` |
| [0020](0020-candlebar-extensions-immutability.md) | CandleBar Extensions Immutability | **Proposed** | 2026-02-26 | CandleBar, frozen, Dict, MappingProxyType | `code_review_hardening` |
| [0021](0021-jsonl-appender-thread-safety.md) | JsonlAppender Thread-Safety | **Accepted** | 2026-02-26 | JsonlAppender, threading.Lock, defense-in-depth | `code_review_hardening` |
| [0022](0022-ws-audit-operational-docs.md) | WS Audit: Operational Docs (#10–#14) | **Implemented** | 2026-02-26 | has_range, TF mapping, prometheus, rate-limit, shutdown | `code_review_hardening` |
| [0023](0023-d1-live-derive-from-m1.md) | D1 Live Derive from M1 | **Implemented** | 2026-02-27 | D1, 86400, derive chain, M1→D1, anchor 79200, ADR-0002 extension | `d1_derive_from_m1` |
| [0024](0024-smc-engine.md) | SMC Engine — Smart Money Concepts | **Implemented** | 2026-02-28 | SMC, OB, FVG, BOS, CHoCH, liquidity, inducement, P/D, core/smc, lifecycle, N1/N2/N3 | `smc_engine_v1` |
| [0024a](0024a-smc-engine-self-audit.md) | SMC Engine Self-Audit & Hardening | **Implemented** | 2026-03-01 | F1-F12, swing wire format, ATR dedup, decay config, trend_bias | `smc_engine_hardening` |
| [0024b](0024b-smc-engine-smc-key-levels.md) | SMC Key Levels — Horizontal Anchors | **Partially Implemented** | 2026-03-01 | PDH/PDL/PWH/PWL, sessions, opens, range, EQH/EQL, budget, key_levels.py, per-kind UI styling | `smc_engine_v1` |
| [0024c](0024c-smc-zone-poi-rendering.md) | SMC Zone POI — Rendering Strategy | **Implemented** | 2026-03-01 | OB, FVG, premium/discount, POI grade, Z1–Z10, zone lifecycle, display filter, Context Stack | `smc_engine_v1` |
| [0025](0025-potik-b-data-quality-summary.md) | Потік B — Data Quality & Activation Summary | **Implemented** | 2026-02-28 | multi-symbol, data quality, derived integrity, XAU/USD focus | `potik_b_data_quality` |
| [0026](0026-overlay-level-rendering-rules.md) | Overlay Level Rendering Rules | **Implemented** | 2026-03-01 | levels, merge, labels, LINE_PX, NOTCH_PX, LEVEL_STYLES, L1–L6 | `smc_engine_v1` |
| [0027](0027-client-side-replay.md) | Client-Side Replay (TradingView-style) | **Implemented** | 2026-02-28 | replay, client-side, scrubber, play/pause, candle-by-candle, TF switch | `replay_v2` |
| [0028-strategy](0028-smc-clean-chart-strategy.md) | SMC Clean Chart Strategy — від Detection Engine до Trading Terminal | **Proposed** | 2026-03-04 | clean chart doctrine, display budget, zone lifecycle, grade threshold, signal/noise | `smc_vis_phi0` |
| [0028](0028-v2-elimination-engine.md) | Elimination Engine — Display Filter Pipeline | **Implemented** | 2026-03-04 | display filter, budget, proximity, TTL, Focus/Research toggle | `smc_vis_phi0` |
| [0029](0029-confluence-scoring.md) | OB Confluence Scoring + Grade System | **Implemented** | 2026-03-05 | confluence, 8 factors, grade A+/A/B/C, badge, DisplayBudget, FVG scoring P5B | `smc_vis_phi1` |
| [0030-alt](0030-alt-tf-sovereignty.md) | TF Sovereignty — Cross-TF Projection Styling | **Implemented** | 2026-03-06 | projection, opacity, dashed, cross-TF, OverlayRenderer, TF sovereignty | `smc_vis_phi1` |
| [0031](0031-bias-banner.md) | Bias Banner — Multi-TF Trend Bias Display | **Implemented** | 2026-03-08 | bias_map, trend_bias, multi-TF, banner, BiasBanner.svelte | `smc_vis_phi2` |
| [0032](0032-overlay-render-throttle-tf-switch.md) | Overlay Render Throttle + TF Switch Stability | **Implemented** | 2026-03-08 | crosshairMove guard, RAF wheel, center_ms viewCache, TF switch UX | `ui_perf_p1_p5` |
| [0033](0033-context-flow-narrative.md) | Context Flow — Multi-TF Narrative Engine | **Implemented** | 2026-03-08 | narrative, scenario, trade/wait, market_phase, FVG context, NarrativePanel | `smc_vis_phi3` |
| [0034](0034-advanced-market-analysis-tda.md) | Advanced Market Analysis — TDA Synchronization & Extended SMC | **Partially Implemented** | 2026-03-09 | TDA, IFVG (P0 ✅), breaker (P1 ✅), DP/Quasimodo/TF sync (P2–P6 ❌ rolled back) | `smc_tda_v1` |
| [0035](0035-sessions-killzones.md) | Sessions & Killzones — Trading Session Awareness | **Implemented** | 2026-03-08 | Asia/London/NY, session H/L, killzone, F9 sweep, narrative session context | `smc_sessions_v1` |
| [0036](0036-premium-trader-first-shell.md) | Premium Trader-First Shell for UI v4 | **Implemented** | 2026-03-09 | premium shell, thesis bar, tactical strip, service rail, signature interaction, UI hierarchy | `ui_v4_premium_shell_v1` |
| [0037](0037-binance-second-broker.md) | Binance Futures — Second Broker (BTC/ETH Live Ingest) | **Implemented** | 2026-03-13 | Binance, BTCUSDT, ETHUSDT, Futures API, 24/7, crypto_24x7, python-binance, anchor=0 | `binance_broker_v1` |
| [0038](0038-initial-backfill-virgin-symbols.md) | Initial Backfill for Virgin Symbols | **Implemented (Amend v2)** | 2026-03-15 | backfill, virgin, bootstrap Phase 2.5, M1 history, historical crawl, derive-only HTF | `cold_start_backfill_v1` |
| [0039](0039-signal-engine.md) | Signal Engine — Numeric Entry/SL/TP + R:R + Alerts | **Implemented** | 2026-03-14 | entry_price, stop_loss, take_profit, R:R, confidence, alerts, signal lifecycle, core/smc/signals.py | `signal_engine_v1` |
| [0040](0040-tda-cascade-signal-engine.md) | TDA Cascade — Daily Signal Engine Rebuild | **Implemented** | 2026-03-18 | TDA, 4-stage cascade, D1→H4→Session→FVG, Config F, partial TP, trailing SL, daily signal | `tda_cascade_v1` |
| [0041](0041-pd-badge-eq-line.md) | Premium/Discount Badge + EQ Line — Decoupled Calc/Display + Variant H Shell Restructure | **Implemented** (P1–P9) | 2026-03-22 | P/D calc/display split, PdBadge HUD, EQ dashed line, D8 coincidence, config granular keys, Variant H shell restructure, directional coloring, amber conflict | `pd_badge_eq_v1` |
| [0042](0042-delta-frame-state-sync.md) | Delta Frame State Synchronization — Full/Delta Parity | **Implemented** | 2026-03-22 | delta frame, state desync, zone_grades, pd_state, bias_map, thick delta, FVG grace, DF-1/DF-2/DF-3 | `delta_frame_parity_v1` |
| [0043](0043-ui-v4-canvas-safe-zones-state-sync.md) | UI v4 — Canvas Safe Zones + State Sync Hardening | **Implemented** | 2026-03-24 | CANVAS_SAFE_TOP_Y, pd_state null-clear, boot_id reset, filterMitigated activation, z-index collision, thick-delta SSOT | `ui_v4_hardening_v1` |
| [0044](0044-htf-live-preview.md) | HTF Live Preview — Incremental HTF Accumulator для D1/H4 forming candle | **Proposed** | 2026-03-24 | HTF preview, _HTFRunningAccumulator, D1/H4 forming candle, O(1) incremental, preview_tick_tfs_s, I1/I3/I5 | `htf_live_preview_v1` |
| ~~0045~~ | ~~VPS SMC Trader Bot~~ → **Moved to trader-v3/ADR-037** | — | 2026-03-30 | *Арчі-specific, не v3 platform ADR* | — |
| ~~0046~~ | ~~Agent Personality Restoration~~ → **Moved to trader-v3/ADR-038** | — | 2026-04-03 | *Арчі-specific, не v3 platform ADR* | — |
| [0047](0047-structure-detection-v2.md) | Structure Detection V2 — Canonical BOS/CHoCH + FVG Accuracy | **Implemented** | 2026-04-07 | BOS, CHoCH, HH/HL/LH/LL, structure.py, FVG display cap, confirmation_bars, ICT canonical | `structure_detection_v2` |
| ~~0048~~ | ~~Platform Wake Engine~~ → **Moved to trader-v3/ADR-039** | — | 2026-04-14 | *Арчі-specific; v3 platform ADR: 0049* | — |
| [0049](0049-wake-engine-external-consumer-ipc.md) | Wake Engine — External Consumer IPC via Redis | **Accepted** | 2026-04-16 | WakeEngine, Redis IPC, wake conditions, PubSub notify, event-driven, external consumer | `wake_engine_v1` |
| [0051](0051-tda-cascade-trigger-timing.md) | TDA Cascade Trigger Timing — Defer to After London Close | **Accepted** | 2026-04-18 | TDA, cascade timing, london_end_hour_utc, NO_NARRATIVE root cause, ADR-0040 fix | `tda_calibration_2026_04` |
| [0052](0052-chat-modularization.md) | Chat Modularization + Security Layer | **Proposed** | 2026-04-19 | ui_archi, feature-modules, Svelte 5 runes, STRIDE threat model, auth/rate_limit/csrf, 8 slices | `ui_archi_chat_v2` |

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

Наступний номер: **0053** (0050 пропущено: TDA timing fix — використано 0051; 0052 = Chat Modularization)
