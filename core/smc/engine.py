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

import dataclasses
import logging
import time
from collections import deque
from typing import Any, Deque, Dict, List, Optional, Tuple

from core.model.bars import CandleBar
from core.smc.config import SmcConfig, SmcDisplayConfig
from core.smc.fvg import detect_fvg
from core.smc.inducement import detect_inducement
from core.smc.key_levels import compute_key_levels, collect_htf_levels
from core.smc.liquidity import detect_liquidity_levels
from core.smc.order_blocks import detect_order_blocks
from core.smc.premium_discount import detect_premium_discount
from core.smc.structure import classify_swings, detect_structure_events
from core.smc.swings import detect_raw_swings
from core.smc.types import (
    SmcDelta, SmcLevel, SmcSnapshot, SmcSwing, SmcZone,
)

_log = logging.getLogger(__name__)

# ── Zone lifecycle constants & helpers (N1) ──────────────────────────

_BULL_ZONE_KINDS = frozenset({"ob_bull", "fvg_bull", "discount"})
_BEAR_ZONE_KINDS = frozenset({"ob_bear", "fvg_bear", "premium"})

_STATUS_RANK = {
    "active": 0,
    "partially_filled": 1,
    "tested": 2,
    "breaker": 3,
    "fading": 4,
    "mitigated": 5,
    "filled": 6,
    "expired": 7,
}


def _zone_rank(z):
    # type: (SmcZone) -> tuple
    """Deterministic sort key: strongest active first, oldest last."""
    return (-z.strength, _STATUS_RANK.get(z.status, 9), -z.anchor_bar_ms, z.id)


def _update_zone_lifecycle(
    fresh_zones,     # type: List[SmcZone]
    active_zones,    # type: Dict[str, SmcZone]
    last_bar,        # type: Optional[CandleBar]
    config,          # type: SmcConfig
    tf_s,            # type: int
):
    # type: (...) -> List[SmcZone]
    """N1 zone lifecycle: merge → FVG evict → mitigate → decay → hide → cap.

    Мутує active_zones in-place (state persistence між on_bar).
    Повертає відфільтрований список зон для snapshot.

    D-01: deterministic cap via _zone_rank.
    D-02: FVG not in fresh_ids → evict (prevents resurrection).
    R-04: mitigation by bar.c (close), NOT by wick.
    """
    fresh_ids = {z.id for z in fresh_zones}

    # 1) Merge fresh zones → active_zones (fresh wins)
    for z in fresh_zones:
        active_zones[z.id] = z

    # 2) D-02: FVG eviction — якщо FVG зникає з fresh → drop
    stale_fvg = [
        zid for zid, z in active_zones.items()
        if z.kind.startswith("fvg") and zid not in fresh_ids
    ]
    for zid in stale_fvg:
        del active_zones[zid]

    # 3) Mitigation + age decay (only with last_bar)
    if last_bar is not None:
        to_delete = []
        for zid, z in list(active_zones.items()):
            # R-04: mitigation by CLOSE, not wick
            mitigated = False
            if z.kind in _BULL_ZONE_KINDS and last_bar.c < z.low:
                mitigated = True
            elif z.kind in _BEAR_ZONE_KINDS and last_bar.c > z.high:
                mitigated = True

            if mitigated and z.status in ("active", "tested", "partially_filled"):
                active_zones[zid] = dataclasses.replace(
                    z, status="mitigated", end_ms=last_bar.open_time_ms,
                )
                continue

            # Age model: bars since creation
            bar_ms = tf_s * 1000
            if bar_ms > 0:
                age_bars = (last_bar.open_time_ms - z.anchor_bar_ms) // bar_ms
            else:
                age_bars = 0

            # >500 bars → expired, drop
            if age_bars > 500:
                to_delete.append(zid)
                continue

            # D1: configurable decay curve (F10: params at config root)
            if age_bars > config.decay_start_bars and z.strength > 0.15:
                if age_bars > config.decay_fast_bars:
                    factor = 0.92  # aggressive: ~0.15 after 20 bars
                else:
                    factor = 0.97  # gentle: ~0.54 after 20 bars
                decay = max(0.15, z.strength * factor)
                active_zones[zid] = dataclasses.replace(
                    z, strength=round(decay, 3),
                )

        for zid in to_delete:
            del active_zones[zid]

    # 4) Hide mitigated (optional)
    if config.hide_mitigated:
        result = [z for z in active_zones.values() if z.status != "mitigated"]
    else:
        result = list(active_zones.values())

    # 5) D-01: deterministic cap
    if len(result) > config.max_zones_per_tf:
        result.sort(key=_zone_rank)
        result = result[:config.max_zones_per_tf]

    return result


def _perf_ms() -> float:
    return time.time() * 1000.0


class _TfState:
    """Стан SMC для однієї пари (symbol, tf_s)."""

    __slots__ = (
        "_bars", "_last_snapshot", "_last_delta", "_lookback",
        "_active_zones",
    )

    def __init__(self, lookback: int) -> None:
        self._bars: Deque[CandleBar] = deque(maxlen=lookback)
        self._last_snapshot: Optional[SmcSnapshot] = None
        self._last_delta: Optional[SmcDelta] = None
        self._lookback = lookback
        self._active_zones: Dict[str, SmcZone] = {}

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
        state._active_zones = {}  # N1: full recompute → reset lifecycle
        snap = self._compute_snapshot(symbol, tf_s, state.bars_list(), state)
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

        new_snap = self._compute_snapshot(bar.symbol, bar.tf_s, bars, state)
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

    def get_snapshot_with_htf_levels(
        self,
        symbol: str,
        tf_s: int,
    ) -> SmcSnapshot:
        """Snapshot з ін’єкцією key levels з вищих TF (ADR-0024b).

        Коли трейдер дивиться на M15 — він бачить D1/H4/H1 рівні.
        S0: pure — читає _states dict (internal state), без I/O.
        """
        snap = self.get_snapshot(symbol, tf_s)
        htf_levels = collect_htf_levels(
            lambda s, t: self.get_snapshot(s, t),
            symbol,
            tf_s,
        )
        if htf_levels:
            merged = list(snap.levels) + htf_levels
            snap = dataclasses.replace(snap, levels=merged)
        return snap

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
        state: _TfState,
    ) -> SmcSnapshot:
        """Full SMC computation для E1 алгоритмів (Swings, Structure, OB, FVG).

        S2: детермінований — same bars → same output.
        """
        import time as _time
        now_ms = int(_time.time() * 1000)

        if not bars:
            return _empty_snapshot(symbol, tf_s)

        # ── F4: ATR once, pass to all detectors ──
        from core.smc.swings import compute_atr
        atr = compute_atr(bars, period=14)

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
        ob_zones = detect_order_blocks(bars, struct_events, self._config, atr=atr)

        # ── E1.4: FVG ──
        fvg_zones = detect_fvg(bars, self._config, atr=atr)

        # ── E2.6: Premium/Discount Zones ──
        pd_zones: List[SmcZone] = detect_premium_discount(classified, bars, self._config)

        # ── N1: Zone lifecycle (merge, FVG evict, mitigate, decay, cap) ──
        fresh_zones: List[SmcZone] = ob_zones + fvg_zones + pd_zones
        last_bar = bars[-1] if bars else None
        all_zones = _update_zone_lifecycle(
            fresh_zones, state._active_zones, last_bar, self._config, tf_s,
        )

        # ── E2.5: Liquidity Levels (Equal Highs / Equal Lows) ──
        # Передаємо classified (hh/hl/lh/ll) — вони містять confirmed info
        levels: List[SmcLevel] = detect_liquidity_levels(classified, bars, self._config, atr=atr)

        # ── ADR-0024b: Key Levels per TF (PDH/PDL, H4H/L, H1H/L, ...) ──
        key_lvls = compute_key_levels(bars)
        levels = levels + key_lvls

        # ── E2.7: Inducement (False Breakout Trap) ──
        inducement_swings: List[SmcSwing] = detect_inducement(bars, classified, self._config, atr=atr)
        if inducement_swings:
            all_swings = all_swings + inducement_swings
            all_swings.sort(key=lambda s: s.time_ms)

        snap = SmcSnapshot(
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

        # ── D1: Display filter (proximity + cap) ──
        snap = _filter_for_display(snap, bars, self._config, atr=atr)
        return snap


# ── D1: Display filter ───────────────────────────────────────────────

def _filter_for_display(
    snap: SmcSnapshot,
    bars: List[CandleBar],
    config: SmcConfig,
    atr: float = 0.0,       # F4: caller-supplied ATR
) -> SmcSnapshot:
    """Proximity + height + cap filter before sending to UI.

    Pure function (S0). Does NOT mutate active_zones state.
    Reduces wire payload: only zones/levels near current price.
    """
    if not bars:
        return snap

    last_bar = bars[-1]
    price = last_bar.c
    if atr <= 0.0:
        from core.smc.swings import compute_atr
        atr = compute_atr(bars, period=14)
    if atr <= 0:
        return snap

    disp = config.display  # type: SmcDisplayConfig
    radius = disp.proximity_atr_mult * atr
    max_height = config.max_zone_height_atr_mult * atr

    # 1) Zones: proximity + height guard (FVG/OB only) + rank + cap
    nearby_zones = []
    for z in snap.zones:
        mid = (z.high + z.low) / 2.0
        if abs(price - mid) > radius:
            continue
        # Retroactive height guard — FVG/OB only (P/D zones are naturally wide)
        if z.kind not in ("premium", "discount") and (z.high - z.low) > max_height:
            continue
        nearby_zones.append(z)
    nearby_zones.sort(key=_zone_rank)
    capped_zones = nearby_zones[:disp.max_display_zones]

    # 2) Levels: proximity filter disabled (ADR-0024b: show all key levels)
    # Трейдер хоче бачити всі рівні — UI стилізує per-kind, не потрібно фільтрувати.
    capped_levels = list(snap.levels)

    # 3) Swings: only last N
    capped_swings = snap.swings[-disp.max_display_swings:]

    return dataclasses.replace(
        snap,
        zones=capped_zones,
        levels=capped_levels,
        swings=capped_swings,
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
    # D1: zones that left display filter (proximity, cap) → tell UI to remove
    disappeared = [z.id for z in prev.zones if z.id not in curr_zone_ids]
    mitigated = mitigated + disappeared
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
