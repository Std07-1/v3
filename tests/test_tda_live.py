"""
tests/test_tda_live.py — P7: TdaLiveRunner I/O wiring tests (ADR-0040 §3.2).

Перевіряє:
  TdaLiveRunner init / warmup (no state file)
  on_bar filters: non-M15, non-complete, disabled, unknown symbol
  on_bar cascade trigger in London window
  Cascade once-per-day guard
  Trade management via on_bar
  get_signal returns active signal
  State persistence (save/load)
  SmcRunner delegation when TDA enabled
  SmcRunner fallback when TDA disabled
  TdaSignal.to_wire() compatibility with WS frame
"""

from __future__ import annotations

import dataclasses
import json
import os
from typing import List, Optional
from unittest.mock import patch

from core.model.bars import CandleBar
from core.smc.tda.types import (
    TdaCascadeConfig,
    TdaSignal,
    MacroResult,
    H4ConfirmResult,
    SessionNarrative,
    FvgEntry,
    TradeState,
)
from runtime.smc.tda_live import TdaLiveRunner, _signal_from_wire, _bar_dict_to_cb

# ──────────────────────────────────────────────────────────────
#  Constants
# ──────────────────────────────────────────────────────────────

SYM = "XAU/USD"
DAY_MS = 1_728_000_000_000  # 2024-10-04 00:00:00 UTC (exact day boundary)
_MS_PER_HOUR = 3_600_000
M15_S = 900
M15_MS = M15_S * 1000
DATE_STR = "2024-10-04"

# ──────────────────────────────────────────────────────────────
#  Helpers
# ──────────────────────────────────────────────────────────────


def _cfg(enabled: bool = True, **overrides) -> TdaCascadeConfig:
    d = {"enabled": enabled}
    d.update(overrides)
    return TdaCascadeConfig.from_dict(d)


def _make_signal(
    symbol: str = SYM,
    date_str: str = DATE_STR,
    direction: str = "LONG",
    entry_price: float = 1998.0,
    stop_loss: float = 1990.0,
    take_profit: float = 2022.0,
    trade_status: str = "open",
) -> TdaSignal:
    """Canonical test signal for I/O wiring tests."""
    return TdaSignal(
        signal_id=f"tda_{symbol.replace('/', '_')}_{date_str}",
        symbol=symbol,
        date_str=date_str,
        macro=MacroResult(
            direction="BULL",
            method="bos",
            confidence="high",
            pivot_count=3,
            d1_bar_count=10,
        ),
        h4_confirm=H4ConfirmResult(
            confirmed=True,
            close_price=2000.0,
            midpoint=1990.0,
            h4_bar_count=5,
            reason="above_mid",
        ),
        session=SessionNarrative(
            narrative="HUNT_PREV_LOW",
            asia_high=2005.0,
            asia_low=1995.0,
            sweep_direction="BULL",
            sweep_price=1994.0,
            asia_bar_count=16,
        ),
        entry=FvgEntry(
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            risk_reward=round(
                (take_profit - entry_price) / (entry_price - stop_loss), 2
            ),
            direction=direction,
            fvg_high=2000.0,
            fvg_low=1996.0,
            fvg_size=4.0,
            entry_bar_ms=DAY_MS + 8 * _MS_PER_HOUR,
        ),
        grade="A",
        grade_score=7,
        grade_factors={
            "macro": True,
            "h4": True,
            "session": True,
            "fvg": True,
            "rr": True,
            "kz": True,
            "disp": True,
        },
        trade=TradeState(
            status=trade_status,
            partial_closed=False,
            partial_r=0.0,
            max_favorable=0.0,
            trail_sl=stop_loss,
            bars_elapsed=0,
            outcome="",
            net_r=0.0,
        ),
        created_ms=DAY_MS + 8 * _MS_PER_HOUR,
        updated_ms=DAY_MS + 8 * _MS_PER_HOUR,
    )


def _m15_bar(
    hour: int = 8,
    minute: int = 0,
    o: float = 2000.0,
    h: float = 2005.0,
    low: float = 1995.0,
    c: float = 2002.0,
    complete: bool = True,
    symbol: str = SYM,
) -> CandleBar:
    """M15 bar at given UTC hour/minute on DAY_MS."""
    open_ms = DAY_MS + hour * _MS_PER_HOUR + minute * 60_000
    return CandleBar(
        symbol=symbol,
        tf_s=M15_S,
        open_time_ms=open_ms,
        close_time_ms=open_ms + M15_MS,
        o=o,
        h=h,
        low=low,
        c=c,
        v=100.0,
        complete=complete,
        src="derived",
    )


def _make_runner(
    enabled: bool = True, symbols: Optional[List[str]] = None, tmp_path=None
):
    """Create TdaLiveRunner with mocked _read_bars (avoids UDS import)."""
    cfg = _cfg(enabled=enabled)
    runner = TdaLiveRunner(cfg, symbols or [SYM])
    # Override _read_bars to avoid real UDS dependency
    object.__setattr__(runner, "_read_bars", lambda uds_reader, symbol, tf_s, limit: [])
    # Override state path if tmp_path provided
    if tmp_path is not None:
        runner._state_path = str(tmp_path / "tda_state.json")
    return runner


# ──────────────────────────────────────────────────────────────
#  Tests: Init & warmup
# ──────────────────────────────────────────────────────────────


class TestTdaLiveRunnerInit:
    def test_init_no_crash(self):
        runner = _make_runner()
        assert runner.get_signal(SYM) is None

    def test_init_disabled(self):
        runner = _make_runner(enabled=False)
        assert runner._cfg.enabled is False

    def test_warmup_no_state_file(self, tmp_path):
        runner = _make_runner(tmp_path=tmp_path)
        runner.warmup()  # no crash
        assert runner.get_signal(SYM) is None
        assert len(runner._signals) == 0


# ──────────────────────────────────────────────────────────────
#  Tests: on_bar filtering
# ──────────────────────────────────────────────────────────────


class TestOnBarFilters:
    """on_bar should ignore non-M15, non-complete, disabled, unknown symbol."""

    def test_non_m15_ignored(self):
        runner = _make_runner()
        bar_h1 = CandleBar(
            symbol=SYM,
            tf_s=3600,
            open_time_ms=DAY_MS + 8 * _MS_PER_HOUR,
            close_time_ms=DAY_MS + 9 * _MS_PER_HOUR,
            o=2000.0,
            h=2010.0,
            low=1990.0,
            c=2005.0,
            v=100.0,
            complete=True,
            src="derived",
        )
        with patch("runtime.smc.tda_live.run_tda_cascade") as mock_cascade:
            runner.on_bar(SYM, 3600, bar_h1, None)
            mock_cascade.assert_not_called()

    def test_non_complete_ignored(self):
        runner = _make_runner()
        bar = _m15_bar(hour=8, complete=False)
        with patch("runtime.smc.tda_live.run_tda_cascade") as mock_cascade:
            runner.on_bar(SYM, M15_S, bar, None)
            mock_cascade.assert_not_called()

    def test_disabled_ignored(self):
        runner = _make_runner(enabled=False)
        bar = _m15_bar(hour=8)
        with patch("runtime.smc.tda_live.run_tda_cascade") as mock_cascade:
            runner.on_bar(SYM, M15_S, bar, None)
            mock_cascade.assert_not_called()

    def test_unknown_symbol_ignored(self):
        runner = _make_runner(symbols=["XAU/USD"])
        bar = _m15_bar(hour=8, symbol="EUR/USD")
        bar_eurusd = CandleBar(
            symbol="EUR/USD",
            tf_s=M15_S,
            open_time_ms=bar.open_time_ms,
            close_time_ms=bar.close_time_ms,
            o=1.10,
            h=1.11,
            low=1.09,
            c=1.105,
            v=50.0,
            complete=True,
            src="derived",
        )
        with patch("runtime.smc.tda_live.run_tda_cascade") as mock_cascade:
            runner.on_bar("EUR/USD", M15_S, bar_eurusd, None)
            mock_cascade.assert_not_called()


# ──────────────────────────────────────────────────────────────
#  Tests: Cascade trigger
# ──────────────────────────────────────────────────────────────


class TestCascadeTrigger:

    @patch("runtime.smc.tda_live.run_tda_cascade")
    def test_cascade_in_london_window(self, mock_cascade, tmp_path):
        """M15 bar at 08:00 UTC → cascade triggers → signal stored."""
        sig = _make_signal()
        mock_cascade.return_value = sig

        runner = _make_runner(tmp_path=tmp_path)
        bar = _m15_bar(hour=8)
        runner.on_bar(SYM, M15_S, bar, None)

        assert mock_cascade.called
        result = runner.get_signal(SYM)
        assert result is not None
        assert result.symbol == SYM
        assert result.grade == "A"

    @patch("runtime.smc.tda_live.run_tda_cascade")
    def test_cascade_before_london_skipped(self, mock_cascade):
        """M15 bar at 05:00 UTC (before London) → no cascade."""
        runner = _make_runner()
        bar = _m15_bar(hour=5)
        runner.on_bar(SYM, M15_S, bar, None)
        mock_cascade.assert_not_called()

    @patch("runtime.smc.tda_live.run_tda_cascade")
    def test_cascade_after_london_skipped_until_next_day(self, mock_cascade):
        """M15 bar at 14:00 UTC (after London window london_end=13) → no cascade today."""
        runner = _make_runner()
        bar = _m15_bar(hour=14)
        runner.on_bar(SYM, M15_S, bar, None)
        mock_cascade.assert_not_called()

    @patch("runtime.smc.tda_live.run_tda_cascade")
    def test_cascade_once_per_day(self, mock_cascade, tmp_path):
        """Cascade runs ONCE per day. Second London bar → no re-run."""
        sig = _make_signal()
        mock_cascade.return_value = sig

        runner = _make_runner(tmp_path=tmp_path)

        bar1 = _m15_bar(hour=8, minute=0)
        runner.on_bar(SYM, M15_S, bar1, None)
        assert mock_cascade.call_count == 1

        bar2 = _m15_bar(hour=8, minute=15)
        runner.on_bar(SYM, M15_S, bar2, None)
        assert mock_cascade.call_count == 1  # NOT 2

    @patch("runtime.smc.tda_live.run_tda_cascade")
    def test_cascade_no_signal_result(self, mock_cascade):
        """Cascade returns None → no signal stored."""
        mock_cascade.return_value = None
        runner = _make_runner()
        bar = _m15_bar(hour=8)
        runner.on_bar(SYM, M15_S, bar, None)

        assert mock_cascade.called
        assert runner.get_signal(SYM) is None

    @patch("runtime.smc.tda_live.run_tda_cascade")
    def test_cascade_exception_handled(self, mock_cascade):
        """Cascade throws → error logged, no crash."""
        mock_cascade.side_effect = RuntimeError("boom")
        runner = _make_runner()
        bar = _m15_bar(hour=8)
        # Should not raise
        runner.on_bar(SYM, M15_S, bar, None)
        assert runner.get_signal(SYM) is None


# ──────────────────────────────────────────────────────────────
#  Tests: Trade management
# ──────────────────────────────────────────────────────────────


class TestTradeManagement:

    @patch("runtime.smc.tda_live.run_tda_cascade")
    def test_trade_update_on_subsequent_bar(self, mock_cascade, tmp_path):
        """After cascade, subsequent M15 bars update trade state."""
        sig = _make_signal(stop_loss=1990.0, take_profit=2022.0)
        mock_cascade.return_value = sig

        runner = _make_runner(tmp_path=tmp_path)
        # Trigger cascade
        bar1 = _m15_bar(hour=8, minute=0)
        runner.on_bar(SYM, M15_S, bar1, None)
        # bar1 triggers cascade AND first trade update → bars_elapsed=1
        assert runner.get_signal(SYM).trade.bars_elapsed == 1

        # Next M15 bar — trade should be updated by update_trade
        bar2 = _m15_bar(hour=8, minute=15, o=2002.0, h=2008.0, low=2000.0, c=2006.0)
        runner.on_bar(SYM, M15_S, bar2, None)
        updated_sig = runner.get_signal(SYM)
        assert updated_sig is not None
        assert updated_sig.trade.bars_elapsed >= 2

    @patch("runtime.smc.tda_live.run_tda_cascade")
    def test_sl_hit_closes_trade(self, mock_cascade, tmp_path):
        """Bar with low < SL → trade closes."""
        sig = _make_signal(
            direction="LONG",
            entry_price=2000.0,
            stop_loss=1990.0,
            take_profit=2030.0,
        )
        mock_cascade.return_value = sig

        runner = _make_runner(tmp_path=tmp_path)
        bar1 = _m15_bar(hour=8)
        runner.on_bar(SYM, M15_S, bar1, None)

        # Bar that hits SL (low = 1985 < sl = 1990)
        bar_sl = _m15_bar(hour=8, minute=15, o=1995.0, h=1997.0, low=1985.0, c=1988.0)
        runner.on_bar(SYM, M15_S, bar_sl, None)
        result = runner.get_signal(SYM)
        assert result is not None
        assert result.trade.status == "closed"
        assert result.trade.outcome == "LOSS"

    @patch("runtime.smc.tda_live.run_tda_cascade")
    def test_closed_trade_not_updated_further(self, mock_cascade, tmp_path):
        """After trade closes, no further updates."""
        sig = _make_signal(stop_loss=1990.0, take_profit=2030.0)
        mock_cascade.return_value = sig

        runner = _make_runner(tmp_path=tmp_path)
        bar1 = _m15_bar(hour=8)
        runner.on_bar(SYM, M15_S, bar1, None)

        # Close via SL
        bar_sl = _m15_bar(hour=8, minute=15, o=1995.0, h=1997.0, low=1985.0, c=1988.0)
        runner.on_bar(SYM, M15_S, bar_sl, None)
        closed_sig = runner.get_signal(SYM)
        assert closed_sig.trade.status == "closed"

        # Another bar — should NOT change anything
        bar3 = _m15_bar(hour=8, minute=30, o=1988.0, h=1992.0, low=1980.0, c=1985.0)
        runner.on_bar(SYM, M15_S, bar3, None)
        same_sig = runner.get_signal(SYM)
        assert same_sig.trade.bars_elapsed == closed_sig.trade.bars_elapsed


# ──────────────────────────────────────────────────────────────
#  Tests: Persistence (save/load)
# ──────────────────────────────────────────────────────────────


class TestPersistence:

    @patch("runtime.smc.tda_live.run_tda_cascade")
    def test_save_and_load_round_trip(self, mock_cascade, tmp_path):
        """Signal survives save → new runner → warmup → load."""
        sig = _make_signal()
        mock_cascade.return_value = sig

        runner1 = _make_runner(tmp_path=tmp_path)
        bar = _m15_bar(hour=8)
        runner1.on_bar(SYM, M15_S, bar, None)
        assert runner1.get_signal(SYM) is not None
        runner1._save_state()

        # Verify file exists
        assert os.path.isfile(str(tmp_path / "tda_state.json"))

        # New runner loads state
        runner2 = _make_runner(tmp_path=tmp_path)
        runner2.warmup()
        loaded = runner2.get_signal(SYM)
        assert loaded is not None
        assert loaded.symbol == SYM
        assert loaded.signal_id == sig.signal_id

    def test_load_skips_closed_trades(self, tmp_path):
        """Closed trades are NOT restored from state."""
        sig = _make_signal(trade_status="open")
        closed_sig = dataclasses.replace(
            sig,
            trade=dataclasses.replace(sig.trade, status="closed", outcome="sl_hit"),
        )

        # Manually write state with closed trade
        state = {SYM: closed_sig.to_wire()}
        state[SYM]["_day_ms"] = DAY_MS
        state_path = tmp_path / "tda_state.json"
        state_path.write_text(json.dumps(state))

        runner = _make_runner(tmp_path=tmp_path)
        runner.warmup()
        assert runner.get_signal(SYM) is None  # NOT restored

    def test_load_restores_open_trades(self, tmp_path):
        """Open trades ARE restored from state."""
        sig = _make_signal(trade_status="open")
        state = {SYM: sig.to_wire()}
        state[SYM]["_day_ms"] = DAY_MS
        state_path = tmp_path / "tda_state.json"
        state_path.write_text(json.dumps(state))

        runner = _make_runner(tmp_path=tmp_path)
        runner.warmup()
        loaded = runner.get_signal(SYM)
        assert loaded is not None
        assert loaded.trade.status == "open"


# ──────────────────────────────────────────────────────────────
#  Tests: Wire format compatibility
# ──────────────────────────────────────────────────────────────


class TestWireFormat:

    def test_signal_to_wire_has_required_fields(self):
        """TdaSignal.to_wire() produces dict compatible with WS frame."""
        sig = _make_signal()
        wire = sig.to_wire()
        assert isinstance(wire, dict)
        # Fields that WS frame expects when iterating signals
        assert "signal_id" in wire
        assert "grade" in wire
        assert "entry" in wire
        assert "trade" in wire
        assert "macro" in wire
        assert "session" in wire

    def test_signal_from_wire_round_trip(self):
        """_signal_from_wire reconstructs signal from wire dict."""
        sig = _make_signal()
        wire = sig.to_wire()
        restored = _signal_from_wire(wire, SYM)
        assert restored is not None
        assert restored.signal_id == sig.signal_id
        assert restored.grade == sig.grade
        assert restored.entry.entry_price == sig.entry.entry_price

    def test_bar_dict_to_cb_short_format(self):
        """_bar_dict_to_cb converts short format bar dict."""
        d = {
            "open_time_ms": DAY_MS,
            "o": 2000.0,
            "h": 2010.0,
            "low": 1990.0,
            "c": 2005.0,
            "v": 100.0,
            "complete": True,
            "src": "derived",
        }
        cb = _bar_dict_to_cb(d, SYM, 900)
        assert cb is not None
        assert cb.o == 2000.0
        assert cb.h == 2010.0
        assert cb.low == 1990.0
        assert cb.c == 2005.0

    def test_bar_dict_to_cb_invalid(self):
        """_bar_dict_to_cb returns None for bad data."""
        assert _bar_dict_to_cb({}, SYM, 900) is None
        assert _bar_dict_to_cb({"open_time_ms": -1}, SYM, 900) is None


# ──────────────────────────────────────────────────────────────
#  Tests: SmcRunner TDA integration
# ──────────────────────────────────────────────────────────────


class TestSmcRunnerTdaIntegration:
    """Verify SmcRunner delegates to TdaLiveRunner when TDA enabled."""

    def _make_smc_runner(self, tda_enabled: bool = False):
        from core.smc.config import SmcConfig
        from core.smc.engine import SmcEngine

        cfg = SmcConfig.from_dict(
            {
                "enabled": True,
                "lookback_bars": 100,
                "swing_period": 2,
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
        engine = SmcEngine(cfg)

        full_cfg = {
            "symbols": [SYM],
            "tf_allowlist_s": [60, 900, 3600, 14400, 86400],
            "smc": {
                "enabled": True,
                "lookback_bars": 100,
                "swing_period": 2,
                "compute_tfs": [900, 3600, 14400, 86400],
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
                "tda_cascade": {"enabled": tda_enabled},
            },
        }
        from runtime.smc.smc_runner import SmcRunner

        return SmcRunner(full_cfg, engine)

    def test_tda_disabled_no_runner(self):
        runner = self._make_smc_runner(tda_enabled=False)
        assert runner._tda_runner is None

    def test_tda_enabled_creates_runner(self):
        runner = self._make_smc_runner(tda_enabled=True)
        assert runner._tda_runner is not None

    def test_get_signals_delegates_to_tda(self):
        """When TDA enabled + signal exists → get_signals returns TDA signal."""
        runner = self._make_smc_runner(tda_enabled=True)
        # Inject signal directly into TDA runner
        sig = _make_signal()
        runner._tda_runner._signals[SYM] = sig

        # Create a dummy narrative (not used by TDA path)
        from core.smc.types import NarrativeBlock

        narr = NarrativeBlock(
            mode="wait",
            sub_mode="",
            headline="",
            bias_summary="",
            scenarios=[],
            next_area="",
            fvg_context="",
            market_phase="london",
            warnings=[],
        )

        sigs, alerts = runner.get_signals(SYM, 900, narr, 2000.0, 5.0)
        assert len(sigs) == 1
        assert sigs[0].signal_id == sig.signal_id
        assert alerts == []

    def test_get_signals_tda_no_signal_returns_empty(self):
        """When TDA enabled but no signal → empty lists."""
        runner = self._make_smc_runner(tda_enabled=True)
        from core.smc.types import NarrativeBlock

        narr = NarrativeBlock(
            mode="wait",
            sub_mode="",
            headline="",
            bias_summary="",
            scenarios=[],
            next_area="",
            fvg_context="",
            market_phase="london",
            warnings=[],
        )
        sigs, alerts = runner.get_signals(SYM, 900, narr, 2000.0, 5.0)
        assert sigs == []
        assert alerts == []


# ═══════════════════════════════════════════════════════════════════
# P8: Config SSOT — config.json round-trip
# ═══════════════════════════════════════════════════════════════════


class TestConfigSsot:
    """P8: Verify config.json:smc.tda_cascade round-trips through TdaCascadeConfig."""

    def test_config_json_has_tda_cascade_section(self):
        with open("config.json") as f:
            cfg = json.load(f)
        assert "tda_cascade" in cfg["smc"], "smc.tda_cascade section missing"

    def test_config_round_trip_all_fields(self):
        with open("config.json") as f:
            cfg = json.load(f)
        d = cfg["smc"]["tda_cascade"]
        tc = TdaCascadeConfig.from_dict(d)
        # Enabled is safe default
        assert tc.enabled is False
        # Spot-check a field from each stage
        assert tc.macro_min_bars == 5
        assert tc.h4_cutoff_hour_utc == 7
        assert tc.london_end_hour_utc == 13
        assert tc.rr_target == 3.0
        assert tc.min_rr == 2.5
        assert tc.max_open_bars_m15 == 96
        assert tc.partial_tp_at_r == 1.0
        assert tc.trail_start_r == 2.0
        assert tc.grade_enabled is True
        assert tc.min_grade_for_entry == "C"

    def test_config_enabled_false_is_k5_compliant(self):
        """K5 Gate: ADR-0040 status=Accepted → enabled=false is safe."""
        with open("config.json") as f:
            cfg = json.load(f)
        assert cfg["smc"]["tda_cascade"]["enabled"] is False

    def test_signals_has_deprecation_marker(self):
        """smc.signals has _deprecated_by pointing to tda_cascade."""
        with open("config.json") as f:
            cfg = json.load(f)
        assert "_deprecated_by" in cfg["smc"]["signals"]
        assert "tda_cascade" in cfg["smc"]["signals"]["_deprecated_by"]
