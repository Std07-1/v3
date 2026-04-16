"""
core/smc/wake_types.py вЂ” Canonical types for WakeEngine (ADR-0049).

Pure dataclasses, zero I/O. Imported by:
  - core/smc/wake_check.py      (pure condition checking)
  - core/smc/auto_wake.py       (platform condition generation)
  - runtime/smc/wake_engine.py   (I/O orchestration)
  - trader-v3/bot/transport/wake_reader.py  (bot-side event parsing)

Invariants:
  - I0: core в†’ does NOT import runtime
  - S0: zero I/O, zero Redis, zero HTTP
"""
from __future__ import annotations

import dataclasses
import enum
from collections import deque
from typing import Any, Dict, List, Optional


# в”Ђв”Ђв”Ђ Enums в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ


class WakeConditionKind(str, enum.Enum):
    """7 types of wake conditions. All $0 checks (numeric comparisons)."""

    PRICE_CROSS = "price_cross"
    PRICE_ZONE_TOUCH = "price_zone_touch"
    SESSION_OPEN = "session_open"
    VOLATILITY_SPIKE = "volatility_spike"
    MAX_SILENCE = "max_silence"
    SCHEDULED = "scheduled"
    STRUCTURE_BREAK = "structure_break"


class FeatureTier(str, enum.Enum):
    """Subscription tier for wire frame field gating (ADR-0049 В§5)."""

    FREE = "free"
    PREMIUM = "premium"


# в”Ђв”Ђв”Ђ Wake Condition & Event в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ


@dataclasses.dataclass(frozen=True)
class WakeCondition:
    """One wake condition. Set by bot (РђСЂС‡С–) OR by platform (AutoWakeGenerator).

    kind:   check type (WakeConditionKind)
    params: kind-dependent parameters:
      price_cross:      {"level": 4680.0, "direction": "below"}
      price_zone_touch: {"zone_high": 4700.0, "zone_low": 4680.0, "tolerance_atr": 0.5}
      session_open:     {"session": "london"}
      volatility_spike: {"atr_mult": 2.0}
      max_silence:      {"hours": 3}
      scheduled:        {"hour_utc": 8, "minute_utc": 0}
      structure_break:  {"tf_s": 900, "type": "bos"}
    reason: human-readable explanation (injected into РђСЂС‡С–'s wake prompt)
    source: "bot" (РђСЂС‡С– defined) or "platform" (AutoWakeGenerator)
    """

    kind: WakeConditionKind
    params: Dict[str, Any]
    reason: str
    source: str = "platform"
    created_at_ms: int = 0


@dataclasses.dataclass(frozen=True)
class WakeEvent:
    """A fired wake event вЂ” result of condition match.

    Written to Redis list by WakeEngine (platform side).
    Read by bot via WakeReader (polling every 30s).
    """

    ts_ms: int
    symbol: str
    kind: str  # WakeConditionKind.value
    reason: str
    price: float
    meta: Dict[str, Any]  # atr, session, zone_id, accumulator_score, etc.


# в”Ђв”Ђв”Ђ Awareness в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ


@dataclasses.dataclass(frozen=True)
class AwarenessState:
    """What changed since last wake вЂ” digest for bot.

    Bot receives this with WakeEvent so it knows everything it missed.
    Like an automatic "morning briefing" between wakes.
    """

    price_delta_pct: float
    atr_delta_pct: float
    new_zones: int
    mitigated_zones: int
    structure_breaks: int
    session_changes: List[str]  # ["asia_close", "london_open"]
    max_zone_proximity_pct: float  # how close to nearest A+/A zone (% of ATR)


@dataclasses.dataclass
class AwarenessAccumulator:
    """Numeric score accumulating between wakes.

    Every tick: score += |price_delta| / ATR (normalized movement).
    Decay: score *= decay each minute (prevents infinite buildup).
    When score >= threshold в†’ wake even without explicit condition match.

    This is the safety net for unpredictable events: flash crash, gap open,
    trending day where no explicit condition was set. РђСЂС‡С– can tune
    threshold and decay via directives.

    Math: threshold=5.0, decay=0.95/min в†’ need ~3 ATR move in 10 min to trigger.
    For XAU/USD with ATR(H4)~45: need ~135 points in 10 min = flash crash.
    """

    score: float = 0.0
    threshold: float = 5.0
    decay: float = 0.95
    events_log: deque = dataclasses.field(
        default_factory=lambda: deque(maxlen=50)
    )
    last_tick_ts: float = 0.0
    last_wake_price: float = 0.0


# в”Ђв”Ђв”Ђ Presence & Thesis (UI layer) в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ


@dataclasses.dataclass(frozen=True)
class PresenceStatus:
    """РђСЂС‡С–'s presence status for UI (NarrativePanel).

    Built by WakeEngine every tick. Published in wire frame.
    Shows user: РђСЂС‡С– is working, not frozen. Reduces "silent bot" anxiety.
    """

    status: str = "sleeping"  # "watching" | "sleeping" | "analyzing" | "alert"
    focus: str = ""  # "С‡РµРєР°СЋ РїСЂРѕР±РѕСЋ 4850 Р°Р±Рѕ London open"
    silence_since_h: float = 0.0
    next_possible_wake: str = ""  # "London open (08:00 UTC) Р°Р±Рѕ price > 4850"
    active_conditions: int = 0
    accumulator_score: float = 0.0
    accumulator_threshold: float = 5.0
    last_thesis: str = ""
    last_analysis_age_h: float = 0.0


@dataclasses.dataclass(frozen=True)
class ThesisLayer:
    """РђСЂС‡С–-generated thesis overlay for NarrativeBlock.

    Written by bot to Redis after each Sonnet analysis.
    Platform reads (NarrativeEnricher) and injects into wire frame.
    Platform NEVER writes thesis вЂ” invariant I7 (Autonomy-First).
    """

    thesis: str  # "Р–РґСѓ sweep PDL 4650 в†’ reaction С–Р· London killzone"
    conviction: str  # "high" | "medium" | "low"
    key_level: str  # "PDL 4650 вЂ” main target for liquidity sweep"
    invalidation: str  # "Break above 4730 invalidates sell thesis"
    updated_at_ms: int = 0
    freshness: str = "stale"  # "fresh" (<1h) | "aging" (1-4h) | "stale" (>4h)
