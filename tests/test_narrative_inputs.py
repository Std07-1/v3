"""NarrativeInputs — один контракт входу розбору для живого шляху, прогону й golden (ADR-0113 Ф1, R10)."""
from __future__ import annotations

import dataclasses
import inspect
import json

import pytest

from core.model.bars import CandleBar
from core.smc.narrative import synthesize_narrative
from core.smc.narrative_inputs import NarrativeInputs
from core.smc.types import SmcZone
from core.smc.zone_path import zone_path
from test_narrative_now_ms import WED_LONDON_KZ, WED_OFF_SESSION, SAT_CLOSED, XAU, runner  # noqa: F401 (фікстура)


def test_fields_are_exactly_synthesize_narrative_parameters():
    # Мова (`tr`, ADR-0116) — не вхід розбору: режим, сценарії й числа від неї не залежать
    params = [name for name in inspect.signature(synthesize_narrative).parameters if name != "tr"]

    assert [field.name for field in dataclasses.fields(NarrativeInputs)] == params  # as_kwargs — єдине відображення


@pytest.mark.parametrize("now_ms", [WED_LONDON_KZ, WED_OFF_SESSION])
def test_get_narrative_is_synthesis_of_its_inputs(runner, now_ms):  # noqa: F811
    inputs = runner.get_narrative_inputs(XAU, 900, 4110.0, 0.0, now_ms=now_ms)

    assert inputs is not None
    assert runner.get_narrative(XAU, 900, 4110.0, 0.0, now_ms=now_ms) == inputs.synthesize()


def test_no_inputs_when_market_closed(runner):  # noqa: F811
    assert runner.get_narrative_inputs(XAU, 900, 4110.0, 0.0, now_ms=SAT_CLOSED) is None


def test_dict_round_trip_is_lossless_through_json(runner):  # noqa: F811
    inputs = runner.get_narrative_inputs(XAU, 900, 4110.0, 0.0, now_ms=WED_LONDON_KZ)
    assert inputs.snapshot.zones and inputs.snapshot.levels and inputs.session_info == ("london", True)
    # поле, якого немає у to_wire, теж має пережити серіалізацію
    zone = dataclasses.replace(inputs.snapshot.zones[0], origin_event_ms=123)
    inputs = dataclasses.replace(inputs, snapshot=dataclasses.replace(
        inputs.snapshot, zones=[zone] + list(inputs.snapshot.zones[1:])))

    restored = NarrativeInputs.from_dict(json.loads(json.dumps(inputs.to_dict())))

    assert restored == inputs
    assert restored.synthesize() == inputs.synthesize()


def test_range_exhaustion_state_refuses_serialization(runner):  # noqa: F811
    inputs = runner.get_narrative_inputs(XAU, 900, 4110.0, 0.0, now_ms=WED_LONDON_KZ)

    with pytest.raises(ValueError, match="range_exhaustion"):
        dataclasses.replace(inputs, range_exhaustion=object()).to_dict()


def test_zone_paths_only_when_enabled_and_survive_round_trip(runner):  # noqa: F811
    runner._full_config["smc"]["narrative"]["zone_path"] = {"enabled": False, "stop_buffer_atr": 0.0}  # не з config.json
    assert runner.get_narrative_inputs(XAU, 900, 4110.0, 0.0, now_ms=WED_LONDON_KZ).zone_paths is None

    runner._full_config["smc"]["narrative"]["zone_path"] = {"enabled": True, "stop_buffer_atr": 0.0}
    inputs = runner.get_narrative_inputs(XAU, 900, 4110.0, 0.0, now_ms=WED_LONDON_KZ)
    bars = runner._engine.get_bars(XAU, 900)
    ob = SmcZone(id="ob_test", symbol=XAU, tf_s=900, kind="ob_bull", start_ms=bars[-20].open_time_ms, end_ms=None,
                 high=bars[-20].low + 1.0, low=bars[-20].low, status="active", strength=0.8,
                 anchor_bar_ms=bars[-20].open_time_ms, origin_event_ms=bars[-19].open_time_ms)
    with_ob = dataclasses.replace(inputs, snapshot=dataclasses.replace(inputs.snapshot, zones=[ob]))

    paths = runner._zone_paths(XAU, with_ob.snapshot)

    assert inputs.zone_paths == {}  # у синтетичному знімку OB немає — FVG і P/D якоря народження не мають
    path = paths["ob_test"]
    assert paths == {"ob_test": dataclasses.replace(zone_path(ob, bars), touch_end_ms=path.touch_end_ms)}
    assert path.entered_ms < path.touch_end_ms <= path.entered_ms + 900_000  # момент дотику — з M5 у бакеті M15
    with_paths = dataclasses.replace(with_ob, zone_paths=paths)
    assert NarrativeInputs.from_dict(json.loads(json.dumps(with_paths.to_dict()))) == with_paths


def test_zone_paths_see_entry_in_forming_zone_bar_from_closed_m5(runner, monkeypatch):  # noqa: F811
    """E-03: H1-зону торкнулись M5 після останнього закритого H1 — вхід видно зараз, з моментом дотику на M5."""
    h1, m5 = 3_600_000, 300_000
    origin = WED_LONDON_KZ - 3 * h1 - WED_LONDON_KZ % h1

    def bar(tf_ms, open_ms, high, low):
        return CandleBar(XAU, tf_ms // 1000, open_ms, open_ms + tf_ms, low, high, low, high, 1.0, True, "history")

    h1_bars = [bar(h1, origin + i * h1, 4120.0 + i, 4108.0 + i) for i in range(-15, 3)]  # закриті H1 до бакета 3
    m5_bars = [bar(m5, origin + 3 * h1 + i * m5, 4115.0, 4106.0 if i == 4 else 4111.0) for i in range(6)]
    bars = {3600: h1_bars, 300: m5_bars}
    monkeypatch.setattr(runner._engine, "get_bars", lambda symbol, tf_s: bars.get(tf_s, []))
    ob = SmcZone(id="ob_h1", symbol=XAU, tf_s=3600, kind="ob_bull", start_ms=origin - h1, end_ms=None, high=4107.0,
                 low=4100.0, status="active", strength=0.8, anchor_bar_ms=origin - h1, origin_event_ms=origin)
    inputs = runner.get_narrative_inputs(XAU, 900, 4110.0, 0.0, now_ms=WED_LONDON_KZ)

    path = runner._zone_paths(XAU, dataclasses.replace(inputs.snapshot, zones=[ob]))["ob_h1"]

    assert zone_path(ob, h1_bars).entered_ms is None  # лише закриті H1 — входу ще не видно
    assert path.entered_ms == origin + 3 * h1 and path.adverse_since_entry == 4106.0
    assert path.touch_end_ms == origin + 3 * h1 + 5 * m5  # кінець M5 №4, а не кінець бакета H1
