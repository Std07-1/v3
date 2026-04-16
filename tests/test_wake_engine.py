"""
tests/test_wake_engine.py вЂ” Unit tests for ADR-0049 Wake Engine components.

Tests pure logic only (core/smc/wake_check.py, core/smc/auto_wake.py, wake_types).
Zero I/O, zero Redis, zero mocks needed.

Run: python -m pytest tests/test_wake_engine.py -v
"""
from __future__ import annotations

import time
from collections import deque

import pytest

from core.smc.wake_types import (
    AwarenessAccumulator,
    PresenceStatus,
    ThesisLayer,
    WakeCondition,
    WakeConditionKind,
    WakeEvent,
)
from core.smc.wake_check import check_condition, accumulator_tick
from core.smc.auto_wake import generate_platform_conditions


# в”Ђв”Ђ Fixtures в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ

class FakeZone:
    """Minimal zone stub matching SmcZone interface."""
    def __init__(self, id: str, high: float, low: float, kind: str = "ob",
                 status: str = "active"):
        self.id = id
        self.high = high
        self.low = low
        self.kind = kind
        self.status = status


class FakeSnapshot:
    """Minimal snapshot stub matching SmcSnapshot interface."""
    def __init__(self, zones=None):
        self.zones = zones or []
        self.swings = []
        self.levels = []
        self.trend_bias = ""


# в”Ђв”Ђ check_condition tests в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ

class TestCheckCondition:
    """Test each WakeConditionKind."""

    def test_price_cross_above(self):
        cond = WakeCondition(
            kind=WakeConditionKind.PRICE_CROSS,
            params={"level": 4680.0, "direction": "above"},
            reason="test",
        )
        assert check_condition(cond, 4681.0, 45.0, {}, 1000) is True
        assert check_condition(cond, 4679.0, 45.0, {}, 1000) is False

    def test_price_cross_below(self):
        cond = WakeCondition(
            kind=WakeConditionKind.PRICE_CROSS,
            params={"level": 4680.0, "direction": "below"},
            reason="test",
        )
        assert check_condition(cond, 4679.0, 45.0, {}, 1000) is True
        assert check_condition(cond, 4681.0, 45.0, {}, 1000) is False

    def test_zone_touch_inside(self):
        cond = WakeCondition(
            kind=WakeConditionKind.PRICE_ZONE_TOUCH,
            params={"zone_high": 4700.0, "zone_low": 4680.0, "tolerance_atr": 0.5},
            reason="test",
        )
        # Price inside zone
        assert check_condition(cond, 4690.0, 45.0, {}, 1000) is True
        # Price within tolerance (0.5 * 45 = 22.5)
        assert check_condition(cond, 4660.0, 45.0, {}, 1000) is True
        # Price far outside
        assert check_condition(cond, 4600.0, 45.0, {}, 1000) is False

    def test_session_open(self):
        cond = WakeCondition(
            kind=WakeConditionKind.SESSION_OPEN,
            params={"session": "london"},
            reason="test",
        )
        assert check_condition(
            cond, 4680.0, 45.0,
            {"current_session": "london", "is_open": True}, 1000,
        ) is True
        assert check_condition(
            cond, 4680.0, 45.0,
            {"current_session": "london", "is_open": False}, 1000,
        ) is False
        assert check_condition(
            cond, 4680.0, 45.0,
            {"current_session": "asia", "is_open": True}, 1000,
        ) is False

    def test_volatility_spike(self):
        cond = WakeCondition(
            kind=WakeConditionKind.VOLATILITY_SPIKE,
            params={"atr_mult": 2.0, "last_bar_range": 100.0},
            reason="test",
        )
        # range=100 > 2.0 * 45 = 90 в†’ True
        assert check_condition(cond, 4680.0, 45.0, {}, 1000) is True
        # ATR = 60, range=100 < 2.0 * 60 = 120 в†’ False
        assert check_condition(cond, 4680.0, 60.0, {}, 1000) is False

    def test_max_silence_never_woke(self):
        cond = WakeCondition(
            kind=WakeConditionKind.MAX_SILENCE,
            params={"hours": 3},
            reason="test",
        )
        # Never woke в†’ True immediately
        assert check_condition(cond, 4680.0, 45.0, {}, 1000, last_wake_ts_ms=0) is True

    def test_max_silence_elapsed(self):
        cond = WakeCondition(
            kind=WakeConditionKind.MAX_SILENCE,
            params={"hours": 3},
            reason="test",
        )
        now_ms = int(time.time() * 1000)
        # Last wake 4h ago в†’ True
        last_wake = now_ms - (4 * 3_600_000)
        assert check_condition(cond, 4680.0, 45.0, {}, now_ms, last_wake_ts_ms=last_wake) is True
        # Last wake 1h ago в†’ False
        last_wake = now_ms - (1 * 3_600_000)
        assert check_condition(cond, 4680.0, 45.0, {}, now_ms, last_wake_ts_ms=last_wake) is False

    def test_structure_break(self):
        cond = WakeCondition(
            kind=WakeConditionKind.STRUCTURE_BREAK,
            params={"type": "bos"},
            reason="test",
        )
        assert check_condition(
            cond, 4680.0, 45.0, {}, 1000,
            structure_events=[{"type": "bos", "tf_s": 900}],
        ) is True
        assert check_condition(
            cond, 4680.0, 45.0, {}, 1000,
            structure_events=[{"type": "choch", "tf_s": 900}],
        ) is False
        assert check_condition(
            cond, 4680.0, 45.0, {}, 1000,
            structure_events=None,
        ) is False

    def test_scheduled(self):
        cond = WakeCondition(
            kind=WakeConditionKind.SCHEDULED,
            params={"hour_utc": 8, "minute_utc": 0},
            reason="test",
        )
        # 2024-01-15 08:00 UTC
        ts_ms = 1705305600000
        assert check_condition(cond, 4680.0, 45.0, {}, ts_ms) is True
        # 2024-01-15 09:00 UTC
        ts_ms_9 = ts_ms + 3_600_000
        assert check_condition(cond, 4680.0, 45.0, {}, ts_ms_9) is False


# в”Ђв”Ђ accumulator_tick tests в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ

class TestAccumulatorTick:
    """Test awareness accumulator math."""

    def test_basic_score_increase(self):
        acc = AwarenessAccumulator(score=0.0, threshold=5.0)
        # Price moved 10 points, ATR=45 в†’ normalized = 10/45 в‰€ 0.222
        new_acc = accumulator_tick(acc, 4690.0, 4680.0, 45.0, ts=1000.0)
        assert new_acc.score > 0
        assert abs(new_acc.score - 10 / 45) < 0.01

    def test_decay_over_time(self):
        acc = AwarenessAccumulator(score=3.0, threshold=5.0, decay=0.95, last_tick_ts=100.0)
        # 1 minute later в†’ score *= 0.95^1
        new_acc = accumulator_tick(acc, 4680.0, 4680.0, 45.0, ts=160.0)
        expected = 3.0 * 0.95  # no price delta (same price)
        assert abs(new_acc.score - expected) < 0.01

    def test_session_bonus(self):
        acc = AwarenessAccumulator(score=0.0, threshold=5.0)
        new_acc = accumulator_tick(
            acc, 4680.0, 4680.0, 45.0,
            session_events=["london_open"], ts=1000.0,
        )
        assert new_acc.score >= 1.0

    def test_gap_bonus(self):
        acc = AwarenessAccumulator(score=0.0, threshold=5.0)
        new_acc = accumulator_tick(
            acc, 4680.0, 4680.0, 45.0,
            gap_detected=True, ts=1000.0,
        )
        assert new_acc.score >= 2.0

    def test_threshold_reached(self):
        acc = AwarenessAccumulator(score=4.5, threshold=5.0)
        # Big move: 50 points / ATR 45 в‰€ 1.11 в†’ 4.5 + 1.11 = 5.61 >= 5.0
        new_acc = accumulator_tick(acc, 4730.0, 4680.0, 45.0, ts=1000.0)
        assert new_acc.score >= new_acc.threshold

    def test_immutability(self):
        """accumulator_tick returns NEW instance, does not mutate."""
        acc = AwarenessAccumulator(score=1.0, threshold=5.0)
        new_acc = accumulator_tick(acc, 4690.0, 4680.0, 45.0, ts=1000.0)
        assert acc.score == 1.0  # original unchanged
        assert new_acc.score != acc.score


# в”Ђв”Ђ generate_platform_conditions tests в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ

class TestAutoWake:
    """Test platform condition generator."""

    def test_empty_when_no_atr(self):
        result = generate_platform_conditions(
            snapshots={}, bias_map={}, atr=0, current_price=4680.0,
            session_info={}, ts_ms=1000,
        )
        assert result == []

    def test_always_includes_max_silence(self):
        result = generate_platform_conditions(
            snapshots={}, bias_map={}, atr=45.0, current_price=4680.0,
            session_info={}, ts_ms=1000,
        )
        kinds = [c.kind for c in result]
        assert WakeConditionKind.MAX_SILENCE in kinds

    def test_zone_proximity(self):
        """Zone within 2 ATR should generate price_zone_touch."""
        zone = FakeZone(id="z1", high=4700.0, low=4680.0)
        snap = FakeSnapshot(zones=[zone])
        result = generate_platform_conditions(
            snapshots={14400: snap},
            bias_map={},
            atr=45.0,
            current_price=4650.0,  # 30 points below zone_low = 30/45 в‰€ 0.67 ATR
            session_info={},
            ts_ms=1000,
            zone_grades={"z1": {"grade": "A+", "score": 8}},
        )
        zone_conds = [c for c in result if c.kind == WakeConditionKind.PRICE_ZONE_TOUCH]
        assert len(zone_conds) == 1
        assert zone_conds[0].params["zone_high"] == 4700.0

    def test_zone_too_far(self):
        """Zone beyond 2 ATR should NOT generate condition."""
        zone = FakeZone(id="z2", high=4900.0, low=4880.0)
        snap = FakeSnapshot(zones=[zone])
        result = generate_platform_conditions(
            snapshots={14400: snap},
            bias_map={},
            atr=45.0,
            current_price=4680.0,  # 200 points away = 4.4 ATR > 2.0
            session_info={},
            ts_ms=1000,
            zone_grades={"z2": {"grade": "A+", "score": 8}},
        )
        zone_conds = [c for c in result if c.kind == WakeConditionKind.PRICE_ZONE_TOUCH]
        assert len(zone_conds) == 0

    def test_bias_divergence(self):
        """D1 bearish + H4 bullish в†’ structure_break."""
        result = generate_platform_conditions(
            snapshots={},
            bias_map={86400: "bearish", 14400: "bullish"},
            atr=45.0,
            current_price=4680.0,
            session_info={},
            ts_ms=1000,
        )
        div_conds = [c for c in result if c.kind == WakeConditionKind.STRUCTURE_BREAK]
        assert len(div_conds) == 1
        assert "divergence" in div_conds[0].reason.lower()

    def test_session_open_within_5min(self):
        result = generate_platform_conditions(
            snapshots={},
            bias_map={},
            atr=45.0,
            current_price=4680.0,
            session_info={
                "next_session": "london",
                "next_open_min": 3,
                "is_open": False,
            },
            ts_ms=1000,
        )
        sess_conds = [c for c in result if c.kind == WakeConditionKind.SESSION_OPEN]
        assert len(sess_conds) == 1

    def test_max_conditions_cap(self):
        """Should never return more than MAX_PLATFORM_CONDITIONS (10)."""
        # Create many zones
        zones = [
            FakeZone(id=f"z{i}", high=4680.0 + i * 5, low=4670.0 + i * 5)
            for i in range(20)
        ]
        snap = FakeSnapshot(zones=zones)
        zone_grades = {
            f"z{i}": {"grade": "A+", "score": 8} for i in range(20)
        }
        result = generate_platform_conditions(
            snapshots={14400: snap},
            bias_map={86400: "bearish", 14400: "bullish"},
            atr=45.0,
            current_price=4685.0,
            session_info={"next_session": "london", "next_open_min": 2},
            ts_ms=1000,
            zone_grades=zone_grades,
        )
        assert len(result) <= 10


# в”Ђв”Ђ Type correctness tests в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ

class TestTypes:
    """Test dataclass construction and frozen invariants."""

    def test_wake_condition_frozen(self):
        cond = WakeCondition(
            kind=WakeConditionKind.PRICE_CROSS,
            params={"level": 4680.0},
            reason="test",
        )
        with pytest.raises(AttributeError):
            cond.reason = "changed"

    def test_presence_defaults(self):
        p = PresenceStatus()
        assert p.status == "sleeping"
        assert p.active_conditions == 0

    def test_thesis_layer_frozen(self):
        t = ThesisLayer(
            thesis="test", conviction="high",
            key_level="4680", invalidation="4730",
        )
        assert t.freshness == "stale"
        with pytest.raises(AttributeError):
            t.thesis = "changed"

    def test_wake_event(self):
        ev = WakeEvent(
            ts_ms=1000, symbol="XAU/USD",
            kind="price_cross", reason="test",
            price=4680.0, meta={"atr": 45.0},
        )
        assert ev.symbol == "XAU/USD"
