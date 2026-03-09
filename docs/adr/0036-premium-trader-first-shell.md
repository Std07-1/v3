# ADR-0036: Premium Trader-First Shell for UI v4

- **Статус**: Proposed
- **Дата**: 2026-03-09
- **Автор**: R_ARCHITECT
- **Initiative**: `ui_v4_premium_shell_v1`
- **Пов'язані ADR**: `0008`, `0027`, `0031`, `0032`, `0033`, `0035`

## 1. Контекст і проблема

Поточний `ui_v4` уже сильний як engineering-grade trading shell, але ще не
став category-defining продуктом. Новий [UI spec](../ui%20spec.md) описує
цільовий стан: інтерфейс, який можна впізнати за силуетом, thesis-first
ієрархією та преміальною стриманістю. Для реалізації цього потрібен ADR,
щоб не звести все до хаотичного `visual polish` без архітектурної дисципліни.

### 1.1. Факти з поточного коду

- [VERIFIED `ui_v4/src/layout/ChartHud.svelte:234`] верхній HUD збирається як
  один `hud-row`.
- [VERIFIED `ui_v4/src/layout/ChartHud.svelte:248`]
  [VERIFIED `ui_v4/src/layout/ChartHud.svelte:261`]
  [VERIFIED `ui_v4/src/layout/ChartHud.svelte:290`]
  [VERIFIED `ui_v4/src/layout/ChartHud.svelte:320`] основні блоки HUD
  розділяються через `hud-sep` з символом `·`.
- [VERIFIED `ui_v4/src/layout/ChartHud.svelte:63`] bias формується як набір
  `biasPills`, а не як частина єдиної thesis-композиції.
- [VERIFIED `ui_v4/src/layout/ChartHud.svelte:318`]
  [VERIFIED `ui_v4/src/layout/ChartHud.svelte:327`] narrative already exists,
  але подається як inline badge `TRADE` / `WAIT` плюс tooltip, а не як primary
  decision surface.
- [VERIFIED `ui_v4/src/layout/ChartHud.svelte:353`] killzone already participates
  in narrative rendering, отже проблема не у відсутності домену, а у shell
  ієрархії.
- [VERIFIED `ui_v4/src/App.svelte:472`]
  [VERIFIED `ui_v4/src/App.svelte:477`] replay entry живе окремою кнопкою поза
  єдиним shell-language.
- [VERIFIED `ui_v4/src/App.svelte:483`]
  [VERIFIED `ui_v4/src/App.svelte:539`]
  [VERIFIED `ui_v4/src/App.svelte:541`] brightness, diag, theme controls і clock
  вже винесені в top-right bar, але візуально ще не зібрані в secondary service rail.
- [VERIFIED `ui_v4/src/chart/themes.ts:68`]
  [VERIFIED `ui_v4/src/chart/themes.ts:70`]
  [VERIFIED `ui_v4/src/chart/themes.ts:96`]
  [VERIFIED `ui_v4/src/chart/themes.ts:98`]
  [VERIFIED `ui_v4/src/chart/themes.ts:124`]
  [VERIFIED `ui_v4/src/chart/themes.ts:126`] для всіх тем `hudBg` і `hudBorder`
  залишаються `transparent`, тобто shell ще не має власної матеріальності як
  окремого архітектурного шару.
- [VERIFIED `ui_v4/src/layout/ChartHud.svelte:2`]
  [VERIFIED `ui_v4/src/layout/ChartHud.svelte:500`] сам компонент декларує себе
  як `Frosted-glass HUD overlay`, тобто поточна модель все ще мислить HUD як
  utility overlay, а не як product shell.

### 1.2. Проблема

Поточна архітектура shell правильно показує факти, але не збирає їх у
преміальну `decision hierarchy`:

- немає `one-glance thesis`;
- mode system (`WAIT / TRADE / trigger states`) ще не керує всією композицією;
- сервісний шар і торговий шар вже розділені технічно, але ще не розділені
  достатньо сильно в product language;
- signature interactions існують частково, але не сприймаються як продуктова пам'ять.

### 1.3. Failure model

1. **Pure visual pass without shell semantics**:
   продукт стане красивішим, але не читабельнішим. Трейдер все ще бачитиме
   `good terminal`, а не `decision cockpit`.
2. **Large rewrite of shell state**:
   легко зламати I4 і отримати паралельну UI-правду поруч із вже існуючим
   `narrative` / `bias_map` / `streamState`.
3. **Motion-heavy redesign**:
   premium-feel перетвориться на демонстраційний UI, що конкурує з chart.
4. **Service controls stay first-plane**:
   cognitive noise лишається, навіть якщо палітра стане дорожчою.
5. **UI-local derived thesis drifts from backend narrative**:
   трейдер побачить одну тезу в shell і іншу в narrative details.

## 2. Constraints

- **I4 Один update-потік**: жодного нового паралельного WS/HTTP каналу для shell.
- **S6 Wire format SSOT**: shell повинен спиратися на чинні `NarrativeBlock`,
  `bias_map`, `momentum_map`, session context. Не вводимо другу правду.
- **Chart is sacred**: будь-який shell-pass не має зменшити пріоритет candles.
- **No runtime rewrite**: це UI/shell ADR, не ADR про backend semantics.
- **P-slices ≤150 LOC**: rollout тільки малими перевірними змінами.
- **Desktop + mobile survivability**: shell має стискатися без втрати основної тези.
- **No new dependencies by default**: premium direction досягається layout,
  token, typography, motion discipline, а не library churn.

## 3. Cross-Role Synthesis

### 3.1. R_TRADER verdict

Трейдер вимагає:

- 3-second readability;
- сильну mode hierarchy;
- один короткий висновок замість набору pills;
- chart як головного героя;
- bias / session / narrative як торговий контекст, а не як дрібні utility labels.

### 3.2. R_SMC_CHIEF verdict

SMC Chief вимагає:

- `Clean Chart Doctrine`;
- один сценарій, не dashboard soup;
- operational relevance замість накопичення сигналів;
- secondary telemetry only if it serves current decision.

### 3.3. R_CHART_UX verdict

Chart UX вимагає:

- власний premium silhouette;
- thesis-first shell;
- materiality and depth without fake premium;
- 1-2 signature interactions with product memory;
- market-aware motion, not decorative animation.

### 3.4. R_BUG_HUNTER verdict

Bug Hunter вимагає:

- no duplicate truth between backend narrative and UI shell;
- no new hidden fallback or parallel service rail state;
- no FPS regressions from shell transitions;
- no desync between replay/service/live state and visible chrome.

### 3.5. Архітектурний висновок

Усі чотири ролі сходяться на одному:

- **потрібна структурна зміна shell hierarchy**;
- **не потрібна нова backend semantics**;
- **потрібен локальний presentation layer**, який збирає чинні сигнали в
  thesis-first композицію;
- **потрібен incremental rollout**, а не великий layout rewrite.

## 4. Розглянуті альтернативи

### Альтернатива A: Incremental shell architecture over existing narrative

- **Суть**: зберегти чинні backend contracts і побудувати новий shell з
  локального presentation layer, який збирає `narrative`, `bias_map`,
  `streamState`, replay/service state в `thesis bar + tactical strip + service rail`.
- **Pros**:
  - мінімальний blast radius;
  - не чіпає WS/runtime semantics;
  - легко розбити на P-slices;
  - low rollback cost;
  - відповідає I4 і S6.
- **Cons**:
  - потребує суворої дисципліни, щоб presentation layer не став другою правдою;
  - premium-feel доведеться добувати через дизайн-систему, а не через радикальний layout engine.
- **Blast radius**:
  `ui_v4/src/types.ts`, `ui_v4/src/App.svelte`, `ui_v4/src/layout/ChartHud.svelte`,
  новий helper у `ui_v4/src/app/`, `ui_v4/src/chart/themes.ts`, можливо `ReplayBar.svelte`.
- **LOC estimate**: ~420-560 LOC, розбиті на 5 slices.

### Альтернатива B: New shell subsystem with dedicated stores and scene orchestration

- **Суть**: побудувати новий shell-store та окрему сценографію UI chrome,
  яка самостійно керує thesis, mode, focus, replay, telemetry і transitions.
- **Pros**:
  - максимальна свобода для product language;
  - можна одразу закласти ширшу сценографію для future premium features.
- **Cons**:
  - високий ризик UI split-brain;
  - більший LOC і довший rollback;
  - потребує окремого architecture surface для state ownership;
  - легко вийти за рамки цього initiative.
- **Blast radius**:
  `App.svelte`, `ChartHud.svelte`, нові stores, service controls, replay integration,
  potential type changes across multiple components.
- **LOC estimate**: ~800-1200 LOC.

### Альтернатива C: Art direction only

- **Суть**: не змінювати shell structure, а покращити лише theme, spacing,
  border, typography, motion і visual polish.
- **Pros**:
  - найменший engineering risk;
  - найшвидший time-to-demo.
- **Cons**:
  - не вирішує core issue з thesis hierarchy;
  - залишає `dot-fragmented utility HUD` як основу;
  - award-level ефект буде поверхневим.
- **Blast radius**:
  здебільшого `themes.ts` і CSS у `ChartHud.svelte` / `App.svelte`.
- **LOC estimate**: ~150-250 LOC.

### Вибір: Альтернатива A

**Обґрунтування**:

Альтернатива A дає саме той рівень архітектурної зміни, який вимагає spec:

- достатньо сильна, щоб змінити shell semantics;
- достатньо мала, щоб не зламати існуючий real-time stack;
- достатньо дисциплінована, щоб не створити другу truth layer.

B занадто широка для цього initiative. C занадто слабка для category-icon цілі.

## 5. Рішення

### 5.1. Types / Contracts (FIRST)

Додаємо **UI-local semantic presentation types** у `ui_v4/src/types.ts`:

- `ShellStage = 'wait' | 'prepare' | 'ready' | 'triggered'`
- `ThesisBarData`
- `TacticalStripData`
- `ServiceRailState`

Ці типи **не змінюють backend contracts** і не виходять за `ui_v4`.
Вони тільки типізують presentation layer, який derive-иться з існуючих
`NarrativeBlock`, `bias_map`, `momentum_map`, `streamState`, replay/service state.

### 5.2. Pure Logic

Новий pure helper: `ui_v4/src/app/shellState.ts`.

Функції:

- `deriveShellStage(narrative) -> ShellStage`
- `deriveThesisBarData(narrative, biasMap, streamState) -> ThesisBarData`
- `deriveTacticalStripData(narrative, biasMap, momentumMap) -> TacticalStripData`
- `deriveServiceRailState(replayActive, freshness, diagnosticsOpen) -> ServiceRailState`

Правило:

- shell **не винаходить** нові торгові семантики;
- shell лише перетворює існуючі сигнали на product hierarchy.

### 5.3. Runtime Integration

Runtime changes **не потрібні**.

Shell споживає вже існуючі дані:

- `narrative` з WS full/delta [ADR-0033]
- `bias_map` / `momentum_map` [ADR-0031]
- session / killzone context [ADR-0035]
- replay state [ADR-0027]
- TF switch stability guarantees [ADR-0032]

Це свідоме обмеження, щоб не порушити I4 та не роздути blast radius.

### 5.4. UI Wiring

#### Primary layer

`ChartHud.svelte` перестає бути просто `utility HUD row` і стає shell з трьох шарів:

1. `Thesis Bar`
2. `Tactical Strip`
3. `Context Reveal`

#### Secondary layer

`App.svelte` top-right bar залишається окремим, але явно стає
`service rail`, а не рівноправним конкурентом thesis.

#### Structural rules

- `symbol + tf` лишаються entry point у shell, але вбудовуються у left anchor,
  а не формують логіку всього рядка;
- `price + freshness` стають compact verification cluster;
- `bias + state + POI + action` формують primary reading order;
- `session + narrative cue + confidence` формують tactical strip;
- diagnostics, brightness, theme, replay, clock демотуються в secondary service rail.

#### Interaction rules

- `TF switch` стає signature interaction з коротким compression/expansion response;
- `Thesis reveal` відкриває tactical explanation без modal takeover;
- `Focus mode` стискає service noise і залишає chart + thesis first-plane;
- `Replay` входить у ту ж shell language, а не живе окремою випадковою кнопкою.

### 5.5. Visual system

`themes.ts` більше не тримає shell у повністю transparent state.

Додаємо семантичні shell tokens:

- `shellPrimaryBg`
- `shellPrimaryBorder`
- `shellSecondaryBg`
- `shellSecondaryBorder`
- `shellQuietText`
- `shellAccentText`
- `shellModeWait`
- `shellModePrepare`
- `shellModeReady`
- `shellModeTriggered`

Перший pass не змінює chart palette. Він змінює лише shell materials,
spacing, radius rhythm, typography hierarchy і motion discipline.

### 5.6. Config

У цьому ADR **немає нових `config.json` ключів**.

Причина:

- shell semantics derive-яться з уже наявних runtime signals;
- feature rollout можна контролювати маленькими slices і rollback,
  не вводячи нову config surface передчасно.

Допустимий local UI persistence only:

- `v4_focus_mode`
- `v4_shell_density`

Це UI-local preferences, не SSOT policy.

## 6. P-Slices

| Slice | Scope | LOC | Інваріант | Verify | Rollback |
|---|---|---:|---|---|---|
| P1 | UI semantic types + `shellState.ts` | ~90 | I4, S6 | `npm run check` + unit smoke for derive helpers | revert types/helper |
| P2 | `ChartHud` restructure into thesis bar + tactical strip | ~130 | chart-first hierarchy | `npm run build` + browser smoke | revert `ChartHud.svelte` |
| P3 | `App.svelte` service rail normalization + replay integration | ~110 | I4, no split-brain | `npm run build` + replay smoke | revert `App.svelte` |
| P4 | shell tokens + materials + typography + mode states | ~120 | no chart readability loss | browser visual QA on dark/black/light | revert `themes.ts` + CSS |
| P5 | signature interactions: TF switch, thesis reveal, focus mode | ~140 | perf budget, accessibility | browser QA + interaction smoke | revert interaction helpers |

## 7. Наслідки

### 7.1. Що змінюється

- shell перестає бути utility-first і стає thesis-first;
- product hierarchy стає явною;
- service controls залишаються доступними, але перестають бути first-plane;
- mode system стає головним читачем urgency;
- replay/focus/TF transitions отримують product memory.

### 7.2. Що НЕ змінюється

- backend WS contracts;
- UDS / runtime / derive / SMC engine logic;
- candle render pipeline;
- session / killzone semantics;
- bias and narrative calculation rules.

### 7.3. Нові rails

- shell must derive from existing canonical state only;
- no new parallel data feed for thesis;
- no shell animation that obscures candles;
- no service control promoted to first-plane unless it directly changes trader decision.

### 7.4. Performance impact

Очікуваний impact low-to-medium:

- трохи більше shell DOM/CSS work;
- zero new backend cost;
- critical risk only in hover/reveal and TF transition path.

Budget:

- no measurable regression in chart interaction;
- shell transitions must stay under a single normal UI frame budget;
- no repeated expensive recomputation outside reactive input changes.

## 8. Rollback

### Per-slice rollback

1. `P1`: revert new shell semantic types and helper.
2. `P2`: revert `ChartHud.svelte` to current inline HUD structure.
3. `P3`: revert `App.svelte` service rail / replay shell integration.
4. `P4`: revert shell tokens and premium materials.
5. `P5`: revert signature interaction helpers and focus mode behavior.

### Full rollback

Повний rollback = повернення до поточного `utility HUD + top-right bar`
без зміни runtime semantics. Це свідомо робить ADR безпечним для ітеративного adoption.

## 9. Open Questions

1. Чи stage mapping має бути строго `WAIT / PREPARE / READY / TRIGGERED`,
   чи `PREPARE` треба виводити лише коли є сценарій без trigger?
2. Чи editorial font pair має бути локально вбудованою,
   чи перший pass лишається на existing stack без font dependency?
3. Який mobile collapse order: `thesis first`, `symbol/tf second`, `service rail last`
   чи інший порядок?
4. Чи `Focus mode` лишається UI-only preference, чи пізніше потребує SSOT config surface?

## 10. Architect Self-Check

- [x] ≥2 альтернативи описано чесно
- [x] blast radius вказано
- [x] I0–I6 / S0–S6 не порушуються
- [x] P-slices визначено
- [x] rollback per-slice є
- [x] types/contracts описані before wiring
- [x] failure model ≥3 сценаріїв
- [x] cross-role synthesis зафіксований
- [x] verify criteria задані
- [x] ADR читається без прихованого контексту

**Результат: 10/10, Proposed → ready for challenge.**
