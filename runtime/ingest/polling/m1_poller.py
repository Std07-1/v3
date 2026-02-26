"""M1 Poller — отримання фінальних M1 барів з FXCM + каскадна деривація.

Ізольований від M5 pipeline (engine_b). Працює як окремий процес.
Поллить M1 від FXCM History API щохвилини, коммітить через UDS.

Деривація делегується DeriveEngine (ADR-0002 Phase 2):
  on_bar(M1) → cascade M3→M5→M15→M30→H1→H4
При відсутності DeriveEngine — degraded-but-loud warning (S17: fallback видалено).

SSOT-1: M1/M3 (візуальність + точки входу).
SSOT-3: H4 (derived через DeriveEngine).
"""
from __future__ import annotations

import ctypes
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.config_loader import pick_config_path, load_system_config
from core.model.bars import CandleBar, ms_to_utc_dt
from env_profile import load_env_secrets
from runtime.ingest.derive_engine import DeriveEngine
from runtime.ingest.market_calendar import MarketCalendar
from runtime.ingest.tick_common import (
    symbols_from_cfg,
    calendar_from_group,
)
from runtime.store.uds import build_uds_from_config, UnifiedDataStore


def _utc_now_ms() -> int:
    return int(time.time() * 1000)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
_M1_MS = 60_000

# Flat bar: O==H==L==C з малим обсягом (calendar-pause маркер від брокера)
# SSOT: config.json → flat_bar_max_volume. Дефолт 4 (як у конфігу).
_FLAT_BAR_MAX_VOLUME_DEFAULT = 4
_flat_bar_max_volume: int = _FLAT_BAR_MAX_VOLUME_DEFAULT


def set_flat_bar_max_volume(v: int) -> None:
    """Встановити flat_bar_max_volume з config (SSOT)."""
    global _flat_bar_max_volume
    _flat_bar_max_volume = max(0, int(v))


def _is_flat(bar: CandleBar) -> bool:
    return bar.o == bar.h == bar.low == bar.c and bar.v <= _flat_bar_max_volume


def _expected_closed_m1_ms(now_ms: int) -> int:
    """Який M1 бар щойно закрився (open_ms останнього закритого)."""
    return (now_ms // _M1_MS) * _M1_MS - _M1_MS


# Кеш останньої торгової хвилини per-calendar (уникає цикл до 10080 ітерацій)
_ltm_cache: Dict[int, int] = {}  # {id(calendar): last_result_ms}
_ltm_cache_input: Dict[int, int] = {}  # {id(calendar): now_ms що дало cache hit}


def _last_trading_minute_ms(calendar: MarketCalendar, now_ms: int) -> int:
    """Пошук останньої торгової хвилини (до 7 днів назад).

    Кешує результат per-calendar: якщо now_ms не змінився — повертає
    попередній результат без повторного циклу (O(1) замість O(10080)).
    """
    cal_id = id(calendar)
    if _ltm_cache_input.get(cal_id) == now_ms:
        cached = _ltm_cache.get(cal_id)
        if cached is not None:
            return cached
    cur = (now_ms // _M1_MS) * _M1_MS - _M1_MS
    for _ in range(7 * 24 * 60):
        if calendar.is_trading_minute(cur):
            _ltm_cache[cal_id] = cur
            _ltm_cache_input[cal_id] = now_ms
            return cur
        cur -= _M1_MS
    result = (now_ms // _M1_MS) * _M1_MS - _M1_MS
    _ltm_cache[cal_id] = result
    _ltm_cache_input[cal_id] = now_ms
    return result


def _expected_closed_m1_calendar(calendar: Optional[MarketCalendar], now_ms: int) -> int:
    """Expected last closed M1 з урахуванням календаря.

    Якщо ринок зараз відкритий → стандартний floor.
    Якщо закритий → floor останньої торгової хвилини.
    """
    if calendar is None or not calendar.enabled:
        return _expected_closed_m1_ms(now_ms)
    last_min = (now_ms // _M1_MS) * _M1_MS - _M1_MS
    if calendar.is_trading_minute(last_min):
        return _expected_closed_m1_ms(now_ms)
    lt = _last_trading_minute_ms(calendar, now_ms)
    if lt <= 0:
        return -1
    # M1: floor = саме lt (бо M1 вирівняний по хвилинах)
    return lt


# ---------------------------------------------------------------------------
# Per-symbol poller
# ---------------------------------------------------------------------------
class M1SymbolPoller:
    """Поллер M1 для одного символу.

    Інженерний підхід:
    - Calendar gate: не поллимо коли ринок закритий
    - Expected bar tracking: знаємо яку M1 очікуємо
    - Watermark: трекаємо останню committed M1
    - Adaptive fetch: caught-up → 2, gap → gap_size+1
    - Calendar-aware ingest: flat bars під час паузи маркуються
    - Gap detection: loud якщо watermark відстає
    """

    # Максимум барів за один fetch (захист від великих гепів)
    MAX_FETCH_N = 120  # 2 години M1
    # Після скількох пропущених хвилин вважати gap (для логу)
    GAP_WARN_THRESHOLD = 3

    def __init__(
        self,
        symbol: str,
        provider: Any,
        uds: UnifiedDataStore,
        calendar: Optional[MarketCalendar],
        tail_fetch_n: int = 5,
        m3_derive: bool = True,
        tail_catchup_max_bars: int = 5000,
        live_recover_threshold_bars: int = 3,
        live_recover_max_bars_per_cycle: int = 120,
        live_recover_cooldown_s: int = 5,
        live_recover_max_total_bars: int = 5000,
        live_recover_log_interval_s: int = 60,
        stale_s: int = 720,
    ) -> None:
        self._symbol = symbol
        self._provider = provider
        self._uds = uds
        self._calendar = calendar
        self._tail_n = max(2, tail_fetch_n)
        self._m3_derive = m3_derive
        self._derive_engine: Optional[DeriveEngine] = None
        self._derive_engine_warned = False
        self._tail_catchup_max_bars = max(0, tail_catchup_max_bars)

        # P0.2: live recover config (ADR-0002)
        self._live_recover_threshold_bars = max(1, live_recover_threshold_bars)
        self._live_recover_max_bars_per_cycle = max(1, live_recover_max_bars_per_cycle)
        self._live_recover_cooldown_s = max(0, live_recover_cooldown_s)
        self._live_recover_max_total_bars = max(0, live_recover_max_total_bars)
        self._live_recover_log_interval_s = max(10, live_recover_log_interval_s)

        # P0.2: recover state
        self._recover_active: bool = False
        self._recover_start_ts: float = 0.0
        self._recover_last_fetch_ts: float = 0.0
        self._recover_total_fetched: int = 0
        self._recover_total_written: int = 0
        self._recover_last_log_ts: float = 0.0
        self._recover_gap_at_start: int = 0

        # P0.3: stale detection (ADR-0002)
        self._stale_s = max(0, stale_s)
        self._last_new_bar_ts: float = 0.0  # time.time() коли останній новий M1 committed
        self._stale_count: int = 0
        self._last_stale_log_ts: float = 0.0

        # Watermark — останній committed M1 open_ms
        self._watermark_ms: Optional[int] = None

        # Counters
        self._committed_m1 = 0
        self._committed_m3 = 0
        self._errors = 0
        self._calendar_skips = 0
        self._gaps_detected = 0
        self._already_caught_up = 0

        # Calendar state tracking
        self._last_market_open: Optional[bool] = None

    # -- Calendar gate ---------------------------------------------------

    def _is_market_open(self, now_ms: int) -> bool:
        if self._calendar is None or not self._calendar.enabled:
            return True
        return self._calendar.is_trading_minute(now_ms)

    def _check_calendar_state(self, now_ms: int) -> bool:
        """Повертає True якщо ринок відкритий. Логує зміни стану."""
        is_open = self._is_market_open(now_ms)
        if self._last_market_open is not None and is_open != self._last_market_open:
            state_str = "open" if is_open else "closed"
            logging.info(
                "M1_CALENDAR_STATE symbol=%s state=%s", self._symbol, state_str,
            )
        self._last_market_open = is_open
        return is_open

    # -- Expected bar + fetch policy ------------------------------------

    def _compute_fetch_n(self, now_ms: int) -> int:
        """Адаптивний fetch count: 2 якщо caught-up, більше якщо gap."""
        expected = _expected_closed_m1_calendar(self._calendar, now_ms)
        if expected <= 0:
            return self._tail_n

        if self._watermark_ms is None:
            # Перший fetch — беремо стандартний хвіст
            return self._tail_n

        gap_bars = int((expected - self._watermark_ms) // _M1_MS)
        if gap_bars <= 0:
            # Caught up
            return 2
        if gap_bars >= self.GAP_WARN_THRESHOLD:
            self._gaps_detected += 1
            if self._gaps_detected <= 5 or self._gaps_detected % 60 == 0:
                logging.info(
                    "M1_GAP_DETECTED symbol=%s gap_bars=%d wm=%s expected=%s",
                    self._symbol, gap_bars, self._watermark_ms, expected,
                )
        # Fetch gap + 1 (щоб перекрити), але не більше ліміту
        return min(gap_bars + 1, self.MAX_FETCH_N)

    # -- Ingest bar (calendar-aware) ------------------------------------

    def _ingest_bar(self, bar: CandleBar) -> bool:
        """Calendar-aware ingest: маркує flat бари під час паузи.

        Повертає True якщо бар committed.
        """
        if not isinstance(bar, CandleBar):
            return False
        if bar.tf_s != 60 or not bar.complete:
            return False

        # Calendar-aware flat bar classification (як engine_b)
        trading = self._is_market_open(bar.open_time_ms)
        flat = _is_flat(bar)

        if flat and trading:
            # Flat під час торгових — приймаємо з маркером (grid completeness)
            bar = CandleBar(
                symbol=bar.symbol, tf_s=bar.tf_s,
                open_time_ms=bar.open_time_ms,
                close_time_ms=bar.close_time_ms,
                o=bar.o, h=bar.h, low=bar.low, c=bar.c, v=bar.v,
                complete=bar.complete, src=bar.src,
                extensions={**bar.extensions, "trading_flat": True},
            )

        if not trading:
            if flat:
                # Flat під час паузи → скіпаємо (шум від брокера)
                return False
            else:
                # Non-flat під час паузи → аномалія, але приймаємо
                bar = CandleBar(
                    symbol=bar.symbol, tf_s=bar.tf_s,
                    open_time_ms=bar.open_time_ms,
                    close_time_ms=bar.close_time_ms,
                    o=bar.o, h=bar.h, low=bar.low, c=bar.c, v=bar.v,
                    complete=bar.complete, src=bar.src,
                    extensions={
                        **bar.extensions,
                        "calendar_pause_nonflat_anomaly": True,
                    },
                )
                logging.warning(
                    "M1_NONFLAT_IN_PAUSE symbol=%s open_ms=%s o=%.5f h=%.5f l=%.5f c=%.5f v=%.0f",
                    self._symbol, bar.open_time_ms,
                    bar.o, bar.h, bar.low, bar.c, bar.v,
                )

        result = self._uds.commit_final_bar(bar)
        if result.ok:
            self._committed_m1 += 1
            # Оновлюємо watermark
            if self._watermark_ms is None or bar.open_time_ms > self._watermark_ms:
                self._watermark_ms = bar.open_time_ms
            # P0.3: оновлюємо час останнього нового бару
            self._last_new_bar_ts = time.time()
            # Каскадна деривація через DeriveEngine (ADR-0002 P2.3)
            if self._derive_engine is not None:
                committed = self._derive_engine.on_bar(bar)
                self._committed_m3 += sum(
                    1 for b in committed if b.tf_s == 180
                )
            elif self._m3_derive and not self._derive_engine_warned:
                # I5: degraded-but-loud — derive_engine missing
                logging.warning(
                    "M1_DERIVE_NO_ENGINE symbol=%s (derive_engine=None, m3_derive=True)"
                    " — деривація M3+ неможлива, тільки M1 commit",
                    self._symbol,
                )
                self._derive_engine_warned = True
            return True
        elif result.reason not in ("stale", "duplicate"):
            logging.warning(
                "M1_COMMIT_REJECT symbol=%s reason=%s open_ms=%s",
                self._symbol, result.reason, bar.open_time_ms,
            )
        return False

    # -- Main poll -------------------------------------------------------

    def poll_once(self) -> None:
        """Один цикл: calendar-aware cutoff → smart fetch → ingest.

        НЕ має calendar gate (як engine_b): замість блокування poll при
        market-closed, покладається на calendar-aware expected + caught-up
        check. Це гарантує що останній бар перед паузою завжди фетчиться.
        """
        now_ms = _utc_now_ms()

        # Calendar state logging (без блокування poll)
        self._check_calendar_state(now_ms)

        # Calendar-aware expected: останній закритий торговий M1
        expected = _expected_closed_m1_calendar(self._calendar, now_ms)
        if expected <= 0:
            # Немає торгових хвилин (довгий weekend / gap) — skip
            self._calendar_skips += 1
            return

        # Check if caught up (watermark >= expected last trading M1)
        if self._watermark_ms is not None and self._watermark_ms >= expected:
            self._already_caught_up += 1
            return

        # Adaptive fetch count
        fetch_n = self._compute_fetch_n(now_ms)

        # Єдиний шлях: history M1 → фільтр закритих → sort → commit у UDS.
        # Fetch: date_to = cutoff + 1 M1 (щоб точно включити cutoff бар)
        date_to = ms_to_utc_dt(expected + _M1_MS) if expected > 0 else None
        try:
            bars = self._provider.fetch_last_n_m1(
                self._symbol, n=fetch_n, date_to_utc=date_to,
            )
        except Exception as exc:
            self._errors += 1
            if self._errors <= 3 or self._errors % 60 == 0:
                logging.warning(
                    "M1_POLL_FETCH_ERROR symbol=%s err=%s total_errors=%d",
                    self._symbol, exc, self._errors,
                )
            return

        if not bars:
            return

        # FXCM може повертати бари у зворотному порядку.
        # Фільтр: тільки бари після watermark і до expected cutoff.
        # Watermark pre-filter запобігає stale spam в UDS (як engine_b).
        if expected > 0:
            bars = [b for b in bars if b.open_time_ms <= expected]
        if self._watermark_ms is not None:
            bars = [b for b in bars if b.open_time_ms > self._watermark_ms]
        bars.sort(key=lambda b: b.open_time_ms)
        if not bars:
            return

        # Ingest кожен бар
        for bar in bars:
            self._ingest_bar(bar)

        # P0.2: live recover після звичайного poll (як engine_b.poll_iteration)
        self._live_recover_check()

        # P0.3: stale detection
        self._stale_check(now_ms)

    # -- Live recover (P0.2: ADR-0002) ----------------------------------

    def _live_recover_check(self) -> None:
        """Перевірка і виконання live-recover після downtime/паузи.

        Якщо gap між watermark і expected > threshold — входить у
        режим recover з cooldown + budget. Кожен цикл перераховує
        вікно від поточного watermark до cutoff.

        Модель: engine_b._live_recover_check() (engine_b.py L1522).
        """
        if self._live_recover_max_total_bars <= 0:
            return
        now_ms = _utc_now_ms()
        cutoff = _expected_closed_m1_calendar(self._calendar, now_ms)
        if cutoff <= 0:
            return
        if self._watermark_ms is None:
            return  # немає watermark — обробляє bootstrap/tail_catchup
        gap_bars = int((cutoff - self._watermark_ms) // _M1_MS)

        # --- Вхід у recover ---
        if not self._recover_active:
            if gap_bars <= self._live_recover_threshold_bars:
                return
            self._recover_active = True
            self._recover_start_ts = time.time()
            self._recover_last_fetch_ts = 0.0
            self._recover_total_fetched = 0
            self._recover_total_written = 0
            self._recover_last_log_ts = 0.0
            self._recover_gap_at_start = gap_bars
            logging.warning(
                "M1_LIVE_RECOVER_START symbol=%s gap_bars=%d cutoff=%s wm=%s",
                self._symbol, gap_bars,
                ms_to_utc_dt(cutoff).isoformat(),
                ms_to_utc_dt(self._watermark_ms).isoformat(),
            )
            # gap_state: degraded-but-loud (Правило №9)
            self._uds.set_gap_state(
                backlog_bars=gap_bars,
                gap_from_ms=self._watermark_ms + _M1_MS,
                gap_to_ms=cutoff,
                policy="m1_live_recover_active",
            )

        # --- Вихід: наздогнали ---
        if gap_bars <= 0:
            self._live_recover_finish("caught_up")
            return

        # --- Вихід: бюджет вичерпано ---
        if self._recover_total_fetched >= self._live_recover_max_total_bars:
            self._live_recover_finish("max_total_reached")
            return

        # --- Cooldown ---
        now_s = time.time()
        if now_s - self._recover_last_fetch_ts < self._live_recover_cooldown_s:
            return

        # --- Fetch batch ---
        n = min(gap_bars, self._live_recover_max_bars_per_cycle)
        remaining_budget = self._live_recover_max_total_bars - self._recover_total_fetched
        n = min(n, remaining_budget)
        if n <= 0:
            self._live_recover_finish("budget_exhausted")
            return

        date_to = ms_to_utc_dt(cutoff + _M1_MS)
        try:
            bars = self._provider.fetch_last_n_m1(
                self._symbol, n=n, date_to_utc=date_to,
            )
        except Exception as exc:
            self._errors += 1
            logging.warning(
                "M1_LIVE_RECOVER_FETCH_ERROR symbol=%s err=%s",
                self._symbol, exc,
            )
            self._recover_last_fetch_ts = now_s
            return

        self._recover_last_fetch_ts = now_s
        self._recover_total_fetched += len(bars) if bars else 0

        if bars:
            bars = [
                b for b in bars
                if b.open_time_ms > self._watermark_ms
                and b.open_time_ms <= cutoff
            ]
            bars.sort(key=lambda b: b.open_time_ms)
            for bar in bars:
                if self._ingest_bar(bar):
                    self._recover_total_written += 1

        # --- Оновити gap_state ---
        remaining_gap = int((cutoff - (self._watermark_ms or 0)) // _M1_MS)
        if remaining_gap > 0:
            self._uds.set_gap_state(
                backlog_bars=remaining_gap,
                gap_from_ms=(self._watermark_ms or 0) + _M1_MS,
                gap_to_ms=cutoff,
                policy="m1_live_recover_active",
            )

        # --- Фазовий лог ---
        if now_s - self._recover_last_log_ts >= self._live_recover_log_interval_s:
            self._recover_last_log_ts = now_s
            elapsed_s = int(now_s - self._recover_start_ts)
            logging.info(
                "M1_LIVE_RECOVER sym=%s remaining=%d fetched=%d written=%d elapsed_s=%d",
                self._symbol, remaining_gap,
                self._recover_total_fetched,
                self._recover_total_written,
                elapsed_s,
            )

    def _live_recover_finish(self, reason: str) -> None:
        elapsed_s = int(time.time() - self._recover_start_ts)
        logging.info(
            "M1_LIVE_RECOVER_DONE symbol=%s reason=%s gap_at_start=%d "
            "fetched=%d written=%d elapsed_s=%d",
            self._symbol, reason,
            self._recover_gap_at_start,
            self._recover_total_fetched,
            self._recover_total_written,
            elapsed_s,
        )
        self._recover_active = False
        # Очистити gap_state
        self._uds.set_gap_state(
            backlog_bars=0,
            gap_from_ms=None,
            gap_to_ms=None,
            policy=None,
        )

    # -- Stale detection (P0.3: ADR-0002) -------------------------------

    def _stale_check(self, now_ms: int) -> None:
        """Якщо ринок відкритий і давно не було нового M1 → loud warning.

        Модель: engine_b m5_tail_stale_s логіка.
        """
        if self._stale_s <= 0:
            return
        if not self._is_market_open(now_ms):
            return  # ринок закритий — stale нерелевантний
        if self._last_new_bar_ts <= 0.0:
            return  # ще жодного бару не було
        now_s = time.time()
        silence_s = now_s - self._last_new_bar_ts
        if silence_s < self._stale_s:
            return
        self._stale_count += 1
        # Throttle stale лог: перший + кожні 60 (Правило §9.1)
        if self._stale_count <= 3 or self._stale_count % 60 == 0:
            logging.warning(
                "M1_STALE symbol=%s silence_s=%d stale_count=%d wm=%s",
                self._symbol, int(silence_s),
                self._stale_count, self._watermark_ms,
            )

    # -- Warmup ----------------------------------------------------------

    def warmup_watermark(self, tail_n: int = 10) -> int:
        """Встановлює watermark з disk tail (M1 final bars)."""
        try:
            candles = self._uds.read_tail_candles(self._symbol, 60, tail_n)
            loaded = 0
            for bar in candles:
                if bar.tf_s == 60 and bar.complete:
                    if self._watermark_ms is None or bar.open_time_ms > self._watermark_ms:
                        self._watermark_ms = bar.open_time_ms
                    loaded += 1
            return loaded
        except Exception as exc:
            logging.warning(
                "M1_WARMUP_ERROR symbol=%s err=%s", self._symbol, exc,
            )
            return 0

    # -- Tail catchup (P0.1: ADR-0002) --------------------------------

    def tail_catchup(self) -> Dict[str, Any]:
        """Заповнює M1 від watermark до expected (bootstrap-time).

        Викликається з _bootstrap_warmup() ПЕРЕД main loop.
        Інваріант P0.1: m1_poller НЕ входить у poll loop поки tail catchup
        не завершився. Гарантує що UI бачить M1 без великих гепів.

        Модель: engine_b._tail_catchup_from_broker() (engine_b.py L428).
        """
        if self._watermark_ms is None:
            return {"tail_catchup_skipped": "no_watermark"}
        if self._tail_catchup_max_bars <= 0:
            return {"tail_catchup_skipped": "disabled"}

        now_ms = _utc_now_ms()
        cutoff_ms = _expected_closed_m1_calendar(self._calendar, now_ms)
        if cutoff_ms <= 0:
            return {"tail_catchup_missing": 0}
        if cutoff_ms <= self._watermark_ms:
            return {"tail_catchup_missing": 0}

        missing = int((cutoff_ms - self._watermark_ms) // _M1_MS)
        if missing <= 0:
            return {"tail_catchup_missing": 0}

        n = min(missing, self._tail_catchup_max_bars)

        # Якщо truncated — loud warning + gap_state (degraded-but-loud)
        if missing > n:
            backlog = missing - n
            logging.warning(
                "M1_TAIL_CATCHUP_TRUNCATED symbol=%s missing_total=%d "
                "fetched=%d backlog=%d",
                self._symbol, missing, n, backlog,
            )
            gap_from_ms = self._watermark_ms + _M1_MS
            self._uds.set_gap_state(
                backlog_bars=backlog,
                gap_from_ms=gap_from_ms,
                gap_to_ms=cutoff_ms,
                policy="m1_tail_catchup_truncated",
            )

        # Fetch: date_to = cutoff + 1 M1 (щоб точно включити cutoff)
        date_to = ms_to_utc_dt(cutoff_ms + _M1_MS)
        try:
            bars = self._provider.fetch_last_n_m1(
                self._symbol, n=n, date_to_utc=date_to,
            )
        except Exception as exc:
            logging.warning(
                "M1_TAIL_CATCHUP_FETCH_ERROR symbol=%s err=%s",
                self._symbol, exc,
            )
            return {
                "tail_catchup_missing": missing,
                "tail_catchup_fetched": 0,
                "tail_catchup_error": str(exc),
            }

        if not bars:
            return {
                "tail_catchup_missing": missing,
                "tail_catchup_fetched": 0,
            }

        # Фільтр: тільки бари після watermark і до cutoff
        bars = [
            b for b in bars
            if b.open_time_ms > self._watermark_ms
            and b.open_time_ms <= cutoff_ms
        ]
        bars.sort(key=lambda b: b.open_time_ms)

        written = 0
        for bar in bars:
            if self._ingest_bar(bar):
                written += 1

        # Очистити gap_state якщо все заповнено
        final_gap = 0
        if cutoff_ms > 0 and self._watermark_ms is not None:
            final_gap = int((cutoff_ms - self._watermark_ms) // _M1_MS)
        if final_gap <= 0:
            self._uds.set_gap_state(
                backlog_bars=0,
                gap_from_ms=None,
                gap_to_ms=None,
                policy=None,
            )

        logging.info(
            "M1_TAIL_CATCHUP symbol=%s missing=%d fetched=%d written=%d "
            "wm_after=%s",
            self._symbol, missing, len(bars), written,
            self._watermark_ms,
        )
        return {
            "tail_catchup_missing": missing,
            "tail_catchup_fetched": len(bars),
            "tail_catchup_written": written,
        }

    @property
    def stats(self) -> dict:
        return {
            "symbol": self._symbol,
            "m1_committed": self._committed_m1,
            "m3_committed": self._committed_m3,
            "errors": self._errors,
            "calendar_skips": self._calendar_skips,
            "gaps_detected": self._gaps_detected,
            "caught_up_skips": self._already_caught_up,
            "watermark_ms": self._watermark_ms,
            "recover_active": self._recover_active,
            "stale_count": self._stale_count,
        }


# ---------------------------------------------------------------------------
# Multi-symbol runner
# ---------------------------------------------------------------------------
class M1PollerRunner:
    """Запускає M1 polling для всіх символів."""

    # Hardcoded defaults для derive warmup (fallback якщо config не задає)
    _DEFAULT_DERIVE_WARMUP: Dict[int, int] = {
        60: 300, 300: 20, 900: 10, 1800: 10, 3600: 10,
    }

    def __init__(
        self,
        pollers: List[M1SymbolPoller],
        provider: Any,
        uds: UnifiedDataStore,
        redis_tail_n: Dict[int, int],
        safety_delay_s: int = 8,
        log_interval_s: int = 300,
        reconnect_cooldown_s: int = 120,
        tail_catchup_enabled: bool = True,
        derive_engine: Optional[DeriveEngine] = None,
        derive_warmup_bars_by_tf: Optional[Dict[int, int]] = None,
        cascade_catchup_m1_bars: int = 1440,
    ) -> None:
        self._pollers = pollers
        self._provider = provider
        self._uds = uds
        self._redis_tail_n = redis_tail_n  # {tf_s: tail_n} для priming
        self._safety_delay_s = safety_delay_s
        self._log_interval_s = max(60, log_interval_s)
        self._last_log_ts = 0.0
        self._reconnect_cooldown_s = reconnect_cooldown_s
        self._last_reconnect_ts = 0.0
        self._connected = False
        self._tail_catchup_enabled = tail_catchup_enabled
        self._derive_engine = derive_engine
        self._derive_warmup_bars_by_tf = derive_warmup_bars_by_tf or dict(self._DEFAULT_DERIVE_WARMUP)
        self._cascade_catchup_m1_bars = max(0, cascade_catchup_m1_bars)
        # Graceful shutdown: stop_event дозволяє перервати sleep між циклами
        import threading as _threading
        self._stop_event = _threading.Event()

    # -- FXCM session lifecycle -----------

    def _try_connect(self) -> bool:
        """Спроба відкрити/перевідкрити FXCM сесію."""
        try:
            if getattr(self._provider, '_fx', None) is not None:
                try:
                    self._provider.__exit__(None, None, None)
                except Exception:
                    pass
            self._provider.__enter__()
            if not self._connected:
                logging.info("M1_POLLER_FXCM_SESSION connected=True")
            self._connected = True
            return True
        except Exception as exc:
            self._connected = False
            logging.warning("M1_POLLER_FXCM_SESSION connected=False err=%s", exc)
            return False

    def _maybe_reconnect(self, cycle_errors: int) -> None:
        """Reconnect якщо всі символи мали помилку в цьому циклі."""
        if cycle_errors < len(self._pollers):
            return
        now = time.time()
        if now - self._last_reconnect_ts < self._reconnect_cooldown_s:
            return
        self._last_reconnect_ts = now
        logging.info("M1_POLLER_RECONNECT all_failed=%d", cycle_errors)
        self._try_connect()

    def shutdown(self) -> None:
        """Закрити FXCM сесію та зупинити polling loop."""
        self._stop_event.set()  # негайно пробуджує _sleep_to_next_minute
        try:
            if self._connected:
                self._provider.__exit__(None, None, None)
                self._connected = False
        except Exception:
            pass

    # -- Bootstrap / warmup ---------------

    def _bootstrap_warmup(self) -> None:
        """Redis priming з диску (M1→H4) + watermark warmup."""
        symbols = [p._symbol for p in self._pollers]  # noqa: SLF001
        bootstrap_degraded = []

        # 1. Redis priming для M1→H4 (всі TF, якими керує m1_poller)
        #    Критично: заповнює self._tails у redis_snapshot, без чого
        #    put_bar() створює порожні deque і перезаписує Redis tail.
        try:
            primed_total = 0
            for sym in symbols:
                for tf_s, tail_n in sorted(self._redis_tail_n.items()):
                    if tail_n <= 0:
                        continue
                    count = self._uds.bootstrap_prime_from_disk(sym, tf_s, tail_n)
                    primed_total += count
            logging.info(
                "M1_POLLER_REDIS_PRIME symbols=%d primed_bars=%d tfs=%s",
                len(symbols), primed_total,
                ",".join(str(t) for t in sorted(self._redis_tail_n)),
            )
        except Exception as exc:
            logging.warning(
                "BOOTSTRAP_DEGRADED phase=redis_priming err=%s", exc,
            )
            bootstrap_degraded.append("redis_priming: %s" % exc)

        # 2. Watermark warmup — останні 10 M1 з диску для watermark init
        try:
            warmup_total = 0
            for p in self._pollers:
                loaded = p.warmup_watermark(tail_n=10)
                warmup_total += loaded
            logging.info(
                "M1_POLLER_WARMUP symbols=%d watermark_loaded=%d",
                len(self._pollers), warmup_total,
            )
        except Exception as exc:
            logging.warning(
                "BOOTSTRAP_DEGRADED phase=watermark_warmup err=%s", exc,
            )
            bootstrap_degraded.append("watermark_warmup: %s" % exc)

        # 2b. DeriveEngine buffer warmup (ADR-0002 P2.3)
        #     Заповнюємо GenericBuffer: M1 + проміжні TF (M5/M15/M30/H1) з диску.
        #     Без проміжних TF cascade M5→H4 не працює до ~4h після рестарту
        #     (cold-start warmup defect, виявлено 2026-02-19).
        if self._derive_engine is not None:
            try:
                engine_warmup = 0
                # SSOT: config.json → bootstrap.derive_warmup_bars_by_tf (S4 ADR-0003)
                warmup_tfs = sorted(self._derive_warmup_bars_by_tf.items())
                for p in self._pollers:
                    sym = p._symbol  # noqa: SLF001
                    all_bars = []
                    for tf_s, tail_n in warmup_tfs:
                        try:
                            bars = self._uds.read_tail_candles(sym, tf_s, tail_n)
                            if bars:
                                all_bars.extend(bars)
                        except Exception as exc:
                            logging.warning(
                                "DERIVE_ENGINE_WARMUP_ERR symbol=%s tf=%d err=%s",
                                sym, tf_s, exc,
                            )
                    if all_bars:
                        engine_warmup += self._derive_engine.warmup_bars(all_bars)
                logging.info(
                    "DERIVE_ENGINE_WARMUP symbols=%d bars=%d",
                    len(self._pollers), engine_warmup,
                )
            except Exception as exc:
                logging.warning(
                    "BOOTSTRAP_DEGRADED phase=derive_engine_warmup err=%s", exc,
                )
                bootstrap_degraded.append("derive_engine_warmup: %s" % exc)

        # 2c. Cascade catchup: прогін warmup M1 через cascade → деривація відсутніх барів.
        #     Після buffer warmup (2b) буфери M5-H1 заповнені з диску.
        #     Cascade catchup пропускає M1 через on_bar → cascade → derive + commit.
        #     UDS відхилить duplicates (stale/dup) — cascade все одно рекурсує
        #     і заповнює прогалини (gap-fill): M5→M15→M30→H1→H4.
        #     Виявлено 2026-02-19: без цього кроку H4 не деривується після рестарту
        #     бо warmup_bars() лише буферизує, не каскадує.
        if self._derive_engine is not None:
            try:
                catchup_n = self._cascade_catchup_m1_bars
                if catchup_n > 0:
                    catchup_total = 0
                    catchup_derived = 0
                    for p in self._pollers:
                        sym = p._symbol  # noqa: SLF001
                        m1_bars = self._uds.read_tail_candles(sym, 60, catchup_n)
                        if not m1_bars:
                            continue
                        for bar in m1_bars:
                            committed = self._derive_engine.on_bar(bar)
                            catchup_total += 1
                            catchup_derived += len(committed)
                    logging.info(
                        "DERIVE_CASCADE_CATCHUP symbols=%d m1_processed=%d "
                        "derived_committed=%d",
                        len(self._pollers), catchup_total, catchup_derived,
                    )
            except Exception as exc:
                logging.warning(
                    "BOOTSTRAP_DEGRADED phase=cascade_catchup err=%s", exc,
                )
                bootstrap_degraded.append("cascade_catchup: %s" % exc)

        # 3. Tail catchup — заповнення від watermark до expected_now
        #    Інваріант P0.1 (ADR-0002): ПЕРЕД main loop.
        if self._tail_catchup_enabled:
            try:
                self._do_tail_catchup()
            except Exception as exc:
                logging.warning(
                    "BOOTSTRAP_DEGRADED phase=tail_catchup err=%s", exc,
                )
                bootstrap_degraded.append("tail_catchup: %s" % exc)

        if bootstrap_degraded:
            logging.warning(
                "M1_POLLER_BOOTSTRAP_DEGRADED phases=%s", bootstrap_degraded,
            )

    def _do_tail_catchup(self) -> None:
        """Tail catchup для всіх символів (потребує FXCM сесії)."""
        if not self._try_connect():
            logging.warning("M1_TAIL_CATCHUP_SKIP (no FXCM session)")
            return

        catchup_total = 0
        for p in self._pollers:
            result = p.tail_catchup()
            written = result.get("tail_catchup_written", 0)
            catchup_total += written
            if result.get("tail_catchup_error"):
                logging.warning(
                    "M1_TAIL_CATCHUP_PARTIAL symbol=%s result=%s",
                    p.stats["symbol"], result,
                )
        logging.info(
            "M1_POLLER_TAIL_CATCHUP symbols=%d total_written=%d",
            len(self._pollers), catchup_total,
        )

    # -- Prime ready signal ---------------

    # TTL має бути достатнім щоб supervisor встиг прочитати (6h як connector)
    _PRIME_READY_TTL_S = 21600

    def _publish_prime_ready(self) -> None:
        """Публікує prime:ready:m1 після bootstrap (S3 ADR-0003)."""
        symbols = [p._symbol for p in self._pollers]  # noqa: SLF001
        tfs = sorted(self._redis_tail_n.keys())
        payload = {
            "v": 1,
            "ready": True,
            "component": "m1_poller",
            "ts_ms": _utc_now_ms(),
            "symbols": symbols,
            "tfs": tfs,
        }
        try:
            self._uds.set_prime_ready(payload, self._PRIME_READY_TTL_S, component="m1")
            logging.info(
                "PRIME_READY_SET component=m1 symbols=%d tfs=%s",
                len(symbols), ",".join(str(t) for t in tfs),
            )
        except Exception as exc:
            logging.warning("PRIME_READY_SET_FAILED component=m1 err=%s", exc)

    # -- Main loop -----------------------

    def run_forever(self) -> None:
        logging.info(
            "M1_POLLER_START symbols=%d safety_delay_s=%d",
            len(self._pollers), self._safety_delay_s,
        )
        self._bootstrap_warmup()
        self._publish_prime_ready()
        self._try_connect()
        self._maybe_log_stats(force=True)  # Початкові stats (watermarks після warmup)
        overdue_interval_s = 60  # Перевірка overdue кожні 60с
        last_overdue_ts = 0.0
        while not self._stop_event.is_set():
            self._sleep_to_next_minute()
            cycle_errors = 0
            for p in self._pollers:
                err_before = p.stats["errors"]
                p.poll_once()
                if p.stats["errors"] > err_before:
                    cycle_errors += 1
            # Timer-based overdue bucket check (safety net для cascade)
            now_ts = time.time()
            if (
                self._derive_engine is not None
                and now_ts - last_overdue_ts >= overdue_interval_s
            ):
                try:
                    overdue = self._derive_engine.check_overdue_buckets(
                        int(now_ts * 1000)
                    )
                    if overdue:
                        logging.info(
                            "OVERDUE_BUCKETS_FILLED count=%d tfs=%s",
                            len(overdue),
                            sorted(set(b.tf_s for b in overdue)),
                        )
                except Exception as exc:
                    logging.warning("OVERDUE_CHECK_ERR err=%s", exc)
                last_overdue_ts = now_ts
            self._maybe_log_stats()
            self._maybe_reconnect(cycle_errors)

    def _sleep_to_next_minute(self) -> None:
        now = time.time()
        next_min = (int(now // 60) + 1) * 60
        target = next_min + self._safety_delay_s
        delay = max(0.0, target - now)
        # stop_event.wait замість time.sleep для graceful shutdown:
        # дозволяє перервати очікування при виклику shutdown()
        self._stop_event.wait(delay)

    def _maybe_log_stats(self, force: bool = False) -> None:
        now = time.time()
        if not force and now - self._last_log_ts < self._log_interval_s:
            return
        self._last_log_ts = now
        total_m1 = sum(p.stats["m1_committed"] for p in self._pollers)
        total_m3 = sum(p.stats["m3_committed"] for p in self._pollers)
        total_err = sum(p.stats["errors"] for p in self._pollers)
        total_cal_skip = sum(p.stats["calendar_skips"] for p in self._pollers)
        total_gaps = sum(p.stats["gaps_detected"] for p in self._pollers)
        total_caught = sum(p.stats["caught_up_skips"] for p in self._pollers)
        recovering = sum(1 for p in self._pollers if p.stats.get("recover_active"))
        total_stale = sum(p.stats.get("stale_count", 0) for p in self._pollers)
        logging.info(
            "M1_POLLER_STATS symbols=%d m1=%d m3=%d err=%d cal_skip=%d "
            "gaps=%d caught_up=%d recovering=%d stale=%d",
            len(self._pollers), total_m1, total_m3, total_err,
            total_cal_skip, total_gaps, total_caught,
            recovering, total_stale,
        )


# ---------------------------------------------------------------------------
# Побудова з конфігу (composition)
# ---------------------------------------------------------------------------
def build_m1_poller(config_path: str) -> Optional[M1PollerRunner]:
    """Будує M1PollerRunner з config.json. Повертає None якщо вимкнено."""
    cfg = load_system_config(config_path)
    m1_cfg = cfg.get("m1_poller", {})
    if not isinstance(m1_cfg, dict):
        m1_cfg = {}

    if not m1_cfg.get("enabled", False):
        logging.info("M1_POLLER_DISABLED (m1_poller.enabled=false)")
        return None

    symbols = symbols_from_cfg(cfg)
    if not symbols:
        logging.warning("M1_POLLER_NO_SYMBOLS")
        return None

    tail_fetch_n = int(m1_cfg.get("tail_fetch_n", 5))
    safety_delay_s = int(m1_cfg.get("safety_delay_s", 8))
    m3_derive = bool(m1_cfg.get("m3_derive_enabled", True))

    # P0.5 (ADR-0002): config ключі для tail catchup / live recover / stale
    tail_catchup_max_bars = int(m1_cfg.get("tail_catchup_max_bars", 5000))
    lr_threshold = int(m1_cfg.get("live_recover_threshold_bars", 3))
    lr_max_cycle = int(m1_cfg.get("live_recover_max_bars_per_cycle", 120))
    lr_cooldown = int(m1_cfg.get("live_recover_cooldown_s", 5))
    lr_max_total = int(m1_cfg.get("live_recover_max_total_bars", 5000))
    lr_log_interval = int(m1_cfg.get("live_recover_log_interval_s", 60))
    stale_s = int(m1_cfg.get("stale_s", 720))
    logging.info(
        "M1_POLLER_CONFIG tail_catchup_max=%d lr_threshold=%d lr_max_cycle=%d "
        "lr_cooldown=%d lr_max_total=%d stale_s=%d",
        tail_catchup_max_bars, lr_threshold, lr_max_cycle,
        lr_cooldown, lr_max_total, stale_s,
    )

    # SSOT: flat_bar_max_volume з config.json (верхній рівень)
    flat_vol_raw = cfg.get("flat_bar_max_volume")
    if flat_vol_raw is not None:
        set_flat_bar_max_volume(int(flat_vol_raw))
        logging.info("M1_POLLER_FLAT_BAR_MAX_VOLUME=%d (from config)", _flat_bar_max_volume)
    else:
        logging.warning(
            "M1_POLLER_FLAT_BAR_MAX_VOLUME=%d (default, config key missing)",
            _flat_bar_max_volume,
        )

    # Ініціалізуємо FXCM provider
    from runtime.ingest.broker.fxcm.provider import FxcmHistoryProvider
    from core.config_loader import env_str

    user_id = env_str("FXCM_USERNAME")
    password = env_str("FXCM_PASSWORD")
    url = env_str("FXCM_HOST_URL")
    connection = env_str("FXCM_CONNECTION") or "Demo"

    if not user_id or not password or not url:
        logging.error("M1_POLLER_NO_FXCM_CREDENTIALS (FXCM_USERNAME/PASSWORD/HOST_URL)")
        return None

    provider = FxcmHistoryProvider(
        user_id=user_id,
        password=password,
        url=url,
        connection=connection,
    )

    data_root = str(cfg.get("data_root", "./data_v3"))
    boot_id = uuid.uuid4().hex

    uds = build_uds_from_config(
        config_path=config_path,
        data_root=data_root,
        boot_id=boot_id,
        role="writer",
        writer_components=True,
    )

    # Будуємо календарі
    cal_by_group = cfg.get("market_calendar_by_group", {})
    cal_sym_groups = cfg.get("market_calendar_symbol_groups", {})

    pollers: List[M1SymbolPoller] = []
    for sym in symbols:
        group = cal_sym_groups.get(sym)
        cal: Optional[MarketCalendar] = None
        if group and isinstance(cal_by_group.get(group), dict):
            cal = calendar_from_group(cal_by_group[group])

        pollers.append(M1SymbolPoller(
            symbol=sym,
            provider=provider,
            uds=uds,
            calendar=cal,
            tail_fetch_n=tail_fetch_n,
            m3_derive=m3_derive,
            tail_catchup_max_bars=tail_catchup_max_bars,
            live_recover_threshold_bars=lr_threshold,
            live_recover_max_bars_per_cycle=lr_max_cycle,
            live_recover_cooldown_s=lr_cooldown,
            live_recover_max_total_bars=lr_max_total,
            live_recover_log_interval_s=lr_log_interval,
            stale_s=stale_s,
        ))

    # -- DeriveEngine (ADR-0002 P2.3): каскадна деривація M1→H4 --
    derive_engine: Optional[DeriveEngine] = None
    derive_enabled = bool(m1_cfg.get("derive_engine_enabled", True))
    if derive_enabled:
        anchor_offset_s = int(cfg.get("day_anchor_offset_s", 0))
        # Calendar per symbol для DeriveEngine
        calendars_for_engine: Dict[str, MarketCalendar] = {}
        for sym in symbols:
            group = cal_sym_groups.get(sym)
            if group and isinstance(cal_by_group.get(group), dict):
                cal_obj = calendar_from_group(cal_by_group[group])
                if cal_obj is not None:
                    calendars_for_engine[sym] = cal_obj

        derive_engine = DeriveEngine(
            symbols=symbols,
            anchor_offset_s=anchor_offset_s,
            calendars=calendars_for_engine,
        )
        # Shared UDS: DeriveEngine коммітить через той же UDS (без file race)
        for sym in symbols:
            derive_engine.register_symbol_uds(sym, uds)
        # Inject DeriveEngine в кожен per-symbol poller
        for p in pollers:
            p._derive_engine = derive_engine  # noqa: SLF001
        logging.info(
            "DERIVE_ENGINE_WIRED symbols=%d anchor_offset_s=%d commit_tfs=%s",
            len(symbols), anchor_offset_s,
            sorted(derive_engine._commit_tfs_s),  # noqa: SLF001
        )

    # Redis tail_n для priming M1→H4 (всі TF, якими керує m1_poller)
    # Без прайминґу derived TF (M5-H4) put_bar() створює порожні deque
    # і перезаписує connector's повні Redis tail — split-brain (20260219-027).
    redis_cfg = cfg.get("redis", {})
    tail_n_raw = redis_cfg.get("tail_n_by_tf_s", {})
    redis_tail_n: Dict[int, int] = {}
    _PRIME_TFS = (60, 180, 300, 900, 1800, 3600, 14400)  # M1→H4
    for tf_s in _PRIME_TFS:
        val = tail_n_raw.get(str(tf_s), 0)
        if int(val) > 0:
            redis_tail_n[tf_s] = int(val)

    # S4 ADR-0003: derive warmup bars з config.json → bootstrap секція
    _derive_warmup_cfg: Optional[Dict[int, int]] = None
    bootstrap_cfg = cfg.get("bootstrap", {})
    if isinstance(bootstrap_cfg, dict):
        raw_warmup = bootstrap_cfg.get("derive_warmup_bars_by_tf")
        if isinstance(raw_warmup, dict):
            _derive_warmup_cfg = {}
            for k, v in raw_warmup.items():
                try:
                    _derive_warmup_cfg[int(k)] = int(v)
                except (ValueError, TypeError):
                    pass
            if _derive_warmup_cfg:
                logging.info(
                    "DERIVE_WARMUP_FROM_CONFIG tfs=%s",
                    sorted(_derive_warmup_cfg.keys()),
                )

    # Cascade catchup: кількість M1 для прогону через cascade при bootstrap.
    # Заповнює прогалини в derived TF (M5→H4) після рестарту.
    _cascade_catchup_m1_n = 1440  # default 24h
    if isinstance(bootstrap_cfg, dict):
        raw_catchup = bootstrap_cfg.get("cascade_catchup_m1_bars")
        if raw_catchup is not None:
            try:
                _cascade_catchup_m1_n = int(raw_catchup)
            except (ValueError, TypeError):
                pass

    return M1PollerRunner(
        pollers=pollers,
        provider=provider,
        uds=uds,
        redis_tail_n=redis_tail_n,
        safety_delay_s=safety_delay_s,
        tail_catchup_enabled=(tail_catchup_max_bars > 0),
        derive_engine=derive_engine,
        derive_warmup_bars_by_tf=_derive_warmup_cfg,
        cascade_catchup_m1_bars=_cascade_catchup_m1_n,
    )


# ---------------------------------------------------------------------------
# Pidfile guard — захист від дублікатів m1_poller (I5: SSOT writer)
# ---------------------------------------------------------------------------
_PID_FILE = Path("logs") / "m1_poller.pid"


def _is_pid_alive(pid: int) -> bool:
    """Перевіряє чи процес з PID живий І є m1_poller (Windows + POSIX).

    Використовує psutil для перевірки cmdline — захист від PID recycling.
    Fallback на OS-level check якщо psutil недоступний.
    """
    try:
        import psutil
        p = psutil.Process(pid)
        cmdline = " ".join(p.cmdline()).lower()
        return "m1_poller" in cmdline
    except Exception:
        pass
    # Fallback: OS-level (без cmdline check — менш надійно)
    if os.name == "nt":
        PROCESS_QUERY_LIMITED = 0x1000
        h = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED, False, pid)
        if h:
            ctypes.windll.kernel32.CloseHandle(h)
            return True
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _pidfile_guard() -> None:
    """Перевірити pidfile; якщо дублікат — fatal exit. Stale — warn + продовжити."""
    if _PID_FILE.exists():
        try:
            old_pid = int(_PID_FILE.read_text().strip())
        except (ValueError, OSError):
            old_pid = 0
        if old_pid and _is_pid_alive(old_pid):
            logging.error(
                "M1_POLLER_DUPLICATE pid=%d вже працює! Pidfile=%s. "
                "Вбийте старий процес або видаліть pidfile.",
                old_pid, _PID_FILE,
            )
            raise SystemExit(2)
        logging.warning("M1_POLLER_STALE_PID old_pid=%d (removed)", old_pid)
    _PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    _PID_FILE.write_text(str(os.getpid()))
    logging.info("M1_POLLER_PID pid=%d file=%s", os.getpid(), _PID_FILE)


def _pidfile_cleanup() -> None:
    """Видалити pidfile при завершенні."""
    try:
        _PID_FILE.unlink(missing_ok=True)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Entrypoint  (python -m runtime.ingest.polling.m1_poller)
# ---------------------------------------------------------------------------
def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )
    _pidfile_guard()

    report = load_env_secrets()
    if report.loaded:
        logging.info("ENV: secrets_loaded path=%s keys=%d", report.path, report.keys_count)

    config_path = pick_config_path()
    logging.info("M1_POLLER config=%s", config_path)

    runner = build_m1_poller(config_path)
    if runner is None:
        logging.info("M1_POLLER_EXIT (disabled or no credentials)")
        return 0

    try:
        runner.run_forever()
    except KeyboardInterrupt:
        logging.info("M1_POLLER_STOP (KeyboardInterrupt)")
    except Exception:
        logging.exception("M1_POLLER_FATAL")
        return 1
    finally:
        runner.shutdown()
        _pidfile_cleanup()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
