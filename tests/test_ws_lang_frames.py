"""Кадри WS і мова з'єднання (ADR-0116 Ф2.2). Головний інваріант — L4: з'єднання, що мови не просило, отримує байт у
байт ті самі кадри, що до мовного шару.

Golden-кадри в `tests/golden/ws_frames/` записано кодом main до Ф2.2 (той самий `runtime/` і `core/smc`, що в main,
`git diff main -- runtime/ core/smc` порожній, крім нового `runtime/i18n_catalog.py`). Справжні `_send_full_frame` і
`_global_delta_loop`; рушій — заглушка над справжнім текстовим шаром: наратив, картку стадії й розбір складають модулі
`core` з golden-входів наративу. Тож будь-яка зміна тексту чи кадру для глядача без мови падає тут.

Оновити golden — лише свідомо: `I18N_WS_GOLDEN_UPDATE=1 pytest tests/test_ws_lang_frames.py` і причина в коміті.
"""
from __future__ import annotations

import asyncio
import json
import os
import pathlib
from typing import Any, Dict, Optional

import pytest

import runtime.ws.ws_server as ws_server
from core.smc.briefing import compose_briefing, liquidity_section, session_section
from core.smc.narrative_inputs import NarrativeInputs
from core.smc.shell_composer import compose_shell_payload
from runtime.ws.app_keys import (
    APP_D1_TICK_RELAY_TFS,
    APP_DELTA_POLL_S,
    APP_FULL_CONFIG,
    APP_PREVIEW_TF_SET,
    APP_SMC_RUNNER,
    APP_WS_SESSIONS,
)

pytestmark = pytest.mark.asyncio

GOLDEN_DIR = pathlib.Path(__file__).parent / "golden" / "ws_frames"
NARRATIVE_DIR = pathlib.Path(__file__).parent / "golden" / "narrative"
UPDATE_GOLDEN = os.environ.get("I18N_WS_GOLDEN_UPDATE") == "1"
SYMBOL, M15, H4 = "XAU/USD", 900, 14400
AS_OF_MS = 1_790_000_100_000  # момент розбору заглушки — фіксований, щоб golden не залежав від годинника
# Мінливе між запусками (час, boot_id) і `meta.config` — список символів і TF з config.json: до мови не стосується, а
# golden інакше ламався б від кожного нового символу; однаковість config між мовами перевіряє окремий тест
VOLATILE_META = ("server_ts_ms", "boot_id", "config")

_BARS = [
    {"open": 4725.0 + i, "high": 4733.0 + i, "low": 4721.0 + i, "close": 4729.0 + i, "volume": 100.0 + i,
     "time": 1_790_000_000 + i * M15, "open_time_ms": (1_790_000_000 + i * M15) * 1000,
     "close_time_ms": (1_790_000_000 + (i + 1) * M15) * 1000}
    for i in range(3)
]
_CLOSED_BAR = {**_BARS[-1], "time": _BARS[-1]["time"] + M15, "open_time_ms": _BARS[-1]["open_time_ms"] + M15 * 1000,
               "close_time_ms": _BARS[-1]["close_time_ms"] + M15 * 1000, "complete": True}


class _Window:
    def __init__(self, bars):
        self.bars_lwc, self.warnings = bars, []


class _Updates:
    def __init__(self, events, cursor_seq):
        self.events, self.cursor_seq, self.warnings = events, cursor_seq, []


class _Uds:
    def read_window(self, spec, policy):
        return _Window(_BARS)

    def read_updates(self, spec):
        return _Updates([], 42)


def _inputs(name: str) -> NarrativeInputs:
    case = json.loads((NARRATIVE_DIR / ("%s.json" % name)).read_text(encoding="utf-8"))
    return NarrativeInputs.from_dict(case["inputs"])


class _TextRunner:
    """Рушій-заглушка: SMC порожній, а тексти складає справжній `core` з golden-входів (M15 — сетап, H4 — контекст)."""

    def __init__(self, config: Dict[str, Any]) -> None:
        self._config = config
        self._inputs = {M15: _inputs("xau_m15_trade_sell_approaching"), H4: _inputs("xau_h4_same_moment")}

    def get_snapshot(self, symbol, tf_s):
        return None

    def get_zone_grades(self, symbol, tf_s):
        return {}

    def get_bias_map(self, symbol):
        return dict(self._inputs[M15].bias_map)

    def get_momentum_map(self, symbol):
        return {}

    def get_pd_state(self, symbol, tf_s):
        return None

    def get_measured_atr(self, symbol, tf_s):
        return None

    def get_rv(self, symbol, tf_s):
        return None

    def get_narrative(self, symbol, tf_s, *args, **kwargs):
        inputs = self._inputs.get(tf_s)
        return None if inputs is None else inputs.synthesize()

    def get_signals(self, symbol, tf_s, narrative, price, atr):
        return [], []

    def get_shell_payload(self, symbol, tf_s, narrative, signal=None):
        return compose_shell_payload(narrative, self.get_bias_map(symbol), tf_s,
                                     self._config.get("smc", {}).get("shell", {}), sessions_active=True, signal=signal)

    def get_briefing(self, symbol):
        setup = self._inputs[M15]
        narratives = {tf_s: inputs.synthesize() for tf_s, inputs in self._inputs.items()}
        tail = (liquidity_section(M15, setup.snapshot.levels, {}, setup.current_price, setup.atr, setup.price_digits, 2),
                session_section(AS_OF_MS, "london", True, ("newyork", AS_OF_MS + 3 * 3_600_000)))
        smc_cfg = self._config.get("smc", {})
        return compose_briefing(symbol, AS_OF_MS, self.get_bias_map(symbol), narratives,
                                [int(tf_s) for tf_s in smc_cfg.get("shell", {}).get("strip_tfs", ())], [H4], [M15],
                                setup.price_digits, tail, atr_by_tf={M15: setup.atr},
                                rr_min_risk_atr=float(smc_cfg.get("briefing", {}).get("rr_min_risk_atr", 0.0)))

    def on_bar_dict(self, symbol, tf_s, bar):
        pass

    def last_delta(self, symbol, tf_s):
        return None

    def clear_delta(self, symbol, tf_s):
        pass

    def get_range_exhaustion(self, symbol):
        return None

    def get_display_levels_wire(self, symbol, tf_s):
        return None


class _FakeWs:
    def __init__(self) -> None:
        self.closed = False
        self.frames: list = []

    async def send_str(self, payload: str) -> None:
        self.frames.append(payload)

    async def send_json(self, obj: dict) -> None:
        self.frames.append(json.dumps(obj))

    async def close(self) -> None:
        self.closed = True


def _app():
    app = ws_server.build_app(config_path="config.json", uds=_Uds())
    app[APP_SMC_RUNNER] = _TextRunner(app[APP_FULL_CONFIG])
    app[APP_PREVIEW_TF_SET] = set()  # без формуючої свічки з preview: кадр — лише закриті бари
    app[APP_D1_TICK_RELAY_TFS] = set()
    app[APP_DELTA_POLL_S] = 0.001
    return app


def _session(app, name: str, lang: Optional[str] = None) -> ws_server.WsSession:
    session = ws_server.WsSession(_FakeWs())  # type: ignore[arg-type]
    session.client_id = name
    session.symbol, session.tf_s = SYMBOL, M15
    app[APP_WS_SESSIONS][name] = session
    return session


def _normalized(payload: str) -> str:
    """Кадр як байти, крім полів VOLATILE_META — їх замінено сталими."""
    frame = json.loads(payload)
    for key in VOLATILE_META:
        if key in frame.get("meta", {}):
            frame["meta"][key] = "<%s>" % key
    return json.dumps(frame, ensure_ascii=False, sort_keys=True, indent=1)


def _assert_golden(name: str, payload: str) -> None:
    path = GOLDEN_DIR / ("%s.json" % name)
    actual = _normalized(payload)
    if UPDATE_GOLDEN:
        GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(actual + "\n", encoding="utf-8", newline="\n")
    assert path.exists(), "немає golden %s — записати свідомо: I18N_WS_GOLDEN_UPDATE=1" % path
    assert actual == path.read_text(encoding="utf-8").rstrip("\n"), "кадр без мови змінився: %s" % name


async def _delta_after_closed_bar(app, *sessions):
    """Справжній _global_delta_loop: перше читання — adopt курсора (кадру нема), друге — закрита свічка, далі тиша."""
    served = {"done": False}

    async def _read(app_, symbol, tf_s, since_seq, include_preview):
        if since_seq is None or served["done"] or (symbol, tf_s) != (SYMBOL, M15):
            return _Updates([], 42)
        served["done"] = True
        return _Updates([{"bar": dict(_CLOSED_BAR), "complete": True}], 43)

    original = ws_server._uds_read_updates
    ws_server._uds_read_updates = _read
    task = asyncio.ensure_future(ws_server._global_delta_loop(app))
    try:
        for _ in range(2000):
            if all(any('"frame_type": "delta"' in f for f in s.ws.frames) for s in sessions):
                break
            await asyncio.sleep(0.002)
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        ws_server._uds_read_updates = original
    return [[f for f in s.ws.frames if '"frame_type": "delta"' in f][0] for s in sessions]


async def test_full_frame_without_lang_is_byte_identical_to_main():
    app = _app()
    session = _session(app, "no-lang")
    await ws_server._send_full_frame(session, app)
    assert len(session.ws.frames) == 1
    frame = json.loads(session.ws.frames[0])
    assert {"narrative", "shell", "briefing"} <= set(frame), "golden має нести всі текстові поля"
    _assert_golden("full_m15_no_lang", session.ws.frames[0])


async def test_delta_without_lang_is_byte_identical_to_main():
    app = _app()
    session = _session(app, "no-lang")
    (delta,) = await _delta_after_closed_bar(app, session)
    assert {"narrative", "shell", "briefing"} <= set(json.loads(delta))
    _assert_golden("delta_m15_no_lang", delta)
