# ADR-0051: TDA Cascade Trigger Timing — Defer to After London Close

- **Status**: Accepted
- **Date**: 2026-04-18
- **Author**: Стас + Claude (R_PATCH_MASTER)
- **Initiative**: `tda_calibration_2026_04`
- **Related ADRs**: ADR-0040 (TDA Cascade Signal Engine)
- **Severity**: S1 (silent zero-signal degradation)

---

## Quality Axes

- **Ambition target**: R3 (root cause fix + replay validation, not patch-and-pray)
- **Maturity impact**: M3 → M3 (consolidates correctness; restores intended signal generation)

---

## 1. Контекст і проблема

### 1.1 Симптом

TDA cascade (ADR-0040) працює в проді з кінця березня. За 14 днів спостережень
(04.04–17.04, 4 символи XAU/XAG/BTC/ETH):

- **28 cascade ticks** залогованих як `TDA_CASCADE_NO_SIGNAL`
- **0 signals** згенеровано
- **100%** rejections at `failed_stage=s3_session`
- Distribution: пізній березень — `narrative=COUNTER_TREND`, мід-квітень —
  `narrative=NO_NARRATIVE` (`sweep_direction=None`)

Backtest 2026-03 показав 75% WR / +2.97R / **9 signals за 49 днів** на тих
самих параметрах. Live = 0/14d. Розрив 100%.

### 1.2 Root cause (verified)

[`runtime/smc/tda_live.py:118-130`](../../runtime/smc/tda_live.py#L118-L130) тригерить
cascade на **першому complete M15 барі в London window**:

```python
bar_hour = (bar.open_time_ms - day_ms) // _MS_PER_HOUR
if (
    self._cfg.london_start_hour_utc      # = 8
    <= bar_hour
    < self._cfg.london_end_hour_utc      # = 13
):
    self._run_cascade(...)
```

Default `london_start_hour_utc=8` → перший eligible бар = 08:00 M15 → cascade
fires at **08:15:04 UTC** (after M15 close).

[`core/smc/tda/stage3_session.py:54-58`](../../core/smc/tda/stage3_session.py#L54-L58)
аналізує London H1 у вікні `[london_start_ms, london_end_ms) = [08:00, 13:00)`.
**На момент 08:15 у цьому вікні 0 complete H1 bars** (08:00–09:00 ще формується).

Stage 3 на лінії 105: `if not london_h1: return SessionNarrative(narrative=_N, ...)` —
NO_NARRATIVE одразу. M15 fallback (lines 200–280) має лише 1 complete M15 бар
(08:00–08:15) — практично завжди inside Asia range. Verdict: 0 sweep detection.

### 1.3 Manual verification (XAU 2026-04-17)

Скрипт `trader-v3/tools/tda_verify_xau_17apr.py` прочитав XAU H1+M15 з Redis
tail і відтворив timeline:

| Час UTC | Подія | Asia high (4806.08) sweep? |
|---|---|---|
| 08:15:04 | Cascade триггер | 0 H1 complete, 3 M15 inside Asia range |
| 11:00 | London H1 close | h=4811.67 → перший H1 sweep ABOVE ✅ |
| 11:45 | M15 close | h=4811.67 → перший M15 sweep ABOVE |
| 12:00 | H1 close | h=4847.70 — продовження impulse |
| 13:00 | London close, NY open | h=4890.67 |

Sweep чисто: macro=BULL strong (Stage 1+2 confirmed), Asia high swept o 11:00
з закриттям вище → **HUNT_PREV_HIGH valid signal**. Cascade на 08:15 його
не міг побачити.

---

## 2. Альтернативи

| Опція | Зміна | Trade-offs |
|---|---|---|
| **A) Defer trigger to ≥ london_end_hour_utc** | `if bar_hour >= self._cfg.london_end_hour_utc:` (1-line) | Сигнал з'являється о 13:15 UTC — встигаємо до NY open (14:30). 1-line diff, мінімальний blast radius, відповідає original ADR-0040 §2.4 design intent ("analyze London after it formed"). London momentum в XAU зазвичай продовжується в NY. |
| B) Mid-session early-look + retry | Тригер при bar_hour=11 + повтор при ≥13 з dedup | Складніше: dedup logic, два cascade runs, подвоєний read cost. Дає +1.5h раніше але потребує ADR-level validation. |
| C) Continuous re-evaluation | Тригер кожні 15min after 08:00 поки не знайде sweep | Spam в логах, складна dedup, нова state machine. Risk surface велика. |
| D) Lower threshold + keep 08:15 trigger | Ослабити Stage 3 (asia_min_h1_bars=0, accept M15-only) | Лікує симптом не root cause. Risk false positives з incomplete data. |

**Вибір: A.** Мінімальний diff, відновлює design intent, статистично
валідно (Stage 3 design передбачає **повний** London snapshot).

---

## 3. Рішення

### 3.1 Patch

**File**: `runtime/smc/tda_live.py`

```python
# BEFORE (lines 122-130)
if (
    self._cfg.london_start_hour_utc
    <= bar_hour
    < self._cfg.london_end_hour_utc
):
    self._run_cascade(symbol, day_ms, uds_reader, now)

# AFTER
if bar_hour >= self._cfg.london_end_hour_utc:
    self._run_cascade(symbol, day_ms, uds_reader, now)
```

### 3.2 Поведінка

- Cascade fires на першому complete M15 барі з `bar_hour >= 13` → **13:15 UTC** (default).
- `last_day != day_ms` guard блокує повторні запуски того самого дня.
- Якщо процес стартує після 13:00 — fires на наступному ж M15 (read_window
  з UDS дає 48 H1 + 96 M15 → достатньо для full London + Asia history).
- Якщо процес down 12–14 UTC — пропускає день (acceptable: signal expires at
  NY end так чи інакше, ще один день очікування).

### 3.3 Inваріанти

- **I0/S1**: read-only behavior unchanged — лише timing of read.
- **S5**: config-driven (no new hardcoded values; reuses `london_end_hour_utc`).
- **ADR-0040 §2.4**: original spec казав "after London session" — патч
  приводить implementation у відповідність до spec.

---

## 4. Validation план

### 4.1 Replay test (mandatory)

Скрипт `trader-v3/tools/tda_replay_14d.py`:

1. Iterate per (symbol × day) for 14 днів × 4 символи = 56 day-symbol pairs.
2. На кожен `day_ms`: знайти у Redis tail M15 бар з `bar_hour >= 13`.
3. Емулювати UDS read на момент `trigger_close_ms`: фільтрувати bars з
   `close_ms <= trigger_close_ms`.
4. Запустити `run_tda_cascade()` з тими самими bars + cfg.
5. Лічити: total runs, signals, grades distribution, failed_stage histogram.

**Pass criterion**: ≥2 signals на 14d × 4sym (минулий live = 0). Якщо <2 — є
ще один баг в Stage 3 caliдration → ескалація до T1.3-T1.4 backtest comparison.

### 4.2 Production rollout

- Apply patch → restart `smc-ws` supervisor on VPS.
- Watch `TDA_CASCADE_*` logs upper bound 7 днів.
- Якщо ≥1 signal generated && grade ≥C → success criterion для 1.05 deploy.

---

## 5. Rollback

- `git revert` патчу → cascade повертається до 08:15 trigger.
- Не потребує state migration (тригерний guard `last_day != day_ms` сумісний).
- Active TDA signals у `data_v3/_signals/tda_state.json` не зачіпаються.

---

## 6. Consequences

### Позитивні

- Усуває timing/algorithm mismatch — Stage 3 нарешті отримує повне
  London window як передбачено алгоритмом.
- Signals генеруються на 13:15 UTC — **до** NY open (14:30) з R:R головно
  досяжним.
- Розблоковує 1.05 deploy decision (Архі launch 2026-05-01) — об'єктивна
  цифра signal rate за 14d replay.

### Негативні / прийняті

- Сигнал з'являється на 5 годин пізніше за Asia close. У днях коли
  XAU робить весь рух у Asia і flat в London → fewer signals (acceptable:
  TDA design priority = London/NY sessions, не Asia).
- Якщо London close = 13:00 і ринок вже зробив reversal — сигнал може бути
  запізнілим. Mitigation: Stage 4 FVG entry validation вже фільтрує stale entries.

### Невідомі

- Backtest 75% WR використовував end-of-day snapshots — ймовірно тому live=0.
  Replay test (§4.1) дасть точну цифру для post-fix expectation.

---

## 7. Acceptance criteria

- [x] 1-line patch applied
- [ ] Replay 14d × 4sym виконано
- [ ] ≥2 signals згенеровано в replay (grade ≥C)
- [ ] VPS deploy + 7d production observation
- [ ] ADR-index.md updated, changelog.jsonl entry (S1)

---

## 8. References

- Verification artifact: `trader-v3/tools/tda_verify_xau_17apr.py`
- Replay artifact: `trader-v3/tools/tda_replay_14d.py`
- Source: ADR-0040 §2.4 (design intent), §3.2 (live runner spec)
- Stage 3 algorithm: `core/smc/tda/stage3_session.py:54-280`
- Trigger location: `runtime/smc/tda_live.py:118-130`
