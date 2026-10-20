"""ADR-0115 S3: W1/MN у ws_server — увімкнення з config, мітки й меню TF, full-кадр без SMC, оновлення через D1."""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import pathlib
from concurrent.futures import ThreadPoolExecutor

from aiohttp import web

import runtime.ws.ws_server as ws_server
from core.calendar_tf import MN_S, W1_S
from runtime.i18n_catalog import load_catalog
from runtime.store.uds import UpdatesResult, WindowResult
from runtime.ws.app_keys import (
    APP_BOOT_ID,
    APP_CALENDAR_VIEW,
    APP_FULL_CONFIG,
    APP_HTF_ANCHOR_RULE_FOR_SYMBOL,
    APP_I18N_CATALOG,
    APP_PREVIEW_TF_SET,
    APP_SMC_RUNNER,
    APP_TF_ALLOWLIST,
    APP_UDS,
    APP_UDS_EXECUTOR,
)
from runtime.ws.ws_server import WsSession, _build_config_frame

REPO = pathlib.Path(__file__).resolve().parents[1]
SYM = "XAU/USD"
CAL_CFG = {"enabled": True, "source_tf_s": 86400, "tfs_s": [604800, 2592000],
           "cold_start_bars_by_tf": {"604800": 520, "2592000": 240},
           "scrollback_chunk_bars_by_tf": {"604800": 260, "2592000": 120}}


def _ms(y: int, m: int, d: int, h: int = 0) -> int:
    return int(dt.datetime(y, m, d, h, tzinfo=dt.timezone.utc).timestamp() * 1000)


def _d1(open_ms: int, c: float, complete: bool = True) -> dict:
    return {"open_time_ms": open_ms, "open": c - 1, "high": c + 1, "low": c - 2, "close": c, "volume": 1.0,
            "complete": complete, "tf_s": 86400}


# D1 пн–пт тижнів 05.10 і 12.10.2026 (відкриття напередодні 21:00 UTC)
ROWS = [_d1(_ms(2026, 10, d, 21), 100.0 + d) for d in (4, 5, 6, 7, 8, 11, 12, 13, 14, 15)]
UNTIL_TUESDAY = [r for r in ROWS if r["open_time_ms"] < _ms(2026, 10, 13, 21)]  # фінали до вт 13.10


class _D1Uds:
    def __init__(self, preview=None, events=None, rows=None):
        self.rows = ROWS if rows is None else rows
        self.preview = preview or []
        self.events = events or []

    def read_window(self, spec, policy):
        assert spec.tf_s == 86400, "W1/MN читаються лише через D1 (I1)"
        sel = [r for r in self.rows if (spec.since_open_ms is None or r["open_time_ms"] > spec.since_open_ms)
               and (spec.to_open_ms is None or r["open_time_ms"] <= spec.to_open_ms)]
        return WindowResult([dict(r) for r in sel[-spec.limit:]], {"source": "disk_range"}, [])

    def read_preview_window(self, symbol, tf_s, limit):
        return WindowResult(list(self.preview), {}, [])

    def read_updates(self, spec):
        return UpdatesResult(list(self.events), 5, None, None, None, 0, {}, [])


class _RunnerProbe:
    """SMC runner, який W1/MN не мають чіпати; розбір символу (ADR-0113) — дозволено."""

    def __init__(self):
        self.calls: list[str] = []

    def __getattr__(self, name):
        def _record(*_a, **_kw):
            self.calls.append(name)
            return None
        return _record


def _app(cal_cfg=CAL_CFG, uds=None, runner=None) -> web.Application:
    app = web.Application()
    app[APP_BOOT_ID] = "t"
    app[APP_FULL_CONFIG] = {"symbols": [SYM], "calendar_tfs": cal_cfg} if cal_cfg is not None else {"symbols": [SYM]}
    app[APP_TF_ALLOWLIST] = {60, 86400}
    app[APP_PREVIEW_TF_SET] = {60, 86400}
    app[APP_HTF_ANCHOR_RULE_FOR_SYMBOL] = lambda _s: "ny_close_us_dst"
    app[APP_UDS] = uds or _D1Uds()
    app[APP_UDS_EXECUTOR] = ThreadPoolExecutor(max_workers=1)
    if runner is not None:
        app[APP_SMC_RUNNER] = runner
    ws_server._init_calendar_view(app, app[APP_FULL_CONFIG])
    return app


class _FakeWs:
    closed = False

    def __init__(self):
        self.frames: list[dict] = []

    async def send_json(self, obj):
        self.frames.append(obj)


def test_labels_w1_mn_and_one_minute_unchanged():
    assert ws_server._TF_LABEL_TO_S["W1"] == W1_S and ws_server._TF_LABEL_TO_S["mn"] == MN_S
    assert ws_server._TF_S_TO_LABEL[MN_S] == "MN" and ws_server._TF_LABEL_TO_S["1m"] == 60


def test_enabled_view_adds_w1_mn_to_menu_after_d1():
    app = _app()
    assert APP_CALENDAR_VIEW in app and {W1_S, MN_S} <= app[APP_TF_ALLOWLIST]
    assert _build_config_frame(WsSession(None), app)["config"]["tfs"] == ["M1", "D1", "W1", "MN"]


def test_disabled_or_invalid_config_keeps_w1_mn_out():
    for cfg in (dict(CAL_CFG, enabled=False), dict(CAL_CFG, tfs_s=[3600]), None):
        app = _app(cal_cfg=cfg)
        assert APP_CALENDAR_VIEW not in app and app[APP_TF_ALLOWLIST] == {60, 86400}


def test_full_frame_w1_projects_d1_without_smc_with_forming_and_timer(monkeypatch):
    now_ms = _ms(2026, 10, 14, 15)  # ср тижня 12.10: пн–вт фінальні, середа формується
    monkeypatch.setattr(ws_server.time, "time", lambda: now_ms / 1000)
    wed = _d1(_ms(2026, 10, 13, 21), 200.0, complete=False)
    runner = _RunnerProbe()
    app = _app(uds=_D1Uds(preview=[wed], rows=UNTIL_TUESDAY), runner=runner)
    app[APP_CALENDAR_VIEW]._clock_ms = lambda: now_ms
    session = WsSession(_FakeWs())
    session.symbol, session.tf_s = SYM, W1_S
    asyncio.run(ws_server._send_full_frame(session, app))
    (frame,) = session.ws.frames
    assert frame["tf"] == "W1" and frame["zones"] == [] and frame["levels"] == []
    assert [c["t_ms"] for c in frame["candles"]] == [_ms(2026, 10, 4, 21), _ms(2026, 10, 11, 21)]
    assert frame["candles"][-1]["c"] == 200.0 and frame["candles"][-1]["h"] == 201.0
    assert frame["bar_close_ms"] == _ms(2026, 10, 16, 21)
    assert runner.calls == ["get_briefing"], "SMC на W1/MN не рахується (S1), розбір символу — так"


def test_updates_dispatch_reads_d1_events_and_projects_bucket(monkeypatch):
    now_ms = _ms(2026, 10, 14, 15)
    event = {"bar": dict(_d1(_ms(2026, 10, 13, 21), 300.0, complete=False)), "complete": False}
    app = _app(uds=_D1Uds(events=[event], rows=UNTIL_TUESDAY))
    app[APP_CALENDAR_VIEW]._clock_ms = lambda: now_ms
    result = asyncio.run(ws_server._uds_read_updates(app, SYM, W1_S, 4, False))
    assert result.cursor_seq == 5
    (ev,) = result.events
    assert ev["bar"]["open_time_ms"] == _ms(2026, 10, 11, 21) and ev["bar"]["close"] == 300.0


def test_repo_config_section_parses_and_gate_knows_adr():
    cfg = json.loads((REPO / "config.json").read_text(encoding="utf-8"))
    parsed = ws_server.calendar_view_config_from_cfg(cfg)
    assert parsed.tfs == {W1_S, MN_S} and parsed.cold_start_bars[W1_S] == 520
    from tools.exit_gates.gates.gate_adr_config_sync import _FEATURE_ADR_MAP, run_gate

    assert _FEATURE_ADR_MAP["calendar_tfs"] == "0115"
    assert run_gate({"root": str(REPO)})["ok"] is True


class _Briefing:
    def __init__(self, as_of_ms):
        self.as_of_ms = as_of_ms

    def to_wire(self):
        return {"symbol": SYM, "as_of_ms": self.as_of_ms, "sections": [{"key": "bias", "title": "Напрям", "rows": []}],
                "warnings": []}


class _BriefingRunner(_RunnerProbe):
    def __init__(self, as_of_ms=1):
        super().__init__()
        self.as_of_ms = as_of_ms

    def get_briefing(self, symbol, tr=None):
        self.calls.append("get_briefing")
        return _Briefing(self.as_of_ms)


def test_full_frame_w1_briefing_gets_profile_section(monkeypatch):
    now_ms = _ms(2026, 10, 14, 15)
    monkeypatch.setattr(ws_server.time, "time", lambda: now_ms / 1000)
    app = _app(uds=_D1Uds(rows=UNTIL_TUESDAY), runner=_BriefingRunner())
    app[APP_CALENDAR_VIEW]._clock_ms = lambda: now_ms
    session = WsSession(_FakeWs())
    session.symbol, session.tf_s = SYM, W1_S
    asyncio.run(ws_server._send_full_frame(session, app))
    (frame,) = session.ws.frames
    keys = [section["key"] for section in frame["briefing"]["sections"]]
    assert keys == ["bias", "calendar_profile"]
    assert frame["briefing"]["sections"][-1]["title"] == "Профіль тижня"


def test_full_frame_w1_in_english_has_english_profile(monkeypatch):
    """ADR-0116 Ф2.4: мова з'єднання доходить до calendar_view — розділ профілю англійською."""
    now_ms = _ms(2026, 10, 14, 15)
    monkeypatch.setattr(ws_server.time, "time", lambda: now_ms / 1000)
    app = _app(uds=_D1Uds(rows=UNTIL_TUESDAY), runner=_BriefingRunner())
    app[APP_I18N_CATALOG] = load_catalog()
    app[APP_CALENDAR_VIEW]._clock_ms = lambda: now_ms
    session = WsSession(_FakeWs())
    session.symbol, session.tf_s, session.lang = SYM, W1_S, "en"
    asyncio.run(ws_server._send_full_frame(session, app))
    (frame,) = session.ws.frames
    profile = frame["briefing"]["sections"][-1]
    assert frame["meta"]["lang"] == "en" and profile["key"] == "calendar_profile"
    assert profile["title"] == "Weekly profile" and profile["rows"][0]["text"].startswith("Week high so far set on ")


def test_delta_w1_resends_briefing_with_profile_only_when_it_changes(monkeypatch):
    now_ms = _ms(2026, 10, 14, 15)
    monkeypatch.setattr(ws_server.time, "time", lambda: now_ms / 1000)
    runner = _BriefingRunner(as_of_ms=100)
    app = _app(uds=_D1Uds(rows=UNTIL_TUESDAY), runner=runner)
    view = app[APP_CALENDAR_VIEW]
    view._clock_ms = lambda: now_ms
    sent: dict = {}
    first, second = {"meta": {}}, {"meta": {}}
    asyncio.run(ws_server._put_calendar_briefing_delta(app, view, SYM, W1_S, first, sent))
    asyncio.run(ws_server._put_calendar_briefing_delta(app, view, SYM, W1_S, second, sent))
    assert [s["key"] for s in first["briefing"]["sections"]] == ["bias", "calendar_profile"]
    assert "briefing" not in second, "той самий розбір удруге не шлеться"
    runner.as_of_ms = 200
    third = {"meta": {}}
    asyncio.run(ws_server._put_calendar_briefing_delta(app, view, SYM, W1_S, third, sent))
    assert third["briefing"]["as_of_ms"] == 200 and third["briefing"]["sections"][-1]["key"] == "calendar_profile"
