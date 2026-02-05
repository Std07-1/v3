"""Канонічні бакети й anchor для TF (P4)."""

from __future__ import annotations

from typing import Any

from core.time_geom import bar_close_incl


TF_ALLOWLIST = {
    60,
    180,
    300,
    900,
    1800,
    3600,
    14400,
    86400,
}


def tf_to_ms(tf_s: int) -> int:
    if tf_s not in TF_ALLOWLIST:
        raise ValueError(f"unsupported_tf_s={tf_s}")
    return int(tf_s * 1000)


def resolve_anchor_offset_ms(tf_s: int, cfg: dict[str, Any]) -> int:
    day_anchor_offset_s = int(cfg.get("day_anchor_offset_s", 0) or 0)
    day_anchor_offset_s_d1 = cfg.get("day_anchor_offset_s_d1", None)
    if tf_s == 86400 and day_anchor_offset_s_d1 is not None:
        return max(0, int(day_anchor_offset_s_d1)) * 1000
    if tf_s >= 14400:
        return max(0, int(day_anchor_offset_s)) * 1000
    return 0


def bucket_start_ms(ts_ms: int, tf_ms: int, anchor_offset_ms: int) -> int:
    start = ((ts_ms - anchor_offset_ms) // tf_ms) * tf_ms + anchor_offset_ms
    return int(start)


def bucket_close_incl(open_ms: int, tf_ms: int) -> int:
    return bar_close_incl(open_ms, tf_ms)
