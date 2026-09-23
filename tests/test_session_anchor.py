"""Сезонний якір торгового дня (ADR-0095 §3.1, §3.7): правило, свідок tz-бази, конвенція FXCM, вкладеність H4 у D1."""
from __future__ import annotations

import datetime as dt
import random
from zoneinfo import ZoneInfo

import pytest

from core.session_anchor import (
    D1_S,
    H4_S,
    RULE_NY_CLOSE_US_DST,
    RULE_UTC_MIDNIGHT,
    htf_anchor_offset_s,
    htf_bucket_start_ms,
    trading_day_open_ms,
)

UTC = dt.timezone.utc
NY = ZoneInfo("America/New_York")
FXCM = RULE_NY_CLOSE_US_DST
H4_MS = H4_S * 1000
D1_MS = D1_S * 1000


def _ms(y, mo, d, h=0, mi=0) -> int:
    return int(dt.datetime(y, mo, d, h, mi, tzinfo=UTC).timestamp() * 1000)


def test_session_anchor_matches_zoneinfo_2007_2040_equal():
    """Свідок: для кожної дати 2007–2040 відкриття = 17:00 America/New_York у UTC (зміниться закон/tzdata — червоне)."""
    day = dt.date(2007, 1, 1)
    while day <= dt.date(2040, 12, 31):
        ny_close = dt.datetime(day.year, day.month, day.day, 17, 0, tzinfo=NY).astimezone(UTC)
        expected_ms = int(ny_close.timestamp() * 1000)
        assert trading_day_open_ms(expected_ms, FXCM) == expected_ms, day
        assert trading_day_open_ms(expected_ms - 1, FXCM) < expected_ms, day
        day += dt.timedelta(days=1)


@pytest.mark.parametrize("open_utc", [
    (1987, 10, 26), (1988, 3, 14), (1995, 3, 30), (1995, 10, 30), (2006, 3, 28), (2006, 10, 30),
])
def test_session_anchor_pre2007_uses_modern_rule_fxcm_convention(open_utc):
    """D1 XAU `src=history` 1987–2006 у вікнах, де старе правило США дало б 22:00, — у брокера 21:00 (сучасне правило)."""
    open_ms = _ms(*open_utc, 21)
    assert trading_day_open_ms(open_ms, FXCM) == open_ms
    assert htf_bucket_start_ms(open_ms + 5 * 3_600_000, D1_S, FXCM) == open_ms


def test_h4_bucket_nested_in_d1_all_seasons_true():
    rng = random.Random(95)
    lo, hi = _ms(2024, 1, 1), _ms(2028, 1, 1)
    for _ in range(20_000):
        ts = rng.randrange(lo, hi)
        d1_open = htf_bucket_start_ms(ts, D1_S, FXCM)
        d1_next = trading_day_open_ms(d1_open + 26 * 3_600_000, FXCM)
        h4_open = htf_bucket_start_ms(ts, H4_S, FXCM)
        assert d1_open <= h4_open <= ts < d1_next
        assert (h4_open - d1_open) % H4_MS == 0
        assert min(h4_open + H4_MS, d1_next) > ts


def test_dst_weekend_2026_03_08_grid_switch_matches_broker():
    """Пт 06.03 18:00 UTC — останній H4 зимової сітки; Нд 08.03 21:00 UTC — відкриття першої літньої доби (23 год)."""
    fri_last_h4 = _ms(2026, 3, 6, 18)
    assert htf_bucket_start_ms(fri_last_h4 + 30 * 60_000, H4_S, FXCM) == fri_last_h4
    sun_open = _ms(2026, 3, 8, 21)
    assert trading_day_open_ms(sun_open, FXCM) == sun_open
    assert trading_day_open_ms(sun_open - 1, FXCM) == _ms(2026, 3, 7, 22)  # субота-доба: 23 год
    assert htf_anchor_offset_s(H4_S, sun_open + 60_000, FXCM) == 21 * 3600


def test_dst_weekend_2026_11_01_winter_grid_starts_at_22():
    """01.11.2026 (перша неділя листопада) — перша зимова доба відкривається о 22:00 UTC; субота-доба має 25 год."""
    sun_open = _ms(2026, 11, 1, 22)
    assert trading_day_open_ms(sun_open, FXCM) == sun_open
    assert trading_day_open_ms(sun_open - 1, FXCM) == _ms(2026, 10, 31, 21)
    assert htf_bucket_start_ms(_ms(2026, 11, 2, 3), H4_S, FXCM) == _ms(2026, 11, 2, 2)


def test_h4_summer_2026_grid_is_tv_grid():
    """Літо 2026: H4 на 21/01/05/09/13/17 UTC — сітка TV і брокера (не наші 22/02/06/10/14/18)."""
    starts = {htf_bucket_start_ms(_ms(2026, 9, 22, h, 30), H4_S, FXCM) % D1_MS // 3_600_000 for h in range(24)}
    assert starts == {21, 1, 5, 9, 13, 17}


def test_below_h4_buckets_are_epoch_aligned_and_anchor_zero():
    ts = _ms(2026, 9, 22, 17, 44) + 12_345
    assert htf_bucket_start_ms(ts, 900, FXCM) == _ms(2026, 9, 22, 17, 30)
    assert htf_bucket_start_ms(ts, 3600, FXCM) == _ms(2026, 9, 22, 17)
    assert htf_anchor_offset_s(3600, ts, FXCM) == 0


def test_utc_midnight_rule_is_binance_grid():
    ts = _ms(2026, 9, 22, 17, 44)
    assert trading_day_open_ms(ts, RULE_UTC_MIDNIGHT) == _ms(2026, 9, 22)
    assert htf_bucket_start_ms(ts, H4_S, RULE_UTC_MIDNIGHT) == _ms(2026, 9, 22, 16)


@pytest.mark.parametrize("call", [
    lambda: trading_day_open_ms(0, "legacy_79200"),
    lambda: htf_bucket_start_ms(0, 43_200, FXCM),
    lambda: htf_anchor_offset_s(43_200, 0, FXCM),
])
def test_unknown_rule_or_tf_raises_loudly(call):
    with pytest.raises(ValueError):
        call()
