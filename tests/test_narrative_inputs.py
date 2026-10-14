"""NarrativeInputs — один контракт входу розбору для живого шляху, прогону й golden (ADR-0113 Ф1, R10)."""
from __future__ import annotations

import dataclasses
import inspect
import json

import pytest

from core.smc.narrative import synthesize_narrative
from core.smc.narrative_inputs import NarrativeInputs
from test_narrative_now_ms import WED_LONDON_KZ, WED_OFF_SESSION, SAT_CLOSED, XAU, runner  # noqa: F401 (фікстура)


def test_fields_are_exactly_synthesize_narrative_parameters():
    params = list(inspect.signature(synthesize_narrative).parameters)

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
