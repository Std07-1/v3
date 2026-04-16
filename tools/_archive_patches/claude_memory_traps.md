# trader-v3 Known Traps & Lessons

## CandleBar .l vs .low (recurring)
- Platform CandleBar dataclass: `.o .h .low .c .v` — NOT `.l`
- Wire/dict format uses key `"l"` for low
- Using `bar.l` → AttributeError → empty SMC overlay

## data/ is sacred
- NEVER overwrite data/ files during deploy
- NEVER seed production with local data/ copies
- First deploy: create empty `{}` only if file doesn't exist

## Haiku gate = root cause of silence
- ObservationRouter (bot/agent/observation_router.py) uses cheap Haiku model as gate
- When price is far from any entry zone → Haiku says "not interesting" → blocks ALL observations
- Result: 12+ hours silence. Diagnosed Apr 2026, fix pending (ADR-034)

## KILL file persists
- `touch /opt/smc-trader-v3/KILL` stops the bot
- File survives restarts → bot won't work until `rm KILL`

## monitor.py backup files
- `monitor.py.bak.20260413_201421` = pre-budget-guard state
- `monitor.py.bak_timers.20260413_202232` = pre-timer-disable state
- Keep as evidence, don't delete yet

## Budget incident
- Emergency budget overrun led to backup + guard patch on Apr 13
- directives.json also has .bak files from same date

## Стас — стиль роботи
- Цінує глибину і якість, не терпить поверхневі відповіді
- Працює українською, технічні терміни англійською
- Правило: один патч = одна ціль, verify перед наступним
- Evidence-based: завжди показуй proof (logs, grep, file:line)
