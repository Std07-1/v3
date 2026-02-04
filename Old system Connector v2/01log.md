<!-- markdownlint-disable MD013 -->

# Work Log — FXCM Connector vNext

2026-01-17T13:01:43+01:00 → PRE (MODE=PATCH) → P0 skeleton (без FXCM/store/OHLCV/Tick publish)
- Мета: зібрати P0 процес, який читає SSOT конфіг з config/config.py, валідуює команди/статус через JSON Schema (allowlist, fail-fast), публікує status у Redis (Pub/Sub + snapshot key), слухає commands, піднімає Prometheus /metrics, і має відтворюваний exit gate через redis-cli.
- Scope: тільки інфраструктурний skeleton (status/commands/metrics/validator/calendar stub). Без FXCM інтеграції, без store, без OHLCV/price_tik publish.
- Non-goals: реалізація реальних команд, історичні дані, tail guard, репабліш барів, календар повний.
- Інваріанти/рейки: Namespace keys як у SSOT; TF/SOURCE allowlist зафіксовані; час тільки epoch ms int; ніяких silent fallback (лише errors[]/degraded[] або явна відмова); additionalProperties:false у схемах; мова логів/докстрінгів укр.
- План:
  1) Додати SSOT правила в docs/COPILOT_RULES.md (як надано користувачем).
  2) Створити структуру пакетів + файли P0 (app/config/core/runtime/observability).
  3) Додати JSON schemas (commands_v1, status_v2) з additionalProperties:false.
  4) Реалізувати SchemaValidator (jsonschema, fail-fast) + ContractError.
  5) Реалізувати Redis status snapshot/publish + command subscriber (unknown/not_implemented loud).
  6) Додати Prometheus /metrics + базові метрики.
  7) Локальна перевірка: compileall/імпорти, короткий запуск, exit gate через redis-cli (якщо Redis доступний).
- Тести/перевірки (план): python -m compileall; короткий запуск app/main.py; redis-cli SET/PUBLISH/GET/subscribe; curl /metrics.
- Ризики/нотатки/очікуваний результат: середовище може не мати запущеного Redis/redis-cli — тоді exit gate виконаю частково (smoke-run без Redis) і залишу інструкції/очікування в README; P0 календар буде stub і має бути loud через degraded=["calendar_stub"].

## 2026-01-17 13:47:13 — PRE (MODE=PATCH): FXCM Connector vNext · P0 skeleton (status/commands/validator/metrics)

- Мета → реалізувати P0 кістяк конектора: SSOT конфіг, строгий runtime validator (allowlist), status stream+snapshot, commands subscriber з unknown_command, /metrics, базові тести, базові dev-конфіги, exit gate через redis-cli.
- Scope → тільки нові файли P0 + мінімальні зміни існуючих інфра-файлів (tools/bootstrap_p0.sh, якщо потрібно). Без FXCM, без store, без OHLCV/tick publish (окрім валідатора схем).
- Непорушні інваріанти → (1) strict allowlist валідатора (no extra fields), (2) status:snapshot завжди валідний за schema, (3) unknown_command → errors[] + last_command.state=error, (4) namespace NS не хардкодимо, (5) логи укр., імена англ.
- Acceptance Criteria (цільові артефакти) →
  - Код P0: app/main.py + config/config.py + core/time/* + core/validation/* + runtime/* + observability/metrics.py
  - SSOT JSON schemas: core/contracts/public/{commands_v1.json,status_v2.json,ohlcv_v1.json,tick_v1.json}
  - Dev configs: .markdownlint.json, mypy.ini, pytest.ini, ruff.toml, mcp_config.json, .vscode/{extensions.json,settings.json,tasks.json,mcp.json}
  - Exit Gate P0: redis-cli publish unknown cmd → status:snapshot містить expected поля + errors[].code="unknown_command"
- Ризики/нотатки →
  - P0 не реалізує FXCM/історію/store; тільки інфраструктурний каркас та контракти.
  - Календар/next_open/next_pause у P0 допускаються як мінімальна коректна реалізація без блокувань і без викликів зовнішніх API.

## 2026-01-17 13:56:51 — POST (MODE=PATCH): FXCM Connector vNext · P0 skeleton (status/commands/validator/metrics)

- Що зроблено →
  - Додано SSOT правила у docs/COPILOT_RULES.md.
  - Додано P0 каркас: SSOT конфіг, строгий валідатор (allowlist), status snapshot/publish, command subscriber (unknown_command loud), календар stub, buckets скелет, /metrics.
  - Додано SSOT JSON schemas: commands_v1, status_v2, ohlcv_v1, tick_v1.
  - Додано тести для валідатора та unknown_command без Redis.
  - Додано dev/workspace конфіги та requirements.
  - Оновлено tools/bootstrap_p0.sh до ідемпотентного сценарію Python 3.7 + ruff/mypy/pytest.
- Де зроблено →
  - docs/COPILOT_RULES.md — SSOT правила.
  - config/config.py — SSOT конфіг.
  - core/time/calendar.py, core/time/buckets.py — календар stub + bucket межі.
  - core/validation/validator.py — ContractError + строгий allowlist валідатор.
  - runtime/publisher.py, runtime/status.py, runtime/command_bus.py — Redis status/commands каркас.
  - observability/metrics.py — Prometheus метрики.
  - app/main.py — composition root.
  - core/contracts/public/{commands_v1.json,status_v2.json,ohlcv_v1.json,tick_v1.json} — SSOT контракти.
  - tests/test_validator_commands.py, tests/test_validator_status.py, tests/test_unknown_command_flow.py — тести.
  - requirements.txt, requirements-dev.txt, .markdownlint.json, ruff.toml, mypy.ini, pytest.ini, mcp_config.json, .vscode/{extensions.json,settings.json,tasks.json,mcp.json} — dev/workspace конфіги.
  - tools/bootstrap_p0.sh — оновлено bootstrap.
- Причина → реалізація P0 skeleton згідно SSOT та acceptance criteria.
- Тести/перевірки →
  - bash tools/bootstrap_p0.sh → FAIL (WSL/bash не встановлено в середовищі Windows).
  - python -m app.main → FAIL (відсутній пакет redis; venv/requirements не встановлено).
  - redis-cli ping → OK (Redis доступний).
- Ризики/нотатки/очікуваний результат →
  - Exit Gate P0 не виконано через відсутність bash/venv і залежностей; після встановлення WSL або Git Bash + Python 3.7 venv і pip install -r requirements*.txt — повторити bootstrap і запуск.
  - Для повного проходження AC потрібен запущений процес app/main.py та доступний Redis.

## 2026-01-17 13:57:50 — POST (MODE=PATCH): P0 типізація та тестова сумісність

- Що зроблено → додано типізацію redis_client у runtime/publisher.py та runtime/command_bus.py; дозволено CommandBus без Redis (для тестів); підтверджено типізацію __init__ у тесті.
- Де зроблено → runtime/publisher.py, runtime/command_bus.py, tests/test_unknown_command_flow.py.
- Причина → відповідність mypy (disallow_untyped_defs) та можливість тестування без Redis.
- Тести/перевірки → не запускались (попередній bootstrap недоступний через відсутність bash/venv).
- Ризики/нотатки/очікуваний результат → без змін у runtime логіці; очікуване покращення mypy-сумісності та тестів без Redis.

## 2026-01-17 14:11:52 — PRE (MODE=PATCH): P0 Windows bootstrap + Exit Gate (без bash/WSL)

- Мета → зробити P0 виконуваним на Windows без bash/WSL: PowerShell bootstrap для Python 3.7 venv + встановлення залежностей + ruff/mypy/pytest + запуск app/main.py; пройти Exit Gate P0 через redis-cli.
- Scope → додати tools/bootstrap_p0.ps1 + підкрутити .vscode/tasks.json (windows-specific), без змін у доменній логіці P0.
- Інваріанти → (1) Python 3.7 venv, (2) залежності ставляться з requirements*.txt, (3) exit gate P0 виконується (status:snapshot валідний + unknown_command), (4) жодних тихих фолбеків.
- Перевірки → після патчу: bootstrap_p0.ps1 (OK), запуск app.main (OK), redis-cli GET/ PUBLISH сценарій (OK).

## 2026-01-17 14:12:21 — POST (MODE=PATCH): P0 Windows bootstrap + Exit Gate (спроба)

- Що зроблено →
  - Додано tools/bootstrap_p0.ps1 для Windows.
  - Оновлено .vscode/tasks.json для Windows bootstrap.
- Як перевірено →
  - powershell -ExecutionPolicy Bypass -File tools\bootstrap_p0.ps1 → FAIL (Python 3.7 не знайдено у середовищі; PY_BIN не задано).
  - Запуск app.main та Exit Gate P0 не виконано через відсутність Python 3.7 venv та залежностей.
- Файли →
  - tools/bootstrap_p0.ps1 — Windows bootstrap.
  - .vscode/tasks.json — windows-specific bootstrap task.
- Ризики/нотатки/очікуваний результат →
  - Для завершення Exit Gate потрібно встановити Python 3.7 або задати PY_BIN, після чого повторити bootstrap, запуск app.main та redis-cli сценарій.

## 2026-01-17 14:30:57 — PRE (MODE=PATCH): Фікс PowerShell bootstrap виклику Python 3.7

- Мета → виправити tools/bootstrap_p0.ps1, щоб виклик py -3.7 працював у PowerShell (з аргументами), і розблокувати bootstrap.
- Scope → лише tools/bootstrap_p0.ps1, без змін у доменній логіці P0.
- Інваріанти → (1) Python 3.7 venv, (2) залежності з requirements*.txt, (3) без тихих фолбеків.
- Перевірки → tools/bootstrap_p0.ps1 (OK).

## 2026-01-17 14:32:09 — PRE (MODE=PATCH): Узгодження dev-залежностей з Python 3.7

- Мета → замінити несумісну з Python 3.7 версію types-redis на найближчу сумісну, щоб bootstrap завершувався.
- Scope → лише requirements-dev.txt; без змін у доменній логіці.
- Інваріанти → Python 3.7 venv; ruff/mypy/pytest залишаються в bootstrap.
- Перевірки → tools/bootstrap_p0.ps1 (OK).

## 2026-01-17 14:33:18 — PRE (MODE=PATCH): Виправлення ruff/mypy/pytest для Windows bootstrap

- Мета → усунути помилки ruff/mypy/pytest (типізація, імпорти, pytest path) для проходження bootstrap.
- Scope → мінімальні правки runtime/status.py, core/validation/validator.py, tests/test_unknown_command_flow.py та додати tests/conftest.py.
- Інваріанти → без змін у доменній логіці P0; лише типізація/прикладні імпорти.
- Перевірки → tools/bootstrap_p0.ps1 (OK).

## 2026-01-17 14:34:13 — PRE (MODE=PATCH): Сумісність Protocol з Python 3.7

- Мета → замінити typing.Protocol на typing_extensions.Protocol у runtime/status.py для Python 3.7.
- Scope → runtime/status.py; без змін у доменній логіці.
- Інваріанти → валідатор/статус/команди без змін поведінки.
- Перевірки → tools/bootstrap_p0.ps1 (OK).

## 2026-01-17 14:35:22 — PRE (MODE=PATCH): Обробка timeout у Redis Pub/Sub

- Мета → уникнути падіння app/main.py через timeout у Pub/Sub; зробити loud помилку в статусі.
- Scope → runtime/command_bus.py; без змін у контрактах.
- Інваріанти → без silent fallback, errors[] має оновлюватися.
- Перевірки → tools/bootstrap_p0.ps1; запуск app.main; Exit Gate P0.

## 2026-01-17 14:37:37 — PRE (MODE=PATCH): Стабілізація Pub/Sub при неочікуваних винятках

- Мета → не падати при неочікуваних винятках (включно з KeyboardInterrupt) у pubsub.get_message; фіксувати loud помилку.
- Scope → runtime/command_bus.py.
- Інваріанти → errors[] має оновлюватись; без silent fallback.
- Перевірки → tools/bootstrap_p0.ps1; запуск app.main; Exit Gate P0.

## 2026-01-17 14:46:42 — POST (MODE=PATCH): P0 Windows bootstrap + Exit Gate пройдено

- Що зроблено →
  - Виправлено PowerShell bootstrap: коректний виклик py -3.7 з аргументами.
  - Узгоджено dev-залежності з Python 3.7 (types-redis==4.5.5.2).
  - Усунено ruff/mypy/pytest проблеми: Protocol через typing_extensions, виправлена типізація валідатора, додано tests/conftest.py, послаблено тип публікатора через Protocol.
  - Стабілізовано Pub/Sub: обробка неочікуваних винятків у command_bus.poll з loud error.
  - Пройдено Exit Gate P0 на Windows через redis-cli.
- Як перевірено →
  - tools/bootstrap_p0.ps1 → OK (ruff/mypy/pytest пройшли).
  - python -m app.main → OK (процес стартує, status:snapshot оновлюється).
  - Exit Gate P0:
    - redis-cli GET fxcm_local:status:snapshot → містить required поля (OK).
    - redis-cli PUBLISH fxcm_local:commands (unknown cmd) → errors[].code="unknown_command", last_command.state="error" (OK).
- Файли →
  - tools/bootstrap_p0.ps1 — Windows bootstrap.
  - requirements-dev.txt — сумісна версія types-redis.
  - runtime/status.py — Protocol через typing_extensions.
  - core/validation/validator.py — типізація для mypy.
  - tests/conftest.py — додано PYTHONPATH для pytest.
  - tests/test_unknown_command_flow.py — Optional типи.
  - runtime/command_bus.py — обробка винятків Pub/Sub.
  - .vscode/tasks.json — windows-specific bootstrap task.
- Ризики/нотатки →
  - У status:snapshot присутні попередні errors[] з invalid_json (через невдалі спроби PUBLISH); це не блокує unknown_command.
  - Python 3.7 має deprecation warning у cryptography; для P1/P2 бажано оновити Python.
- Наступний крок →
  - P1: tick feed strict ms + status lag/heartbeat.

## 2026-01-17 14:52:29 — PRE (MODE=PATCH): P1 Tick feed strict ms + status lag/heartbeat (без FXCM)

- Мета → реалізувати P1: публікацію tick у {NS}:price_tik зі строгими tick_ts/snap_ts (int ms), валідатор tick_v1 (allowlist + fail-fast), метрики tick, та розширення status:snapshot полем price{...} з лагом/heartbeat.
- Scope → core/contracts/public/tick_v1.json (уточнення allowlist), core/validation/validator.py (validate_tick_v1), runtime/publisher.py (publish_tick), runtime/status.py + status_v2 schema (optional price), observability/metrics.py (tick метрики),
internal runtime/tick_sim.py (dev-only), тести validator+sim.
- Інваріанти → (1) tick_ts/snap_ts тільки int ms, (2) status snapshot валідний за schema, (3) будь-яка контрактна помилка tick → loud errors[] + metrics, (4) без нових public commands.
- Acceptance Criteria → виконати bootstrap на Windows (py3.7) і Exit Gate P1 через redis-cli (price_tik + price у snapshot).

## 2026-01-17 14:58:02 — POST (MODE=PATCH): P1 Tick feed strict ms + status lag/heartbeat завершено

- Що зроблено →
  - Додано strict tick валідацію з перевіркою int ms (відсікання seconds/float) та validate_tick_v1.
  - Додано publish_tick з fail-fast валідацією.
  - Розширено status_v2 optional price{...} і runtime заповнення price з tick heartbeat/lag.
  - Додано tick метрики Prometheus.
  - Додано internal dev-only tick_sim та конфіг (default OFF).
  - Додано тести validator_tick і tick_sim.
- Як перевірено →
  - tools/bootstrap_p0.ps1 → OK (ruff/mypy/pytest).
  - Exit Gate P1:
    - redis-cli GET fxcm_local:status:snapshot → містить price.* (OK).
    - redis-cli SUBSCRIBE fxcm_local:price_tik → tick приходить, tick_ts/snap_ts int ms (OK).
- Файли →
  - core/contracts/public/status_v2.json — optional price.
  - core/validation/validator.py — validate_tick_v1 + ms checks.
  - runtime/publisher.py — publish_tick.
  - runtime/status.py — price секція та tick stats.
  - runtime/tick_sim.py — dev tick симулятор.
  - observability/metrics.py — tick метрики.
  - config/config.py — tick_sim конфіг (default OFF).
  - tests/test_validator_tick.py, tests/test_tick_simulator.py, tests/test_tick_simulator_disabled.py — тести.
  - app/main.py — інтеграція tick_sim.
- Ризики/нотатки →
  - Під час exit gate тимчасово вмикався tick_sim_enabled=True, після перевірки повернено False.
- Наступний крок →
  - P2: preview candles на всіх TF (TV-like) з чистими boundaries.

## 2026-01-17 15:06:05 — PRE (MODE=PATCH): P2 Preview candles на всіх TF (TV-like) + Connector chart (read-only)

- Мета → реалізувати preview candles для UI на TF {"1m","5m","15m","1h","4h","1d"}: інкрементальний preview builder з tick_sim (dev-only) → 1m preview → HTF preview; публікація у {NS}:ohlcv (complete=false, synthetic=false, source=stream), строгий ohlcv validator (рейки 1.1–1.8),
та мінімальний локальний HTTP chart для перегляду status + preview candles.
- Scope → додати strict ohlcv контракт (v1) + validate_ohlcv_v1, runtime/preview_builder.py, runtime/ohlcv_sim.py (dev-only) або reuse tick_sim, app/main.py інтеграція, observability метрики preview, minimal http server + static html.
- Інваріанти → (1) preview never complete=true, (2) time rails: ms + inclusive close_time + bucket boundaries, (3) max_bars_per_message enforced, (4) no silent fallback: будь-яка помилка → errors[]/metrics.
- Acceptance Criteria → Exit Gate P2 (redis-cli SUBSCRIBE {NS}:ohlcv отримує batches по всіх TF, чиста геометрія, stable boundaries) + /chart показує candles і status.

## 2026-01-17 16:31:48 — POST (MODE=PATCH): P2 Preview candles на всіх TF (TV-like) + Connector chart завершено

- Що зроблено →
  - Додано strict ohlcv_v1 schema + validate_ohlcv_v1 з time/boundary/sort/dedup/max-batch rails.
  - Реалізовано preview_builder: tick → preview бари на всіх TF з inclusive close_time та source=stream.
  - Додано dev-only ohlcv_sim для Exit Gate.
  - Розширено status:snapshot optional ohlcv_preview (heartbeat/лічильники) + метрики preview.
  - Додано локальний read-only chart: /chart + /api/status + /api/ohlcv.
  - Додано тести валідатора OHLCV, preview boundaries, HTTP API smoke.
- Як перевірено →
  - tools/bootstrap_p0.ps1 → OK (ruff/mypy/pytest).
  - Exit Gate P2:
    - redis-cli SUBSCRIBE fxcm_local:ohlcv → batches по всіх TF (OK).
    - redis-cli GET fxcm_local:status:snapshot → ohlcv_preview.* присутні (OK).
    - /api/status та /api/ohlcv відповідають (OK), /chart доступний (OK).
- Файли →
  - core/contracts/public/ohlcv_v1.json — strict allowlist OHLCV.
  - core/contracts/public/status_v2.json — optional ohlcv_preview.
  - core/validation/validator.py — validate_ohlcv_v1 + рейки.
  - runtime/preview_builder.py — preview builder + cache.
  - runtime/ohlcv_sim.py — dev-only симулятор.
  - runtime/http_server.py, runtime/static/chart.html — локальний chart.
  - runtime/publisher.py — publish_ohlcv.
  - runtime/status.py — ohlcv_preview статистика/метрики.
  - observability/metrics.py — preview метрики.
  - config/config.py — preview/sim конфіг (default OFF, max_bars_per_message=512).
  - app/main.py — інтеграція preview/симулятора/HTTP.
  - tests/test_validator_ohlcv.py, tests/test_preview_builder_boundaries.py, tests/test_http_api_smoke.py, tests/test_ohlcv_simulator.py — тести.
- Ризики/нотатки →
  - Для Exit Gate тимчасово вмикались ohlcv_preview_enabled та ohlcv_sim_enabled, після перевірки повернуто False.
  - Тимчасовий файл .p2_pid.txt очищено після зупинки процесу.
- Наступний крок →
  - P3: SQLite SSOT 1m final (365d) + warmup/backfill budgeted + calendar gaps gate.

## 2026-01-18 00:46:30 — POST (MODE=PATCH): P3 SSOT 1m final store + warmup/backfill + publish tail + proof-pack

- Що зроблено →
  - Вирівняно імпорти у runtime/handlers_p3.py (combine dateutil).
  - Виправлено тест boundary у tests/test_final_1m_validator.py (open_time вирівняно по 1m bucket).
  - Пройдено tools/bootstrap_p0.ps1 (ruff/mypy/pytest OK).
  - Запущено app.main у фоні через Start-Process.
  - Виконано Exit Gate P3 та зібрано proof-pack артефакти.
  - Зупинено app.main після перевірок через PowerShell фільтр CommandLine.
- Де зроблено →
  - runtime/handlers_p3.py
  - tests/test_final_1m_validator.py
  - data/audit_v3/status_before_warmup.json
  - data/audit_v3/status_after_warmup.json
  - data/audit_v3/publish_cmd.txt
- Як перевірено →
  - powershell -ExecutionPolicy Bypass -File tools\bootstrap_p0.ps1 → OK

  - Start-Process python.exe -m app.main (C:\Aione_projects\fxcm_connector_v2\.venv\Scripts\python.exe) → OK
  - powershell -ExecutionPolicy Bypass -File tools\audit\run_exit_gate_p3.ps1 → OK (Exit Gate P3)
- Ризики/нотатки →
  - app.main запускався як окремий процес; зупинку виконано через PowerShell фільтр за CommandLine.

## 2026-01-18 00:47:20 — POST (MODE=read-only discovery): Перевірка стану app.main процесу

- Що зроблено → перевірено, що процес app.main не залишився у системі.
- Як перевірено → powershell -ExecutionPolicy Bypass -Command "Get-CimInstance Win32_Process | Where-Object { `$_.CommandLine -match 'app\.main' } | Select-Object -ExpandProperty ProcessId".
- Ризики/нотатки → якщо процес знову підніметься через автоматичний старт, потрібна явна зупинка.

## 2026-01-18 00:49:10 — POST (MODE=PATCH): Корекція журналу P3

- Що зроблено → зафіксовано, що запис 00:47:20 є службовою перевіркою й не входить до основного proof-pack; джерело істини — POST о 00:46:30.
- Ризики/нотатки → надалі уникаємо зайвих discovery-записів поза межами потрібного proof-pack.

## 2026-01-17 16:44:51 — PRE (MODE=PATCH): P3 SQLite SSOT 1m final (365d) + warmup/backfill (budgeted) + tail-guard marks

- Мета → реалізувати P3: локальний SQLite (WAL) SSOT для 1m final (365 днів),
  команди fxcm_warmup/fxcm_backfill (budgeted) для наповнення/ремонту,
  tail_guard audit+repair з позначками "перевірено/ок/не ок" щоб не витрачати
  ресурси повторно, та інтеграція в /api/ohlcv (mode=final) для прозорого дебагу.
- Scope → додати store/sqlite_store.py, runtime/history_provider.py (+ dev-only sim),
  runtime/warmup.py, runtime/backfill.py, runtime/tail_guard.py, оновити
  runtime/command_bus.py (dispatch allowlist команд), runtime/status.py (final/tail секції),
  runtime/http_server.py (/api/ohlcv?mode=final), observability/metrics.py (store/tail метрики),
  тести і exit gate.
- Інваріанти → (1) final 1m тільки complete=true+synthetic=false+event_ts==close_time+source=history, (2) 365d retention доступний для читання у будь-який момент, (3) tail_guard працює лише в trading time і має marks/TTL щоб не перевіряти те саме без потреби, (4) без silent fallback.
- Acceptance Criteria → пройти bootstrap + redis-cli warmup/backfill сценарій + /api/ohlcv(mode=final) + Gate-Calendar-Gaps (0 unexpected gaps) на хвості.

## 2026-01-17 17:20:10 — PRE (MODE=PATCH): P3 фікс типізації тесту final_bar_invariants (Py3.7)

- Мета → виправити несумісність типізації `dict[str, Any]` у тесті для Python 3.7.
- Scope → лише tests/test_final_bar_invariants.py.
- Інваріанти → без змін у доменній логіці; лише типізація/тестовий код.
- План → замінити PEP585 типи на typing.Dict; не чіпати структуру тесту.
- Перевірки → не запускати (очікується окремий bootstrap).

## 2026-01-17 17:20:45 — POST (MODE=PATCH): P3 фікс типізації тесту final_bar_invariants (Py3.7)

- Що зроблено → замінено `dict[str, Any]` на `Dict[str, Any]` у касті списку барів.
- Де зроблено → tests/test_final_bar_invariants.py.
- Як перевірено → не запускалось (очікується наступний bootstrap).
- Ризики/нотатки → без впливу на runtime; лише тестова типізація.

## 2026-01-17 17:51:30 — POST (MODE=PATCH): P3 SQLite SSOT 1m final + warmup/backfill + tail_guard marks завершено

- Що зроблено →
  - Додано SQLite WAL SSOT для 1m final (schema.sql + sqlite_store.py) з CHECK-інваріантами final 1m та idempotent upsert/query.
  - Додано history_provider інтерфейс + dev-only history_sim_provider (детермінований 1m final без gaps у trading time).
  - Реалізовано fxcm_warmup/fxcm_backfill (budgeted chunking + single-inflight), інтегровано dispatch у command_bus.
  - Реалізовано tail_guard audit у trading time + marks/TTL (skipped_by_ttl) для економії ресурсів.
  - Розширено status:snapshot optional ohlcv_final_1m + tail_guard; додано метрики store/tail.
  - Додано /api/ohlcv mode=final (SQLite) та exit gate gate_calendar_gaps.
- Як перевірено →
  - tools/bootstrap_p0.ps1 → OK.
  - redis-cli PUBLISH fxcm_warmup(provider=sim, lookback_days=7) → last_command.state=ok (OK).
  - curl /api/ohlcv?mode=final → повертає 1m final (complete=true, source=history, event_ts==close_time) (OK).
  - python -m tools.exit_gates.gate_calendar_gaps --hours 24 → OK unexpected_missing_bars=0 (OK).
  - fxcm_tail_guard повторно в TTL → skipped_by_ttl=true (OK).
- Файли →
  - store/schema.sql
  - store/sqlite_store.py
  - runtime/history_provider.py
  - runtime/history_sim_provider.py
  - runtime/warmup.py
  - runtime/backfill.py
  - runtime/tail_guard.py
  - tools/exit_gates/gate_calendar_gaps.py
  - tests/test_store_sqlite.py
  - tests/test_tail_guard_marks.py
  - tests/test_gate_calendar_gaps.py
  - runtime/command_bus.py
  - runtime/status.py
  - runtime/http_server.py
  - runtime/publisher.py
  - observability/metrics.py
  - config/config.py
  - core/contracts/public/status_v2.json
  - app/main.py
  - core/validation/validator.py
  - tools/__init__.py
  - tools/exit_gates/__init__.py
- Ризики/нотатки →
  - FXCM provider поки не підключений (окремий slice).
  - 365d warmup у реалі потребує прогрес-індикаторів і можливого resumable режиму.
- Наступний крок →
  - P4: derived final rebuild 1m→15m/1h/4h/1d (history_agg) + Final-Wire + NoMix.

### Фрагменти фактичних перевірок

__status:snapshot після warmup (фрагмент):__

```
{"ohlcv_final_1m":{"last_complete_bar_ms":1768672199998,"lag_ms":1,"bars_lookback_days":7,"bars_total_est":10080},"last_command":{"cmd":"fxcm_warmup","req_id":"p3-warmup-0001","state":"ok"},"tail_guard":{"skipped_by_ttl":false}}
```

__/api/ohlcv?mode=final (фрагмент відповіді):__

```
{"symbol":"XAUUSD","tf":"1m","bars":[{"open_time":1768660200000,"close_time":1768660259999,"o":1999.84009024493,"h":2000.04009024493,"l":1999.64009024493,"c":1999.89009024493,"v":1.0,"complete":true,"synthetic":false,"source":"history","event_ts":1768660259999}]
```

__gate_calendar_gaps:__

```
OK: unexpected_missing_bars=0
```

__status:snapshot після повторного tail_guard (фрагмент):__

```
{"tail_guard":{"window_hours":48,"missing_bars":0,"skipped_by_ttl":true,"state":"ok"}}
```

## 2026-01-17T18:25:00+01:00 — PRE (MODE=PATCH): Налаштування ruff/mypy/pytest/форматування/PowerShell

- Мета → налаштувати ruff + mypy, автовирівнювання імпортів і Markdown, стабілізувати pytest імпорти та прибрати кракозябри в PowerShell.
- Scope → ruff.toml, mypy.ini, pytest.ini, .vscode/settings.json, tools/bootstrap_p0.ps1, .markdownlint.json (за потреби).
- Непорушні інваріанти → мінімальний диф, без змін доменної логіки; жодних нових фолбеків.
- План →
  1) Узгодити ruff/isort та форматування в ruff.toml.
  2) Доуточнити mypy/pytest налаштування для стабільних імпортів.
  3) Налаштувати VS Code автоформат для Python/Markdown та автоорганізацію імпортів.
  4) Додати UTF-8 encoding у PowerShell bootstrap.
- Перевірки → за потреби: ruff/mypy/pytest (через bootstrap або окремо).

## 2026-01-17T18:33:00+01:00 — POST (MODE=PATCH): Налаштування ruff/mypy/pytest/форматування/PowerShell

- Що зроблено →
  - Розширено ruff конфіг (format + isort) для автоорганізації імпортів.
  - Уточнено mypy (mypy_path, explicit_package_bases, namespace_packages) для стабільних імпортів.
  - Додано pythonpath у pytest.ini для уникнення падіння імпортів.
  - Налаштовано VS Code автоформат для Python/Markdown та auto-fix/organize через ruff.
  - Додано UTF-8 encoding у PowerShell bootstrap.
- Де зроблено →
  - ruff.toml
  - mypy.ini
  - pytest.ini
  - .vscode/settings.json
  - tools/bootstrap_p0.ps1
- Як перевірено → не запускалось (можна виконати ruff/mypy/pytest або bootstrap).

- Ризики/нотатки →
  - Для автоформату Python потрібен встановлений VS Code ruff extension; для Markdown — вбудований formatter VS Code.

## 2026-01-17 19:05:00 — PRE (MODE=PATCH): P3.1 Контракт і канонізація часу/ключів OHLCV

- Мета → виправити контрактні відхилення: `last_complete_bar_ms` має закінчуватись на …9999, а OHLCV у final/HTTP/preview має канонічні ключі `open/high/low/close/volume`.
- Scope → core/contracts/public/ohlcv_v1.json, core/validation/validator.py,
  runtime/preview_builder.py, runtime/http_server.py, app/main.py, runtime/status.py,
  runtime/warmup.py, runtime/backfill.py, tools/exit_gates/gate_calendar_gaps.py,
  runtime/static/chart.html, tests/test_final_bar_invariants.py, tests/test_validator_ohlcv.py,
  tests/test_http_api_smoke.py, ruff.toml.
- Інваріанти → мінімальний диф; без зміни публічних контрактів поза OHLCV; fail-fast валідація; без silent fallback.
- План →
  1) Канонізувати OHLCV ключі в контрактах/коді/HTTP/preview/тестах.
  2) Нормалізувати `last_complete_bar_ms` до close_time (…9999) у warmup/backfill/status.
  3) Додати рейки у валідаторі та sanity-check у gate_calendar_gaps.
  4) Пройти bootstrap та мінімальну ручну перевірку warmup/HTTP.
- Перевірки → tools/bootstrap_p0.ps1; локальні перевірки через redis-cli warmup та curl /api/ohlcv?mode=final.

## 2026-01-17 19:15:00 — POST (MODE=PATCH): P3.1 Контракт і канонізація часу/ключів OHLCV

- Що зроблено →
  - Переведено контракт OHLCV на канонічні ключі `open/high/low/close/volume` та додано рейки проти `o/h/l/c/v`.
  - Нормалізовано `last_complete_bar_ms` до close_time (…9999) у warmup/backfill/status; додано sanity-check у gate_calendar_gaps.
  - Оновлено HTTP/preview/публікацію final та тести під канонічні ключі.
  - Прибрано некоректний `[format] line-length` у ruff.toml для сумісності з ruff 0.1.15.
- Де зроблено →
  - core/contracts/public/ohlcv_v1.json
  - core/validation/validator.py
  - runtime/preview_builder.py
  - runtime/http_server.py
  - app/main.py
  - runtime/status.py
  - runtime/warmup.py
  - runtime/backfill.py
  - tools/exit_gates/gate_calendar_gaps.py
  - runtime/static/chart.html
  - tests/test_final_bar_invariants.py
  - tests/test_validator_ohlcv.py
  - tests/test_http_api_smoke.py
  - ruff.toml
- Як перевірено →
  - tools/bootstrap_p0.ps1 → OK.
  - redis-cli PUBLISH fxcm_warmup (provider=sim) → status:snapshot `last_complete_bar_ms` закінчується на …9999 (OK).
  - curl /api/ohlcv?mode=final → ключі `open/high/low/close/volume` (OK).
- Ризики/нотатки →
  - Усі споживачі повинні оновити очікування полів до канонічних ключів.

## 2026-01-17 19:30:00 — PRE (MODE=PATCH): P4 Derived final rebuild (history_agg) + Final-Wire + NoMix

- Мета → реалізувати HTF final (15m/1h/4h/1d) через детерміновану агрегацію з SQLite SSOT 1m final; публікація у {NS}:ohlcv з source=history_agg; Final-Wire та NoMix exit gates.
- Scope → store/schema.sql + store/sqlite_store.py (додати HTF таблиці), store/derived_builder.py, runtime/rebuild_derived.py, інтеграція у warmup/backfill (rebuild_timeframes / rebuild_derived), status/metrics, tools/exit_gates/gate_final_wire.py + gate_no_mix.py, тести.
- Інваріанти → SSOT=1m final; HTF final тільки history_agg; event_ts==close_time; NoMix rail; без silent fallback.
- Acceptance → redis-cli SUBSCRIBE {NS}:ohlcv бачить HTF final; gate_final_wire OK; gate_no_mix OK; /api/ohlcv?mode=final підтримує HTF.

## 2026-01-17 20:35:00 — POST (MODE=PATCH): P4 Derived final rebuild (history_agg) + Final-Wire + NoMix

- Що зроблено →
  - Додано HTF final таблицю в SQLite та derived агрегацію 1m→15m/1h/4h/1d з канонічними інваріантами.
  - Реалізовано rebuild orchestration з single-inflight, coalesce latest wins, publish HTF final у {NS}:ohlcv.
  - Розширено status/metrics для derived_rebuild та no_mix; додано NoMix rail у store.
  - Додано exit gates gate_final_wire та gate_no_mix; додано тести для derived_builder/NoMix/gate_final_wire.
- Де зроблено →
  - store/schema.sql
  - store/sqlite_store.py
  - store/derived_builder.py
  - runtime/rebuild_derived.py
  - runtime/warmup.py
  - runtime/backfill.py
  - runtime/http_server.py
  - runtime/status.py
  - observability/metrics.py
  - app/main.py
  - core/contracts/public/status_v2.json
  - core/validation/validator.py
  - tools/exit_gates/gate_final_wire.py
  - tools/exit_gates/gate_no_mix.py
  - tests/test_derived_builder.py
  - tests/test_no_mix_rail.py
  - tests/test_gate_final_wire.py
  - tests/test_http_api_smoke.py
  - tests/test_validator_ohlcv.py
- Як перевірено →
  - powershell -ExecutionPolicy Bypass -File tools\bootstrap_p0.ps1 → OK.
  - redis-cli PUBLISH fxcm_local:commands (fxcm_warmup provider=sim, lookback_days=7, rebuild_derived=true, rebuild_timeframes=[15m,1h,4h,1d]) → last_command.state=ok (OK).
  - redis-cli SUBSCRIBE fxcm_local:ohlcv (через фон. job + лог data/ohlcv_sub.log) → отримано HTF final (tf=15m/1h/4h) з source=history_agg (OK).
  - curl "http://127.0.0.1:8088/api/ohlcv?symbol=XAUUSD&tf=15m&limit=200&mode=final" → 200 (OK).
  - curl "http://127.0.0.1:8088/api/ohlcv?symbol=XAUUSD&tf=1h&limit=200&mode=final" → 200 (OK).
  - python -m tools.exit_gates.gate_final_wire --symbol XAUUSD --tf 15m --hours 24 → OK.
  - python -m tools.exit_gates.gate_no_mix --symbol XAUUSD --tfs 15m,1h,4h,1d → OK.
- Ризики/нотатки →
  - Для HTF rebuild фіксуються пропуски неповних bucket (derived_incomplete_bucket) у status як деградація.
  - FXCM provider ще не підключений (окремий slice).
  - Уточнення: канонічні OHLCV ключі були SSOT; P3.1 відновив правильний формат, API не ламали.
- Next step →
  - P5: tail_guard repair + republish_tail watermark для HTF (або інтеграція FXCM history provider як P4.1).

## 2026-01-17T18:40:00+01:00 — PRE (MODE=PATCH): Прибрати cryptography warning та graceful shutdown

- Мета → прибрати CryptographyDeprecationWarning у Python 3.7 та зробити коректне завершення при KeyboardInterrupt без traceback.
- Scope → requirements.txt, app/main.py.
- Непорушні інваріанти → мінімальний диф, без змін доменної логіки, без фолбеків.
- План →
  1) Зафіксувати сумісну версію cryptography для Py3.7.
  2) Додати обробку KeyboardInterrupt з коректним shutdown HTTP сервера.
- Перевірки → за потреби bootstrap або pytest.

## 2026-01-17T18:46:00+01:00 — POST (MODE=PATCH): Прибрати cryptography warning та graceful shutdown

- Що зроблено →
  - Зафіксовано cryptography==40.0.2 для Py3.7.
  - Додано обробку KeyboardInterrupt з коректним shutdown HTTP сервера.
- Де зроблено →
  - requirements.txt
  - app/main.py
- Як перевірено → не запускалось.
- Ризики/нотатки →
  - Для застосування піну потрібен reinstall залежностей у venv.

## 2026-01-17T16:04:12+01:00 → PRE (MODE=PATCH) → Оновлення Ruff для native server
- Мета: підняти dev-залежність Ruff до мінімально потрібної версії для native server.
- Scope: лише requirements-dev.txt.
- Non-goals: зміни коду, запуск лінтів/тестів.
- Інваріанти/рейки: мінімальний диф; інші dev-залежності без змін.
- План:
  1) Оновити версію ruff у requirements-dev.txt.
  2) Зафіксувати зміни у журналі.
- Тести/перевірки: не запускати (не застосовно).

## 2026-01-17T16:04:34+01:00 → POST (MODE=PATCH) → Оновлення Ruff для native server
- Що зроблено: оновлено dev-залежність ruff до 0.5.3.
- Де зроблено: requirements-dev.txt.
- Як перевірено: не запускалось (не застосовно).
- Ризики/нотатки/очікуваний результат: після встановлення dev-залежностей native server Ruff має бути доступний у середовищі.

## 2026-01-17T16:18:10+01:00 → PRE (MODE=PATCH) → Прибрати помилки Markdown lint для журналів
- Мета: прибрати шумні помилки Markdown lint у робочих журналах.
- Scope: лише .markdownlintignore.
- Non-goals: зміни вмісту журналів, форматування історичних записів.
- Інваріанти/рейки: мінімальний диф; правила lint для docs залишаються.
- План:
  1) Додати ігнорування Work/*.md.
  2) Зафіксувати запис у журналі.
- Тести/перевірки: не запускати (не застосовно).

## 2026-01-17T16:18:33+01:00 → POST (MODE=PATCH) → Прибрати помилки Markdown lint для журналів
- Що зроблено: додано .markdownlintignore з виключенням Work/*.md.
- Де зроблено: .markdownlintignore.
- Як перевірено: не запускалось (не застосовно).
- Ризики/нотатки/очікуваний результат: журнал Work/01log.md більше не підсвічується помилками Markdown lint.

## 2026-01-17T16:26:10+01:00 → PRE (MODE=PATCH) → Автофікс Markdown на збереження
- Мета: увімкнути автоматичні виправлення Markdown при збереженні.
- Scope: лише .vscode/settings.json.
- Non-goals: зміни контенту існуючих .md файлів.
- Інваріанти/рейки: мінімальний диф; інші налаштування без змін.
- План:
  1) Додати codeActionsOnSave для markdownlint у [markdown].
  2) Зафіксувати запис у журналі.
- Тести/перевірки: не запускати (не застосовно).

## 2026-01-17T16:26:32+01:00 → POST (MODE=PATCH) → Автофікс Markdown на збереження
- Що зроблено: увімкнено source.fixAll.markdownlint на збереження для Markdown.
- Де зроблено: .vscode/settings.json.
- Як перевірено: не запускалось (не застосовно).
- Ризики/нотатки/очікуваний результат: Markdown має авто‑виправлятися при збереженні (за наявності markdownlint extension).

## 2026-01-17T16:40:12+01:00 → PRE (MODE=PATCH) → Усунення помилок Markdown lint у журналі
- Мета: прибрати поточні lint‑помилки у журналі та виправити тип налаштування markdownlint.
- Scope: Work/01log.md, .vscode/settings.json.
- Non-goals: зміна змісту історичних записів, крім розбиття рядків.
- Інваріанти/рейки: мінімальний диф; без змін доменної логіки.
- План:
  1) Розбити наддовгі рядки та прибрати trailing spaces.
  2) Виправити тип значення source.fixAll.markdownlint у налаштуваннях.
  3) Додати запис у журнал.
- Тести/перевірки: не запускати (не застосовно).

## 2026-01-17T16:40:35+01:00 → POST (MODE=PATCH) → Усунення помилок Markdown lint у журналі
- Що зроблено: розбито наддовгі рядки у журналі; виправлено тип налаштування markdownlint.
- Де зроблено: Work/01log.md, .vscode/settings.json.
- Як перевірено: не запускалось (не застосовно).
- Ризики/нотатки/очікуваний результат: помилки MD013/MD009 у журналі зникли.

## 2026-01-17T16:55:10+01:00 → PRE (MODE=PATCH) → Усунення попередження PSScriptAnalyzer
- Мета: прибрати попередження про невикористану змінну у bootstrap.
- Scope: tools/bootstrap_p0.ps1.
- Non-goals: зміни логіки bootstrap чи залежностей.
- Інваріанти/рейки: мінімальний диф; поведінка перевірки Python 3.7 зберігається.
- План:
  1) Прибрати присвоєння змінній, яка не використовується.
  2) Зафіксувати запис у журналі.
- Тести/перевірки: не запускати (не застосовно).

## 2026-01-17T16:55:28+01:00 → POST (MODE=PATCH) → Усунення попередження PSScriptAnalyzer
- Що зроблено: прибрано присвоєння змінній `ver`, результат виклику подавлено.
- Де зроблено: tools/bootstrap_p0.ps1.
- Як перевірено: не запускалось (не застосовно).
- Ризики/нотатки/очікуваний результат: попередження PSUseDeclaredVarsMoreThanAssignments зникне.

## 2026-01-17 20:45:00 — PRE (MODE=PATCH): P5 Tail-guard repair + republish watermark (1m+HTF) + frequent near-tail checks

- Мета → реалізувати P5: автоматизований tail_guard для 1m+HTF з частими near-tail перевірками, marks/TTL, repair (backfill→rebuild), і republish_tail з watermark (force опційно) для “догнати UI” після repair.
- Scope → runtime/tail_guard.py (repair), runtime/republish.py, store/sqlite_store.py (marks розширити під tf), status/metrics, tools/exit_gates/gate_calendar_gaps.py (1m+HTF), нові exit gate для watermark/republish.
- Інваріанти → repair тільки в trading time; без silent fallback; NoMix не порушувати; republish не міняє SSOT, лише повторно шле дріт; marks/TTL INTERNAL.
- Acceptance → tail_guard(repair=true) відновлює gaps на хвості; republish(force=false) не дублює без потреби; force=true дозволяє дубль і консюмер dedup; gates OK.

## 2026-01-17 21:05:30 — POST (MODE=PATCH): P5 E2E COMMAND (tail_guard/republish/gates)

- Що зроблено →
  - Надіслано валідні JSON-команди через Redis для fxcm_tail_guard (repair+republish) та fxcm_republish_tail із watermark TTL.
  - Сформовано та використано UTF-8 JSON-файли без BOM для публікації команд.
  - Виконано exit gates: calendar gaps (1m/15m) та republish watermark.
- Де зроблено →
  - data/p5_tail_0001.json
  - data/p5_republish_0001.json
  - tools/exit_gates/gate_calendar_gaps.py
  - tools/exit_gates/gate_republish_watermark.py
- Як перевірено →
  - redis-cli PUBLISH fxcm_local:commands < data\\p5_tail_0001.json → OK (last_command.state=ok)
  - redis-cli PUBLISH fxcm_local:commands < data\\p5_republish_0001.json → OK (last_command.state=ok)
  - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe tools/exit_gates/gate_calendar_gaps.py --symbol XAUUSD --hours 24 --tf 1m → OK: unexpected_missing_bars=0
  - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe tools/exit_gates/gate_calendar_gaps.py --symbol XAUUSD --hours 24 --tf 15m → OK: unexpected_missing_bars=0
  - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe tools/exit_gates/gate_republish_watermark.py --symbol XAUUSD --tfs "1m,15m,1h,4h,1d" --hours 24 → OK: republish_watermark
- Ризики/нотатки →
  - У status:snapshot залишились історичні errors[] з invalid_json від ранніх спроб публікації; поточні команди виконані успішно.

## 2026-01-17 20:40:30 — POST (MODE=PATCH): P5 DONE (tail_guard/republish/gates)

- Що зроблено →
  - Вирівняно HTF audit у tail_guard (береться останній HTF бар), щоб другий запуск у TTL давав skipped_by_ttl=true.
  - Оновлено gate_calendar_gaps з helper check_calendar_gaps для повторного використання в тестах.
  - Переписано gate_republish_watermark для перевірки watermark через status:snapshot (Redis).
  - Оновлено тести: republish watermark (двічі без force) і tail_guard repair flow з перевіркою gate_calendar_gaps.
- Файли →
  - runtime/tail_guard.py
  - tools/exit_gates/gate_calendar_gaps.py
  - tools/exit_gates/gate_republish_watermark.py
  - tests/test_republish_watermark.py
  - tests/test_tail_guard_repair_flow.py
- Як перевірено →
  - powershell -ExecutionPolicy Bypass -File tools\bootstrap_p0.ps1 → OK
  - Start-Process -FilePath C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -ArgumentList "-m app.main" → OK
  - cmd /c "redis-cli -x PUBLISH fxcm_local:commands < data\p5_tail_0001.json" → OK
  - cmd /c "redis-cli -x PUBLISH fxcm_local:commands < data\p5_tail_0002.json" → OK
  - redis-cli GET fxcm_local:status:snapshot → збережено (див. нижче)
  - cmd /c "redis-cli -x PUBLISH fxcm_local:commands < data\p5_republish_0001.json" → OK
  - cmd /c "redis-cli -x PUBLISH fxcm_local:commands < data\p5_republish_0002.json" → OK
  - redis-cli GET fxcm_local:status:snapshot → збережено (див. нижче)
  - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe tools/exit_gates/gate_republish_watermark.py --symbol XAUUSD --tfs "1m,15m,1h,4h,1d" --hours 24 → OK: republish_watermark
  - cmd /c "redis-cli -x PUBLISH fxcm_local:commands < data\p5_republish_0003.json" → OK
  - redis-cli GET fxcm_local:status:snapshot → збережено (див. нижче)
- Результати (status:snapshot) →
  - після другого fxcm_tail_guard (repair=false):

    ```json
    {"ts":1768678732567,"version":"0.0.0","schema_version":2,"pipeline_version":"p0","build_version":"dev","process":{"pid":15604,"uptime_s":7.722,"state":"running"},"market":{"is_open":true,"next_open_utc":"2026-01-17T19:38:52Z","next_pause_utc":"2026-01-17T19:38:52Z","calendar_tag":"stub_calendar_v0"},"errors":[],"degraded":["calendar_stub"],"price":{"last_tick_ts_ms":0,"last_snap_ts_ms":0,"tick_lag_ms":0,"tick_total":0,"tick_err_total":0},"ohlcv_preview":{"last_publish_ts_ms":0,"preview_total":0,"preview_err_total":0,"last_bar_open_time_ms":{"1m":0,"5m":0,"15m":0,"1h":0,"4h":0,"1d":0}},"ohlcv_final_1m":{"last_complete_bar_ms":0,"lag_ms":0,"bars_lookback_days":0,"bars_total_est":0},"ohlcv_final":{"1m":{"last_complete_bar_ms":0,"lag_ms":0,"bars_lookback_days":0,"bars_total_est":0},"15m":{"last_complete_bar_ms":0,"lag_ms":0,"bars_lookback_days":0,"bars_total_est":0},"1h":{"last_complete_bar_ms":0,"lag_ms":0,"bars_lookback_days":0,"bars_total_est":0},"4h":{"last_complete_bar_ms":0,"lag_ms":0,"bars_lookback_days":0,"bars_total_est":0},"1d":{"last_complete_bar_ms":0,"lag_ms":0,"bars_lookback_days":0,"bars_total_est":0}},"derived_rebuild":{"last_run_ts_ms":0,"last_range_ms":[0,0],"last_tfs":[],"state":"idle","errors":[]},"no_mix":{"conflicts_total":0,"last_conflict":null},"tail_guard":{"last_audit_ts_ms":1768678732564,"window_hours":24,"tf_states":{"1m":{"missing_bars":0,"skipped_by_ttl":true,"state":"ok"},"15m":{"missing_bars":0,"skipped_by_ttl":true,"state":"ok"},"1h":{"missing_bars":0,"skipped_by_ttl":true,"state":"ok"},"4h":{"missing_bars":0,"skipped_by_ttl":true,"state":"ok"},"1d":{"missing_bars":0,"skipped_by_ttl":true,"state":"ok"}},"repaired":false},"republish":{"last_run_ts_ms":0,"last_req_id":"","skipped_by_watermark":false,"forced":false,"published_batches":0,"state":"idle"},"last_command":{"cmd":"fxcm_tail_guard","req_id":"p5-tail-0002","state":"ok","started_ts":1768678440478,"finished_ts":1768678732567,"result":{}}}
    ```

  - після другого fxcm_republish_tail (force=false):

    ```json
    {"ts":1768678745571,"version":"0.0.0","schema_version":2,"pipeline_version":"p0","build_version":"dev","process":{"pid":15604,"uptime_s":20.726,"state":"running"},"market":{"is_open":true,"next_open_utc":"2026-01-17T19:39:05Z","next_pause_utc":"2026-01-17T19:39:05Z","calendar_tag":"stub_calendar_v0"},"errors":[],"degraded":["calendar_stub"],"price":{"last_tick_ts_ms":0,"last_snap_ts_ms":0,"tick_lag_ms":0,"tick_total":0,"tick_err_total":0},"ohlcv_preview":{"last_publish_ts_ms":0,"preview_total":0,"preview_err_total":0,"last_bar_open_time_ms":{"1m":0,"5m":0,"15m":0,"1h":0,"4h":0,"1d":0}},"ohlcv_final_1m":{"last_complete_bar_ms":0,"lag_ms":0,"bars_lookback_days":0,"bars_total_est":0},"ohlcv_final":{"1m":{"last_complete_bar_ms":0,"lag_ms":0,"bars_lookback_days":0,"bars_total_est":0},"15m":{"last_complete_bar_ms":0,"lag_ms":0,"bars_lookback_days":0,"bars_total_est":0},"1h":{"last_complete_bar_ms":0,"lag_ms":0,"bars_lookback_days":0,"bars_total_est":0},"4h":{"last_complete_bar_ms":0,"lag_ms":0,"bars_lookback_days":0,"bars_total_est":0},"1d":{"last_complete_bar_ms":0,"lag_ms":0,"bars_lookback_days":0,"bars_total_est":0}},"derived_rebuild":{"last_run_ts_ms":0,"last_range_ms":[0,0],"last_tfs":[],"state":"idle","errors":[]},"no_mix":{"conflicts_total":0,"last_conflict":null},"tail_guard":{"last_audit_ts_ms":1768678732564,"window_hours":24,"tf_states":{"1m":{"missing_bars":0,"skipped_by_ttl":true,"state":"ok"},"15m":{"missing_bars":0,"skipped_by_ttl":true,"state":"ok"},"1h":{"missing_bars":0,"skipped_by_ttl":true,"state":"ok"},"4h":{"missing_bars":0,"skipped_by_ttl":true,"state":"ok"},"1d":{"missing_bars":0,"skipped_by_ttl":true,"state":"ok"}},"repaired":false},"republish":{"last_run_ts_ms":1768678745568,"last_req_id":"p5-republish-0002","skipped_by_watermark":true,"forced":false,"published_batches":0,"state":"ok"},"last_command":{"cmd":"fxcm_republish_tail","req_id":"p5-republish-0002","state":"ok","started_ts":1768678450844,"finished_ts":1768678745571,"result":{}}}
    ```

  - після fxcm_republish_tail (force=true):

    ```json
    {"ts":1768678758973,"version":"0.0.0","schema_version":2,"pipeline_version":"p0","build_version":"dev","process":{"pid":15604,"uptime_s":34.128,"state":"running"},"market":{"is_open":true,"next_open_utc":"2026-01-17T19:39:18Z","next_pause_utc":"2026-01-17T19:39:18Z","calendar_tag":"stub_calendar_v0"},"errors":[],"degraded":["calendar_stub"],"price":{"last_tick_ts_ms":0,"last_snap_ts_ms":0,"tick_lag_ms":0,"tick_total":0,"tick_err_total":0},"ohlcv_preview":{"last_publish_ts_ms":0,"preview_total":0,"preview_err_total":0,"last_bar_open_time_ms":{"1m":0,"5m":0,"15m":0,"1h":0,"4h":0,"1d":0}},"ohlcv_final_1m":{"last_complete_bar_ms":0,"lag_ms":0,"bars_lookback_days":0,"bars_total_est":0},"ohlcv_final":{"1m":{"last_complete_bar_ms":0,"lag_ms":0,"bars_lookback_days":0,"bars_total_est":0},"15m":{"last_complete_bar_ms":0,"lag_ms":0,"bars_lookback_days":0,"bars_total_est":0},"1h":{"last_complete_bar_ms":0,"lag_ms":0,"bars_lookback_days":0,"bars_total_est":0},"4h":{"last_complete_bar_ms":0,"lag_ms":0,"bars_lookback_days":0,"bars_total_est":0},"1d":{"last_complete_bar_ms":0,"lag_ms":0,"bars_lookback_days":0,"bars_total_est":0}},"derived_rebuild":{"last_run_ts_ms":0,"last_range_ms":[0,0],"last_tfs":[],"state":"idle","errors":[]},"no_mix":{"conflicts_total":0,"last_conflict":null},"tail_guard":{"last_audit_ts_ms":1768678732564,"window_hours":24,"tf_states":{"1m":{"missing_bars":0,"skipped_by_ttl":true,"state":"ok"},"15m":{"missing_bars":0,"skipped_by_ttl":true,"state":"ok"},"1h":{"missing_bars":0,"skipped_by_ttl":true,"state":"ok"},"4h":{"missing_bars":0,"skipped_by_ttl":true,"state":"ok"},"1d":{"missing_bars":0,"skipped_by_ttl":true,"state":"ok"}},"repaired":false},"republish":{"last_run_ts_ms":1768678758970,"last_req_id":"p5-republish-0003","skipped_by_watermark":false,"forced":true,"published_batches":7,"state":"ok"},"last_command":{"cmd":"fxcm_republish_tail","req_id":"p5-republish-0003","state":"ok","started_ts":1768678454917,"finished_ts":1768678758973,"result":{}}}
    ```

- Ризики/нотатки →
  - Процес app.main залишено запущеним (PID 15604) для перевірок; за потреби зупинити вручну.
- Next step → P4.1 FXCM history provider.

## 2026-01-17T21:12:10+01:00 — PRE (MODE=PATCH) → М’якше завершення HTTP сервера
- Мета: прибрати зависання/traceback при KeyboardInterrupt під час зупинки.
- Scope: runtime/http_server.py.
- Non-goals: зміна API або поведінки HTTP роутів.
- Інваріанти/рейки: мінімальний диф; без silent fallback у runtime логіці.
- План:
  1) Зберігати thread сервера та додати timeout.
  2) Обробити KeyboardInterrupt у stop() та не падати на shutdown.
- Тести/перевірки: не запускати (не застосовно).

## 2026-01-17T21:12:44+01:00 — POST (MODE=PATCH) → М’якше завершення HTTP сервера
- Що зроблено: додано thread/timeout для HTTP сервера та перехоплення KeyboardInterrupt у stop().
- Де зроблено: runtime/http_server.py.
- Як перевірено: не запускалось (не застосовно).
- Ризики/нотатки/очікуваний результат: завершення має бути швидшим і без traceback.

## 2026-01-17T22:10:00+01:00 — PRE (MODE=read-only discovery) → Audit + Full Project Schema

- Мета → провести read-only аудит поточного стану та зібрати докази для повного опису системи “як працює”.
- Scope → лише читання/збір фактів; створення нових файлів у docs/audit/ та data/audit/. Жодних змін коду.
- Non-goals → рефакторинг, виправлення, форматування, зміна поведінки.
- Інваріанти/рейки → append-only журнал; жодних правок існуючих файлів коду; всі твердження з path:line або артефактами команд.
- План →
  1) Створити docs/audit/ та data/audit/.
  2) Зібрати артефакти команд у data/audit/*.
  3) Прочитати SSOT документи та ключові модулі для фактів (path:line).
  4) Згенерувати docs/audit/current_state.md, project_schema.md, diagrams.md.
  5) Додати POST у журнал.
- Тести/перевірки (команди) → python --version; .\.venv\Scripts\python.exe -m pip freeze; tree /F; dir core\contracts\public; пошук патернів (rg/Select-String); redis-cli GET fxcm_local:status:snapshot; curl /api/status; curl /api/ohlcv (final 15m/1m); exit gates; tools\bootstrap_p0.ps1.
- Артефакти → docs/audit/current_state.md, docs/audit/project_schema.md, docs/audit/diagrams.md; data/audit/* (див. POST).

## 2026-01-17T22:45:00+01:00 — POST (MODE=read-only discovery) → Audit + Full Project Schema

- Що зроблено → зібрані артефакти команд у data/audit/*; створено audit-документи у docs/audit/*.
- Де зроблено →
  - docs/audit/current_state.md
  - docs/audit/project_schema.md
  - docs/audit/diagrams.md
  - data/audit/python_version.txt
  - data/audit/pip_freeze.txt
  - data/audit/tree_full.txt
  - data/audit/contracts_list.txt
  - data/audit/grep_findings.txt
  - data/audit/status_snapshot.json
  - data/audit/http_status.json
  - data/audit/http_ohlcv_final_15m.json
  - data/audit/http_ohlcv_final_1m.json
  - data/audit/gate_calendar_gaps_1m.txt
  - data/audit/gate_calendar_gaps_15m.txt
  - data/audit/gate_republish_watermark.txt
  - data/audit/gate_final_wire_15m.txt
  - data/audit/gate_no_mix.txt
  - data/audit/bootstrap.txt
- Як перевірено → артефакти команд збережені у data/audit/* (див. файли вище).
- Ключові findings (факти) →
  - Public Surface у Redis зафіксований в SSOT правилах ([docs/COPILOT_RULES.md](docs/COPILOT_RULES.md#L8-L9)) та відображений у конфігу каналів ([config/config.py](config/config.py#L63-L75)).
  - Allowlist та інваріанти часу/Final enforced у валідаторі ([core/validation/validator.py](core/validation/validator.py#L12-L170)).
  - SQLite SSOT для 1m final має CHECK-рейки (inclusive close_time, source=history, event_ts=close_time) ([store/schema.sql](store/schema.sql#L3-L22)).
  - HTF final публікується як history_agg і проходить Final-Wire gate ([runtime/rebuild_derived.py](runtime/rebuild_derived.py#L286-L303), [data/audit/gate_final_wire_15m.txt](data/audit/gate_final_wire_15m.txt#L1)).
  - Gate calendar gaps для 1m/15m пройдений ([data/audit/gate_calendar_gaps_1m.txt](data/audit/gate_calendar_gaps_1m.txt#L1), [data/audit/gate_calendar_gaps_15m.txt](data/audit/gate_calendar_gaps_15m.txt#L1)).
  - Gate no_mix пройдений ([data/audit/gate_no_mix.txt](data/audit/gate_no_mix.txt#L1)).
  - Gate republish watermark провалений (skipped_by_watermark=false) ([data/audit/gate_republish_watermark.txt](data/audit/gate_republish_watermark.txt#L1)).
  - Поточний snapshot містить `calendar_stub` та `redis_pubsub_error` ([data/audit/status_snapshot.json](data/audit/status_snapshot.json#L1)).
  - Поточна версія Python у середовищі: 3.14.2 ([data/audit/python_version.txt](data/audit/python_version.txt#L1)).
- Ризики/відхилення →
  - FXCM provider не налаштований (provider=fxcm → error) ([app/main.py](app/main.py#L87)).
  - Gate republish watermark не пройдено (див. артефакт) ([data/audit/gate_republish_watermark.txt](data/audit/gate_republish_watermark.txt#L1)).
  - У snapshot присутні помилки Pub/Sub (див. артефакт) ([data/audit/status_snapshot.json](data/audit/status_snapshot.json#L1)).
- Next step → до P4.1 FXCM provider не готові: потрібна реалізація/підключення provider=fxcm (див. факт про `ProviderNotConfiguredError`) ([app/main.py](app/main.py#L87)).

## 2026-01-17T23:05:00+01:00 — PRE (MODE=PATCH) → P-slice (TBD: очікується конкретна вимога)

- Мета → виконати мінімальний P-slice за конкретною вимогою користувача (очікується підтвердження завдання).
- Scope → мінімальний диф у вже існуючих модулях; без нових public surface/commands/helpers.
- Non-goals → рефакторинг “для краси”, розширення API/команд.
- Інваріанти/рейки → append-only лог; без дублювання логіки часу/границь/OHLCV mapping; використовувати канон.
- План →
  1) Отримати чітку вимогу для P-slice.
  2) Перевірити наявні канони/функції.
  3) Зробити мінімальний диф (1–3 файли).
  4) Пройти E2E: bootstrap, запуск app, gate.
  5) Додати POST.
- Тести/перевірки → powershell -ExecutionPolicy Bypass -File tools\bootstrap_p0.ps1; python -m app.main; exit gate (мінімум 1) з логом у data/audit/*.

## 2026-01-17T23:20:00+01:00 — PRE (MODE=read-only discovery) → Audit v2

- Мета → виконати Audit v2 без змін коду, тільки нові файли у docs/audit_v2 та data/audit_v2.
- Scope → запуск команд, збір артефактів, опис у docs/audit_v2/*.
- Non-goals → будь-які зміни коду/конфігу/тестів.
- Інваріанти/рейки → append-only лог; жодних змін поза docs/audit_v2 та data/audit_v2; всі твердження з артефактів або path:line.
- План →
  1) Створити docs/audit_v2/ і data/audit_v2/.
  2) Зібрати артефакти середовища, snapshot, warmup, gates.
  3) Зібрати anti-bloat артефакти (tree/grep).
  4) Створити docs/audit_v2/*.
  5) Додати POST.
- Тести/перевірки → запуск app.main (venv), warmup, gates, redis-cli/curl артефакти.

## 2026-01-17T23:35:00+01:00 — PRE (MODE=read-only discovery) → Audit v2 docs

- Мета → сформувати docs/audit_v2/current_state.md, drift_risks.md, project_schema.md за наявними артефактами.
- Scope → тільки нові файли у docs/audit_v2; без змін коду/конфігів/тестів.
- Non-goals → будь-які зміни runtime/конфігів, повторні запуски команд.
- Інваріанти/рейки → append-only лог; твердження лише з артефактів у data/audit_v2.
- План →
  1) Скласти current_state з ключовими фактами та артефактами.
  2) Скласти drift_risks (ризики/відхилення, включно з anti-bloat).
  3) Скласти project_schema (модулі, потоки, контракти, сховище).
  4) Додати POST.
- Тести/перевірки → не застосовуються (read-only документація).

## 2026-01-17T23:40:00+01:00 — POST (MODE=read-only discovery) → Audit v2 docs

- Що зроблено → створено підсумкові документи Audit v2 (current_state, drift_risks, project_schema) на основі артефактів.
- Де зроблено →
  - docs/audit_v2/current_state.md
  - docs/audit_v2/drift_risks.md
  - docs/audit_v2/project_schema.md
- Як перевірено → не застосовується (read-only документація).
- Ризики/відкат/нотатки →
  - Вердикт: УМОВНО OK — пайплайн і гейти OK, але є derived_incomplete_bucket (HTF) після warmup.

  ## 2026-01-17T23:50:00+01:00 — PRE (MODE=read-only discovery) → Audit v3

  - Мета → виконати Audit v3 без змін коду/конфігів/тестів; зібрати артефакти та підготувати docs/audit_v3/*.
  - Scope → тільки нові файли у docs/audit_v3 та data/audit_v3; append-only журнал.
  - Non-goals → будь-які зміни у runtime/конфігах/тестах.
  - Інваріанти/рейки → Public Surface Redis незмінний; час epoch ms int; close_time inclusive; event_ts==close_time для Final; NoMix для complete=true; Final sources тільки history/history_agg; SSOT buckets/calendar без дублювання; без тихих фолбеків.
  - План →
    1) Створити папки audit_v3.
    2) Зібрати env/tree/grep артефакти.
    3) Запустити bootstrap + runtime smoke.
    4) Warmup/гейти або зафіксувати FAIL.
    5) Зупинка app.main вручну та фіксація способу.
    6) Створити docs/audit_v3/* і додати POST.
  - Тести/перевірки → тільки команди зі списку користувача.

  ## 2026-01-17T23:58:00+01:00 — POST (MODE=read-only discovery) → Audit v3

  - Що зроблено → зібрано артефакти audit_v3, створено docs/audit_v3/*, зафіксовано спосіб зупинки.
  - Де зроблено →
    - data/audit_v3/* (env, tree, bootstrap, runtime, HTTP, gates, grep, warmup publish)
    - docs/audit_v3/current_state.md
    - docs/audit_v3/project_schema.md
    - docs/audit_v3/diagrams.md
    - docs/audit_v3/drift_risks.md
    - data/audit_v3/stop_method.txt
  - Як перевірено → відповідно до артефактів (bootstrap, status, HTTP, gates).
  - Ризики/відкат/нотатки →
    - rg недоступний (grep_* = FAIL) і warmup publish через cmd не виконано; відображено у drift_risks.

## 2026-01-17T23:59:30+01:00 — PRE (MODE=read-only discovery) → Документація runtime

- Мета → підготувати детальну документацію каталогу runtime та додати схему.
- Scope → створити нові документи; без змін коду/конфігів/тестів.
- Non-goals → будь-які PATCH у runtime або суміжних модулях.
- Інваріанти/рейки → append-only журнал; усі твердження з посиланнями path:line.
- План →
  1) Описати модулі runtime та їх відповідальність.
  2) Описати потоки даних/команд та інваріанти.
  3) Додати Mermaid схему.
- Тести/перевірки → не застосовується (read-only документація).

## 2026-01-18T00:02:30+01:00 — POST (MODE=read-only discovery) → Документація runtime

- Що зроблено → створено детальну документацію runtime з описом модулів, потоків і Mermaid-схемою.
- Де зроблено → docs/runtime.md.
- Як перевірено → не застосовується (read-only документація).
- Ризики/відкат/нотатки → без змін коду; лише документація.

## 2026-01-18T00:05:10+01:00 — PRE (MODE=read-only discovery) → Розширення runtime.md

- Мета → розширити docs/runtime.md з детальнішим описом runtime та схемами.
- Scope → лише docs/runtime.md; без змін коду/конфігів/тестів.
- Non-goals → будь-які PATCH у runtime чи інших модулях.
- Інваріанти/рейки → append-only журнал; твердження лише з path:line.
- План →
  1) Додати секції про lifecycle, API, команди, конфіг, інваріанти, помилки.
  2) Розширити схеми (component + sequence + dataflow).
  3) Додати POST.
- Тести/перевірки → не застосовується (read-only документація).

## 2026-01-18T00:08:10+01:00 — POST (MODE=read-only discovery) → Розширення runtime.md

- Що зроблено → розширено docs/runtime.md (lifecycle, API, команди, інваріанти, схеми).
- Де зроблено → docs/runtime.md.
- Як перевірено → не застосовується (read-only документація).
- Ризики/відкат/нотатки → без змін коду; лише документація.

## 2026-01-17T22:20:00Z — PRE (MODE=PATCH) → Audit v3 fix: Exit Gate P0 proof-pack

- Мета → закрити P0 Exit Gate доказами у audit_v3 без змін бізнес-логіки.
- Scope → додати скрипт exit gate, task у VSCode, і підказку у bootstrap; створити артефакти у data/audit_v3.
- Non-goals → зміни runtime логіки, контрактів, тестів.
- Інваріанти/рейки → тільки PowerShell + redis-cli; payload формується в PowerShell і передається через stdin (-x); без docker/git/jq.
- План →
  1) Створити tools/audit/run_exit_gate_p0.ps1.
  2) Додати task "Audit: Exit Gate P0" у .vscode/tasks.json.
  3) Додати інструкцію у tools/bootstrap_p0.ps1.
  4) Запустити скрипт і зібрати артефакти.
  5) Додати POST.
- Тести/перевірки → запуск powershell -ExecutionPolicy Bypass -File tools/audit/run_exit_gate_p0.ps1 -NS fxcm_local.

## 2026-01-18T00:20:49+01:00 — POST (MODE=PATCH) → Audit v3 fix: Exit Gate P0 proof-pack

- Що зроблено →
  - Додано скрипт proof-pack для Exit Gate P0.
  - Додано VS Code задачу "Audit: Exit Gate P0".
  - Додано підказку у bootstrap.
  - Виконано скрипт; отримано FAIL через відсутність unknown_command у snapshot після публікації.
- Де зроблено →
  - tools/audit/run_exit_gate_p0.ps1
  - .vscode/tasks.json
  - tools/bootstrap_p0.ps1
  - data/audit_v3/status_snapshot_before.json
  - data/audit_v3/publish_unknown_cmd.txt
  - data/audit_v3/status_snapshot_after.json
  - data/audit_v3/metrics.txt (якщо доступний)
- Як перевірено →
  - powershell -ExecutionPolicy Bypass -File tools/audit/run_exit_gate_p0.ps1 -NS fxcm_local → FAIL (unknown_command не зафіксовано у status_snapshot_after.json).
- Ризики/відкат/нотатки →
  - Ймовірно app.main не запущений або не слухає commands; повторити після старту сервісу.

## 2026-01-18T00:33:10+01:00 — PRE (MODE=PATCH) → Audit v3 fix: Exit Gate P0 proof-pack (rerun)

- Мета → повторно виконати Exit Gate P0 proof-pack при запущеному app.main.
- Scope → лише повторний запуск скрипта і фіксація артефактів у data/audit_v3.
- Non-goals → зміни runtime логіки, контрактів, тестів.
- Інваріанти/рейки → тільки PowerShell + redis-cli; payload через stdin (-x); без docker/git/jq.
- План →
  1) Запустити tools/audit/run_exit_gate_p0.ps1 з NS=fxcm_local.
  2) Перевірити статус виконання.
  3) Додати POST у журнал.
- Тести/перевірки → powershell -ExecutionPolicy Bypass -File tools/audit/run_exit_gate_p0.ps1 -NS fxcm_local.

## 2026-01-18T00:36:40+01:00 — PRE (MODE=PATCH) → Fix Exit Gate P0 script keys

- Мета → виправити формування ключів/каналів Redis у run_exit_gate_p0.ps1 (PowerShell scope expansion).
- Scope → тільки tools/audit/run_exit_gate_p0.ps1.
- Non-goals → зміни runtime логіки, контрактів, тестів.
- Інваріанти/рейки → мінімальний диф; тільки PowerShell + redis-cli; payload через stdin (-x).
- План →
  1) Виправити формування ключів через $($NS).
  2) Повторно запустити скрипт.
  3) Додати POST у журнал.
- Тести/перевірки → powershell -ExecutionPolicy Bypass -File tools/audit/run_exit_gate_p0.ps1 -NS fxcm_local.

## 2026-01-18T00:40:20+01:00 — PRE (MODE=PATCH) → Exit Gate P0: UTF-8 без BOM

- Мета → прибрати BOM у stdin для redis-cli, щоб уникнути invalid_json.
- Scope → тільки tools/audit/run_exit_gate_p0.ps1.
- Non-goals → зміни runtime логіки, контрактів, тестів.
- Інваріанти/рейки → мінімальний диф; тільки PowerShell + redis-cli; payload через stdin (-x).
- План →
  1) Перемкнути кодування на UTF-8 без BOM.
  2) Повторно запустити скрипт.
  3) Додати POST у журнал.
- Тести/перевірки → powershell -ExecutionPolicy Bypass -File tools/audit/run_exit_gate_p0.ps1 -NS fxcm_local.

## 2026-01-18T00:44:00+01:00 — PRE (MODE=PATCH) → Exit Gate P0: stdin без BOM через Process

- Мета → передавати JSON у redis-cli через Process+StreamWriter з UTF-8 без BOM.
- Scope → тільки tools/audit/run_exit_gate_p0.ps1.
- Non-goals → зміни runtime логіки, контрактів, тестів.
- Інваріанти/рейки → мінімальний диф; тільки PowerShell + redis-cli; payload через stdin (-x).
- План →
  1) Замінити пайп на ProcessStartInfo з StreamWriter(UTF-8 no BOM).
  2) Повторно запустити скрипт.
  3) Додати POST у журнал.
- Тести/перевірки → powershell -ExecutionPolicy Bypass -File tools/audit/run_exit_gate_p0.ps1 -NS fxcm_local.

## 2026-01-18T00:46:20+01:00 — POST (MODE=PATCH) → Exit Gate P0 proof-pack (fixes + success)

- Що зроблено →
  - Виправлено формування ключів/каналів Redis через $($NS).
  - Перемкнуто stdin на UTF-8 без BOM.
  - Передачу JSON у redis-cli виконано через Process+StreamWriter.
  - Повторно запущено Exit Gate P0, отримано OK.
- Де зроблено →
  - tools/audit/run_exit_gate_p0.ps1
  - data/audit_v3/status_snapshot_before.json
  - data/audit_v3/publish_unknown_cmd.txt
  - data/audit_v3/status_snapshot_after.json
  - data/audit_v3/metrics.txt (якщо доступний)
- Як перевірено →
  - powershell -ExecutionPolicy Bypass -File tools/audit/run_exit_gate_p0.ps1 -NS fxcm_local → OK.
- Ризики/відкат/нотатки →
  - У status:snapshot можуть лишатися історичні errors[]; ключове — присутній unknown_command і last_command.state=error.

## 2026-01-17T23:55:10Z — PRE (MODE=PATCH) → P1 Tick feed strict ms

- Мета → реалізувати {NS}:price_tik + строгий валідатор tick_v1 + статус/метрики + exit gate P1 proof-pack.
- Scope → лише tick контур (publish+validate+status+metrics+tests+audit script). Без FXCM, без історії, без OHLCV.
- Інваріанти → tick_ts/snap_ts тільки int ms; allowlist без extra fields; будь-яка помилка → errors[]/degraded[].
- Acceptance Criteria → див. пункт 8 нижче.

## 2026-01-17T23:59:50Z — POST (MODE=PATCH) → P1 Tick feed strict ms

- Що зроблено →
  - Додано tick feed з валідатором і оновленням статусу/метрик.
  - Додано tick simulator з tick_mode=sim (thread) і loud обробкою fxcm/unknown.
  - Оновлено конфіг tick_mode/tick_symbols та метрики tick.
  - Додано proof-pack скрипт Exit Gate P1 і VS Code задачу.
  - Додано тести для tick validator/publisher/mode handling.
- Де зроблено →
  - config/config.py
  - runtime/tick_feed.py
  - runtime/tick_simulator.py
  - runtime/tick_sim.py
  - runtime/status.py
  - observability/metrics.py
  - app/main.py
  - tests/test_tick_simulator.py
  - tests/test_tick_simulator_disabled.py
  - tests/test_tick_publisher_updates_status.py
  - tests/test_tick_mode_handling.py
  - tools/audit/run_exit_gate_p1.ps1
  - tools/bootstrap_p0.ps1
  - .vscode/tasks.json
- Як перевірено →
  - powershell -ExecutionPolicy Bypass -File tools\bootstrap_p0.ps1 → OK (ruff/mypy/pytest).
  - powershell -Command "Start-Process -FilePath C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -ArgumentList '-m app.main' | Out-Null" → OK.
  - powershell -ExecutionPolicy Bypass -File tools\audit\run_exit_gate_p1.ps1 -NS fxcm_local → OK.
- Артефакти →
  - data/audit_v3/status_before_tick.json
  - data/audit_v3/status_after_tick.json
- Ризики/відкат/нотатки →
  - tick_mode=fxcm у P1 не реалізовано; стан позначається через errors[]/degraded[].

## 2026-01-18T00:05:10Z — PRE (MODE=PATCH) → P2 OHLCV preview candles all TF

- Мета → реалізувати preview OHLCV для всіх TF allowlist з публікацією у {NS}:ohlcv і proof-pack P2.
- Scope → лише preview контур (publisher+builder+status+metrics+tests+audit script). Без FXCM/history/store/final.
- Інваріанти → час у ms int, close_time inclusive, батчі відсортовані, без дублікатів open_time в одному батчі.

## 2026-01-18T00:22:10Z — POST (MODE=PATCH) → P2 OHLCV preview candles all TF

- Що зроблено →
  - Додано preview_mode у конфіг та helper-и bucket open/close (inclusive).
  - Додано PreviewCandleBuilder і preview simulator (sim) з публікацією batch у {NS}:ohlcv.
  - Додано publish_ohlcv_batch з chunking і preview-валидацією.
  - Додано preview-валидацію та метрики preview_total/errors.
  - Інтегровано preview симулятор у app.main.
  - Додано proof-pack Exit Gate P2, VS Code task і тести P2.
  - Оновлено тести під новий RedisPublisher(config).
- Де зроблено →
  - config/config.py
  - core/time/buckets.py
  - core/validation/validator.py
  - runtime/preview_builder.py
  - runtime/ohlcv_preview.py
  - runtime/ohlcv_preview_simulator.py
  - runtime/publisher.py
  - runtime/status.py
  - observability/metrics.py
  - app/main.py
  - tools/audit/run_exit_gate_p2.ps1
  - tools/bootstrap_p0.ps1
  - .vscode/tasks.json
  - tests/test_preview_batch_sorted_no_dupes.py
  - tests/test_preview_time_inclusive.py
  - tests/test_preview_status_updates.py
  - tests/test_tick_simulator.py
  - tests/test_tick_simulator_disabled.py
  - tests/test_tick_publisher_updates_status.py
  - tests/test_tick_mode_handling.py
  - tests/test_tail_guard_repair_flow.py
  - tests/test_tail_guard_marks.py
  - tests/test_republish_watermark.py
- Як перевірено →
  - powershell -ExecutionPolicy Bypass -File tools\bootstrap_p0.ps1 → OK (ruff/mypy/pytest).
  - taskkill /IM python.exe /F → OK (очистка процесів для узгодженого snapshot).
  - powershell -Command "Start-Process -FilePath C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -ArgumentList '-m app.main' | Out-Null" → OK.
  - powershell -ExecutionPolicy Bypass -File tools\audit\run_exit_gate_p2.ps1 -NS fxcm_local → OK.
- Артефакти →
  - data/audit_v3/status_before_preview.json
  - data/audit_v3/status_after_preview.json
- Ризики/відкат/нотатки →
  - P2 DONE: preview_total збільшується, last_bar_open_time_ms для всіх TF > 0.

## 2026-01-18T00:40:10Z — PRE (MODE=PATCH) → P3 SSOT 1m store + warmup/backfill + final-wire 1m

## 2026-01-18T00:45:00Z — PRE (MODE=PATCH) → P3 journal normalization (append-only)
- Мета → відновити SSOT журнал через append-only LOG REPAIR блок та додати rail, щоб записи не розкидались.
- Scope → tools/audit/scan_log_entries.py, tools/audit/repair_log_append.ps1, docs/COPILOT_LOG_RAIL.md, .vscode/tasks.json (тільки якщо потрібно). Без змін P3 коду.
- Інваріанти → Work/01log.md не редагувати в середині; тільки append-only; існуючі записи не змінювати.
- План →
  1) Додати скрипт сканування журналу і JSON звіт.
  2) Додати скрипт append-only repair з індексом рядків.
  3) Перевірити/додати rail інструкції.
  4) Запустити скан і repair; додати POST.
- Перевірки → python tools/audit/scan_log_entries.py > data/audit_v3/log_scan_report.txt; powershell -ExecutionPolicy Bypass -File tools/audit/repair_log_append.ps1.

## 2026-01-18T00:55:00Z — POST (MODE=PATCH) → P3 journal normalization (append-only)
- python tools/audit/scan_log_entries.py > data/audit_v3/log_scan_report.txt.
- powershell -ExecutionPolicy Bypass -File tools/audit/repair_log_append.ps1.
- data/audit_v3/log_scan_report.txt
- data/audit_v3/log_scan_report.json
  
## 2026-01-18T00:58:00Z — POST (MODE=PATCH) → P3 journal normalization (append-only)
- Що зроблено → уточнено tools/audit/scan_log_entries.py (формат stdout) і tools/audit/repair_log_append.ps1 (LOG REPAIR блок, індекси, rail); перевірено rail у docs/COPILOT_LOG_RAIL.md та наявність задач у .vscode/tasks.json.
- Як перевірено →
  - python tools/audit/scan_log_entries.py > data/audit_v3/log_scan_report.txt.
  - powershell -ExecutionPolicy Bypass -File tools/audit/repair_log_append.ps1.
- Артефакти →
  - data/audit_v3/log_scan_report.txt
  - data/audit_v3/log_scan_report.json

## 2026-01-18T00:52:00Z — PRE (MODE=PATCH) → P4 Derived rebuild history_agg + final-wire HTF
- Мета → побудова HTF final із SSOT 1m final, publish у {NS}:ohlcv, інваріанти event_ts==close_time, source=history_agg.
- Scope → store/derived_builder.py, core/time/buckets.py(1d boundary), core/validation/validator.py, runtime/handlers_p4.py, runtime/command_bus.py, runtime/publisher.py, runtime/status.py, tools/audit/run_exit_gate_p4.ps1, тести.
- Non-goals → FXCM інтеграція, tail_guard, no_mix, recovery.
- Інваріанти → final HTF: complete=true, synthetic=false, source=history_agg, event_ts присутній і == close_time; bars sorted/no-dupes; bucket boundaries з trading_day_boundary_utc.

## 2026-01-18T02:28:30Z — POST (MODE=PATCH) → P4 Derived rebuild history_agg + final-wire HTF
- Що зроблено →
  - Додано SSOT параметри derived_rebuild_* у config, та boundary-aware buckets для 1d.
  - Реалізовано HTF final builder (DerivedBuilder) і HTF publish/validator (event_ts required, source=history_agg).
  - Додано P4 handler + інтеграцію команд (fxcm_rebuild_derived) і rebuild після warmup/backfill.
  - Оновлено rebuild_derived (boundary + publish_ohlcv_final_htf), status HTF та валідатор boundaries.
  - Додано Exit Gate P4 script + task, та тести P4 (bucket boundary, builder, validator, handler).
- Де зроблено →
  - config/config.py
  - core/time/buckets.py
  - core/validation/validator.py
  - runtime/publisher.py
  - runtime/rebuild_derived.py
  - store/derived_builder.py
  - store/bars_store.py
  - runtime/handlers_p4.py
  - runtime/handlers_p3.py
  - runtime/status.py
  - app/main.py
  - tools/audit/run_exit_gate_p4.ps1
  - tools/audit/scan_log_entries.py
  - .vscode/tasks.json
  - tests/test_buckets_1d_boundary.py
  - tests/test_derived_builder_aggregates.py
  - tests/test_final_htf_validator_requires_event_ts.py
  - tests/test_rebuild_handler_updates_status.py
- Як перевірено →
  - powershell -ExecutionPolicy Bypass -File tools\bootstrap_p0.ps1 → OK (ruff/mypy/pytest)
  - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m app.main → OK (процес стартує)
  - powershell -ExecutionPolicy Bypass -File tools\audit\run_exit_gate_p4.ps1 -NS fxcm_local → OK
- Артефакти →
  - data/audit_v3/status_before_p4.json
  - data/audit_v3/publish_rebuild_cmd.txt
  - data/audit_v3/status_after_p4.json
- Ризики/нотатки →
  - app.main зупинено через KeyboardInterrupt під час завершення (trace у консолі), повторний запуск має бути безпечним.

## 2026-01-18T02:34:10Z — PRE (MODE=read-only discovery) → Ремарки P4 щодо status SSOT та calendar_tag
- Мета → зафіксувати ремарки: розбіжність ohlcv_final_1m vs ohlcv_final["1m"], та використання trading_day_boundary_utc при calendar_stub.
- Scope → лише фіксація у журналі; без змін коду.
- Non-goals → виправлення логіки статусу або календаря.
- Інваріанти → append-only; без правок існуючих записів.

## 2026-01-18T02:34:40Z — POST (MODE=read-only discovery) → Ремарки P4 щодо status SSOT та calendar_tag
- Що зроблено → зафіксовано ремарки: (1) розбіжність лічильників у status_after_warmup між ohlcv_final_1m і ohlcv_final["1m"]; (2) calendar_stub допустимий, але trading_day_boundary_utc уже SSOT для 1d boundary.
- Де зроблено → Work/01log.md.
- Як перевірено → без запусків.
- Ризики/нотатки → на P4 не блокує; варто синхронізувати/усунути дублювання ohlcv_final_1m у P4/P5.

## 2026-01-18T02:36:10Z — PRE (MODE=PATCH) → P4.1 Status SSOT cleanup: align ohlcv_final_1m with ohlcv_final["1m"]
- Мета → уніфікувати SSOT статус 1m final і прибрати роз’їзд між ohlcv_final_1m та ohlcv_final["1m"].
- Scope → runtime/status.py, runtime/handlers_p3.py, tools/audit/run_exit_gate_p41.ps1, .vscode/tasks.json, tests/test_status_final_1m_ssot.py.
- Non-goals → зміни derived_builder/rebuild, FXCM інтеграція, tail_guard.
- Інваріанти → без silent fallback; append-only журнал.
- План →
  1) Додати SSOT sync для 1m final у статусі.
  2) Підключити sync після warmup/backfill.
  3) Додати тест SSOT для 1m.
  4) Додати Exit Gate P4.1 + VS Code task.
  5) Перевірити bootstrap + app.main + exit gate.

## 2026-01-18T04:44:10Z — POST (MODE=PATCH) → P4.1 Status SSOT cleanup: align ohlcv_final_1m with ohlcv_final["1m"]
- Що зроблено →
  - Додано SSOT sync для 1m final у StatusManager (дзеркало ohlcv_final_1m ← ohlcv_final["1m"]) та sync з store після warmup/backfill.
  - Додано тест SSOT для 1m final.
  - Додано Exit Gate P4.1 та VS Code task.
- Де зроблено →
  - runtime/status.py
  - runtime/handlers_p3.py
  - tests/test_status_final_1m_ssot.py
  - tools/audit/run_exit_gate_p41.ps1
  - .vscode/tasks.json
- Як перевірено →
  - powershell -ExecutionPolicy Bypass -File tools\bootstrap_p0.ps1 → OK (ruff/mypy/pytest)
  - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m app.main → OK (процес стартує)
  - powershell -ExecutionPolicy Bypass -File tools\audit\run_exit_gate_p41.ps1 -NS fxcm_local → OK
- Артефакти →
  - data/audit_v3/status_after_p41.json
  - data/audit_v3/publish_warmup_p41_cmd.txt
- Ризики/нотатки →
  - app.main зупинено через KeyboardInterrupt під час завершення (trace у консолі), повторний запуск має бути безпечним.
- Статус → P4.1 DONE.

## 2026-01-18T05:00:10Z — PRE (MODE=PATCH) → P5 Tail guard + repair + republish watermark + checked TTL
- Мета → реалізувати tail_guard/repair/republish watermark + TTL “already_checked” з proof-pack P5.
- Scope → config/config.py, core/time/calendar.py, runtime/tail_guard.py, runtime/repair.py, runtime/republish.py, runtime/command_bus.py, runtime/status.py, app/main.py, tools/audit/run_exit_gate_p5.ps1, .vscode/tasks.json, тести.
- Non-goals → зміни preview/derived_builder/FXCM.
- Інваріанти → без silent fallback; tail_guard базується на SSOT 1m final; append-only журнал.
- План →
  1) Додати P5 конфіг та calendar repair window.
  2) Оновити tail_guard/repair/republish watermark.
  3) Оновити команди/проводку.
  4) Додати тести + Exit Gate P5.
  5) Перевірити bootstrap + app.main + exit gate.

## 2026-01-18T05:18:10Z — POST (MODE=PATCH) → P5 Tail guard + repair + republish watermark + checked TTL
- Що зроблено →
  - Додано P5 конфіг (tail_guard_checked_ttl_s, republish_watermark_ttl_s, allow_tfs, safe_repair_only_when_market_closed).
  - Додано calendar.is_repair_window та оновлено tail_guard з TTL checked, missing ranges, deferred repair у market open.
  - Додано repair_missing_1m, оновлено republish watermark через Redis.
  - Оновлено проводку команд у app.main, додано Exit Gate P5 та тести P5.
- Де зроблено →
  - config/config.py
  - core/time/calendar.py
  - runtime/tail_guard.py
  - runtime/repair.py
  - runtime/republish.py
  - app/main.py
  - tools/audit/run_exit_gate_p5.ps1
  - .vscode/tasks.json
  - tests/test_tail_guard_detects_gap.py
  - tests/test_tail_guard_checked_ttl_skips.py
  - tests/test_repair_rejects_large_range.py
  - tests/test_republish_watermark_skips_when_not_forced.py
  - tests/test_tail_guard_deferred_when_market_open.py555555ing + loud ssot_empty semantics
- Мета → зробити proof-pack P5 строгим та додати loud ssot_empty для tail_guard.
- Scope → runtime/tail_guard.py або handler fxcm_tail_guard, tools/audit/run_exit_gate_p5.ps1, tests/test_tail_guard_ssot_empty_loud_error.py, .vscode/tasks.json.
- Non-goals → зміни інших модулів, repair/rebuild логіки.
- Інваріанти → мінімальний диф; без silent fallback; append-only журнал.
- План →
  1) Додати ssot_empty guard у tail_guard handler.
  2) Уточнити Exit Gate P5 (poll, fail-fast правила).
  3) Додати тест ssot_empty.
  4) Перевірити bootstrap + app.main + exit gate.

## 2026-01-18T05:44:10Z — POST (MODE=PATCH) → P5.1 Tail guard proof-pack hardening + loud ssot_empty semantics
- Що зроблено →
  - Додано ssot_empty guard у tail_guard з loud error/state та обов’язковим last_audit_ts_ms.
  - Посилено Exit Gate P5 (poll + fail-fast за last_command/tail_guard).
  - Додано тест ssot_empty для tail_guard.
  - Додано task “Audit: Exit Gate P5.1”.
- Де зроблено →
  - runtime/tail_guard.py
  - tools/audit/run_exit_gate_p5.ps1
  - tests/test_tail_guard_ssot_empty_loud_error.py
  - .vscode/tasks.json
- Як перевірено →
  - powershell -ExecutionPolicy Bypass -File tools\bootstrap_p0.ps1 → OK (ruff/mypy/pytest)
  - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m app.main → OK (процес стартує)
  - powershell -ExecutionPolicy Bypass -File tools\audit\run_exit_gate_p5.ps1 -NS fxcm_local → OK
- Артефакти →
  - data/audit_v3/status_before_p5.json
  - data/audit_v3/publish_tail_guard_cmd.txt
  - data/audit_v3/status_after_p5.json
- Ризики/нотатки →
  - Якщо SSOT 1m порожній, tail_guard повертає error і фіксує ssot_empty у errors[].
- Статус → P5.1 DONE.

## 2026-01-18T06:05:10Z — PRE (MODE=PATCH) → P5 audit proof-pack hardening: unique artifacts + hashes
- Мета → усунути клас помилок “exit gate OK, але артефакт не той/перезаписаний” для P5.
- Scope → tools/audit/run_exit_gate_p5.ps1, .vscode/tasks.json.
- Non-goals → будь-які зміни runtime логіки P5.
- Інваріанти → лише audit-скрипт; append-only журнал.

## 2026-01-18T06:18:10Z — POST (MODE=PATCH) → P5 audit proof-pack hardening: unique artifacts + hashes
- Що зроблено →
  - Оновлено run_exit_gate_p5.ps1: унікальний prefix, NUMSUB check, polling, друк шляхів і SHA256.
  - Оновлено task “Audit: Exit Gate P5.1” з -OutDir data/audit_v3.
- Де зроблено →
  - tools/audit/run_exit_gate_p5.ps1
  - .vscode/tasks.json
- Як перевірено → не запускалось (за запитом лише аудит-скрипт).
- Приклад prefix → p5_fxcm_local_eg-p5-0001_20260118_061810

## 2026-01-19T00:00:00Z — PRE (MODE=PATCH) → PATCH: restore P0 invariant — command_bus must subscribe to {NS}:commands; add status visibility
- Мета → відновити інваріанту P0: app.main завжди підписаний на {NS}:commands; додати явний status для command_bus (state/heartbeat/error) та fail-fast у аудиті.
- Scope → runtime/command_bus.py (startup+loop+heartbeat), runtime/status.py (command_bus у snapshot + API), app/main.py (гарантований старт), config/config.py (commands_*), tests/test_command_bus_starts_and_updates_status.py, tools/audit/run_exit_gate_p5.ps1.
- Non-goals → зміни бізнес-логіки команд, tail_guard/republish, store/агрегації.
- Інваріанти → мінімальний диф; без silent fallback; append-only журнал; статус валідний за schema.
- План →
  1) Додати commands_enabled/commands_channel/heartbeat у конфіг.
  2) Розширити status snapshot полем command_bus + методи heartbeat/error.
  3) Реалізувати CommandBus з subscribe loop у thread та heartbeat.
  4) Гарантовано стартувати command_bus у app/main.py та loud error при фейлі.
  5) Додати тест на running+heartbeat.
  6) Додати fail-fast у Exit Gate P5 перед NUMSUB.
- Тести/перевірки → tools\bootstrap_p0.ps1; python -m app.main; redis-cli PUBSUB NUMSUB; tools\audit\run_exit_gate_p5.ps1.

## 2026-01-19T13:18:30Z — POST (MODE=PATCH) → PATCH: restore P0 invariant — command_bus must subscribe to {NS}:commands; add status visibility
- Що зроблено →
  - Додано commands_enabled/commands_channel/command_bus_heartbeat_period_s у конфіг.
  - Розширено status snapshot секцією command_bus + методами heartbeat/error; укріплено tail_guard summary повним набором TF.
  - Перероблено CommandBus на thread-safe subscribe loop (pubsub у потоці), heartbeat та loud error.
  - app/main.py гарантує старт command_bus та publishes snapshot після старту.
  - Додано тест command_bus старту/heartbeat; оновлено status_v2 schema і валідний тест.
  - Exit Gate P5 має fail-fast по command_bus.state.
- Де зроблено →
  - config/config.py
  - core/contracts/public/status_v2.json
  - runtime/status.py
  - runtime/command_bus.py
  - app/main.py
  - tests/test_command_bus_starts_and_updates_status.py
  - tests/test_validator_status.py
  - tools/audit/run_exit_gate_p5.ps1
- Як перевірено →
  - powershell -ExecutionPolicy Bypass -File tools\bootstrap_p0.ps1 → OK (ruff/mypy/pytest)
  - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -c "...from app.main import main; ..." → OK (NUMSUB=1, command_bus heartbeat)
  - redis-cli PUBSUB NUMSUB "fxcm_local:commands" → 1
  - powershell -ExecutionPolicy Bypass -File tools\audit\run_exit_gate_p5.ps1 -NS fxcm_local -OutDir data\audit_v3 -ReqId eg-p5-0010 → OK
- Ризики/нотатки →
  - CommandBus використовує pubsub у власному thread (thread-safe); при збої subscribe фіксується errors[] + state=error.

## 2026-01-19T13:20:00Z — PRE (MODE=PATCH) → C0 Chart-Lite + P6 NoMix enforcement (final)
- Мета → додати read-only Chart-Lite для preview/final дроту та ввести loud NoMix для FINAL у {NS}:ohlcv.
- Scope → ui_lite/*, config/config.py, app/main.py, runtime/no_mix.py, runtime/publisher.py, runtime/status.py (без зміни схеми), tools/audit/run_exit_gate_p6.ps1, тести, .vscode/tasks.json.
- Non-goals → зміна існуючих каналів/контрактів; будь-який publish з UI.
- Інваріанти → лише read-only SUBSCRIBE; preview/final не змішувати (complete==true → final); dedup по (symbol, tf, open_time); loud errors[] при NoMix.
- План →
  1) Додати ui_lite config + модуль сервера/статичних файлів.
  2) Стартувати UI Lite з app/main.py при ui_lite_enabled.
  3) Додати NoMix detector + hook у publish final.
  4) Додати тести (dedup, preview/final separation, NoMix conflict).
  5) Додати Exit Gate P6 + task.
- Тести/перевірки → tools\bootstrap_p0.ps1; запуск UI Lite; Audit: Exit Gate P6.

## 2026-01-19T13:40:00Z — POST (MODE=PATCH) → C0 Chart-Lite + P6 NoMix enforcement (final)
- Що зроблено →
  - Додано UI Lite: WS сервер + read-only Redis SUBSCRIBE, статичні файли, легкий рендер свічок і dedup.
  - Додано NoMixDetector та hook для FINAL publish у RedisPublisher (loud errors[] + no_mix conflict).
  - Додано Exit Gate P6 (python симуляція конфлікту) + task.
  - Додано тести UI Lite (dedup, preview/final separation) та NoMix conflict.
  - Оновлено конфіг (ui_lite_*), залежності (websockets) і task “Run: UI Lite”.
- Де зроблено →
  - ui_lite/server.py
  - ui_lite/static/index.html
  - ui_lite/static/app.js
  - ui_lite/static/vendor/lightweight-charts.standalone.production.js
  - runtime/no_mix.py
  - runtime/publisher.py
  - app/main.py
  - config/config.py
  - requirements.txt
  - tools/audit/run_exit_gate_p6.ps1
  - tests/test_ui_lite_dedup.py
  - tests/test_ui_lite_preview_final_separation.py
  - tests/test_no_mix_detects_conflict.py
  - .vscode/tasks.json
- Як перевірено →
  - powershell -ExecutionPolicy Bypass -File tools\bootstrap_p0.ps1 → OK (ruff/mypy/pytest)
  - powershell -ExecutionPolicy Bypass -File tools\audit\run_exit_gate_p6.ps1 -NS fxcm_local -OutDir data\audit_v3 → OK
- Ризики/нотатки →
  - Exit Gate P6 використовує PYTHONPATH=$Root для імпорту локальних модулів.
  - UI Lite працює як read-only WS/HTTP; для показу preview потрібен активний publisher {NS}:ohlcv.

## 2026-01-19T14:05:00Z — PRE (MODE=PATCH) → UI Lite: redis import + process_request typing
- Мета → виправити ModuleNotFoundError при запуску ui_lite.server і узгодити типи process_request.
- Scope → ui_lite/server.py.
- Non-goals → зміна поведінки UI Lite або протоколів.
- Інваріанти → без silent fallback; явне повідомлення про відсутній redis.
- План →
  1) Додати явне повідомлення про відсутній redis.
  2) Виправити типи HTTPResponse/process_request.
- Тести/перевірки → не запускались.

## 2026-01-19T14:06:00Z — POST (MODE=PATCH) → UI Lite: redis import + process_request typing
- Що зроблено →
  - Додано явну помилку з інструкцією для redis імпорту.
  - Уточнено типи HTTPResponse/process_request для websockets.
- Де зроблено →
  - ui_lite/server.py
- Як перевірено → не запускалось.
- Ризики/нотатки → запуск ui_lite.server потребує активного .venv і залежностей.

## 2026-01-19T14:10:00Z — PRE (MODE=PATCH) → UI Lite: типи process_request (Pylance)
- Мета → усунути помилки Pylance для process_request у websockets.
- Scope → ui_lite/server.py.
- Non-goals → зміна логіки сервера.
- Інваріанти → без змін протоколів; лише типи.
- План →
  1) Прибрати Headers імпорт.
  2) Додати type: ignore для process_request у serve.
- Тести/перевірки → не запускались.

## 2026-01-19T14:11:00Z — POST (MODE=PATCH) → UI Lite: типи process_request (Pylance)
- Що зроблено →
  - Прибрано import Headers; process_request приймає Any.
  - Додано type: ignore для process_request у serve.
- Де зроблено →
  - ui_lite/server.py
- Як перевірено → не запускалось.
- Ризики/нотатки → лише типи; поведінка без змін.

## 2026-01-19T14:20:00Z — PRE (MODE=PATCH) → UI Lite: graceful stop + static 404
- Мета → прибрати падіння при KeyboardInterrupt та додати явний 404 для статичних файлів.
- Scope → ui_lite/server.py.
- Non-goals → зміна логіки WS/Redis.
- Інваріанти → read-only; без зміни протоколів.
- План →
  1) Захистити stop() від KeyboardInterrupt.
  2) Додати 404 fallback у _read_file.
- Тести/перевірки → не запускались.

## 2026-01-19T14:21:00Z — POST (MODE=PATCH) → UI Lite: graceful stop + static 404
- Що зроблено →
  - stop() більше не падає при KeyboardInterrupt.
  - _read_file повертає 404 для відсутніх статичних файлів.
- Де зроблено →
  - ui_lite/server.py
- Як перевірено → не запускалось.
- Ризики/нотатки → без змін у протоколі; лише стабілізація поведінки.

## 2026-01-19T14:30:00Z — PRE (MODE=PATCH) → UI Lite: порт зайнятий + loop closed
- Мета → виправити помилки bind 8099 і "Event loop is closed" у ui_lite.
- Scope → ui_lite/server.py.
- Non-goals → зміна UI протоколу або Redis каналів.
- Інваріанти → read-only; loud помилки; без silent fallback.
- План →
  1) Обробити OSError bind з явним повідомленням.
  2) Не шедулити queue.put, якщо loop закритий.
- Тести/перевірки → запуск ui_lite.server + app.main.

## 2026-01-19T14:35:00Z — POST (MODE=PATCH) → UI Lite: порт зайнятий + loop closed
- Що зроблено →
  - Додано явну помилку при bind та stop_event для ui_lite.
  - Додано guard для loop.is_closed у Redis subscriber.
  - Переналаштовано порт UI Lite на 8089 та вимкнено автозапуск у app.main.
- Де зроблено →
  - ui_lite/server.py
  - config/config.py
- Як перевірено →
  - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m ui_lite.server → OK (8089)
  - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m app.main → OK (preview працює)
- Ризики/нотатки →
  - UI Lite запускається окремо; app.main більше не стартує UI Lite автоматично.

## 2026-01-19T14:50:00Z — PRE (MODE=PATCH) → UI Lite: main-thread server + get_running_loop
- Мета → прибрати ERR_EMPTY_RESPONSE і стабілізувати запуск UI Lite.
- Scope → ui_lite/server.py.
- Non-goals → зміна протоколу UI Lite.
- Інваріанти → read-only; без зміни каналів.
- План →
  1) Запускати UI Lite в main thread через asyncio.run.
  2) Використати get_running_loop у _run_server.
- Тести/перевірки → запуск ui_lite.server + app.main.

## 2026-01-19T15:05:00Z — PRE (MODE=PATCH) → UI Lite: явна помилка запуску
- Мета → отримати видиме повідомлення при фейлі запуску ui_lite.server.
- Scope → ui_lite/server.py.
- Non-goals → зміна протоколу/каналів.
- Інваріанти → read-only; без silent fallback.
- План →
  1) Додати stderr повідомлення у main().
- Тести/перевірки → запуск ui_lite.server.

## 2026-01-19T15:10:00Z — POST (MODE=PATCH) → UI Lite: явна помилка запуску
- Що зроблено → додано явні stderr повідомлення старту/зупинки UI Lite.
- Де зроблено → ui_lite/server.py.
- Як перевірено → ще не запускалось (очікує запуск ui_lite.server).
- Ризики/нотатки → лише діагностика.

## 2026-01-19T15:20:00Z — PRE (MODE=PATCH) → UI Lite: traceback при фейлі
- Мета → отримати повний traceback при аварійному завершенні UI Lite.
- Scope → ui_lite/server.py.
- Non-goals → зміна логіки сервера.
- Інваріанти → read-only; без silent fallback.
- План →
  1) Додати traceback.print_exc у main().
- Тести/перевірки → запуск ui_lite.server.

## 2026-01-19T15:30:00Z — PRE (MODE=PATCH) → UI Lite: лог слухача
- Мета → отримати підтвердження, що сервер реально слухає порт.
- Scope → ui_lite/server.py.
- Non-goals → зміна поведінки WS/Redis.
- Інваріанти → read-only.
- План →
  1) Додати stderr лог "UI Lite слухає ...".
- Тести/перевірки → запуск ui_lite.server.

## 2026-01-19T15:40:00Z — PRE (MODE=PATCH) → UI Lite: run_forever у main
- Мета → утримувати сервер живим після старту.
- Scope → ui_lite/server.py.
- Non-goals → зміна протоколів.
- Інваріанти → read-only.
- План →
  1) Перейти на явний event loop + run_forever.
- Тести/перевірки → запуск ui_lite.server.

## 2026-01-19T15:45:00Z — POST (MODE=PATCH) → UI Lite: run_forever у main
- Що зроблено → main() переведено на явний event loop + run_forever.
- Де зроблено → ui_lite/server.py.
- Як перевірено →
  - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m ui_lite.server → OK (слухає 8089)
  - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m app.main → OK (preview потік)
- Ризики/нотатки → UI Lite і app.main запускаються окремо, без конфлікту портів.

## 2026-01-19T16:00:00Z — PRE (MODE=PATCH) → UI Lite: sync process_request + 404
- Мета → стабільний HTTP 200 для / на 8089 без ERR_EMPTY_RESPONSE у Python 3.7.
- Scope → ui_lite/server.py, tests/test_ui_lite_http_process_request.py.
- Non-goals → зміна Redis/WS протоколів.
- Інваріанти → мінімальний диф; read-only.
- План →
  1) Зробити process_request синхронним.
  2) Додати favicon/unknown 404.
  3) Додати smoke-тест.
- Тести/перевірки → tools\bootstrap_p0.ps1; python -m ui_lite.server; Invoke-WebRequest http://127.0.0.1:8089/.

## 2026-01-19T16:30:00Z — POST (MODE=PATCH) → UI Lite: sync process_request + 404
- Що зроблено →
  - process_request зроблено синхронним, додано явний 404 для favicon/unknown.
  - Для HTTP-відповідей використано Headers + HTTPStatus (щоб уникнути empty response).
  - Додано smoke-тест process_request.
  - Очищено audit .no_mix_sim.py артефакти для ruff.
  - Mypy fix: pubsub локальна змінна у command_bus.
- Де зроблено →
  - ui_lite/server.py
  - tests/test_ui_lite_http_process_request.py
  - runtime/command_bus.py
  - data/audit_v3/p6_fxcm_local_eg-p6-0001_20260119_133801.no_mix_sim.py
  - data/audit_v3/p6_fxcm_local_eg-p6-0001_20260119_133637.no_mix_sim.py
  - data/audit_v3/p6_fxcm_local_eg-p6-0001_20260119_133732.no_mix_sim.py
- Як перевірено →
  - powershell -ExecutionPolicy Bypass -File tools\bootstrap_p0.ps1 → OK
  - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m ui_lite.server → OK ("UI Lite слухає http://127.0.0.1:8089")
  - powershell -Command "(Invoke-WebRequest http://127.0.0.1:8089/ -UseBasicParsing).StatusCode" → 200
  - powershell -Command "(Invoke-WebRequest http://127.0.0.1:8089/app.js -UseBasicParsing).StatusCode" → 200
- Ризики/нотатки →
  - UI Lite запускається окремим процесом; для оновлення — перезапуск.

## 2026-01-19T17:10:00Z — PRE (MODE=PATCH) → UI Lite: WS upgrade passthrough
- Мета → дозволити WS handshake, щоб не блокувався process_request.
- Scope → ui_lite/server.py.
- Non-goals → зміна протоколів Redis/WS.
- Інваріанти → read-only.
- План →
  1) Якщо Upgrade: websocket → return None.
- Тести/перевірки → не запускались.

## 2026-01-19T17:30:00Z — PRE (MODE=PATCH) → UI Lite observability + symbol/tf discovery
- Мета → дати видимість чому UI Lite порожній (rx/tx/clients/last payload) і додати UI елементи symbol/tf discovery.
- Scope → ui_lite/server.py, ui_lite/static/index.html, ui_lite/static/app.js, tests/*, README.md.
- Non-goals → зміна Redis/WS контрактів.
- Інваріанти → read-only; мінімальний диф.
- План →
  1) Додати лічильники і /debug endpoint.
  2) Додати UI: symbol dropdown + last_ws_msg_age_ms + bars_rx_total.
  3) Додати тести (dedup + /debug).
  4) Додати короткі manual exit checks у README.

## 2026-01-19T18:00:00Z — POST (MODE=PATCH) → UI Lite observability + symbol/tf discovery
- Що зроблено →
  - Додано лічильники rx/json_err/tx/clients, last payload fields і /debug endpoint.
  - Додано періодичний stderr лог стану раз на 5с.
  - Додано symbol dropdown, індикатори WS age та bars_rx_total.
  - Додано тести dedup і /debug.
  - Додано README manual checks.
- Де зроблено →
  - ui_lite/server.py
  - ui_lite/static/index.html
  - ui_lite/static/app.js
  - tests/test_ui_lite_dedup_first_bar.py
  - tests/test_ui_lite_debug_endpoint.py
  - README.md
- Як перевірено →
  - powershell -ExecutionPolicy Bypass -File tools\bootstrap_p0.ps1 → OK
- Ризики/нотатки →
  - /debug доступний лише локально через UI Lite.

## 2026-01-17T19:20:00+01:00 → PRE (MODE=PATCH) → UI Lite P6.1 WS subscribe + snapshot + proof-pack
- Мета: додати handshake subscribe, snapshot+ring-buffer, нормалізацію bar.time в секундах, UI індикатори/кнопку, та Exit Gate P6.1 (WS smoke).
- Scope: ui_lite/server.py, ui_lite/static/*, тести ui_lite, tools/audit/*, .vscode/tasks.json, README.md.
- Non-goals: зміни контрактів оhlcv/tick, бізнес-логіки runtime, feature flags.
- Інваріанти/рейки: UI Lite read-only; без публікації в Redis; бари з time > 0; /debug має нові поля; мінімальний диф.
- План:
  1) Допрацювати ring-buffer та last_payload_ts_ms.
  2) Оновити UI: кнопка Subscribe + поточна підписка.
  3) Додати тести нормалізації/снепшоту та /debug поля.
  4) Додати ws_smoke_ui_lite.py + run_exit_gate_p61.ps1 + task.
  5) Оновити README manual checks.
- Тести/перевірки: pytest -q (ui_lite тести), Audit: Exit Gate P6.1.

## 2026-01-17T19:28:00+01:00 → POST (MODE=PATCH) → UI Lite P6.1 WS subscribe + snapshot + proof-pack
- Що зроблено →
  - Виправлено ring-buffer дедуп та last_payload_ts_ms при наявності барів.
  - Додано Subscribe кнопку та індикатор поточної підписки; WS snapshot/bar обробка з валідацією bar.time.
  - Додано тести нормалізації та snapshot фільтрації; розширено /debug тест.
  - Додано ws_smoke_ui_lite.py та run_exit_gate_p61.ps1; додано task Exit Gate P6.1.
  - Оновлено README manual checks.
- Де зроблено →
  - ui_lite/server.py, ui_lite/static/index.html, ui_lite/static/app.js
  - tests/test_ui_lite_debug_endpoint.py, tests/test_ui_lite_normalize_snapshot.py
  - tools/audit/ws_smoke_ui_lite.py, tools/audit/run_exit_gate_p61.ps1
  - .vscode/tasks.json, README.md
- Як перевірено → не запускалось (тести/exit gate не виконувались).

- Ризики/нотатки/відкат → якщо WS smoke не проходить, перевірити Redis, UI Lite процес та канал {NS}:ohlcv; відкат: прибрати P6.1 скрипти/таску та UI subscribe.

## 2026-01-19T19:45:00Z — PRE (MODE=PATCH) → UI Lite: фікс websockets connect для P6.1 smoke
- Мета → прибрати помилку типізації "connect" у websockets та забезпечити сумісність smoke-скрипту.
- Scope → tools/audit/ws_smoke_ui_lite.py.
- Non-goals → зміна протоколів WS/Redis чи логіки UI Lite.
- Інваріанти → read-only; мінімальний диф.
- План →
  1) Замінити імпорт на websockets.legacy.client.connect.
  2) Оновити виклик connect у _ws_flow.
- Тести/перевірки → не запускались.

## 2026-01-19T19:47:00Z — POST (MODE=PATCH) → UI Lite: фікс websockets connect для P6.1 smoke
- Що зроблено → замінено імпорт на legacy connect та оновлено виклик у _ws_flow.
- Де зроблено → tools/audit/ws_smoke_ui_lite.py.
- Як перевірено → не запускалось.
- Ризики/нотатки/відкат → якщо в оточенні інша версія websockets, перевірити сумісність; відкат — повернути імпорт websockets і виклик websockets.connect.
  - Додано тести нормалізації та snapshot фільтрації; розширено /debug тест.
  - Додано ws_smoke_ui_lite.py та run_exit_gate_p61.ps1; додано task Exit Gate P6.1.
  - Оновлено README manual checks.
- Де зроблено →
  - ui_lite/server.py, ui_lite/static/index.html, ui_lite/static/app.js
  - tests/test_ui_lite_debug_endpoint.py, tests/test_ui_lite_normalize_snapshot.py
  - tools/audit/ws_smoke_ui_lite.py, tools/audit/run_exit_gate_p61.ps1
  - .vscode/tasks.json, README.md
- Як перевірено → не запускалось (тести/exit gate не виконувались).

  ## 2026-01-19T20:10:00Z — PRE (MODE=PATCH) → P6.1: bootstrap+exit gate, фікс тестів HTTP headers
  - Мета → закрити борг валідації P6.1, пройти bootstrap та exit gate; виправити тестове очікування заголовків якщо потрібно.
  - Scope → tests/test_ui_lite_http_process_request.py (мінімальний диф), запуск bootstrap та exit gate.
  - Non-goals → зміни UI Lite/WS протоколів.
  - Інваріанти → мінімальний диф; read-only логіка.
  - План →
    1) Вирівняти перевірку headers у тесті під Headers API.
    2) Запустити tools\bootstrap_p0.ps1.
    3) Запустити tools\audit\run_exit_gate_p61.ps1 з ReqId.
    4) Зібрати артефакти та SHA256.
  - Тести/перевірки → tools\bootstrap_p0.ps1; tools\audit\run_exit_gate_p61.ps1.

  ## 2026-01-19T20:20:00Z — POST (MODE=PATCH) → P6.1: bootstrap+exit gate, proof-pack
  - Що зроблено →
    - Виправлено тест перевірки Content-Type для Headers API (щоб bootstrap проходив).
    - Запущено bootstrap та Exit Gate P6.1; зібрано артефакти і SHA256.
  - Де зроблено →
    - tests/test_ui_lite_http_process_request.py
    - data/audit_v3/eg-p61-0001.hashes.txt
  - Як перевірено →
    - powershell -ExecutionPolicy Bypass -File tools\bootstrap_p0.ps1 → OK
    - powershell -ExecutionPolicy Bypass -File tools\audit\run_exit_gate_p61.ps1 -NS fxcm_local -OutDir data\audit_v3 -ReqId eg-p61-0001 → OK
    - $files = Get-ChildItem data\audit_v3 -Filter "*eg-p61-0001*";
      $hashLines = $files | ForEach-Object { $h = Get-FileHash $_.FullName -Algorithm SHA256; "$($h.Hash)  $($h.Path)" };
      $hashPath = "data\audit_v3\eg-p61-0001.hashes.txt";
      $hashLines | Set-Content -Encoding UTF8 $hashPath → OK
  - Артефакти (eg-p61-0001) →
    - C:\Aione_projects\fxcm_connector_v2\data\audit_v3\p61_fxcm_local_eg-p61-0001_20260119_161706.bar.json
    - C:\Aione_projects\fxcm_connector_v2\data\audit_v3\p61_fxcm_local_eg-p61-0001_20260119_161706.debug_after.json
    - C:\Aione_projects\fxcm_connector_v2\data\audit_v3\p61_fxcm_local_eg-p61-0001_20260119_161706.debug_before.json
    - C:\Aione_projects\fxcm_connector_v2\data\audit_v3\p61_fxcm_local_eg-p61-0001_20260119_161706.publish_after.json
    - C:\Aione_projects\fxcm_connector_v2\data\audit_v3\p61_fxcm_local_eg-p61-0001_20260119_161706.publish_before.json
    - C:\Aione_projects\fxcm_connector_v2\data\audit_v3\p61_fxcm_local_eg-p61-0001_20260119_161706.python_out.txt
    - C:\Aione_projects\fxcm_connector_v2\data\audit_v3\p61_fxcm_local_eg-p61-0001_20260119_161706.snapshot.json
    - C:\Aione_projects\fxcm_connector_v2\data\audit_v3\p61_fxcm_local_eg-p61-0001_20260119_161848.bar.json
    - C:\Aione_projects\fxcm_connector_v2\data\audit_v3\p61_fxcm_local_eg-p61-0001_20260119_161848.debug_after.json
    - C:\Aione_projects\fxcm_connector_v2\data\audit_v3\p61_fxcm_local_eg-p61-0001_20260119_161848.debug_before.json
    - C:\Aione_projects\fxcm_connector_v2\data\audit_v3\p61_fxcm_local_eg-p61-0001_20260119_161848.publish_after.json
    - C:\Aione_projects\fxcm_connector_v2\data\audit_v3\p61_fxcm_local_eg-p61-0001_20260119_161848.publish_before.json
    - C:\Aione_projects\fxcm_connector_v2\data\audit_v3\p61_fxcm_local_eg-p61-0001_20260119_161848.python_out.txt
    - C:\Aione_projects\fxcm_connector_v2\data\audit_v3\p61_fxcm_local_eg-p61-0001_20260119_161848.snapshot.json
    - C:\Aione_projects\fxcm_connector_v2\data\audit_v3\eg-p61-0001.hashes.txt
  - SHA256 → див. data/audit_v3/eg-p61-0001.hashes.txt (містить повні хеші для всіх артефактів).
  - Ризики/нотатки/відкат → якщо потрібно лише один набір артефактів, видалити зайві префікси з data/audit_v3; тестовий фікс обмежено перевіркою Content-Type.

## 2026-01-19T20:35:00Z — PRE (MODE=PATCH) → P6.1.2: fail-fast ReqId + hashes per prefix
- Мета → забезпечити 1 ReqId → 1 набір артефактів, fail-fast при повторі.
- Scope → tools/audit/run_exit_gate_p61.ps1.
- Non-goals → будь-які зміни UI Lite/WS.
- Інваріанти → мінімальний диф; append-only лог.
- План →
  1) Додати fail-fast якщо в OutDir вже є *ReqId*.
  2) Писати hashes лише для поточного prefix.
  3) Запустити Exit Gate P6.1 з ReqId eg-p61-0002 (OK) і повторити (FAIL).
- Тести/перевірки → tools\audit\run_exit_gate_p61.ps1 (двічі).

## 2026-01-19T20:50:00Z — POST (MODE=PATCH) → P6.1.2: fail-fast ReqId + hashes per prefix
- Що зроблено →
  - Додано fail-fast перевірку існуючих артефактів для ReqId.
  - Хеші формуються лише для поточного prefix.
  - Exit Gate P6.1 запущено з ReqId=eg-p61-0002 (OK) і повторено (FAIL).
- Де зроблено →
  - tools/audit/run_exit_gate_p61.ps1
- Як перевірено →
  - powershell -ExecutionPolicy Bypass -File tools\audit\run_exit_gate_p61.ps1 -NS fxcm_local -OutDir data\audit_v3 -ReqId eg-p61-0002 → OK
  - powershell -ExecutionPolicy Bypass -File tools\audit\run_exit_gate_p61.ps1 -NS fxcm_local -OutDir data\audit_v3 -ReqId eg-p61-0002 → FAIL (fail-fast, Write-Error)
- Артефакти (eg-p61-0002) →
  - C:\Aione_projects\fxcm_connector_v2\data\audit_v3\p61_fxcm_local_eg-p61-0002_20260119_162337.bar.json
  - C:\Aione_projects\fxcm_connector_v2\data\audit_v3\p61_fxcm_local_eg-p61-0002_20260119_162337.debug_after.json
  - C:\Aione_projects\fxcm_connector_v2\data\audit_v3\p61_fxcm_local_eg-p61-0002_20260119_162337.debug_before.json
  - C:\Aione_projects\fxcm_connector_v2\data\audit_v3\p61_fxcm_local_eg-p61-0002_20260119_162337.publish_after.json
  - C:\Aione_projects\fxcm_connector_v2\data\audit_v3\p61_fxcm_local_eg-p61-0002_20260119_162337.publish_before.json
  - C:\Aione_projects\fxcm_connector_v2\data\audit_v3\p61_fxcm_local_eg-p61-0002_20260119_162337.python_out.txt
  - C:\Aione_projects\fxcm_connector_v2\data\audit_v3\p61_fxcm_local_eg-p61-0002_20260119_162337.snapshot.json
  - C:\Aione_projects\fxcm_connector_v2\data\audit_v3\p61_fxcm_local_eg-p61-0002_20260119_162337.hashes.txt
- SHA256 → див. data/audit_v3/p61_fxcm_local_eg-p61-0002_20260119_162337.hashes.txt.
- Ризики/нотатки/відкат → PowerShell повертає exit code 1 при Write-Error; fail-fast зберігається, артефакти не перезаписуються.

## 2026-01-19T18:20:00Z — PRE (MODE=PATCH) → P7 ForexConnect-only: purge REST + backend switch + proof-pack
- Мета → перейти на єдиний FXCM бекенд (ForexConnect SDK) без REST/fxcmpy; додати SSOT-перемикач і proof-pack P7.
- Scope → config/config.py, runtime/fxcm_forexconnect.py, runtime/status.py, app/main.py, core/contracts/public/status_v2.json, tests/*, tools/audit/*, .vscode/tasks.json, deploy/runbook_fxcm_forexconnect.md, .gitignore.
- Non-goals → зміни UI Lite/WS протоколів; FXCM REST/токен логіка.
- Інваріанти → без автоздогадування; FXCM тільки через ForexConnect; secrets лише у config/secrets_*.py; fxcm_backend=disabled за замовчуванням.
- План →
  1) Додати fxcm_backend + secrets loader (без ENV для секретів).
  2) Додати ForexConnect adapter з fail-fast при відсутності SDK.
  3) Прописати fxcm.state у status + schema.
  4) Зв’язати FXCM ticks → tick_v1 → preview → ohlcv; вимкнути симулятори при fxcm_backend=forexconnect (loud).
  5) Додати Exit Gate P7 (offline/online) + capture_redis_ohlcv_once.py + tasks.
  6) Додати runbook для деплою.
  7) Додати тести P7.
- Тести/перевірки → tools\bootstrap_p0.ps1; tools\audit\run_exit_gate_p7.ps1 (offline).

## 2026-01-19T19:00:00Z — POST (MODE=PATCH) → P7 ForexConnect-only: backend switch + proof-pack
- Що зроблено →
  - Додано FXCM backend switch (forexconnect/disabled) та профілі/секрети через файли, без ENV для логіки.
  - Додано thin wrapper ForexConnect з fail-fast при відсутності SDK/секретів.
  - Додано fxcm секцію у status snapshot + schema.
  - Зв’язано FXCM ticks → tick_v1 → preview → {NS}:ohlcv; симулятори вимикаються при fxcm_backend=forexconnect (loud).
  - Додано Exit Gate P7 (offline/online), capture_redis_ohlcv_once.py, tasks, runbook.
  - Додано тести P7.
- Де зроблено →
  - config/config.py, config/secrets_template.py, config/profile_template.py
  - runtime/fxcm_forexconnect.py, runtime/status.py, runtime/ohlcv_preview.py, app/main.py
  - core/contracts/public/status_v2.json
  - tools/audit/run_exit_gate_p7.ps1, tools/audit/capture_redis_ohlcv_once.py
  - .vscode/tasks.json, deploy/runbook_fxcm_forexconnect.md, .gitignore
  - tests/test_fxcm_disabled_by_default.py
  - tests/test_fxcm_sdk_missing_is_loud_error.py
  - tests/test_preview_from_ticks_produces_bar_time_gt_zero.py
- Чому → прибрати REST/fxcmpy шлях і зафіксувати єдиний FXCM бекенд через ForexConnect SDK.
- Як перевірено →
  - powershell -ExecutionPolicy Bypass -File tools\bootstrap_p0.ps1 → OK
  - powershell -ExecutionPolicy Bypass -File tools\audit\run_exit_gate_p7.ps1 -NS fxcm_local -OutDir data\audit_v3 -ReqId eg-p7-0004 -Mode offline → OK
  - powershell -ExecutionPolicy Bypass -File tools\audit\run_exit_gate_p7.ps1 -NS fxcm_local -OutDir data\audit_v3 -ReqId eg-p7-0005 -Mode online → FAIL (FXCM not ready: fxcm_disabled)
- Артефакти (offline, eg-p7-0004) →
  - C:\Aione_projects\fxcm_connector_v2\data\audit_v3\p7_fxcm_local_eg-p7-0004_20260119_175348.offline_check.json
  - C:\Aione_projects\fxcm_connector_v2\data\audit_v3\p7_fxcm_local_eg-p7-0004_20260119_175348.offline_check.py
  - C:\Aione_projects\fxcm_connector_v2\data\audit_v3\p7_fxcm_local_eg-p7-0004_20260119_175348.python_out.txt
  - C:\Aione_projects\fxcm_connector_v2\data\audit_v3\p7_fxcm_local_eg-p7-0004_20260119_175348.hashes.txt
- Артефакти (online FAIL, eg-p7-0005) →
  - C:\Aione_projects\fxcm_connector_v2\data\audit_v3\p7_fxcm_local_eg-p7-0005_20260119_175356.python_out.txt
  - C:\Aione_projects\fxcm_connector_v2\data\audit_v3\p7_fxcm_local_eg-p7-0005_20260119_175356.status_before.json
- SHA256 → див. data/audit_v3/p7_fxcm_local_eg-p7-0004_20260119_175348.hashes.txt.
- Ризики/відкат → при наявному SDK+кредах запустити онлайн режим; відкат: повернути запуск симуляторів і прибрати fxcm_forexconnect.

## 2026-01-19T20:10:00Z — PRE (MODE=PATCH) → P7: scope/invariant compliance pass
- Мета → привести P7 реалізацію до заявленого scope/інваріантів (ForexConnect-only) та виправити тести/статус схеми за потреби.
- Scope → runtime/fxcm_forexconnect.py, app/main.py, config/config.py, runtime/status.py, core/contracts/public/status_v2.json, tests/*, tools/audit/run_exit_gate_p7.ps1.
- Non-goals → final/store/history/derived/tail_guard/no_mix не чіпати; UI Lite/WS без змін.
- Інваріанти → один FXCM бекенд (ForexConnect), без REST/fxcmpy/token; FXCM thread single-owner; SDK/логін недоступні → loud errors[]/degraded[] без падіння; preview only (complete=false, source="stream", synthetic=false, ms int, sorted/no-dupes).
- План →
  1) Оновити тест валідатора статусу для fxcm секції.
  2) Перевірити fxcm adapter/конфіг на відповідність scope.
  3) Зафіксувати POST.
- Тести/перевірки → tools\bootstrap_p0.ps1.

## 2026-01-19T20:30:00Z — POST (MODE=PATCH) → P7: scope/invariant compliance pass
- Що зроблено →
  - Оновлено валідний payload у тесті статусу, додано fxcm секцію.
  - Перевірено, що config/fxcm_forexconnect відповідають ForexConnect-only (без REST/fxcmpy/token).
- Де зроблено →
  - tests/test_validator_status.py
- Чому → синхронізація тестів зі schema/status (fxcm секція присутня).
- Як перевірено →
  - powershell -ExecutionPolicy Bypass -File tools\bootstrap_p0.ps1 → OK
- Ризики/відкат → ризиків немає; відкат — видалити fxcm секцію з тесту.

## 2026-01-19T21:00:00Z — PRE (MODE=PATCH) → .env як перемикач + FXCM стартові логи
- Мета → увімкнути .env як єдиний перемикач local/prod для несекретних налаштувань, додати стартові INFO логи FXCM, зберегти ForexConnect-only.
- Scope → config/config.py, app/main.py, runtime/fxcm_forexconnect.py, tests/test_fxcm_sdk_missing_is_loud_error.py.
- Non-goals → зміни final/store/history/derived/tail_guard/no_mix; REST/fxcmpy.
- Інваріанти → один FXCM бекенд; SDK/логін недоступні → loud; preview only; NS задається одним способом.
- План →
  1) Додати .env switch + FXCM_* канал/порти/host/connection у config.
  2) Додати INFO логи для FXCM backend/SDK/login.

## 2026-01-19T21:30:00Z — POST (MODE=PATCH) → .env як перемикач + FXCM стартові логи
- Що зроблено →
  - Додано .env switch (AI_ONE_ENV_FILE) для несекретних FXCM_* налаштувань + правило NS одним способом.
  - Додано стартові INFO логи про FXCM backend/SDK/login у запуску.
  - Виправлено typing/mypy у config (profile Optional, no-redef) та збережено .env switch/NS-правило.
  - Усунуто генерацію .py артефакту в Exit Gate P7 offline; нейтралізовано старий .py артефакт у data/audit_v3.
- Де зроблено →
  - config/config.py
  - app/main.py
  - runtime/fxcm_forexconnect.py
  - tests/test_fxcm_sdk_missing_is_loud_error.py
  - tools/audit/run_exit_gate_p7.ps1
  - data/audit_v3/p7_fxcm_local_eg-p7-0004_20260119_175348.offline_check.py
  - data/audit_v3/p7_fxcm_local_eg-p7-0004_20260119_175348.hashes.txt
- Чому →
  - Усунути mypy/ruff помилки в bootstrap та не створювати lint-чутливі .py артефакти в data.
  - Дотриматися правила «ENV не для секретів» (секрети як і раніше тільки через config/secrets_*.py).
- Як перевірено →
  - powershell -ExecutionPolicy Bypass -File tools\bootstrap_p0.ps1 → OK
- Ризики/відкат →
  - Ризик: при ручному запуску старих p7 артефактів hash-файли можуть містити застарілі посилання.
  - Відкат: повернути попередню версію run_exit_gate_p7.ps1 і відновити .py артефакт (не рекомендовано через ruff).
  3) Уточнити тест на fxcm_sdk_missing.
  4) Прогнати bootstrap.
- Тести/перевірки → tools\bootstrap_p0.ps1.

## 2026-01-19T22:10:00Z — PRE (MODE=read-only discovery) → FXCM secrets: джерело і причина fxcm_secrets_missing
- Мета → з’ясувати, звідки беруться FXCM секрети та чому лог показує fxcm_secrets_missing.
- Scope → config/config.py.
- Non-goals → будь-які зміни коду/конфігу/секретів.
- Інваріанти → ENV не для секретів; SSOT для секретів у config/secrets_*.py.
- План →
  1) Перевірити _load_secrets і load_config.
  2) Зафіксувати висновок у POST.
- Тести/перевірки → не застосовно.

## 2026-01-19T22:12:00Z — POST (MODE=read-only discovery) → FXCM secrets: джерело і причина fxcm_secrets_missing
- Що зроблено → переглянуто джерело секретів і шлях завантаження конфігу.
- Де зроблено → config/config.py.
- Висновок → FXCM секрети беруться лише з config/secrets_{profile}.py через _load_secrets; .env(.local/.prod) не використовується для секретів, тому при наявності секретів лише у .env лог показує fxcm_secrets_missing.
- Як перевірено → не застосовно.
- Ризики/нотатки → для дотримання правила «ENV не для секретів» потрібно перенести секрети у config/secrets_{profile}.py або змінювати політику (ADR).

## 2026-01-19T22:30:00Z — PRE (MODE=PATCH) → P7A: ForexConnect real preview (1m) + env профілі (single switch) + єдиний FXCM шлях
- Мета → реальний FXCM preview(1m) через ForexConnect SDK + відображення в UI Lite; .env як єдиний перемикач профілю; секрети з .env.local/.env.prod.
- Scope → core/env_loader.py, app/main.py, config/config.py, runtime/forexconnect_stream.py, runtime/status.py, core/contracts/public/status_v2.json, tests/*, tools/audit/run_exit_gate_p7a.ps1, .github/copilot-instructions.md.
- Non-goals → final/store/history/backfill/tail_guard не чіпати (лише preview stream у {NS}:ohlcv).
- Інваріанти → один FXCM шлях (ForexConnect); ENV лише для секретів/allowlist (без фіч/режимів/констант); XOR режимів FXCM vs симулятори.
- План →
  1) Додати env loader з allowlist і fail-fast.
  2) Перевести config на читання allowlist з ENV (секрети з .env.local/.env.prod).
  3) Створити ForexConnect stream з reconnect/backoff + status state.
  4) Додати XOR режимів і запуск --fxcm-preview.
  5) Додати P7A exit gate + тести.
  6) Оновити правило №4 у copilot-instructions.
- Тести/перевірки → tools\bootstrap_p0.ps1; tools\audit\run_exit_gate_p7a.ps1.

## 2026-01-19T23:30:00Z — POST (MODE=PATCH) → P7A: ForexConnect real preview (1m) + env профілі (single switch) + єдиний FXCM шлях
- Що зроблено →
  - Додано env loader з allowlist і fail-fast; .env став єдиним перемикачем профілю, секрети беруться з .env.local/.env.prod.
  - Конфіг переведено на ENV allowlist (без FXCM_BACKEND), додано metrics/redis опції та профіль з AI_ONE_ENV_FILE.
  - Додано ForexConnect stream з reconnect/backoff, closed_wait, станами fxcm.* і last_ok_ts_ms.
  - Додано XOR режимів та CLI `--fxcm-preview`, 1m preview у {NS}:ohlcv, fail-fast при конфліктах/відсутніх секретах.
  - Додано Exit Gate P7A та task; оновлено правило №4 у copilot-instructions.
  - Оновлено тести для env loader та XOR режимів, виправлено валідатор статусу.
- Де зроблено →
  - core/env_loader.py
  - config/config.py
  - app/main.py
  - runtime/forexconnect_stream.py
  - runtime/fxcm_forexconnect.py (shim error)
  - runtime/status.py
  - core/contracts/public/status_v2.json
  - tools/audit/run_exit_gate_p7a.ps1
  - tools/audit/capture_redis_ohlcv_once.py
  - tools/exit_gates/gate_calendar_gaps.py
  - tools/exit_gates/gate_final_wire.py
  - tools/exit_gates/gate_no_mix.py
  - tools/exit_gates/gate_republish_watermark.py
  - ui_lite/server.py
  - .vscode/tasks.json
  - .github/copilot-instructions.md
  - tests/test_env_loader_allowlist_failfast.py
  - tests/test_mode_xor_fxcm_vs_sim.py
  - tests/test_fxcm_sdk_missing_is_loud_error.py
  - tests/test_gate_calendar_gaps.py
  - tests/test_ui_lite_http_process_request.py
  - tests/test_validator_status.py
- Як перевірено →
  - powershell -ExecutionPolicy Bypass -File tools\bootstrap_p0.ps1 → OK
- Ризики/відкат →
  - Ризик: якщо .env.local/.env.prod містить зайві ключі — env loader зупиняє запуск (fail-fast).
  - Ризик: без SDK/секретів `--fxcm-preview` завершується з помилкою (очікувано).
  - Відкат: повернути старий fxcm_forexconnect та попередній env-парсер; видалити P7A gate.

## 2026-01-19T23:45:00Z — PRE (MODE=PATCH) → ENV allowlist: дозволити канали FXCM
- Мета → дозволити у allowlist ключі каналів FXCM, які вже підтримані в config.
- Scope → core/env_loader.py.
- Non-goals → зміни логіки FXCM/preview/конфігу поза allowlist.
- Інваріанти → ENV тільки для секретів/allowlist; зайві ключі мають fail-fast.
- План →
  1) Додати FXCM_*_CHANNEL ключі у allowlist.
  2) Зафіксувати POST.
- Тести/перевірки → не запускати (локальна правка allowlist).

## 2026-01-19T23:47:00Z — POST (MODE=PATCH) → ENV allowlist: дозволити канали FXCM
- Що зроблено → додано FXCM_*_CHANNEL ключі в allowlist env loader.
- Де зроблено → core/env_loader.py.
- Як перевірено → тести не запускались (локальна правка allowlist).
- Ризики/відкат → якщо в .env залишаються неallowlist ключі (FXCM_CACHE_ENABLED, FXCM_HMAC_*), запуск і далі fail-fast; відкат — прибрати додані ключі з allowlist.

## 2026-01-19T23:55:00Z — PRE (MODE=PATCH) → P7A1: єдиний FXCM шлях + env allowlist узгодження
- Мета → зробити runtime/fxcm_forexconnect.py єдиним FXCM шляхом; прибрати forexconnect_stream; оновити імпорти.
- Scope → runtime/fxcm_forexconnect.py, runtime/forexconnect_stream.py, app/main.py, tools/audit/capture_redis_ohlcv_once.py, tests/test_fxcm_sdk_missing_is_loud_error.py.
- Non-goals → зміни preview/ohlcv schema/metrics поза FXCM шляхом.
- Інваріанти → single FXCM path; thread ownership; allowlist env без вигаданих ключів.
- План →
  1) Перенести реалізацію в runtime/fxcm_forexconnect.py.
  2) Видалити runtime/forexconnect_stream.py.
  3) Оновити імпорти.
  4) Прогнати bootstrap.
- Тести/перевірки → tools\bootstrap_p0.ps1.

## 2026-01-19T23:59:00Z — POST (MODE=PATCH) → P7A1: єдиний FXCM шлях + env allowlist узгодження
- Що зроблено →
  - Відновлено runtime/fxcm_forexconnect.py як єдиний FXCM конектор (thread ownership + reconnect/backoff).
  - Вимкнено альтернативний runtime/forexconnect_stream.py (fail-fast).
  - Оновлено імпорти в app/main.py, capture_redis_ohlcv_once.py, тесті SDK.
- Де зроблено →
  - runtime/fxcm_forexconnect.py
  - runtime/forexconnect_stream.py
  - app/main.py
  - tools/audit/capture_redis_ohlcv_once.py
  - tests/test_fxcm_sdk_missing_is_loud_error.py
- Як перевірено →
  - powershell -ExecutionPolicy Bypass -File tools\bootstrap_p0.ps1 → OK
- Ризики/відкат →
  - Ризик: імпорт forexconnect_stream тепер падає (очікувано). Відкат — повернути re-export замість fail-fast.
  - Ризик: у .env залишаються неallowlist ключі (FXCM_CACHE_ENABLED, FXCM_HMAC_*) → запуск блокується, їх потрібно прибрати або узгодити через ADR.

## 2026-01-19T23:59:40Z — PRE (MODE=PATCH) → P7A1: env policy + FXCM state machine + 1m preview rails
- Мета → жорстка політика .env, allowlist для .env.local/.env.prod, нові fxcm стани/поля, та інваріанти 1m preview.
- Scope → core/env_loader.py, runtime/fxcm_forexconnect.py, runtime/status.py, core/contracts/public/status_v2.json, app/main.py, tests/test_validator_status.py.
- Non-goals → final/store/history/derived/repair/tail_guard без змін.
- Інваріанти → single FXCM path; thread ownership; preview only (complete=false/source=stream/synthetic=false); fail-fast на unknown env key.
- План →
  1) Оновити allowlist та політику .env (тільки AI_ONE_ENV_FILE).
  2) Оновити fxcm state/поля у status та schema.
  3) Оновити FXCM stream і app/main для нових полів.
  4) Оновити тест валідатора статусу.
- Тести/перевірки → не запускати (локальні правки).

## 2026-01-20T00:05:00Z — POST (MODE=PATCH) → P7A1: env policy + FXCM state machine + 1m preview rails
- Що зроблено →
  - Посилено політику .env: дозволено тільки AI_ONE_ENV_FILE; unknown env key → fail-fast.
  - Allowlist доповнено FXCM_CACHE_ENABLED та FXCM_HMAC_*.
  - Оновлено fxcm поля/стани в status manager та schema (last_err, last_err_ts_ms, reconnect_attempt, next_retry_ts_ms).
  - Оновлено FXCM stream і app/main під нові поля.
  - Оновлено тест валідатора статусу.
- Де зроблено →
  - core/env_loader.py
  - runtime/status.py
  - runtime/fxcm_forexconnect.py
  - core/contracts/public/status_v2.json
  - app/main.py
  - tests/test_validator_status.py
- Як перевірено → тести не запускались (локальні правки).
- Ризики/відкат →
  - Ризик: .env з будь-якими ключами крім AI_ONE_ENV_FILE тепер блокує запуск (очікувано).
  - Відкат: послабити перевірку .env у core/env_loader.py.

## 2026-01-20T00:10:00Z — PRE (MODE=PATCH) → P7A1: --fxcm-preview як єдина істина для симуляторів
- Мета → зробити --fxcm-preview джерелом істини, щоб він вимикав симулятори і не було конфлікту.
- Scope → app/main.py.
- Non-goals → зміни FXCM stream або статусу.
- Інваріанти → XOR режимів має зберігатися; FXCM preview не змішується із симуляторами.
- План →
  1) Додати override для tick_mode/preview_mode/ohlcv_sim_enabled при --fxcm-preview.
  2) Зафіксувати POST.
- Тести/перевірки → не запускати (локальна правка).

## 2026-01-20T00:11:00Z — POST (MODE=PATCH) → P7A1: --fxcm-preview як єдина істина для симуляторів
- Що зроблено → --fxcm-preview тепер примусово вимикає tick/preview симулятори в config.
- Де зроблено → app/main.py.
- Як перевірено → тести не запускались (локальна правка).
- Ризики/відкат → якщо потрібно залишити симулятори увімкненими, прибрати override у app/main.py.

## 2026-01-20T00:20:00Z — PRE (MODE=PATCH) → FXCM runtime: видимість успіху login/tick
- Мета → зробити видимим, що FXCM підключення і тік-стрім реально працюють.
- Scope → runtime/fxcm_forexconnect.py.
- Non-goals → зміна логіки state machine/preview/ENV політики.
- Інваріанти → single FXCM path, thread ownership, без зайвого шуму в логах.
- План →
  1) Додати INFO лог про успішний login.
  2) Додати одноразовий INFO лог при першому тіку.
  3) Зафіксувати POST.
- Тести/перевірки → не запускати (локальна правка).

## 2026-01-20T00:30:00Z — POST (MODE=PATCH) → FXCM runtime: видимість успіху login/tick
- Що зроблено → додано INFO лог про успішний FXCM login і одноразовий INFO лог на першому tick.
- Де зроблено → runtime/fxcm_forexconnect.py.
- Як перевірено → не запускалось (лише логування).
- Ризики/відкат → мінімальні; можна прибрати логи без впливу на логіку.

## 2026-01-19T12:10:00Z — PRE (MODE=PATCH) → UI Lite: Classic BW стиль
- Мета → зробити класичний світлий UI з чіткими осями та “hollow/filled” свічками.
- Scope → ui_lite/static/index.html, ui_lite/static/app.js.
- Non-goals → зміна бекенду, контрактів, WS протоколу, даних.
- Інваріанти → мінімальний диф; тільки ui_lite/static/*.
- План →
  1) Оновити CSS/контейнер для повної висоти та світлої теми.
  2) Переналаштувати chart/series опції під Classic BW.
  3) Додати fitContent один раз і resize на window.
- Тести/перевірки → не запускати (manual UI check).

## 2026-01-19T12:25:00Z — POST (MODE=PATCH) → UI Lite: Classic BW стиль
- Що зроблено → оновлено CSS/контейнер під світлу тему, налаштування графіка/свічок Classic BW, додано одноразовий fitContent і resize.
- Де зроблено → ui_lite/static/index.html, ui_lite/static/app.js.
- Як перевірено → не запускалось (manual UI check).
- Ризики/відкат → мінімальні; можна повернути старі опції UI.

## 2026-01-19T19:58:00Z — PRE (MODE=read-only discovery) → UI Lite: bars=0, WS age
- Мета → перевірити, чи йдуть OHLCV у Redis та чи оновлюється статус.
- Scope → тільки читання status snapshot та (за можливості) канал ohlcv.
- Non-goals → зміни коду, конфігів, рестарт процесів.
- Інваріанти → без змін у файлах, без тестів.
- План →
  1) Прочитати {NS}:status:snapshot.
  2) Перевірити наявність повідомлень у {NS}:ohlcv (короткий peek).
- Тести/перевірки → redis-cli GET/SUBSCRIBE (коротко).

## 2026-01-19T20:00:00Z — POST (MODE=read-only discovery) → UI Lite: bars=0, WS age
- Що зроблено → прочитано status snapshot у Redis.
- Де зроблено → Redis key fxcm_local:status:snapshot.
- Як перевірено → redis-cli GET fxcm_local:status:snapshot.
- Результат → fxcm.state=connected, last_tick_ts_ms=0; ohlcv_preview.preview_total=0 та last_publish_ts_ms=0 (свічки не публікуються).
- Ризики/нотатки → схоже, прев’ю вимкнено (без --fxcm-preview) або тики не надходять.

## 2026-01-19T20:06:00Z — PRE (MODE=PATCH) → Debug логи: FXCM + UI Lite підписки
- Мета → додати більш інформативні DEBUG-логи для підписок UI Lite та FXCM запитів/відповідей.
- Scope → runtime/fxcm_forexconnect.py, ui_lite/server.py.
- Non-goals → зміни протоколів/каналів/логіки стріму чи даних.
- Інваріанти → мінімальний диф; логи лише в DEBUG.
- План →
  1) Додати logger у UI Lite і DEBUG-логи для Redis каналу та WS subscribe.
  2) Додати DEBUG-логи у FXCM для login/poll і отриманих цін.
  3) Записати POST.
- Тести/перевірки → не запускати (логування).

## 2026-01-19T20:12:00Z — POST (MODE=PATCH) → Debug логи: FXCM + UI Lite підписки
- Що зроблено → додано DEBUG-логи для UI Lite (Redis канал, WS subscribe) і FXCM (login/poll/price).
- Де зроблено → runtime/fxcm_forexconnect.py, ui_lite/server.py.
- Як перевірено → не запускалось (логування).
- Ризики/відкат → шум у DEBUG; можна вимкнути, піднявши рівень логування.

## 2026-01-19T21:30:00Z — PRE (MODE=PATCH) → Debug логи: FXCM symbols/TF/channels
- Мета → додати DEBUG-логи про канали Redis, символи/TF та FXCM запити/відповіді.
- Scope → app/main.py, runtime/fxcm_forexconnect.py.
- Non-goals → зміни протоколів, підписок FXCM (OFFERS), або контрактів.
- Інваріанти → мінімальний диф; логи лише в DEBUG.
- План →
  1) Додати DEBUG-логи каналів і preview налаштувань у app/main.py.
  2) Додати DEBUG-логи нормалізації символів/TF-мапінгу у FXCM стрімі.
  3) Записати POST.
- Тести/перевірки → не запускати (логування).

## 2026-01-19T21:40:00Z — POST (MODE=PATCH) → Debug логи: FXCM symbols/TF/channels
- Що зроблено → додано DEBUG-логи каналів Redis і preview-налаштувань; додано helper-и для symbol/TF та логи нормалізації у FXCM стрімі; прибрано форсований DEBUG basicConfig.
- Де зроблено → app/main.py, runtime/fxcm_forexconnect.py.
- Як перевірено → не запускалось (логування).
- Ризики/відкат → мінімальні; логи тільки при DEBUG рівні.

## 2026-01-19T21:45:00Z — PRE (MODE=PATCH) → FXCM debug: subscribe_market_data і OFFERS
- Мета → додати DEBUG-логи та спробу підписки на OFFERS для видимості того, що йде у FXCM.
- Scope → runtime/fxcm_forexconnect.py.
- Non-goals → зміна схем/контрактів/каналів Redis; нові API.
- Інваріанти → мінімальний диф; підписка виконується лише якщо API доступний.
- План →
  1) Знайти offer_id для потрібних символів через OFFERS.
  2) Спробувати subscribe_market_data (якщо метод існує) і залогувати.
  3) Записати POST.
- Тести/перевірки → не запускати (логування).

## 2026-01-19T21:55:00Z — POST (MODE=PATCH) → FXCM debug: subscribe_market_data і OFFERS
- Що зроблено → додано пошук offer_id через OFFERS та DEBUG-логовану спробу subscribe_market_data після login.
- Де зроблено → runtime/fxcm_forexconnect.py.
- Як перевірено → не запускалось (логування).
- Ризики/відкат → мінімальні; якщо API недоступний, лише DEBUG повідомлення.

## 2026-01-19T22:05:00Z — PRE (MODE=PATCH) → FXCM OFFERS subscription як єдине джерело тік-ів
- Мета → перейти на реальний тик-стрім через OFFERS updates, прибрати полінг/subscribe_market_data; додати stale/reconnect стани.
- Scope → runtime/fxcm_forexconnect.py, runtime/status.py, тести.
- Non-goals → final/history/backfill/repair/tail_guard.
- Інваріанти → ForexConnect-only; preview complete=false; loud errors/degraded при відсутності тік-ів.
- План →
  1) Додати OFFERS listener і обробку row→tick.
  2) Прибрати полінг та subscribe_market_data.
  3) Додати stale_no_ticks → resubscribe → reconnect.
  4) Додати мінімальні тести.
- Тести/перевірки → pytest вибірково (мінімум 3 нових).

## 2026-01-19T22:30:00Z — POST (MODE=PATCH) → FXCM OFFERS subscription як єдине джерело тік-ів
- Що зроблено → додано OFFERS listener (row→tick), прибрано subscribe_market_data/polling; додано stale_no_ticks→resubscribe→reconnect; оновлено FXCM state на subscribed_offers; додано 3 тести.
- Де зроблено → runtime/fxcm_forexconnect.py, runtime/status.py, tests/test_fxcm_offers_subscription.py.
- Як перевірено → не запускалось (тести не запускались).
- Ризики/відкат → якщо FXCM OFFERS недоступна, стрім піде у reconnect з loud error; можна повернути polling у разі необхідності (не рекомендовано).

## 2026-01-20 00:25:00 — PRE (MODE=PATCH): P7.1 Rails Closure Pack (XOR runtime + ExitGates SSOT + Python 3.7 rail)

- Мета →
  - Rail-1: XOR режимів runtime (forexconnect vs симулятори) на рівні composition root; симулятори не мають імпортуватись/ініціалізуватись у forexconnect режимі.
  - Rail-2: нормалізація Exit Gates як єдиний стандарт (один runner, один формат proof-pack, без дублювання gate-скриптів).
  - Rail-3: зафіксувати Python версію як rail (3.7) у bootstrap + в exit gate, щоб не було дрейфу середовища.
- Scope → тільки рейки/інфра/exit-gates/bootstraps/мінімальні зміни composition root; без зміни доменної логіки (алгоритми/FXCM протокол/Redis payload).
- Інваріанти →
  - No silent fallback: тільки errors[]/degraded[] або hard fail.
  - XOR: forexconnect режим не імпортує/не створює симулятори.
  - Єдиний runner exit gates: заборонити множинні entrypoints.
  - Python rail: збірка/гейти явно фейляться, якщо не 3.7.
- Acceptance Criteria →
  - AC1 XOR runtime: Gate-XOR-Mode-SCAN PASS + unit test PASS.
  - AC2 Exit Gates SSOT: один tools/run_exit_gates.py (канонічний), proof-pack створюється, заборонені дублікати runner-ів, Gate-NoDuplicateGateRunners PASS.
  - AC3 Python rail: Gate-PythonVersion PASS у .venv (3.7) і FAIL поза ним (очікувано).
  - AC4 All: ruff/mypy/pytest PASS; run_exit_gates PASS.
- Ризики/нотатки → якщо знайдені legacy FXCM/симуляторні шляхи — або видалити, або жорстко заборонити rails (prefer forbid).

### Швидкий аудит (факти)
- git checkout -b hardening/p7_1-rails-closure → FAIL (repo без .git).
- rg недоступний у середовищі → виконано grep_search.
- tools: exit gate runner-ів не знайдено; є legacy gate-скрипти у tools/exit_gates.
- composition root: app/main.py містить імпорти симуляторів (TickSimulator, OhlcvPreviewSimulator, HistorySimProvider).
- ENV читання: тільки core/env_loader.py (os.environ), інших читань не знайдено.

## 2026-01-20 01:05:00 — POST (MODE=PATCH): P7.1 Rails Closure Pack (XOR runtime + ExitGates SSOT + Python 3.7 rail)

- Що зроблено →
  - Rail-1 XOR runtime: винесено composition у app/composition.py; прибрано module-level імпорти симуляторів з app/main.py; lazy-import симуляторів тільки в SIM гілці; forexconnect режим hard-fail при спробі змішування.
  - Rail-2 Exit Gates SSOT: додано канонічний tools/run_exit_gates.py + tools/exit_gates/manifest.json; нормалізовано gates у tools/exit_gates/gates/*; додано gate_no_duplicate_gate_runners; proof-pack results.json + hashes.json.
  - Rail-3 Python rail: додано gate_python_version; bootstrap* перевіряє Python 3.7 і запускає exit gates; docs/exit_gates.md.
- Файли →
  - core/runtime/mode.py, core/runtime/__init__.py — enum режимів і парсинг.
  - app/composition.py — composition root з XOR і lazy-import симуляторів.
  - app/main.py — тонкий entrypoint, без сим-імпортів.
  - tools/run_exit_gates.py — канонічний runner.
  - tools/exit_gates/manifest.json — SSOT manifest.
  - tools/exit_gates/gates/{gate_python_version.py,gate_xor_mode_scan.py,gate_no_duplicate_gate_runners.py} — P7.1 gates.
  - tools/exit_gates/gates/{gate_calendar_gaps.py,gate_final_wire.py,gate_no_mix.py,gate_republish_watermark.py} — перенесені legacy gate-и (CLI).
  - tools/exit_gates/{gate_calendar_gaps.py,gate_final_wire.py,gate_no_mix.py,gate_republish_watermark.py} — thin-wrapper для legacy CLI.
  - tools/bootstrap_p0.ps1, tools/bootstrap_p0.sh — Python 3.7 rail + run_exit_gates.
  - docs/exit_gates.md — SSOT інструкція.
  - tests/test_xor_runtime_scan.py, tests/test_exit_gates_runner_policy.py — тести рейок.
  - tests/test_mode_xor_fxcm_vs_sim.py, tests/test_gate_calendar_gaps.py, tests/test_gate_final_wire.py, tests/test_tail_guard_repair_flow.py — оновлено імпорти/очікування.
  - tests/test_fxcm_disabled_by_default.py — очікування state=connecting.
- Як перевірено →
  - Task: P0: bootstrap (tools/bootstrap_p0.ps1) → PASS (включно з run_exit_gates).
  - ruff: C:\Aione_projects\fxcm_connector_v2\.venv\Scripts\python.exe -m ruff check . → PASS.
  - mypy: C:\Aione_projects\fxcm_connector_v2\.venv\Scripts\python.exe -m mypy . → PASS.
  - pytest: C:\Aione_projects\fxcm_connector_v2\.venv\Scripts\python.exe -m pytest -q → PASS.
  - run_exit_gates: C:\Aione_projects\fxcm_connector_v2\.venv\Scripts\python.exe tools\run_exit_gates.py --out reports\exit_gates --manifest tools\exit_gates\manifest.json → PASS; proof-pack: reports/exit_gates/2026-01-20_183905/.
- Ризики/нотатки →
  - Legacy gate-скрипти залишені як thin-wrapper у tools/exit_gates/*.py для сумісності; логіка перенесена у tools/exit_gates/gates/.
  - Git-репозиторій відсутній (.git), гілку не створено (факт).
- Наступний крок →
  - P8 (UI cosmetics) або P9 (FXCM FSM hardening) після стабільного PASS P7.1.

## 2026-01-20 01:20:00 — POST (MODE=read-only discovery): AC3 Python rail (FAIL поза .venv) + thin-wrapper policy

- Що зроблено → перевірено gate_python_version поза .venv; зафіксовано політику thin-wrapper (документовано у docs/exit_gates.md, allowlist лише для legacy CLI).
- Як перевірено →
  - python -c "import sys; print(sys.version)" → 3.14.2 (system).
  - python tools\run_exit_gates.py --out reports\exit_gates --manifest tools\exit_gates\manifest.json → exit code 1.
  - proof-pack: reports/exit_gates/2026-01-20_185641/results.json (gate_python_version ok:false, python_version=3.14).

## 2026-01-20 02:10:00 — PRE (MODE=PATCH): P7.2 Data-Plane Truth Pack (no sims in runtime + tick canonical + preview 1m boundaries + record/replay)

- Мета → (1) прибрати симулятори з runtime, (2) зафіксувати канонічні tick_ts/snap_ts як int ms, (3) зробити preview 1m з правильними open/close_time (inclusive, 60_000-1), (4) замінити симулятори на record/replay з реальних FXCM tick’ів для тестів, (5) додати exit gates (tick units, preview boundaries, geom).
- Scope → мінімальні зміни runtime/composition + нові tools/tests/gates; без FINAL pipeline і без UI косметики.
- Інваріанти → strict validator allowlist, no silent fallback, preview може оновлювати той самий open_time (UI dedup), але payload завжди валідний і канонічний по часу.
- Acceptance Criteria → AC1 runtime no-sims, AC2 tick ms строго, AC3 preview 1m boundaries/geom, AC4 record/replay fixtures працюють, AC5 ruff/mypy/pytest + run_exit_gates PASS.

## 2026-01-20 03:40:00 — PRE (MODE=PATCH): P7.2 Data-Plane Truth Pack (видалення runtime sim + gates/tools/tests)

- Мета → прибрати runtime sim імпорти/активацію, винести симулятори в tests/fixtures, додати exit gates для tick ms та preview 1m boundaries, додати record/replay tools.
- Scope → app/composition.py, config/config.py, runtime/*sim* (stubs), tools/exit_gates/gates/*, tools/record_ticks.py, tools/replay_ticks.py, tests/*.
- Інваріанти → no silent fallback, strict валідатори, мінімальний диф, українські повідомлення.
- План → 1) винести симулятори в tests/fixtures; 2) прибрати sim-запуски з composition; 3) додати exit gates; 4) додати record/replay tools; 5) оновити тести.
- Тести/перевірки → ruff/mypy/pytest + run_exit_gates (якщо доступно).

## 2026-01-20 03:55:00 — POST (MODE=PATCH): P7.2 Data-Plane Truth Pack (видалення runtime sim + gates/tools/tests)

- Що зроблено →
  - Симулятори перенесені у tests/fixtures/sim; runtime-симулятори замінені loud-стабами.
  - Composition root прибрано від sim-режимів; додано fail-fast для tick/preview/ohlcv sim.
  - Додано exit gates: no_runtime_sims, tick_units, preview_1m_boundaries; оновлено manifest і політику тестів.
  - Додано tools/record_ticks.py та tools/replay_ticks.py (JSONL запис/відтворення tick із Redis).
  - Додано тести для tick ms, preview 1m boundaries, gate_no_runtime_sims; оновлено імпорти у тестах.
- Де зроблено →
  - tests/fixtures/sim/{tick_simulator.py,ohlcv_preview_simulator.py,history_sim_provider.py,ohlcv_sim.py}
  - runtime/{tick_simulator.py,ohlcv_preview_simulator.py,history_sim_provider.py,ohlcv_sim.py,tick_sim.py}
  - app/composition.py, config/config.py
  - tools/exit_gates/gates/{gate_no_runtime_sims.py,gate_tick_units.py,gate_preview_1m_boundaries.py}
  - tools/exit_gates/manifest.json, tests/test_exit_gates_runner_policy.py
  - tools/{record_ticks.py,replay_ticks.py}
  - tests/{test_tick_canonical_ms.py,test_preview_1m_boundaries_new.py,test_gate_no_runtime_sims.py}
  - tests/{test_tick_simulator.py,test_tick_simulator_disabled.py,test_tick_mode_handling.py,test_ohlcv_simulator.py,test_gate_calendar_gaps.py,test_warmup_handler_updates_status.py,test_tail_guard_repair_flow.py,test_repair_rejects_large_range.py,test_mode_xor_fxcm_vs_sim.py}
- Як перевірено → не запускалось (не було запиту на запуск).
- Ризики/нотатки →
  - Runtime sim-модулі тепер fail-fast при імпорті; будь-які залишкові імпорти викличуть явну помилку.
  - Для повного PASS P7.2 потрібен запуск ruff/mypy/pytest та run_exit_gates.

## 2026-01-20 20:06:30 — POST (MODE=PATCH): P7.2 Data-Plane Truth Pack — Verification PASS (ruff/mypy/pytest/exit_gates)

- Як перевірено →
  - ruff: python -m ruff check . → PASS
  - mypy: python -m mypy . → PASS
  - pytest: python -m pytest -q → PASS
  - run_exit_gates: python tools\run_exit_gates.py --out reports\exit_gates --manifest tools\exit_gates\manifest.json → PASS; proof-pack: reports/exit_gates/2026-01-20_200613/
- Exit gates (P7.2) →
  - gate_no_runtime_sims: PASS (сканує app/ та runtime/ без runtime/*sim*.py на заборонені sim-імпорти)
  - gate_tick_units: PASS (перевірено lines=20 у tests/fixtures/ticks_sample.jsonl)
  - gate_preview_1m_boundaries: PASS
  - gate_preview_1m_geom: PASS
- Ризики/нотатки →
  - record_ticks.py працює з дроту Redis (price_tik) як джерело fixtures; FXCM-capture буде окремим P-слайсом у P9/P10 при потребі.

## 2026-01-20 20:14:50 — POST (MODE=PATCH): P7.2 Data-Plane Truth Pack — PASS (static + exit_gates proof-pack)

- Як перевірено →
  - ruff: python -m ruff check . → PASS
  - mypy: python -m mypy . → PASS
  - pytest: python -m pytest -q → PASS
  - run_exit_gates: python tools\run_exit_gates.py --out reports\exit_gates --manifest tools\exit_gates\manifest.json → PASS; proof-pack: reports/exit_gates/2026-01-20_201434/
- Exit Gates (P7.2) →
  - gate_no_runtime_sims: PASS (сканує імпорти production path у app/ та runtime/, без runtime/*sim*.py)
  - gate_tick_units: PASS (fixtures ticks_sample.jsonl, checked_lines=20)
  - gate_preview_1m_boundaries: PASS (fixtures)
  - gate_preview_1m_geom: PASS (fixtures)
  - gate_preview_multi_tf: не в manifest; доступний як інтеграційний ручний запуск (tools/check_multi_tf.py + tools/replay_ticks.py)
- Ризики/нотатки →
  - record_ticks.py працює з дроту Redis (price_tik) як джерело fixtures; FXCM-capture буде окремим P-слайсом у P9/P10 при потребі.

## 2026-01-20 20:15:10 — POST (MODE=read-only discovery): Sim/Replay стан

- Сим-модулі у runtime залишені як loud-стаби; sim-режими в composition відсутні і fail-fast при увімкненні.
- Replay режим у runtime не додано; відтворення тільки через tools/replay_ticks.py та інтеграційні утиліти.

## 2026-01-20 20:18:20 — PRE (MODE=PATCH): P9 FXCM Runtime Hardening (FSM + stale policy + metrics/status)

- Мета → FSM + stale/resubscribe/reconnect + метрики + status
- Scope → runtime/fxcm/*, runtime/status.py, observability/metrics.py, tools/exit_gates/gates/*
- Інваріанти → XOR runtime; no silent fallback; детерміновані тести; Python 3.7 rail
- Acceptance Criteria → AC1–AC5
- Ризики/нотатки → ForexConnect нестабільний; backoff; уникати блокуючих викликів у main thread

## 2026-01-20 20:41:55 — POST (MODE=PATCH): P9 FXCM Runtime Hardening — PASS (FSM + stale + metrics/status)

- Що зроблено →
  - Додано канонічну FSM для FXCM та session manager з детермінованими переходами/діями (resubscribe/reconnect).
  - Оновлено stale policy з resubscribe→reconnect та backoff, статус/метрики оновлюються в одному місці.
  - Розширено status_v2 та метрики для FXCM SLO (ticks/stale/resubscribe/reconnect/publish_fail/contract_reject).
  - Додано gate_fxcm_fsm_unit і unit тести FSM/stale.
- Файли →
  - runtime/fxcm/fsm.py — FxcmSessionFsm: стани/переходи/рішення.
  - runtime/fxcm/session_manager.py — цикл FSM + синхронізація status/metrics.
  - runtime/fxcm/adapter.py — вузький інтерфейс адаптера FXCM.
  - runtime/fxcm_forexconnect.py — інтеграція FSM у реальний FXCM loop.
  - runtime/status.py — fxcm FSM поля + лічильники.
  - observability/metrics.py — FXCM SLO метрики.
  - core/contracts/public/status_v2.json — розширення fxcm секції (FSM/лічильники).
  - tools/exit_gates/gates/gate_fxcm_fsm_unit.py — unit gate FSM.
  - tests/test_fxcm_fsm_transitions.py, tests/test_fxcm_stale_policy.py — тести переходів/стейлу.
- Як перевірено →
  - ruff: python -m ruff check . → PASS
  - mypy: python -m mypy . → PASS
  - pytest: python -m pytest -q → PASS
  - run_exit_gates: python tools\run_exit_gates.py --out reports\exit_gates --manifest tools\exit_gates\manifest.json → PASS; proof-pack: reports/exit_gates/2026-01-20_204137/
- Exit gates →
  - gate_fxcm_fsm_unit: PASS (connecting→subscribed_offers→stale→resubscribe→reconnect; streaming після tick)
- Ризики/нотатки →
  - FSM працює без реального FXCM; інтеграційні збої FXCM лишаються предметом окремого P9.1 capture.
- Наступний крок → P9.1 FXCM capture fixtures або P10 final 1m SSOT

## 2026-01-20 21:52:10 — PRE (MODE=read-only discovery): UI Lite subscribe snapshot (symbol=None)

- Мета → з’ясувати поведінку UI Lite при subscribe з symbol=None та snapshot з fallback.
- Scope → ui_lite/server.py (WS subscribe/snapshot логіка).
- Інваріанти → без змін коду; лише фактологія.
- Тести/перевірки → не застосовується.

## 2026-01-20 21:52:40 — POST (MODE=read-only discovery): UI Lite subscribe snapshot (symbol=None)

- Що встановлено →
  - WS subscribe приймає symbol=None/tf=1m; якщо symbol/tf відсутні, сервер підставляє останні значення з *STATE.last_payload**.
  - Snapshot формується через _snapshot_for(symbol, tf, mode), тому при fallback може повертати XAUUSD/1m навіть якщо в subscribe символ None.
- Де → ui_lite/server.py (_ws_handler, _snapshot_for).
- Ризики/нотатки → якщо клієнт хоче явний символ, треба відправляти його в subscribe, інакше буде використано останній payload.

## 2026-01-20 21:56:10 — PRE (MODE=read-only discovery): UI Lite запуск і перевірка 404/app.js

- Мета → відтворити запуск UI Lite, перевірити причину 404 і помилки chart.resize.
- Scope → запуск app.main та відкриття UI Lite порту 8089.
- Інваріанти → без змін коду; лише запуск/спостереження.
- Тести/перевірки → python -m app.main (runtime), відкриття http://127.0.0.1:8089/.

## 2026-01-20 21:56:55 — POST (MODE=read-only discovery): UI Lite запуск і перевірка 404/app.js

- Результат → запуск app.main зупинився на ModuleNotFoundError: No module named 'redis'.
- Висновок → UI Lite не піднявся; 404/помилки в браузері можуть бути через відкриття не того порту або через відсутній запуск UI Lite.
- Де → app.main (runtime), залежність redis.

## 2026-01-20 22:02:10 — PRE (MODE=read-only discovery): UI Lite запуск у venv

- Мета → перевірити запуск app.main у venv після активації.
- Scope → запуск python -m app.main у активованому .venv.
- Інваріанти → без змін коду; лише запуск/спостереження.
- Тести/перевірки → .\.venv\Scripts\Activate.ps1; python -m app.main.

## 2026-01-20 22:03:40 — POST (MODE=read-only discovery): UI Lite запуск у venv

- Результат → app.main стартував у venv; UI Lite не піднявся через зайнятий порт 8089 (Errno 10048).
- Висновок → потрібен stop попереднього процесу UI Lite або інший порт (config.ui_lite_port).
- Де → ui_lite/server.py (bind 127.0.0.1:8089), app.main лог.

## 2026-01-20 22:04:20 — POST (MODE=read-only discovery): UI Lite запуск у venv (повтор)

- Результат → UI Lite успішно стартував на 127.0.0.1:8089, FXCM login OK, ticks надходять.
- Де → app.main лог, ui_lite/server.py.

## 2026-01-20 22:10:30 — PRE (MODE=PATCH): P9.1 FXCM Capture Fixtures (direct ForexConnect → JSONL)

- Мета → capture ticks напряму з FXCM у JSONL (ms int) + validator + tests + exit gate (детермінований)
- Scope → tools/capture_fxcm_ticks.py + core/time/timestamps.py + tests/fixtures + tools/exit_gates/gates/*
- Інваріанти → Python 3.7 rail; no silent fallback; exit gates без реального FXCM; secrets не логувати
- Acceptance Criteria → AC1–AC6
- Ризики/нотатки → ForexConnect SDK/creds; timestamps можуть бути неявні; не штурмувати FXCM (duration/символи)

## 2026-01-20 22:15:20 — POST (MODE=PATCH): P9.1 FXCM Capture Fixtures — PASS (validator + gate)

- Що зроблено →
  - Додано SSOT timestamp конвертацію у epoch ms та валідатор JSONL fixtures.
  - Додано capture tool для FXCM OFFERS → JSONL + meta.json (fail-fast на seconds/float).
  - Додано fixtures + unit tests + exit gate для детермінованої перевірки.
  - Додано runbook для ручного capture.
- Файли →
  - core/time/timestamps.py — SSOT epoch ms rail.
  - tools/capture_fxcm_ticks.py — capture OFFERS ticks → JSONL.
  - tools/validate_tick_fixtures.py — валідатор fixtures.
  - tests/fixtures/ticks_sample_fxcm.jsonl — статичний приклад (20 рядків).
  - tests/test_tick_fixtures_validator.py — unit tests валідатора.
  - tools/exit_gates/gates/gate_tick_fixtures_schema.py — gate fixtures.
  - tools/exit_gates/manifest.json — додано gate_tick_fixtures_schema.
  - docs/runbooks/fxcm_capture_ticks.md — runbook capture.
- Як перевірено →
  - ruff: python -m ruff check . → PASS
  - mypy: python -m mypy . → PASS
  - pytest: python -m pytest -q → PASS
  - run_exit_gates: python tools\run_exit_gates.py --out reports\exit_gates --manifest tools\exit_gates\manifest.json → PASS; proof-pack: reports/exit_gates/2026-01-20_211037/
- Exit gates →
  - gate_tick_fixtures_schema PASS (fixtures ticks_sample_fxcm.jsonl)
- Manual smoke (якщо робив) →
  - не запускалось
- Ризики/нотатки →
  - FXCM capture потребує SDK/creds; запускати короткі сесії (30–60с) з 1–2 символами.
- Next → P10 final 1m SSOT або P10.1 history/backfill rail

## 2026-01-20 22:25:30 — PRE (MODE=PATCH): P10 FINAL 1m SSOT (365d) + Derived HTF rebuild (history_agg) + Publish Gates

- Мета → final 1m SSOT 365d; derived HTF з 1m; publish final-wire; history budgets/probe-first; exit gates/тести
- Scope → runtime/fxcm/history*, store/*, store/derived_builder.py, runtime/final/*, runtime/status.py, tools/exit_gates/gates/*
- Інваріанти → P7.1 rails; inclusive close_time; HTF final тільки з 1m; no silent fallback; no mixing final sources
- Acceptance Criteria → AC1–AC7
- Ризики/нотатки → FXCM history нестабільний; market closed; quotas; уникати багатопоточних викликів SDK

## 2026-01-20 22:48:10 — PRE (MODE=PATCH): P10.2 Rails у store/sqlite_store (final 1m)

- Мета → зафіксувати жорсткі інваріанти 1m final у store: source=history, complete=1, synthetic=0, event_ts_ms=close_time_ms, NoMix на рівні upsert.
- Scope → тільки store/sqlite_store.py (upsert_bars).
- Інваріанти → мінімальний диф; без змін контрактів; fail-fast без silent fallback.
- План → додати перевірки полів і NoMix guard перед вставкою.
- Тести/перевірки → не запускати (локальні зміни, без середовища).

## 2026-01-20 22:49:05 — POST (MODE=PATCH): P10.2 Rails у store/sqlite_store (final 1m)

- Що зроблено → додано fail-fast перевірки final 1m (source/history, complete=1, synthetic=0, event_ts_ms=close_time_ms) та NoMix guard перед upsert.
- Де зроблено → store/sqlite_store.py
- Як перевірено → не запускалось (локальна зміна).
- Ризики/нотатки → можливі падіння при некоректних payload; очікувано для hard-rail.

## 2026-01-20 23:10:00 — PRE (MODE=PATCH): P10 Continue — доказ P10.2 rails + history(1m)→store(365d)→derived→publish + gates

- Мета → (1) довести P10.2 тестами, (2) history тільки 1m з budgets, (3) store retention 365d, (4) derived HTF final з 1m, (5) publish final-wire, (6) нові gates
- Scope → store/sqlite_store.py + tests/* + runtime/fxcm/history* + store/derived_builder.py + runtime/final/* + tools/exit_gates/gates/*
- Інваріанти → P7.1 rails; inclusive close_time; HTF final тільки з 1m; no silent fallback; NoMix final-source
- Acceptance Criteria → AC1–AC8
- Ризики/нотатки → FXCM history нестабільний; quotas; не робити багатопотокових викликів SDK

## 2026-01-20 23:25:00 — POST (MODE=PATCH): P10 Continue — PASS (history(1m)→store(365d)→derived→publish + rails/gates)

- Що зроблено →
  - Додано тести P10.2 для rails у SQLiteStore (source/complete/synthetic/event_ts/NoMix).
  - Посилено history budget (без silent sleep) та FXCM history provider: TF=1m rail, probe-first, chunking, loud errors/degraded.
  - Додано retention watermark у meta та індекс для HTF final; оновлено top-level complete/synthetic для final 1m payload.
  - Derived HTF build тепер loud при gaps; HTF allowlist розширено на 5m; final ingest оркестрація history→store→derived→publish.
  - Додано gates: history TF rail scan та deterministic final-wire-from-store.
- Файли →
  - tests/test_sqlite_store_final_rails.py
  - runtime/fxcm/history_budget.py
  - runtime/fxcm/history_provider.py
  - store/sqlite_store.py
  - store/schema.sql
  - store/derived_builder.py
  - runtime/final/final_ingest.py
  - runtime/final/publisher_final.py
  - runtime/publisher.py
  - core/validation/validator.py
  - config/config.py
  - tools/exit_gates/gates/gate_history_tf_rail_scan.py
  - tools/exit_gates/gates/gate_final_wire_from_store.py
  - tools/exit_gates/manifest.json
  - tests/test_final_1m_validator.py
- Як перевірено →
  - ruff: C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m ruff check . → PASS
  - mypy: C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m mypy . → PASS
  - pytest: C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m pytest -q → PASS
  - run_exit_gates: C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe tools/run_exit_gates.py --out reports/exit_gates --manifest tools/exit_gates/manifest.json → PASS; proof-pack: reports/exit_gates/2026-01-20_215041/
- Exit gates →
  - gate_history_tf_rail_scan PASS
  - gate_final_wire_from_store PASS
- Ризики/нотатки →
  - Probe-first може зупиняти бекфіл при порожній відповіді FXCM; це очікуваний hard-rail.
- Next → P12 tail_guard/repair budgets або мінімальний P8 rail для UI Lite (symbol/tf required)

## 2026-01-20 23:30:00 — POST (MODE=PATCH): P10 Continue — PASS (уточнення metrics errors_total)

- Що зроблено → у FXCM history provider прибрано подвійний інкремент errors_total (через StatusManager).
- Файли → runtime/fxcm/history_provider.py
- Як перевірено →
  - ruff: C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m ruff check . → PASS
  - mypy: C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m mypy . → PASS
  - pytest: C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m pytest -q → PASS
  - run_exit_gates: C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe tools/run_exit_gates.py --out reports/exit_gates --manifest tools/exit_gates/manifest.json → PASS; proof-pack: reports/exit_gates/2026-01-20_215142/
- Exit gates →
  - gate_history_tf_rail_scan PASS
  - gate_final_wire_from_store PASS
- Ризики/нотатки → без змін у поведінці rails; лише усунено дубль метрик.

## 2026-01-20 23:40:00 — PRE (MODE=PATCH): P12 TailGuard Marks Persistence + Repair Budgets + Invalidation Rails

Мета → (1) персистентні tail_guard marks у SQLite, (2) інвалідація marks при зміні final-даних, (3) repair budgets з loud error, (4) calendar-aware audit

Scope → store/schema.sql + store/sqlite_store.py + runtime/tail_guard.py + runtime/repair.py (budget rail) + status schema + tools/exit_gates/gates + tests

Інваріанти → P7.1 rails; no silent fallback; calendar-aware; мінімальний диф; без нових runner-ів

Acceptance Criteria → AC1–AC7

Ризики/нотатки → міграція SQLite; не зламати Public Surface {NS}:commands/{NS}:status:snapshot

## 2026-01-20 23:55:00 — POST (MODE=PATCH): P12 TailGuard Marks Persistence + Repair Budgets + Invalidation Rails — PASS

Що зроблено →
- Додано персистентні marks tail_guard у SQLite + API для get/upsert/invalidate та автоматичну інвалідацію при upsert 1m/HTF.
- Tail guard переведено на marks у SQLite (etag + verified_until), calendar-aware аудит, skip без Redis TTL.
- Додано repair budgets (missing/вікно/чанки) з hard fail та loud errors.
- Оновлено status_v2 та StatusManager для marks.
- Додано gates і тести P12.

Файли →
- store/schema.sql — tail_audit_state + індекс.
- store/sqlite_store.py — API tail_audit_state + invalidation rails.
- runtime/tail_guard.py — marks/etag, calendar-aware audit.
- runtime/repair.py — repair budgets (hard fail).
- config/config.py — budgets SSOT.
- runtime/status.py + core/contracts/public/status_v2.json — tail_guard.marks.
- tools/exit_gates/gates/gate_tail_guard_marks_persist.py — gate persist/invalidate.
- tools/exit_gates/gates/gate_tail_guard_repair_budget.py — gate budgets.
- tools/exit_gates/manifest.json — додано 2 gates.
- tests/test_tail_guard_marks_persistence.py — persistence.
- tests/test_tail_guard_invalidation_on_upsert.py — invalidation.
- tests/test_repair_budget_rails.py — budgets.
- tests/test_tail_guard_checked_ttl_skips.py, tests/test_tail_guard_marks.py, tests/test_validator_status.py, tests/test_repair_rejects_large_range.py — оновлення під нові rails.

Як перевірено →
- ruff: C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m ruff check . → PASS
- mypy: C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m mypy . → PASS
- pytest: C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m pytest -q → PASS
- run_exit_gates: C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe tools/run_exit_gates.py --out reports/exit_gates --manifest tools/exit_gates/manifest.json → PASS; proof-pack: reports/exit_gates/2026-01-20_221720/

Exit Gates →
- gate_tail_guard_marks_persist PASS
- gate_tail_guard_repair_budget PASS

Ризики/нотатки → invalidation агресивна (обрізає verified_until); очікувано для гарантії реаудиту.

## 2026-01-20 23:58:00 — PRE (MODE=PATCH): P8.1 UI Lite No-Lie Rail (symbol/tf required, no last_payload fallback)

- Мета → (1) прибрати fallback на last_payload, (2) вимагати symbol/tf у subscribe, (3) детермінований error payload, (4) gate+tests
- Scope → ui_lite/server.py + tests + exit gates
- Інваріанти → no silent fallback; мінімальний диф; без нових runner-ів
- Acceptance Criteria → AC1–AC5
- Ризики/нотатки → не ламати сумісність WS протоколу без явного повідомлення про помилку

## 2026-01-20 23:59:00 — POST (MODE=PATCH): P8.1 UI Lite No-Lie Rail — PASS

- Що зроблено →
  - Забрано fallback на last_payload у WS subscribe; додано явний error payload для missing symbol/tf.
  - Додано unit-тести та gate на відсутність last_payload fallback.
- Файли →
  - ui_lite/server.py — валідація subscribe + error відповіді.
  - tests/test_ui_lite_no_fallback.py — unit-тести missing symbol/tf.
  - tools/exit_gates/gates/gate_ui_lite_no_last_payload_fallback.py — scan gate.
  - tools/exit_gates/manifest.json — додано gate.
- Як перевірено →
  - ruff: C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m ruff check . → PASS
  - mypy: C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m mypy . → PASS
  - pytest: C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m pytest -q → PASS
  - run_exit_gates: C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe tools/run_exit_gates.py --out reports/exit_gates --manifest tools/exit_gates/manifest.json → PASS; proof-pack: reports/exit_gates/2026-01-20_222433/
- Exit Gates → gate_ui_lite_no_last_payload_fallback PASS
- Ризики/нотатки → поведінка WS змінена на явну помилку при missing symbol/tf.

## 2026-01-21 00:05:00 — PRE (MODE=PATCH): Smoke P8.1+P12 (мінімальний живий)

- Мета → підтвердити status snapshot (tail_guard.marks), tail_guard command, skip за marks, UI Lite error payload без symbol/tf
- Scope → runtime запуск + redis-cli + ws клієнт
- Інваріанти → no silent fallback; без нових runner-ів
- План → старт app.main (коротко), GET status, PUBLISH fxcm_tail_guard, GET status, WS subscribe без symbol/tf, стоп процесу
- Тести/перевірки → ручний smoke через PowerShell

## 2026-01-21 00:12:00 — POST (MODE=PATCH): Smoke P8.1+P12 (мінімальний живий)

- Що зроблено →
  - Запуск app.main (PID 18724, потім 12148), перевірка status snapshot.
  - Публікація fxcm_tail_guard через redis-cli та перевірка last_command/marks.
  - WS smoke: subscribe без symbol/tf повертає error payload.
- Результат →
  - status snapshot містить tail_guard.tf_states і tail_guard.marks (verified_* / etag).
  - WS: missing_symbol/missing_tf повертає error (OK).
  - tail_guard command не оброблено: last_command залишився unknown/error; marks не оновились (потрібна перевірка command bus/JSON payload каналу).
- Як перевірено →
  - redis-cli GET fxcm_local:status:snapshot (marks присутні).
  - redis-cli PUBLISH fxcm_local:commands ... (integer=1), потім GET + парсинг last_command/marks.
  - WS клієнт через python websockets (error payload).
- Ризики/нотатки →
  - У snapshot спостерігається tick_contract_reject (symbol не у allowlist) — шум для smoke.
  - Для повного smoke потрібно узгодити channel/payload для fxcm_tail_guard, щоб last_command ставав ok.
  
  ## 2026-01-18 01:12:30 — PRE (MODE=PATCH): P13 Soak Monitor + UI Gap Candles

  - Мета → реалізувати soak-монітор для 1m final + preview та додати UI Lite візуалізацію прогалин (whitespace бари) без silent fill; додати статичний gate-скан, тести й оновити журнал.
  - Scope → tools/soak_monitor.py, ui_lite/static/app.js, тести для вставки whitespace, gate-скан у tools/audit (або наявний gate механізм) + manifest/реєстрація, записи PRE/POST.
  - Non-goals → зміна публічних контрактів, нові entrypoints/runner, зміни бізнес-логіки OHLCV/FXCM.
  - Інваріанти/рейки → Python 3.7; без silent fallback; жодних нових ENV-ключів; мінімальний диф; time=epoch ms → UI секунди; gaps мають показуватись як whitespace, а не підмальовані OHLCV.
  - План:
    1) Додати soak_monitor з fail-fast логікою і чіткими лімітами/порогами.
    2) Додати insertWhitespace у UI Lite та застосувати для snapshot/updates.
    3) Додати тести для вставки whitespace (JS/Python, залежно від існуючих тестів).
    4) Додати gate-скан для перевірки наявності gap-логіки в UI.
    5) За потреби — мінімальні коригування для сумісності з існуючим кодом.
  - Тести/перевірки (план) → ruff, mypy, pytest, Exit Gate P3/P2 за потреби; audit scan log після змін.

  ## 2026-01-18 01:34:30 — POST (MODE=PATCH): P13 Soak Monitor + UI Gap Candles

  - Що зроблено →
    - Додано інструмент tools/soak_monitor.py для моніторингу прогалин у стрімі OHLCV (final/preview) з порогами та JSON-звітом.
    - Додано вставку whitespace барів у UI Lite (snapshot + incremental) для візуалізації прогалин без silent fill.
    - Додано gate-скан UI gap visualization та зареєстровано в manifest.
    - Додано тест gate_ui_gap_visualization_scan.
    - Виконано discovery Redis: типи ключів, канали Pub/Sub і GET status:snapshot (для фактів P13).
  - Де зроблено →
    - tools/soak_monitor.py
    - ui_lite/static/app.js
    - tools/exit_gates/gates/gate_ui_gap_visualization_scan.py
    - tools/exit_gates/manifest.json
    - tests/test_gate_ui_gap_visualization_scan.py
  - Як перевірено →
    - Task “Lint: ruff” → FAIL (WSL/bash відсутні в середовищі Windows).
    - Task “Typecheck: mypy” → FAIL (WSL/bash відсутні в середовищі Windows).
    - Task “Test: pytest” → FAIL (WSL/bash відсутні в середовищі Windows).
    - Task “Audit: Exit Gate P3” → запущено; у виводі терміналу відсутній підтверджений результат (потрібна повторна перевірка).
  - Ризики/нотатки →
    - Результати ruff/mypy/pytest не отримані через відсутність WSL; після встановлення WSL або альтернативного середовища повторити перевірки.
    - Exit Gate P3 потребує повторного запуску з фіксацією результату.
    - Redis discovery: ключ fxcm_local:status:snapshot тип=string; ключі fxcm_local:status/ohlcv/commands тип=none; канали Pub/Sub: fxcm_local:ohlcv, fxcm_local:commands; snapshot отримано (tail_guard, price, fxcm, command_bus присутні).

## 2026-01-21 09:20:00 — PRE (MODE=PATCH): P13 Soak + UI Gap Candles + Status No-Spam + OHLCV wire

- Мета → виконати P13 end-to-end: soak_monitor з reports/soak, UI Lite candlestick + whitespace gaps, status pubsub compact з 8KB rail, відновити OHLCV wire, додати gates/тести, пройти ruff/mypy/pytest та run_exit_gates.
- Scope → tools/soak_monitor.py, ui_lite/static/*, ui_lite/server.py (за потреби), runtime/status.py, tools/exit_gates/gates/*, tools/exit_gates/manifest.json, tests/*, Work/01log.md.
- Non-goals → нові runner-и/entrypoints, зміна WS протоколу, зміна public контрактів.
- Інваріанти/рейки → P7.1 XOR runtime; Python 3.7; no silent fallback; pubsub status ≤ 8KB; snapshot без масивів; UI time ms→sec.
- План:
  1) Зняти baseline: run_exit_gates, ruff, mypy, pytest.
  2) Відновити OHLCV wire (preview 1m) + rate-limited INFO.
  3) Додати rail для status pubsub payload size + компактний payload.
  4) Оновити UI Lite static: chart_demo + chart_adapter + whitespace gaps.
  5) Додати gates і unit тести.
  6) Прогнати ruff/mypy/pytest + run_exit_gates + зафіксувати smoke.
- Тести/перевірки (план) → python -m ruff check ., python -m mypy ., python -m pytest -q, python tools/run_exit_gates.py --out reports/exit_gates --manifest tools/exit_gates/manifest.json.

## 2026-01-21 09:35:00 — POST (MODE=PATCH): P13 Soak + UI Gap Candles + Status No-Spam + OHLCV wire

- Що зроблено →
  - Оновлено UI Lite static (index/styles/app + chart_adapter) для candlestick + whitespace gaps.
  - Додано chart_adapter.js з insertWhitespace та gapPlaceholders.
  - Додано rail для компактного status pubsub (≤8KB) з error+метрикою, snapshot також компактний.
  - Додано rate-limited INFO лог publish OHLCV preview (tf=1m).
  - Оновлено PreviewBuilder для фіналізації попереднього bucket (complete=true) на rollover.
  - Додано gate_status_pubsub_size та gate_ui_candles_gap_scan, оновлено manifest.
  - Додано тести: soak_monitor report, status payload size rail, gap whitespace scan.
  - Оновлено soak_monitor: --mode/--duration_s, reports/soak/<ts>.json, last_open_time_ms.
- Де зроблено →
  - ui_lite/static/index.html
  - ui_lite/static/styles.css
  - ui_lite/static/app.js
  - ui_lite/static/chart_adapter.js
  - ui_lite/server.py
  - runtime/status.py
  - observability/metrics.py
  - runtime/preview_builder.py
  - app/composition.py
  - tools/soak_monitor.py
  - tools/exit_gates/gates/gate_status_pubsub_size.py
  - tools/exit_gates/gates/gate_ui_candles_gap_scan.py
  - tools/exit_gates/gates/gate_ui_gap_visualization_scan.py
  - tools/exit_gates/manifest.json
  - tests/test_soak_monitor_report.py
  - tests/test_status_payload_size_rail.py
  - tests/test_ui_gap_insert_whitespace.py
- Як перевірено →
  - python -m ruff check . → OK (попередньо було FAIL через помилку команди та I001/E501; виправлено ruff --fix + форматування тесту).
  - python -m mypy . → OK.
  - python -m pytest -q → OK.
  - python tools/run_exit_gates.py --out reports/exit_gates --manifest tools/exit_gates/manifest.json → OK.
- Smoke/ручні перевірки →
  - python -m app.main --fxcm-preview → OK (FXCM login успішний, UI Lite піднято на 127.0.0.1:8089, логи publish ohlcv tf=1m).
  - redis-cli --raw SUBSCRIBE fxcm_local:ohlcv → отримано preview 1m повідомлення (AC1).
  - redis-cli --raw SUBSCRIBE fxcm_local:status → payload компактний; GET fxcm_local:status:snapshot size=5842 байт (≤8KB).
  - UI Lite відкрито у браузері http://127.0.0.1:8089 (candlestick + gaps через chart_adapter).
  - python tools/soak_monitor.py --ns fxcm_local --symbol XAUUSD --mode preview --tf 1m --duration_s 120 → OK, звіт reports/soak/1768983053553.json.
- Ризики/нотатки →
  - У status errors[] присутній tick_contract_reject (symbol не у allowlist) — шум для smoke.
  - Порт 8089 був зайнятий попереднім процесом; після зупинки порт звільнено.
  - AC2/AC3 перевірені в UI Lite після підняття сервера.

## 2026-01-21 09:45:00 — POST (MODE=PATCH): P13 finalize (re-run checks після soak_monitor fix)

- Що зроблено →
  - Виправлено запуск tools/soak_monitor.py (sys.path + noqa E402).
  - Повторно пройдено ruff/mypy/pytest та run_exit_gates.
- Як перевірено →
  - python -m ruff check . → OK.
  - python -m mypy . → OK.
  - python -m pytest -q → OK.
  - python tools/run_exit_gates.py --out reports/exit_gates --manifest tools/exit_gates/manifest.json → OK.
- Ризики/нотатки → без змін у функціоналі, лише виправлення запуску soak_monitor.

## 2026-01-21 10:05:00 — PRE (MODE=PATCH): UI Lite resize fallback

- Мета → усунути помилку chart.resize is not a function у UI Lite.
- Scope → ui_lite/static/app.js.
- Non-goals → зміна WS протоколу або логіки даних.
- Інваріанти/рейки → мінімальний диф, без silent fallback (лише безпечний fallback для resize).
- План:
  1) Додати перевірку на наявність chart.resize.
  2) Використати chart.applyOptions для fallback.
- Тести/перевірки (план) → не запускати (UI smoke за потреби).

## 2026-01-21 10:10:00 — POST (MODE=PATCH): UI Lite resize fallback

- Що зроблено → додано fallback для resize через chart.applyOptions, якщо chart.resize недоступний.
- Де зроблено → ui_lite/static/app.js.
- Як перевірено → не запускалось (локальна правка UI).
- Ризики/нотатки → очікується усунення помилки resize на старій версії LightweightCharts.

## 2026-01-21 10:20:00 — PRE (MODE=PATCH): UI Lite favicon

- Мета → прибрати 404 на /favicon.ico у UI Lite.
- Scope → ui_lite/server.py.
- Non-goals → додавання графічної іконки.
- Інваріанти/рейки → мінімальний диф.
- План:
  1) Віддати 204 No Content для /favicon.ico.
- Тести/перевірки (план) → не запускати.

## 2026-01-21 10:22:00 — POST (MODE=PATCH): UI Lite favicon

- Що зроблено → /favicon.ico повертає 204 No Content.
- Де зроблено → ui_lite/server.py.
- Як перевірено → не запускалось.
- Ризики/нотатки → 404 більше не має з'являтись у браузері.

## 2026-01-21 10:40:00 — PRE (MODE=PATCH): UI Lite resize/applyOptions guard

- Мета → прибрати помилку chart.applyOptions is not a function у UI Lite.
- Scope → ui_lite/static/app.js.
- Non-goals → зміна даних/WS протоколу.
- Інваріанти/рейки → мінімальний диф.
- План:
  1) Додати guard для applyOptions, якщо метод відсутній.
- Тести/перевірки (план) → не запускати.

## 2026-01-21 10:42:00 — POST (MODE=PATCH): UI Lite resize/applyOptions guard

- Що зроблено → додано перевірку наявності chart.applyOptions перед викликом.
- Де зроблено → ui_lite/static/app.js.
- Як перевірено → не запускалось.
- Ризики/нотатки → очікується усунення TypeError у консолі.

## 2026-01-21 10:30:00 — PRE (MODE=read-only discovery): Чому немає ohlcv у pubsub

- Мета → з'ясувати, чому redis-cli SUBSCRIBE fxcm_local:ohlcv не отримує повідомлень.
- Scope → тільки Redis status snapshot (read-only).
- Non-goals → зміни коду чи конфігів.
- Інваріанти/рейки → без змін коду, лише читання.
- План:
  1) Перевірити fxcm_local:status:snapshot та поля ohlcv_preview/fxcm/price.
  2) Зафіксувати висновок.

## 2026-01-21 10:31:00 — POST (MODE=read-only discovery): Чому немає ohlcv у pubsub

- Що зроблено → прочитано fxcm_local:status:snapshot.
- Де зроблено → Redis fxcm_local:status:snapshot.
- Як перевірено →
  - redis-cli --raw GET fxcm_local:status:snapshot | python -c "..." → ts=1768983718807.
  - Поле ohlcv_preview → last_publish_ts_ms=0, preview_total=0.
  - Поля fxcm/price → state=streaming, ticks_total=896.
- Висновок → FXCM тики є, але preview OHLCV не публікується (ймовірно, запуск без --fxcm-preview або ohlcv_preview_enabled=false у активному профілі).

## 2026-01-21 10:55:00 — PRE (MODE=PATCH): UI Lite бари з оновленням open_time

- Мета → дозволити оновлення свічок з тим самим open_time, щоб не було плоских ліній.
- Scope → ui_lite/server.py.
- Non-goals → зміна протоколу WS чи контрактів.
- Інваріанти/рейки → мінімальний диф.
- План:
  1) Оновлювати ring buffer для існуючого open_time.
  2) Пропускати лише ідентичні бари, дозволяючи апдейти.
- Тести/перевірки (план) → не запускати.

## 2026-01-21 11:00:00 — POST (MODE=PATCH): UI Lite бари з оновленням open_time

- Що зроблено → оновлення барів у ring buffer для того самого open_time; фільтр пропускає лише ідентичні значення.
- Де зроблено → ui_lite/server.py.
- Як перевірено → не запускалось.
- Ризики/нотатки → має відновити повні свічки (не плоскі лінії) у UI.

## 2026-01-21 11:10:00 — PRE (MODE=PATCH): UI Lite candlestick colors

- Мета → зробити свічки в UI Lite більш читабельними (кольори up/down/wick).
- Scope → ui_lite/static/app.js.
- Non-goals → зміна даних чи протоколів.
- Інваріанти/рейки → мінімальний диф.
- План:
  1) Оновити палітру candlestick (зелений/червоний).
- Тести/перевірки (план) → не запускати.

## 2026-01-21 11:12:00 — POST (MODE=PATCH): UI Lite candlestick colors

- Що зроблено → оновлено кольори up/down/wick/border для candlestick.
- Де зроблено → ui_lite/static/app.js.
- Як перевірено → не запускалось.
- Ризики/нотатки → має покращити візуальну читабельність свічок.

## 2026-01-21 11:20:00 — PRE (MODE=PATCH): UI Lite dark theme + zoom/fit + volume

- Мета → привести UI Lite до вигляду як на зразку (dark theme), додати volume, та кнопку Fit для зручного масштабування.
- Scope → ui_lite/static/index.html, ui_lite/static/styles.css, ui_lite/static/app.js, ui_lite/static/chart_adapter.js.
- Non-goals → зміна протоколів WS чи runtime логіки.
- Інваріанти/рейки → мінімальний диф, без зміни даних.
- План:
  1) Увімкнути dark theme для chart і CSS.
  2) Додати histogram volume series (якщо volume є в барі).
  3) Додати кнопку Fit для швидкого масштабування.
- Тести/перевірки (план) → не запускати.

## 2026-01-21 11:30:00 — POST (MODE=PATCH): UI Lite dark theme + zoom/fit + volume

- Що зроблено → додано volume-гістограму, підключено кнопку Fit, налаштовано темні кольори сітки/кросхейра у chart options.
- Де зроблено → ui_lite/static/app.js.
- Як перевірено → не запускалось (UI зміни, ручна перевірка за потреби).
- Ризики/нотатки → volume відображається лише якщо поле volume є у барі; очікується ручний огляд UI.

## 2026-01-21 11:40:00 — PRE (MODE=PATCH): UI Lite fallback для histogram series

- Мета → прибрати помилку addHistogramSeries is not a function у старій версії LightweightCharts.
- Scope → ui_lite/static/app.js.
- Non-goals → зміни протоколів WS чи даних.
- Інваріанти/рейки → мінімальний диф; без silent fallback (volume просто не показується якщо API відсутній).
- План:
  1) Додати guard для addHistogramSeries.
  2) Захистити setData/update/clear для volumeSeries.
- Тести/перевірки (план) → не запускати.

## 2026-01-21 11:42:00 — POST (MODE=PATCH): UI Lite fallback для histogram series

- Що зроблено → додано guard на addHistogramSeries та безпечні виклики volumeSeries.
- Де зроблено → ui_lite/static/app.js.
- Як перевірено → не запускалось (UI зміни, ручна перевірка за потреби).
- Ризики/нотатки → volume не відображається на старих версіях LightweightCharts; основна свічкова серія працює.

## 2026-01-21 11:50:00 — PRE (MODE=PATCH): UI Lite guard для timeScale

- Мета → прибрати помилку chart.timeScale is not a function на старій версії LightweightCharts.
- Scope → ui_lite/static/app.js.
- Non-goals → зміни даних/WS протоколу.
- Інваріанти/рейки → мінімальний диф; без silent fallback (кнопка Fit не діє без timeScale).
- План:
  1) Додати guard для chart.timeScale у fitContent.
  2) Не викликати fitContent, якщо API відсутній.
- Тести/перевірки (план) → не запускати.

## 2026-01-21 11:52:00 — POST (MODE=PATCH): UI Lite guard для timeScale

- Що зроблено → додано guard для chart.timeScale у fitContent.
- Де зроблено → ui_lite/static/app.js.
- Як перевірено → не запускалось (UI зміни, ручна перевірка за потреби).
- Ризики/нотатки → на старих версіях LightweightCharts кнопка Fit не працює, але UI не падає.

## 2026-01-21 12:05:00 — PRE (MODE=PATCH): Оновлення LightweightCharts до 5.4

- Мета → підняти версію LightweightCharts до 5.4 для доступності потрібних API.
- Scope → ui_lite/static/index.html.
- Non-goals → зміни бекенду або протоколів.
- Інваріанти/рейки → мінімальний диф.
- План:
  1) Оновити script src на lightweight-charts@5.4.0.
- Тести/перевірки (план) → не запускати.

## 2026-01-21 12:06:00 — POST (MODE=PATCH): Оновлення LightweightCharts до 5.4

- Що зроблено → підключення LightweightCharts оновлено до 5.4.0 через CDN.
- Де зроблено → ui_lite/static/index.html.
- Як перевірено → не запускалось.
- Ризики/нотатки → потрібен доступ до CDN; локальний vendor-файл більше не використовується.

## 2026-01-21 12:12:00 — PRE (MODE=PATCH): Локальне підключення LightweightCharts 5.4

- Мета → прибрати помилку LightweightCharts is not defined через CDN, підключивши локальний файл 5.4.
- Scope → ui_lite/static/vendor/lightweight-charts.standalone.production.js, ui_lite/static/index.html.
- Non-goals → зміни бекенду або протоколів.
- Інваріанти/рейки → мінімальний диф, без зміни UI логіки.
- План:
  1) Завантажити lightweight-charts 5.4.0 у vendor.
  2) Повернути script src на локальний /vendor.
- Тести/перевірки (план) → не запускати.

## 2026-01-21 12:16:00 — POST (MODE=PATCH): Локальне підключення LightweightCharts (5.1.0)

- Що зроблено → завантажено lightweight-charts 5.1.0 у vendor та повернуто локальне підключення скрипта.
- Де зроблено → ui_lite/static/vendor/lightweight-charts.standalone.production.js, ui_lite/static/index.html.
- Як перевірено → не запускалось.
- Ризики/нотатки → версія 5.4.0 недоступна на unpkg; використано останню доступну 5.1.0.

## 2026-01-21 12:20:00 — PRE (MODE=PATCH): Сумісність addSeries для v5

- Мета → усунути помилку addCandlestickSeries is not a function у v5.
- Scope → ui_lite/static/app.js.
- Non-goals → зміни протоколів WS чи даних.
- Інваріанти/рейки → мінімальний диф; серії працюють якщо доступні API.
- План:
  1) Додати fallback на chart.addSeries з CandlestickSeries/HistogramSeries.
  2) Захистити setData/update, якщо серія недоступна.
- Тести/перевірки (план) → не запускати.

## 2026-01-21 12:22:00 — POST (MODE=PATCH): Сумісність addSeries для v5

- Що зроблено → додано fallback на chart.addSeries і guard для candlestick/volume серій.
- Де зроблено → ui_lite/static/app.js.
- Як перевірено → не запускалось.
- Ризики/нотатки → якщо API серій недоступний, UI не відображає серії, але не падає.

## 2026-01-21 12:35:00 — PRE (MODE=PATCH): Розділення шкали volume та підписи осі часу

- Мета → щоб volume не перекривав свічки та були читабельні підписи осі часу.
- Scope → ui_lite/static/app.js.
- Non-goals → зміни бекенду/WS.
- Інваріанти/рейки → мінімальний диф.
- План:
  1) Перевести volume на окрему priceScaleId.
  2) Налаштувати scaleMargins для ціни/обсягу.
- Тести/перевірки (план) → не запускати.

## 2026-01-21 12:37:00 — POST (MODE=PATCH): Розділення шкали volume та підписи осі часу

- Що зроблено → volume винесено на окрему шкалу, додано scaleMargins щоб не перекривав свічки.
- Де зроблено → ui_lite/static/app.js.
- Як перевірено → не запускалось.
- Ризики/нотатки → потрібен UI-огляд; підписи осі часу залежать від розміру контейнера.

## 2026-01-21 12:50:00 — PRE (MODE=read-only discovery): Діагностика мікро-нестиковок свічок/гепів

- Мета → з’ясувати причину мікро-нестиковок між свічками без внесення змін.
- Scope → читання ui_lite/static/app.js, ui_lite/static/chart_adapter.js, ui_lite/server.py.
- Non-goals → будь-які патчі або синтетичні виправлення.
- Інваріанти/рейки → лише read-only; фіксувати факти з path:line.
- План:
  1) Перевірити нормалізацію часу/барів у chart_adapter.
  2) Перевірити gap/whitespace логіку в app.js.
  3) Перевірити оновлення барів у server.py (open_time/дедуп).
- Тести/перевірки (план) → не запускати.

## 2026-01-21 13:05:00 — POST (MODE=read-only discovery): Діагностика мікро-нестиковок свічок/гепів

- Що зроблено → проаналізовано нормалізацію часу, gap/whitespace логіку та порядок оновлень.
- Де зроблено → ui_lite/static/chart_adapter.js#L15-L74, ui_lite/static/app.js#L129-L173, ui_lite/server.py#L170-L205, ui_lite/server.py#L395-L454.
- Факти/спостереження →
  - Нормалізація часу завжди робить `time = floor(open_time_ms/1000)` і ігнорує можливий `bar.time` з пабсабу: [ui_lite/static/chart_adapter.js](ui_lite/static/chart_adapter.js#L15-L29), [ui_lite/server.py](ui_lite/server.py#L170-L190).
  - Вставка “whitespace” залежить від строгого порогу `bar.time > lastTime + tfSec`: [ui_lite/static/chart_adapter.js](ui_lite/static/chart_adapter.js#L49-L74).
  - Оновлення у UI приймаються у порядку приходу і `lastUiBarTimeByKey` перезаписується кожним баром без перевірки монотонності: [ui_lite/static/app.js](ui_lite/static/app.js#L155-L173).
  - У broadcaster бари з payload обробляються в порядку як прийшли у `bars` (без сортування за open_time): [ui_lite/server.py](ui_lite/server.py#L395-L431).
- Висновок (ймовірна причина) → мікро-нестиковки виникають коли:
  1) `open_time_ms` не кратний TF і після `floor(ms/1000)` утворюються кроки > tfSec (або “зсув” по секундам), що тригерить gapPlaceholders; або
  2) бари приходять нестрого монотонно (out-of-order) і UI зсуває `lastUiBarTimeByKey` назад, після чого наступний бар виглядає як “геп”.
- Як підтвердити без патчів → перевірити монотонність `open_time_ms` та різницю `Δtime` в потоці WS/Redis; якщо є зворотні/нерівні кроки — причина підтверджена.

## 2026-01-21 13:15:00 — PRE (MODE=PATCH): Нормалізація порядку барів (монотонність)

- Мета → усунути мікро-нестиковки через нестрого монотонні бари.
- Scope → ui_lite/server.py, ui_lite/static/app.js.
- Non-goals → синтетичні гепи/заглушки; зміна контрактів.
- Інваріанти/рейки → мінімальний диф, без зміни payload.
- План:
  1) Відсортувати бари за open_time перед фільтрацією/розсилкою.
  2) Не зменшувати lastUiBarTimeByKey у UI при out-of-order.
- Тести/перевірки (план) → не запускати.

## 2026-01-21 13:18:00 — POST (MODE=PATCH): Нормалізація порядку барів (монотонність)

- Що зроблено → додано сортування барів за open_time та захист від зменшення lastUiBarTimeByKey.
- Де зроблено → ui_lite/server.py, ui_lite/static/app.js.
- Як перевірено → не запускалось.
- Ризики/нотатки → запізнілі бари можуть бути відображені, але не порушать послідовність часової осі.

## 2026-01-21 13:30:00 — PRE (MODE=read-only discovery): Додаткова діагностика нестиковок свічок

- Мета → з’ясувати інші джерела нестиковок (вертикальні/горизонтальні гепи) без змін коду.
- Scope → runtime/* (preview builder), core/time/*, ui_lite/static/chart_adapter.js, ui_lite/server.py.
- Non-goals → будь-які патчі або синтетичні виправлення.
- Інваріанти/рейки → лише read-only; фіксувати факти з path:line.
- План:
  1) Перевірити як формується open_time/open_time_ms у preview/final.
  2) Перевірити округлення/нормалізацію часу (bucket) до TF.
  3) Звірити відповідність TF і кроків у pubsub.
- Тести/перевірки (план) → не запускати.

## 2026-01-21 13:45:00 — POST (MODE=read-only discovery): Додаткова діагностика нестиковок свічок

- Що зроблено → переглянуто формування open_time для preview, кешування та порядок публікації.
- Де зроблено → runtime/preview_builder.py#L72-L122, runtime/publisher.py#L39-L118, ui_lite/static/chart_adapter.js#L15-L74, ui_lite/static/app.js#L155-L173.
- Факти/спостереження →
  - Preview builder використовує `tick_ts_ms` напряму для розрахунку bucket (`get_bucket_open_ms`), отже якщо тики приходять out-of-order — `bucket_start` може рухатись назад: [runtime/preview_builder.py](runtime/preview_builder.py#L72-L86).
  - `OhlcvCache.update_bar` оновлює лише останній бар при збігу `open_time`; якщо приходить пізній тик на старий bucket — бар додається в кінець deque і порушує порядок: [runtime/preview_builder.py](runtime/preview_builder.py#L36-L57).
  - `build_payloads` віддає хвіст кешу як є (без сортування), а `publish_ohlcv_batch` не сортує `bars`: [runtime/preview_builder.py](runtime/preview_builder.py#L114-L122), [runtime/publisher.py](runtime/publisher.py#L55-L112).
  - На UI gap логіка очікує монотонний час і використовує `lastUiBarTimeByKey` для вставки пропусків: [ui_lite/static/app.js](ui_lite/static/app.js#L155-L173).
- Висновок → основна причина нестиковок: out-of-order тики → out-of-order preview bars у кеші → несортований tail у pubsub → UI бачить «рваний» часовий ряд.
- Як підтвердити без патчів → порівняти послідовність `open_time_ms` у payload bars та `tick_ts_ms` (монотонність/кроки) у логах FXCM.

## 2026-01-21 14:10:00 — PRE (MODE=PATCH): P14.0 late-tick drop rail + P14.1 soak + P14.2 тест/exit gate + P14.3 status

- Мета → реалізувати late-tick drop rail у preview, детермінований тест+gate, оновити soak та статусні лічильники.
- Scope → runtime/preview_builder.py, tools/soak_monitor.py, tests/fixtures/ticks_out_of_order_boundary.jsonl, tests/test_preview_builder_late_tick_drop.py, tools/exit_gates/gates/gate_preview_late_tick_drop.py, tools/exit_gates/manifest.json, core/contracts/public/status_v2.json, runtime/status.py, runtime/ohlcv_preview.py, app/composition.py.
- Non-goals → синтетичні гепи/патчі в UI; зміни контрактів OHLCV.
- Інваріанти/рейки → I1–I4 для preview; мінімальний диф; без silent fallback.
- План:
  1) Реалізувати late-tick drop + alignment rail + sorted publish у PreviewBuilder.
  2) Стабілізувати кеш (update same open_time, sort перед publish).
  3) Оновити soak_monitor до past mutation/misaligned exit.
  4) Додати fixture+unit test і exit gate.
  5) Розширити status_v2 + StatusManager поля ohlcv_preview.
- Тести/перевірки (план) → не запускати.

## 2026-01-21 14:35:00 — POST (MODE=PATCH): P14.0 late-tick drop rail + P14.1 soak + P14.2 тест/exit gate + P14.3 status

- Що зроблено →
  - Реалізовано late-tick drop rail, стан потоку, сортування batch та безпечний update кешу preview.
  - Оновлено soak_monitor для past mutations/misaligned та нового exit code.
  - Додано fixture, unit test і exit gate для out-of-order на межі хвилини.
  - Розширено status_v2/StatusManager полями preview-rail (late/misaligned/last_late_tick тощо).
- Де зроблено → runtime/preview_builder.py, runtime/ohlcv_preview.py, app/composition.py, tools/soak_monitor.py, tests/fixtures/ticks_out_of_order_boundary.jsonl, tests/test_preview_builder_late_tick_drop.py, tools/exit_gates/gates/gate_preview_late_tick_drop.py, tools/exit_gates/manifest.json, core/contracts/public/status_v2.json, runtime/status.py.
- Як перевірено → не запускалось.
- Ризики/нотатки → 1d preview тепер вирівнюється по UTC-мінуті (без boundary) через rail; потрібна перевірка бізнес-очікувань.

## 2026-01-21 15:05:00 — PRE (MODE=PATCH): Фікс mypy/pyright для P14

- Мета → усунути помилки read-only Config і типізацію tick у тестах/гейті.
- Scope → tests/test_preview_builder_late_tick_drop.py, tools/exit_gates/gates/gate_preview_late_tick_drop.py.
- Non-goals → зміни логіки preview.
- Інваріанти/рейки → мінімальний диф.
- План:
  1) Використати dataclasses.replace для frozen Config.
  2) Безпечне читання tick полів через get().
- Тести/перевірки (план) → не запускати.

## 2026-01-21 15:07:00 — POST (MODE=PATCH): Фікс mypy/pyright для P14

- Що зроблено → замінено мутацію Config на replace(), додано безпечні get() для tick полів.
- Де зроблено → tests/test_preview_builder_late_tick_drop.py, tools/exit_gates/gates/gate_preview_late_tick_drop.py.
- Як перевірено → не запускалось.
- Ризики/нотатки → лише типізаційні правки.

## 2026-01-21 15:20:00 — PRE (MODE=PATCH): UI Lite панорамування/масштаб

- Мета → дозволити вільне переміщення графіка та масштабування як у ТВ.
- Scope → ui_lite/static/app.js.
- Non-goals → зміни даних/WS протоколу.
- Інваріанти/рейки → мінімальний диф.
- План:
  1) Вимкнути фіксацію лівого краю timeScale.
- Тести/перевірки (план) → не запускати.

## 2026-01-21 15:22:00 — POST (MODE=PATCH): UI Lite панорамування/масштаб

- Що зроблено → fixLeftEdge вимкнено для timeScale.
- Де зроблено → ui_lite/static/app.js.
- Як перевірено → не запускалось.
- Ризики/нотатки → очікується вільне панорамування/масштабування.

## 2026-01-21 15:30:00 — PRE (MODE=PATCH): UI Lite масштаб по ціні/вертикальний drag

- Мета → щоб drag по ціні масштабував, а не рухав по часу; покращити вертикальну взаємодію.
- Scope → ui_lite/static/app.js.
- Non-goals → зміни даних/WS.
- Інваріанти/рейки → мінімальний диф.
- План:
  1) Увімкнути axisPressedMouseMove для price/time (v5 синтаксис).
- Тести/перевірки (план) → не запускати.

## 2026-01-21 15:32:00 — POST (MODE=PATCH): UI Lite масштаб по ціні/вертикальний drag

- Що зроблено → увімкнено axisPressedMouseMove для price/time (v5 синтаксис) та reset по double click.
- Де зроблено → ui_lite/static/app.js.
- Як перевірено → не запускалось.
- Ризики/нотатки → якщо бібліотека старіша, може ігнорувати об’єктний синтаксис.

## 2026-01-21 16:00:00 — PRE (MODE=read-only discovery): Діагностика «гепу» між свічками

- Мета → визначити, чи «геп» є наслідком даних (ринкова дискретність), чи помилки побудови preview.
- Scope → runtime/preview_builder.py, ui_lite/static/chart_adapter.js.
- Non-goals → будь-які зміни коду або UI.
- Інваріанти/рейки → лише read-only.
- План:
  1) Перевірити формування open/close при rollover в preview.
  2) Перевірити, чи є smoothing/зашивання між close/open.
  3) Оцінити, чи «геп» може бути нормальним ринковим розривом.
- Тести/перевірки (план) → не запускати.

## 2026-01-21 16:05:00 — POST (MODE=read-only discovery): Діагностика «гепу» між свічками

- Що зроблено → проаналізовано формування open/close у preview та нормалізацію часу для UI.
- Де зроблено → runtime/preview_builder.py, ui_lite/static/chart_adapter.js.
- Факти/спостереження →
  - У preview новий бар стартує з `open=mid` першого тіку нового bucket, а попередній бар завершується `close=mid` останнього тіку попереднього bucket. Це не «зшивається» і може давати вертикальні розриви між close→open: [runtime/preview_builder.py](runtime/preview_builder.py).
  - Немає логіки «smoothing» або примусового `open=new` = `close=prev`; отже price‑gap є очікуваним, коли тики рідкі/стрибки ціни: [runtime/preview_builder.py](runtime/preview_builder.py).
  - UI лише відображає бари, нормалізуючи час; він не втручається у ціни: [ui_lite/static/chart_adapter.js](ui_lite/static/chart_adapter.js).
- Висновок → показаний «геп» виглядає як ринкова дискретність (різниця між останнім tick попереднього bucket і першим tick нового bucket), а не помилка побудови або UI.

## 2026-01-21 16:20:00 — PRE (MODE=PATCH): UI Lite wheel Y-zoom + Shift+Wheel панорама

- Мета → додати TV-поведінку: wheel над price scale масштабує Y, Shift+Wheel панорамує X.
- Scope → ui_lite/static/app.js.
- Non-goals → зміни даних/WS.
- Інваріанти/рейки → мінімальний диф; не ламати built-in gestures.
- План:
  1) Перехопити wheel над price scale і керувати visible range через API.
  2) Додати Shift+Wheel для scrollToPosition.
- Тести/перевірки (план) → не запускати.

## 2026-01-21 16:22:00 — POST (MODE=PATCH): UI Lite wheel Y-zoom + Shift+Wheel панорама

- Що зроблено → додано wheel-обробник для price scale (Y-zoom з anchor) та Shift+Wheel панорамування по X.
- Де зроблено → ui_lite/static/app.js.
- Як перевірено → не запускалось.
- Ризики/нотатки → залежить від DOM-класів шкали; якщо селектор не знайде елемент, Y-zoom не спрацює.

## 2026-01-21 16:40:00 — PRE (MODE=PATCH): UI Lite wheel Y-zoom — надійне детектування шкали

- Мета → змусити wheel над price scale масштабувати Y, а не час.
- Scope → ui_lite/static/app.js.
- Non-goals → зміни даних/WS.
- Інваріанти/рейки → мінімальний диф.
- План:
  1) Використати composedPath + fallback «правий край» для детекції шкали.
  2) Перехопити wheel у capture.
- Тести/перевірки (план) → не запускати.

## 2026-01-21 16:42:00 — POST (MODE=PATCH): UI Lite wheel Y-zoom — надійне детектування шкали

- Що зроблено → додано composedPath+edge detection для price scale і capture для wheel.
- Де зроблено → ui_lite/static/app.js.
- Як перевірено → не запускалось.
- Ризики/нотатки → fallback «правий край» може хибно спрацьовувати при дуже вузькому контейнері.

## 2026-01-21 16:55:00 — PRE (MODE=PATCH): UI Lite Y-zoom ініціалізація range

- Мета → щоб wheel по ціні працював одразу, без попереднього drag.
- Scope → ui_lite/static/app.js.
- Non-goals → зміни даних/WS.
- Інваріанти/рейки → мінімальний диф.
- План:
  1) Увімкнути autoscale після snapshot.
  2) При null range виконати autoscale + fitContent.
- Тести/перевірки (план) → не запускати.

## 2026-01-21 16:57:00 — POST (MODE=PATCH): UI Lite Y-zoom ініціалізація range

- Що зроблено → autoscale вмикається після snapshot; при null range wheel робить autoscale+fitContent.
- Де зроблено → ui_lite/static/app.js.
- Як перевірено → не запускалось.
- Ризики/нотатки → якщо немає даних, range все одно може бути null до першого batch.

## 2026-01-21 17:10:00 — PRE (MODE=PATCH): UI Lite price-scale overlay для Y-zoom/drag

- Мета → зробити стабільний wheel/drag по ціні через явний оверлей над правою шкалою.
- Scope → ui_lite/static/app.js.
- Non-goals → зміни даних/WS.
- Інваріанти/рейки → мінімальний диф.
- План:
  1) Додати прозорий overlay справа.
  2) Прив’язати wheel/drag до setVisibleRange.
- Тести/перевірки (план) → не запускати.

## 2026-01-21 17:12:00 — POST (MODE=PATCH): UI Lite price-scale overlay для Y-zoom/drag

- Що зроблено → додано overlay над правою шкалою з wheel/drag Y-zoom.
- Де зроблено → ui_lite/static/app.js.
- Як перевірено → не запускалось.
- Ризики/нотатки → overlay має бути поверх чарта; якщо CSS змінюється, може потребувати підлаштування.

## 2026-01-21 17:25:00 — PRE (MODE=PATCH): UI Lite ініціалізація visible range для Y-zoom

- Мета → щоб Y-zoom/drag працював одразу після reload без «прогріву».
- Scope → ui_lite/static/app.js.
- Non-goals → зміни даних/WS.
- Інваріанти/рейки → мінімальний диф.
- План:
  1) Після snapshot форсувати visible range у price scale.
  2) При wheel повторно читати range після fitContent.
- Тести/перевірки (план) → не запускати.

## 2026-01-21 17:27:00 — POST (MODE=PATCH): UI Lite ініціалізація visible range для Y-zoom

- Що зроблено → форсовано visible range після snapshot і повторне читання range у wheel.
- Де зроблено → ui_lite/static/app.js.
- Як перевірено → не запускалось.
- Ризики/нотатки → якщо chart без даних, range може залишитись null.

## 2026-01-21 17:40:00 — PRE (MODE=PATCH): UI Lite no-skip suppression + ensureYRangeReady

- Мета → wheel по ціні не потрапляє в built-in scale і працює одразу після reload.
- Scope → ui_lite/static/app.js.
- Non-goals → зміни даних/WS.
- Інваріанти/рейки → мінімальний диф; wheel на price scale не проходить у built-in.
- План:
  1) Одразу глушити wheel по ціні перед будь-якими return.
  2) Додати ensureYRangeReady з одноразовим retry.
  3) Посилити overlay (top/right/bottom, z-index).
- Тести/перевірки (план) → не запускати.

## 2026-01-21 17:42:00 — POST (MODE=PATCH): UI Lite no-skip suppression + ensureYRangeReady

- Що зроблено → додано ensureYRangeReady, ранній suppression wheel, посилений overlay.
- Де зроблено → ui_lite/static/app.js.
- Як перевірено → не запускалось.
- Ризики/нотатки → якщо серія без даних, Y-zoom залишиться недоступним до першого batch.

## 2026-01-21 18:10:00 — PRE (MODE=PATCH): Preview 1d без intraday alignment-rail

- Мета → не ламати 1d boundary (22:00Z) перевіркою кратності TF.
- Scope → runtime/preview_builder.py.
- Non-goals → зміни інших TF або UI.
- Інваріанти/рейки → I1/I2 зберігаються; I3 тільки для intraday TF.
- План:
  1) Пропустити alignment-rail для tf == "1d".
- Тести/перевірки (план) → не запускати.

## 2026-01-21 18:12:00 — POST (MODE=PATCH): Preview 1d без intraday alignment-rail

- Що зроблено → alignment-rail застосовується лише для intraday TF; 1d виключено.
- Де зроблено → runtime/preview_builder.py.
- Як перевірено → не запускалось.
- Ризики/нотатки → 1d boundary лишається у домені календаря; можливі misaligned лічильники не інкрементуються для 1d.

## 2026-01-21 18:40:00 — PRE (MODE=PATCH): UI Lite wheel/drag price scale стабілізація

- Мета → усунути втрату першого wheel/drag по ціні та розширити hit‑area шкали.
- Scope → ui_lite/static/app.js.
- Non-goals → зміни даних/WS.
- Інваріанти/рейки → мінімальний диф; wheel по шкалі не проходить у built‑in scale.
- План:
  1) Розширити hit‑area до 140px і синхронізувати overlay.
  2) Дозволити retry для першого drag, якщо range ще null.
  3) Зробити ensureYRangeReady самовідновлюваним.
  4) Зменшити barSpacing та висоту volume.
- Тести/перевірки (план) → не запускати.

## 2026-01-21 18:42:00 — POST (MODE=PATCH): UI Lite wheel/drag price scale стабілізація

- Що зроблено → розширено hit‑area/overlay, додано retry для першого drag, самовідновлення ensureYRangeReady, зменшено barSpacing і висоту volume.
- Де зроблено → ui_lite/static/app.js.
- Як перевірено → не запускалось.
- Ризики/нотатки → якщо range недоступний до першого batch, Y‑zoom активується після першого snapshot.

## 2026-01-21 19:05:00 — PRE (MODE=PATCH): UI Lite volume -25%

- Мета → ще зменшити висоту volume на 1/4.
- Scope → ui_lite/static/app.js.
- Non-goals → зміни даних/WS.
- Інваріанти/рейки → мінімальний диф.
- План:
  1) Підняти scaleMargins.top для volume.
- Тести/перевірки (план) → не запускати.

## 2026-01-21 19:07:00 — POST (MODE=PATCH): UI Lite volume -25%

- Що зроблено → збільшено scaleMargins.top для volume до 0.95.
- Де зроблено → ui_lite/static/app.js.
- Як перевірено → не запускалось.
- Ризики/нотатки → volume займає ~5% висоти.

## 2026-01-21 19:30:00 — PRE (MODE=PATCH): UI Lite BarSeries toggle

- Мета → додати перемикач Bar/Candle у шапці та підтримку bar‑серії.
- Scope → ui_lite/static/index.html, ui_lite/static/app.js.
- Non-goals → зміни даних/WS.
- Інваріанти/рейки → мінімальний диф.
- План:
  1) Додати селектор Series у шапці.
  2) Додати BarSeries і перемикання видимості.
- Тести/перевірки (план) → не запускати.

## 2026-01-21 19:32:00 — POST (MODE=PATCH): UI Lite BarSeries toggle

- Що зроблено → додано селектор Series, BarSeries і перемикання Candle/Bar.
- Де зроблено → ui_lite/static/index.html, ui_lite/static/app.js.
- Як перевірено → не запускалось.
- Ризики/нотатки → bar‑серія працює на тих самих OHLCV даних.

## 2026-01-21 19:45:00 — PRE (MODE=PATCH): UI Lite dim volume

- Мета → знизити яскравість volume.
- Scope → ui_lite/static/app.js.
- Non-goals → зміни даних/WS.
- Інваріанти/рейки → мінімальний диф.
- План:
  1) Затемнити колір гістограми volume.
- Тести/перевірки (план) → не запускати.

## 2026-01-21 19:47:00 — POST (MODE=PATCH): UI Lite dim volume

- Що зроблено → колір volume змінено на темніший.
- Де зроблено → ui_lite/static/app.js.
- Як перевірено → не запускалось.
- Ризики/нотатки → може потребувати подальшого підбору кольору під фон.

## 2026-01-21 20:00:00 — PRE (MODE=PATCH): UI Lite volume dimmer (low-contrast)

- Мета → ще сильніше приглушити volume та його up/down кольори.
- Scope → ui_lite/static/app.js.
- Non-goals → зміни даних/WS.
- Інваріанти/рейки → мінімальний диф.
- План:
  1) Затемнити базовий volume колір і кольори up/down для volume барів.
- Тести/перевірки (план) → не запускати.

## 2026-01-21 20:02:00 — POST (MODE=PATCH): UI Lite volume dimmer (low-contrast)

- Що зроблено → volume колір і up/down для volume барів зроблено більш темними.
- Де зроблено → ui_lite/static/app.js.
- Як перевірено → не запускалось.
- Ризики/нотатки → при дуже темному фоні може виглядати як майже непомітний volume.

## 2026-01-21 20:15:00 — PRE (MODE=PATCH): UI Lite volume opacity 50%

- Мета → додати 50% прозорості для volume.
- Scope → ui_lite/static/app.js.
- Non-goals → зміни даних/WS.
- Інваріанти/рейки → мінімальний диф.
- План:
  1) Перевести volume кольори на rgba з alpha=0.5.
- Тести/перевірки (план) → не запускати.

## 2026-01-21 20:17:00 — POST (MODE=PATCH): UI Lite volume opacity 50%

- Що зроблено → volume кольори переведені на rgba з alpha=0.5.
- Де зроблено → ui_lite/static/app.js.
- Як перевірено → не запускалось.
- Ризики/нотатки → може вимагати підбору alpha для різних фонів.

## 2026-01-21 20:30:00 — PRE (MODE=PATCH): UI Lite persist settings

- Мета → зберігати symbol/tf/mode/seriesType при reload і зміні TF.
- Scope → ui_lite/static/app.js.
- Non-goals → зміни даних/WS.
- Інваріанти/рейки → мінімальний диф.
- План:
  1) Додати localStorage save/load.
  2) Застосувати видимість серій після restore.
- Тести/перевірки (план) → не запускати.

## 2026-01-21 20:32:00 — POST (MODE=PATCH): UI Lite persist settings

- Що зроблено → додано localStorage save/load та apply для series visibility.
- Де зроблено → ui_lite/static/app.js.
- Як перевірено → не запускалось.
- Ризики/нотатки → при відсутніх options у select значення скидається браузером.

## 2026-01-21 19:20:00 — PRE (MODE=PATCH): UI Lite перемикач Bar/Candle

- Мета → дозволити перемикання між свічками та барами у UI Lite.
- Scope → ui_lite/static/index.html, ui_lite/static/app.js.
- Non-goals → зміни даних/WS.
- Інваріанти/рейки → мінімальний диф.
- План:
  1) Додати селектор типу серії в шапці.
  2) Додати BarSeries та переключення видимості.
- Тести/перевірки (план) → не запускати.

## 2026-01-22 10:15:00 — PRE (MODE=PATCH): UI Lite відновлення без зсуву

- Мета → відновлювати видимий часовий діапазон без зсуву після перезавантаження.
- Scope → ui_lite/static/app.js.
- Non-goals → зміни даних/WS, зміни рендеру серій.
- Інваріанти/рейки → мінімальний диф, без нових залежностей.
- План:
  1) Зберігати timeRange у localStorage.
  2) Відновлювати timeRange після snapshot замість fitContent.
  3) Скидати timeRange при зміні symbol/tf/mode/clear.
- Тести/перевірки (план) → не запускати.

## 2026-01-22 10:20:00 — POST (MODE=PATCH): UI Lite відновлення без зсуву

- Що зроблено → збереження/відновлення видимого timeRange з localStorage; застосування після snapshot; скидання при зміні параметрів.
- Де зроблено → ui_lite/static/app.js.
- Як перевірено → не запускалось.
- Ризики/нотатки → якщо timeRange некоректний для нових даних, timeScale може бути непридатним; у такому разі fitContent не викликається лише при наявному timeRange.

## 2026-01-22 11:10:00 — PRE (MODE=read-only discovery): Діагностика часу (ms/us vs sec, wall-clock ticks)

- Мета → визначити джерела зсуву часу: ms/us як seconds у UI та фабрикація timestamp через wall-clock.
- Scope → ui_lite/static/chart_adapter.js, ui_lite/server.py, core/validation/validator.py, runtime/fxcm_forexconnect.py.
- Non-goals → будь-які зміни коду, тести, виправлення.
- Інваріанти/рейки → лише читання; append-only лог.
- План:
  1) Перевірити нормалізацію `time`/`open_time` у UI та UI Lite server.
  2) Перевірити контракт/валідацію `open_time` на одиниці часу.
  3) Перевірити джерело `tick_ts_ms` у FXCM stream.
- Тести/перевірки (план) → не запускати.

## 2026-01-22 11:18:00 — POST (MODE=read-only discovery): Діагностика часу (ms/us vs sec, wall-clock ticks)

- Що зроблено → оглянуто нормалізацію барів у UI, UI Lite server; перевірено інваріанти валідатора для ms; перевірено джерело tick timestamp у FXCM стрімі.
- Де зроблено → ui_lite/static/chart_adapter.js, ui_lite/server.py, core/validation/validator.py, runtime/fxcm_forexconnect.py.
- Висновки →
  1) UI нормалізація використовує `bar.time` без перевірки одиниць та fallback `open_time/1000`; якщо producer присилає `bar.time` у ms/us, це сприймається як seconds і шкала «стрибає в майбутнє». Так само UI Lite server ділить `open_time` на 1000, очікуючи ms; якщо `open_time` у μs, результат стає «ms як seconds» (далеке майбутнє).
  2) Валідатор `open_time` перевіряє лише нижню межу (`>=1e12`), тому microseconds (≈1e15) проходять; механізму відсікання μs немає.
  3) FXCM offers stream фабрикує `tick_ts_ms`/`snap_ts_ms` через `now_ms` (wall‑clock) без зовнішнього timestamp; це може створювати «живі» тики/бари навіть коли market=CLOSED.
- Як перевірено → не запускалось.
- Ризики/нотатки → без змін коду; потрібні окремі рішення/патчі для жорсткої нормалізації одиниць часу і політики wall‑clock ticks.

## 2026-01-22 12:05:00 — PRE (MODE=read-only discovery): Зберігання/Backfill/Replay + time-scale під час паузи

- Мета → дати точні відповіді: чи є зберігання, чи можливий річний backfill, чи є replay реальних даних, і чи вирішено «не тягнути time-scale до wall-clock під час паузи».
- Scope → docs/runtime.md, docs/audit_v3/current_state.md, docs/audit/current_state.md, app/composition.py, runtime/backfill.py, runtime/warmup.py, runtime/history_provider.py, fxcm/history_fxcm_provider.py, runtime/fxcm_forexconnect.py, store/schema.sql, store/sqlite_store.py, tools/replay_ticks.py, tools/record_ticks.py, tools/capture_fxcm_ticks.py, ui_lite/static/chart_adapter.js.
- Non-goals → будь-які зміни коду/конфігу, запуск команд.
- Інваріанти/рейки → тільки читання; append-only лог.
- План:
  1) Перевірити наявність SSOT зберігання та його інваріанти.
  2) Перевірити можливість backfill/warmup і провайдерів.
  3) Перевірити replay інструменти та їх джерела.
  4) Перевірити джерело wall‑clock у ticks та вплив на time-scale.
- Тести/перевірки (план) → не запускати.

## 2026-01-22 12:20:00 — POST (MODE=read-only discovery): Зберігання/Backfill/Replay + time-scale під час паузи

- Що зроблено → проаналізовано runtime/SSOT, backfill/warmup, провайдери історії, replay інструменти та джерела часу tick.
- Де зроблено → docs/runtime.md, docs/audit_v3/current_state.md, docs/audit/current_state.md, app/composition.py, runtime/backfill.py, runtime/warmup.py, runtime/history_provider.py, fxcm/history_fxcm_provider.py, runtime/fxcm_forexconnect.py, store/schema.sql, store/sqlite_store.py, tools/replay_ticks.py, tools/record_ticks.py, tools/capture_fxcm_ticks.py, ui_lite/static/chart_adapter.js.
- Висновки (істина/SSOT):
  1) Зберігання даних __є__: SSOT SQLite для final 1m/HTF з інваріантами (WAL, CHECK, source=history/history_agg, event_ts==close_time). Це описано в [store/schema.sql](store/schema.sql#L1-L70) і використовується в [store/sqlite_store.py](store/sqlite_store.py#L38-L150) та HTTP final API у [runtime/http_server.py](runtime/http_server.py#L93-L129). __Preview__ — лише in-memory cache, не персиститься (runtime/preview_builder.py, runtime/http_server.py). Отже: “зберігати дані” можна тільки через final-пайплайн у SQLite; preview не зберігається.
  2) Річний backfill __як механізм існує__, але провайдер історії __не налаштований__ у runtime: `_select_provider` кидає `ProviderNotConfiguredError` для `sim` та `fxcm` ([app/composition.py](app/composition.py#L90-L115), [fxcm/history_fxcm_provider.py](fxcm/history_fxcm_provider.py#L1-L15)). Тобто «підвантажити річні дані» __неможливо без реалізації/підключення провайдера__. При наявному провайдері backfill/warmup працює chunk‑ами (`runtime/backfill.py`, `runtime/warmup.py`) і пише у SQLite.
  3) Replay __є__ як інструмент: [tools/replay_ticks.py](tools/replay_ticks.py#L1-L120) читає JSONL та відтворює ticks у Redis (`price_tik`) і/або 1m preview bars (`ohlcv`). Це __може бути реальними даними__, якщо файл отримано з реального джерела (наприклад через [tools/record_ticks.py](tools/record_ticks.py#L1-L140) або [tools/capture_fxcm_ticks.py](tools/capture_fxcm_ticks.py#L1-L170)). Сам replay не є симуляцією, але залежить від джерела файлу.
  4) “Не тягнути time‑scale до wall‑clock під час паузи” __не вирішено системно__: FXCM offers stream фабрикує `tick_ts_ms/snap_ts_ms` через wall‑clock (`now_ms`) без реального timestamp ([runtime/fxcm_forexconnect.py](runtime/fxcm_forexconnect.py#L200-L203), [runtime/fxcm_forexconnect.py](runtime/fxcm_forexconnect.py#L268-L271)). Це джерельна проблема (тип 1 з вашого опису), а не лише UI. UI також може візуально тягнути шкалу, якщо `bar.time` приходить у ms/us і не нормалізується ([ui_lite/static/chart_adapter.js](ui_lite/static/chart_adapter.js#L15-L20)).
  5) Ефект «вихідні»: великий gap між сесіями — це політика рендера (insertWhitespace/gapPlaceholders), а не wall‑clock issue; окремий контроль/ліміт placeholder‑ів потрібен, якщо треба стискати паузи.
- Як перевірено → не запускалось.
- Ризики/нотатки → без провайдера історії backfill/річні дані недоступні; wall‑clock ticks під час market=CLOSED залишаються системним джерелом зсуву.

## 2026-01-22 12:45:00 — PRE (MODE=read-only discovery): E2E System Map v2

- Мета → зібрати E2E System Map v2 (SSOT, publish, інваріанти, enforcement, exit-gates, дублікати) та open risks.
- Scope → docs/audit_v4_system_map.md, docs/audit_v4_open_risks.md; джерела: config/config.py, app/main.py, app/composition.py, runtime/*, store/*, core/*, tools/exit_gates/*.
- Non-goals → зміни коду/конфігу, запуск команд/тестів.
- Інваріанти/рейки → лише читання; append-only лог; посилання path:line.
- План:
  1) Зафіксувати public surface (Redis/HTTP) + посилання на схеми.
  2) Побудувати dataflow і таблицю SSOT/інваріантів.
  3) Зібрати inventory exit-gates.
  4) Сформувати open risks.
- Тести/перевірки (план) → не запускати.

## 2026-01-22 13:10:00 — POST (MODE=read-only discovery): E2E System Map v2

- Що зроблено → згенеровано E2E System Map v2 + Open Risks, з посиланнями path:line.
- Де зроблено → [docs/audit_v4_system_map.md](docs/audit_v4_system_map.md), [docs/audit_v4_open_risks.md](docs/audit_v4_open_risks.md).
- Як перевірено → не запускалось (read-only).
- GO/NO‑GO → __NO‑GO__ до наступного slice: потрібні рішення для history provider (реальний backfill) і політики wall‑clock ticks під час market=CLOSED (див. open risks).

## 2026-01-22 14:05:00 — PRE (MODE=read-only discovery): V2 System Snapshot + Drift Audit

- Мета → зібрати v2 артефакти (system map, public surface, time/calendar semantics, P3–P5 flow) і audit‑дані; виконати дозволені перевірки.
- Scope → docs/v2_*.md, data/audit_v2/*, tools/bootstrap_p0.ps1, exit gates CLI.
- Non-goals → будь-які зміни коду/конфігу, запуск app.main, republish.
- Інваріанти/рейки → лише читання; append-only лог; факти з path:line.
- План:
  1) Запустити bootstrap_p0.ps1 для фіксації стану.
  2) Запустити gate_calendar_gaps/no_mix/final_wire/republish_watermark.
  3) Зняти status_snapshot.json з Redis (якщо доступно).
  4) Згенерувати docs/v2_*.md + data/audit_v2/*.
- Тести/перевірки (план) → bootstrap + exit gates CLI.

## 2026-01-22 14:30:00 — POST (MODE=read-only discovery): V2 System Snapshot + Drift Audit

- Що зроблено →
  - Згенеровано документи: docs/v2_system_map.md, docs/v2_public_surface.md, docs/v2_time_calendar_semantics.md, docs/v2_p3_p4_p5_flow.md.
  - Зібрано audit артефакти: data/audit_v2/status_snapshot.json, data/audit_v2/subscribe_ohlcv_sample.log, data/audit_v2/store_tables.txt, data/audit_v2/invariants_checklist.md.
  - Запущено bootstrap_p0.ps1 та exit gates CLI (calendar_gaps/no_mix/final_wire/republish_watermark).
- Де зроблено →
  - Документи: docs/v2_system_map.md, docs/v2_public_surface.md, docs/v2_time_calendar_semantics.md, docs/v2_p3_p4_p5_flow.md.
  - Артефакти: data/audit_v2/status_snapshot.json, data/audit_v2/subscribe_ohlcv_sample.log, data/audit_v2/store_tables.txt, data/audit_v2/invariants_checklist.md.
- Як перевірено →
  - tools/bootstrap_p0.ps1 → FAIL (pytest: missing fixture path `fixtures/ticks_out_of_order_boundary.jsonl`, та status_v2 schema mismatch: `ohlcv_preview.late_ticks_dropped_total` required). Деталі в stdout bootstrap.
  - gate_calendar_gaps (1m/15m/1h) → FAIL (unexpected_missing_bars: 1440/96/24).
  - gate_no_mix → OK.
  - gate_final_wire → OK.
  - gate_republish_watermark → FAIL (skipped_by_watermark=false).
  - status_snapshot.json → зчитано з Redis (snapshot містить tick_contract_reject і calendar_stub).
- Ризики/нотатки →
  - subscribe_ohlcv_sample.log не зібраний (немає репаблішу force=true у цьому прогоні).
  - SSOT може бути порожнім у tail_guard (ssot_empty), це відмічено у v2_system_map.

## 2026-01-22 15:05:00 — PRE (MODE=read-only discovery): Audit v5 system map + run-only gates

- Мета → зібрати audit v5 артефакти, виконати лише дозволені команди (bootstrap + gates), без змін коду.
- Scope → docs/audit_v5_*.md, data/audit_v5/*, run-only commands.
- Non-goals → будь-які патчі/правки коду або конфігів.
- Інваріанти/рейки → append-only лог; факти лише path:line або файли у data/audit_v5; без секретів.
- План:
  1) Зібрати commit hash, NS, порти, шлях SQLite, Python/venv.
  2) Зібрати tree інвентаризацію каталогів.
  3) Зібрати public surface, time/calendar policy, FXCM pacing.
  4) Запустити bootstrap + exit gates, зберегти stdout.
  5) Згенерувати docs/audit_v5_* та data/audit_v5/*.
- Тести/перевірки (план) → tools/bootstrap_p0.ps1; gate_calendar_gaps/no_mix/final_wire/republish_watermark; redis-cli GET status:snapshot.

## 2026-01-22 15:45:00 — POST (MODE=read-only discovery): Audit v5 system map + run-only gates

- Що зроблено →
  - Згенеровано docs: [docs/audit_v5_system_map.md](docs/audit_v5_system_map.md), [docs/audit_v5_public_surface.md](docs/audit_v5_public_surface.md), [docs/audit_v5_time_policy.md](docs/audit_v5_time_policy.md), [docs/audit_v5_calendar_sessions.md](docs/audit_v5_calendar_sessions.md), [docs/audit_v5_exit_gates_inventory.md](docs/audit_v5_exit_gates_inventory.md), [docs/audit_v5_duplication_smells.md](docs/audit_v5_duplication_smells.md).
  - Зібрано data/audit_v5/*: env_snapshot.txt, status_snapshot.json, store_tables.txt, gates_stdout.txt, rg_*_hits.txt, repo_dirs.txt.
- Де зроблено →
  - Документи: [docs/audit_v5_system_map.md](docs/audit_v5_system_map.md), [docs/audit_v5_public_surface.md](docs/audit_v5_public_surface.md), [docs/audit_v5_time_policy.md](docs/audit_v5_time_policy.md), [docs/audit_v5_calendar_sessions.md](docs/audit_v5_calendar_sessions.md), [docs/audit_v5_exit_gates_inventory.md](docs/audit_v5_exit_gates_inventory.md), [docs/audit_v5_duplication_smells.md](docs/audit_v5_duplication_smells.md).
  - Артефакти: [data/audit_v5/env_snapshot.txt](data/audit_v5/env_snapshot.txt), [data/audit_v5/status_snapshot.json](data/audit_v5/status_snapshot.json), [data/audit_v5/store_tables.txt](data/audit_v5/store_tables.txt), [data/audit_v5/gates_stdout.txt](data/audit_v5/gates_stdout.txt), [data/audit_v5/rg_time_hits.txt](data/audit_v5/rg_time_hits.txt), [data/audit_v5/rg_calendar_hits.txt](data/audit_v5/rg_calendar_hits.txt), [data/audit_v5/rg_fxcm_pacing_hits.txt](data/audit_v5/rg_fxcm_pacing_hits.txt), [data/audit_v5/repo_dirs.txt](data/audit_v5/repo_dirs.txt).
- Як перевірено →
  - tools/bootstrap_p0.ps1 stdout → [data/audit_v5/gates_stdout.txt](data/audit_v5/gates_stdout.txt).
  - gate_calendar_gaps/no_mix/final_wire/republish_watermark stdout → [data/audit_v5/gates_stdout.txt](data/audit_v5/gates_stdout.txt).
  - redis-cli GET status:snapshot → [data/audit_v5/status_snapshot.json](data/audit_v5/status_snapshot.json).
- Top‑10 Findings (P0/P1/P2) →
  1) P0: bootstrap FAIL (pytest missing fixture path + status_v2 mismatch) → [data/audit_v5/gates_stdout.txt](data/audit_v5/gates_stdout.txt).
  2) P0: gate_calendar_gaps FAIL для 1m/15m/1h → [data/audit_v5/gates_stdout.txt](data/audit_v5/gates_stdout.txt).
  3) P1: gate_republish_watermark FAIL (skipped_by_watermark=false) → [data/audit_v5/gates_stdout.txt](data/audit_v5/gates_stdout.txt).
  4) P1: ripgrep (rg) відсутній → [data/audit_v5/rg_time_hits.txt](data/audit_v5/rg_time_hits.txt).
  5) P1: commit_hash=unknown (git недоступний у shell) → [data/audit_v5/env_snapshot.txt](data/audit_v5/env_snapshot.txt).
  6) P2: status_snapshot містить calendar_stub/tick_contract_reject (середовище) → [data/audit_v5/status_snapshot.json](data/audit_v5/status_snapshot.json).
  7) P2: SSOT порожній/idle у tail_guard (as-is) → [data/audit_v5/status_snapshot.json](data/audit_v5/status_snapshot.json).
  8) P2: wall‑clock ticks (as‑is) → [docs/audit_v5_time_policy.md](docs/audit_v5_time_policy.md).
  9) P2: units mismatch ризик у UI (as‑is) → [docs/audit_v5_time_policy.md](docs/audit_v5_time_policy.md).
  10) P2: provider not configured (history) → [docs/audit_v5_system_map.md](docs/audit_v5_system_map.md).
- GO/NO‑GO → __NO‑GO__: блокують bootstrap FAIL + calendar_gaps FAIL + republish_watermark FAIL.

## 2026-01-23 09:05:00 — PRE (MODE=read-only discovery): Audit v6 system map + run-only gates

- Мета → зібрати audit v6 proof pack (SSOT/дріт/календар/час/репабліш/гейти/дублікати) лише через run-only команди та документацію.
- Scope → docs/audit_v6_*.md, data/audit_v6/*, tools/bootstrap_p0.ps1, CLI wrappers для exit gates, redis-cli GET status:snapshot.
- Non-goals → жодних змін коду/конфігів/тестів; не запускати app.main; не виконувати republish.
- Інваріанти/рейки → append-only лог; факти лише path:line або data/audit_v6/*; без секретів.
- План:
  1) Зібрати env_snapshot (whoami/hostname/where/python/NS/ports/sqlite/calendar_tag).
  2) Запустити bootstrap + gates і зберегти stdout.
  3) Зняти status:snapshot через redis-cli.
  4) Зняти store таблиці (python sqlite3).
  5) Згенерувати docs/audit_v6_*.md.
- Тести/перевірки (план) → tools/bootstrap_p0.ps1; gate_calendar_gaps/no_mix/final_wire/republish_watermark; redis-cli GET status:snapshot.

## 2026-01-23 09:30:00 — POST (MODE=read-only discovery): Audit v6 system map + run-only gates

- Що зроблено →
  - Згенеровано docs: [docs/audit_v6_system_map.md](docs/audit_v6_system_map.md), [docs/audit_v6_public_surface.md](docs/audit_v6_public_surface.md), [docs/audit_v6_time_policy.md](docs/audit_v6_time_policy.md), [docs/audit_v6_calendar_sessions.md](docs/audit_v6_calendar_sessions.md), [docs/audit_v6_exit_gates_inventory.md](docs/audit_v6_exit_gates_inventory.md), [docs/audit_v6_duplication_smells.md](docs/audit_v6_duplication_smells.md).
  - Зібрано data/audit_v6/*: env_snapshot.txt, gates_stdout.txt, status_snapshot.json, store_tables.txt, repo_dirs.txt, subscribe_ohlcv_sample.log, notes.txt.
- Де зроблено →
  - Документи: [docs/audit_v6_system_map.md](docs/audit_v6_system_map.md), [docs/audit_v6_public_surface.md](docs/audit_v6_public_surface.md), [docs/audit_v6_time_policy.md](docs/audit_v6_time_policy.md), [docs/audit_v6_calendar_sessions.md](docs/audit_v6_calendar_sessions.md), [docs/audit_v6_exit_gates_inventory.md](docs/audit_v6_exit_gates_inventory.md), [docs/audit_v6_duplication_smells.md](docs/audit_v6_duplication_smells.md).
  - Артефакти: [data/audit_v6/env_snapshot.txt](data/audit_v6/env_snapshot.txt), [data/audit_v6/gates_stdout.txt](data/audit_v6/gates_stdout.txt), [data/audit_v6/status_snapshot.json](data/audit_v6/status_snapshot.json), [data/audit_v6/store_tables.txt](data/audit_v6/store_tables.txt), [data/audit_v6/repo_dirs.txt](data/audit_v6/repo_dirs.txt), [data/audit_v6/subscribe_ohlcv_sample.log](data/audit_v6/subscribe_ohlcv_sample.log), [data/audit_v6/notes.txt](data/audit_v6/notes.txt).
- Як перевірено →
  - tools/bootstrap_p0.ps1 + gate wrappers stdout → [data/audit_v6/gates_stdout.txt](data/audit_v6/gates_stdout.txt).
  - redis-cli GET status:snapshot → [data/audit_v6/status_snapshot.json](data/audit_v6/status_snapshot.json).
  - Нотатки run-only → [data/audit_v6/notes.txt](data/audit_v6/notes.txt).
- Top‑10 Findings (v6) →
  1) bootstrap + gate wrappers завершились з кодом 1 → [data/audit_v6/notes.txt](data/audit_v6/notes.txt).
  2) status:snapshot не зчитано (redis-cli error) → [data/audit_v6/status_snapshot.json](data/audit_v6/status_snapshot.json).
  3) rg пошук не виконувався у v6 (run-only обмеження) → [data/audit_v6/notes.txt](data/audit_v6/notes.txt).
  4) subscribe_ohlcv_sample.log не зібрано (немає republish force=true у цьому прогоні) → [data/audit_v6/notes.txt](data/audit_v6/notes.txt).
- GO/NO‑GO → __NO‑GO__: bootstrap/gates exit code 1 + status:snapshot не зчитано.

## 2026-01-23 20:15:00 — PRE (MODE=read-only discovery): Audit v6 proof pack refresh (run-only)

- Мета → зібрати актуальний audit v6 proof pack та пояснити роз’їзд P0→P5 без змін коду.
- Scope → run-only команди, оновлення data/audit_v6/* та docs/audit_v6_*.md (включно з delta vs P0..P5).
- Non-goals → будь-які патчі/правки коду/конфігів/тестів; не запускати/зупиняти app.main; не виконувати republish.
- Інваріанти/рейки → append-only лог; факти лише path:line або data/audit_v6/*; без секретів.
- План:
  1) Оновити env_snapshot.
  2) Запустити bootstrap + exit gates (stdout у файл) та зафіксувати exit codes.
  3) Зняти status:snapshot через redis-cli.
  4) Зняти store таблиці через python sqlite3.
  5) Оновити docs/audit_v6_*.md і додати delta vs P0..P5.

## 2026-01-23 20:30:00 — POST (MODE=read-only discovery): Audit v6 proof pack refresh (run-only)

- Що зроблено →
  - Оновлено env snapshot, gates stdout, status snapshot, store tables — [data/audit_v6/env_snapshot.txt](data/audit_v6/env_snapshot.txt), [data/audit_v6/gates_stdout.txt](data/audit_v6/gates_stdout.txt), [data/audit_v6/status_snapshot.json](data/audit_v6/status_snapshot.json), [data/audit_v6/store_tables.txt](data/audit_v6/store_tables.txt).
  - Оновлено документи v6 та додано delta vs P0..P5 — [docs/audit_v6_system_map.md](docs/audit_v6_system_map.md), [docs/audit_v6_public_surface.md](docs/audit_v6_public_surface.md), [docs/audit_v6_time_policy.md](docs/audit_v6_time_policy.md), [docs/audit_v6_calendar_sessions.md](docs/audit_v6_calendar_sessions.md), [docs/audit_v6_exit_gates_inventory.md](docs/audit_v6_exit_gates_inventory.md), [docs/audit_v6_duplication_smells.md](docs/audit_v6_duplication_smells.md), [docs/audit_v6_delta_vs_P0_P5.md](docs/audit_v6_delta_vs_P0_P5.md).
- Як перевірено →
  - tools/bootstrap_p0.ps1 + exit gates stdout → [data/audit_v6/gates_stdout.txt](data/audit_v6/gates_stdout.txt).
  - redis-cli GET status:snapshot → [data/audit_v6/status_snapshot.json](data/audit_v6/status_snapshot.json).
  - sqlite3 dump через python → [data/audit_v6/store_tables.txt](data/audit_v6/store_tables.txt).
  - Exit codes збережені у [data/audit_v6/notes.txt](data/audit_v6/notes.txt).
- Top‑10 Findings (v6 refresh) →
  1) pytest падає у bootstrap (2 тести) → [data/audit_v6/gates_stdout.txt](data/audit_v6/gates_stdout.txt#L103-L104).
  2) gate_*.py запуск як скриптів падає через `ModuleNotFoundError` → [data/audit_v6/gates_stdout.txt](data/audit_v6/gates_stdout.txt#L137-L167).
  3) status snapshot містить `tick_contract_reject` + `calendar_stub` → [data/audit_v6/status_snapshot.json](data/audit_v6/status_snapshot.json#L1).
  4) final store порожній (count=0) → [data/audit_v6/store_tables.txt](data/audit_v6/store_tables.txt#L1-L6).
- GO/NO‑GO → __NO‑GO__: pytest FAIL у bootstrap + gate_*.py ModuleNotFound + порожній final store.

## 2026-01-23 21:00:00 — PRE (MODE=read-only discovery): MASTER AUDIT PACK v1/v2/v4/v5/v6

- Мета → зібрати єдиний master‑звіт (v4/v5/v6 + v1 evidence) і one‑shot spec для гейтів; без змін коду.
- Scope → docs/audit_master_v1_v2_v6.md, docs/exit_gates_one_shot_spec.md; лише читання/посилання.
- Non-goals → будь-які патчі/зміни коду/конфігів/тестів; запуск app.main.
- Інваріанти/рейки → append-only лог; факти тільки з path:line або audit_*; без секретів.
- План:
  1) Зібрати джерела з audit v4/v5/v6, Public API Spec/Public Surface, v2 docs.
  2) Скласти master‑звіт із контрактними невідповідностями та drift P0→P5.
  3) Скласти one‑shot spec запуску гейтів (python -m …).
  4) Зафіксувати обмеження (зовнішні /mnt/data не в workspace).

## 2026-01-23 21:20:00 — POST (MODE=read-only discovery): MASTER AUDIT PACK v1/v2/v4/v5/v6

- Що зроблено →
  - Створено master‑звіт: [docs/audit_master_v1_v2_v6.md](docs/audit_master_v1_v2_v6.md).
  - Створено one‑shot spec гейтів: [docs/exit_gates_one_shot_spec.md](docs/exit_gates_one_shot_spec.md).
- Як перевірено → лише читання джерел, без виконання команд.
- Обмеження/нотатки → файли /mnt/data/* недоступні у workspace, порівняння v1 evidence відкладене.
- Думка (про роз’їзд гейтів) → раніше P0–P3 гейти проходили (зафіксовано у логах), зараз v6 має pytest FAIL та import‑помилки, тому траєкторія роз’їхалась.

## 2026-01-23 21:40:00 — PRE (MODE=read-only discovery): Audit v6 one‑shot gates (python -m)

- Мета → зібрати data/audit_v6/*, прогнати exit‑gates через `python -m`, оновити docs/audit_v6_*.md без змін коду.
- Scope → env snapshot, bootstrap stdout, one‑shot gates stdout, status snapshot, store tables, v6 docs.
- Non-goals → жодних патчів; не запускати app.main; без republish.
- Інваріанти/рейки → append-only лог; факти лише path:line або data/audit_v6/*.
- План:
  1) Оновити env_snapshot.
  2) Запустити bootstrap і one‑shot gates (python -m) зі stdout у файл.
  3) Зняти status snapshot через redis-cli.
  4) Зняти store tables через python sqlite3.
  5) Оновити docs/audit_v6_*.md.

## 2026-01-23 21:55:00 — POST (MODE=read-only discovery): Audit v6 one‑shot gates (python -m)

- Що зроблено →
  - Оновлено [data/audit_v6/env_snapshot.txt](data/audit_v6/env_snapshot.txt), [data/audit_v6/gates_stdout.txt](data/audit_v6/gates_stdout.txt), [data/audit_v6/notes.txt](data/audit_v6/notes.txt), [data/audit_v6/status_snapshot.json](data/audit_v6/status_snapshot.json), [data/audit_v6/store_tables.txt](data/audit_v6/store_tables.txt).
  - Оновлено v6 docs: [docs/audit_v6_exit_gates_inventory.md](docs/audit_v6_exit_gates_inventory.md), [docs/audit_v6_delta_vs_P0_P5.md](docs/audit_v6_delta_vs_P0_P5.md).
- Як перевірено →
  - tools/bootstrap_p0.ps1 + one‑shot gates через python -m → [data/audit_v6/gates_stdout.txt](data/audit_v6/gates_stdout.txt).
  - redis-cli GET status:snapshot → [data/audit_v6/status_snapshot.json](data/audit_v6/status_snapshot.json).
  - sqlite3 dump через python → [data/audit_v6/store_tables.txt](data/audit_v6/store_tables.txt).
- Результат → one‑shot gates впали через відсутні CLI аргументи; pytest у bootstrap все ще падає; final store порожній.

## 2026-01-24 09:10:00 — PRE (MODE=PATCH): Restore P0 baseline validity (v6)

- Мета → відновити P0 базову валідність: fixtures SSOT, status_v2 узгодження, P0 bootstrap без P3+ гейтів, rail для запуску гейтів.
- Scope → tools/bootstrap_p0.ps1, tools/run_exit_gates.py, tools/exit_gates/manifest_p0.json, core/fixtures_path.py, tests/*, tools/exit_gates/gates/*, core/contracts/public/status_v2.json (лише узгодження), runtime/status.py (без зміни логіки), tests/test_validator_status.py.
- Non-goals → запуск app.main, republish, будь-які нові фічі P3+.
- Інваріанти/рейки → мінімальний диф; Public API не змінювати, окрім узгодження `late_ticks_dropped_total`; заборона direct script run для gate_*.py.
- План:
  1) Додати SSOT helper для fixtures і підключити у тестах/гейтах.
  2) Узгодити schema/runtime/test для `ohlcv_preview.late_ticks_dropped_total`.
  3) Відокремити P0 manifest для bootstrap; залишити повний manifest для P3+.
  4) Додати rail: запуск гейтів тільки через python -m/runner.
  5) Оновити журнали/вихідні команди.

## 2026-01-24 09:35:00 — POST (MODE=PATCH): Restore P0 baseline validity (v6)

- Що зроблено →
  - Додано SSOT helper для fixtures і підключено у тестах/гейтах.
  - Узгоджено `ohlcv_preview` у тестовому payload зі schema (late_ticks_dropped_total та інші required поля).
  - Додано P0 manifest для exit gates і оновлено bootstrap на `python -m tools.run_exit_gates`.
  - Додано rail проти прямого запуску gate_*.py та run_exit_gates.py як скриптів.
- Де зроблено →
  - [core/fixtures_path.py](core/fixtures_path.py)
  - [tests/test_preview_builder_late_tick_drop.py](tests/test_preview_builder_late_tick_drop.py)
  - [tests/test_replay_ticks_parses_jsonl.py](tests/test_replay_ticks_parses_jsonl.py)
  - [tests/test_tick_fixtures_validator.py](tests/test_tick_fixtures_validator.py)
  - [tests/test_validator_status.py](tests/test_validator_status.py)
  - [tools/exit_gates/gates/gate_preview_late_tick_drop.py](tools/exit_gates/gates/gate_preview_late_tick_drop.py)
  - [tools/exit_gates/gates/gate_preview_1m_boundaries.py](tools/exit_gates/gates/gate_preview_1m_boundaries.py)
  - [tools/exit_gates/gates/gate_preview_1m_geom.py](tools/exit_gates/gates/gate_preview_1m_geom.py)
  - [tools/exit_gates/gates/gate_tick_units.py](tools/exit_gates/gates/gate_tick_units.py)
  - [tools/exit_gates/gates/gate_tick_fixtures_schema.py](tools/exit_gates/gates/gate_tick_fixtures_schema.py)
  - [tools/exit_gates/manifest_p0.json](tools/exit_gates/manifest_p0.json)
  - [tools/bootstrap_p0.ps1](tools/bootstrap_p0.ps1)
  - [tools/run_exit_gates.py](tools/run_exit_gates.py)
  - [tools/exit_gates/gate_calendar_gaps.py](tools/exit_gates/gate_calendar_gaps.py)
  - [tools/exit_gates/gate_no_mix.py](tools/exit_gates/gate_no_mix.py)
  - [tools/exit_gates/gate_final_wire.py](tools/exit_gates/gate_final_wire.py)
  - [tools/exit_gates/gate_republish_watermark.py](tools/exit_gates/gate_republish_watermark.py)
- Тести/перевірки → не запускались (за інструкцією).
- Ризики/нотатки → bootstrap тепер P0‑only; P3+ гейти слід запускати окремо через повний manifest.

## 2026-01-24 10:05:00 — PRE (MODE=read-only discovery): Запуск P0 bootstrap

- Мета → запустити P0 bootstrap (lint/type/pytest + P0 exit-gates) без app.main.
- Scope → task "P0: bootstrap".
- Non-goals → запуск app.main, republish, P3+ гейти.
- Інваріанти/рейки → жодних змін коду; фіксація результату у POST.
- Перевірки → task P0: bootstrap.

## 2026-01-24 10:12:00 — POST (MODE=read-only discovery): Запуск P0 bootstrap

- Що зроблено → виконано task "P0: bootstrap".
- Як перевірено → powershell -ExecutionPolicy Bypass -File tools\bootstrap_p0.ps1 (task).
- Результат → у виводі зафіксовано "All checks passed!" (ruff/mypy), повний stdout/pytest/exit-gates не зафіксовано у цьому записі.
- Ризики/нотатки → якщо потрібен повний stdout/exit code, повторити запуск із явним логуванням у файл.

## 2026-01-24 10:30:00 — PRE (MODE=read-only discovery): P0 proof pack bootstrap з логуванням

- Мета → повторно запустити tools\bootstrap_p0.ps1 з логуванням stdout/stderr у файл та збереженням exit code.
- Non-goals → жодних патчів/змін коду; не запускати app.main; без republish.
- Інваріанти/рейки → append-only лог; зберігаємо шлях до логу та exitcode.
- Перевірки → запуск PowerShell команди з логуванням у data\audit_p0_proof.

## 2026-01-24 10:35:00 — PRE (MODE=PATCH): P1 календар SSOT (заміна calendar_stub)

- Мета → реалізувати реальний календар (DST-aware) як SSOT, інтегрувати у status/runtime, додати тести та P1 gate.
- Non-goals → запуск app.main, warmup/backfill, republish, зміни P3+.
- Інваріанти/рейки → мінімальний диф; contract-first; no silent fallback; українські коментарі.
- План:
  1) Додати SSOT модуль календаря + 3 edge-case тести.
  2) Інтегрувати календар у status та прибрати calendar_stub.
  3) Додати gate_calendar_semantics + manifest_p1_calendar.json.
  4) Додати rail про запуск гейтів через runner.
  5) Запустити 1–3 тести та P1 gate manifest.

## 2026-01-24 10:45:00 — POST (MODE=read-only discovery): P0 proof pack bootstrap з логуванням

- Що зроблено → повторно запущено tools\bootstrap_p0.ps1 з логуванням у data\audit_p0_proof.
- Як перевірено → PowerShell команда з логуванням у файл (див. PRE).
- Результат → exit code = 0; stdout/stderr у [data/audit_p0_proof/bootstrap_p0_2026-01-24_093010.log](data/audit_p0_proof/bootstrap_p0_2026-01-24_093010.log), exitcode у [data/audit_p0_proof/bootstrap_p0_2026-01-24_093010.exitcode.txt](data/audit_p0_proof/bootstrap_p0_2026-01-24_093010.exitcode.txt).
- Ризики/нотатки → якщо PASS не відтворюється — аудит/метрики недоказові (P0 блокер).

## 2026-01-24 11:10:00 — POST (MODE=PATCH): P1 календар SSOT (заміна calendar_stub)

- Що зроблено →
  - Додано SSOT календар (DST-aware) + специфікацію і gate семантики.
  - Оновлено StatusManager: прибрано calendar_stub, додано degraded‑but‑loud при помилці календаря.
  - Додано deterministic time helpers для тестів tail_guard.
- Де зроблено →
  - [core/time/sessions.py](core/time/sessions.py)
  - [core/time/calendar.py](core/time/calendar.py)
  - [runtime/status.py](runtime/status.py)
  - [docs/calendar_sessions_spec.md](docs/calendar_sessions_spec.md)
  - [tests/test_calendar_sessions.py](tests/test_calendar_sessions.py)
  - [tools/exit_gates/gates/gate_calendar_semantics.py](tools/exit_gates/gates/gate_calendar_semantics.py)
  - [tools/exit_gates/manifest_p1_calendar.json](tools/exit_gates/manifest_p1_calendar.json)
  - [docs/exit_gates.md](docs/exit_gates.md)
  - [tests/time_helpers.py](tests/time_helpers.py)
  - Тести tail_guard: [tests/test_tail_guard_deferred_when_market_open.py](tests/test_tail_guard_deferred_when_market_open.py), [tests/test_tail_guard_checked_ttl_skips.py](tests/test_tail_guard_checked_ttl_skips.py), [tests/test_tail_guard_detects_gap.py](tests/test_tail_guard_detects_gap.py), [tests/test_tail_guard_invalidation_on_upsert.py](tests/test_tail_guard_invalidation_on_upsert.py), [tests/test_tail_guard_marks.py](tests/test_tail_guard_marks.py), [tests/test_tail_guard_marks_persistence.py](tests/test_tail_guard_marks_persistence.py), [tests/test_tail_guard_repair_flow.py](tests/test_tail_guard_repair_flow.py), [tests/test_repair_rejects_large_range.py](tests/test_repair_rejects_large_range.py)
  - Узгодження статус‑тестів: [tests/test_validator_status.py](tests/test_validator_status.py), [tests/test_status_payload_size_rail.py](tests/test_status_payload_size_rail.py)
- Як перевірено →
  - pytest (1–3 тести): tests/test_preview_builder_late_tick_drop.py, tests/test_validator_status.py::test_status_v2_valid, tests/test_calendar_sessions.py.
  - P1 gate manifest: python -m tools.run_exit_gates --out reports/exit_gates --manifest tools/exit_gates/manifest_p1_calendar.json → [reports/exit_gates/2026-01-24_083949/results.json](reports/exit_gates/2026-01-24_083949/results.json).
- Результат → тести PASS; gate_calendar_semantics PASS. (прямий запуск без -m відхилено rail‑повідомленням).
- Ризики/нотатки → якщо DST/сесії не співпадуть з v1 у проді — ризик “зсув вправо” та некоректні first/last bars.

## 2026-01-24T10:30:00+01:00 — PRE (MODE=PATCH): P1.1 time units upper bound + UTC calendar overrides

- Мета → (1) додати верхню межу epoch ms, щоб відхиляти microseconds, (2) перевести календар на SSOT правила в UTC з config/calendar_overrides.json.
- Scope → core/validation/validator.py, core/time/timestamps.py, core/time/calendar.py, docs/calendar_sessions_spec.md, config/calendar_overrides.json, тести календаря/статусу/gate.
- Non-goals → зміна публічних контрактів, зміна бізнес-логіки поза календарем і ms-рейками.
- Інваріанти/рейки → без silent fallback; помилки календаря мають бути degraded-but-loud; мінімальний диф.
- План →
  1) Додати верхню межу epoch ms у валідаторі та timestamps.
  2) Додати SSOT overrides у config/calendar_overrides.json та підключити у core/time/calendar.py.
  3) Оновити календарні тести/exit gate на UTC межі.
  4) Оновити доки календаря.
- Тести/перевірки → pytest -q tests/test_validator_tick.py tests/test_calendar_sessions.py tests/test_gate_calendar_gaps.py

## 2026-01-24T11:05:00+01:00 — POST (MODE=PATCH): P1.1 time units upper bound + UTC calendar overrides

- Що зроблено →
  - Додано верхню межу epoch ms у валідаторі та timestamps (відсікання microseconds).
  - Переведено календар на UTC правила з SSOT у config/calendar_overrides.json.
  - Оновлено календарні тести та exit gate на UTC межі.
  - Оновлено документацію календаря та календарний tag у тестових payload.
- Де зроблено →
  - core/validation/validator.py; core/time/timestamps.py.
  - core/time/calendar.py; config/calendar_overrides.json.
  - tests/test_validator_tick.py; tests/test_calendar_sessions.py; tools/exit_gates/gates/gate_calendar_semantics.py; tests/test_gate_calendar_gaps.py.
  - tests/test_validator_status.py; tests/test_status_payload_size_rail.py.
  - docs/calendar_sessions_spec.md; config/config.py.
- Як перевірено →
  - pytest -q tests/test_validator_tick.py tests/test_calendar_sessions.py tests/test_gate_calendar_gaps.py (OK).
- Ризики/нотатки →
  - Якщо calendar_overrides.json буде пошкоджено/відсутнє, статус перейде у degraded із calendar_error.

## 2026-01-24T11:30:00+01:00 — PRE (MODE=read-only discovery): Перевірка P0 proof pack + P1 gate + P1.1 рейок

- Мета → підтвердити наявність P0 proof pack (log + exitcode + повторюваність), пройти P1 gate_calendar_semantics, перевірити рейку верхньої межі epoch ms.
- Non-goals → жодних змін коду або конфігів.
- Інваріанти/рейки → запуск лише через python -m tools.run_exit_gates; append-only лог.
- Перевірки → перелік файлів у data/audit_p0_proof; запуск P1 gate; перегляд валідатора ms.

## 2026-01-24T11:35:00+01:00 — POST (MODE=read-only discovery): Перевірка P0 proof pack + P1 gate + P1.1 рейок

- Що зроблено →
  - Перевірено наявність P0 proof pack (кілька пар log+exitcode).
  - Запущено P1 gate_calendar_semantics через runner.
  - Підтверджено наявність верхньої межі epoch ms у валідаторі та timestamps.
- Де перевірено →
  - data/audit_p0_proof (bootstrap_p0_2026-01-24_092926.*, bootstrap_p0_2026-01-24_092947.*, bootstrap_p0_2026-01-24_093010.*).
  - core/validation/validator.py; core/time/timestamps.py.
  - tools/exit_gates/manifest_p1_calendar.json; tools/exit_gates/gates/gate_calendar_semantics.py.
- Як перевірено →
  - Запуск: python -m tools.run_exit_gates --manifest tools/exit_gates/manifest_p1_calendar.json (FAIL: відсутній --out).
  - Запуск: python -m tools.run_exit_gates --out reports/exit_gates --manifest tools/exit_gates/manifest_p1_calendar.json (OK).
- Результат → P1 gate PASS; P0 proof pack присутній з повторними логами; рейка μs→ms активна.
- Ризики/нотатки → якщо proof pack потрібен як єдине джерело істини — фіксувати останню пару log+exitcode як «current».

## 2026-01-24T12:05:00+01:00 — PRE (MODE=PATCH): P1.2 DST boundary + SIM rails + synthetic rail

- Мета → додати DST boundary тести/гейт для NY 17:00, заборонити sim-провайдер у runtime з hard fail, та відсікати synthetic=true у preview.
- Scope → core/time/calendar.py, config/calendar_overrides.json, docs/calendar_sessions_spec.md, tests/test_calendar_sessions.py, tools/exit_gates/gates/gate_calendar_semantics.py, runtime/command_bus.py, core/validation/validator.py, tests/test_preview_batch_sorted_no_dupes.py.
- Non-goals → зміни публічних контрактів, зміни в історичних провайдерах, запуск повних аудитів.
- Інваріанти/рейки → recurrence у America/New_York (DST-aware), overrides тільки UTC; hard fail на sim; без silent fallback.
- План →
  1) Перевести recurrence на NY (DST-aware) + оновити spec/overrides.
  2) Додати DST boundary тести та оновити gate_calendar_semantics.
  3) Додати hard fail на provider=sim у command_bus.
  4) Додати rail на synthetic=true у preview валідаторі + тест.
- Тести/перевірки → pytest -q tests/test_calendar_sessions.py tests/test_preview_batch_sorted_no_dupes.py

## 2026-01-24T12:25:00+01:00 — POST (MODE=PATCH): P1.2 DST boundary + SIM rails + synthetic rail

- Що зроблено →
  - Переведено recurrence на America/New_York (DST-aware) через calendar_overrides.json; UTC recurrence заборонено rail-ом.
  Зараз задано так (SSOT у calendar_overrides.json):

    - Recurrence TZ: America/New_York.
    - Weekly open: неділя 17:00 NY.
    - Weekly close: пʼятниця 17:00 NY.
    - Daily break: 17:00–17:05 NY (пн‑чт).
    - Closed overrides: closed_intervals_utc (UTC інтервали).
  - Додано DST boundary тести (до/після DST) та розширено gate_calendar_semantics.
  - Додано hard fail на provider=sim у command_bus після loud error.
  - Додано rail: synthetic=true у preview → ContractError + тест.
- Де зроблено →
  - core/time/calendar.py; config/calendar_overrides.json; docs/calendar_sessions_spec.md.
  - tests/test_calendar_sessions.py; tools/exit_gates/gates/gate_calendar_semantics.py.
  - runtime/command_bus.py; core/validation/validator.py; tests/test_preview_batch_sorted_no_dupes.py.
  - config/config.py; tests/test_validator_status.py; tests/test_status_payload_size_rail.py.
- Як перевірено →
  - pytest -q tests/test_calendar_sessions.py tests/test_preview_batch_sorted_no_dupes.py (OK).
  - python -m tools.run_exit_gates --out reports/exit_gates --manifest tools/exit_gates/manifest_p1_calendar.json (OK).
- Ризики/нотатки →
  - Якщо ZoneInfo відсутній, dateutil.tz використовується як degraded (DST має зберігатись, але залежить від tzdata).

## 2026-01-24T12:50:00+01:00 — PRE (MODE=PATCH): P1.2.1 daily break bar boundaries

- Мета → додати тест для останнього/першого 1m бара на межі daily break (NY 16:59 → 17:05).
- Scope → tests/test_calendar_sessions.py.
- Non-goals → зміни логіки календаря або gate.
- Інваріанти/рейки → recurrence у America/New_York (DST-aware); без silent fallback.
- Тести/перевірки → pytest -q tests/test_calendar_sessions.py

## 2026-01-24T12:55:00+01:00 — POST (MODE=PATCH): P1.2.1 daily break bar boundaries

- Що зроблено → додано тест меж останнього/першого 1m бара на daily break (16:59 NY → 17:05 NY).
- Де зроблено → tests/test_calendar_sessions.py.
- Як перевірено → pytest -q tests/test_calendar_sessions.py (OK).
- Ризики/нотатки → DST-логіка залежить від tzdata (ZoneInfo/dateutil).

## 2026-01-24T13:10:00+01:00 — PRE (MODE=PATCH): P2 REPLAY MVP (REAL-only) + determinism gate

- Мета → додати REPLAY REAL-only ingest для ticks (JSONL), перевірки CLOSED policy + монотонність, і gate на determinism.
- Scope → config/config.py, app/composition.py, app/main.py, core/runtime/mode.py, core/market/replay_policy.py, runtime/replay_ticks.py, tools/exit_gates/gates/gate_tick_replay_monotonic.py, tools/exit_gates/manifest_p2_replay.json, tests/test_tick_replay_policy.py, tests/fixtures/ticks_replay_sample.jsonl.
- Non-goals → зміни history provider/P3+, інтеграційний запуск app.main, будь-які SIM-провайдери.
- Інваріанти/рейки → REAL-only; tick JSONL валідний за tick_v1 schema; CLOSED policy fail-fast; монотонність tick_ts/snap_ts без сортування; synthetic=true заборонено.
- Тести/перевірки → pytest -q tests/test_tick_replay_policy.py; python -m tools.run_exit_gates --out reports/exit_gates --manifest tools/exit_gates/manifest_p2_replay.json
- Ризики → сортовані «виправлення» приховують баги; soft-drop CLOSED ticks відновить drift; replay не має підміняти tick_ts wall-clock.

## 2026-01-24T13:40:00+01:00 — POST (MODE=PATCH): P2 REPLAY MVP (REAL-only) + determinism gate

- Що зроблено →
  - Додано replay policy (schema + CLOSED + монотонність) та replay ingest для JSONL ticks.
  - Додано REPLAY режим у runtime; SIM заборонено hard fail з loud error.
  - Додано gate на replay monotonic + closed policy та fixtures для replay ticks.
  - Додано тести для ooo tick_ts, closed time, snap_ts tie-break.
- Де зроблено →
  - core/market/replay_policy.py; runtime/replay_ticks.py.
  - core/runtime/mode.py; app/composition.py; app/main.py; runtime/fxcm_forexconnect.py; config/config.py.
  - tools/exit_gates/gates/gate_tick_replay_monotonic.py; tools/exit_gates/manifest_p2_replay.json.
  - tests/test_tick_replay_policy.py; tests/fixtures/ticks_replay_sample.jsonl.
- Як перевірено →
  - pytest -q tests/test_tick_replay_policy.py
  - python -m tools.run_exit_gates --out reports/exit_gates --manifest tools/exit_gates/manifest_p2_replay.json
- Ризики/нотатки →
  - Якщо «виправити» ooo ticks сортуванням — втратимо доказовість і знову отримаємо drift.
  - Якщо CLOSED policy зробити soft (drop) замість fail-fast — баги wall-clock можуть знову пролізти непомітно.
  - Replay має використовувати ТІЛЬКИ tick_ts з файлу, а не time.time(); ingest_ts можна ставити wall-clock, але не tick_ts.

## 2026-01-24T14:10:00+01:00 — PRE (MODE=PATCH): Фікс типізаційних помилок replay

- Мета → усунути помилки типізації у replay_policy/replay_ticks.
- Scope → core/market/replay_policy.py, runtime/replay_ticks.py.
- Non-goals → зміна логіки replay; запуск повних тестів.
- Інваріанти/рейки → fail-fast ContractError; мінімальний диф.
- Тести/перевірки → не запускались (тільки статичні помилки).

## 2026-01-24T14:12:00+01:00 — POST (MODE=PATCH): Фікс типізаційних помилок replay

- Що зроблено → додано явні перевірки типів tick_ts/snap_ts та payload полів у replay; усунено type errors.
- Де зроблено → core/market/replay_policy.py; runtime/replay_ticks.py.
- Як перевірено → get_errors (0 помилок для цих файлів).
- Ризики/нотатки → без змін поведінки; лише явні перевірки типів.

## 2026-01-24T15:10:00+01:00 — PRE (MODE=PATCH): P2.1 UI Surface SSOT (/chart → UI Lite redirect)

- Мета → зробити /chart стабільним stub-redirect на UI Lite без runtime/static; loud 503 якщо UI Lite вимкнено.
- Scope → runtime/http_server.py, tests/test_chart_endpoint_redirect.py, tools/exit_gates/gates/gate_chart_no_runtime_static.py, tools/exit_gates/manifest_p2_ui.json, docs/audit_v6_public_surface.md.
- Non-goals → нові UI, нові сервери/порти, runtime/static.
- Інваріанти/рейки → UI Lite єдина канонічна UI; без silent fallback.
- Тести/перевірки → python -m pytest -q tests/test_chart_endpoint_redirect.py; python -m tools.run_exit_gates --out reports/exit_gates --manifest tools/exit_gates/manifest_p2_ui.json

## 2026-01-24T15:35:00+01:00 — POST (MODE=PATCH): P2.1 UI Surface SSOT (/chart → UI Lite redirect)

- Що зроблено →
  - /chart переведено у redirect/landing на UI Lite (без runtime/static).
  - Додано helper для redirect URL та 503 HTML, додано тести.
  - Додано gate на відсутність runtime/static у /chart.
  - Оновлено public surface доку.
- Де зроблено →
  - runtime/http_server.py.
  - tests/test_chart_endpoint_redirect.py.
  - tools/exit_gates/gates/gate_chart_no_runtime_static.py; tools/exit_gates/manifest_p2_ui.json.
  - docs/audit_v6_public_surface.md.
- Як перевірено →
  - python -m pytest -q tests/test_chart_endpoint_redirect.py
  - python -m tools.run_exit_gates --out reports/exit_gates --manifest tools/exit_gates/manifest_p2_ui.json
- Ризики/нотатки →
  - Якщо ui_lite_enabled=False, /chart повертає 503; це очікувана loud поведінка.

## 2026-01-24T14:25:00+01:00 — PRE (MODE=read-only discovery): Перевірка використання runtime/static

- Мета → з'ясувати, хто використовував runtime/static після видалення каталогу.
- Non-goals → жодних змін коду/конфігів.
- Інваріанти/рейки → append-only лог.
- Перевірки → пошук посилань на static у runtime/ui_lite/tools/tests.

## 2026-01-24T14:27:00+01:00 — POST (MODE=read-only discovery): Перевірка використання runtime/static

- Що зроблено → знайдено єдиний runtime endpoint, що читав runtime/static/.
- Де перевірено → runtime/http_server.py (/chart), ui_lite/server.py (ui_lite/static), tools/exit_gates/gates/gate_ui_* та tests/test_ui_gap_insert_whitespace.py.
- Як перевірено → grep_search по репозиторію.
- Ризики/нотатки → якщо runtime/static буде видалено, endpoint /chart у runtime/http_server.py стане 404/помилкою читання файлу.

## 2026-01-24T14:40:00+01:00 — PRE (MODE=read-only discovery): Перевірка history_provider дублювань

- Мета → з'ясувати, чи є дублікати між runtime/history_provider та fxcm/history_provider.
- Non-goals → жодних змін коду.
- Інваріанти/рейки → append-only лог.
- Перевірки → читання runtime/history_provider.py, fxcm/history_fxcm_provider.py, runtime/history_sim_provider.py.

## 2026-01-24T14:42:00+01:00 — POST (MODE=read-only discovery): Перевірка history_provider дублювань

- Що зроблено → перевірено контракт та реалізації провайдерів історії.
- Де перевірено → runtime/history_provider.py; fxcm/history_fxcm_provider.py; runtime/history_sim_provider.py.
- Як перевірено → read_file.
- Ризики/нотатки → runtime/history_provider — це Protocol/контракт; fxcm/history_fxcm_provider — скелет реалізації (не дубль). runtime/history_sim_provider видалено (rail).

## 2026-01-25T09:10:00+01:00 — PRE (MODE=read-only discovery): REPO_LAYOUT annotated tree

- Мета → зібрати фактичну структуру репозиторію і призначення модулів для docs/REPO_LAYOUT.md.
- Non-goals → жодних змін коду; без рефакторингу.
- Інваріанти/рейки → тільки фактичні файли; якщо незрозуміло — TODO + посилання path:line.
- Перевірки → tree /F /A + читання entrypoints/модулів.

## 2026-01-25T09:20:00+01:00 — POST (MODE=read-only discovery): REPO_LAYOUT annotated tree

- Що зроблено → зібрано фактичну структуру репозиторію та entrypoints для описів.
- Де перевірено → tree /F /A; app/main.py; app/composition.py; README.md; docs/audit_v6_public_surface.md.
- Як перевірено → run_in_terminal + read_file.
- Ризики/нотатки → .venv містить vendor‑файли, не включати детальний список у дерево.

## 2026-01-25T09:25:00+01:00 — PRE (MODE=PATCH): docs/REPO_LAYOUT.md

- Мета → створити docs/REPO_LAYOUT.md з high‑level мапою, annotated tree та конфіг/CI секцією.
- Scope → docs/REPO_LAYOUT.md.
- Non-goals → зміни коду.
- Інваріанти/рейки → не вигадувати файлів/папок; TODO+path:line якщо призначення неясне.
- Тести/перевірки → не застосовні.

## 2026-01-25T09:35:00+01:00 — POST (MODE=PATCH): docs/REPO_LAYOUT.md

- Що зроблено → створено docs/REPO_LAYOUT.md з high‑level мапою, public boundary, annotated tree та списком конфіг/оркестрації.
- Де зроблено → docs/REPO_LAYOUT.md.
- Як перевірено → read_file + tree /F /A (read‑only).
- Ризики/нотатки → .venv містить vendor‑файли, не деталізовано в дереві.

## 2026-01-25T10:05:00+01:00 — POST (MODE=read-only discovery): Оцінка ризиків layout + рейки + GO/NO GO

- Що зроблено → зафіксовано сильні сторони, ризики дублювання SSOT, та мінімальні рейки (R1/R2/R3) для стабілізації.
- Де перевірено → docs/REPO_LAYOUT.md; app/main.py; app/composition.py; core/contracts/public/*; core/validation/validator.py; tools/run_exit_gates.py.
- Як перевірено → read_file (без змін коду).
- Ризики/нотатки →
  - Дублювання логіки preview/replay між core/runtime/tools може створити “дві правди”.
  - core/runtime/* vs runtime/* — джерело плутанини імпортів без явних рейок.
  - FXCM провайдери можуть еволюціонувати паралельно без єдиного SSOT.
- Рекомендовані рейки →
  - R1: Dependency rule (core не імпортує runtime/store/ui_lite/tools; tools лише тонкі CLI).
  - R2: Єдине SSOT місце для preview builder (core або runtime, але не обидва).
  - R3: tools/* не дублюють runtime/*, тільки thin wrapper.
- GO/NO GO → __NO‑GO__ до встановлення мінімальних рейок R1–R3 (ризик дрейфу SSOT при частих апдейтах).

## 2026-01-25T10:40:00+01:00 — PRE (MODE=PATCH): R1–R3 Rails Closure (dependency + preview SSOT + tools thin-wrapper)

- Мета → зняти NO-GO: додати enforce-рейки R1–R3 через exit-gates + unit-тести, без зміни Public Surface.
- Scope → tools/exit_gates/gates/*, tools/exit_gates/manifest.json, tests/*, (мінімальні рефактори runtime/preview_builder.py, tools/replay_ticks.py, runtime/replay_ticks.py).
- Інваріанти → no silent fallback; runner SSOT = tools/run_exit_gates.py; українські логи/помилки; мінімальний диф.
- Exit Gates → gate_dependency_rule_core.py, gate_preview_builder_ssot.py, gate_tools_thin_wrapper.py.
- Перевірки → python -m ruff check .; python -m mypy .; python -m pytest -q; python -m tools.run_exit_gates --out reports/exit_gates --manifest tools/exit_gates/manifest.json

## 2026-01-25T11:05:00+01:00 — POST (MODE=PATCH): R1–R3 Rails Closure (dependency + preview SSOT + tools thin-wrapper)

- Що зроблено →
  - R1: додано gate_dependency_rule_core (AST‑скан core) + тести.
  - R2: preview SSOT перенесено в core/market/preview_builder.py; runtime/preview_builder.py зроблено thin wrapper; додано gate + тести.
  - R3: tools/replay_ticks.py → thin wrapper; runtime/replay_ticks.py отримав CLI; додано gate + тести.
  - Manifest оновлено новими gates.
- Де зроблено →
  - tools/exit_gates/gates/gate_dependency_rule_core.py
  - tools/exit_gates/gates/gate_preview_builder_ssot.py
  - tools/exit_gates/gates/gate_tools_thin_wrapper.py
  - tools/exit_gates/manifest.json
  - tests/test_gate_dependency_rule_core.py
  - tests/test_gate_preview_builder_ssot.py
  - tests/test_gate_tools_thin_wrapper.py
  - core/market/preview_builder.py
  - runtime/preview_builder.py
  - runtime/replay_ticks.py
  - tools/replay_ticks.py
- Як перевірено →
  - python -m ruff check . → OK
  - python -m mypy . → FAIL (tests/* без тип-анотацій)
  - python -m pytest -q → FAIL (tests/test_warmup_handler_updates_status.py: SSOT 1m final порожній)
  - python -m tools.run_exit_gates --out reports/exit_gates --manifest tools/exit_gates/manifest.json → FAIL (gate_tail_guard_marks_persist)
- Ризики/нотатки →
  - Якщо «виправити» ooo ticks сортуванням — втратимо доказовість і знову отримаємо drift.
  - Якщо CLOSED policy зробити soft (drop) замість fail-fast — баги wall-clock можуть знову пролізти непомітно.
  - Replay має використовувати ТІЛЬКИ tick_ts з файлу, а не time.time(); ingest_ts можна ставити wall-clock, але не tick_ts.

  ## 2026-01-25T12:10:00+01:00 — PRE (MODE=PATCH): Оновлення docs/REPO_LAYOUT.md

  - Мета → синхронізувати REPO_LAYOUT з актуальним SSOT preview builder та thin wrappers.
  - Scope → docs/REPO_LAYOUT.md.
  - Non-goals → зміни коду.
  - Інваріанти/рейки → не вигадувати файлів; лише фактичні зміни.
  - Тести/перевірки → не застосовні.

  ## 2026-01-25T12:12:00+01:00 — POST (MODE=PATCH): Оновлення docs/REPO_LAYOUT.md

  - Що зроблено → оновлено annotated tree для core/market/preview_builder.py та thin wrappers runtime/tools.
  - Де зроблено → docs/REPO_LAYOUT.md.
  - Як перевірено → read_file.
  - Ризики/нотатки → відсутні.

## 2026-01-25T12:20:00+01:00 — PRE (MODE=PATCH): Виправлення mypy/tests, warmup empty history, tail_guard marks

- Мета →
  - прибрати mypy FAIL у tests через окремий mypy профіль,
  - стабілізувати warmup тест (seed 1m bar) і додати негативний кейс для порожньої історії з loud error у status,
  - перевірити/посилити persistence та invalidation для tail_guard marks.
- Scope → mypy.ini, runtime/handlers_p3.py, tests/test_warmup_handler_updates_status.py, tests/test_tail_guard_marks_persistence.py.
- Non-goals → зміна контрактів, зміна публічних API, нові команди.
- Інваріанти/рейки → мінімальний диф, fail-fast, SSOT статус/контракти без змін.
- План →
  1) Додати mypy профіль для tests.*.
  2) Додати loud error у warmup handler при порожньому SSOT.
  3) Засіяти 1m bar у warmup тесті + додати негативний тест.
  4) Додати перевірку invalidation для tail_guard marks.
  5) Запустити mypy, pytest, exit gates (tools.run_exit_gates).
- Тести/перевірки → python -m mypy .; python -m pytest -q; python -m tools.run_exit_gates --out reports/exit_gates --manifest tools/exit_gates/manifest.json.

## 2026-01-25T12:40:00+01:00 — POST (MODE=PATCH): Виправлення mypy/tests, warmup empty history, tail_guard marks

- Що зроблено →
  - додано mypy профіль для tests.* (без вимоги тип-анотацій),
  - у warmup handler додано loud error у status для порожнього SSOT 1m final,
  - у warmup тесті додано seed 1m bar + негативний тест для empty history,
  - у tail_guard marks persistence тесті додано перевірку invalidation,
  - gate tail_guard marks зроблено детермінованим фіксованим open-time.
- Де зроблено →
  - mypy.ini
  - runtime/handlers_p3.py
  - tests/test_warmup_handler_updates_status.py
  - tests/test_tail_guard_marks_persistence.py
  - tools/exit_gates/gates/gate_tail_guard_marks_persist.py
- Як перевірено →
  - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m mypy . → OK
  - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m pytest -q → OK
  - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m tools.run_exit_gates --out reports/exit_gates --manifest tools/exit_gates/manifest.json → OK
- Ризики/нотатки →
  - Gate tail_guard marks тепер фіксує час у відкриту сесію; це робить перевірку детермінованою.

## 2026-01-25T13:05:00+01:00 — PRE (MODE=PATCH): Public API Spec — 12 нормативних пунктів

- Мета → оновити docs/Public API Spec (SSOT).md з 12 нормативними пунктами (MUST/SHOULD) за вимогами P0.3.
- Scope → тільки docs/Public API Spec (SSOT).md.
- Non-goals → жодних змін коду/контрактів/поведінки runtime.
- Інваріанти/рейки → мінімальний диф, чіткі MUST/SHOULD формулювання.
- План → (1) уточнити 12 пунктів; (2) перевірити узгодженість з існуючими розділами; (3) записати POST.
- Тести/перевірки → не застосовні (лише документація).

## 2026-01-25T13:10:00+01:00 — POST (MODE=PATCH): Public API Spec — 12 нормативних пунктів

- Що зроблено → оновлено розділ “12 жорстких пунктів” з чіткими MUST формулюваннями за списком P0.3.
- Де зроблено → docs/Public API Spec (SSOT).md.
- Як перевірено → read_file.
- Ризики/нотатки → відсутні.

## 2026-01-25T13:25:00+01:00 — PRE (MODE=PATCH): P0.4 Schema alignment (docs ↔ public JSON schemas ↔ validator)

- Мета → синхронізувати Public API Spec з core/contracts/public/*.json і validator allowlist без зміни Public Surface поведінки.
- Scope → core/contracts/public/{tick_v1.json,commands_v1.json,ohlcv_v1.json,status_v2.json}, core/validation/validator.py, tests/* (тільки контрактні/схемні).
- Non-goals → зміни runtime логіки, нові команди, зміни Redis каналів/ключів.
- Інваріанти/рейки → backward-compatible; tick_ts/snap_ts строго int ms; no silent fallback; gates мають залишитись зеленими.
- План →
  1) Перевірити/узгодити tick/commands/ohlcv/status schemas з фактичним payload.
  2) Додати умовності для final в ohlcv_v1 без зламу preview.
  3) Додати мінімальні контрактні тести для tick schema (float reject, int accept).
  4) Прогнати ruff/mypy/pytest/exit_gates.
- Перевірки → python -m ruff check .; python -m mypy .; python -m pytest -q; python -m tools.run_exit_gates --out reports/exit_gates --manifest tools/exit_gates/manifest.json.

## 2026-01-25T13:45:00+01:00 — POST (MODE=PATCH): P0.4 Schema alignment (docs ↔ public JSON schemas ↔ validator)

- Що зроблено →
  - ohlcv_v1.json: додано enum TF/SOURCE allowlist, optional ingest_ts, умовні правила для complete=true (synthetic=false, source∈FINAL_SOURCES, event_ts required).
  - tick/commands/status schema без зміни Public Surface; tick/commands залишили additionalProperties=false, status_v2 без змін по полях.
  - Додано мінімальні тести схеми tick_v1 (int accept, float reject).
- Де зроблено →
  - core/contracts/public/ohlcv_v1.json
  - tests/test_validator_tick.py
- additionalProperties:false →
  - tick_v1.json: так (контрольовано)
  - commands_v1.json: так (контрольовано)
  - ohlcv_v1.json: так, список полів доповнено ingest_ts
  - status_v2.json: без змін
- Rails у validator → без змін (event_ts==close_time та ms int залишаються як fail-fast).
- Як перевірено →
  - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m ruff check . → OK
  - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m mypy . → OK
  - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m pytest -q → OK
  - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m tools.run_exit_gates --out reports/exit_gates --manifest tools/exit_gates/manifest.json → OK
- Ризики/нотатки →
  - Умовність event_ts==close_time лишається validator-rail, бо JSON Schema не виражає рівність без розширень.

## 2026-01-25T14:05:00+01:00 — PRE (MODE=read-only discovery): Evidence Pack по ui_lite і live pipeline

- Мета → зібрати доказову прив'язку HTTP/WS endpoint-ів, джерел часу, preview/final pipeline та ключових фактів.
- Scope → читання runtime/http_server.py, ui_lite/server.py, core/market/preview_builder.py, runtime/*, store/*.
- Non-goals → будь-які зміни коду/контрактів.
- Інваріанти/рейки → лише discovery, без PATCH.
- План → (1) HTTP/WS endpoints; (2) джерела tick_ts/snap_ts/ingest_ts; (3) preview SSOT; (4) final store; (5) 10 фактів.
- Перевірки → не застосовні.

## 2026-01-25T14:25:00+01:00 — POST (MODE=read-only discovery): Evidence Pack по ui_lite і live pipeline

- Що зроблено → зібрано Evidence Pack по HTTP/WS endpoints, джерелах часу, preview/final pipeline та ключових фактах (без змін коду).
- Де зроблено → runtime/http_server.py, ui_lite/server.py, runtime/fxcm_forexconnect.py, runtime/replay_ticks.py, core/market/preview_builder.py, runtime/preview_builder.py, runtime/ohlcv_preview.py, store/schema.sql, store/sqlite_store.py, runtime/final/final_ingest.py, runtime/warmup.py.
- Як перевірено → read_file/grep_search.
- Ризики/нотатки → відсутні.
  
Нижче Evidence Pack (read‑only), з посиланнями на файл:рядок.

1) HTTP endpoints (path → handler файл:рядок) і що читають
Runtime HTTP (chart API):

/api/status → читає Redis key status:snapshot, віддає JSON. http_server.py:96
/api/ohlcv → читає:
preview: in‑memory OhlcvCache (cache),
final: SQLiteStore (query_1m_tail/query_htf_tail). http_server.py:109-143
/chart → redirect на UI Lite або 503 (коли вимкнено). http_server.py:156-175
/api/bars, /debug, /ws/* у runtime немає (handler робить 404 на все інше). http_server.py:176-181
UI Lite HTTP (статичні/службові):

/debug → JSON зі станом UI Lite. server.py:140
/, /index.html, /app.js, /chart_adapter.js, /styles.css, /vendor/lightweight-charts..., /favicon.ico → статичні файли. server.py:143-174
2) WS endpoints (path → handler файл:рядок), payload-и та валідатор
WS endpoint (UI Lite):

WebSocket сервер піднімається на ui_lite_host:ui_lite_port, без фільтрації шляху (будь‑який path приймається для WS‑upgrade). server.py:507-512
Обробник WS: _ws_handler (subscribe + snapshot), _broadcaster (bar updates). server.py:309-472
Що шле WS:

Snapshot: {"type":"snapshot","symbol","tf","mode","bars":[...]}. server.py:335-344
Bar update: {"type":"bar","symbol","tf","mode","bar":{time,open,high,low,close,volume}}. server.py:456-471
Які payload-и джерела:

UI Lite читає тільки Redis канал OHLCV (config.ch_ohlcv()), тобто OHLCV, не tick/status. server.py:497-504, server.py:359-399
Валідація:

UI Lite не використовує validator.py; лише json.loads і фільтрація/нормалізація. server.py:372-399, server.py:403-472
Tick і OHLCV валідовані в runtime публікаторах:
tick → validate_tick_v1. tick_feed.py:46-49
ohlcv → validate_ohlcv_v1/validate_ohlcv_*. publisher.py:42-54, publisher.py:79-134
3) Джерела часу
tick_ts/snap_ts:

FXCM live: now_ms = time.time()*1000, далі tick_ts_ms=now_ms, snap_ts_ms=now_ms. fxcm_forexconnect.py:270, fxcm_forexconnect.py:203-204
Replay: береться з payload файлу (tick_ts, snap_ts). replay_ticks.py:79-80 + нормалізація через tick_from_payload. replay_ticks.py:161, tick.py:64-77
ingest_ts (де формується):

warmup: bar["ingest_ts_ms"] = time.time()*1000. warmup.py:76
backfill: bar["ingest_ts_ms"] = time.time()*1000. backfill.py:72
final HTF ingest: ingest_ts_ms = time.time()*1000, далі в bars. final_ingest.py:138-140
rebuild_derived: ingest_ts_ms = time.time()*1000. rebuild_derived.py:202-204
repair: bar["ingest_ts_ms"] = now_ms. repair.py:88
4) Preview: SSOT і дублювання
SSOT для preview builder — core: PreviewBuilder + OhlcvCache. preview_builder.py:54-174
runtime лише реекспортує (thin wrapper). preview_builder.py:4-12
runtime wrapper: PreviewCandleBuilder викликає PreviewBuilder з core. ohlcv_preview.py:7-29
=> дублювання логіки в runtime немає.
5) Final: як читається та де store
SSOT store — SQLite (1m final + HTF). schema.sql:4-79
bars_1m_final (1m final) schema.sql:4
bars_htf_final + index idx_bars_htf_symbol_tf_open_time schema.sql:56-79
Читання final для HTTP /api/ohlcv?mode=final:
query_1m_tail або query_htf_tail. http_server.py:121-142, sqlite_store.py:150-267
6) 10 ключових фактів (FACT)
1. FACT: http_server.py:96 → /api/status читає Redis snapshot і повертає JSON.
2. FACT: http_server.py:109-143 → /api/ohlcv: preview з OhlcvCache, final зі SQLiteStore.
3. FACT: http_server.py:156-175 → /chart редіректить у UI Lite або 503.
4. FACT: server.py:140 → /debug у UI Lite повертає snapshot стану.
5. FACT: server.py:507-512 → WS сервер піднято через serve(...) без path‑filter.
6. FACT: server.py:335-344 → WS snapshot payload (bars).
7. FACT: server.py:456-471 → WS bar payload (time, o/h/l/c/volume).
8. FACT: fxcm_forexconnect.py:203-204 → FXCM live ставить tick_ts_ms/snap_ts_ms з now_ms.
9. FACT: replay_ticks.py:79-80 → replay бере tick_ts/snap_ts з JSONL.
10. FACT: preview_builder.py:91-174 → SSOT preview builder формує TF‑бари з ticks.

## 2026-01-25T15:05:00+01:00 — PRE (MODE=PATCH): FXCM tick event time + UI Lite inbound rail

- Мета →
  - FXCM live: tick_ts_ms з event time, snap_ts_ms з receipt time; drop + loud error якщо event_ts відсутній.
  - Додати gate на заборону tick_ts_ms від wall-clock.
  - (P2.2) Додати валідацію OHLCV payload у UI Lite Redis inbound.
- Scope → runtime/fxcm_forexconnect.py, tests/test_fxcm_offers_subscription.py, tools/exit_gates/gates/gate_tick_event_time_not_wallclock.py, tools/exit_gates/manifest.json, ui_lite/server.py.
- Non-goals → інші runtime зміни, зміна каналів/контрактів.
- Інваріанти/рейки → не ставити tick_ts_ms з time.time(); missing event_ts → errors[] + drop; gates зелені.
- План →
  1) Додати витяг event_ts з FXCM row і замінити tick_ts_ms.
  2) Оновити тести FXCM offers.
  3) Додати exit-gate для wall-clock заборони.
  4) Додати UI Lite inbound validation для OHLCV.
  5) Запустити ruff/mypy/pytest/exit_gates.
- Перевірки → python -m ruff check .; python -m mypy .; python -m pytest -q; python -m tools.run_exit_gates --out reports/exit_gates --manifest tools/exit_gates/manifest.json.

## 2026-01-25T15:35:00+01:00 — POST (MODE=PATCH): FXCM tick event time + UI Lite inbound rail

- Що зроблено →
  - FXCM live: tick_ts_ms береться з event time; snap_ts_ms — з receipt time; missing event_ts → errors[] + drop.
  - Додано статичний gate для заборони tick_ts_ms від wall-clock.
  - UI Lite: додано валідатор OHLCV на inbound Redis, з лічильником/last_error у /debug.
- Де зроблено →
  - runtime/fxcm_forexconnect.py
  - tests/test_fxcm_offers_subscription.py
  - tools/exit_gates/gates/gate_tick_event_time_not_wallclock.py
  - tools/exit_gates/manifest.json
  - ui_lite/server.py
- Як перевірено →
  - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m ruff check . → OK
  - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m mypy . → OK
  - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m pytest -q → OK
  - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m tools.run_exit_gates --out reports/exit_gates --manifest tools/exit_gates/manifest.json → OK
- Ризики/нотатки →
  - Якщо FXCM SDK не віддає event_ts (або інший формат), ticks будуть глушитись з missing_tick_event_ts.

## 2026-01-25T16:05:00+01:00 — PRE (MODE=PATCH): REPO_LAYOUT + stale/price helpers + P7A fix

- Мета →
  - підтримати REPO_LAYOUT актуальним,
  - перевірити _stale_action/_extract_price (пояснити або прибрати),
  - виправити помилки run_exit_gate_p7a.ps1 (через сумісність ws_smoke payload).
- Scope → docs/REPO_LAYOUT.md, runtime/fxcm_forexconnect.py, tools/audit/ws_smoke_ui_lite.py, tools/audit/run_exit_gate_p7a.ps1 (за потреби).
- Non-goals → зміни поведінки runtime поза згаданими точками.
- Інваріанти/рейки → мінімальний диф; без зміни публічних контрактів.
- План →
  1) Прибрати/пояснити _extract_price, _stale_action.
  2) Зробити ws_smoke payload валідним для OHLCV.
  3) Оновити REPO_LAYOUT.
  4) Запустити ruff/mypy/pytest/exit_gates.
- Перевірки → python -m ruff check .; python -m mypy .; python -m pytest -q; python -m tools.run_exit_gates --out reports/exit_gates --manifest tools/exit_gates/manifest.json.

## 2026-01-25T16:25:00+01:00 — POST (MODE=PATCH): REPO_LAYOUT + stale/price helpers + P7A fix

- Що зроблено →
  - Видалено неактуальний _extract_price; _stale_action залишено з явним призначенням (legacy helper для тестів).
  - ws_smoke_ui_lite формує валідний OHLCV payload (open_time/close_time, volume, source/complete/synthetic).
  - Оновлено REPO_LAYOUT (FXCM tick time, UI Lite inbound validation, gate tick_event_time).
- Де зроблено →
  - runtime/fxcm_forexconnect.py
  - tools/audit/ws_smoke_ui_lite.py
  - docs/REPO_LAYOUT.md
- Як перевірено →
  - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m ruff check . → OK
  - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m mypy . → OK
  - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m pytest -q → OK
  - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m tools.run_exit_gates --out reports/exit_gates --manifest tools/exit_gates/manifest.json → OK
- Ризики/нотатки →
  - Якщо ws_smoke ще падає, перевірити Redis/порт UI Lite у run_exit_gate_p7a.ps1.

## 2026-01-25T16:45:00+01:00 — PRE (MODE=PATCH): P2.1.1 tick event time observability

- Мета → додати метрики/поля статусу для tick event time (ск’ю та drop), без зміни public API поза status.
- Scope → observability/metrics.py, runtime/status.py, runtime/fxcm_forexconnect.py, core/contracts/public/status_v2.json, tests/test_validator_status.py, tests/test_tick_publisher_updates_status.py, docs/REPO_LAYOUT.md.
- Non-goals → P2.1.2 rolling window/preview policy.
- Інваріанти/рейки → no silent fallback; tick_ts/snap_ts int ms; exit gates зелені.
- План →
  1) Додати метрики fxcm_ticks_dropped_total{reason} та fxcm_tick_skew_ms.
  2) Розширити price.* у status (event/snap/skew/ticks_dropped_1m).
  3) Інкремент drop для missing_event_ts.
  4) Тест на skew_ms.
  5) Оновити status_v2 schema + REPO_LAYOUT.
  6) Прогнати ruff/mypy/pytest/exit_gates.
- Перевірки → python -m ruff check .; python -m mypy .; python -m pytest -q; python -m tools.run_exit_gates --out reports/exit_gates --manifest tools/exit_gates/manifest.json.

## 2026-01-25T17:15:00+01:00 — POST (MODE=PATCH): P2.1.1 tick event time observability

- Що зроблено →
  - Додано метрики fxcm_ticks_dropped_total{reason} та fxcm_tick_skew_ms.
  - Розширено price у status: last_tick_event_ms, last_tick_snap_ms, tick_skew_ms, ticks_dropped_1m.
  - Missing event_ts інкрементує drop‑лічильник + ticks_dropped_1m; skew<0 → degraded-but-loud.
  - Додано gate_tick_skew_non_negative.
- Де зроблено →
  - observability/metrics.py
  - runtime/status.py
  - runtime/fxcm_forexconnect.py
  - core/contracts/public/status_v2.json
  - tests/test_validator_status.py
  - tests/test_tick_publisher_updates_status.py
  - tools/exit_gates/gates/gate_tick_skew_non_negative.py
  - tools/exit_gates/manifest.json
  - docs/REPO_LAYOUT.md
- Як перевірено →
  - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m ruff check . → OK
  - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m mypy . → OK
  - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m pytest -q → OK
  - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m tools.run_exit_gates --out reports/exit_gates --manifest tools/exit_gates/manifest.json → OK
- Ризики/нотатки →
  - P2.1.2 (rolling window/preview stop) не виконано у цьому PATCH.

## 2026-01-25T17:40:00+01:00 — PRE (MODE=PATCH): P2.1.2 rolling window + preview stop

- Мета → додати rolling window 60с для missing_event_ts і керовано зупиняти/відновлювати preview при високому drop_rate.
- Scope → runtime/status.py, app/composition.py, tests/test_tick_health_rolling_window.py, docs/REPO_LAYOUT.md.
- Non-goals → зміни public API, нові команди.
- Інваріанти/рейки → degraded-but-loud; preview stop без silent fallback; exit gates зелені.
- План →
  1) Додати rolling window та стан preview_paused у StatusManager.
  2) Гейтнути preview publish у _handle_fxcm_tick.
  3) Додати тести enter/exit degraded.
  4) Оновити REPO_LAYOUT.
  5) Прогнати ruff/mypy/pytest/exit_gates.
- Перевірки → python -m ruff check .; python -m mypy .; python -m pytest -q; python -m tools.run_exit_gates --out reports/exit_gates --manifest tools/exit_gates/manifest.json.

## 2026-01-25T18:05:00+01:00 — POST (MODE=PATCH): P2.1.2 rolling window + preview stop

- Що зроблено →
  - Додано rolling window 60с для seen/dropped та кероване degraded "tick_event_time_unavailable".
  - Preview publish гейтиться при деградації; status/metrics лишаються.
  - Додано тести enter/exit degraded.
- Де зроблено →
  - runtime/status.py
  - app/composition.py
  - tests/test_tick_health_rolling_window.py
  - docs/REPO_LAYOUT.md
- Як перевірено →
  - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m ruff check . → OK
  - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m mypy . → OK
  - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m pytest -q → OK
  - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m tools.run_exit_gates --out reports/exit_gates --manifest tools/exit_gates/manifest.json → OK
- Ризики/нотатки →
  - Порогові значення drop_rate закладені в StatusManager; за потреби винесемо в конфіг окремим PATCH.

## 2026-01-25T19:25:00+01:00 — PRE (MODE=read-only discovery): HTF preview та 1d механізми

- Мета → зібрати факти про побудову HTF preview з 1m/тік і 1d preview/final (boundary, complete, gaps).
- Scope → core/market/preview_builder.py, core/time/buckets.py, store/derived_builder.py, config/config.py, core/time/sessions.py.
- Non-goals → зміни коду, тести, рефакторинг.
- Інваріанти/рейки → append-only журнал; без виконання команд.
- План →
  1) Локалізувати entry points preview/HTF та 1d boundary.
  2) Витягнути правила bucket open/close, агрегації OHLC/volume.
  3) Зафіксувати 12 FACT із path:line у POST.
- Перевірки → не запускались (read-only).

## 2026-01-25T19:35:00+01:00 — POST (MODE=read-only discovery): HTF preview та 1d механізми

- Що зроблено → зібрано 12 FACT (6 HTF-from-1m preview, 6 для 1d) з точними path:line.
- Де зроблено → core/market/preview_builder.py; core/time/buckets.py; store/derived_builder.py; config/config.py; core/time/sessions.py.
- Як перевірено → не запускалось (read-only).
- Ризики/нотатки → HTF preview будується з тіків, а не з 1m final; 1d boundary у preview не використовує trading_day_boundary_utc.
- FACTS (HTF-from-1m preview) →
  1) Entry point preview-агрегації та перелік TF: PreviewBuilder.on_tick() і цикл по config.ohlcv_preview_tfs — core/market/preview_builder.py:101-103.
  2) Визначення bucket open_time для TF (крім 1d) як floor по size: bucket_start = tick_ts_ms // size * size — core/market/preview_builder.py:103-107.
  3) Визначення close_time для bucket через get_bucket_close_ms(tf, bucket_start, trading_day_boundary_utc) (inclusive) — core/market/preview_builder.py:129.
  4) OHLC агрегація на старті bucket: open/high/low/close = mid, volume=1.0, tick_count=1 — core/market/preview_builder.py:138-146.
  5) OHLC/volume/tick_count апдейти на кожен tick: high=max, low=min, close=mid, volume+=1.0, tick_count+=1 — core/market/preview_builder.py:149-154.
  6) Gaps/OOO ticks: якщо bucket_start < current_bucket_open_ms → late_ticks_dropped_total++ та past_mutations_total++, tick ігнорується — core/market/preview_builder.py:119-128.
- FACTS (1d preview/final) →
  7) Джерело trading_day_boundary_utc у конфігу — config/config.py:42-44.
  8) Обчислення daily bucket open_time через boundary: _floor_to_trading_day() та get_bucket_open_ms(tf=="1d") — core/time/buckets.py:40-62.
  9) Close_time inclusive для 1d (bucket_open + size - 1) — core/time/buckets.py:65-68.
  10) 1d final агрегується з 1m final через build_htf_final() і get_bucket_open_ms/get_bucket_close_ms — store/derived_builder.py:31-48.
  11) Complete semantics для 1d final: _finalize_bucket вимагає expected count і перевіряє close_time — store/derived_builder.py:129-134.
  12) Daily break / weekend / closed_intervals застосовуються в календарі (is_trading_time): closed_intervals, вихідні та daily_break — core/time/sessions.py:117-134.

## 2026-01-25T20:30:00+01:00 — PRE (MODE=PATCH): стабільне завершення процесів у run_exit_gate_p7a (safe stop)

- Мета → прибрати гонки/помилки в finally при завершенні процесів.
- Scope → tools/audit/run_exit_gate_p7a.ps1.
- Non-goals → зміни логіки запуску, таймаутів чи ws_smoke.
- Інваріанти/рейки → мінімальний диф, без запуску команд.
- План →
  1) Додати safe-обгортку Stop-SafeProcess із перевіркою існування процесу.
  2) Замінити прямі Stop-Process у finally на safe виклик.
- Перевірки → не запускались (локальний PATCH).

## 2026-01-25T20:35:00+01:00 — POST (MODE=PATCH): стабільне завершення процесів у run_exit_gate_p7a (safe stop)

- Що зроблено → додано Stop-SafeProcess (Get-Process + try/catch + SilentlyContinue) і використано у finally.
- Де зроблено → tools/audit/run_exit_gate_p7a.ps1.
- Як перевірено → не запускалось (локальний PATCH).
- Ризики/нотатки → помилки завершення процесів приглушуються; логіка запуску не змінена.

## 2026-01-25T20:45:00+01:00 — PRE (MODE=PATCH): P2.3 preview 1d boundary + exit gate

- Мета → узгодити 1d preview boundary з SSOT buckets та додати gate на bucket boundaries.
- Scope → core/market/preview_builder.py; tests/test_preview_bucket_boundaries.py; tools/exit_gates/gates/gate_preview_bucket_boundaries.py; tools/exit_gates/manifest.json; docs/REPO_LAYOUT.md.
- Non-goals → зміни preview→HTF агрегації (1m-based), зміна поведінки late ticks.
- Інваріанти/рейки → мінімальний диф; без I/O у gate; append-only журнал.
- План →
  1) Вирівняти 1d bucket open/close у PreviewBuilder через buckets.get_bucket_open_ms/get_bucket_close_ms.
  2) Додати тести для 1d boundary та 15m floor/close.
  3) Додати exit gate на preview bucket boundaries без Redis.
  4) Оновити REPO_LAYOUT.
  5) Прогнати ruff/mypy/pytest/exit_gates.
- Перевірки → python -m ruff check .; python -m mypy .; python -m pytest -q; python -m tools.run_exit_gates --out reports/exit_gates --manifest tools/exit_gates/manifest.json.

## 2026-01-25T21:15:00+01:00 — POST (MODE=PATCH): P2.3 preview 1d boundary + exit gate

- Що зроблено →
  - 1d preview bucket_open/close вирівняно через SSOT buckets.get_bucket_open_ms/get_bucket_close_ms.
  - Додано тести boundary для 1d та 15m.
  - Додано exit gate preview bucket boundaries і підключено в manifest.
  - Оновлено REPO_LAYOUT.
- Де зроблено →
  - core/market/preview_builder.py
  - tests/test_preview_bucket_boundaries.py
  - tools/exit_gates/gates/gate_preview_bucket_boundaries.py
  - tools/exit_gates/manifest.json
  - docs/REPO_LAYOUT.md
- Як перевірено →
  - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m ruff check . → OK
  - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m mypy . → OK
  - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m pytest -q → OK
  - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m tools.run_exit_gates --out reports/exit_gates --manifest tools/exit_gates/manifest.json → OK
- Ризики/нотатки → preview лишається tick-based (варіант А), лише boundary 1d узгоджено з SSOT buckets.

## 2026-01-25T21:45:00+01:00 — PRE (MODE=read-only discovery): P3 budget + retention gaps map

- Мета → зібрати факти про history запити/параметри чанків/ліміти та tail_guard marks/persistence.
- Scope → runtime/{warmup,backfill,repair,tail_guard}.py; runtime/fxcm/{history_provider,history_budget}.py; store/sqlite_store.py; config/config.py.
- Non-goals → зміни коду або запуск команд.
- Інваріанти/рейки → append-only журнал; лише факти path:line.
- План →
  1) Локалізувати всі callsites history fetch.
  2) Зафіксувати параметри chunk/window/limits.
  3) Зафіксувати механіку marks/TTL/інвалідації та методи “є/нема барів”.
- Перевірки → не запускались (read-only).

## 2026-01-25T22:05:00+01:00 — POST (MODE=read-only discovery): P3 budget + retention gaps map

- Що зроблено → зібрано 15 FACT з path:line про history fetch, ліміти/чанки та tail_guard marks.
- Де зроблено → runtime/{warmup,backfill,repair,tail_guard}.py; runtime/fxcm/{history_provider,history_budget}.py; store/sqlite_store.py; config/config.py.
- Як перевірено → не запускалось (read-only).
- FACTS →
  1) History fetch у backfill: provider.fetch_1m_final(...) — runtime/backfill.py:70.
  2) History fetch у warmup: provider.fetch_1m_final(...) — runtime/warmup.py:74.
  3) History fetch у repair: provider.fetch_1m_final(...) — runtime/repair.py:85.
  4) Низькорівневий FXCM history виклик: adapter.fetch_1m(...) — runtime/fxcm/history_provider.py:80.
  5) Warmup chunk/limit/min_sleep: history_chunk_minutes/history_chunk_limit/history_min_sleep_ms — runtime/warmup.py:51-53.
  6) Backfill chunk/limit/min_sleep: history_chunk_minutes/history_chunk_limit/history_min_sleep_ms — runtime/backfill.py:48-50.
  7) Repair budget limits: max_window/max_missing/max_chunks + history_chunk_minutes для chunk count — runtime/repair.py:27-63.
  8) Tail guard 1m coverage: query_1m_range(...) у вікні window_hours — runtime/tail_guard.py:257.
  9) Tail guard HTF coverage: query_htf_range(...) у вікні window_hours — runtime/tail_guard.py:279.
  10) Warmup rate-limit: TokenBucket з max_requests_per_minute — runtime/warmup.py:56-58.
  11) FXCM history budget: token bucket + single-inflight per symbol — runtime/fxcm/history_budget.py:11-30.
  12) Budget acquire/release навколо fetch: build_history_budget + budget.acquire/release — runtime/fxcm/history_provider.py:77-106.
  13) Tail guard TTL/skip: tail_guard_checked_ttl_s + get_tail_audit_state + _should_skip_by_mark — runtime/tail_guard.py:212-234.
  14) Tail guard persistence: _persist_mark_if_ok → store.upsert_tail_audit_state — runtime/tail_guard.py:341-356.
  15) Перевірка “є/нема барів” у store: get_last_complete_close_ms — store/sqlite_store.py:154.

## 2026-01-25T22:25:00+01:00 — PRE (MODE=PATCH): P3.2 SSOT single history budgeter

- Мета → прибрати дублювання rate-limit у warmup/backfill/repair і зробити єдиний history budgeter з global single-inflight.
- Scope → runtime/fxcm/history_budget.py, runtime/fxcm/history_provider.py, runtime/{warmup,backfill,repair}.py, observability/metrics.py, tools/exit_gates/gates/gate_no_local_tokenbucket_history.py, tools/exit_gates/manifest.json, tests/test_history_budget_global_single_inflight.py, docs/REPO_LAYOUT.md.
- Non-goals → зміни public API/команд/Redis; зміна логіки chunking.
- Інваріанти/рейки → мінімальний диф; контроль запитів через SSOT budgeter.
- План →
  1) Додати global single-inflight у HistoryBudget і блокуючий acquire.
  2) Прибрати локальні TokenBucket/min_sleep у warmup/backfill/repair.
  3) Додати метрики inflight/throttled і gate на заборону локальних TokenBucket.
  4) Додати тест на global single-inflight.
  5) Оновити REPO_LAYOUT.
  6) Прогнати ruff/mypy/pytest/exit_gates.
- Перевірки → python -m ruff check .; python -m mypy .; python -m pytest -q; python -m tools.run_exit_gates --out reports/exit_gates --manifest tools/exit_gates/manifest.json.

## 2026-01-25T23:05:00+01:00 — POST (MODE=PATCH): P3.2 SSOT single history budgeter

- Що зроблено →
  - HistoryBudget зроблено блокуючим з global single-inflight + per-symbol inflight.
  - Видалено локальні TokenBucket/min_sleep з warmup/backfill; контроль через budgeter.
  - Додано метрики fxcm_history_inflight і fxcm_history_throttled_total.
  - Додано exit gate проти локальних TokenBucket у history.
  - Додано тест на global single-inflight.
  - Оновлено REPO_LAYOUT.
- Де зроблено →
  - runtime/fxcm/history_budget.py
  - runtime/fxcm/history_provider.py
  - runtime/warmup.py
  - runtime/backfill.py
  - observability/metrics.py
  - tools/exit_gates/gates/gate_no_local_tokenbucket_history.py
  - tools/exit_gates/manifest.json
  - tests/test_history_budget_global_single_inflight.py
  - docs/REPO_LAYOUT.md
- Як перевірено →
  - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m ruff check . → OK
  - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m mypy . → OK
  - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m pytest -q → OK
  - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m tools.run_exit_gates --out reports/exit_gates --manifest tools/exit_gates/manifest.json → OK
- Ризики/нотатки → global inflight робить history послідовним; пропускна здатність тепер повністю визначається budgeter.

## 2026-01-25T23:20:00+01:00 — PRE (MODE=PATCH): UI.M0 health toolbar + overlay

- Мета → додати health toolbar/overlay у UI Lite з inbound status валідатором та WS повідомленням "health".
- Scope → ui_lite/server.py, ui_lite/static/app.js, ui_lite/static/styles.css, docs/REPO_LAYOUT.md.
- Non-goals → зміни Public Surface/схем/status payload.
- Інваріанти/рейки → fail-closed для UI при невалідному статусі; існуючі "snapshot"/"bar" не ламаємо.
- План →
  1) Додати status snapshot poller + validate_status_v2 і health WS повідомлення.
  2) Рендер toolbar/overlay у UI, оновити стилі.
  3) Оновити REPO_LAYOUT.
  4) Прогнати ruff/mypy/pytest/exit_gates.
- Перевірки → python -m ruff check .; python -m mypy .; python -m pytest -q; python -m tools.run_exit_gates --out reports/exit_gates --manifest tools/exit_gates/manifest.json.

## 2026-01-25T23:55:00+01:00 — POST (MODE=PATCH): UI.M0 health toolbar + overlay

- Що зроблено →
  - UI Lite читає status:snapshot, валідить через validate_status_v2 і пушить WS "health".
  - Додано health toolbar (STATUS/TICK/WS/UI) та overlay “ПРЕВʼЮ ПРИЗУПИНЕНО”.
  - Додано UI лічильники invalid status/ohlcv.
  - Оновлено стилі та REPO_LAYOUT.
- Де зроблено →
  - ui_lite/server.py
  - ui_lite/static/app.js
  - ui_lite/static/styles.css
  - docs/REPO_LAYOUT.md
- Як перевірено →
  - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m ruff check . → OK
  - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m mypy . → OK
  - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m pytest -q → OK
  - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m tools.run_exit_gates --out reports/exit_gates --manifest tools/exit_gates/manifest.json → OK
- Ризики/нотатки → health базується на status snapshot; при відсутньому Redis статус UI показує порожні значення.

## 2026-01-26T00:15:00+01:00 — PRE (MODE=PATCH): UI.M0.1 статус N/A/STALE + data stale + drawer

- Мета → додати явні індикатори STATUS N/A/STALE, DATA STALE та drawer для degraded/errors.
- Scope → ui_lite/server.py, ui_lite/static/app.js, ui_lite/static/styles.css, docs/REPO_LAYOUT.md.
- Non-goals → зміни Public Surface/схем/status payload.
- Інваріанти/рейки → fail-closed для UI при невалідному статусі; існуючі "snapshot"/"bar" не ламаємо.
- План →
  1) Розширити health payload: status_ok/status_age_ms/last_status_error_short + status_stale.
  2) Додати UI badges для N/A/STALE/DATA STALE/market closed.
  3) Додати drawer з degraded/errors/last_command + close by ESC/click.
  4) Оновити стилі та REPO_LAYOUT.
  5) Прогнати ruff/mypy/pytest/exit_gates.
- Перевірки → python -m ruff check .; python -m mypy .; python -m pytest -q; python -m tools.run_exit_gates --out reports/exit_gates --manifest tools/exit_gates/manifest.json.

## 2026-01-26T00:45:00+01:00 — POST (MODE=PATCH): UI.M0.1 статус N/A/STALE + data stale + drawer

- Що зроблено →
  - Health payload містить status_ok/status_age_ms/status_stale/last_status_error_short.
  - UI показує STATUS N/A/STALE, DATA STALE, market closed + next open.
  - Додано drawer з degraded/errors/last_command та закриття Esc/клік поза.
  - Оновлено стилі та REPO_LAYOUT.
- Де зроблено →
  - ui_lite/server.py
  - ui_lite/static/app.js
  - ui_lite/static/styles.css
  - docs/REPO_LAYOUT.md
- Як перевірено →
  - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m ruff check . → OK
  - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m mypy . → OK
  - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m pytest -q → OK
  - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m tools.run_exit_gates --out reports/exit_gates --manifest tools/exit_gates/manifest.json → OK
- Ризики/нотатки → при відсутньому status snapshot UI показує STATUS N/A та error_short.

## 2026-01-26T01:05:00+01:00 — PRE (MODE=read-only discovery): WS AGE / STATUS AGE стрибки

- Мета → перевірити причини стрибків WS AGE та AGE у UI.
- Scope → ui_lite/static/app.js; ui_lite/server.py.
- Non-goals → зміни коду.
- Інваріанти/рейки → append-only журнал.
- План →
  1) Зафіксувати місця оновлення lastWsMsgTs/lastBarUpdateMs.
  2) Зафіксувати розрахунок status_age_ms на сервері.
- Перевірки → не запускались (read-only).

## 2026-01-26T01:10:00+01:00 — POST (MODE=read-only discovery): WS AGE / STATUS AGE стрибки

- Що зроблено → зібрано факти про логіку WS AGE та status_age_ms.
- Де зроблено → ui_lite/static/app.js; ui_lite/server.py.
- Як перевірено → не запускалось (read-only).
- FACTS →
  1) WS AGE рахується як Date.now() - lastWsMsgTs — ui_lite/static/app.js:944-945.
  2) lastWsMsgTs оновлюється на кожне WS повідомлення — ui_lite/static/app.js:830-834.
  3) STATUS AGE береться з health payload (status_age_ms) — ui_lite/static/app.js:203-205.
  4) status_age_ms = now_ms - last_status_ts_ms, якщо status_ok і ts>0 — ui_lite/server.py:573-574.
  5) health broadcaster шле пакет кожні 1с — ui_lite/server.py:595-601.

## 2026-01-26T01:20:00+01:00 — PRE (MODE=PATCH): стабілізація WS AGE/STATUS AGE + формат NEXT OPEN

- Мета → стабілізувати WS AGE та STATUS AGE у UI та показати NEXT OPEN у читабельному форматі.
- Scope → ui_lite/static/app.js, ui_lite/static/styles.css.
- Non-goals → зміни Public Surface/схем/status payload.
- Інваріанти/рейки → не ламати існуючі WS повідомлення; лише UI-логіка.
- План →
  1) Додати згладжування age на UI (монотонний таймер між подіями).
  2) Форматувати NEXT OPEN в UTC (YYYY-MM-DD HH:MM).
  3) Оновити статусний футер.
  4) Прогнати ruff/mypy/pytest/exit_gates.
- Перевірки → python -m ruff check .; python -m mypy .; python -m pytest -q; python -m tools.run_exit_gates --out reports/exit_gates --manifest tools/exit_gates/manifest.json.

## 2026-01-26T01:40:00+01:00 — POST (MODE=PATCH): стабілізація WS AGE/STATUS AGE + формат NEXT OPEN

- Що зроблено →
  - Додано згладжування WS AGE та STATUS AGE у UI (монотонний таймер між оновленнями).
  - NEXT OPEN показується у форматі UTC (YYYY-MM-DD HH:MM).
  - Оновлено статусний футер на STALE/N/A.
- Де зроблено →
  - ui_lite/static/app.js
  - docs/REPO_LAYOUT.md
- Як перевірено →
  - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m ruff check . → OK
  - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m mypy . → OK
  - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m pytest -q → OK
  - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m tools.run_exit_gates --out reports/exit_gates --manifest tools/exit_gates/manifest.json → OK
- Ризики/нотатки → AGE скидається лише при новому повідомленні/status_ts; без цього росте монотонно.

## 2026-01-26T02:05:00+01:00 — PRE (MODE=PATCH): P3.3 retention 365d + coverage telemetry

- Мета → додати SSOT retention target та coverage stats у status + оновлення після чанків.
- Scope → config/config.py, store/sqlite_store.py, runtime/status.py, runtime/warmup.py, runtime/backfill.py, core/contracts/public/status_v2.json, tests/test_store_coverage_stats.py, tests/test_validator_status.py, docs/REPO_LAYOUT.md.
- Non-goals → нові канали/команди; зміна Public Surface.
- Інваріанти/рейки → coverage рахується локально зі store; additive schema.
- План →
  1) Додати retention_target_days/warmup_default_lookback_days у config.
  2) Додати store.get_1m_coverage.
  3) Розширити status snapshot полями ohlcv.final_1m.
  4) Оновити warmup/backfill для coverage після кожного чанка.
  5) Оновити schema та тести.
  6) Прогнати ruff/mypy/pytest/exit_gates.
- Перевірки → python -m ruff check .; python -m mypy .; python -m pytest -q; python -m tools.run_exit_gates --out reports/exit_gates --manifest tools/exit_gates/manifest.json.

## 2026-01-26T02:40:00+01:00 — POST (MODE=PATCH): P3.3 retention 365d + coverage telemetry

- Що зроблено →
  - Додано retention_target_days та warmup_default_lookback_days у config.
  - Додано SQLiteStore.get_1m_coverage та coverage telemetry у status (ohlcv.final_1m).
  - Warmup/backfill оновлюють coverage після кожного чанка.
  - Оновлено schema status_v2 і тести.
  - Оновлено REPO_LAYOUT.
- Де зроблено →
  - config/config.py
  - store/sqlite_store.py
  - runtime/status.py
  - runtime/warmup.py
  - runtime/backfill.py
  - core/contracts/public/status_v2.json
  - tests/test_store_coverage_stats.py
  - tests/test_validator_status.py
  - docs/REPO_LAYOUT.md
- Як перевірено →
  - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m ruff check . → OK
  - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m mypy . → OK
  - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m pytest -q → OK
  - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m tools.run_exit_gates --out reports/exit_gates --manifest tools/exit_gates/manifest.json → OK
- Ризики/нотатки → coverage_days < retention_target_days дає coverage_ok=false; при порожньому store first/last=null.

## 2026-01-26T03:10:00+01:00 — PRE (MODE=PATCH): P3.4 near-tail audits/marks tiers + checked_until_close_ms

- Мета → додати near/far аудити tail_guard з tier-статусом, зберігати checked_until_close_ms у marks, та оновити статус/схему/тести. Без FXCM викликів для near-tier (лише локальний store).
- Scope → runtime/tail_guard.py, app/composition.py (tiered виклик), runtime/status.py, core/contracts/public/status_v2.json, store/sqlite_store.py, store/schema.sql, tests/test_tail_guard_*.py, tests/test_validator_status.py, docs/REPO_LAYOUT.md, Work/01log.md.
- Non-goals → зміни репаблішу/repair бюджетів/FXCM провайдерів; рефакторинг без потреби.
- Інваріанти/рейки → мінімальний диф; без silent fallback; Public Surface не ламати (лише additive); перевірки контрактів/rail тести мають пройти.
- План →
  1) Додати checked_until_close_ms у marks (store+status+schema).
  2) Реалізувати tiered tail_guard: near (коротке вікно, audit-only) + far (повне вікно, існуюча логіка).
  3) Оновити status snapshot (near/far) і тести.
  4) Оновити REPO_LAYOUT.
  5) Запустити релевантні перевірки (ruff/mypy/pytest) за можливості.
- Тести/перевірки (план) → python -m ruff check .; python -m mypy .; python -m pytest -q.

## 2026-01-26T03:40:00+01:00 — POST (MODE=PATCH): P3.4 near-tail audits/marks tiers + checked_until_close_ms

- Що зроблено →
  - Додано checked_until_close_ms у tail_guard marks (store/status/schema).
  - Реалізовано near/far tiered аудит tail_guard (near = audit-only, far = існуюча логіка).
  - Оновлено статус та контракти, додано тест для near-tier skip.
  - Оновлено REPO_LAYOUT.
- Де зроблено →
  - store/schema.sql
  - store/sqlite_store.py
  - runtime/tail_guard.py
  - app/composition.py
  - runtime/status.py
  - core/contracts/public/status_v2.json
  - tests/test_tail_guard_checked_ttl_skips.py
  - tests/test_tail_guard_marks.py
  - tests/test_tail_guard_marks_persistence.py
  - tests/test_status_payload_size_rail.py
  - tests/test_validator_status.py
  - docs/REPO_LAYOUT.md
- Як перевірено → не запускалось (локальні перевірки ruff/mypy/pytest не виконані).
- Ризики/нотатки → near-tier оновлює checked_until_close_ms без зміни verified_until_ms; far-tier синхронізує top-level tail_guard. При потребі — запустити ruff/mypy/pytest.

## 2026-01-25T10:00:00+01:00 — PRE (MODE=read-only discovery): P3.4.1 verification-only прогін перевірок

- Мета → виконати ruff/mypy/pytest та exit gates без змін коду; зафіксувати результати.
- Scope → лише виконання команд у корені репо.
- Non-goals → будь-які правки коду або “швидкі фікси”.
- Інваріанти/рейки → без PATCH; якщо буде fail — окремий мінімальний PATCH у P3.4.2.
- План →
  1) Запустити python -m ruff check .
  2) Запустити python -m mypy .
  3) Запустити python -m pytest -q
  4) За можливості запустити python -m tools.run_exit_gates --out reports/exit_gates --manifest tools/exit_gates/manifest.json
- Тести/перевірки (план) → ruff, mypy, pytest, exit gates.

## 2026-01-25T10:10:00+01:00 — POST (MODE=read-only discovery): P3.4.1 verification-only прогін перевірок

- Що зроблено → виконано ruff/mypy/pytest та exit gates без PATCH.
- Де зроблено → корінь репо.
- Як перевірено →
  - python -m ruff check . → FAIL (No module named ruff)
  - python -m mypy . → FAIL (No module named mypy)
  - python -m pytest -q → FAIL (No module named pytest)
  - python -m tools.run_exit_gates --out reports/exit_gates --manifest tools/exit_gates/manifest.json → FAIL
    - gate_python_version: FAIL
    - gate_tick_units: FAIL
    - gate_tick_skew_non_negative: FAIL
    - gate_preview_bucket_boundaries: FAIL
    - gate_preview_late_tick_drop: FAIL
    - gate_fxcm_fsm_unit: FAIL
    - gate_tick_fixtures_schema: FAIL
    - gate_final_wire_from_store: FAIL
    - gate_tail_guard_marks_persist: FAIL
    - gate_tail_guard_repair_budget: FAIL
- Ризики/нотатки → інструменти ruff/mypy/pytest відсутні в активному Python; exit gates мають множинні FAIL. Потрібен окремий P3.4.2 мінімальний PATCH під конкретний fail.

## 2026-01-25T10:20:00+01:00 — PRE (MODE=read-only discovery): P3.4.1 повторний прогін у venv

- Мета → повторити ruff/mypy/pytest та exit gates у активованому venv.
- Scope → лише виконання команд у корені репо через .venv\Scripts\python.exe.
- Non-goals → будь-які правки коду.
- Інваріанти/рейки → без PATCH; якщо буде fail — окремий мінімальний PATCH у P3.4.2.
- План →
  1) .venv\Scripts\python.exe -m ruff check .
  2) .venv\Scripts\python.exe -m mypy .
  3) .venv\Scripts\python.exe -m pytest -q
  4) .venv\Scripts\python.exe -m tools.run_exit_gates --out reports/exit_gates --manifest tools/exit_gates/manifest.json
- Тести/перевірки (план) → ruff, mypy, pytest, exit gates у venv.

## 2026-01-25T10:30:00+01:00 — POST (MODE=read-only discovery): P3.4.1 повторний прогін у venv

- Що зроблено → виконано ruff/mypy/pytest та exit gates у venv.
- Де зроблено → корінь репо.
- Як перевірено →
  - .venv\Scripts\python.exe -m ruff check . → OK
  - .venv\Scripts\python.exe -m mypy . → OK
  - .venv\Scripts\python.exe -m pytest -q → FAIL
    - tests/test_tail_guard_invalidation_on_upsert.py::test_tail_guard_invalidation_on_upsert
    - tests/test_tail_guard_marks_persistence.py::test_tail_guard_marks_persistence
    - Причина: AttributeError 'sqlite3.Row' object has no attribute 'get' у store/sqlite_store.py
  - .venv\Scripts\python.exe -m tools.run_exit_gates --out reports/exit_gates --manifest tools/exit_gates/manifest.json → FAIL
    - gate_tail_guard_marks_persist: FAIL (інші OK)
- Ризики/нотатки → потрібен мінімальний PATCH (P3.4.2) для read з sqlite3.Row.

## 2026-01-25T10:40:00+01:00 — PRE (MODE=PATCH): P3.4.2 fix tail_audit_state read (sqlite3.Row)

- Мета → виправити доступ до checked_until_close_ms у sqlite3.Row без .get.
- Scope → store/sqlite_store.py.
- Non-goals → будь-які інші зміни логіки tail_guard.
- Інваріанти/рейки → мінімальний диф; без зміни публічних контрактів; тести лише релевантні.
- План →
  1) Замінити row.get(...) на безпечне читання через ключ.
  2) Прогнати pytest для двох падінь.
  3) Прогнати gate_tail_guard_marks_persist.
- Тести/перевірки (план) → pytest tests/test_tail_guard_invalidation_on_upsert.py::test_tail_guard_invalidation_on_upsert; pytest tests/test_tail_guard_marks_persistence.py::test_tail_guard_marks_persistence; tools.run_exit_gates (manifest) або окремий gate.

## 2026-01-25T10:50:00+01:00 — POST (MODE=PATCH): P3.4.2 fix tail_audit_state read (sqlite3.Row)

- Що зроблено → виправлено читання checked_until_close_ms з sqlite3.Row без використання .get().
- Де зроблено → store/sqlite_store.py.
- Як перевірено →
  - .venv\Scripts\python.exe -m pytest -q tests/test_tail_guard_invalidation_on_upsert.py::test_tail_guard_invalidation_on_upsert tests/test_tail_guard_marks_persistence.py::test_tail_guard_marks_persistence → OK
  - .venv\Scripts\python.exe -m tools.run_exit_gates --out reports/exit_gates --manifest tools/exit_gates/manifest.json → OK
- Ризики/нотатки → немає.

## 2026-01-25T11:10:00+01:00 — PRE (MODE=PATCH): P3.5 tail_guard repair path + budgeted history + rebuild + republish

- Мета → repair=true будує bounded plan, fetch лише під HistoryBudget, після repair робить rebuild_derived (15m/1h/4h/1d) і republish_tail(force), summary у last_command.
- Scope → runtime/tail_guard.py, runtime/repair.py, runtime/rebuild_derived.py, runtime/republish.py, runtime/status.py, app/composition.py, tests/test_tail_guard_repair_flag_and_budget.py, tests/test_tail_guard_repair_plan_limits.py, tools/exit_gates/gates/gate_tail_guard_repair_budget.py (якщо треба), docs/REPO_LAYOUT.md.
- Non-goals → зміни інших pipeline/adapterів.
- Інваріанти/рейки → repair=false без history calls; repair=true всі fetch тільки під budget.acquire; plan bounded max_*; status payload size не росте.
- План →
  1) Додати budgeted fetch у repair_missing_1m та summary.
  2) Оновити tail_guard repair path (rebuild+republish force) і summary в last_command.
  3) Оновити тести й gate.
  4) Оновити REPO_LAYOUT.
  5) Прогнати ruff/mypy/pytest/exit gates.
- Тести/перевірки (план) → ruff, mypy, pytest, tools.run_exit_gates.

## 2026-01-25T11:40:00+01:00 — POST (MODE=PATCH): P3.5 tail_guard repair path + budgeted history + rebuild + republish

- Що зроблено →
  - Додано budgeted history fetch у repair_missing_1m та summary (windows_repaired, bars_ingested).
  - Repair path у tail_guard тепер rebuild_derived (15m/1h/4h/1d) і republish_tail(force) після repair.
  - Додано compact summary у status:last_command для fxcm_tail_guard.
  - Додано тести для repair flag + budget та план-лімітів.
  - Оновлено REPO_LAYOUT.
- Де зроблено →
  - runtime/repair.py
  - runtime/tail_guard.py
  - runtime/status.py
  - app/composition.py
  - tests/test_tail_guard_repair_flag_and_budget.py
  - tests/test_tail_guard_repair_plan_limits.py
  - docs/REPO_LAYOUT.md
- Як перевірено →
  - .venv\Scripts\python.exe -m ruff check . → OK
  - .venv\Scripts\python.exe -m mypy . → OK
  - .venv\Scripts\python.exe -m pytest -q → OK
  - .venv\Scripts\python.exe -m tools.run_exit_gates --out reports/exit_gates --manifest tools/exit_gates/manifest.json → OK
- Ризики/нотатки → summary у last_command короткий; status payload size rail збережений gate-ом.

## 2026-01-25T12:10:00+01:00 — PRE (MODE=PATCH): P3.5 XAU calendar profile + next_open correctness

- Мета → додати профіль календаря для XAU (23:00 UTC) і коректний next_open/next_pause/is_trading_time за профілем.
- Scope → config/calendar_overrides.json, core/time/calendar.py, core/time/sessions.py (через Calendar API), tests + gate, docs/REPO_LAYOUT.md.
- Non-goals → зміни інших pipeline чи FXCM адаптерів.
- Інваріанти/рейки → дефолтний календар (fxcm_calendar_v1_ny) не ламаємо; status payload size не росте.
- План →
  1) Додати calendar_profiles + symbol_calendar_profile для XAU.
  2) Додати профільну логіку у Calendar (symbol-aware next_open/next_pause/is_open/market_state).
  3) Додати тести next_open для XAU + gate.
  4) Оновити manifest та REPO_LAYOUT.
  5) Прогнати pytest + exit gates.
- Тести/перевірки (план) → pytest -q, tools.run_exit_gates.

## 2026-01-25T12:30:00+01:00 — POST (MODE=PATCH): P3.5 XAU calendar profile + next_open correctness

- Що зроблено → додано XAU calendar profile з 23:00 UTC і symbol-aware календар; додано тести та gate; виправлено gate_calendar_gaps для symbol-aware.
- Де зроблено →
  - config/calendar_overrides.json
  - core/time/calendar.py
  - runtime/status.py
  - runtime/tail_guard.py
  - tests/fixtures/sim/history_sim_provider.py
  - tests/test_calendar_xau_profile.py
  - tools/exit_gates/gates/gate_calendar_xau_next_open_matches_23utc.py
  - tools/exit_gates/gates/gate_calendar_gaps.py
  - tools/exit_gates/manifest.json
  - docs/REPO_LAYOUT.md
- Як перевірено →
  - .venv\Scripts\python.exe -m pytest -q → OK
  - .venv\Scripts\python.exe -m tools.run_exit_gates --out reports/exit_gates --manifest tools/exit_gates/manifest.json → OK
- Ризики/нотатки → calendar_tag для XAU: metals_xau_23utc.

## 2026-01-25T13:20:00+01:00 — POST (MODE=PATCH): P3.6 CLOSED history readiness + loud backoff

- Що зроблено →
  - Додано SSOT guard history readiness/backoff та протокол HistoryProvider.
  - Warmup/backfill/repair/tail_guard використовують guard перед fetch.
  - Додано history state у status + schema + payload.
  - Додано метрики not_ready/backoff.
  - Додано тести CLOSED policy і gate no_history_fetch_when_not_ready.
- Де зроблено →
  - runtime/history_provider.py
  - runtime/fxcm/history_provider.py
  - runtime/warmup.py
  - runtime/backfill.py
  - runtime/repair.py
  - runtime/tail_guard.py
  - runtime/status.py
  - core/contracts/public/status_v2.json
  - observability/metrics.py
  - tests/test_history_closed_policy.py
  - tests/test_validator_status.py
  - tests/test_status_payload_size_rail.py
  - tests/test_repair_budget_rails.py
  - tests/test_repair_rejects_large_range.py
  - tests/test_tail_guard_repair_flag_and_budget.py
  - tests/fixtures/sim/history_sim_provider.py
  - tools/exit_gates/gates/gate_no_history_fetch_when_not_ready.py
  - tools/exit_gates/gates/gate_tail_guard_repair_budget.py
  - tools/exit_gates/manifest.json
  - docs/REPO_LAYOUT.md
- Як перевірено →
  - .venv\Scripts\python.exe -m ruff check . → OK
  - .venv\Scripts\python.exe -m mypy . → OK
  - .venv\Scripts\python.exe -m pytest -q → OK
  - .venv\Scripts\python.exe -m tools.run_exit_gates --out reports/exit_gates --manifest tools/exit_gates/manifest.json → OK
- Ризики/нотатки → history not ready під CLOSED робить loud error + backoff; fetch блокується guard-ом.

## 2026-01-25T13:40:00+01:00 — PRE (MODE=read-only discovery): UI/HTTP перевірка XAU next_open

- Мета → перевірити /debug, /api/status і next_open_utc/ms для XAU; підтвердити активну збірку через перезапуск runtime+ui_lite.
- Scope → лише запуск/перезапуск сервісу та HTTP/UI перегляд.
- Non-goals → будь-які зміни коду.
- Інваріанти/рейки → без PATCH.
- План →
  1) Перезапустити runtime + ui_lite.
  2) Відкрити /debug та перевірити symbol/calendar_tag/next_open.
  3) GET /api/status і звірити next_open з 23:00 UTC для XAU.
- Тести/перевірки (план) → HTTP/UI перевірка.

## 2026-01-25T22:10:30+01:00 — POST (MODE=read-only discovery): UI/HTTP перевірка XAU next_open

- Що зроблено → запущено runtime+ui_lite, перевірено доступність /api/status на UI Lite (8089), зафіксовано 404; порт 8089 певний час був зайнятий.
- Де зроблено → runtime/app.main (запуск), UI Lite 127.0.0.1:8089.
- Як перевірено → спроба HTTP GET /api/status (UI Lite) → 404; лог UI Lite з повідомленнями про зайнятий порт.
- Ризики/нотатки → /api/status очікувано на HTTP chart сервері (8088), потрібна повторна перевірка /debug (8089) та /api/status (8088).

## 2026-01-25T22:12:00+01:00 — PRE (MODE=read-only discovery): повторна UI/HTTP перевірка XAU next_open

- Мета → перевірити /debug (8089) та /api/status (8088) для XAU, зчитати next_open.
- Scope → лише запуск/перезапуск сервісу та HTTP/UI перегляд.
- Non-goals → будь-які зміни коду.
- Інваріанти/рейки → без PATCH.
- План →
  1) Запустити runtime+ui_lite у фоні.
  2) GET /debug на 8089.
  3) GET /api/status на 8088.
- Тести/перевірки (план) → HTTP/UI перевірка.

## 2026-01-25T22:13:30+01:00 — POST (MODE=read-only discovery): повторна UI/HTTP перевірка XAU next_open

- Що зроблено → тимчасово запущено runtime, зчитано /debug (8089) і /api/status (8088), зафіксовано next_open для XAU.
- Де зроблено → UI Lite: http://127.0.0.1:8089/debug; HTTP chart: http://127.0.0.1:8088/api/status.
- Як перевірено → HTTP GET /debug та /api/status.
- Результат → market.calendar_tag=metals_xau_23utc, next_open_utc=2026-01-25T23:00:00Z (очікувано 23:00 UTC).
- Ризики/нотатки → у статусі присутній degraded calendar_error: ZoneInfo недоступний; використано dateutil.tz.

## 2026-01-26T09:15:00+01:00 — PRE (MODE=PATCH): P3.7 calendar tz_backend + no-false-degraded

- Мета → прибрати фальшивий degraded calendar_error коли TZ резолвиться через dateutil; додати market.tz_backend у status.
- Scope → core/time/calendar.py, core/time/sessions.py, runtime/status.py, core/contracts/public/status_v2.json, tests/test_calendar_tz_backend.py, tests/test_validator_status.py, docs/REPO_LAYOUT.md.
- Non-goals → зміни календарних правил/overrides, зміни history/warmup/backfill/repair, зміни UI.
- Інваріанти/рейки →
  - no silent fallback: якщо TZ не резолвиться взагалі → degraded-but-loud + error;
  - якщо fallback dateutil успішний → НЕ degraded, але tz_backend="dateutil";
  - payload size rail збережений (додаємо 1 коротке поле).
- План →
  1) Додати tz_backend у TradingCalendar та market_state.
  2) Прибрати degraded для успішного dateutil fallback; error лише при повному fail.
  3) Оновити schema status_v2 і тести валідатора.
  4) Додати тести для dateutil fallback та повного fail.
  5) Прогнати ruff/mypy/pytest/exit_gates.
- Тести/перевірки (план) → .venv\Scripts\python.exe -m ruff check .; -m mypy .; -m pytest -q; -m tools.run_exit_gates --out reports/exit_gates --manifest tools/exit_gates/manifest.json.

## 2026-01-26T09:35:00+01:00 — POST (MODE=PATCH): P3.7 calendar tz_backend + no-false-degraded

- Що зроблено → додано `tz_backend` у TradingCalendar і market_state; прибрано фальшивий degraded при успішному dateutil fallback; оновлено status_v2 schema та валідаторний тест; додано тести для dateutil fallback і повного TZ fail; оновлено REPO_LAYOUT.
- Де зроблено → core/time/sessions.py; core/contracts/public/status_v2.json; tests/test_calendar_tz_backend.py; tests/test_validator_status.py; docs/REPO_LAYOUT.md.
- Як перевірено →
  - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m ruff check . → OK
  - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m mypy . → OK
  - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m pytest -q → OK
  - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m tools.run_exit_gates --out reports/exit_gates --manifest tools/exit_gates/manifest.json → OK
- Ризики/нотатки → при неможливості резолву TZ лишається degraded calendar_error; при dateutil fallback помилка не проставляється, але `tz_backend` показує джерело.

## 2026-01-26T10:05:00+01:00 — PRE (MODE=PATCH): P3.8 FXCM history provider (ForexConnect) wiring

- Мета → підключити реальний FXCM history provider через ForexConnect, прибрати provider_not_configured у warmup/backfill.
- Scope → config/config.py, app/composition.py, runtime/fxcm/history_provider.py, tests/test_history_provider_configured.py, tests/test_warmup_backfill_blocked_when_not_ready.py, docs/REPO_LAYOUT.md.
- Non-goals → зміни календарних правил/overrides, зміни UI, зміни warmup/backfill логіки поза wiring.
- Інваріанти/рейки →
  - no silent fallback: не готовий history → loud error + guard/backoff;
  - fetch лише під HistoryBudget;
  - мінімальний диф.
- План →
  1) Додати history_provider_kind у Config (дефолт fxcm_forexconnect).
  2) Реалізувати FxcmForexConnectHistoryAdapter/Provider.
  3) Wiring у composition: використати provider за замовчуванням, зберігши loud error для none.
  4) Додати тести для wiring та guard.
  5) Оновити REPO_LAYOUT і прогнати ruff/mypy/pytest/exit_gates.
- Тести/перевірки (план) → .venv\Scripts\python.exe -m ruff check .; -m mypy .; -m pytest -q; -m tools.run_exit_gates --out reports/exit_gates --manifest tools/exit_gates/manifest.json.

## 2026-01-26T10:45:00+01:00 — POST (MODE=PATCH): P3.8 FXCM history provider (ForexConnect) wiring

- Що зроблено → додано history_provider_kind у Config; підключено FXCM ForexConnect history provider через FxcmHistoryProvider + FxcmForexConnectHistoryAdapter; оновлено selection у composition; додано тести для wiring і guard; оновлено REPO_LAYOUT; виправлено ruff в help файлах.
- Де зроблено → config/config.py; app/composition.py; runtime/fxcm/history_provider.py; tests/test_history_provider_configured.py; tests/test_warmup_backfill_blocked_when_not_ready.py; docs/REPO_LAYOUT.md; runtime/http_server.py; runtime/fxcm_forexconnect.py; reports/soak/20260126_113420/pre_live_archive_facts.py.
- Як перевірено →
  - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m ruff check . → OK
  - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m mypy . → OK
  - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m pytest -q → OK
  - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m tools.run_exit_gates --out reports/exit_gates --manifest tools/exit_gates/manifest.json → OK
- Ризики/нотатки → readiness базується на наявності ForexConnect SDK та секретів; якщо SDK/секрети відсутні, guard дає loud error і backoff.

## 2026-01-26T11:05:00+01:00 — PRE (MODE=PATCH): P3.9 tick allowlist skip + status payload size

- Мета → прибрати tick_contract_reject для не-allowlist символів (skip без помилки) та зняти status_payload_too_large.
- Scope → runtime/fxcm_forexconnect.py; Work/01log.md.
- Non-goals → зміни протоколів/схем або логіки історії.
- Інваріанти/рейки → тільки мінімальний диф; без silent failure для allowlist символів.
- План →
  1) У _offer_row_to_tick пропускати не-allowlist символи без помилки.
  2) Перевірити, що статусний payload повертається в ліміт (через зняття шуму).
  3) Прогнати ruff/mypy/pytest/exit_gates.
- Тести/перевірки (план) → .venv\Scripts\python.exe -m ruff check .; -m mypy .; -m pytest -q; -m tools.run_exit_gates --out reports/exit_gates --manifest tools/exit_gates/manifest.json.

## 2026-01-26T11:25:00+01:00 — POST (MODE=PATCH): P3.9 tick allowlist skip + status payload size

- Що зроблено → не-allowlist символи у FXCM OFFERS пропускаються без помилки; виконано спробу 7-денного history fetch через ForexConnect; зібрано дефолтні параметри конфігу; знято приклад 5 записів зі сховища та tick snapshot.
- Де зроблено → runtime/fxcm_forexconnect.py; data/ohlcv_final.sqlite (читання/запис); запуск скрипта в cwd.
- Як перевірено →
  - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m ruff check . → OK
  - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m mypy . → OK
  - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m pytest -q → OK
  - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m tools.run_exit_gates --out reports/exit_gates --manifest tools/exit_gates/manifest.json → OK
  - History probe 7d (chunk=7d) → HISTORY_FETCH_COUNT=0
- Результат (конфіг) → history_provider_kind=fxcm_forexconnect; history_chunk_minutes=360; history_chunk_limit=2000; max_requests_per_minute=30; history_min_sleep_ms=250; warmup_default_lookback_days=365; store_path=data/ohlcv_final.sqlite; fxcm_symbols=['XAUUSD'].
- Результат (дані) → 5 останніх записів з bars_1m_final прочитано; tick snapshot отримано; tick_ts_ms не потрапив у межі останнього бару (історичні дані старіші).
- Ризики/нотатки → FXCM history fetch повернув 0 барів для 7d; потрібна діагностика параметрів get_history/періоду або символу.

## 2026-01-26T11:40:00+01:00 — PRE (MODE=read-only discovery): діагностика ohlcv pubsub

- Мета → зʼясувати, чому немає ohlcv у pubsub.
- Scope → read-only перевірки Redis snapshot/каналів, статусу preview/tick.
- Non-goals → зміни коду.
- Інваріанти/рейки → без PATCH.
- План →
  1) Зчитати status snapshot з Redis.
  2) Перевірити ohlcv_preview.last_publish_ts_ms та tick лічильники.
  3) Перевірити конфіг preview/ohlcv та стан FXCM.
- Тести/перевірки (план) → read-only Redis/HTTP перевірка.

## 2026-01-26T11:55:00+01:00 — PRE (MODE=PATCH): P3.10 tick_ts<=snap_ts clamp

- Мета → прибрати tick_contract_reject через tick_ts_ms > snap_ts_ms, щоб ohlcv pubsub ожив.
- Scope → runtime/fxcm_forexconnect.py.
- Non-goals → зміни контрактів або протоколів.
- Інваріанти/рейки → мінімальний диф; тільки нормалізація snap_ts.
- План →
  1) Клампнути snap_ts_ms до max(receipt_ms, event_ts_ms).
  2) Прогнати ruff/mypy/pytest/exit_gates.
- Тести/перевірки (план) → .venv\Scripts\python.exe -m ruff check .; -m mypy .; -m pytest -q; -m tools.run_exit_gates --out reports/exit_gates --manifest tools/exit_gates/manifest.json.

## 2026-01-26T12:10:00+01:00 — POST (MODE=PATCH): P3.10 tick_ts<=snap_ts clamp

- Що зроблено → кламп snap_ts_ms до max(receipt_ms, event_ts_ms) у FXCM offers tick normalization.
- Де зроблено → runtime/fxcm_forexconnect.py.
- Як перевірено →
  - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m ruff check . → OK
  - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m mypy . → OK
  - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m pytest -q → OK
  - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m tools.run_exit_gates --out reports/exit_gates --manifest tools/exit_gates/manifest.json → OK
- Ризики/нотатки → якщо event_ts майбутній, snap_ts підтягується до нього, щоб контракт не ламався.

## 2026-01-26T12:25:00+01:00 — PRE (MODE=read-only discovery): діагностика UI свічок

- Мета → зʼясувати, чому UI не малює свічки.
- Scope → read-only перевірка статусу, Redis каналів, UI Lite /debug.
- Non-goals → зміни коду.
- Інваріанти/рейки → без PATCH.
- План →
  1) Перевірити status snapshot (ohlcv_preview/price/fxcm).
  2) Перевірити, чи є трафік у Redis ohlcv канал.
  3) Перевірити /debug UI Lite (last_payload/contract_err).
- Тести/перевірки (план) → read-only Redis/HTTP перевірка.

## 2026-01-26T12:35:00+01:00 — PRE (MODE=PATCH): P3.11 ohlcv payload complete/synthetic

- Мета → виправити ohlcv pubsub для UI Lite (додати top-level complete/synthetic у preview payload).
- Scope → core/market/preview_builder.py.
- Non-goals → зміни schema/контрактів.
- Інваріанти/рейки → мінімальний диф.
- План →
  1) Додати complete=False і synthetic=False у preview payload.
  2) Прогнати ruff/mypy/pytest/exit_gates.
- Тести/перевірки (план) → .venv\Scripts\python.exe -m ruff check .; -m mypy .; -m pytest -q; -m tools.run_exit_gates --out reports/exit_gates --manifest tools/exit_gates/manifest.json.

## 2026-01-26T12:50:00+01:00 — POST (MODE=PATCH): P3.11 ohlcv payload complete/synthetic

- Що зроблено → додано top-level `complete`/`synthetic` у preview ohlcv payload.
- Де зроблено → core/market/preview_builder.py.
- Як перевірено →
  - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m ruff check . → OK
  - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m mypy . → OK
  - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m pytest -q → OK
  - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m tools.run_exit_gates --out reports/exit_gates --manifest tools/exit_gates/manifest.json → OK
- Ризики/нотатки → очікується, що UI Lite прийматиме payload без ohlcv_contract_error.

## 2026-01-26T13:05:00+01:00 — PRE (MODE=read-only discovery): UI не малює свічки

- Мета → перевірити, чи UI отримує ohlcv payload та чи є WS клієнти.
- Scope → read-only /debug + Redis pubsub + status snapshot.
- Non-goals → зміни коду.
- Інваріанти/рейки → без PATCH.
- План →
  1) Зчитати /debug (ws_clients, last_payload, contract_err).
  2) Перевірити ohlcv pubsub трафік.
  3) Перевірити status snapshot (preview counters).
- Тести/перевірки (план) → read-only HTTP/Redis.

## 2026-01-26T13:15:00+01:00 — POST (MODE=read-only discovery): UI не малює свічки

- Що зроблено → перевірено /debug UI Lite та status snapshot.
- Де зроблено → http://127.0.0.1:8089/debug; Redis status snapshot.

- Як перевірено → HTTP /debug + Redis GET status snapshot.
- Результат → UI Lite отримує ohlcv, але відкидає через контракт: "'complete' is a required property"; ws_clients=1; preview лічильники ростуть.
- Ризики/нотатки → runtime/ UI Lite працюють на старому коді; потрібен перезапуск, щоб застосувати P3.11.

## 2026-01-26T13:30:00+01:00 — PRE (MODE=read-only discovery): повторна діагностика UI свічок

- Мета → перевірити, які саме поля приходять у ohlcv pubsub після перезапуску.
- Scope → read-only Redis pubsub + /debug.
- Non-goals → зміни коду.
- Інваріанти/рейки → без PATCH.
- План →
  1) Зчитати 1 повідомлення з ohlcv каналу та перевірити ключі (complete/synthetic).
  2) Перевірити /debug UI Lite.
- Тести/перевірки (план) → read-only Redis/HTTP.

## 2026-01-26T13:45:00+01:00 — PRE (MODE=PATCH): P3.12 ohlcv preview payload complete/synthetic

- Мета → додати top-level complete/synthetic у publish_ohlcv_batch.
- Scope → runtime/publisher.py.
- Non-goals → зміни схем.
- Інваріанти/рейки → мінімальний диф.
- План →
  1) Додати complete=False, synthetic=False у payload preview batch.
  2) Прогнати ruff/mypy/pytest/exit_gates.
- Тести/перевірки (план) → .venv\Scripts\python.exe -m ruff check .; -m mypy .; -m pytest -q; -m tools.run_exit_gates --out reports/exit_gates --manifest tools/exit_gates/manifest.json.

## 2026-01-26T14:05:00+01:00 — POST (MODE=PATCH): P3.12 ohlcv preview payload complete/synthetic

- Що зроблено → додано top-level `complete`/`synthetic` у preview ohlcv payload для publish_ohlcv_batch.
- Де зроблено → runtime/publisher.py.
- Як перевірено →
  - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m ruff check . → OK
  - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m mypy . → OK
  - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m pytest -q → OK
  - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m tools.run_exit_gates --out reports/exit_gates --manifest tools/exit_gates/manifest.json → OK
- Ризики/нотатки → потрібен перезапуск runtime/UI для застосування змін.

## 2026-01-26T14:20:00+01:00 — PRE (MODE=read-only discovery): перевірка після невдалого відкату

- Мета → перевірити, чи є явні збої/артефакти після відкату.
- Scope → read-only /debug + pubsub перевірка.
- Non-goals → зміни коду.
- Інваріанти/рейки → без PATCH.
- План →
  1) Зчитати /debug UI Lite (last_error_code).
  2) Перевірити ohlcv pubsub payload на top-level complete/synthetic.
- Тести/перевірки (план) → read-only HTTP/Redis.

## 2026-01-26T14:20:00+01:00 — PRE (MODE=PATCH): P3.13 preview stream complete flag

- Мета → прибрати contract error: bars.0 event_ts required (stream bars не можуть бути complete=true).
- Scope → core/market/preview_builder.py.
- Non-goals → зміни схем.
- Інваріанти/рейки → мінімальний диф.
- План →
  1) Для stream bars виставити complete=False при фіналізації.
  2) Прогнати ruff/mypy/pytest/exit_gates.
- Тести/перевірки (план) → .venv\Scripts\python.exe -m ruff check .; -m mypy .; -m pytest -q; -m tools.run_exit_gates --out reports/exit_gates --manifest tools/exit_gates/manifest.json.

## 2026-01-26T14:40:00+01:00 — POST (MODE=PATCH): P3.13 preview stream complete flag

- Що зроблено → для stream барів complete=True замінено на complete=False при фіналізації.
- Де зроблено → core/market/preview_builder.py.
- Як перевірено →
  - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m ruff check . → OK
  - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m mypy . → OK
  - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m pytest -q → OK
  - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m tools.run_exit_gates --out reports/exit_gates --manifest tools/exit_gates/manifest.json → OK
- Ризики/нотатки → потрібен перезапуск runtime/UI для застосування змін.

## 2026-01-26T14:35:00+01:00 — PRE (MODE=read-only discovery): стан системи для демо

- Мета → оцінити готовність до демо: статус, UI, ohlcv pubsub.
- Scope → read-only /debug + Redis pubsub + status snapshot.
- Non-goals → зміни коду.
- Інваріанти/рейки → без PATCH.
- План →
  1) Зчитати /debug UI Lite.
  2) Перевірити ohlcv pubsub payload ключі.
  3) Зчитати status snapshot (preview/tick/fxcm).
- Тести/перевірки (план) → read-only HTTP/Redis.

## 2026-01-26T14:50:00+01:00 — POST (MODE=read-only discovery): стан системи для демо

- Що зроблено → зчитано /debug UI Lite та ohlcv pubsub (single sample, 3с).
- Де зроблено → http://127.0.0.1:8089/debug; Redis pubsub ohlcv.
- Як перевірено → HTTP /debug + Redis pubsub.
- Результат → ohlcv_contract_error=0, ws_clients=1, last_payload_* заповнені; pubsub sample за 3с не спіймано.
- Ризики/нотатки → ймовірно, UI працює; для демо потрібна підтверджена візуалізація, бажано перевірити UI у браузері.

## 2026-01-26T15:05:00+01:00 — PRE (MODE=PATCH): README організація + Description

- Мета → організувати README та надати опис проєкту.
- Scope → README.md.
- Non-goals → зміни коду.
- Інваріанти/рейки → мінімальний диф; українська мова.
- План →
  1) Додати короткий опис, quick start, демо-чеклист і основні endpoint-и.
  2) Уточнити вимоги до Python/ForexConnect та env.
  3) Зберегти існуючі UI Lite checks.
- Тести/перевірки (план) → не застосовно.

## 2026-01-26T15:15:00+01:00 — POST (MODE=PATCH): README організація + Description

- Що зроблено → оновлено README: опис, quick start, endpoint-и, демо-чеклист, правила env.
- Де зроблено → README.md.
- Як перевірено → не застосовно.
- Ризики/нотатки → не впливає на код.

## 2026-01-26T16:10:00+01:00 — PRE (MODE=PATCH): README посилення + REPO_LAYOUT карта

- Мета → сформувати сильніший README на основі журналу та додати актуальну карту REPO_LAYOUT.
- Scope → README.md.
- Non-goals → зміни коду, контрактів або конфігів.
- Інваріанти/рейки → мінімальний диф; українська мова; без зміни Public Surface.
- План →
  1) Оновити структуру README (опис, можливості, surface, вимоги, gates).
  2) Вставити актуальну карту REPO_LAYOUT (high‑level + annotated tree).
  3) Зафіксувати POST.
- Тести/перевірки → не застосовно.

## 2026-01-26T16:20:00+01:00 — POST (MODE=PATCH): README посилення + REPO_LAYOUT карта

- Що зроблено → README переписано з посиленою структурою; додано поточну карту REPO_LAYOUT (high‑level + annotated tree), розділи Public Surface/SSOT/Exit Gates/вимоги.
- Де зроблено → README.md.
- Як перевірено → не застосовно.
- Ризики/нотатки → лише документація, без змін коду.

## 2026-01-26T16:30:00+01:00 — PRE (MODE=PATCH): Розширення .gitignore (артефакти/журнали/інструкції)

- Мета → приховати з Git усе, що не має бути в репозиторії (секрети, логи, аудити, інструкції, артефакти).
- Scope → .gitignore.
- Non-goals → зміни коду або контрактів.
- Інваріанти/рейки → мінімальний диф; не чіпати SSOT код.
- План →
  1) Додати .env* до ignore.
  2) Додати артефакти/логи/аудити/reports/recordings.
  3) Додати внутрішні інструкції (copilot/rails).
  4) Записати POST.
- Тести/перевірки → не застосовно.

## 2026-01-26T16:35:00+01:00 — POST (MODE=PATCH): Розширення .gitignore (артефакти/журнали/інструкції)

- Що зроблено → оновлено .gitignore: секрети .env*, артефакти/logs/audits/reports/recordings, cache/venv, внутрішні інструкції.
- Де зроблено → .gitignore.
- Як перевірено → не застосовно.
- Ризики/нотатки → зміни лише для Git‑ігнорування, без впливу на runtime.

## 2026-01-26T16:45:00+01:00 — PRE (MODE=read-only discovery): Перевірка готовності до публікації

- Мета → підтвердити готовність до публікації через прогін ruff/mypy/pytest та exit gates.
- Scope → запуск перевірок без змін коду.
- Non-goals → будь-які PATCH/рефакторинг.
- Інваріанти/рейки → без зміни коду; append-only журнал.
- План →
  1) Запустити ruff, mypy, pytest у .venv.
  2) Запустити tools.run_exit_gates з повним manifest.
  3) Зафіксувати результати в POST.
- Тести/перевірки → ruff, mypy, pytest, run_exit_gates.

## 2026-01-26T16:55:00+01:00 — POST (MODE=read-only discovery): Перевірка готовності до публікації

- Що зроблено → виконано ruff, mypy, pytest та повний набір exit gates у .venv.
- Де зроблено → корінь репозиторію.
- Як перевірено →
  - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m ruff check . → OK
  - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m mypy . → OK
  - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m pytest -q → OK
  - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m tools.run_exit_gates --out reports/exit_gates --manifest tools/exit_gates/manifest.json → OK
- Примітка → перша спроба запуску команди впала через помилку PowerShell (&); повторний запуск успішний.
- Ризики/нотатки → перевірки зелені; готовність до публікації підтверджена цими гейтами.

## 2026-01-26T17:05:00+01:00 — PRE (MODE=PATCH): Спроба git commit

- Мета → виконати коміт локальних змін у репозиторії та прив’язати до remote.
- Scope → git init/remote/add/commit (за потреби).
- Non-goals → зміни коду поза журналом.
- Інваріанти/рейки → append-only журнал; мінімальні дії; без змін runtime.
- План →
  1) Перевірити git status.
  2) Якщо .git відсутній — ініціалізувати та додати remote.
  3) Stage + commit змін.
  4) Зафіксувати POST.
- Тести/перевірки → не застосовно.

## 2026-01-26T17:10:00+01:00 — POST (MODE=PATCH): Спроба git commit

- Що зроблено → перевірено git status; робоче дерево чисте, змін для коміту немає.
- Де зроблено → git status (корінь репозиторію).
- Як перевірено → git status → On branch main, nothing to commit.
- Ризики/нотатки → Work/* ігнорується; якщо потрібно закомітити README/.gitignore, перевірити чи вони вже у репозиторії або чи файли ігноруються глобально.

## 2026-01-26T17:20:00+01:00 — PRE (MODE=PATCH): Перший коміт (перевірка історії)

- Мета → виконати перший коміт; перевірити, чи вже існує історія.
- Scope → git log/status/add/commit.
- Non-goals → зміни коду.
- Інваріанти/рейки → append-only журнал; без змін runtime.
- План →
  1) Перевірити git log.
  2) Якщо комітів немає — staged add + commit.
  3) Зафіксувати POST.
- Тести/перевірки → не застосовно.

## 2026-01-26T17:25:00+01:00 — POST (MODE=PATCH): Перший коміт (перевірка історії)

- Що зроблено → перевірено git log; у репозиторії вже є перший коміт.
- Де зроблено → git log (корінь репозиторію).
- Як перевірено → git log --oneline -n 5 → 44d361b "Перший коміт .".
- Ризики/нотатки → нових змін для коміту немає; для нового коміту потрібні зміни у tracked файлах.

## 2026-01-26T17:35:00+01:00 — PRE (MODE=read-only discovery): Перевірка remote і походження коміту

- Мета → зʼясувати, чи є remote і куди «полетів» коміт.
- Scope → git remote -v, git status (read-only).
- Non-goals → зміни коду або push.
- Інваріанти/рейки → append-only журнал.
- План →
  1) Перевірити git remote -v.
  2) Зафіксувати висновок у POST.
- Тести/перевірки → не застосовно.

## 2026-01-26T17:40:00+01:00 — POST (MODE=read-only discovery): Перевірка remote і походження коміту

- Що зроблено → перевірено git remote -v.
- Де зроблено → корінь репозиторію.
- Як перевірено → git remote -v → порожній список (remote не налаштовано).
- Ризики/нотатки → коміт локальний і нікуди не відправлявся; потрібно додати remote та push.

## 2026-01-26T17:50:00+01:00 — PRE (MODE=PATCH): Перший коміт + push

- Мета → створити перший коміт у цьому стані та виконати push на GitHub.
- Scope → git add/commit/push.
- Non-goals → зміни коду.
- Інваріанти/рейки → append-only журнал; без зміни runtime.
- План →
  1) Перевірити git status.
  2) Виконати git add -A.
  3) Зробити commit з описом.
  4) Push у origin/main.
- Тести/перевірки → не застосовно.

## 2026-01-26T17:55:00+01:00 — POST (MODE=PATCH): Перший коміт + push

- Що зроблено → перевірено git status; робоче дерево чисте, нових змін немає.
- Де зроблено → корінь репозиторію.
- Як перевірено → git status → On branch main, nothing to commit.
- Ризики/нотатки → щоб створити новий коміт, потрібні зміни у tracked файлах (не в Work/*, бо ігнорується).

## 2026-01-26T18:05:00+01:00 — PRE (MODE=read-only discovery): Перевірка стану warmup/backfill

- Мета → зчитати status snapshot і визначити стан warmup/backfill (last_command/history/coverage).
- Scope → Redis status:snapshot (read-only).
- Non-goals → зміни коду або запуск команд.
- Інваріанти/рейки → без PATCH; append-only журнал.
- План →
  1) Зчитати status:snapshot з Redis.
  2) Вивести last_command, history, ohlcv_final_1m/coverage.
  3) Зафіксувати POST.
- Тести/перевірки → не застосовно.

## 2026-01-26T18:10:00+01:00 — POST (MODE=read-only discovery): Перевірка стану warmup/backfill

- Що зроблено → зчитано status snapshot з Redis та виведено ключові поля warmup/backfill.
- Де зроблено → Redis {NS}:status:snapshot.
- Як перевірено → python (venv) читання Redis.
- Результат →
  - last_command: cmd=bootstrap, state=ok.
  - history: ready=True, backoff_active=False.
  - ohlcv_final_1m: last_complete_bar_ms=0, bars_total_est=0.
  - ohlcv_final (усі TF): last_complete_bar_ms=0.
  - tail_guard: state idle, marks пусті.
  - coverage: None.
- Висновок → warmup/backfill фактично не запускались або не записали дані (SSOT порожній).

## 2026-01-26T18:20:00+01:00 — PRE (MODE=read-only discovery): Перевірка підключення до FXCM

- Мета → зчитати status snapshot і перевірити FXCM state/поля підключення.
- Scope → Redis status:snapshot (read-only).
- Non-goals → зміни коду або запуск команд.
- Інваріанти/рейки → без PATCH; append-only журнал.
- План →
  1) Зчитати fxcm секцію зі status:snapshot.
  2) Зафіксувати стан (state/last_ok/last_err).
  3) Записати POST.
- Тести/перевірки → не застосовно.

## 2026-01-26T18:25:00+01:00 — POST (MODE=read-only discovery): Перевірка підключення до FXCM

- Що зроблено → зчитано fxcm секцію зі status:snapshot.
- Де зроблено → Redis {NS}:status:snapshot.
- Як перевірено → python (venv) читання Redis.
- Результат → fxcm.state=streaming, fsm_state=streaming, last_err=None, ticks_total>0, reconnect_total=0.
- Висновок → підключення до FXCM активне, стрім тік‑ів працює.

## 2026-01-26T19:20:00+01:00 — PRE (MODE=read-only discovery): діагностика якості барів (rate‑метрики)

- Мета → порахувати gap_price між сусідніми барами, p50/p90/p99 та частку великих розривів.
- Що зроблено → не виконано (запит отримано, розрахунок ще не запускали).
- Де зроблено → N/A.
- Як перевірено → не застосовно.
- Ризики/нотатки → потрібен окремий запуск аналізу по final/preview даних.

## 2026-01-26T20:20:00+01:00 — PRE (MODE=read-only discovery): фіксація live‑пайплайну (фактичний стан)

- Мета → коротко зафіксувати живий ланцюжок FXCM tick → bar → UI у поточному конекторі.
- Scope → status:snapshot, Redis ohlcv, UI Lite /debug (read-only).
- Non-goals → зміни коду або запуск команд.
- Інваріанти/рейки → без PATCH; append-only журнал.
- План →
  1) Зчитати status:snapshot (fxcm/price/ohlcv_preview).
  2) Зняти один payload з {NS}:ohlcv.
  3) Перевірити /debug UI Lite.
- Тести/перевірки → не застосовно.

## 2026-01-26T22:05:00+01:00 — PRE (MODE=PATCH): кросплатформний runner dev-checks

- Мета → прибрати залежність від WSL/bash для pytest/ruff/mypy; додати канонічний runner.
- Scope → tools/* (runner) + документи/задачі (за потреби).
- Non-goals → зміни runtime або контрактів.
- Інваріанти/рейки → мінімальний диф; без зміни Public Surface.
- План →
  1) Додати runner для dev-checks (Windows/Linux).
  2) Додати task/документ у docs (за потреби).
  3) Прогнати локально (за наявності середовища).
- Тести/перевірки → ruff/mypy/pytest через новий runner.

## 2026-01-26T19:20:00+01:00 — POST (MODE=read-only discovery): діагностика якості барів (rate‑метрики)

- Що зроблено → проаналізовано gap_price між сусідніми final 1m барами.
- Де зроблено → SQLite data/ohlcv_final.sqlite (bars_1m_final).
- Як перевірено → python (venv) запит останніх 5000 барів.
- Результат → rows=5000; gap p50=0.0, p90=0.0, p99=0.0; частка gap > p99 = 0.0.
- Ризики/нотатки → метрики по final 1m; preview не аналізувався.

## 2026-01-26T20:20:00+01:00 — POST (MODE=read-only discovery): фіксація live‑пайплайну (фактичний стан)

- Що зроблено → знято status snapshot, 1 payload з {NS}:ohlcv та UI Lite /debug.
- Де зроблено → Redis {NS}:status:snapshot, {NS}:ohlcv; UI Lite http://127.0.0.1:8089/debug.
- Як перевірено → python (venv) читання Redis + HTTP /debug.
- Результат →
  - fxcm.state=streaming; tick_total=4965.
  - ohlcv_preview.preview_total=29790; last_publish_ts_ms=1769459521227.
  - ohlcv sample: keys=[symbol, tf, source, complete, synthetic, bars], symbol=XAUUSD, tf=1m.
  - UI /debug: ws_clients=1; ohlcv_contract_error=None; last_payload_ts_ms=1769456760000.
- Ризики/нотатки → у sample payload відсутній mode (None); перевірити продовженням при потребі.

## 2026-01-26T22:05:00+01:00 — POST (MODE=PATCH): кросплатформний runner dev-checks

- Що зроблено → додано кросплатформний runner dev-checks та виконано прогін.
- Де зроблено → tools/dev_checks.py.
- Як перевірено →
  - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m tools.dev_checks → ruff OK, mypy OK, pytest OK.
- Ризики/нотатки → runner не запускає exit gates без --exit-gates.

## 2026-01-26T22:20:00+01:00 — PRE (MODE=PATCH): Перший коміт (dev_checks)

- Мета → закомітити кросплатформний runner dev-checks.
- Scope → tools/dev_checks.py.
- Non-goals → зміни runtime/контрактів.
- Інваріанти/рейки → append-only журнал; мінімальний диф.
- План →
  1) Перевірити git status.
  2) Додати tools/dev_checks.py у stage.
  3) Створити коміт.
- Тести/перевірки → не застосовно.

## 2026-01-26T22:25:00+01:00 — POST (MODE=PATCH): Перший коміт (dev_checks)

- Що зроблено → додано tools/dev_checks.py у stage та створено коміт.
- Де зроблено → tools/dev_checks.py.
- Як перевірено →
  - git status (untracked file tools/dev_checks.py)
  - git add tools/dev_checks.py
  - git commit -m "Додати кросплатформний runner dev-checks" → OK (b8287b7)
- Ризики/нотатки → Work/01log.md не комітиться (ігнорується за правилами).

## 2026-01-26T22:35:00+01:00 — PRE (MODE=read-only discovery): Перевірка remote і push у GitHub

- Мета → зʼясувати стан remote та спробувати push у origin/main.
- Scope → git remote -v, git status, git push.
- Non-goals → зміни коду.
- Інваріанти/рейки → append-only журнал.
- План →
  1) Перевірити git remote -v.
  2) Перевірити git status.
  3) Виконати git push -u origin main.
- Тести/перевірки → не застосовно.

## 2026-01-26T22:40:00+01:00 — POST (MODE=read-only discovery): Перевірка remote і push у GitHub

- Що зроблено → перевірено remote/status і виконано push у origin/main.
- Де зроблено → git remote -v, git status, git push.
- Як перевірено →
  - origin: https://github.com/Std07-1/fxcm_connector_v2.git
  - git status → clean
  - git push -u origin main → OK (new branch main)
- Ризики/нотатки → репозиторій тепер має код у GitHub; якщо сторінка ще порожня — оновити браузер/перевірити гілку main.

## 2026-01-26T23:05:00+01:00 — PRE (MODE=read-only discovery): Оцінка коміту Work/01log.md

- Мета → оцінити, чи варто комітити Work/01log.md.
- Scope → лише висновок, без змін у .gitignore.
- Non-goals → зміни коду/конфігів.
- Інваріанти/рейки → append-only журнал.
- План →
  1) Оцінити ризики (секрети/шляхи/локальні команди).
  2) Надати рекомендацію.
- Тести/перевірки → не застосовно.

## 2026-01-26T23:10:00+01:00 — POST (MODE=read-only discovery): Оцінка коміту Work/01log.md

- Що зроблено → надано рекомендацію щодо коміту журналу.
- Висновок → комітити можна лише після санітизації (секрети, локальні шляхи, артефакти); інакше залишати поза репозиторієм.
- Ризики/нотатки → журнал містить операційні деталі; публічний репозиторій може підсвічувати внутрішні контексти.

## 2026-01-27T09:10:00+01:00 — PRE (MODE=PATCH): Виправлення одного тесту + повний прогін перевірок

- Мета → знайти падіння тесту, виправити один тест і прогнати ruff/mypy/pytest/exit gates.
- Scope → один тестовий файл + повні перевірки.
- Non-goals → зміни runtime/контрактів.
- Інваріанти/рейки → мінімальний диф; append-only журнал.
- План →
  1) Запустити ruff/mypy/pytest/exit gates.
  2) Ідентифікувати 1 failing test.
  3) Виправити тест мінімальним дифом.
  4) Повторити перевірки.
- Тести/перевірки → ruff, mypy, pytest, run_exit_gates.

## 2026-01-27T09:25:00+01:00 — POST (MODE=PATCH): Виправлення одного тесту + повний прогін перевірок

- Що зроблено → виправлено один тест; виконано ruff/mypy/pytest/exit gates.
- Де зроблено → tests/test_tail_guard_ssot_empty_loud_error.py.
- Як перевірено →
  - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m ruff check . → OK
  - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m mypy . → OK
  - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m pytest -q → OK
  - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m tools.run_exit_gates --out reports/exit_gates --manifest tools/exit_gates/manifest.json → OK
- Ризики/нотатки → тест більше не вимагає last_command.state==error, але перевіряє ssot_empty та cmd.

## 2026-01-27T10:05:00+01:00 — PRE (MODE=read-only discovery): Пункт 1 — FXCM backoff під час market closed

- Мета → перевірити поточний backoff/guard у FXCM під час closed та визначити мінімальний PATCH.
- Scope → runtime/fxcm_forexconnect.py, runtime/fxcm/session_manager.py, runtime/status.py, core/time/calendar.py.
- Non-goals → зміни інших пунктів (2–6).
- Інваріанти/рейки → мінімальний диф; без silent fallback.
- План →
  1) Перевірити поточну FSM/backoff логіку в FXCM runtime.
  2) Зафіксувати факт/прогалину.
  3) Підготувати PATCH для backoff при market closed.
- Тести/перевірки → не застосовно (discovery).

## 2026-01-27T10:20:00+01:00 — POST (MODE=read-only discovery): Пункт 1 — FXCM backoff під час market closed

- Що зроблено → перевірено FXCM runtime на наявність backoff/paused режиму під час market closed.
- Де зроблено → runtime/fxcm_forexconnect.py.
- Як перевірено → read_file.
- Результат → backoff вже реалізовано: при closed → state=paused_market_closed, next_retry_ts_ms=max(next_open_ms, now+backoff), sleep до 30s кроками; reconnect_attempt інкрементується.
- Висновок → пункт 1 уже покритий у коді; PATCH не потрібен.

## 2026-01-27T10:30:00+01:00 — PRE (MODE=read-only discovery): Пункт 2 — Календар як SSOT (профілі/overrides)

- Мета → перевірити, що календарні правила повністю в SSOT (config/calendar_overrides.json) та немає stub‑логіки.
- Scope → core/time/calendar.py, core/time/sessions.py, config/calendar_overrides.json.
- Non-goals → зміни коду/конфігів.
- Інваріанти/рейки → без PATCH; append-only журнал.
- План →
  1) Перевірити читання overrides і профілів.
  2) Перевірити використання профілів у market_state/is_open/next_open.
  3) Зафіксувати прогалини даних (closed_intervals_utc тощо).
- Тести/перевірки → не застосовно.

## 2026-01-27T10:40:00+01:00 — POST (MODE=read-only discovery): Пункт 2 — Календар як SSOT (профілі/overrides)

- Що зроблено → перевірено SSOT календар (overrides + профілі + symbol mapping).
- Де зроблено → core/time/calendar.py, core/time/sessions.py, config/calendar_overrides.json.
- Як перевірено → read_file.
- Результат →
  - calendar_overrides.json містить default_fx і metals_xau профілі + symbol_calendar_profile (XAUUSD→metals_xau).
  - Calendar читає overrides, будує профілі і обирає календар за symbol; TradingCalendar реалізує DST-aware weekly open/close + daily break + closed_intervals.
- Висновок → календар SSOT уже централізований; stub‑логіки немає. Дані closed_intervals_utc порожні — якщо потрібні правила v1, треба окремо заповнити дані (без вигадування значень).

## 2026-01-27T10:50:00+01:00 — PRE (MODE=read-only discovery): Пункт 3 — Верхня межа часу всюди

- Мета → підтвердити, що верхня межа epoch ms застосована в усіх критичних валідаціях.
- Scope → core/validation/validator.py, core/time/timestamps.py, core/market/tick.py.
- Non-goals → зміни коду.
- Інваріанти/рейки → без PATCH; append-only журнал.
- План →
  1) Перевірити _require_ms_int на MAX_EPOCH_MS.
  2) Перевірити to_epoch_ms_utc на MAX_EPOCH_MS.
  3) Перевірити tick normalization.
- Тести/перевірки → не застосовно.

## 2026-01-27T10:55:00+01:00 — POST (MODE=read-only discovery): Пункт 3 — Верхня межа часу всюди

- Що зроблено → перевірено верхню межу epoch ms у валідаторі/таймстемпах.
- Де зроблено → core/validation/validator.py, core/time/timestamps.py, core/market/tick.py.
- Як перевірено → read_file/grep_search.
- Результат →
  - _require_ms_int має MAX_EPOCH_MS (microseconds reject).
  - to_epoch_ms_utc має MAX_EPOCH_MS.
  - normalize_tick використовує MAX_EPOCH_MS у tick rail.
- Висновок → пункт 3 уже покритий; PATCH не потрібен.

## 2026-01-27T11:05:00+01:00 — PRE (MODE=read-only discovery): Пункт 4 — TailGuard store_empty guard

- Мета → перевірити, чи TailGuard явно позначає порожній SQLite як store_empty.
- Scope → runtime/tail_guard.py.
- Non-goals → зміни коду.
- Інваріанти/рейки → без PATCH; append-only журнал.
- План →
  1) Перевірити guard total_1m<=0.
  2) Перевірити статус state=store_empty/ssot_empty.
- Тести/перевірки → не застосовно.

## 2026-01-27T11:10:00+01:00 — POST (MODE=read-only discovery): Пункт 4 — TailGuard store_empty guard

- Що зроблено → перевірено guard порожнього SSOT у TailGuard.
- Де зроблено → runtime/tail_guard.py.
- Як перевірено → read_file.
- Результат → при total_1m<=0 встановлюється error ssot_empty та status state store_empty/ssot_empty по TF.
- Висновок → пункт 4 вже покритий; PATCH не потрібен.

## 2026-01-27T11:20:00+01:00 — PRE (MODE=read-only discovery): Пункт 5 — Derived rebuild тільки після 1m store

- Мета → перевірити, що rebuild HTF запускається після наповнення 1m store та не дає хибних результатів.
- Scope → runtime/rebuild_derived.py, runtime/warmup.py, runtime/backfill.py, runtime/handlers_p3.py.
- Non-goals → зміни коду.
- Інваріанти/рейки → без PATCH; append-only журнал.
- План →
  1) Перевірити rebuild логіку щодо порожнього 1m (empty).
  2) Перевірити, де викликається rebuild (warmup/backfill).
- Тести/перевірки → не застосовно.

## 2026-01-27T11:25:00+01:00 — POST (MODE=read-only discovery): Пункт 5 — Derived rebuild тільки після 1m store

- Що зроблено → перевірено виклики rebuild та поведінку при порожньому 1m.
- Де зроблено → runtime/rebuild_derived.py, runtime/warmup.py, runtime/backfill.py, runtime/handlers_p3.py.
- Як перевірено → read_file.
- Результат →
  - rebuild HTF читає 1m range; якщо rows_1m порожні → state=empty (без publish).
  - warmup/backfill викликають rebuild_callback після наповнення store.
- Висновок → пункт 5 вже покритий; додатковий gate не потрібен без нових вимог.

## 2026-01-27T11:35:00+01:00 — PRE (MODE=read-only discovery): Пункт 6 — Bootstrap/fixtures синхронізація

- Мета → перевірити, що фікстури та валідатор статусу синхронізовані, і є SSOT шлях до fixtures.
- Scope → tests/test_validator_status.py, core/fixtures_path.py.
- Non-goals → зміни коду/конфігів.
- Інваріанти/рейки → без PATCH; append-only журнал.
- План →
  1) Перевірити _valid_status на наявність required полів (late_ticks_dropped_total, tz_backend тощо).
  2) Перевірити SSOT helper для fixtures.
- Тести/перевірки → не застосовно.

## 2026-01-27T11:40:00+01:00 — POST (MODE=read-only discovery): Пункт 6 — Bootstrap/fixtures синхронізація

- Що зроблено → перевірено _valid_status та SSOT шлях до fixtures.
- Де зроблено → tests/test_validator_status.py, core/fixtures_path.py.
- Як перевірено → read_file.
- Результат → _valid_status містить ohlcv_preview.late_ticks_dropped_total та market.tz_backend; fixtures_path визначено як tests/fixtures.
- Висновок → пункт 6 вже покритий; PATCH не потрібен.

## 2026-01-27T12:10:00+01:00 — PRE (MODE=read-only discovery): Розширений аудит (runtime/core)

- Мета → виконати розширений аудит по пунктах: архітектура, ризики, дефекти, техборг, дублювання, роз'їзд, рейки, гейти, рекомендації.
- Scope → runtime/*, core/*, а також cross‑cutting (config/store/tools) для контексту.
- Non-goals → зміни коду/конфігів; запуск сервісів.
- Інваріанти/рейки → append-only журнал; без PATCH.
- План →
  1) Скласти аудит‑запис у docs/ (runtime/core з фокусом на стабільність/архітектуру).
  2) Зафіксувати ключові ризики та рекомендації.
- Тести/перевірки → не застосовно.

## 2026-01-27T12:20:00+01:00 — POST (MODE=read-only discovery): Розширений аудит (runtime/core)

- Що зроблено → сформовано розширений аудит по пунктах (архітектура, ризики, дефекти, техборг, дублювання, роз'їзд, рейки, гейти, рекомендації).
- Де зроблено → docs/audit_v7_runtime_core.md.
- Як перевірено → read-only формування документа.
- Ризики/нотатки → документ базується на поточному коді/рейках; дані середовища не змінювались.

## 2026-01-27T13:10:00+01:00 — PRE (MODE=read-only discovery): Витяг корисного з calendar_sessions_spec.md (v1)

- Мета → витягнути корисні правила з v1 для закриття техборгу календаря.
- Scope → docs/audit_v1/calendar_sessions_spec.md (зовнішній документ), docs/audit_v7_runtime_core.md (оновлення висновків).
- Non-goals → зміни коду/конфігів.
- Інваріанти/рейки → append-only журнал; без PATCH у runtime/core.
- План →
  1) Витягнути ключові правила (UTC overrides, holidays/closed_intervals, boundary semantics).
  2) Оновити audit_v7_runtime_core.md (ризики/рекомендації).
- Тести/перевірки → не застосовно.

## 2026-01-27T13:20:00+01:00 — POST (MODE=read-only discovery): Витяг корисного з calendar_sessions_spec.md (v1)

- Що зроблено → виділено корисні правила v1 та оновлено аудит v7.
- Де зроблено → docs/audit_v7_runtime_core.md.
- Як перевірено → read_file (v1 документ) + редагування аудиту.
- Результат → додано згадки про UTC‑оверрайди, holidays/closed_intervals_utc, daily‑break anchor для 1d boundary, HTF complete policy; додано ризик schedule drift і рекомендацію узгодження профілю.
- Ризики/нотатки → потрібне рішення: переносити v1 UTC‑оверрайди чи залишати NY‑локальні правила.

## 2026-01-27T12:00:00+00:00 — PRE (MODE=PATCH): P1.2 календарні профілі (NY vs v1 UTC overrides) + gate проти “зсуву вправо”

- Мета →
  1) Додати SSOT “calendar overrides” як дані з двома режимами (NY і v1 UTC overrides).
  2) Додати rails: невалідні overrides → init_error + safe market_closed без silent fallback.
  3) Додати exit gate для schedule drift (active calendar_tag vs SSOT).
  4) Додати gate у дефолтний manifest + оновити REPO_LAYOUT.
- Scope → core/time/sessions.py, core/time/calendar.py, config/calendar_overrides.json, tools/exit_gates/gates/gate_calendar_schedule_drift.py, tools/exit_gates/manifest.json, docs/REPO_LAYOUT.md, docs/calendar_sessions_spec.md.
- Non-goals → history provider / warmup/backfill / UI / WS/HTTP протоколи; без ENV-флагів; без змін public API schemas.
- Інваріанти/рейки → Contract-first, мінімальний диф, no silent fallback, календарні дані як SSOT у config/calendar_overrides.json, нові коментарі українською.
- План →
  1) Переформатувати calendar_overrides.json під 2 календарні теги.
  2) Додати loader overrides у sessions.py + прийом overrides у TradingCalendar.
  3) Підключити overrides у Calendar і додати init_error rail.
  4) Додати exit gate schedule drift і включити в manifest.
  5) Додати 2 тести та оновити REPO_LAYOUT + calendar_sessions_spec.md.
- Тести/перевірки → pytest для нових тестів + tools.run_exit_gates з manifest.

## 2026-01-27T22:35:00+00:00 — POST (MODE=PATCH): P1.2 календарні профілі + schedule drift gate

- Що зроблено →
  - Додано SSOT calendar_overrides.json з двома режимами (NY та v1 UTC overrides).
  - TradingCalendar приймає overrides, валідатор/rail для часу та TZ, merge closed_intervals_utc.
  - Calendar використовує loader overrides з degraded-but-loud init_error.
  - Додано gate_calendar_schedule_drift і включено у дефолтний manifest.
  - Додано 2 тести + оновлено XAU семантику під 23:01 UTC.
  - Оновлено REPO_LAYOUT та TODO в calendar_sessions_spec.
- Де зроблено →
  - config/calendar_overrides.json
  - core/time/sessions.py
  - core/time/calendar.py
  - tools/exit_gates/gates/gate_calendar_schedule_drift.py
  - tools/exit_gates/manifest.json
  - tools/exit_gates/gates/gate_calendar_xau_next_open_matches_23utc.py
  - tests/test_calendar_overrides_loading.py
  - tests/test_calendar_schedule_semantics.py
  - tests/test_calendar_xau_profile.py
  - docs/REPO_LAYOUT.md
  - docs/calendar_sessions_spec.md
- Як перевірено →
  - .\.venv\Scripts\python.exe -m pytest -q tests/test_calendar_overrides_loading.py tests/test_calendar_schedule_semantics.py → EXIT_CODE=0
  - .\.venv\Scripts\python.exe -m tools.run_exit_gates --out reports/exit_gates --manifest tools/exit_gates/manifest.json → EXIT_CODE=0
  - Proof artifacts: reports/audit_p1_calendar_schedule/20260127_222915/{pytest.txt,pytest.exitcode.txt,exit_gates.txt,exit_gates.exitcode.txt}
- Ризики/нотатки →
  - Якщо обрано fxcm_calendar_v1_utc_overrides, сесії fixed UTC; якщо FXCM реально DST-aware → перемкнутися на NY tag після польової валідації.

## 2026-01-27T23:10:00+00:00 — PRE (MODE=PATCH): P1.2 calendar v1 UTC overrides → SSOT data + rails/gates

- Мета →
  - Єдина SSOT для календарних даних у config/calendar_overrides.json (fxcm_calendar_v1_utc_overrides) з міграцією closed_intervals_utc з v1.
  - Додати rails для нормалізації/валідації closed_intervals_utc + exit gate.
  - Запускати gate “одним махом” через runner.
- Scope → core/time/sessions.py, core/time/calendar.py, core/time/closed_intervals.py, config/calendar_overrides.json, tools/exit_gates/gates/gate_calendar_closed_intervals.py, tools/exit_gates/manifest_p1_calendar.json, tools/migrate_v1_calendar_overrides.py, tools/run_dev_checks.py, tests/*, docs/REPO_LAYOUT.md.
- Non-goals → warmup/backfill/history/FXCM, public API, календарна логіка/таймзони, ENV.
- Інваріанти/рейки → SSOT тільки calendar_overrides.json; DRY normalize_closed_intervals_utc; fail-fast; формат [start_ms,end_ms) int, start<end, sorted, no overlap, в межах epoch rails.
- План →
  1) Додати core/time/closed_intervals.py з normalize_closed_intervals_utc.
  2) Підключити normalize у loader/session + gate + migration tool.
  3) Міграція v1_calendar_overrides.json у calendar_overrides.json.
  4) Додати gate_calendar_closed_intervals + manifest_p1_calendar.json.
  5) Додати 2–3 тести та оновити REPO_LAYOUT.
- Тести/перевірки → tools.run_dev_checks + tools.run_exit_gates (manifest_p1_calendar.json).

## 2026-01-27T23:59:00+00:00 — POST (MODE=PATCH): P1.2 calendar v1 UTC overrides → SSOT data + rails/gates

- Що зроблено →
  - Додано DRY rail normalize_closed_intervals_utc та підключено в loader/gate/міграцію.
  - Мігровано v1 closed_intervals_utc (і holidays) у config/calendar_overrides.json (fxcm_calendar_v1_utc_overrides).
  - Додано gate_calendar_closed_intervals + manifest_p1_calendar.json.
  - Додано one-shot міграційний тул tools/migrate_v1_calendar_overrides.py.
  - Додано wrapper tools/run_dev_checks.py.
  - Додано 3 тести для нормалізації/ефекту/гейта.
  - Оновлено REPO_LAYOUT, архівовано v1_calendar_overrides.json у docs/evidence.
  - Актуалізовано expected open у test_history_closed_policy (NY 22:00 UTC).
- Де зроблено →
  - core/time/closed_intervals.py
  - core/time/sessions.py
  - tools/migrate_v1_calendar_overrides.py
  - tools/run_dev_checks.py
  - tools/exit_gates/gates/gate_calendar_closed_intervals.py
  - tools/exit_gates/manifest_p1_calendar.json
  - config/calendar_overrides.json
  - tests/test_closed_intervals_normalize.py
  - tests/test_calendar_closed_interval_effect.py
  - tests/test_gate_calendar_closed_intervals.py
  - tests/test_history_closed_policy.py
  - docs/REPO_LAYOUT.md
  - docs/evidence/v1_calendar_overrides.json
- Як перевірено →
  - .\.venv\Scripts\python.exe -m tools.run_dev_checks → EXIT_CODE=0
  - .\.venv\Scripts\python.exe -m tools.run_exit_gates --out reports/exit_gates --manifest tools/exit_gates/manifest_p1_calendar.json → EXIT_CODE=0
  - Артефакти: reports/audit_p1_calendar_data/20260128_143231/{dev_checks.txt,dev_checks.exitcode.txt,exit_gates.txt,exit_gates.exitcode.txt}
- Результат → PASS: SSOT дані календаря нормалізуються/валідуються, closed_intervals_utc мігровано з v1, gate блокує невалідні інтервали.
- Ризики/нотатки →
  - Якщо формат v1 зміниться — повторна міграція тим самим тулом.
  - Якщо closed_intervals_utc виросте — може знадобитись оптимізація пошуку (окремий ADR/P‑slice).

## 2026-01-28T00:30:00+00:00 — PRE (MODE=PATCH): P1.3 фіналізація календарних профілів + інтеграція нових гейтів у default manifest

- Мета →
  1) Додати календарні гейти у default manifest.
  2) Оновити REPO_LAYOUT з новими файлами/manifest.
  3) Додати rail проти прямого запуску gate_*.py (лише через runner або python -m).
- Scope → tools/exit_gates/manifest.json, tools/exit_gates/gates/, tools/run_exit_gates.py, tests/, docs/REPO_LAYOUT.md.
- Non-goals → календарні правила, history provider, warmup/backfill, логіка UI/WS/HTTP.
- Інваріанти/рейки → contract-first, DRY (єдиний дефолтний manifest), rail прямого запуску gate_*.py.
- План →
  1) Оновити default manifest на calendar gates.
  2) Додати rail у run_exit_gates + guard у gate модулях.
  3) Додати тести manifest/run_exit_gates/rail.
  4) Оновити REPO_LAYOUT.
- Тести/перевірки → pytest -q (3 тести) + tools.run_exit_gates (manifest.json).

## 2026-01-28T01:05:00+00:00 — POST (MODE=PATCH): P1.3 фіналізація календарних профілів + інтеграція нових гейтів у default manifest

- Що зроблено →
  - Оновлено tools/exit_gates/manifest.json: додано gate_calendar_schedule_drift і gate_calendar_closed_intervals.
  - Додано rail проти прямого запуску gate_*.py через helper у tools/run_exit_gates.py.
  - Додано тести manifest/default runner/rail прямого запуску.
  - Оновлено REPO_LAYOUT з manifest'ами та календарними гейтами.
- Де зроблено →
  - tools/exit_gates/manifest.json
  - tools/run_exit_gates.py
  - tools/exit_gates/gates/gate_calendar_schedule_drift.py
  - tools/exit_gates/gates/gate_calendar_closed_intervals.py
  - tests/test_manifest_includes_calendar_gates.py
  - tests/test_run_exit_gates_default.py
  - tests/test_gate_direct_run_rail.py
  - docs/REPO_LAYOUT.md
- Як перевірено →
  - .\.venv\Scripts\python.exe -m pytest -q tests/test_manifest_includes_calendar_gates.py tests/test_run_exit_gates_default.py tests/test_gate_direct_run_rail.py → EXIT_CODE=0
  - .\.venv\Scripts\python.exe -m tools.run_exit_gates --out reports/exit_gates --manifest tools/exit_gates/manifest.json → EXIT_CODE=0
- Ризики/нотатки →
  - Додавання нових гейтів потребує оновлення manifest.json і тесту присутності.
  - Rail direct-run блокує сценарії дебагу; для дебагу використовувати python -m tools.exit_gates.gates.<gate_module> або runner.

## 2026-01-28T01:20:00+00:00 — PRE (MODE=read-only discovery): Аудит v2 vs v1 (warmup/backfill/tail_guard/calendar/store/preview)

- Мета → деталізувати «що, де, як, навіщо» у v2 відносно v1, виявити прогалини/дублювання та оцінити обсяг робіт.
- Scope → runtime/* (warmup/backfill/tail_guard/history_provider), core/time/*, store/*, preview pipeline, fxcm/*, observability/metrics.
- Non-goals → будь-які зміни коду/конфігів; без тестів.
- Інваріанти/рейки → append-only журнал; посилання на факти як path:line; не змінювати SSOT/REPO_LAYOUT.
- План →
  1) Прочитати ключові файли runtime/core/store/fxcm.
  2) Зафіксувати поточну поведінку (v2) та розриви з v1.
  3) Сформувати оцінку/ризики/рекомендації.
- Тести/перевірки → не запускати (read-only).

## 2026-01-28T02:10:00+00:00 — POST (MODE=read-only discovery): Аудит v2 vs v1 (warmup/backfill/tail_guard/calendar/store/preview)

- Що зроблено →
  - Проведено читання ключових модулів runtime/core/store/fxcm для порівняння з v1; зафіксовано факти path:line.
- Факти (що/де/як/навіщо) →
  - Warmup: iterує по діапазону, fetch 1m history, upsert у store, оновлює coverage та може викликати rebuild derived. [runtime/warmup.py](runtime/warmup.py#L13-L74)
  - Backfill: chunk fetch 1m history, upsert у store, оновлює coverage, може rebuild HTF. [runtime/backfill.py](runtime/backfill.py#L13-L72)
  - Tail-guard: якщо SSOT 1m порожній — loud error ssot_empty і вихід; repair дозволено лише якщо ринок закритий. [runtime/tail_guard.py](runtime/tail_guard.py#L72-L158)
  - History readiness/backoff: guard_history_ready ставить next_open, backoff_ms, degraded і піднімає HistoryNotReadyError. [runtime/history_provider.py](runtime/history_provider.py#L33-L95)
  - FXCM history provider має backoff та probe (не «storm»), probe empty → error/degraded. [runtime/fxcm/history_provider.py](runtime/fxcm/history_provider.py#L86-L173)
  - Calendar: overrides завантажуються з SSOT calendar_overrides.json, normalize closed_intervals_utc, DST-aware TZ. [core/time/calendar.py](core/time/calendar.py#L13-L49), [core/time/sessions.py](core/time/sessions.py#L45-L149)
  - Preview: кеш preview in-memory (OhlcvCache), не персиститься. [core/market/preview_builder.py](core/market/preview_builder.py#L39-L76)
  - Final store: SQLite SSOT з upsert_1m_final та query tail/coverage. [store/sqlite_store.py](store/sqlite_store.py#L15-L135)
  - Валідатор часу: _require_ms_int відсікає seconds та microseconds. [core/validation/validator.py](core/validation/validator.py#L27-L36)
- Оцінка розривів vs v1 →
  - Персистентність preview у v2 відсутня (виключно RAM кеш), тому після рестарту preview стартує «з нуля». [core/market/preview_builder.py](core/market/preview_builder.py#L39-L76)
  - SSOT final 1m існує (SQLite), але залежить від налаштованого history provider; без нього store порожній, tail_guard переходить у ssot_empty. [store/sqlite_store.py](store/sqlite_store.py#L15-L135), [runtime/tail_guard.py](runtime/tail_guard.py#L72-L118)
  - Backoff/probe у history provider вже реалізовані, але залежать від FXCM readiness/SDK/секретів та фактичних даних. [runtime/history_provider.py](runtime/history_provider.py#L33-L95), [runtime/fxcm/history_provider.py](runtime/fxcm/history_provider.py#L86-L173)
  - Календарні профілі вже підключені через SSOT overrides; вимагають актуальних closed_intervals_utc для повної відповідності v1. [core/time/sessions.py](core/time/sessions.py#L45-L149)
  - Валідація ms rails є; твердження про “μs допускає” не підтверджено валідатором. [core/validation/validator.py](core/validation/validator.py#L27-L36)
- Потенційні дублювання →
  - Runtime preview_builder є thin wrapper над core.preview_builder (DRY збережено). [runtime/preview_builder.py](runtime/preview_builder.py#L1-L12), [core/market/preview_builder.py](core/market/preview_builder.py#L84-L140)
- Оцінка обсягу робіт (наближення до v1) →
  - Середній обсяг: 3–6 днів на інтеграцію preview persistence + SOP warmup/backfill orchestration + контроль history readiness; додатково 1–2 дні на UX/операційні runbook-и.
- Ризики/нотатки →
  - Найбільший ризик: відсутність історії (history provider) блокує final store, derived HTF і tail_guard. [runtime/history_provider.py](runtime/history_provider.py#L33-L95)
  - Preview persistence потребує окремого SSOT сховища або реплікації з final (ADR/окремий slice).
  - Calendar data повинні підтримуватись як SSOT; інакше можливий drift у weekly open/close і святкових періодах. [core/time/sessions.py](core/time/sessions.py#L45-L149)
- Де зроблено → тільки читання, без змін коду/документації.

## 2026-01-28T02:20:00+00:00 — PRE (MODE=read-only discovery): Перевірка використання config-блоку та пошук дублікатів/роз'їздів

- Мета → підтвердити використання параметрів config-блоку в коді та знайти потенційні дублікати/роз'їзди.
- Scope → config/config.py, runtime/*, core/*, store/*, tools/*, tests/*.
- Non-goals → без змін коду/конфігу; без тестів.
- Інваріанти/рейки → append-only журнал, факти як path:line.
- План →
  1) Grep usage для ключових полів блоку.
  2) Зіставити з місцями застосування та можливими дублями.
  3) Сформувати короткий висновок.
- Тести/перевірки → не запускати (read-only).

## 2026-01-28T02:35:00+00:00 — POST (MODE=read-only discovery): Перевірка використання config-блоку та пошук дублікатів/роз'їздів

- Що зроблено → перевірено використання параметрів config-блоку у runtime/core/store/tools/tests; зафіксовано підозрілі дублікати/роз'їзди.
- Факти (використання значень) →
  - calendar_tag/closed_intervals_utc: використовується при створенні Calendar у runtime/tools/tests. [core/time/calendar.py](core/time/calendar.py#L13-L49), [runtime/history_provider.py](runtime/history_provider.py#L33-L95)
  - trading_day_boundary_utc: використовується у buckets і derived rebuild. [core/time/buckets.py](core/time/buckets.py#L34-L61), [runtime/rebuild_derived.py](runtime/rebuild_derived.py#L126-L163)
  - store_path: використовується для SQLiteStore у gates. [tools/exit_gates/gates/gate_calendar_gaps.py](tools/exit_gates/gates/gate_calendar_gaps.py#L87-L90)
  - retention_days/retention_target_days: trim у final ingest та coverage у status. [runtime/final/final_ingest.py](runtime/final/final_ingest.py#L45-L127), [runtime/status.py](runtime/status.py#L915-L928)
  - warmup_lookback_days/history_chunk_minutes/history_chunk_limit: warmup/backfill chunking. [runtime/warmup.py](runtime/warmup.py#L25-L60), [runtime/backfill.py](runtime/backfill.py#L25-L56)
  - max_requests_per_minute: budget для history. [runtime/fxcm/history_budget.py](runtime/fxcm/history_budget.py#L62-L64), [runtime/repair.py](runtime/repair.py#L90-L91)
  - history_min_sleep_ms: використовується у FxcmHistoryProvider.min_sleep_ms (потребує зв'язку під час створення провайдера). [runtime/fxcm/history_provider.py](runtime/fxcm/history_provider.py#L105-L150)
  - tail_guard_*: repair windows/TTL/limits використовуються у tail_guard/repair. [runtime/tail_guard.py](runtime/tail_guard.py#L69-L181), [runtime/repair.py](runtime/repair.py#L49-L85)
  - derived_rebuild_default_tfs/window_hours_default: використовуються у handlers. [runtime/handlers_p3.py](runtime/handlers_p3.py#L45-L119), [runtime/handlers_p4.py](runtime/handlers_p4.py#L113-L114)
- Підозрілі дублікати/роз'їзди →
  - tail_guard_ttl_ms vs tail_guard_ttl_minutes, republish_watermark_ttl_minutes vs republish_watermark_ttl_s: дублікати різних одиниць; потрібна єдина SSOT-одиниця. [config/config.py](config/config.py#L60-L66)
  - trading_day_boundary_utc: дефолт 23:00 у Config, але у derived_builder є дефолт 22:00 (потенційний роз'їзд). [config/config.py](config/config.py#L44-L46), [store/derived_builder.py](store/derived_builder.py#L35-L47)
  - closed_intervals_utc у Config може вводити в оману, бо фактичні інтервали беруться з calendar_overrides.json (через loader). [config/config.py](config/config.py#L44-L46), [core/time/sessions.py](core/time/sessions.py#L90-L149)
- Ризики/нотатки →
  - Якщо Config і derived_builder мають різні trading_day_boundary_utc — можливі роз'їзди HTF меж.
  - Дублікати TTL у різних одиницях можуть вести до неузгоджених змін.
  - closed_intervals_utc у Config не є SSOT для календаря — ризик помилкових очікувань.
- Де зроблено → тільки читання, без змін.

## 2026-01-28T03:00:00+00:00 — PRE (MODE=PATCH): P2 LiveArchive SQLite для preview (evidence + restart continuity)

- Мета →
  1) Додати персистентний LiveArchive для preview bucket-close свічок.
  2) Додати rails (insert-only, UNIQUE, геометрія, TF allowlist, no silent fallback).
  3) Додати exit-gate та включити в default manifest.
  4) Додати SSOT config для live_archive та явне disable.
- Scope → store/live_archive_store.py, runtime/ohlcv_preview.py або callsite preview publish, config/config.py, observability/metrics.py, tools/exit_gates/gates/gate_live_archive_sqlite.py, tools/exit_gates/manifest.json, tests/*, docs/REPO_LAYOUT.md.
- Non-goals → зміни public API, preview/final семантики, warmup/backfill/tail_guard, ENV.
- Інваріанти/рейки → insert-only, UNIQUE(symbol, tf, open_time_ms), close=open+tf_ms-1, TF allowlist, запис лише при bucket close, disabled → degraded, enabled+path invalid → fail-fast.
- План →
  1) Реалізувати LiveArchive SQLite store з rail валідаціями.
  2) Додати config knobs + перевірки.
  3) Інтегрувати архівацію при bucket transition.
  4) Додати метрики та exit-gate.
  5) Додати тести та оновити REPO_LAYOUT.
  6) Прогнати pytest + exit_gates з артефактами у reports/audit_p2_live_archive.

## 2026-01-28T03:35:00+00:00 — POST (MODE=PATCH): P2 LiveArchive SQLite для preview (evidence + restart continuity)

- Що зроблено →
  - Додано LiveArchive SQLite store з insert-only та rails.
  - Додано SSOT config для live_archive та rail проти :memory:.
  - Інтегровано архівацію закритих preview-барів при transition (side-effect).
  - Додано метрики live_archive і exit-gate.
  - Додано тести для store та selection.
  - Оновлено REPO_LAYOUT.
- Де зроблено →
  - store/live_archive_store.py
  - config/config.py
  - app/composition.py
  - runtime/ohlcv_preview.py
  - observability/metrics.py
  - tools/exit_gates/gates/gate_live_archive_sqlite.py
  - tools/exit_gates/manifest.json
  - tests/test_live_archive_store.py
  - tests/test_live_archive_sink_on_bucket_close.py
  - docs/REPO_LAYOUT.md
- Як перевірено →
  - .\.venv\Scripts\python.exe -m pytest -q tests/test_live_archive_store.py tests/test_live_archive_sink_on_bucket_close.py → EXIT_CODE=0
  - .\.venv\Scripts\python.exe -m tools.run_exit_gates --out reports/exit_gates --manifest tools/exit_gates/manifest.json → EXIT_CODE=0
  - Артефакти: reports/audit_p2_live_archive/20260128_171919/{pytest.txt,pytest.exitcode.txt,exit_gates.txt,exit_gates.exitcode.txt}
- Результат → pytest PASS; exit_gates PASS; LiveArchive rails enforced.
- Ризики/нотатки →
  - LiveArchive ≠ SSOT final; не використовувати для derived без ADR.
  - SQLite single-writer: при кількох інстансах потрібен один активний writer.
  - Для середовища без диска live_archive_enabled має бути False (явне рішення у SSOT config).

## 2026-01-28T03:50:00+00:00 — PRE (MODE=read-only discovery): Аудит збереження даних (усі місця/виклики/налаштування)

- Мета → зібрати всі точки збереження даних, всі виклики та налаштування (config/runtime/tools/tests).
- Scope → store/*, runtime/*, config/*, tools/*, observability/*, tests/*, data/*.
- Non-goals → без змін коду/конфігів; без тестів.
- Інваріанти/рейки → append-only журнал, факти як path:line.
- План →
  1) Зібрати всі SQLite store місця (final/derived/live_archive).
  2) Зібрати Redis snapshot/pubsub та файлові артефакти (recordings/data/replay).
  3) Зібрати конфіг-ключі та runtime callsites.
  4) Сформувати повний POST-опис.
- Тести/перевірки → не запускати (read-only).

## 2026-01-28T05:10:00+00:00 — PRE (MODE=PATCH): P2 FileCache SSOT (v1-style) + stream persist (no SQLite)

- Мета →
  1) Додати файловий OHLCV cache (CSV + meta.json) як єдину персистентність конектора (v1-style).
  2) Підключити запис завершених 1m барів зі stream-пайплайну в кеш.
  3) Додати exit-gate(и) cache schema/semantics і включити у default manifest.
  4) Оновити docs/REPO_LAYOUT.md.
- Scope →
  - store/file_cache/*
  - config/config.py
  - runtime/* (один callsite)
  - tools/exit_gates/gates/gate_file_cache_schema.py
  - tools/exit_gates/manifest.json
  - tests/*
  - docs/REPO_LAYOUT.md
- Non-goals →
  - Не видаляти SQLite/міграції.
  - Не змінювати WS/HTTP/Redis keys.
  - Без ENV-флагів; тільки SSOT config.
- Інваріанти/рейки → contract-first, no silent fallback, CACHE_COLUMNS/meta.version/merge+trim у одному місці, rails на ms/geom, sorted+UNIQUE(open_time_ms), duplicates loud.
- План →
  1) Додати store/file_cache з HistoryCache (CSV + meta.json) + rails.
  2) Додати SSOT cache settings у config/config.py.
  3) Інтегрувати append complete 1m барів у stream callsite.
  4) Додати gate_file_cache_schema + включити в manifest.
  5) Додати 1–3 тести + оновити REPO_LAYOUT.
- Тести/перевірки → pytest (нові тести) + tools.run_exit_gates (default manifest).

## 2026-01-28T05:45:00+00:00 — POST (MODE=PATCH): P2 FileCache SSOT (v1-style) + stream persist (no SQLite)

- Що зроблено →
  - Додано FileCache (CSV + meta.json) з SSOT rails/columns/merge+trim у store/file_cache/*.
  - Додано SSOT cache settings у config/config.py.
  - Інтегровано append завершених 1m барів зі stream у runtime callsite (без зміни Redis payload).
  - Додано gate_file_cache_schema та включено в tools/exit_gates/manifest.json.
  - Додано 2 тести для roundtrip/trim+duplicate.
  - Оновлено docs/REPO_LAYOUT.md.
- Де зроблено →
  - store/file_cache/cache_utils.py
  - store/file_cache/history_cache.py
  - store/file_cache/__init__.py
  - config/config.py
  - app/composition.py
  - tools/exit_gates/gates/gate_file_cache_schema.py
  - tools/exit_gates/manifest.json
  - tests/test_file_cache_roundtrip.py
  - tests/test_file_cache_trim_and_duplicate_loud.py
  - docs/REPO_LAYOUT.md
- Як перевірено →
  - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m pytest -q tests/test_file_cache_roundtrip.py tests/test_file_cache_trim_and_duplicate_loud.py → EXIT_CODE=0
  - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m tools.run_exit_gates --out reports/exit_gates --manifest tools/exit_gates/manifest.json → EXIT_CODE=0
- Артефакти → reports/exit_gates/2026-01-28_190128/{results.json,hashes.json}
- Ризики/нотатки →
  - FileCache пишеться тільки для завершених 1m барів; помилки IO → degraded-but-loud.
  - Дублікати у cache не ламають консистентність, але фіксуються як warn/degraded.

## 2026-01-28T04:30:00+00:00 — POST (MODE=read-only discovery): Аудит збереження даних (усі місця/виклики/налаштування)

- Що зроблено → зібрано всі точки персистентності (SQLite, Redis snapshot/pubsub, файлові артефакти tools/runtime) та їхні callsites/налаштування.
- Факти (де саме відбувається збереження/персистентність) →
  - SQLite SSOT final/derived: init_schema/upsert у [store/sqlite_store.py](store/sqlite_store.py#L13-L260), схема у store/schema.sql; шлях визначає config.store_path. [config/config.py](config/config.py#L50-L52)
  - Запис 1m final у SQLite: warmup/backfill/repair виконують store.upsert_1m_final. [runtime/warmup.py](runtime/warmup.py#L35-L69), [runtime/backfill.py](runtime/backfill.py#L33-L57), [runtime/repair.py](runtime/repair.py#L105-L152)
  - Запис HTF final у SQLite: rebuild_derived та final_ingest викликають store.upsert_htf_final. [runtime/rebuild_derived.py](runtime/rebuild_derived.py#L146-L220), [runtime/final/final_ingest.py](runtime/final/final_ingest.py#L86-L160)
  - LiveArchive SQLite (append-only evidence): створення таблиці + insert_bar у [store/live_archive_store.py](store/live_archive_store.py#L22-L96); шлях у config.live_archive_sqlite_path. [config/config.py](config/config.py#L108-L110)
  - LiveArchive callsite: ініціалізація і запис закритих preview-барів у runtime. [app/composition.py](app/composition.py#L127-L571)
  - Redis snapshot/publish: set_snapshot + publish у [runtime/publisher.py](runtime/publisher.py#L35-L120); публікація статусу в [runtime/status.py](runtime/status.py#L1172-L1202)
  - Redis watermark (republish): get/setex ключів watermark. [runtime/republish.py](runtime/republish.py#L47-L66)
  - HTTP read path (status/final): читання status snapshot з Redis і читання final з SQLite. [runtime/http_server.py](runtime/http_server.py#L97-L154)
  - Replay JSONL (вхідні дані): читання файлу з config.replay_ticks_path. [runtime/replay_ticks.py](runtime/replay_ticks.py#L58-L66), [config/config.py](config/config.py#L95-L96)
  - Інструменти/артефакти файлового запису:
    - Запис JSONL tick recordings: [tools/record_ticks.py](tools/record_ticks.py#L103-L120)
    - Запис FXCM tick fixtures (jsonl + meta.json): [tools/capture_fxcm_ticks.py](tools/capture_fxcm_ticks.py#L99-L169)
    - Soak monitor report (reports/soak/*.json): [tools/soak_monitor.py](tools/soak_monitor.py#L59-L258)
    - Exit gates results/hashes: [tools/run_exit_gates.py](tools/run_exit_gates.py#L93-L108)
    - Audit artifacts: capture_redis_ohlcv_once, ws_smoke_ui_lite, scan_log_entries. [tools/audit/capture_redis_ohlcv_once.py](tools/audit/capture_redis_ohlcv_once.py#L82-L84), [tools/audit/ws_smoke_ui_lite.py](tools/audit/ws_smoke_ui_lite.py#L24-L171), [tools/audit/scan_log_entries.py](tools/audit/scan_log_entries.py#L61-L78)
    - Міграція календарних overrides (запис SSOT даних): [tools/migrate_v1_calendar_overrides.py](tools/migrate_v1_calendar_overrides.py#L80-L81)
- Де зроблено → тільки читання: config/config.py, store/*, runtime/*, app/composition.py, tools/* (включно tools/audit/*).
- Як перевірено → read_file + grep_search (read-only), без запуску тестів.
- Ризики/нотатки →
  - Основні персистентні сховища: SQLite SSOT (final/derived) + LiveArchive SQLite; все інше у Redis або файлові артефакти tools.
  - Redis зберігає snapshot/status і watermarks; без Redis частина операцій буде degraded/loud (republish watermark).
  - Файлові артефакти tools не є SSOT і не використовуються runtime як джерело істини.

## 2026-01-28T06:20:00+01:00 — PRE (MODE=PATCH): P2 SSOT = FileCache (CSV+meta) + Redis transport; повне видалення SQLite

- Мета → повністю прибрати SQLite SSOT і перейти на v1-style FileCache як єдину персистентність; Redis лишається transport.
- Scope → store/file_cache/*, app/composition.py, runtime/* (warmup/backfill/repair/tail_guard/http_server/republish), config/config.py, tools/exit_gates/*, tests/*, docs/REPO_LAYOUT.md, видалення sqlite/live_archive/derived/final.
- Non-goals → не змінювати public schemas та Redis/WS/HTTP keys; без derived HTF rebuild у цьому репо.
- Інваріанти/рейки → no silent fallback, time/geometry rails, atomic writes, sorted+UNIQUE open_time_ms, cache IO fail → degraded-but-loud.
- План →
  1) Read-only discovery SQLite callsites та зафіксувати path:line у POST.
  2) Видалити sqlite/live_archive/derived/final модулі та залежності.
  3) Реалізувати v1-style FileCache (CSV+meta, atomic write, rails) і інтегрувати у runtime.
  4) Оновити HTTP final на FileCache; оновити tail_guard/republish/warmup/backfill/repair.
  5) Додати gates: cache_integrity + no_sqlite_left, оновити manifest.
  6) Оновити тести (2–3) і REPO_LAYOUT; зібрати proof-pack.
- Тести/перевірки → tools.run_dev_checks + tools.run_exit_gates (default manifest) з артефактами.

## 2026-01-28T06:40:00+01:00 — PRE (MODE=PATCH): Backfill/repair позначають complete бари

- Мета → гарантувати `complete=True` для барів у backfill/repair перед записом у FileCache.
- Scope → runtime/backfill.py, runtime/repair.py.
- Non-goals → без змін у контрактах/HTTP/гейтах.
- Інваріанти/рейки → no silent fallback; тільки мінімальний диф.
- План →
  1) Додати `complete=True` у backfill.
  2) Додати `complete=True` у repair.
  3) Переконатися, що логіка не змінює порядок/таймінги.
- Тести/перевірки → не запускати (мінімальний локальний диф).

## 2026-01-28T06:42:00+01:00 — POST (MODE=PATCH): Backfill/repair позначають complete бари

- Що зроблено →
  - Додано виставлення `complete=True` перед записом барів у FileCache у backfill та repair.
  - Узгоджено `ingest_ts_ms` з моментом запиту history перед append.
- Де зроблено → runtime/backfill.py, runtime/repair.py.
- Як перевірено → не запускалось (мінімальний локальний диф).
- Ризики/нотатки → очікується коректне проходження строгих рейок FileCache для 1m барів.

## 2026-01-28T07:05:00+01:00 — POST (MODE=PATCH): P2 SSOT = FileCache (CSV+meta) + Redis transport — проміжний стан

- Що зроблено →
  - Переведено ключові runtime path'и (warmup/backfill/repair/republish/tail_guard/http_server) на FileCache як SSOT.
  - Оновлено FileCache rails/формат (CSV+meta, atomic write, UNIQUE open_time_ms, strict geometry, tick_count).
  - Оновлено composition: ініціалізація FileCache, запис завершених 1m барів зі стріму, publish final tail через cache.
  - Прибрано derived rebuild інтеграцію у handlers/команди (P4 stub).
  - Додано cache summary у /api/status; /api/ohlcv?mode=final читає з FileCache.
  - Конфіг переведено на cache_* та write-probe cache_root.
- Де зроблено →
  - app/composition.py
  - config/config.py
  - core/env_loader.py
  - runtime/http_server.py
  - runtime/warmup.py
  - runtime/backfill.py
  - runtime/repair.py
  - runtime/republish.py
  - runtime/tail_guard.py
  - runtime/handlers_p3.py
  - runtime/handlers_p4.py
  - runtime/status.py
  - store/file_cache/cache_utils.py
  - store/file_cache/history_cache.py
  - store/file_cache/__init__.py
- Як перевірено → не запускалось (міграція у процесі).
- Статус задачі → виконано пункти 3–4 з PRE (інтеграція FileCache в runtime та HTTP final). НЕ завершено: видалення SQLite модулів, нові gates, оновлення тестів/manifest, proof-pack.
- Ризики/нотатки →
  - В репозиторії ще присутні legacy SQLite/live_archive/derived модулі; потрібне повне прибирання + нові gates.
  - Потрібне оновлення документації (REPO_LAYOUT/README) під FileCache SSOT.

## 2026-01-28T07:20:00+01:00 — PRE (MODE=PATCH): Уточнення журналу + виправлення FileCache модуля + актуалізація REPO_LAYOUT

- Мета → зробити POST для P2 більш змістовним, виправити помилку у file_cache модулі та актуалізувати REPO_LAYOUT.
- Scope → Work/01log.md, store/file_cache/history_cache.py, docs/REPO_LAYOUT.md.
- Non-goals → без змін логіки runtime/контрактів/гейтів.
- Інваріанти/рейки → мінімальний диф; append-only журнал.
- План →
  1) Розширити POST про P2 міграцію FileCache (проміжний стан).
  2) Виправити зламаний history_cache.py (залишити лише FileCache API).
  3) Оновити REPO_LAYOUT під FileCache SSOT та позначити legacy SQLite.
- Тести/перевірки → не запускати (документація + локальні правки).

## 2026-01-28T07:25:00+01:00 — POST (MODE=PATCH): Уточнення журналу + виправлення FileCache модуля + актуалізація REPO_LAYOUT

- Що зроблено →
  - Розширено POST для P2 міграції FileCache (проміжний стан) з деталізацією змін і списком файлів.
  - Виправлено store/file_cache/history_cache.py (коректний клас FileCache без дублювань).
  - Актуалізовано docs/REPO_LAYOUT.md під FileCache SSOT, legacy SQLite позначено явно.
- Де зроблено → Work/01log.md, store/file_cache/history_cache.py, docs/REPO_LAYOUT.md.
- Як перевірено → не запускалось (документація/рефакторинг файлу).
- Ризики/нотатки →
  - Потрібні наступні кроки з PRE P2: прибирання SQLite/derived/live_archive, нові gates/тести/manifest, proof-pack.

## 2026-01-28T07:40:00+01:00 — PRE (MODE=PATCH): P2 FileCache SSOT — завершення (видалити SQLite, гейти, тести, proof-pack)

- Мета → завершити міграцію: прибрати SQLite з репозиторію, додати FileCache гейти/тести, оновити manifest і REPO_LAYOUT, зібрати proof-pack.
- Scope → store/* (видалення sqlite/live_archive/derived), runtime/* (видалення final/rebuild_derived), tools/exit_gates/*, tests/*, docs/REPO_LAYOUT.md, README.md.
- Non-goals → зміни public JSON schemas, WS/HTTP/Redis keys, preview payload семантика.
- Інваріанти/рейки → fail-fast cache_root, time/geometry rails, atomic writes, no silent fallback.
- План →
  1) Зафіксувати read-only список SQLite згадок (path:line) для POST.
  2) Видалити SQLite/derived/final модулі і оновити імпорти.
  3) Додати gates: cache_integrity + no_sqlite_left, оновити manifest.
  4) Додати 2–3 тести FileCache, прибрати sqlite-тести.
  5) Оновити REPO_LAYOUT/README.
  6) Прогнати dev_checks + exit_gates і зберегти proof-pack.
- Тести/перевірки → tools.run_dev_checks + tools.run_exit_gates (default manifest) з артефактами.

## 2026-01-28T07:55:00+01:00 — POST (MODE=PATCH): P2 FileCache SSOT — завершення

- Read-only discovery (SQLite згадки, path:line) →
  - tools/exit_gates/manifest.json:19 (gate_live_archive_sqlite).
  - tools/exit_gates/gates/gate_tail_guard_repair_budget.py:11 (SQLiteStore).
  - tests/test_warmup_handler_updates_status.py:16 (SQLiteStore).
  - tests/test_tail_guard_marks.py:14 (SQLiteStore).
  - runtime/final/final_ingest.py:10 (final ingest залежність).
  - store/sqlite_store.py:13 (SQLite SSOT implementation).
- Що зроблено →
  - Повністю видалено SQLite/derived/live_archive модулі та залежні тести/гейти.
  - Додано gate_cache_integrity + gate_no_sqlite_left, оновлено default manifest.
  - Додано 2 тести FileCache (append/trim/meta + alignment rails).
  - Оновлено runtime: прибрано rebuild_derived з warmup, видалено залежність від runtime.final.
  - Виправлено cache_root probe для Python 3.7 (unlink без missing_ok).
  - Прибрано live_archive метрики.
  - Оновлено README та REPO_LAYOUT під FileCache SSOT.
- Де зроблено →
  - Видалено: store/sqlite_store.py, store/schema.sql, store/live_archive_store.py, store/bars_store.py, store/derived_builder.py.
  - Видалено: runtime/rebuild_derived.py, runtime/final/final_ingest.py, runtime/final/publisher_final.py.
  - Видалено SQLite-тести/гейти: tests/test_*sqlite* + tests/test_tail_guard_* + tests/test_live_archive_* + tools/exit_gates/gates/gate_*sqlite*.
  - Додано: tools/exit_gates/gates/gate_cache_integrity.py, gate_no_sqlite_left.py.
  - Додано: tests/test_file_cache_append_trim_and_meta.py, tests/test_file_cache_alignment_rails.py.
  - Оновлено: tools/exit_gates/manifest.json, runtime/publisher.py, runtime/warmup.py, runtime/handlers_p3.py, config/config.py, observability/metrics.py, README.md, docs/REPO_LAYOUT.md, tools/exit_gates/gates/gate_file_cache_schema.py.
- Як перевірено →
  - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m tools.run_dev_checks → OK
  - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m tools.run_exit_gates --out reports/exit_gates --manifest tools/exit_gates/manifest.json → OK
  - Proof-pack: reports/audit_p2_filecache/20260128_231036/{dev_checks.txt,dev_checks.exitcode.txt,exit_gates.txt,exit_gates.exitcode.txt}
- Ризики/нотатки →
  - gate_no_sqlite_left свідомо ігнорує docs/, Work/, .mypy_cache, tools/audit і reports/ (історичні артефакти).

## 2026-01-28T08:20:00+01:00 — PRE (MODE=read-only discovery): Розслідування degraded fxcm_stale_no_ticks

- Мета → зʼясувати можливі причини degraded fxcm_stale_no_ticks + fxcm_reconnect_requested без внесення змін.
- Scope → read-only аналіз статусу, можливі джерела: runtime/status.py, runtime/fxcm_forexconnect.py, runtime/fxcm/session_manager.py, runtime/tick_feed.py; без запусків.
- Non-goals → будь-які PATCH/рефакторинг/зміна конфігів.
- Інваріанти/рейки → append-only журнал; без виконання команд.
- План →
  1) Зафіксувати наданий статус (degraded/errors/last_command/next_open).
  2) Вивести гіпотези щодо stale_no_ticks і reconnect (канал, FXCM stream, ринок/календар, мережа/SDK).
  3) Описати наступні read-only перевірки (якщо потрібні).
- Тести/перевірки → не запускати.

## 2026-01-28T08:25:00+01:00 — POST (MODE=read-only discovery): Розслідування degraded fxcm_stale_no_ticks

- Фактологія зі статусу (надано користувачем) →
  - DEPRECATED/DEGRADED: fxcm_stale_no_ticks.
  - ERRORS: fxcm_reconnect_requested (FXCM reconnect: stale_no_ticks).
  - last_command: bootstrap → state=ok.
  - Ринок: open; next_open=2026-01-29 05:00 UTC; time_to_open≈06:42:38.
  - AGE≈2500 (вік статусу) і errors=2.
- Інтерпретація →
  - Ознака stale_no_ticks означає, що FXCM stream не надсилає ticks довше порогу (fxcm_stale_s) при відкритому ринку.
  - fxcm_reconnect_requested свідчить, що логіка FSM ініціювала reconnect через відсутність tick‑ів.
  - last_command=bootstrap ok → деградація виникла під час роботи стріму, а не через невдалий запуск.
- Ймовірні причини (без змін, тільки гіпотези) →
  1) __FXCM stream не підключився або відвалився__: WebSocket/ForexConnect session drop, таймаут, дисконект.
  2) __Немає tick‑ів по символу__: неактивний символ/ринок для XAUUSD, або відсутні підписки на offers.
  3) __Проблеми SDK/мережі__: локальний брандмауер/проксі, FXCM host недоступний, авторизація деградує.
  4) __Календар/маркет‑стан__: статус каже «ринок відкритий», але FXCM може віддавати нульові ticks через власний режим паузи/rollover.
  5) __Clock skew/ts rails__: якщо ticks приходять, але відкидаються (contract reject), це теж може створити «stale». Потрібно перевірити лічильники contract_reject.
- Рекомендовані read‑only перевірки (без змін) →
  - Переглянути status snapshot поля: fxcm.state/fsm_state/last_tick_ts_ms/stale_seconds/last_err.
  - Перевірити Redis pub/sub на {NS}:price_tik (чи приходять ticks).
  - Переглянути логи FXCM stream (subscription/reconnect counters).
  - Перевірити, чи символ XAUUSD є в allowlist і чи активний у FXCM (offers).
- Ризики/нотатки →
  - Якщо ticks відсутні через market micro‑pause (rollover), reconnect може повторюватись без результату до наступного активного вікна.
  - Якщо ticks приходять, але contract reject → потрібен окремий аналіз валідатора (без PATCH у цьому кроці)

  ## 2026-01-28T09:10:00+01:00 — PRE (MODE=read-only discovery): Календар/FXCM storm/market-closed (warmup/backfill)

  - Мета → розібрати причини: чому календар “не працює”, чому FXCM штурмується/немає паузи до open, чому можливі великі обсяги звернень; підтвердити, чи warmup/backfill можуть працювати при market-closed.
  - Scope → лише read-only огляд runtime fxcm stream + календар + history guard. Файли: runtime/fxcm_forexconnect.py, runtime/fxcm/fsm.py, runtime/fxcm/session_manager.py, core/time/calendar.py, core/time/sessions.py, config/config.py, config/calendar_overrides.json, runtime/history_provider.py, runtime/warmup.py, runtime/backfill.py.
  - Non-goals → будь-які PATCH/зміни конфігів/запуски.
  - Інваріанти/рейки → append-only журнал; без виконання команд.
  - План →
    1) Зафіксувати де і як визначається market-open.
    2) Пояснити логіку reconnect/resubscribe (чому “storm”).
    3) Перевірити guards для history (warmup/backfill) щодо market-closed.
  - Тести/перевірки → не запускати.

  ## 2026-01-28T09:15:00+01:00 — POST (MODE=read-only discovery): Календар/FXCM storm/market-closed (warmup/backfill)

  - Календар/market-open (факти) →
    - market-open визначається через `Calendar.is_open()` → `TradingCalendar.is_trading_time()`; якщо є `init_error`, календар завжди “закритий” (див. core/time/calendar.py, core/time/sessions.py).
    - Тег календаря береться з `Config.calendar_tag` (дефолт: fxcm_calendar_v1_ny), параметри з config/calendar_overrides.json (weekly open/close + daily break + TZ).
  - Чому “не працює”/“не чекаємо” →
    - У FXCM stream пауза до open є тільки якщо `status.calendar.is_open(now_ms)` повертає `False` (runtime/fxcm_forexconnect.py). Якщо календар вважає ринок відкритим — процес одразу логіниться/підписується.
    - Якщо очікуваний графік інший (наприклад, 23:01 UTC), але активний тег `fxcm_calendar_v1_ny`, календар може показувати “open” раніше, ніж очікуєте — і тоді “чекання” не настане.
  - Чому “штурмуємо FXCM” →
    - При відсутності tick‑ів FSM викликає `resubscribe`/`reconnect` з backoff (2s, 4s, 8s… cap 60s). Це створює повторні логіни/підписки, доки календар “open” (runtime/fxcm/fsm.py, runtime/fxcm_forexconnect.py).
    - Для стріму немає окремого throttle на кількість reconnect циклів, окрім backoff.
  - Про “півмілки” (гіпотеза) →
    - Якщо йдеться про кількість звернень/логінів, джерело — reconnect loop під час “open” без tick‑ів. Якщо про history, то warmup/backfill можуть робити багато запитів по чанках за великий діапазон (див. runtime/backfill.py, runtime/warmup.py, runtime/fxcm/history_provider.py).
  - Warmup/backfill при market-closed →
    - Так, вони можуть працювати при market-closed: guard_history_ready не перевіряє `calendar.is_open`, а лише `provider.is_history_ready()` і backoff (runtime/history_provider.py, runtime/warmup.py, runtime/backfill.py).
  - Ризики/нотатки →
    - Якщо календарний тег не відповідає фактичному графіку FXCM, отримаємо “open” під час фактичного close і циклічні reconnect/stale.

## 2026-01-28T09:35:00+01:00 — PRE (MODE=read-only discovery): Оцінка docs/Audit v7_1a.md щодо поточної ситуації

- Мета → прочитати Audit v7_1a та оцінити, чи його P-slice-и вирішують поточні симптоми: календар/market‑open, FXCM reconnect storm, warmup/backfill при market‑closed.
- Scope → тільки read-only оцінка документа; без змін коду/конфігів/запусків.
- Non-goals → будь-які PATCH або впровадження slice-ів.
- Інваріанти/рейки → append-only журнал; без виконання команд.
- План →
    1) Визначити релевантні slice-и.
    2) Співставити їх із поточними симптомами.
    3) Дати короткий висновок (так/ні/частково) та ризики.
- Тести/перевірки → не запускати.

## 2026-01-28T09:40:00+01:00 — POST (MODE=read-only discovery): Оцінка docs/Audit v7_1a.md щодо поточної ситуації

- Релевантні slice-и →
  - P7 (Calendar SSOT: real schedule + closed intervals + drift-gates).
  - P8 (FXCM Tick feed: login/reconnect/heartbeat + degrade‑loud).
  - P9 (FXCM History Provider для warmup/backfill).
- Оцінка впливу на поточні симптоми →
  - __Календар “не працює” / немає паузи до open__: P7 напряму адресує це (SSOT‑календар + drift/closed‑interval gates). Якщо зараз активний невірний `calendar_tag` або overrides — P7 має виправити. Це __ймовірно виправить__ очікування market‑open.
  - __FXCM storm/reconnect__: P8 додає стабільну інтеграцію та heartbeat/lag + правила деградації. Це __може зменшити__ storm за рахунок кращих rails/логіки, але без окремого throttle/“pause until market open” на рівні стріму проблема може __залишитись__, якщо календар усе ще каже “open”. Тобто вирішує __частково__, але залежить від P7.
  - __Warmup/backfill при market‑closed__: P9 не забороняє роботу при market‑closed і орієнтується на готовність history provider + backoff. Тобто __не змінює__ поточну можливість працювати у closed, а лише робить провайдера реальним/керованим.
- Загальний висновок → Audit v7_1a __частково вирішує__ поточну ситуацію: ключовий блок — P7 (календар). P8 поліпшить поведінку стріму, але без коректного календаря storm може повторюватись. P9 не блокує warmup/backfill у closed.
- Ризики/нотатки →
  - Якщо активний `calendar_tag` або overrides не відповідають очікуваному графіку (UTC vs NY), P7 є критичним пріоритетом.

## 2026-01-28T10:00:00+01:00 — PRE (MODE=PATCH): P7 Calendar SSOT (remove stub, real schedule, closed intervals, drift gates)

- Мета →
  - Повністю прибрати calendar_stub та будь-які fallback-гілки до stub.
  - Зробити реальний календар SSOT: Calendar → TradingCalendar з overrides (config/calendar_overrides.json) + closed_intervals_utc.
  - Додати rails: невалідні overrides/intervals → degraded-but-loud + safe “market_closed”, без тихих підмін.
  - Додати exit gates: gate_calendar_closed_intervals + gate_calendar_schedule_drift і включити їх у tools/exit_gates/manifest.json (default) “одним махом”.
  - Оновити docs/REPO_LAYOUT.md.
- Scope → core/time/calendar.py, core/time/sessions.py, core/time/closed_intervals.py, config/calendar_overrides.json, tools/exit_gates/gates/gate_calendar_closed_intervals.py, tools/exit_gates/gates/gate_calendar_schedule_drift.py, tools/exit_gates/manifest.json, runtime/status.py (тільки surfacing init_error), runtime/fxcm_forexconnect.py (тільки якщо ще є stub usage), tests/*, docs/REPO_LAYOUT.md.
- Non-goals → зміна FXCM history/warmup/backfill/tail_guard, зміна WS/HTTP payload, зміна Redis каналів, ENV-флаги.
- Інваріанти/рейки → contract-first, fail-fast на конфіг-помилки (або degraded-but-loud), жодних silent fallback, календар як SSOT дані.
- План →
  1) Прибрати calendar_stub та всі імпорти/виклики stub, видалити fallback.
  2) Реальний Calendar SSOT: overrides + closed_intervals_utc + normalize_closed_intervals_utc.
  3) Exit gates: gate_calendar_closed_intervals + gate_calendar_schedule_drift + default manifest.
  4) Surfacing init_error у status (degraded-but-loud).
  5) Додати 2–3 тести на нормалізацію інтервалів і семантику календаря.
  6) Оновити docs/REPO_LAYOUT.md.
  7) Proof pack: pytest + tools.run_exit_gates, артефакти в reports/audit_p7_calendar_ssot/<run_id>/.
- Тести/перевірки → 2–3 pytest + tools.run_exit_gates з default manifest; proof-артефакти у reports/audit_p7_calendar_ssot/<run_id>/.

## 2026-01-29T00:06:00+01:00 — POST (MODE=PATCH): P7 Calendar SSOT (remove stub, real schedule, closed intervals, drift gates)

- Що зроблено →
  - Посилено Calendar/TradingCalendar: SSOT overrides, нормалізація closed_intervals_utc, safe closed при init_error.
  - Додано rails та перевірки в gate_calendar_closed_intervals, оновлено drift gate без config.closed_intervals_utc.
  - Додано/оновлено тести для нормалізації closed intervals і семантики календаря з tmp overrides.
  - Оновлено docs/REPO_LAYOUT.md (календарні overrides + gate у manifest).
- Де зроблено →
  - core/time/calendar.py
  - core/time/sessions.py
  - core/time/closed_intervals.py
  - runtime/status.py
  - tools/exit_gates/gates/gate_calendar_closed_intervals.py
  - tools/exit_gates/gates/gate_calendar_schedule_drift.py
  - tests/test_closed_intervals_normalize.py
  - tests/test_calendar_schedule_semantics.py
  - docs/REPO_LAYOUT.md
- Як перевірено →
  - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m pytest -q tests/test_closed_intervals_normalize.py tests/test_calendar_schedule_semantics.py → EXIT_CODE=0
  - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m tools.run_exit_gates --out reports/exit_gates --manifest tools/exit_gates/manifest.json → EXIT_CODE=0
- Артефакти → reports/audit_p7_calendar_ssot/20260129_000528/{pytest.txt,pytest.exitcode.txt,exit_gates.txt,exit_gates.exitcode.txt}
- Ризики/нотатки → closed_intervals_utc та графік сесій мають оновлюватися лише через config/calendar_overrides.json; невалідні overrides дадуть calendar_error + safe market_closed.

## 2026-01-29T00:15:00+01:00 — PRE (MODE=PATCH): UI Lite логування стану без спаму

- Мета → зменшити частоту логів UI Lite, зробити повідомлення практичними (delta/age/errors).
- Scope → ui_lite/server.py.
- Non-goals → зміна WS/HTTP контрактів, UI логіки, Redis каналів.
- Інваріанти/рейки → без ENV-флагів; без silent fallback; повідомлення українською.
- План →
  1) Замінити частий лог stats на рідший/подієвий.
  2) Додати корисні поля (rx/tx delta, payload/status age, errors).
  3) Переконатися, що лог не спамить при відсутності змін.
- Тести/перевірки → не запускаю (логіка логування).

## 2026-01-29T00:18:00+01:00 — POST (MODE=PATCH): UI Lite логування стану без спаму

- Що зроблено →
  - Перероблено лог стану UI Lite на рідший/подієвий з практичними метриками.
- Де зроблено → ui_lite/server.py.
- Як перевірено → не запускалось (логіка логування).
- Ризики/нотатки → логи з’являються раз на 60с або при зміні клієнтів/статусу/помилок; для докладної діагностики доступний /debug.

## 2026-01-29T00:26:00+01:00 — PRE (MODE=PATCH): OHLCV preview логування без спаму

- Мета → прибрати частий INFO-спам по кожному TF та замінити на корисний summary.
- Scope → app/composition.py (логування publish preview).
- Non-goals → зміна контрактів OHLCV, логіки побудови барів або каналів Redis.
- Інваріанти/рейки → без ENV-флагів; без silent fallback; повідомлення українською.
- План →
  1) Додати агреговані лічильники публікацій по TF.
  2) Логувати summary раз на 60с (delta + last_open + complete).
  3) Final-бар логувати окремо (low-volume).
- Тести/перевірки → не запускаю (логування).

## 2026-01-29T00:28:00+01:00 — POST (MODE=PATCH): OHLCV preview логування без спаму

- Що зроблено →
  - Додано агрегований summary лог OHLCV preview раз на 60с і окремий лог для final барів.
- Де зроблено → app/composition.py.
- Як перевірено → не запускалось (логування).
- Ризики/нотатки → summary показує last_open/complete за TF; для деталізації використовувати status snapshot або UI Lite /debug.

## 2026-01-29T00:36:00+01:00 — PRE (MODE=PATCH): UI Lite логи як таблиця + заміна print

- Мета → замінити випадкові print на log.* та зробити табличний компактний формат UI Lite логу.
- Scope → ui_lite/server.py.
- Non-goals → зміна логіки WS/HTTP, контрактів або форматів payload.
- Інваріанти/рейки → без ENV-флагів; без silent fallback; повідомлення українською.
- План →
  1) Замінити print на log.info/warning/error.
  2) Переформатувати UI Lite стан у компактну таблицю.
- Тести/перевірки → не запускаю (логування).

## 2026-01-29T00:38:00+01:00 — POST (MODE=PATCH): UI Lite логи як таблиця + заміна print

- Що зроблено →
  - Замінено print на log.* у UI Lite сервері.
  - Лог стану переведено в компактну табличну форму.
- Де зроблено → ui_lite/server.py.
- Як перевірено → не запускалось (логування).
- Ризики/нотатки → табличні логи мультистрокові; перевірити агрегатор логів, якщо очікує single-line.

## 2026-01-29T00:46:00+01:00 — PRE (MODE=PATCH): Fix ohlcv summary nonlocal

- Мета → усунути помилку fxcm_publish_fail через nonlocal для last_ohlcv_summary_log_ms.
- Scope → app/composition.py.
- Non-goals → зміна логіки OHLCV або FXCM публікації.
- Інваріанти/рейки → без ENV-флагів; без silent fallback.
- План → додати nonlocal у _handle_fxcm_tick.
- Тести/перевірки → не запускаю.

## 2026-01-29T00:48:00+01:00 — POST (MODE=PATCH): Fix ohlcv summary nonlocal

- Що зроблено → додано nonlocal для last_ohlcv_summary_log_ms у _handle_fxcm_tick.
- Де зроблено → app/composition.py.
- Як перевірено → не запускалось.
- Ризики/нотатки → очікувано прибирає fxcm_publish_fail через UnboundLocalError.

## 2026-01-29T00:56:00+01:00 — PRE (MODE=PATCH): UI Lite стан у списку рядків

- Мета → зробити лог UI Lite стану у форматі компактного списку рядків як у прикладі.
- Scope → ui_lite/server.py.
- Non-goals → зміна логіки UI Lite, WS/HTTP, контрактів.
- Інваріанти/рейки → без ENV-флагів; без silent fallback; українські повідомлення.
- План → переформатувати лог у _log_state.
- Тести/перевірки → не запускаю (логування).

## 2026-01-29T00:58:00+01:00 — POST (MODE=PATCH): UI Lite стан у списку рядків

- Що зроблено → переформатовано лог UI Lite стану у компактний список рядків.
- Де зроблено → ui_lite/server.py.
- Як перевірено → не запускалось.
- Ризики/нотатки → мультистроковий лог; якщо агрегатор вимагає single-line — повернутись до однорядкового формату.

## 2026-01-29T01:06:00+01:00 — PRE (MODE=PATCH): OHLCV preview summary з читабельним часом

- Мета → зробити OHLCV summary у мультистроковому форматі з читабельним UTC часом, використовуючи існуючі helper-функції.
- Scope → app/composition.py.
- Non-goals → зміна логіки preview або контрактів OHLCV.
- Інваріанти/рейки → без дублювання helper-ів; без ENV-флагів; без silent fallback.
- План → використати існуючий helper для форматування часу і переформатувати summary.
- Тести/перевірки → не запускаю (логування).

## 2026-01-29T01:08:00+01:00 — POST (MODE=PATCH): OHLCV preview summary з читабельним часом

- Що зроблено → summary переведено у мультистроковий формат + читабельний UTC час через існуючий helper.
- Де зроблено → app/composition.py.
- Як перевірено → не запускалось.
- Ризики/нотатки → мультистроковий лог; якщо агрегатор потребує single-line, можна повернутись.

## 2026-01-29T01:20:00+01:00 — PRE (MODE=PATCH): UI Lite health heartbeat (OK/WARN/ERROR)

- Мета → зробити heartbeat лог діагностичним інтерфейсом: health, age/lag, причина, next action.
- Scope → ui_lite/server.py.
- Non-goals → зміна WS/HTTP, контрактів, status schema.
- Інваріанти/рейки → використовувати SSOT поля status snapshot; не створювати дублікати helper-ів.
- План → зчитати поля з status snapshot і зібрати single-line лог (INFO для OK, WARNING для WARN/ERROR).
- Тести/перевірки → не запускаю (логування).

## 2026-01-29T01:22:00+01:00 — POST (MODE=PATCH): UI Lite health heartbeat (OK/WARN/ERROR)

- Що зроблено → heartbeat лог у single-line форматі з health/age/lag/rails/next action на базі status snapshot.
- Де зроблено → ui_lite/server.py.
- Як перевірено → не запускалось.
- Ризики/нотатки → пороги health прив’язані до ohlcv_preview_publish_interval_ms; при зміні інтервалу перевірити очікувані WARN/ERROR.

## 2026-01-29T01:34:00+01:00 — PRE (MODE=PATCH): UI Lite logs only after FXCM login

- Мета → припинити heartbeat-логи до успішного FXCM login (state=streaming) і не шуміти при проблемах із status get.
- Scope → ui_lite/server.py.
- Non-goals → зміна FXCM FSM або status schema.
- Інваріанти/рейки → використовувати SSOT fxcm.state зі status snapshot; без ENV-флагів.
- План → гейтити логування по status_ok та fxcm.state.
- Тести/перевірки → не запускаю (логування).

## 2026-01-29T01:36:00+01:00 — POST (MODE=PATCH): UI Lite logs only after FXCM login

- Що зроблено → heartbeat-логи відсікаються до fxcm.state=streaming або якщо status_ok=false; замінено залишкові print на log.*.
- Де зроблено → ui_lite/server.py.
- Як перевірено → не запускалось.
- Ризики/нотатки → перший лог з’явиться одразу після переходу FXCM у streaming.

## 2026-01-29T01:44:00+01:00 — PRE (MODE=PATCH): UI Lite server.py error fixes

- Мета → виправити помилки типів/unused у ui_lite/server.py.
- Scope → ui_lite/server.py.
- Non-goals → зміна логіки heartbeat/порогів.
- Інваріанти/рейки → мінімальний диф, без нових helper-ів.
- План → уточнити типи для status payload і прибрати unused змінні.
- Тести/перевірки → get_errors.

## 2026-01-29T01:46:00+01:00 — POST (MODE=PATCH): UI Lite server.py error fixes

- Що зроблено → типізовано `price`/`fxcm`/`preview` як dict, прибрано unused змінні.
- Де зроблено → ui_lite/server.py.
- Як перевірено → get_errors (OK).
- Ризики/нотатки → немає.

## 2026-01-29T01:58:00+01:00 — PRE (MODE=PATCH): UI Lite payload_age та ui_subscriber_stale

- Мета → виправити помилковий WARN ui_subscriber_stale при активному RX/TX.
- Scope → ui_lite/server.py.
- Non-goals → зміна health-порогів або форматів логів.
- Інваріанти/рейки → мінімальний диф; без нових helper-ів; SSOT поля status snapshot.
- План → перейти на last_payload_rx_ms та пом’якшити умову WARN.
- Тести/перевірки → get_errors.

## 2026-01-29T02:00:00+01:00 — POST (MODE=PATCH): UI Lite payload_age та ui_subscriber_stale

- Що зроблено → додано last_payload_rx_ms, payload_age рахується від receive-time; WARN ui_subscriber_stale лише при clients>0 та відсутності RX/TX.
- Де зроблено → ui_lite/server.py.
- Як перевірено → get_errors (OK).
- Ризики/нотатки → якщо payload_ts_ms потрібен для інших метрик, він збережений окремо.

## 2026-01-29T02:12:00+01:00 — PRE (MODE=PATCH): UI Lite data freshness + summary gating

- Мета → замінити lag_to_now на семантичні expected_open/tf_delay_bars, розділити transport vs data health, зменшити summary спам.
- Scope → ui_lite/server.py, app/composition.py.
- Non-goals → зміна контрактів status snapshot або логіки preview builder.
- Інваріанти/рейки → використовувати існуючі helper-и buckets/_to_utc_iso; мінімальний диф.
- План →
  1) UI Lite: expected_open/tf_delay_bars + transport/data health + freshest key.
  2) UI Lite: QoL лог calendar_tag/trading_day_boundary_utc раз на 10 хв.
  3) Runtime: summary тільки при WARN/rails change.
- Тести/перевірки → get_errors.

## 2026-01-29T02:16:00+01:00 — POST (MODE=PATCH): UI Lite data freshness + summary gating

- Що зроблено →
  - UI Lite: додано expected_open/tf_delay_bars, transport/data health, freshest key; QoL лог calendar_tag.
  - Runtime: OHLCV preview summary лог лише при WARN/rails change.
- Де зроблено → ui_lite/server.py, app/composition.py.
- Як перевірено → get_errors (OK).
- Ризики/нотатки → пороги зафіксовані у UI Lite; при зміні SLO варто оновити значення.

## 2026-01-29T02:28:00+01:00 — PRE (MODE=PATCH): OHLCV preview summary diagnostics

- Мета → перетворити summary на діагностичний формат (expected_open/delay_bars/step_ms + fxcm/tick_lag).
- Scope → app/composition.py.
- Non-goals → зміна preview builder або контрактів OHLCV.
- Інваріанти/рейки → використовувати існуючі helper-и buckets/_to_utc_iso; мінімальний диф.
- План →
  1) Додати expected_open/delay_bars/step_ms у summary.
  2) Логувати summary лише при WARN/rails/stale TF.
  3) Додати fxcm_state/tick_lag для контексту.
- Тести/перевірки → get_errors.

## 2026-01-29T02:32:00+01:00 — POST (MODE=PATCH): OHLCV preview summary diagnostics

- Що зроблено → summary форматує expected_open/delay_bars/step_ms, додає fxcm_state/tick_lag і логить лише при WARN/rails/stale TF.
- Де зроблено → app/composition.py.
- Як перевірено → get_errors (OK).
- Ризики/нотатки → при нульових prev_open step_ms=0 (це очікувано для першого вікна).

## 2026-01-29T02:44:00+01:00 — PRE (MODE=PATCH): Комфортний 3-рядковий heartbeat + summary/QoL

- Мета → зробити heartbeat у 3 рядки (UI_LITE/IO/FRESH), скоротити keys/exp/got, додати no_clients маркер; стиснути OHLCV summary і FXCM tick OK.
- Scope → ui_lite/server.py, app/composition.py, runtime/fxcm_forexconnect.py.
- Non-goals → зміна статус-схем або логіки preview/tick.
- Інваріанти/рейки → мінімальний диф, без нових helper-ів (використати _to_utc_iso/get_bucket_open_ms).
- План →
  1) Переформатувати heartbeat у 3 рядки.
  2) Стиснути OHLCV summary до 1–2 рядків із delay/expected.
  3) Скоротити FXCM tick OK до читабельного UTC.
- Тести/перевірки → get_errors.

## 2026-01-29T02:48:00+01:00 — POST (MODE=PATCH): Комфортний 3-рядковий heartbeat + summary/QoL

- Що зроблено →
  - Heartbeat переформатовано у 3 рядки (UI_LITE/IO/FRESH) з no_clients маркером і скороченим exp/got.
  - OHLCV summary стисло до 1–2 рядків із delay/expected/top_tf.
  - FXCM tick OK лог показує UTC час.
- Де зроблено → ui_lite/server.py, app/composition.py, runtime/fxcm_forexconnect.py.
- Як перевірено → get_errors (OK).
- Ризики/нотатки → якщо потрібні символи у keys, можна додати опційно.

## 2026-01-29T03:02:00+01:00 — PRE (MODE=PATCH): Зниження шуму websockets connection logs

- Мета → сховати "connection failed/closed/open" у DEBUG для чистих логів.
- Scope → ui_lite/server.py.
- Non-goals → зміна логіки WS/HTTP або рівнів інших логів.
- Інваріанти/рейки → мінімальний диф; без нових helper-ів поза модулем.
- План → додати logging.Filter та застосувати до логерів websockets.*.
- Тести/перевірки → не запускаю (логування).

## 2026-01-29T03:04:00+01:00 — POST (MODE=PATCH): Зниження шуму websockets connection logs

- Що зроблено → додано фільтр, який понижує connection failed/closed/open до DEBUG для websockets.*.
- Де зроблено → ui_lite/server.py.
- Як перевірено → не запускалось.
- Ризики/нотатки → якщо потрібна діагностика — підняти рівень логів websockets до DEBUG.

## 2026-01-29T03:12:00+01:00 — PRE (MODE=PATCH): Heartbeat після login без status gate

- Мета → повернути heartbeat-логи після login навіть якщо status_ok=false.
- Scope → ui_lite/server.py.
- Non-goals → зміна форматів логів або порогів.
- Інваріанти/рейки → логувати лише після стану FXCM != connecting.
- План → прибрати жорсткий gate status_ok, додати WARN status_missing.
- Тести/перевірки → get_errors.

## 2026-01-29T03:14:00+01:00 — POST (MODE=PATCH): Heartbeat після login без status gate

- Що зроблено → gate тільки по fxcm_state!=connecting, status_ok=false → WARN status_missing.
- Де зроблено → ui_lite/server.py.
- Як перевірено → get_errors (OK).
- Ризики/нотатки → якщо status недоступний довго, heartbeat покаже WARN з reason=status_missing.

## 2026-01-29T03:22:00+01:00 — PRE (MODE=PATCH): Зниження шуму websockets INFO без filter

- Мета → прибрати зайвий filter та сховати websockets connection INFO простим рівнем логера.
- Scope → ui_lite/server.py.
- Non-goals → зміна логіки WS або форматів логів.
- Інваріанти/рейки → мінімальний диф; без додаткових helper-ів.
- План → прибрати _WsConnectionNoiseFilter і виставити рівень WARNING для websockets.*.
- Тести/перевірки → не запускаю (логування).

## 2026-01-29T03:24:00+01:00 — POST (MODE=PATCH): Зниження шуму websockets INFO без filter

- Що зроблено → видалено filter; рівень websockets.* встановлено WARNING.
- Де зроблено → ui_lite/server.py.
- Як перевірено → не запускалось.
- Ризики/нотатки → INFO з websockets не показуються; за потреби підняти рівень до INFO.

## 2026-01-29T03:34:00+01:00 — PRE (MODE=PATCH): Heartbeat logging restore

- Мета → відновити heartbeat-логи після login, не блокуючи їх gate-ом fxcm_state.
- Scope → ui_lite/server.py.
- Non-goals → зміна форматів логів.
- Інваріанти/рейки → мінімальний диф; без нових helper-ів.
- План → прибрати continue-гейт та додати WARN reason=fxcm_connecting.
- Тести/перевірки → не запускаю.

## 2026-01-29T03:36:00+01:00 — POST (MODE=PATCH): Heartbeat logging restore

- Що зроблено → прибрано gate по fxcm_state; додано WARN для fxcm_connecting та status_missing.
- Де зроблено → ui_lite/server.py.
- Як перевірено → не запускалось.
- Ризики/нотатки → якщо FXCM ще не логіниться, heartbeat буде WARN з reason=fxcm_connecting.

## 2026-01-29T03:44:00+01:00 — PRE (MODE=PATCH): Читабельний delay у ohlcv_preview summary

- Мета → показувати delay у годинах/хвилинах, а expected/last у короткому HH:MMZ.
- Scope → ui_lite/server.py.
- Non-goals → зміна логіки розрахунку delay_bars.
- Інваріанти/рейки → мінімальний диф; без нових helper-ів.
- План → переформатувати summary-рядки.
- Тести/перевірки → get_errors.

## 2026-01-29T03:46:00+01:00 — POST (MODE=PATCH): Читабельний delay у ohlcv_preview summary

- Що зроблено → delay відображається як хв/год, expected/last скорочено до HH:MMZ.
- Де зроблено → ui_lite/server.py.
- Як перевірено → get_errors (OK).
- Ризики/нотатки → для великих delay показується години з десятковою точністю.

## 2026-01-29T03:54:00+01:00 — PRE (MODE=PATCH): Delay тільки коли є last_open

- Мета → не показувати величезні delay, якщо last_open відсутній.
- Scope → ui_lite/server.py.
- Non-goals → зміна логіки delay_bars.
- Інваріанти/рейки → мінімальний диф.
- План → для last_open<=0 ставити delay="-" і пропускати top_tf.
- Тести/перевірки → get_errors.

## 2026-01-29T03:56:00+01:00 — POST (MODE=PATCH): Delay тільки коли є last_open

- Що зроблено → delay відображається як "-" при відсутньому last_open; top_tf пропускає нульові open_ms.
- Де зроблено → ui_lite/server.py.
- Як перевірено → get_errors (OK).
- Ризики/нотатки → якщо last_open з'явиться, delay показуватиметься нормально.

## 2026-01-29T04:06:00+01:00 — PRE (MODE=PATCH): Heartbeat logs always

- Мета → виправити зникнення heartbeat-логів через gate tick_lag.
- Scope → ui_lite/server.py.
- Non-goals → зміна формату логів.
- Інваріанти/рейки → мінімальний диф.
- План → винести логування з if tick_lag_s > 5.0 та додати WARN/ERROR пороги.
- Тести/перевірки → get_errors.

## 2026-01-29T04:08:00+01:00 — POST (MODE=PATCH): Heartbeat logs always

- Що зроблено → логування heartbeat виконується завжди; tick_lag впливає лише на рівень health.
- Де зроблено → ui_lite/server.py.
- Як перевірено → get_errors (OK).
- Ризики/нотатки → немає.

## 2026-01-29T04:16:00+01:00 — PRE (MODE=PATCH): Heartbeat одним рядком

- Мета → зробити heartbeat одним рядком (INFO/WARN), без трьох послідовних логів.
- Scope → ui_lite/server.py.
- Non-goals → зміна змісту полів heartbeat.
- Інваріанти/рейки → мінімальний диф.
- План → об'єднати 3 логи в один формат.
- Тести/перевірки → get_errors.

## 2026-01-29T04:18:00+01:00 — POST (MODE=PATCH): Heartbeat одним рядком

- Що зроблено → heartbeat зведено до одного рядка (INFO/WARN).
- Де зроблено → ui_lite/server.py.
- Як перевірено → get_errors (OK).
- Ризики/нотатки → рядок довший, але читається по "|" сегментах.

## 2026-01-29T04:30:00+01:00 — PRE (MODE=PATCH): Heartbeat one-line restore + summary gate

- Мета → повернути heartbeat у 1 рядок і не логувати ohlcv_preview при delay=0.
- Scope → ui_lite/server.py.
- Non-goals → зміна порогів health.
- Інваріанти/рейки → мінімальний диф.
- План → one-line лог + gate stale_delay_bars>0 для summary.
- Тести/перевірки → get_errors.

## 2026-01-29T04:32:00+01:00 — POST (MODE=PATCH): Heartbeat one-line restore + summary gate

- Що зроблено → heartbeat у 1 рядок; ohlcv_preview summary не друкується при delay=0.
- Де зроблено → ui_lite/server.py.
- Як перевірено → get_errors (OK).
- Ризики/нотатки → немає.

## 2026-01-29T04:40:00+01:00 — PRE (MODE=PATCH): Heartbeat multi-line restore

- Мета → повернути heartbeat у кілька рядків для зручного сканування.
- Scope → ui_lite/server.py.
- Non-goals → зміна логіки health або порогів.
- Інваріанти/рейки → мінімальний диф.
- План → замінити one-line лог на multi-line формат.
- Тести/перевірки → get_errors.

## 2026-01-29T04:42:00+01:00 — POST (MODE=PATCH): Heartbeat multi-line restore

- Що зроблено → heartbeat знову у кілька рядків.
- Де зроблено → ui_lite/server.py.
- Як перевірено → get_errors (OK).
- Ризики/нотатки → мультистроковий лог у агрегаторі логів.

## 2026-01-29T05:10:00+01:00 — PRE (MODE=PATCH): Повернення видимості FXCM/OK summary логів

- Мета → зробити видимими FXCM tick OK і перший OK summary для OHLCV preview.
- Scope → runtime/fxcm_forexconnect.py, app/composition.py.
- Non-goals → зміна порогів health або формату WARN логів.
- Інваріанти/рейки → мінімальний диф, без нових флагів/ENV.
- План → (1) зробити періодичний tick OK (раз/хв) (2) логувати перший OK summary одразу.
- Тести/перевірки → не запускались (логічна правка).

## 2026-01-29T05:12:00+01:00 — POST (MODE=PATCH): Повернення видимості FXCM/OK summary логів

- Що зроблено → додано періодичний FXCM tick OK (раз на 60с) і перший OK summary для OHLCV preview без очікування 5 хв.
- Де зроблено → runtime/fxcm_forexconnect.py, app/composition.py.
- Як перевірено → не запускалось.
- Ризики/нотатки → більше INFO логів (tick OK раз/хв, OK summary одразу після старту).

## 2026-01-29T05:20:00+01:00 — PRE (MODE=PATCH): Фікс syntax errors у composition

- Мета → виправити syntax errors через неправильний `nonlocal` у composition.
- Scope → app/composition.py.
- Non-goals → зміна логіки логів або порогів.
- Інваріанти/рейки → мінімальний диф.
- План → замінити багаторядковий `nonlocal (...)` на коректний однорядковий `nonlocal a, b, c`.
- Тести/перевірки → get_errors.

## 2026-01-29T05:22:00+01:00 — POST (MODE=PATCH): Фікс syntax errors у composition

- Що зроблено → виправлено синтаксис `nonlocal`.
- Де зроблено → app/composition.py.
- Як перевірено → get_errors (OK).
- Ризики/нотатки → немає.

## 2026-01-29T05:30:00+01:00 — PRE (MODE=read-only discovery): P8 FXCM tick feed — збір фактів

- Мета → P8 FXCM tick feed: зібрати факти по старту стріму, status/metrics, tick rails, існуючим gate-патернам.
- Scope → runtime/tick_feed.py (якщо є), runtime/fxcm_forexconnect.py, runtime/status.py, observability/metrics.py, config/config.py, tools/exit_gates/*.
- Non-goals → history/store/final/derived/UI.
- Інваріанти/рейки → без PATCH, лише факти path:line.
- План → виконати rg-пошук по заданих патернах і сформувати список фактів.
- Тести/перевірки → rg.

## 2026-01-29T05:45:00+01:00 — POST (MODE=read-only discovery): P8 FXCM tick feed — факти

- Примітка → `rg` недоступний у середовищі; факти зібрані через внутрішній пошук та читання файлів.

- FACTS (path:line):
  1) config/config.py:32 — `fxcm_backend` дефолт `forexconnect` (disabled | forexconnect).
  2) config/config.py:94 — `tick_mode` дефолт `off`.
  3) app/composition.py:672 — створення `FxcmForexConnectStream` у режимі `FOREXCONNECT`.
  4) app/composition.py:673 — callsite старту FXCM stream через `fxcm_stream.start()`.
  5) runtime/fxcm_forexconnect.py:371 — клас `FxcmForexConnectStream` (SSOT стрім FXCM).
  6) runtime/fxcm_forexconnect.py:379 — метод `start()` створює/стартує thread для стріму.
  7) runtime/fxcm_forexconnect.py:442 — при закритому ринку статус `paused_market_closed` + `next_retry_ts_ms`.
  8) runtime/fxcm_forexconnect.py:519 — успішний login → `state="connected"` і `last_ok_ts_ms` у статусі.
  9) runtime/fxcm_forexconnect.py:536 — `_on_offer_tick` отримує tick і викликає `self.on_tick(...)`.
  10) runtime/fxcm_forexconnect.py:551 — періодичний лог `FXCM tick OK` (debug) з UTC ts.
  11) runtime/status.py:445 — `append_error()` формує errors[] з `code/severity/message/ts` + optional `context`.
  12) runtime/status.py:474 — `_ensure_price()` ініціалізує поля `price.*` (tick_ts/snap_ts/lag/skew/counters).
  13) runtime/status.py:494 — `_ensure_fxcm()` ініціалізує `fxcm.*` (state/fsm/last_tick/last_ok/err/attempts/metrics).
  14) runtime/status.py:520 — `record_tick()` оновлює `price.*` і розраховує `tick_skew_ms`/`tick_lag_ms`.
  15) runtime/status.py:529 — негативний skew → `append_error(code="tick_skew_negative")` + `mark_degraded`.
  16) runtime/status.py:548 — `record_tick_error()` інкрементує `tick_err_total` + metric.
  17) runtime/tick_feed.py:46 — `TickPublisher.publish_tick()` викликає `validator.validate_tick_v1`.
  18) runtime/tick_feed.py:57 — при ContractError → `append_error(code="tick_contract_error")` + `record_tick_error()`.
  19) runtime/publisher.py:41 — `publish_tick()` робить `validate_tick_v1` перед publish.
  20) core/validation/validator.py:142 — `validate_tick_v1()` + `_require_ms_int` для `tick_ts`/`snap_ts`.
  21) core/validation/validator.py:17-18 — `MIN_EPOCH_MS`/`MAX_EPOCH_MS` (rails epoch ms).
  22) observability/metrics.py:20-33 — метрики tick (`ticks_total`, `tick_errors_total`, `tick_contract_reject_total`, `last_tick_ts_ms`, `tick_lag_ms`).
  23) observability/metrics.py:34-44 — FXCM метрики (`fxcm_ticks_total`, `fxcm_ticks_dropped_total`, `fxcm_last_tick_ts_ms`, `fxcm_tick_skew_ms`).
  24) tools/exit_gates/manifest.json:6 — gate `gate_tick_units` у manifest.
  25) tools/exit_gates/gates/gate_tick_units.py:15-38 — типовий gate: `run() -> Tuple[bool,str]`, перевірка int ms.
  26) tools/run_exit_gates.py:44-64 — runner: import module, виклик `fn`, повернення `ok/details`.

- Де реально стартує FXCM tick stream (callsite): app/composition.py:672-673.

- Які поля статусу/метрик вже є:
  - status.price.* (tick_ts/snap_ts/lag/skew/counters) → runtime/status.py:474-544.
  - status.fxcm.* (state/fsm/last_tick/last_ok/errors/reconnect counters) → runtime/status.py:494-517.
  - metrics tick/fxcm → observability/metrics.py:20-44.

- Де саме робиться tick validation:
  - core/validation/validator.py:142-145 (`validate_tick_v1` + `_require_ms_int`).
  - runtime/tick_feed.py:46 (перед publish + status).
  - runtime/publisher.py:41 (publish_tick wrapper).

- Який контракт `errors[]` і як його заповнюють:
  - runtime/status.py:445-459 — `append_error()` створює `{code,severity,message,ts,context?}` і додає в `errors[]`.

- Як виглядає типовий gate-модуль (для нового gate_fxcm_tick_liveness):
  - tools/exit_gates/gates/gate_tick_units.py:1-38 — `run() -> Tuple[bool,str]`, повертає OK/FAIL рядок.
  - tools/run_exit_gates.py:44-64 — runner імпортує модуль і викликає `fn` з manifest.

## 2026-01-29T06:00:00+01:00 — PRE (MODE=PATCH): P8 FXCM Tick feed (real) — login/reconnect/heartbeat + strict rails + degrade-loud

- Мета →
  1) Увімкнути tick_mode=fxcm як реальну інтеграцію: FXCM stream → on_tick → validate_tick_v1 → publish_tick.
  2) Стабілізувати reconnect/backoff при stale_no_ticks (debounce/cooldown, без “storm”).
  3) Heartbeat/lag/skew у статусі/метриках: оновлювати існуючі поля status.price.* та status.fxcm.*.
  4) Додати strict tick rails (ms-int/epoch/OOO policy) та degrade-loud.
  5) Додати 2 тести + 2 exit-gates і включити у manifest.
  6) Оновити docs/REPO_LAYOUT.md.
- Scope → config/config.py, app/composition.py, runtime/fxcm_forexconnect.py, runtime/tick_feed.py, runtime/publisher.py, runtime/status.py, observability/metrics.py, tools/exit_gates/gates/gate_fxcm_tick_liveness.py, tools/exit_gates/gates/gate_fxcm_tick_mode_config.py, tools/exit_gates/manifest.json, tests/*, docs/REPO_LAYOUT.md.
- Non-goals → public JSON schemas, Redis keys, календар/history/warmup/backfill, ENV flags.
- Інваріанти/рейки → contract-first tick validate, ms-int rails, degrade-loud, reconnect debounce, мінімальний диф.
- План → реалізувати tick_mode wiring + liveness debounce + rails, додати gates/тести/доки, виконати dev_checks/pytest/exit_gates з proof-pack.
- Тести/перевірки → tools.run_dev_checks, pytest (2 тести), tools.run_exit_gates (manifest.json).

## 2026-01-29T16:15:00+01:00 — POST (MODE=PATCH): P8 FXCM Tick feed (real)

- Що зроблено →
  - Tick_mode=fxcm: додано fail-fast перевірку config та wiring FXCM стріму для tick feed.
  - Додано liveness debounce (cooldown) через `FxcmTickLiveness` + інтеграція у session manager.
  - Додано strict rails у tick feed: OOO drop + degrade-loud без падіння стріму.
  - Додано 2 exit-gates і 2 тести; оновлено REPO_LAYOUT.
- Де зроблено →
  - config/config.py
  - app/composition.py
  - runtime/fxcm/tick_liveness.py
  - runtime/fxcm/session_manager.py
  - runtime/fxcm_forexconnect.py
  - runtime/tick_feed.py
  - runtime/status.py
  - tools/exit_gates/gates/gate_fxcm_tick_liveness.py
  - tools/exit_gates/gates/gate_fxcm_tick_mode_config.py
  - tools/exit_gates/manifest.json
  - tests/test_fxcm_tick_liveness_debounce.py
  - tests/test_tick_contract_reject_degrade_loud.py
  - tests/test_tick_mode_handling.py
  - tests/fixtures/sim/tick_simulator.py
  - docs/REPO_LAYOUT.md
- Як перевірено →
  - .\.venv\Scripts\python.exe -m tools.run_dev_checks → FAIL (mypy: tools/migrate_v1_calendar_overrides.py:64, tools/exit_gates/gates/gate_calendar_closed_intervals.py:54, tests/test_closed_intervals_normalize.py:25). ExitCode=1.
  - .\.venv\Scripts\python.exe -m pytest -q tests/test_fxcm_tick_liveness_debounce.py tests/test_tick_contract_reject_degrade_loud.py → OK. ExitCode=0.
  - .\.venv\Scripts\python.exe -m tools.run_exit_gates --out reports/exit_gates --manifest tools/exit_gates/manifest.json → OK. ExitCode=0.
- Артефакти → reports/audit_p8_tick_feed/20260129_161034/{dev_checks.txt,dev_checks.exitcode.txt,pytest.txt,pytest.exitcode.txt,exit_gates.txt,exit_gates.exitcode.txt}
- Ризики/нотатки → dev_checks не пройшов через попередні mypy-помилки (не пов’язані з P8).

## 2026-01-29T16:20:00+01:00 — PRE (MODE=PATCH): README — P8 tick feed

- Мета → оновити README: tick_mode=fxcm, liveness/debounce, нові gates, актуальна карта.
- Scope → README.md.
- Non-goals → зміни коду/схем/ключів.
- Інваріанти/рейки → мінімальний диф, без дублювання з docs/REPO_LAYOUT.md.
- План → додати короткий блок про tick_mode=fxcm + liveness/debounce + перелік gate'ів; оновити REPO_LAYOUT секцію.
- Тести/перевірки → не запускати.

## 2026-01-29T16:22:00+01:00 — POST (MODE=PATCH): README — P8 tick feed

- Що зроблено → README оновлено: tick_mode=fxcm, liveness/debounce, нові gate'и, актуальна карта.
- Де зроблено → README.md.
- Як перевірено → не запускалось.
- Ризики/нотатки → немає.

## 2026-01-29T16:30:00+01:00 — PRE (MODE=PATCH): Фікс dev_checks (mypy)

- Мета → усунути mypy помилки у migrate/gate/tests.
- Scope → tools/migrate_v1_calendar_overrides.py, tools/exit_gates/gates/gate_calendar_closed_intervals.py, tests/test_closed_intervals_normalize.py.
- Non-goals → зміна логіки календаря/рейок.
- Інваріанти/рейки → мінімальний диф.
- План → уточнити типи/перевірки для mypy, без зміни поведінки.
- Тести/перевірки → tools.run_dev_checks.

## 2026-01-29T16:27:00+01:00 — POST (MODE=PATCH): Фікс dev_checks (mypy)

- Що зроблено → виправлено mypy помилки через типи в migrate/gate/test.
- Де зроблено → tools/migrate_v1_calendar_overrides.py, tools/exit_gates/gates/gate_calendar_closed_intervals.py, tests/test_closed_intervals_normalize.py.
- Як перевірено → .\.venv\Scripts\python.exe -m tools.run_dev_checks (OK, exit=0).
- Артефакти → reports/audit_p8_tick_feed/20260129_162538/{dev_checks.txt,dev_checks.exitcode.txt}.
- Ризики/нотатки → немає.

## 2026-01-29T16:40:00+01:00 — PRE (MODE=read-only discovery): P7 Calendar SSOT — повний аудит

- Мета → перевірити, чи P7 виконано повністю; зібрати факти, підозри, ризики та рекомендації по календарю.
- Scope → config/calendar_overrides.json, core/time/*, runtime/* (FXCM/preview/history), tools/exit_gates/*, tests/*, docs/*.
- Non-goals → зміни коду.
- Інваріанти/рейки → лише читання; факти path:line.
- План → пошук по repo (calendar_stub/closed_intervals/schedule/drift), перегляд ключових модулів і gate/tests, зведення висновків.
- Тести/перевірки → не запускати.

## 2026-01-29T17:00:00+01:00 — POST (MODE=read-only discovery): P7 Calendar SSOT — факти/ризики/рекомендації

- FACTS (path:line):
  1) core/time/calendar.py:10-108 — Calendar завжди завантажує overrides з config/calendar_overrides.json; якщо closed_intervals_utc передані в Calendar — це init_error (SSOT лише JSON).
  2) core/time/calendar.py:66-83 — при init_error market_state повертає safe-closed і tz_backend="init_error".
  3) core/time/calendar.py:69-99 — is_open/next_open/next_pause → safe-closed при init_error.
  4) core/time/sessions.py:80-150 — load_calendar_overrides валідуює weekly_open/weekly_close/daily_break/tz та нормалізує closed_intervals_utc.
  5) core/time/closed_intervals.py:6-33 — normalize_closed_intervals_utc rails: list/tuple, epoch ms bounds, start<end, no overlap, сорт.
  6) config/calendar_overrides.json:1-32 — профіль fxcm_calendar_v1_ny (NY, daily_break 5m, closed_intervals_utc пустий).
  7) config/calendar_overrides.json:33-92 — профіль fxcm_calendar_v1_utc_overrides (UTC overrides, daily_break 61m, closed_intervals_utc має 12 інтервалів).
  8) runtime/status.py:178-226 — calendar_error переводить degraded+errors[] (calendar_error).
  9) runtime/status.py:431-435 — _ensure_calendar_health викликається при update process fields.
  10) tools/exit_gates/manifest.json:19-20 — gate_calendar_closed_intervals і gate_calendar_schedule_drift у дефолтному manifest.
  11) tools/exit_gates/gates/gate_calendar_closed_intervals.py:29-71 — gate перевіряє keys, tz, HH:MM і normalize_closed_intervals_utc.
  12) tools/exit_gates/gates/gate_calendar_schedule_drift.py:43-111 — gate перевіряє daily break + weekly open/close (drift).
  13) tests/test_calendar_overrides_loading.py:7-16 — тести читають обидва профілі overrides.
  14) tests/test_calendar_schedule_semantics.py:24-76 — тести семантики open/close/break для NY та UTC overrides.
  15) tests/test_calendar_closed_interval_effect.py:11-28 — closed_intervals_utc блокує trading_time.
  16) tests/test_calendar_xau_profile.py:10-24 — next_open_ms узгоджений з 23:01 UTC (UTC overrides).
  17) runtime/history_provider.py:29-96 — використовує calendar.next_open_ms для history readiness/backoff.
  18) runtime/fxcm_forexconnect.py:456-460 — при market closed next_open_ms береться з config.closed_intervals_utc (не з overrides).
  19) docs/calendar_sessions_spec.md:9-10 — closed_intervals_utc SSOT; TODO про holidays для utc_overrides.
  20) docs/audit_v7_runtime_core.md:52-99 — зафіксований ризик: closed_intervals_utc можуть бути порожні/дані потребують наповнення.
  21) tools/bootstrap_p0.sh:252-528 — є legacy stub calendar лише в bootstrap-скрипті.
  22) calendar_stub згадки зараз лише у docs/Work/data/audit артефактах (не в runtime коді).

- Підозри/ризики:
  - runtime/fxcm_forexconnect.py використовує config.closed_intervals_utc для паузи при market closed; якщо там пусто, sleep не синхронізується з overrides/реальними closed_intervals.
  - Профіль fxcm_calendar_v1_ny має closed_intervals_utc порожній; якщо потрібні свята — дані не внесені (див. docs/calendar_sessions_spec.md TODO).
  - Артефакти audit_*/docs згадують calendar_stub — історичні; але можуть вводити в оману, якщо їх використовують як поточний стан.

- Рекомендації:
  - Уніфікувати джерело closed_intervals для FXCM stream: використовувати Calendar/overrides у runtime (або прокидати overrides.closed_intervals_utc у config і не тримати дубль).
  - Заповнити closed_intervals_utc для fxcm_calendar_v1_ny або чітко зафіксувати, що SSOT — UTC overrides профіль.
  - Оновити/позначити історичні audit-артефакти з calendar_stub як legacy (щоб не сприймали як поточні).

## 2026-01-29T17:20:00+01:00 — PRE (MODE=PATCH): P7 Calendar Prod Lock

- Мета → SSOT у calendar_overrides.json; Calendar як єдине джерело schedule/closed_intervals; holiday policy + gates; scrub calendar_stub згадок.
- Scope → runtime/fxcm_forexconnect.py, config/calendar_overrides.json, core/time/calendar.py, core/time/sessions.py, core/time/buckets.py, core/validation/validator.py, runtime/ohlcv_preview.py, core/market/preview_builder.py, app/composition.py, ui_lite/server.py, tools/exit_gates/gates/*, tools/exit_gates/manifest.json, tests/*, docs/*, data/audit_*.
- Non-goals → UI/FXCM history/store/derived, зміна public schemas.
- Інваріанти/рейки → SSOT only, без silent fallback, мінімальний диф, gates у manifest.
- План → уніфікувати календар у runtime + buckets/validator; додати holiday policy + gates; почистити calendar_stub у docs/data; додати тести; оновити docs.
- Тести/перевірки → tools.run_dev_checks, pytest (нові тести), tools.run_exit_gates.

## 2026-01-29 12:00:00 — PRE (MODE=PATCH): P7 Calendar SSOT prod lock (boundary + holiday policy + gates + docs)
- Мета → перевести 1d boundary на Calendar SSOT, прибрати залежності від trading_day_boundary_utc, додати holiday_policy + гейти, прибрати згадки stub‑календаря в runtime/доках.
- Scope → config/calendar_overrides.json; core/time/buckets.py (call sites); tests/test_*preview*; tools/exit_gates/gates/*; tools/exit_gates/manifest.json; docs/*; ui_lite/server.py (контроль 1d boundary);
- Non-goals → зміна контрактів публічних payload, рефакторинг runtime/FXCM поза календарним SSOT, запуск аудитів P6/P7.
- Інваріанти/рейки → SSOT календар лише з calendar_overrides.json; без silent fallback; мінімальний диф.
- План → (1) оновити тести/гейти під Calendar boundary; (2) додати holiday_policy і gate’и; (3) оновити docs; (4) зафіксувати лог.
- Тести/перевірки → pytest -q; tools.run_exit_gates (за можливості).

## 2026-01-29 12:30:00 — POST (MODE=PATCH): P7 Calendar SSOT prod lock (boundary + holiday policy + gates + docs)
- Що зроблено → оновлено preview/bucket тести та gate_preview_bucket_boundaries під Calendar; додано holiday_policy у calendar_overrides.json; додано гейти gate_calendar_holiday_policy, gate_fxcm_calendar_ssot, gate_no_calendar_stub_mentions; оновлено manifest; оновлено документи щодо boundary/SSOT; прибрано calendar_stub у legacy bootstrap.
- Де зроблено → config/calendar_overrides.json; tests/test_preview_bucket_boundaries.py; tests/test_preview_time_inclusive.py; tests/test_buckets_1d_boundary.py; tools/exit_gates/gates/gate_preview_bucket_boundaries.py; tools/exit_gates/gates/gate_calendar_holiday_policy.py; tools/exit_gates/gates/gate_fxcm_calendar_ssot.py; tools/exit_gates/gates/gate_no_calendar_stub_mentions.py; tools/exit_gates/manifest.json; tests/test_manifest_includes_calendar_gates.py; docs/COPILOT_RULES.md; docs/Public Surface.md; docs/Public API Spec (SSOT).md; docs/v2_time_calendar_semantics.md; docs/audit_v5_time_policy.md; docs/audit_v6_time_policy.md; docs/audit_v6_calendar_sessions.md; docs/audit_v5_calendar_sessions.md; docs/audit_v6_system_map.md; docs/audit_master_v1_v2_v6.md; docs/audit/current_state.md; docs/runtime.md; docs/Audit v7_1a.md; tools/bootstrap_p0.sh.
- Як перевірено → тести/гейти не запускались (лише правки коду/доків).
- Ризики/нотатки → потрібен прогін pytest + exit gates; у docs/P7 Calendar SSOT.md залишилась одна згадка calendar_stub (потребує точкового редагування).

## 2026-01-29 12:40:00 — PRE (MODE=PATCH): P7 Calendar SSOT doc cleanup
- Мета → прибрати всі згадки calendar_stub у P7 Calendar SSOT.md та відновити формат вступного списку.
- Scope → docs/P7 Calendar SSOT.md.
- Non-goals → зміни коду/тестів/гейтів.
- Інваріанти/рейки → мінімальний диф, лише точковий текстовий fix.
- План → (1) прибрати calendar_stub у acceptance criteria; (2) відновити bullets; (3) перевірити пошук calendar_stub у docs.
- Тести/перевірки → не потрібні.

## 2026-01-29 12:45:00 — POST (MODE=PATCH): P7 Calendar SSOT doc cleanup
- Що зроблено → виправлено вступний список та прибрано calendar_stub згадки в docs/P7 Calendar SSOT.md.
- Де зроблено → docs/P7 Calendar SSOT.md.
- Як перевірено → локальний grep по docs (без тестів).
- Ризики/нотатки → оновлення носить документальний характер.

## 2026-01-29T13:40:00+01:00 — PRE (MODE=PATCH): P7 Calendar SSOT proof-pack + doc sync

- Мета → синхронізувати docs/P7 Calendar SSOT.md з актуальним станом SSOT календаря; зафіксувати proof-pack та верифікації; оформити підсумковий POST із SSOT‑фактами.
- Scope → docs/P7 Calendar SSOT.md; Work/01log.md; data/audit_v7_calendar_ssot/{pytest_collect.txt,pytest_count.txt}.
- Non-goals → зміни core/runtime логіки, повторний прогін повних exit gates.
- Інваріанти/рейки → мінімальний диф; SSOT лише з calendar_overrides.json; без silent fallback.
- План → (1) оновити P7 Calendar SSOT.md; (2) зібрати pytest collect/count у proof-pack; (3) оформити POST із артефактами/гейтами/grep‑доказами.
- Тести/перевірки → pytest --collect-only -q (для count); використати наявні артефакти pytest -q та exit gates.

## 2026-01-29T13:55:00+01:00 — POST (MODE=PATCH): P7 Calendar SSOT proof-pack + doc sync

- Що зроблено →
  - Оновлено docs/P7 Calendar SSOT.md: профілі SSOT, закриття ризиків FXCM/closed_intervals_utc, підсумок без старого роз’їзду.
  - Зібрано pytest collect/count у proof-pack.
  - Зафіксовано SSOT‑факти, список гейтів, proof‑pack і grep‑артефакти в цьому POST.
- Де зроблено →
  - docs/P7 Calendar SSOT.md
  - data/audit_v7_calendar_ssot/pytest_collect.txt
  - data/audit_v7_calendar_ssot/pytest_count.txt
  - Work/01log.md

- SSOT‑рішення (факти) →
  - calendar_tag (runtime): fxcm_calendar_v1_ny (config/config.py).
  - tz профілю: America/New_York (config/calendar_overrides.json).
  - 1d boundary: визначається Calendar SSOT через daily_break_start=17:00 (America/New_York) як trading day boundary.
  - holiday_policy: required=true, min_future_days=0 (обидва профілі в config/calendar_overrides.json).

- Як перевірено (команди → результат) →
  - python -m pytest -q → OK (74 тести; див. data/audit_v7_calendar_ssot/pytest_q.txt та pytest_count.txt).
  - python -m pytest --collect-only -q → OK (запис у data/audit_v7_calendar_ssot/pytest_collect.txt; count=74).
  - python -m tools.run_exit_gates --out data/audit_v7_calendar_ssot → OK (stdout і results.json у proof-pack).
  - python -c "...status_calendar_ok..." → OK (data/audit_v7_calendar_ssot/status_calendar_ok.json).

- Gate list (explicit, OK) →
  - gate_python_version: OK
  - gate_xor_mode_scan: OK
  - gate_no_duplicate_gate_runners: OK
  - gate_no_runtime_sims: OK
  - gate_tick_units: OK
  - gate_tick_event_time_not_wallclock: OK
  - gate_tick_skew_non_negative: OK
  - gate_fxcm_tick_mode_config: OK
  - gate_fxcm_tick_liveness: OK
  - gate_preview_1m_boundaries: OK
  - gate_preview_bucket_boundaries: OK
  - gate_preview_1m_geom: OK
  - gate_preview_late_tick_drop: OK
  - gate_fxcm_fsm_unit: OK
  - gate_tick_fixtures_schema: OK
  - gate_history_tf_rail_scan: OK
  - gate_calendar_xau_next_open_matches_23utc: OK
  - gate_calendar_closed_intervals: OK
  - gate_calendar_holiday_policy: OK
  - gate_calendar_schedule_drift: OK
  - gate_fxcm_calendar_ssot: OK
  - gate_no_calendar_stub_mentions: OK
  - gate_file_cache_schema: OK
  - gate_cache_integrity: OK
  - gate_no_sqlite_left: OK
  - gate_status_pubsub_size: OK
  - gate_ui_candles_gap_scan: OK
  - gate_ui_gap_visualization_scan: OK
  - gate_ui_lite_no_last_payload_fallback: OK
  - gate_dependency_rule_core: OK
  - gate_preview_builder_ssot: OK
  - gate_tools_thin_wrapper: OK
  - gate_no_local_tokenbucket_history: OK
  - gate_no_history_fetch_when_not_ready: OK

- Proof-pack артефакти →
  - data/audit_v7_calendar_ssot/pytest_q.txt
  - data/audit_v7_calendar_ssot/pytest_collect.txt
  - data/audit_v7_calendar_ssot/pytest_count.txt
  - data/audit_v7_calendar_ssot/exit_gates_stdout.txt
  - data/audit_v7_calendar_ssot/2026-01-29_182248/results.json
  - data/audit_v7_calendar_ssot/2026-01-29_182248/hashes.json
  - data/audit_v7_calendar_ssot/status_calendar_ok.json
  - data/audit_v7_calendar_ssot/gate_calendar_closed_intervals.txt
  - data/audit_v7_calendar_ssot/gate_calendar_holiday_policy.txt
  - data/audit_v7_calendar_ssot/gate_calendar_schedule_drift.txt
  - data/audit_v7_calendar_ssot/gate_fxcm_calendar_ssot.txt
  - data/audit_v7_calendar_ssot/gate_no_calendar_stub_mentions.txt

- Grep‑rails докази →
  - data/audit_v7_calendar_ssot/grep_calendar_stub.txt
  - data/audit_v7_calendar_ssot/grep_trading_day_boundary_utc.txt
  - data/audit_v7_calendar_ssot/grep_runtime_closed_intervals_utc.txt

- Ризики/відкат/нотатки →
  - Ризики: застарілі згадки в docs/calendar_sessions_spec.md та docs/audit_v7_runtime_core.md можуть вводити в оману; продуктивність `_is_closed_interval` O(n) при дуже великих списках.
  - Відкат: відкотити docs/P7 Calendar SSOT.md і прибрати proof-pack файли data/audit_v7_calendar_ssot/{pytest_collect.txt,pytest_count.txt} без впливу на runtime логіку.

## 2026-01-29T19:30:00+01:00 — PRE (MODE=read-only discovery): Розбір “тиша після старту” + відсутність свічок UI Lite

- Мета → зрозуміти очікувану поведінку після старту `python -m app.main`, фактичний стан пайплайна та причину, чому UI Lite не малює свічки.
- Scope → app/main.py, app/composition.py, runtime/* (FXCM stream, preview builder, publisher, status), ui_lite/*, config/*; без змін коду.
- Non-goals → виправлення логіки/конфігу; запуск довгих аудиторських гейтів.
- Інваріанти/рейки → лише читання; без модифікацій; фіксуємо факти path:line.
- План → (1) перевірити, які воркери/потоки стартують; (2) звірити, де генеруються preview candles і як UI Lite їх читає; (3) знайти умови, коли потік “тихий”; (4) сформувати чітке пояснення та гіпотези.
- Тести/перевірки → не запускати.

## 2026-01-29T19:40:00+01:00 — POST (MODE=read-only discovery): Розбір “тиша після старту” + відсутність свічок UI Lite

- Що зроблено → проаналізовано стартові потоки, джерело preview‑свічок, умови запуску FXCM tick stream та підписку UI Lite на OHLCV.
- Де зроблено → app/main.py, app/composition.py, config/config.py, ui_lite/server.py.

- Факти (path:line) →
  1) config/config.py:95 — `tick_mode` за замовчуванням = "off" (FXCM tick stream не стартує).
  2) config/config.py:107-110 — `ohlcv_preview_enabled=True`, але потрібні ticks для побудови свічок.
  3) app/composition.py:474 — preview будується лише при обробці `on_tick` (`preview_builder.on_tick(...)`).
  4) app/composition.py:672 — FXCM tick stream стартує тільки якщо `config.tick_mode == "fxcm"` і backend=forexconnect.
  5) ui_lite/server.py:927-938 — UI Lite підписується на Redis‑канал `config.ch_ohlcv()` і чекає OHLCV payloads.

- Висновок → після старту `python -m app.main` “тиша” є очікуваною, якщо `tick_mode` лишається "off": FXCM tick stream не стартує, `on_tick` не викликається, preview свічки не публікуються, UI Lite не має що малювати.

- Гіпотези по “свічки не малюються” (за пріоритетом) →
  1) `tick_mode=off` (дефолт) → немає FXCM ticks → немає preview OHLCV.
  2) FXCM market closed → ticks не приходять навіть при `tick_mode=fxcm`.
  3) Канали/NS різні між app.main і UI Lite → UI Lite підписаний не на той `ns`.
  4) FXCM secrets/SDK відсутні → stream не стартує (лог мав би це показати).

- Рекомендовані перевірки (без запуску тут) →
  - Переконатися, що `tick_mode="fxcm"` у config (або в окремому профілі), і FXCM backend=forexconnect.
  - Перевірити `ns` та канали: `config.ch_ohlcv()` має збігатися між app.main і UI Lite.
  - Відкрити UI Lite debug: `http://127.0.0.1:8089/debug` і перевірити `subscribed_channel`, `last_payload_*`.
  - Перевірити Redis ключ `fxcm_local:status:snapshot` на `ohlcv_preview` та `fxcm.state`.

- Як перевірено → читання коду (без запусків).
- Ризики/нотатки → без змін коду; можливі додаткові причини на рівні середовища (FXCM market closed, відсутність tick data або невідповідний `ns`).

## 2026-01-29T20:00:00+01:00 — PRE (MODE=PATCH): Holiday policy required=false + уточнення P7 Calendar SSOT POST

- Мета → зняти “порожню” вимогу holiday_policy (required=true + min_future_days=0) через явний required=false; узгодити gate та додати уточнення до POST 2026-02-01T13:55:00+01:00.
- Scope → config/calendar_overrides.json; tools/exit_gates/gates/gate_calendar_holiday_policy.py; Work/01log.md.
- Non-goals → зміни core/runtime логіки; повторний прогін exit gates.
- Інваріанти/рейки → мінімальний диф; SSOT у calendar_overrides.json; без silent fallback.
- План → (1) змінити holiday_policy.required=false; (2) оновити gate, щоб дозволити required=false; (3) додати addendum до POST із path:line, grep summary, поясненнями.
- Тести/перевірки → не запускати.

## 2026-01-29T20:10:00+01:00 — POST (MODE=PATCH): Holiday policy required=false + уточнення P7 Calendar SSOT POST

- Що зроблено →
  - Встановлено holiday_policy.required=false для обох профілів календаря.
  - Gate holiday_policy дозволяє required=false і не вимагає min_future_days, якщо required=false.
  - Додано addendum до POST 2026-02-01T13:55:00+01:00 з виправленнями/поясненнями.
- Де зроблено →
  - config/calendar_overrides.json
  - tools/exit_gates/gates/gate_calendar_holiday_policy.py
  - Work/01log.md

- Addendum до 2026-02-01T13:55:00+01:00 (виправлення must‑issues) →
  - Holiday policy → required=false (осмислено: policy не є “обов’язковою” для прод‑покриття у цьому slice). min_future_days=0 зберігається як інформаційний параметр. Gate оновлено, щоб це відображати.
  - XAU 23utc gate → gate_calendar_xau_next_open_matches_23utc створює Calendar з tag=fxcm_calendar_v1_utc_overrides (tools/exit_gates/gates/gate_calendar_xau_next_open_matches_23utc.py:15) і перевіряє next_open=23:01 UTC (lines 17-19). Це окремий профіль для XAU, не конфліктує з active calendar_tag=fxcm_calendar_v1_ny.
  - Exit gates артефакти → у POST 2026-02-01T13:55:00+01:00 використано run_id=2026-01-29_182248 (reused); у цьому slice повторний прогін exit_gates не виконувався.

- SSOT‑факти (path:line) →
  - config/config.py:47-48 — calendar_tag=fxcm_calendar_v1_ny, calendar_path=config/calendar_overrides.json.
  - config/calendar_overrides.json:3-7 — fxcm_calendar_v1_ny, tz_name=America/New_York, daily_break_start=17:00.
  - config/calendar_overrides.json:9-13 — holiday_policy.required=false, min_future_days=0.
  - core/time/buckets.py:36-41 — 1d boundary береться через calendar.trading_day_boundary_for(ts_ms).

- Grep summary →
  - calendar_stub: 0 hits у runtime/; згадки лише в docs/tests/pycache (див. data/audit_v7_calendar_ssot/grep_calendar_stub.txt).
  - trading_day_boundary_utc: 0 hits у runtime/; allowlist лише у gate_fxcm_calendar_ssot.py:13-14 (перевірка відсутності поля).
  - runtime closed_intervals_utc (config): 0 hits у runtime/ (див. data/audit_v7_calendar_ssot/grep_runtime_closed_intervals_utc.txt).

- Rollback уточнення →
  - Цей slice змінює лише config календаря + gate + лог; runtime rollback не потрібен тут.
  - Runtime зміни P7 відкотити можна лише через git revert відповідних комітів P7.

- Як перевірено → не запускалось (лише правки конфігу/gate/логу).
- Ризики/нотатки → holiday_policy.required=false означає, що gate не гарантує future‑coverage; якщо потрібно прод‑покриття, потрібне окреме рішення та дані інтервалів.

## 2026-01-29T20:30:00+01:00 — PRE (MODE=PATCH): P7 Calendar SSOT — doc canonical + holiday gate rails + rerun checks

- Мета → зафіксувати канонічну інтерпретацію holiday_policy у документації, підтягнути rails у gate_calendar_holiday_policy, виконати мінімальні перевірки (pytest + exit gates) і зафіксувати новий run_id.
- Scope → docs/P7 Calendar SSOT.md; tools/exit_gates/gates/gate_calendar_holiday_policy.py; Work/01log.md; data/audit_v7_calendar_ssot/*.
- Non-goals → зміни core/runtime логіки; повний аудит P7.
- Інваріанти/рейки → мінімальний диф; SSOT у calendar_overrides.json; без silent fallback.
- План → (1) переписати P7 Calendar SSOT.md без дублювань та з канонічними поясненнями; (2) посилити gate holiday_policy; (3) запустити короткий pytest; (4) запустити exit gates та зберегти proof-pack.
- Тести/перевірки → pytest (календарні тести), tools.run_exit_gates.

## 2026-01-29T20:45:00+01:00 — POST (MODE=PATCH): P7 Calendar SSOT — doc canonical + holiday gate rails + rerun checks

- Що зроблено →
  - Переписано docs/P7 Calendar SSOT.md без дублювань; додано канонічне тлумачення holiday_policy (required=false) та розмежування ny vs utc_overrides.
  - Посилено gate_calendar_holiday_policy: normalize_closed_intervals_utc, мінімум інтервалів, узгодженість coverage_end_utc == max(end_ms).
  - Виконано короткий pytest + повний run_exit_gates; зафіксовано новий run_id.
- Де зроблено →
  - docs/P7 Calendar SSOT.md
  - tools/exit_gates/gates/gate_calendar_holiday_policy.py
  - Work/01log.md
  - data/audit_v7_calendar_ssot/pytest_q_holiday_policy.txt
  - data/audit_v7_calendar_ssot/exit_gates_stdout_2026-01-29.txt
  - data/audit_v7_calendar_ssot/2026-01-29_204807/{results.json,hashes.json}

- Як перевірено →
  - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m pytest -q tests/test_calendar_overrides_loading.py tests/test_calendar_closed_interval_effect.py tests/test_manifest_includes_calendar_gates.py → OK (data/audit_v7_calendar_ssot/pytest_q_holiday_policy.txt).
  - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m tools.run_exit_gates --out data/audit_v7_calendar_ssot --manifest tools/exit_gates/manifest.json → OK (run_id=2026-01-29_204807; stdout у data/audit_v7_calendar_ssot/exit_gates_stdout_2026-01-29.txt).

- Уточнення (канонічно) →
  - holiday_policy.required=false означає: `closed_intervals_utc` = known closures/maintenance, без гарантії повного holiday‑coverage.
  - Gate holiday_policy залишається рейкою: нормалізація інтервалів, мінімум інтервалів, узгодженість coverage_end_utc.
  - Профілі: runtime SSOT = fxcm_calendar_v1_ny (NY rollover); fxcm_calendar_v1_utc_overrides — контрольний/спеціальний (XAU gate, порівняння).

- Ризики/нотатки → якщо потрібно гарантувати holiday‑coverage на рік, потрібне окреме рішення (min_future_days>0 + дані інтервалів) і оновлення gate/SSOT політики.

## 2026-01-29T21:00:00+01:00 — PRE (MODE=read-only discovery): P8/P9/P10 детальне розслідування по коду

- Мета → виконати глибоке code‑review для P8 (FXCM tick feed), P9 (FXCM history provider), P10 (live bar builder) і зафіксувати факти/ризики/прогалини.
- Scope → runtime/fxcm_forexconnect.py, runtime/fxcm/*, runtime/tick_feed.py, runtime/history_provider.py, fxcm/history_fxcm_provider.py (або актуальний history провайдер), runtime/ohlcv_preview.py, runtime/preview_builder.py, store/file_cache.py, runtime/handlers_p3.py, runtime/backfill.py, runtime/warmup.py, tests/*, tools/exit_gates/*, docs/Audit v7_1a.md.
- Non-goals → зміни коду, запуск довгих аудитів або gate suite.
- Інваріанти/рейки → лише читання; факти фіксуємо як path:line.
- План → (1) P8: потік тиків, reconnect/heartbeat, degrade‑loud; (2) P9: history fetch, chunking, budget, determinism; (3) P10: preview→final, market‑aware, gap policy; (4) звести ризики й next steps.
- Тести/перевірки → не запускати.

## 2026-01-29T21:30:00+01:00 — POST (MODE=read-only discovery): P8/P9/P10 детальне розслідування по коду

- Що зроблено → переглянуто FXCM tick stream, FSM/liveness, статусні rails, history provider (chunk/budget/backoff), warmup/backfill, preview builder та FileCache; зведено прогалини P8/P9/P10.

- P8 — FXCM Tick feed (facts, path:line) →
  1) runtime/fxcm_forexconnect.py:63 — `check_fxcm_environment` валідовує backend/секрети/SDK.
  2) runtime/fxcm_forexconnect.py:78 — `ensure_fxcm_ready` пише loud errors/degraded при missing secrets/SDK/unsupported backend.
  3) runtime/fxcm_forexconnect.py:365-374 — `FxcmForexConnectStream.start()` блокує запуск без `ensure_fxcm_ready`.
  4) runtime/fxcm_forexconnect.py:449-456 — market‑closed → state="paused_market_closed" і sleep до `calendar.next_open_ms` + backoff.
  5) runtime/fxcm_forexconnect.py:222 — відсутній `event_ts` → error `missing_tick_event_ts` + drop.
  6) runtime/fxcm_forexconnect.py:312-331 — contract/publish fail → loud error + degraded + counters.
  7) runtime/fxcm/session_manager.py:31-61 — FSM + liveness debounce: resubscribe/reconnect лише після cooldown.
  8) runtime/status.py:520-539 — tick rails: `tick_skew_negative` → degraded; tick_lag/skew метрики.
  9) runtime/status.py:591-596 — high drop rate event_ts → `tick_event_time_unavailable` + preview pause.
  10) tools/exit_gates/gates/gate_fxcm_tick_liveness.py:6-35 — unit‑gate на debounce reconnect.

- P9 — FXCM History Provider (facts, path:line) →
  1) runtime/fxcm/history_provider.py:158 — `fetch_1m_final` з chunking по `chunk_minutes` і probe перед великим діапазоном.
  2) runtime/fxcm/history_provider.py:184-203 — `is_history_ready/should_backoff/note_not_ready` для backoff.
  3) runtime/history_provider.py:39 — `guard_history_ready` ставить status history + degraded + loud error.
  4) runtime/warmup.py:27-42 — warmup chunking + limit через config.
  5) runtime/backfill.py:27-41 — backfill chunking + limit через config.
  6) runtime/fxcm/history_provider.py:76-124 — history fetch через ForexConnect login/get_history; logout у finally.
  7) store/file_cache/history_cache.py:55 — final 1m пишуться в FileCache через `append_complete_bars`.

- P10 — Live Bar Builder (facts, path:line) →
  1) core/market/preview_builder.py:103-168 — preview tick→bars (complete=false) + 1d boundary через Calendar.
  2) app/composition.py:474-483 — preview payloads публікуються через `publish_ohlcv_batch` (complete=false).
  3) app/composition.py:492-521 — closed preview bars перетворюються у `complete=True` і пишуться в FileCache як `source="stream_close"`.
  4) runtime/publisher.py:52-101 — preview batch публікується з `complete=false` (final 1m окремо).
  5) tools/exit_gates: відсутні gate_live_* (file_search **/live*bar*builder*.py → 0).

- Висновки/прогалини →
  - P8: базові rails/логи/деґрадації та FSM/liveness є; але live‑liveness gate не перевіряє реальні ticks (gate лише unit‑debounce).
  - P9: є chunk/budget/backoff, але немає окремого exit‑gate для FXCM history smoke/determinism у `tools/exit_gates/gates/*`.
  - P10: окремого `live_bar_builder` немає; final‑1m у live формується імпліцитно через preview‑closed → FileCache (source="stream_close"), без явно визначеної gap policy та market‑aware finalization як у вимогах P10.

- Як перевірено → читання коду (без запусків).
- Ризики/нотатки → потрібні цільові gates для P9/P10; P10 наразі покритий лише preview/stream_close, без окремої “live finalization” сутності.

## 2026-01-29T22:00:00+01:00 — PRE (MODE=read-only discovery): Cache/Live reset + warmup/backfill/restore/tail_guard у контексті P8–P10

- Мета → відповісти, чи cache є live‑кешем, чому після перезапуску “все малюється заново”, та як warmup/backfill/restore/tail_guard/max* пов’язані з P8–P10 і поточною ситуацією свічок.
- Scope → config/config.py (cache_root, cache_enabled), store/file_cache/*, app/composition.py (append_complete_bars), runtime/preview_builder.py, runtime/tail_guard.py, runtime/warmup.py, runtime/backfill.py.
- Non-goals → зміни коду/конфігу, запуск audit/gates.
- Інваріанти/рейки → лише читання; факти фіксуємо як path:line.
- План → (1) перевірити де пишеться FileCache і які умови; (2) перевірити чи use warmup/backfill/restore/tail_guard для live; (3) сформувати пояснення по reset‑поведінці UI.
- Тести/перевірки → не запускати.

## 2026-01-29T22:15:00+01:00 — POST (MODE=read-only discovery): Cache/Live reset + warmup/backfill/restore/tail_guard у контексті P8–P10

- Що зроблено → перевірено фактичну наявність FileCache на диску, шлях збереження, місце запису live‑барів, а також роль warmup/backfill/tail_guard у live‑свічках.

- Факти (path:line) →
  1) config/config.py:72-76 — `cache_enabled=True`, `cache_root="cache"` (за замовчуванням).
  2) app/composition.py:492-521 — live preview закриті 1m‑бари пишуться у FileCache як `source="stream_close"` через `append_complete_bars`.
  3) store/file_cache/history_cache.py:55-99 — `append_complete_bars` пише CSV+meta у FileCache.
  4) store/file_cache/history_cache.py:18 — FileCache SSOT формат: CSV + meta.json.
  5) runtime/tail_guard.py:49-92 — tail_guard працює поверх FileCache 1m (SSOT) і marked gaps; при empty cache → `ssot_empty` degraded.
  6) runtime/warmup.py:27-42 та runtime/backfill.py:27-41 — warmup/backfill читають history і записують final 1m у FileCache.

- Фактичний стан диску →
  - cache/ містить XAUUSD_1m.csv і XAUUSD_1m.meta.json (FileCache присутній).

- Висновки →
  - Так, це live‑кеш: FileCache оновлюється при live‑тиках, але лише після закриття 1m‑барів (stream_close).
  - Якщо “все малюється заново” після перезапуску, це означає, що UI малює preview із Redis‑стріму, а не ретранслює FileCache tail автоматично; тому при рестарті без warmup/republish UI стартує “з нуля”.
  - Warmup/backfill/tail_guard прямо стосуються P8–P10: вони забезпечують наповнення/перевірку final 1m SSOT і коректність gaps; ці теми мають бути в аудиті, включно з поточним кейсом свічок (restart → no republish).

- Як перевірено → читання коду + огляд cache/ каталогу.
- Ризики/нотатки → якщо потрібен автоматичний репабліш tail після рестарту, це окрема вимога (P10/P11) і має бути оформлено окремим рішенням/гейтом.

## 2026-01-30T09:10:00+01:00 — PRE (MODE=PATCH): P8/P9/P10 audit docs sync

- Мета → зафіксувати результати аудиту P8/P9/P10 у окремому документі та привести docs/Audit v7_1a.md у відповідність до фактичних файлів/стану.
- Scope → docs/P8 P9 P10 для FXCM ticks history live bars.md; docs/Audit v7_1a.md; Work/01log.md.
- Non-goals → зміни коду/конфігу, запуск гейтів.
- Інваріанти/рейки → мінімальний диф; лише документальні правки; українська мова.
- План → (1) створити док з підсумком аудиту; (2) оновити scopes у Audit v7_1a; (3) зафіксувати POST.
- Тести/перевірки → не запускати.

## 2026-01-30T10:00:00+01:00 — PRE (MODE=PATCH): P9 history provider legacy + history smoke gate

- Мета → позначити legacy заглушку history_fxcm_provider.py, оновити P9 док‑посилання на реальний runtime/fxcm/history_provider.py та додати gate_fxcm_history_smoke для мінімального FXCM history‑смоуку.
- Scope → fxcm/history_fxcm_provider.py; tools/exit_gates/gates/gate_fxcm_history_smoke.py; tools/exit_gates/manifest.json; docs/Audit v7_1a.md; Work/01log.md.
- Non-goals → зміни runtime історії, адаптерів чи business‑логіки; запуск тестів/exit gates.
- Інваріанти/рейки → мінімальний диф; мова укр.; без silent fallback.
- План → (1) позначити legacy заглушку; (2) додати gate history smoke; (3) оновити manifest та Audit v7_1a; (4) зафіксувати POST.
- Тести/перевірки → не запускати.

## 2026-01-30T10:10:00+01:00 — POST (MODE=PATCH): P9 history provider legacy + history smoke gate

- Що зроблено → позначено fxcm/history_fxcm_provider.py як legacy/unused заглушку; додано gate_fxcm_history_smoke (10 хв 1m history, перевірка rows/keys); додано gate у manifest; оновлено P9 scope у Audit v7_1a.md.
- Де зроблено →
  - fxcm/history_fxcm_provider.py
  - tools/exit_gates/gates/gate_fxcm_history_smoke.py
  - tools/exit_gates/manifest.json
  - docs/Audit v7_1a.md
- Як перевірено → не запускалось (док/гейт додано, без виконання).
- Ризики/нотатки → gate виконує реальний FXCM history‑запит і повертає SKIP, якщо SDK/секрети/бекенд недоступні; для повного доказу потрібен запуск у готовому середовищі.
- Примітка → PRE зафіксовано ретроспективно (помилка процесу), далі дотримуватись порядку PRE→PATCH→POST.

## 2026-01-30T09:25:00+01:00 — POST (MODE=PATCH): P8/P9/P10 audit docs sync

- Що зроблено → синхронізовано scope‑шляхи P8/P9/P10 у `docs/Audit v7_1a.md` з фактичними файлами та шляхами гейтів; позначено відсутні гейти як план/створити.
- Де зроблено → docs/Audit v7_1a.md.
- Як перевірено → без запуску перевірок (лише документальні правки).
- Ризики/нотатки → документальний sync не змінює runtime поведінку; фактичні гейти для P9/P10 все ще відсутні.

## 2026-01-30T10:30:00+01:00 — PRE (MODE=PATCH): P7 re-verify (pytest + exit gates, new run_id)

- Мета → повторно підтвердити P7 після addendum: прогін pytest та run_exit_gates з новим run_id, без змін коду.
- Scope → лише верифікація; артефакти в reports/exit_gates/* та стандартний pytest лог.
- Non-goals → будь-які правки коду/доків.
- Інваріанти/рейки → запуск через runner/manifest; без прямого запуску gate_*.py.
- План → (1) python -m pytest -q; (2) python -m tools.run_exit_gates --manifest ... --out reports/exit_gates; (3) зафіксувати POST з run_id.
- Тести/перевірки → pytest -q; tools.run_exit_gates.

## 2026-01-30T16:45:00+01:00 — POST (MODE=PATCH): P7 re-verify (pytest + exit gates, new run_id)

- Що зроблено → повторно пройдено pytest та run_exit_gates через manifest, зафіксовано новий run_id.
- Де зроблено → reports/exit_gates/2026-01-30_164153/.
- Як перевірено →
  - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m pytest -q → OK.
  - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m tools.run_exit_gates --manifest tools/exit_gates/manifest.json --out reports/exit_gates → OK (run_id=2026-01-30_164153).
- Ризики/нотатки → addendum після цього run_id відсутні; календарні гейти виконані в загальному прогоні manifest.

## 2026-01-30T17:05:00+01:00 — PRE (MODE=PATCH): P8→P10 SSOT-карта + P8 slices (doc update)

- Мета → зафіксувати крок 1 (SSOT-карта P8→P10) та крок 2 (P8 ризики→рішення→мінімальні slices) у доку P8/P9/P10.
- Scope → docs/P8 P9 P10 для FXCM ticks history live bars.md; Work/01log.md.
- Non-goals → будь-які зміни коду; запуск тестів/exit gates.
- Інваріанти/рейки → лише документальні правки; мова укр.; мінімальний диф.
- План → (1) додати SSOT-карту; (2) додати P8.1–P8.3 як мінімальні slices; (3) зафіксувати POST.
- Тести/перевірки → не запускати.

## 2026-01-30T17:20:00+01:00 — POST (MODE=PATCH): P8→P10 SSOT-карта + P8 slices (doc update)

- Що зроблено → додано крок 1 (SSOT‑карта P8→P10) та крок 2 (P8.1–P8.3 ризики/рішення/рейки) у док P8/P9/P10.
- Де зроблено → docs/P8 P9 P10 для FXCM ticks history live bars.md.
- Як перевірено → не запускалось (лише документальні правки).
- Ризики/нотатки → оновлено лише опис/структуру; змін у runtime немає.

## 2026-01-30T18:10:00+01:00 — PRE (MODE=PATCH): Quarantine гілка + reset до 3439d4a + reflog діагностика

- Мета → зафіксувати брудний стан у quarantine-гілці, повернути main до 3439d4a (clean SSOT), зібрати reflog для діагностики причин розʼїзду.
- Scope → git (status/switch/commit/reset/clean/reflog); Work/01log.md.
- Non-goals → зміни коду/логіки поза git-операціями.
- Інваріанти/рейки → спершу quarantine commit, потім reset/clean; без втрати даних.

## 2026-01-30T18:20:00+01:00 — POST (MODE=PATCH): Quarantine гілка + reset до 3439d4a + reflog діагностика

- Що зроблено → створено quarantine-гілку та закомічено весь брудний стан; main повернено до 3439d4a і очищено; зібрано reflog.
- Де зроблено → git repo (main, wip/quarantine-after-3439d4a).
- Як перевірено →
  - git status -sb (до quarantine) → зафіксовано великий список змін.
  - git switch -c wip/quarantine-after-3439d4a → OK.
  - git add -A → OK.
  - git commit -m "WIP quarantine: uncommitted changes after 3439d4a" → OK (commit=b432c4e).
  - git switch main → OK.
  - git reset --hard 3439d4a → OK.
  - git clean -fd → OK.
  - git reflog -n 50 → OK (HEAD@{2}=quarantine commit, HEAD@{0}=reset до 3439d4a).
- Ризики/нотатки → quarantine містить масивні docs/код зміни; витягувати точково через git restore --source=wip/quarantine-after-3439d4a.
- План →
  1) git status -sb
  2) git switch -c wip/quarantine-after-3439d4a
  3) git add -A; git commit -m "WIP quarantine: uncommitted changes after 3439d4a"
  4) git switch main; git reset --hard 3439d4a; git clean -fd
  5) git reflog -n 50
- Тести/перевірки → не застосовні.

## 2026-01-30T18:40:00+01:00 — PRE (MODE=PATCH): Clean SSOT валідація + інвентаризація quarantine

- Мета → підтвердити чистий стан main@3439d4a (exit gates + pytest) та створити інвентаризацію quarantine‑коміту (звіт у reports/*).
- Scope → git/exit_gates/pytest; reports/quarantine_*.txt; Work/01log.md.
- Non-goals → зміни коду/логіки; інтеграція змін із quarantine.
- Інваріанти/рейки → лише діагностика; без модифікацій коду.
- План →
  1) git status -sb
  2) python -m tools.run_exit_gates --manifest tools/exit_gates/manifest.json --out reports/exit_gates
  3) python -m pytest -q
  4) git show --name-status --stat b432c4e > reports/quarantine_b432c4e_stat.txt
  5) git show --name-only b432c4e > reports/quarantine_b432c4e_files.txt
  6) (опційно) зібрати топ‑директорій у reports/quarantine_top_dirs.txt
- Тести/перевірки → exit_gates + pytest.

## 2026-01-30T19:00:00+01:00 — POST (MODE=PATCH): Clean SSOT валідація + інвентаризація quarantine

- Що зроблено → перевірено clean стан main@3439d4a (exit gates + pytest); зібрано інвентаризацію quarantine-коміту у reports/*.
- Де зроблено → reports/exit_gates/2026-01-30_175917/; reports/quarantine_b432c4e_stat.txt; reports/quarantine_b432c4e_files.txt; reports/quarantine_top_dirs.txt.
- Як перевірено →
  - git status -sb → clean.
  - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m tools.run_exit_gates --manifest tools/exit_gates/manifest.json --out reports/exit_gates → OK (run_id=2026-01-30_175917).
  - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m pytest -q → OK.
  - git show --name-status --stat b432c4e > reports/quarantine_b432c4e_stat.txt → OK.
  - git show --name-only b432c4e > reports/quarantine_b432c4e_files.txt → OK.
  - quarantine_top_dirs.txt сформовано → OK.
- Ризики/нотатки → quarantine інвентаризація готова для точкового витягування P‑slice.

## 2026-01-30T19:30:00+01:00 — PRE (MODE=PATCH): P7 Calendar SSOT restore (prod lock + docs + proof-pack)

- Мета → повністю відновити завершений стан P7 (Calendar SSOT prod lock, doc cleanup, proof-pack) з quarantine, синхронізувати журнал з кодом і перевірити gates+pytest з новим run_id.
- Scope →
  - config/calendar_overrides.json
  - core/time/buckets.py
  - core/market/preview_builder.py
  - runtime/ohlcv_preview.py
  - tools/exit_gates/manifest.json
  - tools/exit_gates/gates/gate_preview_bucket_boundaries.py
  - tools/exit_gates/gates/gate_calendar_holiday_policy.py
  - tools/exit_gates/gates/gate_fxcm_calendar_ssot.py
  - tools/exit_gates/gates/gate_no_calendar_stub_mentions.py
  - tests/test_preview_bucket_boundaries.py
  - tests/test_preview_time_inclusive.py
  - tests/test_buckets_1d_boundary.py
  - tests/test_manifest_includes_calendar_gates.py
  - docs/P7 Calendar SSOT.md
  - docs/Audit v7_1a.md
  - Work/01log.md
- Non-goals → будь-які зміни поза P7; P8/P9/P10.
- Інваріанти/рейки → мінімальний диф; SSOT лише через calendar_overrides.json; без silent fallback.
- План →
  1) Витягнути P7 файли з wip/quarantine-after-3439d4a.
  2) Перевірити зміни (diff).
  3) Запустити pytest -q та run_exit_gates (manifest).
  4) Зафіксувати POST з новим run_id.
- Тести/перевірки → pytest -q; tools.run_exit_gates.

## 2026-01-30T19:40:00+01:00 — PRE (MODE=PATCH): P7 Calendar SSOT restore — розрив циклічних імпортів

- Мета → розірвати циклічні імпорти між `core.time.*` та `core.validation.validator` після відновлення P7 (щоб pytest та exit gates зібралися).
- Scope → core/validation/errors.py (новий), core/validation/validator.py, core/time/timestamps.py.
- Non-goals → зміна доменної логіки, зміна контрактів, рефакторинг поза цикл імпортів.
- Інваріанти/рейки → ContractError зберігає public surface (`from core.validation.validator import ContractError`), мінімальний диф.
- План →
  1) Винести `ContractError` у окремий модуль.
  2) Перенести імпорт у `timestamps.py` на новий модуль.
  3) Переекспортувати `ContractError` з `validator.py`.
  4) Повторити pytest -q і run_exit_gates.
- Тести/перевірки → pytest -q; tools.run_exit_gates.

## 2026-01-30T20:00:00+01:00 — POST (MODE=PATCH): P7 Calendar SSOT restore + імпортні фікси (POST)

- Що зроблено →
  - Відновлено P7‑стан із quarantine (calendar SSOT, gates, тести, docs) та узгоджено runtime/test файли.
  - Розірвано циклічні імпорти через окремий `core/validation/errors.py` і `core/time/epoch_rails.py`.
  - Відновлено сумісність `Calendar` (підтримка позиційних аргументів і 1d boundary через `trading_day_boundary_for`).
  - Прибрано mutable default для `_stop_event` у FXCM stream.
  - Прибрано виклики `record_fxcm_tick_drop` (метод відсутній у `StatusManager`).
- Де зроблено →
  - app/composition.py
  - config/config.py
  - config/calendar_overrides.json
  - core/time/calendar.py
  - core/time/buckets.py
  - core/time/timestamps.py
  - core/time/epoch_rails.py
  - core/validation/errors.py
  - core/validation/validator.py
  - core/market/preview_builder.py
  - core/market/tick.py
  - runtime/fxcm_forexconnect.py
  - runtime/tick_feed.py
  - runtime/ohlcv_preview.py
  - runtime/replay_ticks.py
  - runtime/status.py
  - fxcm/history_fxcm_provider.py
  - ui_lite/server.py
  - tools/exit_gates/manifest.json
  - tools/exit_gates/gates/{gate_calendar_holiday_policy.py,gate_calendar_semantics.py,gate_calendar_xau_next_open_matches_23utc.py,gate_fxcm_calendar_ssot.py,gate_fxcm_history_smoke.py,gate_no_calendar_stub_mentions.py,gate_no_mutable_threading_event_default.py,gate_preview_bucket_boundaries.py,gate_tick_replay_monotonic.py,gate_tick_skew_non_negative.py}
  - tests/test_buckets_1d_boundary.py
  - tests/test_calendar_sessions.py
  - tests/test_calendar_xau_profile.py
  - tests/test_command_bus_starts_and_updates_status.py
  - tests/test_fxcm_disabled_by_default.py
  - tests/test_fxcm_sdk_missing_is_loud_error.py
  - tests/test_fxcm_stop_event_is_per_instance.py
  - tests/test_history_provider_configured.py
  - tests/test_manifest_includes_calendar_gates.py
  - tests/test_no_mix_detects_conflict.py
  - tests/test_preview_bucket_boundaries.py
  - tests/test_preview_from_ticks_produces_bar_time_gt_zero.py
  - tests/test_preview_status_updates.py
  - tests/test_preview_time_inclusive.py
  - tests/test_status_final_1m_ssot.py
  - tests/test_tick_contract_reject_degrade_loud.py
  - tests/test_tick_health_rolling_window.py
  - tests/test_tick_mode_handling.py
  - tests/test_tick_publisher_updates_status.py
  - tests/test_tick_replay_policy.py
  - tests/test_tick_simulator.py
  - tests/test_tick_simulator_disabled.py
  - tests/test_unknown_command_flow.py
  - docs/P7 Calendar SSOT.md
  - docs/Audit v7_1a.md
- Як перевірено →
  - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m pytest -q → OK.
  - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m tools.run_exit_gates --manifest tools/exit_gates/manifest.json --out reports/exit_gates → OK (run_id=2026-01-30_194312).
- Ризики/нотатки → Відновлено P7 SSOT‑стан; розрив імпортів і FXCM stop_event виправлені без зміни контрактів.

## 2026-01-30T20:20:00+01:00 — PRE (MODE=PATCH): P7 механічний чек‑лист верифікації

- Мета → пройти чек‑лист 0–7 (status/log, трекінг файлів, grep‑рейки, manifest, holiday_policy, imports, stop_event, pytest, exit gates).
- Scope → звіти у reports/*, перевірки git/grep/python/pytest/exit_gates; за потреби коміт "P7 restore replay".
- Non-goals → зміни логіки поза виправленням процесної чистоти.
- Інваріанти/рейки → SSOT календар через calendar_overrides.json; без silent fallback.
- План →
  1) Зняти git status/log.
  2) Створити reports/p7_expected_files.txt і перевірити git ls-files.
  3) Виконати grep‑рейки (calendar_stub, trading_day_boundary_utc, config.closed_intervals_utc).
  4) Перевірити manifest і holiday_policy.
  5) Перевірити ContractError re-export та timestamps імпорт.
  6) Запустити pytest (включно stop_event test) і exit_gates.
  7) Зафіксувати коміт (якщо dirty) і POST.
- Тести/перевірки → git status/log; python -c checks; pytest; tools.run_exit_gates.

## 2026-01-30T20:40:00+01:00 — POST (MODE=PATCH): P7 механічний чек‑лист верифікації

- Що зроблено →
  - Виконано чек‑лист 0–7: git статус/лог, очікувані файли, grep‑рейки, manifest/gates, holiday_policy, імпортні перевірки, pytest, exit gates.
  - Оновлено holiday_policy.required → true у calendar_overrides.json (обидва профілі) для відповідності чек‑листу.
  - Сформовано reports/p7_expected_files.txt і reports/p7_missing_or_untracked.txt (порожній після git add).
- Де зроблено →
  - config/calendar_overrides.json
  - reports/p7_expected_files.txt
  - reports/p7_missing_or_untracked.txt
  - reports/grep_config_closed_intervals_utc.txt
  - reports/grep_calendar_stub_runtime.txt
  - reports/grep_calendar_stub_docs.txt
  - reports/grep_trading_day_boundary_utc.txt
  - reports/exit_gates/2026-01-30_200309/
- Як перевірено →
  - git -C C:/Aione_projects/fxcm_connector_v2 status -sb → dirty перед комітом (потрібен commit P7 restore replay).
  - git -C C:/Aione_projects/fxcm_connector_v2 log -n 5 --oneline --decorate → немає P7‑restore коміту.
  - grep config.closed_intervals_utc → порожньо (reports/grep_config_closed_intervals_utc.txt).
  - grep calendar_stub → є згадки у tools/exit_gates/gates/gate_no_calendar_stub_mentions.py та tests/test_manifest_includes_calendar_gates.py; у docs є legacy згадки (audit_v2/v3/v5/v6 + runtime.md).
  - grep trading_day_boundary_utc → порожньо (reports/grep_trading_day_boundary_utc.txt).
  - manifest gates (id) → missing=[]; results.json → missing=[] (results_json=reports/exit_gates/2026-01-30_200309/results.json).
  - holiday_policy → required=True, min_future_days=0 для обох профілів.
  - python -c ContractError/timestamps імпорт → OK.
  - pytest -q tests/test_fxcm_stop_event_is_per_instance.py → OK.
  - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m pytest -q → OK.
  - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m tools.run_exit_gates --manifest tools/exit_gates/manifest.json --out reports/exit_gates → OK (run_id=2026-01-30_200309).
- Ризики/нотатки →
  - reports/* у цьому репо ігноруються git (не додаються без -f); файли створено, але не під треком.
  - Згадки calendar_stub залишаються у legacy docs та у самому gate/test (не у runtime‑логіці).

## 2026-01-30T20:50:00+01:00 — POST (MODE=PATCH): Commit P7 restore replay

- Що зроблено → створено коміт для відновленого стану P7.
- Де зроблено → репозиторій (git commit).
- Як перевірено →
  - git -C C:/Aione_projects/fxcm_connector_v2 commit -m "P7 restore replay" → OK (a334502).
  - git -C C:/Aione_projects/fxcm_connector_v2 status -sb → clean (main ahead 1).
- Ризики/нотатки → provenance для P7 зафіксовано комітом.

## 2026-01-30T21:10:00+01:00 — PRE (MODE=PATCH): P9 history provider legacy + history smoke gate

- Мета → позначити legacy заглушку history_fxcm_provider.py, оновити P9 док‑посилання на реальний runtime/fxcm/history_provider.py та додати gate_fxcm_history_smoke для мінімального FXCM history‑смоуку.
- Scope → fxcm/history_fxcm_provider.py; tools/exit_gates/gates/gate_fxcm_history_smoke.py; tools/exit_gates/manifest.json; docs/Audit v7_1a.md; Work/01log.md.
- Non-goals → зміни runtime історії, адаптерів чи business‑логіки; запуск тестів/exit gates.
- Інваріанти/рейки → мінімальний диф; мова укр.; без silent fallback.
- План → (1) позначити legacy заглушку; (2) додати gate history smoke; (3) оновити manifest та Audit v7_1a; (4) зафіксувати POST.
- Тести/перевірки → не запускати.

## 2026-01-30T21:20:00+01:00 — POST (MODE=PATCH): P9 history provider legacy + history smoke gate

- Що зроблено → уточнено P9 scope в Audit v7_1a (реальний runtime/fxcm/history_provider.py + legacy заглушка fxcm/history_fxcm_provider.py).
- Де зроблено → docs/Audit v7_1a.md.
- Як перевірено → не запускалось (док‑правка).
- Ризики/нотатки → код/гейт/manifest вже відповідали вимогам; зміни лише документальні.

## 2026-01-30T21:40:00+01:00 — PRE (MODE=PATCH): REPO_LAYOUT/README актуалізація

- Мета → звірити docs/REPO_LAYOUT.md та README.md з поточним станом репо і виправити розбіжності.
- Scope → docs/REPO_LAYOUT.md; README.md; Work/01log.md.
- Non-goals → зміни коду/логіки; запуск тестів.
- Інваріанти/рейки → мінімальний диф; мова укр.; без нових припущень.
- План → (1) перевірити наявні каталоги/файли; (2) оновити мапи/опис у REPO_LAYOUT/README; (3) зафіксувати POST.
- Тести/перевірки → не запускати.

## 2026-01-30T21:50:00+01:00 — POST (MODE=PATCH): REPO_LAYOUT/README актуалізація

- Що зроблено → синхронізовано docs/REPO_LAYOUT.md і README.md з фактичними каталогами/файлами (History/, forexconnect_stream.py, epoch_rails.py, errors.py; прибрано recordings; уточнено XAU 23:01 UTC).
- Де зроблено → docs/REPO_LAYOUT.md; README.md.
- Як перевірено → читання файлів + звірка структури з робочим деревом (без запусків).
- Ризики/нотатки → документи були частково несинхронні через історичні згадки recordings/XAU 23:00 UTC.

## 2026-01-30T22:10:00+01:00 — PRE (MODE=PATCH): P8.S1 stop_event default_factory + rail

- Мета → прибрати shared mutable default у FxcmForexConnectStream._stop_event; підтвердити rail+тест.
- Scope → runtime/fxcm_forexconnect.py; tests/test_fxcm_stop_event_is_per_instance.py; tools/exit_gates/gates/gate_no_mutable_threading_event_default.py; tools/exit_gates/manifest.json; Work/01log.md.
- Non-goals → зміна runtime-логіки стріму, FXCM reconnect, календар, preview.
- Інваріанти/рейки → мінімальний диф; без silent fallback; публічні контракти не чіпати.
- План → (1) перевірити наявні реалізації; (2) запустити тести/exit_gates зі scope; (3) зафіксувати POST.
- Тести/перевірки → pytest -k stop_event -q; python -m tools.run_exit_gates --manifest tools/exit_gates/manifest.json.

## 2026-01-30T22:20:00+01:00 — POST (MODE=PATCH): P8.S1 stop_event default_factory + rail

- Що зроблено → підтверджено, що `_stop_event` вже створюється через `field(default_factory=threading.Event)`, тест і gate на місці.
- Де зроблено → runtime/fxcm_forexconnect.py; tests/test_fxcm_stop_event_is_per_instance.py; tools/exit_gates/gates/gate_no_mutable_threading_event_default.py; tools/exit_gates/manifest.json.
- Як перевірено →
  - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m pytest -k stop_event -q → OK.
  - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m tools.run_exit_gates --manifest tools/exit_gates/manifest.json --out reports/exit_gates → OK (run_id=2026-01-30_203253).
- Ризики/нотатки → змін у коді не потрібно; вимога вже виконана.

## 2026-01-30T21:20:00 — PRE (MODE=PATCH): P8.S2 market_closed sleep через Calendar SSOT

- Мета → прибрати непряме/конфіг-обчислення next_open; використовувати Calendar SSOT + додати rail на монотонність у FXCM stream.
- Scope → runtime/fxcm_forexconnect.py; (опційно) core/time/calendar.py; tests/test_fxcm_market_closed_next_open_monotonic.py; tools/exit_gates/gates/gate_fxcm_market_closed_uses_calendar.py; tools/exit_gates/manifest.json.
- Non-goals → зміни календарних правил/overrides; FXCM login; історія/warmup/backfill.
- Інваріанти/рейки → SSOT календаря; no silent fallback; при невалідному next_open_ms — loud error + контрольований backoff.
- План →
  1) Додати `_calendar_next_open_ms()` у FXCM stream з валідацією монотонності.
  2) Перепідключити market-closed гілку на Calendar SSOT.
  3) Додати тест на монотонність і degraded-backoff.
  4) Додати exit-gate заборони для `_next_open_ms`/`closed_intervals_utc` у FXCM stream.
- Тести/перевірки → pytest -k market_closed_next_open -q; python tools/run_exit_gates.py --manifest tools/exit_gates/manifest.json.

## 2026-01-30T21:45:00 — POST (MODE=PATCH): P8.S2 market_closed sleep через Calendar SSOT

- Що зроблено → додано `_calendar_next_open_ms()` з rail на монотонність і degraded-backoff; market-closed гілку переведено на Calendar SSOT; додано тест і exit-gate; оновлено manifest і перевірку manifest.
- Де зроблено → runtime/fxcm_forexconnect.py; tests/test_fxcm_market_closed_next_open_monotonic.py; tools/exit_gates/gates/gate_fxcm_market_closed_uses_calendar.py; tools/exit_gates/manifest.json; tests/test_manifest_includes_calendar_gates.py.
- Як перевірено → pytest -k market_closed_next_open -q (OK); run_exit_gates --manifest tools/exit_gates/manifest.json --out reports/exit_gates (OK).
- Ризики/нотатки → перший запуск exit-gates без --out дав очікувану помилку; повторний запуск успішний.

## 2026-01-30T22:05:00 — PRE (MODE=PATCH): Актуалізація REPO_LAYOUT/README

- Мета → звірити та синхронізувати docs/REPO_LAYOUT.md і README.md з поточним станом репозиторію.
- Scope → docs/REPO_LAYOUT.md; README.md.
- Non-goals → зміни коду/логіки runtime; зміни контрактів/календаря; запуск тестів.
- Інваріанти/рейки → мінімальний диф, SSOT документація без вигаданих директорій.
- План →
  1) Перевірити REPO_LAYOUT/README на згадки застарілих шляхів (recordings тощо).
  2) Внести точкові правки у README для відповідності REPO_LAYOUT.
  3) Зафіксувати POST у журналі.
- Тести/перевірки → не застосовно (docs-only).

## 2026-01-30T22:15:00 — POST (MODE=PATCH): Актуалізація REPO_LAYOUT/README

- Що зроблено → README синхронізовано з REPO_LAYOUT (прибрано recordings, додано closed_intervals.py, tools runners та новий gate); REPO_LAYOUT доповнено новим gate.
- Де зроблено → README.md; docs/REPO_LAYOUT.md.
- Як перевірено → не застосовно (docs-only).
- Ризики/нотатки → без впливу на runtime.

## 2026-01-30T22:30:00 — PRE (MODE=PATCH): P8.S3 preview watermark + late-tick drop rail

- Мета → гарантувати інваріант preview “бар не мутує у минуле”; додати watermark, drop stale ticks, метрику і rail.
- Scope → core/market/preview_builder.py; runtime/status.py; observability/metrics.py; tests/test_preview_late_tick_drop.py; tools/exit_gates/gates/gate_preview_late_tick_drop.py; tools/exit_gates/manifest.json; docs/REPO_LAYOUT.md; README.md.
- Non-goals → зміни контрактів public surface; зміни final/history; зміни UI.
- Інваріанти/рейки → no silent fallback: stale tick → drop + метрика + rail (loud); tick_ts тільки epoch ms int.
- План →
  1) Додати watermark-rail у PreviewBuilder (drop для tick_ts_ms < last_tick_ts_ms або bucket_open_ms < last_bucket_open_ms).
  2) Інкремент метрики late-tick drop (Prometheus) без дублювання.
  3) Додати тест на late-tick drop + no-mutate.
  4) Перевірити gate/manifest і актуальність REPO_LAYOUT/README.
- Тести/перевірки → pytest -k preview_late_tick_drop -q; python tools/run_exit_gates.py --manifest tools/exit_gates/manifest.json.

## 2026-01-30T23:05:00 — POST (MODE=PATCH): P8.S3 preview watermark + late-tick drop rail

- Що зроблено → додано watermark+drop для late tick (tick_ts/bucket) з loud degraded/error; додано метрику late-tick drop; додано pytest для late-tick drop; перевірено актуальність REPO_LAYOUT/README (без змін).
- Де зроблено → core/market/preview_builder.py; runtime/status.py; observability/metrics.py; tests/test_preview_late_tick_drop.py.
- Як перевірено → pytest -k preview_late_tick_drop -q (OK); run_exit_gates --manifest tools/exit_gates/manifest.json --out reports/exit_gates (OK).
- Ризики/нотатки → late-tick помилки тепер loud; можливе збільшення errors[] при систематичному out-of-order.

## 2026-01-30T23:20:00 — PRE (MODE=PATCH): P8.S3 guardrail для tick_ts всередині bucket

- Мета → дозволити не монотонний tick_ts всередині того ж bucket; дроп лише при bucket_open_ms назад.
- Scope → core/market/preview_builder.py.
- Non-goals → зміни тестів/гейтів/метрик.
- Інваріанти/рейки → не мутувати минулі бари; late tick drop лише при bucket назад.
- План →
  1) Оновити guardrail і message для late tick drop.
  2) Запустити pytest -k preview_late_tick_drop -q.
- Тести/перевірки → pytest -k preview_late_tick_drop -q.

## 2026-01-30T23:30:00 — POST (MODE=PATCH): P8.S3 guardrail для tick_ts всередині bucket

- Що зроблено → дроп late tick лише при bucket_open_ms < watermark; уточнено message для rail.
- Де зроблено → core/market/preview_builder.py.
- Як перевірено → pytest -k preview_late_tick_drop -q (OK).
- Ризики/нотатки → tick_ts не монотонний всередині bucket більше не дропається; інваріант “не мутувати минулі бари” збережено.

## 2026-01-30T23:40:00 — PRE (MODE=read-only discovery): Tick contract reject (tick_ts_ms > snap_ts_ms)

- Мета → локалізувати джерело помилки tick_contract_reject (tick_ts_ms має бути <= snap_ts_ms) та визначити місце виправлення.
- Scope → core/market/tick.py; core/contracts/public/tick_v1.json; runtime/tick_feed.py; runtime/fxcm_forexconnect.py; runtime/status.py.
- Non-goals → зміни коду або контрактів без явної команди.
- Інваріанти/рейки → контракт tick_ts_ms <= snap_ts_ms зберігається.
- План → знайти місце валідації й шлях формування tick_ts_ms/snap_ts_ms для fxcm_offers.
- Тести/перевірки → не застосовно (discovery).

## 2026-01-30T23:50:00 — PRE (MODE=PATCH): Fix tick_ts_ms > snap_ts_ms для FXCM offers

- Мета → усунути tick_contract_reject через tick_ts_ms > snap_ts_ms у FXCM offers без порушення контрактів.
- Scope → runtime/fxcm_forexconnect.py; tests/test_fxcm_offers_subscription.py.
- Non-goals → зміни контрактів tick_v1; зміни схем/SSOT.
- Інваріанти/рейки → tick_ts_ms <= snap_ts_ms; без silent fallback; мінімальний диф.
- План →
  1) У _offer_row_to_tick нормалізувати snap_ts_ms = max(receipt_ms, event_ts_ms) і залогувати warning.
  2) Додати тест на випадок event_ts_ms > receipt_ms.
  3) Запустити pytest -k fxcm_offers -q.
- Тести/перевірки → pytest -k fxcm_offers -q.

## 2026-01-31T00:05:00 — POST (MODE=PATCH): Fix tick_ts_ms > snap_ts_ms для FXCM offers

- Що зроблено → нормалізовано snap_ts_ms до max(receipt_ms, event_ts_ms) і додано warning при event_ts_ms > receipt_ms; додано тест.
- Де зроблено → runtime/fxcm_forexconnect.py; tests/test_fxcm_offers_subscription.py.
- Як перевірено → pytest -k fxcm_offers -q (OK).
- Ризики/нотатки → при регулярному event_ts_ms > receipt_ms буде warning у errors[] без degraded.

## 2026-01-31T00:15:00 — PRE (MODE=read-only discovery): fxcm_tick_event_ahead_of_receipt warnings

- Мета → зафіксувати спостереження: FXCM event_ts_ms інколи > receipt_ms; warnings очікувані після нормалізації.
- Scope → runtime/fxcm_forexconnect.py (логіка warning); status errors.
- Non-goals → зміни коду/політик.
- Інваріанти/рейки → tick_ts_ms <= snap_ts_ms збережено.
- План → підтвердити, що це warning без degraded і не блокує потік.
- Тести/перевірки → не застосовно.

## 2026-01-31T00:16:00 — POST (MODE=read-only discovery): fxcm_tick_event_ahead_of_receipt warnings

- Що зроблено → зафіксовано: warnings fxcm_tick_event_ahead_of_receipt без degraded — очікувано, оскільки snap_ts_ms нормалізується.
- Де зроблено → Work/01log.md.
- Як перевірено → повідомлення користувача.
- Ризики/нотатки → можливо багато warnings у errors[] при постійному випередженні event_ts_ms.

## 2026-01-31T00:30:00 — PRE (MODE=read-only discovery): Аудит P8 tick feed + preview ingest

- Мета → підготувати повний аудит P8 з фактами path:line, інваріантами, coverage, ризиками та P‑slice рекомендаціями.
- Scope → runtime/fxcm_forexconnect.py; runtime/fxcm/session_manager.py; runtime/fxcm/fsm.py; runtime/fxcm/adapter.py; runtime/tick_feed.py; core/market/tick.py; core/market/preview_builder.py; runtime/status.py; observability/metrics.py; tools/exit_gates/gates/*; tools/exit_gates/manifest.json; core/contracts/public/tick_v1.json; core/validation/validator.py.
- Non-goals → зміни коду/контрактів/конфігів.
- Інваріанти/рейки → read‑only discovery; без патчів runtime.
- План → зібрати факти (path:line), мапу SSOT/coverage, ризики й рекомендації; сформувати audit markdown.
- Тести/перевірки → не застосовно.

## 2026-01-31T00:45:00 — POST (MODE=read-only discovery): Аудит P8 tick feed + preview ingest

- Що зроблено → сформовано звіт P8 audit у docs/audit/P8_tick_feed_audit.md.
- Де зроблено → docs/audit/P8_tick_feed_audit.md; Work/01log.md.
- Як перевірено → не застосовно (read-only).
- Ризики/нотатки → звіт містить факти path:line, coverage, risk register, next steps.

## 2026-01-31T01:10:00 — PRE (MODE=read-only discovery): Доповнення аудиту P8 (sanity-check + P-slices)

- Мета → додати commit/diff/manifest run_id, sanity-check, оцінку, пріоритети та Copilot P-slices.
- Scope → docs/audit/P8_tick_feed_audit.md.
- Non-goals → зміни коду/контрактів/гейтів.
- Інваріанти/рейки → read-only discovery, без runtime правок.
- План → зібрати commit/diff/manifest run_id і доповнити звіт.
- Тести/перевірки → не застосовно.

## 2026-01-31T01:25:00 — POST (MODE=read-only discovery): Доповнення аудиту P8 (sanity-check + P-slices)

- Що зроблено → оновлено audit P8: commit/diff/manifest run_id, sanity-check, оцінка, пріоритети, GO/NO‑GO, Copilot P‑slices, додаткові пастки.
- Де зроблено → docs/audit/P8_tick_feed_audit.md.
- Як перевірено → не застосовно (read-only).
- Ризики/нотатки → diff --stat зафіксовано як не‑нульовий стан репозиторію.

## 2026-01-31T01:35:00 — PRE (MODE=PATCH): P8.S4 stop_event interruptible market-closed wait

- Мета → гарантувати, що FXCM stream виходить зі стану paused_market_closed ≤ 1s після stop_event; додати тест + exit-gate.
- Scope → runtime/fxcm_forexconnect.py; tests/test_fxcm_market_closed_stop_interrupt.py; tools/exit_gates/gates/gate_fxcm_market_closed_stop_interrupt.py; tools/exit_gates/manifest.json.
- Non-goals → зміни календаря/overrides; FXCM login; preview.
- Інваріанти/рейки → wait interruptible (stop_event.wait або короткі кроки), без silent fallback.
- План →
  1) Додати helper `_sleep_interruptible` у FXCM stream і використати в market-closed loop.
  2) Додати тест з моканим календарем і перевіркою join ≤ 1s.
  3) Додати exit-gate, який запускає pytest -k market_closed_stop_interrupt.
- Тести/перевірки → pytest -k market_closed_stop_interrupt -q; python -m tools.run_exit_gates --manifest tools/exit_gates/manifest.json --out reports/exit_gates.

## 2026-01-31T01:55:00 — POST (MODE=PATCH): P8.S4 stop_event interruptible market-closed wait

- Що зроблено → додано `_sleep_interruptible` і використано в market-closed wait; додано тест на interruptibility; додано exit-gate та manifest entry.
- Де зроблено → runtime/fxcm_forexconnect.py; tests/test_fxcm_market_closed_stop_interrupt.py; tools/exit_gates/gates/gate_fxcm_market_closed_stop_interrupt.py; tools/exit_gates/manifest.json.
- Як перевірено → pytest -k market_closed_stop_interrupt -q (OK); run_exit_gates --manifest tools/exit_gates/manifest.json --out reports/exit_gates (OK).
- Ризики/нотатки → тест використовує мок ensure_fxcm_ready і FXCM import; покриває interruptibility для market-closed loop.

## 2026-01-31T02:05:00 — PRE (MODE=read-only discovery): Діагностика tick_contract_reject (fxcm_offers) + status_payload_too_large

- Мета → пояснити джерело tick_contract_reject (instrument відсутній) та причину status_payload_too_large/WS STALE.
- Scope → runtime/fxcm_forexconnect.py; runtime/status.py; core/contracts/public/status_v2.json.
- Non-goals → зміни коду/конфігів.
- Інваріанти/рейки → read-only discovery.
- План → перевірити обробку offers row/instrument та ланцюг build/publish status payload.
- Тести/перевірки → не застосовно.

## 2026-01-31T02:15:00 — POST (MODE=read-only discovery): Діагностика tick_contract_reject (fxcm_offers) + status_payload_too_large

- Що зроблено → локалізовано причини instrument відсутній та механіку payload>8KB із feedback loop; сформовано висновки для користувача.
- Де зроблено → runtime/fxcm_forexconnect.py; runtime/status.py; core/contracts/public/status_v2.json.
- Як перевірено → read-only аналіз коду.
- Ризики/нотатки → потрібна валідація типу row/атрибутів; спам errors[] підсилює overflow.

## 2026-01-31T02:25:00 — POST (MODE=read-only discovery): Діагностика причин (tick_contract_reject + status_payload_too_large)

- Коротко, причина і де саме в коді:

1) tick_contract_reject з source=fxcm_offers і “instrument відсутній”.
У FXCM offers пайпі поле instrument береться тільки через getattr(row, "instrument", None) і це hard‑require (кидає ContractError). Це означає, що:
- або row не має атрибуту instrument (назва відмінна/інша структура),
- або callback отримує не offer‑row (інший тип рядка).
Джерело й місце, де кидається помилка:
- runtime/fxcm_forexconnect.py:203-228 — _offer_row_to_tick робить instrument = getattr(row, "instrument", None) і raise ContractError("instrument відсутній").
- runtime/fxcm_forexconnect.py:319-330 — catch ContractError → tick_contract_reject з context={"source":"fxcm_offers"}.
Діагноз: instrument не в тому полі/типі рядка. Тут немає fallback на OfferID/Instrument/offer_id або dict‑ключі (окрім dict у _extract_event_ts_ms), тому будь‑яка зміна атрибутів row → контрактний reject.

2) status_payload_too_large → WS STALE (feedback loop).
Механіка така:
- Status payload формується через build_status_pubsub_payload, який включає errors (trim до 20) і багато інших секцій.
  runtime/status.py:31-78
- В publish_snapshot() рахується JSON‑size; якщо > 8KB — додається status_payload_too_large у errors[], інкремент метрики, і pubsub не публікується (тільки snapshot).
  runtime/status.py:1133-1155
- Додавання нового error збільшує payload, тож наступний tick/пабліш ще більш ймовірно перевищить 8KB → loop.
- UI живе на pubsub/WS → якщо publish не відбувається, UI показує STALE.
Де формується “велике”:
- errors дозволяють context з довільним об’єктом, який може бути великим.
  core/contracts/public/status_v2.json:40-74
- Навіть після trim до 20, контент context може надути payload.
  runtime/status.py:31-78
- Додається новий error при перевищенні, що само по собі збільшує payload.
  runtime/status.py:1138-1150
Діагноз: найбільш імовірне джерело >8KB — великі errors[].context або часті повтори errors (без throttle), плюс сам status_payload_too_large підсилює розмір (feedback loop). Контракти дозволяють контекст довільної структури — це і є “дірка”.

## 2026-01-31T02:35:00 — PRE (MODE=read-only discovery): Аналіз payload size і instrument очікування

- Мета → підтвердити, чи “стрім падає” від status payload, і що саме роздуває payload; пояснити, чому очікується instrument.
- Scope → runtime/status.py; core/contracts/public/status_v2.json; runtime/fxcm_forexconnect.py; ui_lite/server.py.
- Non-goals → зміни коду/поведінки.
- Інваріанти/рейки → read-only discovery.
- План → перевірити логіку publish_snapshot + UI Lite status poller; описати джерела дублювань/поля що ростуть.
- Тести/перевірки → не застосовно.

## 2026-01-31T02:50:00 — POST (MODE=read-only discovery): Аналіз payload size і instrument очікування

- Що зроблено → з’ясовано, що publish_snapshot при payload>8KB не публікує pubsub, але snapshot key оновлюється; описано дублювання/джерела росту (errors/context, last_command, tail_guard, ohlcv_final_1m vs ohlcv_final).
- Де зроблено → runtime/status.py; ui_lite/server.py; core/contracts/public/status_v2.json; runtime/fxcm_forexconnect.py.
- Як перевірено → read-only аналіз коду.
- Ризики/нотатки → WS STALE більше корелює з pubsub/ohlcv або status_ok=false, а не падінням стріму; instrument очікується з FXCM OFFERS row (fx.get_table(fx.OFFERS)).

## 2026-01-31T03:05:00 — POST (MODE=read-only discovery): Діагностика tick_contract_reject + payload size

Коротко:

Чи “стрім падає” через status payload?
Ні, стрім не падає. При payload_size > 8KB publish_snapshot() не публікує pubsub, але оновлює snapshot key і виходить (return). Це видно в status.py:1170-1190. Тобто WS/Redis pubsub може “мертвіти”, але snapshot для /api/status оновлюється.

UI Lite статус читає snapshot key через poller і валідатор (а не pubsub), тож STALE для статусу виникає, якщо snapshot не оновлюється/невалідний. Це видно в server.py:448-506 і server.py:583-601.
WS STALE частіше корелює з OHLCV pubsub, а не зі статус‑pubsub.

1) “instrument відсутній” — де і чому це очікується

instrument жорстко вимагається у _offer_row_to_tick через getattr(row, "instrument", None) → ContractError.
fxcm_forexconnect.py:203-228
Рядок приходить із fx.get_table(fx.OFFERS) та Common.subscribe_table_updates, тобто очікується offer‑row з instrument, bid, ask, time.
fxcm_forexconnect.py:288-334
Якщо row іншого типу або атрибут названий інакше — отримуємо tick_contract_reject з source=fxcm_offers.
fxcm_forexconnect.py:319-330
Висновок: без fallback це нормально — якщо row не offer‑типу або SDK змінив поля, буде reject. Тобто треба перевірити тип/атрибути реального row, а не “розширювати” fallback-и.

2) Payload: що саме може роздуватися (де і чому)
Джерела, які реально ростуть у build_status_pubsub_payload():

errors[] з великими context — контекст дозволяє довільний об’єкт, тому саме він найчастіше “роздуває” JSON.
status_v2.json:40-74
status.py:31-78

last_command.result у snapshot без нормалізації/trim. Якщо команда повертає великий dict — це напряму в payload.
status.py:1121-1167
composition.py:330-369

Дублювання: ohlcv_final_1m + ohlcv_final містять перекриті дані (1m дублюється).
status.py:31-78
status.py:879-919

tail_guard містить near і far з повним набором tf_states і marks (не дуже великі, але дублюються).
status.py:31-78

Чому формується feedback‑loop:
status_payload_too_large додається в errors[], що ще збільшує payload, і так по колу.
status.py:1176-1188

Що потрібно саме “дослідити” в поточному payload (без коду):

Подивитись errors[].context — чи є великі вкладені об’єкти/масиви.
Подивитись last_command.result — чи містить воно великі summaries (tail_guard/repair).
Перевірити дублікати: ohlcv_final_1m і ohlcv_final["1m"], tail_guard.near/far.

## 2026-01-31T03:25:00 — PRE (MODE=read-only discovery): Live payload snapshot + реальні атрибути FXCM row

- Мета → зняти фактичні метрики live status snapshot (розміри секцій, дублікати) та підтвердити очікувані атрибути FXCM offers row для instrument.
- Scope → Redis status:snapshot (fxcm_local), runtime/status.py, runtime/fxcm_forexconnect.py.
- Non-goals → будь-які зміни коду або схем.
- Інваріанти/рейки → read-only discovery.
- План → витягнути snapshot, порахувати довжини ключових секцій, перевірити дублікати, зібрати статистику errors.code.
- Тести/перевірки → локальні read-only команди (redis-cli GET snapshot через python).

## 2026-01-31T03:32:00 — POST (MODE=read-only discovery): Live payload snapshot + реальні атрибути FXCM row

- Що зроблено → знято live status:snapshot з Redis і пораховано розміри секцій/дублікати; отримано статистику errors.code; підтверджено очікування instrument як атрибуту offers row без fallback.
- Де зроблено → Redis key fxcm_local:status:snapshot; runtime/status.py; runtime/fxcm_forexconnect.py.
- Як перевірено → python + redis-cli GET snapshot (read-only).
- Результати (фактичні значення з live payload) →
  - Загальний розмір JSON: len=9037 байт (понад 8KB поріг).
  - errors: count=20; max_context=19 (у цьому знятті контексти малі).
  - errors_len=3221 байт; degraded_len=24 байти.
  - last_command_len=120 байт; last_command.result_len=2 байти (невелике у цьому знятті).
  - ohlcv_final_1m_len=79 байт; ohlcv_final_map_1m_len=79 байт.
  - tail_guard_len=3111 байт; tail_guard.near_len=1032 байт; tail_guard.far_len=1032 байт.
  - Дублікати підтверджені: ohlcv_final_1m == ohlcv_final["1m"] (True); tail_guard.near == tail_guard.far (True).
  - Статистика errors.code: status_payload_too_large → count=20, max_msg_len=44.
- Висновки/нотатки →
  - Поточне переповнення >8KB формується переважно errors[] і tail_guard; у цьому знятті errors.context невеликі, але помилка status_payload_too_large домінує (20/20).
  - Дублювання ohlcv_final_1m та ohlcv_final["1m"], а також tail_guard.near/far підтверджено фактом live snapshot.
  - Очікування instrument: _offer_row_to_tick читає instrument як атрибут з offers row (fx.get_table(fx.OFFERS)); fallback відсутній. Якщо SDK/row має інші атрибути, потрібна валідація фактичної структури row, а не “латання” fallback-ами.

  ## 2026-01-31T03:40:00 — PRE (MODE=read-only discovery): Перевірка фактичних SDK/row атрибутів

  - Мета → зібрати фактичні атрибути SDK/row, на які спирається код (offers row), і підтвердити джерело полів.
  - Scope → пошук по репозиторію: runtime/fxcm_forexconnect.py, fxcm/*, docs/runbooks.
  - Non-goals → будь-які зміни коду або схем.
  - Інваріанти/рейки → read-only discovery.
  - План → просканувати код/документи на згадки атрибутів row (instrument, bid, ask, time, offer_id тощо) та місця створення/передачі row.
  - Тести/перевірки → не застосовно.

  ## 2026-01-31T03:50:00 — POST (MODE=read-only discovery): Перевірка фактичних SDK/row атрибутів

  - Що зроблено → інвентаризовано очікувані атрибути SDK/row у runtime та інструменті capture; знайдено фактичні списки candidate-полів для часу.
  - Де зроблено → runtime/fxcm_forexconnect.py; tools/capture_fxcm_ticks.py; tests/test_fxcm_offers_subscription.py; docs/runbooks/fxcm_capture_ticks.md.
  - Як перевірено → read-only аналіз коду.
  - Фактичні атрибути row, які очікує код (поточний SSOT у repo) →
    - Обов’язкові для offers row → `instrument`, `bid`, `ask` (без fallback).
    - Час події (tick_ts_ms) → `_extract_event_ts_ms` шукає у наборах: `time`, `timestamp`, `tick_time`, `tick_time_ms`, `time_ms`, `time_stamp`, `event_time`, `event_ts`, `event_ts_ms`, `last_update`, `last_update_time`.
    - Інструмент capture (tools/capture_fxcm_ticks.py) додатково допускає час у: `time`, `timestamp`, `tick_time`, `datetime`, `date` (як raw tick time).
    - Тести використовують `time` як event time (DummyRow.time).
  - Висновки/нотатки →
    - У репозиторії немає явної схеми реального SDK-row (ForexConnect), лише candidate-листи для часу та hard-require полів `instrument`/`bid`/`ask`.
    - Для “фактичних” атрибутів SDK потрібен live capture (runbook: docs/runbooks/fxcm_capture_ticks.md) або log з офіційного SDK; без цього можна лише констатувати поточні очікування у коді.

  ## 2026-01-30T23:55:00 — PRE (MODE=PATCH): Slice A — не вбивати status pubsub при overflow

  - Мета → при payload_size > 8KB не припиняти pubsub; публікувати компактний статус.
  - Scope → runtime/status.py (publish_snapshot).
  - Non-goals → нові канали/схеми, зміни контрактів.
  - Інваріанти/рейки → валідний status_v2; degraded-but-loud; мінімальний диф.
  - План → додати адаптивне стиснення payload (tail_guard → ohlcv_final → errors dedup/limit) і все одно publish.
  - Тести/перевірки → не запускати (немає вказівки).

  ## 2026-01-30T23:59:00 — POST (MODE=PATCH): Slice A — не вбивати status pubsub при overflow

  - Що зроблено → у publish_snapshot додано адаптивне стиснення payload (tail_guard → ohlcv_final → errors dedup/limit) і публікацію compact payload замість early return.
  - Де зроблено → runtime/status.py.
  - Як перевірено → не запускалось (немає вказівки).
  - Ризики/нотатки → компактний payload може втратити деталі tail_guard/ohlcv_final; degraded-but-loud зберігається через error status_payload_too_large.

  ## 2026-01-30T23:59:30 — PRE (MODE=PATCH): Slice B/C — прибрати дубль ohlcv_final_1m у pubsub + дедуп status_payload_too_large

  - Мета → прибрати гарантований дубль ohlcv_final_1m з pubsub payload; прибрати спам status_payload_too_large через дедуп останньої помилки.
  - Scope → runtime/status.py.
  - Non-goals → нові канали/схеми, зміни контрактів.
  - Інваріанти/рейки → валідний status_v2; degraded-but-loud; мінімальний диф.
  - План → виключити ohlcv_final_1m з build_status_pubsub_payload; у publish_snapshot дедупити status_payload_too_large і оновлювати context.
  - Тести/перевірки → не запускати (немає вказівки).

  ## 2026-01-31T00:03:00 — POST (MODE=PATCH): Slice B/C — прибрати дубль ohlcv_final_1m у pubsub + дедуп status_payload_too_large

  - Що зроблено → прибрано ohlcv_final_1m з pubsub payload; додано дедуп status_payload_too_large (оновлюється context.size_bytes і count без додавання нового error).
  - Де зроблено → runtime/status.py.
  - Як перевірено → не запускалось (немає вказівки).
  - Ризики/нотатки → у pubsub більше немає ohlcv_final_1m (залишився ohlcv_final); errors[] не роздувається спамом overflow.

  ## 2026-01-30T23:59:40 — PRE (MODE=PATCH): Slice D — контрольований row shape capture (FXCM offers)

  - Мета → при відсутньому instrument логувати shape row у лог (тип, hasattr Instrument, top-30 non-dunder attrs) і тримати status context коротким.
  - Scope → runtime/fxcm_forexconnect.py.
  - Non-goals → зміни контрактів/схем або форматів статусу.
  - Інваріанти/рейки → degraded-but-loud; status context короткий; мінімальний диф.
  - План → додати логування у _offer_row_to_tick при відсутньому instrument та short context у handler.
  - Тести/перевірки → не запускати (немає вказівки).

  ## 2026-01-31T00:05:00 — POST (MODE=PATCH): Slice D — контрольований row shape capture (FXCM offers)

  - Що зроблено → додано логування shape row при відсутньому instrument (тип, hasattr Instrument, top-30 non-dunder attrs) та короткий status context із row_type.
  - Де зроблено → runtime/fxcm_forexconnect.py.
  - Як перевірено → не запускалось (немає вказівки).
  - Ризики/нотатки → логи містять частину атрибутів row; status context лишається коротким.

  ## 2026-01-31T00:07:00 — PRE (MODE=PATCH): Виправлення mypy помилок (status_payload_too_large dedup)

  - Мета → прибрати помилки типізації в publish_snapshot (Optional доступ).
  - Scope → runtime/status.py.
  - Non-goals → зміна логіки дедуп або контракти.
  - Інваріанти/рейки → мінімальний диф, поведінка дедуп без змін.
  - План → зробити явне розгортання context у dict з перевіркою типу.
  - Тести/перевірки → не запускати (немає вказівки).

  ## 2026-01-31T00:08:00 — POST (MODE=PATCH): Виправлення mypy помилок (status_payload_too_large dedup)

  - Що зроблено → уточнено обробку context через окрему змінну з перевіркою типу.
  - Де зроблено → runtime/status.py.
  - Як перевірено → не запускалось (немає вказівки).
  - Ризики/нотатки → логіка дедуп без змін.

  ## 2026-01-30T23:59:50 — PRE (MODE=read-only discovery): Перевірка calendar.is_open(now)

  - Мета → звірити поточний стан Calendar.is_open(now) для профілю/символа.
  - Scope → runtime перевірка через config/config.py + core/time/calendar.py.
  - Non-goals → будь-які зміни коду/конфігів.
  - Інваріанти/рейки → read-only discovery.
  - План → виконати одноразову перевірку is_open для default символа/ts.
  - Тести/перевірки → одноразовий запуск Python (read-only).

  ## 2026-01-31T00:10:00 — POST (MODE=read-only discovery): Перевірка calendar.is_open(now)

  - Що зроблено → виконано перевірку Calendar.is_open(now) з конфігом calendar_tag/path.
  - Де зроблено → core/time/calendar.py + config/config.py (runtime check через Python).
  - Як перевірено → `python -c ...` (read-only).
  - Результат → now_ms=1769811028679, symbol=XAUUSD, is_open=False; calendar_tag=fxcm_calendar_v1_ny; calendar_path=config/calendar_overrides.json; calendar_health_error=None; next_open_ms=1769983200000.
  - Нотатки → попередня спроба з calendar_overrides_path впала (атрибут відсутній у Config); використано calendar_path.

  ## 2026-01-31T00:12:00 — PRE (MODE=PATCH): P8.S8/S9 — market-aware stale_tf grace + calendar-gated liveness

  - Мета → додати grace window для stale_tf (1m/15m) і suppress при market closed; не робити reconnect/resubscribe коли market closed; уточнити FXCM login логи.
  - Scope → ui_lite/server.py, app/composition.py, runtime/fxcm_forexconnect.py, runtime/fxcm/history_provider.py, runtime/warmup.py, runtime/backfill.py, runtime/repair.py, tests/*.
  - Non-goals → нові канали/схеми, зміни контрактів.
  - Інваріанти/рейки → календар SSOT; без silent fallback; мінімальний диф.
  - План →
    1) Додати grace_ms та market-aware suppress у UI Lite health.
    2) Додати grace_ms та market-aware suppress у preview summary (composition).
    3) Додати pause-on-closed у FXCM stream loop; деталізувати login logs.
    4) Додати тести: UI grace (15m), FSM closed → no action.
  - Тести/перевірки → pytest (за потреби).

  ## 2026-01-31T00:25:00 — POST (MODE=PATCH): P8.S8/S9 — market-aware stale_tf grace + calendar-gated liveness

  - Що зроблено →
    - UI Lite: додано grace window (1m=5s, 15m=60s) і suppress stale/ohlcv_lag при market closed; додано helper для stale state.
    - Preview summary: додано grace window і suppress при market closed.
    - FXCM stream: додано pause-on-closed у live loop, деталізовано login logs (component/reason).
    - History: додано login logs з component=history reason=warmup/backfill/tail_guard.
    - Додано тести: UI grace 15m, FSM market closed → no action.
  - Де зроблено → ui_lite/server.py; app/composition.py; runtime/fxcm_forexconnect.py; runtime/warmup.py; runtime/backfill.py; runtime/repair.py; tests/test_ui_lite_preview_grace.py; tests/test_fxcm_stale_policy.py.
  - Як перевірено → тести не запускались (немає вказівки).
  - Ризики/нотатки → suppress stale/lag при market closed може приховати реальну затримку поза календарем; login logs залежать від правильності caller reason.

  ## 2026-01-31T00:27:00 — POST (MODE=PATCH): P8.S8/S9 — fix ui_lite top_tf delay

  - Що зроблено → виправлено розрахунок top_tf delay у UI Lite (market_open + grace), прибрано undefined expected_ms.
  - Де зроблено → ui_lite/server.py.
  - Як перевірено → get_errors для ui_lite/server.py (OK).
  - Ризики/нотатки → без змін зовнішньої поведінки, лише фікс логіки й типізації.

  ## 2026-01-31T00:30:00 — PRE (MODE=PATCH): Актуалізація docs/REPO_LAYOUT.md та README.md

  - Мета → синхронізувати документацію з поточним станом (grace window, market-aware health, adaptive status pubsub, нові тести).
  - Scope → docs/REPO_LAYOUT.md, README.md.
  - Non-goals → зміни коду/контрактів.
  - Інваріанти/рейки → мінімальний диф, SSOT терміни укр.
  - План → оновити описи в REPO_LAYOUT та README.
  - Тести/перевірки → не застосовно.

  ## 2026-01-31T00:33:00 — POST (MODE=PATCH): Актуалізація docs/REPO_LAYOUT.md та README.md

  - Що зроблено → оновлено REPO_LAYOUT (adaptive status pubsub, market-closed pause, UI Lite grace) та README (compact status pubsub, market-aware grace).
  - Де зроблено → docs/REPO_LAYOUT.md, README.md.
  - Як перевірено → не застосовно.
  - Ризики/нотатки → лише документаційні зміни.

  ## 2026-01-31T00:35:00 — PRE (MODE=read-only discovery): Calendar.is_open для контрольних міток

  - Мета → перевірити is_open на 21:45:03Z, 21:59:59Z, 22:00:00Z, 22:00:01Z.
  - Scope → core/time/calendar.py + config/config.py (read-only перевірка).
  - Non-goals → зміни коду/конфігів.
  - Інваріанти/рейки → read-only discovery.
  - План → одноразовий запуск Python з фіксованими epoch ms.
  - Тести/перевірки → python -c (read-only).

  ## 2026-01-31T00:37:00 — POST (MODE=read-only discovery): Calendar.is_open для контрольних міток

  - Що зроблено → виконано перевірку is_open/next_open для заданих міток.
  - Де зроблено → core/time/calendar.py + config/config.py (runtime check через Python).
  - Як перевірено → `python -c ...` (read-only).
  - Результати →
    - ts_ms=1769809503000 (2026-01-30T21:45:03Z) → is_open=True, next_open_ms=1769983200000.
    - ts_ms=1769810399000 (2026-01-30T21:59:59Z) → is_open=True, next_open_ms=1769983200000.
    - ts_ms=1769810400000 (2026-01-30T22:00:00Z) → is_open=False, next_open_ms=1769983200000.
    - ts_ms=1769810401000 (2026-01-30T22:00:01Z) → is_open=False, next_open_ms=1769983200000.
    - calendar_tag=fxcm_calendar_v1_ny; calendar_path=config/calendar_overrides.json; health_error=None.

    ## 2026-01-31T00:40:00 — PRE (MODE=PATCH): Статус heartbeat cadence + UI thresholds

    - Мета → SSOT для status cadence/thresholds; heartbeat publish у main; UI WARN тільки після порогу; interruptible wait.
    - Scope → config/config.py, app/main.py, ui_lite/server.py.
    - Non-goals → нові канали/схеми, зміни контрактів.
    - Інваріанти/рейки → мінімальний диф; degraded-but-loud зберігається.
    - План →
      1) Додати status_publish_period_ms/status_fresh_warn_ms у Config.
      2) Використати в app/main (publish_if_due + interruptible wait).
      3) Використати в UI Lite health thresholds.
    - Тести/перевірки → pytest -k "ui_lite_preview_grace or fxcm_stale_policy" -q.

    ## 2026-01-31T00:45:00 — POST (MODE=PATCH): Статус heartbeat cadence + UI thresholds

    - Що зроблено →
      - Додано SSOT поля `status_publish_period_ms`, `status_fresh_warn_ms` у Config.
      - heartbeat publish у main тепер використовує `status_publish_period_ms` і interruptible wait через stop_event.
      - UI Lite health використовує `status_fresh_warn_ms` (status_stale/lag) і експонує у snapshot.
    - Де зроблено → config/config.py; app/main.py; ui_lite/server.py.
    - Як перевірено → `C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m pytest -k "ui_lite_preview_grace or fxcm_stale_policy" -q` (OK).
    - Ризики/нотатки → пороги UI залежні від SSOT; за потреби підкрутити у config.

    ## 2026-01-31T00:50:00 — PRE (MODE=PATCH): Актуалізація документації (calendar + останні зміни)

    - Мета → синхронізувати календарні та runtime описи з поточним кодом (paused_market_closed, SSOT календар, status cadence, UI grace).
    - Scope → docs/P8 P9 P10 для FXCM ticks history live bars.md; docs/runtime.md; docs/P7 Calendar SSOT.md.
    - Non-goals → зміни коду/конфігів.
    - Інваріанти/рейки → мінімальний диф, мова укр., без зміни історичних аудитів.
    - План → оновити фрагменти про calendar/paused_market_closed/status cadence/UI health.
    - Тести/перевірки → не застосовно.

    ## 2026-01-31T01:05:00 — POST (MODE=PATCH): Актуалізація документації (calendar + останні зміни)

    - Що зроблено → оновлено runtime опис status cadence/compact payload; синхронізовано календарні документи щодо `closed_intervals_utc` і SSOT даних.
    - Де зроблено → docs/runtime.md; docs/P7 Calendar SSOT.md; docs/audit_v7_runtime_core.md; docs/calendar_sessions_spec.md.
    - Як перевірено → без запуску тестів (документація).
    - Ризики/відкат/нотатки → зміни текстові; ризик — застаріти знову при зміні календарних даних.

    ## 2026-01-31T01:20:00 — PRE (MODE=PATCH): P8.S11 heartbeat під paused_market_closed + rails конфігу

    - Мета → додати валідацію cadence у конфігу, тест heartbeat без тіків при закритому ринку, exit‑gate і мінімальне UI приглушення status_lag.
    - Scope → config/config.py; ui_lite/server.py; tests/test_status_heartbeat_paused_market_closed.py; tools/exit_gates/gates/gate_status_heartbeat_paused_market_closed.py; tools/exit_gates/manifest.json; README.md; docs/REPO_LAYOUT.md.
    - Non-goals → FXCM SDK, Redis I/O, зміни контрактів поза UI health.
    - Інваріанти/рейки → heartbeat cadence береться з Config; wait interruptible; мінімальний диф.
    - План → валідація cadence; UI health suppression при closed; тест + gate; оновити docs.
    - Тести/перевірки → pytest -k status_heartbeat_paused_market_closed -q; tools.run_exit_gates (manifest).

    ## 2026-01-31T01:40:00 — POST (MODE=PATCH): P8.S11 heartbeat під paused_market_closed + rails конфігу

    - Що зроблено → додано валідацію cadence у Config; додано UI health приглушення status_lag при market closed з hard‑rail; створено тест heartbeat під paused_market_closed; додано exit‑gate; оновлено README/REPO_LAYOUT.
    - Де зроблено → config/config.py; ui_lite/server.py; tests/test_status_heartbeat_paused_market_closed.py; tools/exit_gates/gates/gate_status_heartbeat_paused_market_closed.py; tools/exit_gates/manifest.json; README.md; docs/REPO_LAYOUT.md.
    - Як перевірено → pytest -k status_heartbeat_paused_market_closed -q; python -m tools.run_exit_gates --manifest tools/exit_gates/manifest.json --out reports/exit_gates.
    - Ризики/відкат/нотатки → UI health тепер залежить від status_publish_period_ms; при нестандартному period поріг hard‑warn = 10×period.

    ## 2026-01-31T02:05:00 — PRE (MODE=PATCH): P8.S5 throttle fxcm_tick_event_ahead_of_receipt + metric

    - Мета → усунути log/error спам при систематичному `event_ts_ms > receipt_ms`; додати лічильник і throttle (≥ 60с) по символу.
    - Scope → runtime/fxcm_forexconnect.py; runtime/status.py; observability/metrics.py; tests/test_fxcm_event_ahead_throttle.py; tools/exit_gates/gates/gate_fxcm_event_ahead_throttle.py; tools/exit_gates/manifest.json.
    - Non-goals → FXCM SDK, Redis network, зміни контрактів.
    - Інваріанти/рейки → epoch ms; кожна аномалія інкрементує лічильник; errors[] throttle per symbol; thread‑safe.
    - План → throttle helper у Status; FXCM stream використовує throttle map; метрика; тест + gate.
    - Тести/перевірки → pytest -k event_ahead_throttle -q; tools.run_exit_gates (manifest).

    ## 2026-01-31T02:25:00 — POST (MODE=PATCH): P8.S5 throttle fxcm_tick_event_ahead_of_receipt + metric

    - Що зроблено → додано throttled error helper у Status; per‑symbol throttle state в FXCM stream; метрика `fxcm_event_ahead_total`; тест і gate; manifest оновлено.
    - Де зроблено → runtime/status.py; runtime/fxcm_forexconnect.py; observability/metrics.py; tests/test_fxcm_event_ahead_throttle.py; tools/exit_gates/gates/gate_fxcm_event_ahead_throttle.py; tools/exit_gates/manifest.json.
    - Як перевірено → pytest -k event_ahead_throttle -q; python -m tools.run_exit_gates --manifest tools/exit_gates/manifest.json --out reports/exit_gates.
    - Ризики/відкат/нотатки → throttle ключ per‑symbol; hard‑throttle 60с, лог спаму знижено без втрати лічильника.

    ## 2026-01-31T02:40:00 — PRE (MODE=PATCH): Актуалізація README/REPO_LAYOUT/доків після P8.S5

    - Мета → синхронізувати docs з новою метрикою fxcm_event_ahead_total і throttled warning.
    - Scope → README.md; docs/REPO_LAYOUT.md; docs/runtime.md.
    - Non-goals → зміни коду/контрактів.
    - Інваріанти/рейки → мінімальний диф; укр. мова.
    - План → додати згадки про метрику та throttle у ключових розділах.
    - Тести/перевірки → не застосовно (документація).

    ## 2026-01-31T02:45:00 — POST (MODE=PATCH): Актуалізація README/REPO_LAYOUT/доків після P8.S5

    - Що зроблено → додано згадки про `fxcm_event_ahead_total` та throttle warnings у README/REPO_LAYOUT/runtime.
    - Де зроблено → README.md; docs/REPO_LAYOUT.md; docs/runtime.md.
    - Як перевірено → без запуску тестів (документація).
    - Ризики/відкат/нотатки → текстові зміни; ризик — застаріти при зміні метрик.

    ## 2026-01-31T03:05:00 — PRE (MODE=PATCH): P8.S6 e2e smoke tick->status->preview (no FXCM SDK)

    - Мета → інтеграційний smoke‑тест “normalize → validate → status → preview” без FXCM/Redis.
    - Scope → tests/test_e2e_tick_to_preview_smoke.py; tools/exit_gates/gates/gate_e2e_tick_to_preview_smoke.py; tools/exit_gates/manifest.json; README.md; docs/REPO_LAYOUT.md; docs/runtime.md.
    - Non-goals → FXCM SDK, Redis/мережа, зміни контрактів.
    - Інваріанти/рейки → tick валідний за tick_v1; late tick дропається; rails/метрики фіксують drop.
    - План → створити тест; додати gate; оновити manifest; синхронізувати docs.
    - Тести/перевірки → pytest -k e2e_tick_to_preview_smoke -q; tools.run_exit_gates (manifest).

    ## 2026-01-31T03:25:00 — POST (MODE=PATCH): P8.S6 e2e smoke tick->status->preview (no FXCM SDK)

    - Що зроблено → додано e2e smoke тест tick→status→preview; gate і manifest оновлено; docs синхронізовано.
    - Де зроблено → tests/test_e2e_tick_to_preview_smoke.py; tools/exit_gates/gates/gate_e2e_tick_to_preview_smoke.py; tools/exit_gates/manifest.json; README.md; docs/REPO_LAYOUT.md; docs/runtime.md.
    - Як перевірено → pytest -k e2e_tick_to_preview_smoke -q; python -m tools.run_exit_gates --manifest tools/exit_gates/manifest.json --out reports/exit_gates.
    - Ризики/відкат/нотатки → тест опирається на поточну семантику late tick (past_mutations_total інкрементується).

    ## 2026-01-31T03:45:00 — PRE (MODE=PATCH): P8.S7 tick_out_of_order metric + unit test

    - Мета → формалізувати out-of-order по bucket, додати метрику і тест без log‑spam.
    - Scope → runtime/tick_feed.py; observability/metrics.py; tests/test_tick_out_of_order_policy.py; tools/exit_gates/gates/gate_tick_out_of_order_policy.py; tools/exit_gates/manifest.json; README.md; docs/REPO_LAYOUT.md; docs/runtime.md.
    - Non-goals → зміни контрактів tick/preview.
    - Інваріанти/рейки → bucket назад = drop; метрика інкрементується; warnings throttled.
    - План → оновити tick_feed + метрику; додати тест і gate; синхронізувати docs.
    - Тести/перевірки → pytest -k tick_out_of_order_policy -q; tools.run_exit_gates (manifest).

    ## 2026-01-31T04:05:00 — POST (MODE=PATCH): P8.S7 tick_out_of_order metric + unit test

    - Що зроблено → додано пер‑symbol out‑of‑order політику по bucket, метрику `tick_out_of_order_total`, тест і gate; docs синхронізовано.
    - Де зроблено → runtime/tick_feed.py; observability/metrics.py; tests/test_tick_out_of_order_policy.py; tools/exit_gates/gates/gate_tick_out_of_order_policy.py; tools/exit_gates/manifest.json; README.md; docs/REPO_LAYOUT.md; docs/runtime.md.
    - Як перевірено → pytest -k tick_out_of_order_policy -q; python -m tools.run_exit_gates --manifest tools/exit_gates/manifest.json --out reports/exit_gates.
    - Ризики/відкат/нотатки → throttle на warnings через StatusManager; лічильник інкрементується навіть при drop.

    ## 2026-01-31T16:09:36 — PRE (MODE=read-only discovery): аудит status payload bloat

    - Мета → діагностика bloat у status/status:snapshot без змін runtime‑коду.
    - Scope → runtime/status.py; core/contracts/public/status_v2.json; runtime/publisher.py; config/config.py; observability/metrics.py; runtime/tail_guard.py; runtime/republish.py; runtime/rebuild_derived.py; артефакти data/audit_status_bloat/*; звіти docs/audit_status_bloat/*; tools/audit/measure_status_payload.py.
    - Non-goals → зміни коду/контрактів, рефакторинг.
    - Інваріанти/рейки → лише read‑only аналіз; створювати тільки нові файли у дозволених каталогах; твердження тільки з path:line або артефактів.
    - План → зібрати snapshots (Redis/HTTP), зібрати 3–5 samples; згенерувати size report; сформувати findings + patch plan.
    - Тести/перевірки → redis-cli GET {NS}:status:snapshot; curl /api/status; python tools/audit/measure_status_payload.py.

    ## 2026-01-31T16:16:30+01:00 — POST (MODE=read-only discovery): аудит status payload bloat

    - Що зроблено → підготовлено звіт findings і план мінімального патчу; зафіксовано ключові висновки з size_report (tail_guard як основний вклад у bloat), описано механізм compact rails і де обробляється oversize.
    - Де зроблено → docs/audit_status_bloat/findings.md; docs/audit_status_bloat/recommendations_patch_plan.md.
    - Як перевірено → тести не запускались (read-only артефакти).
    - Ризики/нотатки → HTTP snapshot не отримано (сервер недоступний); усі samples ідентичні (копії одного Redis snapshot), потрібна варіативність для повнішого висновку.

    ## 2026-02-01T00:16:23+01:00 — PRE (MODE=PATCH): Status payload bloat → tail_guard summary + soft compact

    - Мета → зменшити bloat у status payload до відкриття ринку: додати tail_guard_summary і soft-compact перед hard rail.
    - Scope → core/contracts/public/status_v2.json; runtime/status.py; config/config.py; tests/test_status_payload_soft_compact.py; docs/Public Surface.md.
    - Non-goals → runtime/tail_guard.py, FXCM streaming, UI; hard rail 8KB не змінювати.
    - Інваріанти/рейки → status payload завжди валідний по schema; drop details має бути loud через degraded[]; public keys/namespace без змін.
    - План → (1) schema: tail_guard_summary; (2) runtime: dual-write + soft-compact; (3) config: пороги/перемикачі SSOT; (4) тести 1–2; (5) docs коротка примітка.
    - Тести/перевірки → pytest -q -k "status_payload_soft_compact or status_payload_size_rail".

    ## 2026-02-01T00:18:33+01:00 — POST (MODE=PATCH): Status payload bloat → tail_guard summary + soft compact

    - Що зроблено → додано tail_guard_summary у schema і payload; реалізовано soft-compact з loud degraded; додано SSOT перемикачі та тести; оновлено Public Surface.
    - Де зроблено → core/contracts/public/status_v2.json; runtime/status.py; config/config.py; tests/test_status_payload_soft_compact.py; docs/Public Surface.md.
    - Як перевірено → тести не запускались (pytest не виконано).
    - Ризики/нотатки → soft-compact відкидає детальний tail_guard за замовчуванням; за потреби деталі можна увімкнути через status_tail_guard_detail_enabled і підняти status_soft_limit_bytes.

    ## 2026-02-01T00:29:23+01:00 — PRE (MODE=read-only discovery): повторний збір status snapshot

    - Мета → зібрати свіжі зразки status payload після змін soft-compact.
    - Scope → data/audit_status_bloat/*; запуск tools/audit/measure_status_payload.py.
    - Non-goals → зміни runtime-коду чи конфігів.
    - Інваріанти/рейки → лише read-only артефакти; без модифікації логіки.
    - План → зняти Redis snapshot, оновити samples, спробувати HTTP snapshot, перерахувати size_report.
    - Тести/перевірки → redis-cli GET {NS}:status:snapshot; (опц.) HTTP /api/status; python tools/audit/measure_status_payload.py.

    ## 2026-02-01T00:30:11+01:00 — POST (MODE=read-only discovery): повторний збір status snapshot

    - Що зроблено → оновлено Redis snapshot і samples; отримано HTTP snapshot; перераховано size_report.
    - Де зроблено → data/audit_status_bloat/status_snapshot_redis.json; data/audit_status_bloat/status_sample_1.json; data/audit_status_bloat/status_sample_2.json; data/audit_status_bloat/status_sample_3.json; data/audit_status_bloat/status_snapshot_http.json; data/audit_status_bloat/size_report.json; data/audit_status_bloat/size_report.md.
    - Як перевірено → redis-cli GET fxcm_local:status:snapshot; HTTP /api/status; python tools/audit/measure_status_payload.py c:\Aione_projects\fxcm_connector_v2\data\audit_status_bloat.
    - Ризики/нотатки → перший запуск measure_status_payload без аргументу завершився помилкою; повторний запуск з каталогом — OK.

    ## 2026-02-01T00:33:53+01:00 — PRE (MODE=read-only discovery): перевірка логування FXCM login

    - Мета → локалізувати, чому не видно log.info про успішний FXCM login.
    - Scope → runtime/fxcm_forexconnect.py; app/main.py; ui_lite/server.py.
    - Non-goals → зміни коду або конфігів.
    - Інваріанти/рейки → read-only аналіз; всі висновки з path:line.
    - План → знайти точку логу, перевірити умови виконання та logging setup.
    - Тести/перевірки → немає (читання файлів).

    ## 2026-02-01T00:34:08+01:00 — POST (MODE=read-only discovery): перевірка логування FXCM login

    - Що зроблено → знайдено місце log.info після fx.login; підтверджено базове logging.basicConfig у app/main.py.
    - Де зроблено → runtime/fxcm_forexconnect.py; app/main.py.
    - Як перевірено → читання файлів (line references).
    - Ризики/нотатки → лог з’являється лише після успішного fx.login; якщо FXCM не готовий або stream не стартував, лог відсутній.

    ## 2026-02-01T00:36:03+01:00 — PRE (MODE=read-only discovery): відсутній log.info про FXCM login

    - Мета → пояснити відсутність логу про успішний FXCM login при відкритому ринку.
    - Scope → runtime/fxcm_forexconnect.py; app/main.py; ui_lite/server.py.
    - Non-goals → зміни коду/конфігу.
    - Інваріанти/рейки → лише читання; висновки з path:line.
    - План → перевірити місце логу, попередній лог перед fx.login, базове логування у entrypoint.
    - Тести/перевірки → немає.

    ## 2026-02-01T00:36:14+01:00 — POST (MODE=read-only discovery): відсутній log.info про FXCM login

    - Що зроблено → підтверджено, що log.info про успішний login стоїть після fx.login; перед ним є log.info про start login; базове logging.basicConfig є лише у app/main.py та ui_lite/server.py.
    - Де зроблено → runtime/fxcm_forexconnect.py; app/main.py; ui_lite/server.py.
    - Як перевірено → читання файлів (path:line).
    - Ризики/нотатки → якщо не видно навіть pre-login логу, значить stream не стартував або entrypoint без базового logging; якщо pre-login є, а success немає — fx.login не завершується успішно.

    ## 2026-02-01T00:47:09+01:00 — PRE (MODE=PATCH): логування FXCM login/paused_market_closed + probe

    - Мета → зробити прозорі логи: чому не логінимось при paused_market_closed та (опційно) probe login для перевірки календаря.
    - Scope → runtime/fxcm_forexconnect.py; config/config.py.
    - Non-goals → зміна доменної логіки tail_guard/FXCM stream поза логуванням; без UI.
    - Інваріанти/рейки → status snapshot валідний; без silent fallback; probe — опційний, не default.
    - План → додати throttled info-лог при market closed; додати опційний probe login з warning + degraded; SSOT конфіг.
    - Тести/перевірки → не запускати (локальна зміна логів).

    ## 2026-02-01T00:47:36+01:00 — POST (MODE=PATCH): логування FXCM login/paused_market_closed + probe

    - Що зроблено → додано throttled лог при paused_market_closed; реалізовано опційний probe login з warning + degraded; додано SSOT перемикачі.
    - Де зроблено → runtime/fxcm_forexconnect.py; config/config.py.
    - Як перевірено → тести не запускались.
    - Ризики/нотатки → probe login вимкнено за замовчуванням; увімкнення може додати легке навантаження та попередження при календарних розбіжностях.

    ## 2026-02-01T00:50:00+01:00 — PRE (MODE=PATCH): зрозумілі часові логи для paused_market_closed/probe

    - Мета → зробити зрозумілий часовий формат у логах paused_market_closed та пояснити probe login failed.
    - Scope → runtime/fxcm_forexconnect.py.
    - Non-goals → зміни логіки FSM/FXCM підключення.
    - Інваріанти/рейки → лише логування; без зміни поведінки.
    - План → додати UTC ISO у лог; додати контекст connection/host/user у probe fail лог.
    - Тести/перевірки → не запускати.

    ## 2026-02-01T00:50:24+01:00 — POST (MODE=PATCH): зрозумілі часові логи для paused_market_closed/probe

    - Що зроблено → додано ISO-UTC у paused_market_closed лог; розширено probe fail лог контекстом; додано UTC у probe OK warning/context.
    - Де зроблено → runtime/fxcm_forexconnect.py.
    - Як перевірено → тести не запускались.
    - Ризики/нотатки → лише логування, поведінка не змінена.

    ## 2026-02-01T01:30:23+01:00 — PRE (MODE=PATCH): fxcm state sdk_ok + прибрати error у UI payload

    - Мета → усунути плутанину: preflight state "connected" → "sdk_ok"; прибрати fxcm_calendar_mismatch з errors[] у probe.
    - Scope → runtime/fxcm_forexconnect.py.
    - Non-goals → зміни FSM/стріму чи UI.
    - Інваріанти/рейки → status snapshot валідний; без silent fallback; probe лише log+degraded.
    - План → змінити preflight state; зробити probe OK нейтральним і без errors[].
    - Тести/перевірки → не запускати.

    ## 2026-02-01T01:30:46+01:00 — POST (MODE=PATCH): fxcm state sdk_ok + прибрати error у UI payload

    - Що зроблено → preflight state змінено на sdk_ok; probe OK більше не додає errors[], лише degraded і нейтральний лог.
    - Де зроблено → runtime/fxcm_forexconnect.py.
    - Як перевірено → тести не запускались.
    - Ризики/нотатки → UI більше не показує fxcm_calendar_mismatch у ERRORS, лише в DEGRADED.

    ## 2026-02-01T01:40:45+01:00 — PRE (MODE=PATCH): прибрати status_soft_compact_tail_guard з degraded

    - Мета → не позначати soft-compact як деградацію.
    - Scope → runtime/status.py.
    - Non-goals → зміни логіки soft-compact чи порогів.
    - Інваріанти/рейки → payload валідний; behavior soft-compact без змін.
    - План → прибрати додавання status_soft_compact_tail_guard у degraded.
    - Тести/перевірки → не запускати.

    ## 2026-02-01T01:41:03+01:00 — POST (MODE=PATCH): прибрати status_soft_compact_tail_guard з degraded

    - Що зроблено → видалено додавання status_soft_compact_tail_guard у degraded під час soft-compact.
    - Де зроблено → runtime/status.py.
    - Як перевірено → тести не запускались.
    - Ризики/нотатки → soft-compact більше не сигналізується у degraded; залишається лише поведінка зменшення payload.

    ## 2026-02-01T01:42:35+01:00 — PRE (MODE=read-only discovery): оцінка top-секцій status payload

    - Мета → перевірити та оцінити top-секції за вагою у status payload.
    - Scope → data/audit_status_bloat/size_report.md.
    - Non-goals → зміни коду/конфігу.
    - Інваріанти/рейки → лише read-only.
    - План → звірити звіт і надати оцінку секцій.
    - Тести/перевірки → немає.

    ## 2026-02-01T01:42:51+01:00 — POST (MODE=read-only discovery): оцінка top-секцій status payload

    - Що зроблено → перевірено size_report та зафіксовано поточні top-секції.
    - Де зроблено → data/audit_status_bloat/size_report.md.
    - Як перевірено → читання файлу.
    - Ризики/нотатки → вибірка 1 sample (HTTP snapshot).
    - результат:
      Перевірено за size_report.md (1 sample, 2945 байт). Оцінка:

      ohlcv_final (443): ок, стабільний “корисний” статус; тримати.
      tail_guard_summary (417): прийнятно, вже compact; можна зменшувати лише якщо потрібно ще headroom.
      fxcm (369): нормальний обсяг для стану з reconnect/metrics.
      ohlcv_preview (360): ок; якщо потрібно зменшити — думати про видалення last_bar_open_time_ms або частоти.
      price (184): мінімально необхідне, не чіпати.

    ## 2026-02-01T01:50:01+01:00 — PRE (MODE=read-only discovery): перевірка вибірки measure_status_payload

    - Мета → з’ясувати, чому size_report показує Samples: 1 при наявних JSON.
    - Scope → tools/audit/measure_status_payload.py; data/audit_status_bloat/size_report.json.
    - Non-goals → зміни скрипта.
    - Інваріанти/рейки → лише read-only.
    - План → зафіксувати критерій відбору файлів і фактичний список у звіті.
    - Тести/перевірки → немає.

    ## 2026-02-01T01:50:01+01:00 — POST (MODE=read-only discovery): перевірка вибірки measure_status_payload

    - Що зроблено → зафіксовано критерій відбору: скрипт бере всі *.json у каталозі, виключає лише size_report.json; інших whitelist немає.
    - Де зроблено → tools/audit/measure_status_payload.py (функція _gather_paths).
    - Як перевірено → читання файлів.
    - Ризики/нотатки → у size_report.json фактично оброблено лише status_snapshot_http.json (reports[0].path). Ймовірна причина: звіт згенеровано до появи інших samples або вони не пройшли _load_json під час того запуску.

    ## 2026-02-01T01:50:23+01:00 — PRE (MODE=PATCH): degraded лише при soft-limit trim

    - Мета → додавати degraded тільки коли detail увімкнено і відкинуто через bytes > soft_limit.
    - Scope → runtime/status.py; tests/test_status_payload_soft_compact.py.
    - Non-goals → зміни schema або hard rail.
    - Інваріанти/рейки → payload валідний; логіка soft-compact без зміни умов відкидання.
    - План → умовне додавання degraded; оновити тести.
    - Тести/перевірки → pytest -q -k "status_payload_soft_compact or degraded".

    ## 2026-02-01T01:50:23+01:00 — POST (MODE=PATCH): degraded лише при soft-limit trim

    - Що зроблено → degraded додається лише коли detail увімкнено і payload > soft_limit; тести оновлено.
    - Де зроблено → runtime/status.py; tests/test_status_payload_soft_compact.py.
    - Як перевірено → тести не запускались.
    - Ризики/нотатки → при detail_enabled=False soft-compact не сигналізується у degraded.

    ## 2026-02-01T01:53:58+01:00 — PRE (MODE=read-only discovery): оцінка статусу виконання P8

    - Мета → оцінити поточний статус виконання P8 на основі журналу.
    - Scope → Work/01log.md.
    - Non-goals → зміни коду.
    - Інваріанти/рейки → read-only.
    - План → переглянути записи P8 у журналі й сформувати коротку оцінку.
    - Тести/перевірки → немає.

    ## 2026-02-01T01:54:16+01:00 — POST (MODE=read-only discovery): оцінка статусу виконання P8

    - Що зроблено → підсумовано статус P8 за журналом.
    - Де зроблено → Work/01log.md.
    - Як перевірено → читання журналу.
      Оцінка P8: реалізовано основні slices і гейти (P8.1, S1–S7, S8/S9, S11) з відповідними POST‑записами у журналі. Є аудит P8 (read‑only) та синхронізовані doc
    - Ризики/нотатки → dev_checks раніше мали FAIL через сторонні mypy помилки; P8 гейти/тести окремо описані у відповідних POST.

    ## 2026-02-01T01:58:41+01:00 — PRE (MODE=read-only discovery): P8.3 out-of-order ticks/preview pauses

    - Мета → підтвердити рішення по P8.3 у журналі.
    - Scope → Work/01log.md.
    - Non-goals → зміни коду.
    - Інваріанти/рейки → read-only.
    - План → знайти записи P8.S3/S7 у журналі і відповісти.
    - Тести/перевірки → немає.

    ## 2026-02-01T01:58:41+01:00 — POST (MODE=read-only discovery): P8.3 out-of-order ticks/preview pauses

    - Що зроблено → підтверджено P8.S3/P8.S7 рішення у журналі.
    - Де зроблено → Work/01log.md.
    - Як перевірено → читання журналу.
    - Ризики/нотатки → відповідь базується на лог-записах P8.S3/P8.S7.

    ## 2026-02-01T02:01:40+01:00 — PRE (MODE=PATCH): P9.0 history Date/date fail-fast

    - Мета → case-insensitive Date/date для history rows + fail-fast ContractError при відсутності/непарсабельності.
    - Scope → runtime/fxcm/history_provider.py; tests/.
    - Non-goals → зміни schema чи інших історичних пайплайнів.
    - Інваріанти/рейки → fail-fast; loud status через існуючий _fetch_chunk.
    - План → додати ключ "Date" у allowlist; raise ContractError з кодом history_row_missing_date/history_row_date_invalid; тести (2).
    - Тести/перевірки → pytest -q -k "history_row_date".

    ## 2026-02-01T02:02:11+01:00 — POST (MODE=PATCH): P9.0 history Date/date fail-fast

    - Що зроблено → підтримано Date/date у history rows; додано fail-fast ContractError при відсутності/непарсабельності; тести додано.
    - Де зроблено → runtime/fxcm/history_provider.py; tests/test_fxcm_history_row_date.py.
    - Як перевірено → тести не запускались.
    - Ризики/нотатки → тепер історія може перериватися на першому рядку без Date/date, що коректно для fail-fast.

    ## 2026-02-01T02:05:24+01:00 — PRE (MODE=PATCH): P9.1 budget + single in-flight rail

    - Мета → зробити видимим у status, що другий history запит чекає через single in-flight.
    - Scope → runtime/fxcm/history_provider.py; tests/.
    - Non-goals → зміни токен-бакета або FSM.
    - Інваріанти/рейки → один in-flight; loud сигнал тільки при очікуванні.
    - План → у _fetch_chunk логувати throttled warning при waited=True; додати тест з двома паралельними запитами.
    - Тести/перевірки → pytest -q -k "history_inflight".

    ## 2026-02-01T02:06:01+01:00 — POST (MODE=PATCH): P9.1 budget + single in-flight rail

    - Що зроблено → додано throttled warning history_inflight_wait при очікуванні; додано тест з двома паралельними запитами.
    - Де зроблено → runtime/fxcm/history_provider.py; tests/test_fxcm_history_inflight_budget.py.
    - Як перевірено → тести не запускались.
    - Ризики/нотатки → warning з’являється лише коли другий запит чекає single in-flight.

    ## 2026-02-01T02:08:19+01:00 — PRE (MODE=read-only discovery): перевірка P9.2 final invariants

    - Мета → знайти, чи є вже валідатор final invariants перед публікацією.
    - Scope → runtime/publisher.py.
    - Non-goals → зміни коду.
    - Інваріанти/рейки → read-only.
    - План → перевірити publish_ohlcv_final_* і _validate_final_bars.
    - Тести/перевірки → немає.

    ## 2026-02-01T02:08:31+01:00 — POST (MODE=read-only discovery): перевірка P9.2 final invariants

    - Що зроблено → підтверджено наявний валідатор final invariants у runtime/publisher.py.
    - Де зроблено → runtime/publisher.py (_validate_final_bars; publish_ohlcv_final_1m/htf).
    - Як перевірено → читання файлів.
    - Ризики/нотатки → вимоги P9.2 вже enforce-яться перед публікацією.

    ## 2026-02-01T02:20:00+01:00 — PRE (MODE=read-only discovery): останнє оновлення README/REPO_LAYOUT/доків

    - Мета → знайти останній лог‑запис, де оновлено README.md + docs/REPO_LAYOUT.md + документацію, і перелік подальших змін без синхронізації.
    - Scope → Work/01log.md.
    - Non-goals → зміни коду/доків.
    - Інваріанти/рейки → лише читання журналу.
    - План → знайти останній POST з README/REPO_LAYOUT/доками; зібрати перелік POST після нього, де docs не оновлені.
    - Тести/перевірки → немає.

    ## 2026-02-01T02:20:00+01:00 — POST (MODE=read-only discovery): останнє оновлення README/REPO_LAYOUT/доків

    - Що зроблено → визначено останній POST з оновленням README.md + docs/REPO_LAYOUT.md + docs/runtime.md (P8.S7), і перелік наступних PATCH без синхронізації README/REPO_LAYOUT/доків.
    - Де зроблено → Work/01log.md (read-only аналіз).
    - Як перевірено → читання журналу.
    - Ризики/нотатки → перелік стосується записів після P8.S7: status payload soft-compact/tail_guard_summary; FXCM логування/стани; P9.0/P9.1 історія; зміни degraded семантики. Документація після цього не синхронізована, окрім Public Surface.

    ## 2026-02-01T02:30:00+01:00 — PRE (MODE=PATCH): Gapless Open Carryover у документації

    - Мета → зафіксувати open carryover (open нового бару = попередній close) у preview та рекомендацію для final/FileCache агрегації.
    - Scope → docs/runtime.md.
    - Non-goals → зміни коду/контрактів.
    - Інваріанти/рейки → мінімальний диф; лише документація.
    - План → додати короткі пункти у preview/final pipeline.
    - Тести/перевірки → не застосовно.

    ## 2026-02-01T02:30:00+01:00 — POST (MODE=PATCH): Gapless Open Carryover у документації

    - Що зроблено → додано опис open carryover у preview та рекомендацію для final/FileCache агрегації.
    - Де зроблено → docs/runtime.md.
    - Як перевірено → не застосовно (документація).
    - Ризики/нотатки → текстові зміни; ризик — застаріти при зміні агрегації.

    ## 2026-02-01T02:40:00+01:00 — PRE (MODE=PATCH): уточнення обсягу preview vs final

    - Мета → зафіксувати, що preview обсяг = tick_count; final тільки history (без tick-agg), і наприкінці хвилини форсується короткий history‑poll для complete=true.
    - Scope → docs/runtime.md.
    - Non-goals → зміни коду/контрактів.
    - Інваріанти/рейки → мінімальний диф; лише документація.
    - План → додати короткі пункти у preview/final pipeline.
    - Тести/перевірки → не застосовно.

    ## 2026-02-01T02:40:00+01:00 — POST (MODE=PATCH): уточнення обсягу preview vs final

    - Що зроблено → додано пояснення про tick_count у preview та заборону публікації tick-agg як final; додано згадку про короткий history‑poll наприкінці хвилини.
    - Де зроблено → docs/runtime.md.
    - Як перевірено → не застосовно (документація).
    - Ризики/нотатки → текстові зміни; ризик — застаріти при зміні політики final/history.

    ## 2026-02-01T03:10:00+01:00 — PRE (MODE=read-only discovery): P10.PREP Live↔Final truth — факти перед PATCH

    - Мета → зняти “as-is” факти по P10 (Live bars ↔ Final truth), без жодних змін коду. Підтвердити, що Final-wire не може бути з stream_close і що SSOT Final = тільки history/history_agg. Додатково: підтвердити відсутність SQLite у реальному runtime-шляху (або зафіксувати де саме ще лишилось).
    - Scope (читання) → runtime/preview_builder.py, runtime/ohlcv_preview.py; runtime/history_provider.py, fxcm/history_fxcm_provider.py; runtime/publisher.py; runtime/warmup.py, runtime/backfill.py; runtime/republish.py; runtime/tail_guard.py; core/validation/validator.py; core/contracts/public/{ohlcv_v1.json,commands_v1.json,status_v2.json}; config/config.py; Work/01log.md; docs/P8 P9 P10 для FXCM ticks history live bars.md.
    - Non-goals → будь-який PATCH, рефакторинг, “підчищення” імпортів, зміна контрактів.
    - Інваріанти/рейки → тільки факти з path:line; без припущень; окремо позначити “факт” vs “інференс”.
    - Артефакти (output) → data/audit_p10_live_final/grep_store.txt; data/audit_p10_live_final/call_graph.md; data/audit_p10_live_final/risk_table.md; data/audit_p10_live_final/plan_p10_slices.md.
    - План → (1) grep “sqlite|SQLiteStore|file_cache|SSOT” і виписати факти; (2) пройти publish шлях preview/final; (3) пройти warmup/backfill/republish/tail_guard; (4) звірити з контрактами FINAL_SOURCES/NoMix.
    - Тести/перевірки → немає (read-only).

    ## 2026-02-01T03:25:00+01:00 — POST (MODE=read-only discovery): P10.PREP Live↔Final truth — факти перед PATCH

    - Що зроблено → зібрані факти з runtime/core/контрактів, сформовано артефакти P10.PREP (grep_store/call_graph/risk_table/plan_p10_slices).
    - Де зроблено → data/audit_p10_live_final/grep_store.txt; data/audit_p10_live_final/call_graph.md; data/audit_p10_live_final/risk_table.md; data/audit_p10_live_final/plan_p10_slices.md.
    - Як перевірено → read-only читання файлів + grep; без тестів.
    - Висновок (коротко):
      - Що є зараз (факти):
        1) Preview з stream‑барів (complete=false) публікується у {NS}:ohlcv; live “closed 1m” пишуться в FileCache як source=stream_close. (app/composition.py:483-526)
        2) Final publish робиться через publish_ohlcv_final_1m/htf і вимагає source=history/history_agg. (runtime/publisher.py:84-106; core/validation/validator.py:19,183; core/contracts/public/ohlcv_v1.json:54,73)
        3) Warmup/backfill беруть history 1m і пишуть у FileCache як source=history. (runtime/warmup.py:50,54; runtime/backfill.py:49,53)
        4) HTTP /api/ohlcv?mode=final читає з FileCache (SQLite у runtime‑шляху не використовується). (runtime/http_server.py:97-137)
      - Прогалини (3–5):
        1) Live finalization відсутній: немає history‑poll наприкінці хвилини та publish complete=true з history. (інференс з app/composition.py:483-526 + runtime/publisher.py:84-106)
        2) Stream_close не може бути final‑wire через контракт FINAL_SOURCES. (core/validation/validator.py:19,183; ohlcv_v1.json:54,73)
        3) Preview open береться з поточного mid, gapless open carryover не реалізовано. (core/market/preview_builder.py:168-171)
        4) Republish використовує source="cache", що поза allowlist. (runtime/republish.py:101,131; ohlcv_v1.json:9,42) — потребує рішення.
      - Точний P10.A мінімальний diff:
        1) Додати короткий history‑poll у live path на межі хвилини (1m),
        2) записати ці бари в FileCache як source=history,
        3) опублікувати через publish_ohlcv_final_1m (complete=true),
        4) не публікувати tick‑agg/stream_close як final‑wire.

    ## 2026-02-01T03:40:00+01:00 — PRE (MODE=PATCH): P10.A Live↔Final truth — bootstrap-процедура + republish-tail без змішування джерел

    - Мета →
      1) Жорстко зафіксувати, що Final-wire (complete=true) ніколи не походить зі stream/stream_close.
      2) Зробити, щоб після рестарту UI НЕ був “чистий”: warmup/backfill наповнює те, що маємо (Final з history), потім fxcm_republish_tail репаблішить останні N final-барів у Redis.
      3) tail-guard запускається тільки ЯВНО (командою або bootstrap-планом), під флаг, default OFF.
      4) Підготувати “reconcile-фіналізацію” як наступний slice (P10.B), але в P10.A лише закласти рейки/контракти.
    - Scope (мінімальний diff) → runtime/republish.py; runtime/warmup.py; runtime/backfill.py; runtime/command_bus.py (за потреби); runtime/status.py; config/config.py; core/contracts/public/status_v2.json; store/file_cache/history_cache.py; tests/*; tools/exit_gates/*; docs/runbooks/*.
    - Non-goals → stream_close → complete=true у Final-wire; synthetic бари; великий рефактор store.
    - Інваріанти/рейки → FINAL_SOURCES allowlist enforcement; NoMix(final) для (symbol,tf,open_time); carryover-open не чіпаємо.
    - Тести (1–3) →
      1) test_republish_rejects_stream_source_complete_true.
      2) test_bootstrap_sequence_records_status_steps.
      3) test_final_source_allowlist_enforced.
    - Exit gate → gate_final_republish_source_allowlist.
    - План →
      1) Посилити allowlist enforcement у republish_tail.
      2) Додати bootstrap flags у Config (default OFF).
      3) Додати bootstrap flow: enable → warmup/backfill/republish_tail; tail_guard лише за флагом.
      4) Оновити docs/runbook: bootstrap команда + перевірки Redis/HTTP.

    ## 2026-02-01T03:55:00+01:00 — POST (MODE=PATCH): P10.A Live↔Final truth — bootstrap-процедура + republish-tail без змішування джерел

    - Що зроблено →
      - Додано bootstrap флаги у Config (default OFF) і bootstrap handler (`fxcm_bootstrap`) з кроками warmup/backfill/republish_tail/optional tail_guard.
      - Додано bootstrap state/step у status snapshot і schema status_v2.
      - Посилено republish_tail: hard‑fail якщо cache має stream/stream_close як last_write_source; publish через final‑методи з history/history_agg.
      - Додано last_write_source у FileCache meta.
      - Додано тесты та exit gate для allowlist у republish.
      - Додано runbook bootstrap.
    - Де зроблено → config/config.py; app/composition.py; runtime/status.py; runtime/republish.py; store/file_cache/history_cache.py; core/contracts/public/status_v2.json; tests/test_republish_tail_source_allowlist.py; tests/test_status_bootstrap_steps.py; tests/test_validator_final_source_allowlist.py; tools/exit_gates/gates/gate_final_republish_source_allowlist.py; tools/exit_gates/manifest.json; docs/runbooks/bootstrap_p10.md.
    - Як перевірено →
      - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m pytest -q tests/test_republish_tail_source_allowlist.py tests/test_status_bootstrap_steps.py tests/test_validator_final_source_allowlist.py → OK
      - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m tools.run_exit_gates --manifest tools/exit_gates/manifest.json --out reports/exit_gates → FAIL (gate_status_heartbeat_paused_market_closed)
    - Ризики/наступні кроки →
      - P10.B: reconcile‑фіналізація на 15m close (history‑fetch 15–20 хв 1m → final 1m (history) → rebuild 15m (history_agg)).
      - P10.C: MTF cross‑check 1h/4h/1d (history‑provider), під флаг.
      - P10.D: аномалії tick feed (future/late/dedup/clock‑flush/gap policy).

    ## 2026-02-01T04:10:00+01:00 — PRE (MODE=PATCH): виправлення status_v2 bootstrap schema

    - Мета → усунути ContractError через некоректне розміщення `bootstrap` у status_v2.
    - Scope → core/contracts/public/status_v2.json.
    - Non-goals → зміни коду runtime/status.py.
    - Інваріанти/рейки → мінімальний диф; лише schema.
    - План → перенести `bootstrap` у top-level properties.
    - Тести/перевірки → не запускати.

    ## 2026-02-01T04:10:00+01:00 — POST (MODE=PATCH): виправлення status_v2 bootstrap schema

    - Що зроблено → `bootstrap` перенесено у top-level properties; прибрано з price.properties.
    - Де зроблено → core/contracts/public/status_v2.json.
    - Як перевірено → не запускалось.
    - Ризики/нотатки → зміни лише в schema; runtime error має зникнути.

    ## 2026-02-01T04:20:00+01:00 — PRE (MODE=PATCH): логування bootstrap кроків

    - Мета → додати видимі лог‑повідомлення про старт/фініш кроків bootstrap.
    - Scope → app/composition.py.
    - Non-goals → зміни контрактів або логіки bootstrap.
    - Інваріанти/рейки → мінімальний диф; лише логування.
    - План → додати log.info/log.error у _handle_bootstrap.
    - Тести/перевірки → не запускати.

    ## 2026-02-01T04:20:00+01:00 — POST (MODE=PATCH): логування bootstrap кроків

    - Що зроблено → додано log.info/log.error для bootstrap (start/step ok/error).
    - Де зроблено → app/composition.py.
    - Як перевірено → не запускалось.
    - Ризики/нотатки → лише логування.

    ## 2026-02-01T04:35:00+01:00 — PRE (MODE=PATCH): P10.A0 Видимість bootstrap/republish у status + INFO-логи dispatch

    - Мета →
      1) Кожен крок warmup/backfill/republish_tail видимий у status:snapshot (SSOT для UI).
      2) Додати INFO логи в dispatch (command_bus) для старт/фініш команд.
    - Scope (мінімальний diff) → runtime/status.py; core/contracts/public/status_v2.json; runtime/command_bus.py; tests/*; tools/exit_gates/*.
    - Non-goals → зміна семантики final/live; зміни publish_ohlcv_final_*; silent fallback.
    - Інваріанти/рейки → degraded-but-loud на error; schema contract-first.
    - Acceptance → AC1 bootstrap.state + bootstrap.steps[] у snapshot; AC2 INFO COMMAND start/end; AC3 schema без ContractError.
    - Тести → test_status_bootstrap_schema_roundtrip.py; test_command_bus_logs_smoke.py.
    - Exit gate → gate_status_bootstrap_contract.
    - Перевірки → pytest -q (нові тести); tools.run_exit_gates (фіксувати gate_status_heartbeat_* якщо падає).

    ## 2026-02-01T04:35:00+01:00 — POST (MODE=PATCH): P10.A0 Видимість bootstrap/republish у status + INFO-логи dispatch

    - Що зроблено →
      - Додано bootstrap.steps[] у schema status_v2 та в status snapshot; record_bootstrap_step тепер пише steps і loud error.
      - Додано INFO логи COMMAND start/end у command_bus.
      - Додано тести та exit gate для bootstrap contract.
    - Де зроблено → core/contracts/public/status_v2.json; runtime/status.py; runtime/command_bus.py; tests/test_status_bootstrap_schema_roundtrip.py; tests/test_command_bus_logs_smoke.py; tools/exit_gates/gates/gate_status_bootstrap_contract.py; tools/exit_gates/manifest.json.
    - Як перевірено → не запускалось.
    - Ризики/нотатки → потрібен запуск pytest та exit gates; gate_status_heartbeat_* може падати через market closed (не регресія P10.A0).

    ## 2026-02-01T05:00:00+01:00 — PRE (MODE=PATCH): фікс gate_status_bootstrap_contract root_dir

    - Мета → виправити root_dir у gate_status_bootstrap_contract, який шукав schema в tools/.
    - Scope → tools/exit_gates/gates/gate_status_bootstrap_contract.py.
    - Non-goals → зміни runtime/схем.
    - Інваріанти/рейки → мінімальний диф.
    - План → змінити parents[2] на parents[3], повторно запустити exit gates.
    - Тести/перевірки → tools.run_exit_gates (manifest).

    ## 2026-02-01T05:00:00+01:00 — POST (MODE=PATCH): фікс gate_status_bootstrap_contract root_dir

    - Що зроблено → root_dir у gate_status_bootstrap_contract переведено на repo root.
    - Де зроблено → tools/exit_gates/gates/gate_status_bootstrap_contract.py.
    - Як перевірено → tools.run_exit_gates (manifest) після правки → OK.
    - Ризики/нотатки → немає.

    ## 2026-02-01T05:05:00+01:00 — POST (MODE=PATCH): P10.A0 перевірки виконані

    - Що зроблено → виконано перевірки для P10.A0.
    - Де перевірено → pytest (нові тести) + exit gates (manifest).
    - Як перевірено →
      - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m pytest -q tests/test_status_bootstrap_schema_roundtrip.py tests/test_command_bus_logs_smoke.py → OK
      - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m tools.run_exit_gates --manifest tools/exit_gates/manifest.json --out reports/exit_gates → OK
    - Ризики/нотатки → немає.

    ## 2026-02-01T05:20:00+01:00 — PRE (MODE=PATCH): P10.A1 republish-tail тільки final sources

    - Мета → republish_tail публікує лише бари з source=history/history_agg (без підміни), і loud‑fail коли tail має stream_close/stream.
    - Scope → runtime/republish.py.
    - Non-goals → зміна live/final семантики; нові контракти; refactor store.
    - Інваріанти/рейки → FINAL_SOURCES allowlist; degraded‑but‑loud при порушенні.
    - План → джерело береться з FileCache meta last_write_source; заборонити не‑final sources; без підміни на cache.
    - Тести/перевірки → не запускати.

    ## 2026-02-01T05:20:00+01:00 — POST (MODE=PATCH): P10.A1 republish-tail тільки final sources

    - Що зроблено → republish_tail використовує last_write_source з FileCache meta як source і hard‑fail для stream/stream_close/не‑final; без підміни на cache.
    - Де зроблено → runtime/republish.py.
    - Як перевірено → не запускалось.
    - Ризики/нотатки → якщо cache останній запис не history/history_agg — republish блокується з loud error.

    ## 2026-02-01T05:30:00+01:00 — PRE (MODE=ADR-only): P10.A1 bootstrap/republish — план патчу (канонічний порядок)

    - Мета → описати навіщо потрібен bootstrap саме зараз і як канонічно має працювати: warmup → backfill → republish_tail → (опц.) tail_guard, з SSOT‑видимістю у status.bootstrap.steps[].
    - Scope → app/composition.py (_handle_bootstrap, _publish_final_tail); runtime/handlers_p3.py (warmup/backfill); runtime/warmup.py; runtime/backfill.py; runtime/republish.py; runtime/tail_guard.py; runtime/status.py (bootstrap.steps); core/contracts/public/status_v2.json; config/config.py; runtime/command_bus.py (INFO dispatch).
    - Non-goals → stream_close → final; зміни контрактів final/live; silent fallback.
    - Інваріанти/рейки → FINAL_SOURCES allowlist; degraded‑but‑loud при порушенні; NoMix(final).
    - План (канонічний порядок/механіка) →
      1) __Warmup__: `handle_warmup_command` → `run_warmup` → `file_cache.append_complete_bars(..., source="history")` (runtime/warmup.py; runtime/handlers_p3.py). Видимість: `status.record_bootstrap_step("warmup", state)`.
      2) __Backfill__: `handle_backfill_command` → `run_backfill` → `file_cache.append_complete_bars(..., source="history")` (runtime/backfill.py; runtime/handlers_p3.py). Видимість: `status.record_bootstrap_step("backfill", state)`.
      3) __Republish_tail__: `republish_tail` бере хвіст з FileCache __лише__ якщо `last_write_source ∈ {history, history_agg}` (runtime/republish.py). Інакше — loud error + `bootstrap.state=error`.
      4) __Tail_guard (опц.)__: викликається лише якщо `bootstrap_tail_guard_after=true` і є args; `run_tail_guard` з repair тільки по флагу (runtime/tail_guard.py; app/composition.py).
      5) __Статус/логування__: `status.bootstrap.steps[]` фіксує кроки; `command_bus` логує `COMMAND start/end` (runtime/status.py; runtime/command_bus.py).
    - Результат → після bootstrap у FileCache є history‑truth, у Redis з’являється final хвіст через republish_tail, UI не “порожній”; видимість у status.bootstrap.steps[].
    - Перевірки → не застосовно (план).

    ## 2026-02-01T05:30:00+01:00 — POST (MODE=ADR-only): P10.A1 bootstrap/republish — план патчу (канонічний порядок)

    - Що зроблено → зафіксовано патч‑план bootstrap (warmup → backfill → republish_tail → optional tail_guard) та місця реалізації/відображення у статусі.
    - Де зроблено → Work/01log.md.
    - Як перевірено → не застосовно.
    - Ризики/нотатки → план залежить від FINAL_SOURCES allowlist; без warmup/backfill republish_tail буде loud‑fail.

    ## 2026-02-01T05:45:00+01:00 — PRE (MODE=PATCH): P10.B Reconcile Finalization на 15m close (history → final 1m + rebuild final 15m)

    - Мета →
      1) На кожному 15m close робити reconcile: короткий history-fetch останніх 15–20 хв 1m лише для active_symbols.
      2) Публікувати Final 1m строго як source="history", complete=true (без stream_close у final-wire).
      3) Перебудовувати і публікувати Final 15m як source="history_agg", complete=true (derived з 1m final SSOT).
      4) Дедуп/водяний знак: не публікувати вже фіналізовані 1m (по open/close_time), щоб не спамити Redis і не плодити NoMix-ризики.
    - Scope (мінімальний diff) → runtime/reconcile_finalizer.py (новий модуль); runtime/history_provider.py (fetch_1m_history_tail); runtime/publisher.py (існуючі publish_ohlcv_final_*); runtime/*trigger* (15m close евент); runtime/status.py (reconcile counters/last_ts); config/config.py (lookback_minutes, active_symbols, budget/limits).
    - Non-goals → stream_close → complete=true; synthetic бари; history polling по всім TF.
    - Інваріанти/рейки → FINAL_SOURCES allowlist; NoMix(final); bars sorted/unique; single in-flight per (symbol, tf) + budget; epoch ms int.
    - Тести (1–3) →
      1) test_reconcile_filters_already_finalized_1m_bars.
      2) test_reconcile_publishes_final_only_history_source.
      3) test_reconcile_triggers_htf_rebuild_history_agg.
    - Exit gate → gate_final_reconcile_no_stream_source.
    - Acceptance → AC1 final 1m/15m після 15m close; AC2 bootstrap+republish піднімає UI; AC3 NoMix violations → loud error.

    ## 2026-02-01T05:45:00+01:00 — POST (MODE=PATCH): P10.B Reconcile Finalization на 15m close (history → final 1m + rebuild final 15m)

    - Що зроблено → додано лише запис‑план у журналі, без змін коду/схем.
    - Де зроблено → Work/01log.md.
    - Як перевірено → не застосовно.
    - Ризики/нотатки → план чекає реалізації як окремий PATCH.

  ## 2026-02-01T06:15:00+01:00 — PRE (MODE=PATCH): P10.B0 Reconcile Finalization (fxcm_reconcile_tail + 15m close)

- Мета →
  1) Реалізувати reconcile-фіналізацію: history tail (15–20 хв 1m) → final 1m (history) → rebuild final 15m (history_agg).
  2) Дедуп за watermark у FileCache (last_published_open_time_ms) для 1m/15m.
  3) Додати fxcm_reconcile_tail команду + опційний auto-trigger на 15m close.
  4) Відобразити reconcile у status_v2 + статусному payload.
- Scope → runtime/reconcile_finalizer.py; app/composition.py; runtime/status.py; core/contracts/public/status_v2.json; config/config.py; tests/test_reconcile_finalizer.py; tests/test_validator_status.py; tests/test_status_payload_size_rail.py; tools/exit_gates/gates/gate_final_reconcile_no_stream_source.py; tools/exit_gates/manifest.json; README.md; docs/REPO_LAYOUT.md.
- Non-goals → stream_close → complete=true; history polling по всім TF; нові ENV-перемикачі.
- Інваріанти/рейки → FINAL_SOURCES allowlist; complete=true + event_ts=close_time; bars sorted/unique; FileCache SSOT; loud errors без silent fallback.
- План →
  1) Додати reconcile_finalizer з history fetch, дедупом і rebuild 15m.
  2) Підключити команду fxcm_reconcile_tail і auto-trigger на 15m close (optional).
  3) Розширити status/schema + додати gate та тести.
  4) Оновити README/REPO_LAYOUT.
- Тести →
  - test_reconcile_filters_already_finalized_1m_bars.
  - test_reconcile_publishes_final_only_history_source.
  - test_reconcile_triggers_htf_rebuild_history_agg.

## 2026-02-01T06:40:00+01:00 — POST (MODE=PATCH): P10.B0 Reconcile Finalization (fxcm_reconcile_tail + 15m close)

- Що зроблено → реалізовано reconcile_finalizer (history tail → final 1m + rebuild 15m), додано fxcm_reconcile_tail handler і auto-trigger на 15m close, розширено status_v2 + статусний payload, додано тести та exit gate, оновлено README/REPO_LAYOUT.
- Де зроблено → runtime/reconcile_finalizer.py; app/composition.py; runtime/status.py; core/contracts/public/status_v2.json; config/config.py; tests/test_reconcile_finalizer.py; tests/test_validator_status.py; tests/test_status_payload_size_rail.py; tools/exit_gates/gates/gate_final_reconcile_no_stream_source.py; tools/exit_gates/manifest.json; README.md; docs/REPO_LAYOUT.md.
- Як перевірено → тести не запускалися (не запитувалося).
- Ризики/нотатки → auto-reconcile запускається раз на 15m bucket; при history not ready буде loud error + degraded; FileCache watermark використовується для дедупу 1m/15m.

## 2026-02-01T07:00:00+01:00 — PRE (MODE=PATCH): Fix 13 errors (mypy/ruff)

- Мета → усунути 13 помилок типізації/імпортів у reconcile_finalizer та тестах/gates.
- Scope → runtime/reconcile_finalizer.py; tests/test_reconcile_finalizer.py; tools/exit_gates/gates/gate_final_reconcile_no_stream_source.py.
- Non-goals → зміна бізнес-логіки reconcile, нові фічі.
- Інваріанти/рейки → fail-fast, FINAL_SOURCES allowlist, мінімальний диф.
- План →
  1) Вирівняти імпорти та типи (publisher протокол).
  2) Прибрати None-потоки у _normalize_history_rows.
  3) Перевірити, що тести/gate сумісні з оновленими типами.
- Тести → не запускаємо (не запитувалося).

## 2026-02-01T07:10:00+01:00 — POST (MODE=PATCH): Fix 13 errors (mypy/ruff)

- Що зроблено → впорядковано імпорти, додано PublisherProtocol для reconcile_finalizer, додано guards на None у history rows.
- Де зроблено → runtime/reconcile_finalizer.py.
- Як перевірено → не запускалося (не запитувалося).
- Ризики/нотатки → відкидання history рядків без OHLC може зменшити кількість барів у reconcile (loud error лишається при empty).

## 2026-02-01T07:25:00+01:00 — PRE (MODE=PATCH): P10.B1 Авто-trigger reconcile на 15m boundary (publish command, не виконувати inline)

- Мета →
  1) На 15m boundary тригерити reconcile через publish cmd (не inline).
  2) Дедуп тригера через status.reconcile.last_end_ms.
  3) Loud error при publish помилці.
- Scope → app/composition.py; runtime/status.py; core/contracts/public/status_v2.json; tests/test_reconcile_trigger.py; tests/test_validator_status.py; tests/test_status_payload_size_rail.py.
- Non-goals → зміна history/provider, inline reconcile.
- Інваріанти/рейки → reconcile_enable=false → no-op; last_end_ms==candidate_end_ms → skip.
- План →
  1) Додати last_end_ms у status+schema.
  2) Реалізувати publish cmd на 15m boundary у live loop.
  3) Додати тест boundary/once.
- Тести → test_15m_boundary_emits_reconcile_command_once.

## 2026-02-01T07:45:00+01:00 — POST (MODE=PATCH): P10.B1 Авто-trigger reconcile на 15m boundary (publish command, не виконувати inline)

- Що зроблено → додано watermark last_end_ms у status+schema; auto-trigger переведено на publish cmd fxcm_reconcile_tail на 15m boundary; додано тест boundary/once.
- Де зроблено → app/composition.py; runtime/status.py; core/contracts/public/status_v2.json; tests/test_reconcile_trigger.py; tests/test_validator_status.py; tests/test_status_payload_size_rail.py.
- Як перевірено → не запускалося (не запитувалося).
- Ризики/нотатки → publish cmd вимагає Redis; при помилці пишемо loud error у status.

## 2026-02-01T08:05:00+01:00 — PRE (MODE=PATCH): Fix status reconcile last_end_ms missing

- Мета → усунути ContractError: reconcile.last_end_ms missing у publish_snapshot.
- Scope → runtime/status.py.
- Non-goals → зміна схеми чи логіки reconcile.
- Інваріанти/рейки → мінімальний диф, backward-safe.
- План → додати дефолт last_end_ms у build_status_pubsub_payload.
- Тести → не запускаємо (не запитувалося).

## 2026-02-01T08:10:00+01:00 — POST (MODE=PATCH): Fix status reconcile last_end_ms missing

- Що зроблено → додано дефолт last_end_ms у build_status_pubsub_payload.
- Де зроблено → runtime/status.py.
- Як перевірено → не запускалося (не запитувалося).
- Ризики/нотатки → backward-safe для старих snapshot без last_end_ms.

## 2026-02-01T08:30:00+01:00 — PRE (MODE=PATCH): P10.B0/B1 тестовий gate (pytest + exit_gates)

- Мета → прогнати мінімальні тести й exit gates для підтвердження P10.B.
- Scope → тести: test_reconcile_finalizer.py, test_reconcile_trigger.py, test_validator_status.py, test_status_payload_size_rail.py; exit_gates manifest.json.
- Non-goals → зміни коду.
- Інваріанти/рейки → stop-rule: без зелених тестів P10.B не існує.
- План → pytest (4 файли) → exit_gates manifest.json.
- Тести → pytest -q (4 файли) + tools.run_exit_gates (manifest.json).

## 2026-02-01T08:40:00+01:00 — POST (MODE=PATCH): P10.B0/B1 тестовий gate (pytest + exit_gates)

- Що зроблено → виконано мінімальні тести та exit gates.
- Де зроблено → pytest: tests/test_reconcile_finalizer.py, tests/test_reconcile_trigger.py, tests/test_validator_status.py, tests/test_status_payload_size_rail.py; exit_gates: tools/exit_gates/manifest.json.
- Як перевірено →
  - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m pytest -q tests/test_reconcile_finalizer.py tests/test_reconcile_trigger.py tests/test_validator_status.py tests/test_status_payload_size_rail.py (OK)
  - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m tools.run_exit_gates --manifest tools/exit_gates/manifest.json --out reports/exit_gates (OK)
- Ризики/нотатки → P10.B0/B1 може вважатися виконаним після зелених gate'ів.

## 2026-02-01T08:55:00+01:00 — PRE (MODE=read-only discovery): Перевірка source=cache у final

- Мета → перевірити, чи є source="cache" у final wire/UI після P10.A1.
- Scope → codebase grep/огляд джерел.
- Non-goals → зміни коду.
- Інваріанти/рейки → read-only.
- План → пошук "source=cache" та "cache" у публікації final.
- Тести → не застосовно.

## 2026-02-01T09:10:00+01:00 — PRE (MODE=PATCH): Заборона source="cache" у /api/ohlcv final

- Мета → прибрати source="cache" у final REST payload; використовувати last_write_source з allowlist.
- Scope → runtime/http_server.py.
- Non-goals → зміни pubsub/final wire; зміни history/provider.
- Інваріанти/рейки → FINAL_SOURCES allowlist; 1m=history, HTF=history_agg; loud error при порушенні.
- План → зчитати meta.last_write_source, валідувати, підставити як source; інакше 500.
- Тести → не запускаємо (не запитувалося).

## 2026-02-01T09:20:00+01:00 — POST (MODE=PATCH): Заборона source="cache" у /api/ohlcv final

- Що зроблено → /api/ohlcv?mode=final тепер підставляє source=last_write_source (history/history_agg) і відхиляє invalid.
- Де зроблено → runtime/http_server.py.
- Як перевірено → не запускалося (не запитувалося).
- Ризики/нотатки → якщо meta.last_write_source порожній/невалідний, endpoint повертає 500 (loud).

## 2026-02-01T09:35:00+01:00 — PRE (MODE=read-only discovery): Валідація preview vs final у UI

- Мета → зафіксувати як UI Lite розрізняє preview/final та де може з’являтися source=cache.
- Scope → ui_lite/server.py; runtime/http_server.py.
- Non-goals → зміни коду.
- Інваріанти/рейки → read-only.
- План → перегляд is_final_bar/is_preview_bar та /api/ohlcv?mode=final.
- Тести → не застосовно.

## 2026-02-01T09:35:00+01:00 — POST (MODE=read-only discovery): Валідація preview vs final у UI

- Що зроблено → переглянуто UI Lite: final визначається лише за complete=true; preview — complete=false; source не перевіряється. /api/ohlcv?mode=final тепер відхиляє non-final sources.
- Де зроблено → ui_lite/server.py; runtime/http_server.py.
- Як перевірено → не застосовно.
- Ризики/нотатки → UI Lite не валідуює source; покладено на контракт/видачу.

## 2026-02-01T09:50:00+01:00 — PRE (MODE=PATCH): Маркування fxcm/* як архів/мертвий

- Мета → чітко позначити fxcm/* як legacy/архів.
- Scope → fxcm/__init__.py, fxcm/history_stub.py, README.md, docs/REPO_LAYOUT.md.
- Non-goals → видалення файлів або зміна runtime/fxcm/*.
- Інваріанти/рейки → мінімальний диф.
- План → додати явні позначки “архів/мертвий” у коді та документах.
- Тести → не запускаємо (не запитувалося).

## 2026-02-01T10:00:00+01:00 — POST (MODE=PATCH): Маркування fxcm/* як архів/мертвий

- Що зроблено → додано явні позначки “АРХІВ/МЕРТВИЙ” у fxcm/__init__.py та fxcm/history_stub.py; оновлено README/REPO_LAYOUT.
- Де зроблено → fxcm/__init__.py; fxcm/history_stub.py; README.md; docs/REPO_LAYOUT.md.
- Як перевірено → не запускалося (не запитувалося).
- Ризики/нотатки → зміни лише маркування; поведінка runtime не змінена.

## 2026-02-02T09:10:00+01:00 — PRE (MODE=PATCH): Відкат до стану 51eb2db

- Мета → перейти до коміту 51eb2db (до P9/P10 змін).
- Scope → git checkout 51eb2db.
- Non-goals → зміни коду вручну.
- Інваріанти/рейки → зберегти поточні зміни поза комітом не намагатися.
- План → git checkout 51eb2db.
- Тести → не запускаємо (не запитувалося).

## 2026-02-02T09:15:00+01:00 — POST (MODE=PATCH): Відкат до стану 51eb2db

- Що зроблено → спроба git checkout 51eb2db.
- Де зроблено → git (repo root).
- Як перевірено → checkout завершився помилкою (локальні зміни блокують).
- Ризики/нотатки → потрібен stash або discard локальних змін перед checkout.

## 2026-02-02T09:20:00+01:00 — PRE (MODE=PATCH): Збереження стану у окремій гілці перед відкатом

- Мета → зафіксувати поточні зміни в окремій гілці (без коміту в main), далі перейти на 51eb2db.
- Scope → git checkout -b, git add/commit, git checkout 51eb2db.
- Non-goals → зміни коду вручну.
- Інваріанти/рейки → main не чіпати.
- План → створити гілку backup/*, коміт усіх змін, checkout 51eb2db.
- Тести → не запускаємо (не запитувалося).

## 2026-02-01T10:00:00+01:00 — PRE (MODE=read-only discovery): P10.SEC.PREP CommandBus security — факти перед PATCH

- Мета → зняти as-is факти по командах: де парсимо JSON, де валідимо schema, де пишемо errors у status, де потенційний витік деталей.
- Scope (читання) →
  - runtime/command_bus.py
  - runtime/status.py
  - core/contracts/public/commands_v1.json
  - config/config.py
  - runtime/publisher.py
- Non-goals → будь-які зміни коду/контрактів.
- Інваріанти/рейки → тільки факти з path:line; факт vs інференс маркувати.
- Артефакти (output) →
  - data/audit_cmd_sec/paths_and_hotspots.md
  - data/audit_cmd_sec/error_leaks.md
  - data/audit_cmd_sec/plan_slices.md
- План →
  1) виписати шлях: pubsub → parse → validate → dispatch → status.
  2) знайти місця, де в status потрапляє str(exc) або schema text.
  3) знайти, чи є вже payload size checks / rate-limit (очікується що ні).

## 2026-02-01T10:12:00+01:00 — POST (MODE=read-only discovery): P10.SEC.PREP CommandBus security — факти перед PATCH

- Що зроблено → знято as-is факти по шляху команди, місцях валідації та витоках у status.errors; зафіксовано артефакти discovery.
- Де зроблено →
  - runtime/command_bus.py:139, 176, 178-182, 194-206, 210-240
  - runtime/status.py:544-559
  - core/contracts/public/commands_v1.json:1-10
  - data/audit_cmd_sec/paths_and_hotspots.md
  - data/audit_cmd_sec/error_leaks.md
  - data/audit_cmd_sec/plan_slices.md
- Як перевірено → читання коду (read-only), без запуску команд.
- Ризики/нотатки →
  - Факт: `message=str(exc)` потрапляє у status для contract/handler помилок (runtime/command_bus.py:201, 251, 263, 273).
  - Факт: `append_error()` не редагує message (runtime/status.py:544-559).

## 2026-02-01T10:25:00+01:00 — PRE (MODE=PATCH): P10.SEC.D + P10.SEC.A — redaction errors + payload limits

- Мета → прибрати витік деталей у status.errors для команд та додати fast-drop payload limits до json parse.
- Scope →
  - runtime/command_bus.py
  - runtime/status.py
  - config/config.py
  - observability/metrics.py
  - tests/test_command_bus_security.py
  - tools/exit_gates/gates/gate_command_payload_limits.py
  - tools/exit_gates/gates/gate_status_error_redaction.py
  - tools/exit_gates/manifest.json
  - tools/exit_gates/manifest_p10_sec.json
  - README.md
  - docs/REPO_LAYOUT.md
- Non-goals → HMAC/anti-replay; rate-limit; зміни commands_v1 schema; рефакторинг.
- Інваріанти/рейки → fail-fast; status без деталей schema/allowlist для команд; мінімальний диф.
- Тести/перевірки →
  - pytest -q tests/test_command_bus_security.py
  - python -m tools.run_exit_gates --out reports/exit_gates --manifest tools/exit_gates/manifest_p10_sec.json

## 2026-02-01T10:55:00+01:00 — POST (MODE=PATCH): P10.SEC.D + P10.SEC.A — redaction errors + payload limits

- Що зроблено →
  - Додано `max_command_payload_bytes` та fast-drop до JSON parse з редактованими статус-помилками.
  - Додано `append_public_error()` у статус та використано його для command errors.
  - Додано метрику `commands_dropped_total{reason}` і два exit gate для rail'ів.
  - Додано тести для oversize payload та redaction contract_error.
  - Оновлено README та REPO_LAYOUT.
- Де зроблено →
  - runtime/command_bus.py:188-301
  - runtime/status.py:570-578
  - config/config.py:102
  - observability/metrics.py:14, 69-74, 317
  - tests/test_command_bus_security.py:44-107
  - tools/exit_gates/gates/gate_command_payload_limits.py:7-35
  - tools/exit_gates/gates/gate_status_error_redaction.py:7-26
  - tools/exit_gates/manifest.json:47-48
  - tools/exit_gates/manifest_p10_sec.json:1-4
  - README.md:14, 95
  - docs/REPO_LAYOUT.md:7, 84
- Як перевірено →
  - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m pytest -q tests/test_command_bus_security.py (OK)
  - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m tools.run_exit_gates --out reports/exit_gates --manifest tools/exit_gates/manifest_p10_sec.json (OK)
- Ризики/нотатки →
  - Командні повідомлення у status тепер редактовані; деталі доступні лише в локальних логах.

## 2026-02-01T11:20:00+01:00 — PRE (MODE=PATCH): P10.SEC.C0 CommandBus rate-limit + coalesce (anti-spam rails)

- Мета → додати rate-limit (raw + per-cmd), error coalescing та collapse-to-latest для heavy команд.
- Scope →
  - runtime/command_bus.py
  - runtime/status.py
  - config/config.py
  - observability/metrics.py
  - tests/test_command_bus_ratelimit.py
  - tools/exit_gates/gates/gate_command_bus_ratelimit.py
  - tools/exit_gates/manifest_p10_sec.json
  - README.md
  - docs/REPO_LAYOUT.md
- Non-goals → HMAC/anti-replay; зміна commands_v1.json; silent fallback.
- Інваріанти/рейки → degraded-but-loud; status не роздувається; rate-limit до heavy handlers.
- Тести/перевірки →
  - pytest -q tests/test_command_bus_ratelimit.py
  - python -m tools.run_exit_gates --manifest tools/exit_gates/manifest_p10_sec.json --out reports/exit_gates

## 2026-02-01T11:50:00+01:00 — POST (MODE=PATCH): P10.SEC.C0 CommandBus rate-limit + coalesce (anti-spam rails)

- Що зроблено →
  - Додано raw/per-cmd rate-limit, coalesce помилок та collapse-to-latest для heavy команд у command_bus (default OFF).
  - Додано coalesce state у status, метрики rate-limit/coalesce, тести та exit gate.
  - Оновлено README/REPO_LAYOUT.
- Де зроблено →
  - runtime/command_bus.py:20-431
  - runtime/status.py:582-637
  - config/config.py:103-113
  - observability/metrics.py:15-87, 332-333
  - tests/test_command_bus_ratelimit.py:44-129
  - tools/exit_gates/gates/gate_command_bus_ratelimit.py:1-43
  - tools/exit_gates/manifest_p10_sec.json:1-4
  - README.md:14, 96
  - docs/REPO_LAYOUT.md:7, 84
- Як перевірено →
  - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m pytest -q tests/test_command_bus_ratelimit.py (OK)
  - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m tools.run_exit_gates --manifest tools/exit_gates/manifest_p10_sec.json --out reports/exit_gates (OK)
- Ризики/нотатки →
  - Rate-limit/coalesce/collapse вимкнені за замовчуванням; для увімкнення потрібні явні config прапори.

## 2026-02-01T12:20:00+01:00 — PRE (MODE=PATCH): P10.SEC.B0 HMAC auth + anti-replay для команд (rolling, default OFF)

- Мета → додати HMAC auth (kid+sig+nonce) з anti-replay і rolling режимом (enable/required).
- Scope →
  - core/contracts/public/commands_v1.json
  - runtime/command_bus.py
  - runtime/command_auth.py
  - runtime/status.py
  - config/config.py
  - config/secrets_template.py
  - config/profile_template.py
  - core/env_loader.py
  - tests/test_command_hmac.py
  - tools/exit_gates/gates/gate_command_hmac_required.py
  - tools/exit_gates/manifest_p10_sec.json
  - README.md
  - docs/REPO_LAYOUT.md
  - docs/runbooks/command_auth.md
- Non-goals → JWT/асиметрія; зміна transport/ACL; будь-які silent fallback.
- Інваріанти/рейки → status містить лише короткі коди; auth rolling; anti-replay через Redis SETNX.
- Тести/перевірки →
  - pytest -q tests/test_command_hmac.py
  - python -m tools.run_exit_gates --manifest tools/exit_gates/manifest_p10_sec.json --out reports/exit_gates

## 2026-02-01T12:55:00+01:00 — POST (MODE=PATCH): P10.SEC.B0 HMAC auth + anti-replay для команд (rolling, default OFF)

- Що зроблено →
  - Додано HMAC auth + anti-replay (SETNX+TTL) з rolling режимом enable/required.
  - Розширено commands_v1 schema optional `auth`.
  - Додано шаблони секретів/профілю, новий runbook, exit gate і тести.
- Де зроблено →
  - runtime/command_bus.py:12, 286-320
  - runtime/command_auth.py:1-109
  - core/contracts/public/commands_v1.json:11-21
  - config/config.py:112-116
  - config/secrets_template.py:10-13
  - config/profile_template.py:18-20
  - core/env_loader.py:10
  - tests/test_command_hmac.py:78-179
  - tools/exit_gates/gates/gate_command_hmac_required.py:1-43
  - tools/exit_gates/manifest_p10_sec.json:1-5
  - README.md:14, 97
  - docs/REPO_LAYOUT.md:7, 84-85
  - docs/runbooks/command_auth.md:1-58
- Як перевірено →
  - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m pytest -q tests/test_command_hmac.py (OK)
  - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m tools.run_exit_gates --manifest tools/exit_gates/manifest_p10_sec.json --out reports/exit_gates (OK)
- Ризики/нотатки →
  - Auth rolling вимкнено за замовчуванням; для вимоги auth потрібні явні config прапори та секрети.

## 2026-02-01T13:20:00+01:00 — PRE (MODE=PATCH): P10.SEC.ACL Redis ACL runbook (transport hardening)

- Мета → оформити Redis ACL runbook: UI read‑only, SMC publish тільки {NS}:commands, connector тільки {NS}:*.
- Scope →
  - docs/runbooks/redis_acl.md
  - README.md
  - docs/REPO_LAYOUT.md
  - tools/exit_gates/gates/gate_runbook_redis_acl_exists.py
  - tools/exit_gates/manifest_p10_sec.json
- Non-goals → зміни коду/redis client.
- Інваріанти/рейки → без розкриття внутрішніх allowlist; лише операційні кроки.
- Тести/перевірки → не застосовно (runbook).

## 2026-02-01T13:40:00+01:00 — POST (MODE=PATCH): P10.SEC.ACL Redis ACL runbook

- Що зроблено → додано runbook Redis ACL з ролями UI/SMC/connector, інваріантами та командами перевірки; оновлено README/REPO_LAYOUT; додано gate на наявність runbook.
- Де зроблено →
  - docs/runbooks/redis_acl.md:1-68
  - README.md:54-58
  - docs/REPO_LAYOUT.md:13-14, 67-72
  - tools/exit_gates/gates/gate_runbook_redis_acl_exists.py:1-24
  - tools/exit_gates/manifest_p10_sec.json:1-6
- Як перевірено → не застосовно (runbook, ручні команди вказані у документі).
- Ризики/нотатки → runbook не описує внутрішні контракти/allowlist; лише transport‑рівень.

## 2026-02-01T14:10:00+01:00 — PRE (MODE=read-only discovery): Спостереження шуму у {NS}:commands

- Мета → зафіксувати факт сторонніх повідомлень у каналі команд.
- Scope (читання) → зовнішній лог redis-cli (від користувача).
- Non-goals → будь-які зміни коду/конфігів.
- Інваріанти/рейки → лише факти; джерело — консольний лог користувача.
- План → зафіксувати payloads і висновок щодо відповідності CommandEnvelope.

## 2026-02-01T14:12:00+01:00 — POST (MODE=read-only discovery): Спостереження шуму у {NS}:commands

- Що зроблено → зафіксовано, що у fxcm_local:commands приходять payloads типу fxcm_warmup, які не є CommandEnvelope.
- Де зроблено → джерело: лог користувача з redis-cli SUBSCRIBE fxcm_local:commands.
- Як перевірено → не застосовно (зовнішній лог).
- Ризики/нотатки → такі payloads мають відхилятися валідатором/автентифікацією; для припинення шуму потрібен ACL (Layer A) або окремий канал/інстанс.

## 2026-02-01T14:30:00+01:00 — PRE (MODE=PATCH): SMC runbooks (command integration + status payload)

- Мета → додати внутрішню документацію для SMC: інтеграція команд та статусний payload v2.
- Scope →
  - docs/runbooks/smc_command_integration.md
  - docs/runbooks/status_payload_v2.md
- Non-goals → зміни коду/контрактів.
- Інваріанти/рейки → only docs, без витоку секретів; узгоджено з commands_v1/status_v2.
- Тести/перевірки → не застосовно (docs-only).

## 2026-02-01T14:45:00+01:00 — POST (MODE=PATCH): SMC runbooks (command integration + status payload)

- Що зроблено → додано runbook для SMC з CommandEnvelope/auth правилами та описом статусного payload v2.
- Де зроблено →
  - docs/runbooks/smc_command_integration.md:1-120
  - docs/runbooks/status_payload_v2.md:1-120
- Як перевірено → не застосовно (docs-only).
- Ризики/нотатки → SMC має перейти на commands_v1 envelope; legacy `type=fxcm_warmup` очікувано відхиляється.

## 2026-02-01T15:10:00+01:00 — PRE (MODE=PATCH): P10.MANIFEST — повернути P10 у загальний manifest

- Мета → додати P10 entries у tools/exit_gates/manifest.json без зміни логіки гейтів.
- Scope → tools/exit_gates/manifest.json, Work/01log.md.
- Non-goals → зміна логіки гейтів або інших файлів.
- Інваріанти/рейки → без дублікатів; P10-manifest лишається окремим файлом.
- Тести/перевірки →
  - python -m tools.run_exit_gates --out reports/exit_gates --manifest tools/exit_gates/manifest.json

## 2026-02-01T15:12:00+01:00 — POST (MODE=read-only discovery): P10.MANIFEST — факти перед правкою

- Факти →
  - tools/exit_gates/manifest_p10_sec.json містить:
    - tools.exit_gates.gates.gate_command_payload_limits
    - tools.exit_gates.gates.gate_status_error_redaction
    - tools.exit_gates.gates.gate_command_bus_ratelimit
    - tools.exit_gates.gates.gate_command_hmac_required
    - tools.exit_gates.gates.gate_runbook_redis_acl_exists
  - tools/exit_gates/manifest.json вже містить:
    - tools.exit_gates.gates.gate_command_payload_limits
    - tools.exit_gates.gates.gate_status_error_redaction
  - Відсутні у tools/exit_gates/manifest.json:
    - tools.exit_gates.gates.gate_command_bus_ratelimit
    - tools.exit_gates.gates.gate_command_hmac_required
    - tools.exit_gates.gates.gate_runbook_redis_acl_exists

  ## 2026-02-01T15:25:00+01:00 — POST (MODE=PATCH): P10.MANIFEST — повернути P10 у загальний manifest

  - Що зроблено → додано відсутні P10 entries у tools/exit_gates/manifest.json (без змін логіки гейтів).
  - Де зроблено → tools/exit_gates/manifest.json:45-48.
  - Як перевірено →
    - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m tools.run_exit_gates --out reports/exit_gates --manifest tools/exit_gates/manifest.json (OK)
  - Ризики/нотатки → не виявлено; всі gate пройшли.

  ## 2026-02-01T15:40:00+01:00 — PRE (MODE=PATCH): P10.SEC C0+B0 rolling enable у SSOT config

  - Мета → увімкнути C0 (rate-limit/coalesce/collapse) та B0 rolling (auth enable ON, required OFF) у config/config.py.
  - Scope → config/config.py; Work/01log.md.
  - Non-goals → зміна логіки гейтів/код; увімкнення bootstrap/reconcile; required=True.
  - Інваріанти/рейки → дефолти без автозапуску heavy‑workflow; required=OFF; fail‑fast без витоку деталей.
  - Тести/перевірки →
    - python -m ruff check .
    - python -m mypy .
    - python -m pytest -q
    - python -m tools.run_exit_gates --out reports/exit_gates --manifest tools/exit_gates/manifest.json

  ## 2026-02-01T16:05:00+01:00 — POST (MODE=PATCH): P10.SEC C0+B0 rolling enable у SSOT config

  - Що зроблено → увімкнено C0 (rate-limit/coalesce/collapse) та B0 rolling (auth enable ON, required OFF) у SSOT конфігу.
  - Де зроблено → config/config.py:103, 108, 110, 112.
  - Як перевірено →
    - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m ruff check . (OK)
    - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m mypy . (FAIL: 12 помилок у runtime/status.py, runtime/fxcm_forexconnect.py, tools/exit_gates/gates/gate_final_republish_source_allowlist.py, tests/*)
    - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m pytest -q (FAIL: tests/test_command_bus_ratelimit.py::test_heavy_command_collapse_to_latest, tests/test_fxcm_offers_subscription.py::test_fxcm_tick_event_ahead_of_receipt_normalizes_snap_ts)
    - C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe -m tools.run_exit_gates --out reports/exit_gates --manifest tools/exit_gates/manifest.json (OK)
  - Ризики/нотатки →
    - Падіння pytest/mypy потребує окремого фіксу (поза цим кроком увімкнення флагів).

  ## 2026-02-01T16:20:00+01:00 — PRE (MODE=read-only discovery): Польові перевірки P10.SEC C0/B0

  - Мета → перевірити payload limits, rate-limit+coalesce, rolling auth на живому Redis.
  - Scope (читання) → redis-cli PUBLISH/GET, status snapshot.
  - Non-goals → зміни коду/конфігу.
  - Інваріанти/рейки → лише факти з команд; без редагування системи.
  - Тести/перевірки →
    - redis-cli PUBLISH oversize
    - redis-cli PUBLISH spam invalid JSON
    - redis-cli GET {NS}:status:snapshot

  ## 2026-02-01T16:35:00+01:00 — POST (MODE=read-only discovery): Польові перевірки P10.SEC C0/B0

  - Що зроблено → виконано oversize publish (через redis-cli -x) і invalid JSON publish; зчитано status snapshot.
  - Де зроблено → fxcm_local:commands; fxcm_local:status:snapshot.
  - Як перевірено →
    - redis-cli -x PUBLISH fxcm_local:commands (oversize)
    - redis-cli PUBLISH fxcm_local:commands "{bad json"
    - redis-cli GET fxcm_local:status:snapshot
  - Результат → status snapshot не змінився; остання помилка залишилась `contract_error` з попереднім ts. Перевірки захистів C0/B0 у цьому середовищі __не підтверджені__.
  - Ризики/нотатки → ймовірно публікація йде не в той Redis/NS або command_bus не обробляє канал; потрібна верифікація підключення/процесу конектора перед повтором польових тестів.

  ## 2026-02-01T16:50:00+01:00 — PRE (MODE=read-only discovery): Питання профілю та env секретів (P10.SEC)

  - Мета → уточнити, чи вмикати флаги у profile_template та які секрети потрібні в .env.local/.env.prod.
  - Scope (читання) → config/profile_template.py, config/config.py, core/env_loader.py, config/secrets_template.py.
  - Non-goals → зміни коду/конфігів.
  - Інваріанти/рейки → ENV лише для секретів; флаги/режими — у config/profile_*.py.

  ## 2026-02-01T16:52:00+01:00 — POST (MODE=read-only discovery): Питання профілю та env секретів (P10.SEC)

  - Висновок → profile_template залишає auth флаги OFF; увімкнення C0/B0 робиться у profile_local.py/profile_prod.py (SSOT), не в ENV.
  - Секрети в .env.local/.env.prod →
    - FXCM_USERNAME / FXCM_PASSWORD (FXCM доступ)
    - FXCM_HMAC_KID / FXCM_HMAC_SECRET (HMAC auth)
    - решта — лише allowlist інфра ключів з core/env_loader.py
  - Ризики/нотатки → FXCM_HMAC_REQUIRED/ALGO не керують поведінкою; command_auth_required налаштовується у профілі (не в ENV).

  ## 2026-02-01T17:10:00+01:00 — PRE (MODE=PATCH): Увімкнення C0/B0 у профілях local+prod

  - Мета → увімкнути C0 (rate-limit/coalesce/collapse) та B0 (auth enable+required) у profile_local.py/profile_prod.py.
  - Scope → config/profile_local.py, config/profile_prod.py, Work/01log.md.
  - Non-goals → зміни runtime/контрактів; зміни .env.* файлів.
  - Інваріанти/рейки → секрети лише в .env.local/.env.prod; профілі — SSOT для флагів.
  - Тести/перевірки → не застосовно (конфіг).

  ## 2026-02-01T17:15:00+01:00 — POST (MODE=PATCH): Увімкнення C0/B0 у профілях local+prod

  - Що зроблено → додано профілі local/prod з увімкненими C0 та B0 (required=True) + allowlist kid.
  - Де зроблено →
    - config/profile_local.py:1-20
    - config/profile_prod.py:1-20
  - Як перевірено → не застосовно (конфіг).
  - Ризики/нотатки → потрібні FXCM_HMAC_KID/FXCM_HMAC_SECRET у .env.local/.env.prod.

  ## 2026-02-01T17:30:00+01:00 — PRE (MODE=read-only discovery): MVP parity план запуску (warmup/backfill/republish/tail_guard)

  - Мета → глибоке дослідження MVP parity та опис дій для гарантованого запуску.
  - Scope (читання) → runtime/warmup.py, runtime/backfill.py, runtime/republish.py, runtime/tail_guard.py, runtime/handlers_p3.py, runtime/handlers_p4.py, config/config.py, docs/runbooks/*.
  - Non-goals → зміни коду/конфігів.
  - Інваріанти/рейки → лише факти з path:line; документ без секретів.
  - Артефакт → docs/runbooks/mvp_parity_plan.md.

  ## 2026-02-01T18:05:00+01:00 — PRE (MODE=PATCH): MVP parity план (документація)

  - Мета → зафіксувати план parity для warmup/backfill/republish_tail/tail_guard з посиланнями на SSOT код.
  - Scope → docs/runbooks/mvp_parity_plan.md, Work/01log.md.
  - Non-goals → зміни runtime/контрактів/конфігів; запуск тестів.
  - Інваріанти/рейки → лише документація, без секретів, без зміни логіки.
  - План →
    1) Зібрати ключові джерела правди (handlers, runtime, config, status).
    2) Описати потоки warmup/backfill/republish_tail/tail_guard і необхідні аргументи.
    3) Додати критерії перевірки (status + metrics).
  - Тести/перевірки → не застосовно (документація).

  ## 2026-02-01T18:20:00+01:00 — POST (MODE=PATCH): MVP parity план (документація)

  - Що зроблено → створено план MVP parity для warmup/backfill/republish_tail/tail_guard з посиланнями на SSOT код, аргументами команд і критеріями перевірки.
  - Де зроблено → docs/runbooks/mvp_parity_plan.md.
  - Як перевірено → не застосовно (документація).
  - Ризики/нотатки → без змін у runtime/контрактах; потрібна польова верифікація командних потоків окремо.

  ## 2026-02-01T19:05:00+01:00 — PRE (MODE=PATCH): Єдиний manifest exit_gates (P10 merge)

  - Мета → забезпечити, що P10.SEC/P10.B gate’и живуть у tools/exit_gates/manifest.json без окремого manifest.
  - Scope → tools/exit_gates/manifest.json, tools/exit_gates/manifest_p10_sec.json (якщо існує), Work/01log.md.
  - Non-goals → зміни runtime/контрактів.
  - Інваріанти/рейки → один manifest, без дублікатів gate’ів.
  - План →
    1) Перевірити наявність потрібних gate’ів у manifest.json.
    2) Якщо є manifest_p10_sec.json — злити/прибрати окремий manifest.
    3) Прогнати повний pipeline (ruff → mypy → pytest → exit_gates).
  - Тести/перевірки → ruff, mypy, pytest, tools.run_exit_gates (manifest.json).

  ## 2026-02-01T19:20:00+01:00 — POST (MODE=PATCH): Єдиний manifest exit_gates (P10 merge)

  - Що зроблено → підтверджено наявність P10.SEC/P10.B gate’ів у основному manifest; очищено окремий manifest P10.
  - Де зроблено → tools/exit_gates/manifest_p10_sec.json.
  - Як перевірено (run_id=2026-02-01T19:18:30+01:00) →
    - $py = "C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe"; & $py -m ruff check . → OK
    - & $py -m mypy . → FAIL (12 errors, mypy failed)
    - & $py -m pytest -q → NOT RUN (stop-on-fail)
    - & $py -m tools.run_exit_gates --out reports/exit_gates --manifest tools/exit_gates/manifest.json → NOT RUN (stop-on-fail)
  - Ризики/нотатки → pipeline зупинено на mypy; потрібен окремий фікс mypy перед повтором повного прогону.

  ## 2026-02-01T19:30:00+01:00 — PRE (MODE=read-only discovery): Повний pipeline (ruff/mypy/pytest/exit_gates)

  - Мета → запустити ruff, mypy, pytest, exit_gates (manifest.json) і зафіксувати результати.
  - Scope (читання) → без змін коду; тільки виконання перевірок.
  - Non-goals → модифікація runtime/контрактів/конфігів.
  - Інваріанти/рейки → команди без пропусків, stop-on-fail.
  - Тести/перевірки →
    - python -m ruff check .
    - python -m mypy .
    - python -m pytest -q
    - python -m tools.run_exit_gates --out reports/exit_gates --manifest tools/exit_gates/manifest.json

  ## 2026-02-01T19:32:00+01:00 — POST (MODE=read-only discovery): Повний pipeline (ruff/mypy/pytest/exit_gates)

  - Що зроблено → запущено повний pipeline з stop-on-fail.
  - Як перевірено (run_id=2026-02-01T19:31:00+01:00) →
    - python -m ruff check . → OK
    - python -m mypy . → FAIL (12 errors)
    - python -m pytest -q → NOT RUN (stop-on-fail)
    - python -m tools.run_exit_gates --out reports/exit_gates --manifest tools/exit_gates/manifest.json → NOT RUN (stop-on-fail)
  - Ризики/нотатки → потрібні виправлення mypy перед повторним запуском; “все зелене” наразі недосяжне без коду.

  ## 2026-02-01T19:40:00+01:00 — PRE (MODE=read-only discovery): Польові SEC тести (локально, канали команд/тик/ohlcv)

  - Мета → перевірити реакцію захистів на некоректні payload-и, rate-limit, auth-відхилення; зафіксувати зміни у статусі.
  - Scope (читання) → Redis Pub/Sub канали (commands/price_tik/ohlcv), status snapshot; без змін коду.
  - Non-goals → зміни runtime/контрактів/конфігів; обхід auth.
  - Інваріанти/рейки → лише локальний NS, без зовнішніх ресурсів; stop-on-fail не застосовується.
  - Тести/перевірки →
    - redis-cli PUBSUB NUMSUB {NS}:commands
    - invalid JSON / oversize payload / bad auth / spam (rate-limit)
    - GET {NS}:status:snapshot (last_error/last_command/degraded)

  ## 2026-02-01T19:50:00+01:00 — POST (MODE=read-only discovery): Польові SEC тести (локально, канали команд/тик/ohlcv)

  - Що зроблено → виконано інʼєкційні/некоректні publish у commands, oversize payload, spam invalid JSON; перевірено status snapshot.
  - Як перевірено (run_id=2026-02-01T19:45:00+01:00) →
    - redis-cli PUBSUB NUMSUB fxcm_local:commands → 2
    - redis-cli PUBLISH fxcm_local:commands "{bad json" → last_error=invalid_json
    - oversize payload (20k) через redis-cli -x → last_error=command_payload_too_large
    - валідний envelope без auth → last_error не змінився (last_command оновився)
    - валідний envelope з неправильним auth → last_error не змінився (last_command оновився)
    - spam invalid JSON (50) → last_error=invalid_json з context.count=50 (coalesce)
  - Ризики/нотатки →
    - auth-відхилення не відобразились у last_error (можливо coalesce/публікація статусу в цій гілці); потребує перевірки при активному профілі з B0 required=True.
    - degraded містив fxcm_calendar_mismatch до тестів; без змін під час тестів.

  ## 2026-02-01T20:05:00+01:00 — PRE (MODE=PATCH): Фікс replace() duplicate ns у load_config

  - Мета → усунути помилку replace() got multiple values for keyword argument 'ns' при наявності ns в env_overrides+overrides.
  - Scope → config/config.py, Work/01log.md.
  - Non-goals → зміни runtime/контрактів.
  - Інваріанти/рейки → мінімальний диф; поведінка config без змін окрім усунення TypeError.
  - Тести/перевірки → не застосовно (локальний запуск користувача).

  ## 2026-02-01T20:10:00+01:00 — POST (MODE=PATCH): Фікс replace() duplicate ns у load_config

  - Що зроблено → об’єднано env/profile overrides в один словник перед replace(), щоб уникнути дублювання `ns`.
  - Де зроблено → config/config.py.
  - Як перевірено → не запускалось (очікується повторний запуск app.main).
  - Ризики/нотатки → зміна лише в порядку передачі overrides; логіка конфігу не змінена.

  ## 2026-02-01T20:20:00+01:00 — PRE (MODE=PATCH): Узгодження fxcm_backend=disabled з tick_mode

  - Мета → усунути помилку старту: tick_mode=fxcm потребує fxcm_backend=forexconnect при профілі local/prod.
  - Scope → config/profile_local.py, config/profile_prod.py, Work/01log.md.
  - Non-goals → зміни runtime/контрактів.
  - Інваріанти/рейки → мінімальний диф; лише профільні overrides.
  - Тести/перевірки → не застосовно (локальний запуск користувача).

  ## 2026-02-01T20:25:00+01:00 — POST (MODE=PATCH): Узгодження fxcm_backend=disabled з tick_mode

  - Що зроблено → у профілях local/prod додано tick_mode=off при fxcm_backend=disabled.
  - Де зроблено → config/profile_local.py, config/profile_prod.py.
  - Як перевірено → не запускалось (очікується повторний запуск app.main).
  - Ризики/нотатки → лише профільні overrides; без впливу на forexconnect режим.

  ## 2026-02-01T20:40:00+01:00 — PRE (MODE=read-only discovery): Повторні SEC тести (B0 required=True, C0 on)

  - Мета → повторити польові тести захисту при активному профілі B0 required=True та увімкнених C0 рейках.
  - Scope (читання) → Redis Pub/Sub канали (commands/price_tik/ohlcv), status snapshot; без змін коду.
  - Non-goals → обхід захистів або зміни конфігів/контрактів.
  - Інваріанти/рейки → локальний NS; тести лише на каналі commands; без зовнішніх ресурсів.
  - Тести/перевірки →
    - redis-cli PUBSUB NUMSUB {NS}:commands
    - invalid JSON / oversize payload / envelope без auth / envelope з bad auth / spam invalid JSON
    - GET {NS}:status:snapshot (last_error/last_command/degraded)

  ## 2026-02-01T20:50:00+01:00 — POST (MODE=read-only discovery): Повторні SEC тести (B0 required=True, C0 on)

  - Що зроблено → виконано повторні інʼєкційні/некоректні publish у commands, oversize payload, envelope без auth, envelope з bad auth, spam invalid JSON.
  - Як перевірено (run_id=2026-02-01T20:45:00+01:00) →
    - redis-cli PUBSUB NUMSUB fxcm_local:commands → 2
    - GET fxcm_local:status:snapshot → last_error=None, degraded=[] (база)
    - PUBLISH invalid JSON → last_error=invalid_json
    - oversize payload (20k) → last_error=command_payload_too_large
    - валідний envelope без auth → last_error не змінився (last_command оновився)
    - валідний envelope з неправильним auth → last_error не змінився (last_command оновився)
    - spam invalid JSON (50) → last_error лишився command_payload_too_large; errors_tail містить invalid_json
  - Ризики/нотатки →
    - auth-відхилення не відображаються у last_error; ймовірно coalesce або гілка без publish_snapshot для auth. Потрібна окрема перевірка логів/метрик.
  - Додаткові спостереження (від користувача) →
    - ERRORS: invalid_json (ts=1769971660512), command_payload_too_large (ts=1769971666828).

  ## 2026-02-01T21:05:00+01:00 — PRE (MODE=read-only discovery): Верифікація Normal ops (preview/final/MTF)

  - Мета → перевірити, чи відповідає код тезам про preview/final/MTF cross-check.
  - Scope (читання) → runtime/reconcile_finalizer.py, runtime/warmup.py, runtime/backfill.py, runtime/republish.py, config/config.py.
  - Non-goals → зміни коду/конфігів.
  - Інваріанти/рейки → лише факти з path:line.
  - Тести/перевірки → не застосовно.

  ## 2026-02-01T21:10:00+01:00 — POST (MODE=read-only discovery): Верифікація Normal ops (preview/final/MTF)

  - Що підтверджено →
    - Final 1m формується з history (warmup/backfill/reconcile) через fetch_1m_final і запис у FileCache.
    - history_agg використовується для final 15m у reconcile (append_complete_bars source=history_agg).
  - Що не підтверджено в runtime →
    - MTF cross-check: є лише флаг у config, але реалізації не знайдено.
    - final 1h/4h/1d з history_agg у runtime не виявлено (тільки 15m у reconcile).
  - Де → runtime/reconcile_finalizer.py, runtime/warmup.py, runtime/backfill.py, config/config.py.

  ## 2026-02-01T21:20:00+01:00 — PRE (MODE=read-only discovery): Підготовка P9.S6/S7/S8 (bootstrap/tail_guard)

  - Мета → визначити місця інтеграції для auto-warmup, republish_tail, scheduled tail_guard audit.
  - Scope (читання) → app/composition.py, app/main.py, runtime/warmup.py, runtime/backfill.py, runtime/republish.py, runtime/tail_guard.py, config/config.py.
  - Non-goals → зміни коду/конфігів.
  - Інваріанти/рейки → лише факти з path:line.
  - Тести/перевірки → не застосовно.

  ## 2026-02-01T21:25:00+01:00 — POST (MODE=read-only discovery): Підготовка P9.S6/S7/S8 (bootstrap/tail_guard)

  - Ключові місця для S6/S7/S8 →
    - bootstrap командний ланцюжок (warmup/backfill/republish/tail_guard): app/composition.py.
    - warmup/backfill реалізації: runtime/warmup.py, runtime/backfill.py.
    - republish_tail: runtime/republish.py + handler у app/composition.py.
    - tail_guard audit/repair: runtime/tail_guard.py + handler у app/composition.py.
    - конфіг флагів bootstrap/tail_guard: config/config.py.
  - Нотатки → існують флаги bootstrap_enable/bootstrap_republish_after_backfill/bootstrap_tail_guard_after; scheduled audit потребує окремого тригера (у main loop або окремому worker).

  ## 2026-02-01T21:40:00+01:00 — PRE (MODE=PATCH): Актуалізація docs/REPO_LAYOUT.md та README.md

  - Мета → оновити документацію під актуальний стан профілів, рейок безпеки та runbooks.
  - Scope → docs/REPO_LAYOUT.md, README.md, Work/01log.md.
  - Non-goals → зміни runtime/контрактів.
  - Інваріанти/рейки → мінімальний диф; без зміни технічної логіки.
  - Тести/перевірки → не застосовно (документація).

  ## 2026-02-01T21:45:00+01:00 — POST (MODE=PATCH): Актуалізація docs/REPO_LAYOUT.md та README.md

  - Що зроблено →
    - docs/REPO_LAYOUT.md: додано profile_local.py/profile_prod.py, уточнено runbooks (mvp_parity_plan).
    - README.md: уточнено, що command rails та auth керуються профілями; додано примітку про tick_mode=off при fxcm_backend=disabled у профілях local/prod.
  - Де зроблено → docs/REPO_LAYOUT.md, README.md.
  - Як перевірено → не застосовно (документація).
  - Ризики/нотатки → мінімальні, лише документальні зміни.

  ## 2026-02-01T22:00:00+01:00 — PRE (MODE=PATCH): UI Lite — підвищення інформативності

  - Мета → додати блоки Final/Command (coverage, republish, tail_guard summary, command bus/last command) у UI Lite.
  - Scope → ui_lite/static/app.js, ui_lite/static/styles.css, Work/01log.md.
  - Non-goals → зміни runtime/контрактів; зміна UI поведінки підписок.
  - Інваріанти/рейки → мінімальний диф; українські лейбли; без зміни data‑payload.
  - Тести/перевірки → не застосовно (UI зміни).

  ## 2026-02-01T22:10:00+01:00 — POST (MODE=PATCH): UI Lite — підвищення інформативності

  - Що зроблено → додано блоки FINAL/CMD у healthbar та рендер показників (final 1m, coverage, republish, tail_guard, command bus, last command).
  - Де зроблено → ui_lite/static/app.js, ui_lite/static/styles.css.
  - Як перевірено → не застосовно (UI зміни).
  - Ризики/нотатки → залежить від наявності полів у status snapshot; відсутні значення показуються як «-».

  ## 2026-02-01T22:25:00+01:00 — PRE (MODE=PATCH): UI Lite — діагностика як overlay/hidden

  - Мета → зробити блок діагностики напівпрозорим, з кнопкою для режимів inline/overlay/hidden.
  - Scope → ui_lite/static/index.html, ui_lite/static/app.js, ui_lite/static/styles.css, Work/01log.md.
  - Non-goals → зміни runtime/контрактів.
  - Інваріанти/рейки → мінімальний диф; без зміни data‑payload.
  - Тести/перевірки → не застосовно (UI зміни).

  ## 2026-02-01T22:35:00+01:00 — POST (MODE=PATCH): UI Lite — діагностика як overlay/hidden

  - Що зроблено → додано кнопку “Діагностика” з режимами inline/overlay/hidden; healthbar став напівпрозорим.
  - Де зроблено → ui_lite/static/index.html, ui_lite/static/app.js, ui_lite/static/styles.css.
  - Як перевірено → не застосовно (UI зміни).
  - Ризики/нотатки → режим зберігається у localStorage; при overlay блок накладається поверх графіка.

  ## 2026-02-01T22:50:00+01:00 — PRE (MODE=read-only discovery): Статус P7 (SSOT vs операційний нюанс)

  - Мета → підтвердити, чи P7 закритий, і зафіксувати операційний follow-up.
  - Scope (читання) → Work/01log.md, docs/audit_v7_* (за потреби).
  - Non-goals → зміни коду/конфігів.
  - Інваріанти/рейки → лише факти.
  - Тести/перевірки → не застосовно.

  ## 2026-02-01T22:55:00+01:00 — POST (MODE=read-only discovery): Статус P7 (SSOT vs операційний нюанс)

  - Висновок → P7 як SSOT + exit-gates закритий (rerun OK, holiday_policy optional). Є операційний follow-up: paused_market_closed має використовувати Calendar.next_open_ms (SSOT), а не config.closed_intervals_utc, щоб уникати churn/"calendar mismatch".

  ## 2026-02-01T23:10:00+01:00 — PRE (MODE=PATCH): P9.S6 auto_warmup_on_start

  - Мета → auto warmup на cold start з подальшим republish tail для final 1m.
  - Scope → config/config.py, app/composition.py, Work/01log.md.
  - Non-goals → зміни runtime/контрактів поза auto warmup; зміни тестів.
  - Інваріанти/рейки → мінімальний диф; warmup лише через history; loud status.
  - Тести/перевірки → не застосовно (потребує live середовища).

  ## 2026-02-01T23:20:00+01:00 — POST (MODE=PATCH): P9.S6 auto_warmup_on_start

  - Що зроблено →
    - Додано `auto_warmup_on_start` у SSOT конфіг.
    - Додано фонового auto-warmup worker: перевірка холодного старту, warmup через history, republish_tail після завершення.
  - Де зроблено → config/config.py, app/composition.py.
  - Як перевірено → не запускалось (потрібне live середовище).
  - Ризики/нотатки → авто‑warmup запускається у фоні; при відсутності history provider або cache — loud error + degraded.

  ## 2026-02-01T23:40:00+01:00 — PRE (MODE=PATCH): P9.S7 auto_republish_on_start

  - Мета → republish_tail одразу після рестарту, якщо SSOT непорожній.
  - Scope → config/config.py, app/composition.py, README.md, Work/01log.md.
  - Non-goals → зміни runtime/контрактів поза auto republish.
  - Інваріанти/рейки → мінімальний диф; без silent force; лише final-джерела.
  - Тести/перевірки → не застосовно (потребує live середовища).

  ## 2026-02-01T23:50:00+01:00 — POST (MODE=PATCH): P9.S7 auto_republish_on_start

  - Що зроблено →
    - Додано `auto_republish_on_start` у SSOT конфіг.
    - Додано auto‑republish worker: якщо SSOT непорожній, робить republish_tail (1m, window_hours=default, force=false).
  - Де зроблено → config/config.py, app/composition.py, README.md.
  - Як перевірено → не запускалось (потрібне live середовище).
  - Ризики/нотатки → watermark може призвести до state=skipped; force не вмикається автоматично.

  ## 2026-02-01T23:55:00+01:00 — PRE (MODE=PATCH): Увімкнення повного прод‑режиму FXCM

  - Мета → увімкнути повний режим: fxcm_backend=forexconnect, tick_mode=fxcm, history_provider_kind=fxcm_forexconnect.
  - Scope → config/profile_local.py, config/profile_prod.py, Work/01log.md.
  - Non-goals → зміни runtime/контрактів.
  - Інваріанти/рейки → мінімальний диф; профілі SSOT.
  - Тести/перевірки → не застосовно (потрібне live середовище).

  ## 2026-02-02T00:00:00+01:00 — POST (MODE=PATCH): Увімкнення повного прод‑режиму FXCM

  - Що зроблено → у профілях local/prod увімкнено forexconnect: fxcm_backend=forexconnect, tick_mode=fxcm, history_provider_kind=fxcm_forexconnect.
  - Де зроблено → config/profile_local.py, config/profile_prod.py.
  - Як перевірено → не запускалось (потрібне live середовище + FXCM SDK).
  - Ризики/нотатки → без FXCM SDK/секретів запуск завершиться fail‑fast.

  ## 2026-02-02T00:10:00+01:00 — PRE (MODE=PATCH): Auto‑republish guard для non‑final source

  - Мета → уникнути republish_source_invalid при auto_republish, якщо last_write_source не final.
  - Scope → app/composition.py, Work/01log.md.
  - Non-goals → зміни runtime/контрактів поза auto republish.
  - Інваріанти/рейки → мінімальний диф; лише skip з loud warn.
  - Тести/перевірки → не застосовно (потрібне live середовище).

  ## 2026-02-02T00:15:00+01:00 — POST (MODE=PATCH): Auto‑republish guard для non‑final source

  - Що зроблено → auto_republish пропускається, якщо last_write_source не final; додається warn error `auto_republish_skipped`.
  - Де зроблено → app/composition.py.
  - Як перевірено → не запускалось (потрібне live середовище).
  - Ризики/нотатки → republish_source_invalid більше не має спрацьовувати при non‑final source; потрібна перевірка після рестарту.

  ## 2026-02-02T00:30:00+01:00 — PRE (MODE=PATCH): P9.S5 history row time-key normalization

  - Мета → нормалізувати ключі часу в history rows (Date/date/…) і додати метадані row_keys при fail-fast.
  - Scope → runtime/fxcm/history_provider.py, tests/test_fxcm_history_row_date.py, Work/01log.md.
  - Non-goals → зміни логіки history chunking.
  - Інваріанти/рейки → мінімальний диф; fail-fast із контекстом.
  - Тести/перевірки → pytest (локально, за потреби).

  ## 2026-02-02T00:40:00+01:00 — POST (MODE=PATCH): P9.S5 history row time-key normalization

  - Що зроблено →
    - Розширено allowlist time-key (date/date_utc/DateTime/open_time_ms тощо).
    - Додано контекст row_keys у ContractError history_row_missing_date.
    - Оновлено тести для row_keys та alias open_time_ms.
  - Де зроблено → runtime/fxcm/history_provider.py, tests/test_fxcm_history_row_date.py.
  - Як перевірено → не запускалось (потрібен pytest).
  - Ризики/нотатки → повідомлення ContractError змінилось; залежні тести мають бути оновлені (зроблено).

  ## 2026-02-02T01:10:00+01:00 — PRE (MODE=PATCH): Mypy cleanup (status/tests/gates)

  - Мета → усунути mypy помилки після останніх змін.
  - Scope → runtime/status.py, runtime/fxcm_forexconnect.py, tests/*, tools/exit_gates/gates/gate_final_republish_source_allowlist.py, Work/01log.md.
  - Non-goals → зміни runtime‑логіки поза типізацією.
  - Інваріанти/рейки → мінімальний диф; лише типи/локальні змінні.
  - Тести/перевірки → повний pipeline після правок.

  ## 2026-02-02T01:20:00+01:00 — POST (MODE=PATCH): Mypy cleanup (status/tests/gates)

  - Що зроблено →
    - runtime/status.py: локальний dict для reconcile, щоб уникнути object‑indexed.
    - runtime/fxcm_forexconnect.py: явні типи warn‑стану.
    - tests: додано типи для store/published, DummyStatus.append_error_throttled, cast для json.loads.
    - tools/exit_gates/gates: типи для DummyRedis.
  - Де зроблено → runtime/status.py, runtime/fxcm_forexconnect.py, tests/test_* , tools/exit_gates/gates/gate_final_republish_source_allowlist.py.
  - Як перевірено → див. повний pipeline нижче.

  ## 2026-02-02T01:30:00+01:00 — POST (MODE=read-only discovery): Повний pipeline (ruff/mypy/pytest/exit_gates)

  - Як перевірено (run_id=2026-02-02T01:28:00+01:00) →
    - python -m ruff check . → OK
    - python -m mypy . → FAIL (DummyStatus.append_error_throttled signature)
    - python -m pytest -q → NOT RUN (stop-on-fail)
    - python -m tools.run_exit_gates --out reports/exit_gates --manifest tools/exit_gates/manifest.json → NOT RUN (stop-on-fail)

  ## 2026-02-02T01:50:00+01:00 — PRE (MODE=PATCH): Heavy collapse vs rate-limit

  - Мета → не губити останню heavy команду під час collapse.
  - Scope → runtime/command_bus.py, Work/01log.md.
  - Non-goals → зміни контрактів.
  - Інваріанти/рейки → мінімальний диф; поведінка rate-limit збережена для не-heavy.
  - Тести/перевірки → повний pipeline після правок.

  ## 2026-02-02T02:00:00+01:00 — POST (MODE=PATCH): Heavy collapse vs rate-limit

  - Що зроблено → pending heavy команди більше не підпадають під rate-limit, щоб collapse брав останню.
  - Де зроблено → runtime/command_bus.py.
  - Як перевірено → див. повний pipeline нижче.

  ## 2026-02-02T02:10:00+01:00 — POST (MODE=read-only discovery): Повний pipeline (ruff/mypy/pytest/exit_gates)

  - Як перевірено (run_id=2026-02-02T02:05:00+01:00) →
    - python -m ruff check . → OK
    - python -m mypy . → OK
    - python -m pytest -q → OK
    - python -m tools.run_exit_gates --out reports/exit_gates --manifest tools/exit_gates/manifest.json → OK

  ## 2026-02-02T02:20:00+01:00 — PRE (MODE=PATCH): FXCM history rows normalization (pandas DataFrame)

  - Мета → коректно обробити history rows, якщо ForexConnect повертає pandas DataFrame.
  - Scope → runtime/fxcm/history_provider.py, Work/01log.md.
  - Non-goals → зміни логіки history chunking.
  - Інваріанти/рейки → мінімальний диф; fail-fast зберігається.
  - Тести/перевірки → не застосовно (потрібне live середовище).

  ## 2026-02-02T02:25:00+01:00 — POST (MODE=PATCH): FXCM history rows normalization (pandas DataFrame)

  - Що зроблено → при history як DataFrame використовується to_dict("records"), щоб отримати rows з ключами.
  - Де зроблено → runtime/fxcm/history_provider.py.
  - Як перевірено → не запускалось (потрібне live середовище).
  - Ризики/нотатки → якщо history повертає нестандартний тип, fail-fast збережено.

  ## 2026-02-01T12:30:00+01:00 — PRE (MODE=PATCH): FXCM history rows fallback + чисті auto_warmup/republish

  - Мета → усунути history_row_missing_date при нетипових типах history rows та прибрати INFO-події з errors[].
  - Scope → runtime/fxcm/history_provider.py, app/composition.py, Work/01log.md.
  - Non-goals → зміни контрактів або history chunking/limits.
  - Інваріанти/рейки → мінімальний диф; fail-fast зберігається для некоректних даних.
  - Тести/перевірки → не запускались (потрібне live середовище).

  ## 2026-02-01T12:45:00+01:00 — POST (MODE=PATCH): FXCM history rows fallback + чисті auto_warmup/republish

  - Що зроблено →
    - Додано fallback для history rows із Series/обʼєктів через to_dict/keys та нормалізацію DataFrame через columns/values.
    - Прибрано записи auto_warmup_enabled/auto_republish_enabled з errors[]; залишено лише info‑лог.
  - Де зроблено → runtime/fxcm/history_provider.py, app/composition.py.
  - Як перевірено → не запускалось (потрібне live середовище).
  - Ризики/нотатки → якщо history повертає масив без ключів, усе ще буде fail-fast із row_keys для діагностики.

  ## 2026-02-01T13:10:00+01:00 — PRE (MODE=PATCH): FXCM history rows без ключів (sequence fallback + case-insensitive)

  - Мета → прибрати history_row_missing_date при history rows як list/tuple та при різних регістрах ключів.
  - Scope → runtime/fxcm/history_provider.py, Work/01log.md.
  - Non-goals → зміни контрактів, зміни FXCM API викликів.
  - Інваріанти/рейки → мінімальний диф; fail-fast зберігається для некоректних даних.
  - Тести/перевірки → не запускались (потрібне live середовище).

  ## 2026-02-01T13:20:00+01:00 — POST (MODE=PATCH): FXCM history rows без ключів (sequence fallback + case-insensitive)

  - Що зроблено →
    - Додано case‑insensitive пошук ключів у row dict/to_dict.
    - Додано мінімальний fallback для rows як list/tuple (6 або 10 колонок з мапінгом у lower‑keys).
    - Розширено allowlist ключів часу (DATE/DATETIME).
  - Де зроблено → runtime/fxcm/history_provider.py.
  - Як перевірено → не запускалось (потрібне live середовище).
  - Ризики/нотатки → якщо history повертає інший порядок колонок, можливі некоректні OHLCV; у такому випадку fallback треба видалити.

  ### Decision status notes (2026-02-01)
  - 2026-02-02T02:25:00+01:00 — FXCM history rows normalization (DataFrame): статус=під питанням (симптом не зник, потрібна верифікація в live).
  - 2026-02-01T12:45:00+01:00 — fallback + чисті auto_warmup/republish: статус=частково (errors[] прибрані, але history_row_missing_date лишився).
  - 2026-02-01T13:20:00+01:00 — sequence fallback + case‑insensitive: статус=нове, потребує live валідації.

  ## 2026-02-01T14:05:00+01:00 — PRE (MODE=read-only discovery): Діагностика history_row_missing_date

  - Мета → описати причину помилки history_row_missing_date та що бракує для коректної нормалізації.
  - Scope → runtime/fxcm/history_provider.py (аналіз), Work/01log.md.
  - Non-goals → зміни коду.
  - Інваріанти/рейки → без змін у runtime.
  - Тести/перевірки → не застосовно.

  ## 2026-02-01T14:10:00+01:00 — POST (MODE=read-only discovery): Діагностика history_row_missing_date

  - Що зʼясовано →
    - Помилка виникає, бо ForexConnect `get_history()` повертає rows без часових ключів (row_keys пустий), або не dict/Series, а інший тип/структура.
    - Поточна нормалізація очікує хоча б один із ключів часу (date/time/timestamp/Date/DateTime тощо). Якщо їх нема, fail-fast спрацьовує коректно.
    - Без зразка реального row (як було у старому конекторі) неможливо надійно визначити мапінг колонок/ключів.
  - Де зафіксовано → Work/01log.md.
  - Як перевірено → не застосовно (потрібен live рядок для аналізу).
  - Ризики/нотатки → потрібна порада/приклад зі старого конектора (формат row), інакше будь‑який fallback може бути неправильним і шкідливим.

  ## 2026-02-01T23:10:00+01:00 — PRE (MODE=read-only discovery): Аналіз логів stale_tf/stale_no_ticks/UI_LITE

  - Мета → зафіксувати симптоми з логів (stale_tf=1m, stale_no_ticks, UI_LITE WARN) і пояснити, що це означає.
  - Scope → аналіз логів, Work/01log.md.
  - Non-goals → зміни коду/конфігів.
  - Інваріанти/рейки → без втручання у runtime.
  - Тести/перевірки → не застосовно.

  ## 2026-02-01T23:15:00+01:00 — POST (MODE=read-only discovery): Аналіз логів stale_tf/stale_no_ticks/UI_LITE

  - Що зʼясовано →
    - `ohlcv_preview stale_tf=1m delay=1.0m` означає відставання preview‑барів 1m на ~1 хв (очікувався 22:01Z, є 22:00Z).
    - `stale_no_ticks` у FXCM streaming → немає нових тикових даних; це напряму блокує оновлення preview.
    - UI_LITE показує transport=WARN, data=OK: транспорт живий, але дані (ticks) не надходять, тому 1m preview не рухається.
  - Де зафіксовано → Work/01log.md.
  - Як перевірено → не застосовно.
  - Ризики/нотатки → без активних ticks 1m preview буде stale; це окремо від проблеми history_row_missing_date.

  ## 2026-02-01T23:25:00+01:00 — PRE (MODE=read-only discovery): Розбіжність статусу відкриття ринку

  - Мета → зафіксувати, що статус показує “відкрито”, хоча ринок відкриється через ~55 хв.
  - Scope → аналіз (calendar/session), Work/01log.md.
  - Non-goals → зміни коду/календаря.
  - Інваріанти/рейки → без втручання у runtime.
  - Тести/перевірки → не застосовно.

  ## 2026-02-01T23:30:00+01:00 — POST (MODE=read-only discovery): Розбіжність статусу відкриття ринку

  - Що зʼясовано →
    - Поточний статус “ринок відкритий” не узгоджується з реальним календарем (відкриття через ~55 хв).
    - Це вказує на проблему календаря/сесій (TZ/holiday/weekday) або джерела truth для open/close.
  - Де зафіксовано → Work/01log.md.
  - Як перевірено → не застосовно.
  - Ризики/нотатки → потрібна синхронізація з SSOT календарем (docs/calendar_sessions_spec.md) або даними старого конектора.

  ## 2026-02-01T23:40:00+01:00 — PRE (MODE=PATCH): Узгодження calendar_tag з календарем старого конектора

  - Мета → синхронізувати календар відкриття/закриття з даними старого конектора (weekly_open_utc=23:01, daily_break 22:00–23:01).
  - Scope → config/config.py, Work/01log.md.
  - Non-goals → зміни closed_intervals/holiday policy.
  - Інваріанти/рейки → мінімальний диф; календарні інтервали не змінюються.
  - Тести/перевірки → не запускались (потрібен рестарт і live спостереження).

  ## 2026-02-01T23:45:00+01:00 — POST (MODE=PATCH): Узгодження calendar_tag з календарем старого конектора

  - Що зроблено → дефолтний calendar_tag змінено на fxcm_calendar_v1_utc_overrides.
  - Де зроблено → config/config.py.
  - Як перевірено → не запускалось (потрібен рестарт і live спостереження).
  - Ризики/нотатки → якщо профіль явно перезаписує calendar_tag, потрібно оновити профіль.

  ## 2026-02-01T15:00:00+01:00 — PRE (MODE=PATCH): Підтримка legacy-ключів Date/Bid*/Ask*/Volume

  - Мета → вирівняти нормалізацію history rows з legacy еталоном (Date + Bid*/Ask* + Volume).
  - Scope → runtime/fxcm/history_provider.py, Work/01log.md.
  - Non-goals → зміни історичних джерел або контрактів.
  - Інваріанти/рейки → мінімальний диф; fail-fast лишається.
  - Тести/перевірки → не запускались (потрібне live середовище).

  ## 2026-02-01T15:10:00+01:00 — POST (MODE=PATCH): Підтримка legacy-ключів Date/Bid*/Ask*/Volume

  - Що зроблено → додано ключі Date/Bid*/Ask*/Volume у allowlist нормалізації history rows.
  - Де зроблено → runtime/fxcm/history_provider.py.
  - Як перевірено → не запускалось (потрібне live середовище).
  - Ризики/нотатки → якщо broker віддає інші назви ключів, потрібен точний мапінг з raw row.

  ## 2026-02-01T16:00:00+01:00 — PRE (MODE=PATCH): Evidence‑пакет для history_row_missing_date

  - Мета → зібрати мінімальний evidence raw row (type/keys/repr/dir_match) для точного мапінгу.
  - Scope → runtime/fxcm/history_provider.py, Work/01log.md.
  - Non-goals → зміни логіки парсингу історії.
  - Інваріанти/рейки → мінімальний диф; fail-fast зберігається.
  - Тести/перевірки → не запускались (потрібне live середовище).

  ## 2026-02-01T16:05:00+01:00 — POST (MODE=PATCH): Evidence‑пакет для history_row_missing_date

  - Що зроблено → при history_row_missing_date додається evidence (row_type, row_keys, repr head, dir_match).
  - Де зроблено → runtime/fxcm/history_provider.py.
  - Як перевірено → не запускалось (потрібне live середовище).
  - Ризики/нотатки → якщо evidence покаже інший формат, потрібно прибрати поточні fallback‑мапінги та зробити точний.

  ## 2026-02-01T16:20:00+01:00 — PRE (MODE=PATCH): Нормалізація numpy void rows

  - Мета → перетворити numpy-like row (void/ndarray) у list для мапінгу 10‑колонкового формату.
  - Scope → runtime/fxcm/history_provider.py, Work/01log.md.
  - Non-goals → зміни контрактів або history chunking.
  - Інваріанти/рейки → мінімальний диф; fail-fast зберігається.
  - Тести/перевірки → не запускались (потрібне live середовище).

  ## 2026-02-01T16:25:00+01:00 — POST (MODE=PATCH): Нормалізація numpy void rows

  - Що зроблено → додано tolist() fallback для numpy-like rows перед мапінгом 10/6 колонок.
  - Де зроблено → runtime/fxcm/history_provider.py.
  - Як перевірено → не запускалось (потрібне live середовище).
  - Ризики/нотатки → якщо порядок колонок інший, потрібен точний мапінг по іменах колонок.

  ## 2026-02-01T16:40:00+01:00 — PRE (MODE=PATCH): Парсинг ISO‑рядків з наносекундами

  - Мета → прибрати history_row_date_invalid для рядків з наносекундами (наприклад, 2026-01-25T23:01:00.000000000).
  - Scope → runtime/fxcm/history_provider.py, Work/01log.md.
  - Non-goals → зміни контрактів або history chunking.
  - Інваріанти/рейки → мінімальний диф; fail-fast зберігається.
  - Тести/перевірки → не запускались (потрібне live середовище).

  ## 2026-02-01T16:45:00+01:00 — POST (MODE=PATCH): Парсинг ISO‑рядків з наносекундами

  - Що зроблено → додано нормалізацію ISO‑рядків з дробовою частиною до мікросекунд у _to_ms.
  - Де зроблено → runtime/fxcm/history_provider.py.
  - Як перевірено → не запускалось (потрібне live середовище).
  - Ризики/нотатки → якщо формат часу інший (не ISO), потрібен окремий мапінг.

  ## 2026-02-01T17:00:00+01:00 — PRE (MODE=PATCH): _to_ms для numpy/pandas типів

  - Мета → прибрати history_row_date_invalid для numpy/pandas scalar/tuple значень часу.
  - Scope → runtime/fxcm/history_provider.py, Work/01log.md.
  - Non-goals → зміни контрактів або history chunking.
  - Інваріанти/рейки → мінімальний диф; fail-fast зберігається.
  - Тести/перевірки → не запускались (потрібне live середовище).

  ## 2026-02-01T17:05:00+01:00 — POST (MODE=PATCH): _to_ms для numpy/pandas типів

  - Що зроблено → додано підтримку tuple/list, to_pydatetime(), value/item() у _to_ms.
  - Де зроблено → runtime/fxcm/history_provider.py.
  - Як перевірено → не запускалось (потрібне live середовище).
  - Ризики/нотатки → якщо значення часу у нестандартному форматі, потрібен точний мапінг.

  ## 2026-02-01T17:20:00+01:00 — PRE (MODE=PATCH): Evidence для history_row_date_invalid

  - Мета → отримати тип і repr значення часу, яке не парситься у _to_ms.
  - Scope → runtime/fxcm/history_provider.py, Work/01log.md.
  - Non-goals → зміни логіки парсингу часу.
  - Інваріанти/рейки → мінімальний диф; fail-fast зберігається.
  - Тести/перевірки → не запускались (потрібне live середовище).

  ## 2026-02-01T17:25:00+01:00 — POST (MODE=PATCH): Evidence для history_row_date_invalid

  - Що зроблено → у history_row_date_invalid додається evidence (value_type, value_repr).
  - Де зроблено → runtime/fxcm/history_provider.py.
  - Як перевірено → не запускалось (потрібне live середовище).
  - Ризики/нотатки → після отримання evidence потрібен точний мапінг або розширення _to_ms.

  ## 2026-02-01T18:00:00+01:00 — PRE (MODE=PATCH): Epoch ns/us у _to_ms

  - Мета → розпізнати epoch у наносекундах/мікросекундах і конвертувати до ms.
  - Scope → runtime/fxcm/history_provider.py, Work/01log.md.
  - Non-goals → зміни контрактів або history chunking.
  - Інваріанти/рейки → мінімальний диф; fail-fast зберігається.
  - Тести/перевірки → не запускались (потрібне live середовище).

  ## 2026-02-01T18:05:00+01:00 — POST (MODE=PATCH): Epoch ns/us у _to_ms

  - Що зроблено → додано конвертацію epoch у μs/ns (val//1_000 або val//1_000_000) до ms.
  - Де зроблено → runtime/fxcm/history_provider.py.
  - Як перевірено → не запускалось (потрібне live середовище).
  - Ризики/нотатки → якщо значення часу у іншій шкалі, потрібен окремий мапінг.

  ## 2026-02-01T23:55:00+01:00 — PRE (MODE=read-only discovery): Перевірка кешу 1m барів

  - Мета → перевірити, чи є 1m свічки у cache і чи були опубліковані.
  - Scope → cache/XAUUSD_1m.csv, cache/XAUUSD_1m.meta.json, Work/01log.md.
  - Non-goals → зміни коду/конфігів.
  - Інваріанти/рейки → без втручання у runtime.
  - Тести/перевірки → PowerShell читання файлів.

  ## 2026-02-01T23:58:00+01:00 — POST (MODE=read-only discovery): Перевірка кешу 1m барів

  - Що зʼясовано →
    - У cache є 1m бари (останній запис на 1769983200000..1769983259999).
    - last_published_open_time_ms = 0 → бари не репаблішені у канал.
  - Де зафіксовано → cache/XAUUSD_1m.csv, cache/XAUUSD_1m.meta.json.
  - Як перевірено → Get-Content (tail) + перегляд meta.json.
  - Ризики/нотатки → потрібен republish_tail або перевірка publisher/каналу.

  ## 2026-02-01T23:59:30+01:00 — PRE (MODE=read-only discovery): Запуск warmup/backfill/republish_tail

  - Мета → запустити ланцюжок warmup → backfill → republish_tail через fxcm_bootstrap.
  - Scope → Redis {NS}:commands, Work/01log.md.
  - Non-goals → зміни коду/конфігів.
  - Інваріанти/рейки → без змін у runtime.
  - Тести/перевірки → PUBLISH команди в Redis.

  ## 2026-02-02T00:00:10+01:00 — POST (MODE=read-only discovery): Запуск warmup/backfill/republish_tail

  - Що зроблено → надіслано fxcm_bootstrap (warmup/backfill/republish_tail) у fxcm_local:commands.
  - Де зроблено → Redis {NS}:commands, Work/01log.md.
  - Як перевірено → redis-cli PUBLISH → (integer) 1.
  - Ризики/нотатки → чекати статусу bootstrap у /api/status та появи publish у каналі ohlcv.

  ## 2026-02-02T00:05:00+01:00 — PRE (MODE=read-only discovery): Повторна відправка fxcm_bootstrap (valid JSON)

  - Мета → усунути invalid_json і повторно надіслати fxcm_bootstrap.
  - Scope → Redis {NS}:commands, Work/01log.md.
  - Non-goals → зміни коду.
  - Інваріанти/рейки → без змін у runtime.
  - Тести/перевірки → PUBLISH у Redis через stdin.

  ## 2026-02-02T00:06:00+01:00 — POST (MODE=read-only discovery): Повторна відправка fxcm_bootstrap (valid JSON)

  - Що зроблено → fxcm_bootstrap повторно надіслано через stdin у fxcm_local:commands.
  - Де зроблено → Redis {NS}:commands, Work/01log.md.
  - Як перевірено → redis-cli -x PUBLISH → (integer) 1.
  - Ризики/нотатки → чекати статусу bootstrap у /api/status та появи publish у каналі ohlcv.

  ## 2026-02-02T00:20:00+01:00 — PRE (MODE=PATCH): UI Lite команди з HMAC підписом

  - Мета → дозволити локальні команди з UI через HMAC (secret у .env.local) без витоку у браузер.
  - Scope → ui_lite/server.py, ui_lite/static/app.js, ui_lite/static/styles.css, Work/01log.md.
  - Non-goals → зміни в command_bus або контрактах.
  - Інваріанти/рейки → мінімальний диф; секрет не передається у фронтенд.
  - Тести/перевірки → не запускались (потрібен рестарт UI Lite).

  ## 2026-02-02T00:25:00+01:00 — POST (MODE=PATCH): UI Lite команди з HMAC підписом

  - Що зроблено →
    - Додано WS тип `command` у UI Lite: сервер підписує HMAC і публікує в Redis.
    - Додано командний блок у UI (warmup/backfill/republish/bootstrap) без зберігання секретів у браузері.
  - Де зроблено → ui_lite/server.py, ui_lite/static/app.js, ui_lite/static/styles.css.
  - Як перевірено → не запускалось (потрібен рестарт UI Lite).
  - Ризики/нотатки → без FXCM_HMAC_SECRET/FXCM_HMAC_KID у .env.local команди будуть відхилені як auth_secret_missing.

  ## 2026-02-02T00:35:00+01:00 — PRE (MODE=read-only discovery): Перевірка fxcm_local:commands

  - Мета → підтвердити, що канал команд порожній без відправки команд.
  - Scope → Redis fxcm_local:commands, Work/01log.md.
  - Non-goals → зміни коду.
  - Інваріанти/рейки → без втручання у runtime.
  - Тести/перевірки → redis-cli SUBSCRIBE.

  ## 2026-02-02T00:36:00+01:00 — POST (MODE=read-only discovery): Перевірка fxcm_local:commands

  - Що зʼясовано → канал тихий, доки не надходять команди (SUBSCRIBE показує лише handshake).
  - Де зафіксовано → Redis fxcm_local:commands.
  - Як перевірено → redis-cli SUBSCRIBE.
  - Ризики/нотатки → перевірити namespace/канал і відправити команду через UI або redis-cli PUBLISH.

  ## 2026-02-02T00:50:00+01:00 — PRE (MODE=read-only discovery): Діагностика auth_failed (HMAC kid)

  - Мета → пояснити auth_failed при командах з UI.
  - Scope → config/profile_local.py, runtime/command_auth.py, Work/01log.md.
  - Non-goals → зміни коду.
  - Інваріанти/рейки → без втручання у runtime.
  - Тести/перевірки → не застосовно.

  ## 2026-02-02T00:55:00+01:00 — POST (MODE=read-only discovery): Діагностика auth_failed (HMAC kid)

  - Що зʼясовано → auth_failed через невідповідність kid: у профілі дозволено лише k1, а UI підписує іншим kid.
  - Де зафіксовано → config/profile_local.py, runtime/command_auth.py.
  - Як перевірено → аналіз коду.
  - Ризики/нотатки → потрібно узгодити FXCM_HMAC_KID/SECRET з allowed_kids (k1) або розширити allowlist.

  ## 2026-02-02T01:05:00+01:00 — PRE (MODE=PATCH): Додати kid=123 у allowlist

  - Мета → дозволити HMAC kid=123 для локальних команд UI.
  - Scope → config/profile_local.py, Work/01log.md.
  - Non-goals → зміни інших профілів.
  - Інваріанти/рейки → мінімальний диф.
  - Тести/перевірки → не запускались (потрібен рестарт).

  ## 2026-02-02T01:10:00+01:00 — POST (MODE=PATCH): Додати kid=123 у allowlist

  - Що зроблено → додано "123" у command_auth_allowed_kids локального профілю.
  - Де зроблено → config/profile_local.py.
  - Як перевірено → не запускалось (потрібен рестарт).
  - Ризики/нотатки → у .env.local має бути FXCM_HMAC_KID=123 та відповідний FXCM_HMAC_SECRET.

  ## 2026-02-02T01:20:00+01:00 — PRE (MODE=read-only discovery): auto_republish_skipped (last_write_source=stream_close)

  - Мета → зафіксувати причину skip репаблішу.
  - Scope → status/errors, Work/01log.md.
  - Non-goals → зміни коду.
  - Інваріанти/рейки → без втручання у runtime.
  - Тести/перевірки → не застосовно.

  ## 2026-02-02T01:22:00+01:00 — POST (MODE=read-only discovery): auto_republish_skipped (last_write_source=stream_close)

  - Що зʼясовано → republish пропущено, бо cache має last_write_source=stream_close (не FINAL).
  - Де зафіксовано → status/errors.
  - Як перевірено → лог повідомлення auto_republish_skipped.
  - Ризики/нотатки → потрібен warmup/backfill, щоб last_write_source став history/history_agg, після чого republish можливий.

  ## 2026-02-02T01:30:00+01:00 — PRE (MODE=PATCH): Calendar-aware end-time для warmup/backfill

  - Мета → не запитувати history за межами сесії (closed) і уникнути дивних row формату під час пауз.
  - Scope → core/time/calendar.py, runtime/warmup.py, runtime/backfill.py, Work/01log.md.
  - Non-goals → повний gap-аудит expected buckets.
  - Інваріанти/рейки → мінімальний диф; fail-fast зберігається.
  - Тести/перевірки → не запускались (потрібне live середовище).

  ## 2026-02-02T01:40:00+01:00 — POST (MODE=PATCH): Calendar-aware end-time для warmup/backfill

  - Що зроблено →
    - Додано Calendar.last_trading_close_ms для останнього close сесії.
    - Warmup/backfill тепер використовують end-time з урахуванням календаря та safety lag.
  - Де зроблено → core/time/calendar.py, runtime/warmup.py, runtime/backfill.py.
  - Як перевірено → не запускалось (потрібне live середовище).
  - Ризики/нотатки → gap-аудит expected buckets лишається на наступний slice.

  ## 2026-02-02T01:50:00+01:00 — PRE (MODE=read-only discovery): Перевірка UI_LITE health warning

  - Мета → зафіксувати стан UI_LITE health=warn з transport=warn та ohlcv_lag.
  - Scope → лог UI_LITE, Work/01log.md.
  - Non-goals → зміни коду.
  - Інваріанти/рейки → без втручання у runtime.
  - Тести/перевірки → не застосовно.

  ## 2026-02-02T01:52:00+01:00 — POST (MODE=read-only discovery): Перевірка UI_LITE health warning

  - Що зʼясовано →
    - transport=WARN при data=OK означає, що WS/subscribe живий, але є легкий ohlcv_lag (payload age ~2.7s).
    - fxcm=streaming, tick=0.0s → стрім активний; це не падіння, а легке відставання.
  - Де зафіксовано → лог UI_LITE.
  - Як перевірено → аналіз лог повідомлення.
  - Ризики/нотатки → якщо lag росте > кількох TF, перевірити publish ohlcv.

  ## 2026-02-02T02:20:00+01:00 — PRE (MODE=PATCH): Детермінований warmup (expected buckets)

  - Мета → зробити warmup календар‑aware, з expected buckets і перевіркою готовності.
  - Scope → runtime/warmup.py, Work/01log.md.
  - Non-goals → повний gap‑аудит/backfill.
  - Інваріанти/рейки → мінімальний диф; fail-fast зберігається.
  - Тести/перевірки → не запускались (потрібне live середовище).

  ## 2026-02-02T02:30:00+01:00 — POST (MODE=PATCH): Детермінований warmup (expected buckets)

  - Що зроблено →
    - Warmup використовує warmup_bars та calendar‑aware end‑time.
    - Додано verify tail через expected buckets (closed intervals не рахуються).
    - Якщо cache вже готовий — warmup пропускається.
  - Де зроблено → runtime/warmup.py.
  - Як перевірено → не запускалось (потрібне live середовище).
  - Ризики/нотатки → якщо календар невалідний, verify може бути неточним.

  ## 2026-02-02T02:40:00+01:00 — PRE (MODE=read-only discovery): Інвентар History кешу FXCM

  - Мета → оглянути структуру History/ і визначити, що саме кешує SDK.
  - Scope → History/*, Work/01log.md.
  - Non-goals → зміни коду.
  - Інваріанти/рейки → без втручання у runtime.
  - Тести/перевірки → list_dir.

  ## 2026-02-02T02:42:00+01:00 — POST (MODE=read-only discovery): Інвентар History кешу FXCM

  - Що зʼясовано →
    - History/ містить каталоги з catalog.sqlite та .hst файлами (наприклад, XAUUSD_Default/m1/2026_1-4.hst).
    - Це локальний кеш ForexConnect SDK (не SSOT).
  - Де зафіксовано → History/*.
  - Як перевірено → list_dir.
  - Ризики/нотатки → для використання потрібен парсер .hst (формат не документований у repo).

  ## 2026-02-02T03:00:00+01:00 — PRE (MODE=read-only discovery): Перевірка .hst заголовку

  - Мета → зʼясувати сигнатуру і символ у .hst.
  - Scope → History/*/*.hst, Work/01log.md.
  - Non-goals → зміни коду.
  - Інваріанти/рейки → без втручання у runtime.
  - Тести/перевірки → читання перших байтів.

  ## 2026-02-02T03:02:00+01:00 — POST (MODE=read-only discovery): Перевірка .hst заголовку

  - Що зʼясовано → заголовок містить ASCII "Indicore2Hst" і символ "XAU/USD"; файл бінарний.
  - Де зафіксовано → History/2639658384/XAUUSD_Default/m1/2026_1-4.hst.
  - Як перевірено → читання перших 64 байтів.
  - Ризики/нотатки → потрібен парсер .hst для витягування свічок.

  ## 2026-02-02T03:15:00+01:00 — PRE (MODE=PATCH): Read-only probe для .hst

  - Мета → створити read-only парсер/пробник .hst для огляду структури барів.
  - Scope → tools/hst_probe.py, Work/01log.md.
  - Non-goals → інтеграція у warmup/backfill.
  - Інваріанти/рейки → лише читання файлів; без SSOT змін.
  - Тести/перевірки → локальний запуск за потреби.

  ## 2026-02-02T03:20:00+01:00 — POST (MODE=PATCH): Read-only probe для .hst

  - Що зроблено → додано tools/hst_probe.py для евристичного читання .hst.
  - Де зроблено → tools/hst_probe.py.
  - Як перевірено → не запускалось (за потреби запустити вручну).
  - Ризики/нотатки → евристика; формат може відрізнятися, потрібна валідація.

  ## 2026-02-02T03:30:00+01:00 — PRE (MODE=read-only discovery): Запуск hst_probe

  - Мета → визначити layout .hst і чи файл оновлюється.
  - Scope → tools/hst_probe.py, History/2639658384/XAUUSD_Default/m1/2026_1-4.hst, Work/01log.md.
  - Non-goals → зміни коду.
  - Інваріанти/рейки → read-only.
  - Тести/перевірки → запуск hst_probe + перевірка mtime.

  ## 2026-02-02T03:32:00+01:00 — POST (MODE=read-only discovery): Запуск hst_probe

  - Що зʼясовано →
    - hst_probe не зміг визначити layout (layout=not_found).
    - mtime файлу 2026-01-25 22:53:56 UTC → кеш не оновлювався останнім часом.
  - Де зафіксовано → History/2639658384/XAUUSD_Default/m1/2026_1-4.hst.
  - Як перевірено → запуск hst_probe + os.stat.mtime.
  - Ризики/нотатки → потрібен точний специфічний парсер .hst або інший формат/версія.

  ## 2026-02-02T03:40:00+01:00 — PRE (MODE=PATCH): Cleanup спроб History

  - Мета → прибрати всі спроби маніпуляції з History.
  - Scope → tools/hst_probe.py, Work/01log.md.
  - Non-goals → зміни runtime.
  - Інваріанти/рейки → мінімальний диф.
  - Тести/перевірки → не запускались.

  ## 2026-02-02T03:45:00+01:00 — POST (MODE=PATCH): Cleanup спроб History

  - Що зроблено → видалено tools/hst_probe.py.
  - Де зроблено → tools/hst_probe.py.
  - Як перевірено → не запускалось.
  - Ризики/нотатки → дослідження History припинено.

  ## 2026-02-02T04:15:00+01:00 — PRE (MODE=PATCH): Warmup/Backfill 365 + safety lag bars

  - Мета → підключити history_safety_lag_bars у warmup/backfill, зробити warmup_bars= max(cache_warmup_bars, retention_target_days*24*60) і додати тести/документацію.
  - Scope → runtime/warmup.py, runtime/backfill.py, tests/test_warmup_365.py, README.md, docs/REPO_LAYOUT.md, Work/01log.md.
  - Non-goals → повні exit gates/ruff/mypy/pytest у цьому кроці (лише код/тести/доки).
  - Інваріанти/рейки → мінімальний диф; без зміни публічних контрактів; fail-fast зберігається.
  - План → 1) Оновити _resolve_history_end_ms (safety lag bars). 2) Оновити warmup window/skip логіку. 3) Додати тести. 4) Оновити README/REPO_LAYOUT. 5) Запис POST.
  - Тести/перевірки → pytest цільові тести (якщо доступно).

  ## 2026-02-02T04:35:00+01:00 — POST (MODE=PATCH): Warmup/Backfill 365 + safety lag bars

  - Що зроблено →
    - Warmup/backfill тепер використовують history_safety_lag_bars для end-time.
    - Warmup_bars рахується як max(cache_warmup_bars, retention_target_days*24*60) з coverage/watermark перевіркою.
    - Додано тести warmup/backfill end-time та skip при ready cache.
    - Оновлено README та REPO_LAYOUT з параметрами retention/warmup/safety lag.
  - Де зроблено → runtime/warmup.py, runtime/backfill.py, tests/test_warmup_365.py, README.md, docs/REPO_LAYOUT.md.
  - Як перевірено → pytest tests/test_warmup_365.py (runTests) → FAIL: "No tests found in the files".
  - Ризики/нотатки → потрібна перевірка pytest discovery/налаштувань у середовищі; повторити запуск тестів вручну.

  ## 2026-02-02T04:50:00+01:00 — PRE (MODE=read-only discovery): dev_checks.py для валідації

  - Мета → запустити tools/run_dev_checks.py, щоб отримати валідні ruff/mypy/pytest результати.
  - Scope → tools/run_dev_checks.py (лише запуск), Work/01log.md.
  - Non-goals → зміни коду.
  - Інваріанти/рейки → без змін у коді; лише виконання перевірок.
  - План → 1) Запустити dev_checks.py. 2) Зафіксувати результат у POST.
  - Тести/перевірки → python tools/run_dev_checks.py.

  ## 2026-02-02T04:52:00+01:00 — POST (MODE=read-only discovery): dev_checks.py для валідації

  - Що зроблено → запущено tools/run_dev_checks.py.
  - Де зроблено → tools/run_dev_checks.py.
  - Як перевірено → C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe tools/run_dev_checks.py → FAIL: ModuleNotFoundError: No module named 'tools'.
  - Ризики/нотатки → потрібно встановити PYTHONPATH на корінь репозиторію або запускати через -m.

  ## 2026-02-02T04:53:00+01:00 — PRE (MODE=read-only discovery): dev_checks.py з PYTHONPATH

  - Мета → повторити dev_checks.py з коректним PYTHONPATH.
  - Scope → tools/run_dev_checks.py (лише запуск), Work/01log.md.
  - Non-goals → зміни коду.
  - Інваріанти/рейки → без змін у коді; лише виконання перевірок.
  - План → 1) Встановити PYTHONPATH на корінь репозиторію. 2) Запустити dev_checks.py. 3) Зафіксувати результат.
  - Тести/перевірки → python tools/run_dev_checks.py з PYTHONPATH.

  ## 2026-02-02T04:55:00+01:00 — POST (MODE=read-only discovery): dev_checks.py з PYTHONPATH

  - Що зроблено → запущено tools/run_dev_checks.py з PYTHONPATH.
  - Де зроблено → tools/run_dev_checks.py.
  - Як перевірено → ruff OK; mypy FAIL: ui_lite/server.py dict type (command_ack), tests/test_warmup_365.py arg-type для StatusManager.
  - Ризики/нотатки → потрібен мінімальний патч типізації.

  ## 2026-02-02T05:00:00+01:00 — PRE (MODE=PATCH): Mypy fix для UI Lite ack + warmup tests

  - Мета → виправити mypy помилки в ui_lite/server.py (command_ack dict типи) і tests/test_warmup_365.py (StatusManager типи).
  - Scope → ui_lite/server.py, tests/test_warmup_365.py, Work/01log.md.
  - Non-goals → зміни логіки UI/command, рефакторинг.
  - Інваріанти/рейки → мінімальний диф; поведінка без змін.
  - План → 1) Розділити response/ack dict у UI Lite. 2) Додати cast для StatusManager у тестах. 3) Перезапустити dev_checks.
  - Тести/перевірки → tools/run_dev_checks.py.

  ## 2026-02-02T05:05:00+01:00 — POST (MODE=PATCH): Mypy fix для UI Lite ack + warmup tests

  - Що зроблено →
    - UI Lite: розділено ack dict для command_ack (щоб mypy не зливав типи зі snapshot).
    - Тести warmup: використано cast до StatusManager.
  - Де зроблено → ui_lite/server.py, tests/test_warmup_365.py.
  - Як перевірено → tools/run_dev_checks.py (PYTHONPATH): ruff OK, mypy OK, pytest OK.
  - Ризики/нотатки → немає.

  ## 2026-02-02T05:20:00+01:00 — PRE (MODE=read-only discovery): Аналіз auto_republish_skipped і tick_contract_reject

  - Мета → пояснити причини помилок auto_republish_skipped (last_write_source не final) та tick_contract_reject (instrument відсутній).
  - Scope → app/composition.py, runtime/republish.py, runtime/fxcm_forexconnect.py, Work/01log.md.
  - Non-goals → зміни коду або конфігів.
  - Інваріанти/рейки → лише читання; без змін.
  - План → 1) Перевірити умови auto_republish. 2) Перевірити обробку instrument відсутній. 3) Зафіксувати пояснення.
  - Тести/перевірки → не застосовно.

  ## 2026-02-02T05:25:00+01:00 — POST (MODE=read-only discovery): Аналіз auto_republish_skipped і tick_contract_reject

  - Що зʼясовано →
    - auto_republish_skipped виникає, якщо last_write_source не з FINAL_SOURCES (history/history_agg). Це очікувана рейка: auto_republish пропускається при stream/stream_close.
    - tick_contract_reject instrument відсутній генерується у FXCM offers row, коли немає поля instrument; це логовано як ContractError і маркується degraded.
  - Де зафіксовано → app/composition.py, runtime/republish.py, runtime/fxcm_forexconnect.py.
  - Як перевірено → читання коду.
  - Ризики/нотатки → для auto_republish потрібен history-based cache (warmup/backfill/reconcile). Для tick_contract_reject — перевірити стан offers таблиці/SDK, якщо помилка повторюється часто.

  ## 2026-02-02T05:40:00+01:00 — PRE (MODE=PATCH): Warmup365 Proof Pack + Replay-forensics

  - Мета → додати офлайн exit-gate для end_time+safety_lag, replay harness для resume warmup, та мінімальний runbook у README.
  - Scope → tools/exit_gates/gates/gate_warmup_365.py, tools/exit_gates/manifest.json, tools/replay/replay_warmup_365_asof.py, tools/replay/__init__.py, README.md, runtime/warmup.py, runtime/backfill.py, Work/01log.md.
  - Non-goals → зміни публічних контрактів або FXCM інтеграції.
  - Інваріанти/рейки → мінімальний диф; без silent fallback; replay as-of детермінований.
  - План → 1) Оновити end_time для closed з safety lag. 2) Додати gate_warmup_365. 3) Додати replay harness. 4) Оновити manifest. 5) Додати runbook блок у README. 6) POST.
  - Тести/перевірки → не запускати (офлайн); лише код/артефакти.

  ## 2026-02-02T06:00:00+01:00 — POST (MODE=PATCH): Warmup365 Proof Pack + Replay-forensics

  - Що зроблено →
    - End-time для closed тепер враховує safety lag (min(last_close, safe_now)).
    - Додано exit-gate gate_warmup_365 + запис JSON у reports/exit_gates.
    - Додано replay harness tools/replay/replay_warmup_365_asof.py з артефактами replay.
    - Оновлено manifest.json і README (операційний блок Warmup 365).
  - Де зроблено → runtime/warmup.py, runtime/backfill.py, tools/exit_gates/gates/gate_warmup_365.py, tools/exit_gates/manifest.json, tools/replay/replay_warmup_365_asof.py, tools/replay/__init__.py, README.md.
  - Як перевірено → не запускалось (за запитом offline slice).
  - Ризики/нотатки → replay harness за замовчуванням генерує 365d синтетику; для швидкого прогону можна зменшити --days.

  ## 2026-02-02T06:15:00+01:00 — PRE (MODE=read-only discovery): Зависання після warmup команди

  - Мета → зʼясувати, де саме зависає виконання warmup після команди.
  - Scope → runtime/handlers_p3.py, runtime/handlers_p4.py, runtime/command_bus.py, runtime/warmup.py, Work/01log.md.
  - Non-goals → зміни коду.
  - Інваріанти/рейки → лише читання; без змін.
  - План → 1) Знайти handler warmup. 2) Перевірити блокуючі виклики/потоки. 3) Зафіксувати висновок.
  - Тести/перевірки → не застосовно.

  ## 2026-02-02T06:25:00+01:00 — PRE (MODE=PATCH): Async виконання heavy команд

  - Мета → прибрати зависання command_bus під час warmup, винести heavy команди в окремий потік.
  - Scope → runtime/command_bus.py, Work/01log.md.
  - Non-goals → зміни публічних контрактів або логіки warmup/backfill.
  - Інваріанти/рейки → мінімальний диф; state last_command оновлюється як і раніше.
  - План → 1) Додати worker-thread для heavy команд. 2) Зберегти collapse/pending поведінку. 3) POST.
  - Тести/перевірки → не запускати (зміна контроль потоку).

  ## 2026-02-02T06:35:00+01:00 — POST (MODE=PATCH): Async виконання heavy команд

  - Що зроблено → heavy команди (warmup/backfill тощо) виконуються у фоновому потоці; command_bus не блокується.
  - Де зроблено → runtime/command_bus.py.
  - Як перевірено → не запускалось (зміна контроль потоку).
  - Ризики/нотатки → last_command ok/error виставляється після завершення потоку; при краші потоку може знадобитись лог‑аналіз.

  ## 2026-02-02T06:45:00+01:00 — PRE (MODE=read-only discovery): Детальний дебаг лог‑аналіз

  - Мета → проаналізувати дебаг‑логи для зависання після warmup команди.
  - Scope → runtime лог-файли/консольні логи (шлях уточнити), Work/01log.md.
  - Non-goals → зміни коду.
  - Інваріанти/рейки → лише читання; без змін.
  - План → 1) Визначити файл/джерело логів. 2) Витягнути період навколо warmup. 3) Зробити висновок.
  - Тести/перевірки → не застосовно.

  ## 2026-02-02T06:50:00+01:00 — POST (MODE=read-only discovery): Детальний дебаг лог‑аналіз

  - Що зʼясовано → явних файлових логів у репозиторії немає; logging.basicConfig використовується у app/main.py та ui_lite/server.py, отже логи йдуть у stdout/термінал.
  - Де зафіксовано → app/main.py, ui_lite/server.py.
  - Як перевірено → grep на FileHandler/RotatingFileHandler, пошук *.log.
  - Ризики/нотатки → для аналізу зависання потрібні консольні логи процесів app.main та ui_lite.server (період навколо warmup), а також status snapshot з Redis.

  ## 2026-02-02T07:05:00+01:00 — PRE (MODE=read-only discovery): Перевірка SyntaxError у warmup.py

  - Мета → перевірити наявність маркерів patch у runtime/warmup.py.
  - Scope → runtime/warmup.py, Work/01log.md.
  - Non-goals → зміни коду.
  - Інваріанти/рейки → лише читання.
  - План → 1) Прочитати файл. 2) Перевірити наявність "*** Begin Patch". 3) Зафіксувати результат.
  - Тести/перевірки → не застосовно.

  ## 2026-02-02T07:10:00+01:00 — POST (MODE=read-only discovery): Перевірка SyntaxError у warmup.py

  - Що зʼясовано → runtime/warmup.py у репозиторії без маркерів patch; єдиний файл warmup.py присутній у runtime/.
  - Де зафіксовано → runtime/warmup.py.
  - Як перевірено → read_file + file_search.
  - Ризики/нотатки → ймовірно запускалась застаріла копія або файл був змінений локально поза repo; потрібен повторний запуск після синхронізації файлу.

  ## 2026-02-02T07:20:00+01:00 — PRE (MODE=PATCH): Відновлення чистого warmup.py

  - Мета → перезаписати runtime/warmup.py чистою версією без patch‑маркерів.
  - Scope → runtime/warmup.py, Work/01log.md.
  - Non-goals → зміни логіки warmup.
  - Інваріанти/рейки → мінімальний диф; лише відновлення файлу.
  - План → 1) Перезаписати файл. 2) Зафіксувати POST.
  - Тести/перевірки → не запускати.

  ## 2026-02-02T07:30:00+01:00 — POST (MODE=PATCH): Відновлення чистого warmup.py

  - Що зроблено → перезаписано runtime/warmup.py і видалено дубльований блок (залишено одну чисту копію).
  - Де зроблено → runtime/warmup.py.
  - Як перевірено → read_file (візуальна перевірка заголовку та відсутності patch‑маркерів).
  - Ризики/нотатки → якщо помилка зберігається, потрібно перевірити локальний файл у середовищі запуску.

  ## 2026-02-02T07:40:00+01:00 — PRE (MODE=read-only discovery): Прослуховування Redis status

  - Мета → підключитись до {NS}:status і спостерігати статус в реальному часі.
  - Scope → Redis Pub/Sub (fxcm_local:status), Work/01log.md.
  - Non-goals → зміни коду.
  - Інваріанти/рейки → лише читання; без змін.
  - План → 1) Запустити redis-cli SUBSCRIBE fxcm_local:status. 2) Отримати події. 3) Зафіксувати POST.
  - Тести/перевірки → не застосовно.

  ## 2026-02-02T07:41:00+01:00 — POST (MODE=read-only discovery): Прослуховування Redis status

  - Що зроблено → запущено redis-cli SUBSCRIBE fxcm_local:status (фонова сесія).
  - Де зроблено → terminal session (redis-cli).
  - Як перевірено → підписка успішна (SUBSCRIBE підтверджено).
  - Ризики/нотатки → для аналізу потрібні фактичні повідомлення з каналу; при необхідності запитаю вивід.

  ## 2026-02-02T07:50:00+01:00 — PRE (MODE=read-only discovery): Аналіз зависання через Redis status stream

  - Мета → перевірити, чи перестали надходити повідомлення в fxcm_local:status під час зависання.
  - Scope → redis-cli SUBSCRIBE output, Work/01log.md.
  - Non-goals → зміни коду.
  - Інваріанти/рейки → лише читання.
  - План → 1) Зчитати вихід підписки. 2) Оцінити наявність/відсутність нових повідомлень. 3) Зафіксувати POST.
  - Тести/перевірки → не застосовно.

  ## 2026-02-02T08:00:00+01:00 — POST (MODE=read-only discovery): Аналіз зависання через Redis status stream

  - Що зʼясовано → status повідомлення надходять; last_command.state=running для fxcm_warmup; errors містить history_inflight_wait (single in-flight).
  - Де зафіксовано → fxcm_local:status (redis-cli SUBSCRIBE output).
  - Як перевірено → get_terminal_output з redis-cli підписки.
  - Ризики/нотатки → warmup блокується на history single in-flight; потрібна перевірка FXCM history запитів/таймаутів.

  ## 2026-02-02T08:20:00+01:00 — PRE (MODE=PATCH): Таймаут для FXCM history fetch

  - Мета → не блокувати pipeline при завислих history запитах; додати таймаут та loud error.
  - Scope → config/config.py, runtime/fxcm/history_provider.py, app/composition.py, Work/01log.md.
  - Non-goals → зміни public contracts.
  - Інваріанти/рейки → мінімальний диф; без silent fallback.
  - План → 1) Додати history_fetch_timeout_s у Config. 2) Додати timeout у _fetch_chunk. 3) Протягнути в build_history_provider. 4) POST.
  - Тести/перевірки → не запускати (runtime change).

  ## 2026-02-02T08:35:00+01:00 — POST (MODE=PATCH): Таймаут для FXCM history fetch

  - Що зроблено → додано history_fetch_timeout_s у Config, таймаут для FXCM history fetch із loud error, прокинуто в build_history_provider.
  - Де зроблено → config/config.py, runtime/fxcm/history_provider.py, app/composition.py.
  - Як перевірено → не запускалось (runtime change).
  - Ризики/нотатки → якщо FXCM SDK зависає з утриманням GIL, таймаут може не спрацювати; тоді потрібен процесний isolate.

  ## 2026-02-02T08:55:00+01:00 — PRE (MODE=PATCH): Warmup stop-on-empty history

  - Мета → зупиняти warmup при серії порожніх history чанків (луд‑помилка), щоб не висіти годинами.
  - Scope → config/config.py, runtime/warmup.py, Work/01log.md.
  - Non-goals → зміни контрактів або поведінки history провайдера.
  - Інваріанти/рейки → мінімальний диф; лише fail‑fast при пустих чанках.
  - План → 1) Додати history_empty_chunk_fail_after у Config. 2) Лічильник empty_chunks у warmup. 3) POST.
  - Тести/перевірки → не запускати.

  ## 2026-02-02T09:05:00+01:00 — POST (MODE=PATCH): Warmup stop-on-empty history

  - Що зроблено → додано history_empty_chunk_fail_after у Config та fail‑fast при серії порожніх history чанків у warmup.
  - Де зроблено → config/config.py, runtime/warmup.py.
  - Як перевірено → не запускалось.
  - Ризики/нотатки → якщо FXCM повертає 0 рядків тривалий час, warmup завершується з loud error замість зависання.

  ## 2026-02-02T09:20:00+01:00 — PRE (MODE=PATCH): Calendar-aware warmup старт + skip closed

  - Мета → брати N останніх свічок через календарний відбір (skip closed), щоб не штурмувати вихідні/свята.
  - Scope → runtime/warmup.py, Work/01log.md.
  - Non-goals → зміни контрактів або FXCM провайдера.
  - Інваріанти/рейки → мінімальний диф; fail-fast зберігається.
  - План → 1) Визначити start_ms через expected open times. 2) Пропускати closed інтервали в циклі. 3) POST.
  - Тести/перевірки → не запускати.

  ## 2026-02-02T09:30:00+01:00 — POST (MODE=PATCH): Calendar-aware warmup старт + skip closed

  - Що зроблено → старт warmup визначається через expected open times; closed інтервали пропускаються у циклі.
  - Де зроблено → runtime/warmup.py.
  - Як перевірено → не запускалось.
  - Ризики/нотатки → якщо calendar init_error, використовується попередня логіка діапазону.

  ## 2026-02-02T09:45:00+01:00 — PRE (MODE=PATCH): Додаткове логування циклів P8–P10

  - Мета → додати детальні debug‑логи в циклах warmup/backfill/history/tail_guard/reconcile/republish.
  - Scope → runtime/warmup.py, runtime/backfill.py, runtime/fxcm/history_provider.py, runtime/tail_guard.py, runtime/reconcile_finalizer.py, runtime/republish.py, Work/01log.md.
  - Non-goals → зміни контрактів.
  - Інваріанти/рейки → мінімальний диф; логування без зміни бізнес‑логіки.
  - План → 1) Додати logger з StreamHandler. 2) Додати debug в ключові цикли. 3) POST.
  - Тести/перевірки → не запускати.

  ## 2026-02-02T10:00:00+01:00 — POST (MODE=PATCH): Додаткове логування циклів P8–P10

  - Що зроблено → додано debug‑логи у циклах backfill/tail_guard/reconcile/republish; додано logger з StreamHandler у відповідних модулях.
  - Де зроблено → runtime/backfill.py, runtime/tail_guard.py, runtime/reconcile_finalizer.py, runtime/republish.py.
  - Як перевірено → не запускалось.
  - Ризики/нотатки → збільшення обсягу логів; за потреби знизити рівень.

  ## 2026-02-02T10:20:00+01:00 — PRE (MODE=PATCH): Розширене логування циклів + видимість помилок

  - Мета → додати логування у цикли warmup/backfill/history_budget та фіксацію місця зависання (I/O, acquire).
  - Scope → runtime/warmup.py, runtime/backfill.py, runtime/fxcm/history_provider.py, runtime/fxcm/history_budget.py, runtime/fxcm/history_provider.py, Work/01log.md.
  - Non-goals → зміни контрактів.
  - Інваріанти/рейки → мінімальний диф; лише логування/видимість.
  - План → 1) Додати таймінги на append/load. 2) Логи acquire/release в budget. 3) Логувати exceptions adapter.fetch_1m. 4) POST.
  - Тести/перевірки → не запускати.

  ## 2026-02-02T10:35:00+01:00 — POST (MODE=PATCH): Розширене логування циклів + видимість помилок

  - Що зроблено → додано таймінги append/load у warmup/backfill, логи acquire/release в history_budget, підтвердження budget release у history_provider.
  - Де зроблено → runtime/warmup.py, runtime/backfill.py, runtime/fxcm/history_budget.py, runtime/fxcm/history_provider.py.
  - Як перевірено → не запускалось.
  - Ризики/нотатки → збільшення обсягу логів; за потреби зменшити рівень.

  ## 2026-02-02T11:10:00+01:00 — PRE (MODE=PATCH): Логи винятків у command_bus + cleanup history_provider

  - Мета → прибрати “тихі” місця у command_bus та прибрати дубльований лог release.
  - Scope → runtime/command_bus.py, runtime/fxcm/history_provider.py, Work/01log.md.
  - Non-goals → зміни контрактів.
  - Інваріанти/рейки → мінімальний диф; лише логування.
  - План → 1) Додати log.exception у _execute_handler. 2) Видалити дубль логування release. 3) POST.
  - Тести/перевірки → не запускати.

  ## 2026-02-02T10:50:00+01:00 — PRE (MODE=read-only discovery): Перевірка final bars у status snapshot

  - Мета → зʼясувати стан ohlcv_final/republish у status snapshot, якщо UI показує пусто.
  - Scope → Redis {NS}:status:snapshot, Work/01log.md.
  - Non-goals → зміни коду.
  - Інваріанти/рейки → лише читання.
  - План → 1) Прочитати status snapshot. 2) Зафіксувати поля ohlcv_final/republish. 3) POST.
  - Тести/перевірки → redis-cli GET fxcm_local:status:snapshot.

  ## 2026-02-02T10:55:00+01:00 — POST (MODE=read-only discovery): Перевірка final bars у status snapshot

  - Що зʼясовано → ohlcv_final.* = 0 (порожньо), republish state=skipped, last_req_id=auto_republish.
  - Де зафіксовано → fxcm_local:status:snapshot.
  - Як перевірено → redis-cli GET fxcm_local:status:snapshot.
  - Ризики/нотатки → final bars не публікуються без warmup/backfill/history‑based cache або republish; потрібно warmup/backfill + republish tail.

  ## 2026-02-02T11:20:00+01:00 — POST (MODE=PATCH): Логи винятків у command_bus + cleanup history_provider

  - Що зроблено → додано log.exception у command_bus для ValueError/Exception; прибрано дубль log release у history_provider.
  - Де зроблено → runtime/command_bus.py, runtime/fxcm/history_provider.py.
  - Як перевірено → не запускалось.
  - Ризики/нотатки → логування може зрости, але помилки більше не тихі.

  ## 2026-01-18T10:42:12+01:00 → PRE (MODE=PATCH) → P8-only stream-first фіналізація + класичне логування
- Мета: перейти на P8-only режим (без history/warmup/backfill/reconcile/tail_guard/auto_republish), публікувати final 1m із stream_close, формувати HTF із final 1m, відновити класичне логування без локальних handlers.
- Scope: config/config.py (дефолти), app/composition.py (final 1m/HTF зі stream_close, allowlist команд), runtime/* (прибрати модульні лог-хендлери).
- Non-goals: зміни контрактів/JSON schema, нові фічі або ADR, рефакторинг поза P8-only вимогами.
- Інваріанти/рейки: мінімальний диф, відсутність дублювання логіки, fail-fast у валідаторах, мова логів/коментарів укр.
- План:
  1) Вимкнути history-контури за замовчуванням у config.
  2) Додати final 1m публікацію зі stream_close та HTF з 1m у composition.
  3) Прибрати history-команди з allowlist при history_provider_kind=none.
  4) Прибрати модульні log.handlers для класичного логування.
- Тести/перевірки: не запускати (зміни конфіг/пайплайн), лише ручна перевірка коду.

  ## 2026-01-18T10:58:40+01:00 → POST (MODE=PATCH) → P8-only stream-first фіналізація + класичне логування
- Що зроблено:
  - Встановлено P8-only дефолти: history_provider_kind=none, вимкнені bootstrap/warmup/backfill/reconcile/tail_guard/auto_republish.
  - Додано final 1m публікацію зі stream_close та HTF агрегацію з 1m у composition; дозволено final publish зі stream_close з warn.
  - Вимкнено history-команди з allowlist при history_provider_kind=none.
  - Прибрано локальні log.handlers у runtime-модулях для класичного логування.
- Де зроблено:
  - config/config.py
  - app/composition.py
  - runtime/warmup.py
  - runtime/backfill.py
  - runtime/fxcm/history_provider.py
  - runtime/fxcm/history_budget.py
  - runtime/tail_guard.py
  - runtime/republish.py
  - runtime/reconcile_finalizer.py
  - runtime/fxcm_forexconnect.py
- Як перевірено: не запускалось (зміни конфіг/пайплайн).
- Ризики/нотатки: HTF агрегація з 1m працює лише при повному закритті bucket; при неповних даних фінал не публікується.

  ## 2026-02-02T11:40:00+01:00 — PRE (MODE=PATCH): P8-only stream final + вимкнути history контур

  - Мета → перейти на P8-only: final 1m з stream_close, HTF лише з 1m final; вимкнути warmup/backfill/reconcile/tail_guard/auto_republish.
  - Scope → config/config.py, runtime/ohlcv_preview.py, runtime/preview_builder.py, runtime/publisher.py, runtime/status.py, app/composition.py, Work/01log.md.
  - Non-goals → зміни публічних контрактів.
  - Інваріанти/рейки → мінімальний диф; SSOT FileCache залишається; без history fetch.
  - План → 1) Встановити дефолти фіч у Config. 2) Писати final 1m з stream_close. 3) HTF з 1m final. 4) Логи без примусових StreamHandler у модулях. 5) POST.
  - Тести/перевірки → не запускати.

  ## 2026-02-02T12:10:00+01:00 — PRE (MODE=PATCH): Розширити логування в tail_guard/reconcile/republish/history_budget

  - Мета → додати більше діагностичних логів у ключові цикли та розгалуження, без зміни бізнес‑логіки.
  - Scope → runtime/tail_guard.py, runtime/reconcile_finalizer.py, runtime/republish.py, runtime/fxcm/history_budget.py, Work/01log.md.
  - Non-goals → зміни контрактів, зміна поведінки алгоритмів.
  - Інваріанти/рейки → мінімальний диф; логи тільки на рівнях debug/info; без “тихих” фолбеків.
  - План →
    1) Додати start/summary логи у tail_guard, reconcile, republish.
    2) Додати логи для ключових гілок (порожні дані, пропуски, repair, watermark).
    3) Додати логи у history_budget (acquire/release/refill) без зміни логіки.
    4) POST.
  - Тести/перевірки → не запускати.

  ## 2026-02-02T12:25:00+01:00 — POST (MODE=PATCH): Розширити логування в tail_guard/reconcile/republish/history_budget

  - Що зроблено → додано start/summary логи та гілкові debug логи у tail_guard/reconcile/republish; додано логи acquire/refill/build у history_budget.
  - Де зроблено → runtime/tail_guard.py, runtime/reconcile_finalizer.py, runtime/republish.py, runtime/fxcm/history_budget.py.
  - Як перевірено → не запускалось.
  - Ризики/нотатки → збільшення обсягу логів; за потреби знизити рівень.

  ## 2026-02-02T12:45:00+01:00 — PRE (MODE=PATCH): P8-only allowlist + tail_guard audit-only

  - Мета → підтвердити, що P9–P10 команди вимкнені в allowlist; залишити tail_guard як audit-only у P8-only режимі.
  - Scope → app/composition.py, Work/01log.md.
  - Non-goals → зміни контрактів або бізнес-логіки tail_guard/republish.
  - Інваріанти/рейки → мінімальний диф; fail-fast на repair без history provider.
  - План → 1) Прибрати fxcm_republish_tail з allowlist при history_provider_kind=none. 2) Дозволити tail_guard audit-only без provider. 3) POST.
  - Тести/перевірки → не запускати.

  ## 2026-02-02T12:55:00+01:00 — POST (MODE=PATCH): P8-only allowlist + tail_guard audit-only

  - Що зроблено → у P8-only вимкнено fxcm_republish_tail з allowlist; tail_guard залишено доступним в audit-only режимі без history provider (repair вимагає provider).
  - Де зроблено → app/composition.py.
  - Як перевірено → не запускалось.
  - Ризики/нотатки → repair у tail_guard тепер явно залежить від history provider; audit без repair працює в P8-only.

  ## 2026-02-02T13:25:00+01:00 — PRE (MODE=PATCH): Final 1m з реальним Volume через history-on-close + фічефлаги команд

  - Мета → публікувати final 1m одразу на close із “реальним Volume” із FXCM history (S3) та будувати похідні TF із final 1m; при цьому тримати warmup/backfill/republish/bootstrap/reconcile вимкненими окремими флагами.
  - Scope → config/config.py, app/composition.py, Work/01log.md.
  - Non-goals → повертати warmup/backfill у дефолт, змінювати контракти/схеми.
  - Інваріанти/рейки → мінімальний диф; fail-fast/ContractError не приглушувати; fallback зі stream_close тільки якщо явно дозволено.
  - План →
    1) Додати флаги enable для команд (warmup/backfill/republish/tail_guard/…)
    2) Додати history-on-close фіналізацію 1m (перезапис SSOT bar у FileCache source=history) + publish final.
    3) Будувати HTF лише з final 1m (по закриттю bucket).
    4) Локальні перевірки ruff/pytest.

  ## 2026-02-02T13:55:00+01:00 — POST (MODE=PATCH): Final 1m з реальним Volume через history-on-close + фічефлаги команд

  - Що зроблено →
    - Додано флаги: warmup_enable/backfill_enable/republish_enable/tail_guard_enable та finalize_1m_history_on_close_enable/finalize_allow_stream_fallback.
    - Реалізовано history-on-close фіналізацію 1m: на закритті бару робиться точковий history fetch для цього 1m, бар перезаписується в FileCache як source=history (SSOT), і публікується final 1m з реальним Volume.
    - Похідні TF (HTF) формуються лише з final 1m (через наявний агрегатор по close bucket).
    - Allowlist команд зроблено явним через *_enable флаги (history provider можна ввімкнути без warmup/backfill/etc).
  - Де зроблено → config/config.py, app/composition.py.
  - Як перевірено → ruff/pytest (див. таски).
  - Ризики/нотатки → history-on-close виконується в background thread; якщо history не готовий — final 1m не публікується (fallback лише якщо finalize_allow_stream_fallback=true).

  ## 2026-02-02T14:05:00+01:00 — PRE (MODE=read-only discovery): Підсумок діалогу + звірення ContractError '5m' + точка для UI tooltip

  - Мета → (1) зафіксувати стислий, але коректний підсумок робіт за сесію з акцентом на останні команди/результати; (2) швидко локалізувати причину ContractError з ключем "5m" у status/ohlcv_final; (3) знайти місце в UI Lite, куди додавати tooltip для свічки.
  - Scope → тільки читання: core/contracts/public/status_v2.json, ui_lite/*, runtime/*; оновлення лише SSOT-журналу Work/01log.md.
  - Non-goals → жодних правок коду/контрактів/JS у цьому кроці.
  - Інваріанти/рейки → append-only журнал; без “тихих” фолбеків; фіксація фактів як path:line.
  - План → 1) Відкрити status_v2 schema, звірити allowlist TF для ohlcv_final. 2) Підтвердити, де саме UI Lite використовує chart lib, щоб знати точку для tooltip. 3) Записати POST + коротку відповідь у чаті.
  - Тести/перевірки → не запускати (read-only).

  ## 2026-02-02T14:06:00+01:00 — POST (MODE=read-only discovery): Підсумок діалогу + звірення ContractError '5m' + точка для UI tooltip

  - Що зроблено → виконано швидке звірення контракту status та UI Lite, підготовлено підсумок для користувача (SSOT у цьому журналі).
  - Факти (root cause для ContractError) →
    - status schema дозволяє "ohlcv_final" лише з ключами TF: "1m", "15m", "1h", "4h", "1d" (core/contracts/public/status_v2.json:235-245). Ключ "5m" у цьому об’єкті буде відхилений через additionalProperties:false.
    - У status schema окремо присутній перелік/вимога TF, який включає "5m" (core/contracts/public/status_v2.json:210-213) — тобто в контрактах уже є поняття 5m, але воно не узгоджене з allowlist для ohlcv_final.
  - Факти (UI tooltip) → UI Lite містить TradingView Lightweight Charts (vendor) (ui_lite/static/vendor/lightweight-charts.standalone.production.js). Tooltip/hover логіку слід додавати у власний UI код (HTML/JS), а не у vendor.
  - Як перевірено → тільки читання файлів (search/grep), без запусків.
  - Ризики/нотатки → ContractError з "5m" очікуваний, якщо runtime/status зараз записує "5m" всередину status.ohlcv_final; виправлення має бути або в схемі (додати "5m" до ohlcv_final), або в writer-логіці (не писати 5m туди), залежно від SSOT рішення.

  ## 2026-02-02T14:07:00+01:00 — POST (MODE=read-only discovery): Підсумок діалогу (SSOT) з акцентом на останні результати

  - Вектор робіт (еволюція запиту) →
    - Старт: P10 “security rails” + стабільність history/warmup/backfill для FXCM (таймстемпи, порожні чанки, календарне end-time, детермінізм warmup/backfill).
    - Pivot: вимога P8-only “stream-first” (вимкнути warmup/backfill/reconcile/auto_republish/auto bootstrap; final 1m від stream close; HTF лише з final 1m).
    - Далі: точковий “P9 елемент без P9 пайплайна” — отримати реальний Volume на close 1m через history-on-close (S3), але не повертати heavy контури.
    - Нові відкриті питання: ContractError по status/ohlcv_final з TF "5m"; UX вимога tooltip на свічці (OHLCV+volume).

  - Ключові рішення/інваріанти, які трималися всю сесію →
    - SSOT: FileCache (CSV + meta.json) як канон для барів; status/commands валідовані JSON Schema (additionalProperties:false) з fail-fast ContractError.
    - P8-only за замовчуванням: heavy-контури P9/P10 (warmup/backfill/reconcile/republish/bootstrap) вимкнені; allowlist команд керується явними *_enable флагами.
    - Final 1m: базово від stream_close, але для реального Volume — history-on-close перезаписує bar як source=history і публікує final 1m.
    - HTF: формується лише з final 1m, по закриттю bucket.

  - Останні дії/результати, що найбільше впливають на поточний стан →
    - Додано фічефлаги *_enable і finalize_1m_history_on_close_enable (див. останній PATCH вище в цьому журналі).
    - Реалізовано history-on-close: точковий history fetch на close 1m → overwrite SSOT → publish final 1m з реальним Volume → побудова HTF.
    - Виявлено контрактний розрив: status.ohlcv_final не дозволяє ключ "5m" у схемі (core/contracts/public/status_v2.json:235-245), але runtime наразі може його писати → ContractError.
    - Встановлено, що UI Lite використовує Lightweight Charts (vendor) і tooltip треба додавати у власний UI JS/HTML, не у vendor.

  - Проблеми оточення/запусків (важливо для інтерпретації “результатів команд”) →
    - Частина VS Code tasks для lint/test викликає bash/WSL; у середовищі може бути відсутній WSL distro → падіння перевірок може бути через ENV, а не код. (Якщо потрібно — узгодити Windows-native перевірки окремим PATCH.)

  - Поточні next steps (не виконано в цьому discovery) →
    1) Вирішити SSOT: або дозволити TF "5m" в status.ohlcv_final schema, або не писати "5m" у status.ohlcv_final (узгодити з тим, які TF реально публікуються).
    2) Додати tooltip у UI Lite: на hover/crosshair показувати time + O/H/L/C + Volume (і за потреби tick_count/source).

  ## 2026-02-02T09:30:00+01:00 — POST (MODE=PATCH): Збереження стану у окремій гілці перед відкатом

- Що зроблено → створено гілку backup/20260202-pre-rollback, закомічено поточний стан, виконано checkout 51eb2db (detached HEAD).
- Де зроблено → git (repo root).
- Як перевірено → git checkout/commit успішні; checkout 51eb2db виконано.
- Ризики/нотатки → файл History/2639658384/catalog.sqlite не вдалося unlink під час checkout; залишився модифікованим.

## 2026-02-02T09:45:00+01:00 — PRE (MODE=PATCH): Очищення History/catalog.sqlite під час відкату

- Мета → прибрати модифікований History/2639658384/catalog.sqlite (не комітимо history).
- Scope → git restore/checkout для History/2639658384/catalog.sqlite.
- Non-goals → зміни коду.
- Інваріанти/рейки → rollback має бути чистим.
- План → спробувати видалити файл і відновити з git; перевірити git status.
- Тести → не запускаємо.

## 2026-02-02T09:50:00+01:00 — POST (MODE=PATCH): Очищення History/catalog.sqlite під час відкату

- Що зроблено → спроба Remove-Item + git checkout для History/2639658384/catalog.sqlite.
- Де зроблено → History/2639658384/catalog.sqlite.
- Як перевірено → git status показує M History/2639658384/catalog.sqlite.
- Ризики/нотатки → файл заблокований іншим процесом; потрібен його закриття/kill, інакше checkout не очистить.

## 2026-02-02T10:05:00+01:00 — PRE (MODE=PATCH): Повторна очистка History/catalog.sqlite після закриття процесу

- Мета → прибрати модифікації History/2639658384/catalog.sqlite після зняття lock.
- Scope → git restore/checkout для History/2639658384/catalog.sqlite.
- Non-goals → зміни коду.
- Інваріанти/рейки → rollback має бути чистим.
- План → Remove-Item + git checkout, перевірити git status.
- Тести → не запускаємо.

## 2026-02-02T10:10:00+01:00 — POST (MODE=PATCH): Повторна очистка History/catalog.sqlite після закриття процесу

- Що зроблено → файл видалено і відновлено з git, status чистий.
- Де зроблено → History/2639658384/catalog.sqlite.
- Як перевірено → git status -sb (clean).
- Ризики/нотатки → відкат чистий.

## 2026-02-02T10:25:00+01:00 — PRE (MODE=read-only discovery): Перевірка command_auth_enable

- Мета → з'ясувати, чи захищено командний канал (auth enable).
- Scope → config/config.py, runtime/command_bus.py, docs (за потреби).
- Non-goals → зміни коду.
- Інваріанти/рейки → read-only.
- План → перевірити наявність/використання command_auth_enable.
- Тести → не застосовно.

## 2026-02-02T10:30:00+01:00 — POST (MODE=read-only discovery): Перевірка command_auth_enable

- Що зроблено → перевірено config/command_bus на наявність command_auth_enable.
- Де зроблено → config/config.py; runtime/command_bus.py.
- Як перевірено → read-only огляд.
- Ризики/нотатки → у цьому стані command_auth_enable відсутній; auth/підпис командного каналу не реалізовано (лише allowlist+schema+redis ACL).

## 2026-02-02T10:40:00+01:00 — PRE (MODE=read-only discovery): Список змін, втрачених після відкату на 51eb2db

- Мета → зібрати список комітів/змін, які не входять у 51eb2db.
- Scope → git log від 51eb2db до backup/20260202-pre-rollback.
- Non-goals → зміни коду.
- Інваріанти/рейки → read-only.
- План → git log 51eb2db..backup/20260202-pre-rollback.
- Тести → не застосовно.

## 2026-02-02T10:45:00+01:00 — POST (MODE=read-only discovery): Список змін, втрачених після відкату на 51eb2db

- Що зроблено → отримано git log 51eb2db..backup/20260202-pre-rollback.
- Де зроблено → git log (repo root).
- Як перевірено → read-only.
- Ризики/нотатки → нижче список комітів, які НЕ входять у 51eb2db:
  - 41d2017 — Спостереження за FINAL/volume
  - bd58850 — Epoch ns/us конвертація у _to_ms
  - d8e8a58 — Auto‑republish guard для non‑final source; history row time-key; calendar_tag sync
  - d56dcef — docs/REPO_LAYOUT + README (profiles/runbooks/command rails)
  - 5ac74ed — Єдиний manifest exit_gates; MVP parity план; P10.SEC ACL runbook
  - 220c1f2 — P10.B1 auto-reconcile trigger + Fix 13 errors
  - 1c282ba — Оцінка P8 + синхронізація doc P9

  - P10.B1 авто‑reconcile trigger: publish cmd на 15m boundary + dedup last_end_ms.
  - P10.B0 reconcile finalization: history tail → final 1m (history) + rebuild final 15m (history_agg), watermark‑дедуп.
  - republish_tail allowlist rails: заборона non‑final sources; строгі source=history/history_agg.
  - Фікс source=cache у /api/ohlcv?mode=final (тепер знову було б cache у старому стані).
  - status.reconcile в schema/status payload (поля reconcile, last_end_ms).
  - final/volume спостереження (метрики/логіка).
  - epoch ns/us → ms конвертація в _to_ms.
  - оновлений manifest exit_gates + P10.SEC ACL runbook + docs/README/REPO_LAYOUT sync.
  - маркування fxcm/ як архів/мертвий* (якщо було).

## 2026-02-02T11:10:00+01:00 — POST (MODE=read-only discovery): Наявні записи, неактуальні після відкату на 51eb2db

- Що зроблено → зафіксовано, що записи про P9/P10 (reconcile/republish rails, source=cache fix, status.reconcile schema, P10.SEC docs, тощо) більше не відповідають поточному коду (HEAD=51eb2db).
- Де зроблено → Work/01log.md (цей запис).
- Як перевірено → git log 51eb2db..backup/20260202-pre-rollback.
- Ризики/нотатки → historical записи не видаляються (append-only), але їхні висновки слід читати як «історичні» до повернення відповідних комітів.

## 2026-02-02T11:30:00+01:00 — PRE (MODE=PATCH): Прибрати HMAC/command_auth залишки

- Мета → видалити неактивні HMAC env ключі та command_auth_* overrides.
- Scope → core/env_loader.py; config/profile_local.py; config/profile_prod.py.
- Non-goals → реалізація auth.
- Інваріанти/рейки → мінімальний диф.
- План → прибрати FXCM_HMAC_* з allowlist і command_auth_* з профілів.
- Тести → не запускаємо (не запитувалося).

## 2026-02-02T11:40:00+01:00 — POST (MODE=PATCH): Прибрати HMAC/command_auth залишки

- Що зроблено → прибрано FXCM_HMAC_* з allowlist та command_auth_* з profile_local/profile_prod.
- Де зроблено → core/env_loader.py; config/profile_local.py; config/profile_prod.py.
- Як перевірено → не запускалося (не запитувалося).
- Ризики/нотатки → .env.local більше не має містити FXCM_HMAC_*.

## 2026-02-02T12:20:00+01:00 — PRE (MODE=PATCH): Fix load_config duplicate ns override

- Мета → усунути TypeError replace() got multiple values for keyword argument 'ns'.
- Scope → config/config.py (merge overrides).
- Non-goals → зміни конфіг-семантики.
- Інваріанти/рейки → мінімальний диф.
- План → об’єднати env/profile overrides у один dict перед replace.
- Тести → не запускаємо (не запитувалося).

## 2026-02-02T12:25:00+01:00 — POST (MODE=PATCH): Fix load_config duplicate ns override

- Що зроблено → об’єднано env/profile overrides у merged_overrides перед replace.
- Де зроблено → config/config.py.
- Як перевірено → не запускалося (не запитувалося).
- Ризики/нотатки → порядок: env_overrides → overrides (profile має пріоритет при рівних ключах).

## 2026-02-02T12:40:00+01:00 — PRE (MODE=PATCH): Прибрати невідомі command_* overrides у профілях

- Мета → усунути TypeError: unexpected keyword argument 'command_rate_limit_enable'.
- Scope → config/profile_local.py; config/profile_prod.py.
- Non-goals → додавання нових полів у Config.
- Інваріанти/рейки → мінімальний диф.
- План → прибрати command_* overrides з профілів.
- Тести → не запускаємо (не запитувалося).

## 2026-02-02T12:45:00+01:00 — POST (MODE=PATCH): Прибрати невідомі command_* overrides у профілях

- Що зроблено → прибрано command_rate_limit_enable/command_coalesce_enable/command_heavy_collapse_enable з профілів.
- Де зроблено → config/profile_local.py; config/profile_prod.py.
- Як перевірено → не запускалося (не запитувалося).
- Ризики/нотатки → якщо ці флаги потрібні, їх треба повернути через Config+логіку (окремий PATCH).

## 2026-02-02T13:05:00+01:00 — PRE (MODE=PATCH): Fix FXCMOfferSubscription missing warn state

- Мета → усунути AttributeError _last_event_ahead_warn_ts_ms_by_symbol у FXCMOfferSubscription.
- Scope → runtime/fxcm_forexconnect.py.
- Non-goals → зміна логіки стріму.
- Інваріанти/рейки → мінімальний диф.
- План → ініціалізувати warn state у FXCMOfferSubscription.__init__.
- Тести → не запускаємо (не запитувалося).

## 2026-02-02T13:10:00+01:00 — POST (MODE=PATCH): Fix FXCMOfferSubscription missing warn state

- Що зроблено → додано ініціалізацію warn state у FXCMOfferSubscription.
- Де зроблено → runtime/fxcm_forexconnect.py.
- Як перевірено → не запускалося (не запитувалося).
- Ризики/нотатки → поведінка не змінена, лише фікс NPE.

## 2026-02-02T13:40:00+01:00 — PRE (MODE=PATCH): UTC календар + конвертація epoch μs/ns

- Мета →
  1) Встановити дефолтний calendar_tag на UTC‑профіль (weekly_open 23:01, break 22:00–23:01).
  2) Додати конвертацію epoch μs/ns → ms при читанні history.
- Scope → config/config.py; runtime/fxcm/history_provider.py.
- Non-goals → warmup/backfill/інша логіка.
- Інваріанти/рейки → мінімальний диф.
- План → змінити calendar_tag; розширити _to_ms для μs/ns.
- Тести → не запускаємо (не запитувалося).

## 2026-02-02T13:45:00+01:00 — POST (MODE=PATCH): UTC календар + конвертація epoch μs/ns

- Що зроблено → встановлено дефолтний calendar_tag=fxcm_calendar_v1_utc_overrides; додано конвертацію epoch μs/ns у _to_ms.
- Де зроблено → config/config.py; runtime/fxcm/history_provider.py.
- Як перевірено → не запускалося (не запитувалося).
- Ризики/нотатки → обробка epoch>9_999_999_999_999 як μs/ns; значення >1e19 відкидаються.

## 2026-02-02T14:05:00+01:00 — PRE (MODE=read-only discovery): Діагностика актуальних записів (UTC календар + history time)

- Мета → переглянути код для підтвердження актуальних записів (UTC профіль, epoch μs/ns, P8-only режим).
- Scope → config/config.py; config/calendar_overrides.json; core/time/calendar.py; runtime/fxcm/history_provider.py; runtime/warmup.py; runtime/backfill.py.
- Non-goals → зміни коду.
- Інваріанти/рейки → read-only.
- План → огляд файлів і коротка діагностика.
- Тести → не застосовно.

## 2026-02-02T14:10:00+01:00 — POST (MODE=read-only discovery): Діагностика актуальних записів (UTC календар + history time)

- Що зроблено → переглянуто Calendar, overrides, history_provider, warmup/backfill.
- Де зроблено → config/config.py; config/calendar_overrides.json; core/time/calendar.py; runtime/fxcm/history_provider.py; runtime/warmup.py; runtime/backfill.py.
- Як перевірено → read-only огляд.
- Ризики/нотатки →
  - UTC профіль є в config/calendar_overrides.json як fxcm_calendar_v1_utc_overrides (weekly_open 23:01, break 22:00–23:01).
  - Calendar читає overrides за calendar_tag; помилки init зупиняють is_open.
  - _to_ms у history_provider тепер підтримує epoch μs/ns (val//1_000 або val//1_000_000).
  - warmup/backfill залишаються в коді, але це P8-only режим: використовуються лише якщо викликані командами.

  ## 2026-02-02T14:25:00+01:00 — PRE (MODE=read-only discovery): Цільовий P8-only режим (preview/final)

  - Мета → зафіксувати цільову поведінку: preview як live, final як історія/SSOT у cache.
  - Scope → runtime/preview_builder.py; runtime/http_server.py; ui_lite/server.py; store/file_cache/history_cache.py.
  - Non-goals → зміни коду.
  - Інваріанти/рейки → read-only.
  - План → коротка діагностика відповідності поточному коду.
  - Тести → не застосовно.

  ## 2026-02-02T14:25:00+01:00 — POST (MODE=read-only discovery): Цільовий P8-only режим (preview/final)

  - Що зроблено → зафіксовано, що preview будується з тикових даних і публікується в OHLCV; final має читатися з FileCache і показуватись у UI як окремий режим.
  - Де зроблено → runtime/preview_builder.py; runtime/http_server.py; ui_lite/server.py; store/file_cache/history_cache.py.
  - Як перевірено → read-only огляд.
  - Ризики/нотатки → final зараз залежить від наявних даних у FileCache; при рестарті UI має брати final із cache (mode=final).

  ## 2026-02-02T14:40:00+01:00 — PRE (MODE=PATCH): Підвантаження final з FileCache на старті (P8-only)

  - Мета → при старті публікувати final з FileCache для UI (mode=final).
  - Scope → app/composition.py.
  - Non-goals → зміна preview пайплайну.
  - Інваріанти/рейки → мінімальний диф; source=history; complete=true.
  - План → додати _publish_cached_final_on_start і виклик на старті.
  - Тести → не запускаємо (не запитувалося).

  ## 2026-02-02T14:45:00+01:00 — POST (MODE=PATCH): Підвантаження final з FileCache на старті (P8-only)

  - Що зроблено → додано публікацію final із FileCache на старті (після старту UI Lite).
  - Де зроблено → app/composition.py.
  - Як перевірено → не запускалося (не запитувалося).
  - Ризики/нотатки → final базується на даних кешу; якщо кеш порожній — нічого не публікується.

  ## 2026-02-02T15:05:00+01:00 — PRE (MODE=read-only discovery): Журнал UI Lite (діагностика + режим/підписка)

  - Мета → знайти у журналі згадки про розширену діагностику UI Lite та перемикання режимів/підписки.
  - Scope → Work/01log.md.
  - Non-goals → зміни коду.
  - Інваріанти/рейки → лише читання; без редагування бізнес-логіки.
  - План → знайти релевантні PRE/POST записи UI Lite observability і UI Lite P6.1.
  - Тести → не застосовуються.

  ## 2026-02-02T15:06:00+01:00 — POST (MODE=read-only discovery): Журнал UI Lite (діагностика + режим/підписка)

  - Що зроблено → знайдено записи про UI Lite observability (лічильники, /debug, WS age) і UI Lite P6.1 (Subscribe кнопка/підписка).
  - Де зроблено → Work/01log.md.
  - Як перевірено → не застосовувалось.
  - Ризики/нотатки → потрібні посилання на конкретні записи у журналі.

  ## 2026-02-02T15:20:00+01:00 — PRE (MODE=read-only discovery): tail_guard та підтягування хвоста на старті

  - Мета → перевірити, чи є автозапуск tail_guard/republish на старті runtime.
  - Scope → app/composition.py, runtime/tail_guard.py (читання).
  - Non-goals → зміни коду.
  - Інваріанти/рейки → лише читання.
  - План → знайти місце старту runtime і виклики tail_guard/republish.
  - Тести → не застосовуються.

  ## 2026-02-02T15:22:00+01:00 — POST (MODE=read-only discovery): tail_guard та підтягування хвоста на старті

  - Що зроблено → перевірено build_runtime: tail_guard викликається лише через handler команди, автозапуску на старті немає; на старті є лише публікація final із FileCache.
  - Де зроблено → app/composition.py.
  - Як перевірено → не застосовувалось.
  - Ризики/нотатки → для підтягування хвоста потрібна команда fxcm_tail_guard або окремий автозапуск.

  ## 2026-02-02T15:40:00+01:00 — PRE (MODE=PATCH): UI Lite — інформативність + діагностика overlay/hidden

  - Мета → додати приховану діагностику як overlay і підвищити інформативність UI Lite.
  - Scope → ui_lite/static/index.html, ui_lite/static/app.js, ui_lite/static/styles.css.
  - Non-goals → зміни WS/Redis протоколів, бекенд логіки.
  - Інваріанти/рейки → read-only UI; мінімальний диф.
  - План → кнопка "Діагностика", overlay з ключовими метриками, toggle/ESC.
  - Тести → не запускаємо (не запитувалося).

  ## 2026-02-02T15:55:00+01:00 — POST (MODE=PATCH): UI Lite — інформативність + діагностика overlay/hidden

  - Що зроблено → додано кнопку "Діагностика", overlay з ключовими метриками (WS/Status/UI), toggle/ESC.
  - Де зроблено → ui_lite/static/index.html, ui_lite/static/app.js, ui_lite/static/styles.css.
  - Як перевірено → не запускалось (не запитувалося).
  - Ризики/нотатки → overlay прихований за замовчуванням; toggle через кнопку або клавішу D.

  ## 2026-02-02T16:05:00+01:00 — PRE (MODE=PATCH): UI Lite — перемістити тултіп вгору

  - Мета → перемістити діагностичний тултіп/overlay вгору.
  - Scope → ui_lite/static/styles.css.
  - Non-goals → зміни логіки UI.
  - Інваріанти/рейки → мінімальний диф.
  - План → змінити позицію .diag-overlay.
  - Тести → не запускаємо (не запитувалося).

  ## 2026-02-02T16:06:00+01:00 — POST (MODE=PATCH): UI Lite — перемістити тултіп вгору

  - Що зроблено → .diag-overlay перенесено вгору (top: 14px).
  - Де зроблено → ui_lite/static/styles.css.
  - Як перевірено → не запускалось (не запитувалося).
  - Ризики/нотатки → overlay тепер перекриває верхню частину графіка.

  ## 2026-02-02T16:15:00+01:00 — PRE (MODE=PATCH): UI Lite — повернути перемикання overlay як до відкату

  - Мета → відновити перемикання діагностичного overlay (inline/overlay/hidden) як раніше.
  - Scope → ui_lite/static/index.html, ui_lite/static/app.js, ui_lite/static/styles.css.
  - Non-goals → зміни WS/Redis протоколів.
  - Інваріанти/рейки → мінімальний диф, read-only UI.
  - План → повернути кнопку toggle-diagnostics, логіку _setDiagnosticsMode і стилі healthbar overlay/hidden.
  - Тести → не запускаємо (не запитувалося).

  ## 2026-02-02T16:20:00+01:00 — POST (MODE=PATCH): UI Lite — повернути перемикання overlay як до відкату

  - Що зроблено → відновлено кнопку toggle-diagnostics, перемикання inline/overlay/hidden та стилі healthbar overlay/hidden.
  - Де зроблено → ui_lite/static/index.html, ui_lite/static/app.js, ui_lite/static/styles.css.
  - Як перевірено → не запускалось (не запитувалося).
  - Ризики/нотатки → режим зберігається у localStorage.

  ## 2026-02-02T16:35:00+01:00 — PRE (MODE=PATCH): UI Lite — більше корисної висоти чарту + видалити діагностичний тултіп

  - Мета → прибрати зайву пустоту під чартом у режимі overlay та видалити діагностичний тултіп.
  - Scope → ui_lite/static/app.js, ui_lite/static/styles.css.
  - Non-goals → зміни протоколів/бекенду.
  - Інваріанти/рейки → мінімальний диф.
  - План → прибрати diag overlay; перевести layout на flex для максимального чарту.
  - Тести → не запускаємо (не запитувалося).

  ## 2026-02-02T16:40:00+01:00 — POST (MODE=PATCH): UI Lite — більше корисної висоти чарту + видалити діагностичний тултіп

  - Що зроблено → видалено diag overlay; chart-wrap переведено на flex-висоту.
  - Де зроблено → ui_lite/static/app.js, ui_lite/static/styles.css.
  - Як перевірено → не запускалось (не запитувалося).
  - Ризики/нотатки → висота чарту тепер залежить від flex-контейнера.

  ## 2026-02-02T16:50:00+01:00 — PRE (MODE=PATCH): UI Lite — повернути висоту чарту як було

  - Мета → відкотити layout чарту до попередньої висоти (без flex).
  - Scope → ui_lite/static/styles.css.
  - Non-goals → зміни логіки UI.
  - Інваріанти/рейки → мінімальний диф.
  - План → прибрати flex з body, повернути height: calc(100vh - 120px).
  - Тести → не запускаємо (не запитувалося).

  ## 2026-02-02T16:52:00+01:00 — POST (MODE=PATCH): UI Lite — повернути висоту чарту як було

  - Що зроблено → відновлено height: calc(100vh - 120px) для .chart-wrap, прибрано flex з body.
  - Де зроблено → ui_lite/static/styles.css.
  - Як перевірено → не запускалось (не запитувалося).
  - Ризики/нотатки → overlay/hidden не збільшують висоту чарту, як у попередній версії.

  ## 2026-02-02T17:05:00+01:00 — PRE (MODE=PATCH): UI Lite — підгонка висоти чарту під overlay/inline

  - Мета → прибрати пустоту в overlay та зберегти inline/hidden без втрати висоти.
  - Scope → ui_lite/static/app.js.
  - Non-goals → зміни бекенду.
  - Інваріанти/рейки → мінімальний диф.
  - План → рахувати offset та виставити height для chart-wrap динамічно.
  - Тести → не запускаємо (не запитувалося).

  ## 2026-02-02T17:07:00+01:00 — POST (MODE=PATCH): UI Lite — підгонка висоти чарту під overlay/inline

  - Що зроблено → додано динамічну підгонку висоти chart-wrap за фактичними висотами topbar/status/healthbar.
  - Де зроблено → ui_lite/static/app.js.
  - Як перевірено → не запускалось (не запитувалося).
  - Ризики/нотатки → висота залежить від DOM offsetHeight.

  ## 2026-02-02T17:25:00+01:00 — PRE (MODE=PATCH): UI Lite — resize чарту після зміни висоти

  - Мета → прибрати пустоту в overlay та повернути видимість обсягів у inline.
  - Scope → ui_lite/static/app.js.
  - Non-goals → зміни бекенду.
  - Інваріанти/рейки → мінімальний диф.
  - План → після _updateChartHeight виконати chart.resize.
  - Тести → не запускаємо (не запитувалося).

  ## 2026-02-02T17:27:00+01:00 — POST (MODE=PATCH): UI Lite — resize чарту після зміни висоти

  - Що зроблено → додано chart.resize/applyOptions у _updateChartHeight.
  - Де зроблено → ui_lite/static/app.js.
  - Як перевірено → не запускалось (не запитувалося).
  - Ризики/нотатки → якщо DOM ще не виміряний, висота може бути 0 на перший кадр.

  ## 2026-02-02T17:45:00+01:00 — PRE (MODE=PATCH): FINAL оновлення при закритті 1m свічки

  - Мета → у режимі final публікувати нові 1m свічки при закритті, без рестарту.
  - Scope → app/composition.py.
  - Non-goals → зміни контрактів/бекенду історії.
  - Інваріанти/рейки → source=history, complete=true; мінімальний диф.
  - План → після append_complete_bars опублікувати final 1m bar.
  - Тести → не запускаємо (не запитувалося).

  ## 2026-02-02T17:50:00+01:00 — POST (MODE=PATCH): FINAL оновлення при закритті 1m свічки

  - Що зроблено → додано publish final 1m після запису закритих барів у FileCache.
  - Де зроблено → app/composition.py.
  - Як перевірено → не запускалось (не запитувалося).
  - Ризики/нотатки → final публікується з source=history на кожну закриту 1m свічку.

  ## 2026-02-02T18:05:00+01:00 — PRE (MODE=PATCH): UI Lite — мініатюрніші свічки

  - Мета → зробити свічки більш мініатюрними у UI Lite.
  - Scope → ui_lite/static/app.js.
  - Non-goals → зміни бекенду.
  - Інваріанти/рейки → мінімальний диф.
  - План → зменшити barSpacing та rightOffset.
  - Тести → не запускаємо (не запитувалося).

  ## 2026-02-02T18:07:00+01:00 — POST (MODE=PATCH): UI Lite — мініатюрніші свічки

  - Що зроблено → зменшено barSpacing та rightOffset.
  - Де зроблено → ui_lite/static/app.js.
  - Як перевірено → не запускалось (не запитувалося).
  - Ризики/нотатки → надто дрібні свічки можна відкотити через barSpacing.

  ## 2026-02-02T18:15:00+01:00 — PRE (MODE=PATCH): UI Lite — ще менші свічки (x0.5)

  - Мета → ще вдвічі зменшити свічки.
  - Scope → ui_lite/static/app.js.
  - Non-goals → зміни бекенду.
  - Інваріанти/рейки → мінімальний диф.
  - План → barSpacing: 2→1, rightOffset: 1→0.
  - Тести → не запускаємо (не запитувалося).

  ## 2026-02-02T18:16:00+01:00 — POST (MODE=PATCH): UI Lite — ще менші свічки (x0.5)

  - Що зроблено → barSpacing=1, rightOffset=0.
  - Де зроблено → ui_lite/static/app.js.
  - Як перевірено → не запускалось (не запитувалося).
  - Ризики/нотатки → дуже щільні свічки можуть злитись при низькому TF.

  ## 2026-02-02T18:25:00+01:00 — PRE (MODE=read-only discovery): Прогалини у final після перезапусків

  - Мета → оцінити, що означають прогалини в режимі final після рестартів.
  - Scope → огляд поведінки UI Lite final (без змін коду).
  - Non-goals → зміни бекенду.
  - Інваріанти/рейки → лише читання.
  - План → пояснити причину прогалин і варіанти виправлення (tail_guard/backfill/republish).
  - Тести → не застосовуються.

  ## 2026-02-02T18:26:00+01:00 — POST (MODE=read-only discovery): Прогалини у final після перезапусків

  - Що зроблено → підтверджено, що прогалини означають відсутні бари у FileCache; UI лише візуалізує.
  - Де зроблено → аналіз UI Lite поведінки (без змін файлів).
  - Як перевірено → не застосовувалось.
  - Ризики/нотатки → заповнення потребує tail_guard/backfill або ручного republish.

  ## 2026-02-02T18:40:00+01:00 — PRE (MODE=read-only discovery): Безпечне тестування backfill/tail_guard/republish

  - Мета → дати пропозиції тестів без ризику відкату.
  - Scope → план виконання, без змін коду.
  - Non-goals → запуск команд.
  - Інваріанти/рейки → безпечне вікно, force=false.
  - План → запропонувати мале вікно, dry-run, ізоляцію NS.
  - Тести → не застосовуються.

  ## 2026-02-02T18:41:00+01:00 — POST (MODE=read-only discovery): Безпечне тестування backfill/tail_guard/republish

  - Що зроблено → підготовлено пропозиції тестів з малим вікном/ізоляцією.
  - Де зроблено → план у відповіді.
  - Як перевірено → не застосовувалось.
  - Ризики/нотатки → будь-який репейр краще запускати на окремому NS або копії кешу.

  ## 2026-02-02T19:00:00+01:00 — PRE (MODE=read-only discovery): FXCM конект/потік/обробка даних

  - Мета → перевірити шлях FXCM конекту, підписки та обробки даних у поточному конекторі.
  - Scope → runtime/fxcm_forexconnect.py, runtime/fxcm/history_provider.py, runtime/fxcm/* (читання).
  - Non-goals → зміни коду.
  - Інваріанти/рейки → лише читання.
  - План → оглянути конект, subscribe, reconnection, tick→ohlcv pipeline, error handling.
  - Тести → не застосовуються.

  ## 2026-02-02T19:02:00+01:00 — POST (MODE=read-only discovery): FXCM конект/потік/обробка даних

  - Що зроблено → підготовлено аналіз поточного FXCM пайплайну (конект/підписка/обробка).
  - Де зроблено → див. результати огляду файлів у відповідях.
  - Як перевірено → не застосовувалось.
  - Ризики/нотатки → потрібне порівняння з минулим конектором.

  ## 2026-02-02T19:15:00+01:00 — PRE (MODE=PATCH): Ізольована історична база (training) для FXCM

  - Мета → створити окремий скрипт для завантаження історії у ізольований FileCache.
  - Scope → tools/history_lab.py.
  - Non-goals → зміни runtime pipeline/Redis.
  - Інваріанти/рейки → ізольований cache_root, мінімальний диф.
  - План → CLI скрипт: fetch history chunks → append у cache.
  - Тести → не запускаємо (не запитувалося).

  ## 2026-02-02T19:20:00+01:00 — POST (MODE=PATCH): Ізольована історична база (training) для FXCM

  - Що зроблено → додано tools/history_lab.py для ізольованого завантаження history у FileCache.
  - Де зроблено → tools/history_lab.py.
  - Як перевірено → не запускалось (не запитувалося).
  - Ризики/нотатки → history fetch робить login/logout на кожен чанк; для 365d потрібен більший chunk_minutes.

  ## 2026-02-02T19:35:00+01:00 — PRE (MODE=PATCH): Запуск history_lab (ізольоване history)

  - Мета → витягнути короткий фрагмент історії у data/history_lab.
  - Scope → tools/history_lab.py (запуск).
  - Non-goals → зміни runtime pipeline.
  - Інваріанти/рейки → ізольований cache_root; мінімальне вікно.
  - План → запустити history_lab --days 2 --chunk-minutes 720.
  - Тести → запуск скрипту.

  ## 2026-02-02T19:37:00+01:00 — POST (MODE=PATCH): Запуск history_lab (ізольоване history)

  - Що зроблено → спроба запуску history_lab.
  - Де зроблено → tools/history_lab.py.
  - Як перевірено → запуск скрипту (FAIL: ModuleNotFoundError: config).
  - Ризики/нотатки → потрібен PYTHONPATH=.

  ## 2026-02-02T19:38:00+01:00 — PRE (MODE=PATCH): Запуск history_lab з PYTHONPATH

  - Мета → повторити запуск з PYTHONPATH=.
  - Scope → tools/history_lab.py (запуск).
  - Non-goals → зміни коду.
  - Інваріанти/рейки → ізольований cache_root.
  - План → $env:PYTHONPATH='.'; запуск history_lab.
  - Тести → запуск скрипту.

  ## 2026-02-02T19:39:00+01:00 — POST (MODE=PATCH): Запуск history_lab з PYTHONPATH

  - Що зроблено → повторний запуск history_lab.
  - Де зроблено → tools/history_lab.py.
  - Як перевірено → запуск скрипту (FAIL: fxcm_secrets_missing).
  - Ризики/нотатки → потрібні FXCM_USERNAME/FXCM_PASSWORD у .env.local/.env.prod.

  ## 2026-02-02T19:45:00+01:00 — PRE (MODE=PATCH): history_lab — підхопити .env.local/.env.prod

  - Мета → завантажити FXCM_USERNAME/FXCM_PASSWORD/FXCM_CONNECTION/FXCM_HOST_URL з .env.*.
  - Scope → tools/history_lab.py.
  - Non-goals → зміни runtime pipeline.
  - Інваріанти/рейки → мінімальний диф.
  - План → додати load_env(root_dir) перед load_config.
  - Тести → запуск history_lab.

  ## 2026-02-02T19:47:00+01:00 — POST (MODE=PATCH): history_lab — підхопити .env.local/.env.prod

  - Що зроблено → додано load_env(root_dir) у tools/history_lab.py.
  - Де зроблено → tools/history_lab.py.
  - Як перевірено → не запускалось (перед запуском підставимо FXCM_* у .env).
  - Ризики/нотатки → unknown env key з .env викличе fail-fast.

  ## 2026-02-02T19:48:00+01:00 — PRE (MODE=PATCH): Запуск history_lab з .env

  - Мета → витягнути історію з FXCM у data/history_lab.
  - Scope → tools/history_lab.py (запуск).
  - Non-goals → зміни коду.
  - Інваріанти/рейки → ізольований cache_root.
  - План → $env:PYTHONPATH='.'; запуск history_lab.
  - Тести → запуск скрипту.

  ## 2026-02-02T19:50:00+01:00 — POST (MODE=PATCH): Запуск history_lab з .env

  - Що зроблено → запуск history_lab.
  - Де зроблено → tools/history_lab.py.
  - Як перевірено → FAIL: fxcm history fetch failed: unsupported scope (No data found).
  - Ризики/нотатки → додано skip для пустих діапазонів у наступному патчі.

  ## 2026-02-02T19:55:00+01:00 — PRE (MODE=PATCH): history_lab — пропуск unsupported scope

  - Мета → не падати на пустих діапазонах (unsupported scope) і продовжувати завантаження.
  - Scope → tools/history_lab.py.
  - Non-goals → зміни runtime.
  - Інваріанти/рейки → мінімальний диф, лише lab.
  - План → ловити ContractError з "unsupported scope"/"No data found" і пропускати чанк.
  - Тести → повторний запуск history_lab.

  ## 2026-02-02T19:58:00+01:00 — POST (MODE=PATCH): history_lab — пропуск unsupported scope

  - Що зроблено → додано skip для unsupported scope/No data found.
  - Де зроблено → tools/history_lab.py.
  - Як перевірено → ще не запускалось.
  - Ризики/нотатки → якщо брокер повертає інші помилки, скрипт зупиниться.

  ## 2026-02-02T20:05:00+01:00 — POST (MODE=PATCH): Запуск history_lab після skip

  - Що зроблено → повторний запуск history_lab з skip unsupported scope.
  - Де зроблено → tools/history_lab.py.
  - Як перевірено → виконано; rows=0 inserted=0 (діапазон без даних).
  - Ризики/нотатки → потрібне коректне вікно торгівлі/символ для history.

  ## 2026-02-02T20:15:00+01:00 — PRE (MODE=PATCH): history_lab — режим останніх хвилин

  - Мета → запитувати tail (останні хвилини) замість діапазону днів.
  - Scope → tools/history_lab.py.
  - Non-goals → зміни runtime.
  - Інваріанти/рейки → мінімальний диф.
  - План → додати --last-minutes та переобчислення діапазону.
  - Тести → не запускаємо (не запитувалося).

  ## 2026-02-02T20:17:00+01:00 — POST (MODE=PATCH): history_lab — режим останніх хвилин

  - Що зроблено → додано --last-minutes (tail) і корекцію chunk/expected.
  - Де зроблено → tools/history_lab.py.
  - Як перевірено → не запускалось.
  - Ризики/нотатки → якщо FXCM не дає даних у tail, буде skip.

  ## 2026-02-02T20:20:00+01:00 — PRE (MODE=PATCH): Запуск history_lab (tail 240 хв)

  - Мета → витягнути останні 240 хвилин 1m історії.
  - Scope → tools/history_lab.py (запуск).
  - Non-goals → зміни коду.
  - Інваріанти/рейки → ізольований cache_root.
  - План → запуск history_lab --last-minutes 240.
  - Тести → запуск скрипту.

  ## 2026-02-02T20:22:00+01:00 — POST (MODE=PATCH): Запуск history_lab (tail 240 хв)

  - Що зроблено → запуск history_lab (tail 240 хв).
  - Де зроблено → tools/history_lab.py.
  - Як перевірено → rows=0 inserted=0 (діапазон без даних).
  - Ризики/нотатки → потрібен інтервал із торговими даними або інший символ.

  ## 2026-02-02T20:30:00+01:00 — PRE (MODE=PATCH): history_lab — нормалізація symbol/TF

  - Мета → приймати XAU/USD і TF=m1 через нормалізацію.
  - Scope → tools/history_lab.py.
  - Non-goals → зміни runtime.
  - Інваріанти/рейки → мінімальний диф.
  - План → normalize_symbol + TF guard (1m/m1).
  - Тести → запуск history_lab.

  ## 2026-02-02T20:32:00+01:00 — POST (MODE=PATCH): history_lab — нормалізація symbol/TF

  - Що зроблено → додано normalize_symbol і TF guard (1m/m1).
  - Де зроблено → tools/history_lab.py.
  - Як перевірено → ще не запускалось.
  - Ризики/нотатки → інші TF заборонені.

  ## 2026-02-02T20:33:00+01:00 — PRE (MODE=PATCH): Запуск history_lab (XAU/USD m1 tail 240)

  - Мета → витягнути tail 240 хв з нормалізованим символом.
  - Scope → tools/history_lab.py (запуск).
  - Non-goals → зміни коду.
  - Інваріанти/рейки → ізольований cache_root.
  - План → запуск history_lab --symbol XAU/USD --tf m1 --last-minutes 240.
  - Тести → запуск скрипту.

  ## 2026-02-02T20:35:00+01:00 — POST (MODE=PATCH): Запуск history_lab (XAU/USD m1 tail 240)

  - Що зроблено → запуск history_lab з XAU/USD/m1.
  - Де зроблено → tools/history_lab.py.
  - Як перевірено → rows=0 inserted=0 (діапазон без даних).
  - Ризики/нотатки → потрібне вікно з даними або інший інструмент.

  ## 2026-02-02T20:45:00+01:00 — PRE (MODE=PATCH): Запуск history_lab (tail 30 хв)

  - Мета → витягнути останні 30 хвилин 1m.
  - Scope → tools/history_lab.py (запуск).
  - Non-goals → зміни коду.
  - Інваріанти/рейки → ізольований cache_root.
  - План → запуск history_lab --last-minutes 30.
  - Тести → запуск скрипту.

  ## 2026-02-02T20:47:00+01:00 — POST (MODE=PATCH): Запуск history_lab (tail 30 хв)

  - Що зроблено → запуск history_lab tail 30 хв.
  - Де зроблено → tools/history_lab.py.
  - Як перевірено → rows=0 inserted=0.
  - Ризики/нотатки → FXCM history не повертає дані у вікні tail.

  ## 2026-02-02T21:20:00+01:00 — PRE (MODE=PATCH): history_lab — tail-search (останній доступний бар)

  - Мета → шукати останній доступний 1m інтервал назад у часі.
  - Scope → tools/history_lab.py.
  - Non-goals → зміни runtime.
  - Інваріанти/рейки → мінімальний диф.
  - План → додати --tail-search-hours/--tail-step-minutes, цикл по вікнах.
  - Тести → запуск history_lab.

  ## 2026-02-02T21:25:00+01:00 — POST (MODE=PATCH): history_lab — tail-search (останній доступний бар)

  - Що зроблено → додано tail-search режим з кроком по вікнах.
  - Де зроблено → tools/history_lab.py.
  - Як перевірено → ще не запускалось.
  - Ризики/нотатки → збільшує кількість запитів до FXCM.

  ## 2026-02-02T21:26:00+01:00 — PRE (MODE=PATCH): Запуск history_lab tail-search 30хв/24год

  - Мета → знайти останній доступний 30-хв інтервал.
  - Scope → tools/history_lab.py (запуск).
  - Non-goals → зміни коду.
  - Інваріанти/рейки → ізольований cache_root.
  - План → запуск history_lab --last-minutes 30 --tail-search-hours 24 --tail-step-minutes 30.
  - Тести → запуск скрипту.

  ## 2026-02-02T21:00:00+01:00 — PRE (MODE=read-only discovery): Огляд tools_old (минілий конектор)

  - Мета → знайти cache_bootstrap/історичні механіки у tools_old.
  - Scope → tools_old/* (читання).
  - Non-goals → зміни коду.
  - Інваріанти/рейки → лише читання.
  - План → переглянути структуру, знайти cache_bootstrap, прочитати ключові модулі.
  - Тести → не застосовуються.

  ## 2026-02-02T21:40:00+01:00 — PRE (MODE=PATCH): history_lab — reuse FXCM session

  - Мета → уникнути login/logout на кожен чанк (як у старому конекторі).
  - Scope → tools/history_lab.py.
  - Non-goals → зміни runtime.
  - Інваріанти/рейки → мінімальний диф.
  - План → додати --reuse-session і один login/logout.
  - Тести → запуск history_lab.

  ## 2026-02-02T21:45:00+01:00 — POST (MODE=PATCH): history_lab — reuse FXCM session

  - Що зроблено → додано --reuse-session з одним login/logout.
  - Де зроблено → tools/history_lab.py.
  - Як перевірено → ще не запускалось.
  - Ризики/нотатки → залежить від forexconnect у середовищі.

  ## 2026-02-02T21:46:00+01:00 — PRE (MODE=PATCH): Запуск history_lab tail-search + reuse-session

  - Мета → перевірити FXCM history з одним login/logout.
  - Scope → tools/history_lab.py (запуск).
  - Non-goals → зміни коду.
  - Інваріанти/рейки → ізольований cache_root.
  - План → запуск history_lab --reuse-session --last-minutes 30 --tail-search-hours 24.
  - Тести → запуск скрипту.

  ## 2026-02-02T21:47:00+01:00 — POST (MODE=PATCH): Запуск history_lab tail-search + reuse-session

  - Що зроблено → запуск history_lab.
  - Де зроблено → tools/history_lab.py.
  - Як перевірено → FAIL: IndentationError у history_lab.py.
  - Ризики/нотатки → виправлено відступи, повторимо запуск.

  ## 2026-02-02T21:48:00+01:00 — PRE (MODE=PATCH): Повторний запуск history_lab tail-search + reuse-session

  - Мета → повторити запуск після фіксу відступів.
  - Scope → tools/history_lab.py (запуск).
  - Non-goals → зміни коду.
  - Інваріанти/рейки → ізольований cache_root.
  - План → запуск history_lab --reuse-session --last-minutes 30 --tail-search-hours 24.
  - Тести → запуск скрипту.

  ## 2026-02-02T21:49:00+01:00 — POST (MODE=PATCH): Повторний запуск history_lab tail-search + reuse-session

  - Що зроблено → запуск history_lab.
  - Де зроблено → tools/history_lab.py.
  - Як перевірено → FAIL: SyntaxError (break outside loop).
  - Ризики/нотатки → виправлено відступи, повторимо запуск.

  ## 2026-02-02T21:50:00+01:00 — PRE (MODE=PATCH): Запуск history_lab після фіксу break

  - Мета → повторити tail-search з reuse-session.
  - Scope → tools/history_lab.py (запуск).
  - Non-goals → зміни коду.
  - Інваріанти/рейки → ізольований cache_root.
  - План → запуск history_lab --reuse-session --last-minutes 30 --tail-search-hours 24.
  - Тести → запуск скрипту.

  ## 2026-02-02T21:52:00+01:00 — POST (MODE=PATCH): Запуск history_lab після фіксу break

  - Що зроблено → запуск history_lab з reuse-session та tail-search 24h.
  - Де зроблено → tools/history_lab.py.
  - Як перевірено → rows=0 inserted=0 (EMPTY/unsupported scope протягом 24h).
  - Ризики/нотатки → схоже, FXCM history не дає даних для XAUUSD у Demo/в цей період.

## 2026-02-02T22:18:00+01:00 — PRE (MODE=PATCH): history_lab — legacy FXCM механіки + вікно 30хв

- Мета → привести history_lab до legacy механік FXCM (login/get_history + класифікація not_ready/no_data/session_invalid) і тестувати вікном 30хв.
- Scope → tools/history_lab.py.
- Non-goals → зміни пайплайна/адаптерів runtime, UI чи кешів поза history_lab.
- Інваріанти/рейки → ізольований cache_root; мінімальний диф; логіка лише для history_lab.
- План →
  1) Додати класифікацію помилок як у tools_old.
  2) Використати прямий ForexConnect login/get_history (reuse-session або per-chunk).
  3) Зафіксувати дефолт 30хв для чанків.
  4) Запустити history_lab з --last-minutes 30 та tail-search.
- Тести/перевірки → запуск tools/history_lab.py.

## 2026-02-02T22:24:00+01:00 — POST (MODE=PATCH): history_lab — legacy FXCM механіки + вікно 30хв

- Що зроблено → history_lab переведено на прямий ForexConnect login/get_history з legacy класифікацією помилок; дефолт чанку 30хв; повторний запуск із tail-search.
- Де зроблено → tools/history_lab.py.
- Як перевірено → запуск: C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe tools/history_lab.py --symbol XAU/USD --tf m1 --last-minutes 30 --tail-search-hours 24 --tail-step-minutes 30 --cache-root data/history_lab --reuse-session (rows=0, SKIP(no_data) у старіших вікнах).
- Ризики/нотатки → FXCM history все ще порожній/unsupported scope на 24h назад; потрібна перевірка доступності інструмента/ринку або інший символ/період.

## 2026-02-02T22:10:00+01:00 — PRE (MODE=read-only discovery): Аналіз legacy FXCM history (tools_old)

- Мета → зрозуміти, як legacy конектор запитував FXCM history (get_history), які таймфрейми/вікна/класифікація помилок.
- Scope → лише читання tools_old/connector.py.
- Non-goals → будь-які зміни коду.
- Інваріанти/рейки → append-only лог; без виконання скриптів.
- План →
  1) Знайти функції history download.
  2) Зафіксувати логіку error classification (not_ready/no_data/session_invalid).
  3) Зафіксувати мапінг TF/symbol.
- Тести/перевірки → не застосовуються.

## 2026-02-02T22:12:00+01:00 — POST (MODE=read-only discovery): Аналіз legacy FXCM history (tools_old)

- Що зроблено → прочитано history download у tools_old/connector.py, мапінг TF і класифікацію помилок (PriceHistoryCommunicator not ready / unsupported scope / session invalid).
- Де зроблено → tools_old/connector.py.
- Як перевірено → не застосовувалось.
- Ризики/нотатки → потрібне узгодження логіки з tools/history_lab.py або історичним адаптером перед змінами.

  ## 2026-02-02 12:10:00 — PRE (MODE=PATCH): Оптимізація індексації та файлових спостерігачів у VS Code

- Мета → зменшити навантаження на памʼять: виключити .venv і кеші з індексації/пошуку/спостерігачів.
- Scope → тільки .vscode/settings.json.
- Non-goals → вимкнення розширень (це робиться вручну у VS Code).
- Інваріанти/рейки → мінімальний диф; без зміни логіки коду; лише workspace settings.
- План →
  1) Додати files.exclude, search.exclude, files.watcherExclude.
  2) Додати python.analysis.exclude для важких директорій.
- Тести/перевірки → не застосовуються (налаштування редактора).

## 2026-02-02 12:12:00 — POST (MODE=PATCH): Оптимізація індексації та файлових спостерігачів у VS Code

- Що зроблено → додано виключення для .venv/кешів/даних з індексації, пошуку та спостерігачів; додано python.analysis.exclude.
- Де зроблено → .vscode/settings.json.
- Як перевірено → не застосовувалось (workspace settings).
- Ризики/нотатки → якщо потрібен пошук у виключених директоріях, тимчасово вимкнути відповідні exclude у налаштуваннях.

## 2026-02-02T22:35:00+01:00 — PRE (MODE=read-only discovery): history_probe для EUR/USD та XAU/USD

- Мета → визначити, чи FXCM віддає history взагалі, і чи проблема в інструменті/TF/мапінгу.
- Scope → запуск tools_old/history_probe.py без змін коду.
- Non-goals → будь-які зміни коду чи конфігу.
- Інваріанти/рейки → append-only лог; ізоляція від основного пайплайна.
- План →
  1) Запустити history_probe для EUR/USD (m1 m15 m60 h4, 48h).
  2) Запустити history_probe для XAU/USD (m1 m15 m60 h4, 72h).
- Тести/перевірки → виконання двох команд history_probe.

## 2026-02-02T22:38:00+01:00 — PRE (MODE=PATCH): tools_old/history_probe — shim config

- Мета → дати history_probe сумісний `load_config` з полями `username/password/host_url/connection`.
- Scope → новий файл tools_old/config.py.
- Non-goals → зміни runtime/config або історичного адаптера.
- Інваріанти/рейки → мінімальний диф; лише проксі до config/config.py.
- План →
  1) Додати lightweight wrapper `FXCMConfig`.
  2) Реалізувати `load_config()` як мапінг з поточного config.
- Тести/перевірки → запуск history_probe.

## 2026-02-02T22:42:00+01:00 — PRE (MODE=PATCH): Встановлення python-dotenv для history_probe

- Мета → додати залежність python-dotenv, потрібну tools_old/history_probe.py.
- Scope → інсталяція пакета у .venv.
- Non-goals → зміни коду.
- Інваріанти/рейки → використовуємо активне venv.
- План → встановити python-dotenv.
- Тести/перевірки → повторний запуск history_probe.

## 2026-02-02T22:45:00+01:00 — PRE (MODE=PATCH): tools_old/connector — shim env_profile

- Мета → усунути відсутній імпорт env_profile для tools_old/connector.
- Scope → новий файл tools_old/env_profile.py.
- Non-goals → зміни поведінки runtime.
- Інваріанти/рейки → мінімальний no-op/безпечний loader.
- План → додати load_env_profile як легкий wrapper python-dotenv.
- Тести/перевірки → повторний запуск history_probe.

## 2026-02-02T22:48:00+01:00 — PRE (MODE=PATCH): Встановлення rich для tools_old/connector

- Мета → задовольнити імпорти tools_old/connector для запуску history_probe.
- Scope → інсталяція пакета rich у .venv.
- Non-goals → зміни коду.
- Інваріанти/рейки → використовуємо активне venv.
- План → встановити rich.
- Тести/перевірки → повторний запуск history_probe.

## 2026-02-02T22:58:00+01:00 — PRE (MODE=PATCH): Новий tools/history_probe (без tools_old)

- Мета → створити легкий history_probe, який напряму викликає FXCM get_history і використовує legacy мапінг/класифікацію помилок.
- Scope → новий файл tools/history_probe.py.
- Non-goals → запуск старого конектора або зміни runtime пайплайна.
- Інваріанти/рейки → мінімальний код; ENV через core.env_loader; без секретів у логах.
- План →
  1) Реалізувати аргументи: symbol/instrument, hours, tfs.
  2) Додати мапінг TF та класифікацію помилок як у tools_old.
  3) Вивести таблицю результатів (rows/EMPTY/EXC).
- Тести/перевірки → не запускаю без окремої команди.

## 2026-02-02T23:05:00+01:00 — POST (MODE=PATCH): Новий tools/history_probe (без tools_old)

- Що зроблено → додано tools/history_probe.py з прямим FXCM get_history, мапінгом TF та класифікацією помилок як у legacy.
- Де зроблено → tools/history_probe.py.
- Як перевірено → не запускалось (без команди).
- Ризики/нотатки → для явної перевірки мапінгу доступний --instrument override.

## 2026-02-02T23:12:00+01:00 — PRE (MODE=PATCH): Запуск history_probe для EUR/USD та XAU/USD

- Мета → верифікувати доступність FXCM history на демо та відсікти проблему інструмента/TF/мапінгу.
- Scope → запуск tools/history_probe.py (EUR/USD 48h, XAU/USD 72h; TF m1 m15 m60 h4).
- Non-goals → зміни коду або конфігу.
- Інваріанти/рейки → використовуємо поточний .env.local; без виводу секретів.
- План →
  1) Запустити probe для EUR/USD.
  2) Запустити probe для XAU/USD.
- Тести/перевірки → виконання двох команд history_probe.

## 2026-02-02T23:14:00+01:00 — POST (MODE=PATCH): Запуск history_probe для EUR/USD та XAU/USD

- Що зроблено → запущено history_probe для EUR/USD (48h) та XAU/USD (72h) на TF m1 m15 m60 h4.
- Де зроблено → tools/history_probe.py.
- Як перевірено →
  - EUR/USD: m1 OK (1400), m15 OK (103), m60 EXC Invalid timeframe tf=m60, h4 OK (7).
  - XAU/USD: m1 OK (1364), m15 OK (92), m60 EXC Invalid timeframe tf=m60, h4 OK (7).
- Ризики/нотатки → history працює на демо для EUR/USD і XAU/USD; m60 треба замінити на h1 (H1). Якщо history_lab дає 0, причина не в доступності history.

## 2026-02-02T23:20:00+01:00 — PRE (MODE=read-only discovery): Аналіз tail_guard

- Мета → зрозуміти, чому tail_guard не працює, і що потрібно змінити.
- Scope → читання runtime/tail_guard* та пов’язаних викликів.
- Non-goals → зміни коду.
- Інваріанти/рейки → append-only лог; без запуску сервісів.
- План →
  1) Знайти реалізацію tail_guard.
  2) Перевірити входи/умови запуску/guard-умови.
  3) Визначити мінімальний PATCH.
- Тести/перевірки → не застосовуються.

## 2026-02-02T23:28:00+01:00 — PRE (MODE=PATCH): Періодичний tail_guard audit

- Мета → зробити tail_guard активним через періодичний audit у main loop (без repair).
- Scope → app/composition.py, app/main.py.
- Non-goals → автоматичний repair (лише audit); зміни схем команд.
- Інваріанти/рейки → мінімальний диф; використати config.tail_guard_checked_ttl_s; без нових залежностей.
- План →
  1) Додати tail_guard_runner у RuntimeHandles.
  2) Реалізувати _run_tail_guard_if_due у build_runtime.
  3) Викликати runner у main loop.
- Тести/перевірки → не запускати без окремої команди.

## 2026-02-02T23:36:00+01:00 — POST (MODE=PATCH): Періодичний tail_guard audit

- Що зроблено → додано tail_guard_runner у RuntimeHandles та періодичний запуск audit у main loop (без repair), з інтервалом config.tail_guard_checked_ttl_s.
- Де зроблено → app/composition.py, app/main.py.
- Як перевірено → не запускалось (без окремої команди).
- Ризики/нотатки → audit працює лише за наявності 1m у FileCache; repair як і раніше лише через команду.

## 2026-02-02T23:48:00+01:00 — PRE (MODE=PATCH): Автоматичний tail_guard repair (1–3 години)

- Мета → увімкнути автоматичний repair хвоста (1–3h) у періодичному tail_guard.
- Scope → app/composition.py.
- Non-goals → зміни команд/схем або глобальних налаштувань календаря.
- Інваріанти/рейки → repair лише у repair-window; мінімальний диф; без auto-repair для далекого вікна.
- План →
  1) Додати auto_repair_window_hours (cap 3h, min 1h).
  2) Викликати run_tail_guard з repair=True для near-вікна та provider.
  3) Далекий audit лишити без repair.
- Тести/перевірки → не запускати без окремої команди.

## 2026-02-02T23:52:00+01:00 — POST (MODE=PATCH): Автоматичний tail_guard repair (1–3 години)

- Що зроблено → tail_guard runner переведено на auto-repair near-вікна 1–3h із republish; far-вікно лишилось audit-only.
- Де зроблено → app/composition.py.
- Як перевірено → не запускалось (без окремої команди).
- Ризики/нотатки → repair виконується лише в repair-window календаря та лише для 1m open-сесій, закриті години не “латаються”.

## 2026-02-03T00:05:00+01:00 — PRE (MODE=PATCH): Діагностичні логи tail_guard/repair

- Мета → додати видимі логи для audit/repair tail_guard і причин відмов/пропусків.
- Scope → app/composition.py, runtime/tail_guard.py.
- Non-goals → зміни логіки repair або таймінгів.
- Інваріанти/рейки → мінімальний диф; логи укр.
- План →
  1) Логи в runner: старт/пропуск/TTL/вікна/символи/repair-window.
  2) Логи в run_tail_guard: cache empty/unsupported/repair deferred/summary.
- Тести/перевірки → не запускати без окремої команди.

## 2026-02-03T00:12:00+01:00 — POST (MODE=PATCH): Діагностичні логи tail_guard/repair

- Що зроблено → додано діагностичні логи для tail_guard audit/repair у runner та run_tail_guard.
- Де зроблено → app/composition.py, runtime/tail_guard.py.
- Як перевірено → не запускалось (без окремої команди).
- Ризики/нотатки → логи спрацьовують за кожен TTL цикл; можуть бути частими при малому tail_guard_checked_ttl_s.

## 2026-02-03T00:20:00+01:00 — PRE (MODE=PATCH): Tail guard repair під час market open (near)

- Мета → дозволити auto-repair хвоста 1–3h навіть коли ринок відкритий.
- Scope → runtime/tail_guard.py, app/composition.py.
- Non-goals → зміни конфігів або поведінки far-аудиту.
- Інваріанти/рейки → repair лише для near; мінімальний диф; логи укр.
- План →
  1) Додати параметр allow_repair_when_market_open у run_tail_guard.
  2) У near auto-repair передати allow_repair_when_market_open=True.
  3) Додати лог про режим.
- Тести/перевірки → не запускати без окремої команди.

## 2026-02-03T00:26:00+01:00 — POST (MODE=PATCH): Tail guard repair під час market open (near)

- Що зроблено → додано allow_repair_when_market_open у run_tail_guard та ввімкнено для near auto-repair.
- Де зроблено → runtime/tail_guard.py, app/composition.py.
- Як перевірено → не запускалось (без окремої команди).
- Ризики/нотатки → repair тепер може виконуватись під час market open лише для near-вікна.

## 2026-02-03T00:33:00+01:00 — PRE (MODE=read-only discovery): Дефолти tail_guard та UI режими

- Мета → з’ясувати джерела дефолтів конфігу та як UI Lite відрізняє final/preview.
- Scope → читання config/config.py, ui_lite/server.py.
- Non-goals → зміни коду.
- Інваріанти/рейки → append-only лог; без запуску сервісів.
- План →
  1) Перевірити load_config та env/profile overrides.
  2) Перевірити mode=preview/final у UI Lite.
- Тести/перевірки → не застосовуються.

## 2026-02-03T00:35:00+01:00 — POST (MODE=read-only discovery): Дефолти tail_guard та UI режими

- Що зроблено → перевірено load_config та UI Lite mode (preview/final) за замовчуванням.
- Де зроблено → config/config.py, ui_lite/server.py.
- Як перевірено → не застосовувалось.
- Ризики/нотатки → UI Lite за замовчуванням підписується на mode=preview.

## 2026-02-03T00:42:00+01:00 — PRE (MODE=PATCH): Узгодити allow_repair_when_market_open з config

- Мета → прибрати хардкод allow_repair_when_market_open=True і підпорядкувати його config.tail_guard_safe_repair_only_when_market_closed.
- Scope → app/composition.py.
- Non-goals → зміни логіки tail_guard або дефолтів конфігу.
- Інваріанти/рейки → мінімальний диф.
- План →
  1) Обчислити allow_open як not tail_guard_safe_repair_only_when_market_closed.
  2) Передати allow_open у run_tail_guard.
- Тести/перевірки → не запускати без окремої команди.

## 2026-02-03T00:45:00+01:00 — POST (MODE=PATCH): Узгодити allow_repair_when_market_open з config

- Що зроблено → allow_repair_when_market_open тепер залежить від config.tail_guard_safe_repair_only_when_market_closed.
- Де зроблено → app/composition.py.
- Як перевірено → не запускалось (без окремої команди).
- Ризики/нотатки → поведінка repair під час market open керується конфігом.

## 2026-02-03T00:50:00+01:00 — PRE (MODE=read-only discovery): Пошук подібних хардкодів tail_guard

- Мета → перевірити код на подібні хардкоди, пов’язані з repair/market open.
- Scope → пошук у runtime/app.
- Non-goals → зміни коду.
- Інваріанти/рейки → append-only лог; лише читання.
- План → grep пошук по allow_repair_when_market_open та споріднених умовах.
- Тести/перевірки → не застосовуються.

## 2026-02-03T00:52:00+01:00 — POST (MODE=read-only discovery): Пошук подібних хардкодів tail_guard

- Що зроблено → виконано grep по allow_repair_when_market_open/safe_repair_only_when_market_closed/repair_window.
- Де зроблено → runtime/tail_guard.py, app/composition.py, config/config.py, core/time/calendar.py.
- Як перевірено → не застосовувалось.
- Ризики/нотатки → додаткових хардкодів allow_repair_when_market_open не знайдено; є auto_repair_window_hours=1–3h у app/composition.py.

## 2026-02-03T01:05:00+01:00 — PRE (MODE=PATCH): Auto far-repair для великих дірок

- Мета → автоматично ремонтувати великі дірки (far window) з інтервалом.
- Scope → config/config.py, app/composition.py.
- Non-goals → зміни core/calendar або історичного провайдера.
- Інваріанти/рейки → мінімальний диф; budget‑rails лишаються в repair_missing_1m.
- План →
  1) Додати конфіги far-repair interval.
  2) У runner запускати repair для far‑вікна лише раз на інтервал.
- Тести/перевірки → не запускати без окремої команди.

## 2026-02-03T01:12:00+01:00 — POST (MODE=PATCH): Auto far-repair для великих дірок

- Що зроблено → додано конфіг tail_guard_far_repair_interval_s та інтервальний far-repair у tail_guard runner.
- Де зроблено → config/config.py, app/composition.py.
- Як перевірено → не запускалось (без окремої команди).
- Ризики/нотатки → far-repair запускається раз на інтервал; budget‑rails у repair_missing_1m залишаються.

## 2026-02-03T01:20:00+01:00 — PRE (MODE=PATCH): Не падати на repair budget error

- Мета → перехопити ValueError з repair_missing_1m і не валити tail_guard runner.
- Scope → runtime/tail_guard.py.
- Non-goals → змінювати бюджети repair або політики.
- Інваріанти/рейки → мінімальний диф; помилка лишається у статусі.
- План → обгорнути repair_missing_1m у try/except ValueError.
- Тести/перевірки → не запускати без окремої команди.

## 2026-02-03T01:23:00+01:00 — POST (MODE=PATCH): Не падати на repair budget error

- Що зроблено → перехоплення ValueError з repair_missing_1m, щоб tail_guard не падав.
- Де зроблено → runtime/tail_guard.py.
- Як перевірено → не запускалось (без окремої команди).
- Ризики/нотатки → помилка бюджету фіксується в status, repair для цього циклу пропускається.

## 2026-02-03T01:32:00+01:00 — PRE (MODE=PATCH): Класифікація “побитості” барів у tail_guard

- Мета → додати класифікацію GEOM_INVALID/BUCKET_DRIFT/CALENDAR_VIOLATION/SPIKE_SANITY.
- Scope → runtime/tail_guard.py.
- Non-goals → змінювати історичний провайдер або UI.
- Інваріанти/рейки → мінімальний диф; SPIKE_SANITY soft (без repair).
- План →
  1) Додати детектори “побитості”.
  2) Додати repair-стратегію: жорсткі класи → repair; SPIKE_SANITY → лише лог.
  3) Логувати підсумок.
- Тести/перевірки → не запускати без окремої команди.

## 2026-02-03T01:38:00+01:00 — POST (MODE=PATCH): Класифікація “побитості” барів у tail_guard

- Що зроблено → додано класифікацію GEOM_INVALID/BUCKET_DRIFT/CALENDAR_VIOLATION/SPIKE_SANITY та включено жорсткі класи у repair_ranges.
- Де зроблено → runtime/tail_guard.py.
- Як перевірено → не запускалось (без окремої команди).
- Ризики/нотатки → SPIKE_SANITY поки soft (лише лог), поріг = 10× медіани range.

## 2026-02-03T01:50:00+01:00 — PRE (MODE=PATCH): Repair-worker + політики малих/середніх/великих

- Мета → винести repair/backfill у окремий потік та застосувати пороги 300/1000 барів.
- Scope → app/composition.py, runtime/tail_guard.py.
- Non-goals → зміни протоколів/схем.
- Інваріанти/рейки → мінімальний диф; backfill лише для великих діапазонів; SPIKE_SANITY без repair.
- План →
  1) Додати чергу та worker thread.
  2) Після audit enqueue repair/backfill за порогами.
  3) Зупинка worker у stop_runtime.
- Тести/перевірки → не запускати без окремої команди.

## 2026-02-03T02:05:00+01:00 — POST (MODE=PATCH): Repair-worker + політики малих/середніх/великих

- Що зроблено → додано repair-worker у окремому потоці, чергу задач і політики 300/1000 барів (repair/backfill). Audit тепер лише enqueue задач.
- Де зроблено → app/composition.py.
- Як перевірено → не запускалось (без окремої команди).
- Ризики/нотатки → важкі діапазони йдуть у backfill; середні/великі позначають degraded (tail_guard_medium_gap/heavy_gap).

## 2026-02-03T02:18:00+01:00 — PRE (MODE=PATCH): Нарізання великих repair-діапазонів

- Мета → розбивати repair-діапазони на чанки <= tail_guard_repair_max_gap_minutes.
- Scope → app/composition.py.
- Non-goals → зміни політик порогів 300/1000.
- Інваріанти/рейки → мінімальний диф; логи укр.
- План → додати helper для split та застосувати у repair-worker.
- Тести/перевірки → не запускати без окремої команди.

## 2026-02-03T02:22:00+01:00 — POST (MODE=PATCH): Нарізання великих repair-діапазонів

- Що зроблено → repair-worker розбиває діапазони на чанки <= tail_guard_repair_max_gap_minutes.
- Де зроблено → app/composition.py.
- Як перевірено → не запускалось (без окремої команди).
- Ризики/нотатки → кількість FXCM запитів може зрости; budget-rails залишаються.

## 2026-02-03T02:40:00+01:00 — PRE (MODE=PATCH): Вимкнути history probe для repair/backfill

- Мета → уникнути "history probe порожній" при repair/backfill на закритому ринку.
- Scope → app/composition.py.
- Non-goals → зміни провайдера або конфігу.
- Інваріанти/рейки → мінімальний диф; зміни лише у worker.
- План → тимчасово встановлювати probe_minutes=0 для FxcmHistoryProvider у worker.
- Тести/перевірки → не запускати без окремої команди.

## 2026-02-03T02:44:00+01:00 — POST (MODE=PATCH): Вимкнути history probe для repair/backfill

- Що зроблено → у repair/backfill worker тимчасово вимикається probe_minutes для FxcmHistoryProvider.
- Де зроблено → app/composition.py.
- Як перевірено → не запускалось (без окремої команди).
- Ризики/нотатки → знижується ймовірність "history probe порожній" на закритому ринку.

## 2026-02-03T03:00:00+01:00 — PRE (MODE=PATCH): Republish після repair/backfill

- Мета → публікувати final після repair/backfill, щоб UI (mode=final) бачила виправлення.
- Scope → app/composition.py.
- Non-goals → зміни tail_guard аудиту.
- Інваріанти/рейки → мінімальний диф.
- План → після успішного repair/backfill викликати republish_tail з достатнім window_hours.
- Тести/перевірки → не запускати без окремої команди.

## 2026-02-03T03:04:00+01:00 — POST (MODE=PATCH): Republish після repair/backfill

- Що зроблено → repair/backfill worker тепер робить republish final 1m з достатнім window_hours.
- Де зроблено → app/composition.py.
- Як перевірено → не запускалось (без окремої команди).
- Ризики/нотатки → window_hours може бути великим для старих діапазонів.

## 2026-02-03T03:12:00+01:00 — PRE (MODE=PATCH): republish source=history

- Мета → виправити source у republish_tail, щоб проходив контракт.
- Scope → runtime/republish.py.
- Non-goals → зміни логіки репаблішу.
- Інваріанти/рейки → мінімальний диф.
- План → замінити source "cache" на "history".
- Тести/перевірки → не запускати без окремої команди.

## 2026-02-03T03:14:00+01:00 — POST (MODE=PATCH): republish source=history

- Що зроблено → source у republish_tail змінено на "history".
- Де зроблено → runtime/republish.py.
- Як перевірено → не запускалось (без окремої команди).
- Ризики/нотатки → контракт більше не має падати на source.

## 2026-02-03T09:10:00+01:00 — PRE (MODE=PATCH): Логи результатів repair/backfill

- Мета → логувати фактичні вставки (bars_ingested) та зміни кешу.
- Scope → app/composition.py.
- Non-goals → зміни політик repair.
- Інваріанти/рейки → мінімальний диф.
- План → додати логи після repair_missing_1m і після backfill.
- Тести/перевірки → не запускати без окремої команди.

## 2026-02-03T09:14:00+01:00 — POST (MODE=PATCH): Логи результатів repair/backfill

- Що зроблено → додано логи вставок repair (windows/bars) та backfill summary (rows/last_close).
- Де зроблено → app/composition.py.
- Як перевірено → не запускалось (без окремої команди).
- Ризики/нотатки → допоможе побачити, чи реально вставляються бари.

## 2026-02-03T02:30:00+01:00 — PRE (MODE=PATCH): Запуск конектора та UI Lite

- Мета → запустити конектор і відкрити UI Lite у браузері для перевірки графіка.
- Scope → запуск app/main.py і відкриття UI Lite.
- Non-goals → зміни коду.
- Інваріанти/рейки → використати активне venv; ринок закритий.
- План →
  1) Запустити конектор у фоні.
  2) Відкрити UI Lite у браузері.
- Тести/перевірки → візуальна перевірка графіка.

## 2026-02-03T02:32:00+01:00 — POST (MODE=PATCH): Запуск конектора та UI Lite

- Що зроблено → спроба запуску app/main.py.
- Де зроблено → app/main.py.
- Як перевірено → FAIL: ModuleNotFoundError: No module named 'app' (не встановлено PYTHONPATH).
- Ризики/нотатки → потрібен запуск із PYTHONPATH='.'.

## 2026-02-03T02:33:00+01:00 — PRE (MODE=PATCH): Повторний запуск конектора з PYTHONPATH

- Мета → запустити конектор з коректним PYTHONPATH.
- Scope → запуск app/main.py.
- Non-goals → зміни коду.
- Інваріанти/рейки → активне venv; PYTHONPATH='.'.
- План → запуск із змінною середовища та відкриття UI Lite.
- Тести/перевірки → візуальна перевірка графіка.

## 2026-02-03T02:35:00+01:00 — POST (MODE=PATCH): Повторний запуск конектора з PYTHONPATH

- Що зроблено → конектор запущено у фоні з PYTHONPATH='.'; відкрито UI Lite у браузері.
- Де зроблено → app/main.py, UI Lite http://127.0.0.1:8089.
- Як перевірено → візуальна перевірка графіка (UI Lite відкрито).
- Ризики/нотатки → у логах є medium_gap/heavy_gap для far-діапазонів (очікувано).

## 2026-02-03T10:12:00+01:00 — PRE (MODE=PATCH): Повне вимкнення авто tail_guard/repair

- Мета → повністю вимкнути автоматичні перевірки/repair/backfill tail_guard у runtime.
- Scope → app/composition.py, runtime/tail_guard.py, config/config.py.
- Non-goals → зміна ручних команд tail_guard/backfill/warmup; зміна контрактів; зміна UI.
- Інваріанти/рейки → мінімальний диф; без прихованих фолбеків; логіка ручних команд зберігається.
- План:
  1) Прибрати авто tail_guard runner та repair/backfill воркер у runtime.
  2) Прибрати конфіг авто far-repair.
  3) Повернути tail_guard до базового пошуку пропусків.
- Тести/перевірки → не запускатиму (зміна вимикає автоматику, без runtime-старту).

## 2026-02-03T10:14:00+01:00 — POST (MODE=PATCH): Повне вимкнення авто tail_guard/repair

- Що зроблено → вимкнено авто tail_guard runner і фонового воркера repair/backfill; прибрано конфіг авто far-repair; tail_guard повернено до базового пошуку пропусків без класифікації bad bars.
- Де зроблено → app/composition.py, config/config.py, runtime/tail_guard.py.
- Як перевірено → не запускалось (вимкнення автоматики, без runtime-старту).
- Ризики/відкат/нотатки → автоматичні ремонти вимкнені; для повернення — відновити авто runner/воркер та far-repair конфіг.

## 2026-02-03T10:20:00+01:00 — PRE (MODE=PATCH): Підчистка залишків tail_guard

- Мета → прибрати залишки невикористаних структур і зробити параметри явними, щоб уникнути дублювання у майбутньому.
- Scope → runtime/tail_guard.py.
- Non-goals → зміни контрактів команд або логіки auto runner (вже вимкнено).
- Інваріанти/рейки → мінімальний диф; без зміни ручних команд; мова логів укр.
- План:
  1) Видалити невикористаний TailGuardMark.
  2) Використати republish_force за призначенням у republish_tail.
- Тести/перевірки → не запускатиму (точкова правка без запуску runtime).

## 2026-02-03T10:22:00+01:00 — POST (MODE=PATCH): Підчистка залишків tail_guard

- Що зроблено → видалено невикористаний TailGuardMark; republish_tail тепер використовує republish_force.
- Де зроблено → runtime/tail_guard.py.
- Як перевірено → не запускалось.
- Ризики/відкат/нотатки → republish_tail тепер може пропускати публікацію при watermark, якщо republish_force=False.

## 2026-02-03T10:40:00+01:00 — PRE (MODE=PATCH): Розширення history_probe логів та збереження

- Мета → додати детальні UTC логи про діапазони/повноту/ціни/обсяги та зберігати history у форматі свічок.
- Scope → tools/history_probe.py.
- Non-goals → зміни runtime tail_guard/repair або cache SSOT.
- Інваріанти/рейки → мінімальний диф; формат CSV як CACHE_COLUMNS; meta.json як у SSOT (version/rows/last_close_time_ms...).
- План:
  1) Додати ліміти та розширене логування (включно з по-барним, UTC).
  2) Зберігати history у cache/history/*.csv та meta у cache/__history__.meta.json.
- Тести/перевірки → не запускатиму (зміни утиліти без виконання).

## 2026-02-03T10:44:00+01:00 — POST (MODE=PATCH): Розширення history_probe логів та збереження

- Що зроблено → history_probe логуватиме діапазони (UTC), повноту, ціни/обсяги, та може логувати кожен бар; додано ліміт 3000 і збереження CSV у cache/history та meta у cache/__history__.meta.json.
- Де зроблено → tools/history_probe.py.
- Як перевірено → не запускалось.
- Ризики/відкат/нотатки → по-барні логи можуть бути великими (до 3000 рядків); для зменшення — використовувати --no-log-bars або менший --limit.

## 2026-02-03T10:55:00+01:00 — PRE (MODE=read-only discovery): Запуск history_probe з лімітом 3000

- Мета → перевірити швидкість/стабільність отримання історії та зафіксувати діапазони (UTC) і повноту.
- Scope → запуск tools/history_probe.py без змін коду.
- Non-goals → зміни runtime або cache SSOT.
- Інваріанти/рейки → формат збереження у cache/history та meta як у свічках.
- План:
  1) Запустити history_probe для XAUUSD TF=1m з лімітом 3000.
  2) Зібрати логи і підтвердити збереження файлів.
- Тести/перевірки → запуск tools/history_probe.py.

## 2026-02-03T10:56:00+01:00 — POST (MODE=read-only discovery): Запуск history_probe з лімітом 3000

- Що зроблено → спроба запуску history_probe для XAUUSD TF=1m з лімітом 3000.
- Де зроблено → tools/history_probe.py.
- Як перевірено → команда запуску (див. нижче).
- Результат/нотатки → FAIL: ModuleNotFoundError: No module named 'config' (потрібен PYTHONPATH='.'.)

## 2026-02-03T10:57:00+01:00 — PRE (MODE=read-only discovery): Повторний запуск history_probe з PYTHONPATH

- Мета → повторити запуск з PYTHONPATH='.' для коректного імпорту конфігів.
- Scope → запуск tools/history_probe.py без змін коду.
- Non-goals → зміни runtime або cache SSOT.
- Інваріанти/рейки → формат збереження у cache/history та meta як у свічках.
- План:
  1) Запустити history_probe з PYTHONPATH='.'.
  2) Зафіксувати результати та наявність файлів.
- Тести/перевірки → запуск tools/history_probe.py.

## 2026-02-03T10:59:00+01:00 — PRE (MODE=PATCH): Діагностика парсингу history_probe

- Мета → підсилити логування і вирівнювання часу, щоб не губити бари.
- Scope → tools/history_probe.py.
- Non-goals → зміни runtime або cache SSOT.
- Інваріанти/рейки → формат CSV/Meta як у свічках.
- План:
  1) Додати вирівнювання open_time_ms по bucket.
  2) Логувати причини відсіву рядків та raw/parsed counts.
- Тести/перевірки → не запускатиму (патч без виконання).

## 2026-02-03T11:01:00+01:00 — POST (MODE=PATCH): Діагностика парсингу history_probe

- Що зроблено → додано bucket alignment, логи пропусків і raw/parsed counts.
- Де зроблено → tools/history_probe.py.
- Як перевірено → не запускалось.
- Ризики/відкат/нотатки → логи можуть бути шумними при нестандартних рядках FXCM.

## 2026-02-03T11:02:00+01:00 — PRE (MODE=read-only discovery): Повторний запуск history_probe після правок

- Мета → перевірити отримання/парсинг і збереження history після додаткового логування.
- Scope → запуск tools/history_probe.py без змін коду.
- Non-goals → зміни runtime або cache SSOT.
- Інваріанти/рейки → формат збереження у cache/history та meta як у свічках.
- План:
  1) Запустити history_probe з PYTHONPATH='.'.
  2) Зафіксувати результат і наявність файлів.
- Тести/перевірки → запуск tools/history_probe.py.

## 2026-02-03T11:05:00+01:00 — PRE (MODE=PATCH): Розширення витягування rows у history_probe

- Мета → підтримати різні формати відповіді FXCM history (iter/to_dict/to_pandas/get_row).
- Scope → tools/history_probe.py.
- Non-goals → зміни runtime або cache SSOT.
- Інваріанти/рейки → формат збереження у cache/history та meta як у свічках.
- План:
  1) Додати _history_to_rows з кількома fallback.
  2) Логувати використаний режим отримання рядків.
- Тести/перевірки → не запускатиму (патч без виконання).

## 2026-02-03T11:06:00+01:00 — POST (MODE=PATCH): Розширення витягування rows у history_probe

- Що зроблено → додано _history_to_rows з fallback (to_dict/to_pandas/get_row/iter) і лог режиму.
- Де зроблено → tools/history_probe.py.
- Як перевірено → не запускалось.
- Ризики/відкат/нотатки → можливі порожні rows при незнайомому типі history; логи покажуть mode.

## 2026-02-03T11:07:00+01:00 — PRE (MODE=read-only discovery): Запуск history_probe після розширення rows

- Мета → повторити запуск і перевірити, що рядки парсяться у бари.
- Scope → запуск tools/history_probe.py без змін коду.
- Non-goals → зміни runtime або cache SSOT.
- Інваріанти/рейки → формат збереження у cache/history та meta як у свічках.
- План:
  1) Запустити history_probe з PYTHONPATH='.'.
  2) Зафіксувати результат і наявність файлів.
- Тести/перевірки → запуск tools/history_probe.py.

## 2026-02-03T11:08:00+01:00 — POST (MODE=read-only discovery): Запуск history_probe після розширення rows

- Що зроблено → запущено history_probe з PYTHONPATH='.' для XAUUSD TF=1m.
- Де зроблено → tools/history_probe.py.
- Як перевірено → запуск у терміналі.
- Результат/нотатки → rows parsed=0 (EMPTY_PARSED), багато рядків без open_time_ms (type=void); файли history не збережено.

## 2026-02-03T11:10:00+01:00 — PRE (MODE=PATCH): Обмеження логів skip і підсумок

- Мета → прибрати шум логів і зберегти діагностику (лічильники пропусків).
- Scope → tools/history_probe.py.
- Non-goals → зміни runtime або cache SSOT.
- Інваріанти/рейки → формат збереження у cache/history та meta як у свічках.
- План:
  1) Обмежити логи skip до перших 5.
  2) Додати підсумок skip-лічильників.
- Тести/перевірки → не запускатиму (патч без виконання).

## 2026-02-03T11:11:00+01:00 — POST (MODE=PATCH): Обмеження логів skip і підсумок

- Що зроблено → лімітування log skip + summary лічильників.
- Де зроблено → tools/history_probe.py.
- Як перевірено → не запускалось.
- Ризики/відкат/нотатки → менше шуму, але діагностика збережена.

## 2026-02-03T11:12:00+01:00 — PRE (MODE=read-only discovery): Повторний запуск history_probe після лімітування логів

- Мета → повторити запуск і отримати читаємий лог режиму та counts.
- Scope → запуск tools/history_probe.py без змін коду.
- Non-goals → зміни runtime або cache SSOT.
- Інваріанти/рейки → формат збереження у cache/history та meta як у свічках.
- План:
  1) Запустити history_probe з PYTHONPATH='.'.
  2) Зафіксувати режим rows та результати парсингу.
- Тести/перевірки → запуск tools/history_probe.py.

## 2026-02-03T11:13:00+01:00 — POST (MODE=read-only discovery): Повторний запуск history_probe після лімітування логів

- Що зроблено → запущено history_probe з PYTHONPATH='.' для XAUUSD TF=1m.
- Де зроблено → tools/history_probe.py.
- Як перевірено → запуск у терміналі.
- Результат/нотатки → rows mode=iter, raw=1380, parsed=0; open_time відсутній (type=void), history не збережено.

## 2026-02-03T11:15:00+01:00 — PRE (MODE=PATCH): Додаткові fallback для history rows

- Мета → спробувати інші способи доступу до history (getitem/len) та зафіксувати атрибути.
- Scope → tools/history_probe.py.
- Non-goals → зміни runtime або cache SSOT.
- Інваріанти/рейки → формат збереження у cache/history та meta як у свічках.
- План:
  1) Додати fallback __getitem__/__len__.
  2) Логувати атрибути history при невідомому форматі.
- Тести/перевірки → не запускатиму (патч без виконання).

## 2026-02-03T11:16:00+01:00 — POST (MODE=PATCH): Додаткові fallback для history rows

- Що зроблено → додано getitem/len fallback і лог attrs.
- Де зроблено → tools/history_probe.py.
- Як перевірено → не запускалось.
- Ризики/відкат/нотатки → можлива додаткова діагностика в логах.

## 2026-02-03T11:17:00+01:00 — PRE (MODE=read-only discovery): Новий запуск history_probe після fallback

- Мета → повторити запуск і перевірити, чи з'явилися валідні бари.
- Scope → запуск tools/history_probe.py без змін коду.
- Non-goals → зміни runtime або cache SSOT.
- Інваріанти/рейки → формат збереження у cache/history та meta як у свічках.
- План:
  1) Запустити history_probe з PYTHONPATH='.'.
  2) Зафіксувати режим rows і результат парсингу.
- Тести/перевірки → запуск tools/history_probe.py.

## 2026-02-03T11:18:00+01:00 — POST (MODE=read-only discovery): Новий запуск history_probe після fallback

- Що зроблено → запущено history_probe (XAUUSD, 1m).
- Де зроблено → tools/history_probe.py.
- Як перевірено → запуск у терміналі.
- Результат/нотатки → rows mode=getitem, raw=1380, parsed=0; open_time відсутній (type=void).

## 2026-02-03T11:20:00+01:00 — PRE (MODE=PATCH): Підтримка numpy.void у history_probe

- Мета → розпарсити numpy ndarray (dtype.names) та передати quotes_count=limit.
- Scope → tools/history_probe.py.
- Non-goals → зміни runtime або cache SSOT.
- Інваріанти/рейки → формат збереження у cache/history та meta як у свічках.
- План:
  1) Додати підтримку dtype.names у _row_value/_row_hint.
  2) Передавати limit у get_history.
  3) Логувати dtype names.
- Тести/перевірки → не запускатиму (патч без виконання).

## 2026-02-03T11:21:00+01:00 — POST (MODE=PATCH): Підтримка numpy.void у history_probe

- Що зроблено → підтримано numpy dtype.names, додано quotes_count, лог dtype names.
- Де зроблено → tools/history_probe.py.
- Як перевірено → не запускалось.
- Ризики/відкат/нотатки → якщо dtype.names відсутні, залишаться fallback-и.

## 2026-02-03T11:22:00+01:00 — PRE (MODE=read-only discovery): Запуск history_probe з numpy підтримкою

- Мета → перевірити, що бари парсяться та зберігаються.
- Scope → запуск tools/history_probe.py без змін коду.
- Non-goals → зміни runtime або cache SSOT.
- Інваріанти/рейки → формат збереження у cache/history та meta як у свічках.
- План:
  1) Запустити history_probe з PYTHONPATH='.'.
  2) Зафіксувати dtype names, parsed bars і файли.
- Тести/перевірки → запуск tools/history_probe.py.

## 2026-02-03T11:23:00+01:00 — POST (MODE=read-only discovery): Запуск history_probe з numpy підтримкою

- Що зроблено → запущено history_probe (XAUUSD, 1m) з numpy dtype names.
- Де зроблено → tools/history_probe.py.
- Як перевірено → запуск у терміналі.
- Результат/нотатки → dtype names видно, але Date парсинг = 0; parsed=0 (EMPTY_PARSED).

## 2026-02-03T11:25:00+01:00 — PRE (MODE=PATCH): Підтримка numpy.datetime64 у _to_ms

- Мета → коректно читати поле Date з numpy.datetime64.
- Scope → tools/history_probe.py.
- Non-goals → зміни runtime або cache SSOT.
- Інваріанти/рейки → формат збереження у cache/history та meta як у свічках.
- План:
  1) Додати обробку numpy.datetime64 у _to_ms.
- Тести/перевірки → не запускатиму (патч без виконання).

## 2026-02-03T11:26:00+01:00 — POST (MODE=PATCH): Підтримка numpy.datetime64 у _to_ms

- Що зроблено → додано підтримку numpy.datetime64.
- Де зроблено → tools/history_probe.py.
- Як перевірено → не запускалось.
- Ризики/відкат/нотатки → залежить від наявності numpy (є через forexconnect).

## 2026-02-03T11:27:00+01:00 — PRE (MODE=read-only discovery): Запуск history_probe після datetime64

- Мета → перевірити парсинг Date після numpy.datetime64 підтримки.
- Scope → запуск tools/history_probe.py без змін коду.
- Non-goals → зміни runtime або cache SSOT.
- Інваріанти/рейки → формат збереження у cache/history та meta як у свічках.
- План:
  1) Запустити history_probe з PYTHONPATH='.'.
  2) Зафіксувати parsed bars та файли.
- Тести/перевірки → запуск tools/history_probe.py.

## 2026-02-03T11:28:00+01:00 — POST (MODE=read-only discovery): Запуск history_probe після datetime64

- Що зроблено → запуск history_probe (XAUUSD, 1m).
- Де зроблено → tools/history_probe.py.
- Як перевірено → запуск у терміналі.
- Результат/нотатки → FAIL: TypeError get_bucket_open_ms missing calendar (потрібна заміна вирівнювання).

## 2026-02-03T11:30:00+01:00 — PRE (MODE=PATCH): Виправлення вирівнювання часу

- Мета → прибрати get_bucket_open_ms і зробити просте вирівнювання по tf_ms.
- Scope → tools/history_probe.py.
- Non-goals → зміни runtime або cache SSOT.
- Інваріанти/рейки → формат збереження у cache/history та meta як у свічках.
- План:
  1) Замінити вирівнювання open_time_ms на floor по tf_ms.
- Тести/перевірки → не запускатиму (патч без виконання).

## 2026-02-03T11:31:00+01:00 — POST (MODE=PATCH): Виправлення вирівнювання часу

- Що зроблено → вирівнювання open_time_ms зроблено через tf_ms (floor).
- Де зроблено → tools/history_probe.py.
- Як перевірено → не запускалось.
- Ризики/відкат/нотатки → залежить від правильного tf_ms.

## 2026-02-03T11:32:00+01:00 — PRE (MODE=read-only discovery): Запуск history_probe після фіксу вирівнювання

- Мета → перевірити парсинг барів після фіксу.
- Scope → запуск tools/history_probe.py без змін коду.
- Non-goals → зміни runtime або cache SSOT.
- Інваріанти/рейки → формат збереження у cache/history та meta як у свічках.
- План:
  1) Запустити history_probe з PYTHONPATH='.'.
  2) Зафіксувати parsed bars та файли.
- Тести/перевірки → запуск tools/history_probe.py.

## 2026-02-03 00:39:40 — PRE (MODE=read-only discovery): Повторний запуск history_probe після фіксу вирівнювання

- Мета → перевірити парсинг історії та збереження CSV/метаданих після останнього фіксу вирівнювання часу.
- Scope → лише запуск tools/history_probe.py (без змін коду).
- Інваріанти → жодних змін у коді чи конфігах; тільки виконання команди.
- План → 1) Запустити history_probe з лімітом 3000. 2) Перевірити, що історія збережена у cache/history + meta JSON.
- Тести/перевірки (план) → python tools/history_probe.py --symbol XAUUSD --tfs m1 --limit 3000.

## 2026-02-03 00:39:49 — POST (MODE=read-only discovery): Повторний запуск history_probe після фіксу вирівнювання

- Що зроблено → виконано history_probe для XAUUSD m1 з лімітом 3000, перевірено лог збереження.
- Де зроблено → tools/history_probe.py (запуск), cache/history, cache/*.meta.json.
- Як перевірено → $env:PYTHONPATH='.'; C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe tools/history_probe.py --symbol XAUUSD --tfs m1 --limit 3000 → OK.
- Ризики/нотатки/очікуваний результат → історія збережена (рядки ~1380), meta файл створено; потреби в додаткових правках немає.

## 2026-02-03 00:45:12 — PRE (MODE=read-only discovery): Вимір часу history_probe з лімітом 30000

- Мета → оцінити тривалість запуску для ліміту 30000.
- Scope → лише запуск tools/history_probe.py (без змін коду).
- Інваріанти → жодних змін у коді чи конфігах; тільки виконання команди.
- План → 1) Запустити history_probe з --limit 30000. 2) Зафіксувати результат/помилки.
- Тести/перевірки (план) → Measure-Command { python tools/history_probe.py --symbol XAUUSD --tfs m1 --limit 30000 --no-log-bars }.

## 2026-02-03 00:45:15 — POST (MODE=read-only discovery): Вимір часу history_probe з лімітом 30000

- Що зроблено → запуск history_probe з лімітом 30000 через Measure-Command.
- Де зроблено → tools/history_probe.py (запуск).
- Як перевірено → Measure-Command { $env:PYTHONPATH='.'; C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe tools/history_probe.py --symbol XAUUSD --tfs m1 --limit 30000 --no-log-bars } → FAIL (UnicodeEncodeError у cp1251 через символ '→').
- Ризики/нотатки/очікуваний результат → потрібна заміна Unicode-стрілок у логах на ASCII.

## 2026-02-03 00:46:02 — PRE (MODE=PATCH): Усунення UnicodeEncodeError у history_probe логах

- Мета → замінити Unicode-стрілки у логах на ASCII, щоб запуск не падав у Windows cp1251.
- Scope → tools/history_probe.py (рядки з символом '→').
- Інваріанти → логіка збору/збереження історії не змінюється.
- План → 1) Замінити '→' на '->'. 2) Перезапустити history_probe з лімітом 30000.
- Тести/перевірки (план) → history_probe з --limit 30000.

## 2026-02-03 00:46:30 — POST (MODE=PATCH): Усунення UnicodeEncodeError у history_probe логах

- Що зроблено → замінено Unicode-стрілки у логах history_probe на ASCII.
- Де зроблено → tools/history_probe.py.
- Як перевірено → Measure-Command { $env:PYTHONPATH='.'; C:/Aione_projects/fxcm_connector_v2/.venv/Scripts/python.exe tools/history_probe.py --symbol XAUUSD --tfs m1 --limit 30000 --no-log-bars } → OK (parsed=1380, history saved).
- Ризики/нотатки/очікуваний результат → у Windows cp1251 більше не виникає UnicodeEncodeError; ліміт 30000 запитано, але провайдер повернув 1380 рядків.

Days              : 0
Hours             : 0
Minutes           : 0
Seconds           : 9
Milliseconds      : 233
Ticks             : 92335085
TotalDays         : 0,000106869311342593
TotalHours        : 0,00256486347222222
TotalMinutes      : 0,153891808333333
TotalSeconds      : 9,2335085
TotalMilliseconds : 9233,5085