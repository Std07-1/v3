"""Бюджет Focus приходить в UI з config.json:smc.display (ADR-0028 v2 §3.4, ADR-0104 §3.7).

UI застосовує бюджет (скільки зон на бік, міток структури, цін рівнів на бік), але чисел не тримає: сервер кладе їх
у кадр конфігу (при підключенні) і в meta.config повного кадру з того самого розібраного SmcConfig, яким працює рушій.
SMC вимкнено — поля немає, UI лишає свої типові до першого кадру з бюджетом.
"""
from __future__ import annotations

import json
import pathlib

from core.smc.config import SmcConfig
from runtime.ws.app_keys import APP_BOOT_ID, APP_DISPLAY_BUDGET, APP_FULL_CONFIG, APP_TF_ALLOWLIST
from runtime.ws.ws_server import WsSession, _build_config_frame, _build_full_frame, _display_budget_wire

REPO = pathlib.Path(__file__).resolve().parents[1]


def _config():
    return json.loads((REPO / "config.json").read_text(encoding="utf-8"))


def _app(with_budget):
    app = {APP_BOOT_ID: "boot", APP_FULL_CONFIG: {"symbols": ["XAU/USD"]}, APP_TF_ALLOWLIST: {900}}
    if with_budget:
        app[APP_DISPLAY_BUDGET] = _display_budget_wire(SmcConfig.from_dict(_config()["smc"]).display)
    return app


def test_budget_comes_from_config_display_section():
    display = _config()["smc"]["display"]
    assert _display_budget_wire(SmcConfig.from_dict(_config()["smc"]).display) == {
        "zones_per_side": display["focus_budget_per_side"],
        "structure_max": display["structure_label_max"],
        "levels_per_side": display["focus_levels_per_side"],
    }


def test_config_frame_and_full_frame_carry_the_same_budget():
    app = _app(with_budget=True)
    config_frame = _build_config_frame(WsSession(None), app)
    full_frame = _build_full_frame(WsSession(None), [], "XAU/USD", "M15", app=app)
    assert config_frame["config"]["display_budget"] == full_frame["meta"]["config"]["display_budget"] == app[APP_DISPLAY_BUDGET]


def test_without_smc_there_is_no_budget_field():
    app = _app(with_budget=False)
    assert "display_budget" not in _build_config_frame(WsSession(None), app)["config"]
    assert "display_budget" not in _build_full_frame(WsSession(None), [], "XAU/USD", "M15", app=app)["meta"]["config"]
