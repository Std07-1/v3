"""Розбір символу (ADR-0113 Ф3) — один на символ, складений сервером з того, що платформа вже рахує.

Напрям TF стрічки, контекст старшого TF (зона й ціль ліквідності), сетапи TF виконання (зона, стан, стоп, ціль, R:R
від краю зони); ліквідність поруч і сесія — окремими секціями від runner. Кожен рядок каже, з якого він TF. Розбір —
композиція готових `NarrativeBlock` різних TF глядача: нового судження про ринок тут немає (R6, R9).

Свідомо без win-rate (журнал точності — Ф4) і без «що змінилось» (потребує пам'яті — Ф2b). Тексти — з бекенду (X28),
UI лише показує рядки; tone — лише колір. S0: pure, без I/O.
"""
from __future__ import annotations

import dataclasses
import time
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from core.smc.liquidity_sweep import FirstReached
from core.smc.narrative import (EQ_LEVEL_KINDS, LIQUIDITY_KINDS_ABOVE, LIQUIDITY_KINDS_BELOW, LIQUIDITY_LABELS,
                                fmt_price, resolve_htf_alignment, tf_to_label)
from core.smc.sessions import OFF_SESSION
from core.smc.types import LEVEL_STATE_FORMING, ActiveScenario, NarrativeBlock, SmcLevel
from core.smc.zone_path import LONG

TONE_BULL, TONE_BEAR, TONE_WARN = "bull", "bear", "warn"
_DEFAULT_PRICE_DIGITS = 2
_ARROWS = {"bullish": "↑", "bearish": "↓"}
_CONTEXT_BIAS = {"long": "Бичачий", "short": "Ведмежий"}
_DIRECTION_TONE = {"long": TONE_BULL, "short": TONE_BEAR, "bullish": TONE_BULL, "bearish": TONE_BEAR}
_SIDE = {"long": "BUY", "short": "SELL"}
_HTF_WORDS = {"bullish": "вгору", "bearish": "вниз"}
_PULLBACK_INSIDE = {"bullish": "зростання", "bearish": "падіння"}
_CLOSED_TEXT = "Ринок закрито — розбір оновиться з відкриттям"


@dataclasses.dataclass(frozen=True)
class BriefingRow:
    tf: str  # з якого TF рядок ("H4"); "" — рядок не з одного TF (стрічка напряму, сесія)
    text: str
    notes: Tuple[str, ...] = ()  # другорядні рядки: стан, стоп · ціль · R:R, межа контексту
    tone: str = ""  # bull | bear | warn | "" — лише колір у UI

    def to_wire(self) -> Dict[str, Any]:
        return {"tf": self.tf, "text": self.text, "notes": list(self.notes), "tone": self.tone}


@dataclasses.dataclass(frozen=True)
class BriefingSection:
    key: str  # bias | context | setups | liquidity | session | state
    title: str
    rows: Tuple[BriefingRow, ...]

    def to_wire(self) -> Dict[str, Any]:
        return {"key": self.key, "title": self.title, "rows": [row.to_wire() for row in self.rows]}


@dataclasses.dataclass(frozen=True)
class Briefing:
    symbol: str
    as_of_ms: int  # момент розбору — кінець бару, на якому його складено
    sections: Tuple[BriefingSection, ...]
    warnings: Tuple[str, ...] = ()

    def to_wire(self) -> Dict[str, Any]:
        return {"symbol": self.symbol, "as_of_ms": self.as_of_ms,
                "sections": [section.to_wire() for section in self.sections], "warnings": list(self.warnings)}


def compose_briefing(symbol, as_of_ms, bias_map, narratives, strip_tfs, context_tfs, setup_tfs, price_digits,
                     tail_sections=()):
    # type: (str, int, Mapping[str, str], Mapping[int, Optional[NarrativeBlock]], Sequence[int], Sequence[int], Sequence[int], Optional[int], Sequence[BriefingSection]) -> Briefing
    """Розбір із наративів TF глядача (`narratives`: tf_s → блок; None — блоку немає). Старші TF — першими."""
    warnings = [] if price_digits is not None else ["price_digits_missing"]
    digits = _DEFAULT_PRICE_DIGITS if price_digits is None else price_digits
    for tf_s in list(context_tfs) + list(setup_tfs):
        if narratives.get(tf_s) is None:
            warnings.append("narrative_unavailable:" + tf_to_label(tf_s))
    sections = [BriefingSection("bias", "Напрям", (_bias_row(bias_map, strip_tfs),))]
    context_rows = tuple(_context_row(tf_s, narratives.get(tf_s)) for tf_s in sorted(context_tfs, reverse=True))
    if context_rows:
        sections.append(BriefingSection("context", "Контекст старшого TF", context_rows))
    setup_rows = tuple(row for tf_s in sorted(setup_tfs, reverse=True)
                       for row in _setup_rows(tf_s, narratives.get(tf_s), digits))
    if setup_rows:
        sections.append(BriefingSection("setups", "Сетапи", setup_rows))
    return Briefing(symbol, as_of_ms, tuple(sections) + tuple(tail_sections), tuple(warnings))


def closed_briefing(symbol, as_of_ms):
    # type: (str, int) -> Briefing
    """Ринок закрито за календарем символу — розбору немає, і це сказано прямо."""
    return Briefing(symbol, as_of_ms, (BriefingSection("state", "Стан", (BriefingRow("", _CLOSED_TEXT),)),),
                    ("market_closed",))


def _bias_row(bias_map, strip_tfs):
    # type: (Mapping[str, str], Sequence[int]) -> BriefingRow
    """Напрям кожного TF стрічки одним рядком + узгодженість D1↔H4 — явний стан, не «вирішення» (§1.4)."""
    parts = ["{} {}".format(tf_to_label(tf_s), _ARROWS.get(bias_map.get(str(tf_s), ""), "—"))
             for tf_s in strip_tfs]
    alignment, direction = resolve_htf_alignment(dict(bias_map))
    d1, h4 = bias_map.get("86400"), bias_map.get("14400")
    if alignment == "aligned":
        note, tone = "D1 і H4 {} — старший напрям узгоджений".format(_HTF_WORDS[direction]), _DIRECTION_TONE[direction]
    elif alignment == "mixed":
        note = "D1 {}, H4 {} — це відкат усередині {} D1".format(_HTF_WORDS[d1], _HTF_WORDS[h4], _PULLBACK_INSIDE[d1])
        tone = TONE_WARN
    elif alignment == "partial":
        known, missing = ("D1", "H4") if d1 else ("H4", "D1")
        note, tone = "{} {}, {} ще без структури".format(known, _HTF_WORDS[direction], missing), ""
    else:
        note, tone = "D1 і H4 ще без структури", ""
    if any(str(tf_s) not in bias_map for tf_s in strip_tfs):
        note += " · «—» — TF ще без структури"
    return BriefingRow("", " · ".join(parts), (note,), tone)


def _context_row(tf_s, block):
    # type: (int, Optional[NarrativeBlock]) -> BriefingRow
    """Старший TF: напрям, ключова зона, ціль ліквідності й межа — з контексту наративу (як у картці H4/D1)."""
    label = tf_to_label(tf_s)
    if block is None or block.htf_context is None:
        return _state_row(label, block)
    context = block.htf_context
    notes = (("Ціль ліквідності: " + context.target_desc,) if context.target_desc else ()) + (context.invalidation,)
    text = "{} контекст — {}".format(_CONTEXT_BIAS[context.direction], context.zone_desc)
    return BriefingRow(label, text, notes, _DIRECTION_TONE[context.direction])


def _setup_rows(tf_s, block, digits):
    # type: (int, Optional[NarrativeBlock], int) -> Tuple[BriefingRow, ...]
    label = tf_to_label(tf_s)
    if block is None or not block.scenarios:
        return (_state_row(label, block),)
    return tuple(_scenario_row(label, tf_s, scenario, index, digits) for index, scenario in enumerate(block.scenarios))


def _scenario_row(label, tf_s, scenario, index, digits):
    # type: (str, int, ActiveScenario, int, int) -> BriefingRow
    """Сетап: бік і зона входу; стан; стоп · ціль · R:R від краю зони — ті самі числа, що в тексті сценарію."""
    zone_tf = scenario.zone_tf_s
    text = "{} · {}".format(_SIDE[scenario.direction], scenario.entry_desc)
    if zone_tf is not None and zone_tf != tf_s:
        text += " (зона {})".format(tf_to_label(zone_tf))
    if index:
        text += " — друга зона"
    state = ("Наближення — " + scenario.trigger_desc) if scenario.trigger == "approaching" else scenario.trigger_desc
    plan = []
    if scenario.stop_price is not None:
        plan.append("Стоп " + fmt_price(scenario.stop_price, digits))
    plan.append("ціль " + scenario.target_desc if scenario.target_desc else "ціль не знайдено")
    rr = reward_to_risk(scenario.direction, scenario.entry_edge, scenario.stop_price, scenario.target_price)
    if rr is not None:
        plan.append("R:R {:.1f} від краю зони".format(rr))
    return BriefingRow(label, text, (state, " · ".join(plan)), _DIRECTION_TONE[scenario.direction])


def reward_to_risk(direction, entry_edge, stop_price, target_price):
    # type: (str, Optional[float], Optional[float], Optional[float]) -> Optional[float]
    """R:R від краю входу: (ціль − край) / (край − стоп) у напрямку сценарію; None — якогось числа нема чи ціль не попереду."""
    if entry_edge is None or stop_price is None or target_price is None:
        return None
    sign = 1.0 if direction == LONG else -1.0
    risk, reward = (entry_edge - stop_price) * sign, (target_price - entry_edge) * sign
    return reward / risk if risk > 0 and reward > 0 else None


def _state_row(label, block):
    # type: (str, Optional[NarrativeBlock]) -> BriefingRow
    """TF без сценарію чи контексту — його стан словами наративу і найближча зона уваги."""
    if block is None:
        return BriefingRow(label, "Розбору цього TF ще немає", (), TONE_WARN)
    notes = ("Найближча зона: " + block.next_area,) if block.next_area else ()
    return BriefingRow(label, _plain(block.headline), notes)


def _plain(headline):
    # type: (str) -> str
    """Заголовок без маркера стану на початку (емодзі ще в даних наративу — K13): у розборі тон несе колір рядка."""
    head, sep, rest = headline.partition(" ")
    return rest if sep and head and not any(ch.isalnum() for ch in head) else headline


# ── Ліквідність поруч і сесія (п. 4–5) ──────────────────────

_SESSION_NAMES = {"asia": "Азія", "london": "Лондон", "newyork": "Нью-Йорк"}
_WEEKDAYS = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Нд")
_MINUTE_MS = 60_000


def liquidity_section(tf_s, levels, reached, price, atr, price_digits, per_side):
    # type: (int, Sequence[SmcLevel], Mapping[str, FirstReached], float, float, Optional[int], int) -> BriefingSection
    """Найближча НЕзнята ліквідність: хаї над ціною, лої під нею, з відстанню в ATR того самого TF.

    Ті самі рівні, що цілі наративу, і так само без тих, що формуються (H/L поточної доби чи сесії — рецензія Ф0, D-01).
    Знята — ціна торгувала строго за рівнем після початку його періоду (`reached`, core.smc.liquidity_sweep); зняті
    EQ-кластери рушій уже відсіяв. Рівні з однією ціною — один пул («PDH/хай Лондона»).
    """
    label, digits = tf_to_label(tf_s), _DEFAULT_PRICE_DIGITS if price_digits is None else price_digits
    rows = []
    for word, kinds, index, ahead in (("Вище", LIQUIDITY_KINDS_ABOVE, 0, lambda level_price: level_price > price),
                                      ("Нижче", LIQUIDITY_KINDS_BELOW, 1, lambda level_price: level_price < price)):
        pools = {}  # type: Dict[float, Dict[str, None]]
        for level in levels:
            if level.kind not in kinds or level.state == LEVEL_STATE_FORMING or not ahead(level.price):
                continue
            if level.kind not in EQ_LEVEL_KINDS and reached.get("level:" + level.id, (None, None))[index] is not None:
                continue
            pools.setdefault(level.price, {})[LIQUIDITY_LABELS.get(level.kind, level.kind.upper())] = None
        nearest = sorted(pools, key=lambda pool_price: abs(pool_price - price))[:per_side]
        parts = ["{} {}{}".format("/".join(pools[pool_price]), fmt_price(pool_price, digits),
                                  " · {:.1f} ATR".format(abs(pool_price - price) / atr) if atr > 0 else "")
                 for pool_price in nearest]
        rows.append(BriefingRow(label, "{}: {}".format(word, ", ".join(parts) or "незнятої ліквідності не видно")))
    return BriefingSection("liquidity", "Ліквідність поруч", tuple(rows))


def session_section(as_of_ms, current, in_killzone, upcoming):
    # type: (int, str, bool, Optional[Tuple[str, int]]) -> BriefingSection
    """Поточна сесія й кілзона за календарем платформи (сезонним, §3.7 п.3) і скільки до наступного відкриття."""
    if current and current != OFF_SESSION:
        text = "{} — {}".format(_SESSION_NAMES.get(current, current), "кілзона" if in_killzone else "поза кілзоною")
    else:
        text = "Поза торговими сесіями"
    notes = ()  # type: Tuple[str, ...]
    if upcoming is not None:
        name, open_ms = upcoming
        notes = ("Далі: {} {} UTC — через {}".format(
            _SESSION_NAMES.get(name, name), _clock(open_ms, as_of_ms), _duration(open_ms - as_of_ms)),)
    return BriefingSection("session", "Сесія", (BriefingRow("", text, notes),))


def _clock(moment_ms, as_of_ms):
    # type: (int, int) -> str
    """«о 12:00» того самого дня UTC; інший день — з днем тижня («Пн 00:00»)."""
    moment, as_of = time.gmtime(moment_ms // 1000), time.gmtime(as_of_ms // 1000)
    hhmm = time.strftime("%H:%M", moment)
    return "о " + hhmm if moment[:3] == as_of[:3] else "{} {}".format(_WEEKDAYS[moment.tm_wday], hhmm)


def _duration(delta_ms):
    # type: (int) -> str
    minutes = max(0, delta_ms) // _MINUTE_MS
    days, hours, mins = minutes // 1440, minutes % 1440 // 60, minutes % 60
    if days:
        return "{} д {} год".format(days, hours)
    if hours:
        return "{} год {} хв".format(hours, mins) if mins else "{} год".format(hours)
    return "{} хв".format(mins)
