from __future__ import annotations

from typing import Dict, TYPE_CHECKING

from core.model.bars import ms_to_utc_dt

if TYPE_CHECKING:
    from runtime.store.uds import UnifiedDataStore


def _day_key(open_time_ms: int) -> str:
    return ms_to_utc_dt(open_time_ms).strftime("%Y%m%d")


def _day_index_key(tf_s: int, day: str) -> str:
    return f"{tf_s}:{day}"


def load_day_index(
    cache: Dict[str, set[int]],
    uds: "UnifiedDataStore",
    symbol: str,
    tf_s: int,
    day: str,
) -> set[int]:
    key = _day_index_key(tf_s, day)
    cached = cache.get(key)
    if cached is not None:
        return cached

    out = uds.load_day_open_times(symbol, tf_s, day)
    cache[key] = out
    return out


def has_on_disk(
    cache: Dict[str, set[int]],
    uds: "UnifiedDataStore",
    symbol: str,
    tf_s: int,
    open_time_ms: int,
) -> bool:
    day = _day_key(open_time_ms)
    idx = load_day_index(cache, uds, symbol, tf_s, day)
    return open_time_ms in idx


def mark_on_disk(
    cache: Dict[str, set[int]],
    uds: "UnifiedDataStore",
    symbol: str,
    tf_s: int,
    open_time_ms: int,
) -> None:
    day = _day_key(open_time_ms)
    idx = load_day_index(cache, uds, symbol, tf_s, day)
    idx.add(open_time_ms)
