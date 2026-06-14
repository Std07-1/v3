# Chat Components

Dumb Svelte 5 components. Приймають props → emit events. **Не** імпортують stores напряму (виняток: `ContextRail.svelte` читає `railStore` для UI-only state).

| File | Props | Emits / callbacks | Notes |
|------|-------|-------------------|-------|
| `ChatHeader.svelte` | `mood`, `moodPulse`, `ttsAuto`, `ttsSupported` | `ontoggletts` | Mood orb + title + bias pills (планується) |
| `ContextRail.svelte` | `directives`, `agentState`, `handoff`, `collapsed` | `ontoggle`, `ondismisshandoff` | Wrapper + collapse button (S6 mobile repair) |
| `ContextRail/HandoffStrip.svelte` | `handoff` | `ondismiss` | Whitelist source (T5 mitigation) |
| `ContextRail/ModeHearth.svelte` | `mood`, `focus_symbol`, `active_scenario`, `inner_thought` | — | |
| `ContextRail/PulseBoard.svelte` | `bias_map`, `market_mental_model`, `metacognition` | — | |
| `ContextRail/PinnedThought.svelte` | `text`, `visible` | `onfade` | Auto-fade 15s |
| `MessageList.svelte` | `messages`, `loading`, `error` | — | Scroll-to-bottom behavior, virtualization-ready |
| `MessageBubble.svelte` | `message: ChatMessage` | `onspeak` | markdown → sanitize → innerHTML (T1 mitigation) |
| `InputBar.svelte` | `value`, `sending`, `maxLength`, `focused` | `onchange`, `onsubmit`, `onfocus`, `onblur` | Auto-grow + rate-limit hook |
| `QuickActions.svelte` | — | `onpick(prompt: string)` | /ціна /сценарій /статус /стоп |
| `EmojiPicker.svelte` | `open`, `categories` | `onpick(emoji: string)`, `onclose` | Telegram-style 4 cat |
| `VoiceButton.svelte` | `listening`, `supported`, `error` | `onstart`, `onstop` | WebSpeech recognition |

## A11y checklist (must-have для кожного)

- [ ] `aria-label` на інтерактивних елементах
- [ ] `role="button"` на клікабельних non-button
- [ ] Keyboard navigation (Tab / Enter / Esc)
- [ ] Focus ring виден (не `outline: none` без заміни)
- [ ] Colour contrast ≥ 4.5:1 (WCAG AA)

## LOC budget

Жоден файл > 150 LOC (template + script + style). Якщо перевищує → винести sub-component.
