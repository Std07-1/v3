"""HUD (CommandRail) показує лише виміряні ATR/RV — без заглушки 1.0 (ADR-0070 rev 3).

Прод 27.09: XAU/USD M30 — HUD «ATR 1.00 · RV 1.00x». Зонд по всіх full-кадрах: 21 з 56 пар символ×TF (усі 7
символів на M1/M3/M30 — TF поза compute_tfs) несли atr = rv = 1.0 рівно. SmcEngine для цих TF стану не має,
get_atr/get_rv віддавали нейтральну заглушку, а UI показував її як виміряну величину (I5/X28). Тепер 1.0
лишився тільки дільником (get_atr для distance/atr); показ бере get_measured_atr/get_rv → None → поля в кадрі
нема, meta.warnings каже чому, UI — «—».
"""
from __future__ import annotations

import dataclasses
import logging
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from typing import Optional

import pytest
from aiohttp import web

import runtime.ws.ws_server as ws_server
from core.model.bars import CandleBar
from core.smc.config import SmcConfig
from core.smc.engine import SmcEngine
from core.smc.swings import compute_atr, compute_rv
from runtime.ws.app_keys import APP_BOOT_ID, APP_SMC_RUNNER, APP_UDS, APP_UDS_EXECUTOR

SYM = "XAU/USD"
M15_S = 900
M30_S = 1800
BASE_MS = 1_789_000_000_000 // 1_800_000 * 1_800_000
UNAVAILABLE = {"atr_unavailable", "rv_unavailable"}


def _bars(count: int, *, tf_s: int = M15_S, volume: float = 100.0) -> list[CandleBar]:
    step_ms = tf_s * 1000
    return [
        CandleBar(symbol=SYM, tf_s=tf_s, open_time_ms=BASE_MS + i * step_ms, close_time_ms=BASE_MS + (i + 1) * step_ms,
                  o=100.0 + i, h=102.0 + i, low=99.0 + i, c=101.0 + i, v=volume, complete=True, src="history")
        for i in range(count)
    ]


# ── core: виміряне проти дільника ──────────────────────────────────────────


def test_engine_tf_without_state_is_unmeasured_while_divisor_stays_one():
    engine = SmcEngine(SmcConfig())
    engine.update(SYM, M15_S, _bars(30))

    assert engine.get_measured_atr(SYM, M30_S) is None
    assert engine.get_rv(SYM, M30_S) is None
    assert engine.get_atr(SYM, M30_S) == 1.0  # distance / atr у proximity — контракт дільника незмінний


def test_engine_computed_tf_reports_measured_atr_and_real_neutral_rv():
    engine = SmcEngine(SmcConfig())
    bars = _bars(30)
    engine.update(SYM, M15_S, bars)

    measured = engine.get_measured_atr(SYM, M15_S)
    assert measured == pytest.approx(compute_atr(bars, period=14))
    assert engine.get_atr(SYM, M15_S) == measured
    assert engine.get_rv(SYM, M15_S) == pytest.approx(1.0)  # рівні обсяги: 1.0 справді виміряне


@pytest.mark.parametrize(
    "bars",
    [
        pytest.param(_bars(20), id="fewer_than_period_plus_one"),
        pytest.param(_bars(21, volume=0.0), id="zero_volume"),
    ],
)
def test_compute_rv_without_measurement_is_none(bars):
    assert compute_rv(bars) is None


def test_compute_rv_measures_last_bar_against_prior_average():
    bars = _bars(21)
    bars[-1] = dataclasses.replace(bars[-1], v=300.0)
    assert compute_rv(bars) == pytest.approx(3.0)


# ── ws: кадр несе лише виміряне ────────────────────────────────────────────


class _Runner:
    """SMC runner-фейк: ATR/RV задані, решта SMC порожня."""

    def __init__(self, atr: Optional[float], rv: Optional[float]) -> None:
        self._atr = atr
        self._rv = rv

    def get_measured_atr(self, symbol, tf_s):
        return self._atr

    def get_rv(self, symbol, tf_s):
        return self._rv

    def get_snapshot(self, symbol, tf_s):
        return None

    def get_bias_map(self, symbol):
        return None

    def get_momentum_map(self, symbol):
        return None

    def get_pd_state(self, symbol, tf_s):
        return None

    def get_narrative(self, *args):
        return None


class _BrokenAtrRunner(_Runner):
    def get_measured_atr(self, symbol, tf_s):
        raise RuntimeError("engine state torn")


class _FakeWs:
    def __init__(self) -> None:
        self.closed = False
        self.frames: list[dict] = []

    async def send_json(self, obj: dict) -> None:
        self.frames.append(obj)


class _OneBarUds:
    def read_window(self, spec, policy):
        open_ms = BASE_MS
        bar = {"time": open_ms // 1000, "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 3.0,
               "open_time_ms": open_ms, "close_time_ms": open_ms + spec.tf_s * 1000, "tf_s": spec.tf_s,
               "src": "history", "complete": True}
        return SimpleNamespace(bars_lwc=[bar], warnings=[])


async def _full_frame(runner: _Runner, tf_s: int) -> dict:
    app = web.Application()
    app[APP_UDS] = _OneBarUds()
    app[APP_UDS_EXECUTOR] = ThreadPoolExecutor(max_workers=1)
    app[APP_BOOT_ID] = "t"
    app[APP_SMC_RUNNER] = runner
    session = ws_server.WsSession(_FakeWs())  # type: ignore[arg-type]
    session.symbol, session.tf_s = SYM, tf_s
    try:
        await ws_server._send_full_frame(session, app)
    finally:
        app[APP_UDS_EXECUTOR].shutdown(wait=True)
    return session.ws.frames[-1]  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_full_frame_on_unmeasured_tf_omits_atr_rv_and_warns():
    frame = await _full_frame(_Runner(atr=None, rv=None), M30_S)

    assert "atr" not in frame and "rv" not in frame
    assert UNAVAILABLE <= set(frame["meta"]["warnings"])


@pytest.mark.asyncio
async def test_full_frame_on_computed_tf_carries_measured_values_without_warnings():
    frame = await _full_frame(_Runner(atr=5.83, rv=0.14), M15_S)

    assert (frame["atr"], frame["rv"]) == (5.83, 0.14)
    assert not UNAVAILABLE & set(frame["meta"].get("warnings", []))


def test_hud_volatility_accessor_error_is_loud_not_a_stub(monkeypatch, caplog):
    monkeypatch.setattr(ws_server, "_HUD_UNAVAILABLE_LOGGED", set())
    with caplog.at_level(logging.INFO, logger=ws_server._log.name):
        atr, rv, warnings = ws_server._hud_volatility(_BrokenAtrRunner(atr=None, rv=0.5), SYM, M15_S)

    assert (atr, rv, warnings) == (None, 0.5, ["atr_unavailable"])
    [record] = caplog.records
    assert record.levelno == logging.WARNING
    assert "field=atr" in record.getMessage() and "engine state torn" in record.getMessage()


def test_hud_volatility_logs_on_transition_not_every_frame(monkeypatch, caplog):
    monkeypatch.setattr(ws_server, "_HUD_UNAVAILABLE_LOGGED", set())
    unmeasured, measured = _Runner(atr=None, rv=0.9), _Runner(atr=4.2, rv=0.9)
    with caplog.at_level(logging.INFO, logger=ws_server._log.name):
        for runner in (unmeasured, unmeasured, measured, unmeasured):
            ws_server._hud_volatility(runner, SYM, M30_S)

    logged = [r for r in caplog.records if "WS_HUD_UNAVAILABLE" in r.getMessage()]
    assert [r.levelno for r in logged] == [logging.INFO, logging.INFO]  # втрата → відновлення → знову втрата
