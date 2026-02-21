"""S2 тести: Tick drops degraded-but-loud — tick_agg stats в TICK_PREVIEW_STATS + WARNING при зростанні."""

from __future__ import annotations

import json
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import MagicMock


class _FakeAgg:
    """Мок TickAggregator з керованими stats."""
    def __init__(self, stats_dict):
        self._stats = dict(stats_dict)

    def stats(self):
        return dict(self._stats)

    def set_stats(self, d):
        self._stats.update(d)


_DEFAULT_AGG_STATS = {
    "ticks_total": 0,
    "ticks_rejected_tf": 0,
    "ticks_dropped_late_bucket": 0,
    "ticks_dropped_before_open": 0,
    "ticks_dropped_out_of_order": 0,
    "promoted_total": 0,
}


def _make_worker(agg_stats=None):
    """Створює TickPreviewWorker з fake UDS і fake agg."""
    from runtime.ingest.tick_preview_worker import TickPreviewWorker
    mock_uds = MagicMock()
    worker = TickPreviewWorker(
        uds=mock_uds,
        tfs=[60],
        publish_min_interval_ms=250,
        curr_ttl_s=1800,
        symbols=["XAU/USD"],
        channel="test:ticks",
        auto_promote_m1=False,
    )
    fake_agg = _FakeAgg(agg_stats or dict(_DEFAULT_AGG_STATS))
    worker._agg = fake_agg
    worker._stats_last_emit_ts = 0.0
    worker._inc("ticks_in_total", 10)
    return worker, fake_agg


def test_payload_contains_tick_agg_stats(caplog):
    """TICK_PREVIEW_STATS payload містить tick_agg_stats."""
    worker, _ = _make_worker({
        "ticks_total": 100,
        "ticks_rejected_tf": 5,
        "ticks_dropped_late_bucket": 2,
        "ticks_dropped_before_open": 1,
        "ticks_dropped_out_of_order": 0,
        "promoted_total": 3,
    })
    with caplog.at_level(logging.DEBUG):
        worker._maybe_emit_stats()
    stats_msgs = [r.message for r in caplog.records if "TICK_PREVIEW_STATS" in r.message]
    assert len(stats_msgs) > 0, "TICK_PREVIEW_STATS має бути в логах"
    json_start = stats_msgs[0].index("{")
    payload = json.loads(stats_msgs[0][json_start:])
    assert "tick_agg_stats" in payload
    agg = payload["tick_agg_stats"]
    assert agg["ticks_dropped_late_bucket"] == 2
    assert agg["ticks_dropped_before_open"] == 1
    assert agg["ticks_total"] == 100


def test_no_warning_on_first_emit(caplog):
    """Перший emit після старту — НЕ дає WARNING (навіть якщо drops > 0)."""
    worker, _ = _make_worker({
        "ticks_total": 50,
        "ticks_rejected_tf": 0,
        "ticks_dropped_late_bucket": 5,
        "ticks_dropped_before_open": 3,
        "ticks_dropped_out_of_order": 2,
        "promoted_total": 0,
    })
    with caplog.at_level(logging.DEBUG):
        worker._maybe_emit_stats()
    warning_msgs = [r.message for r in caplog.records if "TICK_AGG_DROPS" in r.message]
    assert len(warning_msgs) == 0, "Перший emit не повинен давати WARNING"


def test_warning_on_drops_increase(caplog):
    """Другий emit з зростанням drops — є WARNING."""
    worker, fake_agg = _make_worker({
        "ticks_total": 50,
        "ticks_rejected_tf": 0,
        "ticks_dropped_late_bucket": 5,
        "ticks_dropped_before_open": 0,
        "ticks_dropped_out_of_order": 0,
        "promoted_total": 0,
    })
    # Перший emit — ініціалізація
    worker._inc("ticks_in_total", 10)
    worker._maybe_emit_stats()
    # Збільшуємо drops
    fake_agg.set_stats({"ticks_dropped_late_bucket": 12})
    worker._stats_last_emit_ts = 0.0
    worker._inc("ticks_in_total", 10)
    caplog.clear()
    with caplog.at_level(logging.DEBUG):
        worker._maybe_emit_stats()
    warning_msgs = [r.message for r in caplog.records if "TICK_AGG_DROPS" in r.message]
    assert len(warning_msgs) > 0, "WARNING TICK_AGG_DROPS має бути"
    assert "drops=7" in warning_msgs[0]


def test_no_warning_when_drops_stable(caplog):
    """Якщо drops не зросли — WARNING немає."""
    worker, _ = _make_worker({
        "ticks_total": 50,
        "ticks_rejected_tf": 0,
        "ticks_dropped_late_bucket": 5,
        "ticks_dropped_before_open": 0,
        "ticks_dropped_out_of_order": 0,
        "promoted_total": 0,
    })
    # Перший emit
    worker._inc("ticks_in_total", 10)
    worker._maybe_emit_stats()
    # Другий emit — drops НЕ змінились
    worker._stats_last_emit_ts = 0.0
    worker._inc("ticks_in_total", 10)
    caplog.clear()
    with caplog.at_level(logging.DEBUG):
        worker._maybe_emit_stats()
    warning_msgs = [r.message for r in caplog.records if "TICK_AGG_DROPS" in r.message]
    assert len(warning_msgs) == 0, "WARNING не повинен бути якщо drops стабільні"
