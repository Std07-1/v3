"""RedisLayer.read_preview_updates: gap і «майбутній курсор» → fast-forward (I5, гучно).

Корінь (розслідування 21.09, D2): курсор більший за останній виданий seq (скинутий
лічильник Redis або курсор чужого кільця після гонки switch) відкидав кожну нову подію
як seq <= since_seq — delta мовчала годинами. Тепер це gap з fast-forward до лічильника.
Шлях через UDS.read_updates перевіряє, що споживачі бачать warning "cursor_gap".
"""

from __future__ import annotations

import tempfile

from preview_ring_fake import PreviewRingFakeRedis
from runtime.store.layers.redis_layer import RedisLayer
from runtime.store.redis_keys import preview_updates_seq_key
from runtime.store.uds import UnifiedDataStore, UpdatesSpec, _NullDiskLayer

NS = "t"
SYM = "XAU/USD"
TF = 60
RETAIN = 50


def _layer_with_ring(last_seq: int, count: int) -> tuple[RedisLayer, PreviewRingFakeRedis]:
    fake = PreviewRingFakeRedis()
    if count:
        fake.seed_ring(NS, SYM, TF, last_seq, count, retain=RETAIN)
    else:
        fake.kv[preview_updates_seq_key(NS, SYM, TF)] = str(last_seq).encode()
    return RedisLayer(fake, NS), fake


def _read(layer: RedisLayer, since_seq):
    return layer.read_preview_updates(SYM, TF, since_seq, 500, RETAIN)


def test_read_preview_updates_future_cursor_fast_forwards_to_counter():
    layer, _ = _layer_with_ring(last_seq=1000, count=20)
    events, cursor, gap, err = _read(layer, 5_000_000)
    assert err is None
    assert events == []
    assert cursor == 1000
    assert gap is not None and gap["reason"] == "cursor_ahead"
    assert gap["last_seq_available"] == 1000


def test_read_preview_updates_future_cursor_then_delivers_new_events():
    layer, fake = _layer_with_ring(last_seq=1000, count=20)
    _, cursor, _, _ = _read(layer, 5_000_000)
    fake.push_ring_event(NS, SYM, TF, 1_790_000_060_000, retain=RETAIN)
    events, cursor2, gap, _ = _read(layer, cursor)
    assert gap is None
    assert [ev["seq"] for ev in events] == [1001]
    assert cursor2 == 1001


def test_read_preview_updates_empty_ring_future_cursor_is_gap():
    layer, _ = _layer_with_ring(last_seq=7, count=0)
    events, cursor, gap, _ = _read(layer, 900)
    assert events == [] and cursor == 7
    assert gap is not None and gap["reason"] == "cursor_ahead"
    assert gap["first_seq_available"] is None


def test_read_preview_updates_empty_ring_cursor_at_counter_no_gap():
    layer, _ = _layer_with_ring(last_seq=7, count=0)
    events, cursor, gap, _ = _read(layer, 7)
    assert (events, cursor, gap) == ([], 7, None)


def test_read_preview_updates_counter_ahead_of_list_is_publish_window_not_gap():
    """INCR виконано, RPUSH ще ні: курсор adopt-tail = лічильник > max у списку — це не gap."""
    layer, fake = _layer_with_ring(last_seq=1000, count=20)
    fake.incr(preview_updates_seq_key(NS, SYM, TF))  # лічильник 1001, подія ще не в списку
    events, cursor, gap, _ = _read(layer, 1001)
    assert (events, cursor, gap) == ([], 1001, None)


def test_read_preview_updates_cursor_behind_ring_reports_reason():
    layer, _ = _layer_with_ring(last_seq=1000, count=20)
    events, cursor, gap, _ = _read(layer, 10)
    assert events == [] and cursor == 1000
    assert gap is not None and gap["reason"] == "cursor_behind"
    assert gap["first_seq_available"] == 981


def test_read_preview_updates_idle_cursor_at_max_no_counter_read():
    """Звичайний idle-poll (since == max) не читає лічильник — нуль зайвих GET."""
    layer, fake = _layer_with_ring(last_seq=1000, count=20)
    gets: list[str] = []
    original_get = fake.get
    fake.get = lambda key: gets.append(key) or original_get(key)  # type: ignore[method-assign]
    events, cursor, gap, _ = _read(layer, 1000)
    assert (events, cursor, gap) == ([], 1000, None)
    assert gets == []


def test_read_preview_updates_corrupt_counter_keeps_cursor_without_gap():
    layer, fake = _layer_with_ring(last_seq=1000, count=20)
    fake.kv[preview_updates_seq_key(NS, SYM, TF)] = b"not-a-number"
    events, cursor, gap, err = _read(layer, 5_000_000)
    assert (events, cursor, gap, err) == ([], 5_000_000, None, None)


def test_uds_read_updates_future_cursor_surfaces_cursor_gap_warning():
    layer, _ = _layer_with_ring(last_seq=1000, count=20)
    with tempfile.TemporaryDirectory() as tmp:
        uds = UnifiedDataStore(
            data_root=tmp,
            boot_id="t",
            tf_allowlist={TF},
            min_coldload_bars={},
            role="reader",
            redis_layer=layer,
            disk_layer=_NullDiskLayer(),
            preview_tf_allowlist={TF},
        )
        result = uds.read_updates(
            UpdatesSpec(symbol=SYM, tf_s=TF, since_seq=5_000_000, limit=500, include_preview=True)
        )
    assert result.events == []
    assert result.cursor_seq == 1000
    assert "cursor_gap" in result.warnings
    assert result.meta["extensions"]["gap"]["reason"] == "cursor_ahead"
