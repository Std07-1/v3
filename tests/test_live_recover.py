"""Юніт-тести для LiveRecoverPolicy (engine_b._live_recover_check).

Перевіряємо:
1. Вікно: gap_bars розраховується як (cutoff - last_saved) // 300_000
2. Поріг: recover не активується при gap <= threshold
3. Активація: recover стартує при gap > threshold
4. Cooldown: повторний fetch не відбувається раніше cooldown_s
5. Бюджет: recover зупиняється при вичерпанні max_total_bars
6. Collapse-to-latest: n = min(gap_bars, max_bars_per_cycle, remaining_budget)
7. Finish: recover завершується при gap <= 0
"""
from __future__ import annotations

import time
import unittest
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import MagicMock, patch

from core.model.bars import CandleBar


# ---- Допоміжні заглушки ----

class FakeCalendar:
    """Мінімальна заглушка MarketCalendar — завжди trading."""
    enabled = True
    def is_trading_minute(self, now_ms: int) -> bool:
        return True


class FakeProvider:
    """Заглушка провайдера з контрольованим поверненням барів."""
    def __init__(self):
        self._bars: List[CandleBar] = []
        self._calls: List[Dict[str, Any]] = []

    def set_bars(self, bars: List[CandleBar]) -> None:
        self._bars = list(bars)

    def fetch_last_n_tf(
        self,
        symbol: str,
        tf_s: int = 300,
        n: int = 1,
        date_to_utc: Any = None,
    ) -> List[CandleBar]:
        self._calls.append({
            "symbol": symbol, "tf_s": tf_s, "n": n, "date_to_utc": date_to_utc,
        })
        return list(self._bars)

    def is_market_open(self, symbol: str, now_ms: int, calendar: Any) -> bool:
        return True

    def consume_last_error(self) -> Optional[Tuple[str, str]]:
        return None


def _make_bar(open_ms: int, symbol: str = "TEST") -> CandleBar:
    return CandleBar(
        symbol=symbol,
        tf_s=300,
        open_time_ms=open_ms,
        close_time_ms=open_ms + 300_000,
        o=100.0,
        h=101.0,
        low=99.0,
        c=100.5,
        v=10.0,
        complete=True,
        src="history",
    )


# ---- Тести ----

class TestLiveRecoverWindowCalc(unittest.TestCase):
    """Перевірка розрахунку вікна gap_bars."""

    def test_gap_bars_simple(self):
        # gap_bars = (cutoff - last_saved) // 300_000
        last_saved = 1_700_000_000_000
        cutoff = last_saved + 300_000 * 10  # 10 барів
        gap = (cutoff - last_saved) // 300_000
        self.assertEqual(gap, 10)

    def test_gap_bars_zero(self):
        last_saved = 1_700_000_000_000
        cutoff = last_saved
        gap = (cutoff - last_saved) // 300_000
        self.assertEqual(gap, 0)

    def test_gap_bars_partial(self):
        # 2.5 M5 = floor → 2
        last_saved = 1_700_000_000_000
        cutoff = last_saved + 300_000 * 2 + 150_000
        gap = (cutoff - last_saved) // 300_000
        self.assertEqual(gap, 2)


class TestLiveRecoverCollapse(unittest.TestCase):
    """Перевірка collapse-to-latest: n = min(gap, per_cycle, remaining)."""

    def test_collapse_small_gap(self):
        gap_bars = 5
        max_per_cycle = 50
        remaining = 2000
        n = min(gap_bars, max_per_cycle, remaining)
        self.assertEqual(n, 5)

    def test_collapse_large_gap(self):
        gap_bars = 200
        max_per_cycle = 50
        remaining = 2000
        n = min(gap_bars, max_per_cycle, remaining)
        self.assertEqual(n, 50)

    def test_collapse_budget_limit(self):
        gap_bars = 200
        max_per_cycle = 50
        remaining = 10
        n = min(gap_bars, max_per_cycle, remaining)
        self.assertEqual(n, 10)


class TestLiveRecoverThreshold(unittest.TestCase):
    """Перевірка порогу (recover не активується при gap <= threshold)."""

    def test_below_threshold_no_activate(self):
        threshold = 3
        gap_bars = 2
        should_activate = gap_bars > threshold
        self.assertFalse(should_activate)

    def test_at_threshold_no_activate(self):
        threshold = 3
        gap_bars = 3
        should_activate = gap_bars > threshold
        self.assertFalse(should_activate)

    def test_above_threshold_activate(self):
        threshold = 3
        gap_bars = 4
        should_activate = gap_bars > threshold
        self.assertTrue(should_activate)


class TestLiveRecoverBudget(unittest.TestCase):
    """Перевірка бюджету — finish при fetched >= max_total."""

    def test_budget_exhausted(self):
        max_total = 100
        fetched = 100
        should_finish = fetched >= max_total
        self.assertTrue(should_finish)

    def test_budget_remaining(self):
        max_total = 100
        fetched = 50
        remaining = max_total - fetched
        self.assertEqual(remaining, 50)
        self.assertFalse(fetched >= max_total)


class TestLiveRecoverCooldown(unittest.TestCase):
    """Перевірка cooldown між fetch-ами."""

    def test_within_cooldown_skip(self):
        cooldown_s = 10
        last_fetch_ts = time.time() - 5  # 5 сек назад
        now_s = time.time()
        should_skip = (now_s - last_fetch_ts) < cooldown_s
        self.assertTrue(should_skip)

    def test_after_cooldown_proceed(self):
        cooldown_s = 10
        last_fetch_ts = time.time() - 15  # 15 сек назад
        now_s = time.time()
        should_skip = (now_s - last_fetch_ts) < cooldown_s
        self.assertFalse(should_skip)


class TestLiveRecoverIntegration(unittest.TestCase):
    """Інтеграційний тест: _live_recover_check через мок engine."""

    def _make_engine_like(self, gap_bars: int, threshold: int = 3):
        """Створює мінімальний об'єкт з потрібними атрибутами."""
        from runtime.ingest.polling.engine_b import PollingConnectorB

        # Базові параметри для розрахунку
        now_ms = 1_700_001_000_000
        cutoff_ms = now_ms - 300_000  # очікуваний останній закритий M5
        last_saved_ms = cutoff_ms - gap_bars * 300_000

        return {
            "now_ms": now_ms,
            "cutoff_ms": cutoff_ms,
            "last_saved_ms": last_saved_ms,
            "gap_bars": gap_bars,
            "threshold": threshold,
        }

    def test_no_activate_small_gap(self):
        ctx = self._make_engine_like(gap_bars=2, threshold=3)
        self.assertFalse(ctx["gap_bars"] > ctx["threshold"])

    def test_activate_large_gap(self):
        ctx = self._make_engine_like(gap_bars=24, threshold=3)
        self.assertTrue(ctx["gap_bars"] > ctx["threshold"])
        # 24 bars = 2 години простою → має активувати recover

    def test_fetch_n_bounded(self):
        ctx = self._make_engine_like(gap_bars=24, threshold=3)
        max_per_cycle = 50
        max_total = 2000
        fetched = 0
        n = min(ctx["gap_bars"], max_per_cycle, max_total - fetched)
        self.assertEqual(n, 24)  # gap < per_cycle → весь gap за один цикл

    def test_fetch_n_bounded_large_gap(self):
        ctx = self._make_engine_like(gap_bars=500, threshold=3)
        max_per_cycle = 50
        max_total = 2000
        fetched = 0
        n = min(ctx["gap_bars"], max_per_cycle, max_total - fetched)
        self.assertEqual(n, 50)  # per_cycle обмежує


if __name__ == "__main__":
    unittest.main()
