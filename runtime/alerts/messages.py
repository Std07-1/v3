"""Тексти бота сигналів (ADR-0114 §4.4): спрацювання, /alerts, /devices, довідка.

Чисті функції: без мережі й годинника, час — аргументом, пояс — `ZoneInfo` з config (`alerts.display_tz`). Ціни —
знаками символу з `price_display.digits_by_symbol` (як шкала графіка); повноту карти перевіряє `require_digits` на
старті процесу, тож тут символ без знаків — помилка програми, а не тихий інший формат.
"""

from __future__ import annotations

import datetime as dt
from typing import Iterable, Mapping, Sequence
from urllib.parse import quote
from zoneinfo import ZoneInfo

from core.alerts.types import AlertState, CrossDirection, FireVia, PriceAlert
from runtime.alerts.devices import Device

MAX_TEXT = 4000  # Telegram відхиляє > 4096 символів; запас — як у aione-alert.sh (ADR-0106 §3.6)
_DIRECTION = {CrossDirection.UP: "знизу вгору", CrossDirection.DOWN: "згори вниз"}

HELP_TEXT = (
    "Бот сигналів v3: повідомляє, коли ціна перетне вашу лінію на графіку.\n"
    "Лінію позначають «Сповіщати» на сайті; під'єднати пристрій — «☰ → Сповіщення → Під'єднати Telegram».\n"
    "/alerts — мої сповіщення\n/devices — під'єднані пристрої\n/revoke <id> — відключити пристрій"
)
OWNER_ONLY_TEXT = "Цей бот поки працює лише для власника сайту."
BAD_CODE_TEXT = "Код недійсний або минув. Відкрийте на сайті «Під'єднати Telegram» ще раз."


def require_digits(symbols: Iterable[str], digits: Mapping[str, int]) -> None:
    missing = sorted(s for s in symbols if not isinstance(digits.get(s), int))
    if missing:
        raise ValueError(f"price_display.digits_by_symbol: нема знаків для {missing}")


def chart_link(site_url: str, symbol: str, tf_label: str) -> str:
    """Посилання на графік символу й TF, звідки ставили лінію (`/` у символі читабельний, як у знімках ADR-0109)."""
    return f"{site_url.rstrip('/')}/?symbol={quote(symbol, safe='/')}&tf={quote(tf_label, safe='')}"


def fire_message(alert: PriceAlert, *, digits: Mapping[str, int], tz: ZoneInfo, site_url: str) -> str:
    fire = alert.fired
    if fire is None:
        raise ValueError(f"fire_message: сповіщення {alert.alert_id} не спрацювало")
    when = dt.datetime.fromtimestamp(fire.at_ms / 1000, tz)
    lines = [f"🔔 {alert.symbol} {_price(alert, alert.level, digits)} — перетнуто {_DIRECTION[fire.direction]}"]
    if fire.via is FireVia.TICK:
        lines.append(f"Ціна {_price(alert, fire.price, digits)} · {when:%d.%m %H:%M:%S} {when:%Z}")
    else:
        lines.append(
            f"Свічка M1 {when:%d.%m %H:%M} {when:%Z} дійшла до {_price(alert, fire.price, digits)}"
            " (між тіками — видно за свічкою)"
        )
    if fire.after_gap:
        lines.append("Перша ціна після паузи ринку — уже по той бік рівня")
    lines.append(chart_link(site_url, alert.symbol, alert.tf_label))
    return _fit("\n".join(lines))


def alerts_list(alerts: Sequence[PriceAlert], *, digits: Mapping[str, int], tz: ZoneInfo) -> str:
    if not alerts:
        return "Сповіщень нема. Позначте лінію «Сповіщати» на графіку."
    lines = [f"Сповіщення ({len(alerts)}):"]
    for alert in sorted(alerts, key=lambda a: (a.symbol, a.level)):
        level = f"{alert.symbol} {_price(alert, alert.level, digits)}"
        if alert.state is AlertState.ARMED:
            since = dt.datetime.fromtimestamp(alert.active_from_ms / 1000, tz)
            lines.append(f"🔔 {level} — чекає з {since:%d.%m %H:%M}")
        else:
            fired_at = dt.datetime.fromtimestamp(alert.fired.at_ms / 1000, tz)
            lines.append(f"🔕 {level} — спрацювало {fired_at:%d.%m %H:%M} {_DIRECTION[alert.fired.direction]}")
    return _fit("\n".join(lines))


def devices_list(devices: Sequence[Device], *, tz: ZoneInfo) -> str:
    if not devices:
        return "Під'єднаних пристроїв нема."
    lines = [f"Пристрої ({len(devices)}):"]
    for device in devices:
        since = dt.datetime.fromtimestamp(device.created_ms / 1000, tz)
        lines.append(f"{device.device_id} — {device.label}, з {since:%d.%m.%Y}")
    lines.append("Відключити: /revoke <id>")
    return _fit("\n".join(lines))


def _price(alert: PriceAlert, value: float, digits: Mapping[str, int]) -> str:
    return f"{value:.{digits[alert.symbol]}f}"


def _fit(text: str) -> str:
    return text if len(text) <= MAX_TEXT else text[: MAX_TEXT - 1] + "…"
