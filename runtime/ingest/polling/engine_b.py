from __future__ import annotations

import datetime as dt
import logging
import time
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING

from core.model.bars import CandleBar, FINAL_SOURCES, ms_to_utc_dt
from runtime.ingest.market_calendar import MarketCalendar
from runtime.ingest.polling.dedup import has_on_disk, mark_on_disk
from runtime.ingest.polling.fetch_policy import (
    expected_last_closed_m5_open_ms,
    last_trading_minute_open_ms,
)
from core.buckets import bucket_start_ms
from runtime.store.uds import build_uds_from_config

if TYPE_CHECKING:
    from runtime.ingest.broker.fxcm.provider import FxcmHistoryProvider

# Утиліти часу/бакетів

def utc_now_ms() -> int:
    return int(time.time() * 1000)


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
            b0 = bucket_start_ms(anchor_open_ms, tf_s * 1000, off * 1000)
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
        b0 = bucket_start_ms(anchor_open_ms, tf_s * 1000, off * 1000)
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
        safety_delay_s: int,
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
        self._safety_delay_s = safety_delay_s
        self._broker_base_tfs_s = [int(x) for x in broker_base_tfs_s]
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

        boot_id = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        self._boot_id = boot_id
        self._uds = build_uds_from_config(
            self._config_path,
            data_root,
            boot_id,
            role="writer",
            writer_components=True,
        )

        self._last_saved_base: Dict[int, int] = {}  # tf_s -> open_time_ms
        self._poll_counter: int = 0
        self._day_index_cache: Dict[str, set[int]] = {}
        self._group_logs: bool = False
        self._recent_history_errors: List[Tuple[str, str, int]] = []
        self._warn_throttle: Dict[str, Tuple[float, int, str]] = {}
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
        bootstrap_degraded: list = []

        # Phase 1: завантаження watermark/last_saved з диску
        try:
            summary.update(self._bootstrap_from_disk(log_detail=log_detail))
        except Exception as exc:
            logging.warning(
                "BOOTSTRAP_DEGRADED phase=disk_bootstrap symbol=%s err=%s",
                self._symbol, exc,
            )
            bootstrap_degraded.append("disk_bootstrap: %s" % exc)

        # Phase 2: Redis priming з диску
        try:
            prime_summary = self._prime_redis_from_disk(log_detail=log_detail)
            summary.update(prime_summary)
        except Exception as exc:
            logging.warning(
                "BOOTSTRAP_DEGRADED phase=redis_priming symbol=%s err=%s",
                self._symbol, exc,
            )
            bootstrap_degraded.append("redis_priming: %s" % exc)

        # Phase 3: prime_ready сигнал для supervisor gate
        try:
            if self._prime_ready_mode == "auto":
                payload = _prime_ready_payload(
                    boot_id=self._boot_id,
                    symbols=[self._symbol],
                    summaries=[summary],
                    tfs=self.redis_priming_tfs(),
                )
                self.set_prime_ready(payload, PRIME_READY_TTL_S)
        except Exception as exc:
            logging.warning(
                "BOOTSTRAP_DEGRADED phase=prime_ready symbol=%s err=%s",
                self._symbol, exc,
            )
            bootstrap_degraded.append("prime_ready: %s" % exc)

        # Phase 4: D1 cold-start з брокера
        try:
            summary.update(self._cold_start_base_from_broker(log_detail=log_detail))
        except Exception as exc:
            logging.warning(
                "BOOTSTRAP_DEGRADED phase=broker_cold_start symbol=%s err=%s",
                self._symbol, exc,
            )
            bootstrap_degraded.append("broker_cold_start: %s" % exc)

        summary["bootstrap_degraded"] = bootstrap_degraded
        if bootstrap_degraded:
            logging.warning(
                "BOOTSTRAP_FINISHED_DEGRADED symbol=%s degraded=%s",
                self._symbol, bootstrap_degraded,
            )
        else:
            logging.info(
                "D1-only fetcher bootstrap завершено (symbol=%s)",
                self._symbol,
            )
        return summary

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
        result = self._uds.commit_final_bar(bar)
        if not result.ok:
            logging.debug(
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
        for tf_s in self._broker_base_tfs_s:
            last_b = self._load_last_open_ms_from_disk(self._symbol, tf_s)
            if last_b is not None:
                self._last_saved_base[tf_s] = last_b

        return {"d1_base_loaded": bool(self._last_saved_base)}

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

        for tf_s, n_full in sorted(self._broker_base_cold_start_counts.items()):
            if self._broker_base_tfs_s and tf_s not in self._broker_base_tfs_s:
                continue
            # Catch-up: якщо дані є — обчислюємо мінімальний N для gap
            last_known = self._last_saved_base.get(tf_s)
            if last_known is not None:
                now_ms = int(date_to.timestamp() * 1000)
                gap_bars = max(2, (now_ms - last_known) // (tf_s * 1000) + 2)
                n = min(n_full, gap_bars)
                if log_detail:
                    logging.debug(
                        "Cold-start base: TF=%ds catch-up gap_bars=%d n=%d",
                        tf_s, gap_bars, n,
                    )
            else:
                n = n_full
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

    def _loop(self) -> None:
        logging.info(
            "Polling loop: D1-only режим (M5+ derive через m1_poller/DeriveEngine)."
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

    def _last_trading_minute_open_ms(self, now_ms: int) -> int:
        return last_trading_minute_open_ms(
            self._provider,
            self._symbol,
            self._calendar,
            now_ms,
        )

    def _fetch_base_from_broker_on_close(self, anchor_open_ms: int) -> None:
        written = 0
        tried = 0
        # anchor_open_ms вже = _last_trading_minute_open_ms(now) з poll_iteration;
        # повторне застосування зсувало на 1 хвилину назад і ламало детекцію.
        last_trading_open = anchor_open_ms
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
            b0 = bucket_start_ms(last_trading_open, tf_s * 1000, anchor * 1000)
            b1 = b0 + tf_ms
            # b1 = кінець бакета; _last_trading_minute_open_ms(b1) повертає
            # останню торгову хвилину ДО b1 (тобто всередині бакета).
            expected_last = self._last_trading_minute_open_ms(b1)
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

    # --- M5 polling/derive/backfill/recover видалено (ADR-0002 Phase 5+cleanup) ---
    # Весь M5 pipeline тепер через m1_poller + DeriveEngine.
    # Видалений код: _ingest_m5_bars, _try_derive_from_m5, _try_derive_from_m5_buffer,
    #   _derived_tail_state_path, _load_derived_tail_state, _store_derived_tail_state,
    #   _m5_tail_missing_count, _m5_tail_missing_samples, _live_recover_check,
    #   _live_recover_finish, _progressive_backfill_m5, _backfill_missing_m5_from_broker


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
            logging.debug(
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
        logging.debug(
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
