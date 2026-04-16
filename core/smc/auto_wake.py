"""
core/smc/auto_wake.py вЂ” Platform-generated wake conditions (ADR-0049 Strategy B).

Platform looks at SMC data already computed by SmcRunner and generates
baseline wake conditions WITHOUT bot participation. This breaks the deadlock:

  Bot in IDLE в†’ can't set conditions в†’ platform does it в†’ bot wakes up

I7 compliant: platform doesn't decide FOR РђСЂС‡С– вЂ” only wakes him.
S0 compliant: pure function, no I/O, no Redis, no HTTP.

Pattern: same as synthesize_narrative() in core/smc/narrative.py вЂ”
  accepts multi-TF snapshots dict, returns structured result.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from core.smc.types import SmcSnapshot, SmcZone
from core.smc.wake_types import WakeCondition, WakeConditionKind


# в”Ђв”Ђ Defaults (overridable via config.json wake_engine section) в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ

DEFAULT_ZONE_PROXIMITY_ATR = 2.0    # wake when price within 2 ATR of A+/A zone
DEFAULT_MAX_SILENCE_HOURS = 3.0     # default max_silence safety net
MAX_PLATFORM_CONDITIONS = 10        # cap to prevent event flooding
_GRADE_ACCEPT = {"A+", "A"}         # only top-grade zones generate conditions
_ZONE_SCORE_MIN = 6                 # minimum confluence score


def generate_platform_conditions(
    snapshots: Dict[int, SmcSnapshot],
    bias_map: Dict[int, str],
    atr: float,
    current_price: float,
    session_info: Dict[str, Any],
    ts_ms: int = 0,
    zone_grades: Optional[Dict[str, Dict[str, Any]]] = None,
    config: Optional[Dict[str, Any]] = None,
) -> List[WakeCondition]:
    """Generate wake conditions from current SMC state.

    Called by WakeEngine every 2s tick. Pure function, $0 cost.

    Args:
        snapshots:    {tf_s: SmcSnapshot} вЂ” multi-TF view
                      D1 (86400) for bias, H4 (14400) + H1 (3600) for zones
        bias_map:     {tf_s: "bullish"|"bearish"|None}
        atr:          ATR of reference TF (H4 preferred)
        current_price: live price
        session_info: {"current_session": "london", "is_open": True,
                       "next_session": "new_york", "next_open_min": 45}
        ts_ms:        current timestamp ms
        zone_grades:  {zone_id: {"grade": "A+", "score": 8}} вЂ” optional enrichment
        config:       optional override for defaults

    Returns:
        List of WakeCondition, max MAX_PLATFORM_CONDITIONS items.
    """
    if atr <= 0 or current_price <= 0:
        return []

    cfg = config or {}
    proximity_atr = cfg.get("zone_proximity_atr", DEFAULT_ZONE_PROXIMITY_ATR)
    max_silence_h = cfg.get("max_silence_hours", DEFAULT_MAX_SILENCE_HOURS)
    grade_accept = cfg.get("grade_accept", _GRADE_ACCEPT)
    score_min = cfg.get("zone_score_min", _ZONE_SCORE_MIN)

    conditions: List[WakeCondition] = []
    now_ms = ts_ms or int(time.time() * 1000)

    # в”Ђв”Ђ 1. Zone proximity conditions в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ
    # Collect all active zones from multi-TF snapshots, filter by grade,
    # sort by distance to price, take top 3.
    zone_candidates: List[tuple] = []  # (distance_atr, zone, tf_s)

    for tf_s, snap in snapshots.items():
        for z in snap.zones:
            if z.status not in ("active", "untested"):
                continue
            # Grade filtering: use zone_grades dict if provided
            grade = ""
            if zone_grades and z.id in zone_grades:
                gi = zone_grades[z.id]
                grade = gi.get("grade", "")
                score = gi.get("score", 0)
                if grade not in grade_accept or score < score_min:
                    continue
            # Distance from price to zone (nearest edge)
            if current_price > z.high:
                dist = current_price - z.high
            elif current_price < z.low:
                dist = z.low - current_price
            else:
                dist = 0  # price inside zone вЂ” definitely trigger
            dist_atr = dist / atr
            if dist_atr <= proximity_atr:
                zone_candidates.append((dist_atr, z, tf_s))

    # Sort by distance (closest first), take top 3
    zone_candidates.sort(key=lambda x: x[0])
    for dist_atr, z, tf_s in zone_candidates[:3]:
        direction = "below" if current_price > z.high else "above"
        conditions.append(WakeCondition(
            kind=WakeConditionKind.PRICE_ZONE_TOUCH,
            params={
                "zone_high": z.high,
                "zone_low": z.low,
                "tolerance_atr": 0.3,
                "zone_id": z.id,
                "zone_kind": z.kind,
                "tf_s": tf_s,
            },
            reason=(
                f"Price approaching {z.kind} zone {z.low:.0f}-{z.high:.0f} "
                f"({dist_atr:.1f} ATR away, TF={tf_s}s)"
            ),
            source="platform",
            created_at_ms=now_ms,
        ))

    # в”Ђв”Ђ 2. Session transition conditions в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ
    # London and NY opens are always interesting for XAU/USD.
    next_session = session_info.get("next_session", "")
    next_open_min = session_info.get("next_open_min", -1)
    is_open = session_info.get("is_open", False)

    if next_session and 0 < next_open_min <= 5:
        # Session opening within 5 minutes
        conditions.append(WakeCondition(
            kind=WakeConditionKind.SESSION_OPEN,
            params={"session": next_session},
            reason=f"{next_session.title()} session opens in ~{next_open_min}min",
            source="platform",
            created_at_ms=now_ms,
        ))

    # в”Ђв”Ђ 3. Bias divergence detection в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ
    # D1 bearish but H4 bullish (or vice versa) = potential reversal point
    d1_bias = bias_map.get(86400, "")
    h4_bias = bias_map.get(14400, "")
    if d1_bias and h4_bias and d1_bias != h4_bias:
        conditions.append(WakeCondition(
            kind=WakeConditionKind.STRUCTURE_BREAK,
            params={"type": "bias_divergence", "d1": d1_bias, "h4": h4_bias},
            reason=f"HTF bias divergence: D1={d1_bias}, H4={h4_bias}",
            source="platform",
            created_at_ms=now_ms,
        ))

    # в”Ђв”Ђ 4. Default max_silence (always present) в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ
    conditions.append(WakeCondition(
        kind=WakeConditionKind.MAX_SILENCE,
        params={"hours": max_silence_h},
        reason=f"Safety net: max {max_silence_h}h without analysis",
        source="platform",
        created_at_ms=now_ms,
    ))

    return conditions[:MAX_PLATFORM_CONDITIONS]
