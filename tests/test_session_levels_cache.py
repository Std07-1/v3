"""Кеш сесійних рівнів рушія (`SmcEngine.get_session_levels` / `get_session_states`) ≡ пакетна `compute_session_levels`.

Ключ кешу на символ — (версія стрічки M1, `session_moment_key` моменту). Застарілий вихід можливий лише двома шляхами:
стрічка змінилась, а версія — ні; момент змінив вихід, а ключ — ні. Перше стереже прогін кожного виду зміни стрічки,
друге — щохвилинний прохід моменту по замороженій стрічці через межі сесій, killzone, доби, вихідних і перехід DST ЄС.
Справжні бари — M1 XAU з data_v3 (`V3_DATA_ROOT` або `data_root` config), коли сховище поруч; у CI його немає.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import pathlib

import pytest

import core.smc.sessions as sessions_mod
from core.model.bars import CandleBar
from core.smc.config import SmcConfig
from core.smc.engine import _SESSION_M1_CAPACITY, SmcEngine
from core.smc.sessions import compute_session_levels, session_moment_key

REPO = pathlib.Path(__file__).resolve().parents[1]
CFG = json.loads((REPO / "config.json").read_text(encoding="utf-8"))
_SYM = "XAU/USD"
_MIN_MS = 60_000
_HOUR_MS = 3_600_000
_DAY_MS = 86_400_000
_D1_S = 86_400


def _ms(text):
    return int(dt.datetime.strptime(text, "%Y-%m-%d %H:%M").replace(tzinfo=dt.timezone.utc).timestamp() * 1000)


def _m1(open_ms, price, src="history", spread=0.5):
    return CandleBar(symbol=_SYM, tf_s=60, open_time_ms=open_ms, close_time_ms=open_ms + _MIN_MS, o=price,
                     h=price + spread, low=price - spread, c=price, v=1.0, complete=True, src=src)


def _price(i):
    """Детермінована ціна без random: пилка, що рідко повторюється."""
    return 4000.0 + (i * 37 % 211) * 0.25


def _engine():
    return SmcEngine(SmcConfig.from_dict(CFG["smc"]))


def _assert_matches_batch(engine, now_ms):
    """Кешований шлях (влучання з попереднього запиту чи промах, далі точно влучання) ≡ свіжий пакетний розрахунок."""
    tape = sorted(engine._session_m1_bars[_SYM], key=lambda b: b.open_time_ms)
    levels, states = compute_session_levels(tape, engine._session_windows, now_ms, _SYM, tf_s=_D1_S)
    for _ in range(2):
        assert engine.get_session_levels(_SYM, now_ms) == levels, now_ms
        assert engine.get_session_states(_SYM, now_ms) == states, now_ms


def test_cached_levels_match_batch_every_minute_across_sessions_killzones_days_weekend_and_eu_dst_switch():
    engine = _engine()
    start, tape_end = _ms("2026-10-21 00:00"), _ms("2026-10-23 21:00")
    engine.feed_m1_bars_bulk(_SYM, [_m1(t, _price(i)) for i, t in enumerate(range(start, tape_end, 7 * _MIN_MS))])
    # будній день з усіма сесіями (літо); ніч Сб→Нд з переходом ЄС (Нд 25.10 01:00 UTC); ранок Пн — Лондон уже за
    # зимовим годинником (відкриття 08:00 UTC, killzone до 11:00); ніч Вт — доба Лондона взимку змінюється о 01:00 UTC
    # окремо від відкриття Азії, і «вчора» стає Пн замість Пт
    sweeps = (("2026-10-22 06:00", 24 * 60), ("2026-10-24 23:00", 3 * 60), ("2026-10-26 06:00", 6 * 60),
              ("2026-10-27 00:00", 2 * 60))
    key_changes = 0
    for first, minutes in sweeps:
        previous_key = None
        for minute in range(minutes):
            now_ms = _ms(first) + minute * _MIN_MS
            _assert_matches_batch(engine, now_ms)
            key = session_moment_key(now_ms, engine._session_windows)
            key_changes += previous_key is not None and key != previous_key
            previous_key = key
    assert key_changes >= 12  # прохід справді перетнув межі, а не стояв в одному стані


def _session_edges_ms(engine, day_from_ms, day_to_ms):
    """Межі кожної сесії (північ її годинника, відкриття, закриття, killzone) в обох сезонах — мить до і сама межа."""
    edges = []
    for day_ms in range(day_from_ms, day_to_ms, _DAY_MS):
        for sw in engine._session_windows:
            for minute in {0, sw.open_utc_min, sw.close_utc_min, sw.kz_start_min, sw.kz_end_min}:
                for winter_shift_ms in (0, _HOUR_MS):  # узимку правила сесії межа на годину пізніше за UTC
                    edge_ms = day_ms + minute * _MIN_MS + winter_shift_ms
                    edges += [edge_ms - 1, edge_ms]
    return edges


@pytest.mark.parametrize("day_from, day_to, switch", [
    ("2026-03-06", "2026-03-10", "2026-03-08 07:00"),  # США навесні (02:00 за Нью-Йорком)
    ("2026-03-27", "2026-03-31", "2026-03-29 01:00"),  # ЄС навесні
    ("2026-10-23", "2026-10-27", "2026-10-25 01:00"),  # ЄС восени
    ("2026-10-30", "2026-11-03", "2026-11-01 06:00"),  # США восени
])
def test_cached_levels_match_batch_while_feeding_through_every_2026_dst_switch(monkeypatch, day_from, day_to, switch):
    """Пт–Вт навколо кожного переходу 2026 року: стрічка живе (нові хвилини, фінал поверх tick_promoted, витіснення),
    момент проходить межі всіх сесій в обох сезонах і сам перехід (±1 мс). Мала стрічка — 150 барів по 20 хв ≈ 50 год,
    як 2880 M1, — щоб витіснення і обидва стани покриття попередньої сесії траплялися в межах проходу."""
    monkeypatch.setattr("core.smc.engine._SESSION_M1_CAPACITY", 150)
    engine = _engine()
    start_ms, end_ms = _ms(day_from + " 00:07"), _ms(day_to + " 23:59")
    events = []  # (момент, порядок, бар або None — лише перевірка)
    for i, open_ms in enumerate(range(start_ms, end_ms, 20 * _MIN_MS)):
        events.append((open_ms + _MIN_MS, len(events), _m1(open_ms, _price(i), "tick_promoted")))
        if i % 3 == 0:  # фінал брокера тієї самої хвилини з іншим OHLC заміщує tick_promoted
            events.append((open_ms + _MIN_MS + 5_000, len(events), _m1(open_ms, _price(i) + 0.5, spread=1.7)))
    checkpoints = _session_edges_ms(engine, _ms(day_from + " 00:00"), _ms(day_to + " 00:00") + _DAY_MS)
    checkpoints += [_ms(switch) - 1, _ms(switch), _ms(switch) + 1]
    events += [(t, len(events) + i, None) for i, t in enumerate(checkpoints) if start_ms < t < end_ms]

    prev_asia_covered = set()
    for now_ms, _order, bar in sorted(events, key=lambda e: e[:2]):
        if bar is not None:
            engine.feed_m1_bar(bar)
        _assert_matches_batch(engine, now_ms)
        prev_asia_covered.add(any(lv.kind == "p_as_h" for lv in engine.get_session_levels(_SYM, now_ms)))
    assert len(engine._session_m1_bars[_SYM]) == 150  # стрічка повна, витіснення відбувалися
    assert prev_asia_covered == {True, False}  # у понеділок п'ятниця вже витіснена, у вівторок понеділок покритий


def test_every_kind_of_tape_change_invalidates_and_a_change_free_feed_does_not():
    engine = _engine()
    start = _ms("2026-10-21 12:00")
    hole = start + 100 * _MIN_MS
    engine.feed_m1_bars_bulk(_SYM, [_m1(start + i * _MIN_MS, _price(i)) for i in range(_SESSION_M1_CAPACITY + 1)
                                    if start + i * _MIN_MS != hole])
    now_ms = start + _SESSION_M1_CAPACITY * _MIN_MS + 30_000  # Пт 23.10 12:00:30 — Лондон і Нью-Йорк активні
    nxt = start + (_SESSION_M1_CAPACITY + 1) * _MIN_MS
    steps = (
        ("нова хвилина", [_m1(nxt, 4100.0)], True),
        ("tick_promoted наступної", [_m1(nxt + _MIN_MS, 4101.0, "tick_promoted")], True),
        ("фінал заміщує tick_promoted", [_m1(nxt + _MIN_MS, 4101.0, spread=9.0)], True),
        ("рівний повтор фіналу", [_m1(nxt + _MIN_MS, 4101.0, spread=9.0)], False),
        ("tick_promoted після фіналу", [_m1(nxt + _MIN_MS, 4190.0, "tick_promoted")], False),
        ("tick_promoted і новіші бари", [_m1(nxt + 2 * _MIN_MS, 4102.0, "tick_promoted"),
                                         _m1(nxt + 3 * _MIN_MS, 4103.0), _m1(nxt + 4 * _MIN_MS, 4104.0)], True),
        ("фінал усередині стрічки — новий максимум сесії", [_m1(nxt + 2 * _MIN_MS, 4102.0, spread=80.0)], True),
        ("дірка всередині повної стрічки — найстаріший виходить", [_m1(hole, 3990.0)], True),
        ("бар, старший за повну стрічку", [_m1(start - _MIN_MS, 1.0)], False),
    )
    _assert_matches_batch(engine, now_ms)
    for name, bars, changes in steps:
        version = engine._session_m1_versions[_SYM]
        if len(bars) == 1:
            engine.feed_m1_bar(bars[0])
        else:
            engine.feed_m1_bars_bulk(_SYM, bars)
        assert (engine._session_m1_versions[_SYM] != version) is changes, name
        _assert_matches_batch(engine, now_ms)


def test_one_moment_computes_once_for_levels_states_and_display_levels_of_every_tf(monkeypatch):
    engine = _engine()
    start = _ms("2026-10-21 12:00")
    engine.feed_m1_bars_bulk(_SYM, [_m1(start + i * _MIN_MS, _price(i)) for i in range(_SESSION_M1_CAPACITY)])
    now_ms = start + _SESSION_M1_CAPACITY * _MIN_MS
    calls = []
    real = sessions_mod.compute_session_levels
    monkeypatch.setattr(sessions_mod, "compute_session_levels", lambda *a, **kw: calls.append(1) or real(*a, **kw))
    engine.get_session_levels(_SYM, now_ms)
    engine.get_session_states(_SYM, now_ms + 20_000)
    for tf_s in (300, 900, 3600, 14400, _D1_S):
        engine.get_display_levels(_SYM, tf_s, now_ms + 40_000)
    assert len(calls) == 1
    engine.feed_m1_bar(_m1(now_ms, 4100.0))
    engine.get_session_levels(_SYM, now_ms + 60_000)
    assert len(calls) == 2


def test_caller_mutating_the_returned_list_does_not_touch_the_cache():
    engine = _engine()
    start = _ms("2026-10-22 06:00")
    engine.feed_m1_bars_bulk(_SYM, [_m1(start + i * _MIN_MS, _price(i)) for i in range(120)])
    now_ms = start + 120 * _MIN_MS
    expected = list(engine.get_session_levels(_SYM, now_ms))
    engine.get_session_levels(_SYM, now_ms).clear()
    engine.get_session_states(_SYM, now_ms).clear()
    assert expected and engine.get_session_levels(_SYM, now_ms) == expected
    assert engine.get_session_states(_SYM, now_ms)


def _data_root():
    return pathlib.Path(os.environ.get("V3_DATA_ROOT") or REPO / CFG["data_root"])


@pytest.mark.skipif(not (_data_root() / "XAU_USD" / "tf_60").is_dir(),
                    reason="data_v3 з M1 XAU немає поруч (V3_DATA_ROOT або data_root config) — справжні бари недоступні")
def test_cached_levels_match_batch_on_real_xau_m1_tape_snapshots():
    from runtime.smc.smc_runner import _bar_dict_to_candle_bar
    from runtime.store.layers.disk_layer import DiskLayer

    end = _ms("2026-09-25 20:45")  # закриття металів у п'ятницю (EDT)
    rows, _geom = DiskLayer(str(_data_root())).read_window_with_geom(
        _SYM, 60, _SESSION_M1_CAPACITY + 5 * 1440, to_open_ms=end - _MIN_MS, use_tail=True, skip_preview=True)
    bars = [bar for bar in (_bar_dict_to_candle_bar(row, _SYM, 60) for row in rows) if bar is not None]
    assert bars and bars[-1].close_time_ms == end
    engine = _engine()
    # знімок стрічки → прохід моменту по ній: Чт 18 год кроком 5 хв; вихідні до Пн 00:00 кроком 15 хв
    weekend_min = (_ms("2026-09-28 00:00") - end) // _MIN_MS
    snapshots = ((_ms("2026-09-24 06:00"), 5, 18 * 60), (end, 15, weekend_min))
    fed = 0
    for snapshot_ms, step_min, span_min in snapshots:
        version = engine._session_m1_versions.get(_SYM, 0)
        new_minutes = 0
        while fed < len(bars) and bars[fed].close_time_ms <= snapshot_ms:
            engine.feed_m1_bar(bars[fed])
            engine.feed_m1_bar(bars[fed])  # кожна хвилина приходить кількома шляхами — рівний повтор не змінює версію
            fed, new_minutes = fed + 1, new_minutes + 1
        assert engine._session_m1_versions[_SYM] - version == new_minutes
        for minute in range(0, span_min, step_min):
            _assert_matches_batch(engine, snapshot_ms + minute * _MIN_MS)
