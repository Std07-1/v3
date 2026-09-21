"""Перша M1 після перерви: запечені компоненти бару — з тікової історії брокера (ADR-0096 слайс E).

FXCM віддає першу хвилину кожної сесії (денна перерва, вихідні) з open — і high або low — рівним ціні до перерви.
Полер комітить бар один раз, тож без перебудови запечене значення лишається назавжди і тягне M1…D1 (гігантська
перша свічка сесії).

Запечення визначається тіками самої хвилини, а не нашим close перед перервою (бар перед перервою буває округлений:
XAU/XAG 16.09 prev close 4381.00 при запеченому o 4380.77): open, що лежить ПОЗА діапазоном реальних тіків хвилини
(±½ кроку), не є жодною з цін цієї хвилини. Справжній open навіть при розбіжності серій t1 і m1 у 1–2 кроки лежить
усередині (NAS100: open 30299.59 при тіках [30290.71, 30302.71]). Перебудовується ЛИШЕ запечене: open — першим за
часом тіком; high/low — лише той, що дорівнював запеченому open, з тіків і close. c і v — брокерські. Звірки
«тіки = бар» за close чи обсягом немає свідомо: на T+8 с серії не узгоджені (замір 21.09 15:34–15:36 UTC).

Модуль чистий (без I/O і логів) і сумісний з Python 3.7. Рішення — тут; запит тіків і лог — у полері.
"""
from __future__ import annotations

import dataclasses
from typing import Any, Callable, Dict, FrozenSet, Mapping, Optional, Sequence, Tuple

from core.model.bars import CandleBar, normalize_ohlc
from runtime.ingest.m1_session_filter import is_session_open_minute

_M1_MS = 60 * 1000
_CONFIG_SECTION = "session_open_rebuild"  # config.json → m1_poller.session_open_rebuild

MARKER_REBUILT = "session_open_rebuilt"
MARKER_OPEN_BEFORE = "open_before"
MARKER_HIGH_BEFORE = "high_before"
MARKER_LOW_BEFORE = "low_before"
MARKER_PROVISIONAL = "open_provisional"

REASON_REBUILT = "rebuilt"
REASON_OPEN_WITHIN_TICKS = "open_within_ticks"
REASON_NO_TICKS = "no_ticks_in_minute"
REASON_PRICE_STEP_INVALID = "price_step_invalid"

# Причини, з якими бар брокера вже правильний: лишається як є, без маркера і без WARN.
BAR_CORRECT_AS_IS: FrozenSet[str] = frozenset({REASON_OPEN_WITHIN_TICKS})


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
    """Політика з config; секції немає → вимкнено. Битий ключ (зокрема enabled не bool) → ValueError."""
    m1_cfg = cfg.get("m1_poller")
    section = m1_cfg.get(_CONFIG_SECTION) if isinstance(m1_cfg, dict) else None
    if not isinstance(section, dict):
        return DISABLED_POLICY
    enabled = section.get("enabled", False)
    if not isinstance(enabled, bool):
        # bool("false") == True: рядок чи число замість JSON true/false не вгадується, а відмовляє гучно.
        raise ValueError("m1_poller.%s.enabled: очікується true/false, отримано %r" % (_CONFIG_SECTION, enabled))
    gap_min = int(section["gap_min"])
    steps = section["price_step_by_symbol"]
    if gap_min < 1 or not isinstance(steps, dict):
        raise ValueError("m1_poller.%s: gap_min≥1, price_step_by_symbol={...}" % _CONFIG_SECTION)
    price_steps = {str(sym): float(step) for sym, step in steps.items()}
    bad = sorted(sym for sym, step in price_steps.items() if not step > 0)
    if bad:
        raise ValueError("m1_poller.%s.price_step_by_symbol: крок має бути > 0: %s" % (_CONFIG_SECTION, bad))
    return SessionOpenRebuildPolicy(enabled=enabled, gap_ms=gap_min * _M1_MS, price_step_by_symbol=price_steps)


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
    # Календарна частина — те саме правило «перша хвилина сесії», що й у правилі M1→SSOT (одне місце, X35)
    return is_session_open_minute(open_ms, is_trading_fn)


def rebuild_session_open_bar(
    bar: CandleBar,
    ticks: Sequence[Tuple[int, float]],
    price_step: float,
) -> Tuple[Optional[CandleBar], str]:
    """(перебудований бар, REASON_REBUILT) або (None, причина). Вхідний бар не змінюється.

    Тіки — лише з [open_ms, close_ms), порядок — за часом. open у межах [min − ½ кроку, max + ½ кроку] тіків →
    бар справжній (REASON_OPEN_WITHIN_TICKS). Інакше запечений: o = перший за часом тік; high = max(тіки, c),
    якщо брокерський high дорівнював запеченому open (запечена ціна була екстремумом), інакше брокерський; low —
    дзеркально; нормалізація h ≥ max(o, c), low ≤ min(o, c); c і v — брокерські.
    """
    if not price_step > 0:
        return None, REASON_PRICE_STEP_INVALID
    minute_ticks = sorted(
        (tick for tick in ticks if bar.open_time_ms <= tick[0] < bar.close_time_ms), key=lambda tick: tick[0]
    )
    if not minute_ticks:
        return None, REASON_NO_TICKS
    bids = [bid for _tick_ms, bid in minute_ticks]
    half_step = price_step / 2.0
    ticks_low, ticks_high = min(bids), max(bids)
    if ticks_low - half_step <= bar.o <= ticks_high + half_step:
        return None, REASON_OPEN_WITHIN_TICKS
    baked_open = bar.o
    new_high = max(ticks_high, bar.c) if _same_price(bar.h, baked_open, price_step) else bar.h
    new_low = min(ticks_low, bar.c) if _same_price(bar.low, baked_open, price_step) else bar.low
    o, h, low, c = normalize_ohlc(bids[0], new_high, new_low, bar.c)
    extensions = {**bar.extensions, MARKER_REBUILT: True, MARKER_OPEN_BEFORE: baked_open}
    if h != bar.h:
        extensions[MARKER_HIGH_BEFORE] = bar.h
    if low != bar.low:
        extensions[MARKER_LOW_BEFORE] = bar.low
    return dataclasses.replace(bar, o=o, h=h, low=low, c=c, extensions=extensions), REASON_REBUILT


def mark_open_provisional(bar: CandleBar) -> CandleBar:
    """Open не перевірено тіками: бар брокера без змін, з маркером для аудиту і подальшого ремонту (settle)."""
    return dataclasses.replace(bar, extensions={**bar.extensions, MARKER_PROVISIONAL: True})


def _same_price(left: float, right: float, price_step: float) -> bool:
    return abs(left - right) <= price_step / 2.0
