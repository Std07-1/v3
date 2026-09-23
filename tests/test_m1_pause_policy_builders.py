"""Будівники записувачів M1 беруть правила паузи з config, а не з дефолтів у коді (ADR-0099 §3.7, рев'ю п.7).

Кожен будівник викликає `resolve_pause_policy(cfg)` і передає політику в полер. Тест доводить проводку наскрізь:
config з нестандартним запасом дає полер зібраного runner'а (або виклик ремонту) з цим запасом, а не з дефолтом 60.
Брокер, Redis і UDS підмінені: перевіряється саме проводка config → записувач.
"""
from __future__ import annotations

import json
import types
from pathlib import Path

import pytest

from runtime.ingest.m1_session_filter import PAUSE_NOISE_MARGIN_MIN_DEFAULT

CONFIGURED_MARGIN_MIN = 90
assert CONFIGURED_MARGIN_MIN != PAUSE_NOISE_MARGIN_MIN_DEFAULT  # інакше тест не відрізнить config від дефолту

_US_CFD_GROUP = {
    "market_weekend_open_dow": 6, "market_weekend_open_hm": "22:00",
    "market_weekend_close_dow": 4, "market_weekend_close_hm": "20:45",
    "market_daily_break_start_hm": "21:00", "market_daily_break_end_hm": "22:00",
}
_CRYPTO_GROUP = {
    "market_weekend_open_dow": 0, "market_weekend_open_hm": "00:00",
    "market_weekend_close_dow": 6, "market_weekend_close_hm": "23:59",
    "market_daily_break_start_hm": "00:00", "market_daily_break_end_hm": "00:00",
}


def _write_config(tmp_path: Path, **overrides) -> str:
    cfg = {
        "data_root": str(tmp_path / "data_v3"),
        "symbols": ["XAU/USD"],
        "m1_poller": {"enabled": True},
        "m1_session_filter": {"pause_noise_margin_min": CONFIGURED_MARGIN_MIN},
        "market_calendar_by_group": {"cfd_us_22_23": _US_CFD_GROUP, "crypto_24x7": _CRYPTO_GROUP},
        "market_calendar_symbol_groups": {"XAU/USD": "cfd_us_22_23", "BTCUSDT": "crypto_24x7"},
        "htf_anchor": {"rule_by_calendar_group": {"cfd_us_22_23": "ny_close_us_dst", "crypto_24x7": "utc_midnight"}},
    }
    cfg.update(overrides)
    path = tmp_path / "config.json"
    path.write_text(json.dumps(cfg), encoding="utf-8")
    return str(path)


def _fake_uds(**_kwargs):
    return object()


def _margins_of(runner) -> list:
    return [poller._pause_policy.noise_margin_min for poller in runner._pollers]  # noqa: SLF001


def test_fxcm_poller_builder_takes_pause_policy_from_config(tmp_path, monkeypatch):
    from runtime.ingest.broker.fxcm import provider as fxcm_provider
    from runtime.ingest.polling import m1_poller

    monkeypatch.setenv("FXCM_USERNAME", "u")
    monkeypatch.setenv("FXCM_PASSWORD", "p")
    monkeypatch.setenv("FXCM_HOST_URL", "http://fxcm.invalid")
    monkeypatch.setattr(fxcm_provider, "FxcmHistoryProvider", lambda **_kwargs: object())
    monkeypatch.setattr(m1_poller, "build_uds_from_config", _fake_uds)

    runner = m1_poller.build_m1_poller(_write_config(tmp_path))

    assert runner is not None
    assert _margins_of(runner) == [CONFIGURED_MARGIN_MIN]


def test_broker_proxy_worker_builder_takes_pause_policy_from_config(tmp_path, monkeypatch):
    from runtime.ingest import m1_ingestion_worker as worker

    redis_spec = types.SimpleNamespace(host="127.0.0.1", port=6379, db=1, namespace="test", auth_kwargs=lambda: {})
    monkeypatch.setattr(worker, "redis_lib", types.SimpleNamespace(Redis=lambda **_kwargs: object()))
    monkeypatch.setattr(worker, "resolve_redis_spec", lambda _cfg, role, log: redis_spec)
    monkeypatch.setattr(worker, "build_uds_from_config", _fake_uds)

    runner = worker.build_ingestion_worker(_write_config(tmp_path))

    assert runner is not None
    assert _margins_of(runner) == [CONFIGURED_MARGIN_MIN]


def test_binance_worker_builder_takes_pause_policy_from_config(tmp_path, monkeypatch):
    from runtime.ingest import binance_ingest_worker as worker

    monkeypatch.setattr(worker, "BinanceHistoryProvider", lambda **_kwargs: object())
    monkeypatch.setattr(worker, "build_uds_from_config", _fake_uds)

    built = worker.build_binance_ingest_worker(
        _write_config(tmp_path, binance={"enabled": True, "symbols": ["BTCUSDT"]})
    )

    assert built is not None
    runner, _crawl_context = built
    assert _margins_of(runner) == [CONFIGURED_MARGIN_MIN]


def test_repair_tool_main_takes_pause_policy_from_config(tmp_path, monkeypatch):
    from tools.repair import repair_m1_gaps as rmg

    gap_open_ms = 1_789_596_000_000  # 2026-09-16 22:00 UTC — торгова хвилина календаря
    captured = {}

    def _capture_repair(**kwargs):
        captured.update(kwargs)
        return {"total_gaps": 1, "total_fetched": 0, "total_written": 0, "groups": []}

    monkeypatch.setattr(rmg, "detect_m1_gaps", lambda *_args, **_kwargs: [gap_open_ms])
    monkeypatch.setattr(rmg, "_connect_redis", lambda _cfg: (types.SimpleNamespace(close=lambda: None), "test"))
    monkeypatch.setattr(rmg, "repair_gaps", _capture_repair)
    monkeypatch.setattr("sys.argv", [
        "repair_m1_gaps", "--symbol", "XAU/USD", "--commit", "--config", _write_config(tmp_path),
        "--start", "2026-09-16T21:00:00", "--end", "2026-09-16T23:00:00",
    ])

    rmg.main()

    assert captured["pause_policy"].noise_margin_min == CONFIGURED_MARGIN_MIN


@pytest.mark.parametrize("builder_module", [
    "runtime.ingest.polling.m1_poller",
    "runtime.ingest.m1_ingestion_worker",
    "runtime.ingest.binance_ingest_worker",
    "tools.repair.repair_m1_gaps",
    "tools.fetch_tf_backfill",
])
def test_every_m1_writer_resolves_pause_policy_through_the_shared_resolver(builder_module):
    """Запобіжник SSOT: жоден записувач не читає `m1_session_filter` з config сам, лише через resolve_pause_policy."""
    import importlib
    import inspect
    source = inspect.getsource(importlib.import_module(builder_module))
    assert "resolve_pause_policy(cfg, " in source  # на символ: застарілий край залежить від групи
    assert '"m1_session_filter"' not in source and "'m1_session_filter'" not in source


@pytest.mark.parametrize("builder_module", [
    "runtime.ingest.polling.m1_poller",
    "runtime.ingest.m1_ingestion_worker",
    "runtime.ingest.binance_ingest_worker",
    "runtime.ingest.replay",
])
def test_every_derive_engine_builder_goes_through_the_rule_factory(builder_module):
    """Запобіжник SSOT (ADR-0095 S4a): рушій деривації — лише з `build_derive_engine`, секунд якоря з config немає."""
    import importlib
    import inspect
    source = inspect.getsource(importlib.import_module(builder_module))
    assert "build_derive_engine(cfg, symbols, " in source
    assert "DeriveEngine(" not in source
    assert "day_anchor_offset_s" not in source and "d1_anchor_offset_s" not in source


def test_fxcm_poller_builder_wires_rule_per_symbol_from_config(tmp_path, monkeypatch):
    from runtime.ingest.broker.fxcm import provider as fxcm_provider
    from runtime.ingest.polling import m1_poller

    monkeypatch.setenv("FXCM_USERNAME", "u")
    monkeypatch.setenv("FXCM_PASSWORD", "p")
    monkeypatch.setenv("FXCM_HOST_URL", "http://fxcm.invalid")
    monkeypatch.setattr(fxcm_provider, "FxcmHistoryProvider", lambda **_kwargs: object())
    monkeypatch.setattr(m1_poller, "build_uds_from_config", _fake_uds)

    runner = m1_poller.build_m1_poller(_write_config(tmp_path))

    assert runner._derive_engine._anchor_rules == {"XAU/USD": "ny_close_us_dst"}  # noqa: SLF001
