# Chat Feature Module

> **Purpose**: Реактивний UI-шар для розмови Стаса з Arхі. Тонкий рендер поверх HTTP/SSE API trader-v3 бота. Приймає handoffs з інших views (Feed, Thinking, Relationship, Mind, Logs) і продовжує діалог без втрати контексту.

**Parent ADR**: [ADR-0052 — Chat Modularization](../../../../docs/adr/0052-chat-modularization.md)
**Threat model**: [THREAT_MODEL_CHAT.md](../../../../docs/security/THREAT_MODEL_CHAT.md)
**Owner**: Arхі UI

---

## 1. Folder layout

```
features/chat/
  Chat.svelte                     # Композиція (≤200 LOC)
  components/                     # Dumb views — без fetch/storage logic
    ChatHeader.svelte
    ContextRail.svelte            # wrapper + collapse
    ContextRail/
      HandoffStrip.svelte
      ModeHearth.svelte
      PulseBoard.svelte
      PinnedThought.svelte
    MessageList.svelte
    MessageBubble.svelte
    InputBar.svelte
    QuickActions.svelte
    EmojiPicker.svelte
    VoiceButton.svelte
  stores/                         # Svelte 5 runes ($state, $derived)
    chatStore.svelte.ts
    railStore.svelte.ts
    ttsStore.svelte.ts
    voiceStore.svelte.ts
  api/
    chatApi.ts                    # Типізовані HTTP клієнти
  lib/
    rateLimit.ts                  # Client-side throttle
  README.md                       # Цей файл
```

**Переіспользується з `src/lib/`**:
- `sanitize.ts` — XSS-strip для markdown
- `types.ts` — `ChatMessage`, `ChatHandoff`, `Directives`, `AgentState`
- `api.ts` — базовий fetch wrapper (BASE_URL, error handling)

---

## 2. Data flow

```
   User types / клікає
          │
          ▼
   ┌──────────────┐        send()        ┌──────────────┐
   │  InputBar    │ ─────────────────▶   │  chatStore   │
   └──────────────┘                      │  (state)     │
          ▲                              └──────┬───────┘
          │ draft sync                          │
          │                                     │ chatApi.sendMessage()
   ┌──────────────┐                             ▼
   │  EmojiPicker │ ◀─┐              ┌────────────────────┐
   │  VoiceButton │   │ inject       │  POST /chat        │
   │  QuickAction │ ──┘              │  (Bearer + CSRF)   │
   └──────────────┘                  └──────────┬─────────┘
                                                │
                                    SSE / poll  │
                                                ▼
   ┌─────────────────┐            ┌───────────────────────┐
   │  MessageList    │ ◀──────────│  chatStore.messages   │
   │  MessageBubble  │  $derived  │  (runes reactivity)   │
   └─────────────────┘            └───────────────────────┘

   ┌─────────────────┐            ┌───────────────────────┐
   │  ContextRail    │ ◀──────────│  directives (prop)    │
   │  - Handoffs     │            │  + agentState (prop)  │
   │  - Mode         │            │  + handoff (prop)     │
   │  - Pulse        │            │                       │
   │  - Pinned       │            │  railStore.collapsed  │
   └─────────────────┘            └───────────────────────┘
```

---

## 3. Responsibilities

### Chat.svelte (композиція)
- Приймає props: `draft`, `handoff`, `directives`, `agentState`, callbacks
- Рендерить `<ChatHeader>`, `<MessageList>`, `<ContextRail>`, `<InputBar>`
- Жодної fetch/storage логіки — делегує у stores

### stores/*.svelte.ts (state)
- `chatStore` — messages, inputText, sending, lastSync, error. Експортує `send()`, `loadHistory()`, `clearError()`.
- `railStore` — collapsed (localStorage persist), toggle().
- `ttsStore` — auto, supported, speak(text), toggleAuto().
- `voiceStore` — listening, error, supported, start(), stop().

### api/chatApi.ts (network)
- `sendMessage(text: string, csrfToken: string): Promise<ChatMessage>`
- `loadHistory(limit: number): Promise<ChatHistory>`
- Усі виклики проходять через `lib/api.ts` (Bearer, error normalize)

### components/* (views)
- **Dumb** — приймають props, emit callbacks, **не** імпортують stores напряму (крім composition layer у Chat.svelte).
- Це дозволяє тестувати у Storybook / isolation.
- Винятки: `ContextRail.svelte` читає `railStore` (UI-only state).

---

## 4. Security touch points

| Де | Що | Контроль |
|----|----|---------| 
| `InputBar` on submit | Клієнтський rate-limit (1 msg/sec) | `lib/rateLimit.ts` |
| `MessageBubble` render | Санітайз markdown Arхі | `lib/sanitize.ts` (existing) |
| `chatApi.sendMessage` | CSRF double-submit + Bearer | `api/chatApi.ts` wraps |
| `ContextRail / HandoffStrip` | Handoff source whitelist + length cap | Ignore unknown `source` |
| Усі XHR | Origin check + short-lived token | `lib/api.ts` base |

Повний threat-model: [`docs/security/THREAT_MODEL_CHAT.md`](../../../../docs/security/THREAT_MODEL_CHAT.md).

---

## 5. Invariants (MUST preserve)

- **I7 — Degraded-but-loud**: rate-limit hit, CSRF fail, network error → явний UI-сигнал (toast / banner). Жодних silent drops.
- **I1 — Read-only**: Chat UI не пише у UDS / directives напряму — тільки через бот API.
- **Один агент, одна особистість**: Chat не реалізує client-side "Arхі-like" відповіді. Усі повідомлення від агента приходять з бекенда.
- **Svelte 5 runes only**: `$state`, `$derived`, `$derived.by`, `$effect`. Ніяких Svelte 4 stores (`writable`) у нових файлах.

---

## 6. How to extend

**Додати новий quick action**:
1. Додати рядок у `QuickActions.svelte` (значок + prompt).
2. Якщо потрібен новий HTTP endpoint — додати метод у `chatApi.ts`.
3. README.md секції 3 не змінювати без оновлення ADR-0052.

**Додати новий тип handoff джерела** (наприклад, "trade"):
1. Розширити `ChatHandoff.source` union у `lib/types.ts`.
2. Додати whitelist entry у `ContextRail/HandoffStrip.svelte`.
3. Оновити THREAT_MODEL_CHAT T5 мітигацію.
4. Оновити ADR-0052 §5 open questions.

**Додати новий store**:
1. Створити `stores/xxxStore.svelte.ts` із Svelte 5 runes.
2. Додати JSDoc блок зверху: призначення, консьюмери, invariants.
3. Імпортувати у Chat.svelte (не у dumb components).

---

## 7. Testing strategy

- **Unit** — stores з mock API. `@testing-library/svelte` + Vitest.
- **Component** — snapshot бабблів з різним контентом (plain, markdown, code, emoji).
- **E2E** — Playwright для send → render → sanitize flow (вже використовується у ui_archi).
- **Security** — окремі тести у `runtime/api/tests/` (S7/S8).

---

## 8. Slice progress

| Slice | Status | Notes |
|-------|--------|-------|
| S1 — ADR + структура | ✅ Done (цей README + skeletons) | 2026-04-19 |
| S2 — chatStore + api + sanitize | ⏳ Pending | |
| S3 — MessageList + Bubble | ⏳ Pending | |
| S4 — InputBar + VoiceButton + Emoji + QuickActions | ⏳ Pending | |
| S5 — ChatHeader + rail components + railStore | ⏳ Pending | |
| S6 — Mobile rail collapse | ⏳ Pending | |
| S7 — Backend auth + rate_limit + audit | ⏳ Pending | |
| S8 — Backend CSRF + sanitizer + full threat model | ⏳ Pending | |

Updated: 2026-04-19.
