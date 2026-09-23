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

Якір H4/D1 — правило символу (ADR-0095): бакет і крок назад — по сезонній сітці `core.session_anchor`,
не `open + tf`. Будівники створюють рушій лише через `build_derive_engine(cfg, ...)`.

ADR: ADR-0002 (DeriveChain M1→H4), Phase 2; ADR-0095 S4a (сезонний якір).
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, List, Mapping, Optional, Set, Tuple

from core.config_loader import htf_anchor_rule_resolver
from core.derive import (
    DERIVE_CHAIN,
    DERIVE_ORDER,
    DERIVE_SOURCE,
    GenericBuffer,
    derive_bar,
    derive_triggers,
)
from core.model.bars import CandleBar
from core.session_anchor import HTF_ANCHOR_RULES, htf_bucket_start_ms, htf_next_bucket_start_ms
from runtime.store.uds import UnifiedDataStore

log = logging.getLogger("derive_engine")


# ---------------------------------------------------------------------------
# Розмір буфера per source TF (скільки барів зберігати)
# Ключі = source TFs (з DERIVE_CHAIN keys).
# ---------------------------------------------------------------------------
_BUFFER_MAX_KEEP: Dict[int, int] = {
    60: 10080,  # M1 → M3(3) + M5(5) + D1(1440).  7d = warmup alignment
    300: 500,  # M5 → M15(3).          ~41h
    900: 200,  # M15 → M30(2).         ~50h
    1800: 100,  # M30 → H1(2).          ~50h
    3600: 50,  # H1 → H4(4).           ~50h
    86400: 5,  # D1 target buffer — overdue dedup (D-03)
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
        anchor_rules: Mapping[str, str],
        calendars: Optional[Dict[str, Any]] = None,
        cascade_tfs_s: Optional[Set[int]] = None,
        commit_tfs_s: Optional[Set[int]] = None,
    ) -> None:
        """
        Args:
            symbols: список символів.
            anchor_rules: {symbol: правило якоря H4/D1} (ADR-0095, `htf_anchor_rule_resolver`). Символ без
                правила або невідоме правило — ValueError тут, а не тихий якір 0 на першому H4.
            calendars: {symbol: MarketCalendar} — calendar per symbol.
            cascade_tfs_s: TFs для деривації (default: DERIVE_ORDER).
            commit_tfs_s: TFs для UDS commit (default: DERIVE_ORDER).
        """
        missing = sorted(s for s in symbols if s not in anchor_rules)
        if missing:
            raise ValueError("DERIVE_ENGINE_ANCHOR_RULE_MISSING symbols=%s (ADR-0095 S4a)" % missing)
        unknown = sorted(s for s in symbols if anchor_rules[s] not in HTF_ANCHOR_RULES)
        if unknown:
            raise ValueError(
                "DERIVE_ENGINE_ANCHOR_RULE_UNKNOWN symbols=%s allowed=%s" % (unknown, sorted(HTF_ANCHOR_RULES))
            )
        self._symbols = set(symbols)
        self._anchor_rules: Dict[str, str] = {s: anchor_rules[s] for s in symbols}
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
        self._locks: Dict[str, threading.Lock] = {s: threading.Lock() for s in symbols}

        # Статистика
        self._stats_derived: Dict[int, int] = {}
        self._stats_committed: Dict[int, int] = {}
        self._stats_rejected: int = 0
        self._stats_no_uds: int = 0
        self._stats_cascade_calls: int = 0
        self._start_ts = time.time()

        log.info(
            "DeriveEngine init: symbols=%d cascade=%s commit=%s",
            len(symbols),
            sorted(self._cascade_tfs_s),
            sorted(self._commit_tfs_s),
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

    def anchor_rule_for(self, symbol: str) -> str:
        """Правило якоря H4/D1 символу (ADR-0095); KeyError — символ не цього рушія."""
        return self._anchor_rules[symbol]

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
                committed.extend(self._check_overdue_for_symbol(symbol, now_ms))
        return committed

    # Кількість попередніх bucket-ів для overdue-сканування per TF.
    # Чим більший TF — тим глибше потрібно заглядати (H4=4h, один пропуск = 4 bucket M5).
    _OVERDUE_LOOKBACK: Dict[int, int] = {
        180: 3,  # M3:  3 × 3m  = 9m
        300: 6,  # M5:  6 × 5m  = 30m
        900: 4,  # M15: 4 × 15m = 1h
        1800: 4,  # M30: 4 × 30m = 2h
        3600: 3,  # H1:  3 × 1h  = 3h
        14400: 3,  # H4:  3 × 4h  = 12h
        # D1: тонка/святкова доба стає будованою лише з першою хвилиною наступної сесії (ADR-0097
        # фронтир) — після п'ятниці це неділя 22:00 = 3 bucket-и назад, +1 на святковий понеділок.
        86400: 4,
    }

    def _check_overdue_for_symbol(self, symbol: str, now_ms: int) -> List[CandleBar]:
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
        rule = self._anchor_rules[symbol]

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

            # Поточний bucket і крок назад — по сезонній сітці (ADR-0095 S4a): на вихідних переходу DST доба має
            # 23/25 год, тож `cur - tf*i` для H4/D1 виходить за сітку (пн 09.03, пн 02.11)
            prev_bucket = htf_bucket_start_ms(now_ms, target_tf_s, rule)

            # Скануємо N попередніх bucket-ів (не лише 1)
            lookback = self._OVERDUE_LOOKBACK.get(target_tf_s, 2)
            for i in range(1, lookback + 1):
                prev_bucket = htf_bucket_start_ms(prev_bucket - 1, target_tf_s, rule)

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
                    is_trading_fn=is_trading_fn,
                    filter_calendar_pause=True,
                    anchor_rule=rule,
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
                            target_tf_s,
                            symbol,
                            derived.open_time_ms,
                            i,
                        )
                    elif result.reason not in ("stale", "duplicate"):
                        # Писар відмовив (I5): не каскадуємо — вищий TF не будується з бару, якого нема на диску;
                        # бакет не потрапляє в буфер, тож наступна перевірка спробує його знову
                        self._stats_rejected += 1
                        log.warning(
                            "OVERDUE_DERIVE_REJECT tf=%d sym=%s open=%d reason=%s lookback=%d",
                            target_tf_s,
                            symbol,
                            derived.open_time_ms,
                            result.reason,
                            i,
                        )
                        continue
                    # stale/duplicate — бар уже є: каскад продовжуємо

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

        # 1. Буферизація (source TFs + terminal targets для overdue-dedup)
        if bar.tf_s in DERIVE_CHAIN or bar.tf_s in DERIVE_SOURCE:
            self._get_buffer(symbol, bar.tf_s).upsert(bar)

        # 2. Calendar filter для символу (потрібен і для triggers, і для derive)
        cal = self._calendars.get(symbol)
        is_trading_fn = cal.is_trading_minute if cal is not None else None

        # 3. Triggers (calendar-aware: знаходить останній TRADING source
        #    слот у bucket, а не номінальний — фіксить H4 19:00 тощо)
        rule = self._anchor_rules[symbol]
        triggers = derive_triggers(bar, is_trading_fn=is_trading_fn, anchor_rule=rule)
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

            derived = derive_bar(
                symbol=symbol,
                target_tf_s=target_tf_s,
                source_buffer=source_buf,
                bucket_open_ms=bucket_open_ms,
                is_trading_fn=is_trading_fn,
                filter_calendar_pause=True,
                anchor_rule=rule,
            )
            if derived is None:
                # DIAG: лог чому derive_bar повернув None
                if target_tf_s in (300, 900, 1800, 3600, 14400, 86400):
                    src_tf_s = source_info[0]
                    b_end = htf_next_bucket_start_ms(bucket_open_ms, target_tf_s, rule)
                    miss = source_buf.missing_count(
                        bucket_open_ms, b_end, is_trading_fn=is_trading_fn
                    )
                    buf_len = len(source_buf)
                    log.warning(
                        "DERIVE_SKIP tf=%d sym=%s bucket_open=%d "
                        "missing=%d buf_size=%d src_tf=%d cal=%s",
                        target_tf_s,
                        symbol,
                        bucket_open_ms,
                        miss,
                        buf_len,
                        src_tf_s,
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
                            target_tf_s,
                            symbol,
                            derived.open_time_ms,
                        )
                    else:
                        self._stats_rejected += 1
                        if result.reason not in ("stale", "duplicate"):
                            log.warning(
                                "DERIVE_REJECT tf=%d sym=%s open=%d reason=%s",
                                target_tf_s,
                                symbol,
                                derived.open_time_ms,
                                result.reason,
                            )
                else:
                    self._stats_no_uds += 1

            # 6. Recurse: derived бар може бути source для наступного рівня
            further = self._cascade(derived)
            committed.extend(further)

        return committed


def build_derive_engine(
    cfg: Mapping[str, Any],
    symbols: List[str],
    calendars: Optional[Dict[str, Any]] = None,
) -> DeriveEngine:
    """Єдиний будівник DeriveEngine для записувачів (ADR-0095 S4a, D15.2).

    Правило якоря кожного символу — з `htf_anchor_rule_resolver(cfg)`: невалідна секція `htf_anchor` чи символ
    невиміряної групи дають ValueError до старту деривації, а не тихий якір.
    """
    rule_for_symbol = htf_anchor_rule_resolver(dict(cfg))
    rules = {sym: rule_for_symbol(sym) for sym in symbols}
    engine = DeriveEngine(symbols=symbols, anchor_rules=rules, calendars=calendars)
    log.info(
        "DERIVE_ENGINE_WIRED symbols=%d rules=%s commit_tfs=%s",
        len(symbols),
        rules,
        sorted(engine._commit_tfs_s),
    )
    return engine
