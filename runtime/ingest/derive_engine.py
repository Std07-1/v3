"""DeriveEngine — каскадна деривація OHLCV з I/O (runtime обгортка).

Архітектурний шар: runtime/ingest (Layer 2: DeriveEngine).
Dependency Rule: runtime/ імпортує core/, не навпаки.

Потік даних (on_bar cascade):
  m1_poller commit M1 → engine.on_bar(M1)
    → buffer M1 → derive_triggers → derive M3 (commit) + derive M5
      → buffer M5 → derive M15
        → buffer M15 → derive M30
          → buffer M30 → derive H1
            → buffer H1 → derive H4 (commit)

commit_tfs_s контролює які TF коммітяться в UDS:
  Phase 5 (active): DERIVE_ORDER — всі 6 TFs (M3,M5,M15,M30,H1,H4).
  engine_b M5 polling вимкнено (ADR-0002 завершено).

Thread-safety: per-symbol lock для cascade integrity.
Викликається з m1_poller per-symbol threads.

ADR: ADR-0002 derive-chain-from-m1.md, Phase 2.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, List, Optional, Set, Tuple

from core.derive import (
    DERIVE_CHAIN,
    DERIVE_ORDER,
    DERIVE_SOURCE,
    GenericBuffer,
    derive_bar,
    derive_triggers,
)
from core.model.bars import CandleBar
from runtime.store.uds import UnifiedDataStore

log = logging.getLogger("derive_engine")


# ---------------------------------------------------------------------------
# Розмір буфера per source TF (скільки барів зберігати)
# Ключі = source TFs (з DERIVE_CHAIN keys).
# ---------------------------------------------------------------------------
_BUFFER_MAX_KEEP: Dict[int, int] = {
    60:   2000,   # M1 → M3(3) + M5(5).  ~33h trading
    300:  500,    # M5 → M15(3).          ~41h
    900:  200,    # M15 → M30(2).         ~50h
    1800: 100,    # M30 → H1(2).          ~50h
    3600: 50,     # H1 → H4(4).           ~50h
}

# Phase 5 (ADR-0002 завершено): commit всіх derived TFs.
# engine_b M5 polling вимкнено — DeriveEngine єдине джерело M3→H4.
# Каскад: M1→M3(3)+M5(5)→M15(3)→M30(2)→H1(2)→H4(4).
DEFAULT_COMMIT_TFS_S: Set[int] = set(DERIVE_ORDER)  # {180,300,900,1800,3600,14400}


class DeriveEngine:
    """Каскадна деривація OHLCV з I/O commit через UDS.

    Thread-safe: per-symbol lock для cascade integrity.
    Може викликатись з різних потоків (m1_poller per-symbol threads).

    Інваріанти:
    - I0: core/ logic (derive_bar, derive_triggers) — pure, без I/O.
    - I1: запис тільки через UDS.commit_final_bar() (src="derived").
    - I3: final > preview зберігається (UDS watermark).
    - I5: reject → loud warning (не silent fallback).
    """

    def __init__(
        self,
        symbols: List[str],
        anchor_offset_s: int = 0,
        calendars: Optional[Dict[str, Any]] = None,
        cascade_tfs_s: Optional[Set[int]] = None,
        commit_tfs_s: Optional[Set[int]] = None,
    ) -> None:
        """
        Args:
            symbols: список символів.
            anchor_offset_s: TV anchor offset для H4 (config: day_anchor_offset_s).
            calendars: {symbol: MarketCalendar} — calendar per symbol.
            cascade_tfs_s: TFs для деривації (default: DERIVE_ORDER).
            commit_tfs_s: TFs для UDS commit (default: {180, 14400}).
        """
        self._symbols = set(symbols)
        self._anchor_offset_s = anchor_offset_s
        self._calendars: Dict[str, Any] = dict(calendars or {})
        self._cascade_tfs_s: Set[int] = set(cascade_tfs_s or DERIVE_ORDER)
        self._commit_tfs_s: Set[int] = set(
            commit_tfs_s if commit_tfs_s is not None else DEFAULT_COMMIT_TFS_S
        )

        # UDS per symbol (реєструється через register_symbol_uds)
        self._uds_by_symbol: Dict[str, UnifiedDataStore] = {}

        # Буфери: (symbol, tf_s) → GenericBuffer (тільки source TFs)
        self._buffers: Dict[Tuple[str, int], GenericBuffer] = {}

        # Per-symbol lock
        self._locks: Dict[str, threading.Lock] = {
            s: threading.Lock() for s in symbols
        }

        # Статистика
        self._stats_derived: Dict[int, int] = {}
        self._stats_committed: Dict[int, int] = {}
        self._stats_rejected: int = 0
        self._stats_no_uds: int = 0
        self._stats_cascade_calls: int = 0
        self._start_ts = time.time()

        log.info(
            "DeriveEngine init: symbols=%d cascade=%s commit=%s anchor=%d",
            len(symbols),
            sorted(self._cascade_tfs_s),
            sorted(self._commit_tfs_s),
            anchor_offset_s,
        )

    # -------------------------------------------------------------------
    # Setup
    # -------------------------------------------------------------------

    def register_symbol_uds(self, symbol: str, uds: UnifiedDataStore) -> None:
        """Реєструє UDS writer для символу.

        Викликається з m1_poller після створення UDS.
        DeriveEngine використовує ЦЕЙ ЖЕ UDS (shared instance)
        для commit derived барів — без file race.
        """
        self._uds_by_symbol[symbol] = uds

    # -------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------

    def on_bar(self, bar: CandleBar) -> List[CandleBar]:
        """Обробити committed бар і каскадно деривувати.

        Викликається ПІСЛЯ UDS.commit_final_bar(M1) з m1_poller.
        Thread-safe: per-symbol lock.

        Returns:
            Список committed derived барів.
        """
        if bar.symbol not in self._symbols or not bar.complete:
            return []
        lock = self._locks.get(bar.symbol)
        if lock is None:
            return []
        with lock:
            return self._cascade(bar)

    def warmup_bars(self, bars: List[CandleBar]) -> int:
        """Буферизувати бари без cascade (для bootstrap).

        Заповнює GenericBuffer для source TFs (ключі DERIVE_CHAIN).
        Не деривує і не коммітить — тільки buffer fill.
        Бари мають бути sorted by open_time_ms asc.

        Returns:
            Кількість буферизованих барів.
        """
        count = 0
        for bar in bars:
            if bar.symbol not in self._symbols:
                continue
            if bar.tf_s not in DERIVE_CHAIN:
                continue
            self._get_buffer(bar.symbol, bar.tf_s).upsert(bar)
            count += 1
        if count:
            log.info("DeriveEngine warmup: %d bars buffered", count)
        return count

    def stats(self) -> Dict[str, Any]:
        """Агрегована статистика роботи."""
        return {
            "uptime_s": round(time.time() - self._start_ts, 1),
            "cascade_calls": self._stats_cascade_calls,
            "derived_by_tf": dict(self._stats_derived),
            "committed_by_tf": dict(self._stats_committed),
            "committed_total": sum(self._stats_committed.values()),
            "rejected": self._stats_rejected,
            "no_uds": self._stats_no_uds,
            "buffers": len(self._buffers),
            "uds_registered": len(self._uds_by_symbol),
        }

    # -------------------------------------------------------------------
    # Internal cascade
    # -------------------------------------------------------------------

    def _get_buffer(self, symbol: str, tf_s: int) -> GenericBuffer:
        """Lazy-create буфер для (symbol, tf_s)."""
        key = (symbol, tf_s)
        buf = self._buffers.get(key)
        if buf is None:
            buf = GenericBuffer(tf_s, max_keep=_BUFFER_MAX_KEEP.get(tf_s, 100))
            self._buffers[key] = buf
        return buf

    def _cascade(self, bar: CandleBar) -> List[CandleBar]:
        """Каскад: buffer → triggers → derive → commit/skip → recurse.

        Рекурсивний: derived бар може бути source для наступного рівня.
        Глибина обмежена ланцюгом: M1→M5→M15→M30→H1→H4 (max 6).
        """
        self._stats_cascade_calls += 1
        committed: List[CandleBar] = []
        symbol = bar.symbol

        # 1. Буферизація (тільки source TFs — ключі DERIVE_CHAIN)
        if bar.tf_s in DERIVE_CHAIN:
            self._get_buffer(symbol, bar.tf_s).upsert(bar)

        # 2. Triggers
        triggers = derive_triggers(bar, anchor_offset_s=self._anchor_offset_s)
        if not triggers:
            return committed

        # 3. Calendar filter для символу
        cal = self._calendars.get(symbol)
        is_trading_fn = cal.is_trading_minute if cal is not None else None

        # 4. UDS для commit
        uds = self._uds_by_symbol.get(symbol)

        for target_tf_s, bucket_open_ms in triggers:
            if target_tf_s not in self._cascade_tfs_s:
                continue

            source_info = DERIVE_SOURCE.get(target_tf_s)
            if source_info is None:
                continue

            source_buf = self._buffers.get((symbol, source_info[0]))
            if source_buf is None:
                continue

            # Anchor offset — тільки для H4+ (14400+)
            anchor = self._anchor_offset_s if target_tf_s >= 14400 else 0

            derived = derive_bar(
                symbol=symbol,
                target_tf_s=target_tf_s,
                source_buffer=source_buf,
                bucket_open_ms=bucket_open_ms,
                anchor_offset_s=anchor,
                is_trading_fn=is_trading_fn,
                filter_calendar_pause=True,
            )
            if derived is None:
                continue

            self._stats_derived[target_tf_s] = (
                self._stats_derived.get(target_tf_s, 0) + 1
            )

            # 5. Commit (тільки commit_tfs_s)
            if target_tf_s in self._commit_tfs_s:
                if uds is not None:
                    result = uds.commit_final_bar(derived)
                    if result.ok:
                        committed.append(derived)
                        self._stats_committed[target_tf_s] = (
                            self._stats_committed.get(target_tf_s, 0) + 1
                        )
                        log.debug(
                            "DERIVE_OK tf=%d sym=%s open=%d",
                            target_tf_s, symbol, derived.open_time_ms,
                        )
                    else:
                        self._stats_rejected += 1
                        if result.reason not in ("stale", "duplicate"):
                            log.warning(
                                "DERIVE_REJECT tf=%d sym=%s open=%d reason=%s",
                                target_tf_s, symbol,
                                derived.open_time_ms, result.reason,
                            )
                else:
                    self._stats_no_uds += 1

            # 6. Recurse: derived бар може бути source для наступного рівня
            further = self._cascade(derived)
            committed.extend(further)

        return committed
