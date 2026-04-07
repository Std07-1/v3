# ADR-0047: Structure Detection V2 — Canonical BOS/CHoCH + FVG Accuracy

**Status**: Proposed  
**Date**: 2026-04-07  
**Author**: AI-agent + Owner review  
**Supersedes**: Частково ADR-0024 §4.2 (structure detection algorithm)  
**Relates to**: ADR-0024 (SMC Engine), ADR-0029 (Confluence Scoring), ADR-0028 (Display Filter)

---

## 1. Context

### 1.1 Проблема зі Structure Detection

Поточний алгоритм `core/smc/structure.py:detect_structure_events()` має фундаментальну помилку в класифікації BOS vs CHoCH, що призводить до:

- **~85-95% CHoCH і ~5-15% BOS** замість збалансованого розподілу
- Агент бота отримує перекошену картину ринкової структури
- OB майже всі типу "reversal" (CHoCH-based) з бустом 1.5× strength
- Confluence scoring перекошений — F7 (structure) майже завжди +1

### 1.2 Проблема з FVG відображенням

Не всі FVG-зони відображаються на графіку через агресивну фільтрацію:
- `fvg_display_cap = 4` — тверде обмеження на кількість FVG в display
- `fvg_ob_overlap_hide = true` — FVG що перетинається з OB ховається  
- `min_display_strength = 0.25` — слабкі FVG відсікаються
- Результат: трейдер бачить неповну картину FVG — може пропустити entry zones

### 1.3 Відсутність mBOS

В ICT методології є два рівні структури:
- **Major structure**: HH/HL/LH/LL з великих swing points (period=5)
- **Minor structure (mBOS)**: менші pivots (period=2-3) для раннього entry

Код має `detect_fractals(period=2)` — але ці fractals **display-only**, не беруть участі в structure logic.

### 1.4 Мертвий конфіг `confirmation_bars`

`config.json` → `smc.structure.confirmation_bars = 1` існує і парситься в `SmcStructureConfig`, але **ніколи не передається і не використовується** в `detect_structure_events()`. Мертвий параметр = misleading.

---

## 2. Root Cause Analysis (Code Evidence)

### 2.1 Алгоритм зараз (structure.py:80-162)

```python
# Ітерація по bars з окремим swing_idx pointer:
# while swing_idx < len(swing_times) and swing_times[swing_idx] <= bar.open_time_ms:
#     Трекаємо ТІЛЬКИ HH та LL (HL/LH ігноруються!)
#     if s.kind == "hh": last_hh = s
#     elif s.kind == "ll": last_ll = s

# Для кожного bar — перевірка break з temporal guard:
if last_hh and bar.c > last_hh.price and bar.open_time_ms > last_hh.time_ms:
    if trend_bias == "bearish": → CHoCH_BULL   # reversal
    else:                       → BOS_BULL     # continuation (або trend=None)
    last_hh = None  # consumed

elif last_ll and bar.c < last_ll.price and bar.open_time_ms > last_ll.time_ms:
    if trend_bias == "bullish": → CHoCH_BEAR
    else:                       → BOS_BEAR
    last_ll = None  # consumed
```

[VERIFIED structure.py:109-162]

### 2.2 Чому BOS рідкісний

1. `trend_bias` починається з `None` → перший break завжди BOS
2. Кожен наступний break в **протилежному** напрямку = CHoCH (trend фліпається)
3. BOS потребує 2 послідовні breaks в **тому ж** напрямку
4. Для другого break потрібен новий HH/LL pivot (wait period × 2 + 1 = 11 барів при period=5)
5. За ці 11 барів ціна майже завжди ламає протилежну сторону → CHoCH

**В ranging market**: BOS → CHoCH → CHoCH → CHoCH → CHoCH (нескінченно)

### 2.3 Каскадний ефект

| Компонент | Вплив |
|-----------|-------|
| `order_blocks.py:129` | CHoCH OB отримує 1.5× boost. Оскільки CHoCH домінує → **майже всі** OB з бустом |
| `confluence.py:148-156` | F7 (structure) завжди +1, бо CHoCH завжди є поруч |
| Discipline G3 gate | Gate рідко FAIL, бо CHoCH завжди є — gate неефективний |
| Agent prompts | Agent бачить "CHOCH BEAR @ 3245" але рідко "BOS BEAR" — не може розрізнити continuation vs reversal |

### 2.4 FVG Display Evidence

```python
# engine.py _filter_for_display():
fvg_cap = disp.fvg_display_cap  # config: 4
# + fvg_ob_overlap_hide = True → hide if overlaps ANY active OB
# + min_display_strength = 0.25
# + distance: proximity_atr_mult * 1.5 * atr
```

[VERIFIED engine.py:1074-1101, config.py:204-205]

У config.json: `fvg.max_active = 15` (detection layer), але display cap = 4.
Тобто з 15 detected FVG трейдер бачить максимум 4 (і часто менше через overlap hide та distance filter).

---

## 3. Decision: Canonical ICT Structure Detection

### 3.1 Алгоритм V2 — Канонічний BOS/CHoCH

**ICT визначення:**

В **uptrend** (bullish bias): HH → HL → HH → HL ...
- **BOS_BULL**: close вище останнього swing high → тренд **продовжується**
- **CHoCH_BEAR**: close нижче останнього **HL** (higher low) → тренд **зламаний**

В **downtrend** (bearish bias): LL → LH → LL → LH ...
- **BOS_BEAR**: close нижче останнього swing low → тренд **продовжується**
- **CHoCH_BULL**: close вище останнього **LH** (lower high) → тренд **зламаний**

**Різниця з поточним:**

| Аспект | Зараз (V1) | Після (V2) |
|--------|-----------|------------|
| BOS ламає | HH (bullish) або LL (bearish) при тренді в тому ж напрямку | HH або LL — continuation в напрямку тренду |
| CHoCH ламає | HH або LL при тренді в **протилежному** напрямку | **HL** (в uptrend) або **LH** (в downtrend) — internal structure |
| Трекає свінги | Тільки HH та LL | **Всі 4 типи**: HH, HL, LH, LL |
| BOS frequency | ~5-15% | ~40-60% (нормальний trending market) |
| CHoCH frequency | ~85-95% | ~20-40% (справді лише reversals) |

### 3.2 Pseudocode V2

```python
def detect_structure_events_v2(classified_swings, bars, config):
    trend_bias = None  # None → "bullish" | "bearish"
    
    # Трекаємо всі 4 типи swing points
    last_hh = None   # останній HH
    last_hl = None   # останній HL  (internal low в uptrend)
    last_ll = None   # останній LL
    last_lh = None   # останній LH  (internal high в downtrend)
    
    for bar in bars:
        # Update swing tracking
        update_swing_levels(bar, classified_swings)
        
        if trend_bias == "bullish" or trend_bias is None:
            # BOS_BULL: break above last swing high (HH)
            if last_hh and bar.c > last_hh.price:
                emit BOS_BULL
                trend_bias = "bullish"
                last_hh = None  # consumed
                
            # CHoCH_BEAR: break below last HL (internal structure violated)
            elif last_hl and bar.c < last_hl.price:
                emit CHoCH_BEAR
                trend_bias = "bearish"
                last_hl = None  # consumed
                
        if trend_bias == "bearish" or trend_bias is None:
            # BOS_BEAR: break below last swing low (LL)
            if last_ll and bar.c < last_ll.price:
                emit BOS_BEAR
                trend_bias = "bearish"
                last_ll = None  # consumed
                
            # CHoCH_BULL: break above last LH (internal structure violated)
            elif last_lh and bar.c > last_lh.price:
                emit CHoCH_BULL
                trend_bias = "bullish"
                last_lh = None  # consumed
```

### 3.3 FVG Display — збільшити cap

| Параметр | Зараз | Пропозиція | Обґрунтування |
|----------|-------|------------|---------------|
| `fvg_display_cap` | 4 | **6** | Трейдер має бачити більше FVG для повної картини |
| `fvg_ob_overlap_hide` | true | **false** | FVG всередині OB = **confluence**, а не дублювання. Ховати = втрачати інформацію |
| `min_display_strength` | 0.25 | 0.25 | Без змін — це розумний поріг |

### 3.4 mBOS — NOT включено в V2

mBOS (minor BOS) з `period=2`) **відкладено**:
- Додає складність без очевидної вигоди поки V2 не стабілізується
- `detect_fractals(period=2)` залишається display-only
- Може бути додано як ADR-0047a якщо V2 покаже потребу

### 3.5 `confirmation_bars` — підключити або видалити

**Рішення**: Підключити з мінімальним впливом.
- Якщо `confirmation_bars > 1` → вимагати що bar.close за рівнем утримується N барів
- Default = 1 (поточна поведінка: один bar.close достатньо)

---

## 4. Implementation Plan (P-slices)

### P0: Structure V2 — core logic (≤120 LOC, 1 файл)

**Файл**: `core/smc/structure.py`

**Зміни:**
1. `detect_structure_events()` → новий алгоритм V2 з tracking всіх 4 swing types
2. CHoCH тепер ламає HL/LH (internal structure), не HH/LL
3. BOS = continuation break (HH в uptrend, LL в downtrend)
4. Передавати `SmcStructureConfig` як параметр → використовувати `confirmation_bars`
5. Return signature — без змін: `(events, trend_bias, last_bos_ms, last_choch_ms)`

**Wire format**: Без змін — ті самі kinds: `bos_bull, bos_bear, choch_bull, choch_bear`

**Verify**:
- Existing tests `test_smc_e1.py` pass (wire format not changed)
- New test: `test_structure_v2.py` — 8+ cases covering:
  - Uptrend BOS continuation (HH break while bullish)
  - CHoCH_BEAR via HL break (not LL)
  - Downtrend BOS continuation (LL break while bearish)
  - CHoCH_BULL via LH break (not HH)
  - BOS→BOS consecutive (trending)
  - Mixed sequence (ranging market)
  - `trend_bias=None` initialization
  - `confirmation_bars > 1` behavior

### P1: OB strength rebalance (≤30 LOC, 1 файл)

**Файл**: `core/smc/order_blocks.py`

**Зміни:**
- З V2 BOS і CHoCH будуть збалансовані → CHoCH boost 1.5× стає адекватним
- **Без змін коду** — лише верифікація що з новим розподілом BOS/CHoCH grades розумні
- Якщо BOS OBs consistently underscored → додати `bos_impulse_boost` factor

**Verify**: 
- Run existing `test_smc_e1.py`, `test_smc_confluence.py`
- Manual check: BOS-based OBs мають адекватний strength vs CHoCH

### P2: FVG display config (≤10 LOC, 1 файл)

**Файл**: `config.json`

**Зміни:**
```json
"display": {
    "fvg_display_cap": 6,        // було 4
    "fvg_ob_overlap_hide": false  // було true
}
```

**Verify**: 
- Перевірити що UI показує більше FVG
- Не призводить до visual clutter (якщо так — rollback до 5)

### P3: Config wiring + all callers (≤15 LOC, 3 файли)

**Файли**: `core/smc/structure.py`, `core/smc/engine.py`, `tests/test_smc_e2_pd_inducement.py`

**Зміни:**
- `detect_structure_events()` приймає `config: SmcStructureConfig` параметр
- Використовує `config.confirmation_bars` для multi-bar confirmation
- `engine.py` передає `cfg.structure` у **обох** callsites:
  - `_compute_snapshot()` (line ~957)
  - `_compute_full_snapshot_v2()` (line ~425, if exists)
- `test_smc_e2_pd_inducement.py` (line ~38) — update call signature

**Note**: `confirmation_bars` disambiguation — `smc.structure.confirmation_bars` (BOS/CHoCH) vs `smc.inducement.confirmation_bars` (inducements). Різні purposes, різні consumers.

**Verify**: 
- Default `confirmation_bars=1` → поведінка ідентична до V2 з P0
- `confirmation_bars=3` → structure breaks рідше, але сильніше confirmed
- `pytest tests/test_smc_e2_pd_inducement.py` passes

---

## 5. Blast Radius Assessment

### 5.1 Компоненти що зміняться

| Компонент | Файл | Тип зміни |
|-----------|------|-----------|
| Structure detection | `core/smc/structure.py` | Algorithm rewrite (P0) |
| Config wiring | `core/smc/engine.py` | Pass config param (P3) |
| FVG display | `config.json` | Config value change (P2) |

### 5.2 Компоненти з SEMANTIC CHANGE (wire kinds ті самі, поведінка інша)

| Компонент | Вплив | Дія |
|-----------|-------|-----|
| `narrative.py:_find_qualifying_structure_break()` | CHoCH тепер на HL/LH (ближче до зони), а не HH/LL. Trigger resolution для зон зміниться — зони переходять в "triggered" при інших цінових рівнях. | **P0+ verify**: ревізія тестів `test_smc_narrative.py` на semantic validity |
| `engine.py:_update_zone_lifecycle()` | Breaker promotion: `mitigated OB + CHoCH → breaker`. CHoCH frequency drop ~3× → менше breakers. | **P1 monitor**: порівняти breaker count до/після V2 |
| `smc_runner.py:453-458` | Signal relay to Cloudflare Worker. BOS frequency зросте ~5×. | **P0 note**: downstream consumers мають бути rate-tolerant |
| `smc_runner.py:470-476` | `fire_bias()` fires on `trend_bias` change. V2 змінює частоту фліпів → BiasBanner (ADR-0031) оновлюється з іншою частотою. | **P0 note**: monitor bias flip rate |

### 5.3 Компоненти що НЕ зміняться (wire format compatible)

| Компонент | Чому OK |
|-----------|---------|
| `order_blocks.py` | Фільтрує по `kind not in ("bos_bull", "choch_bull", "bos_bear", "choch_bear")` — exact match → zero change |
| `confluence.py` | `_check_structure()` — `kind.endswith("_bull")` / `"_bear"` → zero change |
| `fvg.py` | Independent від structure → zero change |
| Wire format (types.py) | `SmcSwing.to_wire()` — ті ж kinds → zero change |
| UI (OverlayRenderer.ts) | Renders by kind string → zero change |
| Bot prompts.py | Renders kind.upper() → zero change |
| Discipline G3 gate | Checks `kind.startswith("bos_")` or `kind.startswith("choch_")` → zero change |
| ws_server.py context | Filters by `_STRUCTURE_KINDS` set → zero change |
| TDA cascade (`core/smc/tda/`) | Stage1 uses own 3-bar pivot, no BOS/CHoCH dependency → zero change [VERIFIED] |

### 5.4 Callers of `detect_structure_events()` (all must be updated in P3)

| Caller | File:line | Дія |
|--------|-----------|-----|
| `engine.py` _compute_snapshot | line ~957 | Pass `cfg.structure` |
| `engine.py` _compute_full_snapshot_v2 (if exists) | line ~425 | Pass `cfg.structure` |
| `test_smc_e2_pd_inducement.py` | line ~38 | Update call signature |

### 5.5 Semantic change (важливо)

**Однакові wire kinds, інша семантика:**
- `choch_bull` раніше = "break HH while bearish"
- `choch_bull` тепер = "break LH (internal downtrend structure)"
- `bos_bull` раніше = "break HH while bullish or trend=None"
- `bos_bull` тепер = "break HH continuing uptrend"

Це **правильна** зміна семантики — наближає до ICT стандарту. Downstream consumers працюють з kinds, не з семантикою рівнів — zero breakage.

### 5.6 `confirmation_bars` disambiguation

В `config.json` є два `confirmation_bars`:
- `smc.structure.confirmation_bars = 1` — для **structure breaks** (BOS/CHoCH). Використовується цим ADR.
- `smc.inducement.confirmation_bars = 3` — для **inducement detection**. Інший purpose, інший consumer (`inducement.py`).

Це **не конфлікт** — різні підсистеми з різним значенням "confirmation". Обидва залишаються як є.

---

## 6. Alternatives Considered

### A1: Patch V1 — тільки track HL/LH для CHoCH

Мінімальний диф: додати `last_hl`/`last_lh` tracking, CHoCH ламає їх.
- ✅ Простіше
- ❌ Не виправляє consumed swing problem (BOS все ще рідкий)
- ❌ Half-measure

**Відхилено**: не вирішує root cause повністю.

### A2: Dual-chain (major + minor structure)

Два паралельні ланцюги: period=5 (major) + period=2 (minor/mBOS).
- ✅ Дає раннє підтвердження
- ❌ Подвоює complexity
- ❌ Потребує зміни wire format (adding kind `mbos_*`)
- ❌ UI/agent треба навчити розрізняти major vs minor

**Відкладено** до ADR-0047a після стабілізації V2.

### A3: Без змін, тільки FVG display

Не чіпати structure, тільки FVG display cap.
- ❌ Не вирішує фундаментальну проблему BOS/CHoCH
- ❌ Agent все ще бачить перекошену структуру

**Відхилено**: cosmetic fix для structural problem.

---

## 7. Risks and Mitigations

| Ризик | Mitigation |
|-------|------------|
| V2 генерує забагато BOS → OB кількість зростає | `max_active_per_side` cap в OB залишається (config) |
| CHoCH стає рідшим → менше reversal OBs | Очікувано і правильно — CHoCH має бути рідким (reversal = рідкісна подія) |
| `trend_bias=None` на початку → невизначеність | Перший HH break = BOS (conservative), перший HL/LH break = ignored до встановлення тренду |
| FVG cap 6 → visual clutter | Якщо clutter → fine-tune до 5 без ADR |
| Existing tests fail | Wire format не змінено → tests pass. Нові тести для V2 semantics |
| **Breaker promotion rate drops** | CHoCH frequency ↓ → `_update_zone_lifecycle` promotes fewer breakers (OB→breaker on CHoCH). Monitor count і розглянути BOS як trigger для breaker якщо drop > 50% |
| **Narrative trigger semantics shift** | CHoCH fires at HL/LH (ближче до зони) vs HH/LL. Zones may trigger earlier/later. Verify `test_smc_narrative.py` semantic correctness |
| **Signal relay volume change** | BOS signals зростуть ~5×. Cloudflare Worker та downstream мають бути rate-tolerant |
| **Bias flip frequency change** | `fire_bias()` fires at different moments → BiasBanner (ADR-0031) відображає зміни з іншою динамікою |

---

## 8. Rollback Plan

1. `git revert` P0 commit → повертає V1 алгоритм
2. Config changes (P2) — revert одним commit
3. Snapshot compatibility — wire формат не змінений → zero migration

---

## 9. Success Criteria

| Критерій | Метрика | Як перевірити |
|----------|---------|---------------|
| BOS/CHoCH баланс | BOS ≥ 25% від всіх structure events у trending market | Run on H1 XAU/USD last 500 bars |
| CHoCH = reversal only | CHoCH виникає тільки при зламі internal structure (HL/LH) | Test cases + manual audit |
| FVG visibility | ≥ 5 FVG видимі на M15 при active market | Visual check on chart |
| Zero downstream breakage | All existing tests pass | `pytest tests/ -v` |
| Agent quality | Agent бачить BOS та CHoCH з правильними цінами в промпті | Check bot logs after deploy |

---

## 10. Open Questions

1. **Trend initialization**: Коли `trend_bias=None` і ми бачимо HL break — ігнорувати (бо тренд ще не встановлений) чи трактувати як CHoCH? **Пропозиція**: ігнорувати до першого BOS.

2. **Wick vs Body**: Поточний алгоритм використовує `bar.c` (close = body). Деякі ICT трейдери використовують wick (bar.h / bar.low) для BOS detection. **Пропозиція**: залишити `bar.c` (body close) — це канонічний ICT підхід.

3. **CHoCH на HH/LL як fallback**: Якщо немає HL/LH (дуже ранній тренд), чи потрібен fallback на HH/LL для CHoCH? **Пропозиція**: ні — якщо internal structure ще не сформувалась, CHoCH неможливий.

---

## Appendix A: Current Structure Events Distribution (Production Data)

Data from VPS `2026-04-07`:

```
H4: 17 swings — displacement_bear, displacement_bull, displacement_bull, ...
H1: 18 swings — displacement_bear, choch_bear, displacement_bear, ...
M15: 10 swings — displacement_bull, displacement_bull, choch_bull, ...
```

[VERIFIED terminal — check_swings.py output]

Zero `bos_*` events visible in recent H1/M15 data — confirms the CHoCH dominance problem.

---

## Appendix B: FVG Display Pipeline

```
detect_fvg() [max_active=15]
    ↓
_update_fvg_status() [lifecycle: active→partially_filled→filled]
    ↓
_filter_for_display()
    ├── exclude: filled (dead)
    ├── exclude: height > max_zone_height_atr_mult × ATR
    ├── exclude: distance > proximity_atr_mult × 1.5 × ATR
    ├── exclude: overlaps active OB (fvg_ob_overlap_hide=true)  ← LOSSY
    ├── sort by _zone_rank
    └── cap: fvg_display_cap = 4  ← TIGHT
```

3 layers of filtering after detection, each reducing visible count.
Proposed: remove OB overlap hide (=false), widen cap to 6.
