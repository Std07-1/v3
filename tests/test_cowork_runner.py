"""Tests for `cowork.runner` cadence guard (slice cowork.003).

Covers SCHEDULED_SLOTS_UTC behaviour, ±15min tolerance window, both DST
variants for S2/S3, event_flag fallback (present/absent/stale/invalid),
cross-midnight S1, and CadenceDecision serialization.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from cowork.runner import (
    EVENT_FLAG_TTL_MIN,
    SCHEDULED_SLOTS_UTC,
    TOLERANCE_MIN,
    CadenceDecision,
    _find_closest_slot,
    should_run_now,
)


def _utc(y: int, mo: int, d: int, h: int, mi: int) -> datetime:
    return datetime(y, mo, d, h, mi, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Slot table sanity
# ---------------------------------------------------------------------------


def test_slot_table_has_four_named_slots() -> None:
    assert set(SCHEDULED_SLOTS_UTC.keys()) == {
        "S1_pre_asia_23:30",
        "S2_london_kz_open",
        "S3_ny_kz_open",
        "S4_ny_late_19:00",
    }


def test_s2_and_s3_have_both_dst_variants() -> None:
    assert (6, 0) in SCHEDULED_SLOTS_UTC["S2_london_kz_open"]
    assert (7, 0) in SCHEDULED_SLOTS_UTC["S2_london_kz_open"]
    assert (12, 30) in SCHEDULED_SLOTS_UTC["S3_ny_kz_open"]
    assert (13, 30) in SCHEDULED_SLOTS_UTC["S3_ny_kz_open"]


# ---------------------------------------------------------------------------
# Slot match (primary trigger)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "now,expected_slot",
    [
        (_utc(2026, 5, 7, 23, 30), "S1_pre_asia_23:30"),
        (_utc(2026, 5, 7, 23, 35), "S1_pre_asia_23:30"),  # +5min inside tol
        (_utc(2026, 5, 7, 23, 15), "S1_pre_asia_23:30"),  # -15min boundary
        (_utc(2026, 5, 7, 6, 0), "S2_london_kz_open"),  # summer
        (_utc(2026, 1, 7, 7, 0), "S2_london_kz_open"),  # winter
        (_utc(2026, 5, 7, 12, 30), "S3_ny_kz_open"),  # summer
        (_utc(2026, 1, 7, 13, 30), "S3_ny_kz_open"),  # winter
        (_utc(2026, 5, 7, 19, 0), "S4_ny_late_19:00"),
    ],
)
def test_slot_match_within_tolerance_runs(now: datetime, expected_slot: str) -> None:
    decision = should_run_now(now_utc=now)
    assert decision.run is True
    assert decision.reason == "slot_match"
    assert decision.closest_slot == expected_slot
    assert decision.delta_min <= TOLERANCE_MIN
    assert decision.event_flag == "unchecked"


def test_slot_match_at_exact_tolerance_boundary() -> None:
    # S2 winter 07:00 + exactly 15min → must still run.
    decision = should_run_now(now_utc=_utc(2026, 1, 7, 7, 15))
    assert decision.run is True
    assert decision.delta_min == TOLERANCE_MIN


def test_dst_transition_day_accepts_either_variant() -> None:
    # London DST switch ~ last Sun of March / Oct. On that day either
    # S2 06:00 or 07:00 is acceptable; closer wins.
    summer_close = should_run_now(now_utc=_utc(2026, 3, 29, 6, 5))
    winter_close = should_run_now(now_utc=_utc(2026, 3, 29, 7, 5))
    assert summer_close.run and summer_close.closest_slot == "S2_london_kz_open"
    assert winter_close.run and winter_close.closest_slot == "S2_london_kz_open"
    assert summer_close.delta_min == 5
    assert winter_close.delta_min == 5


# ---------------------------------------------------------------------------
# Off-slot skip (no event flag)
# ---------------------------------------------------------------------------


def test_off_slot_no_flag_skips() -> None:
    # 09:42 UTC — between S2 (07:00) and S3 (12:30/13:30); 162min from S2.
    decision = should_run_now(now_utc=_utc(2026, 5, 7, 9, 42))
    assert decision.run is False
    assert decision.reason == "off_slot_skip"
    assert decision.event_flag == "unchecked"
    assert decision.delta_min > TOLERANCE_MIN


def test_off_slot_with_none_path_marks_unchecked() -> None:
    decision = should_run_now(
        now_utc=_utc(2026, 5, 7, 10, 0),
        event_flag_path=None,
    )
    assert decision.run is False
    assert decision.event_flag == "unchecked"


# ---------------------------------------------------------------------------
# Event flag (secondary trigger)
# ---------------------------------------------------------------------------


def test_event_flag_present_runs(tmp_path: Path) -> None:
    flag = tmp_path / "event_flag.json"
    now = _utc(2026, 5, 7, 9, 42)
    flag.write_text(
        json.dumps(
            {
                "trigger": "tda_signal",
                "ts": (now - timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
        )
    )
    decision = should_run_now(now_utc=now, event_flag_path=flag)
    assert decision.run is True
    assert decision.reason == "event_trigger"
    assert decision.event_flag == "present"
    assert decision.event_trigger == "tda_signal"
    assert decision.event_age_min == 10


def test_event_flag_bias_flip_also_valid(tmp_path: Path) -> None:
    flag = tmp_path / "event_flag.json"
    now = _utc(2026, 5, 7, 10, 0)
    flag.write_text(
        json.dumps(
            {
                "trigger": "bias_flip",
                "ts": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
        )
    )
    decision = should_run_now(now_utc=now, event_flag_path=flag)
    assert decision.run is True
    assert decision.event_trigger == "bias_flip"


def test_event_flag_absent_keeps_skip(tmp_path: Path) -> None:
    flag = tmp_path / "event_flag.json"  # never written
    decision = should_run_now(
        now_utc=_utc(2026, 5, 7, 10, 0),
        event_flag_path=flag,
    )
    assert decision.run is False
    assert decision.event_flag == "absent"


def test_event_flag_stale_does_not_run(tmp_path: Path) -> None:
    flag = tmp_path / "event_flag.json"
    now = _utc(2026, 5, 7, 10, 0)
    stale_ts = now - timedelta(minutes=EVENT_FLAG_TTL_MIN + 5)
    flag.write_text(
        json.dumps(
            {
                "trigger": "tda_signal",
                "ts": stale_ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
        )
    )
    decision = should_run_now(now_utc=now, event_flag_path=flag)
    assert decision.run is False
    assert decision.event_flag == "stale"
    assert decision.event_age_min == EVENT_FLAG_TTL_MIN + 5


def test_event_flag_unknown_trigger_invalid(tmp_path: Path) -> None:
    flag = tmp_path / "event_flag.json"
    flag.write_text(
        json.dumps(
            {
                "trigger": "manual_kick",  # not in VALID_TRIGGERS
                "ts": "2026-05-07T10:00:00Z",
            }
        )
    )
    decision = should_run_now(
        now_utc=_utc(2026, 5, 7, 10, 0),
        event_flag_path=flag,
    )
    assert decision.run is False
    assert decision.event_flag == "invalid"


def test_event_flag_garbled_json_invalid(tmp_path: Path) -> None:
    flag = tmp_path / "event_flag.json"
    flag.write_text("{not json")
    decision = should_run_now(
        now_utc=_utc(2026, 5, 7, 10, 0),
        event_flag_path=flag,
    )
    assert decision.run is False
    assert decision.event_flag == "invalid"


def test_event_flag_empty_file_invalid(tmp_path: Path) -> None:
    flag = tmp_path / "event_flag.json"
    flag.write_text("")
    decision = should_run_now(
        now_utc=_utc(2026, 5, 7, 10, 0),
        event_flag_path=flag,
    )
    assert decision.run is False
    assert decision.event_flag == "invalid"


def test_event_flag_missing_ts_invalid(tmp_path: Path) -> None:
    flag = tmp_path / "event_flag.json"
    flag.write_text(json.dumps({"trigger": "tda_signal"}))
    decision = should_run_now(
        now_utc=_utc(2026, 5, 7, 10, 0),
        event_flag_path=flag,
    )
    assert decision.run is False
    assert decision.event_flag == "invalid"


# ---------------------------------------------------------------------------
# Cross-midnight closest-slot resolution
# ---------------------------------------------------------------------------


def test_s1_cross_midnight_closest_slot() -> None:
    # 00:30 UTC — yesterday 23:30 is 60min away, today 23:30 is 23h away.
    name, delta = _find_closest_slot(_utc(2026, 5, 8, 0, 30))
    assert name == "S1_pre_asia_23:30"
    assert delta == 60


def test_late_evening_picks_s1_not_s4() -> None:
    name, delta = _find_closest_slot(_utc(2026, 5, 7, 22, 0))
    # 22:00 → S1 23:30 = 90min; S4 19:00 = 180min. S1 wins.
    assert name == "S1_pre_asia_23:30"
    assert delta == 90


# ---------------------------------------------------------------------------
# Input validation + serialization
# ---------------------------------------------------------------------------


def test_naive_datetime_rejected() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        should_run_now(now_utc=datetime(2026, 5, 7, 7, 0))


def test_log_dict_serializable_for_skip() -> None:
    decision = should_run_now(now_utc=_utc(2026, 5, 7, 9, 42))
    payload = decision.to_log_dict()
    assert payload["event"] == "off_slot_skip"
    assert payload["run"] is False
    assert payload["closest_slot"] in SCHEDULED_SLOTS_UTC
    # Round-trip via JSON to confirm no datetime / Path leaked in.
    json.dumps(payload)


def test_log_dict_for_slot_match_event_field() -> None:
    decision = should_run_now(now_utc=_utc(2026, 5, 7, 19, 0))
    payload = decision.to_log_dict()
    assert payload["event"] == "cadence_decision"
    assert payload["run"] is True
    assert payload["reason"] == "slot_match"
    assert "event_trigger" not in payload
    assert "event_age_min" not in payload


def test_log_dict_for_event_trigger_includes_age(tmp_path: Path) -> None:
    flag = tmp_path / "event_flag.json"
    now = _utc(2026, 5, 7, 10, 0)
    flag.write_text(
        json.dumps(
            {
                "trigger": "tda_signal",
                "ts": (now - timedelta(minutes=8)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
        )
    )
    decision = should_run_now(now_utc=now, event_flag_path=flag)
    payload = decision.to_log_dict()
    assert payload["event_trigger"] == "tda_signal"
    assert payload["event_age_min"] == 8


def test_default_now_uses_current_utc(monkeypatch: pytest.MonkeyPatch) -> None:
    """Smoke check: calling without explicit `now_utc` does not raise and
    returns a CadenceDecision with a non-empty closest slot."""
    decision = should_run_now()
    assert isinstance(decision, CadenceDecision)
    assert decision.closest_slot in SCHEDULED_SLOTS_UTC
    assert decision.now_utc_iso.endswith("Z")

# ---------------------------------------------------------------------------
# cowork.005 — event_flag_payload precedence (HTTP-fetched payload)
# ---------------------------------------------------------------------------


def test_payload_present_takes_precedence_over_path(tmp_path: Path) -> None:
    """When both `event_flag_payload` (fresh) and `event_flag_path` (stale)
    are provided, payload wins — no file read happens."""
    now = _utc(2026, 5, 7, 9, 42)  # off-slot (delta > TOLERANCE_MIN)
    # File flag is stale (45min old, > TTL)
    flag = tmp_path / "event_flag.json"
    flag.write_text(
        json.dumps(
            {
                "trigger": "bias_flip",
                "ts": (now - timedelta(minutes=45)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
        )
    )
    fresh_payload = {
        "trigger": "tda_signal",
        "ts": (now - timedelta(minutes=3)).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    decision = should_run_now(
        now_utc=now,
        event_flag_path=flag,  # stale, would yield run=False
        event_flag_payload=fresh_payload,  # fresh, wins
    )
    assert decision.run is True
    assert decision.reason == "event_trigger"
    assert decision.event_trigger == "tda_signal"
    assert decision.event_age_min == 3


def test_payload_none_falls_back_to_path(tmp_path: Path) -> None:
    """`event_flag_payload=None` is the same as not providing it — file path
    still controls behaviour."""
    now = _utc(2026, 5, 7, 9, 42)
    flag = tmp_path / "event_flag.json"
    flag.write_text(
        json.dumps(
            {
                "trigger": "tda_signal",
                "ts": (now - timedelta(minutes=2)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
        )
    )
    decision = should_run_now(
        now_utc=now,
        event_flag_path=flag,
        event_flag_payload=None,
    )
    assert decision.run is True
    assert decision.reason == "event_trigger"


def test_payload_absent_dict_invalid_marker_yields_invalid() -> None:
    """A non-dict payload (e.g. malformed JSON sentinel) → state='invalid'."""
    now = _utc(2026, 5, 7, 9, 42)
    decision = should_run_now(
        now_utc=now,
        event_flag_payload={"not_a_trigger": "garbage"},
    )
    assert decision.run is False
    assert decision.event_flag == "invalid"


def test_payload_stale_payload_yields_off_slot_skip() -> None:
    """Payload older than TTL → `stale`, off-slot skip."""
    now = _utc(2026, 5, 7, 9, 42)
    decision = should_run_now(
        now_utc=now,
        event_flag_payload={
            "trigger": "tda_signal",
            "ts": (now - timedelta(minutes=EVENT_FLAG_TTL_MIN + 5)).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            ),
        },
    )
    assert decision.run is False
    assert decision.event_flag == "stale"
    assert decision.event_trigger == "tda_signal"
