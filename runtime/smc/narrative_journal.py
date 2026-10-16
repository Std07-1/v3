"""runtime/smc/narrative_journal.py — журнал точності системного наративу (ADR-0113 Ф4): живий прогін.

На кожному закритті бару кроку (типово M15) — рядок розбору для кожного TF глядача, той самий, що пише
`tools.narrative_replay` (`SmcRunner.narrative_row`): момент розбору — закриття бару, ціна — його close. Тож метрики
живого журналу рахує той самий `tools.narrative_replay_metrics`, що й прогін по історії, і «що вважати ціллю» має
одне визначення.

Рядок пишеться не в мить закриття, а коли рушій отримав перший бар, що відкрився не раніше нього (`before_bar`): бари
різних TF одного моменту приходять у довільному порядку, а розбір має бачити їх усі — як прогін, що подає всю групу
закриття перед розбором. Пише незалежно від глядачів (фонове живлення рушія), раз на момент символу; рестарт губить
щонайбільше один крок. Файл — доба UTC моменту розбору (`YYYY-MM-DD.jsonl`), рядки — як у прогоні (sort_keys).
Не пише в UDS; `data_v3/` — runtime-стан.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

_log = logging.getLogger(__name__)

Row = Dict[str, Any]
RowBuilder = Callable[[str, int, float, int], Optional[Row]]


class NarrativeJournal:
    """Відкладений запис рядків розбору на закритті бару кроку (config `smc.narrative_journal`)."""

    def __init__(self, full_cfg: Dict[str, Any]) -> None:
        journal_cfg = full_cfg.get("smc", {}).get("narrative_journal", {})
        self._enabled = bool(journal_cfg.get("enabled", False))
        self._dir = str(journal_cfg.get("path", "data_v3/_narrative_journal"))
        self._step_tf_s = int(journal_cfg.get("step_tf_s", 900))
        self._viewer_tfs: List[int] = [int(tf) for tf in journal_cfg.get("viewer_tfs", (300, 900, 3600, 14400))]
        self._lock = threading.Lock()
        self._pending: Dict[str, Tuple[int, float]] = {}  # symbol → (момент закриття, close бару кроку)
        self._written: Dict[str, int] = {}  # symbol → останній записаний момент
        if self._enabled:  # тека — лише перед першим записом: без рядків журнал нічого не створює на диску
            _log.info("NARRATIVE_JOURNAL_INIT path=%s step_tf_s=%d viewer_tfs=%s", self._dir, self._step_tf_s,
                      self._viewer_tfs)

    @property
    def enabled(self) -> bool:
        return self._enabled

    def after_bar(self, symbol: str, tf_s: int, close_ms: int, close: float, complete: bool) -> None:
        """Закритий бар кроку — момент розбору чекає, доки рушій отримає бари всіх TF цього моменту."""
        if not self._enabled or not complete or tf_s != self._step_tf_s:
            return
        with self._lock:
            if self._written.get(symbol, -1) >= close_ms:
                return  # повтор того самого бару іншим шляхом подачі
            self._pending[symbol] = (close_ms, close)

    def before_bar(self, symbol: str, open_ms: int, build_row: RowBuilder) -> None:
        """Перед подачею бару, що відкрився не раніше моменту розбору, — розбір цього моменту (стан рушія — до нього)."""
        if not self._enabled:
            return
        with self._lock:
            pending = self._pending.get(symbol)
            if pending is None or open_ms < pending[0]:
                return
            del self._pending[symbol]
            self._written[symbol] = pending[0]
        close_ms, price = pending
        rows = []
        for viewer_tf_s in self._viewer_tfs:
            try:
                row = build_row(symbol, viewer_tf_s, price, close_ms)
            except Exception:
                _log.exception("NARRATIVE_JOURNAL_ROW_FAILED sym=%s tf=%d t=%d", symbol, viewer_tf_s, close_ms)
                continue
            if row is not None:
                rows.append(row)
        self._write(close_ms, rows)

    def _write(self, close_ms: int, rows: Sequence[Row]) -> None:
        if not rows:
            return
        day = time.strftime("%Y-%m-%d", time.gmtime(close_ms / 1000))
        path = os.path.join(self._dir, day + ".jsonl")
        try:
            os.makedirs(self._dir, exist_ok=True)
            with open(path, "a", encoding="utf-8") as out:
                for row in rows:
                    out.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        except OSError as exc:
            _log.warning("NARRATIVE_JOURNAL_WRITE_FAILED path=%s err=%s", path, exc)
