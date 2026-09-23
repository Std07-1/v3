"""Формуюча свічка в хвості full-кадру (D4, розслідування 21.09).

До фіксу full-кадр (лише фінальна площина) не мав формуючої свічки на жодному TF — вона
з'являлась тільки з другою delta. Тепер preview-бар поточного бакета додається в хвіст,
ПІСЛЯ SMC/narrative/signals (ті беруть candles[-1] як закритий бар).
"""

from __future__ import annotations

import datetime as dt
import json
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from aiohttp import web

import runtime.ws.ws_server as ws_server
from core.session_anchor import D1_S, H4_S, RULE_NY_CLOSE_US_DST
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


# ── H4/D1: кінець бакета з сезонної сітки (ADR-0095 S9a) ───────────────

_HOUR_MS = 3_600_000
# Нд 01.11.2026: торговий день Сб 31.10 21:00 (літо) триває 25 год до Нд 22:00 (зима);
# його останній H4 — обрубок Нд 21:00–22:00
_FALL_STUB_H4_MS = int(dt.datetime(2026, 11, 1, 21, tzinfo=dt.timezone.utc).timestamp() * 1000)
_FALL_25H_DAY_OPEN_MS = _FALL_STUB_H4_MS - 24 * _HOUR_MS


def test_select_forming_candle_h4_fall_stub_is_forming_inside_its_hour():
    now_ms = _FALL_STUB_H4_MS + _HOUR_MS // 2
    candle = select_forming_candle(
        [], [_lwc(_FALL_STUB_H4_MS)], tf_s=H4_S, now_ms=now_ms, anchor_rule=RULE_NY_CLOSE_US_DST
    )
    assert candle is not None and candle["t_ms"] == _FALL_STUB_H4_MS


def test_select_forming_candle_h4_fall_stub_stale_after_winter_bucket_opens():
    """Нд 22:30: обрубок 21:00 закрився о 22:00 — open + 4 год тримав би його формуючою до 01:00."""
    now_ms = _FALL_STUB_H4_MS + 90 * 60_000
    assert (
        select_forming_candle(
            [], [_lwc(_FALL_STUB_H4_MS)], tf_s=H4_S, now_ms=now_ms, anchor_rule=RULE_NY_CLOSE_US_DST
        )
        is None
    )


def test_select_forming_candle_d1_25h_day_is_forming_in_its_last_hour():
    """Нд 21:30: D1 Сб 31.10 21:00 триває до 22:00 — open + 24 год відкинув би його як прострочений."""
    now_ms = _FALL_STUB_H4_MS + _HOUR_MS // 2
    candle = select_forming_candle(
        [], [_lwc(_FALL_25H_DAY_OPEN_MS)], tf_s=D1_S, now_ms=now_ms, anchor_rule=RULE_NY_CLOSE_US_DST
    )
    assert candle is not None and candle["t_ms"] == _FALL_25H_DAY_OPEN_MS


def test_select_forming_candle_h4_without_anchor_rule_raises():
    now_ms = _FALL_STUB_H4_MS + _HOUR_MS // 2
    with pytest.raises(ValueError, match="anchor_rule_missing"):
        select_forming_candle([], [_lwc(_FALL_STUB_H4_MS)], tf_s=H4_S, now_ms=now_ms)


def _utc_ms(*args: int) -> int:
    return int(dt.datetime(*args, tzinfo=dt.timezone.utc).timestamp() * 1000)


# Ср 23.09.2026 (літо): сітка H4 = 21/01/05/09/13/17; ключ старого воркера — 22/02/../18, D1 — 22:00
_OFF_GRID_CASES = [
    pytest.param(H4_S, _utc_ms(2026, 9, 23, 13), _utc_ms(2026, 9, 23, 18), _utc_ms(2026, 9, 23, 19), id="h4_18_00"),
    pytest.param(D1_S, _utc_ms(2026, 9, 21, 21), _utc_ms(2026, 9, 22, 22), _utc_ms(2026, 9, 22, 23), id="d1_22_00"),
]


@pytest.mark.parametrize("tf_s,last_final_ms,preview_open_ms,now_ms", _OFF_GRID_CASES)
def test_select_forming_candle_htf_off_season_grid_preview_returns_none_and_warns(
    caplog, tf_s, last_final_ms, preview_open_ms, now_ms
):
    """Preview старої сітки (ключ до TTL) не стає формуючою поверх бакета сітки — і не мовчки (I5)."""
    with caplog.at_level("WARNING"):
        candle = select_forming_candle(
            [_final(last_final_ms)], [_lwc(preview_open_ms)], tf_s=tf_s, now_ms=now_ms, anchor_rule=RULE_NY_CLOSE_US_DST
        )
    assert candle is None
    warnings = [r.getMessage() for r in caplog.records if "WS_FORMING_TAIL_OFF_SEASON_GRID" in r.getMessage()]
    assert len(warnings) == 1 and "open_ms=%d" % preview_open_ms in warnings[0]


def test_select_forming_candle_h4_on_season_grid_after_final_is_returned():
    """Контроль до off-grid: той самий день, preview на бакеті сітки 17:00 після фіналу 13:00 — формуюча."""
    candle = select_forming_candle(
        [_final(_utc_ms(2026, 9, 23, 13))],
        [_lwc(_utc_ms(2026, 9, 23, 17))],
        tf_s=H4_S,
        now_ms=_utc_ms(2026, 9, 23, 19),
        anchor_rule=RULE_NY_CLOSE_US_DST,
    )
    assert candle is not None and candle["t_ms"] == _utc_ms(2026, 9, 23, 17)


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


def test_read_forming_candle_m1_does_not_resolve_anchor_rule():
    """Правило якоря потрібне лише H4/D1: символ невиміряної групи не ламає формуючу M1..H1."""

    def _unmeasured(symbol: str) -> str:
        raise ValueError("HTF_ANCHOR_GROUP_UNMEASURED symbol=%s" % symbol)

    fake = PreviewRingFakeRedis()
    _put_preview_curr(fake, BUCKET_MS)
    with tempfile.TemporaryDirectory() as tmp:
        candle = read_forming_candle(
            _real_uds(tmp, fake), SYM, TF, [_final(BUCKET_MS - TF_MS)], NOW_MS, _unmeasured
        )
    assert candle is not None and candle["t_ms"] == BUCKET_MS


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
