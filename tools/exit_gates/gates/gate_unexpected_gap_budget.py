"""Exit-gate: unexpected_gap_budget — перевірка бюджету unexpected M5 gap-ів.

Ціль: unexpected_gap_bars (ринок відкритий, бару немає) не перевищують бюджет.
Сканує M5 JSONL data за N днів, класифікує через MarketCalendar.

Підгейти:
1. total_unexpected_within_budget — sum(unexpected) ≤ total_budget
2. per_symbol_within_budget — max(unexpected per sym) ≤ per_sym_budget
"""
from __future__ import annotations

from pathlib import Path

from core.config_loader import load_system_config
from tools.diag.classify_m5_gaps import classify_gaps, _build_calendar


def run_gate(inputs):
    # type: (dict) -> dict
    root = Path(str(inputs.get("root", "."))).resolve()
    days = int(inputs.get("days", 14))
    total_budget = int(inputs.get("total_budget", 300))
    per_sym_budget = int(inputs.get("per_sym_budget", 250))

    cfg_path = str(root / str(inputs.get("config", "config.json")))
    cfg = load_system_config(cfg_path)
    data_root = str(cfg.get("data_root", str(root / "data_v3")))

    symbols = [str(s) for s in cfg.get("symbols", []) if str(s).strip()]
    if not symbols:
        return {"ok": False, "details": "symbols list empty", "sub_gates": []}

    per_sym_results = {}  # type: Dict[str, Dict[str, Any]]
    total_unexpected = 0

    for sym in symbols:
        cal = _build_calendar(cfg, sym)
        if cal is None:
            per_sym_results[sym] = {"unexpected": -1, "note": "no_calendar"}
            continue
        result = classify_gaps(data_root, sym, cal, days)
        per_sym_results[sym] = {
            "unexpected": result["unexpected_gap"],
            "coverage_pct": result["coverage_pct"],
        }
        total_unexpected += result["unexpected_gap"]

    # --- Підгейт 1: загальний бюджет ---
    ok1 = total_unexpected <= total_budget
    # --- Підгейт 2: per-symbol бюджет ---
    max_sym_val = max((v["unexpected"] for v in per_sym_results.values()
                       if isinstance(v.get("unexpected"), int) and v["unexpected"] >= 0),
                      default=0)
    max_sym_name = next(
        (k for k, v in per_sym_results.items() if v.get("unexpected") == max_sym_val),
        "?",
    )
    ok2 = max_sym_val <= per_sym_budget

    sub_gates = [
        {
            "name": "total_unexpected_within_budget",
            "ok": ok1,
            "details": "total=%d budget=%d" % (total_unexpected, total_budget),
        },
        {
            "name": "per_symbol_within_budget",
            "ok": ok2,
            "details": "max=%d(%s) budget=%d" % (max_sym_val, max_sym_name, per_sym_budget),
        },
    ]

    all_ok = ok1 and ok2
    return {
        "ok": all_ok,
        "details": "total=%d/%d max_sym=%d(%s)/%d days=%d" % (
            total_unexpected, total_budget,
            max_sym_val, max_sym_name, per_sym_budget, days,
        ),
        "sub_gates": sub_gates,
        "metrics": {
            "total_unexpected": total_unexpected,
            "total_budget": total_budget,
            "per_sym_budget": per_sym_budget,
            "per_symbol": per_sym_results,
            "days": days,
            "symbols_count": len(symbols),
        },
    }
