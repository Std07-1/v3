from __future__ import annotations

from runtime.store.uds import UnifiedDataStore


def _make_uds() -> UnifiedDataStore:
    return UnifiedDataStore(
        data_root="./data_v3",
        boot_id="test-boot",
        tf_allowlist={60, 300},
        min_coldload_bars={300: 1},
        role="reader",
    )


def test_bars_to_lwc_keeps_extensions_and_adds_partial_penalty() -> None:
    uds = _make_uds()
    out = uds._bars_to_lwc(
        [
            {
                "open_time_ms": 0,
                "close_time_ms": 300_000,
                "tf_s": 300,
                "o": 1.0,
                "h": 1.2,
                "low": 0.9,
                "c": 1.1,
                "v": 5.0,
                "src": "derived",
                "complete": True,
                "extensions": {
                    "partial": True,
                    "partial_calendar_pause": True,
                    "source_count": 3,
                    "expected_count": 5,
                },
            }
        ]
    )

    assert len(out) == 1
    assert out[0]["extensions"]["partial"] is True
    assert out[0]["partial_penalty"] == 0.4


def test_bars_to_lwc_does_not_add_penalty_for_full_bar() -> None:
    uds = _make_uds()
    out = uds._bars_to_lwc(
        [
            {
                "open_time_ms": 0,
                "close_time_ms": 300_000,
                "tf_s": 300,
                "o": 1.0,
                "h": 1.2,
                "low": 0.9,
                "c": 1.1,
                "v": 5.0,
                "src": "history",
                "complete": True,
                "extensions": {},
            }
        ]
    )

    assert len(out) == 1
    assert "partial_penalty" not in out[0]
