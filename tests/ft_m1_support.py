"""Будівники даних для тестів first_tick_m1 (ADR-0096 §3.3 B). Не тест-модуль: імпорт `from ft_m1_support import …`.

Числа — з ADR-0096 §1.3/§1.4: XAU/USD 26.07.2026 22:01 UTC (перша M1 сесії) у PREVIOUS_CLOSE
O=L=4055.42, у FIRST_TICK O 4089.98 L 4086.33; «запечений» рядок 13.09 22:01 — o 4346.23 > h 4337.69.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from runtime.ingest.broker.fxcm.provider import is_open_outside_range
from runtime.ingest.tick_common import calendar_from_group
from tools.repair.first_tick_m1.common import day_start_ms, sym_dir

UTC = dt.timezone.utc
SYMBOL = "XAU/USD"
DAY = dt.date(2026, 7, 26)  # неділя: сесія відкривається о 22:00 UTC
DAY_MS = day_start_ms(DAY)
MINUTE = 60_000
CFG_CALENDAR = {
    "market_weekend_open_dow": 6, "market_weekend_open_hm": "22:00",
    "market_weekend_close_dow": 4, "market_weekend_close_hm": "20:45",
    "market_daily_break_start_hm": "21:00", "market_daily_break_end_hm": "22:00",
}


def at(day: dt.date, hour: int, minute: int) -> int:
    return day_start_ms(day) + (hour * 60 + minute) * MINUTE


def calendar():
    return calendar_from_group(CFG_CALENDAR)


def make_cfg(data_root: str, symbols=("XAU/USD", "XAG/USD")) -> dict:
    return {
        "symbols": list(symbols),
        "data_root": data_root,
        "market_calendar_by_group": {"cfd_us_22_23": dict(CFG_CALENDAR)},
        "market_calendar_symbol_groups": {sym: "cfd_us_22_23" for sym in symbols},
        "binance": {"enabled": False, "symbols": ["BTCUSDT"]},
    }


def raw_row(open_ms: int, o: float, h: float, low: float, c: float, volume: int = 10) -> dict:
    """Рядок так, як його віддає `FxcmHistoryProvider.fetch_m1_raw_range` (Ask = Bid + спред)."""
    return {"open_time_ms": open_ms, "BidOpen": o, "BidHigh": h, "BidLow": low, "BidClose": c,
            "AskOpen": o + 0.25, "AskHigh": h + 0.25, "AskLow": low + 0.25, "AskClose": c + 0.25, "Volume": volume}


def staged_row(open_ms: int, o: float, h: float, low: float, c: float, volume: int = 10) -> dict:
    row = raw_row(open_ms, o, h, low, c, volume)
    row["raw_open_not_tick"] = is_open_outside_range(o, h, low)
    return row


def fetch_meta(**overrides) -> dict:
    meta = {
        "request": {"date_from_utc": "2026-07-25T23:59:00Z", "date_to_utc": "2026-07-27T00:00:00Z",
                    "quotes_count": -1},
        "rows_outside_day_dropped": 0, "fetched_at_utc": "2026-09-19T12:00:00Z", "run_id": "20260919T120000Z-1",
        "call_seq": 1, "call_duration_s": 1.5, "sdk": {"python": "3.7.0", "forexconnect": "1.6.43"},
    }
    meta.update(overrides)
    return meta


def ssot_bar(open_ms: int, o: float, h: float, low: float, c: float, v: float = 10.0, symbol: str = SYMBOL,
             **extra) -> dict:
    """Бар part-файла у порядку ключів `CandleBar.to_dict` (+ extra наприкінці)."""
    bar = {"symbol": symbol, "tf_s": 60, "open_time_ms": open_ms, "close_time_ms": open_ms + MINUTE, "o": o, "h": h,
           "low": low, "c": c, "v": v, "complete": True, "src": "history"}
    bar.update(extra)
    return bar


def line(bar: dict) -> str:
    """Рядок так, як його пише `ssot_jsonl` (compact, ensure_ascii=False)."""
    return json.dumps(bar, separators=(",", ":"), ensure_ascii=False)


def write_part(data_root, day: dt.date, lines, symbol: str = SYMBOL) -> Path:
    directory = Path(data_root) / sym_dir(symbol) / "tf_60"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / ("part-%s.jsonl" % day.strftime("%Y%m%d"))
    path.write_bytes("".join(text + "\n" for text in lines).encode("utf-8"))
    return path


class Scenario:
    """SSOT у PREVIOUS_CLOSE і staging у FIRST_TICK для тих самих хвилин — вхід plan/apply/verify.

    Кожна хвилина: перший тік `first = prev_c + d`, close `c`, діапазон тіків навколо; PREV-бар має o = prev_c і
    H/L, розтягнуті до нього (як писав провайдер до ADR-0096), staging — сирі тіки.
    """

    _D = (0.37, -0.21, 0.44, -0.18, 0.29)
    _E = (0.12, -0.33, 0.25, 0.08, -0.15, 0.4)

    def __init__(self, root, symbol: str = SYMBOL):
        self.root = Path(root)
        self.data = self.root / "data"
        self.staging = self.root / "staging"
        self.symbol = symbol
        self.parts: dict = {}
        self.staged: dict = {}

    def session(self, day: dt.date, hour: int, minute: int, n: int, prev_close: float, stage: bool = True) -> float:
        for i in range(n):
            open_ms = at(day, hour, minute) + i * MINUTE
            first = round(prev_close + self._D[i % 5], 2)
            close = round(first + self._E[i % 6], 2)
            high, low = round(max(first, close) + 0.11, 2), round(min(first, close) - 0.13, 2)
            self.parts.setdefault(day, []).append(
                line(ssot_bar(open_ms, prev_close, max(high, prev_close), min(low, prev_close), close, float(20 + i),
                              symbol=self.symbol)))
            if stage:
                self.staged.setdefault(day, []).append(staged_row(open_ms, first, high, low, close, 20 + i))
            prev_close = close
        return prev_close

    def add(self, day: dt.date, bar: dict = None, row: dict = None, text: str = None) -> None:
        if bar is not None or text is not None:
            self.parts.setdefault(day, []).append(text if text is not None else line(bar))
        if row is not None:
            self.staged.setdefault(day, []).append(row)

    def write(self) -> "Scenario":
        from tools.repair.first_tick_m1.common import request_window
        from tools.repair.first_tick_m1.staging import write_day_atomic

        for day, lines in self.parts.items():
            write_part(self.data, day, lines, symbol=self.symbol)
        for day, rows in self.staged.items():
            ordered = sorted(rows, key=lambda r: r["open_time_ms"])
            write_day_atomic(self.staging, self.symbol, day, ordered, fetch_meta(request=request_window(day)))
        return self

    def cfg(self) -> dict:
        return make_cfg(str(self.data))


def tree_digest(root) -> dict:
    """{відносний шлях: байти} усього дерева — доказ «нічого не змінено»."""
    root = Path(root)
    if not root.exists():
        return {}
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def signal_during_final_write(monkeypatch, report_format: str) -> dict:
    """`write_json_atomic`, що в мить першого фінального запису звіту `report_format` (є finished_at_utc) викликає
    поточний обробник SIGTERM — так, ніби supervisor надіслав сигнал саме тоді. Повертає {"handler": ...}: обробник,
    що стояв у цю мить (поза `with StopSignals` це вже SIG_DFL, і сигнал убив би процес зі звітом `running`)."""
    import signal

    from tools.repair.first_tick_m1 import common

    real_write, seen = common.write_json_atomic, {}

    def write(path, obj):
        if not seen and isinstance(obj, dict) and obj.get("format") == report_format and obj.get("finished_at_utc"):
            handler = seen["handler"] = signal.getsignal(signal.SIGTERM)
            if callable(handler):
                handler(signal.SIGTERM, None)
        real_write(path, obj)

    monkeypatch.setattr(common, "write_json_atomic", write)
    return seen
