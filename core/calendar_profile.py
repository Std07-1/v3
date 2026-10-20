"""Профіль тижня і місяця (ADR-0115 §10): у який день тижня / тиждень місяця зазвичай стоїть хай і лой бакета. Pure.

Лише власна статистика платформи з D1 SSOT, без прогнозу (ADR-0113 R8): розподіл «де стоїть екстремум» за всю історію
символу і умовна частота «екстремум, що вже стоїть станом на сьогодні, лишився екстремумом бакета». Частки — з 95 %
інтервалом Вілсона.

Слоти: W1 — день тижня торгової дати (Пн…Пт); MN — тиждень місяця за числом торгової дати (1–7, 8–14, 15–21, 22–28,
29–31). Бакет бере участь, якщо він закінчився і всі його D1 мають торгову дату Пн–Пт: так відсіюються огризки вихідних і
індекси 1990–2008 зі старою конвенцією ключів (сесія на ключі свого кінця → «субота»), де день тижня зсунутий на один.
Екстремум — перший D1 з максимумом high / мінімумом low; «лишився» — жоден пізніший D1 бакета не пішов строго далі.
"""

from __future__ import annotations

import dataclasses
import math
from typing import Dict, List, Optional, Sequence, Tuple

from core.calendar_tf import MN_S, W1_S, DayBar, calendar_bucket_start_ms, calendar_next_bucket_start_ms, trading_date
from core.i18n import SOURCE_TRANSLATOR, Translator, msg
from core.smc.briefing import BriefingRow, BriefingSection
from core.smc.narrative import fmt_price

SLOT_COUNT = 5
WILSON_Z_95 = 1.96
# Скільки D1 з торговою датою на вихідних означає систематичну епоху старої конвенції ключів, а не поодинокі огризки:
# індекси 1990–2008 мають ≈ 900 «субот», метали й EUSTX50 — 0–1 (виміряно 19.10.2026 на прод-D1). Півроку щотижневих
# «субот» — поріг із запасом в обидва боки. Епоха відрізається цілком (вибірка — після останньої такої D1): у ній і
# тижні без «суботи» (свято в п'ятницю) мають зсунутий на день тиждень, і правило «чистого бакета» їх не відсіяло б.
OLD_KEY_ERA_MIN_WEEKEND_DAYS = 26
HIGH, LOW = "high", "low"
_SIDES = (HIGH, LOW)
# Менша вибірка — частка не показується (інтервал Вілсона ширший за ±20 п.п.)
MIN_SAMPLE = 20
SECTION_KEY = "calendar_profile"


@dataclasses.dataclass(frozen=True)
class SideProfile:
    """Один бік (хай або лой): розподіл слотів екстремуму і умовна частота «утримався» [k][x]."""

    slot_counts: Tuple[int, ...]  # [x] — у скількох бакетах екстремум стояв у слоті x
    hold_n: Tuple[Tuple[int, ...], ...]  # [k][x] — бакетів, де станом на слот k екстремум стояв у слоті x
    hold_ok: Tuple[Tuple[int, ...], ...]  # [k][x] — з них екстремум бакета так і лишився тим самим


@dataclasses.dataclass(frozen=True)
class CalendarProfile:
    tf_s: int
    buckets: int  # бакетів у вибірці
    first_bucket_ms: Optional[int]  # ключ найстаршого бакета вибірки
    sides: Dict[str, SideProfile]


@dataclasses.dataclass(frozen=True)
class LiveSide:
    """Поточний бакет: слот, у якому стоїть екстремум, ціна і поточний слот (сьогодні)."""

    side: str
    slot: int
    price: float
    current_slot: int


def day_slot(day: DayBar, tf_s: int, rule: str) -> Optional[int]:
    """Слот D1 у бакеті: W1 — день тижня Пн=0…Пт=4; MN — тиждень місяця 0…4. Торгова дата на вихідних — None."""
    date = trading_date(day.open_ms, rule)
    if date.weekday() >= 5:
        return None
    if tf_s == W1_S:
        return date.weekday()
    if tf_s == MN_S:
        return min((date.day - 1) // 7, SLOT_COUNT - 1)
    raise ValueError("calendar profile: unknown tf_s=%r" % (tf_s,))


def build_profile(days: Sequence[DayBar], *, tf_s: int, rule: str, now_ms: int, since_ms: int) -> CalendarProfile:
    """Профіль з D1 (зростання open_ms): лише закінчені «чисті» бакети з ключем ≥ `since_ms` (`d1_policy.history_from`)
    і після епохи старої конвенції ключів, якщо вона в символу є (`OLD_KEY_ERA_MIN_WEEKEND_DAYS`)."""
    since_ms = max(since_ms, _after_old_key_era_ms(days, rule))
    counts = {side: [0] * SLOT_COUNT for side in _SIDES}
    hold_n = {side: [[0] * SLOT_COUNT for _ in range(SLOT_COUNT)] for side in _SIDES}
    hold_ok = {side: [[0] * SLOT_COUNT for _ in range(SLOT_COUNT)] for side in _SIDES}
    buckets = 0
    first_key: Optional[int] = None
    for key, slotted in _clean_buckets(days, tf_s, rule, now_ms, since_ms):
        buckets += 1
        first_key = key if first_key is None else first_key
        for side in _SIDES:
            final_slot, _price = _extreme(slotted, side)
            counts[side][final_slot] += 1
            for k in range(SLOT_COUNT):
                upto = [(s, d) for s, d in slotted if s <= k]
                if not upto:
                    continue
                slot_k, price_k = _extreme(upto, side)
                later = [d for s, d in slotted if s > k]
                held = not any(_beyond(d, price_k, side) for d in later)
                hold_n[side][k][slot_k] += 1
                hold_ok[side][k][slot_k] += int(held)
    sides = {
        side: SideProfile(
            slot_counts=tuple(counts[side]),
            hold_n=tuple(tuple(row) for row in hold_n[side]),
            hold_ok=tuple(tuple(row) for row in hold_ok[side]),
        )
        for side in _SIDES
    }
    return CalendarProfile(tf_s=tf_s, buckets=buckets, first_bucket_ms=first_key, sides=sides)


def live_sides(current_days: Sequence[DayBar], *, tf_s: int, rule: str) -> Dict[str, LiveSide]:
    """Поточний бакет (його D1 разом із формуючою): де стоять хай і лой станом на останній D1. Порожньо — нема D1."""
    slotted = [(s, d) for s, d in ((day_slot(d, tf_s, rule), d) for d in current_days) if s is not None]
    if not slotted:
        return {}
    current = max(s for s, _ in slotted)
    out: Dict[str, LiveSide] = {}
    for side in _SIDES:
        slot, price = _extreme(slotted, side)
        out[side] = LiveSide(side=side, slot=slot, price=price, current_slot=current)
    return out


def wilson_interval(successes: int, n: int, z: float = WILSON_Z_95) -> Tuple[float, float]:
    """95 % інтервал Вілсона для частки successes/n (0 ≤ successes ≤ n, n > 0)."""
    if n <= 0 or not 0 <= successes <= n:
        raise ValueError("wilson_interval: successes=%r n=%r" % (successes, n))
    p = successes / n
    denom = 1.0 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, center - half), min(1.0, center + half)


def _after_old_key_era_ms(days: Sequence[DayBar], rule: str) -> int:
    """Перший момент після епохи старої конвенції ключів (≥ порога D1 на вихідних); епохи нема — 0."""
    weekend = [d.open_ms for d in days if trading_date(d.open_ms, rule).weekday() >= 5]
    return weekend[-1] + 1 if len(weekend) >= OLD_KEY_ERA_MIN_WEEKEND_DAYS else 0


def _clean_buckets(days, tf_s, rule, now_ms, since_ms):
    """(ключ, [(слот, D1)]) закінчених бакетів, усі D1 яких мають торгову дату Пн–Пт; ключ ≥ since_ms."""
    groups: Dict[int, List[DayBar]] = {}
    order: List[int] = []
    for day in days:
        key = calendar_bucket_start_ms(day.open_ms, tf_s, rule)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(day)
    for key in order:
        if key < since_ms or calendar_next_bucket_start_ms(key, tf_s, rule) > now_ms:
            continue
        slotted = [(day_slot(d, tf_s, rule), d) for d in groups[key]]
        if any(s is None for s, _ in slotted):
            continue
        yield key, slotted


def _extreme(slotted, side):
    """(слот, ціна) першого D1 з максимумом high (хай) або мінімумом low (лой)."""
    best_slot, best = slotted[0][0], slotted[0][1]
    for slot, day in slotted[1:]:
        if _beyond(day, best.h if side == HIGH else best.low, side):
            best_slot, best = slot, day
    return best_slot, (best.h if side == HIGH else best.low)


def _beyond(day: DayBar, price: float, side: str) -> bool:
    return day.h > price if side == HIGH else day.low < price


# ── Тексти розділу «Розбору» (X28: UI лише показує; ADR-0113 R8 — «історично…», без «шансу») ──────────────────────
# Мова — перекладач `tr` (ADR-0116): без нього — джерело (uk) байт у байт. Фрази цілі, окремо на TF і бік: відмінки й
# порядок слів у мовах різні, тож складати речення зі слів, як до мовного шару, не можна.

_WORDS = {
    W1_S: {
        "tf": "W1", "title": msg("Профіль тижня"),
        "buckets": lambda tr, n: tr.plural(n, "{count} тиждень|{count} тижні|{count} тижнів", count=_count(n)),
        "slots": (msg("Пн"), msg("Вт"), msg("Ср"), msg("Чт"), msg("Пт")),
        "since": (msg("з понеділка"), msg("з вівторка"), msg("із середи"), msg("з четверга"), msg("з п'ятниці")),
        "as_of": (msg("понеділок"), msg("вівторок"), msg("середу"), msg("четвер"), msg("п'ятницю")),
        "sides": {
            HIGH: {
                "live": msg("Хай тижня поки {since} — {price}"),
                "condition": msg("станом на {as_of} хай тижня стояв {since}"),
                "held": msg("Історично, коли {condition}, він лишився хаєм тижня в {pct} % тижнів ({lo}–{hi} %, "
                            "вибірка {sample})"),
                "head": msg("Хай тижня — у який день"),
            },
            LOW: {
                "live": msg("Лой тижня поки {since} — {price}"),
                "condition": msg("станом на {as_of} лой тижня стояв {since}"),
                "held": msg("Історично, коли {condition}, він лишився лоєм тижня в {pct} % тижнів ({lo}–{hi} %, "
                            "вибірка {sample})"),
                "head": msg("Лой тижня — у який день"),
            },
        },
    },
    MN_S: {
        "tf": "MN", "title": msg("Профіль місяця"),
        "buckets": lambda tr, n: tr.plural(n, "{count} місяць|{count} місяці|{count} місяців", count=_count(n)),
        "slots": (msg("1–7"), msg("8–14"), msg("15–21"), msg("22–28"), msg("29–31")),
        "since": (msg("з першого тижня (1–7)"), msg("з другого тижня (8–14)"), msg("з третього тижня (15–21)"),
                  msg("з четвертого тижня (22–28)"), msg("з останніх днів (29–31)")),
        "as_of": (msg("перший тиждень"), msg("другий тиждень"), msg("третій тиждень"), msg("четвертий тиждень"),
                  msg("останні дні")),
        "sides": {
            HIGH: {
                "live": msg("Хай місяця поки {since} — {price}"),
                "condition": msg("станом на {as_of} хай місяця стояв {since}"),
                "held": msg("Історично, коли {condition}, він лишився хаєм місяця в {pct} % місяців ({lo}–{hi} %, "
                            "вибірка {sample})"),
                "head": msg("Хай місяця — у які числа"),
            },
            LOW: {
                "live": msg("Лой місяця поки {since} — {price}"),
                "condition": msg("станом на {as_of} лой місяця стояв {since}"),
                "held": msg("Історично, коли {condition}, він лишився лоєм місяця в {pct} % місяців ({lo}–{hi} %, "
                            "вибірка {sample})"),
                "head": msg("Лой місяця — у які числа"),
            },
        },
    },
}


def compose_profile_section(
    profile: CalendarProfile,
    live: Dict[str, LiveSide],
    *,
    price_digits: int,
    since_year: Optional[int],
    tr: Translator = SOURCE_TRANSLATOR,
) -> BriefingSection:
    """Розділ «Профіль тижня / місяця» мовою `tr`: живі рядки хаю й лою (якщо є поточний бакет) і два розподіли."""
    words = _WORDS[profile.tf_s]
    rows: List[BriefingRow] = []
    for side in _SIDES:
        if side in live:
            rows.append(_live_row(profile, live[side], words, price_digits, tr))
    for side in _SIDES:
        rows.append(_distribution_row(profile, side, words, since_year, tr))
    return BriefingSection(SECTION_KEY, tr(words["title"]), tuple(rows))


def _live_row(profile: CalendarProfile, state: LiveSide, words, digits: int, tr: Translator) -> BriefingRow:
    phrases = words["sides"][state.side]
    since = tr(words["since"][state.slot])
    text = tr(phrases["live"], since=since, price=fmt_price(state.price, digits))
    if state.current_slot >= SLOT_COUNT - 1:
        return BriefingRow(words["tf"], text)
    side = profile.sides[state.side]
    n = side.hold_n[state.current_slot][state.slot]
    ok = side.hold_ok[state.current_slot][state.slot]
    condition = tr(phrases["condition"], as_of=tr(words["as_of"][state.current_slot]), since=since)
    if n < MIN_SAMPLE:
        note = tr("Історично бакетів, де {condition}, замало для частки (вибірка {sample})", condition=condition,
                  sample=n)
    else:
        lo, hi = wilson_interval(ok, n)
        note = tr(phrases["held"], condition=condition, pct=round(100 * ok / n), lo=round(100 * lo),
                  hi=round(100 * hi), sample=_count(n))
    return BriefingRow(words["tf"], text, (note,))


def _distribution_row(
    profile: CalendarProfile, side: str, words, since_year: Optional[int], tr: Translator
) -> BriefingRow:
    n = profile.buckets
    head = tr(words["sides"][side]["head"])
    buckets = words["buckets"](tr, n)
    if n < MIN_SAMPLE:
        return BriefingRow(words["tf"], head, (tr("Вибірка замала ({buckets})", buckets=buckets),))
    counts = profile.sides[side].slot_counts
    parts = " · ".join("%s %d" % (tr(label), round(100 * c / n)) for label, c in zip(words["slots"], counts))
    half = max((hi - lo) / 2 for lo, hi in (wilson_interval(c, n) for c in counts))
    interval = max(1, round(100 * half))
    if since_year is None:
        note = tr("{buckets}, 95 % інтервал до ±{pp} п.п.", buckets=buckets, pp=interval)
    else:
        note = tr("{buckets} з {year}, 95 % інтервал до ±{pp} п.п.", buckets=buckets, year=since_year, pp=interval)
    return BriefingRow(words["tf"], tr("{head}: {parts} %", head=head, parts=parts), (note,))


def _count(n: int) -> str:
    return format(n, ",").replace(",", "\u00a0")  # нерозривний пробіл між тисячами: «1 880» не рветься
