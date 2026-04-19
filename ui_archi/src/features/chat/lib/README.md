# Chat lib — Client-side utilities

Pure helpers для chat feature. **Без** state, **без** DOM side effects (крім sanitize що приймає string → string).

| File | Purpose | Tests |
|------|---------|-------|
| `rateLimit.ts` | Client-side throttle (1 msg/sec, 10 msg/min) — захист від випадкового подвійного кліку + першого захисного ланцюга T3 | Pending S7 |

## Переіспользується з `src/lib/`

- `sanitize.ts` — DOMPurify-like strip `<script>/<iframe>/on*=` для markdown Arхі (T1 mitigation, client-side частина)
- `api.ts` — базовий fetch wrapper
- `types.ts` — усі TS інтерфейси

## Rules

- **Pure functions** — один input, один output, нема side effects (крім sanitize що працює з string, не DOM)
- **Маленькі файли** — один concern на файл
- **Типізація** — `noImplicitAny`, `strict: true`
- **Тести обов'язкові** для security-critical code (sanitize, rateLimit)
