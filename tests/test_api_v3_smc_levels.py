"""ADR-0059 §3.1.3 (slice 059.3) — GET /api/v3/smc/levels tests.

Covers:
* default response shape (symbol, current_price, previous_day, previous_week, sessions)
* previous_day = {high, low, close, ts_ms} (4 fields)
* previous_week = {high, low} ONLY (no close, no ts_ms — contractual closure)
* sessions = {asia, london, ny}, each with {high, low, complete, swept_high, swept_low}
* incomplete session → high=null, low=null, complete=false (warmup default)
* swept_high/low semantics: current session H/L exceeded its prior occurrence
* current_price = M15.close last complete (cross-endpoint atomic)
* warmup (no M15) → 503 price_unavailable
* missing symbol → 400
"""

from __future__ import annotations

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


class _StubSessionState:
    """Mirrors core.smc.sessions.SessionState fields used by handler."""

    def __init__(
        self,
        *,
        name: str,
        active: bool = False,
        current_high: Optional[float] = None,
        current_low: Optional[float] = None,
        previous_high: Optional[float] = None,
        previous_low: Optional[float] = None,
    ) -> None:
        self.name = name
        self.active = active
        self.in_killzone = False
        self.current_high = current_high
        self.current_low = current_low
        self.current_start_ms = None
        self.previous_high = previous_high
        self.previous_low = previous_low
        self.previous_start_ms = None


class _StubSmcRunner:
    def __init__(
        self,
        *,
        session_states: Optional[List[_StubSessionState]] = None,
    ) -> None:
        self._session_states = session_states or []

    def get_session_states(self, symbol: str, current_time_ms: int) -> List[Any]:
        return list(self._session_states)


class _StubWindowResult:
    def __init__(self, bars_lwc: List[Dict[str, Any]]) -> None:
        self.bars_lwc = bars_lwc
        self.meta: Dict[str, Any] = {}
        self.warnings: List[str] = []


class _StubUds:
    def __init__(self, bars_by_tf: Dict[int, List[Dict[str, Any]]]) -> None:
        self._bars_by_tf = bars_by_tf

    def read_window(self, spec: Any, _policy: Any) -> _StubWindowResult:
        rows = list(self._bars_by_tf.get(spec.tf_s, []))
        return _StubWindowResult(rows[-spec.limit :] if spec.limit else rows)


# ────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────


def _make_m15_bar(
    close: float = 100.0, open_ms: int = 1_700_000_000_000
) -> Dict[str, Any]:
    return {
        "open": close - 0.5,
        "high": close + 1.0,
        "low": close - 1.0,
        "close": close,
        "volume": 10.0,
        "open_time_ms": open_ms,
        "close_time_ms": open_ms + 900_000,
        "complete": True,
    }


def _make_d1_bar(
    *,
    open_ms: int,
    high: float,
    low: float,
    close: float,
    complete: bool = True,
) -> Dict[str, Any]:
    return {
        "open": (high + low) / 2,
        "high": high,
        "low": low,
        "close": close,
        "volume": 1000.0,
        "open_time_ms": open_ms,
        "close_time_ms": open_ms + 86_400_000,
        "complete": complete,
    }


def _build_app(
    *,
    runner: Optional[_StubSmcRunner],
    uds: _StubUds,
    config_symbols: Optional[List[str]] = None,
    tmp_path: Optional[Path] = None,
    skip_runner: bool = False,
) -> web.Application:
    from runtime.ws.ws_server import APP_FULL_CONFIG, APP_SMC_RUNNER, APP_UDS

    app = web.Application()
    app[APP_UDS] = cast(Any, uds)
    if not skip_runner and runner is not None:
        app[APP_SMC_RUNNER] = cast(Any, runner)
    app[APP_FULL_CONFIG] = {"symbols": config_symbols or ["XAU/USD"]}
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


def _make_d1_lookback() -> List[Dict[str, Any]]:
    """6 D1 bars: 5 prior week + 1 previous day. All complete."""
    base_ms = 1_700_000_000_000
    return [
        _make_d1_bar(
            open_ms=base_ms + i * 86_400_000,
            high=110.0 + i,
            low=90.0 - i,
            close=100.0 + i,
        )
        for i in range(6)
    ]


# ────────────────────────────────────────────────────────────
# Tests
# ────────────────────────────────────────────────────────────


async def test_smc_levels_default_response_shape(aiohttp_client, tmp_path):
    runner = _StubSmcRunner(
        session_states=[
            _StubSessionState(
                name="asia",
                active=False,
                current_high=101.0,
                current_low=99.0,
                previous_high=100.5,
                previous_low=99.5,
            ),
            _StubSessionState(
                name="london",
                active=True,
                current_high=102.0,
                current_low=98.5,
                previous_high=101.0,
                previous_low=99.0,
            ),
            _StubSessionState(
                name="newyork",
                active=False,
            ),
        ]
    )
    uds = _StubUds(
        {
            900: [_make_m15_bar(close=100.0)],
            86400: _make_d1_lookback(),
        }
    )
    client = await aiohttp_client(_build_app(runner=runner, uds=uds, tmp_path=tmp_path))
    res = await client.get("/api/v3/smc/levels?symbol=XAU/USD", headers=_auth())
    assert res.status == 200
    body = await res.json()
    assert body["schema_version"] == "v3.1"
    assert body["kind"] == "smc_levels"
    data = body["data"]
    # Required top-level keys
    for k in ("symbol", "current_price", "previous_day", "previous_week", "sessions"):
        assert k in data
    assert data["symbol"] == "XAU/USD"
    assert data["current_price"] == 100.0


async def test_smc_levels_previous_day_fields(aiohttp_client, tmp_path):
    runner = _StubSmcRunner()
    uds = _StubUds({900: [_make_m15_bar()], 86400: _make_d1_lookback()})
    client = await aiohttp_client(_build_app(runner=runner, uds=uds, tmp_path=tmp_path))
    res = await client.get("/api/v3/smc/levels?symbol=XAU/USD", headers=_auth())
    assert res.status == 200
    pd = (await res.json())["data"]["previous_day"]
    assert pd is not None
    # Exactly 4 fields per ADR §3.1.3
    assert set(pd.keys()) == {"high", "low", "close", "ts_ms"}
    # Last D1 bar = i=5 → high=115.0, low=85.0, close=105.0
    assert pd["high"] == 115.0
    assert pd["low"] == 85.0
    assert pd["close"] == 105.0
    assert isinstance(pd["ts_ms"], int)


async def test_smc_levels_previous_week_only_high_low(aiohttp_client, tmp_path):
    """ADR §3.1.3: previous_week intentionally has ONLY {high, low}.

    No close, no ts_ms. Contractual closure — frontend must not see them.
    """
    runner = _StubSmcRunner()
    uds = _StubUds({900: [_make_m15_bar()], 86400: _make_d1_lookback()})
    client = await aiohttp_client(_build_app(runner=runner, uds=uds, tmp_path=tmp_path))
    res = await client.get("/api/v3/smc/levels?symbol=XAU/USD", headers=_auth())
    pw = (await res.json())["data"]["previous_week"]
    assert pw is not None
    assert set(pw.keys()) == {"high", "low"}
    assert "close" not in pw
    assert "ts_ms" not in pw
    # 5 bars before previous_day: i=0..4 → highs=110..114, lows=90..86
    assert pw["high"] == 114.0
    assert pw["low"] == 86.0


async def test_smc_levels_sessions_structure(aiohttp_client, tmp_path):
    runner = _StubSmcRunner(
        session_states=[
            _StubSessionState(
                name="asia",
                active=False,
                current_high=101.0,
                current_low=99.0,
                previous_high=100.5,
                previous_low=99.5,
            ),
            _StubSessionState(
                name="london",
                active=True,
                current_high=102.0,
                current_low=98.5,
                previous_high=101.0,
                previous_low=99.0,
            ),
            _StubSessionState(name="newyork", active=False),
        ]
    )
    uds = _StubUds({900: [_make_m15_bar()], 86400: _make_d1_lookback()})
    client = await aiohttp_client(_build_app(runner=runner, uds=uds, tmp_path=tmp_path))
    res = await client.get("/api/v3/smc/levels?symbol=XAU/USD", headers=_auth())
    sessions = (await res.json())["data"]["sessions"]
    assert set(sessions.keys()) == {"asia", "london", "ny"}
    for name in ("asia", "london", "ny"):
        s = sessions[name]
        assert set(s.keys()) == {"high", "low", "complete", "swept_high", "swept_low"}

    # Asia: not active + has data → complete=True
    assert sessions["asia"]["high"] == 101.0
    assert sessions["asia"]["complete"] is True
    # Asia swept its own previous high (101 > 100.5) and low (99 < 99.5)
    assert sessions["asia"]["swept_high"] is True
    assert sessions["asia"]["swept_low"] is True
    # London: active + has data → complete=False
    assert sessions["london"]["complete"] is False
    assert sessions["london"]["swept_high"] is True  # 102 > 101
    assert sessions["london"]["swept_low"] is True  # 98.5 < 99


async def test_smc_levels_incomplete_session_null(aiohttp_client, tmp_path):
    """NY hasn't opened today → high=null, low=null, complete=false."""
    runner = _StubSmcRunner(
        session_states=[
            _StubSessionState(name="newyork"),
        ]
    )
    uds = _StubUds({900: [_make_m15_bar()], 86400: _make_d1_lookback()})
    client = await aiohttp_client(_build_app(runner=runner, uds=uds, tmp_path=tmp_path))
    res = await client.get("/api/v3/smc/levels?symbol=XAU/USD", headers=_auth())
    ny = (await res.json())["data"]["sessions"]["ny"]
    assert ny["high"] is None
    assert ny["low"] is None
    assert ny["complete"] is False
    assert ny["swept_high"] is False
    assert ny["swept_low"] is False


async def test_smc_levels_warmup_no_m15_bar(aiohttp_client, tmp_path):
    runner = _StubSmcRunner()
    uds = _StubUds({900: [], 86400: _make_d1_lookback()})
    client = await aiohttp_client(_build_app(runner=runner, uds=uds, tmp_path=tmp_path))
    res = await client.get("/api/v3/smc/levels?symbol=XAU/USD", headers=_auth())
    assert res.status == 503
    assert (await res.json())["data"]["code"] == "price_unavailable"


async def test_smc_levels_no_d1_data_nulls(aiohttp_client, tmp_path):
    """No D1 history yet → previous_day=null, previous_week=null but 200 OK."""
    runner = _StubSmcRunner()
    uds = _StubUds({900: [_make_m15_bar()], 86400: []})
    client = await aiohttp_client(_build_app(runner=runner, uds=uds, tmp_path=tmp_path))
    res = await client.get("/api/v3/smc/levels?symbol=XAU/USD", headers=_auth())
    assert res.status == 200
    data = (await res.json())["data"]
    assert data["previous_day"] is None
    assert data["previous_week"] is None


async def test_smc_levels_validation_errors(aiohttp_client, tmp_path):
    runner = _StubSmcRunner()
    uds = _StubUds({900: [_make_m15_bar()], 86400: _make_d1_lookback()})
    client = await aiohttp_client(_build_app(runner=runner, uds=uds, tmp_path=tmp_path))

    # missing symbol
    res = await client.get("/api/v3/smc/levels", headers=_auth())
    assert res.status == 400
    assert (await res.json())["data"]["code"] == "missing_symbol"

    # invalid symbol (not in config)
    res = await client.get("/api/v3/smc/levels?symbol=FOO/BAR", headers=_auth())
    assert res.status == 400
    assert (await res.json())["data"]["code"] == "symbol_invalid"


async def test_smc_levels_runner_unavailable(aiohttp_client, tmp_path):
    uds = _StubUds({900: [_make_m15_bar()], 86400: _make_d1_lookback()})
    client = await aiohttp_client(
        _build_app(runner=None, uds=uds, tmp_path=tmp_path, skip_runner=True)
    )
    res = await client.get("/api/v3/smc/levels?symbol=XAU/USD", headers=_auth())
    assert res.status == 503
    assert (await res.json())["data"]["code"] == "smc_runner_unavailable"
