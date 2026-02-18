"""tests/test_m1_flat_bar_policy.py

Тести для flat_bar_max_volume policy з runtime/ingest/polling/m1_poller.py.
"""
from __future__ import annotations

import pytest

from core.model.bars import CandleBar
from runtime.ingest.polling.m1_poller import (
    _is_flat,
    set_flat_bar_max_volume,
    _flat_bar_max_volume,
    _FLAT_BAR_MAX_VOLUME_DEFAULT,
)


def _make_flat_bar(volume: float = 1.0) -> CandleBar:
    """Flat bar: O==H==L==C."""
    return CandleBar(
        symbol="TEST",
        tf_s=60,
        open_time_ms=1000000,
        close_time_ms=1060000,
        o=100.0,
        h=100.0,
        low=100.0,
        c=100.0,
        v=volume,
        complete=True,
        src="history",
    )


def _make_normal_bar(volume: float = 50.0) -> CandleBar:
    """Non-flat bar: O!=H."""
    return CandleBar(
        symbol="TEST",
        tf_s=60,
        open_time_ms=1000000,
        close_time_ms=1060000,
        o=100.0,
        h=101.0,
        low=99.5,
        c=100.5,
        v=volume,
        complete=True,
        src="history",
    )


class TestFlatBarPolicy:
    """_is_flat: flat bar detection з configurable max_volume."""

    def test_default_value(self):
        """Дефолт = 4 (як у SSOT config.json)."""
        assert _FLAT_BAR_MAX_VOLUME_DEFAULT == 4

    def test_flat_with_low_volume(self):
        """Flat bar з v=1 → flat."""
        set_flat_bar_max_volume(4)
        bar = _make_flat_bar(volume=1.0)
        assert _is_flat(bar) is True

    def test_flat_with_max_volume(self):
        """Flat bar з v==max → flat."""
        set_flat_bar_max_volume(4)
        bar = _make_flat_bar(volume=4.0)
        assert _is_flat(bar) is True

    def test_flat_above_max_volume(self):
        """Flat bar з v > max → NOT flat."""
        set_flat_bar_max_volume(4)
        bar = _make_flat_bar(volume=5.0)
        assert _is_flat(bar) is False

    def test_normal_bar_not_flat(self):
        """Non-flat bar (O!=H) → NOT flat навіть з v=1."""
        set_flat_bar_max_volume(4)
        bar = _make_normal_bar(volume=1.0)
        assert _is_flat(bar) is False

    def test_set_from_config(self):
        """set_flat_bar_max_volume змінює поведінку."""
        set_flat_bar_max_volume(2)
        bar = _make_flat_bar(volume=3.0)
        assert _is_flat(bar) is False  # v=3 > max=2

        set_flat_bar_max_volume(5)
        assert _is_flat(bar) is True   # v=3 <= max=5

        # Restore default
        set_flat_bar_max_volume(_FLAT_BAR_MAX_VOLUME_DEFAULT)
