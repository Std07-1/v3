# Runbook: Live Recover (P2X.9-L1)

## Ціль

Після паузи/рестарту/reconnect конектор автоматично наздоганяє пропущені final M5 бари
без бомбардування брокера та без past-mutation.

## Як працює

1. Кожну хвилину `poll_iteration()` → `_live_recover_check()` порівнює:
   - `expected_last_closed_m5_open_ms` (очікуваний останній закритий M5)
   - `_last_saved_m5_open_ms` (останній записаний на диск)
2. Якщо `gap_bars > live_recover_threshold_bars` (default: 3) → вхід у recover.
3. Кожен цикл досилає `min(gap, max_per_cycle, remaining_budget)` барів.
4. Cooldown між fetch-ами: `live_recover_cooldown_s` (default: 10 сек).
5. Загальний бюджет: `live_recover_max_total_bars` (default: 2000).
6. Фазовий лог кожні `live_recover_log_interval_s` (default: 60 сек).

## Конфіг (config.json)

```json
{
  "live_recover_threshold_bars": 3,
  "live_recover_max_bars_per_cycle": 50,
  "live_recover_cooldown_s": 10,
  "live_recover_max_total_bars": 2000,
  "live_recover_log_interval_s": 60
}
```

## Ключові інваріанти

- **Тільки final M5**: бари йдуть через `_ingest_m5_bars` → `_append_bar` → `_is_final_bar` фільтр.
- **Rate-limit**: cooldown + max_per_cycle + max_total — брокер не бомбардується.
- **Collapse-to-latest**: кожен цикл перераховує вікно від актуального `_last_saved_m5_open_ms` до `cutoff` — одне вікно, не черга з 100 задач.
- **Degraded-but-loud**: WARNING при LIVE_RECOVER_START, INFO фазовий лог, INFO при LIVE_RECOVER_DONE.
- **No preview**: контекст `live_recover_m5`, жодного `publish_preview_bar`.

## Імітація 2-годинного простою

### Крок 1: Запустити конектор нормально

```bash
python -m app.main
```

### Крок 2: Дочекатись стабільної роботи (5+ хвилин)

### Крок 3: Зупинити процес (Ctrl+C)

### Крок 4: Почекати 2 години (або вручну зсунути `_last_saved_m5_open_ms`)

### Крок 5: Перезапустити конектор

```bash
python -m app.main
```

### Крок 6: Перевірити логи

Очікувані записи:

```
LIVE_RECOVER_START symbol=XAU/USD gap_bars=24 cutoff=... last_saved=...
LIVE_RECOVER sym=XAU/USD missing_bars=... fetched=... written=... elapsed_s=...
LIVE_RECOVER_DONE symbol=XAU/USD reason=caught_up gap_at_start=24 fetched=24 written=24 elapsed_s=...
```

### Крок 7: Перевірити дані

- gap_bars має зменшитись до 0
- Нові бари мають бути на диску в `data_v3/<symbol>/tf_300/`

## Тестувальний сценарій (без реального простою)

Юніт-тести в `tests/test_live_recover.py` (17 тестів) перевіряють:

- Розрахунок вікна (gap_bars)
- Поріг активації
- Collapse-to-latest (min(gap, per_cycle, budget))
- Cooldown
- Бюджет

```bash
python -m unittest tests.test_live_recover -v
```

## Exit gate

```bash
python -c "from tools.exit_gates.gates.gate_live_recover_policy import run_gate; import json; print(json.dumps(run_gate({'root': '.'}), indent=2))"
```

Очікувано: 4/4 sub-gates OK.

## Rollback

1. Видалити з `config.json` ключі `live_recover_*`.
2. У `engine_b.py`: видалити `_live_recover_check()` виклик з `poll_iteration()`.
3. Видалити методи `_live_recover_check()` та `_live_recover_finish()`.
4. Видалити параметри конструктора `live_recover_*` з `engine_b.py` та `composition.py`.
