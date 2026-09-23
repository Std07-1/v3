"""Гонка курсора в _global_delta_loop (D2, розслідування 21.09) — у стилі race_sim.

Реальний _global_delta_loop + реальний _handle_switch/_send_full_frame + реальний
UnifiedDataStore(reader) над реальним RedisLayer і in-memory фейком Redis. Switch
вприскується рівно посеред await читання кільця — детерміновано, без живих WS-проб
(проба 21.09 13:37 отруїла групу живого глядача).

Сценарії:
  * switch під час read: курсор M30 не лягає на сесію, що вже на D1; глядач D1 живий;
  * те саме в relay-гілці D1 (третє місце запису курсора);
  * connect: новий курсор None (не 0) — дефолтна група не падає в gap;
  * gap / курсор «з майбутнього» → fast-forward усієї групи + WARN;
  * збій full-кадру → курсор None.
"""

from __future__ import annotations

import asyncio
import json
import logging
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Optional

import pytest
from aiohttp import web

import runtime.ws.ws_server as ws_server
from preview_ring_fake import PreviewRingFakeRedis
from core.config_loader import htf_anchor_rule_resolver
from core.session_anchor import RULE_NY_CLOSE_US_DST, htf_bucket_start_ms
from runtime.store.layers.redis_layer import RedisLayer
from runtime.store.uds import UnifiedDataStore, _NullDiskLayer
from runtime.ws.app_keys import (
    APP_BOOT_ID,
    APP_D1_TICK_RELAY_TFS,
    APP_DELTA_POLL_S,
    APP_FULL_CONFIG,
    APP_HTF_ANCHOR_RULE_FOR_SYMBOL,
    APP_PREVIEW_TF_SET,
    APP_SYMBOLS_SET,
    APP_TF_ALLOWLIST,
    APP_TICK_REDIS_CLIENT,
    APP_TICK_REDIS_NS,
    APP_UDS,
    APP_UDS_EXECUTOR,
    APP_WS_SESSIONS,
)

NS = "t"
RETAIN = 200
XAU, XAG = "XAU/USD", "XAG/USD"
M1, M30, D1 = 60, 1800, 86400
# Порядки величин лічильників кілець як на VPS 21.09 13:53
M30_MAX, D1_MAX, XAG_M1_MAX = 5_354_807, 5_371_182, 5_233_329
FORMING_OPEN_MS = 1_790_000_000_000


class _FakeWs:
    def __init__(self) -> None:
        self.closed = False
        self.frames: list[dict] = []

    async def send_str(self, payload: str) -> None:
        self.frames.append(json.loads(payload))

    async def send_json(self, obj: dict) -> None:
        self.frames.append(obj)

    async def close(self) -> None:
        self.closed = True


def _session(name: str, symbol: str, tf_s: int) -> ws_server.WsSession:
    session = ws_server.WsSession(_FakeWs())  # type: ignore[arg-type]
    session.client_id = name
    session.symbol, session.tf_s = symbol, tf_s
    return session


def _deltas(session: ws_server.WsSession, tf_label: Optional[str] = None) -> list[dict]:
    return [
        f
        for f in session.ws.frames  # type: ignore[attr-defined]
        if f.get("frame_type") == "delta" and (tf_label is None or f.get("tf") == tf_label)
    ]


class _Harness:
    """App + кільця + перехоплення _uds_read_updates (hook після await читання)."""

    def __init__(self, tmp_dir: str) -> None:
        self.redis = PreviewRingFakeRedis()
        for sym, tf_s, last_seq in ((XAU, M30, M30_MAX), (XAU, D1, D1_MAX), (XAG, M1, XAG_M1_MAX)):
            self.redis.seed_ring(NS, sym, tf_s, last_seq, RETAIN, retain=RETAIN)
        uds = UnifiedDataStore(
            data_root=tmp_dir,
            boot_id="race",
            tf_allowlist={M1, M30, D1},
            min_coldload_bars={},
            role="reader",
            redis_layer=RedisLayer(self.redis, NS),
            disk_layer=_NullDiskLayer(),
            preview_tf_allowlist={M1, M30, D1},
            preview_updates_retain=RETAIN,
        )
        self.app = web.Application()
        self.app[APP_UDS] = uds
        self.app[APP_DELTA_POLL_S] = 0.001
        self.app[APP_PREVIEW_TF_SET] = {M1, M30, D1}
        self.app[APP_WS_SESSIONS] = {}
        self.app[APP_FULL_CONFIG] = {
            "symbols": [XAU, XAG],
            "market_calendar_symbol_groups": {XAU: "cfd_us_22_23", XAG: "cfd_us_22_23"},
            "htf_anchor": {"rule_by_calendar_group": {"cfd_us_22_23": RULE_NY_CLOSE_US_DST}},
        }
        # ADR-0095 S9a: бакет relay-свічки D1 — за правилом символу, як у build_app
        self.app[APP_HTF_ANCHOR_RULE_FOR_SYMBOL] = htf_anchor_rule_resolver(self.app[APP_FULL_CONFIG])
        self.app[APP_SYMBOLS_SET] = {XAU, XAG}
        self.app[APP_TF_ALLOWLIST] = {M1, M30, D1}
        self.app[APP_D1_TICK_RELAY_TFS] = set()
        self.app[APP_TICK_REDIS_NS] = NS
        self.app[APP_UDS_EXECUTOR] = ThreadPoolExecutor(max_workers=2)
        self.app[APP_BOOT_ID] = "race"
        self.reads: dict[tuple[str, int], int] = {}
        self.publish_on_read: set[tuple[str, int]] = set()
        self.after_read_hook: Optional[Callable[[tuple[str, int]], Any]] = None

    def add(self, *sessions: ws_server.WsSession) -> None:
        for s in sessions:
            self.app[APP_WS_SESSIONS][s.client_id] = s

    async def run_until(self, target: tuple[str, int], reads: int, monkeypatch) -> None:
        real_read = ws_server._uds_read_updates

        async def _read(app, symbol, tf_s, since_seq, include_preview):
            key = (symbol, tf_s)
            if key in self.publish_on_read:
                self.redis.push_ring_event(NS, symbol, tf_s, FORMING_OPEN_MS, retain=RETAIN)
            result = await real_read(app, symbol, tf_s, since_seq, include_preview)
            self.reads[key] = self.reads.get(key, 0) + 1
            if self.after_read_hook is not None:
                await self.after_read_hook(key)
            return result

        monkeypatch.setattr(ws_server, "_uds_read_updates", _read)
        task = asyncio.ensure_future(ws_server._global_delta_loop(self.app))
        try:
            for _ in range(2000):
                if self.reads.get(target, 0) >= reads:
                    break
                await asyncio.sleep(0.002)
            assert self.reads.get(target, 0) >= reads, "delta loop не зробив потрібну кількість poll"
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            self.app[APP_UDS_EXECUTOR].shutdown(wait=True)


@pytest.fixture
def harness():
    with tempfile.TemporaryDirectory() as tmp:
        yield _Harness(tmp)


def _switch_once_during_read(
    harness: _Harness, session: ws_server.WsSession, read_target: tuple[str, int], tf_label: str
) -> dict:
    """Під час await читання read_target клієнт шле switch (реальний _handle_switch + full)."""
    state = {"done": False}

    async def _hook(key: tuple[str, int]) -> None:
        if state["done"] or key != read_target:
            return
        if session.delta_cursor(read_target) is None:
            return  # спершу сесія має отримати курсор цієї цілі (adopt)
        state["done"] = True
        await ws_server._handle_switch(session, {"symbol": XAU, "tf": tf_label}, harness.app)

    harness.after_read_hook = _hook
    return state


# ── Одиничні інваріанти сесії ─────────────────────────────────────────


def test_ws_session_new_connection_cursor_is_none():
    session = _session("C", XAU, M30)
    assert session.delta_cursor((XAU, M30)) is None


def test_ws_session_store_cursor_for_foreign_target_is_rejected():
    session = _session("S", XAU, D1)
    assert session.store_delta_cursor((XAU, M30), M30_MAX) is False
    assert session.delta_cursor((XAU, D1)) is None
    assert session.delta_cursor((XAU, M30)) is None


def test_ws_session_cursor_of_old_pair_invisible_after_target_change():
    session = _session("S", XAU, M30)
    assert session.store_delta_cursor((XAU, M30), M30_MAX) is True
    session.symbol, session.tf_s = XAU, D1  # навіть без reset курсор M30 не видно для D1
    assert session.delta_cursor((XAU, D1)) is None
    assert session.delta_cursor((XAU, M30)) is None


# ── Гонка switch посеред await ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_delta_loop_switch_during_read_keeps_d1_group_alive(harness, monkeypatch, caplog):
    """S: M30→D1 посеред читання M30. Курсор M30 не лягає на S; глядач V на D1 не глухне."""
    s = _session("S", XAU, M30)
    v = _session("V", XAU, D1)
    v.store_delta_cursor((XAU, D1), D1_MAX)
    harness.add(s, v)
    harness.publish_on_read = {(XAU, M30), (XAU, D1)}
    race = _switch_once_during_read(harness, s, (XAU, M30), "D1")

    with caplog.at_level(logging.WARNING, logger=ws_server._log.name):
        await harness.run_until((XAU, D1), reads=8, monkeypatch=monkeypatch)
    # Прив'язка курсора до цілі (S-A) сама не дає отрути: fast-forward (S-B) тут не має спрацьовувати
    assert not [r for r in caplog.records if "WS_CURSOR_GAP_FASTFORWARD" in r.getMessage()]

    assert race["done"], "switch мав статися посеред читання M30"
    s_cursor = s.delta_cursor((XAU, D1))
    assert s_cursor is not None and D1_MAX - RETAIN <= s_cursor <= D1_MAX + 8
    # V отримує delta на кожному poll з подіями (на HEAD: 1-2 кадри, далі тиша — gap навіки)
    assert len(_deltas(v, "D1")) >= 6
    # S після full D1 отримує лише D1-дельти (жодного M30-кадру після switch)
    full_idx = max(i for i, f in enumerate(s.ws.frames) if f.get("frame_type") == "full")
    assert all(f.get("tf") == "D1" for f in s.ws.frames[full_idx:] if f.get("frame_type") == "delta")
    assert len(_deltas(s, "D1")) >= 3


@pytest.mark.asyncio
async def test_delta_loop_switch_to_quieter_ring_does_not_inherit_future_cursor(harness, monkeypatch, caplog):
    """S: XAU M30→XAG M1 (лічильник кільця менший за курсор M30) — без майбутнього курсора."""
    s = _session("S", XAU, M30)
    harness.add(s)
    harness.publish_on_read = {(XAU, M30), (XAG, M1)}
    state = {"done": False}

    async def _hook(key: tuple[str, int]) -> None:
        if state["done"] or key != (XAU, M30) or s.delta_cursor((XAU, M30)) is None:
            return
        state["done"] = True
        await ws_server._handle_switch(s, {"symbol": XAG, "tf": "M1"}, harness.app)

    harness.after_read_hook = _hook
    with caplog.at_level(logging.WARNING, logger=ws_server._log.name):
        await harness.run_until((XAG, M1), reads=6, monkeypatch=monkeypatch)
    # Прив'язка курсора до цілі (S-A) сама не дає отрути: fast-forward (S-B) тут не має спрацьовувати
    assert not [r for r in caplog.records if "WS_CURSOR_GAP_FASTFORWARD" in r.getMessage()]

    assert state["done"]
    s_cursor = s.delta_cursor((XAG, M1))
    assert s_cursor is not None and s_cursor <= XAG_M1_MAX + 6
    assert len(_deltas(s, "M1")) >= 3


@pytest.mark.asyncio
async def test_delta_loop_relay_branch_switch_does_not_poison_new_group(harness, monkeypatch, caplog):
    """Третє місце запису (relay D1 без подій): сесія, що пішла з D1, не отримує D1-курсор і кадр."""
    tick_redis = PreviewRingFakeRedis()
    tick_ts_ms = int(time.time() * 1000)
    tick_redis.kv[f"{NS}:tick:last:XAU_USD"] = json.dumps(
        {"mid": 4350.0, "tick_ts_ms": tick_ts_ms}
    ).encode()
    harness.app[APP_D1_TICK_RELAY_TFS] = {D1}
    harness.app[APP_TICK_REDIS_CLIENT] = tick_redis
    s = _session("S", XAU, D1)
    v = _session("V", XAU, D1)
    s.store_delta_cursor((XAU, D1), D1_MAX)
    v.store_delta_cursor((XAU, D1), D1_MAX)
    harness.add(s, v)
    harness.publish_on_read = {(XAU, M30)}  # кільце D1 тихе → relay-гілка
    race = _switch_once_during_read(harness, s, (XAU, D1), "M30")

    with caplog.at_level(logging.WARNING, logger=ws_server._log.name):
        await harness.run_until((XAU, M30), reads=6, monkeypatch=monkeypatch)
    # Прив'язка курсора до цілі (S-A) сама не дає отрути: fast-forward (S-B) тут не має спрацьовувати
    assert not [r for r in caplog.records if "WS_CURSOR_GAP_FASTFORWARD" in r.getMessage()]

    assert race["done"]
    s_cursor = s.delta_cursor((XAU, M30))
    assert s_cursor is not None and M30_MAX - RETAIN <= s_cursor <= M30_MAX + 6
    assert len(_deltas(s, "M30")) >= 3
    full_idx = max(i for i, f in enumerate(s.ws.frames) if f.get("frame_type") == "full")
    assert all(f.get("tf") == "M30" for f in s.ws.frames[full_idx:] if f.get("frame_type") == "delta")
    assert len(_deltas(v, "D1")) >= 3  # relay-кадри глядачу D1 йдуть і далі
    # ADR-0095 S9a: relay-свічка D1 — на сезонній сітці (відкриття торгового дня 17:00 NY)
    d1_open_ms = htf_bucket_start_ms(tick_ts_ms, D1, RULE_NY_CLOSE_US_DST)
    assert {f["candles"][0]["t_ms"] for f in _deltas(v, "D1")} == {d1_open_ms}


# ── connect / gap / майбутній курсор ───────────────────────────────────


@pytest.mark.asyncio
async def test_delta_loop_connect_with_none_cursor_keeps_default_group_alive(harness, monkeypatch):
    """Новий connect (курсор None, full ще в дорозі) не валить групу XAU/USD:M30 у gap."""
    v = _session("V", XAU, M30)
    v.store_delta_cursor((XAU, M30), M30_MAX)
    c = _session("C", XAU, M30)
    harness.add(v, c)
    harness.publish_on_read = {(XAU, M30)}

    await harness.run_until((XAU, M30), reads=6, monkeypatch=monkeypatch)

    assert len(_deltas(v, "M30")) >= 5
    assert len(_deltas(c, "M30")) >= 4  # перший poll — adopt, далі delta


@pytest.mark.asyncio
async def test_delta_loop_future_cursor_fast_forwards_group_and_warns(harness, monkeypatch, caplog):
    """Скинутий лічильник Redis: курсори групи > лічильника → fast-forward + WARN, delta оживає."""
    a = _session("A", XAG, M1)
    b = _session("B", XAG, M1)
    a.store_delta_cursor((XAG, M1), M30_MAX)  # «з майбутнього» для кільця XAG M1
    b.store_delta_cursor((XAG, M1), M30_MAX)
    harness.add(a, b)
    harness.publish_on_read = {(XAG, M1)}
    with caplog.at_level(logging.WARNING, logger=ws_server._log.name):
        await harness.run_until((XAG, M1), reads=6, monkeypatch=monkeypatch)

    warns = [r.getMessage() for r in caplog.records if "WS_CURSOR_GAP_FASTFORWARD" in r.getMessage()]
    assert len(warns) == 1 and "reason=cursor_ahead" in warns[0]
    for s in (a, b):
        assert s.delta_cursor((XAG, M1)) <= XAG_M1_MAX + 6
        assert len(_deltas(s, "M1")) >= 4


@pytest.mark.asyncio
async def test_delta_loop_cursor_behind_ring_fast_forwards_group_and_warns(harness, monkeypatch, caplog):
    """Курсор позаду кільця (gap): на HEAD повторювався щоразу; тепер один fast-forward."""
    a = _session("A", XAU, D1)
    a.store_delta_cursor((XAU, D1), M30_MAX)  # < min кільця D1 - 1
    harness.add(a)
    harness.publish_on_read = {(XAU, D1)}
    with caplog.at_level(logging.WARNING, logger=ws_server._log.name):
        await harness.run_until((XAU, D1), reads=6, monkeypatch=monkeypatch)

    warns = [r.getMessage() for r in caplog.records if "WS_CURSOR_GAP_FASTFORWARD" in r.getMessage()]
    assert len(warns) == 1 and "reason=cursor_behind" in warns[0]
    assert len(_deltas(a, "D1")) >= 4


# ── full-кадр: курсор None на всіх шляхах ──────────────────────────────


class _RaisingWindowUds:
    def read_window(self, spec, policy):
        raise RuntimeError("disk exploded")


@pytest.mark.asyncio
async def test_send_full_frame_read_failure_resets_cursor_to_none():
    app = web.Application()
    app[APP_UDS] = _RaisingWindowUds()
    app[APP_UDS_EXECUTOR] = ThreadPoolExecutor(max_workers=1)
    app[APP_BOOT_ID] = "t"
    session = _session("S", XAU, M30)
    session.store_delta_cursor((XAU, M30), 5)
    try:
        await ws_server._send_full_frame(session, app)
    finally:
        app[APP_UDS_EXECUTOR].shutdown(wait=True)
    assert session.delta_cursor((XAU, M30)) is None


@pytest.mark.asyncio
async def test_send_full_frame_uds_unavailable_resets_cursor_to_none():
    app = web.Application()  # без APP_UDS → _uds_read_window повертає None
    app[APP_BOOT_ID] = "t"
    session = _session("S", XAU, M30)
    session.store_delta_cursor((XAU, M30), 5)
    await ws_server._send_full_frame(session, app)
    assert session.delta_cursor((XAU, M30)) is None
    assert session.ws.frames[-1]["meta"]["warnings"] == ["uds_unavailable"]  # type: ignore[attr-defined]
