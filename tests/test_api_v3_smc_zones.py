"""ADR-0059 §3.1.2 (slice 059.2) — GET /api/v3/smc/zones tests.

Covers:
* default response shape (active zones, M15, sorted by proximity_atr ASC)
* include_internal gating (default omits grade_score, ?include_internal=true exposes it)
* pagination (?offset/?limit) + total invariant
* kind filter (ob / fvg / liquidity)
* status filter (active / mitigated / all)
* current_price = M15 last complete bar close (cross-endpoint atomic)
* validation errors: missing/invalid tf, kind/status invalid, limit/offset bounds
* missing SMC runner → 503 smc_runner_unavailable
* warmup (no M15 complete bar) → 503 price_unavailable
* 100 KB payload cap → 503 payload_too_large
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


class _StubZone:
    """Minimal SmcZone shape for endpoint."""

    def __init__(
        self,
        *,
        zid: str,
        kind: str,
        high: float,
        low: float,
        status: str = "active",
        anchor_bar_ms: int = 1_700_000_000_000,
        end_ms: Optional[int] = None,
    ) -> None:
        self.id = zid
        self.kind = kind
        self.high = high
        self.low = low
        self.status = status
        self.anchor_bar_ms = anchor_bar_ms
        self.end_ms = end_ms


class _StubSnapshot:
    def __init__(self, zones: List[_StubZone]) -> None:
        self.zones = zones


class _StubSmcRunner:
    def __init__(
        self,
        *,
        snapshots_by_key: Dict[tuple, _StubSnapshot],
        grades_by_key: Optional[Dict[tuple, Dict[str, Dict[str, Any]]]] = None,
        atr_by_key: Optional[Dict[tuple, float]] = None,
    ) -> None:
        self._snapshots = snapshots_by_key
        self._grades = grades_by_key or {}
        self._atr = atr_by_key or {}

    def get_snapshot(self, symbol: str, tf_s: int) -> Optional[_StubSnapshot]:
        return self._snapshots.get((symbol, tf_s))

    def get_zone_grades(self, symbol: str, tf_s: int) -> Dict[str, Dict[str, Any]]:
        return self._grades.get((symbol, tf_s), {})

    def get_atr(self, symbol: str, tf_s: int, period: int = 14) -> float:
        return self._atr.get((symbol, tf_s), 0.0)

    def get_last_price(self, symbol: str) -> float:  # not used — kept for parity
        return 0.0


class _StubWindowResult:
    def __init__(self, bars_lwc: List[Dict[str, Any]]) -> None:
        self.bars_lwc = bars_lwc
        self.meta: Dict[str, Any] = {}
        self.warnings: List[str] = []


class _StubUds:
    """Stub matching `runtime.store.uds.UnifiedDataStore.read_window` shape."""

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
        "time": open_ms // 1000,
        "open": close - 0.5,
        "high": close + 1.0,
        "low": close - 1.0,
        "close": close,
        "volume": 10.0,
        "open_time_ms": open_ms,
        "close_time_ms": open_ms + 900_000,
        "complete": True,
    }


def _build_app(
    *,
    runner: _StubSmcRunner,
    uds: _StubUds,
    config_symbols: Optional[List[str]] = None,
    tmp_path: Optional[Path] = None,
) -> web.Application:
    from runtime.ws.ws_server import APP_FULL_CONFIG, APP_SMC_RUNNER, APP_UDS

    app = web.Application()
    app[APP_UDS] = cast(Any, uds)
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


def _make_zones_default() -> List[_StubZone]:
    """3 zones at varying distances from price=100.0."""
    return [
        # OB bull at 95-96 (distance 4 below price)
        _StubZone(zid="z_ob1", kind="ob_bull", high=96.0, low=95.0),
        # FVG bear at 102-103 (distance 2 above price)
        _StubZone(zid="z_fvg1", kind="fvg_bear", high=103.0, low=102.0),
        # OB bear at 110-111 (distance 10 above price — farthest)
        _StubZone(zid="z_ob2", kind="ob_bear", high=111.0, low=110.0),
    ]


# ────────────────────────────────────────────────────────────
# Tests
# ────────────────────────────────────────────────────────────


async def test_smc_zones_default_response_shape(aiohttp_client, tmp_path):
    runner = _StubSmcRunner(
        snapshots_by_key={("XAU/USD", 900): _StubSnapshot(_make_zones_default())},
        grades_by_key={
            ("XAU/USD", 900): {
                "z_ob1": {"score": 7, "grade": "A", "factors": ["sweep", "session_NY"]},
                "z_fvg1": {
                    "score": 9,
                    "grade": "A+",
                    "factors": ["sweep", "displacement"],
                },
                "z_ob2": {"score": 4, "grade": "B", "factors": ["sweep"]},
            }
        },
        atr_by_key={("XAU/USD", 900): 1.0},
    )
    uds = _StubUds({900: [_make_m15_bar(close=100.0)]})
    client = await aiohttp_client(_build_app(runner=runner, uds=uds, tmp_path=tmp_path))
    res = await client.get("/api/v3/smc/zones?symbol=XAU/USD&tf=M15", headers=_auth())
    assert res.status == 200
    body = await res.json()
    assert body["schema_version"] == "v3.1"
    assert body["kind"] == "smc_zones"
    data = body["data"]
    assert data["symbol"] == "XAU/USD"
    assert data["tf"] == "M15"
    assert data["current_price"] == 100.0
    assert data["total"] == 3
    assert data["limit"] == 50
    assert data["offset"] == 0
    zones = data["zones"]
    assert len(zones) == 3
    # Sorted by proximity_atr ASC: fvg1 (dist=2) → ob1 (dist=4) → ob2 (dist=10)
    assert [z["id"] for z in zones] == ["z_fvg1", "z_ob1", "z_ob2"]
    # Wire fields complete + grade_score absent by default (X28).
    z0 = zones[0]
    for k in (
        "id",
        "kind",
        "direction",
        "tf",
        "top",
        "bottom",
        "anchor_ms",
        "last_touch_ms",
        "status",
        "grade",
        "confluence_factors",
        "distance_pts",
        "proximity_atr",
    ):
        assert k in z0
    assert "grade_score" not in z0
    assert z0["kind"] == "fvg"
    assert z0["direction"] == "bearish"
    assert z0["distance_pts"] == 2.0
    assert z0["proximity_atr"] == 2.0
    assert z0["grade"] == "A+"
    assert z0["confluence_factors"] == ["sweep", "displacement"]


async def test_smc_zones_include_internal_exposes_score(aiohttp_client, tmp_path):
    runner = _StubSmcRunner(
        snapshots_by_key={("XAU/USD", 900): _StubSnapshot(_make_zones_default())},
        grades_by_key={
            ("XAU/USD", 900): {
                "z_fvg1": {"score": 9, "grade": "A+", "factors": []},
            }
        },
        atr_by_key={("XAU/USD", 900): 1.0},
    )
    uds = _StubUds({900: [_make_m15_bar(close=100.0)]})
    client = await aiohttp_client(_build_app(runner=runner, uds=uds, tmp_path=tmp_path))
    res = await client.get(
        "/api/v3/smc/zones?symbol=XAU/USD&tf=M15&include_internal=true", headers=_auth()
    )
    assert res.status == 200
    data = (await res.json())["data"]
    z0 = data["zones"][0]
    assert z0["id"] == "z_fvg1"
    assert z0["grade_score"] == 9


async def test_smc_zones_pagination_consistency(aiohttp_client, tmp_path):
    zones = [
        _StubZone(zid=f"z{i}", kind="ob_bull", high=90.0 - i, low=89.0 - i)
        for i in range(10)
    ]
    runner = _StubSmcRunner(
        snapshots_by_key={("XAU/USD", 900): _StubSnapshot(zones)},
        atr_by_key={("XAU/USD", 900): 1.0},
    )
    uds = _StubUds({900: [_make_m15_bar(close=100.0)]})
    client = await aiohttp_client(_build_app(runner=runner, uds=uds, tmp_path=tmp_path))

    res = await client.get(
        "/api/v3/smc/zones?symbol=XAU/USD&tf=M15&limit=4", headers=_auth()
    )
    page1 = (await res.json())["data"]
    assert page1["total"] == 10
    assert page1["limit"] == 4
    assert page1["offset"] == 0
    assert len(page1["zones"]) == 4

    res = await client.get(
        "/api/v3/smc/zones?symbol=XAU/USD&tf=M15&limit=4&offset=4", headers=_auth()
    )
    page2 = (await res.json())["data"]
    assert page2["total"] == 10
    assert page2["offset"] == 4
    assert len(page2["zones"]) == 4

    page1_ids = {z["id"] for z in page1["zones"]}
    page2_ids = {z["id"] for z in page2["zones"]}
    assert page1_ids.isdisjoint(page2_ids)


async def test_smc_zones_kind_filter(aiohttp_client, tmp_path):
    zones = [
        _StubZone(zid="ob1", kind="ob_bull", high=96.0, low=95.0),
        _StubZone(zid="fvg1", kind="fvg_bear", high=103.0, low=102.0),
        _StubZone(zid="prem1", kind="premium", high=120.0, low=115.0),
    ]
    runner = _StubSmcRunner(
        snapshots_by_key={("XAU/USD", 900): _StubSnapshot(zones)},
        atr_by_key={("XAU/USD", 900): 1.0},
    )
    uds = _StubUds({900: [_make_m15_bar(close=100.0)]})
    client = await aiohttp_client(_build_app(runner=runner, uds=uds, tmp_path=tmp_path))

    res_ob = await client.get(
        "/api/v3/smc/zones?symbol=XAU/USD&tf=M15&kind=ob", headers=_auth()
    )
    assert {z["id"] for z in (await res_ob.json())["data"]["zones"]} == {"ob1"}

    res_fvg = await client.get(
        "/api/v3/smc/zones?symbol=XAU/USD&tf=M15&kind=fvg", headers=_auth()
    )
    assert {z["id"] for z in (await res_fvg.json())["data"]["zones"]} == {"fvg1"}

    res_liq = await client.get(
        "/api/v3/smc/zones?symbol=XAU/USD&tf=M15&kind=liquidity", headers=_auth()
    )
    assert {z["id"] for z in (await res_liq.json())["data"]["zones"]} == {"prem1"}


async def test_smc_zones_status_filter(aiohttp_client, tmp_path):
    zones = [
        _StubZone(zid="active1", kind="ob_bull", high=96.0, low=95.0, status="active"),
        _StubZone(
            zid="mit1",
            kind="ob_bull",
            high=94.0,
            low=93.0,
            status="mitigated",
            end_ms=1_700_000_900_000,
        ),
    ]
    runner = _StubSmcRunner(
        snapshots_by_key={("XAU/USD", 900): _StubSnapshot(zones)},
        atr_by_key={("XAU/USD", 900): 1.0},
    )
    uds = _StubUds({900: [_make_m15_bar(close=100.0)]})
    client = await aiohttp_client(_build_app(runner=runner, uds=uds, tmp_path=tmp_path))

    # default (status=active)
    res = await client.get("/api/v3/smc/zones?symbol=XAU/USD&tf=M15", headers=_auth())
    assert {z["id"] for z in (await res.json())["data"]["zones"]} == {"active1"}

    res = await client.get(
        "/api/v3/smc/zones?symbol=XAU/USD&tf=M15&status=mitigated", headers=_auth()
    )
    assert {z["id"] for z in (await res.json())["data"]["zones"]} == {"mit1"}

    res = await client.get(
        "/api/v3/smc/zones?symbol=XAU/USD&tf=M15&status=all", headers=_auth()
    )
    assert {z["id"] for z in (await res.json())["data"]["zones"]} == {"active1", "mit1"}


async def test_smc_zones_validation_errors(aiohttp_client, tmp_path):
    runner = _StubSmcRunner(
        snapshots_by_key={("XAU/USD", 900): _StubSnapshot([])},
        atr_by_key={("XAU/USD", 900): 1.0},
    )
    uds = _StubUds({900: [_make_m15_bar(close=100.0)]})
    client = await aiohttp_client(_build_app(runner=runner, uds=uds, tmp_path=tmp_path))

    # missing tf
    res = await client.get("/api/v3/smc/zones?symbol=XAU/USD", headers=_auth())
    assert res.status == 400
    assert (await res.json())["data"]["code"] == "missing_tf"

    # invalid tf
    res = await client.get("/api/v3/smc/zones?symbol=XAU/USD&tf=M5", headers=_auth())
    assert res.status == 400
    assert (await res.json())["data"]["code"] == "tf_invalid"

    # invalid kind
    res = await client.get(
        "/api/v3/smc/zones?symbol=XAU/USD&tf=M15&kind=foo", headers=_auth()
    )
    assert res.status == 400
    assert (await res.json())["data"]["code"] == "kind_invalid"

    # invalid status
    res = await client.get(
        "/api/v3/smc/zones?symbol=XAU/USD&tf=M15&status=bar", headers=_auth()
    )
    assert res.status == 400
    assert (await res.json())["data"]["code"] == "status_invalid"

    # limit too high
    res = await client.get(
        "/api/v3/smc/zones?symbol=XAU/USD&tf=M15&limit=999", headers=_auth()
    )
    assert res.status == 400
    assert (await res.json())["data"]["code"] == "limit_above_max"

    # offset negative
    res = await client.get(
        "/api/v3/smc/zones?symbol=XAU/USD&tf=M15&offset=-1", headers=_auth()
    )
    assert res.status == 400
    assert (await res.json())["data"]["code"] == "offset_invalid"


async def test_smc_zones_warmup_no_m15_bar(aiohttp_client, tmp_path):
    runner = _StubSmcRunner(
        snapshots_by_key={("XAU/USD", 900): _StubSnapshot([])},
        atr_by_key={("XAU/USD", 900): 1.0},
    )
    uds = _StubUds({900: []})  # no bars
    client = await aiohttp_client(_build_app(runner=runner, uds=uds, tmp_path=tmp_path))
    res = await client.get("/api/v3/smc/zones?symbol=XAU/USD&tf=M15", headers=_auth())
    assert res.status == 503
    assert (await res.json())["data"]["code"] == "price_unavailable"


async def test_smc_zones_runner_unavailable(aiohttp_client, tmp_path):
    """When SMC_RUNNER not bound on app — 503 smc_runner_unavailable."""
    from runtime.ws.ws_server import APP_FULL_CONFIG, APP_UDS

    app = web.Application()
    app[APP_UDS] = cast(Any, _StubUds({900: [_make_m15_bar(close=100.0)]}))
    app[APP_FULL_CONFIG] = {"symbols": ["XAU/USD"]}
    signals_dir = tmp_path / "_signals"
    signals_dir.mkdir(exist_ok=True)
    ep.register_routes(
        app,
        token_store=_StubTokenStore(),  # type: ignore[arg-type]
        signals_dir=str(signals_dir),
        audit_dir=None,
    )
    client = await aiohttp_client(app)
    res = await client.get("/api/v3/smc/zones?symbol=XAU/USD&tf=M15", headers=_auth())
    assert res.status == 503
    assert (await res.json())["data"]["code"] == "smc_runner_unavailable"


async def test_smc_zones_payload_cap(aiohttp_client, tmp_path):
    """Synthesize 200 zones with verbose factors → > 100 KB → 503."""
    big_factors = [
        f"factor_{i}_with_long_descriptive_name_for_padding" for i in range(20)
    ]
    zones = [
        _StubZone(
            zid=f"zone_id_padding_{i:04d}", kind="ob_bull", high=90.0 - i, low=89.0 - i
        )
        for i in range(200)
    ]
    grades = {
        f"zone_id_padding_{i:04d}": {"score": 5, "grade": "A", "factors": big_factors}
        for i in range(200)
    }
    runner = _StubSmcRunner(
        snapshots_by_key={("XAU/USD", 900): _StubSnapshot(zones)},
        grades_by_key={("XAU/USD", 900): grades},
        atr_by_key={("XAU/USD", 900): 1.0},
    )
    uds = _StubUds({900: [_make_m15_bar(close=100.0)]})
    client = await aiohttp_client(_build_app(runner=runner, uds=uds, tmp_path=tmp_path))
    # Force big response by requesting max limit.
    res = await client.get(
        "/api/v3/smc/zones?symbol=XAU/USD&tf=M15&limit=200", headers=_auth()
    )
    assert res.status == 503
    assert (await res.json())["data"]["code"] == "payload_too_large"


# ────────────────────────────────────────────────────────────
# ADR-0059 §5.5 / 059.7 — cursor pagination tests (F-S2-001)
# ────────────────────────────────────────────────────────────


def _make_zones_for_pagination(n: int) -> List[_StubZone]:
    """n bullish OB zones spaced 1 pt apart below price=100. Sorted by
    proximity ASC + id lex ASC → deterministic ordering for cursor tests.
    """
    return [
        _StubZone(
            zid=f"z{i:03d}",
            kind="ob_bull",
            high=99.0 - i,
            low=98.0 - i,
        )
        for i in range(n)
    ]


async def test_smc_zones_cursor_basic_walk(aiohttp_client, tmp_path):
    """First page issues opaque cursor → second page resumes via cursor →
    last page returns next_cursor=null. Pages cover the full set with no gaps
    or overlaps and respect the new sort key (proximity, id).
    """
    zones = _make_zones_for_pagination(10)
    runner = _StubSmcRunner(
        snapshots_by_key={("XAU/USD", 900): _StubSnapshot(zones)},
        atr_by_key={("XAU/USD", 900): 1.0},
    )
    uds = _StubUds({900: [_make_m15_bar(close=100.0)]})
    client = await aiohttp_client(_build_app(runner=runner, uds=uds, tmp_path=tmp_path))

    # Page 1 — no cursor, limit=4.
    res = await client.get(
        "/api/v3/smc/zones?symbol=XAU/USD&tf=M15&limit=4", headers=_auth()
    )
    assert res.status == 200
    page1 = (await res.json())["data"]
    assert page1["total"] == 10
    assert "snapshot_id" in page1
    assert isinstance(page1["snapshot_id"], str) and len(page1["snapshot_id"]) == 16
    assert page1["next_cursor"] is not None
    assert isinstance(page1["next_cursor"], str)
    page1_ids = [z["id"] for z in page1["zones"]]
    assert page1_ids == ["z000", "z001", "z002", "z003"]

    # Page 2 — pass cursor.
    cursor = page1["next_cursor"]
    res = await client.get(
        f"/api/v3/smc/zones?symbol=XAU/USD&tf=M15&limit=4&cursor={cursor}",
        headers=_auth(),
    )
    page2 = (await res.json())["data"]
    page2_ids = [z["id"] for z in page2["zones"]]
    assert page2_ids == ["z004", "z005", "z006", "z007"]
    assert page2["snapshot_id"] == page1["snapshot_id"]
    assert page2["next_cursor"] is not None
    # No overlap between pages.
    assert set(page1_ids).isdisjoint(set(page2_ids))

    # Page 3 — final, only 2 left.
    cursor = page2["next_cursor"]
    res = await client.get(
        f"/api/v3/smc/zones?symbol=XAU/USD&tf=M15&limit=4&cursor={cursor}",
        headers=_auth(),
    )
    page3 = (await res.json())["data"]
    assert [z["id"] for z in page3["zones"]] == ["z008", "z009"]
    assert page3["next_cursor"] is None


async def test_smc_zones_cursor_invalid_and_corrupt(aiohttp_client, tmp_path):
    """Bad base64 → cursor_invalid 400. Decoded JSON missing fields →
    cursor_corrupt 400. Empty cursor string is treated as no cursor.
    """
    runner = _StubSmcRunner(
        snapshots_by_key={("XAU/USD", 900): _StubSnapshot([])},
        atr_by_key={("XAU/USD", 900): 1.0},
    )
    uds = _StubUds({900: [_make_m15_bar(close=100.0)]})
    client = await aiohttp_client(_build_app(runner=runner, uds=uds, tmp_path=tmp_path))

    # Bad base64 — '!' is outside the urlsafe alphabet.
    res = await client.get(
        "/api/v3/smc/zones?symbol=XAU/USD&tf=M15&cursor=!!!not_b64!!!",
        headers=_auth(),
    )
    assert res.status == 400
    assert (await res.json())["data"]["code"] == "cursor_invalid"

    # Valid base64 of '{"foo": 1}' → JSON OK but missing required fields.
    import base64
    import json as _json

    raw = (
        base64.urlsafe_b64encode(_json.dumps({"foo": 1}).encode()).rstrip(b"=").decode()
    )
    res = await client.get(
        f"/api/v3/smc/zones?symbol=XAU/USD&tf=M15&cursor={raw}", headers=_auth()
    )
    assert res.status == 400
    assert (await res.json())["data"]["code"] == "cursor_corrupt"

    # Valid base64 of garbage non-JSON bytes → cursor_corrupt.
    raw = base64.urlsafe_b64encode(b"\xff\xfeNOT_JSON").rstrip(b"=").decode()
    res = await client.get(
        f"/api/v3/smc/zones?symbol=XAU/USD&tf=M15&cursor={raw}", headers=_auth()
    )
    assert res.status == 400
    assert (await res.json())["data"]["code"] == "cursor_corrupt"


async def test_smc_zones_cursor_stale_snapshot(aiohttp_client, tmp_path):
    """Cursor's snapshot_id no longer matches → response carries
    `data.warnings: [cursor_stale]` and still serves a best-effort next page.
    """
    # Page 1 over the original 10 zones.
    zones_v1 = _make_zones_for_pagination(10)
    snap_holder: Dict[str, _StubSnapshot] = {
        "snap": _StubSnapshot(zones_v1),
    }

    class _MutatingRunner(_StubSmcRunner):
        def get_snapshot(self, symbol: str, tf_s: int) -> Optional[_StubSnapshot]:
            return snap_holder["snap"]

    runner = _MutatingRunner(
        snapshots_by_key={},
        atr_by_key={("XAU/USD", 900): 1.0},
    )
    uds = _StubUds({900: [_make_m15_bar(close=100.0)]})
    client = await aiohttp_client(_build_app(runner=runner, uds=uds, tmp_path=tmp_path))

    res = await client.get(
        "/api/v3/smc/zones?symbol=XAU/USD&tf=M15&limit=4", headers=_auth()
    )
    page1 = (await res.json())["data"]
    cursor = page1["next_cursor"]
    snap_id_v1 = page1["snapshot_id"]
    assert cursor is not None

    # Mutate snapshot — drop one of the served zones, add a fresh one.
    zones_v2 = _make_zones_for_pagination(11)[1:]  # drop z000, add z010
    snap_holder["snap"] = _StubSnapshot(list(zones_v2))

    res = await client.get(
        f"/api/v3/smc/zones?symbol=XAU/USD&tf=M15&limit=4&cursor={cursor}",
        headers=_auth(),
    )
    page2 = (await res.json())["data"]
    assert page2["snapshot_id"] != snap_id_v1
    assert page2.get("warnings") == ["cursor_stale"]
    # Best-effort: still returns zones with proximity > cursor's last_proximity.
    page2_ids = [z["id"] for z in page2["zones"]]
    assert all(zid > "z003" for zid in page2_ids)
    assert len(page2_ids) == 4


async def test_smc_zones_cursor_overrides_offset(aiohttp_client, tmp_path):
    """When both ?cursor and ?offset are supplied, cursor wins (offset
    silently ignored per ADR-0059 §5.5).
    """
    zones = _make_zones_for_pagination(10)
    runner = _StubSmcRunner(
        snapshots_by_key={("XAU/USD", 900): _StubSnapshot(zones)},
        atr_by_key={("XAU/USD", 900): 1.0},
    )
    uds = _StubUds({900: [_make_m15_bar(close=100.0)]})
    client = await aiohttp_client(_build_app(runner=runner, uds=uds, tmp_path=tmp_path))

    # Issue page 1 with limit=4 to grab a cursor.
    res = await client.get(
        "/api/v3/smc/zones?symbol=XAU/USD&tf=M15&limit=4", headers=_auth()
    )
    cursor = (await res.json())["data"]["next_cursor"]

    # Page 2 with both cursor and bogus offset=99 → cursor wins.
    res = await client.get(
        f"/api/v3/smc/zones?symbol=XAU/USD&tf=M15&limit=4&offset=99&cursor={cursor}",
        headers=_auth(),
    )
    page2 = (await res.json())["data"]
    assert [z["id"] for z in page2["zones"]] == ["z004", "z005", "z006", "z007"]
