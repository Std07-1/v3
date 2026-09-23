"""Засів M1 (`tools.fetch_tf_backfill`) пише в SSOT лише те, що записав би живий M1-полер.

Навіщо.
- Бар, що ще формується. 15.09.2026 засів GER30 без зсуву курсора записав хвилину 17:12 (v=41 проти ~130 у
  сусідів) як complete=true. Наступний засів її не виправляє — бари з open, що вже є на диску, пропускаються.
- Пласкі бари поза сесією. Брокер віддає O=H=L=C і після закриття; полер їх відкидає, а засів писав усе — NAS100 і
  US30 мають пласкі хвилини Сб 22:00 саме із засіву. Власник: TradingView пласких барів не показує, вони ламають графік.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
from pathlib import Path

import pytest

import tools.fetch_tf_backfill as backfill
from core.model.bars import CandleBar
from runtime.ingest.m1_session_filter import resolve_close_safety_ms, split_closed_bars

M1_MS = 60_000
SAFETY_MS = 8_000
# Середа 2026-09-09 12:00:30 UTC — усередині сесії календаря нижче.
NOW_MS = int(dt.datetime(2026, 9, 9, 12, 0, 30, tzinfo=dt.timezone.utc).timestamp() * 1000)
SATURDAY_22 = int(dt.datetime(2026, 9, 5, 22, 0, tzinfo=dt.timezone.utc).timestamp() * 1000)  # до NOW_MS, бар закритий
# Нд 21:30 — пауза, але за 30 хв до відкриття 22:00: «біля краю сесії», де лишається маркер anomaly.
SUNDAY_2130 = int(dt.datetime(2026, 9, 6, 21, 30, tzinfo=dt.timezone.utc).timestamp() * 1000)
CALENDAR_GROUP = {
    "market_weekend_open_dow": 6, "market_weekend_open_hm": "22:00",
    "market_weekend_close_dow": 4, "market_weekend_close_hm": "20:45",
    "market_daily_break_start_hm": "21:00", "market_daily_break_end_hm": "22:00",
}


# Ряд суцільний (open = close сусіда): ланцюг ADR-0101 не міняє барів у тестах правила сесії ADR-0099.
def _bar(open_ms: int, *, o=1.5, h=2.0, low=0.5, c=1.5, v=130.0) -> CandleBar:
    return CandleBar(symbol="GER30", tf_s=60, open_time_ms=open_ms, close_time_ms=open_ms + M1_MS,
                     o=o, h=h, low=low, c=c, v=v, complete=True, src="history")


def _flat(open_ms: int, v: float = 1.0) -> CandleBar:
    return _bar(open_ms, o=1.5, h=1.5, low=1.5, c=1.5, v=v)


def test_split_keeps_only_bars_closed_with_the_broker_safety_margin():
    now_ms = 1_789_492_348_000  # 17:12:28 — хвилина 17:12 ще формується
    forming = _bar(1_789_492_320_000, v=41.0)
    just_closed = _bar(forming.open_time_ms - M1_MS)  # закрилась о 17:12:00, запас 8 с минув
    closed, unclosed = split_closed_bars([just_closed, forming], now_ms, SAFETY_MS)
    assert closed == [just_closed]
    assert unclosed == [forming]


def test_split_holds_back_a_minute_closed_inside_the_safety_window():
    close_ms = 1_789_492_320_000
    bar = _bar(close_ms - M1_MS)
    assert split_closed_bars([bar], close_ms + SAFETY_MS - 1, SAFETY_MS) == ([], [bar])
    assert split_closed_bars([bar], close_ms + SAFETY_MS, SAFETY_MS) == ([bar], [])


@pytest.mark.parametrize("cfg, expected_ms", [
    ({"m1_poller": {"safety_delay_s": 10}}, 10_000),
    ({}, 8_000),
    ({"m1_poller": {"safety_delay_s": "хибне"}}, 8_000),
])
def test_safety_margin_comes_from_the_m1_poller_config(cfg, expected_ms):
    assert resolve_close_safety_ms(cfg) == expected_ms


class _FakeProvider:
    bars = []

    def __init__(self, **_kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def fetch_last_n_m1(self, symbol, n, date_to_utc=None):
        return list(type(self).bars)


def _run_main(tmp_path: Path, monkeypatch, bars, *, with_calendar=True, extra_argv=(), extra_cfg=None):
    data_root = tmp_path / "data_v3"
    cfg = {"data_root": str(data_root), "m1_poller": {"safety_delay_s": 8},
           # ADR-0095 S3a: писар SSOT будується з резолвером правила якоря H4/D1; без секції — відмова старту
           "htf_anchor": {"rule_by_calendar_group": {"test_group": "ny_close_us_dst"}}}
    cfg.update(extra_cfg or {})
    if with_calendar:
        cfg["market_calendar_by_group"] = {"test_group": CALENDAR_GROUP}
        cfg["market_calendar_symbol_groups"] = {"GER30": "test_group"}
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(cfg), encoding="utf-8")
    monkeypatch.setenv("AI_ONE_CONFIG_PATH", str(config_path))
    monkeypatch.setenv("FXCM_USERNAME", "u")
    monkeypatch.setenv("FXCM_PASSWORD", "p")
    monkeypatch.setattr(backfill, "load_env_secrets", lambda: None)
    monkeypatch.setattr(_FakeProvider, "bars", bars)
    monkeypatch.setattr(backfill, "FxcmHistoryProvider", _FakeProvider)
    monkeypatch.setattr(backfill.time, "time", lambda: NOW_MS / 1000)
    monkeypatch.setattr("sys.argv", ["fetch_tf_backfill", "--tf", "60", "--symbol", "GER30", "--n", str(len(bars))]
                        + list(extra_argv))
    rc = backfill.main()
    written = [json.loads(line) for part in sorted((data_root / "GER30" / "tf_60").glob("part-*.jsonl"))
               for line in part.read_text(encoding="utf-8").splitlines() if line.strip()]
    return rc, written


def test_main_does_not_write_the_forming_minute_to_ssot(tmp_path: Path, monkeypatch):
    forming_open = NOW_MS // M1_MS * M1_MS
    bars = [_bar(forming_open - 3 * M1_MS), _bar(forming_open - 2 * M1_MS), _bar(forming_open, v=41.0)]
    rc, written = _run_main(tmp_path, monkeypatch, bars)
    assert rc == 0
    assert [row["v"] for row in written] == [130.0, 130.0]


def test_main_drops_flat_bars_outside_session_and_marks_the_rest_like_the_poller(tmp_path: Path, monkeypatch):
    in_session = NOW_MS // M1_MS * M1_MS - 10 * M1_MS
    bars = [
        _bar(in_session),
        _flat(in_session + M1_MS),            # однотікова хвилина сесії — лишається з маркером
        _flat(SATURDAY_22 - 60 * M1_MS),      # Сб 21:00: глибоко у вихідних, плаский шум брокера — не пишеться
        _bar(SATURDAY_22 - 30 * M1_MS),       # Сб 21:30: глибоко у вихідних, неплаский — теж шум, не пишеться
        _bar(SUNDAY_2130),                    # Нд 21:30: пауза біля краю сесії — аномалія, пишеться з маркером
    ]
    rc, written = _run_main(tmp_path, monkeypatch, bars)
    assert rc == 0
    by_open = {row["open_time_ms"]: row for row in written}
    assert set(by_open) == {in_session, in_session + M1_MS, SUNDAY_2130}
    assert by_open[in_session + M1_MS]["extensions"] == {"trading_flat": True}
    assert by_open[SUNDAY_2130]["extensions"] == {"calendar_pause_nonflat_anomaly": True}


def test_main_pause_noise_margin_comes_from_config(tmp_path: Path, monkeypatch):
    """Засів бере запас із config (m1_session_filter.pause_noise_margin_min): при запасі на всю добу Сб 21:30 уже
    «біля краю» (до Нд 22:00 — 1470 хв) і пишеться як аномалія, а не відкидається як шум."""
    in_session = NOW_MS // M1_MS * M1_MS - 10 * M1_MS
    saturday_2130 = SATURDAY_22 - 30 * M1_MS
    rc, written = _run_main(tmp_path, monkeypatch, [_bar(in_session), _bar(saturday_2130)],
                            extra_cfg={"m1_session_filter": {"pause_noise_margin_min": 1470}})
    assert rc == 0
    by_open = {row["open_time_ms"]: row for row in written}
    assert by_open[saturday_2130]["extensions"] == {"calendar_pause_nonflat_anomaly": True}


def _deep_weekend_bars(count, *, v):
    """Неплаські бари глибоко у вихідних (Сб 21:59, 21:58, …), обсяг v."""
    return [_bar(SATURDAY_22 - (i + 1) * M1_MS, o=5.1, h=5.1, low=5.0, c=5.1, v=v) for i in range(count)]


def test_main_refuses_the_batch_when_trading_like_minutes_fall_outside_the_calendar(tmp_path: Path, monkeypatch):
    """Рев'ю D-01: хибний календар не має тихо їсти справжні хвилини. 5 глибоких барів з обсягом торгівлі
    (v ≥ m1_session_filter.pause_noise_alarm_min_volume, той самий критерій, що тривога полера) — понад допуск 3:
    відмова rc 1, нічого не записано."""
    in_session = _bar(NOW_MS // M1_MS * M1_MS - 10 * M1_MS)
    rc, written = _run_main(tmp_path, monkeypatch, [in_session] + _deep_weekend_bars(5, v=20.0))
    assert rc == 1
    assert written == []


@pytest.mark.parametrize("extra_argv", [(), ("--allow-off-calendar",)])
def test_main_weekend_noise_with_small_volume_is_dropped_without_refusal(tmp_path: Path, monkeypatch, extra_argv):
    """Рев'ю D-01: партія XAG/US30 через вихідні завжди має суботній шум (v 1–5). Він не йде в допуск — відмови
    немає — і відкидається навіть з --allow-off-calendar (раніше з прапором ішов у SSOT як anomaly)."""
    in_session = _bar(NOW_MS // M1_MS * M1_MS - 10 * M1_MS)
    weekend_noise = _deep_weekend_bars(10, v=5.0) + [_flat(SATURDAY_22 - (20 + i) * M1_MS) for i in range(3)]
    rc, written = _run_main(tmp_path, monkeypatch, [in_session] + weekend_noise, extra_argv=list(extra_argv))
    assert rc == 0
    assert [row["open_time_ms"] for row in written] == [in_session.open_time_ms]


def test_main_writes_trading_like_off_calendar_bars_as_anomaly_when_operator_allows_it(tmp_path: Path, monkeypatch):
    """--allow-off-calendar — свідоме рішення оператора: хвилини з обсягом торгівлі пишуться з маркером anomaly."""
    in_session = _bar(NOW_MS // M1_MS * M1_MS - 10 * M1_MS)
    trading_like = _deep_weekend_bars(5, v=20.0)
    rc, written = _run_main(tmp_path, monkeypatch, [in_session] + trading_like, extra_argv=["--allow-off-calendar"])
    assert rc == 0
    by_open = {row["open_time_ms"]: row for row in written}
    assert set(by_open) == {in_session.open_time_ms} | {bar.open_time_ms for bar in trading_like}
    assert all(by_open[bar.open_time_ms]["extensions"] == {"calendar_pause_nonflat_anomaly": True}
               for bar in trading_like)


def test_main_tolerates_a_couple_of_minutes_around_the_session_edge(tmp_path: Path, monkeypatch):
    """Дві-три хвилини навколо межі сесії — не ознака хибного календаря, партія пишеться без відмови."""
    edge = [_flat(SATURDAY_22 - M1_MS), _flat(SATURDAY_22 - 2 * M1_MS)]
    in_session = _bar(NOW_MS // M1_MS * M1_MS - 10 * M1_MS)
    rc, written = _run_main(tmp_path, monkeypatch, [in_session] + edge)
    assert rc == 0
    assert [row["open_time_ms"] for row in written] == [in_session.open_time_ms]


def test_main_refuses_a_symbol_without_session_calendar(tmp_path: Path, monkeypatch):
    rc, written = _run_main(tmp_path, monkeypatch, [_bar(NOW_MS // M1_MS * M1_MS - 5 * M1_MS)], with_calendar=False)
    assert rc == 2
    assert written == []


def test_allow_off_calendar_writes_deep_pause_nonflat_bar_as_anomaly(tmp_path: Path, monkeypatch, caplog):
    """Рев'ю п.3: прапор — підозра на хибний календар. Правило глибини вимкнене: глибокий неплаский бар пишеться з
    маркером anomaly (справжня хвилина під хибним календарем не зникає), пласкі поза сесією — як і раніше, ні."""
    in_session = NOW_MS // M1_MS * M1_MS - 10 * M1_MS
    saturday_2130 = SATURDAY_22 - 30 * M1_MS  # глибоко у вихідних: без прапора — шум, не пишеться
    off_calendar_flats = [_flat(SATURDAY_22 - (i + 1) * M1_MS) for i in range(6)]
    rc, written = _run_main(tmp_path, monkeypatch, [_bar(in_session), _bar(saturday_2130)] + off_calendar_flats,
                            extra_argv=["--allow-off-calendar"])
    assert rc == 0
    by_open = {row["open_time_ms"]: row for row in written}
    assert set(by_open) == {in_session, saturday_2130}
    assert by_open[saturday_2130]["extensions"] == {"calendar_pause_nonflat_anomaly": True}
    assert "BACKFILL_CALENDAR_RULES_RELAXED" in caplog.text


def test_without_the_flag_the_same_deep_pause_bar_is_noise(tmp_path: Path, monkeypatch, caplog):
    """Контроль: без прапора той самий бар — шум глибоко в паузі, і правила не послаблено."""
    in_session = NOW_MS // M1_MS * M1_MS - 10 * M1_MS
    saturday_2130 = SATURDAY_22 - 30 * M1_MS
    rc, written = _run_main(tmp_path, monkeypatch, [_bar(in_session), _bar(saturday_2130)])
    assert rc == 0
    assert [row["open_time_ms"] for row in written] == [in_session]
    assert "BACKFILL_CALENDAR_RULES_RELAXED" not in caplog.text

# --- Правило послідовності ADR-0101 (C3): вкладення краю і ланцюг через межі партії ---------------------------------

TUE_2059 = int(dt.datetime(2026, 9, 8, 20, 59, tzinfo=dt.timezone.utc).timestamp() * 1000)  # остання хвилина сесії
TUE_1200 = int(dt.datetime(2026, 9, 8, 12, 0, tzinfo=dt.timezone.utc).timestamp() * 1000)
EDGE_STALE_CFG = {"flat_bar_max_volume": 4,
                  "m1_session_filter": {"pause_edge_stale_groups": ["test_group"], "pause_edge_stale_volume_mult": 2}}


def _seed_ssot(tmp_path: Path, bars) -> None:
    """Бари, що вже лежать у SSOT до засіву (повні рядки, як їх пише JsonlAppender)."""
    for bar in bars:
        day = dt.datetime.fromtimestamp(bar.open_time_ms / 1000, tz=dt.timezone.utc).strftime("%Y%m%d")
        part = tmp_path / "data_v3" / "GER30" / "tf_60" / ("part-%s.jsonl" % day)
        part.parent.mkdir(parents=True, exist_ok=True)
        with part.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(bar.to_dict()) + "\n")


def test_main_folds_the_stale_edge_into_the_last_session_minute_like_tv(tmp_path: Path, monkeypatch, caplog):
    """XAU 22.09 (TV FX:XAUUSD 15m 20:45 C = 4357.74): 20:59 c=4357.63 + 21:00 v=4 c=4357.74 → 20:59 c=4357.74 v=520;
    21:00 не пишеться, open першої хвилини після перерви = вкладений close."""
    bars = [
        _bar(TUE_2059 - M1_MS, o=4358.0, h=4358.5, low=4357.9, c=4358.33, v=400.0),
        _bar(TUE_2059, o=4358.33, h=4358.73, low=4355.37, c=4357.63, v=516.0),
        _bar(TUE_2059 + M1_MS, o=4357.63, h=4357.74, low=4357.63, c=4357.74, v=4.0),
        _bar(TUE_2059 + 62 * M1_MS, o=4357.74, h=4363.07, low=4357.74, c=4363.06, v=397.0),
    ]
    caplog.set_level(logging.INFO)
    rc, written = _run_main(tmp_path, monkeypatch, bars, extra_cfg=EDGE_STALE_CFG)
    assert rc == 0
    by_open = {row["open_time_ms"]: row for row in written}
    assert sorted(by_open) == [TUE_2059 - M1_MS, TUE_2059, TUE_2059 + 62 * M1_MS]
    assert (by_open[TUE_2059]["c"], by_open[TUE_2059]["v"]) == (4357.74, 520.0)
    assert by_open[TUE_2059]["extensions"] == {"late_ticks_folded": 4.0}
    assert by_open[TUE_2059 + 62 * M1_MS]["o"] == 4357.74 and "extensions" not in by_open[TUE_2059 + 62 * M1_MS]
    assert "'pause_edge_stale_folded': 1" in caplog.text and "вкладено(застарілий край)=1" in caplog.text


def test_main_chains_the_batch_start_to_the_ssot_bar_before_it(tmp_path: Path, monkeypatch):
    """Ланцюг тримається на межі партії: перший новий бар — від close останнього видимого бару SSOT перед нею."""
    _seed_ssot(tmp_path, [_bar(TUE_1200, o=2000.1, h=2000.3, low=1999.9, c=2000.0)])
    batch = [_bar(TUE_1200 + M1_MS, o=2000.4, h=2001.0, low=2000.3, c=2000.9),
             _bar(TUE_1200 + 2 * M1_MS, o=2000.9, h=2001.2, low=2000.8, c=2001.1)]
    rc, written = _run_main(tmp_path, monkeypatch, batch)
    assert rc == 0
    by_open = {row["open_time_ms"]: row for row in written}
    assert (by_open[TUE_1200 + M1_MS]["o"], by_open[TUE_1200 + M1_MS]["low"]) == (2000.0, 2000.0)
    assert by_open[TUE_1200 + M1_MS]["extensions"] == {"open_chained_from": 2000.4}
    assert "extensions" not in by_open[TUE_1200 + 2 * M1_MS]


@pytest.mark.parametrize("last_new_close, break_expected", [(2002.0, False), (2001.5, True)])
def test_main_never_rewrites_the_ssot_bar_right_after_the_batch_and_names_the_break(
        tmp_path: Path, monkeypatch, caplog, last_new_close, break_expected):
    """Партія закінчується перед наявним баром SSOT (крок ланцюжка засіву назад; брокер віддає і сам цей бар). Засів
    його не переписує — друга версія ключа дописом віддала б переможця порядку у файлі (ADR-0098 §3.7); розрив на
    межі він називає з точним значенням для settle (M1_SSOT_EDIT_PENDING)."""
    ssot_next = _bar(TUE_1200 + 2 * M1_MS, o=2002.0, h=2002.4, low=2001.8, c=2002.2)
    _seed_ssot(tmp_path, [ssot_next])
    batch = [_bar(TUE_1200, o=2000.0, h=2002.1, low=1999.9, c=2000.5),
             _bar(TUE_1200 + M1_MS, o=2000.5, h=2002.1, low=2000.4, c=last_new_close),
             ssot_next]
    caplog.set_level(logging.INFO)
    rc, written = _run_main(tmp_path, monkeypatch, batch)
    assert rc == 0
    assert [row["open_time_ms"] for row in written].count(ssot_next.open_time_ms) == 1
    assert ("M1_SSOT_EDIT_PENDING" in caplog.text) is break_expected
    if break_expected:
        assert ("reason=chain open_ms=%d o=2002.00000->2001.50000" % ssot_next.open_time_ms) in caplog.text
