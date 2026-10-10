"""Котирування брокера до відкриття сесії — шум для груп із config (ADR-0099 rev 10.10, ADR-0054 W6 S6).

FXCM для FX шле передвідкриттєві хвилини в неділю з ~19:45 (узимку з 18:37), тиждень відкривається о 21:00 (узимку
22:00). TV їх не показує. Без правила хвилини біля відкриття йшли в SSOT з маркером anomaly (видимі на M1), а глибші —
у відсів з тривогою хибного календаря, бо обсяг у них як у торгівлі.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
from pathlib import Path

import pytest

from core.model.bars import CandleBar
from runtime.ingest.m1_session_filter import (
    VERDICT_PAUSE_NOISE_DROPPED,
    VERDICT_PAUSE_NONFLAT_ANOMALY,
    VERDICT_PAUSE_PREOPEN_DROPPED,
    VERDICT_TRADING,
    classify_m1_by_calendar,
    minutes_to_next_open,
    resolve_pause_policy,
)
from runtime.ingest.tick_common import calendar_for_symbol

REPO_CFG = json.loads((Path(__file__).resolve().parents[1] / "config.json").read_text(encoding="utf-8"))
FLAT_MAX = REPO_CFG["flat_bar_max_volume"]


def _ms(year, month, day, hour, minute):
    return int(dt.datetime(year, month, day, hour, minute, tzinfo=dt.timezone.utc).timestamp() * 1000)


def _m1(open_ms, v=60.0):
    return CandleBar(symbol="USD/JPY", tf_s=60, open_time_ms=open_ms, close_time_ms=open_ms + 60_000,
                     o=158.10, h=158.13, low=158.08, c=158.12, v=v, complete=True, src="history")


def _classify(symbol, open_ms, v=60.0):
    calendar = calendar_for_symbol(REPO_CFG, symbol)
    return classify_m1_by_calendar(_m1(open_ms, v), calendar.is_trading_minute, FLAT_MAX,
                                   resolve_pause_policy(REPO_CFG, symbol))


@pytest.mark.parametrize("open_ms, expected", [
    (_ms(2026, 10, 11, 20, 30), VERDICT_PAUSE_PREOPEN_DROPPED),  # Нд літо: 30 хв до 21:00 — раніше anomaly у SSOT
    (_ms(2026, 10, 11, 19, 45), VERDICT_PAUSE_PREOPEN_DROPPED),  # найраніші влітку — раніше відсів з тривогою (v ≥ 20)
    (_ms(2026, 10, 11, 17, 0), VERDICT_PAUSE_PREOPEN_DROPPED),   # рівно 240 хв до відкриття — межа вікна включно
    (_ms(2026, 11, 8, 18, 37), VERDICT_PAUSE_PREOPEN_DROPPED),   # Нд зима: відкриття 22:00, найраніші 18:37
    (_ms(2026, 10, 11, 16, 59), VERDICT_PAUSE_NOISE_DROPPED),    # за межею вікна — колишня рейка глибини з тривогою
    (_ms(2026, 10, 11, 21, 0), VERDICT_TRADING),                  # відкриття тижня — торгова хвилина
])
def test_usdjpy_sunday_quotes_before_week_open_are_noise(open_ms, expected):
    _out, verdict = _classify("USD/JPY", open_ms)
    assert verdict == expected


def test_usdjpy_after_friday_close_keeps_edge_anomaly_rail():
    """Сторона після закриття — колишні рейки: до відкриття ~48 год, правило котирувань до відкриття не діє, тож
    неплаский бар біля краю лишається гучним anomaly (DST чи хибний календар видно саме тут)."""
    out, verdict = _classify("USD/JPY", _ms(2026, 10, 9, 21, 5))
    assert verdict == VERDICT_PAUSE_NONFLAT_ANOMALY and out.extensions.get("calendar_pause_nonflat_anomaly")


def test_group_not_in_config_list_is_unchanged():
    """cfd_us_22_23 (XAU) правила не має: неплаский бар за 30 хв до відкриття 22:00 — anomaly, як і раніше."""
    out, verdict = _classify("XAU/USD", _ms(2026, 10, 11, 21, 30))
    assert verdict == VERDICT_PAUSE_NONFLAT_ANOMALY and out is not None


def test_minutes_to_next_open_looks_forward_only():
    calendar = calendar_for_symbol(REPO_CFG, "USD/JPY")
    assert minutes_to_next_open(_ms(2026, 10, 11, 20, 59), calendar.is_trading_minute, 240) == 1
    assert minutes_to_next_open(_ms(2026, 10, 9, 21, 0), calendar.is_trading_minute, 240) is None  # після закриття
    assert minutes_to_next_open(_ms(2026, 10, 12, 10, 0), calendar.is_trading_minute, 240) is None  # торгова


@pytest.mark.parametrize("section_patch, symbol, expected_window, expected_log", [
    ({}, "USD/JPY", 240, None),
    ({}, "XAU/USD", None, None),
    ({"pause_preopen_groups": None}, "USD/JPY", None, "key=pause_preopen_groups raw=None"),
    ({"pause_preopen_groups": "fx_24x5_utc_summer"}, "USD/JPY", None, "key=pause_preopen_groups raw='fx_24x5_utc_summer'"),
    ({"pause_preopen_window_min": 0}, "USD/JPY", 1, "M1_SESSION_FILTER_CONFIG_CLAMPED key=pause_preopen_window_min"),
])
def test_preopen_policy_comes_from_config_and_is_loud_on_fallback(caplog, section_patch, symbol, expected_window,
                                                                 expected_log):
    cfg = dict(REPO_CFG)
    section = dict(cfg["m1_session_filter"])
    for key, value in section_patch.items():
        if value is None:
            section.pop(key, None)
        else:
            section[key] = value
    cfg["m1_session_filter"] = section
    with caplog.at_level(logging.WARNING):
        assert resolve_pause_policy(cfg, symbol).preopen_window_min == expected_window
    if expected_log is None:
        assert "M1_SESSION_FILTER_CONFIG" not in caplog.text
    else:
        assert expected_log in caplog.text


class _RecordingUds:
    def __init__(self):
        self.committed = []

    def commit_final_bar(self, bar):
        self.committed.append(bar)
        return type("Result", (), {"ok": True})()


def test_poller_drops_preopen_quotes_once_without_false_calendar_alarm(caplog):
    """Живий полер: обсяг як у торгівлі (v=114, максимум архіву) — не тривога хибного календаря, а INFO і лічильник;
    повторний fetch тієї самої хвилини не рахується двічі; лічильник — у M1_POLLER_STATS."""
    from runtime.ingest.polling.m1_poller import M1PollerRunner, M1SymbolPoller, set_flat_bar_max_volume
    set_flat_bar_max_volume(FLAT_MAX)
    uds = _RecordingUds()
    poller = M1SymbolPoller(symbol="USD/JPY", provider=object(), uds=uds,
                            calendar=calendar_for_symbol(REPO_CFG, "USD/JPY"),
                            pause_policy=resolve_pause_policy(REPO_CFG, "USD/JPY"))
    quotes = [_m1(_ms(2026, 10, 11, 20, 0) + i * 60_000, v=114.0) for i in range(3)]
    with caplog.at_level(logging.INFO):
        for bar in quotes + quotes[:1]:
            assert poller._ingest_bar(bar) is False  # noqa: SLF001
        runner = M1PollerRunner(pollers=[poller], provider=object(), uds=object(), redis_tail_n={})
        runner._maybe_log_stats(force=True)  # noqa: SLF001
    assert uds.committed == []
    assert caplog.text.count("M1_PAUSE_PREOPEN_DROPPED") == 3
    assert "M1_PAUSE_NOISE_ALARM" not in caplog.text and "M1_NONFLAT_IN_PAUSE" not in caplog.text
    assert poller.stats["pause_preopen_dropped"] == 3 and poller.stats["pause_noise_alarms"] == 0
    assert "preopen=3" in caplog.text
