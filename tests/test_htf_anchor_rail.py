"""Тест: HTF anchor offset rail у server.py (Slice-3).

Перевіряє:
- Допустимі remainder-и для HTF (H4/D1) не генерують warning
- Недопустимий remainder генерує overlay_anchor_offset warning
- Лог HTF_ANCHOR_OBS rate-limited
"""
from __future__ import annotations

import unittest

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ui_chart_v3.server import (
    _HTF_ALLOWED_REMAINDERS_MS,
    _HTF_ANCHOR_MIN_TF_S,
    _htf_anchor_obs_log_allowed,
    _htf_anchor_obs_log_state,
)


class TestHTFAllowedRemainders(unittest.TestCase):
    """Перевіряє що відомі broker-конвенції remainder є у allowed set."""

    def test_h4_remainder_2h(self) -> None:
        """H4: remainder 7200000ms (2h offset) = broker convention."""
        self.assertIn(7200000, _HTF_ALLOWED_REMAINDERS_MS)

    def test_d1_remainder_22h(self) -> None:
        """D1: remainder 79200000ms (22:00 UTC) = FXCM convention."""
        self.assertIn(79200000, _HTF_ALLOWED_REMAINDERS_MS)

    def test_d1_remainder_21h_dst(self) -> None:
        """D1 DST: remainder 75600000ms (21:00 UTC) = DST variant."""
        self.assertIn(75600000, _HTF_ALLOWED_REMAINDERS_MS)

    def test_h1_remainder_zero(self) -> None:
        """H1: remainder 0 = aligned (також дозволено)."""
        self.assertIn(0, _HTF_ALLOWED_REMAINDERS_MS)

    def test_unknown_remainder_not_in_set(self) -> None:
        """Невідомий remainder (наприклад 12345) — не дозволений."""
        self.assertNotIn(12345, _HTF_ALLOWED_REMAINDERS_MS)

    def test_htf_min_tf_is_h4(self) -> None:
        """Мінімальний TF для HTF anchor rail = 14400 (H4)."""
        self.assertEqual(_HTF_ANCHOR_MIN_TF_S, 14400)


class TestHTFAnchorObsRateLimit(unittest.TestCase):
    """Перевіряє rate-limiting функцію для HTF_ANCHOR_OBS."""

    def setUp(self) -> None:
        _htf_anchor_obs_log_state.clear()

    def test_first_call_allowed(self) -> None:
        """Перший виклик для symbol/tf — дозволений."""
        result = _htf_anchor_obs_log_allowed("XAU/USD", 14400)
        self.assertTrue(result)

    def test_second_call_blocked(self) -> None:
        """Другий виклик в межах інтервалу — блокований."""
        _htf_anchor_obs_log_allowed("XAU/USD", 14400)
        result = _htf_anchor_obs_log_allowed("XAU/USD", 14400)
        self.assertFalse(result)

    def test_different_symbol_independent(self) -> None:
        """Різні символи мають незалежний rate-limit."""
        _htf_anchor_obs_log_allowed("XAU/USD", 14400)
        result = _htf_anchor_obs_log_allowed("GER30", 14400)
        self.assertTrue(result)

    def test_different_tf_independent(self) -> None:
        """Різні TF мають незалежний rate-limit."""
        _htf_anchor_obs_log_allowed("XAU/USD", 14400)
        result = _htf_anchor_obs_log_allowed("XAU/USD", 86400)
        self.assertTrue(result)


if __name__ == "__main__":
    unittest.main()
