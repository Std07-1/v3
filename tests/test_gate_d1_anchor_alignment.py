"""Exit gate d1_anchor_alignment на сезонній сітці H4/D1 (ADR-0095 §3.7, S5c).

Гейт більше не читає секунди якоря з config: правило символу дає `htf_anchor_rule_resolver`, а бари на диску
перевіряються рівністю сезонній сітці (`assert_on_season_grid`) для tf_86400 і tf_14400.
"""
from __future__ import annotations

import datetime as dt
import json
import shutil
from pathlib import Path
from typing import Any, Dict, Iterable

from tools.exit_gates.gates import gate_d1_anchor_alignment as gate

_REPO_ROOT = Path(__file__).resolve().parents[1]
H4_S, D1_S = 14_400, 86_400


def _ms(y: int, mo: int, d: int, h: int) -> int:
    return int(dt.datetime(y, mo, d, h, tzinfo=dt.timezone.utc).timestamp() * 1000)


def _cfg(**extra: Any) -> Dict[str, Any]:
    cfg: Dict[str, Any] = {
        "symbols": ["XAU/USD"],
        "data_root": "./data_v3",
        "derived_tfs_s": [180, 300, 900, 1800, 3600, 14400, 86400],
        "market_calendar_symbol_groups": {"XAU/USD": "cfd_us_22_23", "HKG33": "cfd_hk_main", "BTCUSDT": "crypto_24x7"},
        "htf_anchor": {"rule_by_calendar_group": {"cfd_us_22_23": "ny_close_us_dst", "crypto_24x7": "utc_midnight"}},
    }
    cfg.update(extra)
    return cfg


def _root(tmp_path: Path, cfg: Dict[str, Any]) -> Path:
    (tmp_path / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
    return tmp_path


def _write_part(root: Path, symbol: str, tf_s: int, day: str, opens: Iterable[int]) -> None:
    tf_dir = root / "data_v3" / symbol.replace("/", "_") / ("tf_%d" % tf_s)
    tf_dir.mkdir(parents=True, exist_ok=True)
    rows = [json.dumps({"open_time_ms": o, "tf_s": tf_s, "complete": True}) for o in opens]
    (tf_dir / ("part-%s.jsonl" % day)).write_text("\n".join(rows) + "\n", encoding="utf-8")


def _sub(result: Dict[str, Any], name: str) -> Dict[str, Any]:
    return result["metrics"][name]


def test_gate_repo_config_all_subgates_green_without_data(tmp_path):
    shutil.copy(_REPO_ROOT / "config.json", tmp_path / "config.json")
    cfg = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
    # Набір символів — з того самого config, а не літерал: активація символу (GER30) не стосується якоря
    expected_symbols = set(cfg["symbols"])
    if cfg.get("binance", {}).get("enabled"):
        expected_symbols |= set(cfg["binance"]["symbols"])
    result = gate.run_gate({"root": str(tmp_path)})
    assert result["ok"] is True, result["details"]
    assert list(result["metrics"]) == list(gate._CHECKS)
    assert expected_symbols and set(_sub(result, "htf_anchor_rule_valid")["rules"]) == expected_symbols
    assert "пропуск" in _sub(result, "disk_data_anchor")["msg"]


def test_disk_summer_h4_and_d1_on_grid_passes(tmp_path):
    root = _root(tmp_path, _cfg())
    _write_part(root, "XAU/USD", H4_S, "20260922", [_ms(2026, 9, 22, h) for h in (1, 5, 9, 13, 17, 21)])
    _write_part(root, "XAU/USD", D1_S, "20260921", [_ms(2026, 9, 21, 21)])
    result = gate.run_gate({"root": str(root)})
    assert result["ok"] is True, result["details"]
    assert (_sub(result, "disk_data_anchor")["files"], _sub(result, "disk_data_anchor")["rows"]) == (2, 7)


def test_disk_legacy_h4_grid_in_summer_fails_with_expected_open(tmp_path):
    """Стара сітка H4 22/02/06.. улітку (дані до S7) — FAIL з очікуваним відкриттям, а не прохід через alt-якір."""
    root = _root(tmp_path, _cfg())
    _write_part(root, "XAU/USD", H4_S, "20260922", [_ms(2026, 9, 22, h) for h in (2, 6, 10, 14, 18, 22)])
    result = gate.run_gate({"root": str(root)})
    sub = _sub(result, "disk_data_anchor")
    assert result["ok"] is False and "disk_data_anchor:FAIL" in result["details"]
    assert sub["violations"] == 6
    assert "bar_off_season_grid" in sub["msg"] and "expected_open_ms=%d" % _ms(2026, 9, 22, 1) in sub["msg"]


def test_disk_winter_d1_on_22_utc_passes_summer_grid_in_winter_fails(tmp_path):
    root = _root(tmp_path, _cfg())
    _write_part(root, "XAU/USD", D1_S, "20260114", [_ms(2026, 1, 14, 22)])
    assert gate.run_gate({"root": str(root)})["ok"] is True
    _write_part(root, "XAU/USD", D1_S, "20260115", [_ms(2026, 1, 15, 21)])
    sub = _sub(gate.run_gate({"root": str(root)}), "disk_data_anchor")
    assert sub["ok"] is False and "season=winter" in sub["msg"]


def test_disk_checks_every_row_not_only_last(tmp_path):
    """Part-файли не відсортовані за open: бар поза сіткою посередині файлу теж FAIL."""
    root = _root(tmp_path, _cfg())
    _write_part(root, "XAU/USD", H4_S, "20260922", [_ms(2026, 9, 22, 1), _ms(2026, 9, 22, 6), _ms(2026, 9, 22, 9)])
    sub = _sub(gate.run_gate({"root": str(root)}), "disk_data_anchor")
    assert (sub["ok"], sub["violations"]) == (False, 1)


def test_disk_row_without_int_open_or_not_json_fails(tmp_path):
    root = _root(tmp_path, _cfg())
    tf_dir = root / "data_v3" / "XAU_USD" / "tf_86400"
    tf_dir.mkdir(parents=True)
    (tf_dir / "part-20260921.jsonl").write_text('{"open_time_ms": "x"}\n{broken\n', encoding="utf-8")
    sub = _sub(gate.run_gate({"root": str(root)}), "disk_data_anchor")
    assert (sub["ok"], sub["violations"]) == (False, 2)
    assert "рядок 1 без цілого open_time_ms" in sub["msg"] and "рядок 2 не JSON-об'єкт" in sub["msg"]


def test_disk_checks_latest_part_file_only(tmp_path):
    """Гейт дивиться на свіжий стан (останній part-файл); вся історія — health `off_season_grid` (ADR-0095 S5a)."""
    root = _root(tmp_path, _cfg())
    _write_part(root, "XAU/USD", H4_S, "20260921", [_ms(2026, 9, 21, 2)])
    _write_part(root, "XAU/USD", H4_S, "20260922", [_ms(2026, 9, 22, 1)])
    assert gate.run_gate({"root": str(root)})["ok"] is True


def test_rule_unmeasured_group_fails_and_other_symbols_still_checked(tmp_path):
    root = _root(tmp_path, _cfg(symbols=["XAU/USD", "HKG33"]))
    _write_part(root, "XAU/USD", H4_S, "20260922", [_ms(2026, 9, 22, 2)])
    result = gate.run_gate({"root": str(root)})
    rule_sub = _sub(result, "htf_anchor_rule_valid")
    assert rule_sub["ok"] is False and "HTF_ANCHOR_GROUP_UNMEASURED symbol=HKG33" in rule_sub["msg"]
    assert _sub(result, "disk_data_anchor")["violations"] == 1


def test_htf_anchor_section_missing_fails_rule_and_disk(tmp_path):
    cfg = _cfg()
    del cfg["htf_anchor"]
    result = gate.run_gate({"root": str(_root(tmp_path, cfg))})
    assert "CONFIG_HTF_ANCHOR_MISSING" in _sub(result, "htf_anchor_rule_valid")["msg"]
    assert _sub(result, "disk_data_anchor")["ok"] is False


def test_binance_symbols_checked_only_when_enabled(tmp_path):
    root = _root(tmp_path, _cfg(binance={"enabled": True, "symbols": ["BTCUSDT"]}))
    _write_part(root, "BTCUSDT", D1_S, "20260922", [_ms(2026, 9, 22, 0)])
    result = gate.run_gate({"root": str(root)})
    assert result["ok"] is True, result["details"]
    assert _sub(result, "htf_anchor_rule_valid")["rules"]["BTCUSDT"] == "utc_midnight"
    off = _root(tmp_path, _cfg(binance={"enabled": False, "symbols": ["BTCUSDT"]}))
    assert "BTCUSDT" not in _sub(gate.run_gate({"root": str(off)}), "htf_anchor_rule_valid")["rules"]


def test_season_samples_fail_when_h4_leaves_d1_grid(tmp_path, monkeypatch):
    """Легасі-роз'їзд H4 23:00 проти D1 22:00 — FAIL підгейта зразків, а не тиха розбіжність."""
    real = gate.htf_anchor_offset_s
    monkeypatch.setattr(gate, "htf_anchor_offset_s", lambda tf_s, ts, rule: 82_800 if tf_s == H4_S else real(tf_s, ts, rule))
    sub = _sub(gate.run_gate({"root": str(_root(tmp_path, _cfg()))}), "season_anchor_samples")
    assert sub["ok"] is False and "ny_close_us_dst/winter D1=79200 H4=82800" in sub["msg"]


def test_legacy_anchor_key_in_config_fails_subgate(tmp_path):
    """Та сама перевірка, якою load_system_config відмовляє старту (CONFIG_LEGACY_ANCHOR_KEY), — окремий FAIL гейта."""
    cfg = _cfg(day_anchor_offset_s_d1=75600, binance={"enabled": False, "d1_anchor_offset_s": 0})
    result = gate.run_gate({"root": str(_root(tmp_path, cfg))})
    sub = _sub(result, "no_legacy_anchor_keys")
    assert result["ok"] is False and sub["ok"] is False
    assert sub["legacy_keys"] == ["day_anchor_offset_s_d1", "binance.d1_anchor_offset_s"]
    assert "CONFIG_LEGACY_ANCHOR_KEY" in sub["msg"]
