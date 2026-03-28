"""
runtime/smc/signal_journal.py — Автоматичний журнал сигналів (JSONL).

Фіксує стани narrative коли mode="trade" з дедуплікацією по (symbol, tf_s).Логує тільки на execution TF (config: execution_tf_s, default M15=900).
Тільки trigger ∈ {ready, triggered} генерує trade_entered; інші → signal_approaching.Логує: появу сигналу, зміну trigger state, зміну primary zone, вихід з trade mode.

**Lifecycle Tracking:**
Поки сигнал активний, трекає MFE/MAE (Max Favorable / Adverse Excursion).
При закритті сигналу емітує summary з повним lifecycle:
  duration_s, entry_price, exit_price, mfe, mae, reached_target, bias.

Storage: data_v3/_signals/journal-YYYY-MM-DD.jsonl
Config:  config.json → smc.signal_journal.enabled

Не пише в UDS, не змінює SSOT — суто діагностичний журнал для offline review.
"""

from __future__ import annotations

import json
import logging
import os
import re
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


class _ActiveSignal:
    """Стан активного trade-сигналу для lifecycle tracking."""

    __slots__ = (
        "started_ms",
        "entry_price",
        "direction",
        "zone_id",
        "target_price",
        "invalidation_price",
        "mfe",
        "mae",
        "peak_trigger",
        "bias_snapshot",
        "sub_mode",
        "session_at_entry",
    )

    def __init__(
        self,
        started_ms: int,
        entry_price: float,
        direction: str,
        zone_id: str,
        target_price: float,
        invalidation_price: float,
        bias_snapshot: Dict[str, str],
        sub_mode: str,
        session_at_entry: str,
    ) -> None:
        self.started_ms = started_ms
        self.entry_price = entry_price
        self.direction = direction
        self.zone_id = zone_id
        self.target_price = target_price
        self.invalidation_price = invalidation_price
        self.mfe = 0.0  # max favorable excursion (pts)
        self.mae = 0.0  # max adverse excursion (pts, always ≥ 0)
        self.peak_trigger = ""  # найвищий trigger state за час життя
        self.bias_snapshot = bias_snapshot
        self.sub_mode = sub_mode
        self.session_at_entry = session_at_entry

    def update_excursion(self, price: float) -> None:
        """Оновлює MFE/MAE на основі поточної ціни."""
        delta = price - self.entry_price
        if self.direction == "short":
            delta = -delta  # для short: падіння = favorable
        if delta > self.mfe:
            self.mfe = delta
        if delta < 0 and abs(delta) > self.mae:
            self.mae = abs(delta)

    def update_peak_trigger(self, trigger: str) -> None:
        """Запам'ятовує найвищий trigger стан."""
        if _TRIGGER_RANK.get(trigger, 0) > _TRIGGER_RANK.get(self.peak_trigger, 0):
            self.peak_trigger = trigger


def _parse_price_from_desc(desc: str) -> float:
    """Витягує ціну з target_desc/invalidation (напр. 'PDH 2892' → 2892.0)."""
    if not desc:
        return 0.0
    m = re.search(r"[\d]+(?:\.[\d]+)?", desc.replace(",", ""))
    return float(m.group()) if m else 0.0


class SignalJournal:
    """Append-only JSONL writer з lifecycle tracking для narrative signals.

    Дедуплікація: логує тільки при зміні (mode, primary_zone_id, trigger).
    Lifecycle: трекає MFE/MAE та генерує summary при закритті сигналу.
    Thread-safe.
    """

    def __init__(self, config: Dict[str, Any]) -> None:
        journal_cfg = config.get("smc", {}).get("signal_journal", {})
        self._enabled = bool(journal_cfg.get("enabled", False))
        self._base_dir = str(journal_cfg.get("path", "data_v3/_signals"))
        self._execution_tf_s = int(journal_cfg.get("execution_tf_s", 900))
        self._lock = threading.Lock()
        # (symbol, tf_s) → (mode, primary_zone_id, trigger)
        self._last_state: Dict[Tuple[str, int], Tuple[str, str, str]] = {}
        # (symbol, tf_s) → _ActiveSignal (lifecycle tracking)
        self._active: Dict[Tuple[str, int], _ActiveSignal] = {}

        if self._enabled:
            os.makedirs(self._base_dir, exist_ok=True)
            self._recover_state_from_journal()
            _log.info("SIGNAL_JOURNAL_INIT path=%s", self._base_dir)

    def _recover_state_from_journal(self) -> None:
        """Відновлює _last_state та _active з сьогоднішнього журналу після рестарту."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        filepath = os.path.join(self._base_dir, f"journal-{today}.jsonl")
        if not os.path.isfile(filepath):
            return

        try:
            with open(filepath, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    sym = rec.get("symbol", "")
                    tf_s = rec.get("tf_s", 0)
                    if not sym or not tf_s:
                        continue

                    key = (sym, tf_s)
                    event = rec.get("event", "")
                    mode = rec.get("mode", "")
                    zone_id = rec.get("zone_id", "")
                    trigger = rec.get("trigger", "")

                    self._last_state[key] = (mode, zone_id, trigger)

                    if event == "trade_entered":
                        self._active[key] = _ActiveSignal(
                            started_ms=rec.get("wall_ms", 0),
                            entry_price=rec.get("price", 0.0),
                            direction=rec.get("direction", ""),
                            zone_id=zone_id,
                            target_price=_parse_price_from_desc(
                                rec.get("target_desc", "")
                            ),
                            invalidation_price=_parse_price_from_desc(
                                rec.get("invalidation", "")
                            ),
                            bias_snapshot=rec.get("bias", {}),
                            sub_mode=rec.get("sub_mode", ""),
                            session_at_entry=rec.get("session", ""),
                        )
                    elif event == "trade_exited":
                        self._active.pop(key, None)
        except OSError as exc:
            _log.warning("SIGNAL_JOURNAL_RECOVER_ERR path=%s err=%s", filepath, exc)
            return

        if self._last_state:
            _log.info(
                "SIGNAL_JOURNAL_RECOVERED states=%d active=%d",
                len(self._last_state),
                len(self._active),
            )

    def record(
        self,
        symbol: str,
        tf_s: int,
        block: NarrativeBlock,
        price: float,
        atr: float,
        bias_map: Optional[Dict[str, str]] = None,
    ) -> None:
        """Фіксує narrative state якщо він змінився. Оновлює lifecycle tracking."""
        if not self._enabled:
            return
        # D-01: логуємо тільки з execution TF (default M15=900)
        if tf_s != self._execution_tf_s:
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
            changed = prev != current_state
            if changed:
                self._last_state[key] = current_state
            prev_mode = prev[0] if prev else ""
            prev_trigger = prev[2] if prev else ""

            # Lifecycle: оновлюємо MFE/MAE навіть без зміни стану
            active = self._active.get(key)
            if active is not None:
                active.update_excursion(price)
                active.update_peak_trigger(primary_trigger)

        if not changed:
            return

        event = self._classify_event(
            block.mode, prev_mode, primary_trigger, prev_trigger, prev is not None
        )
        if event is None:
            return

        # Lifecycle management
        if event == "trade_entered":
            sig = _ActiveSignal(
                started_ms=int(time.time() * 1000),
                entry_price=price,
                direction=block.scenarios[0].direction if block.scenarios else "",
                zone_id=primary_zone_id,
                target_price=_parse_price_from_desc(
                    block.scenarios[0].target_desc or "" if block.scenarios else ""
                ),
                invalidation_price=_parse_price_from_desc(
                    block.scenarios[0].invalidation if block.scenarios else ""
                ),
                bias_snapshot=dict(bias_map) if bias_map else {},
                sub_mode=block.sub_mode,
                session_at_entry=block.current_session,
            )
            sig.update_peak_trigger(primary_trigger)
            with self._lock:
                self._active[key] = sig
            self._append(symbol, tf_s, block, price, atr, event, bias_map)
            return

        if event == "trade_exited":
            with self._lock:
                closed = self._active.pop(key, None)
            self._append(
                symbol, tf_s, block, price, atr, event, bias_map, summary=closed
            )
            return

        # Інші events (trigger escalation, zone_changed)
        if event == "zone_changed" and block.scenarios:
            # Нова зона — оновлюємо target/invalidation в active signal
            with self._lock:
                active = self._active.get(key)
                if active is not None:
                    active.zone_id = primary_zone_id
                    active.target_price = _parse_price_from_desc(
                        block.scenarios[0].target_desc or ""
                    )
                    active.invalidation_price = _parse_price_from_desc(
                        block.scenarios[0].invalidation or ""
                    )

        self._append(symbol, tf_s, block, price, atr, event, bias_map)

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
            # D-03: trade_entered тільки якщо trigger ready/triggered
            if trigger in ("ready", "triggered"):
                return "trade_entered"
            return "signal_approaching"
        if mode != "trade" and prev_mode == "trade":
            return "trade_exited"
        if mode == "trade":
            cur_rank = _TRIGGER_RANK.get(trigger, 0)
            prev_rank = _TRIGGER_RANK.get(prev_trigger, 0)
            if cur_rank != prev_rank:
                return f"signal_{trigger}" if trigger else "signal_change"
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
        bias_map: Optional[Dict[str, str]] = None,
        summary: Optional[_ActiveSignal] = None,
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
            "market_phase": block.market_phase,
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

        # Bias snapshot — HTF picture at signal time
        if bias_map:
            entry["bias"] = bias_map

        # Lifecycle summary при закритті сигналу
        if summary is not None:
            now_ms = int(time.time() * 1000)
            duration_s = round((now_ms - summary.started_ms) / 1000.0, 1)
            entry["lifecycle"] = {
                "duration_s": duration_s,
                "entry_price": round(summary.entry_price, 2),
                "exit_price": round(price, 2),
                "direction": summary.direction,
                "mfe": round(summary.mfe, 2),
                "mae": round(summary.mae, 2),
                "mfe_atr": round(summary.mfe / atr, 2) if atr > 0 else 0.0,
                "mae_atr": round(summary.mae / atr, 2) if atr > 0 else 0.0,
                "peak_trigger": summary.peak_trigger,
                "reached_target": (
                    self._check_target_reached(summary)
                    if summary.target_price > 0
                    else None
                ),
                "hit_invalidation": (
                    self._check_invalidation_hit(summary)
                    if summary.invalidation_price > 0
                    else None
                ),
                "bias_at_entry": summary.bias_snapshot,
                "sub_mode_at_entry": summary.sub_mode,
                "session_at_entry": summary.session_at_entry,
                "zone_id": summary.zone_id,
            }

        filename = f"journal-{now.strftime('%Y-%m-%d')}.jsonl"
        filepath = os.path.join(self._base_dir, filename)
        try:
            with open(filepath, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError as exc:
            _log.warning("SIGNAL_JOURNAL_WRITE_ERR path=%s err=%s", filepath, exc)

    @staticmethod
    def _check_target_reached(sig: _ActiveSignal) -> bool:
        """Чи MFE досягла target price від entry."""
        if sig.target_price <= 0:
            return False
        target_dist = abs(sig.target_price - sig.entry_price)
        return sig.mfe >= target_dist if target_dist > 0 else False

    @staticmethod
    def _check_invalidation_hit(sig: _ActiveSignal) -> bool:
        """Чи MAE перевищила відстань до invalidation."""
        if sig.invalidation_price <= 0:
            return False
        inv_dist = abs(sig.invalidation_price - sig.entry_price)
        return sig.mae >= inv_dist if inv_dist > 0 else False

    # ── TDA Cascade journaling (ADR-0040) ────────────────

    def record_tda(
        self,
        event: str,
        signal: Any,
    ) -> None:
        """Журналює TDA cascade events.

        Events:
          - tda_signal_generated: новий сигнал з каскаду
          - tda_trade_closed: торгівля завершена (Config F outcome)
        """
        if not self._enabled:
            return

        now = datetime.now(timezone.utc)
        entry: Dict[str, Any] = {
            "ts": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "wall_ms": int(time.time() * 1000),
            "source": "tda_cascade",
            "event": event,
            "symbol": signal.symbol,
            "signal_id": signal.signal_id,
            "date": signal.date_str,
            "direction": signal.entry.direction,
            "grade": signal.grade,
            "grade_score": signal.grade_score,
            "entry_price": round(signal.entry.entry_price, 2),
            "stop_loss": round(signal.entry.stop_loss, 2),
            "take_profit": round(signal.entry.take_profit, 2),
            "risk_reward": round(signal.entry.risk_reward, 2),
            "macro_direction": signal.macro.direction,
        }

        if event == "tda_trade_closed":
            entry["trade_outcome"] = signal.trade.outcome
            entry["trade_net_r"] = round(signal.trade.net_r, 3)
            entry["trade_bars_elapsed"] = signal.trade.bars_elapsed
            entry["trade_partial_closed"] = signal.trade.partial_closed
            entry["trade_max_favorable_pts"] = round(signal.trade.max_favorable, 2)

        filename = f"journal-{now.strftime('%Y-%m-%d')}.jsonl"
        filepath = os.path.join(self._base_dir, filename)
        try:
            with open(filepath, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError as exc:
            _log.warning("SIGNAL_JOURNAL_TDA_WRITE_ERR path=%s err=%s", filepath, exc)
