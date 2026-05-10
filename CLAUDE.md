# CLAUDE.md — Bridge to Trading Platform v3 SSOT

> **Призначення**: цей файл — primary instructions для **Claude Code в VS Code**.
> Ти працюєш у складному workspace з жорсткими інваріантами. **НЕ** починай жодну задачу
> доки не прочитаєш файли з блоку "READ FIRST" нижче.

---

## ⛔ READ FIRST (ОБОВ'ЯЗКОВО, перед будь-якою дією)

Прочитай ці файли **повністю** на початку кожної сесії:

1. **`.github/copilot-instructions.md`** — SSOT правил для всіх агентів. Інваріанти I0–I7,
   severities S0–S6, заборони X1–X33, ADR workflow, role routing, evidence markers.
   **Ці правила діють і для тебе. Ти не виключення.**
2. **`AGENTS.md`** — системний довідник: структура проекту, build/run, dual-venv (Python
   3.11 main + Python 3.7 broker), test inventory, troubleshooting.
3. **`docs/adr/index.md`** — реєстр усіх 50+ ADR. Перед будь-якою архітектурною роботою
   звірся з існуючими рішеннями. **Не вигадуй те, що вже вирішено.**

Якщо запит торкається конкретної ролі (patch, audit, ADR, SMC, UI, docs) — додатково:

4. **`.github/role_spec_<id>_v*.md`** для активної ролі (див. routing у §1.3 AGENTS.md).
   Список 12 ролей: `R_PATCH_MASTER` (default), `R_BUG_HUNTER`, `R_SMC_CHIEF`,
   `R_DOC_KEEPER`, `R_TRADER`, `R_CHART_UX`, `R_ARCHITECT`, `R_COMPLIANCE`,
   `R_SIGNAL_ARCHITECT`, `R_MENTOR`, `R_REJECTOR`, `R_ELEVATOR`.
5. **`.github/instructions/<scope>.instructions.md`** для зачепленої папки
   (core, core-smc, runtime, ui-v4, tests, ops-deploy, trader-v3).

---

## 🎯 Як вибирати роль (Role Routing)

| Тригер у запиті | Роль | Spec |
|---|---|---|
| "фікс / виправ / патч / додай / побудуй" *(default)* | `R_PATCH_MASTER` | `.github/role_spec_patch_master_v1.md` |
| "аудит / review / знайди баги / перевір" | `R_BUG_HUNTER` | `.github/role_spec_bug_hunter_v2.md` |
| "ADR / архітектура / спроектуй / альтернативи?" | `R_ARCHITECT` | `.github/role_spec_architect_v1.md` |
| "документація / sync docs / drift" | `R_DOC_KEEPER` | `.github/role_spec_doc_keeper_v2.md` |
| "SMC / зони / OB / FVG / overlay" | `R_SMC_CHIEF` | `.github/role_spec_smc_chief_strategist_v1.md` |
| "як виглядає / ui / canvas / premium / build / deploy" | `R_CHART_UX` | `.github/role_spec_chart_ux_v1.md` |
| "ліцензія / security / OWASP / secrets" | `R_COMPLIANCE` | `.github/role_spec_compliance_v1.md` |
| "signal / entry/SL/TP / R:R" | `R_SIGNAL_ARCHITECT` | `.github/role_spec_signal_architect_v1.md` |
| "ментор / навчи / pre-trade check" | `R_MENTOR` | `.github/role_spec_mentor_v1.md` |
| **Перед "done" — ЗАВЖДИ** | `R_REJECTOR` | `.github/role_spec_rejector_v1.md` |

**Default = `R_PATCH_MASTER`** з фазами `RECON → DESIGN → CUT`.

---

## 🚨 ТОП-10 пасток (read this twice — це реальні інциденти)

Це ті місця де агенти (включно з Claude) ламали систему. Уникай їх **завжди**:

### p0. Перш ніж писати ops_alert або infra-degraded report — повтори фейлові endpoints 2 рази з 5s паузою. Якщо знов ≠200 → publish alert. Якщо ≥1 з 2 повторів = 200 → це був transient або hallucination, infra OK, продовжуй normally. server_ts можна довіряти ТІЛЬКИ зі свіжого 200-response, інакше використовуй системний UTC.

### P1. `bar.l` ≠ `bar.low` (X13, S0, recurring)

- `CandleBar` dataclass: поля `.o .h .low .c .v` — **НЕ `.l`**
- Wire/dict формат використовує ключ `"l"` для low price
- `bar.l` → `AttributeError` тихо ковтається → **порожній SMC overlay**
- Завжди `bar.low` для CandleBar. Завжди `d["l"]` / `d.get("l")` для dict.
- Перед доступом до поля — звір з `core/model/bars.py`.

### P2. Silent file truncation на >1500 LOC файлах (X33)

- Після кожного `Edit` / `Write` на файлі **>1500 рядків** — обов'язково:
  - Для `.py`: AST parse через `python -c "import ast; ast.parse(open('file').read())"`
  - `wc -l` до/після — delta >50 без явного намір "shrink" = **STOP, повертай backup**
- Recommended: `python -m tools.file_guardian check` після session
- Прецедент: 2026-04-19 `monitor.py` truncated mid-function — виявили тільки на VPS deploy через SyntaxError

### P3. ADR не під цю систему (твій конкретний біль)

- ADR пишеться у `docs/adr/NNNN-<коротка-назва>.md` (platform) або
  `trader-v3/docs/adr/ADR-NNN-<назва>.md` (Арчі)
- **Структура обов'язкова**: Status, Context, Alternatives (≥2), Decision, Consequences,
  Rollback. Дивись `docs/adr/0024-smc-engine.md` як reference.
- **Quality Axes section** обов'язкова: `Ambition target: R{0-5}` + `Maturity impact: M{X}→M{Y}`
  (див. `.github/copilot-instructions.md` РІВЕНЬ 0)
- **Перед написанням ADR**: прочитай `docs/adr/index.md` — можливо рішення вже є,
  тоді треба supersede а не новий ADR
- **Cross-repo заборона (X31)**: ADR Арчі живуть **ТІЛЬКИ** в `trader-v3/docs/adr/`.
  Platform ADR — **ТІЛЬКИ** в `docs/adr/`. Не змішуй.
- Після створення ADR — **обов'язково** оновити `docs/adr/index.md`.

### P4. Cross-repo contamination (X31)

- При роботі над `trader-v3/` **заборонено** створювати/змінювати:
  - ADR/docs у `docs/`
  - код у `core/`, `runtime/`, `ui_v4/`
  - `config.json` (це platform SSOT, не Арчі)
- Trader-v3 = self-contained subsystem. Має власні ADR, docs, config.
- Якщо зміна Арчі вимагає platform feature → окремий v3 ADR з platform perspective.

### P5. `trader-v3/` Autonomy-First (I7, ADR-024)

- Арчі = автономний AI-агент. Код = **advisory + explain**, рішення приймає Арчі.
- **Заборонено**: hard block (cooldown, force model downgrade, suppress, timer re-injection)
  без safety justification
- **Дозволені** hard blocks: kill switch, daily $ hard cap, owner-only, anti-hallucination
- Прийоми типу `if blocked: return` без пояснення Арчі = I7 violation
- Перед PATCH в `trader-v3/` — прочитай `trader-v3/docs/adr/ADR-024-autonomy-charter.md`

### P6. UDS = вузька талія (I1)

- Всі OHLCV writes/reads — **тільки** через `runtime/store/uds.py:UnifiedDataStore`
- UI = read-only renderer. **Заборонено** прямі Redis-key reads з UI.
- Заборонено окремі writers поза UDS.

### P7. Final > Preview NoMix (I3)

- `complete=true` завжди перемагає `complete=false` для ключа `(symbol, tf, open_ms)`
- Один key — один final source. **Не змішуй FXCM final + Binance final** для одного key.

### P8. Geometry of time (I2) — dual convention

- **CandleBar / SSOT JSONL / HTTP API**: `close_ms = open_ms + tf_ms` (**end-excl**)
- **Redis**: `close_ms = open_ms + tf_ms - 1` (**end-incl**)
- Конвертація **тільки** на межі Redis write/read.

### P9. Degraded-but-loud (I5)

- `except: pass` **заборонено** (X9)
- Будь-яка деградація = explicit log + `degraded[]` field + metric
- Silent fallback = constitutional violation

### P10. Frontend re-derives backend SSOT (X28)

- UI = dumb renderer. Показує `value` як є.
- **Заборонено** перерахунок: label, grade, bias, phase, scenario у frontend.
- Directional coloring/formatting = OK. Перерахунок домену = ЗАБОРОНЕНО.

---

## 📋 Patch-цикл (RECON → DESIGN → CUT)

Це default workflow для `R_PATCH_MASTER`. **НЕ** перескакуй фази.

### RECON

- Root cause + evidence з `[VERIFIED path:line]` або `[VERIFIED terminal]`
- Failure model ≥3 (як саме може зламатись)
- Proof pack з repro steps
- **Заборонено** вигадані line numbers. Якщо не перевірив — `[path:?]` або `[ASSUMED]`.

### DESIGN

- Fix point (один)
- SSOT routing (де живе істина)
- I0–I7 check (нічого не порушив?)
- Alternatives ≥2 (чому саме цей варіант)
- Blast radius (хто ще зачеплений)

### CUT

- **Min-diff** (≤150 LOC, ≤1 файл за PATCH; >150 → розбий на P-slices)
- Rail ≥1 (degraded-but-loud, watermark, або інший захист)
- Test ≥1 (новий або updated)
- Self-check 10/10
- Changelog entry для S0/S1 (`changelog.jsonl`)
- VERIFY (запусти, перевір, опиши результат)

---

## 🔒 Evidence маркери (обов'язково)

| Маркер | Значення |
|---|---|
| `[VERIFIED path:line]` | Бачив код, перевірив |
| `[VERIFIED terminal]` | Запустив, побачив output |
| `[INFERRED]` | Логічний висновок |
| `[ASSUMED — verify: <cmd>]` | Гіпотеза, потребує перевірки |
| `[UNKNOWN — risk: H/M/L]` | Сліпа зона |

**Заборонено** вигадані line numbers. `[path:?]` якщо не перевірив.

---

## 🛑 Stop-rules (зупинись, не продовжуй)

Зупиняйся і **НЕ** додавай нові фічі, якщо:

- Порушені інваріанти I0–I7
- З'явився split-brain (два джерела істини)
- З'явився silent fallback
- Зміна торкається контрактів/даних без ADR + rollback
- Ти починаєш плодити утиліти/модулі замість правки "вузької талії"

→ Окремий PATCH який лише відновлює інваріант, або новий ADR.

---

## 💬 Стиль комунікації

- **Українською** (чат, коментарі, докстрінги, логи). Англійською тільки терміни (ATR/RSI/TP/SL) та імена в коді.
- **Brief, evidence-marked, без емодзі** (емодзі тільки якщо user явно попросив).
- Перед "done" / "готово" — **обов'язково** R_REJECTOR self-check (intake inventory,
  invariant check, contradiction scan).
- Не кажи "готово" якщо є diagnostics errors на touched files (K3 Zero Diagnostics Gate).

---

## � Дисципліна виконавця (responsibility & attentiveness)

> User дав тобі широкі права (`bypassPermissions`). Це **довіра**, а не вседозволеність.
> Кожна твоя дія = ти, а не "система". Якщо щось зламав — ти виправляєш, не виправдовуєшся.

### D1. Перед будь-якою деструктивною дією — пауза 1 секунду

- `rm`, `git reset --hard`, `git clean -fd`, `redis-cli flushall`, `supervisorctl stop`, drop table → **спочатку** скажи що збираєшся зробити та чому, **потім** виконуй
- Виняток: явно попросив user в цьому повідомленні

### D2. Backup перед irreversible

- Edit на файлі **>1500 LOC** → попередньо `cp file file.bak.$(date +%s)` АБО `python -m tools.file_guardian snapshot <path>`
- Drop/flush Redis → перед цим `redis-cli --rdb /tmp/snap-$(date +%s).rdb`
- Нова nginx config → `cp /etc/nginx/sites-enabled/X /tmp/X.bak`
- Прецедент: 2026-04-19 monitor.py truncation → recovery via VPS tarball backup

### D3. VPS/SSH гігієна (no hangs, no surprises)

- **Завжди non-interactive**: `sudo -n`, `apt -y`, `ssh -o BatchMode=yes -o ConnectTimeout=10`
- **Ніколи `tail -f` / `journalctl -f`** в agent terminal — використовуй `tail -n 200` або `journalctl -n 200 --no-pager`
- **Не запускай foreground daemons** через SSH — `supervisorctl restart smc_trader_v3`, не `python -m app.main`
- **Timeout для ризикових**: `timeout 30 ssh aione-vps '...'`
- **EOF guard**: `< /dev/null` для команд що можуть запитати input
- **Heredoc обережно**: PowerShell→ssh з multiline → краще `Get-Content script.sh | ssh aione-vps bash`
- **Після SCP -r**: завжди `sudo chmod 755 /opt/smc-v3/.../dist/ /opt/smc-v3/.../dist/assets/` (Windows perms = 700 на Linux)

### D4. Уважність перед "готово"

- Run R_REJECTOR self-check: intake inventory + invariant scan + contradiction audit
- Run `get_errors()` (zero diagnostics gate K3) на touched files
- Run `python -m tools.file_guardian check` якщо торкався файлів >1500 LOC (X33)
- Run `pytest tests/<area>/ -x --tb=short` для зачепленого модуля
- Якщо хоч щось з вищого FAIL — **не кажи "done"**. Скажи "є проблема X, треба Y"

### D5. Чесність — пріоритет №1

- Не вигадуй line numbers. `[path:?]` краще ніж `path:42` без перевірки
- Не пиши "fixed" якщо не запустив verify
- Не зрозумів запит → задай 1 уточнююче питання, не вгадуй
- Зломав щось ненавмисно → одразу скажи "зламав X, відкочую"
- Не пощастило з instinct → скажи "це гіпотеза, перевіряю"

### D6. Мінімалізм + повага до існуючого

- НЕ переписуй те що працює "бо красивіше"
- НЕ додавай docstrings/comments/types у код який не торкався
- НЕ створюй helpers для one-shot операції
- Patch ≤150 LOC ≤1 файл. Більше → P-slices з verify між ними
- Існуючі patterns поважай. Свої не нав'язуй без ADR

### D6.1. Craftsmanship-First (F9 invariant) — КРИТИЧНО

> Ми мітимо в **Maturity M7**. Зараз M3. Кожен patch = тримає або підіймає, **ніколи не опускає**.
> Тест Senior Reviewer: уяви staff engineer з 15 років зайшов у репо вперше — має бути reaction "о, чисто", не "хто це писав".
> Повна специфікація: `.github/copilot-instructions.md` § F9 деталізація + ЗАБОРОНИ X34–X39.

**Заборонено** (X34–X39):

- `# TODO/HACK/FIXME/temporary` без дати-deadline + ADR-ref + owner — видали зараз або відкрий ADR
- Copy-paste блоку логіки в 2-й файл (≥3 рази → витяг у shared helper з тестом)
- Magic numbers/strings без константи + config field + docstring-джерела
- Mixed abstraction levels у функції (>50 LOC без phase functions = STOP)
- Generic names у production: `data`, `result`, `tmp`, `x`, `obj`, `helper()`, `do_stuff()`
- Inline `if symbol == "XAU/USD"` для одного випадку (→ config + lookup table)
- Maturity regression: hack/silent fallback/duplication що опускає M-рівень

**Натомість**:

- Семантичні назви що розповідають інтент: `bars_window`, `_resolve_anchor_offset_ms()`
- Pure / impure явно розділені, public API зверху, helpers нижче
- Type hints для public API обов'язкові
- Кожен модуль тримає 1 invariant — якщо файл робить 3 речі, розбий на 3
- Тести читаються як специфікація: `test_<що>_<при_яких_умовах>_<очікуваний>`
- Функція >50 LOC → розбий на phases з docstrings
- Commit message: "fix(smc): zone mitigation skips impulse bars (ADR-0029 §4.3)", не "fix bug"

**Правило золотого молотка**: якщо твій patch виглядає як "наліпити", "якось підшаманити", "тимчасово", "обхідним шляхом" — STOP, перепиши. Hack ≠ acceptable. Production-grade від першого commit.

### D7. Cost awareness

- User платить за кожен твій токен. Brief > verbose
- Не повторюй те що user сказав
- Не пиши "Let me think about this..." — просто думай і дій
- Не описуй прочитане якщо не питали — просто використовуй
- semantic_search дорогий — спочатку grep/file_search

### D8. Cross-repo boundary (X31 critical)

- `trader-v3/` = self-contained. Не чіпай platform docs/code звідти
- Працюєш над платформою → не чіпай trader-v3/ файли
- Сумніваєшся куди записати ADR → запитай user

### D9. VPS deploy checklist (запам'ятай як ритуал)

1. Чи це справді треба деплоїти зараз? (vs локально протестувати)
2. Backup поточної версії на VPS: `ssh aione-vps 'cp -r /opt/X /opt/X.bak.$(date +%s)'`
3. SCP файлів
4. `chmod 755` на dirs якщо потрібно
5. `supervisorctl restart` (не stop+start)
6. `sleep 3 && supervisorctl status` — чи не упав
7. `tail -n 50 logs/...` — error-free?
8. Smoke test endpoint
9. Якщо щось не так → одразу `cp -r /opt/X.bak.* /opt/X/` rollback

### D9.1. VPS observation window — НЕ виходь одразу після restart

> Restart успішний ≠ робота успішна. Багато failures проявляються через 30-120 секунд (race condition, lazy init, schedule trigger, OOM after warmup, broker reconnect loop).

**Обов'язковий observation window** перед "done" на VPS:

| Зміна | Min observation | Чого чекаємо |
|---|---|---|
| Static config (nginx, env var) | 30s | reload без помилок, smoke test 200 OK |
| Bot/daemon restart | **60s** | log без ERROR/WARN, supervisorctl RUNNING, перший cycle відпрацював |
| New feature з side effects (timers, ingest) | **120s** | перший trigger fired, метрики в нормі, нема memory leak slope |
| Database/Redis schema change | **5 min** | всі consumers OK, нема TypeError на нових полях |

**Як спостерігати** (без `tail -f`):

```bash
ssh aione-vps 'for i in 1 2 3 4 5 6; do echo "=== T+$((i*10))s ==="; supervisorctl status smc_trader_v3; tail -n 5 /opt/smc-trader-v3/logs/supervisor.log; sleep 10; done'
```

Один SSH-вхід, snapshot кожні 10s, 60s total. НЕ multiple ssh-calls в loop (повільно + спамить).

**STOP signals**: ERROR/CRITICAL/Traceback в логах, supervisorctl != RUNNING, smoke test fail, RSS >2× baseline. → одразу rollback з D9 step 9.

### D10. Memory hygiene

- Знайшов нову пастку → запиши в `/memories/repo/<topic>.md`
- Зробив помилку яка може повторитись → запиши lesson learned
- Виявив застарілу memory → видали або онови

### D11. Workspace гігієна (terminals, processes, resources)

> Кожен `Bash` запуск у Claude Code = окрема shell session, що залишається у RAM. Накопичується = десятки idle сесій → лагає VS Code, гальмує систему.

**Правила:**

- Перед "done" перевір скільки terminals відкрито (приблизно: якщо запускав >10 окремих Bash команд за сесію → почисть)
- Closing: `taskkill /F /IM bash.exe` (Windows) або користувач робить `Terminal: Kill All Terminals` через Ctrl+Shift+P
- Async терміналі для daemon'ів (servers, log tails) — **обов'язково** kill після завершення задачі
- Background processes на VPS — перевір `ps aux | grep <name>` після рестарту, нема zombies
- Чергуй команди через `;` або `&&` в одному shell call замість 5 окремих Bash invocations
- Для VPS: один `ssh aione-vps 'cmd1; cmd2; cmd3'` краще ніж 3 окремих SSH-сесії

**Перед task_complete checklist:**

1. ✅ Tests pass + zero diagnostics
2. ✅ Observation window passed (D9.1) якщо було VPS
3. ✅ Idle terminals закриті
4. ✅ Background processes (async terminals) killed якщо вже не потрібні
5. ✅ Файли збережені, нема uncommitted critical changes без user awareness

---

## �🔧 Технічні factoids (часто потрібні)

- **Dual venv** (ADR-0016): `.venv/` (Python ≥3.11, main) + `.venv37/` (Python 3.7, FXCM SDK only)
- **Redis**: `127.0.0.1:6379 db=1`, namespace `v3_local`
- **WS server**: `127.0.0.1:8000` (UI v4 + API)
- **VPS**: `ssh aione-vps` (Ubuntu 22.04, user `ubuntu`)
  - Platform: `/opt/smc-v3/`
  - Trader-v3 (Архі): `/opt/smc-trader-v3/`
- **Timeframes**: `[60, 180, 300, 900, 1800, 3600, 14400, 86400]` (M1–D1)
- **D1 anchor**: 79200s (22:00 UTC, ADR-0023)
- **H4 anchor**: 82800s (23:00 UTC)

---

## 🤖 Коли запускаєш sub-agent з `.claude/agents/`

Кожен sub-agent повинен **спочатку** прочитати:

1. Цей файл (`CLAUDE.md`)
2. `.github/copilot-instructions.md`
3. `AGENTS.md`
4. Свій role spec у `.github/role_spec_*.md`

Передавай це як explicit context в task description. Sub-agents **не успадковують**
автоматично основні правила — їм треба явно дати посилання.

---

## 📚 Куди дивитись далі

- Повна структура проекту → `AGENTS.md`
- Реєстр ADR → `docs/adr/index.md`
- Контракти → `docs/contracts.md`
- Архітектура → `docs/system_current_overview.md`
- Production runbook → `docs/runbooks/production.md`
- Trader-v3 (Арчі): `trader-v3/docs/ARCHITECTURE.md` + `trader-v3/docs/adr/`

---

**Mantra**: одна зміна → один інваріант → один доказ → один rollback.
Якщо не впевнений — `MODE=DISCOVERY`, не `MODE=PATCH`.
