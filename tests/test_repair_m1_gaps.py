"""tools/repair/repair_m1_gaps — інструмент ремонту M1 мусить працювати саме тоді, коли він потрібен.

Він був мертвий на VPS з 05.09 (Redis-клієнт без ACL, ADR-0091) і тихо брехав іще раніше:
календар завжди виходив None (ключ ``symbol_groups`` у config не існує), а бюджет сторінок
рахувався від кількості дірок, не від довжини вікна. Кожен тест тут — одна з цих брехень.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import sys
import time
import types
from pathlib import Path

import pytest

from core.model.bars import CandleBar
from runtime.ingest.m1_session_filter import PausePolicy
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


# ── правило послідовності ADR-0101 (C3): нові ключі — у ланцюгу з сусідами SSOT, наявні не переписуються ──────────
EDGE_STALE_POLICY = PausePolicy(noise_margin_min=60, edge_stale_max_volume=8)


def _m1(open_ms: int, o: float, h: float, low: float, c: float, v: float) -> CandleBar:
    return CandleBar(symbol=SYMBOL, tf_s=60, open_time_ms=open_ms, close_time_ms=open_ms + 60_000, o=o, h=h, low=low,
                     c=c, v=v, complete=True, src="history")


def _seed_ssot(tmp_path, bars) -> str:
    """Бари, що вже лежать у SSOT (повні рядки, як їх пише записувач)."""
    tf_dir = tmp_path / SYMBOL.replace("/", "_") / "tf_60"
    tf_dir.mkdir(parents=True, exist_ok=True)
    for bar in bars:
        day = dt.datetime.fromtimestamp(bar.open_time_ms / 1000, dt.timezone.utc).strftime("%Y%m%d")
        with open(tf_dir / f"part-{day}.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(bar.to_dict()) + "\n")
    return str(tmp_path)


def _repair(monkeypatch, data_root: str, gap_opens, broker_bars, pause_policy=EDGE_STALE_POLICY):
    """Ремонт із брокером-підміною: віддає зі своїх барів лише ті, що ремонт попросив (дірки й необов'язкові)."""
    asked: dict = {}

    def _fetch(_cli, _ns, _symbol, _start_ms, end_ms, gaps, optional_opens=frozenset()):
        asked.update(end_ms=end_ms, optional=set(optional_opens))
        wanted = set(gaps) | set(optional_opens)
        return [bar for bar in broker_bars if bar.open_time_ms in wanted]

    monkeypatch.setattr(rmg, "fetch_m1_for_range", _fetch)
    result = rmg.repair_gaps(
        data_root=data_root, symbol=SYMBOL, gap_groups=rmg.group_contiguous_gaps(sorted(gap_opens)),
        all_gap_opens=set(gap_opens), redis_cli=object(), namespace="ns", dry_run=False,
        calendar=calendar_from_group(CFD_US_22_23), flat_max_volume=4, now_ms=None, pause_policy=pause_policy,
    )
    rows = [json.loads(line) for part in sorted((Path(data_root) / "XAU_USD" / "tf_60").glob("part-*.jsonl"))
            for line in part.read_text(encoding="utf-8").splitlines() if line.strip()]
    return result, rows, asked


def test_repair_takes_the_stale_edge_after_a_gap_at_session_close_and_folds_it_like_tv(tmp_path, monkeypatch, caplog):
    """Дірка на 20:59, останній хвилині сесії: ремонт бере в брокера й 21:00 і вкладає її пізні тіки в 20:59, як TV
    (XAU 22.09: c=4357.63 + 21:00 v=4 c=4357.74 → c=4357.74 v=520); 21:00 не пише, 22:01 уже в ланцюгу."""
    t2059 = _ms(2026, 9, 22, 20, 59)
    data_root = _seed_ssot(tmp_path, [_m1(t2059 - 60_000, 4358.0, 4358.5, 4357.9, 4358.33, 400.0),
                                      _m1(t2059 + 62 * 60_000, 4357.74, 4363.07, 4357.74, 4363.06, 397.0)])
    broker = [_m1(t2059, 4358.33, 4358.73, 4355.37, 4357.63, 516.0),
              _m1(t2059 + 60_000, 4357.63, 4357.74, 4357.63, 4357.74, 4.0)]
    caplog.set_level(logging.INFO)
    result, rows, asked = _repair(monkeypatch, data_root, {t2059}, broker)
    assert asked == {"end_ms": t2059 + 60_000, "optional": {t2059 + 60_000}}
    repaired = [row for row in rows if row["open_time_ms"] == t2059]
    assert len(repaired) == 1
    assert (repaired[0]["c"], repaired[0]["v"], repaired[0]["extensions"]) == (4357.74, 520.0, {"late_ticks_folded": 4.0})
    assert not [row for row in rows if row["open_time_ms"] == t2059 + 60_000]
    assert result["total_written"] == 1
    assert "'pause_edge_stale_folded': 1" in caplog.text and "M1_SSOT_EDIT_PENDING" not in caplog.text


def test_repair_chains_the_gap_to_ssot_neighbours_and_names_the_break_it_cannot_rewrite(tmp_path, monkeypatch,
                                                                                        caplog):
    """Дірка 19:01 між наявними 19:00 і 19:02 (полер прив'язав 19:02 до close 19:00). Новий бар — від close 19:00;
    19:02 закомічений: другої версії ключа ремонт не дописує (ADR-0098 §3.7), а називає правку для settle."""
    t1900 = _ms(2026, 9, 22, 19, 0)
    data_root = _seed_ssot(tmp_path, [_m1(t1900, 99.8, 100.2, 99.7, 100.0, 300.0),
                                      _m1(t1900 + 120_000, 100.0, 101.4, 99.9, 101.2, 280.0)])
    caplog.set_level(logging.INFO)
    _result, rows, asked = _repair(monkeypatch, data_root, {t1900 + 60_000},
                                   [_m1(t1900 + 60_000, 100.5, 101.1, 100.4, 101.0, 250.0)])
    assert asked["optional"] == set()  # 19:02 торгова — застарілого краю після цієї дірки немає
    new_rows = [row for row in rows if row["open_time_ms"] == t1900 + 60_000]
    assert len(new_rows) == 1
    assert (new_rows[0]["o"], new_rows[0]["low"]) == (100.0, 100.0)
    assert new_rows[0]["extensions"] == {"open_chained_from": 100.5}
    assert [row["open_time_ms"] for row in rows].count(t1900 + 120_000) == 1
    assert ("M1_SSOT_EDIT_PENDING where=repair_m1_gaps symbol=XAU/USD reason=chain open_ms=%d o=100.00000->101.00000"
            % (t1900 + 120_000)) in caplog.text


def test_repair_does_not_ask_for_the_first_pause_minute_where_the_stale_edge_rule_is_off(tmp_path, monkeypatch):
    """Група без правила застарілого краю (EUSTX50/GER30, рев'ю D-03): першу хвилину паузи ремонт не бере — вкладати
    там нічого, а сама хвилина може бути торгівлею під несезонним календарем."""
    t2059 = _ms(2026, 9, 22, 20, 59)
    data_root = _seed_ssot(tmp_path, [_m1(t2059 - 60_000, 4358.0, 4358.5, 4357.9, 4358.33, 400.0)])
    _result, _rows, asked = _repair(monkeypatch, data_root, {t2059},
                                    [_m1(t2059, 4358.33, 4358.73, 4355.37, 4357.63, 516.0)],
                                    pause_policy=PausePolicy(noise_margin_min=60))
    assert asked == {"end_ms": t2059, "optional": set()}


@pytest.mark.parametrize("broker_has_stale_edge", [True, False])
def test_optional_stale_edge_is_taken_when_present_and_its_absence_is_not_a_missing_minute(monkeypatch, caplog,
                                                                                           broker_has_stale_edge):
    t2059 = _ms(2026, 9, 22, 20, 59)
    first = t2059 + 60_000 if broker_has_stale_edge else t2059
    page = [_bar(o) for o in range(first, t2059 - 10 * 60_000, -60_000)]
    monkeypatch.setattr(rmg, "_fetch_from_sidecar", lambda *a, **k: page)
    with caplog.at_level("WARNING"):
        got = rmg.fetch_m1_for_range(object(), "ns", SYMBOL, t2059, t2059 + 60_000, {t2059},
                                     optional_opens={t2059 + 60_000})
    assert sorted(b.open_time_ms for b in got) == ([t2059, t2059 + 60_000] if broker_has_stale_edge else [t2059])
    assert "REPAIR_BROKER_LACKS_MINUTES" not in caplog.text
