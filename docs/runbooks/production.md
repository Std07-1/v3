# Runbook: Production (запуск, моніторинг, інциденти)

> **Останнє оновлення**: 2026-02-22  
> **Навігація**: [docs/index.md](../index.md)

---

## Зміст

1. [Порядок старту](#порядок-старту)
2. [Health-check](#health-check)
3. [Systemd units (приклад)](#systemd-units-приклад)
4. [Типові інциденти](#типові-інциденти)
5. [Recovery процедури](#recovery-процедури)
6. [Що НЕ робити](#що-не-робити)

---

## Порядок старту

### Передумови

1. Python 3.7 venv активований (`.venv`)
2. `config.json` налаштований (symbols, redis, calendar)
3. `.env` містить FXCM credentials
4. Redis запущений (`127.0.0.1:6379`, db=1 за замовчуванням)
5. `data_v3/` каталог існує з JSONL файлами для всіх символів/TF

### Одна команда (dev)

```bash
python -m app.main --mode all --stdio pipe
```

### Порядок старту (production, manual)

1. **Redis** — має бути запущений *до* конектора
2. **Connector** (M5+ pipeline):

   ```bash
   python -m app.main --mode connector --stdio files --log-dir logs
   ```

3. **M1 Poller** (M1/M3 pipeline):

   ```bash
   python -m app.main --mode m1_poller --stdio files --log-dir logs
   ```

4. **Tick Publisher** (тік-стрім):

   ```bash
   python -m app.main --mode tick_publisher --stdio files --log-dir logs
   ```

5. **Tick Preview Worker** (агрегація тіків → preview):

   ```bash
   python -m app.main --mode tick_preview --stdio files --log-dir logs
   ```

6. **UI** (чекає `prime_ready` від connector):

   ```bash
   python -m app.main --mode ui --stdio files --log-dir logs
   ```

> **Важливо**: у `--mode all` supervisor стартує всі 5 процесів і чекає `prime_ready` перед запуском UI. У ручному режимі переконайтесь, що connector встиг завершити bootstrap (prime Redis) **до** старту UI.

### Supervisor restart policy (ADR-0003 S2)

Supervisor автоматично перезапускає crashed процеси з exponential backoff:

| Категорія | Процеси | Backoff | Max спроб | Вичерпання |
|-----------|---------|---------|:---------:|------------|
| **critical** | connector, m1_poller | 10s→20s→40s→80s→160s | 5 | **supervisor fail** (kills all) |
| **non_critical** | tick_publisher, tick_preview | 5s→10s→20s→…→120s | 10 | видалено з пулу |
| **essential** | ui, ws_server | 5s→10s→20s→…→120s | 10 | видалено з пулу |

- Restart counter скидається після **10 хвилин** стабільної роботи.
- Non-blocking: інші процеси моніторяться без затримки під час backoff delay.
- Bootstrap error → degraded mode, NOT crash (ADR-0003 S1).

**Діагностика** restart-loop:

```bash
# Шукати в логах
grep -i "SUPERVISOR_RESTART\|SUPERVISOR_EXHAUSTED\|SUPERVISOR_CRITICAL" logs/*.log
```

### Derived rebuild (одноразово після cold start)

```bash
python -m tools.rebuild_derived --all
```

Перевірка: `data_v3/_derived_tail_state.json` має 13 символів.  
Див. [runbooks/coldstart.md](coldstart.md) для деталей.

---

## Моніторинг: aione_top

**Рекомендація**: запускати `python -m aione_top` з кореня проєкту під час діагностики або постійно в окремому терміналі.

**Що дивитись спочатку**:

- **Health at a glance** (Page 1) — один рядок: Processes 6/6, Redis, Prime, UI :8089, WS :8000. Усе зелено = норма.
- **Alerts** (смуга під header на всіх сторінках) — якщо не «All systems nominal», з’явились короткі формулювання проблем (Prime not ready, missing process, Redis/UI/WS DOWN, Freshness stale, Last log ERROR).

**OBS-панель** (Page 2, Pipeline): телеметрія з логів за останній інтервал 60 с — writer_drops (stale/duplicate барів), redis_hit_ratio по TF, uds_geom_fix. Допомагає оцінити «шум» UDS без перегляду сирих логів.

**Знімок при інциденті**:

```bash
python -m aione_top --once --export incident_$(date +%Y%m%d_%H%M).json
```

Збереже повний знімок колекторів у JSON для подальшого аналізу або передачі.

---

## Health-check

### /api/status

```bash
curl -s http://127.0.0.1:8089/api/status | python -m json.tool
```

**Ключові поля для моніторингу**:

| Поле | Очікуване | Проблема якщо |
| --- | --- | --- |
| `status.prime_ready` | `true` | `false` → connector ще bootstrapping |
| `status.prime_ready_payload.ready` | `true` | `false` → неповний prime |
| `status.redis_spec` | `{host, port, db}` | відсутній → Redis disconnect |
| `status.warnings[]` | `[]` | непорожній → degraded |
| `status.disk_bootstrap_reads_total` | `0` (після bootstrap) | зростає → disk hot-path порушено |

### /api/config

```bash
curl -s http://127.0.0.1:8089/api/config | python -m json.tool
```

Перевірити `config_invalid=false`. Якщо `true` — логічна суперечність у policy.

### Redis ключі

```bash
redis-cli -n 1 KEYS "v3_local:ohlcv:*"
redis-cli -n 1 KEYS "v3_local:prime:*"
```

Якщо `ohlcv:*` ключів немає, але `prime:ready` є → stale readiness (див. [redis_snapshot_design.md](../redis_snapshot_design.md)).

---

## Systemd units (приклад)

```ini
# /etc/systemd/system/trading-v3.service
[Unit]
Description=Trading Platform v3 (all processes)
After=redis.service network.target

[Service]
Type=simple
User=trader
WorkingDirectory=/opt/trading-v3
Environment="PATH=/opt/trading-v3/.venv/bin:/usr/bin"
ExecStart=/opt/trading-v3/.venv/bin/python -m app.main --mode all --stdio files --log-dir /var/log/trading-v3
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

### Nginx reverse proxy (приклад)

```nginx
server {
    listen 443 ssl;
    server_name trading.example.com;

    location / {
        proxy_pass http://127.0.0.1:8089;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        # WebSocket (якщо буде)
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}
```

> **UNKNOWN**: наразі система не має auth/TLS/rate-limit. Це заплановано для P6 (production-grade web). Nginx proxy з TLS — мінімальний workaround.

---

## Типові інциденти

### 502/522 — сервер не відповідає

**Симптоми**: браузер показує 502 (nginx) або connection refused (direct).

**Діагностика**:

1. Перевірити процеси: `ps aux | grep python`
2. Перевірити /api/status: `curl http://127.0.0.1:8089/api/status`
3. Перевірити логи: `logs/ui.err.log`, `logs/connector.err.log`
4. Перевірити restart-loop: `grep SUPERVISOR_RESTART logs/*.log`

**Типові причини**:

- UI не стартував (connector ще bootstrapping → `prime_ready=false`)
- Порт зайнятий іншим процесом
- Python crash (check exit code)
- UI видалено з пулу після 10 restart-спроб (SUPERVISOR_EXHAUSTED)

**Рішення**: Перезапустити `python -m app.main --mode all`.

---

### Процес crash-loop (нове з ADR-0003 S2)

**Симптоми**: в логах `SUPERVISOR_RESTART label=tick_publisher ...` з наростаючим delay.

**Діагностика**:

1. `grep SUPERVISOR_RESTART logs/*.log` — побачити який процес і скільки спроб.
2. Перевірити лог відповідного процесу: `logs/<label>.err.log`.
3. Якщо `SUPERVISOR_CRITICAL_EXHAUSTED` — supervisor зупинив все.

**Рішення**:

- Якщо non_critical (tick_publisher/tick_preview): перевірити FXCM WS/Redis підключення, перезапустити supervisor.
- Якщо critical (connector/m1_poller): виправити root cause (corrupt JSONL, FXCM down, Redis down), потім перезапустити.

---

### Redis disconnect

**Симптоми**: `/api/status` показує `redis_spec=null` або `warnings[]` містить `redis_down`.

**Діагностика**:

1. `redis-cli ping` → має повернути `PONG`
2. Перевірити `config.json → redis.host/port/db`
3. Перевірити логи: `REDIS_DOWN code=redis_unavailable`

**Наслідки**:

- Cold-load деградує → `meta.source="degraded"`, `disk_blocked=true`
- Updates polling може показувати stale дані
- System функціонує, але в degraded режимі (loud)

**Рішення**: відновити Redis, перезапустити систему.

---

### history=0 (порожній графік)

**Симптоми**: UI показує порожній графік або "Немає даних".

**Діагностика**:

1. `/api/bars?symbol=XAU/USD&tf_s=300&limit=100` → перевірити `bars[]`, `warnings[]`, `meta`
2. `/api/status` → `prime_ready`, `prime_ready_payload.prime_tail_len_by_tf_s`
3. Redis ключі: `redis-cli -n 1 EXISTS v3_local:ohlcv:tail:XAU_USD:300`

**Типові причини**:

- Redis tail порожній (TTL expired), disk_policy=never → degraded
- Connector ще не bootstrapping (prime_ready=false)
- Новий символ без даних на диску

**Рішення**:

1. Якщо prime_ready=false: дочекатись bootstrap
2. Якщо Redis порожній: перезапустити систему (re-prime)
3. Якщо даних на диску немає: запустити backfill вручну

---

### Status payload too large

**Симптоми**: slow /api/status, великий JSON.

**Рішення**: перевірити `config.json → redis.tail_n_by_tf_s` — зменшити tail для не-критичних TF.

---

### WS stale / polling storm

**Симптоми**: UI робить забагато запитів, навантаження на сервер.

**Діагностика**: логи UI → `POLL_STORM` або `visibility_hidden_polling`.

**Рішення**: оновити UI (app.js має setTimeout + visibility pause). Якщо фонова вкладка — polling призупиняється автоматично.

---

## Recovery процедури

### Cold start / Prime (після повної зупинки)

1. Запустити Redis
2. `python -m tools.rebuild_derived --all` (одноразово)
3. `python -m app.main --mode all --stdio pipe`
4. Дочекатись `prime_ready=true` у `/api/status`
5. Перевірити `/api/bars` для ключових символів/TF

### Scrollback / історія

Disk-дані (JSONL) є SSOT. Якщо Redis втрачено — система re-prime з диску при рестарті. Disk є джерелом для scrollback (explicit запити з `to_open_ms`).

**Scrollback rails (P11)**:

- `disk_policy="explicit"` — диск дозволений тільки для scrollback, bootstrap залишається в 60s вікні.
- `max_steps=6` — максимум 6 scrollback запитів на сесію (reset при switch symbol/TF).
- `cooldown=0.5s` — мінімальний інтервал між scrollback запитами.
- Перевищення → порожній фрейм + warning (degraded-but-loud).

**Що робити**: перезапустити систему.  
**Що НЕ робити**: не чіпати `data_v3/` файли вручну.

### Live Recover (M5 gap)

Автоматичний: connector detect gap → `_live_recover_check()` → dosyl missing bars. Деталі: [runbooks/live_recover.md](live_recover.md).

---

## Що НЕ робити

| Заборонено | Чому | Що замість |
| --- | --- | --- |
| Видаляти `data_v3/` файли вручну | SSOT втрата | `tools/purge_broken_bars.py` для repair |
| Писати в Redis вручну | Порушить watermark/seq | Перезапустити систему |
| Запускати UI без connector bootstrap | Порожній графік | `--mode all` або дочекатись prime_ready |
| Додавати `prefer_redis`/`force_disk` в UI код | Порушення Правила 20.2 | Сервер обирає джерело |
| Міняти `config.json` під час роботи | Частково підтримується (mtime cache), але connector потребує рестарту | Зупинити → змінити → запустити |
