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


class _EmptyProvider:
    def fetch_last_n_m1(self, symbol, n, date_to_utc=None):
        _ = (symbol, n, date_to_utc)
        return []


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
