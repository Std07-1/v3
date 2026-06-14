# Chat Stores

Svelte 5 runes state (`$state`, `$derived`, `$effect`). Кожен store — окремий `.svelte.ts` файл з JSDoc заголовком.

| Store | Responsibility | Persistence | Consumers |
|-------|----------------|-------------|-----------|
| `chatStore.svelte.ts` | messages, inputText, sending, lastSync, error, SSE subscription | Memory + server | Chat.svelte, InputBar (через props) |
| `railStore.svelte.ts` | `collapsed: boolean`, toggle(), localStorage persist | localStorage `archi:rail:collapsed` | Chat.svelte, ContextRail |
| `ttsStore.svelte.ts` | `auto`, `supported`, `lastSpokenId`, speak(text), toggleAuto() | localStorage `archi:tts:auto` | Chat.svelte, ChatHeader, MessageBubble |
| `voiceStore.svelte.ts` | `listening`, `supported`, `error`, `recognition`, start(), stop() | Memory | Chat.svelte, VoiceButton, InputBar |

## Template (JSDoc header — обов'язково)

```ts
/**
 * chatStore — Стан активного діалогу з Arхі.
 *
 * Invariants:
 *   - messages[] відсортовано за ts_ms asc
 *   - sending=true блокує повторний submit у InputBar
 *   - error=null означає clean state (success or idle)
 *
 * Degraded-but-loud (I7):
 *   - Mережевий fail → error + зберігання inputText (user не втрачає draft)
 *   - Rate-limit → error "Зачекай 30с", НЕ мовчки дроп
 *
 * Consumers: Chat.svelte (composition), InputBar (via prop binding)
 */
import type { ChatMessage } from '../../../lib/types';
import * as chatApi from '../api/chatApi';

class ChatStore {
    messages = $state<ChatMessage[]>([]);
    inputText = $state('');
    sending = $state(false);
    error = $state<string | null>(null);
    // ...
}

export const chatStore = new ChatStore();
```

## Rules

- **Runes only** — ніяких `writable()` з Svelte 4.
- **Один source of truth** — якщо UI state дублюється між store і prop, prop виграє (композиція > state).
- **Persistence explicit** — завжди documented у таблиці вище. Ніяких прихованих localStorage writes.
- **Error = UI signal** — кожен error шлях має відповідне відображення (toast / banner / inline).
