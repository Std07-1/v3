"""
core/smc/engine.py — SmcEngine orchestrator (ADR-0024 §3.3).

Інваріанти:
  S0: pure logic, NO I/O
  S1: не пише в UDS (read-only overlay)
  S2: same bars → same snapshot (deterministic)
  S4: on_bar() < max_compute_ms (rail з логуванням)
  S5: всі пороги з SmcConfig, не hardcoded

Python 3.7 compatible.
"""
from __future__ import annotations

import logging
import time
from collections import deque
from typing import Any, Deque, Dict, List, Optional, Tuple

from core.model.bars import CandleBar
from core.smc.config import SmcConfig
from core.smc.fvg import detect_fvg
from core.smc.order_blocks import detect_order_blocks
from core.smc.structure import classify_swings, detect_structure_events
from core.smc.swings import detect_raw_swings
from core.smc.types import (
    SmcDelta, SmcLevel, SmcSnapshot, SmcSwing, SmcZone,
)

_log = logging.getLogger(__name__)


def _perf_ms() -> float:
    return time.time() * 1000.0


class _TfState:
    """Стан SMC для однієї пари (symbol, tf_s)."""

    __slots__ = (
        "_bars", "_last_snapshot", "_last_delta", "_lookback",
    )

    def __init__(self, lookback: int) -> None:
        self._bars: Deque[CandleBar] = deque(maxlen=lookback)
        self._last_snapshot: Optional[SmcSnapshot] = None
        self._last_delta: Optional[SmcDelta] = None
        self._lookback = lookback

    def append(self, bar: CandleBar) -> None:
        """Додає бар, зберігаючи вікно lookback_bars."""
        self._bars.append(bar)

    def bars_list(self) -> List[CandleBar]:
        return list(self._bars)

    @property
    def last_snapshot(self) -> Optional[SmcSnapshot]:
        return self._last_snapshot

    @last_snapshot.setter
    def last_snapshot(self, v: Optional[SmcSnapshot]) -> None:
        self._last_snapshot = v

    @property
    def last_delta(self) -> Optional[SmcDelta]:
        return self._last_delta

    @last_delta.setter
    def last_delta(self, v: Optional[SmcDelta]) -> None:
        self._last_delta = v


class SmcEngine:
    """Orchestrator SMC E1 (ADR-0024): per-(symbol, tf) стан.

    Lifecycle:
      1. update(symbol, tf_s, bars)  — full recompute (cold-start, warmup)
      2. on_bar(bar)                 — incremental (append + recompute)
      3. get_snapshot(symbol, tf_s)  — read current state
      4. get_htf_bias(symbol, tf_s)  — trend bias for cross-TF alignment
      5. reset(symbol, tf_s)         — скинути стан (symbol switch)

    S0: NO I/O — жодних файлів, мережі, Redis.
    S2: Deterministic — same bars → same snapshot.
    """

    def __init__(self, config: SmcConfig) -> None:
        self._config = config
        self._states: Dict[Tuple[str, int], _TfState] = {}

    # ── Public API ──────────────────────────────────────────────────

    def update(
        self,
        symbol: str,
        tf_s: int,
        bars: List[CandleBar],
    ) -> SmcSnapshot:
        """Full recompute з N барів (cold-start / warmup).

        Args:
            symbol: символ (напр. "XAU/USD")
            tf_s: таймфрейм у секундах
            bars: відсортований список CandleBar (oldest first)

        Returns:
            SmcSnapshot з усіма E1 зонами, свінгами, рівнями.
        """
        state = self._get_or_create(symbol, tf_s)
        state._bars = deque(bars[-self._config.lookback_bars:], maxlen=self._config.lookback_bars)
        snap = self._compute_snapshot(symbol, tf_s, state.bars_list())
        state.last_snapshot = snap
        return snap

    def on_bar(self, bar: CandleBar) -> SmcDelta:
        """Incremental update: додає бар, перераховує, повертає delta.

        S4: rail — лог якщо elapsed > max_compute_ms.
        """
        t0 = _perf_ms()

        state = self._get_or_create(bar.symbol, bar.tf_s)

        # S1 rail: skip incomplete bars (preview bars)
        if not bar.complete:
            _log.debug("SMC_SKIP_PREVIEW sym=%s tf=%d", bar.symbol, bar.tf_s)
            return _empty_delta(bar)

        prev_snap = state.last_snapshot
        state.append(bar)
        bars = state.bars_list()

        new_snap = self._compute_snapshot(bar.symbol, bar.tf_s, bars)
        state.last_snapshot = new_snap

        delta = _diff_snapshots(prev_snap, new_snap, bar.open_time_ms)
        state.last_delta = delta

        elapsed = _perf_ms() - t0
        if elapsed > self._config.max_compute_ms:
            _log.warning(
                "SMC_SLOW sym=%s tf=%d elapsed_ms=%.1f budget=%d",
                bar.symbol, bar.tf_s, elapsed, self._config.max_compute_ms,
            )

        return delta

    def get_snapshot(self, symbol: str, tf_s: int) -> SmcSnapshot:
        """Повертає поточний стан для (symbol, tf_s)."""
        state = self._states.get((symbol, tf_s))
        if state is not None and state.last_snapshot is not None:
            return state.last_snapshot
        return _empty_snapshot(symbol, tf_s)

    def get_htf_bias(self, symbol: str, tf_s: int) -> Optional[str]:
        """Trend bias для cross-TF alignment (ADR §3.3)."""
        snap = self.get_snapshot(symbol, tf_s)
        return snap.trend_bias

    def last_delta(self, symbol: str, tf_s: int) -> Optional[SmcDelta]:
        """Остання delta (після останнього on_bar) для WS delta frame."""
        state = self._states.get((symbol, tf_s))
        return state.last_delta if state else None

    def reset(self, symbol: str, tf_s: int) -> None:
        """Скидає стан (symbol switch / config change)."""
        key = (symbol, tf_s)
        if key in self._states:
            del self._states[key]
            _log.info("SMC_RESET sym=%s tf=%d", symbol, tf_s)

    # ── Private ─────────────────────────────────────────────────────

    def _get_or_create(self, symbol: str, tf_s: int) -> _TfState:
        key = (symbol, tf_s)
        if key not in self._states:
            self._states[key] = _TfState(self._config.lookback_bars)
        return self._states[key]

    def _compute_snapshot(
        self,
        symbol: str,
        tf_s: int,
        bars: List[CandleBar],
    ) -> SmcSnapshot:
        """Full SMC computation для E1 алгоритмів (Swings, Structure, OB, FVG).

        S2: детермінований — same bars → same output.
        """
        import time as _time
        now_ms = int(_time.time() * 1000)

        if not bars:
            return _empty_snapshot(symbol, tf_s)

        # ── E1.1: Raw swings ──
        raw_swings = detect_raw_swings(bars, period=self._config.swing_period)

        # ── E1.2: Classify + Structure events ──
        classified = classify_swings(raw_swings)
        struct_events, trend_bias, last_bos_ms, last_choch_ms = detect_structure_events(
            classified, bars
        )

        all_swings = classified + struct_events
        all_swings.sort(key=lambda s: s.time_ms)

        # ── E1.3: Order Blocks ──
        ob_zones = detect_order_blocks(bars, struct_events, self._config)

        # ── E1.4: FVG ──
        fvg_zones = detect_fvg(bars, self._config)

        # Обмеження загальної кількості зон (max_zones_per_tf)
        all_zones: List[SmcZone] = ob_zones + fvg_zones
        if len(all_zones) > self._config.max_zones_per_tf:
            # Залишаємо пріоритетно active зони, потім по strength
            all_zones.sort(
                key=lambda z: (
                    0 if z.status == "active" else 1,
                    -z.strength,
                )
            )
            all_zones = all_zones[:self._config.max_zones_per_tf]

        # Рівні — E1 MVP: порожньо (E2 додасть liquidity levels)
        levels: List[SmcLevel] = []

        return SmcSnapshot(
            symbol=symbol,
            tf_s=tf_s,
            zones=all_zones,
            swings=all_swings,
            levels=levels,
            trend_bias=trend_bias,
            last_bos_ms=last_bos_ms,
            last_choch_ms=last_choch_ms,
            computed_at_ms=now_ms,
            bar_count=len(bars),
        )


# ── Helpers ──────────────────────────────────────────────────────────

def _empty_snapshot(symbol: str, tf_s: int) -> SmcSnapshot:
    return SmcSnapshot(
        symbol=symbol,
        tf_s=tf_s,
        zones=[],
        swings=[],
        levels=[],
        trend_bias=None,
        last_bos_ms=None,
        last_choch_ms=None,
        computed_at_ms=int(time.time() * 1000),
        bar_count=0,
    )


def _empty_delta(bar: CandleBar) -> SmcDelta:
    return SmcDelta(
        symbol=bar.symbol,
        tf_s=bar.tf_s,
        bar_open_ms=bar.open_time_ms,
        new_zones=[],
        mitigated_zones=[],
        updated_zones=[],
        new_swings=[],
        new_levels=[],
        removed_levels=[],
        trend_bias=None,
    )


def _diff_snapshots(
    prev: Optional[SmcSnapshot],
    curr: SmcSnapshot,
    bar_open_ms: int,
) -> SmcDelta:
    """Порівнює два snapshots → SmcDelta з мінімальним набором змін."""
    if prev is None:
        # Перший snapshot — всі дані нові
        return SmcDelta(
            symbol=curr.symbol,
            tf_s=curr.tf_s,
            bar_open_ms=bar_open_ms,
            new_zones=list(curr.zones),
            mitigated_zones=[],
            updated_zones=[],
            new_swings=list(curr.swings),
            new_levels=list(curr.levels),
            removed_levels=[],
            trend_bias=curr.trend_bias,
        )

    prev_zone_ids = {z.id: z for z in prev.zones}
    curr_zone_ids = {z.id: z for z in curr.zones}

    new_zones = [z for z in curr.zones if z.id not in prev_zone_ids]
    mitigated = [
        z.id for z in prev.zones
        if z.status in ("active", "tested")
        and z.id in curr_zone_ids
        and curr_zone_ids[z.id].status in ("mitigated", "filled")
    ]
    updated = [
        curr_zone_ids[z.id]
        for z in prev.zones
        if z.id in curr_zone_ids
        and curr_zone_ids[z.id].status != z.status
        and z.id not in mitigated
    ]

    prev_swing_ids = {s.id for s in prev.swings}
    new_swings = [s for s in curr.swings if s.id not in prev_swing_ids]

    prev_level_ids = {l.id: l for l in prev.levels}
    curr_level_ids = {l.id: l for l in curr.levels}
    new_levels = [l for l in curr.levels if l.id not in prev_level_ids]
    removed_levels = [l.id for l in prev.levels if l.id not in curr_level_ids]

    trend_changed = curr.trend_bias != prev.trend_bias
    return SmcDelta(
        symbol=curr.symbol,
        tf_s=curr.tf_s,
        bar_open_ms=bar_open_ms,
        new_zones=new_zones,
        mitigated_zones=mitigated,
        updated_zones=updated,
        new_swings=new_swings,
        new_levels=new_levels,
        removed_levels=removed_levels,
        trend_bias=curr.trend_bias if trend_changed else None,
    )
