"""Правило «чи йде M1 від брокера в SSOT» — одне для живого полера і засіву (runtime/ingest/m1_session_filter.py)."""
from __future__ import annotations

import pytest

from core.model.bars import CandleBar
from runtime.ingest.m1_session_filter import (
    VERDICT_PAUSE_FLAT_DROPPED,
    VERDICT_PAUSE_NONFLAT_ANOMALY,
    VERDICT_TRADING,
    VERDICT_TRADING_FLAT,
    classify_m1_for_ssot,
    is_flat_m1,
)


def _bar(o, h, low, c, v, extensions=None) -> CandleBar:
    return CandleBar(symbol="SYM", tf_s=60, open_time_ms=60_000, close_time_ms=120_000, o=o, h=h, low=low, c=c, v=v,
                     complete=True, src="history", extensions=dict(extensions or {}))


FLAT = _bar(5.0, 5.0, 5.0, 5.0, 1.0)
REGULAR = _bar(5.0, 6.0, 4.0, 5.5, 100.0)


@pytest.mark.parametrize("bar, trading, expected_verdict, expected_marker", [
    (REGULAR, True, VERDICT_TRADING, None),
    (FLAT, True, VERDICT_TRADING_FLAT, "trading_flat"),
    (REGULAR, False, VERDICT_PAUSE_NONFLAT_ANOMALY, "calendar_pause_nonflat_anomaly"),
])
def test_bars_that_reach_ssot_carry_the_session_marker(bar, trading, expected_verdict, expected_marker):
    out, verdict = classify_m1_for_ssot(bar, trading, flat_max_volume=4)
    assert verdict == expected_verdict
    assert out is not None and (out.o, out.h, out.low, out.c, out.v) == (bar.o, bar.h, bar.low, bar.c, bar.v)
    assert out.extensions == ({} if expected_marker is None else {expected_marker: True})


def test_flat_bar_outside_session_never_reaches_ssot():
    assert classify_m1_for_ssot(FLAT, trading=False, flat_max_volume=4) == (None, VERDICT_PAUSE_FLAT_DROPPED)


def test_flat_with_volume_above_threshold_is_a_regular_bar():
    heavy_flat = _bar(5.0, 5.0, 5.0, 5.0, 5.0)
    assert not is_flat_m1(heavy_flat, 4)
    assert classify_m1_for_ssot(heavy_flat, trading=False, flat_max_volume=4)[1] == VERDICT_PAUSE_NONFLAT_ANOMALY


class _RecordingUds:
    def __init__(self):
        self.committed = []

    def commit_final_bar(self, bar):
        self.committed.append(bar)
        return type("Result", (), {"ok": True})()


def test_live_poller_ingest_applies_the_same_rule(caplog):
    """Контроль рефакторингу: полер поводиться як до винесення правила (пауза: плаский відкинуто, неплаский — з WARN)."""
    from runtime.ingest.market_calendar import MarketCalendar
    from runtime.ingest.polling.m1_poller import M1SymbolPoller, set_flat_bar_max_volume

    set_flat_bar_max_volume(4)
    calendar = MarketCalendar(enabled=True, weekend_close_dow=4, weekend_close_hm="20:45", weekend_open_dow=6,
                              weekend_open_hm="22:00", daily_break_start_hm="21:00", daily_break_end_hm="22:00",
                              daily_break_enabled=True)
    uds = _RecordingUds()
    poller = M1SymbolPoller(symbol="SYM", provider=object(), uds=uds, calendar=calendar)
    wednesday_noon = 1_788_955_200_000  # 2026-09-09 12:00 UTC
    saturday = 1_789_250_400_000  # 2026-09-12 22:00 UTC — вихідні за календарем

    def at(open_ms, template):
        return CandleBar(symbol="SYM", tf_s=60, open_time_ms=open_ms, close_time_ms=open_ms + 60_000, o=template.o,
                         h=template.h, low=template.low, c=template.c, v=template.v, complete=True, src="history")

    assert poller._ingest_bar(at(saturday, FLAT)) is False  # noqa: SLF001
    assert poller._ingest_bar(at(wednesday_noon, FLAT)) is True  # noqa: SLF001
    assert poller._ingest_bar(at(saturday + 60_000, REGULAR)) is True  # noqa: SLF001
    assert [b.extensions for b in uds.committed] == [{"trading_flat": True}, {"calendar_pause_nonflat_anomaly": True}]
    assert "M1_NONFLAT_IN_PAUSE" in caplog.text


@pytest.mark.parametrize("cfg, expected", [
    ({}, 4),
    ({"flat_bar_max_volume": 10}, 10),
    ({"flat_bar_max_volume": -3}, 0),
    ({"flat_bar_max_volume": "хибне"}, 4),
])
def test_flat_threshold_normalization_is_shared_by_every_writer(cfg, expected):
    """Один clamp для полера, засіву і ремонту — інакше на тому самому конфізі вони розходяться."""
    from runtime.ingest.m1_session_filter import resolve_flat_max_volume
    assert resolve_flat_max_volume(cfg) == expected


def test_repair_tool_applies_the_same_rule_and_drops_the_forming_minute(monkeypatch):
    """Третій записувач M1 (ремонт дірок) теж не пише пласке поза сесією і хвилину, що формується."""
    from runtime.ingest.market_calendar import MarketCalendar
    from tools.repair import repair_m1_gaps as rmg

    calendar = MarketCalendar(enabled=True, weekend_close_dow=4, weekend_close_hm="20:45", weekend_open_dow=6,
                              weekend_open_hm="22:00", daily_break_start_hm="21:00", daily_break_end_hm="22:00",
                              daily_break_enabled=True)
    wednesday_noon = 1_788_955_200_000  # 2026-09-09 12:00 UTC
    saturday = 1_789_250_400_000  # 2026-09-12 22:00 UTC — вихідні
    now_ms = wednesday_noon + 3 * 60_000 + 30_000

    def _at(open_ms, template):
        return CandleBar(symbol="SYM", tf_s=60, open_time_ms=open_ms, close_time_ms=open_ms + 60_000, o=template.o,
                         h=template.h, low=template.low, c=template.c, v=template.v, complete=True, src="history")

    fetched = [_at(wednesday_noon, REGULAR), _at(saturday, FLAT), _at(wednesday_noon + 3 * 60_000, REGULAR)]
    monkeypatch.setattr(rmg, "fetch_m1_for_range", lambda *a, **k: list(fetched))
    written = {}
    monkeypatch.setattr(rmg, "_append_bars_to_jsonl", lambda root, sym, bars: written.setdefault("bars", bars) and 0 or len(bars))

    result = rmg.repair_gaps(data_root="/nowhere", symbol="SYM", gap_groups=[(wednesday_noon, wednesday_noon)],
                             all_gap_opens={wednesday_noon}, redis_cli=object(), namespace="ns", dry_run=False,
                             calendar=calendar, flat_max_volume=4, now_ms=now_ms, close_safety_ms=8_000)

    assert [b.open_time_ms for b in written["bars"]] == [wednesday_noon]
    assert result["total_fetched"] == 3 and result["total_kept"] == 1


def test_existing_extensions_are_kept_and_input_is_not_mutated():
    bar = _bar(5.0, 5.0, 5.0, 5.0, 1.0, extensions={"source_note": "x"})
    out, _ = classify_m1_for_ssot(bar, trading=True, flat_max_volume=4)
    assert out.extensions == {"source_note": "x", "trading_flat": True}
    assert bar.extensions == {"source_note": "x"}


def test_flat_bar_in_the_reopen_minute_is_broker_placeholder_and_not_written():
    """17.09 22:00 NAS100 і SPX500: O=H=L=C, v=3, ціна = закриттю сесії; той самий запит за 12 хв уже нічого не давав.

    Вимір на проді: 110 нормальних барів хвилини перевідкриття NAS100 мають обсяг 229…5811 і ненульовий діапазон,
    тож правило вузьке — під нього підпадає лише заглушка.
    """
    from runtime.ingest.m1_session_filter import VERDICT_REOPEN_FLAT_DROPPED
    out, verdict = classify_m1_for_ssot(FLAT, trading=True, flat_max_volume=4, session_open_minute=True)
    assert (out, verdict) == (None, VERDICT_REOPEN_FLAT_DROPPED)


def test_normal_reopen_bar_stays():
    """Контроль: справжній бар хвилини перевідкриття (обсяг і діапазон є) пишеться як звичайний."""
    out, verdict = classify_m1_for_ssot(REGULAR, trading=True, flat_max_volume=4, session_open_minute=True)
    assert (out, verdict) == (REGULAR, VERDICT_TRADING)


def test_flat_minute_inside_session_still_stays_with_marker():
    """Контроль межі: однотікова хвилина ВСЕРЕДИНІ сесії — справжня, лишається з маркером trading_flat."""
    out, verdict = classify_m1_for_ssot(FLAT, trading=True, flat_max_volume=4, session_open_minute=False)
    assert verdict == VERDICT_TRADING_FLAT and out.extensions == {"trading_flat": True}


def test_session_open_minute_detection_uses_the_previous_minute():
    """Хвилина перевідкриття = торгова, а попередня — ні (перерва або вихідні)."""
    from runtime.ingest.market_calendar import MarketCalendar
    from runtime.ingest.m1_session_filter import is_session_open_minute
    cal = MarketCalendar(enabled=True, weekend_close_dow=4, weekend_close_hm="20:45", weekend_open_dow=6,
                         weekend_open_hm="22:00", daily_break_start_hm="21:00", daily_break_end_hm="22:00",
                         daily_break_enabled=True)
    reopen = 1_789_596_000_000  # 2026-09-16 22:00 UTC — перша хвилина після денної перерви
    assert is_session_open_minute(reopen, cal.is_trading_minute)
    assert not is_session_open_minute(reopen + 60_000, cal.is_trading_minute)
    assert not is_session_open_minute(reopen - 60_000, cal.is_trading_minute)
