from __future__ import annotations

from collections import OrderedDict
from typing import Any, Optional


class RamLayer:
    """RAM шар: LRU вікна у процесі."""

    def __init__(self, max_keys: int = 8, max_bars: int = 60000) -> None:
        self._max_keys = max(1, int(max_keys))
        self._max_bars = max(1, int(max_bars))
        self._windows: OrderedDict[tuple[str, int], list[dict[str, Any]]] = OrderedDict()

    def _touch(self, key: tuple[str, int]) -> None:
        if key in self._windows:
            self._windows.move_to_end(key)

    def _evict_if_needed(self) -> None:
        while len(self._windows) > self._max_keys:
            self._windows.popitem(last=False)

    def get_window(self, symbol: str, tf_s: int, limit: int) -> Optional[list[dict[str, Any]]]:
        key = (symbol, tf_s)
        bars = self._windows.get(key)
        if not bars:
            return None
        if limit > 0 and len(bars) < limit:
            return None
        self._touch(key)
        if limit > 0:
            return bars[-limit:]
        return list(bars)

    def set_window(self, symbol: str, tf_s: int, bars: list[dict[str, Any]]) -> None:
        key = (symbol, tf_s)
        trimmed = list(bars)
        if self._max_bars and len(trimmed) > self._max_bars:
            trimmed = trimmed[-self._max_bars :]
        self._windows[key] = trimmed
        self._touch(key)
        self._evict_if_needed()

    def upsert_bar(self, symbol: str, tf_s: int, bar: dict[str, Any]) -> None:
        key = (symbol, tf_s)
        bars = self._windows.get(key)
        if bars is None:
            self._windows[key] = [bar]
            self._touch(key)
            self._evict_if_needed()
            return
        open_ms = bar.get("open_time_ms")
        if not isinstance(open_ms, int):
            return
        replaced = False
        for idx, existing in enumerate(bars):
            if existing.get("open_time_ms") == open_ms:
                bars[idx] = bar
                replaced = True
                break
        if not replaced:
            bars.append(bar)
            bars.sort(key=lambda x: x.get("open_time_ms", 0))
        if self._max_bars and len(bars) > self._max_bars:
            self._windows[key] = bars[-self._max_bars :]
        else:
            self._windows[key] = bars
        self._touch(key)
        self._evict_if_needed()

    def stats(self) -> dict[str, Any]:
        return {
            "ram_keys": len(self._windows),
            "ram_max_keys": self._max_keys,
            "ram_max_bars": self._max_bars,
        }
