"""ADR-0095 S5a: symbol_health_check міряє H4/D1 рівністю сезонній сітці символу, а не набором якорів config.

До S5a вимір знав «дозволений набір» (основний якір + DST-альтернативи) і пропускав літні H4/D1 на 22:00
як легальні: так 1612 літніх H4 XAU і 13 D1 жовтня 2025 пройшли health без жодного сигналу. Тепер бар поза
сезонною сіткою — RED `off_season_grid` з очікуваним відкриттям, а ряд, що чесно переходить сітку через
вихідні DST, — нуль дефектів: ні `off_season_grid`, ні дірок, ні відставання.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any, Dict, Iterable

import pytest

from core.config_loader import htf_anchor_rule_resolver, load_system_config, resolve_config_path
from core.health import HEALTH_MEASURE_VERSION
from tools import symbol_health_check
from tools.symbol_health_check import check_symbol

SYMBOL = "XAU/USD"  # група cfd_us_22_23 → ny_close_us_dst
H4_S = 14_400
D1_S = 86_400
H4_MS = H4_S * 1000
D1_MS = D1_S * 1000


def _utc(*args: int) -> int:
    return int(dt.datetime(*args, tzinfo=dt.timezone.utc).timestamp()) * 1000


def _write_bars(data_root: Path, tf_s: int, opens: Iterable[int]) -> None:
    """SSOT-JSONL як на диску: part-файл на UTC-добу відкриття, ключ low — "l"."""
    by_day: Dict[str, list] = {}
    for open_ms in sorted(opens):
        day = dt.datetime.fromtimestamp(open_ms / 1000, dt.timezone.utc).strftime("%Y%m%d")
        by_day.setdefault(day, []).append(open_ms)
    tf_dir = data_root / SYMBOL.replace("/", "_") / ("tf_%d" % tf_s)
    tf_dir.mkdir(parents=True, exist_ok=True)
    for day, day_opens in by_day.items():
        with open(tf_dir / ("part-%s.jsonl" % day), "w", encoding="utf-8") as fh:
            for open_ms in day_opens:
                row = {"symbol": SYMBOL, "tf_s": tf_s, "open_time_ms": open_ms, "close_time_ms": open_ms + tf_s * 1000,
                       "o": 1.0, "h": 2.0, "l": 0.5, "c": 1.5, "v": 1.0, "complete": True, "src": "derived"}
                fh.write(json.dumps(row) + "\n")


@pytest.fixture(scope="module")
def cfg() -> Dict[str, Any]:
    """Справжній config репо (календарі й htf_anchor), звужений до H4/D1 — каскад і корінь тут не міряються."""
    return dict(load_system_config(resolve_config_path(None)), tf_allowlist_s=[H4_S, D1_S])


def _check(cfg: Dict[str, Any], data_root: Path, now_ms: int, symbol: str = SYMBOL) -> Dict[str, Any]:
    return check_symbol(cfg, symbol, data_root=str(data_root), now_ms=now_ms, window_days=7,
                        anchor_rule_for_symbol=htf_anchor_rule_resolver(cfg))


def test_summer_bar_on_winter_hour_is_red_with_expected_open(cfg, tmp_path):
    """Літо 2026: H4 21:00 (сітка 17:00 NY — TV OANDA, нативний FXCM) серед сітки TV FX: 22/02/…, D1 цілком на 22:00
    (колишній легальний «alt») — RED."""
    h4_grid = [_utc(2026, 7, 5, 22) + i * H4_MS for i in range(30)]  # нд 22:00 … пт 18:00
    stray_h4 = _utc(2026, 7, 7, 21)
    d1_winter_hour = [_utc(2026, 7, 5, 22) + i * D1_MS for i in range(5)]
    _write_bars(tmp_path, H4_S, h4_grid + [stray_h4])
    _write_bars(tmp_path, D1_S, d1_winter_hour)

    res = _check(cfg, tmp_path, now_ms=_utc(2026, 7, 11, 12))

    assert res["htf_anchor_rule"] == "ny_close_us_dst"
    h4 = res["tfs"][str(H4_S)]
    assert h4["grade"] == "RED" and "off_season_grid=1" in h4["reasons"]
    assert (h4["geometry"]["off_season_grid"], h4["geometry"]["align_bad"]) == (1, 0)
    sample = h4["geometry"]["off_season_grid_samples"][0]
    assert (sample["open"], sample["expected_open"], sample["season"]) == ("2026-07-07 21:00", "2026-07-07 18:00", "summer")
    assert (sample["open_ms"], sample["expected_open_ms"]) == (stray_h4, _utc(2026, 7, 7, 18))
    assert h4["holes"]["missing"] == 0 and h4["age_buckets"] == 0, "решта ряду на сітці: лише зайвий бар"

    d1 = res["tfs"][str(D1_S)]
    assert d1["grade"] == "RED" and "off_season_grid=5" in d1["reasons"]
    assert d1["holes"] == {"missing": 5, "expected": 5}, "сітка 21:00 порожня: жоден бар 22:00 її не закриває"
    assert res["grade"] == "RED"


def test_series_across_2026_11_01_has_no_grid_defects(cfg, tmp_path):
    """Вихідні 01.11.2026: літня сітка до пт, зимова з нд 23:00 (H4 18:00 EST; D1 22:00) — нуль off_season_grid,
    дірок і відставання."""
    h4 = ([_utc(2026, 10, 27, 22) + i * H4_MS for i in range(18)]  # вт 22:00 … пт 30.10 18:00
          + [_utc(2026, 11, 1, 23) + i * H4_MS for i in range(12)])  # нд 23:00 … вт 03.11 19:00
    d1 = [_utc(2026, 10, 27, 21), _utc(2026, 10, 28, 21), _utc(2026, 10, 29, 21),
          _utc(2026, 11, 1, 22), _utc(2026, 11, 2, 22)]
    _write_bars(tmp_path, H4_S, h4)
    _write_bars(tmp_path, D1_S, d1)

    res = _check(cfg, tmp_path, now_ms=_utc(2026, 11, 3, 23, 30))

    for tf_s, expected in ((H4_S, 29), (D1_S, 4)):
        tf = res["tfs"][str(tf_s)]
        assert tf["geometry"]["off_season_grid"] == 0 and tf["geometry"]["off_season_grid_samples"] == []
        assert tf["holes"] == {"missing": 0, "expected": expected}
        assert tf["age_buckets"] == 0
        assert tf["grade"] != "RED", tf["reasons"]
    assert res["d1_anchor_on_session_edge"] is True, "зимовий D1 22:00 — межа сесії cfd_us"


def test_symbol_of_unmeasured_calendar_group_is_red_not_silent(cfg, tmp_path):
    """HKG33: група cfd_hk_main без виміряної сітки (ADR-0095 §8.4) — RED, а не тихий якір."""
    res = _check(cfg, tmp_path, now_ms=_utc(2026, 7, 11, 12), symbol="HKG33")
    assert (res["grade"], res["reasons"], res["tfs"]) == ("RED", ["htf_anchor_rule_missing"], {})


def test_main_report_carries_measure_version_and_off_season_grid(cfg, tmp_path):
    """Звіт CLI: `measure_version` поточна і не нижча за 3 (baseline v2 непорівнюваний), `off_season_grid` у геометрії."""
    _write_bars(tmp_path / "data", H4_S, [_utc(2026, 7, 7, 21)])
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps(dict(cfg, data_root=str(tmp_path / "data"))), encoding="utf-8")
    out = tmp_path / "report.json"

    rc = symbol_health_check.main(["--symbol", SYMBOL, "--config", str(cfg_path), "--json", str(out)])

    report = json.loads(out.read_text(encoding="utf-8"))
    assert rc == 0 and report["measure_version"] == HEALTH_MEASURE_VERSION >= 3
    assert report["symbols"][SYMBOL]["tfs"][str(H4_S)]["geometry"]["off_season_grid"] == 1
