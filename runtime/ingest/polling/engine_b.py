from __future__ import annotations

import datetime as dt
import logging
import time
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING

from core.model.bars import CandleBar, assert_invariants, ms_to_utc_dt
from runtime.ingest.market_calendar import MarketCalendar
from runtime.store.ssot_jsonl import JsonlAppender, load_day_open_times, tail_last_bar_time_ms

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


def expected_last_closed_m5_open_ms(now_ms: int) -> int:
    tf_ms = 300_000
    return (now_ms // tf_ms) * tf_ms - tf_ms


def sleep_to_next_minute(safety_delay_s: int) -> None:
    now = time.time()
    next_min = (int(now // 60) + 1) * 60
    target = next_min + safety_delay_s
    delay = max(0.0, target - now)
    time.sleep(delay)


class M5Buffer:
    """Буфер закритих 5m барів у памʼяті для побудови derived TF."""

    def __init__(self, max_keep: int = 2000) -> None:
        self._max_keep = max_keep
        self._by_open_ms: Dict[int, CandleBar] = {}
        self._sorted_keys: List[int] = []

    def upsert(self, bar: CandleBar) -> None:
        if bar.tf_s != 300:
            raise ValueError("M5Buffer приймає тільки tf_s=300")
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
        step = 300_000
        for t in range(start_ms, end_ms, step):
            if t not in self._by_open_ms:
                return False
        return True

    def range_bars(self, start_ms: int, end_ms: int) -> List[CandleBar]:
        step = 300_000
        out: List[CandleBar] = []
        for t in range(start_ms, end_ms, step):
            b = self._by_open_ms.get(t)
            if b is None:
                return []
            out.append(b)
        return out

    def missing_count(self, start_ms: int, end_ms: int) -> int:
        step = 300_000
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


def derive_from_m5_for_anchor(
    symbol: str,
    tf_s: int,
    m5: M5Buffer,
    anchor_open_ms: int,
    anchor_offset_s: int = 0,
) -> Optional[CandleBar]:
    tf_ms = tf_s * 1000

    b0 = floor_bucket_start_ms(anchor_open_ms, tf_s, anchor_offset_s=anchor_offset_s)
    b1 = b0 + tf_ms

    if anchor_open_ms != (b1 - 300_000):
        return None

    if not m5.has_range_complete(b0, b1):
        return None

    bars = m5.range_bars(b0, b1)
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
        o=o,
        h=h,
        low=low,
        c=c,
        v=v,
        complete=True,
        src="derived",
    )
    assert_invariants(out, anchor_offset_s=anchor_offset_s)
    return out


def _is_flat_bar(bar: CandleBar, max_volume: int) -> bool:
    return bar.o == bar.h == bar.low == bar.c and bar.v <= max_volume


class PollingConnectorB:
    def __init__(
        self,
        provider: "FxcmHistoryProvider",
        data_root: str,
        symbol: str,
        config_path: str,
        warmup_bars: int,
        safety_delay_s: int,
        m5_tail_fetch_n: int,
        m5_tail_stale_s: int,
        flat_bar_max_volume: int,
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
        market_calendar: MarketCalendar,
    ) -> None:
        self._provider = provider
        self._data_root = data_root
        self._symbol = symbol
        self._config_path = config_path
        self._warmup_bars = warmup_bars
        self._safety_delay_s = safety_delay_s
        self._m5_tail_fetch_n = max(1, int(m5_tail_fetch_n))
        self._m5_tail_stale_ms = max(0, int(m5_tail_stale_s)) * 1000
        self._flat_bar_max_volume = max(0, int(flat_bar_max_volume))
        self._derived_tfs_s = derived_tfs_s
        if 300 in self._derived_tfs_s:
            logging.warning(
                "Polling: derived_tfs_s містить 300, 5m стрімиться окремо; прибираю з derived."
            )
            self._derived_tfs_s = [x for x in self._derived_tfs_s if x != 300]
        if 180 in self._derived_tfs_s:
            logging.warning(
                "Polling: derived_tfs_s містить 180, M3 вимкнено; прибираю з derived."
            )
            self._derived_tfs_s = [x for x in self._derived_tfs_s if x != 180]
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
        # CALENDAR_GATE: швидко вимкнути через config.
        self._calendar = market_calendar
        self._calendar_gate_enabled = bool(market_calendar.enabled)
        self._heavy_skipped = 0

        self._writer = JsonlAppender(
            root=data_root,
            day_anchor_offset_s=day_anchor_offset_s,
            day_anchor_offset_s_d1=day_anchor_offset_s_d1,
            day_anchor_offset_s_d1_alt=day_anchor_offset_s_d1_alt,
            day_anchor_offset_s_alt=day_anchor_offset_s_alt,
            day_anchor_offset_s_alt2=day_anchor_offset_s_alt2,
        )
        m5_keep = max(2000, warmup_bars + 500)
        self._m5 = M5Buffer(max_keep=m5_keep)

        self._derived_from_m5_tfs = list(self._derived_tfs_s)

        self._last_saved_m5_open_ms: Optional[int] = None
        self._last_saved_derived: Dict[int, int] = {}  # tf_s -> open_time_ms
        self._last_saved_base: Dict[int, int] = {}  # tf_s -> open_time_ms
        self._poll_counter: int = 0
        self._day_index_cache: Dict[str, set[int]] = {}
        self._group_logs: bool = False
        self._recent_history_errors: List[Tuple[str, str, int]] = []
        self._warn_throttle: Dict[str, Tuple[float, int, str]] = {}
        self._m5_tail_state: str = "OK"

    def enable_group_logging(self) -> None:
        self._group_logs = True

    def drain_history_errors(self) -> List[Tuple[str, str, int]]:
        out = list(self._recent_history_errors)
        self._recent_history_errors = []
        return out

    def _record_history_error(self, context: str, message: str) -> None:
        self._recent_history_errors.append((context, message, utc_now_ms()))

    def _capture_history_error(self) -> None:
        err = self._provider.consume_last_error()
        if err:
            context, message = err
            self._record_history_error(context, message)

    def _warn_throttled(self, key: str, message: str, every_s: int = 600) -> None:
        now = time.time()
        last_ts, suppressed, last_msg = self._warn_throttle.get(key, (0.0, 0, ""))
        if now - last_ts < every_s:
            self._warn_throttle[key] = (last_ts, suppressed + 1, message)
            return
        if suppressed > 0:
            logging.warning(
                "SUPPRESSED key=%s suppressed=%d window_s=%d last=%s",
                key,
                suppressed,
                int(now - last_ts),
                last_msg,
            )
        logging.warning("%s", message)
        self._warn_throttle[key] = (now, 0, message)

    def run_forever(self) -> None:
        self.bootstrap_and_warmup(log_detail=True)
        self._loop()

    def bootstrap_and_warmup(self, log_detail: bool = False) -> Dict[str, Any]:
        summary: Dict[str, Any] = {"symbol": self._symbol}
        summary.update(self._bootstrap_from_disk(log_detail=log_detail))
        summary.update(self._cold_start_base_from_broker(log_detail=log_detail))
        summary.update(self._warmup_m5_tail(log_detail=log_detail))
        return summary

    def poll_iteration(self) -> None:
        if self._broker_base_fetch_on_close:
            anchor_open = self._last_trading_minute_open_ms(utc_now_ms())
            self._fetch_base_from_broker_on_close(anchor_open)
        self._poll_m5_once()
        self._poll_counter += 1

    def close(self) -> None:
        self._writer.close()

    def _bootstrap_from_disk(self, log_detail: bool = False) -> Dict[str, Any]:
        last_m5 = tail_last_bar_time_ms(self._data_root, self._symbol, tf_s=300)
        self._last_saved_m5_open_ms = last_m5
        if log_detail:
            if last_m5 is not None:
                logging.debug(
                    "Старт: знайдено останній 5m бар на диску open_time_utc=%s",
                    ms_to_utc_dt(last_m5).isoformat(),
                )
            else:
                logging.debug("Старт: на диску немає 5m історії для %s", self._symbol)

        for tf_s in self._derived_tfs_s:
            last_d = tail_last_bar_time_ms(self._data_root, self._symbol, tf_s=tf_s)
            if last_d is not None:
                self._last_saved_derived[tf_s] = last_d

        for tf_s in self._broker_base_tfs_s:
            last_b = tail_last_bar_time_ms(self._data_root, self._symbol, tf_s=tf_s)
            if last_b is not None:
                self._last_saved_base[tf_s] = last_b

        return {"m5_last_exists": last_m5 is not None}

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

    def _warmup_m5_tail(self, log_detail: bool = False) -> Dict[str, Any]:
        if log_detail:
            logging.debug(
                "Warmup: запит %d барів m5 (tail).",
                self._warmup_bars,
            )

        cutoff_open = expected_last_closed_m5_open_ms(utc_now_ms())
        if cutoff_open <= 0:
            return {"warmup_empty": True, "warmup_written": 0}
        date_to = ms_to_utc_dt(cutoff_open + 300_000)

        bars = self._provider.fetch_last_n_tf(
            self._symbol, tf_s=300, n=self._warmup_bars, date_to_utc=date_to
        )
        self._capture_history_error()
        if not bars:
            if log_detail:
                logging.debug(
                    "Warmup: history повернула 0 барів (ринок може бути закритий)."
                )
            return {"warmup_empty": True, "warmup_written": 0}

        bars = [b for b in bars if b.open_time_ms <= cutoff_open]
        if not bars:
            return {"warmup_empty": True, "warmup_written": 0}

        written = self._ingest_m5_bars(
            bars,
            allow_older=True,
            write_missing_older=True,
            log_summary=log_detail,
            context="warmup_m5",
        )
        return {"warmup_empty": False, "warmup_written": written}

    def _loop(self) -> None:
        logging.info(
            "Polling loop: режим B активний (закриті 1m + 5m через history)."
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

    def _last_trading_minute_open_ms(self, now_ms: int) -> int:
        # Повертає open_time останньої торгової хвилини до now_ms.
        cur = (now_ms // 60_000) * 60_000 - 60_000
        for _ in range(7 * 24 * 60):
            if self._provider.is_market_open(self._symbol, cur, self._calendar):
                return cur
            cur -= 60_000
        return (now_ms // 60_000) * 60_000 - 60_000

    def _poll_m5_once(self) -> None:
        now_ms = utc_now_ms()
        tf_ms = 300_000
        cutoff_open_ms = (now_ms // tf_ms) * tf_ms - tf_ms
        if cutoff_open_ms <= 0:
            return
        date_to = ms_to_utc_dt(cutoff_open_ms + tf_ms)

        bars = self._provider.fetch_last_n_tf(
            self._symbol,
            tf_s=300,
            n=self._m5_tail_fetch_n,
            date_to_utc=date_to,
        )
        self._capture_history_error()
        if not bars:
            return

        bars = [b for b in bars if b.open_time_ms <= cutoff_open_ms]
        if not bars:
            return

        max_open_ms = max(b.open_time_ms for b in bars)
        last_saved = self._last_saved_m5_open_ms
        if last_saved is not None and max_open_ms <= last_saved:
            tail_age_ms = now_ms - (last_saved + tf_ms)
            if (
                self._m5_tail_stale_ms > 0
                and tail_age_ms > self._m5_tail_stale_ms
                and self._m5_tail_state != "STALE"
            ):
                self._m5_tail_state = "STALE"
                logging.warning(
                    "M5_TAIL_STALE symbol=%s last_saved=%s age_ms=%d",
                    self._symbol,
                    ms_to_utc_dt(last_saved).isoformat(),
                    tail_age_ms,
                )
            return

        bars.sort(key=lambda b: b.open_time_ms)
        self._ingest_m5_bars(
            bars,
            allow_older=True,
            write_missing_older=True,
            log_summary=True,
            context="tail_m5",
        )
        if self._m5_tail_state == "STALE":
            self._m5_tail_state = "OK"
            logging.info("M5_TAIL_OK symbol=%s", self._symbol)

    def _fetch_base_from_broker_on_close(self, anchor_open_ms: int) -> None:
        written = 0
        tried = 0
        last_trading_open = self._last_trading_minute_open_ms(anchor_open_ms)
        for tf_s in self._broker_base_tfs_s:
            if (
                self._broker_base_max_tf_per_poll > 0
                and tried >= self._broker_base_max_tf_per_poll
            ):
                break
            anchor = select_anchor_offset_for_anchor_open_ms(
                tf_s,
                last_trading_open,
                self._day_anchor_offset_s,
                self._day_anchor_offset_s_alt,
                self._day_anchor_offset_s_alt2,
                self._day_anchor_offset_s_d1,
                self._day_anchor_offset_s_d1_alt,
            )
            tf_ms = tf_s * 1000
            b0 = floor_bucket_start_ms(last_trading_open, tf_s, anchor_offset_s=anchor)
            b1 = b0 + tf_ms
            expected_last = self._last_trading_minute_open_ms(b1 - 60_000)
            if last_trading_open != expected_last:
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

    def _ingest_m5_bars(
        self,
        bars: List[CandleBar],
        allow_older: bool = False,
        write_missing_older: bool = False,
        log_summary: bool = False,
        context: str = "",
    ) -> int:
        written = 0
        skipped_dedup = 0
        skipped_flat = 0
        derived_written = 0

        for b in bars:
            if _is_flat_bar(b, self._flat_bar_max_volume):
                skipped_flat += 1
                continue
            if self._last_saved_m5_open_ms is not None and b.open_time_ms <= self._last_saved_m5_open_ms:
                if allow_older:
                    self._m5.upsert(b)
                    if write_missing_older and not self._has_on_disk(300, b.open_time_ms):
                        self._writer.append(b)
                        self._mark_on_disk(300, b.open_time_ms)
                        written += 1
                    continue
                skipped_dedup += 1
                continue

            self._writer.append(b)
            self._mark_on_disk(300, b.open_time_ms)
            self._m5.upsert(b)
            written += 1
            if self._last_saved_m5_open_ms is None or b.open_time_ms > self._last_saved_m5_open_ms:
                self._last_saved_m5_open_ms = b.open_time_ms

            derived_written += self._try_derive_from_m5(anchor_open_ms=b.open_time_ms)

        if log_summary:
            ctx = context or "ingest_m5"
            log_fn = logging.debug if self._group_logs else logging.info
            log_fn(
                "M5: %s записано=%d derived=%d skip_dedup=%d skip_flat=%d",
                ctx,
                written,
                derived_written,
                skipped_dedup,
                skipped_flat,
            )
        return written

    def _try_derive_from_m5(self, anchor_open_ms: int) -> int:
        if not self._derived_from_m5_tfs:
            return 0
        written = 0
        for tf_s in self._derived_from_m5_tfs:
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
            if anchor_open_ms != (b1 - 300_000):
                continue

            if not self._m5.has_range_complete(b0, b1):
                continue

            d = derive_from_m5_for_anchor(
                self._symbol,
                tf_s=tf_s,
                m5=self._m5,
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

        return written


class MultiSymbolRunner:
    def __init__(
        self,
        engines: List[PollingConnectorB],
        history_summary_interval_s: int,
        history_still_failing_interval_s: int,
        history_circuit_fail_streak: int,
        history_circuit_base_s: int,
        history_circuit_max_s: int,
        history_circuit_log_interval_s: int,
        history_symbols_sample_n: int,
        history_network_error_escalate_s: int,
    ) -> None:
        self._engines = engines
        self._safety_delay_s = max((e._safety_delay_s for e in engines), default=0)  # noqa: SLF001
        for e in self._engines:
            e.enable_group_logging()
        self._calendar_state_by_symbol: Dict[str, bool] = {}
        self._history_summary_interval_s = max(60, int(history_summary_interval_s))
        self._history_still_failing_interval_s = max(60, int(history_still_failing_interval_s))
        self._history_circuit_fail_streak = max(1, int(history_circuit_fail_streak))
        self._history_circuit_base_s = max(60, int(history_circuit_base_s))
        self._history_circuit_max_s = max(self._history_circuit_base_s, int(history_circuit_max_s))
        self._history_circuit_log_interval_s = max(60, int(history_circuit_log_interval_s))
        self._history_symbols_sample_n = max(1, int(history_symbols_sample_n))
        self._history_network_error_escalate_s = max(60, int(history_network_error_escalate_s))
        self._history_state: str = "OK"
        self._history_fail_streak: int = 0
        self._history_first_fail_ts: Optional[float] = None
        self._history_last_fail_ts: Optional[float] = None
        self._history_last_err_kind: str = ""
        self._history_last_err_msg: str = ""
        self._history_suppressed_count: int = 0
        self._history_last_summary_ts: float = 0.0
        self._history_backoff_until_ts: float = 0.0
        self._history_last_circuit_log_ts: float = 0.0

    def _format_symbols_sample(self, symbols: List[str]) -> Tuple[str, int]:
        if not symbols:
            return "", 0
        sample = symbols[: self._history_symbols_sample_n]
        and_more = max(0, len(symbols) - len(sample))
        return ",".join(sample), and_more

    def _error_sample(self, context: str, message: str, limit: int = 180) -> str:
        raw = f"{context}: {message}"
        if len(raw) <= limit:
            return raw
        return raw[: limit - 3] + "..."

    def _classify_history_error(self, message: str, is_market_open: bool) -> str:
        if not is_market_open:
            return "calendar_closed"
        msg = message.lower()
        if "session" in msg or "сесія не відкрита" in msg:
            return "session_expired"
        if "auth" in msg or "login" in msg or "invalid" in msg or "access denied" in msg:
            return "auth_failed"
        if "rate" in msg or "too many" in msg or "429" in msg:
            return "rate_limited"
        if "dns" in msg or "name or service not known" in msg:
            return "dns_error"
        if "timeout" in msg or "timed out" in msg or "http request failed" in msg:
            return "network_timeout"
        if "instrument is not found" in msg or "instrument" in msg or "offer" in msg:
            return "instrument_not_found"
        if "500" in msg or "502" in msg or "503" in msg or "504" in msg:
            return "provider_error"
        if "parse" in msg or "missing key" in msg or "row" in msg or "keyerror" in msg:
            return "parse_error"
        return "provider_error"

    def _history_log_level(self, reason_code: str, fail_duration_s: int) -> int:
        if reason_code == "calendar_closed":
            return logging.INFO
        if reason_code in ("auth_failed", "session_expired"):
            return logging.ERROR
        if (
            reason_code in ("network_timeout", "dns_error")
            and fail_duration_s >= self._history_network_error_escalate_s
        ):
            return logging.ERROR
        if reason_code == "rate_limited":
            return logging.WARNING
        return logging.WARNING

    def _log_calendar_closed_if_needed(self) -> None:
        if not self._engines:
            return
        now_ms = utc_now_ms()
        for e in self._engines:
            if not e._calendar_gate_enabled:  # noqa: SLF001
                continue
            is_open = e._provider.is_market_open(e._symbol, now_ms, e._calendar)  # noqa: SLF001
            prev = self._calendar_state_by_symbol.get(e._symbol)
            if prev is None:
                self._calendar_state_by_symbol[e._symbol] = is_open
                continue
            if prev == is_open:
                continue
            self._calendar_state_by_symbol[e._symbol] = is_open
            if is_open:
                logging.info(
                    "CALENDAR_STATE_CHANGE symbol=%s state=open now=%s",
                    e._symbol,
                    ms_to_utc_dt(now_ms).isoformat(),
                )
            else:
                exp_open = expected_last_closed_m5_open_ms(now_ms)
                logging.info(
                    "CALENDAR_STATE_CHANGE symbol=%s state=closed now=%s next_open_m5=%s",
                    e._symbol,
                    ms_to_utc_dt(now_ms).isoformat(),
                    ms_to_utc_dt(exp_open).isoformat(),
                )

    def _drain_history_errors(self) -> None:
        total = len(self._engines)
        errors_by_symbol: Dict[str, List[str]] = {}
        engine_by_symbol = {e._symbol: e for e in self._engines}  # noqa: SLF001
        for e in self._engines:
            errs = e.drain_history_errors()
            if errs:
                errors_by_symbol[e._symbol] = errs  # noqa: SLF001

        now_s = time.time()
        now_ms = int(now_s * 1000)

        if not errors_by_symbol:
            if self._history_state == "FAIL":
                recovered_after = 0
                if self._history_first_fail_ts is not None:
                    recovered_after = int(now_s - self._history_first_fail_ts)
                logging.info(
                    "HISTORY_STATE_CHANGE to=OK recovered_after_s=%d",
                    recovered_after,
                )
            self._history_state = "OK"
            self._history_fail_streak = 0
            self._history_first_fail_ts = None
            self._history_last_fail_ts = None
            self._history_last_err_kind = ""
            self._history_last_err_msg = ""
            self._history_suppressed_count = 0
            self._history_last_summary_ts = 0.0
            return

        failed = len(errors_by_symbol)
        symbols = sorted(errors_by_symbol.keys())
        symbols_sample, and_more = self._format_symbols_sample(symbols)

        reason_counts: Counter[str] = Counter()
        first_error_sample = ""
        last_err_kind = ""
        last_err_msg = ""
        for sym, errs in errors_by_symbol.items():
            eng = engine_by_symbol.get(sym)
            is_open = True
            if eng is not None and eng._calendar_gate_enabled:  # noqa: SLF001
                is_open = eng._calendar.is_trading_minute(now_ms)  # noqa: SLF001
            for context, message, _ts in errs:
                combined = f"{context}: {message}"
                reason = self._classify_history_error(combined, is_open)
                reason_counts[reason] += 1
                last_err_kind = reason
                last_err_msg = combined
                if not first_error_sample:
                    first_error_sample = self._error_sample(context, message)

        top_reason = "provider_error"
        top_reason_count = 0
        if reason_counts:
            top_reason, top_reason_count = reason_counts.most_common(1)[0]

        if self._history_state == "FAIL":
            self._history_fail_streak += 1
        else:
            self._history_state = "FAIL"
            self._history_fail_streak = 1
            self._history_first_fail_ts = now_s
            self._history_last_summary_ts = 0.0
            self._history_suppressed_count = 0

        self._history_last_fail_ts = now_s
        self._history_last_err_kind = last_err_kind or top_reason
        self._history_last_err_msg = last_err_msg

        fail_duration_s = 0
        if self._history_first_fail_ts is not None:
            fail_duration_s = int(now_s - self._history_first_fail_ts)

        backoff_s = 0
        if failed == total and self._history_fail_streak >= self._history_circuit_fail_streak:
            backoff_s = min(
                self._history_circuit_max_s,
                self._history_circuit_base_s
                * (2 ** min(self._history_fail_streak - self._history_circuit_fail_streak, 2)),
            )
            until_ts = now_s + backoff_s
            if until_ts > self._history_backoff_until_ts:
                self._history_backoff_until_ts = until_ts

        level = self._history_log_level(top_reason, fail_duration_s)

        if self._history_last_summary_ts <= 0.0:
            logging.log(
                level,
                "HISTORY_STATE_CHANGE to=FAIL failed=%d total=%d reason_code=%s reason_top=%s(%d) first_error_sample=%s symbols_sample=%s and_more=%d",
                failed,
                total,
                top_reason,
                top_reason,
                top_reason_count,
                first_error_sample,
                symbols_sample,
                and_more,
            )
            self._history_last_summary_ts = now_s
            self._history_suppressed_count = 0
            return

        if now_s - self._history_last_summary_ts >= self._history_still_failing_interval_s:
            logging.log(
                level,
                "HISTORY_STILL_FAILING failed=%d total=%d streak=%d suppressed=%d reason_top=%s(%d) last_reason=%s first_error_sample=%s symbols_sample=%s and_more=%d backoff_s=%d",
                failed,
                total,
                self._history_fail_streak,
                self._history_suppressed_count,
                top_reason,
                top_reason_count,
                self._history_last_err_kind,
                first_error_sample,
                symbols_sample,
                and_more,
                backoff_s,
            )
            self._history_last_summary_ts = now_s
            self._history_suppressed_count = 0
        else:
            self._history_suppressed_count += 1

    def _log_bootstrap_summary(self, summaries: List[Dict[str, Any]]) -> None:
        total = len(summaries)
        if total <= 0:
            return
        missing_last = [s["symbol"] for s in summaries if not s.get("m5_last_exists")]
        if missing_last:
            logging.warning(
                "Старт: останній 5m на диску %d/%d (немає: %s)",
                total - len(missing_last),
                total,
                ",".join(missing_last),
            )
        else:
            logging.info("Старт: останній 5m на диску %d/%d", total, total)

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
                now_s = time.time()
                if now_s < self._history_backoff_until_ts:
                    remaining_s = int(self._history_backoff_until_ts - now_s)
                    if now_s - self._history_last_circuit_log_ts >= self._history_circuit_log_interval_s:
                        logging.info(
                            "HISTORY_CIRCUIT_SLEEP seconds=%d",
                            remaining_s,
                        )
                        self._history_last_circuit_log_ts = now_s
                    time.sleep(min(60, max(1, remaining_s)))
                    continue

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
