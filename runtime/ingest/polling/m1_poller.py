"""M1 Poller — отримання фінальних M1 барів з FXCM + каскадна деривація.

Працює як окремий процес (ADR-0023: єдиний ingest pipeline, broker_base_tfs_s=[]).
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
from typing import Any, Dict, List, Optional, Tuple

from core.config_loader import pick_config_path, load_system_config
from core.model.bars import CandleBar, ms_to_utc_dt
from env_profile import load_env_secrets
from runtime.ingest.derive_engine import DeriveEngine
from runtime.ingest.market_calendar import MarketCalendar
from runtime.ingest.m1_session_filter import (
    DEFAULT_PAUSE_POLICY,
    FLAT_BAR_MAX_VOLUME_DEFAULT,
    PausePolicy,
    VERDICT_PAUSE_EDGE_STALE_DROPPED,
    VERDICT_PAUSE_FLAT_DROPPED,
    VERDICT_PAUSE_NOISE_DROPPED,
    VERDICT_PAUSE_NONFLAT_ANOMALY,
    VERDICT_REOPEN_FLAT_DROPPED,
    classify_m1_by_calendar,
    is_flat_m1,
    resolve_flat_max_volume,
    resolve_pause_policy,
)
from runtime.ingest.m1_session_open import (
    BAR_CORRECT_AS_IS,
    DISABLED_POLICY,
    SessionOpenRebuildPolicy,
    is_first_bar_after_break,
    mark_open_provisional,
    rebuild_session_open_bar,
    resolve_session_open_rebuild_policy,
)
from runtime.ingest.broker import MAX_BARS_PER_FETCH
from runtime.ingest.polling.m1_drop_ledger import DroppedM1Ledger
from runtime.ingest.tick_common import (
    resolve_symbol_calendars,
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
# Історія «останні n до date_to» від брокера включає й свічку, що відкрилась о date_to (= cutoff + 1 M1, ще
# формується). Вона займає один із n слотів, тож без запасу найстарша потрібна хвилина випадає з відповіді:
# 22.09.2026 17:28 tail_catchup missing=13 → fetched=12, хвилина 17:15 втрачена на 5 символах (за watermark —
# live_recover її вже не бачить). Головний цикл цей запас має (`gap_bars + 1` у _compute_fetch_n).
_FORMING_SLOT = 1
# Чим закінчився добір гепа (_fetch_since_watermark)
GAP_REACHED = "reached"  # геп покрито до watermark: між watermark і cutoff брокер більше нічого не має
GAP_BEYOND_BUDGET = "beyond_budget"  # бюджет сирих барів вичерпано раніше — найстаріша частина гепа лишається діркою
GAP_BROKER_EMPTY = "broker_empty"  # брокер віддав порожньо (сесія мертва) — писати нічого не можна
_PAGE_ATTEMPTS = 3  # спроб на глибоку сторінку добору гепа (перша — одна спроба)
_PAGE_RETRY_PAUSE_S = 0.5

# Flat bar: O==H==L==C з малим обсягом (calendar-pause маркер від брокера)
# SSOT: config.json → flat_bar_max_volume. Дефолт 4 (як у конфігу).
_FLAT_BAR_MAX_VOLUME_DEFAULT = FLAT_BAR_MAX_VOLUME_DEFAULT
_flat_bar_max_volume: int = _FLAT_BAR_MAX_VOLUME_DEFAULT


def set_flat_bar_max_volume(v: int) -> None:
    """Встановити flat_bar_max_volume з config (SSOT)."""
    global _flat_bar_max_volume
    _flat_bar_max_volume = max(0, int(v))


def _is_flat(bar: CandleBar) -> bool:
    return is_flat_m1(bar, _flat_bar_max_volume)


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


def _expected_closed_m1_calendar(
    calendar: Optional[MarketCalendar], now_ms: int
) -> int:
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

    # Перша сторінка звичайного опитування (gap + 1, не більше); більший геп догортається сторінками назад
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
        live_recover_max_consecutive_empty: int = 5,
        live_recover_timeout_s: int = 600,
        stale_s: int = 720,
        session_open_policy: SessionOpenRebuildPolicy = DISABLED_POLICY,
        pause_policy: PausePolicy = DEFAULT_PAUSE_POLICY,
    ) -> None:
        self._symbol = symbol
        self._provider = provider
        self._uds = uds
        # SSOT: config.json → m1_session_filter (resolve_pause_policy у будівниках, ADR-0099)
        self._pause_policy = pause_policy
        self._dropped_ledger = DroppedM1Ledger(pause_policy)
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
        self._live_recover_max_consecutive_empty = max(
            1, live_recover_max_consecutive_empty
        )
        self._live_recover_timeout_s = max(60, live_recover_timeout_s)

        # P0.2: recover state
        self._recover_active: bool = False
        self._recover_start_ts: float = 0.0
        self._recover_last_fetch_ts: float = 0.0
        self._recover_total_fetched: int = 0
        self._recover_total_written: int = 0
        self._recover_last_log_ts: float = 0.0
        self._recover_gap_at_start: int = 0
        self._recover_consecutive_empty: int = 0
        # До цієї хвилини брокер нічого новішого за watermark не має (останній добір дійшов до watermark). Лише
        # для тригера recover: тонкі хвилини без барів не вмикають його щохвилини. Межа вибірки — завжди watermark.
        self._scanned_through_ms: int = 0

        # P0.3: stale detection (ADR-0002)
        self._stale_s = max(0, stale_s)
        self._last_new_bar_ts: float = (
            0.0  # time.time() коли останній новий M1 committed
        )
        self._stale_count: int = 0
        self._last_stale_log_ts: float = 0.0

        # Watermark — останній committed M1 open_ms
        self._watermark_ms: Optional[int] = None
        self._bars_on_disk: int = 0  # M1 bars знайдені на диску під час warmup

        # Counters
        self._committed_m1 = 0
        self._committed_m3 = 0
        self._errors = 0
        self._calendar_skips = 0
        self._pause_noise_dropped = 0
        self._pause_noise_alarms = 0
        self._pause_edge_stale_dropped = 0
        self._gaps_detected = 0
        self._already_caught_up = 0

        # Calendar state tracking
        self._last_market_open: Optional[bool] = None

        # ADR-0096 слайс E: перша хвилина після перерви — open з тікової історії брокера
        self._session_open_policy = session_open_policy

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
                "M1_CALENDAR_STATE symbol=%s state=%s",
                self._symbol,
                state_str,
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
                    self._symbol,
                    gap_bars,
                    self._watermark_ms,
                    expected,
                )
        # Fetch gap + 1 (щоб перекрити), але не більше ліміту
        return min(gap_bars + 1, self.MAX_FETCH_N)

    # -- Добір гепа від watermark (ADR-0002 §P0.1/P0.2 «from watermark+1») --

    def _fetch_since_watermark(
        self, cutoff_ms: int, first_n: int, budget: int
    ) -> Tuple[List[CandleBar], str, int]:
        """Бари (watermark, cutoff] за зростанням, причина завершення (GAP_*) і скільки сирих барів віддав брокер.

        Брокер віддає n останніх існуючих барів з open ≤ date_to — разом із тим, що відкрився о date_to (для
        date_to = cutoff + 1 хв він формується) — і не більше MAX_BARS_PER_FETCH за запит. Якщо найстарший бар
        відповіді ще новіший за watermark + 1 хв, між ними можуть бути бари: гортаємо назад від нього, як
        repair_m1_gaps. У просторі барів паузи без барів проходяться самі, а бари паузи (шум, застарілий край)
        доходять до класифікатора ADR-0099. Писати лише найновішу сторінку не можна — watermark перестрибнув би
        найстаріші хвилини гепа назавжди (рев'ю dfca437, 23.09.2026). Бюджет — унікальні бари гепа. Сторінку, що
        впала або прийшла порожньою, повторюємо; виняток останньої спроби — вгору.
        """
        watermark_ms = self._watermark_ms
        collected: Dict[int, CandleBar] = {}
        date_to_ms = cutoff_ms + _M1_MS
        n = min(max(2, first_n), MAX_BARS_PER_FETCH)
        fetched = 0
        attempts = 1  # перша сторінка без повторів: порожньо/таймаут тут = брокер лежить, таймаути не множимо
        while True:
            raw = self._fetch_page_with_retry(n, date_to_ms, attempts)
            if not raw:
                # Для живого символу історія до date_to є завжди: порожньо = брокер лежить; частковий добір не пишемо
                return [], GAP_BROKER_EMPTY, fetched
            fetched += len(raw)
            oldest_ms = min(b.open_time_ms for b in raw)
            for bar in raw:
                if bar.open_time_ms <= cutoff_ms and (
                    watermark_ms is None or bar.open_time_ms > watermark_ms
                ):
                    collected.setdefault(bar.open_time_ms, bar)
            if watermark_ms is None or oldest_ms <= watermark_ms + _M1_MS:
                reason = GAP_REACHED
                break
            if oldest_ms >= date_to_ms:
                # Старших барів у брокера немає (watermark старший за горизонт історії) — добирати нічого
                logging.warning(
                    "M1_GAP_HISTORY_HORIZON symbol=%s oldest=%s watermark=%s",
                    self._symbol,
                    ms_to_utc_dt(oldest_ms).isoformat(),
                    ms_to_utc_dt(watermark_ms).isoformat(),
                )
                reason = GAP_REACHED
                break
            if len(collected) >= budget:
                reason = GAP_BEYOND_BUDGET
                break
            date_to_ms = oldest_ms  # включно: найстарший отриманий бар повториться і відсіється
            n = MAX_BARS_PER_FETCH
            attempts = _PAGE_ATTEMPTS
        return sorted(collected.values(), key=lambda b: b.open_time_ms), reason, fetched

    def _fetch_page_with_retry(self, n: int, date_to_ms: int, attempts: int) -> List[CandleBar]:
        """Сторінка брокера до `attempts` спроб: разовий таймаут IPC посеред гортання не обнуляє весь добір."""
        for attempt in range(1, attempts + 1):
            try:
                raw = self._provider.fetch_last_n_m1(
                    self._symbol,
                    n=n,
                    date_to_utc=ms_to_utc_dt(date_to_ms),
                ) or []
            except Exception:
                if attempt == attempts:
                    raise
                raw = []
            if raw:
                return raw
            if attempt < attempts:
                time.sleep(_PAGE_RETRY_PAUSE_S)
        return []

    def _ingest_gap(self, bars: List[CandleBar], reason: str, cutoff_ms: int) -> Tuple[int, Optional[Tuple[int, int, int]]]:
        """Комітить найстаріші бари добору, не більше live_recover_max_bars_per_cycle за виклик.

        Решта гепа — наступним циклом від нового watermark (коміт ≈ 50 мс: великий геп інакше займав би цикл
        усіх символів хвилинами). Повертає (записано, дірка для звіту або None): дірка — лише коли бюджет
        вичерпано і найстаріша частина гепа недосяжна.
        """
        watermark_before = self._watermark_ms
        batch = bars[: self._live_recover_max_bars_per_cycle]
        written = sum(1 for bar in batch if self._ingest_bar(bar))
        capped = len(batch) < len(bars)
        if reason == GAP_REACHED and not capped:
            self._scanned_through_ms = max(self._scanned_through_ms, cutoff_ms)
        hole = None
        if reason == GAP_BEYOND_BUDGET and watermark_before is not None:
            hole = (batch[0].open_time_ms if batch else cutoff_ms + _M1_MS, watermark_before, cutoff_ms)
        return written, hole

    def _report_gap_beyond_budget(self, first_written_ms: int, watermark_before_ms: int, cutoff_ms: int) -> None:
        """Геп більший за бюджет добору: найстаріша частина — дірка. Джерело правди — цей WARN; gap_state один на
        процес і його перезаписують інші символи (ремонт — repair_m1_gaps / settle ADR-0098)."""
        hole_to_ms = first_written_ms - _M1_MS
        logging.warning(
            "M1_GAP_BEYOND_BUDGET symbol=%s hole_from=%s hole_to=%s — дірку добирає лише repair_m1_gaps / settle",
            self._symbol,
            ms_to_utc_dt(watermark_before_ms + _M1_MS).isoformat(),
            ms_to_utc_dt(hole_to_ms).isoformat(),
        )
        self._uds.set_gap_state(
            backlog_bars=max(0, int((hole_to_ms - watermark_before_ms) // _M1_MS)),
            gap_from_ms=watermark_before_ms + _M1_MS,
            gap_to_ms=hole_to_ms,
            policy="m1_gap_beyond_budget",
        )

    # -- Ingest bar (calendar-aware) ------------------------------------

    def _report_dropped_bar(self, bar: CandleBar, verdict: str) -> None:
        """WARN і лічильник відкинутого бару — рівно один раз на хвилину (ADR-0099 §3.4); плаский у паузі — мовчки.

        Кожен відкинутий бар іде в лог з OHLCV: при хибному календарі тут потечуть справжні хвилини з великим обсягом,
        і це має бути видно (тривога M1_PAUSE_NOISE_ALARM), а не тихо зникнути.
        """
        if verdict == VERDICT_PAUSE_FLAT_DROPPED or not self._dropped_ledger.first_drop(bar.open_time_ms):
            return
        if verdict == VERDICT_REOPEN_FLAT_DROPPED:
            logging.warning(
                "M1_REOPEN_FLAT_DROPPED symbol=%s open_ms=%s o=%.5f v=%.0f — заглушка брокера у хвилині "
                "перевідкриття (тіків ще немає), у SSOT не йде",
                self._symbol, bar.open_time_ms, bar.o, bar.v,
            )
        elif verdict == VERDICT_PAUSE_NOISE_DROPPED:
            self._pause_noise_dropped += 1
            logging.warning(
                "M1_PAUSE_NOISE_DROPPED symbol=%s open_ms=%s o=%.5f h=%.5f l=%.5f c=%.5f v=%.0f margin_min=%s "
                "dropped_total=%d — хвилина глибоко в паузі сесії, шум брокера у SSOT не йде",
                self._symbol, bar.open_time_ms, bar.o, bar.h, bar.low, bar.c, bar.v,
                self._pause_policy.noise_margin_min, self._pause_noise_dropped,
            )
            alarm = self._dropped_ledger.observe_noise(bar.open_time_ms, bar.v)
            if alarm is not None:
                self._pause_noise_alarms += 1
                logging.error(
                    "M1_PAUSE_NOISE_ALARM symbol=%s reason=%s open_ms=%s v=%.0f noise_in_window=%d window_min=%d "
                    "suppressed=%d — шум глибоко в паузі схожий на торгівлю: ймовірно, календар символу хибний "
                    "(сезон, група, свято), і справжні хвилини йдуть у відсів",
                    self._symbol, alarm.reason, bar.open_time_ms, bar.v, alarm.noise_in_window,
                    self._pause_policy.alarm_window_min, alarm.suppressed_since_last,
                )
        elif verdict == VERDICT_PAUSE_EDGE_STALE_DROPPED:
            self._pause_edge_stale_dropped += 1
            logging.warning(
                "M1_PAUSE_EDGE_STALE_DROPPED symbol=%s open_ms=%s o=%.5f h=%.5f l=%.5f c=%.5f v=%.0f max_v=%s "
                "dropped_total=%d — перша хвилина паузи після закриття з малим обсягом: застарілі тіки, у SSOT не йде",
                self._symbol, bar.open_time_ms, bar.o, bar.h, bar.low, bar.c, bar.v,
                self._pause_policy.edge_stale_max_volume, self._pause_edge_stale_dropped,
            )
        else:
            logging.warning(
                "M1_DROPPED symbol=%s open_ms=%s verdict=%s v=%.0f — бар не йде в SSOT за правилом сесії",
                self._symbol, bar.open_time_ms, verdict, bar.v,
            )

    def _ingest_bar(self, bar: CandleBar) -> bool:
        """Calendar-aware ingest: маркує flat бари під час паузи.

        Повертає True якщо бар committed.
        """
        if not isinstance(bar, CandleBar):
            return False
        if bar.tf_s != 60 or not bar.complete:
            return False
        # ADR-0096 слайс E: запечений open першої хвилини сесії — до правила M1→SSOT, яке бачить уже справжній бар
        bar = self._rebuild_session_open(bar)

        # Правило SSOT за календарем — спільне із засівом і ремонтом дірок (runtime/ingest/m1_session_filter.py)
        classified, verdict = classify_m1_by_calendar(
            bar, self._is_market_open, _flat_bar_max_volume, self._pause_policy
        )
        if classified is None:
            self._report_dropped_bar(bar, verdict)
            return False
        bar = classified
        if verdict == VERDICT_PAUSE_NONFLAT_ANOMALY:
            logging.warning(
                "M1_NONFLAT_IN_PAUSE symbol=%s open_ms=%s o=%.5f h=%.5f l=%.5f c=%.5f v=%.0f",
                self._symbol,
                bar.open_time_ms,
                bar.o,
                bar.h,
                bar.low,
                bar.c,
                bar.v,
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
                self._committed_m3 += sum(1 for b in committed if b.tf_s == 180)
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
                self._symbol,
                result.reason,
                bar.open_time_ms,
            )
        return False

    # -- Перша хвилина після перерви (ADR-0096 слайс E) -----------------

    def _rebuild_session_open(self, bar: CandleBar) -> CandleBar:
        """Перша M1 після перерви: запечені компоненти — з тікової історії брокера (ADR-0096 §3.4 E).

        Бар комітиться один раз, тож виправити можна лише тут, до коміту. Для бару «першого після перерви»
        (кілька на добу) завжди запитуються тіки хвилини: open поза їхнім діапазоном = запечений → перебудова;
        усередині — бар як є (INFO). Тіків не отримано — бар брокера з open_provisional і WARN.
        """
        policy = self._session_open_policy
        if not policy.enabled:
            return bar
        if self._watermark_ms is not None and bar.open_time_ms <= self._watermark_ms:
            return bar  # не новіший за watermark — UDS однаково відкине (stale/duplicate), тіки не потрібні
        if not self._is_market_open(bar.open_time_ms):
            # Бар паузи (пласка заглушка 22:00, суботній шум) далі відкине/позначить правило M1→SSOT; watermark не
            # рушить, тож без цієї перевірки кожен цикл (і live_recover) запитував би t1 і писав WARN по колу.
            return bar
        if is_flat_m1(bar, _flat_bar_max_volume):
            # Пласка заглушка брокера (O=H=L=C = ціна до перерви, мізерний v): перебудовувати нема з чого — тіки дали б
            # змішаний бар (справжній open + застарілий close і хвіст до нього). Бар брокера як є — його класифікує
            # правило M1→SSOT (рев'ю гілки шуму D-02, 21.09).
            return bar
        is_trading_fn = None
        if self._calendar is not None and self._calendar.enabled:
            is_trading_fn = self._calendar.is_trading_minute
        if not is_first_bar_after_break(bar.open_time_ms, self._watermark_ms, policy.gap_ms, is_trading_fn):
            return bar
        rebuilt, reason, ticks_fetched = self._rebuild_baked_components(bar, policy)
        if rebuilt is not None:
            logging.info(
                "FXCM_SESSION_OPEN_REBUILT symbol=%s open_ms=%s o=%.5f→%.5f h=%.5f→%.5f l=%.5f→%.5f c=%.5f "
                "v=%.0f ticks=%s",
                self._symbol, bar.open_time_ms, bar.o, rebuilt.o, bar.h, rebuilt.h, bar.low, rebuilt.low,
                rebuilt.c, rebuilt.v, ticks_fetched,
            )
            return rebuilt
        if reason in BAR_CORRECT_AS_IS:
            logging.info(
                "FXCM_SESSION_OPEN_OK symbol=%s open_ms=%s reason=%s o=%.5f ticks=%s — бар брокера як є",
                self._symbol, bar.open_time_ms, reason, bar.o, ticks_fetched,
            )
            return bar
        logging.warning(
            "FXCM_SESSION_OPEN_BAKED symbol=%s open_ms=%s reason=%s o=%.5f h=%.5f l=%.5f c=%.5f v=%.0f ticks=%s "
            "— open першої хвилини після перерви не перевірено тіками, бар іде з open_provisional",
            self._symbol, bar.open_time_ms, reason, bar.o, bar.h, bar.low, bar.c, bar.v, ticks_fetched,
        )
        return mark_open_provisional(bar)

    def _rebuild_baked_components(
        self, bar: CandleBar, policy: SessionOpenRebuildPolicy
    ) -> Tuple[Optional[CandleBar], str, Optional[int]]:
        """(перебудований бар або None, причина, скільки тіків віддав брокер або None, якщо не віддав)."""
        price_step = policy.price_step_by_symbol.get(self._symbol)
        if price_step is None:
            return None, "price_step_missing", None
        fetch_ticks = getattr(self._provider, "fetch_t1_bid_ticks", None)
        if fetch_ticks is None:
            return None, "provider_without_t1", None
        try:
            ticks = fetch_ticks(self._symbol, bar.open_time_ms, bar.close_time_ms)
        except Exception as exc:  # noqa: BLE001 — будь-яка відмова транспорту = «не доведено»
            # Гучно у виклику (WARN FXCM_SESSION_OPEN_BAKED reason=t1_error); тут — traceback для розбору
            logging.debug("M1_SESSION_OPEN_T1_EXCEPTION symbol=%s", self._symbol, exc_info=True)
            return None, "t1_error: %s" % exc, None
        if ticks is None:
            return None, "t1_unavailable", None
        rebuilt, reason = rebuild_session_open_bar(bar, ticks, price_step)
        return rebuilt, reason, len(ticks)

    # -- Main poll -------------------------------------------------------

    def poll_once(self) -> None:
        """Один цикл: calendar-aware cutoff → smart fetch → ingest.

        НЕ блокує poll при market-closed — покладається на calendar-aware
                expected + caught-up check. Останній бар перед паузою завжди фетчиться.
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

        if self._recover_active:
            # Recover уже добирає геп тим самим добором від watermark — друга вибірка в цьому циклі зайва
            self._live_recover_check()
            self._stale_check(now_ms)
            return

        # Єдиний шлях: history M1 від watermark до expected (сторінками назад, якщо геп не влазить у запит) → UDS.
        hole: Optional[Tuple[int, int, int]] = None
        try:
            bars, reason, _fetched = self._fetch_since_watermark(
                expected, fetch_n, max(self._live_recover_max_total_bars, fetch_n)
            )
        except Exception as exc:
            self._errors += 1
            if self._errors <= 3 or self._errors % 60 == 0:
                logging.warning(
                    "M1_POLL_FETCH_ERROR symbol=%s err=%s total_errors=%d",
                    self._symbol,
                    exc,
                    self._errors,
                )
        else:
            # Бари вже відфільтровані (watermark, expected] і відсортовані — watermark не перестрибує жодної хвилини
            _written, hole = self._ingest_gap(bars, reason, expected)

        # P0.2: live recover після звичайного poll
        self._live_recover_check()
        if hole is not None:
            # Після recover: його finish чистить gap_state
            self._report_gap_beyond_budget(*hole)

        # P0.3: stale detection
        self._stale_check(now_ms)

    # -- Live recover (P0.2: ADR-0002) ----------------------------------

    def _live_recover_check(self) -> None:
        """Перевірка і виконання live-recover після downtime/паузи.

        Якщо gap між watermark і expected > threshold — входить у
        режим recover з cooldown + budget. Кожен цикл перераховує
        вікно від поточного watermark до cutoff.

        Перевіряє gap між watermark і expected, при перевищенні — recover.
        """
        if self._live_recover_max_total_bars <= 0:
            return
        now_ms = _utc_now_ms()
        cutoff = _expected_closed_m1_calendar(self._calendar, now_ms)
        if cutoff <= 0:
            return
        if self._watermark_ms is None:
            return  # немає watermark — обробляє bootstrap/tail_catchup
        # Геп від останнього повного добору: тонкі хвилини, яких у брокера немає, recover щохвилини не вмикають
        gap_bars = int((cutoff - max(self._watermark_ms, self._scanned_through_ms)) // _M1_MS)

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
            self._recover_consecutive_empty = 0
            logging.warning(
                "M1_LIVE_RECOVER_START symbol=%s gap_bars=%d cutoff=%s wm=%s",
                self._symbol,
                gap_bars,
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

        # --- Вихід: бюджет вичерпано (записані бари сесії recover) ---
        if self._recover_total_written >= self._live_recover_max_total_bars:
            self._live_recover_finish("max_total_reached")
            return

        # --- Вихід: timeout (broker не повертає нових барів тривалий час) ---
        elapsed_s = time.time() - self._recover_start_ts
        if elapsed_s >= self._live_recover_timeout_s:
            self._live_recover_finish("timeout")
            return

        # --- Вихід: consecutive empty (broker starved — post-gap/holiday lag) ---
        if self._recover_consecutive_empty >= self._live_recover_max_consecutive_empty:
            self._live_recover_finish("broker_starved")
            return

        # --- Cooldown ---
        now_s = time.time()
        if now_s - self._recover_last_fetch_ts < self._live_recover_cooldown_s:
            return

        # --- Fetch: увесь геп від watermark (сторінками назад), перша сторінка — gap + слот формуючої ---
        n = min(gap_bars, self._live_recover_max_bars_per_cycle)
        try:
            bars, reason, fetched_count = self._fetch_since_watermark(
                cutoff, n + _FORMING_SLOT, self._live_recover_max_total_bars
            )
        except Exception as exc:
            self._errors += 1
            logging.warning(
                "M1_LIVE_RECOVER_FETCH_ERROR symbol=%s err=%s",
                self._symbol,
                exc,
            )
            self._recover_last_fetch_ts = now_s
            return

        self._recover_last_fetch_ts = now_s
        if reason == GAP_BROKER_EMPTY:
            self._recover_consecutive_empty += 1
        else:
            self._recover_consecutive_empty = 0
            self._recover_total_fetched += fetched_count

        written, hole = self._ingest_gap(bars, reason, cutoff)
        self._recover_total_written += written
        if hole is not None:
            self._live_recover_finish("beyond_budget")
            self._report_gap_beyond_budget(*hole)
            return
        if reason == GAP_REACHED and self._scanned_through_ms >= cutoff:
            self._live_recover_finish("caught_up")
            return

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
                self._symbol,
                remaining_gap,
                self._recover_total_fetched,
                self._recover_total_written,
                elapsed_s,
            )

    def _live_recover_finish(self, reason: str) -> None:
        elapsed_s = int(time.time() - self._recover_start_ts)
        logging.info(
            "M1_LIVE_RECOVER_DONE symbol=%s reason=%s gap_at_start=%d "
            "fetched=%d written=%d elapsed_s=%d",
            self._symbol,
            reason,
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

        Stale threshold з config (stale_s). Loud WARN + метрика.
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
                self._symbol,
                int(silence_s),
                self._stale_count,
                self._watermark_ms,
            )

    # -- Warmup ----------------------------------------------------------

    def warmup_watermark(self, tail_n: int = 10) -> int:
        """Встановлює watermark з disk tail (M1 final bars)."""
        try:
            candles = self._uds.read_tail_candles(self._symbol, 60, tail_n)
            loaded = 0
            for bar in candles:
                if bar.tf_s == 60 and bar.complete:
                    if (
                        self._watermark_ms is None
                        or bar.open_time_ms > self._watermark_ms
                    ):
                        self._watermark_ms = bar.open_time_ms
                    loaded += 1
            self._bars_on_disk = loaded  # зберегти для Phase 2.5 trigger
            return loaded
        except Exception as exc:
            logging.warning(
                "M1_WARMUP_ERROR symbol=%s err=%s",
                self._symbol,
                exc,
            )
            return 0

    # -- Initial backfill (ADR-0038) ------------------------------------

    def initial_backfill(self, max_bars: int) -> Dict[str, Any]:
        """Fetch initial M1 history для символу з недостатньою історією.

        Phase 2.5: викликається з _bootstrap_warmup() коли bars_on_disk < max_bars
        після warmup_watermark(). Ідемпотентний: після backfill re-warmup встановлює
        bars_on_disk = max_bars → надалі Phase 2.5 trigger не спрацьовує.

        Returns:
            dict з результатом: bars_expected, bars_fetched, bars_written, etc.
        """
        if self._bars_on_disk >= max_bars:
            return {"initial_backfill_skipped": "enough_bars_on_disk"}
        if max_bars <= 0:
            return {"initial_backfill_skipped": "disabled"}

        now_ms = _utc_now_ms()
        from_ms = now_ms - max_bars * _M1_MS
        to_ms = now_ms

        # Provider must support fetch_m1_range (ADR-0038)
        fetch_fn = getattr(self._provider, "fetch_m1_range", None)
        if fetch_fn is None:
            logging.warning(
                "INITIAL_BACKFILL_SKIP symbol=%s reason=provider_no_fetch_m1_range",
                self._symbol,
            )
            return {"initial_backfill_skipped": "provider_unsupported"}

        try:
            bars = fetch_fn(self._symbol, from_ms, to_ms, max_bars)
        except Exception as exc:
            logging.warning(
                "INITIAL_BACKFILL_FAILED symbol=%s reason=%s",
                self._symbol,
                exc,
            )
            return {
                "initial_backfill_failed": str(exc),
                "bars_expected": max_bars,
                "bars_fetched": 0,
                "bars_written": 0,
            }

        if not bars:
            logging.warning(
                "INITIAL_BACKFILL_EMPTY symbol=%s expected=%d",
                self._symbol,
                max_bars,
            )
            return {
                "bars_expected": max_bars,
                "bars_fetched": 0,
                "bars_written": 0,
            }

        bars.sort(key=lambda b: b.open_time_ms)
        written = 0
        for bar in bars:
            if self._ingest_bar(bar):
                written += 1

        if written < max_bars:
            logging.warning(
                "INITIAL_BACKFILL_PARTIAL symbol=%s expected=%d fetched=%d written=%d",
                self._symbol,
                max_bars,
                len(bars),
                written,
            )
        else:
            logging.info(
                "INITIAL_BACKFILL_OK symbol=%s fetched=%d written=%d",
                self._symbol,
                len(bars),
                written,
            )

        return {
            "bars_expected": max_bars,
            "bars_fetched": len(bars),
            "bars_written": written,
        }

    # -- Tail catchup (P0.1: ADR-0002) --------------------------------

    def tail_catchup(self) -> Dict[str, Any]:
        """Заповнює M1 від watermark до expected (bootstrap-time).

        Викликається з _bootstrap_warmup() ПЕРЕД main loop.
        Інваріант P0.1: m1_poller НЕ входить у poll loop поки tail catchup
        не завершився. Гарантує що UI бачить M1 без великих гепів.

        Фетчить M1 від watermark до cutoff (budget-limited).
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

        # Увесь геп від watermark: сторінками назад від cutoff, доки не дійдемо до watermark або бюджету
        try:
            bars, reason, _fetched = self._fetch_since_watermark(
                cutoff_ms, missing + _FORMING_SLOT, self._tail_catchup_max_bars
            )
        except Exception as exc:
            logging.warning(
                "M1_TAIL_CATCHUP_FETCH_ERROR symbol=%s err=%s",
                self._symbol,
                exc,
            )
            return {
                "tail_catchup_missing": missing,
                "tail_catchup_fetched": 0,
                "tail_catchup_error": str(exc),
            }
        if reason == GAP_BROKER_EMPTY:
            return {
                "tail_catchup_missing": missing,
                "tail_catchup_fetched": 0,
                "tail_catchup_error": "broker returned no bars",
            }

        # Не більше live_recover_max_bars_per_cycle за раз: решту гепа допишуть цикли poll/recover від watermark
        written, hole = self._ingest_gap(bars, reason, cutoff_ms)
        if hole is not None:
            self._report_gap_beyond_budget(*hole)
        elif self._scanned_through_ms >= cutoff_ms:
            self._uds.set_gap_state(
                backlog_bars=0,
                gap_from_ms=None,
                gap_to_ms=None,
                policy=None,
            )
        else:
            backlog = int((cutoff_ms - (self._watermark_ms or cutoff_ms)) // _M1_MS)
            logging.info(
                "M1_TAIL_CATCHUP_BACKLOG symbol=%s backlog_minutes=%d — допише основний цикл",
                self._symbol,
                backlog,
            )
            self._uds.set_gap_state(
                backlog_bars=backlog,
                gap_from_ms=(self._watermark_ms or cutoff_ms) + _M1_MS,
                gap_to_ms=cutoff_ms,
                policy="m1_tail_catchup_backlog",
            )

        logging.info(
            "M1_TAIL_CATCHUP symbol=%s missing=%d fetched=%d written=%d " "wm_after=%s",
            self._symbol,
            missing,
            len(bars),
            written,
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
            "pause_noise_dropped": self._pause_noise_dropped,
            "pause_noise_alarms": self._pause_noise_alarms,
            "pause_edge_stale_dropped": self._pause_edge_stale_dropped,
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
        60: 300,
        300: 20,
        900: 10,
        1800: 10,
        3600: 10,
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
        initial_backfill_m1_bars: int = 1440,
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
        self._derive_warmup_bars_by_tf = derive_warmup_bars_by_tf or dict(
            self._DEFAULT_DERIVE_WARMUP
        )
        self._cascade_catchup_m1_bars = max(0, cascade_catchup_m1_bars)
        self._initial_backfill_m1_bars = max(0, initial_backfill_m1_bars)
        # Graceful shutdown: stop_event дозволяє перервати sleep між циклами
        import threading as _threading

        self._stop_event = _threading.Event()

    # -- FXCM session lifecycle -----------

    def _try_connect(self) -> bool:
        """Спроба відкрити/перевідкрити FXCM сесію."""
        try:
            if getattr(self._provider, "_fx", None) is not None:
                try:
                    self._provider.__exit__(None, None, None)
                except Exception:
                    logging.debug("M1_POLLER_RECONNECT_CLEANUP_FAIL", exc_info=True)
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
            logging.debug("M1_POLLER_SHUTDOWN_CLEANUP_FAIL", exc_info=True)

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
                len(symbols),
                primed_total,
                ",".join(str(t) for t in sorted(self._redis_tail_n)),
            )
        except Exception as exc:
            logging.warning(
                "BOOTSTRAP_DEGRADED phase=redis_priming err=%s",
                exc,
            )
            bootstrap_degraded.append("redis_priming: %s" % exc)

        # 2. Watermark warmup — читаємо tail до initial_backfill_m1_bars для точного
        #    підрахунку bars_on_disk, який використовується у Phase 2.5 trigger
        try:
            warmup_total = 0
            for p in self._pollers:
                loaded = p.warmup_watermark(tail_n=self._initial_backfill_m1_bars)
                warmup_total += loaded
            logging.info(
                "M1_POLLER_WARMUP symbols=%d watermark_loaded=%d",
                len(self._pollers),
                warmup_total,
            )
        except Exception as exc:
            logging.warning(
                "BOOTSTRAP_DEGRADED phase=watermark_warmup err=%s",
                exc,
            )
            bootstrap_degraded.append("watermark_warmup: %s" % exc)

        # 2.5. Initial backfill for under-history symbols (ADR-0038)
        #      Trigger: bars_on_disk < initial_backfill_m1_bars після Phase 2.
        #      Covers: truly virgin symbols (0 bars) AND symbols bootstrapped
        #      with only a few bars by tick publisher before ingest worker start.
        #      Потрібна активна сесія провайдера для fetch.
        virgin_symbols = [
            p
            for p in self._pollers
            if p._bars_on_disk < self._initial_backfill_m1_bars  # noqa: SLF001
        ]
        if virgin_symbols:
            if self._try_connect():
                backfill_n = self._initial_backfill_m1_bars
                backfill_total = 0
                for p in virgin_symbols:
                    try:
                        result = p.initial_backfill(backfill_n)
                        written = result.get("bars_written", 0)
                        backfill_total += written
                        if result.get("initial_backfill_failed"):
                            bootstrap_degraded.append(
                                "initial_backfill:%s: %s"
                                % (
                                    p._symbol,
                                    result["initial_backfill_failed"],
                                )  # noqa: SLF001
                            )
                    except Exception as exc:
                        logging.warning(
                            "BOOTSTRAP_DEGRADED phase=initial_backfill "
                            "symbol=%s err=%s",
                            p._symbol,  # noqa: SLF001
                            exc,
                        )
                        bootstrap_degraded.append(
                            "initial_backfill:%s: %s" % (p._symbol, exc)  # noqa: SLF001
                        )
                logging.info(
                    "INITIAL_BACKFILL_DONE virgin_symbols=%d total_written=%d",
                    len(virgin_symbols),
                    backfill_total,
                )
                # Re-warmup watermark after backfill — tail_n=initial_backfill_m1_bars
                # щоб bars_on_disk після backfill точно відображав кількість барів
                for p in virgin_symbols:
                    p.warmup_watermark(tail_n=self._initial_backfill_m1_bars)
            else:
                logging.warning(
                    "INITIAL_BACKFILL_SKIP reason=no_provider_session "
                    "virgin_symbols=%d",
                    len(virgin_symbols),
                )
                bootstrap_degraded.append("initial_backfill: no_provider_session")

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
                                sym,
                                tf_s,
                                exc,
                            )
                    if all_bars:
                        engine_warmup += self._derive_engine.warmup_bars(all_bars)
                logging.info(
                    "DERIVE_ENGINE_WARMUP symbols=%d bars=%d",
                    len(self._pollers),
                    engine_warmup,
                )
            except Exception as exc:
                logging.warning(
                    "BOOTSTRAP_DEGRADED phase=derive_engine_warmup err=%s",
                    exc,
                )
                bootstrap_degraded.append("derive_engine_warmup: %s" % exc)

        # 2c. Cascade catchup: прогін warmup M1 через cascade → деривація відсутніх барів.
        #     Після buffer warmup (2b) буфери M5-H1 заповнені з диску.
        #     Cascade catchup пропускає M1 через on_bar → cascade → derive + commit.
        #     UDS watermark НЕ скидається — бари що вже на диску дропаються як
        #     stale/duplicate (dedup), нові бари > watermark комітяться нормально.
        #     Виявлено 2026-02-19: без цього кроку H4 не деривується після рестарту
        #     бо warmup_bars() лише буферизує, не каскадує.
        #     Виявлено 2026-03-15: reset_watermark(0) спричиняв перезапис усіх
        #     derived барів у JSONL при кожному рестарті (3x duplication).
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
                        len(self._pollers),
                        catchup_total,
                        catchup_derived,
                    )
            except Exception as exc:
                logging.warning(
                    "BOOTSTRAP_DEGRADED phase=cascade_catchup err=%s",
                    exc,
                )
                bootstrap_degraded.append("cascade_catchup: %s" % exc)

        # 3. Tail catchup — заповнення від watermark до expected_now
        #    Інваріант P0.1 (ADR-0002): ПЕРЕД main loop.
        if self._tail_catchup_enabled:
            try:
                self._do_tail_catchup()
            except Exception as exc:
                logging.warning(
                    "BOOTSTRAP_DEGRADED phase=tail_catchup err=%s",
                    exc,
                )
                bootstrap_degraded.append("tail_catchup: %s" % exc)

        if bootstrap_degraded:
            logging.warning(
                "M1_POLLER_BOOTSTRAP_DEGRADED phases=%s",
                bootstrap_degraded,
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
                    p.stats["symbol"],
                    result,
                )
        logging.info(
            "M1_POLLER_TAIL_CATCHUP symbols=%d total_written=%d",
            len(self._pollers),
            catchup_total,
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
                len(symbols),
                ",".join(str(t) for t in tfs),
            )
        except Exception as exc:
            logging.warning("PRIME_READY_SET_FAILED component=m1 err=%s", exc)

    # -- Main loop -----------------------

    def run_forever(self) -> None:
        logging.info(
            "M1_POLLER_START symbols=%d safety_delay_s=%d",
            len(self._pollers),
            self._safety_delay_s,
        )
        self._bootstrap_warmup()
        self._publish_prime_ready()
        self._try_connect()
        self._maybe_log_stats(force=True)  # Початкові stats (watermarks після warmup)
        overdue_interval_s = 60  # Перевірка overdue кожні 60с
        last_overdue_ts = 0.0
        prime_refresh_interval_s = 3600  # Оновлювати TTL prime:ready:m1 кожну годину
        last_prime_refresh_ts = time.time()
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
            # Periodic refresh prime:ready:m1 TTL (6h expiry, refresh кожну годину)
            if now_ts - last_prime_refresh_ts >= prime_refresh_interval_s:
                self._publish_prime_ready()
                last_prime_refresh_ts = now_ts
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
        total_pause_noise = sum(p.stats["pause_noise_dropped"] for p in self._pollers)
        total_noise_alarms = sum(p.stats["pause_noise_alarms"] for p in self._pollers)
        total_edge_stale = sum(p.stats["pause_edge_stale_dropped"] for p in self._pollers)
        total_gaps = sum(p.stats["gaps_detected"] for p in self._pollers)
        total_caught = sum(p.stats["caught_up_skips"] for p in self._pollers)
        recovering = sum(1 for p in self._pollers if p.stats.get("recover_active"))
        total_stale = sum(p.stats.get("stale_count", 0) for p in self._pollers)
        logging.info(
            "M1_POLLER_STATS symbols=%d m1=%d m3=%d err=%d cal_skip=%d pause_noise=%d edge_stale=%d noise_alarm=%d "
            "gaps=%d caught_up=%d recovering=%d stale=%d",
            len(self._pollers),
            total_m1,
            total_m3,
            total_err,
            total_cal_skip,
            total_pause_noise,
            total_edge_stale,
            total_noise_alarms,
            total_gaps,
            total_caught,
            recovering,
            total_stale,
        )


# ---------------------------------------------------------------------------
# Побудова з конфігу (composition)
# ---------------------------------------------------------------------------
def load_session_open_policy(cfg: dict, symbols: List[str]) -> SessionOpenRebuildPolicy:
    """Політика перебудови першої хвилини (ADR-0096 слайс E) для записувача M1 — стан завжди у лозі.

    Битий конфіг вимикає перебудову з ERROR (ingest не зупиняється); символ без кроку ціни — WARN на старті,
    його перші хвилини підуть з open_provisional.
    """
    try:
        policy = resolve_session_open_rebuild_policy(cfg)
    except (KeyError, TypeError, ValueError) as exc:
        logging.error("M1_SESSION_OPEN_REBUILD_CONFIG_INVALID err=%s — перебудову вимкнено", exc)
        return DISABLED_POLICY
    logging.info(
        "M1_SESSION_OPEN_REBUILD enabled=%s gap_ms=%d price_steps=%d",
        policy.enabled, policy.gap_ms, len(policy.price_step_by_symbol),
    )
    missing_step = [sym for sym in symbols if sym not in policy.price_step_by_symbol]
    if policy.enabled and missing_step:
        logging.warning(
            "M1_SESSION_OPEN_REBUILD_NO_PRICE_STEP symbols=%s — перші хвилини цих символів підуть з open_provisional",
            missing_step,
        )
    return policy


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

    # Exclude symbols owned by binance worker (ADR-0037)
    bn_cfg = cfg.get("binance", {})
    if isinstance(bn_cfg, dict) and bn_cfg.get("enabled", False):
        bn_symbols = set(bn_cfg.get("symbols", []))
        if bn_symbols:
            symbols = [s for s in symbols if s not in bn_symbols]
            if not symbols:
                logging.warning("M1_POLLER_NO_SYMBOLS (all claimed by binance)")
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
    lr_max_consecutive_empty = int(m1_cfg.get("live_recover_max_consecutive_empty", 5))
    lr_timeout = int(m1_cfg.get("live_recover_timeout_s", 600))
    stale_s = int(m1_cfg.get("stale_s", 720))
    logging.info(
        "M1_POLLER_CONFIG tail_catchup_max=%d lr_threshold=%d lr_max_cycle=%d "
        "lr_cooldown=%d lr_max_total=%d stale_s=%d",
        tail_catchup_max_bars,
        lr_threshold,
        lr_max_cycle,
        lr_cooldown,
        lr_max_total,
        stale_s,
    )

    # SSOT: flat_bar_max_volume з config.json (верхній рівень) — нормалізація спільна із засівом
    set_flat_bar_max_volume(resolve_flat_max_volume(cfg))
    if cfg.get("flat_bar_max_volume") is not None:
        logging.info(
            "M1_POLLER_FLAT_BAR_MAX_VOLUME=%d (from config)", _flat_bar_max_volume
        )
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
    # ADR-0054 P0.4: символ без валідного календаря не стартує (замість тихих 24/7)
    calendars, rejected = resolve_symbol_calendars(cfg, symbols, where="m1_poller")
    if rejected:
        symbols = [s for s in symbols if s not in set(rejected)]
    if not symbols:
        logging.error("M1_POLLER_NO_SYMBOLS — жоден символ не має календаря")
        return None

    session_open_policy = load_session_open_policy(cfg, symbols)
    pollers: List[M1SymbolPoller] = []
    for sym in symbols:
        cal = calendars[sym]

        pollers.append(
            M1SymbolPoller(
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
                live_recover_max_consecutive_empty=lr_max_consecutive_empty,
                live_recover_timeout_s=lr_timeout,
                stale_s=stale_s,
                session_open_policy=session_open_policy,
                pause_policy=resolve_pause_policy(cfg, sym),  # ADR-0099: правила паузи залежать від групи символу
            )
        )

    # -- DeriveEngine (ADR-0002 P2.3 + ADR-0023 D1): каскадна деривація M1→H4+D1 --
    derive_engine: Optional[DeriveEngine] = None
    derive_enabled = bool(m1_cfg.get("derive_engine_enabled", True))
    if derive_enabled:
        anchor_offset_s = int(cfg.get("day_anchor_offset_s", 0))
        # ADR-0023: D1 anchor (22:00 UTC = 79200s)
        d1_anchor_offset_s = int(cfg.get("day_anchor_offset_s_d1", 0))
        # Calendar per symbol для DeriveEngine
        calendars_for_engine: Dict[str, MarketCalendar] = {
            sym: calendars[sym] for sym in symbols
        }

        derive_engine = DeriveEngine(
            symbols=symbols,
            anchor_offset_s=anchor_offset_s,
            d1_anchor_offset_s=d1_anchor_offset_s,
            calendars=calendars_for_engine,
        )
        # Shared UDS: DeriveEngine коммітить через той же UDS (без file race)
        for sym in symbols:
            derive_engine.register_symbol_uds(sym, uds)
        # Inject DeriveEngine в кожен per-symbol poller
        for p in pollers:
            p._derive_engine = derive_engine  # noqa: SLF001
        logging.info(
            "DERIVE_ENGINE_WIRED symbols=%d anchor_offset_s=%d d1_anchor=%d commit_tfs=%s",
            len(symbols),
            anchor_offset_s,
            d1_anchor_offset_s,
            sorted(derive_engine._commit_tfs_s),  # noqa: SLF001
        )

    # Redis tail_n для priming M1→H4 (всі TF, якими керує m1_poller)
    # Без прайминґу derived TF (M5-H4) put_bar() створює порожні deque
    # і перезаписує connector's повні Redis tail — split-brain (20260219-027).
    redis_cfg = cfg.get("redis", {})
    tail_n_raw = redis_cfg.get("tail_n_by_tf_s", {})
    redis_tail_n: Dict[int, int] = {}
    _PRIME_TFS = (60, 180, 300, 900, 1800, 3600, 14400, 86400)  # M1→H4+D1 (ADR-0023)
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
                    logging.debug(
                        "M1_POLLER_DERIVE_WARMUP_PARSE_FAILED key=%r value=%r",
                        k,
                        v,
                        exc_info=True,
                    )
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
                logging.debug(
                    "M1_POLLER_CASCADE_CATCHUP_PARSE_FAILED raw=%r",
                    raw_catchup,
                    exc_info=True,
                )
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
        logging.debug("M1_POLLER_PSUTIL_PID_CHECK_FAILED pid=%s", pid, exc_info=True)
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
            logging.debug(
                "M1_POLLER_PIDFILE_READ_FAILED path=%s", _PID_FILE, exc_info=True
            )
            old_pid = 0
        if old_pid and _is_pid_alive(old_pid):
            logging.error(
                "M1_POLLER_DUPLICATE pid=%d вже працює! Pidfile=%s. "
                "Вбийте старий процес або видаліть pidfile.",
                old_pid,
                _PID_FILE,
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
        logging.debug("M1_POLLER_PIDFILE_CLEANUP_FAIL", exc_info=True)


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
        logging.info(
            "ENV: secrets_loaded path=%s keys=%d", report.path, report.keys_count
        )

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
