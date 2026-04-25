# aione-top — TUI-монітор платформи v3

Інтерактивний термінальний моніторинг для trading-платформи v3 (FXCM connector + UI + SMC).

## Запуск

```bash
# Стандартний (live TUI, оновлення кожні 3с)
python -m aione_top

# З іншим інтервалом
python -m aione_top -i 5

# Одноразовий знімок (для діагностики/CI)
python -m aione_top --once

# Експорт знімка в JSON або Prometheus
python -m aione_top --once --export snapshot.json
python -m aione_top --once --export metrics.txt --export-format prometheus

# Вимкнути міні-історію (спарклайни CPU/Mem)
python -m aione_top --no-history

# Інший конфіг або каталог даних
python -m aione_top -c config.json --data-root data_v3
```

**Запускати з кореня проєкту** (де є каталог `logs/`), щоб читати логи та pidfiles.

## Сторінки

### Page 1 — Overview  `[Tab]`

| Панель | Опис |
|--------|------|
| **Header** | CPU / Memory / Uptime + v3 summary; опційно Trend (спарклайни CPU/Mem) |
| **Health at a glance** | Один рядок: Processes 6/6, Redis, Prime, WS :8000, Pidfiles |
| **Alerts** | Смуга проблем: Prime not ready, missing process, Redis/UI/WS DOWN, stale freshness, останній ERROR |
| **Processes** | Таблиця v3-процесів: PID, Role, CPU%, RSS, Threads, Uptime, Status |
| **Components** | Redis / WS :8000 / Pidfiles — стан підсистем |

### Page 2 — Pipeline  `[Tab]`

| Панель | Опис |
|--------|------|
| **Alerts** | Та сама смуга алертів |
| **Bootstrap & Writer** | Boot ID, prime readiness, primed totals (загальні + per-TF), writer status |
| **OBS (last 60s)** | Телеметрія з логів: writer_drops, redis_hit_ratio, uds_geom_fix |
| **Primed Bars + Freshness** | Об'єднана сітка symbol × TF: кількість барів (Redis) + freshness age (disk) |

### Page 3 — Events  `[Tab]`

| Панель | Опис |
|--------|------|
| **Alerts** | Смуга алертів |
| **Alert History** | Ringbuffer змін alert-стану сесії (остання подія перша); прихований якщо порожній |
| **Recent Events** | Останні WARNING/ERROR + ключові події (BOOTSTRAP, PRIME, GAP, RECONNECT тощо) з `logs/*.log` |

## Гарячі клавіші

| Клавіша | Дія |
|---------|-----|
| `Tab` | Переключити сторінку (1→2→3→1) |
| `k` | Режим Kill → `1-9` by PID# / `d` duplicates / `a` all v3 |
| `x` | Режим Restart → `1-9` by # / `a` all v3 |
| `s` | Режим Start → запуск відсутніх ролей |
| `c` | Режим Cache → `r` Redis ns clear / `t` Top cache clear |
| `r` | Force refresh (скинути TTL-кеші) |
| `Space` | Пауза / Продовжити оновлення |
| `q` / `Esc` | Вихід |

## Архітектура

```
aione_top/
├── __main__.py    # python -m aione_top entrypoint
├── __init__.py    # версія
├── app.py         # Main loop, keyboard, page switching, zombie prevention
├── collectors.py  # Збір даних: OS, processes, Redis, disk freshness, UI, pidfiles, pipeline, logs
├── display.py     # Rich TUI рендерер: панелі, таблиці, layout для 3 сторінок
└── actions.py     # Дії: kill processes, clear cache
```

### Колектори (collectors.py)

| Колектор | TTL | Джерело |
|----------|-----|---------|
| `collect_os` | щоцикл | psutil (CPU, Mem, Swap) |
| `collect_processes` | щоцикл | psutil (v3-процеси) |
| `collect_redis` | 10с | Redis INFO + SCAN |
| `collect_disk_freshness` | 10с | disk JSONL (останній бар per sym/TF) |
| `collect_ui_health` | 10с | HTTP GET /api/status (:8000) |
| `collect_ws_health` | 10с | HTTP GET / (:8000, config ws_server) |
| `collect_pidfiles` | 15с | logs/*.pid |
| `collect_pipeline_data` | 10с | Redis prime:ready + status:snapshot |
| `collect_log_tail` | 5с | logs/*.log (tail parse) |
| `collect_obs_from_logs` | 20с | OBS_60S з logs (writer_drops, redis_hit_ratio, uds_geom_fix) |

### Freshness розрахунок

**age = now - close_time** (не від open_time).  
`close_time = open_time_ms + tf_s * 1000`.

Це означає: щойно відкрита М1-свічка показує age ≈ 0 (а не 1 хвилину).

Кольори: 🟢 green ≤ 1.5×TF, 🟡 yellow < 3×TF, 🔴 red ≥ 3×TF.

## Залежності

- `rich` — TUI-рендерінг
- `psutil` — OS/process моніторинг
- `redis` — зв'язок з Redis (з TTL-кешем)
- Python 3.7+

## Zombie Prevention

При старті `aione-top` перевіряє та зупиняє попередні інстанси через:

1. PID-файл `logs/aione_top.pid`
2. Сканування всіх Python-процесів з `aione_top` в cmdline

## Версії

- **v1.0** — Efficiency + observability:
  - Incremental log reader (`_LogTracker`): перший цикл 32KB, подальші ~1-5KB нових байт (30-100× менше I/O)
  - Unified `collect_logs()`: один прохід файлів замість двох окремих колекторів
  - `_freshness_mtime_cache`: пропустити JSON-парс якщо `st_mtime` не змінився
  - Restart counter: виявлення зміни PID по ролі між циклами → лічильник рестартів (колонка `Rst`)
  - Alert history ringbuffer (50 entries): Page 3 показує панель змін алертів сесії
  - `clear_app_cache()` тепер скидає і `_LogTracker` state + buffer
- **v0.9** — Safety + incident-prevention (post Python 3.14 trampoline incident):
  - `_kill_tree()` — `taskkill /F /T` для всіх kill-операцій (трамплін + всі діти одним викликом)
  - Orphan detection: `[ORP!]` в таблиці, alert strip «Orphan worker(s) detected»
  - Redis `m1 cmd queue depth` (`LLEN broker:m1:cmd`) в Bootstrap panel — BLPOP competition detector
  - `BROKER_PROXY_TIMEOUT` + `M1_GAP_DETECTED` → alert strip + log parser
  - SWAP у header (вже збирався, тепер відображається)
  - `ws_server` додано до `get_missing_roles()` (bug fix)
- **v0.8** — Process tree, IO counters, M3+D1 freshness, role colors
- **v0.7** — Health at a glance, Alerts strip, WS :8000, OBS panel, sparklines (--no-history), --export json/prometheus
- **v0.6** — 3 сторінки, combined grid, freshness lag fix, покращений bootstrap panel
- **v0.5** — Page 2 (Pipeline): bootstrap, bars grid, log tail
- **v0.4** — Інтерактивний режим: kill/cache/refresh/pause
- **v0.3** — CPU optimization, TTL-кеші
- **v0.2** — Покращений display
- **v0.1** — Базовий моніторинг
