# ADR-0024a: SMC Engine Self-Audit — Defect Registry & Hardening

- **Статус**: Implemented
- **Дата**: 2026-03-01
- **Автор**: Claude Opus 4.6 (Patch Master)
- **Initiative**: `smc_engine_v1`
- **Parent ADR**: ADR-0024 (SMC Engine Architecture)
- **Scope**: core/smc/, runtime/smc/, ui_v4 (SMC overlay surface)

---

## 1. Контекст і проблема

Після завершення E1+S4+E2+N1/N2/N3+D1/D2/D3 (ADR-0024) проведено повний self-audit
SMC Engine для виявлення дефектів підтримки, O(n²) ризиків та wire-format невідповідностей
перед переходом до E3 (confluence scoring).

**Scope аудиту**:
- 12 файлів `core/smc/` (1974 LOC)
- `runtime/smc/smc_runner.py` (234 LOC)
- `runtime/ws/ws_server.py` (5 SMC integration points)
- `ui_v4/`: OverlayRenderer.ts, smcStore.ts, ChartPane.svelte, types.ts
- config.json:smc (35+ параметрів)
- 147 тестів (6 test files)

**Метод**: code read + AST analysis + synthetic benchmark (500 bars) + exit gate verification.

---

## 2. Health Scorecard

| Dimension | Score | Notes |
|-----------|-------|-------|
| I0 Dependency Rule | 10/10 | exit gate: `violations=0` |
| S0 Pure Core | 10/10 | 0 I/O в core/smc/ |
| S1 Read-only | 10/10 | SmcRunner: тільки read_window |
| S2 Determinism | 9/10 | `time.time()` у computed_at_ms (cosmetic) |
| S3 Zone ID | 10/10 | canonical `make_*_id()` |
| S5 Config SSOT | 9/10 | decay params у SmcDisplayConfig замість lifecycle |
| S6 Wire Format | 7/10 | swing a==b, no trend_bias |
| Test coverage | 8/10 | 147 tests; gap: UI accumulation |
| Performance | 7/10 | 3.8ms/bar ok; full recompute O(n²) risk |
| Maintainability | 8/10 | engine.py approaching 600 LOC |
| **Overall** | **8.8/10** | |

---

## 3. Defect Registry

### 3.1 S1 — Silent Degradation / Wrong UI State

#### F1: UI swings accumulate unboundedly

- **Evidence**: `[VERIFIED ui_v4/src/stores/smcStore.ts:72-76]`
- `applySmcDelta` тільки додає `new_swings`, ніколи не прибирає старі.
- Backend caps swings у `_filter_for_display` (last 20), але delta не містить `removed_swing_ids`.
- **Наслідок**: Після 100+ delta frames UI рендерить 100+ свінгів. Memory bloat + visual noise.
- **Fix**: UI-side cap у `applySmcDelta` — `swings.slice(-MAX_SWINGS)`.

#### F2: UI zones with status='mitigated' не видаляються

- **Evidence**: `[VERIFIED core/smc/engine.py:497]`
- `_diff_snapshots.disappeared` додає zone IDs до `mitigated_zones`.
- UI `applySmcDelta` встановлює `status='mitigated'`, але не фільтрує їх.
- **Наслідок**: "Кладовище" повертається у degraded формі.
- **Fix**: `applySmcDelta` — фільтрувати zones з status='mitigated' після apply.

### 3.2 S2 — Operational Inefficiency

#### F4: compute_atr called 6× per recompute

- **Evidence**: `[VERIFIED]` — engine.py (2×), order_blocks.py (1×), fvg.py (1×), liquidity.py (1×), inducement.py (1×).
- **Наслідок**: 6 ідентичних ATR обчислень на тих самих барах.
- **Fix**: Обчислити ATR один раз в `_compute_snapshot`, передати як параметр.

#### F7: Swing `to_wire()` — a==b (invisible point)

- **Evidence**: `[VERIFIED core/smc/types.py:95-100]`
- `"a": {"t": self.time_ms, "p": self.price}, "b": {"t": self.time_ms, "p": self.price}` — swing рендериться як крапка (не лінія).
- UI OverlayRenderer малює `moveTo(a)→lineTo(b)` де a==b → невидимий мазок.
- **Fix**: Swing wire = `{id, kind, time_ms, price, label}` — point, не line. UI рендерить marker.

#### F8: SmcSnapshot.to_wire() не включає trend_bias

- **Evidence**: `[VERIFIED core/smc/types.py:133-140]`
- `to_wire()` повертає `{zones, swings, levels}`. `trend_bias` є у SmcSnapshot, але не у wire.
- **Fix**: Додати `trend_bias` до `to_wire()` + `SmcData` type.

### 3.3 S3 — Config Drift / Hygiene

#### F6: Orphan `core/smc/tests.py`

- **Evidence**: `[VERIFIED core/smc/tests.py]` — порожня заглушка.
- **Fix**: `git rm core/smc/tests.py`.

#### F10: Decay params у SmcDisplayConfig замість lifecycle

- **Evidence**: `[VERIFIED core/smc/engine.py:119-127]` — `config.display.decay_start_bars` керує lifecycle.
- **Fix**: Перемістити `decay_start_bars`, `decay_fast_bars` з SmcDisplayConfig → SmcConfig root.

#### F12: `__init__.py` не експортує SmcDisplayConfig

- **Evidence**: `[VERIFIED core/smc/__init__.py]`
- **Fix**: Додати SmcDisplayConfig до `__all__`.

---

## 4. Performance Model

### 4.1 Current Benchmark (dev machine)

| Operation | Time | Budget |
|-----------|------|--------|
| `update(500 bars)` | 3.5 ms | — |
| `on_bar()` avg | 3.8 ms | 10 ms |
| `on_bar()` max | 4.7 ms | 10 ms |

### 4.2 Worst-case Projection

- 13 symbols × 8 TFs = 104 `on_bar()` calls per M1 close
- 104 × 3.8ms = ~395ms total (serialized)
- On slow CPU: 2× → 790ms → потенційний processing lag

### 4.3 O(n²) Hot Paths

| Function | Complexity | Where |
|----------|-----------|-------|
| `_update_ob_status` | O(zones × bars) | order_blocks.py:155 |
| `_update_fvg_status` | O(zones × bars) | fvg.py:112 |
| inducement scan | O(minor_swings × window) | inducement.py:85, 122 |
| `_compute_snapshot` | full recompute per on_bar | engine.py:252 |

### 4.4 Mitigation Plan (deferred to E3)

- Incremental detect: тільки нові бари → recompute delta
- Cached ATR: один раз per snapshot (F4 — fixing now)
- Status tracking: зберігати lifecycle state, не перевіряти всі бари

---

## 5. Рішення — Hardening Patch

### 5.1 Scope (this patch)

| ID | Fix | LOC | Severity |
|----|-----|-----|----------|
| F1 | UI swing cap | ~5 | S1 |
| F2 | UI filter mitigated | ~3 | S1 |
| F4 | ATR once, pass as param | ~40 | S2 |
| F6 | Remove tests.py orphan | -9 | S3 |
| F7 | Swing wire format (point, not line) | ~15 | S2 |
| F8 | trend_bias in wire | ~5 | S2 |
| F10 | Decay → lifecycle config | ~25 | S3 |
| F12 | Export SmcDisplayConfig | 1 | S3 |

### 5.2 Deferred (E3 / separate ADR)

| ID | What | Why deferred |
|----|------|-------------|
| F3 | Incremental detect | Architecture change → separate ADR |
| F5 | find_impulse_start | OB quality improvement, not a bug |
| F9 | Document two-cap design | Docs only |

---

## 6. Наслідки

- UI SMC rendering буде коректним: capped swings, no stale zones
- Wire format стане повнішим: trend_bias + proper swing points
- Config hierarchy чистіша: decay = lifecycle concern
- ATR cache усуває 5 зайвих обчислень per recompute
- Prepare for E3: trend_bias + clean wire format = foundation

## 7. Rollback

```bash
git checkout core/smc/engine.py core/smc/config.py core/smc/types.py core/smc/__init__.py
git checkout ui_v4/src/stores/smcStore.ts ui_v4/src/types.ts
git checkout ui_v4/src/chart/overlay/OverlayRenderer.ts ui_v4/src/layout/ChartPane.svelte
git checkout config.json
```

## 8. Verification (Post-Implementation)

- **147 SMC tests**: PASS (0 regressions)
- **395 total tests**: PASS (7 pre-existing failures, unrelated)
- **Vite build**: clean (272.22 kB JS, 11.12 kB CSS)
- **dependency_rule gate**: ok (files=115, violations=0)
- **Changelog**: 20260301-012
