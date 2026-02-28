"""
core/smc/fvg.py — Fair Value Gap detection (ADR-0024 §4.4).

FVG = цінова неефективність — 3-свічковий патерн де між свічкою 1 і свічкою 3 є gap.

S0: pure logic, NO I/O.
S2: deterministic.
S5: пороги з SmcConfig.fvg.
"""
from __future__ import annotations

import dataclasses
from typing import List

from core.model.bars import CandleBar
from core.smc.config import SmcConfig
from core.smc.swings import compute_atr
from core.smc.types import SmcZone, make_zone_id


def detect_fvg(
    bars: List[CandleBar],
    config: SmcConfig,
) -> List[SmcZone]:
    """Виявляє Fair Value Gaps (3-свічковий патерн, ADR §4.4).

    Bullish FVG: bars[i].h < bars[i+2].low  → gap між верхом свічки 0 і низом свічки 2.
    Bearish FVG: bars[i].low > bars[i+2].h  → gap між низом свічки 0 і верхом свічки 2.

    Умова відфільтрації: gap_size >= min_gap_atr_mult × ATR (S5).

    Lifecycle:
      active          → FVG не заповнений
      partially_filled → ціна увійшла в зону але не покрила повністю
      filled          → ціна повністю закрила FVG
    """
    fvg_cfg = config.fvg
    if not fvg_cfg.enabled or len(bars) < 3:
        return []

    atr = compute_atr(bars, period=14)
    min_gap = fvg_cfg.min_gap_atr_mult * atr
    zones: List[SmcZone] = []
    active_count = 0

    for i in range(len(bars) - 2):
        b0, b1, b2 = bars[i], bars[i + 1], bars[i + 2]

        # Bullish FVG: b0.high < b2.low
        if b0.h < b2.low:
            gap_size = b2.low - b0.h
            if gap_size >= min_gap and active_count < fvg_cfg.max_active:
                zone_id = make_zone_id("fvg_bull", b1.symbol, b1.tf_s, b1.open_time_ms)
                if not any(z.id == zone_id for z in zones):
                    strength = min(1.0, gap_size / (atr * 2.0)) if atr > 0 else 0.5
                    zones.append(SmcZone(
                        id=zone_id,
                        symbol=b1.symbol,
                        tf_s=b1.tf_s,
                        kind="fvg_bull",
                        start_ms=b0.open_time_ms,
                        end_ms=None,
                        high=b2.low,   # top edge = b2.low
                        low=b0.h,      # bottom edge = b0.high
                        status="active",
                        strength=round(strength, 3),
                        anchor_bar_ms=b1.open_time_ms,
                    ))
                    active_count += 1

        # Bearish FVG: b0.low > b2.high
        elif b0.low > b2.h:
            gap_size = b0.low - b2.h
            if gap_size >= min_gap and active_count < fvg_cfg.max_active:
                zone_id = make_zone_id("fvg_bear", b1.symbol, b1.tf_s, b1.open_time_ms)
                if not any(z.id == zone_id for z in zones):
                    strength = min(1.0, gap_size / (atr * 2.0)) if atr > 0 else 0.5
                    zones.append(SmcZone(
                        id=zone_id,
                        symbol=b1.symbol,
                        tf_s=b1.tf_s,
                        kind="fvg_bear",
                        start_ms=b0.open_time_ms,
                        end_ms=None,
                        high=b0.low,   # top edge = b0.low
                        low=b2.h,      # bottom edge = b2.high
                        status="active",
                        strength=round(strength, 3),
                        anchor_bar_ms=b1.open_time_ms,
                    ))
                    active_count += 1

    # Оновлюємо статус (fill check)
    zones = _update_fvg_status(zones, bars)

    # Повертаємо тільки не-filled зони або всі (для history)
    return [z for z in zones if z.status != "filled"]


def _update_fvg_status(zones: List[SmcZone], bars: List[CandleBar]) -> List[SmcZone]:
    """Оновлює lifecycle статус FVG зон.

    active → partially_filled: ціна УВІЙШЛА в зону
    partially_filled → filled:  ціна ЗАКРИЛАСЬ за протилежну межу
    active → filled:            ціна одразу пробила повністю
    """
    updated = []
    for zone in zones:
        current_status = zone.status
        current_end_ms = zone.end_ms

        for bar in bars:
            if bar.open_time_ms <= zone.anchor_bar_ms:
                continue
            if not bar.complete:
                continue

            if zone.kind == "fvg_bull":
                enters = bar.low <= zone.high and bar.h >= zone.low
                fills  = bar.c <= zone.low   # close нижче нижньої межі = fill from below

                if fills and current_status in ("active", "partially_filled"):
                    current_status = "filled"
                    current_end_ms = bar.open_time_ms
                    break
                elif enters and current_status == "active":
                    current_status = "partially_filled"

            else:  # fvg_bear
                enters = bar.h >= zone.low and bar.low <= zone.high
                fills  = bar.c >= zone.high  # close вище верхньої межі

                if fills and current_status in ("active", "partially_filled"):
                    current_status = "filled"
                    current_end_ms = bar.open_time_ms
                    break
                elif enters and current_status == "active":
                    current_status = "partially_filled"

        updated.append(dataclasses.replace(
            zone, status=current_status, end_ms=current_end_ms,
        ))

    return updated
