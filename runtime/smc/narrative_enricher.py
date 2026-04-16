"""
runtime/smc/narrative_enricher.py вЂ” Thesis + Presence injection into wire frame (ADR-0049 P5).

Reads Archi's thesis from Redis cache (written by bot after each Sonnet analysis).
Injects into wire frame narrative alongside PresenceStatus from WakeEngine.

This creates the "premium analytics" layer that no one knows is AI-driven:
  - Thesis: "Р–РґСѓ sweep PDL 4650 в†’ reaction С–Р· London killzone"
  - Conviction: "high"
  - Key level: "PDL 4650"
  - Invalidation: "Break above 4730"
  - Presence: "watching", accumulator score, next possible wake

Invariants:
  - I7: NarrativeEnricher READS thesis, NEVER writes. Bot = author.
  - S1: read-only, does NOT write to UDS.
  - K2: Redis ops via run_in_executor (non-blocking).

Pattern: same as WakeEngine Redis reads вЂ” sync calls via executor.
"""
from __future__ import annotations

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, Optional

from core.smc.wake_types import PresenceStatus, ThesisLayer

_log = logging.getLogger(__name__)

# Redis key pattern: {ns}:thesis:{symbol_safe}
_THESIS_KEY_TPL = "{ns}:thesis:{sym}"
_THESIS_CACHE_TTL = 15.0  # refresh from Redis every 15s (thesis changes rarely)

# Freshness thresholds (hours since thesis update)
_FRESH_H = 1.0      # < 1h = "fresh"
_AGING_H = 4.0      # 1-4h = "aging"
                     # > 4h = "stale"


class NarrativeEnricher:
    """Enriches wire frame narrative with Archi's thesis + presence.

    Lives in ws_server process (in-process, like WakeEngine).
    Called during frame serialization for each subscriber.
    """

    def __init__(
        self,
        redis_client: Any,
        namespace: str,
        executor: ThreadPoolExecutor,
    ):
        self._redis = redis_client
        self._ns = namespace
        self._executor = executor

        # Thesis cache: {symbol: ThesisLayer}
        self._thesis_cache: Dict[str, ThesisLayer] = {}
        self._thesis_cache_ts: Dict[str, float] = {}

    def enrich_narrative(
        self,
        narrative_wire: Dict[str, Any],
        symbol: str,
        presence: Optional[PresenceStatus] = None,
        tier: str = "free",
    ) -> Dict[str, Any]:
        """Add thesis + presence to narrative wire dict. Sync, in-memory.

        Called during frame building. Uses cached thesis (refreshed async).
        Does NOT mutate input dict вЂ” returns new dict.

        Args:
            narrative_wire: existing narrative dict from narrative_to_wire()
            symbol:         trading symbol
            presence:       PresenceStatus from WakeEngine (optional)
            tier:           "free" or "premium" вЂ” gates thesis fields

        Returns:
            Enriched narrative dict with 'archi_thesis' and 'archi_presence' keys.
        """
        result = dict(narrative_wire)  # shallow copy, don't mutate original

        # в”Ђв”Ђ Presence (always included, all tiers) в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ
        if presence is not None:
            result["archi_presence"] = {
                "status": presence.status,
                "focus": presence.focus[:120] if presence.focus else "",
                "silence_h": round(presence.silence_since_h, 1),
                "next_wake": presence.next_possible_wake[:120],
                "conditions": presence.active_conditions,
                "accumulator": round(presence.accumulator_score, 2),
                "accumulator_threshold": presence.accumulator_threshold,
            }

        # в”Ђв”Ђ Thesis (premium only when gating enabled) в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ
        thesis = self._thesis_cache.get(symbol)
        if thesis is not None and thesis.thesis:
            if tier == "premium" or tier == "free":  # TODO: gate when subscription ready
                result["archi_thesis"] = {
                    "thesis": thesis.thesis,
                    "conviction": thesis.conviction,
                    "key_level": thesis.key_level,
                    "invalidation": thesis.invalidation,
                    "freshness": thesis.freshness,
                    "updated_at_ms": thesis.updated_at_ms,
                }

        return result

    def refresh_thesis_sync(self, symbol: str) -> None:
        """Sync Redis read for thesis. Called via run_in_executor.

        Bot writes thesis to Redis after each Sonnet analysis:
          HSET {ns}:thesis:{symbol} thesis "..." conviction "high" ...

        We read all fields and build ThesisLayer.
        """
        sym_safe = symbol.replace("/", "_")
        key = _THESIS_KEY_TPL.format(ns=self._ns, sym=sym_safe)

        try:
            raw = self._redis.hgetall(key)
            if not raw:
                return

            # Redis returns bytes вЂ” decode
            data: Dict[str, str] = {}
            for k, v in raw.items():
                k_str = k.decode("utf-8") if isinstance(k, bytes) else str(k)
                v_str = v.decode("utf-8") if isinstance(v, bytes) else str(v)
                data[k_str] = v_str

            if not data.get("thesis"):
                return

            updated_ms = int(data.get("updated_at_ms", "0") or "0")
            now_ms = int(time.time() * 1000)
            age_h = (now_ms - updated_ms) / 3_600_000 if updated_ms > 0 else 99.0

            if age_h < _FRESH_H:
                freshness = "fresh"
            elif age_h < _AGING_H:
                freshness = "aging"
            else:
                freshness = "stale"

            self._thesis_cache[symbol] = ThesisLayer(
                thesis=data.get("thesis", ""),
                conviction=data.get("conviction", ""),
                key_level=data.get("key_level", ""),
                invalidation=data.get("invalidation", ""),
                updated_at_ms=updated_ms,
                freshness=freshness,
            )
            self._thesis_cache_ts[symbol] = time.time()

        except Exception as exc:
            _log.debug("THESIS_READ_ERR sym=%s: %s", symbol, exc)

    def needs_refresh(self, symbol: str) -> bool:
        """Check if thesis cache is stale and needs Redis re-read."""
        last_ts = self._thesis_cache_ts.get(symbol, 0.0)
        return (time.time() - last_ts) > _THESIS_CACHE_TTL
