"""Перетин рівня лінії-сповіщення (ADR-0114 §4.2): чисті переходи стану `PriceAlert`.

Pure: без I/O і без годинника — час приходить аргументом. Вхід — ціна графіка (bid з тіку) або фінальна свічка M1;
вихід — новий `PriceAlert` (той самий об'єкт, якщо нічого не змінилось). Правила:

- бік — з останньої відомої ціни; ціна рівно на рівні — бік невизначений, поки ціна від нього не відійде;
- до `active_from_ms` (промінь з якорем у майбутньому) тіки лише оновлюють бік — перетин рахується від активації;
- спрацювання — ціна дійшла до рівня з боку очікування (дотик рахується); лише один раз, далі `fired` не змінюється;
- свічка M1, відкрита не раніше активації, спрацьовує, коли її high (бік «нижче») або low (бік «вище») дійшов до рівня:
  так ловимо сплеск між проріджених тіків, простій процесу і геп між свічками.
"""

from __future__ import annotations

import dataclasses
from typing import Optional

from core.alerts.types import (
    AlertFire,
    AlertSide,
    AlertState,
    CrossDirection,
    FireVia,
    PriceAlert,
    validate_epoch_ms,
    validate_price,
)


def side_of(level: float, price: float) -> Optional[AlertSide]:
    """Бік ціни відносно рівня; None — ціна рівно на рівні."""
    if price < level:
        return AlertSide.BELOW
    if price > level:
        return AlertSide.ABOVE
    return None


def arm(alert: PriceAlert, *, level: float, reference_price: float, active_from_ms: int) -> PriceAlert:
    """Взвести знову: новий рівень (лінію пересунули) або повтор після спрацювання. Нова версія, факт знято."""
    level = validate_price("level", level)
    reference_price = validate_price("reference_price", reference_price)
    return dataclasses.replace(
        alert,
        level=level,
        active_from_ms=validate_epoch_ms("active_from_ms", active_from_ms),
        version=alert.version + 1,
        state=AlertState.ARMED,
        side=side_of(level, reference_price),
        fired=None,
    )


def on_tick(
    alert: PriceAlert,
    price: float,
    tick_ts_ms: int,
    *,
    prev_tick_ts_ms: Optional[int],
    gap_after_ms: int,
) -> PriceAlert:
    """Тік ціни графіка. `prev_tick_ts_ms` — попередній тік символу (None — перший після старту процесу);
    пауза ≥ `gap_after_ms` (config) позначає спрацювання як «після гепу»."""
    price = validate_price("price", price)
    validate_epoch_ms("tick_ts_ms", tick_ts_ms)
    if gap_after_ms <= 0:
        raise ValueError(f"gap_after_ms: очікується > 0, отримано {gap_after_ms!r}")
    if alert.state is AlertState.FIRED:
        return alert
    current = side_of(alert.level, price)
    if tick_ts_ms < alert.active_from_ms or alert.side is None:
        if current is None or current is alert.side:
            return alert
        return dataclasses.replace(alert, side=current)
    direction = _reached(alert.side, high=price, low=price, level=alert.level)
    if direction is None:
        return alert
    after_gap = prev_tick_ts_ms is not None and tick_ts_ms - prev_tick_ts_ms >= gap_after_ms
    return _fire(alert, AlertFire(tick_ts_ms, price, FireVia.TICK, direction, after_gap))


def on_m1_bar(alert: PriceAlert, *, open_ms: int, low: float, high: float) -> PriceAlert:
    """Фінальна свічка M1 (наздоганяння). Ціна факту — екстремум, що дійшов до рівня; час — відкриття свічки."""
    validate_epoch_ms("open_ms", open_ms)
    low = validate_price("low", low)
    high = validate_price("high", high)
    if low > high:
        raise ValueError(f"low {low} > high {high}")
    if alert.state is AlertState.FIRED or alert.side is None or open_ms < alert.active_from_ms:
        return alert
    direction = _reached(alert.side, high=high, low=low, level=alert.level)
    if direction is None:
        return alert
    extreme = high if direction is CrossDirection.UP else low
    return _fire(alert, AlertFire(open_ms, extreme, FireVia.M1, direction))


def _reached(side: AlertSide, *, high: float, low: float, level: float) -> Optional[CrossDirection]:
    if side is AlertSide.BELOW and high >= level:
        return CrossDirection.UP
    if side is AlertSide.ABOVE and low <= level:
        return CrossDirection.DOWN
    return None


def _fire(alert: PriceAlert, fire: AlertFire) -> PriceAlert:
    return dataclasses.replace(alert, state=AlertState.FIRED, fired=fire)
