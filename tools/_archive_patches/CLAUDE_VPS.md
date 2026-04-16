# trader-v3 — Claude Code Instructions

> Читай це першим при кожній сесії. Це SSOT інструкція для роботи з trader-v3 на VPS.
> Якщо конфліктує з `.github/copilot-instructions.md` — `.github` файл виграє.

---

## Хто ти і хто Стас

Ти — AI-помічник розробника Стаса. Працюєш напряму на VPS з кодом trader-v3 (AI-трейдер "Арчі").
Мова: **українська**. Технічні терміни англійською. Жаргон дозволений.

---

## Що це за проект

**trader-v3** — автономний AI SMC-трейдер "Арчі" з власною особистістю, що працює через Telegram.

- **Людина-аналог**: досвідчений друг-трейдер, який аналізує XAU/USD (+ крипта), дає оцінки, памʼятає контекст
- **Моделі**: Claude Sonnet (default ~$0.02/call), Opus (deep analysis ~$0.08), Haiku/Sonnet (sentinel ~$0.003)
- **Бюджет**: $3-8/місяць. Weekend = sleep mode (0 calls)

### Повʼязаний проект: v3 Platform

v3 Platform (OHLCV pipeline + SMC engine + Chart UI) = **окремий репозиторій**, не тут.
Інтеграція тільки через HTTP API (`http://127.0.0.1:8000/api/*`), WebSocket, та Redis IPC.
**НЕ РЕДАГУЙ файли v3 платформи** з цього проекту.

---

## Структура deployed-коду

```
/opt/smc-trader-v3/
├── bot/                         # Основний пакет (supervisor: python -m bot.main)
│   ├── main.py                  # Entry point
│   ├── config.py                # Config dataclasses
│   ├── agent/                   # Claude API calls
│   │   ├── core.py              # AgentCore — main Claude API caller
│   │   ├── prompts.py           # System prompt builder
│   │   ├── observation_router.py # ObservationRouter (Haiku gate, ⚠ проблемний)
│   │   ├── discipline.py        # Trade discipline checks
│   │   ├── scanner.py           # Symbol scanner
│   │   └── structured_output.py # JSON output parsing
│   ├── scheduling/              # Proactive monitor loop
│   │   ├── monitor.py           # Monitor loop v2 + ThesisStateMachine integration
│   │   ├── mechanical.py        # Mechanical checks (price, ATR, session)
│   │   ├── cost.py              # Cost tracking
│   │   └── scheduler.py         # Schedule management
│   ├── state/                   # Persistence layer
│   │   ├── directives.py        # emit_directives (Claude → code), wake_at, thesis_sm_state
│   │   ├── event_journal.py     # EventJournal — central event ledger
│   │   ├── thesis.py            # ThesisStateMachine (IDLE→WATCHING→CLOSED)
│   │   ├── manager.py           # State manager
│   │   ├── thinking_archive.py  # Thinking archive (JSONL)
│   │   ├── conv_memory.py       # Conversation memory
│   │   ├── curator.py           # Knowledge curator
│   │   ├── forecasts.py         # Forecast tracker
│   │   ├── predictions.py       # Prediction tracker
│   │   └── ...                  # digest, handlers, self_eval, etc.
│   ├── transport/               # I/O layer
│   │   ├── handlers.py          # Telegram message handlers
│   │   ├── telegram.py          # Telegram API wrapper
│   │   ├── platform.py          # v3 Platform API client
│   │   └── voice.py, web_inbox.py, events.py
│   ├── enrichment/              # External data
│   │   ├── market_data.py       # Market data enrichment
│   │   └── news_feed.py         # News feed
│   └── tools/                   # Tool executor
│       └── executor.py
├── config.json                  # SSOT configuration (⚠ єдине джерело правди)
├── smc_trader_prompt_v3.md      # Промпт Арчі (~750 рядків, його "ДНК")
├── .env                         # Secrets (ANTHROPIC_API_KEY, TELEGRAM_*, etc.)
├── data/                        # Runtime state (⚠ НІКОЛИ не перезаписувати!)
│   ├── v3_conversation.json     # Chat history (40 msgs)
│   ├── v3_agent_directives.json # Directives from Claude
│   ├── v3_knowledge.json        # KB
│   ├── event_journal.json       # EventJournal
│   └── ... (20+ runtime files)
├── docs/                        # Architecture docs
│   ├── ARCHITECTURE.md          # Why things are built this way
│   └── adr/                     # 26 ADR (001-026)
│       ├── ADR-024-autonomy-charter.md  # Конституційний I7
│       ├── ADR-033-*             # Dual-Mode Architecture
│       └── ADR-034-*             # Wake Conditions Architecture
├── tests/                       # 6 test files
├── prompts/                     # Additional prompt fragments
├── knowledge/                   # Knowledge base (prompt copy)
├── .github/copilot-instructions.md  # SSOT правила для AI-агентів
├── CONTRIBUTING.md              # Autonomy-first guardrails
└── CHANGELOG.md
```

---

## Інваріанти (HARD RULES — порушення = зупинка)

| # | Інваріант | Пояснення |
|---|-----------|-----------|
| I7 | **Autonomy-First** | Арчі сам приймає рішення. Код = advisory + explain. Hard block тільки: kill switch, daily $ hard cap, owner-only guard, anti-hallucination |
| | **One Agent** | Reactive/proactive/review = режими одного Арчі, НЕ окремі боти |
| | **Config SSOT** | `config.json` = єдине джерело конфігурації. Жодних hardcoded thresholds |
| | **Data Sacred** | `data/` = runtime state. НІКОЛИ не перезаписувати при deploy. НІКОЛИ не seed з локальних файлів |
| | **VPS follows local** | Зміни локально → deploy на VPS. Не навпаки (виняток: emergency hotfix з документуванням) |
| | **Secrets in .env** | `.env` = secrets only. Не комітити, не логувати |
| | **No personality kill** | `smc_trader_prompt_v3.md` = DNA Арчі. Не нейтралізувати голос, не замінювати generic промптом |

---

## Поточний стан (квітень 2026)

### ✅ Deployed & working:
- **ThesisStateMachine** в monitor.py — states: IDLE → WATCHING → APPROACHING → IN_ZONE → TRIGGERED → CLOSED
- **EventJournal** — central event ledger (event_journal.json)
- **Monitor loop v2** з CHECK 0-4 циклом
- **emit_directives** з `wake_at[]`, `watch_levels[]`, `thesis_sm_state`
- **Budget guard** — emergency cap з бекапами directives при порушенні

### ⚠️ Diagnosed problem: Silent Archi
`ObservationRouter` (Haiku gate в `bot/agent/observation_router.py`) блокує ВСІ observations
коли ціна далеко від entry zone → 12+ годин тиші. Morning briefing вимкнений (`if False`).

### 🔴 Не імплементовано (тільки ADR написано):
- **ADR-034**: Wake Conditions Architecture — Арчі сам каже "розбуди мене коли X"
- **v3/ADR-0048**: Platform WakeEngine — v3 перевіряє wake conditions в delta_loop кожні 2s
- Жоден рядок коду не написаний — тільки архітектурні документи

### Priorities (що робити далі):
1. Замінити Haiku gate → wake conditions (ADR-034)
2. Інтегруватись з v3 WakeEngine через Redis (ADR-0048, обидва боки)
3. Re-enable morning briefing через wake condition `session_open`

---

## Інтеграція з v3 Platform

| Канал | Напрямок | Деталі |
|-------|----------|--------|
| Redis IPC | bot ↔ platform | namespace `v3_local`, DB=1 |
| Bot writes | → Redis | `agent:state`, `agent:feed`, `archi:chat` |
| Platform writes | → Redis/WS | OHLCV, SMC zones, narrative (SmcRunner, delta_loop 2s) |
| HTTP API | bot → platform | `http://127.0.0.1:8000/api/status`, `/api/bars` |
| **Future** | bot ↔ platform | `wake:conditions`, `wake:events`, `wake:notify` Redis keys |

---

## Команди

```bash
# Стан бота
sudo supervisorctl status smc_trader_v3
tail -50 /opt/smc-trader-v3/logs/supervisor.log
tail -f /opt/smc-trader-v3/logs/supervisor.log  # live

# Рестарт (з осторогою!)
sudo supervisorctl restart smc_trader_v3

# Kill switch (emergency stop)
touch /opt/smc-trader-v3/KILL
# Видалити перед рестартом після kill:
rm /opt/smc-trader-v3/KILL

# Тести
cd /opt/smc-trader-v3 && python -m pytest tests -q

# v3 Platform стан
curl -s http://127.0.0.1:8000/api/status | python3 -m json.tool

# Логи платформи
tail -20 /var/log/smc-v3/ws_server.stderr.log

# Directives snapshot
python3 -c "import json; d=json.load(open('data/v3_agent_directives.json')); print(json.dumps({k:d[k] for k in ['thesis_sm_state','wake_at','last_analysis_summary']}, indent=2, default=str))"

# Event Journal (останні 5)
tail -5 data/event_journal.json
```

---

## Known Traps

| Trap | Деталі |
|------|--------|
| **KILL file persists** | `touch KILL` зупиняє бота. Файл залишається після restart. `rm KILL` перед перезапуском |
| **data/ timestamps** | `v3_agent_directives.json` — timestamps = epoch seconds. Використовуй `time.time()` |
| **Stale monolith files** | `/home/ubuntu/smc_trader_v3.py` та `adr002_directives.py` — СТАРІ копії (Apr 1). Backed up в `/tmp/stale_backup_20260414/`. Справжній код тільки тут: `/opt/smc-trader-v3/bot/` |
| **monitor.py backups** | `scheduling/monitor.py.bak.20260413_*` — бекапи від budget guard fix. Не видаляти поки що |
| **Supervisor autostart=false** | Бот не стартує автоматично при reboot VPS. Manual: `sudo supervisorctl start smc_trader_v3` |
| **Two .bak files** | `data/v3_agent_directives.json.bak.20260413_*` — бекапи від budget guard та timer patches |

---

## Ключові ADR (обовʼязково прочитати при значних змінах)

| ADR | Назва | Статус |
|-----|-------|--------|
| 024 | Autonomy Charter (конституція I7) | **Active** — SSOT |
| 033 | Dual-Mode Architecture | **Deployed** |
| 034 | Wake Conditions Architecture | **Written, NOT implemented** |
| 018 | Glass-Box Thinking Preservation | Implemented |
| 016 | Cost Self-Regulation | Implemented |
| 026 | Living Context | Implemented |

Повний індекс: `docs/adr/index.md`

---

## Стиль

- Українська для prose/коментарів/логів. Англійська для коду/API/термінів
- Один патч = одна ціль. Verify перед наступним
- Evidence-based: `[VERIFIED file:line]` / `[INFERRED]` маркери
- Degraded-but-loud: жодних `except: pass`
