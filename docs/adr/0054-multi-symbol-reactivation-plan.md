# ADR-0054: Multi-Symbol Re-Activation Plan — Phased Rollout with Regression Net

- **Статус**: Proposed
- **Дата**: 2026-04-24
- **Автор**: Patch Master (GitHub Copilot, "Стас-агент")
- **Initiative**: `multi_symbol_reactivation_v1`
- **Supersedes (partial)**: ADR-0025 §"Rollback" (replaces vague "повторити audit/rebuild" with concrete phased plan)
- **Related**: ADR-0023 §"Multi-symbol rollout" (per-group D1 anchor blocker), ADR-0037 (Binance multi-symbol precedent), ADR-0038 (per-symbol backfill isolation), ADR-0005 (mid-session gap tolerance)

---

## Quality Axes

- **Ambition target**: R3 (architectural — встановлює safety contract для multi-symbol expansion на наступні ~3 місяці)
- **Maturity impact**: M3 → M4 (elevates — додає regression net + isolation guarantee, без яких розширення = регрес)

---

## 1. Контекст і проблема

### 1.1 Поточний стан (April 2026)

`config.json:symbols` = 4 активних символи:

- **XAU/USD, XAG/USD** (FXCM, ADR-0025 focus)
- **BTCUSDT, ETHUSDT** (Binance, ADR-0037)

`data_v3/` містить історичні дані для **15 символів** (ще 11 dormant):
EUSTX50, GBP/CAD, GER30, HKG33, NAS100, NGAS, NZD/CAD, SPX500, US30, USD/CAD, USD/JPY.

### 1.2 Історія (ADR-0025, Feb 2026)

Планувалась поетапна активація мульти-символьної роботи (Потік B). Audit показав:

- ✅ M1 дані чисті для всіх 13 символів (3-5 місяців історії)
- ✅ 0 дублікатів
- ✅ Calendar mapping налаштований для всіх груп (`fx_24x5`, `cfd_us_22_23`, `cfd_eu_21_07`, `cfd_hk_main`, `crypto_24x7`)
- ✅ Архітектура multi-symbol — всі воркери ітерують `config.json:symbols`
- ❌ **Derived TF integrity порушена** (M5/M15/H1/H4/D1 побиті після rebuild для не-XAU символів)

**Рішення тоді**: відкласти Потік B, залишитись на XAU/USD до фіксу integrity issues.

### 1.3 3-місячний emergent pattern (Feb–April 2026)

Спостереження користувача (документується тут вперше):

> "Робиш роботу над символом A → ламається символ B → ремонтуєш B → ламається C. Цикл триває 3 місяці."

**Діагностичний висновок**: символи **не ізольовані**. Десь є **shared state** який каскадить failures між символами. Підозрілі точки:

| Suspect | Чому | Файл/місце |
|---|---|---|
| `_derived_tail_state.json` | Один файл на всі символи. Non-atomic write при rebuild одного → corruption інших | `data_v3/_derived_tail_state.json` |
| Redis namespace `v3_local` (db=1) | Всі символи в одному db. Patterns delete без чіткого префіксу → cross-symbol wipe | `runtime/store/redis_keys.py` |
| Bootstrap "all symbols" loops | Якщо один символ кидає exception, інші можуть отримати partial init | `app/main.py`, `runtime/store/uds.py` bootstrap |
| Shared derive worker | Один процес на всі символи. Corrupt M1 для NGAS може зачепити пам'ять/лічильники | `runtime/ingest/derive_engine.py` |
| Shared HTF tail accumulator | `_HTFRunningAccumulator` — потенційно cross-symbol leak (потребує перевірки) | ADR-0044 |

### 1.4 Чому "просто додати символ" — антипатерн

Без isolation guarantee кожне додавання символу = **roulette** на existing символи. Це порушує:

- **I3 (Final > Preview)** — corrupt derived bars з'являються як final
- **I5 (Degraded-but-loud)** — failures каскадять silently без alerting
- **F9 (Craftsmanship-First)** — patch який "ставиться і дивимось" = production-grade від першого commit violation

ADR-0025 закрив проблему **тимчасово** (XAU only). Цей ADR відкриває її **назавжди** через systematic plan.

### 1.5 Бізнес-контекст (важливо)

Платформа має **дві окремі продуктові траєкторії**:

| Продукт | Audience | Symbol scope | Чому |
|---|---|---|---|
| **Архі (trader-v3/)** | 1 owner (proprietary AI agent) | XAU only | Поки Архі не доведе edge на одному символі — розширення = розфокус |
| **V3 chart UI (ui_v4/)** | Trader community (Discord/Telegram) | Wider basket | Більше символів = ширша audience = engagement з SMC overlay/signals |

Цей ADR стосується **тільки V3 platform/UI**. Архі залишається на XAU незалежно від rollout статусу.

---

## 2. Альтернативи

### A. Status quo — продовжуємо XAU-only

- **Pro**: нуль ризику для existing продакшну
- **Con**: V3 chart product не масштабується, користувацька цінність обмежена 1 символом, ROI on UI/SMC investment низький
- **Verdict**: REJECT — суперечить продуктовій стратегії (chart ≠ trader)

### B. "Big bang" — активувати всі 11 символів одночасно

- **Pro**: одна сесія роботи, повний rollout
- **Con**: повторює помилку ADR-0025 (no isolation guarantee), blast radius = всі символи + existing 4, неможливо локалізувати regression
- **Verdict**: REJECT — порушує F6 (patch-цикл, ≤150 LOC), I5 (degraded-but-loud impossible at scale)

### C. Per-symbol weekly rollout БЕЗ regression net (наївний)

- **Pro**: по 1 символу/тиждень = малий blast radius per slice
- **Con**: 3-місячний pattern показує — без detection mechanism для cross-symbol contamination ми **не побачимо** як NAS100 зламає XAU доки трейдер не поскаржиться
- **Verdict**: REJECT — це і є той цикл який мучив 3 місяці

### D. Phased rollout WITH regression net + isolation investigation (CHOSEN)

- **Pro**: розриває цикл через **detection-first** approach. Кожна фаза має stop-rule. Якщо contamination знайдена — Фаза 3 відкладається, фокус на fix
- **Con**: ~2.5 місяці до повного rollout (vs ~3 тижні big bang)
- **Verdict**: ACCEPT — єдиний варіант сумісний з F9, I5, та продуктовою стратегією

---

## 3. Рішення

Three-phase rollout, **кожна фаза має explicit gate** перед переходом до наступної.

### Фаза 1 — Regression Net (1-2 дні)

**Ціль**: створити sentinel який детектує regression на **already healthy** символах перед/після кожної дії.

**Deliverable**: `tools/symbol_health_check.py` (~80 LOC, read-only).

**Specification**:

```
python -m tools.symbol_health_check --symbols XAU/USD,XAG/USD,BTCUSDT,ETHUSDT
  → For each symbol × TF in [60, 300, 900, 3600, 14400, 86400]:
      - last_bar_age_s (max acceptable per calendar group)
      - broken_bars_count (rolling 1000 bars window)
      - cascade_integrity (M5 derived from M1 == M5 stored?)
      - holes_count (gaps not explained by calendar)
  → Output JSON: GREEN / YELLOW / RED per (symbol, TF)
  → Exit code: 0 if all GREEN, 1 if any RED, 2 if any YELLOW
  → Snapshot baseline → tools/health_baseline.json (gitignored)
```

**Exit gate Фази 1**:

- Tool існує + tested
- Baseline snapshot створений для 4 active символів = 100% GREEN
- Tool integrated у workflow — запускається **before** і **after** будь-якого `rebuild_from_m1` / config зміни

**Якщо baseline НЕ 100% GREEN на існуючих 4** → STOP, не йти у Фазу 2. Спочатку фіксити existing health.

### Фаза 2 — Isolation Investigation (2-3 дні)

**Ціль**: довести або спростувати гіпотезу cross-symbol contamination.

**Test scenario**:

1. `health_check` → snapshot HEALTH-A (XAU/XAG/BTC/ETH = baseline)
2. Дозволити cleanup state: видалити `data_v3/NAS100/tf_*/` крім M1
3. `python -m tools.rebuild_from_m1 --symbol NAS100` каскадом для всіх derived TF
4. `health_check` знову → HEALTH-B
5. Diff HEALTH-A vs HEALTH-B на existing 4 символах

**Outcomes**:

| Diff | Інтерпретація | Action |
|---|---|---|
| **No regression** на existing 4 | Isolation OK. Проблема ADR-0025 була в derived rebuild logic, not shared state | Перейти у Фазу 3 з confidence |
| **Regression** на ≥1 existing | Confirmed contamination point. Зафіксувати **який саме** symbol/TF постраждав | STOP, окремий ADR "Per-symbol Isolation Requirements" + fix перед Фазою 3 |

**Deliverable**: investigation report у `reports/multi_symbol_isolation_audit_<date>.md` + (за потреби) новий ADR з isolation patches.

**Exit gate Фази 2**:

- Test scenario виконаний
- Якщо contamination знайдена — фіксована та повторно перевірена → no regression
- Знання yes/no про contamination зафіксоване як evidence для майбутніх змін

### Фаза 3 — Per-Symbol Rollout (по 1/тиждень, ~10 тижнів)

**Order rationale**: від найбезпечніших (same calendar group as XAU) до найскладніших (HK calendar з lunch breaks, illiquid CFDs).

| Тиждень | Symbol | Calendar group | Чому в цей порядок | Risk markers |
|---|---|---|---|---|
| W1 | **NAS100** | `cfd_us_22_23` | Same group as XAU. Найвищий impact для US trader audience | — |
| W2 | **SPX500** | `cfd_us_22_23` | Same group, validation того що W1 не fluke | Lower volatility baseline |
| W3 | **US30** | `cfd_us_22_23` | Завершуємо US CFD set | — |
| W4 | **GER30** | `cfd_eu_21_07` | Перший EU symbol — тестуємо EU calendar для всієї групи | EU calendar ще не validated end-to-end |
| W5 | **EUSTX50** | `cfd_eu_21_07` | EU set completion | Calendar regression test |
| W6 | **USD/JPY** | `fx_24x5` | Перший FX — окрема calendar group, Asian session важлива для SMC | — |
| W7 | **GBP/CAD** | `fx_24x5` | FX set | — |
| W8 | **NZD/CAD, USD/CAD** | `fx_24x5` | FX completion (можна 2 за тиждень якщо W6/W7 clean) | — |
| W9-10 | **HKG33** | `cfd_hk_main` | **Найскладніший** — 2 lunch breaks per day, HK calendar | Calendar edge cases (ADR-0005) |
| W11 | **NGAS** | `cfd_us_22_23` | Залишаємо на кінець як edge case | Historically illiquid (ADR-0005), mid-session gap tolerance applies |

**Per-symbol activation procedure** (recurring):

1. `health_check --symbols <ALL_ACTIVE>` → snapshot HEALTH-PRE = всі GREEN (else STOP)
2. `python -m tools.rebuild_from_m1 --symbol <NEW>` каскадом всі TF
3. Run `tools/run_exit_gates.py --filter-symbol <NEW>`
4. Add `<NEW>` to `config.json:symbols` (single-line patch, <5 LOC)
5. Restart platform: `python -m app.main --mode all --stdio pipe`
6. `health_check --symbols <ALL_ACTIVE + NEW>` → HEALTH-POST
7. Якщо HEALTH-POST не GREEN на existing символах → **immediate rollback** (revert config + supervisor restart)
8. Watch period 3-5 trading days перед W+1 початком

**Exit gate Фази 3 (per week)**:

- HEALTH-POST = 100% GREEN на всіх activated символах
- Trader smoke test: SMC overlay рендериться, signals генеруються (якщо TDA enabled), drawings працюють
- Жодного rollback за watch period

### Per-Group D1 Anchor — окрема залежність (з ADR-0023)

ADR-0023 §"Multi-symbol rollout" зазначає: GER30 daily break = 21:00 UTC, HKG33 = 19:00 UTC, не 22:00 (default `d1_anchor_offset_s = 79200`). Поточна архітектура — **global** anchor.

**Не блокер для Фаз 1-2** (D1 derive працює коректно для US group + crypto). **Блокер перед W4 (GER30)**.

**Patch перед W4**: розширити `config.json` schema до per-group anchor:

```json
"calendar_groups": {
  "cfd_eu_21_07": { "d1_anchor_offset_s": 75600, ... }
}
```

- `core/buckets.py:resolve_d1_anchor(symbol)` lookup. Окремий ADR (~ADR-005X) перед W4.

### Performance gate (з ADR-0023 §"Performance")

При 13 символах × 8 TF × календар checks: ADR-0023 оцінив ~27M `is_trading_fn` calls/day. Перед W4 виміряти actual rate і додати caching якщо >5M/day. Окремий patch якщо потрібно.

---

## 4. Наслідки

### Pro

- ✅ Розриває 3-місячний цикл "fix A → break B" через detection-first
- ✅ Кожен символ додається з explicit gate, можна зупинитись на будь-якому тижні
- ✅ Архі (XAU only) ізольований від rollout — нульовий impact на trader-v3/
- ✅ V3 chart product росте з audience потенціалом (US CFDs → EU → FX → niches)
- ✅ Документує shared-state hypothesis як evidence — навіть якщо Фаза 2 покаже "no contamination", це знання виключає клас гіпотез

### Con

- ⚠️ ~2.5 місяці до повного rollout (vs ~3 тижні big bang)
- ⚠️ Потребує дисципліни — health_check має запускатись **завжди** перед/після config зміни
- ⚠️ Якщо Фаза 2 знайде contamination, Фаза 3 відкладається на тиждень-два для fix

### Risks

| Risk | Severity | Mitigation |
|---|---|---|
| `health_check` сам має bug → false GREEN | M | Spot-check manually на знайомому символі перед прийняттям baseline |
| Rollback config зміни залишає Redis з partial state | H | Procedure step 7 включає `redis-cli --pattern v3_local:*:<NEW>:* del` |
| Trader сприйняв новий символ → rollback ламає його flow | M | Watch period 3-5 days перед next symbol; communicate rollouts |
| Per-group D1 anchor patch (W4) затримується → блокує EU symbols | M | Можна reorder: W4-W5 → USD/JPY раніше, GER30/EUSTX50 пізніше |

### Maturity progression

- M3 → M4: додаємо health check (observability), isolation evidence (architectural knowledge), explicit gates (process discipline)
- Якщо Фаза 2 виявить + fix shared state → M4 → M5 (production-grade isolation guarantees)

---

## 5. Rollback

Цей ADR — **plan**, не code зміни. Rollback = відмовитись від плану та повернутись до ADR-0025 stance (XAU only).

Per-symbol rollback (всередині Фази 3):

```bash
# 1. Revert config.json (видалити symbol з symbols array)
git checkout config.json

# 2. Restart platform
python -m app.main --mode all --stdio pipe

# 3. Cleanup Redis state для символу (запобігає stale reads)
redis-cli -n 1 --scan --pattern "v3_local:*:<SYMBOL>:*" | xargs -r redis-cli -n 1 del

# 4. health_check verify
python -m tools.symbol_health_check --symbols <REMAINING_ACTIVE>
```

Disk data (`data_v3/<SYMBOL>/`) НЕ видаляємо — залишається для re-attempt.

---

## 6. Verification (after each phase)

| Phase | Verify command | Expected |
|---|---|---|
| 1 | `python -m tools.symbol_health_check --symbols XAU/USD,XAG/USD,BTCUSDT,ETHUSDT` | exit 0, all GREEN |
| 2 | Diff HEALTH-A vs HEALTH-B per scenario above | No regression OR documented + fixed contamination |
| 3 (each W) | `health_check` post-add | All previously-GREEN symbols still GREEN |

---

## 7. Open Questions

1. **Чи треба separate ADR для per-group D1 anchor**, чи patch під цим ADR? — Рекомендація: окремий ADR-005X перед W4, бо торкається `config.json` schema + `core/buckets.py`.
2. **Чи ввімкнути health_check у CI** як gate проти PR що змінює config? — Defer: прийняти рішення після Фази 1 експерименту.
3. **Чи активувати Binance Futures multi-symbol паралельно** (ETH/USDT, SOL/USDT тощо)? — Defer: не входить у scope цього ADR. Окремий roadmap.

---

## 8. Notes

- **Цей ADR — Proposed**. Не імплементується доки не Accepted (per K5 ADR Status Gate).
- **K5 enforcement**: `config.json:symbols` НЕ змінюється доки ADR не Accepted + Фаза 1 не пройшла.
- **Cross-repo isolation (X31)**: цей ADR — platform scope (`docs/adr/`). Жодних змін у `trader-v3/`.
- **Continuation**: якщо роботу перейме інший агент (Claude Code) — цей ADR = SSOT plan. Не переробляти, не переписувати без supersede.

---

## Changelog

- 2026-04-24: Created (Proposed). Documents 3-month emergent pattern + 3-phase plan. Supersedes ADR-0025 §"Rollback".
