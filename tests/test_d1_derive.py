"""tests/test_d1_derive.py

Тести для D1 live derive from M1 (ADR-0023).

Перевіряє:
1. D1 (86400) є в DERIVE_CHAIN, DERIVE_ORDER, DERIVE_SOURCE.
2. resolve_cascade_anchor_s() повертає правильний anchor для D1/H4/інших TF.
3. derive_triggers() з d1_anchor_offset_s розрізняє D1 vs H4 anchor.
4. derive_bar() для D1 з ~1440 M1 барів (boundary-tolerant).
5. DeriveEngine каскадує D1 через on_bar() з M1.

Рівень: core + runtime.
"""
from __future__ import annotations

import unittest
from typing import Any, Dict, Optional
from unittest.mock import MagicMock

from core.derive import (
    DERIVE_CHAIN,
    DERIVE_ORDER,
    DERIVE_SOURCE,
    GenericBuffer,
    MAX_MID_SESSION_GAPS_BY_TF,
    derive_bar,
    derive_triggers,
    resolve_cascade_anchor_s,
)
from core.model.bars import CandleBar


# ---------------------------------------------------------------------------
# Константи для тестування
# ---------------------------------------------------------------------------
H4_ANCHOR = 82800   # 23:00 UTC
D1_ANCHOR = 79200   # 22:00 UTC (5PM EST)
M1_TF_S = 60
D1_TF_S = 86400
H4_TF_S = 14400
D1_TF_MS = D1_TF_S * 1000
M1_TF_MS = M1_TF_S * 1000

# D1 bucket: 2026-02-26 22:00 UTC → 2026-02-27 22:00 UTC
# open_ms = 1772143200000 (Thu 2026-02-26 22:00:00 UTC)
# Aligned: (1772143200000 - 79200000) % 86400000 == 0
D1_BUCKET_OPEN_MS = 1772143200000


def _make_m1(open_ms: int, close_price: float = 100.0, complete: bool = True) -> CandleBar:
    """Створює M1 CandleBar."""
    return CandleBar(
        symbol="XAU/USD",
        tf_s=M1_TF_S,
        open_time_ms=open_ms,
        close_time_ms=open_ms + M1_TF_MS,
        o=100.0,
        h=101.0,
        low=99.0,
        c=close_price,
        v=10,
        complete=complete,
        src="history",
    )


class TestDeriveChainD1(unittest.TestCase):
    """D1 (86400) присутній в каскадних структурах."""

    def test_d1_in_derive_chain(self) -> None:
        targets = DERIVE_CHAIN.get(M1_TF_S, [])
        d1_entry = [(t, n) for t, n in targets if t == D1_TF_S]
        self.assertEqual(len(d1_entry), 1)
        self.assertEqual(d1_entry[0], (86400, 1440))

    def test_d1_in_derive_order(self) -> None:
        self.assertIn(D1_TF_S, DERIVE_ORDER)

    def test_d1_in_derive_source(self) -> None:
        self.assertIn(D1_TF_S, DERIVE_SOURCE)
        src_tf, bars_needed = DERIVE_SOURCE[D1_TF_S]
        self.assertEqual(src_tf, M1_TF_S)
        self.assertEqual(bars_needed, 1440)

    def test_d1_order_before_m15(self) -> None:
        """D1 має бути перед M15 (900) у DERIVE_ORDER — бо source=M1."""
        d1_idx = DERIVE_ORDER.index(D1_TF_S)
        m15_idx = DERIVE_ORDER.index(900)
        self.assertLess(d1_idx, m15_idx)

    def test_d1_max_mid_session_gaps(self) -> None:
        """D1 має підвищений ліміт gap'ів."""
        self.assertIn(D1_TF_S, MAX_MID_SESSION_GAPS_BY_TF)
        self.assertGreaterEqual(MAX_MID_SESSION_GAPS_BY_TF[D1_TF_S], 10)


class TestResolveCascadeAnchor(unittest.TestCase):
    """resolve_cascade_anchor_s() — централізований anchor routing."""

    def test_d1_returns_d1_anchor(self) -> None:
        self.assertEqual(
            resolve_cascade_anchor_s(86400, H4_ANCHOR, D1_ANCHOR),
            D1_ANCHOR,
        )

    def test_h4_returns_h4_anchor(self) -> None:
        self.assertEqual(
            resolve_cascade_anchor_s(14400, H4_ANCHOR, D1_ANCHOR),
            H4_ANCHOR,
        )

    def test_h1_returns_zero(self) -> None:
        self.assertEqual(
            resolve_cascade_anchor_s(3600, H4_ANCHOR, D1_ANCHOR),
            0,
        )

    def test_m5_returns_zero(self) -> None:
        self.assertEqual(
            resolve_cascade_anchor_s(300, H4_ANCHOR, D1_ANCHOR),
            0,
        )

    def test_defaults_are_zero(self) -> None:
        self.assertEqual(resolve_cascade_anchor_s(86400), 0)
        self.assertEqual(resolve_cascade_anchor_s(14400), 0)


class TestDeriveTriggerD1(unittest.TestCase):
    """derive_triggers() для D1 — використовує d1_anchor_offset_s."""

    def test_last_m1_triggers_d1(self) -> None:
        """Останній M1 у D1 bucket має тригернути D1 derive."""
        # Останній M1 у bucket = bucket_end - M1_TF_MS
        last_m1_open = D1_BUCKET_OPEN_MS + D1_TF_MS - M1_TF_MS
        bar = _make_m1(last_m1_open)
        triggers = derive_triggers(
            bar,
            anchor_offset_s=H4_ANCHOR,
            d1_anchor_offset_s=D1_ANCHOR,
        )
        d1_triggers = [(t, o) for t, o in triggers if t == D1_TF_S]
        self.assertEqual(len(d1_triggers), 1)
        self.assertEqual(d1_triggers[0][1], D1_BUCKET_OPEN_MS)

    def test_non_last_m1_no_d1_trigger(self) -> None:
        """M1 на початку D1 bucket НЕ тригерить D1."""
        bar = _make_m1(D1_BUCKET_OPEN_MS)
        triggers = derive_triggers(
            bar,
            anchor_offset_s=H4_ANCHOR,
            d1_anchor_offset_s=D1_ANCHOR,
        )
        d1_triggers = [(t, o) for t, o in triggers if t == D1_TF_S]
        self.assertEqual(len(d1_triggers), 0)


class TestDeriveBarD1(unittest.TestCase):
    """derive_bar() для D1 — boundary-tolerant з 1440 M1 барів."""

    def _fill_buffer(self, buf: GenericBuffer, start_ms: int, count: int) -> None:
        """Заповнює буфер count M1 барами починаючи з start_ms."""
        for i in range(count):
            bar = _make_m1(start_ms + i * M1_TF_MS, close_price=100.0 + i * 0.01)
            buf.upsert(bar)

    def test_full_1440_derives_d1(self) -> None:
        """1440 M1 барів → 1 D1 бар."""
        buf = GenericBuffer(M1_TF_S, max_keep=2000)
        self._fill_buffer(buf, D1_BUCKET_OPEN_MS, 1440)
        result = derive_bar(
            symbol="XAU/USD",
            target_tf_s=D1_TF_S,
            source_buffer=buf,
            bucket_open_ms=D1_BUCKET_OPEN_MS,
            anchor_offset_s=H4_ANCHOR,
            d1_anchor_offset_s=D1_ANCHOR,
        )
        self.assertIsNotNone(result)
        self.assertEqual(result.tf_s, D1_TF_S)
        self.assertEqual(result.open_time_ms, D1_BUCKET_OPEN_MS)
        self.assertTrue(result.complete)

    def test_missing_m1_returns_none(self) -> None:
        """Неповний буфер → None."""
        buf = GenericBuffer(M1_TF_S, max_keep=2000)
        self._fill_buffer(buf, D1_BUCKET_OPEN_MS, 100)  # Лише 100 з 1440
        result = derive_bar(
            symbol="XAU/USD",
            target_tf_s=D1_TF_S,
            source_buffer=buf,
            bucket_open_ms=D1_BUCKET_OPEN_MS,
            anchor_offset_s=H4_ANCHOR,
            d1_anchor_offset_s=D1_ANCHOR,
        )
        self.assertIsNone(result)

    def test_d1_with_calendar_boundary_tolerance(self) -> None:
        """D1 з calendar gaps (market break) → boundary-tolerant derive."""
        buf = GenericBuffer(M1_TF_S, max_keep=2000)

        # Заповнюємо 1440 слотів, пропускаючи 5 на початку (market open boundary)
        skip_first = 5
        for i in range(skip_first, 1440):
            bar = _make_m1(D1_BUCKET_OPEN_MS + i * M1_TF_MS)
            buf.upsert(bar)

        # Calendar: перші 5 хвилин — non-trading
        def is_trading(ms: int) -> bool:
            offset = ms - D1_BUCKET_OPEN_MS
            return offset >= skip_first * M1_TF_MS

        result = derive_bar(
            symbol="XAU/USD",
            target_tf_s=D1_TF_S,
            source_buffer=buf,
            bucket_open_ms=D1_BUCKET_OPEN_MS,
            anchor_offset_s=H4_ANCHOR,
            d1_anchor_offset_s=D1_ANCHOR,
            is_trading_fn=is_trading,
        )
        self.assertIsNotNone(result)
        self.assertEqual(result.tf_s, D1_TF_S)


def _session_calendar(bucket_open_ms: int, session_minutes: int):
    """Календар: торгові перші session_minutes хвилин кожної доби від bucket_open_ms (далі — break)."""

    def is_trading(ms: int) -> bool:
        return (ms - bucket_open_ms) % D1_TF_MS < session_minutes * M1_TF_MS

    return is_trading


class TestD1BuiltFromAvailableMinutes(unittest.TestCase):
    """ADR-0097: тонка/святкова доба = D1 з наявних хвилин, щойно джерело дійшло до кінця доби."""

    SESSION_MIN = 1380  # 22:00→21:00 торгово, 21:00→22:00 break

    def _derive(self, buf: GenericBuffer, target_tf_s: int = D1_TF_S, bucket_open_ms: int = D1_BUCKET_OPEN_MS):
        return derive_bar(
            symbol="XAU/USD",
            target_tf_s=target_tf_s,
            source_buffer=buf,
            bucket_open_ms=bucket_open_ms,
            anchor_offset_s=H4_ANCHOR,
            d1_anchor_offset_s=D1_ANCHOR,
            is_trading_fn=_session_calendar(D1_BUCKET_OPEN_MS, self.SESSION_MIN),
        )

    def _holiday_day(self) -> GenericBuffer:
        """Ранній закрив: торгувались лише перші 1140 хвилин із 1380 (240 бракує — як 07.09 Labor Day)."""
        buf = GenericBuffer(M1_TF_S, max_keep=4000)
        for i in range(1140):
            buf.upsert(_make_m1(D1_BUCKET_OPEN_MS + i * M1_TF_MS, close_price=100.0 + i * 0.01))
        return buf

    def test_holiday_day_is_not_finalized_before_the_source_reaches_day_end(self) -> None:
        self.assertIsNone(self._derive(self._holiday_day()))

    def test_holiday_day_is_built_once_the_next_session_minute_arrives(self) -> None:
        buf = self._holiday_day()
        buf.upsert(_make_m1(D1_BUCKET_OPEN_MS + D1_TF_MS))  # перша хвилина наступної сесії

        bar = self._derive(buf)

        self.assertIsNotNone(bar)
        self.assertEqual(bar.open_time_ms, D1_BUCKET_OPEN_MS)
        self.assertAlmostEqual(bar.c, 100.0 + 1139 * 0.01)
        self.assertEqual(bar.v, 1140 * 10)
        self.assertIn("thin_session", bar.extensions["partial_reasons"])
        self.assertEqual(bar.extensions["source_count"], 1140)
        self.assertEqual(bar.extensions["expected_count"], self.SESSION_MIN)

    def test_thin_day_is_built_when_its_last_trading_minute_arrives(self) -> None:
        buf = GenericBuffer(M1_TF_S, max_keep=4000)
        silent = set(range(300, 330))  # 30 хвилин без угод посеред сесії
        for i in range(self.SESSION_MIN):
            if i not in silent:
                buf.upsert(_make_m1(D1_BUCKET_OPEN_MS + i * M1_TF_MS))

        bar = self._derive(buf)

        self.assertIsNotNone(bar)
        self.assertEqual(bar.extensions["mid_session_gaps"], 30)
        self.assertIn("thin_session", bar.extensions["partial_reasons"])

    def test_day_within_budget_carries_no_thin_session_mark(self) -> None:
        buf = GenericBuffer(M1_TF_S, max_keep=4000)
        for i in range(self.SESSION_MIN):
            if i not in (500, 501):
                buf.upsert(_make_m1(D1_BUCKET_OPEN_MS + i * M1_TF_MS))

        bar = self._derive(buf)

        self.assertIsNotNone(bar)
        self.assertNotIn("thin_session", bar.extensions["partial_reasons"])

    def test_other_tfs_keep_the_gap_budget(self) -> None:
        buf = GenericBuffer(M1_TF_S, max_keep=4000)
        buf.upsert(_make_m1(D1_BUCKET_OPEN_MS))
        buf.upsert(_make_m1(D1_BUCKET_OPEN_MS + 10 * M1_TF_MS))  # фронтир далеко за M5

        self.assertIsNone(self._derive(buf, target_tf_s=300, bucket_open_ms=D1_BUCKET_OPEN_MS))


if __name__ == "__main__":
    unittest.main()
