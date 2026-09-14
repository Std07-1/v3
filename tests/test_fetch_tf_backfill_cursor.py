"""Курсор ланцюжка засіву: те, що `fetch_tf_backfill` друкує як `first=`, приймається як `--date-to` точно.

Навіщо. До 2026-09-14 курсор друкувався без секунд (`2025-11-27T07:00Z`), а `--date-to` приймав лише з
секундами або саму дату. Оператор обрізав курсор до доби — наступна партія закінчувалась о 00:00, і між нею
та першим баром попередньої лишалась дірка. На проді це 10 дірок M1 на XAU і на XAG (до ~12 год кожна):
92 бари, які health показує як root_mismatch, і 2534 бари всередині дірок, яких він не бачить зовсім.
"""
from __future__ import annotations

import datetime as dt

import pytest

from tools.fetch_tf_backfill import _format_cursor, _parse_date_utc

SEAM_FIRST_BAR_MS = int(dt.datetime(2025, 11, 27, 7, 0, tzinfo=dt.timezone.utc).timestamp() * 1000)


def _ms(value: dt.datetime) -> int:
    return int(value.timestamp() * 1000)


@pytest.mark.parametrize("open_ms", [SEAM_FIRST_BAR_MS, SEAM_FIRST_BAR_MS + 18 * 60_000, SEAM_FIRST_BAR_MS + 59_000])
def test_printed_cursor_round_trips_to_the_same_instant(open_ms):
    """Ключова властивість: `--date-to <first>` наступного кроку = рівно перший бар цього, без обрізання."""
    assert _ms(_parse_date_utc(_format_cursor(open_ms))) == open_ms


def test_legacy_cursor_without_seconds_is_accepted_not_truncated_to_the_day():
    """Старі логи засіву мають `first=2025-11-27T07:00Z` — його копіюють як є; раніше це був ValueError."""
    assert _ms(_parse_date_utc("2025-11-27T07:00Z")) == SEAM_FIRST_BAR_MS


@pytest.mark.parametrize("text, expected", [
    ("2025-11-27", dt.datetime(2025, 11, 27, tzinfo=dt.timezone.utc)),
    ("2025-11-27T07:00:00Z", dt.datetime(2025, 11, 27, 7, tzinfo=dt.timezone.utc)),
    ("2025-11-27T07:00:00+00:00", dt.datetime(2025, 11, 27, 7, tzinfo=dt.timezone.utc)),
])
def test_existing_date_formats_still_work(text, expected):
    assert _parse_date_utc(text) == expected


def test_unknown_format_is_refused_loudly():
    with pytest.raises(ValueError, match="Невідомий формат дати"):
        _parse_date_utc("27.11.2025 07:00")
