"""Споживачі ATR беруть виміряне рушієм, а не вгадують заглушку порогом 1.0 (ADR-0070 rev 3, продовження).

get_atr лишився дільником (1.0, коли даних нема), тож WakeEngine, /api/context і наратив відсіювали заглушку
порогом `atr > 1.0` / `atr <= 1.0`. Справжній ATR XAG/USD менший за 1.0 (прод 27.09: M5 0.0717, M15 0.1623,
H1 0.5166, H4 0.8059, D1 2.4656) — поріг його відкидав: WakeEngine брав D1 замість H4 (proximity/volatility у
~3 рази грубіші), /api/context віддавав лише D1, наратив M5..H4 підміняв ATR на h−l одного бару. Тепер ознака
«не виміряно» — None від get_measured_atr; значення < 1.0 — такий самий вимір, як 45.0 у XAU.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import logging
from types import SimpleNamespace
from typing import Dict, Optional

import pytest

import runtime.smc.smc_runner as smc_runner_mod
import runtime.ws.ws_server as ws_server
from core.model.bars import CandleBar
from core.smc.config import SmcConfig
from core.smc.engine import SmcEngine
from core.smc.narrative import _fallback_narrative_block
from core.smc.wake_check import accumulator_tick
from core.smc.wake_types import AwarenessAccumulator, WakeCondition, WakeConditionKind
from runtime.smc.smc_runner import SmcRunner
from runtime.smc.wake_engine import WakeEngine
from runtime.ws.app_keys import APP_SMC_RUNNER
from test_ws_htf_forming_seed import _build_app

XAG = "XAG/USD"
M3_S, M15_S, M30_S, H1_S, H4_S, D1_S = 180, 900, 1800, 3600, 14400, 86400
# Живий прод 27.09 (read-only зонд HUD): XAG — єдиний символ з ATR < 1.0 на M5..H4
XAG_ATR = {300: 0.0717, M15_S: 0.1623, H1_S: 0.5166, H4_S: 0.8059, D1_S: 2.4656}


# ── Акумулятор пробудження (чиста функція) ─────────────────────────────────


def test_accumulator_normalizes_move_by_measured_atr_below_one():
    acc = accumulator_tick(AwarenessAccumulator(), 62.05, 62.00, XAG_ATR[H4_S], ts=1000.0)

    assert acc.score == pytest.approx(0.05 / XAG_ATR[H4_S])  # не сирі 0.05 пункта


def test_accumulator_without_measured_atr_does_not_count_raw_points():
    acc = accumulator_tick(
        AwarenessAccumulator(), 4250.0, 4200.0, 0.0, session_events=["london_open"], ts=1000.0
    )

    assert acc.score == pytest.approx(1.0)  # лише бонус сесії: 50 сирих пунктів XAU непорівнянні зі score


# ── WakeEngine: опорний ATR ────────────────────────────────────────────────


class _Redis:
    def __init__(self) -> None:
        self.pushed: list[tuple[str, str]] = []

    def lpush(self, key, value):
        self.pushed.append((key, value))

    def ltrim(self, key, start, end):
        pass

    def get(self, key):
        return None


class _WakeRunner:
    """SmcRunner-фейк для WakeEngine: ціна + виміряний ATR по TF, SMC-структури порожні."""

    def __init__(self, atr_by_tf: Dict[int, Optional[float]], price: float = 62.0) -> None:
        self.atr_by_tf = atr_by_tf
        self.price = price

    def get_last_price(self, symbol):
        return self.price

    def get_measured_atr(self, symbol, tf_s):
        return self.atr_by_tf.get(tf_s)

    def get_snapshot(self, symbol, tf_s):
        return None

    def get_bias_map(self, symbol):
        return {}

    def get_zone_grades(self, symbol, tf_s):
        return None

    def get_recent_structure_events(self, symbol, since_ts_ms=0):
        return []

    def get_recent_bar_closes(self, symbol, since_ts_ms=0):
        return []


def _wake_engine(runner: _WakeRunner, redis: Optional[_Redis] = None) -> WakeEngine:
    return WakeEngine(
        redis_client=redis or _Redis(),
        namespace="t",
        executor=concurrent.futures.ThreadPoolExecutor(max_workers=1),
        smc_runner=runner,
        symbols=[XAG],
        config={"event_cooldown_s": {"_default": 600}},
    )


def _tick(engine: WakeEngine, ts_ms: int) -> None:
    async def drive():
        await engine._tick_symbol(XAG, ts_ms, asyncio.get_running_loop())

    asyncio.run(drive())


@pytest.mark.parametrize(
    "atr_by_tf,expected",
    [
        pytest.param(XAG_ATR, XAG_ATR[H4_S], id="h4_below_one_not_d1"),
        pytest.param({**XAG_ATR, H4_S: None}, XAG_ATR[H1_S], id="h1_when_h4_unmeasured"),
    ],
)
def test_wake_reference_atr_is_first_measured_h4_h1_d1(atr_by_tf, expected):
    assert _wake_engine(_WakeRunner(atr_by_tf))._reference_atr(XAG) == pytest.approx(expected)


def test_wake_tick_normalizes_xag_move_by_h4_atr():
    runner = _WakeRunner(XAG_ATR, price=62.00)
    engine = _wake_engine(runner)
    _tick(engine, 1_000_000)
    runner.price = 62.05
    _tick(engine, 1_002_000)

    assert engine._accumulators[XAG].score == pytest.approx(0.05 / XAG_ATR[H4_S], rel=1e-3)


def test_wake_without_measured_atr_keeps_bot_conditions_holds_cooldown_and_logs_once(caplog):
    redis = _Redis()
    runner = _WakeRunner({}, price=62.00)
    engine = _wake_engine(runner, redis)
    engine._bot_conditions[XAG] = [
        WakeCondition(WakeConditionKind.PRICE_CROSS, {"level": 70.0, "direction": "below"}, "нижче 70", "bot")
    ]

    with caplog.at_level(logging.WARNING, logger="runtime.smc.wake_engine"):
        _tick(engine, 1_000_000)
        runner.price = 63.00  # рух, який без ATR не оцінити як «значущий»
        _tick(engine, 1_002_000)

    assert len(redis.pushed) == 1  # бот-умова спрацювала без ATR; повтор у кулдауні придушено за часом
    assert len([r for r in caplog.records if "WAKE_ATR_UNMEASURED" in r.getMessage()]) == 1


# ── /api/context: ATR-карта ────────────────────────────────────────────────


class _ContextRunner:
    """Runner для /api/context: лише ціна і виміряний ATR; решта полів контексту падає у warnings."""

    _engine = SimpleNamespace(_states={}, _session_m1_bars={})
    _compute_tfs: set = set()

    def warmup(self, uds) -> None:
        return None

    def get_last_price(self, symbol):
        return 62.0

    def get_measured_atr(self, symbol, tf_s):
        return XAG_ATR.get(tf_s)


def test_context_atr_map_keeps_measured_values_below_one():
    atr_map, warnings = ws_server._context_atr_map(_ContextRunner(), XAG, M15_S)

    assert atr_map == {"M15": 0.16, "H4": 0.81, "D1": 2.47}
    assert warnings == []


def test_context_atr_map_names_unmeasured_tf_instead_of_dropping_silently():
    atr_map, warnings = ws_server._context_atr_map(_ContextRunner(), XAG, M3_S)

    assert atr_map == {"H4": 0.81, "D1": 2.47}
    assert warnings == ["atr_unavailable: M3"]


@pytest.mark.asyncio
async def test_api_context_serves_xag_atr_below_one(tmp_path, aiohttp_client):
    app = _build_app(tmp_path)
    app[APP_SMC_RUNNER] = _ContextRunner()
    client = await aiohttp_client(app)

    ctx = await (await client.get("/api/context", params={"symbol": XAG, "tf": "H1"})).json()

    assert ctx["atr"] == {"H1": 0.52, "H4": 0.81, "D1": 2.47}
    assert not [w for w in ctx.get("warnings", []) if w.startswith("atr")]


# ── Наратив: ATR фільтра цілей ─────────────────────────────────────────────


def _xag_bars(tf_s: int, count: int = 30) -> list[CandleBar]:
    step_ms = tf_s * 1000
    base_ms = 1_789_000_000_000 // step_ms * step_ms
    return [
        CandleBar(XAG, tf_s, base_ms + i * step_ms, base_ms + (i + 1) * step_ms,
                  62.0, 62.1, 61.9, 62.05, 100.0, True, "history")
        for i in range(count)
    ]


@pytest.fixture
def narrative_runner(tmp_path, monkeypatch):
    """SmcRunner з виміряним XAG M15; synthesize_narrative перехоплено — тест дивиться, який ATR він отримав."""
    engine = SmcEngine(SmcConfig())
    engine.update(XAG, M15_S, _xag_bars(M15_S))
    cfg = {
        "symbols": [XAG],
        "smc": {
            "compute_tfs": [M15_S],
            "narrative": {"enabled": True},
            "signal_journal": {"path": str(tmp_path)},
        },
    }
    runner = SmcRunner(cfg, engine)
    runner._warmup_done = True
    runner._journal = SimpleNamespace(record=lambda *args, **kwargs: None)
    seen_atr: list[float] = []

    def fake_synthesize(snap, bias, grades, momentum, viewer_tf_s, price, atr, cfg, **kwargs):
        seen_atr.append(atr)
        return _fallback_narrative_block(["captured"])

    monkeypatch.setattr(smc_runner_mod, "synthesize_narrative", fake_synthesize)
    return runner, seen_atr


def test_narrative_uses_measured_atr_below_one_not_single_bar_estimate(narrative_runner):
    runner, seen_atr = narrative_runner

    runner.get_narrative(XAG, M15_S, 62.05, 0.45)  # 0.45 — h−l одного бару від ws_server

    assert seen_atr == [pytest.approx(runner._engine.get_measured_atr(XAG, M15_S))]
    assert seen_atr[0] < 1.0


def test_narrative_on_unmeasured_viewer_tf_keeps_fallback_and_names_source_once(narrative_runner, caplog):
    runner, seen_atr = narrative_runner

    with caplog.at_level(logging.INFO, logger=smc_runner_mod._log.name):
        runner.get_narrative(XAG, M30_S, 62.05, 0.37)
        runner.get_narrative(XAG, M30_S, 62.05, 0.37)
    runner.get_narrative(XAG, M3_S, 62.05, 0.0)

    assert seen_atr == [0.37, 0.37, 1.0]  # M30/M3 рушій не вимірює: як і раніше — оцінка, без неї дільник
    logged = [r.getMessage() for r in caplog.records if "NARRATIVE_ATR_UNMEASURED" in r.getMessage()]
    assert len(logged) == 1 and "source=caller_estimate" in logged[0]
