# ADR-0086: Публічний санітизований знімок Арчі — `/api/public/snapshot` (ГОРН)

## Метадані

| Поле | Значення |
| ---- | -------- |
| ID | ADR-0086 |
| Статус | **Implemented rev1** (P1-P3 live на gorn.aione-smc.com 2026-07-12, owner-go «го»; rev1 того ж дня: `inner_thought` ВИЛУЧЕНО зі знімка — перший живий знімок показав думку про бюджет → owner: «сирі думки не для інвесторів», публічний голос = теза сценарію; майбутнє public_thought/воркспейс = окремий trader-v3 ADR) |
| Дата | 2026-07-12 |
| Автори | Станіслав (owner, vision «вікно в життя автономної системи») + Fable 5 (архітектор) |
| Будується на | ADR-0048/0049 (WakeEngine + NarrativeEnricher), ADR-078 trader-v3 (canonical wake-params → Redis), ADR-0085 (RECON джерел A/B/C — реюз), ADR-0058 (патерн публічного read-only API) |
| Поважає | I7 (платформа читає, НІКОЛИ не пише за Арчі), X28 (UI dumb renderer — підписи рівнів зі знімка), X31 (нуль змін у trader-v3), S1 (read-only), I5 (degraded-but-loud: 503, не тихий фолбек) |
| Зачіпає шари | `runtime/api/public_snapshot.py` (NEW), `runtime/ws/ws_server.py` (wiring ≤10 LOC), `tests/api/test_public_snapshot.py` (NEW), споживач: `ui_archi_v2` (вже збудований, б'є `/api/public/snapshot`) |
| Initiative | `gorn_public_v1` |

---

## Quality Axes

- **Ambition target**: **R3** — перший **публічний** (без auth) контракт стану Арчі: ГОРН стає застосунком для зовнішніх трейдерів/інвесторів, а не приватним кокпітом. «Вікно в життя автономної системи 24/7».
- **Maturity impact**: **M4 → M4.5** — закривається розрив «публічна сторінка читає приватні endpoint-и»: до цього ГОРН-v2 у dev жив на сирих `/api/agent/state` + `/api/archi/directives` (auth-гейтовані, повні приватних полів). Тепер публічна поверхня має єдиний санітизований контракт з явним whitelist.

---

## 1. Контекст

Owner-vision: ui_archi_v2 = публічний трейдерський застосунок. Головна — живе кільце Арчі
+ його фокус (напрям/теза) + армовані рівні («де він прокинеться») + жива думка. Контент
**авто-фід**: усе з того, що Арчі й так емітить; нуль ручного заповнення; мільйон переглядів
= $0 токенів (сторінка читає знімок, Claude не викликається).

Проблема: сирі джерела приватні й «жирні». `agent:state` містить budget/calls; директиви —
conviction_reasoning, workspace, історію. Публічна сторінка НЕ має бачити це навіть у wire
(той самий принцип, що ADR-0048 W3: premium/приватне не приходить у frame для free).

## 2. RECON (реюз ADR-0085 §2.1, verified 2026-07-07 + live fixtures)

| Джерело | Ключ | Що дає знімку |
|---|---|---|
| A. Wake conditions (canonical, ADR-078) | Redis STRING `{ns}:wake:conditions:{sym_/→_}` = JSON list | армовані рівні: `price_cross`/`candle_close` → level, direction |
| A'. Fallback | директиви `wake_conditions[]` | той самий формат (коли Redis-ключ порожній/відсутній) |
| B. Agent state | Redis HASH `{ns}:agent:state` | presence: ts_ms, mood, health, inner_thought, next_wake_ms/reason, session |
| C. Директиви | файл `{data_dir}/v3_agent_directives.json` | `active_scenario` з **числами**: entry_zone_low/high, targets[], invalidation, direction, grade, confidence |

Все вже пишеться ботом сьогодні — нуль нових вимог до trader-v3 (X31 чистий).

## 3. Альтернативи

1. **Фронт читає сирі endpoint-и, фільтрує сам** — ВІДХИЛЕНО: приватні поля приходять у wire
   (view-source достатньо), X28-порушення (підписи рівнів рахував би фронт), auth-дірка.
2. **Бот пише окремий `{ns}:public:thesis:{sym}`** — ВІДХИЛЕНО для v1: вимагає trader-v3 ADR
   + деплой бота + новий обов'язок Арчі. Платформа вже має всі поля read-only. (Залишається
   природною еволюцією, якщо колись знадобиться bot-курований публічний текст.)
3. **Платформний білдер: композиція A+B+C → санітизований знімок** — ОБРАНО: read-only,
   нуль змін бота, санітизація в одній точці, TTL-кеш робить endpoint копійчаним.

## 4. Рішення

### D1. Контракт `GET /api/public/snapshot` (без auth — свідомо)

```jsonc
{
  "generated_ms": 1783790789294,
  "presence": {            // рівно те, що треба кільцю/голосу
    "ts_ms": ..., "mood": "analytical", "health": "ok",
    "circuit_breaker": "0", "kill_switch_active": false,
    "last_error": "",       // РЕДАКТОВАНО: "" | "1" (маркер, ніколи текст)
    "has_virtual_position": "0", "active_scenarios": 0,
    "has_active_scenario": true,
    // rev1: inner_thought ВИЛУЧЕНО — сирі думки не публічні (§6 R1, закрито)
    "next_wake_ms": ..., "next_wake_reason": "London open", "session": "london"
  },
  "focus": {               // з active_scenario, БЕЗ reasoning/checkpoints/id/trigger
    "symbol": "XAU/USD", "direction": "short", "thesis": "…",
    "session": "London", "grade": "A", "confidence_pct": 55,
    "bias": "D1:bearish H4:bearish",
    "entry_zone": [4328.0, 4335.0], "invalidation": 4348.0,
    "targets": [4305.0, 4267.0], "status": "waiting"
  },
  "watching": [            // армовані price-рівні, підписані ЗВІРКОЮ зі сценарієм
    {"level": 4348, "direction": "above", "role": "invalidation",
     "label": "інвалідація — теза скасовується"}
  ]
}
```

Правила:
- **Whitelist-only**: жодне поле не проходить «бо було в джерелі». Нове поле = правка ADR.
- Підпис рівня = перехресна звірка зі структурними полями сценарію (invalidation/entry/
  trigger/targets, eps 0.5) — НЕ вигаданий текст; фронт рендерить label як дані (X28).
- Інваріант: `focus != null` → `watching` непорожній (синтез з рівнів сценарію, якщо
  wake-умови ще не озброєні).
- `last_error` РЕДАКТОВАНО до маркера (текст може містити шляхи/внутрішнє).
- НЕ віддаються ніколи: budget/calls/token_usage, kill-причини, conviction_reasoning,
  workspace, історія, wake-умови не-цінових kind-ів (session/silence — це внутрішній ритм).

### D2. Реалізація

- `runtime/api/public_snapshot.py` (NEW): pure `build_public_snapshot(state, directives,
  wake_conditions, now_ms)` + `register_public_snapshot(app, redis_client, ns, data_dir,
  ttl_s=5)`. In-proc TTL-кеш 5s: N переглядів = ≤0.2 Redis-читань/с.
- `ws_server.py`: реюз `_agent_redis_client` + `_console_data_dir`; маунт ПОЗА `_archi_auth`
  (публічний за задумом — це рішення цього ADR, не недогляд).
- Деградація (I5): Redis/файл недоступні → **503** (фронт вже має чесний стан «зв'язок
  втрачено · знімок застиг» — верифіковано live 2026-07-12). Порожні джерела → знімок з
  `focus:null, watching:[]` (сторінка чесно каже «поза ринком»).
- Rate-контроль: TTL-кеш + nginx (існуючий). Окремого ліміту v1 не потребує.

### D3. Slices

| # | Що | LOC | Verify |
|---|----|-----|--------|
| P1 | `public_snapshot.py` + тести | ≤150 | pytest: санітизація (заборонені поля відсутні), звірка підписів, синтез-інваріант, порожні входи |
| P2 | ws_server wiring | ≤10 | локальний запуск / VPS smoke: `curl /api/public/snapshot` |
| P3 | deploy (за owner-go): scp ws_server-патч + ui_archi_v2 dist → gorn | — | SNI-curl origin-side + телефон |

## 5. Consequences

- ГОРН стає самодостатнім публічним застосунком: один endpoint, нуль auth, нуль приватного.
- Стенд `.devharness/serve_local.py` тримає той самий контракт (SSOT форми = runtime-модуль;
  стенд — копія для UI-розробки без платформи).
- Майбутнє: tier-гейт (free: затримка/один символ) вставляється в білдер однією точкою
  (патерн ADR-0049 FeatureTier).

## 6. Ризики

- **R1 `inner_thought` публічний — ЗАКРИТО (rev1, 2026-07-12)**: ризик матеріалізувався
  першим же живим знімком (думка містила бюджет «$0.11 — треба еко»). Owner: «сирі думки
  не для інвесторів». Поле вилучено зі знімка; публічний голос = теза сценарію; імпульс
  кільця = зміна публічного контенту (теза/напрям/mood/рівні). Майбутнє: `public_thought`
  або трансляція воркспейсу — окремий trader-v3 ADR.
- **R2 конкуренти бачать рівні тези**: власне продукт і є цим. Free-tier затримка — важіль
  на майбутнє (§5).

## 7. Rollback

Один рядок: прибрати `register_public_snapshot(...)` з ws_server (або config-флаг
`public_snapshot.enabled=false`, якщо додамо при деплої) → endpoint зникає, ГОРН-фронт
чесно показує «зв'язок втрачено». Дані ніде не персистяться.
