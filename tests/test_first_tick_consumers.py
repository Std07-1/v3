"""Споживачі, які мовчки спирались на PREVIOUS_CLOSE і після ADR-0096 (ціна відкриття = перший тік) змінили б поведінку.

Адверсаріальне ревʼю гілки знайшло два таких місця:
- `core.smc.swings.compute_atr` рахував TR як `h − low`. У режимі PREVIOUS_CLOSE H/L свічки вже містили
  попередній close, тож це випадково дорівнювало справжньому TR; з першим тіком розрив на відкритті сесії
  лишається МІЖ свічками — ATR тихо менший за TradingView (H1 до −25% після гепу NAS100 13.09).
- `runtime.ws.candle_map` ховав будь-яку пласку свічку з v ≤ 10. У PREVIOUS_CLOSE однотікова хвилина мала
  open = попередній close і пласкою не була; з першим тіком вона O=H=L=C і зникала б з графіка, хоча інжест
  за календарем записав її як торгову (`trading_flat`).
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


def test_single_tick_trading_minute_stays_on_the_chart():
    """Інжест позначив хвилину торговою — свічка-риска мусить дійти до графіка, як у TV."""
    candle = map_bar_to_candle_v4(_flat_m1({"trading_flat": True}), tf_s=60)
    assert candle is not None and candle["o"] == candle["c"] == 7686.13


def test_unmarked_flat_bar_is_still_hidden():
    """Контроль: без маркера лишається старий фільтр артефактів паузи."""
    assert map_bar_to_candle_v4(_flat_m1(), tf_s=60) is None


def test_calendar_pause_flat_is_hidden_even_if_marked_trading():
    """Маркер паузи сильніший: суперечливий бар не показуємо."""
    assert map_bar_to_candle_v4(_flat_m1({"calendar_pause_flat": True, "trading_flat": True}), tf_s=60) is None
