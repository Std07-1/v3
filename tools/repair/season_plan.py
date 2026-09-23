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
from typing import Any, Dict, Iterable, List, Mapping, Optional, Set, Tuple

from core.model.bars import CandleBar
from core.session_anchor import htf_bucket_start_ms, htf_next_bucket_start_ms
from runtime.store.layers.disk_layer import DiskLayer

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

Buckets = Dict[int, Set[int]]  # TF → відкриття бакетів сезонної сітки


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
