"""Старший TF глядача — контекст без торгового сценарію (ADR-0113, власник 15.10: «H4/D1 так»).

На H4/D1 трейдер бере напрям, ключову зону й ціль ліквідності, а сетап шукає на TF виконання; торговий сценарій (вхід,
стоп, тригер) лишається на молодших TF. Поведінку вмикає блок `smc.narrative.htf_context`; без нього — як раніше.
"""
from __future__ import annotations

from core.smc.narrative import synthesize_narrative
from test_smc_narrative import _default_config, _grade_a6, _level, _snap, _zone

BEARISH = {"86400": "bearish", "14400": "bearish"}
H4, D1, M15 = 14400, 86400, 900
HTF_CONTEXT = {"viewer_tfs": [H4, D1], "setup_tfs": [M15, 3600]}


def _narrate(viewer_tf_s, levels=(), config=None, bias=None, zone=None):
    return synthesize_narrative(_snap(zones=[zone or _zone()], levels=list(levels)), bias or BEARISH, _grade_a6("z1"),
                                {}, viewer_tf_s, 5150.0, 20.0, config or _config(), price_digits=2)


def _config(htf_context=None):
    return dict(_default_config(), htf_context=HTF_CONTEXT if htf_context is None else htf_context)


def test_h4_viewer_gets_direction_zone_and_liquidity_target_without_a_trade_scenario():
    block = _narrate(H4, [_level("pdl", 5062.0)])

    assert (block.mode, block.sub_mode, block.scenarios) == ("wait", "htf_context", [])
    assert block.headline == ("\U0001f9ed H4: ведмежий контекст — зона продажу OB▼ A(6) 5144.00–5225.00, "
                              "ціль ліквідності PDL 5062.00 — сетап шукаємо на M15–H1")


def test_d1_long_context_and_no_target_part_when_no_liquidity_ahead():
    bullish = {"86400": "bullish", "14400": "bullish"}

    block = _narrate(D1, bias=bullish, zone=_zone(kind="ob_bull", low=5100.0, high=5140.0))

    assert block.sub_mode == "htf_context"
    assert block.headline == ("\U0001f9ed D1: бичачий контекст — зона купівлі OB▲ A(6) 5100.00–5140.00 "
                              "— сетап шукаємо на M15–H1")


def test_execution_tfs_keep_the_trade_scenario():
    block = _narrate(M15, [_level("pdl", 5062.0)])

    assert block.mode == "trade" and block.scenarios and block.scenarios[0].target_desc == "PDL 5062.00"


def test_without_the_config_block_h4_is_a_trade_scenario_as_before():
    without = synthesize_narrative(_snap(zones=[_zone()], levels=[_level("pdl", 5062.0)]), BEARISH, _grade_a6("z1"),
                                   {}, H4, 5150.0, 20.0, _default_config(), price_digits=2)
    rolled_back = _narrate(H4, [_level("pdl", 5062.0)], config=_config(htf_context={"viewer_tfs": []}))

    assert without.mode == rolled_back.mode == "trade" and without.scenarios


def test_wait_states_on_h4_keep_their_headlines():
    """Контекст замінює лише торговий сценарій: «контртренд», «зона далеко» тощо — як були."""
    counter = _narrate(H4, bias={"86400": "bullish", "14400": "bullish"})

    assert counter.sub_mode == "counter" and counter.headline.startswith("\U0001f7e0 Контртренд")


def test_context_is_structured_for_the_shell_but_stays_off_the_wire():
    block = _narrate(H4, [_level("pdl", 5062.0)])

    assert block.htf_context is not None
    assert (block.htf_context.zone_desc, block.htf_context.target_desc, block.htf_context.setup_desc) == (
        "зона продажу OB\u25bc A(6) 5144.00\u20135225.00", "PDL 5062.00", "сетап шукаємо на M15\u2013H1")
    assert block.htf_context.invalidation == "Вище 5225.00 \u2014 зона пробита"
    assert "htf_context" not in block.to_wire()
    assert _narrate(M15, [_level("pdl", 5062.0)]).htf_context is None
