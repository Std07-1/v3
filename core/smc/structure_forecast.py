"""
core/smc/structure_forecast.py — Anticipatory structure forecast (ADR-0087).

Phase 1 of the `structure_imminent` wake-kind: derive armed (protected) swing
levels by replaying a snapshot's swings stream, then auto-generate resolved
`structure_imminent` conditions (CHoCH-first) for the WakeEngine.

Deliberate mirror (ADR-0087 §2.2, D15.2 debt): consumption + gating semantics
replicate detect_structure_events() internals — guarded by a consistency test
against the real detector (tests/test_structure_forecast.py). If the detector
ever exports its armed levels directly, switch to that and delete the replay.

Invariants:
  - I0: core → does NOT import runtime
  - S0: zero I/O, zero Redis, zero HTTP
  - W0 (ADR-0087): structure.py / swings.py untouched
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

from core.smc.types import SmcSnapshot, SmcSwing
from core.smc.wake_types import WakeCondition, WakeConditionKind

# Swing kinds that arm a protected level (mirror structure.py:186-193)
_ARMING_KINDS = ("hh", "hl", "ll", "lh")

# Structure event kind → armed level kind it consumes (mirror structure.py:170-178)
_CONSUMES = {
    "bos_bull": "hh",
    "choch_bear": "hl",
    "bos_bear": "ll",
    "choch_bull": "lh",
}

# trend_bias → forecastable break targets: (event_kind, armed_kind, direction).
# Mirror of candidate gating in structure.py:203-241, incl. cold start
# (trend_bias=None → BOS only, CHoCH needs an established trend).
_TARGETS_BY_BIAS: Dict[Optional[str], Tuple[Tuple[str, str, str], ...]] = {
    "bullish": (("bos_bull", "hh", "above"), ("choch_bear", "hl", "below")),
    "bearish": (("bos_bear", "ll", "below"), ("choch_bull", "lh", "above")),
    None: (("bos_bull", "hh", "above"), ("bos_bear", "ll", "below")),
}

# Defaults (overridable via config.json wake_engine.structure_imminent)
DEFAULT_TARGETS = ("choch",)  # CHoCH-first (ADR-0087 rev 2, owner goal)
DEFAULT_PROX_ATR = 1.0
DEFAULT_ARM_RADIUS_ATR = 2.0
DEFAULT_WINDOW_S = 1800
MAX_IMMINENT_CONDITIONS = 4


def derive_armed_levels(
    swings: Sequence[SmcSwing],
) -> Dict[str, Optional[SmcSwing]]:
    """Replay classified swings + structure events → currently armed levels.

    Input: snapshot.swings (classified + events merged and time-sorted,
    engine.py:989-990). Tie-break at equal time_ms: a swing arms BEFORE an
    event consumes — mirror of structure.py:182-197 where the while-loop arms
    swings up to the bar before break detection on that same bar.

    Returns {"hh": SmcSwing|None, "hl": ..., "ll": ..., "lh": ...}.
    Unknown kinds (fractals, inducement, momentum) are ignored.
    """
    armed: Dict[str, Optional[SmcSwing]] = {k: None for k in _ARMING_KINDS}
    ordered = sorted(
        swings,
        key=lambda s: (s.time_ms, 0 if s.kind in _ARMING_KINDS else 1),
    )
    for s in ordered:
        if s.kind in _ARMING_KINDS:
            armed[s.kind] = s
        else:
            consumed_kind = _CONSUMES.get(s.kind)
            if consumed_kind is not None:
                armed[consumed_kind] = None
    return armed


def generate_imminent_conditions(
    tf_pairs: Sequence[Tuple[int, int]],
    snapshots: Dict[int, SmcSnapshot],
    atr_by_tf: Dict[int, float],
    current_price: float,
    ts_ms: int,
    config: Optional[Dict[str, Any]] = None,
) -> List[WakeCondition]:
    """Auto-arm resolved structure_imminent conditions (ADR-0087 §2.2 rev 2).

    Pure, $0 — called by WakeEngine every tick alongside
    generate_platform_conditions(). The level is resolved at generation time,
    so the condition carries ready numbers and dies naturally when the level
    is consumed by a confirmed break or price leaves the arm radius.

    Args:
        tf_pairs:      [(leading_tf_s, target_tf_s), ...] e.g. [(300, 900)]
        snapshots:     {target_tf_s: raw SmcSnapshot} — MUST be raw per-TF
                       snapshots (no display HTF injection)
        atr_by_tf:     {target_tf_s: ATR} for prox normalization
        current_price: live price
        ts_ms:         current timestamp ms
        config:        wake_engine.structure_imminent section (SSOT)
    """
    if current_price <= 0:
        return []

    cfg = config or {}
    targets = tuple(cfg.get("targets", DEFAULT_TARGETS))
    prox_atr = float(cfg.get("prox_atr", DEFAULT_PROX_ATR))
    arm_radius_atr = float(cfg.get("arm_radius_atr", DEFAULT_ARM_RADIUS_ATR))
    window_s = int(cfg.get("window_s", DEFAULT_WINDOW_S))
    max_conditions = int(cfg.get("max_conditions", MAX_IMMINENT_CONDITIONS))

    candidates: List[Tuple[float, WakeCondition]] = []
    for leading_tf_s, target_tf_s in tf_pairs:
        snap = snapshots.get(target_tf_s)
        if snap is None or not snap.swings:
            continue
        atr_tf = float(atr_by_tf.get(target_tf_s, 0.0))
        if atr_tf <= 0:
            continue

        armed = derive_armed_levels(snap.swings)
        for event_kind, armed_kind, direction in _TARGETS_BY_BIAS.get(
            snap.trend_bias, _TARGETS_BY_BIAS[None]
        ):
            if event_kind.split("_")[0] not in targets:
                continue
            level_swing = armed[armed_kind]
            if level_swing is None:
                continue

            dist_atr = abs(current_price - level_swing.price) / atr_tf
            # Beyond-level state (§2.2b pending) is always armable; the
            # approach side is gated by arm_radius_atr.
            beyond = (
                current_price >= level_swing.price
                if direction == "above"
                else current_price <= level_swing.price
            )
            if not beyond and dist_atr > arm_radius_atr:
                continue

            candidates.append(
                (
                    dist_atr,
                    WakeCondition(
                        kind=WakeConditionKind.STRUCTURE_IMMINENT,
                        params={
                            "tf_s": target_tf_s,
                            "leading_tf_s": leading_tf_s,
                            "target": event_kind,
                            "level": level_swing.price,
                            "direction": direction,
                            "protected_swing": armed_kind,
                            "prox_atr": prox_atr,
                            "window_s": window_s,
                            "atr_tf": round(atr_tf, 4),
                        },
                        reason=(
                            f"Imminent {event_kind} on TF={target_tf_s}s: "
                            f"protected {armed_kind.upper()} "
                            f"{level_swing.price:.1f} is {dist_atr:.1f} ATR "
                            f"away (leading TF={leading_tf_s}s)"
                        ),
                        source="platform",
                        created_at_ms=ts_ms,
                    ),
                )
            )

    candidates.sort(key=lambda item: item[0])
    return [cond for _, cond in candidates[:max_conditions]]
