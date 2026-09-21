"""Правило «чи йде M1 від брокера в SSOT» — одне для живого полера і засіву (runtime/ingest/m1_session_filter.py)."""
from __future__ import annotations

import pytest

from core.model.bars import CandleBar
from runtime.ingest.m1_session_filter import (
    DEFAULT_PAUSE_POLICY,
    VERDICT_PAUSE_FLAT_DROPPED,
    VERDICT_PAUSE_NONFLAT_ANOMALY,
    VERDICT_TRADING,
    VERDICT_TRADING_FLAT,
    PausePolicy,
    classify_m1_by_calendar,
    is_flat_m1,
)


def _bar(o, h, low, c, v, extensions=None) -> CandleBar:
    return CandleBar(symbol="SYM", tf_s=60, open_time_ms=60_000, close_time_ms=120_000, o=o, h=h, low=low, c=c, v=v,
                     complete=True, src="history", extensions=dict(extensions or {}))


FLAT = _bar(5.0, 5.0, 5.0, 5.0, 1.0)
REGULAR = _bar(5.0, 6.0, 4.0, 5.5, 100.0)
_BAR_OPEN_MS = 60_000


# Календарі-функції для барів `_bar` (хвилина 60_000): факти про хвилину дає лише календар — як у записувачів.
def _trading_always(_open_ms):
    return True


def _pause_near_edge(open_ms):
    """Хвилина бару — пауза за 2 хв до відкриття: біля краю сесії, але не перша хвилина паузи після закриття."""
    return open_ms >= _BAR_OPEN_MS + 2 * 60_000


def _session_opens_at_bar(open_ms):
    """Хвилина бару — перша торгова після перерви."""
    return open_ms >= _BAR_OPEN_MS


@pytest.mark.parametrize("bar, trading, expected_verdict, expected_marker", [
    (REGULAR, True, VERDICT_TRADING, None),
    (FLAT, True, VERDICT_TRADING_FLAT, "trading_flat"),
    (REGULAR, False, VERDICT_PAUSE_NONFLAT_ANOMALY, "calendar_pause_nonflat_anomaly"),
])
def test_bars_that_reach_ssot_carry_the_session_marker(bar, trading, expected_verdict, expected_marker):
    calendar = _trading_always if trading else _pause_near_edge
    out, verdict = classify_m1_by_calendar(bar, calendar, 4, DEFAULT_PAUSE_POLICY)
    assert verdict == expected_verdict
    assert out is not None and (out.o, out.h, out.low, out.c, out.v) == (bar.o, bar.h, bar.low, bar.c, bar.v)
    assert out.extensions == ({} if expected_marker is None else {expected_marker: True})


def test_flat_bar_outside_session_never_reaches_ssot():
    assert classify_m1_by_calendar(FLAT, _pause_near_edge, 4, DEFAULT_PAUSE_POLICY) == (None, VERDICT_PAUSE_FLAT_DROPPED)


def test_flat_with_volume_above_threshold_is_a_regular_bar():
    heavy_flat = _bar(5.0, 5.0, 5.0, 5.0, 5.0)
    assert not is_flat_m1(heavy_flat, 4)
    assert classify_m1_by_calendar(heavy_flat, _pause_near_edge, 4, DEFAULT_PAUSE_POLICY)[1] == VERDICT_PAUSE_NONFLAT_ANOMALY


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
                          pause_policy=PausePolicy(noise_margin_min=60))._ingest_bar(bar) is False  # noqa: SLF001
    assert M1SymbolPoller(symbol="SYM", provider=object(), uds=wide, calendar=_us_cfd_calendar(),
                          pause_policy=PausePolicy(noise_margin_min=1439))._ingest_bar(bar) is True  # noqa: SLF001
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


def test_repair_filter_drops_deep_pause_bar_as_noise_and_keeps_edge_anomaly():
    """Ремонт дірок — той самий вердикт, що полер і засів: Сб неплаский — шум, 21:00 з v=3 — застарілий край,
    21:00 з обсягом торгівлі — anomaly; політика приходить параметром (main бере її з config)."""
    from runtime.ingest.m1_session_filter import VERDICT_PAUSE_EDGE_STALE_DROPPED, VERDICT_PAUSE_NOISE_DROPPED
    from tools.repair import repair_m1_gaps as rmg

    saturday = _m1_at(SATURDAY_0743, 63.01, 63.02, 63.01, 63.02, 5.0)
    edge = _m1_at(_utc_ms(2026, 9, 16, 21, 0), 5.0, 5.1, 5.0, 5.1, 30.0)
    stale = _m1_at(_utc_ms(2026, 9, 17, 21, 0), 5.0, 5.1, 5.0, 5.1, 3.0)
    kept, verdicts = rmg._filter_fetched_bars([saturday, edge, stale], _us_cfd_calendar(), 4, None,  # noqa: SLF001
                                              8_000, pause_policy=DEFAULT_PAUSE_POLICY)
    assert [b.open_time_ms for b in kept] == [edge.open_time_ms]
    assert verdicts == {VERDICT_PAUSE_NOISE_DROPPED: 1, VERDICT_PAUSE_NONFLAT_ANOMALY: 1,
                        VERDICT_PAUSE_EDGE_STALE_DROPPED: 1}


def test_existing_extensions_are_kept_and_input_is_not_mutated():
    bar = _bar(5.0, 5.0, 5.0, 5.0, 1.0, extensions={"source_note": "x"})
    out, _ = classify_m1_by_calendar(bar, _trading_always, 4, DEFAULT_PAUSE_POLICY)
    assert out.extensions == {"source_note": "x", "trading_flat": True}
    assert bar.extensions == {"source_note": "x"}


def test_flat_bar_in_the_reopen_minute_is_broker_placeholder_and_not_written():
    """17.09 22:00 NAS100 і SPX500: O=H=L=C, v=3, ціна = закриттю сесії; той самий запит за 12 хв уже нічого не давав.

    Вимір на проді: 110 нормальних барів хвилини перевідкриття NAS100 мають обсяг 229…5811 і ненульовий діапазон,
    тож правило вузьке — під нього підпадає лише заглушка.
    """
    from runtime.ingest.m1_session_filter import VERDICT_REOPEN_FLAT_DROPPED
    out, verdict = classify_m1_by_calendar(FLAT, _session_opens_at_bar, 4, DEFAULT_PAUSE_POLICY)
    assert (out, verdict) == (None, VERDICT_REOPEN_FLAT_DROPPED)


def test_normal_reopen_bar_stays():
    """Контроль: справжній бар хвилини перевідкриття (обсяг і діапазон є) пишеться як звичайний."""
    out, verdict = classify_m1_by_calendar(REGULAR, _session_opens_at_bar, 4, DEFAULT_PAUSE_POLICY)
    assert (out, verdict) == (REGULAR, VERDICT_TRADING)


def test_flat_minute_inside_session_still_stays_with_marker():
    """Контроль межі: однотікова хвилина ВСЕРЕДИНІ сесії — справжня, лишається з маркером trading_flat."""
    out, verdict = classify_m1_by_calendar(FLAT, _trading_always, 4, DEFAULT_PAUSE_POLICY)
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
    out = classify_m1_by_calendar(bar, _us_cfd_calendar().is_trading_minute, 4, DEFAULT_PAUSE_POLICY)
    assert out == (None, VERDICT_PAUSE_NOISE_DROPPED)


def test_classify_by_calendar_pause_bar_near_session_edge_keeps_anomaly_and_flat_drop():
    """Біля краю (21:01 — друга хвилина денної перерви): неплаский → anomaly; 21:00 плаский → drop."""
    from runtime.ingest.m1_session_filter import classify_m1_by_calendar
    is_trading = _us_cfd_calendar().is_trading_minute
    first_break_minute = _utc_ms(2026, 9, 16, 21, 0)
    nonflat, verdict = classify_m1_by_calendar(_m1_at(first_break_minute + 60_000, 5.0, 5.1, 5.0, 5.1, 3.0), is_trading,
                                               4, DEFAULT_PAUSE_POLICY)
    assert verdict == VERDICT_PAUSE_NONFLAT_ANOMALY and nonflat.extensions == {"calendar_pause_nonflat_anomaly": True}
    flat = classify_m1_by_calendar(_m1_at(first_break_minute, 5.0, 5.0, 5.0, 5.0, 1.0), is_trading, 4, DEFAULT_PAUSE_POLICY)
    assert flat == (None, VERDICT_PAUSE_FLAT_DROPPED)


@pytest.mark.parametrize("minute, expected_verdict", [
    (44, VERDICT_PAUSE_NONFLAT_ANOMALY),  # Пт 21:44 — рівно 60 хв від останньої торгової 20:44: ще «біля краю»
    (45, "pause_noise_dropped"),          # Пт 21:45 — 61 хв: уже шум
])
def test_classify_by_calendar_margin_boundary_is_inclusive(minute, expected_verdict):
    """Межа DST-зсуву (60 хв) належить краю сесії: зимова справжня хвилина 21:44 під літнім календарем — anomaly."""
    from runtime.ingest.m1_session_filter import classify_m1_by_calendar
    bar = _m1_at(_utc_ms(2026, 9, 18, 21, minute), 5.0, 5.1, 5.0, 5.1, 120.0)
    assert classify_m1_by_calendar(bar, _us_cfd_calendar().is_trading_minute, 4, DEFAULT_PAUSE_POLICY)[1] == expected_verdict


def test_classify_by_calendar_flat_reopen_minute_still_dropped_as_placeholder():
    """Спільний хелпер несе й правило перевідкриття: плаский бар 22:00 після перерви — заглушка брокера."""
    from runtime.ingest.m1_session_filter import VERDICT_REOPEN_FLAT_DROPPED, classify_m1_by_calendar
    bar = _m1_at(_utc_ms(2026, 9, 16, 22, 0), 5.0, 5.0, 5.0, 5.0, 3.0)
    assert classify_m1_by_calendar(bar, _us_cfd_calendar().is_trading_minute, 4, DEFAULT_PAUSE_POLICY) == (
        None, VERDICT_REOPEN_FLAT_DROPPED)


def test_classify_by_calendar_disabled_calendar_never_drops_as_noise():
    from runtime.ingest.m1_session_filter import classify_m1_by_calendar
    bar = _m1_at(SATURDAY_0743, 5.0, 5.1, 5.0, 5.1, 3.0)
    assert classify_m1_by_calendar(bar, lambda _ms: True, 4, DEFAULT_PAUSE_POLICY) == (bar, VERDICT_TRADING)


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


@pytest.mark.parametrize("cfg, expected_margin, expected_log", [
    ({"m1_session_filter": {"pause_noise_margin_min": 90}}, 90, None),
    ({}, 60, "M1_SESSION_FILTER_CONFIG_MISSING raw=None"),
    ({"m1_session_filter": {}}, 60, "M1_SESSION_FILTER_CONFIG_DEFAULT key=pause_noise_margin_min default=60"),
    ({"m1_session_filter": {"pause_noise_margin_min": 0}}, 1,
     "M1_SESSION_FILTER_CONFIG_CLAMPED key=pause_noise_margin_min raw=0 value=1"),
    ({"m1_session_filter": {"pause_noise_margin_min": "хибне"}}, 60,
     "M1_SESSION_FILTER_CONFIG_INVALID key=pause_noise_margin_min raw='хибне' default=60"),
    ({"m1_session_filter": "хибне"}, 60, "M1_SESSION_FILTER_CONFIG_MISSING raw='хибне'"),
])
def test_resolve_pause_policy_is_loud_on_fallback_and_clamp(caplog, cfg, expected_margin, expected_log):
    """I5: дефолт або clamp замість значення з config змінюють те, що пишеться в SSOT, — тому WARNING із сирим значенням."""
    import logging
    from runtime.ingest.m1_session_filter import resolve_pause_policy
    with caplog.at_level(logging.WARNING):
        assert resolve_pause_policy(cfg).noise_margin_min == expected_margin
    if expected_log is None:
        assert "key=pause_noise_margin_min" not in caplog.text
    else:
        assert expected_log in caplog.text


def test_resolve_pause_policy_repo_config_carries_every_key_without_fallback(caplog):
    """SSOT правил паузи — config.json, а не дефолти в коді: репо-конфіг резолвиться без жодного WARNING."""
    import json
    import logging
    from pathlib import Path
    from runtime.ingest.m1_session_filter import resolve_pause_policy
    cfg = json.loads((Path(__file__).resolve().parents[1] / "config.json").read_text(encoding="utf-8"))
    with caplog.at_level(logging.WARNING):
        policy = resolve_pause_policy(cfg)
    assert "M1_SESSION_FILTER_CONFIG" not in caplog.text
    assert policy.noise_margin_min == cfg["m1_session_filter"]["pause_noise_margin_min"]


# --- Рішення приватне, рейки обов'язкові (ADR-0099 §3.1, D15.2) -------------------------------------------------

def test_writers_have_a_single_public_entry_point_to_the_rule():
    """Колишня публічна `classify_m1_for_ssot` з дефолтними рейками давала записувачу тихо лишитися без правила
    глибини чи перевідкриття. Тепер публічна лише `classify_m1_by_calendar`, яка бере факти з календаря сама."""
    import runtime.ingest.m1_session_filter as session_filter
    assert not hasattr(session_filter, "classify_m1_for_ssot")


def test_decision_rails_are_keyword_only_and_required():
    from runtime.ingest.m1_session_filter import _decide_verdict  # перевіряємо саме контракт рейок
    with pytest.raises(TypeError):
        _decide_verdict(REGULAR, flat_max_volume=4, trading=False, session_open_minute=False)  # без deep_in_pause
    with pytest.raises(TypeError):
        _decide_verdict(REGULAR, 4, False, False, False)  # позиційно — заборонено


def test_calendar_suspected_policy_writes_deep_pause_bar_as_anomaly():
    """Політика «календар під підозрою» (засів з --allow-off-calendar) не відкидає неплаский бар за положенням."""
    bar = _m1_at(SATURDAY_0743, 5.0, 6.0, 4.0, 5.5, 300.0)
    out, verdict = classify_m1_by_calendar(bar, _us_cfd_calendar().is_trading_minute, 4,
                                           DEFAULT_PAUSE_POLICY.with_calendar_suspected())
    assert verdict == VERDICT_PAUSE_NONFLAT_ANOMALY and out.extensions == {"calendar_pause_nonflat_anomaly": True}


# --- Облік відкинутих хвилин і тривога хибного календаря (ADR-0099 §3.3–3.4) ---------------------------------------

class _ReplayProvider:
    """Брокер, що на кожен запит віддає ту саму партію — як FXCM, поки watermark полера не рухається."""

    def __init__(self, bars):
        self._bars = list(bars)

    def fetch_last_n_m1(self, symbol, n, date_to_utc=None):
        return list(self._bars)


SUNDAY_2205 = _utc_ms(2026, 9, 20, 22, 5)  # Нд: ринок відкрився о 22:00, останній закритий M1 — 22:04


def _poller_polling_at(monkeypatch, provider, now_ms):
    from runtime.ingest.polling import m1_poller
    m1_poller.set_flat_bar_max_volume(4)
    monkeypatch.setattr(m1_poller, "_utc_now_ms", lambda: now_ms)
    return m1_poller.M1SymbolPoller(symbol="XAG/USD", provider=provider, uds=_RecordingUds(),
                                    calendar=_us_cfd_calendar())


def test_poll_once_twice_on_the_same_noise_counts_and_warns_once(monkeypatch, caplog):
    """Рев'ю п.2: відкинутий бар не рухає watermark, тож кожен цикл брокер віддає його знову. Раніше лічильник і WARN
    завищувались утричі (13 унікальних → 39); тепер хвилина рахується один раз."""
    noise = _m1_at(SATURDAY_0743, 63.01, 63.02, 63.01, 63.02, 5.0)
    poller = _poller_polling_at(monkeypatch, _ReplayProvider([noise]), SUNDAY_2205)
    poller.poll_once()
    poller.poll_once()
    assert caplog.text.count("M1_PAUSE_NOISE_DROPPED") == 1
    assert poller.stats["pause_noise_dropped"] == 1


def test_reopen_placeholder_is_reported_once_across_poll_cycles(monkeypatch, caplog):
    reopen_placeholder = _m1_at(_utc_ms(2026, 9, 20, 22, 0), 5.0, 5.0, 5.0, 5.0, 3.0)
    poller = _poller_polling_at(monkeypatch, _ReplayProvider([reopen_placeholder]), SUNDAY_2205)
    poller.poll_once()
    poller.poll_once()
    assert caplog.text.count("M1_REOPEN_FLAT_DROPPED") == 1


def test_trading_like_noise_raises_false_calendar_alarm_with_stats(monkeypatch, caplog):
    """Рев'ю п.8: справжні за обсягом хвилини глибоко в «паузі» — ознака хибного календаря. Один ERROR на вікно і
    лічильник у M1_POLLER_STATS."""
    import logging
    from runtime.ingest.polling.m1_poller import M1PollerRunner
    trading_like = [_m1_at(SATURDAY_0743 + i * 60_000, 63.0, 63.2, 62.9, 63.1, 180.0) for i in range(3)]
    poller = _poller_polling_at(monkeypatch, _ReplayProvider(trading_like), SUNDAY_2205)
    poller.poll_once()
    alarms = [r for r in caplog.records if "M1_PAUSE_NOISE_ALARM" in r.getMessage()]
    assert len(alarms) == 1 and alarms[0].levelno == logging.ERROR and "reason=volume" in alarms[0].getMessage()
    assert poller.stats["pause_noise_alarms"] == 1
    runner = M1PollerRunner(pollers=[poller], provider=object(), uds=object(), redis_tail_n={})
    with caplog.at_level(logging.INFO):
        runner._maybe_log_stats(force=True)  # noqa: SLF001
    assert "pause_noise=3 edge_stale=0 noise_alarm=1" in caplog.text


def test_noise_alarm_on_volume_is_throttled_per_bar_time_window():
    from runtime.ingest.polling.m1_drop_ledger import ALARM_REASON_VOLUME, DroppedM1Ledger
    policy = DEFAULT_PAUSE_POLICY
    ledger = DroppedM1Ledger(policy)
    heavy = float(policy.alarm_min_volume)
    first = ledger.observe_noise(SATURDAY_0743, heavy)
    assert first is not None and first.reason == ALARM_REASON_VOLUME and first.suppressed_since_last == 0
    assert ledger.observe_noise(SATURDAY_0743 + 60_000, heavy) is None  # те саме вікно — придушено, не спам
    next_window = ledger.observe_noise(SATURDAY_0743 + policy.alarm_window_min * 60_000, heavy)
    assert next_window is not None and next_window.suppressed_since_last == 1


def test_noise_like_volume_below_alarm_threshold_is_quiet():
    from runtime.ingest.polling.m1_drop_ledger import DroppedM1Ledger
    ledger = DroppedM1Ledger(DEFAULT_PAUSE_POLICY)
    assert ledger.observe_noise(SATURDAY_0743, float(DEFAULT_PAUSE_POLICY.alarm_min_volume) - 1) is None


def test_noise_alarm_on_density_counts_distinct_minutes_in_bar_time_window():
    from runtime.ingest.polling.m1_drop_ledger import ALARM_REASON_DENSITY, DroppedM1Ledger
    policy = DEFAULT_PAUSE_POLICY
    assert policy.alarm_max_dropped < policy.alarm_window_min  # щільний потік шуму вміщується у вікно
    ledger = DroppedM1Ledger(policy)
    alarms = [ledger.observe_noise(SATURDAY_0743 + i * 60_000, 2.0) for i in range(policy.alarm_max_dropped + 1)]
    assert alarms[:-1] == [None] * policy.alarm_max_dropped
    assert alarms[-1].reason == ALARM_REASON_DENSITY
    assert alarms[-1].noise_in_window == policy.alarm_max_dropped + 1


def test_sparse_noise_over_hours_is_not_an_alarm_even_when_processed_in_one_batch():
    """Вікно — за часом барів: після вихідних полер за цикл доганяє всю суботу, і це не тривога."""
    from runtime.ingest.polling.m1_drop_ledger import DroppedM1Ledger
    ledger = DroppedM1Ledger(DEFAULT_PAUSE_POLICY)
    every_two_minutes_for_three_hours = [SATURDAY_0743 + i * 120_000 for i in range(90)]
    assert all(ledger.observe_noise(open_ms, 2.0) is None for open_ms in every_two_minutes_for_three_hours)


def test_dropped_minutes_memory_is_bounded():
    from runtime.ingest.polling.m1_drop_ledger import DroppedM1Ledger
    ledger = DroppedM1Ledger(DEFAULT_PAUSE_POLICY, capacity=2)
    assert ledger.first_drop(60_000) and ledger.first_drop(120_000)
    assert not ledger.first_drop(120_000)
    assert ledger.first_drop(180_000)  # витісняє найстаршу
    assert ledger.first_drop(60_000)  # за межею пам'яті — знову «вперше»: пам'ять не росте без меж


# --- Застарілий край: клас «хвилина 21:00» (ADR-0099 §3.2, рев'ю п.4) ---------------------------------------------

@pytest.mark.parametrize("open_ms, volume, expected_verdict", [
    (_utc_ms(2026, 9, 16, 21, 0), 3.0, "pause_edge_stale_dropped"),  # 21:00 — перша хвилина перерви, ~3 застарілі тіки
    (_utc_ms(2026, 9, 16, 21, 0), 8.0, "pause_edge_stale_dropped"),  # поріг 4 × K=2 включний
    (_utc_ms(2026, 9, 16, 21, 0), 9.0, VERDICT_PAUSE_NONFLAT_ANOMALY),  # понад поріг — anomaly, як і раніше
    (_utc_ms(2026, 9, 18, 20, 45), 3.0, "pause_edge_stale_dropped"),  # Пт 20:45 — перша хвилина вихідних
    (_utc_ms(2026, 9, 16, 21, 59), 3.0, VERDICT_PAUSE_NONFLAT_ANOMALY),  # остання перед відкриттям — не чіпаємо
    (_utc_ms(2026, 9, 16, 21, 1), 3.0, VERDICT_PAUSE_NONFLAT_ANOMALY),  # друга хвилина перерви — не чіпаємо
])
def test_edge_stale_rule_takes_only_the_first_pause_minute_after_close(open_ms, volume, expected_verdict):
    """Вузьке правило: лише перша хвилина паузи після закриття. Ширше «біля краю з малим v» під несезонним календарем
    узимку з'їло б 58/57 справжніх хвилин XAU/XAG замість 1/1 (ADR-0099 §2 E)."""
    bar = _m1_at(open_ms, 5.0, 5.1, 5.0, 5.1, volume)
    out, verdict = classify_m1_by_calendar(bar, _us_cfd_calendar().is_trading_minute, 4, DEFAULT_PAUSE_POLICY)
    assert verdict == expected_verdict
    assert (out is None) == (expected_verdict == "pause_edge_stale_dropped")


@pytest.mark.parametrize("policy", [
    DEFAULT_PAUSE_POLICY.with_calendar_suspected(),
    PausePolicy(noise_margin_min=60, edge_stale_max_volume=None),
])
def test_edge_stale_rule_off_keeps_the_2100_bar_as_anomaly(policy):
    """Календар під підозрою (засів з --allow-off-calendar) або K=0 у config — 21:00 пишеться з маркером anomaly."""
    bar = _m1_at(_utc_ms(2026, 9, 16, 21, 0), 5.0, 5.1, 5.0, 5.1, 3.0)
    out, verdict = classify_m1_by_calendar(bar, _us_cfd_calendar().is_trading_minute, 4, policy)
    assert verdict == VERDICT_PAUSE_NONFLAT_ANOMALY and out.extensions == {"calendar_pause_nonflat_anomaly": True}


@pytest.mark.parametrize("cfg, expected_max_volume", [
    ({"flat_bar_max_volume": 5, "m1_session_filter": {"pause_edge_stale_volume_mult": 3}}, 15),
    ({"flat_bar_max_volume": 4, "m1_session_filter": {"pause_edge_stale_volume_mult": 0}}, None),
])
def test_edge_stale_threshold_is_flat_threshold_times_config_multiplier(cfg, expected_max_volume):
    from runtime.ingest.m1_session_filter import resolve_pause_policy
    assert resolve_pause_policy(cfg).edge_stale_max_volume == expected_max_volume


def test_poller_drops_stale_2100_minute_once_with_warn_and_counter(caplog):
    from runtime.ingest.polling.m1_poller import M1SymbolPoller, set_flat_bar_max_volume
    set_flat_bar_max_volume(4)
    uds = _RecordingUds()
    poller = M1SymbolPoller(symbol="NAS100", provider=object(), uds=uds, calendar=_us_cfd_calendar())
    stale = _m1_at(_utc_ms(2026, 9, 15, 21, 0), 24300.5, 24301.0, 24300.5, 24301.0, 3.0)
    assert poller._ingest_bar(stale) is False  # noqa: SLF001
    assert poller._ingest_bar(stale) is False  # noqa: SLF001 — повторний fetch тієї самої хвилини
    assert uds.committed == []
    assert caplog.text.count("M1_PAUSE_EDGE_STALE_DROPPED") == 1
    assert poller.stats["pause_edge_stale_dropped"] == 1
