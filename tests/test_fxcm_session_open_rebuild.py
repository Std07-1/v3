"""Перша хвилина M1 після перерви — запечені компоненти з тікової історії брокера (ADR-0096 слайс E).

⚠️ Механізм ВИМКНЕНО рішенням власника 22.09 (ADR-0100): open першої M1 після перерви = close перед перервою, і
це рівно той бар, який показує TV FX: (PREVIOUS_CLOSE) — перебудова з тіків робила дірку відносно TV. Секція
`m1_poller.session_open_rebuild` лишається задокументованим rollback (`enabled: false`), тож покриття механізму
тут зберігається цілим: якщо його колись увімкнуть назад, воно мусить працювати так само, а не «як вийде».

Навіщо цей файл. Перша M1 кожної сесії у FXCM приходить з open (і high або low) = close перед перервою; полер
комітить її один раз. Тікова історія того ж дня має лише справжні тіки (EUSTX50 21.09 06:01: перший тік 6281.63,
а m1 брокера o=l=6239.79 — close п'ятниці, v=10). На T+8 с t1 і m1 не узгоджені ні за кількістю тіків, ні за
close — тому перебудовується лише запечене. Тести йдуть по конвеєру: провайдер (t1 через єдиний вхід SDK) →
sidecar/proxy (команда fetch_t1) → чиста перебудова → полер до коміту.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import json
import logging
import types

import numpy as np
import pytest

from core.model.bars import CandleBar
from runtime.ingest import broker_sidecar
from runtime.ingest import m1_session_open as so
from runtime.ingest.broker.fxcm import provider as provider_mod
from runtime.ingest.m1_ingestion_worker import BrokerRedisProxy
from runtime.ingest.polling import m1_poller as poller_mod

PREVIOUS_CLOSE = object()
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
    fxcorepy = types.SimpleNamespace(O2GCandleOpenPriceMode=types.SimpleNamespace(PREVIOUS_CLOSE=PREVIOUS_CLOSE))
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
    assert kwargs.get("candle_open_price_mode") is PREVIOUS_CLOSE


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


@pytest.mark.parametrize("tick_count, expect_error", [(20000, False), (20001, True)])
def test_sidecar_refuses_an_oversized_tick_reply_instead_of_truncating(tick_count, expect_error):
    """Запобіжник 20000 тіків: більше — error too_many_ticks і порожні ticks (обрізання дало б хибний діапазон)."""
    provider = _TickProvider(ticks=[(EUSTX50_OPEN_MS + i % 60_000, 6281.63) for i in range(tick_count)])
    redis_cli = _LoopbackRedis(provider, answer=False)
    raw = json.dumps({"v": 1, "cmd": "fetch_t1", "req_id": "r1", "reply_to": BARS_KEY + ":r1", "symbol": "XAU/USD",
                      "from_ms": EUSTX50_OPEN_MS, "to_ms": EUSTX50_OPEN_MS + 60_000})
    assert broker_sidecar._handle_command(provider, raw, redis_cli, BARS_KEY) is False
    reply = json.loads(redis_cli.queues[BARS_KEY + ":r1"][0])
    if expect_error:
        assert reply["error"].startswith("too_many_ticks n=20001 limit=20000") and reply["ticks"] == []
    else:
        assert reply["error"] is None and len(reply["ticks"]) == 20000


@pytest.mark.parametrize("cmd_name, symbol", [("fetch_t2", "XAU/USD"), ("fetch_t1", ""), ("", "XAU/USD")])
def test_sidecar_answers_an_unknown_command_instead_of_silence(cmd_name, symbol):
    """Рев'ю D-04: воркер чекає реплай 15 с — невідома команда (розсинхрон версій) отримує явну відмову одразу."""
    redis_cli = _LoopbackRedis(_TickProvider(), answer=False)
    raw = json.dumps({"v": 1, "cmd": cmd_name, "req_id": "r9", "reply_to": BARS_KEY + ":r9", "symbol": symbol})
    assert broker_sidecar._handle_command(_TickProvider(), raw, redis_cli, BARS_KEY) is False
    reply = json.loads(redis_cli.queues[BARS_KEY + ":r9"][0])
    assert reply["req_id"] == "r9" and reply["error"].startswith("unknown_cmd")


def test_sidecar_unknown_command_without_reply_to_stays_off_the_legacy_queue():
    redis_cli = _LoopbackRedis(_TickProvider(), answer=False)
    raw = json.dumps({"v": 1, "cmd": "fetch_t2", "req_id": "r9", "symbol": "XAU/USD"})
    broker_sidecar._handle_command(_TickProvider(), raw, redis_cli, BARS_KEY)
    assert redis_cli.queues == {}


class _CommandRenamingRedis(_LoopbackRedis):
    """Loopback, де команда приходить до sidecar під іншим іменем — імітація sidecar, що її не знає."""

    def rpush(self, key, value):
        if key == CMD_KEY:
            renamed = dict(json.loads(value), cmd="fetch_t9")
            value = json.dumps(renamed)
        return super().rpush(key, value)


def test_proxy_gets_unknown_cmd_as_a_loud_none(caplog):
    """Проксі (новий воркер) бачить відмову як None з WARN одразу, а не як 15 с тиші."""
    with caplog.at_level(logging.WARNING):
        got = BrokerRedisProxy(_CommandRenamingRedis(_TickProvider()), NS).fetch_t1_bid_ticks("XAU/USD", 0, 60_000)
    assert got is None
    assert "BROKER_PROXY_FETCH_ERROR cmd=fetch_t1 symbol=XAU/USD err=unknown_cmd" in caplog.text


def test_proxy_timeout_is_none_not_an_empty_minute(caplog, monkeypatch):
    import runtime.ingest.m1_ingestion_worker as worker_mod

    monkeypatch.setattr(worker_mod, "_BLPOP_TIMEOUT_S", 0)
    with caplog.at_level(logging.WARNING):
        got = BrokerRedisProxy(_LoopbackRedis(_TickProvider(), answer=False), NS).fetch_t1_bid_ticks("NAS100", 0, 60_000)
    assert got is None
    assert "BROKER_PROXY_TIMEOUT cmd=fetch_t1 symbol=NAS100" in caplog.text


# ---------------------------------------------------------------------------
# P3 — чиста перебудова: запечений = open поза діапазоном тіків хвилини
# ---------------------------------------------------------------------------
XAU_OPEN_MS = _ms("2026-09-20T22:01:00")
NAS_OPEN_MS = _ms("2026-09-20T22:00:00")


def _m1(symbol, open_ms, o, h, low, c, v, extensions=None) -> CandleBar:
    return CandleBar(symbol=symbol, tf_s=60, open_time_ms=open_ms, close_time_ms=open_ms + 60_000, o=o, h=h, low=low,
                     c=c, v=v, complete=True, src="history", extensions=dict(extensions or {}))


def _spread(open_ms, bids):
    """Тіки рівномірно всередині хвилини, починаючи з +5 с (як у пробі EUSTX50: 06:01:05…06:01:59)."""
    step_ms = 54_000 // max(1, len(bids) - 1)
    return [(open_ms + 5_000 + i * step_ms, bid) for i, bid in enumerate(bids)]


# Проба 21.09 (/tmp/p5/t1_probe.py): m1 o=l=6239.79 (ціна п'ятниці) h=6284.14 c=6281.63 v=10; тіки 06:01:05…06:01:59,
# перший і останній 6281.63, max 6284.14, min 6281.63. Проміжні ціни тіків — ілюстрація в цих межах.
EUSTX50_BIDS = [6281.63, 6282.40, 6284.14, 6283.00, 6282.10, 6281.90, 6282.75, 6283.50, 6282.20, 6281.63]
EUSTX50_BAKED = _m1("EUSTX50", EUSTX50_OPEN_MS, o=6239.79, h=6284.14, low=6239.79, c=6281.63, v=10)

# XAU 20.09 22:01: m1 o=h=4380.77 l=4373.41 c=4374.2 v=388, перший тік 4375.62. min тіків (4373.95) ≠ l брокера —
# незапечений low мусить лишитись брокерським, а не взятись з тіків.
XAU_BIDS = [4375.62, 4376.80, 4377.15, 4374.90, 4373.95, 4374.20]
XAU_BAKED = _m1("XAU/USD", XAU_OPEN_MS, o=4380.77, h=4380.77, low=4373.41, c=4374.2, v=388)

# NAS100 20.09 22:00: m1 o=h=29677.82 l=29637.01 c=29640.31, перший тік 29644.01.
NAS_BIDS = [29644.01, 29650.46, 29641.59, 29640.31]
NAS_BAKED = _m1("NAS100", NAS_OPEN_MS, o=29677.82, h=29677.82, low=29637.01, c=29640.31, v=1482)


def _rebuild(bar, ticks, price_step=0.01):
    return so.rebuild_session_open_bar(bar, ticks, price_step)


def test_eustx50_baked_monday_open_and_low_come_from_the_ticks():
    rebuilt, reason = _rebuild(EUSTX50_BAKED, _spread(EUSTX50_OPEN_MS, EUSTX50_BIDS))
    assert reason == so.REASON_REBUILT
    assert (rebuilt.o, rebuilt.h, rebuilt.low, rebuilt.c, rebuilt.v) == (6281.63, 6284.14, 6281.63, 6281.63, 10)
    assert rebuilt.extensions == {"session_open_rebuilt": True, "open_before": 6239.79, "low_before": 6239.79}
    assert (rebuilt.open_time_ms, rebuilt.src, rebuilt.complete) == (EUSTX50_OPEN_MS, "history", True)
    assert EUSTX50_BAKED.o == 6239.79 and EUSTX50_BAKED.extensions == {}  # вхід не змінено


def test_xau_baked_high_is_rebuilt_and_the_real_low_stays_the_brokers():
    """Критерій не залежить від нашого close перед перервою (XAU/XAG 16.09: округлений 4381.00 при o 4380.77)."""
    rebuilt, reason = _rebuild(XAU_BAKED, _spread(XAU_OPEN_MS, XAU_BIDS))
    assert reason == so.REASON_REBUILT
    assert (rebuilt.o, rebuilt.h, rebuilt.low, rebuilt.c, rebuilt.v) == (4375.62, 4377.15, 4373.41, 4374.2, 388)
    assert rebuilt.extensions == {"session_open_rebuilt": True, "open_before": 4380.77, "high_before": 4380.77}


def test_nas100_baked_high_is_rebuilt_and_the_real_low_stays_the_brokers():
    rebuilt, reason = _rebuild(NAS_BAKED, _spread(NAS_OPEN_MS, NAS_BIDS))
    assert reason == so.REASON_REBUILT
    assert (rebuilt.o, rebuilt.h, rebuilt.low, rebuilt.c) == (29644.01, 29650.46, 29637.01, 29640.31)


def test_unbaked_extreme_stays_the_brokers_even_when_ticks_disagree():
    """max тіків (6283.90) ≠ high брокера (6284.14), а high не запечений → лишається брокерський."""
    bids = [6281.63, 6283.90, 6282.00, 6281.63]
    rebuilt, reason = _rebuild(EUSTX50_BAKED, _spread(EUSTX50_OPEN_MS, bids))
    assert reason == so.REASON_REBUILT
    assert (rebuilt.h, rebuilt.low) == (6284.14, 6281.63)
    assert "high_before" not in rebuilt.extensions


def test_baked_extreme_includes_the_close_when_close_is_outside_the_ticks():
    """Запечений high замінюється на max(тіки, c): c (4374.2) вищий за всі тіки → high = c."""
    bids = [4373.62, 4373.90, 4373.50]
    rebuilt, reason = _rebuild(XAU_BAKED, _spread(XAU_OPEN_MS, bids))
    assert reason == so.REASON_REBUILT
    assert (rebuilt.o, rebuilt.h, rebuilt.low, rebuilt.c) == (4373.62, 4374.2, 4373.41, 4374.2)


def test_first_tick_is_the_earliest_by_time_not_by_broker_order():
    """Брокер віддав тіки задом наперед; перший за ЧАСОМ 6281.63, останній 6283.10 — open береться за часом.
    Тік за мілісекунду до хвилини і рівно на close — чужі."""
    inside = [(EUSTX50_OPEN_MS, 6281.63), (EUSTX50_OPEN_MS + 30_000, 6284.14), (EUSTX50_OPEN_MS + 59_999, 6283.10)]
    alien = [(EUSTX50_OPEN_MS - 1, 6100.0), (EUSTX50_OPEN_MS + 60_000, 6400.0)]
    rebuilt, reason = _rebuild(EUSTX50_BAKED, list(reversed(inside)) + alien)
    assert reason == so.REASON_REBUILT
    assert (rebuilt.o, rebuilt.h, rebuilt.low) == (6281.63, 6284.14, 6281.63)


def test_ticks_disagreeing_with_the_bar_on_count_and_close_still_fix_the_open():
    """Замір T+8 с 21.09: тіків більше за v (XAU 1190 при v=818), close тіку ≠ close бару — гейтів на це немає."""
    noisy = XAU_BIDS[:-1] + [4376.00] + [4375.0] * 1000
    rebuilt, reason = _rebuild(XAU_BAKED, _spread(XAU_OPEN_MS, noisy))
    assert reason == so.REASON_REBUILT
    assert (rebuilt.o, rebuilt.c, rebuilt.v) == (4375.62, 4374.2, 388)


def test_real_open_inside_the_tick_range_is_left_as_the_broker_sent_it():
    """NAS100 замір 21.09: open m1 30299.59 ≠ перший тік t1 30299.71, але всередині діапазону [30290.71, 30302.71]."""
    bar = _m1("NAS100", NAS_OPEN_MS, o=30299.59, h=30302.71, low=30290.71, c=30295.00, v=1999)
    ticks = _spread(NAS_OPEN_MS, [30299.71, 30302.71, 30290.71, 30295.00])
    assert _rebuild(bar, ticks) == (None, so.REASON_OPEN_WITHIN_TICKS)
    assert so.REASON_OPEN_WITHIN_TICKS in so.BAR_CORRECT_AS_IS


@pytest.mark.parametrize("open_offset, expected", [
    (0.004, so.REASON_OPEN_WITHIN_TICKS),   # у межах ½ кроку (float-шум, округлення) — справжній
    (0.01, so.REASON_REBUILT),              # рівно крок поза тіками — запечений (допуск повним кроком тут би збрехав)
    (-0.01, so.REASON_REBUILT),             # те саме знизу
])
def test_open_tolerance_is_half_a_price_step(open_offset, expected):
    bids = [6281.63, 6282.00, 6281.80]
    edge = max(bids) if open_offset > 0 else min(bids)
    bar = _m1("EUSTX50", EUSTX50_OPEN_MS, o=round(edge + open_offset, 5), h=6282.50, low=6281.00, c=6281.80, v=3)
    assert _rebuild(bar, _spread(EUSTX50_OPEN_MS, bids))[1] == expected


def test_normalization_keeps_ohlc_consistent_when_the_first_tick_is_outside_the_kept_extreme():
    """Незапечений high брокера нижче за перший тік (t1 і m1 на T+8 с розходяться) → h піднімається до open."""
    bar = _m1("EUSTX50", EUSTX50_OPEN_MS, o=6239.79, h=6281.00, low=6239.79, c=6280.50, v=5)
    rebuilt, _reason = _rebuild(bar, [(EUSTX50_OPEN_MS + 1_000, 6281.63)])
    assert (rebuilt.o, rebuilt.h, rebuilt.low, rebuilt.c) == (6281.63, 6281.63, 6280.50, 6280.50)
    assert rebuilt.extensions["high_before"] == 6281.00


@pytest.mark.parametrize("ticks, price_step, expected", [
    ([], 0.01, so.REASON_NO_TICKS),
    ([(EUSTX50_OPEN_MS + 60_000, 6281.63)], 0.01, so.REASON_NO_TICKS),  # тік лише з наступної хвилини
    (_spread(EUSTX50_OPEN_MS, EUSTX50_BIDS), 0.0, so.REASON_PRICE_STEP_INVALID),
    (_spread(EUSTX50_OPEN_MS, EUSTX50_BIDS), float("nan"), so.REASON_PRICE_STEP_INVALID),
])
def test_unverifiable_open_is_returned_to_the_caller_as_a_reason(ticks, price_step, expected):
    assert _rebuild(EUSTX50_BAKED, ticks, price_step) == (None, expected)
    assert expected not in so.BAR_CORRECT_AS_IS


def test_provisional_marker_keeps_the_broker_values_and_existing_extensions():
    bar = dataclasses.replace(XAU_BAKED, extensions={"source_note": "x"})
    marked = so.mark_open_provisional(bar)
    assert (marked.o, marked.h, marked.low, marked.c, marked.v) == (bar.o, bar.h, bar.low, bar.c, bar.v)
    assert marked.extensions == {"source_note": "x", "open_provisional": True}
    assert bar.extensions == {"source_note": "x"}


def _weekday_break_calendar():
    from runtime.ingest.market_calendar import MarketCalendar

    return MarketCalendar(enabled=True, weekend_close_dow=4, weekend_close_hm="21:00", weekend_open_dow=6,
                          weekend_open_hm="22:00", daily_break_start_hm="21:00", daily_break_end_hm="22:00",
                          daily_break_enabled=True)


@pytest.mark.parametrize("open_iso, prev_iso, use_calendar, expected", [
    ("2026-09-21T06:01:00", "2026-09-18T19:59:00", False, True),   # EUSTX50: вихідні, перший бар 06:01
    ("2026-09-21T12:16:00", "2026-09-21T12:00:00", False, True),   # 16 хв тиші > 15 — перерва за даними
    ("2026-09-21T12:15:00", "2026-09-21T12:00:00", False, False),  # рівно 15 хв — ще ні («старший за»)
    ("2026-09-21T12:01:00", "2026-09-21T12:00:00", False, False),  # сусідня хвилина
    ("2026-09-21T22:00:00", "2026-09-21T21:59:00", True, True),    # календар: 22:00 торгова, 21:59 — перерва
    ("2026-09-21T22:01:00", "2026-09-21T22:00:00", True, False),   # друга хвилина сесії
    ("2026-09-21T22:00:00", None, False, False),                   # ні попереднього бару, ні календаря
])
def test_first_bar_after_break_by_data_gap_or_by_calendar(open_iso, prev_iso, use_calendar, expected):
    is_trading = _weekday_break_calendar().is_trading_minute if use_calendar else None
    prev_ms = _ms(prev_iso) if prev_iso else None
    assert so.is_first_bar_after_break(_ms(open_iso), prev_ms, 15 * 60_000, is_trading) is expected


def test_policy_comes_from_config_and_absent_section_means_disabled():
    cfg = {"m1_poller": {"session_open_rebuild": {"enabled": True, "gap_min": 15,
                                                  "price_step_by_symbol": {"XAU/USD": 0.01, "XAG/USD": 0.001}}}}
    policy = so.resolve_session_open_rebuild_policy(cfg)
    assert (policy.enabled, policy.gap_ms) == (True, 15 * 60_000)
    assert dict(policy.price_step_by_symbol) == {"XAU/USD": 0.01, "XAG/USD": 0.001}
    assert so.resolve_session_open_rebuild_policy({"m1_poller": {}}) == so.DISABLED_POLICY


@pytest.mark.parametrize("section", [
    {"enabled": True, "gap_min": 0, "price_step_by_symbol": {}},
    {"enabled": True, "gap_min": 15, "price_step_by_symbol": {"XAU/USD": 0}},
    {"enabled": True, "price_step_by_symbol": {}},
    {"enabled": "false", "gap_min": 15, "price_step_by_symbol": {}},  # bool("false") == True — не вгадувати
    {"enabled": 1, "gap_min": 15, "price_step_by_symbol": {}},
])
def test_broken_policy_config_is_refused_loudly(section):
    with pytest.raises((ValueError, KeyError)):
        so.resolve_session_open_rebuild_policy({"m1_poller": {"session_open_rebuild": section}})


# ---------------------------------------------------------------------------
# P4 — полер: перебудова ДО коміту і до правила M1→SSOT
# ---------------------------------------------------------------------------
POLICY = so.SessionOpenRebuildPolicy(enabled=True, gap_ms=15 * 60_000,
                                     price_step_by_symbol={"EUSTX50": 0.01, "XAU/USD": 0.01})
FRIDAY_LAST_OPEN_MS = _ms("2026-09-18T19:59:00")


class _RecordingUds:
    def __init__(self):
        self.committed = []

    def commit_final_bar(self, bar):
        self.committed.append(bar)
        return types.SimpleNamespace(ok=True, reason="ok")


class _DirectTicks:
    """Провайдер полера з t1: віддає тіки, None (таймаут/відмова) або кидає виняток."""

    def __init__(self, ticks=None, raises=None):
        self.ticks, self.raises, self.calls = ticks, raises, []

    def fetch_t1_bid_ticks(self, symbol, from_ms, to_ms):
        self.calls.append((symbol, from_ms, to_ms))
        if self.raises is not None:
            raise self.raises
        return self.ticks


def _poller(provider, policy=POLICY, watermark_ms=FRIDAY_LAST_OPEN_MS, calendar=None, symbol="EUSTX50"):
    poller_mod.set_flat_bar_max_volume(4)
    uds = _RecordingUds()
    poller = poller_mod.M1SymbolPoller(symbol=symbol, provider=provider, uds=uds, calendar=calendar,
                                       session_open_policy=policy)
    poller._watermark_ms = watermark_ms  # noqa: SLF001
    return poller, uds


def test_poller_commits_the_rebuilt_first_bar_after_the_weekend(caplog):
    provider = _DirectTicks(ticks=_spread(EUSTX50_OPEN_MS, EUSTX50_BIDS))
    poller, uds = _poller(provider)
    with caplog.at_level(logging.INFO):
        assert poller._ingest_bar(EUSTX50_BAKED) is True  # noqa: SLF001
    committed, = uds.committed
    assert (committed.o, committed.h, committed.low, committed.c, committed.v) == (6281.63, 6284.14, 6281.63, 6281.63, 10)
    assert committed.extensions == {"session_open_rebuilt": True, "open_before": 6239.79, "low_before": 6239.79}
    assert provider.calls == [("EUSTX50", EUSTX50_OPEN_MS, EUSTX50_OPEN_MS + 60_000)]
    assert "FXCM_SESSION_OPEN_REBUILT symbol=EUSTX50" in caplog.text


@pytest.mark.parametrize("provider, reason", [
    (_DirectTicks(ticks=None), "reason=t1_unavailable"),                       # проксі: таймаут/помилка sidecar
    (_DirectTicks(raises=ConnectionError("redis down")), "reason=t1_error: redis down"),
    (_DirectTicks(ticks=[]), "reason=no_ticks_in_minute"),                     # тікова історія ще порожня
    (object(), "reason=provider_without_t1"),
])
def test_first_bar_without_ticks_commits_the_broker_bar_loudly_as_provisional(provider, reason, caplog):
    poller, uds = _poller(provider)
    with caplog.at_level(logging.WARNING):
        assert poller._ingest_bar(EUSTX50_BAKED) is True  # noqa: SLF001
    committed, = uds.committed
    assert (committed.o, committed.low, committed.v) == (6239.79, 6239.79, 10)  # бар брокера без змін
    assert committed.extensions == {"open_provisional": True}
    assert "FXCM_SESSION_OPEN_BAKED symbol=EUSTX50" in caplog.text and reason in caplog.text


def test_real_first_bar_asks_for_ticks_once_and_is_committed_as_is(caplog):
    """Критерій — тіки хвилини, тож t1 запитується і для справжнього відкриття; open усередині → бар як є, без WARN."""
    provider = _DirectTicks(ticks=_spread(EUSTX50_OPEN_MS, EUSTX50_BIDS))
    real_open = dataclasses.replace(EUSTX50_BAKED, o=6281.63, low=6281.63)
    poller, uds = _poller(provider)
    with caplog.at_level(logging.INFO):
        poller._ingest_bar(real_open)  # noqa: SLF001
    assert uds.committed == [real_open] and len(provider.calls) == 1
    assert "FXCM_SESSION_OPEN_OK symbol=EUSTX50" in caplog.text and "reason=open_within_ticks" in caplog.text
    assert "FXCM_SESSION_OPEN_BAKED" not in caplog.text


def test_symbol_without_price_step_is_provisional_not_guessed(caplog):
    bar = dataclasses.replace(EUSTX50_BAKED, symbol="US30")
    provider = _DirectTicks(ticks=_spread(EUSTX50_OPEN_MS, EUSTX50_BIDS))
    poller, uds = _poller(provider, symbol="US30")
    with caplog.at_level(logging.WARNING):
        poller._ingest_bar(bar)  # noqa: SLF001
    assert uds.committed[0].extensions == {"open_provisional": True}
    assert provider.calls == [] and "reason=price_step_missing" in caplog.text


def test_mid_session_bar_never_asks_for_ticks():
    """Звичайний цикл не гальмує: сусідня хвилина в торговий час — без t1 і без маркерів."""
    provider = _DirectTicks(ticks=[])
    noon = _ms("2026-09-21T12:01:00")
    poller, uds = _poller(provider, watermark_ms=noon - 60_000, calendar=_weekday_break_calendar())
    regular = _m1("EUSTX50", noon, 6281.0, 6282.0, 6280.0, 6281.5, 50)
    assert poller._ingest_bar(regular) is True  # noqa: SLF001
    assert provider.calls == [] and uds.committed == [regular]


def test_pause_bars_never_ask_for_ticks_and_never_warn(caplog):
    """Рев'ю D-02: пачка барів паузи (пласкі 21:00–21:04 у денній перерві, суботній шум, неплаский бар у перерві) —
    правило M1→SSOT їх відкидає/позначає, watermark не рухається. 0 запитів t1, 0 WARN BAKED, і так щоциклу."""
    friday = _ms("2026-09-18T20:59:00")
    pause = [_m1("EUSTX50", _ms("2026-09-21T21:0%d:00" % i), 6281.0, 6281.0, 6281.0, 6281.0, 1) for i in range(5)]
    pause.append(_m1("EUSTX50", _ms("2026-09-19T12:00:00"), 6281.0, 6281.0, 6281.0, 6281.0, 2))    # субота
    pause.append(_m1("EUSTX50", _ms("2026-09-21T21:30:00"), 6281.0, 6283.0, 6280.0, 6282.0, 40))   # неплаский у перерві
    provider = _DirectTicks(ticks=_spread(EUSTX50_OPEN_MS, EUSTX50_BIDS))
    poller, _uds = _poller(provider, watermark_ms=friday, calendar=_weekday_break_calendar())
    with caplog.at_level(logging.WARNING):
        for _cycle in range(3):
            for bar in pause:
                poller._ingest_bar(bar)  # noqa: SLF001
    assert provider.calls == []
    assert "FXCM_SESSION_OPEN_BAKED" not in caplog.text


def test_first_trading_bar_after_the_pause_asks_for_ticks_exactly_once():
    """Контроль D-02: після бару паузи перший торговий бар 22:00 іде в перебудову — один запит t1."""
    reopen = _ms("2026-09-21T22:00:00")
    provider = _DirectTicks(ticks=[(reopen + 5_000, 6281.63), (reopen + 30_000, 6284.14)])
    poller, uds = _poller(provider, watermark_ms=_ms("2026-09-21T20:59:00"), calendar=_weekday_break_calendar())
    poller._ingest_bar(_m1("EUSTX50", _ms("2026-09-21T21:00:00"), 6239.79, 6239.79, 6239.79, 6239.79, 1))  # noqa: SLF001
    poller._ingest_bar(_m1("EUSTX50", reopen, 6239.79, 6284.14, 6239.79, 6284.14, 2))  # noqa: SLF001
    assert provider.calls == [("EUSTX50", reopen, reopen + 60_000)]
    assert uds.committed[-1].o == 6281.63


def test_flat_reopen_placeholder_is_not_mixed_with_ticks():
    """Пласка заглушка 22:00 (O=H=L=C = ціна до перерви, v=3) при наявних тіках не перебудовується: перебудова дала б
    змішаний бар (справжній open + застарілий close). t1 не запитується. Далі її бачить правило M1→SSOT, яке йде ПІСЛЯ
    перебудови (ADR-0099 §3.1, §4): плаский бар у хвилині перевідкриття — `reopen_flat_dropped`, у SSOT не йде."""
    reopen = _ms("2026-09-21T22:00:00")
    provider = _DirectTicks(ticks=[(reopen + 5_000, 6281.63), (reopen + 30_000, 6284.14)])
    poller, uds = _poller(provider, watermark_ms=_ms("2026-09-21T20:59:00"), calendar=_weekday_break_calendar())
    placeholder = _m1("EUSTX50", reopen, 6239.79, 6239.79, 6239.79, 6239.79, 3)
    assert poller._ingest_bar(placeholder) is False  # noqa: SLF001
    assert provider.calls == []
    assert uds.committed == []


def test_bar_not_newer_than_watermark_never_asks_for_ticks():
    """Calendar-відкриття, яке вже закомічено (повтор від брокера): UDS його відкине, t1 — зайвий запит."""
    reopen = _ms("2026-09-21T22:00:00")
    provider = _DirectTicks(ticks=[])
    poller, _uds = _poller(provider, watermark_ms=reopen, calendar=_weekday_break_calendar())
    poller._ingest_bar(_m1("EUSTX50", reopen, 6281.0, 6282.0, 6280.0, 6281.5, 50))  # noqa: SLF001
    assert provider.calls == []


def test_disabled_policy_keeps_the_old_behaviour_exactly():
    provider = _DirectTicks(ticks=_spread(EUSTX50_OPEN_MS, EUSTX50_BIDS))
    poller, uds = _poller(provider, policy=so.DISABLED_POLICY)
    poller._ingest_bar(EUSTX50_BAKED)  # noqa: SLF001
    assert provider.calls == [] and uds.committed == [EUSTX50_BAKED]


def test_ssot_rule_sees_the_rebuilt_bar_not_the_baked_one():
    """Бар брокера не пласкій (запечений open/low), а справжня хвилина — два тіки за однією ціною: правило M1→SSOT
    має бачити саме перебудований бар і позначити його trading_flat."""
    bar = _m1("EUSTX50", EUSTX50_OPEN_MS, o=6239.79, h=6250.0, low=6239.79, c=6250.0, v=2)
    ticks = [(EUSTX50_OPEN_MS + 5_000, 6250.0), (EUSTX50_OPEN_MS + 40_000, 6250.0)]
    poller, uds = _poller(_DirectTicks(ticks=ticks))
    assert poller._ingest_bar(bar) is True  # noqa: SLF001
    committed, = uds.committed
    assert (committed.o, committed.h, committed.low, committed.c) == (6250.0, 6250.0, 6250.0, 6250.0)
    assert committed.extensions == {"session_open_rebuilt": True, "open_before": 6239.79, "low_before": 6239.79,
                                    "trading_flat": True}


def test_end_to_end_poller_through_proxy_and_real_sidecar_handler():
    """Повний конвеєр одного процесу з іншим: полер → BrokerRedisProxy → Redis → broker_sidecar._handle_command
    → провайдер t1 → реплай → перебудова → коміт."""
    sidecar_provider = _TickProvider(ticks=_spread(EUSTX50_OPEN_MS, EUSTX50_BIDS))
    proxy = BrokerRedisProxy(_LoopbackRedis(sidecar_provider), NS)
    poller, uds = _poller(proxy)
    assert poller._ingest_bar(EUSTX50_BAKED) is True  # noqa: SLF001
    assert (uds.committed[0].o, uds.committed[0].low) == (6281.63, 6281.63)
    assert sidecar_provider.calls == [("EUSTX50", EUSTX50_OPEN_MS, EUSTX50_OPEN_MS + 60_000)]


def test_writer_policy_loader_is_loud_about_broken_config_and_missing_steps(caplog):
    broken = {"m1_poller": {"session_open_rebuild": {"enabled": True, "gap_min": 0, "price_step_by_symbol": {}}}}
    with caplog.at_level(logging.WARNING):
        assert poller_mod.load_session_open_policy(broken, ["XAU/USD"]) == so.DISABLED_POLICY
        ok = {"m1_poller": {"session_open_rebuild": {"enabled": True, "gap_min": 15,
                                                     "price_step_by_symbol": {"XAU/USD": 0.01}}}}
        assert poller_mod.load_session_open_policy(ok, ["XAU/USD", "US30"]).enabled is True
    assert "M1_SESSION_OPEN_REBUILD_CONFIG_INVALID" in caplog.text
    assert "M1_SESSION_OPEN_REBUILD_NO_PRICE_STEP symbols=['US30']" in caplog.text


def test_string_false_in_config_disables_rebuild_loudly_instead_of_enabling_it(caplog):
    """Рев'ю D-06: "enabled": "false" (рядок) раніше вмикав перебудову через bool(); тепер — ERROR і вимкнено."""
    cfg = {"m1_poller": {"session_open_rebuild": {"enabled": "false", "gap_min": 15,
                                                  "price_step_by_symbol": {"XAU/USD": 0.01}}}}
    with caplog.at_level(logging.ERROR):
        assert poller_mod.load_session_open_policy(cfg, ["XAU/USD"]).enabled is False
    assert "M1_SESSION_OPEN_REBUILD_CONFIG_INVALID" in caplog.text and "'false'" in caplog.text


# ---------------------------------------------------------------------------
# P5 — SSOT конфіг: перебудова вимкнена (ADR-0100), але rollback лишається готовим
# ---------------------------------------------------------------------------


def test_repo_config_keeps_rebuild_disabled_because_previous_close_is_the_tv_bar():
    """ADR-0100: open першої M1 після перерви = close перед перервою — це і є бар TV FX:, перебудова робила дірку.
    Гейт проти тихого повернення: якщо хтось увімкне перебудову, це має бути рішенням з ADR, а не непоміченим
    дифом конфігу."""
    policy = so.resolve_session_open_rebuild_policy(_repo_config())
    assert policy.enabled is False


def test_repo_config_keeps_a_price_step_for_every_active_fxcm_symbol_so_rollback_is_not_a_guess():
    """Секція = задокументований rollback (ADR-0096 §6 E). Новий символ без кроку ціни зламав би перебудову вже
    після увімкнення (WARN + provisional) — тут це ловиться до деплою, поки rollback ще на папері."""
    cfg = _repo_config()
    from runtime.ingest.tick_common import symbols_from_cfg

    policy = so.resolve_session_open_rebuild_policy(cfg)
    binance = cfg.get("binance") or {}
    binance_symbols = set(binance.get("symbols", [])) if binance.get("enabled") else set()
    fxcm_symbols = [sym for sym in symbols_from_cfg(cfg) if sym not in binance_symbols]
    assert policy.gap_ms == 15 * 60_000
    assert fxcm_symbols and [sym for sym in fxcm_symbols if sym not in policy.price_step_by_symbol] == []


def _repo_config():
    import pathlib

    from core.config_loader import load_system_config

    return load_system_config(str(pathlib.Path(__file__).resolve().parents[1] / "config.json"))
