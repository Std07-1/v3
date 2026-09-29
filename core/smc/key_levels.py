"""
core/smc/key_levels.py — Key Price Levels per TF (ADR-0024b).

Обчислює горизонтальні цінові якорі для intraday стратегії:
  - Previous candle High/Low (PDH/PDL для D1, prev H4 H/L, prev H1 H/L)
  - Current candle running High/Low (HOD/LOD, H4H/H4L, H1H/H1L)

Генеруються тільки для TF ≥ H1 (D1, H4, H1).
M30/M15 key levels видалені: трейдер бачить ці свічки напряму → рівні
дублюють видиме і створюють шум.

Cross-TF display filter у engine.py::_KEY_LEVEL_ALLOW далі відсіює:
  M15 viewer: D1 + H4 + H1 prev only (не show h1_h/h1_l — змінюються занадто часто)
  H1 viewer:  D1 + H4
  H4 viewer:  D1
  D1 viewer:  нічого

S0: pure logic, NO I/O.
S2: deterministic — same bars → same levels.
S5: TF map — SSOT тут, не hardcoded в десяти місцях.

Python 3.7 compatible.
"""
from __future__ import annotations

from typing import Dict, List, NamedTuple, Optional, Tuple

from core.model.bars import CandleBar
from core.smc.types import (
    LEVEL_SIDE_HIGH,
    LEVEL_SIDE_LOW,
    LEVEL_SIDE_OPEN,
    LEVEL_STATE_FIXED,
    LEVEL_STATE_FORMING,
    LEVEL_TIER_ANCHOR,
    LEVEL_TIER_CONTEXT,
    LEVEL_TIER_SESSION,
    SmcLevel,
    level_period,
    make_level_id,
    make_level_key,
)


class _KeyLevelSeries(NamedTuple):
    """Ряд key levels одного TF: kinds для wire + контракт рівня ADR-0104 §3.2."""

    kinds: Tuple[str, str, str, str]  # (prev_high, prev_low, curr_high, curr_low)
    series: str  # частина key: d1 | h4 | h1
    family: str  # day | htf
    fixed_tier: int  # важливість завершеного H/L; рухомий — завжди LEVEL_TIER_CONTEXT


# ── TF → ряд key levels ──
# Тільки стратегічно значущі TF для intraday (M15+).
# M1/M3/M5 не генерують key levels (їх prev candle не є стратегічним якорем),
# але ВІДОБРАЖАЮТЬ HTF levels через cross-TF ін'єкцію.
# Only generate key levels for TFs ≥ H1.
# M30/M15 prev/curr H/L are redundant: the viewer sees those candles directly.
# Cross-TF display map in engine.py further filters which kinds appear per viewer.
_TF_KEY_LEVEL_MAP = {
    86400: _KeyLevelSeries(("pdh", "pdl", "dh", "dl"), "d1", "day", LEVEL_TIER_ANCHOR),  # D1: Previous/Current Day
    14400: _KeyLevelSeries(("p_h4_h", "p_h4_l", "h4_h", "h4_l"), "h4", "htf", LEVEL_TIER_CONTEXT),  # H4
    3600: _KeyLevelSeries(("p_h1_h", "p_h1_l", "h1_h", "h1_l"), "h1", "htf", LEVEL_TIER_CONTEXT),  # H1
}  # type: Dict[int, _KeyLevelSeries]

# Усі kinds, що генеруються цим модулем (для LEVEL_KINDS union)
KEY_LEVEL_KINDS = frozenset(
    kind
    for series in _TF_KEY_LEVEL_MAP.values()
    for kind in series.kinds
)

# HTF шари, з яких ін'єктуються рівні на нижчі ТФ (sorted desc)
_HTF_INJECT_ORDER = [86400, 14400, 3600]


def compute_key_levels(bars: List[CandleBar]) -> List[SmcLevel]:
    """Обчислює key levels з серії барів одного TF.

    Повертає 2-4 рівні:
      - prev candle H/L (тільки якщо є ≥1 completed bar)
      - current candle running H/L (останній бар, якщо відрізняється від prev)

    Args:
        bars: відсортовані бари одного (symbol, tf_s), oldest first.

    Returns:
        Список SmcLevel. Порожній якщо TF не має key level map або недостатньо даних.
    """
    if not bars:
        return []

    series = _TF_KEY_LEVEL_MAP.get(bars[0].tf_s)
    if series is None:
        return []
    prev_h_kind, prev_l_kind, curr_h_kind, curr_l_kind = series.kinds

    # Знайти останній completed бар
    completed = [b for b in bars if b.complete]
    if not completed:
        return []

    prev = completed[-1]  # Останній завершений бар
    last = bars[-1]       # Останній бар (може бути incomplete/preview)

    # ── Previous candle High/Low: період завершено ──
    levels = _candle_extremes(prev, series, prev_h_kind, prev_l_kind, LEVEL_STATE_FIXED, series.fixed_tier)

    # ── Current candle running High/Low ──
    # Показуємо тільки якщо поточний бар відрізняється від prev (новий); він ще рухається
    if last.open_time_ms != prev.open_time_ms:
        levels += _candle_extremes(last, series, curr_h_kind, curr_l_kind, LEVEL_STATE_FORMING, LEVEL_TIER_CONTEXT)

    return levels


def _candle_extremes(
    bar: CandleBar,
    series: _KeyLevelSeries,
    high_kind: str,
    low_kind: str,
    state: str,
    tier: int,
) -> List[SmcLevel]:
    """H/L однієї свічки ряду → [high, low]. key — від open бакета: рухомий DH і завершений PDH того самого дня
    мають один key (ADR-0104 §3.2)."""
    period = level_period(bar.open_time_ms)
    return [
        SmcLevel(
            id=make_level_id(kind, bar.symbol, bar.tf_s, price),
            symbol=bar.symbol,
            tf_s=bar.tf_s,
            kind=kind,
            price=price,
            time_ms=bar.open_time_ms,
            touches=1,
            key=make_level_key(series.series, side, bar.symbol, period),
            family=series.family,
            state=state,
            tier=tier,
        )
        for kind, side, price in ((high_kind, LEVEL_SIDE_HIGH, bar.h), (low_kind, LEVEL_SIDE_LOW, bar.low))
    ]


# ── ADR-0104 S3: тиждень і відкриття доби — з барів D1/M1 при показі (окремого TF W1 немає, ADR-0024b §3.7) ──

_DAY_MS = 86_400_000
# Межа торгової доби — 21:00/22:00 UTC (17:00 NY, літо/зима) або 00:00 UTC; через 3 год після межі вже триває дата
# нової доби, тож (межа + 3 год) → північ дає торгову дату бакета D1 за його open чи close попереднього
_TRADING_DATE_SHIFT_MS = 3 * 3_600_000
_EPOCH_WEEKDAY = 3  # 1970-01-01 — четвер (Пн = 0)
_WEEK_ID_TF_S = 7 * 86_400  # «TF» в id тижневих рівнів — щоб id не збігся з D1


def _trading_date_ms(boundary_ms: int) -> int:
    """Північ UTC торгової дати, що починається на межі доби `boundary_ms` (open бакета D1 або close попереднього)."""
    shifted = boundary_ms + _TRADING_DATE_SHIFT_MS
    return shifted - shifted % _DAY_MS


def _week_monday_ms(date_ms: int) -> int:
    """Понеділок торгового тижня дати: Пн–Пт — свій тиждень; Сб/Нд (огризки після закриття) — наступний."""
    weekday = (date_ms // _DAY_MS + _EPOCH_WEEKDAY) % 7
    if weekday >= 5:
        return date_ms + (7 - weekday) * _DAY_MS
    return date_ms - weekday * _DAY_MS


def compute_week_levels(d1_bars: List[CandleBar]) -> List[SmcLevel]:
    """PWH/PWL — H/L попереднього торгового тижня з завершених барів D1 (ADR-0104 S3).

    Поточний тиждень задає закриття останньої завершеної свічки D1 (межа доби, що триває); попередній — тиждень перед
    ним. Барів того тижня немає — рівнів немає: позаминулий тиждень не видається за минулий (§3.6).
    """
    completed = [b for b in d1_bars if b.complete]
    if not completed:
        return []
    previous_monday = _week_monday_ms(_trading_date_ms(completed[-1].close_time_ms)) - 7 * _DAY_MS
    week = [b for b in completed if _week_monday_ms(_trading_date_ms(b.open_time_ms)) == previous_monday]
    if not week:
        return []
    high_bar = max(week, key=lambda b: b.h)
    low_bar = min(week, key=lambda b: b.low)
    period = level_period(week[0].open_time_ms)
    return [
        SmcLevel(
            id=make_level_id(kind, bar.symbol, _WEEK_ID_TF_S, price),
            symbol=bar.symbol,
            tf_s=_WEEK_ID_TF_S,
            kind=kind,
            price=price,
            time_ms=bar.open_time_ms,  # лінія — від свічки D1, що дала екстремум тижня
            touches=1,
            key=make_level_key("w1", side, bar.symbol, period),
            family="week",
            state=LEVEL_STATE_FIXED,
            tier=LEVEL_TIER_ANCHOR,
        )
        for kind, side, bar, price in (("pwh", LEVEL_SIDE_HIGH, high_bar, high_bar.h),
                                       ("pwl", LEVEL_SIDE_LOW, low_bar, low_bar.low))
    ]


def compute_day_open(d1_bars: List[CandleBar], m1_bars: List[CandleBar]) -> List[SmcLevel]:
    """DO — open першого бару M1 поточної торгової доби, тобто першого після закриття останньої завершеної D1.

    Після вихідних це перший бар тижня (відкриття ринку), а не межа п'ятниці. Бару нової доби ще немає — рівня немає.
    """
    completed = [b for b in d1_bars if b.complete]
    if not completed:
        return []
    day_start_ms = completed[-1].close_time_ms
    first = min((b for b in m1_bars if b.open_time_ms >= day_start_ms), key=lambda b: b.open_time_ms, default=None)
    if first is None:
        return []
    return [
        SmcLevel(
            id=make_level_id("do", first.symbol, 86400, first.o),
            symbol=first.symbol,
            tf_s=86400,
            kind="do",
            price=first.o,
            time_ms=first.open_time_ms,
            touches=1,
            key=make_level_key("d1", LEVEL_SIDE_OPEN, first.symbol, level_period(first.open_time_ms)),
            family="open",
            state=LEVEL_STATE_FIXED,
            tier=LEVEL_TIER_SESSION,
        )
    ]


def compute_week_open(d1_bars: List[CandleBar], m1_bars: List[CandleBar]) -> List[SmcLevel]:
    """WO — open першої доби поточного торгового тижня (ADR-0104 S7).

    Перша доба тижня вже завершена — open її свічки D1 (буфер M1 тримає лише ~2 доби, до середи неділі в ньому вже
    немає); ще триває (понеділок) — open першого M1 після закриття останньої D1, як DO. period у key — понеділок тижня:
    key той самий, яким би шляхом не рахувався рівень (закріплення S5 переживе перехід понеділок → вівторок).
    """
    completed = [b for b in d1_bars if b.complete]
    if not completed:
        return []
    current_monday = _week_monday_ms(_trading_date_ms(completed[-1].close_time_ms))
    this_week = [b for b in completed if _week_monday_ms(_trading_date_ms(b.open_time_ms)) == current_monday]
    if this_week:
        price, time_ms = this_week[0].o, this_week[0].open_time_ms
    else:
        day_open = compute_day_open(completed, m1_bars)
        if not day_open:
            return []
        price, time_ms = day_open[0].price, day_open[0].time_ms
    symbol = completed[-1].symbol
    return [
        SmcLevel(
            id=make_level_id("wo", symbol, _WEEK_ID_TF_S, price),
            symbol=symbol,
            tf_s=_WEEK_ID_TF_S,
            kind="wo",
            price=price,
            time_ms=time_ms,
            touches=1,
            key=make_level_key("w1", LEVEL_SIDE_OPEN, symbol, level_period(current_monday)),
            family="open",
            state=LEVEL_STATE_FIXED,
            tier=LEVEL_TIER_SESSION,
        )
    ]


def collect_htf_levels(
    get_snapshot_fn,   # Callable[[str, int], Optional[SmcSnapshot]]
    symbol: str,
    viewing_tf_s: int,
) -> List[SmcLevel]:
    """Збирає key levels з ТФ вищих за viewing_tf_s.

    Використовується для cross-TF ін'єкції: коли трейдер дивиться на M15,
    він бачить D1/H4/H1 рівні як контекст.

    Args:
        get_snapshot_fn: функція (symbol, tf_s) → SmcSnapshot | None
        symbol: торговий символ
        viewing_tf_s: TF chart-у що відображається

    Returns:
        Список SmcLevel з усіх вищих TF. Без дублікатів по id.
    """
    seen_ids = set()  # type: set
    htf_levels = []   # type: List[SmcLevel]

    for htf_s in _HTF_INJECT_ORDER:
        if htf_s <= viewing_tf_s:
            continue  # Тільки ВИЩІ TF

        snap = get_snapshot_fn(symbol, htf_s)
        if snap is None:
            continue

        for lv in snap.levels:
            # Ін'єктуємо тільки key levels (не eq_highs/eq_lows з чужого TF)
            if lv.kind in KEY_LEVEL_KINDS and lv.id not in seen_ids:
                seen_ids.add(lv.id)
                htf_levels.append(lv)

    return htf_levels
