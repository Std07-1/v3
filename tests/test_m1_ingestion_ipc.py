from __future__ import annotations

import json

from runtime.ingest.m1_ingestion_worker import BrokerRedisProxy
from runtime.ingest.polling import m1_poller as m1_poller_module


class _FakeRedis:
    def __init__(self, response_factory=None):
        self._queues = {}
        self._response_factory = response_factory
        self.last_cmd = None
        self.blpop_keys = []
        self.deleted_keys = []

    def rpush(self, key, value):
        self._queues.setdefault(key, []).append(value)
        try:
            payload = json.loads(value)
        except Exception:
            payload = None
        if isinstance(payload, dict) and payload.get("cmd") == "fetch_m1":
            self.last_cmd = payload
            if self._response_factory is not None:
                reply_key, reply_payload = self._response_factory(payload)
                self._queues.setdefault(reply_key, []).append(reply_payload)
        return len(self._queues[key])

    def blpop(self, key, timeout=None):
        _ = timeout
        self.blpop_keys.append(key)
        queue = self._queues.get(key, [])
        if not queue:
            return None
        return key, queue.pop(0)

    def delete(self, key):
        self.deleted_keys.append(key)
        self._queues.pop(key, None)

    def llen(self, key):
        return len(self._queues.get(key, []))

    def expire(self, key, ttl_s):
        _ = (key, ttl_s)

    def ltrim(self, key, start, end):
        _ = (key, start, end)


class _EmptyProvider:
    def fetch_last_n_m1(self, symbol, n, date_to_utc=None):
        _ = (symbol, n, date_to_utc)
        return []

    def consume_last_error(self):
        return None


def _bar_dict(symbol: str) -> dict:
    return {
        "symbol": symbol,
        "tf_s": 60,
        "open_time_ms": 1710000000000,
        "close_time_ms": 1710000060000,
        "o": 1.0,
        "h": 2.0,
        "low": 0.5,
        "c": 1.5,
        "v": 10.0,
        "complete": True,
        "src": "history",
        "extensions": {},
    }


def test_broker_proxy_uses_per_request_reply_queue():
    def _response_factory(cmd):
        payload = json.dumps(
            {
                "v": 1,
                "req_id": cmd["req_id"],
                "symbol": cmd["symbol"],
                "bars": [_bar_dict(cmd["symbol"])],
                "error": None,
            }
        )
        return cmd["reply_to"], payload

    fake_redis = _FakeRedis(response_factory=_response_factory)
    proxy = BrokerRedisProxy(fake_redis, "v3_local")

    bars = proxy.fetch_last_n_m1("XAU/USD", n=2)

    assert len(bars) == 1
    assert fake_redis.last_cmd is not None
    assert fake_redis.blpop_keys == [fake_redis.last_cmd["reply_to"]]
    assert fake_redis.last_cmd["req_id"] in fake_redis.last_cmd["reply_to"]
    assert fake_redis.last_cmd["reply_to"] in fake_redis.deleted_keys


def test_broker_proxy_rejects_mismatched_symbol_response():
    def _response_factory(cmd):
        payload = json.dumps(
            {
                "v": 1,
                "req_id": cmd["req_id"],
                "symbol": "NAS100",
                "bars": [_bar_dict("NAS100")],
                "error": None,
            }
        )
        return cmd["reply_to"], payload

    fake_redis = _FakeRedis(response_factory=_response_factory)
    proxy = BrokerRedisProxy(fake_redis, "v3_local")

    bars = proxy.fetch_last_n_m1("XAU/USD", n=2)

    assert bars == []


def test_poll_once_empty_fetch_still_runs_recovery_and_stale(monkeypatch):
    poller = m1_poller_module.M1SymbolPoller(
        symbol="XAU/USD",
        provider=_EmptyProvider(),
        uds=object(),
        calendar=None,
    )
    poller._watermark_ms = 1710000000000  # noqa: SLF001

    calls = []
    monkeypatch.setattr(m1_poller_module, "_utc_now_ms", lambda: 1710000300000)
    monkeypatch.setattr(
        m1_poller_module,
        "_expected_closed_m1_calendar",
        lambda calendar, now_ms: 1710000240000,
    )
    monkeypatch.setattr(poller, "_live_recover_check", lambda: calls.append("recover"))
    monkeypatch.setattr(
        poller,
        "_stale_check",
        lambda now_ms: calls.append(("stale", now_ms)),
    )

    poller.poll_once()

    assert calls == ["recover", ("stale", 1710000300000)]


# ---------------------------------------------------------------------------
# ADR-0054 §3.6 п.1/2/4 — вік команди, LLEN-гейт, flush черги при connect
# ---------------------------------------------------------------------------
from runtime.ingest import broker_sidecar


class _RecordingRedis(_FakeRedis):
    """FakeRedis, який не авто-відповідає — для перевірки самої черги."""


def test_broker_proxy_команда_несе_ts_ms():
    fake = _FakeRedis()
    proxy = BrokerRedisProxy(fake, "ns")
    proxy.fetch_last_n_m1("XAU/USD", 5)
    assert fake.last_cmd is not None
    assert isinstance(fake.last_cmd.get("ts_ms"), int)
    assert fake.last_cmd["ts_ms"] > 1_700_000_000_000


def test_broker_proxy_llen_гейт_не_докидає_при_заторі():
    fake = _FakeRedis()
    cmd_key = "ns:broker:m1:cmd"
    for _ in range(10):  # = _CMD_QUEUE_CONGESTED_LEN
        fake._queues.setdefault(cmd_key, []).append("{}")
    proxy = BrokerRedisProxy(fake, "ns")
    bars = proxy.fetch_last_n_m1("XAU/USD", 5)
    assert bars == []
    assert fake.last_cmd is None  # команду НЕ додано
    assert fake.llen(cmd_key) == 10


def test_sidecar_дропає_протухлу_команду_без_реплаю():
    fake = _FakeRedis()
    stale = json.dumps({
        "v": 1, "cmd": "fetch_m1", "req_id": "r1", "reply_to": "ns:broker:m1:bars:r1",
        "symbol": "XAU/USD", "n_bars": 5, "ts_ms": 1_700_000_000_000,  # давнє минуле
    })
    needs_reconnect = broker_sidecar._handle_command(_EmptyProvider(), stale, fake, "ns:broker:m1:bars")
    assert needs_reconnect is False
    assert fake._queues.get("ns:broker:m1:bars:r1") is None  # реплаю немає


def test_sidecar_свіжа_команда_без_ts_ms_обробляється_як_раніше():
    import time as _time
    fake = _FakeRedis()
    fresh = json.dumps({
        "v": 1, "cmd": "fetch_m1", "req_id": "r2", "reply_to": "ns:broker:m1:bars:r2",
        "symbol": "XAU/USD", "n_bars": 5,
    })
    broker_sidecar._handle_command(_EmptyProvider(), fresh, fake, "ns:broker:m1:bars")
    replies = fake._queues.get("ns:broker:m1:bars:r2")
    assert replies and json.loads(replies[0])["req_id"] == "r2"

    fresh_ts = json.dumps({
        "v": 1, "cmd": "fetch_m1", "req_id": "r3", "reply_to": "ns:broker:m1:bars:r3",
        "symbol": "XAU/USD", "n_bars": 5, "ts_ms": int(_time.time() * 1000),
    })
    broker_sidecar._handle_command(_EmptyProvider(), fresh_ts, fake, "ns:broker:m1:bars")
    assert fake._queues.get("ns:broker:m1:bars:r3")


def test_sidecar_flush_чистить_чергу_при_connect():
    fake = _FakeRedis()
    cmd_key = "ns:broker:m1:cmd"
    for _ in range(3):
        fake._queues.setdefault(cmd_key, []).append("{}")
    broker_sidecar._flush_cmd_queue(fake, cmd_key)
    assert cmd_key in fake.deleted_keys
    assert fake.llen(cmd_key) == 0
    # порожня черга — delete не викликається вдруге
    fake.deleted_keys.clear()
    broker_sidecar._flush_cmd_queue(fake, cmd_key)
    assert fake.deleted_keys == []
