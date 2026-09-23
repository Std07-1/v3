"""Канонічні бакети й anchor для TF (P4)."""

from __future__ import annotations

from typing import Optional, Set


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


def bucket_start_ms(ts_ms: int, tf_ms: int, anchor_offset_ms: int) -> int:
    start = ((ts_ms - anchor_offset_ms) // tf_ms) * tf_ms + anchor_offset_ms
    return int(start)
