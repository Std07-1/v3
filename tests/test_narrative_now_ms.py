"""Наратив на заданий момент: `now_ms` — один для календаря ринку, сесії й рівнів (ADR-0113 Ф1, R10).

Прогін по історії розбирає ринок на час закриття бару, а не на настінний годинник. Без `now_ms` поведінка жива —
як і раніше, годинник. З `now_ms` годинник не читається взагалі: інакше прогін був би недетермінований.
"""
from __future__ import annotations

import copy
import datetime as dt
import json
import os
from types import SimpleNamespace

import pytest

import core.smc.engine as engine_mod
import runtime.smc.smc_runner as smc_runner_mod
from core.model.bars import CandleBar
from core.smc.config import SmcConfig
from core.smc.engine import SmcEngine
from runtime.smc.smc_runner import SmcRunner

XAU = "XAU/USD"
_REPO_CONFIG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.json")


def _utc_ms(year, month, day, hour, minute=0):
    return int(dt.datetime(year, month, day, hour, minute, tzinfo=dt.timezone.utc).timestamp() * 1000)


# Жовтень 2026 — ще літній сезон (Лондон до 25.10, Нью-Йорк до 01.11)
WED_LONDON_KZ = _utc_ms(2026, 10, 14, 8, 30)
WED_ASIA = _utc_ms(2026, 10, 14, 5, 0)
WED_OFF_SESSION = _utc_ms(2026, 10, 14, 23, 30)  # Нью-Йорк закрився о 21:00, ринок XAU знову з 22:00
SAT_CLOSED = _utc_ms(2026, 10, 17, 12, 0)


def _bars(tf_s, end_ms, count=60):
    step_ms = tf_s * 1000
    first_open = (end_ms - count * step_ms) // step_ms * step_ms
    bars = []
    for i in range(count):
        price = 4100.0 + (i % 7) * 3.0
        bars.append(CandleBar(XAU, tf_s, first_open + i * step_ms, first_open + (i + 1) * step_ms,
                              price, price + 4.0, price - 4.0, price + 1.0, 100.0, True, "history"))
    return bars


@pytest.fixture
def runner(tmp_path):
    cfg = copy.deepcopy(json.load(open(_REPO_CONFIG, encoding="utf-8")))
    cfg["symbols"] = [XAU]
    cfg["smc"]["signal_journal"] = {"enabled": False, "path": str(tmp_path / "signals")}
    cfg["smc"]["narrative_journal"] = {"enabled": False, "path": str(tmp_path / "narrative_journal")}
    cfg["smc"]["tda_cascade"] = {"enabled": False}
    engine = SmcEngine(SmcConfig.from_dict(cfg["smc"]))
    for tf_s in cfg["smc"]["compute_tfs"]:
        engine.update(XAU, tf_s, _bars(tf_s, WED_ASIA))
    engine.feed_m1_bars_bulk(XAU, _bars(60, WED_ASIA, count=600))
    smc_runner = SmcRunner(cfg, engine)
    smc_runner._warmup_done = True
    return smc_runner


@pytest.fixture
def wall_clock_forbidden(monkeypatch):
    """Годинник у runner і рушії падає — тест доводить, що з now_ms його не читають."""

    def _boom():
        raise AssertionError("настінний годинник прочитано попри now_ms")

    monkeypatch.setattr(smc_runner_mod, "time", SimpleNamespace(time=_boom))
    monkeypatch.setattr(engine_mod, "time", SimpleNamespace(time=_boom))


def test_now_ms_decides_market_closed_not_wall_clock(runner, wall_clock_forbidden):
    closed = runner.get_narrative(XAU, 900, 4110.0, 0.0, now_ms=SAT_CLOSED)
    open_ = runner.get_narrative(XAU, 900, 4110.0, 0.0, now_ms=WED_ASIA)

    assert closed.market_phase == "closed"
    assert open_.market_phase != "closed"


@pytest.mark.parametrize(
    "now_ms, session, killzone",
    [(WED_LONDON_KZ, "london", True), (WED_ASIA, "asia", False), (WED_OFF_SESSION, "", False)],
)
def test_now_ms_decides_session_and_killzone(runner, wall_clock_forbidden, now_ms, session, killzone):
    block = runner.get_narrative(XAU, 900, 4110.0, 0.0, now_ms=now_ms)

    assert block.current_session == session
    assert block.in_killzone is killzone


def test_display_snapshot_levels_follow_now_ms(runner, wall_clock_forbidden):
    engine = runner._engine
    at_asia = engine.get_display_snapshot(XAU, 900, now_ms=WED_ASIA).levels
    expected = engine.get_display_levels(XAU, 900, WED_ASIA)

    assert [lv.id for lv in at_asia] == [lv.id for lv in expected]


def test_without_now_ms_live_path_reads_wall_clock(runner, monkeypatch):
    monkeypatch.setattr(smc_runner_mod, "time", SimpleNamespace(time=lambda: SAT_CLOSED / 1000))

    block = runner.get_narrative(XAU, 900, 4110.0, 0.0)

    assert block.market_phase == "closed"  # жива поведінка не змінилась: годинник — субота
