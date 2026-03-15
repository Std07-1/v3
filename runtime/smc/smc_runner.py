"""
runtime/smc/smc_runner.py — SmcRunner: I/O обгортка навколо SmcEngine (ADR-0024 §3.4).

Живе в процесі ws_server (in-process, §6.1).
Не пише в UDS — підтримує S1.
Не async — викликається синхронно з delta_loop (того ж event loop).

Lifecycle:
    runner = SmcRunner(full_config, SmcEngine(smc_config))
    runner.warmup(uds)          # blocking, запускається через run_in_executor
    # далі для кожного committed bar з delta_loop:
    runner.on_bar_dict(symbol, tf_s, bar_dict)
    # при full frame build:
    snap = runner.get_snapshot(symbol, tf_s)  → SmcSnapshot | None

Python 3.7 compatible.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Dict, List, Optional, Set, Tuple

from core.model.bars import CandleBar
from core.smc.engine import SmcEngine
from core.smc.types import SmcDelta, SmcSnapshot, NarrativeBlock
from core.smc.narrative import (
    synthesize_narrative,
    narrative_to_wire,
    _fallback_narrative_block,
)
from runtime.smc.signal_journal import SignalJournal

_log = logging.getLogger(__name__)

# S4 perf rail: лог якщо warmup на одному TF > N мс
_WARMUP_SLOW_MS = 200.0


def _bar_dict_to_candle_bar(
    d: Dict[str, Any],
    symbol: str,
    tf_s: int,
) -> Optional[CandleBar]:
    """Конвертує raw bar dict (bars_lwc або events) → CandleBar.

    Підтримує обидва формати:
      - LWC short: o/h/low/c/v/open_time_ms
      - LWC long:  open/high/low/close/volume/open_time_ms
    Повертає None якщо дані некоректні (I5: degraded-but-loud через caller).
    """
    open_ms = d.get("open_time_ms") or d.get("open_ms")
    if not isinstance(open_ms, (int, float)) or open_ms <= 0:
        return None
    open_ms = int(open_ms)

    def _f(keys: List[str]) -> float:
        for k in keys:
            v = d.get(k)
            if isinstance(v, (int, float)) and v == v:  # not NaN
                return float(v)
        return 0.0

    o = _f(["o", "open"])
    h = _f(["h", "high"])
    low = _f(["low"])
    c = _f(["c", "close"])
    v = _f(["v", "volume"])
    complete = bool(d.get("complete", True))
    src = str(d.get("src", "derived"))

    # Basic sanity
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
    except Exception as exc:
        _log.debug("SMC_BAR_CONV_ERR sym=%s tf=%s err=%s", symbol, tf_s, exc)
        return None


class SmcRunner:
    """I/O обгортка SmcEngine. Один екземпляр на процес.

    Зберігає останній SmcDelta per (symbol, tf_s) для wiring у delta frame.
    Thread-safe: warmup викликається з executor thread; on_bar_dict — з event loop.
    Використовує threading.Lock для захисту _last_deltas.
    """

    def __init__(self, full_cfg: Dict[str, Any], engine: SmcEngine) -> None:
        """
        Args:
            full_cfg:  повний config.json (SSOT). Зчитує symbols + tf_allowlist_s.
            engine:    ready SmcEngine instance (core/smc/).
        """
        self._engine = engine
        self._full_config = full_cfg  # ADR-0033: narrative needs smc.narrative config
        self._symbols: List[str] = list(full_cfg.get("symbols", []))
        tf_raw = full_cfg.get(
            "tf_allowlist_s", [60, 300, 900, 1800, 3600, 14400, 86400]
        )
        self._tf_allowlist: Set[int] = set(int(x) for x in tf_raw)
        smc_cfg = full_cfg.get("smc", {}) if isinstance(full_cfg, dict) else {}
        self._lookback = int(smc_cfg.get("lookback_bars", 500))
        # compute_tfs: TFs where SMC is computed (cross-TF display for others)
        compute_raw = smc_cfg.get("compute_tfs", [900, 3600, 14400, 86400])
        self._compute_tfs: Set[int] = set(int(x) for x in compute_raw)
        self._lock = threading.Lock()
        # (symbol, tf_s) → last SmcDelta after on_bar_dict()
        self._last_deltas: Dict[Tuple[str, int], Optional[SmcDelta]] = {}
        self._journal = SignalJournal(full_cfg)
        _log.info(
            "SMC_RUNNER_INIT symbols=%s tfs=%s compute_tfs=%s lookback=%d",
            self._symbols,
            sorted(self._tf_allowlist),
            sorted(self._compute_tfs),
            self._lookback,
        )

    # ── Warmup ──────────────────────────────────────────

    def warmup(self, uds_reader: Any) -> None:
        """Blocking warmup: читає UDS → SmcEngine.update() для всіх (symbol, tf).

        Запускається через asyncio run_in_executor → не блокує event loop.
        S1: тільки читає UDS, не пише.
        """
        import time as _time

        _log.info(
            "SMC_RUNNER_WARMUP_START symbols=%d compute_tfs=%s",
            len(self._symbols),
            sorted(self._compute_tfs),
        )
        total_ok = 0
        total_err = 0
        m1_warmed: set = set()  # symbols that already got M1 feed

        for symbol in self._symbols:
            for tf_s in sorted(self._compute_tfs):
                t0 = _time.time()
                try:
                    bars = self._read_bars_for_warmup(uds_reader, symbol, tf_s)
                    snap = self._engine.update(symbol, tf_s, bars)
                    # ADR-0035: reuse M1 bars for session H/L (avoid duplicate read)
                    # If lookback < 2880, do a separate larger read for sessions
                    if tf_s == 60:
                        if bars:
                            self._engine.feed_m1_bars_bulk(symbol, bars)
                        if len(bars) < 2880:
                            try:
                                extra = self._read_m1_for_sessions(
                                    uds_reader, symbol, 2880
                                )
                                if len(extra) > len(bars):
                                    self._engine.feed_m1_bars_bulk(symbol, extra)
                            except Exception:
                                _log.debug(
                                    "SMC_WARMUP_EXTRA_FAIL sym=%s",
                                    symbol,
                                    exc_info=True,
                                )
                        m1_warmed.add(symbol)
                    elapsed_ms = (_time.time() - t0) * 1000.0
                    if elapsed_ms > _WARMUP_SLOW_MS:
                        _log.warning(
                            "SMC_WARMUP_SLOW sym=%s tf=%s bars=%d ms=%.1f",
                            symbol,
                            tf_s,
                            len(bars),
                            elapsed_ms,
                        )
                    else:
                        _log.info(
                            "SMC_WARMUP_OK sym=%s tf=%s bars=%d zones=%d swings=%d ms=%.1f",
                            symbol,
                            tf_s,
                            len(bars),
                            len(snap.zones),
                            len(snap.swings),
                            elapsed_ms,
                        )
                    total_ok += 1
                except Exception as exc:
                    _log.warning(
                        "SMC_WARMUP_ERR sym=%s tf=%s err=%s", symbol, tf_s, exc
                    )
                    total_err += 1

        # ADR-0035: warmup M1 bars for session H/L (only if M1 not in compute_tfs)
        # Session analysis needs ~48h of M1 data (prev + current sessions)
        _m1_lookback = max(self._lookback, 2880)  # 2880 M1 bars ≈ 48h
        for symbol in self._symbols:
            if symbol in m1_warmed:
                continue
            try:
                m1_bars = self._read_m1_for_sessions(uds_reader, symbol, _m1_lookback)
                if m1_bars:
                    self._engine.feed_m1_bars_bulk(symbol, m1_bars)
                    _log.info("SMC_WARMUP_M1_OK sym=%s bars=%d", symbol, len(m1_bars))
            except Exception as exc:
                _log.warning("SMC_WARMUP_M1_ERR sym=%s err=%s", symbol, exc)

        _log.info("SMC_RUNNER_WARMUP_DONE ok=%d err=%d", total_ok, total_err)

    def _read_bars_for_warmup(
        self,
        uds_reader: Any,
        symbol: str,
        tf_s: int,
    ) -> List[CandleBar]:
        """UDS read_window → List[CandleBar]. S1: read-only."""
        from runtime.store.uds import WindowSpec, ReadPolicy

        spec = WindowSpec(
            symbol=symbol,
            tf_s=tf_s,
            limit=self._lookback,
            cold_load=True,
        )
        policy = ReadPolicy(disk_policy="explicit", prefer_redis=True)
        result = uds_reader.read_window(spec, policy)
        if result is None:
            return []

        bars_lwc = getattr(result, "bars_lwc", [])
        bars: List[CandleBar] = []
        for d in bars_lwc:
            cb = _bar_dict_to_candle_bar(d, symbol, tf_s)
            if cb is not None:
                bars.append(cb)

        # Сортування за open_time_ms (UDS може давати не відсортовані при disk reads)
        bars.sort(key=lambda b: b.open_time_ms)
        return bars

    def _read_m1_for_sessions(
        self,
        uds_reader: Any,
        symbol: str,
        limit: int,
    ) -> List[CandleBar]:
        """Read M1 bars with custom lookback for session warmup. S1: read-only."""
        from runtime.store.uds import WindowSpec, ReadPolicy

        spec = WindowSpec(
            symbol=symbol,
            tf_s=60,
            limit=limit,
            cold_load=True,
        )
        policy = ReadPolicy(disk_policy="explicit", prefer_redis=True)
        result = uds_reader.read_window(spec, policy)
        if result is None:
            return []
        bars_lwc = getattr(result, "bars_lwc", [])
        bars: List[CandleBar] = []
        for d in bars_lwc:
            cb = _bar_dict_to_candle_bar(d, symbol, 60)
            if cb is not None:
                bars.append(cb)
        bars.sort(key=lambda b: b.open_time_ms)
        return bars

    # ── Live callback ────────────────────────────────────

    def feed_m1_bar_dict(self, symbol: str, bar_dict: Dict[str, Any]) -> None:
        """Feed M1 bar from delta loop for session H/L computation (ADR-0035).

        Called separately from on_bar_dict() because M1 may not be a subscribed TF
        but session engine still needs M1 data for session H/L tracking.
        """
        cb = _bar_dict_to_candle_bar(bar_dict, symbol, 60)
        if cb is not None:
            self._engine.feed_m1_bar(cb)

    def on_bar_dict(
        self,
        symbol: str,
        tf_s: int,
        bar_dict: Dict[str, Any],
    ) -> Optional[SmcDelta]:
        """Callback від delta_loop при кожному committed bar.

        Конвертує bar dict → CandleBar → SmcEngine.on_bar().
        Кешує SmcDelta для наступного delta frame.
        Повертає delta або None (ADR: on_bar скіпає preview bars сам).
        Скіпає TFs не в compute_tfs (cross-TF injection — лише display-time).
        """
        cb = _bar_dict_to_candle_bar(bar_dict, symbol, tf_s)
        if cb is None:
            _log.debug("SMC_BAR_SKIP sym=%s tf=%s reason=bad_dict", symbol, tf_s)
            return None

        # ADR-0035: feed M1 bars to engine for session H/L computation
        if tf_s == 60:
            self._engine.feed_m1_bar(cb)

        if tf_s not in self._compute_tfs:
            return None

        try:
            delta = self._engine.on_bar(cb)
            with self._lock:
                self._last_deltas[(symbol, tf_s)] = delta
            return delta
        except Exception as exc:
            _log.warning("SMC_ON_BAR_ERR sym=%s tf=%s err=%s", symbol, tf_s, exc)
            return None

    # ── Read API (для ws_server frame building) ────────

    def get_snapshot(self, symbol: str, tf_s: int) -> Optional[SmcSnapshot]:
        """Для full frame build — composite snapshot з cross-TF display mapping.

        Використовує get_display_snapshot() для:
        - Маппінг viewer TF → base computed TF
        - Ін'єкція HTF CHoCH/BOS (один вищий TF)
        - Ін'єкція HTF FVG зон per display mapping
        - Context Stack OB зон (L1/L2)
        - HTF key levels
        """
        try:
            return self._engine.get_display_snapshot(symbol, tf_s)
        except Exception as exc:
            _log.warning("SMC_GET_SNAP_ERR sym=%s tf=%s err=%s", symbol, tf_s, exc)
            return None

    def get_zone_grades(self, symbol: str, tf_s: int) -> dict:
        """ADR-0029: zone_grades after get_snapshot() call."""
        return self._engine.get_zone_grades(symbol, tf_s)

    def get_bias_map(self, symbol: str) -> dict:
        """ADR-0031: bias for all compute TFs. Returns {"900": "bullish", ...}."""
        result = {}
        for tf_s in sorted(self._compute_tfs):
            b = self._engine.get_htf_bias(symbol, tf_s)
            if b is not None:
                result[str(tf_s)] = b
        return result

    def get_momentum_map(self, symbol: str) -> dict:
        """Directional momentum per compute TF. Returns {"900": {"b": 2, "r": 1}, ...}."""
        result = {}
        for tf_s in sorted(self._compute_tfs):
            bull, bear = self._engine.get_momentum_score(symbol, tf_s)
            if bull > 0 or bear > 0:
                result[str(tf_s)] = {"b": bull, "r": bear}
        return result

    def get_session_levels_wire(self, symbol: str) -> list:
        """ADR-0035: session levels as wire dicts for delta frame injection.

        Returns list of {id, kind, price, t_ms} for current+prev sessions.
        """
        import time as _t

        try:
            levels = self._engine.get_session_levels(symbol, int(_t.time() * 1000))
            return [lv.to_wire() for lv in levels]
        except Exception:
            _log.debug("SMC_SESSION_LEVELS_WIRE_FAIL sym=%s", symbol, exc_info=True)
            return []

    def get_narrative(self, symbol, viewer_tf_s, current_price, atr=0.0):
        # type: (str, int, float, float) -> Optional[NarrativeBlock]
        """ADR-0033 + ADR-0035: synthesize narrative with session context.

        atr param from ws_server is single-bar proxy — we compute ATR14
        internally for reliable target distance filtering.
        """
        cfg = self._full_config.get("smc", {}).get("narrative", {})
        if not cfg.get("enabled", False):
            return None
        try:
            snap = self.get_snapshot(symbol, viewer_tf_s)
            if snap is None:
                return _fallback_narrative_block(["no_snapshot"])
            # Use ATR14 from engine bars instead of single-bar proxy
            atr_14 = self._engine.get_atr(symbol, viewer_tf_s)
            if atr_14 <= 1.0 and atr > 0:
                atr_14 = atr  # fallback to caller estimate if engine has no data
            bias = self.get_bias_map(symbol)
            grades = self.get_zone_grades(symbol, viewer_tf_s)
            momentum = self.get_momentum_map(symbol)
            # ADR-0035: session info for killzone downgrade + context
            session_info = None
            if self._engine._config.sessions.enabled and self._engine._session_windows:
                import time as _t
                from core.smc.sessions import get_current_session

                now_ms = int(_t.time() * 1000)
                sess_name, sess_kz = get_current_session(
                    now_ms, self._engine._session_windows
                )
                if sess_name:
                    session_info = (sess_name, sess_kz)
            result = synthesize_narrative(
                snap,
                bias,
                grades,
                momentum,
                viewer_tf_s,
                current_price,
                atr_14,
                cfg,
                session_info=session_info,
            )
            self._journal.record(symbol, viewer_tf_s, result, current_price, atr_14)
            return result
        except Exception:
            _log.exception("NARRATIVE_ERROR symbol=%s tf=%d", symbol, viewer_tf_s)
            return _fallback_narrative_block()

    def last_delta(self, symbol: str, tf_s: int) -> Optional[SmcDelta]:
        """Останній SmcDelta після on_bar_dict() — для delta frame wiring."""
        with self._lock:
            return self._last_deltas.get((symbol, tf_s))

    def clear_delta(self, symbol: str, tf_s: int) -> None:
        """Очищає кеш delta після відправки у frame (запобігає повторній відправці)."""
        with self._lock:
            self._last_deltas.pop((symbol, tf_s), None)
