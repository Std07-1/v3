"""Проєкція W1/MN з D1 SSOT для ws_server (ADR-0115 §4.3).

W1/MN не зберігаються: кожне читання складає їх із D1 через UDS (I1) — історія на всю глибину D1, ремонт і settle D1
видно з наступного читання. Модуль нічого не пише. Межі й фінальність — `core.calendar_tf` (pure); тут лише I/O:

- вікно (full і scrollback) — фінальні D1 з диска (`force_disk`: хвіст Redis — 2 000 D1, менше за вікно W1/MN);
- формуюча свічка — фінальні D1 бакета + формуюча D1 з preview-площини;
- оновлення — події D1 (preview і фінали) → перераховані бакети, яких вони торкаються, з курсором потоку D1.

Усі методи blocking — викликати у виконавці UDS. Правило якоря символу — резолвер ws_server (ADR-0095): невиміряна
група піднімає ValueError, і викликач показує її гучно.
"""

from __future__ import annotations

import dataclasses
import logging
import time
from typing import Any, Callable, Dict, FrozenSet, List, Mapping, Optional, Sequence, Tuple

from core.calendar_tf import (
    CALENDAR_TFS,
    CalendarBar,
    CalendarProjection,
    DayBar,
    calendar_bar_close_ms,
    calendar_bucket_start_ms,
    calendar_d1_window_limit,
    calendar_next_bucket_start_ms,
    project_calendar_bars,
)
from core.session_anchor import D1_S
from runtime.store.uds import ReadPolicy, UpdatesResult, UpdatesSpec, WindowResult, WindowSpec
from runtime.ws.candle_map import map_bar_to_candle_v4

_log = logging.getLogger(__name__)

PROJECTED_SRC = "derived"  # проєкція — похідні дані з фінальних D1, як агрегати DeriveEngine
_UPDATES_LIMIT = 500


@dataclasses.dataclass(frozen=True)
class CalendarViewConfig:
    """`config.json:calendar_tfs` (ADR-0115 §4.5)."""

    enabled: bool
    tfs: FrozenSet[int]
    cold_start_bars: Mapping[int, int]
    scrollback_chunk_bars: Mapping[int, int]


def calendar_view_config_from_cfg(cfg: Mapping[str, Any]) -> CalendarViewConfig:
    """Розбір секції; сміття — ValueError з причиною (ws_server пише ERROR і лишає W1/MN вимкненими, I5)."""
    raw = cfg.get("calendar_tfs")
    if raw is None:
        return CalendarViewConfig(False, frozenset(), {}, {})
    if not isinstance(raw, Mapping):
        raise ValueError("calendar_tfs: очікується об'єкт, отримано %s" % type(raw).__name__)
    if int(raw.get("source_tf_s", D1_S)) != D1_S:
        raise ValueError("calendar_tfs.source_tf_s=%r: проєкція визначена лише над D1" % raw.get("source_tf_s"))
    tfs = frozenset(int(tf) for tf in raw.get("tfs_s", []))
    unknown = tfs - CALENDAR_TFS
    if unknown:
        raise ValueError("calendar_tfs.tfs_s: невідомі TF %s (дозволено %s)" % (sorted(unknown), sorted(CALENDAR_TFS)))
    cold = _bars_by_tf(raw, "cold_start_bars_by_tf", tfs)
    chunk = _bars_by_tf(raw, "scrollback_chunk_bars_by_tf", tfs)
    return CalendarViewConfig(enabled=bool(raw.get("enabled", False)), tfs=tfs, cold_start_bars=cold,
                              scrollback_chunk_bars=chunk)


class CalendarTfView:
    """W1/MN над D1 через UDS. `clock_ms` — для тестів; у проді настінний годинник."""

    def __init__(
        self,
        uds: Any,
        rule_for_symbol: Callable[[str], str],
        config: CalendarViewConfig,
        clock_ms: Callable[[], int] = lambda: int(time.time() * 1000),
    ) -> None:
        self._uds = uds
        self._rule_for_symbol = rule_for_symbol
        self._config = config
        self._clock_ms = clock_ms

    @property
    def tfs(self) -> FrozenSet[int]:
        return self._config.tfs if self._config.enabled else frozenset()

    def is_view_tf(self, tf_s: Optional[int]) -> bool:
        return tf_s in self.tfs

    def cold_start_bars(self, tf_s: int) -> int:
        return self._config.cold_start_bars[tf_s]

    def scrollback_chunk_bars(self, tf_s: int) -> int:
        return self._config.scrollback_chunk_bars[tf_s]

    def read_window(self, symbol: str, tf_s: int, limit: int, to_open_ms: Optional[int] = None) -> WindowResult:
        """Фінальні бари W1/MN (I3): `limit` найновіших із ключем ≤ `to_open_ms` (inclusive, як RANGE UDS)."""
        started = time.perf_counter()
        rule = self._rule_for_symbol(symbol)
        d1_to = None
        if to_open_ms is not None:
            d1_to = calendar_next_bucket_start_ms(calendar_bucket_start_ms(to_open_ms, tf_s, rule), tf_s, rule) - 1
        d1_limit = calendar_d1_window_limit(limit, tf_s)
        d1 = self._read_d1(symbol, d1_limit, to_open_ms=d1_to)
        days = _day_bars(d1.bars_lwc, final_only=True)
        # Хвіст: бакет із незавершеною D1 не фінальний, навіть коли його час минув (межа місяця, ≤ 3 хв, ADR-0102)
        forming_day = self._forming_day(symbol) if to_open_ms is None else None
        projection = project_calendar_bars(
            days, forming_day, tf_s=tf_s, rule=rule, now_ms=self._clock_ms(),
            drop_first_bucket=len(d1.bars_lwc) >= d1_limit,
        )
        finals = [b for b in projection.finals if to_open_ms is None or b.open_ms <= to_open_ms][-limit:]
        bars = [_to_lwc(b, symbol, tf_s, rule) for b in finals]
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        _log.info(
            "WS_CALENDAR_VIEW_READ sym=%s tf_s=%d to=%s bars=%d d1=%d ms=%.0f",
            symbol, tf_s, to_open_ms, len(bars), len(d1.bars_lwc), elapsed_ms,
        )
        meta = {"source": "calendar_view", "d1_source": d1.meta.get("source"), "d1_bars": len(d1.bars_lwc)}
        return WindowResult(bars, meta, list(d1.warnings))

    def read_forming(self, symbol: str, tf_s: int, now_ms: int) -> Optional[Dict[str, Any]]:
        """Формуюча свічка W1/MN (complete=false) або None: бакет ще йде або тримає незавершену D1."""
        rule = self._rule_for_symbol(symbol)
        forming_day = self._forming_day(symbol)
        projection = self._project_tail(symbol, tf_s, rule, now_ms, forming_day, (now_ms,))
        return _to_lwc(projection.forming, symbol, tf_s, rule) if projection.forming is not None else None

    def read_updates(self, symbol: str, tf_s: int, since_seq: Optional[int]) -> UpdatesResult:
        """Події D1 (preview + фінали) → події W1/MN перерахованих бакетів; курсор — потоку D1."""
        d1 = self._uds.read_updates(UpdatesSpec(symbol, D1_S, since_seq, _UPDATES_LIMIT, include_preview=True))
        if not d1.events:
            return d1
        rule = self._rule_for_symbol(symbol)
        touched: List[int] = []
        forming_day: Optional[DayBar] = None
        for event in d1.events:
            bar = event.get("bar") if isinstance(event, dict) else None
            day = _day_bar(bar) if isinstance(bar, dict) else None
            if day is None:
                continue
            touched.append(day.open_ms)
            if not bool(event.get("complete")) and (forming_day is None or day.open_ms >= forming_day.open_ms):
                forming_day = day
        if not touched:
            return dataclasses.replace(d1, events=[])
        now_ms = self._clock_ms()
        projection = self._project_tail(symbol, tf_s, rule, now_ms, forming_day, touched)
        keys = {calendar_bucket_start_ms(ts, tf_s, rule) for ts in touched}
        bars = [b for b in _all_bars(projection) if b.open_ms in keys]
        return dataclasses.replace(d1, events=[_event(b, symbol, tf_s, rule) for b in bars])

    def bar_close_ms(self, symbol: str, tf_s: int, now_ms: int) -> Optional[int]:
        """Закриття останнього торгового дня поточного бакета (таймер «3д 04г», ADR-0115 §4.4); минуло — None."""
        rule = self._rule_for_symbol(symbol)
        close_ms = calendar_bar_close_ms(calendar_bucket_start_ms(now_ms, tf_s, rule), tf_s, rule)
        return close_ms if close_ms > now_ms else None

    def _project_tail(
        self,
        symbol: str,
        tf_s: int,
        rule: str,
        now_ms: int,
        forming_day: Optional[DayBar],
        moments_ms: Sequence[int],
    ) -> CalendarProjection:
        """Проєкція бакетів від найстаршого з моментів `moments_ms` (і формуючої D1) до кінця даних.

        Вікно D1 — від ключа найстаршого бакета до «зараз»: читач бере НАЙНОВІШІ `limit` D1 після `since`, тож запас
        рахується до now_ms, а не до найновішого моменту (пізній фінал старого бакета не має загубити його D1).
        """
        anchors = list(moments_ms) + ([forming_day.open_ms] if forming_day is not None else [])
        oldest_key = min(calendar_bucket_start_ms(ts, tf_s, rule) for ts in anchors)
        span_buckets = max(1, (max(anchors + [now_ms]) - oldest_key) // (tf_s * 1000) + 2)
        d1 = self._read_d1(symbol, calendar_d1_window_limit(span_buckets, tf_s), since_open_ms=oldest_key - 1)
        days = _day_bars(d1.bars_lwc, final_only=True)
        return project_calendar_bars(days, forming_day, tf_s=tf_s, rule=rule, now_ms=now_ms)

    def _forming_day(self, symbol: str) -> Optional[DayBar]:
        preview = self._uds.read_preview_window(symbol, D1_S, 1)
        preview_warnings = list(getattr(preview, "warnings", None) or [])
        if preview_warnings:
            # Без preview формуюча W1/MN — лише з фінальних D1: гучно, крім порожньої площини закритого ринку
            level = logging.DEBUG if set(preview_warnings) <= {"preview_empty"} else logging.WARNING
            _log.log(level, "WS_CALENDAR_VIEW_PREVIEW_WARNINGS sym=%s warnings=%s", symbol, preview_warnings)
        for bar in reversed(getattr(preview, "bars_lwc", None) or []):
            if isinstance(bar, dict) and not bar.get("complete", False):
                return _day_bar(bar)
        return None

    def _read_d1(
        self,
        symbol: str,
        limit: int,
        *,
        to_open_ms: Optional[int] = None,
        since_open_ms: Optional[int] = None,
    ) -> WindowResult:
        spec = WindowSpec(symbol=symbol, tf_s=D1_S, limit=limit, since_open_ms=since_open_ms, to_open_ms=to_open_ms)
        return self._uds.read_window(spec, ReadPolicy(force_disk=True, disk_policy="explicit"))


def _bars_by_tf(raw: Mapping[str, Any], key: str, tfs: FrozenSet[int]) -> Dict[int, int]:
    table = raw.get(key, {})
    out: Dict[int, int] = {}
    for tf_s in tfs:
        value = int(table.get(str(tf_s), 0)) if isinstance(table, Mapping) else 0
        if value <= 0:
            raise ValueError("calendar_tfs.%s[%d]: потрібне додатне число барів" % (key, tf_s))
        out[tf_s] = value
    return out


def _day_bar(bar: Mapping[str, Any]) -> Optional[DayBar]:
    """D1 dict (LWC чи короткий формат) → DayBar; display-прихований чи битий бар → None (як у графіку D1)."""
    candle = map_bar_to_candle_v4(dict(bar), tf_s=D1_S)
    if candle is None:
        return None
    return DayBar(open_ms=candle["t_ms"], o=candle["o"], h=candle["h"], low=candle["l"], c=candle["c"], v=candle["v"])


def _day_bars(bars: Sequence[Mapping[str, Any]], *, final_only: bool) -> List[DayBar]:
    days: List[DayBar] = []
    for bar in bars:
        if final_only and not bar.get("complete", True):
            continue
        day = _day_bar(bar)
        if day is not None and (not days or day.open_ms > days[-1].open_ms):
            days.append(day)
    return days


def _all_bars(projection: CalendarProjection) -> Tuple[CalendarBar, ...]:
    return projection.finals + ((projection.forming,) if projection.forming is not None else ())


def _to_lwc(bar: CalendarBar, symbol: str, tf_s: int, rule: str) -> Dict[str, Any]:
    return {
        "symbol": symbol,
        "tf_s": tf_s,
        "time": bar.open_ms // 1000,
        "open_time_ms": bar.open_ms,
        "close_time_ms": calendar_next_bucket_start_ms(bar.open_ms, tf_s, rule),
        "open": bar.o,
        "high": bar.h,
        "low": bar.low,
        "close": bar.c,
        "volume": bar.v,
        "src": PROJECTED_SRC,
        "complete": bar.complete,
    }


def _event(bar: CalendarBar, symbol: str, tf_s: int, rule: str) -> Dict[str, Any]:
    return {
        "key": {"symbol": symbol, "tf_s": tf_s, "open_ms": bar.open_ms},
        "bar": _to_lwc(bar, symbol, tf_s, rule),
        "complete": bar.complete,
        "source": PROJECTED_SRC,
        "event_ts": None,
    }
