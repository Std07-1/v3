"""Рейка старту поллера (ADR-0095 S4a2): останній H4/D1 на диску поза сезонною сіткою — BOOTSTRAP_DEGRADED.

Сценарій, який вона ловить: код із сезонним якорем задеплоєно, а міграцію даних (S7) — ні. Деривація пише нову сітку,
писар відкидає стару, а на диску хвіст ще на старій — старт має це сказати голосно, але не падати.
"""
from __future__ import annotations

import datetime as dt
import logging
from typing import Dict, List

import pytest

from core.model.bars import CandleBar
from core.session_anchor import D1_S, H4_S, RULE_NY_CLOSE_US_DST
from runtime.ingest.derive_engine import DeriveEngine
from runtime.ingest.polling.m1_poller import M1PollerRunner

SYM = "XAU/USD"


def _ms(y: int, mo: int, d: int, h: int = 0) -> int:
    return int(dt.datetime(y, mo, d, h, tzinfo=dt.timezone.utc).timestamp() * 1000)


def _bar(tf_s: int, open_ms: int) -> CandleBar:
    return CandleBar(symbol=SYM, tf_s=tf_s, open_time_ms=open_ms, close_time_ms=open_ms + tf_s * 1000, o=1.0, h=1.0,
                     low=1.0, c=1.0, v=1.0, complete=True, src="derived")


class _Poller:
    _symbol = SYM
    _bars_on_disk = 10**6  # не «незайманий»: initial backfill не потрібен

    def warmup_watermark(self, tail_n: int) -> int:
        _ = tail_n
        return 0


class _Uds:
    """Диск: лише останні H4/D1; решта хвостів порожня."""

    def __init__(self, tails: Dict[int, List[CandleBar]]) -> None:
        self._tails = tails

    def bootstrap_prime_from_disk(self, symbol: str, tf_s: int, tail_n: int) -> int:
        _ = symbol, tf_s, tail_n
        return 0

    def read_tail_candles(self, symbol: str, tf_s: int, limit: int) -> List[CandleBar]:
        _ = symbol, limit
        return list(self._tails.get(tf_s, []))


def _bootstrap(tails: Dict[int, List[CandleBar]], caplog) -> str:
    engine = DeriveEngine(symbols=[SYM], anchor_rules={SYM: RULE_NY_CLOSE_US_DST})
    runner = M1PollerRunner(pollers=[_Poller()], provider=object(), uds=_Uds(tails), redis_tail_n={},
                            tail_catchup_enabled=False, derive_engine=engine, cascade_catchup_m1_bars=0)
    with caplog.at_level(logging.WARNING):
        runner._bootstrap_warmup()  # noqa: SLF001
    return caplog.text


@pytest.mark.parametrize("h4_open, d1_open, off_grid", [
    # H4 на старій сітці 22/02/.. влітку — пре-міграційний прод
    (_ms(2026, 9, 22, 22), _ms(2026, 9, 21, 21), "%s:%d:%d" % (SYM, H4_S, _ms(2026, 9, 22, 22))),
    # D1 22:00 влітку
    (_ms(2026, 9, 22, 21), _ms(2026, 9, 21, 22), "%s:%d:%d" % (SYM, D1_S, _ms(2026, 9, 21, 22))),
])
def test_bootstrap_htf_grid_mismatch_degraded(caplog, h4_open, d1_open, off_grid):
    text = _bootstrap({H4_S: [_bar(H4_S, h4_open)], D1_S: [_bar(D1_S, d1_open)]}, caplog)

    assert "BOOTSTRAP_DEGRADED phase=htf_grid_mismatch_on_disk bars=%s " % off_grid in text
    assert "M1_POLLER_BOOTSTRAP_DEGRADED" in text and "'htf_grid_mismatch_on_disk: %s'" % off_grid in text


def test_bootstrap_on_season_grid_tail_is_quiet(caplog):
    text = _bootstrap({H4_S: [_bar(H4_S, _ms(2026, 9, 22, 21))], D1_S: [_bar(D1_S, _ms(2026, 9, 21, 21))]}, caplog)
    assert "htf_grid" not in text


def test_bootstrap_grid_check_failure_is_loud_not_fatal(caplog):
    class _BrokenUds(_Uds):
        def read_tail_candles(self, symbol, tf_s, limit):
            if tf_s in (H4_S, D1_S):
                raise OSError("disk unavailable")
            return []

    engine = DeriveEngine(symbols=[SYM], anchor_rules={SYM: RULE_NY_CLOSE_US_DST})
    runner = M1PollerRunner(pollers=[_Poller()], provider=object(), uds=_BrokenUds({}), redis_tail_n={},
                            tail_catchup_enabled=False, derive_engine=engine, cascade_catchup_m1_bars=0)
    with caplog.at_level(logging.WARNING):
        runner._bootstrap_warmup()  # noqa: SLF001
    assert "BOOTSTRAP_DEGRADED phase=htf_grid_check err=disk unavailable" in caplog.text
