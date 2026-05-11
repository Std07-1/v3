"""ADR-0059 §6.1 (slice 059.7) — integration test suite (F-S2-002).

End-to-end coverage of the v3 API stack assembled the way ws_server does in
production: token store + audit middleware + kill switch + analysis routes.
Verifies cross-cutting invariants that single-endpoint suites can't:

* E2E flow: token issue → request → 200 envelope → audit JSONL line written.
* Token revoked mid-flight → next request 401 (lookup contract).
* Kill switch ON → /bars/* and /smc/* return 503 with structured envelope,
  while /signals/* keeps serving (ADR-0059 §3.2 — signals never blocked).
* Burst traffic (≥400 reqs across all endpoints) → no 5xx, audit line count
  matches request count exactly.
* Schema migration: server emits "v3.1" for analysis endpoints (bars, smc/*)
  and "v3.0" for legacy endpoints (signals/bias/narrative/macro). A v3.0
  reader that ignores unknown fields handles the v3.1 envelope cleanly.

The stubs below are intentionally minimal — they reproduce only what the
endpoints under test actually call. Heavy SmcRunner internals are out of
scope; that's the slice 059.2/059.3 unit suites' job.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, cast
from unittest.mock import MagicMock

import pytest
from aiohttp import web

from runtime.api_v3 import endpoints as ep
from runtime.api_v3.kill_switch import register_kill_switch

pytestmark = pytest.mark.asyncio


VALID_TOKEN = "tk_" + ("a" * 64)
SECOND_TOKEN = "tk_" + ("b" * 64)


# ────────────────────────────────────────────────────────────
# Stubs (shared across all integration scenarios)
# ────────────────────────────────────────────────────────────


class _Record:
    def __init__(self, consumer: str = "integration_consumer") -> None:
        self.consumer = consumer
        self.scope = "read"
        self.created = "2026-04-01T00:00:00Z"
        self.expires = "2026-12-31T00:00:00Z"


class _MutableTokenStore:
    """Token store with runtime revoke — needed for the mid-flight test."""

    def __init__(self, tokens: Dict[str, _Record]) -> None:
        self._tokens = dict(tokens)

    def lookup(self, token: Optional[str]):
        return self._tokens.get(token or "")

    def revoke(self, token: str) -> None:
        self._tokens.pop(token, None)


class _Zone:
    def __init__(self, *, zid: str, kind: str, high: float, low: float) -> None:
        self.id = zid
        self.kind = kind
        self.high = high
        self.low = low
        self.status = "active"
        self.anchor_bar_ms = 1_700_000_000_000
        self.end_ms: Optional[int] = None


class _Snapshot:
    def __init__(self, zones: List[_Zone]) -> None:
        self.zones = zones


class _SessionState:
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


class _Runner:
    """SmcRunner stub covering all three analysis endpoints."""

    def __init__(self, *, zones: List[_Zone], sessions: List[_SessionState]) -> None:
        self._zones = zones
        self._sessions = sessions

    # smc/zones path
    def get_snapshot(self, symbol: str, tf_s: int) -> Optional[_Snapshot]:
        return _Snapshot(self._zones)

    def get_zone_grades(self, symbol: str, tf_s: int) -> Dict[str, Any]:
        return {}

    def get_atr(self, symbol: str, tf_s: int, period: int = 14) -> float:
        return 1.0

    def get_last_price(self, symbol: str) -> float:
        return 0.0

    # smc/levels path
    def get_session_states(
        self, symbol: str, current_time_ms: int
    ) -> List[_SessionState]:
        return list(self._sessions)


class _WindowResult:
    def __init__(self, bars: List[Dict[str, Any]]) -> None:
        self.bars_lwc = bars
        self.meta: Dict[str, Any] = {}
        self.warnings: List[str] = []


class _Uds:
    def __init__(self, bars_by_tf: Dict[int, List[Dict[str, Any]]]) -> None:
        self._bars_by_tf = bars_by_tf

    def read_window(self, spec: Any, _policy: Any) -> _WindowResult:
        rows = list(self._bars_by_tf.get(spec.tf_s, []))
        return _WindowResult(rows[-spec.limit :] if spec.limit else rows)


# ────────────────────────────────────────────────────────────
# Fixtures: bar/zone factories + full stack assembly
# ────────────────────────────────────────────────────────────


def _make_bars(tf_s: int, n: int) -> List[Dict[str, Any]]:
    base_ms = 1_700_000_000_000
    tf_ms = tf_s * 1000
    return [
        {
            "open": 100.0 + i * 0.1,
            "high": 101.0 + i * 0.1,
            "low": 99.0 + i * 0.1,
            "close": 100.5 + i * 0.1,
            "volume": 10.0,
            "open_time_ms": base_ms + i * tf_ms,
            "close_time_ms": base_ms + (i + 1) * tf_ms,
            "complete": True,
            "time": (base_ms + i * tf_ms) // 1000,
        }
        for i in range(n)
    ]


def _make_d1_lookback(n: int = 6) -> List[Dict[str, Any]]:
    """Enough D1 history for /smc/levels prev_day + prev_week."""
    base_ms = 1_700_000_000_000
    return [
        {
            "open": (110.0 + i + 90.0 - i) / 2,
            "high": 110.0 + i,
            "low": 90.0 - i,
            "close": 100.0 + i,
            "volume": 1000.0,
            "open_time_ms": base_ms + i * 86_400_000,
            "close_time_ms": base_ms + (i + 1) * 86_400_000,
            "complete": True,
        }
        for i in range(n)
    ]


def _make_zones(n: int) -> List[_Zone]:
    return [
        _Zone(zid=f"int_z_{i:03d}", kind="ob_bull", high=99.0 - i, low=98.0 - i)
        for i in range(n)
    ]


def _make_sessions() -> List[_SessionState]:
    return [
        _SessionState(
            name="asia",
            active=False,
            current_high=101.0,
            current_low=99.0,
            previous_high=100.5,
            previous_low=99.5,
        ),
        _SessionState(name="london", active=True, current_high=102.0, current_low=98.5),
        _SessionState(name="newyork", active=False),
    ]


def _build_full_stack(
    *,
    tmp_path: Path,
    token_store: _MutableTokenStore,
    analysis_enabled: bool = True,
    kill_flag_set: bool = False,
) -> tuple[web.Application, Path]:
    """Assemble the full analysis stack the way ws_server.create_app does.

    Returns the app + audit_dir path so tests can inspect the JSONL trail.
    """
    from runtime.ws.ws_server import APP_FULL_CONFIG, APP_SMC_RUNNER, APP_UDS

    # Per-test audit dir keeps assertions hermetic.
    audit_dir = tmp_path / "_audit"
    signals_dir = tmp_path / "_signals"
    signals_dir.mkdir(exist_ok=True)

    runner = _Runner(zones=_make_zones(8), sessions=_make_sessions())
    uds = _Uds(
        {
            900: _make_bars(900, 30),  # M15
            3600: _make_bars(3600, 30),
            14400: _make_bars(14400, 30),
            86400: _make_d1_lookback(),
        }
    )

    app = web.Application()
    # Kill switch must be registered FIRST so its middleware runs ahead of
    # the per-request audit trailer.
    redis_client = MagicMock()
    redis_client.exists = MagicMock(return_value=1 if kill_flag_set else 0)
    register_kill_switch(
        app,
        redis_client=redis_client,
        namespace="integration_ns",
        analysis_enabled=analysis_enabled,
    )
    app[APP_UDS] = cast(Any, uds)
    app[APP_SMC_RUNNER] = cast(Any, runner)
    app[APP_FULL_CONFIG] = {"symbols": ["XAU/USD"]}
    ep.register_routes(
        app,
        token_store=cast(Any, token_store),
        signals_dir=str(signals_dir),
        audit_dir=str(audit_dir),
    )
    return app, audit_dir


def _auth(token: str = VALID_TOKEN) -> Dict[str, str]:
    return {"X-API-Key": token}


def _read_audit_lines(audit_dir: Path) -> List[Dict[str, Any]]:
    """Aggregate every JSONL line written across all daily files."""
    if not audit_dir.exists():
        return []
    out: List[Dict[str, Any]] = []
    for name in sorted(os.listdir(audit_dir)):
        path = audit_dir / name
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    out.append(json.loads(line))
    return out


# ────────────────────────────────────────────────────────────
# E2E happy path — all 3 analysis endpoints + audit trail
# ────────────────────────────────────────────────────────────


async def test_e2e_bars_window_writes_audit(aiohttp_client, tmp_path):
    store = _MutableTokenStore({VALID_TOKEN: _Record()})
    app, audit_dir = _build_full_stack(tmp_path=tmp_path, token_store=store)
    client = await aiohttp_client(app)
    res = await client.get("/api/v3/bars/window?symbol=XAU/USD", headers=_auth())
    assert res.status == 200
    body = await res.json()
    assert body["schema_version"] == "v3.1"
    assert body["kind"] == "bars_window"
    audit = _read_audit_lines(audit_dir)
    assert len(audit) == 1
    assert audit[0]["path"] == "/api/v3/bars/window"
    assert audit[0]["status"] == 200
    assert audit[0]["consumer"] == "integration_consumer"


async def test_e2e_smc_zones_writes_audit(aiohttp_client, tmp_path):
    store = _MutableTokenStore({VALID_TOKEN: _Record()})
    app, audit_dir = _build_full_stack(tmp_path=tmp_path, token_store=store)
    client = await aiohttp_client(app)
    res = await client.get("/api/v3/smc/zones?symbol=XAU/USD&tf=M15", headers=_auth())
    assert res.status == 200
    body = await res.json()
    assert body["schema_version"] == "v3.1"
    assert body["kind"] == "smc_zones"
    audit = _read_audit_lines(audit_dir)
    assert len(audit) == 1
    assert audit[0]["path"] == "/api/v3/smc/zones"


async def test_e2e_smc_levels_writes_audit(aiohttp_client, tmp_path):
    store = _MutableTokenStore({VALID_TOKEN: _Record()})
    app, audit_dir = _build_full_stack(tmp_path=tmp_path, token_store=store)
    client = await aiohttp_client(app)
    res = await client.get("/api/v3/smc/levels?symbol=XAU/USD", headers=_auth())
    assert res.status == 200
    body = await res.json()
    assert body["schema_version"] == "v3.1"
    assert body["kind"] == "smc_levels"
    audit = _read_audit_lines(audit_dir)
    assert len(audit) == 1
    assert audit[0]["path"] == "/api/v3/smc/levels"


# ────────────────────────────────────────────────────────────
# Token revoke mid-flight
# ────────────────────────────────────────────────────────────


async def test_token_revoke_mid_flight_blocks_next_request(aiohttp_client, tmp_path):
    """Token good → 200. Revoke → next request 401 unauthorized."""
    store = _MutableTokenStore({VALID_TOKEN: _Record()})
    app, _ = _build_full_stack(tmp_path=tmp_path, token_store=store)
    client = await aiohttp_client(app)

    res = await client.get("/api/v3/bars/window?symbol=XAU/USD", headers=_auth())
    assert res.status == 200

    store.revoke(VALID_TOKEN)
    res = await client.get("/api/v3/bars/window?symbol=XAU/USD", headers=_auth())
    assert res.status == 401
    body = await res.json()
    assert body["data"]["code"] in {"unauthorized", "invalid_token", "invalid_api_key"}


# ────────────────────────────────────────────────────────────
# Kill switch — analysis blocked, signals pass
# ────────────────────────────────────────────────────────────


async def test_kill_switch_blocks_analysis_keeps_signals(aiohttp_client, tmp_path):
    """Redis kill flag set → analysis endpoints 503, signals/* 200 (no auth
    required for read endpoints — see signals_latest spec)."""
    store = _MutableTokenStore({VALID_TOKEN: _Record()})
    app, _ = _build_full_stack(tmp_path=tmp_path, token_store=store, kill_flag_set=True)
    client = await aiohttp_client(app)

    for path in (
        "/api/v3/bars/window?symbol=XAU/USD",
        "/api/v3/smc/zones?symbol=XAU/USD&tf=M15",
        "/api/v3/smc/levels?symbol=XAU/USD",
    ):
        res = await client.get(path, headers=_auth())
        assert res.status == 503, f"{path} expected 503, got {res.status}"
        body = await res.json()
        assert body["kind"] == "error"
        assert body["data"]["code"] == "analysis_disabled_runtime"

    # /signals/latest must remain reachable (returns its own envelope or 404
    # depending on whether a signal file exists; both are acceptable here).
    res = await client.get("/api/v3/signals/latest?symbol=XAU/USD", headers=_auth())
    assert res.status in {200, 404}, f"signals/latest got {res.status}"


# ────────────────────────────────────────────────────────────
# Burst traffic — audit trail integrity under load
# ────────────────────────────────────────────────────────────


async def test_burst_traffic_audit_count_matches_no_5xx(aiohttp_client, tmp_path):
    """Fire ≥400 mixed requests; assert no 5xx + audit line count == requests."""
    store = _MutableTokenStore({VALID_TOKEN: _Record()})
    app, audit_dir = _build_full_stack(tmp_path=tmp_path, token_store=store)
    client = await aiohttp_client(app)

    paths = (
        ["/api/v3/bars/window?symbol=XAU/USD"] * 200
        + ["/api/v3/smc/zones?symbol=XAU/USD&tf=M15"] * 150
        + ["/api/v3/smc/levels?symbol=XAU/USD"] * 100
    )
    statuses: List[int] = []
    for p in paths:
        res = await client.get(p, headers=_auth())
        statuses.append(res.status)
        await res.read()  # drain

    assert len(statuses) == 450
    # No 5xx anywhere.
    bad = [s for s in statuses if 500 <= s < 600]
    assert not bad, f"unexpected 5xx statuses: {set(bad)}"
    # Each request must produce exactly one audit line.
    audit = _read_audit_lines(audit_dir)
    assert len(audit) == 450


# ────────────────────────────────────────────────────────────
# Schema migration — version per kind + tolerant reader
# ────────────────────────────────────────────────────────────


async def test_schema_version_per_endpoint_kind(aiohttp_client, tmp_path):
    """Analysis endpoints emit v3.1, legacy endpoints emit v3.0."""
    store = _MutableTokenStore({VALID_TOKEN: _Record()})
    app, _ = _build_full_stack(tmp_path=tmp_path, token_store=store)
    client = await aiohttp_client(app)

    v31_paths = [
        "/api/v3/bars/window?symbol=XAU/USD",
        "/api/v3/smc/zones?symbol=XAU/USD&tf=M15",
        "/api/v3/smc/levels?symbol=XAU/USD",
    ]
    for p in v31_paths:
        res = await client.get(p, headers=_auth())
        assert res.status == 200, f"{p}: {res.status}"
        body = await res.json()
        assert body["schema_version"] == "v3.1", f"{p}: {body['schema_version']}"

    # Legacy endpoints — signals/latest. May 404 if no signal file; only assert
    # version when the envelope shape is returned.
    res = await client.get("/api/v3/signals/latest?symbol=XAU/USD", headers=_auth())
    if res.status == 200:
        body = await res.json()
        assert body.get("schema_version") == "v3.0"


async def test_v30_reader_tolerates_v31_envelope(aiohttp_client, tmp_path):
    """Forward-compat: a consumer pinned to v3.0 keys (schema_version, kind,
    server_ts, data) must still parse v3.1 responses without crashing on the
    new fields (snapshot_id, next_cursor, warnings)."""
    store = _MutableTokenStore({VALID_TOKEN: _Record()})
    app, _ = _build_full_stack(tmp_path=tmp_path, token_store=store)
    client = await aiohttp_client(app)

    res = await client.get("/api/v3/smc/zones?symbol=XAU/USD&tf=M15", headers=_auth())
    body = await res.json()

    # v3.0 contract — these four keys MUST exist.
    for required in ("schema_version", "kind", "server_ts", "data"):
        assert required in body, f"missing v3.0 key: {required}"
    # New v3.1 fields are inside data.* — a v3.0 reader that whitelists fields
    # would silently drop them (forward compat). Here we simply assert they
    # exist and have stable types.
    data = body["data"]
    assert "snapshot_id" in data
    assert isinstance(data["snapshot_id"], str)
    assert "next_cursor" in data
    assert data["next_cursor"] is None or isinstance(data["next_cursor"], str)
