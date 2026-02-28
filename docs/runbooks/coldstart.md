# Runbook: Cold Start — одноразовий rebuild derived для всіх символів

> **Навігація**: [docs/index.md](../index.md)

**Initiative**: P2X.8-S1  
**Ціль**: після рестарту UI не має дірок на M3/M5/M15/M30/H1/H4 через відсутній derived-tail.

---

## Передумови

1. `config.json` збережений, `symbols[]` містить 13 символів.
2. M1-дані (`data_v3/<SYMBOL>/tf_60/`) існують для всіх символів.
3. Redis запущений (для подальшого прогріву).
4. `.venv` активований.

---

## Команда

```bash
python -m tools.rebuild_from_m1
```

### Альтернатива — один символ

```bash
python -m tools.rebuild_from_m1 --symbol "XAU/USD"
```

### Dry-run (без запису на диск)

```bash
python -m tools.rebuild_from_m1 --dry-run
```

---

## Очікуваний результат

1. Лог показує каскад M1→M3/M5→M15→M30→H1→H4 для кожного символу.
2. Exit code = 0.
3. Disk TF-файли оновлені для всіх 13 символів.

---

## Перевірка після rebuild

```bash
python -m tools.run_exit_gates --manifest tools/exit_gates/manifest.json
```

---

## Rollback

Derived-файли (tf_180/300/900/1800/3600/14400) можна видалити і відновити повторним запуском:

```bash
python -m tools.rebuild_from_m1
```

---

## Примітки

Канонічний rebuild tool — `tools/rebuild_from_m1.py`. Source = M1 (SSOT).
Використовує `core/derive.py` API, calendar-aware, append-only.

- Rebuild виконується **послідовно** по символах (без паралельності), щоб уникнути race condition на `_derived_tail_state.json`.
- Якщо для символу немає M5-даних на диску — він буде у FAIL-списку, exit code = 2.
- Перед запуском конектора (`python -m app.main_connector`) рекомендується виконати цей rebuild одноразово.
