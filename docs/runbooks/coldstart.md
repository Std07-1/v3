# Runbook: Cold Start — одноразовий rebuild derived для всіх символів

**Initiative**: P2X.8-S1  
**Ціль**: після рестарту UI не має дірок на M15/M30/H1 через відсутній derived-tail.

---

## Передумови

1. `config.json` збережений, `symbols[]` містить 13 символів.
2. M5-дані (`data_v3/<SYMBOL>/tf_300/`) існують для всіх символів.
3. Redis запущений (для подальшого прогріву).
4. `.venv` активований.

---

## Команда

```bash
python -m tools.rebuild_derived --all
```

### Альтернатива — підмножина символів

```bash
python -m tools.rebuild_derived --symbols "XAU/USD,XAG/USD,NGAS"
```

### Dry-run (без запису на диск)

```bash
python -m tools.rebuild_derived --all --dry-run
```

---

## Очікуваний результат

1. Лог показує `=== Rebuild START: <SYMBOL> ===` для кожного символу.
2. Лог показує `Rebuild OK: <SYMBOL>` для кожного символу.
3. Підсумок: `=== ПІДСУМОК: 13/13 OK, 0 FAIL ===`.
4. `data_v3/_derived_tail_state.json` містить 13 символів.
5. Exit code = 0.

---

## Перевірка після rebuild

```bash
python -m tools.run_exit_gates --manifest tools/exit_gates/manifest.json
```

Очікувано: `gate_coldstart_multisymbol: 7/7 OK`.

---

## Rollback

Derived-файли (tf_900, tf_1800, tf_3600) можна видалити і відновити повторним запуском:

```bash
python -m tools.rebuild_derived --all
```

---

## Примітки

- Rebuild виконується **послідовно** по символах (без паралельності), щоб уникнути race condition на `_derived_tail_state.json`.
- Якщо для символу немає M5-даних на диску — він буде у FAIL-списку, exit code = 2.
- Перед запуском конектора (`python -m app.main_connector`) рекомендується виконати цей rebuild одноразово.
