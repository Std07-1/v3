"""Споживачі, чия коректність не має залежати від режиму ціни відкриття (ADR-0096 → ADR-0100).

Два місця мовчки спирались на геометрію барів епохи PREVIOUS_CLOSE; ADR-0096 їх виправив, а ADR-0100 (повернення
до PREVIOUS_CLOSE = паритет з TV FX:) обидва виправлення ЛИШАЄ — вони не «під FIRST_TICK», а під будь-яке джерело
бару. Тести тримають саме цю незалежність:
- `core.smc.swings.compute_atr` рахував TR як `h − low`. На барах FXCM у PREVIOUS_CLOSE H/L уже містять попередній
  close, тож результат той самий; але там, де open ≠ попередній close (бари епохи FIRST_TICK у SSOT, перебудовані
  з тіків, Binance), `h − low` губить геп — ATR тихо менший за TradingView (H1 до −25% на гепі NAS100 13.09).
  Формула тепер канонічна `ta.tr`, як у TV, незалежно від походження бару.
- `runtime.ws.candle_map` ховав пласку свічку з v ≤ 10 за геометрією, а на HTF — за днем тижня. Торгова хвилина
  буває законно пласка (вимір 21.09 на даних епохи FIRST_TICK: EUSTX50 5287/106293 M1, 4.97%; GER30 675), і фільтр
  видаляв справжні часові точки — «рваний графік». Рішення власника 21.09 (аудит + контроль Binance/LWC): display
  ховає ЛИШЕ явний маркер `calendar_pause_flat`; існування бару визначає upstream, не геометрія.
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
    """Однотікова торгова хвилина (маркер trading_flat) — справжній бакет: лишається в серії, як у Binance/TV."""
    candle = map_bar_to_candle_v4(_flat_m1({"trading_flat": True}), tf_s=60)
    assert candle is not None and candle["o"] == candle["c"] == 7686.13


def test_unmarked_flat_bar_stays_display_does_not_reclassify():
    """Без маркера display не перекласифіковує за геометрією: старі засіяні в сесії пласкі хвилини видимі."""
    assert map_bar_to_candle_v4(_flat_m1(), tf_s=60) is not None


def test_flat_bar_in_the_5_to_10_volume_band_stays():
    """Ловить split-brain порогів: інжест (поріг 4) вважав v=7 звичайним баром, а display (поріг 10) ховав."""
    assert map_bar_to_candle_v4(_flat_m1(v=7.0), tf_s=60) is not None


def test_flat_bar_with_real_volume_stays_on_the_chart():
    candle = map_bar_to_candle_v4(_flat_m1({"trading_flat": True}, v=11.0), tf_s=60)
    assert candle is not None and candle["o"] == candle["c"] == 7686.13


@pytest.mark.parametrize("tf_s", [60, 14400, 86400])
def test_calendar_pause_flat_is_hidden_on_every_tf(tf_s):
    """Єдине правило ховання — явний маркер артефакту паузи, однаково для M1, H4 і D1."""
    assert map_bar_to_candle_v4(_flat_m1({"calendar_pause_flat": True}), tf_s=tf_s) is None
    assert map_bar_to_candle_v4(_flat_m1({"calendar_pause_flat": True, "trading_flat": True}), tf_s=tf_s) is None


@pytest.mark.parametrize("tf_s, weekday_open_ms", [
    (14400, 1_789_077_600_000),  # 2026-09-11 06:00 UTC, п'ятниця — раніше ховався за днем тижня
    (86400, 1_788_987_600_000),  # 2026-09-10 05:00 UTC, четвер
])
def test_flat_final_htf_bar_is_not_judged_by_weekday(tf_s, weekday_open_ms):
    """День тижня — не lifecycle: завершений плаский H4/D1 без маркера паузи лишається видимим будь-якого дня."""
    bar = dict(_flat_m1(), open_time_ms=weekday_open_ms, tf_s=tf_s, complete=True)
    assert map_bar_to_candle_v4(bar, tf_s=tf_s) is not None
