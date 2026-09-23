"""План S7.1 (ADR-0095): прогін `build_plan` — усі області разом, JSON плану, відмови до планування, ідемпотентність.

Області перетинаються (D1 поза сіткою в епосі M1 перебудовує і `derived_from_m1`), тож набір — об'єднання зерен, а
новий бар кожного бакета будується один раз. План несе ревізію коду і все, що треба для staging і гейтів S7.2,
крім самих байтів (вони — у `FilePlan.new_bytes`, у JSON не йдуть).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.session_anchor import D1_S, H4_S
from season_plan_synthetic import CFG, append_rows, dataset, history_row, ms
from tools.repair import season_plan as sp

ALL_SCOPES = list(sp.SCOPES)


def apply(plan: sp.SeasonPlan) -> None:
    for file_plan in plan.files:
        Path(file_plan.path).parent.mkdir(parents=True, exist_ok=True)
        Path(file_plan.path).write_bytes(file_plan.new_bytes)


def test_all_scopes_plan_is_json_serialisable_and_second_plan_is_empty(tmp_path):
    dataset(tmp_path)
    plan = sp.build_plan(CFG, str(tmp_path), ["XAU/USD"], ALL_SCOPES)
    (symbol_plan,) = plan.symbols
    doc = json.loads(json.dumps(plan.to_json()))
    assert doc["scopes"] == ALL_SCOPES and len(doc["git_rev"]) in (40, len("unknown"))
    assert doc["symbols"][0]["rule"] == "ny_close_us_dst" and doc["symbols"][0]["season_rule"] == "us"
    assert {f["path"] for f in doc["symbols"][0]["files"]} == {f.to_json()["path"] for f in plan.files}
    assert symbol_plan.rekey_results() == [{"old_open_ms": ms(2026, 3, 4, 23), "new_open_ms": ms(2026, 3, 4, 22),
                                            "src": "derived", "ohlcv_equal": False, "thin_session": False}]
    h4_new = {k for k, bar in symbol_plan.planned[H4_S].items() if bar is not None}
    assert ms(2026, 2, 23, 22) in h4_new and ms(2026, 3, 4, 22) in h4_new, "H4 до M1 з H1 і H4 епохи M1"
    assert symbol_plan.rows[D1_S][sp.ROW_DROPPED] + symbol_plan.rows[D1_S][sp.ROW_OFF_GRID] == 1
    assert {Path(f.path).parent.name for f in plan.files} == {"tf_14400", "tf_86400"}, "M3..H1 уже f(M1)"
    apply(plan)
    again = sp.build_plan(CFG, str(tmp_path), ["XAU/USD"], ALL_SCOPES)
    assert again.files == [] and again.symbols[0].d1_rekey == []


def test_changed_m1_mapping_is_by_symbol_directory_and_absent_symbol_plans_nothing(tmp_path):
    dataset(tmp_path)
    changed = ms(2026, 3, 5, 14, 37)
    plan = sp.build_plan(CFG, str(tmp_path), ["XAU/USD"], ["derived_from_m1"], changed_m1={"XAU_USD": [changed]})
    assert plan.changed_m1 and set(plan.symbols[0].rebuild[H4_S]) == {ms(2026, 3, 5, 14)}
    empty = sp.build_plan(CFG, str(tmp_path), ["XAU/USD"], ["derived_from_m1"], changed_m1={"XAG_USD": [changed]})
    assert empty.symbols[0].rebuild == {} and empty.files == []


def test_refusals_happen_before_any_planning(tmp_path):
    with pytest.raises(ValueError, match="SEASON_PLAN_SCOPE_INVALID"):
        sp.build_plan(CFG, str(tmp_path), ["XAU/USD"], ["h4"])
    with pytest.raises(ValueError, match="SEASON_PLAN_CHANGED_M1_WITHOUT_SCOPE"):
        sp.build_plan(CFG, str(tmp_path), ["XAU/USD"], ["holes"], changed_m1={})
    cfg = dict(CFG, market_calendar_symbol_groups={"XAU/USD": "cfd_us_22_23", "HKG33": "cfd_hk_main"})
    with pytest.raises(ValueError, match="HTF_ANCHOR_GROUP_UNMEASURED"):
        sp.build_plan(cfg, str(tmp_path), ["XAU/USD", "HKG33"], ["derived_from_m1"])


def test_dropped_buckets_are_split_by_trading_minutes(tmp_path):
    dataset(tmp_path)
    append_rows(tmp_path, H4_S, [history_row(H4_S, ms(2026, 2, 28, 2), 1.0)])  # субота: бакет без жодної торгової хвилини
    plan = sp.build_plan(CFG, str(tmp_path), ["XAU/USD"], ["h4_from_h1"])
    no_trading, no_source = plan.symbols[0].dropped(H4_S)
    assert ms(2026, 2, 28, 2) in no_trading
    assert ms(2026, 3, 2, 10) in no_source, "торговий понеділок без H1 на диску — гучно, не мовчки"
    assert ms(2026, 3, 3, 22) not in no_source, "бакет з першою M1 будується з H1 епохи M1"
