# Compliance Risk Register — Trading Platform v3

- **Дата аудиту**: 2026-03-08
- **Роль**: R_COMPLIANCE v1.0
- **Scope**: Full codebase — licenses, security, OWASP, CVE, disclaimers, data protection, technology lifecycle

---

## Резюме

| Severity | Знайдено | Закрито | Відкрито |
|----------|----------|---------|----------|
| **S0** | 0 | 0 | 0 |
| **S1** | 3 | 0 | 3 |
| **S2** | 4 | 0 | 4 |
| **S3** | 4 | 0 | 4 |
| **Разом** | 11 | 0 | 11 |

**Загальна оцінка**: Прийнятний рівень для personal localhost-only trading tool. Жодних S0 (критичних) ризиків. Базова безпекова гігієна на місці: секрети не в коді, `.gitignore` коректний, input validation присутня, path traversal guards є, FXCM SDK не вендориться, ліцензії deps сумісні. **Головні gaps**: відсутній фінансовий disclaimer, Python 3.7 EOL, Redis без auth.

---

## ФАЗА 1: AUDIT — Inventory

### 1.1 Dependency License Matrix

#### Python Dependencies

| Пакет | Версія | SPDX | Copyleft? | Сумісність з Proprietary | CVE | EOL? |
|-------|--------|------|-----------|--------------------------|-----|------|
| redis | 5.0.1 | MIT | Ні | ✅ Сумісна | [ASSUMED — verify: `pip audit`] | Ні |
| numpy | 1.21.6 | BSD-3-Clause | Ні | ✅ Сумісна | [ASSUMED — verify: `pip audit`] | Так (released 2022, series 1.21 no longer maintained) |
| pandas | 1.1.5 | BSD-3-Clause | Ні | ✅ Сумісна | [ASSUMED — verify: `pip audit`] | Так (released 2020, series 1.x deprecated) |
| python-dotenv | ≥0.21 | BSD-3-Clause | Ні | ✅ Сумісна | [ASSUMED — verify: `pip audit`] | Ні |
| aiohttp | ≥3.8 | Apache-2.0 | Ні | ✅ Сумісна (потрібен attribution) | [ASSUMED — verify: `pip audit`] | Ні |
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

### 1.2 Власна ліцензія

[VERIFIED: LICENSE_v1]

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
| LICENSE_v1 | ❌ | ❌ | ✅ Section 11 |
| README.md | ❌ | ❌ | ❌ |
| UI (Svelte) | ❌ | ❌ | ❌ |
| docs/ | ❌ | ❌ | ❌ |
| API responses | ❌ | ❌ | ❌ |
| SECURITY.md | N/A | N/A | N/A |

### 1.7 Existing Compliance Artifacts

| Артефакт | Існує? | Якість |
|----------|--------|--------|
| `LICENSE_v1` | ✅ | Повна, professional |
| `SECURITY.md` | ✅ | Повна, SLA визначені |
| `THIRD_PARTY_NOTICES.md` | ✅ | Повна, всі deps покриті |
| `docs/compliance/fxcm-sdk-license-review.md` | ✅ | Детальна, conservative |
| `requirements.txt` FXCM disclaimer | ✅ | Є |
| Financial disclaimer | ❌ | **ВІДСУТНІЙ** |
| `docs/compliance/risk_register.md` | ✅ | Цей файл |
| Python lockfile (hashes) | ❌ | Відсутній |

---

## ФАЗА 2: ASSESS — Risk Register

| ID | Домен | Severity | Likelihood | Опис | Evidence | Поточна мітигація | Рекомендована мітигація | SLA |
|----|-------|----------|------------|------|----------|-------------------|------------------------|-----|
| **R1** | Security/Auth | **S1** | Low (localhost) | WS/HTTP endpoints без автентифікації | [VERIFIED: ws_server.py] — no auth, no token, no session | 127.0.0.1 binding only | Документувати як accepted risk для localhost. Implement token auth якщо deployment змінюється | This sprint |
| **R2** | Security/Auth | **S1** | Low (localhost) | Redis без паролю — будь-який процес на machine має R/W доступ | [VERIFIED: config.json, ws_server.py, tick_publisher_fxcm.py] — zero `password=` parameter | 127.0.0.1 binding | Додати `requirepass` в Redis config + `password=` в connection code. Або документувати як accepted risk | This sprint |
| **R3** | Compliance/Financial | **S1** | Medium | Відсутній "Not Financial Advice" disclaimer. SMC engine має scoring/grades (A+/A/B/C) що можуть бути сприйняті як торгові рекомендації | [VERIFIED: grep README.md + Svelte — zero matches] | LICENSE_v1 Section 11 (warranty disclaimer, але не financial) | Додати explicit disclaimer в README.md + UI footer/StatusBar | This sprint |
| **R4** | Supply Chain | **S2** | Low | Python dependencies без lockfile з hash verification. `pip install` не верифікує integrity | [VERIFIED: no requirements.lock / pip.lock] | Pinned versions в requirements.txt | Створити lockfile: `pip-compile --generate-hashes` або `pip freeze --all > requirements.lock` | Plan |
| **R5** | Technology/EOL | **S1** | High | Python 3.7 EOL з June 2023. Відомі CVE без патчів. 2.5+ роки без security updates | [VERIFIED: pyproject.toml `requires-python = ">=3.7,<3.8"`] | ADR-0016 Proposed (Python upgrade plan) | Імплементувати ADR-0016 roadmap. Interim: document accepted risk | This sprint |
| **R6** | Secrets | **S2** | Low | `tick_publisher_fxcm.py`: `cfg.get("user_id")` / `cfg.get("password")` fallback з config.json. Ризик випадкового commit credentials | [VERIFIED: tick_publisher_fxcm.py:L401-402] | config.json не містить цих ключів зараз | Видалити fallback на cfg credentials, залишити тільки env vars | Plan |
| **R7** | Data Integrity | **S2** | Medium | `ssot_jsonl_fsync: false` — при crash/power failure можлива втрата даних (OS buffer not flushed) | [VERIFIED: config.json `ssot_jsonl_fsync: false`] | `flush()` після кожного запису | Або увімкнути fsync, або задокументувати trade-off (performance vs durability) в ADR | Plan |
| **R8** | Rate Limiting | **S3** | Low | WS `switch` action та HTTP `/api/status` без rate limiting — потенційний DoS | [VERIFIED: ws_server.py — scrollback has rate limit, otherwise none] | Localhost only | Per-client action throttle | Batch |
| **R9** | Privacy | **S3** | Low | Client IP (`request.remote`) логується при WS connect | [VERIFIED: ws_server.py:L826] | Local-only, logs gitignored | Якщо logs коли-небудь будуть shared — hash або omit IP | Batch |
| **R10** | IP/License | **S2** | Low | AI-generated code: LICENSE_v1 Contribution clause не має explicit AI clause. IP ownership unclear | [VERIFIED: LICENSE_v1 Section 4 — "any material you submit"] | Implicit coverage через broad language | Додати explicit AI clause до LICENSE або policy document | Plan |
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

### Що відсутнє ❌

1. **Financial disclaimer** — README.md, UI, docs — жодного
2. **Python lockfile** — no hash-verified lockfile
3. **Redis auth** — no `requirepass`
4. **ADR-0016 implementation** — Python 3.7 EOL migration stalled
5. **AI code ownership clause** — not explicit in LICENSE

### Що прийнятне за умови ⚠️

1. **No auth on endpoints** — OK for localhost, MUST review if deployment changes
2. **No encryption at rest** — OK for personal machine, MUST review if shared
3. **`ssot_jsonl_fsync: false`** — OK if data recovery from FXCM is possible, SHOULD document
4. **Config.json credential fallback** — OK while keys absent, SHOULD remove pattern

---

## ФАЗА 3: REMEDIATE — Roadmap

### P1: Financial Disclaimer (R3) — ~30 LOC

**Файли**: `README.md`  
**Що**: Додати explicit disclaimer section в README.md  
**Verify**: `grep "Not Financial Advice" README.md`

### P2: Redis Auth Documentation (R1, R2) — 0 LOC

**Файли**: Цей risk register (оновити статус)  
**Що**: Задокументувати як accepted risk для localhost deployment  
**Trigger for re-evaluation**: будь-яка зміна з localhost на network

### P3: Config Credential Fallback (R6) — ~5 LOC

**Файли**: `runtime/ingest/tick_publisher_fxcm.py`  
**Що**: Видалити `cfg.get("user_id")` / `cfg.get("password")` fallback  
**Verify**: `grep "cfg.get.*password\|cfg.get.*user_id" runtime/`

### P4: Python 3.7 EOL Documentation (R5) — 0 LOC

**Що**: ADR-0016 вже Proposed. Задокументувати як accepted risk з clear trigger  
**Trigger**: FXCM releases Python 3.11+ SDK → activate ADR-0016

### P5: AI Code Clause (R10) — 0 LOC (policy)

**Що**: Додати explicit statement в docs/compliance/ або LICENSE amendment щодо AI-generated code ownership  
**[LEGAL OPINION NEEDED]**: Exact language for AI code IP in jurisdictions relevant to author

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
