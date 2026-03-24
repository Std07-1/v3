from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

from env_profile import load_env_secrets
from core.config_loader import pick_config_path, load_system_config
from core.buckets import bucket_start_ms as _bucket_start_ms, resolve_anchor_offset_ms
from runtime.ingest.market_calendar import MarketCalendar
from runtime.ingest.tick_agg import TickAggregator
from runtime.ingest.tick_common import (
    pick_tick_channel,
    symbols_from_cfg,
    build_symbol_aliases,
    to_ms,
    calendar_from_group,
)
from core.model.bars import CandleBar
from runtime.store.redis_spec import resolve_redis_spec
from runtime.store.uds import build_uds_from_config


# ---------------------------------------------------------------------------
# M1→M3 деривація: 3m будується з 1m (не окремо з тиків)
# ---------------------------------------------------------------------------
class _M1toM3Buffer:
    """Деривація M3 preview бару з накопичених M1 preview барів."""

    def __init__(self):
        # type: () -> None
        self._completed = {}  # type: Dict[str, list]  # symbol -> list[CandleBar]
        self._current = {}  # type: Dict[str, CandleBar]  # symbol -> CandleBar

    def update(self, symbol, m1_bar):
        # type: (str, CandleBar) -> Optional[CandleBar]
        """Оновити з M1 баром. Повертає M3 CandleBar або None."""
        prev = self._current.get(symbol)
        if prev is not None and prev.open_time_ms != m1_bar.open_time_ms:
            # M1 rollover — зберігаємо завершений M1
            if symbol not in self._completed:
                self._completed[symbol] = []
            self._completed[symbol].append(prev)
            self._completed[symbol] = self._completed[symbol][-6:]
        self._current[symbol] = m1_bar

        m3_ms = 180_000
        m3_open = (m1_bar.open_time_ms // m3_ms) * m3_ms
        all_bars = []  # type: list
        for b in self._completed.get(symbol, []):
            if m3_open <= b.open_time_ms < m3_open + m3_ms:
                all_bars.append(b)
        if m3_open <= m1_bar.open_time_ms < m3_open + m3_ms:
            all_bars.append(m1_bar)
        if not all_bars:
            return None
        return CandleBar(
            symbol=symbol,
            tf_s=180,
            open_time_ms=m3_open,
            close_time_ms=m3_open + m3_ms,
            o=all_bars[0].o,
            h=max(b.h for b in all_bars),
            low=min(b.low for b in all_bars),
            c=all_bars[-1].c,
            v=0.0,
            complete=False,
            src="derived_m1",
            extensions={"m1_count": len(all_bars)},
        )


# ---------------------------------------------------------------------------
# HTF (H4/D1) інкрементальна деривація з M1 preview барів
# ---------------------------------------------------------------------------
class _RunningBar:
    """Інкрементальний OHLCV акумулятор для одного бакету."""

    __slots__ = ("bucket_open_ms", "tf_s", "o", "h", "low", "c", "v", "count")

    def __init__(self, bucket_open_ms: int, tf_s: int, first_bar):
        self.bucket_open_ms = bucket_open_ms
        self.tf_s = tf_s
        self.o = first_bar.o
        self.h = first_bar.h
        self.low = first_bar.low
        self.c = first_bar.c
        self.v = first_bar.v
        self.count = 1

    def merge(self, bar):
        """O(1) інкрементальне злиття нового M1 бару (новий open_time_ms)."""
        if bar.h > self.h:
            self.h = bar.h
        if bar.low < self.low:
            self.low = bar.low
        self.c = bar.c
        self.v += bar.v
        self.count += 1

    def update_forming(self, bar):
        """Оновлення формуючого M1 (той самий open_time_ms). count/v не змінюються."""
        if bar.h > self.h:
            self.h = bar.h
        if bar.low < self.low:
            self.low = bar.low
        self.c = bar.c

    def to_candle(self, symbol: str) -> CandleBar:
        return CandleBar(
            symbol=symbol,
            tf_s=self.tf_s,
            open_time_ms=self.bucket_open_ms,
            close_time_ms=self.bucket_open_ms + self.tf_s * 1000,
            o=self.o,
            h=self.h,
            low=self.low,
            c=self.c,
            v=self.v,
            complete=False,
            src="htf_preview",
            extensions={"m1_count": self.count},
        )


class _HTFRunningAccumulator:
    """Інкрементальна деривація HTF (H4, D1) preview з M1 барів.

    O(1) per update: running OHLCV state per (symbol, tf_s).
    Dedup: відстежує last_m1_open_ms per (symbol, tf_s).
    Tick-update (same open_time_ms) → update_forming (лише c/h/low).
    Новий M1 bar → full merge (count + v).
    seed() використовує той самий update() — єдиний код path.
    """

    def __init__(self, target_tfs_s: list, anchor_offsets_ms: dict):
        self._target_tfs_s = list(target_tfs_s)
        self._anchor_offsets_ms = dict(anchor_offsets_ms)
        self._running: Dict[str, Dict[int, _RunningBar]] = {}
        self._last_m1_open: Dict[tuple, int] = {}

    def seed(self, symbol: str, m1_finals: list):
        """Ініціалізація з M1 фіналів (послідовний update — однаковий код path)."""
        for bar in m1_finals:
            self.update(symbol, bar)

    def update(self, symbol: str, m1_bar) -> list:
        """Оновлення з M1 баром. Повертає list[CandleBar] HTF previews.

        O(1) per call per target TF. Dedup по M1 open_time_ms.
        """
        sym_state = self._running.setdefault(symbol, {})
        results = []

        for tf_s in self._target_tfs_s:
            tf_ms = tf_s * 1000
            anchor_ms = self._anchor_offsets_ms.get(tf_s, 0)
            bucket_open = _bucket_start_ms(m1_bar.open_time_ms, tf_ms, anchor_ms)

            running = sym_state.get(tf_s)

            if running is None or running.bucket_open_ms != bucket_open:
                # Новий бакет — reset
                sym_state[tf_s] = _RunningBar(bucket_open, tf_s, m1_bar)
                self._last_m1_open[(symbol, tf_s)] = m1_bar.open_time_ms
            else:
                # Той самий бакет — dedup по M1 open_time_ms
                last_m1 = self._last_m1_open.get((symbol, tf_s), -1)
                if m1_bar.open_time_ms != last_m1:
                    # Новий M1 бар — full merge
                    running.merge(m1_bar)
                    self._last_m1_open[(symbol, tf_s)] = m1_bar.open_time_ms
                else:
                    # Той самий M1 бар (tick update) — лише c/h/low
                    running.update_forming(m1_bar)

            results.append(sym_state[tf_s].to_candle(symbol))

        return results


try:
    import redis as redis_lib  # type: ignore
except Exception:
    redis_lib = None  # type: ignore


@dataclass(frozen=True)
class PreviewConfig:
    enabled: bool
    tfs: list[int]
    publish_min_interval_ms: int
    curr_ttl_s: Optional[int]
    symbols: list[str]
    channel: str


def _setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )


def _parse_preview_cfg(cfg: dict[str, Any]) -> PreviewConfig:
    enabled = bool(cfg.get("preview_tick_enabled", False))
    raw_tfs = cfg.get("preview_tick_tfs_s", [60, 180])
    raw_curr_ttl_s = cfg.get("preview_curr_ttl_s")
    tfs: list[int] = []
    if isinstance(raw_tfs, list):
        for item in raw_tfs:
            try:
                tf_s = int(item)
            except Exception:
                logging.debug(
                    "PREVIEW_CFG_TF_PARSE_FAILED item=%r", item, exc_info=True
                )
                continue
            if tf_s > 0:
                tfs.append(tf_s)
    publish_min_interval_ms = int(cfg.get("preview_tick_publish_min_interval_ms", 250))
    curr_ttl_s = int(raw_curr_ttl_s) if raw_curr_ttl_s is not None else None
    symbols_raw = cfg.get("preview_tick_symbols")
    symbols: list[str] = []
    if isinstance(symbols_raw, list):
        symbols = [str(x) for x in symbols_raw if str(x).strip()]
    channel = pick_tick_channel(cfg)
    return PreviewConfig(
        enabled=enabled,
        tfs=tfs,
        publish_min_interval_ms=publish_min_interval_ms,
        curr_ttl_s=curr_ttl_s,
        symbols=symbols,
        channel=channel or "",
    )


def _pick_price(payload: dict[str, Any]) -> Optional[float]:
    mid = payload.get("mid")
    if mid is not None:
        try:
            return float(mid)
        except Exception:
            logging.debug("PICK_PRICE_MID_PARSE_FAILED mid=%r", mid, exc_info=True)
            return None
    bid = payload.get("bid")
    ask = payload.get("ask")
    if bid is None or ask is None:
        return None
    try:
        return (float(bid) + float(ask)) / 2.0
    except Exception:
        logging.debug(
            "PICK_PRICE_BID_ASK_PARSE_FAILED bid=%r ask=%r",
            bid,
            ask,
            exc_info=True,
        )
        return None


class TickPreviewWorker:
    # tick_v1 required fields (from core/contracts/public/marketdata_v1/tick_v1.json)
    _TICK_REQUIRED = ("v", "symbol", "tick_ts_ms", "src", "seq")
    _TICK_ALLOWED = {"v", "symbol", "bid", "ask", "mid", "tick_ts_ms", "src", "seq"}
    # "0 ticks loud" — поріг тиші (секунди)
    _ZERO_TICKS_WARN_INTERVAL_S = 120

    def __init__(
        self,
        *,
        uds: Any,
        tfs: list[int],
        publish_min_interval_ms: int,
        curr_ttl_s: Optional[int],
        symbols: list[str],
        channel: str,
        calendars: Dict[str, MarketCalendar] | None = None,
        auto_promote_m1: bool = False,
        anchor_offset_ms: int = 0,
        htf_preview_tfs: list[int] | None = None,
        htf_anchor_offsets_ms: Dict[int, int] | None = None,
    ) -> None:
        self._uds = uds
        self._tfs = [int(x) for x in tfs if int(x) > 0]
        self._publish_min_interval_ms = max(0, int(publish_min_interval_ms))
        self._curr_ttl_s: Optional[int] = (
            max(1, int(curr_ttl_s)) if curr_ttl_s is not None else None
        )
        self._channel = str(channel)
        self._auto_promote_m1 = bool(auto_promote_m1)
        # HTF accumulator: M1→H4/D1 preview (replaces tick-agg for these TFs)
        _htf_set = set(htf_preview_tfs or [])
        if _htf_set:
            self._htf_acc: Optional[_HTFRunningAccumulator] = _HTFRunningAccumulator(
                sorted(_htf_set),
                htf_anchor_offsets_ms or {},
            )
            # D-06 guard: exclude HTF from TickAgg to prevent dual-path publish
            self._tfs = [t for t in self._tfs if t not in _htf_set]
        else:
            self._htf_acc = None
        # M3 деривація: M1 агрегуємо з тиків, M3 — з M1
        self._derive_m3 = 180 in self._tfs and 60 in self._tfs
        agg_tfs = [t for t in self._tfs if not (t == 180 and self._derive_m3)]
        self._agg = TickAggregator(
            tf_allowlist=agg_tfs,
            source="preview_tick",
            auto_promote=self._auto_promote_m1,
            anchor_offset_ms=int(anchor_offset_ms),
        )
        self._m3_buffer = _M1toM3Buffer() if self._derive_m3 else None
        self._last_tick_ts_ms: Dict[str, int] = {}
        self._last_pub_ms: Dict[tuple[str, int], int] = {}
        self._last_open_ms: Dict[tuple[str, int], int] = {}
        # Forward-gap detection state
        self._gap_warn_last_ts: Dict[tuple[str, int], float] = {}
        base_symbols = symbols
        self._symbol_aliases = build_symbol_aliases(base_symbols)
        self._symbol_allowlist = set(base_symbols)
        self._calendars: Dict[str, MarketCalendar] = calendars or {}
        self._cal_drop_total: int = 0
        self._cal_drop_last_warn_ts: float = 0.0
        self._stats: Dict[str, int] = {}
        self._stats_last_emit_ts = 0.0
        self._last_drops_total: Optional[int] = None  # S2: перший emit без WARNING
        # "0 ticks loud" state
        self._last_tick_rx_ts = time.time()
        self._zero_ticks_warned = False
        # O3-sleep: idle mode (no WS viewers → slow publish)
        self._publish_min_interval_ms_orig = self._publish_min_interval_ms
        self._idle_mode = False
        self._idle_check_ts: float = 0.0
        self._redis_client: Any = None  # set in run_forever()
        self._redis_ns: str = ""  # set in run_forever()

    def _inc(self, key: str, val: int = 1) -> None:
        self._stats[key] = self._stats.get(key, 0) + int(val)

    def _maybe_emit_stats(self) -> None:
        now = time.time()
        if now - self._stats_last_emit_ts < 60:
            return
        self._stats_last_emit_ts = now
        # "0 ticks loud": якщо тиків не було > _ZERO_TICKS_WARN_INTERVAL_S
        silence_s = now - self._last_tick_rx_ts
        if silence_s > self._ZERO_TICKS_WARN_INTERVAL_S:
            if not self._zero_ticks_warned:
                logging.warning(
                    "TickPreview: 0 тиків вже %.0f с (канал=%s) — можливо ринок закритий або channel невірний",
                    silence_s,
                    self._channel,
                )
                self._zero_ticks_warned = True
        else:
            self._zero_ticks_warned = False
        if not self._stats:
            return
        payload: Dict[str, Any] = dict(self._stats)
        self._stats.clear()
        # S2: merge tick_agg stats + degraded-but-loud при зростанні drops
        agg_stats = self._agg.stats()
        payload["tick_agg_stats"] = agg_stats
        drops_total = (
            agg_stats.get("ticks_dropped_late_bucket", 0)
            + agg_stats.get("ticks_dropped_before_open", 0)
            + agg_stats.get("ticks_dropped_out_of_order", 0)
        )
        if self._last_drops_total is None:
            self._last_drops_total = drops_total
            delta = 0
        else:
            delta = drops_total - self._last_drops_total
            if delta < 0:
                delta = 0
            self._last_drops_total = drops_total
        if delta > 0:
            logging.warning(
                "TICK_AGG_DROPS drops=%d interval_s=60 details=%s",
                delta,
                json.dumps(agg_stats, ensure_ascii=False),
            )
        logging.info("TICK_PREVIEW_STATS %s", json.dumps(payload, ensure_ascii=False))

    def _normalize_symbol(self, raw: Any) -> Optional[str]:
        if raw is None:
            return None
        key = str(raw).strip()
        if not key:
            return None
        if self._symbol_aliases:
            canon = self._symbol_aliases.get(key)
            if canon is None:
                return None
            return canon
        return key

    def _validate_tick_schema(self, payload: dict[str, Any]) -> Optional[str]:
        """Процедурний guard за tick_v1.json (без зовнішньої залежності jsonschema)."""
        if not isinstance(payload, dict):
            return "not_dict"
        for field in self._TICK_REQUIRED:
            if field not in payload:
                return f"missing_{field}"
        if not isinstance(payload.get("v"), int):
            return "v_not_int"
        if not isinstance(payload.get("symbol"), str):
            return "symbol_not_str"
        if not isinstance(payload.get("tick_ts_ms"), (int, float)):
            return "tick_ts_ms_not_number"
        if not isinstance(payload.get("src"), str):
            return "src_not_str"
        if not isinstance(payload.get("seq"), int):
            return "seq_not_int"
        # additionalProperties=false
        extra = set(payload.keys()) - self._TICK_ALLOWED
        if extra:
            return f"extra_fields:{','.join(sorted(extra))}"
        return None

    def on_tick(self, payload: dict[str, Any]) -> None:
        self._inc("ticks_in_total")
        self._last_tick_rx_ts = time.time()
        self._zero_ticks_warned = False
        # Schema guard (tick_v1 contract)
        schema_err = self._validate_tick_schema(payload)
        if schema_err is not None:
            self._inc("ticks_dropped_schema")
            self._inc(f"ticks_schema_err:{schema_err}")
            return
        version = payload.get("v")
        if version is not None:
            try:
                version = int(version)
            except Exception:
                logging.debug(
                    "TICK_VERSION_PARSE_FAILED raw=%r", version, exc_info=True
                )
                version = -1
            if version != 1:
                self._inc("ticks_dropped_version")
                return
        symbol = self._normalize_symbol(payload.get("symbol"))
        if symbol is None:
            self._inc("ticks_dropped_symbol")
            return
        if self._symbol_allowlist and symbol not in self._symbol_allowlist:
            self._inc("ticks_dropped_symbol")
            return
        tick_ts_ms = to_ms(payload.get("tick_ts"))
        if tick_ts_ms is None:
            tick_ts_ms = to_ms(payload.get("tick_ts_ms"))
        if tick_ts_ms is None:
            tick_ts_ms = to_ms(payload.get("snap_ts"))
        if tick_ts_ms is None:
            self._inc("ticks_dropped_ts")
            return
        last_ts = self._last_tick_ts_ms.get(symbol)
        if last_ts is not None and tick_ts_ms < last_ts:
            self._inc("ticks_dropped_out_of_order")
            return
        self._last_tick_ts_ms[symbol] = tick_ts_ms
        price = _pick_price(payload)
        if price is None:
            self._inc("ticks_dropped_price")
            return

        # Calendar gate: drop ticks during calendar pause
        cal = self._calendars.get(symbol)
        if cal is not None:
            m1_open_ms = _bucket_start_ms(tick_ts_ms, 60_000, 0)
            if not cal.is_trading_minute(m1_open_ms):
                self._inc("ticks_dropped_calendar_closed")
                self._cal_drop_total += 1
                now_t = time.time()
                if now_t - self._cal_drop_last_warn_ts >= 60.0:
                    self._cal_drop_last_warn_ts = now_t
                    logging.warning(
                        "TICK_PREVIEW_CLOSED_DROP symbol=%s dropped_total=%d m1_open_ms=%d",
                        symbol,
                        self._cal_drop_total,
                        m1_open_ms,
                    )
                return

        for tf_s in self._tfs:
            # M3 деривація: пропускаємо M3 у TickAggregator, виводимо з M1
            if tf_s == 180 and self._derive_m3:
                continue
            promoted, bar = self._agg.update(symbol, tf_s, tick_ts_ms, price)

            # Auto-promote: публікуємо завершений бар попередньої хвилини
            if promoted is not None:
                self._publish_promoted(promoted, symbol, tf_s)

            if bar is None:
                continue
            self._publish_bar(bar, symbol, tf_s)

            # M1→M3 деривація: після кожного M1 оновлення будуємо M3
            if tf_s == 60 and self._m3_buffer is not None:
                m3_bar = self._m3_buffer.update(symbol, bar)
                if m3_bar is not None:
                    self._publish_bar(m3_bar, symbol, 180)

            # HTF preview derivation: M1 → H4/D1
            if tf_s == 60 and self._htf_acc is not None:
                htf_bars = self._htf_acc.update(symbol, bar)
                for htf_bar in htf_bars:
                    self._publish_bar(htf_bar, symbol, htf_bar.tf_s)
        self._maybe_emit_stats()

    def _seed_htf_from_uds(self):
        """Seed HTF акумулятора з M1 фіналів UDS. Викликається при старті."""
        if self._htf_acc is None:
            return
        for symbol in self._symbol_allowlist:
            try:
                m1_bars = self._uds.read_tail_candles(symbol, 60, 1500)
                m1_bars = [b for b in m1_bars if b.complete]
                m1_bars.sort(key=lambda b: b.open_time_ms)
                self._htf_acc.seed(symbol, m1_bars)
                logging.info("HTF_SEED symbol=%s m1_count=%d", symbol, len(m1_bars))
            except Exception as exc:
                logging.warning("HTF_SEED_FAIL symbol=%s err=%s", symbol, exc)

    def _publish_promoted(self, promoted, symbol, tf_s):
        # type: (CandleBar, str, int) -> None
        """Публікація promoted бару (tick→complete) через UDS."""
        try:
            ok = self._uds.publish_promoted_bar(promoted)
            if ok:
                self._inc("promoted_publish_total")
            else:
                self._inc("promoted_publish_rejected")
        except Exception as exc:
            logging.warning(
                "TickPreview: promoted publish err symbol=%s tf_s=%s err=%s",
                symbol,
                tf_s,
                exc,
            )
            self._inc("promoted_publish_errors")

    _IDLE_CHECK_INTERVAL_S = 10.0
    _IDLE_PUBLISH_INTERVAL_MS = 2000

    def _check_idle_mode(self) -> None:
        """O3-sleep: check viewer count from Redis every 10s, toggle idle mode."""
        if self._redis_client is None:
            return
        now = time.time()
        if now - self._idle_check_ts < self._IDLE_CHECK_INTERVAL_S:
            return
        self._idle_check_ts = now
        try:
            vk = f"{self._redis_ns}:ws:viewer_count"
            raw = self._redis_client.get(vk)
            viewers = int(raw) if raw is not None else 0
        except Exception:
            viewers = -1  # unknown → stay in current mode
        if viewers == -1:
            return
        if viewers == 0 and not self._idle_mode:
            self._idle_mode = True
            self._publish_min_interval_ms = self._IDLE_PUBLISH_INTERVAL_MS
            logging.info(
                "TICK_PREVIEW_MODE active→idle viewers=0 throttle_ms=%d",
                self._IDLE_PUBLISH_INTERVAL_MS,
            )
        elif viewers > 0 and self._idle_mode:
            self._idle_mode = False
            self._publish_min_interval_ms = self._publish_min_interval_ms_orig
            logging.info(
                "TICK_PREVIEW_MODE idle→active viewers=%d throttle_ms=%d",
                viewers,
                self._publish_min_interval_ms_orig,
            )

    def _publish_bar(self, bar, symbol, tf_s):
        # type: (CandleBar, str, int) -> None
        """Публікація preview бару з forward-gap detection та throttling."""
        self._check_idle_mode()
        key = (symbol, tf_s)
        last_open = self._last_open_ms.get(key)
        rollover = last_open is None or last_open != bar.open_time_ms
        now_ms = int(time.time() * 1000)

        # Forward-gap detection (degraded-but-loud)
        if last_open is not None and rollover:
            tf_ms = tf_s * 1000
            gap_bars = (bar.open_time_ms - last_open) // tf_ms
            if gap_bars > 1:
                is_closed = False
                cal = self._calendars.get(symbol)
                if cal is not None:
                    mid_ms = last_open + (bar.open_time_ms - last_open) // 2
                    mid_bucket = _bucket_start_ms(mid_ms, 60_000, 0)
                    is_closed = not cal.is_trading_minute(mid_bucket)
                now_t = time.time()
                gw_key = key
                last_gw = self._gap_warn_last_ts.get(gw_key, 0.0)
                if now_t - last_gw >= 60.0:
                    self._gap_warn_last_ts[gw_key] = now_t
                    level = logging.DEBUG if is_closed else logging.WARNING
                    logging.log(
                        level,
                        "PREVIEW_GAP symbol=%s tf_s=%s gap_bars=%d last_open=%d tick_open=%d market_closed=%s",
                        symbol,
                        tf_s,
                        gap_bars,
                        last_open,
                        bar.open_time_ms,
                        is_closed,
                    )
                self._inc("preview_gap_total")

        last_pub = self._last_pub_ms.get(key)
        allow_publish = rollover
        if not allow_publish and last_pub is not None:
            if now_ms - last_pub >= self._publish_min_interval_ms:
                allow_publish = True
        if not allow_publish:
            self._inc("preview_publish_throttled_total")
            return
        try:
            self._uds.publish_preview_bar(bar, ttl_s=self._curr_ttl_s)
            self._last_pub_ms[key] = now_ms
            self._last_open_ms[key] = bar.open_time_ms
            self._inc("preview_publish_total")
        except Exception as exc:
            logging.warning(
                "TickPreview: publish помилка symbol=%s tf_s=%s err=%s",
                symbol,
                tf_s,
                exc,
            )
            self._inc("preview_publish_errors_total")

    def run_forever(self, redis_client: Any, redis_ns: str = "v3_local") -> None:
        # O3-sleep: store redis for idle mode checks
        self._redis_client = redis_client
        self._redis_ns = redis_ns
        self._seed_htf_from_uds()
        while True:
            try:
                pubsub = redis_client.pubsub(ignore_subscribe_messages=True)
                pubsub.subscribe(self._channel)
                logging.info("TickPreview: підписка на канал %s", self._channel)
                for msg in pubsub.listen():
                    if not isinstance(msg, dict):
                        continue
                    data = msg.get("data")
                    if data is None:
                        continue
                    if isinstance(data, bytes):
                        raw = data.decode("utf-8", errors="ignore")
                    else:
                        raw = str(data)
                    try:
                        payload = json.loads(raw)
                    except Exception:
                        self._inc("ticks_dropped_json")
                        continue
                    if isinstance(payload, dict):
                        self.on_tick(payload)
            except Exception as exc:
                logging.warning("TickPreview: помилка pubsub err=%s", exc)
                time.sleep(1.0)


def main() -> int:
    _setup_logging()
    report = load_env_secrets()
    if report.loaded:
        logging.info(
            "ENV: secrets_loaded path=%s keys=%d", report.path, report.keys_count
        )
    else:
        logging.info("ENV: .env не завантажено")

    config_path = pick_config_path()
    try:
        cfg = load_system_config(config_path)
    except Exception as exc:
        logging.error("TickPreview: не вдалося прочитати config.json err=%s", exc)
        time.sleep(5.0)
        return 2

    preview_cfg = _parse_preview_cfg(cfg)
    if not preview_cfg.enabled:
        logging.warning(
            "TickPreview: preview_tick_enabled=false, воркер у режимі очікування"
        )
        while True:
            time.sleep(60.0)

    if not preview_cfg.channel:
        logging.error("TickPreview: tick-канал не заданий")
        while True:
            time.sleep(60.0)

    if redis_lib is None:
        logging.error("TickPreview: redis бібліотека недоступна")
        while True:
            time.sleep(60.0)

    spec = resolve_redis_spec(cfg, role="tick_preview")
    if spec is None:
        logging.error("TickPreview: Redis вимкнено у config")
        while True:
            time.sleep(60.0)

    data_root = str(cfg.get("data_root", "./data_v3"))
    boot_id = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    uds = build_uds_from_config(
        config_path,
        data_root,
        boot_id,
        role="writer",
        writer_components=False,
    )

    # --- Build per-symbol calendars ---
    calendars: Dict[str, MarketCalendar] = {}
    all_symbols = preview_cfg.symbols or symbols_from_cfg(cfg)
    cal_groups = cfg.get("market_calendar_symbol_groups", {})
    cal_by_group = cfg.get("market_calendar_by_group", {})
    if (
        isinstance(cal_groups, dict)
        and isinstance(cal_by_group, dict)
        and bool(cfg.get("calendar_gate_enabled", False))
    ):
        for sym in all_symbols:
            grp_name = cal_groups.get(sym)
            if not grp_name:
                continue
            grp_cfg = cal_by_group.get(grp_name)
            if not isinstance(grp_cfg, dict):
                continue
            cal = calendar_from_group(grp_cfg)
            if cal is not None:
                calendars[sym] = cal
        logging.info(
            "TickPreview: calendar gate для %d/%d символів",
            len(calendars),
            len(all_symbols),
        )

    auto_promote_m1 = bool(cfg.get("tick_auto_promote_m1", False))

    anchor_offset_s = int(cfg.get("day_anchor_offset_s", 0))

    # HTF preview: M1→H4/D1 via accumulator (SSOT anchors from resolve_anchor_offset_ms)
    htf_preview_tfs = [tf for tf in preview_cfg.tfs if tf >= 14400]
    htf_anchor_offsets_ms = {
        tf_s: resolve_anchor_offset_ms(tf_s, cfg) for tf_s in htf_preview_tfs
    }

    worker = TickPreviewWorker(
        uds=uds,
        tfs=preview_cfg.tfs,
        publish_min_interval_ms=preview_cfg.publish_min_interval_ms,
        curr_ttl_s=preview_cfg.curr_ttl_s,
        symbols=all_symbols,
        channel=preview_cfg.channel,
        calendars=calendars,
        auto_promote_m1=auto_promote_m1,
        anchor_offset_ms=anchor_offset_s * 1000,
        htf_preview_tfs=htf_preview_tfs,
        htf_anchor_offsets_ms=htf_anchor_offsets_ms,
    )
    logging.info(
        "TickPreview: tfs=%s derive_m3=%s auto_promote_m1=%s symbols=%d",
        preview_cfg.tfs,
        worker._derive_m3,
        auto_promote_m1,
        len(all_symbols),
    )

    client = redis_lib.Redis(
        host=spec.host,
        port=spec.port,
        db=spec.db,
        decode_responses=False,
        socket_timeout=None,
        socket_connect_timeout=1.0,
    )
    worker.run_forever(client, redis_ns=spec.namespace)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
