"""
core/smc/tda/stage2_h4_confirm.py — Stage 2: H4 Confirmation (ADR-0040 §2.3).

Algorithm:
  1. Compute cutoff = day_ms + h4_cutoff_hour_utc × 3600 × 1000
  2. Filter H4 bars with open_time_ms < cutoff
  3. Gate: need ≥ cfg.h4_min_bars (default 5)
  4. Take last cfg.h4_confirm_bars (default 10) → compute range
  5. BULL: close > midpoint OR last3 trending up → confirmed
  6. BEAR: close < midpoint OR last3 trending down → confirmed
  7. Otherwise → not confirmed

Інваріанти:
  S0: pure, NO I/O
  S2: deterministic — same bars → same output
  S5: thresholds from TdaCascadeConfig
"""

from __future__ import annotations

from typing import List

from core.model.bars import CandleBar
from core.smc.tda.types import H4ConfirmResult, TdaCascadeConfig

_MS_PER_HOUR = 3_600_000


def h4_confirmed(
    h4_bars: List[CandleBar],
    macro_dir: str,
    day_ms: int,
    cfg: TdaCascadeConfig,
) -> H4ConfirmResult:
    """Stage 2: Check H4 directional alignment with D1 macro.

    Args:
        h4_bars: All available H4 bars (any completeness).
        macro_dir: "BULL" or "BEAR" from Stage 1.
        day_ms: Epoch ms of current trading day at 00:00 UTC.
        cfg: TDA cascade config (thresholds).

    Returns:
        H4ConfirmResult with confirmed flag and diagnostics.
    """
    cutoff = day_ms + cfg.h4_cutoff_hour_utc * _MS_PER_HOUR
    past = [b for b in h4_bars if b.open_time_ms < cutoff]

    if len(past) < cfg.h4_min_bars:
        return H4ConfirmResult(
            confirmed=False,
            close_price=0.0,
            midpoint=0.0,
            h4_bar_count=len(past),
            reason="insufficient_bars",
        )

    recent = past[-cfg.h4_confirm_bars :]
    closes = [b.c for b in recent]
    highest = max(b.h for b in recent)
    lowest = min(b.low for b in recent)
    cur = recent[-1].c
    midpt = (highest + lowest) / 2.0
    last3 = closes[-3:]

    bar_count = len(recent)

    if macro_dir == "BULL":
        above_mid = cur > midpt
        trending_up = last3[-1] >= last3[0]
        confirmed = above_mid or trending_up
        reason = "above_midpoint" if above_mid else ("trending" if trending_up else "not_confirmed")
        return H4ConfirmResult(confirmed, cur, midpt, bar_count, reason)

    if macro_dir == "BEAR":
        below_mid = cur < midpt
        trending_dn = last3[-1] <= last3[0]
        confirmed = below_mid or trending_dn
        reason = "below_midpoint" if below_mid else ("trending" if trending_dn else "not_confirmed")
        return H4ConfirmResult(confirmed, cur, midpt, bar_count, reason)

    # CFL or unknown direction → not confirmed
    return H4ConfirmResult(False, cur, midpt, bar_count, "not_confirmed")
