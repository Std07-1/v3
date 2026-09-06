"""ADR-0090 S4 — резолвер `agent_bridge` + гейти: специфікація поведінки."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from runtime.agent_bridge.config import (
    AGENT_BRIDGE_ENABLED_ENV,
    LEGACY_TOP_LEVEL_KEYS,
    resolve_agent_bridge_config,
)
from tools.exit_gates.gates import gate_adr_config_sync, gate_platform_config_no_agent_keys


def test_missing_section_resolves_to_everything_disabled():
    cfg = resolve_agent_bridge_config({"symbols": ["XAU/USD"]}, environ={})
    assert cfg.enabled is False
    assert cfg.enabled_source == "config"
    assert cfg.wake_engine_enabled is False
    assert cfg.console_enabled is False
    assert cfg.data_dir == ""


def test_master_switch_gates_wake_engine_and_console():
    section = {"enabled": False, "wake_engine": {"enabled": True}, "console": {"enabled": True}}
    off = resolve_agent_bridge_config({"agent_bridge": section}, environ={})
    assert off.wake_engine_enabled is False and off.console_enabled is False
    on = resolve_agent_bridge_config({"agent_bridge": {**section, "enabled": True}}, environ={})
    assert on.wake_engine_enabled is True and on.console_enabled is True
    assert on.wake_engine == {"enabled": True}


@pytest.mark.parametrize("raw,expected", [("1", True), ("true", True), ("ON", True), ("0", False), ("no", False)])
def test_env_override_wins_over_config_and_is_attributed(raw, expected):
    cfg = resolve_agent_bridge_config(
        {"agent_bridge": {"enabled": not expected}}, environ={AGENT_BRIDGE_ENABLED_ENV: raw}
    )
    assert cfg.enabled is expected
    assert cfg.enabled_source == "env"


def test_env_override_garbage_is_loud_not_silent_false():
    with pytest.raises(ValueError, match=AGENT_BRIDGE_ENABLED_ENV):
        resolve_agent_bridge_config({"agent_bridge": {"enabled": True}}, environ={AGENT_BRIDGE_ENABLED_ENV: "maybe"})


def test_console_token_comes_from_named_env_var_never_from_config():
    cfg = resolve_agent_bridge_config(
        {"agent_bridge": {"console": {"auth_token_env": "MY_TOKEN", "auth_token": "in-config"}}},
        environ={},
    )
    assert cfg.console.resolve_token({"MY_TOKEN": "s3cret"}) == "s3cret"
    assert cfg.console.resolve_token({}) == ""


def test_repo_config_json_has_single_agent_section():
    cfg = json.loads(Path("config.json").read_text(encoding="utf-8"))
    assert not set(LEGACY_TOP_LEVEL_KEYS) & set(cfg)
    resolved = resolve_agent_bridge_config(cfg, environ={})
    assert resolved.enabled is False, "репо-дефолт = вимкнено (ADR-0090 §3.6)"
    assert resolved.console.auth_token_env == "ARCHI_AUTH_TOKEN"


def test_gate_rejects_legacy_top_level_keys(tmp_path: Path):
    (tmp_path / "config.json").write_text(json.dumps({"symbols": [], "wake_engine": {"enabled": True}}), encoding="utf-8")
    result = gate_platform_config_no_agent_keys.run_gate({"root": str(tmp_path)})
    assert result["ok"] is False
    assert "legacy_top_level:wake_engine" in result["details"]


def test_gate_accepts_single_agent_bridge_section(tmp_path: Path):
    (tmp_path / "config.json").write_text(json.dumps({"symbols": [], "agent_bridge": {"enabled": False}}), encoding="utf-8")
    result = gate_platform_config_no_agent_keys.run_gate({"root": str(tmp_path)})
    assert result["ok"] is True, result


@pytest.mark.parametrize(
    "text,expected",
    [
        ("- **Статус**: **Accepted**\n", "accepted"),
        ("- **Status**: Accepted\n", "accepted"),
        ("| Статус | **Accepted** (owner 2026-09-06) |\n", "accepted"),
        ("## No status here\n", None),
    ],
)
def test_adr_status_extraction_accepts_bullet_plain_and_table_forms(tmp_path: Path, text, expected):
    (tmp_path / "0999-x.md").write_text(text, encoding="utf-8")
    assert gate_adr_config_sync._extract_adr_status(tmp_path, "0999") == expected


@pytest.mark.parametrize("filename", ["0998-legacy-name.md", "ADR-0998-new-name.md"])
def test_adr_status_lookup_accepts_both_filename_schemes(tmp_path: Path, filename):
    (tmp_path / filename).write_text("- **Status**: Accepted\n", encoding="utf-8")
    assert gate_adr_config_sync._extract_adr_status(tmp_path, "0998") == "accepted"
