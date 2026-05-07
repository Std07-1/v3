"""Cowork runtime cadence guard (ADR-001 follow-up, slice cowork.003).

Code-side enforcement of STEP 0a from `cowork_prompt_template_v3.md`. The
prompt describes scheduled slots S1-S4 + event-flag fallback; this module is
the **runtime SSOT** of those rules so the bot can decide whether to consume
its thinking budget for a scan or emit a structured `off_slot_skip` log
without touching the LLM.

Address gap **G3** (bot-side slot definition) from prompt template §"Known
operational gaps". Slot table here MUST stay in sync with the prompt's STEP
0a SCHEDULED SLOTS table.

Address gap **G2** mitigation surface: `should_run_now()` reads
`event_flag.json` written by an external watcher (slice cowork.004).
Without that watcher running, secondary trigger always reports `absent` and
off-slot calls degrade to `silent_skip`.

Pure-ish:
    * No HTTP, no Redis, no UDS writes.
    * Single I/O surface = optional read of `event_flag.json` (documented).
    * Deterministic: same `(now_utc, event_flag_state)` -> same decision.

Invariants:
    CR1 — slot table is the single runtime source of truth for cadence;
          mirrored only in the prompt template's STEP 0a (doc layer).
    CR2 — `should_run_now()` never raises for malformed flag file; degrades
          to `event_flag = "invalid"` and treats as absent.
    CR3 — both DST variants per slot are accepted; the closer one wins on
          transition days (no calendar lookup required).
    CR4 — return value is a single dict suitable for direct
          `json.dumps()` into a structured log line; no datetime objects.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger("cowork.runner")

# ---------------------------------------------------------------------------
# SSOT slot table — mirror of prompt template STEP 0a SCHEDULED SLOTS.
# Each slot lists ALL acceptable (hour, minute) variants in UTC; on a DST
# transition day either neighbour is accepted (CR3). Order inside the list
# does not matter — closeness wins.
# ---------------------------------------------------------------------------

SCHEDULED_SLOTS_UTC: dict[str, list[tuple[int, int]]] = {
    "S1_pre_asia_23:30": [(23, 30)],  # year-round
    "S2_london_kz_open": [(6, 0), (7, 0)],  # summer 06:00 / winter 07:00
    "S3_ny_kz_open": [(12, 30), (13, 30)],  # summer 12:30 / winter 13:30
    "S4_ny_late_19:00": [(19, 0)],  # year-round
}

TOLERANCE_MIN: int = 15
EVENT_FLAG_TTL_MIN: int = 30
VALID_TRIGGERS: frozenset[str] = frozenset({"tda_signal", "bias_flip"})


# ---------------------------------------------------------------------------
# Decision payload
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CadenceDecision:
    """Outcome of `should_run_now()`. JSON-serialisable via `to_log_dict()`."""

    run: bool
    reason: str  # 'slot_match' | 'event_trigger' | 'off_slot_skip'
    now_utc_iso: str
    closest_slot: str  # slot id or 'none'
    delta_min: int  # absolute minutes from closest scheduled slot
    event_flag: str  # 'present' | 'absent' | 'stale' | 'invalid' | 'unchecked'
    event_trigger: Optional[str] = None  # populated when event_flag == 'present'
    event_age_min: Optional[int] = None  # age of valid event flag

    def to_log_dict(self) -> dict:
        d = {
            "event": "cadence_decision" if self.run else "off_slot_skip",
            "run": self.run,
            "reason": self.reason,
            "now_utc": self.now_utc_iso,
            "closest_slot": self.closest_slot,
            "delta_min": self.delta_min,
            "event_flag": self.event_flag,
        }
        if self.event_trigger is not None:
            d["event_trigger"] = self.event_trigger
        if self.event_age_min is not None:
            d["event_age_min"] = self.event_age_min
        return d


# ---------------------------------------------------------------------------
# Phase 1 — closest-slot resolution
# ---------------------------------------------------------------------------


def _slot_distance_min(now: datetime, slot_h: int, slot_m: int) -> int:
    """Minimum minutes between `now` and the slot time on the same UTC day.

    Slot is a wall-clock anchor (HH:MM UTC). Compare against `now` on the
    same calendar day; if the slot already passed by >12h treat the next
    day as candidate too. We pick the smaller absolute delta.
    """
    same_day = now.replace(hour=slot_h, minute=slot_m, second=0, microsecond=0)
    candidates = [same_day]
    # Cross-midnight tolerance: if the same-day slot is >12h away,
    # the previous- or next-day occurrence is necessarily closer.
    delta_minutes_same = abs((now - same_day).total_seconds()) / 60.0
    if delta_minutes_same > 12 * 60:
        if same_day < now:
            candidates.append(same_day + timedelta(days=1))
        else:
            candidates.append(same_day - timedelta(days=1))
    return int(round(min(abs((now - c).total_seconds()) / 60.0 for c in candidates)))


def _find_closest_slot(now_utc: datetime) -> tuple[str, int]:
    """Return `(slot_id, delta_min)` for the closest scheduled slot.

    Iterates every variant; smallest delta wins. Empty `SCHEDULED_SLOTS_UTC`
    would return `('none', 10**9)` but the table is a module constant so
    that branch is unreachable in production (kept for invariant clarity).
    """
    best_slot = "none"
    best_delta = 10**9
    for slot_id, variants in SCHEDULED_SLOTS_UTC.items():
        for hh, mm in variants:
            d = _slot_distance_min(now_utc, hh, mm)
            if d < best_delta:
                best_delta = d
                best_slot = slot_id
    return best_slot, best_delta


# ---------------------------------------------------------------------------
# Phase 2 — event flag check
# ---------------------------------------------------------------------------


def evaluate_event_flag_payload(
    payload: Optional[dict],
    now_utc: datetime,
) -> tuple[str, Optional[str], Optional[int]]:
    """Pure evaluator — same shape result as `_check_event_flag` (CR2).

    Used by both the file-based path (`_check_event_flag`) and the HTTP
    endpoint (`runtime/api_v3/cowork.py:_handle_event_flag`) so the
    state machine has a single SSOT implementation.

    Returns `(state, trigger, age_min)`:
        'absent'  — payload is None
        'invalid' — payload not a dict, or missing/unknown trigger/ts
        'stale'   — valid payload but |age| > EVENT_FLAG_TTL_MIN
        'present' — valid + fresh
    """
    if payload is None:
        return "absent", None, None
    if not isinstance(payload, dict):
        return "invalid", None, None

    trigger = payload.get("trigger")
    ts_raw = payload.get("ts")
    if trigger not in VALID_TRIGGERS or not isinstance(ts_raw, str):
        return "invalid", None, None

    try:
        ts_clean = ts_raw.replace("Z", "+00:00")
        flag_ts = datetime.fromisoformat(ts_clean)
        if flag_ts.tzinfo is None:
            flag_ts = flag_ts.replace(tzinfo=timezone.utc)
    except ValueError:
        return "invalid", None, None

    age_min = int(round((now_utc - flag_ts).total_seconds() / 60.0))
    if age_min > EVENT_FLAG_TTL_MIN or age_min < -EVENT_FLAG_TTL_MIN:
        return "stale", trigger, age_min
    return "present", trigger, age_min


def _check_event_flag(
    flag_path: Optional[Path],
    now_utc: datetime,
) -> tuple[str, Optional[str], Optional[int]]:
    """File-based wrapper for `evaluate_event_flag_payload` (CR2).

    State semantics (CR2 — never raise):
        'unchecked' — `flag_path` is None (caller opted out)
        'absent'    — file does not exist
        'invalid'   — file exists but unreadable / malformed / wrong fields
        'stale'     — valid file but `age > EVENT_FLAG_TTL_MIN`
        'present'   — valid + fresh trigger
    """
    if flag_path is None:
        return "unchecked", None, None

    try:
        if not flag_path.exists():
            return "absent", None, None
        raw = flag_path.read_text(encoding="utf-8").strip()
        if not raw:
            return "invalid", None, None
        payload = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        log.warning(
            "cowork.runner: event_flag read failed path=%s err=%s", flag_path, exc
        )
        return "invalid", None, None

    return evaluate_event_flag_payload(payload, now_utc)


# ---------------------------------------------------------------------------
# Phase 3 — public decision
# ---------------------------------------------------------------------------


def should_run_now(
    now_utc: Optional[datetime] = None,
    event_flag_path: Optional[Path] = None,
    *,
    event_flag_payload: Optional[dict] = None,
) -> CadenceDecision:
    """Decide whether a cowork scan should proceed at `now_utc`.

    Args:
        now_utc: Override clock for tests. Defaults to `datetime.now(UTC)`.
                 MUST be timezone-aware UTC; naive datetimes are rejected
                 with `ValueError` (CR4 — predictable inputs).
        event_flag_path: Path to `event_flag.json` written by external
                         watcher (slice cowork.004). `None` → skip secondary
                         trigger check (CI / unit tests).
        event_flag_payload: Already-fetched payload dict (e.g. from the
                            `/api/v3/cowork/event_flag` HTTP endpoint, slice
                            cowork.005). When provided, takes precedence
                            over `event_flag_path` (no file I/O).

    Returns:
        `CadenceDecision` with `.run` boolean and full structured log fields.
    """
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)
    elif now_utc.tzinfo is None:
        raise ValueError("now_utc must be timezone-aware UTC")
    else:
        now_utc = now_utc.astimezone(timezone.utc)

    now_iso = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    closest_slot, delta_min = _find_closest_slot(now_utc)

    # Primary trigger: scheduled slot match.
    if delta_min <= TOLERANCE_MIN:
        return CadenceDecision(
            run=True,
            reason="slot_match",
            now_utc_iso=now_iso,
            closest_slot=closest_slot,
            delta_min=delta_min,
            event_flag="unchecked",
        )

    # Secondary trigger: event flag.
    # Precedence: explicit payload (HTTP-fetched) wins over file path.
    # Both None → 'unchecked' (caller opted out of secondary trigger).
    if event_flag_payload is not None:
        flag_state, trigger, age_min = evaluate_event_flag_payload(
            event_flag_payload, now_utc
        )
    else:
        flag_state, trigger, age_min = _check_event_flag(event_flag_path, now_utc)
    if flag_state == "present":
        return CadenceDecision(
            run=True,
            reason="event_trigger",
            now_utc_iso=now_iso,
            closest_slot=closest_slot,
            delta_min=delta_min,
            event_flag="present",
            event_trigger=trigger,
            event_age_min=age_min,
        )

    return CadenceDecision(
        run=False,
        reason="off_slot_skip",
        now_utc_iso=now_iso,
        closest_slot=closest_slot,
        delta_min=delta_min,
        event_flag=flag_state,
        event_trigger=trigger,
        event_age_min=age_min,
    )


__all__ = [
    "SCHEDULED_SLOTS_UTC",
    "TOLERANCE_MIN",
    "EVENT_FLAG_TTL_MIN",
    "VALID_TRIGGERS",
    "CadenceDecision",
    "evaluate_event_flag_payload",
    "should_run_now",
]
