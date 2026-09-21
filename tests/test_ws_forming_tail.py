"""Формуюча свічка в хвості full-кадру (D4, розслідування 21.09).

До фіксу full-кадр (лише фінальна площина) не мав формуючої свічки на жодному TF — вона
з'являлась тільки з другою delta. Тепер preview-бар поточного бакета додається в хвіст,
ПІСЛЯ SMC/narrative/signals (ті беруть candles[-1] як закритий бар).
"""

from __future__ import annotations

import json
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from aiohttp import web

import runtime.ws.ws_server as ws_server
from preview_ring_fake import PreviewRingFakeRedis
from runtime.store.layers.redis_layer import RedisLayer
from runtime.store.redis_keys import preview_curr_key
from runtime.store.uds import UnifiedDataStore, _NullDiskLayer
from runtime.ws.app_keys import (
    APP_BOOT_ID,
    APP_PREVIEW_TF_SET,
    APP_SMC_RUNNER,
    APP_UDS,
    APP_UDS_EXECUTOR,
)
from runtime.ws.forming_tail import read_forming_candle, select_forming_candle

SYM = "XAU/USD"
TF = 60
TF_MS = TF * 1000
NOW_MS = 1_790_000_030_000
BUCKET_MS = NOW_MS - NOW_MS % TF_MS


def _lwc(open_ms: int, *, o=10.0, h=12.0, low=9.0, c=11.0, complete=False) -> dict:
    return {
        "time": open_ms // 1000,
        "open": o,
        "high": h,
        "low": low,
        "close": c,
        "volume": 3.0,
        "open_time_ms": open_ms,
        "close_time_ms": open_ms + TF_MS,
        "tf_s": TF,
        "src": "preview_tick",
        "complete": complete,
    }


def _final(open_ms: int) -> dict:
    return {"t_ms": open_ms, "o": 1.0, "h": 2.0, "l": 0.5, "c": 1.5, "v": 1.0}


# ── Pure: select_forming_candle ─────────────────────────────────────────


def test_select_forming_candle_preview_newer_than_last_final_is_returned():
    candle = select_forming_candle(
        [_final(BUCKET_MS - TF_MS)], [_lwc(BUCKET_MS)], tf_s=TF, now_ms=NOW_MS
    )
    assert candle == {"t_ms": BUCKET_MS, "o": 10.0, "h": 12.0, "l": 9.0, "c": 11.0, "v": 3.0}


def test_select_forming_candle_final_of_same_bucket_exists_returns_none():
    """I3: фінал бакета вже є — preview того ж бакета не домішується."""
    assert select_forming_candle([_final(BUCKET_MS)], [_lwc(BUCKET_MS)], tf_s=TF, now_ms=NOW_MS) is None


def test_select_forming_candle_bucket_already_closed_returns_none():
    """Прострочений preview: бар завис на бакеті, що вже закрився."""
    stale_open = BUCKET_MS - TF_MS
    assert (
        select_forming_candle([_final(stale_open - TF_MS)], [_lwc(stale_open)], tf_s=TF, now_ms=NOW_MS)
        is None
    )


def test_select_forming_candle_bucket_in_future_returns_none():
    assert select_forming_candle([], [_lwc(BUCKET_MS + TF_MS)], tf_s=TF, now_ms=NOW_MS) is None


def test_select_forming_candle_no_finals_returns_preview():
    assert select_forming_candle([], [_lwc(BUCKET_MS)], tf_s=TF, now_ms=NOW_MS)["t_ms"] == BUCKET_MS


def test_select_forming_candle_empty_preview_returns_none():
    assert select_forming_candle([_final(BUCKET_MS - TF_MS)], [], tf_s=TF, now_ms=NOW_MS) is None


def test_select_forming_candle_complete_preview_bar_returns_none():
    bar = _lwc(BUCKET_MS, complete=True)
    assert select_forming_candle([], [bar], tf_s=TF, now_ms=NOW_MS) is None


def test_select_forming_candle_infinite_price_returns_none_and_warns(caplog):
    """Формуюча обходить output guard full-кадру — нескінченна ціна не йде на графік і не мовчить (I5).

    h < l сюди не доходить: map_bar_to_candle_v4 уже нормалізує h/l.
    """
    bar = _lwc(BUCKET_MS, h=float("inf"))
    with caplog.at_level("WARNING"):
        assert select_forming_candle([], [bar], tf_s=TF, now_ms=NOW_MS) is None
    assert any("WS_FORMING_TAIL_BAD_SHAPE" in r.getMessage() for r in caplog.records)


def test_select_forming_candle_nan_price_returns_none():
    bar = _lwc(BUCKET_MS, c=float("nan"))
    assert select_forming_candle([], [bar], tf_s=TF, now_ms=NOW_MS) is None


# ── Impure: реальний UDS reader над RedisLayer ─────────────────────────


def _real_uds(tmp: str, fake: PreviewRingFakeRedis) -> UnifiedDataStore:
    return UnifiedDataStore(
        data_root=tmp,
        boot_id="t",
        tf_allowlist={TF},
        min_coldload_bars={},
        role="reader",
        redis_layer=RedisLayer(fake, "t"),
        disk_layer=_NullDiskLayer(),
        preview_tf_allowlist={TF},
    )


def _put_preview_curr(fake: PreviewRingFakeRedis, open_ms: int) -> None:
    payload = {
        "v": 1,
        "symbol": SYM,
        "tf_s": TF,
        "bar": {"open_ms": open_ms, "close_ms": open_ms + TF_MS - 1, "o": 10.0, "h": 12.0, "l": 9.0, "c": 11.0, "v": 3.0},
        "complete": False,
        "source": "preview_tick",
        "payload_ts_ms": NOW_MS,
    }
    fake.set(preview_curr_key("t", SYM, TF), json.dumps(payload))


def test_read_forming_candle_from_preview_curr_via_real_uds():
    fake = PreviewRingFakeRedis()
    _put_preview_curr(fake, BUCKET_MS)
    with tempfile.TemporaryDirectory() as tmp:
        candle = read_forming_candle(_real_uds(tmp, fake), SYM, TF, [_final(BUCKET_MS - TF_MS)], NOW_MS)
    assert candle is not None and candle["t_ms"] == BUCKET_MS and candle["h"] == 12.0


def test_read_forming_candle_expired_preview_keys_returns_none():
    """TTL preview:curr/tail минув → preview порожній → формуючої нема (без помилки)."""
    with tempfile.TemporaryDirectory() as tmp:
        candle = read_forming_candle(
            _real_uds(tmp, PreviewRingFakeRedis()), SYM, TF, [_final(BUCKET_MS - TF_MS)], NOW_MS
        )
    assert candle is None


# ── Інтеграція: _send_full_frame ────────────────────────────────────────


class _FakeWs:
    def __init__(self) -> None:
        self.closed = False
        self.frames: list[dict] = []

    async def send_json(self, obj: dict) -> None:
        self.frames.append(obj)


class _WindowUds:
    """read_window = фінали; read_preview_window = формуючий бар поточного бакета."""

    def __init__(self, finals_lwc: list[dict], preview_lwc: list[dict], *, with_preview: bool = True):
        self._finals = finals_lwc
        self._preview = preview_lwc
        self.preview_reads = 0
        if not with_preview:
            self.read_preview_window = None  # type: ignore[assignment]

    def read_window(self, spec, policy):
        return SimpleNamespace(bars_lwc=list(self._finals), warnings=[])

    def read_preview_window(self, symbol, tf_s, limit):  # type: ignore[no-redef]
        self.preview_reads += 1
        return SimpleNamespace(bars_lwc=list(self._preview), warnings=[])


class _NarrativeProbe:
    """SMC runner-зонд: фіксує, яку ціну/ATR-оцінку full-кадр віддав у narrative/signals."""

    def __init__(self) -> None:
        self.narrative_args: list[tuple] = []
        self._engine = SimpleNamespace(get_atr=lambda s, t: 1.0, get_rv=lambda s, t: 1.0)

    def get_snapshot(self, symbol, tf_s):
        return None

    def get_bias_map(self, symbol):
        return None

    def get_momentum_map(self, symbol):
        return None

    def get_pd_state(self, symbol, tf_s):
        return None

    def get_narrative(self, symbol, tf_s, last_c, atr_est):
        self.narrative_args.append((last_c, atr_est))
        return None


def _full_frame_app(uds, *, preview_tfs=frozenset({TF}), smc=None) -> web.Application:
    app = web.Application()
    app[APP_UDS] = uds
    app[APP_UDS_EXECUTOR] = ThreadPoolExecutor(max_workers=1)
    app[APP_BOOT_ID] = "t"
    app[APP_PREVIEW_TF_SET] = set(preview_tfs)
    if smc is not None:
        app[APP_SMC_RUNNER] = smc
    return app


async def _full_frame(app: web.Application) -> dict:
    session = ws_server.WsSession(_FakeWs())  # type: ignore[arg-type]
    session.symbol, session.tf_s = SYM, TF
    try:
        await ws_server._send_full_frame(session, app)
    finally:
        app[APP_UDS_EXECUTOR].shutdown(wait=True)
    return session.ws.frames[-1]  # type: ignore[attr-defined]


def _now_bucket_ms() -> int:
    """Відкриття «поточного» бакета за 1 с до now — без флейку на межі хвилини."""
    return int(time.time() * 1000) - 1000


@pytest.mark.asyncio
async def test_send_full_frame_appends_forming_after_narrative_inputs():
    bucket = _now_bucket_ms()
    last_final = _lwc(bucket - TF_MS, o=1.0, h=5.0, low=1.0, c=4.0, complete=True)
    forming = _lwc(bucket, o=4.0, h=4.1, low=4.0, c=4.05)
    probe = _NarrativeProbe()
    frame = await _full_frame(_full_frame_app(_WindowUds([last_final], [forming]), smc=probe))

    assert [c["t_ms"] for c in frame["candles"]] == [bucket - TF_MS, bucket]
    assert frame["candles"][-1]["c"] == 4.05
    # narrative/signals отримали закритий бар, а не формуючу (ATR-оцінка = 5-1, не 0.1)
    assert probe.narrative_args == [(4.0, 4.0)]


@pytest.mark.asyncio
async def test_send_full_frame_final_of_current_bucket_skips_preview():
    bucket = _now_bucket_ms()
    finals = [_lwc(bucket - TF_MS, complete=True), _lwc(bucket, c=7.0, complete=True)]
    frame = await _full_frame(_full_frame_app(_WindowUds(finals, [_lwc(bucket, c=99.0)])))
    assert [c["t_ms"] for c in frame["candles"]] == [bucket - TF_MS, bucket]
    assert frame["candles"][-1]["c"] == 7.0


@pytest.mark.asyncio
async def test_send_full_frame_preview_read_failure_warns_and_keeps_finals():
    bucket = _now_bucket_ms()
    uds = _WindowUds([_lwc(bucket - TF_MS, complete=True)], [], with_preview=False)
    frame = await _full_frame(_full_frame_app(uds))
    assert [c["t_ms"] for c in frame["candles"]] == [bucket - TF_MS]
    assert "forming_tail_unavailable" in frame["meta"]["warnings"]


@pytest.mark.asyncio
async def test_send_full_frame_tf_outside_preview_plane_skips_preview_read():
    bucket = _now_bucket_ms()
    uds = _WindowUds([_lwc(bucket - TF_MS, complete=True)], [_lwc(bucket)])
    frame = await _full_frame(_full_frame_app(uds, preview_tfs=frozenset()))
    assert [c["t_ms"] for c in frame["candles"]] == [bucket - TF_MS]
    assert uds.preview_reads == 0
