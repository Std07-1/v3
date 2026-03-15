"""
runtime/smc/signal_journal.py — Автоматичний журнал сигналів (JSONL).

Фіксує стани narrative коли mode="trade" з дедуплікацією по (symbol, tf_s).
Логує: появу сигналу, зміну trigger state, зміну primary zone, вихід з trade mode.

Storage: data_v3/_signals/journal-YYYY-MM-DD.jsonl
Config:  config.json → smc.signal_journal.enabled

Не пише в UDS, не змінює SSOT — суто діагностичний журнал для offline review.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from core.smc.types import NarrativeBlock

_log = logging.getLogger(__name__)

# Ранг trigger-стану для визначення ескалації
_TRIGGER_RANK = {
    "": 0,
    "approaching": 1,
    "in_zone": 2,
    "ready": 3,
    "triggered": 4,
}


class SignalJournal:
    """Append-only JSONL writer для narrative signal states.

    Дедуплікація: логує тільки при зміні (mode, primary_zone_id, trigger)
    для кожного (symbol, tf_s). Thread-safe.
    """

    def __init__(self, config: Dict[str, Any]) -> None:
        journal_cfg = config.get("smc", {}).get("signal_journal", {})
        self._enabled = bool(journal_cfg.get("enabled", False))
        self._base_dir = str(journal_cfg.get("path", "data_v3/_signals"))
        self._lock = threading.Lock()
        # (symbol, tf_s) → (mode, primary_zone_id, trigger)
        self._last_state: Dict[Tuple[str, int], Tuple[str, str, str]] = {}

        if self._enabled:
            os.makedirs(self._base_dir, exist_ok=True)
            _log.info("SIGNAL_JOURNAL_INIT path=%s", self._base_dir)

    def record(
        self,
        symbol: str,
        tf_s: int,
        block: NarrativeBlock,
        price: float,
        atr: float,
    ) -> None:
        """Фіксує narrative state якщо він змінився з останнього виклику."""
        if not self._enabled:
            return

        primary_zone_id = ""
        primary_trigger = ""
        if block.scenarios:
            s0 = block.scenarios[0]
            primary_zone_id = s0.zone_id
            primary_trigger = s0.trigger

        current_state = (block.mode, primary_zone_id, primary_trigger)
        key = (symbol, tf_s)

        with self._lock:
            prev = self._last_state.get(key)
            if prev == current_state:
                return  # Без змін — пропускаємо
            self._last_state[key] = current_state
            prev_mode = prev[0] if prev else ""
            prev_trigger = prev[2] if prev else ""

        event = self._classify_event(
            block.mode, prev_mode, primary_trigger, prev_trigger, prev is not None
        )
        if event is None:
            return

        self._append(symbol, tf_s, block, price, atr, event)

    def _classify_event(
        self,
        mode: str,
        prev_mode: str,
        trigger: str,
        prev_trigger: str,
        had_prev: bool,
    ) -> Optional[str]:
        """Класифікує зміну стану narrative в тип події."""
        if mode == "trade" and prev_mode != "trade":
            return "trade_entered"
        if mode != "trade" and prev_mode == "trade":
            return "trade_exited"
        if mode == "trade":
            cur_rank = _TRIGGER_RANK.get(trigger, 0)
            prev_rank = _TRIGGER_RANK.get(prev_trigger, 0)
            if cur_rank != prev_rank:
                return f"signal_{trigger}" if trigger else "signal_change"
            # Та сама trigger state але інша зона → zone_changed
            if had_prev:
                return "zone_changed"
        return None

    def _append(
        self,
        symbol: str,
        tf_s: int,
        block: NarrativeBlock,
        price: float,
        atr: float,
        event: str,
    ) -> None:
        """Дописує один запис у денний JSONL файл."""
        now = datetime.now(timezone.utc)
        entry: Dict[str, Any] = {
            "ts": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "wall_ms": int(time.time() * 1000),
            "symbol": symbol,
            "tf_s": tf_s,
            "event": event,
            "mode": block.mode,
            "sub_mode": block.sub_mode,
            "headline": block.headline,
            "price": round(price, 2),
            "atr": round(atr, 2),
        }

        if block.scenarios:
            s0 = block.scenarios[0]
            entry["direction"] = s0.direction
            entry["entry_desc"] = s0.entry_desc
            entry["trigger"] = s0.trigger
            entry["trigger_desc"] = s0.trigger_desc
            entry["target_desc"] = s0.target_desc or ""
            entry["invalidation"] = s0.invalidation
            entry["zone_id"] = s0.zone_id

        if block.current_session:
            entry["session"] = block.current_session
            entry["in_killzone"] = block.in_killzone

        filename = f"journal-{now.strftime('%Y-%m-%d')}.jsonl"
        filepath = os.path.join(self._base_dir, filename)
        try:
            with open(filepath, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError as exc:
            _log.warning("SIGNAL_JOURNAL_WRITE_ERR path=%s err=%s", filepath, exc)
