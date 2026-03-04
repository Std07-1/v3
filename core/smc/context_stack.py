"""
core/smc/context_stack.py — Context Stack zone selection (ADR-0024c §3.2).

Збирає зони з HTF snapshots для cross-TF ін'єкції на viewer chart.

Модель Context Stack:
  L1 (Institutional): 1 зона з D1/H4 — де "сидять інституції"
  L2 (Intraday):      0–1 зона з H1 — intraday reaction context
  L3 (Local):         таговує viewer-TF зони — entry refinement

Правило асиметрії: HTF → LTF тільки. LTF зони не піднімаються вгору.

S0: pure logic, NO I/O.
S2: deterministic — same snapshots + price → same result.
S5: параметри з caller (engine передає з config).

Python 3.7 compatible.
"""
from __future__ import annotations

import dataclasses
import logging

_log = logging.getLogger(__name__)

# ── TF weights for ranking (D1 > H4 > H1) ──
_TF_WEIGHTS = {
    86400: 1.0,   # D1
    14400: 0.7,   # H4
    3600:  0.5,   # H1
}  # type: Dict[int, float]

# ── Which TFs contribute to which layer ──
_L1_TFS = [86400, 14400]   # Institutional: D1, H4 (D1 пріоритет)
_L2_TFS = [3600]            # Intraday: H1

# Зони які НЕ є POI (P/D — це фон, не entry)
_BACKGROUND_KINDS = frozenset({"premium", "discount"})


def _zone_mid(z):
    # type: (SmcZone) -> float
    return (z.high + z.low) / 2.0


def _proximity_score(zone_mid, price, atr):
    # type: (float, float, float) -> float
    """0.0 (далеко) .. 1.0 (на ціні). Price-relative для cross-TF ranking.

    ATR-relative не підходить для Context Stack: viewer M1 ATR=2.37
    робить всі HTF зони однаково "далекими". Price-relative (%)
    природно масштабується: 5172/5274 = 1.9% → 0.61 score.
    """
    if price <= 0:
        return 0.0
    dist_pct = abs(zone_mid - price) / price * 100.0  # у відсотках
    # Лінійна шкала: 0% = 1.0, 5% = 0.0
    return max(0.0, 1.0 - dist_pct / 5.0)


def _price_overlap_pct(z1, z2):
    # type: (SmcZone, SmcZone) -> float
    """% overlap по ціні між двома зонами (0.0–1.0 відносно меншої)."""
    overlap_low = max(z1.low, z2.low)
    overlap_high = min(z1.high, z2.high)
    if overlap_high <= overlap_low:
        return 0.0
    overlap_range = overlap_high - overlap_low
    min_range = min(z1.high - z1.low, z2.high - z2.low)
    if min_range <= 0:
        return 0.0
    return overlap_range / min_range


def _select_poi_zones(
    snapshot,      # type: Optional[SmcSnapshot]
    price,         # type: float
    atr,           # type: float
    radius,        # type: float
):
    # type: (...) -> List[SmcZone]
    """Повертає active/tested POI зони (без P/D) з HTF snapshot.

    Proximity gate ВИДАЛЕНО (ADR-0024c fix): HTF зони вже пройшли
    _filter_for_display з їхнім власним ATR. Повторний фільтр з viewer ATR
    хибно відсіює зони (H4 OB at dist=95pt killed by M15 radius=43.9pt).
    Ranking score (_proximity_score) все ще надає перевагу ближчим зонам.
    Budget caps (1+1) обмежують кількість.
    """
    if snapshot is None:
        return []
    result = []
    for z in snapshot.zones:
        # Тільки POI (OB/FVG), не background (premium/discount)
        if z.kind in _BACKGROUND_KINDS:
            continue
        # Тільки active/tested/partially_filled (живі зони)
        if z.status not in ("active", "tested", "partially_filled"):
            continue
        result.append(z)
    return result


def collect_htf_zones(
    get_snapshot_fn,    # type: Callable[[str, int], Optional[SmcSnapshot]]
    symbol,             # type: str
    viewer_tf_s,        # type: int
    last_price,         # type: float
    atr,                # type: float
    proximity_atr_mult=5.0,  # type: float
    institutional_budget=1,  # type: int
    intraday_budget=1,       # type: int
):
    # type: (...) -> List[SmcZone]
    """Context Stack: збирає L1 + L2 зони з HTF snapshots.

    L3 (local) зони вже є в viewer snapshot — таговуються окремо caller-ом.

    Args:
        get_snapshot_fn: (symbol, tf_s) → SmcSnapshot | None
        symbol: торговий символ
        viewer_tf_s: TF chart-у що відображається
        last_price: поточна ціна (для proximity ranking)
        atr: ATR viewer TF (для proximity radius)
        proximity_atr_mult: радіус в ATR (default 5.0)
        institutional_budget: max зон з D1/H4 (default 1)
        intraday_budget: max зон з H1 (default 1)

    Returns:
        Список SmcZone з context_layer='institutional'|'intraday'.
    """
    radius = proximity_atr_mult * atr
    if radius <= 0:
        return []

    result = []  # type: List[SmcZone]

    # ── L1: Institutional Anchor (D1 + H4) ──
    l1_pool = []  # type: List[Tuple[SmcZone, float]]
    for htf_s in _L1_TFS:
        if htf_s <= viewer_tf_s:
            continue  # Тільки ВИЩІ TF (HTF→LTF, не навпаки)
        snap = get_snapshot_fn(symbol, htf_s)
        zones = _select_poi_zones(snap, last_price, atr, radius)
        tf_w = _TF_WEIGHTS.get(htf_s, 0.3)
        for z in zones:
            mid = _zone_mid(z)
            prox = _proximity_score(mid, last_price, atr)
            # Ranking: tf_weight × 2 + strength + proximity
            score = tf_w * 2.0 + z.strength + prox
            l1_pool.append((z, score))

    l1_pool.sort(key=lambda t: -t[1])
    l1_selected = []  # type: List[SmcZone]
    for z, _score in l1_pool[:institutional_budget]:
        tagged = dataclasses.replace(z, context_layer="institutional")
        l1_selected.append(tagged)
        result.append(tagged)

    # ── L2: Intraday Context (H1) ──
    l2_pool = []  # type: List[Tuple[SmcZone, float]]
    for htf_s in _L2_TFS:
        if htf_s <= viewer_tf_s:
            continue
        snap = get_snapshot_fn(symbol, htf_s)
        zones = _select_poi_zones(snap, last_price, atr, radius)
        for z in zones:
            # Dedup: skip if >50% overlap with any L1 zone
            skip = False
            for l1z in l1_selected:
                if _price_overlap_pct(z, l1z) > 0.5:
                    skip = True
                    break
            if skip:
                continue
            mid = _zone_mid(z)
            prox = _proximity_score(mid, last_price, atr)
            score = z.strength * 0.6 + prox * 0.4
            l2_pool.append((z, score))

    l2_pool.sort(key=lambda t: -t[1])
    for z, _score in l2_pool[:intraday_budget]:
        tagged = dataclasses.replace(z, context_layer="intraday")
        result.append(tagged)

    return result


def tag_local_zones(zones):
    # type: (List[SmcZone]) -> List[SmcZone]
    """Таговує viewer-TF зони як 'local' (L3).

    P/D зони не таговуються — вони background, не частина Context Stack.
    """
    result = []
    for z in zones:
        if z.kind in _BACKGROUND_KINDS:
            result.append(z)  # P/D — без тегу
        else:
            result.append(dataclasses.replace(z, context_layer="local"))
    return result
