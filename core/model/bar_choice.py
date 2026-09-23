"""Вибір переможця серед записів бару з однаковим open_time_ms — єдине джерело семантики (ADR-0094).

Навіщо окремий модуль. До ADR-0094 у репо жили ДВІ однойменні `_choose_better_bar`: у
`runtime/store/layers/disk_layer.py` нічия віддавалась пізнішому запису, у `runtime/store/uds.py` —
ранішому, і без кроку `ts`. Через це 139 ключів на живих символах малювались різною свічкою залежно
від шляху читання (cold-load проти scrollback). Семантика вибору живе тепер лише тут; обидва шари
читання і ремонтний дедуп її імпортують.

Порядок критеріїв (перший, що розрізняє записи, вирішує):
  1. complete      > не complete      — I3: Final > Preview
  2. final src     > не final         — порожній src трактується як "history"
  3. НЕ partial    > partial          — derived-бар, зібраний з неповного набору дітей
  4. більший ts    > менший           — event_ts, інакше ssot_write_ts_ms; наявний > відсутній
  5. нічия         → incoming         — пізніший у вхідному порядку, тобто пізніший запис

Правило обрано ВИМІРОМ (ADR-0094 §1.4): проти агрегації M1 на 179 групах різновмісних дублікатів
воно дає 93.9% вірного OHLC; колишні вибирачі — 83.8% (TAIL) і 43.6% (RANGE). Критерій 3 —
найсильніший предиктор правди (правильний член не partial у 124 з 127 груп), а крок 5 перемагає
протилежний (89.9% проти 65.9% за OHLC + v).

Контракт виклику: члени групи подаються У ПОРЯДКУ ФАЙЛА (порядку дозапису). Інакше крок 5 перестає
означати «пізніший запис» і стає випадковим.
"""

from __future__ import annotations

from collections.abc import Set as AbstractSet
from typing import Any, Mapping, Optional

from core.model.bars import FINAL_SOURCES

_TS_FIELDS = ("event_ts", "ssot_write_ts_ms")


def is_complete(bar: Mapping[str, Any]) -> bool:
    return bool(bar.get("complete"))


def is_final_source(
    bar: Mapping[str, Any], final_sources: Optional[AbstractSet[str]] = None
) -> bool:
    src = bar.get("src")
    if not isinstance(src, str):
        return False
    return (src or "history") in (final_sources or FINAL_SOURCES)


def is_partial(bar: Mapping[str, Any]) -> bool:
    """Лише `extensions.partial` — НЕ `boundary_partial`.

    На диску 38 018 барів мають `boundary_partial` без `partial`: це звичайні бари на межі сесії,
    де бракує безкоштовних граничних хвилин, а не неповні дані. Правило C виміряне саме на
    `partial`; розширення визначення змусило б їх програвати дублікатам без жодного виміру.
    """
    extensions = bar.get("extensions")
    return isinstance(extensions, Mapping) and bool(extensions.get("partial"))


def ts_priority(bar: Mapping[str, Any]) -> Optional[int]:
    """Позначка часу запису, якщо бар її несе; bool часом не вважається."""
    for field in _TS_FIELDS:
        value = bar.get(field)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return None


def _is_whole(bar: Mapping[str, Any]) -> bool:
    return not is_partial(bar)


# Кожен предикат повертає True для КРАЩОГО запису; порядок кортежу = пріоритет.
_PREFERENCE = (is_complete, is_final_source, _is_whole)


def choose_better_bar(
    existing: Mapping[str, Any], incoming: Mapping[str, Any]
) -> Mapping[str, Any]:
    """Переможець із двох записів одного ключа; `incoming` — пізніший у порядку файла."""
    for prefers in _PREFERENCE:
        existing_ok, incoming_ok = prefers(existing), prefers(incoming)
        if existing_ok != incoming_ok:
            return incoming if incoming_ok else existing
    existing_ts, incoming_ts = ts_priority(existing), ts_priority(incoming)
    if existing_ts != incoming_ts:
        if incoming_ts is None:
            return existing
        if existing_ts is None or incoming_ts > existing_ts:
            return incoming
        return existing
    return incoming
