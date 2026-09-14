"""Ціна відкриття свічок від FXCM — перший тік, а не закриття попередньої свічки (ADR-0096).

Навіщо цей файл. SDK forexconnect за замовчуванням віддає свічки в режимі PREVIOUS_CLOSE
(`ForexConnect.get_history(..., candle_open_price_mode=PREVIOUS_CLOSE)`). Провайдер параметр не
передавав, а коментар поруч стверджував «FIRST_TICK default». Уся історія M1 на проді — 99.95–100%
свічок із open == close попередньої; перша свічка кожної сесії тягнула вчорашню ціну в O і L/H, а за
нею D1/H4/…: XAU D1 27.07.2026 у нас O=L=4055.42, у FXCM FIRST_TICK і TradingView — O≈4090.

Тести не довіряють коментарям: перевіряють, ЯКИЙ режим реально йде в SDK, і що іншої дороги до SDK
в репо немає.
"""
from __future__ import annotations

import ast
import logging
import pathlib
import types

import pytest

from runtime.ingest.broker.fxcm import provider as provider_mod

REPO = pathlib.Path(__file__).resolve().parents[1]
FIRST_TICK = object()
PREVIOUS_CLOSE = object()


class _FakeForexConnect:
    calls: list = []

    def login(self, *args, **kwargs):
        return None

    def logout(self):
        return None

    def get_history(self, *args, **kwargs):
        type(self).calls.append((args, kwargs))
        return []


@pytest.fixture()
def fake_sdk(monkeypatch):
    _FakeForexConnect.calls = []
    fxcorepy = types.SimpleNamespace(
        O2GCandleOpenPriceMode=types.SimpleNamespace(FIRST_TICK=FIRST_TICK, PREVIOUS_CLOSE=PREVIOUS_CLOSE)
    )
    monkeypatch.setattr(provider_mod, "ForexConnect", _FakeForexConnect)
    monkeypatch.setattr(provider_mod, "fxcorepy", fxcorepy)
    return _FakeForexConnect


def _provider():
    return provider_mod.FxcmHistoryProvider(user_id="u", password="p", url="x", connection="Demo")


@pytest.mark.parametrize("fetch", ["m1", "tf"])
def test_every_history_request_asks_the_sdk_for_first_tick(fake_sdk, fetch):
    """Суть фіксу: у SDK іде саме FIRST_TICK — і для M1, і для старших TF."""
    with _provider() as provider:
        if fetch == "m1":
            provider.fetch_last_n_m1("XAU/USD", n=5)
        else:
            provider.fetch_last_n_tf("XAU/USD", tf_s=14400, n=5)
    assert len(fake_sdk.calls) == 1
    _args, kwargs = fake_sdk.calls[0]
    assert kwargs.get("candle_open_price_mode") is FIRST_TICK


def test_missing_first_tick_enum_refuses_loudly_instead_of_sdk_default(monkeypatch):
    """Без enum SDK мовчки віддав би PREVIOUS_CLOSE — краще не стартувати зовсім."""
    monkeypatch.setattr(provider_mod, "ForexConnect", _FakeForexConnect)
    monkeypatch.setattr(provider_mod, "fxcorepy", types.SimpleNamespace())
    with pytest.raises(RuntimeError, match="FXCM_OPEN_PRICE_MODE_UNAVAILABLE"):
        _provider()


def test_session_start_logs_the_open_price_mode(fake_sdk, caplog):
    """Режим видно в логах кожного старту — логи WIRED живуть ~7 днів, а питання «яким режимом
    зібрано бар» виникає через місяці."""
    with caplog.at_level(logging.INFO):
        with _provider():
            pass
    assert "FXCM_HISTORY_OPEN_MODE mode=FIRST_TICK" in caplog.text


def _history_calls():
    """Усі виклики `.get_history(...)` у робочому коді репо — (шлях, рядок, чи є candle_open_price_mode)."""
    found = []
    for top in ("runtime", "tools", "app", "core"):
        for path in (REPO / top).rglob("*.py"):
            rel = path.relative_to(REPO).as_posix()
            if "/_archive" in rel:
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeDecodeError):
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                        and node.func.attr == "get_history":
                    has_mode = any(k.arg == "candle_open_price_mode" for k in node.keywords)
                    found.append((rel, node.lineno, has_mode))
    return found


def test_the_only_road_to_sdk_history_is_the_provider_with_explicit_mode():
    """Гейт: дефект жив, бо режим залежав від дефолту SDK. Другий виклик get_history повз провайдер або
    без явного режиму повернув би PREVIOUS_CLOSE непомітно."""
    calls = _history_calls()
    assert calls, "гейт нічого не знайшов — перевір, чи не змінився шлях провайдера"
    offenders = [c for c in calls if c[0] != "runtime/ingest/broker/fxcm/provider.py" or not c[2]]
    assert offenders == [], offenders
