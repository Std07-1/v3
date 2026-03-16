"""
core/smc/shell_composer.py — Shell payload composer (ADR-0036 §5.2).

S0: pure logic, NO I/O.
C-DUMB: all derive logic on backend; frontend = dumb renderer.

compose_shell_payload() is a pure function that converts
NarrativeBlock + bias_map + config -> ShellPayload.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from core.smc.types import (
    MicroCard,
    NarrativeBlock,
    ShellPayload,
    TacticalStrip,
    TfChip,
)

logger = logging.getLogger(__name__)

# ── Error markers (B3: error guard) ─────────────────────────

_ERROR_MARKERS = frozenset({"computation_error", "no_snapshot", "narrative_timeout"})

# ── Default config (overridable via config.json smc.shell) ──

_DEFAULT_STAGE_LABELS: Dict[str, str] = {
    "wait": "WAIT",
    "prepare": "PREPARE",
    "ready_long": "LONG · READY",
    "ready_short": "SHORT · READY",
    "triggered_long": "LONG · TRIGGERED",
    "triggered_short": "SHORT · TRIGGERED",
    "stayout": "STAY OUT",
}

_DEFAULT_MODE_TEXT: Dict[str, str] = {
    "wait": "Чекаємо",
    "prepare": "Готуємось",
    "ready": "Готовий до входу",
    "triggered": "Активний сетап",
    "stayout": "Поза ринком",
}

_STRIP_TFS = [86400, 14400, 3600, 900]  # D1, H4, H1, M15

_TF_LABELS: Dict[int, str] = {
    86400: "D1",
    14400: "H4",
    3600: "H1",
    900: "M15",
}

# ── strip tag text ──────────────────────────────────────────

_TAG_ALL_ALIGNED_BULL = "Контекст чистий"
_TAG_ALL_ALIGNED_BEAR = "Контекст чистий"
_TAG_MIXED = "Змішаний контекст"
_TAG_D1_CFL = "D1 проти тренду"


# ── Public API ──────────────────────────────────────────────


def compose_shell_payload(
    narrative: NarrativeBlock,
    bias_map: Dict[str, str],
    viewer_tf_s: int,
    config: Dict[str, Any],
    sessions_active: bool = False,
    signal: Any = None,
) -> ShellPayload:
    """Compose shell payload from already-computed narrative + bias_map.

    Pure function. No I/O. Deterministic for same input.
    """
    stage_labels = config.get("stage_labels", _DEFAULT_STAGE_LABELS)
    mode_texts = config.get("micro_card_mode_text", _DEFAULT_MODE_TEXT)
    strip_tfs = config.get("strip_tfs", _STRIP_TFS)

    # ── Error guard (B3) ──
    if narrative.warnings and (set(narrative.warnings) & _ERROR_MARKERS):
        return _build_error_shell(stage_labels, mode_texts)

    # ── Tactical strip (computed first, needed for D1 cfl downgrade) ──
    tactical_strip = _build_tactical_strip(bias_map, strip_tfs)

    # ── Stage mapping (§5.2.1) ──
    stage, direction = _resolve_stage(narrative, sessions_active, tactical_strip)

    # ── Stage label ──
    stage_label = _resolve_stage_label(stage, direction, stage_labels)

    # ── Stage context ──
    stage_context = _build_stage_context(narrative, stage, direction, signal)

    # ── Micro card ──
    micro_card = _build_micro_card(narrative, stage, mode_texts, sessions_active)

    # ── Signal injection (SE3): only for ready/triggered ──
    # ADR-0039 slot — None until implemented

    return ShellPayload(
        stage=stage,
        stage_label=stage_label,
        stage_context=stage_context,
        micro_card=micro_card,
        tactical_strip=tactical_strip,
    )


# ── Stage resolution (§5.2.1) ──────────────────────────────


def _resolve_stage(
    narrative: NarrativeBlock,
    sessions_active: bool,
    tactical_strip: TacticalStrip,
) -> tuple:
    """Returns (stage, direction)."""
    direction: Optional[str] = None
    if narrative.scenarios:
        direction = narrative.scenarios[0].direction

    # ── wait mode ──
    if narrative.mode == "wait":
        if not narrative.scenarios:
            # Row 1/2/3: no scenarios
            if sessions_active and not narrative.current_session:
                stage = "stayout"  # Row 1: off-session
            else:
                stage = "wait"  # Row 2/3
        else:
            stage = "wait"  # Row 4: wait with scenarios
        return (stage, direction)

    # ── trade mode ──
    if narrative.mode == "trade":
        if narrative.sub_mode == "reduced":
            stage = "prepare"  # Row 5
        elif narrative.sub_mode == "aligned" and narrative.scenarios:
            trigger = narrative.scenarios[0].trigger
            if trigger == "approaching":
                stage = "prepare"  # Row 6
            elif trigger == "in_zone":
                stage = "ready"  # Row 7
            elif trigger == "ready":
                stage = "ready"  # Row 8
            elif trigger == "triggered":
                stage = "triggered"  # Row 9
            else:
                stage = "wait"  # fallback
        else:
            stage = "wait"  # fallback (counter, unknown)
        # ── D1 conflict downgrade (B9) ──
        if stage in ("ready", "triggered"):
            if _has_d1_conflict(tactical_strip):
                stage = "prepare"
        return (stage, direction)

    # ── fallback: unknown mode ──
    return ("wait", direction)


def _has_d1_conflict(strip: TacticalStrip) -> bool:
    """Check if D1 chip is in conflict state."""
    for chip in strip.chips:
        if chip.tf_label == "D1" and chip.chip_state == "cfl":
            return True
    return False


# ── Stage label ─────────────────────────────────────────────


def _resolve_stage_label(
    stage: str, direction: Optional[str], labels: Dict[str, str]
) -> str:
    if stage in ("ready", "triggered") and direction:
        key = f"{stage}_{direction}"
        if key == "ready_long":
            key = "ready_long"
        elif key == "ready_short":
            key = "ready_short"
        elif key == "triggered_long":
            key = "triggered_long"
        elif key == "triggered_short":
            key = "triggered_short"
        return labels.get(key, labels.get(stage, stage.upper()))
    return labels.get(stage, stage.upper())


# ── Stage context ───────────────────────────────────────────


def _build_stage_context(
    narrative: NarrativeBlock,
    stage: str,
    direction: Optional[str],
    signal: Any,
) -> str:
    """Build stage_context string."""
    parts: List[str] = []
    if narrative.bias_summary:
        parts.append(narrative.bias_summary)
    if narrative.scenarios:
        sc = narrative.scenarios[0]
        if sc.entry_desc:
            parts.append(sc.entry_desc)
    return " · ".join(parts) if parts else stage.upper()


# ── Micro card ──────────────────────────────────────────────


def _build_micro_card(
    narrative: NarrativeBlock,
    stage: str,
    mode_texts: Dict[str, str],
    sessions_active: bool,
) -> MicroCard:
    mode_text = mode_texts.get(stage, stage.upper())
    why_text = narrative.bias_summary or ""
    what_needed = ""
    what_cancels = ""
    if narrative.scenarios:
        sc = narrative.scenarios[0]
        what_needed = sc.trigger_desc or ""
        what_cancels = sc.invalidation or ""
    if not what_needed:
        what_needed = "Чекаємо сетап" if stage in ("wait", "stayout") else "Аналіз…"
    if not what_cancels:
        what_cancels = "—"

    # ── Warning ──
    warning: Optional[str] = None
    if sessions_active:
        if not narrative.current_session:
            warning = "Поза торговою сесією — низька ліквідність"
        elif not narrative.in_killzone and stage in ("prepare", "ready", "triggered"):
            warning = "Поза кілзоною — нижча якість сетапу"

    return MicroCard(
        mode_text=mode_text,
        why_text=why_text,
        what_needed=what_needed,
        what_cancels=what_cancels,
        warning=warning,
    )


# ── Tactical strip (§5.2.2) ────────────────────────────────


def _build_tactical_strip(
    bias_map: Dict[str, str],
    strip_tfs: List[int],
) -> TacticalStrip:
    chips: List[TfChip] = []
    directions: List[str] = []

    for tf_s in strip_tfs:
        key = str(tf_s)
        direction = bias_map.get(key, "")
        if not direction:
            continue
        tf_label = _TF_LABELS.get(tf_s, f"TF{tf_s}")
        chips.append(
            TfChip(
                tf_label=tf_label,
                direction=direction,
                chip_state="normal",  # refined below
            )
        )
        directions.append(direction)

    if not chips:
        return TacticalStrip(
            alignment_type="mixed",
            alignment_direction=None,
            chips=[],
            tag_text=_TAG_MIXED,
            tag_variant="warn",
        )

    unique_dirs = set(directions)
    if len(unique_dirs) == 1:
        # All aligned
        d = directions[0]
        variant = "ok_bull" if d == "bullish" else "ok_bear"
        return TacticalStrip(
            alignment_type="htf_aligned",
            alignment_direction=d,
            chips=chips,
            tag_text=_TAG_ALL_ALIGNED_BULL,
            tag_variant=variant,
        )

    # ── Mixed: find conflict/break chips ──
    majority = _majority_direction(directions)
    refined_chips: List[TfChip] = []
    d1_is_conflict = False

    for chip in chips:
        if chip.direction == majority:
            refined_chips.append(chip)
        else:
            # This chip disagrees with majority
            if chip.tf_label == "D1":
                d1_is_conflict = True
                refined_chips.append(
                    TfChip(
                        tf_label=chip.tf_label,
                        direction=chip.direction,
                        chip_state="cfl",
                    )
                )
            else:
                refined_chips.append(
                    TfChip(
                        tf_label=chip.tf_label,
                        direction=chip.direction,
                        chip_state="brk",
                    )
                )

    if d1_is_conflict:
        tag_text = _TAG_D1_CFL
        tag_variant = "danger"
    else:
        # Non-D1 break
        brk_labels = [c.tf_label for c in refined_chips if c.chip_state == "brk"]
        tag_text = f"{', '.join(brk_labels)} проти тренду" if brk_labels else _TAG_MIXED
        tag_variant = "warn"

    return TacticalStrip(
        alignment_type="mixed",
        alignment_direction=None,
        chips=refined_chips,
        tag_text=tag_text,
        tag_variant=tag_variant,
    )


def _majority_direction(directions: List[str]) -> str:
    """Return the most common direction."""
    counts: Dict[str, int] = {}
    for d in directions:
        counts[d] = counts.get(d, 0) + 1
    return max(counts, key=lambda k: counts[k])


# ── Error shell (B3) ───────────────────────────────────────


def _build_error_shell(
    stage_labels: Dict[str, str],
    mode_texts: Dict[str, str],
) -> ShellPayload:
    """Return safe wait shell when narrative has error markers."""
    return ShellPayload(
        stage="wait",
        stage_label=stage_labels.get("wait", "WAIT"),
        stage_context="",
        micro_card=MicroCard(
            mode_text=mode_texts.get("wait", "Чекаємо"),
            why_text="",
            what_needed="Чекаємо дані",
            what_cancels="—",
            warning=None,
        ),
        tactical_strip=TacticalStrip(
            alignment_type="mixed",
            alignment_direction=None,
            chips=[],
            tag_text=_TAG_MIXED,
            tag_variant="warn",
        ),
    )
