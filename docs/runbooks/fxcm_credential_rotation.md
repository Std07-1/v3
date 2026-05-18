# FXCM Demo Credential Rotation + Gap Backfill

> **Runbook**: повернення FXCM data-pipeline в робочий стан після **закінчення терміну дії demo-акаунту**.
> **Audience**: оператор VPS + AI-агент.
> **Trigger**: бачимо що `data_v3/{symbol}/tf_60/part-YYYYMMDD.jsonl` зупинився писатися; `fxcm.stderr.log` показує auth failure; нові M1 не приходять.
> **Last updated**: 2026-05-18 (incident #1 — D251105708 expired; recovery executed successfully)

---

## 0. Incident #1 — фактичні числа (2026-05-18)

| Метрика | Значення |
|---|---|
| Старий USERNAME | `D251105708` |
| Last valid M1 (XAU/USD) | `2026-05-15 20:45 UTC` (Friday close) |
| Reactivated M1 | `2026-05-18 16:00 UTC` (Monday after creds rotation) |
| Real gap (after weekend exclude) | ≈ 17 годин (Sun 22:00 → Mon 15:47 UTC) |
| M1 backfilled XAU/USD | 942 нових барів (з 1500 запитаних — решта dedup) |
| M1 backfilled XAG/USD | 943 нових бари |
| Cascade derive | XAU+XAG: M3, M5, M15, M30, H1, H4 (per-symbol по 79 нових барів) |
| Binance pipeline | Не зачеплено (24/7 ingest, M1 живий) |
| Total wall-time recovery | ~5 хв після того як отримали нові creds |

---

## 1. Чому це періодично буде повторюватися

FXCM **Demo-акаунти живуть обмежений час** (зазвичай 30 днів від останньої активності, або фіксовано на старті). Коли експіряться:

- ForexConnect SDK не може залогінитися → `broker_sidecar` (.venv37) падає в retry loop
- M1 bars перестають надходити в `runtime/ingest/m1_ingestion_worker.py` через Redis IPC
- `data_v3/{SYM}/tf_60/part-*.jsonl` зупиняє приріст → derive-cascade (M3→M5→…→H4→D1) теж зависає
- WS UI відображає stale бари, alerts не спрацьовують, SMC overlay застигає

**Чому це дизайн, а не баг**: FXCM SDK = вузька талія до broker'а. Інших шляхів для FXCM-only символів (XAU/USD, XAG/USD) немає — Binance не дає золото/срібло як ф'ючерси у нашій конфігурації.

**Чому ми НЕ тримаємо backup creds**: політика безпеки — secrets не комітяться, не дублюються. Один акаунт = одне джерело правди. Ротація — раз на місяць максимум.

---

## 2. Файли і компоненти що зачеплені

| Шар | Що змінюється | Де | Чому саме там |
|---|---|---|---|
| Secrets | `FXCM_USERNAME`, `FXCM_PASSWORD` | VPS `/opt/smc-v3/.env` | SSOT для broker `.venv37` + helper tools. Локального `.env` з FXCM creds немає за дизайном (X31 — не дублюємо secrets між машинами) |
| Process | `smc:smc-fxcm`, `smc:smc-ticks` | supervisor (`/etc/supervisor/conf.d/smc-v3.conf`) | broker_sidecar читає `.env` тільки на старті → restart обов'язковий |
| Data | M1 jsonl tail для FXCM-символів | `/opt/smc-v3/data_v3/{XAU_USD,XAG_USD}/tf_60/` | SSOT OHLCV. Binance символи (BTCUSDT, ETHUSDT) не зачеплені |
| Derived | M3, M5, M15, M30, H1, H4, D1 | `/opt/smc-v3/data_v3/{SYM}/tf_{N}/` | Перебудовується каскадно з M1 (`tools.backfill_cascade` + ADR-0023 для D1) |

---

## 3. Pre-flight checks (перед ротацією)

```bash
# 3.1 Підтвердити що проблема саме в FXCM auth (не мережа, не VPS down)
ssh aione-vps 'tail -50 /var/log/smc-v3/fxcm.stderr.log | grep -iE "login|auth|expired|forbidden|unauthorized|reconnect"'

# 3.2 Дізнатися edge of gap (останній валідний M1)
ssh aione-vps 'for sym in XAU_USD XAG_USD; do echo "=== $sym ==="; ls -la /opt/smc-v3/data_v3/$sym/tf_60/ | tail -3; done'

# 3.3 Перевірити що Binance pipeline працює (контроль що інфра в нормі)
ssh aione-vps 'ls -la /opt/smc-v3/data_v3/BTCUSDT/tf_60/ | tail -2'
```

**Що шукаємо**:
- `expired`, `Login failed`, `invalid credentials` → це credential rotation
- Якщо стек `ConnectionError`, `timeout` → це network/VPS, **не цей runbook**

---

## 4. Phase 1 — отримати нові creds

1. Зайти на https://www.fxcm.com/forex-trading-demo/ → створити новий demo
2. Зберегти `FXCM_USERNAME` (формат `D2XXXXXXXX`) + `FXCM_PASSWORD`
3. **Connection** залишається `Demo`, **URL** — `http://www.fxcorporate.com/Hosts.jsp`

**Безпека**: не вставляти в Telegram, не комітити, не зберігати у файлі що індексується git. Передавати в runbook сесію через локальний файл `.env.fxcm.new` (gitignored) АБО paste прямо в SSH heredoc.

---

## 5. Phase 2 — deploy на VPS (одна транзакція, з backup)

> **Два методи редагування — обери один**. Метод A (sed з локального терміналу) працює тільки з **PowerShell / bash**. Метод B (nano прямо на VPS) — fallback якщо термінал dev-машини = `cmd.exe`.

### 5.1 Backup існуючого `.env` (обов'язково, незалежно від методу)

```bash
# Важливо — у .env лежать і інші ключі: ANTHROPIC, channels тощо
ssh aione-vps 'cp /opt/smc-v3/.env /opt/smc-v3/.env.bak.$(date +%Y%m%d-%H%M%S)'
ssh aione-vps 'ls -la /opt/smc-v3/.env.bak.*'
```

### 5.2 Stop FXCM-related processes ПЕРЕД редагуванням

```bash
# щоб не було race: ще працює зі старим, але .env вже з новим
ssh aione-vps 'sudo supervisorctl stop smc:smc-fxcm smc:smc-ticks smc:smc-preview'
```

### 5.3a Method A — sed з PowerShell / bash (швидко, повторювано)

```powershell
# PowerShell на dev-машині. Підставити НОВИЙ_USER і НОВИЙ_PASS.
ssh aione-vps "sed -i 's|^FXCM_USERNAME=.*|FXCM_USERNAME=НОВИЙ_USER|' /opt/smc-v3/.env && sed -i 's|^FXCM_PASSWORD=.*|FXCM_PASSWORD=НОВИЙ_PASS|' /opt/smc-v3/.env && grep -E '^FXCM_(USERNAME|CONNECTION|HOST_URL)=' /opt/smc-v3/.env"
```

> ⚠️ **cmd.exe parens trap (incident #1)**: спроба запустити `ssh aione-vps "cp ... .env.bak.\$(date +%Y%m%d-%H%M%S) && nano ..."` з `cmd.exe` дає `bash: -c: syntax error near unexpected token '('`. Це бо `cmd.exe` не екранує `$(...)` всередині `"..."` так, як bash/PowerShell. Або вживай PowerShell, або Method B.

### 5.3b Method B — nano прямо на VPS (recommended якщо термінал = cmd.exe або не довіряєш sed-quoting'у)

```bash
# 1) Зайти на VPS звичайним ssh (без inline-команди)
ssh aione-vps

# 2) Backup і відкрити редактор (тут вже всередині bash на VPS — $(...) працює)
cp /opt/smc-v3/.env /opt/smc-v3/.env.bak.$(date +%Y%m%d-%H%M%S)
sudo nano /opt/smc-v3/.env
```

У `nano`: знайти рядки `FXCM_USERNAME=...` і `FXCM_PASSWORD=...` → замінити значення.
Зберегти: **`Ctrl+O` → `Enter` → `Ctrl+X`**.

Після збереження — перевірити (PASSWORD маскований):
```bash
grep -E '^FXCM_(USERNAME|CONNECTION|HOST_URL)=' /opt/smc-v3/.env
grep '^FXCM_PASSWORD=' /opt/smc-v3/.env | sed 's/=.*/=***MASKED***/'
```
Вийти з SSH сесії (`exit`) і повернутися до dev-терміналу.

### 5.4 Перезапустити + observation window 60s (D9.1)

```bash
ssh aione-vps 'sudo supervisorctl start smc:smc-fxcm smc:smc-ticks smc:smc-preview && for i in 1 2 3 4 5 6; do echo "=== T+$((i*10))s ==="; sleep 10; sudo supervisorctl status smc:smc-fxcm smc:smc-ticks; tail -n 5 /var/log/smc-v3/fxcm.stderr.log; done'
```

**Очікувані сигнали успіху** (у логах):
- `BROKER_LOGIN_OK` або еквівалент
- `M1_BAR_RECEIVED symbol=XAU/USD`
- supervisorctl status: `RUNNING` uptime > 60s

**STOP-сигнали** (відкат):
- Знову `Login failed` → перевірити що скопіювали правильний USER/PASS без пробілів
- Traceback / CRITICAL → одразу rollback (Phase 9)

---

## 6. Phase 3 — backfill M1 гепу

> **Принцип**: тягнемо M1 з FXCM (запитуємо більше ніж треба, dedup автоматичний по `open_time_ms`). Інші TF НЕ тягнемо напряму — деривуються каскадом.

```bash
# 6.1 Розрахувати скільки M1 барів треба
#     1 день торгів ≈ 1440 хв, але weekend exclude → ~ 1000-1200/день у реалі
#     Запас x2: для 3-денного гепу беремо ~ 7000 барів
GAP_DAYS=3   # підставити реальну кількість днів між last_M1 і now
N_M1=$((GAP_DAYS * 1440 * 2))   # запас x2 для weekend gaps + retries
echo "Beru $N_M1 M1 bars for $GAP_DAYS-day gap"

# 6.2 Run backfill для кожного FXCM-символу
#     Binance-символи НЕ тут — їх інший pipeline.
#     ⚠️ ОБОВ'ЯЗКОВО .venv37/bin/python — fetch_tf_backfill тягне FxcmHistoryProvider
#        що залежить від ForexConnect SDK (Python 3.7, ADR-0016).
#        .venv/bin/python тут дасть ImportError на ForexConnect.
ssh aione-vps "cd /opt/smc-v3 && \
  ./.venv37/bin/python -m tools.fetch_tf_backfill --tf 60 --symbol 'XAU/USD' --n $N_M1 2>&1 | tail -20 && \
  ./.venv37/bin/python -m tools.fetch_tf_backfill --tf 60 --symbol 'XAG/USD' --n $N_M1 2>&1 | tail -20"
```

**Чому `--tf 60` а не одразу всі TF**:
- H4 (14400) — derived-only (ADR-0002). FXCM має інший anchor → роз'їзд з нашим 23:00 UTC anchor
- D1 (86400) — derived-only (ADR-0023). FXCM D1 anchor (17:00 NY) ≠ наш 22:00 UTC
- M3, M5, M15, M30, H1 — деривуються з M1 за один прохід `backfill_cascade`

**Що очікуємо в output**:
```
Backfill TF=60: symbols=1 date_to=2026-05-18T15:47Z n=8640 out=./data_v3
XAU/USD: запит 8640 TF=60 барів до 2026-05-18T15:47Z …
XAU/USD: dedup — пропущено 5400, нових 3240
XAU/USD: записано=3240 first=2026-05-15T20:46Z last=2026-05-18T15:47Z
```

---

## 7. Phase 4 — каскадна деривація M1→H4

> **Tool**: `tools.rebuild_from_m1` (НЕ `tools.backfill_cascade` — такого модуля немає; це була типографська помилка в попередній версії runbook'у). Використовує `.venv/bin/python` (НЕ потребує ForexConnect SDK — читає M1 з диску).
>
> **CLI**: `--symbol X --start YYYY-MM-DD --end YYYY-MM-DD` (нема `--all` — викликати окремо per-symbol).

> ⚠️ **CRITICAL**: `--end` є **END-EXCLUSIVE** — дата `--end` **НЕ** включається в rebuild.
> Щоб включити всі бари за `2026-05-18`, треба `--end 2026-05-19`.
> **Помилка incident #1**: `--end 2026-05-18` не обробила жоден бар з 18 травня.

> ⚠️ **CRITICAL**: Якщо derived TF вже існують (live derive або попередній rebuild), ОБОВ'ЯЗКОВО додавай `--force`.
> Без `--force`, `_has_on_disk()` бачить існуючі файли і пропускає запис (`existed` counter, `written=0`).
> **Помилка incident #1**: пропустили `--force` → partial H4 бари (src=1/4) залишились нетронутими.

```bash
# Per-symbol — END DATE = target_date + 1 day (end-exclusive!), + --force для overwrite partial bars
ssh aione-vps "cd /opt/smc-v3 && \
  ./.venv/bin/python -m tools.rebuild_from_m1 --symbol 'XAU/USD' --start 2026-05-15 --end 2026-05-19 --force 2>&1 | tail -40"

ssh aione-vps "cd /opt/smc-v3 && \
  ./.venv/bin/python -m tools.rebuild_from_m1 --symbol 'XAG/USD' --start 2026-05-15 --end 2026-05-19 --force 2>&1 | tail -40"
```

**Параметри**:
- `--symbol` — обов'язково (без default = all). Для всіх FXCM-символів — окремий виклик
- `--start` / `--end` — ISO date (UTC), **напіввідкритий інтервал \[start, end)** = END-EXCLUSIVE
- `--force` — **обов'язковий** якщо derived bars вже є на диску (live derive або попередній rebuild)
- `--dry-run` (опціонально) — без запису, тільки звіт
- результат: M3, M5, M15, M30, H1, H4 стають consistent з оновленим M1

**Як перевірити що rebuild спрацював**:
```
REBUILD_DONE symbol=XAU/USD elapsed=0.8s stats={... "tf_14400_written": 11, "tf_14400_existed": 0, ...}
```
Якщо `tf_14400_existed > 0` і `tf_14400_written = 0` → ти пропустив `--force`.

**Чому окремий tool а не in-process**: `derive_engine.py` працює в live-режимі (tick-by-tick). Для backfill потрібна batch-обробка з диску — це `rebuild_from_m1`.

**Очікуваний output**:
```
Stage 1: M1 → M3 written=40 existed=...
         M1 → M5 written=24 existed=...
         M1 → D1 existed=1 (forming bar — нормально, write at 22:00 UTC anchor)
Cascade: TF 900, 1800, 3600, 14400 — written/existed counts
Final summary: m1=1365 written=79 existed=817
```

---

## 7b. Phase 4b — ws_server ОБОВ'ЯЗКОВИЙ restart після rebuild

> ⚠️ **CRITICAL**: ws_server тримає UDS snapshot **в пам'яті**. Після `rebuild_from_m1` JSONL-файли оновлені на диску, але ws_server **не знає** про це — він продовжує роздавати stale bars з Redis/RAM snapshot.
>
> **Симптом якщо пропустити**: Archi і UI бачать «gap» (наприклад 13:42-16:42 UTC замість повного 00:00-16:42 UTC), навіть якщо M1 на диску повний. Archi формує помилкові тези типу "gap_up bounce".

```bash
# Restart ws_server після rebuild — примусово перечитує всі JSONL з диску
ssh aione-vps 'sudo supervisorctl restart smc:smc-ws'

# Observation window 60s — чекаємо SMC warmup (D9.1)
ssh aione-vps 'for i in 1 2 3 4 5 6; do echo "=== T+$((i*10))s ==="; sleep 10; sudo supervisorctl status smc:smc-ws; tail -n 3 /var/log/smc-v3/ws_server.stderr.log | grep -E "WARMUP|RUNNING|ERROR"; done'
```

**Acceptance criteria після restart**:
- `SMC_RUNNER_WARMUP_DONE ok=20 err=0` в логах
- `SMC_WARMUP_OK sym=XAU/USD tf=14400 bars=500` — H4 warmed up з правильними барами
- supervisorctl: `RUNNING` uptime > 60s

---

## 8. Phase 5 — D1 окремо (ADR-0023)

> D1 деривується з M1 каскадом `M1→D1(×1440)` з anchor 79200s (22:00 UTC). Не потребує окремих кроків, **але** треба перевірити що `_derived_tail_state.json` оновився.

```bash
ssh aione-vps "ls -la /opt/smc-v3/data_v3/_derived_tail_state.json && \
               cat /opt/smc-v3/data_v3/_derived_tail_state.json | python3 -m json.tool | grep -A1 'XAU_USD'"
```

Якщо стан старий — рестарт live derive worker підбере його через ws_server lifecycle:
```bash
ssh aione-vps 'sudo supervisorctl restart smc:smc-ws'
```

---

## 9. Phase 6 — verify end-to-end

```bash
# 9.1 Останній M1 бар не старший за 5 хвилин
ssh aione-vps 'for sym in XAU_USD XAG_USD; do echo "=== $sym M1 ==="; \
  tail -1 /opt/smc-v3/data_v3/$sym/tf_60/part-$(date -u +%Y%m%d).jsonl 2>&1 | \
  python3 -c "import sys,json,time; d=json.loads(sys.stdin.read()); \
  age=time.time()-d[\"open_time_ms\"]/1000; print(f\"open={d[\\\"open_time_ms\\\"]} age_sec={age:.0f}\")"; done'

# 9.2 H4 і D1 повний рядок (контракт sanity)
ssh aione-vps 'for tf in 14400 86400; do echo "=== XAU_USD tf=$tf last bar ==="; \
  find /opt/smc-v3/data_v3/XAU_USD/tf_$tf/ -name "part-*.jsonl" -type f | sort | tail -1 | \
  xargs tail -1; done'

# 9.3 WS UI смок-тест (опціонально)
curl -s http://127.0.0.1:8000/api/status | python3 -m json.tool | head -30
```

**Acceptance criteria**:
- M1 age < 300s
- H4 last bar має `complete=true` для попередньої свічки
- `/api/status` без `degraded[]` пов'язаних з FXCM

---

## 10. Phase 9 — rollback (якщо щось пішло не так)

```bash
# Знайти останній бекап .env
ssh aione-vps 'ls -t /opt/smc-v3/.env.bak.* | head -3'

# Відкат creds (replace TIMESTAMP)
ssh aione-vps 'cp /opt/smc-v3/.env.bak.TIMESTAMP /opt/smc-v3/.env && \
               sudo supervisorctl restart smc:smc-fxcm smc:smc-ticks'
```

> Зауваження: rollback **не повертає демо до життя** — він просто відновлює попередній стан конфігу. Якщо старий expired, треба знову Phase 1+.

---

## 11. Часті помилки і чому вони трапляються

| Симптом | Причина | Лік |
|---|---|---|
| `bash: -c: syntax error near unexpected token '('` при inline `ssh ... "... $(date +...) ..."` | `cmd.exe` не екранує `$(...)` в подвійних лапках як bash/PowerShell | Method B (nano-on-VPS) АБО переключитись на PowerShell |
| `Login failed` після restart | trailing space у USER/PASS після `sed` | `cat -A /opt/smc-v3/.env \| grep FXCM` → шукати `$` після значення; повторити sed з акуратнішими лапками |
| `ImportError: No module named forexconnect` при backfill | Запустив `fetch_tf_backfill` через `.venv/bin/python` замість `.venv37/bin/python` | ОБОВ'ЯЗКОВО `.venv37/bin/python -m tools.fetch_tf_backfill ...` |
| `ModuleNotFoundError: tools.backfill_cascade` | Стара назва. Правильно — `tools.rebuild_from_m1` | Виправити команду на `python -m tools.rebuild_from_m1 --symbol X --start ... --end ...` |
| M1 приходять, але H4 stale | `_derived_tail_state.json` застряг | `restart smc:smc-ws` |
| Backfill пише `0 нових барів` | broker віддає ті ж бари що вже на диску (dedup працює) | Збільшити `--n` або зменшити `--date-to` |
| `connection timeout` під час backfill | FXCM rate limit | Перерва 5 хв, ретрай меншими батчами (`--n 1000` x several) |
| D1 anchor роз'їзд (свічка зміщена на години) | пробували `fetch_tf_backfill --tf 86400` напряму з FXCM | Видалити пошкоджені D1 файли, перебудувати через `rebuild_from_m1` (D1 derives in-process via ADR-0023) |
| **H4 bars incomplete after rebuild** (`src=1/4`, `src=3/4`) | Не вказав `--force` → існуючі partial bars залишились (rebuild пропустив їх) | Повторити з `--force`. Перевірити: `tf_14400_written` > 0 і `tf_14400_existed` = 0 |
| **rebuild_from_m1 не обробив target date** | `--end` END-EXCLUSIVE: `--end 2026-05-18` не включає 18 травня | Завжди `--end = target_date + 1`: `--end 2026-05-19` для даних 18 травня |
| **UI/Archi бачить gap після rebuild** | ws_server НЕ перезапустили → читає stale UDS snapshot з RAM | `sudo supervisorctl restart smc:smc-ws` + 60s observation (Фаза 7b) |
| **JSONL `head -1` показує не найстарший бар** | Після backfill нові (старі) бари appended в кінець файлу (out-of-order) | Читати через `python3 -c "...json.loads(l)['open_time_ms']..."` + sorted min/max. Не довіряти `head -1` після backfill |

---

## 12. Чому саме така архітектура (для майбутнього reader'а)

1. **Чому FXCM venv ізольований (`.venv37`)** — ForexConnect SDK прибитий до Python 3.7 (ADR-0016). Решта системи на 3.11+. Запуск broker як окремий процес через `app/main.py` mode dispatcher.
2. **Чому broker_sidecar пише в Redis, а не напряму на диск** — split-brain prevention (I1, UDS=narrow waist). `m1_ingestion_worker` єдиний writer M1 в SSOT (`runtime/store/uds.py`).
3. **Чому secrets тільки на VPS** — F1 (Secrets Management). Локальна dev-машина має тільки trader-v3 секрети (Telegram/Anthropic). FXCM-pipeline не запускається локально для розробки UI (mocked replay через `tools.replay`, ADR-0017).
4. **Чому ротація = ручна процедура, не auto-renewal** — FXCM не дає API для авто-створення demo. Спроби автоматизувати = TOS violation. Краще людський gate раз на місяць.
5. **Чому backfill окремий tool а не feature broker_sidecar** — separation of concerns: sidecar = live tail, backfill = historical batch. Різні error modes, різні retry strategies, різний blast radius при крашу.

---

## 13. Майбутні покращення (TODO, не блокери)

- [ ] Метрика `fxcm_last_bar_age_seconds` → alert якщо > 600s протягом 15 хв
- [ ] Pre-expiry warning — раз на тиждень cron-перевірка віку акаунту через FXCM API (якщо доступне)
- [ ] Auto-backfill при detect гепу > 30 хв (з safety cap на N днів і rate-limit)
- [ ] Other broker as failover (Oanda? IB?) — потребує ADR і нового provider у `core/buckets/anchors.py`

---

**Маршрут switch-over (швидкий cheat-sheet)**:
1. Pre-flight: `tail /var/log/smc-v3/fxcm.stderr.log` → confirm auth fail
2. Get new demo creds (FXCM website) → save locally as `.env.fxcm.new` АБО plan для nano-on-VPS
3. SSH to VPS: backup `.env` (timestamped) → stop `smc:smc-fxcm smc:smc-ticks smc:smc-preview`
4. Edit `.env`: Method A (sed з PowerShell) АБО Method B (nano всередині SSH сесії). **НЕ cmd.exe inline `ssh ... "...$(date)..."`** — parens trap.
5. Start back → 60s D9.1 observe (`supervisorctl status` + `tail fxcm.stderr.log`)
6. `./.venv37/bin/python -m tools.fetch_tf_backfill --tf 60 --symbol X --n N` × XAU/USD, XAG/USD (dual venv: .venv37 для SDK)
7. `./.venv/bin/python -m tools.rebuild_from_m1 --symbol X --start FROM --end TO+1_DAY --force` × XAU/USD, XAG/USD (.venv — звичайний)
   ⚠️ `--end` є END-EXCLUSIVE: `--end 2026-05-18` НЕ включає 18 травня → використовуй `--end 2026-05-19`
   ⚠️ `--force` обов'язковий якщо derived bars вже є на диску (live derive або попередній rebuild)
8. `sudo supervisorctl restart smc:smc-ws` + 60s observation → чекаємо `SMC_WARMUP_OK tf=14400`
   ⚠️ ws_server ОБОВ'ЯЗКОВИЙ restart після rebuild — інакше UDS RAM snapshot застарілий
9. Verify M1 age < 300s + H4 forming bar + `/api/status` clean
