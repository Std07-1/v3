# ADR-0061 — VPS Reconciliation 2026-05-05 (Force-Local Sync to origin/main)

- **Status**: Implemented
- **Date**: 2026-05-05
- **Authors**: vikto + Copilot
- **Initiative**: `deploy_discipline_v1`
- **Related**: [ADR-0060](0060-deploy-discipline-vps-catchup.md) (Deploy Discipline), [ADR-0058](0058-public-readonly-api-auth.md) (deploy gate)

---

## Quality Axes

- **Ambition target**: R3 — execute documented playbook, eliminate split-brain, restore deploy invariants.
- **Maturity impact**: M3 (consolidates discipline; precondition for M3→M4 in ADR-0060).

---

## 1. Контекст

**Проблема**: VPS `/opt/smc-v3/` (production) розійшовся з `origin/main` GitHub:

- VPS `HEAD` = `a522f51` (Apr 6, 2026) — **30+ комітів позаду** GitHub `9e767f0` (May 4, 2026).
- VPS dirty tree: **8 modified** + **8 untracked** Python файлів.
- Локальна dev-машина одночасно має чисте дерево, синхронізоване з `origin/main`.

Це порушення **F1 (SSOT)** + ризик **I1 (UDS вузька талія)**: код, який запускається в продакшені, не співпадає з кодом у Git → неможливо відтворити, неможливо безпечно catch-up.

**Тригер сесії**: ADR-0060 ("Deploy Discipline") у статусі Proposed визначив 2-фазний rollout. Ця ADR — Phase 1 ("Reset to known good state") в `[deploy_discipline_v1]`.

---

## 2. Дослідження (Provenance Check)

Перш ніж force-overwrite VPS, провели **provenance investigation** — чи містять VPS-модифіковані файли унікальні правки, яких нема в Git історії?

- 6 modified files звірені з останніми **300 commits** локального репо (PowerShell скрипт `_provenance.ps1`)
- Результат: **0/6 файлів матчили хоча б один commit** з останніх 300

**Висновок**: VPS файли — застарілий snapshot з якогось проміжного стану розробки, не унікальні manual fixes на проді. Force-local **безпечний**.

| File | VPS modification size | Git provenance |
|---|---|---|
| `runtime/ingest/broker_sidecar.py` | +22/-65 | 0/300 commits matched |
| `runtime/ingest/broker/fxcm/fxcm_provider.py` | +1/-5 (cosmetic) | 0/300 |
| `core/smc/engine.py` | -5 (removed `get_bars` accessor) | 0/300 |
| `config.json` | -1 (no `analysis_enabled` flag) | 0/300 |
| `runtime/ws/ws_server.py` | +1/-18 (no kill switch) | 0/300 |
| `core/smc/structure.py` | +35/-67 (no ADR-0047 V2) | 0/300 |

VPS untracked: 5 `.py` (`auto_wake`, `wake_check`, `wake_types`, `wake_engine`, `narrative_enricher`) + dirs `api_v3/`, `tools/`, `ui_archi/` — частково вже інтегровані в `origin/main`, частково — orphaned експерименти.

---

## 3. Альтернативи

| # | Підхід | Pros | Cons | Verdict |
|---|---|---|---|---|
| **A. Force-local (вибрано)** | `git reset --hard origin/main && git clean -fd` | Чисте дерево, відтворюваність, мінімум операцій | Втрата VPS-only змін (але provenance підтвердив — їх нема цінних) | ✅ |
| B. Cherry-pick VPS diffs | Зберегти "корисне" з VPS, поверх pull нового | Збереження потенційно цінних правок | 0/300 match → нема що зберігати; додає годин роботи | ❌ |
| C. Зробити VPS новим origin | Push з VPS у новий бренч, merge | Зберігає історію VPS правок | Легалізує split-brain, плодить branch hell | ❌ |
| D. Wipe + fresh clone | `rm -rf /opt/smc-v3 && git clone` | Найчистіше | Втрачає `.env`, `data_v3/`, `.venv*`, supervisor PIDs — потребує re-bootstrap | ❌ |

**Decision**: A (Force-local) з захищеними gitignored директоріями (`.env*`, `/data_v3/`, `logs/`, `.venv*`).

---

## 4. Рішення (Playbook Executed)

7-крокова послідовність, виконана 2026-05-05 ~07:25–07:35 UTC:

| # | Крок | Деталі | Артефакт |
|---|---|---|---|
| 1 | **Preflight snapshot** | `git status`, `git log -1`, `du -sh data_v3` на VPS | `reports/vps_state_2026_05_04/_preflight*.sh` |
| 2 | **Tar backup** | `sudo tar czf /opt/backups/smc-v3-pre-reset-20260505-072801.tar.gz /opt/smc-v3` | 13MB tarball, retention 7 days |
| 3 | **Verify gitignore safety** | `.env`, `data_v3/`, `logs/`, `.venv*` усі untracked в Git → не зачіпаються `git clean` | confirmed |
| 4 | **Stop workers** | `sudo supervisorctl stop smc:*` (6 воркерів) | confirmed STOPPED |
| 5 | **Reset + clean** | `git fetch origin && git reset --hard origin/main && git clean -fd` | HEAD = `9e767f0` |
| 6 | **Pip install** | `.venv/bin/pip install -r requirements.txt` | All "Already satisfied" — no version drift |
| 7 | **Start + 60s observe** | `sudo supervisorctl start smc:*` → 4 snapshots по 15s | All 6 RUNNING, 0 errors, WakeEngine fires, WS clients live |

**Bonus step**: Sync VPS `data_v3/` → local dev machine (на запит owner-а, для нічної offline розробки):

- VPS: `tar czf /tmp/data_v3_vps_20260505.tar.gz data_v3/` → 30MB compressed (з 342MB)
- SCP → local `reports/vps_state_2026_05_04/`
- Local: `Rename-Item data_v3 data_v3.local_pre_sync_<ts>` (688MB old) → `tar xzf` (30046 файлів, 235MB на NTFS)

---

## 5. Що зроблено / Що НЕ зроблено

### Зроблено

- VPS `HEAD` синхронізовано з GitHub `origin/main` (`a522f51` → `9e767f0`)
- 8 modified + 8 untracked файлів видалено / приведено до канонічного стану
- 6 supervisor worker'ів перезапущено, **60s observation passed** (D9.1 rule)
- Tar backup VPS збережено у `/opt/backups/` (rollback option)
- `data_v3/` (342MB) перенесено VPS → local; old local data збережено як `data_v3.local_pre_sync_<ts>`

### НЕ зроблено (свідомо)

- `.venv37/` (Python 3.7 broker SDK) — **не оновлювався** (broker SDK stable, не потребує)
- `requirements-broker.txt` — **не змінювався** в останніх 30 commits → re-install не потрібен
- `smc_trader_v3` (Архі) — **НЕ ЧІПАЛИ** (X31 boundary, owner-managed, STOPPED Apr 29)
- Pip upgrade `26.0.1 → 26.1` — informational, не виконано (не дотично до задачі)

---

## 6. Наслідки

### Позитивні

- Eliminate split-brain risk: VPS = Git = Local (one source of truth)
- Restore deploy invariants для подальшого ADR-0060 Phase 2
- Готовність до запуску платформи локально (offline dev nights)
- Tar backup у `/opt/backups/` як safety net на 7 днів

### Негативні / Risks

- VPS зміни (якщо колись були цінні) втрачені — але provenance check 0/300 → ризик мінімальний
- Future regression: якщо хтось знову `git commit` на VPS — split-brain повториться → ADR-0060 Phase 2 зобовʼязаний імплементувати **drift detector + heartbeat**

### Migration / Cleanup

- `reports/vps_state_2026_05_04/` тримати як audit trail (untracked у Git, проте не видаляти)
- VPS `/opt/backups/smc-v3-pre-reset-20260505-072801.tar.gz` — auto-delete після 7 днів (cron)
- Local `data_v3.local_pre_sync_<ts>/` (688MB) — owner вирішує коли видалити після перевірки

---

## 7. Rollback

Якщо протягом тижня виявлять регресію:

```bash
ssh aione-vps
sudo supervisorctl stop smc:*
sudo tar xzf /opt/backups/smc-v3-pre-reset-20260505-072801.tar.gz -C /
sudo supervisorctl start smc:*
sudo supervisorctl status smc:*
```

Local data_v3 rollback:

```powershell
Remove-Item data_v3 -Recurse -Force
Rename-Item data_v3.local_pre_sync_20260505-093317 data_v3
```

---

## 8. Verification Evidence

- `[VERIFIED terminal]` — VPS HEAD: `git rev-parse HEAD` → `9e767f0c...` (matches origin/main)
- `[VERIFIED terminal]` — `supervisorctl status smc:*` → 6× RUNNING, uptime monotonic
- `[VERIFIED terminal]` — `tail -n 5 /var/log/smc-v3/ws_server.stderr.log` → WakeEngine fires for XAU/XAG/BTC/ETH, WS_SWITCH events normal
- `[VERIFIED terminal]` — `journalctl -u supervisor --since "30 seconds ago" | grep -iE "error|traceback"` → empty
- `[VERIFIED terminal]` — Local `Get-ChildItem data_v3 -Recurse -File | Measure-Object` → 30046 files (matches VPS `find data_v3 -type f | wc -l`)

---

## 9. Cross-References

- **ADR-0060** §Phase 1 — цей ADR є виконанням Phase 1 ("Reset to known good state")
- **ADR-0058** — public API deploy gate (наступний крок після discipline відновлено)
- **CLAUDE.md** §D9, §D9.1 — VPS deploy checklist + observation window (обидва дотримані)
- **`/memories/repo/dst-config.md`** — будуть оновлені нотатки про SCP perms trap (вже відомо)

---

## 10. Тригер для майбутніх ADR

ADR-0060 Phase 2 (drift detector + heartbeat alerting) тепер **критично потрібен** —
без нього будь-яка нова hotfix-сесія на VPS повторить цю ситуацію за 1-2 тижні.
