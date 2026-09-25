"""ADR-0103 §3.2/S3c: рішення нічного settle — перерва за сезонними календарями, вікна з лагом групи, стан, ретеншн."""
from __future__ import annotations

import pytest

from core.config_loader import load_system_config, m1_settle_policy, pick_config_path
from runtime.ingest.tick_common import calendar_for_symbol
from tools.repair import settle_daily_plan as sp

H = sp.HOUR_MS


def _ms(text):
    return sp.parse_iso_minute(text)


@pytest.fixture(scope="module")
def trading():
    cfg = load_system_config(pick_config_path())
    return {s: calendar_for_symbol(cfg, s).is_trading_minute for s in cfg["symbols"]}


@pytest.mark.parametrize("now, reopen", [
    ("2026-09-24T21:05", "2026-09-24T22:00"),  # літо, Чт: перерва US-групи, EU закриті
    ("2026-11-10T22:05", "2026-11-10T23:00"),  # зима після DST: перерва на годину пізніше
    ("2026-09-26T09:05", "2026-09-27T22:00"),  # Субота: добір хвоста п'ятниці, відкриття — Нд 22:00
])
def test_break_is_common_to_all_symbols_and_the_deadline_keeps_a_guard(trading, now, reopen):
    window = sp.break_window(trading, _ms(now), guard_min=10)
    assert window is not None and window.reopen_ms == _ms(reopen)
    assert window.deadline_ms == _ms(reopen) - 10 * sp.M1_MS


@pytest.mark.parametrize("now", ["2026-11-10T21:05", "2026-09-25T14:00", "2026-09-24T22:05"])
def test_no_run_while_any_symbol_trades(trading, now):
    """Узимку 21:05 брокер ще торгує (перерва 22–23) — cron-слот 21:05 мусить мовчки пропустити день."""
    assert sp.break_window(trading, _ms(now), guard_min=10) is None


def test_window_ends_a_revision_lag_before_the_fetch_per_group():
    policy = m1_settle_policy(load_system_config(pick_config_path()))
    windows = {w.symbol: w for w in sp.symbol_windows(policy.lag_h_by_symbol, _ms("2026-09-25T21:05"), 96, {})}
    assert windows["XAU/USD"].to_ms == _ms("2026-09-25T15:05")
    assert windows["GER30"].to_ms == _ms("2026-09-25T09:05")
    assert windows["XAU/USD"].from_ms == _ms("2026-09-25T15:05") - 96 * H and windows["XAU/USD"].settles


def test_missed_runs_extend_the_window_back_to_the_last_settled_minute():
    lags = {"XAU/USD": 6, "EUSTX50": 12}
    settled = {"XAU_USD": _ms("2026-09-10T15:05"), "EUSTX50": _ms("2026-09-25T00:00")}
    windows = {w.symbol: w for w in sp.symbol_windows(lags, _ms("2026-09-25T21:05"), 96, settled)}
    assert windows["XAU/USD"].from_ms == _ms("2026-09-10T15:05")
    assert windows["EUSTX50"].from_ms == _ms("2026-09-25T09:05") - 96 * H  # свіжий стан — усе одно lookback


def test_m1_fetch_window_starts_on_the_hour_and_runs_to_the_fetch():
    windows = sp.symbol_windows({"XAU/USD": 6, "GER30": 12}, _ms("2026-09-25T21:05"), 96, {})
    assert sp.m1_fetch_window(windows, _ms("2026-09-25T21:05") + 30_000) == (
        _ms("2026-09-21T09:00"), _ms("2026-09-25T21:05"))


def test_state_round_trip_keeps_the_latest_settled_minute(tmp_path):
    windows = sp.symbol_windows({"XAU/USD": 6}, _ms("2026-09-25T21:05"), 96, {})
    sp.save_settled_to(str(tmp_path), windows, {"XAU_USD": _ms("2026-09-26T03:05"), "NAS100": _ms("2026-09-25T15:05")},
                       "run-1")
    assert sp.load_settled_to(str(tmp_path)) == {"XAU_USD": _ms("2026-09-26T03:05"), "NAS100": _ms("2026-09-25T15:05")}
    assert sp.load_settled_to(str(tmp_path / "absent")) == {}


def test_retention_drops_only_the_oldest_beyond_keep():
    names = ["20260925T210501Z", "20260923T210500Z", "20260924T210502Z"]
    assert sp.expired(names, 2) == ["20260923T210500Z"] and sp.expired(names, 3) == []
    assert sp.expired(names, 0) == sorted(names)
