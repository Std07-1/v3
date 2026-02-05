"""Канонічна геометрія часу для барів (перехідний шар)."""

from __future__ import annotations

from typing import Any, Dict


def bar_close_incl(open_ms: int, tf_ms: int) -> int:
    """End‑incl: close = open + tf_ms - 1."""
    return int(open_ms + tf_ms - 1)


def bar_close_excl(open_ms: int, tf_ms: int) -> int:
    """End‑excl: close = open + tf_ms."""
    return int(open_ms + tf_ms)


def normalize_bar(bar: Dict[str, Any], mode: str = "incl") -> Dict[str, Any]:
    """Нормалізує бар до end‑incl або end‑excl без зміни SSOT на диску."""
    out = dict(bar)
    open_ms = out.get("open_time_ms")
    tf_s = out.get("tf_s")
    if isinstance(open_ms, int) and isinstance(tf_s, int) and tf_s > 0:
        tf_ms = tf_s * 1000
        if mode == "incl":
            out["close_time_ms"] = bar_close_incl(open_ms, tf_ms)
        elif mode == "excl":
            out["close_time_ms"] = bar_close_excl(open_ms, tf_ms)
    return out
