"""Розбір символу в runner, кадрі WS і API v3 (ADR-0113 Ф3).

Розбір рахується раз на закритий бар рушія і на момент цього бару (не настінний годинник), не пише журнал сигналів,
а за вимкненим прапорцем `smc.briefing.enabled` його немає ніде.
"""
from __future__ import annotations

import copy
import json

import pytest

from core.model.bars import CandleBar
from core.smc.config import SmcConfig
from core.smc.engine import SmcEngine
from runtime.smc.smc_runner import SmcRunner
from runtime.ws.ws_server import _put_briefing
from test_narrative_now_ms import _REPO_CONFIG, SAT_CLOSED, WED_ASIA, XAU, _bars

M5 = 300


def _runner(tmp_path, end_ms=WED_ASIA, enabled=True):
    cfg = copy.deepcopy(json.load(open(_REPO_CONFIG, encoding="utf-8")))
    cfg["symbols"] = [XAU]
    cfg["smc"]["signal_journal"] = {"enabled": False, "path": str(tmp_path / "signals")}
    cfg["smc"]["narrative_journal"] = {"enabled": False, "path": str(tmp_path / "narrative_journal")}
    cfg["smc"]["tda_cascade"] = {"enabled": False}
    cfg["smc"]["briefing"]["enabled"] = enabled
    engine = SmcEngine(SmcConfig.from_dict(cfg["smc"]))
    for tf_s in cfg["smc"]["compute_tfs"]:
        engine.update(XAU, tf_s, _bars(tf_s, end_ms))
    engine.feed_m1_bars_bulk(XAU, _bars(60, end_ms, count=600))
    runner = SmcRunner(cfg, engine)
    runner._warmup_done = True
    return runner


@pytest.fixture
def journal_forbidden(monkeypatch):
    def _boom(*_args, **_kwargs):
        raise AssertionError("розбір записав журнал сигналів")

    monkeypatch.setattr("runtime.smc.signal_journal.SignalJournal.record", _boom)


def test_briefing_is_composed_at_the_end_of_the_last_closed_bar(tmp_path, journal_forbidden):
    runner = _runner(tmp_path)
    last_m5 = runner._engine.get_bars(XAU, M5)[-1]

    briefing = runner.get_briefing(XAU)

    assert briefing.symbol == XAU and briefing.as_of_ms == last_m5.open_time_ms + M5 * 1000
    assert [section.key for section in briefing.sections] == ["bias", "context", "setups", "liquidity", "session"]
    assert [row.tf for row in briefing.sections[2].rows][:1] == ["H1"]
    assert [row.tf for row in briefing.sections[3].rows] == ["H1", "H1"]
    session = briefing.sections[4].rows[0]
    assert (session.text, session.notes) == ("Азія — поза кілзоною", ("Далі: Лондон о 07:00 UTC — через 2 год",))


def test_briefing_is_cached_until_a_new_bar_closes(tmp_path):
    runner = _runner(tmp_path)
    first = runner.get_briefing(XAU)
    assert runner.get_briefing(XAU) is first

    last = runner._engine.get_bars(XAU, M5)[-1]
    next_open = last.open_time_ms + M5 * 1000
    runner._engine.update(XAU, M5, [CandleBar(XAU, M5, next_open, next_open + M5 * 1000, last.c, last.c + 2.0,
                                              last.c - 2.0, last.c + 1.0, 50.0, True, "history")])

    second = runner.get_briefing(XAU)
    assert second is not first and second.as_of_ms == next_open + M5 * 1000


def test_closed_market_briefing_and_no_briefing_when_disabled_or_before_warmup(tmp_path):
    closed = _runner(tmp_path, end_ms=SAT_CLOSED).get_briefing(XAU)
    disabled = _runner(tmp_path, enabled=False)
    cold = _runner(tmp_path)
    cold._warmup_done = False

    assert closed.warnings == ("market_closed",) and closed.sections[0].key == "state"
    assert disabled.get_briefing(XAU) is None and cold.get_briefing(XAU) is None


def test_briefing_goes_into_the_frame_only_when_the_runner_has_one():
    class _Runner:
        def __init__(self, result):
            self.result = result

        def get_briefing(self, symbol):
            if isinstance(self.result, Exception):
                raise self.result
            return self.result

    class _Briefing:
        def to_wire(self):
            return {"symbol": XAU}

    with_briefing, without, broken = {}, {}, {}
    _put_briefing(with_briefing, _Runner(_Briefing()), XAU)
    _put_briefing(without, _Runner(None), XAU)
    _put_briefing(broken, _Runner(RuntimeError("boom")), XAU)

    assert with_briefing == {"briefing": {"symbol": XAU}} and without == {} and broken == {}


def test_runner_passes_viewer_tf_atr_and_the_config_threshold_into_the_briefing(tmp_path, monkeypatch):
    import runtime.smc.smc_runner as smc_runner_mod

    seen = {}
    real = smc_runner_mod.compose_briefing

    def _spy(*args, **kwargs):
        seen.update(kwargs)
        return real(*args, **kwargs)

    monkeypatch.setattr(smc_runner_mod, "compose_briefing", _spy)
    runner = _runner(tmp_path)
    runner._full_config["smc"]["briefing"]["rr_min_risk_atr"] = 0.7

    runner.get_briefing(XAU)

    assert seen["rr_min_risk_atr"] == 0.7
    assert sorted(seen["atr_by_tf"]) == [900, 3600, 14400, 86400]
    assert all(atr == runner._narrative_atr(XAU, tf_s, 0.0) and atr > 0 for tf_s, atr in seen["atr_by_tf"].items())
