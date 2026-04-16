# SSOT Audit Skill

**Призначення**: швидко перевірити чи зміна не створює split-brain або duplicate source of truth.
**Коли викликати**: при додаванні нової utility/config/contract/route.

## Канонічні SSOT точки v3

| Що | Де SSOT | Anti-pattern |
|----|---------|--------------|
| Config / policy | `config.json` | Hardcoded values у коді |
| Contracts / types | `core/contracts/`, `core/model/bars.py` | Дублювання у UI/runtime |
| Anchor routing | `core/buckets.py:resolve_anchor_offset_ms()` | Inline `if tf_s >= 14400` у N місцях |
| Derive chain | `core/derive.py:DERIVE_CHAIN` | Другий dict з правилами |
| OHLCV storage | `runtime/store/uds.py` | Прямий Redis/disk write |
| FINAL_SOURCES | `core/model/bars.py` | Hardcode strings "stream"/"polled" |
| SMC algorithms | `core/smc/` (pure) + `config.json:smc` | Hardcoded thresholds, I/O в core/smc |
| SMC wire format | `core/smc/types.py` → `ui_v4/src/lib/types.ts` | Другий формат для UI |
| TF allowlist | `config.json:tf_allowlist_s` | Hardcoded list у коді |
| Symbol allowlist | `config.json:symbols` | Hardcoded list |
| LWC Overlay Render Rule | `OverlayRenderer.ts` header + ADR-0024 §18.7 | Sync render with range/zoom |
| Level Render Rules | `OverlayRenderer.ts:renderLevels()` + ADR-0026 | Full-width lines, hidden labels |
| Zone Render Rules | `OverlayRenderer.ts:renderZones()` + ADR-0024c | Render without grade |
| CandleBar fields | `core/model/bars.py:CandleBar` → `.o .h .low .c .v` | `bar.l` замість `bar.low` (X13) |
| Арчі autonomy | `trader-v3/docs/adr/ADR-024` | Hidden hard block for Арчі (X29/X30) |

## Rule of 3

Якщо routing/check з'являється у **≥3 місцях** → SSOT violation in progress.
Дія: централізувати у один `_resolve_*()` / config key / SSOT module.

## Audit checklist

### 1. Чи додав я новий SSOT?
- Якщо так → задокументуй у `.github/copilot-instructions.md` §SSOT таблиці
- Не залишай unsigned

### 2. Чи дублюю існуючий SSOT?
- Grep по аналогічним value/string/threshold
- Якщо знайшов >1 entry — вже duplicate, треба refactor

### 3. Чи hardcoded value поза config.json?
- Магічні числа у коді = config drift
- Винятки: канонічні константи (`MS_PER_DAY = 86400000`)

### 4. Чи UI має другий formaт даних?
- `ui_v4/src/lib/types.ts` має точно mirror'ити `core/smc/types.py`
- Якщо field name diverge → contract violation

### 5. Чи backend rule є у UI коді?
- X28: frontend re-derives label/grade/bias/phase = ЗАБОРОНЕНО
- Backend `value` → UI рендерить як є

### 6. Чи changelog/docs відображають новий SSOT?
- AGENTS.md → "Key Files to Know" якщо це новий core file
- copilot-instructions.md SSOT table якщо це новий канонічний source

## Output format

```markdown
## SSOT Audit
- New SSOT introduced: <name> at <path> — DOCUMENTED ✅
- Existing SSOT touched: <list>
- Duplications found: <none | list with paths:lines>
- Hardcoded values: <none | list>
- UI/backend types parity: ✅ | ❌ <details>

VERDICT: CLEAN / DRIFT_DETECTED
ACTION: <if drift>
```

## Правило використання

- **R_PATCH_MASTER**: у DESIGN gate перед CUT
- **R_BUG_HUNTER**: при будь-якому review
- **R_ARCHITECT**: обов'язково при ADR
- **R_DOC_KEEPER**: при sync прогоні
