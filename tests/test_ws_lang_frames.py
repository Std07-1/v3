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
from runtime.ws import lang_frames
from core.i18n import SOURCE_TRANSLATOR
from core.smc.briefing import compose_briefing, liquidity_section, session_section
from core.smc.narrative_inputs import NarrativeInputs
from core.smc.shell_composer import compose_shell_payload
from runtime.ws.app_keys import (
    APP_D1_TICK_RELAY_TFS,
    APP_DELTA_POLL_S,
    APP_FULL_CONFIG,
    APP_I18N_CATALOG,
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
        self.text_calls: list = []  # (метод, мова перекладача, record) — що просила мовна копія кадру

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

    def get_narrative(self, symbol, tf_s, *args, record=True, tr=SOURCE_TRANSLATOR, **kwargs):
        self.text_calls.append(("narrative", tr.lang, record))
        inputs = self._inputs.get(tf_s)
        return None if inputs is None else inputs.synthesize()

    def get_signals(self, symbol, tf_s, narrative, price, atr):
        return [], []

    def get_shell_payload(self, symbol, tf_s, narrative, signal=None, tr=SOURCE_TRANSLATOR):
        self.text_calls.append(("shell", tr.lang, None))
        return compose_shell_payload(narrative, self.get_bias_map(symbol), tf_s,
                                     self._config.get("smc", {}).get("shell", {}), sessions_active=True, signal=signal)

    def get_briefing(self, symbol, tr=SOURCE_TRANSLATOR):
        self.text_calls.append(("briefing", tr.lang, None))
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
    session.lang = lang
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


# ── Мова з'єднання (Ф2.2) ─────────────────────────────


def _comparable(payload: str) -> str:
    """Кадр без `meta.lang` — те, що мусить збігатися з кадром без мови, поки модулі тексту не перекладено."""
    frame = json.loads(_normalized(payload))
    frame["meta"].pop("lang", None)
    return json.dumps(frame, ensure_ascii=False, sort_keys=True)


async def test_requested_lang_normalizes_code_and_rejects_loudly():
    catalog = ws_server.load_catalog()
    assert lang_frames.requested_lang(None, catalog) == (None, None)
    assert lang_frames.requested_lang("", catalog) == (None, None)
    assert lang_frames.requested_lang("en-US", catalog) == ("en", None)
    assert lang_frames.requested_lang(" RU ", catalog) == ("ru", None)
    assert lang_frames.requested_lang("de", catalog) == (None, "lang_unsupported:de")
    # Сире значення від клієнта не йде ні в meta, ні в лог
    assert lang_frames.requested_lang("<script>", catalog) == (None, "lang_unsupported")


async def test_full_frame_lang_uk_is_no_lang_frame_plus_meta_lang():
    app = _app()
    plain, uk = _session(app, "plain"), _session(app, "uk", lang="uk")
    await ws_server._send_full_frame(plain, app)
    await ws_server._send_full_frame(uk, app)
    assert json.loads(uk.ws.frames[0])["meta"]["lang"] == "uk"
    assert _comparable(uk.ws.frames[0]) == _comparable(plain.ws.frames[0])


async def test_full_frame_lang_en_recomposes_texts_once_and_keeps_data():
    app = _app()
    runner = app[APP_SMC_RUNNER]
    plain, en = _session(app, "plain"), _session(app, "en", lang="en")
    await ws_server._send_full_frame(plain, app)
    runner.text_calls.clear()
    await ws_server._send_full_frame(en, app)
    en_frame, plain_frame = json.loads(en.ws.frames[0]), json.loads(plain.ws.frames[0])
    assert en_frame["meta"]["lang"] == "en"
    assert en_frame["meta"]["config"] == plain_frame["meta"]["config"]
    # Прохід джерела (журнал, сигнали) — один, з record; мовна копія — перекладачем en і без запису в журнал
    assert runner.text_calls.count(("narrative", "uk", True)) == 1
    assert {("narrative", "en", False), ("shell", "en", None), ("briefing", "en", None)} <= set(runner.text_calls)
    # Ф2.2 — проводка: модулі тексту ще не перекладено, тож копія en дає ті самі тексти й ті самі дані
    assert _comparable(en.ws.frames[0]) == _comparable(plain.ws.frames[0])


async def test_unsupported_lang_warns_in_first_full_frame_only_and_serves_source():
    app = _app()
    session = _session(app, "de")
    session.lang, session.lang_warning = lang_frames.requested_lang("de", app[APP_I18N_CATALOG])
    await ws_server._send_full_frame(session, app)
    await ws_server._send_full_frame(session, app)
    first, second = (json.loads(f) for f in session.ws.frames)
    assert "lang_unsupported:de" in first["meta"]["warnings"] and "lang" not in first["meta"]
    assert "lang_unsupported:de" not in second["meta"].get("warnings", [])
    # Далі — кадр як без мови (seq другого кадру сесії — 2, golden записано першим кадром)
    _assert_golden("full_m15_no_lang", json.dumps(dict(second, meta=dict(second["meta"], seq=1))))


async def test_set_lang_action_sends_fresh_full_frame_in_new_language():
    app = _app()
    session = _session(app, "switcher")
    await ws_server._handle_set_lang(session, {"lang": "ru"}, app)
    frame = json.loads(session.ws.frames[-1])
    assert session.lang == "ru" and frame["frame_type"] == "full" and frame["meta"]["lang"] == "ru"


async def test_delta_gives_each_language_group_its_frame_and_no_lang_stays_golden():
    app = _app()
    runner = app[APP_SMC_RUNNER]
    plain, uk, en = _session(app, "plain"), _session(app, "uk", lang="uk"), _session(app, "en", lang="en")
    plain_delta, uk_delta, en_delta = await _delta_after_closed_bar(app, plain, uk, en)
    _assert_golden("delta_m15_no_lang", plain_delta)
    assert json.loads(uk_delta)["meta"]["lang"] == "uk" and json.loads(en_delta)["meta"]["lang"] == "en"
    assert _comparable(uk_delta) == _comparable(plain_delta) == _comparable(en_delta)
    # SMC, сигнали й журнал — один прохід джерела на ціль; мовою en — лише текст
    assert runner.text_calls.count(("narrative", "uk", True)) == 1
    assert ("narrative", "en", False) in runner.text_calls


async def test_delta_without_text_fields_is_one_shared_frame():
    frame = {"type": "render_frame", "frame_type": "delta", "candles": [], "meta": {"seq": 0}}
    sessions = [ws_server.WsSession(_FakeWs()), ws_server.WsSession(_FakeWs())]  # type: ignore[arg-type]
    sessions[1].lang = "en"
    ctx = lang_frames.TextContext(SYMBOL, M15)
    variants = await lang_frames.frames_by_lang(frame, sessions, None, None, ctx, None, ws_server._put_calendar_profile)
    assert len(variants) == 1 and variants[0][0] is frame and variants[0][1] == sessions


async def test_ws_query_lang_reaches_full_frame(aiohttp_client):
    """Справжнє підключення `/ws?lang=en`: мова з адреси доходить до meta повного кадру."""
    client = await aiohttp_client(ws_server.build_app(config_path="config.json", uds=_Uds()))
    ws = await client.ws_connect("/ws?lang=en")
    try:
        for _ in range(10):
            frame = json.loads((await asyncio.wait_for(ws.receive(), timeout=5)).data)
            if frame.get("frame_type") == "full":
                assert frame["meta"]["lang"] == "en"
                return
        raise AssertionError("повного кадру не дочекались")
    finally:
        await ws.close()
