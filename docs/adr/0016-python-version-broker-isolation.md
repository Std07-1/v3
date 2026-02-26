# ADR-0016: Python Version Upgrade + Broker Subprocess Isolation

- **Статус**: Proposed
- **Дата**: 2026-02-26
- **Автор**: code-review-audit
- **Initiative**: `platform_modernization`

## Контекст і проблема

Платформа жорстко пінена на Python 3.7 (`pyproject.toml:9`: `requires-python = ">=3.7,<3.8"`).
Python 3.7 досяг EOL у червні 2023 — це 2.5+ роки без security-патчів.

Причина піну — FXCM SDK (`forexconnect==1.6.43` у `requirements.txt:9`), який не підтримує Python 3.8+.
Це тягне за собою старі версії numpy (1.21.6), pandas (1.1.5), і блокує доступ до:

- `match/case` (3.10+)
- `asyncio.TaskGroup` (3.11+)
- Performance improvements у 3.11–3.13
- Сучасні typing features (`Self`, `TypeVarTuple`, `type` statement)
- Security fixes (CVE) для стандартної бібліотеки

## Розглянуті варіанти

### A. Повна міграція на Python 3.11+

- **Плюси**: чистий stack, сучасні залежності, security
- **Мінуси**: `forexconnect` SDK не має 3.11 wheel/build → потрібно або vendor-патч, або ctypes/CFFI wrapper, або заміна брокера
- **Ризик**: Високий; broker integration = критичний шлях, міграція може зайняти тижні

### B. Subprocess-ізоляція broker-модуля (рекомендовано)

- **Плюси**: broker-процес залишається на Py3.7; core/runtime/ui мігрують на 3.11+. Чітка межа = JSON pipe/Redis між процесами. Вже зараз процеси комунікують через Redis pub/sub.
- **Мінуси**: два venv або один conda-env з sub-interpreter; CI ускладнюється (два Python)
- **Ризик**: Середній; архітектурно вже готово (supervisor spawn, JSON контракти)

### C. Статус-кво (нічого не міняти)

- **Плюси**: нуль зусиль
- **Мінуси**: наростаючий tech-debt, security exposure, неможливість використати нові бібліотеки, бар'єр для contributors

## Рішення

**Варіант B** — subprocess-ізоляція broker-модуля:

1. **Broker venv** (`.venv37/`): Python 3.7 + forexconnect + мінімальні залежності (redis, json). Містить лише: `runtime/ingest/connector/`, `runtime/ingest/polling/m1_poller.py`, `runtime/ingest/tick_publisher.py`.
2. **Main venv** (`.venv/`): Python 3.11+ для всього іншого: `core/`, `runtime/store/`, `runtime/ws/`, `ui_chart_v3/`, `ui_v4/`, `tools/`, `aione_top/`.
3. **Межа**: broker-процеси пишуть у Redis (як зараз). Контракти не змінюються. `app/main.py` supervisor стартує broker-процеси з `.venv37/python`.

### Етапи

| Фаза | Що | Estimate |
|------|-----|----------|
| Phase 1 | Створити `.venv37/`, винести broker requirements | 1 день |
| Phase 2 | Мігрувати `.venv/` на 3.11+, оновити numpy/pandas/aiohttp | 2-3 дні |
| Phase 3 | Оновити supervisor spawn: broker → `.venv37/python` | 0.5 дня |
| Phase 4 | CI: dual-python gate | 1 день |

## Наслідки

- `app/main.py`: supervisor стартує broker-процеси з іншим Python executable
- `pyproject.toml`: split на два проєкти або один з extras
- `requirements.txt`: split на `requirements-broker.txt` (Py3.7) + `requirements.txt` (Py3.11+)
- Інваріанти I0–I6 не порушуються (broker вже ізольований через Redis)
- Exit-gates: додати gate "broker process uses .venv37, main uses .venv"

## Rollback

1. Відновити єдиний `.venv/` з Python 3.7
2. Повернути `pyproject.toml` до `requires-python = ">=3.7,<3.8"`
3. Supervisor: прибрати окремий executable для broker
