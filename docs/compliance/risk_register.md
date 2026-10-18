# Compliance Risk Register — Trading Platform v3

- **Дата аудиту**: 2026-03-08
- **Роль**: R_COMPLIANCE v1.0
- **Scope**: Full codebase — licenses, security, OWASP, CVE, disclaimers, data protection, technology lifecycle

---

## Резюме

| Severity | Знайдено | Закрито | Відкрито |
|----------|----------|---------|----------|
| **S0** | 0 | 0 | 0 |
| **S1** | 3 | 1 | 2 |
| **S2** | 4 | 1 | 3 |
| **S3** | 4 | 0 | 4 |
| **Разом** | 11 | 2 | 9 |

**Загальна оцінка**: Технічний baseline уже придатний для production-grade
localhost-only operation: секрети не в коді, `.gitignore` коректний, input
validation присутня, path traversal guards є, FXCM SDK не вендориться,
ліцензії deps сумісні, CI запускає tests + static exit gates +
dependency/security scan. **Головні gaps**: Python 3.7 EOL, Redis без auth,
відсутність hash-locked Python lockfile. **Commercial deployment boundary**:
written agreement with FXCM потрібен окремо.

---

## ФАЗА 1: AUDIT — Inventory

### 1.1 Dependency License Matrix

#### Python Dependencies

| Пакет | Версія | SPDX | Copyleft? | Сумісність з Proprietary | CVE | EOL? |
|-------|--------|------|-----------|--------------------------|-----|------|
| redis | 5.0.1 | MIT | Ні | ✅ Сумісна | [ASSUMED — verify: `pip audit`] | Ні |
| numpy | 1.21.6 | BSD-3-Clause | Ні | ✅ Сумісна | [ASSUMED — verify: `pip audit`] | Так (released 2022, series 1.21 no longer maintained) |
| pandas | 1.1.5 | BSD-3-Clause | Ні | ✅ Сумісна | [ASSUMED — verify: `pip audit`] | Так (released 2020, series 1.x deprecated) |
| python-dotenv | ≥1.2.2,<2.0 (до 18.10.2026: requirements `>=1.0,<2.0`, pyproject `>=0.21,<1.0`; прод — 0.21.1) | BSD-3-Clause | Ні | ✅ Сумісна | [VERIFIED terminal 18.10.2026: 0.21.1 — PYSEC-2026-2270, 1.2.2: 0; наш шлях не зачеплений] | Ні |
| aiohttp | ==3.14.3 (до 18.10.2026 — `>=3.9,<4.0`; прод — 3.14.3 з 18.10.2026, changelog 20261018-001; локальний `.venv` — 3.13.3) | Apache-2.0 | Ні | ✅ Сумісна (потрібен attribution) | [VERIFIED terminal 18.10.2026: `pip-audit` — 3.13.3: 24 бюлетені, 3.14.3: 0; розбір нижче] | Ні |
| requests (транзитивна, python-binance) | ≥2.33.0,<3.0 (прод 2.33.0, локальний `.venv` 2.32.5) | Apache-2.0 | Ні | ✅ Сумісна | [VERIFIED terminal 18.10.2026: PYSEC-2026-2275 закрито в 2.33.0; наш шлях не зачеплений] | Ні |
| urllib3 (транзитивна, requests) | ≥2.8.0,<3.0 (прод 2.6.3) | MIT | Ні | ✅ Сумісна | [VERIFIED terminal 18.10.2026: 2.6.3 — 5 бюлетенів, 2.8.0: 0; наш шлях не зачеплений] | Ні |
| multidict (транзитивна, aiohttp, yarl) | ≥6.9.1,<7.0 (прод 6.7.1) | Apache-2.0 | Ні | ✅ Сумісна | [VERIFIED зонд 18.10.2026: вада в 6.7.1 є, наш код її не викликає; 6.9.1: 0] | Ні |
| idna (транзитивна, yarl, requests, anyio) | ≥3.15,<4.0 (прод 3.11) | BSD-3-Clause | Ні | ✅ Сумісна | [VERIFIED зонд 18.10.2026: 3.11 — PYSEC-2026-215, не досяжний; 3.15: 0] | Ні |
| starlette (транзитивна, fastapi) | ≥1.3.1,<2.0 (прод 1.0.0) | BSD-3-Clause | Ні | ✅ Сумісна | [VERIFIED terminal 18.10.2026: 1.0.0 — 5 бюлетенів, сайдкар не запущений; 1.3.1: 0] | Ні |
| anyio (транзитивна, starlette, httpx) | ≥4.14.2,<5.0 (прод 4.13.0) | MIT | Ні | ✅ Сумісна | [VERIFIED terminal 18.10.2026: 4.13.0 — 2 бюлетені, не зачеплені; 4.14.2: 0] | Ні |
| forexconnect | 1.6.43 | **Proprietary/EULA** | N/A | ⚠️ Optional, не вендориться | N/A (proprietary) | [ASSUMED — verify: FXCM site] |

**Copyleft contamination**: ❌ Не виявлено. Жодна Python залежність не має GPL/LGPL/AGPL ліцензії.

#### npm Dependencies (ui_v4)

| Пакет | SPDX | Copyleft? | Сумісність | Attribution? |
|-------|------|-----------|------------|-------------|
| lightweight-charts 5.0.0 | Apache-2.0 | Ні | ✅ | Так — потрібен NOTICE |
| svelte ^5.0.0 | MIT | Ні | ✅ | Ні |
| vite ^6.0.0 | MIT | Ні | ✅ | Ні |
| typescript ^5.7.0 | Apache-2.0 | Ні | ✅ (devDep) | Ні (devDep) |
| uuid ^11.1.0 | MIT | Ні | ✅ | Ні |
| svelte-check ^4.0.0 | MIT | Ні | ✅ (devDep) | Ні |
| @sveltejs/vite-plugin-svelte ^5.0.0 | MIT | Ні | ✅ (devDep) | Ні |
| @tsconfig/svelte ^5.0.4 | MIT | Ні | ✅ (devDep) | Ні |
| @types/uuid ^10.0.0 | MIT | Ні | ✅ (devDep) | Ні |

**Copyleft contamination**: ❌ Не виявлено.

**Lock file**: ✅ `ui_v4/package-lock.json` існує.

#### Бюлетені aiohttp і requests — розбір 18.10.2026

Набір пакетів — точна копія `pip freeze` прод-venv `/opt/smc-v3/.venv` (Python 3.11.15, aiohttp 3.13.3 з C-парсером,
requests 2.33.0); аудит — `pip-audit -r <freeze> --no-deps`. Усі aiohttp-сервери слухають лише 127.0.0.1
(`:8000` ws_server, `:8010` agent_bridge, `:8020` smc-alerts), ззовні їх видно тільки через nginx (`client_max_body_size 1m`,
статику віддає сам nginx). `Upgrade` до upstream передає лише `location /ws` (ws_server). Зонди [VERIFIED зонд] — сирий
сокет проти справжнього `build_app()` ws_server (3546) або мінімального aiohttp-застосунку з тими самими налаштуваннями
(2108, 2111); 3.13.3 — з локального `.venv` (Python 3.14), 3.14.3 — з окремого venv на Python 3.11, як на проді.

| Бюлетень (виправлено в) | Суть | Чи зачіпає нас |
|---|---|---|
| PYSEC-2026-2108 (3.14.1) | Недокінчені великі WS-фрейми обходять ліміт розміру | **Так, DoS** [VERIFIED зонд]: `/ws` публічний, після 101 nginx лише тунелює байти. При `max_msg_size=65536` (як у ws_server) недокінчений фрейм на 64 МБ: 3.13.3 тримає в пам'яті всі надіслані 24 МБ і не рве з'єднання; 3.14.3 рве на ~0,2 МБ (пік 0,5 МБ) |
| PYSEC-2026-2111 (3.14.1) | Стиснене тіло запиту при прибиранні розпаковується одним шматком | **Так, помірний DoS** [VERIFIED зонд]: nginx пропускає тіло ≤1 МБ і `Content-Encoding` як є, а маршрути `:8000` тіло не читають. GET із deflate-тілом 255 КБ: пік алокацій 3.13.3 — 64 МБ на запит, 3.14.3 — 2,4 МБ |
| PYSEC-2026-3546 (3.14.2) | Request smuggling через відхилений WS upgrade (тіло не дочитується) | **Низький** [VERIFIED зонд]: ws_handler відхиляє upgrade до рукостискання (503 `ws_capacity*`, 400 на кривому рукостисканні). На 3.13.3 тіло з вкладеним GET не виконалося — решта з'єднання просто «ковтається» (і C-, і Python-парсер); nginx не тримає keepalive до upstream, тож чужі запити на це з'єднання не потрапляють. На 3.14.3 тіло читається як тіло, наступний запит обслуговується |
| PYSEC-2026-2107 (3.14.1) | Необмежена черга конвеєрних запитів | Низький: nginx не конвеєризує до upstream |
| PYSEC-2026-2094, -2103 (3.13.4), -2110 (3.14.1) | Заголовки/трейлери без ліміту, кілька `Host`, обхід `max_line_size` у C-парсері | Пом'якшено nginx (ліміти буферів, `Host $host`, буферизація тіла); напряму — лише локальні процеси |
| PYSEC-2026-2113 (3.14.1) | Payload не закривається при обриві клієнта | Ні: файли віддає nginx, `add_static` ws_server ззовні не досяжний |
| PYSEC-2026-2097, -2098, -2099 (3.13.4) | Static на Windows (NTLM), multipart | Ні: прод — Linux, multipart не парсимо |
| PYSEC-2026-2096, -2101 (3.13.4), -2106 (3.14.0) | Ін'єкція заголовків через `content_type` / `reason` / multipart | Ні: значення з констант коду |
| PYSEC-2026-3545 (3.14.3) | OOB-read у C-парсері відповіді на кривому chunked | Низький: клієнт ходить лише на api.telegram.org (smc-alerts) і Cloudflare Worker (`runtime/relay`) по TLS; DoS лише від зловмисного сервера |
| PYSEC-2026-3547 (3.14.2) | WS-клієнт розпаковує фрейми без узгодженого deflate | Ні: WS-клієнт лише в `tools/diag/*` до власного `127.0.0.1:8000` |
| PYSEC-2026-2095, -2100, -2102 (3.13.4), -2104, -2105 (3.14.0), -2109, -2112, -237 (3.14.1) | DNS-кеш, редиректи/cookies/digest/SNI, `CookieJar.save/load`, керівні символи в заголовках відповіді | Ні: ці можливості клієнта не використовуємо, хости фіксовані |
| requests PYSEC-2026-2275 (2.33.0) | `extract_zipped_paths()` бере передбачуване ім'я в tmp | Ні: розпаковує лише шлях усередині zip, а certifi у venv — звичайний файл (рання гілка `os.path.exists` у `requests/utils.py`); Binance вимкнено (`config.json:binance.enabled=false`, `smc-binance*` STOPPED). Прод уже 2.33.0 |

**Чому точний пін aiohttp.** Діапазон `>=3.9,<4.0` пропускав вразливі версії: прод і локальний `.venv` стояли на 3.13.3,
а CI щоразу ставить найновішу (18.10.2026 — 3.14.5). Тобто CI перевіряв не ту бібліотеку, що працює на проді. 3.14.3 —
найновіша версія, якій на 18.10.2026 вже ≥2 тижні (на PyPI 3.14.4 — з 05.10, 3.14.5 — з 11.10). Наступне підняття — через
dependabot або вручну, з прогоном pytest і exit gates.

**Ризики переходу 3.13.3 → 3.14.3 для ws_server** (за CHANGES.rst aiohttp): серверний парсер відкладає upgrade до
кінця тіла запиту; суворіші перевірки WS-парсера; heartbeat-таймер скидається на будь-які вхідні дані; інакше обробляються
керівні фрейми посеред фрагментованого повідомлення; стиснення payload за замовчуванням — у виконавці (`zlib_executor_size`); вихідні
заголовки з керівними символами тепер `ValueError`; `request.host` без `Host` більше не робить `getfqdn`; ліміт черги
конвеєрних запитів. Транзитивні залежності прод-venv (multidict 6.7.1, yarl 1.23.0, propcache 0.4.1, aiosignal 1.4.0,
aiohappyeyeballs 2.6.1, frozenlist 1.8.0) вимогам 3.14.3 відповідають — оновлюється один пакет. Нове в логах:
`NotAppKeyWarning` на рядкові ключі `request["api_consumer"]` (`runtime/api_v3/endpoints.py:181-182`) — один раз на процес.

**Перевірка 18.10.2026** (окремий venv, Python 3.11, решта пакетів — як на проді): pytest — 2685 passed / 20 skipped
і на 3.13.3, і на 3.14.3 (+ requests 2.34.2); вердикти exit gates однакові (червоні ті самі: ingest_no_direct_writers,
preview_plane, ui_live_candle_plane, coldstart_multisymbol, no_bare_except=38, а також config_singleton через
відсутній `ui_v4/dist` у worktree). Рейка — `tests/test_ws_server.py::test_ws_incomplete_frame_over_max_msg_size_drops_connection`:
червона на 3.13.3, зелена на 3.14.3.

Решту бюлетенів того самого аудиту прод-набору (multidict, starlette, urllib3, anyio, idna, python-dotenv) розібрано нижче.

#### Бюлетені транзитивних залежностей і python-dotenv — розбір 18.10.2026

Набір і метод ті самі: свіжий `pip freeze` прод-venv (18.10.2026, Python 3.11.15), `pip-audit -r <freeze> --no-deps`.
Запущені процеси з цього venv — `smc:smc-ws`, `smc:smc-fxcm`, `smc:smc-preview`, `smc-agent-bridge`, `smc-alerts`;
`smc-binance*` і `smc-ticks` — STOPPED. Жоден модуль платформи не імпортує напряму starlette, anyio, idna, urllib3,
multidict, httpx чи anthropic [VERIFIED grep `app/ runtime/ core/ tools/ tests/`].

| Бюлетень (виправлено в) | Суть | Чи зачіпає нас |
|---|---|---|
| multidict PYSEC-2026-4244 (6.9.1) | C-розширення: `X \| d.items()` і `d.items() - X` не звільняють ключ і значення кожного елемента операнда — пам'ять росте без меж | **Ні** [VERIFIED зонд]: вада справжня — одна така операція з 10 000 елементів на 6.7.1 лишає 10 000 зайвих посилань на значення, на 6.9.1 — 0. Але викликати її нікому: AST-пошук бінарних операцій над `.items()`/`.keys()`/`.values()` в aiohttp 3.14.3, yarl 1.23.0, python-binance і `app/ runtime/ core/` знайшов лише `&` над `.keys()` (aiohttp multipart, yarl `without_query_params`) і над звичайними `dict` (`runtime/alerts/config.py:46`, `core/alerts/codec.py:56`) — вразливих `\|` і `-` над `.items()` нема |
| idna PYSEC-2026-215 (3.15) | `idna.encode()` перевіряє контекстні правила довгого не-ASCII рядка до відсічення за довжиною — квадратичний час | **Ні, але один `request.url` відкрив би DoS** [VERIFIED зонд]: nginx пропускає в `Host` байти ≥0x80, а сервер за замовчуванням для `:80` — блок `archi` (`/api/archi/`, `/api/agent/` → `agent_bridge :8010` з `Host $host`). Сирий запит із `Host` = 4000 × U+0660 (8 КБ) на справжні `build_app()` ws_server (`/api/status`, `/api/context`, `/api/v3/*`, `/ws`, `/`) і `build_bridge_app()` agent_bridge — 0 викликів idna: aiohttp кодує хост лише у властивості `request.url`, а її не читає ні aiohttp, ні наш код (`request.url`/`request.host` у `runtime/` — 0). Контрольний обробник, що читає `request.url`, на тому ж запиті: `idna.encode` — 1,4 с на 3.11 проти 6 мс на 3.15, і весь цей час цикл подій стоїть. Клієнти (smc-alerts → api.telegram.org, relay → Cloudflare Worker) ходять на ASCII-хости — idna не викликається |
| starlette PYSEC-2026-161 (1.0.1), -2280, -2281 (1.1.0), -248 (1.3.0), -249 (1.3.1) | Шлях або `Host` підміняють хост у відновленому `request.url`; `HTTPEndpoint` викликає метод за довільним HTTP-методом; `StaticFiles` на Windows відкриває UNC-шлях (витік NTLM); ліміти urlencoded-форм не діють | **Ні** [VERIFIED terminal]: starlette працює лише у FastAPI-сайдкарі `runtime/api_v3/auth_validator.py` (ADR-0058, 058.1), а його на проді нема — програми в supervisor немає, `127.0.0.1:8001` не слухає, `auth_request` в nginx немає (`/api/v3/` іде на aiohttp `:8000`). І запущений він би не був зачеплений: перевіряє лише заголовок `X-API-Key`, не читає `request.url`, без `HTTPEndpoint`, `StaticFiles` і форм |
| urllib3 PYSEC-2026-141, -142 (2.7.0), -4175, -4176, -4177 (2.8.0) | Чутливі заголовки при крос-доменному редиректі через `ProxyManager.connection_from_url()`; повна розпаковка при потоковому читанні (Brotli, `drain_conn`); буферизація поля розміру chunked; нескінченний цикл на хвості deflate (2.6.2–2.7.0); TLS-налаштування HTTPS-проксі | **Ні** [VERIFIED terminal]: urllib3 тягне лише requests, requests — лише python-binance; Binance на проді вимкнено (`config.json:binance.enabled=false`, `smc-binance*` STOPPED). Проксі не налаштовані (ні в supervisor, ні в `/etc/environment`), Brotli у venv нема; 4176 і 4177 вимагають зловмисного сервера, а адресат один — api.binance.com по TLS |
| anyio PYSEC-2026-4024, -4025 (4.14.2) | Воркери `to_process` не дренують stderr — виклик зависає; `TLSStream.wrap()` звіряє не-ASCII ім'я з сертифікатом за IDNA 2003 | **Ні** [VERIFIED grep]: anyio у прод-venv тягнуть starlette і watchfiles (сайдкар не запущений) і httpx/anthropic 0.94.0, яких не імпортує ніхто; `to_process` не використовуємо, хости — ASCII |
| python-dotenv PYSEC-2026-2270 (1.2.2) | `set_key()`/`unset_key()` при перенесенні між пристроями пишуть крізь симлінк | **Ні** [VERIFIED grep]: лише `load_dotenv()` (`env_profile.py:63`, `runtime/relay/signal_relay.py:14`), `.env` ми не переписуємо |

**python-dotenv: яке джерело правильне.** `requirements.txt` — свідоме рішення: 05.05.2026 (ADR-0062, коміт `1f95530f`,
«cap <1.0 lifted») межу підняли до `>=1.0,<2.0`. `<1.0` у `pyproject.toml` — залишок: 08.03.2026 (`14559298`) файл
перевели з Python 3.7 (`fxcm-connector-v3`, `python-dotenv==0.21.1`) на `>=3.11` з діапазоном `>=0.21,<1.0`, як у
створеному тим самим комітом `requirements-broker.txt`, де `<1.0` потрібне досі (python-dotenv 1.0 вимагає Python ≥3.8);
05.05 межу підняли лише в `requirements.txt`. Тепер обидва файли — `>=1.2.2,<2.0`; `requirements-broker.txt` (`.venv37`,
broker_sidecar) лишається `<1.0` — ту саму ваду має й 0.21.x, але `set_key` там теж не викликається. Прод на 0.21.1, бо
venv після 05.05 з `requirements.txt` не перевстановлювали [INFERRED].

**Чому межі в `requirements.txt`, а не точні піни.** Батьківські пакети пропускають вразливі версії (aiohttp 3.14.3 —
`multidict>=4.5,<7.0`, requests — `urllib3>=1.26,<3`, fastapi 0.136.1 — `starlette>=0.46.0`), а прод-venv сам не
оновлюється. До того ж CI аудитує не прод: `pip-audit -r requirements.txt` розв'язує вимоги наново й бачить найновіші
версії (18.10.2026: multidict 6.9.1, starlette 1.8.0, idna 3.20, anyio 4.15.1, urllib3 2.8.0 — 0 бюлетенів), а прод
жив на старих. Нижня межа — версія з виправленням, усі вийшли ≥2 тижні тому: python-dotenv 1.2.2 — 01.03, idna 3.15 —
12.05, starlette 1.3.1 — 12.06, anyio 4.14.2 — 12.07, urllib3 2.8.0 — 15.09, multidict 6.9.1 — 21.09. Верхня — як у
батька. Точний пін, як в aiohttp, не ставимо: вразливих функцій жодного з цих пакетів наш код не викликає, а пін
транзитивної залежності довелося б рухати разом із батьком.

**Ризики переходу** (за журналами змін). multidict 6.8–6.9 — виправлення C-розширення (здебільшого free-threaded збірка),
з несумісного — лише відмова від Python 3.9. urllib3 2.7–2.8 — відмова від Python 3.9, `FutureWarning` замість
`DeprecationWarning`; шлях лише Binance. idna 3.12–3.15 — Unicode 17, раннє відсічення довгих міток. starlette 1.1–1.3.1
і anyio 4.14.x — лише сайдкар і тести `TestClient`. python-dotenv 0.21.1 → 1.2.2 — єдиний пакет, що змінює запущені
процеси поза aiohttp: його читають `smc:smc-fxcm`, `smc:smc-preview`, `smc-alerts` (`env_profile.load_env_secrets`).
Парсер `.env` той самий (AST `parser.py` і `variables.py` різняться лише префіксами `u""`); у `load_dotenv()` нове —
змінна `PYTHON_DOTENV_DISABLED` (вимикає читання; на проді не задана) і читання FIFO.

**Перевірка 18.10.2026** (окремі venv на Python 3.11 — локально 3.11.0rc2; базовий набір — прод freeze з aiohttp 3.14.3,
тобто прод після оновлення aiohttp того ж дня): pytest — 2686 passed / 20 skipped і на прод-версіях, і на цільових;
вердикти exit gates однакові (ті самі червоні, no_bare_except=38); `pip check` чистий; `pip-audit` цільового набору — 0
(без pip/setuptools); цільовий набір задовольняє `requirements-test.txt` без жодної доінсталяції, а прод-набір із новими
межами дає `ResolutionImpossible` — межі справді відсікають вразливі версії. Нове в тестах — одне
`StarletteDeprecationWarning` (`fastapi.testclient` радить `httpx2` замість `httpx`).

**Поза цим патчем** (той самий аудит): pip 26.0.1 — 4 бюлетені (→26.2, 29.07) і setuptools 79.0.1 — 1 (→83.0.0, 04.07) у
прод-venv: інструменти встановлення, у `requirements.txt` їх нема, працюючі процеси їх не імпортують. anthropic 0.94.0 і
httpx у прод-venv — сироти: їх не вимагає жоден файл вимог і не імпортує жоден модуль платформи.

**Наперед — пуш у браузер (ADR-0114 Rev 2, гілка ще не злита).** `WebPushSender` шле POST через aiohttp на endpoint, який
дає браузер; `validate_endpoint()` перевіряє суфікс хоста, але пропускає не-ASCII мітку перед дозволеним суфіксом. Зонд
[VERIFIED зонд] на копії перевірки: endpoint `https://<1900 × U+30FB + U+6F22>.fcm.googleapis.com/…` (1939 символів, під
лімітом 2048) перевірку проходить, а `yarl.URL()` на ньому — 234 мс на idna 3.11 проти 12 мс на 3.15, і кожна спроба
доставки платить знову (`UnicodeError` кеш yarl не запам'ятовує: 3 виклики — 3 промахи, по ~220 мс). Межа `idna>=3.15`
закриває це до злиття; вимога ASCII-хоста в `validate_endpoint()` — справа тієї гілки.

### 1.2 Власна ліцензія

[VERIFIED: LICENSE]

- Тип: Proprietary Source-Available & Contribution License v2.0
- Copyright: 2024–2026 Stanislav Std07-1
- Governing law: Czech Republic
- Non-Commercial definition: чітка (Section 1)
- Contribution clause: perpetual, irrevocable, sublicensable rights (Section 4)
- Warranty disclaimer: Section 11 — AS IS
- Liability limitation: Section 12 — max extent permitted by law
- AI-generated code: `[ASSUMED]` — Contribution clause (Section 4) covers "any material you submit", likely includes AI-assisted code, але **explicit AI clause відсутній**

### 1.3 FXCM SDK

[VERIFIED: docs/compliance/fxcm-sdk-license-review.md]

- SDK binaries у репо: ❌ Немає — **чисто**
- requirements.txt reference: ✅ Допустимо (reference ≠ redistribution)  
- EULA disclaimer в requirements.txt: ✅ Додано
- THIRD_PARTY_NOTICES.md coverage: ✅ Повне

### 1.4 Secrets Scan

| Перевірка | Результат |
|-----------|----------|
| Hardcoded secrets у .py/.ts/.js | ✅ Не знайдено |
| `.env` в `.gitignore` | ✅ Так |
| `data_v3/` в `.gitignore` | ✅ Так |
| `logs/` в `.gitignore` | ✅ Так |
| `env_profile.py` безпечний | ✅ Не логує значення secrets |
| `.env.example` clean | ✅ Тільки placeholder `***` |
| Credentials у config.json | ✅ Відсутні (але fallback pattern є — R6) |

### 1.5 OWASP Top 10 Checklist

| OWASP | Опис | Статус | Evidence |
|-------|------|--------|----------|
| A01:2021 Broken Access Control | WS/HTTP endpoints без auth | ⚠️ S1 (R1) | Mitigated: 127.0.0.1 binding |
| A02:2021 Cryptographic Failures | Redis plaintext localhost, WS ws:// not wss:// | ⚠️ S3 | Mitigated: localhost only |
| A03:2021 Injection | SQL/cmd injection | ✅ N/A | No SQL, subprocess не з user input |
| A04:2021 Insecure Design | — | ✅ OK | Defense-in-depth, input validation |
| A05:2021 Security Misconfiguration | Redis no auth | ⚠️ S1 (R2) | Mitigated: localhost binding |
| A06:2021 Vulnerable Components | Python 3.7 EOL, old numpy/pandas | ⚠️ S1 (R5) | Blocked by FXCM SDK |
| A07:2021 Auth Failures | No auth on endpoints | ⚠️ S1 (R1) | Mitigated: localhost |
| A08:2021 Software Integrity | No pip lockfile with hashes | ⚠️ S2 (R4) | npm lockfile ✅, pip ❌ |
| A09:2021 Logging Failures | IP logged on WS connect | ℹ️ S3 (R8) | Minimal PII, local only |
| A10:2021 SSRF | FXCM URL from .env | ✅ OK | Hardcoded broker endpoints |

### 1.6 Disclaimer Inventory

| Місце | "Not Financial Advice" | Risk Warning | Warranty Disclaimer |
|-------|----------------------|--------------|-------------------|
| LICENSE | ❌ | ❌ | ✅ Section 11 |
| README.md | ❌ | ❌ | ❌ |
| UI (Svelte) | ❌ | ❌ | ❌ |
| docs/ | ❌ | ❌ | ❌ |
| API responses | ❌ | ❌ | ❌ |
| SECURITY.md | N/A | N/A | N/A |

### 1.7 Existing Compliance Artifacts

| Артефакт | Існує? | Якість |
|----------|--------|--------|
| `LICENSE` | ✅ | Повна, professional |
| `SECURITY.md` | ✅ | Повна, SLA визначені |
| `THIRD_PARTY_NOTICES.md` | ✅ | Повна, всі deps покриті |
| `docs/compliance/fxcm-sdk-license-review.md` | ✅ | Детальна, conservative |
| `requirements.txt` FXCM disclaimer | ✅ | Є |
| Financial disclaimer | ✅ | README.md disclaimer додано |
| `docs/compliance/risk_register.md` | ✅ | Цей файл |
| Python lockfile (hashes) | ❌ | Відсутній |

### 1.8 Automated Enforcement

| Контроль | Enforcement | Статус |
|----------|-------------|--------|
| Tests | `.github/workflows/ci.yml` → `python -m pytest -q tests/` | ✅ |
| Static governance gates | `tools/exit_gates/manifest.ci.json` | ✅ |
| Dependency rule / no_bare_except / docs sync | exit gates in CI | ✅ |
| Python dependency audit | `pip-audit -r requirements.txt` | ✅ |
| Python static security scan | `bandit -q -r app core runtime tools` | ✅ |
| Frontend dependency audit | `npm audit --audit-level=high --omit=dev` | ✅ |
| Dependency update cadence | `.github/dependabot.yml` weekly pip/npm | ✅ |

### 1.9 Commercial Deployment Boundary

- Current approval scope = localhost-only single-user workstation.
- Commercial, team, hosted, or redistributed use with ForexConnect in the stack requires written agreement with FXCM.
- До отримання такого дозволу стек може бути production-grade технічно, але не має commercial-clear legal posture.

---

## ФАЗА 2: ASSESS — Risk Register

| ID | Домен | Severity | Likelihood | Опис | Evidence | Поточна мітигація | Рекомендована мітигація | SLA |
|----|-------|----------|------------|------|----------|-------------------|------------------------|-----|
| **R1** | Security/Auth | **S1** | Low (localhost) | WS/HTTP endpoints без автентифікації | [VERIFIED: ws_server.py] — no auth, no token, no session | 127.0.0.1 binding only | Документувати як accepted risk для localhost. Implement token auth якщо deployment змінюється | This sprint |
| **R2** | Security/Auth | **S1** | Low (localhost) | Redis без паролю — будь-який процес на machine має R/W доступ | [VERIFIED: config.json, ws_server.py, tick_publisher_fxcm.py] — zero `password=` parameter | 127.0.0.1 binding | Додати `requirepass` в Redis config + `password=` в connection code. Або документувати як accepted risk | This sprint |
| **R3** | Compliance/Financial | **S1** | ~~Medium~~ **CLOSED** | ~~Відсутній~~ Disclaimer додано в README.md (2026-03-09) | [VERIFIED: README.md:L3 — "Not Financial Advice"] | LICENSE Section 11 + README.md disclaimer | ✅ Закрито | Done |
| **R4** | Supply Chain | **S2** | Low | Python dependencies без lockfile з hash verification. `pip install` не верифікує integrity | [VERIFIED: no requirements.lock / pip.lock] | Pinned versions в requirements.txt | Створити lockfile: `pip-compile --generate-hashes` або `pip freeze --all > requirements.lock` | Plan |
| **R5** | Technology/EOL | **S1** | High | Python 3.7 EOL з June 2023. Відомі CVE без патчів. 2.5+ роки без security updates | [VERIFIED: pyproject.toml `requires-python = ">=3.7,<3.8"`] | ADR-0016 Proposed (Python upgrade plan) | Імплементувати ADR-0016 roadmap. Interim: document accepted risk | This sprint |
| **R6** | Secrets | **S2** | ~~Low~~ **CLOSED** | ~~cfg.get("user_id")~~ Credential fallback видалено (2026-03-09). Credentials тільки з env vars | [VERIFIED: grep cfg.get.*password — 0 matches] | Тільки env vars (FXCM_USERNAME, FXCM_PASSWORD) | ✅ Закрито | Done |
| **R7** | Data Integrity | **S2** | Medium | `ssot_jsonl_fsync: false` — при crash/power failure можлива втрата даних (OS buffer not flushed) | [VERIFIED: config.json `ssot_jsonl_fsync: false`] | `flush()` після кожного запису | Або увімкнути fsync, або задокументувати trade-off (performance vs durability) в ADR | Plan |
| **R8** | Rate Limiting | **S3** | Low | WS `switch` action та HTTP `/api/status` без rate limiting — потенційний DoS | [VERIFIED: ws_server.py — scrollback has rate limit, otherwise none] | Localhost only | Per-client action throttle | Batch |
| **R9** | Privacy | **S3** | Low | Client IP (`request.remote`) логується при WS connect | [VERIFIED: ws_server.py:L826] | Local-only, logs gitignored | Якщо logs коли-небудь будуть shared — hash або omit IP | Batch |
| **R10** | IP/License | **S2** | Low | AI-generated code: LICENSE Contribution clause не має explicit AI clause. IP ownership unclear | [VERIFIED: LICENSE_v1 Section 4 — "any material you submit"] | Implicit coverage через broad language | Додати explicit AI clause до LICENSE або policy document | Plan |
| **R11** | Validation | **S3** | Low | WS messages валідуються per-field, не через JSON Schema. Ad-hoc але повне | [VERIFIED: ws_server.py input checks] | Per-field validation covers known vectors | Формальна JSON Schema validation — nice-to-have | Batch |

---

## ФАЗА 2: Gap Analysis

### Що є ✅

1. **Власна ліцензія** — professional, comprehensive, Czech law jurisdiction
2. **SECURITY.md** — responsible disclosure, SLA, design principles
3. **THIRD_PARTY_NOTICES.md** — all deps with licenses
4. **FXCM SDK review** — thorough, conservative position documented
5. **Secrets management** — clean: .env, .gitignore, env_profile.py
6. **Input validation** — WS size limits, symbol/TF allowlists, log sanitization, path traversal guard
7. **Error handling** — degraded-but-loud, structured error frames, no secret leaking
8. **npm lockfile** — package-lock.json present
9. **Localhost binding** — all services 127.0.0.1 only
10. **Automated enforcement** — CI запускає tests, static exit gates, dependency review, pip-audit, bandit, npm audit

### Що відсутнє ❌

1. **Python lockfile** — no hash-verified lockfile
2. **Redis auth** — no `requirepass`
3. **ADR-0016 implementation** — Python 3.7 EOL migration stalled
4. **AI code ownership clause** — not explicit in LICENSE
5. **Commercial FXCM permission** — written agreement with FXCM відсутній

### Що прийнятне за умови ⚠️

1. **No auth on endpoints** — OK for localhost, MUST review if deployment changes
2. **No encryption at rest** — OK for personal machine, MUST review if shared
3. **`ssot_jsonl_fsync: false`** — OK if data recovery from FXCM is possible, SHOULD document
4. **Commercial use boundary** — OK лише після окремого письмового дозволу FXCM

---

## ФАЗА 3: REMEDIATE — Roadmap

### P1: Financial Disclaimer (R3) — ✅ DONE (2026-03-09)

**Файли**: `README.md`  
**Що**: Disclaimer додано: "Not Financial Advice", ризик втрати капіталу, SMC = аналітичні мітки  
**Verify**: `grep "Not Financial Advice" README.md` → 1 match ✅

### P2: Redis Auth Documentation (R1, R2) — 0 LOC

**Файли**: Цей risk register (оновити статус)  
**Що**: Задокументувати як accepted risk для localhost deployment  
**Trigger for re-evaluation**: будь-яка зміна з localhost на network

### P3: Config Credential Fallback (R6) — ✅ DONE (2026-03-09)

**Файли**: `runtime/ingest/tick_publisher_fxcm.py`  
**Що**: `cfg.get("user_id")` / `cfg.get("password")` / `cfg.get("url")` / `cfg.get("connection")` fallback видалено. Credentials тільки з env vars  
**Verify**: `grep "cfg.get.*password\|cfg.get.*user_id" runtime/` → 0 matches ✅

### P4: Python 3.7 EOL Documentation (R5) — ✅ ACCEPTED RISK (2026-03-09)

**Що**: Python 3.7 EOL з June 2023. Blocked by FXCM SDK (forexconnect 1.6.43 requires Python 3.7).  
**Поточна мітигація**: localhost only, no untrusted input, ADR-0016 Proposed  
**Trigger для re-evaluation**: FXCM releases Python 3.11+ compatible SDK → activate ADR-0016 migration

### P5: AI Code Clause (R10) — ⏸️ DEFERRED (needs legal opinion)

**Що**: LICENSE Contribution clause (Section 4) implicitly covers AI-generated code через "any material you submit", але explicit AI clause відсутній  
**[LEGAL OPINION NEEDED]**: Exact wording for AI code IP clause, jurisdiction-specific implications  
**Interim**: Current broad contribution language provides reasonable coverage

---

## Accepted Risks (documented, no action needed)

| Risk | Acceptance rationale | Re-evaluation trigger |
|------|---------------------|----------------------|
| No auth on WS/HTTP (R1) | localhost 127.0.0.1 binding | Deployment change to network |
| Redis no password (R2) | localhost only, single user | Deployment change, multi-user |
| No encryption at rest | Personal machine, physical access = root anyway | Shared machine, cloud deployment |
| WS no rate limit (R8) | localhost only, single user | Network exposure |
| Client IP logging (R9) | Local-only, logs gitignored | Log shipping, multi-user |

---

> **Наступний scheduled review**: 2026-06-08 (quarterly)  
> **Trigger для позачергового review**: зміна deployment topology, нова залежність з copyleft, FXCM ToS зміна, push to public repo
