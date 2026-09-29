"""Стрічка M1 движка SMC (сесії H/L, DO/WO, H4 forming): одна хвилина — один бар, фінал брокера важить більше (I3).

Кожна хвилина приходить кілька разів: миттєвий `tick_promoted` з тіків (open = перший тік), за ним фінал брокера
`history` з іншим OHLC, і кожен ще кількома шляхами подачі (delta loop, фоновий feed, підписка на M1). Прод 29.09:
у кільці XAU 67 з 68 хвилин мали обидва бари з різним OHLC; стрічка тримала ~3 копії хвилини (H4 forming: 212 записів
за 65 хв), DO брав open `tick_promoted` (4183.57 проти 4182.24 у SSOT), вікно 2880 записів стискалося до ~15 год.
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
from collections import deque

from core.model.bars import CandleBar
from core.smc.config import SmcConfig
from core.smc.engine import _SESSION_M1_CAPACITY, SmcEngine
from core.smc.key_levels import compute_day_open

REPO = pathlib.Path(__file__).resolve().parents[1]
_SYM = "XAU/USD"
_D1_MS = 86_400_000


def _ms(text):
    return int(dt.datetime.strptime(text, "%Y-%m-%d %H:%M").replace(tzinfo=dt.timezone.utc).timestamp() * 1000)


def _m1(open_ms, o, h, low, c, src):
    return CandleBar(symbol=_SYM, tf_s=60, open_time_ms=open_ms, close_time_ms=open_ms + 60_000, o=o, h=h, low=low,
                     c=c, v=1.0, complete=True, src=src)


def _promoted(open_ms, o, h=None, low=None, c=None):
    return _m1(open_ms, o, h if h is not None else o + 1, low if low is not None else o - 1, c if c is not None else o,
               "tick_promoted")


def _final(open_ms, o, h=None, low=None, c=None):
    return _m1(open_ms, o, h if h is not None else o + 1, low if low is not None else o - 1, c if c is not None else o,
               "history")


def _engine():
    return SmcEngine(SmcConfig.from_dict(json.loads((REPO / "config.json").read_text(encoding="utf-8"))["smc"]))


def _tape(engine):
    return list(engine._session_m1_bars[_SYM])


def test_final_replaces_tick_promoted_of_the_same_minute():
    engine = _engine()
    t = _ms("2026-09-29 22:01")
    engine.feed_m1_bar(_promoted(t, 4183.57))
    engine.feed_m1_bar(_final(t, 4182.24))
    assert [(b.src, b.o) for b in _tape(engine)] == [("history", 4182.24)]


def test_tick_promoted_never_replaces_a_final():
    engine = _engine()
    t = _ms("2026-09-29 22:01")
    engine.feed_m1_bar(_final(t, 4182.24))
    engine.feed_m1_bar(_promoted(t, 4183.57))
    assert [(b.src, b.o) for b in _tape(engine)] == [("history", 4182.24)]


def test_every_feed_path_delivering_the_same_minute_keeps_one_bar():
    engine = _engine()
    t = _ms("2026-09-29 22:01")
    for _path in range(3):  # delta loop, фоновий feed, підписка глядача на M1
        engine.feed_m1_bar(_promoted(t, 4183.57))
        engine.feed_m1_bar(_final(t, 4182.24))
    engine.feed_m1_bars_bulk(_SYM, [_final(t, 4182.24)])
    assert len(_tape(engine)) == 1


def test_late_final_of_an_older_minute_lands_in_place_and_tape_stays_sorted():
    engine = _engine()
    t = _ms("2026-09-29 22:00")
    engine.feed_m1_bar(_promoted(t, 100.0))
    engine.feed_m1_bar(_promoted(t + 60_000, 101.0))
    engine.feed_m1_bar(_promoted(t + 120_000, 102.0))
    engine.feed_m1_bar(_final(t, 99.5))  # фінал хвилини 22:00 після двох наступних tick_promoted
    engine.feed_m1_bar(_final(t - 60_000, 98.0))  # фінал хвилини, якої в стрічці ще не було
    assert [(b.open_time_ms - t, b.src) for b in _tape(engine)] == [
        (-60_000, "history"), (0, "history"), (60_000, "tick_promoted"), (120_000, "tick_promoted")]


def test_full_tape_keeps_the_last_48_hours_of_distinct_minutes():
    engine = _engine()
    start = _ms("2026-09-27 21:00")
    for i in range(_SESSION_M1_CAPACITY + 10):
        engine.feed_m1_bar(_promoted(start + i * 60_000, 100.0))
        engine.feed_m1_bar(_final(start + i * 60_000, 100.0))
    tape = _tape(engine)
    assert len(tape) == _SESSION_M1_CAPACITY == 48 * 60
    assert tape[0].open_time_ms == start + 10 * 60_000
    assert all(b.src == "history" for b in tape)


def test_full_tape_drops_a_bar_older_than_the_window_and_inserts_a_missing_one_inside_it():
    tape_owner = _engine()
    start = _ms("2026-09-27 21:00")
    bars = [_final(start + i * 60_000, 100.0) for i in range(_SESSION_M1_CAPACITY + 1) if i != 5]
    tape_owner.feed_m1_bars_bulk(_SYM, bars)
    first = _tape(tape_owner)[0].open_time_ms
    tape_owner.feed_m1_bar(_final(first - 60_000, 1.0))  # старший за всю повну стрічку — поза вікном
    assert _tape(tape_owner)[0].open_time_ms == first
    missing = start + 5 * 60_000
    tape_owner.feed_m1_bar(_final(missing, 2.0))  # дірка всередині вікна заповнюється, найстаріший бар виходить
    tape = _tape(tape_owner)
    assert len(tape) == _SESSION_M1_CAPACITY
    assert [b.open_time_ms for b in tape] == sorted({b.open_time_ms for b in tape})
    assert any(b.open_time_ms == missing and b.o == 2.0 for b in tape)


def test_day_open_is_the_broker_final_open_not_the_first_tick():
    engine = _engine()
    d1_close = _ms("2026-09-29 22:00")
    last_d1 = CandleBar(symbol=_SYM, tf_s=86400, open_time_ms=d1_close - _D1_MS, close_time_ms=d1_close, o=4100.0,
                        h=4200.0, low=4050.0, c=4180.0, v=1.0, complete=True, src="history")
    engine.feed_m1_bar(_promoted(d1_close, 4183.57))
    engine.feed_m1_bar(_final(d1_close, 4182.24))
    assert [lv.price for lv in compute_day_open([last_d1], _tape(engine))] == [4182.24]


def test_session_high_comes_from_finals_not_from_tick_spikes():
    engine = _engine()
    asia_open = _ms("2026-09-30 00:00")
    # правило, а не прод-випадок: на проді екстремуми tick_promoted не виходили за фінал (754 хвилини, 30.09)
    engine.feed_m1_bar(_promoted(asia_open, 4180.0, h=4199.0))
    engine.feed_m1_bar(_final(asia_open, 4180.0, h=4185.0))
    engine.feed_m1_bar(_final(asia_open + 60_000, 4181.0, h=4184.0))
    levels = {lv.kind: lv.price for lv in engine.get_session_levels(_SYM, asia_open + 120_000)}
    assert levels["as_h"] == 4185.0


def test_store_accepts_a_plain_deque_as_the_engine_holds_it():
    engine = _engine()
    engine.feed_m1_bar(_final(_ms("2026-09-29 22:00"), 1.0))
    assert isinstance(engine._session_m1_bars[_SYM], deque)
    assert engine._session_m1_bars[_SYM].maxlen == _SESSION_M1_CAPACITY
