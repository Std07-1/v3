"""Гейт архіву брокера перед settle M1 (ADR-0103 S2, `tools.repair.settle_gate`): відмова до запису, не тиха компенсація.

Календар — справжній сезонний XAU/USD з config.json (літо: Нд 22:00 → Пт 20:45, денна перерва 21:00–22:00).
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from runtime.ingest.tick_common import calendar_for_symbol
from tools.repair import settle_gate as sg

REPO = Path(__file__).resolve().parents[1]
UTC = dt.timezone.utc
M1 = 60_000


def _ms(y, mo, d, h=0, mi=0) -> int:
    return int(dt.datetime(y, mo, d, h, mi, tzinfo=UTC).timestamp() * 1000)


def _parse_iso(text: str) -> int:
    return int(dt.datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(UTC).timestamp() * 1000)


@pytest.fixture(scope="module")
def is_trading():
    cfg = json.loads((REPO / "config.json").read_text(encoding="utf-8"))
    return calendar_for_symbol(cfg, "XAU/USD").is_trading_minute


def _trading_keys(is_trading, lo, hi):
    return {t for t in range(lo, hi, M1) if is_trading(t)}


def test_week_close_is_last_friday_minute_and_week_open_is_sunday_session_start(is_trading):
    assert sg.is_week_close_minute(is_trading, _ms(2026, 9, 18, 20, 44))
    assert not sg.is_week_close_minute(is_trading, _ms(2026, 9, 18, 20, 43))
    assert not sg.is_week_close_minute(is_trading, _ms(2026, 9, 17, 20, 59))  # денна перерва — не закриття тижня
    assert sg.last_week_open_ms(is_trading, _ms(2026, 9, 23, 12)) == _ms(2026, 9, 20, 22)


def test_gate_counts_chunk_errors_only_when_the_chunk_has_trading_minutes(is_trading):
    lo, hi = _ms(2026, 9, 21), _ms(2026, 9, 22)
    keys = _trading_keys(is_trading, lo, hi)
    chunks = [{"label": "day", "start": "2026-09-19T00:00:00+00:00", "end": "2026-09-20T00:00:00+00:00", "error": "x"},
              {"label": "day", "start": "2026-09-20T00:00:00+00:00", "end": "2026-09-21T00:00:00+00:00", "error": "y"}]
    problems, gate = sg.archive_gate(chunks, keys, is_trading, lo, hi, min_coverage=0.9, parse_iso=_parse_iso)
    assert gate["errors_trading"] == 1  # субота без торгів не лічиться, неділя з 22:00 — лічиться
    assert problems == ["ARCHIVE_CHUNK_ERROR_TRADING day 2026-09-20T00:00..2026-09-21T00:00 y"]
    problems, _gate = sg.archive_gate(None, keys, is_trading, lo, hi, min_coverage=0.9, parse_iso=_parse_iso)
    assert problems == ["ARCHIVE_META_NO_CHUNKS"]


def test_gate_refuses_thin_day_without_baseline_and_accepts_it_against_equal_baseline(is_trading):
    lo, hi = _ms(2026, 9, 21), _ms(2026, 9, 22)
    thin = set(sorted(_trading_keys(is_trading, lo, hi))[:600])
    problems, gate = sg.archive_gate([], thin, is_trading, lo, hi, min_coverage=0.9, parse_iso=_parse_iso)
    assert [p.split(" ratio")[0] for p in problems] == ["COVERAGE_BELOW_MIN"]
    assert gate["days"][0]["got"] == 600
    problems, _gate = sg.archive_gate([], thin, is_trading, lo, hi, min_coverage=0.9, baseline_keys=set(thin),
                                      parse_iso=_parse_iso)
    assert problems == []
    problems, _gate = sg.archive_gate([], set(), is_trading, lo, hi, min_coverage=0.9, parse_iso=_parse_iso)
    assert problems == ["EMPTY_TRADING_DAY day=2026-09-21"]


def test_gate_against_ours_refuses_archive_thinner_than_our_ssot_beyond_tolerance(is_trading):
    lo, hi = _ms(2026, 9, 21), _ms(2026, 9, 21, 0, 5)
    ours = _trading_keys(is_trading, lo, hi)
    archive = set(sorted(ours)[:4])
    problems, _gate = sg.archive_gate([], archive, is_trading, lo, hi, min_coverage=0.9, ours_keys=ours,
                                      parse_iso=_parse_iso)
    assert problems == ["COVERAGE_BELOW_OURS archive_trading=4 ours_trading=5 tol=0 day=2026-09-21"]
    problems, _gate = sg.archive_gate([], archive, is_trading, lo, hi, min_coverage=0.9, ours_keys=ours,
                                      ours_tolerance=1, parse_iso=_parse_iso)
    assert problems == []


def test_missing_week_close_minute_refuses_unless_absent_in_ours_too(is_trading):
    lo, hi = _ms(2026, 9, 18, 20), _ms(2026, 9, 18, 21)
    full = _trading_keys(is_trading, lo, hi)
    close = _ms(2026, 9, 18, 20, 44)
    archive = full - {close}
    problems, _gate = sg.archive_gate([], archive, is_trading, lo, hi, min_coverage=0.5, parse_iso=_parse_iso)
    assert problems == ["WEEK_CLOSE_MINUTE_MISSING Fri 2026-09-18 20:44"]
    problems, gate = sg.archive_gate([], archive, is_trading, lo, hi, min_coverage=0.5, ours_keys=archive,
                                     parse_iso=_parse_iso)
    assert problems == [] and gate["week_close_holiday"] == ["Fri 2026-09-18 20:44"]
