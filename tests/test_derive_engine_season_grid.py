"""DeriveEngine на сезонній сітці (ADR-0095 S4a): правило якоря на символ, overdue крокує по сітці, відмова гучна.

До S4a overdue брав `cur - tf*i`: на вихідних переходу DST доба має 23/25 год, тож у пн 09.03 і пн 02.11 крок назад
виходив за сітку H4/D1 (з правилом — OffSeasonGridError посеред циклу поллера, зі старими якорями — тихий бакет не
тієї сітки). Відмову писаря overdue ковтав мовчки і все одно каскадував бар, якого нема на диску.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
from pathlib import Path
from typing import List, Tuple
from unittest.mock import MagicMock

import pytest

from core.derive import derive_bar as real_derive_bar
from core.model.bars import CandleBar
from core.session_anchor import (
    D1_S,
    H4_S,
    RULE_NY_CLOSE_US_DST,
    RULE_UTC_MIDNIGHT,
    assert_on_season_grid,
    htf_bucket_start_ms,
    htf_next_bucket_start_ms,
)
from runtime.ingest import derive_engine as derive_engine_module
from runtime.ingest.derive_engine import DeriveEngine, build_derive_engine
from runtime.ingest.market_calendar import MarketCalendar

FXCM = RULE_NY_CLOSE_US_DST
SYM = "XAU/USD"
H1_MS = 3_600_000
H4_MS = H4_S * 1000
D1_MS = D1_S * 1000
UTC = dt.timezone.utc


def _ms(y: int, mo: int, d: int, h: int = 0, mi: int = 0) -> int:
    return int(dt.datetime(y, mo, d, h, mi, tzinfo=UTC).timestamp() * 1000)


def _cfd_us_calendar() -> MarketCalendar:
    """cfd_us_22_23 з config.json: вихідні Пт 20:45 → Нд 22:00, перерва 21:00–22:00."""
    return MarketCalendar(enabled=True, weekend_close_dow=4, weekend_close_hm="20:45", weekend_open_dow=6,
                          weekend_open_hm="22:00", daily_break_start_hm="21:00", daily_break_end_hm="22:00",
                          daily_break_enabled=True)


def _bar(tf_s: int, open_ms: int, price: float = 100.0) -> CandleBar:
    return CandleBar(symbol=SYM, tf_s=tf_s, open_time_ms=open_ms, close_time_ms=open_ms + tf_s * 1000,
                     o=price, h=price + 1.0, low=price - 1.0, c=price + 0.5, v=10.0, complete=True, src="history")


def _result(ok: bool, reason=None) -> MagicMock:
    result = MagicMock()
    result.ok = ok
    result.reason = reason
    return result


def _ok_uds() -> MagicMock:
    uds = MagicMock()
    uds.commit_final_bar.return_value = _result(True)
    return uds


# ── Правило на символ: гучно на старті, а не тихий якір 0 на першому H4 ───────────────────────────────────────


def test_engine_symbol_without_rule_is_refused_at_init():
    with pytest.raises(ValueError, match="DERIVE_ENGINE_ANCHOR_RULE_MISSING"):
        DeriveEngine(symbols=[SYM, "NAS100"], anchor_rules={SYM: FXCM})


def test_engine_unknown_rule_is_refused_at_init():
    with pytest.raises(ValueError, match="DERIVE_ENGINE_ANCHOR_RULE_UNKNOWN"):
        DeriveEngine(symbols=[SYM], anchor_rules={SYM: "tv_anchor_79200"})


_CFG = {
    "htf_anchor": {"rule_by_calendar_group": {"cfd_us_22_23": FXCM, "crypto_24x7": RULE_UTC_MIDNIGHT}},
    "market_calendar_symbol_groups": {SYM: "cfd_us_22_23", "BTCUSDT": "crypto_24x7", "HKG33": "cfd_hk_main"},
}


def test_factory_takes_rule_per_symbol_from_calendar_group_and_logs_it(caplog):
    with caplog.at_level(logging.INFO, logger="derive_engine"):
        engine = build_derive_engine(_CFG, [SYM, "BTCUSDT"], {})
    assert engine._anchor_rules == {SYM: FXCM, "BTCUSDT": RULE_UTC_MIDNIGHT}  # noqa: SLF001
    wired = [r.getMessage() for r in caplog.records if r.getMessage().startswith("DERIVE_ENGINE_WIRED")]
    assert len(wired) == 1 and "'BTCUSDT': 'utc_midnight'" in wired[0] and "'XAU/USD': 'ny_close_us_dst'" in wired[0]


@pytest.mark.parametrize("cfg, symbols, code", [
    (_CFG, [SYM, "HKG33"], "HTF_ANCHOR_GROUP_UNMEASURED"),  # сітку cfd_hk_main не виміряно (ADR-0095 §8.4)
    ({"market_calendar_symbol_groups": _CFG["market_calendar_symbol_groups"]}, [SYM], "CONFIG_HTF_ANCHOR_MISSING"),
])
def test_factory_refuses_unresolvable_rule(cfg, symbols, code):
    with pytest.raises(ValueError, match=code):
        build_derive_engine(cfg, symbols, {})


# ── Overdue крокує по сезонній сітці ─────────────────────────────────────────────────────────────────────────


def _spy_derive_bar(monkeypatch) -> List[Tuple[int, int]]:
    """Запис бакетів, які overdue пробує будувати; деривація справжня (з правилом — рівність сітці або відмова)."""
    attempts: List[Tuple[int, int]] = []

    def spy(**kwargs):
        attempts.append((kwargs["target_tf_s"], kwargs["bucket_open_ms"]))
        return real_derive_bar(**kwargs)

    monkeypatch.setattr(derive_engine_module, "derive_bar", spy)
    return attempts


@pytest.mark.parametrize("now_ms, expected_h4, expected_d1", [
    pytest.param(  # весна: сесійна доба Сб 07.03 23:00 → Нд 08.03 22:00 — 23 год, останній H4 Нд 19:00 — обрубок 3 год
        _ms(2026, 3, 9, 6, 30),
        [_ms(2026, 3, 9, 2), _ms(2026, 3, 8, 22), _ms(2026, 3, 8, 19)],
        [_ms(2026, 3, 7, 22), _ms(2026, 3, 6, 22), _ms(2026, 3, 5, 22), _ms(2026, 3, 4, 22)],
        id="mon-2026-03-09",
    ),
    pytest.param(  # осінь: сесійна доба Сб 31.10 22:00 → Нд 01.11 23:00 — 25 год, останній H4 Нд 22:00 — обрубок 1 год
        _ms(2026, 11, 2, 6, 30),
        [_ms(2026, 11, 1, 23), _ms(2026, 11, 1, 22), _ms(2026, 11, 1, 18)],
        [_ms(2026, 10, 31, 21), _ms(2026, 10, 30, 21), _ms(2026, 10, 29, 21), _ms(2026, 10, 28, 21)],
        id="mon-2026-11-02",
    ),
])
def test_overdue_steps_on_season_grid_across_dst_weekend(monkeypatch, now_ms, expected_h4, expected_d1):
    # Контроль: наївний крок `cur - tf*i` у цей понеділок справді виходить за сітку — інакше тест нічого не доводить
    cur_h4 = htf_bucket_start_ms(now_ms, H4_S, FXCM)
    naive = cur_h4 - 3 * H4_MS
    assert htf_bucket_start_ms(naive, H4_S, FXCM) != naive

    attempts = _spy_derive_bar(monkeypatch)
    engine = DeriveEngine(symbols=[SYM], anchor_rules={SYM: FXCM}, calendars={SYM: _cfd_us_calendar()},
                          cascade_tfs_s={H4_S, D1_S}, commit_tfs_s={H4_S, D1_S})
    engine.register_symbol_uds(SYM, _ok_uds())
    # Буфери джерел існують, але дані далеко в минулому: перевіряються лише бакети, які overdue пробує
    engine.warmup_bars([_bar(60, now_ms - 30 * D1_MS), _bar(3600, cur_h4 - 30 * D1_MS)])

    engine.check_overdue_buckets(now_ms)

    assert [o for tf, o in attempts if tf == H4_S] == expected_h4
    assert [o for tf, o in attempts if tf == D1_S] == expected_d1
    for tf, open_ms in attempts:
        assert_on_season_grid(open_ms, tf, FXCM)


def test_overdue_stub_bucket_sun_2200_does_not_absorb_next_day():
    """01.11.2026: обрубок Нд 22:00 (1 год сесійної доби, ринок закритий) — None; H1 23:00..02:00 будують H4 23:00
    нової сесії (18:00 EST)."""
    engine = DeriveEngine(symbols=[SYM], anchor_rules={SYM: FXCM}, calendars={SYM: _cfd_us_calendar()},
                          cascade_tfs_s={H4_S}, commit_tfs_s={H4_S})
    uds = _ok_uds()
    engine.register_symbol_uds(SYM, uds)
    engine.warmup_bars([_bar(3600, _ms(2026, 11, 1, 23) + k * H1_MS, 100.0 + k) for k in range(4)])

    committed = engine.check_overdue_buckets(_ms(2026, 11, 2, 3, 30))

    assert [(b.tf_s, b.open_time_ms) for b in committed] == [(H4_S, _ms(2026, 11, 1, 23))]
    assert (committed[0].o, committed[0].c) == (100.0, 103.5)
    written = [call.args[0].open_time_ms for call in uds.commit_final_bar.call_args_list]
    assert _ms(2026, 11, 1, 22) not in written


class _WeekdayCalendar:
    """Торгово нд 22:00 → пт 21:00 UTC, перерва 21:00–22:00 (фіксовані UTC-години, як статичний календар)."""

    def is_trading_minute(self, ms: int) -> bool:
        t = dt.datetime.fromtimestamp(ms / 1000, UTC)
        if t.hour == 21 or t.weekday() == 5:
            return False
        if t.weekday() == 4 and t.hour >= 22:
            return False
        return not (t.weekday() == 6 and t.hour < 22)


def test_holiday_friday_d1_built_on_sunday_reopen_across_dst_switch(monkeypatch):
    """Святкова пт 30.10 (літо) стає D1 Чт 29.10 21:00, щойно відкрилась Нд 01.11 22:00 — уже зимова доба."""
    attempts = _spy_derive_bar(monkeypatch)
    engine = DeriveEngine(symbols=[SYM], anchor_rules={SYM: FXCM}, calendars={SYM: _WeekdayCalendar()},
                          cascade_tfs_s={D1_S}, commit_tfs_s={D1_S})
    uds = _ok_uds()
    engine.register_symbol_uds(SYM, uds)
    bucket_open = _ms(2026, 10, 29, 21)
    early_close = _ms(2026, 10, 30, 17)
    day_minutes = (early_close - bucket_open - H1_MS) // 60_000  # мінус перерва 21:00–22:00
    engine.warmup_bars([_bar(60, bucket_open + H1_MS + i * 60_000) for i in range(day_minutes)])

    # Сб 12:00: бакет уже прострочений за часом (закрився пт 21:00), а фронтир ADR-0097 ще ні — до відкриття
    # в неділю святкову добу final-ом не фіксуємо. Overdue мусить цей бакет розглянути, інакше перевірка порожня
    saturday = _ms(2026, 10, 31, 12)
    assert htf_next_bucket_start_ms(bucket_open, D1_S, FXCM) <= saturday
    assert engine.check_overdue_buckets(now_ms=saturday) == []
    assert (D1_S, bucket_open) in attempts
    uds.commit_final_bar.assert_not_called()

    sunday_reopen = _ms(2026, 11, 1, 22)
    engine.warmup_bars([_bar(60, sunday_reopen)])
    committed = engine.check_overdue_buckets(now_ms=sunday_reopen + 60_000)

    assert [(b.tf_s, b.open_time_ms) for b in committed] == [(D1_S, bucket_open)]
    assert "thin_session" in committed[0].extensions["partial_reasons"]


# ── Відмова писаря в overdue: гучно і без каскаду ────────────────────────────────────────────────────────────


def _m5_engine(uds) -> DeriveEngine:
    engine = DeriveEngine(symbols=[SYM], anchor_rules={SYM: FXCM}, cascade_tfs_s={300, 900}, commit_tfs_s={300, 900})
    engine.register_symbol_uds(SYM, uds)
    m1 = [_bar(60, i * 60_000) for i in range(15)]
    engine.warmup_bars(m1)
    engine.on_bar(m1[4])  # M5 0:00
    engine.on_bar(m1[9])  # M5 5:00; M5 10:00 «пропущено» — його знайде overdue
    return engine


def test_overdue_reject_is_loud_and_not_cascaded(caplog):
    uds = _ok_uds()
    engine = _m5_engine(uds)
    uds.commit_final_bar.reset_mock()
    uds.commit_final_bar.return_value = _result(False, "ssot_value_error")

    with caplog.at_level(logging.WARNING, logger="derive_engine"):
        committed = engine.check_overdue_buckets(now_ms=20 * 60_000)

    assert committed == []
    assert [(c.args[0].tf_s, c.args[0].open_time_ms) for c in uds.commit_final_bar.call_args_list] == [(300, 600_000)]
    assert engine.stats()["rejected"] == 1
    assert "OVERDUE_DERIVE_REJECT tf=300" in caplog.text and "reason=ssot_value_error" in caplog.text

    # Відкинутий бакет не осів у буфері: наступна перевірка пробує його знову, а не вважає побудованим
    engine.check_overdue_buckets(now_ms=20 * 60_000)
    assert uds.commit_final_bar.call_count == 2


@pytest.mark.parametrize("reason", ["stale", "duplicate"])
def test_overdue_bar_already_on_disk_still_cascades(reason, caplog):
    uds = _ok_uds()
    engine = _m5_engine(uds)
    uds.commit_final_bar.reset_mock()
    uds.commit_final_bar.side_effect = lambda bar: _result(bar.tf_s == 900, None if bar.tf_s == 900 else reason)

    with caplog.at_level(logging.WARNING, logger="derive_engine"):
        committed = engine.check_overdue_buckets(now_ms=20 * 60_000)

    assert [(b.tf_s, b.open_time_ms) for b in committed] == [(900, 0)]
    assert engine.stats()["rejected"] == 0
    assert "OVERDUE_DERIVE_REJECT" not in caplog.text


# ── Відмова писаря в живому каскаді: те саме правило, що в overdue ───────────────────────────────────────────


def _feed_live_quarter(engine: DeriveEngine) -> List[CandleBar]:
    """15 M1 через on_bar (живий шлях): тригери M5 0:00 / 5:00 / 10:00, а M15 0:00 — лише з каскаду M5."""
    return [b for i in range(15) for b in engine.on_bar(_bar(60, i * 60_000))]


def _m5_refused(reason: str):
    """Писар відповідає reason на кожен M5, а M15 приймає."""
    return lambda bar: _result(True) if bar.tf_s == 900 else _result(False, reason)


def _live_m5_m15_engine(uds) -> DeriveEngine:
    engine = DeriveEngine(symbols=[SYM], anchor_rules={SYM: FXCM}, cascade_tfs_s={300, 900}, commit_tfs_s={300, 900})
    engine.register_symbol_uds(SYM, uds)
    return engine


def test_live_reject_is_not_cascaded_and_retried_by_overdue(caplog):
    uds = MagicMock()
    uds.commit_final_bar.side_effect = _m5_refused("ssot_write_failed")  # збій диска на M5; M15 прийняв би
    engine = _live_m5_m15_engine(uds)

    with caplog.at_level(logging.WARNING, logger="derive_engine"):
        committed = _feed_live_quarter(engine)

    # M15 не зібрано з трьох M5, яких нема на диску
    assert committed == []
    written = [(c.args[0].tf_s, c.args[0].open_time_ms) for c in uds.commit_final_bar.call_args_list]
    assert written == [(300, 0), (300, 300_000), (300, 600_000)]
    assert engine.stats()["rejected"] == 3
    assert caplog.text.count("DERIVE_REJECT tf=300") == 3

    # Писар одужав: overdue повторює відкинуті M5 (їх не позначено побудованими) і лише тоді будує M15
    uds.commit_final_bar.reset_mock()
    uds.commit_final_bar.side_effect = None
    uds.commit_final_bar.return_value = _result(True)
    committed = engine.check_overdue_buckets(now_ms=20 * 60_000)

    assert sorted((b.tf_s, b.open_time_ms) for b in committed) == [(300, 0), (300, 300_000), (300, 600_000), (900, 0)]


@pytest.mark.parametrize("reason", ["stale", "duplicate"])
def test_live_bar_already_on_disk_still_cascades_and_is_not_a_reject(reason, caplog):
    uds = MagicMock()
    uds.commit_final_bar.side_effect = _m5_refused(reason)
    engine = _live_m5_m15_engine(uds)

    with caplog.at_level(logging.WARNING, logger="derive_engine"):
        committed = _feed_live_quarter(engine)

    assert [(b.tf_s, b.open_time_ms) for b in committed] == [(900, 0)]
    assert engine.stats()["rejected"] == 0  # та сама семантика лічильника, що в overdue
    assert "DERIVE_REJECT" not in caplog.text


# ── replay: config і правило перевіряються ДО очищення namespace Redis ──────────────────────────────────────


def test_replay_refuses_invalid_anchor_config_before_flushing_redis(tmp_path: Path, monkeypatch):
    from runtime.ingest import replay

    cfg = {
        "data_root": str(tmp_path / "data_v3"),
        "market_calendar_symbol_groups": {SYM: "cfd_us_22_23"},
        "market_calendar_by_group": {"cfd_us_22_23": {
            "market_weekend_open_dow": 6, "market_weekend_open_hm": "22:00",
            "market_weekend_close_dow": 4, "market_weekend_close_hm": "20:45",
            "market_daily_break_start_hm": "21:00", "market_daily_break_end_hm": "22:00",
        }},
    }  # без htf_anchor
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(cfg), encoding="utf-8")
    flushed = []
    monkeypatch.setattr(replay, "_flush_redis_namespace", lambda c: flushed.append(c) or 0)
    monkeypatch.setattr(replay, "build_uds_from_config", MagicMock(side_effect=AssertionError("uds after refusal")))

    with pytest.raises(ValueError, match="CONFIG_HTF_ANCHOR_MISSING"):
        replay.run_replay(config_path=str(config_path), symbols=[SYM], speed=0)

    assert flushed == []
