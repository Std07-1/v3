"""Детектор «знята / не знята» (ADR-0113 Ф2a): момент першого дотику ціни після утворення рівня, найдрібніший ряд,
що покриває момент; збіг з прямим перебором на випадкових рядах."""
from __future__ import annotations

import random

from core.model.bars import CandleBar
from core.smc.liquidity_sweep import TouchIndex, first_reached

H1_MS = 3_600_000
T0 = 1_791_964_800_000


def _bars(highs_lows, step_ms=H1_MS, start=T0):
    return [CandleBar("XAU/USD", step_ms // 1000, start + i * step_ms, start + (i + 1) * step_ms, low, h, low, h, 1.0,
                      True, "history") for i, (h, low) in enumerate(highs_lows)]


def test_first_touch_after_moment_above_and_below():
    index = TouchIndex(_bars([(105, 95), (110, 100), (103, 97), (112, 90)]))

    assert index.first_reached(T0 + H1_MS, 109.0)[0] == T0 + H1_MS  # high 110 — на барі 1
    assert index.first_reached(T0 + H1_MS, 92.0)[1] == T0 + 3 * H1_MS  # low 90 — на барі 3 (бари 0 до моменту не йдуть)
    assert index.first_reached(T0 + 2 * H1_MS, 111.0)[0] == T0 + 3 * H1_MS
    assert index.first_reached(T0 + 2 * H1_MS, 120.0)[0] is None
    assert index.first_reached(T0 + 4 * H1_MS, 100.0) == (None, None)  # після останнього бару — дотику ще нема


def test_matches_brute_force_on_random_series():
    rng = random.Random(7)
    for _ in range(200):
        n = rng.randint(1, 60)
        rows = []
        for _ in range(n):
            low = rng.uniform(90, 110)
            rows.append((low + rng.uniform(0, 5), low))
        bars = _bars(rows)
        index = TouchIndex(bars)
        moment = T0 + rng.randint(-2, n + 1) * H1_MS
        price = rng.uniform(88, 117)
        after = [b for b in bars if b.open_time_ms >= moment]
        expect = (next((b.open_time_ms for b in after if b.h >= price), None),
                  next((b.open_time_ms for b in after if b.low <= price), None))
        assert index.first_reached(moment, price) == expect


def test_finest_series_covering_the_moment_wins_older_moment_falls_to_coarser():
    fine = TouchIndex(_bars([(101, 99), (130, 99)], step_ms=300_000, start=T0 + 10 * H1_MS))  # лише свіже вікно M5
    coarse = TouchIndex(_bars([(105, 95), (120, 100)] + [(101, 99)] * 10))  # H1 від T0

    assert first_reached([fine, coarse], T0 + 10 * H1_MS, 125.0)[0] == T0 + 10 * H1_MS + 300_000  # дрібний ряд
    assert first_reached([fine, coarse], T0, 115.0)[0] == T0 + H1_MS  # момент старший за M5 — старший ряд
    assert first_reached([fine], T0, 115.0) == (None, None)  # жоден ряд не покриває — дотику не видно
