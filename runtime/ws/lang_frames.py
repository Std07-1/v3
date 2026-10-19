"""Мова з'єднання WS (ADR-0116 Ф2.2): яку мову просить глядач і як кадр з текстами аналізу стає кадром його мови.

- `/ws?lang=` і дія `set_lang` дають `WsSession.lang`: код мови з каталогу або None. None означає, що з'єднання мови
  не просило. Тоді кадри байт у байт як до мовного шару (L4: старий UI в кеші браузера, тести, будь-хто;
  `tests/test_ws_lang_frames.py`).
- Мова, якої в каталозі нема, — теж None, плюс попередження `lang_unsupported:<код>` у meta найближчого повного кадру (I5).
- Кадр з текстами (наратив, картка стадії, розбір) складається один раз мовою джерела: SMC, сигнали й журнал — лише там.
  Для кожної іншої мови серед отримувачів робиться копія, у якій заново складено лише текстові поля.
- `meta.lang` — лише для з'єднань, що мову просили: у кожному їхньому повному кадрі й у дельтах з текстами. Дельта без
  текстів — один кадр на всіх, як і була.
Окремий модуль: межа «мова ↔ кадр» в одному місці, ws_server лише проводить (X33: ws_server > 3000 рядків).
"""
from __future__ import annotations

import dataclasses
import logging
import re
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional, Sequence, Tuple

from core.i18n import SOURCE_LANG, SOURCE_TRANSLATOR, Translator
from runtime.i18n_catalog import LanguageCatalog

_log = logging.getLogger(__name__)

LANG_QUERY_PARAM = "lang"
TEXT_FIELDS = ("narrative", "shell", "briefing")
_LANG_CODE = re.compile(r"^[a-z]{2,3}$")

# ws_server._put_calendar_profile (передається, щоб не імпортувати ws_server звідси — цикл імпорту)
PutCalendarProfile = Callable[[Any, Any, str, int, Dict[str, Any], Translator], Awaitable[None]]


@dataclasses.dataclass
class TextContext:
    """Входи текстових полів кадру — те, з чим їх склали мовою джерела, щоб інша мова мала ті самі числа."""

    symbol: str
    tf_s: int
    price: Optional[float] = None  # ціна й оцінка ATR наративу; None — наративу в кадрі нема
    atr: Optional[float] = None
    signal: Any = None  # головний сигнал для картки стадії (лише повний кадр, як і мовою джерела)
    view: Any = None  # CalendarTfView W1/MN — розбір доповнюється профілем тижня / місяця


def requested_lang(raw: Optional[str], catalog: Optional[LanguageCatalog]) -> Tuple[Optional[str], Optional[str]]:
    """Мова з адреси чи дії → (код мови каталогу або None, попередження для meta.warnings або None)."""
    if raw is None or raw.strip() == "":
        return None, None
    code = raw.strip().lower().split("-")[0]
    if not _LANG_CODE.match(code):
        return None, "lang_unsupported"  # сире значення в meta й лог не йде: воно від клієнта
    if catalog is None or catalog.get(code) is None:
        return None, "lang_unsupported:" + code
    return code, None


def translator_for(lang: Optional[str], catalog: Optional[LanguageCatalog]) -> Translator:
    found = catalog.get(lang) if catalog is not None and lang is not None else None
    return found if found is not None else SOURCE_TRANSLATOR


async def localize_full_frame(frame: Dict[str, Any], session: Any, app: Any, runner: Any, ctx: Optional[TextContext],
                              catalog: Optional[LanguageCatalog], put_calendar_profile: PutCalendarProfile) -> Dict[str, Any]:
    """Повний кадр з'єднання: мова, якщо просило, і разове попередження про непідтриману мову."""
    if session.lang_warning is not None:
        frame["meta"].setdefault("warnings", []).append(session.lang_warning)
        session.lang_warning = None
    if session.lang is None:
        return frame
    return await _frame_for_lang(frame, session.lang, app, runner, ctx, catalog, put_calendar_profile)


async def frames_by_lang(frame: Dict[str, Any], recipients: Sequence[Any], app: Any, runner: Any,
                         ctx: Optional[TextContext], catalog: Optional[LanguageCatalog],
                         put_calendar_profile: PutCalendarProfile) -> List[Tuple[Dict[str, Any], List[Any]]]:
    """Дельта → [(кадр, отримувачі)]. Без текстових полів — той самий кадр усім, як до мовного шару."""
    if ctx is None or not any(field in frame for field in TEXT_FIELDS):
        return [(frame, list(recipients))]
    groups: Dict[Optional[str], List[Any]] = {}
    for session in recipients:
        groups.setdefault(session.lang, []).append(session)
    variants = []
    for lang, sessions in groups.items():
        lang_frame = frame if lang is None else await _frame_for_lang(frame, lang, app, runner, ctx, catalog,
                                                                     put_calendar_profile)
        variants.append((lang_frame, sessions))
    return variants


async def _frame_for_lang(frame: Dict[str, Any], lang: str, app: Any, runner: Any, ctx: Optional[TextContext],
                          catalog: Optional[LanguageCatalog], put_calendar_profile: PutCalendarProfile) -> Dict[str, Any]:
    """Копія кадру з `meta.lang`; для мови, відмінної від джерела, текстові поля складено заново її перекладачем."""
    variant = dict(frame, meta=dict(frame["meta"], lang=lang))
    if lang == SOURCE_LANG or ctx is None or runner is None:
        return variant
    tr = translator_for(lang, catalog)
    started = time.perf_counter()
    try:
        await _recompose_texts(variant, frame, app, runner, ctx, tr, put_calendar_profile)
    except Exception:
        # I5: кадр мовою джерела з meta.lang джерела — глядач бачить українську, а не тихо змішаний кадр
        _log.warning("WS_LANG_TEXT_ERR lang=%s sym=%s tf=%s — кадр мовою джерела", lang, ctx.symbol, ctx.tf_s,
                     exc_info=True)
        return dict(frame, meta=dict(frame["meta"], lang=SOURCE_LANG,
                                     warnings=list(frame["meta"].get("warnings", [])) + ["lang_texts_unavailable"]))
    _log.debug("WS_LANG_TEXT lang=%s sym=%s tf=%s ms=%.2f", lang, ctx.symbol, ctx.tf_s,
               (time.perf_counter() - started) * 1000.0)
    return variant


async def _recompose_texts(variant: Dict[str, Any], frame: Dict[str, Any], app: Any, runner: Any, ctx: TextContext,
                           tr: Translator, put_calendar_profile: PutCalendarProfile) -> None:
    """Ті самі текстові поля, що в кадрі джерела, мовою `tr`; числа й рішення — ті самі (record=False: журнал не пише)."""
    from core.smc.narrative import narrative_to_wire

    if "narrative" in frame and ctx.price is not None:
        block = runner.get_narrative(ctx.symbol, ctx.tf_s, ctx.price, ctx.atr or 0.0, record=False, tr=tr)
        if block is not None:
            # Поверх вихідного wire: поля збагачення (archi_*) — не текст платформи, лишаються як є
            variant["narrative"] = dict(frame["narrative"], **narrative_to_wire(block))
            if "shell" in frame:
                shell = runner.get_shell_payload(ctx.symbol, ctx.tf_s, block, signal=ctx.signal, tr=tr)
                if shell is not None:
                    variant["shell"] = shell.to_wire()
    if "briefing" in frame:
        briefing = runner.get_briefing(ctx.symbol, tr=tr)
        if briefing is not None:
            variant["briefing"] = briefing.to_wire()
            if ctx.view is not None:
                await put_calendar_profile(app, ctx.view, ctx.symbol, ctx.tf_s, variant, tr)

