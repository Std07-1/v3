"""
core/smc/sessions.py — Trading Session H/L Computation (ADR-0035).

Інваріанти:
  S0: pure logic, NO I/O.
  S2: deterministic — same bars + config → same levels.
  S3: Level IDs deterministic: {kind}_{symbol}_{tf_s}_{price×100}.
  S5: session windows — SSOT in config.json:smc.sessions.

Python 3.7 compatible.
"""

from __future__ import annotations

import dataclasses
import logging
from typing import Any, Dict, List, Optional, Tuple

from core.model.bars import CandleBar
from core.smc.types import SmcLevel, make_level_id

_log = logging.getLogger(__name__)

# ── Session Window definition ──────────────────────────────


def _parse_utc_minutes(s: str) -> int:
    """Parse 'HH:MM' → minutes from midnight UTC."""
    parts = s.split(":")
    return int(parts[0]) * 60 + int(parts[1])


@dataclasses.dataclass(frozen=True)
class SessionWindow:
    """Визначення однієї торгової сесії (UTC)."""

    name: str  # "asia" | "london" | "newyork"
    label: str  # "Asia" | "London" | "New York"
    open_utc_min: int  # minutes from midnight UTC
    close_utc_min: int  # minutes from midnight UTC
    kz_start_min: int  # killzone start (minutes from midnight)
    kz_end_min: int  # killzone end


@dataclasses.dataclass(frozen=True)
class SessionState:
    """Стан однієї сесії: running H/L + previous H/L."""

    name: str
    active: bool
    in_killzone: bool
    current_high: Optional[float]
    current_low: Optional[float]
    current_start_ms: Optional[int]
    previous_high: Optional[float]
    previous_low: Optional[float]
    previous_start_ms: Optional[int]


def load_session_windows(definitions: Dict[str, Dict[str, Any]]) -> List[SessionWindow]:
    """Config dict → list of SessionWindow. S5: SSOT from config.json."""
    windows = []
    for name, d in definitions.items():
        windows.append(
            SessionWindow(
                name=name,
                label=str(d.get("label", name)),
                open_utc_min=_parse_utc_minutes(str(d.get("open_utc", "00:00"))),
                close_utc_min=_parse_utc_minutes(str(d.get("close_utc", "00:00"))),
                kz_start_min=_parse_utc_minutes(
                    str(d.get("killzone_start_utc", "00:00"))
                ),
                kz_end_min=_parse_utc_minutes(str(d.get("killzone_end_utc", "00:00"))),
            )
        )
    return windows


# ── Naming convention ──────────────────────────────────────

# Session name → (active_high_kind, active_low_kind, prev_high_kind, prev_low_kind)
_SESSION_KIND_MAP: Dict[str, Tuple[str, str, str, str]] = {
    "asia": ("as_h", "as_l", "p_as_h", "p_as_l"),
    "london": ("lon_h", "lon_l", "p_lon_h", "p_lon_l"),
    "newyork": ("ny_h", "ny_l", "p_ny_h", "p_ny_l"),
}


# ── Helpers ────────────────────────────────────────────────


def _ms_to_utc_minutes(epoch_ms: int) -> int:
    """Epoch ms → minutes from midnight UTC of that day."""
    total_s = epoch_ms // 1000
    return (total_s % 86400) // 60


def _ms_to_day_start(epoch_ms: int) -> int:
    """Epoch ms → midnight UTC of that day (epoch ms)."""
    total_s = epoch_ms // 1000
    return (total_s - total_s % 86400) * 1000


def _bar_in_session(bar_open_ms: int, sw: SessionWindow) -> bool:
    """Чи бар належить до сесії (bar.open_ms в [open, close) UTC minutes)."""
    bar_min = _ms_to_utc_minutes(bar_open_ms)
    if sw.open_utc_min < sw.close_utc_min:
        return sw.open_utc_min <= bar_min < sw.close_utc_min
    else:
        # Midnight cross (e.g. 22:00-06:00): open > close
        return bar_min >= sw.open_utc_min or bar_min < sw.close_utc_min


def _bar_in_killzone(bar_open_ms: int, sw: SessionWindow) -> bool:
    """Чи бар в killzone (kz_start ≤ bar_min < kz_end)."""
    bar_min = _ms_to_utc_minutes(bar_open_ms)
    if sw.kz_start_min < sw.kz_end_min:
        return sw.kz_start_min <= bar_min < sw.kz_end_min
    else:
        return bar_min >= sw.kz_start_min or bar_min < sw.kz_end_min


def classify_bar_sessions(bar_open_ms: int, sessions: List[SessionWindow]) -> List[str]:
    """Визначити, до яких сесій належить бар (може бути >1 при overlap)."""
    return [sw.name for sw in sessions if _bar_in_session(bar_open_ms, sw)]


def get_current_session(
    current_time_ms: int, sessions: List[SessionWindow]
) -> Tuple[str, bool]:
    """Визначити поточну сесію + чи в killzone. Returns (session_name, in_killzone).

    Якщо жодна — returns ('off_session', False).
    При overlap — пріоритет: london > newyork > asia (найімовірніший trade).
    """
    active = []
    for sw in sessions:
        if _bar_in_session(current_time_ms, sw):
            kz = _bar_in_killzone(current_time_ms, sw)
            active.append((sw.name, kz))
    if not active:
        return ("off_session", False)
    # Priority при overlap: london > newyork > asia
    _PRIORITY = {"london": 0, "newyork": 1, "asia": 2}
    active.sort(key=lambda x: _PRIORITY.get(x[0], 99))
    return active[0]


# ── Main computation ───────────────────────────────────────


def compute_session_levels(
    bars: List[CandleBar],
    sessions: List[SessionWindow],
    current_time_ms: int,
    symbol: str,
    tf_s: int = 86400,
) -> Tuple[List[SmcLevel], List[SessionState]]:
    """Обчислити session H/L levels + session states.

    Args:
        bars: M1 бари, sorted by open_time_ms (oldest first). Must be complete.
        sessions: session definitions з config.
        current_time_ms: поточний час (epoch ms).
        symbol: символ.
        tf_s: TF під яким session levels зберігаються (86400 = D1 scope).

    Returns:
        levels: List[SmcLevel] — max 12 (3 sessions × 2 H/L × 2 [current+prev])
        states: List[SessionState] — стан кожної сесії.
    """
    if not bars or not sessions:
        return [], []

    # R_BUG_HUNTER: assert sorted (callers pre-sort, but keep as defensive assert)
    for i in range(1, len(bars)):
        if bars[i].open_time_ms < bars[i - 1].open_time_ms:
            _log.debug(
                "SESSION_BARS_UNSORTED idx=%d a_ms=%d b_ms=%d",
                i,
                bars[i - 1].open_time_ms,
                bars[i].open_time_ms,
            )
            break

    # Get current day boundary for current/previous session split
    current_day_start = _ms_to_day_start(current_time_ms)
    prev_day_start = current_day_start - 86400_000

    levels: List[SmcLevel] = []
    states: List[SessionState] = []

    for sw in sessions:
        kinds = _SESSION_KIND_MAP.get(sw.name)
        if kinds is None:
            continue
        act_h_kind, act_l_kind, prev_h_kind, prev_l_kind = kinds

        # Determine if session is currently active + killzone
        is_active = _bar_in_session(current_time_ms, sw)
        is_kz = _bar_in_killzone(current_time_ms, sw) if is_active else False

        # Collect bars for CURRENT session period (today)
        cur_high: Optional[float] = None
        cur_low: Optional[float] = None
        cur_start_ms: Optional[int] = None

        # Collect bars for PREVIOUS session period (yesterday or earlier today)
        prev_high: Optional[float] = None
        prev_low: Optional[float] = None
        prev_start_ms: Optional[int] = None

        for bar in bars:
            if not bar.complete:
                continue
            if not _bar_in_session(bar.open_time_ms, sw):
                continue

            bar_day = _ms_to_day_start(bar.open_time_ms)

            if bar_day >= current_day_start:
                # Today's session = current (running)
                if cur_high is None or bar.h > cur_high:
                    cur_high = bar.h
                if cur_low is None or bar.low < cur_low:
                    cur_low = bar.low
                if cur_start_ms is None:
                    cur_start_ms = bar.open_time_ms
            elif bar_day >= prev_day_start:
                # Yesterday's session = previous (locked)
                if prev_high is None or bar.h > prev_high:
                    prev_high = bar.h
                if prev_low is None or bar.low < prev_low:
                    prev_low = bar.low
                if prev_start_ms is None:
                    prev_start_ms = bar.open_time_ms

        # Generate SmcLevel for each H/L
        # Current session levels: show as long as today's data exists
        # (not only when active — completed session H/L still relevant)
        if cur_high is not None and cur_low is not None:
            levels.append(
                SmcLevel(
                    id=make_level_id(act_h_kind, symbol, tf_s, cur_high),
                    symbol=symbol,
                    tf_s=tf_s,
                    kind=act_h_kind,
                    price=cur_high,
                    time_ms=cur_start_ms,
                    touches=1,
                )
            )
            levels.append(
                SmcLevel(
                    id=make_level_id(act_l_kind, symbol, tf_s, cur_low),
                    symbol=symbol,
                    tf_s=tf_s,
                    kind=act_l_kind,
                    price=cur_low,
                    time_ms=cur_start_ms,
                    touches=1,
                )
            )

        if prev_high is not None and prev_low is not None:
            levels.append(
                SmcLevel(
                    id=make_level_id(prev_h_kind, symbol, tf_s, prev_high),
                    symbol=symbol,
                    tf_s=tf_s,
                    kind=prev_h_kind,
                    price=prev_high,
                    time_ms=prev_start_ms,
                    touches=1,
                )
            )
            levels.append(
                SmcLevel(
                    id=make_level_id(prev_l_kind, symbol, tf_s, prev_low),
                    symbol=symbol,
                    tf_s=tf_s,
                    kind=prev_l_kind,
                    price=prev_low,
                    time_ms=prev_start_ms,
                    touches=1,
                )
            )

        states.append(
            SessionState(
                name=sw.name,
                active=is_active,
                in_killzone=is_kz,
                current_high=cur_high,
                current_low=cur_low,
                current_start_ms=cur_start_ms,
                previous_high=prev_high,
                previous_low=prev_low,
                previous_start_ms=prev_start_ms,
            )
        )

    return levels, states
