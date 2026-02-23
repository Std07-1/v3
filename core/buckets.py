"""Канонічні бакети й anchor для TF (P4)."""

from __future__ import annotations

from typing import Any, Optional, Set


def tf_to_ms(tf_s: int, *, tf_allowlist: Optional[Set[int]] = None) -> int:
    """Конвертує TF секунди → мілісекунди.

    Args:
        tf_s: таймфрейм у секундах (позитивне ціле).
        tf_allowlist: опціональна множина дозволених TF.
            Якщо передана і tf_s не в ній — ValueError.
            Якщо None — валідація не виконується (caller відповідає).
    """
    if tf_allowlist is not None and tf_s not in tf_allowlist:
        raise ValueError("unsupported_tf_s=%d not in allowlist" % tf_s)
    if not isinstance(tf_s, int) or tf_s <= 0:
        raise ValueError("invalid_tf_s=%s" % tf_s)
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
