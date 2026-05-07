"""Smoke tests for cowork.memory.store — append, read, dedup, schema validation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from cowork.memory.schema import PublishedThesis, ScenarioSummary
from cowork.memory.store import append_thesis, read_by_scan_id, read_recent


def _make_thesis(
    *,
    scan_suffix: str = "001",
    symbol: str = "XAU/USD",
    ts: datetime | None = None,
    grade: str = "B",
) -> PublishedThesis:
    ts = ts or datetime(2026, 5, 6, 14, 30, tzinfo=timezone.utc)
    return PublishedThesis(
        ts=ts.isoformat().replace("+00:00", "Z"),
        scan_id=f"scan-20260506-1430{scan_suffix}-xauusd",
        symbol=symbol,
        model="claude-opus-4-7",
        prompt_version="v3.0",
        current_price=4687.5,
        tldr="Range mid; чекаю sweep H1 EQH перед коротким setup.",
        preferred_scenario_id="A",
        preferred_direction="bearish",
        preferred_probability=55,
        thesis_grade=grade,
        market_phase="ranging",
        session="newyork",
        in_killzone=True,
        watch_levels=(4720.0, 4687.0, 4660.0),
        scenarios_summary=(
            ScenarioSummary("A", "Bearish reversal from H4 premium", 55),
            ScenarioSummary("B", "Range continuation", 35),
            ScenarioSummary("C", "Bullish breakout", 10),
        ),
        prompt_hash="ab12cd34",
    )


def test_append_creates_file_and_record(tmp_path: Path) -> None:
    thesis = _make_thesis()
    path, dup = append_thesis(thesis, store_dir=tmp_path)
    assert dup is False
    assert path.exists()
    assert path.read_text(encoding="utf-8").count("\n") == 1


def test_append_same_scan_id_is_idempotent(tmp_path: Path) -> None:
    thesis = _make_thesis(scan_suffix="042")
    path, dup1 = append_thesis(thesis, store_dir=tmp_path)
    assert dup1 is False
    _, dup2 = append_thesis(thesis, store_dir=tmp_path)
    assert dup2 is True
    # Still only one line written
    assert path.read_text(encoding="utf-8").count("\n") == 1


def test_read_recent_returns_newest_first_within_age_cap(tmp_path: Path) -> None:
    base = datetime(2026, 5, 6, 14, 30, tzinfo=timezone.utc)
    older = _make_thesis(scan_suffix="aaa", ts=base - timedelta(hours=10))
    newer = _make_thesis(scan_suffix="bbb", ts=base - timedelta(hours=2), grade="A")
    expired = _make_thesis(scan_suffix="ccc", ts=base - timedelta(hours=20))
    for t in (older, newer, expired):
        append_thesis(t, store_dir=tmp_path)

    out = read_recent("XAU/USD", limit=5, max_age_h=12, store_dir=tmp_path, now=base)
    assert [t.scan_id for t in out] == [newer.scan_id, older.scan_id]
    assert out[0].thesis_grade == "A"


def test_read_recent_filters_by_symbol(tmp_path: Path) -> None:
    base = datetime(2026, 5, 6, 14, 30, tzinfo=timezone.utc)
    xau = _make_thesis(scan_suffix="x01", ts=base - timedelta(hours=1))
    btc = _make_thesis(
        scan_suffix="b01", ts=base - timedelta(hours=1), symbol="BTCUSDT"
    )
    append_thesis(xau, store_dir=tmp_path)
    append_thesis(btc, store_dir=tmp_path)

    xau_only = read_recent("XAU/USD", store_dir=tmp_path, now=base)
    btc_only = read_recent("BTCUSDT", store_dir=tmp_path, now=base)
    assert {t.scan_id for t in xau_only} == {xau.scan_id}
    assert {t.scan_id for t in btc_only} == {btc.scan_id}


def test_read_by_scan_id_roundtrip(tmp_path: Path) -> None:
    thesis = _make_thesis(scan_suffix="rt1")
    append_thesis(thesis, store_dir=tmp_path)
    found = read_by_scan_id(thesis.scan_id, store_dir=tmp_path)
    assert found is not None
    assert found.scan_id == thesis.scan_id
    assert found.preferred_direction == "bearish"
    assert found.scenarios_summary[0].probability == 55


def test_schema_rejects_invalid_grade() -> None:
    with pytest.raises(ValueError, match="thesis_grade"):
        PublishedThesis(
            ts="2026-05-06T14:30:00Z",
            scan_id="scan-20260506-143000-xauusd",
            symbol="XAU/USD",
            model="x",
            prompt_version="v3.0",
            current_price=1.0,
            tldr="x",
            preferred_scenario_id="A",
            preferred_direction="bullish",
            preferred_probability=10,
            thesis_grade="ZZZ",  # invalid
            market_phase="ranging",
            session="asia",
            in_killzone=False,
            watch_levels=(1.0, 2.0),
            scenarios_summary=(ScenarioSummary("A", "x", 100),),
        )


def test_schema_rejects_probability_sum_far_from_100() -> None:
    with pytest.raises(ValueError, match="probabilities"):
        PublishedThesis(
            ts="2026-05-06T14:30:00Z",
            scan_id="scan-20260506-143000-xauusd",
            symbol="XAU/USD",
            model="x",
            prompt_version="v3.0",
            current_price=1.0,
            tldr="x",
            preferred_scenario_id="A",
            preferred_direction="bullish",
            preferred_probability=10,
            thesis_grade="B",
            market_phase="ranging",
            session="asia",
            in_killzone=False,
            watch_levels=(1.0, 2.0),
            scenarios_summary=(
                ScenarioSummary("A", "x", 30),
                ScenarioSummary("B", "y", 30),
            ),
        )


def test_schema_rejects_preferred_id_missing_from_scenarios() -> None:
    with pytest.raises(ValueError, match="not present"):
        PublishedThesis(
            ts="2026-05-06T14:30:00Z",
            scan_id="scan-20260506-143000-xauusd",
            symbol="XAU/USD",
            model="x",
            prompt_version="v3.0",
            current_price=1.0,
            tldr="x",
            preferred_scenario_id="C",  # not in scenarios below
            preferred_direction="bullish",
            preferred_probability=10,
            thesis_grade="B",
            market_phase="ranging",
            session="asia",
            in_killzone=False,
            watch_levels=(1.0, 2.0),
            scenarios_summary=(
                ScenarioSummary("A", "x", 60),
                ScenarioSummary("B", "y", 40),
            ),
        )
