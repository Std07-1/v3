"""
core/smc/tda — TDA Cascade Signal Engine (ADR-0040).

4-stage daily cascade: D1 Macro → H4 Confirm → Session Narrative → M15 FVG Entry.
Trade management: Config F (partial 50% @1R + trail remaining).

Інваріанти:
  S0: pure logic, NO I/O (same rules as core/smc/)
  S2: deterministic — same bars → same signal
  S5: all thresholds from config.json:smc.tda_cascade
"""

from core.smc.tda.types import (
    # Vocabulary
    MACRO_DIRECTIONS,
    MACRO_METHODS,
    MACRO_CONFIDENCES,
    TDA_NARRATIVES,
    TDA_SIGNAL_STATES,
    TDA_OUTCOMES,
    TDA_GRADES,
    GRADE_FACTORS,
    # Stage outputs
    MacroResult,
    H4ConfirmResult,
    SessionNarrative,
    FvgEntry,
    # Trade management
    TradeState,
    # Complete signal
    TdaSignal,
    # Config
    TdaCascadeConfig,
    # Helpers
    make_tda_signal_id,
    compute_grade,
    initial_trade_state,
)
from core.smc.tda.stage1_macro import get_macro_direction
from core.smc.tda.stage2_h4_confirm import h4_confirmed
from core.smc.tda.stage3_session import get_session_narrative
from core.smc.tda.stage4_fvg_entry import find_fvg_entry
from core.smc.tda.stage5_trade_mgmt import update_trade
from core.smc.tda.orchestrator import run_tda_cascade

__all__ = [
    "MACRO_DIRECTIONS",
    "MACRO_METHODS",
    "MACRO_CONFIDENCES",
    "TDA_NARRATIVES",
    "TDA_SIGNAL_STATES",
    "TDA_OUTCOMES",
    "TDA_GRADES",
    "GRADE_FACTORS",
    "MacroResult",
    "H4ConfirmResult",
    "SessionNarrative",
    "FvgEntry",
    "TradeState",
    "TdaSignal",
    "TdaCascadeConfig",
    "make_tda_signal_id",
    "compute_grade",
    "initial_trade_state",
    # Stage functions
    "get_macro_direction",
    "h4_confirmed",
    "get_session_narrative",
    "find_fvg_entry",
    "update_trade",
    "run_tda_cascade",
]
