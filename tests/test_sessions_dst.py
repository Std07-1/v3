"""ADR-0104 S0: сесії за місцевим годинником біржі — межі зсуваються з переходами літнього/зимового часу.

До S0 вікна були фіксованим UTC (літо): після 25.10 (EU) / 01.11 (US) Лондон і Нью-Йорк рахувались би на годину раніше
справжнього відкриття, а їхні H/L — з чужих хвилин. Азія (Токіо) переходів не має.
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib

import pytest

from core.model.bars import CandleBar
from core.session_anchor import SEASON_RULE_EU, SEASON_RULE_US, SEASON_WINTER, _summer_bounds_ms, calendar_season
from core.smc.sessions import _WINTER_SHIFT_MS, _bar_in_killzone, _bar_in_session, _session_clock_ms, \
    _session_open_ms, compute_session_levels, get_current_session, load_session_windows

REPO = pathlib.Path(__file__).resolve().parents[1]


def _repo_windows():
    cfg = json.loads((REPO / "config.json").read_text(encoding="utf-8"))
    return {w.name: w for w in load_session_windows(cfg["smc"]["sessions"]["definitions"])}


def _ms(text):
    return int(dt.datetime.strptime(text, "%Y-%m-%d %H:%M").replace(tzinfo=dt.timezone.utc).timestamp() * 1000)


def test_repo_sessions_follow_their_exchange_clock():
    windows = _repo_windows()
    assert (windows["asia"].season_rule, windows["london"].season_rule, windows["newyork"].season_rule) == (
        "none", "eu", "us")


@pytest.mark.parametrize("session, inside, outside", [
    # літо (обидва правила літні): Лондон 07:00–16:00 UTC, Нью-Йорк 12:00–21:00 UTC
    ("london", ["2026-09-24 07:00", "2026-09-24 15:59"], ["2026-09-24 06:59", "2026-09-24 16:00"]),
    ("newyork", ["2026-09-24 12:00", "2026-09-24 20:59"], ["2026-09-24 11:59", "2026-09-24 21:00"]),
    # розрив 26–31.10: Лондон уже взимку (08:00–17:00 UTC), Нью-Йорк ще влітку (12:00–21:00 UTC)
    ("london", ["2026-10-28 08:00", "2026-10-28 16:59"], ["2026-10-28 07:30", "2026-10-28 17:00"]),
    ("newyork", ["2026-10-28 12:00", "2026-10-28 20:59"], ["2026-10-28 11:59", "2026-10-28 21:00"]),
    # зима: Лондон 08:00–17:00 UTC, Нью-Йорк 13:00–22:00 UTC
    ("london", ["2026-11-10 08:00", "2026-11-10 16:59"], ["2026-11-10 07:59", "2026-11-10 17:00"]),
    ("newyork", ["2026-11-10 13:00", "2026-11-10 21:59"], ["2026-11-10 12:30", "2026-11-10 22:00"]),
    # Азія (Токіо) — без переходу: 00:00–07:00 UTC цілий рік
    ("asia", ["2026-09-24 00:00", "2026-11-10 06:59"], ["2026-09-24 07:00", "2026-11-10 07:00"]),
])
def test_session_window_moves_with_its_exchange_dst(session, inside, outside):
    sw = _repo_windows()[session]
    assert all(_bar_in_session(_ms(t), sw) for t in inside)
    assert not any(_bar_in_session(_ms(t), sw) for t in outside)


def test_killzones_move_with_the_session():
    windows = _repo_windows()
    assert _bar_in_killzone(_ms("2026-11-10 08:30"), windows["london"])  # зима: 08:00–11:00 UTC
    assert not _bar_in_killzone(_ms("2026-11-10 07:30"), windows["london"])
    assert _bar_in_killzone(_ms("2026-11-10 13:30"), windows["newyork"])  # зима: 13:00–16:00 UTC
    assert not _bar_in_killzone(_ms("2026-11-10 12:30"), windows["newyork"])


def test_current_session_in_winter_overlap_keeps_london_priority():
    windows = list(_repo_windows().values())
    assert get_current_session(_ms("2026-11-10 13:30"), windows)[0] == "london"
    assert get_current_session(_ms("2026-11-10 17:30"), windows)[0] == "newyork"  # Лондон закрився о 17:00 UTC
    assert get_current_session(_ms("2026-11-10 07:30"), windows) == ("off_session", False)  # між Азією і Лондоном


def _bar(t, h, low):
    open_ms = _ms(t)
    return CandleBar(symbol="XAU/USD", tf_s=60, open_time_ms=open_ms, close_time_ms=open_ms + 60000, o=low, h=h,
                     low=low, c=h, v=1.0, complete=True, src="history")


def test_winter_new_york_high_includes_its_last_hour_and_excludes_the_hour_before_open():
    ny = _repo_windows()["newyork"]
    bars = [_bar("2026-11-10 12:30", 5000.0, 4000.0),   # до відкриття NY узимку — не NY
            _bar("2026-11-10 13:00", 4100.0, 4050.0),
            _bar("2026-11-10 21:30", 4200.0, 4060.0)]   # остання година NY узимку — NY
    levels, states = compute_session_levels(bars, [ny], _ms("2026-11-10 21:45"), "XAU/USD")
    by_kind = {lv.kind: lv.price for lv in levels}
    assert by_kind == {"ny_h": 4200.0, "ny_l": 4050.0} and states[0].active


def test_unknown_season_rule_is_refused_loudly():
    with pytest.raises(ValueError, match="SESSION_SEASON_RULE_INVALID session=london"):
        load_session_windows({"london": {"open_utc": "07:00", "close_utc": "16:00", "season_rule": "uk"}})


_HOUR_MS = 3_600_000


@pytest.mark.parametrize("rule", [SEASON_RULE_US, SEASON_RULE_EU])
def test_dst_switch_moments_sit_on_utc_hour_edges(rule):
    """Опора сезону сесій «раз на годину» (sessions._is_winter_hour): перехід усередині години зламав би її."""
    for year in range(1970, 2101):
        assert all(edge_ms % _HOUR_MS == 0 for edge_ms in _summer_bounds_ms(year, rule)), year


def _winter(moment_ms, sw):
    return calendar_season(moment_ms, sw.season_rule) == SEASON_WINTER


@pytest.mark.parametrize("session", ["asia", "london", "newyork"])
def test_session_season_by_hour_equals_per_moment_season_around_every_switch(session):
    """Годинник і відкриття сесії із сезоном на годину == сезон самого моменту (calendar_season), 2000–2040."""
    sw = _repo_windows()[session]
    probe_rule = sw.season_rule if sw.season_rule != "none" else SEASON_RULE_US  # Азія без переходу: проби — межі США
    offsets_ms = (-_HOUR_MS, -60_000, -1, 0, 1, 60_000, _HOUR_MS - 1, _HOUR_MS)
    for year in range(2000, 2041):
        for edge_ms in _summer_bounds_ms(year, probe_rule):
            for moment_ms in (edge_ms + d for d in offsets_ms):
                expected_clock_ms = moment_ms - (_WINTER_SHIFT_MS if _winter(moment_ms, sw) else 0)
                assert _session_clock_ms(moment_ms, sw) == expected_clock_ms, moment_ms
            midnight_ms = edge_ms - edge_ms % 86_400_000
            for day_ms in (midnight_ms - 86_400_000, midnight_ms, midnight_ms + 86_400_000):
                clock_open_ms = day_ms + sw.open_utc_min * 60_000
                expected_open_ms = clock_open_ms + (_WINTER_SHIFT_MS if _winter(clock_open_ms, sw) else 0)
                assert _session_open_ms(day_ms, sw) == expected_open_ms, day_ms
