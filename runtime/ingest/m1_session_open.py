"""Перша M1 після перерви: запечені компоненти бару — з тікової історії брокера (ADR-0096 слайс E).

FXCM віддає першу хвилину кожної сесії (денна перерва, вихідні) з open — і high або low — рівним close перед
перервою. Полер комітить бар один раз, тож без перебудови запечене значення лишається назавжди і тягне M1…D1
(гігантська перша свічка сесії).

Перебудовується ЛИШЕ запечене: open — першим тіком хвилини; high/low — лише той, що дорівнює close перед
перервою, з тіків і close. Решта бару (c, v, незапечений екстремум) — брокерська. Звірки «тіки = бар» за
close чи обсягом немає свідомо: на T+8 с тікова історія й m1 брокера не узгоджені ні за кількістю тіків, ні
за close (замір 21.09 15:34–15:36 UTC: XAU 1190 тіків при v=818, EUSTX50 close тіку 6327.63 проти 6328.13) —
такі гейти відкидали б майже кожне відкриття.

Модуль чистий (без I/O і логів) і сумісний з Python 3.7. Рішення — тут; запит тіків і лог — у полері.
"""
from __future__ import annotations

import dataclasses
from typing import Any, Callable, Dict, FrozenSet, Mapping, Optional, Sequence, Tuple

from core.model.bars import CandleBar, normalize_ohlc

_M1_MS = 60 * 1000
_CONFIG_SECTION = "session_open_rebuild"  # config.json → m1_poller.session_open_rebuild

MARKER_REBUILT = "session_open_rebuilt"
MARKER_OPEN_BEFORE = "open_before"
MARKER_HIGH_BEFORE = "high_before"
MARKER_LOW_BEFORE = "low_before"
MARKER_PROVISIONAL = "open_provisional"

REASON_REBUILT = "rebuilt"
REASON_NOT_BAKED = "open_not_baked"
REASON_OPEN_EQUALS_PREV_CLOSE = "first_tick_equals_prev_close"
REASON_NO_TICKS = "no_ticks_in_minute"
REASON_PRICE_STEP_INVALID = "price_step_invalid"

# Причини, з якими бар брокера вже правильний: лишається як є, без маркера і без WARN.
BAR_CORRECT_AS_IS: FrozenSet[str] = frozenset({REASON_NOT_BAKED, REASON_OPEN_EQUALS_PREV_CLOSE})


@dataclasses.dataclass(frozen=True)
class SessionOpenRebuildPolicy:
    """Політика перебудови; SSOT — config.json `m1_poller.session_open_rebuild`.

    gap_ms — попередній закомічений M1 старший за це → бар «перший після перерви» (не залежить від DST і
    календаря). price_step_by_symbol — крок котирування FXCM (10^-digits): ціни рівні, якщо різниця ≤ ½ кроку
    (float-шум брокера на кшталт 30287.710000000003).
    """

    enabled: bool
    gap_ms: int
    price_step_by_symbol: Mapping[str, float]


DISABLED_POLICY = SessionOpenRebuildPolicy(enabled=False, gap_ms=0, price_step_by_symbol={})


def resolve_session_open_rebuild_policy(cfg: Dict[str, Any]) -> SessionOpenRebuildPolicy:
    """Політика з config; секції немає → вимкнено. Битий ключ → ValueError (записувач кричить і вимикає)."""
    m1_cfg = cfg.get("m1_poller")
    section = m1_cfg.get(_CONFIG_SECTION) if isinstance(m1_cfg, dict) else None
    if not isinstance(section, dict):
        return DISABLED_POLICY
    gap_min = int(section["gap_min"])
    steps = section["price_step_by_symbol"]
    if gap_min < 1 or not isinstance(steps, dict):
        raise ValueError("m1_poller.%s: gap_min≥1, price_step_by_symbol={...}" % _CONFIG_SECTION)
    price_steps = {str(sym): float(step) for sym, step in steps.items()}
    bad = sorted(sym for sym, step in price_steps.items() if not step > 0)
    if bad:
        raise ValueError("m1_poller.%s.price_step_by_symbol: крок має бути > 0: %s" % (_CONFIG_SECTION, bad))
    return SessionOpenRebuildPolicy(
        enabled=bool(section.get("enabled", False)),
        gap_ms=gap_min * _M1_MS,
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


def is_open_baked(bar: CandleBar, prev_close: float, price_step: float) -> bool:
    """Open першої хвилини = close перед перервою (±½ кроку) — ознака запеченого бару брокера."""
    return _same_price(bar.o, prev_close, price_step)


def rebuild_session_open_bar(
    bar: CandleBar,
    ticks: Sequence[Tuple[int, float]],
    price_step: float,
    prev_close: float,
) -> Tuple[Optional[CandleBar], str]:
    """(перебудований бар, REASON_REBUILT) або (None, причина). Вхідний бар не змінюється.

    Не запечений (o ≠ prev_close) → REASON_NOT_BAKED. Запечений: o = перший тік хвилини [open_ms, close_ms);
    high/low замінюється лише той, що дорівнює prev_close (запечений екстремум), на max/min(тіки, c); c і v —
    брокерські. Перший тік = prev_close → справжнє відкриття на тій самій ціні (REASON_OPEN_EQUALS_PREV_CLOSE).
    """
    if not price_step > 0:
        return None, REASON_PRICE_STEP_INVALID
    if not is_open_baked(bar, prev_close, price_step):
        return None, REASON_NOT_BAKED
    minute_bids = [bid for _tick_ms, bid in sorted(
        (tick for tick in ticks if bar.open_time_ms <= tick[0] < bar.close_time_ms), key=lambda tick: tick[0]
    )]
    if not minute_bids:
        return None, REASON_NO_TICKS
    new_open = minute_bids[0]
    if _same_price(new_open, prev_close, price_step):
        return None, REASON_OPEN_EQUALS_PREV_CLOSE
    new_high = max(max(minute_bids), bar.c) if _same_price(bar.h, prev_close, price_step) else bar.h
    new_low = min(min(minute_bids), bar.c) if _same_price(bar.low, prev_close, price_step) else bar.low
    o, h, low, c = normalize_ohlc(new_open, new_high, new_low, bar.c)
    extensions = {**bar.extensions, MARKER_REBUILT: True, MARKER_OPEN_BEFORE: bar.o}
    if h != bar.h:
        extensions[MARKER_HIGH_BEFORE] = bar.h
    if low != bar.low:
        extensions[MARKER_LOW_BEFORE] = bar.low
    return dataclasses.replace(bar, o=o, h=h, low=low, c=c, extensions=extensions), REASON_REBUILT


def mark_open_provisional(bar: CandleBar) -> CandleBar:
    """Open не доведено тіками: бар брокера без змін, з маркером для аудиту і подальшого ремонту."""
    return dataclasses.replace(bar, extensions={**bar.extensions, MARKER_PROVISIONAL: True})


def _same_price(left: float, right: float, price_step: float) -> bool:
    return abs(left - right) <= price_step / 2.0
