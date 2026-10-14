"""Метрики прогону наративу (ADR-0113 Ф1): епізоди, мигання, «чекаємо без змісту», результат сценарію по M1."""
from __future__ import annotations

from tools.narrative_replay_metrics import compute_metrics, episodes, first_touch

T0 = 1_791_964_800_000  # середа 14.10.2026 08:00 UTC
STEP = 900_000


def _narrative(headline="⬜ Немає зони рівня A — чекаємо сетап", mode="wait", zone_id="", trigger="approaching",
               next_area="OB▲ A(6) 4090.00–4095.00", closed=False, direction="long"):
    scenarios = []
    if zone_id:
        scenarios = [{"zone_id": zone_id, "direction": direction, "entry_desc": "OB▲ A(6) 4090.00–4095.00",
                      "trigger": trigger, "trigger_desc": "", "target_desc": "PDH 4120.00",
                      "invalidation": "Нижче 4085.00 — зона пробита"}]
    return {"mode": "trade" if zone_id else mode, "headline": headline, "scenarios": scenarios,
            "next_area": "" if closed else next_area, "market_phase": "closed" if closed else "ranging"}


def _row(i, **kwargs):
    return {"t": T0 + i * STEP, "symbol": "XAU/USD", "tf_s": 900, "price": 4100.0, "atr": 4.0,
            "narrative": _narrative(**kwargs)}


def _m1(i, h, low):
    return {"open_time_ms": T0 + i * 60_000, "h": h, "low": low}


def test_episode_is_consecutive_same_primary_zone_and_closed_market_breaks_it():
    rows = [_row(0, zone_id="z1"), _row(1, zone_id="z1"), _row(2, closed=True), _row(3, zone_id="z1"),
            _row(4, zone_id="z2"), _row(5)]

    found = episodes(rows)

    assert [[r["t"] for r in ep] for ep in found] == [[T0, T0 + STEP], [T0 + 3 * STEP], [T0 + 4 * STEP]]


def test_first_touch_long_target_invalidation_ambiguous_open_and_no_target():
    bars = [_m1(0, 4101, 4099), _m1(1, 4121, 4100), _m1(2, 4100, 4080)]
    opens = [b["open_time_ms"] for b in bars]
    hour = 3_600_000

    assert first_touch(bars, opens, T0, hour, "long", 4120.0, 4085.0) == "target"
    assert first_touch(bars, opens, T0 + 120_000, hour, "long", 4120.0, 4085.0) == "invalidated"  # з бару 2
    assert first_touch([_m1(0, 4125, 4080)], [T0], T0, hour, "long", 4120.0, 4085.0) == "ambiguous"
    assert first_touch(bars, opens, T0, 60_000, "long", 4130.0, 4050.0) == "open"  # горизонт — лише бар 0
    assert first_touch(bars, opens, T0, hour, "long", 0.0, 4085.0) == "no_target"
    assert first_touch(bars, opens, T0, hour, "short", 4080.0, 4121.0) == "invalidated"  # бар 1: h=4121


def test_flip_back_counts_short_detour_only_and_empty_wait_needs_no_next_area():
    a = dict(headline="A")
    b = dict(headline="B")
    rows = [_row(0, **a), _row(1, **b), _row(2, **a), _row(3, **b), _row(4, **b), _row(5, **b), _row(6, **a),
            _row(7, headline="A", next_area="")]

    metrics = compute_metrics(rows, [])["900"]

    assert metrics["flip_backs"] == 2  # A|B|A|BBB|AA: B (1 крок) між A і A, A (1 крок) між B і B; BBB — ні
    assert metrics["changes_per_day"] == 4.0  # A|B|A|BBB|A A — 4 зміни за одну добу (next_area не в стані)
    assert metrics["empty_wait_share"] == round(1 / 8, 4)


def test_confirmation_time_and_outcome_from_confirmation():
    rows = [_row(0, zone_id="z1", trigger="approaching"), _row(1, zone_id="z1", trigger="in_zone"),
            _row(2, zone_id="z1", trigger="ready")]
    confirm_ms = T0 + 2 * STEP
    bars = [_m1(0, 4101, 4084), _m1(30, 4121, 4100)]  # до підтвердження — інвалідація, після — ціль
    bars[1]["open_time_ms"] = confirm_ms

    metrics = compute_metrics(rows, bars, horizon_h=72)["900"]

    assert metrics["episodes"] == 1 and metrics["confirmed"] == 1
    assert metrics["confirm_min_median"] == 30.0
    assert metrics["outcomes"] == {"invalidated": 1}
    assert metrics["outcomes_after_confirm"] == {"target": 1}
