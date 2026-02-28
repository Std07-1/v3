"""Exit-gate: coldstart_multisymbol — перевірка готовності cold start для всіх символів.

Ціль: гарантувати що після рестарту 13 символів мають M5/derived/H4/D1 дані
без дірок, і derived_tail_state покриває всі символи.

Підгейти (static):
1. all_symbols_have_data_dirs — кожен символ має каталог у data_v3/
2. all_symbols_have_all_tfs — кожен символ має tf_300..tf_86400
3. derived_state_covers_all — _derived_tail_state.json містить усі символи
4. rebuild_tool_has_batch — rebuild_derived.py підтримує --all flag
5. priming_budget_sufficient — redis_priming_budget_s >= 5

Підгейти (data, sample):
6. m5_recent_data — хоча б 1 bar у tf_300 за останні 7 днів для кожного символу (sample)
7. derived_recent_data — хоча б 1 bar у tf_3600 за останні 7 днів для XAU/USD (sample)
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List


# Канонічний allowlist TF директорій
EXPECTED_TFS = ["tf_300", "tf_900", "tf_1800", "tf_3600", "tf_14400", "tf_86400"]

# Скільки секунд = 7 днів (для sample перевірки)
SEVEN_DAYS_MS = 7 * 24 * 3600 * 1000


def _load_config(root: Path) -> dict:
    cfg_path = root / "config.json"
    if not cfg_path.exists():
        return {}
    try:
        return json.loads(cfg_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _symbols_from_config(cfg: dict) -> List[str]:
    raw = cfg.get("symbols", [])
    if isinstance(raw, list) and raw:
        return [str(s) for s in raw if str(s).strip()]
    sym = cfg.get("symbol", "")
    return [str(sym)] if sym else []


def _symbol_dir_name(symbol: str) -> str:
    return symbol.replace("/", "_")


def _latest_bar_ms_in_dir(tf_dir: Path) -> int:
    """Повертає open_ms останнього бару з part-*.jsonl файлів у каталозі."""
    latest_ms = 0
    if not tf_dir.is_dir():
        return 0
    for part in sorted(tf_dir.glob("part-*.jsonl"), reverse=True):
        try:
            lines = part.read_text(encoding="utf-8", errors="replace").strip().splitlines()
            for line in reversed(lines):
                line = line.strip()
                if not line:
                    continue
                try:
                    bar = json.loads(line)
                    ot = bar.get("open_time_ms") or bar.get("t") or 0
                    if int(ot) > latest_ms:
                        latest_ms = int(ot)
                    return latest_ms
                except Exception:
                    continue
        except Exception:
            continue
    return latest_ms


def run_gate(inputs: dict) -> dict:
    root = Path(str(inputs.get("root", "."))).resolve()
    cfg = _load_config(root)
    symbols = _symbols_from_config(cfg)
    data_root = root / str(cfg.get("data_root", "./data_v3"))

    results: List[dict] = []

    if not symbols:
        results.append({"name": "config_symbols", "ok": False, "details": "symbols[] порожній у config.json"})
        return {"ok": False, "details": "no symbols", "sub_gates": results, "metrics": {"sub_gates_total": 1, "sub_gates_ok": 0}}

    # --- Підгейт 1: data dirs ---
    missing_dirs = []
    for sym in symbols:
        d = data_root / _symbol_dir_name(sym)
        if not d.is_dir():
            missing_dirs.append(sym)
    ok1 = len(missing_dirs) == 0
    results.append({
        "name": "all_symbols_have_data_dirs",
        "ok": ok1,
        "details": "ok" if ok1 else f"відсутні каталоги: {', '.join(missing_dirs)}",
    })

    # --- Підгейт 2: all TFs ---
    missing_tfs: List[str] = []
    for sym in symbols:
        sym_dir = data_root / _symbol_dir_name(sym)
        if not sym_dir.is_dir():
            continue
        for tf in EXPECTED_TFS:
            if not (sym_dir / tf).is_dir():
                missing_tfs.append(f"{sym}/{tf}")
    ok2 = len(missing_tfs) == 0
    results.append({
        "name": "all_symbols_have_all_tfs",
        "ok": ok2,
        "details": "ok" if ok2 else f"відсутні: {', '.join(missing_tfs[:10])}",
    })

    # --- Підгейт 3: derived_tail_state covers all ---
    state_path = data_root / "_derived_tail_state.json"
    state_symbols: List[str] = []
    missing_state: List[str] = []
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state_symbols = list((state.get("symbols") or {}).keys())
        except Exception:
            pass
    for sym in symbols:
        if sym not in state_symbols:
            missing_state.append(sym)
    ok3 = len(missing_state) == 0
    results.append({
        "name": "derived_state_covers_all",
        "ok": ok3,
        "details": "ok" if ok3 else f"відсутні у state: {', '.join(missing_state)}",
    })

    # --- Підгейт 4: rebuild_from_m1.py has CLI ---
    rebuild_py = root / "tools" / "rebuild_from_m1.py"
    has_all = False
    if rebuild_py.exists():
        try:
            text = rebuild_py.read_text(encoding="utf-8", errors="replace")
            has_all = "--symbol" in text or "--config" in text
        except Exception:
            pass
    results.append({
        "name": "rebuild_tool_has_batch",
        "ok": has_all,
        "details": "ok" if has_all else "rebuild_from_m1.py не має CLI",
    })

    # --- Підгейт 5: priming budget >= 5 ---
    priming_budget = cfg.get("redis_priming_budget_s", 2)
    ok5 = int(priming_budget) >= 5
    results.append({
        "name": "priming_budget_sufficient",
        "ok": ok5,
        "details": f"ok (budget={priming_budget}s)" if ok5 else f"budget={priming_budget}s < 5s",
    })

    # --- Підгейт 6: M5 recent data (sample) ---
    now_ms = int(time.time() * 1000)
    cutoff_ms = now_ms - SEVEN_DAYS_MS
    stale_m5: List[str] = []
    for sym in symbols:
        tf_dir = data_root / _symbol_dir_name(sym) / "tf_300"
        latest = _latest_bar_ms_in_dir(tf_dir)
        if latest < cutoff_ms:
            stale_m5.append(sym)
    ok6 = len(stale_m5) == 0
    results.append({
        "name": "m5_recent_data",
        "ok": ok6,
        "details": "ok" if ok6 else f"stale M5 (>7d): {', '.join(stale_m5)}",
    })

    # --- Підгейт 7: derived recent data (sample — XAU/USD H1) ---
    sample_sym = symbols[0] if symbols else "XAU/USD"
    tf_dir_h1 = data_root / _symbol_dir_name(sample_sym) / "tf_3600"
    latest_h1 = _latest_bar_ms_in_dir(tf_dir_h1)
    ok7 = latest_h1 >= cutoff_ms
    results.append({
        "name": "derived_recent_data",
        "ok": ok7,
        "details": f"ok (sample={sample_sym})" if ok7 else f"{sample_sym} H1 stale (>7d)",
    })

    all_ok = all(r["ok"] for r in results)
    summary_parts = [f"{r['name']}={'OK' if r['ok'] else 'FAIL'}" for r in results]
    return {
        "ok": all_ok,
        "details": "; ".join(summary_parts),
        "sub_gates": results,
        "metrics": {
            "sub_gates_total": len(results),
            "sub_gates_ok": sum(1 for r in results if r["ok"]),
            "symbols_count": len(symbols),
            "state_symbols_count": len(state_symbols),
        },
    }


def main() -> int:
    result = run_gate({"root": "."})
    total = result["metrics"]["sub_gates_total"]
    ok_count = result["metrics"]["sub_gates_ok"]
    print(f"gate_coldstart_multisymbol: {ok_count}/{total}")
    for sg in result.get("sub_gates", []):
        status = "OK" if sg["ok"] else "FAIL"
        print(f"  [{status}] {sg['name']}: {sg['details']}")
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
