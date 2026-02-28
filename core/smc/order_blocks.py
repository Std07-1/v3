"""
core/smc/order_blocks.py — Order Block detection (ADR-0024 §4.3).

S0: pure logic, NO I/O.
S2: deterministic.
S5: thresholds з SmcConfig.ob (не hardcoded).
"""
from __future__ import annotations

import dataclasses
from typing import List, Optional

from core.model.bars import CandleBar
from core.smc.config import SmcConfig
from core.smc.swings import compute_atr
from core.smc.types import SmcZone, make_zone_id


def _is_bullish_candle(bar: CandleBar) -> bool:
    return bar.c >= bar.o


def _is_bearish_candle(bar: CandleBar) -> bool:
    return bar.c < bar.o


def detect_order_blocks(
    bars: List[CandleBar],
    structure_swings: List,  # SmcSwing list from structure.py
    config: SmcConfig,
) -> List[SmcZone]:
    """Виявляє Order Blocks на основі BOS/CHoCH подій (ADR §4.3).

    Bullish OB: остання bearish свічка перед bullish BOS/CHoCH.
    Bearish OB: остання bullish свічка перед bearish BOS/CHoCH.

    Lifecycle:
      active → ціна ще не торкалась зони
      tested → ціна увійшла в зону (bounce)
      mitigated → ціна close вийшла за протилежну межу зони

    Повертає список SmcZone, обмежений max_active_per_side.
    """
    if not bars or not structure_swings:
        return []

    ob_cfg = config.ob
    if not ob_cfg.enabled:
        return []

    atr = compute_atr(bars, ob_cfg.atr_period)
    bar_index = {b.open_time_ms: i for i, b in enumerate(bars)}

    zones: List[SmcZone] = []
    bull_count = 0
    bear_count = 0

    # Обробляємо BOS/CHoCH події для пошуку OB
    for swing in structure_swings:
        if swing.kind not in ("bos_bull", "choch_bull", "bos_bear", "choch_bear"):
            continue

        is_bullish_event = swing.kind in ("bos_bull", "choch_bull")

        # Знаходимо індекс бару де виникла подія
        event_idx = bar_index.get(swing.time_ms)
        if event_idx is None:
            # Знаходимо найближчий бар ПІСЛЯ event time
            event_idx = next(
                (i for i, b in enumerate(bars) if b.open_time_ms >= swing.time_ms),
                None,
            )
        if event_idx is None or event_idx < 2:
            continue

        # Вимір імпульсу: від anchor_bar назад до 20 барів
        impulse_end = event_idx
        impulse_start = max(0, impulse_end - 20)
        impulse_bars = bars[impulse_start:impulse_end]

        if not impulse_bars:
            continue

        impulse_range = max(b.h for b in impulse_bars) - min(b.low for b in impulse_bars)
        impulse_strength = impulse_range / atr if atr > 0 else 0.0

        # Фільтр слабких OB (S5: min_impulse_atr_mult з config)
        if impulse_strength < ob_cfg.min_impulse_atr_mult:
            continue

        # Обмеження кількості
        if is_bullish_event and bull_count >= ob_cfg.max_active_per_side:
            continue
        if not is_bullish_event and bear_count >= ob_cfg.max_active_per_side:
            continue

        # Шукаємо останню ПРОТИЛЕЖНУ свічку перед імпульсом
        ob_bar: Optional[CandleBar] = None
        for j in range(impulse_end - 1, impulse_start - 1, -1):
            b = bars[j]
            if is_bullish_event and _is_bearish_candle(b):
                ob_bar = b
                break
            elif not is_bullish_event and _is_bullish_candle(b):
                ob_bar = b
                break

        if ob_bar is None:
            continue  # не знайшли відповідну свічку

        kind = "ob_bull" if is_bullish_event else "ob_bear"
        zone_id = make_zone_id(kind, ob_bar.symbol, ob_bar.tf_s, ob_bar.open_time_ms)

        # Уникаємо дублікатів
        if any(z.id == zone_id for z in zones):
            continue

        strength = min(1.0, impulse_strength / (ob_cfg.min_impulse_atr_mult * 3))

        zone = SmcZone(
            id=zone_id,
            symbol=ob_bar.symbol,
            tf_s=ob_bar.tf_s,
            kind=kind,
            start_ms=ob_bar.open_time_ms,
            end_ms=None,         # активна до mitigation
            high=ob_bar.h,
            low=ob_bar.low,
            status="active",
            strength=round(strength, 3),
            anchor_bar_ms=ob_bar.open_time_ms,
        )
        zones.append(zone)

        if is_bullish_event:
            bull_count += 1
        else:
            bear_count += 1

    # Оновлюємо статус зон (mitigation check) на всьому датасеті барів
    zones = _update_ob_status(zones, bars)
    return zones


def _update_ob_status(zones: List[SmcZone], bars: List[CandleBar]) -> List[SmcZone]:
    """Оновлює lifecycle статус кожної OB зони на основі цінової дії.

    active  → tested:    ціна УВІЙШЛА в зону (не закрилась за межами)
    tested  → mitigated: ціна ЗАКРИЛАСЬ за протилежну межу зони
    active  → mitigated: ціна одразу пробила без bounce

    Повертає нові незмінні SmcZone з оновленим status.
    """
    updated = []
    for zone in zones:
        current_status = zone.status
        current_end_ms = zone.end_ms

        # Перевіряємо лише бари ПІСЛЯ утворення зони
        for bar in bars:
            if bar.open_time_ms <= zone.anchor_bar_ms:
                continue
            if not bar.complete:
                continue

            if zone.kind.endswith("bull"):
                # Bullish OB: ціна тестує знизу вгору
                bar_enters_zone = bar.low <= zone.high and bar.h >= zone.low
                bar_mitigates = bar.c < zone.low     # close нижче низу зони

                if bar_mitigates and current_status in ("active", "tested"):
                    current_status = "mitigated"
                    current_end_ms = bar.open_time_ms
                    break
                elif bar_enters_zone and current_status == "active":
                    current_status = "tested"

            else:
                # Bearish OB: ціна тестує зверху вниз
                bar_enters_zone = bar.h >= zone.low and bar.low <= zone.high
                bar_mitigates = bar.c > zone.high    # close вище верху зони

                if bar_mitigates and current_status in ("active", "tested"):
                    current_status = "mitigated"
                    current_end_ms = bar.open_time_ms
                    break
                elif bar_enters_zone and current_status == "active":
                    current_status = "tested"

        updated.append(dataclasses.replace(
            zone, status=current_status, end_ms=current_end_ms,
        ))

    return updated
