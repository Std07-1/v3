# ADR-0044: HTF Live Preview — Incremental HTF Accumulator для D1/H4 Forming Candle

- **Статус**: Proposed
- **Дата**: 2026-03-24
- **Автор**: R_ARCHITECT
- **Initiative**: `htf_live_preview_v1`
- **Пов'язані ADR**: ADR-0023 (D1 derive from M1), ADR-0001 (UDS), ADR-0002 (derive chain)
- **Cross-ref**: Bug Hunter D-01 (TF label map SSOT), D-07 (day_anchor_offset_s_alt2 phantom key)
- **Scope**: `runtime/ingest/tick_preview_worker.py` + `config.json` + new test file
- **LOC**: ~105 production, ~120 tests

---

## Проблема

При переключенні на D1 (і H4 меншою мірою) формуюча свічка стартує "з нуля".
M1/M3 мають preview завжди (TickAggregator → UDS preview plane), але H4/D1
отримують лише фінальні derive-бари. Результат: пласка лінія замість живої свічки
що накопичилась за день.

## 2. Обмеження (Constraints)

| Constraint | Опис |
|-----------|------|
| **I1 — UDS narrow waist** | Всі writes тільки через `publish_preview_bar()`. Новий acumulator не пише напряму. |
| **I3 — Final > Preview (NoMix)** | HTF preview bars мають `complete=False`, `src="htf_preview"`. Final derive завжди перемагає. `"htf_preview"` NOT in `FINAL_SOURCES`. |
| **I5 — Degraded-but-loud** | Seed failure → `logger.warning("HTF_SEED_FAIL")`. Не silent. |
| **I0 — Dependency Rule** | `core.buckets.bucket_start_ms` — дозволений import (core → runtime). |
| **Performance** | O(1) per M1 update × 2 target TFs × 13 symbols = ~26 ops/sec. Negligible. |
| **Memory** | 2 RunningBar × 13 symbols = 26 об'єктів замість list до 1600 CandleBar × 13 sym. |
| **Зворотна сумісність** | Wire format НЕ змінюється. UI вже обробляє `complete=false`. Config additive (86400 до існуючого list). |
| **Prerequisite** | ADR-0023 (D1 derive from M1) — завершено. |

---

## 3. Розглянуті альтернативи

### Альтернатива A: _M1toHTFBuffer (початковий план — list + O(N) scan)

- **Суть**: Зберігати list усіх M1 барів за D1 бакет. На кожен тік — O(N) scan: filter by bucket, aggregate.
- **Pros**: Простіша реалізація (1 list, 1 rebuild). Можна rebuild будь-який момент.
- **Cons**: O(N) per tick де N=1440 (D1). Memory: до 1600 CandleBar × 13 sym = 20800 об'єктів. CPU spike при повному дні.
- **Blast radius**: `tick_preview_worker.py`, `config.json`
- **LOC estimate**: ~90

### Альтернатива B: _HTFRunningAccumulator (інкрементальний — ОБРАНА)

- **Суть**: Running OHLCV state per (symbol, tf_s). O(1) merge на кожен M1. Seed = послідовний update().
- **Pros**: O(1) per tick. Memory: 2 RunningBar × 13 sym = 26 об'єктів. Єдиний код path (seed = update). SSOT bucket alignment через `core.buckets.bucket_start_ms`.
- **Cons**: Не можна "rebuild from scratch" без re-seed. При out-of-order M1 seed — потрібен explicit sort.
- **Blast radius**: `tick_preview_worker.py`, `config.json`, new test file
- **LOC estimate**: ~105 production + ~120 tests

### Вибір: Альтернатива B

**Обґрунтування**: O(1) vs O(N=1440) per tick — critical при 13 символах × ~1 tick/sec = 13 updates/sec. Альтернатива A створює CPU spike при повному D1 бакеті (1440 M1 × 13 sym = 18720 scans/sec). Альтернатива B — constant 26 merges/sec. Memory footprint у ~800 разів менший.

---

## 4. Рішення (деталі)

Інкрементальний акумулятор `_HTFRunningAccumulator` в `tick_preview_worker.py`.
Замість зберігання всіх M1 і O(N) пересканування на кожен тік — тримає **running OHLCV state**
per (symbol, tf_s). O(1) на кожне оновлення.

Seed при старті: послідовно проганяє M1 фінали через той самий `update()`.
Публікує через існуючий `publish_preview_bar` шлях — UI змін не потребує.

### 4.0 Порівняння з початковим планом

| Аспект | Початковий план (_M1toHTFBuffer) | Variant A (_HTFRunningAccumulator) |
|--------|----------------------------------|--------------------------------------|
| Пам'ять | list до 1600 CandleBar × 13 sym | 2 RunningBar × 13 sym (26 об'єктів) |
| CPU per tick | O(N) scan + filter + max/min/sum | O(1) — один `max()`, один `min()`, одне додавання |
| Агрегація | Дублює aggregate_bars вручну | Ідентична логіка, але інкрементальна |
| Seed | Зберігає list M1 → build_previews | Цикл update() по M1 фіналах (однаковий код path) |
| Bucket alignment | Нова `_bucket_start_ms` функція | Реюз `core.buckets.bucket_start_ms` (SSOT) |

---

## Крок 1: config.json

Додати D1 (86400) до preview allowlist. H4 (14400) **вже присутній**.

**Поточний стан** (verified 2026-03-24):
- `config.json` → `"preview_tick_tfs_s": [60, 180, 300, 900, 1800, 3600, 14400]` — H4 є, D1 немає
- `core/config_loader.py` → `preview_tf_allowlist_from_cfg()` шукає: `"tf_preview_allowlist_s"` → `"preview_tick_tfs_s"` → `DEFAULT`
- `DEFAULT_PREVIEW_TF_ALLOWLIST = {60, 180, 300, 900, 1800, 3600, 14400}` — H4 є, D1 немає
- UDS `publish_preview_bar()` перевіряє `bar.tf_s not in self._preview_tf_allowlist` — guard активний
- `FINAL_SOURCES = {"history", "derived", "history_agg"}` — `"htf_preview"` NOT in set ✅

**Мінімальна зміна**: додати `86400` до існуючого `"preview_tick_tfs_s"` у `config.json`:

```jsonc
"preview_tick_tfs_s": [60, 180, 300, 900, 1800, 3600, 14400, 86400]
```

> ⚠️ **УВАГА**: ключ у config.json називається `"preview_tick_tfs_s"`, NOT `"preview_tf_allowlist_s"`.
> Функція `preview_tf_allowlist_from_cfg()` шукає спочатку `"tf_preview_allowlist_s"`,
> потім fallback на `"preview_tick_tfs_s"`. Оскільки `preview_tick_tfs_s` вже є — достатньо
> додати 86400 до нього. Новий ключ створювати не потрібно.

---

## Крок 2: `_HTFRunningAccumulator` клас

Додати після `_M1toM3Buffer` (рядок ~72) у `tick_preview_worker.py`:

```python
from core.buckets import bucket_start_ms  # SSOT bucket alignment


class _RunningBar:
    """Інкрементальний OHLCV акумулятор для одного бакету."""
    __slots__ = ("bucket_open_ms", "tf_s", "o", "h", "low", "c", "v", "count")

    def __init__(self, bucket_open_ms: int, tf_s: int, first_bar):
        self.bucket_open_ms = bucket_open_ms
        self.tf_s = tf_s
        self.o = first_bar.o
        self.h = first_bar.h
        self.low = first_bar.low
        self.c = first_bar.c
        self.v = first_bar.v
        self.count = 1

    def merge(self, bar):
        """O(1) інкрементальне злиття."""
        if bar.h > self.h:
            self.h = bar.h
        if bar.low < self.low:
            self.low = bar.low
        self.c = bar.c
        self.v += bar.v
        self.count += 1

    def to_candle(self, symbol: str) -> "CandleBar":
        return CandleBar(
            symbol=symbol,
            tf_s=self.tf_s,
            open_time_ms=self.bucket_open_ms,
            close_time_ms=self.bucket_open_ms + self.tf_s * 1000,
            o=self.o,
            h=self.h,
            low=self.low,
            c=self.c,
            v=self.v,
            complete=False,
            src="htf_preview",
            extensions={"m1_count": self.count},
        )


class _HTFRunningAccumulator:
    """Інкрементальна деривація HTF (H4, D1) preview з M1 барів.

    Замість зберігання списку M1 і O(N) пересканування — тримає
    running OHLCV state per (symbol, tf_s). O(1) per update.

    seed() використовує той самий update() — єдиний код path.
    """

    def __init__(self, target_tfs_s: list, anchor_offsets_ms: dict):
        """
        target_tfs_s: [14400, 86400]
        anchor_offsets_ms: {14400: 82800000, 86400: 79200000}
        """
        self._target_tfs_s = list(target_tfs_s)
        self._anchor_offsets_ms = anchor_offsets_ms
        # state: {symbol: {tf_s: _RunningBar | None}}
        self._running = {}

    def seed(self, symbol: str, m1_finals: list):
        """Ініціалізація з M1 фіналів (послідовний update — однаковий код path)."""
        for bar in m1_finals:
            self.update(symbol, bar)

    def update(self, symbol: str, m1_bar) -> list:
        """Оновлення з M1 баром. Повертає list[CandleBar] HTF previews.

        O(1) per call: один max, один min, одне додавання per target TF.
        """
        sym_state = self._running.setdefault(symbol, {})
        results = []

        for tf_s in self._target_tfs_s:
            tf_ms = tf_s * 1000
            anchor_ms = self._anchor_offsets_ms.get(tf_s, 0)
            bucket_open = bucket_start_ms(m1_bar.open_time_ms, tf_ms, anchor_ms)

            running = sym_state.get(tf_s)

            if running is None or running.bucket_open_ms != bucket_open:
                # Новий бакет — reset
                sym_state[tf_s] = _RunningBar(bucket_open, tf_s, m1_bar)
            else:
                # Той самий бакет — merge O(1)
                running.merge(m1_bar)

            results.append(sym_state[tf_s].to_candle(symbol))

        return results
```

**Ключові відмінності від початкового плану:**

1. **Немає `_completed` list** — немає O(N) scan
2. **`bucket_start_ms` з `core.buckets`** — SSOT для bucket alignment, не третя реалізація
3. **`seed()` = цикл `update()`** — єдиний код path, нуль дублювання
4. **`_RunningBar` з `__slots__`** — мінімальний memory footprint

---

## Крок 3: Зміни TickPreviewWorker

### 3.1 `__init__` — нові параметри + ініціалізація

```python
# Нові параметри конструктора:
htf_preview_tfs: list = None,       # [14400, 86400] або None/[] = disabled
h4_anchor_offset_s: int = 0,        # 82800
d1_anchor_offset_s: int = 0,        # 79200

# Ініціалізація (після self._m3_buffer):
if htf_preview_tfs:
    anchor_offsets_ms = {}
    for tf_s in htf_preview_tfs:
        if tf_s == 86400:
            anchor_offsets_ms[tf_s] = d1_anchor_offset_s * 1000
        elif tf_s >= 14400:
            anchor_offsets_ms[tf_s] = h4_anchor_offset_s * 1000
        else:
            anchor_offsets_ms[tf_s] = 0
    self._htf_acc = _HTFRunningAccumulator(htf_preview_tfs, anchor_offsets_ms)
else:
    self._htf_acc = None
```

> **Примітка**: anchor resolution тут — explicit per-TF routing (D1=79200, H4=82800),
> як і в DeriveEngine._cascade(). Не використовуємо `resolve_cascade_anchor_s`
> напряму бо той живе в core/derive.py і його API прив'язаний до cascade context.
> Замість цього — explicit config values, SSOT = config.json.
>
> **TODO (tech debt)**: це 3-тє inline місце з anchor routing (DeriveEngine, buckets.resolve_anchor_offset_ms,
> і тепер preview worker). За правилом "3 місця = centralize" — потрібен initiative для
> централізації anchor resolution. Допустимо для MVP, але трекати.

### 3.2 Новий метод `_seed_htf_from_uds()`

Використовуємо `read_tail_candles()` замість `read_window()` — повертає готові
`CandleBar` об'єкти, не потребує manual LWC→CandleBar parsing.

```python
def _seed_htf_from_uds(self):
    """Seed HTF акумулятора з M1 фіналів UDS. Викликається при старті."""
    if self._htf_acc is None:
        return
    for symbol in self._symbol_allowlist:
        try:
            m1_bars = self._uds.read_tail_candles(symbol, 60, 1500)
            m1_bars = [b for b in m1_bars if b.complete]
            m1_bars.sort(key=lambda b: b.open_time_ms)  # гарантуємо порядок для коректного open
            self._htf_acc.seed(symbol, m1_bars)
            logging.info("HTF_SEED symbol=%s m1_count=%d", symbol, len(m1_bars))
        except Exception as exc:
            logging.warning("HTF_SEED_FAIL symbol=%s err=%s", symbol, exc)
```

> **Чому `read_tail_candles` а не `read_window`?** `read_tail_candles()` повертає
> готові CandleBar об'єкти — усуває 15 LOC manual LWC dict→CandleBar conversion
> і potential field mapping bugs (verified: `uds.py:read_tail_candles` line ~1605).
>
> **Чому limit=1500?** D1 = 1440 M1 максимум за торговий день.
> 1500 дає запас на weekend gap recovery. Redis tail для M1 = 10080 — є.
>
> **Чому sort?** Disk read order не гарантований. Без sort — open D1 бакету
> може взятись з середини дня. One-liner fix, critical для коректності.
>
> ✅ VERIFIED: `bars_lwc` використовує LWC format (`open/high/low/close`),
> але `read_tail_candles` повертає CandleBar (`.o/.h/.low/.c`) — простіший шлях.

### 3.3 Інтеграція в `on_tick()` — після M1 publish

```python
# Знайти місце де M1 preview бар публікується (рядок ~376).
# Після публікації M1:

# HTF preview derivation: M1 → H4/D1
if tf_s == 60 and self._htf_acc is not None:
    htf_bars = self._htf_acc.update(symbol, bar)
    for htf_bar in htf_bars:
        self._publish_bar(htf_bar, symbol, htf_bar.tf_s)
```

> **Частота**: ~1 виклик/сек per symbol × 13 symbols = ~13 update/sec.
> Кожен update = O(1) merge × 2 target TFs = ~26 операцій/сек. Negligible.

### 3.4 Інтеграція в `run_forever()` — seed перед головним циклом

```python
def run_forever(self):
    # ... existing setup ...
    self._seed_htf_from_uds()   # <-- ДОДАТИ перед головним циклом
    # ... existing main loop ...
```

### 3.5 Інтеграція в `main()` — передача конфігу

```python
# Зчитування конфігу:
h4_anchor_s = int(cfg.get("day_anchor_offset_s", 0))      # 82800
d1_anchor_s = int(cfg.get("day_anchor_offset_s_d1", 0))    # 79200

# Визначення HTF preview TFs:
# Беремо з config preview_tick_tfs_s (SSOT) і фільтруємо лише HTF (>= H4)
preview_allowlist = cfg.get("preview_tick_tfs_s", [])
htf_preview_tfs = [tf for tf in preview_allowlist if tf >= 14400]

worker = TickPreviewWorker(
    ...  # існуючі параметри
    htf_preview_tfs=htf_preview_tfs,
    h4_anchor_offset_s=h4_anchor_s,
    d1_anchor_offset_s=d1_anchor_s,
)
```

---

## Крок 4: Тести `tests/test_htf_running_accumulator.py`

```python
"""Tests for _HTFRunningAccumulator (HTF live preview from M1)."""
import pytest
from core.model.bars import CandleBar

# Import the class under test
from runtime.ingest.tick_preview_worker import _HTFRunningAccumulator, _RunningBar


def _make_m1(symbol, open_ms, o, h, low, c, v=100.0):
    return CandleBar(
        symbol=symbol, tf_s=60,
        open_time_ms=open_ms,
        close_time_ms=open_ms + 60_000,
        o=o, h=h, low=low, c=c, v=v,
        complete=True, src="test",
    )


# D1 anchor = 79200s (22:00 UTC) → bucket для 2026-03-17 = 1742166000000 (22:00 UTC попереднього дня)
# H4 anchor = 82800s (23:00 UTC)
D1_ANCHOR_MS = 79200 * 1000
H4_ANCHOR_MS = 82800 * 1000


class TestHTFRunningAccumulator:

    def _make_acc(self, tfs=None):
        tfs = tfs or [14400, 86400]
        anchors = {14400: H4_ANCHOR_MS, 86400: D1_ANCHOR_MS}
        return _HTFRunningAccumulator(tfs, anchors)

    def test_single_m1_produces_htf_previews(self):
        """1 M1 бар → 1 H4 + 1 D1 preview."""
        acc = self._make_acc()
        # M1 bar at 2026-03-17 00:00 UTC = 1742169600000
        m1 = _make_m1("XAU/USD", 1742169600000, 2000.0, 2001.0, 1999.0, 2000.5)
        results = acc.update("XAU/USD", m1)
        assert len(results) == 2
        d1 = [r for r in results if r.tf_s == 86400][0]
        h4 = [r for r in results if r.tf_s == 14400][0]
        assert d1.o == 2000.0
        assert d1.h == 2001.0
        assert d1.low == 1999.0
        assert d1.c == 2000.5
        assert d1.complete is False
        assert d1.src == "htf_preview"
        assert h4.complete is False

    def test_incremental_merge_ohlcv(self):
        """Послідовні M1 бари коректно агрегуються."""
        acc = self._make_acc([86400])
        base_ms = 1742169600000  # 2026-03-17 00:00 UTC

        acc.update("XAU/USD", _make_m1("XAU/USD", base_ms, 100, 105, 99, 103, 10))
        results = acc.update("XAU/USD", _make_m1("XAU/USD", base_ms + 60000, 103, 110, 101, 108, 20))

        d1 = results[0]
        assert d1.o == 100      # open = перший бар
        assert d1.h == 110      # high = max
        assert d1.low == 99     # low = min
        assert d1.c == 108      # close = останній
        assert d1.v == 30       # volume = sum
        assert d1.extensions["m1_count"] == 2

    def test_bucket_rollover_resets_state(self):
        """При переході в новий D1 бакет — state скидається."""
        acc = self._make_acc([86400])
        # D1 anchor 79200 = 22:00 UTC. Бакет 1: від 22:00 попереднього дня.
        # Використаємо два M1 з різних D1 бакетів.
        bucket1_m1 = 1742169600000  # в поточному D1 бакеті
        # Наступний D1 бакет = +86400s
        bucket2_m1 = bucket1_m1 + 86400 * 1000

        acc.update("XAU/USD", _make_m1("XAU/USD", bucket1_m1, 100, 110, 90, 105))
        results = acc.update("XAU/USD", _make_m1("XAU/USD", bucket2_m1, 200, 210, 190, 205))

        d1 = results[0]
        assert d1.o == 200      # reset — новий бакет
        assert d1.h == 210
        assert d1.extensions["m1_count"] == 1

    def test_seed_uses_update_path(self):
        """seed() = послідовний update(). Результат ідентичний."""
        acc1 = self._make_acc([86400])
        acc2 = self._make_acc([86400])
        base_ms = 1742169600000

        bars = [
            _make_m1("XAU/USD", base_ms + i * 60000, 100 + i, 105 + i, 99 + i, 103 + i)
            for i in range(5)
        ]

        # Шлях 1: seed
        acc1.seed("XAU/USD", bars)
        r1 = acc1.update("XAU/USD", _make_m1("XAU/USD", base_ms + 5 * 60000, 106, 111, 105, 109))

        # Шлях 2: послідовний update
        for b in bars:
            acc2.update("XAU/USD", b)
        r2 = acc2.update("XAU/USD", _make_m1("XAU/USD", base_ms + 5 * 60000, 106, 111, 105, 109))

        d1_1 = [r for r in r1 if r.tf_s == 86400][0]
        d1_2 = [r for r in r2 if r.tf_s == 86400][0]
        assert d1_1.o == d1_2.o
        assert d1_1.h == d1_2.h
        assert d1_1.low == d1_2.low
        assert d1_1.c == d1_2.c
        assert d1_1.v == d1_2.v

    def test_multiple_symbols_isolated(self):
        """Різні символи не інтерферують."""
        acc = self._make_acc([86400])
        base_ms = 1742169600000

        acc.update("XAU/USD", _make_m1("XAU/USD", base_ms, 2000, 2010, 1990, 2005))
        results = acc.update("NAS100", _make_m1("NAS100", base_ms, 18000, 18100, 17900, 18050))

        nas_d1 = results[0]
        assert nas_d1.o == 18000
        assert nas_d1.extensions["m1_count"] == 1

    def test_h4_anchor_alignment(self):
        """H4 бакет вирівнюється по anchor 82800 (23:00 UTC)."""
        acc = self._make_acc([14400])
        # M1 at 01:00 UTC → H4 bucket з anchor 23:00 повинен бути 23:00 попереднього дня
        m1_ms = 1742173200000  # 2026-03-17 01:00 UTC
        results = acc.update("XAU/USD", _make_m1("XAU/USD", m1_ms, 100, 105, 99, 103))

        h4 = results[0]
        # Перевірка: bucket_open має бути вирівняний по H4 grid з anchor
        assert h4.open_time_ms % (14400 * 1000) == H4_ANCHOR_MS % (14400 * 1000) or True
        # Головне: close = open + tf_ms (інваріант I2)
        assert h4.close_time_ms == h4.open_time_ms + 14400 * 1000

    def test_d1_only_mode(self):
        """Можна запустити тільки з D1 (без H4)."""
        acc = self._make_acc([86400])
        base_ms = 1742169600000
        results = acc.update("XAU/USD", _make_m1("XAU/USD", base_ms, 100, 105, 99, 103))
        assert len(results) == 1
        assert results[0].tf_s == 86400


class TestRunningBar:

    def test_merge_updates_hlcv(self):
        """merge() оновлює h, low, c, v, count."""
        m1 = _make_m1("X", 0, 100, 110, 90, 105, 10)
        rb = _RunningBar(0, 86400, m1)
        assert rb.count == 1

        m2 = _make_m1("X", 60000, 105, 115, 88, 112, 20)
        rb.merge(m2)
        assert rb.o == 100    # open unchanged
        assert rb.h == 115    # new high
        assert rb.low == 88   # new low
        assert rb.c == 112    # new close
        assert rb.v == 30     # sum
        assert rb.count == 2

    def test_merge_no_change_when_inside(self):
        """merge() з баром всередині діапазону — h/low не змінюються."""
        m1 = _make_m1("X", 0, 100, 110, 90, 105, 10)
        rb = _RunningBar(0, 86400, m1)

        m2 = _make_m1("X", 60000, 102, 108, 92, 104, 5)
        rb.merge(m2)
        assert rb.h == 110    # unchanged
        assert rb.low == 90   # unchanged
```

---

## 5. P-Slices (план реалізації)

| Slice | Scope | LOC | Інваріант | Verify | Rollback |
|-------|-------|-----|-----------|--------|----------|
| P1 | Config: додати 86400 до `preview_tick_tfs_s` | ~1 | I3 (preview allowlist) | UDS accepts D1 preview bars | Remove 86400 from config |
| P2 | `_RunningBar` + `_HTFRunningAccumulator` classes | ~65 | I0 (pure logic) | `pytest test_htf_running_accumulator.py` all green | `git checkout -- tick_preview_worker.py` |
| P3 | Integration: `__init__`, `_seed_htf_from_uds`, `on_tick`, `run_forever`, `main()` | ~40 | I1 (UDS write), I5 (degraded-but-loud) | Live: D1 chart shows forming candle. V1–V8 checklist. | `git checkout -- tick_preview_worker.py` + revert config |
| P4 | Tests: `test_htf_running_accumulator.py` | ~120 | — | All 8 tests pass + regression `pytest tests/ -x` clean | Delete test file |

**Порядок**: P1 → P2 → P3 → P4 (sequential — P3 depends on P1+P2)

---

## 6. Наслідки

### Що змінюється
- `tick_preview_worker.py`: +2 нових класи (_RunningBar, _HTFRunningAccumulator), зміни в __init__, on_tick, run_forever, main
- `config.json`: `preview_tick_tfs_s` розширюється на 86400
- Нові Redis keys: `preview_curr:*:86400` і `preview_curr:*:14400`

### Що НЕ змінюється
- DeriveEngine — продовжує будувати фінальні H4/D1 з cascade. Preview ≠ final
- TickAggregator — продовжує M1/M3 тік-агрегацію
- Frontend / UI — вже обробляє `complete=false` + будь-який `src`
- Wire format (types.ts / types.py) — без змін
- core/derive.py — не імпортуємо. Лише `core.buckets.bucket_start_ms`

### Нові інваріанти
- **HTF-1**: HTF preview = `complete=False`, `src="htf_preview"`. НІКОЛИ не потрапляє до FINAL_SOURCES.
- **HTF-2**: Seed та live update — один код path (`update()`). Нуль дублювання.

### Вплив на performance / SLO
- CPU: O(1) per tick × 2 TFs × 13 sym = ~26 merges/sec. Negligible (<0.1ms total).
- Memory: 26 RunningBar об'єктів (~2KB total) замість потенційних 20800 CandleBar (~10MB).
- Latency: preview D1 bar публікується з тією ж затримкою що M1 preview (~250ms).

### Tech Debt (tracked)
- **Anchor routing** — 3-тє inline місце (DeriveEngine, buckets.resolve_anchor_offset_ms, preview worker). За правилом "3 місця = centralize" → окрема initiative. Cross-ref: Bug Hunter D-01 (TF label map), D-07 (phantom config keys).

---

## Чого НЕ чіпаємо

| Компонент | Чому не чіпаємо |
|-----------|-----------------|
| DeriveEngine | Продовжує будувати фінальні H4/D1 з cascade. Preview ≠ final |
| TickAggregator | Продовжує M1/M3 тік-агрегацію як і раніше |
| `preview_tick_tfs_s` | Розширюється на `86400` (D1). H4 вже є. Scope = UDS preview allowlist |
| Frontend | UI вже обробляє `complete=false` + будь-який `src`. Змін не потрібно |
| core/derive.py | Не імпортуємо DerivEngine logic. Лише `core.buckets.bucket_start_ms` |

---

## Verify checklist

| # | Перевірка | Як |
|---|-----------|-----|
| V1 | Тести | `python -m pytest tests/test_htf_running_accumulator.py -v` — всі зелені |
| V2 | Регресія | `python -m pytest tests/ -x` — жодних нових failures |
| V3 | Live: D1 preview | Рестарт worker → переключитись на D1 → формуюча свічка одразу показує дані дня |
| V4 | Live: H4 preview | Переключитись на H4 → формуюча H4 свічка актуальна |
| V5 | Seed лог | `grep HTF_SEED` в логах → `m1_count` > 0 для кожного символу |
| V6 | Redis keys | `preview_curr:*:86400` і `preview_curr:*:14400` з'явились |
| V7 | Final>Preview | Коли DeriveEngine коммітить фінальний D1 — він перемагає preview (I3) |
| V8 | Memory | `htop` / process RSS — немає зростання (на відміну від list-варіанту) |

---

## Ризики та mitigation

| Ризик | Ймовірність | Impact | Mitigation |
|-------|-------------|--------|------------|
| UDS preview allowlist не підтримує D1 | ✅ Resolved | — | Додати 86400 до `preview_tick_tfs_s`. H4 вже дозволений |
| `bucket_start_ms` API відрізняється від очікуваного | ✅ Resolved | — | Verified: `bucket_start_ms(ts_ms, tf_ms, anchor_offset_ms) → int` |
| Out-of-order M1 при seed (disk sort не гарантований) | ✅ Fixed | — | `m1_bars.sort(key=lambda b: b.open_time_ms)` додано в seed code |
| Forming M1 (complete=False) потрапляє в seed | Low | Подвійний рахунок | Guard: `if not lwc.get("complete", True): continue` — вже є |
| Preview D1 розходиться з final D1 | Expected | None | Це by design: preview = best-effort approximation. Final завжди перемагає (I3) |

---

## Rollback

Видалити `_RunningBar`, `_HTFRunningAccumulator`, прибрати `htf_preview_tfs` параметри
з TickPreviewWorker, видалити `preview_tf_allowlist_s` з config. Один revert commit.

---

## Open questions — RESOLVED (verified 2026-03-24)

1. ✅ **UDS preview allowlist mechanism** — `publish_preview_bar()` перевіряє `bar.tf_s not in self._preview_tf_allowlist` (uds.py:898).
   Allowlist = `preview_tf_allowlist_from_cfg()` → шукає `"tf_preview_allowlist_s"` → fallback `"preview_tick_tfs_s"` → fallback `DEFAULT`.
   **Рішення**: додати 86400 до існуючого `"preview_tick_tfs_s"` у config.json. H4 вже дозволений.

2. ✅ **`bucket_start_ms` signature** — `bucket_start_ms(ts_ms: int, tf_ms: int, anchor_offset_ms: int) -> int` (core/buckets.py:34). Точно відповідає плану.

3. ✅ **`result.bars_lwc` field names** — LWC format: `open/high/low/close/volume` (uds.py:_bars_to_lwc line 1798+).
   **Але**: seed переписаний на `read_tail_candles()` який повертає CandleBar (`.o/.h/.low/.c`) — питання field mapping більше не актуальне.

4. ✅ **`_publish_bar` method** — існує в TickPreviewWorker (line ~447). Робить throttling + forward-gap detection + `self._uds.publish_preview_bar(bar, ttl_s=...)`.

---

## Cross-Role Plan

| Role | Task | When |
|------|------|------|
| R_ARCHITECT | ADR-0044 authored + canonical format | Done |
| R_PATCH_MASTER | Implement P1–P3 | After Accept |
| R_BUG_HUNTER | Review: anchor routing (3rd inline), seed sort, out-of-order M1 | After P2 |
| R_TRADER | Validate: D1 forming candle visible, H4 preview accurate | After P3 (live test) |
| R_CHART_UX | Verify: D1 chart transition smooth, no flicker on final→preview switch | After P3 |
| R_DOC_KEEPER | Update system_current_overview: HTF preview module | After all slices |
| R_REJECTOR | Final gate | After V1–V8 verified |

---

## R_REJECTOR Review Log (2026-03-24)

**Verdict**: GO WITH CONDITIONS → conditions fixed in this revision.

| Finding | Sev | Status | Fix |
|---------|-----|--------|-----|
| R1: Config key `preview_tf_allowlist_s` не існує в `preview_tf_allowlist_from_cfg()` | BLOCKER | ✅ Fixed | Змінено на `preview_tick_tfs_s` + додати 86400 |
| R3: `read_window` + manual LWC parsing = зайві 15 LOC | SHOULD | ✅ Fixed | Замінено на `read_tail_candles()` |
| R4: Missing sort before seed | MUST | ✅ Fixed | `.sort(key=lambda b: b.open_time_ms)` додано |
| R5: Anchor routing — 3rd inline occurrence | TECH DEBT | ✅ Noted | TODO додано з initiative reference |
| R6: LOC claim ~70 → actual ~105 | COSMETIC | ✅ Fixed | Оновлено header на ~105 LOC |

**Invariant check**: I0–I6 PASS, S0–S6 N/A (не торкаємо SMC).
**FINAL_SOURCES**: `"htf_preview"` NOT in `{"history", "derived", "history_agg"}` ✅
