# Cowork Operational Frame v3.0

> **Призначення**: операційна механіка Cowork scheduled task у Claude Desktop sandbox.
> **Pair з**: `cowork_prompt_template_v3.md` (constitutional fence — поведінка LLM).
> **Цей файл** — як цю поведінку **виконати**: token loading, fetch+fallback,
> dedup, hallucination guard, Telegram publish, anti-spam, error handling, logging.
> **Authority**: I0–I7 platform > S0–S6 SMC > X28 > DT-канон > prompt template > цей файл.

> Architect'ський v3 prompt описує політику аналітика, але не операційну механіку
> Cowork scheduled task. Цей frame покриває технічний шар як виконати constitutional
> fence у sandbox-середовищі без env injection і з дисковим fallback.

---

## СТЕП 0 — Завантаж токени з .env (LLM-level через Read tool)

Cowork scheduled task НЕ підтримує env vars. Завантаж секрети на старті scan'у:

```
Read(file_path: "C:\\Users\\vikto\\Documents\\Claude\\Scheduled\\trade-setup-scanner\\.env")
```

Витягни **рівно два значення**:

- `old_news_bot=<TOKEN_API>` — формат `tk_<64_hex>`. API token для `/api/v3/*`.
- `TG_BOT_TOKEN=<TOKEN_TG>` — формат `<digits>:<base64-ish>`. Telegram bot token.

**Fail-loud перевірки:**

- Файл не читається → abort, alert у приват `1501232696`: «.env not readable», exit.
- Будь-який ключ відсутній → abort, alert.
- Format invalid (не починається з `tk_` / не містить `:`) → abort, alert.

Усі ops alert'и йдуть **у приват `1501232696`**, не в канал.

**Security:**

- Тримай значення у LLM context **тільки на час scan'у**.
- **Ніколи не echo, не log, не save** на диск.
- У всіх bash-командах далі вживай **inline substitution**: підстав конкретне
  значення у `-H "X-API-Key: <ось_сюди_old_news_bot_value>"`. Не пиши
  `${old_news_bot}` — bash env пустий.
- При формуванні Telegram curl: те саме, інлайн `bot<TG_BOT_TOKEN_VALUE>/sendMessage`.
- При логуванні в `outputs/scan_log.jsonl`: redact як `tk_***` і `bot:***`.

> **Cadence guard (STEP 0a/0b)** — див. `cowork_prompt_template_v3.md`. Той guard
> виконується **перед** token loading щоб не палити thinking budget на off-slot
> cron drift. Operational frame нижче активується тільки після cadence pass.

---

## СТЕП 1 — Fetch market context (graceful degradation з API → file mount)

Стратегія двошарова: спершу ADR-0059 endpoints (architect's design), при 404 або 503 —
fallback на filesystem mount (бо local v3 пише на диск у real-time).

### Шар A — API attempts (з inline X-API-Key)

```bash
KEY="<ВСТАВ_ТУТ_old_news_bot_VALUE>"
BASE="https://aione-smc.com/api/v3"

# ADR-0059 raw-data analysis endpoints (можуть 404 якщо не deployed)
curl -s -H "X-API-Key: $KEY" "$BASE/smc/levels?symbol=XAU/USD"            > /tmp/levels.json
curl -s -H "X-API-Key: $KEY" "$BASE/smc/zones?symbol=XAU/USD&tf=M15"      > /tmp/zones.json
curl -s -H "X-API-Key: $KEY" "$BASE/bars/window?symbol=XAU/USD&tfs=M15,H1,H4" > /tmp/bars.json

# ADR-0058 existing endpoints (стабільні; /bias і /narrative можуть 503 коли SmcRunner спить)
curl -s -H "X-API-Key: $KEY" "$BASE/macro/context"                          > /tmp/macro.json
curl -s -H "X-API-Key: $KEY" "$BASE/signals/latest?source=all&limit=10"    > /tmp/signals.json
curl -s -H "X-API-Key: $KEY" "$BASE/bias/latest?symbol=XAU/USD"            > /tmp/bias.json
curl -s -H "X-API-Key: $KEY" "$BASE/narrative/snapshot?symbol=XAU/USD"     > /tmp/narrative.json

# ADR-001 cowork memory (PRIOR CONTEXT — обов'язково перед thinking; див. STEP 0.5 у prompt template)
curl -s -H "X-API-Key: $KEY" "$BASE/cowork/recent_thesis?symbol=XAU/USD&limit=3&max_age_h=12" > /tmp/prior.json
```

### Шар B — File mount fallback (для будь-якого 404/503 на API)

Якщо `/smc/levels`, `/smc/zones`, або `/bars/window` повернули `404 not_found` —
читай з файлової системи через Read tool:

```
TODAY=YYYY-MM-DD у UTC
TODAY_COMPACT=YYYYMMDD

# Bars: останні N candles M15/H1/H4 з local v3 store
Read(file_path: "C:\\Users\\vikto\\aione-context\\v3\\data_v3\\XAU_USD\\tf_900\\part-{TODAY_COMPACT}.jsonl")
Read(file_path: "C:\\Users\\vikto\\aione-context\\v3\\data_v3\\XAU_USD\\tf_3600\\part-{TODAY_COMPACT}.jsonl")
Read(file_path: "C:\\Users\\vikto\\aione-context\\v3\\data_v3\\XAU_USD\\tf_14400\\part-{TODAY_COMPACT}.jsonl")

# Today's signal journal (TDA + SMC narrative events)
Read(file_path: "C:\\Users\\vikto\\aione-context\\v3\\data_v3\\_signals\\journal-{TODAY}.jsonl")

# TDA state snapshot per symbol
Read(file_path: "C:\\Users\\vikto\\aione-context\\v3\\data_v3\\_signals\\tda_state.json")
```

Mount paths Windows-format для Read tool (НЕ `/sessions/.../mnt/v3` — це bash-only).

### Поведінка при 503 на `/bias/latest` чи `/narrative/snapshot` (SmcRunner sleep)

- Не fail. Це degraded mode, не error.
- Reading bias з last journal entry (бери `bias` field останнього SMC narrative
  event для XAU/USD).
- Note "live bias unavailable, using last known from <ts>" у пості якщо релевантно.

При **404 на нові endpoints + повністю недоступний mount** → fail-loud, не публікуй.

---

## СТЕП 2 — Виконай аналіз за CORE BEHAVIOR

Прочитай і застосуй `cowork_prompt_template_v3.md` як constitutional fence.
Поведінка LLM (ROLE, MENTAL TOOLBOX M1-M9, THINKING PROTOCOL 5 етапів,
OUTPUT TEMPLATE 7 секцій, ABSOLUTE RULES R1-R10, ANTI-PATTERNS AP1-AP10,
VOICE, CHECKLIST, MODEL, INTEGRATION, ETALON) — звідти. **Не модифікуй.**

**Extended thinking config** (передавай у Anthropic API call):

- **Opus 4.7** (recommended for pivotal moments — NFP/CPI/FOMC days, A+ POI proximity):
  - `extended_thinking`: true
  - `thinking_budget_tokens`: 6000-8000
  - `temperature`: 0.6
- **Sonnet 4.6** (regular hourly runs, no major event):
  - `extended_thinking`: true
  - `thinking_budget_tokens`: 3000-4000
  - `temperature`: 0.5

---

## СТЕП 3 — Дедуп

Файл стану: `/sessions/wonderful-quirky-hamilton/mnt/outputs/published_keys.jsonl`
(append-only, на mount'і).

Прочитай останні 50 рядків, побудуй `last_state`:

- `last_pub_ts_unix`
- `today_count` (постів за сьогодні UTC)
- `last_bias_hash` (hash D1+H4 bias за last published)

Якщо нічого нового відносно last published → **silent_skip** з log запис у
`scan_log.jsonl`. Не дійшло б навіть до Telegram.

> **Note**: дедуп через `published_keys.jsonl` — operational layer (sandbox-local).
> Cowork memory `/api/v3/cowork/published` (R10 у prompt template) — okремий
> платформенний audit trail. Обидва ведуться, не дублюють один одного.

---

## СТЕП 4 — Hallucination guard (Python regex check)

Перед `sendMessage` — Python запис тіла перевіряє кожне число у `$POST_TEXT`
проти source payloads. v3 ALLOWS local-compute math (probabilities як ~XX%,
ATR distance multiples як X.X ATR) — guard має white-list ці patterns:

```bash
python3 - <<'PY'
import json, re, sys, os

post_text = open('/tmp/post_text.txt').read()
sources = []
for f in ('/tmp/levels.json', '/tmp/zones.json', '/tmp/bars.json',
         '/tmp/macro.json', '/tmp/signals.json', '/tmp/bias.json',
         '/tmp/narrative.json', '/tmp/mount_bars.json', '/tmp/mount_journal.json',
         '/tmp/prior.json'):
    if os.path.exists(f):
        try:
            sources.append(json.load(open(f)))
        except Exception:
            with open(f) as ff:
                sources.append(ff.read())

NUM_RE = re.compile(r"-?\d+(?:[.,]\d+)?")

def collect(node, acc):
    if isinstance(node, (int, float)):
        acc.add(str(node)); acc.add(f"{node:.2f}"); acc.add(f"{node:.4f}")
    elif isinstance(node, str):
        for m in NUM_RE.findall(node):
            acc.add(m.replace(",", "."))
    elif isinstance(node, dict):
        for v in node.values():
            collect(v, acc)
    elif isinstance(node, list):
        for v in node:
            collect(v, acc)

ground = set()
for s in sources:
    collect(s, ground)

# v3 white-list patterns: probabilities, ATR multiples, IOFED stages
PROB_RE = re.compile(r"~?\d{1,3}\s*%")
ATR_RE = re.compile(r"\d+(?:[.,]\d+)?\s*ATR")

def is_derived_in_context(token, surrounding):
    if PROB_RE.search(surrounding):
        return True
    if ATR_RE.search(surrounding):
        return True
    return False

violations = []
for m in NUM_RE.finditer(post_text):
    tok = m.group().replace(",", ".")
    if tok in ground:
        continue
    start, end = max(0, m.start() - 15), min(len(post_text), m.end() + 15)
    surrounding = post_text[start:end]
    if is_derived_in_context(tok, surrounding):
        continue
    violations.append(tok)

if violations:
    print(f"GUARD_BLOCKED: {violations}", file=sys.stderr)
    sys.exit(1)
print("GUARD_OK")
PY
```

`exit 1` → **не публікуй**, log в `outputs/guard_blocks.jsonl`, alert ops у
приват, не retry.

---

## СТЕП 5 — Telegram publish (тільки після guard OK)

```bash
TG_BOT="<ВСТАВ_ТУТ_TG_BOT_TOKEN_VALUE>"
CHAT="-1003705152888"  # @smc_v3 канал

# v3 markdown → HTML conversion (Telegram parse_mode=HTML не рендерить markdown)
POST_TEXT_HTML=$(python3 - <<'PY' <<<"$POST_TEXT"
import sys, re
text = sys.stdin.read()
# Bold: **text** → <b>text</b>
text = re.sub(r'\*\*([^*\n]+)\*\*', r'<b>\1</b>', text)
# Code blocks: ```...``` → <pre>...</pre>  (multiline)
text = re.sub(r'```\n?(.*?)\n?```', r'<pre>\1</pre>', text, flags=re.DOTALL)
# Inline code: `text` → <code>text</code>
text = re.sub(r'`([^`\n]+)`', r'<code>\1</code>', text)
# HTML-escape stray < > & (only those NOT part of our tags above)
PLACEHOLDERS = []
def stash(m):
    PLACEHOLDERS.append(m.group(0))
    return f"\x00TAG{len(PLACEHOLDERS)-1}\x00"
text = re.sub(r'</?(?:b|i|u|s|code|pre)[^>]*>', stash, text)
text = text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
def restore(m):
    return PLACEHOLDERS[int(m.group(1))]
text = re.sub(r'\x00TAG(\d+)\x00', restore, text)
print(text, end='')
PY
)

curl -s -X POST "https://api.telegram.org/bot${TG_BOT}/sendMessage" \
  -H "Content-Type: application/json" \
  -d "$(jq -n --arg t "$POST_TEXT_HTML" --arg c "$CHAT" \
    '{chat_id: ($c|tonumber), text: $t, parse_mode: "HTML", disable_web_page_preview: true}')"
```

Тільки після `200` від Telegram → запиши в `published_keys.jsonl`:

```bash
python3 - <<'PY'
import json
from datetime import datetime, timezone
rec = {
    "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "key": "<DEDUP_KEY>",       # hash з symbol + ts + thesis_signature
    "message_id": 0,             # replace with actual MSG_ID_FROM_TG
    "kind": "v3_thesis",         # v3_thesis | v3_side_note | v3_thin_ice | v3_legacy_fallback
    "bias_hash_xau": "computed",
}
with open("/sessions/wonderful-quirky-hamilton/mnt/outputs/published_keys.jsonl", "a") as f:
    f.write(json.dumps(rec) + "\n")
PY
```

**Після Telegram OK + локального журналу — обов'язково POST до cowork memory**
(R10 у prompt template):

```bash
curl -s -X POST -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  "$BASE/cowork/published" \
  --data @/tmp/published_thesis.json
```

Тіло = повний `PublishedThesis` JSON per `cowork.memory.schema.PublishedThesis`.
Idempotent on `scan_id` (CW6). `appended:false, duplicate:true` = OK (race / retry).
422 = schema validation fail → log warning, не retry, завершити run (Discord уже
відправлено).

---

## СТЕП 6 — Anti-spam (per-cycle decisions)

| Trigger | Дія |
|---|---|
| Min interval 90 хв з останнього посту + немає A+ closure | silent_skip |
| Daily cap 4 пости (виключення A+ closure events) | silent_skip після 4-го |
| Off-hours (22:00-07:00 UTC) будні + немає A+ event | silent_skip |
| Weekend (Sat 00:00 → Sun 22:00 UTC) + немає A+ closure | silent_skip |
| WATCHDOG flag P6 (overtrade) | silent_skip |

**Substantive triggers що дозволяють публікацію:**

- новий TDA `signal_id`
- TDA `trade_closed` event
- SMC narrative `trigger=ready/triggered`
- SMC `trade_entered` / `trade_exited`
- bias flip на D1 або H4
- range exhaustion phase change
- тиша >6h + свіжа активність

---

## СТЕП 7 — Error handling matrix (per-endpoint schema_version aware)

| Status | Endpoint group | Дія |
|---|---|---|
| 200 + items | будь-який | Normal flow (Step 2+) |
| 200 + empty | будь-який | Silent skip (off-hours або no data) |
| 404 на ADR-0059 endpoints | `/smc/zones`, `/smc/levels`, `/bars/window` | File mount fallback per Step 1 Шар B |
| 503 `smc_runner_unavailable` | `/bias/latest`, `/narrative/snapshot` | Degraded mode — використай last known from journal |
| 503 `analysis_disabled_runtime` (kill switch ON) | `/smc/zones`, `/smc/levels`, `/bars/window` | Legacy mode — транскрибуй `signals/latest` з ADR-0058 endpoints, не публікуй власної аналітики (per CORE BEHAVIOR §"ЯКЩО kill switch ON") |
| 401 `invalid_api_key` | будь-який | Halt scan, alert приват `1501232696`, не повтор |
| 429 | будь-який | Sleep `Retry-After`, skip cycle |
| 5xx other | будь-який | Halt, alert ops |
| **schema_version mismatch** (per-endpoint) | див. таблицю нижче | Halt, alert, не публікуй ТІЛЬКИ якщо expected version не відповідає |

### schema_version expected per endpoint (ADR-0059 v2 changelog 2026-05-04)

| Endpoint | Expected schema_version |
|---|---|
| `/macro/context` | `v3.0` |
| `/signals/latest` | `v3.0` |
| `/bias/latest` | `v3.0` |
| `/narrative/snapshot` | `v3.0` |
| `/smc/levels` | `v3.1` (ADR-0059 raw-data analysis) |
| `/smc/zones` | `v3.1` (ADR-0059 raw-data analysis) |
| `/bars/window` | `v3.1` (ADR-0059 raw-data analysis) |
| `/cowork/recent_thesis` | `v3.0` (ADR-001 cowork memory) |
| `/cowork/published` | `v3.0` (ADR-001 cowork memory) |

Cowork повинен трактувати `schema_version` як **per-endpoint** значення, не
глобальне. Halt тільки якщо endpoint X повернув version що НЕ відповідає
expected для X.

---

## СТЕП 8 — Logging

`outputs/scan_log.jsonl` (append):

```json
{"ts":"<utc>","scan_id":"<id>","event":"scan_start","prompt_version":"v3.0"}
{"ts":"...","scan_id":"<id>","event":"token_loaded","present":true}
{"ts":"...","scan_id":"<id>","event":"fetch","endpoint":"/macro/context","status":200,"schema":"v3.0","duration_ms":120}
{"ts":"...","scan_id":"<id>","event":"fetch","endpoint":"/smc/zones","status":200,"schema":"v3.1","duration_ms":85}
{"ts":"...","scan_id":"<id>","event":"fetch","endpoint":"/smc/levels","status":404,"fallback":"file_mount"}
{"ts":"...","scan_id":"<id>","event":"prior_context","count":2,"used":true}
{"ts":"...","scan_id":"<id>","event":"thinking","budget":6000,"used":4823,"model":"claude-opus-4.7"}
{"ts":"...","scan_id":"<id>","event":"checklist","passed":12,"of":14,"verdict":"side_note"}
{"ts":"...","scan_id":"<id>","event":"watchdog","triggered":[],"verdict":"clean"}
{"ts":"...","scan_id":"<id>","event":"guard","passed":true}
{"ts":"...","scan_id":"<id>","event":"publish","kind":"v3_thesis","message_id":150}
{"ts":"...","scan_id":"<id>","event":"cowork_post","status":200,"appended":true,"scan_id":"<id>"}
{"ts":"...","scan_id":"<id>","event":"scan_complete","published":1}
```

X-API-Key, TG_BOT_TOKEN — **ніколи** не пиши в логи.

---

## ENVIRONMENT / DEPLOYMENT NOTES

- **Channel**: `-1003705152888` (@smc_v3, "v3 SMC")
- **Ops alerts private**: `1501232696` (Стас)
- **API base URL**: `https://aione-smc.com/api/v3`
- **schema_version**: per-endpoint (див. таблицю у СТЕП 7)
- **Prompt version**: v3.0 (ADR-0059 §5.5 + mentor-grade upgrade + cowork memory R9/R10)
- **Token rotation**: ops issues new token via `tools/api_v3/issue_token.py` →
  updates `.env` → cowork picks up next scan
- **Companion**:
  - `cowork_prompt_template_v3.md` — constitutional fence (LLM behavior contract)
  - `cowork_methodology.md` — deep DT-Канон/IOFED reference
  - `cowork_prompt_validation.md` — 5-scenario gate
  - `cowork_consumer_quickstart.md` — how to consume thesis stream

---

## RE-VALIDATION TRIGGERS

- Calendar reminder раз на місяць
- Будь-яка зміна CORE BEHAVIOR (`cowork_prompt_template_v3.md`) → re-run 5/5
  gate з `cowork_prompt_validation.md`
- M3 hallucination >10% за 3 дні → rollback на попередню версію prompt template

---

## COST AWARENESS (v3 specific)

v3 використовує extended thinking (6-8K Opus / 3-4K Sonnet) per cycle.
Орієнтовний bill:

- Opus 4.7 з 8K thinking: ~$0.50-1.00 per scan cycle
- Sonnet 4.6 з 4K thinking: ~$0.10-0.20 per scan cycle
- 4-6 runs/day (cadence S1-S4 + ad-hoc events) = $2-6/day Opus OR $0.40-1.20/day Sonnet

**Recommendation**: default Sonnet 4.6, escalate to Opus тільки на pivotal events
(NFP/CPI/FOMC days або A+ POI proximity з high-impact news у window). Логіка
ескалації — частина scan setup, не CORE BEHAVIOR.

---

*Source: ADR-0059 (Proposed) · slice 059.5c + v3 mentor-grade upgrade ·
operational frame extracted from Cowork Trade Setup Scanner v3.0 task body
(Claude Desktop scheduled task), reconciled into platform SSOT structure
2026-05-07.*
