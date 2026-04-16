# ADR Impact Map Skill

**Призначення**: побудувати blast radius map для проектованої зміни ПЕРЕД ADR creation.
**Коли викликати**: будь-який R_ARCHITECT prompt; перед ADR draft.

## Протокол

### Step 1 — Identify scope axes

Зміна торкається яких осей:
- [ ] **Data axis**: schema, wire format, storage layout
- [ ] **Compute axis**: algorithm, performance budget, scheduling
- [ ] **Process axis**: supervisor mode, lifecycle, deploy
- [ ] **Contract axis**: API, WS protocol, type definitions
- [ ] **UI axis**: rendering, interaction, visual spec
- [ ] **Governance axis**: invariants, role specs, instructions

### Step 2 — Identify affected modules

Для кожної осі — які модулі точно/ймовірно зміняться:

```
Data:    core/contracts/X.py, runtime/store/uds.py, ui_v4/src/types.ts
Compute: core/smc/Y.py, runtime/smc/smc_runner.py
Process: app/main.py, supervisor config
...
```

### Step 3 — Identify affected ADRs

Які існуючі ADR ця зміна:
- **Реалізує** (extends)
- **Уточнює** (clarifies)
- **Заміщує** (supersedes)
- **Конфліктує** (conflicts with — потрібен resolution)

### Step 4 — Identify affected invariants

| Invariant | Affected? | How |
|-----------|-----------|-----|
| I0 Dependency Rule | Y/N | ... |
| I1 UDS narrow waist | Y/N | ... |
| I2 Time geometry | Y/N | ... |
| I3 Final > Preview | Y/N | ... |
| I4 Single update stream | Y/N | ... |
| I5 Degraded-but-loud | Y/N | ... |
| I6 Stop-rule | — | (meta) |
| I7 Autonomy-First (trader-v3) | Y/N | ... |

Якщо торкає інваріант → ADR обов'язковий, не optional patch.

### Step 5 — Identify affected stop-list (X1-X32)

Особлива увага:
- X13 (bar.low) при роботі з CandleBar
- X28 (UI re-derive) при ui_v4 зміні
- X29-X32 при trader-v3
- X31 (cross-repo) — чи зачіпає обидва репо?

### Step 6 — Cross-repo check

| Зміна у | Потенційний impact на |
|---------|----------------------|
| v3 core/ | trader-v3 (consumer) |
| v3 wire format | trader-v3 platform.py + ui_v4 types.ts |
| v3 SMC | trader-v3 thesis state, ui_v4 rendering |
| trader-v3 prompt | (NONE — boundary) |
| trader-v3 platform IPC | потребує v3 platform ADR (приклад: ADR-0049 + 039) |

### Step 7 — Rollback plan

Кожен ADR має rollback path:
- Які commits revert?
- Які data міграції потрібні (forward / backward)?
- Який is window для rollback decision (24h / 1 week)?

### Step 8 — Verify gates plan

Які тести / gates потрібні щоб довести implementation:
- Unit tests (which file)
- Integration tests
- Exit gates (which AST gate потрібен новий)
- Manual verification (curl, screenshot, etc.)

## Output format

```markdown
# Impact Map: <ADR title draft>

## Scope axes
- ☑ Data
- ☑ Contract
- ☐ Compute
- ☑ UI

## Affected modules
- core/X.py (NEW field)
- runtime/Y.py (consume new field)
- ui_v4/src/types.ts (mirror)

## Affected ADRs
- Extends: 0024 (SMC)
- Conflicts with: 0029 (Grade) — need resolution in §X

## Affected invariants
- I3: ⚠️ — нова preview path, треба гарантувати NoMix
- All others: clean

## Stop-list risks
- X28 risk: UI може почати re-derive нового field — explicit guard потрібен

## Cross-repo
- trader-v3 thesis state може використати — окремий v3-bot ADR plan'ується

## Rollback
- Revert commit X + clear Redis ключі `v3_local:newfeature:*`

## Verify gates
- New test: tests/test_adr00NN_newfield.py
- Update exit_gate: tools/exit_gates/gates/gate_field_compat.py
```

## Правило використання

- **R_ARCHITECT**: ОБОВ'ЯЗКОВО перед ADR draft
- **R_PATCH_MASTER**: при будь-якій зміні >50 LOC або зачіпає invariant
- **R_REJECTOR**: перевіряє чи impact map зроблено для нових ADR
