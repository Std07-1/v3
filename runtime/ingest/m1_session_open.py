"""Перша M1 після перерви: відкриття з тікової історії брокера замість «запеченого» (ADR-0096 слайс E).

FXCM віддає першу хвилину кожної сесії (денна перерва, вихідні) з open — і high або low — рівним close перед
перервою. Полер комітить бар один раз, тож без перебудови запечений open лишається назавжди і тягне M1…D1
(гігантська перша свічка сесії). Тікова історія брокера для тієї ж хвилини містить лише справжні тіки:
бар = (перший bid, max, min, останній bid), а close і обсяг мусять збігтися з брокерськими — це доказ, що
тіки описують ту саму хвилину повністю.

Модуль чистий (без I/O і логів) і сумісний з Python 3.7. Рішення і гейти — тут; запит тіків і лог — у полері.
"""
from __future__ import annotations

import dataclasses
from typing import Any, Callable, Dict, Mapping, Optional, Sequence, Tuple

from core.model.bars import CandleBar, normalize_ohlc

_M1_MS = 60 * 1000
_CONFIG_SECTION = "session_open_rebuild"  # config.json → m1_poller.session_open_rebuild

MARKER_REBUILT = "session_open_rebuilt"
MARKER_OPEN_BEFORE = "open_before"
MARKER_PROVISIONAL = "open_provisional"

REASON_REBUILT = "rebuilt"
REASON_PRICE_STEP_INVALID = "price_step_invalid"
REASON_NO_TICKS = "no_ticks_in_minute"
REASON_TICKS_EXCEED_VOLUME = "ticks_exceed_volume"
REASON_VOLUME_DEFICIT = "volume_deficit"
REASON_CLOSE_MISMATCH = "close_mismatch"
REASON_RANGE_OUTSIDE_BAR = "tick_range_outside_bar"


@dataclasses.dataclass(frozen=True)
class SessionOpenRebuildPolicy:
    """Політика перебудови; SSOT — config.json `m1_poller.session_open_rebuild`.

    gap_ms — попередній закомічений M1 старший за це → бар «перший після перерви» (не залежить від DST і
    календаря). max_volume_deficit — скільки одиниць v брокер може мати понад тікову історію (запечений
    «тік» теж рахується в v: EUSTX50 21.09 06:01 — 10 тіків при v=10). price_step_by_symbol — крок котирування
    FXCM (10^-digits): допуск порівняння цін = ½ кроку.
    """

    enabled: bool
    gap_ms: int
    max_volume_deficit: int
    price_step_by_symbol: Mapping[str, float]


DISABLED_POLICY = SessionOpenRebuildPolicy(enabled=False, gap_ms=0, max_volume_deficit=0, price_step_by_symbol={})


def resolve_session_open_rebuild_policy(cfg: Dict[str, Any]) -> SessionOpenRebuildPolicy:
    """Політика з config; секції немає → вимкнено. Битий ключ → ValueError (записувач кричить і вимикає)."""
    m1_cfg = cfg.get("m1_poller")
    section = m1_cfg.get(_CONFIG_SECTION) if isinstance(m1_cfg, dict) else None
    if not isinstance(section, dict):
        return DISABLED_POLICY
    gap_min = int(section["gap_min"])
    max_volume_deficit = int(section["max_volume_deficit"])
    steps = section["price_step_by_symbol"]
    if gap_min < 1 or max_volume_deficit < 0 or not isinstance(steps, dict):
        raise ValueError("m1_poller.%s: gap_min≥1, max_volume_deficit≥0, price_step_by_symbol={...}" % _CONFIG_SECTION)
    price_steps = {str(sym): float(step) for sym, step in steps.items()}
    bad = sorted(sym for sym, step in price_steps.items() if not step > 0)
    if bad:
        raise ValueError("m1_poller.%s.price_step_by_symbol: крок має бути > 0: %s" % (_CONFIG_SECTION, bad))
    return SessionOpenRebuildPolicy(
        enabled=bool(section.get("enabled", False)),
        gap_ms=gap_min * _M1_MS,
        max_volume_deficit=max_volume_deficit,
        price_step_by_symbol=price_steps,
    )


def is_first_bar_after_break(
    open_ms: int,
    prev_committed_open_ms: Optional[int],
    gap_ms: int,
    is_trading_fn: Optional[Callable[[int], bool]] = None,
) -> bool:
    """Бар — перший після перерви: попередній закомічений M1 старший за gap_ms, або за календарем хвилина
    торгова, а попередня — ні. Два критерії, бо брокер може не віддати саму хвилину відкриття (EUSTX50 21.09:
    перший бар 06:01, не 06:00), а календар може зсунутись на годину (DST); обидва — дешеві й локальні."""
    if prev_committed_open_ms is not None and open_ms - prev_committed_open_ms > gap_ms:
        return True
    if is_trading_fn is None:
        return False
    return is_trading_fn(open_ms) and not is_trading_fn(open_ms - _M1_MS)


def rebuild_session_open_bar(
    bar: CandleBar,
    ticks: Sequence[Tuple[int, float]],
    price_step: float,
    *,
    max_volume_deficit: int,
) -> Tuple[Optional[CandleBar], str]:
    """(перебудований бар, REASON_REBUILT) або (None, причина). Вхідний бар не змінюється.

    Гейти — кожен доводить, що тіки описують ту саму хвилину, що й бар брокера:
    1. тіки лише з [open_ms, close_ms) — чужі хвилини відкидаються, а не зсувають OHLC; ≥1 тік;
    2. v_ticks ≤ v_bar і v_bar − v_ticks ≤ max_volume_deficit;
    3. |останній bid − c_bar| ≤ ½ кроку ціни;
    4. діапазон тіків ⊆ діапазону бару брокера (± ½ кроку): перебудова лише прибирає запечене значення.
    Замінюються тільки o/h/low; c і v лишаються брокерськими — гейти довели їх рівність (як ADR-0096 §3.3 B).
    """
    if not price_step > 0:
        return None, REASON_PRICE_STEP_INVALID
    minute_ticks = sorted(
        (tick for tick in ticks if bar.open_time_ms <= tick[0] < bar.close_time_ms), key=lambda tick: tick[0]
    )
    if not minute_ticks:
        return None, REASON_NO_TICKS
    tick_count = len(minute_ticks)
    if tick_count > bar.v:
        return None, REASON_TICKS_EXCEED_VOLUME
    if bar.v - tick_count > max_volume_deficit:
        return None, REASON_VOLUME_DEFICIT
    half_step = price_step / 2.0
    bids = [bid for _tick_ms, bid in minute_ticks]
    if abs(bids[-1] - bar.c) > half_step:
        return None, REASON_CLOSE_MISMATCH
    if max(bids) > bar.h + half_step or min(bids) < bar.low - half_step:
        return None, REASON_RANGE_OUTSIDE_BAR
    o, h, low, c = normalize_ohlc(bids[0], max(bids), min(bids), bar.c)
    extensions = {**bar.extensions, MARKER_REBUILT: True, MARKER_OPEN_BEFORE: bar.o}
    return dataclasses.replace(bar, o=o, h=h, low=low, c=c, extensions=extensions), REASON_REBUILT


def mark_open_provisional(bar: CandleBar) -> CandleBar:
    """Open не доведено тіками: бар брокера без змін, з маркером для аудиту і подальшого ремонту."""
    return dataclasses.replace(bar, extensions={**bar.extensions, MARKER_PROVISIONAL: True})
