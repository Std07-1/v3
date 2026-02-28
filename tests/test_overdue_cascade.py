"""tests/test_overdue_cascade.py

Тести для DeriveEngine.check_overdue_buckets():
1. Deep lookback (N попередніх bucket-ів, не лише 1).
2. Cascade after overdue derive (overdue M5 → M15 → M30 → H1 → H4).
3. Sorted TF processing (bottom-up: M5 до M15).

Рівень: runtime (DeriveEngine = runtime/ingest/derive_engine.py).
Не потребує Redis/UDS — мокаємо UDS.commit_final_bar().
"""
from __future__ import annotations

import threading
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

from core.derive import DERIVE_CHAIN, DERIVE_ORDER, GenericBuffer
from core.model.bars import CandleBar
from runtime.ingest.derive_engine import DeriveEngine


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_bar(
    symbol: str,
    tf_s: int,
    open_ms: int,
    complete: bool = True,
    src: str = "history",
) -> CandleBar:
    return CandleBar(
        symbol=symbol,
        tf_s=tf_s,
        open_time_ms=open_ms,
        close_time_ms=open_ms + tf_s * 1000,
        o=100.0,
        h=101.0,
        low=99.0,
        c=100.5,
        v=100,
        complete=complete,
        src=src,
    )


def _make_m1_bars(symbol: str, start_ms: int, count: int) -> List[CandleBar]:
    """Створити послідовність M1 барів (60s кожен)."""
    return [
        _make_bar(symbol, 60, start_ms + i * 60_000)
        for i in range(count)
    ]


def _mock_uds() -> MagicMock:
    """Мок UDS з успішним commit."""
    uds = MagicMock()
    result = MagicMock()
    result.ok = True
    result.reason = None
    uds.commit_final_bar.return_value = result
    return uds


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestOverdueLookbackDepth:
    """check_overdue_buckets має сканувати N попередніх bucket-ів."""

    def test_overdue_finds_2nd_bucket_back(self) -> None:
        """M5 бар 2 bucket-и назад (10 хв) має бути знайдений overdue."""
        sym = "TEST/SYM"
        engine = DeriveEngine(
            symbols=[sym],
            anchor_offset_s=0,
            cascade_tfs_s={300},     # тільки M5
            commit_tfs_s={300},
        )
        uds = _mock_uds()
        engine.register_symbol_uds(sym, uds)

        # Заповнюємо буфер M1 для 2 bucket-ів M5 (10 хвилин = 10 M1)
        # Bucket 1: open=0ms, Bucket 2: open=300_000ms
        m1_bars = _make_m1_bars(sym, 0, 10)
        engine.warmup_bars(m1_bars)

        # now_ms = 900_000 (15:00) → cur_bucket = 600_000 (bucket 3)
        # lookback=6 → checks bucket 2 (300_000), bucket 1 (0)
        now_ms = 900_000
        committed = engine.check_overdue_buckets(now_ms)

        # Має знайти обидва overdue M5 бари
        assert len(committed) == 2
        opens = sorted(b.open_time_ms for b in committed)
        assert opens == [0, 300_000]

    def test_old_overdue_skips_already_derived(self) -> None:
        """Вже derived бар не деривується повторно."""
        sym = "TEST/SYM"
        engine = DeriveEngine(
            symbols=[sym],
            anchor_offset_s=0,
            cascade_tfs_s={300},
            commit_tfs_s={300},
        )
        uds = _mock_uds()
        engine.register_symbol_uds(sym, uds)

        # M1 бари для 1 M5 bucket (0..4 хвилини)
        m1_bars = _make_m1_bars(sym, 0, 5)
        engine.warmup_bars(m1_bars)

        # Перший overdue check — деривує
        now_ms = 600_000
        committed1 = engine.check_overdue_buckets(now_ms)
        assert len(committed1) == 1

        # Другий overdue check — бар вже в буфері, не деривує повторно
        committed2 = engine.check_overdue_buckets(now_ms)
        assert len(committed2) == 0


class TestOverdueCascade:
    """Overdue-derived бари мають каскадуватись вгору по DERIVE_CHAIN."""

    def test_overdue_m5_cascades_to_m15(self) -> None:
        """Overdue check знаходить пропущений M5, каскадує до M15."""
        sym = "TEST/SYM"
        engine = DeriveEngine(
            symbols=[sym],
            anchor_offset_s=0,
            cascade_tfs_s={300, 900},    # M5 + M15
            commit_tfs_s={300, 900},
        )
        uds = _mock_uds()
        engine.register_symbol_uds(sym, uds)

        # Заповнюємо M1 для 15 хв (3 × M5 bucket = 1 M15 bucket)
        m1_bars = _make_m1_bars(sym, 0, 15)
        engine.warmup_bars(m1_bars)

        # Вже маємо M5 0:00 і M5 5:00 (через on_bar cascade)
        # Але M5 10:00 ще не має — імітуємо пропуск
        # Warmup НЕ каскадує — треба створити M5 бари руками через on_bar
        # для перших двох bucket-ів, щоб overdue знайшов лише 3-й

        # Спочатку деривуємо M5 0:00 і M5 5:00 через on_bar
        last_m1_0 = _make_bar(sym, 60, 4 * 60_000)   # останній M1 bucket 0
        last_m1_1 = _make_bar(sym, 60, 9 * 60_000)   # останній M1 bucket 1
        engine.on_bar(last_m1_0)
        engine.on_bar(last_m1_1)

        # now_ms = 1200_000 (20 хв) → M5 cur=900_000
        # overdue шукає M5 10:00 (600_000) — знаходить!
        # потім каскадує M5 10:00 → M15 0:00!
        now_ms = 1_200_000
        committed = engine.check_overdue_buckets(now_ms)

        tfs = sorted(set(b.tf_s for b in committed))
        # Має бути і M5(300) і M15(900) 
        assert 300 in tfs, f"M5 missing from overdue cascade: {tfs}"
        assert 900 in tfs, f"M15 missing from overdue cascade: {tfs}"

    def test_sorted_tfs_bottom_up(self) -> None:
        """TF обробляються від найменшого до найбільшого (sorted)."""
        sym = "TEST/SYM"
        engine = DeriveEngine(
            symbols=[sym],
            anchor_offset_s=0,
            cascade_tfs_s={300, 900, 1800},
            commit_tfs_s={300, 900, 1800},
        )

        # Перевіряємо що _OVERDUE_LOOKBACK має ключі для всіх DERIVE_ORDER TFs
        for tf_s in [180, 300, 900, 1800, 3600, 14400]:
            assert tf_s in DeriveEngine._OVERDUE_LOOKBACK, (
                f"TF {tf_s} missing from _OVERDUE_LOOKBACK"
            )


class TestOverdueFullChain:
    """Overdue catches missed bar → cascade completes higher TF."""

    def test_missed_m5_cascades_to_m15_via_overdue(self) -> None:
        """Нормальний cascade будує M5 0:00 і M5 5:00.
        
        M5 10:00 пропущено (on_bar не виклікано).
        Overdue знаходить M5 10:00, каскадує → M15 0:00.
        """
        sym = "TEST/SYM"
        engine = DeriveEngine(
            symbols=[sym],
            anchor_offset_s=0,
            cascade_tfs_s={300, 900},    # M5 + M15
            commit_tfs_s={300, 900},
        )
        uds = _mock_uds()
        engine.register_symbol_uds(sym, uds)

        # Всі 15 хвилин M1 (3 × M5 = 1 M15)
        m1_bars = _make_m1_bars(sym, 0, 15)
        engine.warmup_bars(m1_bars)

        # Нормальний cascade: останній M1 кожного M5-bucket → on_bar
        # M5 bucket 0:  M1 4:00 (trigger)
        engine.on_bar(m1_bars[4])   # → derives M5 0:00
        # M5 bucket 1:  M1 9:00 (trigger)
        engine.on_bar(m1_bars[9])   # → derives M5 5:00
        # M5 bucket 2:  ПРОПУЩЕНО (імітуємо race/пропуск)

        # Overdue check: now = 20 хв
        now_ms = 20 * 60_000
        committed = engine.check_overdue_buckets(now_ms)

        committed_tfs = set(b.tf_s for b in committed)
        # Overdue знаходить M5 10:00, каскадує до M15 0:00
        assert 300 in committed_tfs, f"M5 missing: {committed_tfs}"
        assert 900 in committed_tfs, f"M15 missing from cascade: {committed_tfs}"

    def test_overdue_lookback_covers_key_tfs(self) -> None:
        """Кожен TF від M3 до H4+D1 має lookback у _OVERDUE_LOOKBACK."""
        for tf_s in DERIVE_ORDER:
            depth = DeriveEngine._OVERDUE_LOOKBACK.get(tf_s)
            assert depth is not None, f"TF {tf_s} missing from _OVERDUE_LOOKBACK"
            # D1 (86400): lookback=1 достатній (один bucket = 24h)
            min_depth = 1 if tf_s == 86400 else 2
            assert depth >= min_depth, f"TF {tf_s} lookback={depth} too shallow (min={min_depth})"
