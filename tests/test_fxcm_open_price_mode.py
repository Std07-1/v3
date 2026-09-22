"""Ціна відкриття свічок FXCM — close попередньої свічки (PREVIOUS_CLOSE), бо це і є бар TradingView (ADR-0100).

Навіщо цей файл. Режим ціни відкриття визначає зміст кожного бару, а SDK forexconnect має свій дефолт
(`ForexConnect.get_history(..., candle_open_price_mode=...)`, ForexConnect.py:423). Поки провайдер параметр не
передавав, режим залежав від дефолту SDK і від хибного коментаря поруч — ADR-0096 §3.1 закрив саме цю залежність
(константа в одній точці, гучна відмова без enum, лог режиму, AST-гейт). ADR-0100 лишає механізм і міняє значення:
вимір власника 21–22.09 показав, що TV FX:<символ> віддає саме бари FXCM у PREVIOUS_CLOSE — open == close
попереднього бару і на 15m, і на D1, і через денну перерву 21–22 UTC, і через вихідні (XAU 15m 21.09 22:00
O 4342.62 H 4350.60 L 4342.62 C 4349.61). FIRST_TICK (ADR-0096 слайс A, 15.09–22.09) давав інший open на кожному
барі — 26 706 барів розходження з TV.

Тести не довіряють коментарям: перевіряють, ЯКИЙ режим реально йде в SDK, що іншої дороги до SDK в репо немає, і
що розрив ланцюжка `o == prev.c` (тобто втрата паритету з TV) чутний у логах.
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
def test_every_history_request_asks_the_sdk_for_previous_close(fake_sdk, fetch):
    """Суть контракту: у SDK іде саме PREVIOUS_CLOSE — і для M1, і для старших TF (TV агрегує M1, D1 нативний)."""
    with _provider() as provider:
        if fetch == "m1":
            provider.fetch_last_n_m1("XAU/USD", n=5)
        else:
            provider.fetch_last_n_tf("XAU/USD", tf_s=14400, n=5)
    assert len(fake_sdk.calls) == 1
    _args, kwargs = fake_sdk.calls[0]
    assert kwargs.get("candle_open_price_mode") is PREVIOUS_CLOSE


def test_the_mode_is_never_first_tick_again(fake_sdk):
    """Явний гейт проти повернення ADR-0096 слайса A: FIRST_TICK ≠ бар TV, 26 706 барів розходження."""
    assert provider_mod.OPEN_PRICE_MODE_NAME == "PREVIOUS_CLOSE"
    with _provider() as provider:
        provider.fetch_last_n_m1("XAU/USD", n=5)
    _args, kwargs = fake_sdk.calls[0]
    assert kwargs.get("candle_open_price_mode") is not FIRST_TICK


def test_missing_mode_enum_refuses_loudly_instead_of_sdk_default(monkeypatch):
    """Без enum режим звівся б на дефолт SDK — саме цю залежність закрито, тож краще не стартувати зовсім
    (дефолт може змінитись з версією SDK і зміну ніхто не побачить)."""
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
    assert "FXCM_HISTORY_OPEN_MODE mode=PREVIOUS_CLOSE" in caplog.text


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
    без явного режиму повернув би дефолтний режим непомітно."""
    calls = _history_calls()
    assert calls, "гейт нічого не знайшов — перевір, чи не змінився шлях провайдера"
    offenders = [c for c in calls if c[0] != "runtime/ingest/broker/fxcm/provider.py" or not c[2]]
    assert offenders == [], offenders


def _row(minute, o, h, low, c):
    import datetime as dt

    return {"Date": dt.datetime(2026, 9, 13, 22, minute, tzinfo=dt.timezone.utc),
            "BidOpen": o, "BidHigh": h, "BidLow": low, "BidClose": c, "Volume": 10.0}


def test_open_not_equal_to_previous_close_is_reported_loudly(caplog):
    """Вимір паритету з TV: у PREVIOUS_CLOSE брокер копіює close попередньої свічки в open, тож розрив
    ланцюжка = бар не такий, як у TV. Без сигналу така свічка записалась би мовчки (епоха FIRST_TICK —
    26 706 барів)."""
    rows = [_row(1, 4330.00, 4335.00, 4329.00, 4331.55), _row(2, 4340.00, 4345.00, 4339.00, 4344.00)]
    with caplog.at_level(logging.WARNING):
        bars = provider_mod.normalize_history_to_bars("XAU/USD", 60, rows, src="history")
    assert len(bars) == 2
    assert "FXCM_OPEN_NOT_PREV_CLOSE symbol=XAU/USD tf_s=60 bars=1 of=2" in caplog.text


def test_gap_bar_with_open_outside_the_range_stays_quiet(caplog):
    """Регресія проти рейки FIRST_TICK: у PREVIOUS_CLOSE open (= close перед перервою) законно лежить поза
    [low, high] — H/L розтягуються до нього, і саме такий бар показує TV (XAU 21.09 22:00: L == O). Це не дефект.
    Числа — реальна перша хвилина після перерви XAU 14.09 22:01 (o 4346.23, h 4337.69)."""
    rows = [_row(0, 4340.00, 4347.00, 4339.00, 4346.23), _row(1, 4346.23, 4337.69, 4330.62, 4331.55)]
    with caplog.at_level(logging.WARNING):
        bars = provider_mod.normalize_history_to_bars("XAU/USD", 60, rows, src="history")
    assert [b.h for b in bars] == [4347.00, 4346.23] and bars[1].low == 4330.62
    assert "FXCM_OPEN_NOT_PREV_CLOSE" not in caplog.text


def test_single_bar_batch_has_nothing_to_chain_and_stays_quiet(caplog):
    """Попередник першого бару батчу лишився за межею запиту — рейка про нього не здогадується."""
    rows = [_row(1, 4089.98, 4093.19, 4086.33, 4092.36)]
    with caplog.at_level(logging.WARNING):
        provider_mod.normalize_history_to_bars("XAU/USD", 60, rows, src="history")
    assert "FXCM_OPEN_NOT_PREV_CLOSE" not in caplog.text


def test_chain_measure_tolerates_only_float_representation_noise():
    """Допуск рейки — представлення float брокера (30287.710000000003), а не крок ціни: ½ кроку XAG (0.0005)
    вже мусить бути видно, інакше рейка проспить справжню втрату паритету."""
    rows = [_row(1, 63.0000, 63.0100, 62.9900, 63.0000), _row(2, 63.0005, 63.0100, 62.9900, 63.0100)]
    bars = provider_mod.normalize_history_to_bars("XAG/USD", 60, rows, src="history")
    assert provider_mod.open_chain_breaks(bars) == [bars[1].open_time_ms]

    noisy = [_row(1, 63.0, 63.01, 62.99, 63.01), _row(2, 63.01 + 1e-13, 63.02, 63.0, 63.02)]
    assert provider_mod.open_chain_breaks(provider_mod.normalize_history_to_bars("XAG/USD", 60, noisy, src="history")) == []
