# ADR-0018: SLO Observability + Prometheus Integration

- **Статус**: Proposed
- **Дата**: 2026-02-26
- **Автор**: code-review-audit
- **Initiative**: `observability_v1`

## Контекст і проблема

Система декларує SLO-бюджети (copilot-instructions.md):

- UI cold-load (time-to-first-candle): p95 < 200ms
- `/api/updates` latency: p95 < 50ms
- split-brain events: 0
- silent fallback events: 0

Але **жодного інструменту вимірювання** не існує:

- Немає Prometheus histograms/counters у runtime
- Немає latency logging у WS-server або HTTP API
- `reports/mpv_proof/` містить data correctness proofs, але не performance benchmarks
- `reports/exit_gates/` — 37 прогонів гейтів, жоден не вимірює latency

Без вимірювання SLO — це лише побажання, а не інженерна гарантія.

## Розглянуті варіанти

### A. Мінімальний latency logging (short-term, quick-win)

- Додати `time.monotonic()` timestamps у key paths
- Логувати p50/p95/p99 кожні 60s у stdout
- **Плюси**: ≤50 LOC, працює одразу, zero dependencies
- **Мінуси**: лог-парсинг ≠ dashboards; alerting ручний

### B. Prometheus client integration (long-term, рекомендовано)

- `prometheus_client` (вже є noop-готовність у коді)
- `/metrics` endpoint на окремому порту
- **Плюси**: стандарт індустрії, Grafana dashboards, alerting rules
- **Мінуси**: новий dependency, потрібен Prometheus server для збору

### C. Статус-кво

- **Плюси**: нуль зусиль
- **Мінуси**: SLO залишаються недоведеними; "proof-of-value" неможливий

## Рішення

**Двофазний підхід: A → B**

### Phase 1 — Latency logging (1-2 дні)

Додати вимірювання у три ключові точки:

| Точка | Метрика | Target |
|-------|---------|--------|
| `ui_chart_v3/server.py` → `/api/bars` | `api_bars_latency_ms` | p95 < 200ms |
| `ui_chart_v3/server.py` → `/api/updates` | `api_updates_latency_ms` | p95 < 50ms |
| `runtime/ws/ws_server.py` → broadcast | `ws_broadcast_latency_ms` | p95 < 100ms |

Реалізація:

```python
# core/metrics.py (~40 LOC)
class LatencyTracker:
    """Lightweight p50/p95/p99 tracker, log-only."""
    def __init__(self, name: str, window: int = 1000): ...
    def observe(self, duration_ms: float): ...
    def report(self) -> dict: ...  # {p50, p95, p99, count}
```

Кожні 60s — один лог-рядок з summary. Формат: `[METRICS] api_bars p50=12ms p95=45ms p99=98ms count=1523`.

### Phase 2 — Prometheus (коли Phase 1 доведе/спростує SLO)

1. `prometheus_client` у `requirements.txt`
2. `core/metrics.py` → заміна LatencyTracker на `Histogram`
3. `/metrics` endpoint (порт 9100)
4. Grafana dashboard JSON у `tools/dashboards/`
5. Alerting rules для SLO breach

## Наслідки

### Phase 1

- Новий файл: `core/metrics.py` (~40 LOC)
- Зміни: `ui_chart_v3/server.py` (+5 LOC per endpoint), `runtime/ws/ws_server.py` (+5 LOC)
- Лог-формат: `[METRICS]` prefix для easy grep
- Інваріанти I0–I6 не порушуються (read-only instrumentation)

### Phase 2

- Новий dependency: `prometheus_client`
- Новий endpoint: `/metrics` (порт 9100)
- Зміна `core/metrics.py`: LatencyTracker → Prometheus Histogram

## Rollback

### Phase 1

1. Видалити `core/metrics.py`
2. Прибрати timing calls з server.py / ws_server.py

### Phase 2

1. Повернути LatencyTracker замість Histogram
2. Прибрати `/metrics` endpoint
3. Видалити `prometheus_client` з requirements
