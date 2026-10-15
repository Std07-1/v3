"""ADR-0114 S1: лінія-сповіщення — типи і перетин рівня (core/alerts, pure).

Run: python -m pytest tests/test_alerts_crossing.py -q
"""

from __future__ import annotations

import dataclasses
import math

import pytest

from core.alerts.crossing import arm, on_m1_bar, on_tick, side_of
from core.alerts.types import (
    AlertFire,
    AlertSide,
    AlertState,
    CrossDirection,
    FireVia,
    PriceAlert,
)

LEVEL = 4380.0
T0 = 1_792_000_000_000  # момент взведення
GAP_MS = 15 * 60_000


def _alert(side: AlertSide | None = AlertSide.BELOW, active_from_ms: int = T0, **kw) -> PriceAlert:
    fields = dict(
        alert_id="a1",
        drawing_id="d1",
        subscriber_id="owner",
        symbol="XAU/USD",
        level=LEVEL,
        tf_label="M15",
        active_from_ms=active_from_ms,
        created_at_ms=T0,
        side=side,
    )
    fields.update(kw)
    return PriceAlert(**fields)


def _tick(alert: PriceAlert, price: float, ts_ms: int = T0 + 1_000, prev: int | None = T0) -> PriceAlert:
    return on_tick(alert, price, ts_ms, prev_tick_ts_ms=prev, gap_after_ms=GAP_MS)


# ── side_of / типи ───────────────────────────────────────────────────────


def test_side_of_price_relative_to_level_below_above_or_none_on_level():
    assert side_of(LEVEL, LEVEL - 0.01) is AlertSide.BELOW
    assert side_of(LEVEL, LEVEL + 0.01) is AlertSide.ABOVE
    assert side_of(LEVEL, LEVEL) is None


@pytest.mark.parametrize("level", [math.nan, math.inf, 0.0, -1.0, True])
def test_price_alert_with_invalid_level_rejected(level):
    with pytest.raises(ValueError, match="level"):
        _alert(level=level)


@pytest.mark.parametrize("field", ["alert_id", "drawing_id", "subscriber_id", "symbol", "tf_label"])
def test_price_alert_with_empty_identity_field_rejected(field):
    with pytest.raises(ValueError, match=field):
        _alert(**{field: ""})


def test_price_alert_state_inconsistent_with_fired_rejected():
    fire = AlertFire(T0, LEVEL, FireVia.TICK, CrossDirection.UP)
    with pytest.raises(ValueError, match="state"):
        _alert(state=AlertState.FIRED)
    with pytest.raises(ValueError, match="state"):
        _alert(fired=fire)
    with pytest.raises(ValueError, match="version"):
        _alert(version=0)


# ── тіки ─────────────────────────────────────────────────────────────────


def test_tick_touching_level_from_below_fires_up_once():
    fired = _tick(_alert(), LEVEL)
    assert fired.state is AlertState.FIRED
    assert fired.fired == AlertFire(T0 + 1_000, LEVEL, FireVia.TICK, CrossDirection.UP, after_gap=False)
    # один раз: ціна пішла геть і перетнула знову — факт той самий
    assert _tick(fired, LEVEL + 10, ts_ms=T0 + 2_000) is fired
    assert _tick(fired, LEVEL - 10, ts_ms=T0 + 3_000) is fired


def test_tick_touching_level_from_above_fires_down():
    fired = _tick(_alert(side=AlertSide.ABOVE), LEVEL)
    assert fired.fired is not None and fired.fired.direction is CrossDirection.DOWN


def test_tick_not_reaching_level_returns_same_alert():
    alert = _alert()
    assert _tick(alert, LEVEL - 0.01) is alert


def test_undetermined_side_waits_until_price_leaves_level_then_fires_on_return():
    alert = _alert(side=None)
    assert _tick(alert, LEVEL) is alert  # усе ще на рівні — не спрацювання
    below = _tick(alert, LEVEL - 1)
    assert below.side is AlertSide.BELOW and below.state is AlertState.ARMED
    assert _tick(below, LEVEL + 1, ts_ms=T0 + 2_000).fired.direction is CrossDirection.UP


def test_ticks_before_activation_track_side_without_firing():
    alert = _alert(active_from_ms=T0 + 60_000)  # промінь з якорем у майбутньому
    above = _tick(alert, LEVEL + 5)  # перетнула до активації
    assert above.state is AlertState.ARMED and above.side is AlertSide.ABOVE
    fired = _tick(above, LEVEL - 1, ts_ms=T0 + 61_000)
    assert fired.fired.direction is CrossDirection.DOWN and fired.fired.at_ms == T0 + 61_000


def test_tick_after_market_pause_marks_fire_after_gap():
    assert _tick(_alert(), LEVEL + 3, ts_ms=T0 + GAP_MS, prev=T0).fired.after_gap is True
    assert _tick(_alert(), LEVEL + 3, ts_ms=T0 + GAP_MS - 1, prev=T0).fired.after_gap is False
    assert _tick(_alert(), LEVEL + 3, prev=None).fired.after_gap is False  # перший тік після старту процесу


def test_tick_with_invalid_price_or_gap_threshold_rejected():
    with pytest.raises(ValueError, match="price"):
        _tick(_alert(), math.nan)
    with pytest.raises(ValueError, match="gap_after_ms"):
        on_tick(_alert(), LEVEL, T0, prev_tick_ts_ms=None, gap_after_ms=0)


# ── свічки M1 (наздоганяння) ─────────────────────────────────────────────


def test_m1_wick_reaching_level_without_tick_fires_with_extreme_and_bar_time():
    fired = on_m1_bar(_alert(), open_ms=T0 + 60_000, low=LEVEL - 4, high=LEVEL + 0.3)
    assert fired.fired == AlertFire(T0 + 60_000, LEVEL + 0.3, FireVia.M1, CrossDirection.UP)
    down = on_m1_bar(_alert(side=AlertSide.ABOVE), open_ms=T0 + 60_000, low=LEVEL, high=LEVEL + 9)
    assert down.fired.price == LEVEL and down.fired.direction is CrossDirection.DOWN


def test_m1_bar_entirely_beyond_level_after_gap_still_fires():
    fired = on_m1_bar(_alert(), open_ms=T0 + 3_600_000, low=LEVEL + 5, high=LEVEL + 9)
    assert fired.fired is not None and fired.fired.direction is CrossDirection.UP


def test_m1_bar_ignored_before_activation_with_undetermined_side_or_when_fired():
    early = _alert()
    assert on_m1_bar(early, open_ms=T0 - 60_000, low=LEVEL - 1, high=LEVEL + 1) is early
    undetermined = _alert(side=None)
    assert on_m1_bar(undetermined, open_ms=T0 + 60_000, low=LEVEL - 1, high=LEVEL + 1) is undetermined
    fired = _tick(_alert(), LEVEL)
    assert on_m1_bar(fired, open_ms=T0 + 60_000, low=LEVEL - 1, high=LEVEL + 1) is fired


def test_m1_bar_short_of_level_returns_same_alert_and_inverted_bar_rejected():
    alert = _alert()
    assert on_m1_bar(alert, open_ms=T0 + 60_000, low=LEVEL - 9, high=LEVEL - 0.01) is alert
    with pytest.raises(ValueError, match="low"):
        on_m1_bar(alert, open_ms=T0 + 60_000, low=LEVEL + 1, high=LEVEL - 1)


# ── повторне взведення ───────────────────────────────────────────────────


def test_arm_after_fire_moves_level_bumps_version_and_recomputes_side():
    fired = _tick(_alert(), LEVEL)
    rearmed = arm(fired, level=4400.0, reference_price=4410.0, active_from_ms=T0 + 5_000)
    assert rearmed == dataclasses.replace(
        fired,
        level=4400.0,
        active_from_ms=T0 + 5_000,
        version=2,
        state=AlertState.ARMED,
        side=AlertSide.ABOVE,
        fired=None,
    )
    assert arm(rearmed, level=4410.0, reference_price=4410.0, active_from_ms=T0).side is None
