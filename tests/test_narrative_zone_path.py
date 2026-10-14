"""Наратив зі шляхом ціни (ADR-0113 Ф2a §3.8): ціль від входу (лише не знята й лише та, що існувала на вході), стоп
із запасом від ATR зони, підтвердження лише після бару входу і без відкату, відпрацьований сценарій не повертається.
Без шляху — поведінка Ф1 (golden + диференційний фазинг рецензії)."""
from __future__ import annotations

import dataclasses

from core.smc.narrative import synthesize_narrative
from core.smc.zone_path import SHORT, ZonePath
from test_smc_narrative import _default_config, _grade_a6, _level, _snap, _swing, _zone

T0 = 1_791_964_800_000  # open бару зламу, що народив OB (TF зони M15)
M15_MS = 900_000
ENTERED = T0 + 4 * M15_MS
AFTER_ENTRY_BAR = ENTERED + M15_MS  # перший момент, коли злам підтверджує вхід
BEARISH = {"86400": "bearish", "14400": "bearish"}
ATR = 20.0


def _ob():
    return dataclasses.replace(_zone(kind="ob_bear", high=5225.0, low=5175.0, zone_id="z1", anchor_bar_ms=T0 - M15_MS),
                               origin_event_ms=T0)


def _path(pre_extreme=5120.0, entered_ms=None, favorable=None, adverse=None, birth_atr=ATR):
    return ZonePath("z1", SHORT, 5175.0, birth_atr, pre_extreme, entered_ms, favorable, adverse)


def _config(stop_buffer_atr=0.0):
    return dict(_default_config(), zone_path={"enabled": True, "stop_buffer_atr": stop_buffer_atr})


def _narrate(price, levels, swings=(), path=None, config=None, atr=ATR):
    zone_paths = {"z1": path} if path is not None else None
    return synthesize_narrative(_snap(zones=[_ob()], levels=list(levels), swings=list(swings)), BEARISH, _grade_a6("z1"),
                                {}, 900, price, atr, config or _default_config(), price_digits=2, zone_paths=zone_paths)


def _confirm(at_ms):
    return [_swing("choch_bear", 5170.0, at_ms), _swing("displacement_bear", 5160.0, at_ms)]


def test_target_is_beyond_the_move_already_made_not_between_price_and_zone():
    levels = [_level("lon_l", 5130.0, tf_s=900), _level("pdl", 5062.0)]  # 5130 рух до 5120 уже зняв

    without_path = _narrate(5150.0, levels)
    with_path = _narrate(5150.0, levels, path=_path(pre_extreme=5120.0), config=_config())

    assert without_path.scenarios[0].target_desc == "лой Лондона 5130.00"
    assert with_path.scenarios[0].target_desc == "PDL 5062.00"


def test_after_entry_target_is_only_liquidity_that_existed_at_entry():
    """Новий «попередній H1», створений рухом після входу, не закриває сценарій «ціллю» (рецензія Ф2a, D-01)."""
    made_after = dataclasses.replace(_level("p_h1_l", 5110.0, tf_s=3600), time_ms=ENTERED + 2 * M15_MS)
    path = _path(5160.0, ENTERED, favorable=5108.0, adverse=5190.0)

    block = _narrate(5115.0, [made_after, _level("pdl", 5062.0)], path=path, config=_config())

    assert block.scenarios and block.scenarios[0].target_desc == "PDL 5062.00"
    assert "scenario_target_hit" not in block.warnings


def test_stop_buffer_scales_with_zone_birth_atr_not_viewer_atr():
    path = _path(birth_atr=20.0)

    on_m15 = _narrate(5150.0, [_level("pdl", 5062.0)], path=path, config=_config(stop_buffer_atr=0.5), atr=20.0)
    on_h1 = _narrate(5150.0, [_level("pdl", 5062.0)], path=path, config=_config(stop_buffer_atr=0.5), atr=40.0)

    assert on_m15.scenarios[0].invalidation == on_h1.scenarios[0].invalidation == "Вище 5235.00 — зона пробита"


def test_price_beyond_stop_invalidates_like_the_stop_text_says():
    """Стоп 5235: ціна 5228 — ще живий сценарій, а не «зону пробито» за краєм 5225 (D-04)."""
    alive = _narrate(5228.0, [_level("pdl", 5062.0)], path=_path(5160.0), config=_config(stop_buffer_atr=0.5))
    broken = _narrate(5236.0, [_level("pdl", 5062.0)], path=_path(5160.0), config=_config(stop_buffer_atr=0.5))

    assert alive.scenarios and "scenario_invalidated" not in alive.warnings
    assert broken.scenarios == [] and "scenario_invalidated" in broken.warnings


def test_confirmed_after_entry_stays_confirmed_and_says_so():
    path = _path(5160.0, ENTERED, favorable=5098.0, adverse=5190.0)
    swings = _confirm(AFTER_ENTRY_BAR) + [_swing("bos_bear", 5120.0, AFTER_ENTRY_BAR + 4 * M15_MS)]  # пізній BOS без імпульсу

    stateless = _narrate(5100.0, [_level("pdl", 5062.0)], swings)  # ціна пішла від зони до цілі
    with_path = _narrate(5100.0, [_level("pdl", 5062.0)], swings, path=path, config=_config())

    assert stateless.scenarios[0].trigger == "approaching"
    assert with_path.scenarios[0].trigger == "triggered"  # пізніший злам без імпульсу не затуляє підтвердження (D-02)
    assert with_path.scenarios[0].trigger_desc == "Підтверджено після входу: M15 CHoCH↓ + імпульс"  # D-12


def test_break_before_or_inside_entry_bar_is_not_a_confirmation():
    """Злам до входу (D-03) чи всередині бару входу (D-11) не підтверджує — навіть біля зони й у зоні."""
    path = _path(5160.0, ENTERED, favorable=5150.0, adverse=5190.0)
    for at_ms in (ENTERED - M15_MS, ENTERED):
        near = _narrate(5160.0, [_level("pdl", 5062.0)], _confirm(at_ms), path=path, config=_config())
        inside = _narrate(5200.0, [_level("pdl", 5062.0)], _confirm(at_ms), path=path, config=_config())
        assert near.scenarios[0].trigger == "approaching"
        assert inside.scenarios[0].trigger == "in_zone"
        assert inside.scenarios[0].trigger_desc.startswith("У зоні: чекаємо")


def test_entered_scenario_is_not_zone_too_far_while_running_to_target():
    path = _path(5160.0, ENTERED, favorable=5050.0, adverse=5190.0)

    block = _narrate(5050.0, [_level("pdl", 4980.0)], _confirm(AFTER_ENTRY_BAR), path=path, config=_config())

    assert block.mode == "trade" and "zone_too_far" not in block.warnings  # 125 від зони > 5 ATR, але вхід уже був


def test_closed_scenario_says_what_happened_and_leaves_next_area():
    entered = ENTERED
    stopped = _narrate(5150.0, [_level("pdl", 5062.0)], path=_path(5160.0, entered, 5150.0, 5226.0), config=_config())
    hit = _narrate(5150.0, [_level("pdl", 5062.0)], path=_path(5160.0, entered, 5061.0, 5190.0), config=_config())
    running = _narrate(5150.0, [_level("pdl", 5062.0)], path=_path(5160.0, entered, 5100.0, 5190.0), config=_config())

    assert stopped.scenarios == [] and "scenario_stopped" in stopped.warnings
    assert stopped.headline == "⬜ Сценарій зупинено стопом — чекаємо нову структуру"
    assert hit.scenarios == [] and "scenario_target_hit" in hit.warnings
    assert hit.headline == "⬜ Ціль сценарію взято — чекаємо нову структуру"
    assert hit.next_area == ""  # закрита зона не подається як «наступна»
    assert running.scenarios and running.scenarios[0].zone_id == "z1"


def test_impulse_extreme_itself_is_the_first_target():
    """Лой імпульсу (екстремум руху до входу) — ліквідність, яку ще не знято: перша ціль; рівень вище нього — знятий."""
    swept = _level("lon_l", 5121.0, tf_s=900)
    at_extreme = _level("as_l", 5120.0, tf_s=900)

    block = _narrate(5150.0, [swept, at_extreme], path=_path(pre_extreme=5120.0), config=_config())

    assert block.scenarios[0].target_desc == "лой Азії 5120.00"
