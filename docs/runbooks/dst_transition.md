# Runbook: DST Transition (Перехід літній/зимовий час)

> **Створено**: 2026-03-31 (після першого інциденту з DST-дірою)  
> **Навігація**: [docs/index.md](../index.md) · [runbooks/production.md](production.md)

---

## Зміст

1. [Контекст](#контекст)
2. [Календар переходів](#календар-переходів)
3. [Що змінювати — Зима → Літо](#що-змінювати--зима--літо)
4. [Що змінювати — Літо → Зима](#що-змінювати--літо--зима)
5. [Процедура переходу (покроково)](#процедура-переходу-покроково)
6. [Перевірка після переходу](#перевірка-після-переходу)
7. [Що НЕ змінюється](#що-не-змінюється)
8. [Архітектурний контекст](#архітектурний-контекст)

---

## Контекст

FX/CFD ринки (XAU/USD, XAG/USD, NAS100, SPX500 тощо) прив'язані до **нью-йоркського часу** (ET).
Коли США переходить на EDT (літній) або EST (зимовий), змінюється UTC-зсув сесій.

Платформа використовує **фіксовані UTC-значення** в `config.json` — вони НЕ автоматично адаптуються до DST.

**Якщо не оновити config.json при переході DST:**
- H4 бари перекривають daily break (garbage data)
- D1 бари стартують на годину раніше/пізніше ніж NY close
- Calendar gate пропускає/блокує бари в неправильний час

---

## Календар переходів

### US DST (визначає ринкові сесії)

| Перехід | Коли | UTC offset |
|---------|------|-----------|
| **Зима → Літо (EDT)** | Друга неділя березня, 02:00 ET | UTC-4 |
| **Літо → Зима (EST)** | Перша неділя листопада, 02:00 ET | UTC-5 |

### EU DST (зазвичай через ~3 тижні після US)

| Перехід | Коли |
|---------|------|
| **Зима → Літо (CEST)** | Остання неділя березня |
| **Літо → Зима (CET)** | Остання неділя жовтня |

> **ВАЖЛИВО**: Для платформи релевантний саме **US DST** — FX/CFD сесії визначаються нью-йоркським часом.
> EU DST впливає тільки на час Київ/Лондон у логах, але не на ринкові сесії.

---

## Що змінювати — Зима → Літо

> Переходимо з EST (UTC-5) на EDT (UTC-4). Всі сесії зсуваються на 1 годину раніше в UTC.

### config.json — Day Anchors

```jsonc
// ЗИМА (EST, UTC-5) → ЛІТО (EDT, UTC-4)
"day_anchor_offset_s":      82800 → 79200,   // H4: 23:00 → 22:00 UTC
"day_anchor_offset_s_alt":      0 → 82800,   // зберігаємо зимовий якір в alt
"day_anchor_offset_s_alt2": 79200 → 0,
"day_anchor_offset_s_d1":   79200 → 75600,   // D1: 22:00 → 21:00 UTC
"day_anchor_offset_s_d1_alt":   0 → 79200,   // зберігаємо зимовий D1 в alt
```

### config.json — Market Calendar `cfd_us_22_23`

| Поле | Зима (EST) | Літо (EDT) |
|------|-----------|-----------|
| `market_daily_break_start_hm` | `"22:00"` | `"21:00"` |
| `market_daily_break_end_hm` | `"23:00"` | `"22:00"` |
| `market_weekend_open_hm` | `"23:00"` | `"22:00"` |
| `market_weekend_close_hm` | `"21:45"` | `"20:45"` |

### config.json — Market Calendar `fx_24x5_utc_summer`

| Поле | Зима | Літо |
|------|------|------|
| `market_weekend_open_hm` | `"22:00"` | `"21:00"` |
| `market_weekend_close_hm` | `"21:55"` | `"20:55"` |
| `market_daily_breaks` | `[["21:55", "22:30"]]` | `[["20:55", "21:30"]]` |

> **Примітка**: Назва групи `fx_24x5_utc_summer` — історична. Вона використовується і взимку (просто з іншими значеннями).

---

## Що змінювати — Літо → Зима

> Переходимо з EDT (UTC-4) на EST (UTC-5). Зворотня операція.

### config.json — Day Anchors

```jsonc
// ЛІТО (EDT, UTC-4) → ЗИМА (EST, UTC-5)
"day_anchor_offset_s":      79200 → 82800,   // H4: 22:00 → 23:00 UTC
"day_anchor_offset_s_alt":  82800 → 0,
"day_anchor_offset_s_alt2":     0 → 79200,   // зберігаємо літній якір в alt2
"day_anchor_offset_s_d1":   75600 → 79200,   // D1: 21:00 → 22:00 UTC
"day_anchor_offset_s_d1_alt": 79200 → 0,
```

### config.json — Market Calendar `cfd_us_22_23`

| Поле | Літо (EDT) | Зима (EST) |
|------|-----------|-----------|
| `market_daily_break_start_hm` | `"21:00"` | `"22:00"` |
| `market_daily_break_end_hm` | `"22:00"` | `"23:00"` |
| `market_weekend_open_hm` | `"22:00"` | `"23:00"` |
| `market_weekend_close_hm` | `"20:45"` | `"21:45"` |

### config.json — Market Calendar `fx_24x5_utc_summer`

| Поле | Літо | Зима |
|------|------|------|
| `market_weekend_open_hm` | `"21:00"` | `"22:00"` |
| `market_weekend_close_hm` | `"20:55"` | `"21:55"` |
| `market_daily_breaks` | `[["20:55", "21:30"]]` | `[["21:55", "22:30"]]` |

---

## Процедура переходу (покроково)

### Підготовка (за ~1 день до)

1. Визначити точну дату переходу US DST
2. Підготувати config.json зміни (див. таблиці вище)

### День переходу

Виконувати **в п'ятницю після закриття ринку** або **в неділю перед відкриттям** — коли ринок закритий.

#### 1. Зупинити платформу

**VPS:**
```bash
sudo supervisorctl stop smc:*
```

**Локально:**
```bash
# Якщо supervisor:
python -m app.main --mode all  # Ctrl+C
# Або через taskkill якщо запущені окремі процеси
```

#### 2. Оновити config.json

Змінити значення якорів + calendar (див. таблиці вище).

#### 3. Видалити H4/D1 файли від дати переходу DST

```bash
# VPS (приклад для US DST з 8 березня):
cd /opt/smc-v3/data_v3
for sym in XAU_USD XAG_USD; do
  rm -f $sym/tf_14400/part-2026030[89].jsonl  # дату адаптувати
  rm -f $sym/tf_14400/part-202603[12]*.jsonl
  rm -f $sym/tf_86400/part-2026030[89].jsonl
  rm -f $sym/tf_86400/part-202603[12]*.jsonl
done
```

> **УВАГА**: Видаляти ТІЛЬКИ H4 (`tf_14400`) і D1 (`tf_86400`) для FX/CFD символів.
> M1-H1 використовують `anchor=0` і від DST не залежать.
> BTCUSDT/ETHUSDT використовують `binance.day_anchor_offset_s=0` — не чіпати.

#### 4. Rebuild H4/D1

```bash
# VPS:
cd /opt/smc-v3
source .venv/bin/activate
python -m tools.rebuild_from_m1 --symbol "XAU/USD" --start 2026-03-08
python -m tools.rebuild_from_m1 --symbol "XAG/USD" --start 2026-03-08
```

> Параметр `--start` = день початку US DST, або трохи раніше для безпеки.

#### 5. Верифікувати H4 alignment

```bash
# Перевірити, що H4 бари починаються з правильного якоря:
tail -5 data_v3/XAU_USD/tf_14400/part-YYYYMMDD.jsonl | python3 -c "
import json, sys
from datetime import datetime, timezone
for line in sys.stdin:
    j = json.loads(line)
    o = datetime.fromtimestamp(j['open_time_ms']/1000, timezone.utc)
    c = datetime.fromtimestamp(j['close_time_ms']/1000, timezone.utc)
    print(f'{o:%H:%M} -> {c:%H:%M}')
"
```

**Очікуваний результат (літо):** `02:00, 06:00, 10:00, 14:00, 18:00, 22:00`  
**Очікуваний результат (зима):** `03:00, 07:00, 11:00, 15:00, 19:00, 23:00`

#### 6. Запустити платформу

```bash
sudo supervisorctl start smc:*
```

#### 7. Повторити для локальної машини

Та сама процедура: зупинити → config → delete → rebuild → verify → запустити.

---

## Перевірка після переходу

| Перевірка | Команда | Очікуване |
|-----------|---------|-----------|
| H4 bars alignment | Див. п.5 вище | Бари кратні 4 годинам від якоря |
| D1 bar boundaries | Перевірити `open_time` останнього D1 | Літо: 21:00 UTC, Зима: 22:00 UTC |
| Daily break gap | Перевірити gap в M1 даних | Літо: 21:00-22:00, Зима: 22:00-23:00 |
| Платформа running | `sudo supervisorctl status smc:*` | Всі RUNNING |
| API status | `curl http://127.0.0.1:8000/api/status` | `prime_ready` |

---

## Що НЕ змінюється

| Елемент | Чому |
|---------|------|
| M1, M3, M5, M15, M30, H1 | `anchor_offset=0` — не залежать від DST |
| BTCUSDT, ETHUSDT (crypto) | `binance.day_anchor_offset_s=0`, `crypto_24x7` schedule |
| `cfd_eu_21_07` (GER30, EUSTX50) | Європейські CFD мають фіксований розклад |
| `cfd_hk_main` (HKG33) | Гонконгська біржа не залежить від US DST |
| `core/buckets.py` код | Код generic, якорі приходять з конфігу |
| `runtime/store/ssot_jsonl.py` | `select_anchor_offset_for_open_ms()` використовує `_alt` fields для сумісності |

---

## Архітектурний контекст

### Чому _alt поля важливі

`ssot_jsonl.py` містить функцію `select_anchor_offset_for_open_ms()`, яка при читанні старих барів перебирає `primary → alt → alt2` щоб знайти якір, який математично пасує до `open_time_ms`. Це дозволяє:

- Зберігати бари записані зимовим якорем **поруч** з барами записаними літнім якорем
- Не перебудовувати **всю** історію при DST transition
- Перебудовувати тільки бари **після** дати переходу DST

### Формула bucket alignment

```
bucket_start_ms = ((ts_ms - anchor_offset_ms) // tf_ms) * tf_ms + anchor_offset_ms
```

### H4 grid (приклад)

| Сезон | UTC якір | H4 bars (UTC) |
|-------|----------|---------------|
| **Літо (EDT)** | 22:00 (79200) | 22:00, 02:00, 06:00, 10:00, 14:00, 18:00 |
| **Зима (EST)** | 23:00 (82800) | 23:00, 03:00, 07:00, 11:00, 15:00, 19:00 |

### D1 boundaries

| Сезон | UTC якір | D1 bar |
|-------|----------|--------|
| **Літо (EDT)** | 21:00 (75600) | 21:00 → 21:00 (= 17:00 EDT = NY close) |
| **Зима (EST)** | 22:00 (79200) | 22:00 → 22:00 (= 17:00 EST = NY close) |

---

## Історія DST-переходів

| Дата | Перехід | Хто зробив | Примітки |
|------|---------|-----------|----------|
| 2026-03-31 | Зима → Літо (US DST з 2026-03-08) | AI agent | Перший перехід. Запізнились на ~3 тижні. H4/D1 rebuilt від 2026-03-08 для XAU/USD і XAG/USD |

> Додавати рядок при кожному наступному DST переході.
