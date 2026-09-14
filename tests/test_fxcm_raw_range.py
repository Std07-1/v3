"""Сирий діапазон M1 від FXCM для staging ремонту історії (ADR-0096 §3.3 B, слайс B0).

Навіщо окремий метод провайдера. Ремонт перезабирає добу M1 у FIRST_TICK і мусить бачити рядки брокера
такими, як їх віддав SDK: нормалізація `normalize_history_to_bars` розтягує H/L до «запеченого» open
(§1.4), а `fetch_last_n_m1` ковтає помилку SDK і повертає [] — для staging «порожньо» і «впало» мають
бути різними. Інструмент не має права кликати SDK напряму (гейт у test_fxcm_open_price_mode.py), тож
уся ця поведінка живе тут і перевіряється на фейковому SDK.
"""
from __future__ import annotations

import datetime as dt
import logging
import types

import pytest

from runtime.ingest.broker.fxcm import provider as provider_mod

FIRST_TICK = object()
UTC = dt.timezone.utc
DAY_FROM = dt.datetime(2026, 7, 26, 23, 59, tzinfo=UTC)
DAY_TO = dt.datetime(2026, 7, 28, 0, 0, tzinfo=UTC)


def _sdk_row(minute, o, h, low, c, volume=7):
    return {"Date": dt.datetime(2026, 7, 27, 0, minute, tzinfo=UTC),
            "BidOpen": o, "BidHigh": h, "BidLow": low, "BidClose": c,
            "AskOpen": o + 0.3, "AskHigh": h + 0.3, "AskLow": low + 0.3, "AskClose": c + 0.3, "Volume": volume}


class _FakeForexConnect:
    calls: list = []
    rows: list = []
    error = None

    def login(self, *args, **kwargs):
        return None

    def logout(self):
        return None

    def get_history(self, *args, **kwargs):
        type(self).calls.append((args, kwargs))
        if type(self).error is not None:
            raise type(self).error
        return list(type(self).rows)


@pytest.fixture()
def fake_sdk(monkeypatch):
    _FakeForexConnect.calls = []
    _FakeForexConnect.rows = []
    _FakeForexConnect.error = None
    fxcorepy = types.SimpleNamespace(O2GCandleOpenPriceMode=types.SimpleNamespace(FIRST_TICK=FIRST_TICK))
    monkeypatch.setattr(provider_mod, "ForexConnect", _FakeForexConnect)
    monkeypatch.setattr(provider_mod, "fxcorepy", fxcorepy)
    return _FakeForexConnect


def _provider():
    return provider_mod.FxcmHistoryProvider(user_id="u", password="p", url="x", connection="Demo")


def test_raw_range_sends_explicit_from_to_and_first_tick(fake_sdk):
    """Ловить: метод без явного date_from (SDK віддав би N останніх барів) або без режиму FIRST_TICK."""
    with _provider() as provider:
        provider.fetch_m1_raw_range("XAU/USD", DAY_FROM, DAY_TO)
    assert len(fake_sdk.calls) == 1
    args, kwargs = fake_sdk.calls[0]
    assert args == ("XAU/USD", "m1", DAY_FROM, DAY_TO, -1)
    assert kwargs.get("candle_open_price_mode") is FIRST_TICK


def test_raw_range_keeps_broker_values_unnormalized(fake_sdk):
    """Ловить нормалізацію: «запечений» рядок 13.09 22:01 (o 4346.23 > h 4337.69) мусить лишитись сирим —
    normalize_ohlc дав би BidHigh 4346.23 і сховав би ознаку «не перший тік»."""
    fake_sdk.rows = [_sdk_row(1, 4346.23, 4337.69, 4330.62, 4331.55)]
    with _provider() as provider:
        (row,) = provider.fetch_m1_raw_range("XAU/USD", DAY_FROM, DAY_TO)
    assert row["BidOpen"] == 4346.23 and row["BidHigh"] == 4337.69 and row["BidLow"] == 4330.62
    assert row["open_time_ms"] == int(dt.datetime(2026, 7, 27, 0, 1, tzinfo=UTC).timestamp() * 1000)
    assert row["Volume"] == 7 and isinstance(row["Volume"], int)
    assert list(row) == ["open_time_ms"] + list(provider_mod.RAW_ROW_FIELDS)


def test_raw_range_raises_on_sdk_error_instead_of_empty_list(fake_sdk):
    """Ловить ковтання помилки: fetch_last_n_m1 на тій самій помилці повертає [] (контроль нижче)."""
    fake_sdk.error = RuntimeError("history request failed")
    with _provider() as provider:
        with pytest.raises(RuntimeError, match="history request failed"):
            provider.fetch_m1_raw_range("XAU/USD", DAY_FROM, DAY_TO)
        assert provider.fetch_last_n_m1("XAU/USD", n=5) == []


@pytest.mark.parametrize("window", ["naive_from", "naive_to", "inverted", "equal"])
def test_raw_range_rejects_naive_or_inverted_window(fake_sdk, window):
    date_from, date_to = {
        "naive_from": (DAY_FROM.replace(tzinfo=None), DAY_TO),
        "naive_to": (DAY_FROM, DAY_TO.replace(tzinfo=None)),
        "inverted": (DAY_TO, DAY_FROM),
        "equal": (DAY_FROM, DAY_FROM),
    }[window]
    with _provider() as provider:
        with pytest.raises(ValueError):
            provider.fetch_m1_raw_range("XAU/USD", date_from, date_to)
    assert fake_sdk.calls == []


def test_raw_range_row_missing_field_fails_loud(fake_sdk):
    row = _sdk_row(1, 4089.98, 4093.19, 4086.33, 4092.36)
    del row["AskClose"]
    fake_sdk.rows = [row]
    with _provider() as provider:
        with pytest.raises(ValueError, match="FXCM_RAW_ROW_FIELD_MISSING field=AskClose"):
            provider.fetch_m1_raw_range("XAU/USD", DAY_FROM, DAY_TO)


def test_raw_range_requires_open_session(fake_sdk):
    with pytest.raises(RuntimeError, match="сесія не відкрита"):
        _provider().fetch_m1_raw_range("XAU/USD", DAY_FROM, DAY_TO)


def test_normalize_uses_shared_open_outside_range_predicate(monkeypatch, caplog):
    """Ловить інлайн-копію умови в normalize_history_to_bars: предикат підмінено — лог мусить іти за ним.

    Контроль — справжній предикат на тому самому рядку в межах мовчить."""
    rows = [{"Date": dt.datetime(2026, 7, 26, 22, 1, tzinfo=UTC), "BidOpen": 4089.98, "BidHigh": 4093.19,
             "BidLow": 4086.33, "BidClose": 4092.36, "Volume": 10.0}]
    with caplog.at_level(logging.WARNING):
        provider_mod.normalize_history_to_bars("XAU/USD", 60, rows, src="history")
    assert "FXCM_OPEN_NOT_FIRST_TICK" not in caplog.text
    monkeypatch.setattr(provider_mod, "is_open_outside_range", lambda o, h, low: True)
    with caplog.at_level(logging.WARNING):
        provider_mod.normalize_history_to_bars("XAU/USD", 60, rows, src="history")
    assert "FXCM_OPEN_NOT_FIRST_TICK symbol=XAU/USD tf_s=60 bars=1 of=1" in caplog.text


def test_open_outside_range_predicate_boundaries():
    assert provider_mod.is_open_outside_range(4346.23, 4337.69, 4330.62)
    assert provider_mod.is_open_outside_range(4330.0, 4337.69, 4330.62)
    assert not provider_mod.is_open_outside_range(4337.69, 4337.69, 4330.62)
    assert not provider_mod.is_open_outside_range(4330.62, 4337.69, 4330.62)
