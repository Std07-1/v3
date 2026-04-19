# ADR-0052 — Chat Modularization + Security Layer

**Status**: Proposed
**Date**: 2026-04-19
**Initiative**: `ui_archi_chat_v2`
**Supersedes**: — (перша декомпозиція Chat)
**Related**: ADR-044 (Workspace), ADR-045 (TaskQueue), ADR-034 (Wake Conditions)

---

## 1. Контекст

`ui_archi/src/views/Chat.svelte` виріс до **3006 рядків** (1192 CSS). Один файл містить:

- стан месенджера (messages / sending / draft);
- контекстну панель (handoffs, mode, bias, pulse, pinned thought);
- рендер бабблів + markdown + sanitize;
- поле вводу + emoji-picker + voice + TTS + quick actions;
- всі стилі + адаптивні брейкпойнти;
- весь API-шар.

Наслідки:
- **Неможливо робити surgical fix** — найпростіша зміна (mobile rail collapse) тягне ризик регресії у непов'язаних частинах.
- **Контекст моделі перенасичується** при кожному питанні про Chat → повільні й дорогі відповіді.
- **Нульова безпекова ізоляція** — sanitize, rate-limit, audit розсипані або відсутні.
- **Немає місця для росту** — майбутні фічі (threads, attachments, reactions) ламають файл ще більше.

I7 (Autonomy-First) + I1 (UDS-as-waist) говорять: один агент, одна особистість, тонкий виконавець. Архітектурно це тримається. Але **UI-шар Арчі** вже не тонкий — і це блокує подальшу розробку M4/M5 в ROADMAP.

---

## 2. Рішення

Розбити Chat на **feature-module** `ui_archi/src/features/chat/` з чіткими шарами:

```
features/chat/
  Chat.svelte                     ~150 LOC — тільки композиція дочірніх компонентів
  components/
    ChatHeader.svelte             mood orb, title, bias pills, TTS toggle
    ContextRail.svelte            wrapper + collapse-toggle (mobile-first)
    ContextRail/
      HandoffStrip.svelte         badges від Feed/Thinking/Relationship/Mind/Logs
      ModeHearth.svelte           mood + scenario + inner_thought
      PulseBoard.svelte           bias pills + market_mental_model + metacog
      PinnedThought.svelte        auto-fade overlay
    MessageList.svelte            скрол + virtualization-ready
    MessageBubble.svelte          user/archi варіанти + markdown
    InputBar.svelte               textarea + send + quick actions wrap
    QuickActions.svelte           /ціна /сценарій /статус /стоп кнопки
    EmojiPicker.svelte            Telegram-style 4-категорії
    VoiceButton.svelte            WebSpeech recognition
  stores/
    chatStore.svelte.ts           messages, inputText, sending, draft, sync
    railStore.svelte.ts           collapsed state + localStorage persist
    ttsStore.svelte.ts            ttsAuto, ttsSupported, speak()
    voiceStore.svelte.ts          listening, voiceError, recognition
  api/
    chatApi.ts                    типізовані POST /chat, GET /chat/history
  lib/
    rateLimit.ts                  client-side throttle (anti-spam)
    (sanitize.ts вже існує у src/lib/ — reuse)
```

Паралельно — **backend security layer** у `runtime/api/`:

```
runtime/api/
  auth.py              Bearer-token validation + HMAC підпис відповідей
  rate_limit.py        Redis-backed (10 msg/min per token)
  csrf.py              Double-submit cookie для POST /chat
  sanitizer.py         server-side input cap + HTML strip
  audit.py             immutable log → Redis stream (replay protection)
```

---

## 3. Інваріанти (зберігаються)

- **I1 (UDS-as-waist)** — Chat працює поверх існуючого HTTP/WS API, не торкається UDS.
- **I7 (Autonomy-First)** — sanitize/rate-limit **попереджають**, не мовчки дропають повідомлення. Кожен блок = explicit signal у UI (banner / toast / console log). Жодних silent drops.
- **Один агент, одна особистість** — backend routing не змінюється. Arхі читає повідомлення через той самий контракт.
- **Degraded-but-loud** — rate-limit-hit → toast + audit entry. CSRF-fail → 403 + банер. XSS-attempt → strip + log.

---

## 4. Загрози та мітигації

Повний STRIDE-аналіз у [`docs/security/THREAT_MODEL_CHAT.md`](../security/THREAT_MODEL_CHAT.md). Короткий зріз:

| # | Загроза | Вектор | Мітигація |
|---|---------|--------|-----------|
| T1 | XSS у повідомленнях | Арчі повертає markdown → `innerHTML` | Client `sanitize.ts` + server `sanitizer.py` + CSP header |
| T2 | Token theft | Bearer у localStorage | Short-lived (15 хв) + refresh + `httpOnly` для критичних |
| T3 | Rate abuse | Бот шле 1000 msg/s | Client throttle + Redis-backed server limit (10/min) |
| T4 | CSRF | Зовнішній сайт постить від імені юзера | Double-submit cookie + SameSite=Strict |
| T5 | Prompt injection через handoff | Handoff з Feed містить зловмисний `prompt` | Whitelist джерел + довжина cap + санітайз перед вкиданням у промпт |
| T6 | Secret leak у логах | Tokens/PII у error messages | Redactor middleware (regex strip) перед запис у логи |
| T7 | Replay attacks | Перехоплений POST /chat | Nonce + timestamp у audit stream + cutoff |

---

## 5. Послідовність (8 slices ≤150 LOC кожен)

| # | Slice | Deliverable | Runtime risk |
|---|-------|-------------|--------------|
| S1 | ADR-0052 + structure | Ця ADR + порожні папки + README скелети + THREAT_MODEL skeleton | Нуль |
| S2 | `chatStore` + `chatApi` + sanitize wire-up | Chat.svelte ще монолітний, але логіка винесена у store | Низький |
| S3 | `MessageList` + `MessageBubble` | Рендер месседжів винесено | Середній (regression on markdown) |
| S4 | `InputBar` + `QuickActions` + `VoiceButton` + `EmojiPicker` | Ввід винесено | Середній (focus/keyboard behavior) |
| S5 | `ChatHeader` + 4 rail-компоненти + `railStore` | ContextRail модульний | Середній (responsive) |
| S6 | Mobile rail collapse | ~20 LOC у `ContextRail.svelte` замість хірургії у монолиті | Низький |
| S7 | Backend `auth.py` + `rate_limit.py` + `audit.py` + tests | Нові Python модулі, feature-flag off | Низький (gated) |
| S8 | Backend `csrf.py` + `sanitizer.py` + повний threat model | Security hardening complete | Низький (gated) |

Після кожного slice — deploy, перевірка, commit. ADR-0052 оновлюється при зміні обʼєму/порядку.

---

## 6. Alternatives considered

1. **Не чіпати монолит, тільки P1 mobile fix** — відхилено юзером: "передбач безпеку від атак", "проект дуже розросттиметься".
2. **Tailwind rewrite + shadcn-svelte** — зайвий обʼєм, ламає існуючий theme.css. Відхилено.
3. **Micro-frontends (окремий bundle для chat)** — overkill для одного view. Відхилено.
4. **Перенести все у backend-rendered HTML** — втратимо Svelte-реактивність і SSE deltas. Відхилено.

---

## 7. Rollback

Кожен slice S2–S8 ізольований. Rollback = `git revert <sha>` + деплой. Chat.svelte backup зберігається як `Chat.svelte.bak` у першому комміті S2 (видаляється у S5 коли композиція готова).

S1 = pure docs + пусті файли → rollback тривіальний (`git revert` без runtime impact).

---

## 8. Acceptance criteria

- [ ] Chat.svelte ≤ 200 LOC (тільки композиція)
- [ ] Жоден components/*.svelte > 150 LOC
- [ ] README.md у `features/chat/` описує data flow
- [ ] THREAT_MODEL_CHAT.md має всі 7 векторів з мітигаціями
- [ ] Backend `auth.py` + `rate_limit.py` покриті тестами (≥80% coverage для security-critical paths)
- [ ] Mobile rail collapse працює на viewport ≤768px (Chrome DevTools + реальний пристрій)
- [ ] CSP header блокує inline `<script>` (F12 console verify)
- [ ] Rate-limit-hit показує toast "Зачекай 30 секунд" без мовчазного дропу (I7 compliance)

---

## 9. Open questions

- Чи переносити інші views (Feed, Thinking, Workspace) у `features/*` пізніше? → Так, окремою ADR після завершення Chat.
- Де зберігати Bearer token: localStorage чи `httpOnly` cookie? → S7 вирішить після pentesting S8.
- Чи робити voice/TTS опціональним bundle (code-split)? → S4 може винести у lazy import якщо bundle > 250KB.
