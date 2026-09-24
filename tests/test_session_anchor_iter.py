"""Кінець бакета і перевірка сітки (ADR-0095 S2a): суміжність бакетів, обрубки DST-діб, гучна відмова поза сіткою."""
from __future__ import annotations

import datetime as dt

import pytest

from core.session_anchor import (
    D1_S,
    H4_S,
    RULE_NY_CLOSE_US_DST,
    RULE_UTC_MIDNIGHT,
    OffSeasonGridError,
    assert_on_season_grid,
    htf_bucket_start_ms,
    htf_next_bucket_start_ms,
    season_label,
)

UTC = dt.timezone.utc
FXCM = RULE_NY_CLOSE_US_DST
HOUR_MS = 3_600_000


def _ms(y, mo, d, h=0, mi=0) -> int:
    return int(dt.datetime(y, mo, d, h, mi, tzinfo=UTC).timestamp() * 1000)


@pytest.mark.parametrize("rule", [FXCM, RULE_UTC_MIDNIGHT])
@pytest.mark.parametrize("tf_s", [H4_S, D1_S])
def test_next_bucket_is_adjacent_and_on_grid_2024_2027(rule, tf_s):
    """Ітерація по сітці 2024–2027: кожен бакет закінчується там, де починається наступний, і обидва — на сітці."""
    bucket = htf_bucket_start_ms(_ms(2024, 1, 1), tf_s, rule)
    end = _ms(2028, 1, 1)
    steps = 0
    while bucket < end:
        nxt = htf_next_bucket_start_ms(bucket, tf_s, rule)
        assert nxt > bucket
        assert htf_bucket_start_ms(nxt - 1, tf_s, rule) == bucket
        assert htf_bucket_start_ms(nxt, tf_s, rule) == nxt
        bucket = nxt
        steps += 1
    assert steps > (1400 if tf_s == D1_S else 8000)


def test_next_bucket_spring_2026_03_08_h4_stub_3h():
    """Сесійна субота-доба 07.03 23:00 → 08.03 22:00 має 23 год: останній H4 (нд 19:00) — обрубок на 3 год."""
    assert htf_next_bucket_start_ms(_ms(2026, 3, 8, 19), H4_S, FXCM) == _ms(2026, 3, 8, 22)


def test_next_bucket_fall_2026_11_01_h4_stub_1h():
    """Сесійна субота-доба 31.10 22:00 → 01.11 23:00 має 25 год: H4 нд 22:00 — обрубок на 1 год, не поглинає
    наступну добу."""
    assert htf_next_bucket_start_ms(_ms(2026, 11, 1, 22), H4_S, FXCM) == _ms(2026, 11, 1, 23)
    assert htf_bucket_start_ms(_ms(2026, 11, 1, 22, 30), H4_S, FXCM) == _ms(2026, 11, 1, 22)


def test_next_bucket_d1_23h_and_25h():
    assert htf_next_bucket_start_ms(_ms(2026, 3, 7, 22), D1_S, FXCM) == _ms(2026, 3, 8, 21)
    assert htf_next_bucket_start_ms(_ms(2026, 10, 31, 21), D1_S, FXCM) == _ms(2026, 11, 1, 22)
    assert htf_next_bucket_start_ms(_ms(2026, 9, 22, 21), D1_S, FXCM) == _ms(2026, 9, 23, 21)


def test_next_bucket_below_h4_is_open_plus_tf():
    assert htf_next_bucket_start_ms(_ms(2026, 9, 22, 17), 3600, FXCM) == _ms(2026, 9, 22, 18)


def test_assert_on_season_grid_winter_grid_in_summer_raises():
    """H4 23:00 улітку — це зимова сітка: відмова з очікуваним відкриттям 22:00, а не тихий прохід через alt."""
    with pytest.raises(OffSeasonGridError) as err:
        assert_on_season_grid(_ms(2026, 7, 1, 23), H4_S, FXCM)
    assert err.value.expected_open_ms == _ms(2026, 7, 1, 22)
    assert "bar_off_season_grid" in str(err.value) and "season=summer" in str(err.value)


def test_assert_on_season_grid_accepts_both_seasons():
    assert_on_season_grid(_ms(2026, 7, 1, 22), H4_S, FXCM)
    assert_on_season_grid(_ms(2026, 1, 5, 23), H4_S, FXCM)
    assert_on_season_grid(_ms(2026, 1, 5, 3), H4_S, FXCM)
    assert_on_season_grid(_ms(2026, 1, 5, 22), D1_S, FXCM)
    assert_on_season_grid(_ms(2026, 1, 5), D1_S, RULE_UTC_MIDNIGHT)


def test_season_label():
    assert season_label(_ms(2026, 9, 22, 12), FXCM) == "summer"
    assert season_label(_ms(2026, 1, 5, 12), FXCM) == "winter"
    assert season_label(_ms(2026, 9, 22, 12), RULE_UTC_MIDNIGHT) == "none"
