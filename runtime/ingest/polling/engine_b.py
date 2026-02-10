from __future__ import annotations

import datetime as dt
import json
import logging
import os
import time
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING

from core.model.bars import CandleBar, ms_to_utc_dt
from runtime.ingest.market_calendar import MarketCalendar
from runtime.ingest.polling.dedup import has_on_disk, mark_on_disk
from runtime.ingest.polling.derive import M5Buffer, derive_from_m5_for_anchor
from runtime.ingest.polling.fetch_policy import (
    expected_last_closed_m5_open_ms,
    expected_last_closed_m5_open_ms_calendar,
    last_trading_minute_open_ms,
)
from runtime.ingest.polling.flat_filter import is_flat_bar
from runtime.ingest.polling.time_buckets import floor_bucket_start_ms
from runtime.store.uds import build_uds_from_config

if TYPE_CHECKING:
    from runtime.ingest.broker.fxcm.provider import FxcmHistoryProvider

# Утиліти часу/бакетів

def utc_now_ms() -> int:
    return int(time.time() * 1000)


FINAL_SOURCES = {"history", "derived", "history_agg"}
PRIME_READY_TTL_S = 21600


def _is_final_bar(bar: CandleBar) -> bool:
    if bar.complete is not True:
        return False
    return bar.src in FINAL_SOURCES


def _prime_ready_is_ok(summary: Dict[str, Any]) -> bool:
    if not bool(summary.get("cache_prime_enabled", False)):
        return False
    if bool(summary.get("cache_prime_partial", False)):
        return False
    if summary.get("cache_prime_error") is not None:
        return False
    counts = summary.get("cache_prime_counts")
    return isinstance(counts, dict) and bool(counts)


def _prime_ready_payload(
    *,
    boot_id: str,
    symbols: List[str],
    summaries: List[Dict[str, Any]],
    tfs: List[int],
) -> Dict[str, Any]:
    tail_len_by_tf_s: Dict[int, int] = {}
    tail_len_by_symbol: Dict[str, Dict[int, int]] = {}
    for summary in summaries:
        counts = summary.get("cache_prime_counts")
        if not isinstance(counts, dict):
            continue
        for key, val in counts.items():
            if not isinstance(key, str):
                continue
            try:
                count = int(val)
            except Exception:
                continue
            if ":" not in key:
                continue
            symbol, tf_raw = key.split(":", 1)
            try:
                tf_s = int(tf_raw)
            except Exception:
                continue
            per_symbol = tail_len_by_symbol.setdefault(symbol, {})
            per_symbol[tf_s] = count
            prev = tail_len_by_tf_s.get(tf_s)
            if prev is None or count < prev:
                tail_len_by_tf_s[tf_s] = count
    ready_symbols = [s.get("symbol") for s in summaries if _prime_ready_is_ok(s)]
    ready_symbols = [str(x) for x in ready_symbols if isinstance(x, str) and x]
    partial_symbols = [s.get("symbol") for s in summaries if s.get("cache_prime_partial")]
    partial_symbols = [str(x) for x in partial_symbols if isinstance(x, str) and x]
    empty_symbols = [s.get("symbol") for s in summaries if s.get("cache_prime_empty")]
    empty_symbols = [str(x) for x in empty_symbols if isinstance(x, str) and x]
    ready = len(ready_symbols) == len(symbols) and len(symbols) > 0
    return {
        "v": 1,
        "ready": ready,
        "boot_id": boot_id,
        "ts_ms": utc_now_ms(),
        "symbols": list(symbols),
        "symbols_ready": ready_symbols,
        "cache_prime_partial": partial_symbols,
        "cache_prime_empty": empty_symbols,
        "tfs": list(tfs),
        "prime_tail_len_by_tf_s": tail_len_by_tf_s,
        "prime_tail_len_by_symbol": tail_len_by_symbol,
    }


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


def sleep_to_next_minute(safety_delay_s: int) -> None:
    now = time.time()
    next_min = (int(now // 60) + 1) * 60
    target = next_min + safety_delay_s
    delay = max(0.0, target - now)
    time.sleep(delay)


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
        m5_tail_catchup_max_missing_bars: int,
        m5_tail_catchup_max_lookback_bars: int,
        derived_tail_rebuild_enabled: bool,
        derived_tail_rebuild_m5_bars: int,
        derived_tail_rebuild_budget_s: float,
        m5_backfill_step_bars: int,
        m5_backfill_every_min: int,
        m5_backfill_max_bars: int,
        flat_bar_max_volume: int,
        derived_tfs_s: List[int],
        broker_base_tfs_s: List[int],
        broker_base_fetch_on_close: bool,
        broker_base_max_tf_per_poll: int,
        broker_base_cold_start_counts: Dict[int, int],
        broker_base_cold_start_enabled: bool,
        redis_priming_enabled: bool,
        redis_priming_budget_s: float,
        redis_priming_tfs_s: List[int],
        redis_priming_symbols: List[str],
        redis_tail_n_by_tf_s: Dict[int, int],
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
        self._m5_tail_catchup_max_missing_bars = max(0, int(m5_tail_catchup_max_missing_bars))
        self._m5_tail_catchup_max_lookback_bars = max(0, int(m5_tail_catchup_max_lookback_bars))
        self._derived_tail_rebuild_enabled = bool(derived_tail_rebuild_enabled)
        self._derived_tail_rebuild_m5_bars = max(0, int(derived_tail_rebuild_m5_bars))
        self._derived_tail_rebuild_budget_s = max(0.1, float(derived_tail_rebuild_budget_s))
        self._m5_backfill_step_bars = max(0, int(m5_backfill_step_bars))
        self._m5_backfill_every_s = max(0, int(m5_backfill_every_min)) * 60
        self._m5_backfill_max_bars = max(0, int(m5_backfill_max_bars))
        self._m5_backfill_total = 0
        self._m5_backfill_last_ts = 0.0
        self._m5_backfill_last_head_ms: Optional[int] = None
        self._m5_backfill_no_progress = 0
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
        self._redis_priming_enabled = bool(redis_priming_enabled)
        self._redis_priming_budget_s = float(redis_priming_budget_s)
        self._redis_priming_tfs_s = [int(x) for x in redis_priming_tfs_s if int(x) > 0]
        self._redis_priming_symbols = [str(x) for x in redis_priming_symbols if str(x).strip()]
        self._redis_tail_n_by_tf_s = {int(k): int(v) for k, v in redis_tail_n_by_tf_s.items()}
        self._day_anchor_offset_s = day_anchor_offset_s
        self._day_anchor_offset_s_d1 = day_anchor_offset_s_d1
        self._day_anchor_offset_s_d1_alt = day_anchor_offset_s_d1_alt
        self._day_anchor_offset_s_alt = day_anchor_offset_s_alt
        self._day_anchor_offset_s_alt2 = day_anchor_offset_s_alt2
        # CALENDAR_GATE: швидко вимкнути через config.
        self._calendar = market_calendar
        self._calendar_gate_enabled = bool(market_calendar.enabled)
        self._heavy_skipped = 0

        boot_id = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        self._boot_id = boot_id
        self._uds = build_uds_from_config(
            self._config_path,
            data_root,
            boot_id,
            role="writer",
            writer_components=True,
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
        self._warmup_pending: bool = False
        self._rebuild_pending: bool = False
        self._prime_ready_mode = "auto"

    def enable_group_logging(self) -> None:
        self._group_logs = True

    def set_prime_ready_mode(self, mode: str) -> None:
        self._prime_ready_mode = str(mode)

    def get_boot_id(self) -> str:
        return self._boot_id

    def redis_priming_tfs(self) -> list[int]:
        tfs = list(self._redis_priming_tfs_s)
        if not tfs:
            tfs = sorted(k for k, v in self._redis_tail_n_by_tf_s.items() if int(v) > 0)
        return tfs

    def set_prime_ready(self, payload: dict[str, Any], ttl_s: Optional[int]) -> None:
        self._uds.set_prime_ready(payload, ttl_s)

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
        prime_summary = self._prime_redis_from_disk(log_detail=log_detail)
        summary.update(prime_summary)
        if self._prime_ready_mode == "auto":
            payload = _prime_ready_payload(
                boot_id=self._boot_id,
                symbols=[self._symbol],
                summaries=[summary],
                tfs=self.redis_priming_tfs(),
            )
            self.set_prime_ready(payload, PRIME_READY_TTL_S)
        summary.update(self._tail_catchup_from_broker(log_detail=log_detail))
        summary.update(self._cold_start_base_from_broker(log_detail=log_detail))
        summary.update(self._schedule_post_start_phases())
        return summary

    def _schedule_post_start_phases(self) -> Dict[str, Any]:
        self._warmup_pending = bool(self._warmup_bars > 0)
        self._rebuild_pending = bool(self._derived_tail_rebuild_enabled)
        return {
            "warmup_deferred": self._warmup_pending,
            "derived_rebuild_deferred": self._rebuild_pending,
        }

    def _run_warmup_phase(self) -> None:
        if not self._warmup_pending:
            return
        logging.info("WARMUP_PHASE_START symbol=%s", self._symbol)
        self._warmup_pending = False
        result = self._warmup_m5_tail(log_detail=False)
        logging.info("WARMUP_PHASE_DONE symbol=%s result=%s", self._symbol, result)

    def _run_rebuild_phase(self) -> None:
        if self._warmup_pending:
            return
        if not self._rebuild_pending:
            return
        logging.info("REBUILD_PHASE_START symbol=%s", self._symbol)
        self._rebuild_pending = False
        result = self._rebuild_derived_from_disk_tail(log_detail=False)
        logging.info("REBUILD_PHASE_DONE symbol=%s result=%s", self._symbol, result)

    def _tail_catchup_from_broker(self, log_detail: bool = False) -> Dict[str, Any]:
        last_saved = self._last_saved_m5_open_ms
        if last_saved is None:
            return {"tail_catchup_skipped": "no_disk"}

        tf_ms = 300_000
        now_ms = utc_now_ms()
        cutoff_open_ms = expected_last_closed_m5_open_ms_calendar(
            self._provider,
            self._symbol,
            self._calendar,
            now_ms,
        )
        if cutoff_open_ms <= 0:
            return {"tail_catchup_missing": 0}
        if cutoff_open_ms <= last_saved:
            return {"tail_catchup_missing": 0}

        missing = int((cutoff_open_ms - last_saved) // tf_ms)
        if missing <= 0:
            return {"tail_catchup_missing": 0}

        max_missing = self._m5_tail_catchup_max_missing_bars
        max_lookback = self._m5_tail_catchup_max_lookback_bars
        n = missing
        if max_lookback > 0:
            n = min(n, max_lookback)
        if max_missing > 0:
            n = min(n, max_missing)
        if n <= 0:
            return {"tail_catchup_missing": missing, "tail_catchup_fetched": 0}

        if missing > n:
            backlog = missing - n
            logging.warning(
                "TAIL_CATCHUP_TRUNCATED symbol=%s missing_total=%d fetched=%d backlog=%d",
                self._symbol,
                missing,
                n,
                backlog,
            )
            gap_from_ms = last_saved + tf_ms
            self._uds.set_gap_state(
                backlog_bars=backlog,
                gap_from_ms=gap_from_ms,
                gap_to_ms=cutoff_open_ms,
                policy="manual_tool_required",
            )
        else:
            self._uds.set_gap_state(
                backlog_bars=0,
                gap_from_ms=None,
                gap_to_ms=None,
                policy=None,
            )

        if log_detail:
            logging.debug(
                "Tail catch-up: missing=%d fetch_n=%d last_saved=%s cutoff=%s",
                missing,
                n,
                ms_to_utc_dt(last_saved).isoformat(),
                ms_to_utc_dt(cutoff_open_ms).isoformat(),
            )

        date_to = ms_to_utc_dt(cutoff_open_ms + tf_ms)
        bars = self._provider.fetch_last_n_tf(
            self._symbol, tf_s=300, n=n, date_to_utc=date_to
        )
        self._capture_history_error()
        if not bars:
            return {"tail_catchup_missing": missing, "tail_catchup_fetched": 0}

        bars = [b for b in bars if b.open_time_ms > last_saved and b.open_time_ms <= cutoff_open_ms]
        if not bars:
            return {"tail_catchup_missing": missing, "tail_catchup_fetched": 0}

        written = self._ingest_m5_bars(
            bars,
            allow_older=True,
            write_missing_older=True,
            log_summary=log_detail,
            context="tail_catchup_m5",
        )
        return {
            "tail_catchup_missing": missing,
            "tail_catchup_fetched": len(bars),
            "tail_catchup_written": written,
        }

    def _rebuild_derived_from_disk_tail(self, log_detail: bool = False) -> Dict[str, Any]:
        if not self._derived_from_m5_tfs:
            return {"derived_tail_rebuild_skipped": "no_tfs"}
        if not self._derived_tail_rebuild_enabled:
            return {"derived_tail_rebuild_skipped": "disabled"}
        if self._derived_tail_rebuild_m5_bars <= 0:
            return {"derived_tail_rebuild_skipped": "no_m5_tail"}
        if self._last_saved_m5_open_ms is not None:
            ok_state = self._load_derived_tail_state().get(self._symbol, {})
            ok_ms = ok_state.get("m5_tail_ok_end_ms")
            if ok_ms is None:
                ok_ms = ok_state.get("derived_tail_ok_m5_open_ms")
            ok_window = ok_state.get("m5_tail_ok_window_bars")
            if (
                isinstance(ok_ms, int)
                and isinstance(ok_window, int)
                and ok_window == self._derived_tail_rebuild_m5_bars
                and ok_ms >= self._last_saved_m5_open_ms
            ):
                logging.debug(
                    "DERIVED_TAIL_SKIP_OK symbol=%s ok_end=%s window_bars=%d",
                    self._symbol,
                    ms_to_utc_dt(ok_ms).isoformat(),
                    ok_window,
                )
                return {"derived_tail_rebuild_skipped": "ok_state"}

        bars = self._uds.read_tail_candles(
            self._symbol,
            300,
            self._derived_tail_rebuild_m5_bars,
        )
        if not bars:
            return {"derived_tail_rebuild_empty": True}

        start = time.time()
        if self._last_saved_m5_open_ms is None:
            return {"derived_tail_rebuild_empty": True}
        tail_end_ms = self._last_saved_m5_open_ms
        tail_start_ms = tail_end_ms - (self._derived_tail_rebuild_m5_bars - 1) * 300_000
        bar_set = {b.open_time_ms for b in bars}
        missing_m5_buckets = self._m5_tail_missing_count(tail_start_ms, tail_end_ms, bar_set)
        missing_sample: list[int] = []
        if missing_m5_buckets > 0:
            missing_sample = self._m5_tail_missing_samples(
                tail_start_ms,
                tail_end_ms,
                bar_set,
                limit=20,
            )
        logging.debug(
            "DERIVED_TAIL_M5_CHECK symbol=%s start=%s end=%s missing=%d",
            self._symbol,
            ms_to_utc_dt(tail_start_ms).isoformat(),
            ms_to_utc_dt(tail_end_ms).isoformat(),
            missing_m5_buckets,
        )
        if missing_sample:
            logging.debug(
                "DERIVED_TAIL_M5_MISSING_SAMPLE symbol=%s sample=%s",
                self._symbol,
                ",".join(ms_to_utc_dt(x).isoformat() for x in missing_sample),
            )

        if missing_sample:
            backfill = self._backfill_missing_m5_from_broker(
                missing_sample,
                log_detail=log_detail,
            )
            if int(backfill.get("m5_gap_backfill_written", 0)) > 0:
                bars = self._uds.read_tail_candles(
                    self._symbol,
                    300,
                    self._derived_tail_rebuild_m5_bars,
                )
                bar_set = {b.open_time_ms for b in bars}
                missing_m5_buckets = self._m5_tail_missing_count(
                    tail_start_ms,
                    tail_end_ms,
                    bar_set,
                )
                missing_sample = self._m5_tail_missing_samples(
                    tail_start_ms,
                    tail_end_ms,
                    bar_set,
                    limit=20,
                )
        m5_buf = M5Buffer(max_keep=max(2000, self._derived_tail_rebuild_m5_bars + 5))
        written = 0
        scanned = 0
        partial = False
        missing_m5_ref = [0]
        for b in bars:
            if time.time() - start >= self._derived_tail_rebuild_budget_s:
                partial = True
                break
            m5_buf.upsert(b)
            scanned += 1
            written += self._try_derive_from_m5_buffer(
                m5_buf,
                anchor_open_ms=b.open_time_ms,
                tail_start_ms=tail_start_ms,
                tail_end_ms=tail_end_ms,
                missing_m5_ref=missing_m5_ref,
            )
        derived_missing = int(missing_m5_ref[0])
        logging.debug(
            "DERIVED_TAIL_DERIVED_CHECK symbol=%s missing=%d",
            self._symbol,
            derived_missing,
        )
        derived_coverage: Dict[str, Optional[int]] = {}
        for tf_s in self._derived_from_m5_tfs:
            derived_coverage[str(tf_s)] = self._uds.head_first_open_ms(
                self._symbol,
                tf_s,
            )
        derived_gaps_detected = missing_m5_buckets > 0 or derived_missing > 0

        if partial:
            logging.warning(
                "DERIVED_TAIL_PARTIAL symbol=%s scanned=%d written=%d budget_s=%.2f",
                self._symbol,
                scanned,
                written,
                self._derived_tail_rebuild_budget_s,
            )
        elif log_detail:
            logging.debug(
                "DERIVED_TAIL_OK symbol=%s scanned=%d written=%d missing_m5=%d missing_derived=%d budget_s=%.2f",
                self._symbol,
                scanned,
                written,
                missing_m5_buckets,
                derived_missing,
                self._derived_tail_rebuild_budget_s,
            )

        if missing_m5_buckets > 0:
            logging.warning(
                "DERIVED_TAIL_M5_GAPS symbol=%s missing=%d tail_start=%s tail_end=%s",
                self._symbol,
                missing_m5_buckets,
                ms_to_utc_dt(tail_start_ms).isoformat(),
                ms_to_utc_dt(tail_end_ms).isoformat(),
            )
            if missing_sample:
                logging.warning(
                    "DERIVED_TAIL_M5_GAPS_SAMPLE symbol=%s sample=%s",
                    self._symbol,
                    ",".join(ms_to_utc_dt(x).isoformat() for x in missing_sample),
                )

            self._store_derived_tail_state(
                ok_m5_open_ms=None,
                missing_count=missing_m5_buckets,
                missing_samples=missing_sample,
                tail_end_ms=tail_end_ms,
                derived_coverage_from_ms=derived_coverage,
                derived_gaps_detected=derived_gaps_detected,
            )

        if not partial and missing_m5_buckets == 0:
            self._store_derived_tail_state(
                ok_m5_open_ms=tail_end_ms,
                missing_count=0,
                missing_samples=[],
                tail_end_ms=tail_end_ms,
                derived_coverage_from_ms=derived_coverage,
                derived_gaps_detected=derived_gaps_detected,
            )

        return {
            "derived_tail_rebuild_scanned": scanned,
            "derived_tail_rebuild_written": written,
            "derived_tail_rebuild_partial": partial,
            "derived_tail_rebuild_missing_m5": missing_m5_buckets,
            "derived_tail_rebuild_missing_derived": derived_missing,
        }

    def _prime_redis_from_disk(self, log_detail: bool = False) -> Dict[str, Any]:
        if not self._redis_priming_enabled:
            return {"cache_prime_enabled": False}
        if self._redis_priming_symbols and self._symbol not in self._redis_priming_symbols:
            return {"cache_prime_enabled": True, "cache_prime_skipped": True}
        if not self._uds.has_redis_writer():
            logging.warning("CACHE_PRIME_SKIP symbol=%s reason=redis_disabled", self._symbol)
            return {"cache_prime_enabled": True, "cache_prime_error": "redis_disabled"}

        start = time.time()
        budget_s = max(0.1, float(self._redis_priming_budget_s))
        tfs = list(self._redis_priming_tfs_s)
        if not tfs:
            tfs = sorted(k for k, v in self._redis_tail_n_by_tf_s.items() if int(v) > 0)

        if log_detail:
            logging.info(
                "CACHE_PRIME_START symbol=%s tfs=%s budget_s=%.2f",
                self._symbol,
                ",".join(str(x) for x in tfs),
                budget_s,
            )

        primed_counts: Dict[str, int] = {}
        degraded: list[str] = []
        errors: list[str] = []
        partial = False

        for tf_s in tfs:
            if time.time() - start >= budget_s:
                partial = True
                break
            tail_n = int(self._redis_tail_n_by_tf_s.get(tf_s, 0))
            if tail_n <= 0:
                continue
            count = self._uds.bootstrap_prime_from_disk(
                self._symbol,
                tf_s,
                tail_n,
                log_detail=log_detail,
            )
            if count > 0:
                primed_counts[f"{self._symbol}:{tf_s}"] = count

        if partial:
            degraded.append("cache_prime_partial")
        if not primed_counts:
            degraded.append("cache_prime_empty")

        priming_ts_ms = int(time.time() * 1000)
        self._uds.set_cache_state(
            primed=bool(primed_counts) and not partial,
            prime_partial=partial,
            priming_ts_ms=priming_ts_ms,
            primed_counts=primed_counts,
            degraded=degraded,
            errors=errors,
        )

        if partial:
            logging.warning(
                "CACHE_PRIME_PARTIAL symbol=%s done=%d budget_s=%.2f",
                self._symbol,
                len(primed_counts),
                budget_s,
            )
        else:
            logging.info(
                "CACHE_PRIME_OK symbol=%s done=%d budget_s=%.2f",
                self._symbol,
                len(primed_counts),
                budget_s,
            )

        return {
            "cache_prime_enabled": True,
            "cache_prime_partial": partial,
            "cache_prime_counts": primed_counts,
        }

    def poll_iteration(self) -> None:
        if self._broker_base_fetch_on_close:
            anchor_open = self._last_trading_minute_open_ms(utc_now_ms())
            self._fetch_base_from_broker_on_close(anchor_open)
        self._poll_m5_once()
        self._progressive_backfill_m5()
        self._run_warmup_phase()
        self._run_rebuild_phase()
        self._poll_counter += 1

    def close(self) -> None:
        self._uds.close()

    def _append_bar(self, bar: CandleBar) -> None:
        if not _is_final_bar(bar):
            self._uds.publish_preview_bar(bar)
            return
        wm_open_ms = self._uds.get_watermark_open_ms(bar.symbol, bar.tf_s)
        if wm_open_ms is not None and bar.open_time_ms <= wm_open_ms:
            # Зменшу шум: троттлюю повідомлення та зберігаю перше/останнє значення в одному повідомленні
            key = f"backfill_quarantine:{bar.tf_s}"
            iso = ms_to_utc_dt(bar.open_time_ms).isoformat()
            _, _, last_msg = self._warn_throttle.get(key, (0.0, 0, ""))
            if last_msg and "|" in last_msg:
                first_iso = last_msg.split("|", 1)[0]
            else:
                first_iso = iso
            msg = f"BACKFILL_QUARANTINE symbol={self._symbol} tf_s={bar.tf_s} first={first_iso} last={iso}"
            self._warn_throttled(key, msg)
            # повідомлення тротлиться всередині _warn_throttled, тому тут нічого не логимо
            return
            return
        result = self._uds.commit_final_bar(bar)
        if not result.ok:
            logging.warning(
                "UDS_COMMIT_DROP symbol=%s tf_s=%s open_ms=%s reason=%s",
                bar.symbol,
                bar.tf_s,
                bar.open_time_ms,
                result.reason,
            )

    def _load_last_open_ms_from_disk(self, symbol: str, tf_s: int) -> Optional[int]:
        bars = self._uds.read_tail_candles(symbol, tf_s, limit=1)
        if not bars:
            return None
        return int(bars[-1].open_time_ms)

    def _bootstrap_from_disk(self, log_detail: bool = False) -> Dict[str, Any]:
        last_m5 = self._load_last_open_ms_from_disk(self._symbol, 300)
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
            last_d = self._load_last_open_ms_from_disk(self._symbol, tf_s)
            if last_d is not None:
                self._last_saved_derived[tf_s] = last_d

        for tf_s in self._broker_base_tfs_s:
            last_b = self._load_last_open_ms_from_disk(self._symbol, tf_s)
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
                if has_on_disk(
                    self._day_index_cache,
                    self._uds,
                    self._symbol,
                    tf_s,
                    b.open_time_ms,
                ):
                    total_existing += 1
                    continue
                self._append_bar(b)
                mark_on_disk(
                    self._day_index_cache,
                    self._uds,
                    self._symbol,
                    tf_s,
                    b.open_time_ms,
                )
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

        now_ms = utc_now_ms()
        cutoff_open = expected_last_closed_m5_open_ms_calendar(
            self._provider,
            self._symbol,
            self._calendar,
            now_ms,
        )
        if cutoff_open <= 0:
            return {"warmup_empty": True, "warmup_written": 0}
        last_saved = self._last_saved_m5_open_ms
        if last_saved is not None:
            missing_bars = int((cutoff_open - last_saved) // 300_000)
            if missing_bars <= 0:
                return {"warmup_skipped": True}
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
            self._uds.close()

    def _sleep_to_next_minute(self) -> None:
        sleep_to_next_minute(self._safety_delay_s)

    def _seconds_to_next_minute(self) -> float:
        now = time.time()
        next_min = (int(now // 60) + 1) * 60 + self._safety_delay_s
        return max(0.0, next_min - now)

    def _last_trading_minute_open_ms(self, now_ms: int) -> int:
        return last_trading_minute_open_ms(
            self._provider,
            self._symbol,
            self._calendar,
            now_ms,
        )

    def _poll_m5_once(self) -> None:
        now_ms = utc_now_ms()
        tf_ms = 300_000
        cutoff_open_ms = expected_last_closed_m5_open_ms_calendar(
            self._provider,
            self._symbol,
            self._calendar,
            now_ms,
        )
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
            if has_on_disk(self._day_index_cache, self._uds, self._symbol, tf_s, b0):
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

            self._append_bar(b)
            mark_on_disk(self._day_index_cache, self._uds, self._symbol, tf_s, b0)
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
            if is_flat_bar(b, self._flat_bar_max_volume):
                skipped_flat += 1
                continue
            if self._last_saved_m5_open_ms is not None and b.open_time_ms <= self._last_saved_m5_open_ms:
                if allow_older:
                    self._m5.upsert(b)
                    if write_missing_older and not has_on_disk(
                        self._day_index_cache,
                        self._uds,
                        self._symbol,
                        300,
                        b.open_time_ms,
                    ):
                        self._append_bar(b)
                        mark_on_disk(
                            self._day_index_cache,
                            self._uds,
                            self._symbol,
                            300,
                            b.open_time_ms,
                        )
                        written += 1
                    continue
                skipped_dedup += 1
                continue

            self._append_bar(b)
            mark_on_disk(
                self._day_index_cache,
                self._uds,
                self._symbol,
                300,
                b.open_time_ms,
            )
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
                if has_on_disk(
                    self._day_index_cache,
                    self._uds,
                    self._symbol,
                    tf_s,
                    d.open_time_ms,
                ):
                    continue

            if not has_on_disk(
                self._day_index_cache,
                self._uds,
                self._symbol,
                tf_s,
                d.open_time_ms,
            ):
                self._append_bar(d)
                mark_on_disk(
                    self._day_index_cache,
                    self._uds,
                    self._symbol,
                    tf_s,
                    d.open_time_ms,
                )
                written += 1
            if last is None or d.open_time_ms > last:
                self._last_saved_derived[tf_s] = d.open_time_ms

        return written

    def _try_derive_from_m5_buffer(
        self,
        m5_buf: M5Buffer,
        anchor_open_ms: int,
        tail_start_ms: int,
        tail_end_ms: int,
        missing_m5_ref: list[Any],
    ) -> int:
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

            if b0 < tail_start_ms or b1 > tail_end_ms:
                continue

            if not m5_buf.has_range_complete(b0, b1):
                missing_m5_ref[0] = int(missing_m5_ref[0]) + 1
                continue

            d = derive_from_m5_for_anchor(
                self._symbol,
                tf_s=tf_s,
                m5=m5_buf,
                anchor_open_ms=anchor_open_ms,
                anchor_offset_s=anchor,
            )

            if d is None:
                continue

            last = self._last_saved_derived.get(tf_s)
            if last is not None and d.open_time_ms <= last:
                if has_on_disk(
                    self._day_index_cache,
                    self._uds,
                    self._symbol,
                    tf_s,
                    d.open_time_ms,
                ):
                    continue

            if not has_on_disk(
                self._day_index_cache,
                self._uds,
                self._symbol,
                tf_s,
                d.open_time_ms,
            ):
                self._append_bar(d)
                mark_on_disk(
                    self._day_index_cache,
                    self._uds,
                    self._symbol,
                    tf_s,
                    d.open_time_ms,
                )
                written += 1
            if last is None or d.open_time_ms > last:
                self._last_saved_derived[tf_s] = d.open_time_ms

        return written

    def _derived_tail_state_path(self) -> str:
        return os.path.join(self._data_root, "_derived_tail_state.json")

    def _load_derived_tail_state(self) -> Dict[str, Any]:
        path = self._derived_tail_state_path()
        if not os.path.isfile(path):
            return {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and isinstance(data.get("symbols"), dict):
                return data["symbols"]
        except Exception:
            return {}
        return {}

    def _store_derived_tail_state(
        self,
        *,
        ok_m5_open_ms: Optional[int],
        missing_count: int,
        missing_samples: list[int],
        tail_end_ms: int,
        derived_coverage_from_ms: Dict[str, Optional[int]],
        derived_gaps_detected: bool,
    ) -> None:
        path = self._derived_tail_state_path()
        data: Dict[str, Any] = {"symbols": {}}
        try:
            if os.path.isfile(path):
                with open(path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                if isinstance(raw, dict) and isinstance(raw.get("symbols"), dict):
                    data = raw
        except Exception:
            data = {"symbols": {}}

        symbols = data.get("symbols")
        if not isinstance(symbols, dict):
            symbols = {}
            data["symbols"] = symbols

        entry: Dict[str, Any] = {
            "m5_tail_ok_window_bars": int(self._derived_tail_rebuild_m5_bars),
            "m5_tail_missing_count": int(missing_count),
            "m5_tail_missing_samples": [int(x) for x in missing_samples],
            "m5_tail_missing_end_ms": int(tail_end_ms),
            "m5_tail_missing_window_bars": int(self._derived_tail_rebuild_m5_bars),
            "derived_coverage_from_ms": {
                str(k): int(v) for k, v in derived_coverage_from_ms.items() if v is not None
            },
            "derived_gaps_detected": bool(derived_gaps_detected),
            "ts_ms": int(time.time() * 1000),
        }
        if ok_m5_open_ms is not None:
            entry["m5_tail_ok_end_ms"] = int(ok_m5_open_ms)

        symbols[self._symbol] = entry
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
        except Exception:
            pass

    def _m5_tail_missing_count(
        self,
        tail_start_ms: int,
        tail_end_ms: int,
        bar_set: set[int],
    ) -> int:
        missing = 0
        step = 300_000
        for open_ms in range(tail_start_ms, tail_end_ms + 1, step):
            if not self._calendar.is_trading_minute(open_ms):
                continue
            if open_ms not in bar_set:
                missing += 1
        return missing

    def _m5_tail_missing_samples(
        self,
        tail_start_ms: int,
        tail_end_ms: int,
        bar_set: set[int],
        limit: int,
    ) -> list[int]:
        out: list[int] = []
        step = 300_000
        for open_ms in range(tail_start_ms, tail_end_ms + 1, step):
            if not self._calendar.is_trading_minute(open_ms):
                continue
            if open_ms not in bar_set:
                out.append(open_ms)
                if len(out) >= limit:
                    break
        return out

    def _progressive_backfill_m5(self) -> None:
        if self._m5_backfill_step_bars <= 0:
            return
        if self._m5_backfill_every_s <= 0:
            return
        if self._m5_backfill_max_bars <= 0:
            return
        if self._m5_backfill_total >= self._m5_backfill_max_bars:
            return

        now_s = time.time()
        if now_s - self._m5_backfill_last_ts < self._m5_backfill_every_s:
            return
        self._m5_backfill_last_ts = now_s

        head_ms = self._uds.head_first_open_ms(self._symbol, 300)
        if head_ms is None:
            return
        if self._m5_backfill_last_head_ms is not None and head_ms >= self._m5_backfill_last_head_ms:
            if self._m5_backfill_no_progress >= 2:
                logging.warning(
                    "M5_BACKFILL_STOP symbol=%s reason=no_progress",
                    self._symbol,
                )
                self._m5_backfill_total = self._m5_backfill_max_bars
                return
            self._m5_backfill_no_progress += 1
        else:
            self._m5_backfill_no_progress = 0
        self._m5_backfill_last_head_ms = head_ms

        remaining = self._m5_backfill_max_bars - self._m5_backfill_total
        step_n = min(self._m5_backfill_step_bars, remaining)
        if step_n <= 0:
            return

        date_to = ms_to_utc_dt(head_ms)
        bars = self._provider.fetch_last_n_tf(
            self._symbol,
            tf_s=300,
            n=step_n,
            date_to_utc=date_to,
        )
        self._capture_history_error()
        if not bars:
            return

        older = [b for b in bars if b.open_time_ms < head_ms]
        if not older:
            return

        written = self._ingest_m5_bars(
            older,
            allow_older=True,
            write_missing_older=True,
            log_summary=True,
            context="backfill_m5",
        )
        if written <= 0:
            self._m5_backfill_no_progress += 1
            return

        self._m5_backfill_total += written
        logging.info(
            "M5_BACKFILL_STEP symbol=%s written=%d total=%d remaining=%d head=%s",
            self._symbol,
            written,
            self._m5_backfill_total,
            self._m5_backfill_max_bars - self._m5_backfill_total,
            ms_to_utc_dt(head_ms).isoformat(),
        )

    def _backfill_missing_m5_from_broker(
        self,
        missing_samples: list[int],
        log_detail: bool = False,
    ) -> Dict[str, Any]:
        if not missing_samples:
            return {"m5_gap_backfill_attempted": 0, "m5_gap_backfill_written": 0}

        fetched: list[CandleBar] = []
        attempted = 0
        for open_ms in missing_samples:
            attempted += 1
            date_to = ms_to_utc_dt(open_ms + 300_000)
            bars = self._provider.fetch_last_n_tf(
                self._symbol,
                tf_s=300,
                n=2,
                date_to_utc=date_to,
            )
            self._capture_history_error()
            if not bars:
                continue
            for b in bars:
                if b.open_time_ms != open_ms:
                    continue
                if has_on_disk(
                    self._day_index_cache,
                    self._uds,
                    self._symbol,
                    300,
                    b.open_time_ms,
                ):
                    break
                fetched.append(b)
                break

        if not fetched:
            return {"m5_gap_backfill_attempted": attempted, "m5_gap_backfill_written": 0}

        fetched.sort(key=lambda b: b.open_time_ms)
        written = self._ingest_m5_bars(
            fetched,
            allow_older=True,
            write_missing_older=True,
            log_summary=log_detail,
            context="gap_backfill_m5",
        )
        if written > 0:
            logging.warning(
                "M5_GAP_BACKFILL_OK symbol=%s written=%d attempted=%d",
                self._symbol,
                written,
                attempted,
            )
        elif log_detail:
            logging.debug(
                "M5_GAP_BACKFILL_EMPTY symbol=%s attempted=%d",
                self._symbol,
                attempted,
            )
        return {"m5_gap_backfill_attempted": attempted, "m5_gap_backfill_written": written}



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
        group_logs_enabled: bool = True,
    ) -> None:
        self._engines = engines
        self._safety_delay_s = max((e._safety_delay_s for e in engines), default=0)  # noqa: SLF001
        self._group_logs_enabled = bool(group_logs_enabled)
        if self._group_logs_enabled:
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

        warmup_deferred = [s["symbol"] for s in summaries if s.get("warmup_deferred")]
        if warmup_deferred:
            logging.info(
                "Warmup: deferred %d/%d (%s)",
                len(warmup_deferred),
                total,
                ",".join(warmup_deferred),
            )

    def _set_prime_ready_global(self, summaries: List[Dict[str, Any]]) -> None:
        if not self._engines:
            return
        engine0 = self._engines[0]
        symbols = [e._symbol for e in self._engines]  # noqa: SLF001
        payload = _prime_ready_payload(
            boot_id=engine0.get_boot_id(),
            symbols=symbols,
            summaries=summaries,
            tfs=engine0.redis_priming_tfs(),
        )
        engine0.set_prime_ready(payload, PRIME_READY_TTL_S)
        logging.info(
            "PRIME_READY_SET ready=%s symbols=%s",
            payload.get("ready"),
            ",".join(symbols),
        )

    def run_forever(self) -> None:
        symbols = [e._symbol for e in self._engines]  # noqa: SLF001
        logging.info("Polling loop: multi активний symbols=%s", ",".join(symbols))
        try:
            for e in self._engines:
                e.set_prime_ready_mode("defer")
            summaries: List[Dict[str, Any]] = []
            for e in self._engines:
                summaries.append(e.bootstrap_and_warmup(log_detail=False))
            self._log_bootstrap_summary(summaries)
            self._set_prime_ready_global(summaries)
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
