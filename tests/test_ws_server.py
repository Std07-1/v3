"""
tests/test_ws_server.py — тести для runtime/ws/ws_server.py.

P1 exit-gate: 3 skeleton tests.
P2 exit-gate: +2 UDS integration tests (mock UDS).

pytest tests/test_ws_server.py -v
"""

from __future__ import annotations

import json
import pytest
import asyncio
from aiohttp import WSMsgType
from aiohttp.test_utils import AioHTTPTestCase, unittest_run_loop

from runtime.ws.ws_server import build_app, SCHEMA_V

pytestmark = pytest.mark.asyncio


# ── Mock UDS ───────────────────────────────────────────

class _MockWindowResult:
    """Імітує WindowResult з bars_lwc."""
    def __init__(self, bars_lwc, warnings=None):
        self.bars_lwc = bars_lwc
        self.warnings = warnings or []


class _MockUpdatesResult:
    """Імітує UpdatesResult."""
    def __init__(self, events=None, cursor_seq=0):
        self.events = events or []
        self.cursor_seq = cursor_seq


_MOCK_BARS_LWC = [
    {
        "open": 2350.50, "high": 2355.00, "low": 2348.00,
        "close": 2353.00, "volume": 100.0,
        "time": 1720000000, "open_time_ms": 1720000000000,
        "close_time_ms": 1720000300000,
    },
    {
        "open": 2353.00, "high": 2360.00, "low": 2351.00,
        "close": 2358.00, "volume": 150.0,
        "time": 1720000300, "open_time_ms": 1720000300000,
        "close_time_ms": 1720000600000,
    },
]


class _MockUDS:
    """Mock UDS reader: read_window повертає фіксовані бари."""
    def read_window(self, spec, policy):
        return _MockWindowResult(_MOCK_BARS_LWC)

    def read_updates(self, spec):
        return _MockUpdatesResult([], cursor_seq=42)


# ── Fixtures ───────────────────────────────────────────

@pytest.fixture
def ws_app():
    """Створює aiohttp app з WS endpoint для тестування."""
    return build_app(config_path="config.json")


@pytest.fixture
def ws_app_mock_uds():
    """Створює aiohttp app з mock UDS (детерміновані бари)."""
    return build_app(config_path="config.json", uds=_MockUDS())


# ── S20/S25 tests: error frames ───────────────────────

async def test_ws_s25_unknown_action_error_frame(aiohttp_client, ws_app_mock_uds):
    """S25: unknown action → error frame з code=unknown_action (не silent ignore)."""
    client = await aiohttp_client(ws_app_mock_uds)
    ws = await client.ws_connect("/ws")
    try:
        # Skip initial frames (config + full)
        for _ in range(3):
            try:
                await asyncio.wait_for(ws.receive_json(), timeout=2)
            except asyncio.TimeoutError:
                break

        # Send unknown action
        await ws.send_json({"action": "nonexistent_action_xyz"})
        resp = await asyncio.wait_for(ws.receive_json(), timeout=3)
        assert resp["frame_type"] == "error", "expected error frame, got %s" % resp.get("frame_type")
        assert resp["error"]["code"] == "unknown_action"
        assert "nonexistent_action_xyz" in resp["error"]["message"]
    finally:
        await ws.close()


async def test_ws_s20_bad_json_error_frame(aiohttp_client, ws_app_mock_uds):
    """S20: invalid JSON → error frame з code=json_parse_error."""
    client = await aiohttp_client(ws_app_mock_uds)
    ws = await client.ws_connect("/ws")
    try:
        for _ in range(3):
            try:
                await asyncio.wait_for(ws.receive_json(), timeout=2)
            except asyncio.TimeoutError:
                break

        await ws.send_str("{not valid json!!!")
        resp = await asyncio.wait_for(ws.receive_json(), timeout=3)
        assert resp["frame_type"] == "error"
        assert resp["error"]["code"] == "json_parse_error"
    finally:
        await ws.close()


async def test_ws_s20_missing_action_error_frame(aiohttp_client, ws_app_mock_uds):
    """S20: valid JSON but no 'action' field → error frame з code=missing_action."""
    client = await aiohttp_client(ws_app_mock_uds)
    ws = await client.ws_connect("/ws")
    try:
        for _ in range(3):
            try:
                await asyncio.wait_for(ws.receive_json(), timeout=2)
            except asyncio.TimeoutError:
                break

        await ws.send_json({"data": "no action field"})
        resp = await asyncio.wait_for(ws.receive_json(), timeout=3)
        assert resp["frame_type"] == "error"
        assert resp["error"]["code"] == "missing_action"
    finally:
        await ws.close()


async def _recv_frame(ws, frame_type, timeout=5):
    """Receive frames until one matches frame_type, or timeout."""
    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        remaining = deadline - asyncio.get_event_loop().time()
        if remaining <= 0:
            raise asyncio.TimeoutError("No %s frame within %ss" % (frame_type, timeout))
        msg = await asyncio.wait_for(ws.receive_json(), timeout=remaining)
        if msg.get("frame_type") == frame_type:
            return msg


async def test_ws_hello(aiohttp_client, ws_app):
    """Connect → msg з type=render_frame, schema_v=ui_v4_v2.
    S24: config frame відправляється першим, потім full/heartbeat.
    """
    client = await aiohttp_client(ws_app)
    ws = await client.ws_connect("/ws")
    try:
        # S24: first frame is config
        cfg_msg = await asyncio.wait_for(ws.receive_json(), timeout=5)
        assert cfg_msg["type"] == "render_frame"
        assert cfg_msg["frame_type"] == "config"
        assert cfg_msg["meta"]["schema_v"] == SCHEMA_V
        assert cfg_msg["meta"]["seq"] == 1
        # second frame is full or heartbeat
        msg = await asyncio.wait_for(ws.receive_json(), timeout=5)
        assert msg["type"] == "render_frame"
        assert msg["frame_type"] in ("heartbeat", "full"), "unexpected: %s" % msg["frame_type"]
    finally:
        await ws.close()


async def test_ws_heartbeat(aiohttp_client, ws_app):
    """Skip config+full → wait → heartbeat arrives."""
    client = await aiohttp_client(ws_app)
    ws_app["_heartbeat_s"] = 1
    ws = await client.ws_connect("/ws")
    try:
        # drain config+full (seq 1,2)
        for _ in range(2):
            await asyncio.wait_for(ws.receive_json(), timeout=5)

        # heartbeat frame
        hb = await asyncio.wait_for(ws.receive_json(), timeout=3)
        assert hb["frame_type"] == "heartbeat"
        assert hb["meta"]["schema_v"] == SCHEMA_V
    finally:
        await ws.close()


async def test_ws_seq_monotonic(aiohttp_client, ws_app):
    """3 frames → seq strictly increasing, all unique."""
    client = await aiohttp_client(ws_app)
    ws_app["_heartbeat_s"] = 0.5  # fast heartbeats
    ws = await client.ws_connect("/ws")
    try:
        seqs = []
        for _ in range(3):
            msg = await asyncio.wait_for(ws.receive_json(), timeout=3)
            seqs.append(msg["meta"]["seq"])
            assert msg["meta"]["schema_v"] == SCHEMA_V

        # Strictly increasing
        assert seqs == sorted(seqs)
        assert len(set(seqs)) == len(seqs), f"Duplicate seqs: {seqs}"
        # First seq is 1
        assert seqs[0] == 1
    finally:
        await ws.close()


# ── P2 tests ───────────────────────────────────────────

async def test_ws_full_frame_has_candles(aiohttp_client, ws_app_mock_uds):
    """P2: connect → full frame, candles have correct keys, t_ms > 1e12."""
    client = await aiohttp_client(ws_app_mock_uds)
    ws = await client.ws_connect("/ws")
    try:
        msg = await _recv_frame(ws, "full")
        assert msg["type"] == "render_frame"
        assert msg["meta"]["schema_v"] == SCHEMA_V

        # Must have candles from mock UDS
        candles = msg.get("candles", [])
        assert len(candles) == 2, f"expected 2 candles, got {len(candles)}"

        # Each candle: {t_ms, o, h, l, c, v}
        REQUIRED_KEYS = {"t_ms", "o", "h", "l", "c", "v"}
        for i, candle in enumerate(candles):
            missing = REQUIRED_KEYS - set(candle.keys())
            assert not missing, f"candle[{i}] missing keys: {missing}"
            assert isinstance(candle["t_ms"], int), f"candle[{i}].t_ms not int"
            assert candle["t_ms"] > 1e12, f"candle[{i}].t_ms={candle['t_ms']} not epoch_ms"
            assert isinstance(candle["o"], (int, float))
            assert isinstance(candle["v"], (int, float))

        # Monotonic t_ms
        t_ms_list = [c["t_ms"] for c in candles]
        assert t_ms_list == sorted(t_ms_list), f"candles not sorted by t_ms: {t_ms_list}"

        # Verify values match mock data
        assert candles[0]["t_ms"] == 1720000000000
        assert candles[0]["o"] == 2350.50
        assert candles[1]["t_ms"] == 1720000300000
        assert candles[1]["c"] == 2358.00
    finally:
        await ws.close()


async def test_ws_switch_full_frame(aiohttp_client, ws_app_mock_uds):
    """P2: send switch action → новий full frame з candles."""
    client = await aiohttp_client(ws_app_mock_uds)
    ws = await client.ws_connect("/ws")
    try:
        # Drain initial config + full
        first = await _recv_frame(ws, "full")
        assert first["frame_type"] == "full"

        # Send switch action (same symbol, different TF)
        switch_msg = json.dumps({
            "action": "switch",
            "symbol": "XAU/USD",
            "tf": "M15",
        })
        await ws.send_str(switch_msg)

        # Receive new full frame after switch
        second = await _recv_frame(ws, "full")
        assert second["type"] == "render_frame"
        assert second["frame_type"] == "full"
        assert second["tf"] == "M15"
        assert second["symbol"] == "XAU/USD"

        # candles present (mock always returns same bars)
        candles = second.get("candles", [])
        assert len(candles) == 2
    finally:
        await ws.close()


# ── P1 tests ───────────────────────────────────────────

async def test_ws_full_frame_boot_id_and_config(aiohttp_client, ws_app_mock_uds):
    """P1: full frame meta містить boot_id (str) та config.symbols/tfs."""
    client = await aiohttp_client(ws_app_mock_uds)
    ws = await client.ws_connect("/ws")
    try:
        msg = await _recv_frame(ws, "full")
        assert msg["frame_type"] == "full"
        meta = msg["meta"]

        # boot_id: non-empty string
        assert "boot_id" in meta, "meta missing boot_id"
        assert isinstance(meta["boot_id"], str)
        assert len(meta["boot_id"]) > 0

        # config: symbols and tfs lists
        assert "config" in meta, "meta missing config"
        cfg = meta["config"]
        assert isinstance(cfg["symbols"], list)
        assert len(cfg["symbols"]) > 0  # config.json має >=1 символ
        assert isinstance(cfg["tfs"], list)
        assert len(cfg["tfs"]) > 0  # config.json має >=1 TF
        # P2: tfs мають бути canonical label strings (M1, M5, H1, ...)
        valid_labels = {"M1", "M3", "M5", "M15", "M30", "H1", "H4", "D1"}
        for tf in cfg["tfs"]:
            assert isinstance(tf, str), f"tf {tf} is not str"
            assert tf in valid_labels, f"tf {tf} not in valid labels"
    finally:
        await ws.close()


async def test_ws_default_tf_m30(aiohttp_client, ws_app_mock_uds):
    """P1: initial full frame tf=M30 (1800s), sync з SymbolTfPicker default."""
    client = await aiohttp_client(ws_app_mock_uds)
    ws = await client.ws_connect("/ws")
    try:
        msg = await _recv_frame(ws, "full")
        assert msg["frame_type"] == "full"
        assert msg["tf"] == "M30", f"expected M30, got {msg['tf']}"
    finally:
        await ws.close()
