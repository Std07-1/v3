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
    """Полер застосовує спільне правило: глибоко в паузі — шум (відкинуто з WARN і лічильником), біля краю сесії
    неплаский — anomaly з WARN, однотіковий у сесії — trading_flat."""
    from runtime.ingest.market_calendar import MarketCalendar
    from runtime.ingest.polling.m1_poller import M1SymbolPoller, set_flat_bar_max_volume

    set_flat_bar_max_volume(4)
    calendar = MarketCalendar(enabled=True, weekend_close_dow=4, weekend_close_hm="20:45", weekend_open_dow=6,
                              weekend_open_hm="22:00", daily_break_start_hm="21:00", daily_break_end_hm="22:00",
                              daily_break_enabled=True)
    uds = _RecordingUds()
    poller = M1SymbolPoller(symbol="SYM", provider=object(), uds=uds, calendar=calendar)
    wednesday_noon = 1_788_955_200_000  # 2026-09-09 12:00 UTC
    wednesday_break = wednesday_noon + 9 * 3_600_000  # 2026-09-09 21:00 UTC — перша хвилина денної перерви
    saturday = 1_789_250_400_000  # 2026-09-12 22:00 UTC — вихідні за календарем, доба від обох країв

    def at(open_ms, template):
        return CandleBar(symbol="SYM", tf_s=60, open_time_ms=open_ms, close_time_ms=open_ms + 60_000, o=template.o,
                         h=template.h, low=template.low, c=template.c, v=template.v, complete=True, src="history")

    assert poller._ingest_bar(at(saturday, FLAT)) is False  # noqa: SLF001
    assert poller._ingest_bar(at(wednesday_noon, FLAT)) is True  # noqa: SLF001
    assert poller._ingest_bar(at(wednesday_break, REGULAR)) is True  # noqa: SLF001
    assert poller._ingest_bar(at(saturday + 60_000, REGULAR)) is False  # noqa: SLF001
    assert [b.extensions for b in uds.committed] == [{"trading_flat": True}, {"calendar_pause_nonflat_anomaly": True}]
    assert "M1_NONFLAT_IN_PAUSE" in caplog.text
    assert caplog.text.count("M1_PAUSE_NOISE_DROPPED") == 2
    assert poller.stats["pause_noise_dropped"] == 2


def test_live_poller_pause_noise_margin_comes_from_constructor():
    """Запас приходить у полер параметром (будівники беруть його з config): ширший запас — той самий бар уже anomaly."""
    from runtime.ingest.polling.m1_poller import M1SymbolPoller, set_flat_bar_max_volume

    set_flat_bar_max_volume(4)
    saturday_2201 = 1_789_250_460_000  # 2026-09-12 22:01 UTC: до Пт 20:44 — 1517 хв, до Нд 22:00 — 1439 хв
    bar = CandleBar(symbol="SYM", tf_s=60, open_time_ms=saturday_2201, close_time_ms=saturday_2201 + 60_000,
                    o=5.0, h=6.0, low=4.0, c=5.5, v=100.0, complete=True, src="history")
    narrow, wide = _RecordingUds(), _RecordingUds()
    assert M1SymbolPoller(symbol="SYM", provider=object(), uds=narrow, calendar=_us_cfd_calendar(),
                          pause_noise_margin_min=60)._ingest_bar(bar) is False  # noqa: SLF001
    assert M1SymbolPoller(symbol="SYM", provider=object(), uds=wide, calendar=_us_cfd_calendar(),
                          pause_noise_margin_min=1439)._ingest_bar(bar) is True  # noqa: SLF001
    assert narrow.committed == [] and wide.committed[0].extensions == {"calendar_pause_nonflat_anomaly": True}


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


# --- Шум глибоко в паузі: правило за відстанню від краю сесії (не за обсягом) ---------------------------------------

def _utc_ms(year, month, day, hour, minute):
    import datetime as dt
    return int(dt.datetime(year, month, day, hour, minute, tzinfo=dt.timezone.utc).timestamp() * 1000)


def _us_cfd_calendar():
    """Календар групи cfd_us_22_23 з config.json: вихідні Пт 20:45 → Нд 22:00, денна перерва 21:00–22:00."""
    from runtime.ingest.market_calendar import MarketCalendar
    return MarketCalendar(enabled=True, weekend_close_dow=4, weekend_close_hm="20:45", weekend_open_dow=6,
                          weekend_open_hm="22:00", daily_break_start_hm="21:00", daily_break_end_hm="22:00",
                          daily_break_enabled=True)


def _m1_at(open_ms, o, h, low, c, v):
    return CandleBar(symbol="SYM", tf_s=60, open_time_ms=open_ms, close_time_ms=open_ms + 60_000, o=o, h=h, low=low,
                     c=c, v=v, complete=True, src="history")


SATURDAY_0743 = _utc_ms(2026, 9, 19, 7, 43)  # XAG Сб 19.09 07:43 — суботній шум брокера з v=5


@pytest.mark.parametrize("o, h, low, c, v", [
    (5.0, 5.0, 5.0, 5.0, 1.0),          # плаский
    (63.01, 63.02, 63.01, 63.02, 5.0),  # XAG: діапазон 1 крок, v=5 — поріг пласкості v<=4 його не відсікав
    (5.0, 6.0, 4.0, 5.5, 300.0),        # великий обсяг — правило не дивиться на обсяг
])
def test_classify_by_calendar_bar_deep_in_weekend_pause_is_dropped_as_noise(o, h, low, c, v):
    from runtime.ingest.m1_session_filter import VERDICT_PAUSE_NOISE_DROPPED, classify_m1_by_calendar
    bar = _m1_at(SATURDAY_0743, o, h, low, c, v)
    out = classify_m1_by_calendar(bar, _us_cfd_calendar().is_trading_minute, flat_max_volume=4,
                                  pause_noise_margin_min=60)
    assert out == (None, VERDICT_PAUSE_NOISE_DROPPED)


def test_classify_by_calendar_pause_bar_near_session_edge_keeps_anomaly_and_flat_drop():
    """Біля краю (21:00 — перша хвилина денної перерви) — чинна поведінка: неплаский → anomaly, плаский → drop."""
    from runtime.ingest.m1_session_filter import classify_m1_by_calendar
    is_trading = _us_cfd_calendar().is_trading_minute
    first_break_minute = _utc_ms(2026, 9, 16, 21, 0)
    nonflat, verdict = classify_m1_by_calendar(_m1_at(first_break_minute, 5.0, 5.1, 5.0, 5.1, 3.0), is_trading, 4, 60)
    assert verdict == VERDICT_PAUSE_NONFLAT_ANOMALY and nonflat.extensions == {"calendar_pause_nonflat_anomaly": True}
    flat = classify_m1_by_calendar(_m1_at(first_break_minute, 5.0, 5.0, 5.0, 5.0, 1.0), is_trading, 4, 60)
    assert flat == (None, VERDICT_PAUSE_FLAT_DROPPED)


@pytest.mark.parametrize("minute, expected_verdict", [
    (44, VERDICT_PAUSE_NONFLAT_ANOMALY),  # Пт 21:44 — рівно 60 хв від останньої торгової 20:44: ще «біля краю»
    (45, "pause_noise_dropped"),          # Пт 21:45 — 61 хв: уже шум
])
def test_classify_by_calendar_margin_boundary_is_inclusive(minute, expected_verdict):
    """Межа DST-зсуву (60 хв) належить краю сесії: зимова справжня хвилина 21:44 під літнім календарем — anomaly."""
    from runtime.ingest.m1_session_filter import classify_m1_by_calendar
    bar = _m1_at(_utc_ms(2026, 9, 18, 21, minute), 5.0, 5.1, 5.0, 5.1, 120.0)
    assert classify_m1_by_calendar(bar, _us_cfd_calendar().is_trading_minute, 4, 60)[1] == expected_verdict


def test_classify_by_calendar_flat_reopen_minute_still_dropped_as_placeholder():
    """Спільний хелпер несе й правило перевідкриття: плаский бар 22:00 після перерви — заглушка брокера."""
    from runtime.ingest.m1_session_filter import VERDICT_REOPEN_FLAT_DROPPED, classify_m1_by_calendar
    bar = _m1_at(_utc_ms(2026, 9, 16, 22, 0), 5.0, 5.0, 5.0, 5.0, 3.0)
    assert classify_m1_by_calendar(bar, _us_cfd_calendar().is_trading_minute, 4, 60) == (
        None, VERDICT_REOPEN_FLAT_DROPPED)


def test_classify_by_calendar_disabled_calendar_never_drops_as_noise():
    from runtime.ingest.m1_session_filter import classify_m1_by_calendar
    bar = _m1_at(SATURDAY_0743, 5.0, 5.1, 5.0, 5.1, 3.0)
    assert classify_m1_by_calendar(bar, lambda _ms: True, 4, 60) == (bar, VERDICT_TRADING)


@pytest.mark.parametrize("open_ms, expected", [
    (_utc_ms(2026, 9, 16, 12, 0), 0),     # торгова
    (_utc_ms(2026, 9, 16, 21, 0), 1),     # перша хвилина перерви (торгова 20:59)
    (_utc_ms(2026, 9, 16, 21, 59), 1),    # остання хвилина перерви (торгова 22:00)
    (_utc_ms(2026, 9, 16, 21, 30), 30),   # середина: до 22:00 — 30, до 20:59 — 31
    (SATURDAY_0743, None),                # глибоко у вихідних — за межею пошуку
])
def test_minutes_to_session_edge_counts_to_nearest_trading_minute(open_ms, expected):
    from runtime.ingest.m1_session_filter import minutes_to_session_edge
    assert minutes_to_session_edge(open_ms, _us_cfd_calendar().is_trading_minute, max_minutes=60) == expected


def test_minutes_to_session_edge_search_is_bounded_by_the_margin():
    """Пошук не виходить за ±max_minutes: на вихідних (~49 год паузи) — сама хвилина плюс рівно 2×60 сусідніх."""
    from runtime.ingest.m1_session_filter import minutes_to_session_edge
    calls = []

    def is_trading(ms):
        calls.append(ms)
        return False

    assert minutes_to_session_edge(SATURDAY_0743, is_trading, max_minutes=60) is None
    assert len(calls) == 1 + 2 * 60
    assert max(abs(ms - SATURDAY_0743) for ms in calls) == 60 * 60_000


@pytest.mark.parametrize("cfg, expected", [
    ({}, 60),
    ({"m1_session_filter": {"pause_noise_margin_min": 90}}, 90),
    ({"m1_session_filter": {"pause_noise_margin_min": 0}}, 1),
    ({"m1_session_filter": {"pause_noise_margin_min": "хибне"}}, 60),
    ({"m1_session_filter": "хибне"}, 60),
])
def test_resolve_pause_noise_margin_min_normalizes_config(cfg, expected):
    from runtime.ingest.m1_session_filter import resolve_pause_noise_margin_min
    assert resolve_pause_noise_margin_min(cfg) == expected


def test_resolve_pause_noise_margin_min_repo_config_carries_the_key():
    """SSOT запасу — config.json, а не дефолт у коді: ключ має бути в репо-конфігу."""
    import json
    from pathlib import Path
    from runtime.ingest.m1_session_filter import resolve_pause_noise_margin_min
    cfg = json.loads((Path(__file__).resolve().parents[1] / "config.json").read_text(encoding="utf-8"))
    assert "pause_noise_margin_min" in cfg["m1_session_filter"]
    assert resolve_pause_noise_margin_min(cfg) == cfg["m1_session_filter"]["pause_noise_margin_min"]
