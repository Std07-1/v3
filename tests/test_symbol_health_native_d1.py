"""ADR-0103: при d1_policy=broker_native нативний D1 брокера — не derived-бар, і health не рахує його розбіжністю з M1.

TV D1 = натив FXCM, а він у дні з огризком вихідних чи ревізією брокера не дорівнює агрегату M1 (XAU 08.03.2026,
US30 20.09). Такий рядок несе провенанс `extensions.settled = d1native/…`; health звіряє з M1 лише derived-рядки D1,
а нативні рахує окремо (`native_d1`). За старої політики (derived_m1) той самий рядок — root_mismatch.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any, Dict

from core.config_loader import htf_anchor_rule_resolver, load_system_config, resolve_config_path
from tools.symbol_health_check import check_symbol

SYMBOL = "XAU/USD"
D1_S = 86_400


def _utc(*args: int) -> int:
    return int(dt.datetime(*args, tzinfo=dt.timezone.utc).timestamp()) * 1000


def _write(data_root: Path, tf_s: int, rows) -> None:
    tf_dir = data_root / "XAU_USD" / ("tf_%d" % tf_s)
    tf_dir.mkdir(parents=True, exist_ok=True)
    by_day: Dict[str, list] = {}
    for row in rows:
        day = dt.datetime.fromtimestamp(row["open_time_ms"] / 1000, dt.timezone.utc).strftime("%Y%m%d")
        by_day.setdefault(day, []).append(row)
    for day, day_rows in by_day.items():
        with open(tf_dir / ("part-%s.jsonl" % day), "w", encoding="utf-8") as fh:
            for row in day_rows:
                fh.write(json.dumps(row) + "\n")


def _bar(tf_s: int, open_ms: int, o: float, h: float, low: float, c: float, v: float, ext=None) -> Dict[str, Any]:
    row = {"symbol": SYMBOL, "tf_s": tf_s, "open_time_ms": open_ms, "close_time_ms": open_ms + tf_s * 1000,
           "o": o, "h": h, "l": low, "c": c, "v": v, "complete": True, "src": "history" if ext else "derived"}
    if ext:
        row["extensions"] = ext
    return row


def _tree(tmp_path: Path) -> int:
    """Одна торгова доба XAU (нд 08.03.2026, літо з 07:00 UTC): M1 22:01–22:03 і нативний D1 з open огризка."""
    key = _utc(2026, 3, 8, 21)
    m1 = [_bar(60, _utc(2026, 3, 8, 22, 1), 5170.03, 5191.38, 5170.03, 5182.99, 978.0),
          _bar(60, _utc(2026, 3, 8, 22, 2), 5182.99, 5183.48, 5171.2, 5177.21, 2041.0),
          _bar(60, _utc(2026, 3, 8, 22, 3), 5177.21, 5180.05, 5174.34, 5179.94, 1024.0)]
    _write(tmp_path, 60, m1)
    native = _bar(D1_S, key, 5171.76, 5191.38, 5171.2, 5179.94, 4043.0, ext={"settled": "d1native/20260923T1552"})
    _write(tmp_path, D1_S, [native])
    return key


def _check(tmp_path: Path, source: str) -> Dict[str, Any]:
    cfg = dict(load_system_config(resolve_config_path(None)), tf_allowlist_s=[60, D1_S],
               d1_policy={"source": source, "native_settle_lag_h": 6})
    return check_symbol(cfg, SYMBOL, data_root=str(tmp_path), now_ms=_utc(2026, 3, 9, 12), window_days=7,
                        anchor_rule_for_symbol=htf_anchor_rule_resolver(cfg))


def test_native_d1_is_counted_not_compared_with_m1_under_broker_native(tmp_path):
    _tree(tmp_path)
    d1 = _check(tmp_path, "broker_native")["tfs"][str(D1_S)]
    assert d1["native_d1"] == 1
    assert d1["root"] is None and not any(r.startswith("root_mismatch") for r in d1["reasons"])


def test_same_row_is_root_mismatch_under_derived_m1(tmp_path):
    _tree(tmp_path)
    d1 = _check(tmp_path, "derived_m1")["tfs"][str(D1_S)]
    assert d1["native_d1"] == 0
    assert d1["root"]["mismatched"] == 1 and "root_mismatch=1" in d1["reasons"]
