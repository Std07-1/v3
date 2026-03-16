"""
core/smc/narrative.py — Context Flow Narrative Engine (ADR-0033 Rev 2).

S0: pure logic, NO I/O.
N0: pure function, deterministic for same input.
N3: synthesize_narrative() НІКОЛИ не повертає None — fallback block з warnings.
N5: market_phase display-only, не впливає на mode.
N6: alignment = D1+H4 only.
"""

from __future__ import annotations

import logging

from typing import Dict, List, Optional, Tuple

from core.smc.types import (
    ActiveScenario,
    NarrativeBlock,
    SmcLevel,
    SmcSnapshot,
    SmcSwing,
    SmcZone,
)

logger = logging.getLogger(__name__)

# ── Headlines (ADR-0033 §3.2) ──────────────────────────────

_MODE_HEADLINES = {
    ("trade", "aligned", "long"): "\U0001f7e2 BUY setup ready",
    ("trade", "aligned", "short"): "\U0001f534 SELL setup ready",
    ("trade", "reduced", "long"): "\U0001f7e2 BUY \u2014 reduced: mixed HTF",
    ("trade", "reduced", "short"): "\U0001f534 SELL \u2014 reduced: mixed HTF",
    ("trade", "counter", "long"): "\U0001f7e1 BUY \u2014 counter-trend",
    ("trade", "counter", "short"): "\U0001f7e1 SELL \u2014 counter-trend",
}
_WAIT_HEADLINE = "\U0001f7e1 No setup \u2014 wait"
_DEGRADED_HEADLINE = "\u26a0 Narrative unavailable"

# ── TF labels ───────────────────────────────────────────────

_TF_LABELS = {
    60: "M1",
    180: "M3",
    300: "M5",
    900: "M15",
    1800: "M30",
    3600: "H1",
    14400: "H4",
    86400: "D1",
}


def _tf_to_label(tf_s):
    # type: (int) -> str
    return _TF_LABELS.get(tf_s, "TF{}".format(tf_s))


# ── Fallback (N3 / BH-8) ───────────────────────────────────


def _fallback_narrative_block(warnings=None):
    # type: (Optional[List[str]]) -> NarrativeBlock
    return NarrativeBlock(
        mode="wait",
        sub_mode="",
        headline=_DEGRADED_HEADLINE,
        bias_summary="",
        scenarios=[],
        next_area="",
        fvg_context="",
        market_phase="ranging",
        warnings=warnings or ["computation_error"],
    )


# ── HTF Alignment (SC-4: D1+H4 only, N6) ──────────────────


def _resolve_htf_alignment(bias_map):
    # type: (Dict[str, str]) -> Tuple[str, Optional[str]]
    """Returns (alignment, direction).

    alignment: 'aligned' | 'mixed' | 'no_data'
    direction: 'bullish'|'bearish' if aligned, None otherwise.
    """
    d1 = bias_map.get("86400")
    h4 = bias_map.get("14400")
    if not d1 or not h4:
        return ("no_data", None)
    if d1 == h4:
        return ("aligned", d1)
    return ("mixed", None)


# ── Zone direction ──────────────────────────────────────────


def _zone_direction(zone):
    # type: (SmcZone) -> str
    return "short" if "bear" in zone.kind else "long"


# ── Invalidation (SC-7 / BH-1) ─────────────────────────────


def _find_invalidation(zone):
    # type: (SmcZone) -> str
    if "bear" in zone.kind:
        return "Above {:.0f}".format(zone.high)
    return "Below {:.0f}".format(zone.low)


def _is_invalidated(zone, current_price):
    # type: (SmcZone, float) -> bool
    if "bear" in zone.kind:
        return current_price > zone.high
    return current_price < zone.low


# ── Entry description ───────────────────────────────────────


def _format_entry(zone, grade_info):
    # type: (SmcZone, dict) -> str
    arrow = "\u25bc" if "bear" in zone.kind else "\u25b2"
    grade = grade_info.get("grade", "C")
    score = grade_info.get("score", 0)
    kind_short = "OB" if "ob" in zone.kind else "FVG"
    return "{kind}{arrow} {grade}({score}) {low:.0f}\u2013{high:.0f}".format(
        kind=kind_short,
        arrow=arrow,
        grade=grade,
        score=score,
        low=zone.low,
        high=zone.high,
    )


# ── Trigger Resolution (T-2: 4 IOFED states, proximity + displacement) ──


def _find_qualifying_structure_break(snapshot, zone):
    # type: (SmcSnapshot, SmcZone) -> Optional[SmcSwing]
    """Find the most recent CHoCH/BOS aligned with zone direction.

    Returns the swing if found (after zone creation), None otherwise.
    """
    target_kinds = (
        ("choch_bear", "bos_bear")
        if "bear" in zone.kind
        else ("choch_bull", "bos_bull")
    )
    for sw in reversed(snapshot.swings):
        if sw.kind in target_kinds and sw.time_ms >= zone.anchor_bar_ms:
            return sw
    return None


def _has_displacement_near(snapshot, choch_swing, zone, viewer_tf_s, config):
    # type: (SmcSnapshot, SmcSwing, SmcZone, int, dict) -> bool
    """Displacement marker within \u00b1N viewer-TF bars of the CHoCH.

    Doctrine: MS Shift requires impulse/displacement, not just structure break.
    """
    window_bars = config.get("trigger_displacement_window", 3)
    window_ms = window_bars * viewer_tf_s * 1000
    target_kind = "displacement_bear" if "bear" in zone.kind else "displacement_bull"
    choch_ms = choch_swing.time_ms
    for sw in snapshot.swings:
        if sw.kind == target_kind and abs(sw.time_ms - choch_ms) <= window_ms:
            return True
    return False


def _is_price_proximate(zone, current_price, atr, config):
    # type: (SmcZone, float, float, dict) -> bool
    """Price within trigger_proximity_atr \u00d7 ATR of the nearest zone edge."""
    if atr <= 0:
        return True  # Cannot measure, be permissive
    max_atr = config.get("trigger_proximity_atr", 3.0)
    if current_price < zone.low:
        distance = zone.low - current_price
    elif current_price > zone.high:
        distance = current_price - zone.high
    else:
        return True  # Inside zone
    return distance <= max_atr * atr


def _resolve_trigger_state(snapshot, zone, viewer_tf_s, current_price, atr, config):
    # type: (SmcSnapshot, SmcZone, int, float, float, dict) -> str
    """T-2: 4 IOFED trigger states with proximity + displacement guards.

    Matrix (doctrine: proximity to POI + displacement confirmation):
      CHoCH + displacement + in_zone          \u2192 \"ready\"
      CHoCH + in_zone (no displacement)       \u2192 \"in_zone\"
      CHoCH + displacement + proximate        \u2192 \"triggered\"
      CHoCH + not proximate                   \u2192 \"approaching\"
      CHoCH + proximate (no displacement)     \u2192 \"approaching\"
      no CHoCH + in_zone                      \u2192 \"in_zone\"
      no CHoCH                                \u2192 \"approaching\"
    """
    in_zone = zone.low <= current_price <= zone.high
    choch = _find_qualifying_structure_break(snapshot, zone)
    has_disp = False
    if choch is not None:
        has_disp = _has_displacement_near(snapshot, choch, zone, viewer_tf_s, config)
    proximate = _is_price_proximate(zone, current_price, atr, config)
    if choch is not None and has_disp and in_zone:
        return "ready"
    if choch is not None and in_zone:
        return "in_zone"
    if choch is not None and has_disp and proximate:
        return "triggered"
    if in_zone:
        return "in_zone"
    return "approaching"


def _resolve_trigger_desc(snapshot, zone, viewer_tf_s, current_price, atr, config):
    # type: (SmcSnapshot, SmcZone, int, float, float, dict) -> str
    state = _resolve_trigger_state(
        snapshot, zone, viewer_tf_s, current_price, atr, config
    )
    direction_arrow = "\u2193" if "bear" in zone.kind else "\u2191"
    tf_label = _tf_to_label(viewer_tf_s)

    if state == "ready":
        return "Ready: structure + displacement confirmed in zone"
    if state == "triggered":
        return "Triggered: {tf} CHoCH{a} + displacement \u2014 seek entry".format(
            tf=tf_label, a=direction_arrow
        )
    if state == "in_zone":
        choch = _find_qualifying_structure_break(snapshot, zone)
        if choch is not None:
            return "In zone: CHoCH{a} seen, await displacement".format(
                a=direction_arrow
            )
        return "In zone: wait {tf} CHoCH{a}".format(tf=tf_label, a=direction_arrow)
    # approaching
    if current_price < zone.low:
        dist = zone.low - current_price
    elif current_price > zone.high:
        dist = current_price - zone.high
    else:
        dist = 0.0
    return "Approaching: {:.0f} pts from zone".format(dist)


# ── Target Resolution ───────────────────────────────────────


def _find_target(snapshot, zone, direction, config, current_price=0.0, atr=0.0):
    # type: (SmcSnapshot, SmcZone, str, dict, float, float) -> Optional[str]
    """Find target price. Returns None if unknown (BH-4).

    ATR-distance filter: targets farther than target_max_atr_distance * ATR
    from current_price are skipped (prevents D1-level PDL on M5 chart).
    """
    max_atr_mult = config.get("target_max_atr_distance", 12)
    max_dist = max_atr_mult * atr if atr > 0 else 0.0

    # Priority 1: Key level
    target_level = _find_target_key_level(
        snapshot.levels, direction, current_price, max_dist
    )
    if target_level:
        return "{kind} {price:.0f}".format(
            kind=target_level.kind.upper().replace("_", " "),
            price=target_level.price,
        )

    # Priority 2: opposite-side institutional zone
    for z in snapshot.zones:
        if z.id == zone.id or z.status == "mitigated":
            continue
        z_dir = _zone_direction(z)
        if z_dir != direction and z.context_layer == "institutional":
            center = (z.high + z.low) / 2
            # P2: directional gate — target zone must be ahead of price
            if direction == "short" and center >= current_price:
                continue
            if direction == "long" and center <= current_price:
                continue
            if max_dist > 0 and abs(center - current_price) > max_dist:
                continue
            return "HTF {kind} {low:.0f}\u2013{high:.0f}".format(
                kind=z.kind.split("_")[0].upper(), low=z.low, high=z.high
            )

    # Priority 3: Recent swing extreme
    target_swing = _find_target_swing(
        snapshot.swings, direction, current_price, max_dist
    )
    if target_swing:
        return "Recent {kind} {price:.0f}".format(
            kind=target_swing.kind.upper(), price=target_swing.price
        )

    return None


def _find_target_key_level(levels, direction, current_price=0.0, max_dist=0.0):
    # type: (List[SmcLevel], str, float, float) -> Optional[SmcLevel]
    target_kinds_short = (
        {"pdl", "dl", "p_h4_l", "h4_l", "p_h1_l", "h1_l"}
        if direction == "short"
        else {"pdh", "dh", "p_h4_h", "h4_h", "p_h1_h", "h1_h"}
    )
    for lv in levels:
        if lv.kind in target_kinds_short:
            # P2: directional gate — target must be ahead of price
            if direction == "short" and lv.price >= current_price:
                continue
            if direction == "long" and lv.price <= current_price:
                continue
            if max_dist > 0 and abs(lv.price - current_price) > max_dist:
                continue
            return lv
    return None


def _find_target_swing(swings, direction, current_price=0.0, max_dist=0.0):
    # type: (List[SmcSwing], str, float, float) -> Optional[SmcSwing]
    target_kinds = {"ll", "lh"} if direction == "short" else {"hh", "hl"}
    for sw in reversed(swings):
        if sw.kind in target_kinds:
            # P2: directional gate — target must be ahead of price
            if direction == "short" and sw.price >= current_price:
                continue
            if direction == "long" and sw.price <= current_price:
                continue
            if max_dist > 0 and abs(sw.price - current_price) > max_dist:
                continue
            return sw
    return None


# ── FVG Context (T-5) ───────────────────────────────────────


def _build_fvg_context(snapshot, zone, atr, config):
    # type: (SmcSnapshot, Optional[SmcZone], float, dict) -> str
    if not config.get("fvg_context_enabled", True) or zone is None:
        return ""
    for z in snapshot.zones:
        if "fvg" not in z.kind or z.status == "mitigated":
            continue
        # Overlap з zone
        if z.low <= zone.high and z.high >= zone.low:
            return "OB entry refined by FVG: {:.0f}\u2013{:.0f}".format(z.low, z.high)
    # Closest FVG within 2*ATR
    if atr > 0:
        for z in snapshot.zones:
            if "fvg" not in z.kind or z.status == "mitigated":
                continue
            mid = (z.low + z.high) / 2.0
            zone_mid = (zone.low + zone.high) / 2.0
            if abs(mid - zone_mid) <= 2.0 * atr:
                return (
                    "FVG gap at {:.0f} \u2014 rebalancing expected before zone".format(
                        mid
                    )
                )
    return ""


# ── Market Phase Detection (SC-1: 3 states + BH-6 hysteresis) ──


def _detect_market_phase(swings, config):
    # type: (List[SmcSwing], dict) -> str
    if not config.get("market_phase_enabled", True):
        return "ranging"
    classified = [s for s in swings if s.kind in ("hh", "hl", "lh", "ll")]
    if len(classified) < 2:
        return "ranging"

    # P3: use phase_hysteresis_bars (BH-6) — require N consecutive same-direction swings
    hysteresis = max(2, config.get("phase_hysteresis_bars", 3))
    last_n = classified[-hysteresis:]
    if len(last_n) < hysteresis:
        return "ranging"

    kinds = {s.kind for s in last_n}
    if kinds <= {"hh", "hl"}:
        return "trending_up"
    if kinds <= {"lh", "ll"}:
        return "trending_down"
    return "ranging"


# ── Bias Summary (T-8) ─────────────────────────────────────


def _build_bias_summary(alignment, htf_direction, bias_map, zone, is_counter=False):
    # type: (str, Optional[str], Dict[str, str], Optional[SmcZone], bool) -> str
    if alignment == "no_data":
        return "Insufficient HTF data"
    d1 = bias_map.get("86400", "?")
    h4 = bias_map.get("14400", "?")
    if alignment == "mixed":
        d1_arrow = "\u2191" if d1 == "bullish" else "\u2193"
        h4_arrow = "\u2191" if h4 == "bullish" else "\u2193"
        return "D1{d1} but H4{h4} \u2014 mixed: wait or reduce size".format(
            d1=d1_arrow, h4=h4_arrow
        )
    # aligned
    if is_counter:
        return "HTF {dir} aligned \u2014 counter-trend zone, reduce size".format(
            dir=htf_direction or ""
        )
    if zone and "premium" in getattr(zone, "kind", ""):
        return "H4 pullback to premium OB \u2014 expect rejection"
    return "HTF {dir} aligned \u2014 watch for entry structure".format(
        dir=htf_direction or ""
    )


# ── Session Context (ADR-0035 §5.1) ─────────────────────────

_SESSION_LABELS = {
    "asia": "Asia",
    "london": "London",
    "newyork": "New York",
}


def _build_session_context(session_name, in_killzone):
    # type: (str, bool) -> str
    label = _SESSION_LABELS.get(
        session_name, session_name.title() if session_name else ""
    )
    if not label:
        return ""
    if in_killzone:
        return "{} KZ active \u2014 high probability".format(label)
    return "{} session active".format(label)


# ── Next Area ───────────────────────────────────────────────


def _build_next_area(snapshot, zone_grades):
    # type: (SmcSnapshot, Dict[str, dict]) -> str
    best = None  # type: Optional[SmcZone]
    best_score = -1
    for z in snapshot.zones:
        if z.status == "mitigated":
            continue
        gi = zone_grades.get(z.id, {})
        sc = gi.get("score", 0)
        if sc > best_score:
            best_score = sc
            best = z
    if best is None:
        return ""
    gi = zone_grades.get(best.id, {})
    direction = "sell" if "bear" in best.kind else "buy"
    kind_short = "OB" if "ob" in best.kind else "FVG"
    return "{low:.0f} {dir} {kind} ({grade}/{score})".format(
        low=best.low,
        dir=direction,
        kind=kind_short,
        grade=gi.get("grade", "C"),
        score=gi.get("score", 0),
    )


# ═══════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════


def synthesize_narrative(
    snapshot,  # type: SmcSnapshot
    bias_map,  # type: Dict[str, str]
    zone_grades,  # type: Dict[str, dict]
    momentum_map,  # type: Dict[str, dict]
    viewer_tf_s,  # type: int
    current_price,  # type: float
    atr,  # type: float
    config,  # type: dict
    session_info=None,  # type: Optional[Tuple[str, bool]]
):
    # type: (...) -> NarrativeBlock
    """Synthesize narrative for display (ADR-0033, ADR-0035).

    N0: pure, deterministic.
    N3: NEVER returns None — fallback block з warnings.
    session_info: (current_session_name, in_killzone) or None.
    """
    try:
        return _synthesize_impl(
            snapshot,
            bias_map,
            zone_grades,
            momentum_map,
            viewer_tf_s,
            current_price,
            atr,
            config,
            session_info,
        )
    except Exception:
        sym = getattr(snapshot, "symbol", "?")
        tf = getattr(snapshot, "tf_s", 0)
        logger.exception("NARRATIVE_ERROR symbol=%s tf=%s", sym, tf)
        return _fallback_narrative_block()


def _synthesize_impl(
    snapshot,
    bias_map,
    zone_grades,
    momentum_map,  # TODO(momentum): integrate into bias_summary/headline generation (v2)
    viewer_tf_s,
    current_price,
    atr,
    config,
    session_info=None,
):
    # type: (SmcSnapshot, Dict[str, str], Dict[str, dict], Dict[str, dict], int, float, float, dict, Optional[Tuple[str, bool]]) -> NarrativeBlock
    warnings = []  # type: List[str]
    min_grade = config.get("trade_min_grade", "A")
    min_score = config.get("trade_min_score", 6)
    max_scenarios = config.get("max_scenarios", 2)

    # Step 1: HTF alignment (SC-4, N6)
    alignment, htf_direction = _resolve_htf_alignment(bias_map)

    # Step 2: Best zones
    candidates = _select_candidate_zones(
        snapshot.zones,
        zone_grades,
        current_price,
        min_grade,
        min_score,
        warnings,
        alignment=alignment,
        htf_direction=htf_direction,
        config=config,
    )

    # Step 2b: Invalidation check (SC-7)
    valid = []  # type: List[SmcZone]
    for z in candidates:
        if _is_invalidated(z, current_price):
            warnings.append("scenario_invalidated")
        else:
            valid.append(z)

    primary_zone = valid[0] if valid else None
    alt_zone = valid[1] if len(valid) > 1 else None

    # Step 3: Mode decision (SC-3)
    #   "aligned" = HTF agrees AND zone direction matches HTF.
    #   "counter" = HTF aligned but zone opposes HTF direction.
    #   "reduced" = HTF mixed (D1 ≠ H4).
    direction: str = _zone_direction(primary_zone) if primary_zone else ""
    htf_trade_dir = (
        "long"
        if htf_direction == "bullish"
        else "short" if htf_direction == "bearish" else ""
    )
    if primary_zone and alignment == "aligned" and direction == htf_trade_dir:
        mode, sub_mode = "trade", "aligned"
    elif primary_zone and alignment == "aligned":
        # HTF aligned but zone opposes → counter-trend
        mode, sub_mode = "trade", "counter"
        warnings.append("counter_trend")
    elif primary_zone and alignment == "mixed":
        mode, sub_mode = "trade", "reduced"
    else:
        mode, sub_mode = "wait", ""

    # Build direction from primary (already computed above)

    # Headline
    if mode == "trade" and direction:
        headline = _MODE_HEADLINES.get((mode, sub_mode, direction), _WAIT_HEADLINE)
    else:
        headline = _WAIT_HEADLINE

    # Scenarios (T-1: max 2)
    scenarios = []  # type: List[ActiveScenario]
    if mode == "trade":
        for _zc in (primary_zone, alt_zone):
            if _zc is None:
                continue
            z = _zc
            d = _zone_direction(z)
            gi = zone_grades.get(z.id, {})
            tgt = _find_target(snapshot, z, d, config, current_price, atr)
            if tgt is None:
                warnings.append("no_target_found")
            scenarios.append(
                ActiveScenario(
                    zone_id=z.id,
                    direction=d,
                    entry_desc=_format_entry(z, gi),
                    trigger=_resolve_trigger_state(
                        snapshot, z, viewer_tf_s, current_price, atr, config
                    ),
                    trigger_desc=_resolve_trigger_desc(
                        snapshot, z, viewer_tf_s, current_price, atr, config
                    ),
                    target_desc=tgt,
                    invalidation=_find_invalidation(z),
                )
            )
            if len(scenarios) >= max_scenarios:
                break

    # Bias summary (T-8)
    bias_summary = _build_bias_summary(
        alignment,
        htf_direction,
        bias_map,
        primary_zone,
        is_counter=(sub_mode == "counter"),
    )

    # FVG context (T-5)
    fvg_context = _build_fvg_context(snapshot, primary_zone, atr, config)

    # Market phase (SC-1, BH-6, N5: display-only)
    market_phase = _detect_market_phase(snapshot.swings, config)

    # Next area
    next_area = _build_next_area(snapshot, zone_grades)

    # ADR-0035: session context
    current_session = ""
    in_killzone = False
    session_context = ""
    if session_info:
        current_session = session_info[0] or ""
        in_killzone = bool(session_info[1])
        session_context = _build_session_context(current_session, in_killzone)
        # Killzone downgrade: if trade mode but NOT in killzone → downgrade sub_mode
        if mode == "trade" and not in_killzone and current_session:
            if sub_mode == "aligned":
                sub_mode = "reduced"
                warnings.append("outside_killzone")
                headline = (
                    _MODE_HEADLINES.get((mode, sub_mode, direction), headline)
                    if direction
                    else headline
                )

    return NarrativeBlock(
        mode=mode,
        sub_mode=sub_mode,
        headline=headline,
        bias_summary=bias_summary,
        scenarios=scenarios,
        next_area=next_area,
        fvg_context=fvg_context,
        market_phase=market_phase,
        warnings=warnings,
        current_session=current_session,
        in_killzone=in_killzone,
        session_context=session_context,
    )


# ── Zone selection helpers ──────────────────────────────────

_GRADE_ORDER = {"A+": 0, "A": 1, "B": 2, "C": 3}


def _select_candidate_zones(
    zones,
    zone_grades,
    current_price,
    min_grade,
    min_score,
    warnings,
    alignment=None,
    htf_direction=None,
    config=None,
):
    # type: (List[SmcZone], Dict[str, dict], float, str, int, List[str], Optional[str], Optional[str], Optional[dict]) -> List[SmcZone]
    """Filter + sort zones by grade/score then proximity.

    P5B: FVG zones use fvg_trade_min_score (default 99 = display only).
    P6: HTF-aligned zones get +3 virtual bonus in sort key (ICT doctrine).
    """
    min_grade_idx = _GRADE_ORDER.get(min_grade, 1)
    fvg_min_score = (config or {}).get("fvg_trade_min_score", 99)
    candidates = []  # type: List[SmcZone]
    for z in zones:
        if z.status == "mitigated":
            continue
        gi = zone_grades.get(z.id, {})
        grade = gi.get("grade", "C")
        score = gi.get("score", 0)
        grade_idx = _GRADE_ORDER.get(grade, 3)
        effective_min_score = fvg_min_score if z.kind.startswith("fvg_") else min_score
        if grade_idx <= min_grade_idx and score >= effective_min_score:
            candidates.append(z)

    # Sort: (score + alignment bonus) DESC, proximity ASC
    _HTF_ALIGN_BONUS = 3

    def sort_key(z):
        gi = zone_grades.get(z.id, {})
        mid = (z.low + z.high) / 2.0
        bonus = 0
        if alignment == "aligned" and htf_direction:
            z_dir = _zone_direction(z)
            htf_trade = "long" if htf_direction == "bullish" else "short"
            if z_dir == htf_trade:
                bonus = _HTF_ALIGN_BONUS
        return (-(gi.get("score", 0) + bonus), abs(current_price - mid))

    candidates.sort(key=sort_key)
    return candidates


# ── Wire serialization (BH-5) ──────────────────────────────


def narrative_to_wire(block):
    # type: (NarrativeBlock) -> dict
    """Convert NarrativeBlock to wire dict. S6 compliance."""
    scenarios_wire = []  # type: List[dict]
    for s in block.scenarios:
        scenarios_wire.append(
            {
                "zone_id": s.zone_id,
                "direction": s.direction,
                "entry_desc": s.entry_desc,
                "trigger": s.trigger,
                "trigger_desc": s.trigger_desc,
                "target_desc": s.target_desc,
                "invalidation": s.invalidation,
            }
        )
    return {
        "mode": block.mode,
        "sub_mode": block.sub_mode,
        "headline": block.headline,
        "bias_summary": block.bias_summary,
        "scenarios": scenarios_wire,
        "next_area": block.next_area,
        "fvg_context": block.fvg_context,
        "market_phase": block.market_phase,
        "warnings": block.warnings,
        "current_session": block.current_session,
        "in_killzone": block.in_killzone,
        "session_context": block.session_context,
    }
