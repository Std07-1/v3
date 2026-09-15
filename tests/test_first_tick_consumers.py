"""Споживачі, які мовчки спирались на PREVIOUS_CLOSE і після ADR-0096 (ціна відкриття = перший тік) змінили б поведінку.

Адверсаріальне ревʼю гілки знайшло два таких місця:
- `core.smc.swings.compute_atr` рахував TR як `h − low`. У режимі PREVIOUS_CLOSE H/L свічки вже містили
  попередній close, тож це випадково дорівнювало справжньому TR; з першим тіком розрив на відкритті сесії
  лишається МІЖ свічками — ATR тихо менший за TradingView (H1 до −25% після гепу NAS100 13.09).
- `runtime.ws.candle_map` ховає пласку свічку з v ≤ 10. З першим тіком однотікова хвилина стає O=H=L=C і таких
  більше (засів FIRST_TICK 15.09: GER30 ~1% хвилин, EUSTX50 8.8%). Рішення власника 15.09: TradingView пласких
  барів не показує — графік їх ховає й надалі, навіть з маркером `trading_flat`; у SSOT і похідних TF бар лишається.
"""
from __future__ import annotations

import pytest

from core.model.bars import CandleBar
from core.smc.swings import compute_atr
from runtime.ws.candle_map import map_bar_to_candle_v4

H1_MS = 3_600_000
BASE = 1_789_000_000_000 // H1_MS * H1_MS


def _bar(i, o, h, low, c):
    return CandleBar(symbol="NAS100", tf_s=3600, open_time_ms=BASE + i * H1_MS, close_time_ms=BASE + (i + 1) * H1_MS,
                     o=o, h=h, low=low, c=c, v=100.0, complete=True, src="history")


def test_atr_counts_the_gap_from_previous_close():
    """Геп угору: попередній close 100, свічка 110..115 — TR = 15 (як ta.tr), а не 5."""
    bars = [_bar(0, 99.0, 101.0, 98.0, 100.0), _bar(1, 111.0, 115.0, 110.0, 114.0)]
    assert compute_atr(bars, period=1) == pytest.approx(15.0)


def test_atr_counts_a_gap_down_too():
    bars = [_bar(0, 99.0, 101.0, 98.0, 100.0), _bar(1, 89.0, 90.0, 85.0, 86.0)]
    assert compute_atr(bars, period=1) == pytest.approx(15.0)


def test_atr_without_gaps_is_unchanged():
    """Контроль: close усередині наступної свічки — TR = h − low, як і до зміни (PREVIOUS_CLOSE-дані не зсуваються)."""
    bars = [_bar(i, 100.0, 104.0, 99.0, 102.0) for i in range(20)]
    assert compute_atr(bars, period=14) == pytest.approx(5.0)


def test_atr_oldest_bar_has_no_previous_close():
    assert compute_atr([_bar(0, 100.0, 103.0, 99.0, 101.0)], period=14) == pytest.approx(4.0)


def _flat_m1(extensions=None, v=1.0):
    bar = {"o": 7686.13, "h": 7686.13, "low": 7686.13, "c": 7686.13, "v": v,
           "open_time_ms": 1_789_000_020_000, "tf_s": 60, "src": "history", "complete": True}
    if extensions is not None:
        bar["extensions"] = extensions
    return bar


def test_single_tick_trading_minute_is_hidden_like_tradingview():
    """Однотікова торгова хвилина (маркер інжесту `trading_flat`) на графік не йде: TV пласких барів не показує."""
    assert map_bar_to_candle_v4(_flat_m1({"trading_flat": True}), tf_s=60) is None


def test_unmarked_flat_bar_is_hidden():
    assert map_bar_to_candle_v4(_flat_m1(), tf_s=60) is None


def test_flat_bar_with_real_volume_stays_on_the_chart():
    """Контроль межі фільтра: пласка хвилина з v > 10 — не артефакт, лишається."""
    candle = map_bar_to_candle_v4(_flat_m1({"trading_flat": True}, v=11.0), tf_s=60)
    assert candle is not None and candle["o"] == candle["c"] == 7686.13


def test_calendar_pause_flat_is_hidden_even_if_marked_trading():
    """Маркер паузи сильніший: суперечливий бар не показуємо."""
    assert map_bar_to_candle_v4(_flat_m1({"calendar_pause_flat": True, "trading_flat": True}), tf_s=60) is None
