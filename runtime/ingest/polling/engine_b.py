from __future__ import annotations

import datetime as dt
import logging
import os
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional, Set, Tuple, TYPE_CHECKING

from core.model.bars import CandleBar, assert_invariants, ms_to_utc_dt
from runtime.store.ssot_jsonl import (
    JsonlAppender,
    head_first_bar_time_ms,
    load_day_open_times,
    tail_last_bar_time_ms,
)

if TYPE_CHECKING:
    from runtime.ingest.broker.fxcm.provider import FxcmHistoryProvider


# Утиліти часу/бакетів

def utc_now_ms() -> int:
    return int(time.time() * 1000)


def floor_bucket_start_ms(ts_ms: int, tf_s: int, anchor_offset_s: int = 0) -> int:
    """Початок bucket для tf_s з опційним anchor_offset_s (для D1 зазвичай)."""
    tf_ms = tf_s * 1000
    adj = ts_ms - anchor_offset_s * 1000
    b0 = (adj // tf_ms) * tf_ms
    return b0 + anchor_offset_s * 1000


def _d1_anchor_offsets(
    day_anchor_offset_s: int,
    day_anchor_offset_s_d1: Optional[int],
    day_anchor_offset_s_d1_alt: Optional[int],
) -> Tuple[int, Optional[int]]:
    primary = day_anchor_offset_s_d1 if day_anchor_offset_s_d1 is not None else day_anchor_offset_s
    alt = day_anchor_offset_s_d1_alt
    if alt is not None and alt == primary:
        alt = None
    return primary, alt


def _h4_anchor_offsets(
    day_anchor_offset_s: int,
    day_anchor_offset_s_alt: Optional[int],
    day_anchor_offset_s_alt2: Optional[int],
) -> Tuple[int, Optional[int], Optional[int]]:
    primary = day_anchor_offset_s
    alt = day_anchor_offset_s_alt
    if alt is not None and alt == primary:
        alt = None
    alt2 = day_anchor_offset_s_alt2
    if alt2 is not None and alt2 in (primary, alt):
        alt2 = None
    return primary, alt, alt2


def select_anchor_offset_for_anchor_open_ms(
    tf_s: int,
    anchor_open_ms: int,
    day_anchor_offset_s: int,
    day_anchor_offset_s_alt: Optional[int],
    day_anchor_offset_s_alt2: Optional[int],
    day_anchor_offset_s_d1: Optional[int],
    day_anchor_offset_s_d1_alt: Optional[int],
) -> int:
    if tf_s != 86400:
        tf_ms = tf_s * 1000
        primary, alt, alt2 = _h4_anchor_offsets(
            day_anchor_offset_s,
            day_anchor_offset_s_alt,
            day_anchor_offset_s_alt2,
        )
        for off in (primary, alt, alt2):
            if off is None:
                continue
            b0 = floor_bucket_start_ms(anchor_open_ms, tf_s, anchor_offset_s=off)
            b1 = b0 + tf_ms
            if anchor_open_ms == (b1 - 60_000):
                return off
        return primary
    tf_ms = tf_s * 1000
    primary, alt = _d1_anchor_offsets(
        day_anchor_offset_s,
        day_anchor_offset_s_d1,
        day_anchor_offset_s_d1_alt,
    )
    for off in (primary, alt):
        if off is None:
            continue
        b0 = floor_bucket_start_ms(anchor_open_ms, tf_s, anchor_offset_s=off)
        b1 = b0 + tf_ms
        if anchor_open_ms == (b1 - 60_000):
            return off
    return primary


def expected_last_closed_m1_open_ms(now_ms: int) -> int:
    """Очікуваний open_time для останньої ЗАКРИТОЇ 1m свічки."""
    # bucket boundary на хвилині: ..., 12:34:00.000
    # остання закрита — попередня хвилина
    minute = 60_000
    b = (now_ms // minute) * minute
    return b - minute


def _parse_hm(hm: str) -> Optional[Tuple[int, int]]:
    if not hm:
        return None
    try:
        h, m = hm.split(":", 1)
        return int(h), int(m)
    except Exception:
        return None


def sleep_to_next_minute(safety_delay_s: int) -> None:
    now = time.time()
    next_min = (int(now // 60) + 1) * 60
    target = next_min + safety_delay_s
    delay = max(0.0, target - now)
    time.sleep(delay)


class M1Buffer:
    """Буфер закритих 1m барів у памʼяті для побудови derived TF.

    Зберігаємо останні max_keep барів (за замовчуванням вистачає на 2-4 дні).
    """

    def __init__(self, max_keep: int = 6000) -> None:
        self._max_keep = max_keep
        self._by_open_ms: Dict[int, CandleBar] = {}
        self._sorted_keys: List[int] = []

    def upsert(self, bar: CandleBar) -> None:
        if bar.tf_s != 60:
            raise ValueError("M1Buffer приймає тільки tf_s=60")
        k = bar.open_time_ms
        if k in self._by_open_ms:
            self._by_open_ms[k] = bar
            return
        self._by_open_ms[k] = bar
        self._sorted_keys.append(k)
        self._sorted_keys.sort()
        self._gc()

    def _gc(self) -> None:
        if len(self._sorted_keys) <= self._max_keep:
            return
        drop = len(self._sorted_keys) - self._max_keep
        to_drop = self._sorted_keys[:drop]
        self._sorted_keys = self._sorted_keys[drop:]
        for k in to_drop:
            self._by_open_ms.pop(k, None)

    def has_range_complete(self, start_ms: int, end_ms: int) -> bool:
        """Перевіряє, що є всі 1m бари на [start_ms, end_ms) без пропусків."""
        step = 60_000
        for t in range(start_ms, end_ms, step):
            if t not in self._by_open_ms:
                return False
        return True

    def range_bars(self, start_ms: int, end_ms: int) -> List[CandleBar]:
        step = 60_000
        out: List[CandleBar] = []
        for t in range(start_ms, end_ms, step):
            b = self._by_open_ms.get(t)
            if b is None:
                return []
            out.append(b)
        return out

    def missing_count(self, start_ms: int, end_ms: int) -> int:
        step = 60_000
        missing = 0
        for t in range(start_ms, end_ms, step):
            if t not in self._by_open_ms:
                missing += 1
        return missing

    def earliest_open_ms(self) -> Optional[int]:
        if not self._sorted_keys:
            return None
        return self._sorted_keys[0]

    def latest_open_ms(self) -> Optional[int]:
        if not self._sorted_keys:
            return None
        return self._sorted_keys[-1]


def derive_from_m1_for_anchor(
    symbol: str,
    tf_s: int,
    m1: M1Buffer,
    anchor_open_ms: int,
    anchor_offset_s: int = 0,
) -> Optional[CandleBar]:
    tf_ms = tf_s * 1000

    b0 = floor_bucket_start_ms(anchor_open_ms, tf_s, anchor_offset_s=anchor_offset_s)
    b1 = b0 + tf_ms

    # Емітимо derived рівно на останній хвилині бакету
    if anchor_open_ms != (b1 - 60_000):
        return None

    if not m1.has_range_complete(b0, b1):
        return None

    bars = m1.range_bars(b0, b1)
    if not bars:
        return None

    o = bars[0].o
    c = bars[-1].c
    h = max(x.h for x in bars)
    low = min(x.low for x in bars)
    v = sum(x.v for x in bars)

    out = CandleBar(
        symbol=symbol,
        tf_s=tf_s,
        open_time_ms=b0,
        close_time_ms=b1,
        o=o, h=h, low=low, c=c, v=v,
        complete=True,
        src="derived",
    )
    assert_invariants(out, anchor_offset_s=anchor_offset_s)
    return out


class PollingConnectorB:
    def __init__(
        self,
        provider: "FxcmHistoryProvider",
        data_root: str,
        symbol: str,
        config_path: str,
        warmup_bars: int,
        safety_delay_s: int,
        derived_tfs_s: List[int],
        broker_base_tfs_s: List[int],
        broker_base_fetch_on_close: bool,
        broker_base_max_tf_per_poll: int,
        broker_base_cold_start_counts: Dict[int, int],
        broker_base_cold_start_enabled: bool,
        day_anchor_offset_s: int,
        day_anchor_offset_s_d1: Optional[int],
        day_anchor_offset_s_d1_alt: Optional[int],
        day_anchor_offset_s_alt: Optional[int],
        day_anchor_offset_s_alt2: Optional[int],
        backfill_step_bars: int,
        backfill_every_n_polls: int,
        derived_rebuild_lookback_bars: int,
        derived_tolerate_missing_minutes: int,
        derived_backfill_from_broker: bool,
        derived_force_close_from_broker: bool,
        derived_force_close_max_tf_per_poll: int,
        derived_rebuild_use_tool: bool,
        derived_rebuild_tool_dry_run: bool,
        calendar_gate_enabled: bool,
        poll_diag_enabled: bool,
        market_weekend_close_dow: int,
        market_weekend_close_hm: str,
        market_weekend_open_dow: int,
        market_weekend_open_hm: str,
        market_daily_break_start_hm: str,
        market_daily_break_end_hm: str,
        market_daily_break_enabled: bool,
        heavy_budget_s: int,
    ) -> None:
        self._provider = provider
        self._data_root = data_root
        self._symbol = symbol
        self._config_path = config_path
        self._warmup_bars = warmup_bars
        self._safety_delay_s = safety_delay_s
        self._derived_tfs_s = derived_tfs_s
        self._broker_base_tfs_s = sorted({int(x) for x in broker_base_tfs_s if int(x) > 0})
        overlap = sorted(set(self._broker_base_tfs_s) & set(self._derived_tfs_s))
        if overlap:
            logging.warning(
                "Polling: derived_tfs_s перетинається з broker_base_tfs_s, видаляю з derived: %s",
                ",".join(str(x) for x in overlap),
            )
            self._derived_tfs_s = [x for x in self._derived_tfs_s if x not in overlap]
        self._broker_base_fetch_on_close = bool(broker_base_fetch_on_close)
        self._broker_base_max_tf_per_poll = max(0, int(broker_base_max_tf_per_poll))
        self._broker_base_cold_start_counts = {
            int(k): int(v)
            for k, v in broker_base_cold_start_counts.items()
            if int(k) > 0 and int(v) > 0
        }
        self._broker_base_cold_start_enabled = bool(broker_base_cold_start_enabled)
        self._day_anchor_offset_s = day_anchor_offset_s
        self._day_anchor_offset_s_d1 = day_anchor_offset_s_d1
        self._day_anchor_offset_s_d1_alt = day_anchor_offset_s_d1_alt
        self._day_anchor_offset_s_alt = day_anchor_offset_s_alt
        self._day_anchor_offset_s_alt2 = day_anchor_offset_s_alt2
        self._backfill_step_bars = backfill_step_bars
        self._backfill_every_n_polls = max(1, backfill_every_n_polls)
        self._derived_rebuild_lookback_bars = derived_rebuild_lookback_bars
        self._derived_tolerate_missing_minutes = max(0, int(derived_tolerate_missing_minutes))
        self._derived_backfill_from_broker = bool(derived_backfill_from_broker)
        self._derived_force_close_from_broker = bool(derived_force_close_from_broker)
        self._derived_force_close_max_tf_per_poll = max(0, int(derived_force_close_max_tf_per_poll))
        self._derived_rebuild_use_tool = bool(derived_rebuild_use_tool)
        self._derived_rebuild_tool_dry_run = bool(derived_rebuild_tool_dry_run)
        # CALENDAR_GATE: швидко вимкнути через config.
        self._calendar_gate_enabled = bool(calendar_gate_enabled)
        self._poll_diag_enabled = bool(poll_diag_enabled)
        self._market_weekend_close_dow = int(market_weekend_close_dow)
        self._market_weekend_close_hm = market_weekend_close_hm
        self._market_weekend_open_dow = int(market_weekend_open_dow)
        self._market_weekend_open_hm = market_weekend_open_hm
        self._market_daily_break_start_hm = market_daily_break_start_hm
        self._market_daily_break_end_hm = market_daily_break_end_hm
        self._market_daily_break_enabled = bool(market_daily_break_enabled)
        self._heavy_budget_s = max(0, int(heavy_budget_s))
        self._heavy_skipped = 0
        self._pending_backfill: List[Tuple[int, int]] = []
        self._pending_rebuild_recent = False

        self._writer = JsonlAppender(
            root=data_root,
            day_anchor_offset_s=day_anchor_offset_s,
            day_anchor_offset_s_d1=day_anchor_offset_s_d1,
            day_anchor_offset_s_d1_alt=day_anchor_offset_s_d1_alt,
            day_anchor_offset_s_alt=day_anchor_offset_s_alt,
            day_anchor_offset_s_alt2=day_anchor_offset_s_alt2,
        )
        m1_keep = max(6000, warmup_bars + 2000, self._derived_rebuild_lookback_bars + 2000)
        self._m1 = M1Buffer(max_keep=m1_keep)

        self._last_saved_m1_open_ms: Optional[int] = None
        self._last_saved_derived: Dict[int, int] = {}  # tf_s -> open_time_ms
        self._last_saved_base: Dict[int, int] = {}  # tf_s -> open_time_ms
        self._oldest_saved_m1_open_ms: Optional[int] = None
        self._backfill_cursor_open_ms: Optional[int] = None
        self._poll_counter: int = 0
        self._day_index_cache: Dict[str, set[int]] = {}
        self._missing_derived_backfill_attempted: Set[Tuple[int, int]] = set()
        self._group_logs: bool = False
        self._recent_history_errors: List[str] = []
        self._warn_throttle: Dict[str, Tuple[float, int]] = {}

    def enable_group_logging(self) -> None:
        self._group_logs = True

    def drain_history_errors(self) -> List[str]:
        out = list(self._recent_history_errors)
        self._recent_history_errors = []
        return out

    def _record_history_error(self, context: str, message: str) -> None:
        entry = f"{context}: {message}"
        self._recent_history_errors.append(entry)
        if not self._group_logs:
            logging.warning("History: %s", entry)
        else:
            logging.debug("History: %s", entry)

    def _capture_history_error(self) -> None:
        err = self._provider.consume_last_error()
        if err:
            context, message = err
            self._record_history_error(context, message)

    def _warn_throttled(self, key: str, message: str, every_s: int = 60) -> None:
        now = time.time()
        last_ts, suppressed = self._warn_throttle.get(key, (0.0, 0))
        if now - last_ts < every_s:
            self._warn_throttle[key] = (last_ts, suppressed + 1)
            return
        if suppressed > 0:
            logging.warning("%s [suppressed=%d]", message, suppressed)
        else:
            logging.warning("%s", message)
        self._warn_throttle[key] = (now, 0)

    def run_forever(self) -> None:
        self.bootstrap_and_warmup(log_detail=True)
        self._loop()

    def bootstrap_and_warmup(self, log_detail: bool = False) -> Dict[str, Any]:
        summary: Dict[str, Any] = {"symbol": self._symbol}
        summary.update(self._bootstrap_from_disk(log_detail=log_detail))
        summary.update(self._cold_start_base_from_broker(log_detail=log_detail))
        summary.update(self._warmup_history(log_detail=log_detail))
        return summary

    def poll_iteration(self) -> None:
        self._poll_once()
        self._poll_counter += 1
        remaining_s = self._seconds_to_next_minute()
        if self._heavy_budget_s > 0 and remaining_s < self._heavy_budget_s:
            self._heavy_skipped += 1
            logging.info(
                "Polling: heavy пропущено remaining=%.2fs budget=%ds skipped=%d",
                remaining_s,
                self._heavy_budget_s,
                self._heavy_skipped,
            )
            return
        if self._pending_rebuild_recent:
            self._pending_rebuild_recent = False
            self._rebuild_derived_recent()
            return
        if self._pending_backfill:
            start_ms, end_ms = self._pending_backfill.pop(0)
            written = self._backfill_range(start_ms, end_ms)
            if written > self._backfill_step_bars:
                self._pending_rebuild_recent = True
            return
        if self._poll_counter % self._backfill_every_n_polls == 0:
            written = self._backfill_step()
            if written > self._backfill_step_bars:
                self._pending_rebuild_recent = True
            return

    def close(self) -> None:
        self._writer.close()

    def _bootstrap_from_disk(self, log_detail: bool = False) -> Dict[str, Any]:
        last = tail_last_bar_time_ms(self._data_root, self._symbol, tf_s=60)
        self._last_saved_m1_open_ms = last
        first = head_first_bar_time_ms(self._data_root, self._symbol, tf_s=60)
        self._oldest_saved_m1_open_ms = first
        self._backfill_cursor_open_ms = first
        if log_detail:
            if last is not None:
                logging.debug(
                    "Старт: знайдено останній 1m бар на диску open_time_utc=%s",
                    ms_to_utc_dt(last).isoformat(),
                )
            else:
                logging.debug("Старт: на диску немає 1m історії для %s", self._symbol)

            if first is not None:
                logging.debug(
                    "Старт: знайдено перший 1m бар на диску open_time_utc=%s",
                    ms_to_utc_dt(first).isoformat(),
                )

        for tf_s in self._derived_tfs_s:
            last_d = tail_last_bar_time_ms(self._data_root, self._symbol, tf_s=tf_s)
            if last_d is not None:
                self._last_saved_derived[tf_s] = last_d

        for tf_s in self._broker_base_tfs_s:
            last_b = tail_last_bar_time_ms(self._data_root, self._symbol, tf_s=tf_s)
            if last_b is not None:
                self._last_saved_base[tf_s] = last_b

        return {
            "m1_last_exists": last is not None,
            "m1_first_exists": first is not None,
        }

    def _cold_start_base_from_broker(self, log_detail: bool = False) -> Dict[str, Any]:
        if not self._broker_base_cold_start_enabled:
            return {
                "cold_start_enabled": False,
                "cold_start_counts_empty": False,
                "cold_start_total_found": 0,
                "cold_start_total_written": 0,
                "cold_start_total_existing": 0,
                "cold_start_broker_empty": False,
            }
        if not self._broker_base_cold_start_counts:
            if log_detail:
                logging.debug("Cold-start base: порожні counts, пропуск.")
            return {
                "cold_start_enabled": True,
                "cold_start_counts_empty": True,
                "cold_start_total_found": 0,
                "cold_start_total_written": 0,
                "cold_start_total_existing": 0,
                "cold_start_broker_empty": False,
            }

        date_to = dt.datetime.now(dt.timezone.utc)
        total_written = 0
        total_existing = 0
        total_found = 0

        for tf_s, n in sorted(self._broker_base_cold_start_counts.items()):
            if self._broker_base_tfs_s and tf_s not in self._broker_base_tfs_s:
                continue
            if self._last_saved_base.get(tf_s) is not None:
                if log_detail:
                    logging.debug("Cold-start base: TF=%ds вже є на диску, пропуск.", tf_s)
                continue
            bars = self._provider.fetch_last_n_tf(self._symbol, tf_s=tf_s, n=n, date_to_utc=date_to)
            self._capture_history_error()
            if not bars:
                if log_detail:
                    logging.debug("Cold-start base: broker пустий TF=%ds", tf_s)
                continue

            for b in bars:
                total_found += 1
                if self._has_on_disk(tf_s, b.open_time_ms):
                    total_existing += 1
                    continue
                self._writer.append(b)
                self._mark_on_disk(tf_s, b.open_time_ms)
                total_written += 1
                last = self._last_saved_base.get(tf_s)
                if last is None or b.open_time_ms > last:
                    self._last_saved_base[tf_s] = b.open_time_ms

        if log_detail:
            logging.debug(
                "Cold-start base: written=%d existing=%d found=%d",
                total_written,
                total_existing,
                total_found,
            )

        return {
            "cold_start_enabled": True,
            "cold_start_counts_empty": False,
            "cold_start_total_found": total_found,
            "cold_start_total_written": total_written,
            "cold_start_total_existing": total_existing,
            "cold_start_broker_empty": total_found == 0,
        }

    def _warmup_history(self, log_detail: bool = False) -> Dict[str, Any]:
        """Підтягує warmup_bars останніх 1m барів і пише лише те, чого не було."""
        if log_detail:
            logging.debug(
                "Warmup: запит %d барів m1 (чанками при потребі).",
                self._warmup_bars,
            )

        cutoff_open = self._last_closed_cutoff_open_ms()
        date_to = ms_to_utc_dt(cutoff_open + 60_000)

        # Спроба: один запит на N барів “до останнього закритого”.
        bars = self._provider.fetch_last_n_m1(
            self._symbol, self._warmup_bars, date_to_utc=date_to
        )
        self._capture_history_error()
        if not bars:
            if log_detail:
                logging.debug(
                    "Warmup: history повернула 0 барів (ринок може бути закритий)."
                )
            return {"warmup_empty": True, "warmup_written": 0}

        written = self._ingest_m1_bars(
            bars,
            allow_older=True,
            write_missing_older=False,
            log_summary=log_detail,
            context="warmup",
        )
        return {"warmup_empty": False, "warmup_written": written}

    def _loop(self) -> None:
        logging.info(
            "Polling loop: режим B активний (тільки закриті 1m через history)."
        )
        try:
            while True:
                self._sleep_to_next_minute()
                self.poll_iteration()
        except KeyboardInterrupt:
            logging.info("Зупинено користувачем (KeyboardInterrupt). Завершую.")
        finally:
            self._writer.close()

    def _sleep_to_next_minute(self) -> None:
        sleep_to_next_minute(self._safety_delay_s)

    def _seconds_to_next_minute(self) -> float:
        now = time.time()
        next_min = (int(now // 60) + 1) * 60 + self._safety_delay_s
        return max(0.0, next_min - now)

    def _last_closed_cutoff_open_ms(self) -> int:
        return expected_last_closed_m1_open_ms(utc_now_ms())

    def _is_trading_minute(self, now_ms: int) -> bool:
        if not self._calendar_gate_enabled:
            return True
        # CALENDAR_GATE: легко видалити разом з конфігом.
        dt_now = ms_to_utc_dt(now_ms)
        dow = dt_now.weekday()
        hm_break_start = _parse_hm(self._market_daily_break_start_hm)
        hm_break_end = _parse_hm(self._market_daily_break_end_hm)
        if self._market_daily_break_enabled and hm_break_start and hm_break_end:
            start_min = hm_break_start[0] * 60 + hm_break_start[1]
            end_min = hm_break_end[0] * 60 + hm_break_end[1]
            cur_min = dt_now.hour * 60 + dt_now.minute
            if start_min < cur_min < end_min:
                return False

        hm_close = _parse_hm(self._market_weekend_close_hm)
        hm_open = _parse_hm(self._market_weekend_open_hm)
        if hm_close and hm_open:
            close_min = self._market_weekend_close_dow * 1440 + hm_close[0] * 60 + hm_close[1]
            open_min = self._market_weekend_open_dow * 1440 + hm_open[0] * 60 + hm_open[1]
            cur_min = dow * 1440 + dt_now.hour * 60 + dt_now.minute
            if close_min < open_min:
                if close_min <= cur_min < open_min:
                    return False
            else:
                if cur_min >= close_min or cur_min < open_min:
                    return False
        return True

    def _last_trading_minute_open_ms(self, now_ms: int) -> int:
        # Повертає open_time останньої торгової хвилини до now_ms.
        cur = (now_ms // 60_000) * 60_000 - 60_000
        for _ in range(7 * 24 * 60):
            if self._is_trading_minute(cur):
                return cur
            cur -= 60_000
        return (now_ms // 60_000) * 60_000 - 60_000

    def _enqueue_backfill_window(self, start_ms: int, end_ms: int, reason: str) -> None:
        if start_ms >= end_ms:
            return
        item = (start_ms, end_ms)
        if item in self._pending_backfill:
            return
        self._pending_backfill.append(item)
        self._warn_throttled(
            f"backfill_window:{reason}",
            (
                "Backfill: відкладено window [%s .. %s] reason=%s symbol=%s"
                % (
                    ms_to_utc_dt(start_ms).isoformat(),
                    ms_to_utc_dt(end_ms).isoformat(),
                    reason,
                    self._symbol,
                )
            ),
            every_s=60,
        )

    def _poll_once(self) -> None:
        now_ms = utc_now_ms()
        exp_open = expected_last_closed_m1_open_ms(now_ms)
        date_to = ms_to_utc_dt(exp_open + 60_000)

        market_open = self._is_trading_minute(now_ms)
        calendar_closed_now = self._calendar_gate_enabled and not market_open
        calendar_closed_exp = self._calendar_gate_enabled and not self._is_trading_minute(exp_open)
        base_anchor_open = exp_open
        if not self._is_trading_minute(exp_open):
            base_anchor_open = self._last_trading_minute_open_ms(exp_open)
        if self._broker_base_fetch_on_close:
            self._fetch_base_from_broker_on_close(base_anchor_open)
        if calendar_closed_now and not self._group_logs:
            logging.info(
                "Polling: calendar_closed now=%s exp_open=%s",
                ms_to_utc_dt(now_ms).isoformat(),
                ms_to_utc_dt(exp_open).isoformat(),
            )
        if calendar_closed_exp and not self._group_logs:
            logging.info(
                "Polling: calendar_closed exp_open=%s (skip retry/backfill)",
                ms_to_utc_dt(exp_open).isoformat(),
            )

        # Беремо 2 останні бари, щоб мати шанс побачити exp_open (на практиці інколи вистачає 1, але 2 надійніше).
        bars = self._provider.fetch_last_n_m1(self._symbol, n=2, date_to_utc=date_to)
        self._capture_history_error()

        if self._poll_diag_enabled:
            last_open = bars[-1].open_time_ms if bars else None
            logging.debug(
                "Polling: diag now=%s exp_open=%s last_open=%s market_open=%s bars=%d",
                ms_to_utc_dt(now_ms).isoformat(),
                ms_to_utc_dt(exp_open).isoformat(),
                ms_to_utc_dt(last_open).isoformat() if last_open is not None else "None",
                market_open,
                len(bars),
            )

        if not bars:
            logging.debug("Polling: барів немає (ймовірно market closed).")
            if calendar_closed_now or calendar_closed_exp:
                return
            return

        # Шукаємо бар із open_time_ms == exp_open.
        target = None
        for b in bars:
            if b.open_time_ms == exp_open:
                target = b
                break

        if target is None:
            if calendar_closed_exp:
                if bars:
                    self._ingest_m1_bars(
                        bars,
                        allow_older=True,
                        write_missing_older=True,
                        log_summary=True,
                        context="poll_miss",
                    )
                return
            # Якщо не знайшли — це або затримка “finalization”, або gap більший.
            # Робимо короткий retry перед heavy.
            self._warn_throttled(
                "missed_exp_open",
                (
                    "Polling: не знайдено очікуваний last-closed m1 open=%s; retry через 3s. symbol=%s"
                    % (ms_to_utc_dt(exp_open).isoformat(), self._symbol)
                ),
                every_s=60,
            )
            time.sleep(3)
            date_to_retry = dt.datetime.now(dt.timezone.utc)
            bars = self._provider.fetch_last_n_m1(self._symbol, n=2, date_to_utc=date_to_retry)
            self._capture_history_error()
            for b in bars:
                if b.open_time_ms == exp_open:
                    target = b
                    break
            if target is None:
                if bars:
                    self._ingest_m1_bars(
                        bars,
                        allow_older=True,
                        write_missing_older=True,
                        log_summary=True,
                        context="poll_miss",
                    )
                start_ms = exp_open - 30 * 60_000
                end_ms = exp_open
                self._enqueue_backfill_window(start_ms, end_ms, reason="missed_exp_open")
                return

        self._ingest_m1_bars([target], log_summary=True, context="poll")
        if self._broker_base_fetch_on_close:
            self._fetch_base_from_broker_on_close(target.open_time_ms)

    def _fetch_base_from_broker_on_close(self, anchor_open_ms: int) -> None:
        written = 0
        tried = 0
        for tf_s in self._broker_base_tfs_s:
            if (
                self._broker_base_max_tf_per_poll > 0
                and tried >= self._broker_base_max_tf_per_poll
            ):
                break
            anchor = select_anchor_offset_for_anchor_open_ms(
                tf_s,
                anchor_open_ms,
                self._day_anchor_offset_s,
                self._day_anchor_offset_s_alt,
                self._day_anchor_offset_s_alt2,
                self._day_anchor_offset_s_d1,
                self._day_anchor_offset_s_d1_alt,
            )
            tf_ms = tf_s * 1000
            b0 = floor_bucket_start_ms(anchor_open_ms, tf_s, anchor_offset_s=anchor)
            b1 = b0 + tf_ms
            expected_last = self._last_trading_minute_open_ms(b1 - 60_000)
            if anchor_open_ms != expected_last:
                continue
            if self._has_on_disk(tf_s, b0):
                continue

            tried += 1
            date_to = ms_to_utc_dt(b1)
            bars = self._provider.fetch_last_n_tf(
                self._symbol, tf_s=tf_s, n=1, date_to_utc=date_to
            )
            self._capture_history_error()
            if not bars:
                logging.debug(
                    "Base TF: broker пустий TF=%ds bucket=%s",
                    tf_s,
                    ms_to_utc_dt(b0).isoformat(),
                )
                continue
            b = bars[-1]
            if b.open_time_ms != b0:
                logging.debug(
                    "Base TF: mismatch TF=%ds open=%s очікувалось=%s",
                    tf_s,
                    ms_to_utc_dt(b.open_time_ms).isoformat(),
                    ms_to_utc_dt(b0).isoformat(),
                )
                continue

            self._writer.append(b)
            self._mark_on_disk(tf_s, b0)
            last = self._last_saved_base.get(tf_s)
            if last is None or b0 > last:
                self._last_saved_base[tf_s] = b0
            written += 1

        if written:
            logging.info(
                "Base TF: дописано=%d tried=%d",
                written,
                tried,
            )

    def _day_key(self, open_time_ms: int) -> str:
        return ms_to_utc_dt(open_time_ms).strftime("%Y%m%d")

    def _day_index_key(self, tf_s: int, day: str) -> str:
        return f"{tf_s}:{day}"

    def _load_day_index(self, tf_s: int, day: str) -> set[int]:
        key = self._day_index_key(tf_s, day)
        cached = self._day_index_cache.get(key)
        if cached is not None:
            return cached

        out = load_day_open_times(self._data_root, self._symbol, tf_s, day)
        self._day_index_cache[key] = out
        return out

    def _has_on_disk(self, tf_s: int, open_time_ms: int) -> bool:
        day = self._day_key(open_time_ms)
        idx = self._load_day_index(tf_s, day)
        return open_time_ms in idx

    def _mark_on_disk(self, tf_s: int, open_time_ms: int) -> None:
        day = self._day_key(open_time_ms)
        idx = self._load_day_index(tf_s, day)
        idx.add(open_time_ms)

    def _ingest_m1_bars(
        self,
        bars: List[CandleBar],
        allow_older: bool = False,
        write_missing_older: bool = False,
        log_summary: bool = False,
        context: str = "",
    ) -> int:
        """Дедуп + gap-detect + append SSOT + derive."""
        cutoff = self._last_closed_cutoff_open_ms()
        cutoff_iso = ms_to_utc_dt(cutoff).isoformat()
        written = 0
        skipped_cutoff = 0
        skipped_cutoff_last: Optional[int] = None
        skipped_dedup = 0
        derived_written = 0

        for b in bars:
            if self._last_saved_m1_open_ms is not None:
                gap_min = (b.open_time_ms - self._last_saved_m1_open_ms) // 60_000
                if (
                    gap_min >= 2
                    and b.o == b.h == b.low == b.c
                    and b.v <= 2
                ):
                    self._warn_throttled(
                        "flat_future_bar",
                        (
                            "M1: відсікання flat-bar open=%s gap_min=%d v=%.2f symbol=%s"
                            % (
                                ms_to_utc_dt(b.open_time_ms).isoformat(),
                                gap_min,
                                b.v,
                                self._symbol,
                            )
                        ),
                        every_s=300,
                    )
                    continue
            if b.open_time_ms > cutoff:
                skipped_cutoff += 1
                skipped_cutoff_last = b.open_time_ms
                continue

            if self._last_saved_m1_open_ms is not None and b.open_time_ms <= self._last_saved_m1_open_ms:
                if allow_older:
                    self._m1.upsert(b)
                    if write_missing_older and not self._has_on_disk(60, b.open_time_ms):
                        self._writer.append(b)
                        self._mark_on_disk(60, b.open_time_ms)
                        written += 1
                    if self._oldest_saved_m1_open_ms is None or b.open_time_ms < self._oldest_saved_m1_open_ms:
                        self._oldest_saved_m1_open_ms = b.open_time_ms
                    if self._backfill_cursor_open_ms is None or b.open_time_ms < self._backfill_cursor_open_ms:
                        self._backfill_cursor_open_ms = b.open_time_ms
                    continue
                skipped_dedup += 1
                continue

            # gap detect: якщо пропущено > 1 хв
            if self._last_saved_m1_open_ms is not None and b.open_time_ms > self._last_saved_m1_open_ms:
                gap_ms = b.open_time_ms - self._last_saved_m1_open_ms
                if gap_ms > 60_000:
                    # backfill пропущених хвилин
                    missing_start = self._last_saved_m1_open_ms + 60_000
                    missing_end = b.open_time_ms - 60_000
                    self._warn_throttled(
                        "gap_detect",
                        (
                            "Gap: пропущено %d хв; backfill [%s .. %s] symbol=%s"
                            % (
                                gap_ms // 60_000 - 1,
                                ms_to_utc_dt(missing_start).isoformat(),
                                ms_to_utc_dt(missing_end).isoformat(),
                                self._symbol,
                            )
                        ),
                        every_s=60,
                    )
                    self._enqueue_backfill_window(missing_start, missing_end, reason="gap_detect")

            # пишемо 1m SSOT
            self._writer.append(b)
            self._mark_on_disk(60, b.open_time_ms)
            self._m1.upsert(b)
            written += 1
            if self._last_saved_m1_open_ms is None or b.open_time_ms > self._last_saved_m1_open_ms:
                self._last_saved_m1_open_ms = b.open_time_ms
            if self._oldest_saved_m1_open_ms is None or b.open_time_ms < self._oldest_saved_m1_open_ms:
                self._oldest_saved_m1_open_ms = b.open_time_ms
            if self._backfill_cursor_open_ms is None or b.open_time_ms < self._backfill_cursor_open_ms:
                self._backfill_cursor_open_ms = b.open_time_ms

            logging.debug(
                "M1: записано open=%s o=%.5f h=%.5f low=%.5f c=%.5f v=%.2f",
                ms_to_utc_dt(b.open_time_ms).isoformat(),
                b.o,
                b.h,
                b.low,
                b.c,
                b.v,
            )

            # derive після кожного нового 1m
            derived_written += self._try_derive_all(anchor_open_ms=b.open_time_ms)

        if log_summary:
            ctx = context or "ingest"
            log_fn = logging.debug if self._group_logs else logging.info
            log_fn(
                "M1: %s записано=%d derived=%d skip_cutoff=%d skip_dedup=%d",
                ctx,
                written,
                derived_written,
                skipped_cutoff,
                skipped_dedup,
            )
        if skipped_cutoff:
            last_iso = (
                ms_to_utc_dt(skipped_cutoff_last).isoformat()
                if skipped_cutoff_last is not None
                else "None"
            )
            log_fn = logging.debug if self._group_logs else logging.info
            log_fn(
                "M1: пропуск не закритого бару count=%d last_open=%s cutoff=%s",
                skipped_cutoff,
                last_iso,
                cutoff_iso,
            )
        return written

    def _backfill_range(self, start_ms: int, end_ms: int) -> int:
        """Backfill діапазону [start_ms..end_ms] включно, чанками через date_to + quotes_count."""
        if start_ms > end_ms:
            return 0

        # cursor_end рухається назад
        cursor_end = (
            end_ms + 60_000
        )  # date_to на close наступного, щоб шанс включити end_ms
        chunk = 300  # ForexConnect часто “комфортно” на 300; більше може працювати, але 300 стабільно.

        collected: List[CandleBar] = []
        loops = 0

        while True:
            loops += 1
            if loops > 200:
                logging.error(
                    "Backfill: занадто багато ітерацій; зупиняюсь щоб не зависнути."
                )
                break

            date_to = ms_to_utc_dt(cursor_end)
            bars = self._provider.fetch_last_n_m1(
                self._symbol, n=chunk, date_to_utc=date_to
            )
            self._capture_history_error()
            if not bars:
                break

            # фільтр по діапазону
            for b in bars:
                if start_ms <= b.open_time_ms <= end_ms:
                    collected.append(b)

            oldest = bars[0].open_time_ms
            if oldest <= start_ms:
                break

            # рухаємо cursor_end до “перед oldest”
            cursor_end = oldest - 60_000

        if not collected:
            logging.debug(
                "Backfill: у діапазоні немає барів (можливо ринок був закритий)."
            )
            return 0

        collected.sort(key=lambda x: x.open_time_ms)

        # інжестимо як backfill (старі бари)
        written = self._ingest_m1_bars(
            collected,
            allow_older=True,
            write_missing_older=True,
            log_summary=True,
            context="backfill",
        )
        return written

    def _backfill_step(self) -> int:
        if self._backfill_cursor_open_ms is None:
            return 0

        end_ms = self._backfill_cursor_open_ms - 60_000
        if end_ms <= 0:
            return 0

        step = max(1, self._backfill_step_bars)
        date_to = ms_to_utc_dt(end_ms + 60_000)

        logging.debug(
            "Backfill: крок %d барів (date_to=%s)",
            step,
            date_to.isoformat(),
        )

        before_oldest = self._oldest_saved_m1_open_ms
        bars = self._provider.fetch_last_n_m1(
            self._symbol, n=step, date_to_utc=date_to
        )
        if not bars:
            logging.debug(
                "Backfill: нових барів не знайдено; зупиняю backfill symbol=%s cursor=%s",
                self._symbol,
                ms_to_utc_dt(self._backfill_cursor_open_ms).isoformat()
                if self._backfill_cursor_open_ms is not None
                else "None",
            )
            self._backfill_cursor_open_ms = None
            return 0

        written = self._ingest_m1_bars(bars, allow_older=True, write_missing_older=True)

        after_oldest = self._oldest_saved_m1_open_ms
        if before_oldest == after_oldest:
            logging.info("Backfill: нових барів не знайдено; зупиняю backfill.")
            self._backfill_cursor_open_ms = None
            return 0

        oldest_fetched = bars[0].open_time_ms
        self._backfill_cursor_open_ms = oldest_fetched
        return written

    def _rebuild_derived_recent(self) -> None:
        earliest = self._m1.earliest_open_ms()
        latest = self._m1.latest_open_ms()
        if earliest is None or latest is None:
            return

        lookback = max(0, int(self._derived_rebuild_lookback_bars))
        start_ms = max(earliest, latest - lookback * 60_000)

        if self._derived_rebuild_use_tool:
            self._rebuild_derived_via_tool(start_ms, latest)
            return

        logging.warning(
            "Rebuild derived: вимкнено (use_tool=false), пропуск rebuild у core"
        )

    def _rebuild_derived_via_tool(self, start_ms: int, end_ms: int) -> None:
        tf_list = ",".join(str(x) for x in self._derived_tfs_s)
        project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
        args = [
            sys.executable,
            os.path.join(project_root, "tools", "rebuild_derived.py"),
            "--config",
            self._config_path,
            "--symbol",
            self._symbol,
            "--tf",
            tf_list,
            "--start-utc",
            ms_to_utc_dt(start_ms).isoformat(),
            "--end-utc",
            ms_to_utc_dt(end_ms).isoformat(),
        ]
        if self._derived_rebuild_tool_dry_run:
            args.append("--dry-run")

        logging.info(
            "Rebuild derived (tool): запуск %s",
            " ".join(args),
        )
        try:
            env = os.environ.copy()
            prev = env.get("PYTHONPATH", "")
            env["PYTHONPATH"] = (
                project_root if not prev else f"{project_root}{os.pathsep}{prev}"
            )
            subprocess.run(args, check=True, cwd=project_root, env=env)
        except Exception:
            logging.exception("Rebuild derived (tool): помилка виконання")

    def _try_derive_all(self, anchor_open_ms: int) -> int:
        """Пробуємо збудувати всі derived TF на основі останнього 1m (anchor_open_ms)."""
        written = 0
        for tf_s in self._derived_tfs_s:
            anchor = select_anchor_offset_for_anchor_open_ms(
                tf_s,
                anchor_open_ms,
                self._day_anchor_offset_s,
                self._day_anchor_offset_s_alt,
                self._day_anchor_offset_s_alt2,
                self._day_anchor_offset_s_d1,
                self._day_anchor_offset_s_d1_alt,
            )
            tf_ms = tf_s * 1000
            b0 = floor_bucket_start_ms(anchor_open_ms, tf_s, anchor_offset_s=anchor)
            b1 = b0 + tf_ms
            if anchor_open_ms != (b1 - 60_000):
                continue

            missing = self._m1.missing_count(b0, b1)
            if missing != 0:
                continue

            d = derive_from_m1_for_anchor(
                self._symbol,
                tf_s=tf_s,
                m1=self._m1,
                anchor_open_ms=anchor_open_ms,
                anchor_offset_s=anchor,
            )

            if d is None:
                continue

            last = self._last_saved_derived.get(tf_s)
            if last is not None and d.open_time_ms <= last:
                if self._has_on_disk(tf_s, d.open_time_ms):
                    continue

            if not self._has_on_disk(tf_s, d.open_time_ms):
                self._writer.append(d)
                self._mark_on_disk(tf_s, d.open_time_ms)
                written += 1
            if last is None or d.open_time_ms > last:
                self._last_saved_derived[tf_s] = d.open_time_ms

            logging.debug(
                "DERIVED tf=%ds: записано open=%s o=%.5f h=%.5f low=%.5f c=%.5f v=%.2f",
                tf_s,
                ms_to_utc_dt(d.open_time_ms).isoformat(),
                d.o,
                d.h,
                d.low,
                d.c,
                d.v,
            )

        return written


class MultiSymbolRunner:
    def __init__(self, engines: List[PollingConnectorB]) -> None:
        self._engines = engines
        self._safety_delay_s = max((e._safety_delay_s for e in engines), default=0)  # noqa: SLF001
        for e in self._engines:
            e.enable_group_logging()
        self._last_calendar_closed_exp_open: Optional[int] = None
        self._last_calendar_closed_log_ts: float = 0.0

    def _log_calendar_closed_if_needed(self) -> None:
        if not self._engines:
            return
        e0 = self._engines[0]
        if not e0._calendar_gate_enabled:  # noqa: SLF001
            return
        now_ms = utc_now_ms()
        if e0._is_trading_minute(now_ms):  # noqa: SLF001
            return
        exp_open = expected_last_closed_m1_open_ms(now_ms)
        if self._last_calendar_closed_exp_open == exp_open:
            return
        if now_ms - int(self._last_calendar_closed_log_ts * 1000) < 300_000:
            return
        self._last_calendar_closed_exp_open = exp_open
        self._last_calendar_closed_log_ts = time.time()
        logging.info(
            "Polling: calendar_closed now=%s exp_open=%s",
            ms_to_utc_dt(now_ms).isoformat(),
            ms_to_utc_dt(exp_open).isoformat(),
        )

    def _drain_history_errors(self) -> None:
        total = len(self._engines)
        errors_by_symbol: Dict[str, List[str]] = {}
        for e in self._engines:
            errs = e.drain_history_errors()
            if errs:
                errors_by_symbol[e._symbol] = errs  # noqa: SLF001
        if not errors_by_symbol:
            return
        symbols = sorted(errors_by_symbol.keys())
        logging.warning(
            "History: помилки для %d/%d symbols=%s",
            len(symbols),
            total,
            ",".join(symbols),
        )
        for sym, errs in errors_by_symbol.items():
            for err in errs:
                logging.debug("History: %s: %s", sym, err)

    def _log_bootstrap_summary(self, summaries: List[Dict[str, Any]]) -> None:
        total = len(summaries)
        if total <= 0:
            return
        missing_last = [s["symbol"] for s in summaries if not s.get("m1_last_exists")]
        missing_first = [s["symbol"] for s in summaries if not s.get("m1_first_exists")]
        if missing_last:
            logging.warning(
                "Старт: останній 1m на диску %d/%d (немає: %s)",
                total - len(missing_last),
                total,
                ",".join(missing_last),
            )
        else:
            logging.info("Старт: останній 1m на диску %d/%d", total, total)

        if missing_first:
            logging.warning(
                "Старт: перший 1m на диску %d/%d (немає: %s)",
                total - len(missing_first),
                total,
                ",".join(missing_first),
            )
        else:
            logging.info("Старт: перший 1m на диску %d/%d", total, total)

        counts_empty = [s["symbol"] for s in summaries if s.get("cold_start_counts_empty")]
        broker_empty = [s["symbol"] for s in summaries if s.get("cold_start_broker_empty")]
        enabled = any(s.get("cold_start_enabled") for s in summaries)
        if not enabled:
            logging.info("Cold-start base: вимкнено для %d/%d", total, total)
        else:
            total_written = sum(int(s.get("cold_start_total_written", 0)) for s in summaries)
            total_existing = sum(int(s.get("cold_start_total_existing", 0)) for s in summaries)
            total_found = sum(int(s.get("cold_start_total_found", 0)) for s in summaries)
            logging.info(
                "Cold-start base: written=%d existing=%d found=%d",
                total_written,
                total_existing,
                total_found,
            )
            if counts_empty:
                logging.warning(
                    "Cold-start base: порожні counts для %d/%d (%s)",
                    len(counts_empty),
                    total,
                    ",".join(counts_empty),
                )
            if broker_empty:
                logging.warning(
                    "Cold-start base: broker пустий для %d/%d (%s)",
                    len(broker_empty),
                    total,
                    ",".join(broker_empty),
                )

        warmup_empty = [s["symbol"] for s in summaries if s.get("warmup_empty")]
        warmup_written = sum(int(s.get("warmup_written", 0)) for s in summaries)
        if warmup_empty:
            logging.warning(
                "Warmup: history=0 для %d/%d (%s)",
                len(warmup_empty),
                total,
                ",".join(warmup_empty),
            )
        else:
            logging.info("Warmup: history ok %d/%d", total, total)
        logging.info("Warmup: written=%d", warmup_written)

    def run_forever(self) -> None:
        symbols = [e._symbol for e in self._engines]  # noqa: SLF001
        logging.info("Polling loop: multi активний symbols=%s", ",".join(symbols))
        try:
            summaries: List[Dict[str, Any]] = []
            for e in self._engines:
                summaries.append(e.bootstrap_and_warmup(log_detail=False))
            self._log_bootstrap_summary(summaries)
            self._drain_history_errors()
            while True:
                sleep_to_next_minute(self._safety_delay_s)
                self._log_calendar_closed_if_needed()
                for e in self._engines:
                    e.poll_iteration()
                self._drain_history_errors()
        except KeyboardInterrupt:
            logging.info("Зупинено користувачем (KeyboardInterrupt). Завершую.")
        finally:
            for e in self._engines:
                e.close()
