from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Any, Optional

from core.model.bars import CandleBar, FINAL_SOURCES
from core.config_loader import (
    tf_allowlist_from_cfg, preview_tf_allowlist_from_cfg, min_coldload_bars_from_cfg,
    DEFAULT_PREVIEW_TF_ALLOWLIST, MAX_EVENTS_PER_RESPONSE,
)

from runtime.obs_60s import Obs60s
from runtime.store.layers.disk_layer import DiskLayer
from runtime.store.layers.ram_layer import RamLayer
from runtime.store.layers.redis_layer import RedisLayer
from runtime.store.redis_snapshot import RedisSnapshotWriter, build_redis_snapshot_writer
from runtime.store.redis_spec import resolve_redis_spec
from runtime.store.ssot_jsonl import JsonlAppender

Logging = logging.getLogger("uds")

_OBS = Obs60s("uds")

try:
    from prometheus_client import Counter, Gauge  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    Counter = None  # type: ignore
    Gauge = None  # type: ignore


class _NoopCounter:
    def inc(self, _value: float = 1.0) -> None:
        return


class _NoopGauge:
    def set(self, _value: float) -> None:
        return


if Counter is not None:
    _METRIC_REDIS_WRITE_FAIL_TOTAL = Counter(
        "ai_one_uds_redis_write_fail_total",
        "UDS Redis snapshot write failures",
    )
    _METRIC_PUBSUB_FAIL_TOTAL = Counter(
        "ai_one_uds_pubsub_fail_total",
        "UDS pubsub publish failures",
    )
else:  # pragma: no cover - fallback when prometheus missing
    _METRIC_REDIS_WRITE_FAIL_TOTAL = _NoopCounter()
    _METRIC_PUBSUB_FAIL_TOTAL = _NoopCounter()

if Gauge is not None:
    _METRIC_SPLIT_BRAIN_ACTIVE = Gauge(
        "ai_one_uds_split_brain_active",
        "UDS split brain marker between disk and redis/pubsub",
    )
else:  # pragma: no cover - fallback when prometheus missing
    _METRIC_SPLIT_BRAIN_ACTIVE = _NoopGauge()

# Throttle логів "виправлено геометрію" — один підсумок / 30 с (антиспам при warmup)
_geom_fix_count = 0
_geom_fix_last_log_ts = 0.0
_geom_fix_last_example: Optional[tuple] = None  # (source, tf_s, sorted, dedup_dropped)

_REDIS_SPEC_BOOT_LOGGED = False

try:
    import redis as redis_lib  # type: ignore
    Logging.debug("UDS: redis бібліотека завантажена")
except Exception:
    redis_lib = None  # type: ignore
    Logging.warning("UDS: redis бібліотека недоступна, RedisLayer вимкнено")


REDIS_SOCKET_TIMEOUT_S = 0.4
_DEFAULT_PREVIEW_CURR_TTL_S = 1800  # SSOT fallback; runtime значення з config.json
PREVIEW_TAIL_RETAIN = 2000
PREVIEW_UPDATES_RETAIN = 2000

def _disk_bar_to_candle(
    raw: dict[str, Any],
    symbol: str,
    tf_s: int,
) -> Optional[CandleBar]:
    open_ms = raw.get("open_time_ms")
    close_ms = raw.get("close_time_ms")
    if open_ms is None or close_ms is None:
        return None
    low = raw.get("low")
    if low is None:
        low = raw.get("l")
    if low is None:
        return None
    try:
        src = raw.get("src")
        if src is None or src == "":
            src = "history"
        open_val = raw.get("open")
        if open_val is None:
            open_val = raw.get("o", 0.0)
        high_val = raw.get("high")
        if high_val is None:
            high_val = raw.get("h", 0.0)
        close_val = raw.get("close")
        if close_val is None:
            close_val = raw.get("c", 0.0)
        v_val = raw.get("volume")
        if v_val is None:
            v_val = raw.get("v", 0.0)
        return CandleBar(
            symbol=symbol,
            tf_s=int(tf_s),
            open_time_ms=int(open_ms),
            close_time_ms=int(close_ms),
            o=float(open_val),
            h=float(high_val),
            low=float(low),
            c=float(close_val),
            v=float(v_val),
            complete=bool(raw.get("complete", True)),
            src=str(src),
        )
    except Exception:
        logging.debug(
            "DISK_BAR_PARSE_FAIL symbol=%s tf_s=%s open_ms=%s",
            raw.get("symbol"), raw.get("tf_s"), raw.get("open_time_ms"),
        )
        return None

UPDATES_REDIS_RETAIN_DEFAULT = 2000


def _watermark_drop_reason(open_ms: int, wm_open_ms: Optional[int]) -> Optional[str]:
    if wm_open_ms is None:
        return None
    if open_ms == wm_open_ms:
        return "duplicate"
    if open_ms < wm_open_ms:
        return "stale"
    return None


def _mark_degraded(meta: dict[str, Any], reason: str) -> None:
    ext = meta.setdefault("extensions", {})
    if isinstance(ext, dict):
        degraded = ext.get("degraded")
        if isinstance(degraded, list):
            if reason not in degraded:
                degraded.append(reason)
        else:
            ext["degraded"] = [reason]


def _mark_redis_mismatch(
    meta: dict[str, Any],
    warnings: list[str],
    fields: list[str],
) -> None:
    warnings.append("redis_spec_mismatch")
    _mark_degraded(meta, "redis_spec_mismatch")
    if fields:
        ext = meta.setdefault("extensions", {})
        if isinstance(ext, dict):
            ext["redis_spec_mismatch_fields"] = list(fields)


def _mark_prime_pending(meta: dict[str, Any], warnings: list[str]) -> None:
    warnings.append("prime_pending")
    _mark_degraded(meta, "prime_pending")


def _mark_prime_broken(meta: dict[str, Any], warnings: list[str]) -> None:
    warnings.append("prime_broken")
    _mark_degraded(meta, "prime_broken")


def _mark_prime_incomplete(meta: dict[str, Any], warnings: list[str]) -> None:
    warnings.append("prime_incomplete")
    _mark_degraded(meta, "prime_incomplete")


def _mark_history_short(meta: dict[str, Any], warnings: list[str]) -> None:
    warnings.append("history_short")
    _mark_degraded(meta, "history_short")


def _mark_ram_short_window(
    meta: dict[str, Any],
    warnings: list[str],
    expected: int,
    got: int,
) -> None:
    warnings.append("insufficient_warmup")
    _mark_degraded(meta, "insufficient_warmup")
    ext = meta.setdefault("extensions", {})
    if isinstance(ext, dict):
        ext["expected"] = int(expected)
        ext["got"] = int(got)


def _ensure_bar_payload_end_excl(bar_payload: dict[str, Any]) -> Optional[int]:
    open_ms = bar_payload.get("open_time_ms")
    tf_s = bar_payload.get("tf_s")
    if not isinstance(open_ms, int) or not isinstance(tf_s, int) or tf_s <= 0:
        return None
    close_ms = int(open_ms + tf_s * 1000)
    bar_payload["close_time_ms"] = close_ms
    return close_ms


def _prime_available_final(
    payload: dict[str, Any],
    symbol: str,
    tf_s: int,
) -> Optional[int]:
    per_symbol = payload.get("prime_tail_len_by_symbol")
    if isinstance(per_symbol, dict):
        sym_entry = per_symbol.get(symbol)
        if isinstance(sym_entry, dict):
            value = sym_entry.get(str(tf_s))
            if isinstance(value, int):
                return value
            value = sym_entry.get(tf_s)
            if isinstance(value, int):
                return value
    per_tf = payload.get("prime_tail_len_by_tf_s")
    if isinstance(per_tf, dict):
        value = per_tf.get(str(tf_s))
        if isinstance(value, int):
            return value
        value = per_tf.get(tf_s)
        if isinstance(value, int):
            return value
    return None


def _prime_append_meta(
    meta: dict[str, Any],
    target_min: int,
    available_final: Optional[int],
    effective_min: int,
) -> None:
    ext = meta.setdefault("extensions", {})
    if not isinstance(ext, dict):
        return
    ext["prime_target_min"] = int(target_min)
    if available_final is not None:
        ext["prime_available_final"] = int(available_final)
        ext["prime_effective_min"] = int(effective_min)


@dataclass(frozen=True)
class WindowSpec:
    symbol: str
    tf_s: int
    limit: int
    since_open_ms: Optional[int] = None
    to_open_ms: Optional[int] = None
    cold_load: bool = False


@dataclass(frozen=True)
class ReadPolicy:
    force_disk: bool = False
    prefer_redis: bool = False
    disk_policy: str = "never"  # "never" | "bootstrap" | "explicit"


# ── Disk policy constants ──────────────────────────────────
DISK_POLICY_NEVER = "never"
DISK_POLICY_BOOTSTRAP = "bootstrap"
DISK_POLICY_EXPLICIT = "explicit"
BOOTSTRAP_WINDOW_S = 60  # disk дозволено протягом N сек після boot


@dataclass
class WindowResult:
    bars_lwc: list[dict[str, Any]]
    meta: dict[str, Any]
    warnings: list[str]


@dataclass(frozen=True)
class UpdatesSpec:
    symbol: str
    tf_s: int
    since_seq: Optional[int]
    limit: int
    include_preview: bool = False


@dataclass
class UpdatesResult:
    events: list[dict[str, Any]]
    cursor_seq: int
    disk_last_open_ms: Optional[int]
    bar_close_ms: Optional[int]
    ssot_write_ts_ms: Optional[int]
    api_seen_ts_ms: int
    meta: dict[str, Any]
    warnings: list[str]


@dataclass
class CommitResult:
    ok: bool
    reason: Optional[str]
    ssot_written: bool
    redis_written: bool
    updates_published: bool
    warnings: list[str]


class UnifiedDataStore:
    """UnifiedDataStore: оркестрація RAM↔Redis↔Disk."""

    def __init__(
        self,
        *,
        data_root: str,
        boot_id: str,
        tf_allowlist: set[int],
        min_coldload_bars: dict[int, int],
        role: str = "writer",
        ram_layer: Optional[RamLayer] = None,
        redis_layer: Optional[RedisLayer] = None,
        disk_layer: Optional[DiskLayer] = None,
        jsonl_appender: Optional[JsonlAppender] = None,
        redis_snapshot_writer: Optional[RedisSnapshotWriter] = None,
        updates_bus: Optional[Any] = None,
        redis_spec_mismatch: bool = False,
        redis_spec_mismatch_fields: Optional[list[str]] = None,
        preview_tf_allowlist: Optional[set[int]] = None,
        preview_tf_allowlist_source: str = "default",
        preview_curr_ttl_s: int = _DEFAULT_PREVIEW_CURR_TTL_S,
        preview_tail_retain: int = PREVIEW_TAIL_RETAIN,
        preview_updates_retain: int = PREVIEW_UPDATES_RETAIN,
    ) -> None:
        if role not in ("reader", "writer"):
            raise ValueError(f"UDS: невідома роль {role}")
        self._data_root = data_root
        self._boot_id = boot_id
        self._tf_allowlist = tf_allowlist
        self._min_coldload_bars = min_coldload_bars
        self._role = role
        self._ram = ram_layer or RamLayer()
        self._redis = redis_layer
        self._disk = disk_layer or DiskLayer(data_root)
        self._jsonl = jsonl_appender
        self._redis_writer = redis_snapshot_writer
        self._updates_bus = updates_bus
        self._wm_by_key: dict[tuple[str, int], int] = {}
        self._updates_bus_warned = False
        self._redis_spec_mismatch = bool(redis_spec_mismatch)
        self._redis_spec_mismatch_fields = list(redis_spec_mismatch_fields or [])
        self._preview_tf_allowlist = set(preview_tf_allowlist or DEFAULT_PREVIEW_TF_ALLOWLIST)
        self._preview_allowlist_source = str(preview_tf_allowlist_source or "default")
        self._preview_curr_ttl_s = int(preview_curr_ttl_s)
        self._preview_tail_retain = max(1, int(preview_tail_retain))
        self._preview_updates_retain = max(1, int(preview_updates_retain))
        self._preview_last_open_ms: dict[tuple[str, int], int] = {}
        self._preview_last_publish_ms: dict[tuple[str, int], int] = {}
        self._preview_tail_updates_total = 0
        self._preview_tail_updates_log_ts_ms = 0
        self._preview_nomix_violation = False
        self._preview_nomix_violation_reason: Optional[str] = None
        self._preview_nomix_violation_ts_ms: Optional[int] = None
        self._boot_ts = time.time()  # P1: timestamp старту для bootstrap window
        self._disk_hotpath_blocked = 0  # P4: лічильник заблокованих disk спроб
        self._disk_bootstrap_reads = 0  # P4: лічильник disk читань під час bootstrap
        self._disk_hotpath_last_log_ts = 0.0  # rate-limit логів
        # Throttle логів commit_final_bar drop (stale/duplicate) — 1 рядок / 30 с з підсумком
        self._commit_drop_counts: dict[str, int] = {}
        self._commit_drop_last_log_ts = 0.0
        self._split_brain_active = False
        self._degraded_warning_flag = os.getenv("UDS_COMMIT_DEGRADED_REASON_FF", "0") in {"1", "true", "yes", "on"}
        _METRIC_SPLIT_BRAIN_ACTIVE.set(0)
        if self._preview_allowlist_source == "fallback":
            Logging.debug(
                "UDS: preview_tf_allowlist_source=fallback tf_s=%s",
                sorted(self._preview_tf_allowlist),
            )

    def _head_tail_marker(self, bar: CandleBar) -> str:
        return (
            f"head:3 {bar.open_time_ms},{bar.o:.4f},{bar.h:.4f} "
            f"tail:3 {bar.open_time_ms},{bar.low:.4f},{bar.c:.4f}"
        )

    def _mark_split_brain_active(self, bar: CandleBar, degraded_reasons: list[str]) -> None:
        self._split_brain_active = True
        _METRIC_SPLIT_BRAIN_ACTIVE.set(1)
        reason_text = ",".join(degraded_reasons)
        Logging.warning(
            "[DEGRADED] [%s] [UDS] commit_final_bar: disk append ok, але Redis/pubsub деградовано (%s) | %s",
            bar.symbol,
            reason_text,
            self._head_tail_marker(bar),
        )

    def mark_split_brain_reconciled(self, *, source: str = "manual_replay") -> None:
        """Скидає split-brain після ручної реконсиляції диска до Redis."""
        self._split_brain_active = False
        _METRIC_SPLIT_BRAIN_ACTIVE.set(0)
        Logging.info(
            "[RECOVERED] [UDS] split-brain скинуто після реконсиляції source=%s | head:3 n/a tail:3 n/a",
            source,
        )

    def _set_preview_nomix_violation(self, reason: str) -> None:
        if not self._preview_nomix_violation:
            Logging.warning("UDS: preview_nomix_violation reason=%s", reason)
        self._preview_nomix_violation = True
        self._preview_nomix_violation_reason = reason
        self._preview_nomix_violation_ts_ms = int(time.time() * 1000)

    def _apply_preview_nomix_violation(self, meta: dict[str, Any], warnings: list[str]) -> None:
        if not self._preview_nomix_violation:
            return
        warnings.append("preview_nomix_violation")
        _mark_degraded(meta, "preview_nomix_violation")
        ext = meta.setdefault("extensions", {})
        if isinstance(ext, dict):
            if self._preview_nomix_violation_reason:
                ext["preview_nomix_violation_reason"] = self._preview_nomix_violation_reason
            if self._preview_nomix_violation_ts_ms is not None:
                ext["preview_nomix_violation_ts_ms"] = int(self._preview_nomix_violation_ts_ms)

    def read_window(self, spec: WindowSpec, policy: ReadPolicy) -> WindowResult:
        warnings: list[str] = []
        meta: dict[str, Any] = {"boot_id": self._boot_id}
        symbol = spec.symbol
        tf_s = spec.tf_s
        prime_ready = False
        prime_payload: Optional[dict[str, Any]] = None
        if self._redis_spec_mismatch:
            _mark_redis_mismatch(meta, warnings, self._redis_spec_mismatch_fields)
        if Logging.isEnabledFor(logging.DEBUG):
            Logging.debug(
                "UDS: читання вікна symbol=%s tf_s=%s limit=%s cold_load=%s force_disk=%s prefer_redis=%s",
                symbol,
                tf_s,
                spec.limit,
                spec.cold_load,
                policy.force_disk,
                policy.prefer_redis,
            )
        if tf_s not in self._tf_allowlist:
            warnings.append("tf_not_allowed")

        if policy.force_disk:
            result = self._read_window_disk(spec, meta, warnings)
            _log_window_result(result, warnings, tf_s)
            return result

        if spec.cold_load and policy.prefer_redis and self._redis is not None:
            prime_payload = self._redis.get_prime_ready_payload()
            if prime_payload is not None:
                ready_flag = prime_payload.get("ready")
                prime_ready = bool(ready_flag) if isinstance(ready_flag, bool) else True
            else:
                prime_ready = False
            if not prime_ready:
                _mark_prime_pending(meta, warnings)
            if tf_s not in self._min_coldload_bars:
                warnings.append("redis_min_missing")
                Logging.warning(
                    "UDS: відсутній min_coldload_bars для tf_s=%s, Redis cold-load вимкнено",
                    tf_s,
                )
            else:
                redis_result = self._read_window_redis(spec, meta, warnings)
                if redis_result is not None:
                    min_required = int(self._min_coldload_bars.get(tf_s, 0))
                    redis_len = redis_result.meta.get("redis_len")
                    if not isinstance(redis_len, int):
                        redis_len = len(redis_result.bars_lwc)
                        redis_result.meta["redis_len"] = redis_len

                    if prime_payload is not None:
                        available_final = _prime_available_final(
                            prime_payload,
                            symbol,
                            tf_s,
                        )
                        effective_min = min_required
                        if available_final is not None and min_required > 0:
                            effective_min = min(min_required, available_final)
                        _prime_append_meta(
                            redis_result.meta,
                            min_required,
                            available_final,
                            effective_min,
                        )
                        if available_final is not None and min_required > 0:
                            if available_final < min_required:
                                _mark_history_short(redis_result.meta, warnings)
                        if prime_ready and effective_min > 0 and redis_len < effective_min:
                            _mark_prime_incomplete(redis_result.meta, warnings)
                    _log_window_result(redis_result, warnings, tf_s)
                    return redis_result

        if spec.since_open_ms is None and spec.to_open_ms is None:
            ram_bars = self._ram.get_window(symbol, tf_s, spec.limit)
            if ram_bars is not None:
                ram_bars, geom = _ensure_sorted_dedup(ram_bars)
                if geom is not None:
                    _mark_geom_fix(meta, warnings, geom, source="ram", tf_s=tf_s)
                # P1-unified: якщо RAM має менше барів ніж запитано і disk доступний,
                # читаємо повний набір з диску замість повернення часткових даних.
                # Disk _read_window_disk() автоматично заповнює RAM (set_window).
                if spec.limit > 0 and len(ram_bars) < spec.limit:
                    if self._disk_allowed(policy, "ram_short"):
                        result = self._read_window_disk(spec, meta, warnings)
                        _log_window_result(result, warnings, tf_s)
                        return result
                    # Disk не доступний — повертаємо часткові дані з degraded
                    _mark_ram_short_window(meta, warnings, spec.limit, len(ram_bars))
                    meta.update({"source": "degraded", "redis_hit": False})
                else:
                    meta.update({"source": "ram", "redis_hit": False})
                result = WindowResult(ram_bars, meta, warnings)
                if policy.prefer_redis and prime_ready:
                    _mark_prime_broken(meta, warnings)
                _log_window_result(result, warnings, tf_s)
                return result

            # ── P1 disk_policy guard (RAM miss) ──
            if not self._disk_allowed(policy, "ram_miss"):
                meta.update({"source": "degraded", "disk_blocked": True,
                             "disk_blocked_reason": "ram_miss"})
                warnings.append("disk_disabled_cache_miss")
                result = WindowResult([], meta, warnings)
                _log_window_result(result, warnings, tf_s)
                return result
            result = self._read_window_disk(spec, meta, warnings)
            if policy.prefer_redis and prime_ready:
                _mark_prime_broken(result.meta, warnings)
            _log_window_result(result, warnings, tf_s)
            return result

        # ── P1 disk_policy guard (range query) ──
        if not self._disk_allowed(policy, "range_query"):
            meta.update({"source": "degraded", "disk_blocked": True,
                         "disk_blocked_reason": "range_query"})
            warnings.append("disk_disabled_range_query")
            result = WindowResult([], meta, warnings)
            _log_window_result(result, warnings, tf_s)
            return result
        result = self._read_window_disk(spec, meta, warnings)
        if policy.prefer_redis and prime_ready:
            _mark_prime_broken(result.meta, warnings)
        _log_window_result(result, warnings, tf_s)
        return result

    def read_updates(self, spec: UpdatesSpec) -> UpdatesResult:
        symbol = spec.symbol
        tf_s = spec.tf_s
        limit = spec.limit
        if Logging.isEnabledFor(logging.DEBUG):
            Logging.debug(
                "UDS: читання updates symbol=%s tf_s=%s limit=%s since_seq=%s",
                symbol,
                tf_s,
                limit,
                spec.since_seq,
            )
        warnings: list[str] = []
        meta: dict[str, Any] = {"boot_id": self._boot_id}
        if self._redis_spec_mismatch:
            _mark_redis_mismatch(meta, warnings, self._redis_spec_mismatch_fields)
        events: list[dict[str, Any]] = []
        cursor_seq = spec.since_seq if spec.since_seq is not None else 0
        gap: Optional[dict[str, Any]] = None

        preview_mode = tf_s in self._preview_tf_allowlist
        if spec.include_preview and not preview_mode:
            warnings.append("include_preview_ignored")
            ext = meta.setdefault("extensions", {})
            if isinstance(ext, dict):
                ext["include_preview_ignored"] = True
        if preview_mode:
            ext = meta.setdefault("extensions", {})
            if isinstance(ext, dict):
                ext["plane"] = "preview"
            if self._redis is None:
                warnings.append("preview_requires_redis")
                if isinstance(ext, dict):
                    ext["degraded"] = ["preview_requires_redis"]
            else:
                events, cursor_seq, gap, err = self._redis.read_preview_updates(
                    symbol,
                    tf_s,
                    spec.since_seq,
                    limit,
                    self._preview_updates_retain,
                )
                if err is not None:
                    warnings.append("redis_down")
                    if isinstance(ext, dict):
                        degraded = ext.get("degraded")
                        if isinstance(degraded, list):
                            if "redis_down" not in degraded:
                                degraded.append("redis_down")
                        else:
                            ext["degraded"] = ["redis_down"]
                    events = []
                    if spec.since_seq is not None:
                        cursor_seq = spec.since_seq
                    else:
                        cursor_seq = 0
        else:
            if self._updates_bus is None:
                warnings.append("updates_bus_missing")
            else:
                events, cursor_seq, gap, err = self._updates_bus.read_updates(
                    symbol,
                    tf_s,
                    spec.since_seq,
                    limit,
                )
                if err is not None:
                    warnings.append("redis_down")
                    ext = meta.setdefault("extensions", {})
                    if isinstance(ext, dict):
                        degraded = ext.get("degraded")
                        if isinstance(degraded, list):
                            if "redis_down" not in degraded:
                                degraded.append("redis_down")
                        else:
                            ext["degraded"] = ["redis_down"]
                    events = []
                    if spec.since_seq is not None:
                        cursor_seq = spec.since_seq
                    else:
                        cursor_seq = 0

        if gap is not None:
            warnings.append("cursor_gap")
            ext = meta.setdefault("extensions", {})
            if isinstance(ext, dict):
                ext["gap"] = gap

        if len(events) > MAX_EVENTS_PER_RESPONSE:
            events = events[-MAX_EVENTS_PER_RESPONSE:]
            warnings.append("max_events_trimmed")

        self._apply_preview_nomix_violation(meta, warnings)

        if not preview_mode:
            for ev in events:
                if ev.get("complete") is True and ev.get("source") in FINAL_SOURCES:
                    bar = ev.get("bar")
                    if isinstance(bar, dict):
                        self._ram.upsert_bar(symbol, tf_s, bar)

        api_seen_ts_ms = int(time.time() * 1000)
        disk_last_open_ms = None
        bar_close_ms = None
        ssot_write_ts_ms = None

        result = UpdatesResult(
            events=events,
            cursor_seq=cursor_seq,
            disk_last_open_ms=disk_last_open_ms,
            bar_close_ms=bar_close_ms,
            ssot_write_ts_ms=ssot_write_ts_ms,
            api_seen_ts_ms=api_seen_ts_ms,
            meta=meta,
            warnings=warnings,
        )
        _log_updates_result(result)
        return result

    def commit_final_bar(
        self,
        bar: CandleBar,
        *,
        ssot_write_ts_ms: Optional[int] = None,
    ) -> CommitResult:
        self._ensure_writer_role("commit_final_bar")
        warnings: list[str] = []
        if not isinstance(bar, CandleBar):
            Logging.warning("UDS: commit_final_bar очікує CandleBar")
            return CommitResult(False, "invalid_bar", False, False, False, warnings)
        if not bar.complete:
            Logging.warning(
                "UDS: commit_final_bar пропущено (complete=false) symbol=%s tf_s=%s open_ms=%s",
                bar.symbol,
                bar.tf_s,
                bar.open_time_ms,
            )
            return CommitResult(False, "not_complete", False, False, False, warnings)
        if bar.src not in FINAL_SOURCES:
            Logging.warning(
                "UDS: commit_final_bar пропущено (non_final_source) symbol=%s tf_s=%s src=%s",
                bar.symbol,
                bar.tf_s,
                bar.src,
            )
            return CommitResult(False, "non_final_source", False, False, False, warnings)

        wm = self._init_watermark_for_key(bar.symbol, bar.tf_s)
        drop_reason = _watermark_drop_reason(bar.open_time_ms, wm)
        if drop_reason is not None:
            _OBS.inc_writer_drop(drop_reason, bar.tf_s)
            # Throttle: один підсумковий рядок раз на 30 с замість кожного дропа (антиспам)
            self._commit_drop_counts[drop_reason] = self._commit_drop_counts.get(drop_reason, 0) + 1
            now_mono = time.monotonic()
            if now_mono - self._commit_drop_last_log_ts >= 30.0:
                stale_n = self._commit_drop_counts.get("stale", 0)
                dup_n = self._commit_drop_counts.get("duplicate", 0)
                if stale_n or dup_n:
                    Logging.warning(
                        "UDS: commit_final_bar drops (throttled 30s) stale=%s duplicate=%s example symbol=%s tf_s=%s open_ms=%s wm_open_ms=%s",
                        stale_n,
                        dup_n,
                        bar.symbol,
                        bar.tf_s,
                        bar.open_time_ms,
                        wm,
                    )
                self._commit_drop_counts.clear()
                self._commit_drop_last_log_ts = now_mono
            return CommitResult(False, drop_reason, False, False, False, warnings)

        ssot_written = self._append_to_disk(bar, ssot_write_ts_ms, warnings)
        redis_written = self._write_redis_snapshot(bar, warnings)
        updates_published = self._publish_update(bar, warnings)
        degraded_reasons: list[str] = []
        if not redis_written:
            degraded_reasons.append("redis_write_failed")
        if not updates_published:
            degraded_reasons.append("pubsub_failed")
        if ssot_written and degraded_reasons:
            if self._degraded_warning_flag:
                warnings.append("degraded_reason:" + ",".join(degraded_reasons))
            self._mark_split_brain_active(bar, degraded_reasons)

        if ssot_written:
            self._wm_by_key[(bar.symbol, bar.tf_s)] = bar.open_time_ms
        if ssot_written:
            self._ram.upsert_bar(bar.symbol, bar.tf_s, bar.to_dict())
        # Bridge: фінальний бар preview-TF → preview ring для UI
        if ssot_written and bar.tf_s in self._preview_tf_allowlist:
            self._publish_final_to_preview_ring(bar)
        ok = ssot_written
        reason = None if ok else "ssot_write_failed"
        return CommitResult(ok, reason, ssot_written, redis_written, updates_published, warnings)

    def publish_promoted_bar(self, bar: CandleBar) -> bool:
        """Публікує promoted бар (tick→complete) у preview ring.

        Promoted бар — це tick preview, який завершився по часу (rollover).
        Він NOT in FINAL_SOURCES, тому не йде на диск,
        але позначений complete=True щоб UI одразу мав "mock final".
        Коли прийде справжній History final — він замінить promoted.
        """
        if not isinstance(bar, CandleBar):
            return False
        if bar.src != "tick_promoted":
            Logging.warning("UDS: publish_promoted_bar — src=%s (очікується tick_promoted)", bar.src)
            return False
        if not bar.complete:
            return False
        if bar.tf_s not in self._preview_tf_allowlist:
            return False
        if self._redis is None:
            return False
        try:
            bar_payload = bar.to_dict()
            _ensure_bar_payload_end_excl(bar_payload)
            event = {
                "key": {
                    "symbol": bar.symbol,
                    "tf_s": int(bar.tf_s),
                    "open_ms": int(bar.open_time_ms),
                },
                "bar": bar_payload,
                "complete": True,
                "source": "tick_promoted",
                "event_ts": int(bar_payload.get("close_time_ms")) if bar.complete else None,
            }
            self._redis.publish_preview_event(
                bar.symbol,
                int(bar.tf_s),
                event,
                self._preview_updates_retain,
            )
            return True
        except Exception as exc:
            Logging.warning(
                "UDS: publish_promoted_bar failed symbol=%s tf_s=%s err=%s",
                bar.symbol, bar.tf_s, exc,
            )
            return False

    def publish_preview_bar(self, bar: CandleBar, *, ttl_s: Optional[int] = None) -> None:
        self._ensure_writer_role("publish_preview_bar")
        if not isinstance(bar, CandleBar):
            Logging.warning("UDS: publish_preview_bar очікує CandleBar")
            return
        if bar.complete:
            self._set_preview_nomix_violation("complete_true")
            Logging.warning(
                "UDS: publish_preview_bar пропущено (complete=true) symbol=%s tf_s=%s open_ms=%s",
                bar.symbol,
                bar.tf_s,
                bar.open_time_ms,
            )
            return
        if bar.src in FINAL_SOURCES:
            self._set_preview_nomix_violation("final_source")
            Logging.warning(
                "UDS: publish_preview_bar пропущено (final_source) symbol=%s tf_s=%s src=%s",
                bar.symbol,
                bar.tf_s,
                bar.src,
            )
            return
        if bar.tf_s not in self._preview_tf_allowlist:
            Logging.warning(
                "UDS: publish_preview_bar пропущено (preview_tf_reject) symbol=%s tf_s=%s",
                bar.symbol,
                bar.tf_s,
            )
            return
        if self._redis is None:
            Logging.warning(
                "UDS: publish_preview_bar пропущено (redis_missing) symbol=%s tf_s=%s",
                bar.symbol,
                bar.tf_s,
            )
            return

        payload_ts_ms = int(time.time() * 1000)
        bar_payload = bar.to_dict()
        close_ms = _ensure_bar_payload_end_excl(bar_payload)
        # Redis close_ms = end-incl (open + tf*1000 - 1), matching redis_snapshot convention.
        # CandleBar internal = end-excl; conversion at Redis boundary.
        close_ms_incl = int(close_ms - 1) if close_ms is not None else int(bar_payload.get("close_time_ms")) - 1
        bar_item = {
            "open_ms": int(bar_payload.get("open_time_ms")),
            "close_ms": close_ms_incl,
            "o": bar_payload.get("o"),
            "h": bar_payload.get("h"),
            "l": bar_payload.get("low"),
            "c": bar_payload.get("c"),
            "v": bar_payload.get("v", 0.0),
        }
        curr_payload = {
            "v": 1,
            "symbol": bar.symbol,
            "tf_s": int(bar.tf_s),
            "bar": bar_item,
            "complete": False,
            "source": str(bar.src),
            "payload_ts_ms": payload_ts_ms,
        }
        ttl_curr = ttl_s if ttl_s is not None else self._preview_curr_ttl_s
        try:
            self._redis.write_preview_curr(bar.symbol, bar.tf_s, curr_payload, ttl_curr)
        except Exception as exc:
            Logging.warning("UDS: preview_curr write failed err=%s", exc)
            return

        key = (bar.symbol, bar.tf_s)
        last_open = self._preview_last_open_ms.get(key)
        rollover = last_open is None or last_open != bar.open_time_ms
        self._preview_last_open_ms[key] = bar.open_time_ms

        # P2X.6-U2: tail оновлюється на КОЖЕН publish (не лише rollover),
        # щоб /api/bars завжди повертав актуальну форму бара.
        try:
            tail_payload, _, _ = self._redis.read_preview_tail(bar.symbol, bar.tf_s)
            tail_bars = []
            if isinstance(tail_payload, dict):
                raw = tail_payload.get("bars")
                if isinstance(raw, list):
                    tail_bars = [b for b in raw if isinstance(b, dict)]
            if tail_bars and isinstance(tail_bars[-1].get("open_ms"), int):
                if int(tail_bars[-1].get("open_ms")) == int(bar_item["open_ms"]):
                    tail_bars[-1] = bar_item
                else:
                    tail_bars.append(bar_item)
            else:
                tail_bars.append(bar_item)
            if len(tail_bars) > self._preview_tail_retain:
                tail_bars = tail_bars[-self._preview_tail_retain :]
            new_tail = {
                "v": 1,
                "symbol": bar.symbol,
                "tf_s": int(bar.tf_s),
                "bars": tail_bars,
                "complete": False,
                "source": str(bar.src),
                "payload_ts_ms": payload_ts_ms,
            }
            tail_ttl = self._preview_curr_ttl_s * 2 if self._preview_curr_ttl_s else None
            self._redis.write_preview_tail(bar.symbol, bar.tf_s, new_tail, ttl_s=tail_ttl)
            self._preview_tail_updates_total += 1
            now_ms = int(time.time() * 1000)
            if now_ms - self._preview_tail_updates_log_ts_ms >= 60_000:
                self._preview_tail_updates_log_ts_ms = now_ms
                Logging.info(
                    "UDS: preview_tail_updates_total=%s",
                    self._preview_tail_updates_total,
                )
        except Exception as exc:
            Logging.warning("UDS: preview_tail write failed err=%s", exc)

        self._publish_preview_update(bar_payload, throttle_ms=500, rollover=rollover)

    def read_preview_window(
        self,
        symbol: str,
        tf_s: int,
        limit: int,
        *,
        include_current: bool = True,
    ) -> WindowResult:
        warnings: list[str] = []
        meta: dict[str, Any] = {"boot_id": self._boot_id}
        if self._redis_spec_mismatch:
            _mark_redis_mismatch(meta, warnings, self._redis_spec_mismatch_fields)
        ext = meta.setdefault("extensions", {})
        if isinstance(ext, dict):
            ext["plane"] = "preview"
        self._apply_preview_nomix_violation(meta, warnings)
        if tf_s not in self._preview_tf_allowlist:
            warnings.append("preview_tf_not_allowed")
            if isinstance(ext, dict):
                ext["degraded"] = ["preview_tf_not_allowed"]
            meta["source"] = "preview_unavailable"
            return WindowResult([], meta, warnings)
        if self._redis is None:
            warnings.append("preview_requires_redis")
            if isinstance(ext, dict):
                ext["degraded"] = ["preview_requires_redis"]
            meta["source"] = "preview_unavailable"
            return WindowResult([], meta, warnings)

        tail_payload, _, tail_err = self._redis.read_preview_tail(symbol, tf_s)
        curr_payload, _, curr_err = self._redis.read_preview_curr(symbol, tf_s)
        if tail_err is not None and curr_err is not None:
            warnings.append("preview_empty")
            if isinstance(ext, dict):
                ext["degraded"] = ["preview_empty"]
            meta["source"] = "preview_unavailable"
            return WindowResult([], meta, warnings)

        bars: list[dict[str, Any]] = []
        source = "preview_unavailable"
        if isinstance(tail_payload, dict):
            bars = self._preview_payload_to_bars(tail_payload, symbol, tf_s)
            if bars:
                source = "preview_tail"

        if include_current and isinstance(curr_payload, dict):
            curr_bars = self._preview_payload_to_bars(curr_payload, symbol, tf_s)
            if curr_bars:
                curr_bar = curr_bars[-1]
                bars = self._merge_preview_curr(bars, curr_bar)
                if source == "preview_unavailable":
                    source = "preview_curr"

        bars, geom = _ensure_sorted_dedup(bars)
        if geom is not None:
            _mark_geom_fix(meta, warnings, geom, source=source, tf_s=tf_s)
        lwc = self._bars_to_lwc(bars)
        if limit > 0:
            lwc = lwc[-limit:]
        if not lwc:
            warnings.append("preview_empty")
            if isinstance(ext, dict):
                ext["degraded"] = ["preview_empty"]
        meta["source"] = source
        return WindowResult(lwc, meta, warnings)

    def _preview_payload_to_bars(
        self, payload: dict[str, Any], symbol: str, tf_s: int
    ) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        source = str(payload.get("source", "preview_tick"))
        raw_bars = payload.get("bars")
        if isinstance(raw_bars, list):
            for item in raw_bars:
                if not isinstance(item, dict):
                    continue
                bar = self._redis_payload_bar_to_canonical(item, symbol, tf_s, False, source)
                if bar is not None:
                    out.append(bar)
            return out
        raw_bar = payload.get("bar")
        if isinstance(raw_bar, dict):
            bar = self._redis_payload_bar_to_canonical(raw_bar, symbol, tf_s, False, source)
            if bar is not None:
                out.append(bar)
        return out

    def _merge_preview_curr(
        self, bars: list[dict[str, Any]], curr: dict[str, Any]
    ) -> list[dict[str, Any]]:
        if not bars:
            return [curr]
        last = bars[-1]
        if isinstance(last, dict) and last.get("open_time_ms") == curr.get("open_time_ms"):
            bars[-1] = curr
        else:
            bars.append(curr)
        return bars

    def _publish_final_to_preview_ring(self, bar: CandleBar) -> bool:
        """Публікує фінальний бар у preview ring, щоб UI побачив final>preview.

        Викликається з commit_final_bar для TF ∈ preview_tf_allowlist.
        UI applyUpdates вже реалізує final>preview семантику.
        """
        if self._redis is None:
            return False
        try:
            bar_payload = bar.to_dict()
            event = {
                "key": {
                    "symbol": bar.symbol,
                    "tf_s": int(bar.tf_s),
                    "open_ms": int(bar.open_time_ms),
                },
                "bar": bar_payload,
                "complete": True,
                "source": str(bar.src),
                "event_ts": int(bar_payload.get("close_time_ms")) if bar.complete else None,
            }
            self._redis.publish_preview_event(
                bar.symbol,
                int(bar.tf_s),
                event,
                self._preview_updates_retain,
            )
            return True
        except Exception as exc:
            Logging.warning(
                "UDS: final→preview ring publish failed symbol=%s tf_s=%s err=%s",
                bar.symbol, bar.tf_s, exc,
            )
            return False

    def _publish_preview_update(
        self,
        bar_payload: dict[str, Any],
        *,
        throttle_ms: int,
        rollover: bool,
    ) -> bool:
        if self._redis is None:
            return False
        key = (str(bar_payload.get("symbol")), int(bar_payload.get("tf_s", 0)))
        now_ms = int(time.time() * 1000)
        last_ts = self._preview_last_publish_ms.get(key)
        if not rollover and last_ts is not None and now_ms - last_ts < int(throttle_ms):
            return False
        self._preview_last_publish_ms[key] = now_ms
        event = {
            "key": {
                "symbol": str(bar_payload.get("symbol")),
                "tf_s": int(bar_payload.get("tf_s", 0)),
                "open_ms": int(bar_payload.get("open_time_ms", 0)),
            },
            "bar": bar_payload,
            "complete": False,
            "source": str(bar_payload.get("src", "preview_tick")),
            "event_ts": None,
        }
        try:
            self._redis.publish_preview_event(
                event["key"]["symbol"],
                event["key"]["tf_s"],
                event,
                self._preview_updates_retain,
            )
            return True
        except Exception as exc:
            Logging.warning("UDS: preview updates publish failed err=%s", exc)
            return False

    def snapshot_status(self) -> dict[str, Any]:
        status = {"boot_id": self._boot_id}
        status.update(self._ram.stats())
        status["redis_enabled"] = self._redis is not None
        status["redis_spec_mismatch"] = bool(self._redis_spec_mismatch)
        status["redis_spec_mismatch_fields"] = list(self._redis_spec_mismatch_fields)
        status["prime_target_min_by_tf_s"] = dict(self._min_coldload_bars)
        status["preview_tf_allowlist_s"] = sorted(self._preview_tf_allowlist)
        status["preview_tf_allowlist_source"] = self._preview_allowlist_source
        status["preview_tail_updates_total"] = self._preview_tail_updates_total
        # ── P4: disk policy telemetry ──
        status["disk_hotpath_blocked_total"] = self._disk_hotpath_blocked
        status["disk_bootstrap_reads_total"] = self._disk_bootstrap_reads
        status["disk_bootstrap_elapsed_s"] = round(time.time() - self._boot_ts, 1)
        status["disk_bootstrap_window_s"] = BOOTSTRAP_WINDOW_S
        if self._preview_nomix_violation:
            status["preview_nomix_violation"] = True
            status["preview_nomix_violation_reason"] = self._preview_nomix_violation_reason
            status["preview_nomix_violation_ts_ms"] = self._preview_nomix_violation_ts_ms
        if self._redis is not None:
            payload = self._redis.get_prime_ready_payload()
            status["prime_ready_payload"] = payload
            status["prime_ready"] = bool(payload.get("ready")) if isinstance(payload, dict) else False
        status["ts_ms"] = int(time.time() * 1000)
        return status

    def prime_redis_from_bars(self, symbol: str, tf_s: int, bars: list[CandleBar]) -> int:
        if self._redis_writer is None:
            Logging.warning(
                "UDS: prime_redis_from_bars пропущено (redis_writer_missing) symbol=%s tf_s=%s",
                symbol,
                tf_s,
            )
            return 0
        try:
            return self._redis_writer.prime_from_bars(symbol, tf_s, bars)
        except Exception as exc:
            Logging.warning(
                "UDS: prime_redis_from_bars failed symbol=%s tf_s=%s err=%s",
                symbol,
                tf_s,
                exc,
            )
            return 0

    def bootstrap_prime_from_disk(
        self,
        symbol: str,
        tf_s: int,
        tail_n: int,
        *,
        log_detail: bool = False,
    ) -> int:
        if self._redis_writer is None:
            Logging.warning(
                "UDS: bootstrap_prime_from_disk пропущено (redis_writer_missing) symbol=%s tf_s=%s",
                symbol,
                tf_s,
            )
            return 0
        if tail_n <= 0:
            return 0
        bars, geom = self._disk.read_window_with_geom(
            symbol,
            tf_s,
            tail_n,
            since_open_ms=None,
            to_open_ms=None,
            use_tail=True,
            final_only=True,
            skip_preview=True,
            final_sources=FINAL_SOURCES,
        )
        if geom is not None and log_detail:
            Logging.warning(
                "UDS: bootstrap geom_fix symbol=%s tf_s=%s sorted=%s dedup_dropped=%s",
                symbol,
                tf_s,
                geom.get("sorted"),
                geom.get("dedup_dropped"),
            )
        candles: list[CandleBar] = []
        for raw in bars:
            candle = _disk_bar_to_candle(raw, symbol, tf_s)
            if candle is None:
                continue
            candles.append(candle)
        raw_count = len(bars)
        final_count = len(candles)
        if not candles:
            Logging.info(
                "UDS_PRIME_SUMMARY symbol=%s tf_s=%s raw_count=%s final_count=%s wrote_snap=%s",
                symbol,
                tf_s,
                raw_count,
                final_count,
                False,
            )
            return 0
        try:
            count = self._redis_writer.prime_from_bars(symbol, tf_s, candles)
            Logging.info(
                "UDS_PRIME_SUMMARY symbol=%s tf_s=%s raw_count=%s final_count=%s wrote_snap=%s",
                symbol,
                tf_s,
                raw_count,
                final_count,
                bool(count > 0),
            )
            if log_detail:
                Logging.info(
                    "UDS: bootstrap_prime_from_disk ok symbol=%s tf_s=%s count=%s",
                    symbol,
                    tf_s,
                    count,
                )
            return count
        except Exception as exc:
            Logging.warning(
                "UDS: bootstrap_prime_from_disk failed symbol=%s tf_s=%s err=%s",
                symbol,
                tf_s,
                exc,
            )
            return 0

    def set_cache_state(
        self,
        *,
        primed: bool,
        prime_partial: bool,
        priming_ts_ms: int,
        primed_counts: dict[str, int],
        degraded: list[str],
        errors: list[str],
    ) -> None:
        if self._redis_writer is None:
            return
        self._redis_writer.set_cache_state(
            primed=primed,
            prime_partial=prime_partial,
            priming_ts_ms=priming_ts_ms,
            primed_counts=primed_counts,
            degraded=degraded,
            errors=errors,
        )

    def set_gap_state(
        self,
        *,
        backlog_bars: int,
        gap_from_ms: Optional[int],
        gap_to_ms: Optional[int],
        policy: Optional[str],
    ) -> None:
        if self._redis_writer is None:
            return
        self._redis_writer.set_gap_state(
            backlog_bars=backlog_bars,
            gap_from_ms=gap_from_ms,
            gap_to_ms=gap_to_ms,
            policy=policy,
        )

    def set_prime_ready(
        self,
        payload: dict[str, Any],
        ttl_s: Optional[int],
        component: Optional[str] = None,
    ) -> None:
        if self._redis_writer is None:
            Logging.warning("UDS: set_prime_ready пропущено (redis_writer_missing)")
            return
        try:
            self._redis_writer.set_prime_ready(payload, ttl_s, component=component)
        except Exception as exc:
            Logging.warning("UDS: set_prime_ready failed err=%s", exc)

    def has_redis_writer(self) -> bool:
        return self._redis_writer is not None

    def close(self) -> None:
        if self._jsonl is not None:
            try:
                self._jsonl.close()
            except Exception:
                pass
        if self._redis_writer is not None:
            try:
                self._redis_writer.close()
            except Exception:
                pass

    def _ensure_writer_role(self, action: str) -> None:
        if self._role != "writer":
            Logging.warning(
                "UDS: запис заборонено (UDS_WRITE_FORBIDDEN) action=%s role=%s",
                action,
                self._role,
            )
            raise RuntimeError(f"UDS_WRITE_FORBIDDEN role={self._role}")

    def get_watermark_open_ms(self, symbol: str, tf_s: int) -> Optional[int]:
        return self._init_watermark_for_key(symbol, tf_s)

    def _init_watermark_for_key(self, symbol: str, tf_s: int) -> Optional[int]:
        key = (symbol, tf_s)
        if key in self._wm_by_key:
            return self._wm_by_key[key]
        last_open_ms = self._disk.last_open_ms(symbol, tf_s)
        if last_open_ms is not None:
            self._wm_by_key[key] = last_open_ms
            Logging.debug(
                "UDS: watermark ініціалізовано symbol=%s tf_s=%s wm_open_ms=%s source=disk_last_open_ms",
                symbol,
                tf_s,
                last_open_ms,
            )
        return last_open_ms

    def _append_to_disk(
        self,
        bar: CandleBar,
        ssot_write_ts_ms: Optional[int],
        warnings: list[str],
    ) -> bool:
        if self._jsonl is None:
            warnings.append("ssot_writer_missing")
            Logging.warning(
                "UDS: ssot writer відсутній (jsonl_appender) symbol=%s tf_s=%s",
                bar.symbol,
                bar.tf_s,
            )
            return False
        try:
            _ = ssot_write_ts_ms
            self._jsonl.append(bar)
            return True
        except Exception as exc:
            warnings.append("ssot_write_failed")
            Logging.warning(
                "UDS: ssot write failed symbol=%s tf_s=%s err=%s",
                bar.symbol,
                bar.tf_s,
                exc,
            )
            return False

    def _write_redis_snapshot(self, bar: CandleBar, warnings: list[str]) -> bool:
        if self._redis_writer is None:
            warnings.append("redis_writer_missing")
            _METRIC_REDIS_WRITE_FAIL_TOTAL.inc()
            Logging.warning(
                "[DEGRADED] [%s] [UDS] Redis snapshot writer відсутній tf_s=%s | %s",
                bar.symbol,
                bar.tf_s,
                self._head_tail_marker(bar),
            )
            return False
        try:
            self._redis_writer.put_bar(bar)
            return True
        except Exception as exc:
            warnings.append("redis_write_failed")
            _METRIC_REDIS_WRITE_FAIL_TOTAL.inc()
            Logging.warning(
                "[DEGRADED] [%s] [UDS] Помилка запису Redis snapshot tf_s=%s err=%s | %s",
                bar.symbol,
                bar.tf_s,
                exc,
                self._head_tail_marker(bar),
            )
            return False

    def _publish_update(self, bar: CandleBar, warnings: list[str]) -> bool:
        if self._updates_bus is None:
            warnings.append("updates_bus_missing")
            _METRIC_PUBSUB_FAIL_TOTAL.inc()
            if not self._updates_bus_warned:
                self._updates_bus_warned = True
                Logging.warning(
                    "[DEGRADED] [%s] [UDS] updates bus відсутній, events не публікуються | %s",
                    bar.symbol,
                    self._head_tail_marker(bar),
                )
            return False
        try:
            bar_payload = bar.to_dict()
            event = {
                "key": {
                    "symbol": bar.symbol,
                    "tf_s": int(bar.tf_s),
                    "open_ms": int(bar.open_time_ms),
                },
                "bar": bar_payload,
                "complete": bool(bar.complete),
                "source": str(bar.src),
                "event_ts": int(bar_payload.get("close_time_ms")) if bar.complete else None,
            }
            self._updates_bus.publish(event)
            return True
        except Exception as exc:
            warnings.append("updates_publish_failed")
            _METRIC_PUBSUB_FAIL_TOTAL.inc()
            Logging.warning(
                "[DEGRADED] [%s] [UDS] Помилка публікації updates err=%s | %s",
                bar.symbol,
                exc,
                self._head_tail_marker(bar),
            )
            return False

    # ── P1: disk_policy enforcement ─────────────────────────
    def _disk_allowed(self, policy: ReadPolicy, reason: str) -> bool:
        """Перевіряє чи дозволено disk-читання згідно disk_policy.

        Returns True якщо disk дозволено, False якщо заблоковано.
        Побічні ефекти: лічильник + rate-limited лог.
        """
        dp = policy.disk_policy
        if dp == DISK_POLICY_EXPLICIT:
            return True
        if dp == DISK_POLICY_BOOTSTRAP:
            elapsed = time.time() - self._boot_ts
            if elapsed <= BOOTSTRAP_WINDOW_S:
                self._disk_bootstrap_reads += 1
                return True
            # bootstrap вікно вичерпано — блокуємо
        # dp == "never" або bootstrap expired
        self._disk_hotpath_blocked += 1
        now = time.time()
        if now - self._disk_hotpath_last_log_ts > 5.0:
            self._disk_hotpath_last_log_ts = now
            Logging.warning(
                "DISK_HOTPATH_BLOCKED: reason=%s policy=%s "
                "blocked_total=%d bootstrap_reads=%d",
                reason, dp,
                self._disk_hotpath_blocked,
                self._disk_bootstrap_reads,
            )
        return False

    def _read_window_disk(
        self,
        spec: WindowSpec,
        meta: dict[str, Any],
        warnings: list[str],
    ) -> WindowResult:
        use_tail = (
            spec.cold_load
            and spec.since_open_ms is None
            and spec.to_open_ms is None
        )
        bars, geom = self._disk.read_window_with_geom(
            spec.symbol,
            spec.tf_s,
            spec.limit,
            since_open_ms=spec.since_open_ms,
            to_open_ms=spec.to_open_ms,
            use_tail=use_tail,
        )
        if geom is None:
            bars, geom = _ensure_sorted_dedup(bars)
        if geom is not None:
            _mark_geom_fix(
                meta,
                warnings,
                geom,
                source="disk_tail" if use_tail else "disk_range",
                tf_s=spec.tf_s,
            )
        lwc = self._bars_to_lwc(bars)
        meta.update(
            {
                "source": "disk_tail" if use_tail else "disk_range",
                "redis_hit": False,
            }
        )
        if spec.since_open_ms is None and spec.to_open_ms is None:
            self._ram.set_window(spec.symbol, spec.tf_s, lwc)
        return WindowResult(lwc, meta, warnings)

    def read_tail_candles(
        self,
        symbol: str,
        tf_s: int,
        limit: int,
    ) -> list[CandleBar]:
        if limit <= 0:
            return []
        bars, _geom = self._disk.read_window_with_geom(
            symbol,
            tf_s,
            limit,
            use_tail=True,
            final_only=False,
            skip_preview=False,
            final_sources=None,
        )
        out: list[CandleBar] = []
        for raw in bars:
            candle = _disk_bar_to_candle(raw, symbol, tf_s)
            if candle is not None:
                out.append(candle)
        return out

    def load_day_open_times(
        self,
        symbol: str,
        tf_s: int,
        day: str,
    ) -> set[int]:
        sym_dir = symbol.replace("/", "_")
        tf_dir = f"tf_{tf_s}"
        path = os.path.join(self._data_root, sym_dir, tf_dir, f"part-{day}.jsonl")
        out: set[int] = set()
        if not os.path.isfile(path):
            return out
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                        open_ms = obj.get("open_time_ms")
                        if isinstance(open_ms, int):
                            out.add(open_ms)
                    except Exception:
                        continue
        except Exception:
            return out
        return out

    def _read_window_redis(
        self,
        spec: WindowSpec,
        meta: dict[str, Any],
        warnings: list[str],
    ) -> Optional[WindowResult]:
        if self._redis is None:
            return None
        def _mark_redis_fallback(code: str) -> None:
            warnings.append(f"redis_fallback:{code}")
            _mark_degraded(meta, code)

        def _mark_redis_small_tail() -> None:
            warnings.append("redis_small_tail")
            _mark_degraded(meta, "redis_small_tail")

        payload, ttl_left, source, err = self._redis.read_tail_or_snap(
            spec.symbol, spec.tf_s
        )
        if err is not None:
            Logging.warning(
                "UDS: Redis помилка читання source=%s tf_s=%s error=%s",
                spec.symbol,
                spec.tf_s,
                err,
            )
            if err == "redis_error":
                warnings.append("redis_down")
                ext = meta.setdefault("extensions", {})
                if isinstance(ext, dict):
                    degraded = ext.get("degraded")
                    if isinstance(degraded, list):
                        if "redis_down" not in degraded:
                            degraded.append("redis_down")
                    else:
                        ext["degraded"] = ["redis_down"]
            _mark_redis_fallback(err)
            meta.update(
                {
                    "source": "disk",
                    "redis_hit": False,
                    "redis_error_code": err,
                }
            )
            return None
        if payload is None:
            Logging.info(
                "UDS: Redis порожній payload symbol=%s tf_s=%s",
                spec.symbol,
                spec.tf_s,
            )
            _mark_redis_fallback("redis_empty")
            meta.update(
                {
                    "source": "disk",
                    "redis_hit": False,
                    "redis_error_code": "redis_empty",
                }
            )
            return None
        if ttl_left is None or ttl_left <= 0:
            Logging.warning(
                "UDS: Redis TTL недійсний symbol=%s tf_s=%s ttl_left=%s",
                spec.symbol,
                spec.tf_s,
                ttl_left,
            )
            _mark_redis_fallback("redis_ttl_invalid")
            meta.update(
                {
                    "source": "disk",
                    "redis_hit": False,
                    "redis_error_code": "redis_ttl_invalid",
                }
            )
            return None

        bars = self._redis_payload_to_bars(payload, spec.symbol, spec.tf_s)
        if not bars:
            _mark_redis_fallback("redis_empty")
            meta.update(
                {
                    "source": "disk",
                    "redis_hit": False,
                    "redis_error_code": "redis_empty",
                }
            )
            return None

        bars, geom = _ensure_sorted_dedup(bars)
        if geom is not None:
            _mark_geom_fix(meta, warnings, geom, source=source or "redis_tail", tf_s=spec.tf_s)

        lwc = self._bars_to_lwc(bars)
        if spec.limit > 0:
            lwc = lwc[-spec.limit :]

        min_required = int(self._min_coldload_bars.get(spec.tf_s, 0))
        if min_required > 0 and len(lwc) < min_required:
            _mark_redis_small_tail()
            meta.update(
                {
                    "source": source or "redis_tail",
                    "redis_hit": True,
                    "redis_len": len(lwc),
                    "redis_ttl_s_left": ttl_left,
                    "redis_payload_ts_ms": payload.get("payload_ts_ms"),
                    "redis_seq": payload.get("last_seq")
                    if isinstance(payload.get("last_seq"), int)
                    else payload.get("seq"),
                }
            )
            ext = meta.setdefault("extensions", {})
            if isinstance(ext, dict):
                ext["partial"] = True
            self._ram.set_window(spec.symbol, spec.tf_s, lwc)
            return WindowResult(lwc, meta, warnings)

        meta.update(
            {
                "source": source or "redis_tail",
                "redis_hit": True,
                "redis_ttl_s_left": ttl_left,
                "redis_payload_ts_ms": payload.get("payload_ts_ms"),
                "redis_seq": payload.get("last_seq")
                if isinstance(payload.get("last_seq"), int)
                else payload.get("seq"),
            }
        )
        self._ram.set_window(spec.symbol, spec.tf_s, lwc)
        return WindowResult(lwc, meta, warnings)

    def _bars_to_lwc(self, bars: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for raw in bars:
            b = raw
            t = b.get("open_time_ms")
            if not isinstance(t, int):
                continue
            low_val = b.get("low", b.get("l"))
            complete = bool(b.get("complete", True))
            item = {
                "time": t // 1000,
                "open": float(b.get("o")),
                "high": float(b.get("h")),
                "low": float(low_val),
                "close": float(b.get("c")),
                "volume": float(b.get("v", 0.0)),
                "open_time_ms": int(b.get("open_time_ms")),
                "close_time_ms": int(b.get("close_time_ms"))
                if "close_time_ms" in b
                else None,
                "tf_s": int(b.get("tf_s")),
                "src": str(b.get("src", "")),
                "complete": complete,
            }
            ext_raw = b.get("extensions")
            if isinstance(ext_raw, dict) and ext_raw:
                item["extensions"] = dict(ext_raw)

                # Downstream soft-penalty: partial bars не відкидаємо hard,
                # але додаємо явний penalty для аналітики/сигналів.
                is_partial = bool(
                    ext_raw.get("partial")
                    or ext_raw.get("partial_calendar_pause")
                    or ext_raw.get("boundary_partial")
                )
                if is_partial:
                    src_n = ext_raw.get("source_count")
                    exp_n = ext_raw.get("expected_count")
                    if isinstance(src_n, int) and isinstance(exp_n, int) and exp_n > 0:
                        # Динамічний soft-penalty: частка пропущених source-барів.
                        item["partial_penalty"] = round(max(0.0, min(1.0, 1.0 - (src_n / exp_n))), 4)
                    else:
                        item["partial_penalty"] = 0.15
            if complete and "close_time_ms" in b:
                item["event_ts"] = int(b.get("close_time_ms"))
            if "last_price" in b:
                try:
                    item["last_price"] = float(b.get("last_price"))
                except Exception:
                    pass
            if "last_tick_ts" in b:
                try:
                    item["last_tick_ts"] = int(b.get("last_tick_ts"))
                except Exception:
                    pass
            out.append(item)
        return out

    def _redis_payload_to_bars(
        self, payload: dict[str, Any], symbol: str, tf_s: int
    ) -> list[dict[str, Any]]:
        bars: list[dict[str, Any]] = []
        complete = bool(payload.get("complete", True))
        source = str(payload.get("source", ""))
        raw_bars = payload.get("bars")
        if isinstance(raw_bars, list):
            for item in raw_bars:
                if not isinstance(item, dict):
                    continue
                bar = self._redis_payload_bar_to_canonical(item, symbol, tf_s, complete, source)
                if bar is not None:
                    bars.append(bar)
            return bars
        raw_bar = payload.get("bar")
        if isinstance(raw_bar, dict):
            bar = self._redis_payload_bar_to_canonical(raw_bar, symbol, tf_s, complete, source)
            if bar is not None:
                bars.append(bar)
        return bars

    def _redis_payload_bar_to_canonical(
        self,
        bar: dict[str, Any],
        symbol: str,
        tf_s: int,
        complete: bool,
        source: str,
    ) -> Optional[dict[str, Any]]:
        open_ms = bar.get("open_ms")
        close_ms = bar.get("close_ms")
        if not isinstance(open_ms, int) or not isinstance(close_ms, int):
            return None
        close_ms = int(open_ms + int(tf_s) * 1000)
        return {
            "symbol": symbol,
            "tf_s": int(tf_s),
            "open_time_ms": int(open_ms),
            "close_time_ms": int(close_ms),
            "o": bar.get("o"),
            "h": bar.get("h"),
            "low": bar.get("l"),
            "c": bar.get("c"),
            "v": bar.get("v"),
            "complete": bool(complete),
            "src": str(source),
            "event_ts": int(close_ms) if complete else None,
        }

def _load_cfg(config_path: str) -> dict[str, Any]:
    try:
        with open(config_path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {}
        return data
    except Exception:
        return {}






def _bar_is_complete(bar: dict[str, Any]) -> bool:
    val = bar.get("complete")
    return bool(val) if isinstance(val, bool) else bool(val)


def _bar_is_final_source(bar: dict[str, Any]) -> bool:
    src = bar.get("src")
    return isinstance(src, str) and src in FINAL_SOURCES


def _choose_better_bar(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    existing_complete = _bar_is_complete(existing)
    incoming_complete = _bar_is_complete(incoming)
    if incoming_complete and not existing_complete:
        return incoming
    if existing_complete and not incoming_complete:
        return existing
    existing_final = _bar_is_final_source(existing)
    incoming_final = _bar_is_final_source(incoming)
    if incoming_final and not existing_final:
        return incoming
    if existing_final and not incoming_final:
        return existing
    return existing


def _get_open_ms(bar: dict[str, Any]) -> Optional[int]:
    value = bar.get("open_time_ms")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except Exception:
            return None
    return None


def _ensure_sorted_dedup(
    bars: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], Optional[dict[str, Any]]]:
    if len(bars) <= 1:
        return bars, None

    prev_open: Optional[int] = None
    for bar in bars:
        open_ms = _get_open_ms(bar)
        if open_ms is None:
            continue
        if prev_open is not None and open_ms <= prev_open:
            break
        prev_open = open_ms
    else:
        return bars, None

    sorted_bars = sorted(bars, key=lambda x: _get_open_ms(x) or 0)
    deduped: dict[int, dict[str, Any]] = {}
    dropped = 0
    for bar in sorted_bars:
        open_ms = _get_open_ms(bar)
        if open_ms is None:
            continue
        existing = deduped.get(open_ms)
        if existing is None:
            deduped[open_ms] = bar
            continue
        deduped[open_ms] = _choose_better_bar(existing, bar)
        dropped += 1

    result = [deduped[k] for k in sorted(deduped.keys())]
    geom = {"sorted": True, "dedup_dropped": dropped}
    return result, geom


def _mark_geom_fix(
    meta: dict[str, Any],
    warnings: list[str],
    geom: dict[str, Any],
    *,
    source: str,
    tf_s: int,
) -> None:
    if "geom_non_monotonic" not in warnings:
        warnings.append("geom_non_monotonic")
    extensions = meta.get("extensions")
    if not isinstance(extensions, dict):
        extensions = {}
        meta["extensions"] = extensions
    extensions["geom_fix"] = geom
    extensions["degraded"] = ["geom_non_monotonic"]
    _log_geom_fix(source, tf_s, geom)


def _log_geom_fix(source: str, tf_s: int, geom: dict[str, Any]) -> None:
    global _geom_fix_count, _geom_fix_last_log_ts, _geom_fix_last_example
    _OBS.inc_uds_geom_fix(source, tf_s)
    _geom_fix_count += 1
    _geom_fix_last_example = (source, tf_s, geom.get("sorted"), geom.get("dedup_dropped"))
    now_mono = time.monotonic()
    if now_mono - _geom_fix_last_log_ts >= 30.0:
        if _geom_fix_count > 0 and _geom_fix_last_example:
            src, tf, srt, dedup = _geom_fix_last_example
            Logging.warning(
                "UDS: виправлено геометрію (throttled 30s) total=%s example source=%s tf_s=%s sorted=%s dedup_dropped=%s",
                _geom_fix_count,
                src,
                tf,
                srt,
                dedup,
            )
        _geom_fix_count = 0
        _geom_fix_last_log_ts = now_mono


def _log_window_result(result: WindowResult, warnings: list[str], tf_s: int) -> None:
    meta = result.meta or {}
    source = meta.get("source") if isinstance(meta, dict) else None
    if isinstance(meta, dict) and isinstance(meta.get("redis_hit"), bool):
        _OBS.observe_redis_hit(tf_s, bool(meta.get("redis_hit")))
    count = len(result.bars_lwc)
    if Logging.isEnabledFor(logging.DEBUG):
        Logging.debug(
            "UDS: вікно готове source=%s count=%s warnings=%s",
            source,
            count,
            "|".join(warnings) if warnings else "-",
        )


def _log_updates_result(result: UpdatesResult) -> None:
    if not Logging.isEnabledFor(logging.INFO):
        return
    events_len = len(result.events)
    cursor_seq = result.cursor_seq
    warnings_key = "|".join(result.warnings) if result.warnings else "-"

    # rate-limit identical logs: не спамити, якщо попередній лог не змінився
    lock = getattr(_log_updates_result, "_lock", None)
    if lock is None:
        lock = threading.Lock()
        setattr(_log_updates_result, "_lock", lock)

    with lock:
        prev = getattr(_log_updates_result, "_last_state", None)
        curr = (events_len, cursor_seq, warnings_key)
        if prev == curr:
            return
        setattr(_log_updates_result, "_last_state", curr)

    Logging.debug(
        "UDS: updates готові events=%s cursor_seq=%s warnings=%s",
        events_len,
        cursor_seq,
        warnings_key,
    )


def _redis_layer_from_cfg(cfg: dict[str, Any]) -> Optional[RedisLayer]:
    if redis_lib is None:
        return None
    spec = resolve_redis_spec(cfg, role="read_layer", log=False)
    if spec is None:
        return None
    client = redis_lib.Redis(
        host=spec.host,
        port=spec.port,
        db=spec.db,
        socket_timeout=REDIS_SOCKET_TIMEOUT_S,
        socket_connect_timeout=REDIS_SOCKET_TIMEOUT_S,
        decode_responses=False,
    )
    return RedisLayer(client, spec.namespace)


class _RedisUpdatesBus:
    def __init__(self, client: Any, ns: str, retain: int) -> None:
        self._client = client
        self._ns = ns
        self._retain = max(1, int(retain))

    def _key(self, *parts: str) -> str:
        return ":".join([self._ns, *parts])

    def publish(self, event: dict[str, Any]) -> Optional[int]:
        sym = str(event["key"]["symbol"]).replace("_", "/")
        seq_key = self._key("updates", "seq", sym, str(event["key"]["tf_s"]))
        list_key = self._key("updates", "list", sym, str(event["key"]["tf_s"]))
        seq = int(self._client.incr(seq_key))
        event = dict(event)
        event["seq"] = seq
        payload = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
        self._client.rpush(list_key, payload)
        self._client.ltrim(list_key, -self._retain, -1)
        return seq

    def read_updates(
        self,
        symbol: str,
        tf_s: int,
        since_seq: Optional[int],
        limit: int,
    ) -> tuple[list[dict[str, Any]], int, Optional[dict[str, Any]], Optional[str]]:
        try:
            sym = str(symbol).replace("_", "/")
            seq_key = self._key("updates", "seq", sym, str(tf_s))
            list_key = self._key("updates", "list", sym, str(tf_s))
            raw_list = self._client.lrange(list_key, -self._retain, -1)
            events: list[dict[str, Any]] = []
            min_seq: Optional[int] = None
            max_seq: Optional[int] = None
            for raw in raw_list or []:
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8")
                try:
                    ev = json.loads(raw)
                except Exception:
                    continue
                seq = ev.get("seq")
                if not isinstance(seq, int):
                    continue
                if min_seq is None or seq < min_seq:
                    min_seq = seq
                if max_seq is None or seq > max_seq:
                    max_seq = seq
                if since_seq is not None and seq <= since_seq:
                    continue
                events.append(ev)

            if limit > 0 and len(events) > limit:
                events = events[-limit:]

            cursor_seq = since_seq if since_seq is not None else 0
            if events:
                cursor_seq = max(ev.get("seq", 0) for ev in events)
            else:
                last_seq_raw = self._client.get(seq_key)
                if isinstance(last_seq_raw, bytes):
                    last_seq_raw = last_seq_raw.decode("utf-8")
                try:
                    last_seq = int(last_seq_raw) if last_seq_raw is not None else 0
                except Exception:
                    last_seq = 0
                if since_seq is None:
                    cursor_seq = last_seq

            gap: Optional[dict[str, Any]] = None
            if since_seq is not None and min_seq is not None and since_seq < min_seq - 1:
                gap = {
                    "first_seq_available": min_seq,
                    "last_seq_available": max_seq if max_seq is not None else min_seq,
                }
            return events, int(cursor_seq), gap, None
        except Exception as exc:
            return [], since_seq if since_seq is not None else 0, None, str(exc)


def _updates_bus_from_cfg(cfg: dict[str, Any]) -> Optional[_RedisUpdatesBus]:
    if redis_lib is None:
        return None
    spec = resolve_redis_spec(cfg, role="updates_bus", log=False)
    if spec is None:
        return None
    updates_cfg = cfg.get("updates")
    retain = UPDATES_REDIS_RETAIN_DEFAULT
    if isinstance(updates_cfg, dict):
        try:
            retain = int(updates_cfg.get("retain", retain))
        except Exception:
            retain = UPDATES_REDIS_RETAIN_DEFAULT
    client = redis_lib.Redis(
        host=spec.host,
        port=spec.port,
        db=spec.db,
        socket_timeout=REDIS_SOCKET_TIMEOUT_S,
        socket_connect_timeout=REDIS_SOCKET_TIMEOUT_S,
        decode_responses=False,
    )
    return _RedisUpdatesBus(client, spec.namespace, retain)


def _opt_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except Exception:
        return None


def build_uds_from_config(
    config_path: str,
    data_root: str,
    boot_id: str,
    *,
    role: str = "writer",
    writer_components: bool = False,
) -> UnifiedDataStore:
    global _REDIS_SPEC_BOOT_LOGGED
    cfg = _load_cfg(config_path)

    if not _REDIS_SPEC_BOOT_LOGGED:
        _REDIS_SPEC_BOOT_LOGGED = True
        spec_boot = resolve_redis_spec(cfg, role="boot", log=True)
        if spec_boot is None:
            Logging.info("REDIS_SPEC_EFF enabled=0")
        else:
            Logging.info(
                "REDIS_SPEC_EFF enabled=1 host=%s port=%s db=%s namespace=%s source=%s mismatch=%s mismatch_fields=%s",
                spec_boot.host,
                spec_boot.port,
                spec_boot.db,
                spec_boot.namespace,
                spec_boot.source,
                int(bool(spec_boot.mismatch)),
                ",".join(spec_boot.mismatch_fields) if spec_boot.mismatch_fields else "",
            )

    tf_allowlist = tf_allowlist_from_cfg(cfg)
    preview_tf_allowlist, preview_tf_allowlist_source = preview_tf_allowlist_from_cfg(cfg)
    min_coldload_bars = min_coldload_bars_from_cfg(cfg)
    redis_layer = _redis_layer_from_cfg(cfg)
    updates_bus = _updates_bus_from_cfg(cfg)
    spec_for_status = resolve_redis_spec(cfg, role="uds_status", log=False)
    redis_spec_mismatch = bool(spec_for_status.mismatch) if spec_for_status is not None else False
    redis_spec_mismatch_fields = (
        list(spec_for_status.mismatch_fields) if spec_for_status is not None else []
    )
    jsonl_appender = None
    redis_writer = None
    if writer_components:
        jsonl_appender = JsonlAppender(
            root=data_root,
            day_anchor_offset_s=int(cfg.get("day_anchor_offset_s", 0)),
            day_anchor_offset_s_d1=_opt_int(cfg.get("day_anchor_offset_s_d1")),
            day_anchor_offset_s_d1_alt=_opt_int(cfg.get("day_anchor_offset_s_d1_alt")),
            day_anchor_offset_s_alt=_opt_int(cfg.get("day_anchor_offset_s_alt")),
            day_anchor_offset_s_alt2=_opt_int(cfg.get("day_anchor_offset_s_alt2")),
            fsync=bool(cfg.get("ssot_jsonl_fsync", False)),
        )
        redis_writer = build_redis_snapshot_writer(config_path)
    # S6.5: RAM cache dynamic для обох ролей (writer і reader)
    # symbols × TFs ≈ 13×8 = 104 + запас; max_keys=8 hardcode → LRU thrashing
    n_symbols = len(cfg.get("symbols", []))
    if n_symbols < 1:
        n_symbols = 1
    n_tfs = len(tf_allowlist)
    if n_tfs < 1:
        n_tfs = 8
    ram_max_keys = max(n_symbols * n_tfs + 16, 128)
    ram_layer = RamLayer(max_keys=ram_max_keys, max_bars=60000)
    return UnifiedDataStore(
        data_root=data_root,
        boot_id=boot_id,
        tf_allowlist=tf_allowlist,
        min_coldload_bars=min_coldload_bars,
        role=role,
        ram_layer=ram_layer,
        redis_layer=redis_layer,
        jsonl_appender=jsonl_appender,
        redis_snapshot_writer=redis_writer,
        updates_bus=updates_bus,
        redis_spec_mismatch=redis_spec_mismatch,
        redis_spec_mismatch_fields=redis_spec_mismatch_fields,
        preview_tf_allowlist=preview_tf_allowlist,
        preview_tf_allowlist_source=preview_tf_allowlist_source,
        preview_curr_ttl_s=int(cfg.get("preview_curr_ttl_s", _DEFAULT_PREVIEW_CURR_TTL_S)),
    )
