"""Розбір символу, п. 4–5: ліквідність поруч і сесія (ADR-0113 Ф3).

Ліквідність — лише незнята (детектор core.smc.liquidity_sweep) і лише завершених періодів, як цілі наративу; сесія —
календар платформи з сезоном кожної сесії і торговий календар символу для «наступної».
"""
from __future__ import annotations

import datetime as dt

from core.smc.briefing import liquidity_section, session_section
from core.smc.sessions import OFF_SESSION, next_session_open
from core.smc.types import SmcLevel
from test_sessions_dst import _ms, _repo_windows

H1 = 3600


def _level(kind, price, state="fixed"):
    return SmcLevel(id="%s_%d" % (kind, price * 100), symbol="XAU/USD", tf_s=86400, kind=kind, price=price,
                    time_ms=1_000, touches=1, state=state)


def _texts(section):
    return [row.text for row in section.rows]


# ── Ліквідність ──────────────────────────────────────────────


def test_nearest_untaken_liquidity_above_and_below_with_atr_distance_and_pools():
    levels = [_level("pdh", 5100.0), _level("lon_h", 5100.0), _level("p_as_h", 5080.0), _level("eq_highs", 5090.0),
              _level("dh", 5085.0, state="forming"), _level("pdl", 5000.0), _level("p_ny_l", 5020.0),
              _level("p_lon_l", 5060.0)]  # лой над ціною — не ліквідність над нею
    reached = {"level:p_as_h_508000": (7, None), "level:eq_highs_509000": (9, None), "level:p_ny_l_502000": (None, 8)}

    section = liquidity_section(H1, levels, reached, 5050.0, 20.0, 2, per_side=2)

    assert (section.key, section.title, [row.tf for row in section.rows]) == ("liquidity", "Ліквідність поруч",
                                                                              ["H1", "H1"])
    assert _texts(section) == ["Вище: EQH 5090.00 · 2.0 ATR, PDH/хай Лондона 5100.00 · 2.5 ATR",
                               "Нижче: PDL 5000.00 · 2.5 ATR"]


def test_liquidity_per_side_limit_no_atr_and_nothing_visible():
    levels = [_level("pdh", 5100.0), _level("pwh", 5200.0)]

    assert _texts(liquidity_section(H1, levels, {}, 5050.0, 0.0, None, per_side=1)) == [
        "Вище: PDH 5100.00", "Нижче: незнятої ліквідності не видно"]


# ── Сесія ────────────────────────────────────────────────────


def test_next_session_open_follows_each_session_season():
    windows = list(_repo_windows().values())

    assert next_session_open(_ms("2026-10-14 05:00"), windows) == ("london", _ms("2026-10-14 07:00"))
    assert next_session_open(_ms("2026-10-28 06:00"), windows) == ("london", _ms("2026-10-28 08:00"))  # Лондон узимку
    assert next_session_open(_ms("2026-11-10 12:30"), windows) == ("newyork", _ms("2026-11-10 13:00"))


def test_next_session_open_skips_non_trading_minutes_of_the_symbol_calendar():
    windows = list(_repo_windows().values())

    def fx_week(moment_ms):  # п'ятниця 21:00 → неділя 22:00 UTC — ринок закрито
        moment = dt.datetime.fromtimestamp(moment_ms / 1000, dt.timezone.utc)
        hours = moment.weekday() * 24 + moment.hour
        return not (4 * 24 + 21 <= hours < 6 * 24 + 22)

    assert next_session_open(_ms("2026-10-16 20:00"), windows, fx_week) == ("asia", _ms("2026-10-19 00:00"))
    assert next_session_open(_ms("2026-10-16 20:00"), windows, lambda _ms_: False) is None


def test_session_section_says_session_killzone_and_time_to_the_next_open():
    in_kz = session_section(_ms("2026-10-14 08:30"), "london", True, ("newyork", _ms("2026-10-14 12:00")))
    weekend = session_section(_ms("2026-10-16 20:00"), OFF_SESSION, False, ("asia", _ms("2026-10-19 00:00")))

    assert (in_kz.key, in_kz.rows[0].text) == ("session", "Лондон — кілзона")
    assert in_kz.rows[0].notes == ("Далі: Нью-Йорк о 12:00 UTC — через 3 год 30 хв",)
    assert weekend.rows[0].text == "Поза торговими сесіями"
    assert weekend.rows[0].notes == ("Далі: Азія Пн 00:00 UTC — через 2 д 4 год",)
    assert session_section(0, "asia", False, None).rows[0].notes == ()
