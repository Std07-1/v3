"""Розбір символу — композиція наративів TF глядача (ADR-0113 Ф3, власник 15.10: «Так, беремо»).

Розбір не судить ринок сам: напрям — з bias_map, контекст H4/D1 — з контексту наративу, сетапи M15/H1 — зі сценаріїв
наративу з тими самими числами стопу й цілі. Кожен рядок каже, з якого він TF.
"""
from __future__ import annotations

import json

from core.smc.briefing import _plain, closed_briefing, compose_briefing, reward_to_risk
from core.smc.narrative import synthesize_narrative
from test_smc_narrative import _default_config, _grade_a6, _level, _snap, _zone

M15, H1, H4, D1 = 900, 3600, 14400, 86400
STRIP = [D1, H4, H1, M15]
BEARISH = {"86400": "bearish", "14400": "bearish", "3600": "bearish", "900": "bearish"}
CONFIG = dict(_default_config(), htf_context={"viewer_tfs": [H4, D1], "setup_tfs": [M15, H1]})


def _narrate(viewer_tf_s, zone=None, bias=None, price=5180.0):
    return synthesize_narrative(_snap(zones=[zone or _zone()], levels=[_level("pdl", 5062.0)]), bias or BEARISH,
                                _grade_a6("z1"), {}, viewer_tf_s, price, 20.0, CONFIG, price_digits=2)


def _brief(narratives, bias=None):
    return compose_briefing("XAU/USD", 1_000, BEARISH if bias is None else bias, narratives, STRIP, [H4, D1], [M15, H1], 2)


def _section(briefing, key):
    return next(section for section in briefing.sections if section.key == key)


def test_scenario_carries_the_numbers_of_its_texts_off_the_wire():
    scenario = _narrate(M15).scenarios[0]

    assert (scenario.entry_edge, scenario.stop_price, scenario.target_price, scenario.zone_tf_s) == (
        5144.0, 5225.0, 5062.0, M15)
    assert scenario.target_desc == "PDL 5062.00" and scenario.invalidation == "Вище 5225.00 — зона пробита"
    assert set(scenario.to_wire()) == {"zone_id", "direction", "entry_desc", "trigger", "trigger_desc",
                                       "target_desc", "invalidation"}


def test_bias_row_lists_every_strip_tf_and_says_htf_alignment():
    aligned = _brief({}).sections[0].rows[0]
    mixed = _brief({}, bias={"86400": "bearish", "14400": "bullish", "900": "bullish"}).sections[0].rows[0]

    assert (aligned.text, aligned.notes, aligned.tone) == (
        "D1 ↓ · H4 ↓ · H1 ↓ · M15 ↓", ("D1 і H4 вниз — старший напрям узгоджений",), "bear")
    assert mixed.text == "D1 ↓ · H4 ↑ · H1 — · M15 ↑"
    assert mixed.notes == ("D1 вниз, H4 вгору — старший напрям змішаний · «—» — TF ще без структури",)
    assert mixed.tone == "warn"


def test_bias_row_partial_and_without_htf_structure():
    partial = _brief({}, bias={"14400": "bullish"}).sections[0].rows[0]
    empty = _brief({}, bias={}).sections[0].rows[0]

    assert partial.notes[0].startswith("H4 вгору, D1 ще без структури")
    assert empty.notes[0].startswith("D1 і H4 ще без структури") and empty.tone == ""


def test_context_rows_take_zone_target_and_boundary_from_the_htf_context_d1_first():
    rows = _section(_brief({H4: _narrate(H4), D1: None}), "context").rows

    assert [row.tf for row in rows] == ["D1", "H4"]
    assert (rows[1].text, rows[1].notes, rows[1].tone) == (
        "Ведмежий контекст — зона продажу OB▼ A(6) 5144.00–5225.00",
        ("Ціль ліквідності: PDL 5062.00", "Вище 5225.00 — зона пробита"), "bear")
    assert rows[0].text == "Розбору цього TF ще немає" and rows[0].tone == "warn"


def test_context_row_without_context_says_the_narrative_state_and_nearest_zone():
    counter = _narrate(H4, bias={"86400": "bullish", "14400": "bullish"})

    row = _section(_brief({H4: counter, D1: counter}), "context").rows[1]

    assert row.text == "Контртренд — зона є, але проти HTF"
    assert row.notes == ("Найближча зона: " + counter.next_area,) and row.tone == ""


def test_setup_row_has_zone_state_stop_target_and_reward_to_risk_from_the_zone_edge():
    rows = _section(_brief({M15: _narrate(M15), H1: _narrate(H1)}), "setups").rows

    assert [row.tf for row in rows] == ["H1", "M15"]
    m15 = rows[1]
    assert m15.text == "SELL · OB▼ A(6) 5144.00–5225.00"
    assert m15.notes == ("У зоні: чекаємо M15 CHoCH↓", "Стоп 5225.00 · ціль PDL 5062.00 · R:R 1.0 від краю зони")
    assert m15.tone == "bear"


def test_setup_row_names_a_zone_of_another_tf_and_approach_state():
    zone = _zone(tf_s=H1, low=5144.0, high=5160.0)

    row = _section(_brief({M15: _narrate(M15, zone=zone, price=5130.0), H1: None}), "setups").rows[1]

    assert row.text == "SELL · OB▼ A(6) 5144.00–5160.00 (зона H1)"
    assert row.notes[0] == "Наближення — 0.7 ATR до зони"


def test_reward_to_risk_from_the_entry_edge_and_none_without_a_number_or_a_target_ahead():
    assert reward_to_risk("long", 100.0, 98.0, 106.0) == 3.0
    assert reward_to_risk("short", 100.0, 102.0, 95.0) == 2.5
    assert reward_to_risk("long", 100.0, 98.0, None) is None
    assert reward_to_risk("long", 100.0, 98.0, 99.0) is None  # ціль не попереду краю входу


def test_missing_narratives_are_loud_and_the_briefing_is_json():
    briefing = _brief({M15: _narrate(M15)})

    assert briefing.warnings == ("narrative_unavailable:H4", "narrative_unavailable:D1", "narrative_unavailable:H1")
    wire = json.loads(json.dumps(briefing.to_wire(), ensure_ascii=False))
    assert [section["key"] for section in wire["sections"]] == ["bias", "context", "setups"]
    assert wire["symbol"] == "XAU/USD" and wire["as_of_ms"] == 1_000


def test_closed_market_briefing_says_so():
    wire = closed_briefing("GER30", 5).to_wire()

    assert wire["sections"][0]["rows"][0]["text"] == "Ринок закрито — розбір оновиться з відкриттям"
    assert wire["warnings"] == ["market_closed"]


def test_plain_drops_only_a_leading_state_marker():
    assert _plain("⬜ Немає зони рівня A — чекаємо сетап") == "Немає зони рівня A — чекаємо сетап"
    assert _plain("BUY — у зоні") == "BUY — у зоні"


def _thin_zone_plan(atr):
    thin = _zone(low=5144.0, high=5146.0)  # ризик 2 пункти до цілі PDL 5062 — R:R 41
    briefing = compose_briefing("XAU/USD", 1_000, BEARISH, {M15: _narrate(M15, zone=thin, price=5145.0)}, STRIP, [],
                                [M15], 2, atr_by_tf={M15: atr}, rr_min_risk_atr=1.0)
    return _section(briefing, "setups").rows[0].notes[1]


def test_thin_zone_reward_to_risk_is_hidden_by_the_noise_threshold_not_capped():
    """Власник 16.10: лише поріг 1 ATR — R:R 41 на зоні в 2 пункти при ATR 20 не показується зовсім."""
    assert _thin_zone_plan(20.0) == "Стоп 5146.00 · ціль PDL 5062.00 · R:R не показано — стоп ближче 1 ATR M15"


def test_far_target_behind_a_wide_enough_stop_keeps_its_number_even_above_ten():
    """Стоп не ближче 1 ATR — R:R понад 10 означає далеку ціль, а не вузьку зону: показується числом."""
    assert _thin_zone_plan(2.0) == "Стоп 5146.00 · ціль PDL 5062.00 · R:R 41.0 від краю зони"


def _m15_plan(atr_by_tf, rr_min_risk_atr):
    briefing = compose_briefing("XAU/USD", 1_000, BEARISH, {M15: _narrate(M15)}, STRIP, [], [M15], 2,
                                atr_by_tf=atr_by_tf, rr_min_risk_atr=rr_min_risk_atr)
    return _section(briefing, "setups").rows[0].notes[1]


def test_reward_to_risk_is_hidden_when_the_stop_sits_inside_one_bar_of_noise():
    """Ризик від краю входу 5144 до стопу 5225 — 81: проти ATR M15 100 і порогу 1.0 це шум бару — R:R не показується."""
    assert _m15_plan({M15: 100.0}, 1.0) == "Стоп 5225.00 · ціль PDL 5062.00 · R:R не показано — стоп ближче 1 ATR M15"
    assert _m15_plan({M15: 100.0}, 0.5) == "Стоп 5225.00 · ціль PDL 5062.00 · R:R 1.0 від краю зони"


def test_reward_to_risk_stays_when_atr_is_not_measured_or_threshold_is_off():
    shown = "Стоп 5225.00 · ціль PDL 5062.00 · R:R 1.0 від краю зони"

    assert _m15_plan({}, 1.0) == shown
    assert _m15_plan({M15: 100.0}, 0.0) == shown
