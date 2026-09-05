# ADR-0054: Multi-Symbol Re-Activation Plan — Phased Rollout with Regression Net

- **Статус**: **Accepted (rev 2)** — owner «ADR-0054 так» 2026-09-05; rev 2 приземлено 2026-09-06 після ревізії проти живого коду
  (6 лінз + 9 adversarial-верифікацій) і адверсаріального ревʼю драфту (3 лінзи). Rev 1 (Proposed, 2026-04-24) збережено як історію рішення;
  місця, де rev 1 застаріло, позначені **[rev 1 → rev 2]**.
- **Дата**: 2026-04-24 (rev 2: 2026-09-06)
- **Автор**: Patch Master (GitHub Copilot, «Стас-агент») — rev 1; Fable 5.1 + Станіслав (owner) — rev 2
- **Initiative**: `multi_symbol_reactivation_v1`
- **Supersedes (partial)**: ADR-0025 §"Rollback" (replaces vague "повторити audit/rebuild" with concrete phased plan)
- **Related**: ADR-0002 (derive-only HTF), ADR-0023 §"Multi-symbol rollout", ADR-0037 (Binance multi-symbol precedent, тепер OFF),
  ADR-0038 (virgin backfill — **erratum rev 2 §1.7**), ADR-0005 (mid-session gap tolerance),
  `docs/runbooks/fxcm_credential_rotation.md` §6-7b (offline seed/rebuild recipe), `docs/runbooks/dst_transition.md`

---

## Quality Axes

- **Ambition target**: R3 (architectural — встановлює safety contract для multi-symbol expansion на наступні ~3 місяці)
- **Maturity impact**: M3 → M4 (elevates — додає regression net + isolation guarantee, без яких розширення = регрес). Rev 2: якщо Фаза 2 чиста і закриті S1-патчі §3.6 → M4 → M5 (structural isolation evidence, не дисципліна).

---

## 0. Rev 2 (2026-09-06) — що змінилось і чому

Owner-контекст 05.09.2026: «додаватимемо по одному символу, кожен окремо й незалежно, тому що зачепивши одне, ломається щось інше.
Я витратив 2-3 місяці, втомився й вимкнув ті символи». Binance (BTC/ETH) **вимкнено** (`83b38ae`).
Форма плану rev 1 (три фази, один символ за раз, regression net, stop-rule) підтверджена як правильна. Чотири несучі припущення rev 1 більше не тримають:

| Rev 1 припускав | Реальність 2026-09-05 (доведено кодом/записами) | Rev 2 |
|---|---|---|
| `data_v3/` містить історію 15 символів | Каталоги 11 dormant-символів **видалені**; лишились `XAU_USD, XAG_USD, BTCUSDT, ETHUSDT` | Кожен символ = **virgin** → нова **Фаза 0** (offline seed) |
| Phase 2.5 (ADR-0038) засіє virgin FXCM-символ | `m1_poller.py:659` гейт `fetch_m1_range`, якого FXCM-провайдер/проксі не мають → `INITIAL_BACKFILL_SKIP`, символ стартує з `tail_fetch_n=5` барів | Erratum ADR-0038 §1.7; seed лише offline |
| Підозрюваний №1 `_derived_tail_state.json` — shared state | Файл **мертвий** з 2026-02-19 (writers знято в `c439954`, `033cec9`); але `gate_coldstart_multisymbol.py:139` його вимагає → FAIL для кожного нового символу | Викреслено; sub-gate прибирається у Фазі 0 |
| Per-group D1 anchor = блокер перед W4 | Спростовано: D1 = 1440×M1 (`core/derive.py:34`); глобальні якорі = кінець daily-break (H4 22:00) або всередині нього (D1 21:00) для кожної групи; симуляція EU — 0 втрат | Інваріант «хвилина перед якорем неторгова» (§3.5); справжній блокер FX/HK — `has_range` |
| Активація через `python -m app.main --mode all` | Прод = supervisor-група `smc` (`tools/smc-v3.supervisor.conf`), 4 процеси читають `symbols` при старті, hot-reload нема | Рецепти переписано під supervisor |
| Baseline = 4 символи GREEN | Baseline = **XAU/USD, XAG/USD**; дірка 24.07→04.09 закрита offline 05.09 | §1.1 оновлено |

Головний висновок ревізії для owner-болю «полагодив A → зламалось B»: **записані механізми лютого-квітня — це спільний стан процесів, а не дані окремих символів** (§1.6). Тому Фаза 2 перевизначена з «ребілд одного символу» на «рестарти + дублікати» — саме те, що ламало.

---

## 1. Контекст і проблема

### 1.1 Поточний стан **[rev 1 → rev 2: оновлено на 2026-09-05]**

`config.json:symbols` = **2 активних символи**: `XAU/USD`, `XAG/USD` (FXCM, demo-логін оновлено 05.09). Binance вимкнено (`binance.enabled=false`, supervisor `autostart=false` для `smc-binance*`, дані `data_v3/BTCUSDT|ETHUSDT` збережені, `binance.symbols` лишено для re-enable).

`data_v3/` містить **4** каталоги символів. Історичні дані 11 dormant-символів (EUSTX50, GBP/CAD, GER30, HKG33, NAS100, NGAS, NZD/CAD, SPX500, US30, USD/CAD, USD/JPY) **відсутні** — реактивація = virgin-старт кожного.

Дірка XAU/XAG 2026-07-24 → 2026-09-04 (FXCM demo протухло ще 24.07) закрита 05.09 offline за runbook: пейджований `fetch_tf_backfill --tf 60` + `rebuild_from_m1 --force` при зупиненому ingest, потім restart + re-prime (по ~41.3k M1 на символ).

### 1.2 Історія (ADR-0025, Feb 2026)

Планувалась поетапна активація мульти-символьної роботи (Потік B). Audit показав:

- ✅ M1 дані чисті для всіх 13 символів (3-5 місяців історії) — **[rev 2: ці файли видалені, аудит стосується неіснуючих даних]**
- ✅ 0 дублікатів
- ✅ Calendar mapping налаштований для всіх груп (`fx_24x5_utc_summer`, `cfd_us_22_23`, `cfd_eu_21_07`, `cfd_hk_main`, `crypto_24x7`) — **[rev 2: назва FX-групи у config = `fx_24x5_utc_summer`, не `fx_24x5`]**
- ✅ Архітектура multi-symbol — всі воркери ітерують `config.json:symbols`
- ❌ **Derived TF integrity порушена** (M5/M15/H1/H4/D1 побиті після rebuild для не-XAU символів)

**Рішення тоді**: відкласти Потік B, залишитись на XAU/USD до фіксу integrity issues.

### 1.3 3-місячний emergent pattern (Feb–April 2026)

Спостереження користувача:

> "Робиш роботу над символом A → ламається символ B → ремонтуєш B → ламається C. Цикл триває 3 місяці."

**Діагностичний висновок rev 1**: символи не ізольовані, десь є shared state. **Rev 2 — вердикти по підозрюваних проти коду 2026-09-05:**

| # | Suspect (rev 1) | Вердикт rev 2 | Доказ |
|---|---|---|---|
| 1 | `_derived_tail_state.json` | **REFUTED** — мертвий файл | writers знято `c439954` (19.02), `033cec9` (28.02); єдиний читач — `gate_coldstart_multisymbol.py:139` (sub-gate, що завалить кожен новий символ) |
| 2 | Redis namespace `v3_local` | **Частково**: ключі не колізують (`redis_keys.symbol_key`, tf-термінатор), але всі wipe-шляхи namespace-wide (`replay.py:188`, `tools/diag/clear_redis_cache.py`), per-symbol reset немає; `status:snapshot` — один слот на процес | operator-level драйвер циклу |
| 3 | Bootstrap "all symbols" loops | **CONFIRMED (структура)**: `m1_poller.py:954-975, 978-992, 1053-1084, 1098-1122` — один `try` навколо циклу всіх символів; виняток на символі i пропускає прайм/catchup для i+1..n | наслідок пом'якшений (disk-reads захищені); per-symbol try = патч Фази 1 |
| 4 | Shared derive worker | **REFUTED (дані)**: `derive_engine.py:97-113` буфери/локи/UDS/календарі per-symbol; лише `check_overdue_buckets` ітерує set без per-symbol try | патч ≤10 LOC |
| 5 | `_HTFRunningAccumulator` | **REFUTED**: `tick_preview_worker.py:142-175` per-symbol dict; єдиний спільний стан — глобальні `_anchor_offsets_ms` per tf | — |
| 6 (new) | **Symbol-blind observability** | **CONFIRMED**: `runtime/obs_60s.py:19-27` лічильники без symbol; `redis_snapshot.py` `gap_state`/`status:snapshot` один слот на процес → регресію не атрибутувати | патч ≤30 LOC Фази 1 |
| 7 (new) | **Одна FXCM-сесія + серіалізований proxy** | **CONFIRMED (S2)**: `m1_ingestion_worker.py` BLPOP 15s, sidecar `_MAX_BARS_PER_CMD=200`; ніколи не працювало з >2 FXCM символами; storm-режим спостережено 05.09 (§1.6 п.5) | S1-патчі §3.6 перед W2 |
| 8 (new) | **Ручний DST-якір** | **CONFIRMED**: `market_calendar.py:71-96` фіксовані UTC HH:MM без tz; `docs/runbooks/dst_transition.md` = ручний перемикач ≥12 полів config + purge/rebuild ВСІХ символів | §3.5 DST-вікна |

### 1.4 Чому "просто додати символ" — антипатерн

Без isolation guarantee кожне додавання символу = **roulette** на existing символи. Це порушує I3 (Final > Preview), I5 (Degraded-but-loud), F9 (Craftsmanship-First). ADR-0025 закрив проблему тимчасово (XAU only). Цей ADR відкриває її назавжди через systematic plan.

### 1.5 Бізнес-контекст

| Продукт | Audience | Symbol scope | Чому |
|---|---|---|---|
| **Архі (trader-v3/)** | 1 owner | XAU only | Поки Архі не доведе edge на одному символі — розширення = розфокус. **Rev 2**: Арчі зараз OFF (маркер `ops/archi-off`); платформа НЕ має розширювати його wake-поверхню автоматично (§3.7) |
| **V3 chart UI (ui_v4/)** | Trader community | Wider basket | Більше символів = ширша audience |

Цей ADR стосується **тільки V3 platform/UI**. Жодних змін у `trader-v3/` (X31).

### 1.6 Задокументовані механізми «fix A → break B» (rev 2, з changelog/commits лютого-квітня)

Джерело: `git show 267e0b2^:changelog.jsonl` (975 записів по 2026-03-24; файл був tracked до `267e0b2` 28.03, далі untracked/gitignored,
тому пізніші записи в git відсутні), commits, `docs/audit/`.

1. **Shared bootstrap/writer state.** Фікс D1 для BTC/ETH (`742eda7`, 2026-03-15) скинув watermark **усім** символам → того ж дня S0:
   59 403 дубльованих рядків JSONL (`76504d6`). Два одночасні `m1_poller` → 2 560 дублікатів у 64 файлах.
   Механізм = **рестарти і спільний writer**, не дані символу.
2. **Broker-direct HTF поверх derived.** H4/D1 з FXCM-сітки (src=history) впереміш із derived → подвійні H4 на 11/13 символів,
   криві D1-віки (changelog 20260220-046 ff.). Guard є у `fetch_tf_backfill.py:86-91` (лише H4),
   але `tools/repair/htf_rebuild_from_fxcm.py` досі пише src=history H4/D1.
3. **Ручний DST-якір.** Перемикання ≥12 полів config + purge/rebuild усіх 13 символів двічі в один день (20-21.02); alt-якорі прикручені пізніше.
4. **Календарні баги EU/HK/NGAS** (wrap-break, lunch breaks HK, тригер H4 19:00, illiquid gaps) — **виправлені з тестами/гейтами в лютому** і не повторювались, але валідовані лише на лютневих даних і DST-статичні.
5. **05.09.2026 (свіже):** після відновлення FXCM-логіну sidecar дренив ~25k застарілих команд з Redis-черги `broker:m1:cmd`
   (без TTL, накопичених за 11 днів мертвого демо) по ~58 history-викликів/с; сирота `broker_sidecar` (ppid=1) пережила `supervisorctl stop`.
   Механізм: обробник SIGTERM у sidecar **є** (`broker_sidecar.py:89-92`, реєстрація `:407-408`), але **кооперативний** — прапор `_running=False`
   перевіряється лише між ітераціями `while _running:` (`:503`), cooldown = блокуючий `time.sleep(30)` (`:532`), і прапор не перебиває
   блокуючий виклик ForexConnect [INFERRED: сирота не писала `CONNECT_FAIL` після 21:28 — зависла в login; потребувала `SIGKILL`].
   `app.main._kill_tree` (`app/main.py:415-443`) робить `killpg` по власній групі → app.main гине першим, ескалація `proc.kill()` не настає,
   supervisor бачить вихід головного процесу і KILL не шле. Прямий ризик блокування FXCM-акаунта при кожній кризі логіну;
   масштабується з кількістю символів.

### 1.7 Erratum ADR-0038 (rev 2)

Матриця ADR-0038 «Asset Type Matrix» позначає FXCM-активи як «Phase 2.5 ✅ (якщо virgin)». Це **хибно**: Phase 2.5 gated на `fetch_m1_range`
(`m1_poller.py:659`), якого немає ні в `FxcmHistoryProvider` (лише `fetch_last_n_m1`, `fetch_last_n_tf`), ні в `BrokerRedisProxy`;
sidecar приймає лише `fetch_m1` з `_MAX_BARS_PER_CMD=200`. Для FXCM virgin-символ отримує 0 історії;
`initial_backfill_skipped=provider_unsupported` навіть не потрапляє в `degraded[]` (I5-дірка). Правильний рядок: FXCM Phase 2.5 = ❌
(див. erratum-нотатку в ADR-0038).

---

## 2. Альтернативи

### A. Status quo — продовжуємо XAU/XAG-only

- **Verdict**: REJECT — суперечить продуктовій стратегії (chart ≠ trader).

### B. "Big bang" — активувати всі 11 символів одночасно

- **Verdict**: REJECT — повторює помилку ADR-0025; blast radius = усі символи; неможливо локалізувати регресію. Rev 2 додає: усі 11 virgin → big bang = 11 offline-seed одночасно на одній FXCM-сесії (§1.3 №7).

### C. Per-symbol weekly rollout БЕЗ regression net

- **Verdict**: REJECT — це і є той цикл, який мучив 3 місяці.

### D. Phased rollout WITH regression net + isolation investigation (CHOSEN, rev 2 розширено Фазою 0)

- **Pro**: detection-first; кожна фаза має stop-rule; rev 2 перевіряє саме задокументовані механізми (§1.6), не гіпотетичні.
- **Con**: ~3 місяці до повного rollout; потребує дисципліни PRE/POST на кожній дії.

### E. (rev 2) Структурна ізоляція — per-calendar-group supervisor-програми + `--symbols` фільтр у воркерах

- **Pro**: «незалежність» стає властивістю процесу, не дисципліни; збій групи EU не зачіпає US.
- **Con**: одна FXCM-сесія на логін (sidecar спільний) — ізоляція часткова; зміни у 4 воркерах + supervisor; окрема архітектурна робота.
- **Verdict**: **DEFER → окремий ADR** після Фази 2 (§7 п.1). Не блокер для W1-W3.

---

## 3. Рішення (rev 2)

Чотири фази, **кожна з explicit gate**. Порядок: 3.0 (видалення) вже виконано; далі Фаза 0 → 1 → 2 → 3.

### 3.0 Процедура видалення символу (rev 2; виконано для BTC/ETH 05.09)

K5-гейт rev 1 («`symbols` не змінюється до Accepted+Фаза 1») стосується **додавань**. Видалення code-safe (перевірено: `binance.enabled` гейт у воркерах, api_v3 allowlist, UI отримує `meta.config.symbols`). Процедура:

1. `config.json`: прибрати символ із `symbols[]`; для Binance — `binance.enabled=false` (список `binance.symbols` лишити).
2. Supervisor: `autostart=false` для програм, що більше не потрібні (у репо `tools/smc-v3.supervisor.conf` + живий conf на VPS; **живий conf містить секрети в `environment=` — правити точково, не копіювати шаблон**).
3. `sudo supervisorctl reread && sudo supervisorctl update` (група `smc` перезапускається) → observation ≥120s.
4. Redis: ключі знятого символу лишити на TTL (1-7 днів) або зачистити за списком §5 п.3. Диск `data_v3/<SYM>/` **не видаляти**.
5. UI: збережена пара клієнта може вказувати на знятий символ → фолбек на серверний дефолт (`ui_v4` `320ef50`).
6. Health на решті символів = PRE для наступних дій (05.09 виконано вручну: існуючі gates + live round-trip;
   з Фази 1 — `symbol_health_check` [TO-BE-BUILT]).

### 3.1 Фаза 0 — Virgin Seed + prerequisite-патчі (NEW, 1 тиждень)

**Ціль**: зробити засів історії virgin FXCM-символу відтворюваною offline-процедурою і прибрати відомі пастки інструментів. Без Фази 0 жоден крок Фаз 2-3 не виконуваний (§0).

**P0.1 `tools/fetch_tf_backfill.py` (S2, ≤40 LOC)**: `DERIVED_ONLY_TFS = {180,300,900,1800,3600,14400,86400}` (лише `--tf 60` без
`--force-derived-tf`; зараз guard тільки на H4, а прямий D1 уже дав anchor-інцидент — runbook `fxcm_credential_rotation.md:321`);
`--repeat N`/`--from <ISO>` для ланцюжкового backward-fetch з паузою; підсумок first/last/holes.
До патчу — пейджинг руками через `--date-to` (працює, доведено 05.09: 9 викликів × n=8640 на символ).
**P0.2 `tools/rebuild_from_m1.py` (S2)**: `--force` без `--start` мовчки пропускає dedup-on-finish (`:611-622`, `if _start_ms == 0: continue`)
→ fail loud або брати `head_first_bar_time_ms` як start. Запускати **тільки** для символу поза config або при зупиненому
`smc-fxcm`/`smc-ticks`/`smc-preview` (append без lock у ті самі part-файли, що й live writer).
**P0.3 `gate_coldstart_multisymbol.py`**: прибрати sub-gate 3 `derived_state_covers_all` (мертвий файл); `data_v3/_derived_tail_state.json` → `data_v3/_audit/` (D13.5: лише цей файл).
**P0.4 Fail-fast календаря (I5)**: символ із `symbols` без групи в `market_calendar_symbol_groups` або з невідомою групою →
`CALENDAR_GROUP_MISSING` ERROR + `degraded[]`, для FXCM-воркерів — не стартувати символ
(зараз `tick_preview_worker.py:738-741`, `m1_poller.py:1380-1386` тихо працюють 24/7 без календаря).
**P0.5 (бажано, разово)**: `FxcmHistoryProvider.fetch_m1_range()` + sidecar-операція → ADR-0038 Phase 2.5 і historical crawl автоматично вмикаються для FXCM. Якщо зроблено — Фаза 0 для наступних символів скорочується до «додати в config».
**P0.6** Зафіксувати константу глибини FXCM M1-історії: один пробний виклик `--tf 60 --n 1000 --date-to <now-60d>` на NAS100; результат → `docs/config_reference.md`.

**Процедура seed per symbol** (символ **ще не** в `config.json:symbols`):

1. Підтвердити точну назву інструмента FXCM і групу календаря (`market_calendar_symbol_groups`), для EU/HK — живі години (§3.5).
2. `.venv37/bin/python -m tools.fetch_tf_backfill --tf 60 --symbol <NEW> --n 8640 --date-to <T>` ланцюжком назад до цілі глибини:
   **≥30 торгових днів M1** (≈6 тижнів; для `cfd_us_22_23` це ~180 H4 і ~30 D1 — як 41.3k M1 XAU/XAG 05.09;
   `smc.lookback_bars=500` на H4/D1 для virgin недосяжний — фіксується як `D1_THIN` YELLOW, не блокер).
   **Fetch M1 з FXCM — лише у вікно закритого ринку (Sat 00:00 → Sun 21:45 UTC) або при зупинених `smc-fxcm`/`smc-ticks`**:
   `fetch_tf_backfill` відкриває другу ForexConnect-сесію тим самим логіном поверх живого sidecar.
3. `.venv/bin/python -m tools.rebuild_from_m1 --symbol <NEW> --start <seed_start> --end <today+1d> --force` (end-exclusive).
4. **Тільки M1 з FXCM.** Прямий fetch HTF заборонений (§1.6 п.2); `tools/repair/htf_rebuild_from_fxcm.py` → `tools/_archive/`.
5. Перевірка засіву існуючими інструментами: `rebuild_from_m1` без `SKIP symbol=`, `tools/dedup_derived_jsonl.py` dry-run = 0 дублікатів,
   `gate_coldstart_multisymbol` (без sub-gate 3) OK. Повна перевірка `symbol_health_check --young` [TO-BE-BUILT, Фаза 1] — у Фазі 2 п.2.

**Exit gate Фази 0**: P0.1-P0.4 змержені з тестами; NAS100 засіяно (п.5); XAU/XAG без нових дублікатів і без `DERIVE_REJECT` за 24h
(існуючі gates). Повний baseline GREEN = exit gate Фази 1, не Фази 0 (порядок 0→1→2 не циклічний).

### 3.2 Фаза 1 — Regression Net (spec rev 2, 3-5 днів)

**Deliverable [TO-BE-BUILT]**: `tools/symbol_health_check.py` = тонка I/O-оболонка над pure `core/health/` (календар інжектується як
`is_trading_fn`; нуль `runtime`-імпортів у `core`). Усі прапорці нижче (`--baseline`, `--compare`, `--gate-symbols`, `--young`,
`--list-redis-keys`) — специфікація, не існуючий CLI. Кожен вимір — pure-функція з юніт-тестом на синтетичних `CandleBar`.

Входи per (symbol, TF ∈ `tf_allowlist_s`, включно 60 і 86400): диск через `DiskLayer.read_window_with_geom`
(останні 7 календарних днів плюс `head_first_bar_time_ms`), Redis через `RedisLayer.read_tail_or_snap`
(**лише** `ohlcv:tail|snap:{symbol_key}:{tf}`, ніколи `status:snapshot`, бо це один слот на процес), config через `load_system_config`.

| # | Вимір | Що рахує | Reuse |
|---|---|---|---|
| 0 | config sanity | `set(symbols) ⊆ market_calendar_symbol_groups`, група ∈ `market_calendar_by_group`; **якір на межі/всередині break**: `is_trading_minute(anchor − 1min) == False` для H4 і D1 (break напіввідкритий `[start, end)`, `market_calendar.py:24-32`: 22:00 = перша торгова хвилина); сезон vs `ZoneInfo('America/New_York')` → `CALENDAR_SEASON_MISMATCH` = RED | `gate_tick_preview_calendar.py:41-44` |
| 1 | bucket_age | очікуваний останній закритий бакет − last open на диску, у бакетах | `_expected_closed_m1_calendar`, `bucket_start_ms` |
| 2 | holes | `expected_bucket_opens − existing`, класи unexplained / known_outage / boundary_slip / ignore_minute; **з дискової сітки, не з `gap_state`** (tail_catchup стрибає watermark і чистить gap_state — `m1_poller.py:745-776`) | `tools/diag/classify_m5_gaps.py` (той самий класифікатор, що `gate_unexpected_gap_budget`) |
| 3 | geometry | exact_dup, near_dup (D1, `tf_ms//12`), unsorted, close_bad, align_bad через `select_anchor_offset_for_open_ms` (alt-якорі DST легальні); **`jsonl_duplicate_lines` per (symbol,tf,day)** | `tools/repair/dedup_jsonl_lastwins.py` (імпортує `rebuild_from_m1.py:601`), `tools/dedup_derived_jsonl.py` dry-run; `tools/dedup_jsonl.py` — gitignored, не для VPS |
| 4 | cascade | re-derive кожного stored `src=derived` бакета через `GenericBuffer`/`derive_bar`/`DERIVE_ORDER`/`resolve_cascade_anchor_s`, точна рівність OHLC; `partial` → skipped; **`src_purity`: src≠derived на TF≥M3 = RED** (§1.6 п.2) | `core/derive.py`; замінює локальний ad-hoc `scan_broken_bars.py` (gitignored, толерантність 0.5, без календаря) |
| 5 | redis ≡ disk | зсув last open у бакетах; OHLC на перетині після конверсії `close_ms+1`; ключ відсутній при фіналах на диску = RED — **детектор підозрюваного №3** | `RedisLayer.read_tail_or_snap` |
| 6 | history/smc_ready | `history_depth_days`, `m1_bars ≥ bootstrap.initial_backfill_m1_bars`, `tf_14400 ≥ 120`, `tf_86400 ≥ 20`; молодий символ (`health.young_symbol_days`) → YELLOW, не RED; `virgin_guard`: символ у config без ≥1440 M1 = RED | — |
| 7 | process/IPC | `LLEN {ns}:broker:m1:cmd` (>50 = RED), сироти `broker_sidecar` з ppid=1, `BROKER_PROXY_TIMEOUT`/хв, `tick_last_age_s` з config-allowlist «no-tick» символів, per-symbol `poll_cycle_ms` | логи + `ps` |

**Grading**: RED = align_bad|close_bad|exact_dup|cascade_mismatch|redis-OHLC-mismatch > 0, age > 3 бакетів у торгові години,
нові unexplained holes у вікні PRE→POST, IPC-storm; YELLOW = age 2-3, near_dup, src_mix, молодий символ; GREEN інакше.
Вихід JSON `{ts, config_sha, symbols:{sym:{tf:{dims, grade}}}}`; `--baseline PRE.json`, `--compare PRE.json --gate-symbols <ACTIVE>`
— exit code (0/1/2) рахується **лише** по gate-символах.

**Prerequisite-патчі Фази 1 (обов'язкові, ≤30 LOC кожен):**

- `m1_poller.py` фази 1/2/2b/2c: `try/except` **всередину** per-symbol циклу, `bootstrap_degraded.append(f"{phase}:{sym}: {exc}")`.
- `runtime/obs_60s.py` + `redis_snapshot.py`: лічильники drops/geom-fix і `gap_state` **keyed by symbol**.
- `derive_engine.py check_overdue_buckets`: per-symbol try (`OVERDUE_CHECK_ERR symbol=`).

**Exit gate Фази 1**: tool + тести; baseline `XAU/USD, XAG/USD` = 100% GREEN (після закриття дірки 05.09); tool у workflow — перед/після кожної config-зміни та `rebuild_from_m1`. Якщо baseline не GREEN → STOP, лікувати існуючі.

### 3.3 Фаза 2 — Isolation Probe (перевизначено, 3-5 днів)

**[rev 1 → rev 2]** Rev 1 тестував ізоляцію даних («видалити `data_v3/NAS100/tf_*`, rebuild, diff») — неможливо (NAS100 нема) і не влучає в записані механізми (§1.6 = рестарти + спільний writer). Rev 2:

1. `health_check --baseline HEALTH-A` на XAU/XAG (GREEN).
2. Фаза 0 для NAS100 (seed + rebuild) **при живих** XAU/XAG, але fetch M1 — у вікно закритого ринку (сб/нд) або при зупинених
   `smc-fxcm`/`smc-ticks` (одна FXCM-сесія на логін); `rebuild_from_m1` для символу поза config — будь-коли.
3. NAS100 у `config.json:symbols` — **останнім у списку** (щоб збій його bootstrap не передував існуючим);
   `sudo supervisorctl restart smc:smc-fxcm smc:smc-ticks smc:smc-preview smc:smc-ws` (явний перелік, не `smc:*` — див. §3.4 п.4).
4. **3 послідовні рестарти** тих самих чотирьох програм з інтервалом ≥ одного повного M5-бакета + tail-catchup.
5. `health_check --compare HEALTH-A --gate-symbols XAU/USD,XAG/USD`: дублікати JSONL = 0, кількість derived-барів незмінна, redis≡disk, cmd-черга 0, сиріт нема, `OVERDUE_CHECK_ERR`/`DERIVE_REJECT` = 0 за 24h.

| Результат | Інтерпретація | Дія |
|---|---|---|
| Нуль регресій на XAU/XAG | Process-level isolation достатня для one-at-a-time | Фаза 3 |
| Регресія | Знайдено contamination point — зафіксувати symbol/tf/phase з логів (тепер symbol-keyed) | STOP → патч + повтор Фази 2; за потреби ADR «Structural isolation» (§7 п.1) |

### 3.4 Фаза 3 — Per-Symbol Rollout (по 1/тиждень)

**Порядок хвиль (rev 2, з реальними блокерами):**

| Тиждень | Symbol | Calendar group | Стан/блокер (rev 2) |
|---|---|---|---|
| W1 | **NAS100** | `cfd_us_22_23` | Та ж група, що XAU. Блокерів немає. `known_broker_outages` уже містить NAS100 (2026-02-04) |
| W2 | **SPX500** | `cfd_us_22_23` | Перед W2 — S1-патчі §3.6 (третій FXCM-символ на одній сесії) |
| W3 | **US30** | `cfd_us_22_23` | — |
| W4 | **GER30** | `cfd_eu_21_07` | Per-group anchor **не** блокер (спростовано). Невизначеність: чи зсуваються години FXCM EU-CFD за DST (runbook каже «фіксований» без доказу) → «calendar shadow» 3 торгові дні: `ticks_dropped_calendar_closed`, перший/останній M1 vs календар. За графіком — DST-freeze §3.5 |
| W5 | **EUSTX50** | `cfd_eu_21_07` | Як W4; за графіком — DST-freeze §3.5 |
| W6 | **USD/JPY** | `fx_24x5_utc_summer` | **Блокер §3.5**: патч `GenericBuffer.has_range` перед W6 |
| W7 | **GBP/CAD** | `fx_24x5_utc_summer` | — |
| W8 | **NZD/CAD, USD/CAD** | `fx_24x5_utc_summer` | Разом лише якщо W6/W7 чисті |
| W9-10 | **HKG33** | `cfd_hk_main` | Найскладніший: 2 lunch breaks; **той самий патч §3.5** (HK втрачає 9.4% хвилин у H4 без нього) |
| W11 | **NGAS** | `cfd_us_22_23` | Illiquid, `MAX_MID_SESSION_GAPS` tuning на лютневих даних — перевалідувати |

**W = порядковий номер хвилі, не календарний тиждень.** Freeze-тижні DST (EU 2026-10-25, US 2026-11-01, обидва — неділі) вставляються
між хвилями; за орієнтовним графіком (Фаза 0 з 08.09, W1 ≈ 28.09) вони припадають на W4-W5 (EU-група) — там і потрібна перевірка сезону (§3.5).

**Per-symbol activation procedure (rev 2):**

1. Фаза 0 seed для `<NEW>` (символ поза config; fetch M1 — у вікно закритого ринку або при зупинених `smc-fxcm`/`smc-ticks`).
2. `health_check --baseline PRE.json` на всіх активних = GREEN (інакше STOP).
3. `<NEW>` у `config.json:symbols[]` **останнім**; коміт; на VPS `git pull` (deploy = git, ADR-0060).
4. `sudo supervisorctl restart smc:smc-fxcm smc:smc-ticks smc:smc-preview smc:smc-ws` — явний перелік у цьому порядку
   (`supervisorctl` обробляє імена послідовно; у conf нема `priority=`). **Не** `smc:*`: wildcard підняв би `autostart=false`
   `smc-binance*` у BACKOFF/FATAL, бо воркери виходять миттєво при `binance.enabled=false`, і забруднив би `supervisorctl status`.
   Observation 60-120s. Агрегатні докази bootstrap: `M1_POLLER_REDIS_PRIME symbols=N+1`, `DERIVE_ENGINE_WARMUP symbols=N+1`;
   per-symbol докази для `<NEW>`: `UDS_PRIME_SUMMARY symbol=<NEW>`, `HTF_SEED symbol=<NEW>`, `SMC_WARMUP_M1_OK sym=<NEW>`,
   `M1_TAIL_CATCHUP symbol=<NEW>`; відсутність `INITIAL_BACKFILL_SKIP symbol=<NEW>`, `DERIVE_ENGINE_WARMUP_ERR symbol=<NEW>`, `BOOTSTRAP_DEGRADED`.
5. `health_check --compare PRE.json --gate-symbols <ACTIVE>`: будь-який RED або погіршення виміру на активному символі = **immediate rollback** (§5).
6. Trader smoke: overlay/зони рендеряться, свіжість, drawings. UI сам отримує список з `meta.config.symbols` — змін у `ui_v4` не потрібно.
7. Watch 3-5 торгових днів з age-scaled порогами (молодий символ), лише потім W+1.
8. **Ніяких активацій у тижні DST-переходу** (§3.5).

### 3.5 Календарі, якорі, DST (rev 2)

- **Per-group D1 anchor — не блокер.** Інваріант, який тримає коректність: якір збігається з кінцем daily-break (перша торгова хвилина
  сесії — як H4 22:00 у `cfd_us_22_23`, бо break `[21:00, 22:00)` напіввідкритий) або лежить усередині break (D1 21:00); формально
  `is_trading_minute(anchor − 1min) == False`. Перевіряється виміром 0; тест-специфікація `test_anchor_at_break_edge_us_fx_eu_hk`
  (4 групи × 4 якорі літо/зима). Окремий «ADR-005X» **не потрібен** для W4 (rev 1 §Per-Group D1 Anchor скасовано).
- **Справжній блокер FX/HK (S1, патч ≤40 LOC у `core/derive.py:172-206`)**: `GenericBuffer.has_range/range_bars/missing_count`
  вирішують торговість слота джерела за **першою хвилиною** слота. Сесійний reopen не на межі H1 (FX 21:30; HK 01:15 і 09:15)
  викидає цілий H1 з H4: HKG33 −9.4% торгових хвилин у H4, FX −2.1%, частково мовчки (`ext={}`); preview-H4 ці хвилини містить
  (preview ≠ final). Фікс: предикат «слот торговий, якщо в ньому ≥1 торгова хвилина» (reuse `_has_any_trading_in_range`).
  Тести-специфікації: `test_derive_h4_includes_partial_first_slot_hk_0115`, `_fx_2130`. Знахідка лінзи із симуляцією —
  **верифікувати adversarially до патчу**. Health-вимір: збереження обсягу M1→H4.
- **DST**: перемикання EU 2026-10-25 і US 2026-11-01 (обидва — неділі) за орієнтовним графіком rev 2 припадають на W4-W5
  (rev 1 планував W7-W9). Тиждень переходу = **freeze** (жодного нового символу): PRE у п'ятницю → перемикання за runbook → POST
  у понеділок. Автоматизація сезону (America/New_York → якорі/години) = окремий ADR-кандидат «DST-auto», бажано до 25.10.
- **Performance gate**: цифра 27M `is_trading` викликів/день з ADR-0023 базується на знятому D1-шляху; кешу в `market_calendar.py` немає (`is_trading_minute` парсить інтервали на кожен виклик). Перед W4 **переміряти**, не успадковувати.

### 3.6 Операційні S1-патчі broker-IPC (rev 2, перед W2)

З інциденту 05.09 (§1.6 п.5), окремий патч-цикл під цим ADR:

1. Команда в `broker:m1:cmd` несе `ts_ms`; sidecar дропає команди старші за proxy-timeout → `BROKER_SIDECAR_CMD_STALE_DROPPED` (I5), реплай не робиться.
2. Поллер перед `rpush` перевіряє `LLEN`; при заторі — WARN + skip, не додає.
3. Sidecar: існуючий кооперативний обробник (`broker_sidecar.py:89-92`) зробити дієвим під блокуючими викликами — login/history через
   watchdog-таймаут або окремий потік з `_running`-перевіркою, interruptible cooldown замість `time.sleep(30)` (`:532`);
   `app.main._kill_tree` для прямих дітей — `os.kill(pid, SIGTERM)` + ескалація `proc.kill()` після timeout (не `killpg` по власній групі,
   від якого app.main гине першим); тест `test_app_main_supervisor` «дитина не переживає stop».
4. Sidecar при (re)connect чистить чергу команд.

### 3.7 Поверхня Арчі (rev 2)

`ws_server.py:3621` передає у WakeEngine **весь** `config.json:symbols` → кожен новий chart-символ автоматично генерує wake-події Арчі
у спільний `wake:events`, а per-symbol помилки тіку глушаться на DEBUG (`wake_engine.py:137-141`). Rev 2: config SSOT `wake_engine.symbols`
(default `["XAU/USD"]`), споживається замість глобального списку; помилки тіку → WARNING з per-symbol throttle. Це platform-side,
X31 чистий; узгодити з trader-v3 лише як факт («Арчі бачить тільки свій список»).

---

## 4. Наслідки

### Pro

- ✅ Розриває цикл через detection-first **саме на задокументованих механізмах** (рестарти, дублікати, src-purity, IPC-storm), а не на гіпотетичних.
- ✅ Кожен символ додається з explicit gate; можна зупинитись на будь-якому тижні.
- ✅ Арчі ізольований структурно (`wake_engine.symbols`), не за домовленістю.
- ✅ Знання про 8 підозрюваних зафіксоване з вердиктами — клас гіпотез «shared state у даних» закрито.

### Con

- ⚠️ Фаза 0 додає ~1 тиждень і потребує 4 інструментальних патчів до першого символу.
- ⚠️ Кожна активація = повний рестарт групи `smc` (немає hot-reload) → коротка пауза для XAU/XAG; structural isolation винесено в окремий ADR.
- ⚠️ DST-вікна 25.10 і 01.11 — два freeze-тижні.

### Risks (rev 2)

| Risk | Severity | Mitigation |
|---|---|---|
| health_check сам має bug → false GREEN | M | Pure-функції з юніт-тестами на синтетиці; spot-check на XAU; вимір 4 порівнює з `core.derive`, а не з власною агрегацією |
| Rollback лишає Redis із partial state | H | §5 п.3 — перелічений список ключів через `symbol_key`, ніяких ручних patterns |
| FXCM history depth < 30 днів для деяких CFD | M | P0.6 проба; ціль глибини знижується явно з `D1_THIN` YELLOW |
| Паралельна FXCM-сесія (offline seed) вибиває live-сесію sidecar | M | Seed лише при зупинених `smc-fxcm`/`smc-ticks` **або** для символу поза config у вікно закритого ринку (вихідні) |
| Stale IPC-черга/сирота sidecar при кризі логіну | H | §3.6 перед W2; вимір 7 health_check |
| EU-години FXCM зсуваються за DST всупереч runbook | M | W4 «calendar shadow» 3 дні; вимір 0 сезон |

### Maturity progression

- M3 → M4: health_check (observability, symbol-keyed), вердикти по shared state, explicit gates.
- M4 → M5: якщо Фаза 2 чиста + §3.6 закриті → production-grade isolation evidence.

---

## 5. Rollback

Rollback ADR = відмовитись від плану (ADR-0025 stance: XAU/XAG only).

Per-symbol rollback (Фаза 3), **rev 2 під supervisor і нормалізовані ключі**:

```bash
# 1. config: прибрати <SYMBOL> із symbols[] (git revert коміту активації → git pull на VPS)
# 2. рестарт чотирьох програм у явному порядку (НЕ smc:* — wildcard підняв би autostart=false smc-binance* у FATAL)
sudo supervisorctl restart smc:smc-fxcm smc:smc-ticks smc:smc-preview smc:smc-ws
# 3. Redis cleanup — ключі нормалізовані redis_keys.symbol_key ('/'→'_'); частина ключів symbol-terminal
S=$(echo '<SYMBOL>' | tr / _)
for p in "v3_local:ohlcv:*:$S:*" "v3_local:preview:*:$S:*" "v3_local:updates:*:$S:*" "v3_local:tick:last:$S" "v3_local:wake:conditions:$S" "v3_local:thesis:$S"; do
  redis-cli -n 1 --scan --pattern "$p"
done   # переглянути список → потім xargs -r redis-cli -n 1 del
# 4. health_check --compare PRE.json --gate-symbols <REMAINING> == PRE
```

`symbol_health_check --list-redis-keys <SYM>` [TO-BE-BUILT] має друкувати саме цей список (SSOT = `redis_keys.symbol_key`), щоб runbook
не збирав patterns руками. **[rev 1 → rev 2]**: pattern `v3_local:*:<SYMBOL>:*` з rev 1 не ловив символи зі слешем і symbol-terminal
ключі. Disk `data_v3/<SYMBOL>/` **не видаляємо**.

---

## 6. Verification (after each phase, rev 2)

| Phase | Verify | Expected |
|---|---|---|
| 0 | P0.1-P0.4 tests; NAS100 seed: `rebuild_from_m1` без SKIP + `dedup_derived_jsonl` dry-run = 0; XAU/XAG існуючі gates | patches green; seed ok; без нових дублікатів/`DERIVE_REJECT` |
| 1 | `python -m tools.symbol_health_check --symbols XAU/USD,XAG/USD --baseline PRE.json` | exit 0, all GREEN |
| 2 | 3 рестарти + `--compare HEALTH-A --gate-symbols XAU/USD,XAG/USD` | 0 дублікатів, derived count unchanged, redis≡disk, cmdq 0 |
| 3 (кожен W) | `--compare PRE.json --gate-symbols <ACTIVE>` після activation + через 3-5 днів | усі раніше-GREEN символи GREEN |

---

## 7. Open Questions (rev 2)

1. **Structural isolation** (альтернатива E): per-calendar-group supervisor-програми (`smc-fxcm-us`, `smc-fxcm-eu`, …) + `--symbols` у `m1_ingestion_worker`/`tick_publisher_fxcm`/`tick_preview_worker` при спільному sidecar. Окремий ADR після Фази 2.
2. **FXCM M1 history depth** — константа після P0.6 (05.09 доведено ≥6 тижнів для XAU/XAG).
3. **EU-CFD години vs DST** — підтвердити живими лічильниками у W4; результат → `docs/config_reference.md`.
4. **`fetch_m1_range` для FXCM** (P0.5) — робити до W1 чи після Фази 2?
5. ~~Чи треба separate ADR для per-group D1 anchor~~ — **знято** (rev 2 §3.5).
6. Чи ввімкнути health_check у CI як гейт проти PR, що змінює `symbols` — рішення після Фази 1 (як у rev 1).

---

## 8. Notes

- **Статус Accepted (rev 2)**. K5: `config.json:symbols` **не розширюється** до завершення Фази 1 (baseline GREEN) — видалення §3.0 дозволено.
- **Cross-repo isolation (X31)**: platform scope. Жодних змін у `trader-v3/`; §3.7 — зміна на боці платформи.
- **Continuation**: цей ADR = SSOT plan. Наступна ревізія — тільки як rev 3 з датованим changelog, не переписування «з нуля».
  Повний протокол ревізії rev 2 (6 лінз, 9 верифікацій, 15 агентів) — у сесії 2026-09-05/06 (memory `platform_resume_2026_09_05`).
- **Пастки з практики 05.09** (для виконавця Фаз 0-3):
  - `.env` на VPS з CRLF → логін FXCM падає («Incorrect user name or password»);
  - upload і launch скриптів окремими ssh-сеансами (фоновий `cat` читає `/dev/null` → нульовий файл);
  - origin-side smoke для `aione-smc.com` — тільки `:80` з `Host:` (443 на origin = default-сайт archi);
  - живий supervisor-conf містить секрети в `environment=` — не друкувати і не копіювати шаблоном.

---

## Changelog

- 2026-04-24: Created (Proposed). Documents 3-month emergent pattern + 3-phase plan. Supersedes ADR-0025 §"Rollback".
- 2026-09-05: Owner accepted («ADR-0054 так»); Binance OFF (`83b38ae`); дірка XAU/XAG закрита offline; workflow-ревізія проти коду (6 лінз + 9 adversarial verify).
- 2026-09-06: **rev 2 (Accepted)**. §0 таблиця змін; §1.1/1.2 оновлено на 2 символи і virgin-стан; §1.3 вердикти по 5 підозрюваних + 3 нові;
  §1.6 задокументовані механізми лютого-квітня + інцидент 05.09; §1.7 erratum ADR-0038; альтернатива E (defer); §3.0 видалення;
  **Фаза 0** (seed + P0.1-P0.6); Фаза 1 spec (8 вимірів, prerequisite-патчі); Фаза 2 перевизначена (рестарти + дублікати);
  Фаза 3 під supervisor, хвилі з реальними блокерами, DST-freeze; §3.5 per-group anchor знято, блокер `has_range`;
  §3.6 broker-IPC S1; §3.7 `wake_engine.symbols`; §5 rollback з `symbol_key`.
- 2026-09-06: правки за адверсаріальним ревʼю драфту (3 лінзи): SIGTERM-handler sidecar існує (кооперативний) — §1.6 п.5/§3.6 п.3 переписано;
  `restart smc:*` → явний перелік 4 програм (wildcard підняв би smc-binance* у FATAL); Фаза 0 exit gate без інструмента Фази 1;
  інваріант якоря = «хвилина перед якорем неторгова» (break напіввідкритий); seed лише у вікно закритого ринку; глибина 30 торгових днів
  = ~180 H4/~30 D1; DST-freeze на W4-W5; gitignored `dedup_jsonl.py`/`scan_broken_bars.py` замінено tracked-інструментами;
  changelog provenance і `rebuild_from_m1.py:611-622` виправлено; `.github/copilot-instructions.md` row 0054 → Accepted (rev 2).
