"""ADR-0054 P0.3 — гейт coldstart більше не вимагає мертвого `_derived_tail_state.json`."""
from __future__ import annotations

import json
import time
from pathlib import Path

from tools.exit_gates.gates import gate_coldstart_multisymbol as gate

TFS = (300, 900, 1800, 3600, 14400, 86400)


def _root(tmp_path: Path, symbols, *, fresh: bool = True) -> Path:
    """Мінімальний зліпок репо: config.json + data_v3 з барами на кожен TF."""
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "rebuild_from_m1.py").write_text(
        'p.add_argument("--symbol")\np.add_argument("--config")\n', encoding="utf-8"
    )
    data = tmp_path / "data_v3"
    now_ms = int(time.time() * 1000)
    for sym in symbols:
        key = sym.replace("/", "_")
        for tf in TFS:
            d = data / key / f"tf_{tf}"
            d.mkdir(parents=True)
            open_ms = now_ms - 3600_000 if fresh else now_ms - 30 * 86400_000
            bar = {"open_time_ms": open_ms, "o": 1.0, "h": 1.0, "l": 1.0, "c": 1.0, "v": 1.0}
            (d / "part-x.jsonl").write_text(json.dumps(bar) + "\n", encoding="utf-8")
    (tmp_path / "config.json").write_text(
        json.dumps({"symbols": list(symbols), "data_root": "./data_v3", "redis_priming_budget_s": 10}),
        encoding="utf-8",
    )
    return tmp_path


def test_dead_state_file_is_no_longer_a_subgate(tmp_path):
    root = _root(tmp_path, ["XAU/USD"])
    result = gate.run_gate({"root": str(root)})
    names = [s["name"] for s in result["sub_gates"]]
    assert "derived_state_covers_all" not in names
    assert "state_symbols_count" not in result["metrics"]
    assert result["metrics"]["sub_gates_total"] == 6


def test_virgin_symbol_passes_without_any_state_file(tmp_path):
    """Головна причина патчу: новий символ не має бути у мертвому файлі."""
    root = _root(tmp_path, ["XAU/USD", "NAS100"])
    assert not (root / "data_v3" / "_derived_tail_state.json").exists()
    result = gate.run_gate({"root": str(root)})
    assert result["ok"] is True, result["details"]


def test_stale_state_file_present_does_not_change_verdict(tmp_path):
    """Файл лишається на диску (gitignored) — гейт його просто не читає."""
    root = _root(tmp_path, ["NAS100"])
    (root / "data_v3" / "_derived_tail_state.json").write_text(
        json.dumps({"symbols": {"SOMETHING/ELSE": {}}}), encoding="utf-8"
    )
    assert gate.run_gate({"root": str(root)})["ok"] is True


def test_real_data_problems_still_fail_loudly(tmp_path):
    """Патч знімає мертву перевірку, а не притуплює гейт."""
    root = _root(tmp_path, ["XAU/USD"], fresh=False)
    result = gate.run_gate({"root": str(root)})
    assert result["ok"] is False
    assert "m5_recent_data=FAIL" in result["details"]
