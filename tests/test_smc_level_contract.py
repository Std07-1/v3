"""
tests/test_smc_level_contract.py — контракт рівня ADR-0104 §3.2 (слайс S1).

Специфікація:
  - SmcLevel без полів контракту видає на wire рівно старі id/kind/price/t_ms (зворотна сумісність);
  - key/family/state/tier видаються, коли задані; невідоме значення — гучна помилка;
  - make_level_key / level_period — формат "{series}:{side}:{symbol}:{period}" з ISO UTC хвилиною;
  - S6: словник Python (родини, стани, tier, поля wire) = типи ui_v4/src/types.ts;
  - конструктори (key levels, EQ, сесії) заповнюють контракт; рухомий і завершений H/L одного періоду мають
    один key — на ньому триматиметься закріплення (S5).
"""

import datetime as dt
import json
import pathlib
import re

import pytest

from core.model.bars import CandleBar
from core.smc.config import SmcConfig
from core.smc.key_levels import compute_key_levels
from core.smc.liquidity import detect_liquidity_levels
from core.smc.sessions import compute_session_levels, load_session_windows
from core.smc.types import (
    LEVEL_CONTRACT_WIRE_FIELDS,
    LEVEL_FAMILIES,
    LEVEL_GROUP_BY_KIND,
    LEVEL_GROUPS,
    LEVEL_KINDS,
    LEVEL_STATES,
    LEVEL_TIERS,
    SmcLevel,
    SmcSwing,
    level_period,
    level_price_key,
    make_level_key,
    make_swing_id,
)

_REPO = pathlib.Path(__file__).resolve().parents[1]
_TYPES_TS = _REPO / "ui_v4" / "src" / "types.ts"
_LEGACY_WIRE_FIELDS = {"id", "kind", "price", "t_ms"}
_SYMBOL = "XAU/USD"
_M1_S, _M15_S, _H4_S, _D1_S = 60, 900, 14400, 86400
_D1_OPEN_MS = 1_790_283_600_000  # 2026-09-24T21:00Z — відкриття торгової доби (17:00 NY, літо)


def _ms(text: str) -> int:
    return int(dt.datetime.strptime(text, "%Y-%m-%d %H:%M").replace(tzinfo=dt.timezone.utc).timestamp() * 1000)


def _candle(tf_s: int, open_ms: int, high: float, low: float, complete: bool = True) -> CandleBar:
    return CandleBar(symbol=_SYMBOL, tf_s=tf_s, open_time_ms=open_ms, close_time_ms=open_ms + tf_s * 1000,
                     o=low, h=high, low=low, c=high, v=100.0, complete=complete, src="test")


def _by_kind(levels) -> dict:
    return {lv.kind: lv for lv in levels}


def _contract(level: SmcLevel) -> tuple:
    return level.family, level.state, level.tier


def _level(**contract) -> SmcLevel:
    return SmcLevel(id="pdh_XAU_USD_86400_265034", symbol=_SYMBOL, tf_s=_D1_S, kind="pdh", price=2650.34,
                    time_ms=_D1_OPEN_MS, touches=1, **contract)


# ── wire ──────────────────────────────────────────────────


def test_to_wire_without_contract_fields_keeps_legacy_shape():
    assert set(_level().to_wire()) == _LEGACY_WIRE_FIELDS


def test_to_wire_with_contract_fields_emits_them():
    wire = _level(key="d1:high:XAU/USD:2026-09-24T21:00Z", family="day", state="fixed", tier=1, auto=False,
                  group="day").to_wire()
    assert wire["key"] == "d1:high:XAU/USD:2026-09-24T21:00Z"
    assert (wire["family"], wire["state"], wire["tier"], wire["auto"], wire["group"]) == ("day", "fixed", 1, False,
                                                                                         "day")
    assert set(wire) == _LEGACY_WIRE_FIELDS | set(LEVEL_CONTRACT_WIRE_FIELDS)


@pytest.mark.parametrize("contract", [
    {"family": "weekly"}, {"state": "live"}, {"tier": 0}, {"tier": 4},
    {"tier": True}, {"auto": 1}, {"auto": "yes"},  # True == 1: без звірки типу пройшли б словник
    {"group": "sessions"}, {"group": "month"},  # month — ще не рахується (S7)
])
def test_level_with_unknown_contract_value_raises(contract):
    with pytest.raises(ValueError, match="LEVEL_CONTRACT_INVALID"):
        _level(**contract)


# ── key ───────────────────────────────────────────────────


def test_level_period_is_iso_utc_minute_of_period_start():
    assert level_period(_D1_OPEN_MS) == "2026-09-24T21:00Z"
    assert level_period(_D1_OPEN_MS + 59_999) == "2026-09-24T21:00Z"  # секунди не входять у період


def test_make_level_key_formats_series_side_symbol_period():
    assert make_level_key("london", "low", _SYMBOL, "2026-09-25T07:00Z") == "london:low:XAU/USD:2026-09-25T07:00Z"
    assert make_level_key("eq900", "high", "NAS100", str(level_price_key(21034.567))) == "eq900:high:NAS100:2103457"


def test_make_level_key_rejects_unknown_side():
    with pytest.raises(ValueError, match="LEVEL_KEY_SIDE_INVALID"):
        make_level_key("d1", "top", _SYMBOL, "2026-09-24T21:00Z")


# ── S6: Python ↔ ui_v4/src/types.ts ──────────────────────


def _ts_source() -> str:
    return _TYPES_TS.read_text(encoding="utf-8-sig")


def _ts_string_union(type_name: str) -> set:
    match = re.search(r"export type %s = ([^;]+);" % type_name, _ts_source())
    assert match, "types.ts: немає export type %s" % type_name
    return set(re.findall(r"'([a-z0-9_]+)'", match.group(1)))


def _ts_smc_level_fields() -> dict:
    match = re.search(r"export interface SmcLevel \{(.*?)\n\}", _ts_source(), re.S)
    assert match, "types.ts: немає export interface SmcLevel"
    return dict(re.findall(r"^\s*(\w+)\??:\s*([^;]+);", match.group(1), re.M))


def test_ts_level_family_union_matches_python_vocabulary():
    assert _ts_string_union("LevelFamily") == set(LEVEL_FAMILIES)


def test_ts_level_state_union_matches_python_vocabulary():
    assert _ts_string_union("LevelState") == set(LEVEL_STATES)


def test_ts_level_group_union_matches_python_vocabulary():
    assert _ts_string_union("LevelGroup") == set(LEVEL_GROUPS)


def test_every_level_kind_has_a_menu_group():
    """Рівень без групи обійшов би меню «Рівні» — вимкнути його трейдер не зміг би."""
    assert set(LEVEL_GROUP_BY_KIND) == set(LEVEL_KINDS)


def test_ts_smc_level_declares_every_wire_field():
    fields = _ts_smc_level_fields()
    assert _LEGACY_WIRE_FIELDS | set(LEVEL_CONTRACT_WIRE_FIELDS) <= set(fields)
    assert {int(t) for t in re.findall(r"\d+", fields["tier"])} == set(LEVEL_TIERS)
    assert fields["auto"].strip() == "boolean"


# ── key levels (D1/H4/H1) ─────────────────────────────────


def test_d1_previous_day_is_fixed_anchor_and_current_day_is_forming_context():
    levels = _by_kind(compute_key_levels([
        _candle(_D1_S, _D1_OPEN_MS, 2660.0, 2640.0),
        _candle(_D1_S, _D1_OPEN_MS + _D1_S * 1000, 2670.0, 2650.0, complete=False),
    ]))
    assert levels["pdh"].key == "d1:high:XAU/USD:2026-09-24T21:00Z"
    assert levels["pdl"].key == "d1:low:XAU/USD:2026-09-24T21:00Z"
    assert _contract(levels["pdh"]) == ("day", "fixed", 1)
    assert levels["dh"].key == "d1:high:XAU/USD:2026-09-25T21:00Z"
    assert _contract(levels["dl"]) == ("day", "forming", 3)


def test_forming_day_high_keeps_its_key_after_the_day_closes():
    day0 = _candle(_D1_S, _D1_OPEN_MS, 2660.0, 2640.0)
    day1_open_ms = _D1_OPEN_MS + _D1_S * 1000
    forming = _by_kind(compute_key_levels([day0, _candle(_D1_S, day1_open_ms, 2670.0, 2650.0, complete=False)]))
    closed = _by_kind(compute_key_levels([
        day0,
        _candle(_D1_S, day1_open_ms, 2672.0, 2648.0),
        _candle(_D1_S, day1_open_ms + _D1_S * 1000, 2675.0, 2665.0, complete=False),
    ]))
    assert (closed["pdh"].key, closed["pdl"].key) == (forming["dh"].key, forming["dl"].key)
    assert closed["pdh"].id != forming["dh"].id  # id (kind + ціна) змінюється — дельти бачать заміну


def test_h4_levels_are_htf_family_with_context_tier():
    h4_open_ms = _ms("2026-09-24 22:00")
    levels = _by_kind(compute_key_levels([
        _candle(_H4_S, h4_open_ms, 2655.0, 2645.0),
        _candle(_H4_S, h4_open_ms + _H4_S * 1000, 2658.0, 2650.0, complete=False),
    ]))
    assert levels["p_h4_h"].key == "h4:high:XAU/USD:2026-09-24T22:00Z"
    assert _contract(levels["p_h4_l"]) == ("htf", "fixed", 3)
    assert _contract(levels["h4_h"]) == ("htf", "forming", 3)


# ── EQ (ліквідність) ──────────────────────────────────────


def test_equal_highs_cluster_is_fixed_liquidity_keyed_by_price():
    bars = [_candle(_M15_S, _D1_OPEN_MS + i * _M15_S * 1000, 1901.0, 1899.0) for i in range(20)]
    swings = [SmcSwing(id=make_swing_id("hh", _SYMBOL, _M15_S, bar.open_time_ms), symbol=_SYMBOL, tf_s=_M15_S,
                       kind="hh", price=price, time_ms=bar.open_time_ms, confirmed=True)
              for bar, price in ((bars[5], 1902.0), (bars[10], 1902.5))]
    config = SmcConfig.from_dict({"levels": {"enabled": True, "tolerance_atr_mult": 0.5, "min_touches": 2}})
    (eq_high,) = detect_liquidity_levels(swings, bars, config, atr=2.0)
    assert eq_high.kind == "eq_highs"
    assert eq_high.key == "eq900:high:XAU/USD:%d" % level_price_key(eq_high.price)
    assert _contract(eq_high) == ("liquidity", "fixed", 3)


# ── сесії (вікна — справжній config.json) ─────────────────


def _session_levels(first_bar: str, now: str, minutes: int = 5) -> dict:
    windows = load_session_windows(
        json.loads((_REPO / "config.json").read_text(encoding="utf-8"))["smc"]["sessions"]["definitions"])
    start_ms = _ms(first_bar)
    bars = [_candle(_M1_S, start_ms + i * _M1_S * 1000, 2650.0 + i, 2640.0 - i) for i in range(minutes)]
    levels, _states = compute_session_levels(bars, windows, _ms(now), _SYMBOL, tf_s=_D1_S)
    return _by_kind(levels)


def test_running_session_is_forming_context_keyed_by_its_open():
    lon_h = _session_levels("2026-09-25 07:00", now="2026-09-25 09:00")["lon_h"]
    assert lon_h.key == "london:high:XAU/USD:2026-09-25T07:00Z"
    assert _contract(lon_h) == ("session", "forming", 3)


def test_session_keeps_its_key_after_close_and_on_the_next_day():
    running = _session_levels("2026-09-25 07:00", now="2026-09-25 09:00")
    closed = _session_levels("2026-09-25 07:00", now="2026-09-25 17:00")
    next_day = _session_levels("2026-09-25 07:00", now="2026-09-26 08:00")
    assert closed["lon_l"].key == running["lon_l"].key == next_day["p_lon_l"].key
    assert _contract(closed["lon_l"]) == ("session", "fixed", 2)
    assert _contract(next_day["p_lon_l"]) == ("session", "fixed", 2)


def test_auto_mode_keeps_current_day_sessions_and_leaves_previous_day_to_research():
    """ADR-0104 §3.5: не шість пар «сьогодні + вчора» — попередня доба лише в Research або закріпленням."""
    during_london = _session_levels("2026-09-25 07:00", now="2026-09-25 09:00")
    next_day = _session_levels("2026-09-25 07:00", now="2026-09-26 08:00")
    assert (during_london["lon_h"].auto, during_london["lon_l"].auto) == (True, True)
    assert (next_day["p_lon_h"].auto, next_day["p_lon_l"].auto) == (False, False)
    assert next_day["p_lon_h"].to_wire()["auto"] is False


def test_session_key_uses_nominal_open_not_a_late_first_bar():
    lon_h = _session_levels("2026-09-25 07:30", now="2026-09-25 09:00")["lon_h"]
    assert lon_h.key == "london:high:XAU/USD:2026-09-25T07:00Z"
    assert lon_h.time_ms == _ms("2026-09-25 07:30")  # t_ms — як і раніше, перший бар сесії


@pytest.mark.parametrize("session_kind, first_bar, now, key", [
    # зима: Лондон відкривається о 08:00 UTC, Нью-Йорк о 13:00 UTC; Азія без переходу — 00:00 UTC
    ("lon_h", "2026-11-10 08:00", "2026-11-10 09:00", "london:high:XAU/USD:2026-11-10T08:00Z"),
    ("ny_h", "2026-11-10 13:00", "2026-11-10 14:00", "newyork:high:XAU/USD:2026-11-10T13:00Z"),
    ("as_h", "2026-11-10 00:00", "2026-11-10 01:00", "asia:high:XAU/USD:2026-11-10T00:00Z"),
])
def test_winter_session_key_follows_exchange_clock(session_kind, first_bar, now, key):
    assert _session_levels(first_bar, now=now)[session_kind].key == key
