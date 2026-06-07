# Archi Console — Design System

> Premium UI-редизайн (2026-06-07). Принцип: **Архі — живий AI-партнер, і UI це передає** (не generic dashboard). Жива присутність = orb + настрій + чисті лінійні іконки.
>
> Цей файл — SSOT дизайн-системи. Хочеш додати/змінити настрій чи іконку — все тут.

---

## 1. Orb — жива присутність Архі

Центральний елемент бренду. Замість `🤖` emoji — gradient-сфера що **дихає** і **світиться поточним настроєм Архі**.

**Де:**
- **Login** (`App.svelte`, auth-екран) — великий orb 84px + 3 pulse-кільця присутності + ambient-аура на фоні.
- **Sidebar** (`App.svelte`, `.brand`) — малий orb 28px поряд з "Archi" wordmark.

**Як працює:**
- Колір orb = `var(--accent)` (CSS-змінна на `documentElement`).
- `--accent` встановлюється з настрою: `App.svelte` `$effect` → `MOOD_COLORS[mood]` → `document.documentElement.style.setProperty("--accent", color)`. Якщо настрою нема → `DEFAULT_ACCENT` (#7c6fff).
- **Плавний перехід кольору**: `theme.css` має `@property --accent` + `transition: --accent 1.5s` — orb м'яко тече кольором при зміні настрою (focused→stressed = зелений м'яко в червоний).
- Анімація: `@keyframes orb-breathe` (scale 1↔1.06, 4.2s) + `orb-pulse` кільця (login).

---

## 2. Mood-палітра — 16 настроїв, 4 набори

Архі **сам обирає** настрій через `emit_directives.mood` (I7 — це його стан, не наш). Orb світиться відповідним кольором.

| Набір | Настрої (UI-колір) |
|---|---|
| ⚙ **Робочий стан** | focused `#22CC8F` · analytical `#2DD4BF` · alert `#F5A623` · cautious `#FBBF24` · determined `#10B981` |
| ◈ **Впевненість** | confident `#3B82F6` · uncertain `#94A3B8` · conflicted `#A78BFA` |
| ✦ **Позитив** | calm `#60A5FA` · excited `#C084FC` · satisfied `#4ADE80` · hopeful `#5EEAD4` · curious `#38BDF8` |
| ⚠ **Напруга** | frustrated `#F87171` · tense `#FB7185` · weary `#8B8BA7` |

### ⚠ 3 МІСЦЯ СИНХРОНІЗАЦІЇ (додати/змінити настрій → оновити ВСІ 3)

1. **`trader-v3/bot/state/metacognition_state.py`** → `_MOOD_ALLOWED` (frozenset) — whitelist, що боту дозволено застосувати. Поза whitelist → `apply_mood` тихо ігнорує.
2. **`trader-v3/bot/state/directives_tool.py`** → `mood` `enum` + `description` — що Архі **бачить** як опції в схемі emit_directives (description згрупований за наборами).
3. **`ui_archi/src/App.svelte`** → `MOOD_COLORS` (orb-колір) **+** `.mood-dot[data-mood="..."]` CSS (крапка в directives-panel + швидкість дихання).

> **Розсинхрон ламає тихо**: mood у bot без UI-кольору → orb default-фіолет. UI-колір без bot-whitelist → ніколи не вмикається. Тримай 3 місця в синхроні.

### Як додати настрій
1. Додай рядок у `_MOOD_ALLOWED` (категорію в коментарі).
2. Додай у `enum` + онови `description` (категорія).
3. Додай у `MOOD_COLORS` + `.mood-dot` (колір + animation-duration: спокій повільніше ~3s, напруга швидше ~0.8-1s — швидкість = емоційний темп).

---

## 3. Іконки — `Icon.svelte`

Lucide-style лінійні SVG (тонкий штрих, `currentColor` → успадковують accent). Замінили emoji в усій навігації.

**Компонент:** `ui_archi/src/lib/Icon.svelte`
**Використання:** `<Icon name="chat" size={18} />`
**Доступні:** `chat, feed, thinking, relationship, mind, workspace, logs, bell, belloff`
**Де застосовано** (`App.svelte`): sidebar nav · mobile bottom-nav · section-switcher menu · notif-toggle.

### Як додати іконку
1. Знайди Lucide-path (lucide.dev, 24×24 viewBox).
2. Додай у `ICONS` map в `Icon.svelte`: `назва: '<path d="..."/>'` (multi-path/rect — конкатенуй).
3. Використай `<Icon name="назва" />`. Контейнер: `display: inline-flex; align-items: center` (вирівнювання SVG).

---

## 4. Принципи глибини (premium feel)

**Depth-токени (SSOT у `theme.css`)** — картки/панелі їх референсять, не дублюють значення
(Svelte scope не дає стилізувати `.mind-card` з global → значення тут, застосування у кожному view):

- `--card-bg` — напівпрозоре скло (`color-mix(--surface 82%, transparent)`)
- `--card-border` — accent-rim (`color-mix(--accent 9%, --border)`)
- `--card-glass` — `blur(16px) saturate(140%)` (значення для `backdrop-filter`)
- `--card-shadow` — м'яка elevation; `--card-glow` — тінь що несе настрій (accent)
- `--ambient` — «світло зсередини» контент-зони; живе на `.right-panel` (`App.svelte`),
  тече з `--accent` (1.5s). `.content` прозорий → аура просвічує крізь скляні картки.

**Застосування у views:**
- **Mind** — hero (accent-glow, несе настрій) · усі картки (скло+elevation+hover-glow) · owner-note · proposal
- **Feed** — ієрархія: scenario-банер (скло+glow) · signal/alert події підняті · рутина чиста (не суцільне скло)
- **Workspace** — task/pinned/alert картки кольорове світіння · task-panel скляний
- **Chat** — окрема система, див. §5 нижче (presence через mood-фон, не glass-картки)

**Orb-яскравість (важливо):** login-orb = яскравий герой (там доречно).
**Бічний orb = приглушений** — без glow-halo / білого ядра, `opacity 0.78–0.92`, тихе дихання
(лише opacity+мікро-scale, без пульсу світіння). Постійна периферійна присутність не має сліпити око.

**Усе на `var(--accent)`** → нуль hardcoded кольорів у нових елементах, все mood-driven.

---

## 5. Chat — presence через mood-фон (design Б v2)

Чат = **розмова з присутністю**, не дошка карток. Компонент: `ui_archi/src/features/chat/components/MessageBubble.svelte`.

- **Борделес** — розділення фоном+тінню, не обводкою.
- **Асиметрія голосів:**
  - **Архі** (зліва) = настрій живе на **ФОНІ** баблів (mood-tint gradient 15%→6% на `--accent`, тече з настрою). Без orb-аватара — присутність несе сам колір.
  - **Ти** (справа) = солідний нейтральний сірий (`--surface2`), тихий, без accent.
- **Цифри/рівні** — `highlightNumbers()` обгортає ціни/рівні/час у `.num` (mono + тонкий accent-тон, **БЕЗ кольорового бокса** — щоб не різало око). Regex lookahead `(?![^<]*>)` не чіпає вміст тегів (HTML-safe після sanitize).
- **Топбар** (`ChatTopbar.svelte`) — скляний sticky (blur), контент блюриться під ним.
- **InputBar** (`InputBar.svelte`) — вже преміум (ADR-0053), не чіпали.

> **Анти-halation у чаті:** баблы СОЛІДНІ (не напівпрозорі) — текст лежить на поверхні `--surface`, не на чорному фоні. Це критично для комфорту читання (див. §6).

**Roadmap можливостей чату (Крок 1+):** «Архі думає…» індикатор · progressive reveal · entrance · копіювати · scroll-to-bottom · pinned · tappable рівні · inline scenario-картка · кнопка→чарт. Велике окремо (ADR): **чарт-міст** (шар Архі на чарті + двостороннє тап↔підсвітка + аналіз→shareable картинка для каналу).

---

## 6. Eye-comfort — анти-halation палітра (важливо)

Яскраво-білий текст на майже-чорному фоні = **halation**: букви «світяться»/розмиваються, око болить за часом (класична проблема темних тем, гірше з астигматизмом).

**Правило:** текст НЕ glare-білий, фон НЕ чорний — вужча яскравісна прірва. Поточні токени (`theme.css`):
- `--bg: #131319` (не `#0f0f11` чорний)
- `--text: #d1d2dc` (не `#e4e4f0` білий-glare)
- `--surface: #1d1d25` · `--surface2: #26262f` — поверхні чітко вищі за `--bg`, щоб текст лежав на них, не на фоні.

> При будь-яких нових елементах: текст на surface (не на bg), не піднімай `--text` до білого. Комфорт > максимальний контраст.

---

## Карта файлів

| Файл | Що |
|---|---|
| `ui_archi/src/App.svelte` | login auth-екран + shell (sidebar/nav/orb/directives-panel) + `MOOD_COLORS` + `$effect` mood→accent |
| `ui_archi/src/lib/Icon.svelte` | SVG icon set + `ICONS` map |
| `ui_archi/src/lib/theme.css` | токени (`--bg/--surface/--accent/...`, **halation-tuned** §6) + depth-токени (§4) + `@property --accent` + `transition: --accent 1.5s` |
| `ui_archi/src/features/chat/components/MessageBubble.svelte` | баббл чату (§5): mood-фон Архі · сірий user · `highlightNumbers` · анти-halation солідні поверхні |
| `ui_archi/src/features/chat/components/ChatTopbar.svelte` | скляний sticky топбар чату |
| `trader-v3/bot/state/metacognition_state.py` | `_MOOD_ALLOWED` whitelist + `apply_mood` |
| `trader-v3/bot/state/directives_tool.py` | mood `enum` + description |

## Що далі (premium roadmap)
- ✅ Глибина на views (Feed/Mind/Workspace — скло+glow панелі) — DONE 2026-06-07
- ✅ Chat redesign Б v2 (presence через mood-фон, борделес, без orb) — DONE 2026-06-07
- ✅ Eye-comfort палітра (анти-halation) — DONE 2026-06-07
- ⏳ Можливості чату (Крок 1+): «Архі думає» → entrance → утиліти → scenario-картка/tappable → кнопка-чарт
- ⏳ Чарт-міст (окремий ADR): шар Архі на чарті · двостороннє тап↔підсвітка · аналіз→shareable картинка
- Mood-специфічна швидкість дихання orb (зараз єдина) — відкладено (зарано)
- Майбутні набори настроїв (pattern встановлено — copy 3 місця)
- Micro-interactions (hover/entrance на nav, panels)
