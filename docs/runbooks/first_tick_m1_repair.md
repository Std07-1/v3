# Runbook: ремонт open/high/low M1 з FXCM FIRST_TICK (ADR-0096 §3.3 B)

> Інструмент: `python -m tools.repair.first_tick_m1 <fetch|plan|apply|verify|rollback>`.
> Кожен крок, що пише в `data_v3` на проді, — окреме «го» власника. Усе — від користувача `smc`.

## 0. Передумови

- Слайс A (`fix/fxcm-first-tick`) задеплоєний: інакше live продовжує писати PREVIOUS_CLOSE, і ремонт історії
  змішає епохи назад.
- «Го» власника на вікно B (вихідні: рейка ринку інакше не пустить).
- Каталоги поза `data_v3` і поза репо: `/opt/smc-v3-ft/{staging,sdk,plans,copy,work}`; запуск fetch — з cwd
  `/opt/smc-v3-ft` (у cwd репо живе кеш SDK live-сайдкара `History/`, інструмент відмовить `FT_FETCH_CWD_IS_REPO`).

## 1. Фази і коди виходу

| Фаза | Середовище | Пише | rc |
|---|---|---|---|
| fetch | `.venv37`, cwd поза репо | лише staging | 0 усе ок · 1 є невдалі доби · 2 відмова до виклику · 3 зупинка рейкою |
| plan | `.venv` | лише plan_dir | 0 план · 1 план, є відмовлені part-файли · 2 плану немає |
| apply | `.venv` | part-файли (rewrite_atomic) | 0 ок · 1 розбіжність/звірка (частково) · 2 відмова до запису · 3 записувачі/ринок |
| verify | `.venv` | лише work_dir | 0 порушень немає · 1 порушення · 2 вхідна помилка |
| rollback | `.venv` | part-файли з бекапів | як apply |

Категорії плану: `REPLACE` (записується), `SAME`, `SKIP_BAKED` (open поза [low, high], ланцюжок o == prev_c з
таким рядком, або доба з часткою o == prev_c ≥ 0.9), `SKIP_CLOSE_MISMATCH`, `SKIP_RANGE_EXPANDS`,
`SKIP_FLAT_NON_TRADING`, `SKIP_WINNER_INELIGIBLE`, `MISSING_IN_STAGING`, `EXTRA_IN_STAGING` (ніколи не пишеться).

## 2. Пілот (одна минула доба XAU у вихідні)

```bash
cd /opt/smc-v3-ft
PYTHONPATH=/opt/smc-v3 /opt/smc-v3/.venv37/bin/python -m tools.repair.first_tick_m1 fetch \
  --symbol XAU/USD --from 2026-07-26 --to 2026-07-26 \
  --staging-root /opt/smc-v3-ft/staging --sdk-cwd /opt/smc-v3-ft/sdk --max-calls 1
cd /opt/smc-v3 && .venv/bin/python -m tools.repair.first_tick_m1 plan \
  --symbol XAU/USD --from 2026-07-26 --to 2026-07-26 \
  --staging-root /opt/smc-v3-ft/staging --plan-dir /opt/smc-v3-ft/plans/xau-20260726
```

Очікування (будь-яке відхилення — стоп і розбір до кореня, D13.2):
- `MISSING_IN_STAGING≈0`, `EXTRA_IN_STAGING≈0` — межі запиту SDK покривають добу повністю;
- `SKIP_CLOSE_MISMATCH≈0`, `SKIP_RANGE_EXPANDS≈0` — FIRST_TICK і PREV — ті самі бари;
- `SKIP_BAKED=0` для доби, старшої за тиждень; `REPLACE ≫ 0`;
- ключ 26.07 22:01 у `entries/XAU_USD/part-20260726.jsonl`: `new.o 4089.98`, `new.low 4086.33`;
- `v_differs=0` (обсяг не замінюється; ненульове значення — ознака іншої версії даних, розібрати до apply);
- `refused_files=0`: якщо на проді знайдуться CRLF/неканонічні part-файли — окремий нормалізаційний патч, не B.

Частота логінів FXCM не виміряна: пілот розширювати `--max-calls 1 → 5 → 30`; будь-який `exit 11` поспіль — стоп.

## 3. Доказ на копії

```bash
mkdir -p /opt/smc-v3-ft/copy/XAU_USD/tf_60
cp -a /opt/smc-v3/data_v3/XAU_USD/tf_60/part-20260726.jsonl /opt/smc-v3-ft/copy/XAU_USD/tf_60/
PLAN_SHA=$(sha256sum /opt/smc-v3-ft/plans/xau-20260726/PLAN.json | cut -d' ' -f1)
.venv/bin/python -m tools.repair.first_tick_m1 apply --copy --data-root /opt/smc-v3-ft/copy \
  --plan-dir /opt/smc-v3-ft/plans/xau-20260726 --expect-plan-sha "$PLAN_SHA" \
  --staging-root /opt/smc-v3-ft/staging --manifest-out /opt/smc-v3-ft/plans/xau-20260726-copy.json
.venv/bin/python -m tools.repair.first_tick_m1 verify \
  --apply-manifest /opt/smc-v3-ft/plans/xau-20260726-copy.json --work-dir /opt/smc-v3-ft/work/copy-20260726
```

Копія мусить містити РІВНО ті part-файли діапазону, що й прод (план звʼязаний sha кожного, і відсутніх теж).
verify rc=0 обовʼязковий.

## 4. Прод

1. Бекап цільових файлів: `tar -czf /opt/smc-v3-ft/tar-xau-20260726.tgz -C /opt/smc-v3/data_v3 XAU_USD/tf_60/part-20260726.jsonl`
   і `sha256sum` поруч.
2. `sudo -n supervisorctl stop smc:smc-fxcm smc:smc-preview` — з префіксом `smc:` (без нього мовчки не
   зупиняє; apply це зловить як `APPLY_WRITERS_RUNNING`). smc-binance не зупиняти: пише лише binance-символи,
   для них інструмент відмовляє.
3. apply без `--copy` і без `--data-root` (ціль = data_root конфігу), той самий `--expect-plan-sha`.
   Рейки: скан `/proc` (записувачі за argv і FD на запис), ринок усіх символів закритий ±30 хв, власник файлів ==
   euid (не запускати від root), усі входи плану — ті самі байти.
4. verify по маніфесту apply → rc=0.
5. `sudo -n supervisorctl start smc:smc-fxcm smc:smc-preview`; `sudo -n supervisorctl restart smc:smc-ws`
   (RAM/Redis-кеш старих значень).
6. Вікно спостереження 120 с (D9.1); served == disk для 3 ключів; health root покаже розбіжність O на derived до
   слайса C — очікувано.

Відкат: `rollback --apply-manifest <m> --expect-manifest-sha $(sha256sum <m> | cut -d' ' -f1)` при зупинених
записувачах (відмовляє, якщо файл змінився після apply); крайній випадок — tar з кроку 1.

## 5. Пакети

- Далі — по місяцю/символу: fetch (кілька вихідних, `--only-missing` для продовження), plan на місяць, копія,
  прод. Зміна будь-якого входу після плану (перезабір доби staging, дописаний бар) → apply відмовить
  `APPLY_PLAN_INPUT_CHANGED`; план будується наново в новий plan_dir.
- `SKIP_BAKED` доби перезабирати не раніше ніж через 7 діб (`--min-age-days` за замовчуванням 7) і перепланувати.
- Хвилини `SKIP_FLAT_NON_TRADING` (зимові 21:00–21:59 металів) лишаються PREV до ADR-0095.

## 6. Гігієна

- Бекапи `part-*.jsonl.bak.<ts>` (жорсткі лінки, простір = старий файл) інструмент НЕ видаляє. Власник прибирає
  їх за списком `files[].backup` маніфесту apply після verify rc=0 і тижня спостереження; перед видаленням —
  перелічити scope (D13.5).
- Staging, плани і маніфести — у `/opt/smc-v3-ft`, не в `data_v3` і не в git.
