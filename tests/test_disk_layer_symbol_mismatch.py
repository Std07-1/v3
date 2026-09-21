"""Рядок чужого символу в каталозі SSOT — гучна відмова, а не тихе перепідписування.

Аудит 21.09.2026: у `XAU_USD/tf_86400` лежать 4 однорядкові part-файли з `symbol=XAG/USD` (≈25/67/90 на ціновій
шкалі золота). `uds._disk_bar_to_candle` підписував їх символом каталогу, тож guard `prime_from_bars`
(`b.symbol != symbol`) їх не бачив, а WS-кадр XAU D1 малював свічки срібла.
"""
from __future__ import annotations

import json
import logging

from runtime.store.layers.disk_layer import DiskLayer
from runtime.store.uds import UnifiedDataStore

D1_MS = 86_400_000
DAY_20251219 = 1_766_102_400_000  # 2025-12-19 00:00 UTC
DAY_20251220 = DAY_20251219 + D1_MS


def _d1_row(symbol, open_ms, price, **extra):
    row = {"symbol": symbol, "tf_s": 86400, "open_time_ms": open_ms, "close_time_ms": open_ms + D1_MS,
           "o": price, "h": price + 1.0, "low": price - 1.0, "c": price, "v": 1000.0, "complete": True,
           "src": "history"}
    row.update(extra)
    if symbol is None:
        del row["symbol"]
    return row


def _write_part(root, dir_name, day, rows):
    d = root / dir_name / "tf_86400"
    d.mkdir(parents=True, exist_ok=True)
    (d / ("part-%s.jsonl" % day)).write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def test_read_window_foreign_symbol_row_is_refused_loudly(tmp_path, caplog):
    _write_part(tmp_path, "XAU_USD", "20251219", [_d1_row("XAU/USD", DAY_20251219, 4300.0)])
    _write_part(tmp_path, "XAU_USD", "20251220", [_d1_row("XAG/USD", DAY_20251220, 66.985)])

    with caplog.at_level(logging.WARNING, logger="disk_layer"):
        bars, _geom = DiskLayer(str(tmp_path)).read_window_with_geom("XAU/USD", 86400, 10, use_tail=True)

    assert [(b["symbol"], b["open_time_ms"]) for b in bars] == [("XAU/USD", DAY_20251219)]
    assert "DISK_BAR_SYMBOL_MISMATCH reason=symbol_mismatch symbol=XAU/USD rejected=1" in caplog.text
    assert "XAG/USD" in caplog.text


def test_read_window_foreign_row_with_same_key_does_not_win_the_tie(tmp_path):
    """Чужий рядок того самого open_time_ms, дописаний пізніше, не має вигравати нічию вибирача (ADR-0094)."""
    _write_part(tmp_path, "XAU_USD", "20251220", [_d1_row("XAU/USD", DAY_20251220, 4300.0),
                                                  _d1_row("XAG/USD", DAY_20251220, 66.985)])

    bars, _geom = DiskLayer(str(tmp_path)).read_window_with_geom("XAU/USD", 86400, 10, use_tail=True)

    assert [(b["symbol"], b["o"]) for b in bars] == [("XAU/USD", 4300.0)]


def test_read_window_legacy_row_without_symbol_and_underscore_symbol_are_kept(tmp_path, caplog):
    """Рядок без поля symbol (легасі BTC/ETH) і запис символу у формі каталогу — свої, без відмови."""
    _write_part(tmp_path, "XAU_USD", "20251219", [_d1_row(None, DAY_20251219, 4300.0)])
    _write_part(tmp_path, "XAU_USD", "20251220", [_d1_row("XAU_USD", DAY_20251220, 4310.0)])

    with caplog.at_level(logging.WARNING, logger="disk_layer"):
        bars, _geom = DiskLayer(str(tmp_path)).read_window_with_geom("XAU/USD", 86400, 10, use_tail=True)

    assert [b["open_time_ms"] for b in bars] == [DAY_20251219, DAY_20251220]
    assert "DISK_BAR_SYMBOL_MISMATCH" not in caplog.text


def test_uds_read_tail_candles_foreign_row_is_not_relabeled_as_directory_symbol(tmp_path):
    """Шлях UDS, яким праймиться Redis-хвіст: свічка срібла більше не стає свічкою XAU/USD."""
    _write_part(tmp_path, "XAU_USD", "20251219", [_d1_row("XAU/USD", DAY_20251219, 4300.0)])
    _write_part(tmp_path, "XAU_USD", "20251220", [_d1_row("XAG/USD", DAY_20251220, 66.985)])
    uds = UnifiedDataStore(data_root=str(tmp_path), boot_id="test-boot", tf_allowlist={86400},
                           min_coldload_bars={86400: 1}, role="reader")

    candles = uds.read_tail_candles("XAU/USD", 86400, 10)

    assert [(c.symbol, c.open_time_ms, c.o) for c in candles] == [("XAU/USD", DAY_20251219, 4300.0)]
