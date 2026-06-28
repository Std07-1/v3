# ADR-0076 — Unified Auth Gate (один замок, один ключ, одне місце)

> **Status**: Accepted
> **Date**: 2026-06-28
> **Keywords**: auth, Bearer, console, ARCHI_AUTH_TOKEN, api_v3, X-API-Key, TokenStore, fail-closed, constant-time, middleware, SSOT, I5, I7
> **Initiative**: `auth_unification_v1`
> **Supersedes/absorbs**: невикористані частини ADR-0052 S7/S8 security scaffolding (`runtime/api/{auth,rate_limit,audit,csrf,sanitizer}.py`)
> **Cross-ref**: ADR-0048/0049 (NarrativeEnricher thesis/presence на публічному WS), ADR-0058 (Public Read-Only API), ADR-0025 (Archi Console)

---

## Context

Доступ до приватної консолі Арчі (`/api/agent/*`, `/api/archi/*` — inner_thought, chat,
budget, directives, logs) має бути **token-only**: власник із токеном — заходить; будь-хто
інший — ні. Жива перевірка (2026-06-28, VPS, deployed HEAD `409dd2e`) підтверджує що **двері
зачинені**:

```
localhost:8000  anon /api/agent/state        → 401   owner → 200   wrong-token → 401
localhost:8000  anon /api/archi/{thinking,directives,feed,chat,logs} → 401
gorn.aione-smc.com anon /api/agent/state     → 401   (nginx proxy → :8000)
aione-smc.com  anon /api/agent/state         → 200 text/html (SPA catch-all, НЕ дані; vhost не проксує /api/agent|/api/archi)
ARCHI_AUTH_TOKEN у процесі ws_server         → SET (present)
```

Захист **працює**. Проблема — не в дверях, а в механізмі за ними: він зібраний як
**нашарування трьох саморобних "столів"**, кожен від іншого агента в інший час:

| Механізм | Призначення (threat model) | Стан |
|---|---|---|
| `_archi_auth` (`runtime/ws/ws_server.py:2155`) | консоль власника — **1 спільний секрет** | `==` (не constant-time), **fail-OPEN** при порожньому токені, **15× copy-paste** call-site |
| `check_bearer`/`AuthConfig` (`runtime/api/auth.py`, ADR-0052 S7) | правильні примітиви: `hmac.compare_digest`, reason codes, **fail-closed** | **сирота** — імпортується лише в `tests/test_api_security.py`, нуль production-wiring |
| `_validate_token`+`TokenStore` (`runtime/api_v3/endpoints.py:144`) | публічне data-API — **N виданих ключів** через `X-API-Key` + Redis | працює (`api_v3.enabled=true`), але окремий словник/семантика |

Плюс **мертвий вантаж** ADR-0052 S7/S8: `rate_limit.py`, `audit.py`, `csrf.py`, `sanitizer.py`
— повністю написані, **нуль production-import** (`grep` по `runtime/` поза їх власними файлами =
пусто). Виглядає як безпека, не робить нічого = maturity-regression dead code (X35).

Дві латентні/побічні діри, виявлені аудитом:

* **F1 (Medium, латентний)**: `_archi_auth:2159-2160` при `enabled && not token` → `return True`
  (відкрито). `config.json:agent_console.auth_token=""` — єдине, що тримає двері, це env
  `ARCHI_AUTH_TOKEN`. Якщо env зникне (deploy slip, рестарт без env) — консоль **тихо
  відчиняється всьому світу, без логу/алярму** (порушує I5 degraded-but-loud). Іронія:
  сирота-`check_bearer` тут fail-**closed**.
* **F2 (Low, by-design)**: анонімний `/ws` несе `archi_thesis` (продукт — лишаємо) **і**
  `archi_presence` internals (`accumulator`, `accumulator_threshold`, `next_wake`, raw
  `silence_h`) — внутрішня механіка планувальника. `ui_v4/src/lib/agentState.ts:155-158`
  LOCKED-коментар забороняє raw `silence_h` без UX-контексту, а воно вже на дроті.

**Суть**: два домени auth (1 секрет власника / N ключів API) — легітимно різні. Помилка не в
тому що їх два, а в тому що кожен зроблений руками у своєму файлі, а готовий правильний модуль
лежить нерозпакований. **Стіл є — деталі розкидані по трьох кутах.**

---

## Alternatives

### A1 — Точковий патч (тільки F1): `return True` → `return False`
+ Мінімальний diff (1 рядок), знімає міну негайно.
− Лишає 15× copy-paste, `==`, трьох-механізмову роздробленість, мертвий ADR-0052 suite.
− Не відповідає на запит власника «зробити єдиним». Стіл лишається розкиданим.

### A2 — Грандіозний auth-фреймворк (RBAC, scopes, JWT, sessions, CSRF, OAuth)
+ «Промислово».
− **Over-engineering** — власник уже зняв nginx-stealth саме за надмірність. Single-owner
  консоль + read-only public API не потребують RBAC/JWT/sessions. Порушує right-sizing.

### A3 — Один модуль-SSOT + один gate + дві політики **(ОБРАНО)**
Зібрати наявний правильний `auth.py` як SSOT примітивів; консоль провести через **один**
aiohttp middleware замість 15 inline-перевірок; api_v3 лишити на `TokenStore` (інший threat
model) але узгодити словник (reason/audit). Викинути зайве (`csrf`, `hmac_sign/verify`),
доключити корисне (`rate_limit`, `audit`, `sanitizer`). Один config-розділ `security`.
+ Відповідає «один замок, одне місце»; right-sized; лагодить F1+F4 структурно (через
  `check_bearer`); прибирає dead code; зберігає легітимну різницю двох доменів.
− Більший за A1 (staged P1–P6), вимагає дисциплінованої верифікації кожного slice.

---

## Decision

**Прийнято A3.** Архітектура:

```
   SSOT примітивів:  runtime/api/auth.py  (check_bearer: constant-time · reason codes · fail-CLOSED)
                                 │
   ОДИН gate (aiohttp @middleware) ── deny → 401 + 1 audit-рядок (I5 гучно)
        ПОЛІТИКА A (console)            ПОЛІТИКА B (api_v3)
        1 секрет: ARCHI_AUTH_TOKEN      N ключів: X-API-Key → TokenStore
        /api/agent/* /api/archi/*       /api/v3/*
        Bearer (+ ?token= лише SSE)
                                 │
   ОДИН config-розділ:  config.json → "security": { console_auth, api_v3_auth }
```

Public-поверхні (`/api/status`, `/api/context`, `/ws`, static, SPA) gate **не чіпає** —
лишаються відкриті, як зараз (це публічний продукт).

### Рішення по пунктах аудиту

| # | Рішення | Дія |
|---|---|---|
| **F1** fail-open | **FIX (критично)** | `_archi_auth` делегує в `check_bearer`; `enabled && no token` → **deny** + `_log.error(ARCHI_AUTH_MISCONFIG)` раз на старті |
| **F4** `==` timing | **FIX** | той самий делегат → `hmac.compare_digest` |
| **F5** 3 механізми | **UNIFY концептуально** | `auth.py`=SSOT; console через 1 middleware; api_v3 лишає `TokenStore`, говорить тим самим словником |
| **F6** orphan suite | **поштучно** | `auth.py`→wire(SSOT); `rate_limit`→wire(chat POST, api_v3); `audit`→wire(deny); `sanitizer`→wire(chat POST — зараз лише `.strip()`, нема cap); `csrf.py`→**DELETE** (N/A для header-token, не cookie); `hmac_sign/verify`→**DELETE** (не вживається) |
| **F2** WS presence | **TRIM** | `archi_thesis` публічний (продукт); presence internals (`accumulator*`,`next_wake`,raw `silence_h`) прибрати з free-tier у `narrative_enricher` |
| **F3** `?token=` URL | **KEEP** right-sized | лише 2 SSE-роути, той самий constant-time, задокументувати. EventSource не вміє headers — стандарт |
| **F7** nginx `.bak` | **DELETE** ops | прибрати `/etc/nginx/{archi,gorn}.bak.1782*` + старі `smc_ui_v2.conf.bak.*` після enumerate (D13.5) |

### Slices (кожен — окремий PATCH ≤150 LOC, окремий deploy з explicit go)

* **P1** (~40 LOC): `_archi_auth`→`check_bearer` (fail-closed + constant-time) + startup misconfig-log. *Fixes F1, F4.*
* **P2** (~120, перев. видалення): 15 inline → 1 prefix-middleware (`/api/agent/`,`/api/archi/`). *F5-console.* `?token=` doc.
* **P3** (~30): `security.console_auth` у config.json; прибрати плутаний `agent_console.auth_token`.
* **P4** (~120): delete `csrf`+`hmac_*`; wire `rate_limit`+`audit`+`sanitizer`. *F6.*
* **P5** (~30): trim presence internals у `narrative_enricher`. *F2* (cross-ref ADR-0049).
* **P6** (~40, опц.): api_v3 на спільний reason/audit-словник. *F5-api.*
* **F7**: VPS nginx cleanup (поза репо).

---

## Consequences

**Позитив**:
* Один модуль володіє auth (SSOT) — зміна політики = одне місце, не 15.
* Fail-**closed** за замовчуванням: втрата токена = двері замикаються + гучний лог (I5), не тихо відчиняються.
* Constant-time порівняння токена (прибирає timing side-channel).
* Dead code прибрано — те що лишилось, реально працює (anti-maturity-regression).
* `rate_limit`/`audit`/`sanitizer` нарешті захищають дорогі/мутуючі роути (chat коштує Claude $).

**Ризик / blast radius**:
* Middleware (P2) не має зачепити public-поверхні. Mitigation: prefix-only по
  `/api/agent/`+`/api/archi/`, точне дзеркало поточної поведінки; верифікація **тією ж живою
  curl-матрицею** before/after кожного slice (D13.4 baseline: anon→401, owner→200, public→open).
* Зміна dev-режиму: раніше «enabled+no token = відкрито». Тепер потрібен явний
  `allow_no_token_dev_mode: true` для локального dev без токена. Default = fail-closed.
* P5 змінює wire-контракт narrative (прибирає поля з free-tier). Frontend `agentState.ts`
  має деградувати тихо на відсутні поля (вже робить — optional chaining).

**Negative-checklist**:
* НЕ вводимо RBAC/scopes/JWT/sessions (right-sizing).
* НЕ збиваємо два домени в один токен (різні threat models).
* НЕ чіпаємо публічний чарт/наратив/WS-доступ.

---

## Rollback

* **P1**: revert одного commit → `_archi_auth` повертає стару поведінку. Токен у env лишається, двері далі зачинені (поточний стан).
* **P2**: revert middleware-commit → inline-перевірки повертаються (зберегти у git history до видалення).
* **P4 deletes** (`csrf`,`hmac_*`): `git revert` повертає файли; вони й так не вживались.
* **P5**: revert → presence-поля повертаються на дріт.
* Кожен slice deploy-иться окремо з observation window (D9.1, 60s bot/daemon) — STOP-сигнал → негайний rollback (D9 step 9).

---

## Quality Axes

* **Ambition target**: R2 (структурна консолідація наявних рішень у SSOT — не нова епічна фіча, але й не косметика).
* **Maturity impact**: M3 → **M4** (auth перестає бути клаптиковим: один SSOT-модуль, fail-closed
  default, constant-time, нуль auth dead-code, гучна видимість denied-спроб).
