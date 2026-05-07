# Cowork ADR Index

> **Окремий каталог**: cowork-specific рішення живуть тут, не у `docs/adr/`
> (mirror trader-v3 pattern). Platform-level ADR (наприклад додавання cowork
> endpoints до runtime API) — у platform каталозі з reference на cowork ADR.
>
> **Філософія "чому окремий каталог"**: cowork є self-contained subsystem
> з власним lifecycle (Claude Desktop task), власним deploy циклом,
> власною шкалою changes. Платформенні ADR не повинні засмічуватись cowork
> мікрорішеннями (schema additions, prompt tweaks, T2/T3/T4 rollout).

| ADR | Назва | Статус | Дата |
|-----|-------|--------|------|
| [001](ADR-001-cowork-memory-architecture.md) | Cowork Memory Architecture (Hybrid Execution + V3 State SSOT) | Accepted | 2026-05-06 |
| [002](ADR-002-cadence-runner-and-event-watcher.md) | Cadence Runner + Event Watcher + Event-Flag Endpoint (slices cowork.003-005) | Accepted | 2026-05-07 |

---

## Зв'язок з platform ADR

| Platform ADR | Стосується cowork як |
|---|---|
| [ADR-0058](../../../docs/adr/0058-public-readonly-api-auth.md) | Auth surface (X-API-Key) — cowork endpoints успадковують token gate |
| [ADR-0059](../../../docs/adr/0059-public-analysis-api-raw-data.md) | Raw data endpoints — джерело market context для cowork scan |
