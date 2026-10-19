"""`/api/context` без мови — байт у байт як до мовного шару (ADR-0116 L4): цю відповідь читає Арчі (X31, X40).

Golden `tests/golden/api_context/xau_h1.json` записано на чистому main (до Ф2.2) цим самим тестом. Справжній
SmcRunner на синтетичних барах тестів розбору, час заморожено на моменті тих барів — відповідь не залежить ні від
годинника, ні від дня тижня. Нормалізовано лише boot_id і кількість WS-клієнтів (інші тести процесу).

Оновити golden — лише свідомо: `I18N_API_GOLDEN_UPDATE=1 pytest tests/test_api_context_golden.py` і причина в коміті.
"""
from __future__ import annotations

import json
import os
import pathlib
import time

import pytest
from aiohttp.test_utils import make_mocked_request

import runtime.ws.ws_server as ws_server
from runtime.ws.app_keys import APP_SMC_RUNNER
from test_briefing_runner import _runner
from test_narrative_now_ms import WED_ASIA

pytestmark = pytest.mark.asyncio

GOLDEN = pathlib.Path(__file__).parent / "golden" / "api_context" / "xau_h1.json"
UPDATE = os.environ.get("I18N_API_GOLDEN_UPDATE") == "1"


class _Uds:
    def read_window(self, spec, policy):
        return type("Window", (), {"bars_lwc": [], "warnings": []})()

    def read_updates(self, spec):
        return type("Updates", (), {"events": [], "cursor_seq": 0, "warnings": []})()


def _normalized(body: str) -> str:
    payload = json.loads(body)

    def _walk(node):
        if isinstance(node, dict):
            return {key: ("<%s>" % key if key in ("boot_id", "ws_clients") else _walk(value))
                    for key, value in node.items()}
        if isinstance(node, list):
            return [_walk(item) for item in node]
        return node

    return json.dumps(_walk(payload), ensure_ascii=False, sort_keys=True, indent=1)


async def test_api_context_without_lang_is_byte_identical_to_main(tmp_path, monkeypatch):
    monkeypatch.setattr(time, "time", lambda: WED_ASIA / 1000.0)
    app = ws_server.build_app(config_path="config.json", uds=_Uds())
    app[APP_SMC_RUNNER] = _runner(tmp_path)
    handler = next(route.handler for route in app.router.routes()
                   if route.resource is not None and route.resource.canonical == "/api/context")
    response = await handler(make_mocked_request("GET", "/api/context?symbol=XAU/USD&tf=H1", app=app))
    assert response.status == 200
    actual = _normalized(response.text)
    assert '"narrative"' in actual, "golden має нести наратив — те, що читає Арчі"
    if UPDATE:
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN.write_text(actual + "\n", encoding="utf-8", newline="\n")
    assert GOLDEN.exists(), "немає golden %s — записати свідомо: I18N_API_GOLDEN_UPDATE=1" % GOLDEN
    assert actual == GOLDEN.read_text(encoding="utf-8").rstrip("\n"), "відповідь /api/context без мови змінилась"
