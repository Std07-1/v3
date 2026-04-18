"""GAP-7 closure (changelog 20260418-010): Thesis sync end-to-end wire contract.

Bot side  (trader-v3/bot/transport/wake_sync.py:_sync_thesis)  writes via HSET.
Platform  (runtime/smc/narrative_enricher.py:refresh_thesis_sync) reads HGETALL.

Both sides existed independently but were never tested together. The "blocker" was
"API recovery 1 травня" — but thesis sync is pure Redis I/O. Live Sonnet call only
populates ActiveScenario.thesis text; for wire-contract verification we synthesize
AgentDirectives with active_scenario directly. Zero API cost, zero VPS dependency.

Coverage (14 tests, 5 classes):
  TestWriteContract  (3) — _sync_thesis HSET payload + EXPIRE + clear-on-empty
  TestReadContract   (3) — refresh_thesis_sync bytes-decode + freshness windows
  TestRoundtrip      (4) — write→read full payload + conviction boundaries
  TestSymbolParity   (2) — XAU vs BTC key isolation + "/" → "_" normalization
  TestEnrichWire     (2) — enrich_narrative injects archi_thesis when cached

Symbol parity (XAU/XAG/BTC/ETH): _sync_thesis is symbol-agnostic (sym arg is opaque),
NarrativeEnricher caches per-symbol — same path for forex and crypto. Verified via
TestSymbolParity (key isolation) + TestEnrichWire (per-symbol cache lookup).
"""

from __future__ import annotations

import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

# Add trader-v3/ to sys.path so we can import bot.transport.wake_sync.
# (trader-v3/ is gitignored proprietary subfolder; tests run from v3 root.)
_V3_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_V3_ROOT / "trader-v3"))

from bot.transport.wake_sync import _sync_thesis, _THESIS_KEY_TPL  # noqa: E402

from core.smc.wake_types import PresenceStatus  # noqa: E402
from runtime.smc.narrative_enricher import NarrativeEnricher  # noqa: E402

# ─── Test doubles ────────────────────────────────────────────────────────────


class FakeRedis:
    """Minimal sync Redis stub — only the ops that wake_sync + enricher use.

    Returns bytes from hgetall (matching real redis-py default decode_responses=False),
    so the bytes-decode path in narrative_enricher.refresh_thesis_sync is exercised.
    """

    def __init__(self) -> None:
        self._hashes: Dict[str, Dict[bytes, bytes]] = {}
        self._ttls: Dict[str, int] = {}

    def hset(
        self, key: str, mapping: Optional[Dict[str, Any]] = None, **kwargs: Any
    ) -> int:
        if mapping is None:
            mapping = {}
        h = self._hashes.setdefault(key, {})
        added = 0
        for k, v in mapping.items():
            kb = k.encode("utf-8") if isinstance(k, str) else bytes(k)
            vb = str(v).encode("utf-8") if not isinstance(v, bytes) else v
            if kb not in h:
                added += 1
            h[kb] = vb
        return added

    def hgetall(self, key: str) -> Dict[bytes, bytes]:
        return dict(self._hashes.get(key, {}))

    def expire(self, key: str, seconds: int) -> bool:
        if key in self._hashes:
            self._ttls[key] = seconds
            return True
        return False

    def delete(self, key: str) -> int:
        existed = key in self._hashes
        self._hashes.pop(key, None)
        self._ttls.pop(key, None)
        return 1 if existed else 0

    def ttl(self, key: str) -> int:
        return self._ttls.get(key, -1)


@dataclass
class _FakeScenario:
    """Stand-in for bot.state.directives.ActiveScenario — only fields _sync_thesis reads."""

    thesis: str = ""
    direction: str = "long"
    confidence: float = 0.70
    entry_zone_low: Optional[float] = None
    entry_zone_high: Optional[float] = None
    invalidation: Optional[float] = None


@dataclass
class _FakeDirectives:
    """Stand-in for AgentDirectives — only active_scenario read by _sync_thesis."""

    active_scenario: Optional[_FakeScenario] = None


def _make_enricher(redis: FakeRedis, ns: str = "test_ns") -> NarrativeEnricher:
    executor = ThreadPoolExecutor(max_workers=1)
    return NarrativeEnricher(redis_client=redis, namespace=ns, executor=executor)


# ─── TestWriteContract ───────────────────────────────────────────────────────


class TestWriteContract:
    """Bot writer (_sync_thesis) produces the expected Redis HSET payload."""

    def test_full_payload_written(self) -> None:
        r = FakeRedis()
        d = _FakeDirectives(
            active_scenario=_FakeScenario(
                thesis="Bearish continuation to 4300-4350 sweep zone",
                direction="short",
                confidence=0.80,
                entry_zone_low=4350.0,
                entry_zone_high=4400.0,
                invalidation=4500.0,
            )
        )
        _sync_thesis(r, "test_ns", d, "XAU_USD")

        key = _THESIS_KEY_TPL.format(ns="test_ns", sym="XAU_USD")
        raw = r.hgetall(key)
        # Decode for assertions
        data = {k.decode(): v.decode() for k, v in raw.items()}

        assert data["thesis"] == "Bearish continuation to 4300-4350 sweep zone"
        assert data["conviction"] == "medium"  # 0.65 ≤ 0.80 < 0.85
        assert data["key_level"] == "4350-4400"
        assert data["invalidation"] == "4500.0"
        assert data["direction"] == "short"
        assert data["confidence"] == "0.8"
        assert int(data["updated_at_ms"]) > 0
        # EXPIRE applied
        assert r.ttl(key) == 6 * 3600

    def test_conviction_boundaries(self) -> None:
        """confidence ≥ 0.85 → high; ≥ 0.65 → medium; else → low."""
        cases = [
            (0.95, "high"),
            (0.85, "high"),
            (0.84, "medium"),
            (0.65, "medium"),
            (0.64, "low"),
            (0.0, "low"),
        ]
        for conf, expected in cases:
            r = FakeRedis()
            d = _FakeDirectives(
                active_scenario=_FakeScenario(thesis="Test thesis", confidence=conf)
            )
            _sync_thesis(r, "ns", d, "XAU_USD")
            raw = r.hgetall(_THESIS_KEY_TPL.format(ns="ns", sym="XAU_USD"))
            assert (
                raw[b"conviction"].decode() == expected
            ), f"conf={conf} expected={expected}"

    def test_empty_thesis_clears_redis(self) -> None:
        """Empty thesis (or no active_scenario) → DELETE key, no stale data."""
        r = FakeRedis()
        # Pre-populate stale data
        key = _THESIS_KEY_TPL.format(ns="ns", sym="XAU_USD")
        r.hset(key, mapping={"thesis": "old stale data"})
        assert r.hgetall(key) != {}

        # Sync with empty thesis
        d = _FakeDirectives(active_scenario=_FakeScenario(thesis=""))
        _sync_thesis(r, "ns", d, "XAU_USD")
        assert r.hgetall(key) == {}, "stale thesis must be cleared"

        # Also: no active_scenario at all
        r.hset(key, mapping={"thesis": "more stale"})
        d2 = _FakeDirectives(active_scenario=None)
        _sync_thesis(r, "ns", d2, "XAU_USD")
        assert r.hgetall(key) == {}


# ─── TestReadContract ────────────────────────────────────────────────────────


class TestReadContract:
    """Platform reader (NarrativeEnricher.refresh_thesis_sync) decodes correctly."""

    def test_bytes_decode_path(self) -> None:
        """Real redis returns bytes; enricher must decode both keys and values."""
        r = FakeRedis()
        key = _THESIS_KEY_TPL.format(ns="test_ns", sym="XAU_USD")
        now_ms = int(time.time() * 1000)
        r.hset(
            key,
            mapping={
                "thesis": "Bullish to 4700",
                "conviction": "high",
                "key_level": "PDH 4720",
                "invalidation": "4600",
                "updated_at_ms": str(now_ms),
            },
        )
        enricher = _make_enricher(r, ns="test_ns")
        enricher.refresh_thesis_sync("XAU/USD")  # note "/" — gets normalized to "_"

        cached = enricher._thesis_cache.get("XAU/USD")
        assert cached is not None
        assert cached.thesis == "Bullish to 4700"
        assert cached.conviction == "high"
        assert cached.key_level == "PDH 4720"
        assert cached.invalidation == "4600"
        assert cached.updated_at_ms == now_ms

    def test_freshness_windows(self) -> None:
        """fresh < 1h, aging 1-4h, stale > 4h."""
        cases = [
            (0, "fresh"),  # just now
            (30 * 60 * 1000, "fresh"),  # 30 min
            (90 * 60 * 1000, "aging"),  # 1.5h
            (3 * 3600 * 1000, "aging"),  # 3h
            (5 * 3600 * 1000, "stale"),  # 5h
            (24 * 3600 * 1000, "stale"),  # 1 day
        ]
        for age_ms, expected in cases:
            r = FakeRedis()
            now_ms = int(time.time() * 1000)
            updated_ms = now_ms - age_ms
            key = _THESIS_KEY_TPL.format(ns="ns", sym="XAU_USD")
            r.hset(key, mapping={"thesis": "X", "updated_at_ms": str(updated_ms)})
            enricher = _make_enricher(r, ns="ns")
            enricher.refresh_thesis_sync("XAU/USD")
            cached = enricher._thesis_cache.get("XAU/USD")
            assert cached is not None
            assert (
                cached.freshness == expected
            ), f"age={age_ms}ms expected={expected} got={cached.freshness}"

    def test_empty_hgetall_is_noop(self) -> None:
        """Missing key → no cache entry, no exception."""
        r = FakeRedis()
        enricher = _make_enricher(r)
        enricher.refresh_thesis_sync("XAU/USD")
        assert enricher._thesis_cache.get("XAU/USD") is None


# ─── TestRoundtrip — the actual e2e ──────────────────────────────────────────


class TestRoundtrip:
    """Bot writes → Platform reads → fields preserved."""

    def test_full_roundtrip(self) -> None:
        r = FakeRedis()
        d = _FakeDirectives(
            active_scenario=_FakeScenario(
                thesis="Sell rally into 4450 OB, target sweep PDL 4280",
                direction="short",
                confidence=0.88,
                entry_zone_low=4440.0,
                entry_zone_high=4470.0,
                invalidation=4520.0,
            )
        )
        # Bot writes
        _sync_thesis(r, "v3_local", d, "XAU_USD")

        # Platform reads
        enricher = _make_enricher(r, ns="v3_local")
        enricher.refresh_thesis_sync("XAU/USD")
        cached = enricher._thesis_cache.get("XAU/USD")

        assert cached is not None
        assert cached.thesis.startswith("Sell rally into 4450 OB")
        assert cached.conviction == "high"  # 0.88 ≥ 0.85
        assert cached.key_level == "4440-4470"
        assert cached.invalidation == "4520.0"
        assert cached.freshness == "fresh"

    def test_thesis_truncation_300_chars(self) -> None:
        """_sync_thesis truncates thesis to 300 chars — verify enricher reads truncated form."""
        long_thesis = "A" * 500
        r = FakeRedis()
        d = _FakeDirectives(
            active_scenario=_FakeScenario(thesis=long_thesis, confidence=0.75)
        )
        _sync_thesis(r, "ns", d, "XAU_USD")
        enricher = _make_enricher(r, ns="ns")
        enricher.refresh_thesis_sync("XAU/USD")
        cached = enricher._thesis_cache.get("XAU/USD")
        assert cached is not None
        assert len(cached.thesis) == 300

    def test_key_level_fallback_when_no_entry_zone(self) -> None:
        """If entry_zone missing, key_level uses invalidation: 'inv:4500'."""
        r = FakeRedis()
        d = _FakeDirectives(
            active_scenario=_FakeScenario(
                thesis="X",
                confidence=0.7,
                entry_zone_low=None,
                entry_zone_high=None,
                invalidation=4500.0,
            )
        )
        _sync_thesis(r, "ns", d, "XAU_USD")
        enricher = _make_enricher(r, ns="ns")
        enricher.refresh_thesis_sync("XAU/USD")
        cached = enricher._thesis_cache.get("XAU/USD")
        assert cached is not None
        assert cached.key_level == "inv:4500"

    def test_no_invalidation_no_entry_zone_empty_key_level(self) -> None:
        """Both entry_zone and invalidation None → key_level = ''."""
        r = FakeRedis()
        d = _FakeDirectives(
            active_scenario=_FakeScenario(thesis="X", confidence=0.7, invalidation=None)
        )
        _sync_thesis(r, "ns", d, "XAU_USD")
        enricher = _make_enricher(r, ns="ns")
        enricher.refresh_thesis_sync("XAU/USD")
        cached = enricher._thesis_cache.get("XAU/USD")
        assert cached is not None
        assert cached.key_level == ""


# ─── TestSymbolParity (XAU/XAG forex + BTC/ETH crypto) ───────────────────────


class TestSymbolParity:
    """Same Redis path for forex (XAU/XAG) and crypto (BTC/ETH) — keys isolated."""

    def test_forex_vs_crypto_keys_isolated(self) -> None:
        """XAU thesis MUST NOT leak into BTC and vice versa."""
        r = FakeRedis()
        d_xau = _FakeDirectives(
            active_scenario=_FakeScenario(thesis="XAU bullish 4700", confidence=0.8)
        )
        d_btc = _FakeDirectives(
            active_scenario=_FakeScenario(thesis="BTC bearish 60k", confidence=0.7)
        )
        _sync_thesis(r, "v3_local", d_xau, "XAU_USD")
        _sync_thesis(r, "v3_local", d_btc, "BTCUSDT")

        enricher = _make_enricher(r, ns="v3_local")
        enricher.refresh_thesis_sync("XAU/USD")
        enricher.refresh_thesis_sync("BTCUSDT")

        xau = enricher._thesis_cache.get("XAU/USD")
        btc = enricher._thesis_cache.get("BTCUSDT")
        assert xau is not None and btc is not None
        assert "XAU" in xau.thesis and "BTC" not in xau.thesis
        assert "BTC" in btc.thesis and "XAU" not in btc.thesis

    def test_slash_normalization_both_sides(self) -> None:
        """sym 'XAU/USD' on read must match key written for 'XAU_USD' (sym_safe)."""
        r = FakeRedis()
        # Bot writes with sym_safe (post-normalization in sync_wake_to_redis)
        d = _FakeDirectives(active_scenario=_FakeScenario(thesis="T", confidence=0.7))
        _sync_thesis(r, "ns", d, "XAU_USD")  # sym_safe

        # Platform reader gets the public symbol "XAU/USD" and normalizes internally
        enricher = _make_enricher(r, ns="ns")
        enricher.refresh_thesis_sync("XAU/USD")
        assert (
            enricher._thesis_cache.get("XAU/USD") is not None
        ), "enricher must normalize 'XAU/USD' → 'XAU_USD' to match bot's key"


# ─── TestEnrichWire — narrative injection ────────────────────────────────────


class TestEnrichWire:
    """enrich_narrative attaches archi_thesis + archi_presence to wire dict."""

    def test_thesis_and_presence_injected(self) -> None:
        r = FakeRedis()
        d = _FakeDirectives(
            active_scenario=_FakeScenario(
                thesis="Wait for sweep PDL 4650",
                confidence=0.78,
                entry_zone_low=4650.0,
                entry_zone_high=4680.0,
            )
        )
        _sync_thesis(r, "ns", d, "XAU_USD")
        enricher = _make_enricher(r, ns="ns")
        enricher.refresh_thesis_sync("XAU/USD")

        presence = PresenceStatus(
            status="watching",
            focus="London open",
            silence_since_h=2.5,
            next_possible_wake="PDL 4650 cross",
            active_conditions=3,
            accumulator_score=0.4,
            accumulator_threshold=1.0,
        )
        wire_in = {"phase": "ACCUMULATION", "scenario": "wait"}
        wire_out = enricher.enrich_narrative(
            wire_in, "XAU/USD", presence=presence, tier="premium"
        )

        assert "archi_thesis" in wire_out
        assert wire_out["archi_thesis"]["thesis"] == "Wait for sweep PDL 4650"
        assert wire_out["archi_thesis"]["conviction"] == "medium"
        assert wire_out["archi_thesis"]["key_level"] == "4650-4680"
        assert wire_out["archi_thesis"]["freshness"] == "fresh"
        assert "archi_presence" in wire_out
        assert wire_out["archi_presence"]["status"] == "watching"
        assert wire_out["archi_presence"]["silence_h"] == 2.5
        # Original keys preserved
        assert wire_out["phase"] == "ACCUMULATION"
        # Input not mutated
        assert "archi_thesis" not in wire_in

    def test_btc_thesis_does_not_leak_into_xau_wire(self) -> None:
        """Per-symbol cache lookup — BTC thesis must NOT appear in XAU's wire frame."""
        r = FakeRedis()
        d_btc = _FakeDirectives(
            active_scenario=_FakeScenario(thesis="BTC long to 70k", confidence=0.8)
        )
        _sync_thesis(r, "ns", d_btc, "BTCUSDT")
        enricher = _make_enricher(r, ns="ns")
        enricher.refresh_thesis_sync("BTCUSDT")
        # XAU has no thesis written
        wire = enricher.enrich_narrative({}, "XAU/USD", presence=None, tier="premium")
        assert "archi_thesis" not in wire, "BTC thesis must NOT bleed into XAU wire"

        # BTC's own wire DOES get it
        wire_btc = enricher.enrich_narrative(
            {}, "BTCUSDT", presence=None, tier="premium"
        )
        assert "archi_thesis" in wire_btc
        assert "BTC" in wire_btc["archi_thesis"]["thesis"]
