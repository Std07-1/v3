# ADR-0045: VPS SMC Trader Bot (smc_trader_v3.py)

- **Status**: Accepted
- **Date**: 2026-03-30
- **Author**: R_ARCHITECT
- **Initiative**: `smc_trader_bot_v1`
- **Related ADRs**: ADR-0001 (UDS), ADR-0024 (SMC Engine), ADR-0033 (Narrative), ADR-0036 (Premium Shell), ADR-0039 (Signal Engine), ADR-0040 (TDA Cascade)

---

## 1. Context and Problem

### 1.1 Current State

Trading Platform v3 runs on VPS at `/opt/smc-v3/` under supervisor. The platform exposes:

- **WebSocket** at `ws://localhost:8000/ws` — real-time candle/SMC frames (full + delta)
- **HTTP** `/api/status` — health probe returning `{status, boot_id, ws_clients, server_ts_ms}`
- **WS action** `{"action": "switch", "symbol": "XAU/USD", "tf": "M15"}` → full frame with `bias_map`, `zone_grades`, `narrative`, `signals`, `session_levels`, `pd_state`

**The problem**: The trader interacts with the platform only through the browser UI. There is no mobile-accessible analysis interface. During off-screen hours, killzone entries, or session opens, the trader has no proactive notification. The trader wants:

1. **Reactive analysis** — send chart screenshots (1-5 photos) + text question to Claude via Telegram, receive SMC-enriched analysis using live platform context
2. **Proactive monitoring** — periodic background check that pushes notifications when market conditions change meaningfully (not spam on every tick)
3. **Persistent state** — conversation history, market bias tracking, waiting-for conditions across restarts

### 1.2 Boundary Constraint

The bot is a **consumer** of platform data, not a platform component. It does NOT:
- Import UDS, Redis, or any `core/`/`runtime/` module directly
- Write to `data_v3/` or Redis
- Run inside the `app.main` supervisor process tree
- Share Python environment with the platform

It lives in a **separate directory** (`/opt/smc-trader-v3/`) with its own virtualenv, its own supervisor program, and communicates exclusively through the platform's public WS API (`localhost:8000/ws`) and health endpoint (`/api/status`).

### 1.3 Failure Model

| # | Scenario | Consequence | Mitigation |
|---|----------|-------------|------------|
| F1 | Platform down (ws_server not running) | `/api/status` fails, WS connect fails | Health check before analysis; degrade to Claude-only, log WARNING |
| F2 | Anthropic API rate limit / 529 | Claude call fails | User gets error message; logged at ERROR level |
| F3 | Telegram API down | Bot cannot send/receive | aiogram handles reconnects; supervisor restarts on crash |
| F4 | Bot crash mid-conversation | State lost if not persisted | State file written after every meaningful change |
| F5 | Media group split (photos arrive as separate messages) | Incomplete analysis context | Album collector with 2.5s asyncio timer aggregates all photos from same `media_group_id` |
| F6 | Unauthorized user sends message | Bot responds to stranger | CHAT_ID filter: reject all messages where `message.chat.id != CHAT_ID` |
| F7 | State file corruption (disk full, power loss) | Bot starts with no history | Load with try/except; on failure, start with empty state + log WARNING |
| F8 | WS connection drops mid-context fetch | Incomplete context snapshot | aiohttp session timeout (20s); partial result logged; analysis proceeds with available TFs |
| F9 | Anti-spam suppression | Too-frequent proactive messages | `last_msg_ts` tracked in state; 3h cooldown per direction built into `_compute_change_score` |

### 1.4 Constraints

| Constraint | Details |
|------------|---------|
| **I1 (UDS narrow waist)** | Bot reads via WS (`action: switch`) and HTTP `/api/status`. No direct UDS/Redis import. |
| **I5 (Degraded-but-loud)** | Every failure path logs explicitly. No silent fallback to empty response. |
| **Process isolation** | Separate directory, separate venv, separate supervisor program. Platform process tree untouched. |
| **Security** | Single-owner bot. CHAT_ID filter. Bot token and Anthropic key in `.env` only. |
| **Python** | 3.11+ (zoneinfo for Kyiv timezone, asyncio.TaskGroup compat) |

---

## 2. Considered Alternatives

### Alternative A: Platform-integrated module in `runtime/`

- **Essence**: Add `runtime/bot/telegram_bot.py` inside platform codebase. Import SmcRunner directly.
- **Pros**: Direct SmcRunner access; single deployment.
- **Cons**: Violates I0 (Dependency Rule). Couples bot/platform lifecycles. Adds Telegram/Anthropic deps to platform venv. Bot bug can crash platform asyncio loop.
- **Blast radius**: `runtime/`, `app/main.py`, `requirements.txt`, supervisor config.

### Alternative B: Separate process, localhost WS/HTTP only ← **CHOSEN**

- **Essence**: Standalone `smc_trader_v3.py` in `/opt/smc-trader-v3/`. Communicates via `localhost:8000` WS and `/api/status` only.
- **Pros**: Zero blast radius on platform. Independent deploy/restart. I1 preserved.
- **Cons**: One extra WS hop for context (localhost, negligible latency ~1ms).
- **Blast radius**: 0 platform files touched.

### Alternative C: Serverless / Lambda

- **Essence**: Bot webhook on external platform. Platform exposes API externally.
- **Pros**: No VPS process management.
- **Cons**: Requires exposing platform API externally (security risk). No persistent proactive monitoring. Cold starts. State management complexity.
- **Blast radius**: Platform needs auth middleware. Network config changes.

**Decision: Alternative B** — zero blast radius on platform (I1 + I6 safety). Independent lifecycle. No new platform code required.

---

## 3. Architecture

### 3.1 Diagram

```
/opt/smc-v3/ (platform, supervisor group: smc)     /opt/smc-trader-v3/ (bot, supervisor: smc-trader-v3)
+------------------------------------------+        +-------------------------------------------+
| ws_server.py (:8000)                     |        | smc_trader_v3.py                          |
|  +-- /api/status <-----------------------+-HTTP---+-- PlatformHealthCheck                    |
|  +-- /ws (switch→full frames) <----------+-WS-----+-- PlatformContextFetcher                 |
|  |   [read-only, I1 preserved]           |        |                                           |
|  |                                       |        | +-- TelegramBot (aiogram 3.x)             |
| UDS (in-memory, NOT touched by bot)      |        | |   /start /status /state /context        |
| Redis (NOT touched by bot)               |        | |   /intense /calm /auto /pause /resume    |
| data_v3/ (NOT touched by bot)            |        | |   /waiting <text>  /forget               |
+------------------------------------------+        | |   photo+text handler (album collector)   |
                                                    | |                                           |
                                                    | +-- ProactiveMonitor (asyncio.Task)        |
                                                    |     adaptive interval: launch/entry/info   |
                                                    |     change_score → send / SKIP             |
                                                    |                                           |
                                                    | +-- StateManager                          |
                                                    |     data/v3_market_state.json             |
                                                    |     data/v3_conversation.json             |
                                                    |     data/v3_bot_config.json               |
                                                    |                                           |
                                                    | +-- AnthropicClient (claude-opus-4-6)     |
                                                    |     Reactive: max_tokens=2000             |
                                                    |     Proactive: max_tokens=500             |
                                                    +-------------------------------------------+
```

**Data flow**: Bot → Platform (WS read). Never reverse. Platform is unaware of bot existence.

### 3.2 Phase 1: Reactive Mode

User sends 1-5 photos + text to Telegram. Bot:

1. Collects photos via album collector (2.5s asyncio.Task per `media_group_id`)
2. Downloads each photo → detects MIME type from magic bytes → base64 encode
3. Connects WS, switches through ANALYSIS_TFS (D1/H4/H1/M15), collects full frames
4. Builds Claude prompt: system prompt (SMC mentor persona) + platform context summary + conversation history (last 20) + photos + user text
5. Calls `claude-opus-4-6` with vision: `max_tokens=2000`
6. Sends response via Telegram
7. Persists turn to `v3_conversation.json` (photos stored as `"[N фото]"` description — not raw bytes)

**Album collector**: asyncio.Task per `media_group_id` with 2.5s timer. Timer resets on each new photo. On fire: collect all buffered messages → `process_messages()`. NOT aiogram middleware — simpler and avoids middleware ordering issues.

### 3.3 Phase 2: Proactive Monitor

Background `asyncio.Task` running parallel to Telegram polling.

**Session windows (Kyiv time, `Europe/Kyiv` via `zoneinfo`)**:

| Window | Kyiv time | Type | Interval |
|--------|-----------|------|----------|
| London/NY overlap | 11:00-13:00 | launch | 5 min |
| London open | 08:00-11:00 | entry | 10 min |
| NY open+active | 13:00-20:00 | entry | 10 min |
| Asia / off hours | 20:00-08:00 | info | 30 min |
| Saturday/Sunday | all day | weekend | OFF |

Override commands: `/intense` (always 5min), `/calm` (always 60min), `/auto` (adaptive), `/pause` (off).

**change_score algorithm**:

| Change | Score delta |
|--------|-------------|
| Bias flip on D1 or H4 | +4.0 |
| Bias flip on H1 | +2.0 |
| D1 + H4 same direction (confluence) | +2.0 |
| Narrative mode → "entry" or "prepare" | +4.0 |
| Narrative mode: entry/prepare → "wait" (invalidated) | +2.0 |
| Other narrative mode change | +1.0 |
| New A+/A signal (fingerprint not in previous state) | +3.0 |
| New B signal | +1.0 |
| P/D label change | +2.0 |
| Last message < 180 min ago (anti-spam) | cap at threshold-0.1 |

**Send thresholds**:
- `score >= 4.0` → always send (SCORE_SEND_THRESHOLD)
- `score >= 3.0` AND session is `launch` or `entry` → send (SCORE_ENTRY_THRESHOLD)
- Otherwise → log SKIP, no API call

If threshold met → call Claude (`call_proactive`) with context diff + previous state. Claude returns:
- Message text → send to trader
- `"SKIP"` (literal) → do not send (narrative didn't change meaningfully for trader)

### 3.4 Phase 3: Persistent State

**`v3_market_state.json`**:
```json
{
  "version": 1,
  "bias_map": {"D1": "bearish", "H4": "bearish", "H1": "bullish"},
  "narrative_mode": "prepare",
  "trend_bias": "bearish",
  "pd_label": "premium",
  "signals_fp": ["A+/bearish/3295.5", "A/bearish/3290.0"],
  "waiting_for": "ретест OB 3045-3058",
  "last_msg_ts": 1711756800,
  "last_msg_direction": "bearish",
  "session_log": [
    {"ts": 1711756200, "score": 4.5, "narr_mode": "prepare", "bias": "bearish"}
  ],
  "updated_at": 1711756800
}
```

**`v3_conversation.json`**: list of `{role, content, ts}` — last `MAX_HISTORY * 2 = 40` entries. Photos stored as `"[N фото]"` text description.

**`v3_bot_config.json`**: `{monitor_mode: "auto"|"intense"|"calm"|"pause", last_command_ts}`.

**State writes**: atomic (Python's `Path.write_text` is effectively atomic on Linux for small files). On load failure: default dict + WARNING log.

### 3.5 Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `TELEGRAM_BOT_TOKEN` | Yes | — | From @BotFather |
| `TELEGRAM_CHAT_ID` | Yes | — | Your personal Telegram numeric ID |
| `ANTHROPIC_API_KEY` | Yes | — | Anthropic API key |
| `CLAUDE_MODEL` | No | `claude-opus-4-6` | Claude model |
| `PRIMARY_SYMBOL` | No | `XAU/USD` | Symbol to monitor |
| `PLATFORM_WS_URL` | No | `ws://localhost:8000/ws` | Platform WS |
| `PLATFORM_HTTP_URL` | No | `http://localhost:8000` | Platform HTTP |
| `DATA_DIR` | No | `./data` | State JSON directory |
| `LOG_DIR` | No | `./logs` | Log file directory |

### 3.6 File Layout on VPS

```
/opt/smc-trader-v3/
├── smc_trader_v3.py          # Single-file bot (~1100 LOC)
├── .env                       # Secrets (never commit)
├── .env.example               # Template
├── data/
│   ├── v3_market_state.json   # Persistent market state
│   ├── v3_conversation.json   # Chat history (last 40 turns)
│   └── v3_bot_config.json     # Monitor mode + settings
└── logs/
    └── v3_bot.log             # Rotating log

/etc/supervisor/conf.d/
└── smc-trader-v3.conf         # Independent from smc group
/var/log/smc-trader-v3/
├── bot.stdout.log
└── bot.stderr.log
```

---

## 4. Invariant Verification

| Invariant | Status | Justification |
|-----------|--------|---------------|
| **I0 (Dependency Rule)** | Preserved | Bot is external process. Zero imports from `core/` or `runtime/`. |
| **I1 (UDS narrow waist)** | Preserved | Bot reads via WS (action: switch → full frame). No direct UDS/Redis access. |
| **I5 (Degraded-but-loud)** | Preserved | All failure paths (F1-F9) log explicitly. No silent fallbacks. |
| **B0 (Bot isolation)** | New | Bot MUST NOT import platform modules. No platform path in `sys.path`. |
| **B1 (API-only access)** | New | Bot accesses platform data exclusively via `localhost:8000`. |
| **B2 (Single owner)** | New | All message handlers check `message.chat.id == CHAT_ID`. |

---

## 5. P-Slices

| Slice | Scope | LOC | Verify |
|-------|-------|-----|--------|
| P1 | Skeleton: aiogram + CHAT_ID filter + commands + platform health | ~150 | `/start` replies; non-owner messages rejected |
| P2 | Reactive: album collector + WS context fetch + Claude vision + conversation persistence | ~300 | Photo+text → analysis with platform data |
| P3 | Proactive monitor: asyncio.Task + adaptive schedule + change_score + proactive Claude | ~250 | Bias flip → notification. `/intense`/`calm`/`auto` work |
| P4 | Commands: `/forget`, `/state`, `/waiting`, `/context` + bot_config persistence | ~100 | All commands persist correctly after restart |
| P5 | Setup: `setup_v3.sh` + supervisor config + `.env` template | ~80 | `supervisorctl start smc-trader-v3` works |

**Phases 1-3 shipped in single file** (current implementation). P4-P5 complete.

---

## 6. Consequences

### What Changes
- New `/opt/smc-trader-v3/` directory (external to platform repo)
- New supervisor program `smc-trader-v3` (separate from `[group:smc]`)
- Platform API load: at most 4 WS requests per 5-minute cycle (1 per TF) in `intense` mode. Negligible.

### What Does NOT Change
- Platform codebase (`/opt/smc-v3/`): **zero files modified**
- `data_v3/`, Redis, UDS: **untouched**
- Platform supervisor `smc` group: **unchanged**
- Wire format / WS protocol: **unchanged**

---

## 7. Rollback

| Scope | Steps |
|-------|-------|
| Full | `supervisorctl stop smc-trader-v3 && rm /etc/supervisor/conf.d/smc-trader-v3.conf && supervisorctl reread && rm -rf /opt/smc-trader-v3/` |
| Restart bot | `supervisorctl restart smc-trader-v3` |
| Disable proactive | Send `/pause` in Telegram |
| Reset state | `rm /opt/smc-trader-v3/data/*.json && supervisorctl restart smc-trader-v3` |

Platform rollback: **not required**. Bot creation/deletion has zero platform impact.

---

## 8. Deferred Scope

### Phase 4: News Calendar (deferred)
Economic calendar parsing (Forex Factory scrape or free API). Pre-news alerts (30min before 🔴 event). NFP/FOMC day mode. This adds significant complexity (web scraping fragility); deferred until Phase 1-3 stable.

### Phase 5: Cowork Watchdog (deferred)
Lightweight scheduled task (Claude.ai scheduled agent) that pings `/api/status` on bot and platform, sends alert if either down. Deferred: supervisor `autorestart=true` covers most failure scenarios. Watchdog adds value after 2+ weeks stable operation.

---

## 9. Open Questions

| # | Question | Owner |
|---|----------|-------|
| Q1 | Tune `change_score` weights after 1-2 weeks production data | R_TRADER |
| Q2 | Add web price search for proactive checks when platform context is stale? | R_ARCHITECT |
| Q3 | Should `v3_conversation.json` store thumbnail photos for context re-send? | R_PATCH_MASTER |
