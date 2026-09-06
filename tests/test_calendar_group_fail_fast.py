"""ADR-0054 P0.4 — символ без валідного календаря не стартує тихо у режимі 24/7."""
from __future__ import annotations

import logging

import pytest

from runtime.ingest.tick_common import resolve_symbol_calendars

GROUP = {
    "market_weekend_close_dow": 4,
    "market_weekend_close_hm": "21:00",
    "market_weekend_open_dow": 6,
    "market_weekend_open_hm": "21:05",
}
CFG = {
    "market_calendar_by_group": {"cfd_us_22_23": GROUP},
    "market_calendar_symbol_groups": {"XAU/USD": "cfd_us_22_23"},
}


def test_mapped_symbol_gets_calendar():
    calendars, rejected = resolve_symbol_calendars(CFG, ["XAU/USD"], where="t")
    assert rejected == [] and calendars["XAU/USD"].enabled is True


def test_symbol_without_group_mapping_is_rejected_loudly(caplog):
    """Друкарська помилка в символі раніше давала calendar=None = полінг 24/7."""
    with caplog.at_level(logging.ERROR):
        calendars, rejected = resolve_symbol_calendars(CFG, ["NAS100"], where="t")
    assert rejected == ["NAS100"] and "NAS100" not in calendars
    assert "CALENDAR_GROUP_MISSING" in caplog.text
    assert "no_group_mapping" in caplog.text


def test_group_absent_from_config_is_rejected(caplog):
    cfg = {**CFG, "market_calendar_symbol_groups": {"GER30": "cfd_eu_21_07"}}
    with caplog.at_level(logging.ERROR):
        calendars, rejected = resolve_symbol_calendars(cfg, ["GER30"], where="t")
    assert rejected == ["GER30"] and calendars == {}
    assert "group_not_in_config" in caplog.text


def test_malformed_group_is_rejected(caplog):
    cfg = {
        "market_calendar_by_group": {"broken": {"market_weekend_close_dow": "не число"}},
        "market_calendar_symbol_groups": {"X": "broken"},
    }
    with caplog.at_level(logging.ERROR):
        calendars, rejected = resolve_symbol_calendars(cfg, ["X"], where="t")
    assert rejected == ["X"] and calendars == {}
    assert "build_failed" in caplog.text


def test_healthy_symbols_survive_a_broken_neighbour(caplog):
    """Один зіпсований символ не має гасити решту воркера."""
    with caplog.at_level(logging.ERROR):
        calendars, rejected = resolve_symbol_calendars(CFG, ["XAU/USD", "NAS100"], where="t")
    assert set(calendars) == {"XAU/USD"} and rejected == ["NAS100"]
    assert "CALENDAR_SYMBOLS_REJECTED" in caplog.text


def test_real_config_has_calendar_for_every_active_symbol():
    """Живий config.json: усі symbols мають групу — інакше воркери б їх відсіяли."""
    from core.config_loader import load_system_config, resolve_config_path

    cfg = load_system_config(resolve_config_path())
    calendars, rejected = resolve_symbol_calendars(cfg, cfg["symbols"], where="config-check")
    assert rejected == [], f"символи без календаря у config.json: {rejected}"
    assert set(calendars) == set(cfg["symbols"])


@pytest.mark.parametrize("worker", ["m1_ingestion_worker", "polling.m1_poller", "tick_preview_worker"])
def test_worker_uses_shared_resolver_not_a_local_copy(worker):
    """X35: одна реалізація на три воркери — без відновлення 24/7-фолбеку."""
    import importlib
    import inspect

    mod = importlib.import_module(f"runtime.ingest.{worker}")
    src = inspect.getsource(mod)
    assert "resolve_symbol_calendars" in src
    assert "cal = calendar_from_group(cal_by_group[group])" not in src
