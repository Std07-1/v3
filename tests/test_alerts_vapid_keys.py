"""ADR-0114 Rev 2 S9: генератор ключа VAPID (runtime/alerts/vapid_keys.py).

Run: python -m pytest tests/test_alerts_vapid_keys.py -q
"""

from __future__ import annotations

import os
import stat

import pytest

from runtime.alerts.vapid_keys import ENV_KEY, generate_private_key, main, upsert_env_line
from runtime.alerts.webpush import application_server_key, load_vapid


def _key_in(path) -> str:
    (line,) = [line for line in path.read_text(encoding="utf-8").splitlines() if line.startswith(ENV_KEY + "=")]
    return line.split("=", 1)[1]


def test_generated_key_is_32_raw_bytes_readable_by_sender():
    key = generate_private_key()
    assert len(key) == 43 and "=" not in key
    assert len(application_server_key(load_vapid(key))) == 87  # 65 байт точки P-256 у base64url
    assert generate_private_key() != key


def test_upsert_env_line_replaces_only_its_key():
    text = "ALERTS_TELEGRAM_TOKEN=1:abc\nALERTS_VAPID_PRIVATE_KEY_OLD=x\n"
    added, existed = upsert_env_line(text, ENV_KEY, "k1")
    assert not existed and added == text + f"{ENV_KEY}=k1\n"
    replaced, existed = upsert_env_line(added, ENV_KEY, "k2")
    assert existed and replaced == text + f"{ENV_KEY}=k2\n"


def test_main_creates_file_prints_public_key_never_private(tmp_path, capsys):
    env = tmp_path / "smc-v3" / "alerts.env"
    assert main(["--env-file", str(env)]) == 0
    key = _key_in(env)
    out = capsys.readouterr().out
    assert application_server_key(load_vapid(key)) in out
    assert key not in out
    assert [p.name for p in env.parent.iterdir()] == ["alerts.env"]  # ні tmp, ні бекапу для нового файлу
    if os.name == "posix":
        assert stat.S_IMODE(env.stat().st_mode) == 0o640


def test_main_keeps_other_lines_and_backs_up_previous_file(tmp_path):
    env = tmp_path / "alerts.env"
    env.write_text("ALERTS_TELEGRAM_TOKEN=1:abc\nALERTS_OWNER_CHAT_ID=42\n", encoding="utf-8")
    assert main(["--env-file", str(env)]) == 0
    lines = env.read_text(encoding="utf-8").splitlines()
    assert lines[:2] == ["ALERTS_TELEGRAM_TOKEN=1:abc", "ALERTS_OWNER_CHAT_ID=42"] and lines[2].startswith(ENV_KEY + "=")
    (backup,) = tmp_path.glob("alerts.env.bak.*")
    assert backup.read_text(encoding="utf-8") == "ALERTS_TELEGRAM_TOKEN=1:abc\nALERTS_OWNER_CHAT_ID=42\n"


def test_main_refuses_to_replace_existing_key_without_rotate(tmp_path, capsys):
    env = tmp_path / "alerts.env"
    assert main(["--env-file", str(env)]) == 0
    before = env.read_bytes()
    assert main(["--env-file", str(env)]) == 2
    assert env.read_bytes() == before
    assert list(tmp_path.glob("alerts.env.bak.*")) == []
    assert "--rotate" in capsys.readouterr().err


def test_main_rotate_replaces_key_and_keeps_old_in_backup(tmp_path):
    env = tmp_path / "alerts.env"
    assert main(["--env-file", str(env)]) == 0
    old_key = _key_in(env)
    assert main(["--env-file", str(env), "--rotate"]) == 0
    assert _key_in(env) != old_key
    (backup,) = tmp_path.glob("alerts.env.bak.*")
    assert _key_in(backup) == old_key


@pytest.mark.skipif(not hasattr(os, "geteuid") or os.geteuid() == 0, reason="перевірка для не-root на POSIX")
def test_main_default_path_requires_root(capsys):
    assert main([]) == 2
    assert "root" in capsys.readouterr().err
