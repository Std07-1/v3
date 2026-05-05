# ADR-0063 — TypeScript 5.7 → 6.0.2 Bump (First Major Bump Instance)

- **Status**: Implemented
- **Date**: 2026-05-05
- **Authors**: vikto + Copilot
- **Initiative**: `dependency_governance_v1`
- **Uses playbook**: [ADR-0062](0062-major-bump-playbook.md)

---

## Quality Axes

- **Ambition target**: R3 — first instance тестує playbook end-to-end на найменш ризикованому major bump.
- **Maturity impact**: M3 (consolidates dep hygiene; не змінює архітектуру).

---

## §1 RECON (per ADR-0062 Step 1)

### 1.1 Версії

- **From**: `typescript: ^5.7.0` (resolved → `5.9.3`)
- **To**: `typescript: ^6.0.2`
- **Manifest**: `ui_v4/package.json` (devDependencies)

### 1.2 Changelog

- Official: https://devblogs.microsoft.com/typescript/announcing-typescript-6-0/
- Migration guide: TypeScript Release Notes 6.0
- **Verify before commit**: cross-reference з реальним 6.0.2 release notes

### 1.3 Breaking changes (high-level)

> Заповнюємо verbatim з release notes. На момент написання ADR — список ризиків базується на typical TS major bump pattern:

| # | Категорія | Опис | Ймовірний impact на проект |
|---|---|---|---|
| B1 | Stricter inference | `noImplicitAny` стає тихіше, але інші inference чекери жорсткіші | Low — у нас вже `strict: true` |
| B2 | Lib types | `lib.dom.d.ts` оновлення (DOM API типи) | Low — UI працює з canvas + WS, мало DOM типів |
| B3 | Module resolution | Default `moduleResolution` може змінитися | Low — у нас явно через `extends @tsconfig/svelte` |
| B4 | Deprecated APIs | Старі `tsc` flags видалені | Low — використовуємо мінімум |
| B5 | Svelte compatibility | `svelte-check@4.4.3` потребує сумісності з TS 6 | **Verify** — частий блокер при TS major bumps |

**Risk assessment**: **Low–Medium**. Найбільший ризик — `svelte-check` сумісність.

### 1.4 Usage у коді

```text
TS/Svelte файлів:        41 (ui_v4/src + vite.config.ts)
tsconfig:                strict + ESNext + isolatedModules (max-modern)
type-only imports:       присутні (ESNext-style, OK для TS 6)
decorators / metadata:   немає
namespaces:              немає
```

### 1.5 Transitive impact

- `svelte-check@^4.0.0` — peer на TypeScript; **необхідно перевірити чи 4.x підтримує TS 6**
- `@tsconfig/svelte@^5.0.4` — extends base config; перевірити що він не зламається на TS 6

### 1.6 Severity classification

**S2** (operational drift). Не S0 — нема data corruption / runtime crash risk. Не S1 — є rollback path. CI впіймає більшість регресій до production.

---

## §2 DESIGN (per ADR-0062 Step 2)

### 2.1 Strategy: in-place caret bump

```diff
-"typescript": "^5.7.0",
+"typescript": "^6.0.2",
```

Без feature flag (TypeScript = build-time, не runtime). Без staged migration (всі файли компілюються одночасно).

### 2.2 Альтернативи

| # | Підхід | Pros | Cons | Verdict |
|---|---|---|---|---|
| **A. In-place caret bump (вибрано)** | Один-line зміна в package.json + npm install | Мінімально invasive, швидкий rollback | Якщо svelte-check ламається → блокер | ✅ |
| B. Pin exact `6.0.2` | Передбачувано, нема "drift" від caret | Втрачаємо patch updates автоматично | ❌ (інтегруємо з Dependabot weekly model) |
| C. Wait for TS 6.1+ | Більше часу для exact ecosystem catch-up | FOMO; deferral pattern → infinite delay | ❌ |
| D. Skip TS 6, jump to 6.x latest | Найновіше | Більший diff; виходить за scope playbook test | ❌ |

### 2.3 Test coverage

- `svelte-check` — full type-check всіх 41 файлів
- `vitest` тести — є для UI логіки (`ui_v4/src/**/*.test.ts`)
- `npm run build` — Vite production build

### 2.4 Rollback plan

```powershell
cd ui_v4
git checkout package.json package-lock.json
npm ci
```

Час rollback: ~30 сек. Готово до Step 4.

---

## §3 EXECUTION (per ADR-0062 Steps 3–4)

> Заповнюється під час реального apply. Verbatim команди + результати.

### 3.1 Backup (Step 3)

```powershell
cd c:\Users\vikto\aione-context\v3\ui_v4
Copy-Item package-lock.json package-lock.json.bak.20260505
git status  # confirm clean working tree
```

### 3.2 Apply (Step 4)

```powershell
# Edit package.json devDependencies.typescript: ^5.7.0 → ^6.0.2
npm install
npm ls typescript  # verify version
```

---

## §4 VERIFICATION (per ADR-0062 Step 5)

> Виконано 2026-05-05.

| Tier | Команда | Результат | Pass/Fail |
|---|---|---|---|
| **5.1 Static** | `npm run typecheck` (svelte-check 4.4.3) | 1 error + 1 warning у 2 файлах. **A/B test (git stash → TS 5.9.3)**: ті самі 1 error + 1 warning **pre-existing**, не TS 6 регресія | **PASS** (no new errors) |
| **5.2 Tests** | `npm test` (vitest) | 28/28 pass у 2 файлах (`shellState`, `smcStore`), 704ms | **PASS** |
| **5.3 Build** | `npm run build` | 174 modules → `dist/index.html 1.63kB`, `index-Cku4wQ2g.css 28.38kB`, `index-DDg_5hnv.js 319.77kB`, 1.89s | **PASS** |
| **5.4 Smoke** | manual UI load (local startup 2026-05-05 evening) | UI завантажилась, графіки рендеряться, без runtime errors | **PASS** |

**Acceptance**: 4/4 tiers PASS. Status → `Implemented`.

### 4.1 Pre-existing issues, що НЕ блокують bump

- **`engine.ts:138` `devicePixelRatio`** — option не приймається `TimeChartOptions` (lightweight-charts 5.0.0 typings); присутнє і у TS 5.9.3. Tracked окремо для майбутньої очистки.
- **`DiagPanel.svelte:54` a11y warning** — non-interactive `<div>` з event listeners; також pre-existing.

---

## §5 ROLLBACK (готово до використання)

```powershell
cd c:\Users\vikto\aione-context\v3\ui_v4
git checkout package.json package-lock.json
Remove-Item node_modules -Recurse -Force
npm ci
npm run typecheck  # verify rollback restored TS 5.9.3 working state
```

---

## §6 EVIDENCE

- `[VERIFIED terminal]` — `npm ls typescript` пре-bump = `typescript@5.9.3` (resolved через `^5.7.0` caret)
- `[VERIFIED terminal]` — `npm ls typescript` пост-bump = `typescript@6.0.3` (через `^6.0.2` caret) + `svelte-check@4.4.3` deduped to `typescript@6.0.3` → **svelte-check 4.4.3 сумісний з TS 6** (open question §8 → ВИРІШЕНО)
- `[VERIFIED terminal]` — `npm install` додав 35 transitive packages; 9 vulnerabilities (7 mod, 2 high) — окремий audit (не блокує bump)
- `[VERIFIED terminal]` — A/B test (git stash → TS 5.9.3 → typecheck → той самий 1 error + 1 warning) = **error pre-existing, НЕ TS 6 регресія**
- `[VERIFIED terminal]` — `npm test`: 28 tests pass у 704ms
- `[VERIFIED terminal]` — `npm run build`: 174 modules → dist/, 1.89s, no build errors
- `[VERIFIED terminal]` — `@tsconfig/svelte` не потребує оновлення (open question §8 → ВИРІШЕНО, нема deprecation warnings)

---

## §7 Cross-References

- [ADR-0062](0062-major-bump-playbook.md) — playbook (parent)
- [ADR-0061](0061-vps-reconciliation-2026-05-05.md) — deploy discipline context
- TypeScript 6 release notes — додамо URL після verification

---

## §8 Open Questions (RESOLVED)

- ✅ `svelte-check@4.4.3` **сумісний** з TS 6.0.3 (deduped, без warnings)
- ✅ `@tsconfig/svelte` upgrade **не потрібен** (нема deprecation warnings)
- ⚠ 9 npm vulnerabilities (7 mod, 2 high) — окремо обробити (можливо ще одна ADR або lite playbook через Dependabot)
- ⚠ Pre-existing `devicePixelRatio` error в `engine.ts` — окремий fix (не входить у scope цієї bump ADR)

---

## §9 Sign-off

**Owner**: vikto. **Apply target**: 2026-05-05. **Status update**: після §4 verification.
