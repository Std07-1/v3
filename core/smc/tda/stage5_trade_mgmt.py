"""
core/smc/tda/stage5_trade_mgmt.py — Trade Management Config F (ADR-0040 §4.5).

Pure per-bar trade state machine implementing Config F:
  1. MFE ≥ 1R → close 50% (lock partial_r = 0.5R), trail_sl → entry (breakeven)
  2. MFE ≥ 2R → trail_sl = entry + (MFE - 1R), tightens monotonically
  3. Exit: original SL / trail SL / TP / timeout

Порядок операцій на кожному барі (відповідає simulation):
  update max_fav → partial trigger → trail update →
  original SL check → trail SL check → TP check → timeout check

Інваріанти:
  S0: pure logic, NO I/O
  S2: deterministic — same state + bar → same result
  S5: all thresholds from TdaCascadeConfig
"""

from __future__ import annotations

from core.model.bars import CandleBar
from core.smc.tda.types import FvgEntry, TradeState, TdaCascadeConfig


def update_trade(
    state: TradeState,
    bar: CandleBar,
    entry: FvgEntry,
    cfg: TdaCascadeConfig,
) -> TradeState:
    """Advance trade state by one M15 bar (Config F management).

    Pure function: same inputs → same output (S2).
    Call once per M15 bar after entry.
    """
    if state.status == "closed":
        return state

    ep = entry.entry_price
    risk = entry.risk_pts
    is_long = entry.direction == "LONG"

    # ── Step 1: compute favorable excursion, update MFE ──
    fav = (bar.h - ep) if is_long else (ep - bar.low)
    max_fav = max(state.max_favorable, fav)
    bars = state.bars_elapsed + 1

    # ── Step 2: partial close trigger (MFE ≥ partial_tp_at_r × risk) ──
    partial_closed = state.partial_closed
    partial_r = state.partial_r
    trail_sl = state.trail_sl

    if (
        not partial_closed
        and cfg.partial_tp_enabled
        and max_fav >= risk * cfg.partial_tp_at_r
    ):
        partial_closed = True
        partial_r = cfg.partial_tp_pct * cfg.partial_tp_at_r  # 0.5 × 1.0 = 0.5R
        trail_sl = ep  # breakeven

    # ── Step 3: trail SL update (MFE ≥ trail_start_r × risk) ──
    if partial_closed and max_fav >= risk * cfg.trail_start_r:
        if is_long:
            new_trail = ep + (max_fav - risk * cfg.trail_behind_r)
            trail_sl = max(trail_sl, new_trail)
        else:
            new_trail = ep - (max_fav - risk * cfg.trail_behind_r)
            trail_sl = min(trail_sl, new_trail)

    # ── Step 4: original SL check (only before partial close) ──
    if not partial_closed:
        sl_hit = (is_long and bar.low <= entry.stop_loss) or (
            not is_long and bar.h >= entry.stop_loss
        )
        if sl_hit:
            return TradeState(
                status="closed",
                partial_closed=False,
                partial_r=0.0,
                max_favorable=max_fav,
                trail_sl=trail_sl,
                bars_elapsed=bars,
                outcome="LOSS",
                net_r=-1.0,
            )

    # ── Step 5: trail SL hit check ──
    if partial_closed:
        trail_hit = (is_long and bar.low <= trail_sl) or (
            not is_long and bar.h >= trail_sl
        )
        if trail_hit:
            remaining = 1.0 - cfg.partial_tp_pct
            if is_long:
                captured = (trail_sl - ep) / risk
            else:
                captured = (ep - trail_sl) / risk
            trail_r = remaining * max(captured, 0.0)
            net = partial_r + trail_r
            outcome = "BE" if net <= 0.01 else "PARTIAL_WIN"
            return TradeState(
                status="closed",
                partial_closed=True,
                partial_r=partial_r,
                max_favorable=max_fav,
                trail_sl=trail_sl,
                bars_elapsed=bars,
                outcome=outcome,
                net_r=0.0 if outcome == "BE" else net,
            )

    # ── Step 6: TP hit check ──
    tp_hit = (is_long and bar.h >= entry.take_profit) or (
        not is_long and bar.low <= entry.take_profit
    )
    if tp_hit:
        if partial_closed:
            remaining = 1.0 - cfg.partial_tp_pct
            trail_r = remaining * cfg.rr_target
            net = partial_r + trail_r  # 0.5 + 1.5 = 2.0R
        else:
            net = cfg.rr_target  # 3.0R
        return TradeState(
            status="closed",
            partial_closed=partial_closed,
            partial_r=partial_r,
            max_favorable=max_fav,
            trail_sl=trail_sl,
            bars_elapsed=bars,
            outcome="WIN",
            net_r=net,
        )

    # ── Step 7: timeout check ──
    if bars >= cfg.max_open_bars_m15:
        if partial_closed:
            remaining = 1.0 - cfg.partial_tp_pct
            if is_long:
                open_r = remaining * max((bar.c - ep) / risk, 0.0)
            else:
                open_r = remaining * max((ep - bar.c) / risk, 0.0)
            net = partial_r + open_r
        else:
            net = 0.0
        return TradeState(
            status="closed",
            partial_closed=partial_closed,
            partial_r=partial_r,
            max_favorable=max_fav,
            trail_sl=trail_sl,
            bars_elapsed=bars,
            outcome="TIMEOUT",
            net_r=net,
        )

    # ── Still open ──
    new_status = "partial" if partial_closed else "open"
    return TradeState(
        status=new_status,
        partial_closed=partial_closed,
        partial_r=partial_r,
        max_favorable=max_fav,
        trail_sl=trail_sl,
        bars_elapsed=bars,
        outcome="",
        net_r=0.0,
    )
