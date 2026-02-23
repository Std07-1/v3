# ADR-0005: Контракт джерел даних aione_top

> **Статус**: IMPLEMENTED  
> **Дата**: 2026-02-22  
> **Контекст**: aione_top v0.7 — єдина TUI-панель моніторингу платформи v3. Цей ADR фіксує контракт джерел даних (без зміни поведінки runtime).  
> **Навігація**: [docs/index.md](index.md)

---

## 1. Мета

Одна точка правди для операційного погляду: aione_top збирає дані лише з узгоджених джерел. Зміни цих джерел (ключа, формат, endpoints) мають узгоджуватись з цим контрактом.

## 2. Джерела даних (контракт)

| Джерело | Що збирається | Контракт |
|--------|----------------|----------|
| **OS** | CPU%, Mem%, Swap, Uptime | psutil (без змін) |
| **Processes** | v3 Python-процеси за cmdline | Класифікація за модулем (app.main, m1_poller, connector, tick_*, ui_chart_v3, ws_server, aione_top). Очікувані ролі: connector, tick_pub, tick_preview, m1_poller, ui, ws_server |
| **Redis** | INFO, SCAN ключів, prime_ready | Ключі `{namespace}:*`, окремо `{namespace}:prime:ready`. Конфіг: config.json → redis.host, port, db, namespace |
| **Disk freshness** | Останній бар per symbol/TF з JSONL | data_root/{symbol}/tf_{tf_s}/part-*.jsonl, останній рядок — JSON bar з open_time_ms |
| **UI health** | GET /api/status | HTTP 200, JSON з status.prime_ready, status.boot_id, тощо. Порт за замовч. 8089 |
| **WS health** | GET / | HTTP 200. Порт/хост з config.json → ws_server.port (за замовч. 8000) |
| **Pidfiles** | Файли logs/*.pid | Ім’я з імені файлу, PID з вмісту |
| **Pipeline** | prime:ready + status:snapshot | Redis ключі `{ns}:prime:ready`, `{ns}:status:snapshot` — JSON |
| **Log tail** | Останні рядки WARN/ERROR + key events | Файли: logs/*.log або fallback logs/*.err.log, logs/*.out.log. Формат рядка: див. ADR-0004 (два прийнятні формати) |
| **OBS_60S** | Телеметрія з логів | Рядки містяють «OBS_60S » + JSON (writer_drops, redis_hit_ratio, uds_geom_fix). Джерело: runtime Obs60s, логи кожні 60 с |

## 3. Інваріанти

- aione_top **не змінює** runtime: лише читає Redis, HTTP, файли, psutil.
- Очікувані ролі процесів відповідають `app.main --mode all` (6 worker-ролей). Зміна списку ролей платформи потребує оновлення _EXPECTED_ROLES у aione_top.
- Експорт (--export json/prometheus) використовує вже зібрані дані; формат Prometheus — набір gauge-метрик (aione_cpu_percent, aione_processes, aione_prime_ready, aione_redis_up, aione_ui_up, aione_ws_up).

## 4. Файли

- aione_top/collectors.py — колектори та очікувані ролі (display._EXPECTED_ROLES)
- aione_top/display.py — compute_alerts, Health at a glance, OBS panel
- aione_top/app.py — export, history/sparklines
- config.json — redis.*, ws_server.port (опційно host)
