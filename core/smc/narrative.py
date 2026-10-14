"""
core/smc/narrative.py — Context Flow Narrative Engine (ADR-0033 Rev 2).

S0: pure logic, NO I/O.
N0: pure function, deterministic for same input.
N3: synthesize_narrative() НІКОЛИ не повертає None — fallback block з warnings.
N5: market_phase display-only, не впливає на mode.
N6: alignment = D1+H4 only.
"""

from __future__ import annotations

import logging

from typing import Dict, List, Optional, Tuple

from core.smc.sessions import OFF_SESSION
from core.smc.zone_path import LONG, ZonePath, zone_direction
from core.smc.types import (
    LEVEL_STATE_FORMING,
    ActiveScenario,
    NarrativeBlock,
    SmcLevel,
    SmcSnapshot,
    SmcSwing,
    SmcZone,
)

logger = logging.getLogger(__name__)

# ── Headlines (ADR-0033 §3.2) ──────────────────────────────

# Trigger-aware trade headlines: key = (mode, sub_mode, direction, trigger_state).
# sub_mode="aligned"  → full HTF agreement + inside killzone (or no session data).
# sub_mode="reduced"  → outside killzone; structure valid but timing window uncertain.
# Trigger states: "ready" > "triggered" > "in_zone" > "approaching" (IOFED, ADR-0033 T-2).
_TRADE_HEADLINES = {
    # ALIGNED — full HTF agreement, optimal timing window
    ("trade", "aligned", "long", "ready"): "\U0001f7e2 BUY \u2014 ГОТОВИЙ ДО ВХОДУ",
    (
        "trade",
        "aligned",
        "long",
        "triggered",
    ): "\U0001f7e2 BUY \u2014 тригер, шукаємо вхід",
    (
        "trade",
        "aligned",
        "long",
        "in_zone",
    ): "\U0001f7e1 BUY \u2014 у зоні, чекаємо CHoCH",
    (
        "trade",
        "aligned",
        "long",
        "approaching",
    ): "\U0001f7e1 BUY \u2014 наближення до зони",
    ("trade", "aligned", "short", "ready"): "\U0001f534 SELL \u2014 ГОТОВИЙ ДО ВХОДУ",
    (
        "trade",
        "aligned",
        "short",
        "triggered",
    ): "\U0001f534 SELL \u2014 тригер, шукаємо вхід",
    (
        "trade",
        "aligned",
        "short",
        "in_zone",
    ): "\U0001f7e1 SELL \u2014 у зоні, чекаємо CHoCH",
    (
        "trade",
        "aligned",
        "short",
        "approaching",
    ): "\U0001f7e1 SELL \u2014 наближення до зони",
    # REDUCED — outside killzone (valid structure, timing window not ideal)
    ("trade", "reduced", "long", "ready"): "\U0001f7e2 BUY \u2014 готовий (поза KZ)",
    ("trade", "reduced", "long", "triggered"): "\U0001f7e1 BUY \u2014 тригер (поза KZ)",
    ("trade", "reduced", "long", "in_zone"): "\U0001f7e1 BUY \u2014 у зоні (поза KZ)",
    (
        "trade",
        "reduced",
        "long",
        "approaching",
    ): "\U0001f7e1 BUY \u2014 підхід (поза KZ)",
    ("trade", "reduced", "short", "ready"): "\U0001f534 SELL \u2014 готовий (поза KZ)",
    (
        "trade",
        "reduced",
        "short",
        "triggered",
    ): "\U0001f7e1 SELL \u2014 тригер (поза KZ)",
    ("trade", "reduced", "short", "in_zone"): "\U0001f7e1 SELL \u2014 у зоні (поза KZ)",
    (
        "trade",
        "reduced",
        "short",
        "approaching",
    ): "\U0001f7e1 SELL \u2014 підхід (поза KZ)",
}
# Wait-mode headlines per sub_mode
_WAIT_HEADLINES = {
    "too_far": "\U0001f7e1 Зона далеко \u2014 чекаємо підходу",
    "counter": "\U0001f7e0 Контртренд \u2014 зона є, але проти HTF",
    "reduced": "\U0001f7e1 HTF змішаний \u2014 чекаємо",
    "no_session": "\U0001f550 Поза сесією \u2014 зона є, чекаємо активності",
    # Зон справді нема. Раніше цей самий рядок («ринок без структури») показувався і коли зони є, але нижче рівня
    # A, і коли зону пробито, — поруч із next_area та трендом (ADR-0113 Ф0, K4)
    "": "\u2b1c Зон немає \u2014 чекаємо структуру",
}
# sub_mode "" із зонами на графіку: жодна не дотягує до trade_min_grade, або кандидата щойно пробито
_NO_QUALITY_ZONE_HEADLINE = "\u2b1c Немає зони рівня {grade} \u2014 чекаємо сетап"
_ZONE_BROKEN_HEADLINE = "\u2b1c Зону пробито \u2014 чекаємо нову структуру"
# ADR-0113 Ф2a: сценарій відпрацював після входу — не «зону пробито»
_SCENARIO_CLOSED_HEADLINES = {
    "scenario_target_hit": "\u2b1c Ціль сценарію взято \u2014 чекаємо нову структуру",
    "scenario_stopped": "\u2b1c Сценарій зупинено стопом \u2014 чекаємо нову структуру",
}
# Зона рівня A є, але D1 і H4 ще без структури (alignment no_data) — не «немає зони»
_HTF_PENDING_HEADLINE = "\u2b1c HTF ще без структури \u2014 зона є, чекаємо"
# Зони premium/discount — розмітка діапазону, не POI (не «зона для входу» ні в заголовку, ні в next_area)
_RANGE_ZONE_KINDS = frozenset({"premium", "discount"})
_DEGRADED_HEADLINE = "\u26a0 Наратив недоступний"

# ── TF labels ───────────────────────────────────────────────

# ── ADR-0053: Range Exhaustion display strings ──────────────
# Phrase emitted ONLY for late/exhausted phases — early/mid = silent (clean chart doctrine).
_RANGE_PHASE_HINTS = {
    "late": "ймовірний pullback або consolidation",
    "exhausted": "висока ймовірність розвороту, не chase'ити",
}
_RANGE_ANCHOR_LABELS = {
    "d1_open": "D1",
    "london_open": "London",
    "ny_open": "NY",
    "week_open": "Week",
}


def _build_range_exhaustion_summary(range_state):
    # type: (object) -> str
    """ADR-0053: human-readable phrase for late/exhausted phases.

    Returns "" коли:
      - range_state is None
      - phase in {early, mid} (clean chart doctrine — silent для здорового руху)
      - traveled_dir == 0 (flat day, нема напрямку)
    """
    if range_state is None:
        return ""
    phase = getattr(range_state, "phase", "")
    if phase not in _RANGE_PHASE_HINTS:
        return ""
    traveled_dir = getattr(range_state, "traveled_dir", 0)
    if traveled_dir == 0:
        return ""
    mult = getattr(range_state, "traveled_mult", 0.0)
    anchor_kind = getattr(range_state, "anchor_kind", "")
    anchor_label = _RANGE_ANCHOR_LABELS.get(anchor_kind, anchor_kind)
    arrow = "up" if traveled_dir > 0 else "down"
    hint = _RANGE_PHASE_HINTS[phase]
    return "{} {} {:.2f} ATR ({}) - {}".format(anchor_label, arrow, mult, phase, hint)


_TF_LABELS = {
    60: "M1",
    180: "M3",
    300: "M5",
    900: "M15",
    1800: "M30",
    3600: "H1",
    14400: "H4",
    86400: "D1",
}


def _tf_to_label(tf_s):
    # type: (int) -> str
    return _TF_LABELS.get(tf_s, "TF{}".format(tf_s))


# ── Ціни з точністю символу (ADR-0113 Ф0, K8) ───────────────
# SSOT — config.json price_display.digits_by_symbol (digits брокера, той самий, що в UI); runner передає значення.
# Без нього — 2 знаки і warning price_digits_missing (I5): раніше `{:.0f}` різав USD/JPY 158.3 до «158» / «161».
_DEFAULT_PRICE_DIGITS = 2


def _fmt_price(value, digits):
    # type: (float, int) -> str
    return "{:.{d}f}".format(value, d=digits)


# ── Fallback (N3 / BH-8) ───────────────────────────────────


def _fallback_narrative_block(warnings=None):
    # type: (Optional[List[str]]) -> NarrativeBlock
    return NarrativeBlock(
        mode="wait",
        sub_mode="",
        headline=_DEGRADED_HEADLINE,
        bias_summary="",
        scenarios=[],
        next_area="",
        fvg_context="",
        market_phase="ranging",
        warnings=warnings or ["computation_error"],
    )


# ── HTF Alignment (SC-4: D1+H4 only, N6) ──────────────────


def _resolve_htf_alignment(bias_map):
    # type: (Dict[str, str]) -> Tuple[str, Optional[str]]
    """Returns (alignment, direction).

    alignment: 'aligned' | 'mixed' | 'partial' | 'no_data'
    direction: 'bullish'|'bearish' if aligned/partial, None otherwise.

    'partial' = one of D1/H4 has trend_bias, the other does not yet
    (e.g. insufficient bars for structure detection). Treated as
    cautious/reduced in downstream logic — never "Недостатньо HTF даних".
    """
    d1 = bias_map.get("86400")
    h4 = bias_map.get("14400")
    if d1 and h4:
        if d1 == h4:
            return ("aligned", d1)
        return ("mixed", None)
    if d1 or h4:
        return ("partial", d1 or h4)
    return ("no_data", None)


# ── Zone direction ──────────────────────────────────────────


def _zone_direction(zone):
    # type: (SmcZone) -> str
    return zone_direction(zone)


# ── Invalidation (SC-7 / BH-1) ─────────────────────────────


def _find_invalidation(zone, digits=_DEFAULT_PRICE_DIGITS, stop_price=None):
    # type: (SmcZone, int, Optional[float]) -> str
    """Число — стоп: дальній край зони ± запас (ADR-0113 Ф2a); без запасу — сам край."""
    if "bear" in zone.kind:
        edge = zone.high if stop_price is None else stop_price
        return "Вище {} \u2014 зона пробита".format(_fmt_price(edge, digits))
    edge = zone.low if stop_price is None else stop_price
    return "Нижче {} \u2014 зона пробита".format(_fmt_price(edge, digits))


def _zone_stop_price(zone, atr, stop_buffer_atr):
    # type: (SmcZone, float, float) -> float
    """Стоп за дальнім краєм зони із запасом stop_buffer_atr × ATR (config smc.narrative.zone_path, ADR-0113 Ф2a)."""
    buffer = stop_buffer_atr * atr if atr > 0 else 0.0
    return zone.low - buffer if _zone_direction(zone) == LONG else zone.high + buffer


def _target_ref(path, current_price):
    # type: (Optional[ZonePath], float) -> Tuple[float, float]
    """(опора напрямку цілі, точка відліку відстані до неї).

    Без шляху — поточна ціна для обох (Ф1). Зі шляхом (ADR-0113 Ф2a): опора — далі з краю входу й екстремуму руху до
    входу (ліквідність, яку рух уже пройшов, знята), відстань — від краю входу.
    """
    if path is None:
        return current_price, current_price
    if path.direction == LONG:
        return max(path.entry_edge, path.pre_entry_extreme), path.entry_edge
    return min(path.entry_edge, path.pre_entry_extreme), path.entry_edge


def _path_closed_reason(path, target_price, stop_price):
    # type: (Optional[ZonePath], Optional[float], float) -> Optional[str]
    """Сценарій відпрацював після входу: тінь за стопом — «scenario_stopped», ціль узята — «scenario_target_hit»."""
    if path is None or not path.entered:
        return None
    reached = path.favorable_after_entry
    if path.direction == LONG:
        stopped = path.adverse_since_entry <= stop_price
        hit = target_price is not None and reached is not None and reached >= target_price
    else:
        stopped = path.adverse_since_entry >= stop_price
        hit = target_price is not None and reached is not None and reached <= target_price
    # Порядку стопу й цілі в агрегатах шляху не видно — стоп важить більше (рецензія Ф2a, D-10; час — Ф4)
    if stopped:
        return "scenario_stopped"
    return "scenario_target_hit" if hit else None


def _confirm_not_before_ms(path, zone):
    # type: (Optional[ZonePath], SmcZone) -> Optional[int]
    """Після входу підтверджує лише злам після КІНЦЯ бару входу на TF зони: усередині бару HTF дотик і злам не
    впорядковані (рецензія Ф2a, D-11). None — шляху чи входу немає (Ф1)."""
    if path is None or not path.entered:
        return None
    return path.entered_ms + zone.tf_s * 1000


def _confirmed_after_entry(snapshot, zone, viewer_tf_s, config, not_before_ms):
    # type: (SmcSnapshot, SmcZone, int, dict, Optional[int]) -> bool
    """Будь-який злам у напрямку зони після входу, біля якого є імпульс (не лише останній, D-02)."""
    if not_before_ms is None:
        return False
    kinds = ("choch_bear", "bos_bear") if "bear" in zone.kind else ("choch_bull", "bos_bull")
    return any(
        sw.kind in kinds and sw.time_ms >= not_before_ms
        and _has_displacement_near(snapshot, sw, zone, viewer_tf_s, config)
        for sw in snapshot.swings
    )


def _distance_to_zone(zone, price):
    # type: (SmcZone, float) -> float
    """Відстань від ціни до найближчого краю зони; 0 — ціна в зоні."""
    if price < zone.low:
        return zone.low - price
    if price > zone.high:
        return price - zone.high
    return 0.0


def _is_invalidated(zone, current_price, stop_price=None):
    # type: (SmcZone, float, Optional[float]) -> bool
    """Ціна за краєм зони; зі шляхом (Ф2a) — за стопом, тим самим числом, що в тексті інвалідації (D-04)."""
    if "bear" in zone.kind:
        return current_price > (zone.high if stop_price is None else stop_price)
    return current_price < (zone.low if stop_price is None else stop_price)


# ── Entry description ───────────────────────────────────────


def _format_entry(zone, grade_info, digits=_DEFAULT_PRICE_DIGITS):
    # type: (SmcZone, dict, int) -> str
    arrow = "\u25bc" if "bear" in zone.kind else "\u25b2"
    grade = grade_info.get("grade", "C")
    score = grade_info.get("score", 0)
    kind_short = "OB" if "ob" in zone.kind else "FVG"
    return "{kind}{arrow} {grade}({score}) {low}\u2013{high}".format(
        kind=kind_short,
        arrow=arrow,
        grade=grade,
        score=score,
        low=_fmt_price(zone.low, digits),
        high=_fmt_price(zone.high, digits),
    )


# ── Trigger Resolution (T-2: 4 IOFED states, proximity + displacement) ──


def _find_qualifying_structure_break(snapshot, zone, not_before_ms=None):
    # type: (SmcSnapshot, SmcZone, Optional[int]) -> Optional[SmcSwing]
    """Find the most recent CHoCH/BOS aligned with zone direction.

    Returns the swing if found (after zone creation), None otherwise.

    ADR-0113 Ф0 (K6): OB-свічка передує власному зламу, тож «злам після якоря» завжди знаходив той злам, що
    зону створив, — «ГОТОВИЙ ДО ВХОДУ» без нового підтвердження. Тепер для OB — лише злам ПІСЛЯ origin_event_ms;
    зони без цього знання (FVG, старі) — як було, від свічки-якоря.
    """
    target_kinds = (
        ("choch_bear", "bos_bear")
        if "bear" in zone.kind
        else ("choch_bull", "bos_bull")
    )
    # Кінець бару власного зламу на TF зони (end-excl): злам молодшого TF усередині того самого бару HTF-зони — той
    # самий імпульс, не нове підтвердження (рецензія Ф0, D-02; для зон TF глядача — те саме, що «строго після»)
    origin_end_ms = (
        zone.origin_event_ms + zone.tf_s * 1000 if zone.origin_event_ms is not None else None
    )
    for sw in reversed(snapshot.swings):
        if sw.kind not in target_kinds:
            continue
        if not_before_ms is not None and sw.time_ms < not_before_ms:
            return None  # далі лише давніші — злам до входу підтвердженням входу не є (ADR-0113 Ф2a)
        if origin_end_ms is not None:
            if sw.time_ms >= origin_end_ms:
                return sw
        elif sw.time_ms >= zone.anchor_bar_ms:
            return sw
    return None


def _has_displacement_near(snapshot, choch_swing, zone, viewer_tf_s, config):
    # type: (SmcSnapshot, SmcSwing, SmcZone, int, dict) -> bool
    """Displacement marker within \u00b1N viewer-TF bars of the CHoCH.

    Doctrine: MS Shift requires impulse/displacement, not just structure break.
    """
    window_bars = config.get("trigger_displacement_window", 3)
    window_ms = window_bars * viewer_tf_s * 1000
    target_kind = "displacement_bear" if "bear" in zone.kind else "displacement_bull"
    choch_ms = choch_swing.time_ms
    for sw in snapshot.swings:
        if sw.kind == target_kind and abs(sw.time_ms - choch_ms) <= window_ms:
            return True
    return False


def _is_price_proximate(zone, current_price, atr, config):
    # type: (SmcZone, float, float, dict) -> bool
    """Price within trigger_proximity_atr \u00d7 ATR of the nearest zone edge."""
    if atr <= 0:
        return True  # Cannot measure, be permissive
    max_atr = config.get("trigger_proximity_atr", 3.0)
    if current_price < zone.low:
        distance = zone.low - current_price
    elif current_price > zone.high:
        distance = current_price - zone.high
    else:
        return True  # Inside zone
    return distance <= max_atr * atr


def _resolve_trigger_state(snapshot, zone, viewer_tf_s, current_price, atr, config, path=None):
    # type: (SmcSnapshot, SmcZone, int, float, float, dict, Optional[ZonePath]) -> str
    """T-2: 4 IOFED trigger states with proximity + displacement guards.

    Matrix (doctrine: proximity to POI + displacement confirmation):
      CHoCH + displacement + in_zone          \u2192 \"ready\"
      CHoCH + in_zone (no displacement)       \u2192 \"in_zone\"
      CHoCH + displacement + proximate        \u2192 \"triggered\"
      CHoCH + not proximate                   \u2192 \"approaching\"
      CHoCH + proximate (no displacement)     \u2192 \"approaching\"
      no CHoCH + in_zone                      \u2192 \"in_zone\"
      no CHoCH                                \u2192 \"approaching\"
    """
    in_zone = zone.low <= current_price <= zone.high
    not_before = _confirm_not_before_ms(path, zone)
    # ADR-0113 Ф2a: злам з імпульсом ПІСЛЯ входу — сценарій підтверджено; далі лише «ready» (ціна в зоні) чи
    # «triggered» (пішла до цілі), а не «approaching», як коли стан рахувався лише з поточної ціни
    if _confirmed_after_entry(snapshot, zone, viewer_tf_s, config, not_before):
        return "ready" if in_zone else "triggered"
    # Після входу злам до входу підтвердженням не є й у правилах Ф1 (D-03)
    choch = _find_qualifying_structure_break(snapshot, zone, not_before_ms=not_before)
    has_disp = False
    if choch is not None:
        has_disp = _has_displacement_near(snapshot, choch, zone, viewer_tf_s, config)
    proximate = _is_price_proximate(zone, current_price, atr, config)
    if choch is not None and has_disp and in_zone:
        return "ready"
    if choch is not None and in_zone:
        return "in_zone"
    if choch is not None and has_disp and proximate:
        return "triggered"
    if in_zone:
        return "in_zone"
    return "approaching"


def _resolve_trigger_desc(
    snapshot, zone, viewer_tf_s, current_price, atr, config, digits=_DEFAULT_PRICE_DIGITS, path=None
):
    # type: (SmcSnapshot, SmcZone, int, float, float, dict, int, Optional[ZonePath]) -> str
    state = _resolve_trigger_state(
        snapshot, zone, viewer_tf_s, current_price, atr, config, path
    )
    direction_arrow = "\u2193" if "bear" in zone.kind else "\u2191"
    tf_label = _tf_to_label(viewer_tf_s)

    if state == "ready":
        return "Готовий: структура + імпульс підтверджені в зоні"
    not_before = _confirm_not_before_ms(path, zone)
    if state == "triggered" and not_before is not None:
        # Вхід уже був — без «шукаємо вхід», що штовхало б наздоганяти ціну (рецензія Ф2a, D-12)
        return "Підтверджено після входу: {tf} CHoCH{a} + імпульс".format(tf=tf_label, a=direction_arrow)
    if state == "triggered":
        return "Тригер: {tf} CHoCH{a} + імпульс \u2014 шукаємо вхід".format(
            tf=tf_label, a=direction_arrow
        )
    if state == "in_zone":
        choch = _find_qualifying_structure_break(snapshot, zone, not_before_ms=not_before)
        if choch is not None:
            return "У зоні: CHoCH{a} є, чекаємо імпульс".format(a=direction_arrow)
        return "У зоні: чекаємо {tf} CHoCH{a}".format(tf=tf_label, a=direction_arrow)
    # approaching
    if current_price < zone.low:
        dist = zone.low - current_price
    elif current_price > zone.high:
        dist = current_price - zone.high
    else:
        dist = 0.0
    # «пт» не мав сенсу поза XAU (USD/JPY, XAG): відстань — в ATR, а без ATR — у ціні символу
    if atr > 0:
        return "{:.1f} ATR до зони".format(dist / atr)
    return "{} до зони".format(_fmt_price(dist, digits))


# ── Target Resolution ───────────────────────────────────────


def _find_target(
    snapshot, zone, direction, config, current_price=0.0, atr=0.0, digits=_DEFAULT_PRICE_DIGITS, path=None
):
    # type: (SmcSnapshot, SmcZone, str, dict, float, float, int, Optional[ZonePath]) -> Optional[str]
    """Find target price. Returns None if unknown (BH-4)."""
    target = _select_target(snapshot, zone, direction, config, current_price, atr, digits, path)
    return target[0] if target is not None else None


def _select_target(
    snapshot, zone, direction, config, current_price=0.0, atr=0.0, digits=_DEFAULT_PRICE_DIGITS, path=None
):
    # type: (SmcSnapshot, SmcZone, str, dict, float, float, int, Optional[ZonePath]) -> Optional[Tuple[str, float]]
    """(опис цілі, її ціна для перевірки «взято»). None — ціль не знайдено (BH-4).

    ATR-distance filter: targets farther than target_max_atr_distance * ATR from the distance origin are skipped
    (prevents D1-level PDL on M5 chart). Опора й відлік відстані — `_target_ref`: без шляху — поточна ціна.
    """
    max_atr_mult = config.get("target_max_atr_distance", 12)
    scale_atr = path.birth_atr if path is not None else atr  # ATR зони: стабільний для зони на будь-якому TF (D-05)
    max_dist = max_atr_mult * scale_atr if scale_atr > 0 else 0.0
    ref, dist_from = _target_ref(path, current_price)
    # Після входу ціль — лише з ліквідності, що існувала на вході: рівні й свінги, що створив рух після входу
    # (новий «попередній H1», PDL наступної доби), закривали б сценарій «ціллю» (рецензія Ф2a, D-01)
    formed_before = path.entered_ms if path is not None and path.entered else None

    # Priority 1: Key level
    target_level = _find_target_key_level(snapshot.levels, direction, ref, max_dist, dist_from, formed_before)
    if target_level:
        return (
            "{label} {price}".format(
                label=_TARGET_LEVEL_LABELS.get(target_level.kind, target_level.kind.upper()),
                price=_fmt_price(target_level.price, digits),
            ),
            target_level.price,
        )

    # Priority 2: opposite-side institutional zone
    for z in snapshot.zones:
        if z.id == zone.id or z.status == "mitigated":
            continue
        if formed_before is not None and z.start_ms > formed_before:
            continue
        z_dir = _zone_direction(z)
        if z_dir != direction and z.context_layer == "institutional":
            center = (z.high + z.low) / 2
            near_edge = z.high if direction == "short" else z.low  # ціль-зона взята з ближнього краю
            # P2: directional gate — target zone must be ahead of price (зі шляхом — її ближній край, D-14)
            gate = near_edge if path is not None else center
            if direction == "short" and gate >= ref:
                continue
            if direction == "long" and gate <= ref:
                continue
            if max_dist > 0 and abs(center - dist_from) > max_dist:
                continue
            return (
                "HTF {kind} {low}\u2013{high}".format(
                    kind=z.kind.split("_")[0].upper(),
                    low=_fmt_price(z.low, digits),
                    high=_fmt_price(z.high, digits),
                ),
                near_edge,
            )

    # Priority 3: Recent swing extreme
    target_swing = _find_target_swing(snapshot.swings, direction, ref, max_dist, dist_from, formed_before)
    if target_swing:
        return (
            "Останній {kind} {price}".format(
                kind=target_swing.kind.upper(), price=_fmt_price(target_swing.price, digits)
            ),
            target_swing.price,
        )

    return None


# Ліквідність-ціль (draw on liquidity) за напрямком: ключові рівні дня/H4/H1, EQH/EQL, сесійні H/L, тиждень/місяць.
# Раніше лише 6 kinds і перший за порядком списку (PDL перемагав ближчий H4 low) — ADR-0113 Ф0, K5.
_TARGET_KINDS_BELOW = frozenset(
    {"pdl", "dl", "p_h4_l", "h4_l", "p_h1_l", "h1_l", "eq_lows", "as_l", "p_as_l", "lon_l", "p_lon_l",
     "ny_l", "p_ny_l", "pwl", "pml"}
)
_TARGET_KINDS_ABOVE = frozenset(
    {"pdh", "dh", "p_h4_h", "h4_h", "p_h1_h", "h1_h", "eq_highs", "as_h", "p_as_h", "lon_h", "p_lon_h",
     "ny_h", "p_ny_h", "pwh", "pmh"}
)
# Підписи цілей — без цифри перед ціною там, де її можна сплутати з ціною («P H4 L 2892» журнал читав як 4.0)
_TARGET_LEVEL_LABELS = {
    "pdh": "PDH", "pdl": "PDL", "dh": "хай дня", "dl": "лой дня",
    "p_h4_h": "хай попер. H4", "p_h4_l": "лой попер. H4", "h4_h": "хай H4", "h4_l": "лой H4",
    "p_h1_h": "хай попер. H1", "p_h1_l": "лой попер. H1", "h1_h": "хай H1", "h1_l": "лой H1",
    "eq_highs": "EQH", "eq_lows": "EQL",
    "as_h": "хай Азії", "as_l": "лой Азії", "p_as_h": "хай попер. Азії", "p_as_l": "лой попер. Азії",
    "lon_h": "хай Лондона", "lon_l": "лой Лондона", "p_lon_h": "хай попер. Лондона", "p_lon_l": "лой попер. Лондона",
    "ny_h": "хай NY", "ny_l": "лой NY", "p_ny_h": "хай попер. NY", "p_ny_l": "лой попер. NY",
    "pwh": "PWH", "pwl": "PWL", "pmh": "PMH", "pml": "PML",
}  # type: Dict[str, str]


def _find_target_key_level(levels, direction, current_price=0.0, max_dist=0.0, dist_from=None, formed_before_ms=None):
    # type: (List[SmcLevel], str, float, float, Optional[float], Optional[int]) -> Optional[SmcLevel]
    """Найближчий ЗАВЕРШЕНИЙ рівень ліквідності попереду ціни в напрямку сценарію (у межах max_dist, якщо задано).

    Рівень, що ще формується (H/L поточної сесії, дня, H4/H1 — state forming), — не ціль: поточний діапазон охоплює
    ціну, і на відкритті сесії «ціль» стояла б за копійки від неї (рецензія Ф0, D-01).
    """
    target_kinds = _TARGET_KINDS_BELOW if direction == "short" else _TARGET_KINDS_ABOVE
    origin = current_price if dist_from is None else dist_from  # відлік відстані (Ф2a — край входу)
    best = None  # type: Optional[SmcLevel]
    for lv in levels:
        if lv.kind not in target_kinds or lv.state == LEVEL_STATE_FORMING:
            continue
        if formed_before_ms is not None and (lv.time_ms is None or lv.time_ms > formed_before_ms):
            continue
        # P2: directional gate — target must be ahead of price (Ф2a: ahead of the move already made)
        if direction == "short" and lv.price >= current_price:
            continue
        if direction == "long" and lv.price <= current_price:
            continue
        if max_dist > 0 and abs(lv.price - origin) > max_dist:
            continue
        if best is None or abs(lv.price - current_price) < abs(best.price - current_price):
            best = lv
    return best


def _find_target_swing(swings, direction, current_price=0.0, max_dist=0.0, dist_from=None, formed_before_ms=None):
    # type: (List[SmcSwing], str, float, float, Optional[float], Optional[int]) -> Optional[SmcSwing]
    target_kinds = {"ll", "lh"} if direction == "short" else {"hh", "hl"}
    origin = current_price if dist_from is None else dist_from
    for sw in reversed(swings):
        if formed_before_ms is not None and sw.time_ms > formed_before_ms:
            continue
        if sw.kind in target_kinds:
            # P2: directional gate — target must be ahead of price
            if direction == "short" and sw.price >= current_price:
                continue
            if direction == "long" and sw.price <= current_price:
                continue
            if max_dist > 0 and abs(sw.price - origin) > max_dist:
                continue
            return sw
    return None


# ── FVG Context (T-5) ───────────────────────────────────────


def _build_fvg_context(snapshot, zone, atr, config, digits=_DEFAULT_PRICE_DIGITS):
    # type: (SmcSnapshot, Optional[SmcZone], float, dict, int) -> str
    if not config.get("fvg_context_enabled", True) or zone is None:
        return ""
    for z in snapshot.zones:
        if "fvg" not in z.kind or z.status == "mitigated":
            continue
        # Overlap з zone
        if z.low <= zone.high and z.high >= zone.low:
            return "OB вхід уточнений FVG: {}\u2013{}".format(
                _fmt_price(z.low, digits), _fmt_price(z.high, digits)
            )
    # Closest FVG within 2*ATR
    if atr > 0:
        for z in snapshot.zones:
            if "fvg" not in z.kind or z.status == "mitigated":
                continue
            mid = (z.low + z.high) / 2.0
            zone_mid = (zone.low + zone.high) / 2.0
            if abs(mid - zone_mid) <= 2.0 * atr:
                return "FVG гап на {} \u2014 ребаланс перед зоною".format(_fmt_price(mid, digits))
    return ""


# ── Market Phase Detection (SC-1: 3 states + BH-6 hysteresis) ──


def _detect_market_phase(swings, config):
    # type: (List[SmcSwing], dict) -> str
    if not config.get("market_phase_enabled", True):
        return "ranging"
    classified = [s for s in swings if s.kind in ("hh", "hl", "lh", "ll")]
    if len(classified) < 2:
        return "ranging"

    # P3: use phase_hysteresis_bars (BH-6) — require N consecutive same-direction swings
    hysteresis = max(2, config.get("phase_hysteresis_bars", 3))
    last_n = classified[-hysteresis:]
    if len(last_n) < hysteresis:
        return "ranging"

    kinds = {s.kind for s in last_n}
    if kinds <= {"hh", "hl"}:
        return "trending_up"
    if kinds <= {"lh", "ll"}:
        return "trending_down"
    return "ranging"


# ── Bias Summary (T-8) ─────────────────────────────────────


def _build_bias_summary(
    alignment, htf_direction, bias_map, zone, momentum_map=None, is_counter=False
):
    # type: (str, Optional[str], Dict[str, str], Optional[SmcZone], Optional[Dict[str, dict]], bool) -> str
    if alignment == "no_data":
        return "HTF: \u043e\u0447\u0456\u043a\u0443\u0454\u043c\u043e \u0441\u0442\u0440\u0443\u043a\u0442\u0443\u0440\u0443"
    if alignment == "partial":
        d1 = bias_map.get("86400")
        h4 = bias_map.get("14400")
        if d1:
            arrow = "\u2191" if d1 == "bullish" else "\u2193"
            return "D1{a} (H4 ще без структури)".format(a=arrow)
        if h4:
            arrow = "\u2191" if h4 == "bullish" else "\u2193"
            return "H4{a} (D1 ще без структури)".format(a=arrow)
    d1 = bias_map.get("86400", "?")
    h4 = bias_map.get("14400", "?")
    if alignment == "mixed":
        d1_arrow = "\u2191" if d1 == "bullish" else "\u2193"
        h4_arrow = "\u2191" if h4 == "bullish" else "\u2193"
        return "D1{d1} H4{h4}".format(d1=d1_arrow, h4=h4_arrow)
    # aligned. Без порад щодо розміру позиції в публічному тексті (ADR-0113 R8) і без англійських bullish/bearish
    htf_arrow = "\u2191" if htf_direction == "bullish" else "\u2193"
    if is_counter:
        return "HTF{a} (D1 і H4) \u2014 найкраща зона проти тренду".format(a=htf_arrow)
    if zone and "premium" in getattr(zone, "kind", ""):
        return "H4 відкат до premium OB \u2014 чекаємо відбій"
    base = "HTF{a} (D1 і H4) \u2014 шукаємо структуру для входу".format(a=htf_arrow)
    # P2: momentum context — displacement bars confirm directional intent (ADR-0033 Rev 2)
    if momentum_map:
        bull_total = sum(m.get("b", 0) for m in momentum_map.values())
        bear_total = sum(m.get("r", 0) for m in momentum_map.values())
        if htf_direction == "bullish" and bull_total >= 2:
            base += " \u00b7 бичачий імпульс \u2191"
        elif htf_direction == "bearish" and bear_total >= 2:
            base += " \u00b7 ведмежий імпульс \u2193"
    return base


# ── Session Context (ADR-0035 §5.1) ─────────────────────────

_SESSION_LABELS = {
    "asia": "Asia",
    "london": "London",
    "newyork": "New York",
}


def _build_session_context(session_name, in_killzone):
    # type: (str, bool) -> str
    if not session_name:
        # Календар сесій є, але жодна не йде (між закриттям NY і Азією) — сказати прямо, а не «Off_Session … активна»
        return "Поза торговими сесіями"
    label = _SESSION_LABELS.get(session_name, session_name.title())
    if in_killzone:
        return "{}: killzone активна".format(label)
    return "{} сесія активна".format(label)


# ── Next Area ───────────────────────────────────────────────


def _zone_kind_label(zone):
    # type: (SmcZone) -> str
    """OB / FVG / IFVG — без змішування IFVG і P/D під ярликом «FVG» (ADR-0113 Ф0, K4)."""
    if zone.kind.startswith("ifvg"):
        return "IFVG"
    if zone.kind.startswith("ob"):
        return "OB"
    return "FVG"


def _build_next_area(snapshot, zone_grades, digits=_DEFAULT_PRICE_DIGITS, current_price=0.0, exclude_ids=frozenset()):
    # type: (SmcSnapshot, Dict[str, dict], int, float, frozenset) -> str
    """Найближча зона уваги: оцінені — першими (score↓), далі ближчі до ціни.

    ADR-0113 Ф0 (K3, K4): без mitigated, без перебитих ціною, без premium/discount (розмітка діапазону, не POI);
    зона без оцінки — «без оцінки», а не дефолтне «C/0» (рушій оцінює лише OB, engine.py — FVG не оцінюються).
    """
    best = None  # type: Optional[SmcZone]
    best_key = None  # type: Optional[Tuple[int, float, float]]
    for z in snapshot.zones:
        if z.status == "mitigated" or z.kind in _RANGE_ZONE_KINDS or z.id in exclude_ids:
            continue
        if current_price > 0 and _is_invalidated(z, current_price):
            continue
        gi = zone_grades.get(z.id)
        graded = 0 if gi else 1
        score = gi.get("score", 0) if gi else 0
        dist = abs(current_price - (z.low + z.high) / 2.0) if current_price > 0 else 0.0
        key = (graded, -score, dist)
        if best_key is None or key < best_key:
            best_key = key
            best = z
    if best is None:
        return ""
    gi = zone_grades.get(best.id)
    grade_part = "{}({})".format(gi.get("grade", "C"), gi.get("score", 0)) if gi else "без оцінки"
    return "{kind}{arrow} {grade} {low}\u2013{high}".format(
        kind=_zone_kind_label(best),
        arrow="\u25bc" if "bear" in best.kind else "\u25b2",
        grade=grade_part,
        low=_fmt_price(best.low, digits),
        high=_fmt_price(best.high, digits),
    )


# ═══════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════


def synthesize_narrative(
    snapshot,  # type: SmcSnapshot
    bias_map,  # type: Dict[str, str]
    zone_grades,  # type: Dict[str, dict]
    momentum_map,  # type: Dict[str, dict]
    viewer_tf_s,  # type: int
    current_price,  # type: float
    atr,  # type: float
    config,  # type: dict
    session_info=None,  # type: Optional[Tuple[str, bool]]
    range_exhaustion=None,  # type: Optional[object]
    price_digits=None,  # type: Optional[int]
    zone_paths=None,  # type: Optional[Dict[str, ZonePath]]
):
    # type: (...) -> NarrativeBlock
    """Synthesize narrative for display (ADR-0033, ADR-0035, ADR-0053).

    N0: pure, deterministic.
    N3: NEVER returns None — fallback block з warnings.
    session_info: (current_session_name, in_killzone) or None.
    range_exhaustion: ADR-0053 RangeExhaustionState (primary anchor) or None;
        used to populate NarrativeBlock.range_exhaustion_summary.
    price_digits: знаків після коми для цін символу (config price_display.digits_by_symbol, ADR-0113 Ф0);
        None — 2 знаки і warning price_digits_missing.
    zone_paths: zone_id → ZonePath (шлях ціни від бару зламу зони, ADR-0113 Ф2a); None — без шляху (поведінка Ф1).
    """
    try:
        return _synthesize_impl(
            snapshot,
            bias_map,
            zone_grades,
            momentum_map,
            viewer_tf_s,
            current_price,
            atr,
            config,
            session_info,
            range_exhaustion,
            price_digits,
            zone_paths,
        )
    except Exception:
        sym = getattr(snapshot, "symbol", "?")
        tf = getattr(snapshot, "tf_s", 0)
        logger.exception("NARRATIVE_ERROR symbol=%s tf=%s", sym, tf)
        return _fallback_narrative_block()


def _synthesize_impl(
    snapshot,
    bias_map,
    zone_grades,
    momentum_map,
    viewer_tf_s,
    current_price,
    atr,
    config,
    session_info=None,
    range_exhaustion=None,
    price_digits=None,
    zone_paths=None,
):
    # type: (SmcSnapshot, Dict[str, str], Dict[str, dict], Dict[str, dict], int, float, float, dict, Optional[Tuple[str, bool]], Optional[object], Optional[int], Optional[Dict[str, ZonePath]]) -> NarrativeBlock
    warnings = []  # type: List[str]
    if price_digits is None:
        digits = _DEFAULT_PRICE_DIGITS
        warnings.append("price_digits_missing")
    else:
        digits = price_digits
    min_grade = config.get("trade_min_grade", "A")
    min_score = config.get("trade_min_score", 6)
    max_scenarios = config.get("max_scenarios", 2)

    # Step 1: HTF alignment (SC-4, N6)
    alignment, htf_direction = _resolve_htf_alignment(bias_map)

    # Step 2: Best zones
    candidates = _select_candidate_zones(
        snapshot.zones,
        zone_grades,
        current_price,
        min_grade,
        min_score,
        warnings,
        alignment=alignment,
        htf_direction=htf_direction,
        config=config,
    )

    # Step 2b: Invalidation check (SC-7); Ф2a — сценарій, що відпрацював після входу (стоп / ціль), не повертається
    paths = zone_paths or {}
    stop_buffer_atr = float(config.get("zone_path", {}).get("stop_buffer_atr", 0.0))
    stops = {}  # type: Dict[str, Optional[float]]
    closed = {}  # type: Dict[str, str]
    valid = []  # type: List[SmcZone]
    for z in candidates:
        path = paths.get(z.id)
        # Стоп — лише зі шляхом і від ATR зони на барі зламу (D-05); без шляху — край зони, як у Ф1
        stops[z.id] = _zone_stop_price(z, path.birth_atr, stop_buffer_atr) if path is not None else None
        if _is_invalidated(z, current_price, stops[z.id]):
            warnings.append("scenario_invalidated")
            continue
        if path is not None:
            target = _select_target(snapshot, z, _zone_direction(z), config, current_price, atr, digits, path)
            reason = _path_closed_reason(path, target[1] if target is not None else None, stops[z.id])
            if reason is not None:
                warnings.append(reason)
                closed[z.id] = reason
                continue
        valid.append(z)

    primary_zone = valid[0] if valid else None

    # Step 3: Mode decision (SC-3)
    #   "aligned" = HTF agrees AND zone direction matches HTF.
    #   "counter" = HTF aligned but zone opposes HTF direction.
    #   "reduced" = HTF mixed (D1 ≠ H4).
    direction: str = _zone_direction(primary_zone) if primary_zone else ""
    htf_trade_dir = (
        "long"
        if htf_direction == "bullish"
        else "short" if htf_direction == "bearish" else ""
    )
    # Distance guard: price must be within trade_max_distance_atr of primary zone
    max_dist_atr = config.get("trade_max_distance_atr", 5.0)
    _too_far = False
    primary_path = paths.get(primary_zone.id) if primary_zone else None
    if primary_zone and atr > 0 and not (primary_path is not None and primary_path.entered):
        _too_far = _distance_to_zone(primary_zone, current_price) > max_dist_atr * atr
    # Альтернатива — друга зона того самого напрямку в тих самих межах відстані (ADR-0113 Ф0, K7): раніше бралася
    # будь-яка друга, і під «BUY» міг стояти short за 37 ATR. Сценарій на протилежний бік — окреме рішення (Ф2).
    alt_zone = next(
        (
            z
            for z in valid[1:]
            if _zone_direction(z) == direction
            and (atr <= 0 or _distance_to_zone(z, current_price) <= max_dist_atr * atr)
        ),
        None,
    )

    if _too_far:
        mode, sub_mode = "wait", "too_far"
        warnings.append("zone_too_far")
    elif primary_zone and alignment == "aligned" and direction == htf_trade_dir:
        mode, sub_mode = "trade", "aligned"
    elif primary_zone and alignment == "aligned":
        # HTF aligned but zone opposes → counter-trend → wait (D-02)
        mode, sub_mode = "wait", "counter"
        warnings.append("counter_trend")
    elif primary_zone and alignment in ("mixed", "partial"):
        mode, sub_mode = "wait", "reduced"
    else:
        mode, sub_mode = "wait", ""

    # Build direction from primary (already computed above)

    # Step 3.5: Session-aware mode adjustments (ADR-0035).
    # Extracted before headline so session guards influence headline selection.
    current_session = ""
    in_killzone = False
    session_context = ""
    if session_info is not None:
        # «Поза сесіями» — порожня сесія для всіх перевірок нижче і для wire (UI не друкує «OFF_SESSION»)
        current_session = "" if session_info[0] == OFF_SESSION else (session_info[0] or "")
        in_killzone = bool(session_info[1])
        session_context = _build_session_context(current_session, in_killzone)
        # Guard: no active session (dead zone between sessions) → cannot call trade
        if mode == "trade" and not current_session:
            mode, sub_mode = "wait", "no_session"
            warnings.append("outside_session")
        # Killzone downgrade: inside a session but outside its killzone window
        elif mode == "trade" and not in_killzone:
            if sub_mode == "aligned":
                sub_mode = "reduced"
                warnings.append("outside_killzone")

    # Step 3.6: Primary trigger state (resolved once; drives headline + primary scenario)
    primary_trigger = None  # type: Optional[str]
    if primary_zone and mode == "trade":
        primary_trigger = _resolve_trigger_state(
            snapshot, primary_zone, viewer_tf_s, current_price, atr, config, paths.get(primary_zone.id)
        )

    # Step 4: Headline — all mode / session / trigger state is now known
    if mode == "trade" and direction and primary_trigger:
        headline = _TRADE_HEADLINES.get(
            (mode, sub_mode, direction, primary_trigger),
            _WAIT_HEADLINES.get(sub_mode, _WAIT_HEADLINES[""]),
        )
    elif sub_mode == "" and primary_zone is not None:
        headline = _HTF_PENDING_HEADLINE
    elif sub_mode == "" and not valid and closed:
        headline = _SCENARIO_CLOSED_HEADLINES[next(iter(closed.values()))]
    elif sub_mode == "" and candidates and not valid:
        headline = _ZONE_BROKEN_HEADLINE
    elif sub_mode == "" and any(
        z.status != "mitigated" and z.kind not in _RANGE_ZONE_KINDS for z in snapshot.zones
    ):
        headline = _NO_QUALITY_ZONE_HEADLINE.format(grade=min_grade)
    else:
        headline = _WAIT_HEADLINES.get(sub_mode, _WAIT_HEADLINES[""])

    # Scenarios (T-1: max 2)
    scenarios = []  # type: List[ActiveScenario]
    if mode == "trade":
        for _zc in (primary_zone, alt_zone):
            if _zc is None:
                continue
            z = _zc
            d = _zone_direction(z)
            gi = zone_grades.get(z.id, {})
            path = paths.get(z.id)
            tgt = _find_target(snapshot, z, d, config, current_price, atr, digits, path)
            if tgt is None:
                warnings.append("no_target_found")
            scenarios.append(
                ActiveScenario(
                    zone_id=z.id,
                    direction=d,
                    entry_desc=_format_entry(z, gi, digits),
                    trigger=_resolve_trigger_state(
                        snapshot, z, viewer_tf_s, current_price, atr, config, path
                    ),
                    trigger_desc=_resolve_trigger_desc(
                        snapshot, z, viewer_tf_s, current_price, atr, config, digits, path
                    ),
                    target_desc=tgt,
                    invalidation=_find_invalidation(z, digits, stops[z.id]),
                )
            )
            if len(scenarios) >= max_scenarios:
                break

    # Bias summary (T-8)
    bias_summary = _build_bias_summary(
        alignment,
        htf_direction,
        bias_map,
        primary_zone,
        momentum_map=momentum_map,
        is_counter=(sub_mode == "counter"),
    )

    # FVG context (T-5)
    fvg_context = _build_fvg_context(snapshot, primary_zone, atr, config, digits)

    # Market phase (SC-1, BH-6, N5: display-only)
    market_phase = _detect_market_phase(snapshot.swings, config)

    # Next area
    next_area = _build_next_area(snapshot, zone_grades, digits, current_price, frozenset(closed))

    # ADR-0053: range exhaustion phrase (silent for early/mid/None)
    range_exhaustion_summary = _build_range_exhaustion_summary(range_exhaustion)

    return NarrativeBlock(
        mode=mode,
        sub_mode=sub_mode,
        headline=headline,
        bias_summary=bias_summary,
        scenarios=scenarios,
        next_area=next_area,
        fvg_context=fvg_context,
        market_phase=market_phase,
        warnings=warnings,
        current_session=current_session,
        in_killzone=in_killzone,
        session_context=session_context,
        range_exhaustion_summary=range_exhaustion_summary,
    )


# ── Zone selection helpers ──────────────────────────────────

_GRADE_ORDER = {"A+": 0, "A": 1, "B": 2, "C": 3}


def _select_candidate_zones(
    zones,
    zone_grades,
    current_price,
    min_grade,
    min_score,
    warnings,
    alignment=None,
    htf_direction=None,
    config=None,
):
    # type: (List[SmcZone], Dict[str, dict], float, str, int, List[str], Optional[str], Optional[str], Optional[dict]) -> List[SmcZone]
    """Filter + sort zones by grade/score then proximity.

    P5B: FVG zones use fvg_trade_min_score (default 99 = display only).
    P6: HTF-aligned zones get +3 virtual bonus in sort key (ICT doctrine).
    """
    min_grade_idx = _GRADE_ORDER.get(min_grade, 1)
    fvg_min_score = (config or {}).get("fvg_trade_min_score", 99)
    candidates = []  # type: List[SmcZone]
    for z in zones:
        if z.status == "mitigated":
            continue
        gi = zone_grades.get(z.id, {})
        grade = gi.get("grade", "C")
        score = gi.get("score", 0)
        grade_idx = _GRADE_ORDER.get(grade, 3)
        effective_min_score = fvg_min_score if z.kind.startswith("fvg_") else min_score
        if grade_idx <= min_grade_idx and score >= effective_min_score:
            candidates.append(z)

    # Sort: (score + alignment bonus) DESC, proximity ASC
    _HTF_ALIGN_BONUS = 3

    def sort_key(z):
        gi = zone_grades.get(z.id, {})
        mid = (z.low + z.high) / 2.0
        bonus = 0
        if alignment == "aligned" and htf_direction:
            z_dir = _zone_direction(z)
            htf_trade = "long" if htf_direction == "bullish" else "short"
            if z_dir == htf_trade:
                bonus = _HTF_ALIGN_BONUS
        return (-(gi.get("score", 0) + bonus), abs(current_price - mid))

    candidates.sort(key=sort_key)
    return candidates


# ── Wire serialization (BH-5) ──────────────────────────────


def narrative_to_wire(block):
    # type: (NarrativeBlock) -> dict
    """Convert NarrativeBlock to wire dict. S6 compliance."""
    scenarios_wire = []  # type: List[dict]
    for s in block.scenarios:
        scenarios_wire.append(
            {
                "zone_id": s.zone_id,
                "direction": s.direction,
                "entry_desc": s.entry_desc,
                "trigger": s.trigger,
                "trigger_desc": s.trigger_desc,
                "target_desc": s.target_desc,
                "invalidation": s.invalidation,
            }
        )
    d = {
        "mode": block.mode,
        "sub_mode": block.sub_mode,
        "headline": block.headline,
        "bias_summary": block.bias_summary,
        "scenarios": scenarios_wire,
        "next_area": block.next_area,
        "fvg_context": block.fvg_context,
        "market_phase": block.market_phase,
        "warnings": block.warnings,
        "current_session": block.current_session,
        "in_killzone": block.in_killzone,
        "session_context": block.session_context,
    }
    # ADR-0053: include only when populated (null-compact, matches SmcSnapshot.to_wire pattern)
    if block.range_exhaustion_summary:
        d["range_exhaustion_summary"] = block.range_exhaustion_summary
    return d
