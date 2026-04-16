"""
core/smc/wake_check.py вЂ” Pure $0 condition checker (ADR-0049).

All functions: data in в†’ bool/value out. Zero I/O.
Testable with pytest, no mocks needed.

Invariants:
  - I0: core в†’ does NOT import runtime
  - S0: zero I/O, zero Redis, zero HTTP
"""
from __future__ import annotations

import datetime
from collections import deque
from typing import Any, Dict, List, Optional

from core.smc.wake_types import (
    AwarenessAccumulator,
    WakeCondition,
    WakeConditionKind,
)


def check_condition(
    cond: WakeCondition,
    price: float,
    atr: float,
    session_info: Dict[str, Any],
    ts_ms: int,
    last_wake_ts_ms: int = 0,
    structure_events: Optional[List[Dict[str, Any]]] = None,
) -> bool:
    """Check one wake condition against current market state.

    Every check is O(1) numeric comparison. $0 cost.

    Args:
        cond:       condition to check
        price:      current mid price
        atr:        ATR of reference TF (H4 preferred) for tolerance calcs
        session_info: {"current_session": "london", "is_open": True, ...}
        ts_ms:      current timestamp in milliseconds
        last_wake_ts_ms: timestamp of last wake (for max_silence)
        structure_events: list of new BOS/CHoCH events (for structure_break)

    Returns:
        True if condition matched.
    """
    kind = cond.kind
    p = cond.params

    if kind == WakeConditionKind.PRICE_CROSS:
        level = float(p.get("level", 0))
        direction = p.get("direction", "above")
        if direction == "above":
            return price >= level
        return price <= level

    if kind == WakeConditionKind.PRICE_ZONE_TOUCH:
        zone_high = float(p.get("zone_high", 0))
        zone_low = float(p.get("zone_low", 0))
        tolerance = float(p.get("tolerance_atr", 0.5)) * atr
        return (zone_low - tolerance) <= price <= (zone_high + tolerance)

    if kind == WakeConditionKind.SESSION_OPEN:
        target = p.get("session", "")
        current = session_info.get("current_session", "")
        is_open = session_info.get("is_open", False)
        return current.lower() == target.lower() and bool(is_open)

    if kind == WakeConditionKind.VOLATILITY_SPIKE:
        atr_mult = float(p.get("atr_mult", 2.0))
        bar_range = float(p.get("last_bar_range", 0))
        if atr <= 0:
            return False
        return bar_range > (atr * atr_mult)

    if kind == WakeConditionKind.MAX_SILENCE:
        hours = float(p.get("hours", 3))
        if last_wake_ts_ms <= 0:
            return True  # never woke before в†’ trigger immediately
        elapsed_h = (ts_ms - last_wake_ts_ms) / 3_600_000
        return elapsed_h >= hours

    if kind == WakeConditionKind.SCHEDULED:
        target_h = int(p.get("hour_utc", -1))
        target_m = int(p.get("minute_utc", -1))
        dt = datetime.datetime.utcfromtimestamp(ts_ms / 1000)
        return dt.hour == target_h and dt.minute == target_m

    if kind == WakeConditionKind.STRUCTURE_BREAK:
        if not structure_events:
            return False
        target_tf = p.get("tf_s")
        target_type = (p.get("type", "") or "").lower()  # "bos" or "choch"
        for ev in structure_events:
            tf_match = target_tf is None or ev.get("tf_s") == target_tf
            type_match = not target_type or (ev.get("type", "") or "").lower() == target_type
            if tf_match and type_match:
                return True
        return False

    return False


def accumulator_tick(
    acc: AwarenessAccumulator,
    price: float,
    prev_price: float,
    atr: float,
    session_events: Optional[List[str]] = None,
    gap_detected: bool = False,
    ts: float = 0.0,
) -> AwarenessAccumulator:
    """Update awareness accumulator with one tick. Returns NEW instance.

    Scoring rules:
      - |price_delta| / ATR           (normalized movement, main signal)
      - +1.0 per session change       (London/NY open = attention worthy)
      - +2.0 per gap > 0.5 ATR        (gap = potential opportunity)
      - decay: score *= 0.95/minute   (prevents infinite buildup)

    Args:
        acc:        current accumulator state
        price:      current price
        prev_price: previous price (from last tick)
        atr:        ATR for normalization (<=0 = skip normalization, use raw delta)
        session_events: ["london_open", "asia_close"] etc
        gap_detected: True if gap > 0.5 ATR detected
        ts:         current timestamp (seconds, time.time())

    Returns:
        New AwarenessAccumulator with updated score.
    """
    score = acc.score
    events_log = deque(acc.events_log, maxlen=50)

    # в”Ђв”Ђ Decay based on elapsed time в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ
    if acc.last_tick_ts > 0 and ts > acc.last_tick_ts:
        elapsed_min = (ts - acc.last_tick_ts) / 60.0
        if elapsed_min > 0:
            score *= acc.decay ** elapsed_min

    # в”Ђв”Ђ Price movement (main signal) в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ
    if prev_price > 0 and price > 0:
        delta = abs(price - prev_price)
        normalized = delta / atr if atr > 1.0 else delta
        score += normalized
        if normalized > 0.1:  # filter micro-noise
            events_log.append({
                "type": "price_move",
                "delta": round(delta, 2),
                "normalized": round(normalized, 3),
                "ts": ts,
            })

    # в”Ђв”Ђ Session change bonus в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ
    for ev in session_events or []:
        score += 1.0
        events_log.append({"type": "session", "event": ev, "ts": ts})

    # в”Ђв”Ђ Gap bonus в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ
    if gap_detected:
        score += 2.0
        events_log.append({"type": "gap", "ts": ts})

    return AwarenessAccumulator(
        score=score,
        threshold=acc.threshold,
        decay=acc.decay,
        events_log=events_log,
        last_tick_ts=ts,
        last_wake_price=acc.last_wake_price,
    )
