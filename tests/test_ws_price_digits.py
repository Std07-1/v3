"""Точність ціни символу приходить в UI з config.json:price_display (ADR-0054 rev 7 §3.4.1 S3).

UI не вгадує кількість знаків за величиною ціни: сервер кладе `price_digits` (digits таблиці OFFERS FXCM) у кадр
конфігу і в meta.config повного кадру. Кривий config — відмова, символ без значення — у списку `missing` (ws пише ERROR).
"""
from __future__ import annotations

import asyncio
import json
import pathlib

import pytest

from core.config_loader import price_digits_wire
from runtime.ws.app_keys import APP_BOOT_ID, APP_FULL_CONFIG, APP_PRICE_DIGITS, APP_TF_ALLOWLIST
from runtime.ws.ws_server import WsSession, _build_config_frame, _build_full_frame, build_app

REPO = pathlib.Path(__file__).resolve().parents[1]


def _repo_config():
    return json.loads((REPO / "config.json").read_text(encoding="utf-8"))


def _cfg(digits):
    return {"price_display": {"digits_by_symbol": digits}}


def test_repo_config_has_broker_digits_for_every_chart_symbol():
    """Кожен символ графіка має точність; значення — digits OFFERS FXCM, виміряні 05.10.2026."""
    cfg = _repo_config()
    wire, missing = price_digits_wire(cfg, cfg["symbols"])
    assert missing == []
    assert wire["XAU/USD"] == 2 and wire["XAG/USD"] == 3
    assert price_digits_wire(cfg, ["USD/JPY"])[0] == {"USD/JPY": 3}


@pytest.mark.parametrize("digits", [-1, 9, 2.5, True, "2", None])
def test_invalid_digits_refuse_loudly(digits):
    with pytest.raises(ValueError, match="CONFIG_PRICE_DIGITS_INVALID"):
        price_digits_wire(_cfg({"XAU/USD": digits}), ["XAU/USD"])


def test_digits_section_not_an_object_refuses():
    with pytest.raises(ValueError, match="CONFIG_PRICE_DIGITS_INVALID"):
        price_digits_wire(_cfg([2, 3]), ["XAU/USD"])


def test_symbol_without_digits_is_reported_not_refused():
    wire, missing = price_digits_wire(_cfg({"XAU/USD": 2}), ["XAU/USD", "XAG/USD"])
    assert wire == {"XAU/USD": 2} and missing == ["XAG/USD"]
    assert price_digits_wire({}, ["XAU/USD"]) == ({}, ["XAU/USD"])


def test_wire_carries_only_chart_symbols():
    """У кадр ідуть лише символи графіка, а не весь довідник config."""
    wire, _missing = price_digits_wire(_cfg({"XAU/USD": 2, "USD/JPY": 3}), ["XAU/USD"])
    assert wire == {"XAU/USD": 2}


def _app(with_digits):
    app = {APP_BOOT_ID: "boot", APP_FULL_CONFIG: {"symbols": ["XAU/USD", "XAG/USD"]}, APP_TF_ALLOWLIST: {900}}
    if with_digits:
        app[APP_PRICE_DIGITS] = {"XAU/USD": 2, "XAG/USD": 3}
    return app


def test_config_frame_and_full_frame_carry_the_same_digits():
    app = _app(with_digits=True)
    config_frame = _build_config_frame(WsSession(None), app)
    full_frame = _build_full_frame(WsSession(None), [], "XAG/USD", "M15", app=app)
    assert config_frame["config"]["price_digits"] == full_frame["meta"]["config"]["price_digits"] == app[APP_PRICE_DIGITS]


def test_without_digits_there_is_no_field():
    app = _app(with_digits=False)
    assert "price_digits" not in _build_config_frame(WsSession(None), app)["config"]
    assert "price_digits" not in _build_full_frame(WsSession(None), [], "XAU/USD", "M15", app=app)["meta"]["config"]


@pytest.mark.asyncio
async def test_live_config_frame_carries_repo_digits(aiohttp_client):
    """Справжній build_app з config репо: кадр конфігу при підключенні несе точність кожного символу графіка."""
    cfg = _repo_config()
    client = await aiohttp_client(build_app(config_path="config.json"))
    ws = await client.ws_connect("/ws")
    frame = None
    for _ in range(5):
        frame = await asyncio.wait_for(ws.receive_json(), timeout=5)
        if frame.get("frame_type") == "config":
            break
    assert frame is not None and frame.get("frame_type") == "config"
    assert frame["config"]["price_digits"] == price_digits_wire(cfg, cfg["symbols"])[0]
    await ws.close()
