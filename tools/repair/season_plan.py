"""tools/repair/season_plan.py — план заміни похідних part-файлів (ADR-0095 S7.1, спільний з ADR-0098 C3 і ADR-0101 C5).

Лише планування в пам'яті: SSOT читається так, як його бачить читач (`DiskLayer`, вибирач ADR-0094), а результат —
НОВИЙ вміст part-файлів похідних TF в області дії. На диск не пишеться нічого: staging, валідації й заміна — S7.2 і
S7.3. Області дії:

* `derived_from_m1` — усі TF ланцюга `core.derive` (M3, M5, D1 з M1; M15, M30, H1, H4 каскадом) в епосі M1, так само
  як живий DeriveEngine і `tools.rebuild_from_m1`: `derive_bar`, правило якоря символу, сезонний календар
  `calendar_for_symbol`, прихована M1 (`candle_chain.is_display_hidden`) у бакет не йде, фронтир ADR-0097 для D1,
  формуючий хвіст не фіналізується (критерій `rebuild_from_m1`). Зі `changed_m1` (settle ADR-0101 C5) — лише бакети
  кожного TF, що містять змінений ключ M1.
* `h4_from_h1` — H4 сезонної сітки з H1 на диску для епохи до першої M1 (MIGRATION §4.2).
* `d1_rekey` — D1 поза сезонною сіткою в епосі M1: перебудова з M1 на ключ сітки; поза епохою M1 — MANUAL_REVIEW.
* `holes` — сезонні діри M3..H1 (MIGRATION §5).

Правило рядка одне для всіх областей: свій рядок part-файла в області дії ⇔ бакет його open_time_ms (сезонна сітка
TF) — у наборі перебудови цього TF. Набір — зерна областей, піднесені ланцюгом угору (бакет, чиє джерело
перебудовано, перебудовується), без формуючого хвоста. Бакет без нового бару (жодного торгового слота чи джерела)
свого рядка більше не має — DROP у звіті.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from core.derive import DERIVE_CHAIN, DERIVE_ORDER, DERIVE_SOURCE, GenericBuffer, derive_bar
from core.model.bars import CandleBar
from core.model.candle_chain import is_display_hidden
from core.session_anchor import D1_S, htf_bucket_start_ms, htf_next_bucket_start_ms
from runtime.store.layers.disk_layer import DiskLayer
from tools.rebuild_from_m1 import REBUILD_CHUNK_D1_BUCKETS, _bucket_awaits_source, _grid_bucket_opens

log = logging.getLogger("season_plan")

M1_S = 60
M1_MS = M1_S * 1000
H1_S = 3600
SCOPE_DERIVED_FROM_M1 = "derived_from_m1"
SCOPE_H4_FROM_H1 = "h4_from_h1"
SCOPE_D1_REKEY = "d1_rekey"
SCOPE_HOLES = "holes"
SCOPES = (SCOPE_DERIVED_FROM_M1, SCOPE_H4_FROM_H1, SCOPE_D1_REKEY, SCOPE_HOLES)
ALL_TIME = (0, 1 << 62)  # вікно плану без меж, мс UTC
# Читач відбирає ключі за значенням у (since, to]; стелі ключів тут не треба — межу вікна дає `since`
_ALL_KEYS = 1 << 62
# Хвіст M1, що доводить фронтир ADR-0097: два тижні хвилин — прихованих барів поспіль стільки не буває
# (вихідні з перервою ~2.1 доби, зі святом — до ~4)
_VISIBLE_TAIL_PROBE_KEYS = 14 * 1440

Buckets = Dict[int, Set[int]]  # TF → відкриття бакетів сезонної сітки
PlannedBars = Dict[int, Dict[int, Optional[CandleBar]]]  # TF → бакет → новий бар або None


@dataclass
class SymbolContext:
    """Символ плану: правило якоря, сезонний календар, вікно і межі джерела (як їх бачить читач)."""

    symbol: str
    sym_dir: str
    rule: str
    calendar: Any  # SeasonalMarketCalendar: is_trading_minute, summer, winter, season_of
    window: Tuple[int, int]  # [from, to) плану, мс UTC
    m1_head_ms: Optional[int]
    m1_tail_ms: Optional[int]
    h1_tail_ms: Optional[int] = None

    @property
    def source_end_ms(self) -> int:
        """Кінець джерела: крок за хвостом M1 (без M1 — за хвостом H1), не далі вікна — хвіст не фіналізується."""
        tail, step = (self.m1_tail_ms, M1_MS) if self.m1_tail_ms is not None else (self.h1_tail_ms, H1_S * 1000)
        if tail is None:
            return self.window[0]
        return min(self.window[1], tail + step)

    def is_trading(self, minute_ms: int) -> bool:
        return self.calendar.is_trading_minute(minute_ms)

    def bucket_of(self, open_ms: int, tf_s: int) -> int:
        return htf_bucket_start_ms(open_ms, tf_s, self.rule)

    def next_bucket(self, bucket_ms: int, tf_s: int) -> int:
        return htf_next_bucket_start_ms(bucket_ms, tf_s, self.rule)


class SourceReader:
    """Бари SSOT символу так, як їх бачить читач: вибирач дублікатів ADR-0094, без чужих і нефінальних рядків."""

    def __init__(self, data_root: str, symbol: str) -> None:
        self._disk = DiskLayer(data_root)
        self._symbol = symbol
        self.rejected_rows = 0

    def read(self, tf_s: int, lo_ms: int, hi_ms: int) -> List[CandleBar]:
        """Бари TF з open у [lo, hi) за зростанням; рядок без OHLC — гучно відкинутий (I5)."""
        rows, _geom = self._disk.read_window_with_geom(
            self._symbol, tf_s, _ALL_KEYS, since_open_ms=lo_ms - 1, to_open_ms=hi_ms - 1, use_tail=True, final_only=True
        )
        return self._to_bars(rows, tf_s)

    def newest(self, tf_s: int, keys: int) -> List[CandleBar]:
        rows, _geom = self._disk.read_window_with_geom(self._symbol, tf_s, keys, use_tail=True, final_only=True)
        return self._to_bars(rows, tf_s)

    def _to_bars(self, rows: Iterable[Mapping[str, Any]], tf_s: int) -> List[CandleBar]:
        bars: List[CandleBar] = []
        for row in rows:
            try:
                bars.append(_row_to_bar(row, self._symbol, tf_s))
            except (KeyError, TypeError, ValueError):
                self.rejected_rows += 1
                log.warning("SEASON_PLAN_ROW_REJECTED symbol=%s tf_s=%d open_ms=%s — рядок без OHLC у джерело не йде",
                            self._symbol, tf_s, row.get("open_time_ms"))
        return bars


def _row_to_bar(row: Mapping[str, Any], symbol: str, tf_s: int) -> CandleBar:
    extensions = row.get("extensions")
    open_ms = int(row["open_time_ms"])
    return CandleBar(
        symbol=symbol, tf_s=tf_s, open_time_ms=open_ms, close_time_ms=open_ms + tf_s * 1000,
        o=float(row["o"]), h=float(row["h"]), low=float(row["low"] if "low" in row else row["l"]), c=float(row["c"]),
        v=float(row.get("v", 0.0)), complete=True, src=str(row.get("src") or "derived"),
        extensions=dict(extensions) if isinstance(extensions, dict) else {},
    )


# ── Набір перебудови ───────────────────────────────────────────────────────────────────────────────────────────────
def m1_targets() -> List[int]:
    """TF, що будуються прямо з M1 (M3, M5, D1) — з ланцюга `core.derive`, а не літералом."""
    return [tf_s for tf_s, _bars_needed in DERIVE_CHAIN[M1_S]]


def seed_derived_from_m1(ctx: SymbolContext, changed_m1: Optional[Sequence[int]]) -> Buckets:
    """Зерна M3/M5/D1: усі бакети сітки вікна в епосі M1 або лише ті, що містять змінений ключ M1.

    Бакет із торговою хвилиною до першої M1 не зерно: джерело не покриває його початку (дзеркало формуючого хвоста).
    Вищий TF такий бакет однаково перебудує з бачення джерела (диск + план), якщо в ньому є перебудований дочірній.
    """
    if ctx.m1_head_ms is None:
        return {}
    lo = max(ctx.window[0], ctx.m1_head_ms)
    seeds: Buckets = {}
    for tf_s in m1_targets():
        if changed_m1 is None:
            buckets: Iterable[int] = _grid_bucket_opens(ctx.bucket_of(lo, D1_S), ctx.source_end_ms, tf_s, ctx.rule)
        else:
            buckets = {ctx.bucket_of(k, tf_s) for k in changed_m1 if ctx.window[0] <= k < ctx.window[1]}
        seeds[tf_s] = {b for b in buckets if not _bucket_precedes_source(ctx, b, tf_s)}
    return seeds


def _bucket_precedes_source(ctx: SymbolContext, bucket_ms: int, tf_s: int) -> bool:
    """У бакеті є торгова хвилина раніше першої M1 — джерело не покриває його початку."""
    head_ms = ctx.m1_head_ms if ctx.m1_head_ms is not None else bucket_ms
    return any(ctx.is_trading(t) for t in range(bucket_ms, min(ctx.next_bucket(bucket_ms, tf_s), head_ms), M1_MS))


def complete_rebuild_set(ctx: SymbolContext, seeds: Iterable[Buckets]) -> Tuple[Buckets, Dict[int, int]]:
    """Об'єднання зерен, піднесення ланцюгом угору і відсів формуючого хвоста; повертає (набір, відсіяно за TF).

    Бакет, у вікні якого джерело ще не дійшло до останньої торгової хвилини, не перебудовується на жодному TF
    (`rebuild_from_m1._bucket_awaits_source`): його фіналізує живий DeriveEngine, а наявний рядок лишається.
    """
    rebuild: Buckets = {}
    for seed in seeds:
        for tf_s, buckets in seed.items():
            rebuild.setdefault(tf_s, set()).update(buckets)
    for target_tf_s in DERIVE_ORDER:
        source_tf_s = DERIVE_SOURCE[target_tf_s][0]
        lifted = {ctx.bucket_of(b, target_tf_s) for b in rebuild.get(source_tf_s, ())}
        rebuild.setdefault(target_tf_s, set()).update(lifted)
    tail_kept: Dict[int, int] = {}
    for tf_s, buckets in rebuild.items():
        tail = {b for b in buckets if _bucket_awaits_source(b, ctx.next_bucket(b, tf_s), ctx.source_end_ms, ctx.is_trading)}
        buckets -= tail
        tail_kept[tf_s] = len(tail)
    return {tf_s: buckets for tf_s, buckets in rebuild.items() if buckets}, tail_kept


# ── Нові бари ──────────────────────────────────────────────────────────────────────────────────────────────────────
def plan_bars(ctx: SymbolContext, reader: SourceReader, rebuild: Buckets) -> PlannedBars:
    """Новий бар кожного бакета набору: `derive_bar` з джерела таким, яким воно стане після заміни.

    Джерело TF — бари читача, крім бакетів, що самі перебудовуються, плюс їхні нові бари. Порції — суцільні серії
    бакетів D1 по `REBUILD_CHUNK_D1_BUCKETS`: межа D1 — межа бакета кожного похідного TF, тож бакет лежить в одній
    порції. У джерело M1 додається найновіша видима M1 за порцією — фронтир ADR-0097 для D1, як у `rebuild_from_m1`.
    """
    planned: PlannedBars = {}
    visible_tail = _visible_m1_tail(reader)
    for source_tf_s in sorted(DERIVE_CHAIN):
        targets = [tf_s for tf_s, _n in DERIVE_CHAIN[source_tf_s] if rebuild.get(tf_s)]
        if not targets:
            continue
        days = sorted({ctx.bucket_of(b, D1_S) for tf_s in targets for b in rebuild[tf_s]})
        for chunk_lo, chunk_hi in d1_runs(ctx, days):
            buf = _source_buffer(ctx, reader, source_tf_s, (chunk_lo, chunk_hi), rebuild, planned)
            if source_tf_s == M1_S and visible_tail is not None and visible_tail.open_time_ms >= chunk_hi:
                buf.upsert(visible_tail)
            for tf_s in targets:
                for bucket_ms in sorted(b for b in rebuild[tf_s] if chunk_lo <= b < chunk_hi):
                    planned.setdefault(tf_s, {})[bucket_ms] = derive_bar(
                        symbol=ctx.symbol, target_tf_s=tf_s, source_buffer=buf, bucket_open_ms=bucket_ms,
                        is_trading_fn=ctx.is_trading, filter_calendar_pause=True, anchor_rule=ctx.rule,
                    )
    return planned


def d1_runs(ctx: SymbolContext, days: Iterable[int]) -> List[Tuple[int, int]]:
    """[(початок, кінець)) суцільних серій бакетів D1 (відсортованих), не довших за REBUILD_CHUNK_D1_BUCKETS."""
    runs: List[Tuple[int, int]] = []
    count = 0
    for day in days:
        day_end = ctx.next_bucket(day, D1_S)
        if runs and runs[-1][1] == day and count < REBUILD_CHUNK_D1_BUCKETS:
            runs[-1] = (runs[-1][0], day_end)
            count += 1
        else:
            runs.append((day, day_end))
            count = 1
    return runs


def _source_buffer(
    ctx: SymbolContext, reader: SourceReader, source_tf_s: int, chunk: Tuple[int, int], rebuild: Buckets,
    planned: PlannedBars,
) -> GenericBuffer:
    replaced = rebuild.get(source_tf_s, set())
    bars = {
        bar.open_time_ms: bar for bar in reader.read(source_tf_s, chunk[0], chunk[1])
        if not is_display_hidden(bar.extensions) and ctx.bucket_of(bar.open_time_ms, source_tf_s) not in replaced
    }
    for bucket_ms, bar in planned.get(source_tf_s, {}).items():
        if bar is not None and chunk[0] <= bucket_ms < chunk[1]:
            bars[bucket_ms] = bar
    buf = GenericBuffer(source_tf_s, max_keep=len(bars) + 2)
    for open_ms in sorted(bars):
        buf.upsert(bars[open_ms])
    return buf


def _visible_m1_tail(reader: SourceReader) -> Optional[CandleBar]:
    visible = [bar for bar in reader.newest(M1_S, _VISIBLE_TAIL_PROBE_KEYS) if not is_display_hidden(bar.extensions)]
    return visible[-1] if visible else None
