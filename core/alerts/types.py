"""Лінія-сповіщення (ADR-0114): типи.

Pure: без I/O. Сповіщення належить фігурі трейдера (`drawing_id` з localStorage браузера) і підписнику; стан —
взведене або спрацьоване, один раз (ADR-0114 §2 п.5). Узгодженість полів перевіряється при створенні: неможливий
стан (спрацьоване без факту спрацювання, рівень NaN, порожній символ) не існує навіть у пам'яті.
"""

from __future__ import annotations

import dataclasses
import enum
import math
from typing import Optional


class AlertSide(str, enum.Enum):
    """Бік ціни відносно рівня, з якого чекаємо перетину."""

    BELOW = "below"
    ABOVE = "above"


class AlertState(str, enum.Enum):
    ARMED = "armed"
    FIRED = "fired"


class CrossDirection(str, enum.Enum):
    UP = "up"  # знизу вгору
    DOWN = "down"  # згори вниз


class FireVia(str, enum.Enum):
    """Чим доведено перетин."""

    TICK = "tick"  # тік ціни графіка (bid)
    M1 = "m1"  # фінальна свічка M1 дійшла до рівня, а тіка не було (проріджений релей, простій процесу)


def validate_price(name: str, value: float) -> float:
    """Ціна — скінченне додатне число; інакше ValueError з назвою поля."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name}: очікується число, отримано {type(value).__name__}")
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name}: ціна має бути скінченною і > 0, отримано {value!r}")
    return float(value)


def validate_epoch_ms(name: str, value: int) -> int:
    """Час — ціле epoch ms ≥ 0 (D3: лише epoch ms int)."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name}: очікується epoch ms int ≥ 0, отримано {value!r}")
    return value


@dataclasses.dataclass(frozen=True)
class AlertFire:
    """Факт спрацювання: коли, за якою ціною, чим доведено, у який бік."""

    at_ms: int
    price: float
    via: FireVia
    direction: CrossDirection
    after_gap: bool = False  # перша ціна після паузи ринку вже по той бік рівня

    def __post_init__(self) -> None:
        validate_epoch_ms("at_ms", self.at_ms)
        validate_price("price", self.price)


@dataclasses.dataclass(frozen=True)
class PriceAlert:
    """Сповіщення на рівні лінії.

    side=None — ціна при взведенні стояла рівно на рівні; бік визначить перша ціна, що від нього відійде.
    active_from_ms — з якого моменту перетин рахується (горизонталь — взведення, промінь — час якоря).
    """

    alert_id: str
    drawing_id: str
    subscriber_id: str
    symbol: str
    level: float
    tf_label: str
    active_from_ms: int
    created_at_ms: int
    version: int = 1
    state: AlertState = AlertState.ARMED
    side: Optional[AlertSide] = None
    fired: Optional[AlertFire] = None

    def __post_init__(self) -> None:
        for name in ("alert_id", "drawing_id", "subscriber_id", "symbol", "tf_label"):
            if not isinstance(getattr(self, name), str) or not getattr(self, name):
                raise ValueError(f"{name}: порожній або не рядок")
        validate_price("level", self.level)
        validate_epoch_ms("active_from_ms", self.active_from_ms)
        validate_epoch_ms("created_at_ms", self.created_at_ms)
        if isinstance(self.version, bool) or not isinstance(self.version, int) or self.version < 1:
            raise ValueError(f"version: очікується int ≥ 1, отримано {self.version!r}")
        if (self.state is AlertState.FIRED) != (self.fired is not None):
            raise ValueError(f"state={self.state.value} не узгоджено з fired={self.fired!r}")
