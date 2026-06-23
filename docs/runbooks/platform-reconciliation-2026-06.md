# Runbook — Platform Reconciliation (VPS `/opt/smc-v3` → origin/main)

> **Мета**: підняти production-платформу на VPS (`/opt/smc-v3`) з застарілого
> `71c4acb` (6 трав) до `origin/main` (`fd6e27d`) — **119 комітів** позаду — чисто,
> з backup, gate-ами і rollback. Все під наглядом Стаса.
>
> **Статус документа**: DISCOVERY + PLAN. Жодного production-write не виконано при
> створенні цього runbook (тільки read-only `git log/diff/show/ls-tree/status` +
> `fetch` + read-only `ssh`/`curl`). **Виконання — окремий захід під супервізією.**
>
> **Дата оцінки**: 2026-06-14. Якщо минув час — повтори §0 (drift міг зрости).
>
> **Метод-база**: той самий, що довів себе на trader-v3 git-конверсії (06-10):
> `backup → clear collisions → reset → ff-only pull → rebuild → restart → observe`.
> Деплой-гейт: **production-зміни тільки з explicit GO від Стаса** (Rule #6).

---

## 0. TL;DR — вердикт і чому це безпечно

| Питання | Відповідь | Доказ |
|---|---|---|
| FF можливий? | **ТАК, лінійний** | `71c4acb` = прямий предок `origin/main`; `0 ahead, 119 behind` `[VERIFIED]` |
| VPS-local mods унікальні? | **НІ — нічого зберігати** | 11 файлів: 9 == origin/CRLF, 2 = cowork (нижче) `[VERIFIED]` |
| Data-pipeline зачеплено? | **НІ, нуль** | `bars.py/uds.py/smc_runner.py/derive.py/contracts` = 0 комітів `[VERIFIED]` |
| config.json змінено? | **НІ** | 0 комітів торкаються; VPS == origin `[VERIFIED]` |
| Python deps змінено? | **НІ** | `requirements*.txt` = 0 комітів; pip install НЕ потрібен `[VERIFIED]` |
| Frontend deps змінено? | **ТАК** | `ui_v4/package.json` + lock (PWA `sharp`) → **npm rebuild потрібен** `[VERIFIED]` |
| Блокери pull? | **1 collision** | untracked `runtime/ws/app_keys.py` (origin його трекає) `[VERIFIED]` |
| Ризик | **СЕРЕДНІЙ** | рестарт 26-денного сервера на 5-тиж-новіший код; mitig = backup+observe |

**Суть**: це майже чистий frontend/serving upgrade (тема, mobile, PWA, drawings,
ui_archi, docs, tools-housekeeping). Backend-core (UDS, деривація, контракти,
final-bar pipeline) **не змінювався жодним з 119 комітів**. Все що на VPS
"локально namодифіковано" — або вже в origin (`deda063`), або CRLF/BOM-сміття, або
**свідомо видалений з origin failed-experiment `cowork`** (live = 404, тобто й так
не працює). **Зберігати нема чого.**

---

## 1. Категоризація 119 комітів (`71c4acb..fd6e27d`)

`268 файлів, +19308/−9712` `[VERIFIED git diff --shortstat]`. Розподіл за file-count
(`git diff --dirstat`): **tools/archive+diag ≈40%, ui_v4 ≈30%, docs ≈11%, core/smc
3.3%, ui_archi ≈5%, tests 2.6%, runtime 2%, ci 1%.**

### 1.1 Групи (LOW risk — UI/docs/tools, ~95%)

| Тема | ADR | Кіл-ть | Область | Ризик |
|---|---|---|---|---|
| Visual identity / theme system | ADR-0066 | ~27 | `ui_v4` tokens.css, themes.ts, Brand, Splash, typography | LOW |
| CommandRail / NarrativePanel «Новий кут» | 0065/0069/0070 | ~16 | `ui_v4` + малі `ws_server` wire-додатки | LOW-MED |
| Mobile UX hardening | ADR-0072 | ~12 | `ui_v4` (☰, landscape, outside-dismiss) | LOW |
| Drawing Tools V1 + 42 тести | ADR-0074 | ~11 | `ui_v4/chart/drawings` | LOW |
| Price Scale Overlay | ADR-0073 | ~6 | `ui_v4` chart (LWC native) | LOW |
| PWA installable + SW | ADR-0071 | ~4 | `ui_v4` + `ws_server` static + `package.json` | **MED** |
| ui_archi premium redesign | — | ~12 | `ui_archi` (orb, chat, SSE directives) | LOW |
| CI / exit-gates / tests | — | ~10 | `pyproject` bandit, svelte-check, httpx dep | LOW |
| Docs / ADR / README / public-launch | — | ~20 | `docs/`, README EN+MIT, scrub IP, D13/D14 | ZERO runtime |
| tools/diag + housekeeping | — | ~5 | `tools/` (40% file-count = archive moves) | LOW |
| trader-v3 changelog/docs (cross-ref) | — | ~5 | `docs/changelog`, consciousness logs | ZERO runtime |

### 1.2 🚩 RISKY / backend-дотичні коміти (перевір окремо)

| Commit | Subject | Що це + implication |
|---|---|---|
| `6ed7396` | feat(pwa): ADR-0071 installable PWA, shell-only V1 SW | **Frontend deps (`sharp`, `@types/uuid`) + новий build. Потрібен `npm install`+rebuild.** SW кешує shell — sticky для юзерів. |
| `3f2081f` | feat(ws): serve PWA assets `/manifest.json /sw.js /icons/* /offline.html /robots.txt` | **`ws_server` додає нові static-роути.** Перевір що віддаються 200 після рестарту. |
| `0d29019` | fix(sw+nginx): guard POST cache in SW, add `data:`/`blob:` to CSP for LWC GPU shaders | ⚠️ **nginx/CSP частина — ПОЗА git** (системний конфіг VPS). Якщо CSP жорстка → WebGL-графік може впасти. Перевір console. |
| `6e8e8a4` | PATCH-09: X28-fix CommandRail — backend ATR via wire; RV removed; countdown wallclock | **Wire-схема: ATR тепер у render_frame (additive); RV recompute прибрано з UI.** Єдиний consumer = ui_v4 (rebuild у тому ж коміті). |
| `6fd0c72` | feat(cr): RV restored as backend SSOT + ATR dual-format (ADR-0070 rev 2) | Продовження wire-additive ATR/RV. |
| `a52f0ca` | fix(ui): price-axis single-owner + **ws AppKey SSOT extraction** | **Створює tracked `runtime/ws/app_keys.py`** → це й є collision (§3). AppKey-fix критичний для коректності handler-ів. |
| `deda063` | fix(smc): signals P0 + narrative P1a/P1b/P2 + type annotation | **Цей коміт = апстрім VPS-хотфіксів** (narrative/signals/swings/engine). VPS вже крутить ≈цей код. |
| `b7cb6e3` | feat(cowork): subsystem v1 — memory endpoints + cadence runner | Додав cowork… |
| `4e1de5b` | chore(cowork): **remove cowork subsystem (failed experiment** per changelog 20260511-001) | …і origin його **видалив**. Net-zero. cowork відсутній у `fd6e27d`. |
| `23be04b` | fix(security): scrub VPS origin IP → `aione-vps` alias | Tracked-файли більше не містять raw IP. Без runtime-впливу. |
| `b5d4ac2` | chore(ci): bandit baseline (`pyproject` +60) | CI-only `[tool.bandit] skips`. НЕ runtime dep. |

**Висновок §1**: жодного `schema/migration/contract(data)/requirements-bump/config`
коміту. Wire-зміни (ATR/RV, PWA assets) — **additive**, єдиний consumer (ui_v4)
оновлюється в тих самих комітах. `[VERIFIED git log keyword scan]`

---

## 2. Per-file вердикт VPS-mod → upstream (deliverable #2)

Метрика: `O_w` = `git diff -w origin/main -- <file>` (working tree проти origin,
whitespace-ignored). `O_w` порожній ⟹ VPS-working **вже == origin** ⟹ discard
нічого не змінює. `[усе VERIFIED на VPS]`

### 2.1 Tracked-modified (11 файлів) — **усі SAFE-DISCARD**

| Файл | Local mod (vs HEAD) | Residual vs origin (`-w`) | Вердикт | Причина | Conf |
|---|---|---|---|---|---|
| `core/smc/engine.py` | +15 | **0** | SAFE DISCARD | == origin; апстрім `deda063` | HIGH |
| `core/smc/narrative.py` | +127/−42 | **0** | SAFE DISCARD | == origin; апстрім `deda063` | HIGH |
| `core/smc/swings.py` | +35 | **0** | SAFE DISCARD | == origin; апстрім `deda063` | HIGH |
| `core/smc/signals.py` | +8/−3 | **0** | SAFE DISCARD | == origin; апстрім `deda063` | HIGH |
| `core/smc/shell_composer.py` | +1/−1 | **0** | SAFE DISCARD | == origin | HIGH |
| `runtime/api_v3/__init__.py` | 0/0 | 0/0 | SAFE DISCARD | mode/CRLF only | HIGH |
| `runtime/api_v3/auth_validator.py` | 0/0 | 0/0 | SAFE DISCARD | mode/CRLF only | HIGH |
| `runtime/api_v3/kill_switch.py` | 0/0 | 0/0 | SAFE DISCARD | mode/CRLF only | HIGH |
| `runtime/ws/ws_server.py` | +3412/−3294 | **+1/−1** | SAFE DISCARD | єдиний diff = UTF-8 **BOM**+mojibake (`﻿"""`, `СЃРµСЂРІРµСЂ`); origin чистіший | HIGH |
| `runtime/api_v3/token_store.py` | +4/−2 | +5/−2 | SAFE DISCARD | working має `cowork_write` scope; origin = `{"read"}` (cowork видалено) | HIGH |
| `runtime/api_v3/endpoints.py` | +1896/−1883 | +16/−5 | SAFE DISCARD | working реєструє `/api/v3/cowork/*`; origin — ні (failed experiment) | HIGH |

> **Чому величезні `vs HEAD` цифри, але `O_w`≈0**: робоче дерево на VPS — це
> **≈origin-версії файлів, scp-задеплоєні поверх старого git HEAD**, з CRLF-line-
> endings. Тому `vs HEAD` (старий 71c4acb) — гігантський, а `vs origin` (-w) — нуль.
> VPS фактично **вже крутить майже-origin код**; git це показує як "M" лише через
> line-endings.

### 2.2 Untracked source-файли (2) — **MOVE ASIDE**

| Файл | Стан | Дія | Причина |
|---|---|---|---|
| `runtime/ws/app_keys.py` | untracked, 3914B, origin трекає **+59-рядк. superset** | **mv → backup** ПЕРЕД pull | 🔴 **COLLISION**: pull інакше abort-не. Origin-версія (`a52f0ca`) канонічна |
| `runtime/api_v3/cowork.py` | untracked, 15290B, у origin **відсутній** | mv → backup (опц.) | failed experiment; після pull лишиться orphan (нереферентний). Прибрати для чистоти |

> **cowork — глибша перевірка (D13.2, до кореня)**: origin додав (`b7cb6e3`) і
> **видалив** (`4e1de5b`, changelog 20260511-001) cowork як failed experiment.
> Live-перевірка: `GET /api/v3/cowork/published` → **404**, `…/event_flag` → **404**,
> тоді як `GET /api/v3/smc/zones` → **401** (сервер живий, auth-gated). Тобто
> **cowork і так не обслуговується в проді**. Викидання = узгодження з рішенням
> origin, не втрата. Історія збережена в git (`b7cb6e3`), якщо колись треба. `[VERIFIED]`

> **Нічого з категорії MUST-PRESERVE немає.** Єдиний реально-унікальний VPS-контент
> (cowork) — свідомо покинутий експеримент, мертвий у проді.

---

## 3. VPS-only стан + перевірка залежностей (deliverable #3)

| Об'єкт | Стан | Вплив upgrade | Дія |
|---|---|---|---|
| `config.json` | VPS == origin (`-w` порожній), 0 комітів торкаються | НЕ змінюється | нічого |
| `.env` | 828B, May 18, `-rw-------`, gitignored | pull/reset НЕ чіпають | нічого (verify ignored) |
| `data_v3/` | 410M, gitignored | pull/reset НЕ чіпають | нічого (бекап опц.) |
| `ui_v4/dist`, `node_modules`, `ui_archi/dist` | gitignored (`git check-ignore` ✓), build на VPS | **stale до rebuild** | **npm rebuild** (§4.6) |
| Disk `/` | 63G вільно (14% use) | — | вистачає на tarball |
| node / npm | **`/usr/bin/node` v20.20.0, npm є** | — | rebuild можливий на VPS ✓ |
| Python deps (`requirements*.txt`) | **0 комітів** | pip install НЕ потрібен | пропустити |
| `pyproject.toml` | +60 (`[tool.bandit]`, CI-only) | без runtime-впливу | нічого |
| Frontend deps (`package.json`+lock) | +`sharp@0.34`, +`@types/uuid` (PWA) | **npm install потрібен** | §4.6 |
| Untracked cruft (`.bak`, `dist.bak.*`×15+, `.env.bak*`) | багато, harmless | pull/reset НЕ чіпають | чистити ПО СПИСКУ окремо (НЕ `git clean`) |
| Collision sweep | **рівно 1**: `app_keys.py` | блокує pull | §4.3 |

**Supervisor (за що відповідає рестарт)** `[VERIFIED supervisorctl status]`:
```
smc:smc-ws        RUNNING  pid 936174  uptime 26d 8h   ← рестартуємо ТІЛЬКИ це
smc:smc-fxcm      RUNNING  uptime 26d   ← data feeder, НЕ чіпаємо
smc:smc-preview   RUNNING  uptime 26d   ← preview plane, НЕ чіпаємо
smc:smc-ticks     RUNNING  uptime 26d   ← tick relay, НЕ чіпаємо
smc:smc-binance*  RUNNING  uptime 37d   ← Binance ingest, НЕ чіпаємо
archi_console     RUNNING  uptime 52d   ← (можливо віддає ui_archi — див. §4.6b)
smc_trader_v3     RUNNING  uptime ~1h   ← Архі-бот (окремий repo, X31)
```
Рестарт `smc:smc-ws` **не зачіпає** data-feeders → пайплайн даних не переривається.

---

## 4. RECONCILIATION RUNBOOK (для Стаса; виконувати по черзі)

> ⚠️ **Перед стартом**: відкрий **другу SSH-сесію** до VPS і тримай до кінця §5.
> Усі команди — `cd /opt/smc-v3` якщо не вказано інше. **GO-gate**: не починай без
> власного explicit рішення; кожен 🛑 STOP-gate = зупинись і думай.

### 4.1 Pre-flight (read-only, ~1 хв)
```bash
cd /opt/smc-v3
git fetch origin -q
git rev-parse --short HEAD                         # очікувано: 71c4acb
git rev-parse --short origin/main                  # очікувано: fd6e27d (інакше — drift зріс, перечитай §1)
git merge-base --is-ancestor HEAD origin/main && echo FF-OK || echo "🛑 STOP: не FF"
git status --porcelain | grep -vE '\.bak|dist\.bak' | head   # очікувано: 11 M + 2 ?? (app_keys, cowork)
```
🛑 **STOP-gate**: якщо `FF-OK` не вивелось, АБО origin/main ≠ `fd6e27d` — зупинись,
переоціни (хтось пушив; цей runbook розрахований на лінійний FF до `fd6e27d`).

### 4.2 Backup (D2 — обов'язково)
```bash
# (a) git-тег на поточний HEAD = миттєвий rollback-якір
git tag pre-recon-20260614 71c4acb

# (b) tarball робочого стану (без node_modules і .bak — решта все: source+dist+.env+config+data_v3)
sudo mkdir -p /opt/backups
tar czf /opt/backups/smc-v3-pre-recon-$(date +%Y%m%d-%H%M).tar.gz \
    --exclude='node_modules' --exclude='*.bak*' -C /opt smc-v3
ls -lh /opt/backups/smc-v3-pre-recon-*.tar.gz       # переконайся що файл є і не 0B
```
> tarball потрібен бо `dist/` gitignored — git-rollback НЕ відновить старий build (§5).

### 4.3 Прибрати collision + orphan (ПЕРЕД pull)
```bash
# origin трекає повнішу app_keys.py → відсунь untracked VPS-копію
mv runtime/ws/app_keys.py /opt/backups/app_keys.py.vps-untracked.$(date +%s)
# cowork.py стане orphan після pull (origin його не має) → теж відсунь
mv runtime/api_v3/cowork.py /opt/backups/cowork.py.vps-untracked.$(date +%s)
# контроль: collision-sweep має бути порожнім
git ls-files --others --exclude-standard | grep -vE '\.bak|dist\.bak|/dist/|node_modules' \
  | while read f; do git ls-tree origin/main --name-only -- "$f"; done
echo "(порожньо вище = collision прибрано)"
```

### 4.4 Скинути working-tree + index mods (clean)
```bash
git config core.autocrlf false      # уникнути CRLF-churn (як на trader-v3 конверсії)
git reset --hard 71c4acb            # чистить staged (endpoints/ws_server) + working mods → clean 71c4acb
git status --porcelain | grep -vE '\.bak|dist\.bak' | head   # очікувано: майже порожньо (лишаться тільки untracked .bak)
```
> `reset --hard` НЕ чіпає gitignored (`.env`, `data_v3`, `dist`, `node_modules`) і
> untracked `.bak`. Скидає лише раніше-modified tracked файли — усі доведено
> non-precious (§2).

### 4.5 Fast-forward pull (119 → fd6e27d)
```bash
git pull --ff-only origin main
git rev-parse --short HEAD          # очікувано: fd6e27d
git log --oneline -1
```
🛑 **STOP-gate**: якщо pull НЕ ff (помилка "not possible to fast-forward" або
"untracked … would be overwritten") — зупинись, не форсуй merge. Перевір що §4.3
прибрало ВСІ collision (можливо з'явився новий untracked-source).

### 4.6 Frontend rebuild (node v20 присутній)
```bash
# (a) ui_v4 — обов'язково (PWA deps + усі frontend-зміни)
cd /opt/smc-v3/ui_v4
npm install                         # підтягне sharp (native, prebuilt для node20) + @types/uuid
npm run build                       # регенерує ui_v4/dist (PWA SW, manifest, icons, theme, drawings)
ls -la dist/ dist/assets/ | head    # переконайся що свіжий build (нова дата)
cd /opt/smc-v3
```
> Якщо `npm install` падає на `sharp` (native) — `npm install --include=optional sharp`
> або перевір `node -e "require('sharp')"`. node20 має prebuilt → зазвичай OK.
> `gen-icons` (нова `package.json` script) — dev-утиліта; іконки вже комітнуті в
> `ui_v4/public/icons/`, тому окремо запускати НЕ треба.

**(b) ui_archi — ОПЦІЙНО, підтверди в Стаса.** 12 комітів змінили `ui_archi`
(orb redesign, chat, SSE directives). Якщо `archi_console` (supervisor) віддає
збілджений `ui_archi/dist`:
```bash
cd /opt/smc-v3/ui_archi && npm install && npm run build && cd /opt/smc-v3
# і пізніше: sudo supervisorctl restart archi_console
```
🛑 Спершу з'ясуй чи ui_archi взагалі білдиться/віддається на VPS (а не локально-only).

### 4.7 Рестарт ws_server (cutover)
```bash
sudo -n supervisorctl restart smc:smc-ws
sleep 3 && sudo -n supervisorctl status smc:smc-ws    # очікувано: RUNNING
```
> Тільки `smc:smc-ws`. Data-feeders (`smc-fxcm/preview/ticks/binance`) лишаються живі.

### 4.8 D9.1 Observation window (≥120s — feature-bearing restart)
Один SSH-вхід, snapshot кожні 20s (НЕ `tail -f`):
```bash
sudo -n supervisorctl status smc:smc-ws
for i in 1 2 3 4 5 6; do echo "=== T+$((i*20))s ==="; \
  tail -n 8 /var/log/smc-v3/ws_server.stderr.log; sleep 20; done
```
🛑 **STOP-signals** → rollback (§5): `Traceback`/`ERROR`/`CRITICAL` у логах,
`status != RUNNING`, RSS росте без стелі, smoke-test (§4.9) fail.

### 4.9 Smoke-tests (на VPS, read-only)
```bash
curl -s -o /dev/null -w "status=%{http_code}\n" http://127.0.0.1:8000/api/v3/status        # 200
curl -s -o /dev/null -w "zones=%{http_code}\n" "http://127.0.0.1:8000/api/v3/smc/zones?symbol=XAU/USD&tf=M15"  # 200|401
curl -s -o /dev/null -w "manifest=%{http_code}\n" http://127.0.0.1:8000/manifest.json      # 200 (PWA, новий роут)
curl -s -o /dev/null -w "sw=%{http_code}\n" http://127.0.0.1:8000/sw.js                     # 200 (PWA)
curl -s -o /dev/null -w "cowork=%{http_code}\n" http://127.0.0.1:8000/api/v3/cowork/published # 404 (очікувано — видалено)
```

---

## 5. Verification checklist + ROLLBACK

### 5.1 Verification (перед "done")
- [ ] `git rev-parse --short HEAD` == `fd6e27d`, `git status` чистий (крім `.bak`)
- [ ] `smc:smc-ws` RUNNING ≥120s, лог без `Traceback/ERROR`
- [ ] `/api/v3/status` 200; `/api/v3/smc/zones` 200|401; `/manifest.json` + `/sw.js` 200
- [ ] Браузер (`aione-smc.com`): **графік вантажиться**, свічки тікають
- [ ] **SMC-zones рендеряться** (overlay не порожній — P1 `.low`-пастка не спрацювала)
- [ ] **Final-bars комітяться** (нові свічки закриваються, не "стрибають")
- [ ] WakeEngine тікає (якщо застосовно — `wake:events`/presence у delta_loop)
- [ ] Console браузера: **нема CSP-помилок** на WebGL/LWC (інакше — §6 nginx CSP)
- [ ] PWA: manifest валідний, SW реєструється (DevTools → Application)
- [ ] data-feeders (`smc-fxcm/preview/ticks`) досі RUNNING (не зачепили)

### 5.2 Rollback (якщо STOP-signal)
```bash
# Варіант A — git (код), якщо проблема у коді і dist ще старий:
cd /opt/smc-v3
git reset --hard pre-recon-20260614          # назад на 71c4acb
mv /opt/backups/app_keys.py.vps-untracked.* runtime/ws/app_keys.py   # повернути untracked
sudo -n supervisorctl restart smc:smc-ws

# Варіант B — повний tarball-restore (якщо rebuild зіпсував dist / стан):
sudo -n supervisorctl stop smc:smc-ws
sudo mv /opt/smc-v3 /opt/smc-v3.failed.$(date +%s)
sudo tar xzf /opt/backups/smc-v3-pre-recon-20260614-*.tar.gz -C /opt
sudo -n supervisorctl start smc:smc-ws
sudo -n supervisorctl status smc:smc-ws       # RUNNING + observe 60s
```
> ⚠️ **dist gitignored** → git-rollback (A) НЕ відновлює старий build. Якщо вже
> зробив `npm run build` — використовуй **B (tarball)** для повного відкату, або
> перебілди зі старого source (`git reset --hard 71c4acb && cd ui_v4 && npm ci && npm run build`).

---

## 6. Risk register (ранжовано)

| # | Ризик | L | I | Mitigation |
|---|---|---|---|---|
| R1 | **Collision `app_keys.py` → pull abort** | HIGH (якщо пропустити §4.3) | MED (pull падає, без шкоди) | §4.3 mv untracked перед pull; collision-sweep контроль |
| R2 | **`npm install` / `sharp` native build падає** | MED | MED (stale UI або build fail) | node20 prebuilt sharp; fallback `--include=optional`; build локально+scp dist якщо VPS-build ламається |
| R3 | **Рестарт 26-ден сервера на новіший код не бутиться** | LOW (origin тестований, крутиться локально) | HIGH (графік лежить) | backup tarball + D9.1 observe + rollback готовий; data-feeders не чіпаємо |
| R4 | **CSP/nginx блокує WebGL** (commit `0d29019`, nginx ПОЗА git) | LOW-MED | MED (графік не рендериться) | перевір console; додай `data:`/`blob:` у nginx CSP вручну якщо помилки (commit context) |
| R5 | **PWA SW stickiness** — закешований shell у юзерів | LOW-MED | MED (юзери на старому UI) | SW новий (перший install чистий); при rollback — note: SW треба unregister вручну/versioned |
| R6 | **X28 ATR/RV wire-зміна** (`6e8e8a4`) ламає парсинг frame | LOW | MED | additive поля; єдиний consumer ui_v4 rebuild-иться в тому ж коміті; verify render_frame |
| R7 | **Архі-бот залежить від `/api/v3/cowork/*`** (X31) | LOW | LOW | вже 404 у проді → бот уже без нього; підтвердь зі Стасом (окремий repo) |
| R8 | **CRLF/line-ending churn** після reset+pull | LOW | LOW (косметика) | `core.autocrlf=false` (§4.4); `.gitattributes` нормалізує |
| R9 | **Drift зріс з 14.06** (хтось пушив) | LOW | MED | §4.1 pre-flight ловить (origin/main ≠ fd6e27d → STOP) |

---

## 7. Що НЕ робити (stop-list)

- ❌ `git clean -fdx` / `-fd` — знесе gitignored `data_v3/` (410M state) і `dist/`.
  Untracked `.bak`-cruft чисти **ПО СПИСКУ** окремо, ніколи bulk.
- ❌ `git merge` / non-ff pull — лінійний FF єдиний дозволений (інакше merge-commit).
- ❌ pip install — Python deps не змінювались.
- ❌ Рестарт усієї `smc:` групи — тільки `smc:smc-ws` (data-feeders живі).
- ❌ Чіпати `config.json` / `.env` / `data_v3/` — не зачеплені upgrade-ом.
- ❌ Виконувати без backup (§4.2) і другої SSH-сесії.

---

## Appendix A — Provenance (як зібрано, усе read-only)
- `git fetch` (local + VPS), `git log/diff/show/ls-tree/cat-file/status/rev-list/merge-base` — read-only.
- VPS `ssh` read-only; `curl` GET до localhost (observation, не зміна).
- Ground-truth: VPS HEAD `71c4acb` [origin/main: behind 119], remote HTTPS
  `github.com/Std07-1/v3.git`; origin/main `fd6e27d`.
- **Жодного production-write при підготовці цього документа.**

## Appendix B — Розбіжність з оцінкою 14.06 (D13.3 — чесно)
Оцінка казала "VPS-mods upstreamed (deda063), safe-discard". **Уточнення**: для
core/smc (narrative/signals/swings/engine) — так, апстрім `deda063` ✓. Але
`endpoints.py`/`token_store.py` несуть **cowork**, якого в origin **нема** — не бо
"upstreamed", а бо origin його **видалив як failed experiment** (`4e1de5b`). Вердикт
(safe-discard) той самий, причина — інша. Plus live-cowork = **404** (і так
мертвий). Це найцінніше уточнення: розбіжність напрямку, не висновку.
