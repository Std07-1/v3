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

# Інший конфіг або каталог даних
python -m aione_top -c config.json --data-root data_v3
```

## Сторінки

### Page 1 — Overview  `[Tab]`

| Панель | Опис |
|--------|------|
| **Header** | CPU / Memory / Uptime + v3 summary (процеси, дублікати, derive chain) |
| **Processes** | Таблиця v3-процесів: PID, Role, CPU%, RSS, Threads, Uptime, Status |
| **Components** | Redis / UI :8089 / Pidfiles — стан підсистем |

### Page 2 — Pipeline  `[Tab]`

| Панель | Опис |
|--------|------|
| **Bootstrap & Writer** | Boot ID, prime readiness, primed totals (загальні + per-TF), writer status |
| **Primed Bars + Freshness** | Об'єднана сітка symbol × TF: кількість барів (Redis) + freshness age (disk) |

### Page 3 — Events  `[Tab]`

| Панель | Опис |
|--------|------|
| **Recent Events** | Останні WARNING/ERROR + ключові події (BOOTSTRAP, PRIME, GAP, RECONNECT тощо) з `logs/*.log` |

## Гарячі клавіші

| Клавіша | Дія |
|---------|-----|
| `Tab` | Переключити сторінку (1→2→3→1) |
| `k` | Режим Kill → `1-9` by PID# / `d` duplicates / `a` all v3 |
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
| `collect_ui_health` | 10с | HTTP GET /api/status |
| `collect_pidfiles` | 15с | logs/*.pid |
| `collect_pipeline_data` | 10с | Redis prime:ready + status:snapshot |
| `collect_log_tail` | 5с | logs/*.log (tail parse) |

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

- **v0.6** — 3 сторінки, combined grid, freshness lag fix, покращений bootstrap panel
- **v0.5** — Page 2 (Pipeline): bootstrap, bars grid, log tail
- **v0.4** — Інтерактивний режим: kill/cache/refresh/pause
- **v0.3** — CPU optimization, TTL-кеші
- **v0.2** — Покращений display
- **v0.1** — Базовий моніторинг
