# ADR Archive — Non-Canonical Historical Snapshots

Ця директорія містить **не-канонічні** ADR-артефакти, які зберігаються з історичних
причин, але **не є поточним джерелом правди** для системи.

## Правила

- Файли тут **НЕ** включені в ADR index (`../index.md`).
- Поточні ADR лежать на рівень вище: `docs/adr/*.md`.
- Нічого в `_archive/` не повинно бути referenced з production коду або активних docs.

## Поточні файли

| Файл | Чому тут |
|------|----------|
| `0030-alt-FINAL-plus-patch-plan.md` | Патч-план, а не ADR. Канонічний: `../0030-alt-tf-sovereignty.md` |
| `0034-advanced-market-analysis-tdaa.md` | Снапшот стану ADR-0034 до rollback P2–P6. Канонічний: `../0034-advanced-market-analysis-tda.md` |
| `0035-session-notes-hotfix.md` | Session notes (не ADR). Канонічний ADR: `../0035-sessions-killzones.md` |
