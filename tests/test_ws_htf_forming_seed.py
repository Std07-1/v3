"""Формуюча H4/D1 у ws_server на сезонній сітці символу (ADR-0095 S9a).

Три місця, де ws_server сам рахує бакет формуючої свічки, — сід tick-relay, fallback relay і
`/api/context` h4_forming — беруть `htf_bucket_start_ms` за правилом символу з резолвера `build_app`,
а не статичний `resolve_anchor_offset_ms`: улітку H4 = 21/01/05/.., після 01.11 — 22/02/06/..,
обрубок DST-доби (Нд 01.11 21:00) — окремий бакет. Помилка relay — WARNING із троттлінгом.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import tempfile
import time as real_time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from aiohttp import web

import runtime.ws.ws_server as ws_server
from core.config_loader import htf_anchor_rule_resolver
from core.model.bars import CandleBar
from core.session_anchor import H4_S, RULE_NY_CLOSE_US_DST
from preview_ring_fake import PreviewRingFakeRedis
from runtime.store.layers.redis_layer import RedisLayer
from runtime.store.redis_keys import preview_curr_key
from runtime.store.uds import UnifiedDataStore, _NullDiskLayer
from runtime.ws.app_keys import (
    APP_BOOT_ID,
    APP_D1_TICK_RELAY_TFS,
    APP_DELTA_POLL_S,
    APP_HTF_ANCHOR_RULE_FOR_SYMBOL,
    APP_PREVIEW_TF_SET,
    APP_SMC_RUNNER,
    APP_SYMBOLS_SET,
    APP_TF_ALLOWLIST,
    APP_TICK_REDIS_CLIENT,
    APP_TICK_REDIS_NS,
    APP_UDS,
    APP_UDS_EXECUTOR,
    APP_WS_SESSIONS,
)
from tools.exit_gates.gates.gate_ui_live_candle_plane import _check_overlay_anchor_sentinel

_REPO_ROOT = Path(__file__).resolve().parents[1]
XAU = "XAU/USD"
NS = "t"
M1_MS = 60_000
_CFG = {
    "symbols": [XAU, "HKG33"],
    "market_calendar_symbol_groups": {XAU: "cfd_us_22_23", "HKG33": "cfd_hk_main"},
    "htf_anchor": {"rule_by_calendar_group": {"cfd_us_22_23": RULE_NY_CLOSE_US_DST}},
}


def _utc_ms(*args: int) -> int:
    return int(dt.datetime(*args, tzinfo=dt.timezone.utc).timestamp() * 1000)


def _app_with_resolver() -> web.Application:
    app = web.Application()
    app[APP_HTF_ANCHOR_RULE_FOR_SYMBOL] = htf_anchor_rule_resolver(_CFG)
    return app


def _h4_open_hours(app: web.Application, day_start_ms: int) -> set[int]:
    opens = {
        ws_server._htf_bucket_open_ms(app, XAU, H4_S, day_start_ms + step * 30 * M1_MS)
        for step in range(48)
    }
    return {dt.datetime.fromtimestamp(o / 1000, dt.timezone.utc).hour for o in opens}


# (now/tick, очікуване відкриття H4): літо; обрубок осінньої DST-доби; перша зимова доба
_SCENARIOS = [
    pytest.param(_utc_ms(2026, 7, 1, 23, 30), _utc_ms(2026, 7, 1, 21), id="summer_2100"),
    pytest.param(_utc_ms(2026, 11, 1, 21, 30), _utc_ms(2026, 11, 1, 21), id="fall_stub_sun_2100"),
    pytest.param(_utc_ms(2026, 11, 2, 3, 30), _utc_ms(2026, 11, 2, 2), id="winter_0200"),
]


# ── Бакет на сезонній сітці ────────────────────────────────────────────


def test_htf_bucket_open_ms_h4_summer_grid_21_01_05():
    assert _h4_open_hours(_app_with_resolver(), _utc_ms(2026, 7, 1)) == {21, 1, 5, 9, 13, 17}


def test_htf_bucket_open_ms_h4_after_2026_11_01_winter_grid_22_02_06():
    assert _h4_open_hours(_app_with_resolver(), _utc_ms(2026, 11, 3)) == {22, 2, 6, 10, 14, 18}


def test_htf_bucket_open_ms_without_resolver_raises():
    with pytest.raises(ValueError, match="anchor_rule_missing"):
        ws_server._htf_bucket_open_ms(web.Application(), XAU, H4_S, _utc_ms(2026, 7, 1, 23))


def test_htf_bucket_open_ms_unmeasured_group_raises():
    with pytest.raises(ValueError, match="HTF_ANCHOR_GROUP_UNMEASURED"):
        ws_server._htf_bucket_open_ms(_app_with_resolver(), "HKG33", H4_S, _utc_ms(2026, 7, 1, 23))


# ── build_app: резолвер один на процес ─────────────────────────────────


class _StubUds:
    """build_app(uds=...) пропускає автоініціалізацію UDS."""

    def read_window(self, spec, policy):
        raise AssertionError("тест не читає вікно")


def _build_app(tmp_path: Path, **overrides) -> web.Application:
    cfg = json.loads((_REPO_ROOT / "config.json").read_text(encoding="utf-8"))
    cfg["redis"] = dict(cfg["redis"], enabled=False)  # без живого Redis: tick_price не читається
    cfg["smc"] = dict(cfg.get("smc", {}), enabled=False)  # runner-заглушка ставиться тестом
    cfg.update(overrides)
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
    return ws_server.build_app(config_path=str(cfg_path), uds=_StubUds())


def test_build_app_wires_htf_anchor_rule_per_symbol(tmp_path, caplog):
    with caplog.at_level(logging.INFO, logger=ws_server._log.name):
        app = _build_app(tmp_path)
    assert app[APP_HTF_ANCHOR_RULE_FOR_SYMBOL](XAU) == RULE_NY_CLOSE_US_DST
    assert any("WS_HTF_ANCHOR_WIRED" in r.getMessage() and XAU in r.getMessage() for r in caplog.records)


def test_build_app_without_htf_anchor_degrades_loud(tmp_path, caplog):
    with caplog.at_level(logging.WARNING, logger=ws_server._log.name):
        app = _build_app(tmp_path, htf_anchor={})
    assert any(
        r.levelno == logging.ERROR and "WS_HTF_ANCHOR_RULES_UNAVAILABLE" in r.getMessage() for r in caplog.records
    )
    with pytest.raises(ValueError, match="CONFIG_HTF_ANCHOR_MISSING"):
        app[APP_HTF_ANCHOR_RULE_FOR_SYMBOL](XAU)


# ── /api/context h4_forming ────────────────────────────────────────────


class _FrozenClock:
    """Замість модуля time у ws_server: time() заморожено, решта — справжня."""

    def __init__(self, now_ms: int) -> None:
        self._now_s = now_ms / 1000

    def time(self) -> float:
        return self._now_s

    def __getattr__(self, name: str):
        return getattr(real_time, name)


class _RunnerStub:
    """SMC runner лише з M1 сесії; решта методів відсутня — /api/context кладе їх у warnings."""

    _compute_tfs: set = set()

    def __init__(self, m1_bars: list[CandleBar]) -> None:
        self._engine = type("E", (), {"_session_m1_bars": {XAU: deque(m1_bars)}, "_states": {}})()

    def get_last_price(self, symbol: str) -> float:
        return 0.0

    def warmup(self, uds) -> None:
        return None


def _m1(open_ms: int, o: float, h: float, low: float, c: float) -> CandleBar:
    return CandleBar(XAU, 60, open_ms, open_ms + M1_MS, o, h, low, c, 1.0, True, "history")


@pytest.mark.asyncio
@pytest.mark.parametrize("now_ms,h4_open_ms", _SCENARIOS)
async def test_api_context_h4_forming_on_season_grid(tmp_path, monkeypatch, aiohttp_client, now_ms, h4_open_ms):
    app = _build_app(tmp_path)
    m1_bars = [
        _m1(h4_open_ms - M1_MS, 1.0, 1.0, 1.0, 1.0),  # попередній бакет — не входить
        _m1(h4_open_ms, 10.0, 12.0, 9.0, 11.0),
        _m1(now_ms - M1_MS, 11.0, 15.0, 10.5, 14.0),
    ]
    app[APP_SMC_RUNNER] = _RunnerStub(m1_bars)
    monkeypatch.setattr(ws_server, "time", _FrozenClock(now_ms))
    client = await aiohttp_client(app)
    resp = await client.get("/api/context", params={"symbol": XAU, "tf": "H4"})
    ctx = await resp.json()

    assert not [w for w in ctx.get("warnings", []) if w.startswith("h4_forming")]
    forming = ctx["h4_forming"]
    assert forming["open_ms"] == h4_open_ms
    assert (forming["o"], forming["h"], forming["l"], forming["c"]) == (10.0, 15.0, 9.0, 14.0)
    assert forming["m1_count"] == 2


# ── tick-relay: сід формуючої через справжній _global_delta_loop ───────


class _FakeWs:
    def __init__(self) -> None:
        self.closed = False
        self.frames: list[dict] = []

    async def send_str(self, payload: str) -> None:
        self.frames.append(json.loads(payload))

    async def send_json(self, obj: dict) -> None:
        self.frames.append(obj)


TICK_MID = 4350.0
# O/H/L формуючого бару preview-площини — відмінні від ціни тіку, щоб сід було видно
_PREVIEW_OHL = (4300.0, 4400.0, 4200.0)


def _put_preview_curr(ring: PreviewRingFakeRedis, symbol: str, tf_s: int, open_ms: int) -> None:
    """preview:curr бакета `open_ms` — як його пише HTF-акумулятор tick_preview_worker (ADR-0044)."""
    o, h, low = _PREVIEW_OHL
    bar = {"open_ms": open_ms, "close_ms": open_ms + tf_s * 1000 - 1, "o": o, "h": h, "l": low, "c": 4310.0, "v": 5.0}
    payload = {"v": 1, "symbol": symbol, "tf_s": tf_s, "bar": bar, "complete": False, "source": "htf_preview"}
    ring.set(preview_curr_key(NS, symbol, tf_s), json.dumps(payload))


async def _relay_frames(
    tmp: str,
    tick_ts_ms: int,
    *,
    symbol: str = XAU,
    tf_s: int = H4_S,
    preview_curr_open_ms: int | None = None,
) -> list[dict]:
    ring = PreviewRingFakeRedis()
    ring.seed_ring(NS, symbol, tf_s, 1_000, 10, retain=100)  # кільце тихе → relay-гілка
    if preview_curr_open_ms is not None:
        _put_preview_curr(ring, symbol, tf_s, preview_curr_open_ms)
    tick_redis = PreviewRingFakeRedis()
    tick_key = f"{NS}:tick:last:{symbol.replace('/', '_')}"
    tick_redis.kv[tick_key] = json.dumps({"mid": TICK_MID, "tick_ts_ms": tick_ts_ms}).encode()
    app = _app_with_resolver()
    app[APP_UDS] = UnifiedDataStore(
        data_root=tmp,
        boot_id="t",
        tf_allowlist={tf_s},
        min_coldload_bars={},
        role="reader",
        redis_layer=RedisLayer(ring, NS),
        disk_layer=_NullDiskLayer(),
        preview_tf_allowlist={tf_s},
        preview_updates_retain=100,
    )
    app[APP_DELTA_POLL_S] = 0.001
    app[APP_PREVIEW_TF_SET] = {tf_s}
    app[APP_SYMBOLS_SET] = {symbol}
    app[APP_TF_ALLOWLIST] = {tf_s}
    app[APP_D1_TICK_RELAY_TFS] = {tf_s}
    app[APP_TICK_REDIS_CLIENT] = tick_redis
    app[APP_TICK_REDIS_NS] = NS
    app[APP_UDS_EXECUTOR] = ThreadPoolExecutor(max_workers=2)
    app[APP_BOOT_ID] = "t"
    viewer = ws_server.WsSession(_FakeWs())  # type: ignore[arg-type]
    viewer.client_id, viewer.symbol, viewer.tf_s = "V", symbol, tf_s
    viewer.store_delta_cursor((symbol, tf_s), 1_000)
    app[APP_WS_SESSIONS] = {"V": viewer}

    task = asyncio.ensure_future(ws_server._global_delta_loop(app))
    try:
        for _ in range(2000):
            if viewer.ws.frames:  # type: ignore[attr-defined]
                break
            await asyncio.sleep(0.002)
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        app[APP_UDS_EXECUTOR].shutdown(wait=True)
    return [f for f in viewer.ws.frames if f.get("frame_type") == "delta"]  # type: ignore[attr-defined]


@pytest.mark.asyncio
@pytest.mark.parametrize("tick_ts_ms,h4_open_ms", _SCENARIOS)
async def test_tick_relay_seed_h4_on_season_grid(tick_ts_ms, h4_open_ms):
    with tempfile.TemporaryDirectory() as tmp:
        deltas = await _relay_frames(tmp, tick_ts_ms)
    assert deltas, "relay-кадр H4 не надійшов"
    candle = deltas[0]["candles"][0]
    assert candle["src"] == "tick_relay"
    assert candle["t_ms"] == h4_open_ms


def _seed_log_events(caplog) -> list[str]:
    return [
        r.getMessage().split(" ", 1)[0]
        for r in caplog.records
        if r.getMessage().startswith(("D1_FORMING_SEED_UDS", "D1_FORMING_NO_SEED"))
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("tick_ts_ms,h4_open_ms", _SCENARIOS)
async def test_tick_relay_seed_inherits_ohl_of_preview_bar_on_season_grid(caplog, tick_ts_ms, h4_open_ms):
    """Після рестарту relay-свічка успадковує O/H/L формуючого бару свого бакета з preview-площини, а не перший тік."""
    with caplog.at_level(logging.INFO, logger=ws_server._log.name):
        with tempfile.TemporaryDirectory() as tmp:
            deltas = await _relay_frames(tmp, tick_ts_ms, preview_curr_open_ms=h4_open_ms)
    assert deltas, "relay-кадр H4 не надійшов"
    candle = deltas[0]["candles"][0]
    assert candle["t_ms"] == h4_open_ms
    assert (candle["o"], candle["h"], candle["l"], candle["c"]) == (*_PREVIEW_OHL, TICK_MID)
    assert _seed_log_events(caplog) == ["D1_FORMING_SEED_UDS"]


@pytest.mark.asyncio
async def test_tick_relay_seed_ignores_preview_bar_on_legacy_grid(caplog):
    """Літо, тік 23:30: бакет сітки 21:00. preview-бар старої сітки 22:00 (ключ до TTL) relay не засіває — гучний NO_SEED."""
    with caplog.at_level(logging.INFO, logger=ws_server._log.name):
        with tempfile.TemporaryDirectory() as tmp:
            deltas = await _relay_frames(
                tmp, _utc_ms(2026, 7, 1, 23, 30), preview_curr_open_ms=_utc_ms(2026, 7, 1, 22)
            )
    assert deltas, "relay-кадр H4 не надійшов"
    candle = deltas[0]["candles"][0]
    assert candle["t_ms"] == _utc_ms(2026, 7, 1, 21)
    assert (candle["o"], candle["h"], candle["l"]) == (TICK_MID, TICK_MID, TICK_MID)
    assert _seed_log_events(caplog) == ["D1_FORMING_NO_SEED"]


# ── WS_TICK_RELAY_ERR: WARNING із троттлінгом ──────────────────────────


def test_tick_relay_err_warns_once_per_interval_with_suppressed_count(caplog):
    state: dict = {}
    interval_s = ws_server._TICK_RELAY_WARN_INTERVAL_S
    with caplog.at_level(logging.DEBUG, logger=ws_server._log.name):
        for now_s in (100.0, 101.0, 102.0, 100.0 + interval_s):
            ws_server._warn_tick_relay_err(state, XAU, H4_S, ValueError("boom"), now_s)
    records = [r for r in caplog.records if "WS_TICK_RELAY_ERR" in r.getMessage()]
    assert [r.levelno for r in records] == [logging.WARNING, logging.WARNING]
    assert "suppressed=0" in records[0].getMessage() and "suppressed=2" in records[1].getMessage()


# ── Exit gate ui_live_candle_plane, підгейт 3 ──────────────────────────


def test_gate_anchor_subgate_ws_server_on_season_grid():
    ok, detail, metrics = _check_overlay_anchor_sentinel(str(_REPO_ROOT))
    assert ok, detail
    assert metrics["has_legacy_resolve_anchor_offset_ms"] is False


def test_gate_anchor_subgate_rejects_legacy_static_anchor(tmp_path):
    ws_dir = tmp_path / "runtime" / "ws"
    ws_dir.mkdir(parents=True)
    (ws_dir / "ws_server.py").write_text(
        "APP_HTF_ANCHOR_RULE_FOR_SYMBOL; htf_bucket_start_ms; resolve_anchor_offset_ms(14400, cfg)\n",
        encoding="utf-8",
    )
    ok, detail, _ = _check_overlay_anchor_sentinel(str(tmp_path))
    assert not ok and "legacy_resolve_anchor_offset_ms" in detail
