"""ADR-0104 S3: попередній торговий тиждень (PWH/PWL) і відкриття торгової доби (DO).

Тиждень = бакети D1 торгових дат Пн–Пт (бакет відкривається о 17:00 NY напередодні: 21:00 UTC улітку, 22:00 UTC узимку).
Поточний тиждень задає закриття останньої завершеної D1; попередній — тиждень перед ним. DO — open першого M1 після
закриття останньої завершеної D1 (після вихідних — відкриття ринку в неділю, а не межа п'ятниці).
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib

from core.model.bars import CandleBar
from core.smc.config import SmcConfig
from core.smc.engine import SmcEngine
from core.smc.key_levels import compute_day_open, compute_week_levels, compute_week_open

REPO = pathlib.Path(__file__).resolve().parents[1]
_SYM = "XAU/USD"
_D1_MS = 86_400_000


def _ms(text):
    return int(dt.datetime.strptime(text, "%Y-%m-%d %H:%M").replace(tzinfo=dt.timezone.utc).timestamp() * 1000)


def _d1(open_text, high, low, complete=True):
    open_ms = _ms(open_text)
    return CandleBar(symbol=_SYM, tf_s=86400, open_time_ms=open_ms, close_time_ms=open_ms + _D1_MS, o=low, h=high,
                     low=low, c=high, v=1.0, complete=complete, src="test")


def _m1(open_text, price):
    open_ms = _ms(open_text)
    return CandleBar(symbol=_SYM, tf_s=60, open_time_ms=open_ms, close_time_ms=open_ms + 60_000, o=price,
                     h=price + 1, low=price - 1, c=price, v=1.0, complete=True, src="test")


def _week(first_open_day, highs_lows, anchor="21:00"):
    """Бакети Пн–Пт тижня: перший відкривається в неділю `first_open_day` о `anchor` UTC."""
    start = dt.datetime.strptime(first_open_day, "%Y-%m-%d")
    return [_d1("%s %s" % ((start + dt.timedelta(days=i)).strftime("%Y-%m-%d"), anchor), h, low)
            for i, (h, low) in enumerate(highs_lows)]


_WEEK_14_18 = _week("2026-09-13", [(4100, 4050), (4110, 4060), (4120, 4070), (4130, 4040), (4125, 4080)])
_WEEK_21_25 = _week("2026-09-20", [(4200, 4150), (4260, 4170), (4230, 4160), (4240, 4120), (4315.68, 4254.4)])


def _prices(levels):
    return {lv.kind: lv.price for lv in levels}


def test_monday_previous_week_is_the_week_that_just_closed():
    monday_forming = _d1("2026-09-27 21:00", 4290, 4110, complete=False)
    assert _prices(compute_week_levels(_WEEK_14_18 + _WEEK_21_25 + [monday_forming])) == {"pwh": 4315.68, "pwl": 4120}


def test_midweek_keeps_last_week_as_previous():
    this_week = _week("2026-09-27", [(4290, 4110), (4200, 4100)])  # Пн і Вт 28–29.09 завершені
    assert _prices(compute_week_levels(_WEEK_21_25 + this_week)) == {"pwh": 4315.68, "pwl": 4120}


def test_friday_before_close_still_treats_the_current_week_as_current():
    until_thursday = _WEEK_14_18 + _WEEK_21_25[:4]  # п'ятничний бакет 21–25 ще не завершений
    assert _prices(compute_week_levels(until_thursday)) == {"pwh": 4130, "pwl": 4040}


def test_winter_anchor_22_utc_groups_the_same_way():
    week_9_13 = _week("2026-11-08", [(4000, 3950), (4050, 3900), (4010, 3960), (4020, 3970), (4030, 3980)], "22:00")
    week_16 = _week("2026-11-15", [(4100, 4000)], "22:00")
    assert _prices(compute_week_levels(week_9_13 + week_16)) == {"pwh": 4050, "pwl": 3900}


def test_missing_previous_week_is_not_replaced_by_an_older_one():
    this_week = _week("2026-09-27", [(4290, 4110)])  # тиждень 21–25.09 відсутній у даних
    assert compute_week_levels(_WEEK_14_18 + this_week) == []


def test_saturday_stub_after_friday_close_is_not_counted_in_that_week():
    stub = _d1("2026-09-25 21:00", 9999, 1)  # огризок після п'ятничного закриття (торгова дата — субота)
    monday = _d1("2026-09-27 21:00", 4290, 4110)
    assert _prices(compute_week_levels(_WEEK_21_25 + [stub, monday])) == {"pwh": 4315.68, "pwl": 4120}


def test_week_levels_contract_and_formation_bar():
    pwh, pwl = compute_week_levels(_WEEK_21_25 + [_d1("2026-09-27 21:00", 4290, 4110)])
    assert pwh.key == "w1:high:XAU/USD:2026-09-20T21:00Z" and pwl.key == "w1:low:XAU/USD:2026-09-20T21:00Z"
    assert (pwh.family, pwh.state, pwh.tier) == ("week", "fixed", 1)
    assert pwh.time_ms == _ms("2026-09-24 21:00") and pwl.time_ms == _ms("2026-09-23 21:00")  # свічки екстремумів


def test_day_open_after_the_weekend_is_the_market_open_not_the_friday_boundary():
    m1 = [_m1("2026-09-25 20:59", 4280), _m1("2026-09-27 21:05", 4262.5), _m1("2026-09-27 21:06", 4270)]
    (do,) = compute_day_open(_WEEK_21_25, m1)
    assert (do.kind, do.price, do.time_ms) == ("do", 4262.5, _ms("2026-09-27 21:05"))
    assert do.key == "d1:open:XAU/USD:2026-09-27T21:05Z"
    assert (do.family, do.state, do.tier) == ("open", "fixed", 2)


def test_no_day_open_until_the_first_bar_of_the_new_day():
    assert compute_day_open(_WEEK_21_25, [_m1("2026-09-25 20:59", 4280)]) == []


def test_engine_offers_week_everywhere_and_day_open_below_d1_with_table_defaults():
    cfg = json.loads((REPO / "config.json").read_text(encoding="utf-8"))["smc"]
    engine = SmcEngine(SmcConfig.from_dict(cfg))
    engine.update(_SYM, 86400, _WEEK_14_18 + _WEEK_21_25)
    engine.feed_m1_bars_bulk(_SYM, [_m1("2026-09-27 21:05", 4262.5)])
    now = _ms("2026-09-28 09:00")
    m5 = {lv.kind: lv for lv in engine.get_display_levels(_SYM, 300, now)}
    assert (m5["pwh"].group, m5["pwh"].auto, m5["do"].group, m5["do"].auto) == ("week", True, "open", True)
    d1 = {lv.kind: lv for lv in engine.get_display_levels(_SYM, 86400, now)}
    assert "do" not in d1 and d1["pwh"].auto is True
    h4 = {lv.kind: lv for lv in engine.get_display_levels(_SYM, 14400, now)}
    assert h4["do"].auto is False  # профіль §3.4: на H4 відкриття доби не типове


# ── S7: відкриття тижня (WO) ──

def test_week_open_midweek_is_the_open_of_the_first_d1_of_the_week():
    monday = _d1("2026-09-27 21:00", 4290, 4110)  # завершена свічка понеділка, open = low у фікстурі
    (wo,) = compute_week_open(_WEEK_21_25 + [monday], [])
    assert (wo.kind, wo.price, wo.time_ms) == ("wo", 4110, _ms("2026-09-27 21:00"))
    assert (wo.group, wo.family, wo.state, wo.tier) == (None, "open", "fixed", 2)


def test_week_open_on_monday_comes_from_the_first_m1_and_keeps_the_same_key():
    m1 = [_m1("2026-09-27 21:05", 4262.5)]
    (monday_wo,) = compute_week_open(_WEEK_21_25, m1)  # понеділок ще формується
    (tuesday_wo,) = compute_week_open(_WEEK_21_25 + [_d1("2026-09-27 21:00", 4290, 4262.5)], [])
    assert monday_wo.price == tuesday_wo.price == 4262.5
    assert monday_wo.key == tuesday_wo.key == "w1:open:XAU/USD:2026-09-28T00:00Z"


def test_no_week_open_without_d1_or_first_bar():
    assert compute_week_open([], [_m1("2026-09-27 21:05", 4262.5)]) == []
    assert compute_week_open(_WEEK_21_25, []) == []


def test_week_open_is_offered_below_d1_and_off_by_default():
    cfg = json.loads((REPO / "config.json").read_text(encoding="utf-8"))["smc"]
    engine = SmcEngine(SmcConfig.from_dict(cfg))
    engine.update(_SYM, 86400, _WEEK_21_25 + [_d1("2026-09-27 21:00", 4290, 4262.5)])
    now = _ms("2026-09-29 09:00")
    m15 = {lv.kind: lv for lv in engine.get_display_levels(_SYM, 900, now)}
    assert (m15["wo"].group, m15["wo"].auto, m15["wo"].price) == ("open_week", False, 4262.5)
    assert "wo" not in {lv.kind for lv in engine.get_display_levels(_SYM, 86400, now)}
