# ПОВНИЙ АУДИТ AS-IS + TO-BE ADR + ПЛАН

## STATUS REFRESH (станом на 2026-02-14, після Slice-1..4)

Цей документ **не є повністю актуальним** як оперативний AS-IS, але залишається корисним як історичний аудит.

### Що вже виконано (замість TO-BE)

- Slice-1: `/api/bars` no_data став fully-loud (warnings не губляться).
- Slice-2: Policy SSOT через `/api/config` (`policy_version`, `build_id`, `window_policy`) + UI consume з loud fallback.
- Slice-3: final cold-start read-path використовує внутрішній `prefer_redis=true` при `disk_policy=never`.
- Slice-4: short-window rail — partial window повертається loud (`insufficient_warmup`, expected/got), а не перетворюється в silent empty.

### Що в цьому файлі застаріло

- Тези про “bars=[] через RAM miss” як домінуючий сценарій без rail — частково застарілі (тепер є partial+loud та no_data loud rail).
- Розділи policy split-brain (UI-only hardcode) — частково застарілі, бо UI тепер читає policy із `/api/config`.
- Частина плану P-slices, що описує ці виправлення як майбутні, тепер має статус **done**.

### Де дивитись актуальний стан (SSOT для операцій)

- `README.md`
- `docs/system_current_overview.md`
- `changelog.jsonl` (записи 20260214-200..204)
- `CHANGELOG.md`

### Решта, що ще лишається релевантним

- Історична модель відмов, інвентар dead/parallel path і загальні hardening-напрямки (thread-safety, security perimeter, ops gates) як roadmap-орієнтир.

## 1. AS-IS AUDIT

### 1.1 Активні потоки та плейни (Flow Map)

Обраний формат: **канонічний ланцюг + матриця** (без дублювання A/B).

Канонічний ланцюг A→C→B:

- **A (writers/ingest):** `engine_b`, `m1_poller`, `tick_preview_worker`
- **C (UDS):** `commit_final_bar()`, `publish_preview_bar()`, `read_window()`, `read_updates()`
- **B (UI):** `/api/bars`, `/api/updates`, `/api/overlay` + `applyUpdates(events)`

Матриця плейнів:

| Плейн | Запис | Читання | Канон | Guard/rail |
| --- | --- | --- | --- | --- |
| Final | `commit_final_bar()` → disk + redis snapshot + updates bus | `read_window()` + `read_updates()` | `complete=true`, `src ∈ FINAL_SOURCES`, end-excl | watermark, final>preview bridge |
| Preview | `publish_preview_bar()` → Redis preview curr/tail + preview updates | `read_preview_window()` + preview updates | `complete=false`, `src ∉ FINAL_SOURCES`, end-excl | preview NoMix guard |
| Overlay | read-only агрегація preview у target TF | `/api/overlay` | ефемерний бар для UI | HTF off + degraded warnings |

Bridge: `commit_final_bar()` публікує final у preview ring для `tf_s ∈ preview_tf_allowlist`, щоб promotion final>preview був детермінованим.

### Контракти по ребрах (per-edge contracts)

- FXCM → engine_b  
  - Виклик: engine_b._poll_one()  
  - Контракт: CandleBar (end‑excl, через assert_invariants)  
  - Трансформація: відсутня (end‑excl нативно)
- engine_b → commit_final_bar (engine_b.py:808)  
  - Контракт: CandleBar (complete=true)  
  - Захист: watermark guard (uds.py:629)
- engine_b → publish_preview_bar (engine_b.py:792)  
  - Контракт: CandleBar (complete=false)  
  - Захист: NoMix guard (uds.py:710)
- tick_preview → publish_preview_bar (tick_preview_worker.py:375)  
  - Контракт: CandleBar (complete=false)  
  - Семантика часу: end‑excl (tick_agg.py:108)
- read_window → server (server.py:733)  
  - Контракт: WindowResult.bars_lwc (LWC формат: time = epoch_s)  
  - Трансформація: `_bars_to_lwc` конвертує open_time_ms → time (epoch_s)
- server → браузер (/api/bars)  
  - Формат: JSON { bars: [{ time, open, high, low, close, volume, … }] }  
  - Обмеження/стичинг: `_clamp_limit`, опціональне PREVIOUS_CLOSE stitching
- браузер → chart  
  - Виклик: controller.setBars(bars) — LWC‑сумісний {time, open, high, low, close}  
  - Нормалізація: normalizeBar() (chart_adapter_lite.js:165)

### 1.2 Точки входу та процеси

- Supervisor  
  - Модуль: app.main  
  - Режим: all/connector/ui/...  
  - IO: керування підпроцесами  
  - Стан: список процесів
- Connector  
  - Модуль: app.main_connector → engine_b.PollingConnectorB  
  - Режим: connector  
  - IO: FXCM REST, Redis R/W, запис на диск, UDS(writer), watermarks  
  - Залежності: FXCM login, Redis, диск
- M1 Poller  
  - Модуль: runtime.ingest.polling.m1_poller  
  - Режим: m1_poller  
  - IO: FXCM REST, Redis R/W, диск W, UDS(writer), watermarks  
  - Залежності: FXCM login, Redis, диск
- Tick Publisher  
  - Модуль: runtime.ingest.tick_publisher_fxcm  
  - Режим: tick_publisher  
  - IO: FXCM WS → Redis Pub/Sub  
  - Залежності: FXCM WS, Redis
- Tick Preview  
  - Модуль: runtime.ingest.tick_preview_worker  
  - Режим: tick_preview  
  - IO: Redis R/W, UDS(writer, без диска), агрегація тиков  
  - Залежності: Redis
- UI Server  
  - Модуль: ui_chart_v3.server  
  - Режим: ui  
  - Порт: 8089  
  - IO: Redis R, диск R (bootstrap лише)  
  - Роль: UDS(reader), RAM cache  
- PRIME_READY gate: запуск UI‑процесу тільки після встановлення ключа Redis prime:ready (main.py L301, uds.py:1133)

### 1.3 Матриця власності (SSOT‑права)

- Disk write (JSONL)  
  - connector: YES, m1_poller: YES, tick_preview: NO, UI server: NO, tools: YES (ручний)  
  - Enforcement: _ensure_writer_role (uds.py:1155) + writer_components=False для tick_preview
- Redis snapshot write  
  - connector: YES, m1_poller: YES, tick_preview: NO, UI: NO, tools: NO
- Updates bus publish (final)  
  - connector: YES, m1_poller: YES, tick_preview: NO, UI: NO, tools: NO  
  - Enforcement: commit_final_bar writer guard (uds.py:606)
- Preview publish  
  - connector: YES, m1_poller: NO, tick_preview: YES, UI: NO, tools: NO  
  - Enforcement: publish_preview_bar writer guard (uds.py:704)
- Promoted publish  
  - connector: NO, m1_poller: NO, tick_preview: YES, UI: NO, tools: NO  
  - Enforcement: publish_promoted_bar src check (uds.py:674)
- Derived TF build  
  - connector: YES (M15/M30/H1 з M5), m1_poller: YES (M3 з M1), tick_preview: NO, UI: NO, tools: YES  
  - Конфіг: derived_tfs_s
- Читання з диска (bootstrap/warmup)  
  - UI: YES (лише warmup/bootstrap); інші компоненти — не повинні читати диск у live‑шляху  
  - Enforcement: disk_policy guard (uds.py:1261)
- RAM read  
  - connector: YES, m1_poller: YES, tick_preview: NO, UI: YES, tools: NO  
  - Функція: read_window (uds.py:420)
- Redis read (bars)  
  - connector: YES, m1_poller: YES, tick_preview: NO, UI: YES  
  - Функції: read_window / read_preview_window
- API serve  
  - Тільки UI server відповідає за HTTP API
- Symbol list  
  - Тільки UI читає список символів напряму з диска (os.listdir) — Правило 20 порушено  
  - Місце: `_list_symbols(data_root)` у `ui_chart_v3/server.py` (виклик з `/api/symbols`) — обходить UDS

### 1.4 Політики та ліміти (Policy Diff Table)

- TF allowlist (final)
  - Джерела: core/buckets.TF_ALLOWLIST = {60,180,300,900,1800,3600,14400,86400} — buckets.py:8; config_loader.DEFAULT_TF_ALLOWLIST = {300,900,1800,3600,14400,86400} — config_loader.py:71; config.json tf_allowlist_s = [60,180,300,900,1800,3600,14400,86400] — config.json:63
  - Конфлікт: ТАК — core містить 60/180, config_loader — ні; config.json вирівнює з core.

- TF allowlist (preview)
  - Джерела: config_loader.DEFAULT_PREVIEW_TF_ALLOWLIST = {60,180} — config_loader.py:72; config.json preview_tick_tfs_s = [60,180] — config.json:78
  - Конфлікт: НІ — збігаються.

- Cold-start limit
  - Джерела: COLD_START_BARS_BY_TF (клієнт) — app.js:109; min_coldload_bars_by_tf_s (сервер) — config.json:199;_WARMUP_BARS_BY_TF (warmup) — server.py:40
  - Конфлікт: ТАК — приклад H4: клієнт=1080 vs warmup=300 → warmup < клієнт → RAM miss → можливий порожній графік.

- Max bars cap
  - Джерела: MAX_BARS_CAP = 20000 — app.js:121; MAX_BARS_CAP = 20000 — server.py:107
  - Конфлікт: НІ — узгоджено.

- TF-specific caps
  - Джерела: caps_TF_CAP — server.py:108; COLD_START_BARS_BY_TF — app.js:109
  - Конфлікт: НІ — збігаються для перевірених TF.

- Polling interval
  - Джерела: UPDATES_BASE_FINAL_MS = 3000; UPDATES_BASE_PREVIEW_MS = 1000 — app.js
  - Примітка: тільки клієнтська політика — відсутнє server‑side примусування.

- Disk policy
  - Джерела: disk_policy="never" у server API — server.py:732; disk_policy="bootstrap" на warmup — server.py:84; BOOTSTRAP_WINDOW_S=60 — uds.py:228
  - Конфлікт: НІ — диск = лише bootstrap (очікувана поведінка).

- Redis tail retention
  - Джерела: tail_n_by_tf_s — config.json:211; PREVIEW_TAIL_RETAIN = 2000 — uds.py:46; UPDATES_REDIS_RETAIN_DEFAULT = 2000 — uds.py:96
  - УВАГА: final‑tail з config (напр. M5=2880), preview/updates — хардкод 2000; можливий mismatch → cold‑start для H4 може бути поза Redis‑tail → RAM miss.

- Scrollback chunk
  - Джерела: SCROLLBACK_CHUNK_BY_TF — app.js:138
  - Примітка: клієнтський параметр.

- MAX_EVENTS_PER_RESPONSE
  - Джерела: MAX_EVENTS_PER_RESPONSE = 500 — config_loader.py:73
  - Примітка: визначено в config_loader (хардкод/SSOT‑потреба).

- Bucket anchor offset
  - Джерела: day_anchor_offset_s = 68400 — config.json:175; day_anchor_offset_s_d1 = 75600 — config.json:176; resolve_anchor_offset_ms — buckets.py:25
  - Конфлікт: НІ — різні значення навмисно для різних анкерів/TF.

- Time geometry
  - Джерела: CandleBar.assert_invariants = end‑excl — bars.py:68; core/time_geom.bar_close_incl = end‑incl — time_geom.py:9;_ensure_bar_payload_end_excl — uds.py:151
  - Статус: МЕРТВИЙ КОНФЛІКТ — bar_close_incl визначено але не використовується; фактичний канон — end‑excl (time_geom.py / bucket_close_incl — мертві).

Критичні політичні конфлікти:

- Warmup caps менші, ніж cold-start caps у клієнта для деяких TF (приклад H4: warmup=300 vs client request=1080 → RAM miss → порожній графік).
- bar_close_incl фактично мертвий у runtime — реальний канон end-excl; назва/файл time_geom.py вводять в оману.
- TF_ALLOWLIST дублюється у трьох місцях (core, config_loader, config.json) — потребує SSOT-вирівнювання.

Актуальні уточнення (стан на 2026-02-14):

- `/api/config` уже є policy SSOT (`policy_version`, `build_id`, `window_policy`, allowlists).
- UI вже споживає policy із сервера (`getPolicyMap(...)`), але fallback-константи у `app.js` лишаються як запасний шлях.
- Final cold-load у `/api/bars` використовує `ReadPolicy(..., prefer_redis=bool(cold_load), disk_policy="never")`.
- Query-параметри `prefer_redis/force_disk` усе ще парсяться, але в final path ігноруються з loud warning (`query_param_ignored:*`).

### 1.5 Мертві / паралельні шляхи

- (див. D1–D11 у аудиті; потрібно послідовно видалити/зачистити dead paths і привести до одного SSOT)

| Пункт | Розташування | Виклики | Докази | Ризик |
| --- | --- | --- | --- | --- |
| D1 | `DiskLayer.last_mtime_ms` — disk_layer.py:410 | 0 | `grep "last_mtime_ms"` → тільки визначення | Низький — невикористовуваний, загромаджує API |
| D2 | `DiskLayer.read_window` (non-geom) — disk_layer.py:337 | 0 | зовнішня логіка (UDS викликає `read_window_with_geom`); grep ".read_window(" vs ".read_window_with_geom(" | Низький |
| D3 | `RedisLayer.is_prime_ready` — redis_layer.py:163 | 0 | UDS напряму використовує `get_prime_ready_payload` | Низький |
| D4 | `_digest_bar` — uds.py:1651 | 0 | Застаріло — замінено дедуплікацією за watermark | Низький |
| D5 | `_bar_to_update_event` — uds.py:1637 | 0 | Подія формується inline в `in_publish_update` | Низький |
| D6 | `_next_seq_for_event` — uds.py:1669 | 0 | Покладалося на `_digest_bar`; обидва не використовуються | Низький |
| D7 | Парсинг query-парам `prefer_redis/force_disk` у `/api/bars` | Ігнорується у final path policy-рейлом | Повертає `warnings: query_param_ignored:*` | Середній — вводить в оману API-клієнтів |
| D8 | `OVERLAY_POLL_INTERVAL_MS` — app.js:82 | 0 посилань | Замінено на `OVERLAY_POLL_FAST_MS` / `OVERLAY_POLL_SLOW_MS` (app.js:86) | Низький |
| D9 | `bar_close_incl / bucket_close_incl` — time_geom.py:9, buckets.py:44 | 0 у runtime | CandleBar і UDS застосовують `end-excl` (фактичний канон) | Високий — назви вводять в оману щодо канонічної семантики часу |
| D10 | `floor_bucket_start_ms` (дубль) — time_buckets.py:4 | `engine_b`, `derive` | Дублює `core/buckets.bucket_start_ms` з іншими одиницями параметрів | Середній — ризик розходжень / ускладнення підтримки |
| D11 | Lock у server.py:613 — `threading.Lock()` створюється в хендлері | Створюється локально в обробнику | Непридатно при `ThreadingHTTPServer` (не дає захисту) | Високий — створює помилкове враження потокобезпеки |

### 1.6 UI Render Pipeline — повний потік даних (актуально)

Cold start:
  init() → loadBarsFull()
    → GET /api/bars?limit=COLD_START_BARS_BY_TF[tf]
    → epoch guard check
    → controller.setBars(data.bars)          // chart_adapter_lite.js:735
      → normalizeBar(bar) each               // chart_adapter_lite.js:165
        → filter(Boolean)                    // drops bars with time<=0 or NaN OHLC
        → sort by time, dedupe by time       // chart_adapter_lite.js:741-751
        → candles.setData(deduped)           // LWC API
        → volumes.setData(volumeData)
    → setBarsStore(data.bars)                // app.js:1207 — caps to MAX_RENDER_BARS_WARM
      → rebuildBarsIndex()                   // Map(open_time_ms → index)
    → saveCacheCurrent()                     // uiCacheByKey.set(key, bars)

Incremental updates:
  pollUpdates() → GET /api/updates?since_seq=...
    → epoch guard, boot_id check
    → applyUpdates(events)                   // app.js:1583
      → sort by seq
      → for each event:
        → drop stale (bar.open_time_ms < lastOpenMs - tfMs)
        → forward gap guard (>3 TF periods → reload)
        → key match check (symbol/tf)
        → final>preview invariant
        → NoMix check
        → controller.updateLastBar(bar)      // chart_adapter_lite.js:793
          → normalizeBar(bar)
          → _rafPending = normalized
          → requestAnimationFrame(_flushChartRender)
            → candles.update(bar)
            → volumes.update(...)
        → upsertBarToStore(bar)              // app.js:1219

Scrollback:
  handleVisibleRangeChange() → ensureLeftCoverage()
    → GET /api/bars?to_open_ms=...&limit=SCROLLBACK_CHUNK
    → mergeOlderBars(olderBars)
    → controller.setBars(barsStore)          // full re-render

Where chart can become empty (актуально):

- `bars=[]` у `/api/bars` → `loadBarsFull()` робить `controller.clearAll()` і ставить `no_data`.
- Усі бари відкинуті `normalizeBar()` → `setData([])` і порожній графік.
- Warmup/cold-start mismatch для частини TF усе ще можливий (`disk_policy="never"` + коротке RAM вікно).

Silent/quiet catches, які лишаються:

- `loadScrollbackChunk`: non-AbortError ковтається.
- `fetchNewerBarsFromDisk`: catch повертає `false` без деталізації.
- `pollOverlay`: quiet catch з переходом на slow interval.
- Background refresh на switch: `loadBarsFull().catch(() => {})`.

### 1.7 Concurrency / Capacity / Multi-user

Поточна модель конкурентності:

- HTTP сервер: `http.server.ThreadingHTTPServer` (1 thread на запит).
- Reader UDS: один спільний `UnifiedDataStore(role="reader")` на весь UI-процес.
- UDS має `self._updates_lock` для updates-bus операцій, але RAM layer працює без внутрішнього lock.

Матриця ризикових shared-state у `ui_chart_v3/server.py`:

| Глобальний стан | Поточне використання | Ризик |
| --- | --- | --- |
| `_cfg_cache` | читається/перезаписується на запитах (`_load_cfg_cached`) | data race на складених операціях |
| `_overlay_anchor_warn_state` | mutable dict для rate-limit попереджень overlay | data race |
| `_overlay_obs_log_ts`, `_overlay_req_total`, `_overlay_prev_held_total`, `_overlay_prev_wait_ms_last`, `_overlay_prev_hold_since` | mutable counters/state overlay | data race |
| `_limit_clamp_count`, `_limit_clamp_last_log_ts` | глобальні лічильники clamp | data race |
| `_updates_log_state` + `_updates_log_lock` | lock/state створюються ліниво через `globals()` у `do_GET` | race на самій ініціалізації lock/state |

UDS/RAM аспект:

- `UnifiedDataStore` має lock для updates-буса.
- `RamLayer` (OrderedDict + list мутації в `get_window/set_window/upsert_bar`) не має lock, отже compound read-modify-write у багатопотоці вразливі.

Оцінка ємності (операційний ескіз):

| Метрика | ~10 клієнтів | ~50 клієнтів |
| --- | --- | --- |
| `/api/updates` RPS | ~5–10 | ~25–50 |
| `/api/overlay` RPS | ~5–10 | ~25–50 |
| Burst `/api/bars` на switch | ~2–5 | ~10–25 |
| Сукупний steady-state RPS | ~15–25 | ~70–120 |
| HoL/GIL ризик | низький | середній |

Висновок:

- Для ~10 клієнтів поточна модель зазвичай працездатна.
- Для ~50 клієнтів зростає ризик GIL contention + noisy-neighbor через shared mutable state і lock-less RAM layer.

### 1.8 Security

Матриця безпеки (AS-IS):

| Область | Статус | Факт / наслідок |
| --- | --- | --- |
| Static root | частково безпечно | `os.chdir(static_root)` + stdlib path translation; базовий захист від traversal |
| Експозиція `data_v3` | безпечно в межах процесу | `data_root` поза static root, JSONL не віддаються як static |
| CORS | відсутній | same-origin модель; cross-origin не підтримано |
| AuthN/AuthZ | відсутні | будь-який peer у мережі може читати API |
| Rate limit | відсутній | один «шумний» клієнт може деградувати сервіс |
| TLS | відсутній у процесі | plain HTTP, потрібен reverse proxy/perimeter |
| Cookie policy | частково добре | `HttpOnly; SameSite=Lax` встановлюється для `aione_client_id` |
| Security headers | відсутні | не виставляються `X-Content-Type-Options`, `X-Frame-Options`, `CSP` |
| Секрети | прийнятно | у server.py секрети не hardcoded, ENV не йде у клієнт |

Операційний висновок:

- Для dev/same-origin локально — прийнятно.
- Для 10+ користувачів у спільній мережі без perimeter зростає ризик несанкціонованого доступу і DoS-подібного навантаження через відсутність auth/rate-limit.

### 1.9 Модель відмов (AS-IS)

Формат: єдина матриця інцидентів (AS-IS після Slice-1..4).

| ID | Сценарій | Тригер | Механізм (факт) | Поточний статус | Виявлення | Рекомендований rail |
| --- | --- | --- | --- | --- | --- | --- |
| F1 | Short-window у final TF | Cold-load запитує більше, ніж прогріто/наявно в RAM | `RamLayer.get_window()` тепер повертає partial, а UDS додає `insufficient_warmup` + degraded (не silent empty) | **Частково пом’якшено** | `/api/bars` з `warnings` + `meta.extensions.expected/got` | Вирівняти warmup з cold-start policy + утримувати redis tail достатнім |
| F2 | Race lazy-init lock/state для updates-логів | Паралельні `/api/updates` | `_updates_log_state`/`_updates_log_lock` ініціалізуються через `globals()` у request path | **Актуально** | Навантажувальний тест паралельних updates | Підняти lock/state у module scope (eager init) |
| F3 | Тихий фоновий збій reload | Помилка `loadBarsFull()` у background refresh | `loadBarsFull().catch(() => {})` в switch-handlers ковтає помилки | **Актуально** | Лічильник відхилень `loadBarsFull` + UI status audit | Замінити silent catch на loud degraded status/diag |
| F4 | Повторні forward-gap reload | Великий стрибок `open_time_ms` у updates | Guard `_forwardGapReloadPending` блокує паралельні reload, але можливі послідовні повтори | **Частково пом’якшено** | Частота `forward_gap_reload` у статусі/логах | Ліміт повторів + backoff на повторний reload |
| F5 | Boot-id reset burst | Рестарт UI server | Усі клієнти скидають cursor і роблять cold-reload майже одночасно | **Актуально** | Сплеск `/api/bars` після зміни `boot_id` | Jitter на reload при boot-id mismatch |
| F6 | Lock-less RAM cache race | Багатопотокові read/update RAM | `RamLayer` (OrderedDict/list) без lock; compound операції не атомарні | **Актуально** | Concurrency stress (10+ вкладок/клієнтів) | Додати lock у RamLayer або серіалізувати доступ |
| F7 | Preview cold-start lag на M1/M3 | Старт/переключення preview TF за слабкого preview tail | Preview path комбінує history + preview, але при порожніх джерелах можливий `no_data` до першого апдейту | **Частково пом’якшено** | `/api/bars` для preview TF одразу після старту | Підсилити prime/tail для preview + явний degraded banner |
| F8 | Overlay помилки без loud-сигналу | Timeout/Redis проблеми в overlay polling | `pollOverlay` має quiet catch і лише сповільнює інтервал | **Актуально** | Розрив між live-ціною і overlay/diag | Додати overlay_error індикатор і лічильник |
| F9 | Symbol list з диска | Нові символи не відображаються до перезапуску | `/api/symbols` використовує `_list_symbols(data_root)` (`os.listdir`) | **Актуально** | Звірка `/api/symbols` vs `config.json.symbols` | Перейти на symbols із config як SSOT |
| F10 | Transient preview/final desync після рестарту | Рестарт сервера на активному preview потоці | Після boot reset UI спершу відновлює final snapshot, preview підтягнеться пізніше з updates/overlay | **Актуально, низько-середній ризик** | Моніторинг lag між now і останнім open | Зменшити вікно розсинхрону (faster preview bootstrap) |
| F11 | Конкурентний scrollback+updates | Користувач скролить вліво під час live updates | `barsStore` мутується з двох шляхів; візуально dedup у adapter згладжує дублікати | **Низький, але актуальний** | Порівняння `barsStore.length` vs unique open_time | Серіалізувати merge/upsert або централізувати через чергу |

Поточні пріоритети ризику:

- **P1:** F2, F3, F6, F9
- **P2:** F4, F5, F8
- **P3:** F1, F7, F10, F11

### 1.10 Failure model (коротко: що ламається → чому → як проявляється)

- **Failure:** Empty chart на cold start / switch  
  **Чому:** read-path не гарантує доступного джерела історії в hot-path. Якщо disk заборонений policy, RAM порожній (рестарт/eviction), а Redis недопраймлений або недоступний — повертається порожньо/недостатньо.  
  **Як проявляється:** `window_v1` дає `no_data` або preview-only; UI виглядає як «пустий/мерехтить».  
  **Найгірший ефект:** користувач не відрізняє «даних справді немає» від «платформа деградувала».

- **Failure:** Contract violations / «тихі фолбеки»  
  **Чому:** при деградації `meta`/`warnings` можуть бути неповними; критичні поля (`source`, `redis_hit`, `boot_id`) мають бути стабільно присутні за контрактом `window_v1`.  
  **Як проявляється:** `contract_violation` (як warning) або відсутність явного degraded-сигналу в UI.

- **Failure:** «Random зависання» при 10–50 трейдерах  
  **Чому:** `ThreadingHTTPServer` + lock-less/shared mutable state (зокрема RAM cache і process globals) у multi-thread path.  
  **Як проявляється:** long-tail latency, race-симптоми, періодичні порожні вікна/нестабільність під навантаженням.

- **Failure:** Policy SSOT розмазано  
  **Чому:** config уже містить ключові policy-поля, але клієнт/сервер можуть мати власні caps/винятки.  
  **Як проявляється:** конфлікт «клієнт просить 7 днів, бекенд гарантує 1 день», непередбачуваний UX.

- **Failure:** Безпека / production-ready контур недостатній  
  **Чому:** без auth/rate-limit/security headers/reverse proxy будь-який bad client може деградувати API; `http.server` непридатний як довгостроковий прод-контур.  
  **Як проявляється:** підвищений ризик DoS-подібних інцидентів, нестабільність і операційні ризики.

## 2. TO-BE ADR: "v3 Unified Data Plane for Trading UI"

### 2.0 Узгоджене TO-BE бачення (коротко, по суті)

UX-інваріанти:

- Switch symbol/TF: передбачувано швидкий (`p95 time-to-first-candle < 200мс` у warm-state); cold-state не порожній, а degraded-but-loud.
- Live на M1/M3: стабільний, без update-storm, з контрольованим throughput/throttling.
- Scrollback: чанками, без фризів; disk використовується лише в явному scrollback-режимі з бюджетами.

Server/data-інваріанти:

- Canonical representation: `bar_v1/window_v1/updates_v1` як нормативні контракти для всіх шарів.
- Одна policy SSOT: allowlists/caps/cold-start/warmup/redis-tail/scrollback/polling у єдиному server-enforced policy payload (`/api/config`).
- Disk hot-path ban: switch/cold-start — тільки RAM/Redis; disk — тільки scrollback/recovery з лімітами.
- Degraded-but-loud: `redis_down`, `prime_incomplete`, `insufficient_warmup`, `cursor_gap`, `disk_policy_blocked` завжди відображаються через `warnings[]/meta.degraded[]`.
- Production-grade web контур: ASGI/WSGI + process manager + reverse proxy + secure headers baseline.

### 2.0.1 Decision (явно)

`v3 Unified Data Plane for Trading UI`:

- Єдиний канон даних/геометрії (`bar_v1/window_v1/updates_v1`) і contract-first guardrails.
- Policy SSOT: сервер віддає повний policy payload (`policy_version`, `build_id`, caps, allowlists, chunking, budgets), клієнт не тримає магічні числа (окрім аварійного fallback).
- Hot-path не ходить у disk; disk працює лише як scrollback/cold storage.
- `Final > Preview` enforce і в UDS, і в клієнтському `applyUpdates`.
- Thread safety: shared state захищений lock/ізоляцією; serving model — worker-based.

### 2.0.2 Наслідки (Consequences)

- Redis має тримати достатній tail для hot-path (кероване зростання пам’яті як ціна за стабільний UX).
- Disk scrollback стає контрольованим і не впливає на p95 switch/cold-start.
- UI стає thin/client-contract driven: менше винятків, більше детермінованої контрактної поведінки.

### 2.1 Проблема (актуальний зріз)

Цільова архітектура залишається валідною: **єдина вузька талія UDS + policy SSOT + degraded-but-loud**.

Актуальні проблеми після Slice-1..4:

- shared mutable state у UI server без належної синхронізації (thread-safety);
- тихі/quiet catch шляхи в UI (background reload, overlay, scrollback);
- `/api/symbols` все ще читає диск напряму (`_list_symbols(data_root)`);
- time-geometry helper-и `bar_close_incl/bucket_close_incl` лишаються технічним боргом;
- perimeter hardening (auth/rate-limit/TLS/headers) не завершено.

### 2.2 Інваріанти ADR (чинні)

- **I1:** canonical time в runtime = end-excl (`close_time_ms = open_time_ms + tf_s*1000`).
- **I2:** один update-потік для UI (`/api/updates` + `applyUpdates`).
- **I3:** final > preview + NoMix.
- **I4:** disk hot-path ban у live-контурі (`disk_policy="never"` поза bootstrap).
- **I5:** policy SSOT читається з `/api/config`, fallback лише loud.

### 2.3 Статус виконання (виконано vs відкрито)

| Slice | Статус | Що зроблено / що лишилось |
| --- | --- | --- |
| Slice-1 | ВИКОНАНО | `/api/bars` no_data став fully-loud (warnings rail) |
| Slice-2 | ВИКОНАНО | Policy SSOT через `/api/config` + UI consume з loud fallback |
| Slice-3 | ВИКОНАНО | final cold-load використовує `prefer_redis` внутрішньо при `disk_policy="never"` |
| Slice-4 | ВИКОНАНО | short-window повертається partial+loud (`insufficient_warmup`) |
| Slice-5 | ВІДКРИТО | Thread-safety hardening (module-level locks + RamLayer lock) |
| Slice-6 | ВІДКРИТО | Silent/quiet catch hardening у UI |
| Slice-7 | ВІДКРИТО | `/api/symbols` перевести на config SSOT |
| Slice-8 | ВІДКРИТО (ОБЕРЕЖНО) | Time geometry cleanup (`bar_close_incl/bucket_close_incl`) |
| Slice-9 | ADR/OPS | Security perimeter: auth/rate-limit/TLS/headers + reverse proxy контракт |
| Slice-10 | ADR/PLATFORM | Міграція з `ThreadingHTTPServer` на production-клас serving model (ASGI/worker-process), без зміни контрактів API |

## 3. EXECUTION PLAN (оновлений)

### 3.0 Нормалізація плану P0..P6 (єдиний порядок виконання)

Щоб не мати двох паралельних треків (`Slice-*` і `P*`), далі працюємо за цим відповідником:

| Етап | Статус | Відповідність у поточному плані | Коментар |
| --- | --- | --- | --- |
| P0 — Freeze + Snapshot | ВИКОНАНО (baseline) | docs/audit `p0_freeze_snapshot` + записи 20260214-197..199 | Повторюваність метрик (latency/source-meta/bars+warnings) лишається обов’язковим pre-gate перед змінами |
| P1 — Policy SSOT consolidation | ВИКОНАНО | Slice-2 | `/api/config` + UI consume з `policy_version/build_id`; fallback лише аварійний і loud |
| P2 — Fix empty chart без disk hot-path | ЧАСТКОВО ВИКОНАНО | Slice-3 + Slice-4 | Є `prefer_redis` у cold-load і partial+loud rail; лишається жорсткий gate: без `bars=[]` без відповідних warnings |
| P3 — Thread safety / concurrency | ВІДКЛАДЕНО В ADR | Slice-5 + P10(ADR/PLATFORM) | Не робити часткових латок поза узгодженим ADR serving model |
| P4 — Disk as cold storage (scrollback-only) | ВІДКРИТО | (додається як P11) | switch/cold-start без disk; disk тільки в scrollback режимі з бюджетами |
| P5 — Dead paths cleanup | ВІДКРИТО | (додається як P12) | Тільки після стабілізації; видаляти через grep/test gates |
| P6 — Production-grade web | ВІДКРИТО (ADR/OPS) | Slice-9 + P10 | reverse proxy/TLS/headers/auth/rate-limit/observability + worker-based serving model |

Правило виконання: **йдемо строго по P0→P6**, але використовуємо вже виконані Slice-артефакти як доказову базу, без повторного “перепроєктування” done-кроків.

### P5 — Thread Safety Rails (ВІДКРИТО)

Scope:

- `ui_chart_v3/server.py`: прибрати lazy-init lock/state у request path, перейти на module-scope lock-и;
- `runtime/store/layers/ram_layer.py`: додати lock для `get_window/set_window/upsert_bar`.

Exit gate:

- паралельний stress `/api/updates` + `/api/bars` без race-побічок;
- відсутність runtime exceptions у RAM cache path.

### P6 — Loud Error Policy в UI (ВІДКРИТО)

Scope:

- `ui_chart_v3/static/app.js`: прибрати silent background `loadBarsFull().catch(() => {})`;
- додати явний status/diag для overlay/scrollback/fetchNewer catch-подій.

Exit gate:

- ін’єкція мережевої помилки дає явний `status/diag`, не silent fallback.

### P7 — Symbol List SSOT (ВІДКРИТО)

Scope:

- `ui_chart_v3/server.py`: `/api/symbols` з config (`symbols`) замість `os.listdir(data_root)`.

Exit gate:

- `/api/symbols` детерміновано відповідає `config.json.symbols`.

### P8 — Time Geometry Debt (ОБЕРЕЖНО)

Scope:

- `core/time_geom.py`, `core/buckets.py`, `runtime/ingest/polling/time_buckets.py`.

Exit gate:

- `grep bar_close_incl|bucket_close_incl` не зачіпає live runtime path;
- інваріант end-excl лишається без зміни контрактів.

### P9 — Security Perimeter (ADR/OPS)

Scope:

- security headers у UI server;
- reverse proxy профіль (TLS, rate-limit, IP policy);
- auth/rate-limit модель (мінімум ADR-рішення до PATCH).

Exit gate:

- явний perimeter runbook + перевірка headers у відповідях.

### P10 — HTTP Serving Model Migration (ADR/PLATFORM)

Policy (жорстко):

- `http.server.ThreadingHTTPServer` вважається **перехідним технічним боргом**;
- у прод-контурі його **не розвиваємо** (не додаємо нові stateful/fallback механіки поверх нього);
- нові зміни в UI/API мають бути сумісні з майбутнім worker-based serving model.

Scope:

- ADR на цільовий runtime serving stack (ASGI/worker-process модель, lifecycle, graceful shutdown, timeout policy);
- винесення mutable process-state у безпечні контури (explicit lock/queue/store), без прив’язки до thread-per-request;
- збереження контрактів `/api/bars`, `/api/updates`, `/api/config`, `/api/symbols` без protocol-break.

Exit gate:

- ADR схвалено з rollback-планом і чітким migration path;
- p95 для `/api/updates` і `/api/bars` не гірший за baseline;
- немає regression по final>preview/NoMix/disk hot-path ban.

### P11 — Disk as Cold Storage (scrollback-only)

Policy:

- `/api/bars` без `to_open_ms` (switch/cold-start) працює тільки через RAM/Redis (`disk_policy="never"`);
- disk дозволено лише у scrollback-сценарії (`to_open_ms`), з контрольованими бюджетами.

Scope:

- чітка policy-гілка для scrollback-only disk read;
- бюджети: `bytes/request`, `requests/sec`, `max_steps`;
- після disk-read дані промотуються у RAM cache.

Exit gate:

- switch/cold-start завжди `source=ram|redis`;
- scrollback може мати `source=disk`, але тільки з явними budget/degraded meta полями;
- відсутні фризи при chunked scrollback.

### P12 — Dead Paths Cleanup (після стабілізації)

Policy:

- видаляти лише те, що одночасно: (а) підтверджено unused, (б) покрито grep/test gate.

Scope:

- cleanup dead/legacy шляхів з розділу 1.5 (D1..D11) поетапно;
- без зміни зовнішніх контрактів і без змішування з feature-роботами.

Exit gate:

- для кожного cleanup-slice: `grep`-gate = 0 входжень цільового dead-path;
- система стартує; smoke/P0 snapshot не деградує.

## 4. GO / NO-GO (оновлено)

Дозволено (GO):

- P5, P6, P7 — локальні зміни без зміни зовнішніх контрактів.
- Будь-який patch, що зменшує race/silent-catch ризики без зміни протоколу.
- Docs-first уточнення rails/інваріантів/exit-gates, якщо вони відповідають поточному коду.

Дозволено з обережністю (GO WITH CARE):

- P8 — техборг геометрії часу, потрібен акуратний import-аудит.
- P10 підготовчі кроки (тільки ADR + discovery + benchmark baseline), без одночасного platform rewrite.

Заборонено без окремого ADR (NO-GO):

- зміна семантики часу end-excl → end-incl;
- зміна формату `/api/updates` або паралельний новий update-канал;
- додавання нового storage/cache шару поза UDS-вузькою талією;
- великий рефактор state machine у `app.js` одним кроком.
- будь-який "performance fix", що повертає disk у hot-path final updates/read.

Прямо заборонено (STOP-LIST для поточного етапу):

- розвивати `ThreadingHTTPServer` як довгострокову платформу (додавати нові критичні механізми поверх thread-per-request моделі);
- масштабувати прод-навантаження через збільшення poll-rate/threads замість архітектурної міграції serving model;
- додавати нові глобальні mutable стани у `ui_chart_v3/server.py` без lock/rail;
- приховувати деградації через quiet/silent catch у UI або server.

## 5. OPEN VERIFY BACKLOG (актуалізовано)

- V1: concurrency stress для `_updates_log_*` і RAM layer.
- V2: fault injection для UI quiet catches (overlay/background reload/scrollback).
- V3: перевірка `/api/symbols` після переходу на config SSOT.
- V4: time-geometry grep+import audit без зміни runtime контрактів.
