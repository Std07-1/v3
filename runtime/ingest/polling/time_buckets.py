from __future__ import annotations


def floor_bucket_start_ms(ts_ms: int, tf_s: int, anchor_offset_s: int = 0) -> int:
    """Початок bucket для tf_s з опційним anchor_offset_s (для D1 зазвичай)."""
    tf_ms = tf_s * 1000
    adj = ts_ms - anchor_offset_s * 1000
    b0 = (adj // tf_ms) * tf_ms
    return b0 + anchor_offset_s * 1000
