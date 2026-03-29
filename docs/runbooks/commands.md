# Команди запуску та управління — Trading Platform v3

> Cheat-sheet для локальної розробки (Windows) та VPS (Ubuntu/supervisor).

---

## Локально (Windows PowerShell)

### Чистий старт (вбити все старе)

```powershell
Get-Process python -ErrorAction SilentlyContinue | Stop-Process -Force
Remove-Item logs\supervisor_*.pid -ErrorAction SilentlyContinue
```

### Запуск 6 процесів (кожен у окремому вікні)

**Вікно 1 — M1 poller** (broker_sidecar + m1_ingestion + derive):
```powershell
cd c:\Users\vikto\aione-context\v3
.venv\Scripts\python.exe -u -m app.main --mode m1_poller --stdio pipe
або --stdio files --log-dir logs
```

**Вікно 2 — Tick publisher** (FXCM live ticks → Redis):
```powershell
cd c:\Users\vikto\aione-context\v3
.venv\Scripts\python.exe -u -m app.main --mode tick_publisher --stdio pipe
або --stdio files --log-dir logs
```

**Вікно 3 — Tick preview** (ticks → preview bars):
```powershell
cd c:\Users\vikto\aione-context\v3
.venv\Scripts\python.exe -u -m app.main --mode tick_preview --stdio pipe
або --stdio files --log-dir logs
```

**Вікно 4 — Binance ingest** (BTCUSDT/ETHUSDT M1 + backfill):
```powershell
cd c:\Users\vikto\aione-context\v3
.venv\Scripts\python.exe -u -m app.main --mode binance_ingest_worker --stdio pipe
або --stdio files --log-dir logs
```

**Вікно 5 — Binance tick publisher** (Binance live ticks → Redis):
```powershell
cd c:\Users\vikto\aione-context\v3
.venv\Scripts\python.exe -u -m app.main --mode binance_tick_publisher --stdio pipe
або --stdio files --log-dir logs
```

**Вікно 6 — WS server** (UI backend, порт 8000):
```powershell
cd c:\Users\vikto\aione-context\v3
.venv\Scripts\python.exe -u -m app.main --mode ws_server --stdio pipe
або --stdio files --log-dir logs
```

### Швидкий рестарт тільки UI

В вікні 6 натисни **Ctrl+C**, потім знову:
```powershell
.venv\Scripts\python.exe -u -m app.main --mode ws_server --stdio pipe
```
Дані в інших 5 вікнах не зупиняються.

### Legacy — все в одному процесі

```powershell
.venv\Scripts\python.exe -u -m app.main --mode all --stdio pipe
```

### Перевірка

```powershell
# API health check
curl http://127.0.0.1:8000/api/status

# UI у браузері
start http://127.0.0.1:8000/

# TUI монітор (окреме вікно)
python -m aione_top
```

### UI v4 — build frontend

```powershell
cd ui_v4
npm install
npm run build
cd ..
```

### UI v4 — dev mode (hot-reload)

```powershell
cd ui_v4
npm run dev
# → http://localhost:5173/ (проксі на ws_server:8000)
```

### Тести

```powershell
python -m pytest tests/ -v
python -m pytest tests/test_smc_e1.py -v          # конкретний тест
python -m pytest tests/ -k "smc" -v               # тести за назвою
```

### Quality gates

```powershell
python -m tools.run_exit_gates --manifest tools/exit_gates/manifest.json
```

### Rebuild derived TFs

```powershell
python -m tools.rebuild_from_m1 --symbol XAU/USD --tf 300
python -m tools.rebuild_from_m1 --symbol BTCUSDT --tf 300
```

### Redis diagnostics

```powershell
# Статус Redis
redis-cli -n 1 INFO keyspace

# Останній бар для символу/TF
redis-cli -n 1 ZRANGE "v3_local:ohlcv:snap:XAU/USD:60" -1 -1

# Preview поточний
redis-cli -n 1 GET "v3_local:preview:curr:XAU/USD:60"
```

---

## VPS (Ubuntu, SSH alias: `aione-vps`)

### Підключення

```bash
ssh aione-vps
```

### Supervisor — статус всіх процесів

```bash
sudo supervisorctl status
```

### Рестарт тільки UI (швидкий — ~3 сек)

```bash
sudo supervisorctl restart smc:smc-ws
```

### Рестарт конкретного процесу

```bash
sudo supervisorctl restart smc:smc-fxcm           # FXCM broker reconnect
sudo supervisorctl restart smc:smc-ticks           # FXCM tick stream
sudo supervisorctl restart smc:smc-preview         # tick preview worker
sudo supervisorctl restart smc:smc-binance         # Binance ingest
sudo supervisorctl restart smc:smc-binance-ticks   # Binance tick stream
```

### Рестарт ВСЬОГО

```bash
sudo supervisorctl restart smc:*
```

### Стоп/старт окремого процесу

```bash
sudo supervisorctl stop smc:smc-fxcm
sudo supervisorctl start smc:smc-fxcm
```

### Логи

```bash
# Останні 50 рядків ws_server
tail -50 /var/log/smc-v3/ws_server.stderr.log

# Tail в реальному часі
tail -f /var/log/smc-v3/ws_server.stderr.log

# Всі логи
ls -la /var/log/smc-v3/

# FXCM broker
tail -50 /var/log/smc-v3/fxcm.stderr.log
```

### Деплой UI (з локальної машини)

```powershell
# 1. Збірка (локально)
cd ui_v4
npx vite build

# 2. Копіювання на VPS
scp -r dist/* aione-vps:/opt/smc-v3/ui_v4/dist/

# 3. Рестарт ws_server
ssh aione-vps "sudo supervisorctl restart smc:smc-ws"
```

### Деплой бекенду (з локальної машини)

```powershell
# Копіювання змінених файлів
scp -r core/ aione-vps:/opt/smc-v3/core/
scp -r runtime/ aione-vps:/opt/smc-v3/runtime/
scp config.json aione-vps:/opt/smc-v3/

# Рестарт потрібних процесів
ssh aione-vps "sudo supervisorctl restart smc:smc-ws"
# або всього:
ssh aione-vps "sudo supervisorctl restart smc:*"
```

### API health check

```bash
curl -s http://127.0.0.1:8000/api/status | python3 -m json.tool
```

### Nginx

```bash
# Перевірка конфігу
sudo nginx -t

# Перезавантаження
sudo systemctl reload nginx

# Логи
tail -50 /var/log/nginx/smc_access.log
tail -50 /var/log/nginx/smc_error.log
```

### fail2ban

```bash
# Статус
sudo fail2ban-client status

# Забанені IP для nginx
sudo fail2ban-client status nginx-botsearch

# Розбанити IP
sudo fail2ban-client set nginx-botsearch unbanip <IP>
```

### Диск / RAM / CPU

```bash
df -h              # диск
free -h            # RAM
htop               # CPU/процеси
```

### SSL сертифікат

```bash
# Перевірка терміну
sudo certbot certificates

# Оновлення (зазвичай автоматичне через cron)
sudo certbot renew --dry-run
```

### Supervisor конфіг (якщо потрібно оновити)

```powershell
# З локальної машини:
scp tools/smc-v3.supervisor.conf aione-vps:/etc/supervisor/conf.d/smc-v3.conf
ssh aione-vps "sudo supervisorctl reread; sudo supervisorctl update"
```
