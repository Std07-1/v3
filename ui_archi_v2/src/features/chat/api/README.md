# Chat API Client

Типізований HTTP-шар для розмови з trader-v3 ботом. Оболонка над `src/lib/api.ts` (базовий fetch + Bearer).

## Contract

| Function | Endpoint | Method | Body | Response |
|----------|----------|--------|------|----------|
| `sendMessage(text, csrf)` | `/api/chat` | POST | `{ text, nonce, ts_ms }` | `ChatMessage` |
| `loadHistory(limit?, offset?)` | `/api/chat/history` | GET | — | `ChatHistory` |
| `subscribeStream()` | `/api/chat/stream` | SSE | — | `StreamMessage[]` |

## Security checklist (per call)

- [ ] Bearer token у `Authorization: Bearer <token>` header
- [ ] CSRF double-submit: cookie `csrf_cookie` + header `X-CSRF-Token` з тим самим значенням
- [ ] `Origin` header перевіряється server-side (whitelist)
- [ ] Nonce у body для POST (replay protection T7)
- [ ] `ts_ms` у body (server-side 5-min cutoff)
- [ ] Error responses normalized — ніякого raw text leak у UI (T6)

## Error handling

```ts
try {
    const msg = await chatApi.sendMessage(text, csrf);
    // ...
} catch (e) {
    if (e.code === 'RATE_LIMIT') { /* banner "Зачекай Xс" */ }
    else if (e.code === 'CSRF_FAIL') { /* банер "Оновити сторінку" */ }
    else if (e.code === 'AUTH_FAIL') { /* redirect to login */ }
    else { /* generic error — БЕЗ показу stack trace */ }
}
```

## Non-goals

- Ніяких WS — SSE достатньо (one-way server → client)
- Ніяких client-side retry з експоненційним backoff (довіряємо користувачу вирішити)
- Ніякого offline queueing (drafts зберігаються в store, але не відправляються silently)
