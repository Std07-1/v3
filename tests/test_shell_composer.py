"""
tests/test_shell_composer.py — Shell Composer unit tests (ADR-0036 §5.2).

Covers: stage mapping (9 rows), error guard, sessions guard,
D1 conflict downgrade, tactical strip, micro card, edge cases.
"""

from __future__ import annotations

import pytest

from core.smc.types import (
    ActiveScenario,
    MicroCard,
    NarrativeBlock,
    ShellPayload,
    TacticalStrip,
    TfChip,
)
from core.smc.shell_composer import compose_shell_payload


# ── Helpers ──────────────────────────────────────────────────


def _make_narrative(
    mode: str = "wait",
    sub_mode: str = "",
    scenarios: list | None = None,
    current_session: str = "",
    in_killzone: bool = False,
    warnings: list | None = None,
    bias_summary: str = "Bearish D1+H4",
) -> NarrativeBlock:
    return NarrativeBlock(
        mode=mode,
        sub_mode=sub_mode,
        headline="test",
        bias_summary=bias_summary,
        scenarios=scenarios or [],
        next_area="",
        fvg_context="",
        market_phase="ranging",
        warnings=warnings or [],
        current_session=current_session,
        in_killzone=in_killzone,
    )


def _scenario(
    direction: str = "short",
    trigger: str = "approaching",
    trigger_desc: str = "CHoCH на M15",
    invalidation: str = "Above 2880",
) -> ActiveScenario:
    return ActiveScenario(
        zone_id="ob_bear_XAU/USD_900_1000",
        direction=direction,
        entry_desc="OB▼ A(7) 2860–2870",
        trigger=trigger,
        trigger_desc=trigger_desc,
        target_desc="2840",
        invalidation=invalidation,
    )


_ALIGNED_BIAS = {
    "86400": "bearish",
    "14400": "bearish",
    "3600": "bearish",
    "900": "bearish",
}
_MIXED_BIAS = {
    "86400": "bearish",
    "14400": "bearish",
    "3600": "bullish",
    "900": "bearish",
}
_D1_CFL_BIAS = {
    "86400": "bullish",
    "14400": "bearish",
    "3600": "bearish",
    "900": "bearish",
}
_CFG: dict = {}


# ── Stage Mapping Tests (§5.2.1, Rows 1-9) ──────────────────


class TestStageMapping:
    """Tests for the 9-row stage mapping table in ADR-0036 §5.2.1."""

    def test_row1_wait_no_scenarios_off_session_stayout(self):
        """Row 1: wait, no scenarios, sessions_active, no session -> stayout."""
        narr = _make_narrative(mode="wait", current_session="")
        result = compose_shell_payload(
            narr, _ALIGNED_BIAS, 900, _CFG, sessions_active=True
        )
        assert result.stage == "stayout"
        assert result.stage_label == "STAY OUT"

    def test_row2_wait_no_scenarios_sessions_disabled_wait(self):
        """Row 2: wait, no scenarios, sessions NOT active -> wait (not stayout)."""
        narr = _make_narrative(mode="wait", current_session="")
        result = compose_shell_payload(
            narr, _ALIGNED_BIAS, 900, _CFG, sessions_active=False
        )
        assert result.stage == "wait"

    def test_row3_wait_no_scenarios_in_session_wait(self):
        """Row 3: wait, no scenarios, in session -> wait."""
        narr = _make_narrative(mode="wait", current_session="london")
        result = compose_shell_payload(
            narr, _ALIGNED_BIAS, 900, _CFG, sessions_active=True
        )
        assert result.stage == "wait"

    def test_row4_wait_with_scenarios_wait(self):
        """Row 4: wait with scenarios -> wait (not prepare)."""
        narr = _make_narrative(mode="wait", scenarios=[_scenario()])
        result = compose_shell_payload(
            narr, _ALIGNED_BIAS, 900, _CFG, sessions_active=True
        )
        assert result.stage == "wait"

    def test_row5_trade_reduced_prepare(self):
        """Row 5: trade+reduced -> prepare."""
        narr = _make_narrative(
            mode="trade", sub_mode="reduced", scenarios=[_scenario()]
        )
        result = compose_shell_payload(narr, _ALIGNED_BIAS, 900, _CFG)
        assert result.stage == "prepare"

    def test_row6_trade_aligned_approaching_prepare(self):
        """Row 6: trade+aligned+approaching -> prepare."""
        narr = _make_narrative(
            mode="trade",
            sub_mode="aligned",
            scenarios=[_scenario(trigger="approaching")],
        )
        result = compose_shell_payload(narr, _ALIGNED_BIAS, 900, _CFG)
        assert result.stage == "prepare"

    def test_row7_trade_aligned_in_zone_ready(self):
        """Row 7: trade+aligned+in_zone -> ready."""
        narr = _make_narrative(
            mode="trade",
            sub_mode="aligned",
            scenarios=[_scenario(trigger="in_zone")],
        )
        result = compose_shell_payload(narr, _ALIGNED_BIAS, 900, _CFG)
        assert result.stage == "ready"
        assert "SHORT" in result.stage_label

    def test_row8_trade_aligned_ready_ready(self):
        """Row 8: trade+aligned+ready -> ready."""
        narr = _make_narrative(
            mode="trade",
            sub_mode="aligned",
            scenarios=[_scenario(trigger="ready")],
        )
        result = compose_shell_payload(narr, _ALIGNED_BIAS, 900, _CFG)
        assert result.stage == "ready"

    def test_row9_trade_aligned_triggered_triggered(self):
        """Row 9: trade+aligned+triggered -> triggered."""
        narr = _make_narrative(
            mode="trade",
            sub_mode="aligned",
            scenarios=[_scenario(trigger="triggered")],
        )
        result = compose_shell_payload(narr, _ALIGNED_BIAS, 900, _CFG)
        assert result.stage == "triggered"
        assert "SHORT" in result.stage_label

    def test_ready_long_label(self):
        """Direction label: ready + long -> LONG · READY."""
        narr = _make_narrative(
            mode="trade",
            sub_mode="aligned",
            scenarios=[_scenario(direction="long", trigger="in_zone")],
        )
        result = compose_shell_payload(narr, _ALIGNED_BIAS, 900, _CFG)
        assert result.stage == "ready"
        assert "LONG" in result.stage_label


# ── Error Guard (B3) ────────────────────────────────────────


class TestErrorGuard:
    """B3: error markers in warnings -> safe wait shell."""

    def test_computation_error_returns_wait(self):
        narr = _make_narrative(warnings=["computation_error"])
        result = compose_shell_payload(narr, _ALIGNED_BIAS, 900, _CFG)
        assert result.stage == "wait"

    def test_no_snapshot_returns_wait(self):
        narr = _make_narrative(warnings=["no_snapshot"])
        result = compose_shell_payload(narr, _ALIGNED_BIAS, 900, _CFG)
        assert result.stage == "wait"

    def test_non_error_warning_does_not_block(self):
        narr = _make_narrative(
            mode="trade",
            sub_mode="aligned",
            scenarios=[_scenario(trigger="in_zone")],
            warnings=["some_other_warning"],
        )
        result = compose_shell_payload(narr, _ALIGNED_BIAS, 900, _CFG)
        assert result.stage == "ready"


# ── D1 Conflict Downgrade (B9) ──────────────────────────────


class TestD1ConflictDowngrade:
    """B9: D1 proти всіх -> downgrade ready/triggered to prepare."""

    def test_ready_downgraded_to_prepare(self):
        narr = _make_narrative(
            mode="trade",
            sub_mode="aligned",
            scenarios=[_scenario(trigger="in_zone")],
        )
        result = compose_shell_payload(narr, _D1_CFL_BIAS, 900, _CFG)
        assert result.stage == "prepare"  # downgraded from ready

    def test_triggered_downgraded_to_prepare(self):
        narr = _make_narrative(
            mode="trade",
            sub_mode="aligned",
            scenarios=[_scenario(trigger="triggered")],
        )
        result = compose_shell_payload(narr, _D1_CFL_BIAS, 900, _CFG)
        assert result.stage == "prepare"  # downgraded from triggered

    def test_prepare_not_affected(self):
        narr = _make_narrative(
            mode="trade",
            sub_mode="reduced",
            scenarios=[_scenario()],
        )
        result = compose_shell_payload(narr, _D1_CFL_BIAS, 900, _CFG)
        assert result.stage == "prepare"  # stays prepare


# ── Tactical Strip (§5.2.2) ─────────────────────────────────


class TestTacticalStrip:
    """TF alignment strip composition."""

    def test_all_aligned_bearish(self):
        narr = _make_narrative()
        result = compose_shell_payload(narr, _ALIGNED_BIAS, 900, _CFG)
        strip = result.tactical_strip
        assert strip.alignment_type == "htf_aligned"
        assert strip.alignment_direction == "bearish"
        assert strip.tag_variant == "ok_bear"
        assert len(strip.chips) == 4

    def test_mixed_h1_break(self):
        narr = _make_narrative()
        result = compose_shell_payload(narr, _MIXED_BIAS, 900, _CFG)
        strip = result.tactical_strip
        assert strip.alignment_type == "mixed"
        h1_chip = next(c for c in strip.chips if c.tf_label == "H1")
        assert h1_chip.chip_state == "brk"
        assert strip.tag_variant == "warn"

    def test_d1_conflict(self):
        narr = _make_narrative()
        result = compose_shell_payload(narr, _D1_CFL_BIAS, 900, _CFG)
        strip = result.tactical_strip
        d1_chip = next(c for c in strip.chips if c.tf_label == "D1")
        assert d1_chip.chip_state == "cfl"
        assert strip.tag_variant == "danger"
        assert "D1" in strip.tag_text

    def test_empty_bias_map(self):
        narr = _make_narrative()
        result = compose_shell_payload(narr, {}, 900, _CFG)
        strip = result.tactical_strip
        assert strip.chips == []
        assert strip.alignment_type == "mixed"


# ── Micro Card (§5.2.3) ─────────────────────────────────────


class TestMicroCard:
    """Micro card construction from narrative fields."""

    def test_card_from_scenario(self):
        narr = _make_narrative(
            mode="trade",
            sub_mode="aligned",
            scenarios=[
                _scenario(trigger_desc="CHoCH на M15", invalidation="Above 2880")
            ],
            bias_summary="Bearish D1+H4",
        )
        result = compose_shell_payload(narr, _ALIGNED_BIAS, 900, _CFG)
        card = result.micro_card
        assert card.why_text == "OB▼ A(7) 2860–2870 · Bearish D1+H4"
        assert card.what_needed == "CHoCH на M15"
        assert card.what_cancels == "Above 2880"

    def test_card_wait_fallback(self):
        narr = _make_narrative(mode="wait")
        result = compose_shell_payload(narr, _ALIGNED_BIAS, 900, _CFG)
        card = result.micro_card
        assert card.what_needed == "Чекаємо сетап"

    def test_warning_off_session(self):
        narr = _make_narrative(mode="wait", current_session="")
        result = compose_shell_payload(
            narr, _ALIGNED_BIAS, 900, _CFG, sessions_active=True
        )
        card = result.micro_card
        assert card.warning is not None
        assert "сесі" in card.warning

    def test_warning_outside_killzone(self):
        narr = _make_narrative(
            mode="trade",
            sub_mode="aligned",
            scenarios=[_scenario(trigger="in_zone")],
            current_session="london",
            in_killzone=False,
        )
        result = compose_shell_payload(
            narr, _ALIGNED_BIAS, 900, _CFG, sessions_active=True
        )
        card = result.micro_card
        assert card.warning is not None
        assert "кілзон" in card.warning

    def test_no_warning_in_killzone(self):
        narr = _make_narrative(
            mode="trade",
            sub_mode="aligned",
            scenarios=[_scenario(trigger="in_zone")],
            current_session="london",
            in_killzone=True,
        )
        result = compose_shell_payload(
            narr, _ALIGNED_BIAS, 900, _CFG, sessions_active=True
        )
        card = result.micro_card
        assert card.warning is None


# ── Wire Serialization ──────────────────────────────────────


class TestWireSerialization:
    """to_wire() produces valid JSON-serializable dict."""

    def test_shell_to_wire_structure(self):
        narr = _make_narrative(
            mode="trade",
            sub_mode="aligned",
            scenarios=[_scenario(trigger="in_zone")],
        )
        result = compose_shell_payload(narr, _ALIGNED_BIAS, 900, _CFG)
        wire = result.to_wire()
        assert wire["stage"] == "ready"
        assert isinstance(wire["micro_card"], dict)
        assert isinstance(wire["tactical_strip"], dict)
        assert isinstance(wire["tactical_strip"]["chips"], list)
        assert wire["signal"] is None

    def test_tfchip_to_wire(self):
        chip = TfChip(tf_label="D1", direction="bearish", chip_state="normal")
        assert chip.to_wire() == {
            "tf_label": "D1",
            "direction": "bearish",
            "chip_state": "normal",
        }
