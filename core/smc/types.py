"""
core/smc/types.py — Канонічні типи SMC Engine (ADR-0024 §5.1).

Інваріанти:
  S0: чисті dataclasses, NO I/O
  S3: Zone/Swing/Level IDs детерміновані: same input → same id
  S6: to_wire() відповідає ui_v4/src/lib/types.ts SmcZone/SmcSwing/SmcLevel

Python 3.7 compatible.
"""
from __future__ import annotations

import dataclasses
from typing import Any, Dict, List, Optional

# ── Vocabulary constants (S5: config-driven via engine, але kinds — SSOT тут) ──
ZONE_KINDS = frozenset({
    "ob_bull", "ob_bear",        # Order Blocks (§4.3)
    "fvg_bull", "fvg_bear",      # Fair Value Gaps (§4.4)
    "premium", "discount",       # Premium/Discount Zones (§4.6)
})
ZONE_STATUSES = frozenset({
    "active", "tested", "mitigated", "breaker",
    "partially_filled", "filled", "fading", "expired",
})

SWING_KINDS = frozenset({
    "hh", "hl", "lh", "ll",                              # Basic structure
    "bos_bull", "bos_bear", "choch_bull", "choch_bear",  # Structure events (§4.2)
    "inducement_bull", "inducement_bear",                 # Inducement (§4.7)
})

LEVEL_KINDS = frozenset({"eq_highs", "eq_lows", "pdh", "pdl", "pwh", "pwl"})

POI_GRADES = frozenset({"A+", "A", "B", "C"})


@dataclasses.dataclass(frozen=True)
class SmcZone:
    """Order Block або Fair Value Gap — незмінний після створення.

    S3: id = "{kind}_{symbol}_{tf_s}_{anchor_bar_ms}" — детермінований.
    """
    id: str               # Deterministic: "{kind}_{symbol}_{tf_s}_{anchor_bar_ms}"
    symbol: str
    tf_s: int
    kind: str             # ZONE_KINDS
    start_ms: int         # Left edge — open_time_ms of anchor candle
    end_ms: Optional[int] # Right edge; None = still active
    high: float           # Zone top
    low: float            # Zone bottom
    status: str           # ZONE_STATUSES
    strength: float       # 0.0–1.0, impulse / ATR
    anchor_bar_ms: int    # The candle that created this zone

    def to_wire(self) -> Dict[str, Any]:
        """S6: wire format = ui_v4 SmcZone type."""
        return {
            "id": self.id,
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "high": self.high,
            "low": self.low,
            "kind": self.kind,
            "status": self.status,
            "strength": round(self.strength, 3),
        }

    def with_status(self, status: str, end_ms: Optional[int] = None) -> "SmcZone":
        """Повертає нову зону з оновленим статусом (frozen=True → нова копія)."""
        return dataclasses.replace(self, status=status, end_ms=end_ms or self.end_ms)


@dataclasses.dataclass(frozen=True)
class SmcSwing:
    """Swing point або Structure event (BOS/CHoCH).

    S3: id = "{kind}_{symbol}_{tf_s}_{time_ms}" — детермінований.
    """
    id: str               # "{kind}_{symbol}_{tf_s}_{time_ms}"
    symbol: str
    tf_s: int
    kind: str             # SWING_KINDS
    price: float
    time_ms: int          # open_time_ms of the bar where swing occurred
    confirmed: bool       # True once `period` bars have passed

    def to_wire(self) -> Dict[str, Any]:
        """S6: wire format = ui_v4 SmcSwing type."""
        return {
            "id": self.id,
            "a": {"t": self.time_ms, "p": self.price},
            "b": {"t": self.time_ms, "p": self.price},
            "label": self.kind.upper().replace("_", " "),
        }


@dataclasses.dataclass(frozen=True)
class SmcLevel:
    """Liquidity level (Equal Highs/Lows, PDH/PDL, PWH/PWL).

    S3: id = "{kind}_{symbol}_{tf_s}_{price_int}" — детермінований.
    """
    id: str               # "{kind}_{symbol}_{tf_s}_{price×100_int}"
    symbol: str
    tf_s: int
    kind: str             # LEVEL_KINDS
    price: float
    time_ms: Optional[int]
    touches: int

    def to_wire(self) -> Dict[str, Any]:
        """S6: wire format = ui_v4 SmcLevel type."""
        return {
            "id": self.id,
            "price": self.price,
            "t_ms": self.time_ms,
            "color": None,  # UI вирішує по kind
        }


@dataclasses.dataclass(frozen=True)
class SmcSnapshot:
    """Повний стан SMC для (symbol, tf) — для full WS frame / HTTP GET."""
    symbol: str
    tf_s: int
    zones: List[SmcZone]
    swings: List[SmcSwing]
    levels: List[SmcLevel]
    trend_bias: Optional[str]  # "bullish" | "bearish" | "neutral" | None
    last_bos_ms: Optional[int]
    last_choch_ms: Optional[int]
    computed_at_ms: int
    bar_count: int

    def to_wire(self) -> Dict[str, Any]:
        """S6: wire format — вбудовується у WS full frame."""
        return {
            "zones":   [z.to_wire() for z in self.zones],
            "swings":  [s.to_wire() for s in self.swings],
            "levels":  [low.to_wire() for low in self.levels],
        }


@dataclasses.dataclass(frozen=True)
class SmcDelta:
    """Інкрементальна зміна після on_bar() — для WS delta frame."""
    symbol: str
    tf_s: int
    bar_open_ms: int
    new_zones: List[SmcZone]
    mitigated_zones: List[str]   # IDs зон що стали mitigated/filled
    updated_zones: List[SmcZone] # Зони з оновленим статусом
    new_swings: List[SmcSwing]
    new_levels: List[SmcLevel]
    removed_levels: List[str]    # IDs видалених рівнів
    trend_bias: Optional[str]

    @property
    def has_changes(self) -> bool:
        return bool(
            self.new_zones or self.mitigated_zones or self.updated_zones
            or self.new_swings or self.new_levels or self.removed_levels
        )

    def to_wire(self) -> Dict[str, Any]:
        return {
            "new_zones":          [z.to_wire() for z in self.new_zones],
            "mitigated_zone_ids": list(self.mitigated_zones),
            "updated_zones":      [z.to_wire() for z in self.updated_zones],
            "new_swings":         [s.to_wire() for s in self.new_swings],
            "new_levels":         [low.to_wire() for low in self.new_levels],
            "removed_level_ids":  list(self.removed_levels),
            "trend_bias":         self.trend_bias,
        }


def make_zone_id(kind: str, symbol: str, tf_s: int, anchor_bar_ms: int) -> str:
    """S3: детермінований zone ID."""
    sym_safe = symbol.replace("/", "_").replace(" ", "_")
    return f"{kind}_{sym_safe}_{tf_s}_{anchor_bar_ms}"


def make_swing_id(kind: str, symbol: str, tf_s: int, time_ms: int) -> str:
    """S3: детермінований swing ID."""
    sym_safe = symbol.replace("/", "_").replace(" ", "_")
    return f"{kind}_{sym_safe}_{tf_s}_{time_ms}"


def make_level_id(kind: str, symbol: str, tf_s: int, price: float) -> str:
    """S3: детермінований level ID (price rounded to int*100)."""
    sym_safe = symbol.replace("/", "_").replace(" ", "_")
    price_key = int(round(price * 100))
    return f"{kind}_{sym_safe}_{tf_s}_{price_key}"