"""Суцільний ланцюг свічок (ADR-0101): open = close попереднього існуючого бару; застарілий край — у останній бар сесії."""
from __future__ import annotations

import json
import logging

from core.model.bars import CandleBar
import pytest

from runtime.ingest.m1_session_filter import (
    MARKER_OPEN_CHAINED,
    SSOT_EDIT_CHAIN,
    SSOT_EDIT_CHAIN_AFTER_FOLD,
    SSOT_EDIT_FOLD,
    PausePolicy,
    VERDICT_PAUSE_EDGE_STALE_ALREADY_FOLDED,
    VERDICT_PAUSE_EDGE_STALE_DROPPED,
    VERDICT_PAUSE_EDGE_STALE_FOLDED,
    chain_open_to_prev_close,
    fold_edge_stale,
    normalize_m1_sequence,
    open_breaks_chain,
    plan_m1_append,
)
from core.model.candle_chain import hole_possible_between
from runtime.ingest.market_calendar import MarketCalendar
from runtime.ingest.polling import m1_poller as poller_mod
from runtime.ingest.polling.m1_poller import M1SymbolPoller
from runtime.store.ssot_jsonl import read_m1_chain_context
from runtime.store.uds import UnifiedDataStore

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


def _write_m1_part(root, rows):
    folder = root / "XAU_USD" / "tf_60"
    folder.mkdir(parents=True)
    body = "".join(json.dumps(row, separators=(",", ":")) + "\n" for row in rows)
    (folder / "part-20260922.jsonl").write_text(body, encoding="utf-8")


def _row(bar: CandleBar, extensions=None) -> dict:
    row = {"symbol": bar.symbol, "tf_s": 60, "open_time_ms": bar.open_time_ms, "close_time_ms": bar.close_time_ms,
           "o": bar.o, "h": bar.h, "low": bar.low, "c": bar.c, "v": bar.v, "complete": True, "src": "history"}
    if extensions:
        row["extensions"] = extensions
    return row


def test_uds_tail_read_keeps_row_markers(tmp_path):
    """Хвіст з диска несе extensions: без них прихований бар після рестарту — «торговий» для ланцюга й агрегації."""
    _write_m1_part(tmp_path, [_row(BAR_2059), _row(STALE_2100, {"calendar_pause_flat": True})])
    uds = UnifiedDataStore(data_root=str(tmp_path), boot_id="test-boot", tf_allowlist={60},
                           min_coldload_bars={60: 1}, role="reader")

    candles = uds.read_tail_candles("XAU/USD", 60, 10)

    assert [c.extensions for c in candles] == [{}, {"calendar_pause_flat": True}]


def test_live_poller_warmup_from_real_disk_skips_the_hidden_bar(tmp_path):
    hidden = CandleBar(symbol="XAU/USD", tf_s=60, open_time_ms=TUE_2059 + M1_MS, close_time_ms=TUE_2059 + 2 * M1_MS,
                       o=4350.0, h=4350.0, low=4350.0, c=4350.0, v=1.0, complete=True, src="history")
    _write_m1_part(tmp_path, [_row(BAR_2059), _row(hidden, {"calendar_pause_flat": True})])
    uds = UnifiedDataStore(data_root=str(tmp_path), boot_id="test-boot", tf_allowlist={60},
                           min_coldload_bars={60: 1}, role="reader")
    poller = _poller(uds)

    poller.warmup_watermark(tail_n=10)

    assert poller._last_bar.open_time_ms == TUE_2059 and poller._last_bar.c == 4357.63  # noqa: SLF001


def test_poller_without_prev_bar_writes_broker_open_as_is(monkeypatch):
    uds = _Uds()
    poller = _poller(uds)
    assert poller._ingest_bar(BAR_2201)  # noqa: SLF001
    assert uds.committed[-1].o == 4357.74 and "open_chained_from" not in uds.committed[-1].extensions
    assert poller_mod.chain_open_to_prev_close is chain_open_to_prev_close


# --- Пакетні записувачі: дозапис нових ключів між барами SSOT (ADR-0101 C3) ------------------------------------------

def _plan(bars, ssot_bars=(), **kwargs):
    kwargs.setdefault("session_open_grace_min", 1)  # як config для cfd_us_22_23: метали відкривають сесію о 22:01
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


def test_plan_names_the_fold_into_an_existing_last_session_minute_and_the_chain_after_it():
    """20:59 уже в SSOT, 21:00 полер відкинув: вкласти може лише settle — план не пише 21:00 і називає правку 20:59.
    Новий 22:01 пишеться в ланцюгу з close, що лежить на диску зараз (як у полера); його правка на вкладений close
    рахується від брокерського бару — це рівно TV: o = low = 4357.74, без маркера ланцюга."""
    plan = _plan([STALE_2100, BAR_2201], [BAR_2059])
    assert [b.open_time_ms for b in plan.to_write] == [BAR_2201.open_time_ms]
    written = plan.to_write[0]
    assert written.o == 4357.63 and written.extensions[MARKER_OPEN_CHAINED] == 4357.74
    fold, chain_after = plan.ssot_edits
    assert fold.reason == SSOT_EDIT_FOLD and fold.current is BAR_2059
    assert (fold.target.c, fold.target.v) == (4357.74, 520.0)
    assert chain_after.reason == SSOT_EDIT_CHAIN_AFTER_FOLD and chain_after.current is written
    assert chain_after.target == BAR_2201
    assert (STALE_2100, VERDICT_PAUSE_EDGE_STALE_DROPPED) in plan.verdicts


def test_plan_names_the_chain_of_the_existing_first_bar_after_the_folded_edge():
    """22:01 уже в SSOT (полер прив'язав до закоміченого close 20:59): вкладення 21:00 у 20:59 тягне правку й 22:01 —
    інакше після settle-правки 20:59 на відкритті лишився б розрив 0.11."""
    poller_2201 = chain_open_to_prev_close(BAR_2059, BAR_2201)
    plan = _plan([STALE_2100], [BAR_2059, poller_2201])
    assert plan.to_write == ()
    fold, chain_after = plan.ssot_edits
    assert fold.reason == SSOT_EDIT_FOLD and fold.current is BAR_2059
    assert chain_after.reason == SSOT_EDIT_CHAIN_AFTER_FOLD and chain_after.current is poller_2201
    assert (chain_after.target.o, chain_after.target.c) == (4357.74, 4363.06)


def test_plan_rerun_over_the_already_folded_edge_names_no_edit():
    """Повторний засів того самого вікна: 21:00 на диск не пишеться (вкладена в 20:59), тож знову приходить новим
    ключем. Її тіки вже в 20:59 (маркер late_ticks_folded) — вкласти вдруге означало б подвоїти обсяг (v 520 → 524)."""
    first = _plan([BAR_2059, STALE_2100, BAR_2201])
    rerun = _plan([STALE_2100], list(first.to_write), occupied_opens={b.open_time_ms for b in first.to_write})
    assert rerun.to_write == () and rerun.ssot_edits == ()
    assert rerun.verdicts == ((STALE_2100, VERDICT_PAUSE_EDGE_STALE_ALREADY_FOLDED),)


def _visible_after_plan(ssot_bars, plan):
    """SSOT після дозапису і всіх названих правок, застосованих буквально (так їх прочитав би оператор)."""
    state = {b.open_time_ms: b for b in ssot_bars if not b.extensions.get("calendar_pause_flat")}
    state.update({b.open_time_ms: b for b in plan.to_write})
    for edit in plan.ssot_edits:
        assert state[edit.current.open_time_ms] == edit.current  # current — бар таким, яким він лежить після дозапису
        state[edit.current.open_time_ms] = edit.target
    return [state[open_ms] for open_ms in sorted(state)]


_TUE_2057 = TUE_2059 - 2 * M1_MS
_POLLER_2059_AFTER_OUTAGE = _bar(TUE_2059, 4358.33, 4358.73, 4355.37, 4357.63, 516.0)  # o від close 20:56 = 4358.33
_CLOSURE_SCENARIOS = {
    # 22:01 у SSOT, прив'язаний полером до закоміченого close 20:59
    "next_bar_in_ssot": ([STALE_2100], [BAR_2059, chain_open_to_prev_close(BAR_2059, BAR_2201)]),
    # 22:01 новий: пишеться від close на диску
    "next_bar_new": ([STALE_2100, BAR_2201], [BAR_2059]),
    # дірка 20:57–20:58 перед наявним 20:59 і застарілий край після нього: той самий бар — і ланцюг, і вкладення
    "chain_and_fold_on_one_bar": (
        [_bar(_TUE_2057, 4358.33, 4358.45, 4358.30, 4358.40, 310.0),
         _bar(_TUE_2057 + M1_MS, 4358.40, 4358.41, 4358.15, 4358.20, 290.0), STALE_2100],
        [_bar(_TUE_2057 - M1_MS, 4358.0, 4358.4, 4357.9, 4358.33, 280.0), _POLLER_2059_AFTER_OUTAGE,
         chain_open_to_prev_close(_POLLER_2059_AFTER_OUTAGE, BAR_2201)]),
}


@pytest.mark.parametrize("scenario", sorted(_CLOSURE_SCENARIOS))
def test_named_edits_are_closed_under_the_chain_rule(scenario):
    """Набір правок замкнений: дозапис + усі цілі разом не лишають розриву ланцюга; правка одна на ключ."""
    bars, ssot_bars = _CLOSURE_SCENARIOS[scenario]
    plan = _plan(bars, ssot_bars)
    assert len({edit.current.open_time_ms for edit in plan.ssot_edits}) == len(plan.ssot_edits)
    visible = _visible_after_plan(ssot_bars, plan)
    assert [(b.open_time_ms, a.c, b.o) for a, b in zip(visible, visible[1:]) if open_breaks_chain(a.c, b.o)] == []
    assert next(b for b in visible if b.open_time_ms == TUE_2059).c == 4357.74  # вкладений close, як у TV


def test_chain_and_fold_of_one_bar_are_one_edit_with_both_rules():
    bars, ssot_bars = _CLOSURE_SCENARIOS["chain_and_fold_on_one_bar"]
    edit = next(e for e in _plan(bars, ssot_bars).ssot_edits if e.current.open_time_ms == TUE_2059)
    assert edit.reason == "%s+%s" % (SSOT_EDIT_CHAIN, SSOT_EDIT_FOLD) and edit.current is _POLLER_2059_AFTER_OUTAGE
    assert (edit.target.o, edit.target.c, edit.target.v) == (4358.20, 4357.74, 520.0)


# Діра 20:31–20:49 (торгові хвилини): сусід SSOT 20:30 лежить поза вибіркою брокера 20:50–20:52
SSOT_BEFORE_HOLE = _bar(TUE_2059 - 29 * M1_MS, 4350.0, 4351.0, 4349.5, 4350.5, 300.0)
SAMPLE_AFTER_HOLE = [_bar(TUE_2059 - (9 - k) * M1_MS, 4356.0 + k, 4356.9 + k, 4355.8 + k, 4356.5 + k, 200.0)
                     for k in range(3)]


def test_plan_does_not_pull_the_chain_across_our_hole_outside_the_sample():
    """ADR-0101 §3.1: open першого нового бару лишається брокерським — інакше свічка малює рух за всю діру."""
    plan = _plan(SAMPLE_AFTER_HOLE, [SSOT_BEFORE_HOLE])

    assert plan.to_write[0].o == 4356.0 and MARKER_OPEN_CHAINED not in plan.to_write[0].extensions
    assert plan.chain_gaps_left == ((SSOT_BEFORE_HOLE.open_time_ms, SAMPLE_AFTER_HOLE[0].open_time_ms),)


def test_plan_pulls_the_chain_across_a_broker_gap_the_sample_covers():
    """Хвилини діри, які вибірка засвідчує (ремонт запитав їх у брокера), — геп брокера: ланцюг тягнеться."""
    covered = [(SSOT_BEFORE_HOLE.open_time_ms + M1_MS, SAMPLE_AFTER_HOLE[-1].open_time_ms)]
    plan = _plan(SAMPLE_AFTER_HOLE, [SSOT_BEFORE_HOLE], covered_ranges=covered)

    assert plan.to_write[0].o == 4350.5 and plan.chain_gaps_left == ()


def test_plan_pulls_the_chain_across_the_session_open_minute_the_broker_skips():
    """Метали відкривають сесію о 22:01: хвилина 22:00 без бару діри не доводить (session_open_grace_min=1)."""
    first_after_break = _bar(BAR_2201.open_time_ms, 4357.74, 4363.07, 4357.74, 4363.06, 397.0)

    plan = _plan([first_after_break], [BAR_2059], session_open_grace_min=1)
    blocked = _plan([first_after_break], [BAR_2059], session_open_grace_min=0)

    assert plan.to_write[0].o == BAR_2059.c and plan.chain_gaps_left == ()
    assert blocked.to_write[0].o == 4357.74 and blocked.chain_gaps_left == ((TUE_2059, BAR_2201.open_time_ms),)


def test_plan_names_no_chain_edit_for_the_ssot_bar_beyond_an_uncovered_hole():
    ssot_after_hole = _bar(SAMPLE_AFTER_HOLE[-1].open_time_ms + 5 * M1_MS, 4370.0, 4371.0, 4369.0, 4370.5, 100.0)

    plan = _plan(SAMPLE_AFTER_HOLE, [ssot_after_hole])

    assert plan.ssot_edits == ()
    assert plan.chain_gaps_left == ((SAMPLE_AFTER_HOLE[-1].open_time_ms, ssot_after_hole.open_time_ms),)


def test_hole_possible_between_skips_minutes_the_source_covers():
    is_trading = _calendar().is_trading_minute
    a, b = SSOT_BEFORE_HOLE.open_time_ms, SAMPLE_AFTER_HOLE[0].open_time_ms

    assert hole_possible_between(a, b, is_trading_fn=is_trading)
    assert not hole_possible_between(a, b, is_trading_fn=is_trading, covered=[(a + M1_MS, b - M1_MS)])
    assert hole_possible_between(a, b, is_trading_fn=is_trading, covered=[(a + 2 * M1_MS, b - M1_MS)])


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
