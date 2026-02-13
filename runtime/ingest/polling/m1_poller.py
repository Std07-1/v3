"""M1 Poller — отримання фінальних M1 барів з FXCM + деривація M3.

Ізольований від M5 pipeline (engine_b). Працює як окремий процес.
Поллить M1 від FXCM History API щохвилини, коммітить через UDS,
будує M3 derived з 3×M1 (аналогічно M5→H1 у engine_b).

SSOT-1: M1/M3 (візуальність + точки входу).
Не впливає на SSOT-2 (M5+) та SSOT-3 (H4/D1).
"""
from __future__ import annotations

import logging
import time
import uuid
from typing import Any, Dict, List, Optional

from core.config_loader import pick_config_path, load_system_config
from core.model.bars import CandleBar, assert_invariants
from env_profile import load_env_secrets
from runtime.ingest.market_calendar import MarketCalendar
from runtime.ingest.tick_common import (
    symbols_from_cfg,
    calendar_from_group,
)
from runtime.store.uds import build_uds_from_config, UnifiedDataStore


def _utc_now_ms() -> int:
    return int(time.time() * 1000)


# ---------------------------------------------------------------------------
# M1Buffer — буфер закритих M1 для деривації M3
# ---------------------------------------------------------------------------
class M1Buffer:
    """Буфер закритих M1 барів для побудови M3 derived."""

    def __init__(self, max_keep: int = 500) -> None:
        self._max_keep = max_keep
        self._by_open_ms: Dict[int, CandleBar] = {}
        self._sorted_keys: List[int] = []

    def upsert(self, bar: CandleBar) -> None:
        """Додає M1 бар у буфер."""
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

    def has_full_m3(self, m3_open_ms: int) -> bool:
        """Чи є всі 3 M1 бари для M3 bucket."""
        step = 60_000
        for i in range(3):
            if (m3_open_ms + i * step) not in self._by_open_ms:
                return False
        return True

    def m3_bars(self, m3_open_ms: int) -> List[CandleBar]:
        """Повертає 3 M1 бари для M3 bucket або порожній list."""
        step = 60_000
        out: List[CandleBar] = []
        for i in range(3):
            b = self._by_open_ms.get(m3_open_ms + i * step)
            if b is None:
                return []
            out.append(b)
        return out


def _derive_m3(symbol: str, m1_buf: M1Buffer, m1_bar: CandleBar) -> Optional[CandleBar]:
    """Будує M3 бар якщо поточний M1 — останній у M3 bucket."""
    m3_ms = 180_000
    m3_open = (m1_bar.open_time_ms // m3_ms) * m3_ms
    m3_close = m3_open + m3_ms

    # M3 будується тільки коли прийшов останній M1 (третій)
    expected_last_m1 = m3_open + 2 * 60_000
    if m1_bar.open_time_ms != expected_last_m1:
        return None

    if not m1_buf.has_full_m3(m3_open):
        return None

    bars = m1_buf.m3_bars(m3_open)
    if not bars:
        return None

    # Фільтруємо calendar-pause flat бари
    trading = [b for b in bars if not b.extensions.get("calendar_pause_flat")]
    if not trading:
        return None

    extensions: dict[str, Any] = {}
    if len(trading) < len(bars):
        extensions["partial_calendar_pause"] = True
        extensions["calendar_pause_m1_count"] = len(bars) - len(trading)

    out = CandleBar(
        symbol=symbol,
        tf_s=180,
        open_time_ms=m3_open,
        close_time_ms=m3_close,
        o=trading[0].o,
        h=max(b.h for b in trading),
        low=min(b.low for b in trading),
        c=trading[-1].c,
        v=sum(b.v for b in trading),
        complete=True,
        src="derived",
        extensions=extensions,
    )
    assert_invariants(out)
    return out


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
_M1_MS = 60_000

# Flat bar: O==H==L==C з малим обсягом (calendar-pause маркер від брокера)
_FLAT_BAR_MAX_VOLUME = 1


def _is_flat(bar: CandleBar) -> bool:
    return bar.o == bar.h == bar.low == bar.c and bar.v <= _FLAT_BAR_MAX_VOLUME


def _expected_closed_m1_ms(now_ms: int) -> int:
    """Який M1 бар щойно закрився (open_ms останнього закритого)."""
    return (now_ms // _M1_MS) * _M1_MS - _M1_MS


def _last_trading_minute_ms(calendar: MarketCalendar, now_ms: int) -> int:
    """Пошук останньої торгової хвилини (до 7 днів назад)."""
    cur = (now_ms // _M1_MS) * _M1_MS - _M1_MS
    for _ in range(7 * 24 * 60):
        if calendar.is_trading_minute(cur):
            return cur
        cur -= _M1_MS
    return (now_ms // _M1_MS) * _M1_MS - _M1_MS


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
    ) -> None:
        self._symbol = symbol
        self._provider = provider
        self._uds = uds
        self._calendar = calendar
        self._tail_n = max(2, tail_fetch_n)
        self._m3_derive = m3_derive
        self._m1_buf = M1Buffer()

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
            # Flat під час торгових годин → пропускаємо (сміття)
            return False

        if not trading:
            if flat:
                # Flat під час паузи → приймаємо з маркером
                bar = CandleBar(
                    symbol=bar.symbol, tf_s=bar.tf_s,
                    open_time_ms=bar.open_time_ms,
                    close_time_ms=bar.close_time_ms,
                    o=bar.o, h=bar.h, low=bar.low, c=bar.c, v=bar.v,
                    complete=bar.complete, src=bar.src,
                    extensions={**bar.extensions, "calendar_pause_flat": True},
                )
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
            # M1Buffer для M3 деривації
            self._m1_buf.upsert(bar)
            # M3 деривація
            if self._m3_derive:
                m3 = _derive_m3(self._symbol, self._m1_buf, bar)
                if m3 is not None:
                    m3_result = self._uds.commit_final_bar(m3)
                    if m3_result.ok:
                        self._committed_m3 += 1
            return True
        elif result.reason not in ("stale", "duplicate"):
            logging.warning(
                "M1_COMMIT_REJECT symbol=%s reason=%s open_ms=%s",
                self._symbol, result.reason, bar.open_time_ms,
            )
        return False

    # -- Main poll -------------------------------------------------------

    def poll_once(self) -> None:
        """Один цикл: calendar check → smart fetch → ingest."""
        now_ms = _utc_now_ms()

        # Calendar gate
        if not self._check_calendar_state(now_ms):
            self._calendar_skips += 1
            return

        # Adaptive fetch count
        fetch_n = self._compute_fetch_n(now_ms)

        # Check if caught up (expected == watermark)
        expected = _expected_closed_m1_calendar(self._calendar, now_ms)
        if expected > 0 and self._watermark_ms is not None:
            if self._watermark_ms >= expected:
                self._already_caught_up += 1
                return

        # Єдиний шлях: history M1 → фільтр закритих → sort → commit у UDS.
        try:
            bars = self._provider.fetch_last_n_m1(self._symbol, n=fetch_n)
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
        # Потрібен asc порядок для watermark/commit (щоб не дропати старі як stale).
        if expected > 0:
            bars = [b for b in bars if b.open_time_ms <= expected]
        bars.sort(key=lambda b: b.open_time_ms)
        if not bars:
            return

        # Ingest кожен бар
        for bar in bars:
            self._ingest_bar(bar)

    # -- Warmup ----------------------------------------------------------

    def warmup_m1_buffer(self, tail_n: int = 10) -> int:
        """Заповнює M1Buffer з disk tail + встановлює watermark."""
        try:
            candles = self._uds.read_tail_candles(self._symbol, 60, tail_n)
            loaded = 0
            for bar in candles:
                if bar.tf_s == 60 and bar.complete:
                    self._m1_buf.upsert(bar)
                    # Watermark з диску
                    if self._watermark_ms is None or bar.open_time_ms > self._watermark_ms:
                        self._watermark_ms = bar.open_time_ms
                    loaded += 1
            return loaded
        except Exception as exc:
            logging.warning(
                "M1_WARMUP_ERROR symbol=%s err=%s", self._symbol, exc,
            )
            return 0

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
        }


# ---------------------------------------------------------------------------
# Multi-symbol runner
# ---------------------------------------------------------------------------
class M1PollerRunner:
    """Запускає M1 polling для всіх символів."""

    def __init__(
        self,
        pollers: List[M1SymbolPoller],
        provider: Any,
        uds: UnifiedDataStore,
        redis_tail_n: Dict[int, int],
        safety_delay_s: int = 8,
        log_interval_s: int = 300,
        reconnect_cooldown_s: int = 120,
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
        """Закрити FXCM сесію."""
        try:
            if self._connected:
                self._provider.__exit__(None, None, None)
                self._connected = False
        except Exception:
            pass

    # -- Bootstrap / warmup ---------------

    def _bootstrap_warmup(self) -> None:
        """Redis priming з диску + M1Buffer warmup для M3 деривації."""
        symbols = [p._symbol for p in self._pollers]  # noqa: SLF001
        # 1. Redis priming для M1/M3
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

        # 2. M1Buffer warmup — останні 10 M1 з диску для M3 деривації
        warmup_total = 0
        for p in self._pollers:
            loaded = p.warmup_m1_buffer(tail_n=10)
            warmup_total += loaded
        logging.info(
            "M1_POLLER_WARMUP symbols=%d m1_buffer_loaded=%d",
            len(self._pollers), warmup_total,
        )

    # -- Main loop -----------------------

    def run_forever(self) -> None:
        logging.info(
            "M1_POLLER_START symbols=%d safety_delay_s=%d",
            len(self._pollers), self._safety_delay_s,
        )
        self._bootstrap_warmup()
        self._try_connect()
        self._maybe_log_stats(force=True)  # Початкові stats (watermarks після warmup)
        while True:
            self._sleep_to_next_minute()
            cycle_errors = 0
            for p in self._pollers:
                err_before = p.stats["errors"]
                p.poll_once()
                if p.stats["errors"] > err_before:
                    cycle_errors += 1
            self._maybe_log_stats()
            self._maybe_reconnect(cycle_errors)

    def _sleep_to_next_minute(self) -> None:
        now = time.time()
        next_min = (int(now // 60) + 1) * 60
        target = next_min + self._safety_delay_s
        delay = max(0.0, target - now)
        time.sleep(delay)

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
        logging.info(
            "M1_POLLER_STATS symbols=%d m1=%d m3=%d err=%d cal_skip=%d gaps=%d caught_up=%d",
            len(self._pollers), total_m1, total_m3, total_err,
            total_cal_skip, total_gaps, total_caught,
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
        ))

    # Redis tail_n для priming M1/M3
    redis_cfg = cfg.get("redis", {})
    tail_n_raw = redis_cfg.get("tail_n_by_tf_s", {})
    redis_tail_n: Dict[int, int] = {}
    for tf_s in (60, 180):
        val = tail_n_raw.get(str(tf_s), 0)
        if int(val) > 0:
            redis_tail_n[tf_s] = int(val)

    return M1PollerRunner(
        pollers=pollers,
        provider=provider,
        uds=uds,
        redis_tail_n=redis_tail_n,
        safety_delay_s=safety_delay_s,
    )


# ---------------------------------------------------------------------------
# Entrypoint  (python -m runtime.ingest.polling.m1_poller)
# ---------------------------------------------------------------------------
def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )
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

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
