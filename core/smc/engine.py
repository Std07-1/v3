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
from typing import Deque, Dict, List, Optional, Tuple

from core.model.bars import CandleBar
from core.smc.confluence import score_zone_confluence
from core.smc.config import SmcConfig, SmcDisplayConfig
from core.smc.context_stack import collect_htf_zones, tag_local_zones
from core.smc.fvg import detect_fvg
from core.smc.inducement import detect_inducement
from core.smc.key_levels import compute_key_levels, collect_htf_levels
from core.smc.liquidity import detect_liquidity_levels
from core.smc.order_blocks import detect_order_blocks
from core.smc.premium_discount import compute_pd_state, detect_premium_discount
from core.smc.structure import classify_swings, detect_structure_events
from core.smc.momentum import detect_displacement, compute_momentum_score
from core.smc.swings import detect_raw_swings, detect_fractals
from core.smc.types import (
    PdState,
    SmcDelta,
    SmcLevel,
    SmcSnapshot,
    SmcSwing,
    SmcZone,
    SESSION_LEVEL_KINDS,
)

_log = logging.getLogger(__name__)

# ── Zone lifecycle constants & helpers (N1) ──────────────────────────

_BULL_ZONE_KINDS = frozenset({"ob_bull", "fvg_bull", "discount", "ifvg_bull"})
_BEAR_ZONE_KINDS = frozenset({"ob_bear", "fvg_bear", "premium", "ifvg_bear"})

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


# Q2: TF-aware decay multipliers — HTF zones live much longer.
# Maps tf_s → (decay_start_mult, decay_fast_mult, expire_bars).
# Default (LTF): decay_start_bars as-is, expire at 500.
# H4/D1: start decay 4×/10× later, expire at 2000/5000 bars.
_TF_DECAY_PROFILE = {
    14400: (4.0, 4.0, 2000),  # H4: 4× slower decay, expire ~333 days
    86400: (
        10.0,
        10.0,
        5000,
    ),  # D1: 10× slower decay, expire ~14 years (effectively never)
}  # type: Dict[int, tuple]


def _zone_rank(z):
    # type: (SmcZone) -> tuple
    """Deterministic sort key: strongest active first, oldest last."""
    return (-z.strength, _STATUS_RANK.get(z.status, 9), -z.anchor_bar_ms, z.id)


def _update_zone_lifecycle(
    fresh_zones,  # type: List[SmcZone]
    active_zones,  # type: Dict[str, SmcZone]
    last_bar,  # type: Optional[CandleBar]
    config,  # type: SmcConfig
    tf_s,  # type: int
    struct_events=None,  # type: Optional[List[SmcSwing]]
):
    # type: (...) -> List[SmcZone]
    """N1 zone lifecycle: merge → FVG evict → mitigate → decay → hide → cap.

    Мутує active_zones in-place (state persistence між on_bar).
    Повертає відфільтрований список зон для snapshot.

    D-01: deterministic cap via _zone_rank.
    D-02: FVG not in fresh_ids → evict (prevents resurrection).
    R-04: mitigation by bar.c (close), NOT by wick.
    ADR-0034 P1: struct_events → Breaker promotion для mitigated OBs.
    """
    fresh_ids = {z.id for z in fresh_zones}

    # 1) Merge fresh zones → active_zones (fresh wins)
    # ADR-0034 P0: IFVG zones — never overwrite existing (preserve lifecycle state)
    for z in fresh_zones:
        if z.kind.startswith("ifvg") and z.id in active_zones:
            continue
        active_zones[z.id] = z

    # 2) D-02: FVG eviction — якщо FVG зникає з fresh → drop
    # Виняток: filled FVG зберігаються для dimmed display на UI
    # ADR-0042 P3: grace period — не видаляємо FVG молодше fvg_grace_bars
    _bar_ms = tf_s * 1000 if tf_s > 0 else 1
    stale_fvg = [
        zid
        for zid, z in active_zones.items()
        if z.kind.startswith("fvg")
        and zid not in fresh_ids
        and z.status not in ("filled", "mitigated")
        and (
            last_bar is None  # no bar → старий алгоритм: видалити
            or (last_bar.open_time_ms - z.anchor_bar_ms) // _bar_ms
            >= config.fvg_grace_bars
        )
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
                    z,
                    status="mitigated",
                    end_ms=last_bar.open_time_ms,
                )
                continue

            # Age model: bars since creation
            bar_ms = tf_s * 1000
            if bar_ms > 0:
                age_bars = (last_bar.open_time_ms - z.anchor_bar_ms) // bar_ms
            else:
                age_bars = 0

            # Q2: TF-aware expire threshold
            decay_profile = _TF_DECAY_PROFILE.get(tf_s)
            expire_bars = decay_profile[2] if decay_profile else 500
            if age_bars > expire_bars:
                to_delete.append(zid)
                continue

            # Q2: TF-aware decay curve — HTF zones decay much slower
            start_mult = decay_profile[0] if decay_profile else 1.0
            fast_mult = decay_profile[1] if decay_profile else 1.0
            eff_decay_start = int(config.decay_start_bars * start_mult)
            eff_decay_fast = int(config.decay_fast_bars * fast_mult)

            if age_bars > eff_decay_start and z.strength > 0.15:
                if age_bars > eff_decay_fast:
                    factor = 0.92  # aggressive: ~0.15 after 20 bars
                else:
                    factor = 0.97  # gentle: ~0.54 after 20 bars
                decay = max(0.15, z.strength * factor)
                active_zones[zid] = dataclasses.replace(
                    z,
                    strength=round(decay, 3),
                )

        for zid in to_delete:
            del active_zones[zid]

        # ── step 3c: ADR-0034 P1 — Breaker promotion ──────────────
        # Mitigated OB + CHoCH in opposite direction → status="breaker".
        # Breaker = mitigated OB that now works in reverse (acts as new POI).
        tda = config.tda
        if tda.enabled and tda.breaker_enabled and struct_events:
            lookback_ms = tda.breaker_choch_lookback_bars * tf_s * 1000
            # Collect CHoCH events by direction
            choch_bull = [e for e in struct_events if e.kind == "choch_bull"]
            choch_bear = [e for e in struct_events if e.kind == "choch_bear"]
            for zid, z in list(active_zones.items()):
                if z.status != "mitigated":
                    continue
                if z.kind not in ("ob_bull", "ob_bear"):
                    continue
                if z.end_ms is None:
                    continue
                # ob_bull mitigated → need choch_bear after mitigation (confirms bear context)
                # ob_bear mitigated → need choch_bull after mitigation (confirms bull context)
                needed = choch_bear if z.kind == "ob_bull" else choch_bull
                cutoff = z.end_ms + lookback_ms
                has_choch = any(z.end_ms < e.time_ms <= cutoff for e in needed)
                if has_choch:
                    active_zones[zid] = dataclasses.replace(z, status="breaker")

        # ── step 3b: post-mitigation TTL (ADR-0028 Φ0) ────────────
        # Mitigated zones linger for `mitigated_ttl_bars` then get removed.
        # Anchor = zone.end_ms (set during mitigation at step 3, line ~117).
        # ADR-0034 P1: skip breaker zones — they persist until mitigated or expired.
        mitigated_ttl = config.display.mitigated_ttl_bars  # default 20
        bar_ms = tf_s * 1000
        if mitigated_ttl > 0 and bar_ms > 0:
            ttl_delete = []
            for zid, z in active_zones.items():
                if z.status == "mitigated" and z.end_ms is not None:
                    bars_since = (last_bar.open_time_ms - z.end_ms) // bar_ms
                    if bars_since > mitigated_ttl:
                        ttl_delete.append(zid)
            for zid in ttl_delete:
                del active_zones[zid]

    # 4) Hide mitigated (optional)
    # FVG filled/mitigated зберігаються для dimmed display на UI
    if config.hide_mitigated:
        result = [
            z
            for z in active_zones.values()
            if z.status != "mitigated"
            or z.kind.startswith("fvg")  # FVG: keep for dimmed render
        ]
    else:
        result = list(active_zones.values())

    # 5) D-01: deterministic cap (FVG capped separately in detect_fvg)
    fvg_result = [z for z in result if z.kind.startswith("fvg")]
    non_fvg = [z for z in result if not z.kind.startswith("fvg")]
    if len(non_fvg) > config.max_zones_per_tf:
        non_fvg.sort(key=_zone_rank)
        non_fvg = non_fvg[: config.max_zones_per_tf]

    return non_fvg + fvg_result


def _perf_ms() -> float:
    return time.time() * 1000.0


class _TfState:
    """Стан SMC для однієї пари (symbol, tf_s)."""

    __slots__ = (
        "_bars",
        "_last_snapshot",
        "_last_delta",
        "_lookback",
        "_active_zones",
    )

    def __init__(self, lookback: int) -> None:
        self._bars: Deque[CandleBar] = deque(maxlen=lookback)
        self._last_snapshot: Optional[SmcSnapshot] = None
        self._last_delta: Optional[SmcDelta] = None
        self._lookback = lookback
        self._active_zones: Dict[str, SmcZone] = {}

    def append(self, bar: CandleBar) -> None:
        """Додає бар, зберігаючи вікно lookback_bars.

        Dedup: якщо останній бар має той самий open_time_ms — замінює
        (final bar update від паралельних feed-шляхів).
        """
        if self._bars and self._bars[-1].open_time_ms == bar.open_time_ms:
            self._bars[-1] = bar
        else:
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
        self._zone_grades: Dict[Tuple[str, int], Dict[str, dict]] = {}  # ADR-0029
        # ADR-0035: session support
        self._session_windows = []  # type: list
        self._session_m1_bars: Dict[str, Deque[CandleBar]] = {}
        self._init_sessions()

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
        state._bars = deque(
            bars[-self._config.lookback_bars :], maxlen=self._config.lookback_bars
        )
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
                bar.symbol,
                bar.tf_s,
                elapsed,
                self._config.max_compute_ms,
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

    def get_atr(self, symbol: str, tf_s: int, period: int = 14) -> float:
        """ATR14 for (symbol, tf). Returns 1.0 fallback if no data."""
        state = self._states.get((symbol, tf_s))
        if state is None or not state.bars_list():
            return 1.0
        from core.smc.swings import compute_atr

        return compute_atr(state.bars_list(), period=period)

    def get_momentum_score(self, symbol: str, tf_s: int) -> Tuple[int, int]:
        """Momentum score (bull_count, bear_count) for given (symbol, tf)."""
        state = self._states.get((symbol, tf_s))
        if state is None or not state.bars_list():
            return (0, 0)
        bars = state.bars_list()
        from core.smc.swings import compute_atr

        atr = compute_atr(bars, period=14)
        mom = self._config.momentum
        return compute_momentum_score(
            bars,
            atr,
            min_body_atr=mom.min_body_atr_mult,
            max_wick_ratio=mom.max_wick_ratio,
            lookback=mom.lookback_bars,
        )

    def get_pd_state(self, symbol: str, tf_s: int) -> Optional["PdState"]:
        """ADR-0041: P/D position for (symbol, tf). Returns None if no data."""
        base_tf = self._VIEWER_TO_BASE.get(tf_s, tf_s)
        state = self._states.get((symbol, base_tf))
        if state is None or not state.bars_list():
            return None
        bars = state.bars_list()
        cfg = self._config.for_tf(tf_s)
        raw_swings = detect_raw_swings(bars, period=cfg.swing_period)
        classified = classify_swings(raw_swings)
        current_price = bars[-1].c
        return compute_pd_state(classified, current_price, cfg)

    # ── ADR-0035: Session support ───────────────────────────────

    def _init_sessions(self) -> None:
        """Ініціалізує session windows з config (S5: SSOT)."""
        scfg = self._config.sessions
        if scfg.enabled and scfg._definitions:
            from core.smc.sessions import load_session_windows

            self._session_windows = load_session_windows(scfg._definitions)

    def feed_m1_bar(self, bar: CandleBar) -> None:
        """Зберігає M1 бар для обчислення session H/L.

        Зберігаються тільки complete M1 бари, últimos ~2880 (2 дні).
        S0: pure — лише зберігає у internal deque.
        """
        if bar.tf_s != 60 or not bar.complete:
            return
        sym = bar.symbol
        if sym not in self._session_m1_bars:
            self._session_m1_bars[sym] = deque(maxlen=2880)  # 48h × 60 min
        self._session_m1_bars[sym].append(bar)

    def feed_m1_bars_bulk(self, symbol: str, bars: List[CandleBar]) -> None:
        """Bulk feed M1 bars (warmup). S0: pure."""
        if symbol not in self._session_m1_bars:
            self._session_m1_bars[symbol] = deque(maxlen=2880)
        q = self._session_m1_bars[symbol]
        for b in bars:
            if b.tf_s == 60 and b.complete:
                q.append(b)

    def get_session_levels(self, symbol: str, current_time_ms: int) -> List[SmcLevel]:
        """Обчислити session H/L levels. S0: pure, S2: deterministic."""
        if not self._session_windows or not self._config.sessions.enabled:
            return []
        m1_bars = self._session_m1_bars.get(symbol)
        if not m1_bars:
            return []
        from core.smc.sessions import compute_session_levels

        sorted_bars = sorted(list(m1_bars), key=lambda b: b.open_time_ms)
        levels, _states = compute_session_levels(
            sorted_bars,
            self._session_windows,
            current_time_ms,
            symbol,
            tf_s=86400,
        )
        return levels

    def get_session_states(self, symbol: str, current_time_ms: int):
        """Get session states for narrative. Returns list of SessionState."""
        if not self._session_windows or not self._config.sessions.enabled:
            return []
        m1_bars = self._session_m1_bars.get(symbol)
        if not m1_bars:
            return []
        from core.smc.sessions import compute_session_levels

        sorted_bars = sorted(list(m1_bars), key=lambda b: b.open_time_ms)
        _levels, states = compute_session_levels(
            sorted_bars,
            self._session_windows,
            current_time_ms,
            symbol,
            tf_s=86400,
        )
        return states

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

    def get_snapshot_with_context_stack(
        self,
        symbol: str,
        tf_s: int,
    ) -> SmcSnapshot:
        """Snapshot з ін'єкцією HTF levels + Context Stack зон (ADR-0024c §3.2).

        Об'єднує:
          - HTF key levels (ADR-0024b: D1/H4/H1 рівні)
          - Context Stack zones (ADR-0024c: L1 institutional + L2 intraday)
          - Local zones (tagged as 'local')
          - Premium/Discount (background, без тегу)

        S0: pure — читає _states dict (internal state), без I/O.
        """
        snap = self.get_snapshot(symbol, tf_s)

        # ── HTF key levels (ADR-0024b) ──
        htf_levels = collect_htf_levels(
            lambda s, t: self.get_snapshot(s, t),
            symbol,
            tf_s,
        )
        merged_levels = list(snap.levels)
        if htf_levels:
            merged_levels = merged_levels + htf_levels

        # ── Context Stack zones (ADR-0024c) ──
        state = self._states.get((symbol, tf_s))
        if state is not None and state.bars_list():
            bars = state.bars_list()
            last_bar = bars[-1]
            from core.smc.swings import compute_atr

            atr = compute_atr(bars, period=14)
            if atr > 0:
                cs_cfg = self._config.context_stack
                if cs_cfg.enabled:
                    htf_zones = collect_htf_zones(
                        lambda s, t: self.get_snapshot(s, t),
                        symbol,
                        tf_s,
                        last_bar.c,
                        atr,
                        proximity_atr_mult=self._config.display.proximity_atr_mult,
                        institutional_budget=cs_cfg.institutional_budget,
                        intraday_budget=cs_cfg.intraday_budget,
                    )
                    # Tag local zones + merge
                    local_zones = tag_local_zones(snap.zones)
                    merged_zones = htf_zones + local_zones
                else:
                    merged_zones = list(snap.zones)
            else:
                merged_zones = list(snap.zones)
        else:
            merged_zones = list(snap.zones)

        return dataclasses.replace(
            snap,
            zones=merged_zones,
            levels=merged_levels,
        )

    # ── Cross-TF display mapping (SSOT: compute_tfs config) ─────────

    # Viewer TF → base computed TF. Lower TFs map to nearest computed.
    # User rules: CHoCH/BOS on M15, H1, H4. FVG on M15, H1, H4, D1.
    _VIEWER_TO_BASE = {
        60: 300,
        180: 300,
        300: 300,  # M1/M3/M5 → M5
        900: 900,  # M15 → M15
        1800: 3600,
        3600: 3600,  # M30/H1 → H1
        14400: 14400,  # H4 → H4
        86400: 86400,  # D1 → D1
    }  # type: Dict[int, int]

    # CHoCH/BOS: show current_base + one higher TF
    _STRUCTURE_NEXT_TF = {
        300: 900,  # M5  → +M15
        900: 3600,  # M15 → +H1
        3600: 14400,  # H1  → +H4
    }  # type: Dict[int, int]

    # FVG: explicit per-base TF display mapping
    _FVG_DISPLAY_TFS = {
        300: [300, 900, 3600],  # M5  → M5+M15+H1
        900: [900, 3600, 14400],  # M15 → M15+H1+H4
        3600: [3600, 14400],  # H1  → H1+H4
        14400: [14400, 86400],  # H4  → H4+D1
        86400: [86400],  # D1  → D1
    }  # type: Dict[int, List[int]]

    # Structure (BOS/CHoCH) lives only on these TFs
    _STRUCTURE_TFS = frozenset({300, 900, 3600, 14400})

    # Key Level display map: base_tf → which HTF key level kinds to show.
    # Principle: show levels from TFs the trader CAN'T read on current zoom.
    #   M15 viewer: D1 (PDH/PDL + HOD/LOD) + H4 (prev+curr) + H1 (prev only)
    #   H1 viewer:  D1 (PDH/PDL + HOD/LOD) + H4 (prev+curr)
    #   H4 viewer:  D1 (PDH/PDL + HOD/LOD)
    #   D1 viewer:  nothing (candles visible)
    # Current-hour H/L (h1_h/h1_l) excluded: changes too often, just noise.
    # M30/M15 H/L: viewer sees those candles → never show.
    # ADR-0035 §3.4: session level kinds split
    _SESSION_ALL = frozenset(
        {
            "as_h",
            "as_l",
            "p_as_h",
            "p_as_l",
            "lon_h",
            "lon_l",
            "p_lon_h",
            "p_lon_l",
            "ny_h",
            "ny_l",
            "p_ny_h",
            "p_ny_l",
        }
    )
    _SESSION_PREV_ONLY = frozenset(
        {
            "p_as_h",
            "p_as_l",
            "p_lon_h",
            "p_lon_l",
            "p_ny_h",
            "p_ny_l",
        }
    )

    _KEY_LEVEL_ALLOW = {
        300: frozenset(
            {
                "pdh",
                "pdl",
                "dh",
                "dl",  # D1
                "p_h4_h",
                "p_h4_l",
                "h4_h",
                "h4_l",  # H4
                "p_h1_h",
                "p_h1_l",
                "h1_h",
                "h1_l",  # H1 prev+curr
            }
        )
        | _SESSION_ALL,
        900: frozenset(
            {
                "pdh",
                "pdl",
                "dh",
                "dl",  # D1
                "p_h4_h",
                "p_h4_l",
                "h4_h",
                "h4_l",  # H4
                "p_h1_h",
                "p_h1_l",  # H1 prev only
            }
        )
        | _SESSION_ALL,
        3600: frozenset(
            {
                "pdh",
                "pdl",
                "dh",
                "dl",  # D1
                "p_h4_h",
                "p_h4_l",
                "h4_h",
                "h4_l",  # H4
            }
        )
        | _SESSION_ALL,
        14400: frozenset(
            {
                "pdh",
                "pdl",
                "dh",
                "dl",  # D1
            }
        )
        | _SESSION_PREV_ONLY,
        86400: frozenset(),  # D1 viewer: candles visible, no key levels
    }  # type: Dict[int, frozenset]

    def get_display_snapshot(
        self,
        symbol: str,
        viewer_tf_s: int,
    ) -> SmcSnapshot:
        """Composite snapshot for viewer with cross-TF injection.

        Combines:
          - Base snapshot from mapped compute-TF
          - HTF CHoCH/BOS structure events (one TF higher)
          - HTF FVG zones per display mapping
          - OB zones via Context Stack (existing L1/L2 mechanism)
          - HTF key levels (existing)

        S0: pure — reads _states dict, no I/O.
        """
        # 1. Map viewer → base computed TF
        compute_tfs = self._config.compute_tfs
        base_tf = self._VIEWER_TO_BASE.get(viewer_tf_s)
        if base_tf is None:
            # Fallback: nearest computed TF >= viewer
            for t in sorted(compute_tfs):
                if t >= viewer_tf_s:
                    base_tf = t
                    break
            if base_tf is None:
                base_tf = max(compute_tfs) if compute_tfs else viewer_tf_s

        snap = self.get_snapshot(symbol, base_tf)

        # 2. Separate base swings: keep structure only if base is in STRUCTURE_TFS
        base_swings = list(snap.swings)
        if base_tf not in self._STRUCTURE_TFS:
            base_swings = [
                s
                for s in base_swings
                if not s.kind.startswith("bos_") and not s.kind.startswith("choch_")
            ]

        # 3. Inject structure events from ONE higher TF
        next_tf = self._STRUCTURE_NEXT_TF.get(base_tf)
        if next_tf:
            htf_snap = self.get_snapshot(symbol, next_tf)
            htf_structure = [
                s
                for s in htf_snap.swings
                if s.kind.startswith("bos_") or s.kind.startswith("choch_")
            ]
            base_swings = base_swings + htf_structure
            # A-1: Propagate HTF trend_bias when base TF has None
            if snap.trend_bias is None and htf_snap.trend_bias is not None:
                snap = dataclasses.replace(snap, trend_bias=htf_snap.trend_bias)

        base_swings.sort(key=lambda s: s.time_ms)

        # 4. Inject HTF FVG zones per display mapping
        fvg_tfs = self._FVG_DISPLAY_TFS.get(base_tf, [base_tf])
        extra_fvg = []  # type: List[SmcZone]
        for tf in fvg_tfs:
            if tf == base_tf:
                continue  # already in base snap
            htf_snap = self.get_snapshot(symbol, tf)
            for z in htf_snap.zones:
                if z.kind.startswith("fvg"):
                    extra_fvg.append(z)
        # A-3: Collect IDs from step-4 FVG injection for dedup with step 5
        _step4_zone_ids = frozenset(z.id for z in extra_fvg)  # type: frozenset
        base_zones = list(snap.zones) + extra_fvg

        # 5. Context Stack OB injection (existing L1/L2 from context_stack.py)
        state = self._states.get((symbol, base_tf))
        if state is not None and state.bars_list():
            bars = state.bars_list()
            last_bar = bars[-1]
            from core.smc.swings import compute_atr

            atr = compute_atr(bars, period=14)
            if atr > 0:
                cs_cfg = self._config.context_stack
                if cs_cfg.enabled:
                    htf_zones = collect_htf_zones(
                        lambda s, t: self.get_snapshot(s, t),
                        symbol,
                        base_tf,
                        last_bar.c,
                        atr,
                        proximity_atr_mult=self._config.display.proximity_atr_mult,
                        institutional_budget=cs_cfg.institutional_budget,
                        intraday_budget=cs_cfg.intraday_budget,
                    )
                    # A-3: Dedup — remove Context Stack zones already injected at step 4
                    htf_zones = [z for z in htf_zones if z.id not in _step4_zone_ids]
                    local_zones = tag_local_zones(base_zones)
                    base_zones = htf_zones + local_zones
                # else: keep base_zones as-is
            # else: keep base_zones as-is

        # 6. Key levels: curated display (only HTF levels trader can't read)
        # Base snap already has EQ levels (eq_highs/eq_lows) from base_tf.
        # Key levels (PDH/PDL etc) come from HTF snapshots, filtered by allow-set.
        allowed = self._KEY_LEVEL_ALLOW.get(base_tf, frozenset())
        merged_levels = []  # type: List[SmcLevel]
        # Keep EQ levels from base snapshot (already capped by max_levels config)
        for lv in snap.levels:
            if lv.kind in ("eq_highs", "eq_lows"):
                merged_levels.append(lv)
        # Inject allowed HTF key levels
        if allowed:
            seen_ids = set()  # type: set
            for htf_s in [86400, 14400, 3600]:  # D1 → H4 → H1
                if htf_s <= base_tf:
                    continue
                htf_snap = self.get_snapshot(symbol, htf_s)
                for lv in htf_snap.levels:
                    if lv.kind in allowed and lv.id not in seen_ids:
                        seen_ids.add(lv.id)
                        merged_levels.append(lv)

        # 6b. ADR-0035: inject session H/L levels (filtered by allowed set)
        if allowed and self._config.sessions.enabled:
            session_levels = self.get_session_levels(symbol, int(time.time() * 1000))
            for lv in session_levels:
                if lv.kind in allowed:
                    merged_levels.append(lv)

        # 7. Confluence scoring (ADR-0029 E5: after cross-TF injection)
        #    bars/last_bar/atr already computed in step 5 (same guard).
        zone_grades = {}  # type: Dict[str, dict]
        if state is not None and state.bars_list() and atr > 0:
            conf_cfg = self._config.confluence.to_scoring_dict()
            swing_dicts = [
                s.to_wire()
                for s in base_swings
                if not s.kind.startswith("bos_") and not s.kind.startswith("choch_")
            ]
            struct_dicts = [
                s.to_wire()
                for s in base_swings
                if s.kind.startswith("bos_") or s.kind.startswith("choch_")
            ]
            bar_dicts = [
                {"open_time_ms": b.open_time_ms, "h": b.h, "l": b.low}
                for b in bars[-50:]
            ]
            all_zone_wires = []
            for zz in base_zones:
                w = zz.to_wire()
                w["anchor_bar_ms"] = zz.anchor_bar_ms
                all_zone_wires.append(w)
            for z in base_zones:
                z_wire = z.to_wire()
                # anchor_bar_ms needed by scorer but not in UI wire (S6)
                z_wire["anchor_bar_ms"] = z.anchor_bar_ms
                if z_wire.get("kind", "").startswith("ob_"):
                    htf_ctx = []
                    for hz in base_zones:
                        if hz.tf_s > base_tf:
                            hw = hz.to_wire()
                            hw["anchor_bar_ms"] = hz.anchor_bar_ms
                            htf_ctx.append(hw)
                    # ADR-0035 F9: session level wires for confluence scoring
                    session_lv_wires = [
                        lv.to_wire()
                        for lv in merged_levels
                        if lv.kind in SESSION_LEVEL_KINDS
                    ]
                    result = score_zone_confluence(
                        zone=z_wire,
                        bars=bar_dicts,
                        swings=swing_dicts,
                        zones_all=all_zone_wires,
                        htf_zones=htf_ctx,
                        structure=struct_dicts,
                        atr=atr,
                        current_price=last_bar.c,
                        tf_s=base_tf,
                        config=conf_cfg,
                        session_levels=session_lv_wires,
                    )
                    zone_grades[z_wire["id"]] = result
        self._zone_grades[(symbol, viewer_tf_s)] = zone_grades

        return dataclasses.replace(
            snap,
            tf_s=viewer_tf_s,  # Tag snapshot with viewer TF for UI
            zones=base_zones,
            swings=base_swings,
            levels=merged_levels,
        )

    def get_zone_grades(self, symbol: str, tf_s: int) -> Dict[str, dict]:
        """ADR-0029: zone_grades для full frame wire payload."""
        return self._zone_grades.get((symbol, tf_s), {})

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

        # ── Per-TF config (S5: tf_overrides from SSOT) ──
        cfg = self._config.for_tf(tf_s)

        # ── F4: ATR once, pass to all detectors ──
        from core.smc.swings import compute_atr

        atr = compute_atr(bars, period=14)

        # ── E1.1: Raw swings ──
        raw_swings = detect_raw_swings(bars, period=cfg.swing_period)

        # ── E1.2: Classify + Structure events ──
        classified = classify_swings(raw_swings)
        struct_events, trend_bias, last_bos_ms, last_choch_ms = detect_structure_events(
            classified, bars, config=cfg.structure
        )

        all_swings = classified + struct_events
        all_swings.sort(key=lambda s: s.time_ms)

        # ── E1.3: Order Blocks ──
        ob_zones = detect_order_blocks(bars, struct_events, cfg, atr=atr)

        # ── E1.4: FVG ──
        fvg_zones = detect_fvg(bars, cfg, atr=atr)

        # ── E2.6: Premium/Discount Zones ──
        pd_zones: List[SmcZone] = detect_premium_discount(classified, bars, cfg)

        # ── N1: Zone lifecycle (merge, FVG evict, mitigate, decay, cap) ──
        fresh_zones: List[SmcZone] = ob_zones + fvg_zones + pd_zones
        last_bar = bars[-1] if bars else None
        all_zones = _update_zone_lifecycle(
            fresh_zones,
            state._active_zones,
            last_bar,
            cfg,
            tf_s,
            struct_events=struct_events,
        )

        # ── E2.5: Liquidity Levels (Equal Highs / Equal Lows) ──
        # Передаємо classified (hh/hl/lh/ll) — вони містять confirmed info
        levels: List[SmcLevel] = detect_liquidity_levels(classified, bars, cfg, atr=atr)

        # ── ADR-0024b: Key Levels per TF (PDH/PDL, H4H/L, H1H/L, ...) ──
        key_lvls = compute_key_levels(bars)
        levels = levels + key_lvls

        # ── E2.7: Inducement (False Breakout Trap) ──
        inducement_swings: List[SmcSwing] = detect_inducement(
            bars, classified, cfg, atr=atr
        )
        if inducement_swings:
            all_swings = all_swings + inducement_swings
            all_swings.sort(key=lambda s: s.time_ms)

        # ── Williams Fractals (display-only, separate from structure chain) ──
        fractal_swings = detect_fractals(bars, period=cfg.fractal_period)
        if fractal_swings:
            all_swings = all_swings + fractal_swings
            all_swings.sort(key=lambda s: s.time_ms)

        # ── Displacement candles (momentum markers) ──
        mom_cfg = cfg.momentum
        if mom_cfg.enabled:
            disp_swings = detect_displacement(
                bars,
                atr,
                min_body_atr=mom_cfg.min_body_atr_mult,
                max_wick_ratio=mom_cfg.max_wick_ratio,
            )
            if disp_swings:
                all_swings = all_swings + disp_swings
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
        snap = _filter_for_display(snap, bars, cfg, atr=atr)

        return snap


# ── D1: Display filter ───────────────────────────────────────────────


def _filter_for_display(
    snap: SmcSnapshot,
    bars: List[CandleBar],
    config: SmcConfig,
    atr: float = 0.0,  # F4: caller-supplied ATR
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

    disp: SmcDisplayConfig = config.display
    radius = disp.proximity_atr_mult * atr
    max_height = config.max_zone_height_atr_mult * atr

    # 0) ADR-0028 Φ0: min strength gate (decay floor 0.15 ≠ display threshold)
    min_str = disp.min_display_strength  # default 0.25
    eligible_zones = [z for z in snap.zones if z.strength >= min_str]

    # 1a) FVG zones: exclude filled, distance cap, then rank cap (ADR-0028 Φ0)
    fvg_cap = disp.fvg_display_cap  # default 4
    fvg_max_dist = disp.proximity_atr_mult * 1.5 * atr  # A-4: ~9 ATR distance cap

    # ADR-0033 SC-2: collect active OB ranges for FVG overlap check
    ob_ranges = []  # type: list
    if disp.fvg_ob_overlap_hide:
        for z in eligible_zones:
            if "ob" in z.kind and z.status not in ("mitigated", "filled"):
                ob_ranges.append((z.low, z.high))

    fvg_zones = []
    for z in eligible_zones:
        if not z.kind.startswith("fvg"):
            continue
        if z.status == "filled":  # A-2: filled = dead
            continue
        if (z.high - z.low) > max_height:  # height guard — same as OBs
            continue
        if abs(price - (z.high + z.low) / 2.0) > fvg_max_dist:  # A-4
            continue
        # ADR-0033 SC-2: hide FVG that overlaps any active OB (any overlap > 0)
        if disp.fvg_ob_overlap_hide:
            overlaps_ob = any(
                z.low < ob_h and z.high > ob_l for ob_l, ob_h in ob_ranges
            )
            if overlaps_ob:
                continue
        fvg_zones.append(z)

    fvg_zones.sort(key=_zone_rank)
    fvg_zones = fvg_zones[:fvg_cap]

    # 1b) Non-FVG zones: proximity + height guard + rank + cap
    nearby_zones = []
    for z in eligible_zones:
        if z.kind.startswith("fvg"):
            continue  # handled above
        mid = (z.high + z.low) / 2.0
        if abs(price - mid) > radius:
            continue
        # Retroactive height guard — OB only (P/D zones are naturally wide)
        if z.kind not in ("premium", "discount") and (z.high - z.low) > max_height:
            continue
        nearby_zones.append(z)
    nearby_zones.sort(key=_zone_rank)
    capped_zones = nearby_zones[: disp.max_display_zones] + fvg_zones

    # 2) Levels: proximity filter disabled (ADR-0024b: show all key levels)
    # Трейдер хоче бачити всі рівні — UI стилізує per-kind, не потрібно фільтрувати.
    capped_levels = list(snap.levels)

    # 3) Swings: only last N (structure swings + fractals capped separately)
    struct_swings = [
        s
        for s in snap.swings
        if not s.kind.startswith("fractal_") and not s.kind.startswith("displacement_")
    ]
    frac_swings = [s for s in snap.swings if s.kind.startswith("fractal_")]
    disp_swings = [s for s in snap.swings if s.kind.startswith("displacement_")]
    capped_swings = (
        struct_swings[-disp.max_display_swings :]
        + frac_swings[-disp.max_display_fractals :]
        + disp_swings[-config.momentum.max_display :]
    )
    capped_swings.sort(key=lambda s: s.time_ms)

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
        z.id
        for z in prev.zones
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

    prev_level_ids = {low.id: low for low in prev.levels}
    curr_level_ids = {low.id: low for low in curr.levels}
    new_levels = [low for low in curr.levels if low.id not in prev_level_ids]
    removed_levels = [low.id for low in prev.levels if low.id not in curr_level_ids]

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
