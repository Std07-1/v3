"""
core/smc/premium_discount.py — Premium/Discount Zones (ADR-0024 §4.6).

Концепт: Ділимо діапазон від останнього confirmed Swing High до Swing Low.
  Premium (upper 50%): price > equilibrium → sell setups мають перевагу.
  Discount (lower 50%): price < equilibrium → buy setups мають перевагу.
  Equilibrium (50%): "справедлива ціна".

ADR-0041: calc/display decoupled.
  compute_pd_state() — завжди обчислює позицію ціни у P/D range.
  detect_premium_discount() — будує zone objects для UI (controlled by show_zones).

S0: pure logic, NO I/O.
S2: deterministic — same swings → same zones.
S5: параметри з SmcConfig (config.json:smc.premium_discount).

Python 3.7 compatible.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

from core.model.bars import CandleBar
from core.smc.config import SmcConfig
from core.smc.types import PdState, SmcSwing, SmcZone, make_zone_id

# Kinds swing-high / swing-low після classify_swings()
_HIGH_SWING_KINDS = frozenset({"hh", "lh"})
_LOW_SWING_KINDS = frozenset({"ll", "hl"})


def _find_swing_range(
    classified: List[SmcSwing],
) -> Tuple[Optional[SmcSwing], Optional[SmcSwing]]:
    """Знайти останній confirmed SH та SL з classified swings."""
    last_high: Optional[SmcSwing] = None
    last_low: Optional[SmcSwing] = None
    for s in reversed(classified):
        if not s.confirmed:
            continue
        if last_high is None and s.kind in _HIGH_SWING_KINDS:
            last_high = s
        if last_low is None and s.kind in _LOW_SWING_KINDS:
            last_low = s
        if last_high is not None and last_low is not None:
            break
    return last_high, last_low


def compute_pd_state(
    classified: List[SmcSwing],
    current_price: float,
    config: SmcConfig,
) -> Optional[PdState]:
    """Обчислити поточну P/D позицію ціни (ADR-0041).

    Завжди працює коли calc_enabled=True, незалежно від display settings.
    Використовується narrative, confluence, badge.

    Args:
        classified: підтверджені swings (hh/hl/lh/ll) з structure.py.
        current_price: поточна ціна (last bar close).
        config: SmcConfig.

    Returns:
        PdState або None якщо calc disabled або недостатньо swings.
    """
    if not config.premium_discount.calc_enabled:
        return None
    if not classified:
        return None

    last_high, last_low = _find_swing_range(classified)
    if last_high is None or last_low is None:
        return None
    if last_high.price <= last_low.price:
        return None

    range_high = last_high.price
    range_low = last_low.price
    equilibrium = (range_high + range_low) / 2.0
    span = range_high - range_low

    # percent: 0 = at range_low, 100 = at range_high
    raw_pct = ((current_price - range_low) / span) * 100.0
    pd_percent = max(0.0, min(100.0, raw_pct))

    eq_low = config.premium_discount.eq_low
    eq_high = config.premium_discount.eq_high
    if pd_percent < eq_low:
        label = "DISCOUNT"
    elif pd_percent > eq_high:
        label = "PREMIUM"
    else:
        label = "EQ"

    return PdState(
        range_high=range_high,
        range_low=range_low,
        equilibrium=equilibrium,
        pd_percent=pd_percent,
        label=label,
        current_price=current_price,
    )


def detect_premium_discount(
    classified: List[SmcSwing],
    bars: List[CandleBar],
    config: SmcConfig,
) -> List[SmcZone]:
    """Будує пару Premium/Discount зон з поточного swing range.

    ADR-0041: guard uses calc_enabled (NOT old enabled).
    Zones are always computed when calc_enabled=True.
    UI rendering controlled separately by show_zones (OverlayRenderer).

    Алгоритм:
      1. Знайти останній confirmed Swing High (hh/lh) — range_high.
      2. Знайти останній confirmed Swing Low  (ll/hl) — range_low.
      3. Equilibrium = (range_high + range_low) / 2.
      4. Premium zone: high=range_high, low=equilibrium.
      5. Discount zone: high=equilibrium, low=range_low.

    S3: Zone ID = make_zone_id("premium"|"discount", symbol, tf_s, anchor_ms)
        де anchor_ms = max(swing_high.time_ms, swing_low.time_ms).

    Args:
        classified: підтверджені swings (hh/hl/lh/ll) з structure.py.
        bars: OHLCV bars (використовуємо symbol/tf_s останнього бару).
        config: SmcConfig.

    Returns:
        [] або [premium_zone, discount_zone] — завжди парно або порожньо.
    """
    if not config.premium_discount.calc_enabled:
        return []
    if not bars or not classified:
        return []

    last_high, last_low = _find_swing_range(classified)
    if last_high is None or last_low is None:
        return []

    # Rail: range має бути валідним (SH > SL)
    if last_high.price <= last_low.price:
        return []

    symbol = bars[-1].symbol
    tf_s = bars[-1].tf_s

    range_high = last_high.price
    range_low = last_low.price
    equilibrium = (range_high + range_low) / 2.0
    anchor_ms = max(last_high.time_ms, last_low.time_ms)
    start_ms = min(last_high.time_ms, last_low.time_ms)

    premium = SmcZone(
        id=make_zone_id("premium", symbol, tf_s, anchor_ms),
        symbol=symbol,
        tf_s=tf_s,
        kind="premium",
        start_ms=start_ms,
        end_ms=None,
        high=range_high,
        low=equilibrium,
        status="active",
        strength=1.0,
        anchor_bar_ms=anchor_ms,
    )
    discount = SmcZone(
        id=make_zone_id("discount", symbol, tf_s, anchor_ms),
        symbol=symbol,
        tf_s=tf_s,
        kind="discount",
        start_ms=start_ms,
        end_ms=None,
        high=equilibrium,
        low=range_low,
        status="active",
        strength=1.0,
        anchor_bar_ms=anchor_ms,
    )
    return [premium, discount]
