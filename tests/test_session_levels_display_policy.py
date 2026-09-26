"""ADR-0035 §3.4: сесійні рівні на графіку — одна політика TF для повного кадру і дельти.

Дефект 26.09: дельта ws_server вкладала `session_levels` усіх сесій для будь-якого TF, UI вливав їх у шар рівнів —
на D1 (політика: жодного рівня сесій) з'являлись 6 рівнів попередніх сесій, на H4 (лише попередні) — ще й поточні.
Тепер і повний кадр (get_display_snapshot), і дельта беруть get_display_session_levels з тим самим allow-set.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

from core.smc.config import SmcConfig
from core.smc.engine import SmcEngine
from core.smc.types import SmcLevel, make_level_id

REPO = pathlib.Path(__file__).resolve().parents[1]
CURRENT = ("as_h", "as_l", "lon_h", "lon_l", "ny_h", "ny_l")
PREVIOUS = ("p_as_h", "p_as_l", "p_lon_h", "p_lon_l", "p_ny_h", "p_ny_l")
SESSION_KINDS = frozenset(CURRENT + PREVIOUS)
NOW_MS = 1790380800000  # 2026-09-26 00:00 UTC — значення не впливає: сесійні рівні підставлені


def _engine(monkeypatch, enabled=True):
    engine = SmcEngine(SmcConfig.from_dict({"sessions": {"enabled": enabled, "definitions": {
        "asia": {"label": "Asia", "open_utc": "00:00", "close_utc": "07:00"},
        "london": {"label": "London", "open_utc": "07:00", "close_utc": "16:00"},
        "newyork": {"label": "New York", "open_utc": "12:00", "close_utc": "21:00"},
    }}}))
    levels = [SmcLevel(id=make_level_id(k, "XAU/USD", 86400, 4000.0 + i), symbol="XAU/USD", tf_s=86400, kind=k,
                       price=4000.0 + i, time_ms=NOW_MS, touches=0) for i, k in enumerate(CURRENT + PREVIOUS)]
    monkeypatch.setattr(engine, "get_session_levels", lambda symbol, now_ms: list(levels))
    return engine


def _kinds(levels):
    return {lv.kind for lv in levels if lv.kind in SESSION_KINDS}


@pytest.mark.parametrize("viewer_tf_s, expected", [
    (86400, set()),                         # D1: свічки видно — жодного сесійного рівня
    (14400, set(PREVIOUS)),                 # H4: лише попередні сесії
    (3600, set(CURRENT + PREVIOUS)),        # H1 (і M30 через базовий H1)
    (1800, set(CURRENT + PREVIOUS)),
    (300, set(CURRENT + PREVIOUS)),         # M5 (і M1/M3)
    (60, set(CURRENT + PREVIOUS)),
])
def test_delta_and_full_frame_show_the_same_session_levels_per_tf(monkeypatch, viewer_tf_s, expected):
    engine = _engine(monkeypatch)
    delta = _kinds(engine.get_display_session_levels("XAU/USD", viewer_tf_s, NOW_MS))
    full = _kinds(engine.get_display_snapshot("XAU/USD", viewer_tf_s).levels)
    assert delta == expected and full == expected


def test_sessions_disabled_leave_no_session_levels_on_any_tf(monkeypatch):
    engine = _engine(monkeypatch, enabled=False)
    assert all(engine.get_display_session_levels("XAU/USD", tf, NOW_MS) == [] for tf in (60, 300, 3600, 14400, 86400))


def test_ws_delta_uses_the_display_policy_not_the_unfiltered_session_list():
    """Сторож регресії: нефільтрований get_session_levels_wire — лише для /api/context (зовнішні споживачі, не графік)."""
    tree = ast.parse((REPO / "runtime" / "ws" / "ws_server.py").read_text(encoding="utf-8"))
    callers = []

    def visit(node, enclosing):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            enclosing = node.name  # найближча функція, що обгортає виклик (вкладені — окремо)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr == "get_session_levels_wire":
            callers.append(enclosing)
        for child in ast.iter_child_nodes(node):
            visit(child, enclosing)

    visit(tree, None)
    assert callers == ["_api_context"], callers
