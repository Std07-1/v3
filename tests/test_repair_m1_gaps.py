"""tools/repair/repair_m1_gaps — інструмент ремонту M1 мусить працювати саме тоді, коли він потрібен.

Він був мертвий на VPS з 05.09 (Redis-клієнт без ACL, ADR-0091) і тихо брехав іще раніше:
календар завжди виходив None (ключ ``symbol_groups`` у config не існує), а бюджет сторінок
рахувався від кількості дірок, не від довжини вікна. Кожен тест тут — одна з цих брехень.
"""
from __future__ import annotations

import datetime as dt
import json
import sys
import time
import types

import pytest

from core.model.bars import CandleBar
from runtime.ingest.tick_common import calendar_from_group
from tools.repair import repair_m1_gaps as rmg

# Той самий календар, що в config.json для XAU/XAG/NAS100/SPX500.
CFD_US_22_23 = {
    "market_weekend_open_dow": 6,
    "market_weekend_open_hm": "22:00",
    "market_weekend_close_dow": 4,
    "market_weekend_close_hm": "20:45",
    "market_daily_break_start_hm": "21:00",
    "market_daily_break_end_hm": "22:00",
}
SYMBOL = "XAU/USD"
REDIS_CFG = {"redis": {"enabled": True, "host": "127.0.0.1", "port": 6379, "db": 1, "namespace": "v3_test"}}


def _ms(y: int, mo: int, d: int, h: int, mi: int) -> int:
    return int(dt.datetime(y, mo, d, h, mi, tzinfo=dt.timezone.utc).timestamp()) * 1000


def _bar(open_ms: int) -> CandleBar:
    return CandleBar(
        symbol=SYMBOL, tf_s=60, open_time_ms=open_ms, close_time_ms=open_ms + 60_000,
        o=1.0, h=1.0, low=1.0, c=1.0, v=1.0, complete=True, src="history", extensions={},
    )


def _write_m1(tmp_path, opens) -> str:
    """Покласти M1 у part-файли так, як їх читає ``load_day_open_times``."""
    tf_dir = tmp_path / SYMBOL.replace("/", "_") / "tf_60"
    tf_dir.mkdir(parents=True)
    by_day: dict = {}
    for o in opens:
        day = dt.datetime.fromtimestamp(o / 1000, dt.timezone.utc).strftime("%Y%m%d")
        by_day.setdefault(day, []).append(o)
    for day, day_opens in by_day.items():
        with open(tf_dir / f"part-{day}.jsonl", "w", encoding="utf-8") as fh:
            for o in day_opens:
                fh.write(json.dumps({"open_time_ms": o}) + "\n")
    return str(tmp_path)


def _fake_redis(monkeypatch, *, fail_auth: bool = False) -> dict:
    """Підмінити пакет redis: запам'ятати kwargs клієнта, за потреби впасти на AUTH."""
    captured: dict = {}

    class RedisError(Exception):
        pass

    class AuthenticationError(RedisError):
        pass

    class FakeRedis:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def ping(self):
            if fail_auth:
                raise AuthenticationError("Authentication required.")
            return True

    mod = types.ModuleType("redis")
    mod.Redis = FakeRedis
    mod.exceptions = types.SimpleNamespace(RedisError=RedisError, AuthenticationError=AuthenticationError)
    monkeypatch.setitem(sys.modules, "redis", mod)
    return captured


# ── календар ────────────────────────────────────────────────────────────────
def test_calendar_resolves_from_config_ssot_keys():
    cfg = {"market_calendar_by_group": {"cfd_us_22_23": CFD_US_22_23},
           "market_calendar_symbol_groups": {SYMBOL: "cfd_us_22_23"}}
    cal = rmg._load_calendar(cfg, SYMBOL)
    assert cal.is_trading_minute(_ms(2026, 9, 8, 12, 0)) is True
    assert cal.is_trading_minute(_ms(2026, 9, 8, 21, 30)) is False


def test_symbol_without_calendar_refuses_loudly():
    with pytest.raises(SystemExit, match="REPAIR_CALENDAR_MISSING"):
        rmg._load_calendar({"market_calendar_by_group": {}, "market_calendar_symbol_groups": {}}, SYMBOL)


# ── детекція дірок ──────────────────────────────────────────────────────────
def test_daily_break_is_not_counted_as_gap(tmp_path):
    """Вівторок 20:50–22:10: 10 торгових хв до перерви + 11 після. Перерва — не дірка."""
    cal = calendar_from_group(CFD_US_22_23)
    assert cal.is_trading_minute(_ms(2026, 9, 8, 21, 30)) is False, "контроль: перерва справді закрита"
    gaps = rmg.detect_m1_gaps(_write_m1(tmp_path, []), SYMBOL,
                              _ms(2026, 9, 8, 20, 50), _ms(2026, 9, 8, 22, 10), cal)
    assert len(gaps) == 21
    assert not [g for g in gaps if _ms(2026, 9, 8, 21, 0) <= g < _ms(2026, 9, 8, 22, 0)]


def test_present_bars_are_not_gaps(tmp_path):
    cal = calendar_from_group(CFD_US_22_23)
    start = _ms(2026, 9, 8, 10, 0)
    opens = [start + i * 60_000 for i in range(10) if i != 4]
    gaps = rmg.detect_m1_gaps(_write_m1(tmp_path, opens), SYMBOL, start, start + 9 * 60_000, cal)
    assert gaps == [start + 4 * 60_000]


# ── бюджет сторінок ─────────────────────────────────────────────────────────
def test_page_budget_covers_whole_window_not_gap_count():
    start, end = _ms(2026, 9, 6, 22, 0), _ms(2026, 9, 7, 20, 59)
    span_min = (end - start) // 60_000 + 1
    assert rmg._page_budget(start, end) * rmg._MAX_BARS_PER_FETCH >= span_min
    old_formula = 43 // rmg._MAX_BARS_PER_FETCH + 5  # 43 дірки XAU за 07.09
    assert old_formula * rmg._MAX_BARS_PER_FETCH < span_min, "контроль: стара формула справді не діставала"


def test_page_budget_exhaustion_is_loud(monkeypatch, caplog):
    start, end = _ms(2026, 9, 7, 10, 0), _ms(2026, 9, 7, 20, 0)
    monkeypatch.setattr(rmg, "_fetch_from_sidecar", lambda *a, **k: [_bar(end - i * 60_000) for i in range(5)])
    monkeypatch.setattr(rmg, "_page_budget", lambda s, e: 1)
    monkeypatch.setattr(rmg.time, "sleep", lambda _s: None)
    with caplog.at_level("WARNING"):
        rmg.fetch_m1_for_range(object(), "ns", SYMBOL, start, end, {start + 60_000})
    assert "REPAIR_FETCH_PAGE_BUDGET_EXHAUSTED" in caplog.text


def test_complete_coverage_is_not_reported_as_exhausted(monkeypatch, caplog):
    start, end = _ms(2026, 9, 7, 10, 0), _ms(2026, 9, 7, 20, 0)
    target = end - 60_000
    monkeypatch.setattr(rmg, "_fetch_from_sidecar", lambda *a, **k: [_bar(end), _bar(target)])
    monkeypatch.setattr(rmg, "_page_budget", lambda s, e: 1)
    with caplog.at_level("WARNING"):
        bars = rmg.fetch_m1_for_range(object(), "ns", SYMBOL, start, end, {target})
    assert [b.open_time_ms for b in bars] == [target]
    assert "PAGE_BUDGET_EXHAUSTED" not in caplog.text


# ── Redis і контракт IPC ────────────────────────────────────────────────────
def test_connect_redis_passes_acl_credentials(monkeypatch):
    captured = _fake_redis(monkeypatch)
    monkeypatch.setenv("AI_ONE_REDIS_USERNAME", "smc_platform")
    monkeypatch.setenv("AI_ONE_REDIS_PASSWORD", "test-only")
    cli, ns = rmg._connect_redis(REDIS_CFG)
    assert cli is not None and ns == "v3_test"
    assert captured["username"] == "smc_platform"
    assert captured["password"] == "test-only"


def test_connect_redis_auth_failure_is_loud_and_names_env(monkeypatch, caplog):
    _fake_redis(monkeypatch, fail_auth=True)
    monkeypatch.delenv("AI_ONE_REDIS_USERNAME", raising=False)
    monkeypatch.delenv("AI_ONE_REDIS_PASSWORD", raising=False)
    with caplog.at_level("ERROR"):
        cli, _ns = rmg._connect_redis(REDIS_CFG)
    assert cli is None
    assert "REPAIR_REDIS_AUTH_FAILED" in caplog.text
    assert "AI_ONE_REDIS_PASSWORD" in caplog.text


def test_sidecar_command_carries_ts_ms():
    pushed = []

    class FakeCli:
        def rpush(self, _key, payload):
            pushed.append(json.loads(payload))

        def blpop(self, _key, timeout):
            return None

        def delete(self, _key):
            pass

    before = int(time.time() * 1000)
    assert rmg._fetch_from_sidecar(FakeCli(), "ns", SYMBOL, 200) == []
    assert before <= pushed[0]["ts_ms"] <= int(time.time() * 1000)

# ── вердикт: «хвилин нема» ≠ «брокер мовчить» ──────────────────────────────
def test_broker_answered_but_lacks_minutes_is_named(monkeypatch, caplog):
    """Живий кейс 07.09: брокер віддав бари на все вікно, а нашої дірки серед них немає."""
    start, end = _ms(2026, 9, 7, 18, 49), _ms(2026, 9, 7, 20, 59)
    hole = _ms(2026, 9, 7, 19, 13)
    broker_page = [_bar(o) for o in range(end, start - 60_000, -60_000) if o != hole]
    monkeypatch.setattr(rmg, "_fetch_from_sidecar", lambda *a, **k: broker_page)
    with caplog.at_level("WARNING"):
        assert rmg.fetch_m1_for_range(object(), "ns", SYMBOL, start, end, {hole}) == []
    assert "REPAIR_BROKER_LACKS_MINUTES" in caplog.text


def test_silent_broker_is_not_reported_as_lacking_minutes(monkeypatch, caplog):
    """Контроль: брокер не відповів — це НЕ доказ, що хвилин у нього немає."""
    monkeypatch.setattr(rmg, "_fetch_from_sidecar", lambda *a, **k: [])
    monkeypatch.setattr(rmg.time, "sleep", lambda _s: None)
    with caplog.at_level("WARNING"):
        rmg.fetch_m1_for_range(object(), "ns", SYMBOL, _ms(2026, 9, 7, 18, 0), _ms(2026, 9, 7, 20, 59),
                               {_ms(2026, 9, 7, 19, 13)})
    assert "REPAIR_BROKER_LACKS_MINUTES" not in caplog.text
    assert "REPAIR_FETCH_PAGE_FAILED" in caplog.text
