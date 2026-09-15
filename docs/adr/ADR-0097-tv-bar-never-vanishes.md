# ADR-0097: Свічки як у TradingView — бар будується з наявних хвилин і закривається лише за підтвердженими даними

## Метадані

| Поле | Значення |
| ---- | -------- |
| ID | ADR-0097 |
| Статус | **Proposed** (2026-09-15). P0 = цей документ. Кожен слайс коду і кожен крок міграції даних — окреме «го» власника |
| Дата | 2026-09-15 |
| Автори | Станіслав (owner: «не викидаємо, має бути як у TradingView»; «TradingView не показує пласкі бари») + Claude Opus 5 (дизайн v1 → критик → дизайн v2 → атака трьома лінзами: live-outage, persistence-contract, data-migration → ця фінальна редакція) |
| Номер | 0097 (реєстр `docs/adr/index.md`, рядок «Наступний номер» після цього ADR → 0098). 0095 зарезервовано під сезонний ремонт derived, 0096 = FIRST_TICK |
| База коду | main `4c085b7` (прод) + `20f1700` (`_slot_has_trading`, гілка `fix/derive-slot-any-trading-minute`) + `1128454`, `c86a707` (`m1_session_filter`, гілка `fix/backfill-forming-bar`). Обидві гілки мерджаться до P1 |
| Замінює (supersede) | ADR-0005 (бюджет пропусків — у режимі `tv`); ADR-0023 D-06 (бюджет D1 за TF); ADR-0092 шар 2 (`D1GapPolicy`), §1.3 («бюджет = DST-детектор»), §7.1 п.2, «жорстке правило» шару 3 |
| Доповнює | ADR-0002 (ланцюг деривації лишається для `legacy`), ADR-0013b (маркери partial), ADR-0092 шар 3 (G1 `season_guard`, G2 `calendar_intrusion`), ADR-0094 (один вибирач), ADR-0096 (слайс C — ремонт derived), ADR-0054 W4 (GER30/EUSTX50) |
| Поважає | I0 (core чистий), I1 (один writer, контракт UDS не змінюється), I2, I3, I5 (кожна відмова, очікування і втрата — іменована подія), config SSOT, X9, X28 (графік не перераховує), X33 (`uds.py` 2399 рядків, `m1_poller.py` 1615) |
| Зачіпає шари | `core/` (derive, derive_cover NEW, derive_close NEW, health), `runtime/ingest/` (derive_engine, derive_closer NEW, derive_policy NEW, polling/m1_poller, polling/m1_frontier NEW, m1_ingestion_worker, replay, binance_ingest_worker), `runtime/store/uds.py`, `tools/` (rebuild_from_m1, repair/purge_derived_window NEW, symbol_health_check, diag/d1_gap_anatomy), `config.json`, runbook DST |
| Initiative | `tv_bars_v1` |

---

## Quality Axes

- **Ambition target: R3.** Похідна свічка кожного TF дорівнює агрегату наявних торгових хвилин M1 — правило
  TradingView «є хоч одна хвилина — є бар». Деградацію видно на рівні бару (`m1_cover`, `gap_suspect`), а не
  через те, що бару нема. Не R4: сезон (DST) і журнал простою поллера лишаються зовнішніми передумовами.
- **Maturity impact: M3 → M4.**
  - Бакет закривається за підтвердженими даними, а не за годинником.
  - У `tv` один шлях закриття і одне джерело (M1): гонка тригера з overdue, розбіжність буфера з диском і
    втрата маркерів дітей стають структурно неможливими.
  - Тихі `None`, `stale`, `duplicate`, обрізаний backlog і запізніла хвилина стають іменованими подіями.
  - DST-детектор перестає бути побічним ефектом бюджету і стає явним вимірюваним сигналом.

---

## 0. Рішення власника

1. **«Не викидаємо, має бути як у TradingView».** Бар будується з наявних хвилин. Бакет без жодної хвилини
   бару не має.
2. **«TradingView не показує пласкі бари… зазвичай вони приходять після закриття».**
   - Графік ховає пласкі свічки, як на проді: `candle_map._is_display_flat_bar`, v ≤ 10 [VERIFIED
     runtime/ws/candle_map.py:40-60].
   - Пласкі хвилини поза сесією не пишуться: `classify_m1_for_ssot` [VERIFIED backfill-forming-bar
     runtime/ingest/m1_session_filter.py, `c86a707`].
   - Цей ADR графіка не змінює.
3. **Делеговано агенту.**
   - **R1 (DST).** Бар із хибним якорем будується і отримує на рівні бару YELLOW (`session_edge_run`). RED дає
     лише детектор повторюваності: однаковий крайовий прогін ≥ N сесій поспіль — дата-свідок у сімействі G1
     `season_guard` ADR-0092.
   - **R2 (сліпий кут).** Дірки коротші за 45 хв у ліквідний час приймаються. Видимість — `m1_cover.cnt/exp`.
     Точний сигнал — окремий ADR довговічного журналу простою поллера. Сюди ж (G7) другий сліпий кут: `feed_outage`
     процес-локальний, тож бакет, зібраний уже після рестарту, що перекрив простій, цієї причини не отримає —
     довговічний журнал закриває обидва.
   - **Б5 (поза календарем).** Непласкі хвилини поза календарем у бар не входять: календар — SSOT сесії. Кожна
     така хвилина рахується, логується і потрапляє в health.

Як закрито кожен пункт критика (Б1–Д20) і атаки (LO1–LO6, PC1–PC5, DM1–DM8) — §12.

---

## 1. Контекст

### 1.1 Бюджет відкидає бакети, в яких є хвилини

- `MAX_MID_SESSION_GAPS=3` і `{86400: 15}` [VERIFIED core/derive.py:54-57]. Перевищення →
  `_collect_boundary_tolerant` → `None` → `derive_bar` → `None`. Без календаря будь-яка дірка дає `None`.
- Реально кусає лише M5 і D1: у M3, M15, M30, H1 і H4 не більше 4 слотів. Ці TF зникають каскадом.
- Масштаб на локальних даних (TV == агрегат M1 на всіх TF у 9 прогонах, miss=0, mism=0, extra=0)
  [VERIFIED terminal scratchpad/wf_budget_scale/*.out]:

| Символ | Втрачено D1 | Інші втрати |
|---|---|---|
| XAU (169 сесій) | 18 | M5 29; змінено M15–H4 14–16 |
| XAG | 18 | M5 37 |
| GER30 [INFERRED, кандидат-календар 00:30–20:00] | 11/102 (11.04% обсягу D1) | M5 11 |

### 1.2 Бюджет випадково робить дві чужі роботи

**(а) Єдиний DST-детектор.** Зимові дані XAU під літнім конфігом: сьогодні 0/88 D1, без бюджету 88/88 без
жодного сигналу [VERIFIED terminal, recon]. `season_guard` і `D1_NOT_BUILT` у коді відсутні.

**(б) Єдине гальмо проти білду з хвилин, що не доїхали.** Overdue закриває бакет за годинником
(`cur_bucket = bucket(now)`) [VERIFIED runtime/ingest/derive_engine.py:282-306]. Приклад: XAU 08.06
`OVERDUE_DERIVE_OK` о 21:00:14 → `duplicate` для хвилини 20:59 о 21:01:08; M3…D1 без 20:59 [VERIFIED recon
derive-live]. Сигнатура урізаного хвоста на диску: M5 70, M15 32, H1 13, H4 9, D1 6.

### 1.3 Супутні дефекти (сьогодні, `legacy`)

1. `_cascade` кладе в буфер бар, який UDS відкинув [VERIFIED derive_engine.py:355-357, 447-461].
2. Строгий шлях не успадковує маркери дітей [VERIFIED core/derive.py:427-442].
3. Overdue мовчить на `None`, `stale`, `duplicate` [VERIFIED derive_engine.py:305-306, 323]; `DERIVE_SKIP` без M3
   [VERIFIED :406].
4. Replay не має overdue [VERIFIED runtime/ingest/replay.py:394-401].
5. Rebuild будує крайові бакети з неповних M1 [VERIFIED tools/rebuild_from_m1.py:280-281], дублікати M1 бере
   last-wins [VERIFIED :274], тримає в пам'яті ≤ 100 000 M1 з FIFO-витісненням [VERIFIED :267;
   core/derive.py:157-160] — на повній історії XAU (225 901 M1) жовтень–лютий мовчки не будується
   [VERIFIED terminal scratchpad/wf2_data/buf_cap.out].
6. Поллер при відставанні > 120 бере найновіші [VERIFIED m1_poller.py:138, 261]; **`tail_catchup` просить до
   5000 [VERIFIED m1_poller.py:720; config.json:20], а sidecar ріже до 200** [VERIFIED
   runtime/ingest/broker_sidecar.py:80, 211]: простій 300 хв → 100 хвилин втрачено назавжди, watermark
   перестрибнув [VERIFIED terminal scratchpad/wf2_livepath/sim_tail_cap.out]. Частота на проді — [UNKNOWN —
   risk: M].
7. Запізніла M1 нижче watermark відфільтровується без лічильника [VERIFIED m1_poller.py:377-378, 501-505,
   768-772].
8. `_disk_bar_to_candle` губить `extensions` при читанні з диска [VERIFIED runtime/store/uds.py:127-139], хоча
   `to_dict` їх пише [VERIFIED core/model/bars.py:60-61].
9. Overdue щохвилини повторює `derive_bar` для неторгових бакетів; з `20f1700` це ×2.4 CPU, у перервах ×6–9 (S3)
   [VERIFIED wf_review.md, lens perf].
10. Порядок у циклі поллера: кожна M1 пакета комітиться і одразу йде в `on_bar` (тригер); overdue — лише після
    циклу по всіх поллерах, не частіше ніж раз на 60 с [VERIFIED backfill-forming-bar m1_poller.py:294-305,
    382-383, 1195-1206]. Будь-який «закривач» поза `on_bar` програє гонку тригеру наступного бакета (§3.3,
    LO1/PC1).

### 1.4 Хто читає маркери

- Поведінку міняють лише: `bar_choice` (правило 3 — лише `extensions.partial`) [VERIFIED
  core/model/bar_choice.py:48-56]; health (`_declares_partial`) [VERIFIED tools/symbol_health_check.py:99-112];
  soft-penalty `partial_penalty = 1 − source_count/expected_count` [VERIFIED uds.py:1832-1846].
- Шлях disk/RAM несе `extensions` у dict до `candle_map` [VERIFIED uds.py:1827-1829], але вихід `candle_map`,
  `/api v3` і Redis їх не несуть [VERIFIED runtime/store/redis_snapshot.py:116-139; uds.py:1912-1937;
  атака persistence-contract, grep api_v3]. SMC і TDA маркерів не читають.
- **Схожість на TV визначає лише одне: чи бар існує і чи він дорівнює агрегату M1.**

---

## 2. Альтернативи

### A. Зняти бюджет (`MAX = ∞`)
Відхилено. У live урізаний final фіксується масово (overdue за годинником); DST стає тихим (88/88); дірки в
ліквідний час — тихі «повні» бари. Порушує I5.

### B. Шар 2 ADR-0092 (`D1GapPolicy`, відмова відновлювана)
Відхилено власником: суперечить «не викидаємо»; покриває лише D1; відмова в live відновлюється лише rebuild.

### C (v1). Бар з наявних хвилин + grace + ревізія derived у UDS
Відхилено. Grace під простоєм фіксує урізаний бар (Б1); маркери не переживають рестарт (Б3); ранжування ревізій
у `bar_choice` за `m1_count` розводить шляхи читання (у Redis поля нема — повтор проблеми ADR-0094).

### C2 (дизайн v2). Бар з наявних хвилин + ланцюг M1→M5→…→H4 + курсор закриття поруч із тригером
Відхилено за атакою. Курсор, що біжить **поза** `on_bar`, програє гонку тригеру наступного бакета: попередній
бакет відкидається як `stale`, батьки комітяться урізаними назавжди (LO1/PC1, симуляція на реальних
`DeriveEngine` і `_watermark_drop_reason`: M15 20:45 v=500 проти 700, … H4 18:00 8750 проти 8950) [VERIFIED
terminal scratchpad/wf2_livepath/sim_outage_race.out сценарій A; wf2_invariants/batch_stale.py]. Горизонт
рестарту в годинникових бакетах дитини (M5 = 30 хв) коротший за паузу XAU → той самий результат без простою
(LO2) [VERIFIED sim_outage_race.out сценарій B]. Ланцюг вимагає ще й домінування дітей, маркерів дітей з диска
і буферів derived, узгоджених із SSOT — три окремі механізми, кожен з власною дірою.

### D. Provisional derived через preview-канал, final після settle
Відхилено: preview лише для TF ≥ H4 [VERIFIED runtime/ingest/tick_preview_worker.py:749]; для M5…H1 потрібен
новий канал; final запізнюється для всіх барів; preview не переживає рестарт.

### C3 (обрано). Кожен TF — прямо з M1; один курсор закриття всередині `on_bar` і після fetch; frontier лише з підтверджених даних

**Суть:**
- У `tv` кожен похідний TF будується **безпосередньо з буфера M1** (агрегат торгових хвилин бакета). Ланцюг
  ADR-0002 лишається лише для `legacy`. Буфер M1 уже тримає 10 080 хвилин (7 діб) [VERIFIED
  derive_engine.py:51-58], що покриває D1 (≤ 1440) і будь-який менший TF.
- **Один шлях закриття**: курсор на (symbol, tf) проходить синхронно в `on_bar` після кожного коміту M1 і після
  кожної спроби fetch. Тригерів у `tv` нема. Коміти кожного TF ідуть строго за часом → watermark UDS не може
  випередити незакритий бакет.
- Бакет CLOSED, коли frontier (закомічена M1 або підтверджений fetch + settle) пройшов його останню торгову
  хвилину.
- Контракт UDS (I1/I3) не змінюється. `mode=legacy` — байт-у-байт поточний код; `mode=shadow` — вердикти без
  коміту.

**Чому пряма агрегація безпечна:**
- OHLC агрегату M1 тотожний агрегату через проміжні TF; `v` — сума з точністю float, у межах допуску health.
- Покриття, обчислене прямо з хвилин, дорівнює згортці через ланцюг: 0 розбіжностей на 7 TF × 4 вікна
  [VERIFIED terminal scratchpad/wf2_cover/monoid_check.py, `*.out`] — отже частоти маркерів §3.1.3, виміряні
  для v2, чинні й для C3.
- Критерій приймання «`tv` == REF_cal» стає визначенням, а не наслідком каскаду.
- CPU: H4 = 240 M1, D1 = 1380 M1 — раз на бакет; порожні бакети курсор проходить один раз (§3.3).

**Мінуси:**
- Final на закритті сесії, коли остання хвилина без угод, запізнюється до `m1_settle_s` (XAU D1 2/67).
- Сліпий кут дірок < 45 хв (R2).
- Хвилина, що доїхала після закриття бакета, у live — лише лічильник. Контракт ревізії — окремий ADR за виміром.
- `legacy` і `tv` мають різні внутрішні шляхи (ланцюг проти прямої агрегації) до повного переходу.

**Як відрізнити «брокер не відповів» від «барів нема» (Б1):**
- (i) proxy кидає виняток — міняє лічильники `poll_once`/reconnect/`live_recover`, FXCM provider усе одно
  ковтає помилку [VERIFIED runtime/ingest/broker/fxcm/provider.py:169-174]. Неповно.
- (ii) Sentinel-тип відповіді — ламає контракт `List[CandleBar]` трьох провайдерів.
- **(iii) Overlap proof (обрано)**: не залежить від провайдера, не змінює контракт, закритий за замовчуванням.

---

## 3. Рішення (C3)

### 3.1 Правило збирання (core, чисте)

#### 3.1.1 `GapPolicy`

- `derive_bar(*, …, gap_policy=GapPolicy.LEGACY, suspect=None, loss_windows=())`. `LEGACY` = поточний код
  байт-у-байт (golden-тест).
- **`TV`:**
  1. Потрібен `is_trading_fn`, інакше `ValueError("derive_tv_requires_calendar")` (С11).
  2. Джерело — лише M1 (`source_tf=60`) для будь-якого target TF; інше джерело → `ValueError`.
  3. Бар = агрегат усіх присутніх M1 у торгових хвилинах бакета; бюджету нема. `None` лише коли таких M1 нема.
     Пласкі хвилини паузи (`calendar_pause_flat`) фільтруються, як сьогодні [VERIFIED core/derive.py:275-276].
  4. Присутні M1 у **неторгових** хвилинах бакета в агрегат **не входять**; їх кількість —
     `extensions.off_calendar_m1` (Б5).
  5. Повний бар (`cnt == exp`) — **без жодних нових extensions** (як сьогоднішній строгий бар). Приросту диска на
     повних барах нема (PC3).
  6. Неповний бар (`cnt < exp`): `partial=true`, `partial_reasons=["m1_gaps"]`, `source_count=cnt`,
     `expected_count=exp` (одиниці — M1 для всіх TF; `partial_penalty` в uds.py:1839-1844 стає точним),
     `m1_cover` (§3.1.2). `boundary_partial` і `mid_session_gaps` у `tv` не пишуться: їх замінює покриття.
     Домен правила 3 вибирача не змінюється — воно читає лише `partial`.
  7. `extensions.gap_suspect: [..]` — лише непорожній (§3.1.3).

#### 3.1.2 `m1_cover` — покриття хвилинами (новий `core/derive_cover.py`)

Одиниця — торгова хвилина за календарем. Обчислюється одним обходом торгових хвилин бакета.

| Поле | Зміст |
|---|---|
| `exp`, `cnt` | очікувані торгові хвилини / присутні M1 серед них |
| `run` | найдовший прогін відсутніх торгових хвилин (включно з хвилинами відкриття/закриття сесії, Д14) |
| `edge_open` | найдовший прогін, що починається на хвилині відкриття сесії (попередня хвилина неторгова) |
| `edge_close` | найдовший прогін, що закінчується на хвилині закриття сесії (наступна неторгова) |

- **Прогін** — максимальна послідовність відсутніх торгових хвилин t, t+60 с, …; неторгова хвилина його
  розриває.
- Серіалізація короткими ключами `{"exp","cnt","run","edge_open","edge_close"}` лише на неповних барах
  (XAU M5: 197 з 31 830 [VERIFIED terminal атака PC3]).
- FXCM не віддає хвилину відкриття сесії → структурний `edge_open=1` на бакетах через відкриття. Поріг 45 цього не
  зачіпає; `cnt/exp` — інформація.

#### 3.1.3 `gap_suspect` (YELLOW на рівні бару)

| Причина | Умова |
|---|---|
| `long_run` | `run ≥ derive_policy.gap_suspect_long_run_min` (45). SSOT переїжджає з `tools/diag/d1_gap_anatomy.py:53` у config; анатомія читає config (D15.2) |
| `session_edge_run` | `max(edge_open, edge_close) ≥` той самий поріг (DST, ранні закриття) |
| `off_calendar_m1` | `off_calendar_m1 > 0` |
| `feed_outage` | бакет перетинається з `loss_windows` поллера (§3.2), лише в межах процесу |

- `long_run` на M5–M30 структурно неможливий (`exp < 45`); там дірку видно як відсутній чи неповний бар і
  `cnt/exp` (R2).
- Виміряні частоти (поріг 45) [VERIFIED terminal wf2_cover/*.out; повторено незалежно wf2_data/suspect.out]:

| Вікно | D1 | H4 | H1 | M5 |
|---|---|---|---|---|
| XAU літо | `long_run` 3, `session_edge_run` 1 (02.04, закриття) | `long_run` 4 | `long_run` 1 | 0 |
| GER30 [INFERRED календар] | 0 | 0 | 0 | 0 |
| XAU зима під літнім конфігом | edge 87/88, серії 80 і 7 | edge 86/507 | — | — |
| XAG зима під літнім конфігом | edge 88/88, серія 88 | edge 88/506 | — | — |
| XAU/XAG зима під **правильним** конфігом; NAS100/SPX500 20.04–10.09 | свята дають поодинокі edge з боками, що чергуються; max серія одного боку = 1 | — | — | — |

### 3.2 Frontier підтверджених M1 (поллер, runtime)

Логіка — у новому чистому модулі `runtime/ingest/polling/m1_frontier.py` (`FrontierTracker`, без I/O, свій
тест). `m1_poller.py` лише викликає його (X33: мінімум правок у файлі > 1500 рядків).

**Визначення.** `wm` — watermark M1 поллера. Fetch F: запит `n` барів з `date_to = C + 60 с` (як сьогодні
[VERIFIED m1_poller.py:354, 475, 742]) о wall W; сира відповідь R до фільтрів.

**Запит.** `n = min(gap + 2, page_cap)`. `+2`, а не `+1`: включність `date_to` у FXCM не виміряна, і провайдер
може віддати бар з `open == C + 60 с` [VERIFIED provider.py:257-268; `1128454`]. З `+2` доказ правильний за будь-
якої включності (LO6). `page_cap = MAX_FETCH_N = 120` для всіх трьох шляхів (нижче за sidecar 200).

**Підтвердження.** **F підтверджений ⇔ R ≠ ∅ ∧ wm_before ≠ None ∧ min(open(R)) ≤ wm_before.**
Обґрунтування: брокер віддає n останніх барів ≤ `date_to`; у (wm, C + 60 с] не більше gap + 1 барів (по одному
на хвилину, хвилин без тіків нема); отже (gap + 2)-й бар від кінця лежить ≤ wm. Якщо його нема — відповідь
неповна: таймаут, затор, помилка SDK, обрізання будь-де (proxy, sidecar, `n`).

**Непідтверджений fetch:**
- `R = ∅` → лог `M1_FRONTIER_UNCONFIRMED symbol reason=<proxy.last_fetch_outcome|empty>` (dedup) + лічильник;
  frontier стоїть.
- **`R ≠ ∅` і `min(open(R)) > wm_before` — незалежно від `n`** → `M1_BACKLOG_TRUNCATED` WARN +
  `loss_windows.append((wm_before + 60 с, min_open))` (deque, TTL 2 доби) (LO3). Сьогоднішній код бари все одно
  комітить і watermark стрибає; у `tv` derive ставить `feed_outage`. Для `tail_catchup`/`live_recover` цей випадок
  прибирає пагінація (нижче), тож лишається лише справжня аномалія.

**Пагінація `tail_catchup` і `live_recover` (LO3, DM4).** Замість одного запиту на `min(missing, 5000)` —
сторінки за cutoff: `C_k = min(C, wm_k + (page_cap − 2)·60 с)`, `n = page_cap`, кожна сторінка перевіряється
overlap proof, `wm_k` — watermark після коміту сторінки. Сумарний бюджет лишається `tail_catchup_max_bars`
(5000) і `live_recover_max_total_bars` [VERIFIED config.json:20]. Непідтверджена сторінка → стоп пагінації + WARN,
далі звичайний `poll_once`. Ціна: до ~42 сторінок на символ при максимальному простої (секунди–хвилини на
старті); звичайний рестарт — 1 сторінка.

**Settle.** Лише для підтвердженого F:
`settled_through ← max(settled_through, min(C, floor_min(W − m1_settle_s) − 60 с))`. Хвилина остаточна, коли
підтверджений fetch відбувся не раніше, ніж через `m1_settle_s` після її закриття (лаг публікації брокера).

**`frontier = max(wm, settled_through)`**, монотонний.

**Запізніла хвилина нижче watermark (LO5).** Трекер тримає кільце відкриттів останніх `page_cap + 2` закомічених
M1. Бар з R, у якого `open ≤ wm_before`, `open` у межах кільця і його нема в кільці → `M1_LATE_BELOW_WM` (лічильник +
INFO, dedup). Бар не пишеться (контракт поллера не змінюється), але сигнал іде в гейт ревізії (§6). Сліпа зона:
старше за кільце — лише health `cover_stale`/root.

**Гістограма** `m1_commit_lag_s = wall − close_ms` у `stats` — калібрування settle.

**Зміни API:**

| Де | Зміна |
|---|---|
| `BrokerRedisProxy` | адитивна властивість `last_fetch_outcome` ∈ {`ok`,`timeout`,`congested`,`parse_error`,`req_mismatch`,`symbol_mismatch`,`broker_error`}. Тип повернення не змінюється [VERIFIED m1_ingestion_worker.py:96-165]. Поллери опитуються послідовно в одному потоці — гонки нема [VERIFIED m1_poller.py:1178-1183] |
| `M1SymbolPoller` | один хелпер `_fetch_window(gap, cutoff) → (bars_filtered, verdict)` для `poll_once`, `live_recover`, `tail_catchup`; пагінація для двох останніх; `settled_through_ms` |
| `M1SymbolPoller` → `DeriveEngine` | після кожної спроби fetch `engine.note_m1_frontier(symbol, frontier_ms, loss_windows)`; у `legacy` ігнорується |

**Поведінка поллера для `legacy` не змінюється**, крім двох виправлень дефектів, що діють у всіх режимах:
пагінація (закриває втрату > 200 хвилин, §1.3.6) і `n = gap + 2` (на одну хвилину більше в запиті). Фільтри
`open > wm`, лічильники порожніх відповідей, reconnect і «caught up» — ті самі.

**Межа гарантії (Д17).** Frontier захищає лише від поллера. Офлайн-запис M1 нижче watermark
(`repair_m1_gaps`, `fetch_tf_backfill --tf 60`) ловить health `cover_stale`/root (§3.6); ранбук: після ремонту
M1 — purge + rebuild derived для вікна ремонту.

### 3.3 Закриття бакета: один курсор, одне джерело

**`core/derive_close.py` (чисте):**
- `first_trading_minute`, `last_trading_minute(bucket_open, tf_s, is_trading_fn) → Optional[int]`.
- `bucket_close_state(bucket_open, tf_s, frontier_ms, is_trading_fn) → EMPTY | OPEN | CLOSED`: EMPTY — нема
  торгової хвилини; OPEN — `frontier is None or frontier < last_min`; CLOSED — інакше.
- `aligned_window(start_ms, end_ms, anchors_by_tf)` — [min floor, max ceil] за **всіма легальними якорями** кожного
  TF (`day_anchor_offset_s`, `_alt`, `_alt2`, `_d1`, `_d1_alt`) [VERIFIED config.json:150-154] — один хелпер для
  rebuild і purge (С12, PC2).

**`runtime/ingest/derive_closer.py` (NEW)** — лише `tv`/`shadow`:

*Ініціалізація курсора* (після прогріву буфера M1 з диска):
- `cursor[tf] = max(derived_wm(sym, tf) + tf, horizon[tf])`, де `horizon[tf]` = перший бакет TF, **повністю**
  покритий буфером M1 (`bucket_ceil(oldest_m1_in_buffer, tf)`). Горизонт — 7 діб для кожного TF, а не 3–6
  годинникових бакетів (LO2).
- Закомічені бакети (≤ `derived_wm`) курсор **не переглядає**: після рестарту нема duplicate/stale і нема
  шуму лічильників ревізії (LO4, PC4).
- Якщо `derived_wm + tf < horizon[tf]` і в проміжку є **не-EMPTY** бакети → WARN
  `DERIVE_NOT_BUILT reason=beyond_restart_horizon tf count=…` (потрібен rebuild). EMPTY-бакети вихідних і пауз
  тривоги не дають (LO2).
- У `tv` крок bootstrap 2c (реплей 10 080 M1 через `on_bar`) [VERIFIED backfill-forming-bar
  m1_poller.py:1066-1087] **замінюється** одним `advance(frontier = остання M1 на диску)`.

*Прохід `advance(sym, frontier)`* — викликається:
- синхронно в `on_bar` після кожного успішного коміту M1 (`frontier = max(wm, settled_through)`), **до** будь-якої
  іншої роботи з наступною M1 пакета;
- у `note_m1_frontier` після кожної спроби fetch.

Для кожного TF у `DERIVE_ORDER`, поки `steps < max_close_steps_per_pass`:
- EMPTY → курсор +1 (лічильник `empty`);
- OPEN → стоп; якщо `now − (last_min + 60 с) > outage_warn_after_s` → WARN `DERIVE_WAIT_FEED` (dedup на бакет);
- CLOSED → `first_min < oldest_m1_in_buffer` → WARN `DERIVE_NOT_BUILT reason=beyond_buffer`, курсор +1;
  інакше `derive_bar(TV)` з буфера M1 → коміт → курсор +1. `None` (жодної торгової M1) → `not_built_empty_minutes`:
  лічильник, якщо бакет НЕ перетинає `loss_windows`/`feed_outage`, і WARN `DERIVE_NOT_BUILT reason=empty_after_outage`,
  якщо перетинає (G7: інакше ціла доба без хвилин не відрізняється від законного свята, а календар свят не знає —
  передумова ADR-0092), курсор +1.

**Чому гонки LO1/PC1 більше нема.** У пакеті M1 10:05–10:09 після простою коміт 10:05 викликає `advance(10:05)`:
M5 10:00 (остання хвилина 10:04 без угод) CLOSED і комітиться **раніше**, ніж будь-що з бакета 10:05. Для
пакета через закриття сесії (20:58…22:12) `advance` на першій M1 22:0x закриває M5 20:55, M15 20:45, M30 20:30,
H1 20:00, H4 18:00 з M1 напряму, у порядку часу. Watermark кожного TF у UDS зсувається лише курсором цього ж TF,
тож `stale` для незакритого бакета неможливий.

**Коміт у `tv`.** `ok` → далі. `duplicate`/`stale` у `tv` означає іншого writer'а цього ключа (rebuild при
живому writer, ручна правка) → WARN `DERIVE_COMMIT_CONFLICT tf open reason winner_differs=<bool>` (порівняння з
`uds.read_final_bar`), курсор +1. Буферів derived у `tv` нема — розбіжність буфера з диском (Б2) структурно
неможлива.

**`legacy` (P8a, виправлення дефекту §1.3.1, діє без зміни режиму):** `_settle_commit` — один метод для тригера
й overdue. На `duplicate`/`stale` у буфер іде переможець із SSOT (буфер → кеш хвоста → `uds.read_final_bar`);
`DERIVE_REVISION_DROPPED` — лише коли OHLCV відрізняються; `stale` без переможця → `DERIVE_NOT_BUILT
reason=stale_hole` один раз: негативний кеш `(sym, tf, open)` з TTL = overdue lookback, WARN з dedup (PC5).
Лічильники з bootstrap-реплею 2c ідуть у `*_catchup`, не в гейти (LO4).

**Лічильники виду закриття:** `closed_by_data` (wm ≥ last_min) проти `closed_by_settle`.

**Логи I5 у `tv`:** `DERIVE_GAP_SUSPECT` (WARN, dedup, reasons + cover), `DERIVE_NOT_BUILT`
(`beyond_restart_horizon`, `beyond_buffer`: WARN; `empty_minutes`: лічильник), `DERIVE_WAIT_FEED`,
`DERIVE_OFF_CALENDAR_M1`, `DERIVE_COMMIT_CONFLICT`. `DERIVE_SKIP` лишається лише в `legacy`.
`vps_derive_diag.py` фільтрує за префіксом `DERIVE_` — не ламається.

### 3.4 Shadow (Д16)

- Коміт іде по `legacy` без змін.
- Паралельно курсор TV у пам'яті з тим самим frontier будує TV-бар з буфера M1 (без коміту). Похідних
  TV-буферів не потрібно.
- Вердикт — коли існують обидва: TV закрив бакет і legacy-переможець є (буфер → `read_final_bar`). Якщо legacy ще
  нема — бакет у `pending` до `overdue lookback + 120 с`, потім вердикт.

| Вердикт | Зміст |
|---|---|
| `same` | OHLCV збіг |
| `would_build` | legacy `None`, TV бар |
| `legacy_only` | лише legacy |
| `differs` | OHLCV різні (з розбивкою: `legacy_truncated_tail` — legacy без останніх хвилин, `other`) |

- Лічильники в `stats()` + INFO для всього, крім `same` (dedup). Додатково: `closed_by_settle`, `wait_feed`,
  `backlog_truncated`, `late_below_wm`, `m1_commit_lag_s` p50/p99/p99.9, час `advance` на прохід.

### 3.5 Config (SSOT) і фабрика (С10)

```json
"derive_policy": {
  "mode": "legacy",
  "mode_by_symbol": {},
  "mode_by_provider": {"binance": "legacy"},
  "m1_settle_s": 180,
  "gap_suspect_long_run_min": 45,
  "outage_warn_after_s": 900,
  "max_close_steps_per_pass": 1440,
  "season_signature_sessions": 3,
  "season_since_by_calendar_group": {}
}
```

- `mode` ∈ `legacy | shadow | tv`. Без ключа → `legacy`.
- `m1_settle_s=180` — стартове значення до калібрування (p99.9 `m1_commit_lag_s` за shadow, вгору).
- **`season_since_by_calendar_group`** — **дата межі РИНКУ** (перша доба, коли ринок працює за чинним сезоном), а
  **не** дата правки `config.json`. Оскільку ранбук DST ручний, ці дві дати розходяться; тому поле має два
  підключі на групу (G2, ревізія критика):
  `{"cfd_us_22_23": {"market_since": "2026-03-08", "applied_at": "2026-03-31"}}`.
  - `market_since` — межа сезону ринку. Її читає **rebuild** (§3.7): вікно ремонту не може починатись раніше, тож
    свіжі дні з хибним якорем (lag-вікно `market_since … applied_at`) **ремонтуються**, а не блокуються.
  - `applied_at` — коли календар справді став чинним у конфізі. Його читає **health** (§3.6): календарні виміри
    рахуються лише від `applied_at`, тож lag-вікно не дає хибного RED на правильних за старим сезоном даних.
  - `applied_at < market_since` (правка ДО межі — бажаний порядок у ранбуку) → обидва читання беруть
    `market_since`; lag-вікна немає.
  - Свідок сезону (§3.7 п.4) lag-вікна не ловить: там календар уже правильний, тому пін визначення обов'язковий.
  Заповнює власник за журналом ранбука DST у P5 [ASSUMED — verify: `git log -p config.json` за березень 2026 /
  docs/runbooks/dst_transition.md]; ранбук DST отримує крок «оновити обидва підключі» (P5). Читають
  rebuild (§3.7, `market_since`) і health (§3.6, `applied_at`) — одне джерело (DM1, DM5). Коли ADR-0092 G1 / ADR-0095 дадуть сезонний календар,
  поле переїде у фабрику календаря.
- `runtime/ingest/derive_policy.py`: `load_derive_policy(cfg)` + `build_derive_engine(cfg, symbols, calendars,
  provider)`. Використовують усі 4 конструктори [VERIFIED m1_ingestion_worker.py:323; m1_poller.py:1423;
  binance_ingest_worker.py:231; replay.py:282].
- **Пріоритет режимів (G5)**: `mode_by_provider` (примус) > `mode_by_symbol` > `mode`. Одне правило, один тест
  `test_mode_resolution_provider_beats_symbol_beats_global`; два виміри перекриття без правила — та сама пастка
  D15.2, яку §11 декларує закритою.
- `provider="binance"` → примусово `legacy`, WARN `DERIVE_POLICY_FORCED_LEGACY
  reason=frontier_semantics_unreviewed` (binance `enabled:false` [VERIFIED config.json:637-638]).
- Replay: `provider="replay"`, frontier = закомічена M1 (§3.7).

### 3.6 Health (С8, Б5, С7, DM5, DM8)

- **Сезонна межа.** Усі календарно-залежні виміри (root з календарем, `calendar_intrusion`, `cover_stale`,
  `season_signature`) рахуються лише для бакетів з `open ≥ applied_at` групи (§3.5, G2). Раніше — root v2 (без
  календаря), звіт `HEALTH_SEASON_BOUNDARY_SKIPPED count=…`. `season_since_by_calendar_group` відсутній для групи →
  календарні виміри не рахуються, YELLOW `season_since_missing`; **P11c відмовляється стартувати health для живих
  груп без цього ключа** (G6: інакше `HEALTH_MEASURE_VERSION` 2→3 збігається з вимкненими вимірами, старий baseline
  v2 уже не порівнюється [VERIFIED core/health/compare.py:26, 64-67], і між P11c та гейтом G-D нема ні старого
  baseline, ні нових чисел). Так ручне перемикання DST не дає хибного RED на 7-денному вікні
  (виміряно: літо під зимовим календарем — intrusion 23/23 сесій, edge-серія 20 [VERIFIED terminal
  wf2_data/flip_window.out]). Півзастосований ранбук (календар змінено, `season_since` ні) дає RED із підказкою
  «перевір season_since» — це правдивий сигнал.
- **`measure_root_consistency(..., is_trading_fn)`.** M1 фільтруються як у writer (REF_cal). `_matches_aggregate`
  звіряє також `v` (`|Σv − v| ≤ max(1e-6, 1e-9·|v|)`) [сьогодні без v — VERIFIED core/health/measures.py:323-329].
- **`calendar_intrusion`** (G2 ADR-0092): непласкі M1 у неторгових хвилинах **D1-бакетів з `exp > 0`**; одиниця
  серії — такий D1-бакет. Принти у бакетах з `exp = 0` (суботи XAG: 7 із 8 субот, 13–113 хв [VERIFIED terminal
  wf2_data/xag_summer_intrusion.out]) — окремий INFO-лічильник `weekend_prints`, у грейд не йде (DM8).
- **Бари з `m1_cover`** не звільняються через `declared_partial`; `cover.cnt` ≠ кількість календарних M1 бакета →
  `cover_stale` (M1 змінили після збирання, Д17). Бари без покриття (повні або legacy): звіряє root; legacy —
  плюс сигнатура `tail_truncation`.
- **`measure_season_signature(d1_bars | M1)`:** серія ≥ `season_signature_sessions` торгових D1 поспіль з
  `edge ≥ 45` з того самого боку → RED; поодинокі → YELLOW `gap_suspect_edge`; `calendar_intrusion` ≥ N D1-бакетів
  поспіль → RED, поодинокий → YELLOW.
- **`measure_cascade`** як і раніше пропускає бакети з неповним набором дітей [VERIFIED measures.py:362-364]:
  TV-бари з дірками перевіряє root — свідомо.
- `HEALTH_MEASURE_VERSION` 2 → 3 [VERIFIED core/health/compare.py:26]. `_WORSE_IF_UP +=`
  `root.cover_stale`, `root.volume_mismatched`, `season.signature_streak`, `root.calendar_intrusion`. Лічильники
  `gap_suspect` у регресії не йдуть (їх рухає ринок).

### 3.7 Replay і rebuild (Б4, С11, С12, DM1–DM4, DM6, DM7, PC2)

**Replay:** після кожного успішного коміту M1 — `on_bar` → `advance(frontier = open M1)`. У кінці бакети з
`last_min` пізніше за останню M1 лишаються OPEN. У `tv` без календаря — відмова.

**Rebuild `--gap-policy {legacy,tv}`.** `legacy` = поточний код без жодних змін (golden `sim_rebuild_dump.py`,
DM7). Усе нижче — **лише `tv`**:

1. Календар обов'язковий, інакше `REBUILD_CALENDAR_MISSING`, exit ≠ 0.
2. `--start` обов'язковий (без дефолту на голову історії). **`start < season_since` групи → відмова
   `REBUILD_TV_SEASON_MISMATCH`** (DM1). Минулі сезони — лише після ADR-0095 (сезонний календар). Причина:
   зимові D1/H4 на диску правильні (XAU 90/90 з якорем 22:00, 85/87 == REF під зимовим календарем), а `tv` під
   літнім конфігом переписав би 85 з них хибним close і виключив би 4212 M1 [VERIFIED terminal
   wf2_data/winter_regress.out].
3. **План (SSOT для rebuild і purge)** `plan_tv_rebuild(window, calendar, anchors, m1_head, m1_tail)`:
   - вікно розбивається на порції за межами D1 (`--chunk-days`, дефолт 31);
   - для кожної порції M1 вантажаться з `aligned_window` порції у **dict без FIFO**, дублікати — через
     `choose_better_bar` у порядку файла; > `REBUILD_CHUNK_MAX_M1` (конфіг, дефолт 200 000) → відмова
     `REBUILD_CHUNK_TOO_LARGE` до будь-якого purge (DM2);
   - бакет належить порції, що містить його `open` (без подвійного будування);
   - `head_open` — перша торгова хвилина бакета раніша за першу M1 на диску; `edge_open` — остання торгова
     хвилина пізніша за останню M1 на диску. Обидва не будуються і **не прибираються** (DM6).
4. **Свідок сезону у плані:** `season_signature` на D1 плану RED → відмова `REBUILD_TV_SEASON_MISMATCH
   reason=data_witness` (ловить хибний `season_since`).
5. **Dry-run** (той самий план і збирання без `append`) **не** вимагає очищеного вікна (DM3). Звіт на TF:
   `built / empty / head_open / edge_open / gap_suspect{reason} / off_calendar_m1` і різниця з поточним диском
   (читання через вибирач): `new / changed{ohlc,v,markers} / vanished`.
6. **Реальний прогін:** на кожну порцію — перевірка «вікно очищене» (жодного derived-ключа плану, включно з
   near-dup у `tf_ms//12` біля меж і ключами альтернативних якорів), інакше `REBUILD_TV_WINDOW_NOT_PURGED tf keys`
   + підказка purge. `--force` з `tv` заборонено: старий «цілий» урізаний бар переміг би новий partial за правилом
   3 [VERIFIED bar_choice.py:73-90].

**Purge:** `tools/repair/purge_derived_window.py` (NEW) споживає **той самий план**, але прибирає **кожен**
derived-ключ (TF ≠ 60), чий `open_ms` лежить у вирівняному вікні, а не лише бакети плану, **плюс** бар у межах
`tf_ms//12` за кожною з двох меж вікна (G1, ревізія критика). Чому не «лише бакети плану»: near-dedup зливає
суміжну пару D1 **у будь-якому місці вікна читання** (`near_threshold = tf_ms // 12` для D1, merge-цикл по всьому
результату, нічия → **ранній** бар [VERIFIED uds.py:1987-1989, 2023-2040; bar_choice.choose_better_near_duplicate]),
а для H4 `near_threshold = 0`, тож alt-якірний H4 навіть не зливається — на графіку лишаються дві паралельні серії
(22:00 і 23:00). Ключі alt-якорів усередині вікна не належать бакетам плану, тому попереднє формулювання лишало
саме дефект PC2, зсунутий з межі в середину; популяція реальна — локальний XAU D1 змішаний 21:00/22:00, і ручний
ранбук DST створює таке вікно всередині `[season_since, D−1]`. `head_open`/`edge_open` не чіпає. Через
`jsonl_rewrite.rewrite_atomic` (tmp + fsync, бекап жорстким лінком, `os.replace`) [VERIFIED
tools/repair/jsonl_rewrite.py:35-80]; guard `--writers-stopped` як у rebuild [VERIFIED rebuild_from_m1.py:620].

---

## 4. Тести-специфікації

**Старий код** = main `4c085b7` + `20f1700` + `c86a707`. ✗ — тест на ньому падає; ✓ — guard.

| Слайс | Тест | Сценарій → очікування | Старий |
|---|---|---|---|
| P1 | `test_cover_random_presence_matches_bruteforce` | 5 груп config + GER30-кандидат + FX-зима, випадкова присутність → поля == повільний перебір | ✗ |
| P1 | `test_cover_h1_50min_hole_across_m30_boundary` | дірка 50 хв через межу M30 → H1 `run=50`, `long_run` | ✗ |
| P1 | `test_cover_structural_open_minute_edge_open_1_no_suspect` | бракує лише хвилини відкриття → `edge_open=1`, `gap_suspect` відсутній | ✗ |
| P2 | `test_bucket_close_state_empty_open_closed` | 3 стани, XAU D1 з 20:59 без угод | ✗ |
| P2 | `test_aligned_window_covers_all_legal_anchors` | вікно 00:00–00:00 → межі за min/max по `anchor`, `_alt`, `_alt2`, `_d1`, `_d1_alt` | ✗ |
| P3 | `test_tv_every_tf_from_m1_equals_ref_cal` | фікстура M1 → 7 TF == REF_cal (OHLC exact, v у допуску) | ✗ |
| P3 | `test_tv_m5_single_minute_partial_units_m1` | 1/5 M1 → бар = хвилина, `partial`, `source_count=1`, `expected_count=5`, `m1_cover` | ✗ (None) |
| P3 | `test_tv_full_bar_has_no_new_extensions` | усі хвилини → extensions без `m1_cover`/`gap_suspect` | ✗ |
| P3 | `test_tv_d1_scatter_30_gaps_no_suspect` | прогони ≤ 3 → OHLCV == агрегат, `gap_suspect` відсутній | ✗ |
| P3 | `test_tv_d1_dst_edge_run_counts_boundary_minute` | бракує 60 хв від відкриття → `edge_open=60`, `session_edge_run` | ✗ |
| P3 | `test_tv_xau_winter_mondays_fixture_all_edge_marked` | `tests/fixtures/adr0097/xau_winter_mondays_m1_opens.json.gz` (лише open_ms, CI не читає `data_v3`), літній config → 15/15 `session_edge_run` | ✗ |
| P3 | `test_tv_off_calendar_nonflat_excluded_and_counted` | непласка M1 у паузі → не в OHLCV, `off_calendar_m1=1` | ✗ |
| P3 | `test_tv_requires_calendar_raises` / `test_tv_rejects_non_m1_source` | `ValueError` | ✗ |
| P3 | `test_legacy_policy_golden_identical` | фікстури M5/D1/H4 → `LEGACY` == вихід старого коду | ✓ |
| P3 | `test_empty_bucket_none_both_policies` | 0 M1 → None | ✓ |
| P4 | `test_read_tail_candles_preserves_extensions` | запис з `m1_cover` → читання несе поле | ✗ ({}) |
| P4 | `test_read_final_bar_returns_chooser_winner` | whole + пізніший partial → whole | ✗ |
| P5 | `test_factory_used_by_all_constructors_binance_forced_legacy` | 4 сайти → фабрика; binance+tv → legacy + WARN; без ключа → legacy | ✗ |
| P7b | `test_frontier_confirmed_only_with_overlap_n_gap_plus_2` | таймаут/затор/FXCM error `[]` → unconfirmed з reason; `n=gap+2` з перекриттям → confirmed; включний і виключний `date_to` → той самий вердикт | ✗ |
| P7b | `test_any_nonempty_without_overlap_records_loss_window` | R ≠ ∅, min_open > wm, `n` ≠ cap (обрізання в sidecar) → WARN + вікно (LO3) | ✗ |
| P7b | `test_settled_through_requires_wall_after_settle` | підтвердження о m+68 с при settle 180 → frontier < m; о m+248 с → ≥ m | ✗ |
| P7b | `test_late_minute_below_wm_counted` | R містить відсутній у кільці бар ≤ wm → `late_below_wm=1`, не пишеться | ✗ |
| P7c | `test_live_recover_and_poll_request_gap_plus_2` | `n == gap+2` | ✗ |
| P7d | `test_tail_catchup_paginates_with_fake_sidecar_cap_200` | missing 300, fake cap 200 → 0 втрачених хвилин, кожна сторінка підтверджена (DM4, LO3) | ✗ (100 втрачено) |
| P7d | `test_tail_catchup_budget_5000_kept` | missing 6000 → 5000 записано, `M1_TAIL_CATCHUP_TRUNCATED` | ✓ |
| P8a | `test_legacy_duplicate_loads_winner_into_buffer` | диск має бар ≠ derived → буфер == переможець, `revision_dropped` лише на відмінному OHLCV | ✗ |
| P8a | `test_legacy_stale_hole_negative_cache_warn_once` | 60 проходів overdue → 1 WARN, 1 дисковий lookup (PC5) | ✗ |
| P8a | `test_legacy_bootstrap_counters_separate` | урізаний бар на диску + реплей 2c → `revision_dropped_catchup=1`, live=0 | ✗ |
| P8b | `test_partial_outage_no_commit_until_recovery_then_full_bar` (**Б1**) | M1 10:00–10:02, 10:03–10:12 fetch = таймаут → коміту M5 нема; після recover → повний M5, v = Σ | ✗ |
| P8b | `test_batch_recovery_prev_bucket_last_minute_no_trade_is_built` (**LO1/PC1**) | 10:04 відсутня, пакет 10:05–10:09 → на диску M5 10:00 і 10:05, `commit_conflict=0` | ✗ (10:00 stale) |
| P8b | `test_outage_across_session_close_all_tfs_equal_m1_aggregate` (**LO1**, сценарій A) | XAU, 20:59 без угод, простій 20:58–22:12 → M5 20:55…H4 18:00 == агрегат M1 | ✗ |
| P8b | `test_restart_in_pause_before_settle_builds_full` (**LO2**, сценарій B) | зупинка 21:01, рестарт о 21:30/21:50/22:12/22:30 → M5 20:55…H4 18:00 == агрегат M1 | ✗ |
| P8b | `test_restart_does_not_revisit_committed_buckets` (**LO4/PC4**) | рестарт з хвостом посеред D1 → 0 комітів ≤ derived_wm, лічильники ревізії 0 | ✗ |
| P8b | `test_beyond_restart_horizon_counts_non_empty_only` | derived_wm на 9 діб позаду через вихідні → WARN з count лише торгових бакетів; рестарт у паузі → WARN нема | ✗ |
| P8b | `test_tail_delay_below_settle_builds_full_bar` | хвіст 20:57–20:59 затримано k < settle → close 20:59 | ✗ |
| P8b | `test_last_minute_without_trades_closes_by_settle` | 20:59 без угод, fetch здоровий → CLOSED на першому poll ≥ 20:59+60+settle, `closed_by_settle++` | ✗ |
| P8b | `test_cursor_skips_empty_buckets_once` | вихідні → `derive_bar` для неторгового бакета 0 разів за 60 проходів | ✗ |
| P8b | `test_tv_commit_conflict_is_loud` | ключ уже на диску від іншого writer'а → WARN, курсор іде далі | ✗ |
| P8c | `test_engine_tv_mode_has_no_trigger_commits` | `on_bar` у `tv` комітить лише через `advance` | ✗ |
| P8d | `test_shadow_verdicts_without_tv_commit` | legacy None, TV бар → `would_build`, коміт лише legacy; legacy пізніше за TV → `pending` → вердикт | ✗ |
| P9 | `test_replay_builds_bucket_without_last_minute_trade` | остання торгова хвилина M5 без M1 → бар є | ✗ |
| P10a | `test_rebuild_tv_refuses_without_calendar_or_start` | exit ≠ 0 з кодом | ✗ |
| P10a | `test_rebuild_tv_refuses_window_before_season_since` (**DM1**) | `start < season_since` → `REBUILD_TV_SEASON_MISMATCH` | ✗ |
| P10a | `test_rebuild_tv_data_witness_refuses_wrong_season` | зима під літнім конфігом при хибному `season_since` → відмова `data_witness` | ✗ |
| P10a | `test_rebuild_tv_window_larger_than_buffer_first_last_d1_equal_ref` (**DM2**) | 150 000 M1 → перший і останній D1 == REF_cal | ✗ |
| P10a | `test_rebuild_reads_m1_duplicates_via_chooser` | final раніше, non-final пізніше → final | ✗ |
| P10a | `test_rebuild_one_day_window_builds_d1_via_outward_extension` | `start=D, end=D+1` → D1 D−1 21:00 будується | ✗ |
| P10a | `test_plan_marks_head_open_and_edge_open` (**DM6**) | перша M1 09:41 посеред сесії → D1/H4/… голови `head_open` | ✗ |
| P10a | `test_rebuild_legacy_unchanged_golden` (**DM7**) | `legacy` == старий код байт-у-байт | ✓ |
| P10b | `test_rebuild_tv_dry_run_on_unpurged_reports_changed_real_refuses` (**DM3**) | dry-run → `changed > 0`; реальний без purge → відмова | ✗ |
| P10b | `test_rebuild_tv_dry_run_built_equals_real_after_purge` (**Б4**) | звіт dry-run == записане на копії, 7 TF | ✗ |
| P10c | `test_purge_removes_alt_anchor_neardup_at_window_edge` (**PC2**) | D1 21:00 D−1 поза вікном 22:00 → прибрано; M1 недоторкані; бекап є | ✗ |
| P10c | `test_purge_removes_alt_anchor_keys_inside_window` (**G1**) | D1 21:00 і H4 23:00 **усередині** вікна 22:00 → прибрано разом з бакетами плану | ✗ |
| P10c | `test_purge_skips_head_and_edge_open` | бакети голови/хвоста цілі | ✗ |
| P11a | `test_root_filters_calendar_counts_intrusion_trading_d1_only` (**DM8**) | непласкі M1 у паузі торгового дня → intrusion; суботні → `weekend_prints` | ✗ |
| P11a | `test_root_checks_volume` / `test_cover_bar_not_exempted_and_cover_stale` | — | ✗ |
| P11a | `test_health_skips_calendar_measures_before_season_since` (**DM5**) | 7-денне вікно через перемикання → жодного RED на правильних даних, `HEALTH_SEASON_BOUNDARY_SKIPPED` | ✗ |
| P11a | `test_season_signature_red_after_n_sessions_yellow_single` | 3 D1 поспіль `edge_open ≥ 45` → RED; 1 → YELLOW; свята з боками, що чергуються → не RED | ✗ |
| P11b | `test_health_measure_version_is_3_and_cross_version_not_comparable` | — | ✗ |
| P12 | `test_d1_tv_policy_spec` (замінює `test_d1_max_mid_session_gaps`) | [VERIFIED tests/test_d1_derive.py:91-94] | ✗ |

- Повний `pytest tests -q` до і після, stash baseline (D13.4). Орієнтир: 1449 passed, 6 skipped на `db73fab`
  [VERIFIED docs/adr/ADR-0096-fxcm-open-price-first-tick.md:207].
- Слайси live-шляху (P4, P7c, P7d, P8b, P8c) перед «готово» — один живий round-trip (D15.1): M1 комітиться →
  frontier → курсор закриває бакет → на диску фактичний бар, рівний агрегату M1.
- Симуляції атаки (`wf2_livepath/sim_outage_race.py`, `sim_cursor_race.py`, `sim_tail_cap.py`,
  `wf2_invariants/batch_stale.py`, `purge_edge_neardup.py`, `wf2_data/buf_cap.py`) переносяться у тести P7d, P8b,
  P10a, P10c як сценарії (не як скрипти з `data_v3`).

---

## 5. P-slices (≤ 150 LOC, ≤ 1 файл коду + тест)

| # | Файл | Зміст | LOC | Verify |
|---|---|---|---|---|
| P0 | цей ADR, `index.md`, примітки в ADR-0005 і ADR-0092 (шар 2, §7.1 п.2); далі: ADR-0023 D-06, ADR-0013b, ADR-0092 §1.3 і жорстке правило шару 3, `docs/contracts.md:94-96` | документи | docs | рев'ю власника |
| P1 | `core/derive_cover.py` NEW | `cover_from_minutes`, серіалізація, `suspect_reasons` | ≤ 90 | тести P1; `wf2_cover` 0 розбіжностей |
| P2 | `core/derive_close.py` NEW | `first/last_trading_minute`, `bucket_close_state`, `aligned_window` (усі якорі) | ≤ 90 | тести P2 |
| P3 | `core/derive.py` | `GapPolicy`, TV-збирання з M1 для будь-якого TF, маркери, off_calendar, докстрінг `_collect_boundary_tolerant` | ≤ 130 | тести P3; golden legacy |
| P4 | `runtime/store/uds.py` (X33: бекап, AST, `wc -l`) | `extensions` у `_disk_bar_to_candle`; **`read_final_bar` — NEW публічний метод** (G4: `grep read_final_bar` = 0 влучень; читає дисковий переможець через `choose_better_bar` (SSOT ADR-0094), Redis і RAM не чіпає) | ≤ 70 | тести; round-trip |
| P5 | `runtime/ingest/derive_policy.py` NEW + блок `config.json` + крок `season_since` у `docs/runbooks/dst_transition.md` | завантажувач і фабрика | ≤ 100 | тести; без ключа = legacy |
| P6 | 4 конструктори (`m1_poller.py`, `m1_ingestion_worker.py`, `binance_ingest_worker.py`, `replay.py`) | механічна заміна на фабрику, ≤ 8 LOC на файл. **Свідомий виняток із «≤ 1 файл» — потрібне погодження власника**: одна ціль, нуль логіки | ≤ 30 | тест фабрики; D9.1 |
| P7a | `runtime/ingest/m1_ingestion_worker.py` | `last_fetch_outcome` | ≤ 20 | тест |
| P7b | `runtime/ingest/polling/m1_frontier.py` NEW | `FrontierTracker`: overlap proof (`gap+2`), settle, loss_windows, кільце late-below-wm, гістограма лагу — без I/O | ≤ 130 | тести P7b |
| P7c | `runtime/ingest/polling/m1_poller.py` (X33) | `_fetch_window` ×3, трекер, `note_m1_frontier`, `tv`-заміна кроку 2c на `advance` | ≤ 120 | тести P7c; round-trip |
| P7d | `runtime/ingest/polling/m1_poller.py` (X33) | пагінація `tail_catchup`/`live_recover` за cutoff. **Виправляє дефект §1.3.6 у всіх режимах — можна винести раніше окремим «го»** | ≤ 100 | тести P7d; D9.1 |
| P8a | `runtime/ingest/derive_engine.py` | `legacy`: `_settle_commit`, буфер = SSOT, негативний кеш `stale_hole`, лічильники `*_catchup` | ≤ 100 | тести P8a; D9.1; лічильники на VPS |
| P8b | `runtime/ingest/derive_closer.py` NEW | курсор, ініціалізація з горизонтом буфера M1, `advance`, TV-коміт, логи I5 | ≤ 150 | тести P8b; M2 |
| P8c | `runtime/ingest/derive_engine.py` | диспетчер режиму: `on_bar`/`note_m1_frontier`/`advance_from_disk` → closer у `tv`; тригери лише `legacy` | ≤ 50 | тести P8c |
| P8d | `runtime/ingest/derive_closer.py` | shadow: курсор без коміту, `pending`, вердикти | ≤ 120 | тест; M3 |
| P9 | `runtime/ingest/replay.py` | frontier = закомічена M1 | ≤ 60 | тест |
| P10a | `tools/rebuild_from_m1.py` | `tv`: план (порції, dict через вибирач, head/edge, сезон, свідок), обов'язкові календар і `--start` | ≤ 150 | тести P10a |
| P10b | `tools/rebuild_from_m1.py` | `tv`: dry-run звіт проти диска; реальний запис з перевіркою очищення на порцію | ≤ 110 | тести P10b |
| P10c | `tools/repair/purge_derived_window.py` NEW | purge за планом, near-dup і альтернативні якорі, `rewrite_atomic` | ≤ 100 | тести P10c |
| P11a | `core/health/measures.py` | root з календарем і `v`, `season_since`, `cover_stale`, `calendar_intrusion` (одиниця), `season_signature` | ≤ 150 | тести P11a |
| P11b | `core/health/grading.py` + `compare.py` (1 рядок версії) | грейди, `_WORSE_IF_UP`, v3 | ≤ 80 | тести |
| P11c | `tools/symbol_health_check.py` | wiring календаря, `season_since`, нових вимірів | ≤ 80 | health на копіях |
| P12 | `tools/diag/d1_gap_anatomy.py`, `tests/test_d1_derive.py` | `LONG_RUN_MIN` з config [VERIFIED :53, 242]; визначення absent | ≤ 60 | pytest = baseline |

**Порядок:** мердж `20f1700` і `c86a707` → P0 → P1 → P2 → P3 → P4 → P5 → P6 → P7a → P7b → P7c → P7d → P8a → P8b →
P8c → P8d → P9 → P10a → P10b → P10c → P11a → P11b → P11c → P12.
**Паралельно:** P10*/P11* після P3 і P5; P9 після P8c; P7d (з мінімальним власним overlap-чеком) — може йти до
всіх, як виправлення прод-дефекту.

---

## 6. Гейти і виміри

**M0 — baseline** (старий код, `legacy`). Вікна: XAU/XAG літо 08.03–11.06 і зима 02.11–06.03; GER30 21.04–11.09
[INFERRED календар]; NAS100/SPX500 20.04–10.09 — лише за списками присутності M1 з VPS (scratchpad
`NAS100_all.txt`, `SPX500_all.txt`); US30 — не виміряно. Метрики на TF: бакети з ≥ 1 M1, побудовано, зникло,
розбіжних з REF_cal/REF_all, втрата `v`, `tail_truncation`.

**M1 — офлайн `tv` після P3/P10b (приймання):**
- `tv` == REF_cal на 7 TF (крайові бакети розширено назовні; `head_open`/`edge_open` звітовано окремо);
- `REF_all − REF_cal` == `calendar_intrusion` + `weekend_prints`;
- `legacy` rebuild і `derive_bar(LEGACY)` байт-у-байт == старий код (`sim_rebuild_dump.py`);
- розподіл `gap_suspect` == §3.1.3;
- XAU 09.04 (35 розсипаних хвилин) без маркера — задокументований сліпий кут R2;
- вікно, більше за 100 000 M1, будується повністю.

**M2 — live-симуляція** (сценарії атаки як тести + `sim_live_lock.py` з розширеннями):
- затримка хвоста k ∈ {1, 3, 4, 16, 60} хв; settle ∈ {120, 180, 300};
- таймаути proxy 3/10/30 хв посеред бакета;
- простій через закриття сесії з останньою хвилиною без угод (LO1);
- зупинка перед settle і рестарт у паузі о 21:30/21:50/22:12/22:30 (LO2);
- `tail_catchup` з fake sidecar cap 200 (LO3).
- Очікування: 0 урізаних комітів; 0 `stale`/`commit_conflict`; при k·60 < settle — 0 урізаних; при k·60 ≥ settle —
  `late_below_wm` або `revision` > 0 і гучно.

**Гейт G-A (локально):** pytest == baseline; M1 і M2 пройдені; health v3 на TV-перебудованих копіях поточного
сезону — 0 `cover_stale`, 0 нових `season_signature`/`calendar_intrusion` RED. RED `season_signature` —
**лише** на синтетичній копії «зима під літнім конфігом» (DM1).

**Гейт G-B (VPS, «го»):** деплой з `mode=legacy`. Діють лише виправлення дефектів: P8a (буфер = SSOT, негативний
кеш) і P7d (пагінація). D9.1 120 с. Добовий зріз: `DERIVE_REVISION_DROPPED` (live окремо від `*_catchup`),
`stale_hole`, `M1_FRONTIER_UNCONFIRMED` за причинами, `M1_BACKLOG_TRUNCATED`, `M1_LATE_BELOW_WM`,
`m1_commit_lag_s`.

**Гейт G-C — shadow ≥ 5 торгових днів** (включно з відкриттям у неділю і закриттям у п'ятницю, усі 5 живих
символів, **і хоча б один плановий рестарт у паузі**):
1. там, де legacy збудував і TV закрив за даними: `same = 100%` за OHLCV, крім `differs.legacy_truncated_tail`
   (очікуваний дефект legacy) — показується власнику числом;
2. `would_build` за TF — власнику числом;
3. частка `closed_by_settle` ≤ 3× історичної (Д19: GER30 M3 0.11%, M5 0.075%; XAU M5 0.038%, H4 3/403,
   D1 2/67 — усі на закритті сесії); вищу треба пояснити простоєм;
4. кожен `DERIVE_WAIT_FEED` має відповідник `BROKER_PROXY_TIMEOUT`/`QUEUE_CONGESTED`/`M1_LIVE_RECOVER` у тих
   самих хвилинах;
5. `M1_FRONTIER_UNCONFIRMED reason=no_overlap` на здоровому фіді = 0 (перевірка `gap+2`, LO6);
6. `m1_settle_s` відкалібровано: p99.9 лагу, вгору;
7. час `advance` на прохід і CPU ≤ baseline G-B (гейт перфу ADR-0054 §3.5);
8. ERROR/Traceback = 0.

**Гейт G-D (`mode=tv`, «го»):** G-C пройдено; `season_since` заповнено для груп живих символів; P11c health
(`season_signature`) за розкладом або exit-gate. G1 `season_guard` (ADR-0092) **рекомендовано** до переходу US DST
01.11.2026; без G1 хибний DST стає RED через N=3 сесії (прийнятно за R1).

**Рішення щодо контракту ревізії:** якщо за тиждень `tv` live-лічильники `M1_LATE_BELOW_WM` + пізні хвилини
хвоста > 0 — окремий ADR про ревізію derived у UDS. Ранжування в `bar_choice` за покриттям цей ADR свідомо
відхиляє (Б3).

---

## 7. Міграція даних

### 7.1 Самі по собі дані не змінюються
Зміна коду історії не переписує; `tv` впливає лише на нові бакети (курсор не переглядає закомічені). Урізані в
live хвости лікуються тільки purge + rebuild; rebuild без `--force` їх пропускає [VERIFIED
rebuild_from_m1.py:282].

### 7.2 Очікуваний ефект перебудови (локальні числа; на VPS ймовірно менші)

| Символ | Нові бари | Змінені | Примітки |
|---|---|---|---|
| XAU (169 сесій) | 18 D1, 29 M5, 10 M15, 7 M30, 5 H1 | M15 16, M30 16, H1 15, H4 14 | 7 D1 з діркою в ліквідний час: `long_run` на 6; 14 з 18 D1 — у зимовому вікні, тобто **за межею `season_since`** до ADR-0095 |
| XAG | 18 D1, 37 M5, 10 M15, 7 M30, 6 H1, 1 H4 | M15 21, M30 18, H1 14, H4 14 | — |
| NAS100, SPX500 | «голок» у святкові дні нема: min `cnt/exp` D1 = 1080/1380 за 20.04–10.09 [VERIFIED terminal атака DM, verified_ok] | не виміряно | dry-run на копії VPS обов'язковий |
| US30 | не виміряно | — | dry-run на копії VPS |
| GER30 [INFERRED календар] | 11 D1, 11 M5 | M15 10, M30 8, H1 6, H4 6 | до 25.10 DST EU не зачіпає |

Шви бекфілу (14.05) можуть бути локальним артефактом: у VPS-вимірі ADR-0092 §6.1 їх нема.

### 7.3 Процедура для живих символів (XAU, XAG, NAS100, SPX500, US30 [VERIFIED config.json:3-9])

Кожен крок — «го» власника (D9).

**Координація.** ADR-0096 слайс C і ADR-0095 теж переписують derived. Порядок: ADR-0096 B (значення M1) →
ADR-0095 (сезонний календар) → purge + rebuild `tv`. **До ADR-0095 міграція `tv` дозволена лише для вікна
`[season_since, D−1]` — це примусово в коді (§3.7 п.2)**, а не рекомендація.

1. `rebuild_from_m1 --gap-policy tv --dry-run --start <season_since>` на копії даних VPS (read-only) → звіт
   `new / changed{ohlc,v,markers} / vanished / head_open / edge_open / gap_suspect` на символ і TF. Перевірка:
   `changed.ohlc` на повних барах з незмінними M1 — лише відомі урізані хвости legacy.
2. Бекап `tf_180…tf_86400` (D2). M1 не чіпається.
3. Writers зупинено (`--writers-stopped`) → `purge_derived_window` за планом → `rebuild --gap-policy tv`
   порціями. Тривалість простою writers — у звіт; після старту `M1_TAIL_CATCHUP_TRUNCATED` = 0 (пагінація P7d
   дотягує до 5000 хвилин).
4. `sort_jsonl_by_open_ms`; health v3 (новий baseline; v2 з v3 не порівнюється).
5. Старт writers → cold-load/re-prime Redis за ранбуком [ASSUMED — verify: docs/runbooks/production.md:100-107] →
   D9.1 120 с.

### 7.4 GER30 і EUSTX50 (не активні)

1. Передумова: виправити календар W4 (GER30 фактично 00:31–19:59, конфіг 07–21 [VERIFIED config.json:94-101,
   136-137]); EUSTX50 спершу виміряти (локальних M1 нема).
2. Засів M1 FIRST_TICK — GER30 зроблено 15.09; фікс засіву `1128454`/`c86a707` мерджиться до P1.
3. Офлайн TV-rebuild на засіяних даних у межах `season_since` групи `cfd_eu_*` → health v3 GREEN/YELLOW без
   `season_signature` → чекліст активації ADR-0054. Вікна після 25.10 (DST EU) — лише з оновленим `season_since`.
4. Live `mode=tv` для них — після G-D на живих символах; до того `legacy` (межа «історія `tv`, live `legacy`» →
   після G-D повторний purge + rebuild проміжку). Альтернатива: `mode_by_symbol: {"EUSTX50": "tv"}` при активації,
   лише після G-C.

### 7.5 Споживачі
- UI, API, SMC і trader-v3 бачать лише, що бар з'явився (PDH/PDL лікуються, ADR-0093).
- Графік ховає пласкі свічки (рішення 2): M5 з однієї пласкої хвилини може бути невидимим — правило відображення,
  SSOT правильний.
- `partial_penalty` (uds.py:1839-1844) рахується в одиницях M1 — точніше, форма поля та сама.

---

## 8. Наслідки і ризики

**Позитивні:** зникає 18 D1/символ-метал за сезон і хвости M15–H4; урізаний final через годинник, гонку тригера
чи рестарт у паузі структурно неможливий; прод-дефект втрати > 200 хвилин у `tail_catchup` закрито; DST-сигнал
явний; тихі відмови стали подіями.

**Негативні:** два внутрішні шляхи деривації до повного переходу; `season_since` — новий ручний крок ранбука DST;
запізнення final до `m1_settle_s` на закритті сесії; сліпий кут R2.

| # | Ризик | S | Мітигація |
|---|---|---|---|
| R1 | Хибний якір DST: D1/H4 з неправильним вікном стають final | S1 | `session_edge_run` (YELLOW) + `calendar_intrusion`; RED через `season_signature` за N=3 (зима 87/88 і 88/88 edge, серії 80/88; літо max 1; свята не дають серій; GER30 0); rebuild і health обмежені `season_since`; G1 рекомендовано до 01.11; після виправлення — purge + rebuild (ADR-0095) |
| R2 | Дірка < 45 хв у ліквідний час або розсип — тихий бар | S2 | `m1_cover.cnt/exp`; `cover_stale`; окремий ADR журналу простою |
| R3 | Обрізана відповідь брокера | S1 → S2 | overlap proof на будь-якій непорожній відповіді + `feed_outage`; пагінація `tail_catchup`/`live_recover` (P7d) |
| R4 | Хвилина доїхала після закриття бакета | S2 | `M1_LATE_BELOW_WM` (кільце) + пізні хвилини хвоста; контракт ревізії — окремий ADR за виміром |
| R5 | Final на закритті сесії запізнюється до `m1_settle_s` | S3 | лише коли остання хвилина без угод (XAU D1 2/67); калібрування; H4/D1 прикриває preview |
| R6 | Розходження документів | S2 | P0 оновлює всі посилання (Д13) |
| R7 | Правки X33 у `m1_poller.py` (`c86a707` + P6 + P7c + P7d) | S1 | логіка винесена в `m1_frontier.py`; бекап, AST, `wc -l`, `file_guardian check`; P7c/P7d лише після мерджу `c86a707`, послідовно |
| R8 | Міграція на VPS | S1 | dry-run на копії, план = SSOT для purge/rebuild, порції, бекап, «го», D9.1 |
| R9 | `m1_settle_s=180` до калібрування | S2 | лише `shadow` до G-C п.6 |
| R10 | `season_since` заповнено хибно | S1 | свідок сезону в плані rebuild (§3.7 п.4) і `season_signature` у health |
| R11 | Прохід `advance` на кожну M1 додає CPU у гарячому шляху | S3 | O(7) перевірок голови курсора на M1; порожні бакети проходяться один раз; G-C п.7 |

---

## 9. Rollback

- **Код:** `derive_policy.mode = legacy` + рестарт: `derive_bar` — байт-у-байт старий вихід (golden), курсор і
  shadow вимкнені, тригери й overdue — як були. `FrontierTracker` лишається пасивним обчисленням.
- P8a (буфер = SSOT, негативний кеш) і P7d (пагінація) — виправлення дефектів; відкочуються окремим `git revert`.
- **P4 теж діє в усіх режимах (G3).** Після нього `read_tail_candles` несе `extensions` [VERIFIED uds.py:1599-1621 →
  `_disk_bar_to_candle` їх сьогодні губить], а обидва legacy-виклики derive йдуть з `filter_calendar_pause=True`
  [VERIFIED derive_engine.py:303, 402 → core/derive.py:274-276]. Тобто M1 з диска з `calendar_pause_flat` у хвилині,
  яку **чинний** календар вважає торговою, після P4 вперше випадатиме і з legacy-агрегації — це прибирає розбіжність
  live проти rebuild (rebuild їх уже викидає [VERIFIED rebuild_from_m1.py:99, 115]). Виміряна популяція: **0 з
  225 902** M1 XAU і **0 з 226 265** M1 XAG несуть `calendar_pause_flat` (`trading_flat` 1 і 2) [VERIFIED terminal],
  тож практичний ризик нульовий, але «`legacy` = байт-у-байт» справедливе для `derive_bar` (golden), а на рівні
  системи — з цією поправкою.
- **Health:** revert P11a–c. Звіти v3 і v2 не порівнюються (`verdicts_comparable=false`) [VERIFIED
  compare.py:64-67].
- **Дані:** відновити `tf_180…tf_86400` з бекапу кроку 7.3.2 (плюс бекапи `rewrite_atomic` на файл);
  `sort_jsonl_by_open_ms`; re-prime Redis рестартом; D9.1. M1 міграція не зачіпала.
- `season_since_by_calendar_group` без `tv` ні на що не впливає — видаляти не треба.

---

## 10. Відкрите і невиміряне

- US30: жодного виміру; NAS100/SPX500 — лише присутність M1, без OHLCV-дифу. Числа — з dry-run на копії VPS.
- Розподіл `m1_commit_lag_s` — дає лише shadow (G-C).
- Включність `date_to` у FXCM не виміряна; `gap+2` робить доказ незалежним від неї, G-C п.5 це підтверджує.
- Чи sidecar читає `config.json` (для SSOT ліміту 200): неважливо для коректності — сторінка 120 < 200, а будь-яке
  обрізання ловить overlap proof. [UNKNOWN — risk: L]
- Частота прод-втрат `tail_catchup` > 200 хвилин на VPS до P7d — не виміряна (grep `M1_TAIL_CATCHUP` у логах VPS
  за «го»).
- Чи TradingView включає в H1/H4 торгові пласкі хвилини (`trading_flat`) і «запечену» першу хвилину свіжих діб
  (ADR-0096 §1.4). Агрегація їх бере, як і сьогодні. Звірка з TV — окремий вимір.
- Значення `season_since` для груп — з журналу ранбука, не з пам'яті.
- Числа GER30 — під кандидат-календарем, не під SSOT.

---

## 11. Самоперевірка R_REJECTOR

- **I1/I3:** writer'ів не додано; контракт `commit_final_bar` не змінено; у `tv` коміти кожного TF монотонні за
  часом — watermark UDS не може поховати незакритий бакет.
- **I5:** кожен шлях `None`, відмови, очікування, конфлікту, обрізання і запізнення має іменовану подію.
- **D15.2:** одне джерело деривації в `tv` (M1), один шлях закриття (курсор), один предикат закриття (live,
  replay, rebuild), один хелпер fetch, один план (rebuild + purge), одне вирівняне вікно, одна `season_since`
  (rebuild + health), пороги в config.
- **Суперечності:** ADR-0092 шар 2 і §7.1 п.2 позначено; дизайн v2 («шлях тригера не змінюється», «горизонт =
  `_OVERDUE_LOOKBACK`», «правило обрізання `n == MAX_FETCH_N`», «`m1_full: true` мізерний», «dry-run з
  precheck») — кожне твердження, спростоване атакою, прибрано, а не залатано.
- **Невиміряне позначено** (§10).

---

## 12. Пункт критика / атаки → як закрито

**Критик (`wf_critic.md`):**

| Пункт | Як закрито | Доказ / тест |
|---|---|---|
| **Б1** урізані бари, коли закриття збігається з простоєм | Grace нема. Frontier = max(wm, `settled_through`); settle посувається лише підтвердженим fetch (overlap proof, §3.2). `[]` на таймаут/затор/помилку не посуває frontier | proxy/provider повертають `[]` [VERIFIED m1_ingestion_worker.py:101-165; provider.py:169-174]; `test_partial_outage_no_commit_until_recovery_then_full_bar` |
| **Б2** буфер ≠ диск, повтори overdue після рестарту | `tv`: буферів derived нема (пряма агрегація з M1), курсор не переглядає закомічені. `legacy`: P8a переможець із SSOT + негативний кеш | `test_restart_does_not_revisit_committed_buckets`, `test_legacy_duplicate_loads_winner_into_buffer` |
| **Б3** маркери губляться при рестарті і в Redis | Батько не залежить від маркерів дітей (покриття з M1). P4 передає `extensions` з диска (для порівняння конфлікту і health). Redis DeriveEngine не читає. Ранжування ревізій за покриттям відхилено | [VERIFIED uds.py:127-139; bars.py:60-61]; `test_read_tail_candles_preserves_extensions`, `test_restart_in_pause_before_settle_builds_full` |
| **Б4** dry-run і M15…H4 у rebuild хибні; last-wins | `tv`: план і пряма агрегація з M1 у dict через вибирач; dry-run = той самий код без `append` | `test_rebuild_tv_dry_run_built_equals_real_after_purge`, `test_rebuild_reads_m1_duplicates_via_chooser` |
| **Б5** хвилини поза календарем викидаються мовчки | Не входять у бар (рішення агента), `off_calendar_m1` + `gap_suspect` + WARN; health `calendar_intrusion` в одиницях торгових D1, `weekend_prints` окремо | `test_tv_off_calendar_nonflat_excluded_and_counted`, `test_root_filters_calendar_counts_intrusion_trading_d1_only` |
| **С6** `long_run` на M5–H1 сліпий | Покриття кожного бару прямо з хвилин; еквівалентність зі згорткою через ланцюг виміряна | 0 розбіжностей, 7 TF × 4 вікна [VERIFIED wf2_cover]; `test_cover_h1_50min_hole_across_m30_boundary` |
| **С7** `run_at_session_edge` → RED без календаря свят | Бар — YELLOW; RED лише `season_signature` ≥ N сесій одного боку; свята дають поодинокі edge з боками, що чергуються | [VERIFIED wf2_data/suspect.out]; `test_season_signature_red_after_n_sessions_yellow_single` |
| **С8** версія health, обсяг, каскад | v3; `v` у root; cover-бари без звільнення; `_WORSE_IF_UP`; каскад свідомо пропускає неповні | §3.6; `test_health_measure_version_is_3_…` |
| **С9** числа GER30 не з SSOT | Позначено [INFERRED]; виправлення календаря W4 — передумова | §7.4 |
| **С10** чотири конструктори | Одна фабрика; binance → `legacy` + WARN | `test_factory_used_by_all_constructors_binance_forced_legacy` |
| **С11** `calendar_missing` → `always_true` | `tv` без календаря — `ValueError`/відмова | `test_tv_requires_calendar_raises`, `test_rebuild_tv_refuses_without_calendar_or_start` |
| **С12** guard країв не будує D1 на добовому вікні | Вікно розширюється назовні за всіма якорями; пропуск лише `head_open`/`edge_open` | `test_rebuild_one_day_window_builds_d1_via_outward_extension` |
| **Д13** неповний список superseded | P0: ADR-0005, ADR-0092 шар 2 і §7.1 п.2 — позначено в цьому P0; ADR-0023 D-06, ADR-0013b, ADR-0092 §1.3 і жорстке правило шару 3, contracts.md — решта P0 (§10 звіту) | §5 P0 |
| **Д14** чи включає прогін граничні хвилини | Так; DST-прогін = 60 | `test_tv_d1_dst_edge_run_counts_boundary_minute` |
| **Д15** preview для derived | Лише TF ≥ H4; R5 на M5…H1 не прикрита | [VERIFIED tick_preview_worker.py:749] |
| **Д16** shadow-гейт не реалізується | Курсор TV у пам'яті з M1 без TV-буферів, `pending` для асинхронності | P8d; `test_shadow_verdicts_without_tv_commit` |
| **Д17** «усе ≤ frontier остаточне» неповне | Межа гарантії сказана явно; офлайн-ремонти ловить `cover_stale`/root; ранбук purge + rebuild | §3.2 |
| **Д18** графік і flat | Рішення власника 2 | [VERIFIED ADR-0096:118, 209] |
| **Д19** baseline для settle | У гейті G-C п.3 | `wf_rejector_tv/grace_freq.py` |
| **Д20** фікстура DST-понеділків | Лише open_ms 15 понеділків у `tests/fixtures/adr0097/` | P3 |

**Атака, лінза live-outage:**

| Пункт | Серйозність | Як закрито | Тест |
|---|---|---|---|
| **LO1** тригер наступного бакета комітиться раніше за курсор → `stale` і урізані батьки | blocker | У `tv` тригерів нема; єдиний курсор біжить синхронно в `on_bar` після кожного коміту M1 і комітить кожен TF строго за часом; батьки — з M1 напряму, без залежності від дітей (§3.3, C3) | `test_batch_recovery_prev_bucket_last_minute_no_trade_is_built`, `test_outage_across_session_close_all_tfs_equal_m1_aggregate` |
| **LO2** горизонт рестарту в годинникових бакетах < паузи | blocker | Горизонт = перший бакет TF, повністю покритий буфером M1 (7 діб); курсор з `derived_wm + tf`; `beyond_restart_horizon` рахує лише не-EMPTY | `test_restart_in_pause_before_settle_builds_full` (21:30/21:50/22:12/22:30), `test_beyond_restart_horizon_counts_non_empty_only` |
| **LO3** обрізання в sidecar (200) не бачить правило `n == MAX_FETCH_N` | major | Будь-яка непорожня відповідь без перекриття → `M1_BACKLOG_TRUNCATED` + loss_window; пагінація `tail_catchup`/`live_recover` сторінками 120 | `test_any_nonempty_without_overlap_records_loss_window`, `test_tail_catchup_paginates_with_fake_sidecar_cap_200` |
| **LO4** bootstrap-реплей 10 080 M1 забруднює лічильники | major | `tv`: реплей 2c замінено одним `advance`, закомічені бакети не переглядаються. `legacy`: лічильники `*_catchup` окремо, у гейти не йдуть | `test_restart_does_not_revisit_committed_buckets`, `test_legacy_bootstrap_counters_separate` |
| **LO5** запізніла хвилина нижче wm невидима | minor | Кільце закомічених відкриттів → `M1_LATE_BELOW_WM` у гейті ревізії; сліпа зона за кільцем названа | `test_late_minute_below_wm_counted` |
| **LO6** `date_to = C+60`: включність може дати фоновий `no_overlap` | minor | `n = gap + 2` — доказ правильний за будь-якої включності; G-C п.5 | `test_frontier_confirmed_only_with_overlap_n_gap_plus_2` |

**Атака, лінза persistence-contract:**

| Пункт | Серйозність | Як закрито | Тест |
|---|---|---|---|
| **PC1** бакет, закритий курсором, губиться як `stale` при пакеті | major | = LO1 | = LO1 |
| **PC2** purge за одним якорем лишає D1 alt-якоря, near-dup перемагає | major | `aligned_window` за всіма легальними якорями; purge прибирає near-dup у `tf_ms//12` біля меж; перевірка очищення — те саме | `test_aligned_window_covers_all_legal_anchors`, `test_purge_removes_alt_anchor_neardup_at_window_edge` |
| **PC3** `m1_full: true` = +16% до derived-файлів | minor | Відхилено маркер на повних барах: у C3 він нікому не потрібен (батько рахує з M1, health звіряє root). Покриття лише на неповних барах | `test_tv_full_bar_has_no_new_extensions` |
| **PC4** хибний `REVISION_DROPPED` для першого бакета вікна catchup | minor | Курсор не переглядає закомічені; реплею в `tv` нема | `test_restart_does_not_revisit_committed_buckets` |
| **PC5** `stale_hole` у `legacy` повторюється щохвилини | minor | Негативний кеш з TTL = lookback + dedup WARN | `test_legacy_stale_hole_negative_cache_warn_once` |

**Атака, лінза data-migration:**

| Пункт | Серйозність | Як закрито | Тест |
|---|---|---|---|
| **DM1** міграція `tv` за минулий сезон псує правильні зимові D1/H4 | blocker | `season_since` (config SSOT); rebuild/purge `tv` відмовляють для `start < season_since`; свідок сезону в плані; порядок з ADR-0095 — примусовий, не рекомендація; G-A перефразовано | `test_rebuild_tv_refuses_window_before_season_since`, `test_rebuild_tv_data_witness_refuses_wrong_season` |
| **DM2** FIFO-буфер rebuild мовчки втрачає ранню історію | blocker | Порції за D1, dict без FIFO, гучна відмова `REBUILD_CHUNK_TOO_LARGE` до purge; purge — тим самим планом | `test_rebuild_tv_window_larger_than_buffer_first_last_d1_equal_ref` |
| **DM3** dry-run неможливий через precheck очищення | major | Dry-run precheck не виконує, звітує дифом проти диска; реальний прогін — перевірка на порцію | `test_rebuild_tv_dry_run_on_unpurged_reports_changed_real_refuses` |
| **DM4** спільний `_fetch_window` урізає `tail_catchup` 5000 → 120 | major | Пагінація зберігає бюджет 5000 при сторінці 120; заодно закрито наявну втрату > 200 через sidecar | `test_tail_catchup_budget_5000_kept`, `test_tail_catchup_paginates_with_fake_sidecar_cap_200` |
| **DM5** health v3 дає хибний RED після ручного перемикання DST | major | Календарні виміри лише для `open ≥ season_since`; решта — `HEALTH_SEASON_BOUNDARY_SKIPPED` | `test_health_skips_calendar_measures_before_season_since` |
| **DM6** голова історії M1 посеред сесії | minor | Симетричний `head_open`: не purge, не rebuild, лічильник у звіті | `test_plan_marks_head_open_and_edge_open`, `test_purge_skips_head_and_edge_open` |
| **DM7** «legacy байт-у-байт» неперевірюване | minor | Усі зміни rebuild — лише `tv`; `legacy` — старий код | `test_rebuild_legacy_unchanged_golden` |
| **DM8** одиниця серії `calendar_intrusion` не визначена (суботи XAG) | minor | Одиниця — торговий D1-бакет (`exp > 0`, як G2 ADR-0092); суботні принти — `weekend_prints` INFO | `test_root_filters_calendar_counts_intrusion_trading_d1_only` |

### Ревізія фінального ADR (R_REJECTOR: APPROVE_WITH_NOTES, 2026-09-16)

| Пункт | Серйозність | Як закрито | Тест |
|---|---|---|---|
| **G1** purge за планом лишає alt-якірні бари в СЕРЕДИНІ вікна (near-dedup зливає будь-яку суміжну пару D1 всюди, для H4 поріг 0 → дві паралельні серії) | major | §3.7 Purge: прибирається **кожен** derived-ключ (TF ≠ 60) з `open_ms` у вирівняному вікні + near-поріг за обома межами | `test_purge_removes_alt_anchor_keys_inside_window` |
| **G2** `season_since` двозначний: «дата правки config» блокує ремонт свіжих хибних днів, «межа ринку» дає хибний RED у lag-вікні | major | §3.5: два підключі — `market_since` (читає rebuild) і `applied_at` (читає health); правка ДО межі → lag-вікна немає | `test_rebuild_tv_refuses_window_before_season_since`, `test_health_skips_calendar_measures_before_season_since` |
| **G3** P4 діє в усіх режимах, але не названий | minor | §9: названо; виміряна популяція `calendar_pause_flat` 0 з 225 902 XAU і 0 з 226 265 XAG | golden P3 + round-trip P4 |
| **G4** `uds.read_final_bar` вживався як наявний API | minor | P4: позначено NEW (дисковий переможець через `choose_better_bar`), LOC 40 → 70 | `test_read_final_bar_returns_chooser_winner` |
| **G5** пріоритет `mode_by_provider`/`mode_by_symbol`/`mode` не заданий | minor | §3.5: provider > symbol > global | `test_mode_resolution_provider_beats_symbol_beats_global` |
| **G6** між P11c і G-D немає ні baseline v2, ні нових вимірів | minor | §3.6: P11c відмовляє для живих груп без `season_since_by_calendar_group` | wiring-тест P11c |
| **G7** дві тихі зони: порожній CLOSED-бакет і процес-локальний `feed_outage` | nit (I5) | §3.3: WARN `empty_after_outage`, коли бакет перетинає `loss_windows`; друга зона названа в R2 | тест P8b |
| **G8** метадані (рядок index), «Частково замінено» як факт при Proposed | trivial | Метадані без номера рядка; примітки в 0005/0092 — «Замінюється ADR-0097 (Proposed), чинності набирає з `mode=tv`» | — |

---

## Changelog

- 2026-09-15 — створено (Proposed). Шлях: дизайн v1 (`wf_design.md`, варіант C з grace) → критик (Б1–Б5,
  С6–С12, Д13–Д20) → дизайн v2 (C2: ланцюг + курсор поруч із тригером) → атака трьома лінзами (LO1–LO6, PC1–PC5,
  DM1–DM8; 4 blocker, 6 major) → фінальна редакція C3: кожен TF прямо з M1, один курсор у `on_bar`, горизонт
  буфера M1, overlap proof на будь-якій відповіді + пагінація, `season_since` для rebuild і health, план rebuild
  порціями як SSOT для purge. Позначено ADR-0005 і ADR-0092 (шар 2, §7.1 п.2).
