"""
tests/test_smc_runner.py — SmcRunner інтеграційні тести (ADR-0024 §3.4).

Перевіряє:
  _bar_dict_to_candle_bar: short format / long format / bad dict / invalid geometry
  SmcRunner.__init__: читає tf_allowlist_s, symbols, lookback з full_cfg
  SmcRunner.on_bar_dict: valid bar → delta cached; bad dict → None; preview bar → delta з has_changes
  SmcRunner.get_snapshot: після engine.update через warmup або on_bar_dict
  SmcRunner.last_delta + clear_delta: кеш дельти
  SmcRunner.warmup: mock UDS → engine наповнений барами; uds=None → без краші

Python 3.7 compatible.
"""

from __future__ import annotations

import types
from typing import Any, Dict, List, Optional

import pytest

from core.model.bars import CandleBar
from core.smc.config import SmcConfig
from core.smc.engine import SmcEngine
from runtime.smc.smc_runner import SmcRunner, _bar_dict_to_candle_bar

# ──────────────────────────────────────────────────────────────
#  Константи та хелпери
# ──────────────────────────────────────────────────────────────

SYM = "XAU/USD"
TF = 60  # M1
T0 = 1_700_000_000_000  # arbitrary epoch ms
BAR_MS = TF * 1000


def _make_engine(swing_period: int = 2) -> SmcEngine:
    """Легкий SmcEngine для тестів (малий swing_period, малий lookback)."""
    cfg = SmcConfig.from_dict(
        {
            "enabled": True,
            "lookback_bars": 100,
            "swing_period": swing_period,
            "ob": {
                "enabled": True,
                "min_impulse_atr_mult": 0.1,
                "atr_period": 5,
                "max_active_per_side": 5,
            },
            "fvg": {"enabled": True, "min_gap_atr_mult": 0.0, "max_active": 10},
            "structure": {"enabled": True, "confirmation_bars": 1},
            "max_zones_per_tf": 30,
            "performance": {"max_compute_ms": 2000, "log_slow_threshold_ms": 500},
        }
    )
    return SmcEngine(cfg)


def _make_full_cfg(
    symbols: Optional[List[str]] = None,
    tf_allowlist: Optional[List[int]] = None,
) -> Dict[str, Any]:
    """Мінімальний full_cfg для SmcRunner."""
    tfs = tf_allowlist or [60, 300, 900]
    return {
        "symbols": symbols or [SYM],
        "tf_allowlist_s": tfs,
        "smc": {
            "enabled": True,
            "lookback_bars": 100,
            "swing_period": 2,
            "compute_tfs": tfs,  # match test TFs so on_bar_dict/warmup work
            "ob": {
                "enabled": True,
                "min_impulse_atr_mult": 0.1,
                "atr_period": 5,
                "max_active_per_side": 5,
            },
            "fvg": {"enabled": True, "min_gap_atr_mult": 0.0, "max_active": 10},
            "structure": {"enabled": True, "confirmation_bars": 1},
            "max_zones_per_tf": 30,
            "performance": {"max_compute_ms": 2000, "log_slow_threshold_ms": 500},
        },
    }


def _bar_dict(
    i: int = 0,
    o: float = 1900.0,
    h: float = 1902.0,
    low: float = 1898.0,
    c: float = 1901.0,
    complete: bool = True,
    fmt: str = "short",
) -> Dict[str, Any]:
    """Будує bar dict у short (o/h/low/c) або long (open/high/low/close) форматі."""
    open_ms = T0 + i * BAR_MS
    base: Dict[str, Any] = {
        "open_time_ms": open_ms,
        "complete": complete,
        "src": "derived",
    }
    if fmt == "short":
        base.update({"o": o, "h": h, "low": low, "c": c, "v": 500.0})
    else:
        base.update({"open": o, "high": h, "low": low, "close": c, "volume": 500.0})
    return base


def _candle_bar(
    i: int, o: float, h: float, low: float, c: float, complete: bool = True
) -> CandleBar:
    open_ms = T0 + i * BAR_MS
    return CandleBar(
        symbol=SYM,
        tf_s=TF,
        open_time_ms=open_ms,
        close_time_ms=open_ms + BAR_MS,
        o=o,
        h=h,
        low=low,
        c=c,
        v=500.0,
        complete=complete,
        src="derived",
    )


def _flat_candle_bars(n: int, p: float = 1900.0) -> List[CandleBar]:
    return [_candle_bar(i, p, p + 1.0, p - 1.0, p) for i in range(n)]


# ──────────────────────────────────────────────────────────────
#  _bar_dict_to_candle_bar — unit tests
# ──────────────────────────────────────────────────────────────


class TestBarDictToCandleBar:
    """Перевіряє конвертер raw bar dict → CandleBar."""

    def test_short_format_ok(self):
        """LWC short format o/h/low/c/v → CandleBar."""
        d = _bar_dict(i=5, fmt="short")
        cb = _bar_dict_to_candle_bar(d, SYM, TF)
        assert cb is not None
        assert cb.symbol == SYM
        assert cb.tf_s == TF
        assert cb.open_time_ms == T0 + 5 * BAR_MS
        assert cb.close_time_ms == T0 + 5 * BAR_MS + BAR_MS
        assert cb.o == 1900.0
        assert cb.h == 1902.0
        assert cb.low == 1898.0
        assert cb.c == 1901.0
        assert cb.complete is True

    def test_long_format_ok(self):
        """LWC long format open/high/low/close/volume → CandleBar."""
        d = _bar_dict(i=3, fmt="long")
        cb = _bar_dict_to_candle_bar(d, SYM, TF)
        assert cb is not None
        assert cb.h == 1902.0
        assert cb.o == 1900.0
        assert cb.c == 1901.0
        assert cb.v == 500.0

    def test_preview_bar_complete_false(self):
        """complete=False → CandleBar.complete=False (не фільтрується тут)."""
        d = _bar_dict(complete=False, fmt="short")
        cb = _bar_dict_to_candle_bar(d, SYM, TF)
        assert cb is not None
        assert cb.complete is False

    def test_missing_open_time_ms_returns_none(self):
        """Відсутній open_time_ms → None."""
        d = {"o": 1900.0, "h": 1902.0, "low": 1898.0, "c": 1901.0, "v": 100.0}
        assert _bar_dict_to_candle_bar(d, SYM, TF) is None

    def test_zero_open_time_ms_returns_none(self):
        """open_time_ms=0 → None."""
        d = _bar_dict()
        d["open_time_ms"] = 0
        assert _bar_dict_to_candle_bar(d, SYM, TF) is None

    def test_invalid_geometry_h_lt_low_returns_none(self):
        """h < low (inverted) → None (S4 rail)."""
        d = _bar_dict(h=1895.0, low=1900.0)  # h < low
        assert _bar_dict_to_candle_bar(d, SYM, TF) is None

    def test_open_zero_returns_none(self):
        """o=0 → None (bad price data)."""
        d = _bar_dict()
        d["o"] = 0.0
        d["open"] = 0.0
        assert _bar_dict_to_candle_bar(d, SYM, TF) is None

    def test_close_time_correct(self):
        """close_time_ms = open_time_ms + tf_s * 1000 (I2)."""
        d = _bar_dict(i=0)
        cb = _bar_dict_to_candle_bar(d, SYM, TF)
        assert cb is not None
        assert cb.close_time_ms == cb.open_time_ms + TF * 1000


# ──────────────────────────────────────────────────────────────
#  SmcRunner.__init__ — конструктор
# ──────────────────────────────────────────────────────────────


class TestSmcRunnerInit:
    """Конструктор читає config з full_cfg."""

    def test_reads_tf_allowlist(self):
        cfg = _make_full_cfg(tf_allowlist=[60, 300, 3600])
        runner = SmcRunner(cfg, _make_engine())
        assert runner._tf_allowlist == {60, 300, 3600}

    def test_reads_symbols(self):
        cfg = _make_full_cfg(symbols=["XAU/USD", "EUR/USD"])
        runner = SmcRunner(cfg, _make_engine())
        assert "XAU/USD" in runner._symbols
        assert "EUR/USD" in runner._symbols

    def test_empty_smc_section_uses_defaults(self):
        """Відсутня секція smc → defaults (lookback_bars=500)."""
        cfg = {"symbols": [SYM], "tf_allowlist_s": [60]}
        runner = SmcRunner(cfg, _make_engine())
        assert runner._lookback == 500  # default

    def test_lookback_from_smc_section(self):
        cfg = _make_full_cfg()
        runner = SmcRunner(cfg, _make_engine())
        assert runner._lookback == 100  # з _make_full_cfg smc.lookback_bars


# ──────────────────────────────────────────────────────────────
#  SmcRunner.on_bar_dict
# ──────────────────────────────────────────────────────────────


class TestSmcRunnerOnBarDict:
    """on_bar_dict: кеш delta, фільтрація preview, bad dict."""

    def _runner_with_warmup(self) -> SmcRunner:
        """Runner з наперед завантаженими барами (через engine.update)."""
        engine = _make_engine()
        engine.update(SYM, TF, _flat_candle_bars(30))
        runner = SmcRunner(_make_full_cfg(), engine)
        return runner

    def test_valid_bar_returns_delta(self):
        """Валідний complete bar → повертає SmcDelta (не None)."""
        runner = self._runner_with_warmup()
        d = _bar_dict(i=100, complete=True)
        delta = runner.on_bar_dict(SYM, TF, d)
        assert delta is not None

    def test_valid_bar_cached_in_last_delta(self):
        """Після on_bar_dict delta доступна через last_delta()."""
        runner = self._runner_with_warmup()
        d = _bar_dict(i=101, complete=True)
        delta_returned = runner.on_bar_dict(SYM, TF, d)
        delta_cached = runner.last_delta(SYM, TF)
        assert delta_cached is not None
        assert delta_cached is delta_returned

    def test_preview_bar_returned_not_none(self):
        """complete=False → engine.on_bar() не raises, runner повертає SmcDelta."""
        runner = self._runner_with_warmup()
        d = _bar_dict(i=102, complete=False)
        # engine ігнорує incomplete бари — повертає delta з has_changes=False
        delta = runner.on_bar_dict(SYM, TF, d)
        # SmcDelta завжди повертається, але has_changes=False для preview
        assert delta is not None
        assert delta.has_changes is False

    def test_bad_dict_returns_none(self):
        """Відсутні поля → None (без краші)."""
        runner = self._runner_with_warmup()
        assert runner.on_bar_dict(SYM, TF, {}) is None

    def test_invalid_geometry_returns_none(self):
        """h < low → None (без краші)."""
        runner = self._runner_with_warmup()
        d = _bar_dict(h=1895.0, low=1900.0)
        assert runner.on_bar_dict(SYM, TF, d) is None

    def test_long_format_works(self):
        """on_bar_dict підтримує long format (open/high/low/close)."""
        runner = self._runner_with_warmup()
        d = _bar_dict(i=103, fmt="long", complete=True)
        delta = runner.on_bar_dict(SYM, TF, d)
        assert delta is not None


# ──────────────────────────────────────────────────────────────
#  SmcRunner.get_snapshot
# ──────────────────────────────────────────────────────────────


class TestSmcRunnerGetSnapshot:
    """get_snapshot повертає SmcSnapshot після наповнення engine."""

    def test_get_snapshot_before_warmup_empty_snapshot(self):
        """engine без барів → get_snapshot повертає порожній snapshot (bar_count=0).

        SmcEngine.update([]) ще не викликано, але перший get_snapshot для нового
        (symbol, tf) може повернути порожній SmcSnapshot (engine lazy-init), або
        SmcRunner.get_snapshot повертає None якщо engine ще не бачив цей ключ.
        Перевіряємо: або None, або SmcSnapshot з bar_count=0.
        """
        engine = SmcEngine(SmcConfig())
        runner = SmcRunner(_make_full_cfg(), engine)
        snap = runner.get_snapshot(SYM, TF)
        # Дозволяємо обидва варіанти: None або порожній snapshot
        assert snap is None or (hasattr(snap, "bar_count") and snap.bar_count == 0)

    def test_get_snapshot_after_update(self):
        """engine.update → get_snapshot повертає SmcSnapshot."""
        engine = _make_engine()
        engine.update(SYM, TF, _flat_candle_bars(20))
        runner = SmcRunner(_make_full_cfg(), engine)
        snap = runner.get_snapshot(SYM, TF)
        assert snap is not None
        # zones/swings/levels мають бути списками
        assert isinstance(snap.zones, list)
        assert isinstance(snap.swings, list)
        assert isinstance(snap.levels, list)

    def test_get_snapshot_after_on_bar_dict(self):
        """on_bar_dict тригерить engine.on_bar → snapshot оновлюється."""
        engine = _make_engine()
        engine.update(SYM, TF, _flat_candle_bars(20))
        runner = SmcRunner(_make_full_cfg(), engine)
        snap_before = runner.get_snapshot(SYM, TF)
        # Додаємо новий бар
        d = _bar_dict(i=200, complete=True)
        runner.on_bar_dict(SYM, TF, d)
        snap_after = runner.get_snapshot(SYM, TF)
        # Snapshot не None після on_bar
        assert snap_after is not None
        # updated_ms ≥ попереднього (snapshot оновлено або залишився той самий)
        if snap_before is not None:
            assert snap_after.computed_at_ms >= snap_before.computed_at_ms


# ──────────────────────────────────────────────────────────────
#  SmcRunner.last_delta / clear_delta
# ──────────────────────────────────────────────────────────────


class TestSmcRunnerDeltaCache:
    """Кеш delta: last_delta / clear_delta."""

    def _runner_with_warmup(self) -> SmcRunner:
        engine = _make_engine()
        engine.update(SYM, TF, _flat_candle_bars(25))
        return SmcRunner(_make_full_cfg(), engine)

    def test_last_delta_none_before_any_bar(self):
        """Без on_bar_dict → last_delta = None."""
        engine = _make_engine()
        engine.update(SYM, TF, _flat_candle_bars(20))
        runner = SmcRunner(_make_full_cfg(), engine)
        assert runner.last_delta(SYM, TF) is None

    def test_last_delta_populated_after_on_bar(self):
        """Після on_bar_dict → last_delta не None."""
        runner = self._runner_with_warmup()
        runner.on_bar_dict(SYM, TF, _bar_dict(i=300, complete=True))
        assert runner.last_delta(SYM, TF) is not None

    def test_clear_delta_removes_cache(self):
        """clear_delta → last_delta = None."""
        runner = self._runner_with_warmup()
        runner.on_bar_dict(SYM, TF, _bar_dict(i=301, complete=True))
        assert runner.last_delta(SYM, TF) is not None
        runner.clear_delta(SYM, TF)
        assert runner.last_delta(SYM, TF) is None

    def test_clear_delta_idempotent(self):
        """Подвійний clear_delta не падає."""
        runner = self._runner_with_warmup()
        runner.clear_delta(SYM, TF)  # нічого не було → не падає
        runner.clear_delta(SYM, TF)

    def test_delta_separate_per_tf(self):
        """Delta кешується окремо для кожного (symbol, tf_s)."""
        engine = _make_engine()
        engine.update(SYM, TF, _flat_candle_bars(20))
        engine.update(SYM, 300, _flat_candle_bars(20))
        runner = SmcRunner(_make_full_cfg(), engine)

        runner.on_bar_dict(SYM, TF, _bar_dict(i=400, complete=True))
        # TF=300 не notified → last_delta = None
        assert runner.last_delta(SYM, TF) is not None
        assert runner.last_delta(SYM, 300) is None


# ──────────────────────────────────────────────────────────────
#  SmcRunner.warmup — mock UDS
# ──────────────────────────────────────────────────────────────


class _MockWindowResult:
    """Мок WindowResult з полем bars_lwc."""

    def __init__(self, bars_lwc: List[Dict[str, Any]]) -> None:
        self.bars_lwc = bars_lwc


class _MockUds:
    """Мок UDS reader для warmup (S1: read-only)."""

    def __init__(self, bars_lwc: Optional[List[Dict[str, Any]]] = None) -> None:
        self._bars_lwc = bars_lwc
        self.calls: List[Any] = []

    def read_window(self, spec: Any, policy: Any) -> Optional[_MockWindowResult]:
        self.calls.append((spec, policy))
        if self._bars_lwc is None:
            return None
        return _MockWindowResult(list(self._bars_lwc))


def _make_lwc_bars_dicts(n: int) -> List[Dict[str, Any]]:
    """Генерує n bar dict у short LWC форматі."""
    return [_bar_dict(i=i, fmt="short") for i in range(n)]


class TestSmcRunnerWarmup:
    """warmup: mock UDS → engine наповнений; uds=None → без краші."""

    def test_warmup_populates_snapshot(self):
        """warmup з 20 барами → get_snapshot не None."""
        dicts = _make_lwc_bars_dicts(20)
        uds = _MockUds(dicts)
        engine = _make_engine()
        runner = SmcRunner(_make_full_cfg(symbols=[SYM], tf_allowlist=[TF]), engine)
        runner.warmup(uds)
        snap = runner.get_snapshot(SYM, TF)
        assert snap is not None
        assert isinstance(snap.zones, list)
        assert isinstance(snap.swings, list)

    def test_warmup_calls_read_window_per_symbol_tf(self):
        """warmup викликає read_window для кожної (symbol, tf) пари + M1 session reads."""
        uds = _MockUds([])
        engine = _make_engine()
        runner = SmcRunner(
            _make_full_cfg(symbols=[SYM, "EUR/USD"], tf_allowlist=[60, 300]),
            engine,
        )
        runner.warmup(uds)
        # 2 symbols × 2 TFs = 4 warmup reads
        # + 2 extra M1 session reads (2880 limit, when M1 warmup < 2880 bars)
        assert len(uds.calls) == 6

    def test_warmup_with_uds_returning_none_no_crash(self):
        """read_window повертає None (пустий результат) → warmup не падає."""
        uds = _MockUds(None)  # read_window → None
        engine = _make_engine()
        runner = SmcRunner(_make_full_cfg(symbols=[SYM], tf_allowlist=[TF]), engine)
        runner.warmup(uds)  # не повинно кидати
        # snapshot відсутній (0 барів)
        snap = runner.get_snapshot(SYM, TF)
        # engine.update викликається з [] → snapshot може бути або None або порожній
        # Не падаємо — це головне. Snapshot може повернути valid empty.
        # (engine.update([]) → _compute_snapshot([]) → порожній snapshot)

    def test_warmup_skips_bad_dicts(self):
        """Якщо bars_lwc містить некоректні записи → engine не падає."""
        bad_dicts = [
            {"open_time_ms": 0},  # open_time_ms=0
            {"o": 1900.0},  # відсутній open_time_ms
            {
                "open_time_ms": T0,
                "o": 1900.0,
                "h": 1895.0,
                "low": 1900.0,
                "c": 1901.0,
            },  # h < low
        ] + _make_lwc_bars_dicts(
            10
        )  # 10 валідних
        uds = _MockUds(bad_dicts)
        engine = _make_engine()
        runner = SmcRunner(_make_full_cfg(symbols=[SYM], tf_allowlist=[TF]), engine)
        runner.warmup(uds)  # не повинно кидати
        snap = runner.get_snapshot(SYM, TF)
        assert snap is not None

    def test_warmup_sorts_bars_by_open_time_ms(self):
        """UDS може давати бари не в порядку → warmup сортує → snapshot коректний."""
        dicts = list(reversed(_make_lwc_bars_dicts(20)))  # зворотний порядок
        uds = _MockUds(dicts)
        engine = _make_engine()
        runner = SmcRunner(_make_full_cfg(symbols=[SYM], tf_allowlist=[TF]), engine)
        runner.warmup(uds)
        snap = runner.get_snapshot(SYM, TF)
        assert snap is not None


# ──────────────────────────────────────────────────────────────
#  SmcRunner.get_narrative — market-hours guard
# ──────────────────────────────────────────────────────────────


class TestSmcRunnerMarketClosedGuard:
    """Narrative повертає 'market_closed' коли ринок закритий."""

    def _make_runner_with_calendar(self):
        """Runner з calendar для XAU/USD (cfd_us_22_23)."""
        cfg = _make_full_cfg(symbols=[SYM], tf_allowlist=[TF])
        cfg["smc"]["narrative"] = {"enabled": True}
        # ADR-0035: sessions enabled for narrative
        cfg["smc"]["sessions"] = {"enabled": False}
        cfg["market_calendar_by_group"] = {
            "cfd_us_22_23": {
                "market_weekend_open_dow": 6,
                "market_weekend_open_hm": "22:00",
                "market_weekend_close_dow": 4,
                "market_weekend_close_hm": "20:45",
                "market_daily_break_start_hm": "21:00",
                "market_daily_break_end_hm": "22:00",
            }
        }
        cfg["market_calendar_symbol_groups"] = {SYM: "cfd_us_22_23"}
        engine = _make_engine()
        return SmcRunner(cfg, engine)

    def test_calendar_built_in_init(self):
        """SmcRunner.__init__ побудує calendar для XAU/USD."""
        runner = self._make_runner_with_calendar()
        assert SYM in runner._calendars
        assert runner._calendars[SYM].enabled is True

    def test_market_closed_returns_wait_block(self, monkeypatch):
        """Коли ринок закритий → get_narrative повертає market_closed block."""
        import time

        runner = self._make_runner_with_calendar()
        runner._warmup_done = True  # simulate completed warmup
        # Субота 15:00 UTC → ринок гарантовано закритий
        # dow=5 (saturday), 15:00 = 900 min
        saturday_15h_ms = 1742306400000  # 2025-03-15 Sat 15:00 UTC (arbitrary)
        # Замість фіксованої дати, використаємо monkeypatch
        # Saturday = dow 5, between close (Fri 20:45) and open (Sun 22:00)
        import datetime

        # Знайдемо наступну суботу 15:00 UTC
        dt_sat = datetime.datetime(2025, 3, 15, 15, 0, 0)  # Saturday
        assert dt_sat.weekday() == 5  # Saturday
        sat_ms = int(dt_sat.timestamp() * 1000)

        monkeypatch.setattr(time, "time", lambda: sat_ms / 1000)
        result = runner.get_narrative(SYM, TF, 1900.0, 5.0)
        assert result is not None
        assert result.mode == "wait"
        assert result.sub_mode == "market_closed"
        assert result.market_phase == "closed"
        assert "market_closed" in result.warnings

    def test_no_calendar_symbol_runs_normally(self):
        """Символ без calendar (напр. crypto) → narrative працює без guard."""
        cfg = _make_full_cfg(symbols=["BTCUSDT"], tf_allowlist=[TF])
        cfg["smc"]["narrative"] = {"enabled": True}
        cfg["smc"]["sessions"] = {"enabled": False}
        # Без market_calendar_by_group
        engine = _make_engine()
        runner = SmcRunner(cfg, engine)
        assert "BTCUSDT" not in runner._calendars


# ──────────────────────────────────────────────────────────────
#  SmcRunner._warmup_done — journal gated until warmup completes
# ──────────────────────────────────────────────────────────────


class TestSmcRunnerWarmupDoneGuard:
    """Journal записи блокуються до завершення warmup."""

    def test_warmup_done_false_initially(self):
        """Прапорець _warmup_done = False до виклику warmup()."""
        engine = _make_engine()
        runner = SmcRunner(_make_full_cfg(symbols=[SYM], tf_allowlist=[TF]), engine)
        assert runner._warmup_done is False

    def test_warmup_done_true_after_warmup(self):
        """Після warmup() прапорець = True."""
        dicts = _make_lwc_bars_dicts(20)
        uds = _MockUds(dicts)
        engine = _make_engine()
        runner = SmcRunner(_make_full_cfg(symbols=[SYM], tf_allowlist=[TF]), engine)
        runner.warmup(uds)
        assert runner._warmup_done is True


# ──────────────────────────────────────────────────────────────
#  on_bar_dict → journal (all TFs, not just viewer)
# ──────────────────────────────────────────────────────────────


class TestOnBarDictJournalTick:
    """on_bar_dict() triggers journal.record() for complete bars after warmup."""

    def test_on_bar_dict_calls_journal_on_complete_bar(self, monkeypatch):
        """Complete bar on compute TF → journal.record() called."""
        dicts = _make_lwc_bars_dicts(20)
        uds = _MockUds(dicts)
        engine = _make_engine()
        cfg = _make_full_cfg(symbols=[SYM], tf_allowlist=[TF])
        cfg["smc"]["narrative"] = {"enabled": True}
        cfg["smc"]["sessions"] = {"enabled": False}
        cfg["smc"]["signal_journal"] = {"enabled": True, "path": "data_v3/_signals"}
        runner = SmcRunner(cfg, engine)
        runner.warmup(uds)

        # Spy on journal.record
        recorded = []
        original_record = runner._journal.record

        def spy_record(*args, **kwargs):
            recorded.append((args, kwargs))
            # Don't actually write file

        monkeypatch.setattr(runner._journal, "record", spy_record)

        # Feed a complete bar
        bar = _bar_dict(i=20, complete=True)
        runner.on_bar_dict(SYM, TF, bar)

        assert len(recorded) >= 1, "journal.record() should be called from on_bar_dict"
        # First positional arg is symbol
        assert recorded[0][0][0] == SYM

    def test_on_bar_dict_skips_journal_on_preview_bar(self, monkeypatch):
        """Preview bar (complete=False) → journal NOT called from on_bar_dict."""
        dicts = _make_lwc_bars_dicts(20)
        uds = _MockUds(dicts)
        engine = _make_engine()
        cfg = _make_full_cfg(symbols=[SYM], tf_allowlist=[TF])
        cfg["smc"]["narrative"] = {"enabled": True}
        cfg["smc"]["sessions"] = {"enabled": False}
        cfg["smc"]["signal_journal"] = {"enabled": True, "path": "data_v3/_signals"}
        runner = SmcRunner(cfg, engine)
        runner.warmup(uds)

        recorded = []
        monkeypatch.setattr(
            runner._journal, "record", lambda *a, **kw: recorded.append(1)
        )

        bar = _bar_dict(i=20, complete=False)
        runner.on_bar_dict(SYM, TF, bar)

        assert len(recorded) == 0, "journal should NOT be called for preview bars"

    def test_on_bar_dict_skips_journal_before_warmup(self, monkeypatch):
        """Before warmup → journal NOT called even for complete bars."""
        engine = _make_engine()
        cfg = _make_full_cfg(symbols=[SYM], tf_allowlist=[TF])
        cfg["smc"]["narrative"] = {"enabled": True}
        cfg["smc"]["sessions"] = {"enabled": False}
        cfg["smc"]["signal_journal"] = {"enabled": True, "path": "data_v3/_signals"}
        runner = SmcRunner(cfg, engine)
        # No warmup!

        recorded = []
        monkeypatch.setattr(
            runner._journal, "record", lambda *a, **kw: recorded.append(1)
        )

        bar = _bar_dict(i=0, complete=True)
        runner.on_bar_dict(SYM, TF, bar)

        assert len(recorded) == 0, "journal should NOT be called before warmup"


class TestSignalStatePersistence:
    """_prev_signals save/load cycle survives restart."""

    def test_save_then_load_roundtrip(self, tmp_path):
        """Saved signal state is recovered on new SmcRunner init."""
        import json
        from core.smc.types import SignalSpec

        engine = _make_engine()
        cfg = _make_full_cfg(symbols=[SYM], tf_allowlist=[TF])
        cfg["smc"]["signal_journal"] = {"enabled": True, "path": str(tmp_path)}
        runner = SmcRunner(cfg, engine)

        # Manually inject a prev signal
        sig = SignalSpec(
            signal_id="sig_XAU_USD_60_zone1",
            zone_id="zone1",
            symbol=SYM,
            tf_s=TF,
            direction="long",
            entry_price=2860.0,
            stop_loss=2855.0,
            take_profit=2890.0,
            risk_reward=6.0,
            entry_method="ote",
            entry_desc="OB 2858-2862",
            confidence=72,
            confidence_factors={"bias": 20, "structure": 15},
            grade="A",
            state="approaching",
            state_reason="12 pt to zone",
            created_ms=1000,
            updated_ms=2000,
            bars_alive=5,
            session="london",
            in_killzone=True,
            warnings=[],
        )
        runner._prev_signals[(SYM, TF)] = [sig]
        runner._save_prev_signals()

        # Verify file was written
        state_file = tmp_path / "signal_state.json"
        assert state_file.exists()
        data = json.loads(state_file.read_text(encoding="utf-8"))
        assert f"{SYM}|{TF}" in data

        # Create new runner — should recover
        runner2 = SmcRunner(cfg, engine)
        assert (SYM, TF) in runner2._prev_signals
        recovered = runner2._prev_signals[(SYM, TF)]
        assert len(recovered) == 1
        assert recovered[0].signal_id == "sig_XAU_USD_60_zone1"
        assert recovered[0].state == "approaching"
        assert recovered[0].entry_price == 2860.0
        assert recovered[0].stop_loss == 2855.0

    def test_load_missing_file_no_crash(self, tmp_path):
        """No state file → empty _prev_signals (no crash)."""
        engine = _make_engine()
        cfg = _make_full_cfg(symbols=[SYM], tf_allowlist=[TF])
        cfg["smc"]["signal_journal"] = {"enabled": True, "path": str(tmp_path)}
        runner = SmcRunner(cfg, engine)
        assert len(runner._prev_signals) == 0

    def test_load_corrupted_file_no_crash(self, tmp_path):
        """Corrupted state file → empty _prev_signals (graceful)."""
        state_file = tmp_path / "signal_state.json"
        state_file.write_text("{broken json!", encoding="utf-8")

        engine = _make_engine()
        cfg = _make_full_cfg(symbols=[SYM], tf_allowlist=[TF])
        cfg["smc"]["signal_journal"] = {"enabled": True, "path": str(tmp_path)}
        runner = SmcRunner(cfg, engine)
        assert len(runner._prev_signals) == 0
