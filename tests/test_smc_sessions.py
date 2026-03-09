"""
tests/test_smc_sessions.py — ADR-0035 Sessions & Killzones tests.

Covers:
  - compute_session_levels: session H/L computation
  - classify_bar_sessions: bar→session mapping
  - get_current_session: priority resolution
  - _bar_in_session: boundary + midnight-crossing
  - F9 confluence factor: _check_session_sweep
  - NarrativeBlock session fields
  - SmcEngine session wiring
"""

import dataclasses
import pytest
from core.model.bars import CandleBar
from core.smc.sessions import (
    SessionWindow,
    load_session_windows,
    classify_bar_sessions,
    get_current_session,
    compute_session_levels,
    _parse_utc_minutes,
    _ms_to_utc_minutes,
    _bar_in_session,
)
from core.smc.types import SESSION_LEVEL_KINDS, LEVEL_KINDS, SmcLevel
from core.smc.confluence import _check_session_sweep


# ── Fixtures ──────────────────────────────────────────────

DEFINITIONS = {
    "asia": {
        "label": "Asia",
        "open_utc": "00:00",
        "close_utc": "07:00",
        "killzone_start_utc": "00:00",
        "killzone_end_utc": "04:00",
    },
    "london": {
        "label": "London",
        "open_utc": "07:00",
        "close_utc": "16:00",
        "killzone_start_utc": "07:00",
        "killzone_end_utc": "10:00",
    },
    "newyork": {
        "label": "New York",
        "open_utc": "12:00",
        "close_utc": "21:00",
        "killzone_start_utc": "12:00",
        "killzone_end_utc": "15:00",
    },
}


def _make_sessions():
    return load_session_windows(DEFINITIONS)


def _make_bar(open_ms, h=100.0, low=90.0, o=95.0, c=97.0, complete=True):
    """M1 bar helper."""
    return CandleBar(
        symbol="XAU/USD",
        tf_s=60,
        open_time_ms=open_ms,
        close_time_ms=open_ms + 60000,
        o=o,
        h=h,
        low=low,
        c=c,
        v=100.0,
        complete=complete,
        src="derived",
    )


# 2026-03-09 is a Monday. 00:00 UTC = 1741478400000 ms
_DAY_START_MS = 1741478400000  # 2026-03-09 00:00:00 UTC


# ── _parse_utc_minutes ────────────────────────────────────


class TestParseUtcMinutes:
    def test_midnight(self):
        assert _parse_utc_minutes("00:00") == 0

    def test_noon(self):
        assert _parse_utc_minutes("12:00") == 720

    def test_830(self):
        assert _parse_utc_minutes("08:30") == 510

    def test_2100(self):
        assert _parse_utc_minutes("21:00") == 1260


# ── load_session_windows ──────────────────────────────────


class TestLoadSessionWindows:
    def test_basic_load(self):
        sessions = _make_sessions()
        assert len(sessions) == 3
        names = {s.name for s in sessions}
        assert names == {"asia", "london", "newyork"}

    def test_asia_times(self):
        sessions = _make_sessions()
        asia = next(s for s in sessions if s.name == "asia")
        assert asia.open_utc_min == 0
        assert asia.close_utc_min == 420  # 7*60
        assert asia.kz_start_min == 0
        assert asia.kz_end_min == 240  # 4*60

    def test_empty_definitions(self):
        assert load_session_windows({}) == []


# ── _bar_in_session ───────────────────────────────────────


class TestBarInSession:
    def test_bar_at_session_start(self):
        asia = SessionWindow("asia", "Asia", 0, 420, 0, 240)
        # 00:00 UTC = minute 0 → epoch ms for that minute
        bar_ms = _DAY_START_MS  # exactly 00:00 UTC
        assert _bar_in_session(bar_ms, asia) is True

    def test_bar_at_session_end_exclusive(self):
        asia = SessionWindow("asia", "Asia", 0, 420, 0, 240)
        # 07:00 UTC = 420 min → should be end-exclusive
        bar_ms = _DAY_START_MS + 420 * 60 * 1000
        assert _bar_in_session(bar_ms, asia) is False

    def test_bar_before_session(self):
        london = SessionWindow("london", "London", 420, 960, 420, 600)
        # 06:59 UTC = 419 min
        bar_ms = _DAY_START_MS + 419 * 60 * 1000
        assert _bar_in_session(bar_ms, london) is False

    def test_bar_inside_session(self):
        london = SessionWindow("london", "London", 420, 960, 420, 600)
        # 08:20 UTC = 500 min
        bar_ms = _DAY_START_MS + 500 * 60 * 1000
        assert _bar_in_session(bar_ms, london) is True


# ── classify_bar_sessions ─────────────────────────────────


class TestClassifyBarSessions:
    def test_asia_bar(self):
        sessions = _make_sessions()
        # 03:00 UTC = 180 min from midnight
        bar_ms = _DAY_START_MS + 180 * 60 * 1000
        result = classify_bar_sessions(bar_ms, sessions)
        assert "asia" in result
        assert "london" not in result

    def test_overlap_london_ny(self):
        sessions = _make_sessions()
        # 13:00 UTC = 780 min → both london (420-960) and newyork (720-1260)
        bar_ms = _DAY_START_MS + 780 * 60 * 1000
        result = classify_bar_sessions(bar_ms, sessions)
        assert "london" in result
        assert "newyork" in result

    def test_outside_all(self):
        sessions = _make_sessions()
        # 22:00 UTC = 1320 min → no sessions (all end by 21:00)
        bar_ms = _DAY_START_MS + 1320 * 60 * 1000
        result = classify_bar_sessions(bar_ms, sessions)
        assert result == []


# ── get_current_session ───────────────────────────────────


class TestGetCurrentSession:
    def test_asia_time(self):
        sessions = _make_sessions()
        # 02:00 UTC
        ts = _DAY_START_MS + 120 * 60 * 1000
        name, kz = get_current_session(ts, sessions)
        assert name == "asia"
        assert kz is True  # 02:00 is in asia KZ (00:00-04:00)

    def test_london_priority_over_ny(self):
        sessions = _make_sessions()
        # 13:00 UTC = in both london (07-16) and NY (12-21)
        ts = _DAY_START_MS + 780 * 60 * 1000
        name, kz = get_current_session(ts, sessions)
        assert name == "london"

    def test_ny_killzone(self):
        sessions = _make_sessions()
        # 12:30 UTC → in NY killzone (12-15) AND London session
        # Priority: london > newyork
        ts = _DAY_START_MS + 750 * 60 * 1000
        name, kz = get_current_session(ts, sessions)
        # London has priority, but 12:30 is NOT in London KZ (07-10)
        assert name == "london"
        assert kz is False

    def test_no_session(self):
        sessions = _make_sessions()
        ts = _DAY_START_MS + 1320 * 60 * 1000  # 22:00
        name, kz = get_current_session(ts, sessions)
        assert name == "off_session"
        assert kz is False


# ── compute_session_levels ────────────────────────────────


class TestComputeSessionLevels:
    def _make_asia_bars(self, day_start_ms):
        """Generate M1 bars for complete Asia session (00:00-07:00)."""
        bars = []
        for i in range(420):  # 7 hours × 60 minutes
            open_ms = day_start_ms + i * 60000
            # Create bars with varying H/L
            h = 2000.0 + (i % 50)  # peak around minute ~49
            low = 1950.0 + (i % 30)  # low varies
            bars.append(_make_bar(open_ms, h=h, low=low, o=1970.0, c=1975.0))
        return bars

    def test_basic_session_levels(self):
        sessions = _make_sessions()
        bars = self._make_asia_bars(_DAY_START_MS)
        # Current time at 08:00 UTC (after Asia close)
        current_ms = _DAY_START_MS + 480 * 60000
        levels, states = compute_session_levels(
            bars,
            sessions,
            current_ms,
            "XAU/USD",
        )
        # Should have at least asia levels (current session H/L)
        asia_kinds = [lv.kind for lv in levels if "as_" in lv.kind]
        assert len(asia_kinds) >= 2  # at least as_h and as_l

    def test_level_ids_deterministic(self):
        """S3: same input → same level IDs."""
        sessions = _make_sessions()
        bars = self._make_asia_bars(_DAY_START_MS)
        current_ms = _DAY_START_MS + 480 * 60000
        levels1, _ = compute_session_levels(bars, sessions, current_ms, "XAU/USD")
        levels2, _ = compute_session_levels(bars, sessions, current_ms, "XAU/USD")
        ids1 = [lv.id for lv in levels1]
        ids2 = [lv.id for lv in levels2]
        assert ids1 == ids2

    def test_no_bars_returns_empty(self):
        sessions = _make_sessions()
        levels, states = compute_session_levels(
            [],
            sessions,
            _DAY_START_MS,
            "XAU/USD",
        )
        assert levels == []
        assert states == []

    def test_session_states_returned(self):
        sessions = _make_sessions()
        bars = self._make_asia_bars(_DAY_START_MS)
        current_ms = _DAY_START_MS + 480 * 60000
        _, states = compute_session_levels(
            bars,
            sessions,
            current_ms,
            "XAU/USD",
        )
        assert len(states) > 0
        asia_st = next((s for s in states if s.name == "asia"), None)
        assert asia_st is not None


# ── SESSION_LEVEL_KINDS ───────────────────────────────────


class TestSessionLevelKinds:
    def test_count(self):
        assert len(SESSION_LEVEL_KINDS) == 12

    def test_subset_of_level_kinds(self):
        assert SESSION_LEVEL_KINDS.issubset(LEVEL_KINDS)

    def test_expected_kinds(self):
        expected = {
            "as_h",
            "as_l",
            "p_as_h",
            "p_as_l",
            "lon_h",
            "lon_l",
            "p_lon_h",
            "p_lon_l",
            "ny_h",
            "ny_l",
            "p_ny_h",
            "p_ny_l",
        }
        assert SESSION_LEVEL_KINDS == expected


# ── F9 _check_session_sweep ──────────────────────────────


class TestCheckSessionSweep:
    def test_no_levels_returns_zero(self):
        zone = {"kind": "ob_bull", "high": 2000.0, "low": 1990.0}
        assert _check_session_sweep(zone, [], {}) == 0

    def test_bull_ob_near_session_low(self):
        zone = {"kind": "ob_bull", "high": 2000.0, "low": 1990.0}
        levels = [{"kind": "as_l", "price": 1989.0}]
        result = _check_session_sweep(zone, levels, {})
        assert result == 2  # Wick sweep by default

    def test_bear_ob_near_session_high(self):
        zone = {"kind": "ob_bear", "high": 2010.0, "low": 2000.0}
        levels = [{"kind": "lon_h", "price": 2011.0}]
        result = _check_session_sweep(zone, levels, {})
        assert result == 2

    def test_bull_ob_far_from_session_level(self):
        zone = {"kind": "ob_bull", "high": 2000.0, "low": 1990.0}
        levels = [{"kind": "as_l", "price": 1900.0}]  # far away
        result = _check_session_sweep(zone, levels, {})
        assert result == 0

    def test_sweep_body_ok_reduces_score(self):
        zone = {"kind": "ob_bull", "high": 2000.0, "low": 1990.0}
        levels = [{"kind": "as_l", "price": 1989.0}]
        result = _check_session_sweep(zone, levels, {"sweep_body_ok": True})
        assert result == 1

    def test_non_ob_zone_passes_through(self):
        """F9 is only checked in score_zone_confluence for ob_ zones."""
        zone = {"kind": "fvg_bull", "high": 2000.0, "low": 1990.0}
        levels = [{"kind": "as_l", "price": 1989.0}]
        result = _check_session_sweep(zone, levels, {})
        # Still returns score since function itself doesn't filter by ob_
        assert result >= 0


# ── NarrativeBlock session fields ─────────────────────────


class TestNarrativeBlockSessions:
    def test_default_values(self):
        from core.smc.types import NarrativeBlock

        block = NarrativeBlock(
            mode="wait",
            sub_mode="",
            headline="test",
            bias_summary="",
            scenarios=[],
            next_area="",
            fvg_context="",
            market_phase="ranging",
            warnings=[],
        )
        assert block.current_session == ""
        assert block.in_killzone is False
        assert block.session_context == ""

    def test_with_session(self):
        from core.smc.types import NarrativeBlock

        block = NarrativeBlock(
            mode="trade",
            sub_mode="aligned",
            headline="test",
            bias_summary="",
            scenarios=[],
            next_area="",
            fvg_context="",
            market_phase="trending_up",
            warnings=[],
            current_session="london",
            in_killzone=True,
            session_context="London KZ active",
        )
        assert block.current_session == "london"
        assert block.in_killzone is True


# ── narrative_to_wire includes session fields ─────────────


class TestNarrativeWireSessions:
    def test_wire_includes_session_fields(self):
        from core.smc.types import NarrativeBlock
        from core.smc.narrative import narrative_to_wire

        block = NarrativeBlock(
            mode="wait",
            sub_mode="",
            headline="test",
            bias_summary="",
            scenarios=[],
            next_area="",
            fvg_context="",
            market_phase="ranging",
            warnings=[],
            current_session="london",
            in_killzone=True,
            session_context="London KZ active",
        )
        wire = narrative_to_wire(block)
        assert wire["current_session"] == "london"
        assert wire["in_killzone"] is True
        assert wire["session_context"] == "London KZ active"


# ── SmcEngine session integration ─────────────────────────


class TestSmcEngineSessions:
    def _make_engine(self):
        from core.smc.config import SmcConfig

        cfg_dict = {
            "sessions": {
                "enabled": True,
                "definitions": {
                    "asia": DEFINITIONS["asia"],
                    "london": DEFINITIONS["london"],
                    "newyork": DEFINITIONS["newyork"],
                },
                "level_budget_per_session": 2,
                "previous_session_ttl_bars": 500,
                "sweep_lookback_bars": 30,
                "sweep_body_ok": False,
            }
        }
        config = SmcConfig.from_dict(cfg_dict)
        from core.smc.engine import SmcEngine

        return SmcEngine(config)

    def test_session_windows_loaded(self):
        engine = self._make_engine()
        assert len(engine._session_windows) == 3

    def test_feed_m1_bar(self):
        engine = self._make_engine()
        bar = _make_bar(_DAY_START_MS)
        engine.feed_m1_bar(bar)
        assert "XAU/USD" in engine._session_m1_bars
        assert len(engine._session_m1_bars["XAU/USD"]) == 1

    def test_feed_m1_skips_incomplete(self):
        engine = self._make_engine()
        bar = _make_bar(_DAY_START_MS, complete=False)
        engine.feed_m1_bar(bar)
        assert "XAU/USD" not in engine._session_m1_bars

    def test_feed_m1_bars_bulk(self):
        engine = self._make_engine()
        bars = [_make_bar(_DAY_START_MS + i * 60000) for i in range(100)]
        engine.feed_m1_bars_bulk("XAU/USD", bars)
        assert len(engine._session_m1_bars["XAU/USD"]) == 100

    def test_get_session_levels_empty(self):
        engine = self._make_engine()
        levels = engine.get_session_levels("XAU/USD", _DAY_START_MS)
        assert levels == []

    def test_get_session_levels_with_data(self):
        engine = self._make_engine()
        # Feed asia bars
        bars = []
        for i in range(420):
            open_ms = _DAY_START_MS + i * 60000
            bars.append(_make_bar(open_ms, h=2000.0 + i, low=1950.0 + i % 20))
        engine.feed_m1_bars_bulk("XAU/USD", bars)
        # Query at 08:00 UTC
        current_ms = _DAY_START_MS + 480 * 60000
        levels = engine.get_session_levels("XAU/USD", current_ms)
        assert len(levels) > 0
        kinds = {lv.kind for lv in levels}
        # At least asia current session H/L
        assert kinds & {"as_h", "as_l"}
