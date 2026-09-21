"""Redis-клієнт тіків (APP_TICK_REDIS_CLIENT) не залежить від прапорця D1 relay.

Вердикт скептика 21.09: клієнт створювався лише при d1_live_tick_relay_enabled=true,
тож вимкнення relay мовчки прибирало /api/context.tick_price і старт
WakeEngine/NarrativeEnricher («WAKE_ENGINE_SKIP: no redis»). redis.Redis(...) не
відкриває з'єднання до першої команди — тест не потребує живого Redis.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from runtime.ws.app_keys import (
    APP_D1_TICK_RELAY_TFS,
    APP_TICK_REDIS_CLIENT,
    APP_TICK_REDIS_NS,
)
from runtime.ws.ws_server import _log as ws_log
from runtime.ws.ws_server import build_app

_REPO_CONFIG = Path(__file__).resolve().parents[1] / "config.json"


class _StubUds:
    """build_app(uds=...) пропускає автоініціалізацію UDS — тест лише про tick-клієнт."""


def _build(tmp_path: Path, **overrides) -> object:
    cfg = json.loads(_REPO_CONFIG.read_text(encoding="utf-8"))
    for key, value in overrides.items():
        cfg[key] = value
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
    return build_app(config_path=str(cfg_path), uds=_StubUds())


def test_repo_config_d1_relay_disabled():
    cfg = json.loads(_REPO_CONFIG.read_text(encoding="utf-8"))
    assert cfg["d1_live_tick_relay_enabled"] is False


def test_build_app_relay_disabled_still_creates_tick_client(tmp_path):
    app = _build(tmp_path, d1_live_tick_relay_enabled=False)
    assert app[APP_D1_TICK_RELAY_TFS] == set()
    assert app.get(APP_TICK_REDIS_CLIENT) is not None
    assert app[APP_TICK_REDIS_NS] == json.loads(_REPO_CONFIG.read_text(encoding="utf-8"))["redis"][
        "namespace"
    ]


def test_build_app_relay_enabled_uses_same_tick_client(tmp_path):
    app = _build(tmp_path, d1_live_tick_relay_enabled=True, d1_live_tick_relay_tfs_s=[86400])
    assert app[APP_D1_TICK_RELAY_TFS] == {86400}
    assert app.get(APP_TICK_REDIS_CLIENT) is not None


def test_build_app_redis_disabled_no_tick_client_and_warns(tmp_path, caplog):
    cfg = json.loads(_REPO_CONFIG.read_text(encoding="utf-8"))
    redis_cfg = dict(cfg["redis"], enabled=False)
    with caplog.at_level(logging.WARNING, logger=ws_log.name):
        app = _build(tmp_path, redis=redis_cfg)
    assert app.get(APP_TICK_REDIS_CLIENT) is None
    assert app[APP_TICK_REDIS_NS] == ""
    assert any("TICK_REDIS_CLIENT_SKIP" in r.getMessage() for r in caplog.records)
