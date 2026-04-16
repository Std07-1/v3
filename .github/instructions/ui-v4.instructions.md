---
applyTo: "ui_v4/**"
---

# ui_v4/ — UI v4 Svelte Frontend

**SSOT**: `.github/copilot-instructions.md` §G + `.github/role_spec_chart_ux_v1.md`.

## Жорсткі правила

### G1 — UI = read-only renderer
- UI **НЕ** реалізує доменну логіку. Показує те що дає backend.
- UI **НЕ** робить кеш OHLCV, **НЕ** читає Redis напряму.
- State merge/dedup = строга policy (`applyUpdates()`), не ad-hoc.

### X28 — No backend re-derive (CRITICAL, recent precedent)
- Frontend **НЕ** перераховує: `label`, `grade`, `bias`, `phase`, `scenario`
- Backend SSOT посилає `value` → UI рендерить "як є"
- Directional coloring (bull=green / bear=red) = OK. Перерахунок domain = ЗАБОРОНЕНО.
- Прецедент: P/D label split-brain (changelog 20260322-005)

### I4 — Один update-потік
- WS delta stream + REST fallback для warmup — всі події через `applyUpdates()`
- Паралельні WS subscriptions для "різних фреймів" заборонено

## Canvas rendering (ADR-0024/0026/0028)

- **DPR correctness**: `canvas.width = cssWidth * DPR`, CSS size окремо
- **RAF throttle**: всі renders у `requestAnimationFrame`, не sync у event handlers
- **No stale Y**: ADR-0024 §18.7 — sync render з range/zoom trigger заборонено (→ stale coord → blurry)
- **LWC Overlay Render Rule**: header comment у `OverlayRenderer.ts` — SSOT

### Levels (ADR-0026 L1-L6)
- Full-width lines заборонені (приховують інші elements)
- Підписи завжди видимі (не обрізати)
- Merge тільки при фізичному overlap (не просто близьких ціні)

### Zones (ADR-0024c Z1-Z10)
- Zone без grade — не рендеримо (display filter, ADR-0028)
- Mitigation по closing body, не по тіні
- Lifecycle обов'язковий: active / tested / mitigated / expired

## Types SSOT

- `ui_v4/src/types.ts` = mirror of `core/smc/types.py`
- Зміна Python types → **обов'язково** пересинхронізувати TS (ADR K4 adjacent contract)
- Structural drift = runtime bugs. Compile-time check = `npx tsc --noEmit`

## Premium product rules (R_CHART_UX)

Перед "done" у UI slice:
1. [ ] Screenshot Audit Table (N1–N12 negative checklist, CA1–CA10 contradiction audit)
2. [ ] WCAG AA contrast на всіх 3 темах
3. [ ] DPR audit: blur test на 1.0/1.5/2.0
4. [ ] Render budget: <4ms per RAF frame
5. [ ] Фази дотримані (STRUCTURAL → TYPOGRAPHY → MODE → INTERACTIONS → MOTION → FINAL QA)

## Build & deploy

- `cd ui_v4 && npm run build` → `dist/`
- Deploy: `scp dist/* aione-vps:/opt/smc-v3/ui_v4/dist/`
- **ОБОВ'ЯЗКОВО** після scp: `ssh aione-vps "sudo chmod 755 /opt/smc-v3/ui_v4/dist/ /opt/smc-v3/ui_v4/dist/assets/"` (SCP permissions trap)
