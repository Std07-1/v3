"""ADR-0039: Signal Engine — Numeric Entry/SL/TP + R:R + Confidence.

Pure, deterministic, NO I/O (S0, I0). All params from config (S5).
Consumes NarrativeBlock + SmcSnapshot and produces SignalSpec list + alerts.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from core.smc.types import (
    NarrativeBlock,
    SignalAlert,
    SignalSpec,
    SmcLevel,
    SmcSnapshot,
    SmcSwing,
    SmcZone,
)

logger = logging.getLogger(__name__)

# ── Default config (S5: overridden by config.json:smc.signals) ──────

_DEFAULTS: Dict[str, Any] = {
    "enabled": False,
    "entry_method": "ote",
    "sl_buffer_atr": 0.2,
    "tp_atr_multiplier": 2.0,
    "approach_atr_mult": 1.5,
    "signal_ttl_bars": 50,
    "max_active_signals": 3,
    "min_risk_reward": 1.5,
    "confidence_weights": {
        "bias_alignment": 0.30,
        "structure": 0.25,
        "confluence_grade": 0.20,
        "session": 0.15,
        "momentum": 0.10,
    },
}


# ── Entry / SL / TP resolution (§4.2–4.4) ──────────────────────────


def _resolve_entry(
    zone: SmcZone, direction: str, method: str, atr: float
) -> Tuple[float, str]:
    """Resolve entry price and description.

    Returns: (entry_price, entry_desc)
    """
    z_range = zone.high - zone.low
    if method == "zone_edge" or (method == "ote" and atr > 0 and z_range < 0.5 * atr):
        # Thin zone → edge entry (aggressive)
        if direction == "long":
            price = zone.low
            desc = f"Вхід з краю зони {zone.low:.0f}"
        else:
            price = zone.high
            desc = f"Вхід з краю зони {zone.high:.0f}"
        return price, desc

    if method == "ote":
        # OTE = 61.8% retracement into zone
        if direction == "long":
            price = zone.high - 0.618 * z_range  # deeper into zone = lower for long
            desc = f"OTE 61.8% {zone.low:.0f}–{zone.high:.0f} = {price:.1f}"
        else:
            price = zone.low + 0.618 * z_range  # deeper into zone = higher for short
            desc = f"OTE 61.8% {zone.low:.0f}–{zone.high:.0f} = {price:.1f}"
        return price, desc

    # zone_mid fallback
    price = (zone.high + zone.low) / 2.0
    desc = f"Середина зони {zone.low:.0f}–{zone.high:.0f} = {price:.1f}"
    return price, desc


def _resolve_stop_loss(
    zone: SmcZone, direction: str, atr: float, buffer_atr: float
) -> float:
    """SL = zone opposite edge + noise buffer.

    Buffer = max(zone_range × 0.35, atr × buffer_atr × 0.5).
    - zone_range × 0.35: proportional to zone size (structural)
    - atr × buffer_atr × 0.5: volatility floor (noise protection)
    Whichever is larger wins — avoids both absurd SL far from zone (old)
    and impossibly tight SL that noise kills immediately (too low floor).
    """
    zone_range = abs(zone.high - zone.low)
    structural = zone_range * 0.35
    noise_floor = atr * buffer_atr * 0.5 if atr > 0 else 0.0
    buf = max(structural, noise_floor)
    if direction == "long":
        return zone.low - buf
    else:
        return zone.high + buf


def _resolve_take_profit(
    snapshot: SmcSnapshot,
    zone: SmcZone,
    direction: str,
    current_price: float,
    atr: float,
    entry_price: float,
    tp_atr_mult: float,
) -> Tuple[float, bool]:
    """Resolve TP price. Returns (tp_price, is_fallback).

    Priority: key level → swing → ATR fallback.
    """
    max_dist = 12.0 * atr if atr > 0 else 0.0

    # Priority 1: Key level in direction
    kl = _find_target_level(snapshot.levels, direction, current_price, max_dist)
    if kl is not None:
        return kl.price, False

    # Priority 2: Swing extreme
    sw = _find_target_swing(snapshot.swings, direction, current_price, max_dist)
    if sw is not None:
        return sw.price, False

    # Priority 3: ATR fallback
    if direction == "long":
        return entry_price + atr * tp_atr_mult, True
    else:
        return entry_price - atr * tp_atr_mult, True


def _find_target_level(
    levels: List[SmcLevel], direction: str, current_price: float, max_dist: float
) -> Optional[SmcLevel]:
    target_kinds = (
        {"pdl", "dl", "p_h4_l", "h4_l", "p_h1_l", "h1_l"}
        if direction == "short"
        else {"pdh", "dh", "p_h4_h", "h4_h", "p_h1_h", "h1_h"}
    )
    best: Optional[SmcLevel] = None
    best_dist = float("inf")
    for lv in levels:
        if lv.kind not in target_kinds:
            continue
        if direction == "short" and lv.price >= current_price:
            continue
        if direction == "long" and lv.price <= current_price:
            continue
        dist = abs(lv.price - current_price)
        if max_dist > 0 and dist > max_dist:
            continue
        if dist < best_dist:
            best = lv
            best_dist = dist
    return best


def _find_target_swing(
    swings: List[SmcSwing], direction: str, current_price: float, max_dist: float
) -> Optional[SmcSwing]:
    target_kinds = {"ll", "lh"} if direction == "short" else {"hh", "hl"}
    for sw in reversed(swings):
        if sw.kind not in target_kinds:
            continue
        if direction == "short" and sw.price >= current_price:
            continue
        if direction == "long" and sw.price <= current_price:
            continue
        dist = abs(sw.price - current_price)
        if max_dist > 0 and dist > max_dist:
            continue
        return sw
    return None


# ── R:R calculation ────────────────────────────────────────────────


def _calc_risk_reward(entry: float, sl: float, tp: float, direction: str) -> float:
    """R:R = reward / risk. Returns 0.0 if risk ≤ 0."""
    if direction == "long":
        risk = entry - sl
        reward = tp - entry
    else:
        risk = sl - entry
        reward = entry - tp
    if risk <= 0:
        return 0.0
    return round(reward / risk, 2)


# ── Confidence score (§4.5) ────────────────────────────────────────


def _calc_confidence(
    bias_map: Dict[str, str],
    direction: str,
    zone_grade_info: dict,
    snapshot: SmcSnapshot,
    session_info: Optional[Tuple[str, bool]],
    momentum_map: Dict[str, dict],
    weights: Dict[str, float],
) -> Tuple[int, Dict[str, int]]:
    """Composite confidence 0–100. Returns (score, factors_dict)."""
    factors: Dict[str, int] = {}

    # 1. Bias alignment (D1+H4)
    d1_bias = bias_map.get("86400", "")
    h4_bias = bias_map.get("14400", "")
    expected = "bullish" if direction == "long" else "bearish"
    d1_match = d1_bias == expected
    h4_match = h4_bias == expected
    if d1_match and h4_match:
        factors["bias_alignment"] = 100
    elif d1_match or h4_match:
        factors["bias_alignment"] = 50
    else:
        factors["bias_alignment"] = 0

    # 2. Structure (recent BOS/CHoCH aligned with trade direction).
    # SWING_KINDS uses suffixed forms: "bos_bull"/"bos_bear", "choch_bull"/"choch_bear".
    # Old code compared against bare "bos"/"choch" which never matched → structure
    # factor was silently 0, degrading every signal confidence by 25% (P0 fix).
    bos_kind = "bos_bull" if direction == "long" else "bos_bear"
    choch_kind = "choch_bull" if direction == "long" else "choch_bear"
    bos_count = 0
    choch_count = 0
    for sw in snapshot.swings[-20:]:
        if sw.kind == bos_kind:
            bos_count += 1
        elif sw.kind == choch_kind:
            choch_count += 1
    if choch_count > 0 and bos_count > 0:
        factors["structure"] = 100
    elif bos_count > 0:
        factors["structure"] = 75
    elif choch_count > 0:
        factors["structure"] = 50
    else:
        factors["structure"] = 0

    # 3. Confluence grade (from zone_grades scoring)
    score = zone_grade_info.get("score", 0)
    if score >= 8:
        factors["confluence_grade"] = 100
    elif score >= 6:
        factors["confluence_grade"] = 80
    elif score >= 4:
        factors["confluence_grade"] = 50
    elif score >= 2:
        factors["confluence_grade"] = 20
    else:
        factors["confluence_grade"] = 0

    # 4. Session
    sess_name, in_kz = session_info if session_info else ("", False)
    if in_kz:
        factors["session"] = 100
    elif sess_name:
        factors["session"] = 50
    else:
        factors["session"] = 0

    # 5. Momentum (displacement bars in direction)
    total_b = sum(m.get("b", 0) for m in momentum_map.values())
    total_r = sum(m.get("r", 0) for m in momentum_map.values())
    if direction == "long":
        mom = total_b
    else:
        mom = total_r
    if mom >= 2:
        factors["momentum"] = 100
    elif mom >= 1:
        factors["momentum"] = 50
    else:
        factors["momentum"] = 0

    # Weighted composite
    total = 0.0
    for k, v in factors.items():
        w = weights.get(k, 0.0)
        total += v * w
    return int(round(total)), factors


# ── Signal lifecycle (§4.6) ────────────────────────────────────────


# Terminal signal states (cannot transition further)
_TERMINAL_STATES = frozenset({"invalidated", "completed", "expired", "skipped"})


def _determine_state(
    trigger: str,
    current_price: float,
    zone: SmcZone,
    entry_price: float,
    stop_loss: float,
    take_profit: float,
    direction: str,
    atr: float,
    approach_mult: float,
    was_active: bool = False,
) -> Tuple[str, str]:
    """Map narrative trigger + price location → signal state + reason.

    States: pending → watch → approaching → active → ready → completed/invalidated
    'watch' = zone of interest (entry > approach_mult × ATR from price)
    'skipped' = price reached TP without signal ever being active/ready
    """
    # Check invalidation: price broke SL level
    if direction == "long" and current_price < stop_loss:
        return "invalidated", "Ціна пробила SL {:.0f}".format(stop_loss)
    if direction == "short" and current_price > stop_loss:
        return "invalidated", "Ціна пробила SL {:.0f}".format(stop_loss)

    # Check TP reached
    tp_reached = (direction == "long" and current_price >= take_profit) or (
        direction == "short" and current_price <= take_profit
    )
    if tp_reached:
        if was_active:
            return "completed", "Target {:.0f} досягнуто".format(take_profit)
        return "skipped", "Ціна пройшла target {:.0f} без входу".format(take_profit)

    # Distance filter → watch (zone of interest, not actionable yet)
    dist = abs(current_price - entry_price)
    if atr > 0 and dist > approach_mult * atr:
        return "watch", "Зона інтересу: {:.0f} пт до entry".format(dist)

    # Map narrative trigger states
    if trigger in ("ready", "triggered"):
        return "ready", "Структура підтверджена"

    if trigger == "in_zone":
        return "active", "Ціна у зоні, чекаємо підтвердження"

    if trigger == "approaching":
        return "approaching", "Наближення до зони"

    return "pending", "Зона виявлена"


def _make_alert(
    signal: SignalSpec, old_state: str, now_ms: int
) -> Optional[SignalAlert]:
    """Generate alert if state changed to a notable state."""
    if signal.state == old_state:
        return None

    _PRIORITY = {
        "approaching": "low",
        "active": "medium",
        "ready": "high",
        "invalidated": "medium",
        "completed": "medium",
        "skipped": "low",
        # "watch" excluded — informational, no alert
    }
    priority = _PRIORITY.get(signal.state)
    if priority is None:
        return None

    _dir = "▲" if signal.direction == "long" else "▼"
    headline = (
        f"{signal.symbol}: {_dir} {signal.grade} {signal.state.upper()} — "
        f"entry {signal.entry_price:.0f}, R:R {signal.risk_reward:.1f}:1"
    )
    return SignalAlert(
        signal_id=signal.signal_id,
        alert_type=signal.state,
        headline=headline,
        priority=priority,
        ts_ms=now_ms,
    )


# ── Main entry point ───────────────────────────────────────────────


def synthesize_signals(
    narrative: NarrativeBlock,
    snapshot: SmcSnapshot,
    zone_grades: Dict[str, dict],
    bias_map: Dict[str, str],
    momentum_map: Dict[str, dict],
    current_price: float,
    atr: float,
    config: Dict[str, Any],
    previous_signals: List[SignalSpec],
    now_ms: int,
    session_info: Optional[Tuple[str, bool]] = None,
) -> Tuple[List[SignalSpec], List[SignalAlert]]:
    """ADR-0039: synthesize numeric signals from narrative + SMC data.

    Pure, deterministic (S2). Returns (signals, alerts).
    """
    cfg = {**_DEFAULTS, **config}
    weights = cfg.get("confidence_weights", _DEFAULTS["confidence_weights"])
    max_signals = cfg.get("max_active_signals", 3)
    ttl_bars = cfg.get("signal_ttl_bars", 50)
    min_rr = cfg.get("min_risk_reward", 1.5)
    entry_method = cfg.get("entry_method", "ote")
    sl_buffer = cfg.get("sl_buffer_atr", 0.2)
    tp_atr_mult = cfg.get("tp_atr_multiplier", 2.0)
    approach_mult = cfg.get("approach_atr_mult", 1.5)

    # Index previous signals by zone_id for lifecycle continuity
    prev_by_zone: Dict[str, SignalSpec] = {s.zone_id: s for s in previous_signals}

    signals: List[SignalSpec] = []
    alerts: List[SignalAlert] = []

    # Build zone lookup from snapshot
    zone_by_id: Dict[str, SmcZone] = {z.id: z for z in snapshot.zones}

    for scenario in narrative.scenarios[:max_signals]:
        zone = zone_by_id.get(scenario.zone_id)
        if zone is None:
            continue

        direction = scenario.direction
        gi = zone_grades.get(zone.id, {})
        grade = gi.get("grade", "C")

        # Lifecycle
        prev = prev_by_zone.get(scenario.zone_id)
        created_ms = prev.created_ms if prev else now_ms
        bars_alive = (prev.bars_alive + 1) if prev else 0

        # TP / SL / Entry lock: once computed, values are immutable (D3 fix)
        if prev and prev.state not in _TERMINAL_STATES:
            entry_price = prev.entry_price
            entry_desc = prev.entry_desc
            stop_loss = prev.stop_loss
            tp_price = prev.take_profit
            tp_fallback = False
            rr = prev.risk_reward
        else:
            entry_price, entry_desc = _resolve_entry(zone, direction, entry_method, atr)
            stop_loss = _resolve_stop_loss(zone, direction, atr, sl_buffer)
            tp_price, tp_fallback = _resolve_take_profit(
                snapshot,
                zone,
                direction,
                current_price,
                atr,
                entry_price,
                tp_atr_mult,
            )
            rr = _calc_risk_reward(entry_price, stop_loss, tp_price, direction)

        # Warnings
        warns: List[str] = []
        if tp_fallback:
            warns.append("no_target_found")
        if rr < min_rr:
            warns.append("low_risk_reward")

        # Confidence (always recalculated — live market context)
        confidence, conf_factors = _calc_confidence(
            bias_map, direction, gi, snapshot, session_info, momentum_map, weights
        )

        # Was signal ever active/ready? (for skipped vs completed)
        was_active = prev is not None and prev.state in ("active", "ready")

        # TTL check
        if bars_alive > ttl_bars:
            state, state_reason = "expired", f"TTL {ttl_bars} барів вичерпано"
        else:
            state, state_reason = _determine_state(
                scenario.trigger,
                current_price,
                zone,
                entry_price,
                stop_loss,
                tp_price,
                direction,
                atr,
                approach_mult,
                was_active=was_active,
            )

        # Preserve terminal states from previous
        if prev and prev.state in _TERMINAL_STATES:
            state = prev.state
            state_reason = prev.state_reason

        sess_name = session_info[0] if session_info else ""
        in_kz = session_info[1] if session_info else False

        # Deterministic signal ID — survives restarts (D1 fix)
        sig_id = (
            prev.signal_id
            if prev
            else f"sig_{snapshot.symbol}_{snapshot.tf_s}_{zone.id}"
        )

        sig = SignalSpec(
            signal_id=sig_id,
            zone_id=zone.id,
            symbol=snapshot.symbol,
            tf_s=snapshot.tf_s,
            direction=direction,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=tp_price,
            risk_reward=rr,
            entry_method=entry_method,
            entry_desc=entry_desc,
            confidence=confidence,
            confidence_factors=conf_factors,
            grade=grade,
            state=state,
            state_reason=state_reason,
            created_ms=created_ms,
            updated_ms=now_ms,
            bars_alive=bars_alive,
            session=sess_name,
            in_killzone=in_kz,
            warnings=warns,
        )
        signals.append(sig)

        # Alert on state transition
        old_state = prev.state if prev else ""
        alert = _make_alert(sig, old_state, now_ms)
        if alert is not None:
            alerts.append(alert)

    return signals, alerts
