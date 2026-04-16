---
applyTo: "trader-v3/**"
---

# trader-v3/ — Арчі AI Agent (I7 Autonomy-First)

**SSOT invariant**: I7 — `trader-v3/docs/adr/ADR-024-autonomy-charter.md`.
**Baseline**: trader-v3 ADR-039 (Platform Wake Integration). **Next ADR**: 040.

## ЗОЛОТЕ ПРАВИЛО

> Арчі — автономний AI-агент. Код = **advisory + explain**. Рішення приймає Арчі.

Перед будь-яким `if ...: return` / raise / block — запитай себе:
> "Чи Арчі побачить це обмеження у своєму контексті / логах / промпті?"

Якщо **ні** — це X30 violation (hidden constraint).

## X-stop-list specific to trader-v3

| # | Заборона |
|---|---|
| X29 | Hard block (cooldown, force-downgrade, suppress, timer re-injection) без safety justification |
| X30 | Hidden constraint невидимий для Арчі |
| X31 | Cross-repo contamination: trader-v3 task зачіпає v3 platform (docs/, core/, runtime/, config.json) |
| X32 | Арчі runtime data (`*_directives.json`, `*_conversation.json`, `*_journal.json`) поза `trader-v3/data/` |

## Що ДОЗВОЛЕНО як hard block (тільки 4 випадки)

1. **Kill switch** — owner-operated stop button
2. **Daily $ hard cap** — budget safety (API cost runaway protection)
3. **Owner-only guard** — Telegram user_id check
4. **Anti-hallucination** — wrong-output detection (gibberish, wrong language)

Все інше → **warning + explain**, Арчі сам вирішує.

## Мова промптів / logs / commit messages

| ❌ Заборонено | ✅ Замість |
|---|---|
| "НІКОЛИ не..." | "З досвіду..." |
| "Force downgrade to Sonnet" | "Budget low — Sonnet recommended, you choose" |
| "Cooldown 30 min" | "Last VP was 5 min ago — consider skip" |
| Silent suppress notification | "Event X occurred. Notify now? (y/n)" |

## Data boundary

- Runtime data → `trader-v3/data/` локально або `/opt/smc-trader-v3/data/` на VPS
- **НІКОЛИ** у `v3/data/` або `v3/` root (X32)
- Deploy скрипт skip'ає existing `data/*.json` files (precedent: conversation.json incident 2026-03-30)

## ADR boundary (X31)

| Зміна | Де ADR |
|-------|--------|
| Арчі behavior / state machine / prompt | `trader-v3/docs/adr/ADR-NNN-*.md` (3-digit, next=040) |
| Platform feature для Арчі (consumer side) | `trader-v3/docs/adr/` |
| Platform feature для Арчі (producer side) | `v3/docs/adr/NNNN-*.md` (4-digit, next=0050) |
| Обидва боки | **Два** ADR з cross-ref (приклад: v3/0049 + trader-v3/039) |

## Deploy & post-deploy

- `ssh aione-vps` → `/opt/smc-trader-v3/` → supervisor `smc_trader_v3`
- Deploy: `cp smc_trader_v3.py smc_trader_prompt_v3.md /opt/smc-trader-v3/ && supervisorctl restart smc_trader_v3`
- **ОБОВ'ЯЗКОВО** після deploy: 48h watch window (`.github/prompts/post-deploy-watch.prompt.md`)
- НЕ deploy у п'ятницю увечері (watch падає на weekend)

## Питання-фільтр перед PATCH у trader-v3

1. Чи я додаю hard block? → Одне з 4 дозволених?
2. Чи Арчі побачить це у контексті?
3. Чи я не змішую з v3 platform? (X31)
4. Чи Арчі data залишається у `trader-v3/data/`? (X32)
5. Чи мова = рекомендації, не накази? (I7)
6. Чи є post-deploy watch plan?
