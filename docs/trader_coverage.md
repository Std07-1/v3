# Trader Concepts Coverage Map

> **Аудиторія**: Розробники та AI-агенти, що змінюють `core/smc/`, `runtime/smc/`, `ui_v4/` або ADR.
> **Мета**: Перш ніж торкатись будь-якого компонента SMC — перевір цю таблицю. Тут зопоставлено кожен трейдерський концепт з ADR, що захищає його контракт, і модулем де він реалізований.
> **SSOT**: Ця таблиця синхронізована з `docs/adr/index.md` і `.github/role_spec_trader_v1.md` (§1.1–§1.3).
> **Остання перевірка**: 2026-03-14

---

## Правило читання

```
Перш ніж змінити X → знайди X у колонці "Концепт" → перечитай ADR у колонці "ADR" → 
перевір модуль у колонці "Де в коді" → запусти `pytest tests/test_smc_*.py` після зміни.
```

---

## 1. Матриця покриття

| Концепт | Trader spec (§) | ADR (canonical) | Де в коді | UI компонент | Статус |
|---------|----------------|-----------------|-----------|--------------|--------|
| **Order Block (OB)** | §1.1 | [ADR-0024](adr/0024-smc-engine.md) §E1, [ADR-0024c](adr/0024c-smc-zone-poi-rendering.md), [ADR-0029](adr/0029-confluence-scoring.md) | `core/smc/order_blocks.py` | `OverlayRenderer.ts:renderZones()` | ✅ Повністю |
| **Fair Value Gap (FVG)** | §1.1 | [ADR-0024](adr/0024-smc-engine.md) §E1, [ADR-0024c](adr/0024c-smc-zone-poi-rendering.md), [ADR-0029](adr/0029-confluence-scoring.md) | `core/smc/fvg.py` | `OverlayRenderer.ts:renderZones()` | ✅ Повністю |
| **IFVG (Inverted FVG)** | — | [ADR-0034](adr/0034-advanced-market-analysis-tda.md) P0 | `core/smc/fvg.py` (invert flag) | `OverlayRenderer.ts:renderZones()` | ✅ P0 реалізований |
| **Breaker Block** | — | [ADR-0034](adr/0034-advanced-market-analysis-tda.md) P1 | `core/smc/order_blocks.py` (breaker_bull/bear) | `OverlayRenderer.ts:renderZones()` | ✅ P1 реалізований |
| **BOS / CHoCH (Structure)** | §1.1 | [ADR-0024](adr/0024-smc-engine.md) §E1 | `core/smc/structure.py` | `OverlayRenderer.ts:renderSwings()` | ✅ Повністю |
| **Liquidity (BSL/SSL/EQH/EQL)** | §1.1 | [ADR-0024](adr/0024-smc-engine.md) §E2, [ADR-0024b](adr/0024b-smc-engine-smc-key-levels.md) | `core/smc/liquidity.py` | `OverlayRenderer.ts:renderLevels()` | ✅ Повністю |
| **Key Levels (PDH/PDL/PWH/PWL)** | §1.3.2 | [ADR-0024b](adr/0024b-smc-engine-smc-key-levels.md) | `core/smc/key_levels.py` | `OverlayRenderer.ts:renderLevels()` | ⚠️ Частково (Partially Implemented) |
| **Inducement** | §1.1 | [ADR-0024](adr/0024-smc-engine.md) §E2 | `core/smc/inducement.py` | `OverlayRenderer.ts:renderLevels()` | ✅ Повністю |
| **Premium / Discount (P/D)** | §1.1 | [ADR-0024](adr/0024-smc-engine.md) §E1, [ADR-0024c](adr/0024c-smc-zone-poi-rendering.md) | `core/smc/premium_discount.py` | `OverlayRenderer.ts:renderPremiumDiscount()` | ✅ Повністю |
| **Zone POI Grade (A+/A/B/C)** | §1.1, §1.4, §2.1 | [ADR-0029](adr/0029-confluence-scoring.md) | `core/smc/confluence.py` | grade badge у `OverlayRenderer.ts` | ✅ Повністю |
| **Zone lifecycle (active/mitigated/decay)** | §4.3 | [ADR-0024c](adr/0024c-smc-zone-poi-rendering.md) Z1–Z10, [ADR-0024](adr/0024-smc-engine.md) §N1 | `core/smc/engine.py` | `OverlayRenderer.ts` (opacity TTL) | ✅ Повністю |
| **Display budget / Elimination** | §2.2 | [ADR-0028](adr/0028-v2-elimination-engine.md) | — | `ui_v4/src/chart/overlay/DisplayBudget.ts` | ✅ Повністю |
| **Cross-TF projection** | §1.2, §2.2 | [ADR-0030-alt](adr/0030-alt-tf-sovereignty.md) | — | `OverlayRenderer.ts` (TF opacity + dashed) | ✅ Повністю |
| **Bias Banner (multi-TF trend bias)** | §1.3.5 | [ADR-0031](adr/0031-bias-banner.md) | `runtime/smc/smc_runner.py:get_bias_map()` | `BiasBanner.svelte` | ✅ Повністю |
| **Context Flow (Multi-TF Narrative)** | §1.3.5 | [ADR-0033](adr/0033-context-flow-narrative.md) | `ui_v4/src/narrativeEngine.ts` | `NarrativePanel.svelte` | ✅ Повністю |
| **Sessions (Asia/London/NY) H/L** | §1.3.2 | [ADR-0035](adr/0035-sessions-killzones.md) | `core/smc/sessions.py` | `OverlayRenderer.ts` (session levels) | ✅ Повністю |
| **Killzones (London 07–10 / NY 12–15 UTC)** | §1.3.2 | [ADR-0035](adr/0035-sessions-killzones.md) | `core/smc/sessions.py:classify_killzone()` | `OverlayRenderer.ts` (killzone shading) | ✅ Повністю |
| **Session Sweeps (F9 sweep confluence)** | §1.3.2, §4.5 | [ADR-0035](adr/0035-sessions-killzones.md) §F9 | `core/smc/sessions.py:detect_session_sweep()` | confluence factor у scoring | ✅ Повністю |
| **Displacement candles** | §1.3.4 | [ADR-0024](adr/0024-smc-engine.md) (8 детекторів), [ADR-0024a](adr/0024a-smc-engine-self-audit.md) | `core/smc/momentum.py:detect_displacement()` | `OverlayRenderer.ts` (displacement_bull/bear markers) | ✅ Повністю |
| **Momentum map** | §1.3.4, §1.3.5 | [ADR-0024a](adr/0024a-smc-engine-self-audit.md), [ADR-0033](adr/0033-context-flow-narrative.md) | `runtime/smc/smc_runner.py:get_momentum_map()` | `BiasBanner.svelte`, WS frame `momentum_map` | ✅ Повністю |
| **Williams Fractals (display-only)** | §1.3.1 | [ADR-0034](adr/0034-advanced-market-analysis-tda.md) §F5 (display-only, без Protected Fractal) | `core/smc/swings.py:detect_fractals()` | `OverlayRenderer.ts` (fractal_high/low markers) | ⚠️ Реалізовано як display-only markers. Protected Fractal (HTF+LTF confirmation) — **не реалізований**. |
| **Overlay Level Rendering (L1–L6)** | §2.2 | [ADR-0026](adr/0026-overlay-level-rendering-rules.md) | — | `OverlayRenderer.ts:renderLevels()` | ✅ Повністю |
| **Overlay Zone Rendering (Z1–Z10)** | §2.2 | [ADR-0024c](adr/0024c-smc-zone-poi-rendering.md) | — | `OverlayRenderer.ts:renderZones()` | ✅ Повністю |
| **IOFED Drill (5 кроків)** | §1.3.3 | **Немає ADR** | **Не реалізований в backend** | **Не реалізований** | ❌ Концептуальний фреймворк. Визначений тільки в `role_spec_trader_v1.md` §1.3.3 і `.claude/agents/smc-trader-validator.md`. Реалізація потребує нового ADR. |

---

## 2. Gaps (що не захищено ADR або не реалізовано)

### GAP-1: IOFED Drill — не реалізовано і немає ADR

**Що це:** 5-крокова процедура точного входу (HTF POI → Price in zone → LTF CHoCH → LTF OB/FVG → SL/TP). Описана в `role_spec_trader_v1.md` §1.3.3.

**Чому gap:** Не реалізовано ні в `core/smc/`, ні в `runtime/smc/`, ні в `ui_v4/`. Немає ADR, що описує архітектуру реалізації.

**Ризик:** Не можна "зламати" те чого немає, але якщо хтось почне реалізацію без ADR — ризик I0/I1 порушень.

**Якщо хочеш реалізувати:** Спочатку `MODE=ADR` → новий ADR-0039 (або наступний номер). Тільки після Accepted ADR → `MODE=BUILD`.

### GAP-2: Williams Fractals — без standalone ADR

**Що це:** `detect_fractals()` в `core/smc/swings.py` — Williams 5-свічковий паттерн, kind=`fractal_high`/`fractal_low`.

**Де задокументовано:** Побіжно в ADR-0034 §F5 як "display-only markers, protected fractal = not implemented". Немає секції з власним контрактом (period, wire format, UI rules).

**Ризик середній:** Хтось міняє `fractal_period` або wire format `fractal_high/low` — перевірити ADR-0034 і tests/test_smc_e1.py (де detect_fractals тестується у складі engine).

### GAP-3: ADR-0024b — Partially Implemented

**Що це:** Key Levels (PDH/PDL/DH/DL, EQH/EQL, session opens) — реалізовані частково.

**Ризик:** Не всі заплановані kind-и реалізовані. При додаванні нового kind → оновити ADR-0024b статус і tests.

---

## 3. Тестова прив'язка

| Концепт | Основний тест | Що покриває |
|---------|---------------|-------------|
| OB/FVG/Confluence | `tests/test_smc_e1.py`, `tests/test_smc_confluence.py` | Детекція, scoring 8 факторів, grade |
| Liquidity/Inducement | `tests/test_smc_e2_liquidity.py`, `tests/test_smc_e2_pd_inducement.py` | BSL/SSL/EQH, mitigation, inducement biased zones |
| Zone lifecycle | `tests/test_smc_n1_lifecycle.py` | active→mitigated→evicted, decay |
| Key Levels | `tests/test_smc_key_levels.py` | PDH/PDL/DH/DL detection |
| Displacement | `tests/test_smc_e1.py` (через engine) | displacement_bull/bear у snapshot |
| Sessions/Killzones | `tests/test_smc_sessions.py` | Asia/London/NY H/L, F9 sweep, killzone classify |
| D1 display filter | `tests/test_smc_d1_display_filter.py` | D1 zone TTL + visibility rules |
| SmcRunner (warmup, on_bar) | `tests/test_smc_runner.py` | Runtime wiring, delta, performance < 50ms |
| Fractals | `tests/test_smc_e1.py` (fractal_high/low у swings) | detect_fractals() output |

---

## 4. Quick-check перед змінами

```bash
# Після будь-яких змін у core/smc/:
python -m pytest tests/test_smc_e1.py tests/test_smc_confluence.py tests/test_smc_sessions.py tests/test_smc_runner.py -v

# Після змін у confluence scoring (ADR-0029):
python -m pytest tests/test_smc_confluence.py -v

# Після змін у sessions (ADR-0035):
python -m pytest tests/test_smc_sessions.py -v

# Повна SMC suite:
python -m pytest tests/test_smc_*.py -v
```

---

## 5. Навігація до деталей

| Хочу дізнатись... | Читай |
|------------------|-------|
| Повна архітектура SMC engine | [ADR-0024](adr/0024-smc-engine.md) |
| Чому зона отримала grade X | [ADR-0029](adr/0029-confluence-scoring.md) — 8 факторів F1–F8 детально |
| Правила відображення зон на canvas | [ADR-0024c](adr/0024c-smc-zone-poi-rendering.md) Z1–Z10 |
| Правила відображення рівнів | [ADR-0026](adr/0026-overlay-level-rendering-rules.md) L1–L6 |
| Як працює display budget | [ADR-0028](adr/0028-v2-elimination-engine.md) |
| Сесії та killzones | [ADR-0035](adr/0035-sessions-killzones.md) |
| Narrative / Context Flow | [ADR-0033](adr/0033-context-flow-narrative.md) |
| Wire format (Python→TypeScript) | `core/smc/types.py` → `ui_v4/src/types.ts` |
