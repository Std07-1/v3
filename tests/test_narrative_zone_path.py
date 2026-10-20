"""Наратив зі шляхом ціни (ADR-0113 Ф2a §3.8): ціль від входу — найближча незнята ліквідність (детектор «знята / не
знята»), після входу — лише та, що утворилась до входу; стоп із запасом від ATR зони; підтвердження лише після бару
входу; закриття — лише стопом (монотонно й однаково на всіх TF). Без шляху — поведінка Ф1 (golden + диференційний
фазинг рецензій)."""
from __future__ import annotations

import dataclasses

from core.smc.narrative import synthesize_narrative
from core.smc.zone_path import SHORT, ZonePath
from test_smc_narrative import _default_config, _grade_a6, _level, _snap, _swing, _zone

T0 = 1_791_964_800_000  # open бару зламу, що народив OB (TF зони M15)
M15_MS = 900_000
H1_MS = 3_600_000
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


def _narrate(price, levels, swings=(), path=None, config=None, atr=ATR, reached=None):
    zone_paths = {"z1": path} if path is not None else None
    return synthesize_narrative(_snap(zones=[_ob()], levels=list(levels), swings=list(swings)), BEARISH,
                                _grade_a6("z1"), {}, 900, price, atr, config or _default_config(), price_digits=2,
                                zone_paths=zone_paths, liquidity_reached=reached)


def _swept_below(level, at_ms):
    """Ціна торгувала нижче рівня в момент at_ms — для short-цілі рівень знято."""
    return {"level:" + level.id: (None, at_ms)}


def _confirm(at_ms):
    return [_swing("choch_bear", 5170.0, at_ms), _swing("displacement_bear", 5160.0, at_ms)]


def test_target_is_nearest_unswept_liquidity_not_one_the_move_already_took():
    taken = _level("lon_l", 5130.0, tf_s=900)  # рух до 5120 пройшов нижче 5130 після того, як рівень утворився
    levels = [taken, _level("pdl", 5062.0)]

    without_path = _narrate(5150.0, levels)
    with_path = _narrate(5150.0, levels, path=_path(), config=_config(), reached=_swept_below(taken, T0 + M15_MS))

    assert without_path.scenarios[0].target_desc == "лой Лондона 5130.00"
    assert with_path.scenarios[0].target_desc == "PDL 5062.00"


def test_old_zone_targets_unswept_level_formed_on_the_retrace_not_the_far_old_extreme():
    """Стара зона: давній екстремум 4980, а на відкаті утворився «лой попер. H4» 5100, якого ціна не брала — він і ціль."""
    retrace_low = dataclasses.replace(_level("p_h4_l", 5100.0, tf_s=14400), time_ms=T0 + 40 * M15_MS)

    block = _narrate(5150.0, [retrace_low], path=_path(pre_extreme=4980.0), config=_config())

    assert block.scenarios[0].target_desc == "лой поп. H4 5100.00"


def test_impulse_extreme_is_the_target_when_no_unswept_liquidity_object_beyond():
    """Варіант (а) власника 14.10: незнятої ліквідності немає — ціль сам лой імпульсу (строго попереду входу, E-02)."""
    alone = _narrate(5150.0, [], path=_path(pre_extreme=5120.0), config=_config())
    too_far = _narrate(5150.0, [], path=_path(pre_extreme=4900.0), config=_config())  # 275 > 12 × ATR 20 від 5175
    inside_zone = _narrate(5150.0, [], path=_path(pre_extreme=5180.0), config=_config())  # «екстремум» за краєм входу

    assert alone.scenarios[0].target_desc == "лой імпульсу 5120.00"
    assert too_far.scenarios[0].target_desc is None and "no_target_found" in too_far.warnings
    assert inside_zone.scenarios[0].target_desc is None


def test_after_entry_target_is_nearest_unswept_liquidity_even_one_the_move_made():
    """Після входу ціль — найближча незнята ліквідність попереду, і та, що зробив рух після входу (хай/лой відкату):
    закриття лише стопом, тож ціль — «куди далі», а не подія (S3f; стара зона XAG H1 25.05 — «хай попер. H4 78.676»)."""
    made_after = dataclasses.replace(_level("p_h1_l", 5110.0, tf_s=3600), time_ms=ENTERED)
    path = _path(5160.0, ENTERED, favorable=5112.0, adverse=5190.0)

    block = _narrate(5115.0, [made_after, _level("pdl", 5062.0)], path=path, config=_config())

    assert block.scenarios[0].target_desc == "лой поп. H1 5110.00"


def test_taken_target_gives_way_to_the_next_unswept():
    """Ціль, яку ціна вже взяла, — позаду: наступна незнята (сценарій від цього не закривається)."""
    first = dataclasses.replace(_level("p_h4_l", 5100.0, tf_s=14400), time_ms=T0 - 10 * H1_MS)
    path = _path(5160.0, ENTERED, favorable=5095.0, adverse=5190.0)
    taken = _swept_below(first, ENTERED + 2 * M15_MS)

    before = _narrate(5120.0, [first, _level("pdl", 5062.0)], path=path, config=_config())
    after = _narrate(5120.0, [first, _level("pdl", 5062.0)], path=path, config=_config(), reached=taken)

    assert before.scenarios[0].target_desc == "лой поп. H4 5100.00"
    assert after.scenarios and after.scenarios[0].target_desc == "PDL 5062.00"


def test_stop_buffer_scales_with_zone_birth_atr_and_invalidates_by_the_same_number():
    path = _path(birth_atr=20.0)
    cfg = _config(stop_buffer_atr=0.5)

    on_m15 = _narrate(5150.0, [_level("pdl", 5062.0)], path=path, config=cfg, atr=20.0)
    on_h1 = _narrate(5150.0, [_level("pdl", 5062.0)], path=path, config=cfg, atr=40.0)
    alive = _narrate(5228.0, [_level("pdl", 5062.0)], path=path, config=cfg)  # за краєм 5225, до стопу 5235
    broken = _narrate(5236.0, [_level("pdl", 5062.0)], path=path, config=cfg)

    assert on_m15.scenarios[0].invalidation == on_h1.scenarios[0].invalidation == "Вище 5235.00 — зона пробита"
    assert alive.scenarios and "OB▼" in alive.next_area  # D-04/E-12: ще жива — і в next_area
    assert broken.scenarios == [] and "scenario_invalidated" in broken.warnings


def test_confirmed_after_entry_stays_confirmed_and_headline_says_so():
    path = _path(5160.0, ENTERED, favorable=5098.0, adverse=5190.0)
    swings = _confirm(AFTER_ENTRY_BAR) + [_swing("bos_bear", 5120.0, AFTER_ENTRY_BAR + 4 * M15_MS)]  # пізній BOS без імпульсу

    stateless = _narrate(5100.0, [_level("pdl", 5062.0)], swings)  # ціна пішла від зони до цілі
    with_path = _narrate(5100.0, [_level("pdl", 5062.0)], swings, path=path, config=_config())

    assert stateless.scenarios[0].trigger == "approaching"
    assert with_path.scenarios[0].trigger == "triggered"  # пізніший злам без імпульсу не затуляє підтвердження (D-02)
    assert with_path.scenarios[0].trigger_desc == "Підтверджено після входу: M15 CHoCH↓ + імпульс"  # D-12
    assert with_path.headline == "\U0001f534 SELL — підтверджено після входу"  # E-05: без «шукаємо вхід»


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


def test_only_the_stop_closes_a_scenario_monotonic_and_silently():
    """Закриття — лише стопом після входу: без заголовка про подію, якої без пам'яті не довести (E-06/E-07)."""
    stopped = _narrate(5150.0, [_level("pdl", 5062.0)], path=_path(5160.0, ENTERED, 5150.0, 5226.0), config=_config())
    reached_target = _narrate(5070.0, [_level("pdl", 5062.0), _level("pwl", 5000.0)],
                              path=_path(5160.0, ENTERED, 5061.0, 5190.0), config=_config())

    assert stopped.scenarios == [] and "scenario_stopped" in stopped.warnings
    assert "Сценарій" not in stopped.headline and "Зону пробито" not in stopped.headline
    assert stopped.next_area == ""  # закрита зона не подається як «наступна»
    assert reached_target.scenarios and reached_target.scenarios[0].zone_id == "z1"  # ціль — не закриття


def test_zone_with_birth_anchor_but_no_path_is_not_a_trade_candidate():
    """Бар зламу поза вікном барів рушія — шлях невідомий; без нього зона «воскресала» б за правилами Ф1 (E-11)."""
    block = synthesize_narrative(_snap(zones=[_ob()], levels=[_level("pdl", 5062.0)]), BEARISH, _grade_a6("z1"), {},
                                 900, 5150.0, ATR, _config(), price_digits=2, zone_paths={}, liquidity_reached={})

    assert block.scenarios == [] and "scenario_path_unknown" in block.warnings


def test_break_after_the_finer_touch_inside_the_entry_bar_confirms():
    """E-03: момент дотику з дрібнішого ряду (`touch_end_ms`) — злам після нього підтверджує, навіть усередині бару
    входу на TF зони; без нього — як D-11, лише після кінця бару входу."""
    touch_end = ENTERED + 300_000  # кінець M5, що торкнувся
    swings = _confirm(touch_end)
    coarse = _path(5160.0, ENTERED, favorable=5098.0, adverse=5190.0)
    fine = dataclasses.replace(coarse, touch_end_ms=touch_end)


    def trigger(confirm_swings, path):
        block = _narrate(5100.0, [_level("pdl", 5062.0)], confirm_swings, path=path, config=_config())
        return block.scenarios[0].trigger

    assert trigger(swings, coarse) == "approaching"
    assert trigger(swings, fine) == "triggered"
    assert trigger(_confirm(touch_end - 60_000), fine) == "approaching"  # злам до дотику — не підтвердження
