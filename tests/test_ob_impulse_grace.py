"""
tests/test_ob_impulse_grace.py — Regression: impulse grace for OB mitigation.

Bug: bars during the impulse (between OB candle and BOS/CHoCH event) could
briefly close below the zone body → premature mitigation.

Fix: _update_ob_status() now skips bars until after impulse_end_ms.
"""

from __future__ import annotations

from typing import List

import pytest

from core.model.bars import CandleBar
from core.smc.order_blocks import _update_ob_status
from core.smc.types import SmcZone

SYM = "XAU/USD"
TF = 900  # M15
T0 = 1_700_000_000_000
BAR_MS = TF * 1000


def _bar(
    i: int,
    o: float,
    h: float,
    low: float,
    c: float,
    complete: bool = True,
) -> CandleBar:
    open_ms = T0 + i * BAR_MS
    return CandleBar(
        symbol=SYM,
        tf_s=TF,
        open_time_ms=open_ms,
        close_time_ms=open_ms + BAR_MS,
        o=o,
        h=h,
        low=low,
        c=c,
        v=1000.0,
        complete=complete,
        src="derived",
    )


def _make_bull_ob(anchor_i: int) -> SmcZone:
    """Bull OB zone з body 4420..4424 at bar index anchor_i."""
    anchor_ms = T0 + anchor_i * BAR_MS
    return SmcZone(
        id=f"ob_bull_{SYM.replace('/', '_')}_{TF}_{anchor_ms}",
        symbol=SYM,
        tf_s=TF,
        kind="ob_bull",
        start_ms=anchor_ms,
        end_ms=None,
        high=4424.0,
        low=4420.0,
        status="active",
        strength=0.8,
        anchor_bar_ms=anchor_ms,
    )


def _make_bear_ob(anchor_i: int) -> SmcZone:
    """Bear OB zone з body 4480..4484 at bar index anchor_i."""
    anchor_ms = T0 + anchor_i * BAR_MS
    return SmcZone(
        id=f"ob_bear_{SYM.replace('/', '_')}_{TF}_{anchor_ms}",
        symbol=SYM,
        tf_s=TF,
        kind="ob_bear",
        start_ms=anchor_ms,
        end_ms=None,
        high=4484.0,
        low=4480.0,
        status="active",
        strength=0.8,
        anchor_bar_ms=anchor_ms,
    )


# ──────────────────────────────────────────────────────────────
#  Regression: impulse bars must NOT mitigate
# ──────────────────────────────────────────────────────────────


class TestImpulseGrace:
    """Бари імпульсу (між OB та BOS/CHoCH) не повинні мітигувати зону."""

    def test_bull_ob_impulse_bar_below_body_no_mitigation(self) -> None:
        """Bull OB(i=5), BOS at i=8. Bar i=6 closes below body → NOT mitigated."""
        zone = _make_bull_ob(5)

        bars = [
            _bar(5, 4426.0, 4428.0, 4418.0, 4420.0),  # OB candle (bearish body)
            _bar(
                6, 4419.0, 4425.0, 4415.0, 4418.0
            ),  # impulse bar — close 4418 < 4420 = zone.low
            _bar(7, 4422.0, 4440.0, 4420.0, 4438.0),  # impulse continues up
            _bar(8, 4438.0, 4460.0, 4436.0, 4455.0),  # BOS event bar
            _bar(9, 4455.0, 4470.0, 4450.0, 4465.0),  # post-impulse — far above zone
            _bar(10, 4465.0, 4480.0, 4460.0, 4496.0),  # price went to 4496
        ]

        impulse_ends = {zone.id: bars[3].open_time_ms}  # BOS bar i=8

        result = _update_ob_status([zone], bars, impulse_ends)
        assert (
            result[0].status == "active"
        ), f"Zone should be active (impulse grace), got: {result[0].status}"

    def test_bull_ob_no_grace_old_behavior_would_mitigate(self) -> None:
        """Without impulse_ends, same bars would mitigate (proves bug existed)."""
        zone = _make_bull_ob(5)

        bars = [
            _bar(5, 4426.0, 4428.0, 4418.0, 4420.0),
            _bar(
                6, 4419.0, 4425.0, 4415.0, 4418.0
            ),  # close < zone.low → mitigated without grace
            _bar(7, 4422.0, 4440.0, 4420.0, 4438.0),
            _bar(8, 4438.0, 4460.0, 4436.0, 4455.0),
        ]

        # No impulse_ends → falls back to anchor_bar_ms (old behavior)
        result = _update_ob_status([zone], bars, impulse_ends=None)
        assert (
            result[0].status == "mitigated"
        ), "Without impulse grace, bar i=6 should mitigate (proves old bug)"

    def test_bull_ob_real_mitigation_after_impulse(self) -> None:
        """Post-impulse bar that closes below zone → legitimate mitigation."""
        zone = _make_bull_ob(5)

        bars = [
            _bar(5, 4426.0, 4428.0, 4418.0, 4420.0),  # OB candle
            _bar(6, 4419.0, 4425.0, 4415.0, 4418.0),  # impulse — close below body
            _bar(7, 4422.0, 4440.0, 4420.0, 4438.0),  # impulse up
            _bar(8, 4438.0, 4460.0, 4436.0, 4455.0),  # BOS bar
            _bar(9, 4455.0, 4460.0, 4425.0, 4430.0),  # post-impulse — bounce near zone
            _bar(
                10, 4430.0, 4432.0, 4410.0, 4415.0
            ),  # returns and closes below → mitigated
        ]

        impulse_ends = {zone.id: bars[3].open_time_ms}
        result = _update_ob_status([zone], bars, impulse_ends)
        assert (
            result[0].status == "mitigated"
        ), "Post-impulse bar below zone.low should mitigate"

    def test_bear_ob_impulse_bar_above_body_no_mitigation(self) -> None:
        """Bear OB(i=5), BOS at i=8. Impulse bar i=6 closes above body → NOT mitigated."""
        zone = _make_bear_ob(5)

        bars = [
            _bar(5, 4478.0, 4486.0, 4476.0, 4484.0),  # OB candle (bullish body)
            _bar(
                6, 4485.0, 4490.0, 4481.0, 4486.0
            ),  # impulse bar — close 4486 > 4484 = zone.high
            _bar(7, 4483.0, 4484.0, 4465.0, 4468.0),  # impulse down
            _bar(8, 4468.0, 4470.0, 4440.0, 4445.0),  # BOS event bar
            _bar(9, 4445.0, 4450.0, 4430.0, 4435.0),  # post-impulse — far below zone
        ]

        impulse_ends = {zone.id: bars[3].open_time_ms}
        result = _update_ob_status([zone], bars, impulse_ends)
        assert (
            result[0].status == "active"
        ), f"Bear OB should be active (impulse grace), got: {result[0].status}"

    def test_tested_status_preserved_during_impulse_grace(self) -> None:
        """If zone enters 'tested' during impulse bars, it stays tested, not mitigated."""
        zone = _make_bull_ob(5)

        bars = [
            _bar(5, 4426.0, 4428.0, 4418.0, 4420.0),  # OB candle
            _bar(
                6, 4421.0, 4425.0, 4419.0, 4422.0
            ),  # impulse bar — enters zone (low=4419 < high=4424)
            _bar(
                7, 4418.0, 4424.0, 4415.0, 4416.0
            ),  # impulse — close < zone.low (4416 < 4420)
            _bar(8, 4420.0, 4460.0, 4418.0, 4455.0),  # BOS event bar
            _bar(9, 4455.0, 4470.0, 4450.0, 4465.0),  # post-impulse
        ]

        impulse_ends = {zone.id: bars[3].open_time_ms}
        result = _update_ob_status([zone], bars, impulse_ends)
        # Bar i=6 enters zone → tested, bar i=7 closes below → but still in impulse → NOT mitigated
        # Post-impulse bars are above zone → stays active or tested
        assert result[0].status in (
            "active",
            "tested",
        ), f"Zone should not be mitigated during impulse, got: {result[0].status}"

    def test_incomplete_bars_skipped(self) -> None:
        """Incomplete (preview) bars should not affect mitigation status."""
        zone = _make_bull_ob(5)

        bars = [
            _bar(5, 4426.0, 4428.0, 4418.0, 4420.0),
            _bar(8, 4438.0, 4460.0, 4436.0, 4455.0),  # BOS bar (complete)
            _bar(
                9, 4440.0, 4442.0, 4410.0, 4415.0, complete=False
            ),  # preview closes below
        ]

        impulse_ends = {zone.id: bars[1].open_time_ms}
        result = _update_ob_status([zone], bars, impulse_ends)
        assert result[0].status == "active", "Preview bars should not mitigate"
