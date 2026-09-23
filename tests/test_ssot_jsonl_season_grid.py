"""Писар SSOT пише H4/D1 лише на сезонній сітці символу: рівність, а не членство в наборі якорів (ADR-0095 §3.3, §3.7).

Раніше `JsonlAppender` пропускав бар, якщо його якір входив у набір {primary, alt, alt2} з config у будь-яку дату.
Так на диск потрапили 1612 літніх H4 XAU на зимовій сітці і 13 D1 XAU жовтня 2025 на 22:00 UTC без жодного сигналу.
Тепер відмова гучна (`bar_off_season_grid` з очікуваним відкриттям), а HTF-бар без резолвера правила — `anchor_rule_missing`.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from core.config_loader import htf_anchor_rule_resolver
from core.model.bars import CandleBar
from core.session_anchor import D1_S, H4_S, OffSeasonGridError
from runtime.store.ssot_jsonl import JsonlAppender

_CFG = {
    "htf_anchor": {"rule_by_calendar_group": {"cfd_us_22_23": "ny_close_us_dst", "crypto_24x7": "utc_midnight"}},
    "market_calendar_symbol_groups": {"XAU/USD": "cfd_us_22_23", "BTCUSDT": "crypto_24x7"},
}


def _utc_ms(*args: int) -> int:
    return int(dt.datetime(*args, tzinfo=dt.timezone.utc).timestamp() * 1000)


def _bar(symbol: str, tf_s: int, open_ms: int) -> CandleBar:
    return CandleBar(symbol=symbol, tf_s=tf_s, open_time_ms=open_ms, close_time_ms=open_ms + tf_s * 1000,
                     o=100.0, h=101.0, low=99.0, c=100.5, v=10.0, complete=True, src="derived")


def _written_opens(root: Path, symbol: str, tf_s: int) -> list:
    tf_dir = root / symbol.replace("/", "_") / ("tf_%d" % tf_s)
    return [json.loads(line)["open_time_ms"] for part in sorted(tf_dir.glob("part-*.jsonl"))
            for line in part.read_text(encoding="utf-8").splitlines() if line.strip()]


def _appender(root: Path) -> JsonlAppender:
    return JsonlAppender(str(root), anchor_rule_for_symbol=htf_anchor_rule_resolver(_CFG))


def _assert_rejected(app: JsonlAppender, bar: CandleBar, expected_open_ms: int, season: str) -> None:
    with pytest.raises(OffSeasonGridError) as exc:
        app.append(bar)
    assert exc.value.expected_open_ms == expected_open_ms
    assert "bar_off_season_grid" in str(exc.value) and ("season=%s" % season) in str(exc.value)


def test_jsonl_appender_winter_grid_in_summer_rejected(tmp_path: Path):
    """Ср 01.07.2026 — літо США: H4 21/01/05/.. UTC. Бар 22:00 (зимова сітка) — відмова з очікуваним 21:00."""
    app = _appender(tmp_path)
    _assert_rejected(app, _bar("XAU/USD", H4_S, _utc_ms(2026, 7, 1, 22)), _utc_ms(2026, 7, 1, 21), "summer")
    assert _written_opens(tmp_path, "XAU/USD", H4_S) == []

    on_grid = _utc_ms(2026, 7, 1, 21)
    app.append(_bar("XAU/USD", H4_S, on_grid))
    app.close()
    assert _written_opens(tmp_path, "XAU/USD", H4_S) == [on_grid]


def test_jsonl_appender_summer_grid_in_winter_rejected(tmp_path: Path):
    """Пн 05.01.2026 — зима США: H4 22/02/06/10/14/18 UTC. Бар 21:00 (літня сітка) лежить у бакеті 18:00 — відмова."""
    app = _appender(tmp_path)
    _assert_rejected(app, _bar("XAU/USD", H4_S, _utc_ms(2026, 1, 5, 21)), _utc_ms(2026, 1, 5, 18), "winter")
    assert _written_opens(tmp_path, "XAU/USD", H4_S) == []

    on_grid = _utc_ms(2026, 1, 5, 22)
    app.append(_bar("XAU/USD", H4_S, on_grid))
    app.close()
    assert _written_opens(tmp_path, "XAU/USD", H4_S) == [on_grid]


def test_jsonl_appender_d1_oct2025_2200_rejected(tmp_path: Path):
    """Ср 15.10.2025 — літо США до 02.11: D1 відкривається о 21:00 UTC. Старий валідатор пропускав 22:00 як «d1_alt»."""
    app = _appender(tmp_path)
    _assert_rejected(app, _bar("XAU/USD", D1_S, _utc_ms(2025, 10, 15, 22)), _utc_ms(2025, 10, 15, 21), "summer")
    assert _written_opens(tmp_path, "XAU/USD", D1_S) == []

    on_grid = _utc_ms(2025, 10, 15, 21)
    app.append(_bar("XAU/USD", D1_S, on_grid))
    app.close()
    assert _written_opens(tmp_path, "XAU/USD", D1_S) == [on_grid]


def test_jsonl_appender_binance_utc_midnight_accepted(tmp_path: Path):
    """BTCUSDT (crypto_24x7 → utc_midnight): H4 00/04/.. і D1 00:00 UTC приймаються в будь-який сезон; H4 21:00 — ні."""
    app = _appender(tmp_path)
    h4_opens = [_utc_ms(2026, 7, 1, 0), _utc_ms(2026, 7, 1, 20), _utc_ms(2026, 1, 5, 4)]
    d1_opens = [_utc_ms(2026, 7, 1), _utc_ms(2026, 1, 5)]
    for open_ms in h4_opens:
        app.append(_bar("BTCUSDT", H4_S, open_ms))
    for open_ms in d1_opens:
        app.append(_bar("BTCUSDT", D1_S, open_ms))
    _assert_rejected(app, _bar("BTCUSDT", H4_S, _utc_ms(2026, 7, 1, 21)), _utc_ms(2026, 7, 1, 20), "none")
    app.close()
    assert sorted(_written_opens(tmp_path, "BTCUSDT", H4_S)) == sorted(h4_opens)
    assert sorted(_written_opens(tmp_path, "BTCUSDT", D1_S)) == sorted(d1_opens)


@pytest.mark.parametrize("tf_s, open_ms", [(H4_S, _utc_ms(2026, 7, 1, 21)), (D1_S, _utc_ms(2026, 7, 1, 21))])
def test_jsonl_appender_htf_without_resolver_raises(tmp_path: Path, tf_s: int, open_ms: int):
    """Без резолвера HTF-бар навіть на правильній сітці — гучна відмова, а не тихий якір 0; M1 резолвера не потребує."""
    app = JsonlAppender(str(tmp_path))
    with pytest.raises(ValueError, match="anchor_rule_missing"):
        app.append(_bar("XAU/USD", tf_s, open_ms))
    assert _written_opens(tmp_path, "XAU/USD", tf_s) == []

    m1 = CandleBar(symbol="XAU/USD", tf_s=60, open_time_ms=open_ms, close_time_ms=open_ms + 60_000,
                   o=1.0, h=1.0, low=1.0, c=1.0, v=1.0, complete=True, src="history")
    app.append(m1)
    app.close()
    assert _written_opens(tmp_path, "XAU/USD", 60) == [open_ms]


@pytest.mark.parametrize("tf_s, short_close_s", [(H4_S, 3 * 3600), (D1_S, 23 * 3600)])
def test_jsonl_appender_htf_on_grid_with_wrong_close_rejected(tmp_path: Path, tf_s: int, short_close_s: int):
    """Бар на сезонній сітці, але close ≠ open + tf (обрубок) — `bar_close_time_invalid`, на диску нічого (I2)."""
    open_ms = _utc_ms(2026, 7, 1, 21)
    stub = CandleBar(symbol="XAU/USD", tf_s=tf_s, open_time_ms=open_ms, close_time_ms=open_ms + short_close_s * 1000,
                     o=100.0, h=101.0, low=99.0, c=100.5, v=10.0, complete=True, src="derived")
    app = _appender(tmp_path)
    with pytest.raises(ValueError, match="bar_close_time_invalid"):
        app.append(stub)
    app.close()
    assert _written_opens(tmp_path, "XAU/USD", tf_s) == []


@pytest.mark.parametrize("tf_s, open_shift_ms, src, reason", [
    (60, 30_000, "history", "bar_bucket_misaligned"),
    (3600, 30 * 60_000, "history", "bar_bucket_misaligned"),
    (60, 0, "derived", "derived_1m_forbidden"),
])
def test_jsonl_appender_intraday_geometry_rejected(tmp_path: Path, tf_s: int, open_shift_ms: int, src: str,
                                                   reason: str):
    """M1..H1 перевіряються від епохи без резолвера: зсув open або derived M1 — гучна відмова, на диску нічого."""
    open_ms = _utc_ms(2026, 7, 1, 21) + open_shift_ms
    bar = CandleBar(symbol="XAU/USD", tf_s=tf_s, open_time_ms=open_ms, close_time_ms=open_ms + tf_s * 1000,
                    o=1.0, h=1.0, low=1.0, c=1.0, v=1.0, complete=True, src=src)
    app = _appender(tmp_path)
    with pytest.raises(ValueError, match=reason):
        app.append(bar)
    app.close()
    assert _written_opens(tmp_path, "XAU/USD", tf_s) == []


@patch("runtime.store.uds.build_redis_snapshot_writer", return_value=None)
@patch("runtime.store.uds._redis_layer_from_cfg", return_value=None)
@patch("runtime.store.uds._updates_bus_from_cfg", return_value=None)
def test_build_uds_writer_wires_season_grid_resolver(_bus, _redis, _snap, tmp_path: Path):
    """Живий писар (build_uds_from_config, writer_components) отримує резолвер з config; без htf_anchor — відмова старту."""
    from runtime.store.uds import build_uds_from_config

    base = {"symbols": ["XAU/USD"], "tf_allowlist_s": [60, 14400, 86400], "redis": {"enabled": False}}
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps(base), encoding="utf-8")
    with pytest.raises(ValueError, match="CONFIG_HTF_ANCHOR_MISSING"):
        build_uds_from_config(str(cfg_path), str(tmp_path / "data"), "boot-s3a", writer_components=True)

    cfg_path.write_text(json.dumps(dict(base, **_CFG)), encoding="utf-8")
    uds = build_uds_from_config(str(cfg_path), str(tmp_path / "data"), "boot-s3a", writer_components=True)
    try:
        with pytest.raises(OffSeasonGridError):
            uds._jsonl.append(_bar("XAU/USD", H4_S, _utc_ms(2026, 7, 1, 22)))  # noqa: SLF001
    finally:
        uds.close()
