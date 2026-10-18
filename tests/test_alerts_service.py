"""ADR-0114 S5: збирання процесу smc-alerts — config, секрети, старт (runtime/alerts/{config,service}.py),
читач останнього close (feeds.py) і getMe (telegram.py).

Run: python -m pytest tests/test_alerts_service.py -q
"""

from __future__ import annotations

from types import SimpleNamespace

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from runtime.alerts import service
from runtime.alerts.config import AlertsConfig
from runtime.alerts.feeds import M1ReadError, uds_last_close_reader
from runtime.alerts.telegram import TelegramClient, TelegramError

SECTION = {
    "_comment": "ADR-0114",
    "enabled": True,
    "port": 8020,
    "state_dir": "/var/lib/smc-v3/alerts",
    "env_file": "/etc/smc-v3/alerts.env",
    "site_url": "https://aione-smc.com",
    "display_tz": "Europe/Prague",
    "telegram_api": "https://api.telegram.org",
    "telegram_enabled": False,
    "vapid_subject": "https://aione-smc.com",
    "push_ttl_s": 21600,
    "push_timeout_s": 10,
    "push_host_suffixes": ["fcm.googleapis.com", "push.services.mozilla.com", "push.apple.com", "notify.windows.com"],
    "device_idle_days": 90,
    "device_prune_s": 3600,
    "max_active_per_subscriber": 50,
    "max_devices": 5000,
    "devices_per_ip_per_hour": 10,
    "pair_code_ttl_s": 600,
    "pair_confirm_attempts": 5,
    "pair_max_pending": 100,
    "requests_per_min": 60,
    "sse_heartbeat_s": 25,
    "gap_after_s": 900,
    "tick_reconnect_s": 5,
    "m1_poll_s": 15,
    "m1_final_lag_s": 600,
    "m1_chunk_bars": 120,
    "delivery_poll_s": 1,
    "retry_first_s": 2,
    "retry_max_s": 300,
    "telegram_poll_timeout_s": 25,
    "telegram_error_pause_s": 10,
}


# ── config ───────────────────────────────────────────────────────────────


def test_config_section_parses_and_ignores_comment():
    config = AlertsConfig.from_cfg({"alerts": SECTION})
    assert config.port == 8020 and config.sse_heartbeat_s == 25 and config.enabled is True


@pytest.mark.parametrize(
    "patch, reason",
    [
        ({"port": None}, "port"),
        ({"prot": 1}, "невідомі"),
        ({"port": True}, "port"),
        ({"m1_chunk_bars": 0}, "m1_chunk_bars"),
        ({"retry_max_s": "300"}, "retry_max_s"),
        ({"state_dir": ""}, "state_dir"),
        ({"site_url": "https://aione-smc.com/"}, "site_url"),
        ({"site_url": "http://aione-smc.com"}, "site_url"),
        ({"enabled": "true"}, "enabled"),
        ({"telegram_enabled": 0}, "telegram_enabled"),
        ({"vapid_subject": "aione-smc.com"}, "vapid_subject"),
        ({"push_host_suffixes": []}, "push_host_suffixes"),
        ({"push_host_suffixes": ["https://fcm.googleapis.com"]}, "push_host_suffixes"),
        ({"push_host_suffixes": ["FCM.googleapis.com"]}, "push_host_suffixes"),
        ({"push_host_suffixes": "fcm.googleapis.com"}, "push_host_suffixes"),
        ({"device_idle_days": 0}, "device_idle_days"),
    ],
)
def test_config_rejects_missing_unknown_or_mistyped_keys(patch, reason):
    section = {**SECTION, **patch}
    section = {k: v for k, v in section.items() if v is not None}
    with pytest.raises(ValueError, match=reason):
        AlertsConfig.from_cfg({"alerts": section})


def test_config_without_section_is_loud():
    with pytest.raises(ValueError, match="нема секції alerts"):
        AlertsConfig.from_cfg({})


# ── старт ────────────────────────────────────────────────────────────────


def test_secrets_required_and_owner_chat_numeric(monkeypatch, tmp_path):
    monkeypatch.delenv("ALERTS_TELEGRAM_TOKEN", raising=False)
    monkeypatch.delenv("ALERTS_OWNER_CHAT_ID", raising=False)
    with pytest.raises(SystemExit, match="ALERTS_SECRETS_MISSING"):
        service._read_telegram_secrets(str(tmp_path / "nope.env"))
    monkeypatch.setenv("ALERTS_TELEGRAM_TOKEN", "1:x")
    monkeypatch.setenv("ALERTS_OWNER_CHAT_ID", "@owner")
    with pytest.raises(SystemExit, match="ALERTS_SECRETS_MISSING"):
        service._read_telegram_secrets(str(tmp_path / "nope.env"))
    env = tmp_path / "alerts.env"
    env.write_text("ALERTS_OWNER_CHAT_ID=-1001\n", encoding="utf-8")
    monkeypatch.delenv("ALERTS_OWNER_CHAT_ID")
    assert service._read_telegram_secrets(str(env)) == ("1:x", -1001)


def test_vapid_key_required_valid_and_gives_public_key(monkeypatch, tmp_path):
    from runtime.alerts.vapid_keys import generate_private_key
    from runtime.alerts.webpush import application_server_key, load_vapid

    monkeypatch.delenv("ALERTS_VAPID_PRIVATE_KEY", raising=False)
    with pytest.raises(SystemExit, match="ALERTS_SECRETS_MISSING.*vapid_keys"):
        service._read_vapid_key(str(tmp_path / "nope.env"))
    monkeypatch.setenv("ALERTS_VAPID_PRIVATE_KEY", "not-a-key")
    with pytest.raises(SystemExit, match="ALERTS_SECRETS_INVALID"):
        service._read_vapid_key(str(tmp_path / "nope.env"))
    key = generate_private_key()
    monkeypatch.setenv("ALERTS_VAPID_PRIVATE_KEY", key)
    assert service._read_vapid_key(str(tmp_path / "nope.env")) == (key, application_server_key(load_vapid(key)))


def _cfg(state_dir) -> dict:
    return {
        "symbols": ["XAU/USD"],
        "price_display": {"digits_by_symbol": {"XAU/USD": 2}},
        "channels": {"price_tick": "probe:ticks"},
        "redis": {"enabled": True, "host": "127.0.0.1", "port": 6379, "db": 1, "namespace": "probe"},
        "alerts": {**SECTION, "state_dir": str(state_dir)},
    }


def test_build_service_refuses_to_start_half_alive(tmp_path, monkeypatch):
    monkeypatch.setenv("ALERTS_TELEGRAM_TOKEN", "1:x")
    monkeypatch.setenv("ALERTS_OWNER_CHAT_ID", "1001")
    cfg = _cfg(tmp_path / "missing")
    with pytest.raises(SystemExit, match="ALERTS_STATE_DIR_MISSING"):
        service.build_service(cfg, "config.json", AlertsConfig.from_cfg(cfg))
    no_digits = {**_cfg(tmp_path), "price_display": {"digits_by_symbol": {}}}
    with pytest.raises(ValueError, match="XAU/USD"):
        service.build_service(no_digits, "config.json", AlertsConfig.from_cfg(no_digits))
    no_channel = {**_cfg(tmp_path), "channels": {}}
    monkeypatch.delenv("FXCM_PRICE_TICK_CHANNEL", raising=False)
    monkeypatch.delenv("FXCM_PRICE_SNAPSHOT_CHANNEL", raising=False)
    with pytest.raises(SystemExit, match="ALERTS_NO_TICKS"):
        service.build_service(no_channel, "config.json", AlertsConfig.from_cfg(no_channel))


@pytest.mark.asyncio
async def test_disabled_service_answers_health_only():
    async with TestClient(TestServer(service._disabled_app())) as client:
        assert await (await client.get("/api/alerts/health")).json() == {"enabled": False}
        assert (await client.get("/api/alerts")).status == 404


# ── останній close і getMe ───────────────────────────────────────────────


def test_last_close_reader_uses_range_to_now_and_is_loud_on_degraded():
    seen = []

    def read_window(spec, policy):
        seen.append(spec)
        if spec.symbol == "BAD":
            return SimpleNamespace(meta={"source": "degraded"}, warnings=["x"], bars_lwc=[])
        if spec.symbol == "EMPTY":
            return SimpleNamespace(meta={"source": "disk_range"}, warnings=[], bars_lwc=[])
        rows = [{"close": 4371.5, "complete": True}]
        return SimpleNamespace(meta={"source": "disk_range"}, warnings=[], bars_lwc=rows)

    read = uds_last_close_reader(SimpleNamespace(read_window=read_window))
    assert read("XAU/USD", 1_000) == 4371.5 and read("EMPTY", 1_000) is None
    assert (seen[0].since_open_ms, seen[0].to_open_ms, seen[0].limit) == (None, 1_000, 1)  # не хвіст: без кешу RAM
    with pytest.raises(M1ReadError):
        read("BAD", 1_000)


@pytest.mark.asyncio
async def test_get_username_from_get_me():
    token = "9:probe"

    async def get_me(request):
        username = request.query.get("u", "v3_signals_bot")
        return web.json_response({"ok": True, "result": {"id": 9, "is_bot": True, "username": username}})

    async def get_me_broken(request):
        return web.json_response({"ok": True, "result": {"id": 9}})

    app = web.Application()
    app.router.add_post(f"/bot{token}/getMe", get_me)
    app.router.add_post("/bot9:broken/getMe", get_me_broken)
    async with TestServer(app) as server, aiohttp.ClientSession() as session:
        base = str(server.make_url(""))
        assert await TelegramClient(session, token, api_base=base).get_username() == "v3_signals_bot"
        with pytest.raises(TelegramError, match="username"):
            await TelegramClient(session, "9:broken", api_base=base).get_username()
