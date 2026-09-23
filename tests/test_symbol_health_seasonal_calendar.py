"""ADR-0095 S6a: symbol_health_check питає торговість хвилини в сезонного календаря групи, а не в плоских полів.

Плоскі поля — літній розклад. Узимку XAU має перерву 22:00–23:00, а не 21:00–22:00. Тож плоский календар бачив
зимове відкриття сесії розривом на межі діри (`at_gap` замість `inner`), а в зимових вікнах чекав бари 22:xx і не
чекав 21:xx. Вимір ланцюга йде по всій історії, тож кожна зима XAU/XAG ховала сигнал ADR-0101 C4. Версія виміру 5:
baseline v4 не порівнює виміри, залежні від календаря, і дає rc=3, а не хибний відкат.
"""
from __future__ import annotations

import copy
import datetime as dt
import json
from pathlib import Path
from typing import Any, Dict, List

import pytest

from core.config_loader import htf_anchor_rule_resolver, load_system_config, resolve_config_path
from core.health import HEALTH_MEASURE_VERSION, compare_reports, measure_chain_breaks, measure_holes
from core.session_anchor import RULE_NY_CLOSE_US_DST
from runtime.ingest.tick_common import calendar_for_symbol, calendar_from_group
from tools import symbol_health_check
from tools.symbol_health_check import check_symbol

SYMBOL = "XAU/USD"  # група cfd_us_22_23: узимку перерва 22:00–23:00, перший бар брокера 23:01
M1_MS = 60_000


def _utc(*args: int) -> int:
    return int(dt.datetime(*args, tzinfo=dt.timezone.utc).timestamp()) * 1000


@pytest.fixture(scope="module")
def cfg() -> Dict[str, Any]:
    """Справжній config репо (сезонні календарі), звужений до M1 — ланцюг і дірки міряються на ньому."""
    return dict(load_system_config(resolve_config_path(None)), tf_allowlist_s=[60])


# Пн 03.11.2025 — зима США. Вікно 1 доба до 23:05: Нд 02.11 23:05 → Пн 23:05.
WINTER_NOW = _utc(2025, 11, 3, 23, 5)
WINTER_SESSION_END = _utc(2025, 11, 3, 21, 59)
WINTER_FIRST_BROKER_BAR = _utc(2025, 11, 3, 23, 1)


def _write_winter_day(data_root: Path, cfg: Dict[str, Any]) -> List[int]:
    """M1 XAU на кожну торгову хвилину зимового розкладу у вікні, крім 23:00 (брокер відкривається о 23:01).

    Ланцюг суцільний, крім відкриття сесії: open 23:01 ≠ close 21:59 — розрив, який брокер робить щодня.
    """
    calendar = calendar_for_symbol(cfg, SYMBOL)
    window_start = WINTER_NOW - 86_400_000
    opens = [ms for ms in range(window_start, WINTER_NOW, M1_MS)
             if calendar.is_trading_minute(ms) and ms != _utc(2025, 11, 3, 23, 0)]
    tf_dir = data_root / SYMBOL.replace("/", "_") / "tf_60"
    tf_dir.mkdir(parents=True, exist_ok=True)
    price = 4000.0
    with open(tf_dir / "part-20251103.jsonl", "w", encoding="utf-8") as fh:
        for open_ms in opens:
            bar_open = price + 0.7 if open_ms == WINTER_FIRST_BROKER_BAR else price
            price = bar_open + 0.1
            row = {"symbol": SYMBOL, "tf_s": 60, "open_time_ms": open_ms, "close_time_ms": open_ms + M1_MS,
                   "o": bar_open, "h": price + 0.2, "l": bar_open - 0.2, "c": price, "v": 1.0, "complete": True,
                   "src": "history"}
            fh.write(json.dumps(row) + "\n")
    return opens


def _check(cfg: Dict[str, Any], data_root: Path) -> Dict[str, Any]:
    return check_symbol(cfg, SYMBOL, data_root=str(data_root), now_ms=WINTER_NOW, window_days=1,
                        anchor_rule_for_symbol=htf_anchor_rule_resolver(cfg))


def test_winter_session_open_is_inner_and_winter_break_is_not_a_hole(cfg, tmp_path):
    """Зимове відкриття 21:59 → 23:01 — розрив без діри (YELLOW), а дірка у вікні одна: 23:00, перша хвилина
    розкладу, яку брокер пропускає (дірки запізнення відкриття не враховують — так само влітку о 22:00)."""
    opens = _write_winter_day(tmp_path, cfg)

    m1 = _check(cfg, tmp_path)["tfs"]["60"]

    chain = m1["chain_breaks"]
    assert (chain["inner"], chain["at_gap"]) == (1, 0)
    assert (chain["inner_samples"][0]["prev_open"], chain["inner_samples"][0]["open"]) == (
        "2025-11-03 21:59", "2025-11-03 23:01")
    assert m1["holes"] == {"missing": 1, "expected": len(opens) + 1}
    assert "chain_breaks_inner=1" in m1["reasons"]


def test_flat_summer_calendar_hides_the_same_winter_open_as_at_gap(cfg, tmp_path):
    """Контроль (v4): плоскі поля групи = літній розклад. Той самий ряд дає `at_gap` замість `inner` і 61 «дірку»
    (22:00–22:59 і 23:00): узимку плоский календар чекає бари в перерві і ховає розрив відкриття від YELLOW."""
    _write_winter_day(tmp_path, cfg)
    bars = symbol_health_check._read_bars(str(tmp_path), SYMBOL, 60)
    flat = calendar_from_group(cfg["market_calendar_by_group"]["cfd_us_22_23"])

    chain = measure_chain_breaks(bars, is_trading_fn=flat.is_trading_minute, session_open_grace_min=1)
    holes = measure_holes([bar.open_time_ms for bar in bars], start_ms=WINTER_NOW - 86_400_000, end_ms=WINTER_NOW,
                          tf_s=60, rule=RULE_NY_CLOSE_US_DST, is_trading_fn=flat.is_trading_minute)

    assert (chain.inner, chain.at_gap, holes.missing) == (0, 1, 61)


def test_group_without_season_blocks_is_red_calendar_config_invalid_not_silent_summer(cfg, tmp_path):
    """Група без `season_rule`/блоків — health не міряє літнім розкладом узимку мовчки: RED з причиною."""
    flat_cfg = copy.deepcopy(cfg)
    group = flat_cfg["market_calendar_by_group"]["cfd_us_22_23"]
    for key in ("season_rule", "summer", "winter"):
        group.pop(key)

    res = _check(flat_cfg, tmp_path)

    assert (res["grade"], res["reasons"], res["tfs"]) == ("RED", ["calendar_config_invalid"], {})


def test_symbol_without_calendar_group_is_red_calendar_group_missing(cfg, tmp_path):
    no_group = copy.deepcopy(cfg)
    no_group["market_calendar_symbol_groups"].pop(SYMBOL)
    res = _check(no_group, tmp_path)
    assert (res["grade"], res["reasons"], res["tfs"]) == ("RED", ["calendar_group_missing"], {})


# ── межа версії виміру v5 ───────────────────────────────────────────────────
def _report(tf: Dict[str, Any], measure_version: int) -> Dict[str, Any]:
    return {"measure_version": measure_version,
            "symbols": {SYMBOL: {"symbol": SYMBOL, "grade": "YELLOW", "tfs": {"60": tf}}}}


def _m1_row(inner: int, at_gap: int, holes: int, age: int, bars: int = 1000, ohlc_bad: int = 0) -> Dict[str, Any]:
    return {"grade": "YELLOW", "reasons": [], "bars": bars, "age_buckets": age,
            "holes": {"missing": holes, "expected": 1380},
            "geometry": {"dup_conflicting": 0, "close_bad": 0, "ohlc_bad": ohlc_bad},
            "chain_breaks": {"inner": inner, "at_gap": at_gap}}


def test_v4_baseline_calendar_measures_not_compared_with_v5_but_bar_loss_is():
    """Зимові відкриття XAU: v4 `at_gap` 90 → v5 `inner` 90 на тих самих даних — не відкат. Втрата барів і OHLC
    від календаря не залежать і через межу v5 лишаються регресією."""
    assert HEALTH_MEASURE_VERSION == 5
    before = _report(_m1_row(inner=10, at_gap=90, holes=0, age=0), measure_version=4)
    after = _report(_m1_row(inner=100, at_gap=0, holes=3, age=2, bars=990, ohlc_bad=1), measure_version=5)

    res = compare_reports(before, after)

    assert {(r.measure, r.before, r.after) for r in res.regressions} == {("барів", 1000, 990), ("хибних OHLC", 0, 1)}
    assert res.verdicts_comparable is False
    assert set(res.calendar_skipped_measures) == {
        "дірок", "вік (бакетів)", "розривів ланцюга без діри", "розривів ланцюга на межі діри"}
    within_v5 = compare_reports(dict(before, measure_version=5), after)
    assert ("розривів ланцюга без діри", 10, 100) in {(r.measure, r.before, r.after) for r in within_v5.regressions}
    assert within_v5.calendar_skipped_measures == ()


def test_cli_v4_baseline_with_fewer_inner_breaks_gives_rc3_not_rollback(cfg, tmp_path):
    """CLI: baseline v4 із меншими `inner` і дірками (літній розклад узимку) — rc=3 «перезніміть baseline», не rc=1."""
    _write_winter_day(tmp_path / "data", cfg)
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps(dict(cfg, data_root=str(tmp_path / "data"))), encoding="utf-8")
    report_path = tmp_path / "report.json"
    assert symbol_health_check.main(["--symbol", SYMBOL, "--config", str(cfg_path), "--json", str(report_path)]) == 0

    baseline = json.loads(report_path.read_text(encoding="utf-8"))
    baseline["measure_version"] = 4
    m1 = baseline["symbols"][SYMBOL]["tfs"]["60"]
    m1["chain_breaks"].update(inner=0, at_gap=1)
    m1["holes"]["missing"] = 0
    baseline_path = tmp_path / "baseline_v4.json"
    baseline_path.write_text(json.dumps(baseline), encoding="utf-8")

    rc = symbol_health_check.main(["--symbol", SYMBOL, "--config", str(cfg_path), "--compare", str(baseline_path)])

    assert rc == 3
