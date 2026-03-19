# Changelog

<!-- markdownlint-disable MD013 -->

Журнал змін тепер ведеться у структурованому форматі JSONL (детально),
а тут — короткий індекс для швидкого огляду.

- Основний журнал: changelog.jsonl
- Це файл — короткий індекс і довідка.

## Індекс (дерево за area)

1. UI (ui_chart)
2. Polling
3. App
4. Packaging
5. Docs
6. Governance
7. Runtime
8. Tools
9. TUI
10. Core

- 2026-03-12 · 20260312-022 · **SUPERVISOR TRAMPOLINE RAIL**: `app/main.py` зафіксовано для Python 3.14 venv trampoline lifecycle; `_kill_tree()` тепер clean для Windows/POSIX type-checking, а regression tests покривають taskkill tree kill, `_terminate()` і `supervisor.pid` lock semantics.
- 2026-03-12 · 20260312-023 · **DIAGNOSTIC CLEANUP PACK**: приглушено тільки `MD013` у `CHANGELOG.md`, прибрано dead CSS у drawing toolbar, bias toggle у HUD переведено на semantic button, виправлено invalid type-comment у confluence та переведено `websockets` у lazy import для локальних diag scripts.

- 2026-03-13 · 20260313-002 · **TYPED GATE ENFORCEMENT**: ADR-0034 status aligned across 4 docs (→ "Partially Implemented P0+P1"), `tdaa.md` deprecated, ADR-0016 Appendix C (Python 3.14 incident), rules K3–K6 (Zero Diagnostics + Adjacent Contract + ADR Status Gate + One Slice One Gate), X24–X27, L2 dual-venv fix, `gate_adr_config_sync.py` machine gate. 62 tests ✅.

- 2026-03-13 · 20260313-003 · **ADR-0016 DOC SYNC**: Appendix C про Python 3.14 trampoline/type-drift інцидент проведено в навігаційні доки: `docs/adr/index.md` уточнює keywords ADR-0016, а `docs/system_current_overview.md` тепер прямо фіксує Windows tree-kill + `supervisor.pid` rail.

- 2026-03-13 · 20260313-004 · **PUBLIC DOC SYNC + ADR-0034 LOG CLARIFICATION**: [AGENTS.md](AGENTS.md) і [README.md](README.md) підтягнуто до ADR-0016 Appendix C (Python 3.14.2, Windows trampoline, tree-kill, `supervisor.pid`). Історичні записи ADR-0034 за 20260310-001 / 20260310-002 / 20260309-011 не переписувались; натомість зафіксовано, що canonical truth = current ADR-0034 status "Partially Implemented (P0+P1 only)".

- 2026-03-15 · 20260315-003 · **SIGNAL JOURNAL**: `runtime/smc/signal_journal.py` — автоматичний JSONL-лог narrative сигналів з lifecycle tracking (MFE/MAE, duration, bias, peak_trigger, reached_target). Hook: `SmcRunner.get_narrative()`. Config: `smc.signal_journal.enabled=true`.
- 2026-03-15 · 20260315-004 · **NARRATIVE S1 FIX**: counter-trend sub_mode (🟡 замість 🔴 при протилежному bias) + trigger proximity/displacement guards (3 helpers: `_price_near_zone`, `_displacement_into_zone`, `_choch_after_zone`). ADR-0033 Rev 3.
- 2026-03-15 · 20260315-005 · **DERIVE RESTART FIX**: D1/H4 missing після рестарту — M1 buffer cap 2000→10080, `UDS.reset_watermark()` перед cascade catchup, config warmup aligned.
- 2026-03-15 · 20260315-006 · **BACKWARD CRAWL**: ADR-0038 Amendment v2 — daemon thread для поступового backfill M1 історії (до 30 днів). Видалено Phase 2.5b (D1 direct fetch).
- 2026-03-16 · 20260316-001 · **MARKET-HOURS GUARD**: SmcRunner.get_narrative() тепер перевіряє `MarketCalendar.is_trading_minute()` — на закритому ринку повертає `mode=wait, sub_mode=market_closed` замість хибних сигналів. TS types + UI components оновлені.
- 2026-03-16 · 20260316-002 · **FVG HEIGHT GUARD**: `_filter_for_display()` тепер фільтрує FVG зони по `max_zone_height_atr_mult * ATR` — гігантські HTF FVG більше не покривають полчарту.
- 2026-03-16 · 20260316-003 · **JOURNAL WARMUP GUARD**: SignalJournal не записує під час cascade catchup — `_warmup_done` flag у SmcRunner блокує journal.record() до завершення warmup.
- 2026-03-16 · 20260316-004 · **CASCADE DEDUP FIX**: Видалено `reset_watermark(0)` з cascade catchup — derived бари більше не дублюються в JSONL при кожному рестарті. 59,403 дублікати по 3 символах. Додано `tools/dedup_derived_jsonl.py` для очищення.

- 2026-03-19 · 20260319-001 · **TDA CASCADE SIGNAL ENGINE (ADR-0040)**: Повний rebuild Signal Engine як 4-stage cascade (D1→H4→Session→M15 FVG). P0: types (TdaCascadeConfig, TdaSignal, FvgEntry, TradeState). P1–P4: stages 1–4 (macro, H4 confirm, session narrative, FVG entry). P5: Config F trade management (50% at 1R, trail from 2R). P6: cascade orchestrator. P7: runtime wiring (TdaLiveRunner + SmcRunner integration). P8: config SSOT (`smc.tda_cascade`, 32 fields, `enabled: false`). Старі сигнали ADR-0039 = fallback. 755 tests ✅.

- 2026-03-13 · 20260313-005 · **ADR-0034a ARCHIVE CLARIFICATION**: [0034-advanced-market-analysis-tdaa.md](docs/adr/0034-advanced-market-analysis-tdaa.md) переведено з framing "deprecated copy" у точніше framing: archive-only snapshot pre-rollback. Підтримуваним документом лишається [0034-advanced-market-analysis-tda.md](docs/adr/0034-advanced-market-analysis-tda.md), але `tdaa` збережений як історичний зріз, а не як сміттєвий дубль.

- 2026-03-14 · 20260314-001 · **BINANCE SECOND BROKER (ADR-0037)**: Binance Futures як другий брокер для BTCUSDT/ETHUSDT. P0: `BinanceHistoryProvider` (REST klines, backoff retry). P1: `binance_ingest_worker.py` (M1 polling + derive cascade, anchor_offset_s=0). P2: `binance_tick_publisher.py` (WS kline_1m → Redis tick payloads). P3: supervisor wiring в `app/main.py`. Config: `crypto_24x7` calendar, `binance.enabled=false`. 510 tests ✅, 0 нових gate violations.
- 2026-03-14 · 20260314-002 · **INITIAL BACKFILL VIRGIN SYMBOLS (ADR-0038)**: Phase 2.5 в bootstrap для virgin symbols. S1: `bootstrap.initial_backfill_m1_bars=1440, initial_backfill_d1_bars=180`. S2: `fetch_m1_range()`. S3: `initial_backfill()` + Phase 2.5 в `_bootstrap_warmup()`. S4: `fetch_d1_range()` + `initial_backfill_d1()` + Phase 2.5b (D1 direct 180 bars). `_parse_klines` parameterized with `tf_s`. I1 compliant, idempotent. 510 tests ✅.
- 2026-03-14 · 20260314-003 · **LOG TRIAGE P1 — SILENT EXCEPTION HANDLERS**: `logger.debug` додано до 16 silent `except: pass` / `except: return []` обробників у 8 файлах. Категорії: 11 cleanup/teardown, 4 data-path best-effort, 1 process lifecycle. SCREAMING_SNAKE naming. Створено `tools/diag/log_audit.py` (audit naming coverage = 74%). Remaining silent: **0**. 510 tests ✅.
- 2026-03-14 · 20260314-004 · **FXCM TICK_PUBLISHER HEARTBEAT + STALE DETECTION (I5 FIX)**: tick_publisher — heartbeat кожні 60с, stale detection 300с → auto-reconnect, session cleanup перед новою спробою. broker_sidecar — consecutive failure escalation (10→ERROR, 60→CRITICAL). Порушення I5 виправлено. 510 tests ✅.
- 2026-03-14 · 20260314-005 · **BINANCE H4/D1 ANCHOR CONFIG FIX**: `day_anchor_offset_s_alt=0` + `day_anchor_offset_s_d1_alt=0` у config.json — розблоковує Binance H4+D1 derive з crypto midnight grid. Видалено 4 stale H4 файли (watermark poisoning). 510 tests ✅.
- 2026-03-14 · 20260314-006 · **D1 DUPLICATE SPAM FIX**: derive_engine буферизує terminal target TFs (D1/H4/M3) для overdue-dedup guard. Root cause: target_buf==None → ~2 duplicate/хв. 510 tests ✅.
- 2026-03-14 · 20260314-007 · **WS_SERVER ACCESS LOG SPAM FIX**: `access_log=None` у `web.AppRunner()` — вимкнено ~227k рядків/день aiohttp access log шуму. 510 tests ✅.
- 2026-03-12 · 20260312-024 · **WS SMC TYPE DRIFT**: у `runtime/ws/ws_server.py` вирівняно `SmcRunnerLike` з ADR-0035 session-level API та додано локальний `cast(Any, ...)` на двох best-effort викликах, щоб прибрати false-negative type checker без зміни runtime behavior.
- 2026-03-12 · 20260312-025 · **DOCS FRAGMENT FIX**: у `docs/contracts.md` виправлено ToC fragment для секції Redis snapshot, бо heading був перейменований до `Redis snapshot та preview`, а anchor у змісті лишився старий.
- 2026-03-12 · 20260312-002 · **DATA REPAIR + REBUILD FIX**: purge+re-fetch 749 corrupt M1 барів XAU/USD (incident 2026-03-11 14:31–2026-03-12 04:00), rebuild всіх derived TFs (M3-H4), D1 volume fix (565K→809K). Виправлено баг у `tools/rebuild_from_m1.py` Stage 1: partial M3/M5 бари записувались після першого M1 замість повної агрегації.

11. SMC

### App (supervisor)

- 2026-03-12 · 20260312-001 · **TRAMPOLINE FIX**: Python 3.14 venv trampoline на Windows створював orphan workers при `proc.terminate()`. Додано `_kill_tree()` (taskkill /F /T), замінено `_terminate()`, PID-file lock. Root cause: duplicate m1_ingestion_workers → BROKER_PROXY_TIMEOUT → stuck M1_LIVE_RECOVER.

### Governance (code-review audit)

|     Дата   |    Індекс    | area:     Опис                                       |

- 2026-03-08 · 20260308-015 · **AUTOMATION BASELINE + RATCHET**: додано GitHub Actions CI (`python` smoke + `ui_v4` typecheck/build), weekly Dependabot для `pip`/`npm`, README sync по automation/license, і затягнуто `no_bare_except` budget з 85 до 76 через debug-loud cleanup у config_loader / market_calendar / replay.

- 2026-03-08 · 20260308-016 · **SECOND RATCHET**: debug-loud cleanup у `disk_layer` / `ssot_jsonl` / `app.main`, повторний вимір `no_bare_except` = 65, budget затягнуто з 76 до 65.

- 2026-03-09 · 20260309-005 · **DX AUDIT D2/D3/D7/D10/D12**: Redis preflight, log rotation, stale dist/ detection, tsconfig strict, 17 unused var cleanup across 10 files.

- 2026-03-09 · 20260309-006 · **ADR-0035 DT RESEARCH**: Sessions & Killzones enhancement from Dark Trader conference — Asia close_utc 09→07 UTC, F9 wick/body sweep distinction, rebalancing narrative pattern, Frankfurt bridge, OTT=KZ confirmation, Q11-Q16.

- 2026-03-08 · 20260308-017 · **THIRD RATCHET**: `ws_server` доведено до `0` counted violations, загальний `no_bare_except` знижено до `58`, budget затягнуто з 65 до 58.

- 2026-03-08 · 20260308-018 · **FOURTH RATCHET**: `ui_chart_v3/server.py` доведено до `0` counted violations, загальний `no_bare_except` знижено до `48`, budget затягнуто з 58 до 48.

- 2026-03-08 · 20260308-019 · **FIFTH RATCHET**: `runtime/store/redis_snapshot.py` доведено до `0` counted violations, загальний `no_bare_except` знижено до `44`, budget затягнуто з 48 до 44.

- 2026-03-08 · 20260308-020 · **SIXTH RATCHET**: `runtime/store/layers/redis_layer.py` доведено до `0` counted violations, загальний `no_bare_except` знижено до `40`, budget затягнуто з 44 до 40.

- 2026-03-08 · 20260308-021 · **SEVENTH RATCHET**: `runtime/ingest/tick_preview_worker.py` доведено до `0` counted violations, загальний `no_bare_except` знижено до `36`, budget затягнуто з 40 до 36.

- 2026-03-08 · 20260308-022 · **EIGHTH RATCHET**: `runtime/store/uds.py` доведено до `0` counted violations; після актуального repo-wide переміру `no_bare_except` зафіксовано на `27`, budget затягнуто з 36 до 27.

- 2026-03-08 · 20260308-023 · **NINTH RATCHET**: `runtime/ingest/broker/fxcm/provider.py` доведено до `0` counted violations, загальний `no_bare_except` знижено до `21`, budget затягнуто з 27 до 21.

- 2026-03-08 · 20260308-024 · **TENTH RATCHET**: `runtime/ingest/tick_publisher_fxcm.py` і `runtime/store/layers/disk_layer.py` доведено до `0` counted violations, загальний `no_bare_except` знижено до `12`, budget затягнуто з 21 до 12.

- 2026-03-08 · 20260308-025 · **ZERO-DEBT + COMMERCIAL RAILS**: `no_bare_except` доведено до `0`, додано `governance_baseline` gate, `manifest.ci.json`, розширено CI до tests + static exit gates + dependency/security scan, AGENTS переведено в index-only mirror, а legal/security docs уточнено до явного localhost/commercial boundary.

- 2026-03-08 · 20260308-026 · **TEST LANE GREEN**: відновлено `aiohttp_client` fixture через repo-local `tests/conftest.py`, повернуто helper surface для TV mismatch probes у `ui_chart_v3/server.py`, закрито Windows cleanup у `test_qa_002.py`, додано `requirements-test.txt`, і повний `pytest -q tests/` пройшов green (`451 passed`).

- 2026-03-08 · 20260308-027 · **WARNING DEBT CLOSED**: `runtime/ws/ws_server.py` переведено на `aiohttp.web.AppKey` без started-app mutation, `tests/test_ws_server.py` підлаштовано під нові app keys, а repair/audit tools переведено з `utcfromtimestamp()` на timezone-aware `fromtimestamp(..., UTC)`; повторний `pytest -q tests/` завершився clean без warning summary (`451 passed`).

- 2026-03-11 · 20260311-021 · **M1 LIVE IPC RAIL**: `m1_ingestion_worker`/`broker_sidecar` переведено з blind shared response queue на `req_id + reply_to`, додано валідацію `symbol/req_id`, а `M1SymbolPoller.poll_once()` більше не пропускає `live_recover/stale` після empty fetch; targeted verify: `pytest tests/test_m1_ingestion_ipc.py -q` (`3 passed`).

- 2026-03-04 · 20260304-024 · **ROLE ROUTING**: додано систему ролей (4 ролі) до AGENTS.md §1.3 + copilot-instructions.md РІВЕНЬ 0. Автоматичний та явний вибір ролі за контекстом.

- 2026-03-05 · 20260305-003 · **R_TRADER ROLE**: нова роль "SMC Trader" — валідація scoring/grades/display з позиції трейдера. 3 формати виходу (Setup Evaluation, Grade Challenge, Chart Audit), severity T0-T3, 6 метрик якості, SMC глосарій.

- 2026-03-07 · 20260307-005 · **R_TRADER v1.1 + R_CHART_UX v1.1**: розширення ролей п'ятьма ICT-концептами — Fractals (Williams strict + nested), Sessions/Killzones (Asia/London/NY sweeps), IOFED (5-step precision entry drill), Momentum/Displacement, Context Flow (Wyckoff phases + MTF narrative). Trader: §1.3 (5 підсекцій), оновлені Setup Eval/Grade/Chart Audit templates, +12 glossary terms. Chart UX: visual specs для fractals/sessions/killzones/displacement/IOFED/context flow, оновлена ієрархія ваги (14 рівнів), render pipeline (16 кроків), 5 Appendix A specs.

- 2026-03-07 · 20260307-006 · **DOC_KEEPER SYNC**: 15 drifts fixed (10×S2, 5×S3) across 4 docs. ADR-0028/0029/0030-alt/0031/0032 → copilot-instructions + docs/index. confluence.py, momentum.py, BiasBanner.svelte, DisplayBudget.ts, lwc.ts → annotated trees. Test count 422→431. Role spec count 4→6. Confluence POI removed from "Не реалізовано".

- 2026-03-07 · 20260307-007 · **R_ARCHITECT ROLE**: нова роль "Systems Architect (ADR-First Doctrine)" — первинний автор ADR, trade-off аналіз, P-slice planning, cross-role coordination, масштабування та release. 12 секцій: конституційні закони A0–A6, компетенції (backend/frontend/SMC/DevOps), pipeline взаємодії з 6 ролями, канонічний ADR шаблон, 3 фази (RECON→DESIGN→STEWARD), архітектурні патерни A→C→B, checklist для типових ADR-тем. Role spec count 6→7.

- 2026-03-08 · 20260308-002 · **ADR-0033 Context Flow — Multi-TF Narrative Engine**: Proposed. Backend-synthesized narrative layer що перетворює SMC data у actionable decision: mode (trade/wait/caution), ActiveScenario (entry/trigger/target/invalidation), bias_summary, market_phase, FVG context. 3 альтернативи (backend/frontend/hybrid), вибір Alt A (backend pure). 5 P-slices ~350 LOC: types → narrative.py → FVG overlap → runtime wire → NarrativePanel.svelte. Initiative: smc_vis_phi3.
- 2026-03-09 · 20260309-001 · **ADR-0033 Implemented**: Context Flow — Multi-TF Narrative Engine (5 P-Slices). P1: types (ActiveScenario+NarrativeBlock). P2: narrative.py pure synthesis ~350 LOC + 20 tests. P3: FVG-OB overlap hide. P4: smc_runner+ws_server wiring. P5: NarrativePanel.svelte + ChartPane mount. Build: 172 modules, 307 KB, 0 errors. 196 SMC tests pass. Feature gate: smc.narrative.enabled=false.

- 2026-03-09 · 20260309-002 · **ADR-0034 Advanced Market Analysis — TDA Synchronization & Extended SMC**: Proposed. TDA (Top-Down Analysis) methodology integration: IFVG detection (inverted FVG), breaker OB transition, Decisional Point tagging, Quasimodo pattern, TF synchronization validation, idea-level invalidation, protected fractal. 8 P-slices ~510 LOC. 3 alternatives (modular extension/separate module/narrative-only), вибір Alt A (modular). Initiative: smc_tda_v1. Джерело: research/Advanced Market Analysis. Dark Trader.csv.

- 2026-03-10 · 20260310-001 · **ADR-0034 Phase 1 BUILD (P0+P1+P2)**: IFVG detection (`ifvg_bull`/`ifvg_bear` kinds, `_update_fvg_status` tuple return, engine step 4b, `SmcTdaConfig`), Breaker transition (`_apply_breaker_transition`, engine step 7b), TF-sync validation (`_compute_tf_sync`: full/partial/broken/warmup, `NarrativeBlock.tf_sync`+`invalidation_tf`). Wire format: `types.ts` updated (`origin_zone_id`, `tf_sync`, `invalidation_tf`). Config: `smc.tda` section added (enabled=false). 30 new tests, 521 total pass. Initiative: smc_tda_v1.

- 2026-03-10 · 20260310-002 · **ADR-0034 Phase 2 BUILD (P3+P4+P5)**: Decisional Point (`_apply_dp_tagging`, engine step 7c, `SmcZone.is_decisional`), Protected Fractal (engine `get_display_snapshot` step 6c, `SmcSwing.is_protected`), Quasimodo detection (new `quasimodo.py`: 4-swing pattern + BOS confirmation, engine step 8), F10 quasimodo confluence factor (+1, max 14 pts). Wire: `types.ts` (`is_decisional`, `is_protected`, quasimodo kinds), `config.json` TDA sub-toggles. 28 new tests, 549 total pass. Initiative: smc_tda_v1.

- 2026-03-09 · 20260309-011 · **ADR-0034 Phase 3 BUILD (P6)**: Idea-Level Invalidation. `_find_htf_swing_invalidation()` in `narrative.py`: HTF swing-based invalidation (last LL for long, last HH for short). `synthesize_narrative()` accepts `snapshots_map` for cross-TF lookup. `smc_runner._build_snapshots_map()`. `SmcTdaConfig.idea_invalidation_enabled` toggle. ADR-0034 status → **Implemented** (all 8 P-slices done). 17 new tests, 566 total pass. Initiative: smc_tda_v1.

- 2026-03-09 · 20260309-003 · **R_COMPLIANCE ROLE**: нова роль "Compliance & Safety Officer" — 8-ма роль AI-агента. 7 доменів компетенції: ліцензії та IP, безпека коду (OWASP), фінансове регулювання та disclaimers, захист даних, операційна безпека, документаційна відповідність, technology lifecycle (EOL). 14 секцій: конституційні закони C0–C6, 3 фази (AUDIT→ASSESS→REMEDIATE), severity S0–S3, 4 чеклисти, 10 pre-loaded ризиків, cross-cutting gate. Role spec count 7→8.

- 2026-03-09 · 20260309-005 · **DX AUDIT REMEDIATION**: D2 Redis preflight (fail-fast exit 3), D3 log rotation (50MB cap), D7 stale dist/ warning, D10 connector logs cleanup, D12 strict TS (`noUnusedLocals`/`noUnusedParameters`=true) + 17 unused var cleanup across 10 files. `.gitignore`: unignored `env_profile.py` та `pyproject.toml`.

- 2026-03-09 · 20260309-004 · **COMPLIANCE AUDIT**: повний аудит R_COMPLIANCE по 7 доменах. Risk register створено (docs/compliance/risk_register.md): 11 ризиків (0×S0, 3×S1, 4×S2, 4×S3). Remediated: P1 financial disclaimer в README.md (R3 CLOSED), P3 credential fallback видалено з tick_publisher_fxcm.py (R6 CLOSED). Accepted risks: no auth (localhost), Redis no password (localhost), Python 3.7 EOL (blocked by FXCM SDK). Zero copyleft deps, zero hardcoded secrets, OWASP compliant for localhost deployment.

- 2026-03-06 · 20260306-004 · **R_CHART_UX ROLE**: нова роль "Chart Experience Craftsman + DevOps" — візуальний крафт (Canvas 2D, DPR, анімації, теми, WCAG, micro-interactions) + DevOps (build pipeline, supervisor, process lifecycle, DX). 12 секцій, 10 заборон, 2 appendices.

- 2026-03-02 · 20260302-023 · **DOC_KEEPER SYNC**: 34 drifts fixed across 6 docs. Phantom files removed, test/ADR/gate counts corrected, ADR statuses synced, missing files added to trees, broken links fixed.

- 2026-02-26 · 20260226-001 · P1: README — ui_chart_v3 позначено як private module
- 2026-02-26 · 20260226-002 · P2: Sync preview TF allowlist (docs + default = config.json SSOT)
- 2026-02-26 · 20260226-003 · P7: Видалено 6 битих лінків з docs/index.md
- 2026-02-26 · 20260226-004 · ADR-0016/0017/0018: Python isolation, Replay-mode, SLO observability (Proposed)
- 2026-02-26 · 20260226-005 · 6 quick-fix patches: bisect.insort, warmup lock, LTM cache, LRU FD, stop_event, max_workers (ADR-0019)
- 2026-02-26 · 20260226-006 · ADR-0019/0020/0021/0022 + ADR-0001 addendum: повний аудит 14 дефектів
- 2026-02-28 · 20260228-001 · Codebase cleanup: connector removal з main.py, 15+ stale config keys purged, 3 tools import migration, gate_htf_available ADR-0023 bypass
- 2026-02-28 · 20260228-002 · Codebase cleanup v2: 18 orphan tools deleted, rebuild_derived consolidated → rebuild_from_m1, engine_b comments cleaned, 2 gates + 4 docs updated
- 2026-02-28 · 20260228-003 · ADR-0024: SMC Engine - Smart Money Concepts computation layer (Proposed)
- 2026-02-28 · 20260228-004 · ADR-0024 rev 2.0: FAQ, 5 new algorithms, Trader Experience Architecture, Growth Trajectory
- 2026-03-01 · 20260301-001 · ADR-0024 rev 2.1: staff-engineer review corrections (SmcRunner direct callback, MVP=E1 gate, types.ts prefix guard) + copilot-instructions SMC layer (C5+C6, S0-S6, MODE=BUILD)
- 2026-03-01 · 20260301-003 · ADR triage: 0005/0014/0015→Implemented, 0021→Accepted, 0022→Implemented, 0024→Accepted. Index links fixed.
- 2026-03-01 · 20260301-004 · **SMC E1 BUILD**: types.py (frozen dataclasses S2/S3/S6), config.py (SmcConfig), swings.py (fractal pivots), structure.py (BOS/CHoCH), order_blocks.py, fvg.py, engine.py (SmcEngine orchestrator S4 rail), `__init__.py`, config.json[smc], tests/test_smc_e1.py 20/20 ✅. ADR-0024 → Implemented (E1).
- 2026-03-01 · 20260301-005 · **SMC S4 WIRING**: SmcRunner (runtime/smc/), ws_server zones/swings/levels full frame injection, delta smc_delta field, warmup via executor. UI §6.1a: SmcZone.kind→string, OverlayRenderer.zoneColor prefix-match. 30/30 ✅.
- 2026-03-01 · 20260301-006 · **SMC Runner tests**: test_smc_runner.py — warmup, on_bar, delta, reentrancy, performance rail. 31/31 ✅.
- 2026-03-01 · 20260301-007 · **SMC E2 Liquidity**: detect_liquidity_levels() ATR-кластери swing рівнів + smcStore.ts (applySmcFull/applySmcDelta) + ChartPane incremental update. 73 tests ✅.
- 2026-03-01 · 20260301-008 · **SMC E2 P/D+Inducement**: Premium/Discount zones (§4.6) + Inducement/False Breakout Trap (§4.7) + b.close→b.c fix. 111 tests ✅.
- 2026-03-02 · 20260301-009 · **SMC N1+N2+N3**: Zone lifecycle (_update_zone_lifecycle: merge/evict/mitigate/decay/cap), config caps (max_zones 10, FVG height guard 5×ATR, min_gap 0.3, P/D off), UI strength-opacity + 4 toggles OB/FVG/SW/LVL. D-01/D-02/R-01–R-04 addressed. 125 tests ✅.
- 2026-03-02 · 20260301-010 · **DOCS**: ADR-0024 §18 Implementation Progress, §5.3/§10/§16 актуалізовано. system_current_overview (SMC pipeline, Mermaid, tree). contracts.md SMC wire format. docs/index.md SMC навігація + S0–S6. adr/index.md +ADR-0025.
- 2026-03-02 · 20260301-011 · **SMC D1+D2+D3**: Display filter (proximity N×ATR + height guard + zone/level/swing caps), decay tuning (start 30, floor 0.15, two-tier 0.97/0.92), disappeared→mitigated delta fix, hide_mitigated=true, bull/bear-aware OverlayRenderer colors. 147 tests ✅.
- 2026-03-02 · 20260301-012 · **SMC HARDENING (ADR-0024a)**: 8 fixes from self-audit. F1: UI swing cap (MAX_UI_SWINGS=40). F2: filterMitigatedZones helper. F4: ATR compute 6×→1× per recompute. F7: swing wire format a/b→point {kind,time_ms,price}. F8: trend_bias in snapshot/full wire. F10: decay params moved to SmcConfig root. F12: SmcDisplayConfig exported. F6: orphan tests.py removed. 147 tests ✅.
- 2026-03-02 · 20260301-013 · **SMC KEY LEVELS (ADR-0024b)**: PDH/PDL/DH/DL + H4/H1/M30/M15 prev/curr H/L, cross-TF injection, per-kind UI styling (D1=orange, H4=purple, H1=blue, M30=teal, M15=cyan), proximity filter disabled for levels. 167 tests ✅.
- 2026-03-02 · 20260301-014 · **DOCS + FIXES**: Double-RAF overlay sync fix (ADR-0024 §18.7), drawing localStorage fix, key levels visualization docs (§18.8), copilot-instructions render rule + smell-test. 411 tests ✅.
- 2026-03-02 · 20260301-015 · **SMC ZONE FIX**: OB/FVG proximity 3 root causes — proximity_atr_mult 5→15, Context Stack re-filter gate removed, proximity_score ATR-relative→price-relative %. 415 tests ✅.
- 2026-03-02 · 20260301-016 · **DOCS**: ADR-0024c rewrite — status Implemented, §11 full file inventory (13 modules, 2478 LOC), §14 Implementation Notes (pipeline, Context Stack, lifecycle FSM, UI rendering). ADR index updated.
- 2026-03-02 · 20260301-017 · **REPLAY MODE (ADR-0017)**: runtime/ingest/replay.py (~300 LOC) — reads M1 JSONL, feeds UDS+DeriveEngine pipeline. NullJsonlAppender (no disk writes), watermark pre-population (bypass stale check), Redis flush. app/main.py --mode replay --speed N --symbols S. UI+WS auto-start. ADR-0017 → Implemented. 415 tests ✅.
- 2026-03-02 · 20260301-018 · **REPLAY v2**: NullDiskLayer для UI/WS reader (V3_REPLAY_MODE env), --start YYYY-MM-DD date filter, Redis tail 10080→500 (3.4x speedup: 171 vs 50 bars/s). End-to-end verified: /api/bars returns redis_tail source. 411 tests ✅.
- 2026-03-02 · 20260301-020 · **ZONE QUALITY H1 (ADR-0024c)**: Q4: OB zone=body not wicks (tighter, fewer false mitigations). Q1: CHoCH OB 1.5× strength boost. Q2: TF-aware decay (H4 4×, D1 10× slower). Q3: FVG min_gap halved for H1+. 411 tests ✅.
- 2026-03-02 · 20260302-021 · **FIX S0: UI FREEZE** (effect_update_depth_exceeded): main $effect reads+writes smcData in delta path → infinite reactive loop. Fix: `untrack(smcData)`. 1-line. Vite clean.
- 2026-03-02 · 20260302-022 · **FOG ZONES (ADR-0024c)**: renderZones() → horizontal CanvasGradient + proximity-based materialization. Base opacity 2× reduction. P/D zones disabled (config). _rgba() helper. Vite build OK.
- 2026-03-05 · 20260305-001 · **ADR-0028 Φ0: ELIMINATION ENGINE**: Config tuning (proximity 6.0, +6 display fields with budget validation). Server: post-mitigation TTL eviction, min_display_strength gate, FVG sort+cap. Client: DisplayBudget.ts (per-side budget, strength→opacity, Focus/Research passthrough). UI: F/R toggle button + F keyboard shortcut + localStorage. 415 tests ✅, 0 tsc regressions.

- 2026-03-05 · 20260305-002 · **ADR-0029 Accepted: CONFLUENCE SCORING**: OB Confluence Scoring + Grade System (8 factors, max 11 pts, grades A+/A/B/C). 6 errata (E1-E5+ER-6) applied. Decision Log R1.1-R1.4: 4 deferred alternatives documented. 4 P-slices planned (~130 LOC prod + ~60 LOC tests).

- 2026-03-05 · 20260305-004 · **ADR-0029 Implemented: CONFLUENCE SCORING + GRADES**: core/smc/confluence.py (8 factors F1-F8, max 11 pts, A+/A/B/C grades). Engine step 7 scoring post cross-TF injection. Wire: zone_grades in full frame via smc_runner→ws_server. UI: gold/white/gray grade badges on zones, Focus mode filters to A+/A OBs only. 9 new tests. 424 pass / 7 pre-existing fail.

- 2026-03-08 · 20260308-001 · **ADR-0031 Implemented: BIAS BANNER**: P1 smc_runner.get_bias_map() + ws_server bias_map in full frame. P2 SmcData.bias_map type + store + normalizeSmcData. P3 BiasBanner.svelte pills D1/H4/H1/M15 з colour coding + alignment indicator. 7 files, 424 pass, 0 TS errors.

- 2026-03-08 · 20260308-002 · **WILLIAMS FRACTALS (P1)**: display-only fractal markers (period=2, Williams 5-bar). Separate `detect_fractals()` — NOT fed into BOS/CHoCH chain. Purple triangle markers ▲/▼, FR toggle, `max_display_fractals=30`. 7 files, 424 pass, 0 TS errors.

- 2026-03-09 · 20260309-007 · **ADR-0035 Implemented: SESSIONS & KILLZONES**: Full P0-P7 BUILD. P0: `SESSION_LEVEL_KINDS` (12 kinds), `SmcSessionsConfig`, `config.json` sessions block. P1: `core/smc/sessions.py` (~230 LOC pure logic). P2+P3: SmcEngine session integration (M1 bar feed, cross-TF level injection step 6b). P4: F9 confluence factor `_check_session_sweep` (tri-state 0/1/2, wick vs body). P5: Narrative extension (session context, killzone downgrade). P6: Wire+UI (12 session level styles Asia=#CE93D8 London=#FF9800 NY=#42A5F5, 3-tier priority sort). P7: 40 tests. 491 pass, 0 fail.

- 2026-03-10 · 20260310-001 · **ADR-0035 HOTFIX: NARRATIVE DELTA + SESSION LEVELS + KZ BADGE**: P1: M1 warmup 500→2880 bars (48h) for session H/L. P2: Narrative recomputation in WS delta loop on complete bars. P3: M1 live feed in delta loop (`feed_m1_bar_dict` + `_m1_cursor_by_sym`). P4: Session levels in delta frames (`get_session_levels_wire` + `applySessionLevels`). P5: KZ badge in ChartHud + session context tooltip. Bonus: 4 pre-existing TS errors fixed (FrameType/UiWarningCode/SmcData defaults). 491 pass, 0 TS errors.

- 2026-03-09 · 20260309-008 · **R_CHART_UX PREMIUM REFOCUS**:
	роль `R_CHART_UX` переформульовано в scenario-first premium trader-grade product design,
	зафіксовано premium restraint, thesis/decision hierarchy і розширено routing vocabulary
	на design/premium/HUD/Awwwards запити без створення нової ролі.

### Runtime

|     Дата   |    Індекс    | area:     Опис                                       |

- 2026-03-08 · 20260308-014 · **ADR-0016 P1-P7: DUAL-VENV BROKER ISOLATION**: broker_sidecar.py (.venv37/ FXCM fetcher), m1_ingestion_worker.py (BrokerRedisProxy → M1SymbolPoller), app/main.py_python_for() routing, requirements-broker.txt, gate_dual_python, docs update. Platform ≥3.11, broker 3.7. Legacy m1_poller fallback retained.

- 2026-03-01 · 20260301-002 · S20+S25: WS error frames (json_parse_error, missing_action, unknown_action) + frameRouter.ts handling + 3 new tests, 6 fixed

### UI_v4

|     Дата   |    Індекс    | area:     Опис                                       |

- 2026-02-25 · 20260225-097 · UI_v4: Fix D1 horizontal chart gaps (missing Mondays). Змінено мапінг часу для D1 з рядка YYYY-MM-DD назад на epoch seconds з `+3h` зсувом. Lightweight Charts тепер відмальовує безперервну вісь X без розривів (1 file ~2 LOC).
- 2026-02-24 · 20260224-091 · ADR-0007: Glass-like Drawing Toolbar + Theme-aware drawing colors (WCAG AA, CSS custom properties, backdrop-filter blur, 5 files ~80 LOC)
- 2026-02-24 · 20260224-092 · ADR-0008 PATCH A: Y-axis sync render via notifyPriceRangeChanged callback (3 files ~12 LOC)
- 2026-02-24 · 20260224-093 · ADR-0008 PATCH B: Draft/drag clamp at null coordinates — prevents freeze (1 file ~12 LOC)

### Tools

|     Дата   |    Індекс    | area:     Опис                                       |

- 2026-02-24 · 20260224-096 · ui_v4: Fix HUD/Chart TF split-brain & cross-pollution. Виправлено leak змінної `frame` у `ws_server.py`, додано `currentPair` як SSOT до `frameRouter.ts`, та split-brain guard для відкидання чужих дельт.
- 2026-02-24 · 20260224-095 · core: ADR-0011 SSOT Broadcast Serialization (ws_server). Реалізовано єдиний глобальний `_global_delta_loop`, який зменшує CPU навантаження з O(N) до O(1) за парами символ/ТФ. Розсилка працює через `asyncio.gather` без backpressure.
- 2026-02-24 · 20260224-094 · core: ADR-0010 Thread-safe RAM Layer (P3.2). Додано threading.Lock до RamLayer для безпечного доступу з ws_server та tick_agg одночасно.
- 2026-02-23 · 20260223-080 · rebuild_from_m1.py: staged cascade M1→M3/M5→M15→M30→H1→H4 tool (8751 bars derived across 13 symbols)
- 2026-02-21 · 20260221-028 · Batch B: S15 dependency_rule gate (I0 AST enforcement, 0 violations) + S16 no_bare_except gate (ratchet budget=86, auto-allow cleanup/import) + S17 remove_derive_m3 fallback (loud warning)
- 2026-02-21 · 20260221-025 · Gate-fix batch: 5 pre-existing FAIL gates fixed → 24/24 OK (preview_not_on_disk, htf_available, unexpected_gap_budget, api_splitbrain, overlay_anchor_sentinel). Config: derived_tfs_s=[14400].
- 2026-02-20 · 20260220-040 · Gap signaling: GET /api/gaps endpoint — serves scanner report for UI gap visualization
- 2026-02-20 · 20260220-039 · Tail integrity scanner: all symbols × all TFs × N days — geometry + calendar-aware gaps + monitoring output
- 2026-02-20 · 20260220-038 · backfill_cascade.py: waterfall M1→H4 tool with calendar-aware derive + multi-anchor SSOT writer (107 H4 + 540 bars)
- 2026-02-19 · 20260219-032 · aione-top v0.6.1: 5 bugfixes (CPU %, freshness publish_time, primed totals, layout, Ctrl+C restore)
- 2026-02-19 · 20260219-031 · aione-top v0.6: 3 pages, combined grid, freshness lag fix, improved bootstrap
- 2026-02-19 · 20260219-030 · aione-top v0.5: Page 2 Pipeline Monitor (bootstrap, bars grid, log tail) + [Tab] page switching
- 2026-02-19 · 20260219-023 · aione-top v0.1: htop-like read-only TUI monitor for v3 platform
- 2026-02-19 · 20260219-017 · ADR-0002 P4: M5(derived) vs M5(broker) exit gate — OHLC PASS (9/13 exact, 12/13 < 0.03%), volume excluded (FXCM semantic), 1 corrupted M1 bar (USD_CAD).

### Core

|     Дата   |    Індекс    | area:     Опис                                       |

- 2026-02-23 · 20260223-082 · ADR-0004: mid-session gap tolerance in derive cascade (MAX_MID_SESSION_GAPS=3) + full 13-symbol×8TF audit + NGAS H4 fix (6 new bars)
- 2026-02-23 · 20260223-081 · Slice 3+4+5: Live preview candles for ALL TFs (M1→H4) via config + anchor fix; HUD streaming auto-fixed; D1 alignment verified (22:00 UTC winter)
- 2026-02-23 · 20260223-080 · Slice 1+2: flat bar display filter (D1 weekend artifacts hidden) + rebuild_from_m1 tool (8751 bars filled across 13 symbols M3→H4 from existing M1 data)
- 2026-02-23 · 20260223-079 · AUDIT: Derive engine session alignment confirmed TV-correct (modulo bucketing in bucket_start_ms). No code changes — formal proof only.
- 2026-02-23 · 20260224-075 · Boundary-tolerant derive: fix missing M3/M5/M15/M30/H1/H4 bars at session open/close (broker omits first M1 after reopen → cascade failure). 1457 bars recovered.
- 2026-02-20 · 20260220-037 · ADR-0002 P6: Calendar-aware derive_triggers — fix H4 19:00 NEVER derived (ALL symbols, ALL dates) + overdue bucket safety net (60s timer)

### Runtime

|     Дата   |    Індекс    | area:     Опис                                       |

- 2026-02-26 · 20260226-001 · runtime/core: ADR-0014 UDS Split-Brain Resilience (Degraded-But-Loud metric + recovery), ADR-0015 Calendar Pause decisions finalize. Test added.
- 2026-02-28 · 20260228-005 · D1 near-duplicate fix: 13 history@21:00 bars removed (XAU/USD) + _ensure_sorted_dedup near-dedup guard (tf_ms//12 threshold for DST anchor jitter)
- 2026-02-23 · 20260223-083 · Massive FXCM history backfill: M1×6 + M5×2 + direct M15/H1/D1 + rebuild cascade. All 13 symbols, M1 81-120K bars (5mo), H1 10K (1.5yr), H4 2.5-6.6K, D1 2-10K. Coverage 93-98%.
- 2026-02-22 · 20260222-069 · P11: scrollback disk_policy=explicit + max_steps(6) + cooldown(0.5s) rails
- 2026-02-22 · 20260222-068 · P12 pass-2: ~110 LOC dead code removed (uds.py, ssot_jsonl, derive, redis_snapshot, main_connector) + time_geom.py deleted
- 2026-02-22 · 20260222-067 · FINDING: event_ts end-excl vs contracts.md end-incl — відкладений розгляд
- 2026-02-22 · 20260222-066 · Time geometry: preview close_ms → end-incl + dual convention docs (CandleBar=end-excl, Redis ALL=end-incl)
- 2026-02-22 · 20260222-042 · P5-fix2: TF label case fix (M15/H1 uppercase) + supervisor process summary table
- 2026-02-22 · 20260222-041 · P5-fix: ws_server port-retry з reuse_address (Windows TIME_WAIT resilience, crash loop fix)
- 2026-02-22 · 20260222-040 · P5: ws_server supervisor integration — auto-start via --mode all (essential, config-gated)
- 2026-02-22 · 20260222-039 · P2: UDS integration — full/delta/scrollback frames via WS (5/5 tests, mock UDS)
- 2026-02-22 · 20260222-038 · P1: ws_server.py skeleton — WS endpoint + heartbeat + seq (aiohttp, 3/3 tests)
- 2026-02-22 · 20260222-037 · P0: candle_map.py — bar→candle mapping R2 closure (7/7 tests)
- 2026-02-21 · 20260221-029 · S17.2: M1Buffer/_derive_m3 dead code removal (~95 LOC) + no_bare_except ratchet (86->85) + P7 Closeout note
- 2026-02-21 · 20260221-027 · Batch A: S11 SSOT log throttle (30s) + S12 preview tail TTL (curr*2) + S13 strip seq from /api/updates (allowlist) + S10 symbol normalization in UpdatesBus
- 2026-02-21 · 20260221-026 · S6.5+S7+S9: RAM LRU writer dynamic (8→128+), tail_n aligned to coldload (H4 256→1080, D1 128→365, M1→10080, M3→3360), fsync option for SSOT JSONL
- 2026-02-21 · 20260221-023 · S5: default complete=True → False + MISSING_COMPLETE warning (ui_chart server.py)
- 2026-02-21 · 20260221-022 · S6: TF_ALLOWLIST прибрано з buckets.py; tf_to_ms generic; SSOT=config.json
- 2026-02-21 · 20260221-021 · S3: FINAL_SOURCES single SSOT у core/model/bars.py (4 дублікати прибрано)
- 2026-02-21 · 20260221-019 · S2: Tick aggregator drops — degraded-but-loud WARNING emission (I5 fix)
- 2026-02-21 · 20260221-018 · S1: Preview TTL SSOT — wire config→UDS, eliminate 3-way mismatch (1800/120/60)
- 2026-02-20 · 20260220-046 · H4 anchor fix 68400→79200 + purge 202k legacy M5/H4 history bars + rebuild H4 from H1 (13 symbols) + Redis reprime + warmup config fix

- 2026-02-20 · 20260220-045 · P-slice 1 DISCOVERY: updates pipeline code correct; root cause = m1_poller not started (previous "0 events" diagnosis was wrong diagnostic query)

- 2026-02-20 · 20260220-042 · Deep overdue scan (N buckets) + cascade after overdue derive — fixes H1/H4 gap propagation + 6 tests + backfill 23 bars

- 2026-02-20 · 20260220-041 · Unified cold-load: disk_policy=explicit for ALL TFs + RAM-short→disk fallthrough + Redis TTL survives breaks

- 2026-02-19 · 20260219-036 · Bootstrap cascade catchup: M1 warmup → cascade → gap-fill M5→H4. Fixes H4=0 after restart.
- 2026-02-19 · 20260219-035 · ADR-0003 S4: Bootstrap params → config.json SSOT (bootstrap section, 4 keys)
- 2026-02-19 · 20260219-034 · ADR-0003 S3: Unified prime_ready AND-gate (connector+m1_poller) + budget fix (5→15s) + config timeout
- 2026-02-19 · 20260219-033 · ADR-0003 S2: Supervisor process isolation — restart with backoff instead of kill-all
- 2026-02-19 · 20260219-029 · ADR-0003 S1: bootstrap error isolation (engine_b 4 phases + m1_poller 4 phases) — degraded mode instead of crash
- 2026-02-19 · 20260219-022 · m1_poller pidfile guard: prevent duplicate instances (I5 single-writer enforcement)
- 2026-02-19 · 20260219-021 · DeriveEngine warmup fix: load M5/M15/M30/H1 from disk at bootstrap (4512 bars vs 3862)
- 2026-02-19 · 20260219-020 · Duplicate m1_poller diagnosis: 2 stale processes killed, 2560 disk duplicates removed
- 2026-02-19 · 20260219-018 · ADR-0002 P5: engine_b → D1-only (m5_polling_enabled=false, derived_tfs_s=[]). M5+ derive chain повністю через m1_poller.

### UI (ui_chart)

|     Дата   |    Індекс    | area:     Опис                                       |

- 2026-02-24 · 20260224-090 · ADR-0006 ws_server CPU opt: delta_poll 1→2s, ThreadPoolExecutor(max_workers=2) замість default(32). Idle 4.8→2.2% CPU, threads 44→21.
- 2026-02-24 · 20260224-088 · D1 weekend artifact fix: weekend-aware flat filter (Fri/Sat UTC) + connector unclosed/weekend guards + disk cleanup 24 bars
- 2026-02-24 · 20260224-087c · D1 appear-then-disappear fix: seq passthrough in /api/updates + forward-gap HTF-dependent (D1=10,H4=15) + skip background loadBarsFull on cache hit (switch race elimination)
- 2026-02-24 · 20260224-086 · D1 flat bar filter HTF exemption (>=H4 skips flat filter) + ws_server disk_policy=explicit
- 2026-02-24 · 20260224-089 · Drawing persistence: localStorage per symbol+TF (`v4_drawings_{sym}_{tf}`), symbol/TF restore on connect (skip default frame), toolbar collapse persistence. Баг fix: applyLocally early returns перед saveToStorage.
- 2026-02-24 · 20260224-088 · PATCH 2 DrawingToolbar: floating (position:absolute, top:80px), no background, collapse ‹/›, 28px width, ukr labels. Moved inside chart-wrapper.
- 2026-02-24 · 20260224-087 · PATCH 4 Bug Fixes: click-click state machine (TradingView-style), renderSync() for X+Y axis (wheel+dblclick), continuous drawing (Escape to exit). Magnet deferred.
- 2026-02-23 · 20260223-085 · ADR-0006 PATCH 1+3: unblock DrawingsRenderer, 4 tools, CommandStack, hotkeys, noop sendAction, brightness sync.
- 2026-02-23 · 20260223-078 · Phase 2 UX: themes dark/black/light, volume color sync with candle style, brightness control (☀/🌙 scroll), transparent HUD + adaptive colors, OHLCV legend below HUD with delta, idle auto-scroll 15s, top-right bar shifted left
- 2026-02-23 · 20260223-077 · 7 UX fixes: increase history limits (MAX_BARS 2x, steps 6→12), conditional wall indicator, resize refit (autoSize), clock → top-right compact bar, remove bottom StatusBar, TV-style OHLCV legend, maxBarSpacing 12→50
- 2026-02-24 · 20260224-076 · Fix LWC "Value is null" crash on scrollback: dedup overlapping bars + WhitespaceData guard + crash-loop prevention (prependData + _replacePastBars)
- 2026-02-24 · 20260224-074 · P3.7 follow toggle + isAtEnd guard + P3.9 visible range cache — ALL P3 items DONE (engine.ts +35 LOC, viewCache.ts 55 LOC new, ChartPane wiring)
- 2026-02-23 · 20260223-071 · Fix LWC "Cannot update oldest data" crash: head guard + rAF queue + backend delta sort (race condition preview→final)
- 2026-02-22 · 20260222-064 · P3.11-P3.15: multi-theme (dark/gray/blue), candle styles (5 presets), favorites (sym/TF), diagnostic panel (Ctrl+Shift+D), scrollback policy (wall detect + bar cap + indicator)
- 2026-02-22 · 20260222-063 · Documentation sync: system_current_overview, README, index.md, CHANGELOG, audit rev2 (entries 055-062)
- 2026-02-22 · 20260222-062 · T8/T9/T10 PHASE 4 polish: config frame (S24), delta_poll to config (S23), config_loader SSOT (S26)
- 2026-02-22 · 20260222-061 · T6/S19: WS output guards for candle shape + monotonicity on all outgoing frames
- 2026-02-22 · 20260222-060 · T1: Disable DrawingToolbar + DrawingsRenderer + keyboard shortcuts (trading tools deferred)
- 2026-02-22 · 20260222-059 · P3-popout: pop-out animation + clock right-edge (REVERTED by user — too aggressive, save for later)
- 2026-02-22 · 20260222-058 · P3.1-P3.2+P3.6-P3.10: HUD overlay (frosted glass) + cursor tooltip + streaming dot + pulse + wheel cycling
- 2026-02-22 · 20260222-057 · P3.3-P3.5: Price axis interactions (Y-zoom, Y-pan, dblclick reset) — V3 parity
- 2026-02-22 · 20260222-056 · P0 BLOCKER FIX: ws_server ReadPolicy prefer_redis=True (was False → 0 candles forever)
- 2026-02-22 · 20260222-055 · P3 DISCOVERY rev2: 13-category gap analysis V3→V4 appended to P3 prompt
- 2026-02-22 · 20260222-054 · Same-origin SPA serving: ws_server.py роздає ui_v4/dist/ + dynamic WS_URL (Rule §11, port 8000)
- 2026-02-21 · 20260221-031 · UI v4 GO-1..GO-5 + DiagState SSOT: scaffold Vite6/Svelte5/TS5.7, WS wiring, frameRouter, connection DiagState hooks, SymbolTfPicker, StatusBar, StatusOverlay, LWC v5 API fixes. 0 errors 0 warnings.
- 2026-02-21 · 20260221-008 · D1 fix: purge 499 legacy derived midnight bars (10 sym) + normalizeBar +2h→+3h DST + Redis D1 reprime all 13 sym
- 2026-02-21 · 20260221-007 · D1 fix: normalizeBar/normalizeRange moved inside createChartController closure (ReferenceError fix)
- 2026-02-21 · 20260221-006 · D1 fix: setViewTimeframe ordering — call before setBars for correct barTimeSpanSeconds
- 2026-02-21 · 20260221-005 · D1 display: normalizeBar YYYY-MM-DD for barTimeSpanSeconds>=86400, sort/format/tooltip string-safe
- 2026-02-19 · 20260219-016 · I3 guard: /api/bars preview overlay не перекриває complete history бар. Fix: stale preview_tail (v=0) заміщував final history (v=242) після market close.
- 2026-02-19 · 20260218-015 · ADR-0002 P3: Remove H4 derive from UI (~590 LOC). H4 now first-class UDS TF via read_window(tf_s=14400). Removed align=tv handler, 11 tests, backward compat kept.
- 2026-02-18 · 20260218-014 · ADR-0002 P2.3: DeriveEngine wired into m1_poller (on M1 commit → cascade M3+H4, shared UDS, warmup 300 M1).
- 2026-02-18 · 20260218-013 · ADR-0002 Phase 2: runtime/ingest/derive_engine.py — DeriveEngine каскадна деривація з I/O (on_bar, commit_tfs_s, per-symbol lock).
- 2026-02-18 · 20260218-012 · ADR-0002 Phase 1: core/derive.py — DERIVE_CHAIN + GenericBuffer + aggregate_bars (strict cascade pure logic).
- 2026-02-18 · 20260218-011 · Документація: повний посібник candle acquisition + оновлення system_overview, audit/plan, README.
- 2026-02-18 · 20260218-010 · P0.fix: remove calendar gate from poll_once — fixes missed last bar before market pause.
- 2026-02-18 · 20260218-009 · P0.2+P0.3: m1_poller live_recover + stale detection +214 LOC (ADR-0002 Phase 0 CODE COMPLETE).
- 2026-02-18 · 20260218-008 · P0.5+P0.1: m1_poller tail catchup on bootstrap + 5 new config keys (ADR-0002 Phase 0).
- 2026-02-18 · 20260218-007 · ADR-0002: 2 corrections — config SSOT alignment + tail catchup before main loop invariant.
- 2026-02-18 · 20260218-006 · ADR-0002: DeriveChain M1→H4 (4 phases). M1 stabilization → cascade derive → remove engine_b M5.
- 2026-02-18 · 20260218-005 · H4 derive calendar-aware + видалення broker H4 polling + auto align=tv. ROOT CAUSE: expected_count=4 хардкод → 19:00 bucket (3 trading H1) завжди incomplete.
- 2026-02-18 · 20260218-004 · UI: fetch timeout 15с + watchdog 30с для стабільного polling (fix browser stops after ~2h).
- 2026-02-16 · 20260216-013 · P0: symbol canonicalization (_→/) on API boundary + flat_bar_max_volume from config.
- 2026-02-16 · 20260216-011 · Scrollback: limit 100→400 for M1/M3 + UI SCROLLBACK_BARS from /api/config.
- 2026-02-16 · 20260216-010 · D1 align=tv: /api/bars auto-applies align=tv for D1 (tf_s=86400) same as H4.
- 2026-02-16 · 20260216-009 · UI auto align=tv for H4 in all /api/bars requests + debug HUD.
- 2026-02-16 · 20260216-008 · HTF freshness: staleness watch + tail sync from FXCM + verify (before/after).
- 2026-02-16 · 20260216-007 · VERIFY: server restart — 19:00 partial bar visible, cleanup duplicate tests.
- 2026-02-16 · 20260216-004 · align=tv partial before calendar break + M5 refine + last_bucket_probe.
- 2026-02-16 · 20260216-003 · align=tv meta cleanup: derived_h1_final source, no redis leak, derived_* on cache hit.
- 2026-02-16 · 20260216-002 · H4 align=tv: budgeted fill-to-limit + derived meta.source + tv_tooltip_compare.
- 2026-02-16 · 20260216-001 · H4 TV-aligned derived view: /api/bars?align=tv (anchor_remainder_ms=10800000) + loud updates guard + audit tool.
- 2026-02-16 · 20260215-012 · Діагностичний PATCH: instrument truth (meta.extensions) + debug_map rails + tv_mismatch_probe tool + 10 тестів.
- 2026-02-15 · 20260215-008 · HTF Anchor Offset PATCH (Slice 1-4): mpv_proof terminology fix, UI UTC-only display, runtime HTF anchor rail, hardcode_scan.py.
- 2026-02-15 · 20260215-005 · Public API: нормалізація window_v1 у /api/bars і /api/updates + rail-дропи і тести.
- 2026-02-14 · 20260214-204 · Docs sync: README + system overview + annotated tree після Slice-1..4.
- 2026-02-14 · 20260214-203 · Slice-4: short-window rail (partial+loud), RamLayer/UDS + verify.
- 2026-02-14 · 20260214-202 · Slice-3: final cold-start via internal `prefer_redis=true` (disk_policy=never).
- 2026-02-14 · 20260214-201 · Slice-2: Policy SSOT via `/api/config` + UI consume with loud fallback.
- 2026-02-14 · 20260214-200 · Slice-1: `/api/bars` no_data fully-loud (warnings preserved/rail).
- 2026-02-14 · 20260214-195 · P1-P4: static no-store headers + app.js build-id + cursor_gap informational + SETDATA debug.
- 2026-02-14 · 20260214-193 · P1-P4: cache-first switch + HTF continuity + perf marks (ui lag fix).
- 2026-02-14 · 20260214-192 · Dead imports cleanup: normalize_bar, DEFAULT_TF_ALLOWLIST (uds.py).
- 2026-02-14 · 20260214-191 · P1-P4: disk hotpath ban — disk_policy rail + bootstrap warmup + telemetry.
- 2026-02-14 · 20260214-190 · P4: server-side limit clamp (tf-aware + global 20k).
- 2026-02-14 · 20260214-189 · P3: debounce 120ms on symbol/TF switch.
- 2026-02-14 · 20260214-188 · P2: scrollback chunks 10k→2k, min 5k→500, trigger 2k→1k.
- 2026-02-14 · 20260214-187 · P1: COLD_START_BARS_BY_TF + MAX_BARS_CAP=20000 + view-last-limit.
- 2026-02-14 · 20260214-186 · Meta exposure в /api/updates response.
- 2026-02-14 · 20260214-185 · P6: preview poll limit 500→50.
- 2026-02-14 · 20260214-184 · P5: NoMix upgrade allowlist для preview TF.
- 2026-02-14 · 20260214-183 · P4: AbortController + updatesInFlight reset при epoch switch.
- 2026-02-14 · 20260214-182 · P3: UI cursor_gap recovery — debounced cold-reload.
- 2026-02-13 · 20260213-170 · Overlay: фільтр flat-bar + без prev_bar для HTF.
- 2026-02-13 · 20260213-171 · Overlay: вимкнено для H4/D1, HTF тільки broker final.
- 2026-02-13 · 20260213-172 · UI: гейти batch/epoch для /api/updates + drop-лічильник.
- 2026-02-13 · 20260213-173 · UI: contract-guard завжди додає contract_violation.
- 2026-02-13 · 20260213-154 · FXCM FIRST_TICK + UI stitching: fix M1 volume alternation + gaps. 0 gaps all TFs.
- 2026-02-12 · 20260212-120 · P2X.11-TICKR1 rollback audit: повний, двопроцесна архітектура відновлена.
- 2026-02-13 · 20260213-150 · PREVIOUS_CLOSE stitching: open[i]=close[i-1] in /api/bars — TV-like smooth candles. 0 price gaps across all TFs.
- 2026-02-11 · 20260211-107 · Fix: HUD-ціна для preview TF (1m/3m) — canonical field fallback.
- 2026-02-11 · 20260211-106 · P2X.6-U6: HTF overlay base_tf_s=M3 для H4/D1 + last_price.
- 2026-02-11 · 20260211-105 · UI: overlay ціна на шкалі + HUD для TF>=M5.
- 2026-02-11 · 20260211-093 · P2X.6-U3: fix prev_bar final-check (open_ms=b_prev включено).
- 2026-02-11 · 20260211-094 · P2X.6-U4: overlay anchor offset вирівняно з final + sentinel.
- 2026-02-11 · 20260211-095 · P2X.6-U5: UI polling backoff + visibility + single-flight.
- 2026-02-11 · 20260211-096 · UI: debug-індикатор поточних інтервалів polling.
- 2026-02-11 · 20260211-097 · P2X.6-O1: preview_tail_updates_total + лог/60с.
- 2026-02-11 · 20260211-098 · P2X.6-O1: overlay counters + log/60с.
- 2026-02-11 · 20260211-099 · P2X.6-O1: HUD meta (plane/seq/gap/held_prev).
- 2026-02-11 · 20260211-100 · P2X.6-O1: meta перенесено у debug-рядок.
- 2026-02-11 · 20260211-101 · Preview: прибрано тикові обсяги для 1m/3m.
- 2026-02-11 · 20260211-102 · UI: ціна шкали оновлюється за tick (last_price).
- 2026-02-11 · 20260211-103 · UI: UTC/last bar перенесено у HUD (без debug залежності).
- 2026-02-11 · 20260211-104 · UI: UTC/last bar повернено у diag (debug-незалежно).
- 2026-02-11 · 20260211-092 · P2X.6-U3: 2-bar overlay (prev_bar + curr_bar) з hold-last-until-final.
- 2026-02-11 · 20260211-091 · P2X.6-U2: preview:tail оновлюється на кожен publish (flat bars fix).
- 2026-02-11 · 20260211-090 · P2X.6-U1 PATCH B: overlay series + polling (1s) для TF≥M5.
- 2026-02-11 · 20260211-089 · P2X.6-U1 PATCH A: /api/overlay endpoint (read-only, preview-based).
- 2026-02-11 · 20260211-085 · P2X.6: API-cleanup — prefer_redis/force_disk ігноруються з loud warning.
- 2026-02-10 · 20260210-058 · P2X.6-A: UI read-path для preview TF + include_preview.
- 2026-02-10 · 20260210-056 · UI: відновлено _safe_int/_parse_bool та прибрано сміттєвий блок.
- 2026-02-10 · 20260210-050 · VERIFY: smoke UI /api/status/bars/updates після P2X.5.
- 2026-02-10 · 20260210-049 · VERIFY: UI не містить Redis токенів.
- 2026-02-10 · 20260210-045 · P2X.5-A: прибрано Redis read-path з UI.
- 2026-02-09 · 20260209-051 · UI: прибрано prefer_redis/force_disk та disk-fill.
- 2026-02-09 · 20260209-029 · VERIFY: крок A (Disk vs Redis) для /api/bars tf_s=300.
- 2026-02-09 · 20260209-026 · VERIFY: реальні /api/bars і /api/updates (XAU/USD).
- 2026-02-09 · 20260209-025 · P2.2: thin-API через UDS для /api/bars та /api/updates.
- 2026-02-09 · 20260209-022 · UI: fallback на o/h/l/c для normalizeBar.
- 2026-02-09 · 20260209-021 · P1b: guard у disk /api/bars + прибрано дубль.
- 2026-02-09 · 20260209-008 · P1b: warn-only guard для API контрактів.
- 2026-02-09 · 20260209-006 · P0.4b: cursor_seq завжди число (>=0).
- 2026-02-09 · 20260209-005 · P0.4: стабільний cursor_seq у /api/updates.
- 2026-02-09 · 20260209-004 · P0.2-P0.3: epoch-гейт і reset контексту в UI.
- 2026-02-09 · 20260209-002 · P0.1: epoch у всіх UI запитах.
- 2026-02-09 · 20260209-001 · P0: діаг-логи /api/bars та /api/updates.
- 2026-02-09 · 20260209-003 · UI: гібридний load, per-TF ліміти, захист від cache override.
- 2026-02-08 · 20260208-022 · UI: guard сортування barsStore перед stitch.
- 2026-02-08 · 20260208-021 · UI: простий scrollback 5k/2k + фаворитні ліміти.
- 2026-02-08 · 20260208-020 · UI: ensure-left-coverage loop + continuity log.
- 2026-02-08 · 20260208-019 · UI: range-based scrollback, LRU hot/warm, favorites.
- 2026-02-08 · 20260208-018 · Docs: дослідження UI кешу/scrollback/SMC.
- 2026-02-08 · 20260208-017 · UI: prefetch історії з диску для всіх символів.
- 2026-02-08 · 20260208-016 · UI: scrollback для 5m і стабілізація restore cache.
- 2026-02-08 · 20260208-015 · UI: prefetch кешу для всіх символів.
- 2026-02-08 · 20260208-014 · UI: фікс кешу при перемиканні symbol/tf.
- 2026-02-08 · 20260208-013 · UI: захист від NaN TF у запитах API.
- 2026-02-08 · 20260208-012 · UI: кеш по (symbol, tf) і інкрементальний догруз.
- 2026-02-08 · 20260208-011 · UI: scrollback з диску при скролі вліво.
- 2026-02-08 · 20260208-010 · UI: піднято Redis tail для 15m/30m/1h.
- 2026-02-08 · 20260208-006 · UI: tail‑читання диску, anti‑race load, кеш конфігу.
- 2026-02-07 · 20260207-051 · P8.1: guard у /api/bars проти малого Redis tail.
- 2026-02-07 · 20260207-044 · VERIFY: P7.2 Redis read hit/miss/down для /api/bars.
- 2026-02-07 · 20260207-043 · P7.2: UI /api/bars Redis-read + fallback на диск + діагностика.
- 2026-02-07 · 20260207-011 · UI: докстрінг /api/updates у server.py.
- 2026-02-07 · 20260207-010 · UI: TF allowlist з config.json.
- 2026-02-05 · 20260205-058 · UI: глушення ConnectionAbortedError при відповіді.
- 2026-02-05 · 20260205-050 · UI: client_id cookie + розширений лог клієнта.
- 2026-02-05 · 20260205-049 · UI: розширено debug-лог підключення клієнта.
- 2026-02-05 · 20260205-047 · UI: приховано системні логи, debug підключення клієнта.
- 2026-02-05 · 20260205-044 · UI: static_root/config.json резолвінг відносно пакета.
- 2026-02-05 · 20260205-042 · UI: data_root за замовчуванням з config.json.
- 2026-02-05 · 20260205-031 · OBS: lag close→api ~1.1m, ssot→api ~58.8s (повернутись).
- 2026-02-05 · 20260205-029 · P6.4: lag close→api та ssot→api в UI діагностиці.
- 2026-02-05 · 20260205-024 · VERIFY: відновлено оновлення барів із затримкою 11–40с.
- 2026-02-05 · 20260205-023 · P6.3: курсорний watermark + disk_last_open_ms + self‑heal.
- 2026-02-05 · 20260205-022 · VERIFY: API факти для freeze (config/updates/bars).
- 2026-02-05 · 20260205-020 · Live: preview з live_store + merge‑policy кешу.
- 2026-02-05 · 20260205-018 · P6: rolling window кеш + boot_id.
- 2026-02-05 · 20260205-017 · P5: server‑side guard для /api/updates і /api/bars.
- 2026-02-05 · 20260205-015 · P3.1: seq‑cursor для live‑апдейтів.
- 2026-02-05 · 20260205-014 · VERIFY: end‑incl нормалізація для 1m/1d.
- 2026-02-05 · 20260205-013 · P3: cursor/watermark + drop stale.
- 2026-02-05 · 20260205-012 · P2: NoMix + final>preview в applyUpdates.
- 2026-02-05 · 20260205-011 · P1: /api/updates + UI applyUpdates.
- 2026-02-05 · 20260205-010 · P0: нормалізація close_time_ms до end‑incl у API.
- 2026-02-04 · 20260204-163 · UI: live_symbol з live_state/config для дефолту.
- 2026-02-04 · 20260204-162 · UI: live_state шлях резолвиться від config.json.
- 2026-02-04 · 20260204-159 · Live: last_price/last_tick_ts у live‑стані.
- 2026-02-04 · 20260204-158 · UI: live‑свічка для всіх TF через live‑state.
- 2026-02-04 · 20260204-140 · UI: live‑свічка і ціна через /api/live.
- 2026-02-05 · 20260204-157 · UI: ui_debug коректно ховає підпис і Follow.
- 2026-02-05 · 20260204-156 · UI: індикатор стріму не червоніє без нових барів.
- 2026-02-05 · 20260204-155 · UI: повторний клік по HUD Symbol/TF закриває меню.
- 2026-02-05 · 20260204-154 · UI: повторний клік ховає HUD-меню.
- 2026-02-05 · 20260204-153 · UI: ще вужчий HUD і прозоріші меню.
- 2026-02-05 · 20260204-149 · UI: шторку HUD зроблено вужчою і прозорішою.
- 2026-02-05 · 20260204-148 · UI: шторка під HUD має ширину HUD.
- 2026-02-05 · 20260204-147 · UI: HUD-меню і HUD мають однакову ширину.
- 2026-02-05 · 20260204-146 · UI: ширина HUD-меню = ширина HUD.
- 2026-02-04 · 20260204-145 · UI: меню Symbol/TF відвʼязано від кнопок.
- 2026-02-04 · 20260204-144 · UI: меню Symbol/TF випадає під HUD.
- 2026-02-04 · 20260204-143 · UI: сховано кнопки Symbol/TF.
- 2026-02-04 · 20260204-142 · UI: оновлено вигляд списків у меню.
- 2026-02-04 · 20260204-141 · UI: кліки по HUD відкривають меню Symbol/TF.
- 2026-02-04 · 20260204-140 · UI: компактні меню Symbol/TF зі скролом.
- 2026-02-04 · 20260204-139 · UI: ще один крок від ціни для підпису.
- 2026-02-04 · 20260204-138 · UI: підпис зміщено від ціни.
- 2026-02-04 · 20260204-137 · UI: підпис у 3 рядки + кнопка HUD ховає інструменти.
- 2026-02-04 · 20260204-136 · UI: повернено кнопку меню у HUD.
- 2026-02-04 · 20260204-134 · UI: /api/config приймає шлях із слешем + cache-busting.
- 2026-02-04 · 20260204-133 · UI: мінімальний підпис справа і кнопки біля HUD.
- 2026-02-04 · 20260204-132 · UI: кнопки окремо від шапки, шапка ховається.
- 2026-02-04 · 20260204-131 · UI: ui_debug прапор і приховування debug блоку.
- 2026-02-04 · 20260204-130 · UI: шторка не накладається на ціну, follow справа.
- 2026-02-04 · 20260204-129 · UI: підкрутка шторки/годинника/статусу.
- 2026-02-04 · 20260204-128 · UI: шторка без фону, Last bar біля теми.
- 2026-02-04 · 20260204-127 · UI: HUD зафіксовано вгорі з більшою прозорістю.
- 2026-02-04 · 20260204-126 · UI: ширша зона наведення для Symbol/TF у HUD.
- 2026-02-04 · 20260204-125 · UI: ще більший HUD‑пульс.
- 2026-02-04 · 20260204-124 · UI: HUD‑пульс більш «вперед».
- 2026-02-04 · 20260204-123 · UI: анімаційне підсвічення Symbol/TF у HUD.
- 2026-02-04 · 20260204-122 · UI: курсор у HUD не змінюється.
- 2026-02-04 · 20260204-121 · UI: колесо миші змінює Symbol/TF у HUD.
- 2026-02-04 · 20260204-120 · UI: HUD прилягає до шторки.
- 2026-02-04 · 20260204-119 · UI: HUD рухається разом зі шторкою.
- 2026-02-04 · 20260204-118 · UI: тултіп зміщується через drawer-offset у JS.
- 2026-02-04 · 20260204-117 · UI: тултіп зміщується разом зі шторкою.
- 2026-02-04 · 20260204-116 · UI: випадаючі меню в шторці не обрізаються.
- 2026-02-04 · 20260204-115 · UI: кнопка шторки клікабельна.
- 2026-02-04 · 20260204-114 · UI: HUD зверху з індикатором та кнопкою шторки.
- 2026-02-04 · 20260204-113 · UI: українізовано підписи HUD.
- 2026-02-04 · 20260204-112 · UI: HUD на чарті, шторка зверху і індикатор стріму.
- 2026-02-04 · 20260204-111 · UI: прибрано Reload і підсилено контраст bars-dark.
- 2026-02-04 · 20260204-110 · UI: збереження Symbol/TF та дефолт XAU/1m.
- 2026-02-04 · 20260204-109 · UI: follow без підпису праворуч біля статусу.
- 2026-02-04 · 20260204-108 · UI: тема як toggle + вужчі/прозоріші тулбари.
- 2026-02-04 · 20260204-107 · UI: іконкові перемикачі з прозорими меню.
- 2026-02-04 · 20260204-106 · UI: приховано підписи у верхній панелі.
- 2026-02-04 · 20260204-105 · UI: прибрано темно-сірий з перемикача тем.
- 2026-02-04 · 20260204-104 · UI: dark-gray без сірого фону (поведінка як світла тема).
- 2026-02-04 · 20260204-103 · UI: світліший темно-сірий фон у сторінці та графіку.
- 2026-02-04 · 20260204-102 · UI: світліший фон для темно-сірої теми.
- 2026-02-04 · 20260204-101 · UI: відновлено структуру app.js і перемикання тем.
- 2026-02-04 · 20260204-100 · UI: фікс синтаксису init теми.
- 2026-02-04 · 20260204-099 · UI: прибрано старий checkbox handler теми.
- 2026-02-04 · 20260204-098 · UI: стабільне читання теми з select.
- 2026-02-04 · 20260204-097 · UI: debug теми у статусі.
- 2026-02-04 · 20260204-096 · UI: стабілізація перемикання тем.
- 2026-02-04 · 20260204-095 · UI: оновлено cache-busting для тем.
- 2026-02-04 · 20260204-094 · UI: 3 теми (світла/темна/темно-сіра).
- 2026-02-04 · 20260204-093 · UI: autoscale/координати для барів через activeSeries.
- 2026-02-04 · 20260204-092 · UI: прозорі свічки у режимі барів.
- 2026-02-04 · 20260204-091 · UI: бари звичайні/чорносірі + адаптація обсягу під стиль.
- 2026-02-04 · 20260204-090 · UI: сірі спадні свічки зроблено порожніми.
- 2026-02-04 · 20260204-089 · UI: темно-сірі для білих свічок.
- 2026-02-04 · 20260204-088 · UI: no-store для статики + cache-busting.
- 2026-02-04 · 20260204-087 · UI: перемикання стилів через visible + спільні дані.
- 2026-02-04 · 20260204-086 · UI: перемикання стилів через setData.
- 2026-02-04 · 20260204-085 · UI: повторне застосування стилю свічок після load.
- 2026-02-04 · 20260204-084 · UI: стилі свічок (сірі/білі/порожні/бари).
- 2026-02-04 · 20260204-043 · /favicon.ico повертає 204.
- 2026-02-04 · 20260204-037 · дедуплікація барів за time перед setData.
- 2026-02-04 · 20260204-036 · guard для null у visible range.
- 2026-02-03 · 20260203-057 · виправлено зв’язок label з полями.
- 2026-02-03 · 20260203-055 · годинник ближче до краю, gap консистентний.
- 2026-02-03 · 20260203-054 · gap між Last bar і UTC.
- 2026-02-03 · 20260203-053 · розширено блок часу в діагностиці.
- 2026-02-03 · 20260203-052 · Last bar + UTC разом, вирівняно відступи.
- 2026-02-03 · 20260203-051 · переставлено Bars/Error лівіше від часу.
- 2026-02-03 · 20260203-050 · UTC-годинник перенесено у діагностику.
- 2026-02-03 · 20260203-049 · UTC-годинник перенесено вгору.
- 2026-02-03 · 20260203-048 · прибрано дубль dblclick handler.
- 2026-02-03 · 20260203-047 · виправлено синтаксичну помилку chart_adapter_lite.
- 2026-02-03 · 20260203-046 · формат Last bar + поточний UTC час.
- 2026-02-03 · 20260203-045 · reset/follow як у ТВ + хоткеї + відступ.
- 2026-02-03 · 20260203-044 · тултіп по тілу+тінях, обсяг із мапи.
- 2026-02-03 · 20260203-043 · тултіп лише в тілі свічки.
- 2026-02-03 · 20260203-042 · тултіп: без часу, прозоріший, з обсягом.
- 2026-02-03 · 20260203-041 · тултіп і follow як у ТВ.
- 2026-02-03 · 20260203-040 · рішення: обмеження максимуму свічок відхилено/зупинено.
- 2026-02-03 · 20260203-039 · відкат обмеження максимуму свічок.
- 2026-02-03 · 20260203-038 · зменшено максимум свічок (maxBarSpacing).
- 2026-02-03 · 20260203-037 · обмежено максимум розміру свічок.
- 2026-02-03 · 20260203-036 · обсяги: ще менші й прозоріші.
- 2026-02-03 · 20260203-035 · висота/прозорість обсягів.
- 2026-02-03 · 20260203-034 · тема чарту + обсяги, смоук‑гейт на volumes.
- 2026-02-03 · 20260203-033 · рейки/гейти та смоук‑перевірки UI.
- 2026-02-03 · 20260203-032 · root causes і чекліст для нового чарта.
- 2026-02-03 · 20260203-031 · wheel по ціні: capture + manual/auto range.
- 2026-02-03 · 20260203-030 · стабілізовано wheel‑скрол по часу.
- 2026-02-03 · 20260203-029 · нормалізовано time range і wheel‑скрол по часу.
- 2026-02-03 · 20260203-028 · прибрано Bars, показуємо максимум.
- 2026-02-03 · 20260203-027 · відкат до 4.1.3 через init_error.
- 2026-02-03 · 20260203-026 · оновлено lightweight‑charts до 5.0.7 + wheel‑zoom ціни.
- 2026-02-03 · 20260203-025 · прибрано масштаб по ціні над нижньою шкалою.
- 2026-02-03 · 20260203-024 · Alt/нижня шкала -> масштаб по ціні.
- 2026-02-03 · 20260203-023 · відкат CDN lightweight‑charts до 4.1.3.
- 2026-02-03 · 20260203-022 · кастомний wheel‑zoom + оновлення lightweight‑charts.
- 2026-02-03 · 20260203-021 · wheel: час за замовчуванням, ціна біля шкали або Alt.
- 2026-02-03 · 20260203-020 · колесо миші масштабує лише ціну.
- 2026-02-03 · 20260203-019 · Shift+Wheel для масштабу по ціні.
- 2026-02-03 · 20260203-018 · уточнено масштабування по ціні як у ТВ.
- 2026-02-03 · 20260203-017 · масштабування по ціні колесом миші.
- 2026-02-03 · 20260203-016 · ручне вертикальне перетягування без зникнення свічок.
- 2026-02-03 · 20260203-015 · відновлено autoScale для ціни.
- 2026-02-03 · 20260203-014 · TV‑подібне керування скролом і масштабом.
- 2026-02-03 · 20260203-013 · вимкнено gap‑whitespace у UI чарті.
- 2026-02-03 · 20260203-010 · POST: діагностична панель, gap‑whitespace, guards LWC.
- 2026-02-03 · 20260203-009 · PRE (legacy): діагностична панель + gap‑whitespace + guards LWC.

### Polling

- 2026-02-19 · 20260219-019 · ADR-0002 P5.5 cleanup: dead M5 removed (~1145 LOC engine_b, ~145 LOC dead files), commit_tfs_s fix, time_buckets→core/buckets, config cleaned, README updated.
- 2026-02-10 · 20260210-052 · P2X.5-C1: ingest polling без ssot_jsonl.
- 2026-02-07 · 20260207-064 · M5: поступове підтягування хвоста (300 барів/5 хв, ліміт 30000).
- 2026-02-07 · 20260207-063 · M5: calendar-aware cutoff + warmup за missing + backlog status.
- 2026-02-07 · 20260207-059 · Calendar: start break включно (22:00 закрито).
- 2026-02-07 · 20260207-024 · Polling: винесено dedup/derive/flat_filter/fetch_policy.
- 2026-02-07 · 20260207-020 · Polling: flat-bar поріг volume з config.json.
- 2026-02-07 · 20260207-019 · Polling: history-пороги з config.json.
- 2026-02-07 · 20260207-018 · Polling: stateful history logging + reason_code.
- 2026-02-07 · 20260207-009 · Polling: пороги M5 tail з config.json.
- 2026-02-06 · 20260207-002 · Cleanup: видалено v3_polling_b.py.
- 2026-02-06 · 20260206-032 · Polling: фільтр плоских M5 барів.
- 2026-02-06 · 20260207-003 · Docs: README оновлено під M5-only.
- 2026-02-06 · 20260207-004 · Config: групові календарі за символами (UTC, single break).
- 2026-02-06 · 20260207-005 · Config: прибрано базові market_* ключі.
- 2026-02-06 · 20260207-006 · Docs: довідка про групові календарі.
- 2026-02-06 · 20260207-007 · Logging: transition-only календар, M5 tail state, UI debug gate.
- 2026-02-07 · 20260207-008 · Docs: додано annotated tree.
- 2026-02-06 · 20260206-031 · Док: опис поточної M5-only системи.

### App

- 2026-02-11 · 20260211-079 · Supervisor: режим tick_publisher.
- 2026-02-08 · 20260208-028 · Профілі: канали/redis у env + env для url/connection.
- 2026-02-08 · 20260208-027 · Env: очищено .env.local/.env.prod.
- 2026-02-08 · 20260208-029 · Профілі: один config.json + Redis з env (db/ns) + видалено дублікати.
- 2026-02-08 · 20260208-030 · ENV: лог профілю в UI + фікс os імпорту для Redis env.
- 2026-02-08 · 20260208-033 · VERIFY: локальний профіль підхоплюється у connector/UI/main.
- 2026-02-08 · 20260208-034 · VERIFY: прод профіль підхоплюється у connector/UI.

### Packaging

- 2026-02-09 · 20260209-001 · Deps: додано pandas для Python 3.7.

### Docs

- 2026-03-09 · 20260309-025 · **ADR-0036 FINAL MOBILE PASS**:
	фінальний mobile-pass узгодив premium shell на вузьких екранах без зміни semantic truth чи runtime поведінки.
	У `ui_v4/src/App.svelte` service rail на mobile переходить у full-width bottom rail з окремим replay offset; у `ui_v4/src/layout/ReplayBar.svelte`
	`Replay study` отримав додатковий нижній відступ і компактніший mobile rhythm; у `ui_v4/src/layout/DrawingToolbar.svelte` left rail перетворено на compact bottom-left dock
	з horizontal tools-group, icon-first buttons і схованим header/copy для narrow viewport. VERIFY: `npm run typecheck` і `npm run build` проходять clean.
	Примітка: інтегрований browser tool не змінив реальний viewport під час live-check, тому mobile proof зафіксовано через responsive CSS/DOM audit, а не через повноцінну runtime-emulation.

- 2026-03-09 · 20260309-024 · **ADR-0036 REDESIGN SLICE 5**:
	п’ятий redesign-pass прибрав останній raw chart-tooling характер із left tool rail.
	У `ui_v4/src/layout/DrawingToolbar.svelte` голий glyph-strip перетворено на компактний chart-side rail з header (`Chart tools` / `Markups`),
	label-first tool cards (`Level`, `Trend`, `Zone`, `Clear`), спокійнішим active state і calmer collapsed badge `Draw`, без зміни hotkeys, collapse-state чи drawing semantics.
	Live browser-pass підтвердив новий silhouette і коректний active selection для tool buttons. VERIFY: `npm run typecheck` і `npm run build` проходять з `0 errors, 0 warnings`.

- 2026-03-09 · 20260309-023 · **ADR-0036 REDESIGN SLICE 4**:
	четвертий redesign-pass перевів diagnostics surface з ops overlay у calmer right-side review drawer.
	У `ui_v4/src/App.svelte` trigger `Inspect` замінено на label-first desk control, а `ui_v4/src/layout/DiagPanel.svelte`
	отримав backdrop, editorial header, close affordance, м’якші section cards і trader-facing section labels (`Live service`, `Frame cadence`, `Transport`, `Notes`).
	Live browser-pass підтвердив новий drawer як `Review desk` / `Service pulse` без втрати read-only diagnostics semantics. VERIFY: `npm run typecheck` і `npm run build` проходять з `0 errors, 0 warnings`.

- 2026-03-09 · 20260309-022 · **ADR-0036 REDESIGN SLICE 3**:
	третій redesign-pass прибрав залишковий tooling feel із service rail, top controls і replay chrome.
	У `ui_v4/src/App.svelte` secondary chrome перепаковано в тихі state/style/meta clusters з явними trader-facing labels замість набору іконок,
	у `ui_v4/src/layout/ReplayBar.svelte` replay перетворено з transport bar на calmer study rail, а `ui_v4/src/layout/ChartHud.svelte`
	отримав м’якший anchor material для symbol/tf/favorite controls. Live browser-pass підтвердив `Feed Live` / `Mode Focus` у shell chrome
	та `Replay study` з `Return live` у replay mode. VERIFY: `npm run typecheck` і `npm run build` проходять з `0 errors, 0 warnings`.

- 2026-03-09 · 20260309-021 · **ADR-0036 REDESIGN SLICE 2**:
	другий redesign-pass поглибив shell typography, spacing rhythm і material depth, а також дочистив trader-facing copy.
	У `ui_v4/src/app/shellState.ts` action/context phrasing більше не тягне технічні маркери на кшталт `TF1800` або `Off-session`,
	а в `ui_v4/src/layout/ChartHud.svelte` reveal отримав виразніший headline, editorial hierarchy і глибші scenario cards.
	Live browser-pass підтвердив `ChoCH↓ into OB` у thesis action та `Outside killzone` у context strip. VERIFY: `npm run typecheck` і
	`npm run build` проходять з `0 errors, 0 warnings`.

- 2026-03-09 · 20260309-020 · **ADR-0036 REDESIGN SLICE 1**:
	перший справжній visual redesign-pass перевів premium shell з utility HUD у виразніший thesis-first surface.
	У `ui_v4/src/layout/ChartHud.svelte` thesis bar перебудовано в editorial plate з label/value hierarchy,
	тихішим verification cluster і окремим `Context` strip, а в `ui_v4/src/App.svelte` service rail демотовано
	у спокійніший secondary layer з менш «ops chrome» характером. VERIFY: `npm run typecheck` і
	`npm run build` проходять з `0 errors, 0 warnings`.

- 2026-03-09 · 20260309-019 · **ADR-0036 BROWSER POLISH**:
	після live browser-pass для premium shell підчищено trader-facing copy і mobile breakpoint.
	У `ui_v4/src/app/shellState.ts` нормалізовано session/trigger labels (`Off-session`, acronym casing,
	без сирого `Triggered:` у action-line), у `ui_v4/src/layout/ChartHud.svelte` mobile thesis row
	переведено на thesis-first order, а `ui_v4/src/App.svelte` переносить service rail у нижню mobile-safe zone,
	щоб secondary chrome не конкурував із shell. VERIFY: `npm run typecheck` і `npm run build`
	проходять з `0 errors, 0 warnings`.

- 2026-03-09 · 20260309-018 · **ADR-0036 P5 FOCUS/RESEARCH UNIFIED**:
	`App.svelte`, `ChartPane.svelte` і `ChartHud.svelte` зведено до одного existing focus/research state
	з ADR-0028 замість окремого shell-toggle. `ChartPane` тепер віддає `displayMode` у `App`,
	service rail отримав власний `F/R` toggle, а `ChartHud` використовує той самий state лише як
	presentation-signal для тихішого tactical/service emphasis. VERIFY: `npm run typecheck` і
	`npm run build` проходять з `0 errors, 0 warnings`.

- 2026-03-09 · 20260309-017 · **ADR-0036 P3 SERVICE RAIL + CLEAN VERIFY**:
	`ui_v4/src/App.svelte` нормалізовано як secondary service rail: replay інтегровано в top-right bar,
	додано `deriveServiceRailState()` для demotion/freshness presentation,
	а окрему replay-кнопку поза rail прибрано. Паралельно закрито залишковий warning-borg
	у `DiagPanel.svelte` та `DrawingToolbar.svelte`, тож `ui_v4` знову проходить
	`npm run typecheck` і `npm run build` з `0 errors, 0 warnings`.

- 2026-03-09 · 20260309-016 · **ADR-0036 P2 CHARTHUD SHELL**:
	`ui_v4/src/layout/ChartHud.svelte` перекомпоновано з utility-first HUD row у thesis-first shell:
	left anchor зберігає explicit symbol/TF controls, primary row тепер читається як
	`bias / state / POI / action`, tactical strip винесено окремо,
	а narrative detail переїхав у collapsible `Thesis Reveal` з hotkey `N` і auto-collapse.
	VERIFY: `npm run typecheck`, `npm run build`; warning у `ChartHud.svelte` закрито,
	залишились лише pre-existing warnings у `DiagPanel.svelte` та `DrawingToolbar.svelte`.

- 2026-03-09 · 20260309-015 · **ADR-0036 P1 CODE SLICE**:
	реалізовано перший кодовий slice premium shell у `ui_v4` без зміни wire/runtime scope:
	до `src/types.ts` додано semantic contracts `ShellStage`, `ThesisBarData`,
	`TacticalStripData`, `ServiceRailState`, а в `src/app/shellState.ts` додано pure helper API
	для stage derive, thesis/tactical/service presentation state, session accent і TF horizon labels.
	VERIFY: `npm run typecheck`, `npm run build`; лишились тільки pre-existing Svelte warnings
	у `ChartHud.svelte`, `DiagPanel.svelte`, `DrawingToolbar.svelte`.

- 2026-03-09 · 20260309-014 · **ADR-0036 P1 BRIEF CANONIZED**:
	implementation brief для P1 перенесено з чату в канонічний ADR-0036: exact files,
	exact helper API, exact verify, active-view semantics, TF horizon labels і P1 out-of-scope,
	щоб format/presentation change не трималась на неформальних домовленостях.

- 2026-03-09 · 20260309-013 · **ADR-0036 ACTIVE-VIEW CLARIFICATION**:
	в ADR-0036 і UI spec уточнено, що thesis bar читається відносно `active symbol + active TF`,
	а `symbol/TF` не зникають, а лишаються explicit left-anchor trader controls; також додано
	v1 taxonomy для TF horizon (`M3` scalp, `M15` tactical intraday, `H1` calmer intraday, `H4/D1` context-first).

- 2026-03-09 · 20260309-012 · **ADR-0036 HARDENED FOR BUILD**:
	у канонічний ADR-0036 додано explicit type contracts, ASCII wireframe,
	truth tables для null/cap logic, per-slice acceptance/test strategy,
	forward-compatibility з ADR-0034, focus/replay/a11y rails, DoD і doc cross-references.

- 2026-03-09 · 20260309-011 · **ADR-0036 ERRATA INTEGRATED**:
	у proposed ADR-0036 перенесено accepted parts з errata: `ShellStage` зафіксовано
	як presentation-only mapping поверх canonical narrative, додано optional hooks
	для TF-sync/killzone/replay, перейменовано `Context Reveal` у `Thesis Reveal`,
	уточнено ambient/session shell rails, concrete performance gates та P-slice verify.

- 2026-03-09 · 20260309-010 · **ADR-0036 PROPOSED: PREMIUM TRADER-FIRST SHELL**:
	архітектурно оформлено перехід `ui_v4` від utility-first HUD до
	thesis-first premium shell. ADR зводить позиції `R_ARCHITECT`, `R_TRADER`,
	`R_SMC_CHIEF`, `R_BUG_HUNTER`, `R_CHART_UX`, фіксує 3 альтернативи,
	вибирає incremental shell architecture без runtime-rewrite,
	і розкладає реалізацію на 5 P-slices.

- 2026-03-09 · 20260309-009 · **UI SPEC: PREMIUM TRADER-FIRST SHELL**:
	додано `docs/ui spec.md` як окремий execution-oriented design brief для
	award-level напряму `ui_v4`: що саме бракує до category-icon рівня,
	пояснення по кожному gap, trader-first зміни для HUD/top shell,
	before/after framing, visual system direction і перші 2 ініціативи
	без зміни runtime логіки.

- 2026-02-22 · 20260222-070 · Docs/README sync: aione_top in annotated tree/index/canon, dual convention I2, scrollback rails I6, ws_server LOC update, production runbook P11 details
- 2026-02-22 · 20260222-066 · Time geometry dual convention: contracts.md (Redis+preview=end-incl), system_overview (conv table), governance (fix I1 contradiction)
- 2026-02-22 · 20260222-063 · Docs sync: README + system overview + index + CHANGELOG + audit after T1-T10 COMPLETE (ui_v4 chart parity DONE)
- 2026-02-21 · 20260221-030 · P7 Closeout enrichment: slice-ID note, S14/S18 evidence pointers, ratchet policy, SKIP gate accountability, cross-run diff, P9 scope (3 bullets)
- 2026-02-19 · 20260219-028 · ADR-0003 Cold Start Hardening: 4 P-slices (error isolation, process restart, unified gate, config SSOT), 8 exit gates, 10 failure scenarios.
- 2026-02-15 · 20260215-006 · Docs overhaul: index.md, contracts.md, ui_api.md, production runbook, audit P0-P6 progress, README trim, back-links до всіх docs.
- 2026-02-15 · 20260215-004 · Додано docs/author_profile.md — розгорнутий профіль автора (від першої особи) на основі реального коду, журналу, контрактів і gate-ів.
- 2026-02-15 · 20260215-002 · README: зроблено змістовніший AS-IS опис (A→C→B, процеси, інваріанти, API, exit-gates) і синхронізовано з доками.
- 2026-02-14 · 20260214-216 · Docs: актуалізовано redis_snapshot_design під RedisSpec/namespace+db, prime:ready, disk_policy rails і redis_small_tail.
- 2026-02-14 · 20260214-213 · Нормалізовано execution plan у формат P0→P6, додано P11 (scrollback-only disk) і P12 (dead-path cleanup) з exit-gates.
- 2026-02-14 · 20260214-212 · Додано компактний failure-model (що/чому/прояв) і короткий TO-BE Decision/Consequences у research-аудит.
- 2026-02-14 · 20260214-211 · Зафіксовано policy міграції з ThreadingHTTPServer, розширено GO/NO-GO, додано обов’язковий preflight-read audit+overview для Copilot.
- 2026-02-14 · 20260214-210 · Завершено уніфікацію стилю всього research audit-файлу (нумерація/статуси/заголовки, український формат).
- 2026-02-14 · 20260214-209 · Оновлено TO-BE ADR + Execution Plan: done/open/ADR-needed статуси та план P5–P9.
- 2026-02-14 · 20260214-208 · FAILURE MODEL (F1–F11) переформатовано в єдину AS-IS матрицю з актуальними статусами й пріоритетами.
- 2026-02-14 · 20260214-207 · Уніфіковано та актуалізовано секції 1.7–1.8 (Concurrency/Security) у research аудиті.
- 2026-02-14 · 20260214-206 · Актуалізовано фрагмент AS-IS 1.1–1.6: єдиний формат (A→C→B + матриця), уточнені потоки/policy/rails.
- 2026-02-14 · 20260214-205 · Status refresh: research аудит позначено як частково історичний після Slice-1..4.
- 2026-02-14 · 20260214-197 · P0 Freeze+Snapshot: smoke runbook + JSON results + AS-IS flow/policy snapshots (docs/audit/p0_freeze_snapshot/).
- 2026-02-14 · 20260214-198 · P1 Policy Diff Table: Observed vs Config vs Client для TF=60/180/14400/86400 (docs/audit/p1_policy_diff/).
- 2026-02-14 · 20260214-199 · P2 Empty Chart Root Cause Pack: observed matrix + sufficiency + codepath + runbook (docs/audit/p2_empty_chart_rootcause/).
- 2026-02-13 · 20260213-160 · Посібник: як отримати TV-like M1/M3 свічки від брокера до UI.
- 2026-02-10 · 20260210-070 · ADR-0001: актуалізовано статус P2X і тести TickAggregator.
- 2026-02-10 · 20260210-068 · Docs: оновлено README та system_current_overview під UDS write-center.
- 2026-02-10 · 20260210-067 · ADR-0001: актуалізовано поточний стан UDS/preview/write-center.
- 2026-02-10 · 20260210-003 · ADR-0001: діагностика розриву інваріантів і мінімальний план P2X.
- 2026-02-10 · 20260210-002 · Docs: ADR-0001 апдейт + огляд системи + README під UDS write-center.
- 2026-02-09 · 20260209-040 · ADR-0001: примітка про slice mapping.
- 2026-02-09 · 20260209-028 · ADR-0001: додано VERIFY факт, причину та план hardening.
- 2026-02-09 · 20260209-027 · ADR-0001: оновлено поточний стан UDS та статус P-slices.
- 2026-02-08 · 20260208-031 · README: оновлено опис профілів і запуску під v3.
- 2026-02-08 · 20260208-032 · Docs: чистка зайвого, оновлення схем, приховано Host Contract.
- 2026-02-08 · 20260208-026 · Env: перевірка каналів і портів.
- 2026-02-08 · 20260208-025 · Профілі: env креденшіали + config.local/prod + вибір config.
- 2026-02-08 · 20260208-005 · Config: group_logs_enabled + перевірка дублікатів списків.
- 2026-02-07 · 20260207-061 · Connector: wait timeout login без stacktrace.
- 2026-02-07 · 20260207-058 · Startup: Redis init preflight з ping і логами.
- 2026-02-07 · 20260207-035 · Connector: чисте завершення під час retry/backoff.
- 2026-02-07 · 20260207-032 · Supervisor: запуск через subprocess -m.
- 2026-02-07 · 20260207-033 · Supervisor: stdio режими pipe/files/inherit/null + new-console guard.
- 2026-02-07 · 20260207-029 · Supervisor: режими all/connector/ui.
- 2026-02-07 · 20260207-023 · Connector: login failure без stacktrace.
- 2026-02-07 · 20260207-017 · Connector: календарний sleep при ORA-499.
- 2026-02-07 · 20260207-016 · Connector: ORA-499 як INFO без stacktrace.
- 2026-02-07 · 20260207-015 · Connector: retry/backoff з config.json.
- 2026-02-07 · 20260207-014 · Connector: retry/backoff при помилці логіну.
- 2026-02-07 · 20260207-013 · App: fail-fast валідація config.json.
- 2026-02-06 · 20260207-001 · Supervisor: прибрано derived_force_close.
- 2026-02-06 · 20260206-030 · Ops: видалено каталоги tf_60/tf_180 у data_v3.
- 2026-02-06 · 20260206-029 · Ops: спроба видалити tf_60/tf_180 — файли зайняті.
- 2026-02-06 · 20260206-028 · Config: M5-only tail=500, прибрано live/backfill/diag ключі.

### Governance

- 2026-02-24 · 20260224-073 · Docs sync: mark P3.1-P3.5 as DONE, narrow remaining P3 scope to P3.7+P3.9 (~100 LOC).
- 2026-02-22 · 20260222-065 · P12: Dead paths cleanup — видалено ~444 LOC мертвого коду (D1-D6, D9-D10, selftests) з 6 файлів.
- 2026-02-22 · 20260222-050 · P8 rev 2: +Q11 (WS output integrity), +Q12 (ui_v4 policy bridge), GO/NO-GO updated (S1/S2/Q8 FIXED, +S19/S24).
- 2026-02-22 · 20260222-049 · P7 rev 2: +17 findings (47→64), Pattern G (WS/ui_v4), S19-S29 remediation, ws_server+ui_v4 across all sections.
- 2026-02-22 · 20260222-048 · P6 rev 2: ws_server+ui_v4 config consumers, §16.4 policy bridge, H15-H22/V1-V8 hardcodes, F8-F13 (22 policy areas).
- 2026-02-22 · 20260222-047 · P5 rev 2: +23 guards (109→132), §9.5/§9.6 ws_server+ui_v4 guards, F8-F9 (silent unknown action/frame).
- 2026-02-22 · 20260222-046 · P4 CREATED: API surface audit (949 LOC, 15 sections, 11 HTTP + WS endpoints, F1-F10 incl F4 HIGH WS no output guards).
- 2026-02-22 · 20260222-045 · P3 rev 2: повний аудит UDS store layer з file:line evidence (24 sections, 1637 LOC).
- 2026-02-22 · 20260222-044 · P1 rev 2: +ws_server (#8) +aione_top (#9) +ui_v4 (#10), section 17 role detection.
- 2026-02-21 · 20260221-024 · P5/P6/P7 docs оновлено: S1-S6 позначено FIXED (H-1,H-2,M-1,M-2,M-3 + summary tables + roadmap). Assessment 5 failing gates.
- 2026-02-21 · 20260221-023 · S5: default complete=True → False + MISSING_COMPLETE warning +_pop_bar_warnings (server.py). 6/6 tests PASS.
- 2026-02-21 · 20260221-022 · S6: TF_ALLOWLIST прибрано з buckets.py; tf_to_ms generic з optional allowlist; SSOT=config.json. 7/7 tests PASS.
- 2026-02-21 · 20260221-021 · S3: FINAL_SOURCES+SOURCE_ALLOWLIST single SSOT у core/model/bars.py; 4 дублікати прибрано (uds/ssot_jsonl/disk_layer/engine_b). 6/6 tests PASS.
- 2026-02-21 · 20260221-020 · P8 doc updates: I4 dual-plane routing + stitching calendar-awareness warning + exit gates 19/24 OK (5 pre-existing)
- 2026-02-21 · 20260221-017 · P8: Verification & Clarifications Pack — 10 questions (Q1-Q10), file:line evidence, GO/NO-GO: GO with S1+S2 conditions.
- 2026-02-21 · 20260221-016 · P7: Gap Analysis — 47 findings (2 HIGH, 10 MED, 24 LOW, 11 INFO), 6 cross-cutting patterns, remediation roadmap S1-S18.
- 2026-02-21 · 20260221-015 · P6: Config & Policy SSOT — 19 sections, 7 findings, SSOT compliance matrix.
- 2026-02-21 · 20260221-014 · P5: Contracts & Guards — 109 guards (G1-G109), 4 JSON Schema, 16 dataclass, 24 exit gates.
- 2026-02-21 · 20260221-013 · P4: API Surface — 8 HTTP endpoints, normalization pipeline, contract guards.
- 2026-02-21 · 20260221-012 · P3: UDS / Store — 23 sections, ~1411 lines, 10 findings.
- 2026-02-21 · 20260221-011 · P2: Data Flow — 16 sections, 12 findings.
- 2026-02-21 · 20260221-010 · P1: Process Inventory — 6 processes, 3 FXCM sessions, Redis topology.
- 2026-02-15 · 20260215-003 · Ліцензія: посилено (source-available, non-commercial, no-redistribution, contribution-back).
- 2026-02-15 · 20260215-001 · P0b: proof-pack Redis readiness vs TTL (stale readiness vs wrong DB/keyspace) + extract контракту /api/bars.
- 2026-02-14 · 20260214-215 · P0b: Gate Triage Pack для 6 failing gates (run_id=20260214T224659Z) з evidence → класифікацією → next action.
- 2026-02-14 · 20260214-214 · P0 re-freeze: exit-gates FAIL (NO-GO) + UI smoke baseline stable snapshot (run_id=20260214T224659Z).
- 2026-02-14 · 20260214-196 · DISCOVERY: flow-map + ownership matrix + дубль/parallel path audit (runtime/ui/docs/config).
- 2026-02-14 · 20260214-194 · DISCOVERY: діагностика порожнього UI-чарту при bars>0 (cache/version/render path).
- 2026-02-13 · 20260213-151 · Docs: dual pipeline M1/M3+M5+ — повний перепис system_current_overview (supervisor, SSOT planes, annotated tree, mermaid), README (modes, status done), ADR-0001 (wiring done, M1 poller), redis_snapshot (TTL M1/M3).
- 2026-02-12 · 20260212-116 · P2X.9-L1: LiveRecoverPolicy — auto-catchup M5 після паузи/reconnect. Rate-limit (cooldown+budget+per_cycle), collapse-to-latest, no preview. 17 тестів, gate 4/4, runbook live_recover.
- 2026-02-12 · 20260212-115 · P2X.8-S4: MarketCalendar multi-break (HKG33 lunch+afternoon) — unexpected 209→0, gate 3/3 OK, budgets tightened.
- 2026-02-12 · 20260212-114 · P2X.8-S3: targeted M5 backfill 13 sym (20575 new, 98.1% gap reduction) + gate unexpected_gap_budget 2/2 OK.
- 2026-02-11 · 20260211-113 · P2X.8-S2: MarketCalendar wrap-break fix + gap classification tool + gate 3/3 OK.
- 2026-02-11 · 20260211-112 · P2X.8-S1: rebuild_derived --all + бюджети (5s) + runbook coldstart. Gate coldstart_multisymbol 7/7 OK.
- 2026-02-12 · 20260212-115 · P2X.8-S4: MarketCalendar multi-break (HKG33 lunch+afternoon) — unexpected 209→0, gate 3/3 OK, budgets tightened.
- 2026-02-12 · 20260212-110 · P2X.8-C1: unified config loader (core/config_loader.py) + portable UI config (ui_config.json + ?api_base=) + DRY cleanup (6 copies_pick_config_path, 7+ _env_str). Gate config_singleton 6/6 OK. Видалено config.local.json.
- 2026-02-11 · 20260211-108 · P2X.6-G2: gates (preview_tail_live_shape, preview_bus_isolation, ui_live_candle_plane) + audit matrix.
- 2026-02-11 · 20260211-109 · P2X.7-D1: catch-up для H4/D1 (cold-start → gap-based) + fix on-close timing (double apply + expected_last). Gate htf_available 5/5 OK.
- 2026-02-11 · 20260211-088 · P2X.6-G1: Exit-gate preview_plane (4 sub-gates: nomix_disk, uds_hotpath, api_splitbrain, tick_schema).
- 2026-02-11 · 20260211-074 · Правило: неочікувані зміни — попередження після завершення.
- 2026-02-08 · 20260208-024 · Docs: додано requirements.txt і pyproject.toml.
- 2026-02-08 · 20260208-023 · Docs: оновлено README і поточний опис UI scrollback.
- 2026-02-07 · 20260207-065 · Analysis: опис потоку від 5m до derived та UI.
- 2026-02-07 · 20260207-066 · Analysis: перегляд tail 4h JSONL.
- 2026-02-07 · 20260207-067 · Analysis: джерела 4h/1d та derived у конфігу.
- 2026-02-07 · 20260207-068 · Analysis: утиліти rebuild/isolated для derived.
- 2026-02-08 · 20260207-069 · Analysis: інвентар writer/reader для data_v3.
- 2026-02-08 · 20260208-001 · Analysis: зміни логіки derived/Redis за 2026-02-07/08.
- 2026-02-08 · 20260208-003 · Docs: оновлено схеми/опис Redis snapshots і README.
- 2026-02-08 · 20260208-009 · Docs: README + UI/Redis схеми як опорна точка.
- 2026-02-09 · 20260209-002 · Analysis: аудит UI кешу/scrollback/Redis для стабільності live.
- 2026-02-09 · 20260209-004 · IDE: діагностика Jedi (Python 3.7 у .venv, jedi відсутній).
- 2026-02-03 · 20260203-062 · відновлено **main** для ui_chart_v3.
- 2026-02-03 · 20260203-004 · запуск UI через python -m ui_chart_v3
- 2026-02-12 · 20260212-125 · I-TAILSTITCH UI: forward-gap guard (applyUpdates reload), drop-stale, rAF render throttle.

### Runtime

- 2026-02-18 · 20260218-001 · Pause flat бари скіпаються (engine_b + m1_poller). derive.py calendar-aware.
- 2026-02-18 · 20260218-002 · M5 gap-fill: бари за watermark але відсутні на диску тепер приймаються.
- 2026-02-18 · 20260218-003 · tick_publisher: graceful shutdown замість KeyboardInterrupt traceback.
- 2026-02-16 · 20260216-013 · m1_poller: flat_bar_max_volume from config.json (SSOT), default=4, loud warning if missing.
- 2026-02-14 · 20260214-192 · Dead imports cleanup: normalize_bar, DEFAULT_TF_ALLOWLIST (uds.py).
- 2026-02-14 · 20260214-181 · P1+P2: preview updates fast-forward + adopt-tail (redis_layer).
- 2026-02-13 · 20260213-180 · Preview: close_time_ms end-excl для preview-plane (tf=60/180) в UDS.
- 2026-02-13 · 20260213-179 · VERIFY: /api/bars dt_ms після рестарту (tf=60 incl, інші excl).
- 2026-02-13 · 20260213-178 · VERIFY: процес 8089 з .venv і boot_id змінюється після рестарту.
- 2026-02-13 · 20260213-177 · VERIFY: boot_id UI + пошук normalize_bar/tf_ms-1 у коді.
- 2026-02-13 · 20260213-176 · VERIFY: SSOT JSONL end-excl для XAU/USD tf_300 (dt=tf_ms).
- 2026-02-13 · 20260213-174 · UDS/API: close_time_ms канонізовано як end-excl (без normalize incl).
- 2026-02-13 · 20260213-173 · SSOT: блокування preview/non-final записів у JSONL + лічильник.
- 2026-02-13 · 20260213-162 · Перезапуск baseline + вмикання стріму (polling+preview сумісність).
- 2026-02-13 · 20260213-159 · M1 poller: явний fetch_last_n_m1 + мінімальне очищення імпортів.
- 2026-02-13 · 20260213-158 · M1 poller: сортування + фільтр закритих барів перед ingest.
- 2026-02-13 · 20260213-157 · Увімкнено m1_poller при вимкненому стрімі (polling-only перевірка).
- 2026-02-13 · 20260213-156 · Тимчасово вимкнено tick_stream і preview_tick для перевірки відсутності M1 preview.
- 2026-02-13 · 20260213-155 · Тимчасово вимкнено m1_poller для перевірки tick-стріму.
- 2026-02-13 · 20260213-154 · FXCM FIRST_TICK: removed PREVIOUS_CLOSE from provider.py (volume alternation artifact), re-enabled UI stitching. 0 gaps all TFs. 68/68 tests.
- 2026-02-13 · 20260213-152 · M1 quality (P1+P2+P4): stitching off by default (ui_stitching_enabled=false), tick auto-promote on rollover (tick_auto_promote_m1=true, src=tick_promoted), Windows logging fix (pipe+pump). 68/68 tests. VERIFY: logs>0, promoted events in /api/updates, no stitching deformation.
- 2026-02-13 · 20260213-150 · PREVIOUS_CLOSE stitching: open[i]=close[i-1] in /api/bars for smooth candles. SSOT preserved on disk. Also removed temp diagnostic from provider.py.
- 2026-02-12 · 20260212-149 · Fix BID/MID price mismatch: tick publisher now uses BID like FXCM History finals. No more candle jumps when final replaces preview.
- 2026-02-12 · 20260212-148 · TICKR2: M1/M3 finals bridge → preview ring + /api/bars history for preview TFs. All 13 symbols have M1+M3 in UI. Finals overwrite tick previews.
- 2026-02-12 · 20260212-147 · TICKR2: M1 Poller engineering upgrade — calendar gate, watermark tracking, adaptive fetch, gap detection, calendar-aware flat bar ingest. M1 >= M5 robustness, TV-like.
- 2026-02-12 · 20260212-146 · TICKR2 C.1: M1 Poller warmup — Redis priming (276 bars) + M1Buffer bootstrap (125 M1) from disk.
- 2026-02-12 · 20260212-145 · TICKR2: M1 Poller FXCM session lifecycle fix + reconnect (120s cooldown). Live verified: 88 M1 + 24 M3, 13 symbols, 0 errors.
- 2026-02-12 · 20260212-144 · TICKR2 C.2: M1 poller + M3 derive — `m1_poller.py`, ізольований від M5 pipeline. Config: 60/180 у tf_allowlist, Redis TTL/tail, supervisor spawn.
- 2026-02-12 · 20260212-143 · H.3 P2+P3: DRY `preview_tf_allowlist_from_cfg` + `min_coldload_bars_from_cfg` → `core/config_loader.py`. H.3 завершено.
- 2026-02-12 · 20260212-142 · H.3 P1: DRY `tf_allowlist_from_cfg` + 3 константи → `core/config_loader.py`. Усунуто дублювання з server.py та uds.py.
- 2026-02-12 · 20260212-141 · H.4: Dead code removal `server.py` — 19 мертвих функцій, 6 глобалів, 3 константи, 4 імпорти. Нетто −511 LOC (1448→937).
- 2026-02-12 · 20260212-140 · H.5 DRY: `tick_common.py` — 5 спільних функцій витягнуто з tick_preview_worker, tick_publisher, main_connector. Нетто −67 LOC.
- 2026-02-12 · 20260212-130 · Tools: додано утиліту очищення Redis-кешу за namespace/scope (`tools/diag/clear_redis_cache.py`).
- 2026-02-12 · 20260212-125 · I-TAILSTITCH: TV-like live candles — forward-gap guard, render throttle (rAF), M3 derived from M1, PREVIEW_GAP detection.
- 2026-02-12 · 20260212-124 · Logging policy: один INFO REDIS_SPEC_EFF (boot) замість UDS_REDIS_SPEC_EFF per-role.
- 2026-02-12 · 20260212-123 · Логи: менше дубльованих RedisSpec/Redis init; preview TF allowlist без fallback-warning; throttling calendar_pause_nonflat.
- 2026-02-12 · 20260212-122 · P2X.6-T1: Preview M1/M3 normalisation — calendar gate + v=0 + ticks_n + polling cap 1s. Gate 4/4. Tests 66/66 OK.
- 2026-02-12 · 20260212-121 · P2X.10-CFG: Channels/prefix migrated .env → config.json "channels" section. .env тільки секрети.
- 2026-02-12 · 20260212-120 · P2X.10-FLAT: Calendar-aware flat bar policy — accept provider-sent flat during pause, anomaly warning for non-flat. Derived ігнорують calendar_pause_flat M5.
- 2026-02-12 · 20260212-119 · P2X.10-H1P1: Partial derived bars — rebuild будує H1/M15/M30/H4/D1 з calendar+extensions коли break перетинає bucket. 159 partial_built, 0 partial_no_bar.
- 2026-02-12 · 20260212-118 · P2X.10-GAPS: M5/H1 gaps → 0% unexpected: FX break 21:55-22:30, known_broker_outages, backfill 588 M5, rebuild derived, classify_h1_gaps tool.
- 2026-02-12 · 20260212-117 · Fix: усунуто шум DEBUG-логів UDS — видалено setLevel(DEBUG), read_window/window_result → DEBUG, server.py root logger → INFO.
- 2026-02-12 · 20260212-116 · P2X.9-L1: LiveRecoverPolicy — engine_b auto-catchup після паузи/reconnect з rate-limit, collapse-to-latest, degraded-but-loud. Gate 4/4 OK.
- 2026-02-12 · 20260212-115 · MarketCalendar: daily_breaks (multi-break list),_is_in_break helper, HKG33 lunch 04:00-05:00 + afternoon 08:30-09:15.
- 2026-02-11 · 20260211-113 · MarketCalendar: daily break wrap-break (start>end) fix для GER30/EUSTX50/HKG33.
- 2026-02-11 · 20260211-087 · P2X.6: Tick schema guard + 0-ticks loud detection у TickPreviewWorker.
- 2026-02-11 · 20260211-086 · P2X.6: Tick ts wallclock fallback — loud src + counter + ratio warning.
- 2026-02-11 · 20260211-084 · VERIFY: tick_publisher -> Redis SUBSCRIBE отримує ticks.
- 2026-02-11 · 20260211-083 · TickPublisher: AO2GTableListener замість O2GTableListener.
- 2026-02-11 · 20260211-082 · TickPublisher: підписка на OFFERS через O2GTableListener.
- 2026-02-11 · 20260211-081 · Config: tick_stream_* дефолти для FXCM tick publisher.
- 2026-02-11 · 20260211-078 · B2.3: FXCM TickPublisher + v-guard у TickPreview.
- 2026-02-11 · 20260211-077 · DISCOVERY: перевірено tick_agg/tick_preview_worker.
- 2026-02-11 · 20260211-075 · DISCOVERY: tick-канал порожній бо немає публікатора тика.
- 2026-02-10 · 20260210-073 · TickPreview: прибрано socket_timeout для pubsub.
- 2026-02-10 · 20260210-072 · P2X.6-B2.1: TickPreviewWorker + режим tick_preview.
- 2026-02-10 · 20260210-071 · DISCOVERY: база для P2X.6-B2 (tick канал/preview publish/профілі env).
- 2026-02-10 · 20260210-069 · P2X.6-B1: тести TickAggregator (open=first tick, late bucket drop).
- 2026-02-10 · 20260210-066 · P2X.6-B1: rail проти late bucket у TickAggregator.
- 2026-02-10 · 20260210-065 · P2X.6-B1: TickAggregator для preview з open=перший тик.
- 2026-02-10 · 20260210-064 · DISCOVERY: tick-stream docs + узгодження preview_tick з кодом.
- 2026-02-10 · 20260210-063 · VERIFY: /api/status allowlist + include_preview_ignored.
- 2026-02-10 · 20260210-060 · P2X.6-A: preview NoMix guard + include_preview ignored для final.
- 2026-02-10 · 20260210-057 · P2X.6-A: preview keyspace + UDS preview plane (runtime).
- 2026-02-10 · 20260210-051 · P2X.5-C1: UDS helper'и для tail/head/day-index.
- 2026-02-10 · 20260210-044 · VERIFY: /api/status + /api/bars (prime_incomplete зник).
- 2026-02-10 · 20260210-043 · VERIFY: /api/status + /api/bars (prime_incomplete зʼявився).
- 2026-02-10 · 20260210-042 · VERIFY: /api/status + /api/bars після рестарту (history_short).
- 2026-02-10 · 20260210-027 · UDS: _disk_bar_to_candle використовує fallback o/h/c для priming.
- 2026-02-10 · 20260210-028 · VERIFY: XAG D1 tail проходить фільтри priming.
- 2026-02-10 · 20260210-029 · VERIFY: DiskLayer data_root і last_open_ms.
- 2026-02-10 · 20260210-030 · UDS: _disk_bar_to_candle мапить на CandleBar o/h/c/v.
- 2026-02-10 · 20260210-031 · VERIFY: Redis TTL (snap/tail) + /api/bars prefer_redis.
- 2026-02-10 · 20260210-032 · DISCOVERY: db/ns mismatch та fallback у /api/bars.
- 2026-02-10 · 20260210-033 · RedisSpec SSOT + loud fallback + namespace ключ.
- 2026-02-10 · 20260210-034 · VERIFY: RedisSpec logs у UI процесі.
- 2026-02-10 · 20260210-035 · P2X.4-A rails + P2X.4-B/C prime_ready/ordering.
- 2026-02-10 · 20260210-036 · VERIFY: /api/status + /api/bars prefer_redis.
- 2026-02-10 · 20260210-037 · P2X.4-D: SSOT конфіг без ENV магії + prefer_redis без disk fallback.
- 2026-02-10 · 20260210-038 · VERIFY: P2X.4-D після рестарту.
- 2026-02-10 · 20260210-039 · P2X.4-E: розширений prime і канонічний meta.
- 2026-02-10 · 20260210-040 · P2X.4-E: prime_incomplete rail + partial у extensions.
- 2026-02-10 · 20260210-041 · P2X.4-E: history_short/effective_min у меті.
- 2026-02-10 · 20260210-026 · VERIFY: tail JSONL має open/high/close=None, але o/h/c присутні.
- 2026-02-10 · 20260210-025 · UDS: відновлено UDS_PRIME_SUMMARY та final-only фільтри у bootstrap_prime_from_disk.
- 2026-02-10 · 20260210-024 · ENV: додано AI_ONE_CONFIG_PATH для явного config.json.
- 2026-02-10 · 20260210-023 · VERIFY: connector verbose не показує UDS_PRIME_SUMMARY; ENV порожні.
- 2026-02-10 · 20260210-022 · VERIFY: Redis keys існують у db=1 для v3_local snap/tail/updates.
- 2026-02-10 · 20260210-021 · UDS: Redis spec/ping логи та final-only priming через DiskLayer.
- 2026-02-10 · 20260210-019 · VERIFY: REDIS_SNAP_PRIME не видно у поточних логах writer+UI.
- 2026-02-10 · 20260210-018 · RedisSnapshotWriter: DEBUG лог успішного write + INFO для priming.
- 2026-02-10 · 20260210-017 · VERIFY: Redis db=1 порожній для ohlcv snap/tail та updates.
- 2026-02-10 · 20260210-016 · VERIFY: BACKFILL_QUARANTINE працює; Redis клієнти на db=1.
- 2026-02-10 · 20260210-015 · Priming: src=="" трактувати як history; backfill quarantine до commit_final_bar.
- 2026-02-10 · 20260210-014 · VERIFY: disk_max_open_ms оновлено (max_open_ms=1770688800000 для XAU/XAG M5).
- 2026-02-10 · 20260210-013 · VERIFY: wm_open_ms попереду disk_max_open_ms; масові stale drops.
- 2026-02-10 · 20260210-012 · VERIFY: disk_max_open_ms (max open_ms по SSOT, без future-bar) затверджено.
- 2026-02-10 · 20260210-011 · VERIFY: знімок логів UDS updates/bars після переходу на Redis bus.
- 2026-02-10 · 20260210-010 · VERIFY: writer+UI запущено, Redis ключі XAU відсутні за шаблонами.
- 2026-02-10 · 20260210-009 · UDS: bootstrap priming Redis snapshots з диску (final-only).
- 2026-02-10 · 20260210-008 · VERIFY: writer+UI запущено, Redis keys відсутні, exit-gates повторно FAIL.
- 2026-02-10 · 20260210-007 · VERIFY: exit-gates після P2X.2/P2X.3 (часткові FAIL).
- 2026-02-10 · 20260210-005 · P2X.2/P2X.3: Redis UpdatesBus + writer через UDS.
- 2026-02-10 · 20260210-004 · UDS: writer-API (commit_final_bar/publish_preview_bar) + watermark rail.
- 2026-02-10 · 20260210-001 · DISCOVERY: write-path обходить UDS; updates читає disk tail.
- 2026-02-09 · 20260209-052 · Redis: явні init/ping логи + priming старт.
- 2026-02-09 · 20260209-050 · UDS: range-запити більше не читаються через tail.
- 2026-02-09 · 20260209-049 · DISCOVERY: конектор→SSOT/Redis, UDS read-only, UI polling cadence.
- 2026-02-09 · 20260209-048 · DISCOVERY: split-brain діагностика (UI read-path, UDS tail-range, cursor_seq).
- 2026-02-09 · 20260209-047 · TRIAGE: Redis keys XAU_USD відсутні; WRITER_DROP_STALE і DERIVED_TAIL_M5_GAPS для XAG/USD.
- 2026-02-09 · 20260209-046 · OPS: перезапуск writer+UI з файловими stdout/stderr.
- 2026-02-09 · 20260209-045 · TRIAGE: UI порт 8089 живий, /api/config ok, logs/ порожній.
- 2026-02-09 · 20260209-044 · VERIFY: smoke writer+UI після Redis key normalize (FAIL).
- 2026-02-09 · 20260209-042 · Redis key normalization + writer replay classification + updates warnings gate.
- 2026-02-09 · 20260209-041 · VERIFY: smoke writer+UI (P3/P5) з фіксацією ризиків.
- 2026-02-09 · 20260209-039 · P5: OBS_60S лічильники drop/geom/redis hit ratio.
- 2026-02-09 · 20260209-038 · P3: exit-gates thin handlers + single-writer + cursor sanity.
- 2026-02-09 · 20260209-037 · P2.4: watermark per (symbol,tf) + init через DiskLayer; loud redis_down.
- 2026-02-09 · 20260209-036 · P2.3/P2.4: UDS read-only rail + writer watermark.
- 2026-02-09 · 20260209-035 · P2.2: детермінований dedup, fix since_open_ms, min_coldload TF.
- 2026-02-09 · 20260209-034 · P2.2: dedup у disk_tail та loud degrade через geom metadata.
- 2026-02-09 · 20260209-033 · UDS: прибрано власний handler логера.
- 2026-02-09 · 20260209-032 · P2.2: сортування tail-читання JSONL у DiskLayer.
- 2026-02-09 · 20260209-031 · UDS: українські логи та базове покриття читання.
- 2026-02-09 · 20260209-030 · P2.2: hardening UDS (geom sort/dedup) + прибрано дефолт min_coldload_bars.
- 2026-02-09 · 20260209-024 · P2.1: UDS інтерфейс і шари RAM/Redis/Disk.
- 2026-02-09 · 20260209-023 · P2.0: факти read/write шляхів і меж контрактів.
- 2026-02-08 · 20260208-008 · Ops: Redis FLUSHDB (cache reset).
- 2026-02-08 · 20260208-007 · Runtime: відновлено group_logs_enabled у MultiSymbolRunner.
- 2026-02-08 · 20260207-070 · Rebuild: відновлено derived (15/30/1h) для XAU/USD з M5.
- 2026-02-08 · 20260208-002 · Ops: очищення **pycache** у .venv; Redis flushdb не виконано (Redis down).
- 2026-02-08 · 20260208-004 · Polling: warmup і rebuild винесені у окремі фази після старту.
- 2026-02-06 · 20260206-027 · Polling: прибрано залишки backfill/rebuild полів у init.
- 2026-02-06 · 20260206-026 · Аналіз: порівняння системного M5/derived з ізольованими інструментами.
- 2026-02-06 · 20260206-025 · VERIFY: rebuild 15m isolated (2026-02-05).
- 2026-02-06 · 20260206-024 · Tools: rebuild 15m з isolated M5 (day pack).
- 2026-02-06 · 20260206-023 · VERIFY: ізольований warmup 5m (XAU/USD) 5000 барів.
- 2026-02-06 · 20260206-022 · Tools: ізольований warmup 5m на 5000 барів.
- 2026-02-06 · 20260206-021 · Polling: tail-repair M5 без exp_open.
- 2026-02-06 · 20260206-020 · Polling: календар не впливає на інжест M1/M5.
- 2026-02-06 · 20260206-019 · VERIFY: 21:45/22:00 5m/15m після короткого poll.
- 2026-02-06 · 20260206-018 · Polling: довибір 5m для 21:45/22:00 через break-логіку.

- 2026-02-07 · 20260207-060 · M5 gaps: auto-backfill зі broker + not-ok state з samples.
- 2026-02-07 · 20260207-057 · P8.3: DEBUG sample missing M5 у хвості.
- 2026-02-07 · 20260207-056 · P8.3: DEBUG логи ok/не ok для хвоста M5.
- 2026-02-07 · 20260207-055 · P8.3: ok‑стан M5 хвоста за календарем + вікно warmup.
- 2026-02-07 · 20260207-054 · P8.3: skip повторний rebuild при ok стані.
- 2026-02-07 · 20260207-053 · P8.3: derived tail rebuild з диску під бюджет.
- 2026-02-07 · 20260207-052 · P8.2: tail catch-up з брокера по “дірці” M5.
- 2026-02-07 · 20260207-050 · P8.0: priming Redis tail/snap з диску під бюджет.
- 2026-02-07 · 20260207-037 · Redis snapshots: мінімальний writer + status.
- 2026-02-07 · 20260207-038 · Redis snapshots: один вузький append‑шлях.
- 2026-02-07 · 20260207-039 · P7.1: Redis snapshots verify (cold-start, TTL, status).
- 2026-02-07 · 20260207-040 · P7.1: Redis crash-test (shutdown/kill + recovery).

- 2026-02-07 · 20260207-062 · Audit: перевірка 5m cold-start/ingest/redis/ui.
- 2026-02-07 · 20260207-049 · VERIFY: exit-gates пройшли з піднятим UI.
- 2026-02-07 · 20260207-048 · Exit-gates: фікс payload_budgets + повторний прогін.
- 2026-02-07 · 20260207-047 · Док: явна помітка про end-excl в SSOT і end-incl у API.
- 2026-02-07 · 20260207-046 · VERIFY: запуск exit-gates (часткові фейли).
- 2026-02-07 · 20260207-045 · Exit-gates: rails для Redis schema, API contracts і payload budgets.
- 2026-02-07 · 20260207-036 · Docs: прибрано дубль Governance у CHANGELOG.
- 2026-02-07 · 20260207-034 · Docs: схеми supervisor stdio + приклади запусків.
- 2026-02-07 · 20260207-041 · Exit-gates: runner + P7.1 Redis gate.
- 2026-02-07 · 20260207-042 · Exit-gates: дефолтний запуск без CLI.
- 2026-02-07 · 20260207-031 · Docs: шлях без HTML у README.
- 2026-02-07 · 20260207-030 · Docs: команди запуску UI/connector/system.
- 2026-02-07 · 20260207-028 · Docs: дизайн Redis snapshots без pub/sub.
- 2026-02-07 · 20260207-027 · Docs: виправлено fenced block у схемі.
- 2026-02-07 · 20260207-026 · Docs: актуалізовано схеми polling і залежності.
- 2026-02-07 · 20260207-025 · Docs: оновлено схеми і annotated tree.
- 2026-02-07 · 20260207-022 · Docs: прибрано дубль заголовка App.
- 2026-02-07 · 20260207-021 · Docs: розширено annotated tree і схеми процесів.
- 2026-02-07 · 20260207-012 · Docs: оновлено TEMP-002, прибрано TEMP-003.
- 2026-02-06 · 20260206-008 · Формалізовано тимчасові блоки (реєстр).
- 2026-02-06 · 20260206-007 · Інженерна оцінка готовності/чистоти та частки корисного коду.
- 2026-02-06 · 20260206-004 · Cleanup: завершено переміщення History у research.
- 2026-02-06 · 20260206-003 · Cleanup: переміщено History/catalog.sqlite у research.
- 2026-02-06 · 20260206-002 · Cleanup: перенесено не-прод артефакти у research/.

### Tools

- 2026-02-16 · 20260216-013 · bars_as_is_sweep: read-only audit /api/bars across symbols×TFs with JSON+MD report.
- 2026-02-16 · 20260216-012 · htf_tail_sync: per-pair resilience (try/except per pair, result always present, totals split by error category).
- 2026-02-16 · 20260216-008 · HTF freshness: staleness watch + tail sync from FXCM + verify (before/after).
- 2026-02-16 · 20260216-006 · Fix: tv_tooltip_compare syntax + verify pytest/three_steps_proof (fake).
- 2026-02-16 · 20260216-005 · Three steps proof: api-limit tv_tooltip_compare + three_steps_proof runner + tests.
- 2026-02-16 · 20260216-004 · last_bucket_probe: перевірка derived H4 бару за open_time_ms (found/partial/dropped).
- 2026-02-16 · 20260216-002 · TV Tooltip Compare: порівняння OHLC з TradingView tooltip vs /api/bars (PASS/FAIL звіт).
- 2026-02-16 · 20260215-014 · TV CSV Compare: додано --tz, auto time-field та strict PASS (common==limit).
- 2026-02-16 · 20260215-013 · TV CSV Compare: детерміноване порівняння TradingView CSV vs /api/bars з PASS/FAIL verdict + 10 тестів.
- 2026-02-16 · 20260215-011 · Rewrite-range mode + strict exit gate: clean JSONL rewrite (atomic os.replace) + 4-condition gate + 4 тести (14 total).
- 2026-02-15 · 20260215-010 · HTF Rebuild from FXCM: controlled rebuild tool для H4/D1 барів із FXCM raw history (--dry-run/--commit + Redis refresh + 10 tests).
- 2026-02-15 · 20260215-009 · FXCM Raw Compare: diagnostic tool для порівняння API bars vs FXCM raw history (GO/NO-GO rebuild).
- 2026-02-15 · 20260215-008 · HTF Anchor Offset PATCH: mpv_proof_pack fix + hardcode_scan.py (pure Python, no ripgrep).
- 2026-02-15 · 20260215-007 · HTF Anchor Proof Pack: diagnostic tool + evidence H4/D1/H1 anchor alignment (13 sym × 3 TF).
- 2026-02-13 · 20260213-175 · Exit-gates: end-excl close_time + уточнення ADR/доків часу.
- 2026-02-13 · 20260213-169 · OPS: H4 backfill 365 днів (n=2200) для всіх символів.
- 2026-02-13 · 20260213-168 · OPS: підкачка M1(300) + rebuild M3 + backfill H4(30)/D1(365).
- 2026-02-13 · 20260213-167 · fetch_tf_backfill: підкачка M1/H4/D1 з FXCM History API.
- 2026-02-13 · 20260213-166 · Видалено tail_audit/repair_tail/m1m3_disk_audit за запитом.
- 2026-02-13 · 20260213-165 · Repair+verify: tail repair + M5 backfill + rebuild derived, gaps=0.
- 2026-02-13 · 20260213-164 · Tail repair: CLI для підтягування пропусків SSOT з History API.
- 2026-02-13 · 20260213-163 · Tail audit: інструмент перевірки gap у SSOT хвостах.
- 2026-02-13 · 20260213-161 · Gate: M1 updates contract + виняток tick_promoted у nomix.
- 2026-02-13 · 20260213-153 · Dedup M1 (77 dups видалено, 13 sym) + rebuild M3 + config_reference.md (довідник 80+ полів config.json). Audit tool m1m3_disk_audit.py.
- 2026-02-12 · 20260212-119 · gate_derived_partial: 3 підгейти (rebuild_has_partial, bars_has_extensions, classify_uses_partial).
- 2026-02-12 · 20260212-118 · classify_h1_gaps.py: новий діагностичний інструмент H1 gap-ів (structural_break, known_outage, classify_hour).
- 2026-02-12 · 20260212-115 · gate_calendar_multi_break + tighter gap budgets (100/50).
- 2026-02-12 · 20260212-114 · P2X.8-S3: fetch_m5_isolated.py +CLI (--symbol/--all/--date-to/--n/--backfill) + dedup + gate_unexpected_gap_budget.
- 2026-02-11 · 20260211-112 · P2X.8-S1: rebuild_derived.py +--all/--symbols flag, state update, batch rebuild 13/13 OK.
- 2026-02-11 · 20260211-076 · B2.2: TickSimPublisher для смоук-верифікації preview пайпа.
- 2026-02-10 · 20260210-062 · VERIFY: exit-gates report 20260210T184011Z.
- 2026-02-10 · 20260210-061 · P2X.6-A: gate no_preview_in_final_redis + уточнено preview_not_on_disk.
- 2026-02-10 · 20260210-059 · P2X.6-A: gate preview_not_on_disk.
- 2026-02-10 · 20260210-055 · VERIFY: tools.run_exit_gates (P2X.5) зелений.
- 2026-02-10 · 20260210-054 · P2X.5-C3: payload_budgets враховує prime_ready.
- 2026-02-10 · 20260210-053 · P2X.5-C2/C3: exit-gates узгоджено з RedisSpec/prime_ready.
- 2026-02-10 · 20260210-048 · VERIFY: tools.run_exit_gates (P2X.5).
- 2026-02-10 · 20260210-047 · P2X.5-B: gate_ui_no_direct_redis підтримує runner.
- 2026-02-10 · 20260210-046 · P2X.5-B: gate проти прямого Redis у UI.
- 2026-02-10 · 20260210-006 · Exit-gate: заборона прямого JsonlAppender/RedisSnapshotWriter у runtime/ingest.
- 2026-02-09 · 20260209-043 · Exit-gates: нормалізація символів Redis + scan-підказка.

### Core

- 2026-02-12 · 20260212-119 · CandleBar.extensions: Dict[str,Any] = {} — meta-поле для partial/degraded барів, backward-compatible.- 2026-02-11 · 20260211-080 · Contract: tick_v1 для тик-повідомлень.
- 2026-02-09 · 20260209-007 · P1a: додано контракти marketdata v1.
- 2026-02-06 · 20260206-001 · Оцінка готовності до масштабування та DRY/SSOT.
- 2026-02-05 · 20260205-033 · уточнено цільову структуру (calendar=signal, JSONL SSOT, без NS:commands).
- 2026-02-06 · 20260206-014 · Додано force_close процес для derived/base TF.
- 2026-02-05 · 20260205-053 · VERIFY: запуск app.main у фоні для живих логів.
- 2026-02-05 · 20260205-052 · VERIFY: запуск app.main для читання логів (stdout/stderr у файли).
- 2026-02-05 · 20260205-048 · VERIFY: запуск app.main після змін логування UI.
- 2026-02-05 · 20260205-046 · VERIFY: повторний запуск app.main (UI піднято).
- 2026-02-05 · 20260205-045 · VERIFY: запуск app.main після фіксу data_root.
- 2026-02-05 · 20260205-043 · Supervisor: прибрано --verbose аргумент.
- 2026-02-05 · 20260205-041 · VERIFY: запуск app.main (ui потребує --data-root).
- 2026-02-05 · 20260205-040 · supervisor: app/main.py для ingest+ui.
- 2026-02-05 · 20260205-032 · план переходу на strangler-структуру (1–2 P-slices).
- 2026-02-05 · 20260205-027 · правило підтвердження рекомендації перед PATCH.
- 2026-02-05 · 20260205-021 · аудит: UI, публікація свічок і live‑тиків.
- 2026-02-05 · 20260205-009 · закріплено інваріанти 1–3 та P‑slices у правилах.
- 2026-02-05 · 20260205-008 · погоджено мінімальний пояс безпеки (інваріанти 1–3).
- 2026-02-05 · 20260205-007 · аудит інваріантів 1–3 (час, update‑потік, NoMix).
- 2026-02-05 · 20260205-006 · надано загальний план робіт.
- 2026-02-05 · 20260205-005 · структура: створено core/ та runtime/.
- 2026-02-05 · 20260205-004 · архітектурна консультація: live‑потік і швидке перемикання.
- 2026-02-04 · 20260204-083 · видалено legacy з репозиторію.
- 2026-02-04 · 20260204-082 · сховано legacy та tail_guard.py.
- 2026-02-04 · 20260204-081 · push main на GitHub.
- 2026-02-04 · 20260204-080 · перейменовано гілку на main.
- 2026-02-04 · 20260204-067 · перший git коміт.
- 2026-02-04 · 20260204-066 · приховано журнали, .github/.vscode та config-и mypy/pytest.
- 2026-02-04 · 20260204-065 · README + .gitignore для репо та даних.
- 2026-02-03 · 20260203-012 · додано гілки індексу для polling/tui/packaging.
- 2026-02-03 · 20260203-011 · оновлено правила журналу: без PRE, індекс як дерево.
- 2026-02-03 · 20260203-008 · POST: аудит UI практик (Old system Connector v2).
- 2026-02-03 · 20260203-007 · PRE (legacy): аудит UI практик у Old system Connector v2.
- 2026-02-03 · 20260203-006 · додано масштабовані практики для великої системи.
- 2026-02-03 · 20260203-005 · оновлено `copilot-instructions.md` (правила PRE/PATCH/VERIFY/POST + журнал JSONL).
- 2026-02-06 · 20260206-013 · VERIFY: rebuild derived (з broker-backfill) — у процесі.
- 2026-02-06 · 20260206-012 · VERIFY: rebuild derived (помилка ModuleNotFoundError).
- 2026-02-06 · 20260206-011 · Fix: відновлено v3_polling_b для tools/*.
- 2026-02-06 · 20260206-010 · Fix: base TF після паузи + fallback derived з брокера.
- 2026-02-06 · 20260206-009 · Аналіз: побиті derived перед паузою та відсутні 4h/1d.
- 2026-02-06 · 20260206-006 · Calendar: групові overrides і мапінг symbol→group.
- 2026-02-06 · 20260206-005 · Calendar: per-symbol MarketCalendar + provider is_market_open.
- 2026-02-05 · 20260205-065 · Polling: fallback ingest при miss exp_open.
- 2026-02-05 · 20260205-064 · Publish-if-bar: прибрано календарний відсік у інжесті.
- 2026-02-05 · 20260205-063 · Calendar: правило publish-if-bar + фільтр flat-bar.
- 2026-02-05 · 20260205-062 · Polling: throttling calendar_closed + уточнення backfill stop.
- 2026-02-05 · 20260205-061 · Calendar gate: ігнор для символів (GBP/CAD).
- 2026-02-05 · 20260205-060 · Polling: календарні фільтри + системний calendar_closed.
- 2026-02-05 · 20260205-059 · Видалено порожні свічки 2026-02-05T22:02:00Z.
- 2026-02-05 · 20260205-057 · Polling: throttling для повторюваних warning.
- 2026-02-05 · 20260205-056 · Polling: календарний гейт для exp_open.
- 2026-02-05 · 20260205-055 · Polling: шумні логи у DEBUG, M1 summary — debug у multi.
- 2026-02-05 · 20260205-054 · M1: агреговано лог skip_cutoff.
- 2026-02-05 · 20260205-051 · Polling: групове логування по символах + агрегація history помилок.
- 2026-02-05 · 20260205-039 · уніфіковано CandleBar/інваріанти в core.
- 2026-02-05 · 20260205-038 · VERIFY: smoke-run після P-slice B.
- 2026-02-05 · 20260205-037 · P-slice B: винесено wiring у composition, додано lifecycle.
- 2026-02-05 · 20260205-036 · P-slice A: прибрано legacy-код з v3_polling_b.py.
- 2026-02-05 · 20260205-035 · VERIFY: smoke-run нового entrypoint (thin wrapper).
- 2026-02-05 · 20260205-034 · P-slice A: механічне винесення модулів polling (1:1).
- 2026-02-05 · 20260205-030 · P6.5.1+P6.6: retry перед heavy + відкладені backfill.
- 2026-02-05 · 20260205-028 · VERIFY: py_compile для v3_polling_b.py.
- 2026-02-05 · 20260205-026 · P6.5: heavy_budget_s з конфігу для важких задач.
- 2026-02-05 · 20260205-025 · Base TF: враховано останню торгову хвилину бакета.
- 2026-02-05 · 20260205-019 · Live: close_time_ms повернено до end‑excl для SSOT.
- 2026-02-05 · 20260205-016 · P4: канонічні бакети в core + live anchor.
- 2026-02-05 · 20260205-003 · Live: коректне закриття builders.
- 2026-02-05 · 20260205-002 · Base TF: anchor по останній торговій хвилині.
- 2026-02-04 · 20260204-168 · Видалено свічку XAU/USD 2026-02-04T22:02:00Z.
- 2026-02-04 · 20260204-167 · Base TF: fetch навіть під calendar_closed.
- 2026-02-04 · 20260204-166 · Live: multi‑symbol live_state та /api/live для вибраного символу.
- 2026-02-04 · 20260204-165 · Live: резолв шляхів live_state/store від config.json.
- 2026-02-04 · 20260204-164 · Live: стійкий запис live_state + коректне закриття writer.
- 2026-02-04 · 20260204-163 · Live: тикові логи у DEBUG.
- 2026-02-04 · 20260204-161 · Live: сумісне очікування таблиць без wait_for_tables.
- 2026-02-04 · 20260204-160 · Live: автозапуск live_candle з main.
- 2026-02-04 · 20260204-139 · Live: конфіг для вмикання/збереження стріму.
- 2026-02-04 · 20260204-138 · Live: модуль імітації живої свічки з тиками.
- 2026-02-05 · 20260205-001 · polling: календарний гейт і diag‑прапор.
- 2026-02-04 · 20260204-079 · видалено побиті 3m XAU/USD за 2025-10-02..2026-01-21.
- 2026-02-04 · 20260204-078 · VERIFY: XAG/XAU 3m на 22:00/23:00 відсутні.
- 2026-02-04 · 20260204-077 · VERIFY: XAG похідні 3/5/15/30/1h на 23:00 відсутні.
- 2026-02-04 · 20260204-076 · VERIFY: XAG похідні 3/5/15/30/1h на 22:00 існують.
- 2026-02-04 · 20260204-075 · VERIFY: XAG бари на 2026-02-02T22:00 існують.
- 2026-02-04 · 20260204-074 · VERIFY: запуск multi polling для XAU/XAG/EUR.
- 2026-02-04 · 20260204-073 · VERIFY: py_compile після multi-символів.
- 2026-02-04 · 20260204-072 · multi: стрімінг кількох символів в одному циклі.
- 2026-02-04 · 20260204-071 · VERIFY: py_compile після cold-start бази.
- 2026-02-04 · 20260204-070 · cold-start: авто-підтягування базових TF з брокера.
- 2026-02-04 · 20260204-069 · VERIFY: broker TF fetch 6 місяців (EUR/USD).
- 2026-02-04 · 20260204-068 · symbol змінено на EUR/USD для холодного старту.
- 2026-02-04 · 20260204-064 · VERIFY: py_compile після broker_base TF.
- 2026-02-04 · 20260204-063 · база TF: 1m+4h/1d з брокера, derived 3m-1h з M1.
- 2026-02-04 · 20260204-062 · VERIFY: broker TF fetch x3 (4h=900, 1d=300).
- 2026-02-04 · 20260204-061 · H4: три offsets 19:00/21:00/22:00.
- 2026-02-04 · 20260204-060 · VERIFY: broker TF fetch з D1 alt offset.
- 2026-02-04 · 20260204-059 · D1: alt offset для DST (21:00/22:00).
- 2026-02-04 · 20260204-058 · VERIFY: py_compile після D1 offset.
- 2026-02-04 · 20260204-057 · D1: окремий anchor_offset_s_d1.
- 2026-02-04 · 20260204-056 · VERIFY: broker TF fetch з per-TF offsets.
- 2026-02-04 · 20260204-055 · VERIFY: py_compile rebuild_derived.py (per-TF offsets).
- 2026-02-04 · 20260204-054 · broker TF fetch: окремі anchor offsets по TF.
- 2026-02-04 · 20260204-053 · VERIFY: broker TF fetch з anchor_offset_s=21:00.
- 2026-02-04 · 20260204-052 · VERIFY: перелік bar_bucket_misaligned у UTC.
- 2026-02-04 · 20260204-051 · VERIFY: broker TF fetch 4h/1d.
- 2026-02-04 · 20260204-050 · VERIFY: py_compile rebuild_derived.py (broker TF fetch).
- 2026-02-04 · 20260204-049 · broker TF fetch: ліміти через rebuild tool.
- 2026-02-04 · 20260204-048 · VERIFY: py_compile rebuild_derived.py (state-мітки).
- 2026-02-04 · 20260204-047 · rebuild: state-мітки ok для інкрементального проходу.
- 2026-02-04 · 20260204-046 · rebuild: tolerant‑partial лише 21:59/21:44.
- 2026-02-04 · 20260204-045 · VERIFY: py_compile rebuild_derived.py.
- 2026-02-04 · 20260204-044 · rebuild: логи в DEBUG + підсумок, ignored‑partial.
- 2026-02-04 · 20260204-042 · ігнор хвилини 2026-02-01T23:02:00Z у diag/rebuild.
- 2026-02-04 · 20260204-041 · досліджено пропуски M1 для TF=14400/86400.
- 2026-02-04 · 20260204-040 · запуск rebuild TF=1800s.
- 2026-02-04 · 20260204-039 · запуск rebuild TF=900s.
- 2026-02-04 · 20260204-038 · запуск rebuild TF=300s.
- 2026-02-04 · 20260204-035 · rebuild tool: PYTHONPATH + cwd.
- 2026-02-04 · 20260204-034 · перевірено наявність останніх барів на диску.
- 2026-02-04 · 20260204-033 · запуск rebuild TF=180 з broker-backfill.
- 2026-02-04 · 20260204-032 · rebuild: partial derived при tolerate_missing.
- 2026-02-04 · 20260204-031 · rebuild: толеранс missing_m1 для останнього бару.
- 2026-02-04 · 20260204-030 · перевірка наявності M1/TF для 2026-02-03T23:00.
- 2026-02-04 · 20260204-029 · rebuild: логи missing M1 і існуючих TF.
- 2026-02-04 · 20260204-028 · rebuild: M1Buffer охоплює весь діапазон.
- 2026-02-04 · 20260204-027 · rebuild: детальні логи missing_m1 і backfill.
- 2026-02-04 · 20260204-026 · rebuild: backfill M1/TF з брокера при пропусках.
- 2026-02-04 · 20260204-025 · запущено rebuild для TF=180s.
- 2026-02-04 · 20260204-024 · видалено внутрішній rebuild derived з core.
- 2026-02-04 · 20260204-023 · rebuild derived через окремий модуль під флаг.
- 2026-02-04 · 20260204-022 · окремий модуль для ручної перебудови derived.
- 2026-02-04 · 20260204-021 · періодичний rebuild derived + примусове закриття TF з брокера.
- 2026-02-04 · 20260204-020 · anchor-offset для старших TF у history/derived/tail-guard.
- 2026-02-04 · 20260204-019 · tail-guard: лічильники циклів і сон до вікна.
- 2026-02-04 · 20260204-018 · tail-guard: прозоре логування циклів.
- 2026-02-04 · 20260204-017 · tail-guard: старт від дати.
- 2026-02-04 · 20260204-016 · tail-guard: window + max_days.
- 2026-02-04 · 20260204-015 · прибрано зайвий diagnose_derived.
- 2026-02-04 · 20260204-014 · виправлено tf‑мапінг та utc_dt_to_ms.
- 2026-02-04 · 20260204-013 · tools: **init** для python -m.
- 2026-02-04 · 20260204-012 · діагностику винесено в tools/diag_derived.py.
- 2026-02-04 · 20260204-011 · fallback derived з брокера при пропусках M1.
- 2026-02-04 · 20260204-010 · fix weekend close/open у діагностиці.
- 2026-02-04 · 20260204-009 · діагностика M1: вивід пропусків торгових хвилин.
- 2026-02-04 · 20260204-008 · діагностика M1: boundary slip + 24x7 у DEBUG.
- 2026-02-04 · 20260204-007 · діагностика M1: торговий календар.
- 2026-02-04 · 20260204-006 · діагностика M1: on_disk + expected_24x7.
- 2026-02-04 · 20260204-005 · діагностика M1: log data_root і відсутні дні.
- 2026-02-04 · 20260204-004 · day anchor D1 = 22:00 UTC.
- 2026-02-04 · 20260204-003 · додано параметри diagnose_derived у конфіг.
- 2026-02-04 · 20260204-002 · діагностика похідних і збільшений M1Buffer.
- 2026-02-04 · 20260204-001 · оптимізовано логування M1/derived.
- 2026-02-03 · 20260203-063 · захист від помилки history при закритті ринку.
- 2026-02-03 · 20260203-061 · rebuild derived для історії + lookback 60k.
- 2026-02-03 · 20260203-060 · backfill через останні N барів до date_to.
- 2026-02-03 · 20260203-059 · seed буфера + dedup backfill + rebuild derived.
- 2026-02-03 · 20260203-058 · запуск для перевірки backfill/rebuild.
- 2026-02-03 · 20260203-056 · поступовий backfill і rebuild похідних.
- 2026-02-03 · 20260203-002 · фільтр незакритих 1m, clean shutdown, fix numpy datetime, low/l.

### TUI

- 2026-02-03 · 20260203-003 · fallback low/l + clean KeyboardInterrupt.

## Формат запису (JSONL)

- id
- ts (UTC)
- area
- initiative
- status (active|reverted)
- reverts (id)
- scope
- files
- summary
- details
- why
- goal
- risks
- rollback_steps
- notes
