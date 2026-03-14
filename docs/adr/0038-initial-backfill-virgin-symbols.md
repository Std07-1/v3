# ADR-0038: Initial Backfill for Virgin Symbols

- **Статус**: Implemented
- **Дата**: 2026-03-14
- **Автор**: Architect (R_ARCHITECT)
- **Initiative**: cold_start_backfill_v1

## Контекст і проблема

При першому запуску нового символу (або нового провайдера — Binance) графік наповнюється зі швидкістю 1 M1/хв. H1 з'являється через 60 хв, H4 — через 240 хв, D1 — через 24 год.

**Причинний ланцюжок** (VERIFIED `m1_poller.py:588–619`):

```
Перший запуск → JSONL порожній
  → warmup_watermark() читає 10 M1 з tail → 0 знайдено
    → watermark_ms = None
      → tail_catchup() skip (guard: if watermark_ms is None: return)
        → дані тільки з live polling (1 bar/min)
          → cascade живиться тільки live M1
            → HTF порожні годинами
```

Проблема стосується **будь-якого** нового символу на будь-якому провайдері.

Підтверджено: BTCUSDT 184 M1 (~3 год live), H4/D1 відсутні.

## Обмеження

| # | Обмеження | Джерело |
|---|-----------|---------|
| C1 | Всі writes тільки через UDS | I1 |
| C2 | Degraded-but-loud: backfill failure ≠ crash | I5 |
| C3 | Bootstrap params в config.json SSOT | ADR-0003 S4 |
| C4 | Provider-agnostic: FXCM, Binance, future | ADR-0016, ADR-0037 |
| C5 | Не міняти tail_catchup — працює для restart | Стабільність |
| C6 | M1PollerRunner = спільна точка входу | Архітектура |
| C7 | Cascade catchup (Phase 2c) без змін | ADR-0003 |

## Розглянуті варіанти

### A. Phase 2.5 — Initial History Fetch в bootstrap (обрано)

Нова фаза між Phase 2 (watermark warmup) та Phase 2b (DeriveEngine warmup).
Trigger: `watermark_ms is None` після Phase 2. Fetch N M1 від провайдера → write через UDS → set watermark → continue pipeline.

**Плюси**: мінімальний blast radius (1 фаза, існуючий flow не змінюється). Idempotent (partial fetch → watermark → restart → tail_catchup дозаповнить). Provider interface вже підтримує fetch with endTime.

**Мінуси**: потрібен новий метод `fetch_m1_range()` для range-based fetch.

### B. Окремий backfill tool

`python -m tools.initial_backfill --symbol BTCUSDT --provider binance`.

**Плюси**: ізольовано, zero risk.
**Мінуси**: ручний крок, не масштабується.

### C. Зняти guard watermark=None у tail_catchup

Fallback watermark = `now - N_days`.

**Плюси**: zero нового коду.
**Мінуси**: порушує семантику tail_catchup, міксує restart vs virgin.

## Рішення

**Альтернатива A** — Phase 2.5: Initial Backfill.

### Місце в bootstrap pipeline

```
_bootstrap_warmup()
  ├── Phase 1:   Redis priming (disk→Redis, all TFs)
  ├── Phase 2:   Watermark warmup (10 M1 per symbol from disk tail)
  │
  ├── Phase 2.5: *** INITIAL BACKFILL ***
  │              trigger: watermark_ms is None after Phase 2
  │              action: fetch initial M1 history from provider
  │              write: M1SymbolPoller._ingest_bar() per bar
  │              result: watermark set, JSONL populated
  │
  ├── Phase 2b:  DeriveEngine buffer warmup
  ├── Phase 2c:  Cascade catchup (1440 M1 through derive)
  ├── Phase 3:   Tail catchup (fetch gap M1 watermark → now)
  └── prime:ready:m1
```

### Provider interface

Новий метод `fetch_m1_range()` в `BinanceHistoryProvider`:

```python
def fetch_m1_range(
    self, symbol: str, from_ms: int, to_ms: int, limit: int
) -> List[CandleBar]:
```

Відмінність від `fetch_last_n_m1`: використовує `startTime` + `endTime` для діапазону.
Binance: `GET /fapi/v1/klines?symbol=X&interval=1m&startTime=from_ms&endTime=to_ms&limit=1500`.

### Backfill стратегія

Config-driven. Default = `cascade_catchup_m1_bars` (1440 = 1 день M1).

```json
"bootstrap": {
    "initial_backfill_m1_bars": 1440,
    ...existing...
}
```

`from_ms = now_ms - initial_backfill_m1_bars * 60_000`.
Fetch chunk = 1500 (Binance API limit). Якщо N > 1500 → поки не потрібно (1440 < 1500).

### Failure handling (I5)

| Сценарій | Поведінка |
|----------|-----------|
| Повний fail (network) | WARNING `INITIAL_BACKFILL_FAILED`. Watermark = None. Tail_catchup skip. Live-only. Degraded-but-loud. |
| Partial (800/1440) | Записати що є → watermark set → tail_catchup дозаповнить → cascade з неповними. WARNING `INITIAL_BACKFILL_PARTIAL`. |
| Provider не підтримує | Skip з WARNING. Fallback live-only. |
| Символ новий (< 1440 M1) | Записати всі бари → watermark set. |

### Idempotency

Phase 2.5 trigger = `watermark_ms is None after Phase 2`.
Після успішного backfill → JSONL populated → Phase 2 warmup знаходить → watermark ≠ None → Phase 2.5 skip.
Один раз backfill → потім назавжди restart-safe.

### D1 coverage

D1 derive від M1 (ADR-0023) потребує 1440 M1 → 1 D1 бар через cascade.
Для глибшої D1 історії — окремий Phase 2.6 (optional S4): fetch D1 напряму від Binance `interval=1d`.

## Наслідки

### Інваріанти (safe)

| Інваріант | Статус |
|-----------|--------|
| I0 Dependency Rule | ✅ Backfill logic в runtime/, provider в runtime/ingest/broker/ |
| I1 UDS вузька талія | ✅ Writes через `_ingest_bar()` → UDS |
| I2 Геометрія часу | ✅ Epoch ms, existing bar contract |
| I3 Final > Preview | ✅ Backfill = final бари (complete=True) |
| I4 Dual plane routing | ✅ Не торкається |
| I5 Degraded-but-loud | ✅ Підсилюється (explicit WARNING) |
| I6 Stop-rule | ✅ Не міняє інваріанти |

### Файли (blast radius)

| Файл | Зміна |
|------|-------|
| `config.json` | +1 поле `bootstrap.initial_backfill_m1_bars` |
| `runtime/ingest/broker/binance/provider.py` | +метод `fetch_m1_range()` |
| `runtime/ingest/polling/m1_poller.py` | Phase 2.5 в `_bootstrap_warmup()` + per-symbol `initial_backfill()` + `initial_backfill_d1()` в M1SymbolPoller |

### S4: D1 Direct Backfill

D1 через cascade від 1440 M1 дає лише ~1 D1 бар. Для SMC/trading decisions потрібна глибша D1 історія.

**Рішення**: `fetch_d1_range()` в BinanceHistoryProvider (interval=1d) + `initial_backfill_d1()` в M1SymbolPoller.
Phase 2.5b в `_bootstrap_warmup()` — після M1 backfill + re-warmup, перед Phase 2b.
Config: `bootstrap.initial_backfill_d1_bars: 180` (6 місяців).
Idempotency: перевірка `read_tail_candles(sym, 86400, 1)` — якщо D1 є → skip.
Write path: `UDS.commit_final_bar()` (I1 compliant).
`_parse_klines` параметризовано: `tf_s` parameter з default 60 для backward compat.

## P-Slices

| Slice | Що | LOC est | Файли | Verify |
|-------|-----|---------|-------|--------|
| S1 | Config: `bootstrap.initial_backfill_m1_bars` default 1440 | ~5 | config.json | Field loads |
| S2 | `fetch_m1_range()` в BinanceHistoryProvider | ~35 | provider.py | Unit test fetch |
| S3 | Phase 2.5 в `_bootstrap_warmup()` + `initial_backfill()` в M1SymbolPoller | ~60 | m1_poller.py | Virgin symbol → backfill → watermark set |
| S4 | D1 direct backfill: `fetch_d1_range()` + Phase 2.5b | ~80 | provider.py, m1_poller.py, binance_ingest_worker.py, config.json | D1 ≥ 30 bars |

## Exit Gates

| Gate | Опис |
|------|------|
| G1 | Virgin symbol (порожній JSONL) → після bootstrap M1 count ≥ `initial_backfill_m1_bars` |
| G2 | Cascade після backfill: H1 ≥ 24, H4 ≥ 6 |
| G3 | Restart після backfill: tail_catchup fills gap, no re-backfill |
| G4 | Backfill failure: process NOT crash, WARNING в logs |
| G5 | Multi-symbol: кожен symbol backfill-ається незалежно |

## Rollback

1. Видалити Phase 2.5 блок з `_bootstrap_warmup()`
2. Видалити `initial_backfill()` з M1SymbolPoller
3. Видалити `fetch_m1_range()` з BinanceHistoryProvider
4. Видалити `initial_backfill_m1_bars` з config.json bootstrap section
5. Поведінка повертається до live-only для virgin symbols
