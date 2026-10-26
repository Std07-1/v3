"""ADR-0106 §3.8: сторож VPS рахує відставання M1 у торгових хвилинах і розрізняє «потік мовчить» і «символ мовчить».

Інцидент 24.07–04.09: протух демо-доступ FXCM, M1 по XAU/XAG не йшли шість тижнів, а процеси були RUNNING.
"""

from __future__ import annotations

import calendar as calendar_mod
import json
import os

from core.health.measures import missing_trading_minutes
from tools import feed_lag_check
from tools.feed_lag_check import Thresholds, verdict_lines

MIN = 60_000
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _utc_ms(y, mo, d, h, mi):
    return calendar_mod.timegm((y, mo, d, h, mi, 0)) * 1000


def test_missing_trading_minutes_skips_non_trading_window():
    closed = range(_utc_ms(2026, 10, 21, 21, 0), _utc_ms(2026, 10, 21, 22, 0), MIN)  # денна перерва
    is_trading = lambda ms: ms not in closed  # noqa: E731
    last_open = _utc_ms(2026, 10, 21, 20, 55)
    now = _utc_ms(2026, 10, 21, 22, 6)  # остання закрита хвилина 22:05
    # 20:56..20:59 (4) + 22:00..22:05 (6); перерва не рахується
    assert missing_trading_minutes(last_open, now_ms=now, is_trading_fn=is_trading, cap=1440) == 10


def test_missing_trading_minutes_newest_bar_is_last_closed_minute_returns_zero():
    now = _utc_ms(2026, 10, 21, 14, 31) + 20_000
    last_open = _utc_ms(2026, 10, 21, 14, 30)
    assert missing_trading_minutes(last_open, now_ms=now, is_trading_fn=lambda ms: True, cap=1440) == 0


def test_missing_trading_minutes_old_series_stops_at_cap():
    now = _utc_ms(2026, 10, 21, 14, 0)
    last_open = now - 30 * 86_400_000
    assert missing_trading_minutes(last_open, now_ms=now, is_trading_fn=lambda ms: True, cap=1440) == 1440


THRESHOLDS = Thresholds(lag_min=12, feed_min_symbols=4, symbol_lag_min=1440)


def test_verdict_feed_silent_when_most_symbols_lag():
    lags = {"XAU/USD": 25, "XAG/USD": 25, "NAS100": 25, "US30": 24, "USD/JPY": 3}
    assert verdict_lines(lags, THRESHOLDS) == [
        "LAG XAU/USD 25", "LAG XAG/USD 25", "LAG NAS100 25", "LAG US30 24", "FEED_SILENT 4 5", "OK 5",
    ]


def test_verdict_us_holiday_indices_only_is_not_feed_silent():
    lags = {"XAU/USD": 0, "XAG/USD": 1, "NAS100": 200, "SPX500": 190, "US30": 200, "USD/JPY": 0}
    lines = verdict_lines(lags, THRESHOLDS)
    assert not any(line.startswith(("FEED_SILENT", "SYMBOL_SILENT")) for line in lines)
    assert lines[-1] == "OK 6"


def test_verdict_single_symbol_silent_for_a_trading_day():
    lags = {"XAU/USD": 0, "GER30": 1440}
    assert verdict_lines(lags, THRESHOLDS) == ["LAG GER30 1440", "SYMBOL_SILENT GER30 1440", "OK 2"]


def _box(tmp_path, bars_open_ms):
    """config.json репо з одним символом XAU/USD і тимчасовою data_root з M1 на диску."""
    with open(os.path.join(REPO, "config.json"), encoding="utf-8") as fh:
        cfg = json.load(fh)
    cfg["symbols"] = ["XAU/USD"]
    cfg["data_root"] = "./data"
    tf_dir = tmp_path / "data" / "XAU_USD" / "tf_60"
    tf_dir.mkdir(parents=True)
    rows = [
        {"symbol": "XAU/USD", "tf_s": 60, "open_time_ms": o, "close_time_ms": o + MIN, "o": 1.0, "h": 1.0,
         "low": 1.0, "c": 1.0, "v": 1, "complete": True, "src": "history"}
        for o in bars_open_ms
    ]
    (tf_dir / "part-20261023.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(cfg), encoding="utf-8")
    return str(config_path)


def test_main_weekend_after_friday_close_reports_no_lag(tmp_path, capsys):
    config_path = _box(tmp_path, [_utc_ms(2026, 10, 23, 20, 58)])  # остання M1 перед закриттям п'ятниці
    rc = feed_lag_check.main(["--config", config_path, "--now-ms", str(_utc_ms(2026, 10, 24, 12, 0))])
    assert rc == feed_lag_check.EXIT_OK
    assert capsys.readouterr().out.split("\n")[0] == "OK 1"


def test_main_trading_hours_without_new_bars_reports_lag(tmp_path, capsys):
    config_path = _box(tmp_path, [_utc_ms(2026, 10, 21, 14, 0)])
    rc = feed_lag_check.main(["--config", config_path, "--now-ms", str(_utc_ms(2026, 10, 21, 14, 31))])
    assert rc == feed_lag_check.EXIT_OK
    # бракує хвилин 14:01..14:30 (остання закрита — 14:30)
    assert capsys.readouterr().out.split("\n")[:2] == ["LAG XAU/USD 30", "OK 1"]


def test_main_unreadable_config_exits_2(tmp_path, capsys):
    bad = tmp_path / "config.json"
    bad.write_text("{}", encoding="utf-8")
    assert feed_lag_check.main(["--config", str(bad)]) == feed_lag_check.EXIT_UNREADABLE
    assert "FEED_LAG_CHECK_FAILED" in capsys.readouterr().err
