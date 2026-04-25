"""core/smc/range_exhaustion.py — ADR-0053 Range Exhaustion Detector.

Pure compute: ATR-based daily/session travel gauge.
No I/O, no Redis, no UDS writes. S1-compliant (SMC read-only overlay).

Inputs (keyword-only):
  - symbol, current_price, now_ms
  - bars_d1, bars_h1 (for ATR baselines)
  - anchors: {anchor_kind → (open_ms, open_price)}
  - active_session ("asia" | "london" | "ny" | "weekend")
  - cfg (SmcRangeExhaustionConfig)

Output: RangeExhaustionSnapshot with per-anchor states + primary selection.

I5: degraded-but-loud — empty bars/bad anchor produces explicit `degraded[]` markers,
NOT silent zero.
"""

from __future__ import annotations

import dataclasses
import time
from typing import Dict, List, Optional, Tuple

from core.model.bars import CandleBar
from core.smc.config import SmcRangeExhaustionConfig
from core.smc.swings import compute_atr
from core.smc.types import (
    RANGE_ANCHOR_KINDS,
    RangeExhaustionSnapshot,
    RangeExhaustionState,
)

# Session-based anchors use H1 ATR scaled to approximate session range budget.
# D1-based anchors (d1_open, week_open) use D1 ATR directly.
_SESSION_ANCHORS = frozenset({"london_open", "ny_open"})


def _classify_phase(mult: float, cfg: SmcRangeExhaustionConfig) -> str:
    if mult < cfg.phase_early_max:
        return "early"
    if mult < cfg.phase_mid_max:
        return "mid"
    if mult < cfg.phase_late_max:
        return "late"
    return "exhausted"


def _confidence_delta_for(phase: str, cfg: SmcRangeExhaustionConfig) -> float:
    if phase == "early":
        return cfg.confidence_delta_early
    if phase == "mid":
        return cfg.confidence_delta_mid
    if phase == "late":
        return cfg.confidence_delta_late
    if phase == "exhausted":
        return cfg.confidence_delta_exhausted
    return 0.0


def _build_state(
    *,
    anchor_kind: str,
    anchor_ms: int,
    anchor_price: float,
    current_price: float,
    atr_baseline: float,
    cfg: SmcRangeExhaustionConfig,
    degraded: Optional[List[str]] = None,
) -> RangeExhaustionState:
    deg: List[str] = list(degraded) if degraded else []
    if atr_baseline <= 0.0:
        deg.append("bad_atr_baseline")
        atr_baseline = 1.0  # rail: never divide by zero
    if anchor_price <= 0.0:
        deg.append("bad_anchor_price")

    traveled_abs = abs(current_price - anchor_price)
    if current_price > anchor_price:
        traveled_dir = 1
    elif current_price < anchor_price:
        traveled_dir = -1
    else:
        traveled_dir = 0
    traveled_mult = traveled_abs / atr_baseline
    phase = _classify_phase(traveled_mult, cfg)
    confidence_delta = _confidence_delta_for(phase, cfg)
    remaining_budget = max(0.0, cfg.exhaustion_cap - traveled_mult)

    return RangeExhaustionState(
        anchor_kind=anchor_kind,
        anchor_ms=anchor_ms,
        anchor_price=anchor_price,
        current_price=current_price,
        traveled_abs=round(traveled_abs, 4),
        traveled_dir=traveled_dir,
        atr_baseline=round(atr_baseline, 4),
        traveled_mult=round(traveled_mult, 4),
        phase=phase,
        remaining_budget=round(remaining_budget, 4),
        confidence_delta=confidence_delta,
        degraded=deg,
    )


def _select_primary(
    states: Dict[str, RangeExhaustionState],
    active_session: Optional[str],
    cfg: SmcRangeExhaustionConfig,
) -> RangeExhaustionState:
    """Pick primary anchor per cfg._primary_rules for active_session.

    active_session=None → "session unknown": primary falls through cascade
    (d1_open → london_open → ny_open → week_open). Caller must mark primary
    state з `degraded=["session_context_unavailable"]` externally — this
    helper does not mutate states.

    Safety: якщо preferred відсутній — fallback cascade у тому ж порядку.
    """
    if active_session is None:
        preferred = "d1_open"
    else:
        preferred = cfg._primary_rules.get(active_session, "d1_open")
    state = states.get(preferred)
    if state is not None:
        return state
    for fallback in ("d1_open", "london_open", "ny_open", "week_open"):
        if fallback in states:
            return states[fallback]
    raise ValueError("range_exhaustion: no anchors computed (caller must guard)")


def compute_range_exhaustion(
    *,
    symbol: str,
    current_price: float,
    bars_d1: List[CandleBar],
    bars_h1: List[CandleBar],
    anchors: Dict[str, Tuple[int, float]],
    active_session: Optional[str] = None,
    now_ms: Optional[int] = None,
    cfg: Optional[SmcRangeExhaustionConfig] = None,
) -> RangeExhaustionSnapshot:
    """Compute range exhaustion across all supplied anchor points.

    active_session: "asia" | "london" | "ny" | "weekend" | None.
        None = session context unavailable (first-class state, not a hack):
        primary picks d1_open via cascade and is tagged with
        `degraded=["session_context_unavailable"]` на snapshot level.

    Missing anchors are skipped. If NO anchors supplied → returns fully-degraded
    fallback snapshot with `degraded=["no_anchors_available"]` on primary.
    """
    if cfg is None:
        cfg = SmcRangeExhaustionConfig()
    if now_ms is None:
        now_ms = int(time.time() * 1000)

    atr_d1 = compute_atr(bars_d1, period=cfg.atr_period) if bars_d1 else 0.0
    atr_h1 = compute_atr(bars_h1, period=cfg.atr_period) if bars_h1 else 0.0
    atr_session = atr_h1 * cfg.session_atr_scale

    states: Dict[str, RangeExhaustionState] = {}
    for anchor_kind, payload in anchors.items():
        if anchor_kind not in RANGE_ANCHOR_KINDS:
            continue
        open_ms, open_price = payload
        if anchor_kind in _SESSION_ANCHORS:
            baseline = atr_session
            bars_ok = bool(bars_h1)
        else:
            baseline = atr_d1
            bars_ok = bool(bars_d1)
        deg: List[str] = []
        if not bars_ok:
            deg.append("no_bars_for_baseline")
        states[anchor_kind] = _build_state(
            anchor_kind=anchor_kind,
            anchor_ms=open_ms,
            anchor_price=open_price,
            current_price=current_price,
            atr_baseline=baseline,
            cfg=cfg,
            degraded=deg,
        )

    if not states:
        fallback = _build_state(
            anchor_kind="d1_open",
            anchor_ms=0,
            anchor_price=current_price,
            current_price=current_price,
            atr_baseline=1.0,
            cfg=cfg,
            degraded=["no_anchors_available"],
        )
        return RangeExhaustionSnapshot(
            symbol=symbol,
            primary=fallback,
            by_anchor={"d1_open": fallback},
            computed_at_ms=now_ms,
        )

    primary = _select_primary(states, active_session, cfg)
    if active_session is None and "session_context_unavailable" not in primary.degraded:
        # First-class "session unknown" signal — loud degrade, not silent fallback.
        primary = dataclasses.replace(
            primary,
            degraded=[*primary.degraded, "session_context_unavailable"],
        )
        states = {**states, primary.anchor_kind: primary}
    return RangeExhaustionSnapshot(
        symbol=symbol,
        primary=primary,
        by_anchor=states,
        computed_at_ms=now_ms,
    )
