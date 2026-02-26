from __future__ import annotations

from core.derive import GenericBuffer, derive_bar
from core.model.bars import CandleBar


def _m1_bar(open_ms: int, *, price: float, calendar_pause_flat: bool = False) -> CandleBar:
    return CandleBar(
        symbol="XAU/USD",
        tf_s=60,
        open_time_ms=open_ms,
        close_time_ms=open_ms + 60_000,
        o=price,
        h=price + 0.2,
        low=price - 0.2,
        c=price + 0.1,
        v=10.0,
        complete=True,
        src="history",
        extensions={"calendar_pause_flat": True} if calendar_pause_flat else {},
    )


def test_m5_elapsed_partial_calendar_pause_sets_complete_and_extensions() -> None:
    """Elapsed bucket + partial calendar pause => complete=True + explicit partial markers."""
    bucket_open_ms = 0
    buf = GenericBuffer(tf_s=60, max_keep=32)

    bars = [
        _m1_bar(0, price=10.0),
        _m1_bar(60_000, price=11.0),
        _m1_bar(120_000, price=12.0, calendar_pause_flat=True),
        _m1_bar(180_000, price=13.0, calendar_pause_flat=True),
        _m1_bar(240_000, price=14.0),
    ]
    buf.upsert_many(bars)

    out = derive_bar(
        symbol="XAU/USD",
        target_tf_s=300,
        source_buffer=buf,
        bucket_open_ms=bucket_open_ms,
        is_trading_fn=lambda _t: True,
        filter_calendar_pause=True,
    )

    assert out is not None
    assert out.complete is True
    assert out.extensions.get("partial") is True
    assert out.extensions.get("partial_calendar_pause") is True
    assert out.extensions.get("source_count") == 3
    assert out.extensions.get("expected_count") == 5
    assert out.extensions.get("partial_reasons") == ["calendar_pause"]


def test_strict_client_can_filter_partial_by_extension_flag() -> None:
    """Strict clients можуть фільтрувати partial derived bars через extensions.partial."""
    bucket_open_ms = 0
    buf = GenericBuffer(tf_s=60, max_keep=32)

    buf.upsert_many(
        [
            _m1_bar(0, price=10.0),
            _m1_bar(120_000, price=12.0, calendar_pause_flat=True),
            _m1_bar(180_000, price=13.0),
            _m1_bar(240_000, price=14.0),
        ]
    )

    out = derive_bar(
        symbol="XAU/USD",
        target_tf_s=300,
        source_buffer=buf,
        bucket_open_ms=bucket_open_ms,
        is_trading_fn=lambda _t: True,
        filter_calendar_pause=True,
    )

    assert out is not None
    strict_accept = not bool(out.extensions.get("partial"))
    assert strict_accept is False

def test_partial_reasons_can_coexist_for_boundary_and_calendar_pause() -> None:
    """Partial причини можуть співіснувати: boundary_gap + calendar_pause."""
    bucket_open_ms = 0
    buf = GenericBuffer(tf_s=60, max_keep=32)

    # Пропускаємо першу хвилину bucket (boundary gap), одну хвилину позначаємо calendar pause.
    buf.upsert_many(
        [
            _m1_bar(120_000, price=12.0, calendar_pause_flat=True),
            _m1_bar(180_000, price=13.0),
            _m1_bar(240_000, price=14.0),
        ]
    )

    def _is_trading(t: int) -> bool:
        # Перший слот (t=0) не торговий, тому відсутній t=60 вважається open-boundary gap.
        if t == 0:
            return False
        return True

    out = derive_bar(
        symbol="XAU/USD",
        target_tf_s=300,
        source_buffer=buf,
        bucket_open_ms=bucket_open_ms,
        is_trading_fn=_is_trading,
        filter_calendar_pause=True,
    )

    assert out is not None
    reasons = out.extensions.get("partial_reasons")
    assert isinstance(reasons, list)
    assert "calendar_pause" in reasons
    assert "boundary_gap" in reasons
