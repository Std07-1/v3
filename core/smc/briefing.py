"""Розбір символу (ADR-0113 Ф3) — один на символ, складений сервером з того, що платформа вже рахує.

Напрям TF стрічки, контекст старшого TF (зона й ціль ліквідності), сетапи TF виконання (зона, стан, стоп, ціль, R:R
від краю зони); ліквідність поруч і сесія — окремими секціями від runner. Кожен рядок каже, з якого він TF. Розбір —
композиція готових `NarrativeBlock` різних TF глядача: нового судження про ринок тут немає (R6, R9).

Свідомо без win-rate (журнал точності — Ф4) і без «що змінилось» (потребує пам'яті — Ф2b). Тексти — з бекенду (X28),
UI лише показує рядки; tone — лише колір. S0: pure, без I/O.

Мова тексту — перекладач `tr` (ADR-0116): без нього — джерело (uk), байт у байт як до мовного шару. Фрази — цілими
реченнями з плейсхолдерами, бо відмінки й порядок слів у мовах різні; числа форматуються до підстановки.
"""
from __future__ import annotations

import dataclasses
import time
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from core.i18n import SOURCE_TRANSLATOR, Translator, msg
from core.smc.liquidity_sweep import FirstReached
from core.smc.narrative import (EQ_LEVEL_KINDS, LIQUIDITY_KINDS_ABOVE, LIQUIDITY_KINDS_BELOW, fmt_price, level_label,
                                resolve_htf_alignment, tf_to_label)
from core.smc.sessions import OFF_SESSION
from core.smc.types import LEVEL_STATE_FORMING, ActiveScenario, NarrativeBlock, SmcLevel
from core.smc.zone_path import LONG

TONE_BULL, TONE_BEAR, TONE_WARN = "bull", "bear", "warn"
_DEFAULT_PRICE_DIGITS = 2
_ARROWS = {"bullish": "↑", "bearish": "↓"}
_CONTEXT_TEXT = {"long": msg("Бичачий контекст — {zone}"), "short": msg("Ведмежий контекст — {zone}")}
_DIRECTION_TONE = {"long": TONE_BULL, "short": TONE_BEAR, "bullish": TONE_BULL, "bearish": TONE_BEAR}
_SIDE = {"long": "BUY", "short": "SELL"}
_HTF_WORDS = {"bullish": msg("вгору"), "bearish": msg("вниз")}
_CLOSED_TEXT = msg("Ринок закрито — розбір оновиться з відкриттям")


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
                     tail_sections=(), atr_by_tf=None, rr_min_risk_atr=0.0, tr=SOURCE_TRANSLATOR):
    # type: (str, int, Mapping[str, str], Mapping[int, Optional[NarrativeBlock]], Sequence[int], Sequence[int], Sequence[int], Optional[int], Sequence[BriefingSection], Optional[Mapping[int, float]], float, Translator) -> Briefing
    """Розбір із наративів TF глядача (`narratives`: tf_s → блок; None — блоку немає). Старші TF — першими.

    `atr_by_tf` — виміряний ATR TF глядача (той самий, що в його наративі); `rr_min_risk_atr` — поріг «стоп у шумі
    бару»: ризик від краю входу до стопу менший за нього × ATR — R:R не показується (`smc.briefing`).
    """
    warnings = [] if price_digits is not None else ["price_digits_missing"]
    digits = _DEFAULT_PRICE_DIGITS if price_digits is None else price_digits
    for tf_s in list(context_tfs) + list(setup_tfs):
        if narratives.get(tf_s) is None:
            warnings.append("narrative_unavailable:" + tf_to_label(tf_s))
    sections = [BriefingSection("bias", tr("Напрям"), (_bias_row(bias_map, strip_tfs, tr),))]
    context_rows = tuple(_context_row(tf_s, narratives.get(tf_s), tr) for tf_s in sorted(context_tfs, reverse=True))
    if context_rows:
        sections.append(BriefingSection("context", tr("Контекст старшого TF"), context_rows))
    setup_rows = tuple(row for tf_s in sorted(setup_tfs, reverse=True)
                       for row in _setup_rows(tf_s, narratives.get(tf_s), digits, (atr_by_tf or {}).get(tf_s, 0.0),
                                              rr_min_risk_atr, tr))
    if setup_rows:
        sections.append(BriefingSection("setups", tr("Сетапи"), setup_rows))
    return Briefing(symbol, as_of_ms, tuple(sections) + tuple(tail_sections), tuple(warnings))


def closed_briefing(symbol, as_of_ms, tr=SOURCE_TRANSLATOR):
    # type: (str, int, Translator) -> Briefing
    """Ринок закрито за календарем символу — розбору немає, і це сказано прямо."""
    return Briefing(symbol, as_of_ms, (BriefingSection("state", tr("Стан"), (BriefingRow("", tr(_CLOSED_TEXT)),)),),
                    ("market_closed",))


def _bias_row(bias_map, strip_tfs, tr):
    # type: (Mapping[str, str], Sequence[int], Translator) -> BriefingRow
    """Напрям кожного TF стрічки одним рядком + узгодженість D1↔H4 — явний стан, не «вирішення» (§1.4)."""
    parts = ["{} {}".format(tf_to_label(tf_s), _ARROWS.get(bias_map.get(str(tf_s), ""), "—"))
             for tf_s in strip_tfs]
    alignment, direction = resolve_htf_alignment(dict(bias_map))
    d1, h4 = bias_map.get("86400"), bias_map.get("14400")
    if alignment == "aligned":
        note = tr("D1 і H4 {direction} — старший напрям узгоджений", direction=tr(_HTF_WORDS[direction]))
        tone = _DIRECTION_TONE[direction]
    elif alignment == "mixed":
        # Стан, не висновок: H4 проти D1 буває і відкатом, і початком розвороту (власник 16.10)
        note = tr("D1 {d1}, H4 {h4} — старший напрям змішаний", d1=tr(_HTF_WORDS[d1]), h4=tr(_HTF_WORDS[h4]))
        tone = TONE_WARN
    elif alignment == "partial":
        known, missing = ("D1", "H4") if d1 else ("H4", "D1")
        note = tr("{known} {direction}, {missing} ще без структури", known=known, direction=tr(_HTF_WORDS[direction]),
                  missing=missing)
        tone = ""
    else:
        note, tone = tr("D1 і H4 ще без структури"), ""
    if any(str(tf_s) not in bias_map for tf_s in strip_tfs):
        note += " · " + tr("«—» — TF ще без структури")
    return BriefingRow("", " · ".join(parts), (note,), tone)


def _context_row(tf_s, block, tr):
    # type: (int, Optional[NarrativeBlock], Translator) -> BriefingRow
    """Старший TF: напрям, ключова зона, ціль ліквідності й межа — з контексту наративу (як у картці H4/D1)."""
    label = tf_to_label(tf_s)
    if block is None or block.htf_context is None:
        return _state_row(label, block, tr)
    context = block.htf_context
    notes = ((tr("Ціль ліквідності: {target}", target=context.target_desc),) if context.target_desc else ())
    notes += (context.invalidation,)
    text = tr(_CONTEXT_TEXT[context.direction], zone=context.zone_desc)
    return BriefingRow(label, text, notes, _DIRECTION_TONE[context.direction])


def _setup_rows(tf_s, block, digits, atr, rr_min_risk_atr, tr):
    # type: (int, Optional[NarrativeBlock], int, float, float, Translator) -> Tuple[BriefingRow, ...]
    label = tf_to_label(tf_s)
    if block is None or not block.scenarios:
        return (_state_row(label, block, tr),)
    return tuple(_scenario_row(label, tf_s, scenario, index, digits, atr, rr_min_risk_atr, tr)
                 for index, scenario in enumerate(block.scenarios))


def _scenario_row(label, tf_s, scenario, index, digits, atr, rr_min_risk_atr, tr):
    # type: (str, int, ActiveScenario, int, int, float, float, Translator) -> BriefingRow
    """Сетап: бік і зона входу; стан; стоп · ціль · R:R від краю зони — ті самі числа, що в тексті сценарію.

    Стоп ближче за `rr_min_risk_atr` × ATR TF глядача — у шумі одного бару: R:R від такого стопу — не оцінка угоди, а
    наслідок тонкої зони (XAU H1 15.10: зона 0.31, R:R 33.0), тож замість числа — чому його немає (R3). ATR не виміряно —
    поріг не діє.
    """
    zone_tf = scenario.zone_tf_s
    text = "{} · {}".format(_SIDE[scenario.direction], scenario.entry_desc)
    if zone_tf is not None and zone_tf != tf_s:
        text += " " + tr("(зона {tf})", tf=tf_to_label(zone_tf))
    if index:
        text += " — " + tr("друга зона")
    state = (tr("Наближення — {trigger}", trigger=scenario.trigger_desc) if scenario.trigger == "approaching"
             else scenario.trigger_desc)
    plan = []
    if scenario.stop_price is not None:
        plan.append(tr("Стоп {price}", price=fmt_price(scenario.stop_price, digits)))
    plan.append(tr("ціль {target}", target=scenario.target_desc) if scenario.target_desc else tr("ціль не знайдено"))
    rr = reward_to_risk(scenario.direction, scenario.entry_edge, scenario.stop_price, scenario.target_price)
    if rr is not None and atr > 0 and abs(scenario.entry_edge - scenario.stop_price) < rr_min_risk_atr * atr:
        plan.append(tr("R:R не показано — стоп ближче {limit} ATR {tf}", limit="{:g}".format(rr_min_risk_atr), tf=label))
    elif rr is not None:
        plan.append(tr("R:R {rr} від краю зони", rr="{:.1f}".format(rr)))
    return BriefingRow(label, text, (state, " · ".join(plan)), _DIRECTION_TONE[scenario.direction])


def reward_to_risk(direction, entry_edge, stop_price, target_price):
    # type: (str, Optional[float], Optional[float], Optional[float]) -> Optional[float]
    """R:R від краю входу: (ціль − край) / (край − стоп) у напрямку сценарію; None — якогось числа нема чи ціль не попереду."""
    if entry_edge is None or stop_price is None or target_price is None:
        return None
    sign = 1.0 if direction == LONG else -1.0
    risk, reward = (entry_edge - stop_price) * sign, (target_price - entry_edge) * sign
    return reward / risk if risk > 0 and reward > 0 else None


def _state_row(label, block, tr):
    # type: (str, Optional[NarrativeBlock], Translator) -> BriefingRow
    """TF без сценарію чи контексту — його стан словами наративу і найближча зона уваги."""
    if block is None:
        return BriefingRow(label, tr("Розбору цього TF ще немає"), (), TONE_WARN)
    notes = (tr("Найближча зона: {zone}", zone=block.next_area),) if block.next_area else ()
    return BriefingRow(label, _plain(block.headline), notes)


def _plain(headline):
    # type: (str) -> str
    """Заголовок без маркера стану на початку (емодзі ще в даних наративу — K13): у розборі тон несе колір рядка."""
    head, sep, rest = headline.partition(" ")
    return rest if sep and head and not any(ch.isalnum() for ch in head) else headline


# ── Ліквідність поруч і сесія (п. 4–5) ──────────────────────

_SESSION_NAMES = {"asia": msg("Азія"), "london": msg("Лондон"), "newyork": msg("Нью-Йорк")}
_WEEKDAYS = (msg("Пн"), msg("Вт"), msg("Ср"), msg("Чт"), msg("Пт"), msg("Сб"), msg("Нд"))
_MINUTE_MS = 60_000


def liquidity_section(tf_s, levels, reached, price, atr, price_digits, per_side, tr=SOURCE_TRANSLATOR):
    # type: (int, Sequence[SmcLevel], Mapping[str, FirstReached], float, float, Optional[int], int, Translator) -> BriefingSection
    """Найближча НЕзнята ліквідність: хаї над ціною, лої під нею, з відстанню в ATR того самого TF.

    Ті самі рівні, що цілі наративу, і так само без тих, що формуються (H/L поточної доби чи сесії — рецензія Ф0, D-01).
    Знята — ціна торгувала строго за рівнем після початку його періоду (`reached`, core.smc.liquidity_sweep); зняті
    EQ-кластери рушій уже відсіяв. Рівні з однією ціною — один пул («PDH/хай Лондона»).
    """
    label, digits = tf_to_label(tf_s), _DEFAULT_PRICE_DIGITS if price_digits is None else price_digits
    rows = []
    for line, kinds, index, ahead in ((msg("Вище: {pools}"), LIQUIDITY_KINDS_ABOVE, 0, lambda level_price: level_price > price),
                                      (msg("Нижче: {pools}"), LIQUIDITY_KINDS_BELOW, 1, lambda level_price: level_price < price)):
        pools = {}  # type: Dict[float, Dict[str, None]]
        for level in levels:
            if level.kind not in kinds or level.state == LEVEL_STATE_FORMING or not ahead(level.price):
                continue
            if level.kind not in EQ_LEVEL_KINDS and reached.get("level:" + level.id, (None, None))[index] is not None:
                continue
            pools.setdefault(level.price, {})[level_label(level.kind, tr)] = None
        nearest = sorted(pools, key=lambda pool_price: abs(pool_price - price))[:per_side]
        parts = ["{} {}{}".format("/".join(pools[pool_price]), fmt_price(pool_price, digits),
                                  " · {:.1f} ATR".format(abs(pool_price - price) / atr) if atr > 0 else "")
                 for pool_price in nearest]
        rows.append(BriefingRow(label, tr(line, pools=", ".join(parts) or tr("незнятої ліквідності не видно"))))
    return BriefingSection("liquidity", tr("Ліквідність поруч"), tuple(rows))


def session_section(as_of_ms, current, in_killzone, upcoming, tr=SOURCE_TRANSLATOR):
    # type: (int, str, bool, Optional[Tuple[str, int]], Translator) -> BriefingSection
    """Поточна сесія й кілзона за календарем платформи (сезонним, §3.7 п.3) і скільки до наступного відкриття."""
    if current and current != OFF_SESSION:
        name = _session_name(current, tr)
        text = tr("{session} — кілзона", session=name) if in_killzone else tr("{session} — поза кілзоною", session=name)
    else:
        text = tr("Поза торговими сесіями")
    notes = ()  # type: Tuple[str, ...]
    if upcoming is not None:
        name, open_ms = upcoming
        notes = (tr("Далі: {session} {time} UTC — через {duration}", session=_session_name(name, tr),
                    time=_clock(open_ms, as_of_ms, tr), duration=_duration(open_ms - as_of_ms, tr)),)
    return BriefingSection("session", tr("Сесія"), (BriefingRow("", text, notes),))


def _session_name(code, tr):
    # type: (str, Translator) -> str
    return tr(_SESSION_NAMES[code]) if code in _SESSION_NAMES else code


def _clock(moment_ms, as_of_ms, tr):
    # type: (int, int, Translator) -> str
    """«о 12:00» того самого дня UTC; інший день — з днем тижня («Пн 00:00»)."""
    moment, as_of = time.gmtime(moment_ms // 1000), time.gmtime(as_of_ms // 1000)
    hhmm = time.strftime("%H:%M", moment)
    return tr("о {time}", time=hhmm) if moment[:3] == as_of[:3] else "{} {}".format(tr(_WEEKDAYS[moment.tm_wday]), hhmm)


def _duration(delta_ms, tr):
    # type: (int, Translator) -> str
    minutes = max(0, delta_ms) // _MINUTE_MS
    days, hours, mins = minutes // 1440, minutes % 1440 // 60, minutes % 60
    if days:
        return tr("{days} д {hours} год", days=days, hours=hours)
    if hours:
        return tr("{hours} год {minutes} хв", hours=hours, minutes=mins) if mins else tr("{hours} год", hours=hours)
    return tr("{minutes} хв", minutes=mins)
