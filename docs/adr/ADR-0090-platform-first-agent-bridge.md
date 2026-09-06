# ADR-0090: Platform-first — AI як зовнішній читач: винесення Agent Bridge з ws_server

## Метадані

| Поле | Значення |
| ---- | -------- |
| ID | ADR-0090 |
| Статус | **Accepted** (owner-рішення 2026-09-06; реалізація слайсами S1–S7, статус кожного — у §8) |
| Дата | 2026-09-06 |
| Автори | Станіслав (owner, рішення «система і є система, це торгова платформа, до якої може підключитись AI й читати її») + Fable 5.1 (архітектор; RECON = workflow 3 лінзи проти коду 06.09) |
| Будується на | ADR-0058 (публічний read-only API як контракт для зовнішніх споживачів), ADR-0049 (WakeEngine як IPC для зовнішнього споживача — сам принцип зберігається, змінюється хост), ADR-0076 (unified auth gate — переїжджає з ws_server у bridge) |
| Supersedes (partial) | ADR-0049 §delta_loop (WakeEngine у процесі ws_server), ADR-0085 §D1 (`frame.archi_chart` у публічному WS-кадрі), ADR-0086 §D2 (маунт `/api/public/snapshot` у ws_server), ADR-0088 (хост handlers `/api/archi/wakes\|now` у ws_server); ADR-0075/0087/0089 — без змін по суті, змінюється лише процес-хост WakeEngine |
| Поважає | I1 (UDS — єдиний writer; bridge читає SMC через ws_server, не через UDS), I5 (degraded-but-loud: bridge down → UI Арчі чесно «зв'язок втрачено», платформа не помічає), S1 (SMC read-only), X31 (нуль змін у `trader-v3/`; bridge живе в репо платформи як опційний адаптер) |
| Зачіпає шари | `runtime/agent_bridge/` (NEW: app, routes, wake_engine, narrative, public_snapshot, wake_cards), `runtime/ws/ws_server.py` (−≈1 000 LOC), `config.json` (нова секція `agent_bridge`, top-level `wake_engine`/`agent_console` знімаються), `tools/agent-bridge.supervisor.conf` (NEW), `tools/archi-nginx.conf` (+gorn/ochi vhosts у репо), `ui_v4` (overlay через endpoint замість полів кадру), `tests/agent_bridge/`, docs (README, SSOT правил) |
| Initiative | `platform_first_v1` |

---

## Quality Axes

- **Ambition target**: **R3** — архітектурна межа продукту: платформа = самодостатній публічний
  продукт (broker pipeline → UDS → live chart → SMC → read-only API); будь-який зовнішній
  клієнт, включно з AI-агентом, підключається і **читає**. Це і є ідентичність, яку owner
  будував два роки; Арчі — перший клієнт, не тіло.
- **Maturity impact**: **M4 → M5** — зчеплення з агентом стає структурним фактом (окремий процес,
  окремий користувач, окрема config-секція `enabled=false` за замовчуванням), а не дисципліною;
  рестарти платформи і адаптера незалежні; публічний процес більше не читає і не пише приватні
  файли іншого сервісу.

---

## 1. Контекст

### 1.1 Owner-рішення (2026-09-06)

> «Я два роки будував цю систему як "категорично без AI", потім вирішив спробувати, так з'явився
> Арчі. Зараз ми пишемо, що система побудована під AI-агентів тощо. Це потрібно відрізати. Система і є
> система, це торгова платформа, до якої може підключитись AI й читати її. Якщо в коді це не так —
> виправити. Разом з тим, що перезапуски чіпляють Арчі, Binance тощо.»

### 1.2 Що каже код (RECON 06.09, workflow «coupling-map», усі рядки відкриті)

Імпортів з `trader-v3/` у `core/`, `runtime/`, `app/` немає (перевірено grep). Зчеплення **операційне
й наративне**, і воно вшите в `runtime/ws/ws_server.py` (3 873 LOC після SEC-06) трьома шарами:

| # | Шар | Факти (file:line) | Категорія |
|---|---|---|---|
| 1 | **Платформа читає і пише приватні файли бота** | 15 роутів `/api/archi/*` (маунт `ws_server.py:3177-3191`) читають з `agent_console.data_dir=/opt/smc-trader-v3/data` (`config.json:336-341`): thinking-архів (`:338-351`, `:2366-2371`), `v3_agent_directives.json` (`:2387`, `:2489-2491`, `:3048`), relationship memo (`:2539`), логи бота (`:2855-2857`), `wake_cards.py:33-34`. **Пишуть**: `POST /api/archi/owner-note` (`:2925-2955`) і `POST /api/archi/proposals/review` (`:2970-3024`, переписує директиви); `POST /api/archi/chat` (`:2581-2594`) штовхає у `archi:web_inbox` = витрати Claude | 3 — має зникнути з платформи |
| 2 | **Агентська машинерія в гарячому циклі** | `WakeEngine.tick` у `delta_loop` кожні 2 с (`:1591-1598`, init `:3614-3648`, `wake_engine.enabled=true` за замовчуванням); `NarrativeEnricher` вставляє тезу бота у **кожен анонімний** WS-кадр (`:884-902`; tier-gate = TODO `narrative_enricher.py:101`); `frame.archi_chart` рахується на кожен delta (`:1523-1526`, `_archi_chart_wire :441-512`); `/api/public/snapshot` монтується безумовно (`:3193-3207`) | 2 — переїжджає в bridge |
| 3 | **Репо і конфіг** | top-level `wake_engine`, `agent_console` у `config.json`; три агентські SPA (`ui_archi`, `ui_archi_v2`, `ui_ochi`); `tools/bot_patches/`, `tools/check_wake.py`, `check_directives*.py`, `reset_budget.py`, `diag/archi_health.py` з hardcoded `/opt/smc-trader-v3`; 8 тестів мають форми бота; `gate_ui_no_direct_redis.py:12-19` whitelist-ить ws_server для agent-Redis | 2/3 |

Справді generic read-only поверхня: `/ws`, `/api/status`, `/api/context`, `/api/v3/*` (ADR-0058).

### 1.3 Чому це проблема, а не смак

- **Ідентичність.** README, CONTRIBUTING, SSOT правил (`.github/copilot-instructions.md` I7 «Autonomy-First
  (Арчі)» як інваріант платформи, X29/X30), AGENTS.md §2.1, `docs/index.md` §6, `system_current_overview.md`
  §919-941, `SECURITY.md` — описують платформу як тіло агента (деталі і заміни — §5.7).
- **Рестарти.** `supervisorctl restart smc:smc-ws` гасить будильник Арчі (WakeEngine), його SSE і `/api/agent|archi`;
  до 05.09 група `smc` тягнула ще й Binance. Бот і платформа не мають бути в одному життєвому циклі.
- **Безпека.** Публічний процес мав шлях на запис у пам'ять агента і читав його файли; RCE у ws_server =
  доступ до директив, думок і логів бота (ADR-0091 §2).
- **Продукт.** «Premium analytics, про які ніхто не знає, що це AI» (`narrative_enricher.py:4-12`) —
  чужа тезa у кадрі кожного анонімного відвідувача без tier-гейту.

---

## 2. Альтернативи

### A. Лишити як є, вимкнути прапорцями (`wake_engine.enabled=false`, `agent_console.enabled=false`)

- Pro: нуль коду.
- Con: код і `config.json` лишаються «під агента»; прапорці на проді все одно `true`; ідентичність не змінюється.
- **REJECT** — це не відрізання, це пауза.

### B. Видалити агентську машинерію з платформи повністю (перенести в `trader-v3/`)

- Pro: найчистіша межа.
- Con: WakeEngine споживає SMC-стан платформи (ціна, ATR, зони, structure) кожні 2 с — з боку бота це
  або дублювання SMC-розрахунку, або новий внутрішній API; три SPA живуть на доменах платформи; порушує
  X31 у зворотний бік (масовий переїзд коду в чужий репо однією операцією).
- **DEFER** — кінцевий стан для console/chat/proposals (бот сам володіє своєю поверхнею), не перший крок.

### C. Винести в окремий опційний процес `runtime/agent_bridge/` у репо платформи (CHOSEN)

- Pro: механічне переміщення ≈1 900 LOC без зміни контрактів UI; окремий supervisor-program поза групою
  `smc`, окремий користувач, окремий порт; `agent_bridge.enabled=false` за замовчуванням; платформа
  лишає лише generic read-only поверхню; рестарти незалежні; X31 чистий.
- Con: два процеси замість одного; WakeEngine потребує SMC-входу через localhost-endpoint; +2-5 с
  latency wake (було 2 с in-process).
- **ACCEPT.**

### D. Перейменувати без переміщення (`client_thesis`, «external annotations»)

- Pro: дешево.
- Con: ті самі процес, конфіг і файли бота в ws_server; косметика.
- **REJECT** як самостійне рішення; використовується як S7 поверх C.

---

## 3. Рішення

### 3.0 Принципи

1. **Платформа = продукт.** `runtime/ws/ws_server.py` обслуговує `/ws`, `/api/status`, `/api/context`,
   `/api/v3/*`, статику. Жодного роуту з `archi|agent|public` у назві, жодного читання/запису поза
   `/opt/smc-v3` і Redis-ключами платформи.
2. **AI = клієнт.** Усе, що існує заради агента, живе в `runtime/agent_bridge/` і працює як окремий процес
   `smc-agent-bridge` (127.0.0.1:8010), окремий unix-user (ADR-0091), `agent_bridge.enabled=false`
   за замовчуванням у репо; на хості вмикається лише env `AI_ONE_AGENT_BRIDGE_ENABLED=1` у
   supervisor-програмі (config.json = git-singleton, overlay заборонений `gate_config_singleton`).
3. **Контракти UI не змінюються.** Три SPA б'ють у ті самі шляхи; змінюється лише `proxy_pass` у nginx.
   Виняток — `frame.archi_chart`/`archi_thesis`/`archi_presence` у WS-кадрі (§3.3).
4. **Платформа не є writer у чужий стан.** Роути, що пишуть файли бота, переїжджають у bridge як є (S1),
   а в S6 стають проксі до bot-owned endpoint (альтернатива B як кінцевий стан для writes).

### 3.1 Слайси (кожен ≤150 LOC нового коду; переміщення = окремі коміти «move only»)

| Слайс | Що | Обсяг | Verify |
|---|---|---|---|
| **S1** Bridge app + console/ochi routes | `runtime/agent_bridge/{app,context,routes_console,routes_ochi,thinking_archive,wake_cards,__main__}.py` (handler'и перенесені verbatim в `register_*_routes(app, ctx)`; `_archi_auth` → `BridgeContext.authorize` над `runtime.api.auth.check_bearer`; `wake_cards.py` — git mv; thinking-helpers → `thinking_archive.read_thinking_records`); `[program:smc-agent-bridge]` у **новому** `tools/agent-bridge.supervisor.conf` (поза групою smc; вимкнений bridge = живий процес лише з `GET /api/bridge/health` `enabled:false`, приватні маршрути 404 — supervisor бачить RUNNING, без BACKOFF/FATAL); `tools/archi-nginx.conf` → `127.0.0.1:8010`; ws_server: блок консолі/agent-маршрутів видалено (−995 LOC), Redis-клієнт лишається для `/api/v3` TokenStore/kill_switch. **Не переносилось**: `runtime/api/public_snapshot.py` + його маунт (untracked WIP ADR-0086) — лишається в ws_server із `_console_data_dir`; переїде разом із WIP. Ціна для `/api/archi/now` — Redis `{ns}:tick:last:{SYM}` (як `/api/context`), не SmcRunner: поза сесією `price=null` + `degraded:[price_unavailable]`. Health без auth: `GET /api/bridge/health` | move ≈1 020; new ≈300 (+тести) | `pytest tests/test_agent_bridge_app.py tests/test_ws_server_no_agent_surface.py`; живий процес: `AI_ONE_AGENT_BRIDGE_ENABLED=1 ARCHI_AUTH_TOKEN=x python -m runtime.agent_bridge --port 8011` → health 200, `/api/archi/now` 401/200; `grep -cE "/api/(archi\|agent)" runtime/ws/ws_server.py` = 0 у закоміченому дереві |
| **S2** WakeEngine у bridge | `runtime/agent_bridge/wake_engine.py` (move 699 LOC) з власним 2-с циклом; SMC-вхід = localhost-only `GET /api/internal/smc_snapshot?symbol=` на ws_server (`allow 127.0.0.1`, не проксюється nginx) — рівно ті поля, що `WakeEngine._tick_symbol` читає з `SmcRunner` сьогодні (`wake_engine.py:79, 143-200`); pure `core/smc/wake_check.py`, `wake_types.py`, `auto_wake.py`, `structure_forecast.py` лишаються в core | move 699; new ≈100 (endpoint + клієнт) | wake_events у Redis з'являються при вимкненому `ws_server.wake_engine` (його більше нема) і ввімкненому bridge; latency wake ≤5 с (лог `WAKE_EVENT` vs `tick_last`) |
| **S3** Overlay замість полів кадру | `NarrativeEnricher` (202 LOC) + `_archi_chart_wire` (`:441-512`) → bridge; `GET /api/agent/overlay?symbol=&tf=` → `{archi_chart, archi_thesis, archi_presence}` (bearer або публічний — owner-рішення, див. §7); ws_server: `render_frame`/`delta_loop` без enrichment (`:884-906`, `:1523-1526`, `:1600-1612` видалено); `ui_v4` ArchiLayer/NarrativePanel полять overlay при увімкненому toggle | move ≈260; new ≈80 (endpoint + ui fetch) | `render_frame` не містить ключів `archi_*` (новий тест); overlay endpoint 200 з тими самими числами, що раніше в кадрі (порівняння на одному snapshot) |
| **S4** Config SSOT | `config.json`: секція `agent_bridge` (§3.6); top-level `wake_engine`/`agent_console` знято; резолвер `runtime/agent_bridge/config.py` + `APP_AGENT_BRIDGE_CFG`; новий CI exit-gate `platform_config_no_agent_keys` (top-level ключі `archi\|agent\|wake\|thesis\|narrative\|presence` заборонені, крім `agent_bridge`); `gate_adr_config_sync`: `agent_bridge*` → ADR-0090/0049, три форми статусу, схема імен `ADR-NNNN-*` | ≈150 з тестами | `run_exit_gates` (обидва маніфести) + `pytest tests/test_agent_bridge_config.py` зелені; ws_server стартує без секції `agent_bridge` |
| **S5** Репо-гігієна | `tools/bot_patches/`, `check_archi*`, `check_wake.py`, `check_directives*.py`, `reset_budget.py`, `diag/archi_*` → `trader-v3/tools/` (один коміт у кожному репо); три SPA → `agent_bridge/ui/` (шляхи API без змін); `gate_ui_no_direct_redis.py`: ws_server знято з `ALLOW_FILES`; 8 тестів → `tests/agent_bridge/`; новий тест `test_ws_server_has_no_agent_surface` | move; new ≈40 | гейт без винятку зелений; `pytest tests/agent_bridge` зелений |
| **S6** Writes → bot-owned | `owner-note`, `proposals/review`, `chat` → проксі до endpoint бота (companion trader-v3 ADR); bridge стає strictly read-only щодо файлів бота; `agent_bridge.data_dir` знімається | new ≈60 (+ trader-v3) | `sudo -u <bridge-user> test -w /opt/smc-trader-v3/data` = fail; UI Арчі працює |
| **S7** Client-agnostic імена | `archi_chart/archi_thesis/archi_presence` → `client_levels/client_thesis/client_presence` + поле `source`; docstring `narrative_enricher.py:4-12` без «no one knows is AI-driven» | ≈60 (wire + types.ts + тести) | контракт-тест wire |

Порядок: S4 → S1 → S2 → S3 → S5 → S6 → S7. S4 перший, бо решта читає нову config-секцію. S1–S3 —
weekend-роботи з observation (ADR-0060 D9.1).

### 3.2 Топологія після S3

```text
Cloudflare ──► nginx ──► aione-smc.com          ──► ws_server :8000  (/ws, /api/status, /api/context, /api/v3/*)
                    ├──► archi|gorn|ochi.…        ──► smc-agent-bridge :8010 (/api/archi/*, /api/agent/*, /api/public/*)
                    │                                    │  bearer (ADR-0076)          │ read: Redis agent:*, thesis:*, wake:conditions
                    │                                    │  GET /api/internal/smc_snapshot ◄─ localhost-only на ws_server
                    └──► ui_v4 ArchiLayer (toggle) ──► GET /api/agent/overlay (bridge)
trader-v3 (smc_trader_v3, окремий program)  ◄──► Redis IPC (wake:events, archi:web_inbox) ◄──► bridge
```

### 3.3 Контракт `frame` (зміна wire, supersedes ADR-0085 §D1)

- `frame.archi_chart`, `narrative.archi_thesis`, `narrative.archi_presence` **зникають** з публічного
  WS-кадру. UI отримує їх з `GET /api/agent/overlay?symbol=&tf=` лише при увімкненому toggle «Арчі на чарті».
- Кадр без агентських полів = за замовчуванням для всіх; будь-який tier-гейт живе в bridge, не в
  `render_frame`.

### 3.4 Процеси й рестарти

- `[group:smc]` = `smc-ws, smc-fxcm, smc-ticks, smc-preview` (+ `smc-binance*` з `autostart=false`, поки
  Binance вимкнено — ADR-0054 rev 2 §3.0). `smc-agent-bridge` — **окремий** program у власному conf-файлі.
- `restart smc:smc-ws` не чіпає bridge і бота; `restart smc-agent-bridge` не чіпає графік. Бот перезапускається
  лише своїми `ops/archi-on|off`.

**Деплой S1 на VPS (після go; поки Арчі OFF — bridge може лишатись вимкненим):**

1. `git pull` (S4+S1) → `sudo supervisorctl restart smc:smc-ws` — консоль зникає з `:8000` (лог `AGENT_BRIDGE_DISABLED`).
2. `sudo cp tools/agent-bridge.supervisor.conf /etc/supervisor/conf.d/agent-bridge.conf`; у `environment=` цього файлу
   виставити `AI_ONE_AGENT_BRIDGE_ENABLED="1"` (коли потрібна консоль) і `ARCHI_AUTH_TOKEN="…"` (перенести зі
   `smc-ws`, потім прибрати звідти); `sudo supervisorctl reread && sudo supervisorctl update`.
3. Smoke: `curl -s 127.0.0.1:8010/api/bridge/health`; `curl -s 127.0.0.1:8010/api/agent/state` → 401; з Bearer → 200/204.
4. nginx (backup у `/root/` спершу): `sites-enabled/archi` і `sites-enabled/gorn` — `proxy_pass` для `/api/archi/` та
   `/api/agent/` → `http://127.0.0.1:8010/...`; `/api/public/` (gorn) лишити на `:8000` до переїзду WIP;
   `sudo nginx -t && sudo systemctl reload nginx`; origin-smoke `curl -sk --resolve archi.aione-smc.com:443:127.0.0.1 https://archi.aione-smc.com/api/agent/state` → 401.
5. Observation 60 с (ADR-0060 D9.1): `supervisorctl status smc-agent-bridge smc:smc-ws`, `tail -n 50 /var/log/smc-v3/agent_bridge.stderr.log`.
6. Rollback: nginx backup назад + `sudo supervisorctl stop smc-agent-bridge`; платформа — `git checkout c4b300e -- .` + restart smc:smc-ws.

### 3.5 Що лишається в платформі і чому

- `core/smc/wake_check.py`, `wake_types.py`, `auto_wake.py`, `structure_forecast.py` — чисті алгоритми над
  SMC-снапшотом без I/O; це «умови на ринку», придатні будь-якому клієнту. Можуть переїхати під
  `agent_bridge/core` пізніше без зміни поведінки.
- `/api/context` — generic SMC-контекст для будь-якого зовнішнього споживача (ADR-0058), із hardening з
  ADR-0091 P5 (валідація символу, без тексту винятків у відповіді).

### 3.6 Config (S4)

```jsonc
"agent_bridge": {
  "enabled": false,                 // репо-дефолт; на хості — лише env AI_ONE_AGENT_BRIDGE_ENABLED=1 (overlay заборонений)
  "host": "127.0.0.1", "port": 8010,
  "data_dir": "/opt/smc-trader-v3/data", // git-singleton, тому шлях тут; read-only через group; після S6 — знімається
  "smc_snapshot_url": "http://127.0.0.1:8000/api/internal/smc_snapshot",
  "wake_engine": { ...поточна секція wake_engine без змін (enabled діє лише при agent_bridge.enabled)... },
  "console":     { "enabled": true, "auth_token_env": "ARCHI_AUTH_TOKEN", "allow_no_token_dev_mode": false,
                   "thinking_max_items": 100, "feed_max_items": 200 },
  "public_snapshot": { "enabled": false, "ttl_s": 5 }
}
```

Резолвер `runtime/agent_bridge/config.py` (`resolve_agent_bridge_config`): секція відсутня → усе вимкнено;
невалідне значення env = ValueError на старті; off-стан логується `AGENT_BRIDGE_DISABLED`, env-override —
`AGENT_BRIDGE_ENABLED_BY_ENV` (I5). Токен консолі — лише з env за ім'ям `console.auth_token_env`.

### 3.7 Docs-слайс (текст, без коду; виконано `ee03177`, до S4)

| Місце | Зараз | Стає |
|---|---|---|
| `README.md:7-77`, `README.uk.md` | hero з тезою Арчі; lead «wired over Redis to Archi…»; «Why it isn't just another trading bot»; «Meet Archi» | hero-підпис без тези; lead: «Open real-time SMC trading platform: broker-grade pipeline (FXCM/Binance) → UDS → live WebSocket chart + in-process SMC analytics + read-only API. External clients — including an AI trading agent, maintained separately — connect and read.»; «What it is» (pipeline, UDS/Final>Preview, $0 SMC, read-only API, exit gates/ADR); «External clients» 4 рядки з посиланням на trader-v3 |
| `.github/copilot-instructions.md:111,207,208,802-803` | I7 «Autonomy-First (Арчі)» як platform invariant; «I0–I7»; X29/X30 | платформа = **I0–I6**; I7/X29/X30 → `trader-v3/docs`; X31/X32 = «межа зовнішньої підсистеми: платформа не посилається на внутрішнє trader-v3; runtime-дані клієнта не живуть у дереві платформи» |
| `CONTRIBUTING.md:7-9, 28-29` | «AI-native… much of the code is written by AI agents»; I7 у списку | «developed under strict invariant governance (.github/)»; список без I7 |
| `AGENTS.md:199, 219-321` | «AI agent governance»; 100 рядків файлового дерева бота | «contributor & assistant rules»; §2.1 = 5 рядків «External clients» |
| `CLAUDE.md:13,96-103,151,184` | P5 Autonomy-First; «I0–I7» | один рядок під P4: «працюєш у trader-v3/? читай його CLAUDE.md/ADR-024»; «I0–I6» |
| `docs/index.md:114-121`, `system_current_overview.md:23,919-941`, `docs/ui_api.md:561-587` | «Agent Console» як шар платформи | «Optional adapter for external clients (agent_bridge, config-gated, bearer, private hostname)»; view-by-view описи → `agent_bridge/ui/README` |
| `SECURITY.md:31,40` | «all services bind 127.0.0.1, single-user workstation» | реальна топологія (§3.2) + вимоги ізоляції з ADR-0091 |
| `runtime/smc/narrative_enricher.py:4-12` | «premium analytics layer that no one knows is AI-driven» | «інжектує опубліковану зовнішнім клієнтом тезу; платформа не авторка контенту» |

> Виконання (`ee03177`): усе з таблиці, крім X29/X30 — їх сенс (hard block / hidden constraint) залишено як
> покажчики на ADR-024, бо ці номери цитують prompts, role specs і skills; межа платформи винесена в нове **X40**.
> «I0–I7» у `role_spec_*`/`prompts/*` — залишок для окремого проходу.

---

## 4. Наслідки

### Pro

- Публічний процес не читає і не пише чужі файли; ідентичність продукту відповідає коду.
- Рестарти незалежні структурно (окремий program поза групою `smc`).
- `ws_server.py` −≈1 000 LOC; гейт `ui_no_direct_redis` знову захищає ws_server без винятку.
- Агентські UI не змінюються (лише nginx); бот не змінюється до S6 (X31).
- Готовий каркас для будь-якого іншого зовнішнього клієнта (overlay endpoint, bridge як шаблон).

### Con

- Два процеси; wake-latency 2 → ≤5 с (bridge полить `/api/internal/smc_snapshot`).
- ADR-0085 wire міняється: клієнти старих бандлів `ui_v4` до S3-деплою не побачать шар Арчі (toggle
  порожній, без помилок).
- До S6 bridge усе ще читає файли бота — тому bridge = окремий користувач з read через групу (ADR-0091 P3).

### Ризики

| Ризик | Severity | Mitigation |
|---|---|---|
| `/api/internal/smc_snapshot` витікає назовні | H | bind-check `request.remote == 127.0.0.1` + не проксюється жодним vhost + тест `test_internal_snapshot_rejects_non_loopback` |
| Розсинхрон SMC між кадром і overlay | M | overlay несе `smc_seq`/`server_ts_ms` зі snapshot; UI показує вік |
| Втрата wake-подій під час рестарту bridge | M | WakeEngine cooldown/dedup у Redis (ADR-0087 E2) переживає рестарт; bridge стартує ≤5 с |
| S1 «move only» ламає імпорти тестів | L | `tests/agent_bridge/` у тому ж коміті; CI |

---

## 5. Rollback

- Кожен слайс = окремий коміт; rollback = `git revert` + `supervisorctl restart` відповідного program.
- До S3 включно старі маршрути існують у bridge під тими ж шляхами → nginx `proxy_pass` назад на `:8000`
  повертає стару топологію лише якщо повернути й ws_server-коміт S1 (обидва revert-и разом).
- `agent_bridge.enabled=false` вимикає весь адаптер без коду; платформа працює як звичайно.

---

## 6. Verification (після кожного слайсу)

| Слайс | Команда/перевірка | Очікувано |
|---|---|---|
| S4 | `python -m tools.run_exit_gates --manifest tools/exit_gates/manifest.ci.json` (гейт `platform_config_no_agent_keys`); `pytest tests/test_agent_bridge_config.py` | 0 top-level agent-ключів; гейти ok; 18 тестів |
| S1 | `grep -cE "/api/(archi\|agent)" runtime/ws/ws_server.py` (закомічене дерево; WIP додає лише `/api/public`); `pytest tests/test_agent_bridge_app.py tests/test_ws_server_no_agent_surface.py`; живий процес `python -m runtime.agent_bridge --port 8011` + `curl /api/bridge/health`, `/api/archi/now` | 0; 12 тестів; health 200, 401 без bearer / 200 з |
| S2 | лог bridge `WAKE_EVENT`, Redis `LLEN wake:events` росте; ws_server лог без `WakeEngine` | подія ≤5 с після умови |
| S3 | `python -c` build_full_frame → keys | нема `archi_*`; overlay 200 |
| S5 | `run_exit_gates` (`ui_no_direct_redis` без винятку); `pytest tests/agent_bridge` | ok |
| S6 | `sudo -u <bridge> test -w /opt/smc-trader-v3/data` | fail (нема запису) |

---

## 7. Open Questions

1. Overlay endpoint — bearer чи публічний? Публічний = ті самі дані, що зараз у кожному кадрі (тобто
   нічого нового не відкривається); bearer = чесніше для «premium». Owner-рішення до S3.
2. Companion trader-v3 ADR для S6 (bot-owned endpoint для owner-note/proposals/chat) — після Фази 0 ADR-0054
   чи паралельно?
3. Чи лишати `core/smc/wake_*` у `core/` (generic «умови на ринку») чи перенести під `agent_bridge/core` —
   рішення після S2 за фактом хто ще їх імпортує.

---

## 8. Статус слайсів

| Слайс | Статус | Коміт |
|---|---|---|
| S4 config | ✅ 2026-09-06 | `c4b300e` (env-override замість overlay; CI-гейт `platform_config_no_agent_keys`; деплой на VPS окремо, за go) |
| S1 bridge + routes | ⏳ | — |
| S2 WakeEngine | ⏳ | — |
| S3 overlay | ⏳ | — |
| S5 репо-гігієна | ⏳ | — |
| S6 writes → bot | ⏳ | — |
| S7 імена | ⏳ | — |
| Docs-слайс §3.7 | ✅ 2026-09-06 | `ee03177` (нове X40; X29/X30 збережено як покажчики) |

---

## Changelog

- 2026-09-06: Created (Accepted за owner-рішенням). RECON: workflow 3 лінзи (coupling-map, docs-framing,
  public-surface) проти `ed4ca99`…`1ace5f9`; факти §1.2 з file:line. Того ж дня без цього ADR зроблено
  quick wins ADR-0091 P1 (платформа під `smc`, ANTHROPIC-ключ знято з env ws_server, SEC-06 rails).
- 2026-09-06: Docs-слайс §3.7 виконано (`ee03177`). Відхилення від таблиці: X29/X30 залишено з оригінальним
  сенсом як покажчики на `trader-v3/ADR-024` (їх цитують `.github/instructions/trader-v3.instructions.md`,
  `prompts/arhci-handoff`, `role_spec_patch_master` Z13/Z14, skills) — переозначення створило б протиріччя;
  правило «platform-код не читає/пише файли клієнта поза `runtime/agent_bridge/`» = нове **X40**.
  Залишок: `role_spec_*`/`prompts/*`/`skills/contradiction-audit` ще пишуть «I0–I7» — окремий doc-keeper прохід.
- 2026-09-06: S4 реалізовано з відхиленнями від плану (амендовано §3.0 п.2, §3.1, §3.6, §6): (1) «VPS overlay»
  неможливий — `gate_config_singleton` забороняє `config.local.json` і `AI_ONE_CONFIG_PATH`; замість нього env
  `AI_ONE_AGENT_BRIDGE_ENABLED` у supervisor-програмі хоста; (2) `data_dir` лишається VPS-шляхом у git-singleton
  до S6; (3) `console` отримує `enabled` + `allow_no_token_dev_mode` (перенесено як є з `agent_console`);
  (4) `tools/check_config.py` — бот-скрипт для `/opt/smc-trader-v3/config.json` (кандидат S5), тому перевірка
  top-level ключів = CI exit-gate `platform_config_no_agent_keys`. Рев'ю: workflow 3 лінзи + верифікатори,
  5 підтверджених S3 (усі виправлено), 8 спростовано як pre-existing/поза патчем.
- 2026-09-06: S1 виконано move-only (амендовано §3.1 S1, §3.4 деплой, §6). Відхилення від плану:
  (1) `public_snapshot.py` не переносився — untracked WIP ADR-0086, маунт лишається в ws_server із
  `_console_data_dir`; (2) ціна `/api/archi/now` — Redis `tick:last` замість SmcRunner (bridge = окремий
  процес; tick mid замість M1 close, поза сесією `price_unavailable`); (3) ws_server лишає Redis-клієнт для
  `/api/v3`; (4) додано `GET /api/bridge/health` без auth для smoke/supervisor; (5) `_read_thinking_records`
  → `thinking_archive.read_thinking_records`. Рев'ю: workflow 4 лінзи + адверсарні верифікатори (23 агенти) — 12 підтверджених (S2: supervisor класифікує exit до startsecs як failed start → BACKOFF→FATAL; тому вимкнений bridge лишається RUNNING лише з health; решта S3 — семантика ціни в docs, застарілі коментарі, мертвий local у закоміченому дереві, runbook 502), усе виправлено; 7 спростовано як pre-existing/поза патчем.
