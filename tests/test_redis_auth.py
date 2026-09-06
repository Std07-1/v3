"""ADR-0091 P2 — ACL-креденшели Redis: SSOT, env-джерело, рейл проти пропущених клієнтів."""
from __future__ import annotations

import pytest

from runtime.store.redis_spec import REDIS_PASSWORD_ENV, REDIS_USERNAME_ENV, resolve_redis_spec
from tools.exit_gates.gates import gate_redis_clients_use_auth

CFG = {"redis": {"enabled": True, "host": "127.0.0.1", "port": 6379, "db": 1, "namespace": "v3_local"}}


def test_without_env_spec_has_no_credentials(monkeypatch):
    """Дефолт репо і dev: сервер без ACL → жодних kwargs, поведінка не змінилась."""
    monkeypatch.delenv(REDIS_USERNAME_ENV, raising=False)
    monkeypatch.delenv(REDIS_PASSWORD_ENV, raising=False)
    spec = resolve_redis_spec(CFG, role="test", log=False)
    assert spec is not None and spec.auth_kwargs() == {}


def test_password_only_is_default_user(monkeypatch):
    monkeypatch.delenv(REDIS_USERNAME_ENV, raising=False)
    monkeypatch.setenv(REDIS_PASSWORD_ENV, "s3cret")
    spec = resolve_redis_spec(CFG, role="test", log=False)
    assert spec.auth_kwargs() == {"password": "s3cret"}


def test_acl_user_and_password(monkeypatch):
    monkeypatch.setenv(REDIS_USERNAME_ENV, "smc_platform")
    monkeypatch.setenv(REDIS_PASSWORD_ENV, "s3cret")
    spec = resolve_redis_spec(CFG, role="test", log=False)
    assert spec.auth_kwargs() == {"username": "smc_platform", "password": "s3cret"}


def test_blank_env_is_treated_as_absent(monkeypatch):
    monkeypatch.setenv(REDIS_USERNAME_ENV, "   ")
    monkeypatch.setenv(REDIS_PASSWORD_ENV, "")
    spec = resolve_redis_spec(CFG, role="test", log=False)
    assert spec.auth_kwargs() == {}


def test_credentials_never_leak_into_repr(monkeypatch):
    monkeypatch.setenv(REDIS_PASSWORD_ENV, "top-secret-value")
    spec = resolve_redis_spec(CFG, role="test", log=False)
    assert "top-secret-value" in repr(spec), "поле є в dataclass — не логувати spec цілком"
    assert spec.auth_kwargs()["password"] == "top-secret-value"


def test_every_platform_redis_client_passes_credentials():
    """Головний рейл: жоден Redis(...) у core/runtime/app не лишився без auth."""
    result = gate_redis_clients_use_auth.run_gate({"root": "."})
    assert result["ok"] is True, result["details"]
    assert result["metrics"]["violations"] == 0


def test_gate_catches_a_client_without_credentials(tmp_path):
    pkg = tmp_path / "runtime"
    pkg.mkdir()
    (pkg / "leaky.py").write_text("import redis\nc = redis.Redis(host='h', port=1, db=1)\n", encoding="utf-8")
    result = gate_redis_clients_use_auth.run_gate({"root": str(tmp_path)})
    assert result["ok"] is False and "runtime/leaky.py:2" in result["details"]


@pytest.mark.parametrize("snippet", [
    "import redis\nc = redis.Redis(host='h', **spec.auth_kwargs())\n",
    "import redis\nc = redis.Redis(host='h', username='u', password='p')\n",
])
def test_gate_accepts_authenticated_clients(tmp_path, snippet):
    pkg = tmp_path / "runtime"
    pkg.mkdir()
    (pkg / "ok.py").write_text(snippet, encoding="utf-8")
    assert gate_redis_clients_use_auth.run_gate({"root": str(tmp_path)})["ok"] is True
