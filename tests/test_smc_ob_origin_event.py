"""
tests/test_smc_ob_origin_event.py — ADR-0113 Ф0 (K6): OB знає бар власного зламу структури.

Наратив шукає підтвердження зони ПІСЛЯ цього зламу: OB-свічка передує власному BOS/CHoCH, тож «злам після
свічки-якоря» завжди знаходив той самий злам, що зону створив.
"""

from __future__ import annotations

from core.model.bars import CandleBar
from core.smc.config import SmcConfig
from core.smc.order_blocks import detect_order_blocks
from core.smc.types import SmcSwing

SYM = "XAU/USD"
TF = 900
T0 = 1_700_000_000_000
BAR_MS = TF * 1000


def _bar(i: int, o: float, h: float, low: float, c: float) -> CandleBar:
    open_ms = T0 + i * BAR_MS
    return CandleBar(
        symbol=SYM, tf_s=TF, open_time_ms=open_ms, close_time_ms=open_ms + BAR_MS,
        o=o, h=h, low=low, c=c, v=1000.0, complete=True, src="derived",
    )


def test_ob_records_its_creating_break():
    """Бичачий BOS на барі 10 створює OB з останньої ведмежої свічки перед імпульсом (бар 8)."""
    bars = [_bar(i, 100.0, 100.5, 99.5, 100.2) for i in range(8)]
    bars.append(_bar(8, 100.4, 100.6, 99.6, 99.8))  # ведмежа — OB
    bars.append(_bar(9, 99.9, 103.0, 99.8, 102.8))  # імпульс
    bars.append(_bar(10, 102.8, 106.0, 102.6, 105.8))  # злам
    bars.append(_bar(11, 105.8, 106.2, 105.0, 105.5))
    bos = SmcSwing(id="bos", symbol=SYM, tf_s=TF, kind="bos_bull", price=103.0,
                   time_ms=bars[10].open_time_ms, confirmed=True)

    zones = detect_order_blocks(bars, [bos], SmcConfig(), atr=1.0)

    obs = [z for z in zones if z.kind == "ob_bull"]
    assert len(obs) == 1
    assert obs[0].anchor_bar_ms == bars[8].open_time_ms
    assert obs[0].origin_event_ms == bos.time_ms
    assert "origin_event_ms" not in obs[0].to_wire()  # S6: wire не змінено
