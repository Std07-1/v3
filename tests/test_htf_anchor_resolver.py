"""Резолвер правила якоря H4/D1 за групою календаря (ADR-0095 §3.4, рішення власника 23.09.2026)."""
from __future__ import annotations

import json
import os

import pytest

from core.config_loader import htf_anchor_rule_resolver
from core.session_anchor import RULE_NY_CLOSE_US_DST, RULE_UTC_MIDNIGHT

_REPO_CONFIG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.json")


def _cfg(**rules):
    return {
        "htf_anchor": {"rule_by_calendar_group": rules or {
            "cfd_us_22_23": RULE_NY_CLOSE_US_DST,
            "cfd_eu_eustx50": RULE_NY_CLOSE_US_DST,
            "crypto_24x7": RULE_UTC_MIDNIGHT,
        }},
        "market_calendar_symbol_groups": {
            "XAU/USD": "cfd_us_22_23", "EUSTX50": "cfd_eu_eustx50", "BTCUSDT": "crypto_24x7",
            "HKG33": "cfd_hk_main", "USD/JPY": "fx_24x5_utc_summer",
        },
    }


def test_resolver_gives_each_measured_group_its_rule():
    rule_for = htf_anchor_rule_resolver(_cfg())
    assert rule_for("XAU/USD") == RULE_NY_CLOSE_US_DST
    assert rule_for("EUSTX50") == RULE_NY_CLOSE_US_DST
    assert rule_for("BTCUSDT") == RULE_UTC_MIDNIGHT


@pytest.mark.parametrize("symbol", ["HKG33", "USD/JPY"])
def test_resolver_unmeasured_group_raises_loudly(symbol):
    """Сітку брокера для групи не виміряно — не тихий default, а відмова з назвою групи."""
    rule_for = htf_anchor_rule_resolver(_cfg())
    with pytest.raises(ValueError, match="HTF_ANCHOR_GROUP_UNMEASURED"):
        rule_for(symbol)


def test_resolver_symbol_without_calendar_group_raises():
    with pytest.raises(ValueError, match="HTF_ANCHOR_SYMBOL_WITHOUT_GROUP"):
        htf_anchor_rule_resolver(_cfg())("GER30")


@pytest.mark.parametrize("cfg", [{}, {"htf_anchor": {}}, {"htf_anchor": {"rule_by_calendar_group": {}}}])
def test_resolver_missing_section_raises(cfg):
    with pytest.raises(ValueError, match="CONFIG_HTF_ANCHOR_MISSING"):
        htf_anchor_rule_resolver(cfg)


def test_resolver_unknown_rule_raises_at_build():
    with pytest.raises(ValueError, match="CONFIG_HTF_ANCHOR_RULE_UNKNOWN"):
        htf_anchor_rule_resolver(_cfg(cfd_us_22_23="legacy_79200"))


def test_repo_config_htf_anchor_covers_every_live_symbol():
    """config.json репо: кожен символ графіка і Binance має правило; невиміряні групи — лише поза ними."""
    with open(_REPO_CONFIG, encoding="utf-8") as fh:
        cfg = json.load(fh)
    rule_for = htf_anchor_rule_resolver(cfg)
    live = list(cfg["symbols"]) + list(cfg.get("binance", {}).get("symbols", [])) + ["EUSTX50", "GER30"]
    assert {s: rule_for(s) for s in live}["BTCUSDT"] == RULE_UTC_MIDNIGHT
    assert all(rule_for(s) == RULE_NY_CLOSE_US_DST for s in cfg["symbols"])
    with pytest.raises(ValueError, match="HTF_ANCHOR_GROUP_UNMEASURED"):
        rule_for("HKG33")
