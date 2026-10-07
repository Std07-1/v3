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
def calendars():
    cfg = load_system_config(pick_config_path())
    return {s: calendar_for_symbol(cfg, s) for s in cfg["symbols"]}


def _repo_break(calendars, now):
    """Перерва за правилом прогону: символи без денної перерви (FX) не блокують (ADR-0103 rev §3.5)."""
    trading = {s: c.is_trading_minute for s, c in calendars.items()}
    return sp.break_window(trading, _ms(now), guard_min=10, continuous=sp.continuous_symbols(calendars, _ms(now)))


@pytest.mark.parametrize("now, reopen", [
    ("2026-09-24T21:05", "2026-09-24T22:00"),  # літо, Чт: перерва US-групи, EU закриті, FX торгує
    ("2026-11-10T22:05", "2026-11-10T23:00"),  # зима після DST: перерва на годину пізніше
    ("2026-09-26T09:05", "2026-09-27T21:00"),  # Субота: добір хвоста п'ятниці; першим відкривається FX — Нд 21:00
])
def test_break_of_symbols_with_a_daily_break_and_the_deadline_keeps_a_guard(calendars, now, reopen):
    window = _repo_break(calendars, now)
    assert window is not None and window.reopen_ms == _ms(reopen)
    assert window.deadline_ms == _ms(reopen) - 10 * sp.M1_MS


@pytest.mark.parametrize("now", ["2026-11-10T21:05", "2026-09-25T14:00", "2026-09-24T22:05"])
def test_no_run_while_any_symbol_with_a_daily_break_trades(calendars, now):
    """Узимку 21:05 брокер ще торгує (перерва 22–23) — cron-слот 21:05 мусить мовчки пропустити день."""
    assert _repo_break(calendars, now) is None


def test_window_ends_a_revision_lag_before_the_fetch_per_group():
    policy = m1_settle_policy(load_system_config(pick_config_path()))
    windows = {w.symbol: w for w in sp.symbol_windows(policy.lag_h_by_symbol, _ms("2026-09-25T21:05"), 96, {})}
    assert windows["XAU/USD"].to_ms == _ms("2026-09-25T15:05")
    assert windows["GER30"].to_ms == _ms("2026-09-25T09:05")
    assert windows["XAU/USD"].from_ms == _ms("2026-09-25T15:05") - 96 * H and windows["XAU/USD"].settles


def test_each_minute_settles_once_the_window_starts_where_the_last_run_stopped():
    """Регресія 28.09: вікно з перекриттям 96 год перетнуло вихідні й затягнуло округлення історії індексів брокером
    (тисячі хвилин Чт/Пт) — TV показує живий потік, не перерахований архів."""
    lags = {"XAU/USD": 6, "EUSTX50": 12}
    settled = {"XAU_USD": _ms("2026-09-26T03:05"), "EUSTX50": _ms("2026-09-25T21:05")}
    windows = {w.symbol: w for w in sp.symbol_windows(lags, _ms("2026-09-28T21:05"), 96, settled)}
    assert (windows["XAU/USD"].from_ms, windows["XAU/USD"].to_ms) == (_ms("2026-09-26T03:05"), _ms("2026-09-28T15:05"))
    assert (windows["EUSTX50"].from_ms, windows["EUSTX50"].to_ms) == (_ms("2026-09-25T21:05"), _ms("2026-09-28T09:05"))
    assert windows["XAU/USD"].unsettled_from_ms is None


def test_long_missed_runs_leave_the_older_gap_unsettled_and_flagged():
    settled = {"XAU_USD": _ms("2026-09-10T15:05")}
    (w,) = sp.symbol_windows({"XAU/USD": 6}, _ms("2026-09-25T21:05"), 96, settled)
    assert w.from_ms == _ms("2026-09-25T15:05") - 96 * H and w.unsettled_from_ms == _ms("2026-09-10T15:05")


def test_state_ahead_of_the_lag_boundary_gives_an_empty_window():
    (w,) = sp.symbol_windows({"GER30": 12}, _ms("2026-09-28T21:05"), 96, {"GER30": _ms("2026-09-28T15:05")})
    assert not w.settles


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


def test_provisional_tail_settles_to_the_fetch_but_keeps_state_at_the_lag():
    """XAG 28.09 20:59 брокер опублікував округленим (close 60.0), в архіві о 21:05 — уже 60.495: хвіст прибирає його
    тієї ж ночі, а стан лишається на «забір − лаг» — наступна ніч переустоює хвіст остаточно."""
    fetched = _ms("2026-09-29T21:05")
    settled = {"XAG_USD": _ms("2026-09-28T15:05")}
    (w,) = sp.symbol_windows({"XAG/USD": 6}, fetched, 96, settled, provisional_to_ms=fetched)
    assert (w.from_ms, w.to_ms, w.settled_to_ms) == (_ms("2026-09-28T15:05"), fetched, _ms("2026-09-29T15:05"))


def test_state_advances_only_to_the_lag_boundary_after_a_provisional_run(tmp_path):
    fetched = _ms("2026-09-29T21:05")
    windows = sp.symbol_windows({"XAG/USD": 6}, fetched, 96, {}, provisional_to_ms=fetched)
    sp.save_settled_to(str(tmp_path), windows, {}, "run")
    assert sp.load_settled_to(str(tmp_path)) == {"XAG_USD": _ms("2026-09-29T15:05")}


@pytest.fixture(scope="module")
def calendars_with_fx():
    """Символи config + USD/JPY (група FX 24x5 є в config і до активації W6)."""
    cfg = load_system_config(pick_config_path())
    symbols = list(dict.fromkeys(list(cfg["symbols"]) + ["USD/JPY"]))
    return {s: calendar_for_symbol(cfg, s) for s in symbols}


@pytest.mark.parametrize("ts", ["2026-09-24T21:05", "2026-11-10T22:05"])
def test_daily_break_is_read_from_the_calendar_and_fx_has_none(calendars_with_fx, ts):
    assert not calendars_with_fx["USD/JPY"].has_daily_break_at(_ms(ts))
    assert calendars_with_fx["XAU/USD"].has_daily_break_at(_ms(ts)) and calendars_with_fx["GER30"].has_daily_break_at(_ms(ts))


def test_fx_without_a_daily_break_never_blocks_the_weekday_run(calendars_with_fx):
    """ADR-0103 rev 07.10.2026: USD/JPY торгує й о 21:05 — «усі в перерві» з ним не настало б ніколи, і будній settle
    зник би для всіх символів. FX прогону не блокує; дедлайн — відкриття металів та індексів о 22:00."""
    now = _ms("2026-09-24T21:05")
    trading = {s: c.is_trading_minute for s, c in calendars_with_fx.items()}
    continuous = sp.continuous_symbols(calendars_with_fx, now)
    assert continuous == frozenset({"USD/JPY"}) and trading["USD/JPY"](now)
    assert sp.break_window(trading, now, guard_min=10) is None  # старе правило: будній прогін не стартує ніколи
    window = sp.break_window(trading, now, guard_min=10, continuous=continuous)
    assert window is not None and window.reopen_ms == _ms("2026-09-24T22:00")


def test_closed_fx_reopens_first_on_sunday_and_sets_the_weekend_deadline(calendars_with_fx):
    """Субота: закрите все; FX відкривається Нд 21:00 UTC, на годину раніше металів — дедлайн рахується від нього."""
    now = _ms("2026-09-26T09:05")
    trading = {s: c.is_trading_minute for s, c in calendars_with_fx.items()}
    window = sp.break_window(trading, now, guard_min=10, continuous=sp.continuous_symbols(calendars_with_fx, now))
    assert window is not None and window.reopen_ms == _ms("2026-09-27T21:00")


def test_only_symbols_without_a_daily_break_trading_is_not_a_break():
    always = lambda _ms: True  # noqa: E731
    assert sp.break_window({"USD/JPY": always}, _ms("2026-09-24T21:05"), 10, continuous=frozenset({"USD/JPY"})) is None


def test_symbol_trading_during_the_run_gets_no_provisional_tail():
    """Хвилини FX після «забір − лаг» брокер ще доправить — провізорний хвіст лише символам, що стоять у перерві."""
    fetched = _ms("2026-09-29T21:05")
    windows = {w.symbol: w for w in sp.symbol_windows({"XAG/USD": 6, "USD/JPY": 6}, fetched, 96, {},
                                                       provisional_to_ms=fetched, no_provisional=frozenset({"USD/JPY"}))}
    assert windows["XAG/USD"].to_ms == fetched
    assert windows["USD/JPY"].to_ms == windows["USD/JPY"].settled_to_ms == _ms("2026-09-29T15:05")
