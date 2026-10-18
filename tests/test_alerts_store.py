"""ADR-0114 S2: стан сповіщень на диску — codec (core/alerts) і AlertStore (runtime/alerts).

Run: python -m pytest tests/test_alerts_store.py -q
"""

from __future__ import annotations

import json
import os

import pytest

from core.alerts.codec import alert_from_dict, alert_to_dict
from core.alerts.crossing import on_tick
from core.alerts.types import AlertSide, PriceAlert
from runtime.alerts import store as store_mod
from runtime.alerts.store import STATE_FILE, AlertStore, StoreSchemaError

T0 = 1_792_000_000_000


def _alert(alert_id: str = "a1", **kw) -> PriceAlert:
    fields = dict(
        alert_id=alert_id,
        drawing_id=f"d-{alert_id}",
        subscriber_id="owner",
        symbol="XAU/USD",
        level=4380.0,
        tf_label="M15",
        active_from_ms=T0,
        created_at_ms=T0,
        side=AlertSide.BELOW,
    )
    fields.update(kw)
    return PriceAlert(**fields)


def _fired(alert_id: str = "a1") -> PriceAlert:
    return on_tick(_alert(alert_id), 4380.5, T0 + 1_000, prev_tick_ts_ms=T0, gap_after_ms=900_000)


@pytest.fixture
def path(tmp_path):
    return tmp_path / STATE_FILE


# ── codec ────────────────────────────────────────────────────────────────


def test_codec_round_trip_armed_and_fired_alert_through_json():
    for alert in (_alert(), _alert(side=None), _fired()):
        assert alert_from_dict(json.loads(json.dumps(alert_to_dict(alert)))) == alert


@pytest.mark.parametrize(
    "mutate, field",
    [
        (lambda d: d.update(extra=1), "зайві"),
        (lambda d: d.pop("level"), "бракує"),
        (lambda d: d.update(state="sleeping"), "state"),
        (lambda d: d.update(level="4380"), "level"),
        (lambda d: d["fired"].update(after_gap=0), "after_gap"),
        (lambda d: d["fired"].update(via="sms"), "fired.via"),
    ],
)
def test_codec_rejects_unknown_missing_or_invalid_fields_loudly(mutate, field):
    raw = alert_to_dict(_fired())
    mutate(raw)
    with pytest.raises(ValueError, match=field):
        alert_from_dict(raw)


# ── AlertStore: збереження ───────────────────────────────────────────────


def test_missing_state_file_loads_empty_without_problems(path):
    store = AlertStore.load(path, now_ms=T0)
    assert (store.alerts, store.outbox, store.checked_until_ms, store.problems) == ({}, {}, {}, [])


def test_commit_then_load_restores_alerts_outbox_and_checkpoints(path):
    store = AlertStore(path)
    store.put(_alert("a1"))
    fired = _fired("a2")
    store.put(fired)
    store.enqueue(fired, now_ms=T0 + 2_000)
    store.mark_checked("XAU/USD", T0 + 60_000)
    store.commit()

    loaded = AlertStore.load(path, now_ms=T0)
    assert loaded.alerts == store.alerts and loaded.outbox == store.outbox
    assert loaded.checked_until_ms == {"XAU/USD": T0 + 60_000} and loaded.problems == []
    assert [p.name for p in path.parent.iterdir()] == [STATE_FILE]  # tmp не лишився


def test_outbox_keeps_fired_snapshot_after_line_removed(path):
    store = AlertStore(path)
    fired = _fired()
    store.put(fired)
    store.enqueue(fired, now_ms=T0)
    store.remove(fired.alert_id)  # трейдер видалив лінію після спрацювання
    store.commit()
    assert list(AlertStore.load(path, now_ms=T0).outbox.values())[0].alert == fired


def test_enqueue_unfired_rejected_and_repeat_enqueue_keeps_first_entry(path):
    store = AlertStore(path)
    with pytest.raises(ValueError, match="не спрацювало"):
        store.enqueue(_alert(), now_ms=T0)
    fired = _fired()
    store.enqueue(fired, now_ms=T0)
    store.enqueue(fired, now_ms=T0 + 5_000)
    assert [e.enqueued_at_ms for e in store.outbox.values()] == [T0]
    store.ack((fired.alert_id, fired.version))
    store.ack((fired.alert_id, fired.version))  # повторне підтвердження нешкідливе
    assert store.outbox == {}


def test_mark_checked_only_moves_forward(path):
    store = AlertStore(path)
    assert store.mark_checked("XAU/USD", T0) is True
    assert store.mark_checked("XAU/USD", T0 - 60_000) is False
    assert store.mark_checked("XAU/USD", T0) is False
    assert store.checked_until_ms == {"XAU/USD": T0}


def test_failed_write_keeps_previous_file_and_leaves_no_tmp(path, monkeypatch):
    store = AlertStore(path)
    store.put(_alert("a1"))
    store.commit()
    before = path.read_bytes()
    store.put(_alert("a2"))

    def broken_replace(src, dst):
        raise OSError("диск повний")

    monkeypatch.setattr(store_mod.os, "replace", broken_replace)
    with pytest.raises(OSError, match="диск повний"):
        store.commit()
    assert path.read_bytes() == before
    assert [p.name for p in path.parent.iterdir()] == [STATE_FILE]


# ── AlertStore: зіпсований файл і відкат версії ──────────────────────────


def test_unparseable_file_quarantined_raw_and_store_starts_empty(path):
    path.write_bytes(b'{"schema": 1, "alerts": [')
    store = AlertStore.load(path, now_ms=T0)
    backup = path.with_name(f"{STATE_FILE}.corrupt-{T0}")
    assert backup.read_bytes() == b'{"schema": 1, "alerts": ['
    assert store.alerts == {} and len(store.problems) == 1 and backup.name in store.problems[0]


def test_one_invalid_alert_quarantines_copy_and_keeps_valid_ones(path):
    good, bad = alert_to_dict(_alert("a1")), {**alert_to_dict(_alert("a2")), "level": -1}
    doc = {"schema": 1, "alerts": [good, bad], "outbox": [], "checked_until_ms": {"XAU/USD": "вчора"}}
    path.write_text(json.dumps(doc), encoding="utf-8")
    store = AlertStore.load(path, now_ms=T0)
    assert list(store.alerts) == ["a1"]
    assert "alerts[1]" in store.problems[0] and "checked_until_ms[XAU/USD]" in store.problems[0]
    assert path.with_name(f"{STATE_FILE}.corrupt-{T0}").exists()


@pytest.mark.parametrize(
    "entry, reason",
    [
        ({"alert": alert_to_dict(_alert()), "enqueued_at_ms": T0, "attempts": 0}, "без факту спрацювання"),
        ({"alert": alert_to_dict(_fired()), "enqueued_at_ms": T0, "attempts": -1}, "attempts"),
        ({"alert": alert_to_dict(_fired()), "enqueued_at_ms": T0, "attempts": "1"}, "attempts"),
        ({"alert": alert_to_dict(_fired()), "enqueued_at_ms": T0}, "з ключами"),
    ],
)
def test_invalid_outbox_entry_reported_and_dropped(path, entry, reason):
    doc = {"schema": 1, "alerts": [], "outbox": [entry], "checked_until_ms": {}}
    path.write_text(json.dumps(doc), encoding="utf-8")
    store = AlertStore.load(path, now_ms=T0)
    assert store.outbox == {} and "outbox[0]" in store.problems[0] and reason in store.problems[0]


def test_wrong_top_level_shape_quarantined(path):
    path.write_text(json.dumps({"schema": 1, "alerts": []}), encoding="utf-8")
    store = AlertStore.load(path, now_ms=T0)
    assert store.alerts == {} and "верхній рівень" in store.problems[0]


def test_newer_schema_refuses_to_load_and_leaves_file_untouched(path):
    raw = json.dumps({"schema": 3, "alerts": [], "outbox": [], "checked_until_ms": {}}).encode()
    path.write_bytes(raw)
    with pytest.raises(StoreSchemaError, match="schema=3"):
        AlertStore.load(path, now_ms=T0)
    assert path.read_bytes() == raw and os.listdir(path.parent) == [STATE_FILE]
