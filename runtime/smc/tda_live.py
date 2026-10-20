"""
runtime/smc/tda_live.py — TdaLiveRunner: I/O wiring for TDA Cascade (ADR-0040 §3.2).

Connects pure TDA cascade logic (core/smc/tda/) to live bar events via SmcRunner.
One signal per day per symbol: cascade runs once when London session starts,
then trade management (Config F) runs per M15 bar until close/expiry.

When tda_cascade.enabled=true, this runner handles signal generation
instead of ADR-0039 zone-reactive signals.

Інваріанти:
  S1: read-only — reads UDS, does not write
  S5: all thresholds from config.json:smc.tda_cascade (TdaCascadeConfig)
  I5: degraded-but-loud — all errors logged
"""

from __future__ import annotations

import dataclasses
import json
import logging
import os
import time as _time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from core.model.bars import FINAL_SOURCES, CandleBar
from core.smc.tda.types import TdaCascadeConfig, TdaSignal
from core.smc.tda.orchestrator import run_tda_cascade
from core.smc.tda.stage5_trade_mgmt import update_trade

_log = logging.getLogger(__name__)

_MS_PER_DAY = 86_400_000
_MS_PER_HOUR = 3_600_000


def _now_ms() -> int:
    return int(_time.time() * 1000)


def _day_ms_from_epoch(epoch_ms: int) -> int:
    """Truncate epoch ms to day start (00:00 UTC)."""
    return (epoch_ms // _MS_PER_DAY) * _MS_PER_DAY


def _date_str_from_day_ms(day_ms: int) -> str:
    """Convert day_ms to 'YYYY-MM-DD' string."""
    dt = datetime.fromtimestamp(day_ms / 1000, tz=timezone.utc)
    return dt.strftime("%Y-%m-%d")


class TdaLiveRunner:
    """Runtime wiring for TDA Cascade (ADR-0040).

    Lifecycle:
      1. __init__(cfg, symbols) — parse config
      2. warmup(uds_reader) — load persisted signals
      3. on_bar(symbol, tf_s, bar) — called on each complete bar
         - On first London-window M15 bar: run daily cascade
         - On subsequent M15 bars: update trade management
      4. get_signal(symbol) → Optional[TdaSignal] — for frame injection
    """

    def __init__(
        self, cfg: TdaCascadeConfig, symbols: List[str], journal: Any = None
    ) -> None:
        self._cfg = cfg
        self._symbols = list(symbols)
        self._journal = journal  # SignalJournal for audit trail

        # Active signal per symbol (one per day max)
        self._signals: Dict[str, TdaSignal] = {}

        # Track which day's cascade already ran per symbol
        self._last_cascade_day_ms: Dict[str, int] = {}

        # open_ms останнього обробленого фінального бару M15 на символ: Config F рахує кожен бар один раз
        self._last_bar_open_ms: Dict[str, int] = {}

        # Позначки «каскад дня вирішено» на символ (_mark_cascade_day): {symbol: {day_ms, reason, at_ms}}
        self._cascade_marks: Dict[str, Dict[str, Any]] = {}

        # Persistence path
        self._state_path = os.path.join("data_v3", "_signals", "tda_state.json")

        _log.info(
            "TDA_LIVE_INIT enabled=%s symbols=%d grade=%s min_grade=%s",
            cfg.enabled,
            len(symbols),
            "on" if cfg.grade_enabled else "off",
            cfg.min_grade_for_entry,
        )

    def warmup(self) -> None:
        """Load persisted TDA signals and cascade-day marks from previous session."""
        self._load_state()
        self._load_cascade_days()
        _log.info(
            "TDA_LIVE_WARMUP active_signals=%d",
            len(self._signals),
        )

    def on_bar(
        self,
        symbol: str,
        tf_s: int,
        bar: CandleBar,
        uds_reader: Any,
    ) -> None:
        """Process a complete bar event.

        On M15 bar during London window: run cascade if not yet run today.
        On M15 bar when trade is open: update trade management.
        """
        if not self._cfg.enabled:
            return
        if symbol not in self._symbols:
            return
        if tf_s != 900:  # Only M15 bars drive TDA
            return
        if not bar.complete:
            return
        # ADR-0040 §3.2 rev 20.10.2026: каскад і Config F бачать кожен бар M15 один раз і лише фінал брокера.
        # Кільце preview дає на бар дві complete-події (tick_promoted, потім фінал), і обидва шляхи подачі
        # (delta loop глядача, фоновий feed) — кожну; bars_elapsed рахувався ×2–×4, TP/SL — і по OHLC тіків.
        if bar.src not in FINAL_SOURCES:
            _log.debug("TDA_BAR_SKIP sym=%s open_ms=%d reason=not_final src=%s", symbol, bar.open_time_ms, bar.src)
            return
        if bar.open_time_ms <= self._last_bar_open_ms.get(symbol, -1):
            _log.debug("TDA_BAR_SKIP sym=%s open_ms=%d reason=already_counted", symbol, bar.open_time_ms)
            return
        self._last_bar_open_ms[symbol] = bar.open_time_ms

        now = _now_ms()
        day_ms = _day_ms_from_epoch(bar.open_time_ms)

        # ── Daily cascade trigger ──
        last_day = self._last_cascade_day_ms.get(symbol, 0)
        if last_day != day_ms:
            bar_hour = (bar.open_time_ms - day_ms) // _MS_PER_HOUR
            # ADR-0051: Fire cascade AFTER London close so Stage 3 sees the full
            # London H1 window [london_start, london_end). Previously fired on
            # the first London-window bar (08:15 UTC) when 0 H1 bars were
            # complete → Stage 3 always returned NO_NARRATIVE → 0 signals/14d.
            if bar_hour >= self._cfg.london_end_hour_utc:
                self._run_cascade(symbol, day_ms, bar.open_time_ms, uds_reader, now)

        # ── Trade management (Config F) ──
        sig = self._signals.get(symbol)
        if sig is not None and sig.trade.status != "closed":
            new_trade = update_trade(sig.trade, bar, sig.entry, self._cfg)
            if new_trade is not sig.trade:
                self._signals[symbol] = dataclasses.replace(
                    sig,
                    trade=new_trade,
                    updated_ms=now,
                )
                # Persist on state change
                if new_trade.status == "closed":
                    _log.info(
                        "TDA_TRADE_CLOSED sym=%s id=%s outcome=%s net_r=%.2f bars=%d",
                        symbol,
                        sig.signal_id,
                        new_trade.outcome,
                        new_trade.net_r,
                        new_trade.bars_elapsed,
                    )
                    self._save_state()
                    # Journal: record trade close for audit trail
                    if self._journal is not None:
                        try:
                            self._journal.record_tda(
                                "tda_trade_closed", self._signals[symbol]
                            )
                        except Exception as jexc:
                            _log.warning("TDA_JOURNAL_ERR event=closed err=%s", jexc)

    def get_signal(self, symbol: str) -> Optional[TdaSignal]:
        """Get current TDA signal for frame injection. Returns None if no active signal."""
        return self._signals.get(symbol)

    # ── Private: cascade execution ────────────────────────

    def _run_cascade(
        self,
        symbol: str,
        day_ms: int,
        bar_open_ms: int,
        uds_reader: Any,
        now_ms: int,
    ) -> None:
        """Execute full TDA cascade for one symbol, one day — лише на тригері (першому фіналі M15 після london_end)."""
        date_str = _date_str_from_day_ms(day_ms)

        # Skip if closed signal already exists for today
        existing = self._signals.get(symbol)
        if existing is not None and existing.date_str == date_str:
            self._mark_cascade_day(symbol, day_ms, "already_signaled")
            return

        try:
            d1_bars = self._read_bars(uds_reader, symbol, 86400, 30)
            h4_bars = self._read_bars(uds_reader, symbol, 14400, 30)
            h1_bars = self._read_bars(uds_reader, symbol, 3600, 48)
            m15_bars = self._read_bars(uds_reader, symbol, 900, 96)
        except Exception as exc:
            _log.warning("TDA_CASCADE_READ_ERR sym=%s err=%s", symbol, exc)
            self._mark_cascade_day(symbol, day_ms, "error")
            return

        trigger_open_ms = _trigger_bar_open_ms(m15_bars, bar_open_ms, day_ms, self._cfg)
        if trigger_open_ms != bar_open_ms:
            # Процес не бачив тригера (лежав чи стартував пізніше): день пропущено — пізній прогін на більших даних
            # дав би сигнал, якого в канонічний момент не було (ADR-0040 §3.2 rev 20.10)
            _log.warning(
                "TDA_CASCADE_MISSED_WINDOW sym=%s date=%s trigger_open_ms=%d bar_open_ms=%d — каскад дня пропущено",
                symbol, date_str, trigger_open_ms, bar_open_ms,
            )
            self._mark_cascade_day(symbol, day_ms, "missed_window")
            return

        try:
            diag: Dict[str, Any] = {}
            sig = run_tda_cascade(
                symbol,
                date_str,
                d1_bars,
                h4_bars,
                h1_bars,
                m15_bars,
                day_ms,
                self._cfg,
                now_ms,
                diagnostics=diag,
            )
        except Exception as exc:
            _log.warning("TDA_CASCADE_RUN_ERR sym=%s err=%s", symbol, exc)
            self._mark_cascade_day(symbol, day_ms, "error")
            return

        self._mark_cascade_day(symbol, day_ms, "no_signal" if sig is None else "signal")

        if sig is None:
            _log.info(
                "TDA_CASCADE_NO_SIGNAL sym=%s date=%s failed=%s d1=%d h4=%d m15=%d diag=%s",
                symbol,
                date_str,
                diag.get("failed_stage", "?"),
                diag.get("d1_count", 0),
                diag.get("h4_count", 0),
                diag.get("m15_count", 0),
                {k: v for k, v in diag.items() if k.startswith("s") or k == "grade"},
            )
            return

        self._signals[symbol] = sig
        _log.info(
            "TDA_CASCADE_SIGNAL sym=%s id=%s grade=%s(%d) dir=%s entry=%.2f sl=%.2f tp=%.2f rr=%.2f",
            symbol,
            sig.signal_id,
            sig.grade,
            sig.grade_score,
            sig.entry.direction,
            sig.entry.entry_price,
            sig.entry.stop_loss,
            sig.entry.take_profit,
            sig.entry.risk_reward,
        )
        self._save_state()
        # Journal: record new TDA signal for audit trail
        if self._journal is not None:
            try:
                self._journal.record_tda("tda_signal_generated", sig)
            except Exception as jexc:
                _log.warning("TDA_JOURNAL_ERR event=generated err=%s", jexc)

    # ── Private: UDS reads (S1: read-only) ────────────────

    def _read_bars(
        self,
        uds_reader: Any,
        symbol: str,
        tf_s: int,
        limit: int,
    ) -> List[CandleBar]:
        """Read bars from UDS. S1: read-only."""
        from runtime.store.uds import WindowSpec, ReadPolicy

        spec = WindowSpec(symbol=symbol, tf_s=tf_s, limit=limit, cold_load=True)
        policy = ReadPolicy(disk_policy="explicit", prefer_redis=True)
        result = uds_reader.read_window(spec, policy)
        if result is None:
            return []

        bars_lwc = getattr(result, "bars_lwc", [])
        bars: List[CandleBar] = []
        for d in bars_lwc:
            cb = _bar_dict_to_cb(d, symbol, tf_s)
            if cb is not None:
                bars.append(cb)
        bars.sort(key=lambda b: b.open_time_ms)
        return bars

    # ── Private: persistence ──────────────────────────────

    def _save_state(self) -> None:
        """Persist active signals to survive restarts."""
        try:
            data: Dict[str, Any] = {}
            for sym, sig in self._signals.items():
                data[sym] = sig.to_wire()
                data[sym]["_day_ms"] = self._last_cascade_day_ms.get(sym, 0)
            _write_json_atomic(self._state_path, data)
        except Exception as exc:
            _log.warning("TDA_STATE_SAVE_ERR err=%s", exc)

    @property
    def _cascade_days_path(self) -> str:
        return os.path.join(os.path.dirname(self._state_path), "tda_cascade_days.json")

    def _mark_cascade_day(self, symbol: str, day_ms: int, reason: str) -> None:
        """Каскад дня символу вирішено — reason: signal | no_signal | already_signaled | error | missed_window.

        Позначка пишеться на кожній спробі й переживає рестарт: стан сигналів (_save_state) знає день лише символів
        із сигналом, тож рестарт після london_end повторював каскад дня (20.10: другий tda_NAS100_2026-10-20).
        """
        self._last_cascade_day_ms[symbol] = day_ms
        self._cascade_marks[symbol] = {"day_ms": day_ms, "reason": reason, "at_ms": _now_ms()}
        try:
            _write_json_atomic(self._cascade_days_path, self._cascade_marks)
        except Exception as exc:
            _log.warning("TDA_CASCADE_DAYS_SAVE_ERR sym=%s err=%s", symbol, exc)

    def _load_cascade_days(self) -> None:
        """Позначки днів каскаду з попередньої сесії; найпізніший день перемагає позначку зі стану сигналів."""
        if not os.path.isfile(self._cascade_days_path):
            return
        try:
            with open(self._cascade_days_path, "r", encoding="utf-8") as f:
                marks = json.load(f)
        except Exception as exc:
            _log.warning("TDA_CASCADE_DAYS_LOAD_ERR err=%s", exc)
            return
        for sym, mark in marks.items():
            day_ms = mark.get("day_ms") if isinstance(mark, dict) else None
            if not isinstance(day_ms, int):
                _log.warning("TDA_CASCADE_DAYS_BAD_MARK sym=%s mark=%r", sym, mark)
                continue
            self._cascade_marks[sym] = mark
            self._last_cascade_day_ms[sym] = max(day_ms, self._last_cascade_day_ms.get(sym, 0))

    def _load_state(self) -> None:
        """Restore signals from previous session.

        Only restores signals with status != 'closed' (still active trades).
        """
        if not os.path.isfile(self._state_path):
            return
        try:
            with open(self._state_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for sym, wire in data.items():
                if not isinstance(wire, dict):
                    continue
                day_ms = wire.get("_day_ms", 0)
                if day_ms:  # і для закритої угоди: інакше рестарт повторював каскад дня (rev 20.10)
                    self._last_cascade_day_ms[sym] = day_ms
                trade_w = wire.get("trade", {})
                if trade_w.get("status") == "closed":
                    continue  # Don't restore closed trades

                try:
                    sig = _signal_from_wire(wire, sym)
                    if sig is not None:
                        self._signals[sym] = sig
                except Exception as exc:
                    _log.debug("TDA_STATE_RESTORE_ERR sym=%s err=%s", sym, exc)

            if self._signals:
                _log.info(
                    "TDA_STATE_RECOVERED symbols=%s",
                    list(self._signals.keys()),
                )
        except Exception as exc:
            _log.warning("TDA_STATE_LOAD_ERR err=%s", exc)


def _write_json_atomic(path: str, payload: Any) -> None:
    """JSON у файл через tmp + os.replace: читач бачить старий або новий файл, ніколи не обрізаний."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _trigger_bar_open_ms(m15_bars: List[CandleBar], bar_open_ms: int, day_ms: int, cfg: TdaCascadeConfig) -> int:
    """Тригер каскаду дня — перший фінал M15 з відкриттям не раніше london_end; поточний бар — теж кандидат."""
    london_end_ms = day_ms + cfg.london_end_hour_utc * _MS_PER_HOUR
    return min([b.open_time_ms for b in m15_bars if b.complete and b.open_time_ms >= london_end_ms] + [bar_open_ms])


def _signal_from_wire(w: Dict[str, Any], symbol: str) -> Optional[TdaSignal]:
    """Reconstruct TdaSignal from wire dict (persistence restore)."""
    from core.smc.tda.types import (
        MacroResult,
        H4ConfirmResult,
        SessionNarrative,
        FvgEntry,
        TradeState,
    )

    try:
        macro_w = w.get("macro", {})
        macro = MacroResult(
            direction=macro_w.get("direction", "CFL"),
            method=macro_w.get("method", ""),
            confidence=macro_w.get("confidence", ""),
            pivot_count=macro_w.get("pivot_count", 0),
            d1_bar_count=macro_w.get("d1_bar_count", 0),
        )
        h4_w = w.get("h4_confirm", {})
        h4 = H4ConfirmResult(
            confirmed=h4_w.get("confirmed", False),
            close_price=h4_w.get("close_price", 0.0),
            midpoint=h4_w.get("midpoint", 0.0),
            h4_bar_count=h4_w.get("h4_bar_count", 0),
            reason=h4_w.get("reason", ""),
        )
        sess_w = w.get("session", {})
        session = SessionNarrative(
            narrative=sess_w.get("narrative", "NO_NARRATIVE"),
            asia_high=sess_w.get("asia_high", 0.0),
            asia_low=sess_w.get("asia_low", 0.0),
            sweep_direction=sess_w.get("sweep_direction"),
            sweep_price=sess_w.get("sweep_price"),
            asia_bar_count=sess_w.get("asia_bar_count", 0),
        )
        entry_w = w.get("entry", {})
        entry = FvgEntry(
            entry_price=entry_w.get("entry_price", 0.0),
            stop_loss=entry_w.get("stop_loss", 0.0),
            take_profit=entry_w.get("take_profit", 0.0),
            risk_reward=entry_w.get("risk_reward", 0.0),
            direction=entry_w.get("direction", ""),
            fvg_high=entry_w.get("fvg_high", 0.0),
            fvg_low=entry_w.get("fvg_low", 0.0),
            fvg_size=entry_w.get("fvg_size", 0.0),
            entry_bar_ms=entry_w.get("entry_bar_ms", 0),
        )
        trade_w = w.get("trade", {})
        trade = TradeState(
            status=trade_w.get("status", "open"),
            partial_closed=trade_w.get("partial_closed", False),
            partial_r=trade_w.get("partial_r", 0.0),
            max_favorable=trade_w.get("max_favorable_pts", 0.0),
            trail_sl=trade_w.get("trail_sl", 0.0),
            bars_elapsed=trade_w.get("bars_elapsed", 0),
            outcome=trade_w.get("outcome", ""),
            net_r=trade_w.get("net_r", 0.0),
        )
        return TdaSignal(
            signal_id=w.get("signal_id", ""),
            symbol=symbol,
            date_str=w.get("date", ""),
            macro=macro,
            h4_confirm=h4,
            session=session,
            entry=entry,
            grade=w.get("grade", "C"),
            grade_score=w.get("grade_score", 0),
            grade_factors=w.get("grade_factors", {}),
            trade=trade,
            created_ms=w.get("created_ms", 0),
            updated_ms=w.get("updated_ms", 0),
        )
    except Exception:
        return None


def _bar_dict_to_cb(d: Dict[str, Any], symbol: str, tf_s: int) -> Optional[CandleBar]:
    """Convert bar dict to CandleBar (shared logic with smc_runner)."""
    open_ms = d.get("open_time_ms") or d.get("open_ms")
    if not isinstance(open_ms, (int, float)) or open_ms <= 0:
        return None
    open_ms = int(open_ms)

    def _f(keys: List[str]) -> float:
        for k in keys:
            v = d.get(k)
            if isinstance(v, (int, float)) and v == v:
                return float(v)
        return 0.0

    o = _f(["o", "open"])
    h = _f(["h", "high"])
    low = _f(["low"])
    c = _f(["c", "close"])
    v = _f(["v", "volume"])
    complete = bool(d.get("complete", True))
    src = str(d.get("src", "derived"))

    if h < low or o <= 0:
        return None

    close_ms = open_ms + tf_s * 1000
    try:
        return CandleBar(
            symbol=symbol,
            tf_s=tf_s,
            open_time_ms=open_ms,
            close_time_ms=close_ms,
            o=o,
            h=h,
            low=low,
            c=c,
            v=v,
            complete=complete,
            src=src,
        )
    except Exception:
        return None
