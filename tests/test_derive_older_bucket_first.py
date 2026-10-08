"""Похідні бакети закриваються від старшого до новішого і будуються з наявних хвилин (ADR-0097, зріз M5 08.10).

Інцидент USD/JPY 07.10 21:00–21:40 UTC (ролловер FX, хвилини без угод): добір після активації приніс M1 одним пакетом.
M5 21:00 (бракує 21:01 і останньої 21:04) тригера не має; M5 21:05 (лише 21:09) і M5 21:20 (лише 21:24) бюджет
відкидав; overdue після пакета комітив M5 21:10 першим, і M5 21:00 ставав `stale` назавжди. M15 21:00 зібрано з
одного M5 21:10: o=158.025 l=158.023 замість TV o=158.051 l=157.992. Фікстура — справжні хвилини з SSOT проду.
"""
from __future__ import annotations

import datetime as dt
from types import SimpleNamespace
from typing import Dict, List, Optional, Tuple

from core.model.bars import CandleBar
from core.session_anchor import RULE_NY_CLOSE_FX
from runtime.ingest.derive_engine import DeriveEngine

SYM = "USD/JPY"
M1_MS = 60_000
UTC = dt.timezone.utc

# (хвилина 07.10 UTC, o, h, l, c, v) — M1 USD/JPY з data_v3 проду
_M1_0710 = [
    ("20:45", 158.078, 158.084, 158.077, 158.083, 18), ("20:46", 158.083, 158.084, 158.083, 158.084, 9),
    ("20:47", 158.084, 158.087, 158.082, 158.083, 9), ("20:48", 158.083, 158.085, 158.083, 158.083, 9),
    ("20:49", 158.083, 158.089, 158.083, 158.086, 25), ("20:50", 158.086, 158.087, 158.079, 158.081, 43),
    ("20:51", 158.081, 158.091, 158.081, 158.089, 18), ("20:52", 158.089, 158.089, 158.087, 158.088, 9),
    ("20:53", 158.088, 158.088, 158.086, 158.088, 12), ("20:54", 158.088, 158.089, 158.085, 158.086, 16),
    ("20:55", 158.086, 158.089, 158.079, 158.088, 42), ("20:56", 158.088, 158.096, 158.087, 158.088, 37),
    ("20:57", 158.088, 158.088, 158.080, 158.088, 52), ("20:58", 158.088, 158.089, 158.081, 158.081, 64),
    ("20:59", 158.081, 158.091, 158.051, 158.051, 144), ("21:00", 158.051, 158.051, 157.992, 158.025, 41),
    ("21:02", 158.025, 158.025, 158.013, 158.013, 2), ("21:03", 158.013, 158.023, 158.013, 158.023, 8),
    ("21:09", 158.023, 158.028, 158.023, 158.025, 9), ("21:12", 158.025, 158.025, 158.023, 158.025, 36),
    ("21:13", 158.025, 158.027, 158.025, 158.026, 17), ("21:16", 158.026, 158.026, 158.026, 158.026, 9),
    ("21:17", 158.026, 158.030, 158.026, 158.030, 10), ("21:18", 158.030, 158.030, 158.030, 158.030, 1),
    ("21:19", 158.030, 158.030, 158.030, 158.030, 2), ("21:24", 158.030, 158.030, 158.030, 158.030, 7),
    ("21:25", 158.030, 158.030, 158.030, 158.030, 9), ("21:26", 158.030, 158.030, 158.030, 158.030, 12),
    ("21:27", 158.030, 158.030, 158.030, 158.030, 18), ("21:28", 158.030, 158.030, 158.030, 158.030, 21),
    ("21:30", 158.030, 158.033, 158.030, 158.033, 59), ("21:31", 158.033, 158.033, 158.031, 158.033, 29),
    ("21:32", 158.033, 158.033, 158.031, 158.032, 18), ("21:33", 158.032, 158.036, 158.029, 158.036, 30),
    ("21:34", 158.036, 158.037, 158.030, 158.036, 45), ("21:35", 158.036, 158.038, 158.030, 158.038, 22),
    ("21:36", 158.038, 158.038, 158.033, 158.034, 13), ("21:37", 158.034, 158.035, 158.033, 158.033, 13),
    ("21:38", 158.033, 158.036, 158.032, 158.036, 46), ("21:39", 158.036, 158.037, 158.035, 158.035, 15),
]


def _ms(hhmm: str) -> int:
    hour, minute = (int(x) for x in hhmm.split(":"))
    return int(dt.datetime(2026, 10, 7, hour, minute, tzinfo=UTC).timestamp() * 1000)


def _m1_bars() -> List[CandleBar]:
    return [
        CandleBar(symbol=SYM, tf_s=60, open_time_ms=_ms(t), close_time_ms=_ms(t) + M1_MS,
                  o=o, h=h, low=low, c=c, v=float(v), complete=True, src="history")
        for t, o, h, low, c, v in _M1_0710
    ]


class _FxWeekday:
    """FX 24x5 посеред тижня: торгова кожна хвилина (денної перерви нема)."""

    def is_trading_minute(self, ms: int) -> bool:
        return True


class _WatermarkUds:
    """Писар SSOT з правилом UDS: open == watermark TF → duplicate, open < watermark → stale."""

    def __init__(self) -> None:
        self.bars: Dict[Tuple[int, int], CandleBar] = {}
        self.watermark: Dict[int, int] = {}
        self.drops: List[Tuple[int, int, str]] = []

    def commit_final_bar(self, bar: CandleBar):
        wm = self.watermark.get(bar.tf_s)
        if wm is not None and bar.open_time_ms <= wm:
            reason = "duplicate" if bar.open_time_ms == wm else "stale"
            self.drops.append((bar.tf_s, bar.open_time_ms, reason))
            return SimpleNamespace(ok=False, reason=reason)
        self.watermark[bar.tf_s] = bar.open_time_ms
        self.bars[(bar.tf_s, bar.open_time_ms)] = bar
        return SimpleNamespace(ok=True, reason=None)


def _engine(uds: _WatermarkUds) -> DeriveEngine:
    tfs = {180, 300, 900, 1800, 3600}
    engine = DeriveEngine(symbols=[SYM], anchor_rules={SYM: RULE_NY_CLOSE_FX}, calendars={SYM: _FxWeekday()},
                          cascade_tfs_s=tfs, commit_tfs_s=tfs)
    engine.register_symbol_uds(SYM, uds)
    return engine


def _aggregate(minutes: List[CandleBar], open_ms: int, tf_s: int) -> Optional[Tuple[float, float, float, float, float]]:
    inside = [b for b in minutes if open_ms <= b.open_time_ms < open_ms + tf_s * 1000]
    if not inside:
        return None
    return (inside[0].o, max(b.h for b in inside), min(b.low for b in inside), inside[-1].c, sum(b.v for b in inside))


def _ohlcv(bar: CandleBar) -> Tuple[float, float, float, float, float]:
    return (bar.o, bar.h, bar.low, bar.c, bar.v)


def test_batch_catchup_builds_every_bucket_with_minutes_equal_to_m1_aggregate() -> None:
    uds = _WatermarkUds()
    engine = _engine(uds)
    minutes = _m1_bars()

    for bar in minutes:  # пакет добору: M1 комітяться підряд, overdue — лише після пакета
        engine.on_bar(bar)
    engine.check_overdue_buckets(_ms("21:40") + 30_000, frontier_ms_by_symbol={SYM: _ms("21:40")})

    assert [d for d in uds.drops if d[2] == "stale"] == []
    expected_m5 = [_ms("20:45") + i * 300_000 for i in range(11)]  # 20:45 … 21:35: у кожному є хвилина
    assert sorted(o for tf, o in uds.bars if tf == 300) == expected_m5
    for (tf_s, open_ms), bar in uds.bars.items():
        assert _ohlcv(bar) == _aggregate(minutes, open_ms, tf_s), (tf_s, open_ms)
    # M15 21:00 — OHLC бару TV FX:USDJPY (звірка 08.10)
    assert _ohlcv(uds.bars[(900, _ms("21:00"))])[:4] == (158.051, 158.051, 157.992, 158.026)
    assert (1800, _ms("21:00")) in uds.bars


def test_m5_past_budget_carries_thin_session_mark() -> None:
    uds = _WatermarkUds()
    engine = _engine(uds)
    for bar in _m1_bars():
        engine.on_bar(bar)

    only_2109 = uds.bars[(300, _ms("21:05"))]
    assert (only_2109.extensions["source_count"], only_2109.extensions["expected_count"]) == (1, 5)
    assert "thin_session" in only_2109.extensions["partial_reasons"]


def test_overdue_commits_pending_buckets_oldest_first() -> None:
    uds = _WatermarkUds()
    engine = _engine(uds)
    engine.warmup_bars([b for b in _m1_bars() if b.open_time_ms < _ms("20:55")])

    engine.check_overdue_buckets(_ms("20:56"), frontier_ms_by_symbol={SYM: _ms("20:55")})

    m5 = [o for tf, o in uds.bars if tf == 300]
    assert m5 == [_ms("20:45"), _ms("20:50")]
    assert uds.drops == []
