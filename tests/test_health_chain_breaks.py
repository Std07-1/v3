"""ADR-0101 C4: health міряє розриви суцільного ланцюга M1 у SSOT — open ≠ close попереднього видимого бару.

Ряд підсовується з відомою відповіддю: ревізія брокера між сусідніми хвилинами — розрив без діри (`inner`), розрив
після діри в торгових хвилинах — `at_gap`, суцільний ланцюг — нуль. Межу між ними дає календар символу: через
денну перерву діри бути не може, тож розрив там — `inner`. Перша хвилина сесії, яку брокер пропускає (метали FXCM
відкриваються о 22:01, `session_open_grace_min` групи), діри теж не доводить — перевіряється на справжньому
календарі XAU.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any, Dict, List, Sequence

import pytest

from core.config_loader import (
    htf_anchor_rule_resolver,
    load_system_config,
    resolve_config_path,
    session_open_grace_resolver,
)
from core.health import (
    HEALTH_MEASURE_VERSION,
    ChainBreak,
    compare_reports,
    grade_symbol_tf,
    measure_chain_breaks,
)
from core.model.bars import CandleBar
from core.model.candle_chain import MARKER_CALENDAR_PAUSE_FLAT, hole_possible_between
from runtime.ingest.tick_common import resolve_symbol_calendars
from tools.symbol_health_check import check_symbol

M1_MS = 60_000


def _utc(*args: int) -> int:
    return int(dt.datetime(*args, tzinfo=dt.timezone.utc).timestamp()) * 1000


def _trading_except_daily_break(ms: int) -> bool:
    """Календар як cfd_us влітку: щоденна перерва 21:00–22:00 UTC, решта хвилин торгові."""
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).hour != 21


def _m1(open_ms: int, o: float, c: float, **extensions: Any) -> CandleBar:
    return CandleBar(
        symbol="X", tf_s=60, open_time_ms=open_ms, close_time_ms=open_ms + M1_MS,
        o=o, h=max(o, c) + 0.5, low=min(o, c) - 0.5, c=c, v=10.0, complete=True, src="history",
        extensions=dict(extensions),
    )


def _chain(start_ms: int, closes: Sequence[float], first_open: float = 100.0) -> List[CandleBar]:
    """Суцільний ланцюг сусідніх хвилин: open кожного бару = close попереднього."""
    bars: List[CandleBar] = []
    prev_close = first_open
    for i, close in enumerate(closes):
        bars.append(_m1(start_ms + i * M1_MS, prev_close, close))
        prev_close = close
    return bars


def _measure(bars: Sequence[CandleBar]):
    return measure_chain_breaks(bars, is_trading_fn=_trading_except_daily_break)


TUESDAY_10 = _utc(2026, 7, 7, 10)


# ── вимір ───────────────────────────────────────────────────────────────────
def test_clean_chain_has_no_breaks():
    res = _measure(_chain(TUESDAY_10, [100.0 + i * 0.25 for i in range(30)]))
    assert (res.checked, res.hidden, res.inner, res.at_gap) == (29, 0, 0, 0)
    assert res.inner_samples == () and res.at_gap_samples == ()


def test_broker_revision_between_adjacent_minutes_is_inner_break():
    """Брокер переписав close 10:03 (4355.63 → 4355.08), а open 10:04 лишив сирим — розрив без діри."""
    bars = _chain(TUESDAY_10, [4355.10, 4355.30, 4355.50, 4355.63, 4355.90], first_open=4355.0)
    revised = bars[3]
    bars[3] = _m1(revised.open_time_ms, revised.o, 4355.08)

    res = _measure(bars)

    assert (res.inner, res.at_gap) == (1, 0)
    assert res.inner_samples == (ChainBreak(TUESDAY_10 + 3 * M1_MS, TUESDAY_10 + 4 * M1_MS, 4355.08, 4355.63),)


def test_break_after_hole_in_trading_minutes_is_at_gap():
    """Три торгові хвилини без барів, open після діри ≠ close перед нею — розрив на межі діри, не `inner`."""
    before = _chain(TUESDAY_10, [100.0, 100.5, 101.0])
    after = _chain(TUESDAY_10 + 6 * M1_MS, [103.0, 103.5], first_open=102.0)

    res = _measure(before + after)

    assert (res.inner, res.at_gap) == (0, 1)
    assert res.at_gap_samples == (ChainBreak(TUESDAY_10 + 2 * M1_MS, TUESDAY_10 + 6 * M1_MS, 101.0, 102.0),)


def test_hole_with_intact_chain_is_not_a_break():
    """Геп брокера, через який ланцюг суцільний (open після = close до), — не розрив."""
    before = _chain(TUESDAY_10, [100.0, 100.5, 101.0])
    after = _chain(TUESDAY_10 + 6 * M1_MS, [103.0], first_open=101.0)
    res = _measure(before + after)
    assert (res.inner, res.at_gap) == (0, 0)


def test_break_across_daily_pause_is_inner():
    """Між 20:59 і 22:00 лише хвилини перерви: діри бути не може, розрив на відкритті сесії — `inner`."""
    session_end = _chain(_utc(2026, 7, 7, 20, 57), [4357.50, 4357.60, 4357.63])
    session_open = _chain(_utc(2026, 7, 7, 22), [4358.0], first_open=4357.74)

    res = _measure(session_end + session_open)

    assert (res.inner, res.at_gap) == (1, 0)
    assert res.inner_samples[0].prev_open_ms == _utc(2026, 7, 7, 20, 59)


def test_hidden_pause_flat_bar_is_not_a_chain_neighbour():
    """Бар з `calendar_pause_flat` display ховає: ланцюг іде від 20:59 до 22:00 повз нього."""
    session_end = _chain(_utc(2026, 7, 7, 20, 58), [4357.60, 4357.63])
    hidden = _m1(_utc(2026, 7, 7, 21), 4000.0, 4000.0, **{MARKER_CALENDAR_PAUSE_FLAT: True})
    session_open = _chain(_utc(2026, 7, 7, 22), [4358.0], first_open=4357.63)

    res = _measure(session_end + [hidden] + session_open)

    assert (res.checked, res.hidden, res.inner, res.at_gap) == (2, 1, 0, 0)


def test_float_representation_difference_is_not_a_break():
    bars = [_m1(TUESDAY_10, 100.0, 4355.1), _m1(TUESDAY_10 + M1_MS, 4355.1 + 1e-12, 4355.2)]
    assert _measure(bars).inner == 0


def test_measure_sees_duplicate_keys_as_readers_do():
    """Два записи 10:01: ранній рве ланцюг, пізніший (переможець вибирача, ADR-0094) — ні. Графік бачить пізніший."""
    bars = _chain(TUESDAY_10, [100.0, 100.5, 101.0])
    stale = _m1(TUESDAY_10 + M1_MS, 99.0, 100.5)
    res = _measure([bars[0], stale, bars[1], bars[2]])
    assert (res.checked, res.inner, res.at_gap) == (2, 0, 0)


def test_samples_are_newest_breaks_in_time_order():
    """Свіжий розрив (регресія записувача) має бути у звіті, навіть коли старих розривів більше за ліміт семплів."""
    bars = _chain(TUESDAY_10, [100.0 + i for i in range(9)])
    for i in range(7):  # ревізія close кожної з перших семи хвилин: розрив на кожному наступному open
        bars[i] = _m1(bars[i].open_time_ms, bars[i].o, bars[i].c - 0.25)

    res = measure_chain_breaks(bars, is_trading_fn=_trading_except_daily_break, max_samples=5)

    assert res.inner == 7
    assert [s.open_ms for s in res.inner_samples] == [TUESDAY_10 + i * M1_MS for i in range(3, 8)]
    assert measure_chain_breaks(bars, is_trading_fn=_trading_except_daily_break, max_samples=0).inner_samples == ()


def test_session_open_minute_proves_hole_only_without_grace():
    """20:59 → 22:01, хвилина 22:00 відкриття сесії без бару: без запізнення — можлива діра, із запізненням 1 — ні."""
    bars = _chain(_utc(2026, 7, 7, 20, 58), [4357.60, 4357.63]) + _chain(
        _utc(2026, 7, 7, 22, 1), [4358.0], first_open=4357.74)

    strict = measure_chain_breaks(bars, is_trading_fn=_trading_except_daily_break)
    graced = measure_chain_breaks(bars, is_trading_fn=_trading_except_daily_break, session_open_grace_min=1)

    assert (strict.inner, strict.at_gap) == (0, 1)
    assert (graced.inner, graced.at_gap) == (1, 0)


def test_hole_possible_between_rejects_negative_grace():
    with pytest.raises(ValueError):
        hole_possible_between(TUESDAY_10, TUESDAY_10 + 5 * M1_MS, is_trading_fn=_trading_except_daily_break,
                              session_open_grace_min=-1)


# ── вердикт ─────────────────────────────────────────────────────────────────
def test_inner_break_grades_yellow_and_at_gap_alone_does_not():
    bars = _chain(TUESDAY_10, [100.0, 100.5, 101.0])
    bars[1] = _m1(bars[1].open_time_ms, bars[1].o, 100.4)  # ревізія close без open наступного
    inner = _measure(bars)
    grade = grade_symbol_tf(chain=inner)
    assert (grade.grade, grade.reasons) == ("YELLOW", ["chain_breaks_inner=1"])

    at_gap = _measure(_chain(TUESDAY_10, [100.0]) + _chain(TUESDAY_10 + 5 * M1_MS, [101.0], first_open=100.7))
    assert at_gap.at_gap == 1 and grade_symbol_tf(chain=at_gap).grade == "GREEN"


# ── звіт інструмента ────────────────────────────────────────────────────────
SYMBOL = "XAU/USD"


def _write_m1(data_root: Path, bars: Sequence[CandleBar]) -> None:
    """SSOT-JSONL як на диску: part-файл на UTC-добу відкриття, ключ low — "l"."""
    tf_dir = data_root / SYMBOL.replace("/", "_") / "tf_60"
    tf_dir.mkdir(parents=True, exist_ok=True)
    day = dt.datetime.fromtimestamp(bars[0].open_time_ms / 1000, dt.timezone.utc).strftime("%Y%m%d")
    with open(tf_dir / ("part-%s.jsonl" % day), "w", encoding="utf-8") as fh:
        for bar in bars:
            row = {"symbol": SYMBOL, "tf_s": 60, "open_time_ms": bar.open_time_ms, "close_time_ms": bar.close_time_ms,
                   "o": bar.o, "h": bar.h, "l": bar.low, "c": bar.c, "v": bar.v, "complete": True, "src": "history"}
            fh.write(json.dumps(row) + "\n")


@pytest.fixture(scope="module")
def cfg() -> Dict[str, Any]:
    """Справжній config репо (календар XAU), звужений до M1 і M5 — ланцюг міряється лише на M1."""
    return dict(load_system_config(resolve_config_path(None)), tf_allowlist_s=[60, 300])


def test_report_row_m1_carries_chain_breaks_with_samples(cfg, tmp_path):
    """Вівторок посеред сесії XAU: ревізія між сусідніми хвилинами (inner) і розрив після діри (at_gap)."""
    bars = _chain(TUESDAY_10, [4355.0, 4355.5, 4356.0, 4356.5], first_open=4355.0)
    bars[1] = _m1(bars[1].open_time_ms, bars[1].o, 4355.4)
    bars += _chain(TUESDAY_10 + 8 * M1_MS, [4357.5, 4358.0], first_open=4357.0)
    _write_m1(tmp_path, bars)

    res = check_symbol(cfg, SYMBOL, data_root=str(tmp_path), now_ms=_utc(2026, 7, 7, 12), window_days=1,
                       anchor_rule_for_symbol=htf_anchor_rule_resolver(cfg))

    m1 = res["tfs"]["60"]
    chain = m1["chain_breaks"]
    assert (chain["checked"], chain["hidden"], chain["inner"], chain["at_gap"]) == (5, 0, 1, 1)
    assert "chain_breaks_inner=1" in m1["reasons"]
    inner = chain["inner_samples"][0]
    assert (inner["prev_open"], inner["open"], inner["prev_close"], inner["bar_open"]) == (
        "2026-07-07 10:01", "2026-07-07 10:02", 4355.4, 4355.5)
    assert inner["delta"] == pytest.approx(0.1)
    at_gap = chain["at_gap_samples"][0]
    assert (at_gap["prev_open"], at_gap["open"], at_gap["open_ms"]) == (
        "2026-07-07 10:03", "2026-07-07 10:08", TUESDAY_10 + 8 * M1_MS)
    assert res["tfs"]["300"]["chain_breaks"] is None, "похідні успадковують ланцюг від M1 — не міряються"


# ── відкриття сесії металів на справжньому календарі XAU ────────────────────
@pytest.fixture(scope="module")
def xau_measure(cfg):
    """Вимір з календарем XAU і запізненням відкриття його групи — як у `check_symbol`."""
    calendars, rejected = resolve_symbol_calendars(cfg, [SYMBOL], where="test_health_chain_breaks")
    assert not rejected
    grace = session_open_grace_resolver(cfg)(SYMBOL)

    def measure(bars: Sequence[CandleBar]):
        return measure_chain_breaks(bars, is_trading_fn=calendars[SYMBOL].is_trading_minute,
                                    session_open_grace_min=grace)

    return measure


def test_xau_session_open_break_from_adr_example_is_inner_and_yellow(xau_measure):
    """ADR-0101 §1.1, 22.09.2026: 20:59 c 4357.63 → 22:01 o 4357.74. Перший бар металів — 22:01, діри немає."""
    session_end = _chain(_utc(2026, 9, 22, 20, 57), [4357.50, 4357.60, 4357.63])
    session_open = _chain(_utc(2026, 9, 22, 22, 1), [4358.0], first_open=4357.74)

    res = xau_measure(session_end + session_open)

    assert (res.inner, res.at_gap) == (1, 0)
    assert res.inner_samples == (ChainBreak(_utc(2026, 9, 22, 20, 59), _utc(2026, 9, 22, 22, 1), 4357.63, 4357.74),)
    grade = grade_symbol_tf(chain=res)
    assert (grade.grade, grade.reasons) == ("YELLOW", ["chain_breaks_inner=1"])


def test_xau_weekend_open_break_is_inner(xau_measure):
    """Пт 18.09 20:44 (останній бар тижня) → нд 20.09 22:01 (перший бар металів): лише вихідні й хвилина відкриття."""
    bars = _chain(_utc(2026, 9, 18, 20, 43), [4300.5, 4301.0]) + _chain(
        _utc(2026, 9, 20, 22, 1), [4306.0], first_open=4305.0)
    res = xau_measure(bars)
    assert (res.inner, res.at_gap) == (1, 0)


def test_xau_missing_minute_mid_session_stays_at_gap(xau_measure):
    """Посеред сесії одна торгова хвилина без бару — можлива наша діра: розрив на ній `at_gap`, не `inner`."""
    bars = _chain(TUESDAY_10, [4355.0, 4355.5]) + _chain(TUESDAY_10 + 3 * M1_MS, [4356.5], first_open=4356.0)
    res = xau_measure(bars)
    assert (res.inner, res.at_gap) == (0, 1)


def test_xau_minutes_missing_after_session_open_stay_at_gap(xau_measure):
    """20:59 → 22:03: запізнення відкриття покриває лише 22:00, а 22:01 і 22:02 без барів — можлива діра."""
    bars = _chain(_utc(2026, 9, 22, 20, 58), [4357.60, 4357.63]) + _chain(
        _utc(2026, 9, 22, 22, 3), [4358.0], first_open=4357.74)
    res = xau_measure(bars)
    assert (res.inner, res.at_gap) == (0, 1)


def test_report_row_m1_names_session_open_grace_and_classifies_open_as_inner(cfg, tmp_path):
    """Звіт інструмента бере запізнення з групи календаря символу і показує його поруч із лічильниками."""
    bars = _chain(_utc(2026, 9, 22, 20, 57), [4357.50, 4357.60, 4357.63]) + _chain(
        _utc(2026, 9, 22, 22, 1), [4358.0, 4358.5], first_open=4357.74)
    _write_m1(tmp_path, bars)

    res = check_symbol(cfg, SYMBOL, data_root=str(tmp_path), now_ms=_utc(2026, 9, 22, 23), window_days=1,
                       anchor_rule_for_symbol=htf_anchor_rule_resolver(cfg))

    chain = res["tfs"]["60"]["chain_breaks"]
    assert (chain["inner"], chain["at_gap"], chain["session_open_grace_min"]) == (1, 0, 1)
    assert (chain["inner_samples"][0]["prev_open"], chain["inner_samples"][0]["open"]) == (
        "2026-09-22 20:59", "2026-09-22 22:01")


# ── запізнення відкриття сесії з config ─────────────────────────────────────
def _calendar_cfg(**grace_by_group: Any) -> Dict[str, Any]:
    groups = {name: ({} if grace is None else {"session_open_grace_min": grace})
              for name, grace in grace_by_group.items()}
    return {"market_calendar_by_group": groups,
            "market_calendar_symbol_groups": {"S_%s" % name: name for name in grace_by_group}}


def test_session_open_grace_resolver_reads_group_and_defaults_to_zero():
    grace_for = session_open_grace_resolver(_calendar_cfg(metals=1, eu=2, fx=None))
    assert (grace_for("S_metals"), grace_for("S_eu"), grace_for("S_fx")) == (1, 2, 0)


@pytest.mark.parametrize("bad", [-1, True, "1", 1.5, None])
def test_session_open_grace_resolver_rejects_invalid_value_loudly(bad):
    cfg = _calendar_cfg(metals=0)
    cfg["market_calendar_by_group"]["metals"]["session_open_grace_min"] = bad
    with pytest.raises(ValueError, match="CONFIG_SESSION_OPEN_GRACE_INVALID group=metals"):
        session_open_grace_resolver(cfg)


def test_session_open_grace_resolver_rejects_symbol_without_group():
    with pytest.raises(ValueError, match="SESSION_OPEN_GRACE_SYMBOL_WITHOUT_GROUP symbol=XYZ"):
        session_open_grace_resolver(_calendar_cfg(metals=1))("XYZ")


def test_repo_config_session_open_grace_matches_broker_first_bar_scan(cfg):
    """Виміряно 23.09.2026 на SSOT: метали й індекси США — група з першим баром металів 22:01, EUSTX50 06:01,
    GER30 00:31; групу без виміру (FX, крипта) брокер відкриває на хвилині календаря."""
    grace_for = session_open_grace_resolver(cfg)
    assert {sym: grace_for(sym) for sym in ("XAU/USD", "XAG/USD", "NAS100", "EUSTX50", "GER30", "BTCUSDT")} == {
        "XAU/USD": 1, "XAG/USD": 1, "NAS100": 1, "EUSTX50": 1, "GER30": 1, "BTCUSDT": 0}


# ── порівняння звітів ───────────────────────────────────────────────────────
def _report(chain: Any, measure_version: int = HEALTH_MEASURE_VERSION) -> Dict[str, Any]:
    tf = {"grade": "GREEN", "reasons": [], "bars": 100, "age_buckets": 0,
          "holes": {"missing": 0, "expected": 100}, "geometry": {}, "cascade": None}
    if chain is not None:
        tf["chain_breaks"] = chain
    return {"measure_version": measure_version,
            "symbols": {SYMBOL: {"symbol": SYMBOL, "grade": "GREEN", "tfs": {"60": tf}}}}


def test_more_chain_breaks_is_regression_and_fewer_is_improvement():
    before = _report({"inner": 0, "at_gap": 3})
    after = _report({"inner": 2, "at_gap": 1})
    res = compare_reports(before, after)
    assert [(r.measure, r.before, r.after) for r in res.regressions] == [("розривів ланцюга без діри", 0, 2)]
    assert [(r.measure, r.before, r.after) for r in res.improvements] == [("розривів ланцюга на межі діри", 3, 1)]


def test_v3_baseline_without_chain_breaks_gives_no_false_regression():
    """Baseline v3 поля не має: вимір пропускається, вердикти через межу v4 не порівнюються (rc=3, не відкат)."""
    res = compare_reports(_report(None, measure_version=3), _report({"inner": 70, "at_gap": 147}))
    assert res.ok is True and res.verdicts_comparable is False
    assert res.measure_versions == (3, HEALTH_MEASURE_VERSION) and HEALTH_MEASURE_VERSION == 4
