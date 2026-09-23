"""ADR-0101 C4, рев'ю S3: бар, який ховає display, — одне правило для display, ланцюга й агрегації похідних.

Критерій приховування живе лише в `core.model.candle_chain.is_display_hidden`. Якщо його змінять, display
(`candle_map`), записувачі й вимір ланцюга (`m1_session_filter`, `m1_poller`, health) і агрегація (`derive`,
`rebuild_from_m1`) зміняться разом: сторож нижче не пускає маркер літералом у код поза модулем предиката.
"""
from __future__ import annotations

import datetime as dt
import re
from pathlib import Path
from typing import Any

import pytest

from core.derive import aggregate_bars
from core.health import measure_chain_breaks
from core.model.bars import CandleBar
from core.model.candle_chain import MARKER_CALENDAR_PAUSE_FLAT, is_display_hidden
from runtime.ws.candle_map import map_bar_to_candle_v4

REPO = Path(__file__).resolve().parents[1]
M1_MS = 60_000
TUESDAY_10 = int(dt.datetime(2026, 7, 7, 10, tzinfo=dt.timezone.utc).timestamp()) * 1000


def _m1(open_ms: int, o: float, c: float, **extensions: Any) -> CandleBar:
    return CandleBar(
        symbol="X", tf_s=60, open_time_ms=open_ms, close_time_ms=open_ms + M1_MS,
        o=o, h=max(o, c), low=min(o, c), c=c, v=10.0, complete=True, src="history", extensions=dict(extensions),
    )


@pytest.mark.parametrize(
    "extensions, hidden",
    [
        ({MARKER_CALENDAR_PAUSE_FLAT: True}, True),
        ({MARKER_CALENDAR_PAUSE_FLAT: False}, False),
        ({}, False),
        (None, False),
        ("calendar_pause_flat", False),  # зіпсоване поле рядка — не словник
    ],
)
def test_is_display_hidden_reads_only_explicit_marker(extensions, hidden):
    assert is_display_hidden(extensions) is hidden


def test_display_chain_and_aggregation_skip_the_same_hidden_bar():
    """Прихований бар 10:01 з чужою ціною: display його не малює, ланцюг іде повз, агрегат M3 його не бере."""
    first = _m1(TUESDAY_10, 100.0, 101.0)
    hidden = _m1(TUESDAY_10 + M1_MS, 90.0, 90.0, **{MARKER_CALENDAR_PAUSE_FLAT: True})
    third = _m1(TUESDAY_10 + 2 * M1_MS, 101.0, 102.0)

    assert map_bar_to_candle_v4(hidden.to_dict(), tf_s=60) is None
    assert map_bar_to_candle_v4(first.to_dict(), tf_s=60) is not None

    chain = measure_chain_breaks([first, hidden, third], is_trading_fn=lambda _ms: True)
    assert (chain.checked, chain.hidden, chain.inner, chain.at_gap) == (1, 1, 0, 0)

    m3 = aggregate_bars([first, hidden, third], symbol="X", target_tf_s=180, bucket_open_ms=TUESDAY_10)
    assert m3 is not None
    assert (m3.o, m3.h, m3.low, m3.c) == (100.0, 102.0, 100.0, 102.0)
    assert m3.extensions.get("partial_calendar_pause") is True


def test_marker_literal_lives_only_in_candle_chain():
    """Сторож D15.2: код читає маркер лише через `is_display_hidden` — другого критерію приховування немає."""
    literal = re.compile(r"""["']%s["']""" % MARKER_CALENDAR_PAUSE_FLAT)
    home = REPO / "core" / "model" / "candle_chain.py"
    offenders = [
        "%s:%d" % (path.relative_to(REPO).as_posix(), lineno)
        for top in ("core", "runtime", "tools")
        for path in sorted((REPO / top).rglob("*.py"))
        if path != home
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if literal.search(line)
    ]
    assert offenders == []
