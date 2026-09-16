# Runbook: ремонт open/high/low M1 з FXCM FIRST_TICK (ADR-0096 §3.3 B)

> Інструмент: `python -m tools.repair.first_tick_m1 <fetch|plan|apply|verify|rollback>`.
> Кожен крок, що пише в `data_v3` на проді, — окреме «го» власника. Усе — від користувача `smc` (він `nologin`, тож
> кожна команда — через sudo з явним HOME, інакше SDK не знайде свій кеш):
> `sudo -n -u smc env HOME=/var/lib/smc PYTHONPATH=/opt/smc-v3 /opt/smc-v3/.venv37/bin/python -m tools.repair.first_tick_m1 …`
> (фази plan/apply/verify/rollback — тим самим способом, але з `/opt/smc-v3/.venv/bin/python`).

## 0. Передумови

- Слайс A (`fix/fxcm-first-tick`) задеплоєний: інакше live продовжує писати PREVIOUS_CLOSE, і ремонт історії
  змішає епохи назад.
- «Го» власника на вікно B (вихідні: рейка ринку інакше не пустить).
- Каталоги поза `data_v3` і поза репо: `/opt/smc-v3-ft/{staging,sdk,plans,copy,work}`; запуск fetch — з cwd
  `/opt/smc-v3-ft` (у cwd репо живе кеш SDK live-сайдкара `History/`, інструмент відмовить `FT_FETCH_CWD_IS_REPO`).

## 1. Фази і коди виходу

| Фаза | Середовище | Пише | rc |
|---|---|---|---|
| fetch | `.venv37`, cwd поза репо | лише staging | 0 усе ок · 1 є невдалі доби або сесії (`sessions_failed=`) · 2 відмова до виклику · 3 зупинка рейкою · 128+signum сигнал |
| plan | `.venv` | лише plan_dir | 0 план · 1 план, є відмовлені part-файли · 2 плану немає |
| apply | `.venv` | part-файли (rewrite_atomic) | 0 ок · 1 розбіжність/звірка (частково) · 2 відмова до запису · 3 записувачі/ринок · 128+signum сигнал |
| verify | `.venv` | лише work_dir | 0 порушень немає · 1 порушення · 2 вхідна помилка |
| rollback | `.venv` | part-файли з бекапів | як apply; 3 — ще й файл змінився посеред відкату |

Категорії плану: `REPLACE` (записується), `SAME`, `SKIP_BAKED` (open поза [low, high], ланцюжок o == prev_c з
таким рядком, або доба з часткою o == prev_c ≥ 0.9), `SKIP_CLOSE_MISMATCH`, `SKIP_RANGE_EXPANDS`,
`SKIP_RANGE_CHANGED_BEYOND_STRETCH` (звузилась межа, якої PREVIOUS_CLOSE не розтягував — інша версія даних),
`SKIP_FLAT_NON_TRADING`, `SKIP_WOULD_HIDE` (після заміни O=H=L=C з v ≤ 10 — Redis cold-load сховав би свічку;
лишається PREV до патча межі Redis, ADR §3.3 B), `SKIP_WINNER_INELIGIBLE`, `MISSING_IN_STAGING`,
`EXTRA_IN_STAGING` (ніколи не пишеться).

Сесії fetch: одна дитина = один логін FXCM на пакет до `--days-per-session` діб (дефолт 7, 1..14), кожна доба —
окремий `get_history` під власним дедлайном (`--call-timeout-s`, перевзводиться перед кожним кроком усередині
дитини). `--max-calls` рахує `get_history` (доби), не логіни. Логіни рахує `--max-logins` (дефолт
⌈max-calls / days-per-session⌉, стеля 60): кожна сесія — логін, і та, що не дала жодної доби (логін відмовив чи
завис, дитина впала до першої доби) — теж; ліміт перевіряється перед кожною сесією (`FT_FETCH_MAX_LOGINS_REACHED`,
rc 3). Оцінка логінів: ⌈діб / days_per_session⌉ + по одному на кожну перервану сесію (дедлайн, відмова SDK, логін,
логаут) — перервана сесія з'їдає логін із того самого бюджету, тож прогін із перерваними сесіями зупиниться за
логінами раніше, ніж за `--max-calls`. Приклад: 5 символів × ~260 торгових діб ≈ 1300 діб → ≈ 186 логінів замість
≈ 1300; за вихідні з `--max-calls 300` — ≈ 43 логіни. Ліміт відмов поспіль `--max-consecutive-failures` (1..10)
діє окремо на доби (`FT_FETCH_TOO_MANY_FAILURES`) і на сесії (`FT_FETCH_TOO_MANY_SESSION_FAILURES`): сесія не ok —
логін, дедлайн, SDK або логаут, що відмовив після останньої доби (доби в ній закомічено, а сесію брокера не
закрито штатно).
`--dry-run` друкує `sessions=` і `max_logins=`. Пауза `--call-interval-s` — між сесіями.

## 2. Пілот (одна минула доба XAU у вихідні)

```bash
cd /opt/smc-v3-ft
PYTHONPATH=/opt/smc-v3 /opt/smc-v3/.venv37/bin/python -m tools.repair.first_tick_m1 fetch \
  --symbol XAU/USD --from 2026-07-26 --to 2026-07-26 \
  --staging-root /opt/smc-v3-ft/staging --sdk-cwd /opt/smc-v3-ft/sdk --max-calls 1 --max-logins 1
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

Частота логінів FXCM не виміряна: пілот — рівно один логін на одну добу (`--max-calls 1`, отже `--max-logins` = 1;
логін, що відмовив, не повторюється), далі одна сесія на тиждень (`--max-calls 7`), далі кілька сесій
(`--max-calls 30` = не більше 5 логінів, разом із перерваними); будь-яка сесія `child_error` із `login` у
`sessions[].detail` поспіль — стоп і розбір. Невдала доба посеред сесії (`deadline`/`timeout` у `calls[]`) —
перезабирається наступним прогоном з `--only-missing`.

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
3. apply без `--copy` і без `--data-root` (ціль = data_root конфігу), той самий `--expect-plan-sha`, НОВИЙ
   `--manifest-out` (наявний файл → `APPLY_MANIFEST_EXISTS` rc=2: маніфест частково застосованого прогону не
   затирається). Рейки: realpath кожного шляху запису в межах data_root, скан `/proc` (записувачі за argv і FD на
   запис), ринок усіх символів закритий ±30 хв, власник файлів == euid (не запускати від root), усі входи плану —
   ті самі байти. Перерваний apply (сигнал, Ctrl+C, kill) — маніфест каже правду: `replacing` вирішує диск
   (verify/rollback звіряють sha); далі — verify або rollback за цим маніфестом, не повторний apply.
4. verify по маніфесту apply → **rc=0**. rc=0 означає і «змінились рівно заплановані o/h/low», і «план застосовано
   ВЕСЬ»: файли плану, яких немає у стані «після», друкуються як `VERIFY_FILE_NOT_APPLIED` і дають
   `FT_VERIFY_INCOMPLETE=1` з rc=1. Якщо apply завершився не нулем (рейка during, зміна входу, сигнал) — це саме той
   випадок: доперепланувати решту діб у НОВИЙ plan_dir (apply на цей план уже відмовить `APPLY_PLAN_INPUT_CHANGED`) і
   застосувати. Свідомо прийняти часткове (напр. перед відкатом) — `--allow-incomplete`.
5. `sudo -n supervisorctl start smc:smc-fxcm smc:smc-preview`; `sudo -n supervisorctl restart smc:smc-ws`
   (RAM/Redis-кеш старих значень).
6. Вікно спостереження 120 с (D9.1); served == disk для 3 ключів; health root покаже розбіжність O на derived до
   слайса C — очікувано.

Відкат: `rollback --apply-manifest <m> --expect-manifest-sha $(sha256sum <m> | cut -d' ' -f1)` при зупинених
записувачах (відмовляє, якщо файл не «до» і не «після» apply). Перерваний відкат — той самий виклик ще раз:
файли, уже повернуті до байтів «до», пропускаються (`already_restored` у звіті), решта відкочується; кожен запуск
пише новий звіт `<m>.rollback-*.json`. Крайній випадок — tar з кроку 1.

Лок `<plan_dir>/.apply.lock` (apply і rollback) і `<staging>/_fetch.lock`: процес, що зник на ЦЬОМУ host (SIGKILL,
OOM, перезавантаження), лишає лок — наступний запуск знімає його сам із `FT_LOCK_STALE_REMOVED` (host у локу ==
поточний, процесу з його pid немає). Відмова `*_LOCK_HELD` з `reason=`:
- `pid_alive` — процес живий: дочекатись або зупинити його штатно, лок не чіпати;
- `other_host` (спільний диск) — на host із лока `ps -p <pid>`; лише якщо процесу там немає — `rm` лока і повтор;
- `holder_unparsable` / `pid_liveness_unknown` — прочитати лок (`cat`), перевірити pid вручну, лише тоді `rm`;
- `holder_changed` — лок змінився між читанням і зняттям (хтось узяв його паралельно): нічого не чіпати, повторити
  команду; якщо повторюється — шукати другий запущений процес інструмента.

Прибраний `plan_dir` (плани — витратні, §5) відкату не блокує: `rollback` відмовляє іменовано
`ROLLBACK_PLAN_DIR_MISSING` (rc 2, нічого не записано), бо там живе лок; `mkdir -p <plan_dir>` і повтор — відкат
читає лише маніфест і бекапи.

## 5. Пакети

- Далі — по місяцю/символу: fetch (кілька вихідних, `--only-missing` для продовження), plan на місяць, копія,
  прод. Зміна будь-якого входу після плану (перезабір доби staging, дописаний бар) → apply відмовить
  `APPLY_PLAN_INPUT_CHANGED`; план будується наново в новий plan_dir.
- План попереднього формату (`ft_m1_plan_v1` / `ft_m1_plan_v2`, до правил SKIP_WOULD_HIDE /
  SKIP_RANGE_CHANGED_BEYOND_STRETCH / SKIP_V_DIFFERS) apply і verify відмовляють `APPLY_PLAN_FORMAT_UNSUPPORTED`
  (rc 2) — перепланувати в новий plan_dir; staging лишається чинним, перезабір не потрібен.
- Хвилини `SKIP_V_DIFFERS` (брокер віддав інший tick volume — інша витяжка) лишаються PREV: ремонт value-only не
  заміняє `v`, а писати діапазон з однієї версії даних і обсяг з іншої заборонено. Перезабрати добу пізніше.
- Діапазон без жодного part-файла і жодної доби staging → `PLAN_NO_INPUTS` (rc 2): перевірити `--from/--to`,
  `--staging-root` і що fetch дійсно поклав доби.
- `SKIP_BAKED` доби перезабирати не раніше ніж через 7 діб (`--min-age-days` за замовчуванням 7) і перепланувати.
- Хвилини `SKIP_FLAT_NON_TRADING` (зимові 21:00–21:59 металів) лишаються PREV до ADR-0095.

## 6. Гігієна

- Бекапи `part-*.jsonl.bak.<ts>[.<n>]` (жорсткі лінки, простір = старий файл) інструмент НЕ видаляє. Власник прибирає
  їх за списком `files[].backup` маніфесту apply після verify rc=0 і тижня спостереження; перед видаленням —
  перелічити scope (D13.5). Після видалення бекапів rollback за цим маніфестом відмовить `ROLLBACK_BACKUP_MISSING`
  (rc 2, нічого не записано) — відкат тоді лише з tar кроку §4.1.
- **Свої бекапи відкату.** `rollback` теж лишає в `data_v3` жорсткий лінк пропатченого вмісту
  (`part-*.jsonl.bak.<ts>[.<n>]`). Їх у маніфесті apply немає — шляхи друкує саме відкат
  (`FT_ROLLBACK_OWN_BACKUP`, поле `files[].backup_of_patched` у звіті `<m>.rollback-*.json`). Прибирати за цим
  списком; вони безпечні до видалення одразу після успішного відкату.
- Залишок `part-*.jsonl.tmp` у теці SSOT означає аварію між записом і `os.replace`: читачі його не бачать
  (фільтр `part-*.jsonl`), видаляти після перевірки, що жоден процес інструмента не працює.
- Staging, плани і маніфести — у `/opt/smc-v3-ft`, не в `data_v3` і не в git.
