"""Лінія-сповіщення (ADR-0114): `PriceAlert` ↔ dict для файлу стану й HTTP.

Pure. Суворо за D3: бракує поля, зайве поле чи невідоме значення enum → `ValueError` з назвою поля, а не тихий
ігнор. Ключі dict — імена полів dataclass (одне джерело назв).
"""

from __future__ import annotations

import dataclasses
from typing import Any, Dict, Mapping, Optional

from core.alerts.types import AlertFire, AlertSide, AlertState, CrossDirection, FireVia, PriceAlert

_ALERT_FIELDS = frozenset(f.name for f in dataclasses.fields(PriceAlert))
_FIRE_FIELDS = frozenset(f.name for f in dataclasses.fields(AlertFire))


def alert_to_dict(alert: PriceAlert) -> Dict[str, Any]:
    out = dataclasses.asdict(alert)
    out["state"] = alert.state.value
    out["side"] = alert.side.value if alert.side is not None else None
    if alert.fired is not None:
        out["fired"] = {**out["fired"], "via": alert.fired.via.value, "direction": alert.fired.direction.value}
    return out


def alert_from_dict(raw: Mapping[str, Any]) -> PriceAlert:
    _require_exact_keys("alert", raw, _ALERT_FIELDS)
    side_raw = raw["side"]
    return PriceAlert(
        **{name: raw[name] for name in _ALERT_FIELDS - {"state", "side", "fired"}},
        state=_enum(AlertState, "state", raw["state"]),
        side=_enum(AlertSide, "side", side_raw) if side_raw is not None else None,
        fired=_fire_from_dict(raw["fired"]),
    )


def _fire_from_dict(raw: Optional[Mapping[str, Any]]) -> Optional[AlertFire]:
    if raw is None:
        return None
    _require_exact_keys("fired", raw, _FIRE_FIELDS)
    if not isinstance(raw["after_gap"], bool):
        raise ValueError(f"fired.after_gap: очікується bool, отримано {raw['after_gap']!r}")
    return AlertFire(
        at_ms=raw["at_ms"],
        price=raw["price"],
        via=_enum(FireVia, "fired.via", raw["via"]),
        direction=_enum(CrossDirection, "fired.direction", raw["direction"]),
        after_gap=raw["after_gap"],
    )


def _require_exact_keys(what: str, raw: Any, expected: frozenset) -> None:
    if not isinstance(raw, Mapping):
        raise ValueError(f"{what}: очікується об'єкт, отримано {type(raw).__name__}")
    missing, extra = expected - raw.keys(), raw.keys() - expected
    if missing or extra:
        raise ValueError(f"{what}: бракує {sorted(missing)}, зайві {sorted(extra)}")


def _enum(enum_cls: Any, name: str, value: Any) -> Any:
    try:
        return enum_cls(value)
    except ValueError:
        raise ValueError(f"{name}: невідоме значення {value!r}") from None
