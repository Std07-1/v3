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


# ---------------------------------------------------------------------------
# P2 — протокол sidecar ↔ worker: команда fetch_t1 з тим самим reply-механізмом
# ---------------------------------------------------------------------------
import json  # noqa: E402

from runtime.ingest import broker_sidecar  # noqa: E402
from runtime.ingest.m1_ingestion_worker import BrokerRedisProxy  # noqa: E402

NS = "ns"
CMD_KEY = "ns:broker:m1:cmd"
BARS_KEY = "ns:broker:m1:bars"


class _TickProvider:
    """Провайдер sidecar-боку: віддає наперед задані тіки (або None/виняток) і пам'ятає запити."""

    def __init__(self, ticks=None, error=None, raises=None):
        self.ticks, self.error, self.raises = ticks, error, raises
        self.calls = []
        self._last_error = None

    def fetch_t1_bid_ticks(self, symbol, from_ms, to_ms):
        self.calls.append((symbol, from_ms, to_ms))
        if self.raises is not None:
            raise self.raises
        if self.error is not None:
            self._last_error = ("помилка t1 " + symbol, self.error)
            return None
        return list(self.ticks or [])

    def fetch_last_n_m1(self, symbol, n, date_to_utc=None):
        return []

    def consume_last_error(self):
        err, self._last_error = self._last_error, None
        return err


class _LoopbackRedis:
    """Redis, де RPUSH у чергу команд синхронно виконує СПРАВЖНІЙ broker_sidecar._handle_command.

    Так тест проходить реальний протокол обох боків: proxy (3.11) серіалізує → sidecar (3.7-код) обробляє →
    реплай у per-request ключ → proxy розбирає.
    """

    def __init__(self, provider, answer=True):
        self.provider, self.answer = provider, answer
        self.queues, self.deleted, self.reconnects = {}, [], []

    def rpush(self, key, value):
        self.queues.setdefault(key, []).append(value)
        if key == CMD_KEY and self.answer:
            raw = self.queues[key].pop()
            self.reconnects.append(broker_sidecar._handle_command(self.provider, raw, self, BARS_KEY))
        return 1

    def blpop(self, key, timeout=None):
        queue = self.queues.get(key) or []
        return (key, queue.pop(0)) if queue else None

    def llen(self, key):
        return len(self.queues.get(key, []))

    def delete(self, key):
        self.deleted.append(key)
        self.queues.pop(key, None)

    def expire(self, key, ttl_s):
        return True

    def ltrim(self, key, start, end):
        return True


def test_fetch_t1_roundtrip_through_real_sidecar_handler_returns_tick_tuples():
    ticks = [(EUSTX50_OPEN_MS + 5_250, 6281.63), (EUSTX50_OPEN_MS + 59_900, 6283.10)]
    provider = _TickProvider(ticks=ticks)
    redis_cli = _LoopbackRedis(provider)

    got = BrokerRedisProxy(redis_cli, NS).fetch_t1_bid_ticks("EUSTX50", EUSTX50_OPEN_MS, EUSTX50_OPEN_MS + 60_000)

    assert got == ticks
    assert provider.calls == [("EUSTX50", EUSTX50_OPEN_MS, EUSTX50_OPEN_MS + 60_000)]
    assert redis_cli.reconnects == [False]
    assert any(k.startswith(BARS_KEY + ":") for k in redis_cli.deleted)  # per-request ключ прибрано


def test_fetch_m1_still_roundtrips_after_the_shared_request_refactor():
    """Контроль рефакторингу проксі: fetch_m1 іде тим самим каналом і дає той самий результат."""
    redis_cli = _LoopbackRedis(_TickProvider())
    assert BrokerRedisProxy(redis_cli, NS).fetch_last_n_m1("XAU/USD", 5) == []
    assert redis_cli.reconnects == [False]


def test_t1_failure_is_loud_none_and_does_not_reconnect_the_session(caplog):
    """Відмова t1-історії: error-реплай, proxy → None, sidecar НЕ рве сесію (OFFERS/тіки/fetch_m1 живуть),
    а last_error спожито — наступна fetch_m1 не перепідключиться через чужу помилку."""
    provider = _TickProvider(error="no tick history")
    redis_cli = _LoopbackRedis(provider)
    with caplog.at_level(logging.WARNING):
        got = BrokerRedisProxy(redis_cli, NS).fetch_t1_bid_ticks("XAU/USD", 0, 60_000)
    assert got is None
    assert redis_cli.reconnects == [False]
    assert provider.consume_last_error() is None
    assert "BROKER_SIDECAR_T1_ERROR symbol=XAU/USD" in caplog.text
    assert "BROKER_PROXY_FETCH_ERROR cmd=fetch_t1 symbol=XAU/USD" in caplog.text


def test_t1_without_a_session_reconnects_like_fetch_m1():
    redis_cli = _LoopbackRedis(_TickProvider(raises=RuntimeError("FXCM сесія не відкрита.")))
    assert BrokerRedisProxy(redis_cli, NS).fetch_t1_bid_ticks("XAU/USD", 0, 60_000) is None
    assert redis_cli.reconnects == [True]


@pytest.mark.parametrize("from_ms, to_ms", [(0, 0), (60_000, 0), (0, 60_001), ("x", 60_000)])
def test_sidecar_refuses_a_window_other_than_one_minute_without_calling_the_broker(from_ms, to_ms):
    provider = _TickProvider(ticks=[(1, 1.0)])
    redis_cli = _LoopbackRedis(provider, answer=False)
    raw = json.dumps({"v": 1, "cmd": "fetch_t1", "req_id": "r1", "reply_to": BARS_KEY + ":r1", "symbol": "XAU/USD",
                      "from_ms": from_ms, "to_ms": to_ms})
    assert broker_sidecar._handle_command(provider, raw, redis_cli, BARS_KEY) is False
    reply = json.loads(redis_cli.queues[BARS_KEY + ":r1"][0])
    assert reply["error"].startswith("invalid_window") and reply["ticks"] == []
    assert provider.calls == []


def test_proxy_timeout_is_none_not_an_empty_minute(caplog, monkeypatch):
    import runtime.ingest.m1_ingestion_worker as worker_mod

    monkeypatch.setattr(worker_mod, "_BLPOP_TIMEOUT_S", 0)
    with caplog.at_level(logging.WARNING):
        got = BrokerRedisProxy(_LoopbackRedis(_TickProvider(), answer=False), NS).fetch_t1_bid_ticks("NAS100", 0, 60_000)
    assert got is None
    assert "BROKER_PROXY_TIMEOUT cmd=fetch_t1 symbol=NAS100" in caplog.text
