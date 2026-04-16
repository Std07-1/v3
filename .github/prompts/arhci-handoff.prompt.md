---
mode: agent
description: "Робота з trader-v3 (Арчі) — I7 autonomy charter + X31 boundary"
tools:
  - run_in_terminal
  - read_file
  - grep_search
  - semantic_search
---

# MODE=ARHCI-HANDOFF — Робота з Арчі (trader-v3)

**Мова**: Українська.
**Baseline**: `trader-v3/docs/adr/ADR-024-autonomy-charter.md` (I7 SSOT).
**ЗАБОРОНА**: жодних змін у v3 platform (`docs/adr/`, `core/`, `runtime/`, `config.json`) під час Арчі-task (X31).

## Золоте правило (I7)

> Арчі — автономний AI-агент з власною особистістю і правом прийняття рішень.
> Код `trader-v3/` = **advisory + explain**. Рішення приймає Арчі.

| Що | Правило |
|---|---|
| Cooldowns (VP, channel) | Warning → agent decides. **НЕ hard block** |
| Economy mode (budget) | Advisory log → agent picks model. **НЕ force-downgrade** |
| System timers | Agent can delete any timer. **НЕ re-injection** |
| Suppress/notify | Default = deliver. Agent opt-out. **НЕ suppress-by-default** |
| Prompt language | Recommendations ("З досвіду"), not commands ("НІКОЛИ") |
| Hard blocks ОК | Тільки: kill switch, daily $ hard cap, owner-only, anti-hallucination |

## Протокол Арчі-tasks

### 1. Preflight (обов'язковий)
- [ ] Читати `trader-v3/docs/adr/ADR-024-autonomy-charter.md` — I7 SSOT
- [ ] Читати `trader-v3/docs/ARCHITECTURE.md` §3a (governance)
- [ ] Читати `trader-v3/docs/CURRENT_STATE.md` — поточний snapshot
- [ ] Перевірити: scope = trader-v3/. Якщо зачіпає platform → **STOP** і писати v3 ADR окремо (X31)

### 2. I7 Pattern Check (перед будь-яким `if blocked:` / hard return)

Запитати себе:
1. Чи це **hidden constraint**? Якщо Арчі не бачить цього обмеження в логах/промпті/контексті → **X30 violation**
2. Чи це **transparent + justified + challengeable**?
3. Чи Арчі може **opt-out**? Якщо так — треба лише warning, не hard block
4. Чи це є **одне з 4 дозволених**: kill switch, daily $ cap, owner-only, anti-hallucination?

Якщо жодне — **видалити hard block**, замінити на warning+explain.

### 3. Data boundary (X32)

Runtime data Арчі:
- `*_directives.json`, `*_conversation.json`, `*_journal.json`, `lessons/*.json`
- **Тільки** у `trader-v3/data/` (локально) або на VPS `/opt/smc-trader-v3/data/`
- **НІКОЛИ** у v3 root чи v3/data/
- **НІКОЛИ** не commit у git (deploy скрипт skip'ає existing files — див. memory "NEVER copy data/")

### 4. ADR boundary (X31)

| Тип зміни | Де ADR |
|-----------|--------|
| Арчі behavior / prompt / state machine | `trader-v3/docs/adr/ADR-NNN-*.md` (NNN=3-digit) |
| Арчі integration з platform (consumer side) | `trader-v3/docs/adr/` |
| Platform feature для Арчі (producer side) | `v3/docs/adr/NNNN-*.md` (4-digit) |
| Обидва перспективи потрібні | **Два ADR** з cross-ref (приклад: v3/0049 + trader-v3/039) |

### 5. Deploy check (VPS)

Перед deploy:
- [ ] `trader-v3/deploy.sh` не copy'ить `data/*.json` (setup_v3.sh skip existing)
- [ ] `smc_trader_prompt_v3.md` — поточна версія (~1500 lines)
- [ ] `tests/` — pytest passes локально
- [ ] VPS: `ssh aione-vps "sudo supervisorctl status smc_trader_v3"` = RUNNING

### 6. Post-deploy watch (обов'язково)

Див. `.github/prompts/post-deploy-watch.prompt.md`. Watch window = **48h**.

## Питання-фільтр перед PATCH

1. Чи ти додаєш hard block? → Чи це одне з 4 дозволених?
2. Чи Арчі побачить цю зміну у своєму контексті / логах / промпті?
3. Чи не змішую v3 platform з trader-v3 у одному commit? (X31)
4. Чи не кладу Арчі runtime data у v3/ дерево? (X32)
5. Чи мова в промпті — **рекомендації чи накази**? (I7: рекомендації)

## Заборони (specific to Арчі work)

- **X29**: hard block (cooldown, force-downgrade, suppress, timer re-injection) без safety justification
- **X30**: hidden constraint невидимий для Арчі
- **X31**: створювати/змінювати v3 ADR під час trader-v3 task
- **X32**: класти Арчі runtime data у v3 root або v3/data/

Виконуй Арчі-task: {{input}}
