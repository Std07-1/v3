"""ADR-0114 S3: двигун сповіщень (runtime/alerts/engine.py) і джерела цін (runtime/alerts/feeds.py).

Без Redis і диска платформи: стан — у tmp_path, тіки — фейковий PubSub, свічки — фейковий читач.
Run: python -m pytest tests/test_alerts_engine.py -q
"""

from __future__ import annotations

import asyncio
import json
import logging
from types import SimpleNamespace

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from core.alerts.types import AlertSide, AlertState, CrossDirection, FireVia, PriceAlert
from runtime.alerts.engine import DEGRADED_STATE_WRITE, M1_MS, AlertEngine, FinalBar, final_bars_from_lwc
from runtime.alerts.feeds import (
    DEGRADED_M1_PREFIX,
    DEGRADED_TICKS,
    M1ReadError,
    parse_tick,
    poll_m1_once,
    run_tick_feed,
    uds_final_m1_reader,
)
from runtime.alerts.store import STATE_FILE, AlertStore

LEVEL = 4380.0
T0 = 1_792_000_020_000  # 20 с усередині хвилини
MINUTE0 = T0 // M1_MS * M1_MS
GAP_MS = 15 * 60_000
SYMBOLS = frozenset({"XAU/USD", "NAS100"})


def _alert(alert_id: str = "a1", symbol: str = "XAU/USD", **kw) -> PriceAlert:
    fields = dict(
        alert_id=alert_id,
        drawing_id=f"d-{alert_id}",
        subscriber_id="owner",
        symbol=symbol,
        level=LEVEL,
        tf_label="M15",
        active_from_ms=T0,
        created_at_ms=T0,
        side=AlertSide.BELOW,
    )
    fields.update(kw)
    return PriceAlert(**fields)


@pytest.fixture
def engine(tmp_path):
    store = AlertStore(tmp_path / STATE_FILE)
    store.put(_alert("a1"))
    store.put(_alert("n1", symbol="NAS100", level=20000.0))
    return AlertEngine(store, gap_after_ms=GAP_MS, clock_ms=lambda: T0 + 99)


def _on_disk(engine: AlertEngine) -> AlertStore:
    return AlertStore.load(engine.store.path, now_ms=T0)


def _count_commits(engine: AlertEngine, monkeypatch) -> list:
    calls: list = []
    real = engine.store.commit
    monkeypatch.setattr(engine.store, "commit", lambda: (calls.append(1), real())[1])
    return calls


# ── тіки ─────────────────────────────────────────────────────────────────


def test_tick_crossing_fires_enqueues_and_commits_state_with_outbox(engine):
    fired = engine.apply_tick("XAU/USD", LEVEL + 0.2, T0 + 1_000)
    assert [a.alert_id for a in fired] == ["a1"]
    disk = _on_disk(engine)
    assert disk.alerts["a1"].state is AlertState.FIRED
    assert [e.alert.fired.price for e in disk.outbox.values()] == [LEVEL + 0.2]
    assert disk.alerts["n1"].state is AlertState.ARMED  # інший символ не чіпали


def test_tick_without_change_does_not_write(engine, monkeypatch):
    calls = _count_commits(engine, monkeypatch)
    assert engine.apply_tick("XAU/USD", LEVEL - 5, T0 + 1_000) == []
    assert calls == [] and not engine.store.path.exists()


def test_tick_resolving_undetermined_side_is_persisted(engine):
    engine.store.put(_alert("a1", side=None))
    engine.apply_tick("XAU/USD", LEVEL - 1, T0 + 1_000)
    assert _on_disk(engine).alerts["a1"].side is AlertSide.BELOW


def test_tick_after_market_pause_reports_gap(engine):
    engine.apply_tick("XAU/USD", LEVEL - 1, T0)  # перший тік — без гепу, нижче рівня
    fired = engine.apply_tick("XAU/USD", LEVEL + 3, T0 + GAP_MS)
    assert fired[0].fired.after_gap is True


# ── свічки M1 ────────────────────────────────────────────────────────────


def test_m1_bars_fire_on_earliest_touch_and_move_checkpoint_in_one_write(engine, monkeypatch):
    calls = _count_commits(engine, monkeypatch)
    bars = [
        FinalBar(MINUTE0 + M1_MS, LEVEL - 5, LEVEL - 1),
        FinalBar(MINUTE0 + 2 * M1_MS, LEVEL - 2, LEVEL + 1),
        FinalBar(MINUTE0 + 3 * M1_MS, LEVEL, LEVEL + 4),
    ]
    fired = engine.apply_m1_bars("XAU/USD", bars, safe_until_ms=MINUTE0 + 3 * M1_MS)
    assert fired[0].fired.at_ms == MINUTE0 + 2 * M1_MS and fired[0].fired.via is FireVia.M1
    assert len(calls) == 1
    assert _on_disk(engine).checked_until_ms == {"XAU/USD": MINUTE0 + 3 * M1_MS}


def test_m1_bars_without_change_and_same_checkpoint_do_not_write(engine, monkeypatch):
    engine.apply_m1_bars("XAU/USD", [], safe_until_ms=MINUTE0)
    calls = _count_commits(engine, monkeypatch)
    engine.apply_m1_bars("XAU/USD", [FinalBar(MINUTE0 + M1_MS, LEVEL - 9, LEVEL - 8)], safe_until_ms=MINUTE0)
    assert calls == []


def test_m1_after_ms_starts_at_activation_minute_and_follows_checkpoint(engine):
    assert engine.m1_after_ms("XAU/USD") == MINUTE0 - 1  # (after, to]: хвилина активації входить
    engine.store.mark_checked("XAU/USD", MINUTE0 + 5 * M1_MS)
    assert engine.m1_after_ms("XAU/USD") == MINUTE0 + 5 * M1_MS
    engine.store.put(_alert("a1", active_from_ms=MINUTE0 + 9 * M1_MS + 1))  # перевзведено пізніше за позначку
    assert engine.m1_after_ms("XAU/USD") == MINUTE0 + 9 * M1_MS - 1
    assert engine.m1_after_ms("GER30") is None


def test_final_bars_from_lwc_keeps_only_final_numeric_rows_sorted():
    rows = [
        {"open_time_ms": 3, "low": 1.0, "high": 2.0, "complete": True},
        {"open_time_ms": 1, "low": 1, "high": 2, "complete": True},
        {"open_time_ms": 2, "low": 1.0, "high": 2.0, "complete": False},
        {"open_time_ms": 4, "low": True, "high": 2.0, "complete": True},
        {"open_time_ms": 5, "high": 2.0, "complete": True},
    ]
    bars, rejected = final_bars_from_lwc(rows)
    assert [b.open_ms for b in bars] == [1, 3] and rejected == 3


# ── збій запису ──────────────────────────────────────────────────────────


def test_failed_write_is_loud_keeps_outbox_in_memory_and_retry_recovers(engine, monkeypatch, caplog):
    real_commit = engine.store.commit

    def broken() -> None:
        raise OSError("диск повний")

    monkeypatch.setattr(engine.store, "commit", broken)
    with caplog.at_level(logging.ERROR, logger="runtime.alerts.engine"):
        engine.apply_tick("XAU/USD", LEVEL + 1, T0 + 1_000)
        engine.apply_tick("NAS100", 20001.0, T0 + 1_000)  # другий збій — без нового ERROR
    assert DEGRADED_STATE_WRITE in engine.degraded and len(engine.store.outbox) == 2
    assert [r.message for r in caplog.records].count(caplog.records[0].message) == 1

    monkeypatch.setattr(engine.store, "commit", real_commit)
    engine.retry_unsaved()
    assert DEGRADED_STATE_WRITE not in engine.degraded and len(_on_disk(engine).outbox) == 2


# ── тіки з PubSub ────────────────────────────────────────────────────────


def _tick_message(symbol: str, mid, ts=T0 + 1_000) -> dict:
    return {"type": "message", "data": json.dumps({"symbol": symbol, "mid": mid, "tick_ts_ms": ts}).encode()}


@pytest.mark.parametrize(
    "data, reason",
    [
        (b"not json", "не JSON"),
        (b"[1]", "не об'єкт"),
        (json.dumps({"symbol": ["XAU/USD"], "mid": 1.0, "tick_ts_ms": 1}).encode(), "symbol"),
        (json.dumps({"symbol": "XAU/USD", "mid": "1.0", "tick_ts_ms": 1}).encode(), "mid"),
        (json.dumps({"symbol": "XAU/USD", "mid": 0, "tick_ts_ms": 1}).encode(), "mid"),
        (json.dumps({"symbol": "XAU/USD", "mid": 1.0, "tick_ts_ms": True}).encode(), "tick_ts_ms"),
        (None, "не рядок"),
    ],
)
def test_parse_tick_malformed_raises_with_reason(data, reason):
    with pytest.raises(ValueError, match=reason):
        parse_tick(data, SYMBOLS)


def test_parse_tick_foreign_symbol_is_filtered_not_error():
    assert parse_tick(json.dumps({"symbol": "GER30", "mid": 1.0, "tick_ts_ms": 1}), SYMBOLS) is None


def test_parse_tick_reads_chart_price_from_mid():
    assert parse_tick(_tick_message("XAU/USD", 4380.5)["data"], SYMBOLS) == ("XAU/USD", 4380.5, T0 + 1_000)


class _FakePubSub:
    def __init__(self, messages, *, then_block: bool = False) -> None:
        self._messages, self._then_block, self.closed = messages, then_block, False

    async def listen(self):
        for message in self._messages:
            yield message
        if self._then_block:
            await asyncio.Event().wait()

    async def aclose(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_tick_feed_applies_ticks_flags_drop_and_recovers_on_reconnect(engine):
    first = _FakePubSub(
        [
            {"type": "subscribe", "data": 1},
            {"type": "message", "data": b"bad"},
            _tick_message("GER30", 25000.0),  # чужий символ — пропуск, не збій потоку
            _tick_message("XAU/USD", LEVEL),
        ]
    )
    healthy = _FakePubSub([], then_block=True)
    opened: list = []

    async def open_pubsub():
        opened.append(1)
        if len(opened) == 1:
            return first
        if len(opened) == 2:
            raise RedisConnectionError("redis недоступний")
        return healthy

    flags_seen: list = []
    task = asyncio.create_task(run_tick_feed(open_pubsub, engine, SYMBOLS, reconnect_s=0))
    for _ in range(200):
        flags_seen.append(DEGRADED_TICKS in engine.degraded)
        if len(opened) >= 3:
            break
        await asyncio.sleep(0)
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert engine.store.alerts["a1"].fired.direction is CrossDirection.UP
    assert first.closed and True in flags_seen and DEGRADED_TICKS not in engine.degraded


# ── опитування фінальних M1 ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_poll_m1_reads_contiguous_chunks_and_fires_from_disk_bars(engine):
    calls: list = []
    touch_ms = MINUTE0 + 7 * M1_MS

    def read(symbol, after_ms, to_ms, limit):
        calls.append((symbol, after_ms, to_ms, limit))
        if symbol == "XAU/USD" and after_ms < touch_ms <= to_ms:
            return [{"open_time_ms": touch_ms, "low": LEVEL - 1, "high": LEVEL + 1, "complete": True}]
        return []

    now = MINUTE0 + 10 * M1_MS + 30_000
    await poll_m1_once(read, engine, ["XAU/USD"], final_lag_ms=2 * M1_MS, chunk_bars=4, clock_ms=lambda: now)
    after = MINUTE0 - 1
    assert calls == [
        ("XAU/USD", after, after + 4 * M1_MS, 4),
        ("XAU/USD", after + 4 * M1_MS, after + 8 * M1_MS, 4),
        ("XAU/USD", after + 8 * M1_MS, now, 4),
    ]
    assert engine.store.alerts["a1"].fired.at_ms == touch_ms
    assert engine.store.checked_until_ms["XAU/USD"] == now - 2 * M1_MS


@pytest.mark.asyncio
async def test_poll_m1_failure_flags_only_that_symbol(engine):
    def read(symbol, after_ms, to_ms, limit):
        if symbol == "XAU/USD":
            raise M1ReadError("disk_disabled_range_query")
        return []

    now = MINUTE0 + 3 * M1_MS
    await poll_m1_once(read, engine, ["XAU/USD", "NAS100"], final_lag_ms=M1_MS, chunk_bars=60, clock_ms=lambda: now)
    assert set(engine.degraded) == {DEGRADED_M1_PREFIX + "XAU/USD"}
    assert engine.store.checked_until_ms["NAS100"] == now - M1_MS


def test_uds_reader_requests_disk_range_and_raises_on_degraded_window():
    seen: list = []

    def read_window(spec, policy):
        seen.append((spec, policy))
        source = "degraded" if spec.symbol == "BAD" else "disk_range"
        return SimpleNamespace(meta={"source": source}, warnings=["disk_disabled_range_query"], bars_lwc=[{"x": 1}])

    read = uds_final_m1_reader(SimpleNamespace(read_window=read_window))
    assert read("XAU/USD", 10, 20, 5) == [{"x": 1}]
    spec, policy = seen[0]
    requested = (spec.tf_s, spec.since_open_ms, spec.to_open_ms, spec.limit, policy.disk_policy)
    assert requested == (60, 10, 20, 5, "explicit")
    with pytest.raises(M1ReadError, match="BAD"):
        read("BAD", 10, 20, 5)
