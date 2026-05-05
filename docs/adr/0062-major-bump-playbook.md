# ADR-0062 — Major Bump Playbook (Reusable Template)

- **Status**: Accepted
- **Date**: 2026-05-05
- **Authors**: vikto + Copilot
- **Initiative**: `dependency_governance_v1`
- **Related**: [ADR-0060](0060-deploy-discipline-vps-catchup.md) (Deploy Discipline), [ADR-0061](0061-vps-reconciliation-2026-05-05.md) (Reconciliation)

---

## Quality Axes

- **Ambition target**: R3 — створює reusable governance template, який знімає ad-hoc прийняття рішень для кожного major bump.
- **Maturity impact**: M3 (consolidates dependency hygiene; precondition для безпечного M3→M4).

---

## 1. Контекст

Dependabot регулярно відкриває PRs на оновлення залежностей. Серед них:

- **Patch / minor bumps** — низький blast radius, можна merge після CI.
- **Major bumps** — можуть ламати API, runtime поведінку, перформанс, типи, sandbox semantics.

Поточний стан (станом на 2026-05-05): є ~6 відкритих Dependabot PRs, серед них кілька **major** (TypeScript 5→6, vite 6→8, svelte-vite-plugin 5→7, numpy 1→2, pandas 2→3, uuid 11→13). Потенційно **redis 5→7** (deferred — окрема інфраструктурна migration).

**Проблема**: без reusable template кожен major bump = ad-hoc дискусія "merge / wait / rollback" → втрачаємо час, плодимо неузгоджені рішення, ризикуємо регресії.

**Рішення**: один **reusable playbook** (ця ADR), який кожна наступна major bump ADR (0063, 0064, ...) використовує як **template + checklist**, прописуючи лише deltas.

---

## 2. Альтернативи

| # | Підхід | Pros | Cons | Verdict |
|---|---|---|---|---|
| **A. Reusable playbook ADR (вибрано)** | Одна ADR з універсальним 7-step process; кожна bump-ADR посилається + заповнює деталі | DRY, governance scales, нові інженери орієнтуються за template | Потрібна формальна дисципліна слідувати playbook | ✅ |
| B. Inline у кожній bump ADR | Кожна ADR — самодостатня | Контекст self-contained | Drift template, copy-paste hell, governance regression | ❌ |
| C. Auto-merge усіх Dependabot | Найшвидше | I7 violation: dependency hijack risk, breaking changes silent | ❌ |
| D. Заборонити major bumps | Найбезпечніше | Stale deps → CVE risk, FOMO на покращення | ❌ |

**Decision**: A — reusable template + per-bump ADR (R-instance).

---

## 3. Reusable Playbook (7 кроків)

> Кожна major bump ADR (R-instance) ОБОВ'ЯЗКОВО проходить ці 7 кроків і документує результати.

### Step 1 — RECON: blast radius assessment

Заповнити в R-instance ADR:

- **Поточна / нова версія**: `<from> → <to>`
- **Changelog link**: офіційний CHANGELOG / migration guide
- **Breaking changes**: список з changelog (verbatim, з посиланням на section)
- **Usages у коді**:
  - `grep -r "<package>" --include="*.{py,ts,svelte}" | wc -l` → кількість файлів
  - `grep -r "<api_that_changed>" ...` → конкретні зачеплені API
- **Transitive impact**: peer deps, що тягнуться разом
- **Severity classification** (S0–S3 з copilot-instructions.md)

### Step 2 — DESIGN: migration plan

- **Strategy**: in-place bump / staged migration / feature flag / rollback plan
- **Code changes required** (якщо є): мінімальний diff
- **Test coverage**: чи покривають існуючі тести зачеплені API?
- **Alternatives ≥2** (як завжди для ADR)
- **Quality Axes**: Ambition R-target + Maturity impact

### Step 3 — SAFETY NET: pre-bump backup

- Branch: `dep/<package>-<from>-to-<to>-bump`
- Lock file backup: `cp package-lock.json package-lock.json.bak.<ts>` (або `pip freeze > requirements.lock.bak.<ts>`)
- Git commit poin перед bump (rollback marker)

### Step 4 — APPLY: minimum-viable change

- One bump per slice (не chain'ити major bumps)
- Один файл (`package.json` / `requirements.txt`) + lock file
- Запустити `npm install` / `pip install -U <pkg>`
- Перевірити, що lock file має очікувану версію

### Step 5 — VERIFY: 4-tier check

| Tier | Перевірка | Pass criteria |
|---|---|---|
| **5.1 Static** | `tsc --noEmit` (TS), `svelte-check` (Svelte), `mypy`/lint (Python) | 0 нових errors |
| **5.2 Tests** | `npm test` / `pytest tests/` | All pass |
| **5.3 Build** | `npm run build` / `python -m tools.run_exit_gates` | Build succeeds, exit gates green |
| **5.4 Smoke** | Manual UI check / API smoke endpoint | Critical user flow works |

Якщо **будь-який tier FAIL** — переходимо до Step 7 (Rollback).

### Step 6 — POST: documentation + cleanup

- R-instance ADR: переводимо в `Status: Implemented`
- Update `docs/adr/index.md`
- Видалити lock backup (`.bak.<ts>`)
- Якщо API мігрував — оновити touched code comments / docs
- Repo memory `/memories/repo/dep-bumps.md` — додати entry

### Step 7 — ROLLBACK (if any tier failed)

```bash
# npm side
git checkout package.json package-lock.json
npm ci

# pip side
git checkout requirements.txt
pip install -r requirements.txt --force-reinstall

# Update R-instance ADR status to "Rejected" + reason
```

---

## 4. Acceptance Criteria для R-instance ADR

R-instance ADR (наприклад ADR-0063 для TS 6) **ОБОВ'ЯЗКОВО** містить:

| Section | Зміст |
|---|---|
| **Header** | Status, Date, link на ADR-0062 (`Uses playbook: ADR-0062`) |
| **Quality Axes** | R-target + M-impact |
| **§1 RECON** | Кроки 1.1–1.5 з деталями |
| **§2 DESIGN** | Strategy + alternatives ≥2 |
| **§3 EXECUTION** | Кроки 3–5 з verbatim командами та результатами |
| **§4 VERIFICATION** | 4-tier table з pass/fail для кожного |
| **§5 ROLLBACK** | Команди (готові, протестовані) |
| **§6 EVIDENCE** | `[VERIFIED terminal]` маркери для кожного критичного кроку |

---

## 5. Класифікація Dependabot PRs

Не всі bumps — major. Швидкий decision tree:

| Тип bump | Process | ADR? |
|---|---|---|
| **Patch** (5.7.0 → 5.7.1) | CI → merge | Ні |
| **Minor** (5.7.0 → 5.8.0) | CI + smoke → merge | Ні |
| **Major з clean changelog** (no breaking, ecosystem standard) | playbook lite (Steps 1, 4, 5, 6) | Optional changelog entry |
| **Major з breaking changes** | full playbook (Steps 1–7) | **Так** (R-instance ADR) |
| **Infrastructure migration** (Redis 5→7, Postgres) | окрема ADR + initiative | **Так** (не Dependabot scope) |

**Правило великого пальця**: якщо bump потребує code changes у репо — це major з ADR. Якщо тільки `npm install` — playbook lite.

---

## 6. Інваріанти

| ID | Інваріант |
|---|---|
| **DEP-1** | One bump per PR/slice. Не chain'ити (TS 6 + vite 8 окремо, не разом) |
| **DEP-2** | Lock file завжди commit'иться разом з manifest |
| **DEP-3** | Rollback path має бути готовий **до** apply (не після) |
| **DEP-4** | Major bump без R-instance ADR — заборонено (auto-block у CI, майбутнє) |
| **DEP-5** | Production deploy major bump — тільки після прохождення local + VPS staging (ADR-0060 Phase 2) |

---

## 7. Cross-References

- [ADR-0060](0060-deploy-discipline-vps-catchup.md) — Phase 2 drift detector гарантує, що bump застосований і там і там
- [ADR-0061](0061-vps-reconciliation-2026-05-05.md) — приклад того, що буває коли deploy discipline відсутня
- [`/memories/repo/dep-bumps.md`](../../) — running log of past bumps + lessons

---

## 8. Перші заплановані instances

| ADR | Bump | Очікуваний blast radius |
|---|---|---|
| **0063** | TypeScript 5.7 → 6.0.2 | Low (типи); найперший instance, тестує playbook |
| 0064 | vite 6 → 8 + svelte-plugin-svelte 5 → 7 (paired) | Medium (build chain) |
| 0065 | numpy 1 → 2 | High (всі pandas/SMC numeric paths) |
| 0066 | pandas 2 → 3 | High (depends on numpy 2 first) |
| (none) | uuid 11 → 13 | playbook lite — sm grep usages |
| (none) | lightweight-charts 5.0.0 → 5.1.0 | minor |
| (none) | python-binance, python-dotenv | minor |
| (deferred) | Redis 5 → 7 | infrastructure, окрема ADR з owner approval |

---

## 9. Acceptance / Sign-off

**Owner**: vikto. **Sign-off date**: 2026-05-05.

Цей playbook стає чинним після merge у `main`. Перший instance — ADR-0063 (TS 6).
