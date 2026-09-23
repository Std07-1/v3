"""Суцільний ланцюг свічок M1 (ADR-0101 §3.1) — одне визначення розриву для записувачів і health.

Інваріант SSOT: для сусідніх видимих барів a < b ``o(b) == c(a)`` у межах представлення float. Бар, який display
ховає (``is_display_hidden``: маркер ``calendar_pause_flat``), не сусід у ланцюзі, хоча ключ його зайнятий.

Правило послідовності, що тримає інваріант, живе в ``runtime.ingest.m1_session_filter``, вимір у SSOT — у
``core.health.measure_chain_breaks``. Предикати розриву і прихованого бару тут, в одному місці, щоб display,
записувач, агрегація і вимір не розійшлись (D15.2), а ``core`` не імпортував ``runtime`` (I0). Тут же критерій, чи
може між сусідніми барами бути наша діра (``hole_possible_between``): один для виміру і для пакетного записувача, що
тягне ланцюг лише там, де діри бути не може.

Модуль чистий і сумісний з Python 3.7: його імпортує ``m1_session_filter``, яким користується і ``.venv37``.
"""
from __future__ import annotations

from typing import Any, Callable, Mapping, Optional

# Допуск — представлення float, не крок ціни (як рейка ADR-0100 open_chain_breaks)
CHAIN_REL_TOL = 1e-9

# Маркер інжесту, за яким display ховає бар (артефакт паузи/вихідних від брокера). Читати — лише через
# `is_display_hidden`, щоб критерій приховування жив в одному місці.
MARKER_CALENDAR_PAUSE_FLAT = "calendar_pause_flat"

# Крок календаря: предикат торгової хвилини визначений на хвилинах
_MINUTE_MS = 60_000


def is_display_hidden(extensions: Optional[Mapping[str, Any]]) -> bool:
    """Бар — артефакт паузи/вихідних від брокера, і display його ховає: лише явний маркер інжесту (ADR-0096 §3.1).

    Одне правило для display (``runtime/ws/candle_map.py``), ланцюга (такий бар не сусід ні для записувачів M1, ні для
    health) і агрегації похідних (у бакет не йде). Змінити критерій приховування — тут, і він зміниться для всіх разом.
    ``extensions`` — розширення бару; не словник (зіпсований рядок) — бар не прихований.
    """
    return isinstance(extensions, Mapping) and bool(extensions.get(MARKER_CALENDAR_PAUSE_FLAT))


def open_breaks_chain(prev_close: float, bar_open: float) -> bool:
    """Розрив ланцюга: open бару ≠ close попереднього в межах представлення float."""
    return abs(bar_open - prev_close) > CHAIN_REL_TOL * max(1.0, abs(prev_close))


def hole_possible_between(
    prev_open_ms: int,
    bar_open_ms: int,
    *,
    is_trading_fn: Callable[[int], bool],
    session_open_grace_min: int = 0,
) -> bool:
    """Чи може між сусідніми M1 a < b бути наша діра — торгова хвилина, бар якої брокер мав дати (ADR-0101 §3.1).

    Хвилини строго між барами: ``[a + 1 хв, b)``. Діри немає, якщо жодна з них не торгова за календарем (сусідні
    хвилини, денна перерва, вихідні) або торгова лише як одна з перших ``session_open_grace_min`` хвилин сесії.
    Частина символів відкриває сесію в брокера на хвилину пізніше календаря: перший бар металів FXCM — 22:01, а не
    22:00 (скан SSOT 23.09.2026: XAU 149 з 152 літніх відкриттів, XAG 148 з 152, о 22:00 — жодного), EUSTX50 — 06:01,
    GER30 — 00:31. Відсутність бару в такій хвилині діри не доводить: через неї ланцюг у брокера суцільний, як
    через перерву.

    ``is_trading_fn`` — предикат хвилини (``MarketCalendar.is_trading_minute``); ``session_open_grace_min`` — з групи
    календаря символу (``core.config_loader.session_open_grace_resolver``).
    """
    if session_open_grace_min < 0:
        raise ValueError("session_open_grace_min=%r: запізнення відкриття сесії — хвилин ≥ 0" % session_open_grace_min)
    for minute_ms in range(prev_open_ms + _MINUTE_MS, bar_open_ms, _MINUTE_MS):
        if is_trading_fn(minute_ms) and not _in_session_open_grace(minute_ms, is_trading_fn, session_open_grace_min):
            return True
    return False


def _in_session_open_grace(minute_ms: int, is_trading_fn: Callable[[int], bool], grace_min: int) -> bool:
    """Торгова хвилина — одна з перших ``grace_min`` хвилин сесії: серед ``grace_min`` хвилин перед нею є неторгова."""
    return any(not is_trading_fn(minute_ms - k * _MINUTE_MS) for k in range(1, grace_min + 1))
