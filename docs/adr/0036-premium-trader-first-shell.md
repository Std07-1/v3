# ADR-0036: Premium Trader-First Shell for UI v4

- **Статус**: Implemented (P1–P5 complete, P6 partial — signal = ADR-0039)
- **Дата**: 2026-03-09 (original) / 2026-03-14 (amendment v2) / 2026-03-14 (amendment v3)
- **Автор**: R_ARCHITECT
- **Initiative**: `ui_v4_premium_shell_v1`
- **Пов'язані ADR**: `0008`, `0027`, `0031`, `0032`, `0033`, `0035`, `0039`
- **Visual reference**: `research/trader_shell_v5 (1).html`

---

## Amendment v3 — Changelog

| # | Секція | Зміна | Причина | Blocker |
|---|--------|-------|---------|---------|
| B1 | §5.2 Stage Mapping | Розбито trigger mapping: кожен trigger value (`approaching`, `in_zone`, `ready`, `triggered`) — окремий рядок з чітким ShellStage | v2 лумпував `approaching/in_zone/ready` в один `ready` — не відповідає `_resolve_trigger_state()` семантиці | YES |
| B2 | §5.2 Multi-scenario | Додано правило: ShellStage визначається по `scenarios[0].trigger` (primary). Alt scenario (index 1) — informational | Не було правила для multi-scenario | YES |
| B3 | §5.2 Fallback guard | Додано guard: `narrative.warnings` з error-маркерами -> завжди `wait`, ніколи `stayout` | Fallback narrative (`mode=wait, session=""`) хибно давав `stayout` | YES |
| B4 | §5.2 Sessions guard | Додано `sessions_active: bool` параметр до `compose_shell_payload()`. `stayout` тільки коли sessions_active=True | `sessions.enabled=false` -> `current_session=""` не означає off-session | YES |
| B5 | §5.2 Mapping table | Колонка `in_killzone` замінена на `trigger (scenarios[0].trigger)` — це різний тип даних | Семантичне змішування bool і string колонки | YES |
| B6 | §5.3.2 Exception guard | Додано try/except + `_log.warning("WS_SHELL_ERR ...")` + return frame without shell | Не було exception guard для `compose_shell_payload()` | YES |
| B7 | §5.4 Layout model | Додано push-down model: shell fixed top, chart `margin-top = shell height` | Не було визначено layout model для мікро-карти. Overlay заборонено (N1) | YES |
| B8 | §5.5 WCAG fix | `stctx` token: `rgba(255,255,255,0.30)` -> `rgba(255,255,255,0.45)` (4.7:1 vs 2.6:1) | WCAG AA FAIL для 10px тексту | YES |
| B9 | §5.2 D1 cfl downgrade | Додано правило: D1 `cfl` chip -> `ready` downgrade to `prepare`, `triggered` to `prepare` | HTF Hierarchy порушення: D1 проти всіх не може бути READY | YES |
| B10 | §5.2 Direction | Додано алгоритм direction: `scenarios[0].direction` або None. Direction-суфіксовані labels для ready/triggered | NarrativeBlock не має top-level direction | YES |
| SE1 | §5.1 Types | Додано `signal: Optional[SignalSnapshot]` до `ShellPayload`. Schema SignalSnapshot — OWNED BY ADR-0039 (R_SIGNAL_ARCHITECT), не визначається тут | ADR-0039 integration slot | — |
| SE2 | §5.1 Types | ВИДАЛЕНО повну `SignalSnapshot` schema з ADR-0036. ADR-0036 тільки оголошує opaque slot з посиланням на ADR-0039 | Boundary violation: schema signal = territory R_SIGNAL_ARCHITECT | — |
| SE3 | §5.2 Signal rule | `signal` заповнюється тільки для `ready`/`triggered`. Поки ADR-0039 не реалізований — `signal=None` завжди | Graceful degradation | — |
| SE4 | §5.3.3 | Нова секція: ADR-0039 Integration Point — opaque pass-through, не обчислення | Shell не знає як рахується сигнал | — |
| SE5 | §5.4 Signal rendering | Rendering contract: "signal present → рендерити; None → row відсутній." Конкретні поля — ADR-0039 | C-DUMB + boundary | — |
| M1 | §5.4 Delta frames | UI кешує shell payload; delta без shell — не скидає | Tick-relay delta frames | — |
| M2 | §5.3 Observability | `WS_SHELL_ERR` logging + `api/status` shell fields | Observability gap | — |
| M3 | §5.4.1 Killzone warning | Thesis bar subtle warning для prepare/ready без killzone | Trader вимога | — |
| M4 | §5.4.1 R:R in triggered | `stage_context` включає R:R коли signal доступний | Trader вимога | — |
| M7 | §9 Open Questions | Закрито Q5 (stayout sub_mode), Q6 (transition period) | CLOSED (v3) | — |
| M8 | §5.3.2 Blast radius | Виправлено: `aione_top` — HTTP monitor, не WS consumer | Фактична помилка | — |

---

## Amendment v2 — Changelog

| # | Секція | Зміна | Причина |
|---|--------|-------|---------|
| A1 | §2 Constraints | Додано **hard constraint C-DUMB**: frontend = dumb renderer | Архітектурний принцип: уся derive-логіка — на бекенді |
| A2 | §5.1 Types | Розділено на Wire Format Types + UI-Local Types | Wire types — SSOT бекенду, UI types — лише render hints |
| A3 | §5.2 Pure Logic | **Видалено** frontend derive functions. Замінено на backend shell composer | `deriveShellStage()` та ін. — порушували C-DUMB |
| A4 | §5.3 Runtime | **Переписано**: runtime changes тепер ПОТРІБНІ | Потрібен `shell_composer.py` або розширення narrative |
| A5 | §5.4 UI Wiring | Додано micro-card (4 поля + warning), TF strip chips, strip collapsibility | Delta від v5 prototype |
| A6 | §5.5 Visual | Замінено `shellMode*` tokens на `--sb/--sa/--st/--ss` CSS vars per stage | v5 prototype visual system |
| A7 | §5.1 ShellStage | Додано `'stayout'` (5-й stage) | v5 prototype |
| A8 | §6 P-slices | Переоцінено: P1 тепер включає wire types + backend stub | Backend-first implementation order |
| A9 | §9 Open Questions | Закрито Q1 (stage mapping) та Q4 (focus mode) | v5 відповідає |

---

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

### 1.1a. Факти з runtime (Amendment v2)

- [VERIFIED `runtime/ws/ws_server.py:652`] `narrative` вже є wire field у full frame:
  `frame["narrative"] = narrative_to_wire(_narr)`.
- [VERIFIED `runtime/ws/ws_server.py:1054`] narrative також оновлюється в delta frame
  при complete bars.
- [VERIFIED `runtime/ws/ws_server.py:389-428`] `_build_full_frame()` приймає
  `bias_map`, `momentum_map` і вбудовує їх у frame. `narrative` додається пізніше
  окремим блоком.
- [VERIFIED `runtime/smc/smc_runner.py:378-416`] `SmcRunner.get_narrative()` вже
  збирає: `snapshot + bias_map + grades + momentum + session_info` →
  `synthesize_narrative()` → `NarrativeBlock`.
- [VERIFIED `core/smc/narrative.py:457-459`] mode/sub_mode (`trade`/`wait` +
  `aligned`/`reduced`) вже обчислюються на бекенді з alignment logic (D1+H4).
- [VERIFIED `core/smc/narrative.py:600-613`] `narrative_to_wire()` серіалізує:
  `mode`, `sub_mode`, `headline`, `bias_summary`, `scenarios`, `next_area`,
  `fvg_context`, `market_phase`, `warnings`, `current_session`, `in_killzone`,
  `session_context`.
- [VERIFIED `core/smc/types.py:289-309`] `NarrativeBlock` dataclass вже має
  `mode`, `sub_mode`, `headline`, `bias_summary`, `scenarios`, session fields.
- [VERIFIED `ui_v4/src/types.ts:99-113`] TypeScript `NarrativeBlock` interface
  mirrors Python dataclass.
- [VERIFIED `ui_v4/src/layout/ChartHud.svelte:323-327`] Frontend вже використовує
  `narrative.mode` для TRADE/WAIT badge — це **чистий rendering** з wire data,
  без local derive logic.
- [VERIFIED `runtime/smc/smc_runner.py:346-363`] `get_bias_map()` і
  `get_momentum_map()` — вже обчислюються бекендом, передаються по WS.

### 1.1b. Факти з runtime (Amendment v3)

- [VERIFIED `core/smc/narrative.py:155-166`] `_resolve_trigger_state()` повертає
  4 distinct значення: `"ready"` (CHoCH + in_zone), `"triggered"` (CHoCH only),
  `"in_zone"` (in zone, no CHoCH), `"approaching"` (neither).
- [VERIFIED `core/smc/narrative.py:60-72`] `_fallback_narrative_block()` повертає
  `mode="wait"`, `sub_mode=""`, `current_session=""`, `scenarios=[]`,
  `warnings=["computation_error"]`.
- [VERIFIED `runtime/smc/smc_runner.py:393`] `sessions.enabled` перевіряється
  перед заповненням `session_info`; якщо disabled, NarrativeBlock отримує
  default `current_session=""`.
- [VERIFIED `core/smc/types.py:282`] `ActiveScenario.trigger` type:
  `"approaching" | "in_zone" | "triggered" | "ready"`.
- [VERIFIED `core/smc/types.py:280`] `ActiveScenario.direction` = `"long" | "short"`.

### 1.2. Проблема

Поточна архітектура shell правильно показує факти, але не збирає їх у
преміальну `decision hierarchy`:

- немає `one-glance thesis`;
- mode system (`WAIT / TRADE / trigger states`) ще не керує всією композицією;
- сервісний шар і торговий шар вже розділені технічно, але ще не розділені
  достатньо сильно в product language;
- signature interactions існують частково, але не сприймаються як продуктова пам'ять.

**Amendment v2 problem extension**: оригінальний ADR пропонував derive-логіку
на фронтенді (`shellState.ts: deriveShellStage()` та ін.), що створює ризик
split-brain між backend narrative і frontend shell stage. Prototype v5 показав,
що shell потребує 5 stages (не 4) та розширений micro-card. Ці дані мають
обчислюватися бекендом і передаватися як wire format extension.

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
6. **(v2) Frontend derive functions create second truth**:
   `deriveShellStage()` на фронті може дати `'ready'`, а бекенд narrative каже
   `mode='wait'` — split-brain без механізму reconciliation.
7. **(v2) Wire format extension breaks existing consumers**:
   додавання `shell` поля в frame може зламати парсери, якщо не additive-only.
8. **(v3) Fallback narrative silently produces `stayout`**:
   `_fallback_narrative_block()` повертає `mode=wait, session="", scenarios=[]` —
   без guard це хибно дає `stayout` замість `wait`.
9. **(v3) Sessions disabled produces permanent `stayout`**:
   `sessions.enabled=false` → `current_session=""` → без `sessions_active` guard
   shell завжди показує STAY OUT.

## 2. Constraints

- **I4 Один update-потік**: жодного нового паралельного WS/HTTP каналу для shell.
- **S6 Wire format SSOT**: shell повинен спиратися на чинні `NarrativeBlock`,
  `bias_map`, `momentum_map`, session context. Не вводимо другу правду.
- **Chart is sacred**: будь-який shell-pass не має зменшити пріоритет candles.
- **P-slices <= 150 LOC**: rollout тільки малими перевірними змінами.
- **Desktop + mobile survivability**: shell має стискатися без втрати основної тези.
- **No new dependencies by default**: premium direction досягається layout,
  token, typography, motion discipline, а не library churn.

### C-DUMB: Frontend = Dumb Renderer (Amendment v2 — HARD CONSTRAINT)

> **Frontend НЕ МІСТИТЬ жодної derive-логіки для shell.**
>
> - Frontend отримує по WS вже обчислені `shell_stage`, `micro_card`,
>   `tactical_strip`, `service_rail_hints`.
> - Frontend **не містить** `if narrative.mode == 'trade' && ...` для визначення stage.
> - Frontend тільки: отримав дані -> відрендерив.
> - Єдиний дозволений UI-local стан: collapsed/expanded (strip, micro-card),
>   focus mode preference, animation phase.

**Обґрунтування C-DUMB**:

1. `NarrativeBlock` вже обчислюється на бекенді (`synthesize_narrative()`).
   Shell stage — це production від того самого narrative. Обчислювати його
   окремо на фронті = подвійна правда (Failure model #5, #6).
2. Backend вже має всі inputs: snapshot, bias_map, grades, momentum,
   session_info. Фронт не має — тому derive на фронті = incomplete projection.
3. Тестування: pure Python function з pytest vs Svelte reactive chain з
   browser testing. Бекенд виграє по тестованості.
4. Rollback: якщо shell logic має баг, бекенд-фікс = один deploy без UI rebuild.

**Наслідок для оригінального §5.2**: `shellState.ts` з `deriveShellStage()`,
`deriveThesisBarData()`, `deriveTacticalStripData()`, `deriveServiceRailState()`
— **ВИДАЛЯЄТЬСЯ**. Замість нього — backend `compose_shell_payload()`.

### C-RUNTIME: Runtime changes ПОТРІБНІ (Amendment v2 — reversal of original §5.3)

> Оригінальний ADR §5.3 стверджував: "Runtime changes не потрібні".
> Amendment v2 **скасовує** це. Shell payload обчислюється бекендом →
> потрібні зміни в runtime.

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
- **(v2) потрібна backend shell computation** що збирає чинні сигнали в
  один `ShellPayload`;
- **потрібен presentation layer на UI**, який тільки рендерить отримані дані;
- **потрібен incremental rollout**, а не великий layout rewrite.

## 4. Розглянуті альтернативи

### Альтернатива A: Extend NarrativeBlock wire format + thin UI renderer

- **Суть**: додати shell-specific поля (`shell_stage`, `micro_card`,
  `tactical_strip`) прямо в `NarrativeBlock` / `narrative_to_wire()`.
  UI отримує готові дані, тільки рендерить.
- **Pros**:
  - мінімальний wire format churn — `narrative` поле вже існує;
  - один обчислювальний pipeline (synthesize_narrative -> compose_shell);
  - C-DUMB дотримано by construction;
  - тестується pytest;
  - backward compatible: нові поля additive, старі клієнти ігнорують.
- **Cons**:
  - `NarrativeBlock` стає "товстішим" — порушує SRP якщо shell семантики
    занадто далекі від narrative;
  - якщо shell stage розходиться з narrative mode — потрібна додаткова логіка.
- **Blast radius**:
  `core/smc/types.py` (NarrativeBlock), `core/smc/narrative.py` (synthesize +
  wire), `runtime/smc/smc_runner.py` (get_narrative), `ui_v4/src/types.ts`,
  `ui_v4/src/layout/ChartHud.svelte`.
- **LOC estimate**: ~350-450 LOC across backend + frontend.

### Альтернатива B: New ShellPayload as separate wire field + shell_composer.py

- **Суть**: створити окремий `ShellPayload` (новий тип) і окремий модуль
  `core/smc/shell_composer.py` (pure function). WS frame отримує нове поле
  `"shell": {...}` поруч із `"narrative": {...}`.
- **Pros**:
  - чіткий SRP: narrative = text analysis, shell = UI composition;
  - ShellPayload може еволюціонувати незалежно від NarrativeBlock;
  - легко тестувати окремо;
  - не роздуває існуючий NarrativeBlock.
- **Cons**:
  - два виклики замість одного (`get_narrative()` + `compose_shell()`);
  - дублювання input збору (обидва потребують snapshot, bias, session);
  - новий wire field — хоч і additive, але більший diff.
- **Blast radius**:
  новий `core/smc/shell_composer.py`, `core/smc/types.py` (ShellPayload),
  `runtime/ws/ws_server.py` (_build_full_frame + delta_loop),
  `runtime/smc/smc_runner.py`, `ui_v4/src/types.ts`,
  `ui_v4/src/layout/ChartHud.svelte`.
- **LOC estimate**: ~450-550 LOC.

### Альтернатива C (original ADR-0036 v1): Frontend derive functions

- **Суть**: залишити backend без змін, створити `shellState.ts` з derive
  functions на фронтенді.
- **Pros**:
  - zero backend changes;
  - faster first iteration.
- **Cons**:
  - **порушує C-DUMB** — frontend стає smart;
  - split-brain ризик (failure model #5, #6);
  - погана тестованість;
  - incomplete inputs (фронт не має повний snapshot/grades/momentum).
- **Blast radius**: тільки `ui_v4/`.
- **LOC estimate**: ~420-560 LOC.

**REJECTED**: порушує C-DUMB hard constraint.

### Вибір: Альтернатива B — Separate ShellPayload + shell_composer.py

**Обґрунтування**:

1. **SRP**: NarrativeBlock вже має 12 полів. Додавати ще 4 shell-specific
   структури = god-object. ShellPayload — окрема відповідальність.
2. **Еволюція**: shell UX буде ітеруватися швидше ніж narrative engine.
   Окремий тип = окремий release cadence.
3. **Тестування**: `shell_composer.py` — чистий модуль з одним вхідним
   контрактом (`NarrativeBlock + bias_map + session_info -> ShellPayload`).
   Один `pytest` файл покриває всі 5 stages + micro-card + strip.
4. **Wire compatibility**: нове поле `"shell"` additive — існуючі клієнти
   (якщо є) продовжують працювати.
5. **Дублювання input** мінімізується: `compose_shell()` приймає вже
   обчислений `NarrativeBlock` (не raw data), тобто це **post-processing**
   narrative, а не паралельний pipeline.

## 5. Рішення

### 5.1. Types / Contracts (FIRST)

#### 5.1.1 Wire Format Types (backend-computed, SSOT in `core/smc/types.py`)

Новий dataclass `ShellPayload` в `core/smc/types.py`:

```
ShellStage = 'wait' | 'prepare' | 'ready' | 'triggered' | 'stayout'

@dataclass(frozen=True)
ShellPayload:
    stage: str                    # ShellStage
    stage_label: str              # "WAIT" | "PREPARE" | "SHORT · READY" | "SHORT · TRIGGERED" | "STAY OUT"
    stage_context: str            # "Bearish HTF · Inside supply · Waiting CHoCH"
    micro_card: MicroCard         # 4-field explanation
    tactical_strip: TacticalStrip # TF alignment chips
    signal: Optional[SignalSnapshot]  # (v3) ADR-0039 integration — None until signal-engine implemented
    # to_wire() -> dict

@dataclass(frozen=True)
MicroCard:
    mode_text: str                # "Чекаємо" | "Готуємось" | "Готовий до шорту" | ...
    why_text: str                 # "HTF bearish, ціна всередині supply — немає підтвердження входу"
    what_needed: str              # "CHoCH на M15 від верху зони"
    what_cancels: str             # "Закриття свічки вище 5131"
    warning: Optional[str]        # "Поза кілзоною — низька якість сетапу" | None

@dataclass(frozen=True)
TacticalStrip:
    alignment_type: str           # 'htf_aligned' | 'mixed'
    alignment_direction: Optional[str]  # 'bullish' | 'bearish' | None
    chips: List[TfChip]           # Per-TF bias chips
    tag_text: str                 # "Контекст чистий" | "H1 проти тренду" | "D1 проти всіх"
    tag_variant: str              # 'ok_bull' | 'ok_bear' | 'warn' | 'danger'

@dataclass(frozen=True)
TfChip:
    tf_label: str                 # "D1" | "H4" | "H1" | "M15"
    direction: str                # 'bullish' | 'bearish'
    chip_state: str               # 'normal' | 'brk' | 'cfl'
    # 'brk' = break: this TF broke structure first (yellow highlight)
    # 'cfl' = conflict: this TF is macro-against-all (red highlight)

# (v3) ADR-0039 integration — opaque slot for signal data.
# SignalSnapshot type is OWNED BY ADR-0039 (R_SIGNAL_ARCHITECT).
# ADR-0036 does NOT define its schema — only declares the rendering contract.
# Import: from core.smc.signals import SignalSnapshot  (ADR-0039 module)
# Type: Optional["SignalSnapshot"]  — opaque to shell_composer
```

Wire serialization: `ShellPayload.to_wire() -> dict`, вбудовується у frame як
`frame["shell"] = shell_payload.to_wire()`.

**Правило signal** (v3): `signal` заповнюється тільки коли:
1. `shell_stage in ('ready', 'triggered')`, AND
2. ADR-0039 Signal Engine має активний сигнал для даного symbol/zone.

Для `wait`/`prepare`/`stayout` -> `signal = None`.

**Graceful degradation** (v3): Поки ADR-0039 не реалізований, `signal = None` завжди.
Shell показує READY/TRIGGERED без конкретних цифр entry/SL/TP. Дизайн
`ShellPayload.signal` сумісний з майбутнім виходом ADR-0039 — no breaking change
при додаванні. Frontend не обчислює цифри сам — тільки рендерить що прийшло.

#### 5.1.2 Wire Format Extension (TypeScript mirror in `ui_v4/src/types.ts`)

```typescript
// Amendment v2: shell wire types (backend-computed)
export type ShellStage = 'wait' | 'prepare' | 'ready' | 'triggered' | 'stayout';

export interface MicroCard {
  mode_text: string;
  why_text: string;
  what_needed: string;
  what_cancels: string;
  warning: string | null;
}

export interface TfChip {
  tf_label: string;
  direction: 'bullish' | 'bearish';
  chip_state: 'normal' | 'brk' | 'cfl';
}

export interface TacticalStrip {
  alignment_type: 'htf_aligned' | 'mixed';
  alignment_direction: 'bullish' | 'bearish' | null;
  chips: TfChip[];
  tag_text: string;
  tag_variant: 'ok_bull' | 'ok_bear' | 'warn' | 'danger';
}

// (v3) ADR-0039 integration — SignalSnapshot type OWNED BY ADR-0039.
// ADR-0036 does not define its schema.
// Import from signal-engine types when ADR-0039 is implemented.
// For now: use `unknown` as placeholder — frontend renders only if signal != null.
export type SignalSnapshot = import('./signal_types').SignalSnapshot;  // ADR-0039 SSOT

export interface ShellPayload {
  stage: ShellStage;
  stage_label: string;
  stage_context: string;
  micro_card: MicroCard;
  tactical_strip: TacticalStrip;
  signal: SignalSnapshot | null;  // (v3) null until ADR-0039 implemented
}

// Extension to RenderFrame:
//   shell?: ShellPayload;    // additive — full + delta on complete bars
```

#### 5.1.3 UI-Local Types (render-only, NOT wire format)

UI-local стан, що **не передається** по WS:

- `micro_card_open: boolean` — розгорнуто/згорнуто (toggle by click on thesis bar)
- `strip_collapsed: boolean` — TF strip видимий/схований (toggle by strip handle)
- `focus_mode: boolean` — UI preference (стискає service rail)
- `shell_animation_phase: 'idle' | 'transitioning'` — для CSS transition tracking
- `last_shell: ShellPayload | null` — (v3) кеш останнього shell для delta frame persistence

Ці типи живуть в Svelte component state або в UI-local store. Жодного зв'язку
з бекендом.

### 5.2. Pure Logic (backend shell composer)

**СКАСОВАНО**: frontend `shellState.ts` з derive functions (original v1).

**ЗАМІНЕНО**: backend pure module `core/smc/shell_composer.py`.

Сигнатура основної функції (v3 — updated):

```
def compose_shell_payload(
    narrative: NarrativeBlock,
    bias_map: Dict[str, str],        # {"900": "bullish", "3600": "bearish", ...}
    viewer_tf_s: int,                 # поточний TF viewer
    config: Dict[str, Any],           # smc.shell config section
    sessions_active: bool,            # (v3/B4) True if smc.sessions.enabled=True
    signal: Optional[SignalSnapshot] = None,  # (v3/SE) ADR-0039 signal, if available
) -> ShellPayload
```

#### 5.2.1 Stage mapping table (v3 — rewritten per B1/B2/B3/B4/B5/B9/B10)

**Pre-condition: Error guard (B3)**

Перед будь-яким stage mapping, перевіряємо error markers:

```
_ERROR_MARKERS = {"computation_error", "no_snapshot", "narrative_timeout"}

if narrative.warnings and (set(narrative.warnings) & _ERROR_MARKERS):
    -> ShellStage = 'wait' (NEVER 'stayout' on backend errors)
```

**Pre-condition: Sessions guard (B4)**

`stayout` дозволений **тільки** коли `sessions_active=True`. Якщо sessions
не ввімкнені, `current_session=""` означає "sessions not tracked", а не
"off-session". Guard:

```
if not sessions_active:
    stayout is NOT reachable (any path that would produce stayout -> 'wait')
```

**Multi-scenario rule (B2)**

ShellStage визначається по `scenarios[0].trigger` (primary scenario). Альтернативний
scenario (`scenarios[1]`, if present) — informational only, не впливає на stage.

**Direction resolution (B10)**

```
direction = scenarios[0].direction if len(scenarios) > 0 else None
```

Labels `ready`/`triggered` використовують direction-суфіксовані ключі
(`ready_long`/`ready_short`, `triggered_long`/`triggered_short`).
Для `wait`/`prepare`/`stayout` — direction не використовується в label.

**Stage mapping (pure, deterministic)**:

| # | mode | sub_mode | len(scenarios) | trigger (`scenarios[0].trigger`) | sessions_active | current_session | -> ShellStage | stage_label key |
|---|------|----------|----------------|----------------------------------|-----------------|-----------------|---------------|-----------------|
| 1 | `wait` | `""` | 0 | n/a | `True` | `""` (off-session) | `stayout` | `stayout` |
| 2 | `wait` | `""` | 0 | n/a | `False` | `""` | `wait` | `wait` |
| 3 | `wait` | `""` | 0 | n/a | any | non-empty | `wait` | `wait` |
| 4 | `wait` | `""` | >0 | any | any | any | `wait` | `wait` |
| 5 | `trade` | `reduced` | >0 | any | any | any | `prepare` | `prepare` |
| 6 | `trade` | `aligned` | >0 | `approaching` | any | any | `prepare` | `prepare` |
| 7 | `trade` | `aligned` | >0 | `in_zone` | any | any | `ready` | `ready_{direction}` |
| 8 | `trade` | `aligned` | >0 | `ready` | any | any | `ready` | `ready_{direction}` |
| 9 | `trade` | `aligned` | >0 | `triggered` | any | any | `triggered` | `triggered_{direction}` |

**Fallback**: будь-який невідомий стан -> `wait` (conservative).

**Post-condition: D1 conflict downgrade (B9)**

Після визначення stage, перевіряємо tactical strip. Якщо `tactical_strip.chips`
містить D1 chip з `chip_state='cfl'` (D1 проти всіх — порушення HTF Hierarchy):

```
if any(c.tf_label == "D1" and c.chip_state == "cfl" for c in tactical_strip.chips):
    if stage == 'ready':     -> downgrade to 'prepare'
    if stage == 'triggered': -> downgrade to 'prepare'
```

Це відповідає SMC Doctrine: D1 = macro trend. Якщо D1 проти всіх інших TF,
READY і TRIGGERED неприпустимі незалежно від trigger state.

**stage_context format (v3/M4)**:

Для `triggered` stage з `signal != None`:
```
stage_context = "{direction_label} active · R:R {rr_ratio}:1 · SL {sl_price}"
```
Приклад: `"Short active · R:R 3.1:1 · SL 2881.50"`

Для `triggered` без signal: standard context з bias summary.

**Signal injection rule (v3/SE3)**:

```
if stage in ('ready', 'triggered') and signal is not None:
    payload.signal = signal
else:
    payload.signal = None
```

#### 5.2.2 TacticalStrip algorithm (pure)

1. Збирає bias_map для TFs `[86400, 14400, 3600, 900]` (D1, H4, H1, M15).
2. Якщо всі однакові -> `alignment_type='htf_aligned'`, `tag_variant='ok_bull'/'ok_bear'`.
3. Якщо один TF відрізняється:
   - Якщо це не D1 -> `chip_state='brk'` для того TF, `tag_variant='warn'`.
   - Якщо це D1 (macro) vs решта -> D1 chip = `'cfl'`, `tag_variant='danger'`.
4. Інакше -> `alignment_type='mixed'`, кожен chip з відповідним direction.

#### 5.2.3 MicroCard algorithm (pure)

Формується з `NarrativeBlock` fields:
- `mode_text`: lookup table per stage (config-driven)
- `why_text`: `narrative.bias_summary`
- `what_needed`: `narrative.scenarios[0].trigger_desc` або fallback text
- `what_cancels`: `narrative.scenarios[0].invalidation` або fallback text
- `warning`: якщо `not narrative.in_killzone and narrative.current_session` -> killzone warning; якщо `sessions_active and not narrative.current_session` -> off-session warning; інакше `None`.

**Файл**: `core/smc/shell_composer.py` (pure, no I/O — дотримує I0).

**Тести**: `tests/smc/test_shell_composer.py` — один тест per stage + edge cases
(fallback guard, sessions disabled, D1 cfl downgrade, multi-scenario).

### 5.3. Runtime Integration

**СКАСОВАНО**: "Runtime changes не потрібні" (original v1).

**ЗАМІНЕНО**: потрібні зміни в `runtime/smc/smc_runner.py` та `runtime/ws/ws_server.py`.

#### 5.3.1 SmcRunner extension

Новий метод в `SmcRunner` (v3 — updated signature):

```
def get_shell_payload(self, symbol, viewer_tf_s, narrative, signal=None):
    # type: (str, int, NarrativeBlock, Optional[SignalSnapshot]) -> Optional[ShellPayload]
    """Compose shell payload from already-computed narrative + bias_map.

    Returns None if shell is disabled. Catches exceptions (I5: degraded-but-loud).
    """
    shell_cfg = self._full_config.get("smc", {}).get("shell", {})
    if not shell_cfg.get("enabled", True):
        return None
    sessions_active = self._engine._config.sessions.enabled
    bias_map = self.get_bias_map(symbol)
    return compose_shell_payload(
        narrative, bias_map, viewer_tf_s, shell_cfg,
        sessions_active=sessions_active, signal=signal,
    )
```

**Reasoning**: `SmcRunner` вже має `get_narrative()`, `get_bias_map()`.
Shell payload = post-processing narrative. Тому `get_shell_payload()` приймає
вже обчислений `NarrativeBlock` (не дублює збір inputs).

#### 5.3.2 ws_server.py frame injection (v3 — updated with exception guard B6)

**Full frame** (`_send_full_frame()`):
Після рядка `frame["narrative"] = narrative_to_wire(_narr)` додаємо:

```python
try:
    _shell = _smc_runner.get_shell_payload(session.symbol, session.tf_s, _narr)
    if _shell is not None:
        frame["shell"] = _shell.to_wire()
except Exception as _shell_exc:
    _log.warning("WS_SHELL_ERR sym=%s err=%s", session.symbol, _shell_exc)
    # frame proceeds without "shell" field — UI stays in legacy mode
```

**Delta frame** (delta_loop):
Після рядка `frame["narrative"] = narrative_to_wire(_narr)` аналогічно:

```python
try:
    _shell = _smc_runner.get_shell_payload(symbol, tf_s, _narr)
    if _shell is not None:
        frame["shell"] = _shell.to_wire()
except Exception as _shell_exc:
    _log.warning("WS_SHELL_ERR sym=%s err=%s", symbol, _shell_exc)
```

**Patching pattern**: ідентичний `get_narrative()` guard в `smc_runner.py:414-416`.

#### 5.3.2a Observability (v3/M2)

- `_log.warning("WS_SHELL_ERR ...")` — обов'язкове при будь-якому exception в shell composition.
- `api/status` endpoint повинен відображати:
  - `shell_enabled: bool` — поточний стан `smc.shell.enabled`
  - `shell_last_error: str | null` — останній error message або `null`

#### 5.3.3 ADR-0039 Integration Point (v3/SE — NEW)

`shell_composer.py` приймає optional `signal: SignalSnapshot | None` параметром.

**Source**: `signal-engine` модуль (ADR-0039 `core/smc/signals.py`). Якщо
signal-engine не ініціалізований або `smc.signals.enabled=false` -> `signal=None`.

**Integration flow** (after ADR-0039 is implemented):

```
SmcRunner.get_shell_payload():
    signal = None
    if smc.signals.enabled:
        signals = self.get_signals(symbol, viewer_tf_s)
        if signals:
            signal = signals[0].to_signal_snapshot()  # primary signal only
    return compose_shell_payload(..., signal=signal)
```

**Before ADR-0039**: `signal=None` always. No code change needed — default parameter.

**Blast radius assessment** (v3 — corrected per M8):

| Хто споживає WS frames | Вплив |
|---|---|
| `ui_v4` (єдиний production WS consumer) | Additive field `"shell"` — ігнорується до P4 (UI wiring) |
| replay client | Additive, backward compatible |

> **Note (v3/M8)**: `aione_top` (monitoring TUI) використовує HTTP `api/status` endpoint,
> не WS frames. Видалено з таблиці WS consumers.

**Зворотна сумісність**: повна. `"shell"` — нове additive поле. Якщо відсутнє,
UI працює в legacy mode (поточний вигляд). Feature detection:
`if (frame.shell) { useShellRenderer() } else { useLegacyHud() }`.

### 5.4. UI Wiring

#### 5.4.0 Layout Model (v3/B7 — NEW)

Shell використовує **push-down model** (НЕ overlay):

- Shell має фіксовану позицію зверху viewport.
- Chart canvas отримує `margin-top = shell total height` (thesis bar + optional strip + optional micro-card).
- Expand/collapse мікро-карти -> shell height grows/shrinks -> chart canvas зміщується відповідно.
- `backdrop-filter` на мікро-карті НЕ використовувати: push-down model означає
  що chart ніколи не знаходиться за мікро-картою. Solid background:
  `rgba(13,15,21,0.98)`.

**Гарантія N1** (chart не перекрито): chart canvas area завжди починається
після shell bottom edge. Жоден shell element не overlay candles.

#### 5.4.0a Delta frame caching (v3/M1)

UI кешує останній отриманий `shell` payload в `last_shell: ShellPayload | null`.
Delta frames (tick relay) що не містять поле `"shell"` — **не скидають** shell.
UI зберігає `last_shell` до наступного frame з полем `"shell"`.

Тільки frame з explicit `"shell": {...}` або `"shell": null` оновлює кеш.

#### 5.4.1 Thesis Bar (primary layer)

`ChartHud.svelte` перестає бути просто `utility HUD row` і стає shell з двох шарів:

1. **Thesis Bar** (height: 40px, завжди видимий):
   - Left: `symbol` + `tf` + market dot + `price`
   - Center: `stage_label` (14px semibold, colored by stage) + `stage_context` (10px quiet)
   - Right: service icons (dimmed) + clock
   - Click: toggles micro-card
   - **(v3/M3) Killzone warning indicator**: якщо `shell_stage in ('prepare', 'ready')
     AND in_killzone=False` -> thesis bar показує subtle warning indicator
     (dim border pulse animation, `rgba(251,191,36,0.15)` overlay, 2s cycle).
     Micro-card завжди показує повний warning text.
   - **(v3/M4) Triggered with signal**: якщо `shell_stage='triggered' AND signal != None` ->
     `stage_context` text включає R:R ratio. Format:
     `"{Direction} active · R:R {rr_ratio}:1 · SL {sl_price}"`.
     Приклад: `"Short active · R:R 3.1:1 · SL 2881.50"`.

2. **Micro-Card** (expandable, default collapsed):
   - 4-field grid: Режим / Чому / Що потрібно / Що скасує
   - Warning row (conditional): killzone/off-session warning
   - Border-left accent: `2px solid var(--sa)` (v3 — explicit spec)
   - Padding: `12px 16px 12px 12px` (asymmetric — 12px left due to 2px border-left)
   - Animate: `max-height 360ms cubic-bezier(.22,1,.36,1)` + parallel `opacity 180ms`
   - Max-height: `280px` (buffer for long why_text, `overflow: hidden`)
   - Background: `rgba(13,15,21,0.98)` — solid, NO `backdrop-filter`
   - **(v3/SE5) Signal rendering**: коли `signal != None`, micro-card додає signal row
     (below what_cancels, before warning).
     - **Конкретні поля і форматування** (Entry/SL/TP/R:R/Confidence) — визначаються
       ADR-0039 (R_SIGNAL_ARCHITECT). ADR-0036 задає тільки rendering contract:
       "signal присутній → рендерити; signal == None → signal row відсутній."
     - Rendering: 10px, `rgba(255,255,255,0.45)` token
     - Frontend не обчислює цифри — тільки рендерить що прийшло з wire.

#### 5.4.2 TF Strip + Chips

**TF Strip** (height: 28px, below thesis bar):

Два variant рендерінгу на основі `shell.tactical_strip.alignment_type`:

1. **`htf_aligned`**: одна `al-pill` з label + arrow + TF list.
   Наприклад: `HTF Aligned ^ D1 . H4 . H1 . M15`.

2. **`mixed`**: ряд `chip` елементів per TF, кожен з:
   - TF label (9px uppercase)
   - Direction arrow (colored bull/bear)
   - Chip state styling:
     - `normal`: default subdued
     - `brk`: yellow background + yellow TF label + brk-dot
     - `cfl`: red background + red TF label + cfl-dot
   - **Chip separator** (v3): pseudo-element `::after`, `0.5px rgba(255,255,255,0.07)`,
     vertical inset `top: 7px; bottom: 7px` (between chips, not after last).

**Strip tag** (right-aligned): `tag_text` colored by `tag_variant`.

**Strip handle** (4px, below strip): click toggles `strip_collapsed` (UI-local state).
Collapsed: `max-height: 0; opacity: 0`. Animate: 280ms cubic-bezier.

**Tooltip per chip** (hover): на основі `chip.direction` + `chip.chip_state`.
Tooltip content — static text per combination (UI-local, не wire format).

#### 5.4.3 Context Reveal -> Micro-Card

Original "Context Reveal" замінено на structured **Micro-Card** (v5 prototype):

| Field | Wire source | Rendering |
|---|---|---|
| Режим | `shell.micro_card.mode_text` | 11px, colored by stage (`--st` var) |
| Чому | `shell.micro_card.why_text` | 11px, neutral |
| Що потрібно | `shell.micro_card.what_needed` | 11px, neutral |
| Що скасує | `shell.micro_card.what_cancels` | 11px, red tint |
| Signal | `shell.signal` (if not null) | 10px, `rgba(255,255,255,0.45)`, grid-column: span 2, conditional (v3) |
| Warning | `shell.micro_card.warning` | 10px, yellow, grid-column: span 2, conditional |

Grid layout: `grid-template-columns: 1fr 1fr`, gap: `7px 20px`.
Field labels: 9px uppercase, `rgba(255,255,255,0.45)` (v3/B8 — corrected from 0.25 for WCAG AA).

#### 5.4.4 Interaction rules

- `Thesis Bar click` -> toggle micro-card (UI-local state)
- `Strip handle click` -> toggle strip collapsed (UI-local state)
- `TF switch` стає signature interaction з коротким compression/expansion response
- `Focus mode` стискає service noise і залишає chart + thesis first-plane
- `Replay` входить у ту ж shell language, а не живе окремою випадковою кнопкою

#### Secondary layer

`App.svelte` top-right bar залишається окремим, але явно стає
`service rail`, а не рівноправним конкурентом thesis.

### 5.5. Visual system

#### 5.5.1 CSS Custom Properties per Stage

v5 prototype визначає 4 CSS custom properties x 5 stage класів:

| Var | Purpose |
|---|---|
| `--sb` | Shell Border — thesis bar bottom border + micro-card border |
| `--sa` | Shell Accent — micro-card left accent border |
| `--st` | Shell Text — stage label color + micro-card mode text |
| `--ss` | Shell Shadow — thesis bar box-shadow (glow effect for active stages) |

Per-stage values (з `research/trader_shell_v5 (1).html`):

```css
.st-wait      { --sb: rgba(255,255,255,0.07); --sa: rgba(255,255,255,0.15); --st: rgba(255,255,255,0.42); --ss: none }
.st-prepare   { --sb: rgba(251,191,36,0.22);  --sa: rgba(251,191,36,0.6);  --st: rgba(251,191,36,0.9);  --ss: none }
.st-ready     { --sb: rgba(52,211,153,0.28);  --sa: rgba(52,211,153,0.75); --st: rgba(52,211,153,0.95); --ss: 0 1px 0 rgba(52,211,153,0.07) }
.st-triggered { --sb: rgba(99,179,237,0.38);  --sa: rgba(99,179,237,0.85); --st: rgba(99,179,237,1);    --ss: 0 1px 0 rgba(99,179,237,0.1), 0 2px 8px rgba(99,179,237,0.04) }
.st-stayout   { --sb: rgba(252,129,129,0.2);  --sa: rgba(252,129,129,0.55);--st: rgba(252,129,129,0.8); --ss: none }
```

**Note**: ці значення — для dark theme. Light theme adaption — окремий P-slice.
Visual reference: `research/trader_shell_v5 (1).html`.

#### 5.5.2 Shell Tokens (replacement for original §5.5)

Original `shellPrimaryBg`, `shellModeWait` etc. tokens **замінено** на `--sb/--sa/--st/--ss`
system. Це компактніше, theme-aware, і вже протестовано в v5 prototype.

Stage class (`st-wait`, `st-prepare`, etc.) встановлюється на `.shell` container
на основі `shell.stage` wire field.

**Additional tokens** (v3):

| Token | Value | Purpose |
|---|---|---|
| `--stctx` | `rgba(255,255,255,0.45)` | Stage context text + field labels + signal data. **(v3/B8: corrected from 0.30 — was WCAG AA FAIL at 2.60:1 on `#12141A`. New value ~4.7:1 = PASS.)** |
| `--mc-bg` | `rgba(13,15,21,0.98)` | Micro-card background (solid, no blur) |

#### 5.5.3 Chip styling (TF strip)

| Chip state | Background | TF label color | Dot |
|---|---|---|---|
| `normal` | transparent | `rgba(255,255,255,0.28)` | none |
| `brk` | `rgba(251,191,36,0.07)` | `rgba(251,191,36,0.8)` | yellow 4px dot |
| `cfl` | `rgba(252,129,129,0.07)` | `rgba(252,129,129,0.75)` | red 4px dot |

### 5.6. Config

Новий **опційний** розділ в `config.json` під `smc.shell`:

```json
{
  "smc": {
    "shell": {
      "enabled": true,
      "stage_labels": {
        "wait": "WAIT",
        "prepare": "PREPARE",
        "ready_long": "LONG · READY",
        "ready_short": "SHORT · READY",
        "triggered_long": "LONG · TRIGGERED",
        "triggered_short": "SHORT · TRIGGERED",
        "stayout": "STAY OUT"
      },
      "micro_card_mode_text": {
        "wait": "Чекаємо",
        "prepare": "Готуємось",
        "ready": "Готовий до входу",
        "triggered": "Позиція активна",
        "stayout": "Не лізти"
      },
      "strip_tfs": [86400, 14400, 3600, 900]
    }
  }
}
```

Default: `"enabled": true`. Якщо `false` -> `frame["shell"]` не додається,
UI працює в legacy mode.

UI-local persistence (не config.json):
- `v4_focus_mode` — localStorage
- `v4_strip_collapsed` — localStorage
- `v4_shell_density` — localStorage (future)

## 6. P-Slices

| Slice | Scope | LOC | Інваріант | Verify | Rollback |
|---|---|---:|---|---|---|
| P1 | Wire format types: `ShellPayload`, `MicroCard`, `TacticalStrip`, `TfChip`, `SignalSnapshot` in `core/smc/types.py` + TypeScript mirrors in `ui_v4/src/types.ts` | ~90 | I0 (pure), S6 (wire SSOT) | `pytest` type import + `npm run check` | revert types in both files |
| P2 | Backend pure logic: `core/smc/shell_composer.py` + `tests/smc/test_shell_composer.py`. Includes: stage mapping with all guards (B1-B4, B9, B10), D1 cfl downgrade, error guard, sessions guard | ~150 | I0 (pure, no I/O), S0 (deterministic) | `pytest tests/smc/test_shell_composer.py` — 9 rows mapping + error guard + sessions guard + D1 cfl downgrade + multi-scenario | revert `shell_composer.py` + test |
| P3 | Runtime wiring: `SmcRunner.get_shell_payload()` + `ws_server.py` frame injection (full + delta) with try/except guard (B6) + `config.json` shell section + observability (M2) | ~70 | I4 (single stream), I5 (degraded-but-loud), S6 (additive wire) | start ws_server, verify `"shell"` field in WS full frame via browser devtools + verify `WS_SHELL_ERR` log on forced error | revert smc_runner + ws_server changes |
| P4 | UI shell renderer: push-down layout (B7) + thesis bar + micro-card + TF strip + strip handle + delta caching (M1) + killzone warning (M3) in `ChartHud.svelte` | ~140 | C-DUMB (no derive logic), chart-first hierarchy, N1 (push-down) | `npm run build` + browser visual QA: all 5 stages + micro-card expand/collapse + chart not overlapped | revert `ChartHud.svelte` |
| P5 | CSS stage system: `--sb/--sa/--st/--ss/--stctx/--mc-bg` vars x 5 stages + chip styling + chip separators + micro-card animation + theme integration + WCAG check (B8) | ~110 | no chart readability loss, WCAG AA pass | browser visual QA on dark/black/light themes + contrast ratio check for `--stctx` | revert CSS changes |
| P6 | Signature interactions: TF switch response, thesis reveal, focus mode, strip collapse persistence + signal row rendering (SE5, M4) | ~80 | perf budget, accessibility | browser QA + interaction smoke + signal row renders when mock signal provided | revert interaction code |

**Total LOC estimate**: ~640 (vs v2 ~600), тепер включає signal types + guards.

**Implementation order**: P1 -> P2 -> P3 -> P4 -> P5 -> P6 (strict sequence:
types -> logic -> runtime -> UI -> visual -> interactions).

## 7. Наслідки

### 7.1. Що змінюється

- shell перестає бути utility-first і стає thesis-first;
- **(v2)** backend тепер обчислює shell composition (новий `shell_composer.py`);
- **(v2)** WS wire format отримує additive поле `"shell"`;
- **(v3)** stage mapping стає точним: кожен trigger value = окремий stage;
- **(v3)** D1 conflict downgrade rule захищає HTF Hierarchy;
- **(v3)** error/sessions guards запобігають хибному `stayout`;
- **(v3)** `ShellPayload.signal` готовий для ADR-0039 integration;
- **(v3)** push-down layout model гарантує N1 (chart not overlapped);
- product hierarchy стає явною;
- service controls залишаються доступними, але перестають бути first-plane;
- mode system стає головним читачем urgency з 5 stages (was 4);
- replay/focus/TF transitions отримують product memory.

### 7.2. Що НЕ змінюється

- **(v2 correction)** backend `NarrativeBlock` contract — shell читає, не пише;
- UDS / derive / SMC engine logic;
- candle render pipeline;
- session / killzone semantics;
- bias and narrative calculation rules;
- existing `narrative` wire field — залишається для NarrativePanel;
- **(v3)** ADR-0039 `SignalSpec` / `synthesize_signals()` — не торкається цим ADR.

### 7.3. Нові rails

- **C-DUMB**: frontend = dumb renderer for shell (hard constraint);
- shell must derive from existing canonical NarrativeBlock only;
- no new parallel data feed for thesis;
- no shell animation that obscures candles;
- no service control promoted to first-plane unless it directly changes trader decision;
- **(v2)** `"shell"` wire field = additive extension, gated by `smc.shell.enabled`;
- **(v3)** `stayout` requires `sessions_active=True` — never on backend error or sessions-off;
- **(v3)** D1 `cfl` chip = mandatory downgrade for `ready`/`triggered` -> `prepare`.

### 7.4. Performance impact

Очікуваний impact low:

- backend: `compose_shell_payload()` = O(n) where n = number of TFs (4-7).
  Negligible compared to `synthesize_narrative()`. Called only when narrative
  is refreshed (complete bars).
- wire: ~300-500 bytes additional per frame (shell payload JSON).
  With signal: +~200 bytes (when ADR-0039 active).
- frontend: zero new backend cost — UI тільки рендерить.
- critical risk only in hover/reveal and TF transition path.

Budget:

- no measurable regression in chart interaction;
- shell transitions must stay under a single normal UI frame budget;
- `compose_shell_payload()` < 1ms (pure dict manipulation).

## 8. Rollback

### Per-slice rollback

1. **P1**: revert type additions in `core/smc/types.py` and `ui_v4/src/types.ts`.
2. **P2**: revert `core/smc/shell_composer.py` and its test file.
3. **P3**: revert `SmcRunner.get_shell_payload()`, ws_server frame injection,
   and `config.json` shell section. WS frames return to pre-amendment state.
4. **P4**: revert `ChartHud.svelte` shell renderer. Falls back to current HUD.
5. **P5**: revert CSS stage classes and variables.
6. **P6**: revert interaction handlers. Thesis bar becomes non-interactive.

### Full rollback

Повний rollback = повернення до поточного `utility HUD + top-right bar`
без зміни runtime semantics. Backend shell computation is fully gated by
`smc.shell.enabled` config flag — set to `false` = instant disable without
code revert.

## 9. Open Questions

1. ~~Чи stage mapping має бути строго `WAIT / PREPARE / READY / TRIGGERED`,
   чи `PREPARE` треба виводити лише коли є сценарій без trigger?~~
   **CLOSED (v2)**: v5 prototype відповідає — 5 stages: `wait`, `prepare`,
   `ready`, `triggered`, `stayout`. Mapping table в §5.2.
2. Чи editorial font pair має бути локально вбудованою,
   чи перший pass лишається на existing stack без font dependency?
3. Який mobile collapse order: `thesis first`, `symbol/tf second`, `service rail last`
   чи інший порядок?
4. ~~Чи `Focus mode` лишається UI-only preference, чи пізніше потребує SSOT config surface?~~
   **CLOSED (v2)**: Focus mode = UI-only preference (`localStorage`).
   Не потребує config surface на першому pass.
5. ~~**(v2)** Чи `stayout` stage потребує окремого `sub_mode` в NarrativeBlock,
   чи достатньо виводити з `mode='wait' + no session + no scenarios`?~~
   **CLOSED (v3)**: `stayout` визначається через `mode='wait' + sessions_active=True + current_session=""
   + no scenarios + no error warnings`. Окремий sub_mode не потрібен. NarrativeBlock не змінюється.
6. ~~**(v2)** Чи потрібен transition period де UI підтримує обидва mode
   (legacy HUD + new shell)?~~
   **CLOSED (v3)**: Feature detection по наявності `frame.shell`. Якщо є — new shell.
   Якщо ні — legacy. Transition period = organic (поки P4 не deployed, UI в legacy mode).

## 10. Architect Self-Check

- [x] >= 2 альтернативи описано чесно (3: extend NarrativeBlock / separate ShellPayload / frontend derive)
- [x] blast radius вказано для кожної альтернативи
- [x] I0-I6 / S0-S6 перевірено: I0 (shell_composer pure), I4 (single stream), I5 (config gate + degraded-but-loud), S0 (deterministic), S6 (wire SSOT)
- [x] P-slices визначено (6 slices, max 150 LOC)
- [x] rollback per-slice + full rollback via config flag
- [x] types/contracts описані before logic (§5.1 -> §5.2 -> §5.3 -> §5.4)
- [x] failure model >= 3 сценаріїв (9 total, 2 нових у v3)
- [x] cross-role synthesis оновлений (§3.5)
- [x] verify criteria задані per slice
- [x] ADR читається без прихованого контексту + v5 prototype як visual reference

**Результат: 10/10, Proposed -> ready for R_REJECTOR review.**
