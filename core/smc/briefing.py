"""Розбір символу (ADR-0113 Ф3) — один на символ, складений сервером з того, що платформа вже рахує.

Напрям TF стрічки, контекст старшого TF (зона й ціль ліквідності), сетапи TF виконання (зона, стан, стоп, ціль, R:R
від краю зони); ліквідність поруч і сесія — окремими секціями від runner. Кожен рядок каже, з якого він TF. Розбір —
композиція готових `NarrativeBlock` різних TF глядача: нового судження про ринок тут немає (R6, R9).

Свідомо без win-rate (журнал точності — Ф4) і без «що змінилось» (потребує пам'яті — Ф2b). Тексти — з бекенду (X28),
UI лише показує рядки; tone — лише колір. S0: pure, без I/O.
"""
from __future__ import annotations

import dataclasses
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from core.smc.narrative import fmt_price, resolve_htf_alignment, tf_to_label
from core.smc.types import ActiveScenario, NarrativeBlock
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
