"""Рівні на графіку: кандидати всіх груп меню «Рівні» + типовий стан за TF (ADR-0104 §3.7, рішення 29.09).

Історія: 26.09 дельта ws_server вкладала `session_levels` усіх сесій для будь-якого TF, і на D1 з'являлась «каша» з
сесій. Тоді сервер просто не надсилав рівні поза політикою TF. Тепер (меню «Рівні» окремо на кожен TF) сервер надсилає
кандидатів усіх груп, а що видно типово, каже `auto` за таблицею config smc.level_defaults (Таблиця А): на D1 сесії
приходять з auto=false і без вибору трейдера не малюються.
"""
from __future__ import annotations

import ast
import dataclasses
import json
import pathlib

import pytest

from core.smc.config import SmcConfig
from core.smc.engine import SmcEngine, _empty_snapshot
from core.smc.types import SmcLevel, make_level_id

REPO = pathlib.Path(__file__).resolve().parents[1]
CURRENT = ("as_h", "as_l", "lon_h", "lon_l", "ny_h", "ny_l")
PREVIOUS = ("p_as_h", "p_as_l", "p_lon_h", "p_lon_l", "p_ny_h", "p_ny_l")
SESSION_KINDS = frozenset(CURRENT + PREVIOUS)
NOW_MS = 1790380800000  # 2026-09-26 00:00 UTC — значення не впливає: рівні підставлені
_SYM = "XAU/USD"
# Рівні знімків TF: EQ свого TF + key levels (D1 — день, H4/H1 — попередня свічка)
_SNAPSHOT_KINDS = {86400: ("eq_highs", "pdh", "pdl"), 14400: ("eq_lows", "p_h4_h", "p_h4_l"),
                   3600: ("eq_highs", "p_h1_h", "p_h1_l"), 900: ("eq_lows",), 300: ("eq_highs", "eq_lows")}


def _level(kind, tf_s, price, auto=None):
    return SmcLevel(id=make_level_id(kind, _SYM, tf_s, price), symbol=_SYM, tf_s=tf_s, kind=kind, price=price,
                    time_ms=NOW_MS, touches=1, auto=auto)


def _engine(monkeypatch, level_defaults=None, sessions_enabled=True):
    cfg = {"sessions": {"enabled": sessions_enabled, "definitions": {
        "asia": {"label": "Asia", "open_utc": "00:00", "close_utc": "07:00"},
        "london": {"label": "London", "open_utc": "07:00", "close_utc": "16:00"},
        "newyork": {"label": "New York", "open_utc": "12:00", "close_utc": "21:00"},
    }}}
    if level_defaults is not None:
        cfg["level_defaults"] = level_defaults
    engine = SmcEngine(SmcConfig.from_dict(cfg))
    # S2a: поточна доба — auto=true, попередня — false
    sessions = [_level(k, 86400, 4000.0 + i, auto=k in CURRENT) for i, k in enumerate(CURRENT + PREVIOUS)]
    monkeypatch.setattr(engine, "get_session_levels",
                        lambda symbol, now_ms: list(sessions) if sessions_enabled else [])
    snapshots = {tf: dataclasses.replace(_empty_snapshot(_SYM, tf), levels=[
        _level(k, tf, 3000.0 + tf / 100 + i) for i, k in enumerate(kinds)]) for tf, kinds in _SNAPSHOT_KINDS.items()}
    monkeypatch.setattr(engine, "get_snapshot", lambda symbol, tf_s: snapshots.get(tf_s, _empty_snapshot(symbol, tf_s)))
    return engine


def _repo_level_defaults():
    return json.loads((REPO / "config.json").read_text(encoding="utf-8"))["smc"]["level_defaults"]


def _groups(levels, auto=None):
    return {lv.group for lv in levels if auto is None or lv.auto is auto}


@pytest.mark.parametrize("viewer_tf_s, candidate_groups, default_on", [
    # D1: старших TF нема; сесії — кандидати, але типово вимкнені (перший біль власника: «каша» на D1)
    (86400, {"liquidity", "asia", "london", "newyork", "sessions_prev"}, {"liquidity"}),
    (14400, {"liquidity", "day", "asia", "london", "newyork", "sessions_prev"}, {"day", "liquidity"}),
    (3600, {"liquidity", "day", "h4", "asia", "london", "newyork", "sessions_prev"},
     {"day", "asia", "london", "newyork", "liquidity"}),
    (1800, {"liquidity", "day", "h4", "asia", "london", "newyork", "sessions_prev"},   # M30 → базовий H1
     {"day", "asia", "london", "newyork", "liquidity"}),
    (900, {"liquidity", "day", "h4", "h1", "asia", "london", "newyork", "sessions_prev"},
     {"day", "asia", "london", "newyork", "liquidity"}),
    (300, {"liquidity", "day", "h4", "h1", "asia", "london", "newyork", "sessions_prev"},
     {"day", "asia", "london", "newyork", "liquidity"}),
    (60, {"liquidity", "day", "h4", "h1", "asia", "london", "newyork", "sessions_prev"},   # M1 → базовий M5
     {"day", "asia", "london", "newyork", "liquidity"}),
])
def test_full_frame_offers_every_group_and_marks_table_a_defaults(monkeypatch, viewer_tf_s, candidate_groups,
                                                                   default_on):
    levels = _engine(monkeypatch, _repo_level_defaults()).get_display_snapshot(_SYM, viewer_tf_s).levels
    assert all(lv.group is not None and lv.auto is not None for lv in levels)
    assert _groups(levels) == candidate_groups
    assert _groups(levels, auto=True) == default_on
    assert _groups(levels, auto=False) == candidate_groups - default_on


def test_own_previous_candle_is_not_offered_to_its_viewer(monkeypatch):
    """Попередня свічка свого TF видна на графіку: H4 не отримує Prev 4H, H1 — Prev 1H."""
    engine = _engine(monkeypatch, _repo_level_defaults())
    assert "h4" not in _groups(engine.get_display_levels(_SYM, 14400, NOW_MS))
    assert "h1" not in _groups(engine.get_display_levels(_SYM, 3600, NOW_MS))


def test_without_defaults_table_sessions_keep_s2a_auto_and_other_groups_stay_undecided(monkeypatch):
    levels = _engine(monkeypatch).get_display_levels(_SYM, 300, NOW_MS)
    auto_by_kind = {lv.kind: lv.auto for lv in levels}
    assert all(auto_by_kind[k] is True for k in CURRENT) and all(auto_by_kind[k] is False for k in PREVIOUS)
    assert auto_by_kind["pdh"] is None and auto_by_kind["eq_highs"] is None
    assert all(lv.group is not None for lv in levels)


def test_sessions_disabled_offer_no_session_levels(monkeypatch):
    levels = _engine(monkeypatch, _repo_level_defaults(), sessions_enabled=False).get_display_levels(_SYM, 300, NOW_MS)
    assert not {lv.kind for lv in levels} & SESSION_KINDS


def test_unknown_group_in_defaults_table_fails_at_startup():
    with pytest.raises(ValueError, match="LEVEL_DEFAULTS_GROUP_INVALID tf=300"):
        SmcConfig.from_dict({"level_defaults": {"by_base_tf": {"300": ["day", "sessions"]}}})


# ── легасі-дельта (до слайсу 2c): session_levels за старою політикою TF ──

@pytest.mark.parametrize("viewer_tf_s, expected", [
    (86400, set()), (14400, set(PREVIOUS)), (3600, SESSION_KINDS), (300, SESSION_KINDS),
])
def test_legacy_delta_session_levels_follow_the_old_tf_policy(monkeypatch, viewer_tf_s, expected):
    engine = _engine(monkeypatch)
    assert {lv.kind for lv in engine.get_display_session_levels(_SYM, viewer_tf_s, NOW_MS)} == expected


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
