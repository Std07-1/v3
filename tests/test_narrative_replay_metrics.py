"""Метрики прогону наративу (ADR-0113 Ф1): епізоди й сетапи, мигання, «чекаємо без змісту», угода від краю зони."""
from __future__ import annotations

from tools.narrative_replay_metrics import compute_metrics, episodes, parse_zone_range, setups, trade_outcome

T0 = 1_791_964_800_000  # середа 14.10.2026 08:00 UTC
STEP = 900_000
HOUR = 3_600_000
LONG_ZONE = (4090.0, 4095.0)  # OB▲: вхід — верхній край 4095, ціль PDH 4120, інвалідація нижче 4085


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


def _m1(minute, h, low):
    return {"open_time_ms": T0 + minute * 60_000, "h": h, "low": low}


def _outcome(bars, start_minute=0, horizon_ms=HOUR):
    opens = [b["open_time_ms"] for b in bars]
    return trade_outcome(bars, opens, T0 + start_minute * 60_000, horizon_ms, "long", LONG_ZONE, 4120.0, 4085.0)


def test_episodes_break_on_gaps_and_setups_merge_episodes_of_one_zone():
    rows = [_row(0, zone_id="z1"), _row(1, zone_id="z1"), _row(2, closed=True), _row(3, zone_id="z1"),
            _row(4, zone_id="z2"), _row(5)]

    found = episodes(rows)

    assert [[r["t"] for r in ep] for ep in found] == [[T0, T0 + STEP], [T0 + 3 * STEP], [T0 + 4 * STEP]]
    assert [[r["t"] for r in setup] for setup in setups(found)] == [[T0, T0 + STEP, T0 + 3 * STEP], [T0 + 4 * STEP]]


def test_trade_needs_entry_at_zone_edge_before_target_counts():
    assert _outcome([_m1(0, 4101, 4097), _m1(1, 4121, 4100)]) == "missed"  # до цілі без входу — угоди не було
    assert _outcome([_m1(0, 4101, 4094), _m1(1, 4121, 4100)]) == "target"  # торкнулась 4095 → потім ціль
    assert _outcome([_m1(0, 4101, 4094), _m1(1, 4096, 4080)]) == "invalidated"
    assert _outcome([_m1(0, 4101, 4084)]) == "ambiguous"  # вхід і стоп в одному барі
    assert _outcome([_m1(0, 4101, 4094), _m1(1, 4125, 4080)]) == "ambiguous"  # після входу — обидва в барі
    assert _outcome([_m1(0, 4101, 4094), _m1(1, 4110, 4090)]) == "open"
    assert _outcome([_m1(0, 4101, 4097)]) == "no_fill"
    assert _outcome([_m1(0, 4101, 4094), _m1(90, 4121, 4100)]) == "open"  # ціль поза горизонтом години


def test_trade_outcome_short_mirrors_long():
    bars = [_m1(0, 4091, 4085), _m1(1, 4088, 4060)]
    opens = [b["open_time_ms"] for b in bars]

    assert trade_outcome(bars, opens, T0, HOUR, "short", LONG_ZONE, 4065.0, 4100.0) == "target"  # вхід 4090
    assert trade_outcome(bars, opens, T0, HOUR, "short", LONG_ZONE, 0.0, 4100.0) == "no_target"


def test_zone_range_parse_takes_prices_not_grade_score():
    assert parse_zone_range("OB▲ A(6) 4090.00–4095.00") == (4090.0, 4095.0)
    assert parse_zone_range("OB▼ A+(9) 72.515–75.175") == (72.515, 75.175)
    assert parse_zone_range("без діапазону") is None


def test_flip_back_counts_short_detour_only_and_empty_wait_needs_no_next_area():
    a = dict(headline="A")
    b = dict(headline="B")
    rows = [_row(0, **a), _row(1, **b), _row(2, **a), _row(3, **b), _row(4, **b), _row(5, **b), _row(6, **a),
            _row(7, headline="A", next_area="")]

    metrics = compute_metrics(rows, [])["900"]

    assert metrics["flip_backs"] == 2  # A|B|A|BBB|AA: B (1 крок) між A і A, A (1 крок) між B і B; BBB — ні
    assert metrics["changes_per_day"] == 4.0  # 4 зміни за одну добу (next_area не в стані)
    assert metrics["empty_wait_share"] == round(1 / 8, 4)


def test_setup_outcome_counted_once_with_confirmation_and_rr_at_entry():
    rows = [_row(0, zone_id="z1", trigger="approaching"), _row(1), _row(2, zone_id="z1", trigger="ready")]
    confirm_minute = 30
    bars = [_m1(0, 4101, 4094), _m1(1, 4096, 4084), _m1(confirm_minute, 4096, 4094), _m1(31, 4121, 4100)]

    metrics = compute_metrics(rows, bars, horizon_h=72)["900"]

    assert metrics["episodes"] == 2 and metrics["setups"] == 1  # мигання не подвоює результат
    assert metrics["confirmed"] == 1 and metrics["confirm_min_median"] == 30.0
    assert metrics["outcomes"] == {"invalidated": 1}  # від першої появи: вхід 4095 → стоп 4085
    assert metrics["outcomes_after_confirm"] == {"target": 1}  # від підтвердження: вхід → ціль
    assert metrics["rr_at_entry_median"] == 2.5  # (4120 − 4095) / (4095 − 4085)
