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

ADR: ADR-0002 (DeriveChain M1→H4), Phase 2.
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
    resolve_cascade_anchor_s,
)
from core.model.bars import CandleBar
from core.buckets import bucket_start_ms as _bucket_start_ms
from runtime.store.uds import UnifiedDataStore

log = logging.getLogger("derive_engine")


# ---------------------------------------------------------------------------
# Розмір буфера per source TF (скільки барів зберігати)
# Ключі = source TFs (з DERIVE_CHAIN keys).
# ---------------------------------------------------------------------------
_BUFFER_MAX_KEEP: Dict[int, int] = {
    60:   2000,   # M1 → M3(3) + M5(5) + D1(1440).  ~33h trading
    300:  500,    # M5 → M15(3).          ~41h
    900:  200,    # M15 → M30(2).         ~50h
    1800: 100,    # M30 → H1(2).          ~50h
    3600: 50,     # H1 → H4(4).           ~50h
    86400: 5,     # D1 target buffer — overdue dedup (D-03)
}

# Phase 5 (ADR-0002 завершено): commit всіх derived TFs.
# engine_b M5 polling вимкнено — DeriveEngine єдине джерело M3→H4+D1.
# Каскад: M1→M3(3)+M5(5)+D1(1440)→M15(3)→M30(2)→H1(2)→H4(4). ADR-0023.
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
        d1_anchor_offset_s: int = 0,
        calendars: Optional[Dict[str, Any]] = None,
        cascade_tfs_s: Optional[Set[int]] = None,
        commit_tfs_s: Optional[Set[int]] = None,
    ) -> None:
        """
        Args:
            symbols: список символів.
            anchor_offset_s: TV anchor offset для H4 (config: day_anchor_offset_s).
            d1_anchor_offset_s: D1 anchor offset (config: day_anchor_offset_s_d1, ADR-0023).
            calendars: {symbol: MarketCalendar} — calendar per symbol.
            cascade_tfs_s: TFs для деривації (default: DERIVE_ORDER).
            commit_tfs_s: TFs для UDS commit (default: {180, 14400}).
        """
        self._symbols = set(symbols)
        self._anchor_offset_s = anchor_offset_s
        self._d1_anchor_offset_s = d1_anchor_offset_s
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
            "DeriveEngine init: symbols=%d cascade=%s commit=%s anchor=%d d1_anchor=%d",
            len(symbols),
            sorted(self._cascade_tfs_s),
            sorted(self._commit_tfs_s),
            anchor_offset_s,
            d1_anchor_offset_s,
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

        Thread-safety: per-symbol lock захищає від гонки з on_bar
        під час bootstrap, коли m1_poller вже може почати poll.

        Returns:
            Кількість буферизованих барів.
        """
        count = 0
        # Групуємо по символу для мінімізації lock contention
        by_sym: Dict[str, List[CandleBar]] = {}
        for bar in bars:
            if bar.symbol not in self._symbols:
                continue
            if bar.tf_s not in DERIVE_CHAIN:
                continue
            by_sym.setdefault(bar.symbol, []).append(bar)
        for sym, sym_bars in by_sym.items():
            lock = self._locks.get(sym)
            if lock is None:
                continue
            with lock:
                for bar in sym_bars:
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

    def check_overdue_buckets(self, now_ms: int) -> List[CandleBar]:
        """Перевірка та деривація прострочених bucket'ів (timer-based safety net).

        Для кожного символу і TF перевіряє: чи є bucket, час якого вже минув
        (bucket_end <= now_ms), але bar не був committed (ні в UDS committed set,
        ні в буфері як вже derived). Якщо source-бари достатні — деривує.

        Цей метод — страховка від ситуацій де trigger не спрацював
        (race, restart mid-bucket, out-of-order delivery).
        Викликається з m1_poller після кожного poll cycle або по таймеру.

        Returns:
            Список newly committed derived барів.
        """
        committed: List[CandleBar] = []

        for symbol in self._symbols:
            lock = self._locks.get(symbol)
            if lock is None:
                continue
            with lock:
                committed.extend(
                    self._check_overdue_for_symbol(symbol, now_ms)
                )
        return committed

    # Кількість попередніх bucket-ів для overdue-сканування per TF.
    # Чим більший TF — тим глибше потрібно заглядати (H4=4h, один пропуск = 4 bucket M5).
    _OVERDUE_LOOKBACK: Dict[int, int] = {
        180:   3,    # M3:  3 × 3m  = 9m
        300:   6,    # M5:  6 × 5m  = 30m
        900:   4,    # M15: 4 × 15m = 1h
        1800:  4,    # M30: 4 × 30m = 2h
        3600:  3,    # H1:  3 × 1h  = 3h
        14400: 3,    # H4:  3 × 4h  = 12h
        86400: 1,    # D1:  1 × 24h = 24h (ADR-0023)
    }

    def _check_overdue_for_symbol(
        self, symbol: str, now_ms: int
    ) -> List[CandleBar]:
        """Per-symbol overdue check (має бути під lock).

        Сканує N попередніх bucket-ів (не лише 1) і каскадує
        successfully derived бари для можливості побудови H1/H4.
        """
        committed: List[CandleBar] = []
        cal = self._calendars.get(symbol)
        is_trading_fn = cal.is_trading_minute if cal is not None else None
        uds = self._uds_by_symbol.get(symbol)
        if uds is None:
            return committed

        # Перевіряємо кожен target TF, починаючи з найменших
        # (щоб M5 з'явився до того, як перевіряємо M15)
        sorted_tfs = sorted(self._cascade_tfs_s)
        for target_tf_s in sorted_tfs:
            source_info = DERIVE_SOURCE.get(target_tf_s)
            if source_info is None:
                continue
            source_tf_s, _ = source_info
            source_buf = self._buffers.get((symbol, source_tf_s))
            if source_buf is None:
                continue

            target_tf_ms = target_tf_s * 1000
            anchor = resolve_cascade_anchor_s(
                target_tf_s, self._anchor_offset_s, self._d1_anchor_offset_s
            )
            anchor_ms = anchor * 1000

            # Поточний bucket
            cur_bucket = _bucket_start_ms(now_ms, target_tf_ms, anchor_ms)

            # Скануємо N попередніх bucket-ів (не лише 1)
            lookback = self._OVERDUE_LOOKBACK.get(target_tf_s, 2)
            for i in range(1, lookback + 1):
                prev_bucket = cur_bucket - target_tf_ms * i

                # Перевірка: чи вже є derived бар у target буфері
                target_buf = self._buffers.get((symbol, target_tf_s))
                if target_buf is not None and prev_bucket in target_buf:
                    continue

                # Спроба деривації
                derived = derive_bar(
                    symbol=symbol,
                    target_tf_s=target_tf_s,
                    source_buffer=source_buf,
                    bucket_open_ms=prev_bucket,
                    anchor_offset_s=anchor,
                    d1_anchor_offset_s=self._d1_anchor_offset_s,
                    is_trading_fn=is_trading_fn,
                    filter_calendar_pause=True,
                )
                if derived is None:
                    continue

                # Commit
                if target_tf_s in self._commit_tfs_s:
                    result = uds.commit_final_bar(derived)
                    if result.ok:
                        committed.append(derived)
                        self._stats_committed[target_tf_s] = (
                            self._stats_committed.get(target_tf_s, 0) + 1
                        )
                        log.info(
                            "OVERDUE_DERIVE_OK tf=%d sym=%s open=%d lookback=%d",
                            target_tf_s, symbol, derived.open_time_ms, i,
                        )
                    # stale/duplicate — тиха ситуація, бар вже є

                # Каскад: буферизуємо + рекурсивна деривація вище
                # (overdue M5 → може побудувати M15 → M30 → H1 → H4)
                further = self._cascade(derived)
                committed.extend(further)

        return committed

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

        # 2. Calendar filter для символу (потрібен і для triggers, і для derive)
        cal = self._calendars.get(symbol)
        is_trading_fn = cal.is_trading_minute if cal is not None else None

        # 3. Triggers (calendar-aware: знаходить останній TRADING source
        #    слот у bucket, а не номінальний — фіксить H4 19:00 тощо)
        triggers = derive_triggers(
            bar,
            anchor_offset_s=self._anchor_offset_s,
            is_trading_fn=is_trading_fn,
            d1_anchor_offset_s=self._d1_anchor_offset_s,
        )
        if not triggers:
            return committed

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

            # Anchor offset — централізований routing (ADR-0023)
            anchor = resolve_cascade_anchor_s(
                target_tf_s, self._anchor_offset_s, self._d1_anchor_offset_s
            )

            derived = derive_bar(
                symbol=symbol,
                target_tf_s=target_tf_s,
                source_buffer=source_buf,
                bucket_open_ms=bucket_open_ms,
                anchor_offset_s=anchor,
                d1_anchor_offset_s=self._d1_anchor_offset_s,
                is_trading_fn=is_trading_fn,
                filter_calendar_pause=True,
            )
            if derived is None:
                # DIAG: лог чому derive_bar повернув None
                if target_tf_s in (300, 900, 1800, 3600, 14400, 86400):
                    src_tf_s = source_info[0]
                    tgt_ms = target_tf_s * 1000
                    b_end = bucket_open_ms + tgt_ms
                    miss = source_buf.missing_count(
                        bucket_open_ms, b_end, is_trading_fn=is_trading_fn
                    )
                    buf_len = len(source_buf)
                    log.warning(
                        "DERIVE_SKIP tf=%d sym=%s bucket_open=%d "
                        "missing=%d buf_size=%d src_tf=%d cal=%s",
                        target_tf_s, symbol, bucket_open_ms,
                        miss, buf_len, src_tf_s,
                        "yes" if is_trading_fn else "no",
                    )
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
