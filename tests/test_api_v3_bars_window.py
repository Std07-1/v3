"""ADR-0059 §3.1.1 (slice 059.1) — GET /api/v3/bars/window tests.

Covers:
* default tfs (M15,H1,H4) returns three buckets
* count out-of-range rejected (400) at both ends
* invalid symbol / TF rejected (400)
* since_ms incremental fetch returns only newer bars
* since_ms older than 1 year → full window + warning
* current_price = M15 last complete bar close
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict, List, Optional, cast

import pytest
from aiohttp import web

from runtime.api_v3 import endpoints as ep

pytestmark = pytest.mark.asyncio

VALID_TOKEN = "tk_" + ("a" * 64)


# ────────────────────────────────────────────────────────────
# Stubs
# ────────────────────────────────────────────────────────────


class _StubRecord:
    def __init__(self) -> None:
        self.consumer = "test_consumer"
        self.scope = "read"
        self.created = "2026-04-01T00:00:00Z"
        self.expires = "2026-12-31T00:00:00Z"


class _StubTokenStore:
    def __init__(self) -> None:
        self._valid = {VALID_TOKEN: _StubRecord()}

    def lookup(self, token: Optional[str]):
        return self._valid.get(token or "")


class _StubWindowResult:
    def __init__(self, bars_lwc: List[Dict[str, Any]]) -> None:
        self.bars_lwc = bars_lwc
        self.meta: Dict[str, Any] = {}
        self.warnings: List[str] = []


class _StubUds:
    """Stub matching `runtime.store.uds.UnifiedDataStore.read_window` shape."""

    def __init__(self, bars_by_tf: Dict[int, List[Dict[str, Any]]]) -> None:
        self._bars_by_tf = bars_by_tf
        self.calls: List[tuple[str, int, int]] = []

    def read_window(self, spec: Any, _policy: Any) -> _StubWindowResult:
        self.calls.append((spec.symbol, spec.tf_s, spec.limit))
        rows = list(self._bars_by_tf.get(spec.tf_s, []))
        return _StubWindowResult(rows[-spec.limit :] if spec.limit else rows)


# ────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────


def _make_bars(
    tf_s: int, n: int, *, start_open_ms: int = 1_700_000_000_000
) -> List[Dict[str, Any]]:
    """Generate `n` complete bars in the UDS `bars_lwc` shape."""
    tf_ms = tf_s * 1000
    out: List[Dict[str, Any]] = []
    for i in range(n):
        open_ms = start_open_ms + i * tf_ms
        out.append(
            {
                "time": open_ms // 1000,
                "open": 100.0 + i,
                "high": 101.0 + i,
                "low": 99.0 + i,
                "close": 100.5 + i,
                "volume": 10.0,
                "open_time_ms": open_ms,
                "close_time_ms": open_ms + tf_ms,
                "complete": True,
            }
        )
    return out


def _build_app(
    uds: _StubUds,
    *,
    config_symbols: Optional[List[str]] = None,
    tmp_path: Optional[Path] = None,
) -> web.Application:
    from runtime.ws.ws_server import APP_FULL_CONFIG, APP_UDS

    app = web.Application()
    app[APP_UDS] = cast(Any, uds)
    app[APP_FULL_CONFIG] = {"symbols": config_symbols or ["XAU/USD", "BTCUSDT"]}
    signals_dir = (tmp_path or Path(".")) / "_signals"
    signals_dir.mkdir(exist_ok=True)
    ep.register_routes(
        app,
        token_store=_StubTokenStore(),  # type: ignore[arg-type]
        signals_dir=str(signals_dir),
        audit_dir=None,
    )
    return app


def _auth() -> Dict[str, str]:
    return {"X-API-Key": VALID_TOKEN}


# ────────────────────────────────────────────────────────────
# Tests
# ────────────────────────────────────────────────────────────


async def test_bars_window_default_tfs_returns_M15_H1_H4(aiohttp_client, tmp_path):
    uds = _StubUds(
        {
            900: _make_bars(900, 60),
            3600: _make_bars(3600, 60),
            14400: _make_bars(14400, 60),
        }
    )
    client = await aiohttp_client(_build_app(uds, tmp_path=tmp_path))
    resp = await client.get("/api/v3/bars/window?symbol=XAU/USD", headers=_auth())
    assert resp.status == 200, await resp.text()
    body = await resp.json()
    assert body["schema_version"] == "v3.1"
    assert body["kind"] == "bars_window"
    data = body["data"]
    assert data["symbol"] == "XAU/USD"
    assert data["incremental"] is False
    assert data["since_ms"] is None
    assert set(data["bars"].keys()) == {"M15", "H1", "H4"}
    for tf_label in ("M15", "H1", "H4"):
        rows = data["bars"][tf_label]
        assert len(rows) == 60
        first = rows[0]
        assert set(first.keys()) == {"open_ms", "o", "h", "l", "c", "v"}
        # Monotonic ASC
        for prev, curr in zip(rows, rows[1:]):
            assert curr["open_ms"] > prev["open_ms"]
    # current_price = last M15 close
    last_m15 = data["bars"]["M15"][-1]
    assert data["current_price"] == last_m15["c"]
    assert data["meta"]["latest_open_ms"]["M15"] == last_m15["open_ms"]


async def test_bars_window_count_above_max_rejected_400(aiohttp_client, tmp_path):
    uds = _StubUds({900: _make_bars(900, 10)})
    client = await aiohttp_client(_build_app(uds, tmp_path=tmp_path))
    resp = await client.get(
        "/api/v3/bars/window?symbol=XAU/USD&count=999", headers=_auth()
    )
    assert resp.status == 400
    body = await resp.json()
    assert body["data"]["code"] == "count_above_max"


async def test_bars_window_count_below_min_rejected_400(aiohttp_client, tmp_path):
    uds = _StubUds({900: _make_bars(900, 10)})
    client = await aiohttp_client(_build_app(uds, tmp_path=tmp_path))
    resp = await client.get(
        "/api/v3/bars/window?symbol=XAU/USD&count=10", headers=_auth()
    )
    assert resp.status == 400
    body = await resp.json()
    assert body["data"]["code"] == "count_below_min"


async def test_bars_window_invalid_symbol_400(aiohttp_client, tmp_path):
    uds = _StubUds({900: _make_bars(900, 10)})
    client = await aiohttp_client(_build_app(uds, tmp_path=tmp_path))
    resp = await client.get("/api/v3/bars/window?symbol=NOPE/USD", headers=_auth())
    assert resp.status == 400
    body = await resp.json()
    assert body["data"]["code"] == "symbol_invalid"


async def test_bars_window_invalid_tf_400(aiohttp_client, tmp_path):
    uds = _StubUds({900: _make_bars(900, 10)})
    client = await aiohttp_client(_build_app(uds, tmp_path=tmp_path))
    resp = await client.get(
        "/api/v3/bars/window?symbol=XAU/USD&tfs=M15,D1", headers=_auth()
    )
    assert resp.status == 400
    body = await resp.json()
    assert body["data"]["code"] == "tf_invalid"


async def test_bars_window_since_ms_filters_to_new_bars_only(aiohttp_client, tmp_path):
    # Anchor recent enough that `since_ms` won't trip the 1-year staleness
    # check. Pick a base 2 hours ago so all M15 bars land within the window.
    now_ms = int(time.time() * 1000)
    base = now_ms - 2 * 3600 * 1000
    bars_m15 = _make_bars(900, 60, start_open_ms=base)
    uds = _StubUds(
        {900: bars_m15, 3600: _make_bars(3600, 60, start_open_ms=base), 14400: []}
    )
    client = await aiohttp_client(_build_app(uds, tmp_path=tmp_path))
    cutoff = bars_m15[-3]["open_time_ms"]  # only last 2 bars are newer
    resp = await client.get(
        f"/api/v3/bars/window?symbol=XAU/USD&tfs=M15&since_ms={cutoff}",
        headers=_auth(),
    )
    assert resp.status == 200, await resp.text()
    body = await resp.json()
    data = body["data"]
    assert data["incremental"] is True
    assert data["since_ms"] == cutoff
    m15_rows = data["bars"]["M15"]
    assert len(m15_rows) == 2
    assert all(r["open_ms"] > cutoff for r in m15_rows)
    assert "since_ms_too_old_full_window_returned" not in data["meta"]["warnings"]


async def test_bars_window_since_ms_too_old_returns_full_with_warning(
    aiohttp_client, tmp_path
):
    now_ms = int(time.time() * 1000)
    bars_m15 = _make_bars(900, 60, start_open_ms=now_ms - 60 * 900_000)
    uds = _StubUds({900: bars_m15})
    client = await aiohttp_client(_build_app(uds, tmp_path=tmp_path))
    very_old = 1_000_000_000_000  # year 2001 → > 1 year stale
    resp = await client.get(
        f"/api/v3/bars/window?symbol=XAU/USD&tfs=M15&since_ms={very_old}",
        headers=_auth(),
    )
    assert resp.status == 200, await resp.text()
    body = await resp.json()
    data = body["data"]
    # incremental flag still reflects that the consumer asked, but warnings
    # tell them they got a full window back.
    assert data["incremental"] is True
    assert data["since_ms"] == very_old
    assert len(data["bars"]["M15"]) == 60
    assert "since_ms_too_old_full_window_returned" in data["meta"]["warnings"]
