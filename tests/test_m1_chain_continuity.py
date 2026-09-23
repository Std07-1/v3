"""Суцільний ланцюг свічок (ADR-0101): open = close попереднього існуючого бару; застарілий край — у останній бар сесії."""
from __future__ import annotations

import json
import logging

from core.model.bars import CandleBar
from runtime.ingest.m1_session_filter import (
    MARKER_OPEN_CHAINED,
    SSOT_EDIT_CHAIN,
    SSOT_EDIT_FOLD,
    PausePolicy,
    VERDICT_PAUSE_EDGE_STALE_DROPPED,
    VERDICT_PAUSE_EDGE_STALE_FOLDED,
    chain_open_to_prev_close,
    fold_edge_stale,
    normalize_m1_sequence,
    plan_m1_append,
)
from runtime.ingest.market_calendar import MarketCalendar
from runtime.ingest.polling import m1_poller as poller_mod
from runtime.ingest.polling.m1_poller import M1SymbolPoller
from runtime.store.ssot_jsonl import read_m1_chain_context

M1_MS = 60_000
TUE_2059 = 1_790_110_740_000  # 2026-09-22 20:59 UTC (XAU: остання хвилина сесії перед перервою)
US_CFD_POLICY = PausePolicy(noise_margin_min=60, edge_stale_max_volume=8)


def _calendar() -> MarketCalendar:
    return MarketCalendar(enabled=True, weekend_close_dow=4, weekend_close_hm="20:45", weekend_open_dow=6,
                          weekend_open_hm="22:00", daily_break_start_hm="21:00", daily_break_end_hm="22:00",
                          daily_break_enabled=True)


def _bar(open_ms: int, o: float, h: float, low: float, c: float, v: float) -> CandleBar:
    return CandleBar(symbol="XAU/USD", tf_s=60, open_time_ms=open_ms, close_time_ms=open_ms + M1_MS, o=o, h=h,
                     low=low, c=c, v=v, complete=True, src="history")


# XAU 22.09.2026 (архів FXCM PREVIOUS_CLOSE): 20:59, застарілий край 21:00, перша хвилина після перерви 22:01
BAR_2059 = _bar(TUE_2059, 4358.33, 4358.73, 4355.37, 4357.63, 516.0)
STALE_2100 = _bar(TUE_2059 + M1_MS, 4357.63, 4357.74, 4357.63, 4357.74, 4.0)
BAR_2201 = _bar(TUE_2059 + 62 * M1_MS, 4357.74, 4363.07, 4357.74, 4363.06, 397.0)


def test_chain_restores_open_broken_by_broker_revision():
    """XAU 22.09 19:01 c 4355.63 → 4355.08 (ревізія), 19:02 o лишився 4355.63: open стає close попереднього."""
    prev = _bar(0, 4353.32, 4355.95, 4354.39, 4355.08, 663.0)
    broken = _bar(M1_MS, 4355.63, 4355.71, 4353.72, 4354.06, 598.0)
    fixed = chain_open_to_prev_close(prev, broken)
    assert (fixed.o, fixed.h, fixed.low, fixed.c) == (4355.08, 4355.71, 4353.72, 4354.06)
    assert fixed.extensions["open_chained_from"] == 4355.63
    assert chain_open_to_prev_close(prev, fixed) is fixed  # ідемпотентно


def test_chain_extends_high_low_to_cover_the_new_open():
    prev = _bar(0, 100.0, 101.0, 99.0, 102.0, 1.0)
    fixed = chain_open_to_prev_close(prev, _bar(M1_MS, 100.5, 100.8, 100.2, 100.6, 1.0))
    assert (fixed.o, fixed.h, fixed.low) == (102.0, 102.0, 100.2)


def test_fold_edge_stale_like_tv():
    """TV FX:XAUUSD 15m 20:45 C = 4357.74: пізні тіки 21:00 у останньому барі сесії."""
    folded = fold_edge_stale(BAR_2059, STALE_2100)
    assert (folded.o, folded.h, folded.low, folded.c, folded.v) == (4358.33, 4358.73, 4355.37, 4357.74, 520.0)
    assert folded.extensions["late_ticks_folded"] == 4.0


def test_sequence_folds_stale_edge_and_keeps_the_open_continuous():
    out, verdicts = normalize_m1_sequence([BAR_2201, STALE_2100, BAR_2059], is_trading_fn=_calendar().is_trading_minute,
                                          flat_max_volume=4, pause_policy=US_CFD_POLICY)
    assert [b.open_time_ms for b in out] == [TUE_2059, BAR_2201.open_time_ms]
    assert out[0].c == 4357.74 and out[1].o == 4357.74 and "open_chained_from" not in out[1].extensions
    assert (STALE_2100, VERDICT_PAUSE_EDGE_STALE_FOLDED) in verdicts


def test_sequence_chains_to_the_bar_before_the_window():
    prev = _bar(TUE_2059 - M1_MS, 4358.0, 4358.5, 4357.9, 4358.20, 300.0)
    out, _ = normalize_m1_sequence([BAR_2059], is_trading_fn=_calendar().is_trading_minute, flat_max_volume=4,
                                   pause_policy=US_CFD_POLICY, prev_bar=prev)
    assert out[0].o == 4358.20 and out[0].extensions["open_chained_from"] == 4358.33


class _Uds:
    def __init__(self, tail=()):
        self.committed = []
        self._tail = list(tail)

    def commit_final_bar(self, bar):
        self.committed.append(bar)
        return type("Result", (), {"ok": True})()

    def read_tail_candles(self, symbol, tf_s, n):
        return self._tail[-n:]

    def set_gap_state(self, **kwargs):
        pass


def _poller(uds):
    return M1SymbolPoller(symbol="XAU/USD", provider=object(), uds=uds, calendar=_calendar(), m3_derive=False,
                          pause_policy=US_CFD_POLICY)


def test_live_poller_keeps_chain_across_the_break_when_stale_edge_is_dropped(caplog):
    """Наживо застарілий край відкидається (вкладення — у settle), але open першої хвилини сесії прив'язується до
    закоміченого close: розриву на графіку немає одразу."""
    uds = _Uds()
    poller = _poller(uds)
    caplog.set_level(logging.WARNING)

    assert poller._ingest_bar(BAR_2059)  # noqa: SLF001
    assert not poller._ingest_bar(STALE_2100)  # noqa: SLF001
    assert poller._ingest_bar(BAR_2201)  # noqa: SLF001

    assert uds.committed[-1].o == BAR_2059.c == 4357.63
    assert uds.committed[-1].extensions["open_chained_from"] == 4357.74
    assert "M1_OPEN_CHAINED" in caplog.text and poller.stats["open_chained"] == 1


def test_live_poller_takes_the_chain_from_the_disk_tail_after_restart():
    hidden_flat = CandleBar(symbol="XAU/USD", tf_s=60, open_time_ms=TUE_2059 + M1_MS, close_time_ms=TUE_2059 + 2 * M1_MS,
                            o=1.0, h=1.0, low=1.0, c=1.0, v=1.0, complete=True, src="history",
                            extensions={"calendar_pause_flat": True})
    uds = _Uds(tail=[BAR_2059, hidden_flat])
    poller = _poller(uds)
    poller.warmup_watermark(tail_n=10)

    assert poller._ingest_bar(BAR_2201)  # noqa: SLF001
    assert uds.committed[-1].o == 4357.63  # від видимого 20:59, не від пласкої паузи


def test_poller_without_prev_bar_writes_broker_open_as_is(monkeypatch):
    uds = _Uds()
    poller = _poller(uds)
    assert poller._ingest_bar(BAR_2201)  # noqa: SLF001
    assert uds.committed[-1].o == 4357.74 and "open_chained_from" not in uds.committed[-1].extensions
    assert poller_mod.chain_open_to_prev_close is chain_open_to_prev_close


# --- Пакетні записувачі: дозапис нових ключів між барами SSOT (ADR-0101 C3) ------------------------------------------

def _plan(bars, ssot_bars=(), **kwargs):
    return plan_m1_append(bars, list(ssot_bars), is_trading_fn=_calendar().is_trading_minute, flat_max_volume=4,
                          pause_policy=US_CFD_POLICY, **kwargs)


def _hidden_pause_flat(open_ms: int) -> CandleBar:
    return CandleBar(symbol="XAU/USD", tf_s=60, open_time_ms=open_ms, close_time_ms=open_ms + M1_MS, o=1.0, h=1.0,
                     low=1.0, c=1.0, v=1.0, complete=True, src="history", extensions={"calendar_pause_flat": True})


TUE_1900 = TUE_2059 - 119 * M1_MS  # 2026-09-22 19:00 UTC — усередині сесії


def test_plan_chains_the_window_start_to_the_last_visible_ssot_bar_before_it():
    """Перший новий бар прив'язується до останнього ВИДИМОГО бару SSOT перед вікном; пласку паузу display ховає —
    сусідом вона не є, а її ключ зайнятий і не переписується."""
    ssot_prev = _bar(TUE_1900, 2000.1, 2000.3, 1999.9, 2000.0, 300.0)
    broker_same_key_as_hidden = _bar(TUE_1900 + M1_MS, 2000.0, 2000.2, 1999.8, 2000.1, 250.0)
    new_bar = _bar(TUE_1900 + 2 * M1_MS, 2000.4, 2001.0, 2000.3, 2000.9, 310.0)
    plan = _plan([broker_same_key_as_hidden, new_bar], [ssot_prev, _hidden_pause_flat(TUE_1900 + M1_MS)])
    assert [b.open_time_ms for b in plan.to_write] == [new_bar.open_time_ms]
    written = plan.to_write[0]
    assert (written.o, written.h, written.low, written.c) == (2000.0, 2001.0, 2000.0, 2000.9)
    assert written.extensions[MARKER_OPEN_CHAINED] == 2000.4
    assert plan.already_in_ssot == 1 and plan.open_chained == 1 and plan.ssot_edits == ()


def test_plan_names_the_chain_edit_of_the_ssot_bar_right_after_the_window_and_does_not_write_it():
    """Бар SSOT одразу після вікна: закомічений фінал записувач не переписує (дубль ключа дописом — ADR-0098 §3.7),
    а називає правку для settle з точним значенням."""
    new_bar = _bar(TUE_1900, 2000.0, 2001.6, 1999.9, 2001.5, 300.0)
    ssot_next = _bar(TUE_1900 + M1_MS, 2002.0, 2002.4, 2001.8, 2002.2, 280.0)
    plan = _plan([new_bar], [ssot_next])
    assert [b.open_time_ms for b in plan.to_write] == [new_bar.open_time_ms]
    (edit,) = plan.ssot_edits
    assert edit.reason == SSOT_EDIT_CHAIN and edit.current is ssot_next
    assert (edit.target.o, edit.target.low, edit.target.c) == (2001.5, 2001.5, 2002.2)


def test_plan_ssot_bar_after_the_window_already_in_chain_needs_no_edit():
    new_bar = _bar(TUE_1900, 2000.0, 2002.1, 1999.9, 2002.0, 300.0)
    plan = _plan([new_bar], [_bar(TUE_1900 + M1_MS, 2002.0, 2002.4, 2001.8, 2002.2, 280.0)])
    assert plan.ssot_edits == () and plan.open_chained == 0


def test_plan_folds_the_stale_edge_into_a_new_last_session_minute_like_tv():
    """XAU 22.09: 20:59 c=4357.63 + 21:00 v=4 c=4357.74 → 20:59 c=4357.74 v=520; 22:01 o=4357.74 без правки."""
    plan = _plan([BAR_2201, STALE_2100, BAR_2059])
    assert [b.open_time_ms for b in plan.to_write] == [TUE_2059, BAR_2201.open_time_ms]
    folded = plan.to_write[0]
    assert (folded.c, folded.v, folded.extensions["late_ticks_folded"]) == (4357.74, 520.0, 4.0)
    assert (STALE_2100, VERDICT_PAUSE_EDGE_STALE_FOLDED) in plan.verdicts
    assert plan.open_chained == 0 and plan.ssot_edits == ()


def test_plan_names_the_fold_into_an_existing_last_session_minute():
    """20:59 уже в SSOT, 21:00 полер відкинув: вкласти може лише settle — план не пише 21:00 і називає правку 20:59;
    новий 22:01 прив'язується до close, який лежить на диску зараз."""
    plan = _plan([STALE_2100, BAR_2201], [BAR_2059])
    assert [b.open_time_ms for b in plan.to_write] == [BAR_2201.open_time_ms]
    assert plan.to_write[0].o == 4357.63 and plan.to_write[0].extensions[MARKER_OPEN_CHAINED] == 4357.74
    (edit,) = plan.ssot_edits
    assert edit.reason == SSOT_EDIT_FOLD and edit.current is BAR_2059
    assert (edit.target.c, edit.target.v) == (4357.74, 520.0)
    assert (STALE_2100, VERDICT_PAUSE_EDGE_STALE_DROPPED) in plan.verdicts


def test_plan_does_not_write_a_key_occupied_on_disk_by_any_row():
    plan = _plan([BAR_2059], occupied_opens={TUE_2059})
    assert plan.to_write == () and plan.verdicts == () and plan.already_in_ssot == 1


def test_read_m1_chain_context_sees_what_readers_see(tmp_path, caplog):
    """Сусіди для ланцюга — з тим самим вибирачем, що в читачів (пізніший рядок перемагає), з extensions; рядок без
    OHLC у ланцюг не йде — гучно."""
    part = tmp_path / "XAU_USD" / "tf_60" / "part-20260922.jsonl"
    part.parent.mkdir(parents=True)
    stale_row = dict(BAR_2059.to_dict(), c=1.0)
    hidden_row = _hidden_pause_flat(TUE_2059 + M1_MS).to_dict()
    broken_row = {"open_time_ms": TUE_2059 + 2 * M1_MS, "complete": True, "src": "history"}
    part.write_text("\n".join(json.dumps(row) for row in (stale_row, BAR_2059.to_dict(), hidden_row, broken_row)),
                    encoding="utf-8")
    caplog.set_level(logging.WARNING)
    bars = read_m1_chain_context(str(tmp_path), "XAU/USD", TUE_2059, TUE_2059)
    assert [(b.open_time_ms, b.c) for b in bars] == [(TUE_2059, 4357.63), (TUE_2059 + M1_MS, 1.0)]
    assert bars[1].extensions == {"calendar_pause_flat": True}
    assert "SSOT_M1_CONTEXT_ROWS_REJECTED" in caplog.text
