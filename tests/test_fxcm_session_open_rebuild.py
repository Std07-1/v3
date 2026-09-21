"""Перша хвилина M1 після перерви — з тікової історії брокера, а не «запечена» (ADR-0096 слайс E).

Навіщо цей файл. Перша M1 кожної сесії у FXCM приходить з open (і high або low) = close перед перервою;
полер комітить її один раз, тож на M1…D1 стоїть гігантська перша свічка. Тікова історія того ж дня має лише
справжні тіки (EUSTX50 21.09 06:01: 10 тіків від 6281.63, а m1 брокера o=l=6239.79 — close п'ятниці, v=10).
Тести йдуть по конвеєру: провайдер (t1 через єдиний вхід SDK) → sidecar/proxy (команда fetch_t1) →
чиста перебудова з гейтами → полер до коміту.
"""
from __future__ import annotations

import datetime as dt
import logging
import types

import numpy as np
import pytest

from runtime.ingest.broker.fxcm import provider as provider_mod

FIRST_TICK = object()
EUSTX50_OPEN_MS = int(dt.datetime(2026, 9, 21, 6, 1, tzinfo=dt.timezone.utc).timestamp() * 1000)


def _ms(iso: str) -> int:
    return int(dt.datetime.fromisoformat(iso).replace(tzinfo=dt.timezone.utc).timestamp() * 1000)


def _sdk_tick_rows(ticks):
    """Рядки t1 у формі, яку віддає ForexConnect.get_history (forexconnect/ForexConnect.py:462)."""
    rows = np.zeros(len(ticks), np.dtype([("Date", "M8[ns]"), ("Bid", "f8"), ("Ask", "f8")]))
    for idx, (iso, bid) in enumerate(ticks):
        rows[idx]["Date"] = np.datetime64(iso)
        rows[idx]["Bid"] = bid
        rows[idx]["Ask"] = bid + 1.2
    return rows


class _FakeForexConnect:
    calls: list = []
    reply = None

    def login(self, *args, **kwargs):
        return None

    def logout(self):
        return None

    def get_history(self, *args, **kwargs):
        type(self).calls.append((args, kwargs))
        if isinstance(type(self).reply, Exception):
            raise type(self).reply
        return type(self).reply


@pytest.fixture()
def fake_sdk(monkeypatch):
    _FakeForexConnect.calls = []
    _FakeForexConnect.reply = []
    fxcorepy = types.SimpleNamespace(O2GCandleOpenPriceMode=types.SimpleNamespace(FIRST_TICK=FIRST_TICK))
    monkeypatch.setattr(provider_mod, "ForexConnect", _FakeForexConnect)
    monkeypatch.setattr(provider_mod, "fxcorepy", fxcorepy)
    return _FakeForexConnect


def _provider():
    return provider_mod.FxcmHistoryProvider(user_id="u", password="p", url="x", connection="Demo")


# ---------------------------------------------------------------------------
# P1 — провайдер: тіки t1 через єдиний вхід SDK
# ---------------------------------------------------------------------------


def test_t1_ticks_come_through_the_single_sdk_entry_for_the_exact_window(fake_sdk):
    """Запит іде тим самим `_get_history` (гейт ADR-0096: один виклик SDK, явний режим), таймфрейм t1,
    вікно [from, to] — рівно хвилина; numpy-час тіку → epoch ms, ціна — Bid."""
    fake_sdk.reply = _sdk_tick_rows([("2026-09-21T06:01:05.250", 6281.63), ("2026-09-21T06:01:59.900", 6283.10)])
    with _provider() as provider:
        ticks = provider.fetch_t1_bid_ticks("EUSTX50", EUSTX50_OPEN_MS, EUSTX50_OPEN_MS + 60_000)

    assert ticks == [(_ms("2026-09-21T06:01:05.250"), 6281.63), (_ms("2026-09-21T06:01:59.900"), 6283.10)]
    (args, kwargs), = fake_sdk.calls
    symbol, timeframe, date_from, date_to, quotes_count = args
    assert (symbol, timeframe, quotes_count) == ("EUSTX50", "t1", -1)
    assert date_from == dt.datetime(2026, 9, 21, 6, 1, tzinfo=dt.timezone.utc)
    assert date_to == dt.datetime(2026, 9, 21, 6, 2, tzinfo=dt.timezone.utc)
    assert kwargs.get("candle_open_price_mode") is FIRST_TICK


def test_t1_sdk_failure_is_none_and_loud_not_an_empty_minute(fake_sdk, caplog):
    """Помилка SDK ≠ «тіків немає»: None + WARN + last_error, щоб полер не сплутав відмову з порожньою хвилиною."""
    fake_sdk.reply = RuntimeError("PriceHistoryCommunicator: request failed")
    with caplog.at_level(logging.WARNING):
        with _provider() as provider:
            ticks = provider.fetch_t1_bid_ticks("XAU/USD", 1, 60_001)
            last_error = provider.consume_last_error()
    assert ticks is None
    assert last_error is not None and "t1" in last_error[0]
    assert "FXCM_TICK_HISTORY_ERROR symbol=XAU/USD" in caplog.text


def test_broken_tick_rows_are_counted_loudly_not_guessed(caplog):
    rows = [{"Date": dt.datetime(2026, 9, 20, 22, 1, 3, tzinfo=dt.timezone.utc), "Bid": 4375.62},
            {"Date": dt.datetime(2026, 9, 20, 22, 1, 4, tzinfo=dt.timezone.utc)}]
    with caplog.at_level(logging.WARNING):
        ticks = provider_mod.normalize_tick_rows("XAU/USD", rows)
    assert ticks == [(_ms("2026-09-20T22:01:03"), 4375.62)]
    assert "FXCM_TICK_ROWS_SKIPPED symbol=XAU/USD skipped=1 of=2" in caplog.text


def test_ticks_are_the_same_price_side_as_m1_candles():
    """Свічка SDK має лише Bid*/Ask* (без «Open») → M1 = Bid; тіки для її перебудови — теж Bid, не Ask/mid."""
    candle = {"Date": dt.datetime(2026, 9, 20, 22, 1, tzinfo=dt.timezone.utc),
              "BidOpen": 4375.62, "BidHigh": 4378.0, "BidLow": 4374.1, "BidClose": 4377.5,
              "AskOpen": 4376.02, "AskHigh": 4378.4, "AskLow": 4374.5, "AskClose": 4377.9, "Volume": 388}
    bar, = provider_mod.normalize_history_to_bars("XAU/USD", 60, [candle], src="history")
    assert (bar.o, bar.c) == (candle["BidOpen"], candle["BidClose"])
    assert provider_mod.TICK_PRICE_FIELD == "Bid"
