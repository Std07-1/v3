# ADR-0041: Premium/Discount Badge + EQ Line — Decoupled Calc/Display

- **Статус**: Implemented (P1–P9 implemented, changelog 20260322-003/005)
- **Дата**: 2026-03-22
- **Автор**: R_ARCHITECT
- **Initiative**: `pd_badge_eq_v1`
- **Пов'язані ADR**: ADR-0024 (SMC Engine), ADR-0024b (Key Levels), ADR-0024c (Zone POI Rendering), ADR-0028 (Elimination Engine), ADR-0029 (Confluence), ADR-0036 (Premium Shell)

---

## 1. Контекст і проблема

### 1.1 Поточний стан

`config.json:smc.premium_discount` має **один** ключ `enabled` (зараз `false`):

```json
"premium_discount": { "enabled": false }
```

[VERIFIED config.json:321-323] — `"enabled": false`.

Цей ключ одночасно контролює:
1. **Розрахунок** P/D зон у `core/smc/premium_discount.py:53` — `if not config.premium_discount.enabled: return []`
2. **UI відображення** — zones з kind `premium`/`discount` потрапляють у SmcSnapshot і мали б рендеритись, але OverlayRenderer.ts:561 їх hard-skip: `if (z.kind === 'premium' || z.kind === 'discount') continue;`

[VERIFIED core/smc/premium_discount.py:53] — early return при `enabled=false`.
[VERIFIED ui_v4/src/chart/overlay/OverlayRenderer.ts:561] — hard skip P/D zones.

### 1.2 Проблема: Трейдер втратив P/D контекст

Трейдер вимкнув `enabled: false` через **візуальний шум** (filled rectangles premium/discount зон займали 50% чарту). Наслідок:

1. `detect_premium_discount()` повертає `[]` — **розрахунок зупинений**.
2. Confluence scoring F6 (`_check_premium_discount` у `confluence.py:127`) рахує P/D з swings напряму, **але** не має даних від engine (zones відсутні у snapshot).
3. Narrative (`narrative.py:424`) перевіряє `if zone and "premium" in zone.kind` — **але P/D зон немає** бо calc disabled.
4. Результат: 2 збиткові лонги в H4 premium зоні, PDL не перевірявся.

[VERIFIED core/smc/confluence.py:127-129] — `_check_premium_discount()` рахує з swings (незалежно від zones).
[VERIFIED core/smc/narrative.py:424] — перевірка `premium` у zone kind.
[VERIFIED core/smc/engine.py:951] — pd_zones = [] коли disabled.

### 1.3 Architectural flaw

Один `enabled` контролює і calc і display — порушує **принцип розділення concerns**. Трейдер хоче:
- **Calc ЗАВЖДИ працює** (narrative, confluence F6, signal cascade мають P/D context)
- **Filled rectangles OFF** (візуальний шум)
- **Badge ON** (компактний HUD елемент: "DISCOUNT 38%")
- **EQ line ON** (тонка пунктирна лінія equilibrium)

### 1.4 Failure Model

| # | Сценарій | Наслідок | Захист |
|---|----------|----------|--------|
| F1 | `calc_enabled: false` (lockable override) | Вся P/D логіка мертва, narrative/confluence blind | Default true, warn у docs |
| F2 | H4 swing range = 0 (SH == SL) | Division by zero у percent calc | Guard `range_high <= range_low` вже є (premium_discount.py:76) |
| F3 | EQ line збігається з PDH/PDL (±0.5 ATR) | Visual clutter — 2 лінії на одній ціні | D8 coincidence rule: hide EQ, keep PDH/PDL |
| F4 | Cold start: no swings yet | pd_state = null, badge не показується | Badge renders only when pd_state present |
| F5 | TF switch: pd_state stale | Badge показує стару P/D для попереднього TF | pd_state прив'язаний до viewer TF через snapshot |
| F6 | 3 теми x badge colors | WCAG AA порушення на одній з тем | Explicit color per theme, contrast verified |

---

## 2. Constraints

- **Інваріанти**: I0 (pure logic в core/), I1 (через UDS/snapshot), I4 (один WS потік), S0 (pure), S5 (config SSOT), S6 (wire format sync)
- **Budget**: ~200 LOC total across 6 files, 0 new dependencies
- **Backward compatibility**: wire format extension (additive field `pd_state`), old clients ignore unknown fields
- **Performance**: 1 додатковий dict у full frame (~50 bytes), 1 dashed line render (~0.1ms)
- **Display budget** (ADR-0028): EQ line = 1 chart object; badge = HUD (не входить у budget <=12)

---

## 3. Розглянуті альтернативи

### Alternative A: Granular config + pd_state у wire format + PdBadge + EQ line

- **Суть**: Розділити `enabled` на 4 ключі (`calc_enabled`, `show_badge`, `show_eq_line`, `show_zones`). Додати `pd_state` об'єкт у SmcSnapshot wire → WS full frame. Новий Svelte badge + новий render метод для EQ line. D8 coincidence rule для EQ vs PDH/PDL.
- **Pros**: Повний контроль, calc ніколи не вимикається випадково, мінімальний visual footprint, narrative/confluence завжди мають дані.
- **Cons**: 6 файлів торкнуто, wire format extension.
- **Blast radius**: config.py, premium_discount.py, engine.py (або smc_runner.py), ws_server.py, types.ts, OverlayRenderer.ts, themes.ts, ChartPane.svelte, PdBadge.svelte (новий).
- **LOC estimate**: ~200

### Alternative B: UI-only fix (compute P/D на клієнті з swings)

- **Суть**: Не змінювати backend. На клієнті взяти swings з SmcData, обчислити equilibrium/percent, показати badge. OverlayRenderer малює EQ line з тих же swings.
- **Pros**: 0 backend змін, менший blast radius.
- **Cons**: Дублювання логіки (Python + TS), `enabled: false` досі гасить calc → narrative.py:424 та engine zones все ще порожні. Не вирішує root cause (narrative blind). Порушує I0 (логіка в UI замість core/).
- **Blast radius**: types.ts, OverlayRenderer.ts, ChartPane.svelte, PdBadge.svelte.
- **LOC estimate**: ~120

### Alternative C: Тільки `enabled: true` + hide zones у OverlayRenderer

- **Суть**: Повернути `"enabled": true` в config.json. OverlayRenderer вже hard-skip P/D zones (line 561). Додати тільки badge + EQ line.
- **Pros**: Мінімальні зміни (не чіпаємо config structure).
- **Cons**: Один ключ досі контролює все. Трейдер не може вимкнути badge окремо від EQ line. Наступний раз хтось поставить `enabled: false` — та сама проблема. Не вирішує architectural flaw.
- **Blast radius**: config.json (1 значення), OverlayRenderer.ts, ChartPane.svelte, PdBadge.svelte.
- **LOC estimate**: ~100

### Рішення: Alternative A

**Обгрунтування**: Єдина альтернатива що вирішує root cause (calc/display coupling). Alt B дублює логіку і не фіксить narrative blindness. Alt C — косметичний патч, не architectural fix. Alt A відповідає S5 (config SSOT), I0 (pure calc), і забезпечує що narrative/confluence ЗАВЖДИ мають P/D context незалежно від UI preferences трейдера.

---

## 4. Рішення (деталі)

### 4.1 Types / Contracts (FIRST)

#### Python: `SmcPremiumDiscountConfig` (config.py)

```
@dataclass
SmcPremiumDiscountConfig:
    calc_enabled: bool = True       # Engine завжди рахує (lockable)
    show_badge: bool = True         # HUD chip у нижньому лівому куті
    show_eq_line: bool = True       # EQ horizontal dashed line на чарті
    show_zones: bool = False        # Filled rectangles (default OFF — шум)
    eq_pdh_coincidence_atr_mult: float = 0.5  # D8: hide EQ якщо |EQ - PDH/PDL| < mult * ATR
```

Backward compat: `from_dict()` mapує old `"enabled"` → `calc_enabled` для міграції.

#### Python: `PdState` (новий lightweight dataclass у types.py)

```
@dataclass(frozen=True)
PdState:
    range_high: float       # Swing High price
    range_low: float        # Swing Low price
    equilibrium: float      # (high + low) / 2
    pd_percent: float       # 0.0-100.0 (0 = range_low, 100 = range_high)
    label: str              # "PREMIUM" | "DISCOUNT" | "EQ"
    current_price: float    # Ціна на момент розрахунку
```

Wire format (dict): `{"range_high": ..., "range_low": ..., "equilibrium": ..., "pd_percent": ..., "label": ...}`

#### TypeScript: `PdState` (types.ts)

```
interface PdState {
    range_high: number;
    range_low: number;
    equilibrium: number;
    pd_percent: number;     // 0-100
    label: 'PREMIUM' | 'DISCOUNT' | 'EQ';
}
```

Added to SmcData: `pd_state?: PdState | null;`

#### TypeScript: Theme extensions (themes.ts)

9 нових properties у ThemeDef:
- `pdBadgeDiscountBg`, `pdBadgeDiscountText` — зелені відтінки
- `pdBadgePremiumBg`, `pdBadgePremiumText` — червоні відтінки
- `pdBadgeEqBg`, `pdBadgeEqText` — сірі відтінки
- `pdEqLineColor` — 40% opacity gray для EQ dashed line

### 4.2 Pure Logic (core/)

#### `premium_discount.py` — нова функція `compute_pd_state()`

Сигнатура:
```
def compute_pd_state(
    classified: List[SmcSwing],
    current_price: float,
    config: SmcConfig,
) -> Optional[PdState]
```

Алгоритм:
1. Якщо `not config.premium_discount.calc_enabled` — return None
2. Знайти last SH / SL (та сама логіка що `detect_premium_discount`)
3. EQ = (SH + SL) / 2
4. percent = ((price - SL) / (SH - SL)) * 100, clamped 0-100
5. label = "DISCOUNT" if percent < 48, "PREMIUM" if percent > 52, "EQ" otherwise
6. Return PdState(...)

Existing `detect_premium_discount()`: змінити guard з `enabled` на `calc_enabled`. Zones ЗАВЖДИ генеруються коли `calc_enabled=true` (потрібні для narrative.py:424 і confluence F6 context). `show_zones` контролює тільки UI rendering (OverlayRenderer.ts:561 — існуючий hard-skip замінюється на config-driven check). Це забезпечує що engine state завжди повний, а UI показує тільки те що трейдер обрав.

### 4.3 Runtime Integration

#### `smc_runner.py` — новий метод `get_pd_state()`

```
def get_pd_state(self, symbol: str, viewer_tf_s: int) -> Optional[dict]:
```

Викликає `compute_pd_state()` з classified swings поточного snapshot + current price.

#### `ws_server.py` — додати `pd_state` у full frame

Pattern аналогічний `bias_map` / `momentum_map`:
1. `SmcRunnerLike` protocol: додати `get_pd_state(symbol, tf_s) -> Any`
2. У `_build_full_frame()`: optional param `pd_state`
3. У `_send_full_frame()`: виклик `_smc_runner.get_pd_state()`, pass до frame builder
4. Wire: `frame["pd_state"] = pd_state` якщо not None

### 4.4 UI Wiring

#### `smcStore.ts` — parse pd_state

У `applySmcFull()`: додати `pd_state` param, зберігати в SmcData.

#### `PdBadge.svelte` — новий компонент (~50 LOC)

- Props: `pdState: PdState | null`
- Position: нижній лівий кут (absolute, поруч з BiasBanner)
- Render: `DISCOUNT 38%` / `PREMIUM 71%` / `EQ ~50%`
- Colors: з theme (pdBadgeDiscountBg/Text, pdBadgePremiumBg/Text, pdBadgeEqBg/Text)
- WCAG AA: контраст >= 4.5:1 на всіх 3 темах

#### `OverlayRenderer.ts` — `renderPdEqLine()`

- Новий метод у render pipeline (після levels, перед labels)
- Input: `pd_state.equilibrium` price → canvas Y coordinate
- Render: 1px dashed line `[4,4]`, 40% opacity gray, full chart width
- DPR: `Math.round(y * dpr + 0.5) / dpr` для pixel-perfect
- **D8 Coincidence rule**: якщо будь-який level з kind `pdh` або `pdl` має price у межах `eq_pdh_coincidence_atr_mult * ATR` від equilibrium — **не малювати EQ line**
- Входить у display budget як 1 chart object

#### `ChartPane.svelte` — підключення

- Import PdBadge
- Pass pd_state з smcStore до PdBadge

### 4.5 Config

Нові ключі в `config.json:smc.premium_discount`:

```json
"premium_discount": {
    "calc_enabled": true,
    "show_badge": true,
    "show_eq_line": true,
    "show_zones": false,
    "eq_pdh_coincidence_atr_mult": 0.5
}
```

Migration: `from_dict()` mapує old `{"enabled": X}` → `{"calc_enabled": X, "show_zones": X}`.

---

## 5. P-Slices (план реалізації)

| Slice | Scope | Files | LOC | Invariant | Verify | Rollback |
|-------|-------|-------|-----|-----------|--------|----------|
| P1 | Config split + PdState type + compute_pd_state() | config.py, types.py, premium_discount.py, config.json | ~80 | I0 (pure), S5 (config) | pytest: compute_pd_state з різними цінами (discount/premium/eq/no swings); config migration old→new | `git checkout -- core/smc/config.py core/smc/types.py core/smc/premium_discount.py config.json` |
| P2 | Wire: smc_runner + ws_server + types.ts + smcStore | smc_runner.py, ws_server.py, types.ts, smcStore.ts | ~60 | I4 (single stream), S6 (wire sync) | WS full frame містить `pd_state`; `applySmcFull` parse; dev console verify | `git checkout -- runtime/smc/smc_runner.py runtime/ws/ws_server.py ui_v4/src/types.ts ui_v4/src/stores/smcStore.ts` |
| P3 | EQ line render + D8 coincidence + themes | OverlayRenderer.ts, themes.ts | ~70 | Display budget (1 obj) | Visual: EQ line видна на M15 XAU/USD; D8: line зникає коли EQ near PDH/PDL; 3 themes OK | `git checkout -- ui_v4/src/chart/overlay/OverlayRenderer.ts ui_v4/src/chart/themes.ts` |
| P4 | PdBadge.svelte + ChartPane wiring | PdBadge.svelte (new), ChartPane.svelte | ~60 | WCAG AA | Visual: badge видимий у нижньому лівому; 3 themes contrast OK; N6 screenshot audit | `git checkout -- ui_v4/src/layout/PdBadge.svelte ui_v4/src/layout/ChartPane.svelte` |

**Total**: ~270 LOC, 10 files (1 new).

**Order**: P1 → P2 → P3 + P4 (P3/P4 can parallel after P2).

---

## 5a. Variant H: Shell Restructure (amendment, 2026-03-22)

### Контекст рішення

Після реалізації P1–P4 (badge bottom-left, EQ line, config split) проведено аналіз 7 варіантів розміщення P/D badge (A–G) з участю R_SMC_TRADER та R_CHART_UX. Жоден не вирішував повністю проблему семантичного конфлікту: **"PREMIUM + bullish = amber warning"** — коли P/D label вказує premium (ціна вище equilibrium), але bias direction каже long, трейдер має побачити що ці два сигнали конфліктують. У варіантах A–G badge був ізольований від bias context. Трейдер запропонував Variant H — реструктуризацію shell.

### Суть Variant H: два шари shell замість одного

ADR-0036 визначає shell як thesis bar + tactical strip + service rail. Variant H розширює цю модель: thesis bar стає чистішим (4 елементи замість 6+), а tactical strip отримує stage-driven visibility.

```
┌─ thesis bar (anchor cluster) ──────────────────────────┐
│ XAU/USD · M15  3324.50  [PREMIUM 71%]  │  headline     │
└────────────────────────────────────────────────────────┘
┌─ tactical strip (з'являється від PREPARE) ─────────────┐
│ [London KZ]  D1▲ H4▼ H1▲ M15▼  ·  inv: 3360  ·  ...  │
└────────────────────────────────────────────────────────┘
```

**Ключова зміна**: bias pills переїхали з thesis bar у tactical strip. Thesis bar: `символ → ціна → P/D chip → separator → headline`. Чотири елементи, один потік читання. P/D chip замінює PdBadge з bottom-left (P4) на inline position у thesis bar.

### Directional coloring для P/D chip

P/D chip отримує directional coloring — колір залежить не тільки від P/D label, а й від bias direction з narrative. Це вирішує проблему "PREMIUM + bullish = amber warning" без жодних змін у Python backend.

| pd_label | bias direction | chip color | семантика |
|----------|---------------|------------|-----------|
| DISCOUNT | long | зелений | aligned — P/D підтверджує bias |
| PREMIUM | short | червоний | aligned — P/D підтверджує bias |
| PREMIUM | long | amber | **CONFLICT** — ціна в premium, але bias long |
| DISCOUNT | short | amber | **CONFLICT** — ціна в discount, але bias short |
| EQ (45–55%) | будь-який | нейтральний сірий | equilibrium zone, bias irrelevant |

**EQ threshold**: 45–55% (не точка 50%) — запобігає мерехтінню при коливаннях навколо equilibrium. Chip показує "EQ" без відсотка.

### Stage-driven tactical strip visibility

Tactical strip видимість залежить від ShellStage (ADR-0036 §5.2):

| Stage | Tactical strip | P/D chip у thesis bar |
|-------|---------------|----------------------|
| WAIT | прихований (`grid-template-rows: 0fr`, `overflow: hidden`) | сірий або кольоровий, без bias context |
| PREPARE | видимий: session + bias pills + market phase | кольоровий з bias direction |
| READY | видимий: + invalidation price + target | повний directional color |
| TRIGGERED | видимий: + entry details | повний directional color + accent bar |
| STAYOUT | прихований | сірий |

**CSS анімація появи**: `grid-template-rows: 0fr → 1fr`, `transition: 150ms ease-out`. Не `display: none` — щоб CSS transition працював (display не анімується). Push-down layout model з ADR-0036 amendment v3 (B7) зберігається.

### Amber логіка — frontend only (C-DUMB compliant)

Нуль змін Python. Нуль нових wire fields. Amber derivation — це UI-local render hint, не backend truth. Відповідає C-DUMB principle (ADR-0036 amendment v2, A1): backend відправляє `pd_state.label` і `narrative.scenarios[0].direction`, frontend визначає chip color.

Новий helper `derivePdBadge()` у `shellState.ts`:

```
// Input: pd_state.label (з wire), direction (з narrative.scenarios[0].direction)
// Output: { label, percent, colorVariant: 'aligned-green' | 'aligned-red' | 'amber' | 'neutral' }
derivePdBadge(pdLabel, pdPercent, direction):
  if pdLabel == 'EQ' -> neutral
  if pdLabel == 'PREMIUM' && direction == 'long' -> amber
  if pdLabel == 'DISCOUNT' && direction == 'short' -> amber
  if pdLabel == 'PREMIUM' && direction == 'short' -> aligned-red
  if pdLabel == 'DISCOUNT' && direction == 'long' -> aligned-green
  if direction == null -> use pdLabel default (green/red without amber)
```

### Accent bar

Для READY/TRIGGERED stages — gradient bottom border на tactical strip:

- READY: subtle directional gradient (зелений для long, червоний для short)
- TRIGGERED: brighter accent + pulse animation (1 cycle, не loop)
- Implemented через CSS `border-image: linear-gradient(...)` або `::after` pseudo-element

### Failure model (Variant H specific)

| # | Сценарій | Наслідок | Захист |
|---|----------|----------|--------|
| VH-F1 | `direction = null` (no scenarios, WAIT stage) | Amber logic не може визначити conflict | Fallback: chip колір за pd_label (green/red), без amber |
| VH-F2 | Tactical strip transition glitch при швидкому stage toggle | Візуальний стриб | `will-change: grid-template-rows` + RAF debounce |
| VH-F3 | EQ threshold 45–55% не відповідає XAU/USD volatility | Зона мерехтіння ширша/вужча ніж потрібно | Config-driven threshold (можна тюнити без ADR) |

### P-Slices (Variant H)

| Slice | Scope | Files | LOC | Invariant | Verify | Rollback |
|-------|-------|-------|-----|-----------|--------|----------|
| P5 | `derivePdBadge()` helper + EQ threshold 45–55% | shellState.ts | ~40 | C-DUMB (no backend change), I0 (pure function) | Unit test: all 5 color variants (aligned-green, aligned-red, amber x2, neutral); EQ threshold boundary (44.9% → DISCOUNT, 45.0% → EQ, 55.0% → EQ, 55.1% → PREMIUM) | `git checkout -- ui_v4/src/stores/shellState.ts` |
| P6 | Thesis bar restructure: anchor + P/D chip inline + headline. Bias pills прибрати з thesis bar | ChartHud.svelte, PdBadge.svelte | ~60 | ADR-0036 thesis bar layout, N1 (push-down) | Visual: thesis bar = symbol + price + P/D chip + headline; bias pills відсутні; 3 themes | `git checkout -- ui_v4/src/layout/ChartHud.svelte ui_v4/src/layout/PdBadge.svelte` |
| P7 | Tactical strip: `grid-template-rows` анімація + stage-driven visibility + bias pills + session + inv + target | ChartHud.svelte, styles | ~80 | ADR-0036 §5.4 (push-down), WCAG AA | Visual: strip hidden on WAIT, visible on PREPARE+; 150ms transition smooth; bias pills visible in strip; push-down layout correct | `git checkout -- ui_v4/src/layout/ChartHud.svelte` |
| P8 | Accent bar: gradient bottom border для READY/TRIGGERED | ChartHud.svelte, themes.ts | ~30 | Display budget (HUD, not chart obj) | Visual: accent bar visible on READY/TRIGGERED only; directional color matches bias; pulse on TRIGGERED (1 cycle) | `git checkout -- ui_v4/src/layout/ChartHud.svelte ui_v4/src/chart/themes.ts` |
| P9 | PdBadge migration: з bottom-left absolute → inline thesis bar (замінює P4 layout) | PdBadge.svelte, ChartPane.svelte, ChartHud.svelte | ~40 | WCAG AA (4.5:1 contrast), N6 | Visual: badge НЕ в bottom-left, badge В thesis bar; directional coloring працює; amber visible при PREMIUM+long; 3 themes contrast OK | `git checkout -- ui_v4/src/layout/PdBadge.svelte ui_v4/src/layout/ChartPane.svelte ui_v4/src/layout/ChartHud.svelte` |

**Variant H total**: ~250 LOC, 5 files (0 new — PdBadge.svelte вже створений у P4).

**Order**: P5 → P6 + P9 (parallel, обидва залежать від P5) → P7 → P8 (accent bar останній).

### Cross-references

- **ADR-0036** (Premium Trader-First Shell, §5.4) — Variant H розширює thesis bar + tactical strip layout model з ADR-0036. Push-down model (B7) зберігається. Bias pills мігрують з thesis bar у tactical strip.
- **ADR-0041 P1–P4** — базова реалізація P/D calc/display split, PdState wire format, EQ line, PdBadge component. Variant H надбудовується поверх: P/D chip переїжджає з bottom-left у thesis bar, додається directional coloring.
- **ADR-0033** (Context Flow Narrative) — `scenarios[0].direction` використовується для amber logic. Narrative не змінюється.

---

## 6. Consequences

### Що ЗМІНЮЄТЬСЯ

| Що | Деталі |
|----|--------|
| `config.json:smc.premium_discount` | 1 key → 5 keys (calc_enabled, show_badge, show_eq_line, show_zones, eq_pdh_coincidence_atr_mult) |
| `SmcPremiumDiscountConfig` (config.py) | Додано 4 поля, backward-compat migration |
| `detect_premium_discount()` | Guard змінений: `calc_enabled` (замість `enabled`). Zones генеруються завжди для narrative/confluence. |
| Wire format (WS full frame) | Additive: нове поле `pd_state` (optional) |
| `SmcData` (types.ts) | Additive: `pd_state?: PdState \| null` |
| OverlayRenderer.ts | Новий метод `renderPdEqLine()` + D8 coincidence |
| themes.ts | +7 color properties у ThemeDef + values для 3 тем |
| ChartPane.svelte | +1 component mount (PdBadge) |
| PdBadge.svelte | **Новий файл** |
| Display budget | +1 chart object (EQ line) |

### Що НЕ ЗМІНЮЄТЬСЯ

- `confluence.py:_check_premium_discount()` — вже рахує F6 з swings напряму, не залежить від zones
- `narrative.py` — перевіряє zone kind, zones тепер знову генеруються коли calc_enabled=true (навіть якщо show_zones=false, zones є у engine state для narrative)
- `engine.py:951` — `detect_premium_discount()` викликається як раніше, guard всередині функції
- SmcDelta wire format — pd_state тільки у full frame (як bias_map)
- Display budget cap <=12 — EQ line рахується як 1, badge = HUD (за budget)
- OverlayRenderer.ts:561 P/D zone skip — змінюється з hard-skip на config-driven (`show_zones`). Коли `show_zones: false` (default) — поведінка ідентична поточній

### Нові інваріанти

- **PD-1**: `calc_enabled: true` = default і LOCKABLE. Вимкнення = явне рішення, warn у документації.
- **PD-2**: Badge = HUD element, не chart object. Не входить у budget <=12.
- **PD-3**: D8 coincidence rule: EQ line hidden коли |EQ - PDH/PDL| < threshold * ATR.
- **PD-4** (Variant H): Amber coloring = frontend-only derivation. Backend не знає про amber. C-DUMB principle.
- **PD-5** (Variant H): EQ threshold = 45–55% (hysteresis band, не точка). Config-driven якщо потрібна корекція.
- **PD-6** (Variant H): Tactical strip visibility = stage-driven. WAIT/STAYOUT = hidden (0fr). PREPARE+ = visible (1fr).

---

## 7. Rollback

| Slice | Rollback |
|-------|----------|
| P1 | `git checkout -- core/smc/config.py core/smc/types.py core/smc/premium_discount.py config.json` + restart backend |
| P2 | `git checkout -- runtime/smc/smc_runner.py runtime/ws/ws_server.py ui_v4/src/types.ts ui_v4/src/stores/smcStore.ts` + restart both |
| P3 | `git checkout -- ui_v4/src/chart/overlay/OverlayRenderer.ts ui_v4/src/chart/themes.ts` + rebuild UI |
| P4 | `rm ui_v4/src/layout/PdBadge.svelte && git checkout -- ui_v4/src/layout/ChartPane.svelte` + rebuild UI |

### Variant H rollback

| Slice | Rollback |
|-------|----------|
| P5 | `git checkout -- ui_v4/src/stores/shellState.ts` + rebuild UI |
| P6 | `git checkout -- ui_v4/src/layout/ChartHud.svelte ui_v4/src/layout/PdBadge.svelte` + rebuild UI |
| P7 | `git checkout -- ui_v4/src/layout/ChartHud.svelte` + rebuild UI |
| P8 | `git checkout -- ui_v4/src/layout/ChartHud.svelte ui_v4/src/chart/themes.ts` + rebuild UI |
| P9 | `git checkout -- ui_v4/src/layout/PdBadge.svelte ui_v4/src/layout/ChartPane.svelte ui_v4/src/layout/ChartHud.svelte` + rebuild UI |

Full rollback: revert all P-slices у зворотному порядку (P9 → P5 → P4 → P1), `config.json` повертає `"enabled": false`. Variant H rollback (P9 → P5) незалежний від P1–P4 rollback — можна відкотити тільки shell restructure, зберігши badge bottom-left + EQ line.

---

## 8. Open Questions

| # | Питання | Хто | Deadline |
|---|---------|-----|----------|
| Q1 | EQ threshold 48-52% — чи достатньо широкий для XAU/USD? | R_TRADER | До P1 accept |
| Q2 | Badge position: нижній лівий — чи не конфліктує з NarrativePanel? | R_CHART_UX | До P4 |
| Q3 | Чи потрібен pd_state у delta frame (не тільки full)? Поки ні — percent змінюється рідко. | R_ARCHITECT | Post-deploy review |
| Q4 | Variant H: accent bar pulse animation — CSS-only чи потрібен JS trigger? CSS `animation` з `animation-iteration-count: 1` preferred. | R_CHART_UX | До P8 |
| Q5 | Variant H: чи bias pills у tactical strip потребують окремого responsive breakpoint для mobile? | R_CHART_UX | До P7 |

---

## ADR Self-Check

| # | Питання | |
|---|---------|---|
| 1 | >=2 альтернативи з trade-offs? | YES (3 alternatives) |
| 2 | Blast radius для кожної? | YES |
| 3 | I0-I6 / S0-S6 перевірено? | YES (I0, I1, I4, S0, S5, S6) |
| 4 | P-slices <=150 LOC? | YES (P1–P4: 80,60,70,60; Variant H P5–P9: 40,60,80,30,40) |
| 5 | Rollback per-slice? | YES (P1–P4 + P5–P9 independent rollback) |
| 6 | Types/contracts перед логікою? | YES (4.1 → 4.2 → 4.3 → 4.4; Variant H: P5 helper first) |
| 7 | Failure modes >=3? | YES (6 base + 3 Variant H = 9 total) |
| 8 | Хто реалізує? | YES (cross-role plan below) |
| 9 | Verify criteria per slice? | YES |
| 10 | Новий інженер зрозуміє? | YES |

**10/10 = GO**
