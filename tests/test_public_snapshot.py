"""ADR-0086 — публічний санітизований знімок: тести як специфікація контракту.

Ядро інваріантів: whitelist-only (приватне НІКОЛИ не витікає), підписи рівнів =
звірка зі сценарієм (не вигаданий текст), focus→watching непорожній (синтез).
"""

from __future__ import annotations

import json

from runtime.api.public_snapshot import build_public_snapshot

_NOW_MS = 1_783_790_000_000


def _state(**overrides):
    base = {
        "ts_ms": str(_NOW_MS - 5_000),
        "mood": "calm",
        "health": "ok",
        "circuit_breaker": "0",
        "has_virtual_position": "0",
        "active_scenarios": "0",
        "inner_thought": "London killzone близько.",
        "next_wake_ms": str(_NOW_MS + 1_800_000),
        "next_wake_reason": "London open",
        "market_session": "london",
        # приватне, що НЕ має витекти:
        "budget_today_usd": "1.42",
        "budget_pct": "31",
        "calls_today": "14",
        "model_current": "sonnet",
        "last_error": "Traceback /opt/smc-trader-v3/bot/core.py:854",
    }
    base.update(overrides)
    return base


def _scenario(**overrides):
    base = {
        "id": "xau_short_london_0618",
        "symbol": "XAU/USD",
        "direction": "short",
        "thesis": "D1/H4 bearish cascade в силі.",
        "entry_zone_low": 4328.0,
        "entry_zone_high": 4335.0,
        "targets": [4305.0, 4267.0],
        "invalidation": 4348.0,
        "status": "waiting",
        "confidence": 0.55,
        "conviction_reasoning": "ВНУТРІШНЄ: BOE ризик, знижена впевненість",
        "checkpoints": ["внутрішній чекпоінт"],
        "trigger": {"kind": "price_cross", "level": 4325, "direction": "below"},
        "session": "London",
        "grade": "A",
    }
    base.update(overrides)
    return base


def _wake(level, direction="above", kind="price_cross"):
    return {"kind": kind, "params": {"level": level, "direction": direction, "symbol": "XAU/USD"}}


def test_private_fields_never_leak_anywhere_in_snapshot():
    directives = {
        "mood": "analytical",
        "active_scenario": _scenario(),
        "kill_switch_active": False,
        "token_usage_today": {"usd": 1.42},
        "workspace_items": ["приватна нотатка"],
    }
    snapshot = build_public_snapshot(_state(), directives, [_wake(4348)], _NOW_MS)
    serialized = json.dumps(snapshot, ensure_ascii=False)
    for forbidden in (
        "budget", "calls_today", "model_current", "token_usage",
        "conviction_reasoning", "ВНУТРІШНЄ", "checkpoints", "workspace",
        "trigger", "Traceback", "/opt/",
        # rev1 (owner 2026-07-12): сирі думки не публічні — голос = теза сценарію
        "inner_thought", "killzone близько",
    ):
        assert forbidden not in serialized, f"витік приватного поля: {forbidden}"


def test_last_error_redacted_to_marker_not_text():
    snapshot = build_public_snapshot(_state(), {"active_scenario": _scenario()}, [], _NOW_MS)
    assert snapshot["presence"]["last_error"] == "1"
    snapshot_ok = build_public_snapshot(
        _state(last_error=""), {"active_scenario": _scenario()}, [], _NOW_MS
    )
    assert snapshot_ok["presence"]["last_error"] == ""


def test_level_labels_are_matched_against_scenario_structure():
    conditions = [_wake(4348), _wake(4335), _wake(4305, "below"), _wake(4325, "below"), _wake(4400)]
    snapshot = build_public_snapshot(
        _state(), {"active_scenario": _scenario()}, conditions, _NOW_MS
    )
    roles = {item["level"]: item["role"] for item in snapshot["watching"]}
    assert roles[4348.0] == "invalidation"
    assert roles[4335.0] == "entry"
    assert roles[4305.0] == "target"
    assert roles[4325.0] == "trigger"
    assert roles[4400.0] == "watch"
    # сорт зверху вниз як драбина
    levels = [item["level"] for item in snapshot["watching"]]
    assert levels == sorted(levels, reverse=True)


def test_focus_null_and_watching_empty_without_scenario():
    snapshot = build_public_snapshot(_state(), {}, [], _NOW_MS)
    assert snapshot["focus"] is None
    assert snapshot["watching"] == []
    assert snapshot["presence"]["has_active_scenario"] is False


def test_invariant_focus_implies_watching_via_synthesis():
    """Сценарій є, wake-умови ще не озброєні → позначки синтезуються з рівнів сценарію."""
    snapshot = build_public_snapshot(_state(), {"active_scenario": _scenario()}, [], _NOW_MS)
    assert snapshot["focus"] is not None
    roles = [item["role"] for item in snapshot["watching"]]
    assert "invalidation" in roles and "entry" in roles and "target" in roles


def test_same_level_dedup_prefers_structural_role():
    conditions = [_wake(4335, kind="candle_close"), _wake(4335)]
    snapshot = build_public_snapshot(
        _state(), {"active_scenario": _scenario()}, conditions, _NOW_MS
    )
    matching = [item for item in snapshot["watching"] if item["level"] == 4335.0]
    assert len(matching) == 1
    assert matching[0]["role"] == "entry"


def test_non_price_kinds_ignored():
    conditions = [
        {"kind": "session_open", "params": {"session": "london", "symbol": "XAU/USD"}},
        {"kind": "max_silence", "params": {"minutes": 60}},
        _wake(4348),
    ]
    snapshot = build_public_snapshot(
        _state(), {"active_scenario": _scenario()}, conditions, _NOW_MS
    )
    assert [item["level"] for item in snapshot["watching"]] == [4348.0]


def test_focus_shape_matches_contract():
    snapshot = build_public_snapshot(
        _state(), {"active_scenario": _scenario()}, [_wake(4348)], _NOW_MS
    )
    focus = snapshot["focus"]
    assert focus["symbol"] == "XAU/USD"
    assert focus["direction"] == "short"
    assert focus["confidence_pct"] == 55
    assert focus["entry_zone"] == [4328.0, 4335.0]
    assert focus["invalidation"] == 4348.0
    assert focus["targets"] == [4305.0, 4267.0]
    assert focus["grade"] == "A"


def test_symbol_derived_from_wake_params_when_scenario_lacks_it():
    scenario = _scenario()
    scenario.pop("symbol")
    snapshot = build_public_snapshot(
        _state(), {"active_scenario": scenario}, [_wake(4348)], _NOW_MS
    )
    assert snapshot["focus"]["symbol"] == "XAU/USD"


def test_mood_prefers_directives_over_state():
    snapshot = build_public_snapshot(
        _state(mood="calm"), {"mood": "analytical", "active_scenario": _scenario()}, [], _NOW_MS
    )
    assert snapshot["presence"]["mood"] == "analytical"


# ── route-інтеграція (ADR-0086 P2): маунт без auth, кеш, 503-деградація ──────

import pytest
from aiohttp import web

from runtime.api.public_snapshot import register_public_snapshot

pytestmark = pytest.mark.asyncio


class _StubRedis:
    def __init__(self, state, wake_json=None):
        self._state = state
        self._wake_json = wake_json
        self.hgetall_calls = 0

    def hgetall(self, _key):
        self.hgetall_calls += 1
        return self._state

    def get(self, _key):
        return self._wake_json


def _app_with_snapshot(tmp_path, redis_client, ttl_s=5.0):
    directives_file = tmp_path / "v3_agent_directives.json"
    directives_file.write_text(
        json.dumps({"mood": "analytical", "active_scenario": _scenario()}, ensure_ascii=False),
        encoding="utf-8",
    )
    app = web.Application()
    register_public_snapshot(
        app, redis_client=redis_client, namespace="v3_test", data_dir=str(tmp_path), ttl_s=ttl_s
    )
    return app


async def test_route_serves_sanitized_snapshot_without_auth(aiohttp_client, tmp_path):
    stub = _StubRedis(_state(), wake_json=json.dumps([_wake(4348)]))
    client = await aiohttp_client(_app_with_snapshot(tmp_path, stub))
    resp = await client.get("/api/public/snapshot")  # жодного Authorization
    assert resp.status == 200
    body = await resp.json()
    assert body["focus"]["symbol"] == "XAU/USD"
    assert body["watching"][0]["role"] == "invalidation"
    assert "budget" not in json.dumps(body)


async def test_route_ttl_cache_prevents_source_rereads(aiohttp_client, tmp_path):
    stub = _StubRedis(_state())
    client = await aiohttp_client(_app_with_snapshot(tmp_path, stub, ttl_s=60.0))
    for _ in range(5):
        assert (await client.get("/api/public/snapshot")).status == 200
    assert stub.hgetall_calls == 1  # 5 запитів = 1 читання джерел


async def test_route_503_when_redis_not_configured(aiohttp_client, tmp_path):
    client = await aiohttp_client(_app_with_snapshot(tmp_path, None))
    resp = await client.get("/api/public/snapshot")
    assert resp.status == 503
