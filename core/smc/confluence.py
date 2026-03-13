"""
core/smc/confluence.py — OB Confluence Scoring (ADR-0029, ADR-0035).

Pure function, zero I/O. S0/S2/S5 compliant.

9 factors (max 13 pts):
  F1 sweep +2, F2 fvg_after +2, F3 htf_align +2,
  F4 extremum +1, F5 impulse +1, F6 pd_align +1,
  F7 structure +1, F8 tf_sig +1, F9 session_sweep +2

Grade: A+ >= 8, A >= 6, B >= 4, C < 4 (configurable).
"""

from __future__ import annotations


def _check_liquidity_sweep(zone, bars, swings, tf_s, config):
    # type: (dict, list, list, int, dict) -> int
    """F1: Swing зняте у вікні перед OB anchor (in-situ з bars). E1/E3."""
    lookback = config.get("sweep_lookback_bars", 10)
    tf_ms = tf_s * 1000
    anchor = zone.get("anchor_bar_ms", 0)
    window_start = anchor - lookback * tf_ms

    # Swing kinds: hh/lh = highs, ll/hl = lows (structure.py convention)
    _SWING_HIGHS = frozenset({"hh", "lh", "swing_high"})
    _SWING_LOWS = frozenset({"ll", "hl", "swing_low"})
    relevant = [
        s
        for s in swings
        if s.get("kind") in (_SWING_HIGHS | _SWING_LOWS)
        and window_start <= s.get("time_ms", 0) < anchor
    ]
    if not relevant:
        return 0
    is_bull = zone.get("kind", "").startswith("ob_bull")
    for sw in relevant:
        sw_price = sw.get("price", 0)
        sw_time = sw.get("time_ms", 0)
        sweep_bars = [b for b in bars if sw_time < b.get("open_time_ms", 0) <= anchor]
        if is_bull and sw.get("kind") in _SWING_LOWS:
            if any(b.get("l", 999999) < sw_price for b in sweep_bars):
                return 2
        elif not is_bull and sw.get("kind") in _SWING_HIGHS:
            if any(b.get("h", 0) > sw_price for b in sweep_bars):
                return 2
    return 0


def _check_fvg_after(zone, all_zones, tf_s, config):
    # type: (dict, list, int, dict) -> int
    """F2: FVG підтверджує OB — часове вікно АБО цінове перекриття.

    Варіант A: FVG anchor у вікні N барів після OB anchor (time adjacency).
    Варіант B: FVG ціново перекривається з OB (будь-який напрямок у часі).
    Будь-який → +2.
    """
    tf_ms = tf_s * 1000
    window = config.get("fvg_lookforward_bars", 3) * tf_ms
    anchor = zone.get("anchor_bar_ms", 0)
    ob_hi = zone.get("high", 0)
    ob_lo = zone.get("low", 0)
    for z in all_zones:
        if not z.get("kind", "").startswith("fvg"):
            continue
        fvg_anchor = z.get("anchor_bar_ms", 0)
        # A: time adjacency — FVG right after OB (classic)
        if anchor < fvg_anchor <= anchor + window:
            return 2
        # B: price overlap — OB inside FVG or FVG inside OB (any temporal order)
        fvg_hi = z.get("high", 0)
        fvg_lo = z.get("low", 0)
        if fvg_lo < ob_hi and fvg_hi > ob_lo:
            return 2
    return 0


_HTF_ALIVE_STATUSES = frozenset(("active", "tested", "partially_filled"))


def _check_htf_alignment(zone, htf_zones):
    # type: (dict, list) -> int
    """F3: OB mid-price всередині alive HTF zone (ER-10)."""
    mid = (zone["high"] + zone["low"]) / 2.0
    return (
        2
        if any(
            hz
            for hz in htf_zones
            if hz.get("low", 0) <= mid <= hz.get("high", 0)
            and hz.get("status") in _HTF_ALIVE_STATUSES
        )
        else 0
    )


def _check_extremum(zone, swings, atr, tf_s, config):
    # type: (dict, list, float, int, dict) -> int
    """F4: OB anchor = swing point ± tolerance. E3."""
    tol = config.get("extremum_tolerance_atr", 0.3) * atr
    tf_ms = tf_s * 1000
    anchor = zone.get("anchor_bar_ms", 0)
    is_bull = zone.get("kind", "").startswith("ob_bull")
    _SWING_HIGHS = frozenset({"hh", "lh", "swing_high"})
    _SWING_LOWS = frozenset({"ll", "hl", "swing_low"})
    for s in swings:
        if abs(s.get("time_ms", 0) - anchor) < tf_ms * 3:
            if is_bull and s.get("kind") in _SWING_LOWS:
                if abs(s["price"] - zone["low"]) < tol:
                    return 1
            elif not is_bull and s.get("kind") in _SWING_HIGHS:
                if abs(s["price"] - zone["high"]) < tol:
                    return 1
    return 0


def _check_impulse(zone, config):
    # type: (dict, dict) -> int
    """F5: zone.strength >= threshold."""
    return (
        1
        if zone.get("strength", 0) >= config.get("strong_impulse_threshold", 0.7)
        else 0
    )


def _check_premium_discount(zone, current_price, swings):
    # type: (dict, float, list) -> int
    """F6: Bullish OB in discount / Bearish OB in premium. E3."""
    _SWING_HIGHS = frozenset({"hh", "lh", "swing_high"})
    _SWING_LOWS = frozenset({"ll", "hl", "swing_low"})
    highs = [s["price"] for s in swings if s.get("kind") in _SWING_HIGHS][-5:]
    lows = [s["price"] for s in swings if s.get("kind") in _SWING_LOWS][-5:]
    if not highs or not lows:
        return 0
    mid = (max(highs) + min(lows)) / 2.0
    kind = zone.get("kind", "")
    if kind.startswith("ob_bull") and current_price < mid:
        return 1
    if kind.startswith("ob_bear") and current_price > mid:
        return 1
    return 0


def _check_structure(zone, structure):
    # type: (dict, list) -> int
    """F7: BOS/CHoCH у напрямку OB після anchor. E2."""
    is_bull_ob = zone.get("kind", "").startswith("ob_bull")
    anchor = zone.get("anchor_bar_ms", 0)
    for s in structure:
        if s.get("time_ms", 0) > anchor:
            s_bull = s.get("kind", "").endswith("_bull")
            if (is_bull_ob and s_bull) or (not is_bull_ob and not s_bull):
                return 1
    return 0


def _check_tf_significance(tf_s):
    # type: (int) -> int
    """F8: tf_s >= 14400 (H4+)."""
    return 1 if tf_s >= 14400 else 0


def _check_session_sweep(zone, levels, config):
    # type: (dict, list, dict) -> int
    """F9: OB знаходиться біля session H/L що було swept (ADR-0035 §4.1).

    Tri-state: 0 = no session sweep, 1 = nearby session level, 2 = wick sweep.
    Session levels: as_h/l, lon_h/l, ny_h/l + prev variants.
    """
    _SESSION_KINDS = frozenset(
        {
            "as_h",
            "as_l",
            "p_as_h",
            "p_as_l",
            "lon_h",
            "lon_l",
            "p_lon_h",
            "p_lon_l",
            "ny_h",
            "ny_l",
            "p_ny_h",
            "p_ny_l",
        }
    )
    sweep_body_ok = config.get("sweep_body_ok", False)
    if not levels:
        return 0

    zone_high = zone.get("high", 0)
    zone_low = zone.get("low", 0)
    if zone_high <= 0:
        return 0
    zone_range = zone_high - zone_low if zone_high > zone_low else 1.0
    # Proximity threshold: 50% of zone range
    proximity = zone_range * 0.5

    is_bull = zone.get("kind", "").startswith("ob_bull")

    for lv in levels:
        kind = lv.get("kind", "")
        if kind not in _SESSION_KINDS:
            continue
        price = lv.get("price", 0)
        if price <= 0:
            continue

        # Bull OB near session low, Bear OB near session high
        is_low_level = kind.endswith("_l")
        if is_bull and is_low_level:
            if abs(zone_low - price) <= proximity:
                return 2 if not sweep_body_ok else 1
        elif not is_bull and not is_low_level:
            if abs(zone_high - price) <= proximity:
                return 2 if not sweep_body_ok else 1

    return 0


def _score_to_grade(score, config):
    # type: (int, dict) -> str
    gt = config.get("grade_thresholds", {})
    if score >= gt.get("a_plus", 8):
        return "A+"
    if score >= gt.get("a", 6):
        return "A"
    if score >= gt.get("b", 4):
        return "B"
    return "C"


def score_zone_confluence(
    zone,
    bars,
    swings,
    zones_all,
    htf_zones,
    structure,
    atr,
    current_price,
    tf_s,
    config,
    session_levels=None,
):
    # type: (dict, list, list, list, list, list, float, float, int, dict, object) -> dict
    """Score OB zone confluence. Returns {'score': int, 'grade': str, 'factors': list}.

    Non-OB zones → {'score': 0, 'grade': 'C', 'factors': []}.
    session_levels: list of session level dicts (kind, price) for F9.
    """
    if not zone.get("kind", "").startswith("ob_"):
        return {"score": 0, "grade": "C", "factors": []}

    checks = [
        ("sweep", _check_liquidity_sweep, (zone, bars, swings, tf_s, config)),
        ("fvg_after", _check_fvg_after, (zone, zones_all, tf_s, config)),
        ("htf_align", _check_htf_alignment, (zone, htf_zones)),
        ("extremum", _check_extremum, (zone, swings, atr, tf_s, config)),
        ("impulse", _check_impulse, (zone, config)),
        ("pd_align", _check_premium_discount, (zone, current_price, swings)),
        ("structure", _check_structure, (zone, structure)),
        ("tf_sig", _check_tf_significance, (tf_s,)),
        ("session_sweep", _check_session_sweep, (zone, session_levels or [], config)),
    ]
    factors = []
    score = 0
    for name, fn, args in checks:
        pts = fn(*args)  # type: ignore[operator]
        if pts > 0:
            factors.append("{} +{}".format(name, pts))
            score += pts
    return {"score": score, "grade": _score_to_grade(score, config), "factors": factors}


def score_fvg_strength(fvg_zone, atr, partial_fill_pct=0.0):
    # type: (dict, float, float) -> float
    """FVG strength = f(gap_size/ATR, partial_fill). P-Φ1-1b."""
    gap = fvg_zone.get("high", 0) - fvg_zone.get("low", 0)
    ratio = gap / atr if atr > 0 else 0.0
    if ratio > 1.5:
        base = 0.9
    elif ratio > 0.8:
        base = 0.6
    elif ratio > 0.3:
        base = 0.3
    else:
        base = 0.1
    if partial_fill_pct > 0.8:
        base *= 0.3
    elif partial_fill_pct > 0.5:
        base *= 0.5
    elif partial_fill_pct > 0.0:
        base *= 0.7
    return min(1.0, base)
