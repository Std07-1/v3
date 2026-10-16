"""
core/smc/swings.py — Fractal pivot swing detection (ADR-0024 §4.1).

S0: pure logic, NO I/O.
S2: deterministic — same bars → same swings.
"""
from __future__ import annotations

from typing import List, Optional

from core.model.bars import CandleBar
from core.smc.types import SmcSwing, make_swing_id


def detect_fractals(
    bars: List[CandleBar],
    period: int = 2,
) -> List[SmcSwing]:
    """Williams Fractal detection (display-only markers).

    Same algorithm as detect_raw_swings but with kind='fractal_high'/'fractal_low'.
    Separate from structure swings to avoid polluting BOS/CHoCH chain.
    """
    n = len(bars)
    if n < 2 * period + 1:
        return []
    result = []  # type: List[SmcSwing]
    for i in range(period, n - period):
        b = bars[i]
        left_h = [bars[j].h for j in range(i - period, i)]
        right_h = [bars[j].h for j in range(i + 1, i + period + 1)]
        if b.h > max(left_h) and b.h >= max(right_h):
            result.append(SmcSwing(
                id=make_swing_id("fh", b.symbol, b.tf_s, b.open_time_ms),
                symbol=b.symbol, tf_s=b.tf_s, kind="fractal_high",
                price=b.h, time_ms=b.open_time_ms, confirmed=True,
            ))
        left_l = [bars[j].low for j in range(i - period, i)]
        right_l = [bars[j].low for j in range(i + 1, i + period + 1)]
        if b.low < min(left_l) and b.low <= min(right_l):
            result.append(SmcSwing(
                id=make_swing_id("fl", b.symbol, b.tf_s, b.open_time_ms),
                symbol=b.symbol, tf_s=b.tf_s, kind="fractal_low",
                price=b.low, time_ms=b.open_time_ms, confirmed=True,
            ))
    result.sort(key=lambda s: s.time_ms)
    return result


def detect_raw_swings(
    bars: List[CandleBar],
    period: int = 5,
) -> List[SmcSwing]:
    """Fractal pivot detection (E1 foundation, ADR §4.1).

    Swing High: bars[i].h > max(bars[i-p:i].h)  AND  bars[i].h >= max(bars[i+1:i+p+1].h)
    Swing Low:  bars[i].low < min(bars[i-p:i].low) AND bars[i].low <= min(bars[i+1:i+p+1].low)

    Повертає тільки підтверджені свінги (потрібно 2*period+1 барів).
    Останні `period` барів — unconfirmed (майбутнє ще не відоме).
    """
    n = len(bars)
    if n < 2 * period + 1:
        return []

    swings: List[SmcSwing] = []
    # Confirmed range: period .. n-period-1 (включно)
    for i in range(period, n - period):
        b = bars[i]

        left_h  = [bars[j].h   for j in range(i - period, i)]
        right_h = [bars[j].h   for j in range(i + 1, i + period + 1)]
        if b.h > max(left_h) and b.h >= max(right_h):
            swings.append(SmcSwing(
                id=make_swing_id("sh", b.symbol, b.tf_s, b.open_time_ms),
                symbol=b.symbol,
                tf_s=b.tf_s,
                kind="hh",       # буде переписано в structure.py на HH/LH
                price=b.h,
                time_ms=b.open_time_ms,
                confirmed=True,
            ))

        left_l  = [bars[j].low for j in range(i - period, i)]
        right_l = [bars[j].low for j in range(i + 1, i + period + 1)]
        if b.low < min(left_l) and b.low <= min(right_l):
            swings.append(SmcSwing(
                id=make_swing_id("sl", b.symbol, b.tf_s, b.open_time_ms),
                symbol=b.symbol,
                tf_s=b.tf_s,
                kind="ll",       # буде переписано в structure.py на LL/HL
                price=b.low,
                time_ms=b.open_time_ms,
                confirmed=True,
            ))

    # Сортуємо за часом (S2: deterministic order)
    swings.sort(key=lambda s: s.time_ms)
    return swings


def compute_atr(bars: List[CandleBar], period: int = 14) -> float:
    """Average True Range — helper для OB/FVG strength (S5: period з config).

    True Range враховує розрив від попереднього close — канонічна `ta.tr` TradingView, яка не залежить від
    джерела бару. На барах FXCM у режимі PREVIOUS_CLOSE (ADR-0100) H/L уже містять попередній close, тож
    `h − low` дав би те саме; але там, де open ≠ попередній close (бари епохи FIRST_TICK у SSOT, перебудовані
    з тіків, Binance), `h − low` губить геп — ATR виходив меншим за TV (H1 до −25% на гепі NAS100 13.09).
    """
    if not bars:
        return 1.0  # fallback, ніколи не вернути 0
    n = min(len(bars), period)
    total = 0.0
    for i in range(1, n + 1):
        b = bars[-i]
        if i < len(bars):
            prev_close = bars[-i - 1].c
            tr = max(b.h, prev_close) - min(b.low, prev_close)
        else:
            tr = b.h - b.low  # найстаріший бар ряду — попереднього close немає
        total += tr
    atr = total / n
    return atr if atr > 0.0 else 1.0  # rail: atr > 0


def rolling_atr(bars: List[CandleBar], period: int = 14) -> List[float]:
    """ATR на кожному барі ряду — `compute_atr(bars[:i + 1], period)` для кожного i, за один прохід (той самий порядок
    додавання, тож значення ті самі до біта).

    Значення бару залежить лише від нього й `period` попередніх, тож після закриття бару не змінюється, хоч би скільки
    барів прийшло потім (ADR-0113 E-08a); на перших `period` барах ряду — з того, що є, як у compute_atr.
    """
    true_ranges: List[float] = []
    atrs: List[float] = []
    for i, b in enumerate(bars):
        if i > 0:
            prev_close = bars[i - 1].c
            true_ranges.append(max(b.h, prev_close) - min(b.low, prev_close))
        else:
            true_ranges.append(b.h - b.low)  # найстаріший бар ряду — попереднього close немає
        recent = true_ranges[-period:]
        atr = sum(reversed(recent)) / len(recent)  # від новішого до старішого — як compute_atr
        atrs.append(atr if atr > 0.0 else 1.0)  # rail: atr > 0
    return atrs


def compute_rv(bars: List[CandleBar], period: int = 20) -> Optional[float]:
    """Relative Volume — last bar's volume / SMA(volume, period) of prior bars.

    ADR-0070 §Tier 1 + amendment: backend SSOT for RV. Shipped as
    `frame.rv` in ws_server, consumed by CommandRail (frontend MUST NOT
    re-derive — X28).

    None — RV не виміряно (ADR-0070 rev 3: нейтральні 1.0 HUD показував як виміряні 1.00x):
      - bars empty / fewer than period+1 bars
      - last bar volume null/zero (preview/forming)
      - prior window has fewer than period/2 valid volume samples
      - SMA collapses to <=0
    """
    if not bars or len(bars) < period + 1:
        return None
    last = bars[-1]
    last_v = getattr(last, "v", None)
    if last_v is None or last_v <= 0:
        return None
    # Prior window = `period` bars BEFORE the last one.
    prior = bars[-(period + 1):-1]
    valid: List[float] = []
    for b in prior:
        v = getattr(b, "v", None)
        if v is not None and v > 0:
            valid.append(float(v))
    if len(valid) < period // 2:
        return None
    sma = sum(valid) / len(valid)
    if sma <= 0:
        return None
    return float(last_v) / sma


def find_impulse_start(
    bars: List[CandleBar],
    end_idx: int,
    min_bars_back: int = 1,
    max_bars_back: int = 20,
) -> int:
    """Повертає індекс початку імпульсного руху що завершився на end_idx.

    Шукаємо точку де напрямок руху змінився — найпростіша евристика:
    попередній swing low (для bullish impulse) або swing high (для bearish).
    Якщо не знайдено — повертає max(0, end_idx - max_bars_back).
    """
    start = max(0, end_idx - max_bars_back)
    return start
