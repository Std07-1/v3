"""Наратив зі шляхом ціни (ADR-0113 Ф2a §3.8): ціль від входу (лише не знята), стоп із запасом, підтвердження після
входу не відкочується, відпрацьований сценарій не повертається. Без шляху — поведінка Ф1 (див. golden)."""
from __future__ import annotations

import dataclasses

from core.smc.narrative import synthesize_narrative
from core.smc.zone_path import SHORT, ZonePath
from test_smc_narrative import _default_config, _grade_a6, _level, _snap, _swing, _zone

T0 = 1_791_964_800_000  # open бару зламу, що народив OB
M15_MS = 900_000
BEARISH = {"86400": "bearish", "14400": "bearish"}
ATR = 20.0


def _ob():
    return dataclasses.replace(_zone(kind="ob_bear", high=5225.0, low=5175.0, zone_id="z1", anchor_bar_ms=T0 - M15_MS),
                               origin_event_ms=T0)


def _path(pre_extreme=5120.0, entered_ms=None, post_high=None, post_low=None):
    return ZonePath("z1", SHORT, 5175.0, pre_extreme, entered_ms, post_high, post_low)


def _config(stop_buffer_atr=0.0):
    return dict(_default_config(), zone_path={"enabled": True, "stop_buffer_atr": stop_buffer_atr})


def _narrate(price, levels, swings=(), path=None, config=None):
    zone_paths = {"z1": path} if path is not None else None
    return synthesize_narrative(_snap(zones=[_ob()], levels=list(levels), swings=list(swings)), BEARISH, _grade_a6("z1"),
                                {}, 900, price, ATR, config or _default_config(), price_digits=2, zone_paths=zone_paths)


def test_target_is_beyond_the_move_already_made_not_between_price_and_zone():
    levels = [_level("lon_l", 5130.0, tf_s=900), _level("pdl", 5062.0)]  # 5130 рух до 5120 уже зняв

    without_path = _narrate(5150.0, levels)
    with_path = _narrate(5150.0, levels, path=_path(pre_extreme=5120.0), config=_config())

    assert without_path.scenarios[0].target_desc == "лой Лондона 5130.00"
    assert with_path.scenarios[0].target_desc == "PDL 5062.00"


def test_stop_buffer_moves_invalidation_beyond_far_edge():
    block = _narrate(5150.0, [_level("pdl", 5062.0)], path=_path(), config=_config(stop_buffer_atr=0.5))

    assert block.scenarios[0].invalidation == "Вище 5235.00 — зона пробита"  # 5225 + 0.5 × ATR 20


def test_confirmed_after_entry_does_not_fall_back_to_approaching():
    entered = T0 + 4 * M15_MS
    swings = [_swing("choch_bear", 5170.0, entered + M15_MS), _swing("displacement_bear", 5160.0, entered + M15_MS)]
    path = _path(pre_extreme=5160.0, entered_ms=entered, post_high=5190.0, post_low=5098.0)

    stateless = _narrate(5100.0, [_level("pdl", 5062.0)], swings)  # ціна пішла від зони до цілі
    with_path = _narrate(5100.0, [_level("pdl", 5062.0)], swings, path=path, config=_config())

    assert stateless.scenarios[0].trigger == "approaching"
    assert with_path.scenarios[0].trigger == "triggered"


def test_break_before_entry_is_not_a_confirmation():
    entered = T0 + 4 * M15_MS
    swings = [_swing("choch_bear", 5170.0, entered - M15_MS), _swing("displacement_bear", 5160.0, entered - M15_MS)]
    path = _path(pre_extreme=5160.0, entered_ms=entered, post_high=5190.0, post_low=5150.0)

    assert _narrate(5100.0, [_level("pdl", 5062.0)], swings, path=path, config=_config()).scenarios[0].trigger \
        == "approaching"


def test_scenario_closed_after_entry_on_stop_or_target_does_not_come_back():
    entered = T0 + 4 * M15_MS
    stopped = _narrate(5150.0, [_level("pdl", 5062.0)], path=_path(5160.0, entered, 5226.0, 5150.0), config=_config())
    hit = _narrate(5150.0, [_level("pdl", 5062.0)], path=_path(5160.0, entered, 5190.0, 5061.0), config=_config())
    running = _narrate(5150.0, [_level("pdl", 5062.0)], path=_path(5160.0, entered, 5190.0, 5100.0), config=_config())

    assert stopped.scenarios == [] and "scenario_stopped" in stopped.warnings
    assert hit.scenarios == [] and "scenario_target_hit" in hit.warnings
    assert running.scenarios and running.scenarios[0].zone_id == "z1"
