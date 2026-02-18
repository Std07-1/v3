"""tests/test_symbol_canon.py

Тести для _canonicalize_symbol з ui_chart_v3/server.py.
"""
from __future__ import annotations

import pytest


# Імпортуємо функцію з server.py
from ui_chart_v3.server import _canonicalize_symbol


_SAMPLE_CFG = {
    "symbols": [
        "XAU/USD",
        "XAG/USD",
        "USD/JPY",
        "GBP/CAD",
        "NZD/CAD",
        "USD/CAD",
        "SPX500",
        "NAS100",
        "GER30",
        "NGAS",
    ],
}


class TestCanonicalizeSymbol:
    """_canonicalize_symbol: нормалізація symbol з API query."""

    def test_slash_symbol_passthrough(self):
        """Символ з '/' що є у config → passthrough, input=None."""
        canon, inp = _canonicalize_symbol("XAU/USD", _SAMPLE_CFG)
        assert canon == "XAU/USD"
        assert inp is None

    def test_underscore_to_slash(self):
        """Символ з '_' де canon з '/' є у config → замінити."""
        canon, inp = _canonicalize_symbol("USD_JPY", _SAMPLE_CFG)
        assert canon == "USD/JPY"
        assert inp == "USD_JPY"

    def test_no_slash_symbol_passthrough(self):
        """Символ без '/' (SPX500) що вже є у config → passthrough."""
        canon, inp = _canonicalize_symbol("SPX500", _SAMPLE_CFG)
        assert canon == "SPX500"
        assert inp is None

    def test_unknown_symbol_passthrough(self):
        """Символ якого немає в config → passthrough (не ламаємо)."""
        canon, inp = _canonicalize_symbol("UNKNOWN_SYM", _SAMPLE_CFG)
        assert canon == "UNKNOWN_SYM"
        assert inp is None

    def test_empty_config(self):
        """Порожній config.symbols → passthrough."""
        canon, inp = _canonicalize_symbol("XAU_USD", {})
        assert canon == "XAU_USD"
        assert inp is None

    def test_all_slash_symbols(self):
        """Перевірка всіх символів з '/' — всі мають canon."""
        for raw in ["XAU_USD", "XAG_USD", "USD_JPY", "GBP_CAD", "NZD_CAD", "USD_CAD"]:
            canon, inp = _canonicalize_symbol(raw, _SAMPLE_CFG)
            assert "/" in canon, "Canon for %s should have '/'" % raw
            assert inp == raw
